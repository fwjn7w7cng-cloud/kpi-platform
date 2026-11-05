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
import time
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
PANEL_NAME = "Управление"
TOOLS = ("adm", "ptn", "ptm")     # «для нас» — отдельная кнопка в шапке: пульт админа, кабинет партнёра (не путать с показом)
# Защита приложения, которое смотрит в интернет (Адам 01.11, «обезопась максимально»):
#   заголовки браузеру — не встраивать в чужие сайты, не угадывать тип файла, не отдавать адрес с ключом другим сайтам,
#   скрипты и стили только свои (и шрифты Google);
#   перебор ключей и паролей — счётчик отказов по адресу: AUTH_FAILS отказов за AUTH_WINDOW секунд → 429 на входе (а fail2ban
#   по строке «auth fail … from <ip>» в журнале банит адрес на файрволе); на всё /api — REQ_LIMIT запросов в минуту с адреса.
SEC_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
               "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-src blob:; worker-src 'self'; "
       "manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
AUTH_FAILS, AUTH_WINDOW = 10, 600
REQ_LIMIT, REQ_WINDOW = 300, 60
TOO_MANY = "Слишком много попыток с этого адреса. Подождите 10 минут."
FILE_TYPES = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "pdf": "application/pdf", "png": "image/png", "csv": "text/csv; charset=utf-8"}


class Throttle:
    """Счётчик событий по ключу (адресу) за окно времени: hit() → True, если лимит уже исчерпан."""

    def __init__(self, limit, window):
        self.limit, self.window, self.seen = limit, window, {}

    def _prune(self, now):
        if len(self.seen) > 10000:                      # память не растёт от случайных адресов
            self.seen = {k: v for k, v in self.seen.items() if v and v[-1] > now - self.window}

    def count(self, key, now=None):
        now = now or time.time()
        hits = [t for t in self.seen.get(key, ()) if t > now - self.window]
        self.seen[key] = hits
        return len(hits)

    def hit(self, key, now=None):
        now = now or time.time()
        self._prune(now)
        hits = [t for t in self.seen.get(key, ()) if t > now - self.window] + [now]
        self.seen[key] = hits
        return len(hits) > self.limit

    def blocked(self, key, now=None):
        return self.count(key, now) >= self.limit


class _FileBox:
    """Вложения экрана: кладём с номером владельца — чужой файл по ссылке не отдадим (web.reply_json пишет files[id] = (имя, байты))."""

    def __init__(self, store, uid):
        self.store, self.uid = store, uid

    def __setitem__(self, fid, value):
        self.store[fid] = (value[0], value[1], self.uid)


