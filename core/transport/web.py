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

from core.ui import screens
from core.ui.web import reply_json, static_files

log = logging.getLogger("kpi")
ROOT = Path(__file__).resolve().parent.parent.parent
UID_START = 10 ** 12          # номера пользователей приложения — далеко от номеров Telegram
INBOX_MAX = 50
FILES_MAX = 50
HOME_SIDS = ("home", "mon")
FILE_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "pdf": "application/pdf", "png": "image/png", "csv": "text/csv; charset=utf-8"}


class WebApi:
    def __init__(self, app, admin_key="", base_url="", name=None, short="KPI"):
        self.app, self.admin_key = app, admin_key
        self.lock = threading.Lock()
        self.files = {}
        app.web_url = base_url.rstrip("/")
        for uid in self._office().get("web_admins") or []:
            app.admins.add(int(uid))
        self.name = name or (app.cfg.name + (" · демо" if app.demo else ""))
        self.static = static_files(self.name, short, live=True)
        self.page = (ROOT / "web" / "app.html").read_text(encoding="utf-8").replace("__NAME__", self.name)

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
            box = (self._office().get(key) or []) + [reply_json(r, self._files()) for r in rs]
            self._office().put(key, box[-INBOX_MAX:])
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
        tabs, extra = [], []
        from core.demo import has_access
        allowed = has_access(self.app, self._office(), uid, ctx.today)
        for row in (screens.bottom_rows(ctx) if allowed else []):   # без доступа к демо — ни вкладок, ни меню
            for label, to in row:
                (extra if to.startswith("sw:") or to == "gst" else tabs).append([label, to])
        msgs = [reply_json(r, self._files()) for r in mine]
        first = msgs[0].get("view") if msgs else None
        home = sid in HOME_SIDS or (sid == "sw" and first and first.get("title") == ctx.cfg.name)
        who = ctx.me["fio"] if ctx.me and allowed else ""
        role_t = "" if not allowed else (ctx.position_name if ctx.role in ("staff", "head") and ctx.position_name else
                                         ctx.cfg.role_title(ctx.role) if ctx.role not in ("guest", "admin") else
                                         "Администратор" if ctx.role == "admin" else "")
        return {"msgs": msgs, "inbox": self._inbox(uid), "home": bool(home), "tabs": tabs, "extra": extra,
                "company": ctx.cfg.name, "who": who, "role": role_t, "guest": not allowed or ctx.role == "guest"}

    def handle(self, method, path, query, body, token):
        """→ (код, {заголовки}, байты). Без сети — так провод и проверяется в тестах."""
        u = path.split("?")[0]
        if method == "GET":
            if u in ("/", "/index.html"):
                return 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache"}, self.page.encode()
            name = u.lstrip("/")
            if name in self.static:
                data, ctype = self.static[name]
                return 200, {"Content-Type": ctype, "Cache-Control": "no-cache" if name == "sw.js" else "max-age=86400"}, data
            if u.startswith("/api/file/"):
                f = self.files.get(u.rsplit("/", 1)[-1])
                if not f:
                    return 404, {"Content-Type": "text/plain; charset=utf-8"}, "Файл устарел — откройте экран заново".encode()
                from urllib.parse import quote
                # Свой тип файла и «inline»: телефон показывает Excel / Word / PDF просмотром с кнопкой «Поделиться»,
                # а приложение открывает его в отдельном окне (target=_blank) — назад в приложение одной кнопкой «Готово».
                return 200, {"Content-Type": FILE_TYPES.get(f[0].rsplit(".", 1)[-1].lower(), "application/octet-stream"),
                             "Content-Disposition": f"inline; filename*=UTF-8''{quote(f[0])}"}, f[1]
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

    @staticmethod
    def _json(data):
        return 200, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, \
            json.dumps(data, ensure_ascii=False).encode()


def serve(api, port=8101, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kpi"

        def _go(self, method):
            body = {}
            if method == "POST":
                n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
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

        def log_message(self, fmt, *args):     # адреса и ключи в журнал не пишем
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    log.info("веб-приложение %s: http://%s:%s", api.name, host, port)
    srv.serve_forever()
