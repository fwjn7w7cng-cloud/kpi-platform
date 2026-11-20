"""🧾 Продажа в магазине (Адам 20.11, учёт в Алапе, шаг 3; кассы нет — «никакой кассы»): продавец собирает корзину, скидка
(до 5% — сам, директор магазина — до 20%, владелец — любая), как платит (наличные / карта / перевод / рассрочка / предоплата
при доставке), как забирает (сам / доставка / доставка и сборка), покупатель. После «Продать»:
  - товар списывается со склада (goods.qty), в строке продажи запоминается себестоимость на этот момент (для маржи 🔒);
  - строки продаж (batch 0 — «из приложения») → план и % продавца считает ядро; чек — в цифры магазина (rt:<месяц>);
  - рассрочка → договор в rt_inst; доставка → заказ в rt_orders (этапы как в core/ui/retail.py).
Таблица shop_sales (миграция 9). Маржа и «ниже себестоимости» — только через acc.can_see.
"""
import json
from datetime import date, datetime, timedelta

from core.ui import retail as RT
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.views import View
from modules import acc as A
from modules import ship as SH

M = "stock"
DISC = {"seller": 0.05, "shop": 0.20}                       # потолок скидки по обязанности; владелец — любая
PAY = [["cash", "💵 Наличные"], ["card", "💳 Карта"], ["transfer", "📲 Перевод"], ["inst", "📆 Рассрочка"], ["prepay", "⏳ Предоплата"]]
TAKE = [["self", "Сам, сейчас"], ["deliv", "🚚 Доставка"], ["asm", "🚚 + 🔧 Доставка и сборка"]]
PAY_T = dict(PAY)


def ok(ctx):
    return ctx.duty in ("seller", "shop") and bool(ctx.me)


def max_disc(ctx):
    return 0.9 if ctx.role == "owner" else DISC.get(ctx.duty, 0)


menu("sale", "🧾 Продажа", {"rep", "sv", "owner"}, module=M, order=5, when=ok)


def cart(ctx):
    c = ctx.session.get("cart")
    return c if isinstance(c, dict) else {}


def _goods(db, codes):
    if not codes:
        return {}
    return {g["code"]: g for g in db.q(f"SELECT * FROM goods WHERE code IN ({','.join('?' * len(codes))})", *codes)}


def totals(ctx):
    c = cart(ctx)
    gs = _goods(ctx.db, list(c))
    lines = [(gs[k], n) for k, n in c.items() if k in gs]
    return lines, sum(g["price"] * n for g, n in lines)


