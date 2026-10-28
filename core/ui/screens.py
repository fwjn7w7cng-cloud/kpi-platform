"""Экраны ядра: главная, карточка KPI, команда, отдел, рейтинг, месяц, табель, люди, замок, демо, помощь.

Экраны модулей лежат в modules/<модуль>.py и регистрируются так же.
Каждый экран — функция (ctx, *аргументы) → Reply. Права проверяет сам экран через ctx.sees / ctx.role.
"""
from datetime import date, timedelta

from core.access import editions as E
from core.access import roles as R
from core.calendar import day_title, month_of, month_title, shift_month
from core.config import INDUSTRIES
from core.kpi.engine import MAIN, SELLERS
from core.month import cycle
from core.ui.kit import METRIC_HEAD, Button, Reply, fmt, metric_row, money, pct, rows
from core.ui.registry import MENU, menu, screen, text_input
from core.ui.views import Card, Cell, Row, View, tone

LEADERS = {"sv", "head", "owner", "admin"}
ALL = set(R.ROLES) | {"admin"}


# ======================= главная =======================

menu("me", "📈 Мой план", {"rep", "newbie"}, order=10)
menu("team", "👥 Моя команда", {"sv"}, order=10)
menu("sales", "🏢 Отдел продаж", {"head", "owner", "admin"}, order=10,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")
menu("rating", "🏆 Рейтинг", {"rep", "newbie", "sv", "head", "owner", "admin"}, order=20,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")
menu("people", "👤 Люди", {"sv", "head", "owner", "admin"}, order=60)
menu("ts", "📅 Табель", {"sv", "head", "owner", "admin"}, order=65)
menu("month", "🗓 Месяц", {"head", "owner", "accountant", "admin"}, order=80)
menu("help", "❓ Как пользоваться", ALL, order=99)


@screen("home")
def home(ctx):
    if ctx.role == "guest":
        return ctx.ask("register", "👋 Здравствуйте! Вас пока нет в списке сотрудников.\n\n"
                                   "Напишите в ответ свои фамилию и имя — администратор подключит вас.", buttons=[])
    role_t = ctx.cfg.role_title(ctx.role) if ctx.role in R.ROLES else "Администратор"
    v = View(ctx.cfg.name, f"{ctx.name()} · {role_t} · {month_title(ctx.month).lower()}")
    v.tiles(_home_tiles(ctx))
    lines = _home_lines(ctx)
    if lines:
        v.lines(lines)
    if ctx.demo:
        v.note(f"Демо · {INDUSTRIES[ctx.industry].lower()} · редакция «{E.EDITIONS[ctx.cfg.edition]['title']}». "
               "Все люди и цифры выдуманы.")
    btns = []
    for m in MENU:
        if ctx.role not in m.roles:
            continue
        if m.depts and ctx.dept_kind not in m.depts:
            continue
        if m.when and not m.when(ctx):
            continue
        btns.append(Button(m.label, m.to) if ctx.can(m.module) else Button(m.label, lock=m.module))
    kb = rows(btns, 2)
    kb.append(_month_switch(ctx))
    if ctx.demo:
        kb.append([Button("🎭 Роль", "sw:role"), Button("🏭 Отрасль", "sw:ind"), Button("💼 Редакция", "sw:ed")])
    elif ctx.admin:
        kb.append([Button("👁 Смотреть как…", "viewas")])
    return Reply(view=v, buttons=kb)


def _home_tiles(ctx):
    k = ctx.kpi()
    if ctx.role in SELLERS and ctx.me:
        m = k.emp(ctx.me["id"])
        t = [("План продаж", pct(m["sales"].pct), m["sales"].tone), ("АКБ", fmt(m["akb"].fact, ""), m["akb"].tone)]
        if ctx.can("payroll"):
            t.append(("Зарплата сейчас", money(ctx.pay().calc(ctx.me).total), None))
        return t
    if ctx.role == "sv" and ctx.me:
        m = k.scope("team", ctx.me["team"])
        return [("План команды", pct(m["sales"].pct), m["sales"].tone), ("Продажи", money(m["sales"].fact), None)]
    if ctx.role in ("head", "owner", "admin", "accountant"):
        if ctx.role == "head" and ctx.dept_kind != "sales":
            return _staff_tiles(ctx)
        sales = next((d for d in ctx.cfg.departments if d.kind == "sales"), None)
        m = k.scope("dept", sales.id) if sales else None
        t = []
        if m:
            t = [("План отдела", pct(m["sales"].pct), m["sales"].tone), ("Продажи", money(m["sales"].fact), None)]
        if ctx.can("payroll") and ctx.role in ("owner", "admin", "accountant"):
            fund = sum(ctx.pay().calc(e).total for e in ctx.kpi().employees.values() if e["active"])
            t.append(("Фонд зарплаты", money(fund), None))
        return t[:3]
    return _staff_tiles(ctx)


def _staff_tiles(ctx):
    if not ctx.me:
        return []
    t = [("Рабочих дней", f"{ctx.pay().worked_days(ctx.me):g} из {ctx.kpi().cal.total}", None)]
    if ctx.can("payroll"):
        t.append(("Зарплата сейчас", money(ctx.pay().calc(ctx.me).total), None))
    return t


def _home_lines(ctx):
    k = ctx.kpi()
    cal = k.cal
    out = []
    if ctx.is_current():
        out.append(f"Рабочих дней: прошло {cal.passed} из {cal.total} · к сегодня нужно {pct(cal.need)} плана")
    else:
        out.append(f"Месяц {cycle.TITLES[cycle.status(ctx.db, ctx.month)]}")
    if ctx.role in SELLERS and ctx.me:
        m = k.emp(ctx.me["id"])["sales"]
        if m.plan and m.left and cal.left:
            out.append(f"До плана {money(m.left)} — по {money(m.per_day)} в день")
    return out


def _month_switch(ctx):
    prev = shift_month(ctx.month, -1)
    b = [Button(f"◀ {month_title(prev).split()[0]}", f"mon:{prev}")]
    if not ctx.is_current():
        nxt = shift_month(ctx.month, 1)
        b.append(Button(f"{month_title(nxt).split()[0]} ▶", f"mon:{nxt}"))
    return b


@screen("mon")
def set_month(ctx, month):
    cur = month_of(ctx.today)
    ctx.session["month"] = month if month <= cur else cur
    ctx.month = ctx.session["month"]
    ctx._kpi, ctx._pay = {}, {}
    return home(ctx)


@text_input("register")
def on_register(ctx, text, data):
    name = " ".join(text.split())[:80]
    reqs = ctx.db.get("register", {}) or {}
    reqs[str(ctx.user_id)] = {"name": name, "ts": ctx.today.isoformat()}
    ctx.db.put("register", reqs)
    msgs = [Reply(text=f"Спасибо, {name}! Заявка отправлена администратору.")]
    for a in ctx.app.admins:
        msgs.append(Reply(text=f"🆕 Заявка на подключение: {name}", to=a,
                          buttons=[[Button("👤 Разобрать заявки", "regs")]]))
    return msgs


@screen("regs")
def registrations(ctx, uid=None, emp=None):
    if not ctx.admin:
        return home(ctx)
    reqs = ctx.db.get("register", {}) or {}
    if uid and emp:
        ctx.db.c.execute("UPDATE employees SET tg_id = ? WHERE id = ?", (int(uid), int(emp)))
        reqs.pop(uid, None)
        ctx.db.put("register", reqs)
        e = ctx.db.employee(int(emp))
        return [Reply(text=f"✅ {e['fio']} подключён."), Reply(text="Вас подключили — нажмите /start", to=int(uid))]
    if uid:
        name = reqs.get(uid, {}).get("name", "")
        words = name.lower().split()
        cand = [e for e in ctx.db.employees() if not e["tg_id"] and any(w in e["fio"].lower() for w in words)]
        cand = cand or [e for e in ctx.db.employees() if not e["tg_id"]]
        return Reply(text=f"Кто это — «{name}»?", buttons=rows([Button(e["fio"], f"regs:{uid}:{e['id']}") for e in cand[:20]], 1))
    if not reqs:
        return Reply(text="Новых заявок нет.")
    return Reply(text="Заявки на подключение:", buttons=rows([Button(r["name"], f"regs:{u}") for u, r in reqs.items()], 1))


# ======================= KPI =======================

def kpi_view(ctx, emp, title=None):
    k = ctx.kpi()
    m = k.emp(emp["id"])
    pos = ctx.cfg.position(emp["position"])
    sub = f"{pos['name'] if pos else ''} · {month_title(ctx.month).lower()}"
    v = View(title or emp["fio"], sub)
    s = m["sales"]
    t = [("Продажи", money(s.fact), None), ("План", pct(s.pct), s.tone)]
    if ctx.is_current() and s.plan:
        t.append(("Прогноз", pct(s.forecast / s.plan), tone(s.forecast / s.plan)))
    v.tiles(t)
    rows_ = [metric_row(m[c]) for c in MAIN if c in m and (m[c].plan or m[c].fact)]
    v.table(METRIC_HEAD, rows_, title="Показатели")
    fx = [metric_row(x) for code, x in m.items() if code.startswith("f:")]
    if fx:
        v.table(["Свой показатель", "", "Значение", ""], [Row([r.cells[0].text, "", r.cells[2].text, ""]) for r in fx])
    if ctx.is_current() and s.plan and s.left:
        v.note(f"Чтобы выйти на план: {money(s.per_day)} в день, рабочих дней осталось {k.cal.left}.")
    return v


@screen("me")
def me(ctx):
    if not ctx.me or ctx.role not in SELLERS:
        return home(ctx)
    return emp_card(ctx, str(ctx.me["id"]))


@screen("emp")
def emp_card(ctx, emp_id):
    emp = ctx.db.employee(int(emp_id))
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа к этому сотруднику.")
    if emp["role"] not in SELLERS:
        from core.ui.registry import SCREENS
        return SCREENS["staffcard"][0](ctx, emp_id) if "staffcard" in SCREENS else Reply(text=emp["fio"])
    v = kpi_view(ctx, emp)
    i = emp["id"]
    b = [Button("📦 По группам", f"grp:{i}"), _mod(ctx, "💰 Зарплата", f"pay:{i}", "payroll"),
         _mod(ctx, "💵 Долги", f"debts:e:{i}", "debts"), _mod(ctx, "📞 Кому позвонить", f"call:{i}", "call_today"),
         _mod(ctx, "🏅 Достижения", f"ach:{i}", "achievements"), _mod(ctx, "🔮 Прогноз", f"fc:e:{i}", "forecast")]
    kb = rows(b, 2)
    if emp["team"] and ctx.scope in ("team", "dept", "all"):
        kb.append([Button("‹ Команда", f"team:{emp['team']}")])
    return Reply(view=v, buttons=kb)


def _mod(ctx, label, to, module):
    return Button(label, to) if ctx.can(module) else Button(label, lock=module)


@screen("grp")
def groups(ctx, emp_id):
    emp = ctx.db.employee(int(emp_id))
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    m = ctx.kpi().emp(emp["id"])
    focus = ctx.kpi().focus
    gs = [(c, x) for c, x in m.items() if c.startswith("grp:")]
    gs.sort(key=lambda t: (t[0][4:] not in focus, -t[1].fact))
    v = View("По группам товаров", f"{emp['fio']} · {month_title(ctx.month).lower()}")
    v.table(["Группа", "План", "Факт", "%"],
            [metric_row(x, ("⭐ " if c[4:] in focus else "") + x.title) for c, x in gs])
    v.note("⭐ — фокусные группы месяца")
    return Reply(view=v, buttons=[[Button("‹ Назад", f"emp:{emp['id']}")]])


def _team_ids(ctx, team):
    return ctx.kpi().members("team", team)


@screen("team")
def team(ctx, team_id=None):
    team_id = team_id or (ctx.me["team"] if ctx.me else "")
    t = next((x for x in ctx.cfg.teams if x.id == team_id), None)
    if not t:
        return sales(ctx)
    if ctx.scope == "team" and (not ctx.me or ctx.me["team"] != team_id) or ctx.scope == "self":
        return Reply(text="Нет доступа к этой команде.")
    k = ctx.kpi()
    m = k.scope("team", team_id)
    v = View(t.name, f"{month_title(ctx.month)} · к сегодня нужно {pct(k.cal.need)}")
    v.tiles([("Продажи", money(m["sales"].fact), None), ("План", pct(m["sales"].pct), m["sales"].tone),
             ("АКБ", fmt(m["akb"].fact, ""), m["akb"].tone)])
    ids = _team_ids(ctx, team_id)
    rs = []
    for i, x in k.rating(ids):
        e = k.employees[i]
        mm = k.emp(i)
        sub = "новичок" if e["role"] == "newbie" else ""
        if e.get("fired") and e["fired"] <= k.end:
            sub = f"уволен {e['fired'][8:10]}.{e['fired'][5:7]}"
        rs.append(Row([_short(e["fio"]), money(x.fact, ""), Cell(pct(x.pct), x.tone, bar=_bar(x)),
                       Cell(pct(mm["akb"].pct), mm["akb"].tone)], sub=sub))
    v.table(["Менеджер", "Продажи", "План", "АКБ"], rs, title="Команда")
    v.table(METRIC_HEAD, [metric_row(m[c]) for c in MAIN if m[c].plan or m[c].fact], title="Показатели команды")
    kb = rows([Button(_short(k.employees[i]["fio"]), f"emp:{i}") for i, _ in k.rating(ids)], 3)
    kb.append([_mod(ctx, "💵 Долги команды", f"debts:t:{team_id}", "debts"),
               _mod(ctx, "💰 Фонд команды", f"fund:t:{team_id}", "payroll")])
    if ctx.scope in ("dept", "all"):
        kb.append([Button("‹ Отдел продаж", "sales")])
    return Reply(view=v, buttons=kb)


@screen("sales")
def sales(ctx):
    if ctx.scope not in ("dept", "all"):
        return home(ctx)
    k = ctx.kpi()
    dept = next((d for d in ctx.cfg.departments if d.kind == "sales"), None)
    m = k.scope("dept", dept.id)
    v = View(dept.name, f"{month_title(ctx.month)} · к сегодня нужно {pct(k.cal.need)}")
    s = m["sales"]
    t = [("Продажи", money(s.fact), None), ("План", pct(s.pct), s.tone)]
    if ctx.is_current() and s.plan:
        t.append(("Прогноз", pct(s.forecast / s.plan), tone(s.forecast / s.plan)))
    v.tiles(t)
    trs = []
    for team_ in ctx.cfg.teams_of(dept.id):
        tm = k.scope("team", team_.id)["sales"]
        trs.append(Row([team_.name, money(tm.fact, ""), Cell(pct(tm.pct), tm.tone, bar=_bar(tm))]))
    v.table(["Команда", "Продажи", "План"], trs, title="Команды")
    v.table(METRIC_HEAD, [metric_row(m[c]) for c in MAIN if m[c].plan or m[c].fact], title="Показатели отдела")
    if k.unknown:
        v.note("Есть продажи людей, которых нет в списке: " + ", ".join(list(k.unknown)[:3]))
    kb = rows([Button(t_.name, f"team:{t_.id}") for t_ in ctx.cfg.teams_of(dept.id)], 2)
    kb += rows([Button("🏆 Рейтинг", "rating"), _mod(ctx, "💵 Долги", "debts:d", "debts"),
                _mod(ctx, "🔮 Прогноз", "fc:d", "forecast"), _mod(ctx, "🚨 Сторож", "watch", "watchdog")], 2)
    return Reply(view=v, buttons=kb)


@screen("rating")
def rating(ctx, metric="sales"):
    k = ctx.kpi()
    if ctx.scope == "team" and ctx.me:
        ids, where = k.members("team", ctx.me["team"]), "команда"
    elif ctx.role in SELLERS and ctx.me and ctx.me["team"]:
        ids, where = k.members("dept", ctx.me["dept"]), "отдел"
    else:
        ids, where = k.members("dept", next(d.id for d in ctx.cfg.departments if d.kind == "sales")), "отдел"
    titles = {"sales": "Продажи", "akb": "АКБ", "focus": "Фокусные", "collect": "Сбор"}
    v = View(f"🏆 Рейтинг · {titles.get(metric, metric)}", f"{where} · {month_title(ctx.month).lower()}")
    rs = []
    for n, (i, x) in enumerate(k.rating(ids, metric), 1):
        e = k.employees[i]
        me_ = ctx.me and e["id"] == ctx.me["id"]
        rs.append(Row([f"{n}. {_short(e['fio'])}" + (" ← вы" if me_ else ""), fmt(x.fact, x.unit),
                       Cell(pct(x.pct), x.tone, bar=_bar(x))], mark="good" if me_ else None))
    v.table(["Место", "Факт", "План"], rs)
    kb = [[Button(("• " if c == metric else "") + t, f"rating:{c}") for c, t in titles.items()]]
    return Reply(view=v, buttons=kb)


# ======================= люди =======================

@screen("people")
def people(ctx, dept_id=None):
    if ctx.scope == "self":
        return home(ctx)
    depts = [d for d in ctx.cfg.departments if ctx.scope == "all" or (ctx.me and d.id == ctx.me["dept"])]
    if not dept_id:
        if len(depts) == 1:
            dept_id = depts[0].id
        else:
            v = View("👤 Люди", "по отделам")
            v.table(["Отдел", "Человек"], [Row([d.name, str(len(ctx.db.employees(dept=d.id)))]) for d in depts])
            return Reply(view=v, buttons=rows([Button(d.name, f"people:{d.id}") for d in depts], 2))
    d = ctx.cfg.dept(dept_id)
    es = [e for e in ctx.db.employees(dept=dept_id) if ctx.sees(e)]
    v = View(f"👤 {d.name}", f"{len(es)} человек")
    v.table(["Сотрудник", "Должность"], [Row([e["fio"], (ctx.cfg.position(e["position"]) or {}).get("name", "")],
                                              sub="мессенджер не привязан" if not e["tg_id"] and not ctx.demo else "")
                                          for e in es])
    kb = rows([Button(_short(e["fio"]), f"emp:{e['id']}") for e in es], 3)
    if len(depts) > 1:
        kb.append([Button("‹ Отделы", "people")])
    return Reply(view=v, buttons=kb)


@screen("viewas")
def view_as(ctx, emp_id=None):
    if not ctx.admin:
        return home(ctx)
    if emp_id == "off":
        ctx.session.pop("as", None)
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if emp_id:
        ctx.session["as"] = int(emp_id)
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    es = ctx.db.employees()
    kb = rows([Button(f"{_short(e['fio'])} · {ctx.cfg.role_title(e['role'])}", f"viewas:{e['id']}") for e in es[:40]], 2)
    kb.append([Button("↩️ Снова админ", "viewas:off")])
    return Reply(text="Смотреть бота глазами сотрудника:", buttons=kb)


# ======================= табель =======================

TS_KINDS = {"late": "⏰ Опоздание", "absent": "✗ Пропуск", "truancy": "🚫 Прогул", "half": "½ Полдня",
            "extra": "💼 Работал в выходной", "fine": "💸 Штраф", "comment": "💬 Комментарий"}


@screen("ts")
def timesheet(ctx, emp_id=None):
    if emp_id:
        emp = ctx.db.employee(int(emp_id))
        if not emp or not ctx.sees(emp):
            return Reply(text="Нет доступа.")
        marks = ctx.db.timesheet(ctx.kpi().cal.start, ctx.kpi().cal.end, emp["id"])
        v = View(f"📅 Табель · {emp['fio']}", month_title(ctx.month))
        p = ctx.pay()
        v.tiles([("Отработано дней", f"{p.worked_days(emp):g}", None), ("Пропусков", str(p.missed_days(emp)),
                  "bad" if p.missed_days(emp) else "good")])
        v.table(["День", "Отметка", "Кто"], [Row([day_title(date.fromisoformat(r["day"])), _ts_label(r), r["who"][:20]])
                                             for r in marks]) if marks else v.note("Все дни — как обычно.")
        kb = []
        if ctx.scope != "self" and cycle.can_edit(ctx.db, ctx.month):
            kb.append([Button("➕ Отметить день", f"tsa:{emp['id']}")])
        return Reply(view=v, buttons=kb)
    marks = [r for r in ctx.db.timesheet(ctx.kpi().cal.start, ctx.kpi().cal.end) if ctx.sees(r["emp_id"])]
    v = View("📅 Табель", f"{month_title(ctx.month)} · отклонения от обычного дня")
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    v.table(["День", "Сотрудник", "Отметка"], [Row([day_title(date.fromisoformat(r["day"])), _short(names.get(r["emp_id"], "?")),
                                                    _ts_label(r)]) for r in marks[-30:]]) if marks else v.note("Отметок нет.")
    people_ = [e for e in ctx.db.employees() if ctx.sees(e)]
    kb = rows([Button(_short(e["fio"]), f"ts:{e['id']}") for e in people_[:30]], 3)
    return Reply(view=v, buttons=kb)


def _ts_label(r):
    s = TS_KINDS.get(r["kind"], r["kind"])
    if r["kind"] == "late":
        s += f" {r['minutes']} мин"
    if r["kind"] == "fine" and r["amount"]:
        s += f" {money(r['amount'])}"
    if r["note"]:
        s += f" · {r['note']}"
    return s


@screen("tsa")
def ts_add(ctx, emp_id, kind=None, day=None, extra=None):
    emp = ctx.db.employee(int(emp_id))
    if not emp or not ctx.sees(emp) or ctx.scope == "self":
        return Reply(text="Нет доступа.")
    if not cycle.can_edit(ctx.db, ctx.month):
        return Reply(text="Месяц утверждён — табель закрыт.")
    if not kind:
        return Reply(text=f"Что отметить у {emp['fio']}?",
                     buttons=rows([Button(t, f"tsa:{emp_id}:{k}") for k, t in TS_KINDS.items()], 2))
    if not day:
        days = [ctx.today - timedelta(days=i) for i in range(0, 7)]
        days = [d for d in days if month_of(d) == ctx.month] or [ctx.kpi().cal.end]
        return Reply(text="Какой день?", buttons=rows([Button(day_title(d), f"tsa:{emp_id}:{kind}:{d.isoformat()}")
                                                         for d in days], 3))
    if kind == "late" and not extra:
        return Reply(text="Насколько опоздал?", buttons=[[Button(f"{m} мин", f"tsa:{emp_id}:late:{day}:{m}") for m in (10, 30, 60, 120)]])
    if kind in ("fine", "comment") and not extra:
        return ctx.ask("ts_text", "Напишите " + ("сумму и причину: «500 курил в зале»" if kind == "fine" else "комментарий"),
                       {"emp": int(emp_id), "kind": kind, "day": day})
    ctx.db.add_timesheet(day, int(emp_id), kind, minutes=int(extra) if kind == "late" else 0, who=ctx.author())
    ctx.db.audit(ctx.author(), "timesheet", f"{emp['fio']} {day} {kind} {extra or ''}")
    return timesheet(ctx, emp_id)


@text_input("ts_text")
def on_ts_text(ctx, text, data):
    amount, note = 0, text.strip()
    if data["kind"] == "fine":
        parts = note.split(maxsplit=1)
        try:
            amount = float(parts[0].replace(" ", "").replace(",", "."))
            note = parts[1] if len(parts) > 1 else "штраф"
        except ValueError:
            return ctx.ask("ts_text", "Сначала сумма, потом причина: «500 курил в зале»", data)
    ctx.db.add_timesheet(data["day"], data["emp"], data["kind"], amount=amount, note=note[:200], who=ctx.author())
    return timesheet(ctx, str(data["emp"]))


# ======================= месяц =======================

@screen("month")
def month_screen(ctx, action=None):
    st = cycle.status(ctx.db, ctx.month)
    may_manage = ctx.role in ("owner", "admin")
    may_review = may_manage or ctx.role == "head"
    out = []
    if action == "review" and may_review and st == "open":
        cycle.advance(ctx.db, ctx.month, ctx.author())
        out.append(Reply(text=f"🗓 {month_title(ctx.month)} — на согласовании. Новые автоматические отчёты за него больше не принимаются."))
    elif action == "approve" and may_manage and st != "approved":
        snap = cycle.approve(ctx.db, ctx.month, ctx.author(), ctx.pay())
        out.append(Reply(text=f"✅ {month_title(ctx.month)} утверждён. Сотрудников в расчёте: {len(snap)}. "
                              "Табель и правки закрыты."))
    elif action == "reopen" and may_manage and st == "approved":
        return ctx.ask("reopen", "Почему открываем месяц заново? Напишите причину — она попадёт в журнал.")
    elif action == "xlsx" and ctx.can("payroll") and ctx.role in ("owner", "accountant", "admin"):
        slips = [ctx.pay().calc(e) for e in ctx.kpi().employees.values()]
        data = cycle.registry_xlsx(ctx.cfg, ctx.month, slips)
        return Reply(text="📑 Ведомость", files=[(f"Ведомость {month_title(ctx.month)}.xlsx", data)])
    st = cycle.status(ctx.db, ctx.month)
    v = View(f"🗓 {month_title(ctx.month)}", f"Статус: {cycle.TITLES[st]}")
    steps = [("Открыт", "идут продажи, вносится табель и правки"), ("На согласовании", "проверяем табель и правки"),
             ("Утверждён", "всё закрыто, ведомость готова")]
    idx = ["open", "review", "approved"].index(st)
    v.cards([Card(("Сейчас: " if i == idx else "✓ " if i < idx else "") + a, "", [b],
                  color="good" if i == idx else None) for i, (a, b) in enumerate(steps)])
    wait = cycle.edits(ctx.db, ctx.month, "wait")
    if wait:
        v.note(f"Правок ждут решения: {len(wait)}")
    kb = []
    if st == "open" and may_review:
        kb.append([Button("📨 На согласование", "month:review")])
    if st != "approved" and may_manage:
        kb.append([Button("✅ Утвердить месяц", "month:approve")])
    if st == "approved" and may_manage:
        kb.append([Button("↩️ Открыть заново", "month:reopen")])
    kb.append([_mod(ctx, "✏️ Правки", "edits", "payroll"), _mod(ctx, "📑 Ведомость Excel", "month:xlsx", "payroll")])
    out.append(Reply(view=v, buttons=kb))
    return out


@text_input("reopen")
def on_reopen(ctx, text, data):
    cycle.reopen(ctx.db, ctx.month, ctx.author(), text.strip()[:300])
    return month_screen(ctx)


# ======================= замок, демо, помощь =======================

@screen("lock")
def lock(ctx, module):
    title = E.MODULES.get(module, (module,))[0]
    since = E.min_edition(module) if module in E.MODULES else None
    v = View(f"🔒 {title}", E.lock_text(module) if module in E.MODULES else "")
    v.lines([LOCK_TEXT.get(module, "Этот раздел подключается отдельно."),
             f"Входит в редакцию «{E.EDITIONS[since]['title']}» и выше." if since else
             "Не входит в редакции — подключается отдельным модулем."])
    kb = []
    if ctx.demo:
        kb.append([Button("💼 Сменить редакцию", "sw:ed")])
    return Reply(view=v, buttons=kb)


LOCK_TEXT = {
    "debts": "Долги клиентов по срокам, просрочка 30+ и 60+, кто должен больше всех — у каждого торгового и по команде.",
    "payroll": "Зарплата каждого сотрудника считается сама по отчётам и табелю: оклад, проценты, бонусы, штрафы.",
    "reminders": "Бот сам напоминает: кто отстаёт от плана, сколько нужно в день, что не сделано.",
    "achievements": "Медали и доска почёта за план, АКБ, сбор денег — соревнование без лишних слов.",
    "staff": "Кабинеты склада, водителей и офиса: свой табель, своя зарплата, свои задачи.",
    "tasks": "Руководитель ставит задачу — сотрудник отчитывается кнопкой, руководитель видит, что сделано.",
    "approval": "Цепочка: руководитель отдела предлагает премию или удержание с причиной — директор утверждает.",
    "audit": "Журнал: кто, что и когда менял в табеле, правках и месяце.",
    "watchdog": "«Ночной сторож» смотрит цифры и будит, если что-то не так: просели продажи, пропал клиент, выросли скидки.",
    "forecast": "Прогноз выполнения плана на конец месяца и сравнение команд и отделов.",
    "call_today": "Список на сегодня: кому позвонить — заснувшие клиенты, должники, точки маршрута без заказа.",
    "formulas": "Свои показатели компании по формуле: средний чек, сбор к продажам — что угодно из готовых цифр.",
    "ai": "Аналитик сам находит главное в цифрах и пишет простыми словами, на что обратить внимание.",
    "miniapp": "Красивая панель с графиками внутри мессенджера, как отдельное приложение.",
    "direct_1c": "Данные из 1С напрямую, почти в реальном времени, без выгрузок на почту.",
    "branches": "Несколько филиалов с общим рейтингом и сводкой по компании.",
    "branding": "Бот в цветах и с логотипом компании.",
}


@screen("sw")
def demo(ctx, what=None, value=None):
    if not ctx.demo:
        return home(ctx)
    if what == "role" and value:
        ctx.session["role"], ctx.session["as"] = value, None
        if value == "rep" and ctx.session.get("pick"):
            ctx.session["as"] = int(ctx.session.pop("pick"))
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "as" and value:
        e = ctx.db.employee(int(value))
        ctx.session["role"], ctx.session["as"] = (e["role"] if e["role"] != "newbie" else "rep"), e["id"]
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "role":
        roles = [("rep", "Торговый"), ("sv", "Супервайзер"), ("head", "Руководитель продаж"), ("owner", "Директор"),
                 ("accountant", "Бухгалтер"), ("staff", "Склад / водитель"), ("admin", "Администратор")]
        kb = rows([Button(t, f"sw:role:{r}") for r, t in roles], 2)
        kb.append([Button("👤 Выбрать конкретного человека", "sw:who")])
        return Reply(text="🎭 Чьими глазами смотрим?", buttons=kb)
    if what == "who":
        es = ctx.db.employees()
        return Reply(text="Выберите сотрудника:", buttons=rows([Button(f"{_short(e['fio'])} · "
                     f"{(ctx.cfg.position(e['position']) or {}).get('name', '')[:14]}", f"sw:as:{e['id']}") for e in es], 2))
    if what == "ind" and value:
        ctx.session["industry"] = value
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "ind":
        return Reply(text="🏭 Отрасль демо-фирмы:", buttons=rows([Button(("• " if k == ctx.industry else "") + t, f"sw:ind:{k}")
                                                                   for k, t in INDUSTRIES.items()], 2))
    if what == "ed" and value:
        ctx.session["edition"] = value
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "ed":
        lines = []
        for code in E.ORDER:
            mods = E.edition_modules(code) - (E.edition_modules(E.ORDER[E.ORDER.index(code) - 1]) if E.ORDER.index(code) else set())
            lines.append(f"{E.EDITIONS[code]['title']} (до {E.EDITIONS[code]['users']} чел.): "
                         + ", ".join(E.MODULES[m][0] for m in sorted(mods)))
        v = View("💼 Редакции", "что входит в каждую")
        v.lines(lines)
        return Reply(view=v, buttons=[[Button(("• " if c == ctx.cfg.edition else "") + E.EDITIONS[c]["title"], f"sw:ed:{c}")
                                       for c in E.ORDER], [Button("🔄 Начать демо заново", "sw:reset")]])
    if what == "reset":
        ctx.app.reset_demo(ctx.industry)
        ctx.session = {"industry": ctx.industry}
        ctx.save()
        return [Reply(text="🔄 Демо заполнено заново."), home(ctx.app.ctx(ctx.user_id))]
    return home(ctx)


HELP = {
    "rep": ["📈 Мой план — продажи, АКБ, SKU, сбор денег, визиты: план, факт и сколько нужно в день.",
            "💰 Зарплата — сколько уже заработано и за что, строка за строкой.",
            "📞 Кому позвонить — список на сегодня: должники, заснувшие клиенты, маршрут без заказа.",
            "🏆 Рейтинг — ваше место в отделе."],
    "sv": ["👥 Моя команда — каждый торговый: план, АКБ, нажмите фамилию — его карточка.",
           "📅 Табель — опоздания, пропуски, работа в выходной.", "📌 Задачи — поставить задачу своим."],
    "head": ["🏢 Отдел — команды и показатели отдела.", "✏️ Правки — предложить премию или удержание с причиной.",
             "🗓 Месяц — отправить месяц на согласование."],
    "owner": ["🏢 Отдел продаж — вся картина: команды, прогноз, сторож.", "💰 Фонд — зарплата по отделам.",
              "🗓 Месяц — утвердить месяц: правки решены, ведомость уходит бухгалтеру."],
    "accountant": ["🗓 Месяц → 📑 Ведомость Excel — расчёт всех сотрудников.", "💰 Фонд — суммы по отделам."],
    "staff": ["💰 Моя зарплата — дни и начисления.", "📅 Мой табель — отметки за месяц.", "🌴 Отпуск — заявка на отпуск или отгул."],
    "admin": ["Всё, что видят остальные, плюс 👁 «Смотреть как» — бот глазами любого сотрудника."],
}


@screen("help")
def help_screen(ctx):
    role = "rep" if ctx.role == "newbie" else ctx.role
    v = View("❓ Как пользоваться", ctx.cfg.role_title(role) if role in R.ROLES else "Администратор")
    v.lines(HELP.get(role, HELP["staff"]) + ["🏠 — всегда вернёт на главную.",
                                             "◀ Месяц — посмотреть прошлые месяцы.",
                                             "🔒 — раздел есть, но не входит в вашу редакцию: нажмите, чтобы узнать, что в нём."])
    return Reply(view=v)


# ======================= мелочи =======================

def _short(fio):
    p = fio.split()
    return f"{p[0]} {p[1][0]}." if len(p) > 1 else fio


def _bar(m):
    if m.pct is None or m.lower_better:
        return None
    return (min(m.pct, 1.5) / 1.5, min(m.need, 1.5) / 1.5)


short = _short
bar = _bar
mod_button = _mod
