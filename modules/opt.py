"""🧾 Продажа оптом в учёте Алапы (Адам 20.11; эскиз OPT.png): опт продают отдельные менеджеры опта, не менеджеры магазинов,
по оптовой цене (`goods.price_opt`); клиенты постоянные, берут в долг; товар забирает водитель клиента со склада.

Путь: менеджер опта `os` — клиент (свой или ➕ новый), товары по оптовой цене (склад — где свободно, `ship.pick_wh`), оплата
в долг / сразу / частично, срок 7 / 14 / 30 дней или дата, водитель клиента → «Оформить накладную» (`os:ok`) → накладная «О-0001»
ждёт выдачи (товар в резерве, `ship.reserved`). Кладовщик `ow` → `ow:<id>` отмечает, сколько выдал → `owy:<id>`: списание со склада
выданного, накладная на выданную сумму, строки продаж batch 0 (план и % менеджера), долг — срез `debts` за сегодня (batch 0).
Клиент `oc:<id>`: долг по срокам (0–30 / 31–60 / 61–90 / 91+ от дня накладной), просрочено (после срока оплаты), накладные, оплаты;
«💵 Принять оплату» `op:<id>` → `opy:<id>` — оплата закрывает старые накладные первыми (`ledger`), строка `payments` batch 0 (сбор).
Клиент в данных ядра — код «O<id>» (таблица `clients`, batch 0). Менеджер магазина оптовых цен не видит (`opt_ok`).
"""
import json
from datetime import date, datetime, timedelta

from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen
from core.ui.views import View
from modules import acc as A
from modules import ship as SH

M = "stock"
DUE = [["7", "7 дн"], ["14", "14 дн"], ["30", "30 дн"], ["d", "своя дата"]]
PAY = [["debt", "💳 В долг"], ["paid", "💵 Оплачено сразу"], ["part", "Частично"]]


def _shop_dept(ctx):
    from core.ui.retail import shop_dept
    return shop_dept(ctx.cfg)


def active(ctx):
    """Опт ведётся в Алапе: учёт без 1С (или демо с магазинами) и товары есть."""
    src = (ctx.cfg.source or {}).get("kind")
    return (src == "alapa" or (ctx.demo and getattr(ctx, "segment", "opt") != "opt")) and \
        bool(ctx.db.one("SELECT 1 AS x FROM goods WHERE off = 0 LIMIT 1"))


def opt_ok(ctx):
    """Продаёт оптом: менеджеры отдела продаж, который не «Магазины»."""
    return bool(ctx.me) and ctx.dept_kind == "sales" and ctx.me["dept"] != _shop_dept(ctx) and ctx.role in ("rep", "sv", "head")


def sees_all(ctx):
    return ctx.role in ("owner", "admin", "accountant") or (opt_ok(ctx) and ctx.role in ("sv", "head"))


def can_pay(ctx, cl):
    return sees_all(ctx) or (opt_ok(ctx) and cl["manager"] == ctx.me["id"])


menu("os", "🧾 Продажа оптом", {"rep", "sv", "head"}, module=M, order=5, when=lambda c: opt_ok(c) and active(c))
menu("oc", "🏢 Оптовые клиенты", {"rep", "sv", "head", "owner", "admin", "accountant"}, module=M, order=6,
     when=lambda c: active(c) and (opt_ok(c) or sees_all(c)))
menu("ow", "📤 Выдача клиентам", {"head", "staff", "owner", "admin"}, module=M, order=7,
     when=lambda c: active(c) and A.keeper(c) and bool(c.db.one("SELECT 1 AS x FROM opt_orders WHERE status = 'wait' LIMIT 1")))


def no(oid):
    return f"О-{int(oid):04d}"


def _client(db, cid):
    return db.one("SELECT * FROM opt_clients WHERE id = ?", int(cid)) if str(cid).isdigit() else None


def _key(db, emp_id):
    return A.emp_key(db, emp_id)


def my_clients(ctx):
    q = "SELECT * FROM opt_clients WHERE off = 0"
    if not sees_all(ctx):
        return ctx.db.q(q + " AND manager = ? ORDER BY name", ctx.me["id"])
    return ctx.db.q(q + " ORDER BY name")


# ======================= долг клиента =======================

