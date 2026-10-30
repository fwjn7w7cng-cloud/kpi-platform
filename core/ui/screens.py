"""Экраны ядра: главная, карточка KPI, команда, отдел, рейтинг, месяц, табель, люди, замок, демо, помощь.

Экраны модулей лежат в modules/<модуль>.py и регистрируются так же.
Каждый экран — функция (ctx, *аргументы) → Reply. Права проверяет сам экран через ctx.sees / ctx.role.
"""
from datetime import date, timedelta

from core.access import editions as E
from core.access import roles as R
from core.calendar import MONTHS_GEN, day_title, month_of, month_title, shift_month
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
                                   "Вход — по ссылке-приглашению: попросите её у администратора или руководителя и откройте "
                                   "на этом телефоне. Или напишите в ответ свои фамилию и имя — администратор подключит вас.",
                       buttons=[])
    if ctx.me and ctx.role not in ("owner", "admin") and not ctx.cfg.dept_on(ctx.me["dept"]):
        return _dept_off(ctx)
    role_t = ctx.cfg.role_title(ctx.role) if ctx.role in R.ROLES else "Администратор"
    if ctx.role in ("staff", "head") and ctx.position_name:
        role_t = ctx.position_name
    v = View(ctx.cfg.name, f"{ctx.name()} · {role_t} · {month_title(ctx.month).lower()}")
    v.tiles(_home_tiles(ctx))
    lines = _home_lines(ctx)
    if lines:
        v.lines(lines)
    from core.calendar import in_ramadan, ramadan
    if in_ramadan(ctx.db, ctx.today):
        r = ramadan(ctx.db)
        v.note(f"🌙 Рамадан мубарак! До {_dm(r['end'])}: рассылки в {r['morning']} и {r['evening']}"
               + (", опоздания без штрафа." if r.get("late_free") else "."))
    if ctx.demo:
        v.note(f"Демо · {INDUSTRIES[ctx.industry].lower()} · набор «{ctx.cfg.set_title}». Все люди и цифры выдуманы.")
        from core.demo import is_partner
        if is_partner(ctx):
            v.note("🤝 Вы партнёр: «📘 Инструкция» — что нажимать, «🤝 Мои клиенты» — приглашения, анкеты и заявки, "
                   "«🔄 Заново» — свежие цифры перед каждым показом.")
    # Что уже есть в нижнем меню, на главной не повторяем (переключатели демо, «Смотреть как», отзывы — тоже там).
    kb = rows(_dept_buttons(ctx), 2) + rows(_home_buttons(ctx), 2)
    if ctx.demo and ctx.app.demo_gate and ctx.user_id not in ctx.app.admins:
        from core.demo import is_partner
        if not is_partner(ctx):                      # гость-клиент: анкета для подключения всегда под рукой
            kb.append([Button("🧩 Собрать и цена", "sw:ed"), Button("📋 Анкета для подключения", "cd")])
    kb.append(_month_switch(ctx))
    return Reply(view=v, buttons=kb)


DEPT_ICON = {"sales": "🏢", "warehouse": "📦", "logistics": "🚚", "purchasing": "🛒", "office": "🏛"}
PKG_MODULE = {"sales": "kpi", "stock": "stock", "logistics": "logistics", "office": "hr"}   # замок отдела → его пакет


def _dept_off(ctx):
    """Сотрудник отдела, чей пакет не куплен: в приложение не входит."""
    d = ctx.cfg.dept(ctx.me["dept"])
    pkg = E.dept_package(d.kind) if d else None
    title = E.PACKAGES[pkg]["title"] if pkg else "этого отдела"
    v = View(ctx.cfg.name, f"{ctx.name()} · {ctx.position_name or ctx.cfg.role_title(ctx.role)}")
    v.lines([f"Отдел «{d.name if d else ''}» пока не подключён к приложению: пакет «{title}» не куплен.",
             "Как только директор подключит пакет, здесь появятся ваши экраны: табель, зарплата, задачи."])
    kb = [[Button("🧩 Собрать", "sw:ed")]] if ctx.demo else []
    return Reply(view=v, buttons=kb)


