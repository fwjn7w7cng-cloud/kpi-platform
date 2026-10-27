"""Модуль «График смен и заявки на отпуск»: сотрудник просит отпуск или отгул кнопками →
руководитель отдела (или руководитель компании) решает → кто отсутствует на этой и следующей неделе.
"""
from datetime import date, timedelta

from core.calendar import day_title
from core.ui.kit import Button, Reply, rows
from core.ui.registry import menu, screen
from core.ui.screens import short
from core.ui.views import Row, View

M = "shifts"
KINDS = {"vacation": "🌴 Отпуск", "dayoff": "🕐 Отгул", "sick": "🤒 Больничный"}
ST = {"wait": "⏳ ждёт", "ok": "✅ да", "no": "❌ нет"}

menu("lv", "🌴 Отпуск и отгулы", {"rep", "newbie", "sv", "staff", "head", "accountant"}, module=M, order=70,
     when=lambda c: c.me is not None)
menu("lvi", "🌴 Заявки на отпуск", {"head", "owner", "admin"}, module=M, order=71)


@screen("lv", module=M)
def leave(ctx, kind=None, start=None, days=None):
    if not ctx.me:
        return Reply(text="Заявку подаёт сотрудник.")
    if kind and start and days:
        s = date.fromisoformat(start)
        e = s + timedelta(days=int(days) - 1)
        lid = ctx.db.x("INSERT INTO leave (emp_id, kind, start, end, status, ts) VALUES (?,?,?,?,?,?)",
                       ctx.me["id"], kind, s, e, "wait", ctx.today)
        out = [Reply(text=f"📨 Заявка отправлена: {KINDS[kind]} {day_title(s)}–{day_title(e)}")]
        for h in _deciders(ctx, ctx.me):
            if h["tg_id"]:
                out.append(Reply(text=f"🌴 {ctx.me['fio']}: {KINDS[kind]} {day_title(s)}–{day_title(e)}", to=h["tg_id"],
                                 buttons=[[Button("✅ Да", f"lvd:{lid}:1"), Button("❌ Нет", f"lvd:{lid}:0")]]))
        out.append(leave(ctx))
        return out
    if kind and start:
        return Reply(text="Сколько дней?", buttons=[[Button(str(n), f"lv:{kind}:{start}:{n}") for n in (1, 2, 3, 7, 14)]])
    if kind:
        ds = [ctx.today + timedelta(days=i) for i in range(1, 22)]
        return Reply(text="С какого дня?", buttons=rows([Button(day_title(d), f"lv:{kind}:{d.isoformat()}") for d in ds], 4))
    mine = ctx.db.q("SELECT * FROM leave WHERE emp_id = ? ORDER BY start DESC LIMIT 10", ctx.me["id"])
    v = View("🌴 Мои заявки", ctx.me["fio"])
    v.table(["Что", "Когда", "Решение"], [Row([KINDS.get(r["kind"], r["kind"]), _span(r), ST[r["status"]]]) for r in mine])
    if not mine:
        v.note("Заявок пока нет.")
    return Reply(view=v, buttons=[[Button(t, f"lv:{k}") for k, t in KINDS.items()]])


def _deciders(ctx, emp):
    heads = [e for e in ctx.db.employees() if e["role"] == "head" and e["dept"] == emp["dept"] and e["id"] != emp["id"]]
    return heads or [e for e in ctx.db.employees() if e["role"] == "owner"]


@screen("lvi", module=M)
def inbox(ctx):
    rs = [r for r in ctx.db.q("SELECT * FROM leave WHERE status = 'wait' ORDER BY start") if ctx.sees(r["emp_id"])]
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    v = View("🌴 Заявки на отпуск", f"ждут решения: {len(rs)}")
    v.table(["Сотрудник", "Что", "Когда"], [Row([short(names[r["emp_id"]]), KINDS.get(r["kind"], ""), _span(r)]) for r in rs])
    off = ctx.db.q("SELECT * FROM leave WHERE status = 'ok' AND end >= ? AND start <= ? ORDER BY start",
                   ctx.today, ctx.today + timedelta(days=14))
    v.table(["Отсутствуют (2 недели)", "Что", "Когда"], [Row([short(names[r["emp_id"]]), KINDS.get(r["kind"], ""), _span(r)])
                                                         for r in off if ctx.sees(r["emp_id"])])
    if not rs:
        v.note("Новых заявок нет.")
    kb = [[Button(f"✅ {short(names[r['emp_id']])}", f"lvd:{r['id']}:1"), Button("❌", f"lvd:{r['id']}:0")] for r in rs[:8]]
    return Reply(view=v, buttons=kb)


@screen("lvd", module=M)
def decide(ctx, lid, ok):
    r = ctx.db.one("SELECT * FROM leave WHERE id = ?", int(lid))
    if not r or not ctx.sees(r["emp_id"]) or ctx.scope == "self":
        return Reply(text="Нет доступа.")
    if r["status"] != "wait":
        return Reply(text="По этой заявке уже решили.")
    ctx.db.x("UPDATE leave SET status = ?, decided_by = ? WHERE id = ?", "ok" if ok == "1" else "no", ctx.author(), int(lid))
    emp = ctx.db.employee(r["emp_id"])
    out = [Reply(text=f"{'✅ Согласовано' if ok == '1' else '❌ Отказано'}: {emp['fio']} {_span(r)}")]
    if emp["tg_id"]:
        out.append(Reply(text=f"Ваша заявка {KINDS.get(r['kind'], '')} {_span(r)}: {'✅ согласована' if ok == '1' else '❌ отклонена'}",
                         to=emp["tg_id"]))
    out.append(inbox(ctx))
    return out


def _span(r):
    s, e = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
    return day_title(s) if s == e else f"{day_title(s)}–{day_title(e)}"
