"""Веб-провод: своё приложение вместо мессенджера (web/app.html, ставится на телефон значком).

Приложение шлёт нажатия на /api/*, провод передаёт их тому же App, что и Telegram, и отдаёт экраны JSON-ом.
Кто это: телефон получает случайный ключ (cookie «kid») → свой номер пользователя в базе офиса (app.office()).
Сообщения другим людям (заявка админу, «доступ открыт» гостю) копятся в их ленте «🔔» и приходят при следующем
обращении (или опросе /api/poll раз в 30 секунд).
Админ: один раз открыть /?admin=<WEB_ADMIN_KEY> — этот телефон становится админом (ключ лежит только на сервере).

Запуск: python3 run.py <клиент> --web [порт]   (служба deploy/kpi-web@.service, за Caddy).
"""
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import push
from core.ui import screens
from core.ui.web import BRAND, app_name, icon_png, reply_json, static_files

log = logging.getLogger("kpi")
ROOT = Path(__file__).resolve().parent.parent.parent
UID_START = 10 ** 12          # номера пользователей приложения — далеко от номеров Telegram
INBOX_MAX = 50
FILES_MAX = 50
HOME_SIDS = ("home", "mon")
TOOLS = ("adm", "ptn", "ptm")     # «для нас» — отдельная кнопка в шапке: пульт админа, кабинет партнёра (не путать с показом)
FILE_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "pdf": "application/pdf", "png": "image/png", "csv": "text/csv; charset=utf-8"}