def _dept_buttons(ctx):
    """Все отделы — каждый своей кнопкой (тому, кто видит всю фирму). Продажи → экран отдела продаж,
    остальные → экран отдела из модуля кабинетов (deptv:<отдел>)."""
    if ctx.scope != "all" or ctx.role not in ("owner", "admin"):
        return []
    out = []
    for d in ctx.cfg.departments:
        label = f"{DEPT_ICON.get(d.kind, '🏬')} {d.name}"
        if not ctx.cfg.dept_on(d.id):
            out.append(Button(label, lock=PKG_MODULE.get(E.dept_package(d.kind), "staff")))
        elif d.kind == "sales":
            out.append(Button(label, "sales"))
        else:
            out.append(_mod(ctx, label, f"deptv:{d.id}", "staff"))
    return out


# Длинная главная (больше MENU_MAX кнопок) собирается в группы: на главной — самое частое и кнопки групп,
# внутри группы — её пункты. Пункт без группы всегда на главной; группа из одного пункта не создаётся.
MENU_MAX = 12
MENU_GROUPS = [
    ("sales", "📈 Продажи и клиенты", {"rating", "cli", "rtd", "alerts", "fc", "cmp", "debts", "rv", "watch", "achb",
                                       "fx", "call"}),
    ("money", "💰 Зарплата, планы, месяц", {"fund", "month", "pl", "log", "tf", "up"}),
    ("ops", "📦 Закупки, склад, логистика", {"sk", "sow", "sol", "lg", "lgk", "wrc", "wsh", "fu"}),
    ("team", "👥 Люди и задачи", {"people", "pa", "ts", "tasks", "qz", "rmd", "hr"}),
    ("more", "⭐ Максимум", {"direct_1c", "branches", "branding", "custom"}),
]
TOP_FOR_SELLERS = {"call", "cli"}         # торговому это нужно каждый день — не прятать в группу


def _menu_items(ctx):
    out = []
    for m in MENU:
        if ctx.role not in m.roles:
            continue
        if m.depts and ctx.dept_kind not in m.depts:
            continue
        if m.when and not m.when(ctx):
            continue
        out.append((m, Button(m.label, m.to) if ctx.can(m.module) else Button(m.label, lock=m.module)))
    return out


def _group_of(ctx, m):
    if ctx.role in SELLERS and m.to in TOP_FOR_SELLERS:
        return None
    return next((g for g, _, tos in MENU_GROUPS if m.to in tos), None)


HOME_SKIP = {"sales", "deptv", "sk", "lg", "wrc", "fu"}   # у владельца и админа они внутри кнопок отделов


def _home_items(ctx):
    """Пункты главной без тех, что уже в нижнем меню или заменены кнопками отделов."""
    skip = {to for row in bottom_rows(ctx) for _, to in row}
    if _dept_buttons(ctx):
        skip |= HOME_SKIP
    return [(m, b) for m, b in _menu_items(ctx) if m.to not in skip]


def _home_buttons(ctx):
    items = _home_items(ctx)
    if len(items) <= MENU_MAX:
        return [b for _, b in items]
    groups = {}
    for m, b in items:
        g = _group_of(ctx, m)
        if g:
            groups.setdefault(g, []).append(b)
    top = [b for m, b in items if not _group_of(ctx, m) or len(groups[_group_of(ctx, m)]) == 1]
    helpers = [b for b in top if b.to in ("help", "fb", "fbl")]
    top = [b for b in top if b not in helpers]
    grp = [Button(title, f"mg:{g}") for g, title, _ in MENU_GROUPS if len(groups.get(g, [])) > 1]
    return top + grp + helpers


@screen("mg")
def menu_group(ctx, gid):
    title = next((t for g, t, _ in MENU_GROUPS if g == gid), None)
    bs = [b for m, b in _home_items(ctx) if _group_of(ctx, m) == gid]
    if not title or not bs:
        return home(ctx)
    return Reply(text=title, buttons=rows(bs, 2))


