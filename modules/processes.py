"""🔄 Процессы отдела (Адам 31.10, ⭐ Дополнительно): работа отдела пошагово — шаг, кто отвечает, срок; отметки по шагам,
просрочки, напоминания; руководитель подразделения видит свой отдел, директор — все отделы и любой процесс до шага;
задания отделу от руководителя. Процессы описываются под каждую фирму (при подключении или в приложении).

Описание процесса (шаблон): {id, kind — вид отдела, name, steps: [[шаг, кто, дней]]}. «Кто»: head — начальник отдела,
staff — сотрудник отдела, head:<вид> / staff:<вид> — другого отдела, accountant — бухгалтер, owner — директор,
pos:<должность> — конкретная должность.
Запущенный процесс: {id, tpl, name, title, kind, by, started, status, steps: [{t, who, due, by, ts, note}]}; срок шага —
дата запуска + дни всех шагов до него включительно. База: proc_tpl, proc_runs (ключи), номера — proc_seq.
Экраны: prc[:вид] — процессы (по отделам), pro:<№> — процесс, prk:<№> — окно «отметить шаги», prn — запустить,
prq — задание отделу, prt[:вид] — описания процессов, prd:<id> — описание и правка.
"""
from datetime import date, datetime, timedelta

from core.calendar import day_title
from core.jobs.schedule import job
from core.ui.kit import Button, Reply, ok_row, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, View

M = "processes"
KIND_TITLE = {"sales": "📈 Продажи", "purchasing": "🛒 Закупки", "warehouse": "📦 Склад", "logistics": "🚚 Логистика",
              "office": "🏢 Офис и бухгалтерия"}
WHO_TITLE = {"head": "начальник отдела", "staff": "сотрудник отдела", "accountant": "бухгалтер", "owner": "директор"}

# Примеры процессов — чтобы было с чего начать; у каждой фирмы свои (правятся в «📋 Описания процессов»).
DEFAULT = [
    ("purchasing", "Заказ поставщику", [("Составить заказ", "staff", 1), ("Согласовать", "head", 1),
                                         ("Отправить поставщику", "staff", 1), ("Оплатить", "accountant", 2),
                                         ("Принять товар", "staff:warehouse", 5)]),
    ("purchasing", "Новый поставщик", [("Запросить прайс и условия", "staff", 2), ("Сравнить с текущими", "staff", 1),
                                        ("Решение", "head", 1), ("Договор", "owner", 3)]),
    ("warehouse", "Инвентаризация", [("Подготовить ведомость", "head", 1), ("Пересчитать товар", "staff", 2),
                                      ("Сверить с 1С", "accountant", 1), ("Акт и списание", "head", 1)]),
    ("warehouse", "Приёмка поставки", [("Разгрузить и пересчитать", "staff", 1), ("Сверить с накладной", "staff", 1),
                                        ("Оприходовать в 1С", "accountant", 1)]),
    ("logistics", "Рейс", [("Собрать заявки", "staff", 1), ("Загрузить машину", "staff:warehouse", 1),
                           ("Доставить", "staff", 1), ("Сдать документы и деньги", "staff", 1)]),
    ("logistics", "Ремонт машины", [("Заявка на ремонт", "staff", 1), ("Согласовать", "head", 1),
                                     ("Ремонт", "staff", 3), ("Оплата", "accountant", 2)]),
    ("sales", "Новый клиент", [("Договор и документы", "staff", 2), ("Проверить долги и лимит", "head", 1),
                                ("Завести в 1С", "accountant", 1), ("Первый заказ", "staff", 3)]),
    ("office", "Закрытие месяца", [("Табели от отделов", "head", 2), ("Расчёт зарплаты", "accountant", 2),
                                    ("Утвердить", "owner", 1), ("Выплатить", "accountant", 2)]),
]


def ok(ctx):
    return ctx.role in ("owner", "admin", "head", "staff", "accountant", "sv", "rep")


menu("prc", "🔄 Процессы", {"owner", "admin", "head", "staff", "accountant", "sv"}, module=M, order=72, when=ok)


# ---------- данные ----------

def _today(ctx):
    return ctx.today if isinstance(ctx.today, date) else date.fromisoformat(str(ctx.today))


