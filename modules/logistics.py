"""Модуль «Логистика»: рейсы водителей по маршрутным листам 1С — пробег, вес, объём, точки доставки;
двойные заезды (один клиент в один день в двух рейсах), дальние полупустые рейсы, доставка к продажам;
«Мой день» водителя (выезд, возврат, ночёвка, деньги с клиентов, привоз) и отметки диспетчера; сдельная оплата.

Сдельная оплата — кубиком зарплаты per_unit в анкете клиента, единицы считает этот модуль:
  km       пробег за месяц          kg / m3  вес / объём за месяц
  points   точки доставки (клиент в рейсе)   trips   рейсы
  nights   ночёвки в рейсе          overtime минуты до начала и после конца рабочего дня (logistics.day_start / day_end)
  cash     клиентов, с которых водитель забрал деньги
  pickup   привоз от поставщика: до 1 м³ — 1, до 2 — 1,5, до 4 — 2, больше — 2,5
Например водителю: {type: per_unit, unit: km, price: "4"}, {type: per_unit, unit: points, price: "25"}.
Отметки дня: сам водитель — сегодня и вчера; диспетчер, начальник логистики, директор — любой день открытого месяца.
Настройки (logistics в анкете): day_start "08:00", day_end "18:00", far_km 150, far_points 3, cost_share "3%".
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.calendar import day_title, month_title
from core.pay import components as C
from core.month import cycle
from core.ui.kit import Button, Reply, money, num, pct, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "logistics"


def leader(ctx):
    """Видит всех водителей и отмечает за них: директор, админ, начальник логистики, диспетчер."""
    return ctx.role in ("owner", "admin") or (ctx.role == "head" and ctx.dept_kind == "logistics") or ctx.duty == "dispatcher"


def is_driver(ctx):
    return ctx.duty == "driver" or (ctx.role == "staff" and ctx.dept_kind == "logistics" and not ctx.duty)


menu("lg", "🚚 Рейсы", {"owner", "admin", "head", "staff"}, module=M, order=31, when=leader)
menu("lgk", "📝 Отметки водителей", {"head", "staff"}, module=M, order=32, when=leader)
menu("dm", "🚚 Мой день", {"staff"}, module=M, order=30, when=is_driver)
menu("lgd", "📋 Мои рейсы", {"staff"}, module=M, order=31, when=is_driver)


def _lcfg(ctx_or_cfg):
    cfg = getattr(ctx_or_cfg, "cfg", ctx_or_cfg)
    c = cfg.raw.get("logistics") or {}
    share = str(c.get("cost_share", "3%")).replace("%", "").replace(",", ".")
    return {"start": c.get("day_start", "08:00"), "end": c.get("day_end", "18:00"), "far_km": float(c.get("far_km", 150)),
            "far_points": int(c.get("far_points", 3)), "share": float(share) / 100}


class Trips:
    """Рейсы месяца по водителям (ключи 1С переведены в сотрудников)."""

    def __init__(self, db, month, keymap_who):
        from core.data.db import month_bounds
        s, e = month_bounds(month)
        self.trips = defaultdict(dict)            # водитель → {(день, рейс): {...}}
        self.unknown = set()
        for r in db.q("SELECT * FROM trips WHERE day BETWEEN ? AND ?", s, e):
            emp = keymap_who(r["emp_key"], r["day"])
            if emp is None:
                self.unknown.add(r["emp_key"])
                continue
            self.trips[emp][(r["day"], r["trip"])] = {"km": r["km"], "kg": r["kg"], "m3": r["m3"], "stops": set()}
        self.stops = defaultdict(list)            # (день, клиент) → [(водитель, рейс)]
        for r in db.q("SELECT * FROM trip_stops WHERE day BETWEEN ? AND ?", s, e):
            emp = keymap_who(r["emp_key"], r["day"])
            if emp is None:
                continue
            t = self.trips[emp].get((r["day"], r["trip"]))
            if t is not None:
                t["stops"].add(r["client"])
            pair = (emp, r["trip"])
            if pair not in self.stops[(r["day"], r["client"])]:
                self.stops[(r["day"], r["client"])].append(pair)

    def totals(self, emp):
        ts = self.trips.get(emp, {})
        return {"trips": len(ts), "km": sum(t["km"] for t in ts.values()), "kg": sum(t["kg"] for t in ts.values()),
                "m3": sum(t["m3"] for t in ts.values()), "points": sum(len(t["stops"]) for t in ts.values()),
                "days": len({d for d, _ in ts})}

    def doubles(self):
        return [(d, c, v) for (d, c), v in sorted(self.stops.items()) if len({t for _, t in v}) > 1]


def trips(ctx, month=None):
    month = month or ctx.month
    return ctx.app.memo(ctx, "trips", lambda: Trips(ctx.db, month, ctx.kpi(month).who), month)


# ---------- единицы для сдельной оплаты ----------

def _unit(code):
    def fn(pay, emp):
        cache = pay.__dict__.setdefault("_trips", None)
        if cache is None:
            pay._trips = cache = Trips(pay.db, pay.kpi.month, pay.kpi.who)
        return cache.totals(emp["id"])[code]
    return fn


for _code in ("km", "kg", "m3", "points", "trips"):
    C.unit(_code, _unit(_code))


def _all_points(pay, emp):
    """Отгрузки всего отдела за месяц (точки всех рейсов) — сдельная часть склада и сборщиков."""
    cache = pay.__dict__.setdefault("_trips", None)
    if cache is None:
        pay._trips = cache = Trips(pay.db, pay.kpi.month, pay.kpi.who)
    return sum(cache.totals(e)["points"] for e in cache.trips)


C.unit("all_points", _all_points)


# ---------- «Мой день»: отметки водителя ----------
# База: ключ dm:<месяц> = {id водителя: {день: {out, back, night, cash, pick: [[м³, кг, что]]}}}

def marks(db, month):
    return db.get(f"dm:{month}") or {}


def _mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def overtime(cfg, m):
    """Минуты до начала и после конца рабочего дня; возврат раньше выезда — значит после полуночи."""
    if m.get("night") or not m.get("out") or not m.get("back"):
        return 0
    lc = _lcfg(cfg)
    o, b, s_, e = _mins(m["out"]), _mins(m["back"]), _mins(lc["start"]), _mins(lc["end"])
    if b < o:
        b += 24 * 60
    return max(0, s_ - o) + max(0, b - e)


def pick_coef(m3):
    return 1 if m3 <= 1 else 1.5 if m3 <= 2 else 2 if m3 <= 4 else 2.5


def mark_totals(cfg, ms):
    days = ms.values()
    return {"nights": sum(1 for m in days if m.get("night")), "overtime": sum(overtime(cfg, m) for m in days),
            "cash": sum(int(m.get("cash") or 0) for m in days),
            "pickup": sum(pick_coef(p[0]) for m in days for p in m.get("pick") or [])}


def _mark_unit(code):
    def fn(pay, emp):
        return mark_totals(pay.cfg, marks(pay.db, pay.kpi.month).get(str(emp["id"]), {}))[code]
    return fn


for _code in ("nights", "overtime", "cash", "pickup"):
    C.unit(_code, _mark_unit(_code))


# ---------- экраны ----------

@screen("lg", module=M)
def overview(ctx):
    if not leader(ctx):
        return driver(ctx)
    t = trips(ctx)
    drivers = sorted(t.trips, key=lambda e: -t.totals(e)["points"])
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    tot = defaultdict(float)
    rs = []
    for e in drivers:
        x = t.totals(e)
        for k_, val in x.items():
            tot[k_] += val
        rs.append(Row([short(names.get(e, "?")), str(x["trips"]), num(round(x["km"])), num(round(x["kg"])), str(x["points"])],
                      sub=f"дней в рейсе {x['days']} · {num(round(x['points'] / x['trips'], 1)) if x['trips'] else 0} точек за рейс"))
    v = View("🚚 Рейсы водителей", month_title(ctx.month))
    v.tiles([("Рейсов", num(tot["trips"]), None), ("Пробег, км", num(round(tot["km"])), None),
             ("Точек доставки", num(tot["points"]), None), ("Двойные заезды", str(len(t.doubles())),
                                                            "warn" if t.doubles() else "good")])
    v.table(["Водитель", "Рейсов", "Км", "Кг", "Точек"], rs, title="Рейтинг по точкам доставки") if rs else \
        v.note("Рейсов в этом месяце нет — пришлите отчёт 1С с маршрутными листами.")
    if t.unknown:
        v.note("Водители из 1С, которых нет в списке сотрудников: " + ", ".join(sorted(t.unknown)[:4]))
    far = far_trips(ctx, t)
    if far:
        v.note(f"🛣 Дальних полупустых рейсов: {len(far)} — пробег {num(round(sum(x[3]['km'] for x in far)))} км.")
    kb = rows([Button(short(names.get(e, "?")), f"lgd:{e}") for e in drivers], 3)
    kb.append([Button(f"🔁 Двойные заезды ({len(t.doubles())})", "lgx"), Button(f"🛣 Дальние ({len(far)})", "lgf")])
    kb.append([Button("📝 Отметки дня", "lgk"), Button("💸 Доставка к продажам", "lgs")])
    if ctx.can("fuel"):
        kb.append([Button("⛽ ГСМ", "fu")])
    return Reply(view=v, buttons=kb)


@screen("lgd", module=M)
def driver(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    t = trips(ctx)
    x = t.totals(emp["id"])
    v = View(f"🚚 {emp['fio']}", f"рейсы · {month_title(ctx.month).lower()}")
    v.tiles([("Рейсов", str(x["trips"]), None), ("Пробег, км", num(round(x["km"])), None), ("Точек", str(x["points"]), None),
             ("Вес, кг", num(round(x["kg"])), None)])
    rs = [Row([day_title(date.fromisoformat(d)), f"№{n}", num(round(r["km"])), num(round(r["kg"])), str(len(r["stops"]))])
          for (d, n), r in sorted(t.trips.get(emp["id"], {}).items(), reverse=True)[:25]]
    v.table(["День", "Рейс", "Км", "Кг", "Точек"], rs) if rs else v.note("Рейсов нет.")
    mt = mark_totals(ctx.cfg, marks(ctx.db, ctx.month).get(str(emp["id"]), {}))
    v.lines([f"🌙 Ночёвок {mt['nights']} · ⏱ переработка {num(mt['overtime'])} мин. · 💵 деньги с {mt['cash']} клиентов · "
             f"📦 привозов {num(mt['pickup'])} ед."], title="Отметки за месяц")
    kb = [[Button("🚚 День", f"dm:{emp['id']}")]]
    if ctx.can("payroll"):
        kb[0].append(Button("💰 Зарплата", f"pay:{emp['id']}"))
    if leader(ctx):
        kb.append([Button("‹ Рейсы", "lg")])
    return Reply(view=v, buttons=kb)


@screen("lgx", module=M)
def doubles(ctx):
    if not leader(ctx):
        return driver(ctx)
    t = trips(ctx)
    names = {e["id"]: short(e["fio"]) for e in ctx.db.employees(active=None)}
    ds = t.doubles()
    v = View("🔁 Двойные заезды", f"{month_title(ctx.month)} · клиент в один день в двух и больше рейсах")
    v.cards([Card(ctx.db.client_name(c), day_title(date.fromisoformat(d)),
                  [" · ".join(f"{names.get(e, '?')} рейс №{n}" for e, n in trips_)], color="warn") for d, c, trips_ in ds[-15:]])
    if not ds:
        v.note("Двойных заездов нет 👍")
    else:
        v.note("Обычно это дозаказ после отгрузки или забытая накладная — лишний рейс и бензин.")
    return Reply(view=v, buttons=[[Button("‹ Рейсы", "lg")]])


# ---------- дальние рейсы и доставка к продажам ----------

def far_trips(ctx, t=None):
    """[(водитель, день, рейс, данные)] — далеко и почти пусто: пробег ≥ far_km и точек ≤ far_points."""
    t = t or trips(ctx)
    lc = _lcfg(ctx)
    out = [(e, d, n, r) for e, ts in t.trips.items() for (d, n), r in ts.items()
           if r["km"] >= lc["far_km"] and len(r["stops"]) <= lc["far_points"]]
    return sorted(out, key=lambda x: (x[1], x[2]), reverse=True)


@screen("lgf", module=M)
def far(ctx):
    if not leader(ctx):
        return driver(ctx)
    lc = _lcfg(ctx)
    names = {e["id"]: short(e["fio"]) for e in ctx.db.employees(active=None)}
    fs = far_trips(ctx)
    v = View("🛣 Дальние рейсы", f"{month_title(ctx.month)} · от {num(lc['far_km'])} км и не больше {lc['far_points']} точек")
    v.tiles([("Рейсов", str(len(fs)), "warn" if fs else "good"), ("Пробег, км", num(round(sum(r["km"] for *_, r in fs))), None)])
    v.cards([Card(f"{names.get(e, '?')} · рейс №{n}", day_title(date.fromisoformat(d)),
                  [f"{num(round(r['km']))} км · {len(r['stops'])} точ. · {num(round(r['kg']))} кг",
                   ", ".join(ctx.db.client_name(c) for c in sorted(r["stops"]))], color="warn") for e, d, n, r in fs[:15]])
    if not fs:
        v.note("Дальних полупустых рейсов нет 👍")
    else:
        v.note("Такой рейс можно было совместить с другим или собрать больше точек по пути.")
    return Reply(view=v, buttons=[[Button("‹ Рейсы", "lg")]])


def _prices(ctx, emp):
    p = ctx.pay()
    out = {}
    for c in p.schemes.get(p.scheme_of(emp), []):
        if c.get("type") == "per_unit" and c.get("unit") in ("km", "points", "kg") and c.get("price"):
            out[c["unit"]] = float(c["price"])
    return out


def delivery(ctx):
    """Сдельная часть каждого рейса (км, вес, точки по ценам водителя) поровну на клиентов рейса против их продаж в тот день."""
    from core.data.db import month_bounds
    t = trips(ctx)
    s, e = month_bounds(ctx.month)
    sales = defaultdict(float)
    for r in ctx.db.q("SELECT day, client, SUM(amount) AS a FROM sales WHERE day BETWEEN ? AND ? GROUP BY day, client", s, e):
        sales[(r["day"], r["client"])] = r["a"]
    emps = {x["id"]: x for x in ctx.db.employees(active=None)}
    per = defaultdict(lambda: {"n": 0, "cost": 0.0, "sales": 0.0})
    for emp, ts in t.trips.items():
        pr = _prices(ctx, emps[emp]) if emp in emps else {}
        for (d, _), r in ts.items():
            if not r["stops"]:
                continue
            cost = r["km"] * pr.get("km", 0) + r["kg"] * pr.get("kg", 0) + len(r["stops"]) * pr.get("points", 0)
            for c in r["stops"]:
                x = per[c]
                x["n"] += 1
                x["cost"] += cost / len(r["stops"])
                x["sales"] += sales.get((d, c), 0)
    return per


@screen("lgs", module=M)
def to_sales(ctx):
    if not leader(ctx):
        return driver(ctx)
    lc = _lcfg(ctx)
    per = delivery(ctx)
    cost, sold = sum(x["cost"] for x in per.values()), sum(x["sales"] for x in per.values())
    bad = sorted(((c, x) for c, x in per.items() if x["sales"] <= 0 or x["cost"] / x["sales"] > lc["share"]),
                 key=lambda t_: -t_[1]["cost"])
    v = View("💸 Доставка к продажам", f"{month_title(ctx.month)} · сдельная часть рейса поровну на его клиентов")
    v.tiles([("Доставка", money(cost), None), ("Продажи с доставкой", money(sold), None),
             ("Доля доставки", pct(cost / sold, 1) if sold else "—", None),
             (f"Дороже {pct(lc['share'])}", str(len(bad)), "warn" if bad else "good")])
    v.table(["Клиент", "Доставок", "Доставка", "Продажи", "Доля"],
            [Row([ctx.db.client_name(c)[:28], str(x["n"]), money(x["cost"], ""), money(x["sales"], ""),
                  Cell(pct(x["cost"] / x["sales"], 1) if x["sales"] > 0 else "нет", "bad")], sub=f"код {c}")
             for c, x in bad[:20]], title="Где доставка съедает продажу") if bad else v.note("Везде доставка в норме 👍")
    v.note("Кандидаты на доставку реже (раз в неделю), на заказ от минимальной суммы или на самовывоз.")
    return Reply(view=v, buttons=[[Button("‹ Рейсы", "lg")]])


# ---------- «Мой день» и отметки диспетчера ----------

def _open(ctx, day):
    from core.calendar import month_of
    return cycle.status(ctx.db, month_of(day)) != "approved"


def may_mark(ctx, emp, day):
    if not _open(ctx, day) or day > ctx.today:
        return False
    if leader(ctx):
        return True
    return bool(ctx.me and emp["id"] == ctx.me["id"] and (ctx.today - day).days <= 1)


def _get(ctx, emp_id, day):
    from core.calendar import month_of
    return marks(ctx.db, month_of(day)).get(str(emp_id), {}).get(day.isoformat(), {})


def _put(ctx, emp_id, day, m):
    from core.calendar import month_of
    key = f"dm:{month_of(day)}"
    allm = ctx.db.get(key) or {}
    days = allm.setdefault(str(emp_id), {})
    if m:
        days[day.isoformat()] = m
    else:
        days.pop(day.isoformat(), None)
    ctx.db.put(key, allm)


def _drivers(ctx):
    return [e for e in ctx.db.employees() if (ctx.cfg.position(e["position"]) or {}).get("duty") == "driver"]


def _args(ctx, emp_id, day):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    d = date.fromisoformat(day) if day else ctx.today
    return emp, d


@screen("dm", module=M)
def my_day(ctx, emp_id=None, day=None):
    emp, d = _args(ctx, emp_id, day)
    if not emp or not (ctx.sees(emp) or leader(ctx)):
        return Reply(text="«Мой день» — у водителей; отмечать за них может диспетчер.")
    m = _get(ctx, emp["id"], d)
    t = trips(ctx)
    day_trips = {n: r for (dd, n), r in t.trips.get(emp["id"], {}).items() if dd == d.isoformat()}
    ot = overtime(ctx.cfg, m)
    lc = _lcfg(ctx)
    v = View(f"🚚 {short(emp['fio'])} · {day_title(d)}", f"рабочий день {lc['start']}–{lc['end']}")
    v.tiles([("Выехал", m.get("out") or "—", None), ("Вернулся", m.get("back") or ("🌙 ночёвка" if m.get("night") else "—"), None),
             ("Переработка", f"{ot} мин." if ot else "—", "good" if ot else None)])
    lines = [f"Рейс №{n}: {num(round(r['km']))} км · {len(r['stops'])} точ. · {num(round(r['kg']))} кг"
             for n, r in sorted(day_trips.items())] or ["Рейсов в 1С за этот день пока нет."]
    v.lines(lines, title="Рейсы по 1С")
    extra = []
    if m.get("night"):
        extra.append("🌙 Ночёвка в рейсе")
    if m.get("cash"):
        extra.append(f"💵 Деньги забрал у {m['cash']} клиентов")
    for p in m.get("pick") or []:
        extra.append(f"📦 Привоз: {num(p[0])} м³ · {num(p[1])} кг{(' · ' + p[2]) if len(p) > 2 and p[2] else ''} "
                     f"(коэф. {num(pick_coef(p[0]))})")
    if extra:
        v.lines(extra, title="Отметки")
    if d == ctx.today and not m:
        y = d - timedelta(days=1)
        ym = _get(ctx, emp["id"], y)
        if ym:
            v.note(f"Вчера: выезд {ym.get('out', '—')} · " + ("ночёвка" if ym.get("night") else f"возврат {ym.get('back', '—')}")
                   + (f" · переработка {overtime(ctx.cfg, ym)} мин." if overtime(ctx.cfg, ym) else ""))
        v.note("Утром — «🚀 Выехал сейчас», вечером — «🏁 Вернулся сейчас». Забыли — «🕐 Ввести время».")
    e, iso = emp["id"], d.isoformat()
    kb = []
    if may_mark(ctx, emp, d):
        if d == ctx.today:
            kb.append([Button("🚀 Выехал сейчас", f"dmo:{e}:{iso}"), Button("🏁 Вернулся сейчас", f"dmr:{e}:{iso}")])
        kb.append([Button("🕐 Ввести время", f"dmt:{e}:{iso}"),
                   Button("🌙 Убрать ночёвку" if m.get("night") else "🌙 Ночёвка", f"dmn:{e}:{iso}")])
        kb.append([Button("💵 Деньги с клиентов", f"dmc:{e}:{iso}"), Button("📦 Привоз", f"dmp:{e}:{iso}")])
        if m:
            kb.append([Button("🗑 Очистить день", f"dmx:{e}:{iso}")])
    else:
        v.note("Отметить можно сегодня и вчера; за другие дни — диспетчер или начальник логистики."
               if _open(ctx, d) else "Месяц утверждён — отметки закрыты.")
    nav = [Button("◀ " + day_title(d - timedelta(days=1)), f"dm:{e}:{(d - timedelta(days=1)).isoformat()}")]
    if d < ctx.today:
        nav.append(Button(day_title(d + timedelta(days=1)) + " ▶", f"dm:{e}:{(d + timedelta(days=1)).isoformat()}"))
    kb.append(nav)
    if leader(ctx):
        kb.append([Button("‹ Отметки дня", f"lgk:{iso}")])
    return Reply(view=v, buttons=kb)


def _change(ctx, emp_id, day, fn):
    emp, d = _args(ctx, emp_id, day)
    if not emp or not may_mark(ctx, emp, d):
        return Reply(text="Этот день отметить нельзя.")
    m = dict(_get(ctx, emp["id"], d))
    fn(m)
    _put(ctx, emp["id"], d, {k: v for k, v in m.items() if v})
    return my_day(ctx, str(emp["id"]), d.isoformat())


def _now():
    return datetime.now().strftime("%H:%M")


@screen("dmo", module=M)
def mark_out(ctx, emp_id, day):
    return _change(ctx, emp_id, day, lambda m: m.update(out=_now()))


@screen("dmr", module=M)
def mark_back(ctx, emp_id, day):
    return _change(ctx, emp_id, day, lambda m: m.update(back=_now(), night=0))


@screen("dmn", module=M)
def mark_night(ctx, emp_id, day):
    return _change(ctx, emp_id, day, lambda m: m.update(night=0 if m.get("night") else 1))


@screen("dmx", module=M)
def mark_clear(ctx, emp_id, day):
    return _change(ctx, emp_id, day, lambda m: m.clear())


def _ask(ctx, kind, emp_id, day, prompt):
    emp, d = _args(ctx, emp_id, day)
    if not emp or not may_mark(ctx, emp, d):
        return Reply(text="Этот день отметить нельзя.")
    return ctx.ask(kind, prompt, {"emp": emp["id"], "day": d.isoformat()})


@screen("dmt", module=M)
def ask_time(ctx, emp_id, day):
    return _ask(ctx, "dm_time", emp_id, day, "Во сколько выехал и вернулся? Например:\n\n07:10 19:40\n\n"
                                              "Только выезд — «07:10», только возврат — «- 19:40».")


@screen("dmc", module=M)
def ask_cash(ctx, emp_id, day):
    return _ask(ctx, "dm_cash", emp_id, day, "У скольких клиентов забрали деньги? Напишите число (0 — убрать).")


@screen("dmp", module=M)
def ask_pick(ctx, emp_id, day):
    return _ask(ctx, "dm_pick", emp_id, day, "Что привезли от поставщика: объём м³, вес кг и что — например:\n\n"
                                              "2,5 450 трубы\n\nКаждый привоз — отдельной строкой.")


TIME = re.compile(r"(\d{1,2})[:.](\d{2})")


def _hm(x):
    m = TIME.fullmatch(x)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


@text_input("dm_time")
def on_time(ctx, text, data):
    parts = (text or "").replace(",", " ").split()
    if not parts or len(parts) > 2:
        return ctx.ask("dm_time", "Не понял. Напишите «07:10 19:40».", data)
    out_ = None if parts[0] in ("-", "—") else _hm(parts[0])
    back = _hm(parts[1]) if len(parts) > 1 else None
    if (parts[0] not in ("-", "—") and out_ is None) or (len(parts) > 1 and back is None):
        return ctx.ask("dm_time", "Время пишите так: 07:10 (часы:минуты).", data)

    def fn(m):
        if out_:
            m["out"] = out_
        if back:
            m["back"], m["night"] = back, 0
    return _change(ctx, str(data["emp"]), data["day"], fn)


@text_input("dm_cash")
def on_cash(ctx, text, data):
    t = (text or "").strip()
    if not t.isdigit() or int(t) > 200:
        return ctx.ask("dm_cash", "Напишите число клиентов, например 3.", data)
    return _change(ctx, str(data["emp"]), data["day"], lambda m: m.update(cash=int(t)))


@text_input("dm_pick")
def on_pick(ctx, text, data):
    picks = []
    for line in (text or "").splitlines():
        p = line.replace(",", ".").split(maxsplit=2)
        if not p:
            continue
        try:
            m3, kg = float(p[0]), float(p[1]) if len(p) > 1 else 0.0
        except ValueError:
            return ctx.ask("dm_pick", f"Не понял «{line.strip()}». Пишите «2,5 450 трубы».", data)
        if m3 <= 0 or m3 > 100:
            return ctx.ask("dm_pick", "Объём — от 0,1 до 100 м³.", data)
        picks.append([m3, kg, p[2][:40] if len(p) > 2 else ""])
    if not picks:
        return ctx.ask("dm_pick", "Напишите «2,5 450 трубы».", data)
    return _change(ctx, str(data["emp"]), data["day"], lambda m: m.update(pick=(m.get("pick") or []) + picks))


@screen("lgk", module=M)
def day_marks(ctx, day=None):
    if not leader(ctx):
        return my_day(ctx)
    d = date.fromisoformat(day) if day else ctx.today
    rs, kb = [], []
    for e in _drivers(ctx):
        m = _get(ctx, e["id"], d)
        ot = overtime(ctx.cfg, m)
        extra = (["ночёвка"] if m.get("night") else []) + ([f"деньги с {m['cash']} кл."] if m.get("cash") else []) + \
            [f"привоз {num(p[0])} м³" for p in m.get("pick") or []]
        rs.append(Row([short(e["fio"]), m.get("out") or Cell("—", "warn" if d < ctx.today else None),
                       m.get("back") or ("ночёвка" if m.get("night") else Cell("—", "warn" if d < ctx.today else None)),
                       Cell(f"{ot} мин", "good") if ot else ""], sub=" · ".join(extra)))
        kb.append(Button(short(e["fio"]), f"dm:{e['id']}:{d.isoformat()}"))
    v = View("📝 Отметки водителей", f"{day_title(d)} · нажмите водителя, чтобы отметить")
    v.table(["Водитель", "Выезд", "Возврат", "Сверх"], rs) if rs else v.note("Водителей нет.")
    v.note("Переработка — минуты до начала и после конца рабочего дня; при ночёвке не считается.")
    out = rows(kb, 3)
    nav = [Button("◀ " + day_title(d - timedelta(days=1)), f"lgk:{(d - timedelta(days=1)).isoformat()}")]
    if d < ctx.today:
        nav.append(Button(day_title(d + timedelta(days=1)) + " ▶", f"lgk:{(d + timedelta(days=1)).isoformat()}"))
    out += [nav, [Button("‹ Рейсы", "lg")]]
    return Reply(view=v, buttons=out)