class WebApi:
    def __init__(self, app, admin_key="", base_url="", name=None, short=BRAND, panel=False, lock=None):
        """panel — приложение управления (Адам 02.11, admin.alapa.pro): та же программа демо, но только «💰 Деньги и клиенты»
        и всё «для нас»; свой значок «Управление»; вход — только по ключу администратора. lock — общий с демо (одно приложение)."""
        self.auth_guard = Throttle(AUTH_FAILS, AUTH_WINDOW)      # отказы на входе: ключ админа, приглашение, папка 1С, ключ 1С
        self.req_guard = Throttle(REQ_LIMIT, REQ_WINDOW)         # всего запросов к /api с адреса
        self.app, self.admin_key, self.panel = app, admin_key, panel
        self.lock = lock or threading.Lock()
        self.files = {}
        if not panel:                                  # ссылки-приглашения ведут в демо, а не в управление
            app.web_url = base_url.rstrip("/")
        if panel:
            name, short = name or f"{BRAND} · {PANEL_NAME}", PANEL_NAME
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
        if self.panel:                                 # утренняя сводка — в приложение управления (core/control.digest)
            pa = set(self._office().get("panel_admins") or [])
            pa.add(uid)
            self._office().put("panel_admins", sorted(pa))

    def _accepted(self, uid):
        """Пустили ли этого человека: в демо — по приглашению, у настоящего клиента — он в списке сотрудников."""
        from core.demo import has_access
        if uid in self.app.admins:
            return True
        ctx = self.app.ctx(uid)
        return bool(has_access(self.app, self._office(), uid, ctx.today) and (self.app.demo or ctx.me))

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
            items = [reply_json(r, self._files(to)) for r in rs]
            box = (self._office().get(key) or []) + items
            self._office().put(key, box[-INBOX_MAX:])
            if push.has(self._office(), to):          # 🔔 на телефон: первое сообщение, остальные — «и ещё N»
                title, body = push.summary(items[0])
                if len(items) > 1:
                    body = f"{body} · и ещё {len(items) - 1}"
                push.send_async(self._office, to, title, body)
        return mine

    def _files(self, uid):
        while len(self.files) > FILES_MAX:
            self.files.pop(next(iter(self.files)))
        return _FileBox(self.files, uid)

    def _file_of(self, fid, uid):
        """Вложение по ссылке — только его владельцу."""
        f = self.files.get(fid)
        return f if f and uid and f[2] == uid else None

    def _fail(self, what, ip):
        """Отказ на входе: в журнал (fail2ban читает «auth fail … from <ip>») и в счётчик адреса."""
        log.warning("auth fail %s from %s", what, ip or "-")
        self.auth_guard.hit(ip or "-")

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
        msgs = [reply_json(r, self._files(uid)) for r in mine]
        first = msgs[0].get("view") if msgs else None
        home = sid in HOME_SIDS or (sid in ("sw", "swb") and first and first.get("title") == ctx.cfg.name)
        who = ctx.me["fio"] if ctx.me and allowed else ""
        role_t = "" if not allowed else (ctx.position_name if ctx.role in ("staff", "head") and ctx.position_name else
                                         ctx.cfg.role_title(ctx.role) if ctx.role not in ("guest", "admin") else
                                         "Администратор" if ctx.role == "admin" else "")
        return {"ver": self.ver, "msgs": msgs, "inbox": self._inbox(uid), "home": bool(home), "tabs": tabs, "extra": extra, "tools": tools,
                "company": PANEL_NAME if self.panel else ctx.cfg.name, "who": who, "role": role_t, "guest": not allowed or ctx.role == "guest",
                "push": None if self.app.demo or not allowed or ctx.role == "guest" else push.has(self._office(), uid)}

    # ---------- фоновая работа: рассылки и отчёты (core/jobs/worker.py) ----------

    def background(self):
        """Работник: рассылки по расписанию и забор отчётов. В демо — только утренняя сводка «💰 Деньги и клиенты» админу."""
        if self.app.demo:
            from core.control import Morning
            return Morning(self.app, lambda out: self._deliver(0, out))
        from core.jobs.worker import Worker
        return Worker(self.app, lambda out: self._deliver(0, out))

    def tick(self, worker, now=None):
        """Одна минута работника — под тем же замком, что и нажатия."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = now or datetime.now(ZoneInfo(self.app.cfg.timezone)).replace(tzinfo=None)
        with self.lock:
            return worker.tick(now)

    def handle(self, method, path, query, body, token, ip=""):
        """→ (код, {заголовки}, байты). Без сети — так провод и проверяется в тестах. ip — адрес телефона (для защиты от перебора)."""
        u = path.split("?")[0]
        if u.startswith("/api/") and self.req_guard.hit(ip or "-"):
            return 429, {"Content-Type": "text/plain; charset=utf-8", "Retry-After": "60"}, TOO_MANY.encode()
        if method == "GET":
            if u in ("/", "/index.html"):
                return 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache",
                             "Content-Security-Policy": CSP}, self.page.encode()
            name = u.lstrip("/")
            if name in self.static:
                data, ctype = self.static[name]
                return 200, {"Content-Type": ctype, "Cache-Control": "no-cache" if name in ("sw.js", "manifest.webmanifest") else "max-age=3600"}, data
            if u.startswith("/api/file/") and u.count("/") == 4:   # /api/file/<id>/pages и /api/file/<id>/p<N> — страницы PDF картинками
                from core.ui.views import pdf_pages
                fid, part = u.split("/")[3], u.split("/")[4]
                with self.lock:
                    f = self._file_of(fid, self._device(token, create=False)[1])
                pages = pdf_pages(f[1]) if f and f[0].lower().endswith(".pdf") else []
                if part == "pages":
                    return self._json({"n": len(pages)})
                if part.startswith("p") and part[1:].isdigit() and int(part[1:]) < len(pages):
                    return 200, {"Content-Type": "image/jpeg", "Cache-Control": "max-age=3600"}, pages[int(part[1:])]
                return 404, {"Content-Type": "text/plain; charset=utf-8"}, "Файл устарел — откройте экран заново".encode()
            if u.startswith("/api/file/"):
                with self.lock:
                    f = self._file_of(u.rsplit("/", 1)[-1], self._device(token, create=False)[1])
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
            self.app.panel = self.panel
            login = act == "start" and bool(body.get("arg") or body.get("admin"))    # вход по ссылке: приглашение или ключ админа
            if login and self.auth_guard.blocked(ip or "-"):
                return 429, {"Content-Type": "text/plain; charset=utf-8", "Retry-After": "600"}, TOO_MANY.encode()
            tok, uid, new = self._device(token, create=True)
            try:
                if act == "start":
                    arg = str(body.get("arg") or "")[:40]
                    if body.get("admin"):
                        if self.admin_key and secrets.compare_digest(str(body["admin"]).encode(), self.admin_key.encode()):
                            self._make_admin(uid)
                            log.warning("веб: телефон %s стал администратором (ip %s)", uid, ip or "-")
                        else:
                            self._fail("admin", ip)
                    out, sid = self.app.start(uid, "", arg), "home"
                    if arg and not self._accepted(uid):
                        self._fail("invite", ip)
                elif act == "press":
                    to = str(body.get("to") or "home")[:64]
                    form = body.get("form") if isinstance(body.get("form"), dict) else None
                    out = self.app.press(uid, to, top=bool(body.get("top")), form=form)
                    if to == "swb:s:q":                # тихое сохранение конструктора: экран не нужен, ленту 🔔 не трогаем
                        return self._json({"ok": True})
                    sid = getattr(self.app, "last_sid", to.split(":")[0])
                elif act == "push":                        # подписка телефона на уведомления (или отписка)
                    if body.get("off"):
                        push.unsubscribe(self._office(), uid, body.get("endpoint"))
                        return self._json({"ok": True, "on": False})
                    ok = self._accepted(uid) and push.subscribe(self._office(), uid, body.get("sub") or {})
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

    def handle_raw(self, method, path, headers, body, ip=""):
        """Запросы без cookie и JSON: /dav/… (сетевая папка) и /api/1c (файл от 1С). → (код, заголовки, байты)."""
        from core import onec_link as L
        if self.app.demo:
            return 404, {"Content-Type": "text/plain; charset=utf-8"}, "В демо отчёты не принимаются".encode()
        u = path.split("?")[0]
        if (u == "/dav" or u.startswith("/dav/") or u == "/api/1c") and self.auth_guard.blocked(ip or "-"):
            return 429, {"Content-Type": "text/plain; charset=utf-8", "Retry-After": "600"}, TOO_MANY.encode()
        if u == "/dav" or u.startswith("/dav/"):
            with self.lock:
                res = L.dav(self.app, method, path, headers, body)
            if res[0] == 401 and headers.get("Authorization"):
                self._fail("dav", ip)
            return res
        if u == "/api/1c":
            if method != "POST":
                return 405, {"Content-Type": "text/plain; charset=utf-8"}, "POST с файлом в теле".encode()
            q = parse_qs(urlparse(path).query)
            with self.lock:
                if not L.check_key(self.app.db(), headers.get("X-Key") or (q.get("key") or [""])[0]):
                    self._fail("1c", ip)
                    return 403, {"Content-Type": "text/plain; charset=utf-8"}, "неверный ключ".encode()
                code, text = L.receive_direct(self.app, (q.get("name") or [""])[0], body, lambda out: self._deliver(0, out))
            return code, {"Content-Type": "text/plain; charset=utf-8"}, text.encode()
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, b""

    @staticmethod
    def _json(data):
        return 200, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, \
            json.dumps(data, ensure_ascii=False).encode()


def serve(api, port=8101, host="127.0.0.1", panel=None):
    """panel — WebApi приложения управления: отвечает тем, кто пришёл на адрес admin.<домен> (заголовок Host от Caddy)."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "kpi"

        def _api(self):
            h = (self.headers.get("Host") or "").split(":")[0].lower()
            return panel if panel is not None and h.startswith("admin.") else api

        def _ip(self):
            """Адрес телефона: приложение слушает только 127.0.0.1, снаружи в него ходит Caddy и передаёт адрес в X-Forwarded-For."""
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            return (fwd or self.client_address[0])[:64]

        def _raw(self, method):
            n = min(int(self.headers.get("Content-Length") or 0), 16 * 1024 * 1024)
            body = self.rfile.read(n) if n else b""
            code, headers, raw = self._api().handle_raw(method, self.path, self.headers, body, ip=self._ip())
            self.send_response(code)
            for k, v in SEC_HEADERS.items():
                headers.setdefault(k, v)
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
            code, headers, raw = self._api().handle(method, self.path, parse_qs(urlparse(self.path).query), body, token, ip=self._ip())
            self.send_response(code)
            for k, v in SEC_HEADERS.items():
                headers.setdefault(k, v)
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
