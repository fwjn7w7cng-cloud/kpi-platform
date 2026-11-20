"""🚚 Учёт в Алапе (только без 1С; Адам 20.11): остатки по местам, резерв, погрузка, маршрутный лист, перемещения.

Как устроено (по словам Адама: «начальника логистики нет — менеджер в заказе назначает водителя, маршрутный лист формируется
после отгрузки: что погрузили в машину, то и встаёт в маршрут»):
  - остатки по местам — таблица stock_loc (код, место, есть, образцов): место «wh» — склад, остальные — магазины (команды
    отдела продавцов); образец стоит в зале и не продаётся; goods.qty — всего по фирме;
  - продажа «сам забирает» списывает из салона сразу; с доставкой — резерв (заказ со строками lines [[код, шт, откуда]]),
    водителя выбирает менеджер (driver) или позже («🚚 Сменить водителя»);
  - кладовщик в «📤 Погрузке» по водителям: ✓ погрузил / ✕ не погрузил (не влез, нет товара) → «🚚 Машина ушла» —
    маршрутный лист (route_sheets) только из погруженного, товар списывается с места, менеджеру — что не погрузили;
    рейс и точки ложатся в trips / trip_stops (batch 0) — сдельная водителя считается как по листам 1С;
  - водитель в «🗺 Мой лист»: точки по порядку (⬆ выше), «Доставил» / «Не доставил» (товар обратно на место, снова в резерв);
  - перемещение склад → салон (для продажи или образцом): заявка директора магазина → «Отгрузить» на складе (в пути) →
    «Принять» в салоне с пересчётом (moves).
Кто видит: погрузка — склад, владелец, админ; лист — водитель свой, остальные (склад, менеджеры, владелец) — смотреть;
перемещения — склад, директора магазинов (свой), владелец; остатки по местам — владелец, админ, бухгалтер (себестоимость 🔒).
У фирм на 1С ничего из этого нет: маршрутные листы приходят из 1С (modules/logistics.py).
"""
import json
from datetime import date, datetime, timedelta

from core.ui import retail as RT
from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen
from core.ui.views import View
from modules import acc as A

M = "stock"
WH = "wh"
LOAD_T = {"": ("погрузить", ""), "on": ("✓ погружено", "good"), "no": ("✕ не погружен", "bad")}


# ======================= места и остатки =======================

def has_locs(db):
    return bool(db.one("SELECT 1 AS x FROM stock_loc LIMIT 1"))


def active(ctx):
    """Учёт по местам включён: есть остатки по местам, а в демо — сегмент не «опт»."""
    return getattr(ctx, "segment", "mix") != "opt" and has_locs(ctx.db)


WH_KEY = "warehouses"     # склады, добавленные в приложении (загрузка остатков из Excel): [[id, название]]
WH_FULL = "warehouses_full"   # склады переименовывали / убирали в приложении — тогда весь список в WH_KEY, анкета не читается


def _cfg(src):
    return getattr(src, "cfg", src)


def whs(src):
    """Склады учёта (Адам 20.11: у сети их несколько): из анкеты (warehouses) + добавленные в приложении; нет — один «Склад».
    src — ctx (с базой) или анкета."""
    out = [tuple(w) for w in (getattr(_cfg(src), "warehouses", None) or [])]
    db = getattr(src, "db", None)
    if db is not None and db.get(WH_FULL):                       # склады правили в приложении — список целиком в базе
        return [tuple(w) for w in (db.get(WH_KEY) or [])] or [(WH, "Склад")]
    if db is not None:
        have = {w for w, _ in out}
        out += [tuple(w) for w in (db.get(WH_KEY) or []) if w[0] not in have]
    return out or [(WH, "Склад")]


def set_whs(ctx, items):
    """Записать весь список складов (справочники, modules/catalog.py)."""
    ctx.db.put(WH_KEY, [list(w) for w in items])
    ctx.db.put(WH_FULL, True)


def add_wh(db, name):
    """Новый склад из приложения → его id (тот же, если склад с таким названием уже есть)."""
    ws = db.get(WH_KEY) or []
    for w, n in ws:
        if n.lower() == name.lower():
            return w
    ids, n = {w for w, _ in ws}, len(ws) + 1
    while f"w{n}" in ids:
        n += 1
    wid = f"w{n}"
    db.put(WH_KEY, ws + [[wid, name[:40]]])
    return wid


