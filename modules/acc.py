"""📦 Учёт в Алапе (Адам 20.11, вариант «вместо 1С», без бухгалтерии и отчётов для налоговой): товары, приход от поставщика,
приёмка складом по количеству, себестоимость по средней цене приходов — и 🔒 закрытые цифры.

Закрытые цифры — себестоимость, цены закупки, суммы приходов, наценка, маржа. Видят:
  владелец (роль owner) — всегда;
  сотрудники, которым владелец включил доступ (ключ базы `secret` → people);
  поддержка Алапы (администратор по ссылке, без своей карточки сотрудника) — только если владелец разрешил, только открыв
  на 30 минут с причиной; владельцу сразу уведомление, в журнале — кто, когда, зачем.
Каждый просмотр закрытого не владельцем пишется в журнал `secret_log` (не чаще раза в час на одно и то же).
Честно: от того, кто управляет сервером, программа базу не спрячет — замок и журнал работают на всё, что открывается через
приложение. Шифрование ключом владельца — отдельным шагом по запросу клиента.

Таблицы (миграция 8): goods (код, название, группа, цена продажи, себестоимость, остаток, поставщик), receipts (приход:
номер, день, поставщик, draft → sent → done, оплата debt / paid, строки [[код, шт, цена закупки, принято]]), secret_log.
"""
import json
from datetime import date, datetime, timedelta

from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.views import View

M = "stock"
OPEN_MIN = 30           # поддержка открывает закрытые цифры на 30 минут
LOG_EVERY = 60          # один и тот же просмотр одного человека — в журнал не чаще раза в час
LOCK = "🔒"


# ======================= 🔒 кто видит закрытые цифры =======================

def settings(db):
    s = db.get("secret") or {}
    return {"people": [int(x) for x in s.get("people") or []], "support": s.get("support", True), "open": s.get("open") or {}}


def save_settings(db, s):
    db.put("secret", s)


def is_support(ctx):
    """Поддержка Алапы: вошла по ссылке администратора, своей карточки сотрудника нет."""
    return ctx.role == "admin" and not ctx.me


def is_owner(ctx):
    return ctx.role == "owner"


def support_open(ctx):
    o = settings(ctx.db)["open"]
    try:
        return bool(o) and datetime.fromisoformat(o["until"]) > datetime.now()
    except (KeyError, ValueError):
        return False


def can_see(ctx, what=None):
    """Видит ли этот человек закрытые цифры. what — что открыл (для журнала); владелец в журнал не пишется."""
    if is_owner(ctx):
        return True
    s = settings(ctx.db)
    if is_support(ctx):
        ok = s["support"] and support_open(ctx)
    else:
        ok = bool(ctx.me) and ctx.me["id"] in s["people"]
    if ok and what:
        log(ctx, what)
    return ok


def _who(ctx):
    if is_support(ctx):
        return "Поддержка Алапы"
    if ctx.me:
        p = ctx.me["fio"].split()
        return f"{p[0]} {p[1][0]}." if len(p) > 1 else ctx.me["fio"]
    return "—"


def log(ctx, what, why="", force=False):
    who = _who(ctx)
    now = datetime.now()
    if not force:
        last = ctx.db.one("SELECT ts FROM secret_log WHERE who = ? AND what = ? ORDER BY id DESC LIMIT 1", who, what)
        if last and now - datetime.fromisoformat(last["ts"]) < timedelta(minutes=LOG_EVERY):
            return
    ctx.db.x("INSERT INTO secret_log (ts, who, role, what, why, support) VALUES (?,?,?,?,?,?)", now.isoformat(timespec="seconds"),
             who, ctx.position_name or ctx.role, what, why[:300], int(is_support(ctx)))


def _owners(ctx):
    return [e for e in ctx.db.employees() if e["role"] == "owner"]


def closed(ctx, x, fmt=money):
    """Цифра или замок."""
    return fmt(x) if can_see(ctx) else LOCK


def support_block(ctx, v):
    """Поддержке — замок с объяснением и «открыть на 30 минут с причиной» (если владелец разрешил)."""
    if not is_support(ctx) or support_open(ctx):
        return
    if not settings(ctx.db)["support"]:
        v.ui("rows", {"items": [{"t": "🔒 Владелец выключил доступ поддержки", "s": "закрытые цифры может показать только он сам"}]}, "")
        return
    v.ui("sform", {"fields": [{"k": "why", "t": "area", "l": "Зачем открываете? (обязательно)", "req": 1, "max": 300,
                               "ph": "Например: настраиваем приход по просьбе владельца"}],
                   "send": [f"🔓 Открыть на {OPEN_MIN} минут и записать", "seco"], "err": "",
                   "note": "Это закрытые цифры клиента. Владелец сразу получит уведомление и увидит в журнале: кто, когда, на сколько и зачем.",
                   "top": []}, "🔒 Закрытые цифры клиента")


