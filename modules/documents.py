"""Модуль «Сроки документов»: медкнижки, права, договоры — у кого что и до какого числа.
Утром руководителю — что истекает в ближайшие 30 дней или уже истекло.
"""
from datetime import date

from core.calendar import day_title
from core.jobs.schedule import job
from core.ui.kit import Button, Reply, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, Row, View

M = "documents"
SOON = 30

menu("docs", "📄 Документы", {"staff", "sv", "head", "owner", "admin", "accountant"}, module=M, order=75)


def _left(d, today):
    return (date.fromisoformat(d) - today).days


def _tone(n):
    return "bad" if n < 0 else "warn" if n <= SOON else "good"


@screen("docs", module=M)
def docs(ctx):
    rs = [r for r in ctx.db.q("SELECT * FROM documents ORDER BY until") if ctx.sees(r["emp_id"])]
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    v = View("📄 Сроки документов", f"красное — истёк, жёлтое — меньше {SOON} дней")
    v.cards([Card(names.get(r["emp_id"], "?"), day_title(date.fromisoformat(r["until"])),
                  [r["title"], _left_text(_left(r["until"], ctx.today))], color=_tone(_left(r["until"], ctx.today)))
             for r in rs if _left(r["until"], ctx.today) <= 90 or ctx.scope == "self"])
    if not rs:
        v.note("Документов пока нет.")
    kb = []
    if ctx.scope != "self":
        kb.append([Button("➕ Добавить документ", "doca")])
    return Reply(view=v, buttons=kb)


def _left_text(n):
    if n < 0:
        return f"истёк {-n} дн. назад"
    return "истекает сегодня" if n == 0 else f"осталось {n} дн."


@screen("doca", module=M)
def doc_add(ctx, emp_id=None):
    if ctx.scope == "self":
        return Reply(text="Документы вносит руководитель.")
    if not emp_id:
        es = [e for e in ctx.db.employees() if ctx.sees(e)]
        return Reply(text="Чей документ?", buttons=rows([Button(short(e["fio"]), f"doca:{e['id']}") for e in es], 3))
    return ctx.ask("doc", "Напишите название и срок: «Медкнижка 15.03.2027»", {"emp": int(emp_id)})


@text_input("doc")
def on_doc(ctx, text, data):
    parts = text.strip().rsplit(maxsplit=1)
    try:
        d, m, y = parts[1].split(".")
        until = date(int(y), int(m), int(d))
    except (ValueError, IndexError):
        return ctx.ask("doc", "Не понял дату. Пример: «Медкнижка 15.03.2027»", data)
    ctx.db.x("INSERT INTO documents (emp_id, title, until) VALUES (?,?,?)", data["emp"], parts[0][:80], until)
    return docs(ctx)


@job("docs_soon", "morning", {"head", "owner", "admin"}, "Утром: истекающие документы", module=M)
def soon(ctx):
    rs = [r for r in ctx.db.q("SELECT * FROM documents ORDER BY until") if ctx.sees(r["emp_id"])
          and _left(r["until"], ctx.today) in (-1, 0, 7, 14, 30)]
    if not rs:
        return None
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    v = View("📄 Документы: пора продлить")
    v.table(["Сотрудник", "Документ", "Срок"], [Row([short(names[r["emp_id"]]), r["title"],
                                                   _left_text(_left(r["until"], ctx.today))]) for r in rs])
    return Reply(view=v, buttons=[[Button("📄 Все документы", "docs")]])