# ======================= нижнее меню =======================
# Постоянные кнопки под полем ввода: самое нужное для роли + в демо переключатели. Нажатие приходит текстом —
# App.text узнаёт подпись и открывает экран. Модуль не в редакции → экран сам покажет замок.

BOTTOM = {
    "rep":   [("📈 Мой план", "me"), ("🚗 Маршрут", "rt"), ("😴 Клиенты", "cli"), ("💰 Зарплата", "pay")],
    "sv":    [("👥 Команда", "team"), ("📋 Отчёты дня", "rtd"), ("💵 Долги", "debts"), ("📌 Задачи", "tasks")],
    "head":  [("🏢 Отдел", "sales"), ("📋 Планы", "pl"), ("🗓 Месяц", "month"), ("📌 Задачи", "tasks")],
    "owner": [("💰 Фонд", "fund"), ("✅ Утвердить", "appr"), ("🗓 Месяц", "month")],
    "accountant": [("🗓 Месяц", "month"), ("💰 Фонд", "fund"), ("📥 Отчёты", "up")],
    "staff": [("🧑‍🔧 Кабинет", "cab"), ("💰 Зарплата", "pay")],
    "admin": [("👥 Сотрудники", "pa"), ("📥 Отчёты", "up"), ("💬 Отзывы", "fbl"), ("🗓 Месяц", "month"), ("👁 Смотреть как", "viewas")],
}
HEAD_OTHER = [("👤 Люди", "people"), ("📅 Табель", "ts"), ("📌 Задачи", "tasks")]
# Отделы вне продаж: у начальника и у сотрудника — своё, по виду отдела и обязанности (водитель, диспетчер, кадры).
# Пункт закрытого модуля открывает замок с объяснением — как и на главной.
BOTTOM_DEPT = {
    ("head", "purchasing"): [("📦 Склад", "sk"), ("✅ Согласовать", "sow"), ("🛒 Заказы", "sol"), ("📅 Табель", "ts")],
    ("staff", "purchasing"): [("📦 Мои поставщики", "sk"), ("🛒 Заказы", "sol"), ("💰 Зарплата", "pay")],
    ("head", "warehouse"): [("📥 Приёмка", "wrc"), ("📦 Отгрузки", "wsh"), ("📅 Табель", "ts"), ("👤 Люди", "people")],
    ("staff", "warehouse"): [("📥 Приёмка", "wrc"), ("📦 Отгрузки", "wsh"), ("💰 Зарплата", "pay")],
    ("head", "logistics"): [("🚚 Рейсы", "lg"), ("📝 Отметки", "lgk"), ("⛽ ГСМ", "fu"), ("📅 Табель", "ts")],
    ("dispatcher", ""): [("🚚 Рейсы", "lg"), ("📝 Отметки", "lgk"), ("⛽ ГСМ", "fu")],
    ("driver", ""): [("🚚 Мой день", "dm"), ("📋 Мои рейсы", "lgd"), ("💰 Зарплата", "pay")],
    ("hr", ""): [("👤 Кадры", "hr"), ("🧑‍🔧 Кабинет", "cab"), ("💰 Зарплата", "pay")],
}
MODULE_OF = {"sk": "stock", "sow": "stock", "sol": "stock", "wrc": "warehouse", "wsh": "warehouse", "lg": "logistics",
             "lgk": "logistics", "fu": "fuel", "dm": "logistics", "lgd": "logistics", "hr": "hr", "cab": "staff",
             "pay": "payroll"}


def _dept_bottom(ctx):
    if ctx.role not in ("head", "staff") or ctx.dept_kind == "sales":
        return None
    if ctx.role == "staff" and ctx.duty:
        return BOTTOM_DEPT.get((ctx.duty, ""))
    return BOTTOM_DEPT.get((ctx.role, ctx.dept_kind))