def ledger(db, cid, today):
    """Долг клиента: оплаты закрывают накладные по порядку → (долг, просрочено, корзины по возрасту, [накладные с остатком])."""
    orders = db.q("SELECT * FROM opt_orders WHERE client = ? AND status = 'done' ORDER BY day, id", cid)
    paid = sum(r["amount"] for r in db.q("SELECT amount FROM opt_pay WHERE client = ?", cid))
    out, debt, late, b = [], 0.0, 0.0, [0.0, 0.0, 0.0, 0.0]
    for o in orders:
        take = min(paid, o["total"])
        paid -= take
        rest = round(o["total"] - take, 2)
        out.append(dict(o, rest=rest))
        if rest > 0:
            debt += rest
            if o["due"] < today.isoformat():
                late += rest
            age = (today - date.fromisoformat(o["day"])).days
            b[0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3] += rest
    return round(debt - paid, 2), round(late, 2), b, out          # остаток оплат сверх накладных — переплата (долг меньше нуля)


def sync_debts(ctx):
    """Срез долгов за сегодня в данные ядра (batch 0): дебиторка, просрочка, зарплата менеджера считаются как по 1С."""
    db, t = ctx.db, ctx.today.isoformat()
    db.x('DELETE FROM debts WHERE batch = 0 AND "on" = ? AND client LIKE ?', t, "O%")
    for cl in db.q("SELECT * FROM opt_clients"):
        key = _key(db, cl["manager"])
        debt, _late, b, _ = ledger(db, cl["id"], ctx.today)
        if key and debt > 0:
            db.x('INSERT INTO debts (batch, "on", emp_key, client, total, d0_30, d31_60, d61_90, d91) VALUES (0,?,?,?,?,?,?,?,?)',
                 t, key, f"O{cl['id']}", debt, *[round(x, 2) for x in b])


# ======================= продажа =======================

def cart(ctx):
    c = ctx.session.get("ocart")
    return c if isinstance(c, dict) else {}


def lines_of(ctx):
    c = cart(ctx)
    gs = {g["code"]: g for g in ctx.db.q(f"SELECT * FROM goods WHERE code IN ({','.join('?' * len(c))})", *c)} if c else {}
    ls = [(gs[k], n) for k, n in c.items() if k in gs]
    return ls, sum((g["price_opt"] or 0) * n for g, n in ls)