@screen("sale", module=M)
def sale(ctx, act=None, code=None, err=""):
    if not ok(ctx):
        return Reply(text="🧾 Продажу оформляют продавцы и директора магазинов.")
    if act == "ok":
        return checkout(ctx)
    c = cart(ctx)
    if act in ("a", "m", "x") and code and ctx.db.one("SELECT 1 AS x FROM goods WHERE code = ?", code):
        n = c.get(code, 0) + (1 if act == "a" else -1 if act == "m" else -c.get(code, 0))
        if n > 0:
            c[code] = min(n, 999)
        else:
            c.pop(code, None)
        ctx.session["cart"] = c
        ctx.save()
    if act == "clr":
        ctx.session["cart"] = {}
        ctx.save()
    lines, tot = totals(ctx)
    shop = RT.shop_name(ctx.cfg, ctx.me["team"])
    v = View("🧾 Продажа", f"{RT._short(ctx.me['fio'])} · {shop}")
    v.ui("hero", {"label": "Итого без скидки", "big": money(tot), "md": True, "id": "sale",
                  "sub": f"{len(lines)} поз. · {sum(n for _, n in lines)} шт" if lines else "добавьте товары ниже",
                  "pairs": [], "acts": []}, fallback=[f"Итого: {money(tot)}"])
    q = str((ctx.form or {}).get("q", "")).strip().lower()[:60] if act == "q" else ""
    v.ui("form", {"fields": [{"k": "q", "l": "🔍 Товар", "v": q, "ph": "название или код", "w": True}], "send": ["Найти", "sale:q"]}, "")
    if q:
        found = [g for g in ctx.db.q("SELECT * FROM goods ORDER BY name") if q in g["name"].lower() or q == g["code"]][:12]
        title = f"Нашлось: {len(found)}"
    else:
        found = ctx.db.q("SELECT g.* FROM goods g LEFT JOIN (SELECT code, SUM(qty) AS q FROM item_sales WHERE day >= ? GROUP BY code) s "
                         "ON s.code = g.code WHERE g.qty > 0 ORDER BY COALESCE(s.q, 0) DESC LIMIT 8", ctx.month + "-01")
        title = "Часто берут"
    locs = SH.has_locs(ctx.db)
    res = SH.reserved(ctx.db) if locs else {}
    items = []
    for g in found:
        if locs:                                             # свободно = есть − образцы − резерв, по местам (modules/ship.py)
            here, wh = SH.free(ctx.db, g["code"], ctx.me["team"], res), SH.free_wh(ctx.db, ctx, g["code"], res)
            can, s_ = here + wh, f"{money(g['price'])} · свободно: тут {A._num(here)} · склад {A._num(wh)}"
            if not here and SH.stock_at(ctx.db, g["code"], ctx.me["team"])[1]:
                s_ += " · в зале образец"
        else:
            can, s_ = g["qty"], f"{money(g['price'])} · на складе {A._num(g['qty'])}"
        items.append({"t": g["name"], "s": s_, "chip": ["＋ добавить", f"sale:a:{g['code']}", ""] if can > c.get(g["code"], 0)
                      else ["нет свободного", "", "bad"]})
    v.ui("rows", {"items": items, "empty": "Ничего не нашлось"}, title)
    if lines:
        v.ui("rows", {"items": [{"t": g["name"], "s": f"{money(g['price'])} × {n}", "v": money(g["price"] * n),
                                 "chip": ["− 1", f"sale:m:{g['code']}", ""]} for g, n in lines],
                      "foot": [["🗑 Очистить корзину", "sale:clr"]]}, "Корзина")
        v.ui("sform", {"fields": form_fields(ctx), "send": ["✅ Продать", "sale:ok"], "err": err, "top": [],
                       "note": f"Скидка до {pct(max_disc(ctx))} — сами. Себестоимость продавец не видит."}, "Оформление")
    return Reply(view=v, buttons=[[Button("🧾 Мои продажи сегодня", "sll")]])


def form_fields(ctx, f=None):
    f = f or {}
    asm = bool((RT.orders(ctx.db) or {}).get("asm_word"))
    take = [t for t in TAKE if t[0] != "asm" or asm]
    return [
        {"k": "disc", "t": "num", "l": "Скидка, %", "v": f.get("disc", "0")},
        {"k": "pay", "t": "seg", "l": "Как платит", "v": f.get("pay", "cash"), "opts": PAY},
        {"k": "months", "t": "seg", "l": "Срок рассрочки", "v": f.get("months", "10"), "opts": [["6", "6 мес"], ["10", "10 мес"], ["12", "12 мес"]],
         "when": ["pay", ["inst"]]},
        {"k": "first", "t": "seg", "l": "Первый взнос", "v": f.get("first", "25"), "opts": [["20", "20%"], ["25", "25%"], ["30", "30%"]],
         "when": ["pay", ["inst"]]},
        {"k": "prepay", "t": "num", "l": "Предоплата, ₽", "v": f.get("prepay", ""), "when": ["pay", ["prepay"]]},
        {"k": "take", "t": "seg", "l": "Забирает", "v": f.get("take", "self"), "opts": take},
        {"k": "drv", "t": "seg", "l": "Водитель · сколько у него заказов", "v": f.get("drv", ""), "when": ["take", ["deliv", "asm"]],
         "opts": [[str(e["id"]), f"🚚 {RT._short(e['fio'])} · {SH.driver_load(ctx.db, e['id'])}"] for e in SH.drivers(ctx)] + [["", "Назначу позже"]]},
        {"k": "addr", "t": "text", "l": "Адрес доставки", "v": f.get("addr", ""), "ph": "улица, дом, квартира", "when": ["take", ["deliv", "asm"]]},
        {"k": "dday", "t": "date", "l": "Дата доставки", "v": f.get("dday", (ctx.today + timedelta(days=2)).isoformat()),
         "min": ctx.today.isoformat(), "when": ["take", ["deliv", "asm"]],
         "quick": [[(ctx.today + timedelta(days=k)).isoformat(), t] for k, t in ((1, "Завтра"), (2, "Послезавтра"), (7, "Через неделю"))]},
        {"k": "buyer", "t": "text", "l": "Покупатель (для рассрочки и доставки — обязательно)", "v": f.get("buyer", ""),
         "ph": "Алиев Хасан, 8 928 000-00-00"},
    ]