def templates(ctx):
    tpl = ctx.db.get("proc_tpl")
    if tpl is None:
        kinds = {d.kind for d in ctx.cfg.departments}
        tpl = [{"id": n + 1, "kind": k, "name": nm, "steps": [list(s) for s in st]}
               for n, (k, nm, st) in enumerate([x for x in DEFAULT if x[0] in kinds])]
        ctx.db.put("proc_tpl", tpl)
        if ctx.demo:
            _seed_runs(ctx, tpl)
    return tpl


def runs(ctx):
    return ctx.db.get("proc_runs") or []


def _save_runs(ctx, rs):
    ctx.db.put("proc_runs", rs[-500:])


def _next_id(rs):
    return max([r["id"] for r in rs] or [0]) + 1


def _start(ctx, tpl, title, by, started=None):
    started = started or _today(ctx)
    steps, day = [], started
    for t, who, days in tpl["steps"]:
        day = day + timedelta(days=int(days))
        steps.append({"t": t, "who": who, "due": day.isoformat(), "by": "", "ts": "", "note": ""})
    return {"id": 0, "tpl": tpl["id"], "name": tpl["name"], "title": title, "kind": tpl["kind"], "by": by,
            "started": started.isoformat(), "status": "open", "steps": steps}


def _seed_runs(ctx, tpl):
    """Демо: несколько процессов в работе — с выполненными шагами и одной просрочкой, чтобы было что показать."""
    rs, today = [], _today(ctx)
    for n, t in enumerate(tpl):
        r = _start(ctx, t, f"№{100 + n}", "демо", started=today - timedelta(days=3 + n % 4))
        done = min(len(r["steps"]) - 1, 1 + n % 3)
        for s in r["steps"][:done]:
            s["by"], s["ts"] = "демо", (today - timedelta(days=1)).isoformat()
        r["id"] = _next_id(rs)
        rs.append(r)
    ctx.db.put("proc_runs", rs)


def _kind_of(ctx, e=None):
    e = e or ctx.me
    d = ctx.cfg.dept(e["dept"]) if e else None
    return d.kind if d else ""


def who_title(ctx, who):
    if who in WHO_TITLE:
        return WHO_TITLE[who]
    if who.startswith("pos:"):
        p = ctx.cfg.position(who[4:])
        return p["name"].lower() if p else who[4:]
    if ":" in who:
        r, k = who.split(":", 1)
        return f"{WHO_TITLE.get(r, r)} · {KIND_TITLE.get(k, k)[2:].lower()}"
    return who


def _is_mine(ctx, run, who, e=None):
    """Отвечает ли сотрудник за шаг."""
    e = e or ctx.me
    if not e:
        return False
    kind = _kind_of(ctx, e)
    if who.startswith("pos:"):
        return e["position"] == who[4:]
    role, _, k = who.partition(":")
    k = k or run["kind"]
    if role == "owner":
        return e["role"] == "owner"
    if role == "accountant":
        return e["role"] == "accountant"
    if role == "head":
        return e["role"] == "head" and kind == k
    if role == "staff":
        return e["role"] in ("staff", "rep", "sv") and kind == k
    return False


def _sees_kind(ctx, kind):
    return ctx.role in ("owner", "admin") or (ctx.me and _kind_of(ctx) == kind) or ctx.role == "accountant"


def _manages(ctx, kind):
    return ctx.role in ("owner", "admin") or (ctx.role == "head" and _kind_of(ctx) == kind)


def _may_mark(ctx, run, s):
    return _manages(ctx, run["kind"]) or _is_mine(ctx, run, s["who"])


def _cur(run):
    return next((s for s in run["steps"] if not s["by"]), None)


def _late(ctx, s):
    return not s["by"] and s["due"] < _today(ctx).isoformat()


def _people(ctx, run, who):
    return [e for e in ctx.db.employees() if _is_mine(ctx, run, who, e)]


def _d(iso):
    return day_title(date.fromisoformat(iso))


# ---------- экраны ----------