def wh_ids(src):
    return {w for w, _ in whs(src)}


def main_wh(src):
    return whs(src)[0][0]


def locs(src):
    return whs(src) + RT.shops(_cfg(src))


def free_wh(db, cfg, code, res=None):
    """Свободно по всем складам вместе."""
    res = reserved(db) if res is None else res
    return sum(free(db, code, w, res) for w in wh_ids(cfg))


def pick_wh(db, cfg, code, n, res=None):
    """Склад, где свободно не меньше n (первый по списку), или None."""
    res = reserved(db) if res is None else res
    return next((w for w, _ in whs(cfg) if free(db, code, w, res) >= n), None)


def loc_name(cfg, loc):
    return dict(locs(cfg)).get(loc, loc)


def stock_at(db, code, loc):
    r = db.one("SELECT qty, smp FROM stock_loc WHERE code = ? AND loc = ?", code, loc)
    return (r["qty"], r["smp"]) if r else (0, 0)


def ship_orders(db):
    """Заказы учёта в Алапе (со строками), ещё не уехавшие и не отменённые."""
    return [o for o in RT.orders(db).get("items", []) if o.get("lines") and not o.get("off")]


def reserved(db):
    """(код, место) → штук в резерве: проданы с доставкой, ещё не погружены в машину."""
    out = {}
    for o in ship_orders(db):
        if o.get("load") == "gone":
            continue
        for c, n, src in o["lines"]:
            out[(c, src)] = out.get((c, src), 0) + n
    return out


def free(db, code, loc, res=None):
    q, s = stock_at(db, code, loc)
    res = reserved(db) if res is None else res
    return max(q - s - res.get((code, loc), 0), 0)


def move_qty(db, code, loc, delta, smp=0, total=True):
    """Изменить остаток на месте (и всего по фирме — goods.qty, если total)."""
    db.x("INSERT INTO stock_loc (code, loc, qty, smp) VALUES (?,?,?,?) ON CONFLICT(code, loc) DO UPDATE SET qty = qty + ?, smp = smp + ?",
         code, loc, delta, smp, delta, smp)
    if total:
        db.x("UPDATE goods SET qty = qty + ? WHERE code = ?", delta, code)


def loc_rows(ctx, code):
    """Таблица «Где лежит» для карточки товара: есть / образец / резерв / свободно."""
    res = reserved(ctx.db)
    rows = []
    for loc, name in locs(ctx):
        q, s = stock_at(ctx.db, code, loc)
        r = res.get((code, loc), 0)
        if not q and not r and loc not in wh_ids(ctx):
            continue
        f = max(q - s - r, 0)
        rows.append({"n": name + (" · образец" if s else ""), "cells": [[A._num(q), ""], [A._num(r) if r else "—", ""],
                                                                          [A._num(f), "good" if f else "bad"]], "det": ""})
    return rows


def free_total(db, code, res=None):
    res = reserved(db) if res is None else res
    return sum(free(db, code, loc, res) for loc in [r["loc"] for r in db.q("SELECT loc FROM stock_loc WHERE code = ?", code)])


# ======================= водители и заказы =======================

def drivers(ctx):
    return [e for e in ctx.db.employees() if (ctx.cfg.position(e["position"]) or {}).get("duty") == "driver"]


def driver_load(db, emp_id):
    return sum(1 for o in ship_orders(db) if o.get("driver") == emp_id and o.get("load") != "gone" and o["stage"] < 2)


def _short(db, emp_id):
    e = db.employee(emp_id) if emp_id else None
    return RT._short(e["fio"]) if e else "без водителя"


def _save_order(db, o):
    od = RT.orders(db)
    od["items"] = [o if x["id"] == o["id"] else x for x in od.get("items", [])]
    db.put("rt_orders", od)


def _order(db, oid):
    return next((o for o in RT.orders(db).get("items", []) if str(o["id"]) == str(oid)), None)


def new_steps(o):
    """Дорога заказа учёта в Алапе: Заказ → Погружен → Доставлен (→ Сборка)."""
    st = ["Заказ", "Погружен" if o.get("load") != "no" else "Не погружен", "Доставлен"] + (["Сборка"] if o.get("need_asm") else [])
    if o.get("load") == "gone" and o.get("dlv") != "ok":
        st[1], cur = "В пути", 1
    elif o.get("dlv") == "ok":
        cur = 3 if o.get("need_asm") and o["stage"] < 4 else len(st)
    else:
        cur = 1 if o.get("load") == "no" else 0
    return [[t, "done" if i < cur else "cur" if i == cur else ""] for i, t in enumerate(st)]


