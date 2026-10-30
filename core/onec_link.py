"""🔌 Связь с 1С — отчёты попадают в приложение клиента сами (Адам 31.10: «выгрузка прямо в приложение, как на Google Диск»).

Три пути, все ведут в папку клиента `data/<код>/inbox/` → фоновая работа забирает (core/jobs/worker.py) → «🧪 Что пришло»:
  📁 Сетевая папка (WebDAV) — адрес `https://<код>.<домен>/dav/`, логин и пароль. На компьютере с 1С её подключают один раз как
     сетевой диск Windows, а выгрузку 1С по расписанию направляют в неё — как сейчас на Google Диск. Ничего ставить не нужно.
  🔌 Прямая отправка — 1С сама шлёт файл: POST `https://<код>.<домен>/api/1c?name=<имя файла>`, заголовок `X-Key: <ключ>`,
     тело — сам файл. Ответ — текст «что принято» (файл принимается сразу, не ждёт 10 минут).
  📥 Кнопка «Загрузить» — разовые файлы (core/reports.py).
Секреты (пароль папки, ключ 1С) хранятся в базе клиента (`onec_link`), показываются только админу и директору в приложении;
сменить — одной кнопкой (старые сразу перестают работать). Пароль храним как есть: его нужно показывать, чтобы вписать в 1С.
"""
import base64
import hmac
import secrets
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

from core.ui.kit import Button, Reply
from core.ui.registry import screen
from core.ui.views import View

KEY = "onec_link"
ROLES = ("admin", "owner")
EXT = (".xls", ".xlsx", ".csv", ".txt", ".xml", ".mxl", ".html", ".htm")
MAX_BYTES = 15 * 1024 * 1024


# ---------- папка и секреты ----------

def inbox_dir(app):
    """Папка клиента для отчётов: source.path / INBOX_DIR / data/<код>/inbox."""
    import os
    from core.data.db import client_db_path
    src = app.cfg.source or {}
    base = app.data_dir / app.client_id if app.data_dir else Path(client_db_path(app.client_id)).parent
    return Path(src.get("path") or os.environ.get("INBOX_DIR") or (base / "inbox"))


def secrets_of(db, renew=None):
    """{login, password, key} — создаются при первом обращении; renew="password" / "key" — сменить."""
    s = dict(db.get(KEY) or {})
    if not s.get("password") or renew == "password":
        s["password"] = secrets.token_urlsafe(9)
    if not s.get("key") or renew == "key":
        s["key"] = secrets.token_urlsafe(18)
    s.setdefault("login", "1c")
    db.put(KEY, s)
    return s


def check_key(db, key):
    s = db.get(KEY) or {}
    return bool(s.get("key") and key and hmac.compare_digest(str(key).encode(), s["key"].encode()))


def check_basic(db, header):
    """Authorization: Basic … для сетевой папки."""
    s = db.get(KEY) or {}
    if not s.get("password") or not header or not header.lower().startswith("basic "):
        return False
    try:
        login, _, pwd = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    login = login.split("\\")[-1]                       # Windows иногда шлёт «домен\логин»
    return hmac.compare_digest(login.encode(), s.get("login", "1c").encode()) and \
        hmac.compare_digest(pwd.encode(), s["password"].encode())


def safe_name(name):
    name = Path(unquote(str(name or "")).replace("\\", "/")).name.strip()
    return name[:120] if name and not name.startswith(".") else ""


# ---------- прямая отправка из 1С ----------

def receive_direct(app, name, data, deliver=None):
    """Файл от 1С: принять сразу, записать в журнал, сообщить админам. → (код, текст ответа для 1С)."""
    from core import reports
    from core.ingest.pipeline import ingest
    name = safe_name(name) or f"отчёт 1С {datetime.now():%Y-%m-%d %H%M%S}.xlsx"
    if not data:
        return 400, "пустой файл"
    if len(data) > MAX_BYTES:
        return 413, "файл больше 15 МБ — выгружайте отчёт за месяц"
    db = app.db()
    res = ingest(app.client_id, db, [(name, data)], False, app.hooks)[0]
    reports.log_result(db, name, res, "1С прислала сама")
    if deliver and (res.ok or res.errors):
        text = "📬 Новые отчёты 1С (прислала 1С)\n" + res.text()
        kb = [[Button("🧪 Что пришло", "upl")]] + ([[Button("🧾 Нет в расчёте", "pak")]] if db.unknown_keys() else [])
        deliver([Reply(text=text[:3500], buttons=kb, to=a, edit=False) for a in app.admin_ids(db)])
    return (200 if not res.errors else 422), res.text()