@screen("prc", module=M)
def hub(ctx, kind=None):
    templates(ctx)
    rs = [r for r in runs(ctx) if r["status"] == "open"]
    if kind is None and ctx.role not in ("owner", "admin", "accountant") and ctx.me:
        kind = _kind_of(ctx)
    kb = []
    if kind is None:                                     # директор: все отделы
        v = View("🔄 Процессы отделов", "что в работе и что просрочено")
        kinds = [d.kind for d in ctx.cfg.departments if d.kind in KIND_TITLE]
        cards = []
        for k in dict.fromkeys(kinds):
            mine = [r for r in rs if r["kind"] == k]
            late = [r for r in mine if any(_late(ctx, s) for s in r["steps"])]
            cards.append(Card(KIND_TITLE[k], f"в работе {len(mine)}", [f"⚠️ просрочено: {len(late)}" if late else "без просрочек"],
                              color="bad" if late else "good" if mine else None))
            kb.append([Button(f"{KIND_TITLE[k]} · {len(mine)}" + (f" · ⚠️ {len(late)}" if late else ""), f"prc:{k}")])
        v.cards(cards)
    else:
        if not _sees_kind(ctx, kind):
            return Reply(text="Процессы этого отдела видит его руководитель и директор.")
        v = View(f"🔄 Процессы · {KIND_TITLE.get(kind, kind)[2:]}", "в работе по шагам")
        mine = [r for r in rs if r["kind"] == kind]
        if ctx.me:
            todo = [(r, s) for r in rs for s in r["steps"] if not s["by"] and s is _cur(r) and _is_mine(ctx, r, s["who"])]
            if todo:
                v.lines([f"{'⚠️' if _late(ctx, s) else '•'} {r['name']} {r['title']}: {s['t']} — до {_d(s['due'])}" for r, s in todo],
                        title="Мои шаги")
        cards = []
        for r in mine:
            s = _cur(r)
            late = s and _late(ctx, s)
            done = sum(1 for x in r["steps"] if x["by"])
            cards.append(Card(f"{r['name']} {r['title']}", f"{done} из {len(r['steps'])}",
                              [f"Сейчас: {s['t']} — {who_title(ctx, s['who'])}, до {_d(s['due'])}"] if s else ["все шаги сделаны"],
                              color="bad" if late else None))
            kb.append([Button(("⚠️ " if late else "🔄 ") + f"{r['name']} {r['title']}"[:34], f"pro:{r['id']}")])
        v.cards(cards) if cards else v.note("Процессов в работе нет.")
    kb.append([Button("➕ Запустить процесс", "prn")] + ([Button("📌 Задание отделу", "prq")]
                                                         if ctx.role in ("owner", "admin", "head") else []))
    if ctx.role in ("owner", "admin", "head"):
        kb.append([Button("📋 Описания процессов", "prt")])
    return Reply(view=v, buttons=kb)


@screen("pro", module=M)
def run_card(ctx, rid):
    r = next((x for x in runs(ctx) if x["id"] == int(rid)), None)
    if not r or not _sees_kind(ctx, r["kind"]):
        return Reply(text="Нет такого процесса.")
    v = View(f"🔄 {r['name']} {r['title']}", f"{KIND_TITLE.get(r['kind'], '')[2:]} · запустил {r['by']} {_d(r['started'])}")
    lines = []
    for s in r["steps"]:
        mark = "●" if s["by"] else ("⚠️" if _late(ctx, s) else "○")
        tail = f" · ✅ {s['by']} {_d(s['ts'][:10])}" if s["by"] else f" · до {_d(s['due'])}"
        lines.append(f"{mark} {s['t']} — {who_title(ctx, s['who'])}{tail}" + (f"\n   💬 {s['note']}" if s["note"] else ""))
    v.lines(lines, title="Шаги")
    kb = []
    if r["status"] == "open" and any(not s["by"] and _may_mark(ctx, r, s) for s in r["steps"]):
        kb.append([Button("☑️ Отметить сделанные шаги", f"prk:{r['id']}")])
    if r["status"] == "open" and _cur(r) and _manages(ctx, r["kind"]):
        kb.append([Button("🔔 Напомнить ответственному", f"prr:{r['id']}")])
    return Reply(view=v, buttons=kb)


