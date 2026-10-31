"""Модуль «ГСМ водителей»: сколько топлива положено по километрам рейсов и сколько ушло по топливной карте.

  положено, л  = пробег за месяц (рейсы 1С) × норма водителя ÷ 100
  положено, ₽  = литры × цена литра (fuel.price в анкете)
  разница      = карта − положено: плюс — перерасход, минус — экономия
Норма — у каждого водителя своя (по машине), по умолчанию fuel.norm из анкеты. Сумму по топливной карте за месяц
вписывают диспетчер, начальник логистики, бухгалтер или директор. В зарплату не входит — это справка для разговора.
"""
from core.calendar import month_title
from core.month import cycle
from core.ui.kit import Button, Reply, money, num, pct, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Cell, Row, View

M = "fuel"


def ok(ctx):
    return ctx.role in ("owner", "admin", "accountant") or (ctx.role == "head" and ctx.dept_kind == "logistics") \
        or ctx.duty == "dispatcher"


menu("fu", "⛽ ГСМ водителей", {"owner", "admin", "accountant", "head", "staff"}, module=M, order=34, when=ok)


def settings(ctx):
    c = ctx.cfg.raw.get("fuel") or {}
    return float(str(c.get("norm", 12)).replace(",", ".")), float(c.get("price", 60))


def norms(db):
    return {int(k): float(v) for k, v in (db.get("fuel_norms") or {}).items()}


def cards(db, month):
    return {int(k): float(v) for k, v in (db.get(f"fuel_card:{month}") or {}).items()}


def rows_of(ctx):
    from modules.logistics import _drivers, trips
    norm0, price = settings(ctx)
    t = trips(ctx)
    ns, cs = norms(ctx.db), cards(ctx.db, ctx.month)
    out = []
    for e in _drivers(ctx):
        km = t.totals(e["id"])["km"]
        n = ns.get(e["id"], norm0)
        liters = km * n / 100
        due = liters * price
        card = cs.get(e["id"])
        out.append({"emp": e, "km": km, "norm": n, "liters": liters, "due": due, "card": card,
                    "diff": (card - due) if card is not None else None})
    return out


@screen("fu", module=M)
def fuel(ctx):
    if not ok(ctx):
        return Reply(text="ГСМ видят логистика, бухгалтер и директор.")
    norm0, price = settings(ctx)
    rs = rows_of(ctx)
    due = sum(r["due"] for r in rs)
    card = sum(r["card"] for r in rs if r["card"] is not None)
    over = [r for r in rs if r["diff"] is not None and r["diff"] > r["due"] * 0.05]
    v = View("⛽ ГСМ водителей", f"{month_title(ctx.month)} · литр {money(price)} · норма по умолчанию {num(norm0)} л/100 км")
    v.tiles([("Положено", money(due), None), ("По картам", money(card) if card else "—", None),
             ("Разница", (("+" if card - due > 0 else "") + money(card - due)) if card else "—",
              "bad" if card and card - due > due * 0.05 else "good" if card else None),
             ("Перерасход > 5%", str(len(over)), "bad" if over else "good")])
    v.table(["Водитель", "Км", "Положено", "Карта", "Разница"],
            [Row([short(r["emp"]["fio"]), num(round(r["km"])), money(r["due"], ""),
                  money(r["card"], "") if r["card"] is not None else Cell("не внесена", "warn"),
                  Cell(("+" if r["diff"] > 0 else "") + money(r["diff"], ""), "bad" if r["diff"] > r["due"] * 0.05 else "good")
                  if r["diff"] is not None else ""],
                 sub=f"норма {num(r['norm'])} л · положено {num(round(r['liters']))} л"
                 + (f" · {'+' if r['diff'] > 0 else ''}{pct(r['diff'] / r['due'])}" if r["diff"] is not None and r["due"] else ""))
             for r in rs])
    v.note("Плюс — по карте ушло больше положенного: повод спросить водителя (заправки, маршрут, машина). "
           "В зарплату не входит.")
    kb = []
    if cycle.status(ctx.db, ctx.month) != "approved":
        kb = rows([Button(f"⛽ {short(r['emp']['fio'])}", f"fue:{r['emp']['id']}") for r in rs], 3)
    kb.append([Button("‹ Рейсы", "lg")] if ctx.can("logistics") else [])
    return Reply(view=v, buttons=[r for r in kb if r])


