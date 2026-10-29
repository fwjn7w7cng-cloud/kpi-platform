"""Модуль «Кабинеты склада, водителей, офиса»: у каждого сотрудника не из продаж — свой кабинет
(дни, зарплата, табель, задачи, отпуск), у начальника отдела — свой отдел.
"""
from core.calendar import month_title
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import menu, screen
from core.ui.screens import mod_button, short
from core.ui.views import Row, View

M = "staff"

menu("cab", "🧑‍🔧 Мой кабинет", {"staff", "accountant"}, module=M, order=5, when=lambda c: c.me is not None)
menu("cab", "🧑‍🔧 Мой кабинет", {"head"}, module=M, order=5,
     when=lambda c: c.me is not None and c.dept_kind != "sales")
menu("deptv", "🏬 Мой отдел", {"head"}, module=M, order=6, when=lambda c: c.dept_kind != "sales")
menu("deptv", "🏬 Отделы", {"owner", "admin"}, module=M, order=12)


@screen("cab", module=M)
def cabinet(ctx):
    if not ctx.me:
        return Reply(text="Кабинет есть у сотрудников.")
    return staff_card(ctx, str(ctx.me["id"]))


@screen("staffcard")
def staff_card(ctx, emp_id):
    emp = ctx.db.employee(int(emp_id))
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    if not ctx.can(M):
        return Reply(text="🔒 Кабинеты сотрудников вне продаж — в редакции «Бизнес».",
                     buttons=[[Button("Что это?", f"lock:{M}")]])
    p = ctx.pay()
    pos = ctx.cfg.position(emp["position"])
    dept = ctx.cfg.dept(emp["dept"])
    v = View(emp["fio"], f"{pos['name'] if pos else ''} · {dept.name if dept else ''} · {month_title(ctx.month).lower()}")
    t = [("Отработано", f"{p.worked_days(emp):g} из {ctx.kpi().cal.total} дн.", None),
         ("Пропусков", str(p.missed_days(emp)), "bad" if p.missed_days(emp) else "good")]
    if ctx.can("payroll"):
        t.append(("Зарплата", money(p.calc(emp).total), None))
    v.tiles(t)
    open_tasks = ctx.db.q("SELECT text, due FROM tasks WHERE emp_id = ? AND status = 'open' ORDER BY due", emp["id"])
    if open_tasks and ctx.can("tasks"):
        v.table(["Задача", "Срок"], [Row([x["text"], x["due"][8:10] + "." + x["due"][5:7]]) for x in open_tasks[:5]])
    kb = rows([mod_button(ctx, "💰 Зарплата", f"pay:{emp['id']}", "payroll"), Button("📅 Табель", f"ts:{emp['id']}"),
               mod_button(ctx, "📌 Задачи", "tasks", "tasks")], 2)
    return Reply(view=v, buttons=kb)


@screen("deptv", module=M)
def dept_view(ctx, dept_id=None):
    if ctx.role == "head" and ctx.me:
        dept_id = ctx.me["dept"]
    if not dept_id:
        ds = [d for d in ctx.cfg.departments if d.kind != "sales"]
        return Reply(text="Какой отдел?", buttons=rows([Button(d.name, f"deptv:{d.id}") for d in ds], 2))
    d = ctx.cfg.dept(dept_id)
    es = ctx.db.employees(dept=dept_id)
    p = ctx.pay()
    v = View(f"🏬 {d.name}", f"{len(es)} человек · {month_title(ctx.month).lower()}")
    t = [("Человек", str(len(es)), None), ("Пропусков", str(sum(p.missed_days(e) for e in es)), None)]
    if ctx.can("payroll"):
        t.append(("Фонд", money(sum(p.calc(e).total for e in es)), None))
    v.tiles(t)
    v.table(["Сотрудник", "Дней", "Зарплата"], [Row([e["fio"], f"{p.worked_days(e):g}",
                                                     money(p.calc(e).total, "") if ctx.can("payroll") else "—"]) for e in es])
    kb = rows([Button(short(e["fio"]), f"staffcard:{e['id']}") for e in es], 3)
    extra = [Button("📅 Табель отдела", "ts")]
    if d.kind in ("warehouse", "purchasing"):          # склад и закупки — остатки и заказы
        extra.append(mod_button(ctx, "📦 Остатки", "sk", "stock"))
    if d.kind == "logistics":
        extra.append(mod_button(ctx, "🚚 Рейсы", "lg", "logistics"))
    kb.append(extra)
    return Reply(view=v, buttons=kb)
