"""Модуль «Возвраты»: возвраты от клиентов по причинам — чья ошибка: торгового, склада, водителя, клиента.

Чья ошибка — решает переводчик клиента по комментарию 1С (у каждой фирмы свои слова), в нейтральных данных
это поля fault (rep | warehouse | driver | client | other) и who (ключ виноватого сотрудника).
Нераспознанные (fault пустой) видны администратору — по ним дописывают правила переводчика.

Удержания — кубиком per_unit в анкете клиента, единицы считает этот модуль:
  ret_own  сумма возвратов по вине самого торгового (fault=rep, его клиент), ₽ — rate: "3%", cut: true
  ret_err  сколько раз сотрудник назван виноватым (who), шт. — price: "500", cut: true
"""
from collections import defaultdict
from datetime import date

from core.calendar import day_title, month_title
from core.data.db import month_bounds
from core.kpi.engine import SELLERS
from core.pay import components as C
from core.ui.kit import Button, Reply, money, pct, rows
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "returns"
FAULT = {"rep": "Ошибка торгового", "warehouse": "Ошибка склада", "driver": "Ошибка водителя", "client": "Отказ клиента",
         "other": "Другое (брак, сроки)", "": "Не разобрано"}

menu("rv", "↩️ Возвраты", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=33)


def load(db, month, who):
    s, e = month_bounds(month)
    out = []
    for r in db.q("SELECT * FROM returns WHERE day BETWEEN ? AND ? ORDER BY day", s, e):
        r["rep"] = who(r["emp_key"], r["day"])
        r["guilty"] = who(r["who"], r["day"]) if r["who"] else None
        out.append(r)
    return out


def _cached(pay):
    if getattr(pay, "_returns", None) is None:
        pay._returns = load(pay.db, pay.kpi.month, pay.kpi.who)
    return pay._returns


C.unit("ret_own", lambda pay, emp: sum(r["amount"] for r in _cached(pay) if r["fault"] == "rep" and r["rep"] == emp["id"]))
C.unit("ret_err", lambda pay, emp: sum(1 for r in _cached(pay) if r["guilty"] == emp["id"]))


def _visible(ctx, r):
    if ctx.scope == "all":
        return True
    if ctx.role in SELLERS:
        return ctx.me and r["rep"] == ctx.me["id"]
    if r["rep"] and ctx.sees(r["rep"]):
        return True
    return bool(r["guilty"] and ctx.sees(r["guilty"]))


@screen("rv", module=M)
def returns(ctx, fault=None):
    if ctx.role == "head" and ctx.dept_kind not in ("sales", "warehouse", "logistics") and ctx.scope != "all":
        return Reply(text="Нет доступа.")
    k = ctx.kpi()
    rs = [r for r in load(ctx.db, ctx.month, k.who) if _visible(ctx, r)]
    if fault is not None:
        f = "" if fault == "-" else fault
        rs = [r for r in rs if r["fault"] == f]
    names = {e["id"]: short(e["fio"]) for e in ctx.db.employees(active=None)}
    by = defaultdict(lambda: [0, 0.0])
    for r in rs:
        by[r["fault"]][0] += 1
        by[r["fault"]][1] += r["amount"]
    total = sum(r["amount"] for r in rs)
    sales = sum(k.sales.get(i, 0) for i in ({r["rep"] for r in rs if r["rep"]} if ctx.role in SELLERS else k.sales))
    title = FAULT.get("" if fault == "-" else fault, "") if fault is not None else ""
    v = View(f"↩️ Возвраты{' · ' + title if title else ''}", month_title(ctx.month))
    v.tiles([("Возвратов", str(len(rs)), None), ("На сумму", money(total), None)] +
            ([("От продаж", pct(total / sales, 1), "warn" if sales and total / sales > 0.02 else None)] if sales else []))
    if fault is None:
        v.table(["Чья ошибка", "Шт.", "Сумма"], [Row([FAULT[f], str(by[f][0]), money(by[f][1], "")]) for f in FAULT if by[f][0]],
                title="По причинам")
        if ctx.role not in SELLERS:
            per = defaultdict(lambda: [0, 0.0])
            for r in rs:
                if r["fault"] == "rep" and r["rep"]:
                    per[r["rep"]][0] += 1
                    per[r["rep"]][1] += r["amount"]
                elif r["guilty"]:
                    per[r["guilty"]][0] += 1
            if per:
                v.table(["Кто виноват", "Шт.", "Сумма"], [Row([names.get(i, "?"), str(n), money(s, "") if s else "—"])
                                                          for i, (n, s) in sorted(per.items(), key=lambda t: -t[1][0])[:12]],
                        title="По людям")
    cards = []
    for r in sorted(rs, key=lambda r: r["day"], reverse=True)[:12]:
        who = names.get(r["guilty"]) if r["guilty"] else ""
        lines = [r["reason"]] + ([f"торговый {names.get(r['rep'], '?')}"] if ctx.role not in SELLERS and r["rep"] else [])
        cards.append(Card(ctx.db.client_name(r["client"]), money(r["amount"]),
                          [x for x in lines if x], color="bad" if r["fault"] in ("rep", "warehouse", "driver") else None,
                          note=f"{day_title(date.fromisoformat(r['day']))} · {r['doc']} · {FAULT[r['fault']]}" + (f" · {who}" if who else "")))
    v.cards(cards, title="Последние")
    if not rs:
        v.note("Возвратов нет 👍")
    kb = [[Button(FAULT[f], f"rv:{f or '-'}") for f in fs] for fs in (("rep", "warehouse"), ("driver", "client"), ("other", ""))
          if any(by[f][0] for f in fs)] if fault is None else [[Button("‹ Возвраты", "rv")]]
    return Reply(view=v, buttons=kb)