@screen("os", module=M)
def sale(ctx, act=None, code=None, err=""):
    if not opt_ok(ctx) or not active(ctx):
        return Reply(text="🧾 Оптом продают менеджеры опта.")
    if act == "ok":
        return checkout(ctx)
    c = cart(ctx)
    if act in ("a", "m", "x") and code and ctx.db.one("SELECT 1 AS x FROM goods WHERE code = ? AND off = 0", code):
        n = c.get(code, 0) + (1 if act == "a" else -1 if act == "m" else -c.get(code, 0))
        c = {**c, code: min(n, 9999)} if n > 0 else {k: x for k, x in c.items() if k != code}
    if act == "n" and code:                                        # сразу N штук — из поля «сколько»
        n = A._parse_num((ctx.form or {}).get("n", ""))
        if n and n == int(n):
            c = {**c, code: int(n)}
    if act == "clr":
        c = {}
    if act == "cl" and str(code).isdigit():
        ctx.session["ocl"] = int(code)
    ctx.session["ocart"] = c
    ctx.save()
    ls, tot = lines_of(ctx)
    cl = _client(ctx.db, ctx.session.get("ocl") or 0)
    v = View("🧾 Продажа оптом", f"{ctx.me['fio']} · по оптовым ценам")
    mine = my_clients(ctx)
    if cl:
        debt, late, _, _ = ledger(ctx.db, cl["id"], ctx.today)
        v.ui("rows", {"items": [{"t": cl["name"], "s": f"долг {money(debt)}" + (f" · просрочено {money(late)}" if late else " · просрочки нет"),
                                 "chip": ["сменить", "os:cl:0", "blue"]}]}, "Клиент")
    else:
        v.ui("choice", {"items": [[x["name"], f"os:cl:{x['id']}", False] for x in mine[:30]]}, "Клиент" if mine else "")
        v.ui("form", {"fields": [{"k": "name", "l": "➕ Новый клиент", "ph": "название фирмы или ИП", "w": True}],
                      "send": ["Добавить", "ocn"]}, "" if mine else "Клиент")
    q = str((ctx.form or {}).get("q", "")).strip().lower()[:60] if act == "q" else ""
    v.ui("form", {"fields": [{"k": "q", "l": "🔍 Товар", "v": q, "ph": "название или код", "w": True}], "send": ["Найти", "os:q"]}, "")
    res = SH.reserved(ctx.db)
    found = [g for g in ctx.db.q("SELECT * FROM goods WHERE off = 0 ORDER BY name") if q in g["name"].lower() or q == g["code"]][:12] if q else []
    items = []
    for g in found:
        fw = SH.free_wh(ctx.db, ctx, g["code"], res)
        items.append({"t": g["name"], "s": f"опт {money(g['price_opt'])} · свободно на складах {A._num(fw)}" if g["price_opt"] else
                      "оптовой цены нет — впишите в карточке товара", "chip": ["＋ добавить", f"os:a:{g['code']}", ""]
                      if g["price_opt"] and fw > c.get(g["code"], 0) else ["нельзя", "", "bad"]})
    if q:
        v.ui("rows", {"items": items, "empty": "Ничего не нашлось"}, f"Нашлось: {len(found)}")
    if ls:
        rows = []
        for g, n in ls:
            w = SH.pick_wh(ctx.db, ctx, g["code"], n, res)
            rows.append({"t": g["name"], "s": f"{money(g['price_opt'])} × {n} · " + (f"со склада {SH.loc_name(ctx, w)}" if w else
                         f"на одном складе столько нет (всего {A._num(SH.free_wh(ctx.db, ctx, g['code'], res))})"),
                         "v": money(g["price_opt"] * n), "chip": ["− 1", f"os:m:{g['code']}", "" if w else "bad"]})
        v.ui("rows", {"items": rows, "foot": [["🗑 Очистить", "os:clr"]]}, f"Накладная · {money(tot)}")
        v.ui("sform", {"fields": form_fields(ctx, cl), "send": ["✅ Оформить накладную", "os:ok"], "err": err, "top": [],
                       "note": "Товар уйдёт в резерв на складе и спишется, когда кладовщик отдаст его водителю клиента."}, "Оформление")
    return Reply(view=v, buttons=[[Button("🏢 Мои клиенты", "oc")]])


def form_fields(ctx, cl, f=None):
    f = f or {}
    return [{"k": "pay", "t": "seg", "l": "Оплата", "v": f.get("pay", "debt"), "opts": PAY},
            {"k": "paid", "t": "num", "l": "Оплатил сейчас, ₽", "v": f.get("paid", ""), "when": ["pay", ["part"]]},
            {"k": "due", "t": "seg", "l": "Срок оплаты", "v": f.get("due", str((cl or {}).get("days") or 30) if str((cl or {}).get("days")) in ("7", "14", "30") else "30"),
             "opts": DUE, "when": ["pay", ["debt", "part"]]},
            {"k": "dday", "t": "date", "l": "Оплатить до", "v": f.get("dday", (ctx.today + timedelta(days=30)).isoformat()),
             "min": ctx.today.isoformat(), "when": ["due", ["d"]]},
            {"k": "driver", "t": "text", "l": "Заберёт водитель клиента", "v": f.get("driver", ""), "ph": "фамилия, номер машины"},
            {"k": "note", "t": "text", "l": "Комментарий", "v": f.get("note", "")}]


@screen("ocn", module=M)
def new_client(ctx):
    if not opt_ok(ctx) and not sees_all(ctx):
        return Reply(text="Оптовых клиентов заводят менеджеры опта.")
    name = " ".join(str((ctx.form or {}).get("name", "")).split())[:80]
    if len(name) < 2:
        return sale(ctx, err="впишите название клиента")
    have = ctx.db.one("SELECT id FROM opt_clients WHERE lower(name) = lower(?) AND off = 0", name)
    cid = have["id"] if have else ctx.db.x("INSERT INTO opt_clients (name, manager, ts) VALUES (?,?,?)", name,
                                            (ctx.me or {}).get("id"), datetime.now().isoformat(timespec="seconds"))
    ctx.db.x("INSERT OR REPLACE INTO clients (batch, code, name) VALUES (0, ?, ?)", f"O{cid}", name)
    ctx.session["ocl"] = cid
    ctx.save()
    return sale(ctx)