# ---------- сетевая папка (WebDAV: ровно столько, сколько нужно Windows и 1С) ----------

DAV_HEADERS = {"DAV": "1, 2", "MS-Author-Via": "DAV", "Allow": "OPTIONS, PROPFIND, PROPPATCH, GET, HEAD, PUT, DELETE, MOVE, LOCK, UNLOCK"}


def _prop(href, name, size, mtime, folder):
    t = formatdate(mtime, usegmt=True)
    created = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%SZ")
    rt = "<D:resourcetype><D:collection/></D:resourcetype>" if folder else "<D:resourcetype/>"
    extra = "" if folder else f"<D:getcontentlength>{size}</D:getcontentlength><D:getcontenttype>application/octet-stream</D:getcontenttype>"
    return (f"<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop><D:displayname>{escape(name)}</D:displayname>{rt}"
            f"<D:getlastmodified>{t}</D:getlastmodified><D:creationdate>{created}</D:creationdate>{extra}"
            f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")


def dav(app, method, path, headers, body):
    """Обработать запрос к /dav/… → (код, заголовки, байты). Видно только то, что лежит в папке и ещё не забрано."""
    db = app.db()
    if method == "OPTIONS":
        return 200, dict(DAV_HEADERS), b""
    if not check_basic(db, headers.get("Authorization")):
        return 401, {"WWW-Authenticate": 'Basic realm="1C", charset="UTF-8"', **DAV_HEADERS}, "Нужен логин и пароль".encode()
    folder = inbox_dir(app)
    folder.mkdir(parents=True, exist_ok=True)
    rel = unquote(path.split("?")[0])[len("/dav"):].strip("/")
    name = safe_name(rel) if rel else ""
    f = folder / name if name else None
    if rel and "/" in rel:
        return 403, {}, "Вложенные папки не нужны — кладите файлы прямо в папку".encode()
    if method == "PROPFIND":
        depth = headers.get("Depth", "1")
        items = []
        if not name:
            st = folder.stat()
            items.append(_prop("/dav/", "1С → приложение", 0, st.st_mtime, True))
            if depth != "0":
                for x in sorted(folder.iterdir()):
                    if x.is_file():
                        s = x.stat()
                        items.append(_prop("/dav/" + quote(x.name), x.name, s.st_size, s.st_mtime, False))
        else:
            if not f.exists():
                return 404, {}, b""
            s = f.stat()
            items.append(_prop("/dav/" + quote(f.name), f.name, s.st_size, s.st_mtime, False))
        xml = '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + "".join(items) + "</D:multistatus>"
        return 207, {"Content-Type": 'application/xml; charset="utf-8"', **DAV_HEADERS}, xml.encode()
    if method == "PROPPATCH":
        href = "/dav/" + quote(name) if name else "/dav/"
        xml = ('<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:"><D:response>'
               f'<D:href>{escape(href)}</D:href><D:propstat><D:prop/><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
               '</D:response></D:multistatus>')
        return 207, {"Content-Type": 'application/xml; charset="utf-8"'}, xml.encode()
    if method in ("LOCK", "UNLOCK"):
        if method == "UNLOCK":
            return 204, {}, b""
        token = f"opaquelocktoken:{secrets.token_hex(16)}"
        xml = ('<?xml version="1.0" encoding="utf-8"?><D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
               '<D:locktype><D:write/></D:locktype><D:lockscope><D:exclusive/></D:lockscope><D:depth>0</D:depth>'
               f'<D:timeout>Second-3600</D:timeout><D:locktoken><D:href>{token}</D:href></D:locktoken>'
               '</D:activelock></D:lockdiscovery></D:prop>')
        return 200, {"Content-Type": 'application/xml; charset="utf-8"', "Lock-Token": f"<{token}>"}, xml.encode()
    if not name:
        return 405, dict(DAV_HEADERS), b""
    if method in ("GET", "HEAD"):
        if not f.exists():
            return 404, {}, b""
        data = f.read_bytes()
        return 200, {"Content-Type": "application/octet-stream", "Content-Length": str(len(data))}, b"" if method == "HEAD" else data
    if method == "PUT":
        if len(body or b"") > MAX_BYTES:
            return 413, {}, "Файл больше 15 МБ".encode()
        if body and not name.lower().endswith(EXT) and not name.lower().endswith(".tmp"):
            return 415, {}, "Принимаются отчёты: .xls, .xlsx, .csv, .mxl".encode()
        existed = f.exists()
        f.write_bytes(body or b"")
        return (204 if existed else 201), {}, b""
    if method == "DELETE":
        if f.exists():
            f.unlink()
        return 204, {}, b""
    if method == "MOVE":                                # Windows сохраняет во временный файл, потом переименовывает
        dest = headers.get("Destination", "")
        dname = safe_name(dest.split("/dav", 1)[-1]) if "/dav" in dest else ""
        if not f.exists() or not dname:
            return 404 if not f.exists() else 400, {}, b""
        target = folder / dname
        existed = target.exists()
        if existed and headers.get("Overwrite", "T").upper() == "F":
            return 412, {}, b""
        f.replace(target)
        return (204 if existed else 201), {}, b""
    if method == "MKCOL":
        return 403, {}, "Вложенные папки не нужны".encode()
    return 405, dict(DAV_HEADERS), b""


# ---------- экран для админа ----------

def _host(ctx):
    from core.onboard import domain
    base = (ctx.app.web_url or "").rstrip("/")
    return base or f"https://{ctx.cfg.id.replace('_', '-')}.{domain(ctx)}"


def guide_lines(ctx, s):
    base = _host(ctx)
    win = base.replace("https://", "\\\\").replace("/", "\\") + "@SSL\\dav"
    folder = [f"Адрес папки: {base}/dav/", f"Для «Подключить сетевой диск» в Windows: {win}",
              f"Логин: {s['login']}", f"Пароль: {s['password']}"]
    steps = ["1. На компьютере, где работает 1С: «Этот компьютер» → «Подключить сетевой диск».",
             f"2. Папка: {win} — галочки «Восстанавливать при входе» и «Использовать другие учётные данные».",
             f"3. Логин {s['login']}, пароль — выше, «Запомнить». Появится диск — это папка приложения.",
             "4. В 1С в настройке выгрузки отчётов по расписанию укажите этот диск как папку сохранения (как сейчас Google Диск).",
             "5. Проверка: положите туда любой отчёт — через 1–10 минут он появится в «🧪 Что пришло»."]
    direct = [f"Адрес: {base}/api/1c?name=<имя файла.xlsx>", f"Заголовок: X-Key: {s['key']}",
              "Метод POST, тело — сам файл. Ответ — текст, что принято (код 200) или что не так (422)."]
    code = ["Соединение = Новый HTTPСоединение(\"" + base.split("//")[-1] + "\", , , , , 60, Новый ЗащищенноеСоединениеOpenSSL);",
            "Запрос = Новый HTTPЗапрос(\"/api/1c?name=\" + КодироватьСтроку(ИмяФайла, СпособКодированияСтроки.КодировкаURL));",
            f"Запрос.Заголовки.Вставить(\"X-Key\", \"{s['key']}\");",
            "Запрос.УстановитьТелоИзДвоичныхДанных(Новый ДвоичныеДанные(ПолноеИмяФайла));",
            "Ответ = Соединение.ОтправитьДляОбработки(Запрос);  // Ответ.ПолучитьТелоКакСтроку() — что принято"]
    return folder, steps, direct, code


@screen("ucs")
def link_screen(ctx, act=None, sure=None):
    if ctx.demo:
        v = View("🔌 Связь с 1С", "как отчёты попадают в приложение")
        v.lines(["📁 Сетевая папка — подключается в Windows как обычный диск; 1С по расписанию сохраняет туда отчёты, как на Google Диск.",
                 "🔌 Прямая отправка — 1С сама отправляет файлы в приложение (для тех, у кого есть программист 1С).",
                 "📥 Кнопка «Загрузить» — разовые файлы с телефона или компьютера.",
                 "Файл пришёл — через минуту-другую он в «🧪 Что пришло», а администратору — уведомление."])
        v.note("В демо цифры выдуманы — у настоящего клиента здесь адрес папки, логин, пароль и инструкция для 1С.")
        return Reply(view=v)
    if not (ctx.admin or ctx.role in ROLES):
        return Reply(text="Связь с 1С настраивают администратор и руководитель.")
    if act in ("password", "key"):
        what = act
        if sure != "yes":
            t = "пароль папки" if what == "password" else "ключ прямой отправки"
            return Reply(text=f"Сменить {t}? Старый сразу перестанет работать — его нужно будет заменить в Windows / 1С.",
                         buttons=[[Button("🔄 Да, сменить", f"ucs:{what}:yes")], [Button("‹ Связь с 1С", "ucs")]])
        secrets_of(ctx.db, renew=what)
        ctx.db.audit(ctx.name(), "onec_link", f"сменён {'пароль папки' if what == 'password' else 'ключ 1С'}")
        return [Reply(text="🔄 Готово, новый — на экране. Передайте его тому, кто настраивает 1С."), link_screen(ctx)]
    s = secrets_of(ctx.db)
    folder, steps, direct, code = guide_lines(ctx, s)
    v = View("🔌 Связь с 1С", "отчёты приходят в приложение сами")
    v.lines(folder, title="📁 Сетевая папка — как Google Диск, без программиста")
    v.lines(steps, title="Как подключить")
    v.lines(direct, title="🔌 Прямая отправка — для программиста 1С")
    v.note("Логин, пароль и ключ — только для вашей фирмы. Кто их знает, может присылать отчёты; утекли — смените кнопкой ниже.")
    from core import reports
    lg = reports.log_of(ctx.db)
    if lg:
        e = lg[0]
        v.note(f"Последний отчёт: {e['ts']} · {e['name']} ({e['who']}).")
    return Reply(view=v, buttons=[[Button("📄 Инструкция для 1С (PDF)", "ucp")], [Button("🔄 Новый пароль папки", "ucs:password"),
                                                                                      Button("🔄 Новый ключ 1С", "ucs:key")],
                                  [Button("🧪 Что пришло", "upl"), Button("📥 Загрузить", "up")]])


@screen("ucp")
def link_pdf(ctx):
    if ctx.demo or not (ctx.admin or ctx.role in ROLES):
        return link_screen(ctx)
    from core.ui.views import to_pdf
    s = secrets_of(ctx.db)
    folder, steps, direct, code = guide_lines(ctx, s)
    v = View(f"Отчёты 1С → приложение «{ctx.cfg.name}»", "инструкция для того, кто настраивает 1С")
    v.lines(folder, title="Вариант 1. Сетевая папка (без программиста)")
    v.lines(steps, title="Шаги")
    v.lines(direct, title="Вариант 2. Прямая отправка из 1С (программисту)")
    v.lines(code, title="Пример кода 1С")
    v.lines(["Продажи, Перечисление, СКЮ, АКБ, ОК, Аналитика, Занесённые деньги — за текущий месяц, 4 раза в день;",
             "1–3 числа — ещё и за прошлый месяц; Дебиторка и Продажи за 30 дней — на 7, 14, 21 и конец месяца.",
             "Имена файлов любые — бот сам узнаёт отчёт по содержимому. Тот же файл второй раз не примется."],
            title="Какие отчёты выгружать")
    v.note("Пароль и ключ — секрет фирмы. Не публикуйте их; если утекли — администратор сменит их в приложении.")
    return Reply(text="📄 Инструкция — перешлите тому, кто настраивает 1С.",
                 files=[(f"Связь с 1С — {ctx.cfg.name[:30]}.pdf", to_pdf([v]))], buttons=[[Button("‹ Связь с 1С", "ucs")]])
