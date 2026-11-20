"""📥 Товары и остатки из Excel (Адам 20.11, учёт в Алапе): стартовая загрузка без ручного ввода.

Понимает два вида файла:
  • шаблон Алапы (`gdt`): Код, Название, Группа, Цена продажи, Себестоимость, дальше колонка на каждое место (склад, магазин);
  • отчёт «Остатки» из 1С как есть: строка места хранения (её число = сумма товаров под ней) → товары со штуками.
    Места узнаются по сумме, без оформления файла (у 1С группы бывают без отступов и жирного).
Коды нет — бот даёт свои (A00001…), одно и то же название в разных местах — один товар.

Порядок: «📥 Товары из Excel» (`gdu`) → файл → проверка (`gdu` показывает, что нашлось: товаров, штук, места, ошибки) →
у каждого места из файла выбрать, куда оно в Алапе (склад, магазин, ➕ новый склад, не загружать) → «✅ Загрузить» (`gdy`).
Остатки из файла заменяют остатки этих товаров на выбранных местах; товары не из файла не трогаются. Цены и себестоимость —
только если в файле есть (себестоимость — закрытая цифра: принимается только у того, кто её видит).
Ожидающая загрузка — ключ базы `goods_import`; файл приходит через /api/upload (core/reports.receive → сюда по session.upto).
"""
import io
import re
from datetime import datetime

from core.ingest.excel import num, read_grid
from core.ui.kit import Button, Reply
from core.ui.registry import menu, screen
from core.ui.views import View
from modules import acc as A
from modules import ship as SH

M = "stock"
KEY = "goods_import"
MAX_ROWS = 20000
NAME_HEADS = ("название", "наименование", "номенклатура", "товар")
FIXED = {"код": "code", "артикул": "code", "название": "name", "наименование": "name", "номенклатура": "name", "товар": "name",
         "группа": "grp", "цена": "price", "цена продажи": "price", "розничная цена": "price", "себестоимость": "cost",
         "закупочная цена": "cost", "цена закупки": "cost", "поставщик": "supplier"}


def may(ctx):
    return ctx.role in ("owner", "admin") or ctx.dept_kind == "purchasing"


menu("gdu", "📥 Товары из Excel", {"owner", "admin", "head", "staff"}, module=M, order=28, when=may)


def _norm(s):
    return " ".join(str(s or "").split()).lower()


def _head(h):
    k = _norm(h).replace("🔒", "").strip()
    return FIXED.get(k) or FIXED.get(k.split(",")[0].strip())


# ======================= разбор файла =======================

