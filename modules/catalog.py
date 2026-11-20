"""⚙️ Справочники учёта в Алапе и правка товара (Адам 20.11: «переименовывать названия, разделы, добавлять новые, удалять старые»).

Товар (`gde:<код>|new` → форма → `gds:<код>|new`, убрать `gdz:<код>[:ok]`): название, раздел, поставщик, три цены — закупочная 🔒
(= себестоимость, только тем, кто видит закрытые цифры), оптовая, розничная. Товар с историей (продажи, приходы, остатки) не стирается —
уходит в архив (`goods.off`), история остаётся; без истории — удаляется.
Справочники (`rf`): разделы товаров (группы), склады, магазины — ✏️ переименовать (`rfe:<вид>:<ключ>` → `rfs`), ➕ добавить
(`rfe:<вид>:new`), 🗑 убрать (`rfd:<вид>:<ключ>[:ok]`, только пустое). Вид: g — раздел (ключ — md5 названия), w — склад, s — магазин.
Склады — `ship.whs` / `ship.set_whs`; магазины — правки поверх анкеты (`retail.apply_edits`, ключ базы `shops_edit`).
«Видит магазины» (`shs:<сотрудник>` → `shy:<сотрудник>`) — руководителю отмечаются магазины (`retail.SEES_KEY`), он видит только их.
Правит: владелец, админ, закупки (товары и разделы); склады и магазины — владелец и админ.
"""
import hashlib
from datetime import datetime

from core.ui import retail as RT
from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen
from core.ui.views import View
from modules import acc as A
from modules import ship as SH

M = "stock"


def boss(ctx):
    return ctx.role in ("owner", "admin")


def editor(ctx):
    return boss(ctx) or ctx.dept_kind == "purchasing"


menu("rf", "⚙️ Справочники", {"owner", "admin", "head", "staff"}, module=M, order=29, when=editor)


def _num(x):
    return A._parse_num(x) if str(x).strip() not in ("", "0") else 0


def _key(name):
    return hashlib.md5(name.encode()).hexdigest()[:8]


def groups(db):
    """[(раздел, товаров)]: из товаров + заведённые пустыми (ключ базы grp_new)."""
    got = {r["grp"]: r["n"] for r in db.q("SELECT grp, COUNT(*) AS n FROM goods WHERE off = 0 AND grp != '' GROUP BY grp")}
    for n in db.get("grp_new") or []:
        got.setdefault(n, 0)
    return sorted(got.items(), key=lambda x: x[0].lower())


# ======================= товар =======================

@screen("gde", module=M)
def goods_edit(ctx, code=None, err="", f=None):
    if not editor(ctx):
        return A.goods(ctx)
    g = ctx.db.one("SELECT * FROM goods WHERE code = ?", code or "") if code != "new" else None
    if code != "new" and not g:
        return A.goods(ctx)
    see = A.can_see(ctx, f"закупочная цена товара «{g['name']}»" if g else None)
    f = f or {}
    val = lambda k, d="": str(f.get(k, d if g is None else (g[k] if g[k] not in (None, 0) else "")))  # noqa: E731
    gs = [n for n, _ in groups(ctx.db)]
    fields = [{"k": "name", "t": "text", "l": "Название", "v": val("name"), "req": 1},
              {"k": "grp", "t": "seg", "l": "Раздел", "v": val("grp"), "opts": [[n, n] for n in gs] + [["", "без раздела"]]} if gs else None,
              {"k": "grp_new", "t": "text", "l": "Новый раздел" if gs else "Раздел", "v": str(f.get("grp_new", "")), "ph": "впишите, если его нет в списке"},
              {"k": "price", "t": "num", "l": "Цена розничная", "v": A._num(float(val("price") or 0)) if val("price") else ""},
              {"k": "price_opt", "t": "num", "l": "Цена оптовая", "v": A._num(float(val("price_opt") or 0)) if val("price_opt") else ""}]
    if see:
        fields.append({"k": "cost", "t": "num", "l": f"Цена закупочная {A.LOCK}", "v": A._num(float(val("cost") or 0)) if val("cost") else ""})
    fields.append({"k": "supplier", "t": "text", "l": "Поставщик", "v": val("supplier")})
    v = View("➕ Новый товар" if not g else f"✏️ {g['name']}", f"код {g['code']}" if g else "код бот даст сам")
    v.ui("sform", {"fields": [x for x in fields if x], "send": ["💾 Сохранить", f"gds:{code}"], "err": err, "top": [],
                   "note": "Магазин продаёт по розничной, опт — по оптовой. " + (f"Закупочную {A.LOCK} видите вы и те, кому открыл владелец."
                                                                                if see else "Закупочную цену меняет тот, кому владелец открыл закрытые цифры.")},
         "", fallback=[x["l"] for x in fields if x])
    kb = [[Button("‹ К товару" if g else "‹ К товарам", f"gdi:{code}" if g else "gd")]]
    if g and not g["off"]:
        kb.append([Button("🗑 Убрать товар", f"gdz:{code}")])
    if g and g["off"]:
        kb.append([Button("↩️ Вернуть из архива", f"gdz:{code}:back")])
    return Reply(view=v, buttons=kb)


