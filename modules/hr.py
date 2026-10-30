"""Модуль «Кадры»: приём, перевод и увольнение по чек-листу — кто за какой пункт отвечает, кто закрыл и когда;
когда всё закрыто — «Провести»: человек появляется в расчёте с даты приёма, переходит на новую должность или
уходит из расчёта после последнего дня; приказ — файлом Word.

Ответственные за пункты: owner — директор, hr — сотрудник с обязанностью «Кадры», head — начальник отдела,
куда (или откуда) человек идёт, accountant — бухгалтер. Директор и админ могут закрыть любой пункт.
Запускают: директор, админ, кадры — в любой отдел; начальник отдела — в свой.
Перевод: старая запись закрывается днём перед переводом, новая — с даты перевода (зарплата месяца — по двум строкам),
ключи 1С и вход в приложение переходят новой записи.
База: hr_procs (список процессов), hr_seq (номера приказов).
"""
import io
import zipfile
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from core.calendar import MONTHS_GEN, day_title, first_day, last_day, month_of, shift_month
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, View

M = "hr"
KINDS = {"hire": ("➕ Приём", "приём на работу", "п"), "move": ("🔁 Перевод", "перевод", "пер"),
         "fire": ("🚪 Увольнение", "увольнение", "у")}
WHO = {"owner": "директор", "hr": "кадры", "head": "начальник отдела", "accountant": "бухгалтер"}
ITEMS = {
    "hire": [("Решение директора", "owner"), ("Анкета и документы", "hr"), ("Трудовой договор подписан", "hr"),
             ("Рабочее место, ключи, доступы", "head"), ("Инструктаж и наставник", "head"), ("Внесён в 1С", "hr")],
    "move": [("Решение директора", "owner"), ("Сдал дела на старом месте", "head"), ("Доп. соглашение подписано", "hr"),
             ("Новые доступы, старые закрыты", "head"), ("Изменён в 1С", "hr")],
    "fire": [("Решение директора", "owner"), ("Заявление", "hr"), ("Сдал дела, ключи, товар, долги клиентов", "head"),
             ("Окончательный расчёт", "accountant"), ("Сведения о стаже выданы", "hr"), ("Отключён в 1С и приложении", "hr")],
}


def _kind(ctx, x):
    d = ctx.cfg.dept(x["dept"]) if x else None
    return d.kind if d else ""


def starts(ctx):
    """Отделы, в которые этот человек может запускать кадровые процессы."""
    if ctx.role in ("owner", "admin") or ctx.duty == "hr":
        return {d.id for d in ctx.cfg.departments}
    if ctx.role == "head" and ctx.me:
        return {ctx.me["dept"]}
    return set()


def ok(ctx):
    return bool(starts(ctx)) or ctx.role == "accountant"


menu("hr", "👤 Кадры", {"owner", "admin", "head", "staff", "accountant"}, module=M, order=70, when=ok)


def procs(db):
    return db.get("hr_procs") or []


def _save(db, ps):
    db.put("hr_procs", ps)


def _find(ctx, pid):
    return next((p for p in procs(ctx.db) if p["id"] == int(pid)), None)


def _sees(ctx, p):
    if ctx.role in ("owner", "admin", "accountant") or ctx.duty == "hr":
        return True
    return ctx.role == "head" and ctx.me and ctx.me["dept"] in (p["dept"], p.get("from_dept"))


def may_close(ctx, p, who):
    if ctx.role in ("owner", "admin"):
        return True
    if who == "hr":
        return ctx.duty == "hr"
    if who == "accountant":
        return ctx.role == "accountant"
    if who == "head":
        return ctx.role == "head" and ctx.me and ctx.me["dept"] in (p["dept"], p.get("from_dept"))
    return False


def _pos_name(ctx, pid):
    return (ctx.cfg.position(pid) or {}).get("name", pid)