def _num(x):
    try:
        return float(str(x).replace(" ", "").replace(",", ".") or 0)
    except ValueError:
        return None


def _err(ctx, msg, f):
    r = sale(ctx, err=msg)
    for kind, b in r.view.blocks:
        if kind == "ui" and b[0] == "sform":
            b[1]["fields"] = form_fields(ctx, {k: str(x)[:200] for k, x in f.items()})
    return r


def checkout(ctx):
    f = ctx.form or {}
    lines, tot = totals(ctx)
    if not lines:
        return sale(ctx, err="Корзина пустая")
    take0 = f.get("take") if f.get("take") in ("self", "deliv", "asm") else "self"
    srcs = {}
    if SH.has_locs(ctx.db):                                  # откуда отдаём: сам — из этого салона, доставка — со склада (или из салона)
        res, here = SH.reserved(ctx.db), ctx.me["team"]
        for g, n in lines:
            fh, fw = SH.free(ctx.db, g["code"], here, res), SH.free_wh(ctx.db, ctx, g["code"], res)
            if take0 == "self":
                if fh < n:
                    return _err(ctx, f"«{g['name']}» в этом салоне свободно {A._num(fh)}" + (" (образец не продаётся)" if
                                SH.stock_at(ctx.db, g["code"], here)[1] else "") + " — со склада только доставкой.", f)
                srcs[g["code"]] = here
            else:
                w = SH.pick_wh(ctx.db, ctx, g["code"], n, res)
                if w:
                    srcs[g["code"]] = w
                elif fh >= n:
                    srcs[g["code"]] = here
                else:
                    return _err(ctx, f"«{g['name']}» свободно: склад {A._num(fw)}, этот салон {A._num(fh)}.", f)
    else:
        for g, n in lines:
            if n > g["qty"]:
                return _err(ctx, f"«{g['name']}» на складе только {A._num(g['qty'])}", f)
    d = _num(f.get("disc"))
    if d is None or d < 0 or d / 100 > max_disc(ctx):
        return _err(ctx, f"Скидка — от 0 до {round(max_disc(ctx) * 100)}%. Больше — директор магазина.", f)
    disc = d / 100
    pay = f.get("pay") if f.get("pay") in PAY_T else "cash"
    take = f.get("take") if f.get("take") in ("self", "deliv", "asm") else "self"
    buyer = str(f.get("buyer", "")).strip()[:120]
    total = round(tot * (1 - disc), -1)
    if pay == "prepay" and take == "self":
        return _err(ctx, "Предоплата — только когда товар везём. Сам забирает — оплата сразу.", f)
    if (pay == "inst" or take != "self") and len(buyer) < 3:
        return _err(ctx, "Для рассрочки и доставки напишите покупателя: имя и телефон.", f)
    dday = None
    if take != "self":
        try:
            dday = date.fromisoformat(str(f.get("dday", ""))[:10])
        except ValueError:
            dday = None
        if not dday or dday < ctx.today or dday > ctx.today + timedelta(days=60):
            return _err(ctx, "Дата доставки — с сегодня и не дальше 60 дней.", f)
        if len(str(f.get("addr", "")).strip()) < 5:
            return _err(ctx, "Напишите адрес доставки.", f)
    prepay = 0
    if pay == "prepay":
        prepay = _num(f.get("prepay"))
        if not prepay or prepay <= 0 or prepay >= total:
            return _err(ctx, f"Предоплата — больше нуля и меньше {money(total)}.", f)
    months = int(f.get("months")) if str(f.get("months")) in ("6", "10", "12") else 10
    first = int(f.get("first")) if str(f.get("first")) in ("20", "25", "30") else 25
    drv = int(f["drv"]) if str(f.get("drv", "")).isdigit() and any(e["id"] == int(f["drv"]) for e in SH.drivers(ctx)) else None
    return finish(ctx, lines, total, disc, pay, take, buyer, dday, str(f.get("addr", "")).strip()[:160], prepay, months, first,
                  srcs, drv)


