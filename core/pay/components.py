"""Кубики зарплаты. Схема сотрудника в config.yaml — список кубиков с настройками:

    pay_schemes:
      rep:
        - {type: salary, amount: 25000}
        - {type: sales_percent, rate: 0.004}
        - {type: plan_bonus, metric: sales, mode: steps, steps: [[0.8, 5000], [1.0, 15000]]}

Кубик = функция (ctx, emp, params) → список строк расчёта (название, сумма, пояснение).
Клиент может добавить свой кубик в clients/<id>/rules.py: ``hooks.component("тип", fn, check)``.

Типы:
  salary         оклад за отработанные дни: amount (или pay.salary в анкете, или личный оклад сотрудника)
                 ÷ salary_days × отработанные дни
  sales_percent  процент от продаж: rate; scope = self | team | dept; min_plan — платить, только если план
                 выполнен на эту долю; rate_above — другая ставка на часть сверх плана
  plan_bonus     бонус за выполнение плана по показателю metric (sales, akb, sku, collect, focus, grp:<код>,
                 visits); scope; mode = steps (steps: [[доля, сумма], …] — берётся высшая достигнутая ступень)
                 или linear (amount × выполнение, порог min, потолок cap)
  fixed_bonus    фиксированный бонус name/amount; cut_per_miss — минус за каждый пропуск дня
  debt_penalty   удержание amount, если просрочка дольше days дней больше доли share от продаж
  timesheet      штрафы табеля: опоздания и прогулы (правила — timesheet в анкете)
  per_unit       сдельно: unit — единица (км, кг, точки, ошибки…), price — сумма за единицу или rate — доля
                 от суммы (для единиц в ₽); cut: true — это удержание (минус). Единицы дают модули (UNITS).
"""

# Единицы для сдельных кубиков: код → (название, модуль). Считает их модуль: ``unit("km", fn)``,
# fn(payroll, emp) → количество за месяц. Модуль не подключён — кубик даёт ноль с пояснением.
UNIT_TITLES = {
    "km": ("Пробег, км", "logistics"), "kg": ("Вес, кг", "logistics"), "m3": ("Объём, м³", "logistics"),
    "points": ("Точки доставки", "logistics"), "trips": ("Рейсы", "logistics"),
    "ret_own": ("Возвраты по своей вине, ₽", "returns"), "ret_err": ("Ошибки в возвратах", "returns"),
}
UNITS = {}


def unit(code, fn):
    """Модуль регистрирует, как считать единицу."""
    UNITS[code] = fn

