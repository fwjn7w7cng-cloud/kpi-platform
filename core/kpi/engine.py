"""Расчёт KPI за месяц по нейтральным данным.

    k = Kpi(cfg, db, "2026-10", today)
    m = k.emp(emp_id)            # {код: Metric} — продажи, АКБ, SKU, сбор, фокус, визиты, просрочка, группы, формулы
    t = k.scope("team", "north") # то же суммой по команде

Показатели (код → что это):
  sales    продажи за месяц, ₽ (возвраты с минусом)
  akb      активная клиентская база: клиентов с покупками от kpi.akb_min за месяц
  sku      позиции в точках: пар «клиент × группа товаров» с покупкой
  collect  занесённые деньги, ₽
  focus    продажи фокусных групп месяца, ₽ (список в настройке focus:<месяц>)
  visits   визиты с заказом: точки маршрута, где в тот день была продажа (план — точки маршрута на прошедшие дни)
  overdue  доля просрочки дольше kpi.debt_days[-1] дней (чем меньше — тем лучше) от базы kpi.debt_base:
           month — продажи месяца (по умолчанию), 30d — продажи за последние 30 дней, debt — весь долг;
           норма — kpi.debt_norm (по умолчанию 10%). Просрочка сверх любого порога — overdue_amount():
           точно, если переводчик дал её готовой (поле over), иначе из корзин 0–30/31–60/61–90/91+
           (порог внутри корзины — пропорционально, например 45 дней = половина 31–60 + 61–90 + 91+)
  grp:<код> продажи группы товаров
  <формула> свои показатели из formulas в config.yaml (модуль formulas)
"""
from collections import defaultdict
from dataclasses import dataclass, field

from core.calendar import Month
from core.data.db import month_bounds
from core.kpi import formula

TITLES = {
    "sales": ("Продажи", "₽"), "akb": ("АКБ", "кл."), "sku": ("SKU в точках", "поз."),
    "collect": ("Сбор денег", "₽"), "focus": ("Фокусные группы", "₽"), "visits": ("Визиты с заказом", "виз."),
    "overdue": ("Просрочка", "%"),
}
SELLERS = {"rep", "newbie"}     # у кого считаются личные продажи
MAIN = ["sales", "focus", "akb", "sku", "collect", "visits", "overdue"]


@dataclass
class Metric:
    code: str
    title: str
    fact: float = 0.0
    plan: float = 0.0
    unit: str = ""
    lower_better: bool = False
    need: float = 1.0          # доля месяца, которую надо было пройти к сегодня
    left_days: int = 0
    total_days: int = 0
    detail: dict = field(default_factory=dict)

    @property
    def pct(self):
        """Выполнение плана (1.0 = 100%). Для «меньше — лучше» — насколько уложились в предел."""
        if not self.plan:
            return None
        if self.lower_better:
            return self.plan / self.fact if self.fact else 2.0
        return self.fact / self.plan

    @property
    def left(self):
        if self.code == "visits":
            return 0          # план визитов — уже «на сегодня», добирать нечего
        return max(self.plan - self.fact, 0) if self.plan and not self.lower_better else 0

    @property
    def per_day(self):
        """Сколько нужно в день до конца месяца, чтобы выйти на план."""
        return self.left / self.left_days if self.left_days else 0

    @property
    def forecast(self):
        """Прогноз на конец месяца по текущему темпу."""
        if self.lower_better or self.code in ("akb", "sku"):
            return self.fact
        passed = self.total_days - self.left_days
        return self.fact / passed * self.total_days if passed else self.fact

    @property
    def tone(self):
        p = self.pct
        if p is None:
            return None
        if self.lower_better:
            return "good" if p >= 1 else "warn" if p >= 0.8 else "bad"
        need = self.need if self.code not in ("akb", "sku") else min(1.0, self.need + 0.2)
        if self.code == "visits":
            need = 0.85
        return "good" if p >= need - 1e-9 else "warn" if p >= need - 0.15 else "bad"


