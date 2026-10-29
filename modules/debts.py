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
    first = min((ctx.cfg.raw.get("kpi") or {}).get("debt_days") or [days])
    norm = k.debt_norm
    base_t = {"month": "продажам месяца", "30d": "продажам за 30 дней", "debt": "всему долгу"}.get(k.debt_base, "продажам")
    rows_all = [(i, r) for i in ids for r in k.debts.get(i, [])]
    total = sum(r["total"] for _, r in rows_all)
    over1 = sum(k.overdue_amount(r, first) for _, r in rows_all)
    over, base, share = k.debt_share(ids)
    to_norm = sum(max(o - norm * b_, 0) for o, b_, _ in (k.debt_share([i]) for i in ids))   # каждый — до своей нормы
    title = {"e": k.employees[int(ident)]["fio"] if kind == "e" else "",
             "t": next((t.name for t in ctx.cfg.teams if t.id == ident), "")}.get(kind, "Отдел продаж")
    on = day_title(date.fromisoformat(k.debt_on)) if k.debt_on else "—"
    v = View(f"💵 Долги · {title}", f"срез на {on}")
    t = [("Всего долг", money(total), None)]
    if first != days:
        t.append((f"Просрочка {first}+", money(over1), "warn" if over1 else "good"))
    t += [(f"Просрочка {days}+", money(over), "bad" if share > norm else "good"),
          ("Собрать до нормы", money(to_norm), "bad" if to_norm else "good")]
    v.tiles(t)
    if kind == "e":
        top = sorted((r for _, r in rows_all), key=lambda r: -(k.overdue_amount(r) + k.overdue_amount(r, first) * 0.5))
        v.cards([Card(ctx.db.client_name(r["client"]), money(r["total"]),
                      [f"{first}+: {money(k.overdue_amount(r, first))}" + (f" · {days}+: {money(k.overdue_amount(r))}"
                                                                          if first != days else "")],
                      color="bad" if k.overdue_amount(r) else "warn")
                 for r in top[:15] if k.overdue_amount(r, first) > 0], title="Кто просрочил")
        v.note(f"Доля просрочки {days}+ — {pct(share, 1)} к {base_t}, норма — {pct(norm)}."
               + (f" Чтобы выйти в норму, собрать {money(to_norm)}." if to_norm else ""))
        kb = [[Button("‹ План", f"emp:{ident}")]]
    else:
        per = []
        for i in ids:
            o, b_, sh = k.debt_share([i])
            per.append((i, sum(r["total"] for r in k.debts.get(i, [])), o, sh, max(o - norm * b_, 0)))
        per.sort(key=lambda x: -x[2])
        v.table(["Менеджер", "Долг", f"{days}+", "Доля", "Собрать"],
                [Row([short(k.employees[i]["fio"]), money(t_, ""), money(o, ""),
                      Cell(pct(sh), "bad" if sh > norm else "warn" if sh > norm / 2 else "good"), money(tn, "") if tn else "—"])
                 for i, t_, o, sh, tn in per])
        v.note(f"Доля — просрочка {days}+ к {base_t}. Больше {pct(norm)} — красная зона; «Собрать» — сколько занести до нормы.")
        kb = rows([Button(short(k.employees[i]["fio"]), f"debts:e:{i}") for i, *_ in per], 3)
    return Reply(view=v, buttons=kb)
