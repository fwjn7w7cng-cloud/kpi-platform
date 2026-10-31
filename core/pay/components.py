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
  debt_penalty   удержание за просрочку. days — порог (по умолчанию из анкеты), of — база доли: sales/month
                 (продажи месяца), 30d (за 30 дней), debt (весь долг); scope — self/team/dept. mode:
                   fixed   — amount, если доля просрочки больше share (по умолчанию — норма анкеты)
                   percent — rate от суммы просрочки (с min — только если доля больше; max — не больше суммы)
                   steps   — ступени [[доля, сумма]]: берётся сумма самой высокой превышенной ступени
  debt_bonus     бонус за низкую просрочку (те же days / of / scope). mode fixed — amount, если доля не больше share;
                 steps — [[доля, сумма]]: берётся самая щедрая ступень, в которую уложились
  timesheet      штрафы табеля: опоздания и прогулы (правила — timesheet в анкете)
  per_unit       сдельно: unit — единица (км, кг, точки, ошибки…), price — сумма за единицу или rate — доля
                 от суммы (для единиц в ₽); cut: true — это удержание (минус). Единицы дают модули (UNITS).

Добавлено 31.10 (Адам: «все вариации, чтобы ядро было очень гибким»):
  salary         mode: days (по отработанным дням, как было) | fixed (всегда полностью) | calendar (по календарным дням)
  sales_percent  base: sales | collect (от собранных денег) | margin (от наценки — нужна себестоимость в отчёте 1С);
                 groups: {код группы: ставка} — своя ставка по группам/брендам (остальное — по rate); focus_rate — по фокусным
  plan_bonus     mode fixed — amount, если план выполнен не меньше min (по умолчанию 100%); scope all — вся компания
  multiplier     множитель к уже посчитанному: metric (показатель плана или overdue — доля просрочки), steps [[порог, коэф.]];
                 applies: percent (проценты) | bonus (бонусы) | variable (всё, кроме оклада). Считается после остальных.
  new_clients    за каждого нового (или вернувшегося) клиента: не брал months месяцев (по умолчанию 3), купил на min ₽
  discount_penalty  средняя скидка выше norm: mode fixed — amount, percent — rate от продаж
  rank_bonus     место в рейтинге отдела по metric: amounts [за 1-е, за 2-е, за 3-е …]
  seniority      за стаж: amount за каждый полный год, не больше max
  limits         min_total — гарантированный минимум (доплата до него), max_variable — потолок всего, кроме оклада.
                 Считается последним.
