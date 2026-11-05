"""📄 Правила и оплата (Адам 01.11; без договора и подписей — «правила предоставления услуг»): клиент видит, с какого дня
платит, сколько бесплатных дней осталось, следующий счёт,
график по датам, правила и порядок оплаты (PDF). Добавить или убрать функции и людей — окно с тремя цифрами
(«сейчас за остаток месяца», «со следующего каждый месяц», «счёт 1-го, итого») → заявка → одобряет администратор платформы.

Порядок оплаты (утверждён Адамом 01.11):
  договорились — первая половина подключения; запуск — вторая; 14 дней с запуска (включая его) — бесплатно;
  15-й день — счёт за остаток месяца по дням, оплата за 5 дней (осталось ≤ 3 дней — в счёт 1-го числа);
  дальше счёт 1-го, оплата до 5-го; 6-го напоминание, 10-го — только смотреть, 15-го — отключение, данные 3 месяца.
  Добавили функцию / людей — доплата за остаток месяца по дням в следующем счёте, дальше новая цена;
  убрали — до конца оплаченного месяца всё работает, со следующего счёт меньше.

Где лежит: у клиента — ключ «billing» в его базе {contract, launch, setup, people, functions, paid: [месяцы], got: {месяц: сколько пришло}, marks: [отметки для «снять последнюю»], extras: [...],
req: [...]}; одобренные функции перекрывают анкету (core/ui/context). В демо — то же в сессии зрителя («bl»), даты выдуманы:
договорились 14 дней назад, запуск 10 дней назад; заявку зритель одобряет сам кнопкой «🎭 Одобрить».
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

from core.access import editions as E
from core.access import pricing as P
from core.calendar import MONTHS, MONTHS_GEN, first_day, month_of, shift_month
from core.ui.kit import Button, Reply, money
from core.ui.registry import menu, screen, text_input
from core.ui.views import Cell, Row, View, to_pdf

KEY = "billing"
SHORT_TAIL = 3               # до конца месяца осталось столько дней или меньше — кусок идёт в счёт 1-го числа
PAY_DAYS = 5                 # на оплату первого счёта
STEPS = (("remind", 1), ("view", 5), ("off", 10))
HOLD_DAYS = 7                # «⏳ Отсрочка» — на столько дней от сегодня     # после срока оплаты: напоминание, только смотреть, отключение

menu("bl", "📄 Правила и оплата", {"owner", "admin"}, order=85)


# ---------- состояние ----------

def is_admin(ctx):
    return ctx.user_id in ctx.app.admins


def state(ctx):
    if ctx.demo:
        st = ctx.session.get("bl")
        if not isinstance(st, dict) or st.get("day") != ctx.today.isoformat():
            st = {"day": ctx.today.isoformat(), "contract": (ctx.today - timedelta(days=14)).isoformat(),
                  "launch": (ctx.today - timedelta(days=10)).isoformat(), "paid": [], "extras": [], "req": []}
            ctx.session["bl"] = st
        return st
    return ctx.office.get(KEY) or {}


def today_(ctx):
    """Какой день смотрим: у клиента — сегодня; в демо можно листать дни (Адам 01.11: «посмотреть, что к чему»)."""
    if ctx.demo:
        try:
            return ctx.today + timedelta(days=int(ctx.session.get("bl_shift") or 0))
        except (TypeError, ValueError):
            return ctx.today
    return ctx.today


def save(ctx, st):
    if ctx.demo:
        ctx.session["bl"] = st
        ctx.save()
    else:
        ctx.office.put(KEY, st)


def _d(s):
    return date.fromisoformat(s) if s else None


def dm(d):
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def mname(month):
    return MONTHS[first_day(month).month - 1]


def mgen(month):
    return MONTHS_GEN[first_day(month).month - 1]


def dim(d):
    return monthrange(d.year, d.month)[1]


def tail_days(d):
    """Сколько дней от d до конца месяца, включая d."""
    return dim(d) - d.day + 1


def part(amount, d):
    """Сумма за остаток месяца с дня d по дням, до сотни."""
    return P.rnd(amount * tail_days(d) / dim(d))


# ---------- деньги ----------

def funcs(ctx):
    from core.ui.tariff import funcs_of
    return funcs_of(ctx.cfg)


def people(ctx, st=None):
    from core.ui.tariff import counts_of
    st = state(ctx) if st is None else st
    return int(st["people"]) if st.get("people") is not None else sum(counts_of(ctx).values())


def counts_for(ctx, n):
    """Люди по отделам для расчёта, когда всего людей n: разница ложится на первый подключённый отдел."""
    from core.ui.tariff import counts_of
    c = dict(counts_of(ctx))
    diff = n - sum(c.values())
    pk = E.packages_of(funcs(ctx), ctx.cfg.depts or ())
    key = next((k for k in ("sales", "stock", "logistics", "office") if pk.get(k)), "sales")
    c[key] = max(0, c.get(key, 0) + diff)
    return c


def monthly(ctx, fs=None, n=None, st=None):
    from core.ui.tariff import quote_of
    fs = funcs(ctx) if fs is None else set(fs)
    n = people(ctx, st) if n is None else n
    if not fs:
        return 0
    return quote_of(ctx, fs, year=False, counts=counts_for(ctx, n) or {"sales": 0})["monthly"]


def setup_sum(ctx, st):
    if st.get("setup"):
        return int(st["setup"])
    from core.ui.tariff import quote_of
    return quote_of(ctx, funcs(ctx), year=False)["setup"]


def trial_days(ctx):
    return int(P.prices(ctx.office)["trial_days"])


def first_paid(ctx, st):
    """Первый платный день: запуск + 14 дней (день запуска — первый бесплатный)."""
    launch = _d(st.get("launch"))
    return launch + timedelta(days=trial_days(ctx)) if launch else None


def merged(fp):
    """Хвост первого месяца короткий — он идёт в счёт 1-го числа следующего месяца."""
    return tail_days(fp) <= SHORT_TAIL


def first_bill_month(fp):
    return shift_month(month_of(fp), 1) if merged(fp) else month_of(fp)


def bill(ctx, st, month, m=None):
    """Счёт за месяц: (дата счёта, срок оплаты, сумма, строки [(что, сумма)]) или None, если месяц ещё бесплатный."""
    fp = first_paid(ctx, st)
    if not fp:
        return None
    m = monthly(ctx, st=st) if m is None else m
    fbm = first_bill_month(fp)
    if month < fbm:
        return None
    lines = []
    if month == month_of(fp) and not merged(fp):
        day, due = fp, fp + timedelta(days=PAY_DAYS)
        lines.append((f"{dm(fp)} – {dm(date(fp.year, fp.month, dim(fp)))} ({tail_days(fp)} дн.)", part(m, fp)))
    else:
        day = first_day(month)
        due = day + timedelta(days=PAY_DAYS - 1)
        if month == fbm and merged(fp):
            lines.append((f"{dm(fp)} – конец {mgen(month_of(fp))} ({tail_days(fp)} дн.)", part(m, fp)))
        lines.append((mname(month), m))
    for x in st.get("extras") or []:
        if x.get("month") == month:
            lines.append((x.get("title") or "доплата", int(x.get("amount") or 0)))
    return day, due, sum(a for _, a in lines), lines


def months_to_pay(ctx, st, upto):
    """Месяцы со счётом с первого по upto включительно."""
    fp = first_paid(ctx, st)
    if not fp:
        return []
    out, mo = [], first_bill_month(fp)
    while mo <= upto:
        out.append(mo)
        mo = shift_month(mo, 1)
    return out


def got(st, mo):
    """Сколько уже пришло за месяц (Адам 01.11: «впишу оплатил всё или сумму, если только половину»)."""
    return int((st.get("got") or {}).get(mo, 0))


def is_paid(st, mo, b):
    return mo in set(st.get("paid") or []) or (b is not None and got(st, mo) >= b[2])


def due_bills(ctx, st):
    """Выставленные и не оплаченные (или оплаченные частично) счета на сегодня: [(месяц, счёт)]."""
    out = []
    for mo in months_to_pay(ctx, st, month_of(today_(ctx))):
        b = bill(ctx, st, mo)
        if b and b[0] <= today_(ctx) and not is_paid(st, mo, b):
            out.append((mo, b))
    return out


def next_bill(ctx, st):
    """Ближайший будущий счёт: (месяц, счёт) или None."""
    fp = first_paid(ctx, st)
    if not fp:
        return None
    mo = max(first_bill_month(fp), month_of(today_(ctx)))
    for _ in range(3):
        b = bill(ctx, st, mo)
        if b and b[0] > today_(ctx):
            return mo, b
        mo = shift_month(mo, 1)
    return None


def hold_till(st):
    h = _d(st.get("hold"))
    return h


def level(ctx, st=None):
    """Что сейчас с доступом (вариант А, Адам 01.11 — ограничения включаются сами, отсрочку даёт Адам):
    ok — всё работает; remind — счёт просрочен, напоминание; view — только просмотр; off — доступ закрыт.
    Пока действует отсрочка («⏳», st.hold) — ничего не ограничиваем."""
    st = state(ctx) if st is None else st
    due = due_bills(ctx, st)
    if not due:
        return "ok"
    pay_by = due[0][1][1]
    t = today_(ctx)
    h = hold_till(st)
    if t <= (max(pay_by, h) if h else pay_by):
        return "ok"
    view_from, off_from = limit_dates(ctx, st)
    if t < view_from:
        return "remind"
    if t < off_from:
        return "view"
    return "off"


def limit_dates(ctx, st):
    """(с какого дня только просмотр, с какого — закрыто) по первому неоплаченному счёту, с учётом отсрочки; или None."""
    due = due_bills(ctx, st)
    if not due:
        return None
    pay_by = due[0][1][1]
    h = hold_till(st)
    if h and h > pay_by:                       # отсрочка: после неё — день на напоминание, потом просмотр, через 5 дней — закрыто
        return h + timedelta(days=2), h + timedelta(days=2 + STEPS[2][1] - STEPS[1][1])
    return pay_by + timedelta(days=STEPS[1][1]), pay_by + timedelta(days=STEPS[2][1])


ALLOWED_WHEN_OFF = {"bl", "bld", "blo", "blt", "home"}


def gate(ctx, what="press", sid=""):
    """Ограничения у настоящего клиента (core/app зовёт перед каждым нажатием, текстом и файлом).
    None — пустить; иначе — что ответить. Администратор платформы ходит всегда; в демо ограничений нет."""
    if ctx.demo or is_admin(ctx):
        return None
    st = state(ctx)
    if not st.get("launch"):
        return None
    lv = level(ctx, st)
    boss = ctx.role in ("owner", "admin")
    if lv == "off":
        if boss and sid in ALLOWED_WHEN_OFF - {"home"}:
            return None
        txt = ("⛔ Доступ закрыт: счёт не оплачен. Все данные сохранены и хранятся 3 месяца — как только оплата придёт, "
               "всё включится в тот же день.")
        return Reply(text=txt if boss else "⛔ Приложение временно недоступно. Обратитесь к руководителю.",
                     buttons=[[Button("📄 Правила и оплата", "bl")]] if boss else [])
    if lv == "view" and what in ("text", "upload"):
        return Reply(text="👀 Сейчас только просмотр: счёт не оплачен, поэтому изменения и новые отчёты 1С не принимаются. "
                          "Оплата придёт — всё включится в тот же день.",
                     buttons=[[Button("📄 Правила и оплата", "bl")]] if boss else [[Button("🏠 Главная", "home")]])
    return None


def status(ctx, st):
    """Главная строка: (текст, цвет)."""
    if not st.get("contract") and not st.get("launch"):
        return "🤝 Ещё не договорились — даты появятся, когда договоримся.", None
    fp = first_paid(ctx, st)
    if not fp:
        return f"🛠 Настраиваем приложение. {trial_days(ctx)} бесплатных дней начнутся со дня запуска.", "blue"
    if today_(ctx) < fp:
        left = (fp - today_(ctx)).days
        return f"🎁 Бесплатно ещё {left} дн. — до {dm(fp - timedelta(days=1))} включительно.", "good"
    due = due_bills(ctx, st)
    if due:
        mo, (day, pay_by, amount, _) = due[0]
        late = (today_(ctx) - pay_by).days
        h = hold_till(st)
        if h and today_(ctx) <= h and late > 0:
            g0 = got(st, mo)
            left = f"осталось {money(amount - g0)}" if g0 else money(amount)
            return f"⏳ Счёт за {mname(mo)} ({left}) — отсрочка до {dm(h)} включительно.", "warn"
        g = got(st, mo)
        what = (f"Счёт за {mname(mo)}: оплачено {money(g)} из {money(amount)}, осталось {money(amount - g)}" if g
                else f"Счёт за {mname(mo)} · {money(amount)}")
        if late <= 0:
            return f"💳 {what} — оплатить до {dm(pay_by)}.", "warn"
        view_from, off_from = limit_dates(ctx, st)
        if today_(ctx) < view_from:
            return f"⚠️ {what} не оплачен. С {dm(view_from)} — только просмотр.", "bad"
        if today_(ctx) < off_from:
            return f"⚠️ {what} не оплачен — по правилам только просмотр. С {dm(off_from)} — отключение.", "bad"
        return f"⛔ {what} не оплачен — по правилам доступ закрыт. Данные хранятся 3 месяца.", "bad"
    nb = next_bill(ctx, st)
    paid = sorted(mo for mo in months_to_pay(ctx, st, month_of(today_(ctx))) if is_paid(st, mo, bill(ctx, st, mo)))
    head = f"✅ {mname(paid[-1]).capitalize()} оплачен." if paid else "✅ Счетов к оплате нет."
    if nb:
        mo, (day, _, amount, _) = nb
        return f"{head} Следующий счёт {dm(day)} · {money(amount)}.", "good"
    return head, "good"


def home_line(ctx):
    """Плашка на главной у директора и админа: бесплатные дни, скорый счёт, неоплата."""
    if ctx.role not in ("owner", "admin"):
        return ""
    st = state(ctx)
    fp = first_paid(ctx, st)
    if not fp:
        return ""
    if today_(ctx) < fp:
        return f"🎁 Бесплатно ещё {(fp - today_(ctx)).days} дн. — «📄 Правила и оплата»."
    if due_bills(ctx, st):
        return status(ctx, st)[0]
    nb = next_bill(ctx, st)
    if nb and (nb[1][0] - today_(ctx)).days <= 3:
        return f"💳 Счёт через {(nb[1][0] - today_(ctx)).days} дн.: {dm(nb[1][0])} · {money(nb[1][2])}."
    return ""


# ---------- график ----------

def schedule(ctx, st):
    """[(дата или текст, что, сумма, отметка)] — с того дня, как договорились, до трёх месяцев вперёд."""
    setup = setup_sum(ctx, st)
    half = setup // 2
    paid = set(st.get("paid") or [])
    out = []
    c, launch = _d(st.get("contract")), _d(st.get("launch"))
    out.append((dm(c) if c else "когда договоримся", "Договорились — первая половина подключения", half, "✓" if c else ""))
    out.append((dm(launch) if launch else "день запуска", "Запуск — вторая половина подключения", setup - half,
                "✓" if launch else ""))
    fp = first_paid(ctx, st)
    td = trial_days(ctx)
    if launch:
        out.append((f"{dm(launch)} – {dm(fp - timedelta(days=1))}", f"{td} дней бесплатно", 0,
                    "сейчас" if today_(ctx) < fp else "✓"))
        start = first_bill_month(fp)
        for mo in [start, shift_month(start, 1), shift_month(start, 2)]:
            day, due, amount, lines = bill(ctx, st, mo)
            what = "Первый счёт" if mo == start else f"Счёт за {mname(mo)}"
            g = got(st, mo)
            mark = ("оплачен" if is_paid(st, mo, (day, due, amount, lines)) else
                    f"оплачено {money(g)}, осталось {money(amount - g)}" if g else
                    "ждёт оплаты" if day <= today_(ctx) else "")
            out.append((dm(day), f"{what}, оплатить до {dm(due)}", amount, mark))
    else:
        out.append(("с запуска", f"{td} дней бесплатно", 0, ""))
        out.append(("15-й день", "Первый счёт: остаток месяца по дням, оплата за 5 дней", None, ""))
        out.append(("1-е число", "Дальше каждый месяц, оплата до 5-го", monthly(ctx, st=st), ""))
    return out


# ---------- экран ----------

@screen("bl")
def billing_screen(ctx, note=None):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return Reply(text="Правила и оплату видит директор.")
    st = state(ctx)
    if ctx.demo:
        save(ctx, st)
    text, tone = status(ctx, st)
    m = monthly(ctx, st=st)
    sub = f"{ctx.cfg.name} · набор «{ctx.cfg.set_title}» · {people(ctx, st)} чел."
    v = View("📄 Правила и оплата", sub)
    fp = first_paid(ctx, st)
    tiles = []
    if fp and today_(ctx) < fp:
        tiles.append(("Бесплатно, дней", str((fp - today_(ctx)).days), "good"))
    nb = next_bill(ctx, st)
    if nb:
        tiles.append((f"Счёт {dm(nb[1][0])}", money(nb[1][2]), None))
    tiles.append(("В месяц", money(m), None))
    v.tiles(tiles)
    v.lines([text], title="Сейчас")
    if ctx.demo:
        v.note(f"Пример: даты и суммы выдуманы — у вашей фирмы будут свои. Смотрим на {dm(today_(ctx))}"
               + (" (сегодня)" if today_(ctx) == ctx.today else "") + ". Листайте дни кнопками ◀ ▶ — увидите, "
               "как идут бесплатные дни, приходит счёт и что будет, если не оплатить.")
    if note:
        v.note(note)
    v.table(["Когда и что", "₽"], sched_rows(ctx, st))
    if nb and len(nb[1][3]) > 1:
        v.table([f"Счёт {dm(nb[1][0])}", "₽"], [Row([a, money(b, "")]) for a, b in nb[1][3]] +
                [Row([Cell("Итого", bold=True), Cell(money(nb[1][2], ""), bold=True)])])
    v.lines(["Счёт — 1-го числа, оплата до 5-го. 6-го — напоминание, 10-го — только просмотр, 15-го — отключение; "
             "данные хранятся 3 месяца.",
             "Добавить или убрать функции и людей — кнопкой ниже: сразу видно, сколько это стоит, включаем после одобрения."],
            title="Как платим")
    kb = []
    if ctx.demo:
        kb.append([Button("◀ неделя", "blt:-7"), Button("📅 Сегодня", "blt:0"), Button("неделя ▶", "blt:7")])
    wait = [r for r in st.get("req") or [] if r.get("status") == "wait"]
    if wait:
        r = wait[-1]
        v.lines([f"⏳ Ждёт одобрения: {req_title(r)} · с {mgen(r['from'])} {signed(r['new'] - r['old'])} в месяц"],
                title="Заявка")
        kb.append([Button("✖️ Отозвать заявку", f"blx:{r['id']}")])
        if ctx.demo:
            kb.append([Button("🎭 Одобрить (в демо — вы за нас)", f"bly:{r['id']}",
                              info="В демо одобряете вы сами — у настоящего клиента заявка уходит нам, "
                                   "и функция включается после нашего «✅ Одобрить».")])
    else:
        kb.append([Button("➕ Добавить или ➖ убрать функции и людей", "blc")])
    kb.append([Button("📄 Правила PDF", "bld"), Button("📄 Порядок оплаты PDF", "blo")])
    done = [r for r in st.get("req") or [] if r.get("status") in ("ok", "no")][-3:]
    if done:
        v.lines([f"{'✅' if r['status'] == 'ok' else '❌'} {r['ts']} · {req_title(r)}"
                 + (f" — {r['why']}" if r.get("why") else "") for r in reversed(done)], title="Прошлые заявки")
    if not ctx.demo:
        kb.append([Button("💳 Тариф", "tf")])
    if is_admin(ctx) and not ctx.demo:
        kb.append([Button("🛠 Даты и оплата (для нас)", "blm")])
    return Reply(view=v, buttons=kb)


def sched_rows(ctx, st):
    """График строками: дата крупно, что это — целиком под ней (Адам 01.11: «половины фраз нет»)."""
    out = []
    for when, what, amt, mark in schedule(ctx, st):
        tone = "good" if mark in ("✓", "оплачен") else "warn" if mark and mark != "сейчас" else None
        out.append(Row([when, "—" if amt is None else money(amt, "")], sub=what + (f" · {mark}" if mark and mark != "✓" else ""), mark=tone))
    return out


@screen("blt")
def billing_shift(ctx, days="0"):
    """Демо: листать дни вперёд-назад, чтобы увидеть, как меняются счёт и шаги."""
    if not ctx.demo:
        return billing_screen(ctx)
    try:
        d = int(days)
    except ValueError:
        d = 0
    ctx.session["bl_shift"] = 0 if d == 0 else max(-30, min(120, int(ctx.session.get("bl_shift") or 0) + d))
    ctx.save()
    return billing_screen(ctx)


def signed(x):
    return ("+ " if x > 0 else "− " if x < 0 else "") + money(abs(x))


def req_title(r):
    parts = [f"+ {E.FUNC_SHORT.get(f, f)}" for f in r.get("add") or []]
    parts += [f"− {E.FUNC_SHORT.get(f, f)}" for f in r.get("drop") or []]
    a, b = r.get("people") or [0, 0]
    if a != b:
        parts.append(f"люди {a} → {b}")
    return ", ".join(parts) or "без изменений"


# ---------- окно «добавить / убрать» ----------
# blc — открыть; blc:t:<функция> — галочка; blc:p:<±n> — люди; blc:i — ничего (кнопка «Почему так»);
# blc:ok — отправить на одобрение; blc:no — выйти без изменений. Черновик — session["bl_draft"].

def calc(ctx, st, new_f, new_p):
    """Три цифры окна: (сейчас за остаток месяца, разница в месяц, следующий счёт: (дата, [строки], итого), дней)."""
    old = monthly(ctx, st=st)
    new = monthly(ctx, new_f, new_p, st)
    d = new - old
    fp = first_paid(ctx, st)
    now = 0
    if d > 0 and fp and today_(ctx) >= fp:
        now = part(d, today_(ctx))
    nxt = shift_month(month_of(today_(ctx)), 1)
    if fp and today_(ctx) < fp:                     # бесплатные дни: новая цена — с первого счёта
        nb = next_bill(ctx, st)
        nxt = nb[0] if nb else first_bill_month(fp)
    st2 = dict(st, extras=list(st.get("extras") or []) + ([{"month": nxt, "amount": now,
                                                             "title": f"доплата за {mname(month_of(today_(ctx)))}"}]
                                                          if now else []))
    b = bill(ctx, st2, nxt, new) if fp else None
    return old, new, now, nxt, b


def explain(ctx, d, now):
    t = tail_days(today_(ctx))
    m = mgen(month_of(today_(ctx)))
    if d > 0 and now:
        return (f"Новая функция включается сразу. За оставшиеся {t} дн. {m} платите только за эти дни: "
                f"{money(d)} ÷ {dim(today_(ctx))} дн. × {t} дн. ≈ {money(now)}. Эта доплата придёт одной строкой "
                f"в следующем счёте 1-го числа — отдельного счёта нет. Со следующего месяца — полная новая цена.")
    if d > 0:
        return "Пока идут бесплатные дни, за добавленное не платите. Новая цена — с первого счёта."
    if d < 0:
        return (f"Убираем со следующего месяца: до конца {m} всё работает как было, за оплаченный месяц деньги не "
                "возвращаются. Со следующего счёта сумма меньше.")
    return "Сумма не меняется."


@screen("blc")
def change_window(ctx, act=None, value=None):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return Reply(text="Менять подключение может директор.")
    st = state(ctx)
    if any(r.get("status") == "wait" for r in st.get("req") or []):
        return billing_screen(ctx, "Сначала дождитесь ответа по прошлой заявке — или отзовите её.")
    have, n0 = funcs(ctx), people(ctx, st)
    d = ctx.session.get("bl_draft")
    if act is None or not isinstance(d, dict):
        d = {"f": sorted(have), "p": n0}
    if act == "no":
        ctx.session.pop("bl_draft", None)
        ctx.save()
        return billing_screen(ctx)
    if act == "t" and value in E.FUNCS:
        d["f"] = sorted(E.toggle_function(d["f"], value))
    elif act == "p":
        try:
            d["p"] = max(0, int(d["p"]) + int(value))
        except (TypeError, ValueError):
            pass
    new_f, new_p = set(d["f"]), int(d["p"])
    old, new, now, nxt, b = calc(ctx, st, new_f, new_p)
    if act == "ok":
        ctx.session.pop("bl_draft", None)
        ctx.save()
        if new_f == have and new_p == n0:
            return billing_screen(ctx, "Ничего не изменилось — заявка не нужна.")
        if not new_f:
            return billing_screen(ctx, "Хотя бы одна функция должна остаться.")
        return send_request(ctx, st, have, new_f, n0, new_p, old, new, now, nxt)
    ctx.session["bl_draft"] = d
    ctx.save()
    add = sorted(new_f - have, key=list(E.FUNCS).index)
    drop = sorted(have - new_f, key=list(E.FUNCS).index)
    what = [f"➕ {E.FUNC_SHORT[f]}" for f in add] + [f"➖ {E.FUNC_SHORT[f]}" for f in drop]
    if new_p != n0:
        what.append(f"👥 {signed(new_p - n0).replace(' ₽', '')} чел.")
    v = View("➕➖ Изменить подключение", " · ".join(what) if what else "отметьте, что добавить или убрать")
    diff = new - old
    cur = mgen(month_of(today_(ctx)))
    fp = first_paid(ctx, st)
    free = not fp or today_(ctx) < fp
    rows_ = [Row(["Сейчас — бесплатные дни" if free else f"Сейчас, за остаток {cur} ({tail_days(today_(ctx))} дн.)",
                  Cell(signed(now) if now else "0 ₽", "warn" if now else None, bold=True)],
                 sub="придёт строкой в следующем счёте" if now else ""),
             Row(["С первого счёта, в месяц" if free else f"С {mgen(nxt)} каждый месяц",
                  Cell(signed(diff) if diff else "0 ₽", "good" if diff < 0 else None, bold=True)],
                 sub=f"было {money(old)} → станет {money(new)}" if diff else "")]
    if b:
        v.tiles([(f"Счёт {dm(b[0])}, итого", money(b[2]), None)])
    v.table(["Что меняется", "₽"], rows_)
    if b and (diff or now):
        v.table([f"Счёт {dm(b[0])}", "₽"], [Row([a, money(x, "")]) for a, x in b[3]] +
                [Row([Cell("Итого", bold=True), Cell(money(b[2], ""), bold=True)])])
    if diff < 0:
        v.note(f"До конца {cur} всё работает, с {mgen(nxt)} счёт меньше на {money(-diff)}.")
    pr = P.prices(ctx.office)
    from core.ui.screens import LOCK_TEXT
    kb = [[Button("ℹ️ Почему так", "blc:i", info=explain(ctx, diff, now))]]
    for grp, (icon, gtitle) in E.FUNC_GROUPS.items():
        for f in [f for f in E.FUNCS if E.FUNCS[f][0] == grp]:
            kb.append([Button(f"{'☑' if f in new_f else '☐'} {icon} {E.FUNC_SHORT[f]} · {P.fmt(pr['f_' + f])}",
                              f"blc:t:{f}", info=LOCK_TEXT.get(f, E.MODULES.get(f, ("",))[0]))])
    kb.append([Button("−10", "blc:p:-10"), Button("−1", "blc:p:-1"), Button(f"👥 {new_p} чел.", "blc:i",
               info=f"Люди в приложении — {P.fmt(pr['user'])} ₽ за человека в месяц; директор и администратор бесплатно."),
               Button("+1", "blc:p:1"), Button("+10", "blc:p:10")])
    kb.append([Button("✅ Отправить на одобрение", "blc:ok"), Button("✖️ Отмена", "blc:no")])
    return Reply(view=v, buttons=kb)


def send_request(ctx, st, have, new_f, n0, new_p, old, new, now, nxt):
    reqs = list(st.get("req") or [])
    rid = max([int(r.get("id", 0)) for r in reqs] + [0]) + 1
    r = {"id": rid, "add": sorted(new_f - have), "drop": sorted(have - new_f), "people": [n0, new_p], "old": old,
         "new": new, "now": now, "from": nxt, "status": "wait", "ts": f"{today_(ctx):%d.%m} {datetime.now():%H:%M}",
         "who": ctx.name(), "uid": ctx.user_id, "funcs": sorted(new_f)}
    reqs.append(r)
    st["req"] = reqs[-50:]
    save(ctx, st)
    if not ctx.demo:
        ctx.db.audit(ctx.author(), "billing_req", req_title(r))
    out = []
    if not ctx.demo:
        text = (f"📄 {ctx.cfg.name}: {ctx.name()} просит: {req_title(r)}.\n"
                f"Сейчас за остаток {mgen(month_of(today_(ctx)))}: {signed(now) if now else '0 ₽'}\n"
                f"С {mgen(nxt)} каждый месяц: {money(old)} → {money(new)} ({signed(new - old)})")
        out += [Reply(text=text, to=a, buttons=[[Button("✅ Одобрить", f"bly:{rid}"), Button("❌ Отказать", f"bln:{rid}")]])
                for a in ctx.app.admins if a != ctx.user_id]
    out.append(billing_screen(ctx, "⏳ Заявка отправлена на одобрение." + (
        " В демо одобрите её сами — кнопкой «🎭 Одобрить»." if ctx.demo else
        " Как только одобрим — включится, придёт уведомление.")))
    return out


def _find(st, rid):
    return next((r for r in st.get("req") or [] if str(r.get("id")) == str(rid)), None)


@screen("blx")
def withdraw(ctx, rid):
    st = state(ctx)
    r = _find(st, rid)
    if r and r.get("status") == "wait" and (ctx.role in ("owner", "admin") or is_admin(ctx)):
        st["req"].remove(r)
        save(ctx, st)
        return billing_screen(ctx, "Заявка отозвана.")
    return billing_screen(ctx)


@screen("bly")
def approve(ctx, rid):
    if not (is_admin(ctx) or ctx.demo):
        return Reply(text="Заявку одобряем мы — она уже у нас.")
    st = state(ctx)
    r = _find(st, rid)
    if not r or r.get("status") != "wait":
        return billing_screen(ctx, "Эта заявка уже решена.")
    fs = set(r["funcs"])
    old, new, now, nxt, _ = calc(ctx, st, fs, r["people"][1])          # доплата — на день одобрения
    r.update(status="ok", now=now, new=new, decided=f"{today_(ctx):%d.%m} {datetime.now():%H:%M}")
    if now:
        st.setdefault("extras", []).append({"month": nxt, "amount": now, "title": f"доплата за {mname(month_of(today_(ctx)))}: "
                                                                                 f"{req_title(r)}"})
    st["people"] = r["people"][1]
    if ctx.demo:
        ctx.session["funcs"] = sorted(fs)
        ctx.session.pop("packages", None)
        ctx.session.pop("edition", None)
    else:
        st["functions"] = sorted(fs)
    save(ctx, st)
    out = []
    if not ctx.demo:
        ctx.db.audit(ctx.author(), "billing_ok", req_title(r))
        if r.get("uid") and r["uid"] != ctx.user_id:
            out.append(Reply(text=f"✅ Включено: {req_title(r)}. С {mgen(r['from'])} — {money(new)} в месяц"
                                  + (f", за остаток {mgen(month_of(today_(ctx)))} {money(now)} — в следующем счёте." if now else "."),
                             to=r["uid"], buttons=[[Button("📄 Правила и оплата", "bl")]]))
    out.append(billing_screen(ctx.app.ctx(ctx.user_id), f"✅ Одобрено: {req_title(r)}."))
    return out


@screen("bln")
def reject(ctx, rid):
    if not is_admin(ctx):
        return Reply(text="Заявку решаем мы — она уже у нас.")
    r = _find(state(ctx), rid)
    if not r or r.get("status") != "wait":
        return billing_screen(ctx, "Эта заявка уже решена.")
    return ctx.ask("bl_no", f"Отказ: {req_title(r)}.\nНапишите причину для клиента или «-» — без комментария.",
                   {"id": rid}, buttons=[[Button("✖️ Отмена", "bl")]])


def do_reject(ctx, rid, why):
    """Отказ по заявке. → сообщения (клиенту) или None, если заявка уже решена."""
    st = state(ctx)
    r = _find(st, rid)
    if not r or r.get("status") != "wait":
        return None
    why = (why or "").strip()
    r.update(status="no", why="" if why in ("-", "—") else why[:300], decided=f"{ctx.today:%d.%m} {datetime.now():%H:%M}")
    save(ctx, st)
    ctx.db.audit(ctx.author(), "billing_no", req_title(r))
    out = []
    if r.get("uid") and r["uid"] != ctx.user_id:
        out.append(Reply(text=f"❌ Заявка не одобрена: {req_title(r)}." + (f"\n{r['why']}" if r["why"] else ""),
                         to=r["uid"], buttons=[[Button("📄 Правила и оплата", "bl")]]))
    return out


@text_input("bl_no")
def on_reject(ctx, text, data):
    if not is_admin(ctx):
        return billing_screen(ctx)
    out = do_reject(ctx, data.get("id"), text)
    if out is None:
        return billing_screen(ctx)
    return out + [billing_screen(ctx, "Отказ отправлен клиенту.")]


# ---------- для администратора платформы: даты и оплата ----------

@screen("blm")
def manage(ctx, act=None):
    if not is_admin(ctx) or ctx.demo:
        return billing_screen(ctx)
    st = state(ctx)
    t = today_(ctx).isoformat()
    note = None
    if act == "c":
        st["contract"] = t
        st["setup"] = setup_sum(ctx, st)
        note = "🤝 Договорились — сегодня."
    elif act == "l":
        st["launch"] = t
        st.setdefault("contract", t)
        st["setup"] = setup_sum(ctx, st)
        if st.get("people") is None:
            st["people"] = people(ctx, st)
        note = "🚀 Запуск — сегодня, бесплатные дни пошли."
    elif act == "p":
        mo = pay_month(ctx, st)
        if mo:
            st["paid"] = sorted(set(st.get("paid") or []) | {mo})
            st.setdefault("marks", []).append({"mo": mo, "all": True})
            note = f"✅ Оплачен весь счёт за {mname(mo)}."
    elif act == "s":
        mo = pay_month(ctx, st)
        if mo:
            b = bill(ctx, st, mo)
            return ctx.ask("bl_sum", f"Сколько пришло за {mname(mo)}? Счёт {money(b[2])}, уже отмечено {money(got(st, mo))}. "
                                     "Напишите сумму, например 22 500.", {"mo": mo}, buttons=[[Button("✖️ Отмена", "blm")]])
    elif act == "u" and (st.get("marks") or st.get("paid")):
        if st.get("marks"):
            m_ = st["marks"].pop()
            mo = m_["mo"]
            if m_.get("all"):
                st["paid"] = [x for x in st.get("paid") or [] if x != mo]
            else:
                st.setdefault("got", {})[mo] = max(0, got(st, mo) - int(m_.get("sum") or 0))
                st["paid"] = [x for x in st.get("paid") or [] if x != mo]
        else:
            mo = sorted(st["paid"])[-1]
            st["paid"] = sorted(st["paid"])[:-1]
        note = f"↩️ Последняя отметка за {mname(mo)} снята."
    elif act == "h":
        st["hold"] = (today_(ctx) + timedelta(days=HOLD_DAYS)).isoformat()
        note = f"⏳ Отсрочка до {dm(_d(st['hold']))} включительно — до этого дня ничего не ограничиваем."
    elif act == "x" and st.get("hold"):
        st.pop("hold", None)
        note = "Отсрочка снята."
    elif act == "d":
        return ctx.ask("bl_dates", "Даты через пробел: когда договорились и запуск, например «16.11.2026 20.11.2026». "
                                   "Запуска ещё не было — только первую дату.", buttons=[[Button("✖️ Отмена", "blm")]])
    if act:
        save(ctx, st)
        ctx.db.audit(ctx.author(), "billing", note or act)
    v = View("🛠 Даты и оплата", ctx.cfg.name)
    v.table(["Что", "Сейчас"], [Row(["Договорились", dm(_d(st["contract"])) if st.get("contract") else "—"]),
                                Row(["Запуск", dm(_d(st["launch"])) if st.get("launch") else "—"]),
                                Row(["Подключение", money(setup_sum(ctx, st))]),
                                Row(["Людей по тарифу", str(people(ctx, st))]),
                                Row(["Оплачены месяцы", ", ".join(mname(m) for m in sorted(st.get("paid") or [])) or "—"])] +
            [Row([f"Частично за {mname(m)}", money(a)]) for m, a in sorted((st.get("got") or {}).items())
             if a and m not in (st.get("paid") or [])])
    if note:
        v.note(note)
    v.note("Пока нет приложения управления оплатами — отмечаешь здесь сам. Неоплата бота не выключает: шаги 6 / 10 / 15 "
           "клиент видит текстом, решение за тобой. Частичная оплата — клиент видит остаток.")
    pay_mo = pay_month(ctx, st)
    kb = [[Button("🤝 Договорились сегодня", "blm:c"), Button("🚀 Запуск сегодня", "blm:l")],
          [Button(f"✅ Оплачено всё за {mname(pay_mo)}", "blm:p"), Button("✏️ Оплачено частично", "blm:s")] if pay_mo else [],
          [Button("↩️ Снять последнюю отметку", "blm:u")] if st.get("marks") or st.get("paid") else [],
          [Button(f"⏳ Отсрочка на {HOLD_DAYS} дней", "blm:h")] + ([Button("✖️ Снять отсрочку", "blm:x")] if st.get("hold") else []),
          [Button("✏️ Вписать даты", "blm:d")]]
    return Reply(view=v, buttons=[r for r in kb if r])


def pay_month(ctx, st):
    """За какой месяц отмечать оплату: первый неоплаченный выставленный, иначе ближайший будущий."""
    due = due_bills(ctx, st)
    nb = next_bill(ctx, st)
    return due[0][0] if due else (nb[0] if nb else None)


def parse_sum(text):
    t = (text or "").replace(" ", "").replace("\u00a0", "").replace("₽", "").replace(",", ".")
    try:
        return int(round(float(t)))
    except ValueError:
        return 0


def apply_sum(ctx, mo, x):
    """Отметить, что за месяц пришло x (частичная оплата; набралась вся сумма — месяц оплачен)."""
    st = state(ctx)
    st.setdefault("got", {})[mo] = got(st, mo) + x
    st.setdefault("marks", []).append({"mo": mo, "sum": x})
    b = bill(ctx, st, mo)
    if b and got(st, mo) >= b[2]:
        st["paid"] = sorted(set(st.get("paid") or []) | {mo})
    save(ctx, st)
    ctx.db.audit(ctx.author(), "billing", f"оплата за {mname(mo)}: {x}")


@text_input("bl_sum")
def on_sum(ctx, text, data):
    if not is_admin(ctx):
        return billing_screen(ctx)
    x = parse_sum(text)
    if x <= 0:
        return ctx.ask("bl_sum", "Нужна сумма числом, например 22 500.", data, buttons=[[Button("✖️ Отмена", "blm")]])
    apply_sum(ctx, data["mo"], x)
    return manage(ctx)


def apply_dates(ctx, text):
    """«16.11.2026 20.11.2026» → когда договорились и запуск. → True, если понял."""
    ds = []
    for w in (text or "").replace(",", " ").split():
        try:
            ds.append(datetime.strptime(w.strip(), "%d.%m.%Y").date())
        except ValueError:
            continue
    if not ds or len(ds) > 2 or (len(ds) == 2 and ds[1] < ds[0]):
        return False
    st = state(ctx)
    st["contract"] = ds[0].isoformat()
    st["setup"] = setup_sum(ctx, st)
    if len(ds) == 2:
        st["launch"] = ds[1].isoformat()
        if st.get("people") is None:
            st["people"] = people(ctx, st)
    else:
        st.pop("launch", None)
    save(ctx, st)
    ctx.db.audit(ctx.author(), "billing", "даты: " + text.strip()[:40])
    return True


@text_input("bl_dates")
def on_dates(ctx, text, data):
    if not is_admin(ctx):
        return billing_screen(ctx)
    if not apply_dates(ctx, text):
        return ctx.ask("bl_dates", "Не понял. Пример: «16.11.2026 20.11.2026» — когда договорились, потом запуск.",
                       buttons=[[Button("✖️ Отмена", "blm")]])
    return manage(ctx)


# ---------- документы ----------

def _client_lines(ctx, st):
    from core.ui.tariff import quote_of
    q = quote_of(ctx, funcs(ctx), year=False, counts=counts_for(ctx, people(ctx, st)))
    rows = [Row([a, money(b, "")], sub=c) for a, b, c in q["lines"]]
    if q["discount"]:
        rows.append(Row(["Скидка: все четыре отдела", "−" + money(q["discount"], "")]))
    rows.append(Row([Cell("Итого в месяц", bold=True), Cell(money(q["monthly"], ""), bold=True)]))
    rows.append(Row([Cell("Подключение, один раз", bold=True), Cell(money(setup_sum(ctx, st), ""), bold=True)],
                    sub="две половины: когда договорились и в день запуска"))
    return rows


LEAD = ("Мы считаем зарплату и показываем цифры вашей фирмы — вы платите за это раз в месяц. Ниже — как это устроено, без "
        "мелкого шрифта. Начиная работу с нами, вы соглашаетесь с этими правилами.")

CONTRACT = [
    ("1. Что мы делаем", [
        "Мы настраиваем для вашей фирмы приложение и держим его в работе. Ваши люди открывают его с телефона и "
        "компьютера и каждый день видят: свои продажи, план и сколько осталось до него; свою зарплату — по строкам, "
        "за что сколько; что нужно сделать сегодня.",
        "Руководители видят всю картину по отделам. Цифры берутся из вашей 1С, зарплата считается по вашим правилам. "
        "Что входит и сколько стоит — в Приложении 1.",
        "Приложение наше. Пользуется им ваша фирма и не передаёт доступ другим."]),
    ("2. Как запускаемся", [
        "1) Договариваемся. Вы оплачиваете первую половину подключения — с этого дня мы начинаем настройку.",
        "2) Настраиваем. Мы делаем отделы, должности, правила зарплаты, приём отчётов из 1С и вход для сотрудников. "
        "От вас — анкета, список людей, правила зарплаты и человек, который по нашей инструкции настроит выгрузку из 1С. "
        "Срок — до … рабочих дней. Пока ждём что-то от вас, срок стоит.",
        "3) Запуск. В приложении ваши настоящие цифры, люди могут войти. В этот день — вторая половина подключения.",
        "4) 14 дней бесплатно — со дня запуска. Работает всё, что вы выбрали, за месяц вы не платите. Не подошло — "
        "скажите до конца 14-го дня, и мы спокойно расстанемся: месяц не платите, вторую половину подключения тоже, "
        "если ещё не оплатили. Первая половина остаётся за настройку — работа уже сделана."]),
    ("3. Деньги", [
        "Подключение — один раз: половина, когда договорились, половина в день запуска.",
        "Первый месяц — на 15-й день после запуска, только за оставшиеся дни месяца; 5 дней на оплату.",
        "Каждый следующий месяц — счёт 1-го числа, оплата до 5-го, вперёд.",
        "В сумму месяца входят выбранные функции и люди в приложении. Директор и администратор — бесплатно.",
        "Если от первого месяца осталось 3 дня или меньше, отдельного счёта не будет — они придут вместе со счётом 1-го числа.",
        "Можно платить частями: в приложении видно, сколько уже оплачено и сколько осталось.",
        "За год сразу — минус 10 %.",
        "Всё это директор видит в приложении, в «Правила и оплата»: сколько бесплатных дней осталось, когда следующий "
        "счёт и на какую сумму. Сюрпризов не будет."]),
    ("4. Растёте — растёт и приложение", [
        "Добавить функцию, отдел или людей. Директор отмечает нужное в приложении и сразу видит три цифры: сколько за "
        "остаток этого месяца, сколько дальше в месяц и каким будет следующий счёт. Мы проверяем, что всё настроено, "
        "и включаем — обычно в тот же день.",
        "Убрать. Так же, через приложение. До конца оплаченного месяца всё работает как было, со следующего счёт меньше.",
        "Нужно то, чего ещё нет. Опишите в приложении своими словами. Мы уточним, покажем эскиз и цену, а вы правите, "
        "пока не скажете «Согласен». Небольшая доработка (до 1 рабочего дня) придёт в счёте 1-го числа. Большая — "
        "половина после «Согласен», половина после запуска. Правки по ходу бесплатно, пока они не меняют суть."]),
    ("5. Если что-то пошло не так", [
        "Неверно посчитана зарплата — нажмите «Не работает» и приложите экран. Исправим за 1 рабочий день, бесплатно.",
        "Не пришли отчёты из 1С — приложение само предупредит. Поможем найти причину — у вас в 1С или у нас.",
        "Приложение не работало по нашей вине больше суток — вычтем эти дни из следующего счёта, просить не надо.",
        "Не согласны со счётом — напишите до срока оплаты. Пока разбираемся, ограничения не включаются.",
        "Оплата не пришла вовремя — на следующий день после срока напоминание. Через 5 дней — только просмотр (цифры "
        "видно, новые отчёты не принимаются). Через 10 дней — доступ закрыт. Оплатили — включаем в тот же день.",
        "Не договорились — любая сторона может расстаться (раздел 7)."]),
    ("6. Ваши данные", [
        "Все цифры и списки сотрудников — ваши. Используем их только для вашего приложения и никому не передаём.",
        "У каждой фирмы своя отдельная база, другие клиенты её не видят. Каждую ночь делаем копию.",
        "Согласие сотрудников на обработку их данных берёте вы, как работодатель.",
        "Выгрузим ваши расчёты в Excel в любой момент — просто попросите."]),
    ("7. Как расстаться", [
        "Правила действуют с первой оплаты и сами продлеваются каждый месяц, пока вы платите.",
        "Уйти можно в любой момент: доступ работает до конца оплаченного месяца. Платили за год — вернём деньги за "
        "неиспользованные полные месяцы, посчитав прошедшие по обычной цене, без скидки.",
        "После закрытия храним ваши данные 3 месяца: передумали — продолжаем с того же места, подключение заново не "
        "платите. Потом удаляем, раньше — если попросите.",
        "Цену меняем не чаще раза в год и предупреждаем за 30 дней. Оплаченный год не меняется.",
        "Сообщения в приложении и в мессенджере считаются официальными."]),
]

ORDER_RULES = [
    "Первый счёт — только за оставшиеся дни месяца: сумма месяца ÷ дней в месяце × оставшиеся дни.",
    "Добавили функцию в середине месяца — за остаток месяца доплата придёт строкой в счёте 1-го числа, дальше — новая цена.",
    "Убрали — до конца оплаченного месяца всё работает, со следующего счёт меньше.",
    "Платите частями — в приложении видно, сколько оплачено и сколько осталось.",
]
LATE = [("до 5-го", "всё работает, видна строка «счёт — оплатить до 5-го»"), ("6-го", "напоминание"),
        ("10-го", "только просмотр: цифры видно, менять нельзя, отчёты 1С не принимаются"),
        ("15-го", "доступ закрыт, данные хранятся 3 месяца")]


def contract_views(ctx):
    st = state(ctx)
    v = View("Правила предоставления услуг", ctx.cfg.name)
    v.lines([LEAD])
    for title, items in CONTRACT:
        v.lines(items if title.startswith("2.") else [f"• {x}" for x in items], title=title)
    v2 = View("Что входит и сколько стоит", f"набор «{ctx.cfg.set_title}» · {people(ctx, st)} чел.")
    v2.table(["Что", "₽ в месяц"], _client_lines(ctx, st))
    v3 = View("График оплат", ctx.cfg.name)
    if ctx.demo:
        v3.note("Пример: даты и суммы выдуманы — у вашей фирмы будут свои.")
    v3.table(["Когда и что", "₽"], sched_rows(ctx, st))
    return [v, v2, v3]


def order_views(ctx):
    st = state(ctx)
    m = monthly(ctx, st=st)
    v = View("Порядок оплаты", f"{ctx.cfg.name} · {money(m)} в месяц · подключение {money(setup_sum(ctx, st))}")
    v.lines(["Вы точно знаете, с какого дня платите и сколько: всё это видно в приложении, в «Правила и оплата»."])
    v.lines([status(ctx, st)[0]], title="Сейчас")
    if ctx.demo:
        v.note("Пример: даты и суммы выдуманы — у вашей фирмы будут свои.")
    v.table(["Когда и что", "₽"], sched_rows(ctx, st))
    v.lines([f"• {x}" for x in ORDER_RULES], title="Как считается")
    v.table(["Если оплата задержалась", "Что происходит"], [Row([a, b]) for a, b in LATE])
    v.lines(["Оплатили — всё включается в тот же день."])
    return [v]


@screen("bld")
def contract_pdf(ctx):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return billing_screen(ctx)
    return Reply(text="📄 Правила предоставления услуг — простыми словами.",
                 files=[(f"Правила предоставления услуг {today_(ctx):%d.%m.%Y}.pdf", to_pdf(contract_views(ctx)))],
                 buttons=[[Button("📄 Правила и оплата", "bl")]])


@screen("blo")
def order_pdf(ctx):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return billing_screen(ctx)
    return Reply(text="📄 Порядок оплаты — даты и суммы вашей фирмы.",
                 files=[(f"Порядок оплаты {today_(ctx):%d.%m.%Y}.pdf", to_pdf(order_views(ctx)))],
                 buttons=[[Button("📄 Правила и оплата", "bl")]])


def people_limit(ctx):
    """Новый сотрудник сверх тарифа (Адам 01.11: добавлять людей — только после одобрения) → Reply с объяснением, иначе None."""
    if ctx.demo:
        return None
    st = state(ctx)
    if st.get("people") is None:
        return None
    from core.ui.tariff import counts_of
    now = sum(counts_of(ctx).values())
    if now < int(st["people"]):
        return None
    if ctx.role in ("owner", "admin"):
        return Reply(text=f"👥 По тарифу {st['people']} чел., в приложении уже {now}. Чтобы добавить людей — "
                          "«➕ Добавить или ➖ убрать»: сразу увидите цену, включим после одобрения.",
                     buttons=[[Button("➕ Добавить людей", "blc")], [Button("👥 Сотрудники", "pa")]])
    return Reply(text=f"👥 По тарифу {st['people']} чел., в приложении уже {now}. Попросите директора добавить людей "
                      "в «📄 Правила и оплата».", buttons=[[Button("👥 Сотрудники", "pa")]])
