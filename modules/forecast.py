"""Модуль «Прогноз плана и сравнение отделов»: куда придём к концу месяца при нынешнем темпе,
и как команды идут сейчас против того же дня прошлого месяца.
"""
from datetime import date

from core.calendar import month_title, shift_month
from core.data.db import month_bounds
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Cell, Row, View, tone

M = "forecast"

menu("fc", "🔮 Прогноз", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=28,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")
menu("cmp", "⚖️ Сравнение команд", {"head", "owner", "admin"}, module=M, order=29,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


@screen("fc", module=M)
def forecast(ctx, kind=None, ident=None):
    k = ctx.kpi()
    if not kind:
        if ctx.role in ("rep", "newbie") and ctx.me:
            kind, ident = "e", str(ctx.me["id"])
        elif ctx.role == "sv" and ctx.me:
            kind, ident = "t", ctx.me["team"]
        else:
            kind = "d"
    if kind == "e":
        emp = ctx.db.employee(int(ident))
        if not ctx.sees(emp):
            return Reply(text="Нет доступа.")
        m = k.emp(emp["id"])
        v = View(f"🔮 Прогноз · {emp['fio']}", f"если работать как сейчас · {k.cal.left} раб. дн. впереди")
        rs = []
        for code in ("sales", "focus", "collect"):
            x = m[code]
            if not x.plan:
                continue
            f = x.forecast
            rs.append(Row([x.title, money(x.fact, ""), money(f, ""), Cell(pct(f / x.plan), tone(f / x.plan))],
                          sub=f"план {money(x.plan)}"))
        v.table(["Показатель", "Сейчас", "Прогноз", "% плана"], rs)
        if ctx.can("payroll"):
            v.note(_pay_forecast(ctx, emp))
        return Reply(view=v, buttons=[[Button("‹ План", f"emp:{emp['id']}")]])
    sales_dept = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    ids = k.members("team", ident) if kind == "t" else k.members("dept", sales_dept)
    sc = k.scope("team", ident) if kind == "t" else k.scope("dept", sales_dept)
    s = sc["sales"]
    v = View("🔮 Прогноз на конец месяца", month_title(ctx.month))
    if s.plan:
        v.tiles([("Сейчас", pct(s.pct), s.tone), ("Прогноз", pct(s.forecast / s.plan), tone(s.forecast / s.plan)),
                 ("Не хватит", money(max(s.plan - s.forecast, 0)), "bad" if s.forecast < s.plan else "good")])
    rs = []
    for i in ids:
        x = k.emp(i)["sales"]
        if x.plan:
            rs.append((x.forecast / x.plan, Row([short(k.employees[i]["fio"]), money(x.forecast, ""),
                                                 Cell(pct(x.forecast / x.plan), tone(x.forecast / x.plan))])))
    v.table(["Менеджер", "Прогноз, ₽", "% плана"], [r for _, r in sorted(rs, key=lambda t: -t[0])])
    return Reply(view=v)


def _pay_forecast(ctx, emp):
    """Сколько выйдет зарплата, если дотянуть до 100% плана (продажи — по плану)."""
    p = ctx.pay()
    now = p.calc(emp).total
    m = ctx.kpi().emp(emp["id"])["sales"]
    if not m.plan or m.fact >= m.plan:
        return f"Зарплата сейчас {money(now)}."
    return f"Зарплата сейчас {money(now)}. Каждые +{money(m.plan * 0.1)} продаж приближают следующий бонус."


@screen("cmp", module=M)
def compare(ctx):
    """Команды: этот месяц к сегодняшнему дню против прошлого к тому же дню."""
    k = ctx.kpi()
    prev_m = shift_month(ctx.month, -1)
    s1, _ = month_bounds(ctx.month)
    s0, e0 = month_bounds(prev_m)
    day = min(ctx.today.day, int(e0[-2:]))
    cut0 = f"{prev_m}-{day:02d}"
    prev = ctx.kpi(prev_m)
    v = View("⚖️ Сравнение команд", f"{month_title(ctx.month)} против {month_title(prev_m).lower()} к {day} числу")
    rs = []
    for t in ctx.cfg.teams:
        ids = k.members("team", t.id)
        now = sum(k.sales.get(i, 0) for i in ids)
        before = sum(v_ for i in prev.members("team", t.id) for d, v_ in prev.daily.get(i, {}).items() if d < cut0)
        g = now / before - 1 if before else None
        rs.append(Row([t.name, money(now, ""), money(before, ""), Cell(("+" if g and g > 0 else "") + pct(g) if g is not None else "—",
                                                                    "good" if g and g > 0 else "bad" if g is not None else None)]))
    v.table(["Команда", "Сейчас", "Тогда", "Рост"], rs)
    by_dept = []
    for d in ctx.cfg.departments:
        if ctx.can("payroll"):
            es = [e for e in k.employees.values() if e["dept"] == d.id and e["active"]]
            by_dept.append(Row([d.name, str(len(es)), money(sum(ctx.pay().calc(e).total for e in es), "")]))
    if by_dept:
        v.table(["Отдел", "Чел.", "Фонд сейчас"], by_dept, title="Отделы")
    return Reply(view=v)
