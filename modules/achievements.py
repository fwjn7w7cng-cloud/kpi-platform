"""Модуль «Достижения и челленджи»: медали за месяц (считаются из цифр, хранить нечего) и доска почёта.

Медали (код → за что):
  plan     🎯 План выполнен (за прошедший месяц — по факту, за текущий — если прогноз ≥ 100%)
  leader   🥇 Лидер месяца — лучший % плана в отделе
  akb      👥 Больше всех активных клиентов
  sku      📦 Шире всех ассортимент в точках
  clean    💎 Чистые долги — просрочки почти нет
  rocket   🚀 Рывок — самый большой рост продаж к прошлому месяцу (от +10%)
"""
from core.calendar import month_title, shift_month
from core.ui.kit import Button, Reply, pct
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "achievements"

MEDALS = {"plan": ("🎯", "План выполнен", "План"), "leader": ("🥇", "Лидер месяца", "Лидер"),
          "akb": ("👥", "Больше всех клиентов", "АКБ"), "sku": ("📦", "Шире всех ассортимент", "SKU"),
          "clean": ("💎", "Чистые долги", "Долги"), "rocket": ("🚀", "Рывок месяца", "Рывок")}

menu("achb", "🏅 Доска почёта", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=45,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def awards(ctx, month=None):
    """{сотрудник: [коды медалей]} за месяц."""
    month = month or ctx.month
    k = ctx.kpi(month)
    sales_dept = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    ids = k.members("dept", sales_dept)
    out = {i: [] for i in ids}
    if not ids:
        return out
    for i in ids:
        m = k.emp(i)
        s = m["sales"]
        if s.plan and ((k.cal.closed and (s.pct or 0) >= 1) or (not k.cal.closed and s.forecast >= s.plan)):
            out[i].append("plan")
        if m["overdue"].detail.get("debt") and m["overdue"].fact <= 0.02:
            out[i].append("clean")
    best = max(ids, key=lambda i: k.emp(i)["sales"].pct or 0)
    out[best].append("leader")
    out[max(ids, key=lambda i: k.emp(i)["akb"].fact)].append("akb")
    out[max(ids, key=lambda i: k.emp(i)["sku"].fact)].append("sku")
    prev = ctx.kpi(shift_month(month, -1))
    growth = {}
    for i in ids:
        a = prev.sales.get(i, 0)
        if a > 0:
            b = k.sales.get(i, 0) if k.cal.closed else k.emp(i)["sales"].forecast
            growth[i] = b / a - 1
    if growth:
        g = max(growth, key=growth.get)
        if growth[g] >= 0.10:
            out[g].append("rocket")
    return out


@screen("achb", module=M)
def board(ctx):
    a = awards(ctx)
    k = ctx.kpi()
    ranked = sorted(a.items(), key=lambda t: (-len(t[1]), -(k.emp(t[0])["sales"].pct or 0)))
    v = View("🏅 Доска почёта", month_title(ctx.month) + ("" if k.cal.closed else " · пока идёт месяц"))
    v.table(["Сотрудник", "Медали", "План"],
            [Row([short(k.employees[i]["fio"]), Cell(" · ".join(MEDALS[c][2] for c in ms) or "—", "blue" if ms else "muted"),
                  Cell(pct(k.emp(i)["sales"].pct), k.emp(i)["sales"].tone)]) for i, ms in ranked], title="Медали месяца")
    v.lines([f"{w} — {t.lower()}" for _, t, w in MEDALS.values()], title="За что медали")
    return Reply(view=v, buttons=[[Button(short(k.employees[i]["fio"]), f"ach:{i}") for i, _ in ranked[j:j + 3]]
                                  for j in range(0, min(len(ranked), 9), 3)])


@screen("ach", module=M)
def mine(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp:
        return board(ctx)
    hist = []
    for n in range(3):
        mon = shift_month(ctx.month, -n)
        got = awards(ctx, mon).get(emp["id"], [])
        hist.append((mon, got))
    v = View(f"🏅 {emp['fio']}", "медали за 3 месяца")
    v.cards([Card(month_title(m), str(len(g)) if g else "—", [MEDALS[c][1] for c in g] or ["медалей нет"],
                  color="good" if g else None) for m, g in hist])
    return Reply(view=v, buttons=[[Button("🏅 Доска почёта", "achb")]])