def finish(ctx, lines, total, disc, pay, take, buyer, dday, addr, prepay, months, first, srcs=None, drv=None):
    db, me, day = ctx.db, ctx.me, ctx.today.isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    gcode = {r["name"]: r["code"] for r in db.q("SELECT code, name FROM groups")}
    key = (db.one("SELECT key FROM emp_keys WHERE emp_id = ? ORDER BY since DESC LIMIT 1", me["id"]) or {}).get("key", "")
    rows = [[g["code"], n, round(g["price"] * (1 - disc), 2), g["cost"]] for g, n in lines]
    by_g = {}
    for g, n in lines:
        if srcs:                                            # учёт по местам: сам забрал — списали из салона; доставка — резерв до погрузки
            if take == "self":
                SH.move_qty(db, g["code"], srcs[g["code"]], -n)
        else:
            db.x("UPDATE goods SET qty = qty - ? WHERE code = ?", n, g["code"])
        k = gcode.get(g["grp"], "")
        by_g[k] = by_g.get(k, 0) + g["price"] * n * (1 - disc)
    if key:
        for k, a in by_g.items():                           # строки продаж «из приложения» (batch 0) — план и % продавца
            db.x("INSERT INTO sales (batch, day, emp_key, client, grp, amount, qty, discount) VALUES (0,?,?,?,?,?,?,?)",
                 day, key, f"R-{me['team']}", k, round(a, 2), sum(n for g, n in lines if gcode.get(g["grp"], "") == k), disc)
    mdata = db.get(f"rt:{ctx.month}") or {}
    ch = mdata.setdefault("checks", {}).setdefault(str(me["id"]), {})
    x = ch.get(day) or [0, 0, 0]
    ch[day] = [x[0] + 1, x[1] + sum(n for _, n in lines), x[2] + total]
    if pay == "inst":
        mdata.setdefault("inst", {})[str(me["id"])] = mdata.get("inst", {}).get(str(me["id"]), 0) + 1
    db.put(f"rt:{ctx.month}", mdata)
    item = ", ".join(g["name"] for g, _ in lines)[:80]
    od, inst_id, order_id = RT.orders(db) or {}, None, None
    items = od.get("items") or []
    nid = max([o["id"] for o in items] + [x["id"] for x in RT.inst_list(db)] + [1000]) + 1
    if pay == "inst":
        inst = RT.inst_list(db)
        inst.append({"id": nid, "day": day, "shop": me["team"], "seller": me["id"], "client": buyer, "sum": total,
                     "first": round(total * first / 100, -1), "months": months, "late": False, "late_days": 0, "debt": 0})
        db.put("rt_inst", inst)
        inst_id = nid
    if take != "self":
        stage = 1 if pay != "prepay" else 0
        items.append({"id": nid, "day": day, "shop": me["team"], "seller": me["id"], "client": buyer, "item": item, "sum": total,
                      "pay": "inst" if pay == "inst" else "prepay" if pay == "prepay" else "full", "prepay": prepay,
                      "months": months if pay == "inst" else 0, "deliv": dday.isoformat(), "stage": stage, "paid": True,
                      "late": False, "asm": None, "complaint": False, "addr": addr, "need_asm": take == "asm"})
        if srcs:
            items[-1].update(lines=[[g["code"], n, srcs[g["code"]]] for g, n in lines], driver=drv, load="", stage=0)
        od["items"] = items
        od.setdefault("stages", ["Заказ", "Оплата", "Доставка"] + (["Сборка"] if od.get("asm_word") else []))
        db.put("rt_orders", od)
        order_id = nid
    sid = db.x("INSERT INTO shop_sales (day, ts, shop, seller, lines, disc, total, pay, take, buyer, order_id, inst_id) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", day, now, me["team"], me["id"], json.dumps(rows), disc, total, pay, take, buyer,
               order_id, inst_id)
    ctx.session["cart"] = {}
    ctx.save()
    return sale_card(ctx, sid)


