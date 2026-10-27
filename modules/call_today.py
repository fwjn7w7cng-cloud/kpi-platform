"""Модуль «Кому позвонить сегодня»: список на день для торгового.

  💵 должники — просрочка дольше 30 дней, крупные сверху
  😴 заснувшие — покупали в каждом из 2 прошлых месяцев, а в этом нет 14+ дней
  🗺 маршрут сегодня — точки маршрута, у которых в этом месяце ещё не было заказа
"""
from datetime import date

from core.calendar import shift_month
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen
from core.ui.views import Card, View

M = "call_today"

menu("call", "📞 Кому позвонить", {"rep", "newbie"}, module=M, order=12)


@screen("call", module=M)
def call(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp) or emp["role"] not in SELLERS:
        return Reply(text="Список есть у торговых.")
    k = ctx.kpi()
    i = emp["id"]
    cards = []
    for r in sorted(k.debts.get(i, []), key=lambda r: -(r["d31_60"] + r["d61_90"] + r["d91"]))[:5]:
        over = r["d31_60"] + r["d61_90"] + r["d91"]
        if over > 0:
            cards.append(_card(ctx, r["client"], "💵 Просрочка", money(over), f"весь долг {money(r['total'])}", "bad"))
    p1, p2 = ctx.kpi(shift_month(ctx.month, -1)), ctx.kpi(shift_month(ctx.month, -2))
    now = k.by_client.get(i, {})
    sleepy = [c for c, v in p1.by_client.get(i, {}).items() if v > 0 and p2.by_client.get(i, {}).get(c, 0) > 0
              and now.get(c, 0) <= 0]
    if (ctx.today - k.cal.start).days >= 14 or not ctx.is_current():
        for c in sorted(sleepy, key=lambda c: -p1.by_client[i][c])[:5]:
            cards.append(_card(ctx, c, "😴 Заснул", money(p1.by_client[i][c]), "покупал в прошлом месяце, в этом — нет", "warn"))
    today = ctx.today.isoformat()
    route = [c for d, c in k.route.get(i, []) if d == today and now.get(c, 0) <= 0]
    for c in route[:6]:
        cards.append(_card(ctx, c, "🗺 Маршрут сегодня", "", "в этом месяце заказа ещё не было", None))
    v = View(f"📞 Кому позвонить · {emp['fio']}", f"на {ctx.today:%d.%m}")
    v.cards(cards)
    if not cards:
        v.note("Сегодня звонить некому — все клиенты в порядке 👍")
    return Reply(view=v, buttons=[[Button("‹ План", f"emp:{i}")]])


def _card(ctx, code, title, value, line, color):
    c = ctx.db.one("SELECT name, town, phone FROM clients WHERE code = ?", code) or {"name": code, "town": "", "phone": ""}
    return Card(f"{title} · {c['name']}", value, [x for x in (c["town"], line) if x], phone=c["phone"], color=color)
