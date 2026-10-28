"""Модуль «Склад для закупок»: остатки по поставщикам, что закончилось и что скоро закончится, что лежит
без движения; заказ поставщику — черновик сам из «нужно», правка текстом, Excel, «в пути», «пришёл».

Данные: последний срез остатков (stock) и продажи по товарам (item_sales).
  скорость    продано за окно stock.window дней (по умолчанию 60) ÷ дней окна
  дней запаса остаток ÷ скорость
  🔴 нет       остатка нет, а за окно товар продавался
  🟠 скоро     запаса меньше stock.low_days дней (по умолчанию 14)
  💤 залежалось остаток есть, продаж нет stock.stale_days дней (по умолчанию 90; если история короче — не считаем)
  нужно       скорость × stock.cover_days (30) − остаток − «в пути» (заказы, отправленные поставщику, до 45 дней)
Видят: руководство, админ и отдел закупок.
"""
import io
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.calendar import day_title
from core.jobs.schedule import job
from core.ui.kit import Button, Reply, money, num, rows
from core.ui.registry import free_text, menu, screen, text_input
from core.ui.views import Card, Cell, Row, View

M = "stock"
CAT = {"out": ("Закончилось", "bad"), "low": ("Скоро закончится", "warn"), "stale": ("Залежалось", None)}
TRANSIT_DAYS = 45