def parse(grid):
    """Таблица → {"kind": template|grouped|flat, "places": [названия], "items": [{name, code, grp, price, cost, supplier,
    qty: {место: шт}}], "errors": [...]} или {"errors": [...]} если не понял."""
    grid = [[str(c).strip() if not isinstance(c, (int, float)) else c for c in r] for r in grid[:MAX_ROWS]]
    h = next((i for i, r in enumerate(grid[:15]) if any(_head(c) == "name" for c in r)), None)
    first = _norm(grid[0][0]) if grid and grid[0] else ""
    if h is not None and first != "место хранения":
        cols = {}
        places = []
        for j, c in enumerate(grid[h]):
            k = _head(c)
            if k and k not in cols:
                cols[k] = j
            elif str(c).strip() and not k:
                places.append((j, str(c).strip()))
        out, errs = [], []
        for i, r in enumerate(grid[h + 1:], h + 2):
            name = str(r[cols["name"]] if cols["name"] < len(r) else "").strip()
            if not name or _norm(name) in ("итого", "всего"):
                continue
            it = {"name": name[:120], "row": i}
            for k in ("code", "grp", "supplier"):
                if k in cols and cols[k] < len(r):
                    it[k] = str(r[cols[k]]).strip()[:60]
            for k in ("price", "cost"):
                if k in cols and cols[k] < len(r) and str(r[cols[k]]).strip():
                    x = num(r[cols[k]])
                    if x < 0 or x > 1e9:
                        errs.append(f"строка {i}: {'цена' if k == 'price' else 'себестоимость'} «{r[cols[k]]}» — не число")
                    else:
                        it[k] = x
            it["qty"] = {}
            for j, pname in places:
                v = r[j] if j < len(r) else ""
                if str(v).strip() == "":
                    continue
                x = num(v)
                if x < 0 or x != x:
                    errs.append(f"строка {i}: «{pname}» — штук не может быть меньше нуля")
                    continue
                it["qty"][pname] = x
            out.append(it)
        return {"kind": "template", "places": [p for _, p in places], "items": out, "errors": errs}
    # отчёт 1С: [место / номенклатура, число]
    start = 1 if first == "место хранения" else 0
    rows = []
    for i, r in enumerate(grid[start:], start + 1):
        name = str(r[0] if r else "").strip()
        if not name or _norm(name) in ("номенклатура", "товар"):
            continue
        if _norm(name) in ("итого", "всего"):
            break
        q = next((c for c in r[1:] if isinstance(c, (int, float)) or re.fullmatch(r"-?[\d\s.,]+", str(c).strip() or "x")), "")
        rows.append((i, name, num(q)))
    if not rows:
        return {"errors": ["не нашёл товаров: нужна колонка «Название» (шаблон) или отчёт «Остатки» из 1С"]}
    items, places, errs = {}, [], []
    k = 0
    grouped = start == 1
    while k < len(rows) and grouped:
        i, pname, total = rows[k]
        acc, j = 0.0, k + 1
        while j < len(rows) and acc < total - 1e-9:
            acc += rows[j][2]
            j += 1
        if abs(acc - total) > 1e-6 or j == k + 1:
            grouped = False                    # суммы не сходятся — значит, мест в файле нет
            break
        places.append(pname)
        for _i, n, q in rows[k + 1:j]:
            it = items.setdefault(_norm(n), {"name": n[:120], "row": _i, "qty": {}})
            it["qty"][pname] = it["qty"].get(pname, 0) + q
        k = j
    if not grouped:
        items, places = {}, ["Склад"]
        for i, n, q in rows:
            it = items.setdefault(_norm(n), {"name": n[:120], "row": i, "qty": {}})
            it["qty"]["Склад"] = it["qty"].get("Склад", 0) + q
    for it in items.values():
        if any(q < 0 for q in it["qty"].values()):
            errs.append(f"строка {it['row']}: «{it['name'][:40]}» — остаток меньше нуля, загружу как 0")
            it["qty"] = {p: max(q, 0) for p, q in it["qty"].items()}
    return {"kind": "grouped" if grouped else "flat", "places": places, "items": list(items.values()), "errors": errs}


def guess(ctx, place):
    """Куда в Алапе место из файла: магазин по названию, склад по названию, «склад…» — новый склад, иначе — выбрать."""
    p = _norm(place)
    for loc, n in SH.locs(ctx):
        if _norm(n) == p:
            return loc
    for loc, n in RT_shops(ctx):
        if _norm(n) in p or p in _norm(n):
            return loc
    if "склад" in p:
        return "new"
    return ""


def RT_shops(ctx):
    from core.ui import retail as RT
    return RT.shops(ctx.cfg)


# ======================= экраны =======================

def template(ctx):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Товары"
    head = ["Код", "Название", "Группа", "Цена продажи", "Себестоимость 🔒", "Поставщик"] + [n for _, n in SH.locs(ctx)]
    ws.append(head)
    ws.append(["", "Диван «Лондон»", "Диваны", 64000, 41000, "Фабрика N"] + [""] * (len(head) - 6))
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E6F0E8")
    for col, w in zip("ABCDEF", (12, 40, 18, 14, 16, 18)):
        ws.column_dimensions[col].width = w
    h = wb.create_sheet("Как заполнять")
    for line in ["Одна строка — один товар. Код можно не писать — бот даст свой.",
                 "Колонки мест — сколько штук лежит на каждом складе и в каждом магазине. Пусто — не трогать.",
                 "Цена и себестоимость — по желанию. Себестоимость видит только владелец и те, кому он открыл.",
                 "Можно загрузить и отчёт «Остатки» из 1С как есть — места хранения бот найдёт сам."]:
        h.append([line])
    h.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@screen("gdt", module=M)
def template_file(ctx):
    if not may(ctx):
        return Reply(text="Товары загружают владелец, админ и закупки.")
    return Reply(text="⬇️ Шаблон: колонки мест — по вашим складам и магазинам. Заполните и загрузите в «📥 Товары из Excel».",
                 files=[("Товары и остатки — шаблон.xlsx", template(ctx))], buttons=[[Button("‹ Товары из Excel", "gdu")]])


