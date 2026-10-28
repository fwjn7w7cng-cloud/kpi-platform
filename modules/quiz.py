"""Модуль «Тесты и обучение новичков».

Вопросы: из файла клиента clients/<код>/quiz.yaml (questions) и добавленные в боте руководителем.
Уроки для новичка: там же (lessons: заголовок, текст, вопросы); следующий урок открывается, когда
пройден предыдущий (порог quiz.pass в анкете, по умолчанию 80%).

Тест недели: count случайных вопросов (по умолчанию 10) раз в неделю — в quiz.day / quiz.time из анкеты
(по умолчанию воскресенье 10:00) бот присылает торговым приглашение; руководители видят результаты.

Формат вопроса в боте (руководитель → «➕ Вопрос»):
    Какой порог АКБ?
    + 5 000 ₽
    - 1 000 ₽
    - 10 000 ₽
"""
import json
import random
from datetime import datetime
from pathlib import Path

import yaml

from core.jobs.schedule import job
from core.ui.kit import Button, Reply, pct, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "quiz"
TAKERS = {"rep", "newbie", "sv"}
LEADS = {"head", "owner", "admin"}
ROOT = Path(__file__).resolve().parent.parent

menu("qz", "📝 Тесты", {"rep", "sv"} | LEADS, module=M, order=40,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")
menu("qz", "🎓 Обучение", {"newbie"}, module=M, order=9)


# ---------- вопросы ----------

def _file(cfg):
    p = ROOT / "clients" / cfg.id / "quiz.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def settings(cfg):
    q = cfg.raw.get("quiz") or {}
    pas = str(q.get("pass", "80%")).replace("%", "").replace(",", ".")
    return {"day": q.get("day", "sun"), "time": str(q.get("time", "10:00")), "count": int(q.get("count", 10)),
            "pass": float(pas) / 100 if float(pas) > 1 else float(pas)}


def bank(ctx):
    """{id: {text, options: [правильный, …], topic}} — из файла клиента и из бота."""
    out = {}
    for n, q in enumerate(_file(ctx.cfg).get("questions") or []):
        out[f"y{n}"] = {"text": q["q"], "options": [str(x) for x in q["a"]], "topic": q.get("topic", "")}
    for r in ctx.db.q("SELECT * FROM quiz_questions WHERE active = 1"):
        out[f"d{r['id']}"] = {"text": r["text"], "options": json.loads(r["options"]), "topic": r["topic"]}
    return out


def lessons(ctx):
    out = []
    for n, les in enumerate(_file(ctx.cfg).get("lessons") or []):
        qs = {f"l{n}.{i}": {"text": q["q"], "options": [str(x) for x in q["a"]], "topic": les.get("title", "")}
              for i, q in enumerate(les.get("questions") or [])}
        out.append({"title": les.get("title", f"Урок {n + 1}"), "text": les.get("text", ""), "qs": qs})
    return out


def week_ref(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def best(ctx, emp_id, kind, ref):
    return ctx.db.one("SELECT * FROM quiz_results WHERE emp_id = ? AND kind = ? AND ref = ? ORDER BY score * 1.0 / total DESC "
                      "LIMIT 1", emp_id, kind, ref)


def lesson_open(ctx, emp_id, n):
    if n == 0:
        return True
    r = best(ctx, emp_id, "lesson", str(n - 1))
    return bool(r and r["score"] / r["total"] >= settings(ctx.cfg)["pass"] - 1e-9)


# ---------- экраны ----------

@screen("qz", module=M)
def hub(ctx):
    week = week_ref(ctx.today)
    s = settings(ctx.cfg)
    if ctx.role in LEADS:
        return results(ctx)
    if not ctx.me:
        return Reply(text="Тесты проходят сотрудники.")
    v = View("🎓 Обучение" if ctx.role == "newbie" else "📝 Тесты", ctx.me["fio"])
    kb = []
    les = lessons(ctx)
    if ctx.role == "newbie" and les:
        cards = []
        for n, le in enumerate(les):
            r = best(ctx, ctx.me["id"], "lesson", str(n))
            ok = r and r["score"] / r["total"] >= s["pass"] - 1e-9
            st = "✅ пройден" if ok else ("открыт" if lesson_open(ctx, ctx.me["id"], n) else "🔒 после предыдущего")
            cards.append(Card(f"{n + 1}. {le['title']}", st, [f"лучший результат {r['score']} из {r['total']}"] if r else [],
                              color="good" if ok else None))
            if lesson_open(ctx, ctx.me["id"], n):
                kb.append([Button(f"{'✅' if ok else '📖'} {n + 1}. {le['title'][:30]}", f"qzl:{n}")])
        done = sum(1 for n in range(len(les)) if lesson_open(ctx, ctx.me["id"], n + 1))
        v.tiles([("Уроков пройдено", f"{done} из {len(les)}", "good" if done == len(les) else None)])
        v.cards(cards, title="Курс новичка")
    r = best(ctx, ctx.me["id"], "test", week)
    if ctx.role != "newbie" or not les:
        b = bank(ctx)
        v.tiles([("Тест недели", f"{r['score']} из {r['total']}" if r else "не пройден",
                  ("good" if r["score"] / r["total"] >= s["pass"] else "warn") if r else "bad"),
                 ("Вопросов в банке", str(len(b)), None)])
        if b:
            kb.append([Button("🔁 Пройти ещё раз" if r else "📝 Пройти тест недели", f"qzs:test:{week}")])
        hist = ctx.db.q("SELECT ref, MAX(score * 1.0 / total) AS p FROM quiz_results WHERE emp_id = ? AND kind = 'test' "
                        "GROUP BY ref ORDER BY ref DESC LIMIT 6", ctx.me["id"])
        if hist:
            v.table(["Неделя", "Результат"], [Row([h["ref"], Cell(pct(h["p"]), "good" if h["p"] >= s["pass"] else "warn")])
                                              for h in hist], title="Мои тесты")
    return Reply(view=v, buttons=kb)


@screen("qzl", module=M)
def lesson(ctx, n):
    les = lessons(ctx)
    n = int(n)
    if not ctx.me or n >= len(les) or not lesson_open(ctx, ctx.me["id"], n):
        return Reply(text="Урок пока закрыт.")
    le = les[n]
    v = View(f"📖 {n + 1}. {le['title']}", f"урок {n + 1} из {len(les)}")
    v.lines([x.strip() for x in le["text"].strip().split("\n") if x.strip()])
    kb = [[Button(f"📝 Вопросы по уроку ({len(le['qs'])})", f"qzs:lesson:{n}")]] if le["qs"] else []
    kb.append([Button("‹ Обучение", "qz")])
    return Reply(view=v, buttons=kb)


@screen("qzs", module=M)
def start(ctx, kind, ref):
    if not ctx.me:
        return Reply(text="Тесты проходят сотрудники.")
    if kind == "lesson":
        les = lessons(ctx)
        if int(ref) >= len(les) or not lesson_open(ctx, ctx.me["id"], int(ref)):
            return Reply(text="Урок пока закрыт.")
        qs = les[int(ref)]["qs"]
        ids = list(qs)
    else:
        qs = bank(ctx)
        R = random.Random(f"{ctx.me['id']}:{ref}:{datetime.now().isoformat()}")
        ids = R.sample(list(qs), min(settings(ctx.cfg)["count"], len(qs)))
    if not ids:
        return Reply(text="Вопросов пока нет.")
    R = random.Random()
    order = {}
    for q in ids:
        o = list(range(len(qs[q]["options"])))
        R.shuffle(o)
        order[q] = o
    ctx.session["quiz"] = {"kind": kind, "ref": str(ref), "ids": ids, "i": 0, "score": 0, "wrong": [], "order": order}
    ctx.save()
    return _ask(ctx)


def _qs(ctx, st):
    if st["kind"] == "lesson":
        return lessons(ctx)[int(st["ref"])]["qs"]
    return bank(ctx)


def _ask(ctx, pre=None):
    st = ctx.session.get("quiz")
    qs = _qs(ctx, st)
    qid = st["ids"][st["i"]]
    q = qs.get(qid)
    if q is None:                        # вопрос удалили по ходу теста
        st["i"] += 1
        return _next(ctx, pre)
    head = f"Вопрос {st['i'] + 1} из {len(st['ids'])}"
    text = (pre + "\n\n" if pre else "") + f"{head}\n\n{q['text']}"
    kb = [[Button(q["options"][j][:60], f"qza:{j}")] for j in st["order"][qid]]
    return Reply(text=text, buttons=kb)


def _next(ctx, pre=None):
    st = ctx.session["quiz"]
    if st["i"] < len(st["ids"]):
        ctx.save()
        return _ask(ctx, pre)
    total = len(st["ids"])
    ctx.db.x("INSERT INTO quiz_results (emp_id, kind, ref, score, total, wrong, ts) VALUES (?,?,?,?,?,?,?)",
             ctx.me["id"], st["kind"], st["ref"], st["score"], total, json.dumps(st["wrong"], ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds"))
    ctx.session.pop("quiz", None)
    ctx.save()
    p = st["score"] / total if total else 0
    ok = p >= settings(ctx.cfg)["pass"] - 1e-9
    v = View("🎉 Готово" if ok else "📝 Результат", f"{st['score']} из {total} · {pct(p)}")
    lines = [("✅ Пройдено!" if ok else f"Нужно от {pct(settings(ctx.cfg)['pass'])} — попробуйте ещё раз.")]
    if st["kind"] == "lesson" and ok and int(st["ref"]) + 1 < len(lessons(ctx)):
        lines.append("Открыт следующий урок.")
    v.lines(lines)
    if st["wrong"]:
        v.cards([Card(w["q"], "", [f"Правильно: {w['a']}"], color="warn") for w in st["wrong"][:8]], title="Ошибки")
    out = [Reply(text=pre)] if pre else []
    out.append(Reply(view=v, buttons=[[Button("‹ Тесты" if st["kind"] == "test" else "‹ Обучение", "qz")]]))
    return out


@screen("qza", module=M)
def answer(ctx, j):
    st = ctx.session.get("quiz")
    if not st or not ctx.me:
        return hub(ctx)
    qs = _qs(ctx, st)
    qid = st["ids"][st["i"]]
    q = qs.get(qid)
    if q is None:
        st["i"] += 1
        return _next(ctx)
    j = int(j) if str(j).isdigit() else -1
    if j == 0:
        st["score"] += 1
        pre = "✅ Верно!"
    else:
        st["wrong"].append({"q": q["text"], "a": q["options"][0]})
        pre = f"❌ Неверно. Правильно: {q['options'][0]}"
    st["i"] += 1
    return _next(ctx, pre)


# ---------- руководителю ----------

@screen("qzr", module=M)
def results(ctx, ref=None):
    if ctx.role not in LEADS:
        return hub(ctx)
    ref = ref or week_ref(ctx.today)
    s = settings(ctx.cfg)
    v = View("📝 Тесты · результаты", f"неделя {ref} · порог {pct(s['pass'])}")
    rs, passed, people = [], 0, [e for e in ctx.db.employees() if e["role"] in TAKERS and ctx.sees(e)]
    for e in people:
        if e["role"] == "newbie":
            n = len(lessons(ctx))
            done = sum(1 for i in range(n) if lesson_open(ctx, e["id"], i + 1))
            rs.append(Row([short(e["fio"]), Cell(f"🎓 уроков {done} из {n}", "good" if done == n else "warn")]))
            continue
        r = best(ctx, e["id"], "test", ref)
        if r and r["score"] / r["total"] >= s["pass"]:
            passed += 1
        rs.append(Row([short(e["fio"]), Cell(f"{r['score']} из {r['total']}" if r else "не проходил",
                                            ("good" if r["score"] / r["total"] >= s["pass"] else "warn") if r else "bad")]))
    v.tiles([("Сдали", f"{passed} из {sum(1 for e in people if e['role'] != 'newbie')}", None),
             ("Вопросов в банке", str(len(bank(ctx))), None)])
    v.table(["Сотрудник", "Результат"], rs)
    hard = {}
    for r in ctx.db.q("SELECT wrong FROM quiz_results WHERE kind = 'test' AND ref = ?", ref):
        for w in json.loads(r["wrong"] or "[]"):
            hard[w["q"]] = hard.get(w["q"], 0) + 1
    if hard:
        v.lines([f"{n}× {q}" for q, n in sorted(hard.items(), key=lambda x: -x[1])[:5]], title="Чаще всего ошибаются")
    kb = [[Button("➕ Вопрос", "qzq"), Button("📚 Вопросы", "qzb")]]
    return Reply(view=v, buttons=kb)


@screen("qzb", module=M)
def bank_screen(ctx, page="0"):
    if ctx.role not in LEADS:
        return hub(ctx)
    b = bank(ctx)
    items = list(b.items())
    page = int(page)
    v = View("📚 Банк вопросов", f"всего {len(items)} · из файла и добавленные в боте")
    v.cards([Card(q["text"], "в боте" if qid.startswith("d") else "", [f"✅ {q['options'][0]}"] +
                  [f"· {x}" for x in q["options"][1:]]) for qid, q in items[page * 8:page * 8 + 8]])
    kb = [[Button(f"🗑 {q['text'][:24]}", f"qzx:{qid[1:]}")] for qid, q in items[page * 8:page * 8 + 8] if qid.startswith("d")]
    nav = []
    if page:
        nav.append(Button("◀", f"qzb:{page - 1}"))
    if (page + 1) * 8 < len(items):
        nav.append(Button("▶", f"qzb:{page + 1}"))
    kb += [nav] if nav else []
    kb.append([Button("➕ Вопрос", "qzq"), Button("‹ Результаты", "qzr")])
    return Reply(view=v, buttons=kb)


@screen("qzq", module=M)
def add(ctx):
    if ctx.role not in LEADS:
        return hub(ctx)
    return ctx.ask("quiz_q", "Напишите вопрос и ответы, каждый с новой строки:\n\nКакой порог АКБ?\n+ 5 000 ₽\n- 1 000 ₽\n- 10 000 ₽\n\n"
                             "«+» — правильный (один), «-» — неправильные (от одного).")


@text_input("quiz_q")
def on_add(ctx, text, data):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    q = [x for x in lines if not x.startswith(("+", "-", "—"))]
    good = [x[1:].strip() for x in lines if x.startswith("+")]
    bad = [x[1:].strip() for x in lines if x.startswith(("-", "—"))]
    if len(q) != 1 or len(good) != 1 or not bad:
        return ctx.ask("quiz_q", "Нужна одна строка вопроса, один ответ с «+» и хотя бы один с «-». Попробуйте ещё раз.")
    ctx.db.x("INSERT INTO quiz_questions (text, options, author, ts) VALUES (?,?,?,?)", q[0][:300],
             json.dumps(good + bad, ensure_ascii=False), ctx.author(), datetime.now().isoformat(timespec="seconds"))
    return [Reply(text="✅ Вопрос добавлен в банк."), bank_screen(ctx)]


@screen("qzx", module=M)
def remove(ctx, qid):
    if ctx.role not in LEADS:
        return hub(ctx)
    ctx.db.x("UPDATE quiz_questions SET active = 0 WHERE id = ?", int(qid))
    return bank_screen(ctx)


# ---------- рассылки ----------

@job("quiz_week", lambda app: settings(app.cfg)["time"], {"rep", "sv"}, "Тест недели — приглашение", module=M,
     workdays=False, days=lambda app: [settings(app.cfg)["day"]])
def invite(ctx):
    if not ctx.me or not bank(ctx):
        return None
    return Reply(text=f"📝 Тест недели готов: {min(settings(ctx.cfg)['count'], len(bank(ctx)))} вопросов, пара минут.",
                 buttons=[[Button("📝 Пройти", f"qzs:test:{week_ref(ctx.today)}")]])
