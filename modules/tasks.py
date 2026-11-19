"""📌 Задания (Адам 19.11: «точно как в первом боте, с тем же функционалом», 2 000 ₽ в месяц) — для всех отделов.

Кто даёт: директор и администратор — любому; начальник отдела — своему отделу; супервайзер — торговым своей команды.
Задание — одному или нескольким, разовое со сроком или повтор (ежедневно по рабочим дням, по дням недели, по числам месяца,
раз в квартал, ежегодно, каждые N дней), приоритет (высокий / средний / низкий / заморожен), фото или скрин.
Похожее уже давали — бот предупреждает. Повтор — одна карточка на человека: выполнил — следующая встаёт на следующий
нужный день (выдаётся в 09:00 того дня), не выполнил — карточка остаётся красной.

Исполнитель: «✅ Выполнено» (комментарий и скрин — по желанию), «❌ Не выполнено» (пояснение обязательно), «⏳ Перенести срок»
(дата и причина → решает автор). Автор: 👍 принять / ↩️ вернуть на доработку; на «не выполнено» — оставить в работе,
изменить и отправить снова, снять. Решает только автор и администратор; начальник продаж видит задания супервайзеров.

Доска «неделя» (как в Weeek): 7 дней со вчерашнего, листается по дню; фильтры «мои заметки / я поручил / мне / человек»;
на компьютере карточку перетаскивают на другой день, на телефоне — «📅 Перенести» в карточке; заметки себе (без сообщений
и напоминаний); просроченное — в колонке «сегодня» красным; в дне сверху «Повторяемые», ниже «Прочие». Поиск по словам.

Напоминания: 10:00 и 16:00 рабочего дня — с дня срока (торговым в 16:00 — только высокий приоритет); высокий приоритет —
каждый день и ещё в 12:00 и 14:00. Утром в 08:00 автору — сводка: просрочено, ждёт решения. Число на кнопке «Задания» —
мои дела со сроком сегодня и раньше + мои решения.

Для зарплаты: единицы «задачи в срок» и «не в срок» (кубик «Сдельно») — невыполненные, «не ответил» и без ответа после срока.
Таблицы task_defs (задание), task_items (копия у каждого исполнителя: история, ответ, перенос), task_files (фото).
Люди — номер сотрудника; администратор платформы без карточки — отрицательный номер пользователя.
"""
import base64
import hashlib
import json
import re
from datetime import date, datetime, timedelta

from core.calendar import MONTHS_GEN, WEEKDAYS, month_of
from core.jobs.schedule import job, tick
from core.ui.kit import Button, Reply
from core.ui.registry import menu, screen
from core.ui.views import View

M = "tasks"
BTN = "📌 Задания"
ALL_ROLES = {"rep", "newbie", "sv", "staff", "head", "owner", "admin", "accountant"}
AUTHOR_ROLES = {"owner", "admin", "head", "sv"}
SELLERS = {"rep", "newbie"}
PRIO = [("", "без приоритета"), ("hi", "высокий"), ("mid", "средний"), ("lo", "низкий"), ("fz", "заморожен")]
PRIO_T = dict(PRIO)
FREQ = [("daily", "Ежедневно (рабочие дни)"), ("weekly", "По дням недели"), ("monthly", "По числам месяца"),
        ("quarterly", "Раз в квартал"), ("yearly", "Ежегодно"), ("every", "Каждые N дней")]
FILT = [("all", "Всё"), ("note", "📝 Мои заметки"), ("given", "📤 Я поручил"), ("me", "📥 Мне")]
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MON3 = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
STATUS = {"work": "в работе", "done": "✅ выполнено — ждёт 👍", "fail": "❌ не выполнено — ждёт решения",
          "ok": "✅ выполнено", "off": "✖️ снято", "miss": "⚫ не ответил"}
REP_DAYS = [(1, "в тот же день"), (2, "2 дня"), (3, "3 дня"), (6, "неделя")]
ISSUE_HOUR, LATE_HOUR = 9, 17
MOVE_WAIT = 120                 # «срок перенесён» — через 2 минуты; вернул обратно — ничего
IDLE_RESET = 30 * 60            # полчаса не заходил на доску — снова со вчерашнего дня
PHOTO_MAX = 700_000             # байт после сжатия в приложении

menu("tasks", BTN, ALL_ROLES, module=M, order=50)


# ======================= время и календарь =======================

def now_s(ctx):
    return f"{ctx.today.isoformat()} {datetime.now():%H:%M:%S}"


def is_off(cfg, d):
    return WEEKDAYS[d.weekday()] in set(cfg.weekend) or d.isoformat() in (getattr(cfg, "holidays", None) or {})


def work_day(cfg, d, step=1):
    for _ in range(14):
        if not is_off(cfg, d):
            return d
        d += timedelta(days=step)
    return d


def quick_deadline(ctx, k):
    d = ctx.today + timedelta(days=k)
    return d if k == 0 else work_day(ctx.cfg, d)


def dshow(ctx, s):
    if not s:
        return "—"
    s = str(s)
    try:
        y = int(s[:4])
        return f"{int(s[8:10])}.{s[5:7]}" + ("" if y == ctx.today.year else f".{y}")
    except (ValueError, TypeError):
        return s


def day_label(ctx, s):
    try:
        d = date.fromisoformat(str(s)[:10])
    except ValueError:
        return str(s)
    if d == ctx.today:
        return "сегодня"
    if d == ctx.today + timedelta(days=1):
        return "завтра"
    if d == ctx.today - timedelta(days=1):
        return "вчера"
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def short(fio):
    w = str(fio or "").split()
    return " ".join(w[:2]) if w else "—"


# ======================= люди =======================

def me_key(ctx):
    if ctx.me:
        return ctx.me["id"]
    return -abs(int(ctx.user_id or 1))


def is_admin(ctx):
    """Права администратора — по роли, которой человек сейчас смотрит («Смотреть как» — права того, кого смотрим)."""
    return ctx.role == "admin"


def is_author(ctx):
    return ctx.role in AUTHOR_ROLES


def people(ctx):
    """Кому этот человек может давать задания: [(номер, имя, отдел)]."""
    out = []
    mine = me_key(ctx)
    for e in ctx.db.employees():
        if e["id"] == mine:
            continue
        if ctx.role in ("owner", "admin"):
            ok = True
        elif ctx.role == "head" and ctx.me:
            ok = e["dept"] == ctx.me["dept"] and e["role"] not in ("owner", "admin")
        elif ctx.role == "sv" and ctx.me:
            ok = e["role"] in SELLERS and e["team"] == ctx.me["team"] and e["dept"] == ctx.me["dept"]
        else:
            ok = False
        if ok:
            out.append((e["id"], short(e["fio"]), e["dept"]))
    return out


def sv_ids(ctx):
    if not ctx.me:
        return []
    return [e["id"] for e in ctx.db.employees(dept=ctx.me["dept"]) if e["role"] == "sv"]


def seen(ctx):
    """Чьи выданные задания видит автор: (условие по task_defs t, параметры)."""
    if is_admin(ctx):
        return "t.self = 0", []
    ids = [me_key(ctx)]
    if ctx.role == "head" and ctx.dept_kind == "sales":
        ids += sv_ids(ctx)                       # начальник продаж видит задания супервайзеров (решает их автор)
    return f"t.self = 0 AND t.author IN ({','.join('?' * len(ids))})", ids


def may_manage(ctx, t):
    """Решать по заданию — только тот, кто его дал, и администратор. Заметку себе — только её автор."""
    if not t:
        return False
    if t["self"]:
        return t["author"] == me_key(ctx)
    return t["author"] == me_key(ctx) or bool(is_admin(ctx))


def uid_of(ctx, key):
    """Кому слать в ленту: номер сотрудника → его вход; отрицательный — пользователь сам."""
    key = int(key or 0)
    if key < 0:
        return -key
    e = ctx.db.employee(key) if key else None
    return e["tg_id"] if e and e.get("tg_id") else 0


def group_title(ctx, dept):
    d = ctx.cfg.dept(dept) if dept else None
    return d.name if d else "Прочие"


# ======================= база =======================

def _task(r):
    if not r:
        return None
    r = dict(r)
    r["rule"] = json.loads(r.get("rule") or "{}")
    r["people"] = json.loads(r.get("people") or "[]")
    return r


def task(db, tid):
    try:
        return _task(db.one("SELECT * FROM task_defs WHERE id = ?", int(tid)))
    except (TypeError, ValueError):
        return None


def _item(x):
    x = dict(x)
    x["log"] = json.loads(x.get("log") or "[]")
    x["move"] = json.loads(x.get("move") or "null")
    return x


def item(db, iid):
    try:
        r = db.one("SELECT * FROM task_items WHERE id = ?", int(iid))
    except (TypeError, ValueError):
        return None
    return _item(r) if r else None


def items(db, where="1=1", *args):
    return [_item(x) for x in db.q(f"SELECT * FROM task_items WHERE {where} ORDER BY id DESC", *args)]


def save_item(db, x, **kw):
    x.update(kw)
    if x["status"] != "work" and (x.get("move") or {}).get("state") == "ask":
        x["move"] = dict(x["move"], state="closed")          # закрыто — просьба о переносе больше не ждёт решения
    db.x("UPDATE task_items SET status=?, answer=?, photo=?, ans_ts=?, done_ts=?, move=?, log=?, deadline=?, bday=? WHERE id=?",
         x["status"], x.get("answer") or "", x.get("photo") or "", x.get("ans_ts"), x.get("done_ts"),
         json.dumps(x.get("move"), ensure_ascii=False), json.dumps(x["log"], ensure_ascii=False), x["deadline"], x.get("bday"), x["id"])


def add_log(ctx, x, who, what):
    x["log"].append([now_s(ctx)[:16], who, what])


def overdue(ctx, x):
    return x["status"] == "work" and bool(x["deadline"]) and x["deadline"] < ctx.today.isoformat()


def prio_of(t):
    return t.get("prio") or ""


def save_photo(ctx, data_url):
    """Фото из формы приложения (сжато там до 1600 точек) → номер в task_files; не картинка или слишком большое — ''."""
    s = str(data_url or "")
    m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", s)
    if not m:
        return ""
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:              # noqa: BLE001
        return ""
    if not raw or len(raw) > PHOTO_MAX:
        return ""
    return str(ctx.db.x("INSERT INTO task_files (data, mime, owner, ts) VALUES (?,?,?,?)", raw, m.group(1), me_key(ctx), now_s(ctx)))


def photo_file(ctx, fid):
    if not fid:
        return None
    r = ctx.db.one("SELECT data, mime FROM task_files WHERE id = ?", int(fid)) if str(fid).isdigit() else None
    if not r:
        return None
    ext = {"image/png": "png", "image/webp": "webp"}.get(r["mime"], "jpg")
    return (f"скрин.{ext}", bytes(r["data"]))


# ======================= повторы =======================

def rule_text(rule):
    if not rule:
        return "разовое"
    k = rule.get("type")
    if k == "daily":
        return "↻ ежедневно (рабочие дни)"
    if k == "weekly":
        return "↻ по дням: " + ", ".join(WD[d] for d in sorted(rule.get("days") or []))
    if k == "monthly":
        return "↻ по числам: " + ", ".join(str(d) for d in sorted(rule.get("days") or []))
    if k == "yearly":
        md = rule.get("md") or "01-01"
        return f"↻ ежегодно {md[3:5]}.{md[0:2]}"
    if k == "every":
        return f"↻ каждые {rule.get('n')} дн."
    if k == "quarterly":
        try:
            fm = int(str(rule.get("from") or "2026-01")[5:7])
        except ValueError:
            fm = 1
        return f"↻ раз в квартал, {rule.get('day') or 1}-го ({', '.join(MON3[(fm - 1 + 3 * i) % 12] for i in range(4))})"
    return "↻ повтор"