def order_note(db, o):
    if o.get("load") == "no":
        return f" · не погружен: {o.get('no_why') or 'причина не указана'} · {_short(db, o.get('driver'))}"
    if o.get("load") == "gone":
        return f" · лист №{o.get('sheet')} · {_short(db, o.get('driver'))}" + (" · доставлен" if o.get("dlv") == "ok" else "")
    return f" · водитель: {_short(db, o.get('driver'))}"


def can_manage_order(ctx, o):
    return ctx.role in ("owner", "admin") or (ctx.me and (o.get("seller") == ctx.me["id"] or
                                                          (ctx.duty == "shop" and ctx.me["team"] == o.get("shop"))))


@screen("rdv", module=M)
def reassign(ctx, oid=None, drv=None):
    """Сменить водителя заказу (менеджер, директор магазина, владелец)."""
    o = _order(ctx.db, oid)
    if not o or not o.get("lines") or o.get("load") == "gone" or not can_manage_order(ctx, o):
        return RT.orders_screen(ctx, "me" if ctx.duty == "seller" else None)
    if drv is not None:
        o["driver"] = int(drv) if str(drv).isdigit() and int(drv) else None
        o["load"], o["no_why"] = "", ""
        _save_order(ctx.db, o)
        return RT.orders_screen(ctx, "me" if ctx.duty == "seller" else None)
    v = View(f"🚚 Заказ №{o['id']}", f"{o['item']} · {o['client']}")
    v.ui("rows", {"items": [{"t": o["item"], "s": f"доставка {RT._dm(o['deliv'])}" + order_note(ctx.db, o), "steps": new_steps(o)}]}, "")
    v.ui("choice", {"items": [[f"🚚 {RT._short(e['fio'])} · {driver_load(ctx.db, e['id'])}", f"rdv:{o['id']}:{e['id']}",
                               o.get("driver") == e["id"]] for e in drivers(ctx)] + [["Пока без водителя", f"rdv:{o['id']}:0", not o.get("driver")]]},
         "Кто везёт · сколько у него уже заказов")
    return Reply(view=v)


# ======================= 📤 погрузка =======================