@screen("prk", module=M)
def mark_window(ctx, rid, act=None, value=None):
    """Окно: отметить сделанные шаги галочками, «ОК» — отмечено, следующий шаг уходит ответственному."""
    rs = runs(ctx)
    r = next((x for x in rs if x["id"] == int(rid)), None)
    if not r or r["status"] != "open":
        return Reply(text="Процесс уже завершён.")
    mine = [i for i, s in enumerate(r["steps"]) if not s["by"] and _may_mark(ctx, r, s)]
    pre = f"prk:{r['id']}"
    d = ctx.session.get("prk")
    if act is None or not isinstance(d, dict) or d.get("k") != pre:
        d = {"k": pre, "sel": []}
    if act == "no":
        ctx.session.pop("prk", None)
        ctx.save()
        return run_card(ctx, rid)
    if act == "t" and (value or "").isdigit() and int(value) in mine:
        i = int(value)
        d["sel"] = [x for x in d["sel"] if x != i] if i in d["sel"] else d["sel"] + [i]
    if act == "ok":
        ctx.session.pop("prk", None)
        ctx.save()
        who = short(ctx.name()) if ctx.me else ctx.name()
        for i in d["sel"]:
            if i in mine:
                r["steps"][i]["by"], r["steps"][i]["ts"] = who, datetime.now().isoformat(timespec="minutes")
        out = []
        nxt = _cur(r)
        if not nxt:
            r["status"] = "done"
        _save_runs(ctx, rs)
        if d["sel"]:
            ctx.db.audit(ctx.author(), "process", f"{r['name']} {r['title']}: отмечено шагов {len(d['sel'])}")
        if nxt and d["sel"]:
            for e in _people(ctx, r, nxt["who"]):
                if e.get("tg_id") and (not ctx.me or e["id"] != ctx.me["id"]):
                    out.append(Reply(text=f"🔄 {r['name']} {r['title']}: ваш шаг — «{nxt['t']}», до {_d(nxt['due'])}.",
                                     to=e["tg_id"], buttons=[[Button("🔄 Открыть", f"pro:{r['id']}")]]))
        return out + [run_card(ctx, rid)]
    ctx.session["prk"] = d
    ctx.save()
    v = View(f"☑️ {r['name']} {r['title']}", "отметьте сделанные шаги и нажмите «ОК»")
    if not mine:
        v.note("Ваших открытых шагов нет.")
    kb = [[Button(f"{'☑' if i in d['sel'] else '☐'} {r['steps'][i]['t']} · до {_d(r['steps'][i]['due'])}", f"{pre}:t:{i}",
                  info=f"Отвечает: {who_title(ctx, r['steps'][i]['who'])}. Срок — {_d(r['steps'][i]['due'])}.")] for i in mine]
    kb.append(ok_row(pre))
    return Reply(view=v, buttons=kb)


@screen("prr", module=M)
def remind(ctx, rid):
    r = next((x for x in runs(ctx) if x["id"] == int(rid)), None)
    if not r or not _manages(ctx, r["kind"]) or not _cur(r):
        return hub(ctx)
    s = _cur(r)
    out = [Reply(text=f"🔔 Напоминание: {r['name']} {r['title']} — «{s['t']}», срок {_d(s['due'])}.", to=e["tg_id"],
                 buttons=[[Button("🔄 Открыть", f"pro:{r['id']}")]]) for e in _people(ctx, r, s["who"]) if e.get("tg_id")]
    return out + [Reply(text=f"🔔 Напомнили: {len(out)} чел." if out else "Некому напомнить — ответственные ещё не вошли в приложение."),
                  run_card(ctx, rid)]


# ----- запустить процесс -----

@screen("prn", module=M)
def start(ctx, act=None, value=None):
    from core.ui import pick
    tpl = templates(ctx)
    kinds = None if ctx.role in ("owner", "admin") else {_kind_of(ctx)}
    opts = [(str(t["id"]), f"{KIND_TITLE.get(t['kind'], '')[:2]} {t['name']} · шагов {len(t['steps'])}") for t in tpl
            if kinds is None or t["kind"] in kinds]
    if not opts:
        return Reply(text="Для вашего отдела процессы ещё не описаны — руководитель добавит их в «📋 Описания процессов».")
    res = pick.one(ctx, "prn", act, value, opts, None, "➕ Какой процесс запускаем?", "отметьте и нажмите «ОК»")
    if res.cancel or (res.done and not res.value):
        return hub(ctx)
    if not res.done:
        return res.reply
    return ctx.ask("proc_title", "Подпись процесса — чтобы отличать: например «Опт-Снаб, 05.11» или «машина 123». «-» — без подписи.",
                   {"tpl": int(res.value)}, buttons=[[Button("✖️ Отмена", "prc")]])


@text_input("proc_title")
def on_title(ctx, text, data):
    t = next((x for x in templates(ctx) if x["id"] == data["tpl"]), None)
    if not t:
        return hub(ctx)
    rs = runs(ctx)
    title = "" if text.strip() in ("-", "—") else " ".join(text.split())[:60]
    r = _start(ctx, t, title, short(ctx.name()) if ctx.me else ctx.name())
    r["id"] = _next_id(rs)
    rs.append(r)
    _save_runs(ctx, rs)
    ctx.db.audit(ctx.author(), "process", f"запущен: {r['name']} {r['title']}")
    s = r["steps"][0]
    out = [Reply(text=f"🔄 Новый процесс «{r['name']} {r['title']}»: ваш шаг — «{s['t']}», до {_d(s['due'])}.", to=e["tg_id"],
                 buttons=[[Button("🔄 Открыть", f"pro:{r['id']}")]])
           for e in _people(ctx, r, s["who"]) if e.get("tg_id") and (not ctx.me or e["id"] != ctx.me["id"])]
    return out + [run_card(ctx, str(r["id"]))]


