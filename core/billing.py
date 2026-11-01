"""📄 Договор и оплата (Адам 01.11): клиент видит, с какого дня платит, сколько бесплатных дней осталось, следующий счёт,
график по датам, договор и порядок оплаты (PDF). Добавить или убрать функции и людей — окно с тремя цифрами
(«сейчас за остаток месяца», «со следующего каждый месяц», «счёт 1-го, итого») → заявка → одобряет администратор платформы.

Порядок оплаты (утверждён Адамом 01.11):
  договор — первая половина подключения; запуск — вторая; 14 дней с запуска (включая его) — бесплатно;
  15-й день — счёт за остаток месяца по дням, оплата за 5 дней (осталось ≤ 3 дней — в счёт 1-го числа);
  дальше счёт 1-го, оплата до 5-го; 6-го напоминание, 10-го — только смотреть, 15-го — отключение, данные 3 месяца.
  Добавили функцию / людей — доплата за остаток месяца по дням в следующем счёте, дальше новая цена;
  убрали — до конца оплаченного месяца всё работает, со следующего счёт меньше.

Где лежит: у клиента — ключ «billing» в его базе {contract, launch, setup, people, functions, paid: [месяцы], extras: [...],
req: [...]}; одобренные функции перекрывают анкету (core/ui/context). В демо — то же в сессии зрителя («bl»), даты выдуманы:
договор 14 дней назад, запуск 10 дней назад; заявку зритель одобряет сам кнопкой «🎭 Одобрить как Алапа».
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
STEPS = (("remind", 1), ("view", 5), ("off", 10))     # после срока оплаты: напоминание, только смотреть, отключение

menu("bl", "📄 Договор и оплата", {"owner", "admin"}, order=85)


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
    pk = E.packages_of(funcs(ctx))
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


def due_bills(ctx, st):
    """Выставленные и не оплаченные счета на сегодня: [(месяц, счёт)]."""
    paid = set(st.get("paid") or [])
    out = []
    for mo in months_to_pay(ctx, st, month_of(ctx.today)):
        b = bill(ctx, st, mo)
        if b and b[0] <= ctx.today and mo not in paid:
            out.append((mo, b))
    return out


def next_bill(ctx, st):
    """Ближайший будущий счёт: (месяц, счёт) или None."""
    fp = first_paid(ctx, st)
    if not fp:
        return None
    mo = max(first_bill_month(fp), month_of(ctx.today))
    for _ in range(3):
        b = bill(ctx, st, mo)
        if b and b[0] > ctx.today:
            return mo, b
        mo = shift_month(mo, 1)
    return None


def status(ctx, st):
    """Главная строка: (текст, цвет)."""
    if not st.get("contract") and not st.get("launch"):
        return "📝 Договор ещё не подписан — даты появятся после подписания.", None
    fp = first_paid(ctx, st)
    if not fp:
        return f"🛠 Настраиваем приложение. {trial_days(ctx)} бесплатных дней начнутся со дня запуска.", "blue"
    if ctx.today < fp:
        left = (fp - ctx.today).days
        return f"🎁 Бесплатно ещё {left} дн. — до {dm(fp - timedelta(days=1))} включительно.", "good"
    due = due_bills(ctx, st)
    if due:
        mo, (day, pay_by, amount, _) = due[0]
        late = (ctx.today - pay_by).days
        what = f"Счёт за {mname(mo)} · {money(amount)}"
        if late <= 0:
            return f"💳 {what} — оплатить до {dm(pay_by)}.", "warn"
        view_from = pay_by + timedelta(days=STEPS[1][1])
        off_from = pay_by + timedelta(days=STEPS[2][1])
        if ctx.today < view_from:
            return f"⚠️ {what} не оплачен. С {dm(view_from)} — только просмотр.", "bad"
        if ctx.today < off_from:
            return f"⚠️ {what} не оплачен — по договору только просмотр. С {dm(off_from)} — отключение.", "bad"
        return f"⛔ {what} не оплачен — по договору доступ закрыт. Данные хранятся 3 месяца.", "bad"
    nb = next_bill(ctx, st)
    paid = sorted(st.get("paid") or [])
    head = f"✅ Оплачено по {mname(paid[-1])}." if paid else "✅ Счетов к оплате нет."
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
    if ctx.today < fp:
        return f"🎁 Бесплатно ещё {(fp - ctx.today).days} дн. — «📄 Договор и оплата»."
    if due_bills(ctx, st):
        return status(ctx, st)[0]
    nb = next_bill(ctx, st)
    if nb and (nb[1][0] - ctx.today).days <= 3:
        return f"💳 Счёт через {(nb[1][0] - ctx.today).days} дн.: {dm(nb[1][0])} · {money(nb[1][2])}."
    return ""


# ---------- график ----------

def schedule(ctx, st):
    """[(дата или текст, что, сумма, отметка)] — от договора до трёх месяцев вперёд."""
    setup = setup_sum(ctx, st)
    half = setup // 2
    paid = set(st.get("paid") or [])
    out = []
    c, launch = _d(st.get("contract")), _d(st.get("launch"))
    out.append((dm(c) if c else "при подписании", "Договор — первая половина подключения", half, "✓" if c else ""))
    out.append((dm(launch) if launch else "день запуска", "Запуск — вторая половина подключения", setup - half,
                "✓" if launch else ""))
    fp = first_paid(ctx, st)
    td = trial_days(ctx)
    if launch:
        out.append((f"{dm(launch)} – {dm(fp - timedelta(days=1))}", f"{td} дней бесплатно", 0,
                    "сейчас" if ctx.today < fp else "✓"))
        start = first_bill_month(fp)
        for mo in [start, shift_month(start, 1), shift_month(start, 2)]:
            day, due, amount, lines = bill(ctx, st, mo)
            what = "Первый счёт" if mo == start else f"Счёт за {mname(mo)}"
            mark = "оплачен" if mo in paid else ("ждёт оплаты" if day <= ctx.today else "")
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
        return Reply(text="Договор и оплату видит директор.")
    st = state(ctx)
    if ctx.demo:
        save(ctx, st)
    text, tone = status(ctx, st)
    m = monthly(ctx, st=st)
    sub = f"{ctx.cfg.name} · набор «{ctx.cfg.set_title}» · {people(ctx, st)} чел."
    v = View("📄 Договор и оплата", sub)
    fp = first_paid(ctx, st)
    tiles = []
    if fp and ctx.today < fp:
        tiles.append(("Бесплатно, дней", str((fp - ctx.today).days), "good"))
    nb = next_bill(ctx, st)
    if nb:
        tiles.append((f"Счёт {dm(nb[1][0])}", money(nb[1][2]), None))
    tiles.append(("В месяц", money(m), None))
    v.tiles(tiles)
    v.lines([text], title="Сейчас")
    if note:
        v.note(note)
    v.table(["Когда", "Что", "₽"], [Row([when, what, "—" if amt is None else money(amt, "")],
                                          sub=mark, mark="good" if mark in ("✓", "оплачен") else "warn" if mark == "ждёт оплаты" else None)
                                         for when, what, amt, mark in schedule(ctx, st)])
    if nb and len(nb[1][3]) > 1:
        v.table([f"Счёт {dm(nb[1][0])}", "₽"], [Row([a, money(b, "")]) for a, b in nb[1][3]] +
                [Row([Cell("Итого", bold=True), Cell(money(nb[1][2], ""), bold=True)])])
    v.lines(["Счёт — 1-го числа, оплата до 5-го. 6-го — напоминание, 10-го — только просмотр, 15-го — отключение; "
             "данные хранятся 3 месяца.",
             "Добавить или убрать функции и людей — кнопкой ниже: сразу видно, сколько это стоит, включаем после одобрения."],
            title="Как платим")
    kb = []
    wait = [r for r in st.get("req") or [] if r.get("status") == "wait"]
    if wait:
        r = wait[-1]
        v.lines([f"⏳ Ждёт одобрения: {req_title(r)} · с {mgen(r['from'])} {signed(r['new'] - r['old'])} в месяц"],
                title="Заявка")
        kb.append([Button("✖️ Отозвать заявку", f"blx:{r['id']}")])
        if ctx.demo:
            kb.append([Button("🎭 Одобрить как Алапа", f"bly:{r['id']}",
                              info="В демо одобряете вы сами — у настоящего клиента заявка уходит нам, "
                                   "и функция включается после нашего «✅ Одобрить».")])
    else:
        kb.append([Button("➕ Добавить или ➖ убрать функции и людей", "blc")])
    kb.append([Button("📄 Договор PDF", "bld"), Button("📄 Порядок оплаты PDF", "blo")])
    done = [r for r in st.get("req") or [] if r.get("status") in ("ok", "no")][-3:]
    if done:
        v.lines([f"{'✅' if r['status'] == 'ok' else '❌'} {r['ts']} · {req_title(r)}"
                 + (f" — {r['why']}" if r.get("why") else "") for r in reversed(done)], title="Прошлые заявки")
    if not ctx.demo:
        kb.append([Button("💳 Тариф", "tf")])
    if is_admin(ctx) and not ctx.demo:
        kb.append([Button("🛠 Даты и оплата (Алапа)", "blm")])
    return Reply(view=v, buttons=kb)


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
    if d > 0 and fp and ctx.today >= fp:
        now = part(d, ctx.today)
    nxt = shift_month(month_of(ctx.today), 1)
    if fp and ctx.today < fp:                     # бесплатные дни: новая цена — с первого счёта
        nb = next_bill(ctx, st)
        nxt = nb[0] if nb else first_bill_month(fp)
    st2 = dict(st, extras=list(st.get("extras") or []) + ([{"month": nxt, "amount": now,
                                                             "title": f"доплата за {mname(month_of(ctx.today))}"}]
                                                          if now else []))
    b = bill(ctx, st2, nxt, new) if fp else None
    return old, new, now, nxt, b


def explain(ctx, d, now):
    t = tail_days(ctx.today)
    m = mgen(month_of(ctx.today))
    if d > 0 and now:
        return (f"Новая функция включается сразу. За оставшиеся {t} дн. {m} платите только за эти дни: "
                f"{money(d)} ÷ {dim(ctx.today)} дн. × {t} дн. ≈ {money(now)}. Эта доплата придёт одной строкой "
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
    cur = mgen(month_of(ctx.today))
    fp = first_paid(ctx, st)
    free = not fp or ctx.today < fp
    rows_ = [Row(["Сейчас — бесплатные дни" if free else f"Сейчас, за остаток {cur} ({tail_days(ctx.today)} дн.)",
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
         "new": new, "now": now, "from": nxt, "status": "wait", "ts": f"{ctx.today:%d.%m} {datetime.now():%H:%M}",
         "who": ctx.name(), "uid": ctx.user_id, "funcs": sorted(new_f)}
    reqs.append(r)
    st["req"] = reqs[-50:]
    save(ctx, st)
    if not ctx.demo:
        ctx.db.audit(ctx.author(), "billing_req", req_title(r))
    out = []
    if not ctx.demo:
        text = (f"📄 {ctx.cfg.name}: {ctx.name()} просит: {req_title(r)}.\n"
                f"Сейчас за остаток {mgen(month_of(ctx.today))}: {signed(now) if now else '0 ₽'}\n"
                f"С {mgen(nxt)} каждый месяц: {money(old)} → {money(new)} ({signed(new - old)})")
        out += [Reply(text=text, to=a, buttons=[[Button("✅ Одобрить", f"bly:{rid}"), Button("❌ Отказать", f"bln:{rid}")]])
                for a in ctx.app.admins if a != ctx.user_id]
    out.append(billing_screen(ctx, "⏳ Заявка отправлена на одобрение." + (
        " В демо одобрите её сами — кнопкой «🎭 Одобрить как Алапа»." if ctx.demo else
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
        return Reply(text="Одобряет администратор Алапы.")
    st = state(ctx)
    r = _find(st, rid)
    if not r or r.get("status") != "wait":
        return billing_screen(ctx, "Эта заявка уже решена.")
    fs = set(r["funcs"])
    old, new, now, nxt, _ = calc(ctx, st, fs, r["people"][1])          # доплата — на день одобрения
    r.update(status="ok", now=now, new=new, decided=f"{ctx.today:%d.%m} {datetime.now():%H:%M}")
    if now:
        st.setdefault("extras", []).append({"month": nxt, "amount": now, "title": f"доплата за {mname(month_of(ctx.today))}: "
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
                                  + (f", за остаток {mgen(month_of(ctx.today))} {money(now)} — в следующем счёте." if now else "."),
                             to=r["uid"], buttons=[[Button("📄 Договор и оплата", "bl")]]))
    out.append(billing_screen(ctx.app.ctx(ctx.user_id), f"✅ Одобрено: {req_title(r)}."))
    return out


@screen("bln")
def reject(ctx, rid):
    if not is_admin(ctx):
        return Reply(text="Решает администратор Алапы.")
    r = _find(state(ctx), rid)
    if not r or r.get("status") != "wait":
        return billing_screen(ctx, "Эта заявка уже решена.")
    return ctx.ask("bl_no", f"Отказ: {req_title(r)}.\nНапишите причину для клиента или «-» — без комментария.",
                   {"id": rid}, buttons=[[Button("✖️ Отмена", "bl")]])


@text_input("bl_no")
def on_reject(ctx, text, data):
    st = state(ctx)
    r = _find(st, data.get("id"))
    if not r or r.get("status") != "wait" or not is_admin(ctx):
        return billing_screen(ctx)
    why = (text or "").strip()
    r.update(status="no", why="" if why in ("-", "—") else why[:300], decided=f"{ctx.today:%d.%m} {datetime.now():%H:%M}")
    save(ctx, st)
    ctx.db.audit(ctx.author(), "billing_no", req_title(r))
    out = []
    if r.get("uid") and r["uid"] != ctx.user_id:
        out.append(Reply(text=f"❌ Заявка не одобрена: {req_title(r)}." + (f"\n{r['why']}" if r["why"] else ""),
                         to=r["uid"], buttons=[[Button("📄 Договор и оплата", "bl")]]))
    out.append(billing_screen(ctx, "Отказ отправлен клиенту."))
    return out


# ---------- для администратора платформы: даты и оплата ----------

@screen("blm")
def manage(ctx, act=None):
    if not is_admin(ctx) or ctx.demo:
        return billing_screen(ctx)
    st = state(ctx)
    t = ctx.today.isoformat()
    note = None
    if act == "c":
        st["contract"] = t
        st["setup"] = setup_sum(ctx, st)
        note = "📝 Договор — сегодня."
    elif act == "l":
        st["launch"] = t
        st.setdefault("contract", t)
        st["setup"] = setup_sum(ctx, st)
        if st.get("people") is None:
            st["people"] = people(ctx, st)
        note = "🚀 Запуск — сегодня, бесплатные дни пошли."
    elif act == "p":
        due = due_bills(ctx, st)
        nb = next_bill(ctx, st)
        mo = due[0][0] if due else (nb[0] if nb else None)
        if mo:
            st["paid"] = sorted(set(st.get("paid") or []) | {mo})
            note = f"✅ Оплачен счёт за {mname(mo)}."
    elif act == "u" and st.get("paid"):
        mo = sorted(st["paid"])[-1]
        st["paid"] = sorted(st["paid"])[:-1]
        note = f"↩️ Оплата за {mname(mo)} снята."
    elif act == "d":
        return ctx.ask("bl_dates", "Даты через пробел: договор и запуск, например «16.11.2026 20.11.2026». "
                                   "Запуска ещё не было — только дату договора.", buttons=[[Button("✖️ Отмена", "blm")]])
    if act:
        save(ctx, st)
        ctx.db.audit(ctx.author(), "billing", note or act)
    v = View("🛠 Даты и оплата", ctx.cfg.name)
    v.table(["Что", "Сейчас"], [Row(["Договор", dm(_d(st["contract"])) if st.get("contract") else "—"]),
                                Row(["Запуск", dm(_d(st["launch"])) if st.get("launch") else "—"]),
                                Row(["Подключение", money(setup_sum(ctx, st))]),
                                Row(["Людей по тарифу", str(people(ctx, st))]),
                                Row(["Оплачены месяцы", ", ".join(mname(m) for m in sorted(st.get("paid") or [])) or "—"])])
    if note:
        v.note(note)
    v.note("Пока нет приложения управления оплатами — отмечаешь здесь сам. Неоплата бота не выключает: шаги 6 / 10 / 15 "
           "клиент видит текстом, решение за тобой.")
    due = due_bills(ctx, st)
    nb = next_bill(ctx, st)
    pay_mo = due[0][0] if due else (nb[0] if nb else None)
    kb = [[Button("📝 Договор сегодня", "blm:c"), Button("🚀 Запуск сегодня", "blm:l")],
          [Button(f"✅ Оплачен {mname(pay_mo)}", "blm:p")] if pay_mo else [],
          [Button("↩️ Снять последнюю оплату", "blm:u")] if st.get("paid") else [],
          [Button("✏️ Вписать даты", "blm:d")]]
    return Reply(view=v, buttons=[r for r in kb if r])


@text_input("bl_dates")
def on_dates(ctx, text, data):
    if not is_admin(ctx):
        return billing_screen(ctx)
    ds = []
    for w in (text or "").replace(",", " ").split():
        try:
            ds.append(datetime.strptime(w.strip(), "%d.%m.%Y").date())
        except ValueError:
            continue
    if not ds or len(ds) > 2 or (len(ds) == 2 and ds[1] < ds[0]):
        return ctx.ask("bl_dates", "Не понял. Пример: «16.11.2026 20.11.2026» — договор, потом запуск.",
                       buttons=[[Button("✖️ Отмена", "blm")]])
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
                    sub="две половины: при подписании и в день запуска"))
    return rows


CONTRACT = [
    ("1. Кто и о чём договаривается", [
        "Мы — Алапа: …………. Вы — …………, подписывает …………",
        "Мы настраиваем для вас приложение «Алапа» и даём вашим сотрудникам доступ к нему с телефона и компьютера.",
        "Приложение берёт цифры из вашей 1С, показывает, кто как работает, и считает зарплату по вашим правилам.",
        "Что входит и сколько стоит — Приложение 1, когда платить — Приложение 2.",
        "Приложение наше. Пользуется им ваша фирма, доступ другим фирмам не передаётся."]),
    ("2. Подключение и 14 дней бесплатно", [
        "Мы настраиваем: отделы, должности, как считается зарплата, приём отчётов из 1С, вход сотрудников; даём инструкцию.",
        "Вы даёте: анкету, список сотрудников, правила зарплаты, образцы отчётов; ваш человек или программист 1С "
        "настраивает выгрузку по нашей инструкции.",
        "Срок — до … рабочих дней после первой оплаты и всего, что выше. Ждём что-то от вас — срок сдвигается.",
        "Запуск — день, когда мы написали «всё работает»: в приложении ваши цифры, сотрудники могут войти.",
        "14 дней бесплатно — со дня запуска. Всё, что вы выбрали, работает, за месяц вы не платите. Подключение сюда не входит.",
        "Передумали за эти 14 дней — напишите нам: за месяц ничего не платите, первая половина подключения не "
        "возвращается, вторую (если ещё не оплатили) не платите."]),
    ("3. Сколько и когда платить", [
        "Подключение — один раз, двумя половинами: при подписании и в день запуска.",
        "Первый месяц — с 15-го дня после запуска до конца месяца, только за эти дни; оплата за 5 дней. "
        "Осталось 3 дня и меньше — эти дни войдут в счёт 1-го числа.",
        "Дальше каждый месяц — счёт 1-го числа, оплата до 5-го, вперёд за этот месяц.",
        "Сотрудники считаются на 1-е число; директор и администратор — бесплатно. За год сразу — скидка 10 %.",
        "Добавить функцию, отдел или людей — заявка в приложении, цена видна сразу, включаем после нашего одобрения; "
        "за остаток месяца доплата по дням в следующем счёте.",
        "Убрать — тоже заявка и наше одобрение; до конца оплаченного месяца всё работает, со следующего счёт меньше.",
        "Новая функция под вас: описываете в приложении, мы показываем эскиз и цену, делаем после вашего «Согласен». "
        "Маленькая (до 1 рабочего дня) — в счёт 1-го числа; большая — половина после «Согласен», половина после запуска."]),
    ("4. Если оплата не пришла", [
        "6-е — напоминание, всё работает. 10-е — только смотреть, новые отчёты 1С не принимаются. 15-е — доступ закрыт.",
        "Оплатили — включаем в тот же день. Данные после закрытия храним 3 месяца, потом удаляем.",
        "Договорились об отсрочке — даты сдвигаются."]),
    ("5. Ваши данные", [
        "Все цифры и списки сотрудников — ваши. Используем только для вашего приложения и никому не передаём.",
        "Данные лежат на сервере, у каждой фирмы отдельно; другие клиенты их не видят.",
        "Согласие сотрудников на обработку их данных берёте вы, как работодатель.",
        "Выгрузим расчёты в Excel по просьбе; расстаёмся — удалим всё через 3 месяца или раньше, если попросите."]),
    ("6. Кто за что отвечает", [
        "Мы — чтобы приложение работало и считало по договорённым правилам; ошибки в расчёте исправляем бесплатно.",
        "Вы — за цифры в 1С и за итоговую зарплату: перед выплатой её проверяет и утверждает ваш руководитель.",
        "Плановые работы — предупреждаем заранее. Не работало по нашей вине больше суток — вычитаем эти дни из счёта.",
        "Больше чем сумма за один месяц никто никому не должен."]),
    ("7. Срок, цены и как расстаться", [
        "Договор действует со дня подписания и сам продлевается каждый месяц, пока вы платите.",
        "Расстаться можно в любой момент: доступ работает до конца оплаченного месяца. Платили за год — вернём за "
        "неполные месяцы по обычной цене.",
        "Цену меняем не чаще раза в год и предупреждаем за 30 дней; оплаченный год не меняется.",
        "Письма, сообщения в приложении и в мессенджере считаются официальными. Споры — сначала разговором."]),
]


def contract_views(ctx):
    st = state(ctx)
    c = _d(st.get("contract"))
    v = View("Договор оказания услуг «Алапа»", f"№ …… · {dm(c) + ' ' + str(c.year) if c else 'дата ……'} · {ctx.cfg.name}")
    for title, items in CONTRACT:
        v.lines([f"• {x}" for x in items], title=title)
    v2 = View("Приложение 1. Что входит и сколько стоит", f"набор «{ctx.cfg.set_title}» · {people(ctx, st)} чел.")
    v2.table(["Что", "₽ в месяц"], _client_lines(ctx, st))
    v3 = View("Приложение 2. График оплат", ctx.cfg.name)
    v3.table(["Когда", "Что", "₽"], [Row([w, x, "—" if a is None else money(a, "")]) for w, x, a, _ in schedule(ctx, st)])
    v3.lines(["Мы: …………  ИНН ……  подпись ……", "Вы: …………  ИНН ……  подпись ……"], title="Подписи")
    return [v, v2, v3]


def order_views(ctx):
    st = state(ctx)
    m = monthly(ctx, st=st)
    v = View("Порядок оплаты", f"{ctx.cfg.name} · {money(m)} в месяц · подключение {money(setup_sum(ctx, st))}")
    v.lines([status(ctx, st)[0]], title="Сейчас")
    v.table(["Когда", "Что", "₽"], [Row([w, x, "—" if a is None else money(a, "")], sub=mk) for w, x, a, mk in schedule(ctx, st)])
    v.lines(CONTRACT[1][1][3:5] + CONTRACT[2][1] + CONTRACT[3][1], title="Правила")
    return [v]


@screen("bld")
def contract_pdf(ctx):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return billing_screen(ctx)
    return Reply(text="📄 Договор — простыми словами. Реквизиты впишем при подписании.",
                 files=[(f"Договор Алапа {ctx.today:%d.%m.%Y}.pdf", to_pdf(contract_views(ctx)))],
                 buttons=[[Button("📄 Договор и оплата", "bl")]])


@screen("blo")
def order_pdf(ctx):
    if ctx.role not in ("owner", "admin") and not is_admin(ctx):
        return billing_screen(ctx)
    return Reply(text="📄 Порядок оплаты — даты и суммы вашей фирмы.",
                 files=[(f"Порядок оплаты Алапа {ctx.today:%d.%m.%Y}.pdf", to_pdf(order_views(ctx)))],
                 buttons=[[Button("📄 Договор и оплата", "bl")]])


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
                      "в «📄 Договор и оплата».", buttons=[[Button("👥 Сотрудники", "pa")]])