def checkout(ctx):
    f = ctx.form or {}
    ls, tot = lines_of(ctx)
    cl = _client(ctx.db, ctx.session.get("ocl") or 0)
    if not cl:
        return sale(ctx, err="выберите клиента")
    if not ls:
        return sale(ctx, err="добавьте товары")
    res = SH.reserved(ctx.db)
    rows = []
    for g, n in ls:
        if not g["price_opt"]:
            return sale(ctx, err=f"у «{g['name']}» нет оптовой цены")
        w = SH.pick_wh(ctx.db, ctx, g["code"], n, res)
        if not w:
            return sale(ctx, err=f"«{g['name']}»: на одном складе столько свободно нет")
        rows.append([g["code"], n, g["price_opt"], w])
    pay = f.get("pay") if f.get("pay") in ("debt", "paid", "part") else "debt"
    paid = tot if pay == "paid" else 0
    if pay == "part":
        paid = A._parse_num(f.get("paid", "")) or 0
        if not 0 < paid < tot:
            return sale(ctx, err=f"оплатил сейчас — больше нуля и меньше {money(tot)}")
    if f.get("due") == "d":
        try:
            due = date.fromisoformat(str(f.get("dday", ""))[:10])
        except ValueError:
            return sale(ctx, err="дата оплаты")
        if due < ctx.today:
            return sale(ctx, err="срок оплаты — не раньше сегодня")
    else:
        due = ctx.today + timedelta(days=int(f.get("due")) if str(f.get("due")) in ("7", "14", "30") else 30)
    if pay == "paid":
        due = ctx.today
    now = datetime.now().isoformat(timespec="seconds")
    oid = ctx.db.x("INSERT INTO opt_orders (day, ts, client, manager, lines, total, due, driver, note) VALUES (?,?,?,?,?,?,?,?,?)",
                   ctx.today.isoformat(), now, cl["id"], ctx.me["id"], json.dumps(rows), round(tot, 2), due.isoformat(),
                   " ".join(str(f.get("driver", "")).split())[:80], " ".join(str(f.get("note", "")).split())[:200])
    if paid:
        add_pay(ctx, cl, paid, "при накладной " + no(oid))
    ctx.session["ocart"] = {}
    ctx.save()
    out = [order_card(ctx, oid)]
    for e in ctx.db.employees():                                   # кладовщикам складов — «ждёт выдачи»
        d = ctx.cfg.dept(e["dept"])
        if e.get("tg_id") and d and d.kind == "warehouse":
            out.append(Reply(text=f"📤 {no(oid)} · {cl['name']} — ждёт выдачи водителю клиента.", buttons=[[Button("📤 Выдать", f"ow:{oid}")]],
                             to=e["tg_id"]))
    return out


def add_pay(ctx, cl, amount, note=""):
    t = ctx.today.isoformat()
    ctx.db.x("INSERT INTO opt_pay (day, ts, client, amount, how, who, note) VALUES (?,?,?,?,?,?,?)", t,
             datetime.now().isoformat(timespec="seconds"), cl["id"], round(amount, 2), "", A._who(ctx), note[:200])
    key = _key(ctx.db, cl["manager"])
    if key:
        ctx.db.x("INSERT INTO payments (batch, day, emp_key, client, amount) VALUES (0,?,?,?,?)", t, key, f"O{cl['id']}", round(amount, 2))
    sync_debts(ctx)


ST = {"wait": ("ждёт выдачи", "warn"), "done": ("выдано", "good"), "off": ("отменена", "")}


