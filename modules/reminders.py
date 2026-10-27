"""Модуль «Напоминания и тревоги»: кто отстаёт от плана прямо сейчас (экран руководителю)
и вечерние напоминания — торговому «сколько нужно завтра», руководителю — кто отстаёт.
"""
from core.calendar import month_of
from core.jobs.schedule import job
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Cell, Row, View

M = "reminders"

menu("alerts", "🔔 Кто отстаёт", {"sv", "head", "owner", "admin"}, module=M, order=25,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def _ids(ctx):
    k = ctx.kpi()
    if ctx.role == "sv" and ctx.me:
        return k.members("team", ctx.me["team"])
    sales = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    return k.members("dept", sales)


def behind(ctx):
    """[(сотрудник, показатель, отставание в ₽ от «нужно к сегодня»)] — по всем показателям с планом."""
    k = ctx.kpi()
    out = []
    for i in _ids(ctx):
        m = k.emp(i)
        for code in ("sales", "focus", "collect"):
            x = m[code]
            if x.plan and x.tone == "bad":
                gap = x.plan * x.need - x.fact
                out.append((i, x, gap))
    return sorted(out, key=lambda t: -t[2])


@screen("alerts", module=M)
def alerts(ctx):
    k = ctx.kpi()
    rows_ = behind(ctx)
    v = View("🔔 Кто отстаёт", f"к сегодня нужно {pct(k.cal.need)} плана")
    v.table(["Сотрудник", "Показатель", "Выполнено", "Отставание"],
            [Row([short(k.employees[i]["fio"]), x.title, Cell(pct(x.pct), x.tone), money(gap, "")]) for i, x, gap in rows_[:20]])
    if not rows_:
        v.note("Все идут в графике 👍")
    else:
        v.note("Отставание — сколько не хватает до «нужно к сегодня».")
    kb = [[Button(f"{short(k.employees[i]['fio'])}", f"emp:{i}") for i, _, _ in rows_[j:j + 3]] for j in range(0, min(len(rows_), 9), 3)]
    return Reply(view=v, buttons=kb)


@job("evening_rep", "evening", {"rep", "newbie"}, "Вечернее напоминание торговому", module=M)
def evening_rep(ctx):
    ctx.month = month_of(ctx.today)
    if ctx.role not in SELLERS or not ctx.me:
        return None
    m = ctx.kpi().emp(ctx.me["id"])["sales"]
    if not m.plan or not m.left or not ctx.kpi().cal.left:
        return None
    mood = "Идёте в графике 👍" if m.tone == "good" else "Чуть отстаёте" if m.tone == "warn" else "Отставание от графика ⚠️"
    return Reply(text=f"🌙 {mood}\nПлан выполнен на {pct(m.pct)}. Чтобы успеть, нужно по {money(m.per_day)} в день "
                      f"ещё {ctx.kpi().cal.left} раб. дн.", buttons=[[Button("📈 Мой план", "me")]])


@job("evening_leads", "evening", {"sv", "head", "owner"}, "Вечером руководителю: кто отстаёт", module=M)
def evening_leads(ctx):
    ctx.month = month_of(ctx.today)
    if ctx.role == "head" and ctx.dept_kind != "sales":
        return None
    rows_ = behind(ctx)
    if not rows_:
        return None
    return alerts(ctx)