def quarter_hit(rule, x):
    import calendar
    try:
        fy, fm = int(str(rule.get("from"))[:4]), int(str(rule.get("from"))[5:7])
        day = max(1, min(31, int(rule.get("day") or 1)))
    except (ValueError, TypeError):
        return False
    k = (x.year * 12 + x.month) - (fy * 12 + fm)
    if k < 0 or k % 3:
        return False
    return x.day == min(day, calendar.monthrange(x.year, x.month)[1])


def due_today(cfg, rule, d):
    """Выдавать ли повтор в день d. Нужный день выпал на выходной — в ближайший рабочий после."""
    import calendar
    k = rule.get("type")
    if is_off(cfg, d):
        return False
    if k == "daily":
        return True
    if k == "weekly":
        return d.weekday() in (rule.get("days") or [])

    def back(hit, limit=40, stop=None):
        x = d
        for _ in range(limit):
            if hit(x):
                return True
            x -= timedelta(days=1)
            if (stop and x < stop) or not is_off(cfg, x):
                return False
        return False
    if k == "monthly":
        days = rule.get("days") or []

        def hit(x):
            last = calendar.monthrange(x.year, x.month)[1]
            return x.day in days or (x.day == last and any(n > last for n in days))
        return back(hit)
    if k == "yearly":
        md = rule.get("md") or ""
        return back(lambda x: x.strftime("%m-%d") == md or (md == "02-29" and not calendar.isleap(x.year)
                                                             and x.strftime("%m-%d") == "02-28"), 7)
    if k == "quarterly":
        return back(lambda x: quarter_hit(rule, x), 10)
    if k == "every":
        try:
            n, f = max(1, int(rule.get("n") or 1)), date.fromisoformat(rule.get("from") or "")
        except (ValueError, TypeError):
            return False
        if d < f:
            return False
        return back(lambda x: (x - f).days % n == 0, 400, stop=f)
    return False


def rep_deadline(cfg, d, n):
    """Срок копии повтора: N рабочих дней, считая день выдачи."""
    left, x = max(1, int(n or 1)) - 1, d
    while left > 0:
        x += timedelta(days=1)
        if not is_off(cfg, x):
            left -= 1
    return x.isoformat()


def next_due(cfg, rule, after):
    d = after + timedelta(days=1)
    for _ in range(800):
        if due_today(cfg, rule, d):
            return d
        d += timedelta(days=1)
    return None


def issue_day(cfg, x, t):
    """День, на который выдана копия: от него считается следующий повтор."""
    try:
        return date.fromisoformat(str(x.get("iday") or "")[:10])
    except ValueError:
        pass
    try:
        return date.fromisoformat(str(x.get("ts") or "")[:10])
    except ValueError:
        return None


def make_item(ctx, t, who, name, deadline, day=None, note="выдано"):
    return ctx.db.x("INSERT INTO task_items (task_id, who, name, deadline, status, log, ts, iday) VALUES (?,?,?,?,?,?,?,?)",
                    t["id"], who, name, deadline, "work",
                    json.dumps([[now_s(ctx)[:16], t["author_name"], note]], ensure_ascii=False), now_s(ctx),
                    (day or ctx.today).isoformat())


def after_done(ctx, t, x):
    """Повтор: копия выполнена (или снята) — следующая этому же человеку на следующий нужный день. → [Reply] ему, если сегодня."""
    if not t or t["kind"] != "rep" or t["status"] != "active":
        return []
    if ctx.db.q("SELECT 1 FROM task_items WHERE task_id=? AND who=? AND (status='work' OR id>?)", t["id"], x["who"], x["id"]):
        return []
    base = max(ctx.today, issue_day(ctx.cfg, x, t) or ctx.today)
    nd = next_due(ctx.cfg, t["rule"], base)
    if not nd:
        return []
    dl = rep_deadline(ctx.cfg, nd, t["days"])
    iid = ctx.db.x("INSERT INTO task_items (task_id, who, name, deadline, status, log, ts, iday) VALUES (?,?,?,?,?,?,?,?)",
                   t["id"], x["who"], x["name"], dl, "work",
                   json.dumps([[now_s(ctx)[:16], "бот", f"повтор после выполнения — на {dshow(ctx, dl)}"]], ensure_ascii=False),
                   now_s(ctx), nd.isoformat())
    if t["self"] or nd > ctx.today:
        return []                       # на будущее — человеку в 09:00 того дня (issue)
    return deliver(ctx, t, [iid])


def drop_after_copy(ctx, t, x):
    """Выполнение не засчитали (↩️, «снова в работу») — убрать следующую копию, если её ещё не выдали и не трогали."""
    if not t or t["kind"] != "rep":
        return
    nxt = items(ctx.db, "task_id=? AND who=? AND id>?", t["id"], x["who"], x["id"])
    if len(nxt) != 1:
        return
    n = nxt[0]
    if n["status"] != "work" or n.get("answer") or n.get("photo") or n.get("bday") or n.get("move") or len(n["log"]) > 1:
        return
    if str(n.get("iday") or "") > ctx.today.isoformat() or t["self"]:
        ctx.db.x("DELETE FROM task_items WHERE id = ?", n["id"])


# ======================= похожие =======================

STOP = {"для", "что", "все", "всё", "как", "это", "или", "так", "при", "над", "под", "без", "только", "срочно"}


def words(s):
    return {w[:4] for w in re.findall(r"[а-яёa-z0-9]{3,}", str(s).lower().replace("ё", "е")) if w not in STOP}


def similar(ctx, text, limit=2):
    w = words(text)
    if not w:
        return []
    cond, args = seen(ctx)
    out = []
    for t in ctx.db.q(f"SELECT * FROM task_defs t WHERE {cond} ORDER BY id DESC LIMIT 500", *args):
        w2 = words(t["text"])
        if w2 and len(w & w2) / min(len(w), len(w2)) >= 0.5:
            out.append(_task(t))
    return out[:limit]


# ======================= сообщения =======================

def task_head(ctx, t, x=None):
    p = {"hi": "🔴 ", "mid": "🟡 ", "lo": "🔵 ", "fz": "⚪ "}.get(prio_of(t), "")
    return (f"📌 {p}Задание № {t['id']}" + (f" · до {dshow(ctx, x['deadline'])}" if x else "")
            + (f" · {rule_text(t['rule'])}" if t["kind"] == "rep" else ""))


def for_worker(ctx, t, x):
    lines = [task_head(ctx, t, x), t["text"] or "", f"от {t['author_name']}"]
    if overdue(ctx, x):
        lines.append("🔴 Срок прошёл — задание просрочено.")
    if (x.get("move") or {}).get("state") == "ask":
        lines.append(f"⏳ Вы просили перенести на {dshow(ctx, x['move']['to'])} — ждём ответа.")
    return "\n".join(lines)


def worker_kb(x):
    return [[Button("✅ Выполнено", f"tka:{x['id']}:ok"), Button("❌ Не выполнено", f"tka:{x['id']}:no")],
            [Button("⏳ Перенести срок", f"tka:{x['id']}:mv")]]


def deliver(ctx, t, iids):
    """Исполнителям — сообщение в ленту (и уведомление на телефон). Заметке себе — ничего."""
    if t["self"]:
        return []
    out = []
    for iid in iids:
        x = item(ctx.db, iid)
        to = uid_of(ctx, x["who"]) if x else 0
        if to:
            out.append(Reply(text=for_worker(ctx, t, x), buttons=worker_kb(x), to=to, files=_photo_list(ctx, t["photo"])))
    return out


def _photo_list(ctx, fid):
    f = photo_file(ctx, fid)
    return [f] if f else []


def to_author(ctx, t, text, buttons=None, photo=""):
    to = uid_of(ctx, t["author"])
    if not to or t["author"] == me_key(ctx):
        return []
    return [Reply(text=text, buttons=buttons or [], to=to, files=_photo_list(ctx, photo))]


def to_worker(ctx, x, text, buttons=None):
    to = uid_of(ctx, x["who"])
    if not to or x["who"] == me_key(ctx):
        return []
    return [Reply(text=text, buttons=buttons or [], to=to)]


# ======================= доска «неделя» =======================

def state(ctx, reset=False):
    import time
    st = ctx.session.setdefault("tkb", {"w": "", "f": "all", "p": 0})
    t = time.time()
    if reset or t - st.get("ts", 0) > IDLE_RESET:
        st["w"] = ""
    st["ts"] = t
    return st


def first_day(ctx):
    return ctx.today - timedelta(days=1)


def kind_of(r, me):
    if r["self"]:
        return "note"
    if r["author"] == me and r["who"] != me:
        return "given"
    return "me"


def cards(ctx, start, flt="all", who=0):
    me, d0 = me_key(ctx), ctx.today
    s, e = start.isoformat(), (start + timedelta(days=6)).isoformat()
    in_week = start <= d0 <= start + timedelta(days=6)
    rows = ctx.db.q(
        "SELECT i.*, t.text, t.photo AS tphoto, t.author, t.author_name, t.kind AS tkind, t.prio, t.self "
        "FROM task_items i JOIN task_defs t ON t.id = i.task_id "
        "WHERE (i.who=? OR t.author=?) AND i.status IN ('work','done','fail','ok') "
        "AND ((i.deadline BETWEEN ? AND ?) OR (i.status IN ('work','fail') AND i.deadline < ?) "
        "OR (i.status IN ('done','ok') AND substr(COALESCE(i.ans_ts, i.done_ts, ''),1,10) BETWEEN ? AND ?) "
        "OR (i.bday BETWEEN ? AND ?))", me, me, s, e, d0.isoformat(), s, e, s, e)
    out = []
    for r in rows:
        if str(r.get("iday") or "") > d0.isoformat() and r["who"] != me and not r["self"] and r["author"] != me:
            continue                      # копия на будущее, человеку ещё не выдана
        k = kind_of(r, me)
        if flt not in ("all", k) or (who and (k != "given" or r["who"] != who)):
            continue
        over = r["status"] in ("work", "fail") and (r["deadline"] or "") < d0.isoformat()
        if k == "note" and over and r.get("bday") and r["bday"] == r["deadline"]:
            over = False                  # заметку сам поставил в прошлый день — стоит там и не краснеет
        col = r["deadline"]
        if not (s <= (col or "") <= e) and r["status"] in ("done", "ok"):
            col = (r.get("ans_ts") or r.get("done_ts") or "")[:10]
        if r["status"] in ("done", "ok") and r.get("bday"):
            col = r["bday"]
            if not s <= col <= e:
                continue
        if k == "given" and r["status"] in ("work", "fail") and r.get("bday") and not over:
            col = r["bday"]
            if not s <= col <= e:
                continue
        if over:
            if not in_week:
                if not s <= (r["deadline"] or "") <= e:
                    continue
            else:
                col = d0.isoformat()
        done = r["status"] in ("ok", "done")
        ck, wait = "", False
        if k == "note":
            ck = f"tkx:{r['id']}:{0 if r['status'] == 'ok' else 1}"
        elif k == "me" and r["status"] == "work" and r["who"] == me:
            ck = f"tka:{r['id']}:ok"
        elif k == "given" and r["status"] == "done":
            wait, ck = True, f"tkac:{r['id']}"
        who_t = f"→ {r['name']}" if k == "given" else f"от {short(r['author_name'])}" if k == "me" else ""
        if r["status"] == "fail":
            who_t += " · ❌ не выполнено"
        if (json.loads(r["move"] or "null") or {}).get("state") == "ask":
            who_t += " · просит перенести"
        out.append({"id": r["id"], "no": r["task_id"], "t": (r["text"] or "")[:160], "d": col, "p": r["prio"] or "",
                    "rep": r["tkind"] == "rep", "k": k, "who": who_t.strip(" ·"), "done": done, "wait": wait, "over": over,
                    "od": dshow(ctx, r["deadline"]) if over else "", "ck": ck, "o": f"tko:{r['id']}",
                    "mv": k == "note" or (k == "given" and r["author"] == me), "clip": bool(r.get("tphoto") or r.get("photo"))})
    order = {"hi": 0, "mid": 1, "lo": 2, "": 3, "fz": 4}
    out.sort(key=lambda c: (c["d"] or "", c["done"], not c["over"], order.get(c["p"], 3), c["no"]))
    return out


