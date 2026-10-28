"""Модуль «Логистика»: рейсы водителей по маршрутным листам 1С — пробег, вес, объём, точки доставки;
двойные заезды (один клиент в один день в двух рейсах); рейтинг водителей; сдельная оплата.

Сдельная оплата — кубиком зарплаты per_unit в анкете клиента, единицы считает этот модуль:
  km       пробег за месяц          kg / m3  вес / объём за месяц
  points   точки доставки (клиент в рейсе)   trips   рейсы
Например водителю: {type: per_unit, unit: km, price: "4"}, {type: per_unit, unit: points, price: "25"}.
"""
from collections import defaultdict
from datetime import date

from core.calendar import day_title, month_title
from core.pay import components as C
from core.ui.kit import Button, Reply, num, rows
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, Row, View

M = "logistics"


def leader(ctx):
    return ctx.role in ("owner", "admin") or (ctx.role == "head" and ctx.dept_kind == "logistics")


menu("lg", "🚚 Рейсы", {"owner", "admin", "head"}, module=M, order=31, when=leader)
menu("lgd", "🚚 Мои рейсы", {"staff"}, module=M, order=31, when=lambda c: c.dept_kind == "logistics")


class Trips:
    """Рейсы месяца по водителям (ключи 1С переведены в сотрудников)."""

    def __init__(self, db, month, keymap_who):
        from core.data.db import month_bounds
        s, e = month_bounds(month)
        self.trips = defaultdict(dict)            # водитель → {(день, рейс): {...}}
        self.unknown = set()
        for r in db.q("SELECT * FROM trips WHERE day BETWEEN ? AND ?", s, e):
            emp = keymap_who(r["emp_key"], r["day"])
            if emp is None:
                self.unknown.add(r["emp_key"])
                continue
            self.trips[emp][(r["day"], r["trip"])] = {"km": r["km"], "kg": r["kg"], "m3": r["m3"], "stops": set()}
        self.stops = defaultdict(list)            # (день, клиент) → [(водитель, рейс)]
        for r in db.q("SELECT * FROM trip_stops WHERE day BETWEEN ? AND ?", s, e):
            emp = keymap_who(r["emp_key"], r["day"])
            if emp is None:
                continue
            t = self.trips[emp].get((r["day"], r["trip"]))
            if t is not None:
                t["stops"].add(r["client"])
            pair = (emp, r["trip"])
            if pair not in self.stops[(r["day"], r["client"])]:
                self.stops[(r["day"], r["client"])].append(pair)

    def totals(self, emp):
        ts = self.trips.get(emp, {})
        return {"trips": len(ts), "km": sum(t["km"] for t in ts.values()), "kg": sum(t["kg"] for t in ts.values()),
                "m3": sum(t["m3"] for t in ts.values()), "points": sum(len(t["stops"]) for t in ts.values()),
                "days": len({d for d, _ in ts})}

    def doubles(self):
        return [(d, c, v) for (d, c), v in sorted(self.stops.items()) if len({t for _, t in v}) > 1]


def trips(ctx, month=None):
    month = month or ctx.month
    return ctx.app.memo(ctx, "trips", lambda: Trips(ctx.db, month, ctx.kpi(month).who), month)


# ---------- единицы для сдельной оплаты ----------

def _unit(code):
    def fn(pay, emp):
        cache = pay.__dict__.setdefault("_trips", None)
        if cache is None:
            pay._trips = cache = Trips(pay.db, pay.kpi.month, pay.kpi.who)
        return cache.totals(emp["id"])[code]
    return fn


for _code in ("km", "kg", "m3", "points", "trips"):
    C.unit(_code, _unit(_code))


# ---------- экраны ----------

@screen("lg", module=M)
def overview(ctx):
    if not leader(ctx):
        return driver(ctx)
    t = trips(ctx)
    drivers = sorted(t.trips, key=lambda e: -t.totals(e)["points"])
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    tot = defaultdict(float)
    rs = []
    for e in drivers:
        x = t.totals(e)
        for k_, val in x.items():
            tot[k_] += val
        rs.append(Row([short(names.get(e, "?")), str(x["trips"]), num(round(x["km"])), num(round(x["kg"])), str(x["points"])],
                      sub=f"дней в рейсе {x['days']} · {num(round(x['points'] / x['trips'], 1)) if x['trips'] else 0} точек за рейс"))
    v = View("🚚 Рейсы водителей", month_title(ctx.month))
    v.tiles([("Рейсов", num(tot["trips"]), None), ("Пробег, км", num(round(tot["km"])), None),
             ("Точек доставки", num(tot["points"]), None), ("Двойные заезды", str(len(t.doubles())),
                                                            "warn" if t.doubles() else "good")])
    v.table(["Водитель", "Рейсов", "Км", "Кг", "Точек"], rs, title="Рейтинг по точкам доставки") if rs else \
        v.note("Рейсов в этом месяце нет — пришлите отчёт 1С с маршрутными листами.")
    if t.unknown:
        v.note("Водители из 1С, которых нет в списке сотрудников: " + ", ".join(sorted(t.unknown)[:4]))
    kb = rows([Button(short(names.get(e, "?")), f"lgd:{e}") for e in drivers], 3)
    kb.append([Button(f"🔁 Двойные заезды ({len(t.doubles())})", "lgx")])
    return Reply(view=v, buttons=kb)


@screen("lgd", module=M)
def driver(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    t = trips(ctx)
    x = t.totals(emp["id"])
    v = View(f"🚚 {emp['fio']}", f"рейсы · {month_title(ctx.month).lower()}")
    v.tiles([("Рейсов", str(x["trips"]), None), ("Пробег, км", num(round(x["km"])), None), ("Точек", str(x["points"]), None),
             ("Вес, кг", num(round(x["kg"])), None)])
    rs = [Row([day_title(date.fromisoformat(d)), f"№{n}", num(round(r["km"])), num(round(r["kg"])), str(len(r["stops"]))])
          for (d, n), r in sorted(t.trips.get(emp["id"], {}).items(), reverse=True)[:25]]
    v.table(["День", "Рейс", "Км", "Кг", "Точек"], rs) if rs else v.note("Рейсов нет.")
    kb = [[Button("💰 Зарплата", f"pay:{emp['id']}")]] if ctx.can("payroll") else []
    if leader(ctx):
        kb.append([Button("‹ Рейсы", "lg")])
    return Reply(view=v, buttons=kb)


@screen("lgx", module=M)
def doubles(ctx):
    if not leader(ctx):
        return driver(ctx)
    t = trips(ctx)
    names = {e["id"]: short(e["fio"]) for e in ctx.db.employees(active=None)}
    ds = t.doubles()
    v = View("🔁 Двойные заезды", f"{month_title(ctx.month)} · клиент в один день в двух и больше рейсах")
    v.cards([Card(ctx.db.client_name(c), day_title(date.fromisoformat(d)),
                  [" · ".join(f"{names.get(e, '?')} рейс №{n}" for e, n in trips_)], color="warn") for d, c, trips_ in ds[-15:]])
    if not ds:
        v.note("Двойных заездов нет 👍")
    else:
        v.note("Обычно это дозаказ после отгрузки или забытая накладная — лишний рейс и бензин.")
    return Reply(view=v, buttons=[[Button("‹ Рейсы", "lg")]])