SCOPES = {"self", "team", "dept"}
METRICS = {"sales", "akb", "sku", "collect", "focus", "visits"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _metric_ok(m):
    return m in METRICS or (isinstance(m, str) and m.startswith("grp:"))


# ---------- проверки настроек (для проверки анкеты) ----------

def check(c):
    """Список проблем одного кубика."""
    t = c.get("type")
    p = []
    if t == "salary":
        if "amount" in c and (not _num(c["amount"]) or c["amount"] < 0):
            p.append("salary.amount — неотрицательное число")
    elif t == "sales_percent":
        if not _num(c.get("rate")) or not 0 <= c["rate"] < 1:
            p.append("sales_percent.rate — доля от 0 до 1 (0,4% = 0.004)")
        if "rate_above" in c and (not _num(c["rate_above"]) or not 0 <= c["rate_above"] < 1):
            p.append("sales_percent.rate_above — доля от 0 до 1")
    elif t == "plan_bonus":
        if not _metric_ok(c.get("metric")):
            p.append(f"plan_bonus.metric «{c.get('metric')}» — один из {', '.join(sorted(METRICS))} или grp:<код>")
        mode = c.get("mode", "steps")
        if mode == "steps":
            st = c.get("steps")
            if not st or not all(isinstance(s, (list, tuple)) and len(s) == 2 and _num(s[0]) and _num(s[1]) for s in st):
                p.append("plan_bonus.steps — список пар [доля плана, сумма], например [[0.8, 5000], [1.0, 15000]]")
        elif mode == "linear":
            if not _num(c.get("amount")):
                p.append("plan_bonus.amount — сумма бонуса за 100% плана")
        else:
            p.append(f"plan_bonus.mode «{mode}» — steps или linear")
    elif t == "fixed_bonus":
        if not _num(c.get("amount")):
            p.append("fixed_bonus.amount — сумма бонуса")
    elif t == "debt_penalty":
        if not _num(c.get("amount")) or not _num(c.get("share", 0.1)):
            p.append("debt_penalty: amount — сумма удержания, share — допустимая доля просрочки")
    elif t == "timesheet":
        pass
    elif t == "per_unit":
        if c.get("unit") not in UNIT_TITLES:
            p.append(f"per_unit.unit «{c.get('unit')}» — одна из: {', '.join(UNIT_TITLES)}")
        if not _num(c.get("price")) and not _num(c.get("rate")):
            p.append("per_unit: price — сумма за единицу («4») или rate — доля («3%»)")
    if c.get("scope", "self") not in SCOPES:
        p.append(f"scope «{c.get('scope')}» — self, team или dept")
    return p


# ---------- расчёт ----------

def _scope_metric(ctx, emp, c, code):
    scope = c.get("scope", "self")
    if scope == "team":
        return ctx.kpi.scope("team", emp["team"]).get(code)
    if scope == "dept":
        return ctx.kpi.scope("dept", emp["dept"]).get(code)
    return ctx.kpi.emp(emp["id"]).get(code)


def salary(ctx, emp, c):
    base = emp.get("salary")
    if base is None:
        base = c.get("amount", ctx.default_salary)
    days = ctx.worked_days(emp)
    amount = base / ctx.salary_days * days
    return [("Оклад", amount, f"{_n(base)} ÷ {ctx.salary_days} × {_d(days)} дн.")]


def sales_percent(ctx, emp, c):
    m = _scope_metric(ctx, emp, c, "sales")
    if m is None:
        return []
    rate = emp.get("rate") if emp.get("rate") is not None and c.get("scope", "self") == "self" else c["rate"]
    where = {"team": "команды", "dept": "отдела"}.get(c.get("scope", "self"), "")
    title = f"% от продаж {where}".strip()
    if c.get("min_plan") and (m.pct or 0) < c["min_plan"]:
        return [(title, 0, f"план {_p(m.pct)} — нужно от {_p(c['min_plan'])}")]
    if c.get("rate_above") and m.plan:
        base = min(m.fact, m.plan)
        over = max(m.fact - m.plan, 0)
        return [(title, base * rate + over * c["rate_above"],
                 f"{_n(base)} × {_p(rate, 2)} + сверх плана {_n(over)} × {_p(c['rate_above'], 2)}")]
    return [(title, m.fact * rate, f"{_n(m.fact)} × {_p(rate, 2)}")]


def plan_bonus(ctx, emp, c):
    m = _scope_metric(ctx, emp, c, c["metric"])
    if m is None or not m.plan:
        return []
    pct = m.pct or 0
    title = f"Бонус: {m.title if m.title.isupper() or c['metric'].startswith('grp:') else m.title.lower()}"
    if c.get("scope", "self") != "self":
        title += " (команда)" if c["scope"] == "team" else " (отдел)"
    if c.get("mode", "steps") == "steps":
        got = 0
        for share, amount in sorted(c["steps"]):
            if pct >= share - 1e-9:
                got = amount
        nxt = next(((s, a) for s, a in sorted(c["steps"]) if pct < s - 1e-9), None)
        note = f"план {_p(pct)}" + (f" · до {_n(nxt[1])} нужно {_p(nxt[0])}" if nxt else "")
        return [(title, got, note)]
    lo, cap = c.get("min", 0), c.get("cap", 10)
    k = 0 if pct < lo else min(pct, cap)
    if pct < lo:
        return [(title, 0, f"план {_p(pct)} — бонус от {_p(lo)}")]
    return [(title, c["amount"] * k, f"{_n(c['amount'])} × {_p(k)}" + (f" (потолок {_p(cap)})" if pct > cap else ""))]


def fixed_bonus(ctx, emp, c):
    miss = ctx.missed_days(emp)
    cut = c.get("cut_per_miss", 0) * miss
    amount = max(c["amount"] - cut, 0)
    note = f"−{_n(cut)} за {miss} пропуск." if cut else "без пропусков"
    return [(c.get("name", "Бонус"), amount, note)]


def debt_penalty(ctx, emp, c):
    m = ctx.kpi.emp(emp["id"]).get("overdue")
    if m is None or not m.detail.get("debt"):
        return []
    share = c.get("share", 0.1)
    if m.fact > share:
        return [(f"Просрочка {m.detail['days']}+ дн.", -c["amount"], f"{_p(m.fact)} от продаж — больше {_p(share)}")]
    return []


def timesheet(ctx, emp, c):
    fines = ctx.fines(emp)
    if not fines:
        return []
    total = sum(a for a, _ in fines)
    return [("Штрафы по табелю", -total, ", ".join(n for _, n in fines[:4]) + ("…" if len(fines) > 4 else ""))]


def per_unit(ctx, emp, c):
    code = c["unit"]
    title, module = UNIT_TITLES.get(code, (code, ""))
    name = c.get("name") or (("Удержание: " if c.get("cut") else "") + title.split(",")[0].lower()).capitalize()
    if module and not ctx.cfg.has_module(module):
        return []
    fn = UNITS.get(code)
    if fn is None:
        return [(name, 0, "нет данных")]
    qty = fn(ctx, emp) or 0
    sign = -1 if c.get("cut") else 1
    if _num(c.get("rate")):
        return [(name, sign * qty * c["rate"], f"{_n(qty)} × {_p(c['rate'], 1)}")] if qty else []
    if not qty:
        return []
    unit_ = title.split(", ")[1] if ", " in title else "шт."
    return [(name, sign * qty * c["price"], f"{_d(round(qty, 1))} {unit_} × {_d(c['price'])} ₽")]


BUILTIN = {"salary": salary, "sales_percent": sales_percent, "plan_bonus": plan_bonus, "fixed_bonus": fixed_bonus,
           "debt_penalty": debt_penalty, "timesheet": timesheet, "per_unit": per_unit}


def _n(x):
    return f"{round(x):,}".replace(",", " ")


def _p(x, digits=0):
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def _d(x):
    return f"{x:g}".replace(".", ",")