def people_filter(ctx):
    seen_, out = set(), []
    for r in ctx.db.q("SELECT DISTINCT i.who, i.name FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE t.author=? "
                      "AND t.self = 0 ORDER BY i.name", me_key(ctx)):
        if r["who"] not in seen_ and r["who"] != me_key(ctx):
            seen_.add(r["who"])
            out.append([r["who"], short(r["name"])])
    return out


def week_title(start):
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day} – {end.day} {MONTHS_GEN[end.month - 1]}"
    return f"{start.day} {MONTHS_GEN[start.month - 1]} – {end.day} {MONTHS_GEN[end.month - 1]}"


def board_data(ctx, st):
    d0 = ctx.today
    try:
        start = date.fromisoformat(st.get("w") or "")
        if not 2024 <= start.year <= d0.year + 2:
            raise ValueError
    except ValueError:
        start = first_day(ctx)
    flt = st.get("f") if st.get("f") in dict(FILT) else "all"
    who = int(st.get("p") or 0)
    days = [{"d": (start + timedelta(days=i)).isoformat(), "l": f"{WD[(start + timedelta(days=i)).weekday()]}, {(start + timedelta(days=i)).day}",
             "today": start + timedelta(days=i) == d0, "off": is_off(ctx.cfg, start + timedelta(days=i)), "past": start + timedelta(days=i) < d0}
            for i in range(7)]
    cs = cards(ctx, start, flt, who)
    ppl = people_filter(ctx)
    return {"title": week_title(start), "prev": f"tasks:s:{(start - timedelta(days=1)).isoformat()}",
            "next": f"tasks:s:{(start + timedelta(days=1)).isoformat()}", "now": "tasks:d:0", "is_now": start == first_day(ctx),
            "filters": [[k, n, k == flt and not who, f"tasks:f:{k}"] for k, n in FILT],
            "people": [[t, n, t == who, f"tasks:p:{t}"] for t, n in ppl],
            "days": days, "cards": cs, "move": "tkm", "note": "tkq", "today": d0.isoformat(),
            "new": "tkn" if is_author(ctx) else "", "list": "tkl", "find": "tkf",
            "sel": st.get("day") or "", "flash": st.pop("flash", None)}


def badge(ctx):
    """Сколько ждёт человека: его задания и заметки в работе со сроком сегодня и раньше + ждут его решения."""
    try:
        me, d0 = me_key(ctx), ctx.today.isoformat()
        n = ctx.db.one("SELECT COUNT(*) AS n FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE i.who=? AND i.status='work' "
                       "AND i.deadline<=? AND COALESCE(i.iday,'')<=? AND NOT (t.self=1 AND i.bday IS NOT NULL AND i.bday=i.deadline "
                       "AND i.deadline<?)", me, d0, d0, d0)["n"]
        n += ctx.db.one("SELECT COUNT(*) AS n FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE t.author=? AND i.who<>? "
                        "AND (i.status='fail' OR i.move LIKE '%\"state\": \"ask\"%')", me, me)["n"]
        return int(n)
    except Exception:              # noqa: BLE001 — таблиц ещё нет (старая база)
        return 0


@screen("tasks", module=M)
def board(ctx, op=None, arg=None, head=""):
    """Доска: tasks · tasks:s:<день> (окно с дня) · tasks:d:<день|0> · tasks:f:<фильтр> · tasks:p:<человек> · tasks:fi:<копия>."""
    st = state(ctx, reset=op is None and not head)
    if op == "s" and arg:
        try:
            w = date.fromisoformat(arg)
            st["w"] = (w if 2024 <= w.year <= ctx.today.year + 2 else first_day(ctx)).isoformat()
        except ValueError:
            st["w"] = ""
    elif op == "d":
        try:
            w = date.fromisoformat(arg) if arg not in (None, "0") else None
        except ValueError:
            w = None
        try:
            cur = date.fromisoformat(st.get("w") or "")
        except ValueError:
            cur = first_day(ctx)
        if w is None:
            st["w"] = ""
        elif not cur <= w <= cur + timedelta(days=6):
            st["w"] = (w - timedelta(days=1)).isoformat()
        st["day"] = arg if w else ""
    elif op == "f":
        st["f"], st["p"] = (arg if arg in dict(FILT) else "all"), 0
    elif op == "p":
        st["p"] = int(arg) if arg and arg.lstrip("-").isdigit() else 0
        st["f"] = "given" if st["p"] else "all"
    elif op == "fi" and arg:
        day = found_day(ctx, arg)
        if day:
            try:
                cur = date.fromisoformat(st.get("w") or "")
            except ValueError:
                cur = first_day(ctx)
            w = date.fromisoformat(day)
            if not cur <= w <= cur + timedelta(days=6):
                st["w"] = (w - timedelta(days=1)).isoformat()
            st["flash"] = [str(arg), day]
            st["f"], st["p"] = "all", 0
    ctx.save()
    data = board_data(ctx, st)
    v = View(BTN, "")
    if head:
        v.note(head)
    v.ui("board", data, fallback=[f"{c['d'][8:10]}.{c['d'][5:7]} · {'✅ ' if c['done'] else '🔴 ' if c['over'] else ''}{c['t']}"
                                  + (f" · {c['who']}" if c["who"] else "") for c in data["cards"]] or ["Заданий на эту неделю нет."])
    return Reply(view=v, buttons=[[Button("📋 Списком", "tkl")]])


def found_day(ctx, iid):
    x = item(ctx.db, iid)
    if not x:
        return None
    t = task(ctx.db, x["task_id"]) or {}
    me, d0 = me_key(ctx), ctx.today.isoformat()
    if x["who"] != me and t.get("author") != me:
        return None
    if t.get("self") and t.get("author") != me:
        return None
    dl = x.get("deadline") or d0
    if x["status"] in ("done", "ok"):
        return x.get("bday") or dl
    if x["status"] in ("work", "fail") and dl < d0 and not (t.get("self") and x.get("bday") == dl):
        return d0
    if t.get("author") == me and x["who"] != me and x.get("bday"):
        return x["bday"]
    return dl


# ======================= заметка себе =======================

def add_note(ctx, text, d, prio="", rule=None):
    me = me_key(ctx)
    name = short(ctx.name())
    tid = ctx.db.x("INSERT INTO task_defs (text, photo, author, author_name, kind, rule, prio, days, people, status, self, ts) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,1,?)", text[:2000], "", me, name, "rep" if rule else "once",
                   json.dumps(rule or {}, ensure_ascii=False), prio, 1, json.dumps([[me, name]], ensure_ascii=False), "active", now_s(ctx))
    t = task(ctx.db, tid)
    make_item(ctx, t, me, name, d)
    return tid


@screen("tkq", module=M)
def quick_note(ctx, d=None):
    """«+ Заметка» в дне доски: текст из поля (форма {text})."""
    text = " ".join(str((ctx.form or {}).get("text") or "").split())[:2000]
    try:
        day = date.fromisoformat(str(d))
    except ValueError:
        day = ctx.today
    if text:
        add_note(ctx, text, day.isoformat())
    return board(ctx, head="📝 Заметка добавлена." if text else "")


@screen("tkx", module=M)
def toggle_note(ctx, iid, want=None):
    x = item(ctx.db, iid)
    t = task(ctx.db, x["task_id"]) if x else None
    if not x or not t or not t["self"] or x["who"] != me_key(ctx):
        return board(ctx)
    if (want == "1" and x["status"] == "ok") or (want == "0" and x["status"] != "ok"):
        return board(ctx)
    out = []
    if x["status"] == "ok":
        add_log(ctx, x, "я", "снова в работе")
        save_item(ctx.db, x, status="work", done_ts=None)
        drop_after_copy(ctx, t, x)
    else:
        add_log(ctx, x, "я", "выполнено")
        save_item(ctx.db, x, status="ok", done_ts=now_s(ctx))
        out += after_done(ctx, t, x)
    return out + [board(ctx)]


@screen("tkdel", module=M)
def delete_note(ctx, iid):
    x = item(ctx.db, iid)
    t = task(ctx.db, x["task_id"]) if x else None
    if x and t and t["self"] and x["who"] == me_key(ctx):
        ctx.db.x("DELETE FROM task_items WHERE id = ?", x["id"])
        if not ctx.db.q("SELECT 1 FROM task_items WHERE task_id = ?", t["id"]):
            ctx.db.x("UPDATE task_defs SET status='stop' WHERE id = ?", t["id"])
        return board(ctx, head="🗑 Заметка удалена.")
    return board(ctx)


# ======================= перенос на доске =======================

def month_closed(ctx, x):
    return bool(x.get("deadline")) and str(x["deadline"])[:7] < month_of(ctx.today)


def close_ask(x):
    if (x.get("move") or {}).get("state") == "ask":
        x["move"] = dict(x["move"], state="closed")


def note_day(ctx, x, d):
    if x["status"] in ("ok", "done"):
        x["bday"] = d.isoformat()
        save_item(ctx.db, x)
        return
    if d.isoformat() != x["deadline"]:
        add_log(ctx, x, "я", f"перенёс на {dshow(ctx, d.isoformat())}")
        x["deadline"] = d.isoformat()
    x["bday"] = d.isoformat() if d < ctx.today else None
    save_item(ctx.db, x)


def _pending(ctx):
    return dict(ctx.db.get("tk_moved", {}) or {})


def tell_later(ctx, x, old, new, prev):
    """«срок перенесён» — через MOVE_WAIT секунд (вернул на место — ничего и всё как было)."""
    st = _pending(ctx)
    v = st.get(str(x["id"])) or {"orig": old, "prev": prev}
    if new == v["orig"]:
        st.pop(str(x["id"]), None)
        ctx.db.put("tk_moved", st)
        pv = v.get("prev") or {}
        if pv.get("st") in ("work", "fail") and x["status"] in ("work", "fail"):
            x["status"], x["move"], x["bday"] = pv["st"], pv.get("mv"), pv.get("bd")
            save_item(ctx.db, x)
        return
    v.update(new=new, at=datetime.now().isoformat(timespec="seconds"))
    st[str(x["id"])] = v
    ctx.db.put("tk_moved", st)


