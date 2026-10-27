"""Модуль «AI-аналитик»: главное в цифрах простыми словами — где больше всего денег теряем,
кто тянет, кто проседает, чем рискуем.

Сейчас выводы собираются правилами из готовых расчётов (быстро, бесплатно, без отправки данных наружу).
Подключение языковой модели — следующим шагом: она получит эти же цифры и напишет разбор свободным текстом.
"""
from core.calendar import shift_month
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import View

M = "ai"

menu("ai", "🤖 Аналитик", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=27,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def insights(ctx):
    k = ctx.kpi()
    out = []
    if ctx.role in SELLERS and ctx.me:
        m = k.emp(ctx.me["id"])
        worst = min((x for c, x in m.items() if c.startswith("grp:") and x.plan), key=lambda x: x.pct or 0, default=None)
        if worst and (worst.pct or 0) < k.cal.need:
            out.append(f"Слабее всего — «{worst.title}»: {pct(worst.pct)} плана. Добор {money(worst.plan * k.cal.need - worst.fact)} "
                       f"выведет группу в график.")
        s = m["sales"]
        if s.plan and s.forecast < s.plan:
            out.append(f"По нынешнему темпу месяц закроется на {pct(s.forecast / s.plan)}. Нужно {money(s.per_day)} в день вместо "
                       f"{money(s.fact / max(k.cal.passed, 1))}.")
        od = m["overdue"]
        if od.detail.get("sum"):
            out.append(f"Просрочка {od.detail['days']}+ дней — {money(od.detail['sum'])}. Собрать её — самый быстрый плюс к зарплате.")
        return out or ["Всё идёт по плану — держите темп 👍"]
    sales_dept = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    ids = k.members("team", ctx.me["team"]) if ctx.role == "sv" and ctx.me else k.members("dept", sales_dept)
    if not ids:
        return ["Пока нет данных по продажам."]
    gaps = sorted(((i, k.emp(i)["sales"]) for i in ids), key=lambda t: -(t[1].plan * k.cal.need - t[1].fact if t[1].plan else 0))
    i, x = gaps[0]
    if x.plan and x.plan * k.cal.need > x.fact:
        out.append(f"Больше всего отстаёт {short(k.employees[i]['fio'])}: не хватает {money(x.plan * k.cal.need - x.fact)} "
                   "до графика. Разговор с ним даст больше всего.")
    best = max(ids, key=lambda i: k.emp(i)["sales"].pct or 0)
    out.append(f"Лучше всех идёт {short(k.employees[best]['fio'])} — {pct(k.emp(best)['sales'].pct)} плана. "
               "Стоит разобрать, что он делает иначе.")
    prev = ctx.kpi(shift_month(ctx.month, -1))
    drops = []
    for i in ids:
        for c, v in prev.by_client.get(i, {}).items():
            if v > 0 and k.by_client.get(i, {}).get(c, 0) <= 0:
                drops.append(v)
    if drops and (ctx.today - k.cal.start).days >= 10:
        out.append(f"{len(drops)} клиентов покупали в прошлом месяце, а в этом — нет. Это {money(sum(drops))} продаж "
                   "прошлого месяца под вопросом.")
    sc = k.scope("team", ctx.me["team"]) if ctx.role == "sv" and ctx.me else k.scope("dept", sales_dept)
    od = sc["overdue"]
    if od.detail.get("sum"):
        out.append(f"Просрочка {od.detail['days']}+ — {money(od.detail['sum'])} ({pct(od.fact)} от продаж).")
    return out


@screen("ai", module=M)
def ai(ctx):
    v = View("🤖 Аналитик", "главное сейчас")
    v.lines([f"{n}. {t}" for n, t in enumerate(insights(ctx), 1)])
    v.note("Выводы по цифрам бота. Разбор языковой моделью подключается отдельно.")
    return Reply(view=v, buttons=[[Button("🏠 Главная", "home")]])
