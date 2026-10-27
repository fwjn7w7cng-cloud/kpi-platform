"""Рассылки ядра: утренняя сводка каждому по его роли."""
from core.calendar import day_title, month_of
from core.jobs.schedule import job
from core.kpi.engine import MAIN, SELLERS
from core.ui.kit import METRIC_HEAD, Button, Reply, metric_row, money, pct
from core.ui.views import Cell, Row, View


@job("morning_brief", "morning", {"rep", "newbie", "sv", "head", "owner", "admin"}, "Утренняя сводка")
def morning(ctx):
    ctx.month = month_of(ctx.today)
    k = ctx.kpi()
    if not k.cal.is_workday(ctx.today):
        return None
    v = View(f"☀️ Доброе утро · {day_title(ctx.today)}", f"рабочий день {k.cal.passed + 1} из {k.cal.total}")
    if ctx.role in SELLERS and ctx.me:
        m = k.emp(ctx.me["id"])
        s = m["sales"]
        v.tiles([("План", pct(s.pct), s.tone), ("Нужно в день", money(s.per_day), None)])
        v.table(METRIC_HEAD, [metric_row(m[c]) for c in MAIN if m[c].plan])
        lines = ctx.app.hooks.run("morning_lines", ctx) if ctx.app.hooks else None
        if lines:
            v.lines(lines)
        return Reply(view=v, buttons=[[Button("📈 Мой план", "me"), Button("📞 Кому позвонить", "call")]])
    if ctx.role == "sv" and ctx.me:
        ids = k.members("team", ctx.me["team"])
        title = next((t.name for t in ctx.cfg.teams if t.id == ctx.me["team"]), "Команда")
    else:
        sales = next((d for d in ctx.cfg.departments if d.kind == "sales"), None)
        if not sales or (ctx.role == "head" and ctx.dept_kind != "sales"):
            return None
        ids = k.members("dept", sales.id)
        title = sales.name
    scope = k.scope("team", ctx.me["team"]) if ctx.role == "sv" else k.scope("dept", next(
        d.id for d in ctx.cfg.departments if d.kind == "sales"))
    s = scope["sales"]
    v.tiles([(title, pct(s.pct), s.tone), ("К сегодня нужно", pct(k.cal.need), None)])
    behind = [(i, m) for i, m in k.rating(ids) if m.tone == "bad"]
    v.table(["Отстают", "Продажи", "План"],
            [Row([k.employees[i]["fio"], money(m.fact, ""), Cell(pct(m.pct), m.tone)]) for i, m in behind[:8]])
    if not behind:
        v.note("Все идут в графике 👍")
    return Reply(view=v, buttons=[[Button("👥 Команда" if ctx.role == "sv" else "🏢 Отдел", "team" if ctx.role == "sv" else "sales")]])