def move_item(ctx, iid, to):
    x = item(ctx.db, iid)
    if not x:
        return "Задание не найдено."
    t = task(ctx.db, x["task_id"])
    me = me_key(ctx)
    try:
        d = date.fromisoformat(str(to))
    except ValueError:
        return "Неверная дата."
    if not 2024 <= d.year <= ctx.today.year + 2:
        return "Неверная дата."
    if t["self"] and x["who"] == me:
        note_day(ctx, x, d)
        return ""
    if t["author"] == me and x["who"] != me and x["status"] in ("done", "ok"):
        x["bday"] = d.isoformat()                 # решённое — только место; срок и «вовремя» прежние
        save_item(ctx.db, x)
        return ""
    if t["author"] == me and x["who"] != me and x["status"] in ("work", "fail") and month_closed(ctx, x):
        x["bday"] = d.isoformat()
        save_item(ctx.db, x)
        return ""
    if t["author"] == me and x["who"] != me and x["status"] in ("work", "fail"):
        nd = max(d, ctx.today).isoformat()        # в прошлое — только место; срок не раньше сегодня
        prev = {"st": x["status"], "mv": x.get("move"), "bd": x.get("bday")}
        x["bday"] = d.isoformat() if d < ctx.today else None
        old = x["deadline"]
        if nd == old:
            save_item(ctx.db, x)
            return ""
        add_log(ctx, x, short(ctx.name()), f"перенёс срок {dshow(ctx, old)} → {dshow(ctx, nd)}")
        close_ask(x)
        x["deadline"] = nd
        if x["status"] == "fail":
            x["status"] = "work"
        save_item(ctx.db, x)
        tell_later(ctx, x, old, nd, prev)
        return ""
    return "Это задание переносит его автор. Попросить перенос — «⏳ Перенести срок» в карточке."


@screen("tkm", module=M)
def move_screen(ctx, iid, to):
    msg = move_item(ctx, iid, to)
    return board(ctx, head=msg)


def send_moved(app, db, ctx, now=None):
    """Каждую минуту: «срок перенесён», которым вышли 2 минуты. → [Reply]."""
    st = dict(db.get("tk_moved", {}) or {})
    if not st:
        return []
    now = now or datetime.now()
    out, left = [], {}
    for k, v in st.items():
        try:
            at = datetime.fromisoformat(v.get("at") or "")
        except ValueError:
            at = now
        if (now - at).total_seconds() < MOVE_WAIT:
            left[k] = v
            continue
        x = item(db, k)
        if not x or x["status"] not in ("work", "fail") or x["deadline"] != v.get("new"):
            continue
        t = task(db, x["task_id"]) or {}
        to = uid_of(ctx, x["who"])
        if to:
            out.append(Reply(text=f"📅 Срок задания № {x['task_id']} перенесён: {dshow(ctx, v['orig'])} → {dshow(ctx, v['new'])}.\n"
                                  f"«{(t.get('text') or '')[:150]}»", buttons=[[Button(BTN, "tasks")]], to=to))
    db.put("tk_moved", left)
    return out


# ======================= новое задание =======================

def form_fields(ctx, d=None):
    """Поля «нового задания» — одной страницей."""
    d = d or {}
    lst = people(ctx)
    groups = []
    if ctx.role in ("owner", "admin"):
        for dep in ctx.cfg.departments:
            ps = [[str(i), n] for i, n, g in lst if g == dep.id]
            if ps:
                groups.append([dep.name, ps])
        rest = [[str(i), n] for i, n, g in lst if not ctx.cfg.dept(g)]
        if rest:
            groups.append(["Прочие", rest])
    else:
        groups = [["", [[str(i), n] for i, n, _ in lst]]]
    quick = [[quick_deadline(ctx, k).isoformat(), t] for k, t in ((0, "Сегодня"), (1, "Завтра"), (3, "Через 3 дня"), (7, "Через неделю"))]
    dl = d.get("dl") or quick[1][0]
    return [
        {"k": "text", "t": "area", "l": "Что сделать", "v": d.get("text", ""), "req": 1, "max": 2000, "ph": "Текст задания…"},
        {"k": "photo", "t": "photo", "l": "📷 Фото / скрин", "v": ""},
        {"k": "people", "t": "ppl", "l": "Кому", "groups": groups, "v": ",".join(str(x) for x in d.get("people") or []), "req": 1},
        {"k": "kind", "t": "seg", "l": "Вид", "v": d.get("kind", "once"), "opts": [["once", "Разово"], ["rep", "↻ Повторять"]]},
        {"k": "dl", "t": "date", "l": "Срок", "v": dl, "quick": quick, "min": ctx.today.isoformat(), "when": ["kind", ["once"]]},
        {"k": "freq", "t": "seg", "l": "Как часто", "v": d.get("freq", "daily"), "opts": [[a, b] for a, b in FREQ], "when": ["kind", ["rep"]]},
        {"k": "wd", "t": "multi", "l": "Дни недели", "v": d.get("wd", ""), "when": ["freq", ["weekly"]],
         "opts": [[str(i), WD[i]] for i in range(7) if WEEKDAYS[i] not in ctx.cfg.weekend]},
        {"k": "md", "t": "text", "l": "Числа месяца (через запятую)", "v": d.get("md", ""), "ph": "1, 15", "when": ["freq", ["monthly"]]},
        {"k": "yd", "t": "text", "l": "Дата в году (дд.мм)", "v": d.get("yd", ""), "ph": "31.12", "when": ["freq", ["yearly"]]},
        {"k": "n", "t": "num", "l": "Каждые сколько дней", "v": d.get("n", "10"), "when": ["freq", ["every"]]},
        {"k": "qf", "t": "date", "l": "Первый раз (дальше — каждые 3 месяца в это число)", "v": d.get("qf", ctx.today.isoformat()),
         "when": ["freq", ["quarterly"]]},
        {"k": "days", "t": "seg", "l": "Срок у каждой копии", "v": str(d.get("days", 1)), "opts": [[str(k), t] for k, t in REP_DAYS],
         "when": ["kind", ["rep"]]},
        {"k": "prio", "t": "seg", "l": "Приоритет", "v": d.get("prio", ""),
         "opts": [["hi", "🔴 Высокий"], ["mid", "🟡 Средний"], ["lo", "🔵 Низкий"], ["fz", "⚪ Заморожен"], ["", "Без"]]},
    ]


def set_err(r, msg):
    """Ошибку — в форму этого экрана (поля остаются как были)."""
    for kind, b in r.view.blocks:
        if kind == "ui" and b[0] == "sform":
            b[1]["err"] = msg
            return r
    r.text = msg
    return r


def form_view(title, sub, fields, send, err="", note="", top=None):
    v = View(title, sub)
    v.ui("sform", {"fields": fields, "send": send, "err": err, "note": note, "top": top or []},
         fallback=[f"{f['l']}: {f.get('v') or ''}" for f in fields if f["t"] not in ("photo", "head")])
    return v


@screen("tkn", module=M)
def new_task(ctx):
    if not is_author(ctx):
        return Reply(text="Задания дают руководители. Себе — заметкой: «+ Заметка» на доске.")
    if not people(ctx):
        return Reply(text="Пока некому давать задания: в вашем отделе или команде нет людей.", buttons=[[Button(BTN, "tasks")]])
    v = form_view("➕ Новое задание", "всё на одной странице", form_fields(ctx), ["📨 Отправить", "tks"],
                  note="Похожее уже давали — бот предупредит перед отправкой.")
    return Reply(view=v, buttons=[[Button("‹ К заданиям", "tasks")]])


def parse_rule(ctx, f, old=None):
    """Поля формы → правило повтора или ошибка."""
    k = f.get("freq") or "daily"
    if k == "daily":
        return {"type": "daily"}, ""
    if k == "weekly":
        days = sorted({int(z) for z in str(f.get("wd") or "").split(",") if z.strip().isdigit() and 0 <= int(z) <= 6})
        days = [z for z in days if WEEKDAYS[z] not in ctx.cfg.weekend]
        return ({"type": "weekly", "days": days}, "") if days else ({}, "отметьте дни недели")
    if k == "monthly":
        days = sorted({int(z) for z in re.findall(r"\d+", str(f.get("md") or "")) if 1 <= int(z) <= 31})
        return ({"type": "monthly", "days": days}, "") if days else ({}, "впишите числа месяца")
    if k == "yearly":
        m = re.match(r"^\s*(\d{1,2})[./](\d{1,2})\s*$", str(f.get("yd") or ""))
        try:
            dd, mm = int(m.group(1)), int(m.group(2))
            date(2024, mm, dd)
        except Exception:          # noqa: BLE001
            return {}, "дата в году — дд.мм, например 31.12"
        return {"type": "yearly", "md": f"{mm:02d}-{dd:02d}"}, ""
    if k == "quarterly":
        try:
            q = date.fromisoformat(str(f.get("qf") or "")[:10])
            if not 2024 <= q.year <= ctx.today.year + 2:
                raise ValueError
        except ValueError:
            return {}, "раз в квартал — выберите дату первого раза"
        return {"type": "quarterly", "day": q.day, "from": q.strftime("%Y-%m")}, ""
    if k == "every":
        try:
            n = int(float(str(f.get("n") or "").replace(",", ".")))
        except ValueError:
            n = 0
        if not 1 <= n <= 366:
            return {}, "каждые сколько дней — число от 1 до 366"
        fr = (old or {}).get("from") if (old or {}).get("type") == "every" else ""
        return {"type": "every", "n": n, "from": fr or ctx.today.isoformat()}, ""
    return {}, "выберите, как часто"


def _draft_from_form(ctx, f):
    allowed = {i: n for i, n, _ in people(ctx)}
    ppl = []
    for z in str(f.get("people") or "").split(","):
        z = z.strip()
        if z.lstrip("-").isdigit() and int(z) in allowed and int(z) not in [p[0] for p in ppl]:
            ppl.append([int(z), allowed[int(z)]])
    body = str(f.get("text") or "").strip()[:2000]
    kind = "rep" if f.get("kind") == "rep" else "once"
    prio = f.get("prio") if f.get("prio") in PRIO_T else ""
    errs = []
    if not body:
        errs.append("напишите, что нужно сделать")
    if not ppl:
        errs.append("отметьте, кому")
    d = {"text": body, "people": ppl, "kind": kind, "prio": prio, "rule": {}, "days": 1, "deadline": ""}
    if kind == "once":
        try:
            dl = date.fromisoformat(str(f.get("dl") or ""))
        except ValueError:
            dl = None
        if not dl or dl < ctx.today or dl.year > ctx.today.year + 2:
            errs.append("срок уже прошёл или не выбран")
        else:
            d["deadline"] = dl.isoformat()
    else:
        d["rule"], e = parse_rule(ctx, f)
        if e:
            errs.append(e)
        try:
            d["days"] = max(1, min(31, int(f.get("days") or 1)))
        except ValueError:
            d["days"] = 1
    return d, errs


