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

Добавлено 31.10, вторая волна (Адам: «все расчёты, которые только есть», цифры по умолчанию — круглые):
  plan_bonus     grow — сверх 100 % бонус растёт (× выполнение, до cap); mode below — минус amount, если план ниже min;
                 mode each_group — фикс за каждую фокусную группу с планом не ниже min (by_exec — × выполнение общего плана)
  sales_percent  debt_rates [[доля просрочки, ставка]] — ставка зависит от просрочки (берётся первая ступень, куда уложились);
                 groups: ставка 0 — группа не идёт в процент
  debt_penalty   mode buckets — rate61 от просрочки 61–90 и rate91 от 91+; when: last (на последнюю дату) | avg (в среднем
                 по всем срезам месяца)
  growth_bonus   рост к прошлому году (vs: ly) или к прошлому месяцу (vs: pm): steps [[рост, сумма]], rate_over — % от продаж сверх
                 последней ступени
  kpi_card       KPI-карта: items [[показатель, вес]], amount × Σ вес × выполнение (выполнение до cap), min — порог карты
  orders         заказы (клиент × день): mode each — price за заказ; mode avg — amount, если средний чек не меньше norm
  new_clients    kind: new (никогда не брал) | back (вернувшиеся «спящие») | both
  team_days      супервайзеру: amount, если команда отработала норму дней (минус allow выходных на человека)
  old_debt       rate от денег, собранных с клиентов, у которых в начале месяца была просрочка days+ (по умолчанию 91)
  period_bonus   за квартал / год (period: quarter | year): steps по сумме плана и факта всех месяцев; платится в последнем месяце
  awards         за значки доски почёта: amount за каждый, не больше max
  pool           котёл отдела: price × отгрузки отдела (или rate × продажи отдела), делится по коэффициентам должностей
                 (coefs {часть названия должности: коэф.}) и отработанным дням
  fixed_bonus    cut_unit + cut_price — минус за каждую ошибку (единица сдельной оплаты); hand — «на руки», не в ведомость
  salary         mode shift — за смену (выходы × amount); advance — оклад как аванс (минус из начисленного)
  fuel_comp      ГСМ в общую зарплату: mode fixed (amount) | day (price × дни) | km (price × км) | norm (км × l100 / 100 ×
                 fuel_price); км — из рейсов + вписанные руками (отметка «км» в табеле, торговый — сам за день)
  limits         name — своё название доплаты («Доплата до оклада»)
  timesheet      свои правила должности: late [[минуты, сумма]], truancy — прогул, fines [[название, сумма]] — свои штрафы,
                 extra_coef — выход в выходной (1 или 2 дня), newbie_free — новичкам без штрафов