@screen("oo", module=M)
def order_card(ctx, oid=None, act=None):
    o = ctx.db.one("SELECT * FROM opt_orders WHERE id = ?", int(oid)) if str(oid or "").isdigit() else None
    cl = _client(ctx.db, o["client"]) if o else None
    if not o or not (sees_all(ctx) or A.keeper(ctx) or (ctx.me and o["manager"] == ctx.me["id"])):
        return Reply(text="Накладная не найдена.")
    if act == "x" and o["status"] == "wait" and (sees_all(ctx) or (ctx.me and o["manager"] == ctx.me["id"])):
        ctx.db.x("UPDATE opt_orders SET status = 'off' WHERE id = ?", o["id"])
        o = dict(o, status="off")
    names = A._names(ctx.db)
    st, tone = ST[o["status"]]
    v = View(f"🧾 Накладная {no(o['id'])}", f"{cl['name']} · {A._dm(o['day'])}")
    v.ui("hero", {"label": "Сумма", "big": money(o["total"]), "md": True, "id": f"oo{o['id']}",
                  "sub": f"оплатить до {A._dm(o['due'])}" + (f" · водитель клиента: {o['driver']}" if o["driver"] else ""),
                  "pairs": [["Состояние", st, tone]], "acts": []}, fallback=[f"Сумма: {money(o['total'])}"])
    v.ui("rows", {"items": [{"t": names.get(c, c), "s": f"{money(p)} × {A._num(n)} · {SH.loc_name(ctx, w)}", "v": money(p * n)}
                            for c, n, p, w in json.loads(o["lines"])]}, "Товары")
    kb = [[Button("🏢 Клиент", f"oc:{cl['id']}")]]
    if o["status"] == "wait" and A.keeper(ctx):
        kb.insert(0, [Button("📤 Выдать", f"ow:{o['id']}")])
    if o["status"] == "wait" and (sees_all(ctx) or (ctx.me and o["manager"] == ctx.me["id"])):
        kb.append([Button("✖️ Отменить накладную", f"oo:{o['id']}:x")])
    if opt_ok(ctx):
        kb.append([Button("🧾 Новая продажа", "os")])
    return Reply(view=v, buttons=kb)


# ======================= выдача =======================

@screen("ow", module=M)
def give(ctx, oid=None, err=""):
    if not A.keeper(ctx):
        return Reply(text="Выдаёт склад.")
    o = ctx.db.one("SELECT * FROM opt_orders WHERE id = ? AND status = 'wait'", int(oid)) if str(oid or "").isdigit() else None
    if not o:
        rs = ctx.db.q("SELECT o.*, c.name FROM opt_orders o JOIN opt_clients c ON c.id = o.client WHERE o.status = 'wait' ORDER BY o.id")
        v = View("📤 Выдача клиентам", f"ждут {len(rs)}")
        v.ui("rows", {"items": [{"t": f"{no(r['id'])} · {r['name']}", "s": f"{len(json.loads(r['lines']))} поз."
                                 + (f" · водитель: {r['driver']}" if r["driver"] else ""), "chip": ["выдать", f"ow:{r['id']}", ""]} for r in rs],
                      "empty": "Сейчас никто не ждёт"}, "")
        return Reply(view=v)
    cl = _client(ctx.db, o["client"])
    names = A._names(ctx.db)
    ls = json.loads(o["lines"])
    v = View("📤 Выдача", f"{no(o['id'])} · {cl['name']}")
    v.ui("hero", {"label": f"Накладная {no(o['id'])} · {cl['name']}", "big": f"{len(ls)} поз.", "md": True, "id": f"ow{o['id']}",
                  "sub": f"водитель клиента: {o['driver']}" if o["driver"] else "водитель клиента не вписан", "pairs": [], "acts": []}, "")
    v.ui("sform", {"fields": [{"k": f"l{i}", "t": "num", "l": f"{names.get(c, c)} · {SH.loc_name(ctx, w)} · нужно {A._num(n)}", "v": A._num(n)}
                              for i, (c, n, p, w) in enumerate(ls)], "send": ["✅ Выдал — машина уехала", f"owy:{o['id']}"],
                   "err": err, "top": [], "note": "Списывается только выданное. Выдали меньше — долг клиента посчитается на выданное."},
         "Сколько выдали", fallback=[names.get(c, c) for c, *_ in ls])
    return Reply(view=v, buttons=[[Button("‹ Все выдачи", "ow")]])