def ok(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "purchasing"


menu("sk", "📦 Склад", {"owner", "admin", "head", "staff"}, module=M, order=30, when=ok)


def _cfg(ctx):
    s = ctx.cfg.raw.get("stock") or {}
    return {k: int(s.get(k, d)) for k, d in (("window", 60), ("low_days", 14), ("stale_days", 90), ("cover_days", 30))}


class Stock:
    def __init__(self, ctx):
        self.ctx = ctx
        c = _cfg(ctx)
        self.c = c
        r = ctx.db.one('SELECT MAX("on") AS d FROM stock WHERE "on" <= ?', ctx.today)
        self.on = r["d"] if r and r["d"] else None
        self.items = {x["code"]: x for x in ctx.db.q('SELECT * FROM stock WHERE "on" = ?', self.on)} if self.on else {}
        start = (ctx.today - timedelta(days=c["window"])).isoformat()
        self.sold = defaultdict(float)
        self.amount = defaultdict(float)
        for x in ctx.db.q("SELECT code, SUM(qty) AS q, SUM(amount) AS a FROM item_sales WHERE day > ? AND day <= ? GROUP BY code",
                          start, ctx.today):
            self.sold[x["code"]], self.amount[x["code"]] = x["q"], x["a"]
        first = ctx.db.one("SELECT MIN(day) AS d FROM item_sales")
        self.history = (ctx.today - date.fromisoformat(first["d"])).days if first and first["d"] else 0
        self.last = {x["code"]: x["d"] for x in ctx.db.q("SELECT code, MAX(day) AS d FROM item_sales WHERE qty > 0 GROUP BY code")}
        self.days = min(c["window"], max(self.history, 1))
        self.transit = defaultdict(float)
        since = (ctx.today - timedelta(days=TRANSIT_DAYS)).isoformat()
        for o in ctx.db.q("SELECT items FROM stock_orders WHERE status = 'sent' AND sent_ts >= ?", since):
            for code, q in json.loads(o["items"]):
                self.transit[code] += q

    def speed(self, code):
        return self.sold.get(code, 0) / self.days

    def days_left(self, code):
        s = self.speed(code)
        return self.items[code]["qty"] / s if s > 0 else None

    def price(self, code):
        q = self.sold.get(code, 0)
        return self.amount.get(code, 0) / q if q else 0

    def cat(self, code):
        it = self.items[code]
        if it["qty"] <= 0 and self.sold.get(code, 0) > 0:
            return "out"
        d = self.days_left(code)
        if d is not None and d < self.c["low_days"]:
            return "low"
        if it["qty"] > 0 and self.history >= self.c["stale_days"]:
            last = self.last.get(code)
            if not last or (self.ctx.today - date.fromisoformat(last)).days >= self.c["stale_days"]:
                return "stale"
        return None

    def need(self, code, transit=True):
        n = self.speed(code) * self.c["cover_days"] - max(self.items[code]["qty"], 0) - (self.transit.get(code, 0) if transit else 0)
        return max(round(n), 0)

    def suppliers(self):
        out = defaultdict(list)
        for code, it in self.items.items():
            out[it["supplier"] or "Без поставщика"].append(code)
        return sorted(out.items(), key=lambda t: t[0])


def load(ctx):
    return ctx.app.memo(ctx, "stock", lambda: Stock(ctx), month="-")


def _unit(it):
    return it.get("unit") or "шт"


@screen("sk", module=M)
def stock(ctx):
    if not ok(ctx):
        return Reply(text="Склад видят руководство и отдел закупок.")
    s = load(ctx)
    if not s.on:
        return Reply(text="Остатков пока нет — пришлите отчёт 1С «Остатки товаров».")
    cats = defaultdict(list)
    for code in s.items:
        c = s.cat(code)
        if c:
            cats[c].append(code)
    v = View("📦 Склад", f"остатки на {day_title(date.fromisoformat(s.on))} · товаров {len(s.items)}")
    stale_sum = sum(s.items[c]["qty"] * (s.price(c) or 0) for c in cats["stale"])
    v.tiles([("Закончилось", str(len(cats["out"])), "bad" if cats["out"] else "good"),
             ("Скоро закончится", str(len(cats["low"])), "warn" if cats["low"] else "good"),
             ("Залежалось", str(len(cats["stale"])) if s.history >= s.c["stale_days"] else "—", None)])
    rs = []
    for i, (sup, codes) in enumerate(s.suppliers()):
        out_ = sum(1 for c in codes if s.cat(c) == "out")
        low = sum(1 for c in codes if s.cat(c) == "low")
        need = sum(1 for c in codes if s.need(c) > 0)
        rs.append(Row([sup, str(len(codes)), Cell(str(out_), "bad" if out_ else None), Cell(str(low), "warn" if low else None),
                       str(need)]))
    v.table(["Поставщик", "Товаров", "Нет", "Скоро", "Заказать"], rs, title="По поставщикам")
    if stale_sum:
        v.note(f"В залежалом товаре — около {money(stale_sum)} по цене продажи.")
    kb = [[Button(f"🔴 Нет ({len(cats['out'])})", "skf:out"), Button(f"🟠 Скоро ({len(cats['low'])})", "skf:low")],
          [Button(f"💤 Залежалось ({len(cats['stale'])})", "skf:stale"), Button("🛒 Заказы", "sol")]]
    kb += rows([Button(sup[:24], f"sks:{i}") for i, (sup, _) in enumerate(s.suppliers())], 2)
    v.note("Поиск товара: напишите боту его код или часть названия.", where="all")
    return Reply(view=v, buttons=kb)


def _item_card(s, code):
    it = s.items[code]
    d = s.days_left(code)
    c = s.cat(code)
    lines = [f"остаток {num(it['qty'])} {_unit(it)} · продаётся {num(round(s.speed(code) * 30))} в месяц",
             f"запаса {'на ' + num(round(d)) + ' дн.' if d is not None else '—'} · нужно заказать {num(s.need(code))}"]
    if s.transit.get(code):
        lines.append(f"в пути {num(s.transit[code])}")
    return Card(f"{it['name']}", f"код {code}", lines, color=CAT[c][1] if c else None)


@screen("skf", module=M)
def by_cat(ctx, cat):
    if not ok(ctx) or cat not in CAT:
        return Reply(text="Нет доступа.")
    s = load(ctx)
    codes = [c for c in s.items if s.cat(c) == cat]
    if cat == "stale":
        codes.sort(key=lambda c: -s.items[c]["qty"])
    else:
        codes.sort(key=lambda c: -s.speed(c))
    v = View(f"📦 {CAT[cat][0]}", f"{len(codes)} товаров")
    v.cards([_item_card(s, c) for c in codes[:15]])
    if not codes:
        v.note("Таких товаров нет 👍")
    return Reply(view=v, buttons=[[Button("‹ Склад", "sk")]])


@screen("sks", module=M)
def supplier(ctx, i):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    s = load(ctx)
    sups = s.suppliers()
    if int(i) >= len(sups):
        return stock(ctx)
    sup, codes = sups[int(i)]
    order = {"out": 0, "low": 1, None: 2, "stale": 3}
    codes = sorted(codes, key=lambda c: (order[s.cat(c)], -s.need(c)))
    v = View(f"📦 {sup}", f"товаров {len(codes)} · к заказу {sum(1 for c in codes if s.need(c) > 0)}")
    rs = []
    for c in codes[:40]:
        it, d, ct = s.items[c], s.days_left(c), s.cat(c)
        rs.append(Row([it["name"][:34], num(it["qty"]), Cell(num(round(d)) if d is not None else "—", CAT[ct][1] if ct else None),
                       num(s.need(c))], sub=f"код {c}" + (f" · в пути {num(s.transit[c])}" if s.transit.get(c) else "")))
    v.table(["Товар", "Остаток", "Дней", "Нужно"], rs)
    if len(codes) > 40:
        v.note(f"Показаны 40 из {len(codes)}, сначала срочные.")
    return Reply(view=v, buttons=[[Button("🛒 Заказ этому поставщику", f"son:{i}")], [Button("‹ Склад", "sk")]])


# ---------- заказы ----------

def _order(ctx, oid):
    o = ctx.db.one("SELECT * FROM stock_orders WHERE id = ?", int(oid))
    if o:
        o["items"] = json.loads(o["items"])
    return o


@screen("son", module=M)
def new_order(ctx, i):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    s = load(ctx)
    sups = s.suppliers()
    if int(i) >= len(sups):
        return stock(ctx)
    sup, codes = sups[int(i)]
    draft = ctx.db.one("SELECT id FROM stock_orders WHERE supplier = ? AND status = 'draft'", sup)
    if draft:
        return order(ctx, draft["id"])
    items = [[c, s.need(c)] for c in codes if s.need(c) > 0 and s.sold.get(c, 0) > 0]
    oid = ctx.db.x("INSERT INTO stock_orders (supplier, items, author, ts) VALUES (?,?,?,?)", sup, json.dumps(items),
                   ctx.author(), datetime.now().isoformat(timespec="seconds"))
    return order(ctx, oid)


ST = {"draft": "черновик", "sent": "🚚 в пути", "came": "📦 пришёл"}


@screen("sko", module=M)
def order(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o:
        return Reply(text="Нет доступа.")
    s = load(ctx)
    v = View(f"🛒 Заказ №{o['id']} · {o['supplier']}", f"{ST[o['status']]} · {o['author']}")
    total = 0
    rs = []
    for code, q in o["items"]:
        it = s.items.get(code, {"name": code, "unit": "шт"})
        p = s.price(code) if code in s.items else 0
        total += p * q
        rs.append(Row([it["name"][:34], f"{num(q)} {_unit(it)}", money(p * q, "") if p else "—"], sub=f"код {code}"))
    v.table(["Товар", "Кол-во", "≈ ₽"], rs) if rs else v.note("Пусто. Нажмите «✏️ Изменить» и впишите товары.")
    if total:
        v.note(f"Примерно на {money(total)} по цене продажи (закупочная — ниже).")
    kb = []
    if o["status"] == "draft":
        kb.append([Button("✏️ Изменить", f"soe:{o['id']}"), Button("📨 Отправлен поставщику", f"sos:{o['id']}")])
    if o["status"] == "sent":
        kb.append([Button("📦 Пришёл", f"soc:{o['id']}")])
    kb.append([Button("📥 Excel", f"sox:{o['id']}"), Button("‹ Заказы", "sol")])
    return Reply(view=v, buttons=kb)


@screen("soe", module=M)
def order_edit(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o or o["status"] != "draft":
        return Reply(text="Менять можно только черновик.")
    return ctx.ask("stock_ord", "Напишите товары строками «код количество» (0 — убрать):\n\n100012 40\n100031 0",
                   {"id": o["id"]})


@text_input("stock_ord")
def on_edit(ctx, text, data):
    o = _order(ctx, data["id"])
    items = dict((c, q) for c, q in o["items"])
    for line in (text or "").splitlines():
        p = line.replace(",", ".").split()
        if not p:
            continue
        try:
            code, q = p[0], float(p[1]) if len(p) > 1 else None
        except ValueError:
            return ctx.ask("stock_ord", f"Не понял «{line.strip()}». Пишите «код количество».", data)
        if q is None:
            return ctx.ask("stock_ord", f"В строке «{line.strip()}» нет количества.", data)
        if q <= 0:
            items.pop(code, None)
        else:
            items[code] = q
    ctx.db.x("UPDATE stock_orders SET items = ? WHERE id = ?", json.dumps([[c, q] for c, q in items.items()]), o["id"])
    return order(ctx, o["id"])


@screen("sos", module=M)
def order_sent(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o or o["status"] != "draft":
        return Reply(text="Заказ уже отправлен.")
    ctx.db.x("UPDATE stock_orders SET status = 'sent', sent_ts = ? WHERE id = ?", ctx.today, o["id"])
    ctx.db.audit(ctx.author(), "stock_order", f"№{o['id']} {o['supplier']} отправлен")
    return [Reply(text=f"🚚 Заказ №{o['id']} в пути — бот вычтет его из «нужно» до прихода (или {TRANSIT_DAYS} дней)."),
            order(ctx, oid)]


@screen("soc", module=M)
def order_came(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o or o["status"] != "sent":
        return Reply(text="Этот заказ не в пути.")
    ctx.db.x("UPDATE stock_orders SET status = 'came', came_ts = ? WHERE id = ?", ctx.today, o["id"])
    return order(ctx, oid)


@screen("sol", module=M)
def orders(ctx):
    if not ok(ctx):
        return Reply(text="Нет доступа.")
    rs = ctx.db.q("SELECT * FROM stock_orders ORDER BY id DESC LIMIT 20")
    v = View("🛒 Заказы поставщикам", f"последние {len(rs)}")
    v.table(["№", "Поставщик", "Статус"], [Row([str(o["id"]), o["supplier"][:28], ST[o["status"]]], sub=o["ts"][:10]) for o in rs]) \
        if rs else v.note("Заказов пока нет. Откройте поставщика → «🛒 Заказ».")
    return Reply(view=v, buttons=rows([Button(f"№{o['id']}", f"sko:{o['id']}") for o in rs[:12]], 4) + [[Button("‹ Склад", "sk")]])


@screen("sox", module=M)
def order_xlsx(ctx, oid):
    o = _order(ctx, oid)
    if not ok(ctx) or not o:
        return Reply(text="Нет доступа.")
    from openpyxl import Workbook
    from openpyxl.styles import Font
    s = load(ctx)
    wb = Workbook()
    ws = wb.active
    ws.title = "Заказ"
    ws.append([f"Заказ №{o['id']} · {o['supplier']} · {ctx.cfg.name} · {ctx.today:%d.%m.%Y}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(["Код", "Товар", "Количество", "Ед."])
    for c in ws[3]:
        c.font = Font(bold=True)
    for code, q in o["items"]:
        it = s.items.get(code, {"name": code, "unit": "шт"})
        ws.append([code, it["name"], q, _unit(it)])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 12, 50
    ws2 = wb.create_sheet("Для нас")
    ws2.append(["Код", "Товар", "Остаток", "Продаётся в месяц", "Дней запаса", "В заказе"])
    for code, q in o["items"]:
        if code in s.items:
            d = s.days_left(code)
            ws2.append([code, s.items[code]["name"], s.items[code]["qty"], round(s.speed(code) * 30),
                        round(d) if d is not None else None, q])
    buf = io.BytesIO()
    wb.save(buf)
    return Reply(text=f"📥 Заказ №{o['id']}", files=[(f"Заказ {o['id']} {o['supplier'][:30]}.xlsx", buf.getvalue())])


# ---------- поиск ----------

@free_text(module=M, order=30)
def find(ctx, text):
    t = (text or "").strip()
    if not ok(ctx) or len(t) < 3 or len(t) > 40:
        return None
    s = load(ctx)
    if not s.items:
        return None
    if t in s.items:
        v = View("🔎 Товар", s.items[t]["supplier"])
        v.cards([_item_card(s, t)])
        return Reply(view=v, buttons=[[Button("‹ Склад", "sk")]])
    if t.isdigit():
        return None
    found = [c for c, it in s.items.items() if t.lower() in it["name"].lower()]
    if not found:
        return None
    v = View(f"🔎 «{t}»", f"найдено {len(found)}")
    v.cards([_item_card(s, c) for c in found[:10]])
    return Reply(view=v, buttons=[[Button("‹ Склад", "sk")]])


# ---------- рассылки ----------

@job("stock_morning", "morning", {"owner", "head", "staff"}, "Утром: что закончилось на складе", module=M)
def morning(ctx):
    if not ok(ctx) or ctx.role == "staff":
        return None
    s = load(ctx)
    if not s.items:
        return None
    out_ = [c for c in s.items if s.cat(c) == "out"]
    low = [c for c in s.items if s.cat(c) == "low"]
    if not out_ and not low:
        return None
    lines = [f"🔴 Закончилось: {len(out_)}", f"🟠 Скоро закончится: {len(low)}"]
    lines += [f"• {s.items[c]['name']}" for c in sorted(out_, key=lambda c: -s.speed(c))[:5]]
    return Reply(text="📦 Склад сегодня\n" + "\n".join(lines), buttons=[[Button("📦 Склад", "sk")]])