# ----- задание отделу -----

@screen("prq", module=M)
def task(ctx):
    if ctx.role not in ("owner", "admin", "head"):
        return hub(ctx)
    return ctx.ask("proc_task", "Задание и срок, например: «Инвентаризация витрины до 05.11». Без даты — срок 3 дня.",
                   buttons=[[Button("✖️ Отмена", "prc")]])


@text_input("proc_task")
def on_task(ctx, text, data):
    import re
    t = " ".join(text.split())[:120]
    m = re.search(r"(?:до\s+)?(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*$", t)
    today = _today(ctx)
    due = today + timedelta(days=3)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            due = date(y, int(m.group(2)), int(m.group(1)))
            t = t[:m.start()].strip()
        except ValueError:
            pass
    kind = _kind_of(ctx) if ctx.role == "head" else (_kind_of(ctx) or "office")
    if ctx.role in ("owner", "admin"):
        ctx.session["prq"] = {"t": t, "due": due.isoformat()}
        ctx.save()
        return task_dept(ctx)
    return _make_task(ctx, kind, t, due)


@screen("prqd", module=M)
def task_dept(ctx, act=None, value=None):
    from core.ui import pick
    d = ctx.session.get("prq") or {}
    kinds = [k for k in dict.fromkeys(x.kind for x in ctx.cfg.departments) if k in KIND_TITLE]
    res = pick.one(ctx, "prqd", act, value, [(k, KIND_TITLE[k]) for k in kinds], None, "📌 Какому отделу задание?",
                   d.get("t", ""))
    if res.cancel or (res.done and not res.value):
        return hub(ctx)
    if not res.done:
        return res.reply
    return _make_task(ctx, res.value, d.get("t", "Задание"), date.fromisoformat(d.get("due")))


def _make_task(ctx, kind, text, due):
    rs = runs(ctx)
    start_ = _today(ctx)
    r = {"id": _next_id(rs), "tpl": 0, "name": "📌", "title": text, "kind": kind, "by": short(ctx.name()) if ctx.me else ctx.name(),
         "started": start_.isoformat(), "status": "open",
         "steps": [{"t": text, "who": "staff", "due": due.isoformat(), "by": "", "ts": "", "note": ""}]}
    rs.append(r)
    _save_runs(ctx, rs)
    out = [Reply(text=f"📌 Задание отделу: «{text}», до {_d(due.isoformat())}.", to=e["tg_id"],
                 buttons=[[Button("🔄 Открыть", f"pro:{r['id']}")]]) for e in _people(ctx, r, "staff") if e.get("tg_id")]
    return out + [run_card(ctx, str(r["id"]))]


# ----- описания процессов -----

@screen("prt", module=M)
def tpl_list(ctx, kind=None):
    if ctx.role not in ("owner", "admin", "head"):
        return hub(ctx)
    if ctx.role == "head":
        kind = _kind_of(ctx)
    tpl = templates(ctx)
    v = View("📋 Описания процессов", "шаги, кто отвечает, сроки — под вашу фирму")
    shown = [t for t in tpl if kind is None or t["kind"] == kind]
    v.cards([Card(f"{KIND_TITLE.get(t['kind'], '')[:2]} {t['name']}", f"шагов {len(t['steps'])}",
                  [f"{n}. {s[0]} — {who_title(ctx, s[1])}, {s[2]} дн." for n, s in enumerate(t["steps"], 1)]) for t in shown]) \
        if shown else v.note("Процессов пока нет.")
    kb = rows([Button(f"✏️ {t['name']}"[:30], f"prd:{t['id']}") for t in shown], 2)
    kb.append([Button("➕ Новый процесс", f"prdn:{kind or ''}")])
    return Reply(view=v, buttons=kb)


HOW = ("Первая строка — название, дальше по строке на шаг: «шаг — кто — дней».\n"
       "Кто: начальник, сотрудник, бухгалтер, директор, склад, логистика, закупки, продажи или название должности.\n"
       "Пример:\nЗаказ поставщику\nСоставить заказ — сотрудник — 1\nСогласовать — начальник — 1\nОплатить — бухгалтер — 2")