def keeper(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "warehouse"


menu("ld", "📤 Погрузка", {"owner", "admin", "head", "staff"}, module=M, order=26, when=lambda c: keeper(c) and active(c))
menu("rs", "🗺 Мой маршрутный лист", {"staff"}, module=M, order=29, when=lambda c: c.duty == "driver" and active(c))
menu("mv", "🔁 Перемещения", {"owner", "admin", "head", "staff", "sv"}, module=M, order=27,
     when=lambda c: active(c) and (keeper(c) or c.duty == "shop"))
menu("stl", "📍 Остатки по местам", {"owner", "admin", "accountant"}, module=M, order=27, when=active)


def to_load(ctx, today=None):
    t = (today or ctx.today).isoformat()
    return [o for o in ship_orders(ctx.db) if o.get("load") != "gone" and o["stage"] < 2 and o["deliv"] <= t]


@screen("ld", module=M)
def loading(ctx, act=None, drv=None, oid=None):
    if not keeper(ctx) or not has_locs(ctx.db):
        return Reply(text="Погрузку ведёт склад.")
    if act == "t" and oid:
        o = _order(ctx.db, oid)
        if o and o.get("load") != "gone":
            o["load"] = {"": "on", "on": "no", "no": ""}[o.get("load", "")]
            o["no_why"] = "не влез или нет товара" if o["load"] == "no" else ""
            _save_order(ctx.db, o)
    if act == "go":
        return truck_gone(ctx, drv)
    ds = drivers(ctx)
    orders = to_load(ctx)
    cur = int(drv) if drv and str(drv).isdigit() else (next((e["id"] for e in ds if any(o.get("driver") == e["id"] for o in orders)), 0))
    v = View("📤 Погрузка", f"сегодня · {len(orders)} заказов")
    tabs = [[f"🚚 {RT._short(e['fio'])} · {sum(1 for o in orders if o.get('driver') == e['id'])}", f"ld:d:{e['id']}", cur == e["id"]] for e in ds]
    nod = [o for o in orders if not o.get("driver")]
    if nod:
        tabs.append([f"Без водителя · {len(nod)}", "ld:d:0", cur == 0])
    v.ui("choice", {"items": tabs}, "")
    mine = [o for o in orders if (o.get("driver") or 0) == cur]
    names = A._names(ctx.db)
    items = []
    for o in mine:
        lab, tone = LOAD_T.get(o.get("load", ""), LOAD_T[""])
        what = ", ".join(f"{names.get(c, c)} × {A._num(n)} ({loc_name(ctx, src).lower() if src in wh_ids(ctx) else loc_name(ctx, src)})" for c, n, src in o["lines"])
        items.append({"t": f"№{o['id']} · {o['client']}", "s": f"{what} · менеджер {_short(ctx.db, o.get('seller'))}"
                      + (f" · {o.get('no_why')}" if o.get("load") == "no" else ""),
                      "chip": [lab, f"ld:t:{cur}:{o['id']}" if cur else "", tone]})
    on = [o for o in mine if o.get("load") == "on"]
    foot = [[f"🚚 Машина ушла — маршрутный лист из {len(on)}", f"ld:go:{cur}"]] if cur and on else []
    v.ui("rows", {"items": items, "foot": foot, "right": "нажмите: погрузить → ✓ → ✕",
                  "empty": "Этому водителю грузить нечего" if cur else "Заказов без водителя нет"},
         "Без водителя — назначит менеджер" if not cur else "Заказы этого водителя")
    mv = ctx.db.q("SELECT * FROM moves WHERE status = 'ask' ORDER BY id")
    if mv:
        v.ui("rows", {"items": [{"t": f"→ {loc_name(ctx, m['loc'])} · перемещение №{m['id']}", "s": _lines_txt(ctx, m),
                                 "chip": ["отгрузить", f"mvo:{m['id']}", "warn"]} for m in mv]}, "В салоны")
    v.ui("rows", {"items": [], "empty": "✓ — положили в машину, ✕ — не влез или нет товара (останется на складе в резерве, менеджеру "
                                        "уведомление). «Машина ушла» — маршрутный лист только из погруженного, товар спишется со склада."}, "")
    return Reply(view=v)


def truck_gone(ctx, drv):
    if not drv or not str(drv).isdigit() or not ctx.db.employee(int(drv)):
        return loading(ctx)
    drv = int(drv)
    orders = [o for o in to_load(ctx) if o.get("driver") == drv]
    on = [o for o in orders if o.get("load") == "on"]
    if not on:
        return loading(ctx, "d", drv)
    now = datetime.now()
    sid = ctx.db.x("INSERT INTO route_sheets (day, driver, ts_out, orders, by) VALUES (?,?,?,?,?)", ctx.today.isoformat(), drv,
                   now.isoformat(timespec="seconds"), json.dumps([o["id"] for o in on]), A._who(ctx))
    key = (ctx.db.one("SELECT key FROM emp_keys WHERE emp_id = ? ORDER BY since DESC LIMIT 1", drv) or {}).get("key", f"emp{drv}")
    out = []
    for o in on:
        for c, n, src in o["lines"]:
            move_qty(ctx.db, c, src, -n)
        o.update(load="gone", stage=2, sheet=sid, loaded_ts=now.strftime("%H:%M"))
        _save_order(ctx.db, o)
        ctx.db.x("INSERT INTO trip_stops (batch, day, emp_key, trip, client, doc) VALUES (0,?,?,?,?,?)",
                 ctx.today.isoformat(), key, f"Л-{sid}", f"Z{o['id']}", f"№{o['id']}")
    ctx.db.x("INSERT INTO trips (batch, day, emp_key, trip, km, kg, m3) VALUES (0,?,?,?,0,0,0)", ctx.today.isoformat(), key, f"Л-{sid}")
    for o in orders:
        if o.get("load") == "no":
            out += _tell(ctx, o.get("seller"), f"🚚 Заказ №{o['id']} ({o['client']}) не погрузили в машину {_short(ctx.db, drv)}: "
                                               f"{o.get('no_why')}. Можно отдать другому водителю.", f"rdv:{o['id']}")
    out += _tell(ctx, drv, f"🗺 Маршрутный лист №{sid}: {len(on)} точек. Откройте «Мой маршрутный лист».", f"rs:{sid}")
    return [sheet(ctx, sid)] + out


def _tell(ctx, emp_id, text, to):
    e = ctx.db.employee(emp_id) if emp_id else None
    if e and e.get("tg_id"):
        return [Reply(text=text, buttons=[[Button("Открыть", to)]], to=e["tg_id"])]
    return []


# ======================= 🗺 маршрутный лист =======================

@screen("rs", module=M)
def sheet(ctx, sid=None, act=None, oid=None):
    db = ctx.db
    if sid and str(sid).isdigit():
        s = db.one("SELECT * FROM route_sheets WHERE id = ?", int(sid))
    elif ctx.me and ctx.duty == "driver":
        s = db.one("SELECT * FROM route_sheets WHERE driver = ? ORDER BY (status = 'open') DESC, id DESC LIMIT 1", ctx.me["id"])
    else:
        s = None
    if not s:
        return Reply(text="🗺 Маршрутного листа пока нет: он появится, когда склад погрузит машину и нажмёт «Машина ушла».")
    mine = bool(ctx.me) and ctx.me["id"] == s["driver"]
    if not (mine or keeper(ctx) or ctx.role in ("owner", "admin") or ctx.duty in ("seller", "shop")):
        return Reply(text="Маршрутный лист видит водитель, склад, менеджеры и владелец.")
    ids = json.loads(s["orders"])
    if act and oid and (mine or ctx.role in ("owner", "admin")) and s["status"] == "open":
        o = _order(db, oid)
        if o and o.get("sheet") == s["id"] and not o.get("dlv"):
            if act == "up" and o["id"] in ids and ids.index(o["id"]) > 0:
                i = ids.index(o["id"])
                ids[i - 1], ids[i] = ids[i], ids[i - 1]
                db.x("UPDATE route_sheets SET orders = ? WHERE id = ?", json.dumps(ids), s["id"])
            elif act == "ok":
                o.update(dlv="ok", dlv_ts=datetime.now().strftime("%H:%M"),
                         stage=3 if o.get("need_asm") and RT.orders(db).get("asm_word") else 4)
                _save_order(db, o)
            elif act == "no":
                for c, n, src in o["lines"]:
                    move_qty(db, c, src, n)                         # обратно на место и снова в резерв
                o.update(load="", stage=0, sheet=None, fail_why="не доставил: покупатель не принял или не было дома",
                         driver=o.get("driver"))
                _save_order(db, o)
        left = [i for i in ids if (_order(db, i) or {}).get("sheet") == s["id"] and not (_order(db, i) or {}).get("dlv")]
        if not left:
            db.x("UPDATE route_sheets SET status = 'closed' WHERE id = ?", s["id"])
        s = db.one("SELECT * FROM route_sheets WHERE id = ?", s["id"])
    names = A._names(db)
    pts = [o for o in (_order(db, i) for i in ids) if o]
    money_in = sum(o["sum"] - o.get("prepay", 0) for o in pts if o["pay"] == "prepay" and o.get("sheet") == s["id"])
    v = View(f"🗺 Маршрутный лист №{s['id']}", f"{_short(db, s['driver'])} · {RT._dm(s['day'])} · выехал {s['ts_out'][11:16]}")
    v.ui("hero", {"label": f"Погружено на складе · {s['by']}", "big": f"{len(pts)} точ{'ка' if len(pts) == 1 else 'ки' if len(pts) < 5 else 'ек'}",
                  "md": True, "id": f"rs{s['id']}",
                  "sub": ("закрыт" if s["status"] == "closed" else "в пути") + (f" · получить с покупателей {money(money_in)}" if money_in else ""),
                  "pairs": [["Доставлено", str(sum(1 for o in pts if o.get("dlv") == "ok")), "good"],
                            ["Осталось", str(sum(1 for o in pts if o.get("sheet") == s["id"] and not o.get("dlv"))), ""]], "acts": []},
         fallback=[f"Лист №{s['id']}: {len(pts)} точек"])
    rows = []
    for k, o in enumerate(pts, 1):
        back = o.get("sheet") != s["id"]
        st = "✓ " + o.get("dlv_ts", "") if o.get("dlv") == "ok" else "✕ вернул" if back else "в пути"
        what = ", ".join(f"{names.get(c, c)} × {A._num(n)}" for c, n, src in o["lines"])
        pay = f" · получить {money(o['sum'] - o.get('prepay', 0))}" if o["pay"] == "prepay" else ""
        acts = []
        if (mine or ctx.role in ("owner", "admin")) and not o.get("dlv") and not back and s["status"] == "open":
            acts = [["✅ Доставил", f"rs:{s['id']}:ok:{o['id']}"], ["✕ Не доставил", f"rs:{s['id']}:no:{o['id']}"]] + \
                ([["⬆ Выше", f"rs:{s['id']}:up:{o['id']}"]] if k > 1 else [])
        rows.append({"n": f"{k} · {o.get('addr') or 'адрес не указан'}", "cells": [[st, "good" if o.get("dlv") == "ok" else "bad" if back else ""]],
                     "det": f"{o['client']} · {what}{pay}" + (" · сборка после" if o.get("need_asm") else ""), "acts": acts})
    v.ui("team", {"head": ["Точка", "Статус"], "rows": rows, "right": "нажмите на точку",
                  "note": "«Не доставил» — товар вернётся на склад и снова встанет в резерв, менеджеру — уведомление."}, "Точки по порядку")
    return Reply(view=v)


# ======================= 🔁 перемещения =======================

def _lines_txt(ctx, m):
    names = A._names(ctx.db)
    return ", ".join(f"{names.get(c, c)} × {A._num(n)}{' (образец)' if smp else ''}" for c, n, smp, *_ in json.loads(m["lines"]))


MV_ST = {"ask": ("заявка", "warn"), "go": ("едет", "blue"), "done": ("принято", "good")}


@screen("mv", module=M)
def moves(ctx):
    if not (keeper(ctx) or ctx.duty == "shop"):
        return Reply(text="Перемещения ведут склад и директора магазинов.")
    q, a = "SELECT * FROM moves", []
    if ctx.duty == "shop":
        q, a = q + " WHERE loc = ?", [ctx.me["team"]]
    rs = ctx.db.q(q + " ORDER BY CASE status WHEN 'ask' THEN 0 WHEN 'go' THEN 1 ELSE 2 END, id DESC LIMIT 30", *a)
    v = View("🔁 Перемещения", "склад → салоны")
    v.ui("rows", {"items": [{"t": f"№{m['id']} · → {loc_name(ctx, m['loc'])}", "s": f"{RT._dm(m['day'])} · {_lines_txt(ctx, m)}",
                             "chip": [MV_ST[m["status"]][0], f"mvo:{m['id']}", MV_ST[m["status"]][1]]} for m in rs],
                  "empty": "Перемещений нет", "foot": [["➕ Заявка: привезти в салон", "mvn"]] if ctx.duty == "shop" else []}, "")
    return Reply(view=v)


@screen("mvn", module=M)
def move_new(ctx, err="", f=None):
    if ctx.duty != "shop":
        return moves(ctx)
    f = f or {}
    v = View("🔁 Заявка на перемещение", f"Склад → {RT.shop_name(ctx.cfg, ctx.me['team'])}")
    v.ui("sform", {"fields": [
        {"k": "lines", "t": "area", "l": "Что привезти — каждый товар с новой строки: название или код; штук", "req": 1, "max": 2000,
         "v": f.get("lines", ""), "ph": "Кресло «Релакс»; 2"},
        {"k": "smp", "t": "seg", "l": "Зачем", "v": f.get("smp", "0"), "opts": [["0", "Для продажи"], ["1", "Образцом в зал"]]}],
        "send": ["📨 Отправить на склад", "mvs"], "err": err, "top": [],
        "note": "Склад отгрузит, вы пересчитаете и примете. Образец в зале не продаётся — по нему показывают."}, "")
    return Reply(view=v, buttons=[[Button("‹ К перемещениям", "mv")]])


@screen("mvs", module=M)
def move_save(ctx):
    if ctx.duty != "shop":
        return moves(ctx)
    f = ctx.form or {}
    goods_ = ctx.db.q("SELECT code, name FROM goods WHERE off = 0")
    out, errs = [], []
    for i, line in enumerate(str(f.get("lines", "")).splitlines()[:40], 1):
        parts = [p.strip() for p in line.split(";")]
        if not line.strip():
            continue
        if len(parts) != 2:
            errs.append(f"строка {i}: товар; штук")
            continue
        name = parts[0].lower()
        hit = [g for g in goods_ if g["code"] == parts[0]] or [g for g in goods_ if name and name in g["name"].lower()]
        n = A._parse_num(parts[1])
        if len(hit) != 1:
            errs.append(f"строка {i}: «{parts[0][:30]}» — {'не найден' if not hit else 'подходит несколько, напишите точнее'}")
        elif not n or n != int(n):
            errs.append(f"строка {i}: штук — целое число")
        elif pick_wh(ctx.db, ctx, hit[0]["code"], n) is None:
            errs.append(f"строка {i}: ни на одном складе столько свободно нет (всего {A._num(free_wh(ctx.db, ctx, hit[0]['code']))})")
        else:
            out.append([hit[0]["code"], int(n), 1 if f.get("smp") == "1" else 0, None])
    if errs or not out:
        return move_new(ctx, "; ".join(errs[:4]) or "добавьте товар", {k: str(x) for k, x in f.items()})
    mid = ctx.db.x("INSERT INTO moves (day, loc, lines, author, author_id, ts) VALUES (?,?,?,?,?,?)", ctx.today.isoformat(),
                   ctx.me["team"], json.dumps(out), A._who(ctx), ctx.me["id"], datetime.now().isoformat(timespec="seconds"))
    return move_card(ctx, mid)


@screen("mvo", module=M)
def move_card(ctx, mid=None, act=None):
    m = ctx.db.one("SELECT * FROM moves WHERE id = ?", int(mid)) if mid and str(mid).isdigit() else None
    if not m or not (keeper(ctx) or (ctx.duty == "shop" and ctx.me["team"] == m["loc"])):
        return moves(ctx)
    if act == "go" and m["status"] == "ask" and keeper(ctx):
        src = {}
        for c, n, smp, _g in json.loads(m["lines"]):
            src[c] = pick_wh(ctx.db, ctx, c, n)
            if src[c] is None:
                return Reply(text=f"На складах свободно меньше, чем просят: {A._names(ctx.db).get(c, c)}.", buttons=[[Button("‹", f"mvo:{m['id']}")]])
        for c, n, smp, _g in json.loads(m["lines"]):
            move_qty(ctx.db, c, src[c], -n, total=False)                # в пути: на складе уже нет, в салоне ещё нет
        ctx.db.x("UPDATE moves SET status = 'go', go_ts = ? WHERE id = ?", datetime.now().isoformat(timespec="seconds"), m["id"])
        m = ctx.db.one("SELECT * FROM moves WHERE id = ?", m["id"])
    st, tone = MV_ST[m["status"]]
    v = View(f"🔁 Перемещение №{m['id']}", f"Склад → {loc_name(ctx, m['loc'])}")
    steps = [["Заявка", "done"], ["Едет", "cur" if m["status"] == "go" else "done" if m["status"] == "done" else ""],
             ["Принято в салоне", "done" if m["status"] == "done" else ""]]
    v.ui("rows", {"items": [{"t": f"Заявка {m['author']} · {RT._dm(m['day'])}", "s": _lines_txt(ctx, m), "steps": steps, "chip": [st, "", tone]}]}, "")
    if m["status"] == "done" and m["note"]:
        v.ui("rows", {"items": [{"t": "При приёмке", "s": m["note"], "chip": ["недостача", "", "bad"]}]}, "")
    if m["status"] == "ask" and keeper(ctx):
        v.ui("rows", {"items": [], "foot": [["📤 Отгрузить в салон", f"mvo:{m['id']}:go"]],
                      "empty": "Пока едет — товар ни на складе, ни в салоне: «в пути»."}, "")
    if m["status"] == "go" and ctx.duty == "shop" and ctx.me["team"] == m["loc"]:
        names = A._names(ctx.db)
        v.ui("sform", {"fields": [{"k": f"l{i}", "t": "num", "l": f"{names.get(c, c)}{' · образец' if smp else ''} · отгружено {n}", "v": str(n)}
                                  for i, (c, n, smp, _g) in enumerate(json.loads(m["lines"]))],
                       "send": ["✅ Принять в салон", f"mvy:{m['id']}"], "err": "", "top": [],
                       "note": "Меньше, чем отгрузили, — недостача уйдёт складу и директору."}, "Пересчитали в салоне")
    return Reply(view=v, buttons=[[Button("‹ К перемещениям", "mv")]])


@screen("mvy", module=M)
def move_accept(ctx, mid=None):
    m = ctx.db.one("SELECT * FROM moves WHERE id = ? AND status = 'go'", int(mid)) if mid and str(mid).isdigit() else None
    if not m or ctx.duty != "shop" or ctx.me["team"] != m["loc"]:
        return moves(ctx)
    f = ctx.form or {}
    ls = json.loads(m["lines"])
    names = A._names(ctx.db)
    short = []
    for i, row in enumerate(ls):
        c, n, smp, _g = row
        got = A._parse_num(f.get(f"l{i}", n)) if str(f.get(f"l{i}", n)).strip() not in ("0", "") else 0
        got = min(int(got or 0), n)
        row[3] = got
        move_qty(ctx.db, c, m["loc"], got, smp=got if smp else 0, total=False)
        if got < n:
            ctx.db.x("UPDATE goods SET qty = qty - ? WHERE code = ?", n - got, c)       # пропало в пути — из «всего по фирме»
            short.append(f"{names.get(c, c)} — {n - got} шт")
    ctx.db.x("UPDATE moves SET status = 'done', done_ts = ?, lines = ?, note = ? WHERE id = ?", datetime.now().isoformat(timespec="seconds"),
             json.dumps(ls), ("Недостача: " + ", ".join(short)) if short else "", m["id"])
    return move_card(ctx, m["id"])


# ======================= 📍 остатки по местам =======================

@screen("stl", module=M)
def stock_by_loc(ctx):
    if ctx.role not in ("owner", "admin", "accountant"):
        return Reply(text="Остатки по местам видят владелец, админ и бухгалтер.")
    see = A.can_see(ctx, "остатки по местам с себестоимостью")
    cost = {g["code"]: g["cost"] for g in ctx.db.q("SELECT code, cost FROM goods")}
    res = reserved(ctx.db)
    rows, total, pcs = [], 0, 0
    for loc, name in locs(ctx):
        rs = ctx.db.q("SELECT code, qty, smp FROM stock_loc WHERE loc = ?", loc)
        q = sum(r["qty"] for r in rs)
        c = sum(r["qty"] * cost.get(r["code"], 0) for r in rs)
        total += c
        pcs += q
        r_ = sum(n for (cd, lc), n in res.items() if lc == loc)
        rows.append({"n": name, "cells": [[A._num(q), ""], [A._num(r_) if r_ else "—", ""]] + ([[A._num(c), ""]] if see else []),
                     "det": f"образцов в зале {A._num(sum(r['smp'] for r in rs))}" if loc not in wh_ids(ctx) else ""})
    way = ctx.db.q("SELECT lines FROM moves WHERE status = 'go'")
    way_c = sum(n * cost.get(c, 0) for m in way for c, n, *_ in json.loads(m["lines"]))
    res_c = sum(n * cost.get(c, 0) for (c, _l), n in res.items())
    v = View("📍 Остатки по местам", "")
    v.ui("hero", {"label": f"Всё в фирме · по себестоимости {A.LOCK}" if see else "Всё в фирме", "md": True, "id": "stl",
                  "big": money(total) if see else f"{A._num(pcs)} шт",
                  "sub": (f"в резерве под заказы {money(res_c)} · в пути {money(way_c)}" if see else "себестоимость закрыта владельцем"),
                  "pairs": [], "acts": []}, fallback=["Остатки по местам"])
    A.support_block(ctx, v)
    v.ui("team", {"head": ["Где", "Штук", "Резерв"] + ([f"Себест. {A.LOCK}"] if see else []), "rows": rows}, "")
    t2 = (ctx.today - timedelta(days=2)).isoformat()
    stuck = [o for o in to_load(ctx) if o["deliv"] <= t2]
    late_mv = ctx.db.q("SELECT id, loc FROM moves WHERE status = 'go' AND go_ts < ?", t2)
    att = []
    if stuck:
        att.append({"t": "Продали, а машины нет 2 дня и больше", "s": f"{len(stuck)} заказов · водители: "
                    + ", ".join(sorted({_short(ctx.db, o.get('driver')) for o in stuck})), "chip": [str(len(stuck)), "ld", "bad"]})
    for m in late_mv:
        att.append({"t": f"Перемещение №{m['id']} не принято 2 дня", "s": f"→ {loc_name(ctx, m['loc'])}", "chip": ["открыть", f"mvo:{m['id']}", "warn"]})
    v.ui("rows", {"items": att, "empty": "Всё в порядке: машины уходят вовремя, перемещения приняты"}, "Обратите внимание")
    return Reply(view=v)