def bottom_rows(ctx):
    """[[(подпись, экран)]] нижнего меню этого зрителя."""
    if ctx.role == "guest":
        return []
    role = "rep" if ctx.role == "newbie" else ctx.role
    items = _dept_bottom(ctx) or (HEAD_OTHER if role == "head" and ctx.dept_kind != "sales" else BOTTOM.get(role, BOTTOM["staff"]))
    if ctx.me and ctx.role not in ("owner", "admin") and not ctx.cfg.dept_on(ctx.me["dept"]):
        items = []                                    # отдел не подключён — только главная и «не работает»
    if ctx.role == "owner" and ctx.can("hr"):
        items = items + [("👤 Кадры", "hr")]
    if ctx.role == "newbie":
        items = [("🎓 Обучение", "qz")] + items[:3]
    fb = ("💬 Отзывы", "fbl") if ctx.role == "admin" else \
        ("🐞 Не работает", "fb") if ctx.role in ("rep", "newbie", "staff") else ("💬 Предложение", "fb")
    items = [("🏠 Главная", "home")] + [x for x in items if x != fb] + [fb]
    out = [items[i:i + 3] for i in range(0, len(items), 3)]
    if ctx.demo:
        out.append([("🎭 Роль", "sw:role"), ("🏭 Отрасль", "sw:ind"), ("🧩 Собрать", "sw:ed")])
        if ctx.user_id in ctx.app.admins and ctx.app.demo_gate:
            out.append([("🛠 Пульт", "adm")])
        from core.demo import is_partner
        if is_partner(ctx):
            out.append([("🤝 Мои клиенты", "ptn"), ("📘 Инструкция", "ptm"), ("🔄 Заново", "sw:reset")])
    return out


def bottom_keyboard(ctx):
    return [[label for label, _ in row] for row in bottom_rows(ctx)]


def bottom_target(ctx, text):
    t = (text or "").strip()
    return next((to for row in bottom_rows(ctx) for label, to in row if label == t), None)


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
    for a in ctx.app.admin_ids(ctx.db):
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
    tail = [Button("‹ Отделы", "people")] if len(depts) > 1 else []
    if ctx.role in ("admin", "owner", "head"):
        tail.append(Button("👥 Сотрудники и доступ", f"pa:{dept_id}"))
    if tail:
        kb.append(tail)
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
    from core.access import pricing as P
    title = E.MODULES.get(module, (module,))[0]
    v = View(f"🔒 {title}", E.lock_text(module) if module in E.MODULES else "")
    lines = [LOCK_TEXT.get(module, "Этот раздел подключается отдельно.")]
    if module in E.MODULES:
        pkg, level = E.package_of(module)
        pr = P.prices(ctx.office)
        if pkg == "sales":
            lines.append(f"Пакет «Продажи» уровня «{E.LEVELS[level]}»: {P.fmt(pr['sales_' + level])} ₽ в месяц "
                         f"+ {P.fmt(pr['sales_' + level + '_user'])} ₽ за человека.")
        elif pkg == "max":
            lines.append(f"Пакет «Максимум»: {P.fmt(pr['max'])} ₽ в месяц — всё сразу, вместе с функциями на заказ.")
        elif pkg in ("stock", "logistics", "office"):
            lines.append(f"Пакет «{E.PACKAGES[pkg]['title']}»: {P.fmt(pr[pkg])} ₽ в месяц "
                         f"+ {P.fmt(pr[pkg + '_user'])} ₽ за человека в отделе.")
        elif pkg == "ops":
            lines.append("Входит в пакеты «Склад и закупки», «Логистика» и «Кадры и офис».")
    v.lines(lines)
    kb = []
    if ctx.demo:
        kb.append([Button("🧩 Собрать", "sw:ed")])
    elif ctx.role in ("owner", "admin"):
        kb.append([Button("💳 Тариф", "tf")])
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
    "clients": "Кто из клиентов перестал брать, кто берёт меньше, кто растёт, какие бренды бросили — с карточкой клиента по коду.",
    "route": "Маршрут на день по точкам и отчёт торгового вечером: продал, был без заказа, не застал — СВ видит всё сразу.",
    "quiz": "Тест по продукции раз в неделю и курс для новичка: уроки, вопросы, результаты у руководителя.",
    "plans": "Руководитель продаж готовит планы — директор утверждает — каждый получает свой план 1-го числа.",
    "stock": "Закупки: что закончилось и скоро закончится, что лежит; заказ поставщику сам из «нужно», согласование по сумме, Excel поставщику.",
    "logistics": "Рейсы водителей: пробег, вес, точки, двойные и дальние рейсы, доставка к продажам; «Мой день» водителя и сдельная оплата.",
    "returns": "Возвраты по причинам: чья ошибка — торгового, склада, водителя; удержания в зарплате.",
    "warehouse": "Склад: приёмка поставок по заказу (пришло / недостача), отгрузки и ошибки сборки, бонус «без ошибок».",
    "hr": "Кадры: приём, перевод и увольнение по чек-листу, кто за какой пункт отвечает, приказ файлом.",
    "fuel": "ГСМ: сколько топлива положено водителю по километрам и сколько ушло по топливной карте.",
    "direct_1c": "Данные из 1С напрямую, почти в реальном времени, без выгрузок файлов; заказ поставщику — сразу в 1С.",
    "branches": "Несколько филиалов и юрлиц в одном приложении: общий рейтинг и сводка по всей компании.",
    "branding": "Приложение в цветах компании: свой логотип и название на значке телефона, свой адрес.",
    "custom": "До 3 новых функций в месяц под вашу компанию — сделаем с нуля, каждая до одного рабочего дня.",
}