@screen("fue", module=M)
def edit(ctx, emp_id):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    e = ctx.db.employee(int(emp_id))
    norm0, _ = settings(ctx)
    n = norms(ctx.db).get(e["id"], norm0)
    c = cards(ctx.db, ctx.month).get(e["id"])
    return Reply(text=f"⛽ {e['fio']}\nНорма: {num(n)} л/100 км · карта за {month_title(ctx.month).lower()}: "
                      f"{money(c) if c is not None else 'не внесена'}",
                 buttons=[[Button("💳 Сумма по карте", f"fuc:{e['id']}"), Button("📏 Норма", f"fun:{e['id']}")],
                          [Button("‹ ГСМ", "fu")]])


@screen("fuc", module=M)
def ask_card(ctx, emp_id):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    return ctx.ask("fuel_card", "Сколько ушло по топливной карте за месяц, ₽? Например: 38 500", {"emp": int(emp_id)})


@screen("fun", module=M)
def ask_norm(ctx, emp_id):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    return ctx.ask("fuel_norm", "Норма расхода, литров на 100 км? Например: 12,5", {"emp": int(emp_id)})


def _num(text):
    try:
        return float((text or "").replace(" ", "").replace(" ", "").replace("₽", "").replace(",", "."))
    except ValueError:
        return None


@text_input("fuel_card")
def on_card(ctx, text, data):
    x = _num(text)
    if x is None or x < 0 or x > 10_000_000:
        return ctx.ask("fuel_card", "Напишите сумму числом, например 38 500.", data)
    key = f"fuel_card:{ctx.month}"
    cs = ctx.db.get(key) or {}
    cs[str(data["emp"])] = x
    ctx.db.put(key, cs)
    ctx.db.audit(ctx.author(), "fuel_card", f"{ctx.db.employee(data['emp'])['fio']}: {money(x)}")
    return fuel(ctx)


@text_input("fuel_norm")
def on_norm(ctx, text, data):
    x = _num(text)
    if x is None or not 3 <= x <= 60:
        return ctx.ask("fuel_norm", "Норма — от 3 до 60 литров на 100 км, например 12,5.", data)
    ns = ctx.db.get("fuel_norms") or {}
    ns[str(data["emp"])] = x
    ctx.db.put("fuel_norms", ns)
    ctx.db.audit(ctx.author(), "fuel_norm", f"{ctx.db.employee(data['emp'])['fio']}: {num(x)} л/100 км")
    return fuel(ctx)


# ---------- единицы для зарплаты: экономия / перерасход топлива, ₽ ----------

def _fuel_diff(pay, emp):
    """Карта − положено по норме, ₽ (плюс — перерасход). Нет суммы по карте — None."""
    from modules.logistics import Trips
    c = pay.cfg.raw.get("fuel") or {}
    norm0, price = float(str(c.get("norm", 12)).replace(",", ".")), float(c.get("price", 60))
    card = cards(pay.db, pay.kpi.month).get(emp["id"])
    if card is None:
        return None
    t = pay.__dict__.get("_trips") or Trips(pay.db, pay.kpi.month, pay.kpi.who)
    pay._trips = t
    km = t.totals(emp["id"])["km"]
    return card - km * norms(pay.db).get(emp["id"], norm0) / 100 * price


from core.pay import components as _C  # noqa: E402

_C.unit("fuel_save", lambda pay, emp: max(-(_fuel_diff(pay, emp) or 0), 0))
_C.unit("fuel_over", lambda pay, emp: max(_fuel_diff(pay, emp) or 0, 0))
