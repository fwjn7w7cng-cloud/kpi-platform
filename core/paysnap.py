"""🕓 Как менялась зарплата (Адам 19.11, как в его первом боте): раз в час бот запоминает расчёт каждого — строки, итог и
главные цифры (продажи, сбор, АКБ, SKU, просрочка) — только если с прошлого снимка что-то поменялось; хранит KEEP_DAYS дня.
Экран `pch:<сотрудник>[:<снимок>]` — «было → сейчас»: итог, какие строки прибавили ▲ и убавили ▼, что изменилось в цифрах и
что за это время пришло (отчёты 1С, табель, правки).

Снимает фоновая работа (core/jobs/worker.py) в начале каждого часа. В демо снимков нет — история собирается на лету: расчёт
«каким он был» вечером каждого из прошлых дней (`Kpi(upto=день)`), события — по датам фактов.
"""
import hashlib
import json
from datetime import date, datetime, timedelta

from core.calendar import month_of, shift_month
from core.kpi.engine import SELLERS, Kpi
from core.month import cycle
from core.pay.engine import Payroll

KEEP_DAYS = 4
SHOW = 18                     # сколько времён показать кнопками
FACTS = [("sales", "Продажи", "₽"), ("collect", "Сбор денег", "₽"), ("akb", "АКБ", "кл."), ("sku", "SKU в точках", "поз."),
         ("overdue", "Просрочка", "%")]
KIND_TITLE = {"sales": "Продажи", "debts": "Дебиторка", "payments": "Сбор денег", "visits": "Маршруты", "returns": "Возвраты",
              "stock": "Остатки", "item_sales": "Продажи по товарам", "trips": "Рейсы", "trip_stops": "Рейсы",
              "clients": "Клиенты", "groups": "Группы товаров"}
TS_KIND = {"absent": "пропуск", "truancy": "прогул", "half": "полдня", "late": "опоздание", "fine": "штраф",
           "extra": "работа в выходной", "km": "км за день"}


