"""Модуль «Задачи от руководителя»: руководитель ставит задачу своим людям → сотрудник отмечает
«✅ Сделано» или «❌ Не смог» с пояснением → руководитель видит, что сделано.
Напоминание утром — у кого задачи на сегодня или просрочены.
"""
from datetime import date, timedelta

from core.calendar import day_title
from core.jobs.schedule import job
from core.ui.kit import Button, Reply, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, View

M = "tasks"
LEADS = {"sv", "head", "owner", "admin"}

menu("tasks", "📌 Задачи", {"rep", "newbie", "sv", "staff", "head", "owner", "admin", "accountant"}, module=M, order=50)


def _card(t, names, today):
    due = date.fromisoformat(t["due"])
    late = t["status"] == "open" and due < today
    st = {"open": "просрочена" if late else f"до {day_title(due)}", "done": "✅ сделано", "fail": "❌ не смог"}[t["status"]]
    lines = [t["text"]] + ([t["note"]] if t["note"] else [])
    return Card(names.get(t["emp_id"], "?"), st, lines, color="bad" if late or t["status"] == "fail" else
                "good" if t["status"] == "done" else None)


@screen("tasks", module=M)
def tasks(ctx, show="open"):
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    mine = ctx.me and ctx.role not in LEADS
    if mine:
        rs = ctx.db.q("SELECT * FROM tasks WHERE emp_id = ? ORDER BY status = 'open' DESC, due", ctx.me["id"])
    else:
        author = ctx.me["id"] if ctx.me else 0
        rs = [t for t in ctx.db.q("SELECT * FROM tasks ORDER BY status = 'open' DESC, due")
              if ctx.sees(t["emp_id"]) or t["author_id"] == author]
    if show == "open":
        rs = [t for t in rs if t["status"] == "open"]
    v = View("📌 Задачи", "мои" if mine else ("открытые" if show == "open" else "все"))
    v.cards([_card(t, names, ctx.today) for t in rs[:20]])
    if not rs:
        v.note("Задач нет.")
    kb = []
    if mine:
        kb += [[Button(f"✅ {t['text'][:22]}", f"tkd:{t['id']}:1"), Button("❌", f"tkd:{t['id']}:0")]
               for t in rs if t["status"] == "open"][:6]
    else:
        kb.append([Button("➕ Новая задача", "tka"), Button("Все" if show == "open" else "Открытые",
                                                          "tasks:all" if show == "open" else "tasks")])
    return Reply(view=v, buttons=kb)


@screen("tka", module=M)
def task_add(ctx, emp_id=None, due=None):
    if ctx.role not in LEADS:
        return Reply(text="Задачи ставит руководитель.")
    if not emp_id:
        es = [e for e in ctx.db.employees() if ctx.sees(e) and (not ctx.me or e["id"] != ctx.me["id"])]
        return Reply(text="Кому задача?", buttons=rows([Button(short(e["fio"]), f"tka:{e['id']}") for e in es], 3))
    if not due:
        opts = [("Сегодня", 0), ("Завтра", 1), ("За 3 дня", 3), ("За неделю", 7)]
        return Reply(text="Срок?", buttons=[[Button(t, f"tka:{emp_id}:{(ctx.today + timedelta(days=d)).isoformat()}") for t, d in opts]])
    return ctx.ask("task", "Напишите задачу одним сообщением.", {"emp": int(emp_id), "due": due})


@text_input("task")
def on_task(ctx, text, data):
    tid = ctx.db.x("INSERT INTO tasks (author_id, emp_id, text, due, ts) VALUES (?,?,?,?,?)",
                   ctx.me["id"] if ctx.me else 0, data["emp"], text.strip()[:500], data["due"], ctx.today)
    emp = ctx.db.employee(data["emp"])
    out = [Reply(text=f"📌 Задача поставлена: {emp['fio']}")]
    if emp["tg_id"]:
        out.append(Reply(text=f"📌 Новая задача до {day_title(date.fromisoformat(data['due']))}:\n{text.strip()}\nОт: {ctx.author()}",
                         to=emp["tg_id"], buttons=[[Button("✅ Сделано", f"tkd:{tid}:1"), Button("❌ Не смог", f"tkd:{tid}:0")]]))
    out.append(tasks(ctx))
    return out


@screen("tkd", module=M)
def task_done(ctx, tid, ok):
    t = ctx.db.one("SELECT * FROM tasks WHERE id = ?", int(tid))
    if not t or not ctx.me or t["emp_id"] != ctx.me["id"]:
        return Reply(text="Это не ваша задача.")
    if t["status"] != "open":
        return Reply(text="Задача уже закрыта.")
    if ok == "0":
        return ctx.ask("task_fail", "Почему не получилось? Пара слов для руководителя.", {"id": t["id"]})
    return _close(ctx, t, "done", "")


@text_input("task_fail")
def on_fail(ctx, text, data):
    t = ctx.db.one("SELECT * FROM tasks WHERE id = ?", data["id"])
    return _close(ctx, t, "fail", text.strip()[:300])


def _close(ctx, t, status, note):
    ctx.db.x("UPDATE tasks SET status = ?, note = ?, done_ts = ? WHERE id = ?", status, note, ctx.today, t["id"])
    out = [Reply(text="✅ Отмечено." if status == "done" else "Отмечено, руководитель увидит пояснение.")]
    author = ctx.db.employee(t["author_id"])
    if author and author["tg_id"]:
        out.append(Reply(text=f"📌 {ctx.me['fio']}: {'✅ сделал' if status == 'done' else '❌ не смог'} — {t['text']}"
                              + (f"\n{note}" if note else ""), to=author["tg_id"]))
    out.append(tasks(ctx))
    return out


@job("tasks_today", "morning", {"rep", "newbie", "sv", "staff", "head", "accountant"}, "Утром: задачи на сегодня", module=M)
def today_tasks(ctx):
    if not ctx.me:
        return None
    rs = ctx.db.q("SELECT * FROM tasks WHERE emp_id = ? AND status = 'open' AND due <= ?", ctx.me["id"], ctx.today)
    if not rs:
        return None
    return Reply(text="📌 Задачи на сегодня:\n" + "\n".join(f"• {t['text']}" for t in rs), buttons=[[Button("📌 Задачи", "tasks")]])


# ---------- единицы для зарплаты: задачи месяца ----------

def _tasks(pay, emp, good):
    s, e = str(pay.kpi.start), str(pay.kpi.end)
    n = 0
    for t in pay.db.q("SELECT * FROM tasks WHERE emp_id = ? AND due BETWEEN ? AND ?", emp["id"], s, e):
        on_time = t["status"] == "done" and (t["done_ts"] or "")[:10] <= t["due"]
        late = t["status"] == "fail" or (t["status"] == "done" and not on_time) or \
            (t["status"] == "open" and t["due"] < min(pay.cal.today, pay.cal.end).isoformat())
        n += on_time if good else late
    return n


from core.pay import components as _C  # noqa: E402

_C.unit("tasks_done", lambda pay, emp: _tasks(pay, emp, True))
_C.unit("tasks_late", lambda pay, emp: _tasks(pay, emp, False))
