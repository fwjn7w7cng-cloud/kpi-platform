"""Модуль «Цепочка утверждения зарплаты и реестр»: руководитель отдела предлагает правку с причиной →
руководитель компании принимает или отклоняет → месяц утверждается → ведомость бухгалтеру, листки сотрудникам.
"""
from core.calendar import month_title
from core.month import cycle
from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, View

M = "approval"

menu("appr", "✅ На утверждение", {"owner", "admin"}, module=M, order=35)


@screen("appr", module=M)
def queue(ctx, edit_id=None, ok=None):
    if ctx.role not in ("owner", "admin"):
        return Reply(text="Решает руководитель компании.")
    out = []
    if edit_id:
        try:
            cycle.decide(ctx.db, int(edit_id), ok == "1", ctx.author())
            e = ctx.db.one("SELECT * FROM edits WHERE id = ?", int(edit_id))
            emp = ctx.db.employee(e["emp_id"])
            out.append(Reply(text=f"{'✅ Принято' if ok == '1' else '❌ Отклонено'}: {emp['fio']} {money(e['amount'])}"))
        except cycle.MonthError as err:
            out.append(Reply(text=f"⚠️ {err}"))
    wait = cycle.edits(ctx.db, ctx.month, "wait")
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    v = View("✅ Правки ждут решения", month_title(ctx.month))
    v.cards([Card(names.get(e["emp_id"], "?"), money(e["amount"]), [e["reason"], f"предложил: {e['author']}"],
                  color="good" if e["amount"] > 0 else "bad") for e in wait])
    if not wait:
        v.note("Всё решено 👍")
    kb = [[Button(f"✅ {short(names.get(e['emp_id'], '?'))} {money(e['amount'], '')}", f"appr:{e['id']}:1"),
           Button("❌", f"appr:{e['id']}:0")] for e in wait[:8]]
    kb.append([Button("🗓 Месяц", "month")])
    if cycle.status(ctx.db, ctx.month) == "approved":
        kb.append([Button("📨 Разослать расчётные листки", "slips")])
    out.append(Reply(view=v, buttons=kb))
    return out


@screen("slips", module=M)
def send_slips(ctx):
    """Каждому сотруднику с мессенджером — его расчётный листок за утверждённый месяц."""
    if ctx.role not in ("owner", "admin", "accountant"):
        return Reply(text="Нет доступа.")
    if cycle.status(ctx.db, ctx.month) != "approved":
        return Reply(text="Листки рассылаются после утверждения месяца.")
    from modules.payroll import payslip
    out, n = [], 0
    for e in ctx.db.employees():
        if not e["tg_id"]:
            continue
        r = payslip(ctx, str(e["id"]))
        r.to, r.edit, r.buttons = e["tg_id"], False, []
        out.append(r)
        n += 1
    out.insert(0, Reply(text=f"📨 Листки разосланы: {n} чел." + ("" if n else " (ни у кого не привязан мессенджер)")))
    return out