def receive(ctx, name, data):
    """Файл из «📥 Товары из Excel» → разобрать и показать проверку (ничего не записывая)."""
    if ctx.demo:
        return [Reply(text="В демо товары уже есть — загружать некуда. У настоящего клиента здесь грузится его файл.")]
    if not may(ctx):
        return [Reply(text="Товары загружают владелец, админ и закупки.")]
    if not str(name).lower().endswith((".xls", ".xlsx")):
        return [Reply(text=f"«{name}» — нужен Excel (.xls или .xlsx).")]
    try:
        res = parse(read_grid(data))
    except Exception:                                     # noqa: BLE001 — битый файл: сказать по-человечески
        return [upload(ctx, f"«{name}» не открылся — сохраните его в Excel заново и загрузите ещё раз.")]
    if not res.get("items"):
        return [upload(ctx, "; ".join(res.get("errors") or ["в файле нет товаров"]))]
    res.update(name=str(name)[:80], who=A._who(ctx), ts=datetime.now().strftime("%d.%m %H:%M"),
               map={p: guess(ctx, p) for p in res["places"]})
    ctx.db.put(KEY, res)
    return [upload(ctx)]


@screen("gdu", module=M)
def upload(ctx, note=None):
    if not may(ctx):
        return Reply(text="Товары загружают владелец, админ и закупки.")
    ctx.session["upto"] = "gdu"
    ctx.save()
    v = View("📥 Товары из Excel", ctx.cfg.name)
    if note:
        v.note(note)
    pend = ctx.db.get(KEY)
    if not pend or ctx.demo:
        n = (ctx.db.one("SELECT COUNT(*) AS n FROM goods") or {}).get("n", 0)
        v.ui("rows", {"items": [{"t": "Шаблон Алапы", "s": "колонки мест уже по вашим складам и магазинам", "chip": ["⬇️ скачать", "gdt", "blue"]},
                                {"t": "Или отчёт «Остатки» из 1С", "s": "как есть: места хранения бот найдёт сам"}]},
             f"Сейчас товаров в Алапе: {A._num(n)}")
        v.note("Остатки из файла заменяют остатки этих товаров на выбранных местах. Перед записью — проверка, ничего не потеряется.")
        return Reply(view=v, buttons=[[Button("⬇️ Шаблон", "gdt")]], upload=True)
    items = pend["items"]
    have = {_norm(r["name"]): r["code"] for r in ctx.db.q("SELECT code, name FROM goods")}
    codes = {r["code"] for r in ctx.db.q("SELECT code FROM goods")}
    new = sum(1 for it in items if not (it.get("code") in codes or _norm(it["name"]) in have))
    pcs = sum(sum(it["qty"].values()) for it in items)
    by = {p: sum(it["qty"].get(p, 0) for it in items) for p in pend["places"]}
    v.ui("hero", {"label": f"Файл «{pend['name']}» · {pend['ts']}", "big": f"{A._num(len(items))} товаров", "md": True, "id": "gdu",
                  "pairs": [["новых", A._num(new)], ["уже есть", A._num(len(items) - new)], ["штук", A._num(pcs)]]}, "")
    opts = [[loc, n] for loc, n in SH.locs(ctx)] + [["new", "➕ новый склад"], ["skip", "не загружать"]]
    fields = [{"k": f"p{i}", "t": "seg", "l": f"«{p}» · {A._num(by[p])} шт → куда", "v": pend["map"].get(p, ""), "opts": opts}
              for i, p in enumerate(pend["places"])]
    v.ui("sform", {"fields": fields, "send": [f"✅ Загрузить {A._num(len(items))} товаров", "gdy"], "err": "", "top": [],
                   "note": "«➕ новый склад» — склад с названием из файла появится в Алапе. Остатки на выбранных местах заменятся цифрами из файла."},
         "Места из файла", fallback=[f["l"] for f in fields])
    if pend.get("errors"):
        v.ui("rows", {"items": [{"t": e} for e in pend["errors"][:10]]}, f"Проверить · {len(pend['errors'])}")
    has_price = sum(1 for it in items if it.get("price"))
    v.note(f"Цены продажи есть у {A._num(has_price)} из {A._num(len(items))}." + ("" if has_price else
           " Цены можно загрузить потом тем же шаблоном — только колонки «Название» и «Цена продажи»."))
    return Reply(view=v, buttons=[[Button("✖️ Отменить загрузку", "gdx"), Button("⬇️ Шаблон", "gdt")]], upload=True)


