"""Модуль «Ночной сторож»: смотрит цифры и поднимает тревогу, если что-то не так.

Проверки:
  quiet      торговый вчера ничего не продал, хотя обычно продаёт каждый день
  lost       крупный клиент (из верхних 20% прошлого месяца) в этом месяце не покупает 14+ дней
  discount   средняя скидка торгового выросла на 3+ п.п. к прошлому месяцу
  returns    возвраты больше 5% продаж
  debt_grow  просрочка 60+ выросла на 20%+ между двумя последними срезами
  no_data    свежих продаж нет 2+ рабочих дня — возможно, не пришли отчёты
"""
from collections import defaultdict
from datetime import date, timedelta

from core.calendar import month_of, shift_month
from core.data.db import month_bounds
from core.jobs.schedule import job
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Card, View

M = "watchdog"
ICON = {"quiet": "😶", "lost": "🚪", "discount": "🏷", "returns": "↩️", "debt_grow": "📈", "no_data": "📭"}

menu("watch", "🚨 Ночной сторож", {"head", "owner", "admin"}, module=M, order=55,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def check(ctx):
    """[(вид, сотрудник или None, текст)]."""
    ctx.month = month_of(ctx.today)
    k = ctx.kpi()
    db = ctx.db
    out = []
    names = {e["id"]: e["fio"] for e in k.employees.values()}
    last = db.one("SELECT MAX(day) AS d FROM sales")
    last_day = date.fromisoformat(last["d"]) if last and last["d"] else None
    wd = [d for d in k.cal.workdays if d < ctx.today]
    if last_day and len([d for d in wd if d > last_day]) >= 2:
        out.append(("no_data", None, f"Свежих продаж нет с {last_day:%d.%m} — проверьте, приходят ли отчёты"))
    yday = max(wd) if wd else None
    sales_dept = next(d.id for d in ctx.cfg.departments if d.kind == "sales")
    ids = [i for i in k.members("dept", sales_dept) if ctx.sees(i)]
    for i in ids:
        days = k.daily.get(i, {})
        active = sum(1 for d in wd if days.get(d.isoformat(), 0) > 0)
        if yday and len(wd) >= 5 and active / len(wd) >= 0.6 and days.get(yday.isoformat(), 0) <= 0:
            out.append(("quiet", i, f"{short(names[i])}: вчера ни одной продажи (обычно продаёт {active} из {len(wd)} дней)"))
        amounts = _rows(k, i)
        gross = sum(x for x in amounts if x > 0)
        back = -sum(x for x in amounts if x < 0)
        if gross and back / gross > 0.05:
            out.append(("returns", i, f"{short(names[i])}: возвраты {pct(back / gross)} от продаж"))
    prev = ctx.kpi(shift_month(ctx.month, -1))
    for i in ids:
        clients = prev.by_client.get(i, {})
        if not clients:
            continue
        top = sorted(clients.items(), key=lambda t: -t[1])[:max(1, len(clients) // 5)]
        now = k.by_client.get(i, {})
        if (ctx.today - k.cal.start).days >= 14:
            for c, v in top:
                if now.get(c, 0) <= 0:
                    out.append(("lost", i, f"{short(names[i])}: {ctx.db.client_name(c)} не покупает с прошлого месяца "
                                           f"(было {money(v)})"))
        d_now = _discount(ctx, i, ctx.month)
        d_prev = _discount(ctx, i, prev.month)
        if d_now is not None and d_prev is not None and d_now - d_prev >= 0.03:
            out.append(("discount", i, f"{short(names[i])}: средняя скидка {pct(d_now, 1)} — было {pct(d_prev, 1)}"))
    snaps = [r["on"] for r in db.q('SELECT DISTINCT "on" FROM debts ORDER BY "on" DESC LIMIT 2')]
    if len(snaps) == 2:
        a = defaultdict(float)
        b = defaultdict(float)
        for r in db.q('SELECT * FROM debts WHERE "on" IN (?, ?)', *snaps):
            emp = k.who(r["emp_key"], r["on"])
            (a if r["on"] == snaps[0] else b)[emp] += r["d61_90"] + r["d91"]
        for i in ids:
            if b.get(i, 0) > 10000 and a.get(i, 0) / b[i] >= 1.2:
                out.append(("debt_grow", i, f"{short(names[i])}: просрочка 60+ выросла до {money(a[i])} (было {money(b[i])})"))
    return out


def _rows(k, emp):
    return [r[0] for r in k.db.c.execute(
        "SELECT amount FROM sales WHERE day BETWEEN ? AND ? AND emp_key IN (SELECT key FROM emp_keys WHERE emp_id = ?)",
        (k.start, k.end, emp))]


def _discount(ctx, emp, month):
    s, e = month_bounds(month)
    r = ctx.db.one("SELECT SUM(amount * discount) / NULLIF(SUM(amount), 0) AS d FROM sales WHERE amount > 0 AND "
                   "day BETWEEN ? AND ? AND emp_key IN (SELECT key FROM emp_keys WHERE emp_id = ?)", s, e, emp)
    return r["d"] if r else None


@screen("watch", module=M)
def watch(ctx):
    found = check(ctx)
    v = View("🚨 Ночной сторож", f"нашёл: {len(found)}")
    v.cards([Card(ICON[kind] + " " + text.split(":")[0], "", [text.split(":", 1)[1].strip() if ":" in text else text],
                  color="bad" if kind in ("lost", "debt_grow", "no_data") else "warn") for kind, _, text in found[:20]])
    if not found:
        v.note("Всё спокойно 👍")
    kb = [[Button(short(ctx.kpi().employees[i]["fio"]), f"emp:{i}") for _, i, _ in found[j:j + 3] if i]
          for j in range(0, min(len(found), 9), 3)]
    return Reply(view=v, buttons=[r for r in kb if r])


@job("watchdog", "morning", {"head", "owner", "admin"}, "Утром: что нашёл сторож", module=M)
def morning(ctx):
    if ctx.role == "head" and ctx.dept_kind != "sales":
        return None
    return watch(ctx) if check(ctx) else None