@screen("sw")
def demo(ctx, what=None, value=None, *more):
    if not ctx.demo:
        return home(ctx)
    if what == "role" and value:
        ctx.session["role"], ctx.session["as"] = value, None
        if value == "rep" and ctx.session.get("pick"):
            ctx.session["as"] = int(ctx.session.pop("pick"))
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "pos" and value:
        e = next((x for x in ctx.db.employees() if x["position"] == value), None)
        if e:
            ctx.session["role"], ctx.session["as"] = (e["role"] if e["role"] != "newbie" else "rep"), e["id"]
            ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "as" and value:
        e = ctx.db.employee(int(value))
        ctx.session["role"], ctx.session["as"] = (e["role"] if e["role"] != "newbie" else "rep"), e["id"]
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    if what == "role":
        return Reply(text="🎭 Чьими глазами смотрим?\n\nПродажи и руководство — по ролям, остальные отделы — по должностям.",
                     buttons=_role_picker(ctx))
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
    from core.ui import tariff as T
    if what == "ed" and value in E.PRESETS:
        return T.set_packages(ctx, E.PRESETS[value]["packages"])
    if what == "ed":
        return T.build(ctx)
    if what == "pk" and value:
        return T.toggle(ctx, value, more[0] if more else None)
    if what == "pn":
        return T.reset_counts(ctx) if value == "reset" else T.ask_counts(ctx)
    if what == "yr":
        return T.toggle_year(ctx)
    if what == "pdf":
        return T.pdf(ctx)
    if what == "pay":
        return T.pay_screen(ctx)
    if what == "py" and value:
        return T.pay_toggle(ctx, value)
    if what == "req":
        return T.request(ctx)
    if what == "reset":
        ctx.app.reset_demo(ctx.industry, ctx.user_id)
        ctx.session = {"industry": ctx.industry}
        ctx.save()
        return [Reply(text="🔄 Демо заполнено заново."), home(ctx.app.ctx(ctx.user_id))]
    return home(ctx)