@screen("seco", module=M)
def support_unlock(ctx):
    if not is_support(ctx):
        return goods(ctx)
    s = settings(ctx.db)
    why = str((ctx.form or {}).get("why", "")).strip()
    if not s["support"]:
        return goods(ctx)
    if len(why) < 5:
        r = goods(ctx)
        r.text = "Напишите, зачем открываете, — хотя бы несколько слов. Владелец увидит эту причину."
        return r
    until = datetime.now() + timedelta(minutes=OPEN_MIN)
    s["open"] = {"until": until.isoformat(timespec="seconds"), "why": why}
    save_settings(ctx.db, s)
    log(ctx, f"открыла закрытые цифры на {OPEN_MIN} мин", why, force=True)
    out = [goods(ctx)]
    for o in _owners(ctx):
        if o.get("tg_id"):
            out.append(Reply(text=f"🔒 Поддержка Алапы открыла закрытые цифры на {OPEN_MIN} минут.\nПричина: «{why}»\n"
                                  "Запись — в «🔒 Закрытые цифры». Там же можно выключить доступ поддержки совсем.",
                             buttons=[[Button("🔒 Закрытые цифры", "sec")]], to=o["tg_id"]))
    return out


# ======================= владелец: кто видит и журнал =======================

def _candidates(ctx, s):
    """Кому владелец может включить: руководители, бухгалтер, закупки, директора магазинов — и все, у кого уже включено."""
    out = []
    for e in ctx.db.employees():
        if e["role"] == "owner":
            continue
        d = ctx.cfg.dept(e["dept"])
        if e["id"] in s["people"] or e["role"] in ("head", "accountant", "admin") or (d and d.kind == "purchasing") \
                or (ctx.cfg.position(e["position"]) or {}).get("duty") == "shop":
            out.append(e)
    return sorted(out, key=lambda e: (e["id"] not in s["people"], e["fio"]))


@screen("sec", module=M)
def secret_screen(ctx, act=None, arg=None):
    if not is_owner(ctx):
        return Reply(text="🔒 Закрытые цифры настраивает только владелец фирмы.")
    s = settings(ctx.db)
    if act == "t" and arg:
        if arg == "sup":
            s["support"] = not s["support"]
            if not s["support"]:
                s["open"] = {}
        elif arg.isdigit() and ctx.db.employee(int(arg)):
            i = int(arg)
            s["people"] = [x for x in s["people"] if x != i] if i in s["people"] else s["people"] + [i]
        save_settings(ctx.db, s)
    v = View("🔒 Закрытые цифры", "себестоимость, цены закупки, наценка, маржа")
    v.ui("rows", {"items": [{"t": "Что закрыто", "s": "себестоимость, цены и суммы закупки, наценка, маржа, прибыль. "
                                                      "Кроме вас — только те, кому вы включили."}]}, "")
    items = []
    for e in _candidates(ctx, s):
        on = e["id"] in s["people"]
        p = e["fio"].split()
        items.append({"t": f"{p[0]} {p[1][0]}. · {(ctx.cfg.position(e['position']) or {}).get('name', '')}",
                      "s": "видит закрытые цифры" if on else "не видит",
                      "chip": ["✓ включено" if on else "выключено", f"sec:t:{e['id']}", "good" if on else ""]})
    sup = s["support"]
    items.append({"t": "Поддержка Алапы", "s": "только открыв на 30 минут с причиной — вам придёт уведомление" if sup else
                  "не может открыть совсем", "chip": ["✓ разрешено" if sup else "запрещено", "sec:t:sup", "good" if sup else "bad"]})
    v.ui("rows", {"items": items, "right": "нажмите, чтобы включить / выключить"}, "Кто видит")
    rows = []
    for r in ctx.db.q("SELECT * FROM secret_log ORDER BY id DESC LIMIT 25"):
        t = datetime.fromisoformat(r["ts"])
        day = "сегодня" if t.date() == date.today() else "вчера" if t.date() == date.today() - timedelta(days=1) else t.strftime("%d.%m")
        rows.append({"t": f"{r['who']} · {day} {t.strftime('%H:%M')}", "s": r["what"] + (f" · «{r['why']}»" if r["why"] else ""),
                     "chip": ["поддержка", "", "bad"] if r["support"] else None})
    for x in rows:
        if not x["chip"]:
            x.pop("chip")
    v.ui("rows", {"items": rows, "empty": "Пока никто, кроме вас, закрытые цифры не открывал"}, "Кто смотрел")
    return Reply(view=v)