"""

# Единицы для сдельных кубиков: код → (название, модуль). Считает их модуль: ``unit("km", fn)``,
# fn(payroll, emp) → количество за месяц. Модуль не подключён — кубик даёт ноль с пояснением.
UNIT_TITLES = {
    "km": ("Пробег, км", "logistics"), "kg": ("Вес, кг", "logistics"), "m3": ("Объём, м³", "logistics"),
    "points": ("Точки доставки", "logistics"), "trips": ("Рейсы", "logistics"),
    "ret_own": ("Возвраты по своей вине, ₽", "returns"), "ret_err": ("Ошибки в возвратах", "returns"),
    "nights": ("Ночёвки в рейсе, ноч.", "logistics"), "overtime": ("Переработка, мин.", "logistics"),
    "cash": ("Деньги с клиентов, клиентов", "logistics"), "pickup": ("Привоз от поставщика, ед.", "logistics"),
    "all_points": ("Отгрузки отдела, точек", "logistics"),
    "fuel_save": ("Экономия топлива, ₽", "fuel"), "fuel_over": ("Перерасход топлива, ₽", "fuel"),
    "stockout": ("Закончилось у своих поставщиков, позиций", "stock"), "stale": ("Залежалый товар, позиций", "stock"),
    "tasks_done": ("Задачи в срок, шт.", "tasks"), "tasks_late": ("Задачи с опозданием или «не смог», шт.", "tasks"),
}
UNITS = {}


def unit(code, fn):
    """Модуль регистрирует, как считать единицу."""
    UNITS[code] = fn

SCOPES = {"self", "team", "dept", "all"}
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
        if not _num(c.get("rate", 0)) or not 0 <= c.get("rate", 0) < 1:
            p.append("sales_percent.rate — доля от 0 до 1 (0,4% = 0.004)")
        if c.get("base", "sales") not in ("sales", "focus", "collect", "margin"):
            p.append("sales_percent.base — sales, focus, collect или margin")
        if c.get("rate_steps") is not None:
            st = c["rate_steps"]
            if not st or not all(isinstance(x, (list, tuple)) and len(x) == 2 and _num(x[0]) and _num(x[1]) for x in st):
                p.append("sales_percent.rate_steps — пары [доля плана, ставка]: «80% → 0,3%; 100% → 0,5%»")
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
        elif mode in ("linear", "fixed"):
            if not _num(c.get("amount")):
                p.append("plan_bonus.amount — сумма бонуса за 100% плана")
        else:
            p.append(f"plan_bonus.mode «{mode}» — steps, linear или fixed")
    elif t == "fixed_bonus":
        if not _num(c.get("amount")):
            p.append("fixed_bonus.amount — сумма бонуса")
    elif t in ("debt_penalty", "debt_bonus"):
        mode = c.get("mode", "fixed")
        allowed = ("fixed", "percent", "steps") if t == "debt_penalty" else ("fixed", "steps")
        if mode not in allowed:
            p.append(f"{t}.mode «{mode}» — {', '.join(allowed)}")
        elif mode == "fixed" and (not _num(c.get("amount")) or not _num(c.get("share", 0.1))):
            p.append(f"{t}: amount — сумма, share — доля просрочки («10%»)")
        elif mode == "percent" and not _num(c.get("rate")):
            p.append("debt_penalty: rate — доля от суммы просрочки («5%»)")
        elif mode == "steps":
            st = c.get("steps")
            if not st or not all(isinstance(s, (list, tuple)) and len(s) == 2 and _num(s[0]) and _num(s[1]) for s in st):
                p.append(f"{t}.steps — пары [доля просрочки, сумма]: «5% → 1 000; 10% → 3 000»")
        if "days" in c and (not isinstance(c["days"], int) or c["days"] <= 0):
            p.append(f"{t}.days — число дней просрочки, например 60")
        if c.get("of", "sales") not in ("sales", "month", "30d", "debt"):
            p.append(f"{t}.of — sales, 30d или debt")
    elif t == "timesheet":
        pass
    elif t == "multiplier":
        if c.get("metric") != "overdue" and not _metric_ok(c.get("metric")):
            p.append(f"multiplier.metric «{c.get('metric')}» — показатель плана или overdue")
        st = c.get("steps")
        if not st or not all(isinstance(x, (list, tuple)) and len(x) == 2 and _num(x[0]) and _num(x[1]) for x in st):
            p.append("multiplier.steps — пары [порог, коэффициент]: «90% → 0,8; 110% → 1,2»")
        if c.get("applies", "bonus") not in ("percent", "bonus", "variable"):
            p.append("multiplier.applies — percent, bonus или variable")
    elif t == "new_clients":
        if not _num(c.get("amount")):
            p.append("new_clients.amount — сумма за клиента")
    elif t == "discount_penalty":
        if not _num(c.get("norm")):
            p.append("discount_penalty.norm — допустимая средняя скидка («5%»)")
        if c.get("mode", "fixed") == "fixed" and not _num(c.get("amount")):
            p.append("discount_penalty.amount — сумма удержания")
        if c.get("mode") == "percent" and not _num(c.get("rate")):
            p.append("discount_penalty.rate — доля от продаж")
    elif t == "rank_bonus":
        if not isinstance(c.get("amounts"), list) or not all(_num(x) for x in c["amounts"]):
            p.append("rank_bonus.amounts — суммы за места: [5000, 3000, 1000]")
    elif t == "seniority":
        if not _num(c.get("amount")):
            p.append("seniority.amount — сумма за год стажа")
    elif t == "limits":
        if not _num(c.get("min_total", 0)) or not _num(c.get("max_variable", 0)):
            p.append("limits: min_total и max_variable — суммы")
    elif t == "per_unit":
        if c.get("unit") not in UNIT_TITLES:
            p.append(f"per_unit.unit «{c.get('unit')}» — одна из: {', '.join(UNIT_TITLES)}")
        if not _num(c.get("price")) and not _num(c.get("rate")):
            p.append("per_unit: price — сумма за единицу («4») или rate — доля («3%»)")
    if c.get("scope", "self") not in SCOPES:
        p.append(f"scope «{c.get('scope')}» — self, team, dept или all")
    if t == "salary" and c.get("mode", "days") not in ("days", "fixed", "calendar"):
        p.append("salary.mode — days, fixed или calendar")
    return p


# ---------- расчёт ----------

def _scope_metric(ctx, emp, c, code):
    scope = c.get("scope", "self")
    if scope == "team":
        return ctx.kpi.scope("team", emp["team"]).get(code)
    if scope == "dept":
        return ctx.kpi.scope("dept", emp["dept"]).get(code)
    if scope == "all":
        return ctx.kpi.scope("all").get(code)
    return ctx.kpi.emp(emp["id"]).get(code)


def salary(ctx, emp, c):
    base = emp.get("salary")
    if base is None:
        base = c.get("amount", ctx.default_salary)
    mode = c.get("mode", "days")
    if mode == "fixed":
        return [("Оклад", base, "фиксированный, за месяц")]
    if mode == "calendar":
        import calendar as _cal
        cal = ctx.cal
        total = _cal.monthrange(cal.start.year, cal.start.month)[1]
        hired = emp.get("hired") or "0000"
        fired = emp.get("fired") or "9999"
        last = cal.end if cal.closed else min(cal.today, cal.end)
        n = sum(1 for d in range(1, last.day + 1) if hired <= f"{cal.start:%Y-%m}-{d:02d}" <= fired)
        n -= ctx.missed_days(emp)
        n = max(n, 0)
        return [("Оклад", base / total * n, f"{_n(base)} ÷ {total} календ. дн. × {n} дн.")]
    days = ctx.worked_days(emp)
    amount = base / ctx.salary_days * days
    return [("Оклад", amount, f"{_n(base)} ÷ {ctx.salary_days} × {_d(days)} дн.")]


PCT_TITLE = {"sales": "продаж", "focus": "фокусных", "collect": "собранных денег"}


def sales_percent(ctx, emp, c):
    """Процент с показателя base (sales | focus | collect | margin). Связь с планом этого же показателя:
    min_plan — платить только с порога; rate_steps [[доля плана, ставка]] — ставка по ступеням; rate_above — сверх плана."""
    base = c.get("base", "sales")
    if base == "margin":
        return [("% от наценки", 0, "нужна себестоимость в отчёте 1С — подключим вместе с клиентом")]
    if c.get("groups") or c.get("focus_rate") is not None:
        return _group_percent(ctx, emp, c)
    m = _scope_metric(ctx, emp, c, base)
    if m is None:
        return []
    rate = c.get("rate", 0)
    if base == "sales" and emp.get("rate") is not None and c.get("scope", "self") == "self":
        rate = emp["rate"]
    where = {"team": " команды", "dept": " отдела", "all": " компании"}.get(c.get("scope", "self"), "")
    title = f"% от {PCT_TITLE.get(base, base)}{where}"
    pct = m.pct or 0
    if c.get("min_plan") and pct < c["min_plan"] - 1e-9:
        return [(title, 0, f"план {_p(pct)} — платится от {_p(c['min_plan'])}")]
    if c.get("rate_steps"):
        got = None
        for share, r in sorted(c["rate_steps"]):
            if pct >= share - 1e-9:
                got = r
        if got is None:
            return [(title, 0, f"план {_p(pct)} — ставка от {_p(sorted(c['rate_steps'])[0][0])}")]
        return [(title, m.fact * got, f"{_n(m.fact)} × {_p(got, 2)} (план {_p(pct)})")]
    if c.get("rate_above") and m.plan:
        b = min(m.fact, m.plan)
        over = max(m.fact - m.plan, 0)
        return [(title, b * rate + over * c["rate_above"],
                 f"{_n(b)} × {_p(rate, 2)} + сверх плана {_n(over)} × {_p(c['rate_above'], 2)}")]
    return [(title, m.fact * rate, f"{_n(m.fact)} × {_p(rate, 2)}")]


def _group_percent(ctx, emp, c):
    """Своя ставка по группам товаров и фокусным; остальное — по rate."""
    k = ctx.kpi
    if c.get("scope", "self") == "self":
        groups = k.raw(emp["id"])["groups"]
    else:
        sc = c["scope"]
        ids = k.members("team" if sc == "team" else "dept" if sc == "dept" else "all",
                        emp["team"] if sc == "team" else emp["dept"] if sc == "dept" else None)
        groups = {}
        for i in ids:
            for g, v in k.raw(i)["groups"].items():
                groups[g] = groups.get(g, 0) + v
    rates = {str(g): float(r) for g, r in (c.get("groups") or {}).items()}
    base_rate = c.get("rate", 0)
    focus_rate = c.get("focus_rate")
    out, rest = [], 0.0
    by_rate = {}
    for g, v in groups.items():
        if g in rates:
            by_rate.setdefault(("grp", g), [0.0, rates[g]])[0] += v
        elif focus_rate is not None and g in k.focus:
            by_rate.setdefault(("focus", ""), [0.0, focus_rate])[0] += v
        else:
            rest += v
    for (kind, g), (v, r) in sorted(by_rate.items()):
        name = "фокусные группы" if kind == "focus" else k.group_name(g)
        out.append((f"% от продаж: {name}", v * r, f"{_n(v)} × {_p(r, 2)}"))
    if base_rate and rest:
        out.append(("% от продаж: остальное", rest * base_rate, f"{_n(rest)} × {_p(base_rate, 2)}"))
    return out


def plan_bonus(ctx, emp, c):
    m = _scope_metric(ctx, emp, c, c["metric"])
    if m is None or not m.plan:
        return []
    pct = m.pct or 0
    title = f"Бонус: {m.title if m.title.isupper() or c['metric'].startswith('grp:') else m.title.lower()}"
    if c.get("scope", "self") != "self":
        title += {"team": " (команда)", "dept": " (отдел)", "all": " (компания)"}[c["scope"]]
    if c.get("mode") == "fixed":
        need = c.get("min", 1.0)
        return [(title, c["amount"] if pct >= need - 1e-9 else 0, f"план {_p(pct)} · фикс при {_p(need)}")]
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


def _debt(ctx, emp, c):
    """(просрочка, база, доля, подпись) для кубиков дебиторки по scope/days/of."""
    k = ctx.kpi
    scope = c.get("scope", "self")
    ids = [emp["id"]] if scope == "self" else k.members("team" if scope == "team" else "dept",
                                                          emp["team"] if scope == "team" else emp["dept"])
    days = c.get("days") or k.debt_days
    over, base, share = k.debt_share(ids, days, c.get("of"))
    of = {"sales": "month"}.get(c.get("of"), c.get("of")) or k.debt_base
    where = {"team": " команды", "dept": " отдела"}.get(scope, "")
    base_t = {"month": "продаж", "30d": "продаж 30 дн.", "debt": "долга"}[of]
    return over, base, share, f"{days}+{where}: {_n(over)} · {_p(share, 1)} от {base_t}", days


def debt_penalty(ctx, emp, c):
    over, base, share, note, days = _debt(ctx, emp, c)
    if not over:
        return []
    title = c.get("name") or f"Просрочка {days}+ дн."
    mode = c.get("mode", "fixed")
    if mode == "percent":
        if c.get("min") and share <= c["min"]:
            return []
        amount = over * c["rate"]
        if c.get("max"):
            amount = min(amount, c["max"])
        return [(title, -amount, f"{note} · ×{_p(c['rate'], 1)}" + (f", макс. {_n(c['max'])}" if c.get("max") else ""))]
    if mode == "steps":
        got = 0
        for s, a in sorted(c["steps"]):
            if share > s:
                got = a
        return [(title, -got, note)] if got else []
    lim = c.get("share", ctx.kpi.debt_norm)
    if share > lim:
        return [(title, -c["amount"], f"{note} > {_p(lim)}")]
    return []


def debt_bonus(ctx, emp, c):
    over, base, share, note, days = _debt(ctx, emp, c)
    if base <= 0:
        return []
    title = c.get("name") or f"Бонус: низкая просрочка {days}+"
    if c.get("mode", "fixed") == "steps":
        for s, a in sorted(c["steps"]):
            if share <= s:
                return [(title, a, f"{note} ≤ {_p(s)}")]
        return [(title, 0, f"{note} · бонус до {_p(max(s for s, _ in c['steps']))}")]
    lim = c.get("share", ctx.kpi.debt_norm / 2)
    if share <= lim:
        return [(title, c["amount"], f"{note} ≤ {_p(lim)}")]
    return [(title, 0, f"{note} · бонус до {_p(lim)}")]


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


def new_clients(ctx, emp, c):
    """Новые или вернувшиеся клиенты: купили в этом месяце на min ₽, а предыдущие months месяцев не брали."""
    from datetime import date as _date
    k = ctx.kpi
    months = int(c.get("months", 3))
    now = {cl for cl, v in k.by_client.get(emp["id"], {}).items() if v >= c.get("min", 1)}
    if not now:
        return [(c.get("name", "Новые клиенты"), 0, "новых клиентов нет")]
    start = _date.fromisoformat(str(k.start))
    y, m = start.year, start.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    since = _date(y, m, 1).isoformat()
    cache = ctx.__dict__.setdefault("_old_clients", {})
    if since not in cache:                       # клиенты, которые брали у фирмы за прошлые months месяцев
        cache[since] = {r[0] for r in ctx.db.c.execute(
            "SELECT DISTINCT client FROM sales WHERE day >= ? AND day < ? AND amount > 0", (since, start.isoformat()))}
    new = [cl for cl in now if cl not in cache[since]]
    return [(c.get("name", "Новые клиенты"), len(new) * c["amount"],
             f"{len(new)} × {_n(c['amount'])} (не брали {months} мес.)" if new else f"новых нет (не брали {months} мес.)")]


def discount_penalty(ctx, emp, c):
    k = ctx.kpi
    s = d = 0.0
    for r in ctx.db.c.execute("SELECT day, emp_key, amount, discount FROM sales WHERE day BETWEEN ? AND ? AND amount > 0",
                              (k.start, k.end)):
        if k.who(r[1], r[0]) == emp["id"]:
            s += r[2]
            d += r[2] * (r[3] or 0)
    if s <= 0:
        return []
    avg = d / s
    title = c.get("name", "Лишние скидки")
    if avg <= c["norm"]:
        return [(title, 0, f"средняя скидка {_p(avg, 1)} ≤ {_p(c['norm'], 1)}")]
    amount = s * c["rate"] if c.get("mode") == "percent" else c["amount"]
    return [(title, -amount, f"средняя скидка {_p(avg, 1)} > {_p(c['norm'], 1)}")]


def rank_bonus(ctx, emp, c):
    k = ctx.kpi
    ids = k.members("dept", emp["dept"])
    if emp["id"] not in ids:
        return []
    order = [i for i, _ in k.rating(ids, c.get("metric", "sales"))]
    place = order.index(emp["id"]) + 1
    amounts = c["amounts"]
    got = amounts[place - 1] if place <= len(amounts) else 0
    return [(c.get("name", "За место в рейтинге"), got, f"{place}-е место из {len(order)}")]


def seniority(ctx, emp, c):
    from datetime import date as _date
    if not emp.get("hired"):
        return []
    h = _date.fromisoformat(emp["hired"])
    on = ctx.cal.end
    years = on.year - h.year - ((on.month, on.day) < (h.month, h.day))
    if years <= 0:
        return []
    amount = years * c["amount"]
    if c.get("max"):
        amount = min(amount, c["max"])
    return [(c.get("name", "За стаж"), amount, f"{years} г. × {_n(c['amount'])}")]


# Кубики, которые считаются после остальных: множитель и пределы. Строка расчёта помнит свой кубик (kind).
PERCENT_KINDS = {"sales_percent"}
BONUS_KINDS = {"plan_bonus", "fixed_bonus", "debt_bonus", "new_clients", "rank_bonus", "per_unit", "seniority"}


def multiplier(ctx, emp, c, rows):
    """rows — [(название, сумма, пояснение, кубик)] уже посчитанные. → строка «множитель» (разница)."""
    applies = c.get("applies", "bonus")
    kinds = PERCENT_KINDS if applies == "percent" else BONUS_KINDS if applies == "bonus" else None
    base = sum(r[1] for r in rows if r[1] > 0 and (r[3] in kinds if kinds else r[3] not in ("salary",)))
    if not base:
        return []
    if c.get("metric") == "overdue":
        over, b, share, note, days = _debt(ctx, emp, c)
        coef = 1.0
        for s_, k_ in sorted(c["steps"]):
            if share > s_:
                coef = k_
        why = f"просрочка {_p(share, 1)}"
    else:
        m = _scope_metric(ctx, emp, c, c["metric"])
        if m is None or not m.plan:
            return []
        pct = m.pct or 0
        coef = None
        for s_, k_ in sorted(c["steps"]):
            if pct >= s_ - 1e-9:
                coef = k_
        if coef is None:
            coef = sorted(c["steps"])[0][1] if c.get("below_first", "first") == "first" else 0
        why = f"{m.title.lower()} {_p(pct)}"
    what = {"percent": "к процентам", "bonus": "к бонусам", "variable": "к премии"}[applies]
    if abs(coef - 1) < 1e-9:
        return [(f"Множитель {what}", 0, f"{why} → ×1")]
    return [(f"Множитель {what}", base * (coef - 1), f"{why} → ×{_d(coef)} от {_n(base)}")]


def limits(ctx, emp, c, rows):
    out = []
    var = sum(r[1] for r in rows if r[3] not in ("salary",) and r[1] > 0)
    cap = c.get("max_variable")
    if cap and var > cap:
        out.append(("Потолок премии", cap - var, f"сверх {_n(cap)} не платится"))
    total = sum(r[1] for r in rows) + sum(r[1] for r in out)
    mn = c.get("min_total")
    if mn and total < mn:
        out.append(("Доплата до гарантированного минимума", mn - total, f"минимум {_n(mn)}"))
    return out


POST = {"multiplier": multiplier, "limits": limits}


BUILTIN = {"new_clients": new_clients, "discount_penalty": discount_penalty, "rank_bonus": rank_bonus,
           "seniority": seniority, "salary": salary, "sales_percent": sales_percent, "plan_bonus": plan_bonus, "fixed_bonus": fixed_bonus,
           "debt_penalty": debt_penalty, "debt_bonus": debt_bonus, "timesheet": timesheet, "per_unit": per_unit}


def _n(x):
    return f"{round(x):,}".replace(",", " ")


def _p(x, digits=0):
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def _d(x):
    return f"{x:g}".replace(".", ",")