def _role_picker(ctx):
    """Кнопки выбора роли в демо: продажи и руководство — ролями; другие отделы — каждой должностью, где есть люди."""
    kb = [[Button("🧑‍💼 Торговый", "sw:role:rep"), Button("👥 Супервайзер", "sw:role:sv")],
          [Button("🏢 Руководитель продаж", "sw:role:head"), Button("👔 Директор", "sw:role:owner")],
          [Button("🧾 Бухгалтер", "sw:role:accountant"), Button("🛠 Администратор", "sw:role:admin")]]
    have = {e["position"] for e in ctx.db.employees()}
    for d in ctx.cfg.departments:
        if d.kind == "sales":
            continue
        ps = [p for p in ctx.cfg.positions if p["dept"] == d.id and p["id"] in have
              and p["role"] not in ("owner", "accountant", "sv", "rep", "newbie")]
        if ps:
            kb += rows([Button(f"{DEPT_ICON.get(d.kind, '🏬')} {p['name']}", f"sw:pos:{p['id']}") for p in ps], 2)
    kb.append([Button("👤 Выбрать конкретного человека", "sw:who")])
    return kb


HELP_DEPT = {
    ("head", "purchasing"): ["📦 Склад — все поставщики: что закончилось, что скоро, что залежалось, сколько заказать.",
                             "🛒 Заказ — бот сам кладёт в черновик всё, что нужно; поправьте строками «код количество».",
                             "✅ Согласовать — заказы менеджеров до порога согласуете вы, крупнее — директор.",
                             "👥 Кто за каких поставщиков — назначьте менеджеров, каждый увидит только своих.",
                             "🔎 Поиск товара — напишите код или часть названия."],
    ("staff", "purchasing"): ["📦 Мои поставщики — остатки и «нужно заказать» только по вашим поставщикам.",
                              "🛒 Заказ: черновик → «📨 На согласование» → согласовали → «📨 Отправлен поставщику».",
                              "↩️ Если заказ вернули — в нём комментарий, что поправить.",
                              "📥 Excel — файл заказа для поставщика.", "🔎 Поиск товара — напишите код или часть названия."],
    ("head", "warehouse"): ["📥 Приёмка — какие поставки едут; пришло всё или недостача по строкам.",
                            "📦 Отгрузки — сколько точек отгружено по дням и ошибки склада из возвратов.",
                            "📅 Табель — опоздания и пропуски склада; 👤 Люди — карточки кладовщиков."],
    ("staff", "warehouse"): ["📥 Приёмка — примите поставку: «пришло всё» или впишите недостачу.",
                             "📦 Отгрузки — отгрузки и ваши ошибки за месяц (каждая режет бонус).",
                             "💰 Зарплата — оклад, бонус «без ошибок» и удержания."],
    ("head", "logistics"): ["🚚 Рейсы — рейтинг водителей, дальние рейсы, двойные заезды, доставка к продажам.",
                            "📝 Отметки — выезд, возврат, ночёвки, деньги и привозы всех водителей за день.",
                            "⛽ ГСМ — сколько топлива положено по километрам против топливной карты."],
    ("dispatcher", ""): ["📝 Отметки — заполните день за водителей: выезд, возврат, ночёвка, деньги, привоз.",
                         "🚚 Рейсы — что развезли, двойные заезды и дальние рейсы.",
                         "⛽ ГСМ — нормы водителей и суммы по топливным картам."],
    ("driver", ""): ["🚚 Мой день — «Выехал» утром и «Вернулся» вечером, ночёвка, деньги с клиентов, привоз.",
                     "Переработка — минуты до начала и после конца рабочего дня, платится по ставке.",
                     "📋 Мои рейсы — километры, вес, точки; 💰 Зарплата — всё по строкам."],
    ("hr", ""): ["👤 Кадры — приём, перевод, увольнение: чек-лист, кто за какой пункт, приказ файлом.",
                 "Когда все пункты закрыты — «✅ Провести»: человек появится в расчёте (или уйдёт из него) с нужной даты."],
}