@screen("owy", module=M)
def give_done(ctx, oid=None):
    if not A.keeper(ctx):
        return Reply(text="Выдаёт склад.")
    o = ctx.db.one("SELECT * FROM opt_orders WHERE id = ? AND status = 'wait'", int(oid)) if str(oid or "").isdigit() else None
    if not o:
        return give(ctx)
    f = ctx.form or {}
    ls = json.loads(o["lines"])
    got = []
    for i, (c, n, p, w) in enumerate(ls):
        raw = str(f.get(f"l{i}", "")).replace(" ", "").replace(",", ".")
        try:
            x = float(raw)
        except ValueError:
            return give(ctx, oid, f"строка {i + 1}: впишите число (0 — не выдали)")
        if x < 0 or x > n or x != int(x):
            return give(ctx, oid, f"строка {i + 1}: от 0 до {A._num(n)} целым числом")
        got.append(int(x))
    db, t = ctx.db, ctx.today.isoformat()
    gcode = {r["name"]: r["code"] for r in db.q("SELECT code, name FROM groups")}
    goods = {g["code"]: g for g in db.q("SELECT code, grp FROM goods")}
    key = _key(db, o["manager"])
    new, by_g = [], {}
    for (c, n, p, w), x in zip(ls, got):
        if x:
            SH.move_qty(db, c, w, -x)
            k = gcode.get((goods.get(c) or {}).get("grp", ""), "")
            a, q = by_g.get(k, (0, 0))
            by_g[k] = (a + p * x, q + x)
        new.append([c, x, p, w])
    total = round(sum(p * x for c, x, p, w in new), 2)
    if key:
        for k, (a, q) in by_g.items():                             # строки продаж «из приложения» — план и % менеджера опта
            db.x("INSERT INTO sales (batch, day, emp_key, client, grp, amount, qty, discount) VALUES (0,?,?,?,?,?,?,0)",
                 t, key, f"O{o['client']}", k, round(a, 2), q)
    short = [f"{A._names(db).get(c, c)} — {A._num(n - x)} шт" for (c, n, p, w), x in zip(ls, got) if x < n]
    db.x("UPDATE opt_orders SET status = 'done', lines = ?, total = ?, given_ts = ?, given_by = ?, note = ? WHERE id = ?",
         json.dumps(new), total, datetime.now().isoformat(timespec="seconds"), A._who(ctx),
         (o["note"] + (" · " if o["note"] else "") + "не выдали: " + ", ".join(short)) if short else o["note"], o["id"])
    sync_debts(ctx)
    out = [order_card(ctx, o["id"])]
    m = db.employee(o["manager"]) if o["manager"] else None
    if m and m.get("tg_id"):
        out.append(Reply(text=f"📤 {no(o['id'])} выдана на {money(total)}" + (f". Не выдали: {', '.join(short)}." if short else "."),
                         buttons=[[Button("🧾 Накладная", f"oo:{o['id']}")]], to=m["tg_id"]))
    return out


# ======================= клиенты и оплаты =======================

@screen("oc", module=M)
def clients(ctx, cid=None, note=None):
    if not (opt_ok(ctx) or sees_all(ctx)):
        return Reply(text="Оптовых клиентов видят менеджеры опта, директор и бухгалтер.")
    cl = _client(ctx.db, cid) if cid else None
    if cl and (sees_all(ctx) or cl["manager"] == (ctx.me or {}).get("id")):
        return client_card(ctx, cl, note)
    rows = []
    tot = late_tot = 0
    for c in my_clients(ctx):
        debt, late, _, _ = ledger(ctx.db, c["id"], ctx.today)
        tot += max(debt, 0)
        late_tot += late
        m = ctx.db.employee(c["manager"]) if c["manager"] else None
        rows.append({"t": c["name"], "s": (f"просрочено {money(late)}" if late else "без просрочки") + (f" · {m['fio']}" if m and sees_all(ctx) else ""),
                     "v": money(debt), "chip": ["открыть", f"oc:{c['id']}", "bad" if late else ""]})
    v = View("🏢 Оптовые клиенты", f"{len(rows)} клиентов")
    v.ui("hero", {"label": "Должны сейчас", "big": money(tot), "md": True, "id": "oc",
                  "pairs": [["Просрочено", money(late_tot), "bad" if late_tot else "good"]], "acts": []}, fallback=[f"Должны: {money(tot)}"])
    v.ui("rows", {"items": sorted(rows, key=lambda r: r["chip"][2] != "bad"), "empty": "Клиентов пока нет — заводятся в «🧾 Продажа оптом»"}, "")
    return Reply(view=v, buttons=[[Button("🧾 Продажа оптом", "os")]] if opt_ok(ctx) else [])