@screen("prd", module=M)
def tpl_edit(ctx, tid):
    t = next((x for x in templates(ctx) if x["id"] == int(tid)), None)
    if not t or not _manages(ctx, t["kind"]):
        return tpl_list(ctx)
    cur = t["name"] + "\n" + "\n".join(f"{s[0]} — {who_title(ctx, s[1])} — {s[2]}" for s in t["steps"])
    return ctx.ask("proc_tpl", f"Сейчас:\n{cur}\n\n{HOW}\n\nНапишите заново целиком. «0» — удалить процесс.",
                   {"id": t["id"], "kind": t["kind"]}, buttons=[[Button("✖️ Отмена", "prt")]])


@screen("prdn", module=M)
def tpl_new(ctx, kind=""):
    if ctx.role not in ("owner", "admin", "head"):
        return hub(ctx)
    kind = _kind_of(ctx) if ctx.role == "head" else (kind or _kind_of(ctx) or "office")
    return ctx.ask("proc_tpl", f"Новый процесс · {KIND_TITLE.get(kind, kind)}\n\n{HOW}", {"id": 0, "kind": kind},
                   buttons=[[Button("✖️ Отмена", "prt")]])


_WORDS = [("начальн", "head"), ("руковод", "head"), ("бухгалт", "accountant"), ("директор", "owner"), ("сотрудник", "staff"),
          ("склад", "staff:warehouse"), ("логист", "staff:logistics"), ("водител", "staff:logistics"), ("закуп", "staff:purchasing"),
          ("продаж", "staff:sales"), ("торгов", "staff:sales")]


def _who(ctx, word):
    w = word.lower().strip()
    p = next((p for p in ctx.cfg.positions if p["name"].lower() == w), None)
    if p:
        return f"pos:{p['id']}"
    return next((code for key, code in _WORDS if key in w), "staff")


@text_input("proc_tpl")
def on_tpl(ctx, text, data):
    import re
    tpl = templates(ctx)
    if text.strip() == "0" and data.get("id"):
        ctx.db.put("proc_tpl", [t for t in tpl if t["id"] != data["id"]])
        return tpl_list(ctx)
    lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
    steps = []
    for ln in lines[1:]:
        parts = [p.strip() for p in re.split(r"\s+[—–-]\s+|;", ln) if p.strip()]
        if not parts:
            continue
        days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        steps.append([parts[0][:60], _who(ctx, parts[1]) if len(parts) > 1 else "staff", days])
    if not lines or not steps:
        return ctx.ask("proc_tpl", "Не понял. " + HOW, data, buttons=[[Button("✖️ Отмена", "prt")]])
    if data.get("id"):
        t = next(x for x in tpl if x["id"] == data["id"])
        t["name"], t["steps"] = lines[0][:60], steps
    else:
        tpl.append({"id": max([x["id"] for x in tpl] or [0]) + 1, "kind": data["kind"], "name": lines[0][:60], "steps": steps})
    ctx.db.put("proc_tpl", tpl)
    ctx.db.audit(ctx.author(), "process_tpl", lines[0][:60])
    return tpl_list(ctx)


# ---------- напоминания: утром — свои шаги на сегодня и просроченные ----------

@job("proc_today", "morning", {"owner", "head", "staff", "accountant", "sv", "rep"}, "Утром: шаги процессов на сегодня", module=M)
def today_steps(ctx):
    if not ctx.me:
        return None
    today = _today(ctx).isoformat()
    mine = [(r, s) for r in runs(ctx) if r["status"] == "open" for s in [_cur(r)]
            if s and s["due"] <= today and _is_mine(ctx, r, s["who"])]
    late = [r for r in runs(ctx) if r["status"] == "open" and _cur(r) and _late(ctx, _cur(r))
            and ctx.role == "head" and r["kind"] == _kind_of(ctx)]
    if not mine and not late:
        return None
    text = []
    if mine:
        text.append("🔄 Шаги на сегодня:\n" + "\n".join(f"{'⚠️' if s['due'] < today else '•'} {r['name']} {r['title']}: {s['t']}"
                                                       for r, s in mine))
    if late:
        text.append(f"⚠️ В отделе просрочено процессов: {len(late)}")
    return Reply(text="\n\n".join(text), buttons=[[Button("🔄 Процессы", "prc")]])