@screen("gds", module=M)
def goods_save(ctx, code=None):
    if not editor(ctx):
        return A.goods(ctx)
    f = ctx.form or {}
    g = ctx.db.one("SELECT * FROM goods WHERE code = ?", code or "") if code != "new" else None
    if code != "new" and not g:
        return A.goods(ctx)
    name = " ".join(str(f.get("name", "")).split())[:120]
    grp = " ".join(str(f.get("grp_new", "")).split())[:40] or str(f.get("grp", ""))[:40]
    errs = []
    if len(name) < 2:
        errs.append("впишите название")
    elif ctx.db.one("SELECT 1 AS x FROM goods WHERE lower(name) = lower(?) AND code != ? AND off = 0", name, code or ""):
        errs.append("товар с таким названием уже есть")
    prices = {}
    for k in ("price", "price_opt", "cost"):
        raw = str(f.get(k, "")).strip()
        if k == "cost" and not A.can_see(ctx):
            continue
        x = 0 if raw in ("", "0") else A._parse_num(raw)
        if x is None:
            errs.append({"price": "розничная", "price_opt": "оптовая", "cost": "закупочная"}[k] + " цена — число")
        else:
            prices[k] = x
    if errs:
        return goods_edit(ctx, code, "; ".join(errs), f)
    now = datetime.now().isoformat(timespec="seconds")
    sup = " ".join(str(f.get("supplier", "")).split())[:60]
    if g:
        sets = ["name = ?", "grp = ?", "supplier = ?", "ts = ?"] + [f"{k} = ?" for k in prices]
        ctx.db.x(f"UPDATE goods SET {', '.join(sets)} WHERE code = ?", name, grp, sup, now, *prices.values(), code)
        what = "изменил товар"
    else:
        import re
        seq = max([int(r["code"][1:]) for r in ctx.db.q("SELECT code FROM goods") if re.fullmatch(r"A\d{5}", r["code"])] + [0]) + 1
        code = f"A{seq:05d}"
        ctx.db.x("INSERT INTO goods (code, name, grp, price, price_opt, cost, qty, supplier, ts) VALUES (?,?,?,?,?,?,0,?,?)",
                 code, name, grp, prices.get("price", 0), prices.get("price_opt", 0), prices.get("cost", 0), sup, now)
        what = "завёл товар"
    ctx.db.audit(A._who(ctx), "goods", f"{what} {code} «{name}»")
    return A.goods_card(ctx, code)


def _has_history(db, code):
    return bool(db.one("SELECT 1 AS x FROM item_sales WHERE code = ? LIMIT 1", code)
                or db.one("SELECT 1 AS x FROM stock_loc WHERE code = ? AND qty != 0 LIMIT 1", code)
                or any(code in r["lines"] for r in db.q("SELECT lines FROM receipts")))


