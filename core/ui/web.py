"""Экраны для веб-приложения: Reply → JSON (плитки, таблицы, карточки, кнопки) и файлы «установки» (manifest, значки, sw.js).

Одно описание экрана (View) рисуется и в мессенджере (картинка / текст), и в приложении (web/app.html).
"""
import io
import json
from pathlib import Path

from core.ui.views import Grid

ROOT = Path(__file__).resolve().parent.parent.parent


def _cell(c):
    d = {"t": c.text}
    if c.color:
        d["c"] = c.color
    if c.bar:
        d["b"] = [round(float(x), 3) if x is not None else None for x in c.bar]
    if c.chip:
        d["chip"] = 1
    if c.bold:
        d["w"] = 1
    return d


def view_json(v):
    out = []
    for k, data in v.blocks:
        if k == "tiles":
            out.append({"k": "tiles", "items": [[a, b, c] for a, b, c in data]})
        elif k == "table":
            title, head, rows = data
            out.append({"k": "table", "title": title, "head": [str(h) for h in head], "even": isinstance(head, Grid),
                        "first": getattr(head, "first", None),
                        "rows": [{"cells": [_cell(c) for c in r.cells], "sub": r.sub, "mark": r.mark} for r in rows]})
        elif k == "cards":
            title, items = data
            mx = max([float(c.bar) for c in items if isinstance(c.bar, (int, float)) and c.bar] or [1])
            out.append({"k": "cards", "title": title, "items": [
                {"title": c.title, "value": c.value, "lines": [str(x) for x in c.lines], "phone": c.phone,
                 "color": c.color, "note": c.note, "nc": c.note_color,
                 "bar": ([float(c.bar) / mx] if isinstance(c.bar, (int, float)) else [float(x) for x in c.bar])
                 if c.bar else None} for c in items]})
        elif k == "matrix":
            title, names, rows = data
            out.append({"k": "matrix", "title": title, "names": names,
                        "rows": [[lab, [_cell(c) for c in cs]] for lab, cs in rows]})
        elif k == "medals":
            title, items = data
            out.append({"k": "medals", "title": title, "items": [list(x) for x in items]})
        elif k == "lines":
            title, items = data
            out.append({"k": "lines", "title": title, "items": [str(x) for x in items]})
        elif k == "note":
            text, where = data
            if where != "txt":
                out.append({"k": "note", "text": text})
    return {"title": v.title, "sub": v.subtitle, "blocks": out}


def reply_json(r, files=None):
    """files — словарь, куда сложить вложения: {id: (имя, байты)}; в ответ попадают ссылки на них."""
    d = {}
    if r.view:
        d["view"] = view_json(r.view)
    if r.text:
        d["text"] = r.text
    if r.ask:
        d["ask"] = r.ask
    if r.files:
        if files is None:
            d["files"] = [[f[0], ""] for f in r.files]
        else:
            import secrets
            d["files"] = []
            for name, data in r.files:
                fid = secrets.token_hex(8)
                files[fid] = (name, data)
                d["files"].append([name, f"/api/file/{fid}"])
    if r.upload:
        d["upload"] = 1
    rows = [[[b.label, b.to] for b in row if b.to != "home"] for row in r.buttons]
    d["buttons"] = [row for row in rows if row]
    return d


# ---------- «установка» на телефон ----------

BRAND = "Алапа"                                     # имя продукта и фирмы (Адам 30.10): «зарплата» по-чеченски, ударение на первое «а»
BRAND_COLOR = "#15284F"                             # тёмно-синий логотипа
DOMAIN = "kpibotsmith.ru"                            # домен приложений: demo. / <код клиента>. / admin. (Адам сменит — поменять здесь)
LOGO = ROOT / "assets" / "brand" / "alapa-logo.png"  # буква А со стрелкой роста, фон прозрачный


def app_name(cfg_name, demo=False):
    """Как приложение подписано во вкладке и в манифесте: «Алапа · демо» или «Алапа · <фирма>»."""
    return f"{BRAND} · {'демо' if demo else cfg_name}"


def manifest(name, short=BRAND, color=BRAND_COLOR):
    return {"name": name, "short_name": short, "start_url": "./", "scope": "./", "display": "standalone",
            "background_color": "#FFFFFF", "theme_color": color, "lang": "ru",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}


def icon_png(size, pad=None):
    """Значок приложения: логотип на белом. pad — поля по краям долей от стороны; у 512 (маскируемый значок Android)
    поля шире, чтобы круглая маска не срезала низ буквы."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (size, size), "#FFFFFF")
    if pad is None:
        pad = 0.19 if size >= 512 else 0.13
    try:
        logo = Image.open(LOGO).convert("RGBA")
        w = int(size * (1 - 2 * pad))
        logo = logo.resize((w, int(logo.height * w / logo.width)), Image.LANCZOS)
        img.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), logo)
    except OSError:                                 # файла логотипа нет — буква на синем, чтобы приложение всё равно ставилось
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, size, size], fill=BRAND_COLOR)
        try:
            f = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "Inter-Bold.otf"), int(size * 0.5))
        except OSError:
            f = ImageFont.load_default()
        d.text((size / 2, size / 2), "А", fill="white", font=f, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


SW_STATIC = """// Снимок: последняя открытая версия доступна и без сети.
const C = "kpi-app";
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => c.addAll(["./", "manifest.webmanifest", "icon-192.png"]))); });
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(C).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
"""

SW_LIVE = """// Живое приложение: оболочка (страница, значки) без сети открывается, а данные — только с сервера.
const C = "kpi-app-shell";
self.addEventListener("push", e => {
  let d = {}; try { d = e.data ? e.data.json() : {}; } catch (x) { d = {title: "Новое сообщение", body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "Новое сообщение",
    {body: d.body || "", icon: "icon-192.png", badge: "icon-192.png", tag: d.tag || undefined, data: {url: d.url || "/"}}));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(ws => {
    for (const w of ws) if ("focus" in w) return w.focus();
    return clients.openWindow(url);
  }));
});
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => c.addAll(["./", "manifest.webmanifest", "icon-192.png"]))); });
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(C).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
"""


def static_files(name, short=BRAND, live=False):
    """{путь: (байты, тип)} — всё, кроме самой страницы. short — подпись значка на экране телефона."""
    return {"manifest.webmanifest": (json.dumps(manifest(name, short), ensure_ascii=False, indent=1).encode(),
                                     "application/manifest+json"),
            "sw.js": ((SW_LIVE if live else SW_STATIC).encode(), "text/javascript"),
            "icon-180.png": (icon_png(180), "image/png"),
            "icon-192.png": (icon_png(192), "image/png"),
            "icon-512.png": (icon_png(512), "image/png")}
