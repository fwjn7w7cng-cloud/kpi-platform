"""Модуль «Свои формулы KPI»: показатели компании по формуле из готовых цифр (formulas в анкете).
Сами формулы считает ядро (core/kpi/formula.py) и показывает в карточке; здесь — сводный экран.
"""
from core.ui.kit import Reply, fmt
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Row, View

M = "formulas"

menu("fx", "🧮 Свои показатели", {"sv", "head", "owner", "admin"}, module=M, order=58,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


@screen("fx", module=M)
def formulas(ctx):
    k = ctx.kpi()
    fs = ctx.cfg.raw.get("formulas") or {}
    if not fs:
        return Reply(text="В анкете компании пока нет своих формул.")
    sales_dept = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    ids = k.members("team", ctx.me["team"]) if ctx.role == "sv" and ctx.me else k.members("dept", sales_dept)
    v = View("🧮 Свои показатели", " · ".join(f"{f['name']} = {f['expr']}" for f in fs.values()))
    head = ["Менеджер"] + [f["name"] for f in fs.values()]
    rs = []
    for i in ids:
        m = k.emp(i)
        rs.append(Row([short(k.employees[i]["fio"])] + [fmt(m[f"f:{c}"].fact, f.get("unit", "")) for c, f in fs.items()]))
    v.table(head, rs)
    return Reply(view=v)
