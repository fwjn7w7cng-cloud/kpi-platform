"""«Не работает» и предложения — есть у всех, в любой редакции (это поддержка, а не модуль на продажу).

Сотрудник пишет текст (можно скрин с подписью) → администратору сразу приходит сообщение с кнопками
«✍️ Ответить» и «✅ Сделано» → автору приходит ответ. У администратора — «💬 Отзывы»: новые и закрытые.
"""
from core.ui.kit import Button, Reply, rows
from core.ui.registry import menu, screen, text_input
from core.ui.views import Card, View

KINDS = {"bug": "🐞 Не работает", "idea": "💡 Предложение", "note": "💬 Отзыв"}
ST = {"new": "новое", "answered": "отвечено", "done": "✅ сделано"}
LEADS = {"sv", "head", "owner", "accountant"}

menu("fb", "🐞 Не работает", {"rep", "newbie", "staff"}, order=96)
menu("fb", "💬 Предложение / отзыв", LEADS, order=96)
menu("fbl", "💬 Отзывы", {"admin"}, order=96)


@screen("fb")
def feedback(ctx, kind=None):
    if ctx.role == "admin":
        return inbox(ctx)
    if not kind:
        if ctx.role not in LEADS:
            kind = "bug"
        else:
            return Reply(text="Что хотите написать?", buttons=[[Button(t, f"fb:{k}") for k, t in KINDS.items()]])
    prompt = {"bug": "Опишите, что не работает: какая кнопка, что ждали, что получилось. Можно прислать скрин с подписью.",
              "idea": "Напишите предложение — что добавить или сделать удобнее.",
              "note": "Напишите отзыв — что нравится, что мешает."}.get(kind, "Напишите сообщение.")
    return ctx.ask("fb_text", prompt, {"kind": kind if kind in KINDS else "bug"})


@text_input("fb_text")
def on_text(ctx, text, data):
    text = (text or "").strip()
    if not text and not ctx.photo:
        return ctx.ask("fb_text", "Пустое сообщение. Напишите пару слов.", data)
    kind = data.get("kind", "bug")
    fid = ctx.office.x("INSERT INTO feedback (user_id, emp_id, kind, text, photo, ts) VALUES (?,?,?,?,?,?)",
                   ctx.user_id, ctx.me["id"] if ctx.me else None, kind, text[:2000], ctx.photo or "", ctx.today)
    out = [Reply(text="✅ Отправлено администратору. Ответ придёт сюда." +
                      ("\n\nВ демо сообщение видно в роли «Администратор» → «💬 Отзывы»." if ctx.demo else ""))]
    for a in ctx.app.admins:
        out.append(Reply(text=f"{KINDS[kind]} · {ctx.name()}\n{text}"[:1000], photo=ctx.photo or "", to=a,
                         buttons=[[Button("✍️ Ответить", f"fba:{fid}"), Button("✅ Сделано", f"fbd:{fid}")]]))
    return out


def _card(ctx, r):
    e = ctx.db.employee(r["emp_id"]) if r["emp_id"] else None
    who = e["fio"] if e else f"id {r['user_id']}"
    lines = [r["text"] or "(скрин без подписи)"] + ([f"Ответ: {r['answer']}"] if r["answer"] else [])
    return Card(f"{KINDS.get(r['kind'], '')} · {who}", ST.get(r["status"], ""), lines,
                color="bad" if r["kind"] == "bug" and r["status"] == "new" else "good" if r["status"] == "done" else None,
                note=r["ts"][:10])


@screen("fbl")
def inbox(ctx, show="new"):
    if ctx.role != "admin":
        return feedback(ctx)
    q = "status != 'done'" if show == "new" else "status = 'done'"
    rs = ctx.office.q(f"SELECT * FROM feedback WHERE {q} ORDER BY id DESC LIMIT 20")
    v = View("💬 Отзывы и «не работает»", "новые и отвеченные" if show == "new" else "сделанные")
    v.cards([_card(ctx, r) for r in rs])
    if not rs:
        v.note("Пусто.")
    kb = rows([Button(f"{KINDS.get(r['kind'], '')[:2]} №{r['id']}", f"fbo:{r['id']}") for r in rs[:12]], 4)
    kb.append([Button("✅ Сделанные" if show == "new" else "🆕 Новые", "fbl:done" if show == "new" else "fbl")])
    return Reply(view=v, buttons=kb)


@screen("fbo")
def open_one(ctx, fid):
    r = ctx.office.one("SELECT * FROM feedback WHERE id = ?", int(fid))
    if ctx.role != "admin" or not r:
        return Reply(text="Нет доступа.")
    v = View(f"№{r['id']}", ST.get(r["status"], ""))
    v.cards([_card(ctx, r)])
    out = []
    if r["photo"]:
        out.append(Reply(text="Скрин:", photo=r["photo"]))
    kb = [[Button("✍️ Ответить", f"fba:{r['id']}"), Button("✅ Сделано", f"fbd:{r['id']}")], [Button("‹ Отзывы", "fbl")]]
    out.append(Reply(view=v, buttons=kb))
    return out


@screen("fba")
def answer(ctx, fid):
    if ctx.role != "admin":
        return Reply(text="Нет доступа.")
    return ctx.ask("fb_answer", "Напишите ответ — он уйдёт автору.", {"id": int(fid)})


@text_input("fb_answer")
def on_answer(ctx, text, data):
    r = ctx.office.one("SELECT * FROM feedback WHERE id = ?", data["id"])
    if not r:
        return Reply(text="Сообщение не найдено.")
    ctx.office.x("UPDATE feedback SET answer = ?, status = CASE status WHEN 'done' THEN 'done' ELSE 'answered' END WHERE id = ?",
             text.strip()[:1000], r["id"])
    return [Reply(text=f"💬 Ответ администратора:\n{text.strip()}", to=r["user_id"],
                  buttons=[[Button("🐞 Написать ещё", "fb")]]), inbox(ctx)]


@screen("fbd")
def done(ctx, fid):
    r = ctx.office.one("SELECT * FROM feedback WHERE id = ?", int(fid))
    if ctx.role != "admin" or not r:
        return Reply(text="Нет доступа.")
    ctx.office.x("UPDATE feedback SET status = 'done' WHERE id = ?", r["id"])
    what = (r["text"] or "ваше сообщение")[:120]
    return [Reply(text=f"✅ Сделано: {what}", to=r["user_id"]), inbox(ctx)]