@screen("gdx", module=M)
def cancel(ctx):
    if may(ctx):
        ctx.db.put(KEY, None)
    return upload(ctx, "Загрузка отменена — ничего не записано.")


@screen("gdy", module=M)
def apply(ctx):
    pend = ctx.db.get(KEY)
    if not may(ctx) or not pend or ctx.demo:
        return upload(ctx)
    f = ctx.form or {}
    valid = {loc for loc, _ in SH.locs(ctx)} | {"new", "skip"}
    mp = {}
    for i, p in enumerate(pend["places"]):
        x = str(f.get(f"p{i}", pend["map"].get(p, "")))
        if x not in valid:
            pend["map"] = {**pend["map"], **{q: str(f.get(f"p{k}", pend["map"].get(q, ""))) for k, q in enumerate(pend["places"])}}
            ctx.db.put(KEY, pend)
            return upload(ctx, f"Выберите, куда «{p}».")
        mp[p] = x
    for p, x in mp.items():
        if x == "new":
            mp[p] = SH.add_wh(ctx.db, p)
    see = A.can_see(ctx, "себестоимость при загрузке товаров")
    have = {_norm(r["name"]): r["code"] for r in ctx.db.q("SELECT code, name FROM goods")}
    codes = {r["code"] for r in ctx.db.q("SELECT code FROM goods")}
    seq = max([int(c[1:]) for c in codes if re.fullmatch(r"A\d{5}", c)] + [0])
    now = datetime.now().isoformat(timespec="seconds")
    added = upd = 0
    for it in pend["items"]:
        code = it.get("code") if it.get("code") else have.get(_norm(it["name"]))
        if not code:
            seq += 1
            code = f"A{seq:05d}"
        if code in codes:
            sets, vals = ["name = ?", "ts = ?"], [it["name"], now]
            for k in ("grp", "supplier", "price") + (("cost",) if see else ()):
                if it.get(k) not in (None, ""):
                    sets.append(f"{k} = ?")
                    vals.append(it[k])
            ctx.db.x(f"UPDATE goods SET {', '.join(sets)} WHERE code = ?", *vals, code)
            upd += 1
        else:
            ctx.db.x("INSERT INTO goods (code, name, grp, price, cost, qty, supplier, ts) VALUES (?,?,?,?,?,0,?,?)", code, it["name"],
                     it.get("grp", ""), it.get("price") or 0, (it.get("cost") or 0) if see else 0, it.get("supplier", ""), now)
            codes.add(code)
            added += 1
        have[_norm(it["name"])] = code
        for p, q in it["qty"].items():
            loc = mp.get(p)
            if not loc or loc == "skip":
                continue
            ctx.db.x("INSERT INTO stock_loc (code, loc, qty, smp) VALUES (?,?,?,0) ON CONFLICT(code, loc) DO UPDATE SET qty = ?",
                     code, loc, q, q)
        ctx.db.x("UPDATE goods SET qty = COALESCE((SELECT SUM(qty) FROM stock_loc WHERE code = ?), qty) WHERE code = ?", code, code)
    ctx.db.put(KEY, None)
    if see:
        A.log(ctx, f"загрузил товары из «{pend['name']}»: новых {added}, обновлено {upd}", force=True)
    ctx.db.audit(A._who(ctx), "goods_import", f"{pend['name']}: новых {added}, обновлено {upd}")
    v = View("✅ Товары загружены", pend["name"])
    v.ui("rows", {"items": [{"t": "Новых товаров", "v": A._num(added)}, {"t": "Обновлено", "v": A._num(upd)}] +
                  [{"t": f"«{p}» → {SH.loc_name(ctx, loc)}", "v": ""} for p, loc in mp.items() if loc != "skip"],
                  "foot": [["📦 Товары", "gd"], ["📍 Остатки по местам", "stl"]]}, "")
    return Reply(view=v)