@screen("slo", module=M)
def sale_card(ctx, sid=None):
    s = ctx.db.one("SELECT * FROM shop_sales WHERE id = ?", int(sid)) if sid and str(sid).isdigit() else None
    if not s or not (ctx.role == "owner" or (ctx.me and (s["seller"] == ctx.me["id"] or (ctx.duty == "shop" and s["shop"] == ctx.me["team"])))):
        return Reply(text="Продажа не найдена.")
    see = A.can_see(ctx, f"продажа №{s['id']} с себестоимостью")
    names = A._names(ctx.db)
    ls = json.loads(s["lines"])
    margin = sum((p - c) * n for _, n, p, c in ls)
    v = View(f"🧾 Продажа №{s['id']}", f"{RT.shop_name(ctx.cfg, s['shop'])} · {s['ts'][11:16]}")
    pairs = [["Оплата", PAY_T.get(s["pay"], s["pay"])[2:], ""], ["Скидка", pct(s["disc"]), ""]]
    if see:
        pairs.append([f"Маржа {A.LOCK}", money(margin), "bad" if margin < 0 else ""])
    v.ui("hero", {"label": "Продано", "big": money(s["total"]), "md": True, "id": f"slo{s['id']}",
                  "sub": dict(TAKE).get(s["take"], "") + (f" · {s['buyer']}" if s["buyer"] else ""), "pairs": pairs, "acts": []},
         fallback=[f"Продано: {money(s['total'])}"])
    v.ui("rows", {"items": [{"t": names.get(c, c), "s": f"{money(p)} × {A._num(n)}", "v": money(p * n)} for c, n, p, _ in ls]}, "Товары")
    links = []
    if s["order_id"]:
        links.append(["🚚 Заказ с доставкой", "rto:me" if ctx.duty == "seller" else "rto"])
    links.append(["🧾 Новая продажа", "sale"])
    note = ("Товар в резерве на складе — спишется, когда его погрузят в машину. Продажа уже в плане продавца." if s["take"] != "self"
            else "Товар списан. Продажа уже в плане продавца.")
    v.ui("rows", {"items": [], "foot": links, "empty": note}, "")
    return Reply(view=v)


@screen("sll", module=M)
def my_sales(ctx):
    if not ok(ctx) and ctx.role != "owner":
        return Reply(text="Продажи видят продавцы, директора магазинов и владелец.")
    q, a = "SELECT * FROM shop_sales WHERE day = ?", [ctx.today.isoformat()]
    if ctx.duty == "seller":
        q, a = q + " AND seller = ?", a + [ctx.me["id"]]
    elif ctx.duty == "shop":
        q, a = q + " AND shop = ?", a + [ctx.me["team"]]
    rs = ctx.db.q(q + " ORDER BY id DESC", *a)
    v = View("🧾 Продажи сегодня", "")
    v.ui("rows", {"items": [{"t": f"№{s['id']} · {s['ts'][11:16]} · {PAY_T.get(s['pay'], '')}", "s": s["buyer"] or "без имени",
                             "v": money(s["total"]), "chip": ["открыть", f"slo:{s['id']}", ""]} for s in rs],
                  "empty": "Сегодня продаж через приложение ещё нет", "foot": [["🧾 Новая продажа", "sale"]] if ok(ctx) else []},
         f"{len(rs)} продаж · {money(sum(s['total'] for s in rs))}")
    return Reply(view=v)


def owner_block(ctx, v):
    """Директору на главную: продажи магазинов сегодня из приложения, маржа 🔒 и «ниже себестоимости» 🔒."""
    rs = ctx.db.q("SELECT * FROM shop_sales WHERE day = ?", ctx.today.isoformat())
    if not rs:
        return
    see = A.can_see(ctx)
    items = []
    for team, name in RT.shops(ctx.cfg):
        mine = [s for s in rs if s["shop"] == team]
        if not mine:
            continue
        tot = sum(s["total"] for s in mine)
        mg = sum((p - c) * n for s in mine for _, n, p, c in json.loads(s["lines"]))
        items.append({"t": name, "s": f"{len(mine)} продаж" + (f" · маржа {A.LOCK} {money(mg)}" if see else ""), "v": money(tot)})
    v.ui("rows", {"items": items, "right": "через приложение"}, "Продано сегодня")
    if see:
        names = A._names(ctx.db)
        low = [{"t": f"{names.get(c, c)} · скидка {pct(s['disc'])}", "s": f"{RT.shop_name(ctx.cfg, s['shop'])} · {money((p - cst) * n)} к себестоимости",
                "chip": ["минус", f"slo:{s['id']}", "bad"]} for s in rs for c, n, p, cst in json.loads(s["lines"]) if p < cst]
        if low:
            v.ui("rows", {"items": low[:6]}, f"Продали ниже себестоимости {A.LOCK}")