HELP = {
    "rep": ["📈 Мой план — продажи, АКБ, SKU, сбор денег, визиты: план, факт и сколько нужно в день.",
            "💰 Зарплата — сколько уже заработано и за что, строка за строкой.",
            "📞 Кому позвонить — список на сегодня: должники, заснувшие клиенты, маршрут без заказа.",
            "🚗 Маршрут — точки на сегодня; отметьте каждую и вечером «📋 Сформировать отчёт».",
            "😴 Клиенты — кто перестал брать, кто берёт меньше; напишите боту код клиента — откроется карточка.",
            "📝 Тесты — тест недели; 🐞 Не работает — написать администратору.",
            "🏆 Рейтинг — ваше место в отделе."],
    "sv": ["👥 Моя команда — каждый торговый: план, АКБ, нажмите фамилию — его карточка.",
           "📅 Табель — опоздания, пропуски, работа в выходной.", "📌 Задачи — поставить задачу своим.",
           "📋 Отчёты дня — кто где был и кто не отчитался; 😴 Клиенты — спящие и падающие по команде."],
    "head": ["🏢 Отдел — команды и показатели отдела.", "✏️ Правки — предложить премию или удержание с причиной.",
             "👥 Сотрудники и доступ — свой отдел: добавить человека, выдать ссылку-приглашение, закрыть доступ.",
             "📋 Планы — подготовить планы месяца и отправить директору на утверждение.",
             "🗓 Месяц — отправить месяц на согласование."],
    "owner": ["🏢 Отдел продаж — вся картина: команды, прогноз, сторож.", "💰 Фонд — зарплата по отделам.",
              "👥 Сотрудники и доступ — добавить человека, выдать ссылку-приглашение в приложение, закрыть доступ уволенному, "
              "назначить роль (в том числе «Администратор» — того, кто будет вести бот).",
              "💳 Тариф — что подключено и сколько стоит; подключить отдел или уровень — одной кнопкой, заявка уйдёт администратору.",
              "📋 Планы — утвердить или вернуть планы; 📦 Склад, 🚚 Рейсы, ↩️ Возвраты — если подключены.",
              "🗓 Месяц — утвердить месяц: правки решены, ведомость уходит бухгалтеру."],
    "accountant": ["🗓 Месяц → 📑 Ведомость Excel — расчёт всех сотрудников.", "💰 Фонд — суммы по отделам.",
                   "📥 Отчёты — загрузить отчёты из 1С (сохранить в Excel → «Выбрать файлы»); бот покажет, что принял."],
    "staff": ["💰 Моя зарплата — дни и начисления.", "📅 Мой табель — отметки за месяц.",
              "🚚 Мои рейсы — у водителей: рейсы, километры, точки.", "🐞 Не работает — написать администратору."],
    "admin": ["👥 Сотрудники — список по отделам: ➕ добавить, 🔗 пригласить (одноразовая ссылка на 7 дней — человек открыл "
              "на своём телефоне и он в приложении), 🚫 закрыть доступ, 🗑 уволить, ✏️ должность и роль; 🧾 «Нет в расчёте» — "
              "имена из отчётов 1С без человека: нажать и выбрать, кто это; 📨 Заявки — кто написал фамилию сам.",
              "📥 Отчёты — загрузить файлы из 1С и 🧪 «Что пришло»: какой отчёт принят, за какой месяц, кого из торговых бот не узнал.",
              "Всё, что видят остальные, плюс 👁 «Смотреть как» — бот глазами любого сотрудника.",
              "🛠 Пульт (в демо) — всё для вас в одном месте: пригласить клиента, добавить партнёра, гости, партнёры, "
              "заявки «Хочу так», 💰 цены, памятка партнёра."],
}


@screen("help")
def help_screen(ctx):
    role = "rep" if ctx.role == "newbie" else ctx.role
    v = View("❓ Как пользоваться", ctx.cfg.role_title(role) if role in R.ROLES else "Администратор")
    dept = HELP_DEPT.get((ctx.duty, "")) if ctx.role == "staff" and ctx.duty else HELP_DEPT.get((ctx.role, ctx.dept_kind))
    v.lines((dept or HELP.get(role, HELP["staff"])) + ["🏠 — всегда вернёт на главную.",
                                             "◀ Месяц — посмотреть прошлые месяцы.",
                                             "🔒 — раздел есть, но не входит в вашу редакцию: нажмите, чтобы узнать, что в нём."])
    return Reply(view=v)


# ======================= мелочи =======================

def _dm(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


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