@screen("gdz", module=M)
def goods_remove(ctx, code=None, act=None):
    g = ctx.db.one("SELECT * FROM goods WHERE code = ?", code or "")
    if not editor(ctx) or not g:
        return A.goods(ctx)
    if act == "back":
        ctx.db.x("UPDATE goods SET off = 0 WHERE code = ?", code)
        ctx.db.audit(A._who(ctx), "goods", f"вернул из архива {code}")
        return A.goods_card(ctx, code)
    hist = _has_history(ctx.db, code)
    if act != "ok":
        v = View(f"🗑 Убрать «{g['name']}»?", "")
        v.ui("rows", {"items": [{"t": "Уйдёт в архив" if hist else "Удалится совсем",
                                 "s": "есть продажи, приходы или остатки — история останется, в продаже и поиске товара не будет"
                                 if hist else "истории нет — товар стирается"}],
                      "foot": [["✅ Да, убрать", f"gdz:{code}:ok"], ["✖️ Отмена", f"gde:{code}"]]}, "Точно?")
        return Reply(view=v)
    if hist:
        ctx.db.x("UPDATE goods SET off = 1 WHERE code = ?", code)
    else:
        ctx.db.x("DELETE FROM goods WHERE code = ?", code)
        ctx.db.x("DELETE FROM stock_loc WHERE code = ?", code)
    ctx.db.audit(A._who(ctx), "goods", f"{'в архив' if hist else 'удалил'} {code} «{g['name']}»")
    return A.goods(ctx)


# ======================= справочники =======================

KINDS = {"g": "раздел", "w": "склад", "s": "магазин"}


def _stock_at(db, loc):
    return (db.one("SELECT SUM(qty) AS q FROM stock_loc WHERE loc = ?", loc) or {}).get("q") or 0


def _people_in(ctx, sid):
    ids = {t.id for t in RT.shop_teams(ctx.cfg, sid)}
    return [e for e in ctx.db.employees() if e["team"] in ids]


@screen("rf", module=M)
def refs(ctx, note=None):
    if not editor(ctx):
        return Reply(text="Справочники правят владелец, админ и закупки.")
    v = View("⚙️ Справочники", ctx.cfg.name)
    if note:
        v.note(note)
    nogrp = (ctx.db.one("SELECT COUNT(*) AS n FROM goods WHERE off = 0 AND grp = ''") or {}).get("n", 0)
    items = [{"t": n, "s": f"{A._num(c)} товаров", "chip": ["✏️ изменить", f"rfe:g:{_key(n)}", ""]} for n, c in groups(ctx.db)]
    if nogrp:
        items.append({"t": "Без раздела", "s": f"{A._num(nogrp)} товаров — разложить в карточках", "chip": ["список", "gd", "warn"]})
    v.ui("rows", {"items": items, "empty": "Разделов пока нет", "foot": [["➕ Раздел", "rfe:g:new"]]}, "Разделы товаров")
    if boss(ctx):
        v.ui("rows", {"items": [{"t": n, "s": f"{A._num(_stock_at(ctx.db, w))} шт", "chip": ["✏️ изменить", f"rfe:w:{w}", ""]}
                                for w, n in SH.whs(ctx)], "foot": [["➕ Склад", "rfe:w:new"]]}, "Склады")
        if RT.has_shops(ctx.cfg):
            v.ui("rows", {"items": [{"t": n, "s": f"{A._num(_stock_at(ctx.db, s))} шт · {len(_people_in(ctx, s))} чел.",
                                     "chip": ["✏️ изменить", f"rfe:s:{s}", ""]} for s, n in RT.shops(ctx.cfg)],
                          "foot": [["➕ Магазин", "rfe:s:new"]]}, "Магазины")
    v.note("Переименование — сразу везде, история не теряется. Убрать можно только пустое: склад без товара, магазин без товара и людей; "
           "раздел — его товары станут «без раздела».")
    return Reply(view=v, buttons=[[Button("📦 Товары", "gd")]])


def _name_of(ctx, kind, key):
    if kind == "g":
        return next((n for n, _ in groups(ctx.db) if _key(n) == key), None)
    if kind == "w":
        return dict(SH.whs(ctx)).get(key)
    return dict(RT.shops(ctx.cfg)).get(key)