class WebApi:
    def __init__(self, app, admin_key="", base_url="", name=None, short=BRAND):
        self.app, self.admin_key = app, admin_key
        self.lock = threading.Lock()
        self.files = {}
        app.web_url = base_url.rstrip("/")
        for uid in self._office().get("web_admins") or []:
            app.admins.add(int(uid))
        self.name = name or app_name(app.cfg.name, app.demo)      # «Алапа · демо» / «Алапа · <фирма>»
        raw = (ROOT / "web" / "app.html").read_text(encoding="utf-8")
        import hashlib
        # версия страницы и значка: телефон со старой страницей перезагрузится сам, значок и название не возьмёт из старой памяти
        self.ver = hashlib.sha1((raw + self.name + short).encode() + icon_png(180)).hexdigest()[:10]
        self.static = static_files(self.name, short, live=True, ver=self.ver)
        self.page = raw.replace("__NAME__", self.name).replace("__SHORT__", short).replace("__VER__", self.ver)

    def _office(self):
        return self.app.office()

    # ---------- кто это ----------

    def _device(self, token, create):
        devs = self._office().get("web_devices") or {}
        if token and token in devs:
            return token, int(devs[token]), False
        if not create:
            return None, None, False
        seq = int(self._office().get("web_seq") or UID_START) + 1
        token = secrets.token_urlsafe(24)
        devs[token] = seq
        self._office().put("web_seq", seq)
        self._office().put("web_devices", devs)
        return token, seq, True

    def _make_admin(self, uid):
        admins = set(self._office().get("web_admins") or [])
        admins.add(uid)
        self._office().put("web_admins", sorted(admins))
        self.app.admins.add(uid)

    # ---------- лента сообщений для тех, кого сейчас нет ----------

    def _deliver(self, uid, out):
        mine, others = [], {}
        for r in out:
            if not r.to or r.to == uid:
                mine.append(r)
            else:
                others.setdefault(int(r.to), []).append(r)
        for to, rs in others.items():
            key = f"inbox:{to}"
            items = [reply_json(r, self._files()) for r in rs]
            box = (self._office().get(key) or []) + items
            self._office().put(key, box[-INBOX_MAX:])
            if push.has(self._office(), to):          # 🔔 на телефон: первое сообщение, остальные — «и ещё N»
                title, body = push.summary(items[0])
                if len(items) > 1:
                    body = f"{body} · и ещё {len(items) - 1}"
                push.send_async(self._office, to, title, body)
        return mine

    def _files(self):
        while len(self.files) > FILES_MAX:
            self.files.pop(next(iter(self.files)))
        return self.files

    def _inbox(self, uid):
        key = f"inbox:{uid}"
        box = self._office().get(key) or []
        if box:
            self._office().put(key, [])
        return box

    # ---------- ответ приложению ----------

    def _answer(self, uid, out, sid=None):
        mine = self._deliver(uid, out)
        ctx = self.app.ctx(uid)
        tabs, extra, tools = [], [], []
        from core.demo import has_access
        allowed = has_access(self.app, self._office(), uid, ctx.today)
        for row in (screens.bottom_rows(ctx) if allowed else []):   # без доступа к демо — ни вкладок, ни меню
            for label, to in row:
                (tools if to in TOOLS else extra if to.startswith("sw:") else tabs).append([label, to])
        msgs = [reply_json(r, self._files()) for r in mine]
        first = msgs[0].get("view") if msgs else None
        home = sid in HOME_SIDS or (sid == "sw" and first and first.get("title") == ctx.cfg.name)
        who = ctx.me["fio"] if ctx.me and allowed else ""
        role_t = "" if not allowed else (ctx.position_name if ctx.role in ("staff", "head") and ctx.position_name else
                                         ctx.cfg.role_title(ctx.role) if ctx.role not in ("guest", "admin") else
                                         "Администратор" if ctx.role == "admin" else "")
        return {"ver": self.ver, "msgs": msgs, "inbox": self._inbox(uid), "home": bool(home), "tabs": tabs, "extra": extra, "tools": tools,
                "company": ctx.cfg.name, "who": who, "role": role_t, "guest": not allowed or ctx.role == "guest",
                "push": None if self.app.demo or not allowed or ctx.role == "guest" else push.has(self._office(), uid)}

    # ---------- фоновая работа: рассылки и отчёты (core/jobs/worker.py) ----------

    def background(self):
        """Работник: рассылки по расписанию и забор отчётов. В демо — None."""
        if self.app.demo:
            return None
        from core.jobs.worker import Worker
        return Worker(self.app, lambda out: self._deliver(0, out))

    def tick(self, worker, now=None):
        """Одна минута работника — под тем же замком, что и нажатия."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = now or datetime.now(ZoneInfo(self.app.cfg.timezone)).replace(tzinfo=None)
        with self.lock:
            return worker.tick(now)

    def handle(self, method, path, query, body, token):
        """→ (код, {заголовки}, байты). Без сети — так провод и проверяется в тестах."""
        u = path.split("?")[0]
        if method == "GET":
            if u in ("/", "/index.html"):
                return 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache"}, self.page.encode()
            name = u.lstrip("/")
            if name in self.static:
                data, ctype = self.static[name]
                return 200, {"Content-Type": ctype, "Cache-Control": "no-cache" if name in ("sw.js", "manifest.webmanifest") else "max-age=3600"}, data
            if u.startswith("/api/file/") and u.count("/") == 4:   # /api/file/<id>/pages и /api/file/<id>/p<N> — страницы PDF картинками
                from core.ui.views import pdf_pages
                fid, part = u.split("/")[3], u.split("/")[4]
                f = self.files.get(fid)
                pages = pdf_pages(f[1]) if f and f[0].lower().endswith(".pdf") else []
                if part == "pages":
                    return self._json({"n": len(pages)})
                if part.startswith("p") and part[1:].isdigit() and int(part[1:]) < len(pages):
                    return 200, {"Content-Type": "image/jpeg", "Cache-Control": "max-age=3600"}, pages[int(part[1:])]
                return 404, {"Content-Type": "text/plain; charset=utf-8"}, "Файл устарел — откройте экран заново".encode()
            if u.startswith("/api/file/"):
                f = self.files.get(u.rsplit("/", 1)[-1])
                if not f:
                    return 404, {"Content-Type": "text/plain; charset=utf-8"}, "Файл устарел — откройте экран заново".encode()
                from urllib.parse import quote
                # Свой тип файла и «inline»: телефон показывает Excel / Word / PDF просмотром с кнопкой «Поделиться»,
                # а приложение открывает его в отдельном окне (target=_blank) — назад в приложение одной кнопкой «Готово».
                return 200, {"Content-Type": FILE_TYPES.get(f[0].rsplit(".", 1)[-1].lower(), "application/octet-stream"),
                             "Content-Disposition": f"inline; filename*=UTF-8''{quote(f[0])}"}, f[1]
            if u == "/api/push-key":                    # открытый ключ сервера для подписки на уведомления
                with self.lock:
                    return self._json({"key": push.public_key(self._office())})
            if u == "/api/poll":
                with self.lock:
                    tok, uid, _ = self._device(token, create=False)
                    box = self._inbox(uid) if uid else []
                return self._json({"inbox": box})
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, "Нет такой страницы".encode()
        if method != "POST" or not u.startswith("/api/"):
            return 405, {"Content-Type": "text/plain; charset=utf-8"}, b"method"
        act = u[len("/api/"):]
        with self.lock:
            tok, uid, new = self._device(token, create=True)
            try:
                if act == "start":
                    if self.admin_key and body.get("admin") and secrets.compare_digest(str(body["admin"]).encode(), self.admin_key.encode()):
                        self._make_admin(uid)
                    out, sid = self.app.start(uid, "", str(body.get("arg") or "")[:40]), "home"
                elif act == "press":
                    to = str(body.get("to") or "home")[:64]
                    out = self.app.press(uid, to, top=bool(body.get("top")))
                    sid = getattr(self.app, "last_sid", to.split(":")[0])
                elif act == "push":                        # подписка телефона на уведомления (или отписка)
                    if body.get("off"):
                        push.unsubscribe(self._office(), uid, body.get("endpoint"))
                        return self._json({"ok": True, "on": False})
                    ok = push.subscribe(self._office(), uid, body.get("sub") or {})
                    return self._json({"ok": ok, "on": ok})
                elif act == "upload":                      # файл отчёта: {name, data: base64}
                    import base64
                    try:
                        raw = base64.b64decode(str(body.get("data") or ""), validate=False)
                    except ValueError:
                        raw = b""
                    out = self.app.upload(uid, str(body.get("name") or "")[:120], raw)
                    sid = "up"
                elif act == "text":
                    out = self.app.text(uid, str(body.get("text") or "")[:4000])
                    sid = getattr(self.app, "last_sid", "")
                else:
                    return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"api"
                data = self._answer(uid, out, sid)
            except Exception:                       # noqa: BLE001
                log.exception("веб: %s", act)
                data = {"msgs": [{"text": "⚠️ Не получилось. Попробуйте ещё раз.", "buttons": [["🏠 Главная", "home"]]}],
                        "inbox": [], "tabs": [], "extra": [], "home": False}
        code, headers, raw = self._json(data)
        if new:
            headers["Set-Cookie"] = f"kid={tok}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax; Secure"
        return code, headers, raw

    # ---------- 1С: сетевая папка и прямая отправка (core/onec_link.py) ----------

    def handle_raw(self, method, path, headers, body):
        """Запросы без cookie и JSON: /dav/… (сетевая папка) и /api/1c (файл от 1С). → (код, заголовки, байты)."""
        from core import onec_link as L
        if self.app.demo:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, "В демо отчёты не принимаются".encode()
        u = path.split("?")[0]
        if u == "/dav" or u.startswith("/dav/"):
            with self.lock:
                return L.dav(self.app, method, path, headers, body)
        if u == "/api/1c":
            if method != "POST":
                return 405, {"Content-Type": "text/plain; charset=utf-8"}, "POST с файлом в теле".encode()
            q = parse_qs(urlparse(path).query)
            with self.lock:
                if not L.check_key(self.app.db(), headers.get("X-Key") or (q.get("key") or [""])[0]):
                    return 403, {"Content-Type": "text/plain; charset=utf-8"}, "неверный ключ".encode()
                code, text = L.receive_direct(self.app, (q.get("name") or [""])[0], body, lambda out: self._deliver(0, out))
            return code, {"Content-Type": "text/plain; charset=utf-8"}, text.encode()
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, b""

    @staticmethod
    def _json(data):
        return 200, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, \
            json.dumps(data, ensure_ascii=False).encode()


def serve(api, port=8101, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kpi"

        def _raw(self, method):
            n = min(int(self.headers.get("Content-Length") or 0), 16 * 1024 * 1024)
            body = self.rfile.read(n) if n else b""
            code, headers, raw = api.handle_raw(method, self.path, self.headers, body)
            self.send_response(code)
            for k, v in headers.items():
                if k != "Content-Length":
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw) if method != "HEAD" else int(headers.get("Content-Length", 0))))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(raw)

        def _is_raw(self):
            u = self.path.split("?")[0]
            return u == "/dav" or u.startswith("/dav/") or u == "/api/1c"

        def _go(self, method):
            if self._is_raw():
                return self._raw(method)
            body = {}
            if method == "POST":
                limit = 22_000_000 if self.path.startswith("/api/upload") else 1_000_000   # файл отчёта до 15 МБ в base64
                n = min(int(self.headers.get("Content-Length") or 0), limit)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = {}
            token = None
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "kid":
                    token = v
            code, headers, raw = api.handle(method, self.path, parse_qs(urlparse(self.path).query), body, token)
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def do_OPTIONS(self):
            self._raw("OPTIONS") if self._is_raw() else self.send_error(405)

        def do_HEAD(self):
            self._raw("HEAD") if self._is_raw() else self.send_error(405)

    for _m in ("PROPFIND", "PROPPATCH", "PUT", "DELETE", "MOVE", "LOCK", "UNLOCK", "MKCOL"):    # сетевая папка (WebDAV)
        setattr(Handler, f"do_{_m}", lambda self, m=_m: self._raw(m) if self._is_raw() else self.send_error(405))

        def log_message(self, fmt, *args):     # адреса и ключи в журнал не пишем
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    log.info("веб-приложение %s: http://%s:%s", api.name, host, port)
    worker = api.background()
    if worker:
        def loop():
            import time
            while True:
                time.sleep(60 - time.time() % 60 + 1)          # раз в минуту, в начале минуты
                try:
                    api.tick(worker)
                except Exception:                              # noqa: BLE001
                    log.exception("фоновая работа")
        threading.Thread(target=loop, daemon=True, name="worker").start()
        log.info("рассылки и забор отчётов: %s", ", ".join(t for t, _ in worker.srcs) or "источников нет — только кнопка «📥 Отчёты»")
    srv.serve_forever()
