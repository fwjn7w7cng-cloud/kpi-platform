"""Демо-приложение (веб, ставится на телефон значком) из экранов демо-бота.

    python3 tools/web_demo.py /var/www/kpi/demo          # собрать в папку (её раздаёт веб-сервер)

Робот проходит экраны демо по ролям (как tools/crawl.py), складывает их в JSON и вставляет в web/app_template.html.
Рядом кладёт manifest.webmanifest, значки и sw.js — без них телефон не «установит» страницу как приложение.
Это снимок: цифры на день сборки, нажатия ничего не сохраняют. Пересобирать — той же командой (например, раз в сутки).
"""
import json
import shutil
import sys
import tempfile
from collections import deque
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.app import App                       # noqa: E402
from core.calendar import month_of, month_title  # noqa: E402
from core.ui import screens                    # noqa: E402
from core.ui.views import Grid                 # noqa: E402

ROLES = [("owner", "Директор"), ("head", "Руководитель продаж"), ("sv", "Супервайзер"), ("rep", "Торговый"),
         ("staff", "Склад / водитель"), ("accountant", "Бухгалтер")]
SKIP = ("sw:", "mon:", "bk:", "viewas", "home")
MAXN = {"owner": 900, "head": 600, "sv": 500, "rep": 400, "staff": 120, "accountant": 300}
NAME = "Опт-Партнёр KPI"


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


def _view(v):
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
            out.append({"k": "cards", "title": title, "items": [
                {"title": c.title, "value": c.value, "lines": [str(x) for x in c.lines], "phone": c.phone,
                 "color": c.color, "note": c.note, "nc": c.note_color,
                 "bar": [float(x) for x in c.bar] if c.bar else None} for c in items]})
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


def _reply(r):
    d = {}
    if r.view:
        d["view"] = _view(r.view)
    if r.text:
        d["text"] = r.text
    if r.ask:
        d["ask"] = r.ask
    if r.files:
        d["files"] = [f[0] for f in r.files]
    rows = [[[b.label, b.to] for b in row if b.to != "home" and not b.to.startswith("bk:")] for row in r.buttons]
    d["buttons"] = [row for row in rows if row]
    return d


def export(today=None, roles=None, limit=None):
    """Снимок экранов демо: {company, day, month, roles: {роль: {title, who, bottom, screens}}}."""
    today = today or date.today()
    tmp = Path(tempfile.mkdtemp())
    try:
        app = App("demo", today=today, admins=[], data_dir=tmp)
        ind = app.cfg.industry
        data = {"company": app.cfg.name, "day": today.strftime("%d.%m.%Y"),
                "month": month_title(month_of(today)).lower(), "roles": {}}
        for i, (role, title) in enumerate(ROLES):
            if roles and role not in roles:
                continue
            uid = 500 + i
            app.press(uid, "sw:ed:control")
            app.press(uid, f"sw:role:{role}")
            ctx = app.ctx(uid)
            bottom = [[lab, to] for row in screens.bottom_rows(ctx) for lab, to in row if not to.startswith("sw:")]
            scr, seen = {}, set()
            q = deque([("home", 0)] + [(to, 1) for _, to in bottom if to != "home"])
            cap = limit or MAXN[role]
            while q and len(scr) < cap:
                to, depth = q.popleft()
                if to in seen:
                    continue
                seen.add(to)
                rev = app.db(ind).rev()
                out = app.start(uid) if to == "home" else app.press(uid, to)
                mine = [r for r in out if not r.to]
                changed = app.db(ind).rev() != rev
                if changed:                         # действие что-то записало — вернуть демо как было
                    app.reset_demo(ind)
                if not mine:
                    continue
                if changed or any(r.ask for r in mine):
                    scr[to] = {"msgs": [_reply(r) for r in mine], "action": 1}
                    continue
                scr[to] = {"msgs": [_reply(r) for r in mine]}
                if depth < 4:
                    for r in mine:
                        for row in r.buttons:
                            for b in row:
                                if b.to and not b.to.startswith(SKIP) and b.to not in seen:
                                    q.append((b.to, depth + 1))
            data["roles"][role] = {"title": title, "who": ctx.me["fio"] if ctx.me else "", "bottom": bottom,
                                   "screens": scr}
        return data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _icon(size, path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (size, size), "#1F5FD1")
    d = ImageDraw.Draw(img)
    for y in range(size):                           # мягкий градиент сверху вниз
        t = y / size
        d.line([(0, y), (size, y)], fill=(int(31 + 28 * t), int(95 + 44 * t), int(209 + 8 * t)))
    try:
        f = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "Inter-Bold.otf"), int(size * 0.34))
    except OSError:
        f = ImageFont.load_default()
    d.text((size / 2, size / 2), "KPI", fill="white", font=f, anchor="mm")
    img.save(path)


MANIFEST = {"name": NAME, "short_name": "KPI демо", "start_url": "./", "scope": "./", "display": "standalone",
            "background_color": "#F3F5F8", "theme_color": "#1F5FD1", "lang": "ru",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}

SW = """// Демо-приложение: последняя открытая версия доступна и без сети.
const C = "kpi-demo";
self.addEventListener("install", e => { self.skipWaiting(); e.waitUntil(caches.open(C).then(c => c.addAll(["./", "manifest.webmanifest", "icon-192.png"]))); });
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(C).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
"""


def build(out, data):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tpl = (ROOT / "web" / "app_template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (out / "index.html").write_text(tpl.replace("__DATA__", js), encoding="utf-8")
    (out / "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "sw.js").write_text(SW, encoding="utf-8")
    for s in (180, 192, 512):
        _icon(s, out / f"icon-{s}.png")
    return out / "index.html"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Куда собрать? python3 tools/web_demo.py /var/www/kpi/demo")
    data = export()
    page = build(sys.argv[1], data)
    n = sum(len(r["screens"]) for r in data["roles"].values())
    print(f"✅ Демо-приложение: {page} · экранов {n} · {page.stat().st_size // 1024} КБ")