@screen("rfe", module=M)
def ref_edit(ctx, kind="", key="", err=""):
    if kind not in KINDS or not editor(ctx) or (kind != "g" and not boss(ctx)):
        return refs(ctx)
    cur = None if key == "new" else _name_of(ctx, kind, key)
    if key != "new" and cur is None:
        return refs(ctx)
    v = View(f"➕ {KINDS[kind].capitalize()}" if key == "new" else f"✏️ {cur}", "")
    v.ui("sform", {"fields": [{"k": "name", "t": "text", "l": "Название", "v": cur or "", "req": 1}],
                   "send": ["💾 Сохранить", f"rfs:{kind}:{key}"], "err": err, "top": [], "note": ""}, "", fallback=["Название"])
    kb = [[Button("‹ Справочники", "rf")]]
    if cur is not None:
        kb.append([Button(f"🗑 Убрать {KINDS[kind]}", f"rfd:{kind}:{key}")])
    return Reply(view=v, buttons=kb)


@screen("rfs", module=M)
def ref_save(ctx, kind="", key=""):
    if kind not in KINDS or not editor(ctx) or (kind != "g" and not boss(ctx)):
        return refs(ctx)
    name = " ".join(str((ctx.form or {}).get("name", "")).split())[:40]
    if len(name) < 2:
        return ref_edit(ctx, kind, key, "впишите название")
    taken = {n.lower() for n, _ in (groups(ctx.db) if kind == "g" else SH.whs(ctx) if kind == "w" else RT.shops(ctx.cfg))}
    cur = None if key == "new" else _name_of(ctx, kind, key)
    if name.lower() in taken and name.lower() != (cur or "").lower():
        return ref_edit(ctx, kind, key, "такое название уже есть")
    if kind in "ws" and name.lower() in {n.lower() for _, n in SH.locs(ctx)} - {(cur or "").lower()}:
        return ref_edit(ctx, kind, key, "так уже называется склад или магазин")
    if kind == "g":
        if cur:
            ctx.db.x("UPDATE goods SET grp = ? WHERE grp = ?", name, cur)
            ctx.db.put("grp_new", sorted({name if x == cur else x for x in ctx.db.get("grp_new") or []}))
            note = f"Раздел «{cur}» → «{name}»."
        else:
            note = f"Раздел «{name}» добавлен — товары в него кладут в карточке товара (✏️)."
            ctx.db.put("grp_new", sorted(set((ctx.db.get("grp_new") or []) + [name])))
    elif kind == "w":
        ws = SH.whs(ctx)
        if cur:
            ws = [(w, name if w == key else n) for w, n in ws]
            SH.set_whs(ctx, ws)
            note = f"Склад «{cur}» → «{name}»."
        else:
            SH.set_whs(ctx, ws)
            SH.add_wh(ctx.db, name)
            note = f"Склад «{name}» добавлен."
    else:
        e = ctx.db.get(RT.EDIT_KEY) or {}
        if cur:
            e.setdefault("names", {})[key] = name
            note = f"Магазин «{cur}» → «{name}»."
        else:
            have = {t.id for t in ctx.app.cfg.teams} | {s for s, _ in e.get("add") or []}
            n = 1
            while f"s{n}" in have:
                n += 1
            e.setdefault("add", []).append([f"s{n}", name])
            note = f"Магазин «{name}» добавлен — людей в него переносите в «👥 Сотрудники»."
        ctx.db.put(RT.EDIT_KEY, e)
        ctx.cfg = RT.apply_edits(ctx.app.cfg, ctx.db)
    ctx.db.audit(A._who(ctx), "refs", note)
    return refs(ctx, "✅ " + note)