def client_card(ctx, cl, note=None):
    debt, late, b, orders = ledger(ctx.db, cl["id"], ctx.today)
    m = ctx.db.employee(cl["manager"]) if cl["manager"] else None
    v = View(f"🏢 {cl['name']}", f"менеджер: {m['fio']}" if m else "")
    if note:
        v.note(note)
    v.ui("hero", {"label": "Должен сейчас", "big": money(debt), "md": True, "id": f"oc{cl['id']}",
                  "sub": f"из них просрочено {money(late)}", "pairs": [["До 30 дн", money(b[0]), ""], ["31–60", money(b[1]), ""],
                                                                     ["61+", money(b[2] + b[3]), "bad" if b[2] + b[3] else ""]],
                  "acts": [["💵 Принять оплату", f"op:{cl['id']}"]] if can_pay(ctx, cl) else []}, fallback=[f"Должен: {money(debt)}"])
    wait = ctx.db.q("SELECT * FROM opt_orders WHERE client = ? AND status = 'wait' ORDER BY id DESC", cl["id"])
    items = [{"t": f"{no(o['id'])} · {A._dm(o['day'])}", "s": "ждёт выдачи на складе", "v": money(o["total"]),
              "chip": ["открыть", f"oo:{o['id']}", "warn"]} for o in wait]
    for o in reversed(orders[-15:]):
        lt = o["rest"] > 0 and o["due"] < ctx.today.isoformat()
        items.append({"t": f"{no(o['id'])} · {A._dm(o['day'])}", "s": ("оплачено" if o["rest"] <= 0 else
                      f"осталось {money(o['rest'])} · оплатить до {A._dm(o['due'])}" + (" · просрочено" if lt else "")),
                      "v": money(o["total"]), "chip": ["открыть", f"oo:{o['id']}", "bad" if lt else "good" if o["rest"] <= 0 else ""]})
    v.ui("rows", {"items": items, "empty": "Накладных ещё нет"}, "Накладные")
    pays = ctx.db.q("SELECT * FROM opt_pay WHERE client = ? ORDER BY id DESC LIMIT 10", cl["id"])
    v.ui("rows", {"items": [{"t": f"{A._dm(p['day'])}" + (f" · {p['how']}" if p["how"] else ""), "s": f"принял {p['who']}"
                             + (f" · {p['note']}" if p["note"] else ""), "v": money(p["amount"])} for p in pays], "empty": "Оплат ещё не было"}, "Оплаты")
    v.note("Оплата закрывает самые старые накладные первыми. Долг и просрочка идут в дебиторку и в зарплату менеджера.")
    kb = [[Button("‹ Все клиенты", "oc")]]
    if opt_ok(ctx):
        kb.append([Button("🧾 Продать ему", f"os:cl:{cl['id']}")])
    return Reply(view=v, buttons=kb)


@screen("op", module=M)
def pay_form(ctx, cid=None, err=""):
    cl = _client(ctx.db, cid or 0)
    if not cl or not can_pay(ctx, cl):
        return clients(ctx)
    debt = ledger(ctx.db, cl["id"], ctx.today)[0]
    v = View("💵 Оплата", f"{cl['name']} · должен {money(debt)}")
    v.ui("sform", {"fields": [{"k": "sum", "t": "num", "l": "Сколько принято, ₽", "v": "", "req": 1},
                              {"k": "how", "t": "seg", "l": "Как", "v": "нал", "opts": [["нал", "Наличными"], ["перевод", "Переводом"], ["карта", "Картой"]]},
                              {"k": "note", "t": "text", "l": "Комментарий", "v": ""}],
                   "send": ["✅ Принять", f"opy:{cl['id']}"], "err": err, "top": [], "note": "Закроет самые старые накладные первыми."},
         "", fallback=["Сколько принято"])
    return Reply(view=v, buttons=[[Button("‹ Клиент", f"oc:{cl['id']}")]])


@screen("opy", module=M)
def pay_save(ctx, cid=None):
    cl = _client(ctx.db, cid or 0)
    if not cl or not can_pay(ctx, cl):
        return clients(ctx)
    f = ctx.form or {}
    x = A._parse_num(f.get("sum", ""))
    if not x:
        return pay_form(ctx, cid, "сумма — число больше нуля")
    add_pay(ctx, cl, x, " ".join(str(f.get("note", "")).split())[:200])
    ctx.db.x("UPDATE opt_pay SET how = ? WHERE id = (SELECT MAX(id) FROM opt_pay WHERE client = ?)",
             f.get("how") if f.get("how") in ("нал", "перевод", "карта") else "", cl["id"])
    ctx.db.audit(A._who(ctx), "opt_pay", f"{cl['name']}: {money(x)}")
    return client_card(ctx, cl, f"✅ Принято {money(x)}.")
