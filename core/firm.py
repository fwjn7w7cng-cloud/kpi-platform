"""⚙️ Настройки фирмы — у владельца и администратора клиента (Адам 20.11, эскиз FS.png: «после запуска их админ сам вносит изменения»).

Один экран со всем, что можно менять в своём приложении: люди (имена, должности, роли, ссылки), кто где работает (`pw`),
кто какие магазины видит (`fsl`), товары и разделы, склады и чьи они, магазины (справочники `rf`), загрузка из Excel,
логотип и название значка (`fbr`), оплата (`bl`). Ничего нового в данных — экраны ведут на уже готовые.
"""
import base64
from datetime import datetime

from core.ui.kit import Button, Reply
from core.ui.registry import screen
from core.ui.views import View


def ok(ctx):
    return ctx.role in ("owner", "admin") or ctx.admin


@screen("fs")
def settings(ctx, note=None):
    if not ok(ctx):
        return Reply(text="Настройки фирмы — у владельца и администратора.")
    from core.ui.retail import shops
    v = View("⚙️ Настройки фирмы", ctx.cfg.name)
    if note:
        v.note(note)
    es = ctx.db.employees()
    leads = [e for e in es if e["role"] in ("head", "sv", "owner")]
    v.ui("rows", {"items": [{"t": "📘 Инструкция администратора", "s": "что где нажимать — по шагам", "chip": ["›", "ag", ""]}]}, "")
    v.ui("rows", {"items": [
        {"t": f"Сотрудники · {len(es)}", "s": "имена, должность, роль, ссылки для входа, уволить", "chip": ["›", "pa", ""]},
        {"t": "📍 Кто где работает", "s": "магазины, склады, команды — перевести одним нажатием", "chip": ["›", "pw", ""]},
    ] + ([{"t": f"👔 Кто какие магазины видит · {len(leads)}", "s": "руководители и владельцы", "chip": ["›", "fsl", ""]}]
         if len(shops(ctx.cfg)) > 1 else [])}, "👥 Люди")
    if ctx.can("goods"):
        n = (ctx.db.one("SELECT COUNT(*) AS n FROM goods WHERE off = 0") or {}).get("n", 0)
        from modules.catalog import groups
        from modules.ship import whs
        v.ui("rows", {"items": [
            {"t": f"Товары · {n}", "s": "цены, разделы, новый товар, архив", "chip": ["›", "gd", ""]},
            {"t": "📥 Загрузить из Excel", "s": "шаблон или «Остатки» из 1С", "chip": ["›", "gdu", ""]},
            {"t": f"Разделы · {len(groups(ctx.db))} · Склады · {len(whs(ctx))}" + (f" · Магазины · {len(shops(ctx.cfg))}" if shops(ctx.cfg) else ""),
             "s": "переименовать, добавить, убрать; чей склад", "chip": ["›", "rf", ""]},
        ]}, "📦 Товар")
    items = [{"t": "🎨 Логотип и название на значке", "s": "цвета приложения — из логотипа", "chip": ["›", "fbr", ""]}]
    if not ctx.demo:
        items.append({"t": "💳 Оплата", "s": "сколько и когда платить", "chip": ["›", "bl", ""]})
    v.ui("rows", {"items": items}, "🎨 Вид и оплата")
    return Reply(view=v)


@screen("fsl")
def leaders(ctx):
    if not ok(ctx):
        return settings(ctx)
    from core.ui.retail import SEES_KEY, shops
    sn = dict(shops(ctx.cfg))
    sees = ctx.db.get(SEES_KEY) or {}
    items = []
    for e in ctx.db.employees():
        if e["role"] not in ("head", "sv", "owner"):
            continue
        mine = sees.get(str(e["id"])) or []
        items.append({"t": e["fio"], "s": ", ".join(sn.get(s, s) for s in mine) if mine else "все магазины",
                      "chip": ["🏬 изменить", f"shs:{e['id']}", ""]})
    v = View("👔 Кто какие магазины видит", ctx.cfg.name)
    v.ui("rows", {"items": items, "empty": "Руководителей и владельцев пока нет"}, "")
    return Reply(view=v, buttons=[[Button("‹ Настройки", "fs")]])


@screen("fbr")
def brand(ctx, note=None):
    if not ok(ctx):
        return settings(ctx)
    from core import brand as BR
    b = BR.get(ctx.office)
    ctx.session["upto"] = "brandme"
    ctx.save()
    v = View("🎨 Логотип и название", ctx.cfg.name)
    if note:
        v.note(note)
    if not BR.logo_bytes(b):
        v.note("Логотипа пока нет. «Выбрать логотип» внизу — PNG или JPG. Цвета приложения бот возьмёт из него сам.")
    v.ui("form", {"fields": [{"k": "name", "l": "Название на значке телефона", "v": b.get("name") or ctx.cfg.name, "w": True},
                             {"k": "c1", "l": "Главный цвет", "v": b.get("c1", ""), "ph": "#0B1F4D"},
                             {"k": "c2", "l": "Второй цвет", "v": b.get("c2", ""), "ph": "#E0B04A"}],
                  "send": ["Сохранить", "fbrs"], "hint": "Приложение у всех обновится само при следующем открытии."}, "")
    files = [("значок.png", BR.icon(512, b))] if BR.logo_bytes(b) else []
    return Reply(view=v, buttons=[[Button("‹ Настройки", "fs")]], upload="image", files=files)


def _save(ctx, b):
    from core import brand as BR
    ctx.office.put(BR.KEY, {**b, "ts": datetime.now().isoformat()})
    ctx.db.audit(ctx.name(), "brand", f"{b.get('name', '')} {b.get('c1', '')} {b.get('c2', '')}")


@screen("fbrs")
def brand_save(ctx):
    if not ok(ctx):
        return settings(ctx)
    from core import brand as BR
    form = ctx.form or {}
    b = dict(BR.get(ctx.office))
    for k in ("c1", "c2"):
        x = str(form.get(k, "")).strip().upper()
        x = x if not x or x.startswith("#") else "#" + x
        if x and not BR.HEX.fullmatch(x):
            return brand(ctx, note=f"Цвет «{x}» — нужно как #0B1F4D.")
        if x:
            b[k] = x
    b["name"] = str(form.get("name", "")).strip()[:30] or ctx.cfg.name
    _save(ctx, b)
    return brand(ctx, note="✅ Сохранено.")


def receive(ctx, name, data):
    """Логотип своей фирмы (reports.receive по session.upto = brandme)."""
    from core import brand as BR
    if not ok(ctx):
        return [Reply(text="Логотип меняют владелец и администратор.")]
    if not data or len(data) > 8 * 1024 * 1024:
        return [brand(ctx, note="Файл пустой или больше 8 МБ.")]
    png, err = BR.prepare(data)
    if err:
        return [brand(ctx, note=err)]
    b = dict(BR.get(ctx.office))
    b["c1"], b["c2"] = BR.colors(png)
    b["logo"] = base64.b64encode(png).decode()
    b.setdefault("name", ctx.cfg.name)
    _save(ctx, b)
    return [brand(ctx, note=f"✅ Логотип стоит. Цвета: {b['c1']} и {b['c2']}.")]