@screen("rfd", module=M)
def ref_delete(ctx, kind="", key="", act=None):
    if kind not in KINDS or not editor(ctx) or (kind != "g" and not boss(ctx)):
        return refs(ctx)
    cur = _name_of(ctx, kind, key)
    if cur is None:
        return refs(ctx)
    why = ""
    if kind == "w":
        if _stock_at(ctx.db, key):
            why = f"на складе {A._num(_stock_at(ctx.db, key))} шт — сначала переместите товар"
        elif len(SH.whs(ctx)) == 1:
            why = "это последний склад"
        elif any(src == key for (_c, src) in SH.reserved(ctx.db)):
            why = "со склада ждут отгрузки заказы"
    if kind == "s":
        if _stock_at(ctx.db, key):
            why = f"в магазине {A._num(_stock_at(ctx.db, key))} шт — сначала переместите товар"
        elif _people_in(ctx, key):
            why = f"в магазине {len(_people_in(ctx, key))} чел. — переведите их в другой магазин"
    if why:
        return refs(ctx, f"Убрать «{cur}» нельзя: {why}.")
    if act != "ok":
        v = View(f"🗑 Убрать «{cur}»?", "")
        v.ui("rows", {"items": [{"t": f"Товары раздела станут «без раздела»" if kind == "g" else "Пустой — можно убрать",
                                 "s": "история продаж и приходов остаётся"}],
                      "foot": [["✅ Да, убрать", f"rfd:{kind}:{key}:ok"], ["✖️ Отмена", "rf"]]}, "Точно?")
        return Reply(view=v)
    if kind == "g":
        ctx.db.x("UPDATE goods SET grp = '' WHERE grp = ?", cur)
        ctx.db.put("grp_new", [x for x in ctx.db.get("grp_new") or [] if x != cur])
    elif kind == "w":
        SH.set_whs(ctx, [(w, n) for w, n in SH.whs(ctx) if w != key])
    else:
        e = ctx.db.get(RT.EDIT_KEY) or {}
        e["off"] = sorted(set(e.get("off") or []) | {key})
        ctx.db.put(RT.EDIT_KEY, e)
        ctx.cfg = RT.apply_edits(ctx.app.cfg, ctx.db)
    ctx.db.audit(A._who(ctx), "refs", f"убрал {KINDS[kind]} «{cur}»")
    return refs(ctx, f"✅ «{cur}» убран.")


# ======================= «видит магазины» =======================

def sees_ok(ctx, e):
    return boss(ctx) and e and RT.has_shops(ctx.cfg) and e["role"] in ("head", "sv")


@screen("shs", module=M)
def sees(ctx, emp_id=None, note=None):
    e = ctx.db.employee(int(emp_id)) if str(emp_id or "").isdigit() else None
    if not sees_ok(ctx, e):
        return Reply(text="Магазины руководителю отмечают владелец и админ.")
    mine = (ctx.db.get(RT.SEES_KEY) or {}).get(str(e["id"])) or []
    all_ = RT.shops(ctx.cfg)
    v = View(f"👤 {e['fio']}", "какие магазины видит")
    if note:
        v.note(note)
    v.ui("sform", {"fields": [{"k": "shops", "t": "multi", "l": f"Видит магазины · {len(mine) or len(all_)} из {len(all_)}",
                               "v": ",".join(mine), "opts": [[s, n] for s, n in all_]}],
                   "send": ["✅ Сохранить", f"shy:{e['id']}"], "err": "", "top": [],
                   "note": "Ничего не отмечено — видит все магазины. Продажи, люди, планы, табель и зарплата — только отмеченных."},
         "", fallback=[n for _, n in all_])
    return Reply(view=v, buttons=[[Button("‹ Сотрудник", f"pae:{e['id']}")]])


@screen("shy", module=M)
def sees_save(ctx, emp_id=None):
    e = ctx.db.employee(int(emp_id)) if str(emp_id or "").isdigit() else None
    if not sees_ok(ctx, e):
        return Reply(text="Магазины руководителю отмечают владелец и админ.")
    have = {s for s, _ in RT.shops(ctx.cfg)}
    pick = [s for s in str((ctx.form or {}).get("shops", "")).split(",") if s in have]
    d = ctx.db.get(RT.SEES_KEY) or {}
    if pick and len(pick) < len(have):
        d[str(e["id"])] = pick
    else:
        d.pop(str(e["id"]), None)
    ctx.db.put(RT.SEES_KEY, d)
    names = dict(RT.shops(ctx.cfg))
    ctx.db.audit(A._who(ctx), "sees", f"{e['fio']}: " + (", ".join(names[s] for s in pick) if str(e["id"]) in d else "все магазины"))
    return sees(ctx, e["id"], "✅ Сохранено: " + (", ".join(names[s] for s in pick) if str(e["id"]) in d else "видит все магазины."))