"""

# Единицы для сдельных кубиков: код → (название, модуль). Считает их модуль: ``unit("km", fn)``,
# fn(payroll, emp) → количество за месяц. Модуль не подключён — кубик даёт ноль с пояснением.
UNIT_TITLES = {
    "long_day": ("Длинные дни, дн.", "logistics"), "quiz_ok": ("Сданные тесты, шт.", "quiz"), "quiz_fail": ("Несданные тесты, шт.", "quiz"),
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
        elif mode in ("linear", "fixed", "below", "each_group"):
            if not _num(c.get("amount")):
                p.append("plan_bonus.amount — сумма бонуса за 100% плана")
        else:
            p.append(f"plan_bonus.mode «{mode}» — steps, linear, fixed, below или each_group")
    elif t == "fixed_bonus":
        if not _num(c.get("amount")):
            p.append("fixed_bonus.amount — сумма бонуса")
    elif t in ("debt_penalty", "debt_bonus"):
        mode = c.get("mode", "fixed")
        allowed = ("fixed", "percent", "steps", "buckets") if t == "debt_penalty" else ("fixed", "steps")
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
        for f in c.get("fines") or []:
            if not (isinstance(f, (list, tuple)) and len(f) == 2 and isinstance(f[0], str) and _num(f[1])):
                p.append("timesheet.fines — пары [название, сумма]: [[«Нет на собрании», 500]]")
                break
    elif t == "growth_bonus":
        if c.get("vs", "ly") not in ("ly", "pm"):
            p.append("growth_bonus.vs — ly (прошлый год) или pm (прошлый месяц)")
        st = c.get("steps")
        if not st or not all(isinstance(x, (list, tuple)) and len(x) == 2 and _num(x[0]) and _num(x[1]) for x in st):
            p.append("growth_bonus.steps — пары [рост, сумма]: «10% → 5 000; 20% → 10 000»")
    elif t == "kpi_card":
        it = c.get("items")
        if not it or not all(isinstance(x, (list, tuple)) and len(x) == 2 and _metric_ok(x[0]) and _num(x[1]) for x in it):
            p.append("kpi_card.items — пары [показатель, вес]: [[sales, 0.5], [akb, 0.3]]")
        if not _num(c.get("amount")):
            p.append("kpi_card.amount — премия за 100% карты")
    elif t == "orders":
        if c.get("mode", "each") == "each" and not _num(c.get("price")):
            p.append("orders.price — сумма за заказ")
        if c.get("mode") == "avg" and not (_num(c.get("amount")) and _num(c.get("norm"))):
            p.append("orders: amount — сумма, norm — средний чек от")
    elif t == "period_bonus":
        if c.get("period", "quarter") not in ("quarter", "year"):
            p.append("period_bonus.period — quarter или year")
    elif t in ("team_days", "old_debt", "awards", "pool"):
        if t == "old_debt" and not _num(c.get("rate")):
            p.append("old_debt.rate — доля от собранных денег")
        if t in ("team_days", "awards") and not _num(c.get("amount")):
            p.append(f"{t}.amount — сумма")
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
    if t == "salary" and c.get("mode", "days") not in ("days", "fixed", "calendar", "shift", "advance"):
        p.append("salary.mode — days, fixed, calendar, shift или advance")
    if t == "fuel_comp" and c.get("mode", "fixed") not in ("fixed", "day", "km", "norm"):
        p.append("fuel_comp.mode — fixed, day, km или norm")
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


def own_salary(ctx, emp, c):
    """(оклад, пояснение): own — у каждого свой (из карточки; не вписан — сумма должности), иначе — одна сумма на всех."""
    amount = c.get("amount", ctx.default_salary)
    if c.get("own", True) and emp.get("salary") is not None:
        return emp["salary"], " (свой)" if "own" in c else ""
    return amount, " (своего нет — как у должности)" if c.get("own") else ""


def salary(ctx, emp, c):
    base, mark = own_salary(ctx, emp, c)
    mode = c.get("mode", "days")
    if mode == "fixed":
        return [("Оклад", base, "фиксированный, за месяц" + mark)]
    if mode == "shift":                              # за смену: выходы по табелю × ставка смены
        days = ctx.worked_days(emp)
        rate = c.get("amount", 0)
        return [("За смены", rate * days, f"{_d(days)} смен × {_n(rate)}")]
    if mode == "advance":                            # оклад как аванс: уже выплачен в месяце — вычитается из начисленного
        return [("Аванс (выплачен в месяце)", -base, "вычитается из сдельной части" + mark)]
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
        return [("Оклад", base / total * n, f"{_n(base)} ÷ {total} календ. дн. × {n} дн." + mark)]
    days = ctx.worked_days(emp)
    amount = base / ctx.salary_days * days
    return [("Оклад", amount, f"{_n(base)} ÷ {ctx.salary_days} × {_d(days)} дн." + mark)]


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
    if c.get("debt_rates"):
        _o, _b, share, dnote, _d_ = _debt(ctx, emp, c)
        got = 0
        for s_, r_ in sorted(c["debt_rates"]):
            if share <= s_ + 1e-9:
                got = r_
                break
        return [(title, m.fact * got, f"{_n(m.fact)} × {_p(got, 2)} — просрочка {_p(share, 1)}")]
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
        if not r:
            out.append((f"% от продаж: {name}", 0, f"{_n(v)} — не идёт в процент"))
            continue
        out.append((f"% от продаж: {name}", v * r, f"{_n(v)} × {_p(r, 2)}"))
    if base_rate and rest:
        out.append(("% от продаж: остальное", rest * base_rate, f"{_n(rest)} × {_p(base_rate, 2)}"))
    return out


def plan_bonus(ctx, emp, c):
    mode = c.get("mode", "steps")
    if mode == "each_group":
        return _each_group(ctx, emp, c)
    m = _scope_metric(ctx, emp, c, c["metric"])
    if m is None or not m.plan:
        return []
    pct = m.pct or 0
    title = f"Бонус: {m.title if m.title.isupper() or c['metric'].startswith('grp:') else m.title.lower()}"
    if c.get("scope", "self") != "self":
        title += {"team": " (команда)", "dept": " (отдел)", "all": " (компания)"}[c["scope"]]
    grow = bool(c.get("grow")) and pct > 1

    def over(amount, note):
        if not (grow and amount):
            return amount, note
        k = min(pct, c.get("cap") or 10)
        return amount * k, note + f" · сверх плана ×{_p(k)}"

    if mode == "below":
        need = c.get("min", 0.8)
        if pct < need - 1e-9:
            return [(f"План ниже {_p(need)}", -c["amount"], f"план {_p(pct)}")]
        return []
    if mode == "fixed":
        need = c.get("min", 1.0)
        a, note = over(c["amount"] if pct >= need - 1e-9 else 0, f"план {_p(pct)} · фикс при {_p(need)}")
        return [(title, a, note)]
    if mode == "steps":
        got = 0
        for share, amount in sorted(c["steps"]):
            if pct >= share - 1e-9:
                got = amount
        nxt = next(((s, a) for s, a in sorted(c["steps"]) if pct < s - 1e-9), None)
        note = f"план {_p(pct)}" + (f" · до {_n(nxt[1])} нужно {_p(nxt[0])}" if nxt else "")
        got, note = over(got, note)
        return [(title, got, note)]
    lo, cap = c.get("min", 0), c.get("cap", 10)
    if not c.get("grow", True):
        cap = min(cap, 1.0)                       # «сверх 100 % не растёт»
    k = 0 if pct < lo else min(pct, cap)
    if pct < lo:
        return [(title, 0, f"план {_p(pct)} — бонус от {_p(lo)}")]
    return [(title, c["amount"] * k, f"{_n(c['amount'])} × {_p(k)}" + (f" (потолок {_p(cap)})" if pct > cap else ""))]


def _each_group(ctx, emp, c):
    """Фикс за каждую фокусную группу, где план выполнен не меньше min; by_exec — × выполнение общего плана."""
    k = ctx.kpi
    need = c.get("min", 1.0)
    done, total = 0, 0
    for g in sorted(k.focus):
        m = _scope_metric(ctx, emp, c, f"grp:{g}")
        if m is None or not m.plan:
            continue
        total += 1
        if (m.pct or 0) >= need - 1e-9:
            done += 1
    if not total:
        return []
    amount = done * c["amount"]
    note = f"{done} из {total} групп × {_n(c['amount'])}"
    if c.get("by_exec"):
        s_ = _scope_metric(ctx, emp, c, "sales")
        ex = min(s_.pct or 0, c.get("cap") or 10) if s_ and s_.plan else 0
        amount *= ex
        note += f" × план {_p(ex)}"
    return [("Фокусные группы", amount, note)]


HAND = " · на руки"          # строка «на руки»: в фонде есть, в ведомость кассы не идёт (бонус от начальника)


def fixed_bonus(ctx, emp, c):
    miss = ctx.missed_days(emp)
    cut = c.get("cut_per_miss", 0) * miss
    notes = [f"−{_n(cut)} за {miss} пропуск."] if cut else []
    if c.get("cut_unit") and c.get("cut_price"):
        fn = UNITS.get(c["cut_unit"])
        n_err = (fn(ctx, emp) or 0) if fn else 0
        if n_err:
            cut += n_err * c["cut_price"]
            notes.append(f"−{_n(n_err * c['cut_price'])} за {_d(n_err)} ошиб.")
    amount = max(c["amount"] - cut, 0)
    name = c.get("name", "Бонус") + (HAND if c.get("hand") else "")
    plain = not c.get("cut_per_miss") and not c.get("cut_unit")
    return [(name, amount, ", ".join(notes) or ("" if plain else "без пропусков и ошибок"))]


def _ids(ctx, emp, c):
    k = ctx.kpi
    scope = c.get("scope", "self")
    if scope == "self":
        return [emp["id"]]
    if scope == "all":
        return k.members("all")
    return k.members("team" if scope == "team" else "dept", emp["team"] if scope == "team" else emp["dept"])


def _debt_snapshots(ctx, ids, days):
    """[(дата, просрочка)] по всем срезам дебиторки месяца — для «в среднем за месяц»."""
    k = ctx.kpi
    ids = set(ids)
    by = {}
    for r in ctx.db.c.execute('SELECT "on", emp_key, total, d0_30, d31_60, d61_90, d91 FROM debts WHERE "on" BETWEEN ? AND ?',
                              (str(k.start), str(k.end))):
        if k.who(r[1], r[0]) in ids:
            row = {"total": r[2], "d0_30": r[3], "d31_60": r[4], "d61_90": r[5], "d91": r[6]}
            by[r[0]] = by.get(r[0], 0) + k.overdue_amount(row, days)
    return sorted(by.items())


def _debt(ctx, emp, c):
    """(просрочка, база, доля, подпись) для кубиков дебиторки по scope/days/of; when avg — среднее по срезам месяца."""
    k = ctx.kpi
    scope = c.get("scope", "self")
    ids = _ids(ctx, emp, c)
    days = c.get("days") or k.debt_days
    over, base, share = k.debt_share(ids, days, c.get("of"))
    if c.get("when") == "avg":
        snaps = _debt_snapshots(ctx, ids, days)
        if snaps:
            over = sum(v for _, v in snaps) / len(snaps)
            share = over / base if base > 0 else (1.0 if over else 0.0)
    of = {"sales": "month"}.get(c.get("of"), c.get("of")) or k.debt_base
    where = {"team": " команды", "dept": " отдела", "all": " компании"}.get(scope, "")
    base_t = {"month": "продаж", "30d": "продаж 30 дн.", "debt": "долга"}[of]
    avg = " в среднем за месяц" if c.get("when") == "avg" else ""
    return over, base, share, f"{days}+{where}{avg}: {_n(over)} · {_p(share, 1)} от {base_t}", days


def debt_penalty(ctx, emp, c):
    if c.get("mode") == "buckets":
        k = ctx.kpi
        rows = [r for i in _ids(ctx, emp, c) for r in k.debts.get(i, [])]
        d61 = sum(r.get("d61_90", 0) or 0 for r in rows)
        d91 = sum(r.get("d91", 0) or 0 for r in rows)
        out = []
        if d61 and c.get("rate61"):
            out.append((f"Просрочка 61–90 дн. (−{_p(c['rate61'])})", -d61 * c["rate61"], _n(d61)))
        if d91 and c.get("rate91"):
            out.append((f"Просрочка 91+ дн. (−{_p(c['rate91'])})", -d91 * c["rate91"], _n(d91)))
        return out
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
    fines = ctx.fines(emp, c)
    out = []
    if fines:
        total = sum(a for a, _ in fines)
        out.append(("Штрафы по табелю", -total, ", ".join(n for _, n in fines[:4]) + ("…" if len(fines) > 4 else "")))
    coef = c.get("extra_coef", 1)
    if coef and coef > 1:
        extra = sum(1 for r in ctx._ts.get(emp["id"], []) if r["kind"] == "extra")
        if extra:
            base = emp.get("salary") or ctx.default_salary or 0
            day = base / ctx.salary_days if ctx.salary_days else 0
            out.append(("Работа в выходной", extra * day * (coef - 1), f"{extra} дн. × ещё {_d(coef - 1)} дня"))
    return out


def per_unit(ctx, emp, c):
    code = c["unit"]
    title, module = UNIT_TITLES.get(code, (code, ""))
    name = c.get("name") or (("Удержание: " if c.get("cut") else "") + title.split(",")[0].lower()).capitalize()
    if module and not ctx.cfg.has_module(module):
        return []
    fn = UNITS.get(code)
    if fn is None:
        return [(name, 0, "нет данных")]
    try:
        qty = fn(ctx, emp, c) or 0
    except TypeError:
        qty = fn(ctx, emp) or 0
    sign = -1 if c.get("cut") else 1
    if c.get("day_rate"):                         # ночёвка и т. п. — по дневному окладу
        base = emp.get("salary") or ctx.default_salary or 0
        price = base / ctx.salary_days if ctx.salary_days else 0
        return [(name, sign * qty * price, f"{_d(qty)} × {_n(price)} (оклад ÷ {ctx.salary_days})")] if qty else []
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
    kind = c.get("kind", "both")
    if kind != "both":
        ever = cache.setdefault(("ever", since), {r[0] for r in ctx.db.c.execute(
            "SELECT DISTINCT client FROM sales WHERE day < ? AND amount > 0", (since,))})
        new = [cl for cl in new if (cl in ever) == (kind == "back")]
    what = {"new": "новых", "back": "вернувшихся", "both": "новых и вернувшихся"}[kind]
    title = c.get("name") or {"new": "Новые клиенты", "back": "Вернувшиеся клиенты", "both": "Новые клиенты"}[kind]
    return [(title, len(new) * c["amount"],
             f"{len(new)} × {_n(c['amount'])} (не брали {months} мес.)" if new else f"{what} нет (не брали {months} мес.)")]


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


def _sales_of(ctx, emp, start, end):
    """Продажи сотрудника за любой период (по ключам источника)."""
    k = ctx.kpi
    tot = 0.0
    for r in ctx.db.c.execute("SELECT day, emp_key, amount FROM sales WHERE day BETWEEN ? AND ?", (str(start), str(end))):
        if k.who(r[1], r[0]) == emp["id"]:
            tot += r[2]
    return tot


def growth_bonus(ctx, emp, c):
    """Рост продаж к тому же месяцу прошлого года (vs ly) или к прошлому месяцу (vs pm)."""
    import calendar as _cal
    from datetime import date as _date
    k = ctx.kpi
    st = _date.fromisoformat(str(k.start))
    if c.get("vs", "ly") == "ly":
        a = _date(st.year - 1, st.month, 1)
        what = "к прошлому году"
    else:
        a = _date(st.year - (st.month == 1), st.month - 1 or 12, 1)
        what = "к прошлому месяцу"
    b = _date(a.year, a.month, _cal.monthrange(a.year, a.month)[1])
    if not k.cal.closed:                                   # месяц идёт — сравниваем те же дни
        b = min(b, _date(a.year, a.month, min(k.cal.today.day, b.day)))
    was = _sales_of(ctx, emp, a, b)
    now = k.sales.get(emp["id"], 0.0)
    title = c.get("name") or f"Рост {what}"
    if was <= 0:
        return [(title, 0, f"нет продаж за {a:%m.%Y} для сравнения")]
    g = now / was - 1
    got, top = 0, None
    for share, amount in sorted(c["steps"]):
        if g >= share - 1e-9:
            got, top = amount, share
    note = f"{_n(now)} против {_n(was)} → {'+' if g >= 0 else ''}{_p(g)}"
    if top is not None and c.get("rate_over"):
        extra_ = max(now - was * (1 + top), 0) * c["rate_over"]
        got += extra_
        note += f" · сверх +{_p(top)}: {_n(extra_)}"
    return [(title, got, note)]


def kpi_card(ctx, emp, c):
    """KPI-карта: премия × Σ вес × выполнение; выполнение показателя — до cap; карта ниже min — ноль."""
    total, parts, w_all = 0.0, [], 0.0
    cap = c.get("cap", 1.2)
    for metric, w in c["items"]:
        m = _scope_metric(ctx, emp, c, metric)
        w_all += w
        if m is None or not m.plan:
            parts.append(f"{metric}: нет плана")
            continue
        pct = min(m.pct or 0, cap)
        total += w * pct
        parts.append(f"{m.title.lower()} {_p(pct)}×{_d(w)}")
    k = total / w_all if w_all else 0
    title = c.get("name") or "KPI-карта"
    if k < c.get("min", 0) - 1e-9:
        return [(title, 0, f"карта {_p(k)} — платится от {_p(c['min'])}")]
    return [(title, c["amount"] * k, f"{_n(c['amount'])} × {_p(k)} ({'; '.join(parts)})")]


def orders(ctx, emp, c):
    """Заказы = клиент × день с продажей. each — за каждый; avg — фикс, если средний чек не меньше norm."""
    k = ctx.kpi
    ids = set(_ids(ctx, emp, c))
    seen, s = set(), 0.0
    for r in ctx.db.c.execute("SELECT day, emp_key, client, amount FROM sales WHERE day BETWEEN ? AND ? AND amount > 0",
                              (str(k.start), str(k.end))):
        if k.who(r[1], r[0]) in ids:
            seen.add((r[0], r[2]))
            s += r[3]
    n = len(seen)
    if c.get("mode", "each") == "avg":
        avg = s / n if n else 0
        ok = avg >= c["norm"]
        return [(c.get("name") or "Средний чек", c["amount"] if ok else 0, f"{_n(avg)} при норме {_n(c['norm'])} ({n} заказов)")]
    return [(c.get("name") or "За заказы", n * c["price"], f"{n} × {_n(c['price'])}")] if n else []


def team_days(ctx, emp, c):
    """Супервайзеру: команда отработала норму дней (каждому можно allow выходных за свой счёт)."""
    k = ctx.kpi
    ids = [i for i in k.members("team", emp["team"]) if i != emp["id"]]
    if not ids:
        return []
    allow = c.get("allow", 3)
    got = sum(ctx.worked_days(k.employees[i]) for i in ids)
    need = sum(max(len(ctx._span(k.employees[i])[0]) - allow, 0) for i in ids)
    title = c.get("name") or "Команда отработала месяц"
    if not k.cal.closed:
        return [(title, 0, f"считается в конце месяца: сейчас {_d(got)} из {need} дн.")]
    return [(title, c["amount"] if got >= need else 0, f"{_d(got)} из {need} дн.")]


def old_debt(ctx, emp, c):
    """% от денег, собранных с клиентов, у которых на первую дату месяца была просрочка days+ (старые долги)."""
    k = ctx.kpi
    days = c.get("days", 91)
    first = ctx.db.c.execute('SELECT MIN("on") FROM debts WHERE "on" BETWEEN ? AND ?', (str(k.start), str(k.end))).fetchone()[0]
    if not first:
        return []
    bad = set()
    for r in ctx.db.c.execute('SELECT emp_key, client, total, d0_30, d31_60, d61_90, d91 FROM debts WHERE "on" = ?', (first,)):
        if k.who(r[0], first) == emp["id"]:
            row = {"total": r[2], "d0_30": r[3], "d31_60": r[4], "d61_90": r[5], "d91": r[6]}
            if k.overdue_amount(row, days) > 0:
                bad.add(r[1])
    got = 0.0
    for r in ctx.db.c.execute("SELECT day, emp_key, client, amount FROM payments WHERE day BETWEEN ? AND ?", (str(k.start), str(k.end))):
        if r[2] in bad and k.who(r[1], r[0]) == emp["id"]:
            got += r[3]
    title = c.get("name") or f"Собрано старых долгов {days}+"
    return [(title, got * c["rate"], f"{_n(got)} × {_p(c['rate'], 1)} ({len(bad)} клиентов)")] if got else \
        [(title, 0, f"с должников {days}+ денег не было")]


def period_bonus(ctx, emp, c):
    """За квартал или год: сумма факта и плана всех месяцев периода; платится в последнем месяце периода."""
    from core.kpi.engine import Kpi
    month = ctx.kpi.month
    y, m = int(month[:4]), int(month[5:7])
    period = c.get("period", "quarter")
    last = m in (3, 6, 9, 12) if period == "quarter" else m == 12
    first = (m - 1) // 3 * 3 + 1 if period == "quarter" else 1
    title = c.get("name") or ("Бонус за квартал" if period == "quarter" else "Бонус за год")
    fact = plan = 0.0
    metric = c.get("metric", "sales")
    for mm in range(first, m + 1):
        k = ctx.kpi if mm == m else Kpi(ctx.cfg, ctx.db, f"{y}-{mm:02d}", ctx.cal.today, ctx.hooks)
        x = k.emp(emp["id"]).get(metric)
        if x is not None:
            fact += x.fact
            plan += x.plan
    pct = fact / plan if plan else 0
    got = 0
    for share, amount in sorted(c.get("steps") or []):
        if pct >= share - 1e-9:
            got = amount
    when = "квартала" if period == "quarter" else "года"
    if not last:
        return [(title, 0, f"с начала {when} {_p(pct)} — платится в конце {when}")]
    return [(title, got, f"{when}: {_n(fact)} из {_n(plan)} — {_p(pct)}")]


class _UiLike:
    """Для доски почёта: ей нужен ctx.kpi(месяц) и ctx.cfg, как у экранов."""
    def __init__(self, pay):
        self._p = pay
        self.cfg = pay.cfg
        self.month = pay.kpi.month

    def kpi(self, month=None):
        from core.kpi.engine import Kpi
        if not month or month == self._p.kpi.month:
            return self._p.kpi
        return Kpi(self._p.cfg, self._p.db, month, self._p.cal.today, self._p.hooks)


def awards(ctx, emp, c):
    """За значки доски почёта месяца (план, лидер, клиенты, ассортимент, чистые долги, рывок)."""
    try:
        from modules.achievements import awards as _aw
        got = _aw(_UiLike(ctx)).get(emp["id"], [])
    except Exception:                              # noqa: BLE001 — доски почёта нет в наборе
        return []
    amount = len(got) * c["amount"]
    if c.get("max"):
        amount = min(amount, c["max"])
    return [(c.get("name") or "За значки", amount, f"{len(got)} знач. × {_n(c['amount'])}" if got else "значков нет")]


def pool(ctx, emp, c):
    """Котёл отдела: price × отгрузки отдела (all_points) или rate × продажи отдела; делится по коэффициентам и дням."""
    k = ctx.kpi
    if c.get("base", "points") == "points":
        fn = UNITS.get("all_points")
        qty = (fn(ctx, emp) or 0) if fn else 0
        total = qty * c.get("price", 0)
        what = f"{_d(qty)} отгрузок × {_n(c.get('price', 0))}"
    else:
        ids = k.members("dept", next((d.id for d in ctx.cfg.departments if d.kind == "sales"), None))
        sales = sum(k.sales.get(i, 0) for i in ids)
        total = sales * c.get("rate", 0)
        what = f"{_n(sales)} × {_p(c.get('rate', 0), 2)}"
    coefs = {str(a).lower(): float(b) for a, b in (c.get("coefs") or {}).items()}

    def coef(e):
        pos = (ctx.positions.get(e.get("position")) or {}).get("name", e.get("position") or "").lower()
        return next((v for key, v in coefs.items() if key in pos), 1.0)

    team = [e for e in k.employees.values() if e["active"] and e["dept"] == emp["dept"]
            and ctx.scheme_of(e) == ctx.scheme_of(emp)]
    w = {e["id"]: coef(e) * ctx.worked_days(e) for e in team}
    allw = sum(w.values())
    mine = total * w.get(emp["id"], 0) / allw if allw else 0
    return [(c.get("name") or "Котёл отдела", mine, f"{what} = {_n(total)}, доля {_p(w.get(emp['id'], 0) / allw if allw else 0, 1)}")]


def km_of(ctx, emp):
    """Км за месяц для ГСМ: из рейсов (логистика) + вписанные руками (табель, отметка «км»; торговые — сами за день)."""
    manual = sum(r["amount"] or 0 for r in ctx._ts.get(emp["id"], []) if r["kind"] == "km")
    fn = UNITS.get("km")
    trips = 0.0
    if fn and ctx.cfg.has_module("logistics"):
        try:
            trips = fn(ctx, emp) or 0
        except Exception:                          # noqa: BLE001 — рейсов нет
            trips = 0
    return trips + manual, trips, manual


def fuel_comp(ctx, emp, c):
    """ГСМ — в общую зарплату: fixed — фикс в месяц; day — за отработанный день; km — ₽ за км; norm — км × л/100 × цена литра."""
    mode = c.get("mode", "fixed")
    title = c.get("name") or "ГСМ"
    if mode == "fixed":
        return [(title, c.get("amount", 0), "фикс в месяц")]
    if mode == "day":
        days = ctx.worked_days(emp)
        return [(title, days * c.get("price", 0), f"{_d(days)} дн. × {_n(c.get('price', 0))}")]
    km, trips, manual = km_of(ctx, emp)
    src = " + ".join(x for x in (f"рейсы {_d(round(trips))}" if trips else "", f"вписано {_d(round(manual))}" if manual else "") if x)
    if not km:
        return [(title, 0, "км за месяц пока не вписаны")]
    if mode == "norm":
        liters = km * c.get("l100", 10) / 100
        return [(title, liters * c.get("fuel_price", 0), f"{_d(round(km))} км ({src}) × {_d(c.get('l100', 10))} л/100 = "
                 f"{_d(round(liters))} л × {_n(c.get('fuel_price', 0))}")]
    return [(title, km * c.get("price", 0), f"{_d(round(km))} км ({src}) × {_d(c.get('price', 0))} ₽")]


# Кубики, которые считаются после остальных: множитель и пределы. Строка расчёта помнит свой кубик (kind).
PERCENT_KINDS = {"sales_percent"}
BONUS_KINDS = {"plan_bonus", "fixed_bonus", "debt_bonus", "new_clients", "rank_bonus", "per_unit", "seniority",
               "growth_bonus", "kpi_card", "orders", "team_days", "period_bonus", "awards", "pool", "fuel_comp"}
PERCENT_KINDS |= {"old_debt"}


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
    if mn and c.get("own") and emp.get("salary") is not None:      # «не меньше оклада» — своего оклада человека
        mn = emp["salary"]
    if mn and total < mn:
        out.append((c.get("name") or "Доплата до гарантированного минимума", mn - total, f"минимум {_n(mn)}"))
    return out


POST = {"multiplier": multiplier, "limits": limits}


BUILTIN = {"new_clients": new_clients, "discount_penalty": discount_penalty, "rank_bonus": rank_bonus,
           "seniority": seniority, "salary": salary, "sales_percent": sales_percent, "plan_bonus": plan_bonus, "fixed_bonus": fixed_bonus,
           "debt_penalty": debt_penalty, "debt_bonus": debt_bonus, "timesheet": timesheet, "per_unit": per_unit,
           "growth_bonus": growth_bonus, "kpi_card": kpi_card, "orders": orders, "team_days": team_days, "old_debt": old_debt,
           "period_bonus": period_bonus, "awards": awards, "pool": pool, "fuel_comp": fuel_comp}


def _n(x):
    return f"{round(x):,}".replace(",", " ")


def _p(x, digits=0):
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def _d(x):
    return f"{x:g}".replace(".", ",")
