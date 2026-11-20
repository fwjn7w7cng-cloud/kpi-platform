"""📄 Правила и оплата (Адам 01.11; без договора и подписей — «правила предоставления услуг»): клиент видит, до какого дня
оплачено, следующую оплату, график по датам, правила и порядок оплаты (PDF). Добавить или убрать функции и людей — окно
с цифрами («сейчас за дни до оплаты», «с <дня оплаты> каждый месяц», «оплата <дата>, итого») → заявка → одобряет
администратор платформы.

Порядок оплаты (Адам 20.11, вместо порядка 01.11 с 14 бесплатными днями и счётом 1-го числа):
  договорились — первая половина подключения; запуск — вторая половина и первый месяц (бесплатных дней нет);
  месяц — с дня запуска до того же числа (запуск 20.11 → оплачено по 19.12), дальше платят каждый месяц в число запуска
  (20-го; запуск 29–31-го — в коротком месяце последний день). Ключ периода — месяц его начала («2026-11» = 20.11–19.12).
  Срок — сам день оплаты; +1 день напоминание, +5 только просмотр, +10 закрыто, данные 3 месяца.
  Добавили функцию / людей — доплата по дням до следующей оплаты, строкой в ней; убрали — до следующей оплаты всё как было.

Где лежит: у клиента — ключ «billing» в его базе {contract, launch, setup, people, functions, paid: [периоды], got: {период: сколько пришло}, marks: [отметки для «снять последнюю»], extras: [...],
req: [...]}; одобренные функции перекрывают анкету (core/ui/context). В демо — то же в сессии зрителя («bl»), даты выдуманы:
договорились 14 дней назад, запуск 10 дней назад (первый месяц оплачен); заявку зритель одобряет сам кнопкой «🎭 Одобрить».
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
STEPS = (("remind", 1), ("view", 5), ("off", 10))   # дней после дня оплаты: напоминание, только смотреть, отключение
HOLD_DAYS = 7                # «⏳ Отсрочка» — на столько дней от сегодня

# Адам 17.11: в демо «Правила и оплата» не показываем — код, экраны и тексты остаются; вернуть — DEMO_SHOW = True.
DEMO_SHOW = False
menu("bl", "📄 Правила и оплата", {"owner", "admin"}, order=85, when=lambda c: DEMO_SHOW or not c.demo)


# ---------- состояние ----------

def is_admin(ctx):
    return ctx.user_id in ctx.app.admins


def state(ctx):
    if ctx.demo:
        st = ctx.session.get("bl")
        if not isinstance(st, dict) or st.get("day") != ctx.today.isoformat():
            st = {"day": ctx.today.isoformat(), "contract": (ctx.today - timedelta(days=14)).isoformat(),
                  "launch": (ctx.today - timedelta(days=10)).isoformat(),
                  "paid": [month_of(ctx.today - timedelta(days=10))], "extras": [], "req": []}
            ctx.session["bl"] = st
        return st
    return ctx.office.get(KEY) or {}


def acc_upgrade(cfg, office):
    """Учёт в Алапе стал отдельными функциями (Адам 20.11). У клиента с учётом в Алапе, созданного раньше, — один раз
    включить их, чтобы ничего не закрылось замком. Возвращает состояние оплаты (billing)."""
    b = office.get(KEY) or {}
    if (cfg.source or {}).get("kind") != "alapa" or office.get("acc_v1"):
        return b
    from core.ui.tariff import funcs_of
    fs = set(b["functions"]) if isinstance(b.get("functions"), list) else set(funcs_of(cfg))
    seg = cfg.segment or "opt"
    fs |= {"goods", "ship"} | ({"shop_sale"} if seg != "opt" else set()) | ({"opt_sale"} if seg != "retail" else set())
    b = {**b, "functions": sorted(fs)}
    office.put(KEY, b)
    office.put("acc_v1", True)
    return b


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


# ---------- деньги ----------

def funcs(ctx):
    from core.ui.tariff import funcs_of
    return funcs_of(ctx.cfg)


def people(ctx, st=None):
    from core.ui.tariff import counts_of
    st = state(ctx) if st is None else st
    if not ctx.demo:                                               # люди заведены — счёт по ним сам (Адам 20.11)
        n = real_people(ctx)
        if n:
            return n
    return int(st["people"]) if st.get("people") is not None else sum(counts_of(ctx).values())


def place_of(ctx, e):
    """Строка «Люди по отделам», где числится сотрудник (ключ people_rows)."""
    from core.ui.retail import shop_dept, shops
    sd = shop_dept(ctx.cfg)
    wd = next((d.id for d in ctx.cfg.departments if d.kind == "warehouse"), None)
    ws = (e.get("extra") or {}).get("whs") or []
    if e["dept"] == sd:
        return f"{sd}/{e['team']}"
    if e["dept"] == wd:
        if e["role"] == "head":
            return f"{wd}/head"
        if ws:
            return f"{wd}/@{ws[0]}"
        sid = next((s for s, _ in shops(ctx.cfg) if e["team"] == f"{wd}_{s}"), None)
        if sid:
            return f"{wd}/s:{sid}"                                 # складской магазина без своего склада
    return e["dept"]


def people_rows(ctx):
    """Отделы клиента для «👥 Люди» в пульте (Адам 20.11: «по его отделам»): [(ключ, подпись, пакет, в приложении)].
    Магазины — по одному; общие склады — по складу, склад магазина (или складской магазина без своего склада) — под магазином,
    завсклад — своей строкой. Директор и админ клиента не считаются (бесплатно)."""
    from core.ui.retail import shop_dept
    from modules.ship import owners, whs
    sd = shop_dept(ctx.cfg)
    app = {}
    for e in ctx.db.employees():
        if e["role"] in ("owner", "admin"):
            continue
        k = place_of(ctx, e)
        app[k] = app.get(k, 0) + 1
    places = whs(ctx) if (ctx.cfg.warehouses or ctx.db.get("warehouses")) else []    # склады учёта клиента (Адам 20.11)
    wh_dept = next((d.id for d in ctx.cfg.departments if d.kind == "warehouse"), None)
    split = wh_dept and len(places) > 1
    ow = owners(ctx) if split else {}
    wpkg = E.dept_package("warehouse") or "stock"

    def wlabel(wname):
        return wname if wname.lower().startswith("склад") else f"Склад · {wname}"
    out = []
    for d in ctx.cfg.departments:
        pkg = E.dept_package(d.kind) or "office"
        if d.id == wh_dept and split:                             # общие склады — здесь, склады магазинов — под своим магазином
            for wid, wname in places:
                if not ow.get(wid):
                    out.append((f"{wh_dept}/@{wid}", f"Общий · {wlabel(wname)}", wpkg, app.get(f"{wh_dept}/@{wid}", 0)))
            out.append((f"{wh_dept}/head", "Завсклад · все общие склады", wpkg, app.get(f"{wh_dept}/head", 0)))
            rest = app.get(wh_dept, 0)
            if rest:
                out.append((wh_dept, f"{d.name} · без склада", wpkg, rest))
        elif d.id == sd:
            for t in ctx.cfg.teams_of(d.id):
                out.append((f"{d.id}/{t.id}", f"{d.name} · {t.name}", pkg, app.get(f"{d.id}/{t.id}", 0)))
                own = [(w, n) for w, n in places if split and ow.get(w) == t.id]
                for wid, wname in own:
                    out.append((f"{wh_dept}/@{wid}", f"{t.name} · {wlabel(wname)}", wpkg, app.get(f"{wh_dept}/@{wid}", 0)))
                if wh_dept and app.get(f"{wh_dept}/s:{t.id}"):
                    out.append((f"{wh_dept}/s:{t.id}", f"{t.name} · складской", wpkg, app[f"{wh_dept}/s:{t.id}"]))
        else:
            n = app.get(d.id, 0) + (app.get(f"{d.id}/head", 0) if d.id == wh_dept else 0) + \
                sum(v for k, v in app.items() if d.id == wh_dept and (k.startswith(f"{wh_dept}/@") or k.startswith(f"{wh_dept}/s:")))
            out.append((d.id, d.name, pkg, n))
    return out


def real_people(ctx):
    """Люди в приложении клиента за деньги (без владельца и администратора). 0 — людей ещё не заводили."""
    return sum(1 for e in ctx.db.employees() if e["role"] not in ("owner", "admin"))


def dcounts(ctx, st=None):
    """Люди по отделам клиента: (числа {ключ: n}, типовые ли). Вписаны в пульте — они; нет — сколько есть в приложении,
    а пустое приложение — типовые числа размера, разложенные на первый отдел каждого раздела (с пометкой «типовые»)."""
    st = state(ctx) if st is None else st
    rows = people_rows(ctx)
    real = {k: n for k, _, _, n in rows}
    if not ctx.demo and sum(real.values()):                        # люди заведены — считаем по ним (Адам 20.11)
        return real, False
    if isinstance(st.get("dcounts"), dict):
        return {k: int(st["dcounts"].get(k) or 0) for k, *_ in rows}, False
    if sum(real.values()):
        return real, False
    from core.ui.tariff import counts_of
    old = st.get("counts") if isinstance(st.get("counts"), dict) else None
    base = dict(old) if old else {}
    if not base:
        n = people(ctx, st)
        pk = E.packages_of(funcs(ctx), ctx.cfg.depts or ())
        base = {next((k for k in E.DEPT_CODES if pk.get(k)), "sales"): n}
    out = {k: 0 for k, *_ in rows}
    for pkg, n in base.items():
        k = next((k for k, _, p, _ in rows if p == pkg), None) or (rows[0][0] if rows else None)
        if k:
            out[k] += int(n or 0)
    diff = people(ctx, st) - sum(out.values())                     # поля всегда в сумме дают «Всего»
    if rows and diff:
        k = next((k for k in out if out[k]), rows[0][0])
        out[k] = max(0, out[k] + diff)
    return out, True


def counts_for(ctx, n):
    """Люди по разделам цены (продажи / склад / логистика / офис), когда всего людей n: разница — на первый подключённый."""
    from core.ui.tariff import counts_of
    st = state(ctx)
    pk = E.packages_of(funcs(ctx), ctx.cfg.depts or ())
    if not ctx.demo and real_people(ctx) or isinstance(st.get("dcounts"), dict):   # по людям / по отделам клиента (Адам 20.11)
        dc, _ = dcounts(ctx, st)
        c = {k: 0 for k in E.DEPT_CODES}
        for key, _, pkg, _ in people_rows(ctx):
            c[pkg if pkg in c else "office"] += int(dc.get(key) or 0)
    elif isinstance(st.get("counts"), dict):
        c = {k: int(st["counts"].get(k) or 0) for k in E.DEPT_CODES}
    else:
        c = dict(counts_of(ctx))
    first = next((k for k in E.DEPT_CODES if pk.get(k)), "sales")
    for k in E.DEPT_CODES:                                    # люди в неподключённом разделе — в первый подключённый
        if k != first and c.get(k) and not pk.get(k):
            c[first] = c.get(first, 0) + c.pop(k)
            c[k] = 0
    diff = n - sum(c.values())
    c[first] = max(0, c.get(first, 0) + diff)
    return c


def monthly(ctx, fs=None, n=None, st=None, on=None):
    """В месяц по тарифу; on — на какой день смотреть скидки из пульта (по умолчанию — сегодня)."""
    from core.ui.tariff import quote_of
    st = state(ctx) if st is None else st
    fs = funcs(ctx) if fs is None else set(fs)
    n = people(ctx, st) if n is None else n
    if not fs:
        return 0
    q = quote_of(ctx, fs, year=False, counts=counts_for(ctx, n) or {"sales": 0})
    ds = active_discounts(st, on or today_(ctx))
    if not ds:
        return q["monthly"]
    return max(0, P.rnd(q["monthly"] - discount_sum(ctx, fs, q, ds)))


# ---------- скидки из пульта (Адам 17.11: «процентные скидки со сроком или бессрочно») ----------
# st["discounts"] = [{id, what: all | people | <функция>, pct, start, till (ISO или None — бессрочно), why, ts}]

def active_discounts(st, on):
    d = on.isoformat() if hasattr(on, "isoformat") else str(on)
    return [x for x in st.get("discounts") or [] if (x.get("start") or "") <= d and (not x.get("till") or d <= x["till"])]


def discount_sum(ctx, fs, q, ds):
    """Сколько рублей снимают скидки: на всё — от месячной суммы, на людей — от их цены, на функцию — от её цены."""
    pr = P.prices(ctx.office)
    out = 0.0
    for x in ds:
        pct = float(x.get("pct") or 0) / 100
        w = x.get("what")
        if w == "all":
            out += q["monthly"] * pct
        elif w == "people":
            out += q["people"] * pr["user"] * pct
        elif w in fs:
            out += pr.get(f"f_{w}", 0) * pct
    return out


def discount_title(x):
    w = x.get("what")
    on = "на всё" if w == "all" else "на людей" if w == "people" else f"на «{E.FUNC_SHORT.get(w, w)}»"
    return f"−{P.fmt(x.get('pct') or 0)}% {on}"


def discount_term(x):
    if not x.get("till"):
        return "бессрочно"
    return f"до {dm(_d(x['till']))} {x['till'][:4]}"


def setup_sum(ctx, st):
    if st.get("setup"):
        return int(st["setup"])
    from core.ui.tariff import quote_of
    return quote_of(ctx, funcs(ctx), year=False)["setup"]


def launch_of(st):
    return _d(st.get("launch"))


def pday(launch, mo):
    """Число оплаты в месяце mo: число запуска (запуск 31-го — в коротком месяце последний день)."""
    f = first_day(mo)
    return date(f.year, f.month, min(launch.day, dim(f)))


def period_of(launch, d):
    """Ключ (месяц начала) оплаченного периода, в котором лежит день d."""
    mo = month_of(d)
    return mo if pday(launch, mo) <= d else shift_month(mo, -1)


def period(launch, mo):
    """(первый день, последний день) периода, начавшегося в месяце mo: 20 ноября – 19 декабря."""
    a = pday(launch, mo)
    return a, pday(launch, shift_month(mo, 1)) - timedelta(days=1)


def period_title(launch, mo):
    a, b = period(launch, mo)
    return f"{dm(a)} – {dm(b)}"


def first_paid(ctx, st):
    """С какого дня платный период (Адам 20.11: бесплатных дней нет) — это день запуска."""
    return launch_of(st)


def bill(ctx, st, mo, m=None):
    """Счёт периода mo: (день оплаты, срок, сумма, строки [(что, сумма)]) или None (до запуска).
    Адам 20.11: в день запуска — вторая половина подключения и первый месяц; дальше каждый месяц в то же число."""
    launch = launch_of(st)
    if not launch or mo < month_of(launch):
        return None
    day = pday(launch, mo)
    if m is None:
        m = monthly(ctx, st=st, on=day)
    lines = []
    if mo == month_of(launch):
        setup = setup_sum(ctx, st)
        if setup - setup // 2:
            lines.append(("Подключение, вторая половина", setup - setup // 2))
    ds = active_discounts(st, day)
    lines.append((period_title(launch, mo) + (" · " + ", ".join(discount_title(x) for x in ds) if ds else ""), m))
    for x in st.get("extras") or []:
        if x.get("month") == mo:
            lines.append((x.get("title") or "доплата", int(x.get("amount") or 0)))
    return day, day, sum(a for _, a in lines), lines


def months_to_pay(ctx, st, upto):
    """Периоды со счётом с первого (запуск) по upto включительно."""
    launch = launch_of(st)
    if not launch:
        return []
    out, mo = [], month_of(launch)
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
    mo = max(month_of(fp), month_of(today_(ctx)))
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


def pname(launch, mo):
    """«за 20 ноября – 19 декабря» — как называем счёт периода."""
    return "за " + period_title(launch, mo)


def status(ctx, st):
    """Главная строка: (текст, цвет)."""
    if not st.get("contract") and not st.get("launch"):
        return "🤝 Ещё не договорились — даты появятся, когда договоримся.", None
    fp = first_paid(ctx, st)
    if not fp:
        return "🛠 Настраиваем приложение. В день запуска — вторая половина подключения и первый месяц.", "blue"
    due = due_bills(ctx, st)
    if due:
        mo, (day, pay_by, amount, _) = due[0]
        late = (today_(ctx) - pay_by).days
        h = hold_till(st)
        if h and today_(ctx) <= h and late > 0:
            g0 = got(st, mo)
            left = f"осталось {money(amount - g0)}" if g0 else money(amount)
            return f"⏳ Счёт {pname(fp, mo)} ({left}) — отсрочка до {dm(h)} включительно.", "warn"
        g = got(st, mo)
        what = (f"Счёт {pname(fp, mo)}: оплачено {money(g)} из {money(amount)}, осталось {money(amount - g)}" if g
                else f"Счёт {pname(fp, mo)} · {money(amount)}")
        if late <= 0:
            return f"💳 {what} — оплатить сегодня, {dm(pay_by)}.", "warn"
        view_from, off_from = limit_dates(ctx, st)
        if today_(ctx) < view_from:
            return f"⚠️ {what} не оплачен. С {dm(view_from)} — только просмотр.", "bad"
        if today_(ctx) < off_from:
            return f"⚠️ {what} не оплачен — по правилам только просмотр. С {dm(off_from)} — отключение.", "bad"
        return f"⛔ {what} не оплачен — по правилам доступ закрыт. Данные хранятся 3 месяца.", "bad"
    nb = next_bill(ctx, st)
    paid = sorted(mo for mo in months_to_pay(ctx, st, month_of(today_(ctx))) if is_paid(st, mo, bill(ctx, st, mo)))
    head = f"✅ Оплачено до {dm(period(fp, paid[-1])[1])} включительно." if paid else "✅ Счетов к оплате нет."
    if nb:
        mo, (day, _, amount, _) = nb
        return f"{head} Следующая оплата {dm(day)} · {money(amount)}.", "good"
    return head, "good"


def home_line(ctx):
    """Плашка на главной у директора и админа: скорая оплата, неоплата."""
    if ctx.role not in ("owner", "admin") or (ctx.demo and not DEMO_SHOW):
        return ""
    st = state(ctx)
    fp = first_paid(ctx, st)
    if not fp:
        return ""
    if due_bills(ctx, st):
        return status(ctx, st)[0]
    nb = next_bill(ctx, st)
    if nb and (nb[1][0] - today_(ctx)).days <= 3:
        return f"💳 Оплата через {(nb[1][0] - today_(ctx)).days} дн.: {dm(nb[1][0])} · {money(nb[1][2])}."
    return ""


# ---------- график ----------

def schedule(ctx, st):
    """[(дата или текст, что, сумма, отметка)]: договорились, запуск (первый счёт), дальше — ближайшие оплаты."""
    setup = setup_sum(ctx, st)
    half = setup // 2
    out = []
    c, launch = _d(st.get("contract")), _d(st.get("launch"))
    out.append((dm(c) if c else "когда договоримся", "Договорились — первая половина подключения", half, "✓" if c else ""))
    if launch:
        first = month_of(launch)
        cur = period_of(launch, today_(ctx)) if today_(ctx) >= launch else first
        mos = [first] + [m for m in (cur, shift_month(cur, 1), shift_month(cur, 2)) if m > first]
        for mo in mos[:4]:
            day, due, amount, lines = bill(ctx, st, mo)
            what = (f"Запуск — вторая половина подключения и первый месяц ({period_title(launch, mo)})" if mo == first
                    else f"Оплата {pname(launch, mo)}")
            g = got(st, mo)
            mark = ("оплачен" if is_paid(st, mo, (day, due, amount, lines)) else
                    f"оплачено {money(g)}, осталось {money(amount - g)}" if g else
                    "ждёт оплаты" if day <= today_(ctx) else "")
            out.append((dm(day), what, amount, mark))
    else:
        m = monthly(ctx, st=st)
        out.append(("день запуска", "Запуск — вторая половина подключения и первый месяц", setup - half + m, ""))
        out.append(("то же число", "Дальше — каждый месяц в число запуска: запустились 20-го — платите 20-го", m, ""))
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
    paid = [mo for mo in months_to_pay(ctx, st, month_of(today_(ctx))) if is_paid(st, mo, bill(ctx, st, mo))]
    if paid:
        tiles.append(("Оплачено до", dm(period(fp, max(paid))[1]), "good"))
    nb = next_bill(ctx, st)
    if nb:
        tiles.append((f"Оплата {dm(nb[1][0])}", money(nb[1][2]), None))
    tiles.append(("В месяц", money(m), None))
    v.tiles(tiles)
    v.lines([text], title="Сейчас")
    if ctx.demo:
        v.note(f"Пример: даты и суммы выдуманы — у настоящей фирмы будут свои. Смотрим на {dm(today_(ctx))}"
               + (" (сегодня)" if today_(ctx) == ctx.today else "") + ". Листайте дни кнопками ◀ ▶ — увидите, "
               "как приходит день оплаты и что будет, если не оплатить.")
    if note:
        v.note(note)
    v.table(["Когда и что", "₽"], sched_rows(ctx, st))
    if nb and len(nb[1][3]) > 1:
        v.table([f"Оплата {dm(nb[1][0])}", "₽"], [Row([a, money(b, "")]) for a, b in nb[1][3]] +
                [Row([Cell("Итого", bold=True), Cell(money(nb[1][2], ""), bold=True)])])
    v.lines([(f"Платите каждый месяц {fp.day}-го — в число запуска" if fp else "Платите каждый месяц в число запуска")
             + ": в день запуска — подключение и первый месяц, дальше вперёд за следующий. На следующий день после "
             "срока — напоминание, через 5 дней — только просмотр, через 10 — отключение; данные хранятся 3 месяца.",
             "Добавить или убрать функции и людей — кнопкой ниже: сразу видно, сколько это стоит, включаем после одобрения."],
            title="Как платим")
    kb = []
    if ctx.demo:
        kb.append([Button("◀ неделя", "blt:-7"), Button("📅 Сегодня", "blt:0"), Button("неделя ▶", "blt:7")])
    wait = [r for r in st.get("req") or [] if r.get("status") == "wait"]
    if wait:
        r = wait[-1]
        v.lines([f"⏳ Ждёт одобрения: {req_title(r)} · {from_text(st, r['from'])} {signed(r['new'] - r['old'])} в месяц"],
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

def from_text(st, mo):
    """«с 20 декабря» — с какого дня новая цена (период mo)."""
    launch = launch_of(st)
    return f"с {dm(pday(launch, mo))}" if launch and mo else "с запуска"


def left_days(launch, d):
    """(дней до следующей оплаты включая d, дней в периоде, ключ следующего периода)."""
    mo = period_of(launch, d)
    a, b = period(launch, mo)
    return (b - d).days + 1, (b - a).days + 1, shift_month(mo, 1)


def calc(ctx, st, new_f, new_p):
    """Цифры окна: (было в месяц, станет в месяц, сейчас за дни до следующей оплаты, ключ следующего периода, его счёт)."""
    old = monthly(ctx, st=st)
    new = monthly(ctx, new_f, new_p, st)
    d = new - old
    launch = launch_of(st)
    if not launch:
        return old, new, 0, None, None
    t = max(today_(ctx), launch)
    left, total, nxt = left_days(launch, t)
    now = P.rnd(d * left / total) if d > 0 else 0
    st2 = dict(st, extras=list(st.get("extras") or []) + ([{"month": nxt, "amount": now, "title": "доплата за дни до оплаты"}]
                                                          if now else []))
    return old, new, now, nxt, bill(ctx, st2, nxt, new)


def explain(ctx, st, d, now):
    launch = launch_of(st)
    if not launch:
        return "До запуска ничего не доплачиваете: новая сумма — с первого месяца, в день запуска."
    left, total, nxt = left_days(launch, max(today_(ctx), launch))
    nd = dm(pday(launch, nxt))
    if d > 0 and now:
        return (f"Новая функция включается сразу. До следующей оплаты {nd} осталось {left} дн. — за них доплата по дням: "
                f"{money(d)} ÷ {total} дн. × {left} дн. ≈ {money(now)}. Она придёт строкой в оплате {nd} — отдельного "
                "счёта нет. Дальше — полная новая цена.")
    if d < 0:
        return (f"Убираем с {nd}: до этого дня всё работает как было, за оплаченный месяц деньги не возвращаются. "
                "Со следующей оплаты сумма меньше.")
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
    fp = first_paid(ctx, st)
    if fp:
        left = left_days(fp, max(today_(ctx), fp))[0]
        nd = dm(pday(fp, nxt))
    rows_ = [Row(["До запуска — ничего" if not fp else f"Сейчас, за дни до оплаты {nd} ({left} дн.)",
                  Cell(signed(now) if now else "0 ₽", "warn" if now else None, bold=True)],
                 sub="придёт строкой в следующей оплате" if now else ""),
             Row(["С запуска, в месяц" if not fp else f"С {nd} каждый месяц",
                  Cell(signed(diff) if diff else "0 ₽", "good" if diff < 0 else None, bold=True)],
                 sub=f"было {money(old)} → станет {money(new)}" if diff else "")]
    if b:
        v.tiles([(f"Оплата {dm(b[0])}, итого", money(b[2]), None)])
    v.table(["Что меняется", "₽"], rows_)
    if b and (diff or now):
        v.table([f"Оплата {dm(b[0])}", "₽"], [Row([a, money(x, "")]) for a, x in b[3]] +
                [Row([Cell("Итого", bold=True), Cell(money(b[2], ""), bold=True)])])
    if diff < 0 and fp:
        v.note(f"До {nd} всё работает, с {nd} оплата меньше на {money(-diff)}.")
    pr = P.prices(ctx.office)
    from core.ui.screens import LOCK_TEXT
    kb = [[Button("ℹ️ Почему так", "blc:i", info=explain(ctx, st, diff, now))]]
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
                f"Сейчас за дни до оплаты: {signed(now) if now else '0 ₽'}\n"
                f"{from_text(st, nxt).capitalize()} каждый месяц: {money(old)} → {money(new)} ({signed(new - old)})")
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
        st.setdefault("extras", []).append({"month": nxt, "amount": now, "title": f"доплата за дни до оплаты: {req_title(r)}"})
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
            out.append(Reply(text=f"✅ Включено: {req_title(r)}. {from_text(st, nxt).capitalize()} — {money(new)} в месяц"
                                  + (f", за дни до оплаты {money(now)} — в следующей оплате." if now else "."),
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
        note = "🚀 Запуск — сегодня: сегодня же оплата — вторая половина подключения и первый месяц."
    elif act == "p":
        mo = pay_month(ctx, st)
        if mo:
            st["paid"] = sorted(set(st.get("paid") or []) | {mo})
            st.setdefault("marks", []).append({"mo": mo, "all": True})
            note = f"✅ Оплачен весь счёт {pname(launch_of(st), mo)}."
    elif act == "s":
        mo = pay_month(ctx, st)
        if mo:
            b = bill(ctx, st, mo)
            return ctx.ask("bl_sum", f"Сколько пришло {pname(launch_of(st), mo)}? Счёт {money(b[2])}, уже отмечено {money(got(st, mo))}. "
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
        note = f"↩️ Последняя отметка {pname(launch_of(st), mo)} снята."
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
                                Row(["Оплачено", ", ".join(period_title(launch_of(st), m) for m in sorted(st.get("paid") or []))
                                     if st.get("launch") else "—"])] +
            [Row([f"Частично {pname(launch_of(st), m)}", money(a)]) for m, a in sorted((st.get("got") or {}).items())
             if a and m not in (st.get("paid") or [])])
    if note:
        v.note(note)
    v.note("Оплата — каждый месяц в число запуска. После срока: +1 день напоминание, +5 только просмотр, +10 закрыто "
           "(включается само, «⏳ Отсрочка» — остановить). Частичная оплата — клиент видит остаток.")
    pay_mo = pay_month(ctx, st)
    kb = [[Button("🤝 Договорились сегодня", "blm:c"), Button("🚀 Запуск сегодня", "blm:l")],
          [Button(f"✅ Оплачено всё {pname(launch_of(st), pay_mo)}", "blm:p"), Button("✏️ Оплачено частично", "blm:s")] if pay_mo else [],
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
    """Отметить, что за период пришло x (частичная оплата; набралась вся сумма — месяц оплачен)."""
    st = state(ctx)
    st.setdefault("got", {})[mo] = got(st, mo) + x
    st.setdefault("marks", []).append({"mo": mo, "sum": x})
    b = bill(ctx, st, mo)
    if b and got(st, mo) >= b[2]:
        st["paid"] = sorted(set(st.get("paid") or []) | {mo})
    save(ctx, st)
    ctx.db.audit(ctx.author(), "billing", f"оплата {pname(launch_of(st), mo)}: {x}")


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
                    sub="половина, когда договорились, половина — в день запуска вместе с первым месяцем"))
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
        "3) Запуск. В приложении ваши настоящие цифры, люди могут войти. В этот день — вторая половина подключения "
        "и первый месяц. С этого дня всё работает."]),
    ("3. Деньги", [
        "Подключение — один раз: половина, когда договорились, половина в день запуска.",
        "Первый месяц оплачивается в день запуска. Месяц — с этого дня до того же числа следующего месяца: "
        "запустились 20 ноября — оплачено до 20 декабря.",
        "Дальше — каждый месяц в число запуска, вперёд: запустились 20-го — платите 20-го. Запуск 29–31-го — в коротком "
        "месяце платите в его последний день.",
        "В сумму месяца входят выбранные функции и люди в приложении. Директор и администратор — бесплатно.",
        "Можно платить частями: в приложении видно, сколько уже оплачено и сколько осталось.",
        "За год сразу — минус 10 %.",
        "Всё это директор видит в приложении, в «Правила и оплата»: до какого дня оплачено, когда следующая "
        "оплата и на какую сумму. Сюрпризов не будет."]),
    ("4. Растёте — растёт и приложение", [
        "Добавить функцию, отдел или людей. Директор отмечает нужное в приложении и сразу видит три цифры: сколько за "
        "дни до следующей оплаты, сколько дальше в месяц и какой будет следующая оплата. Мы проверяем, что всё настроено, "
        "и включаем — обычно в тот же день.",
        "Убрать. Так же, через приложение. До следующей оплаты всё работает как было, дальше сумма меньше.",
        "Нужно то, чего ещё нет. Опишите в приложении своими словами. Мы уточним, покажем эскиз и цену, а вы правите, "
        "пока не скажете «Согласен». Небольшая доработка (до 1 рабочего дня) придёт строкой в следующей оплате. Большая — "
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
        "Сообщения в приложении считаются официальными."]),
]

ORDER_RULES = [
    "В день запуска — вторая половина подключения и первый месяц. Бесплатных дней нет.",
    "Месяц — с дня запуска до того же числа следующего месяца. Дальше платите каждый месяц в это число, вперёд.",
    "Добавили функцию между оплатами — за дни до следующей оплаты доплата по дням придёт строкой в ней, дальше — новая цена.",
    "Убрали — до следующей оплаты всё работает, дальше сумма меньше.",
    "Платите частями — в приложении видно, сколько оплачено и сколько осталось.",
]
LATE = [("в день оплаты", "всё работает, видна строка «оплатить сегодня»"), ("на следующий день", "напоминание"),
        ("через 5 дней", "только просмотр: цифры видно, менять нельзя, отчёты 1С не принимаются"),
        ("через 10 дней", "доступ закрыт, данные хранятся 3 месяца")]


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
        v3.note("Пример: даты и суммы выдуманы — у настоящей фирмы будут свои.")
    v3.table(["Когда и что", "₽"], sched_rows(ctx, st))
    return [v, v2, v3]


def order_views(ctx):
    st = state(ctx)
    m = monthly(ctx, st=st)
    v = View("Порядок оплаты", f"{ctx.cfg.name} · {money(m)} в месяц · подключение {money(setup_sum(ctx, st))}")
    v.lines(["Вы точно знаете, в какой день платите и сколько: всё это видно в приложении, в «Правила и оплата»."])
    v.lines([status(ctx, st)[0]], title="Сейчас")
    if ctx.demo:
        v.note("Пример: даты и суммы выдуманы — у настоящей фирмы будут свои.")
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
    if ctx.demo or True:                 # Адам 20.11: счёт идёт по настоящим людям — добавлять можно, сумма меняется сама
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
