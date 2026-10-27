"""Модуль «Дебиторка»: долги клиентов по срокам на последний срез — у торгового, команды, отдела.

Срез — последняя пачка долгов не позже сегодня (или конца выбранного месяца).
"""
from core.calendar import day_title
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money, pct, rows
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View
from datetime import date

M = "debts"

menu("debts", "💵 Долги", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=30,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def _scope_ids(ctx, kind, ident):
    k = ctx.kpi()
    if kind == "e":
        return [int(ident)]
    if kind == "t":
        return k.members("team", ident)
    return [i for i in k.members("dept", next(d.id for d in ctx.cfg.departments if d.kind == "sales")) if ctx.sees(i)]


@screen("debts", module=M)
def debts(ctx, kind=None, ident=None):
    if not kind:
        if ctx.role in SELLERS and ctx.me:
            kind, ident = "e", str(ctx.me["id"])
        elif ctx.role == "sv" and ctx.me:
            kind, ident = "t", ctx.me["team"]
        else:
            kind = "d"
    ids = _scope_ids(ctx, kind, ident)
    if kind == "e" and not ctx.sees(int(ident)):
        return Reply(text="Нет доступа.")
    k = ctx.kpi()
    days = k.debt_days
    rows_all = [(i, r) for i in ids for r in k.debts.get(i, [])]
    total = sum(r["total"] for _, r in rows_all)
    over30 = sum(r["d31_60"] + r["d61_90"] + r["d91"] for _, r in rows_all)
    over60 = sum(r["d61_90"] + r["d91"] for _, r in rows_all)
    sales = sum(k.sales.get(i, 0) for i in ids)
    title = {"e": k.employees[int(ident)]["fio"] if kind == "e" else "",
             "t": next((t.name for t in ctx.cfg.teams if t.id == ident), "")}.get(kind, "Отдел продаж")
    on = day_title(date.fromisoformat(k.debt_on)) if k.debt_on else "—"
    v = View(f"💵 Долги · {title}", f"срез на {on}")
    v.tiles([("Всего долг", money(total), None), ("Просрочка 30+", money(over30), "warn" if over30 else "good"),
             (f"Просрочка {days}+", money(over60), "bad" if over60 > 0.1 * max(sales, 1) else "good")])
    if kind == "e":
        top = sorted((r for _, r in rows_all), key=lambda r: -(r["d61_90"] + r["d91"] + r["d31_60"] * 0.5))
        v.cards([Card(ctx.db.client_name(r["client"]), money(r["total"]),
                      [f"31–60: {money(r['d31_60'])} · 61+: {money(r['d61_90'] + r['d91'])}"],
                      color="bad" if r["d61_90"] + r["d91"] else "warn" if r["d31_60"] else None)
                 for r in top[:15] if r["d31_60"] + r["d61_90"] + r["d91"] > 0], title="Кто просрочил")
        kb = [[Button("‹ План", f"emp:{ident}")]]
    else:
        per = []
        for i in ids:
            rs = k.debts.get(i, [])
            o = sum(r["d61_90"] + r["d91"] for r in rs)
            s = k.sales.get(i, 0)
            share = o / s if s else (1 if o else 0)
            per.append((i, sum(r["total"] for r in rs), o, share))
        per.sort(key=lambda x: -x[2])
        v.table(["Менеджер", "Долг", f"{days}+", "Доля"],
                [Row([short(k.employees[i]["fio"]), money(t, ""), money(o, ""),
                      Cell(pct(sh), "bad" if sh > 0.1 else "warn" if sh > 0.05 else "good")]) for i, t, o, sh in per])
        v.note(f"Доля — просрочка {days}+ к продажам месяца. Больше 10% — красная зона.")
        kb = rows([Button(short(k.employees[i]["fio"]), f"debts:e:{i}") for i, *_ in per], 3)
    return Reply(view=v, buttons=kb)