# ======================= товары =======================

def goods_ok(ctx):
    d = ctx.cfg.dept(ctx.me["dept"]) if ctx.me else None
    return ctx.role in ("owner", "admin", "accountant") or (d and d.kind in ("purchasing", "warehouse")) \
        or ctx.duty in ("seller", "shop")


def receipts_ok(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind in ("purchasing", "warehouse")


def buyer(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "purchasing"


def keeper(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "warehouse"


menu("gd", "📦 Товары", {"owner", "admin", "head", "staff", "accountant", "rep", "sv"}, module=M, order=27,
     when=lambda c: goods_ok(c) and bool(c.db.one("SELECT 1 AS x FROM goods LIMIT 1")))
menu("rcl", "📥 Приходы от поставщиков", {"owner", "admin", "head", "staff"}, module=M, order=27,
     when=lambda c: receipts_ok(c) and bool(c.db.one("SELECT 1 AS x FROM goods LIMIT 1")))
menu("sec", "🔒 Закрытые цифры", {"owner"}, module=M, order=26)


def _margin_month(ctx):
    """Маржа месяца: продажи по товарам − штуки × себестоимость (только по товарам учёта)."""
    cost = {r["code"]: r["cost"] for r in ctx.db.q("SELECT code, cost FROM goods")}
    s = ctx.month + "-01"
    e = ctx.month + "-31"
    sale = got = 0
    for r in ctx.db.q("SELECT code, SUM(qty) AS q, SUM(amount) AS a FROM item_sales WHERE day BETWEEN ? AND ? GROUP BY code", s, e):
        if r["code"] in cost:
            sale += r["a"] or 0
            got += (r["a"] or 0) - (r["q"] or 0) * cost[r["code"]]
    return sale, got


def _tone(mk):
    return "good" if mk >= 0.45 else "warn" if mk >= 0.35 else "bad"


def _num(x):
    return money(x, "")


def short_name(n):
    """«Декор и свет · эконом» → «Декор · эконом» — чтобы влезло в узкую колонку на телефоне."""
    if " · " in n:
        a, b = n.split(" · ", 1)
        return f"{a.split()[0]} · {b}"
    return n


@screen("gd", module=M)
def goods(ctx, flt=None):
    if not goods_ok(ctx):
        return Reply(text="Товары видят руководство, закупки, склад и магазины.")
    see = can_see(ctx, "товары с себестоимостью")
    q = str((ctx.form or {}).get("q", "")).strip().lower()[:60]
    rows = ctx.db.q("SELECT * FROM goods WHERE off = 0 ORDER BY code")
    stock_cost = sum(r["qty"] * r["cost"] for r in rows)
    stock_price = sum(r["qty"] * r["price"] for r in rows)
    v = View("📦 Товары", f"{len(rows)} товаров")
    if see:
        sale, mg = _margin_month(ctx)
        v.ui("hero", {"label": f"Склад сейчас · по себестоимости {LOCK}", "big": money(stock_cost), "md": True,
                      "sub": f"в ценах продажи {money(stock_price)} · {len(rows)} товаров", "id": "gd",
                      "pairs": [[f"Наценка средняя {LOCK}", pct(stock_price / stock_cost - 1) if stock_cost else "—", ""],
                                [f"Маржа за месяц {LOCK}", money(mg), ""]], "acts": []},
             fallback=[f"Склад по себестоимости: {money(stock_cost)}"])
    else:
        v.ui("hero", {"label": "Склад сейчас", "big": f"{len(rows)} товаров", "md": True,
                      "sub": f"в ценах продажи {money(stock_price)}", "id": "gd",
                      "pairs": [["Себестоимость и наценка", LOCK, ""]], "acts": []}, fallback=[f"Товаров: {len(rows)}"])
    support_block(ctx, v)
    v.ui("form", {"fields": [{"k": "q", "l": "🔍 Найти товар", "v": q, "ph": "название или код", "w": True}],
                  "send": ["Найти", f"gd:{flt or ''}"]}, "")
    tabs = [["Все", "gd", not flt], ["Мало или нет", "gd:low", flt == "low"]] + ([["Низкая наценка", "gd:mk", flt == "mk"]] if see else [])
    v.ui("choice", {"items": tabs}, "")
    if q:
        rows = [r for r in rows if q in r["name"].lower() or q == r["code"]]
    if flt == "low":
        rows = sorted([r for r in rows if r["qty"] <= 3], key=lambda r: r["qty"])
    if flt == "mk" and see:
        rows = sorted([r for r in rows if r["cost"]], key=lambda r: r["price"] / r["cost"])
    head = ["Товар", "Остаток", "Цена"] + ([f"Себест. {LOCK}", f"Нац. {LOCK}"] if see else [])
    out = []
    for r in rows[:40]:
        cells = [[_num(r["qty"]), "bad" if r["qty"] <= 0 else ""], [_num(r["price"]), ""]]
        acts = [["📦 Карточка", f"gdi:{r['code']}"]] + ([["✏️ Изменить", f"gde:{r['code']}"]] if buyer(ctx) else [])
        if see:
            mk = r["price"] / r["cost"] - 1 if r["cost"] else 0
            cells += [[_num(r["cost"]), ""], [pct(mk), _tone(mk)]]
        out.append({"n": short_name(r["name"]), "cells": cells, "det": f"{r['name']} · код {r['code']} · {r['supplier']}",
                    "acts": acts})
    if buyer(ctx):
        v.ui("quick", {"items": [["➕ Новый товар", "gde:new"], ["⚙️ Справочники", "rf"], ["📥 Из Excel", "gdu"]]}, "")
    v.ui("team", {"head": head, "rows": out, "right": f"{len(rows)} шт." if len(rows) > 40 else "",
                  "note": ("Себестоимость считается сама: средняя цена всех приходов товара. Пришла партия дороже — себестоимость поднялась."
                           if see else "🔒 Себестоимость и наценка закрыты владельцем.")}, "Товары",
         fallback=[f"{r['name']}: {_num(r['qty'])} шт." for r in rows[:40]])
    return Reply(view=v)


@screen("gdi", module=M)
def goods_card(ctx, code=None):
    g = ctx.db.one("SELECT * FROM goods WHERE code = ?", code or "")
    if not g or not goods_ok(ctx):
        return goods(ctx)
    see = can_see(ctx, f"себестоимость товара «{g['name']}»")
    sold = ctx.db.one("SELECT SUM(qty) AS q FROM item_sales WHERE code = ? AND day >= ?", code, ctx.month + "-01")["q"] or 0
    v = View(g["name"], f"код {g['code']} · {g['grp'] or 'без раздела'}" + (" · в архиве" if g["off"] else ""))
    v.ui("hero", {"label": "Цена розничная", "big": money(g["price"]),
                  "sub": f"оптовая {money(g['price_opt'])} · код {g['code']}" + (f" · {g['supplier']}" if g["supplier"] else ""), "id": f"gdi{code}",
                  "pairs": [["На складе", f"{_num(g['qty'])} {g['unit']}", "bad" if g["qty"] <= 0 else ""],
                            ["Продано за месяц", _num(sold), ""]], "acts": []},
         fallback=[f"Цена: {money(g['price'])}", f"На складе: {_num(g['qty'])}"])
    mk = g["price"] / g["cost"] - 1 if g["cost"] else 0
    if see:
        mo = g["price_opt"] / g["cost"] - 1 if g["cost"] and g["price_opt"] else 0
        items = [{"t": f"Закупочная (себестоимость) {LOCK}", "s": "средняя цена всех приходов", "v": money(g["cost"])},
                 {"t": f"Наценка розницы {LOCK}", "s": f"с одной штуки {money(g['price'] - g['cost'])}", "chip": [pct(mk), "", _tone(mk)]}]
        if g["price_opt"]:
            items.append({"t": f"Наценка опта {LOCK}", "s": f"с одной штуки {money(g['price_opt'] - g['cost'])}", "chip": [pct(mo), "", _tone(mo)]})
    else:
        items = [{"t": "Себестоимость и наценка", "s": "закрыты владельцем", "chip": [LOCK, "", ""]}]
    v.ui("rows", {"items": items}, "")
    support_block(ctx, v)
    from modules import ship as SH
    if SH.active(ctx):
        v.ui("team", {"head": ["Где", "Есть", "Резерв", "Свободно"], "rows": SH.loc_rows(ctx, code),
                      "note": "Резерв — продано с доставкой, ждёт машину. Образец в зале не продаётся."}, "Где лежит")
    hist = []
    for r in ctx.db.q("SELECT * FROM receipts WHERE status = 'done' ORDER BY day DESC, id DESC LIMIT 60"):
        for c, n, price, got in json.loads(r["lines"]):
            if c == code:
                hist.append({"t": f"Приход №{r['id']} · {_dm(r['day'])}", "s": r["supplier"],
                             "v": f"{_num(got)} шт" + (f" × {_num(price)}" if see else "")})
    v.ui("rows", {"items": hist[:6], "empty": "Приходов в учёте ещё не было"}, "Последние приходы")
    kb = [[Button("‹ К товарам", "gd")]] + ([[Button("✏️ Изменить", f"gde:{code}")]] if buyer(ctx) else [])
    return Reply(view=v, buttons=kb)


# ======================= приходы =======================

ST = {"draft": ("черновик", ""), "sent": ("ждёт склад", "warn"), "done": ("принят", "good")}


def _dm(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}"


def _lines(r):
    return json.loads(r["lines"] or "[]")


def _names(db):
    return {g["code"]: g["name"] for g in db.q("SELECT code, name FROM goods")}


@screen("rcl", module=M)
def receipts(ctx):
    if not receipts_ok(ctx):
        return Reply(text="Приходы видят руководство, закупки и склад.")
    see = can_see(ctx, "суммы приходов")
    rs = ctx.db.q("SELECT * FROM receipts ORDER BY CASE status WHEN 'sent' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, day DESC, id DESC LIMIT 40")
    month = [r for r in rs if r["status"] == "done" and r["day"][:7] == ctx.month]
    total = sum(n * p for r in month for c, n, p, got in _lines(r))
    debt = sum((got if got is not None else n) * p for r in ctx.db.q("SELECT * FROM receipts WHERE pay = 'debt' AND status != 'draft'")
               for c, n, p, got in _lines(r))
    v = View("📥 Приходы от поставщиков", "")
    v.ui("tiles", {"items": [
        ["📝", "Черновики", str(sum(1 for r in rs if r["status"] == "draft")), "у закупок", ""],
        ["📦", "Ждут склад", str(sum(1 for r in rs if r["status"] == "sent")), "пересчитать и принять", ""],
        ["✅", "Принято за месяц", str(len(month)), f"на {money(total)}" if see else f"на {LOCK}", ""],
        ["🏭", "Должны поставщикам", money(debt) if see else LOCK, "по приходам в долг", ""]]}, "")
    items = []
    names = _names(ctx.db)
    for r in rs:
        ls = _lines(r)
        st, tone = ST.get(r["status"], ("", ""))
        s = f"{_dm(r['day'])} · {len(ls)} поз. · {_num(sum(n for c, n, p, g in ls))} шт"
        if see:
            s += f" · {money(sum(n * p for c, n, p, g in ls))}"
        items.append({"t": f"№{r['id']} · {r['supplier']}", "s": s + (f" · {', '.join(names.get(c, c) for c, *_ in ls[:2])}" if ls else ""),
                      "chip": [st, f"rco:{r['id']}", tone]})
    foot = [["➕ Новый приход", "rcn"]] if buyer(ctx) else []
    v.ui("rows", {"items": items, "empty": "Приходов пока нет", "foot": foot, "right": "нажмите на статус"}, "Приходы")
    return Reply(view=v)


def _suppliers(db):
    return sorted({r["supplier"] for r in db.q("SELECT DISTINCT supplier FROM goods UNION SELECT DISTINCT supplier FROM receipts")
                   if r["supplier"]})


def receipt_form(ctx, r=None, err=""):
    names = _names(ctx.db)
    lines = "\n".join(f"{names.get(c, c)}; {_num(n)}; {_num(p)}" for c, n, p, g in _lines(r)) if r else ""
    sup = _suppliers(ctx.db)
    cur = (r or {}).get("supplier") or ""
    fields = ([{"k": "supplier", "t": "seg", "l": "Поставщик", "v": cur if cur in sup else (sup[0] if sup else ""), "opts": [[x, x] for x in sup]}]
              if sup else []) + [
        {"k": "sup_new", "t": "text", "l": "Новый поставщик" if sup else "Поставщик", "v": "" if cur in sup else cur,
         "ph": "впишите, если его нет в списке" if sup else "название поставщика", "req": 0 if sup else 1}]
    from modules import ship as SH
    if len(SH.whs(ctx)) > 1:                              # у сети несколько складов — на какой пришло
        w0 = (r or {}).get("wh") or SH.main_wh(ctx)
        fields.append({"k": "wh", "t": "seg", "l": "На какой склад", "v": w0, "opts": [[w, n] for w, n in SH.whs(ctx)]})
    fields += [
        {"k": "no", "t": "text", "l": "Номер накладной поставщика", "v": (r or {}).get("no", ""), "ph": "№ 2231"},
        {"k": "day", "t": "date", "l": "Дата", "v": (r or {}).get("day") or ctx.today.isoformat(),
         "quick": [[ctx.today.isoformat(), "Сегодня"], [(ctx.today - timedelta(days=1)).isoformat(), "Вчера"]]},
        {"k": "pay", "t": "seg", "l": "Оплата поставщику", "v": (r or {}).get("pay", "debt"), "opts": [["debt", "В долг"], ["paid", "Оплачено"]]},
        {"k": "lines", "t": "area", "l": "Товары — каждый с новой строки: название или код; штук; цена закупки", "req": 1, "max": 4000,
         "v": lines, "ph": "Диван «Лондон»; 6; 38000\nКресло «Релакс»; 10; 14500"},
    ]
    v = View(f"📥 Приход{' №' + str(r['id']) if r else ''}", "черновик — склад его ещё не видит")
    v.ui("sform", {"fields": fields, "send": ["💾 Сохранить", f"rcs:{r['id'] if r else ''}"], "err": err, "top": [],
                   "note": "Цены закупки — закрытые цифры: кладовщик их не увидит, только количество."},
         fallback=[f["l"] for f in fields])
    return Reply(view=v, buttons=[[Button("‹ К приходам", "rcl")]])


@screen("rcn", module=M)
def receipt_new(ctx, rid=None):
    if not buyer(ctx):
        return receipts(ctx)
    if not can_see(ctx):
        return Reply(text="🔒 Цены закупки — закрытые цифры. Приход с ценами заводит тот, кому владелец включил доступ "
                          "(«🔒 Закрытые цифры» у владельца).", buttons=[[Button("‹ К приходам", "rcl")]])
    r = ctx.db.one("SELECT * FROM receipts WHERE id = ? AND status = 'draft'", int(rid)) if rid and str(rid).isdigit() else None
    return receipt_form(ctx, r)


def _parse_num(x):
    x = str(x).replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".")
    try:
        v = float(x)
    except ValueError:
        return None
    return v if 0 < v < 1e9 else None


def parse_lines(db, text):
    """«название или код; шт; цена» по строкам → ([[код, шт, цена, None]], [ошибки])."""
    goods_ = db.q("SELECT code, name FROM goods WHERE off = 0")
    out, errs = [], []
    for i, line in enumerate(str(text).splitlines()[:80], 1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ";").split(";")]
        if len(parts) != 3:
            errs.append(f"строка {i}: нужно три части через «;» — товар; штук; цена")
            continue
        name, n, p = parts[0].lower(), _parse_num(parts[1]), _parse_num(parts[2])
        hit = [g for g in goods_ if g["code"] == parts[0]] or [g for g in goods_ if g["name"].lower() == name] or \
            [g for g in goods_ if name and name in g["name"].lower()]
        if not hit:
            errs.append(f"строка {i}: товар «{parts[0][:40]}» не найден")
        elif len(hit) > 1:
            errs.append(f"строка {i}: «{parts[0][:40]}» — подходит {len(hit)} товаров, напишите точнее или код")
        elif not n:
            errs.append(f"строка {i}: штук — число больше нуля")
        elif not p:
            errs.append(f"строка {i}: цена — число больше нуля")
        else:
            out.append([hit[0]["code"], n, p, None])
    if not out and not errs:
        errs.append("добавьте хотя бы один товар")
    return out, errs


@screen("rcs", module=M)
def receipt_save(ctx, rid=None):
    if not buyer(ctx) or not can_see(ctx):
        return receipts(ctx)
    f = ctx.form or {}
    r = ctx.db.one("SELECT * FROM receipts WHERE id = ? AND status = 'draft'", int(rid)) if rid and str(rid).isdigit() else None
    sup = (str(f.get("sup_new", "")).strip() or str(f.get("supplier", "")).strip())[:80]
    from modules import ship as SH
    wh = str(f.get("wh") or "") if str(f.get("wh") or "") in SH.wh_ids(ctx) else SH.main_wh(ctx)
    try:
        day = date.fromisoformat(str(f.get("day", ""))[:10])
    except ValueError:
        day = None
    lines, errs = parse_lines(ctx.db, f.get("lines", ""))
    if len(sup) < 2:
        errs.insert(0, "выберите или впишите поставщика")
    if not day or day > ctx.today or day < ctx.today - timedelta(days=120):
        errs.insert(0, "дата — не позже сегодня и не раньше 120 дней назад")
    if errs:
        rep = receipt_form(ctx, r, "; ".join(errs[:5]))
        for kind, b in rep.view.blocks:                       # оставить то, что человек уже написал
            if kind == "ui" and b[0] == "sform":
                for fl in b[1]["fields"]:
                    if fl["k"] in f:
                        fl["v"] = str(f[fl["k"]])[:4000]
        return rep
    pay = "paid" if f.get("pay") == "paid" else "debt"
    data = (str(f.get("no", "")).strip()[:30], day.isoformat(), sup, pay, json.dumps(lines), wh)
    if r:
        ctx.db.x("UPDATE receipts SET no = ?, day = ?, supplier = ?, pay = ?, lines = ?, wh = ? WHERE id = ?", *data, r["id"])
        rid = r["id"]
    else:
        rid = ctx.db.x("INSERT INTO receipts (no, day, supplier, pay, lines, wh, author, author_id, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                       *data, _who(ctx), (ctx.me or {}).get("id"), datetime.now().isoformat(timespec="seconds"))
    log(ctx, f"завёл приход №{rid} с ценами закупки", force=True)
    return receipt_card(ctx, rid)


@screen("rco", module=M)
def receipt_card(ctx, rid=None, act=None):
    r = ctx.db.one("SELECT * FROM receipts WHERE id = ?", int(rid)) if rid and str(rid).isdigit() else None
    if not r or not receipts_ok(ctx):
        return receipts(ctx)
    if act == "send" and r["status"] == "draft" and buyer(ctx):
        ctx.db.x("UPDATE receipts SET status = 'sent' WHERE id = ?", r["id"])
        r["status"] = "sent"
    see = can_see(ctx, f"приход №{r['id']} с ценами")
    names = _names(ctx.db)
    ls = _lines(r)
    tot = sum(n * p for c, n, p, g in ls)
    st, tone = ST.get(r["status"], ("", ""))
    v = View(f"Приход №{r['id']}", f"{r['supplier']} · {_dm(r['day'])}")
    v.ui("hero", {"label": f"{r['supplier']} · накладная {r['no'] or 'без номера'} · {_dm(r['day'])}",
                  "big": money(tot) if see else f"{len(ls)} поз. · {_num(sum(n for c, n, p, g in ls))} шт", "md": True, "id": f"rco{r['id']}",
                  "sub": f"{st} · {'в долг' if r['pay'] == 'debt' else 'оплачено'} · завёл {r['author']}",
                  "pairs": [], "acts": []}, fallback=[f"Приход №{r['id']}: {st}"])
    rows = []
    short = 0
    for c, n, p, got in ls:
        cells = [[_num(n), ""]]
        if r["status"] == "done":
            cells.append([_num(got), "bad" if got < n else "good"])
            short += max(n - got, 0)
        if see:
            cells += [[_num(p), ""], [_num((got if got is not None else n) * p), ""]]
        rows.append({"n": names.get(c, c), "cells": cells, "det": f"код {c}", "acts": [["📦 Товар", f"gdi:{c}"]]})
    head = ["Товар", "По накл."] + (["Принято"] if r["status"] == "done" else []) + ([f"Цена {LOCK}", "Сумма"] if see else [])
    v.ui("team", {"head": head, "rows": rows, "note": "" if see else "🔒 Цены и суммы закрыты — у вас нет доступа к закрытым цифрам."}, "Товары")
    if r["status"] == "done":
        v.ui("rows", {"items": [{"t": f"Принял {r['done_by']} · {_dm(r['done_ts'] or r['day'])}",
                                 "s": r["note"] or "всё сошлось", "chip": ["недостача", "", "bad"] if short else ["сошлось", "", "good"]}]}, "")
    acts = []
    if r["status"] == "draft" and buyer(ctx):
        acts = [["📥 Отправить на склад", f"rco:{r['id']}:send"], ["✏️ Изменить", f"rcn:{r['id']}"]]
    if r["status"] == "sent" and keeper(ctx):
        acts = [["📦 Пересчитать и принять", f"rca:{r['id']}"]]
    if acts:
        v.ui("rows", {"items": [], "foot": acts,
                      "empty": "Склад примет по количеству — цены кладовщик не увидит. Остаток и себестоимость обновятся сами."
                      if r["status"] == "draft" else "Пересчитайте товар и впишите, сколько пришло на самом деле."}, "")
    return Reply(view=v, buttons=[[Button("‹ К приходам", "rcl")]])


@screen("rca", module=M)
def receipt_accept(ctx, rid=None, err=""):
    r = ctx.db.one("SELECT * FROM receipts WHERE id = ? AND status = 'sent'", int(rid)) if rid and str(rid).isdigit() else None
    if not r or not keeper(ctx):
        return receipts(ctx)
    names = _names(ctx.db)
    fields = [{"k": f"l{i}", "t": "num", "l": f"{names.get(c, c)} · по накладной {_num(n)}", "v": _num(n).replace(" ", "")}
              for i, (c, n, p, g) in enumerate(_lines(r))]
    fields.append({"k": "note", "t": "text", "l": "Комментарий (необязательно)", "v": "", "ph": "например: одно кресло с браком"})
    v = View(f"Приёмка №{r['id']}", r["supplier"])
    v.ui("hero", {"label": f"{r['supplier']} · машина приехала", "big": f"{len(fields) - 1} поз. · {_num(sum(n for c, n, p, g in _lines(r)))} шт",
                  "sub": "пересчитайте и впишите, сколько пришло", "id": f"rca{r['id']}", "pairs": [], "acts": [], "md": True},
         fallback=["Приёмка"])
    v.ui("sform", {"fields": fields, "send": ["✅ Принять на склад", f"rcy:{r['id']}"], "err": err, "top": [],
                   "note": "Меньше, чем по накладной, — недостача уйдёт в закупки. Цены и суммы здесь не показываются."}, "Пересчёт")
    return Reply(view=v, buttons=[[Button("‹ К приходу", f"rco:{r['id']}")]])


@screen("rcy", module=M)
def receipt_done(ctx, rid=None):
    r = ctx.db.one("SELECT * FROM receipts WHERE id = ? AND status = 'sent'", int(rid)) if rid and str(rid).isdigit() else None
    if not r or not keeper(ctx):
        return receipts(ctx)
    f = ctx.form or {}
    ls = _lines(r)
    got = []
    for i, (c, n, p, g) in enumerate(ls):
        raw = str(f.get(f"l{i}", "")).strip().replace(",", ".")
        try:
            x = float(raw)
        except ValueError:
            return receipt_accept(ctx, rid, f"Строка {i + 1}: впишите число (0, если не пришло)")
        if x < 0 or x > n * 3:
            return receipt_accept(ctx, rid, f"Строка {i + 1}: от 0 до {_num(n * 3)}")
        got.append(x)
    names = _names(ctx.db)
    shorts = []
    for (c, n, p, g), x in zip(ls, got):
        if x:
            cur = ctx.db.one("SELECT qty, cost FROM goods WHERE code = ?", c)
            q0, c0 = max(cur["qty"], 0), cur["cost"]
            cost = (q0 * c0 + x * p) / (q0 + x) if q0 + x else p          # средняя цена всех приходов
            ctx.db.x("UPDATE goods SET qty = qty + ?, cost = ?, ts = ? WHERE code = ?", x, round(cost, 2),
                     datetime.now().isoformat(timespec="seconds"), c)
            from modules import ship as SH
            if SH.has_locs(ctx.db) or ctx.cfg.warehouses:              # учёт по местам: приход — на склад прихода (modules/ship.py)
                w = r["wh"] if r["wh"] in SH.wh_ids(ctx) else SH.main_wh(ctx)
                ctx.db.x("INSERT INTO stock_loc (code, loc, qty, smp) VALUES (?, ?, ?, 0) "
                         "ON CONFLICT(code, loc) DO UPDATE SET qty = qty + ?", c, w, x, x)
        if x < n:
            shorts.append(f"{names.get(c, c)} — {_num(n - x)} шт")
    note = str(f.get("note", "")).strip()[:200]
    if shorts:
        note = "Недостача: " + ", ".join(shorts) + (f". {note}" if note else "")
    ctx.db.x("UPDATE receipts SET status = 'done', lines = ?, done_ts = ?, done_by = ?, note = ? WHERE id = ?",
             json.dumps([[c, n, p, x] for (c, n, p, g), x in zip(ls, got)]), datetime.now().isoformat(timespec="seconds"),
             _who(ctx), note, r["id"])
    out = [receipt_card(ctx, r["id"])]
    if shorts and r["author_id"]:
        a = ctx.db.employee(r["author_id"])
        if a and a.get("tg_id"):
            out.append(Reply(text=f"📦 Приход №{r['id']} ({r['supplier']}) принят с недостачей: {', '.join(shorts)}.",
                             buttons=[[Button("📥 Открыть приход", f"rco:{r['id']}")]], to=a["tg_id"]))
    return out