class Kpi:
    def __init__(self, cfg, db, month, today, hooks=None):
        self.cfg, self.db, self.month, self.hooks = cfg, db, month, hooks
        self.cal = Month(cfg, month, today)
        kcfg = cfg.raw.get("kpi") or {}
        self.akb_min = float(kcfg.get("akb_min", 1))
        self.debt_days = (kcfg.get("debt_days") or [30, 60])[-1]
        self.debt_norm = float(kcfg.get("debt_norm", 0.10))
        self.debt_base = kcfg.get("debt_base", "month")
        self.start, self.end = month_bounds(month)
        self._keys = db.key_map()
        self.plans = db.plans(month)
        self.focus = set(db.get(f"focus:{month}", []) or [])
        self.employees = {e["id"]: e for e in db.employees(active=None)}
        self._load()
        self._cache = {}

    # ---------- загрузка ----------

    def who(self, key, day):
        best = None
        for since, emp in self._keys.get(key, ()):
            if since <= day:
                best = emp
        return best

    def _load(self):
        s, e = self.start, self.end
        self.sales = defaultdict(float)                 # emp → ₽
        self.by_client = defaultdict(lambda: defaultdict(float))   # emp → клиент → ₽
        self.by_group = defaultdict(lambda: defaultdict(float))    # emp → группа → ₽
        self.pairs = defaultdict(set)                   # emp → {(клиент, группа)}
        self.sold_days = defaultdict(set)               # emp → {(день, клиент)}
        self.daily = defaultdict(lambda: defaultdict(float))       # emp → день → ₽
        self.unknown = defaultdict(float)
        for r in self.db.c.execute("SELECT day, emp_key, client, grp, amount FROM sales WHERE day BETWEEN ? AND ?", (s, e)):
            emp = self.who(r[1], r[0])
            if emp is None:
                self.unknown[r[1]] += r[4]
                continue
            self.sales[emp] += r[4]
            self.by_client[emp][r[2]] += r[4]
            self.by_group[emp][r[3]] += r[4]
            self.daily[emp][r[0]] += r[4]
            if r[4] > 0:
                self.pairs[emp].add((r[2], r[3]))
                self.sold_days[emp].add((r[0], r[2]))
        self.collect = defaultdict(float)
        for r in self.db.c.execute("SELECT day, emp_key, amount FROM payments WHERE day BETWEEN ? AND ?", (s, e)):
            emp = self.who(r[1], r[0])
            if emp is not None:
                self.collect[emp] += r[2]
        self.route = defaultdict(list)                  # emp → [(день, клиент)]
        for r in self.db.c.execute("SELECT day, emp_key, client FROM visits WHERE day BETWEEN ? AND ?", (s, e)):
            emp = self.who(r[1], r[0])
            if emp is not None:
                self.route[emp].append((r[0], r[2]))
        on = min(self.cal.today, self.cal.end)
        self.debt_on, debts = self.db.debts_on(on)
        self.debts = defaultdict(list)                  # emp → [строки долгов]
        for r in debts:
            emp = self.who(r["emp_key"], r["on"])
            if emp is not None:
                self.debts[emp].append(r)

    # ---------- показатели ----------

    def _m(self, code, fact, plan=0.0, **kw):
        title, unit = TITLES.get(code, (code, ""))
        return Metric(code, kw.pop("title", title), fact, plan or 0.0, kw.pop("unit", unit), need=kw.pop("need", self.cal.need),
                      left_days=self.cal.left, total_days=self.cal.total, **kw)

    def raw(self, emp):
        """Сырые значения сотрудника (для формул и сумм по командам)."""
        today = self.cal.today.isoformat()
        route_passed = [(d, c) for d, c in self.route.get(emp, []) if d < today or self.cal.closed]
        hit = sum(1 for dc in route_passed if dc in self.sold_days.get(emp, ()))
        overdue = sum(self.overdue_amount(r) for r in self.debts.get(emp, []))
        debt_total = sum(r["total"] for r in self.debts.get(emp, []))
        return {
            "sales": self.sales.get(emp, 0.0),
            "akb": sum(1 for v in self.by_client.get(emp, {}).values() if v >= self.akb_min),
            "sku": len(self.pairs.get(emp, ())),
            "collect": self.collect.get(emp, 0.0),
            "focus": sum(v for g, v in self.by_group.get(emp, {}).items() if g in self.focus),
            "visits": hit, "visits_plan": len(route_passed),
            "overdue": overdue, "debt": debt_total, "debt_base": self.base_of(emp, debt_total),
            "groups": dict(self.by_group.get(emp, {})),
        }

    def emp(self, emp_id):
        if emp_id in self._cache:
            return self._cache[emp_id]
        r = self.raw(emp_id)
        p = self.plans.get(emp_id, {})
        out = self._build(r, p)
        self._own_calendar(emp_id, out)
        if self.hooks:
            extra = self.hooks.run("kpi_extra", self, self.employees.get(emp_id), out)
            if extra:
                out.update(extra)
        self._cache[emp_id] = out
        return out

    def _own_calendar(self, emp_id, out):
        """Пришёл или ушёл посреди месяца — график «нужно к сегодня» и прогноз считаются по его дням."""
        e = self.employees.get(emp_id) or {}
        start = max(self.start, e.get("hired") or self.start)
        end = min(self.end, e.get("fired") or self.end)
        if start == self.start and end == self.end:
            return
        mine = [d for d in self.cal.workdays if start <= d.isoformat() <= end]
        if not mine:
            return
        passed = sum(1 for d in mine if d < self.cal.today) if not self.cal.closed else len(mine)
        for m in out.values():
            if isinstance(m, Metric) and m.code != "visits":
                m.total_days, m.left_days = len(mine), len(mine) - passed
                m.need = passed / len(mine)

    def _build(self, r, p):
        sales = r["sales"]
        out = {
            "sales": self._m("sales", sales, p.get("sales")),
            "focus": self._m("focus", r["focus"], p.get("focus")),
            "akb": self._m("akb", r["akb"], p.get("akb")),
            "sku": self._m("sku", r["sku"], p.get("sku")),
            "collect": self._m("collect", r["collect"], p.get("collect")),
            "visits": self._m("visits", r["visits"], r["visits_plan"], need=1.0),
            "overdue": self._m("overdue", (r["overdue"] / r["debt_base"]) if r["debt_base"] > 0 else (1.0 if r["overdue"] else 0.0),
                               p.get("overdue", self.debt_norm if r["debt"] else 0), lower_better=True,
                               detail={"sum": r["overdue"], "debt": r["debt"], "days": self.debt_days, "base": r["debt_base"],
                                       "to_norm": max(r["overdue"] - self.debt_norm * r["debt_base"], 0)}),
        }
        for g in sorted(set(r["groups"]) | {k[4:] for k in p if k.startswith("grp:")}):
            if not g:
                continue
            out[f"grp:{g}"] = self._m(f"grp:{g}", r["groups"].get(g, 0.0), p.get(f"grp:{g}"),
                                      title=self.group_name(g), unit="₽")
        for code, f in (self.cfg.raw.get("formulas") or {}).items():
            if not self.cfg.has_module("formulas"):
                break
            val = formula.evaluate(f["expr"], {k: (m.fact if isinstance(m, Metric) else m) for k, m in out.items()
                                               if ":" not in k})
            out[f"f:{code}"] = self._m(f"f:{code}", val, title=f.get("name", code), unit=f.get("unit", ""))
        return out

    def group_name(self, code):
        if not hasattr(self, "_groups"):
            self._groups = {r["code"]: r["name"] for r in self.db.q("SELECT code, name FROM groups")}
        return self._groups.get(code, code)

    # ---------- группы людей ----------

    def members(self, kind, ident=None):
        """Продавцы в области: all | dept | team. Только те, у кого есть продажи или планы, или роль rep/sv."""
        out = []
        for e in self.employees.values():
            if not e["active"] and e["id"] not in self.sales:
                continue
            if e["role"] not in SELLERS:
                continue
            if (e.get("hired") or "") > self.end or (e.get("fired") or "9999") < self.start:
                continue            # не работал в этом месяце
            if kind == "team" and e["team"] != ident:
                continue
            if kind == "dept" and e["dept"] != ident:
                continue
            out.append(e["id"])
        return out

    def scope(self, kind, ident=None):
        """Сумма показателей по команде / отделу / компании."""
        key = ("scope", kind, ident)
        if key not in self._cache:
            self._cache[key] = self._scope(kind, ident)
        return self._cache[key]

    def _scope(self, kind, ident):
        ids = self.members(kind, ident)
        tot = defaultdict(float)
        groups = defaultdict(float)
        plans = defaultdict(float)
        for i in ids:
            r = self.raw(i)
            for k, v in r.items():
                if k == "groups":
                    for g, x in v.items():
                        groups[g] += x
                else:
                    tot[k] += v
            for k, v in self.plans.get(i, {}).items():
                plans[k] += v
        tot["groups"] = dict(groups)
        if tot.get("debt") and not plans.get("overdue"):
            plans["overdue"] = self.debt_norm
        return self._build(tot, plans)

    # ---------- дебиторка ----------

    BUCKETS = (("d0_30", 0, 30), ("d31_60", 30, 60), ("d61_90", 60, 90), ("d91", 90, None))

    def overdue_amount(self, r, days=None):
        """Просрочка строки долга старше days дней (по умолчанию — главный порог анкеты)."""
        days = self.debt_days if days is None else days
        if days == self.debt_days and r.get("over") is not None:
            return r["over"]
        out = 0.0
        for col, lo, hi in self.BUCKETS:
            v = r.get(col) or 0
            if hi is None or days <= lo:          # 91+ целиком (для порога больше 90 — приближённо)
                out += v
            elif days < hi:
                out += v * (hi - days) / (hi - lo)
        return out

    def sales_30d(self, emp):
        """Продажи за 30 дней до сегодня (или до конца месяца) — база нормы просрочки «30d»."""
        if not hasattr(self, "_s30"):
            from datetime import timedelta
            end = min(self.cal.today, self.cal.end + timedelta(days=1))
            start = end - timedelta(days=30)
            self._s30 = defaultdict(float)
            for r in self.db.c.execute("SELECT day, emp_key, amount FROM sales WHERE day >= ? AND day < ?",
                                       (start.isoformat(), end.isoformat())):
                e = self.who(r[1], r[0])
                if e is not None:
                    self._s30[e] += r[2]
        return self._s30.get(emp, 0.0)

    def base_of(self, emp, debt_total):
        if self.debt_base == "debt":
            return debt_total
        if self.debt_base == "30d":
            return self.sales_30d(emp)
        return self.sales.get(emp, 0.0)

    def debt_share(self, ids, days=None, of=None):
        """(просрочка, база, доля) по людям ids: порог days, база of (month | 30d | debt | sales = month)."""
        of = {"sales": "month"}.get(of, of) or self.debt_base
        over = sum(self.overdue_amount(r, days) for i in ids for r in self.debts.get(i, []))
        debt = sum(r["total"] for i in ids for r in self.debts.get(i, []))
        base = debt if of == "debt" else sum(self.sales_30d(i) if of == "30d" else self.sales.get(i, 0.0) for i in ids)
        share = over / base if base > 0 else (1.0 if over else 0.0)
        return over, base, share

    def rating(self, ids, metric="sales"):
        """[(сотрудник, Metric)] по убыванию выполнения плана (без плана — по факту)."""
        rows = [(i, self.emp(i)[metric]) for i in ids]
        return sorted(rows, key=lambda x: (x[1].pct if x[1].pct is not None else -1, x[1].fact), reverse=True)