def now_s(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M")


# ---------- снимок ----------

def pack(slip, k=None):
    """Расчёт → (итог, [[строка, сумма]], {цифры}) — суммы до рубля, чтобы копейки не «менялись» каждый час."""
    agg = {}
    for r in slip.rows:
        agg[str(r[0])] = agg.get(str(r[0]), 0) + float(r[1] or 0)
    rows = [[n, round(v)] for n, v in agg.items()]
    facts = {}
    if k is not None and slip.emp.get("role") in SELLERS:
        try:
            m = k.emp(slip.emp["id"])
            for code, _t, unit in FACTS:
                if code in m:
                    f = m[code].fact or 0
                    facts[code] = round(f * 1000) / 10 if unit == "%" else round(f)
        except Exception:                              # noqa: BLE001 — цифры для подсказки, расчёт важнее
            pass
    return round(slip.total), rows, facts


def months_open(db, today):
    cur = month_of(today)
    prev = shift_month(cur, -1)
    return [cur] + ([prev] if cycle.status(db, prev) != "approved" else [])


def take(app, now=None):
    """Снять расчёт всех людей открытых месяцев; пишет только изменившихся. → сколько снимков записано."""
    db = app.db()
    now = now or datetime.now()
    ts, n = now_s(now), 0
    for month in months_open(db, now.date()):
        k = Kpi(app.cfg, db, month, now.date(), app.hooks)
        p = Payroll(app.cfg, db, k, app.hooks)
        for e in db.employees(active=None):
            if not e["active"] and not (e.get("fired") or "") >= f"{month}-01":
                continue
            try:
                total, rows, facts = pack(p.calc(e), k)
            except Exception:                          # noqa: BLE001
                continue
            h = hashlib.md5(json.dumps([total, rows, facts], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            last = db.one("SELECT hash FROM pay_hist WHERE emp_id = ? AND month = ? ORDER BY id DESC LIMIT 1", e["id"], month)
            if last and last["hash"] == h:
                continue
            db.x("INSERT INTO pay_hist (ts, emp_id, month, total, rows, facts, hash) VALUES (?,?,?,?,?,?,?)",
                 ts, e["id"], month, total, json.dumps(rows, ensure_ascii=False), json.dumps(facts), h)
            n += 1
    db.x("DELETE FROM pay_hist WHERE ts < ?", now_s(now - timedelta(days=KEEP_DAYS)))
    return n


def snaps(ctx, emp, month):
    """[{id, ts, total, rows, facts}] по времени. В демо — вечер каждого из прошлых KEEP_DAYS дней, посчитанный заново."""
    if not ctx.demo:
        return [dict(r, rows=json.loads(r["rows"]), facts=json.loads(r["facts"] or "{}"))
                for r in ctx.db.q("SELECT * FROM pay_hist WHERE emp_id = ? AND month = ? ORDER BY id", emp["id"], month)]
    if month != month_of(ctx.today):
        return []
    out = []
    for back in range(KEEP_DAYS, 0, -1):
        d = ctx.today - timedelta(days=back)
        if d < date.fromisoformat(f"{month}-01"):
            continue
        out.append({"id": back, "ts": f"{d.isoformat()} 19:00", "day": d})
    return out


def _demo_calc(ctx, emp, s):
    """Демо: расчёт «каким он был» вечером того дня. Отчёты 1С за день приходят ночью, поэтому в нём факты по день раньше."""
    d = s["day"]
    k = Kpi(ctx.cfg, ctx.db, ctx.month, d, ctx.app.hooks, upto=d - timedelta(days=1))
    total, rows, facts = pack(Payroll(ctx.cfg, ctx.db, k, ctx.app.hooks).calc(emp["id"]), k)
    return dict(s, total=total, rows=rows, facts=facts)


def pick(lst, sid, now):
    """Выбранный снимок; по умолчанию — примерно сутки назад (последний не позже), иначе самый ранний."""
    if sid:
        x = next((s for s in lst if str(s["id"]) == str(sid)), None)
        if x:
            return x
    day_ago = now_s(now - timedelta(hours=24))
    old = [s for s in lst if s["ts"] <= day_ago]
    return old[-1] if old else (lst[0] if lst else None)


def diff(old_rows, new_rows):
    """[(строка, было, стало)] — только изменившиеся, крупные сверху."""
    a = {n: v for n, v in old_rows}
    b = {n: v for n, v in new_rows}
    keys = list(dict.fromkeys(list(a) + list(b)))
    out = [(k, a.get(k, 0), b.get(k, 0)) for k in keys if round(a.get(k, 0)) != round(b.get(k, 0))]
    return sorted(out, key=lambda x: -abs(x[2] - x[1]))


def fact_changes(old, new):
    out = []
    for code, title, unit in FACTS:
        if code in old or code in new:
            a, b = old.get(code, 0), new.get(code, 0)
            if a != b:
                out.append((title, a, b, unit))
    return out


def events(ctx, emp, s):
    """Что пришло за это время: отчёты 1С, табель и правки по человеку. В демо — по датам фактов после того вечера."""
    db, out = ctx.db, []
    if ctx.demo:
        d = (s["day"] - timedelta(days=1)).isoformat()
        for r in db.timesheet(s["day"], ctx.today, emp["id"]):
            out.append(f"{r['day'][8:10]}.{r['day'][5:7]} — табель: {TS_KIND.get(r['kind'], r['kind'])}"
                       + (f" {r['minutes']} мин" if r["kind"] == "late" else "") + (f" ({r['who']})" if r["who"] else ""))
        ds = [r["day"] for r in db.q("SELECT DISTINCT day FROM sales WHERE day > ? AND day <= ? ORDER BY day", d, ctx.today)]
        if ds:
            out.insert(0, "1С: Продажи за " + ", ".join(f"{x[8:10]}.{x[5:7]}" for x in ds))
        return out
    since = s["ts"].replace(" ", "T")
    rep = {}
    for r in db.q("SELECT kind, ts FROM batches WHERE ts >= ? ORDER BY ts", since):
        t = KIND_TITLE.get(r["kind"], r["kind"])
        c = rep.setdefault(t, [0, ""])
        c[0] += 1
        c[1] = r["ts"]
    for t, (c, last) in rep.items():
        out.append(f"1С: {t} — " + (f"{c} раз, последний {_when(ctx, last)}" if c > 1 else _when(ctx, last)))
    for r in db.q("SELECT * FROM timesheet WHERE emp_id = ? AND ts >= ? ORDER BY ts", emp["id"], since):
        out.append(f"{_when(ctx, r['ts'])} — табель {r['day'][8:10]}.{r['day'][5:7]}: {TS_KIND.get(r['kind'], r['kind'])}"
                   + (f" {r['minutes']} мин" if r["kind"] == "late" else "") + (f" ({r['who']})" if r["who"] else ""))
    for r in db.q("SELECT * FROM edits WHERE emp_id = ? AND (ts >= ? OR decided_ts >= ?) ORDER BY ts", emp["id"], since, since):
        st = {"ok": "принята", "no": "отклонена", "wait": "ждёт решения"}.get(r["status"], r["status"])
        out.append(f"{_when(ctx, r['decided_ts'] or r['ts'])} — {'премия' if r['amount'] > 0 else 'удержание'} "
                   f"{r['amount']:+,.0f}".replace(",", " ") + f": {r['reason']} — {st}")
    return out[:20]


def _when(ctx, ts):
    return tlabel(ctx, str(ts).replace("T", " ")[:16])


def tlabel(ctx, ts):
    d = ts[:10]
    day = ("сегодня" if d == ctx.today.isoformat() else "вчера" if d == (ctx.today - timedelta(days=1)).isoformat()
           else f"{ts[8:10]}.{ts[5:7]}")
    return f"{day} {ts[11:16]}".strip()


def compare(ctx, emp, sid=None, now=None):
    """Данные экрана: (снимки, выбранный, итог сейчас, строки сейчас, цифры сейчас)."""
    now = now or datetime.now()
    if ctx.demo:
        now = datetime.combine(ctx.today, now.time())
    lst = snaps(ctx, emp, ctx.month)
    s = pick(lst, sid, now)
    if s and ctx.demo:
        s = _demo_calc(ctx, emp, s)
    k = ctx.kpi()
    total, rows, facts = pack(ctx.pay().calc(emp["id"]), k)
    return lst, s, total, rows, facts
