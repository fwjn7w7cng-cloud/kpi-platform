"""Модуль «Предварительный расчёт зарплаты»: моя зарплата строка за строкой, фонд по команде/отделу/компании,
правки (премия или удержание с причиной).

Правки: без модуля «Цепочка утверждения» правку вносит руководитель компании или админ — сразу принята.
С модулем — руководитель отдела и супервайзер предлагают, руководитель компании решает (modules/approval).
"""
from core.calendar import month_title
from core.kpi.engine import SELLERS
from core.month import cycle
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Cell, Row, View

M = "payroll"

menu("pay", "💰 Моя зарплата", {"rep", "newbie", "sv", "staff", "head", "accountant"}, module=M, order=15,
     when=lambda c: c.me is not None)
menu("fund", "💰 Фонд зарплаты", {"head", "owner", "accountant", "admin"}, module=M, order=40)


@screen("pay", module=M)
def payslip(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    slip = ctx.pay().calc(emp)
    pos = ctx.cfg.position(emp["position"])
    st = cycle.status(ctx.db, ctx.month)
    v = View(f"💰 {emp['fio']}", f"{pos['name'] if pos else ''} · {month_title(ctx.month).lower()} · "
                                f"{'утверждено' if st == 'approved' else 'предварительно'}")
    v.tiles([("К выплате", money(slip.total), "good" if slip.total else None),
             ("Отработано", f"{slip.days:g} из {slip.days_total} дн.", None)])
    v.table(["Строка", "Сумма"], [Row([r[0], Cell(money(r[1], ""), "bad" if r[1] < 0 else None)], sub=r[2])
                                  for r in slip.rows])
    if not slip.rows:
        v.note("Для этой должности зарплата в боте не считается.")
    elif st != "approved":
        v.note("Расчёт предварительный: обновляется с каждым отчётом. Официальная сумма — после утверждения месяца.")
    kb = []
    if emp["role"] in SELLERS:
        kb.append([Button("📈 План", f"emp:{emp['id']}")])
    if _may_edit(ctx) and st != "approved" and (not ctx.me or emp["id"] != ctx.me["id"]):
        kb.append([Button("✏️ Премия / удержание", f"eda:{emp['id']}")])
    return Reply(view=v, buttons=kb)


@screen("fund", module=M)
def fund(ctx, kind=None, ident=None):
    p = ctx.pay()
    es = [e for e in ctx.kpi().employees.values() if ctx.sees(e)]
    es = [e for e in es if e["active"] or p.calc(e).total]
    if ctx.scope == "dept" and ctx.me and not kind:
        kind, ident = "dept", ctx.me["dept"]
    if ctx.scope == "team" and ctx.me:
        kind, ident = "t", ctx.me["team"]
    if kind == "t":
        es = [e for e in es if e["team"] == ident]
    elif kind == "dept":
        es = [e for e in es if e["dept"] == ident]
    total = sum(p.calc(e).total for e in es)
    title = {"t": next((t.name for t in ctx.cfg.teams if t.id == ident), ""),
             "dept": (ctx.cfg.dept(ident).name if ident and ctx.cfg.dept(ident) else "")}.get(kind, "вся компания")
    v = View("💰 Фонд зарплаты", f"{title} · {month_title(ctx.month).lower()}")
    v.tiles([("Итого", money(total), None), ("Человек", str(len(es)), None)])
    if not kind and ctx.scope == "all":
        rs = []
        for d in ctx.cfg.departments:
            part = [e for e in es if e["dept"] == d.id]
            if part:
                rs.append(Row([d.name, str(len(part)), money(sum(p.calc(e).total for e in part), "")]))
        v.table(["Отдел", "Чел.", "Сумма"], rs)
        kb = rows([Button(d.name, f"fund:dept:{d.id}") for d in ctx.cfg.departments], 2)
    else:
        es.sort(key=lambda e: -p.calc(e).total)
        v.table(["Сотрудник", "Сумма"], [Row([e["fio"], money(p.calc(e).total, "")]) for e in es])
        kb = rows([Button(short(e["fio"]), f"pay:{e['id']}") for e in es], 3)
        if ctx.scope == "all":
            kb.append([Button("‹ Все отделы", "fund")])
    kb.append([Button("✏️ Правки", "edits")] if _may_edit(ctx) or ctx.role in ("owner", "admin") else [])
    return Reply(view=v, buttons=[r for r in kb if r])


# ---------- правки ----------

def _may_edit(ctx):
    if ctx.role in ("owner", "admin"):
        return True
    return ctx.can("approval") and ctx.role in ("head", "sv")


@screen("edits", module=M)
def edits(ctx):
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    es = [e for e in cycle.edits(ctx.db, ctx.month) if ctx.sees(e["emp_id"])]
    v = View("✏️ Премии и удержания", month_title(ctx.month))
    icon = {"wait": "⏳", "ok": "✅", "no": "❌"}
    v.table(["Сотрудник", "Сумма", ""], [Row([short(names.get(e["emp_id"], "?")), Cell(money(e["amount"], ""),
                                              "good" if e["amount"] > 0 else "bad"), icon[e["status"]]],
                                             sub=f"{e['reason']} · {e['author']}") for e in es])
    if not es:
        v.note("Правок в этом месяце нет.")
    kb = []
    if ctx.role in ("owner", "admin") and ctx.can("approval") and any(e["status"] == "wait" for e in es):
        kb.append([Button("✅ Решить по правкам", "appr")])
    if _may_edit(ctx) and cycle.can_edit(ctx.db, ctx.month):
        kb.append([Button("➕ Новая правка", "eda")])
    return Reply(view=v, buttons=kb)


@screen("eda", module=M)
def edit_add(ctx, emp_id=None):
    if not _may_edit(ctx):
        return Reply(text="Правки вносит руководитель.")
    if not cycle.can_edit(ctx.db, ctx.month):
        return Reply(text="Месяц утверждён — правки закрыты.")
    if not emp_id:
        es = [e for e in ctx.db.employees() if ctx.sees(e) and (not ctx.me or e["id"] != ctx.me["id"])]
        return Reply(text="Кому правка?", buttons=rows([Button(short(e["fio"]), f"eda:{e['id']}") for e in es], 3))
    emp = ctx.db.employee(int(emp_id))
    return ctx.ask("edit", f"✏️ {emp['fio']}\n\nНапишите сумму и причину. Премия — просто число, удержание — с минусом:\n"
                           "«5000 подменял коллегу» или «-2000 опоздал с отчётом»", {"emp": emp["id"]})


@text_input("edit")
def on_edit(ctx, text, data):
    parts = text.strip().split(maxsplit=1)
    try:
        amount = float(parts[0].replace(" ", "").replace(",", ".").replace("−", "-"))
    except (ValueError, IndexError):
        return ctx.ask("edit", "Сначала сумма, потом причина: «5000 подменял коллегу»", data)
    reason = parts[1] if len(parts) > 1 else ""
    direct = ctx.role in ("owner", "admin") or not ctx.can("approval")
    try:
        cycle.propose(ctx.db, ctx.month, data["emp"], amount, reason, ctx.author(), direct=direct)
    except cycle.MonthError as e:
        return ctx.ask("edit", f"⚠️ {e}. Попробуйте ещё раз.", data)
    emp = ctx.db.employee(data["emp"])
    msg = f"{'✅ Принято' if direct else '📨 Отправлено на утверждение'}: {emp['fio']} {money(amount)} — {reason}"
    out = [Reply(text=msg)]
    if not direct:
        for o in ctx.db.employees():
            if o["role"] == "owner" and o["tg_id"]:
                out.append(Reply(text=f"✏️ Новая правка: {emp['fio']} {money(amount)} — {reason}\nПредложил: {ctx.author()}",
                                 to=o["tg_id"], buttons=[[Button("✅ Решить", "appr")]]))
    out.append(edits(ctx))
    return out