def draft_sig(d):
    body = json.dumps({k: d.get(k) for k in ("text", "photo", "people", "kind", "deadline", "rule", "prio", "days")},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(body.encode()).hexdigest()[:8]


@screen("tks", module=M)
def submit_task(ctx):
    """Форма нового задания → проверка → похожее было — «Проверьте», иначе сразу отправить."""
    if not is_author(ctx):
        return Reply(text="Задания дают руководители.")
    f = ctx.form or {}
    d, errs = _draft_from_form(ctx, f)
    ph = str(f.get("photo") or "")
    if ph:
        d["photo"] = save_photo(ctx, ph)
        if not d["photo"]:
            errs.append("фото не прикрепилось — добавьте его заново (до 700 КБ, картинка)")
    if errs:
        keep = dict(f, people=[p[0] for p in d["people"]], photo="")
        v = form_view("➕ Новое задание", "всё на одной странице", form_fields(ctx, keep), ["📨 Отправить", "tks"],
                      err="Не отправлено: " + "; ".join(errs) + ".")
        return Reply(view=v, buttons=[[Button("‹ К заданиям", "tasks")]])
    ctx.session["tk_draft"] = d
    ctx.save()
    if similar(ctx, d["text"]):
        return confirm(ctx)
    return send_task(ctx, draft_sig(d))


def confirm(ctx):
    d = ctx.session.get("tk_draft") or {}
    v = View("📌 Проверьте задание", "похожее уже было")
    v.lines([d.get("text") or "", "Кому: " + ", ".join(n for _, n in d.get("people") or []),
             ("Срок: " + dshow(ctx, d.get("deadline"))) if d.get("kind") == "once" else rule_text(d.get("rule"))
             + " · срок " + ("в тот же день" if (d.get("days") or 1) <= 1 else f"{d['days']} раб. дн.")])
    rows = []
    for t in similar(ctx, d.get("text") or ""):
        its = items(ctx.db, "task_id=?", t["id"])
        st = ", ".join(f"{x['name']} — {'🔴 просрочено' if overdue(ctx, x) else STATUS.get(x['status'], '')}" for x in its[:3])
        rows.append({"t": f"№ {t['id']} «{(t['text'] or '')[:80]}»",
                     "s": ("повтор — уже есть" if t["kind"] == "rep" and t["status"] == "active" else "уже было")
                          + f" · {dshow(ctx, t['ts'][:10])}" + (f" · {st}" if st else ""), "chip": ["⚠️", "", "warn"]})
    v.ui("rows", {"items": rows}, "Похожее", fallback=[r["t"] for r in rows])
    return Reply(view=v, buttons=[[Button("📨 Отправить всё равно", f"tkgo:{draft_sig(d)}")], [Button("‹ Изменить", "tkn")]])


@screen("tkgo", module=M)
def send_task(ctx, sig=""):
    d = ctx.session.get("tk_draft") or {}
    if not is_author(ctx):
        return Reply(text="Задания дают руководители.")
    if not d.get("text") or not d.get("people") or (d.get("kind") == "once" and not d.get("deadline")):
        return Reply(text="Черновик задания неполный — начните заново.", buttons=[[Button("➕ Новое задание", "tkn")]])
    if sig != draft_sig(d):
        return [Reply(text="⚠️ Экран устарел — черновик с тех пор изменился. Проверьте ещё раз."), confirm(ctx)]
    rule = d["rule"] if d["kind"] == "rep" else {}
    tid = ctx.db.x("INSERT INTO task_defs (text, photo, author, author_name, kind, rule, prio, days, people, status, self, ts) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,0,?)", d["text"], d.get("photo") or "", me_key(ctx), short(ctx.name()) or "Руководитель",
                   d["kind"], json.dumps(rule, ensure_ascii=False), d.get("prio") or "", d.get("days", 1),
                   json.dumps(d["people"], ensure_ascii=False), "active", now_s(ctx))
    t = task(ctx.db, tid)
    ctx.session.pop("tk_draft", None)
    ctx.save()
    ctx.db.audit(ctx.author(), "task", f"№ {tid} → {', '.join(n for _, n in d['people'])}")
    out = []
    hour = datetime.now().hour
    if d["kind"] == "once":
        out += deliver(ctx, t, [make_item(ctx, t, w, n, d["deadline"]) for w, n in d["people"]])
        sent = "отправлено"
    elif due_today(ctx.cfg, rule, ctx.today) and ISSUE_HOUR <= hour < LATE_HOUR:
        dl = rep_deadline(ctx.cfg, ctx.today, d.get("days"))
        out += deliver(ctx, t, [make_item(ctx, t, w, n, dl) for w, n in d["people"]])
        sent = "отправлено на сегодня; выполнено — следующая сама встанет на следующий день по расписанию"
    else:
        sent = "бот выдаст в ближайший нужный день в 09:00"
    return out + [board(ctx, head=f"✅ Задание № {tid} — {sent}. " + ", ".join(n for _, n in d["people"]))]


# ======================= карточки =======================

def _hist(ctx, x):
    return [f"{a[8:10]}.{a[5:7]} {a[11:16]} · {w}: {s}" for a, w, s in x["log"][-12:]]


@screen("tko", module=M)
def open_card(ctx, iid):
    x = item(ctx.db, iid)
    if not x:
        return board(ctx)
    t = task(ctx.db, x["task_id"])
    me = me_key(ctx)
    if t["self"] and x["who"] == me:                          # заметка — форма: текст, дата, приоритет, повтор
        v = form_view(f"📝 Заметка № {t['id']}", "видна только вам", card_fields(ctx, t, x), ["💾 Сохранить", f"tkcs:{x['id']}"])
        return Reply(view=v, buttons=[[Button("✅ Выполнено" if x["status"] != "ok" else "↩️ Снова в работу",
                                              f"tkx:{x['id']}:{0 if x['status'] == 'ok' else 1}")],
                                      [Button("🗑 Удалить заметку", f"tkdel:{x['id']}")], [Button("‹ К доске", "tasks")]])
    if t["self"]:
        return board(ctx)
    if t["author"] == me and x["who"] != me or (may_manage(ctx, t) and x["who"] != me):
        return item_card(ctx, x["id"])
    if x["who"] == me:                                        # мне поручили
        v = View(f"📥 Задание № {t['id']}", f"от {t['author_name']}")
        v.lines(for_worker(ctx, t, x).split("\n")[1:] + ([STATUS.get(x["status"], "")] if x["status"] != "work" else []))
        kb = worker_kb(x) if x["status"] == "work" else []
        return Reply(view=v, buttons=kb + [[Button("‹ К доске", "tasks")]], files=_photo_list(ctx, t["photo"]))
    cond, args = seen(ctx)
    if ctx.db.q(f"SELECT 1 FROM task_defs t WHERE t.id=? AND {cond}", t["id"], *args):
        return item_card(ctx, x["id"])
    return board(ctx)


def card_fields(ctx, t, x):
    rule = t["rule"] or {}
    k = rule.get("type") or "daily"
    md = rule.get("md") or ctx.today.strftime("%m-%d")
    mine_note = bool(t["self"])
    shown = (x.get("bday") or x["deadline"]) if (x and mine_note) else (x["deadline"] if x else ctx.today.isoformat())
    return [
        {"k": "text", "t": "area", "l": "Задача", "v": t["text"] or "", "req": 1, "max": 2000},
        {"k": "d", "t": "date", "l": "Дата" if mine_note else f"Срок · {x['name']}" if x else "Срок", "v": shown},
        {"k": "prio", "t": "seg", "l": "Приоритет", "v": prio_of(t),
         "opts": [["hi", "🔴 Высокий"], ["mid", "🟡 Средний"], ["lo", "🔵 Низкий"], ["fz", "⚪ Заморожен"], ["", "Без"]]},
        {"k": "kind", "t": "seg", "l": "Повторять", "v": t["kind"], "opts": [["once", "Не повторять"], ["rep", "↻ Повторять"]]},
        {"k": "freq", "t": "seg", "l": "Как часто", "v": k, "opts": [[a, b] for a, b in FREQ], "when": ["kind", ["rep"]]},
        {"k": "wd", "t": "multi", "l": "Дни недели", "when": ["freq", ["weekly"]],
         "v": ",".join(str(z) for z in rule.get("days") or []) if k == "weekly" else "",
         "opts": [[str(i), WD[i]] for i in range(7) if WEEKDAYS[i] not in ctx.cfg.weekend]},
        {"k": "md", "t": "text", "l": "Числа месяца (через запятую)", "ph": "1, 15", "when": ["freq", ["monthly"]],
         "v": ", ".join(str(z) for z in rule.get("days") or []) if k == "monthly" else ""},
        {"k": "yd", "t": "text", "l": "Дата в году (дд.мм)", "v": f"{md[3:5]}.{md[0:2]}", "when": ["freq", ["yearly"]]},
        {"k": "n", "t": "num", "l": "Каждые сколько дней", "v": str(rule.get("n") or 10), "when": ["freq", ["every"]]},
        {"k": "qf", "t": "date", "l": "Первый раз (дальше — каждые 3 месяца)", "when": ["freq", ["quarterly"]],
         "v": f"{rule['from']}-{int(rule.get('day') or 1):02d}" if k == "quarterly" and rule.get("from") else ctx.today.isoformat()},
    ]


@screen("tkcs", module=M)
def card_save(ctx, iid):
    """Сохранить карточку заметки или своего поручения: текст, дата/срок, приоритет, повтор."""
    x = item(ctx.db, iid)
    t = task(ctx.db, x["task_id"]) if x else None
    if not x or not t:
        return board(ctx)
    if not ((t["self"] and t["author"] == me_key(ctx)) or (not t["self"] and may_manage(ctx, t))):
        return board(ctx, head="Эту задачу меняет её автор.")
    if not t["self"] and x["status"] not in ("work", "fail"):
        return board(ctx, head="Это задание уже закрыто — менять нечего.")
    f = ctx.form or {}
    text = " ".join(str(f.get("text") or "").split())[:2000]
    errs = [] if text else ["текст"]
    try:
        d = date.fromisoformat(str(f.get("d") or ""))
    except ValueError:
        d = None
        errs.append("дата")
    rule, rerr = ({}, "") if f.get("kind") != "rep" else parse_rule(ctx, f, t["rule"])
    if rerr:
        errs.append(rerr)
    if not t["self"] and d and d.isoformat() != x["deadline"] and d < ctx.today:
        errs.append("срок поручения — не раньше сегодня")
    if errs:
        v = form_view(f"📝 № {t['id']}", "", card_fields(ctx, t, x), ["💾 Сохранить", f"tkcs:{x['id']}"],
                      err="Не сохранено, поправьте: " + "; ".join(errs) + ".")
        return Reply(view=v, buttons=[[Button("‹ К доске", "tasks")]])
    prio = f.get("prio") if f.get("prio") in PRIO_T else ""
    changed = ["текст"] if text != (t["text"] or "") else []
    kind = "rep" if rule else "once"
    ctx.db.x("UPDATE task_defs SET text=?, prio=?, kind=?, rule=? WHERE id=?", text, prio, kind,
             json.dumps(rule, ensure_ascii=False), t["id"])
    out = []
    if t["self"]:
        if d.isoformat() != (x.get("bday") or x["deadline"]):
            note_day(ctx, x, d)
    elif d.isoformat() != x["deadline"] and month_closed(ctx, x):
        x["bday"] = d.isoformat()
        save_item(ctx.db, x)
    elif d.isoformat() != x["deadline"]:
        old = x["deadline"]
        x["bday"] = None
        add_log(ctx, x, short(ctx.name()), f"срок {dshow(ctx, old)} → {dshow(ctx, d.isoformat())}")
        close_ask(x)
        x["deadline"] = d.isoformat()
        if x["status"] == "fail":
            x["status"] = "work"
        save_item(ctx.db, x)
        changed.append(f"срок {dshow(ctx, old)} → {dshow(ctx, d.isoformat())}")
    if not t["self"] and changed:
        t2 = task(ctx.db, t["id"])
        for y in items(ctx.db, "task_id=? AND status IN ('work','fail')", t["id"]):
            if "текст" not in changed and y["id"] != x["id"]:
                continue
            what = [c for c in changed if c == "текст" or y["id"] == x["id"]]
            out += to_worker(ctx, y, f"✏️ Задание № {t['id']} изменено: {', '.join(what)}.\n\n" + for_worker(ctx, t2, y), worker_kb(y))
    return out + [board(ctx, head="💾 Сохранено.")]


@screen("tki", module=M)
def item_card(ctx, iid):
    """Карточка копии для автора: ответ, история, решения."""
    x = item(ctx.db, iid)
    if not x:
        return board(ctx)
    t = task(ctx.db, x["task_id"])
    cond, args = seen(ctx)
    if x["who"] != me_key(ctx) and not ctx.db.q(f"SELECT 1 FROM task_defs t WHERE t.id=? AND {cond}", t["id"], *args):
        return board(ctx)
    if t["self"] and t["author"] != me_key(ctx):
        return board(ctx)
    v = View(f"📤 Задание № {t['id']}", f"{x['name']} · " + ("🔴 просрочено" if overdue(ctx, x) else STATUS.get(x["status"], x["status"])))
    v.lines([task_head(ctx, t, x), t["text"] or ""] + ([f"💬 {x['answer']}" + (" 📎" if x.get("photo") else "")] if x.get("answer") else []))
    if x["log"]:
        v.lines(_hist(ctx, x), title="История")
    kb = []
    mg = may_manage(ctx, t)
    if mg and x["status"] == "done":
        kb.append([Button("👍 Подтвердить", f"tkac:{x['id']}"), Button("↩️ Вернуть", f"tkrw:{x['id']}")])
    elif mg and x["status"] == "fail":
        kb += [[Button("📌 Оставить в работе", f"tkkp:{x['id']}")],
               [Button("✏️ Изменить и отправить", f"tked:{x['id']}"), Button("✖️ Снять", f"tkrm:{x['id']}")]]
    elif mg and x["status"] == "work":
        kb.append([Button("✏️ Срок и текст", f"tkce:{x['id']}"), Button("✖️ Снять", f"tkrm:{x['id']}")])
    if mg and (x.get("move") or {}).get("state") == "ask":
        kb.append([Button(f"✅ Перенести на {dshow(ctx, x['move']['to'])}", f"tkma:{x['id']}:{x['move']['to']}"),
                   Button("❌ Оставить срок", f"tkmn:{x['id']}")])
    if mg and t["kind"] == "rep" and t["status"] == "active":
        kb.append([Button("⏹ Остановить повтор", f"tkps:{t['id']}")])
    kb.append([Button("‹ К доске", "tasks")])
    return Reply(view=v, buttons=kb, files=_photo_list(ctx, x.get("photo") or t["photo"]))


@screen("tkce", module=M)
def given_edit(ctx, iid):
    x = item(ctx.db, iid)
    t = task(ctx.db, x["task_id"]) if x else None
    if not x or not may_manage(ctx, t) or x["status"] not in ("work", "fail"):
        return board(ctx)
    v = form_view(f"✏️ Задание № {t['id']}", x["name"], card_fields(ctx, t, x), ["💾 Сохранить", f"tkcs:{x['id']}"],
                  note="Новый срок или текст — исполнителю придёт уведомление. Повтор меняется для всех копий задания.")
    return Reply(view=v, buttons=[[Button("‹ К карточке", f"tki:{x['id']}")]])


# ======================= ответ исполнителя =======================

@screen("tka", module=M)
def answer_form(ctx, iid, act="ok"):
    x = item(ctx.db, iid)
    if not x or x["who"] != me_key(ctx):
        return board(ctx)
    t = task(ctx.db, x["task_id"])
    if x["status"] != "work":
        return board(ctx, head=f"Задание № {t['id']} уже {STATUS.get(x['status'], '')}.")
    tomorrow = work_day(ctx.cfg, ctx.today + timedelta(days=1)).isoformat()
    fields = [{"k": "_h", "t": "head", "l": (t["text"] or "")[:300]},
              {"k": "ans", "t": "seg", "l": "Ответ", "v": act if act in ("ok", "no", "mv") else "ok",
               "opts": [["ok", "✅ Выполнено"], ["no", "❌ Не выполнено"], ["mv", "⏳ Перенести срок"]]},
              {"k": "to", "t": "date", "l": f"На какую дату · сейчас срок {dshow(ctx, x['deadline'])}", "v": tomorrow,
               "min": ctx.today.isoformat(), "when": ["ans", ["mv"]]},
              {"k": "text", "t": "area", "l": "Комментарий", "max": 1500, "ph": "Что сделано / почему не выполнено / почему перенести",
               "hint": "для ❌ и ⏳ — обязательно"},
              {"k": "photo", "t": "photo", "l": "📷 Фото / скрин", "when": ["ans", ["ok", "no"]]}]
    v = form_view(f"📌 Задание № {t['id']}", f"до {dshow(ctx, x['deadline'])} · от {t['author_name']}", fields,
                  ["📨 Отправить ответ", f"tkas:{x['id']}"], note=f"Ответ уйдёт: {t['author_name']}.")
    return Reply(view=v, buttons=[[Button("‹ Назад", "tasks")]], files=_photo_list(ctx, t["photo"]))


@screen("tkas", module=M)
def answer_submit(ctx, iid):
    x = item(ctx.db, iid)
    if not x or x["who"] != me_key(ctx):
        return board(ctx)
    t = task(ctx.db, x["task_id"])
    if x["status"] != "work":
        return board(ctx, head="Это задание уже закрыто или снято — ответ не нужен.")
    f = ctx.form or {}
    ans = f.get("ans") if f.get("ans") in ("ok", "no", "mv") else "ok"
    text = str(f.get("text") or "").strip()[:1500]
    if ans in ("no", "mv") and not text:
        r = answer_form(ctx, iid, ans)
        set_err(r, "Напишите комментарий — " + ("почему не выполнено." if ans == "no" else "почему переносите."))
        return r
    who = short(ctx.name())
    if ans == "mv":
        try:
            dd = date.fromisoformat(str(f.get("to") or ""))
        except ValueError:
            dd = None
        if not dd or dd < ctx.today or dd.year > ctx.today.year + 2:
            r = answer_form(ctx, iid, "mv")
            set_err(r, "Дата уже прошла — перенести можно только вперёд.")
            return r
        x["move"] = {"to": dd.isoformat(), "why": text[:300], "state": "ask"}
        add_log(ctx, x, who, f"просит перенести на {dshow(ctx, dd.isoformat())}: {text[:120]}")
        save_item(ctx.db, x)
        out = to_author(ctx, t, f"⏳ {x['name']} просит перенести срок задания № {t['id']} с {dshow(ctx, x['deadline'])} на "
                                f"{dshow(ctx, dd.isoformat())}\n«{(t['text'] or '')[:150]}»\nПричина: {text}",
                        [[Button("✅ Перенести", f"tkma:{x['id']}:{dd.isoformat()}"), Button("❌ Оставить срок", f"tkmn:{x['id']}")]])
        return out + [board(ctx, head="📨 Просьба отправлена — ответ придёт в ленту.")]
    ph = str(f.get("photo") or "")
    fid = save_photo(ctx, ph) if ph else ""
    if ph and not fid:
        r = answer_form(ctx, iid, ans)
        set_err(r, "Фото не прикрепилось — добавьте его заново (картинка до 700 КБ).")
        return r
    ok = ans == "ok"
    x["answer"], x["photo"], x["ans_ts"] = text, fid, now_s(ctx)
    add_log(ctx, x, who, ("выполнено" if ok else "не выполнено") + (f": {text[:200]}" if text else "") + (" 📎" if fid else ""))
    save_item(ctx.db, x, status="done" if ok else "fail")
    out = after_done(ctx, t, x) if ok else []
    late = " (с опозданием)" if x["deadline"] and ctx.today.isoformat() > x["deadline"] else ""
    head = (f"{'✅' if ok else '❌'} {x['name']}: задание № {t['id']} {'выполнено' if ok else 'не выполнено'}{late}\n"
            f"«{(t['text'] or '')[:150]}»" + (f"\n💬 {text}" if text else ""))
    kb = ([[Button("👍 Подтвердить", f"tkac:{x['id']}"), Button("↩️ Вернуть на доработку", f"tkrw:{x['id']}")]] if ok else
          [[Button("📌 Оставить в работе", f"tkkp:{x['id']}")], [Button("✏️ Изменить и отправить", f"tked:{x['id']}"),
                                                                Button("✖️ Снять", f"tkrm:{x['id']}")]])
    out += to_author(ctx, t, head, kb, fid)
    return out + [board(ctx, head="📨 Ответ отправлен: " + t["author_name"] + ("" if ok else " — он решит, что дальше."))]


# ======================= решения автора =======================

def _mine(ctx, iid):
    x = item(ctx.db, iid)
    t = task(ctx.db, x["task_id"]) if x else None
    return (x, t) if x and t and may_manage(ctx, t) and not t["self"] else (None, None)


@screen("tkac", module=M)
def accept(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "done":
        return board(ctx)
    add_log(ctx, x, short(ctx.name()), "подтвердил выполнение")
    save_item(ctx.db, x, status="ok", done_ts=x.get("ans_ts") or now_s(ctx))
    out = to_worker(ctx, x, f"👍 Задание № {t['id']} принято. Спасибо!") + after_done(ctx, t, x)
    return out + [board(ctx, head="👍 Принято.")]


@screen("tkrw", module=M)
def rework(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "done":
        return board(ctx, head="Это задание уже решено или снято — возвращать не нужно.")
    v = form_view(f"↩️ Задание № {t['id']}", x["name"], [{"k": "text", "t": "area", "l": "Что доработать?", "max": 600,
                                                          "ph": "Напишите — исполнитель получит это сообщение"}],
                  ["↩️ Вернуть на доработку", f"tkrws:{x['id']}"])
    return Reply(view=v, buttons=[[Button("‹ К карточке", f"tki:{x['id']}")]])


@screen("tkrws", module=M)
def rework_send(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "done":
        return board(ctx, head="Это задание уже решено или снято — возвращать не нужно.")
    text = str((ctx.form or {}).get("text") or "").strip()[:600]
    add_log(ctx, x, short(ctx.name()), "вернул на доработку" + (f": {text[:200]}" if text else ""))
    save_item(ctx.db, x, status="work", answer="", photo="")
    drop_after_copy(ctx, t, x)
    out = to_worker(ctx, x, f"↩️ Задание № {t['id']} вернули на доработку" + (f":\n💬 {text}" if text else "")
                    + "\n\n" + for_worker(ctx, t, x), worker_kb(x))
    return out + [board(ctx, head="↩️ Вернул на доработку.")]


@screen("tkkp", module=M)
def keep(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "fail":
        return board(ctx)
    add_log(ctx, x, short(ctx.name()), "оставил в работе")
    save_item(ctx.db, x, status="work")
    return to_worker(ctx, x, f"📌 Задание № {t['id']} остаётся в работе.\n\n" + for_worker(ctx, t, x), worker_kb(x)) + \
        [board(ctx, head="📌 Оставлено в работе.")]


@screen("tked", module=M)
def edit_after_fail(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "fail":
        return board(ctx, head="Это задание уже решено или снято — правка не нужна.")
    dl = x["deadline"] if x["deadline"] and x["deadline"] >= ctx.today.isoformat() else \
        work_day(ctx.cfg, ctx.today + timedelta(days=1)).isoformat()
    v = form_view(f"✏️ Задание № {t['id']}", x["name"],
                  [{"k": "text", "t": "area", "l": "Текст задания", "v": t["text"] or "", "max": 2000, "req": 1},
                   {"k": "dl", "t": "date", "l": "Срок", "v": dl, "min": ctx.today.isoformat(), "req": 1}],
                  ["📨 Отправить снова", f"tkeds:{x['id']}"],
                  note=f"Его ответ: «{(x['answer'] or '')[:200]}»" if x["answer"] else "")
    return Reply(view=v, buttons=[[Button("‹ К карточке", f"tki:{x['id']}")]])


@screen("tkeds", module=M)
def edit_send(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] != "fail":
        return board(ctx, head="Это задание уже решено или снято — правка не нужна.")
    f = ctx.form or {}
    text = str(f.get("text") or "").strip()[:2000]
    try:
        dd = date.fromisoformat(str(f.get("dl") or ""))
    except ValueError:
        dd = None
    if not text or not dd or dd < ctx.today:
        r = edit_after_fail(ctx, iid)
        set_err(r, "Нужен текст и срок с сегодняшнего дня.")
        return r
    multi = text != (t["text"] or "") and len(items(ctx.db, "task_id=?", t["id"])) > 1
    if text != (t["text"] or "") and not multi:
        ctx.db.x("UPDATE task_defs SET text=? WHERE id=?", text, t["id"])
    elif multi:
        add_log(ctx, x, short(ctx.name()), "новый текст: " + text[:300])
    add_log(ctx, x, short(ctx.name()), f"изменил и отправил снова, срок {dshow(ctx, dd.isoformat())}")
    save_item(ctx.db, x, status="work", answer="", photo="", deadline=dd.isoformat())
    t = task(ctx.db, t["id"])
    out = to_worker(ctx, x, "✏️ Задание изменено и отправлено снова:\n\n" + for_worker(ctx, t, x)
                    + (f"\n\n✏️ Для вас теперь: {text}" if multi else ""), worker_kb(x))
    return out + [board(ctx, head="📨 Отправлено снова.")]


@screen("tkrm", module=M)
def remove(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or x["status"] not in ("fail", "work", "done"):
        return board(ctx)
    add_log(ctx, x, short(ctx.name()), "снял задание")
    save_item(ctx.db, x, status="off", done_ts=now_s(ctx))
    out = to_worker(ctx, x, f"✖️ Задание № {t['id']} снято.") + after_done(ctx, t, x)
    return out + [board(ctx, head="✖️ Снято.")]


@screen("tkma", module=M)
def move_yes(ctx, iid, to=""):
    x, t = _mine(ctx, iid)
    if not x or (x.get("move") or {}).get("state") != "ask":
        return board(ctx, head="Просьба о переносе уже решена.")
    if x["status"] != "work":
        x["move"] = dict(x["move"], state="closed")
        save_item(ctx.db, x)
        return board(ctx, head=f"Просьба о переносе уже не нужна: задание № {t['id']} — {STATUS.get(x['status'], x['status'])}.")
    if to != x["move"].get("to"):
        return item_card(ctx, x["id"])            # кнопка старой просьбы — решить по новой
    mv = x["move"]
    add_log(ctx, x, short(ctx.name()), f"перенёс срок на {dshow(ctx, mv['to'])}")
    mv["state"] = "yes"
    save_item(ctx.db, x, deadline=mv["to"])
    return to_worker(ctx, x, f"✅ Срок задания № {t['id']} перенесён на {dshow(ctx, mv['to'])}.") + \
        [board(ctx, head=f"✅ Перенесено на {dshow(ctx, mv['to'])}.")]


@screen("tkmn", module=M)
def move_no(ctx, iid):
    x, t = _mine(ctx, iid)
    if not x or (x.get("move") or {}).get("state") != "ask":
        return board(ctx, head="Просьба о переносе уже решена.")
    add_log(ctx, x, short(ctx.name()), "не перенёс срок")
    x["move"]["state"] = "no"
    save_item(ctx.db, x)
    return to_worker(ctx, x, f"❌ Срок задания № {t['id']} не перенесён — до {dshow(ctx, x['deadline'])}.") + \
        [board(ctx, head="❌ Срок оставлен.")]


@screen("tkps", module=M)
def stop_repeat(ctx, tid):
    t = task(ctx.db, tid)
    if not t or not may_manage(ctx, t):
        return board(ctx)
    ctx.db.x("UPDATE task_defs SET status='stop' WHERE id=?", t["id"])
    ctx.db.audit(ctx.author(), "task_stop", f"№ {t['id']}")
    out = []
    if not t["self"]:
        for x in items(ctx.db, "task_id=? AND status='work'", t["id"]):
            future = str(x.get("iday") or "") > ctx.today.isoformat()
            add_log(ctx, x, short(ctx.name()), "повтор остановлен — копия снята")
            save_item(ctx.db, x, status="off", done_ts=now_s(ctx))
            if not future:
                out += to_worker(ctx, x, f"✖️ Задание № {t['id']} снято — повтор остановлен.")
    return out + [board(ctx, head=f"⏹ Повтор № {t['id']} больше не выдаётся.")]


# ======================= списком, поиск =======================

def _counts(ctx):
    cond, args = seen(ctx)
    mine = [_item(x) for x in ctx.db.q(f"SELECT i.* FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE {cond} ORDER BY i.id DESC", *args)]
    return mine


def waits_me(ctx, x, t=None):
    t = t or task(ctx.db, x["task_id"])
    if not t or t["author"] != me_key(ctx):
        return False
    return x["status"] == "fail" or (x.get("move") or {}).get("state") == "ask"


@screen("tkl", module=M)
def overview(ctx, part=None):
    """📋 Списком: мне дали, выданные мной (ждут решения / просрочено / в работе / история / повторы), по людям."""
    month = month_of(ctx.today)
    me = me_key(ctx)
    got = items(ctx.db, "who=? AND status IN ('work','done','fail') AND task_id NOT IN (SELECT id FROM task_defs WHERE self=1) "
                        "AND COALESCE(iday,'')<=?", me, ctx.today.isoformat())
    v = View("📋 Задания списком", "")
    mine = _counts(ctx) if is_author(ctx) else []
    if part in ("w", "o", "r", "h"):
        rows = {"w": [x for x in mine if waits_me(ctx, x)], "o": [x for x in mine if overdue(ctx, x)],
                "r": [x for x in mine if x["status"] == "work"], "h": mine[:40]}[part]
        title = {"w": "⏳ Ждут вашего решения", "o": "🔴 Просрочено", "r": "📋 В работе", "h": "📜 История"}[part]
        v.ui("rows", {"items": [_row(ctx, x) for x in rows], "empty": "Пусто."}, f"{title} · {len(rows)}",
             fallback=[_row(ctx, x)["t"] for x in rows] or ["Пусто."])
        return Reply(view=v, buttons=[[Button("‹ Списком", "tkl")]])
    if part == "p":
        cond, args = seen(ctx)
        reps = [_task(t) for t in ctx.db.q(f"SELECT * FROM task_defs t WHERE t.kind='rep' AND t.status='active' AND {cond} ORDER BY id DESC", *args)]
        v.ui("rows", {"items": [{"t": f"№ {t['id']} · {(t['text'] or '')[:80]}",
                                 "s": f"{rule_text(t['rule'])} · " + ", ".join(n for _, n in t["people"]),
                                 "chip": ["⏹", f"tkps:{t['id']}", "bad"] if may_manage(ctx, t) else None} for t in reps],
                      "empty": "Повторов нет."}, "↻ Повторы", fallback=[f"№ {t['id']} {t['text']}" for t in reps])
        return Reply(view=v, buttons=[[Button("‹ Списком", "tkl")]])
    if got:
        v.ui("rows", {"items": [_row(ctx, x, mine_=True) for x in got]}, f"📥 Мне дали · {len(got)}",
             fallback=[_row(ctx, x, mine_=True)["t"] for x in got])
    else:
        v.note("Сейчас заданий для вас нет 👍")
    kb = []
    if is_author(ctx):
        cur = [x for x in mine if x["ts"][:7] == month or x["status"] in ("work", "done", "fail")]
        work = [x for x in cur if x["status"] == "work"]
        od = [x for x in work if overdue(ctx, x)]
        wait = [x for x in cur if waits_me(ctx, x)]
        okm = [x for x in mine if (x["status"] == "ok" and (x["done_ts"] or "")[:7] == month)
               or (x["status"] == "done" and (x["ans_ts"] or "")[:7] == month)]
        v.ui("tiles", {"items": [["⏳", "Ждут решения", str(len(wait)), "ваших", "tkl:w"], ["🔴", "Просрочено", str(len(od)), "сейчас", "tkl:o"],
                                 ["📋", "В работе", str(len(work)), "выдано", "tkl:r"], ["✅", "Выполнено", str(len(okm)), "за месяц", "tkl:h"]]},
             "📤 Выданные мной", fallback=[f"В работе {len(work)} · ждут решения {len(wait)} · просрочено {len(od)} · выполнено {len(okm)}"])
        by = {}
        for x in mine:
            if x["status"] == "off":
                continue
            s = by.setdefault(x["name"], [0, 0, 0, 0, 0])
            if x["status"] == "miss":
                s[3] += 1
            if x["status"] == "fail":
                s[4] += 1
            if x["status"] in ("ok", "done"):
                s[0] += 1
                at = x["done_ts"] if x["status"] == "ok" else x["ans_ts"]
                if at and x["deadline"] and at[:10] <= x["deadline"]:
                    s[1] += 1
            if overdue(ctx, x):
                s[2] += 1
        if by:
            v.ui("rows", {"items": [{"t": n, "s": f"выполнено {a} · вовремя {b_}" + (f" ({round(b_ / a * 100)}%)" if a else "")
                                     + (f" · ❌ {f_}" if f_ else "") + (f" · 🔴 {c}" if c else "") + (f" · ⚫ {m_}" if m_ else ""),
                                     "bar": [b_ / a if a else 0, "good" if a and b_ / a >= .8 else "warn"]}
                                    for n, (a, b_, c, m_, f_) in sorted(by.items(), key=lambda z: -z[1][0])]}, "По людям",
                 fallback=[f"{n}: {a}" for n, (a, *_r) in by.items()])
        kb = [[Button("↻ Повторы", "tkl:p"), Button("📜 История", "tkl:h")], [Button("➕ Новое задание", "tkn")]]
        if is_admin(ctx):
            f = ctx.db.get("task_pen_from", "") or ""
            kb.append([Button(f"⚙️ «Не в срок» для зарплаты: {'с ' + dshow(ctx, f) if f else 'со всех'}", "tkpen")])
    kb.append([Button("‹ К доске", "tasks")])
    return Reply(view=v, buttons=kb)


def _row(ctx, x, mine_=False):
    t = task(ctx.db, x["task_id"]) or {}
    mark = "🔴" if overdue(ctx, x) else {"work": "▫️", "done": "⏳", "fail": "❌", "ok": "✅", "off": "✖️", "miss": "⚫"}.get(x["status"], "")
    if (x.get("move") or {}).get("state") == "ask":
        mark = "⏳📅"
    who = f"от {short(t.get('author_name'))}" if mine_ else x["name"]
    return {"t": f"{mark} № {x['task_id']} · {(t.get('text') or '')[:60]}", "s": f"{who} · до {dshow(ctx, x['deadline'])}",
            "chip": ["›", f"tko:{x['id']}" if mine_ else f"tki:{x['id']}", ""]}


@screen("tkpen", module=M)
def pen_setting(ctx, val=None):
    """С какого дня задания считаются для зарплаты («не в срок» / «в срок») — только администратор."""
    if not is_admin(ctx):
        return overview(ctx)
    if val in ("on", "off"):
        ctx.db.put("task_pen_from", ctx.today.isoformat() if val == "on" else "")
        ctx.db.audit(ctx.author(), "task_pen", val)
        return overview(ctx)
    f = ctx.db.get("task_pen_from", "") or ""
    v = View("⚙️ Задания и зарплата", "")
    v.lines(["Если в схеме зарплаты есть «Сдельно → задачи в срок / не в срок», бот считает их за месяц срока.",
             "«Не в срок»: ❌ не выполнено, ⚫ не ответил и срок прошёл без ответа (на последний день месяца). Снятое — не считается.",
             "Сейчас: " + (f"задания со сроком с {dshow(ctx, f)}." if f else "все задания.")])
    return Reply(view=v, buttons=[[Button("Считать с сегодняшнего дня", "tkpen:on") if not f else Button("Считать все", "tkpen:off")],
                                  [Button("‹ Списком", "tkl")]])


@screen("tkf", module=M)
def search(ctx):
    """🔎 Поиск задачи по словам: свои заметки, поручения, задания мне — и выполненные."""
    q = str((ctx.form or {}).get("q") or "").strip()[:80]
    v = View("🔎 Найти задачу", "")
    v.ui("form", {"fields": [{"k": "q", "l": "Слова из задачи", "v": q, "ph": "например: касса", "w": True}], "send": ["🔎 Найти", "tkf"]},
         fallback=["Напишите слова из задачи"])
    if q:
        found = find(ctx, q)
        v.ui("rows", {"items": found, "empty": "Задач с такими словами не нашёл."}, f"Нашлось · {len(found)}",
             fallback=[x["t"] for x in found] or ["Ничего"])
    return Reply(view=v, buttons=[[Button("‹ К доске", "tasks")]])


def _toks(s):
    return re.findall(r"[а-яёa-z0-9]+", str(s or "").lower().replace("ё", "е"))


def find(ctx, q, limit=25):
    ws = _toks(q)
    if not ws:
        return []
    me, d0 = me_key(ctx), ctx.today.isoformat()
    rows = ctx.db.q("SELECT i.*, t.text, t.author, t.author_name, t.kind AS tkind, t.rule, t.self FROM task_items i "
                    "JOIN task_defs t ON t.id = i.task_id WHERE (i.who=? OR t.author=?) ORDER BY i.id DESC LIMIT 5000", me, me)
    seen_, out = set(), []
    for r in rows:
        if r["self"] and r["author"] != me:
            continue
        key = (r["task_id"], r["who"])
        if key in seen_:
            continue
        tk = _toks(r["text"])
        if not all(any(t.startswith(w) if not w[0].isdigit() else t == w for t in tk) for w in ws):
            continue
        seen_.add(key)
        k = kind_of(r, me)
        bits = ["📝 заметка" if k == "note" else f"→ {r['name']}" if k == "given" else f"от {short(r['author_name'])}"]
        if r["tkind"] == "rep":
            bits.append(rule_text(json.loads(r["rule"] or "{}")))
        bits.append(STATUS.get(r["status"], r["status"]).split(" — ")[0])
        if r["status"] in ("work", "fail"):
            bits.append("сегодня" if r["deadline"] == d0 else f"срок {dshow(ctx, r['deadline'])}")
        out.append({"t": (r["text"] or "")[:120], "s": " · ".join(bits), "chip": ["›", f"tasks:fi:{r['id']}", ""],
                    "_o": (0 if r["status"] in ("work", "fail") else 1, -r["id"])})
    out.sort(key=lambda x: x.pop("_o"))
    return out[:limit]


# ======================= по расписанию =======================

def issue(app, now=None):
    """09:00 рабочего дня (раз в день): повторы на сегодня — первая копия тем, у кого нет открытой, и копии «на сегодня»,
    отложенные после выполнения. → [Reply]."""
    db = app.db()
    ctx = app.ctx(0)
    d = (now or datetime.now()).date()
    if is_off(app.cfg, d) or db.get("tk_issued") == d.isoformat():
        return []
    db.put("tk_issued", d.isoformat())
    out = []
    active = {e["id"] for e in db.employees()}
    for t in db.q("SELECT * FROM task_defs WHERE kind='rep' AND status='active'"):
        t = _task(t)
        if not due_today(app.cfg, t["rule"], d):
            continue
        dl = rep_deadline(app.cfg, d, t["days"])
        new = []
        for who, name in t["people"]:
            if who > 0 and who not in active:
                continue                                   # уволенным не выдаём
            xs = items(db, "task_id=? AND who=?", t["id"], who)
            if any(x["status"] in ("work", "fail") or (x["deadline"] or "") >= d.isoformat() for x in xs):
                continue
            last = max(xs, key=lambda x: x["id"]) if xs else None
            if last and last["status"] not in ("ok", "done"):
                continue                                   # прошлая снята / не ответил — не оживляем
            new.append(db.x("INSERT INTO task_items (task_id, who, name, deadline, status, log, ts, iday) VALUES (?,?,?,?,?,?,?,?)",
                            t["id"], who, name, dl, "work", json.dumps([[f"{d} {ISSUE_HOUR:02d}:00", "бот", f"повтор — на {dl[8:10]}.{dl[5:7]}"]],
                                                                       ensure_ascii=False), f"{d} {ISSUE_HOUR:02d}:00:00", d.isoformat()))
        if new and not t["self"]:
            out += deliver(ctx, t, new)
    for x in items(db, "status='work' AND iday=?", d.isoformat()):      # «после выполнения» — выданные на сегодня заранее
        t = task(db, x["task_id"])
        if t and not t["self"] and t["status"] == "active" and (x["ts"] or "")[:10] < d.isoformat():
            out += deliver(ctx, t, [x["id"]])
    return out


@tick
def _tick(app, now):
    """Каждую минуту из фоновой работы: «срок перенесён» и выдача повторов в 09:00."""
    if M not in app.cfg.modules:
        return []
    db = app.db()
    out = send_moved(app, db, app.ctx(0), now)
    if now.hour == ISSUE_HOUR:
        out += issue(app, now)
    return out


def reminder(ctx, urgent_only=False, late=False):
    """Напоминание этому человеку: задания в работе со сроком сегодня и раньше; высокий приоритет — каждый раз."""
    if not ctx.me or is_off(ctx.cfg, ctx.today):
        return None
    per = []
    for x in items(ctx.db, "who=? AND status='work' AND COALESCE(iday,'')<=?", ctx.me["id"], ctx.today.isoformat()):
        t = task(ctx.db, x["task_id"])
        if not t or t["self"]:
            continue
        hi = prio_of(t) == "hi"
        if urgent_only and not hi:
            continue
        if late and ctx.role in SELLERS and not hi:
            continue                                    # торговым в 16:00 — только высокий приоритет
        if not hi and x["deadline"] and x["deadline"] > ctx.today.isoformat():
            continue
        per.append((t, x))
    if not per:
        return None
    lines = ["⏰ Напоминание: задания в работе"]
    kb = []
    for t, x in per[:10]:
        lines.append(f"{'🔴 ' if prio_of(t) == 'hi' else ''}{'просрочено · ' if overdue(ctx, x) else ''}№ {t['id']} · до "
                     f"{dshow(ctx, x['deadline'])} · {(t['text'] or '')[:80]}")
        kb.append([Button(f"✅ № {t['id']}", f"tka:{x['id']}:ok"), Button(f"❌ № {t['id']}", f"tka:{x['id']}:no"),
                   Button("⏳", f"tka:{x['id']}:mv")])
    return Reply(text="\n".join(lines), buttons=kb + [[Button(BTN, "tasks")]])


@job("tasks_remind10", "10:00", ALL_ROLES, "10:00: напоминание о заданиях", module=M)
def remind10(ctx):
    return reminder(ctx)


@job("tasks_remind12", "12:00", ALL_ROLES, "12:00: высокий приоритет", module=M)
def remind12(ctx):
    return reminder(ctx, urgent_only=True)


@job("tasks_remind14", "14:00", ALL_ROLES, "14:00: высокий приоритет", module=M)
def remind14(ctx):
    return reminder(ctx, urgent_only=True)


@job("tasks_remind16", "16:00", ALL_ROLES, "16:00: напоминание о заданиях", module=M)
def remind16(ctx):
    return reminder(ctx, late=True)


@job("tasks_morning", "08:00", AUTHOR_ROLES | {"admin"}, "08:00: сводка автору заданий", module=M)
def morning(ctx):
    """Автору — просрочено, ждёт решения, не ответили."""
    if not is_author(ctx) or is_off(ctx.cfg, ctx.today):
        return None
    cond, args = ("t.self = 0 AND t.author = ?", [me_key(ctx)]) if is_admin(ctx) else seen(ctx)
    mine = [_item(x) for x in ctx.db.q(f"SELECT i.* FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE {cond}", *args)]
    od = [x for x in mine if overdue(ctx, x)]
    wait = [x for x in mine if waits_me(ctx, x)]
    work = [x for x in mine if x["status"] == "work"]
    if not (od or wait):
        return None
    lines = ["📌 Задания на утро", f"В работе {len(work)} · ждут вашего решения {len(wait)} · просрочено {len(od)}"]
    lines += [f"🔴 № {x['task_id']} · {x['name']} · срок был {dshow(ctx, x['deadline'])}" for x in od[:8]]
    lines += [f"⏳ № {x['task_id']} · {x['name']} · {STATUS.get(x['status'], 'просит перенести срок')}" for x in wait[:8]]
    return Reply(text="\n".join(lines), buttons=[[Button(BTN, "tasks")]])


# ======================= для зарплаты: задачи в срок / не в срок =======================

def _month_items(pay, emp):
    s, e = str(pay.kpi.start), str(pay.kpi.end)
    f = pay.db.get("task_pen_from", "") or ""
    try:
        rows = pay.db.q("SELECT i.* FROM task_items i JOIN task_defs t ON t.id=i.task_id WHERE t.self=0 AND i.who=? "
                        "AND i.deadline BETWEEN ? AND ? AND i.deadline >= ?", emp["id"], s, e, f)
    except Exception:              # noqa: BLE001
        return []
    return rows


def _tasks(pay, emp, good):
    today = min(pay.cal.today, pay.cal.end).isoformat()
    month_over = pay.cal.today > pay.cal.end
    n = 0
    for x in _month_items(pay, emp):
        at = (x.get("done_ts") if x["status"] == "ok" else x.get("ans_ts")) or ""
        on_time = x["status"] in ("ok", "done") and at[:10] <= x["deadline"]
        late = x["status"] in ("fail", "miss") or (x["status"] in ("ok", "done") and not on_time) or \
            (x["status"] == "work" and x["deadline"] < today and month_over and "ask" not in str(x.get("move") or ""))
        n += on_time if good else late
    return n


from core.pay import components as _C  # noqa: E402

_C.unit("tasks_done", lambda pay, emp: _tasks(pay, emp, True))
_C.unit("tasks_late", lambda pay, emp: _tasks(pay, emp, False))
