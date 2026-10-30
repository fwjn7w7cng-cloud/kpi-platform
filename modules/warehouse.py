"""Модуль «Склад»: приёмка поставок по заказам закупок (пришло всё / недостача по строкам) и отгрузки —
сколько точек склад собрал по дням и сколько ошибок склада нашлось в возвратах.

Связи: заказы — из модуля «Закупки» (stock_orders, статус «в пути» → «принят» / «принят с недостачей»);
точки — из рейсов логистики (trip_stops); ошибки — из возвратов (fault = warehouse, who = виноватый).
Деньги за ошибки — кубиком per_unit ret_err в схеме зарплаты склада (его считает модуль возвратов).
Видят: склад (кладовщики и заведующий), директор, админ; закупки получают сообщение о недостаче.
"""
import json
from collections import defaultdict
from datetime import date, datetime

from core.calendar import day_title, month_title
from core.data.db import month_bounds
from core.ui.kit import Button, Reply, num, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "warehouse"


def ok(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "warehouse"


menu("wrc", "📥 Приёмка поставок", {"owner", "admin", "head", "staff"}, module=M, order=28, when=ok)
menu("wsh", "📦 Отгрузки и ошибки", {"head", "staff"}, module=M, order=29, when=lambda c: c.dept_kind == "warehouse")


def _order(ctx, oid):
    o = ctx.db.one("SELECT * FROM stock_orders WHERE id = ?", int(oid))
    if o:
        o["items"] = json.loads(o["items"])
        o["extra"] = json.loads(o.get("extra") or "{}")
    return o


def _names(ctx):
    r = ctx.db.one('SELECT MAX("on") AS d FROM stock')
    if not r or not r["d"]:
        return {}
    return {x["code"]: x["name"] for x in ctx.db.q('SELECT code, name FROM stock WHERE "on" = ?', r["d"])}


@screen("wrc", module=M)
def receive(ctx, oid=None):
    if not ok(ctx):
        return Reply(text="Приёмку ведёт склад.")
    if oid:
        return receive_one(ctx, oid)
    going = [_order(ctx, r["id"]) for r in ctx.db.q("SELECT id FROM stock_orders WHERE status = 'sent' ORDER BY sent_ts")]
    done = [_order(ctx, r["id"]) for r in ctx.db.q("SELECT id FROM stock_orders WHERE status IN ('came', 'part') "
                                                  "ORDER BY came_ts DESC LIMIT 6")]
    v = View("📥 Приёмка поставок", f"едет {len(going)} · отправленные поставщику заказы закупок")
    v.cards([Card(f"№{o['id']} · {o['supplier']}", f"позиций {len(o['items'])}",
                  [f"отправлен {_dm(o['sent_ts'])} · оформил {o['author']}"], color="warn") for o in going])
    if not going:
        v.note("Сейчас ничего не едет.")
    if done:
        v.table(["№", "Поставщик", "Принят", "Итог"],
                [Row([str(o["id"]), o["supplier"][:22], _dm(o["came_ts"]),
                      Cell("недостача", "bad") if o["status"] == "part" else Cell("всё", "good")]) for o in done],
                title="Недавно приняты")
    return Reply(view=v, buttons=rows([Button(f"📥 №{o['id']} {o['supplier'][:16]}", f"wrc:{o['id']}") for o in going], 1))


def _dm(iso):
    return day_title(date.fromisoformat(iso[:10])) if iso else "—"


def receive_one(ctx, oid):
    o = _order(ctx, oid)
    if not o:
        return Reply(text="Нет такого заказа.")
    nm = _names(ctx)
    got = dict((c, q) for c, q in o["extra"].get("got", []))
    v = View(f"📥 Поставка №{o['id']}", f"{o['supplier']} · {'едет' if o['status'] == 'sent' else 'принята'}")
    head = ["Товар", "Заказано"] + (["Пришло"] if got else [])
    v.table(head, [Row([nm.get(c, c)[:34], num(q)] + ([Cell(num(got.get(c, q)), "bad" if got.get(c, q) < q else "good")]
                                                      if got else []), sub=f"код {c}") for c, q in o["items"]])
    kb = []
    if o["status"] == "sent":
        v.note("Проверьте товар по строкам. Всё сошлось — «Пришло всё»; нет — впишите, сколько пришло по недостающим.")
        kb = [[Button("✅ Пришло всё", f"wra:{o['id']}"), Button("⚠️ Недостача", f"wrn:{o['id']}")]]
    kb.append([Button("‹ Приёмка", "wrc")])
    return Reply(view=v, buttons=kb)


def _close(ctx, o, got):
    short_ = [(c, q, got.get(c, q)) for c, q in o["items"] if got.get(c, q) < q]
    o["extra"]["got"] = [[c, got.get(c, q)] for c, q in o["items"]]
    o["extra"].setdefault("hist", []).append([datetime.now().strftime("%d.%m %H:%M"), ctx.author(),
                                              "принят с недостачей" if short_ else "принят полностью"])
    ctx.db.x("UPDATE stock_orders SET status = ?, came_ts = ?, extra = ? WHERE id = ?", "part" if short_ else "came",
             ctx.today.isoformat(), json.dumps(o["extra"], ensure_ascii=False), o["id"])
    ctx.db.audit(ctx.author(), "stock_receive", f"№{o['id']} {o['supplier']}" + (f", недостача {len(short_)} поз." if short_ else ""))
    out = [Reply(text=f"✅ Поставка №{o['id']} принята" + (f", недостача по {len(short_)} позициям — закупки получили список."
                                                          if short_ else " полностью."))]
    if short_:
        nm = _names(ctx)
        text = (f"⚠️ Поставка №{o['id']} · {o['supplier']} пришла не вся:\n" +
                "\n".join(f"• {nm.get(c, c)[:40]}: заказано {num(q)}, пришло {num(g)}" for c, q, g in short_))
        to = [e for e in ctx.db.employees() if e.get("tg_id") and ctx.cfg.dept(e["dept"])
              and ctx.cfg.dept(e["dept"]).kind == "purchasing" and (e["role"] == "head" or e["id"] == o.get("emp_id"))]
        out += [Reply(text=text, to=e["tg_id"], buttons=[[Button("🛒 Заказ", f"sko:{o['id']}")]]) for e in to]
    out.append(receive(ctx))
    return out


@screen("wra", module=M)
def all_came(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o or o["status"] != "sent":
        return Reply(text="Эта поставка уже принята.")
    return _close(ctx, o, {})


@screen("wrn", module=M)
def shortage(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o or o["status"] != "sent":
        return Reply(text="Эта поставка уже принята.")
    ex = o["items"][0][0] if o["items"] else "100012"
    return ctx.ask("wh_short", f"Напишите строками «код сколько пришло» — только то, чего не хватает:\n\n{ex} 30\n\n"
                               "Остальное бот примет полностью.", {"id": o["id"]})


@text_input("wh_short")
def on_short(ctx, text, data):
    o = _order(ctx, data["id"])
    if not o or o["status"] != "sent":
        return Reply(text="Эта поставка уже принята.")
    codes = {c for c, _ in o["items"]}
    got = {}
    for line in (text or "").splitlines():
        p = line.replace(",", ".").split()
        if not p:
            continue
        if p[0] not in codes:
            return ctx.ask("wh_short", f"Кода {p[0]} нет в этой поставке. Пишите «код сколько пришло».", data)
        try:
            got[p[0]] = max(float(p[1]), 0) if len(p) > 1 else None
        except ValueError:
            got[p[0]] = None
        if got[p[0]] is None:
            return ctx.ask("wh_short", f"В строке «{line.strip()}» нет количества.", data)
    if not got:
        return ctx.ask("wh_short", "Напишите «код сколько пришло».", data)
    return _close(ctx, o, got)


# ---------- отгрузки и ошибки ----------

@screen("wsh", module=M)
def shipments(ctx):
    if not ok(ctx):
        return Reply(text="Отгрузки видит склад.")
    s, e = month_bounds(ctx.month)
    by_day = defaultdict(set)
    for r in ctx.db.q("SELECT day, trip, client FROM trip_stops WHERE day BETWEEN ? AND ?", s, e):
        by_day[r["day"]].add((r["trip"], r["client"]))
    errs = ctx.db.q("SELECT * FROM returns WHERE day BETWEEN ? AND ? AND fault = 'warehouse' ORDER BY day DESC", s, e)
    who = ctx.kpi().who
    team = [x for x in ctx.db.employees() if ctx.cfg.dept(x["dept"]) and ctx.cfg.dept(x["dept"]).kind == "warehouse"]
    per = defaultdict(int)
    for r in errs:
        g = who(r["who"], r["day"]) if r["who"] else None
        per[g] += 1
    total = sum(len(v) for v in by_day.values())
    v = View("📦 Отгрузки и ошибки склада", month_title(ctx.month))
    v.tiles([("Отгрузок (точек)", num(total), None), ("В день", num(round(total / len(by_day))) if by_day else "—", None),
             ("Ошибок склада", str(len(errs)), "bad" if errs else "good")])
    v.table(["День", "Рейсов", "Точек"], [Row([day_title(date.fromisoformat(d)), str(len({t for t, _ in x})), str(len(x))])
                                          for d, x in sorted(by_day.items(), reverse=True)[:10]], title="По дням")
    mine = ctx.me if ctx.role == "staff" else None
    rs = [Row([short(x["fio"]), Cell(str(per.get(x["id"], 0)), "bad" if per.get(x["id"]) else "good")],
              mark="good" if mine and x["id"] == mine["id"] else None) for x in team]
    if per.get(None):
        rs.append(Row(["без фамилии", str(per[None])]))
    v.table(["Кладовщик", "Ошибок"], rs, title="Ошибки в возвратах (каждая режет бонус)")
    v.cards([Card(ctx.db.client_name(r["client"]), day_title(date.fromisoformat(r["day"])), [r["reason"]], color="bad")
             for r in errs[:5]], title="Последние ошибки")
    return Reply(view=v, buttons=[[Button("📥 Приёмка", "wrc")]])
