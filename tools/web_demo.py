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
from core.ui.web import reply_json, static_files  # noqa: E402

ROLES = [("owner", "Директор"), ("head", "Руководитель продаж"), ("sv", "Супервайзер"), ("rep", "Торговый"),
         ("pos:buy_head", "Начальник закупок"), ("pos:buyer", "Менеджер по закупкам"), ("pos:storekeeper", "Кладовщик"),
         ("pos:dispatcher", "Диспетчер"), ("pos:driver", "Водитель"), ("pos:hr", "Кадры"), ("accountant", "Бухгалтер")]
SKIP = ("sw:", "mon:", "bk:", "viewas", "home")
MAXN = {"owner": 900, "head": 600, "sv": 500, "rep": 400, "accountant": 300}     # остальные — 150
NAME = "Опт-Партнёр KPI"


def _reply(r):
    d = reply_json(r)
    d["buttons"] = [row for row in ([[l, to] for l, to in row if not to.startswith("bk:")] for row in d["buttons"]) if row]
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
            app.press(uid, "sw:ed:max")
            app.press(uid, f"sw:{role}" if role.startswith("pos:") else f"sw:role:{role}")
            ctx = app.ctx(uid)
            bottom = [[lab, to] for row in screens.bottom_rows(ctx) for lab, to in row if not to.startswith("sw:")]
            scr, seen = {}, set()
            q = deque([("home", 0)] + [(to, 1) for _, to in bottom if to != "home"])
            cap = limit or MAXN.get(role, 150)
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


def build(out, data):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tpl = (ROOT / "web" / "app_template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (out / "index.html").write_text(tpl.replace("__DATA__", js), encoding="utf-8")
    for name, (data_, _) in static_files(NAME, "KPI демо").items():
        (out / name).write_bytes(data_)
    return out / "index.html"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Куда собрать? python3 tools/web_demo.py /var/www/kpi/demo")
    data = export()
    page = build(sys.argv[1], data)
    n = sum(len(r["screens"]) for r in data["roles"].values())
    print(f"✅ Демо-приложение: {page} · экранов {n} · {page.stat().st_size // 1024} КБ")