def _dm(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def _title(ctx, p):
    return f"{KINDS[p['kind']][0]} · {p['fio']}"


def _sub(ctx, p):
    dept = ctx.cfg.dept(p["dept"])
    if p["kind"] == "fire":
        return f"последний день {_dm(p['day'])} · {_pos_name(ctx, p['pos'])}"
    s = f"{_pos_name(ctx, p['pos'])} · {dept.name if dept else ''} · с {_dm(p['day'])}"
    return s + (f" · оклад {money(p['salary'])}" if p.get("salary") else "")


# ---------- список ----------

@screen("hr", module=M)
def hr_list(ctx):
    if not ok(ctx):
        return Reply(text="Кадры ведут директор, кадры и начальники отделов.")
    ps = [p for p in procs(ctx.db) if _sees(ctx, p)]
    open_ = [p for p in ps if p["status"] == "open"]
    done = [p for p in ps if p["status"] != "open"][-5:]
    mine = sum(1 for p in open_ for t, who, by, _ in p["items"] if not by and may_close(ctx, p, who))
    v = View("👤 Кадры", f"в работе {len(open_)} · ждут вас пунктов: {mine}")
    cards = []
    for p in open_:
        n = sum(1 for it in p["items"] if it[2])
        wait = [f"{t} — {WHO[who]}" for t, who, by, _ in p["items"] if not by][:2]
        cards.append(Card(_title(ctx, p), f"{n} из {len(p['items'])}", [_sub(ctx, p)] + [f"○ {w}" for w in wait],
                          color="good" if n == len(p["items"]) else "warn"))
    v.cards(cards, title="В работе")
    if not open_:
        v.note("Открытых процессов нет.")
    if done:
        v.lines([f"{'✅' if p['status'] == 'done' else '✖️'} {KINDS[p['kind']][1]} · {p['fio']} · "
                 f"{('приказ №' + p['order']) if p.get('order') else 'отменён'}" for p in reversed(done)], title="Проведены")
    kb = rows([Button(f"{KINDS[p['kind']][0].split()[0]} {short(p['fio'])}", f"hro:{p['id']}") for p in open_ + done[::-1]], 2)
    if starts(ctx):
        kb.append([Button(KINDS[k][0], f"hrn:{k}") for k in KINDS])
    return Reply(view=v, buttons=kb)


@screen("hro", module=M)
def proc(ctx, pid):
    p = _find(ctx, pid)
    if not p or not _sees(ctx, p):
        return Reply(text="Нет такого процесса.")
    v = View(_title(ctx, p), _sub(ctx, p))
    lines, kb = [], []
    for i, (t, who, by, ts) in enumerate(p["items"]):
        lines.append(f"{'●' if by else '○'} {t} — {WHO[who]}" + (f" · {by}, {ts}" if by else ""))
        if not by and p["status"] == "open" and may_close(ctx, p, who):
            kb.append([Button(f"✅ {t[:30]}", f"hrc:{p['id']}:{i}")])
    v.lines(lines, title="Чек-лист")
    v.note(f"Запустил {p['author']} {p['created']}." + (f" Приказ №{p['order']}." if p.get("order") else ""))
    if p["status"] == "open" and all(it[2] for it in p["items"]) and (starts(ctx) or ctx.role in ("owner", "admin")):
        kb.append([Button("✅ Провести и выпустить приказ", f"hra:{p['id']}")])
    if p.get("order"):
        kb.append([Button("📄 Приказ (Word)", f"hrd:{p['id']}")])
    if p["status"] == "open" and (ctx.role in ("owner", "admin") or p.get("author_id") == (ctx.me or {}).get("id")):
        kb.append([Button("✖️ Отменить", f"hrx:{p['id']}")])
    kb.append([Button("‹ Кадры", "hr")])
    return Reply(view=v, buttons=kb)


@screen("hrc", module=M)
def close_item(ctx, pid, i):
    ps = procs(ctx.db)
    p = next((x for x in ps if x["id"] == int(pid)), None)
    if not p or p["status"] != "open" or int(i) >= len(p["items"]):
        return Reply(text="Пункт уже закрыт.")
    it = p["items"][int(i)]
    if it[2] or not may_close(ctx, p, it[1]):
        return Reply(text="Этот пункт закрывает " + WHO[it[1]] + ".")
    it[2], it[3] = short(ctx.name()) if ctx.me else ctx.name(), datetime.now().strftime("%d.%m %H:%M")
    _save(ctx.db, ps)
    ctx.db.audit(ctx.author(), "hr_item", f"{KINDS[p['kind']][1]} {p['fio']}: {it[0]}")
    out = []
    if all(x[2] for x in p["items"]):
        out.append(Reply(text=f"🎉 Все пункты закрыты: {KINDS[p['kind']][1]} · {p['fio']}. Можно проводить."))
        out += _tell(ctx, p, f"✅ {KINDS[p['kind']][1].capitalize()} · {p['fio']}: все пункты закрыты — можно проводить.",
                     ("hr",))
    return out + [proc(ctx, pid)]


@screen("hrx", module=M)
def cancel(ctx, pid):
    ps = procs(ctx.db)
    p = next((x for x in ps if x["id"] == int(pid)), None)
    if not p or p["status"] != "open":
        return hr_list(ctx)
    if not (ctx.role in ("owner", "admin") or p.get("author_id") == (ctx.me or {}).get("id")):
        return Reply(text="Отменить может тот, кто запустил, или директор.")
    p["status"] = "cancel"
    _save(ctx.db, ps)
    ctx.db.audit(ctx.author(), "hr_cancel", f"{KINDS[p['kind']][1]} {p['fio']}")
    return hr_list(ctx)


def _tell(ctx, p, text, whos):
    """Сообщение ответственным: кадрам, начальнику отдела, бухгалтеру, директору."""
    out = []
    for e in ctx.db.employees():
        if not e.get("tg_id") or (ctx.me and e["id"] == ctx.me["id"]):
            continue
        duty = (ctx.cfg.position(e["position"]) or {}).get("duty")
        hit = ("hr" in whos and duty == "hr") or ("owner" in whos and e["role"] == "owner") or \
              ("accountant" in whos and e["role"] == "accountant") or \
              ("head" in whos and e["role"] == "head" and e["dept"] in (p["dept"], p.get("from_dept")))
        if hit:
            out.append(Reply(text=text, to=e["tg_id"], buttons=[[Button("👤 Открыть", f"hro:{p['id']}")]]))
    return out


# ---------- запуск: мастер ----------

def _w(ctx):
    return ctx.session.setdefault("hrw", {})


@screen("hrn", module=M)
def start(ctx, kind):
    if kind not in KINDS or not starts(ctx):
        return Reply(text="Запускать кадровые процессы могут директор, кадры и начальник своего отдела.")
    ctx.session["hrw"] = {"kind": kind}
    ctx.save()
    if kind == "hire":
        return ctx.ask("hr_fio", "➕ Приём. Фамилия, имя и отчество нового сотрудника?",
                       buttons=[[Button("✖️ Отмена", "hr")]])
    es = [e for e in ctx.db.employees() if e["dept"] in starts(ctx) and e["role"] != "owner"]
    return Reply(text=f"{KINDS[kind][0]}. Кого?", buttons=rows([Button(short(e["fio"]) + " · " + _pos_name(ctx, e["position"])[:14],
                                                                      f"hrw:emp:{e['id']}") for e in es], 2)
                 + [[Button("✖️ Отмена", "hr")]])


@text_input("hr_fio")
def on_fio(ctx, text, data):
    fio = " ".join((text or "").split())[:80]
    if len(fio.split()) < 2:
        return ctx.ask("hr_fio", "Напишите фамилию и имя (можно с отчеством).", buttons=[[Button("✖️ Отмена", "hr")]])
    _w(ctx)["fio"] = fio
    ctx.save()
    return _ask_pos(ctx)


def _ask_pos(ctx):
    w = _w(ctx)
    ps = [p for p in ctx.cfg.positions if p["dept"] in starts(ctx) and p["role"] not in ("owner",)
          and (w["kind"] != "move" or p["id"] != w.get("old_pos"))]
    return Reply(text=f"{KINDS[w['kind']][0]} · {w.get('fio', '')}\nНа какую должность?",
                 buttons=rows([Button(p["name"][:28], f"hrw:pos:{p['id']}") for p in ps], 2) + [[Button("✖️ Отмена", "hr")]])


def _ask_day(ctx):
    w = _w(ctx)
    t = ctx.today
    nxt_mon = t + timedelta(days=(7 - t.weekday()) % 7 or 7)
    if w["kind"] == "fire":
        opts = [("Сегодня", t), ("Завтра", t + timedelta(days=1)), ("Конец месяца", last_day(month_of(t)))]
        q = "Последний рабочий день?"
    else:
        opts = [("Сегодня", t), ("Завтра", t + timedelta(days=1)), ("С понедельника", nxt_mon),
                ("С 1-го числа", first_day(shift_month(month_of(t), 1)))]
        q = "С какого дня?"
    return Reply(text=f"{KINDS[w['kind']][0]} · {w.get('fio', '')}\n{q}",
                 buttons=rows([Button(f"{a} · {day_title(d)}", f"hrw:day:{d.isoformat()}") for a, d in opts], 2)
                 + [[Button("✖️ Отмена", "hr")]])


@screen("hrw", module=M)
def wizard(ctx, what, value):
    w = _w(ctx)
    if not w.get("kind") or not starts(ctx):
        return hr_list(ctx)
    if what == "emp":
        e = ctx.db.employee(int(value))
        if not e or e["dept"] not in starts(ctx):
            return hr_list(ctx)
        w.update(emp=e["id"], fio=e["fio"], old_pos=e["position"], from_dept=e["dept"], salary=e.get("salary"))
        ctx.save()
        if w["kind"] == "fire":
            w.update(pos=e["position"], dept=e["dept"], team=e["team"])
            ctx.save()
            return _ask_day(ctx)
        return _ask_pos(ctx)
    if what == "pos":
        p = ctx.cfg.position(value)
        if not p:
            return _ask_pos(ctx)
        w.update(pos=p["id"], dept=p["dept"], team="")
        ctx.save()
        if _kind(ctx, p) == "sales" and p["role"] in ("rep", "sv", "newbie"):
            ts = ctx.cfg.teams_of(p["dept"])
            return Reply(text="В какую команду?", buttons=rows([Button(t.name, f"hrw:team:{t.id}") for t in ts], 2))
        return _ask_day(ctx)
    if what == "team":
        w["team"] = value
        ctx.save()
        return _ask_day(ctx)
    if what == "day":
        w["day"] = value
        ctx.save()
        if w["kind"] == "hire":
            return ctx.ask("hr_salary", "Оклад? Напишите сумму, например 45 000, или «-» — по схеме должности.",
                           buttons=[[Button("По схеме должности", "hrw:sal:-")], [Button("✖️ Отмена", "hr")]])
        return _create(ctx)
    if what == "sal":
        ctx.session.pop("await", None)
        return _create(ctx)
    return hr_list(ctx)


@text_input("hr_salary")
def on_salary(ctx, text, data):
    t = (text or "").replace(" ", "").replace(" ", "").replace("₽", "")
    if t not in ("-", "—", ""):
        if not t.isdigit() or not 1000 <= int(t) <= 10_000_000:
            return ctx.ask("hr_salary", "Напишите сумму числом, например 45 000, или «-».")
        _w(ctx)["salary"] = int(t)
        ctx.save()
    return _create(ctx)


def _create(ctx):
    w = ctx.session.pop("hrw", {})
    ctx.save()
    if not w.get("kind") or not w.get("day") or not w.get("pos"):
        return hr_list(ctx)
    ps = procs(ctx.db)
    pid = max([p["id"] for p in ps] or [0]) + 1
    p = {"id": pid, "kind": w["kind"], "fio": w["fio"], "emp": w.get("emp"), "pos": w["pos"], "dept": w["dept"],
         "team": w.get("team", ""), "from_dept": w.get("from_dept"), "day": w["day"],
         "salary": w.get("salary") if w["kind"] == "hire" else None, "status": "open",
         "items": [[t, who, "", ""] for t, who in ITEMS[w["kind"]]], "author": ctx.name(),
         "author_id": (ctx.me or {}).get("id"), "created": ctx.today.strftime("%d.%m.%Y")}
    if ctx.role in ("owner",):                    # запустил сам директор — его решение уже есть
        p["items"][0][2], p["items"][0][3] = short(ctx.name()), datetime.now().strftime("%d.%m %H:%M")
    ps.append(p)
    _save(ctx.db, ps)
    ctx.db.audit(ctx.author(), "hr_start", f"{KINDS[p['kind']][1]} {p['fio']}")
    out = [Reply(text=f"{KINDS[p['kind']][0]} · {p['fio']} — запущено. Ответственным ушли их пункты.")]
    out += _tell(ctx, p, f"👤 {KINDS[p['kind']][1].capitalize()} · {p['fio']} ({_sub(ctx, p)}) — есть ваши пункты.",
                 ("hr", "head", "owner", "accountant"))
    return out + [proc(ctx, str(pid))]


# ---------- провести и приказ ----------

@screen("hra", module=M)
def apply(ctx, pid):
    ps = procs(ctx.db)
    p = next((x for x in ps if x["id"] == int(pid)), None)
    if not p or p["status"] != "open" or not all(it[2] for it in p["items"]):
        return Reply(text="Провести можно, когда закрыты все пункты.")
    if not (starts(ctx) or ctx.role in ("owner", "admin")):
        return Reply(text="Проводит директор, кадры или начальник отдела.")
    day = date.fromisoformat(p["day"])
    pos = ctx.cfg.position(p["pos"]) or {}
    db = ctx.db
    if p["kind"] == "hire":
        role = "newbie" if pos.get("role") == "rep" else pos.get("role", "staff")
        kw = {"hired": day}
        if p.get("salary"):
            kw["salary"] = p["salary"]
        p["emp"] = db.add_employee(p["fio"], p["dept"], team=p.get("team", ""), position=p["pos"], role=role, **kw)
    elif p["kind"] == "move":
        old = db.employee(p["emp"])
        kw = {"hired": day}
        if old.get("salary"):
            kw["salary"] = old["salary"]
        new = db.add_employee(old["fio"], p["dept"], team=p.get("team", ""), position=p["pos"], role=pos.get("role", "staff"), **kw)
        db.x("UPDATE employees SET fired = ?, tg_id = NULL WHERE id = ?", (day - timedelta(days=1)).isoformat(), old["id"])
        if old.get("tg_id"):
            db.x("UPDATE employees SET tg_id = ? WHERE id = ?", old["tg_id"], new)
        for k in db.q("SELECT DISTINCT key FROM emp_keys WHERE emp_id = ?", old["id"]):
            db.bind_key(k["key"], new, since=day)
        p["emp"] = new
    else:
        active = 0 if day < first_day(month_of(ctx.today)) else 1      # до конца месяца остаётся в расчёте до последнего дня
        db.x("UPDATE employees SET fired = ?, active = ? WHERE id = ?", day.isoformat(), active, p["emp"])
    seq = db.get("hr_seq") or {}
    y = str(day.year)
    seq[y] = int(seq.get(y, 0)) + 1
    db.put("hr_seq", seq)
    p["order"] = f"{seq[y]}-{KINDS[p['kind']][2]}"
    p["status"] = "done"
    _save(db, ps)
    db.audit(ctx.author(), "hr_apply", f"{KINDS[p['kind']][1]} {p['fio']}, приказ №{p['order']}")
    out = [Reply(text=f"✅ Проведено: {KINDS[p['kind']][1]} · {p['fio']}. Приказ №{p['order']} — файлом ниже.")]
    out += _tell(ctx, p, f"✅ {KINDS[p['kind']][1].capitalize()} · {p['fio']} проведён(а), приказ №{p['order']}.",
                 ("hr", "head", "accountant"))
    out.append(order_file(ctx, pid))
    return out + [proc(ctx, pid)]


@screen("hrd", module=M)
def order_file(ctx, pid):
    p = _find(ctx, pid)
    if not p or not p.get("order") or not _sees(ctx, p):
        return Reply(text="Приказа ещё нет.")
    dept = ctx.cfg.dept(p["dept"])
    d = date.fromisoformat(p["day"])
    when = f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year} г."
    body = {
        "hire": [f"Принять на работу: {p['fio']}", f"на должность «{_pos_name(ctx, p['pos'])}» в {dept.name if dept else ''}",
                 f"с {when}" + (f", оклад {money(p['salary'])} в месяц." if p.get("salary") else ".")],
        "move": [f"Перевести: {p['fio']}", f"на должность «{_pos_name(ctx, p['pos'])}» в {dept.name if dept else ''}",
                 f"с {when}."],
        "fire": [f"Уволить: {p['fio']}", f"с должности «{_pos_name(ctx, p['pos'])}»",
                 f"последний рабочий день — {when}", "Бухгалтерии произвести окончательный расчёт."],
    }[p["kind"]]
    paras = [(ctx.cfg.name, "c"), (f"ПРИКАЗ № {p['order']}", "cb"), (f"от {ctx.today.strftime('%d.%m.%Y')}", "c"),
             (f"О {'приёме на работу' if p['kind'] == 'hire' else 'переводе' if p['kind'] == 'move' else 'увольнении'}", "b"),
             ("", "")] + [(x, "") for x in body] + [("", ""), ("Основание: решение директора, чек-лист процесса.", ""),
                                                   ("", ""), ("Директор ____________________", ""),
                                                   ("С приказом ознакомлен(а) ____________________", "")]
    return Reply(text=f"📄 Приказ №{p['order']}", files=[(f"Приказ {p['order']} {p['fio']}.docx", docx(paras))])


def docx(paras):
    """Простой документ Word без внешних библиотек: [(текст, стиль)], стиль — '' / 'b' жирный / 'c' по центру / 'cb'."""
    def para(text, st):
        ppr = '<w:pPr><w:jc w:val="center"/></w:pPr>' if "c" in st else ""
        rpr = '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr>' if "b" in st else '<w:rPr><w:sz w:val="26"/></w:rPr>'
        return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(para(t, s) for t, s in paras) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701"/>'
           '</w:sectPr></w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()
