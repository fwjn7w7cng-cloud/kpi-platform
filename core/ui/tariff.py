"""Конструктор и цены: «🧩 Собрать» в демо, «💳 Тариф» у директора клиента, «💰 Цены» у администратора.

Конструктор (демо): зритель включает пакеты по отделам, задаёт, сколько у него людей, и сразу видит две суммы —
подключение (один раз) и дальше в месяц; «📄 Коммерческое предложение» — PDF с расчётом; «📨 Хочу так» — заявка админу.
Тариф (клиент): что подключено, сколько людей, сколько в месяц; «➕ Подключить…» — заявка администратору.
Цены правит только администратор платформы (ctx.user_id в app.admins) — в базе офиса, поверх стандартных.
"""
from datetime import datetime

from core.access import editions as E
from core.access import pricing as P
from core.ui.kit import Button, Reply, money, ok_row, rows
from core.ui.registry import menu, screen, text_input
from core.ui.views import Cell, Row, View, to_pdf

DEPT_ORDER = ("sales", "stock", "logistics", "office")
REQ_KEY = "tariff_req"

menu("tf", "💳 Тариф", {"owner", "admin"}, order=86, when=lambda c: not c.demo)


def is_admin(ctx):
    return ctx.user_id in ctx.app.admins


# ---------- что считаем ----------

def counts_of(ctx):
    """Сколько людей в отделах каждого пакета: у клиента — по базе; в демо зритель может вписать свои числа."""
    own = ctx.session.get("counts") if ctx.demo else None
    if isinstance(own, dict):
        return {k: int(own.get(k, 0)) for k in DEPT_ORDER}
    hc = P.headcount(ctx.cfg, ctx.db)
    return {k: hc.get(k, 0) for k in DEPT_ORDER}


def funcs_of(cfg):
    """Функции, которые сейчас подключены: выбранные поштучно или по набору пакетов."""
    return set(cfg.functions) if cfg.functions is not None else E.functions_of(cfg.packages)


def quote_of(ctx, sel=None, year=None, counts=None):
    if year is None:
        year = bool(ctx.session.get("year")) if ctx.demo else False
    return P.quote(sel if sel is not None else funcs_of(ctx.cfg), counts or counts_of(ctx), P.prices(ctx.office), year=year)


def quote_view(ctx, q, title, sub):
    """Экран расчёта: две главные суммы, что подключено по строкам, подключение, условия."""
    v = View(title, sub)
    if q["empty"]:
        v.tiles([("Подключение, один раз", "—", None), ("Дальше, в месяц", "—", None)])
        v.note("Ничего не выбрано — отметьте хотя бы одну функцию.")
        return v
    tiles = [("Подключение, один раз", money(q["setup"]), None), ("Дальше, в месяц", money(q["monthly"]), "good")]
    if q["year"]:
        tiles.append(("За год: в месяц", money(q["monthly_year"]), "good"))
    v.tiles(tiles)
    pr = P.prices(ctx.office)
    rs = [Row([label, money(amount, "")], sub=detail) for label, amount, detail in q["lines"] if not label.startswith("⭐")]
    if q["discount"]:
        rs.append(Row([f"Скидка: все 4 отдела −{P.fmt(pr['disc_all'])}%", Cell("−" + money(q["discount"], ""), "good")]))
    rs += [Row([label, money(amount, "")], sub=detail) for label, amount, detail in q["lines"] if label.startswith("⭐")]
    rs.append(Row([Cell("Итого в месяц", bold=True), Cell(money(q["monthly"], ""), bold=True)]))
    v.table(["Что подключено", "₽ в месяц"], rs)
    srows = [Row([a, money(b, "")]) for a, b in q["setup_lines"]]
    srows.append(Row([Cell("Итого подключение", bold=True), Cell(money(q["setup"], ""), bold=True)]))
    v.table(["Подключение — платится один раз", "₽"], srows)
    lines = [f"Первые {q['trial_days']} дней — бесплатно: смотрите и решайте." if q["trial_days"] else "",
             "Платите только за функции; настройки внутри функции — бесплатно. Люди считаются в подключённых отделах, "
             "директор и администратор — бесплатно.",
             f"Оплата за год — минус {P.fmt(pr['disc_year'])}%: {money(q['monthly_year'])} в месяц, {money(q['year_total'])} за год.",
             "Функцию можно добавить потом — доплата только за неё." if not q["all_depts"] else
             f"Все четыре отдела — скидка {P.fmt(pr['disc_all'])}% уже учтена."]
    v.lines([x for x in lines if x], title="Условия")
    return v


# ---------- конструктор (демо) ----------

@screen("tf")
def tariff(ctx):
    if ctx.demo:
        return build(ctx)
    if ctx.role not in ("owner", "admin"):
        return Reply(text="Тариф видит директор.")
    q = quote_of(ctx)
    v = quote_view(ctx, q, "💳 Тариф", f"{ctx.cfg.name} · набор «{ctx.cfg.set_title}» · {q['people']} чел. в приложении")
    kb = []
    pr = P.prices(ctx.office)
    have = funcs_of(ctx.cfg)
    for f in [f for f in E.FUNCS if f not in have][:12]:
        icon = E.FUNC_GROUPS[E.FUNCS[f][0]][0]
        kb.append([Button(f"➕ {icon} {E.FUNC_SHORT[f]} · {P.fmt(pr['f_' + f])}", f"tfr:{f}")])
    kb.append([Button("📄 Расчёт PDF", "tfp")])
    if is_admin(ctx):
        kb.append([Button("💰 Цены", "pr")])
    return Reply(view=v, buttons=kb)


def build(ctx):
    q = quote_of(ctx)
    counts = counts_of(ctx)
    people = sum(counts.values())
    title = f"набор «{E.funcs_title(base_of(ctx), funcs_of(ctx.cfg))}»"
    v = quote_view(ctx, q, "🧩 Собрать бота", f"{title} · людей в фирме {people}")
    p = ctx.cfg.packages
    kb = [[Button("☑️ Выбрать набор", "swe")],
          [Button(f"👥 Люди: {' / '.join(str(counts[c]) for c in DEPT_ORDER)} — изменить", "sw:pn")],
          [Button("📄 Коммерческое предложение", "sw:pdf")],
          [Button("🧱 Конструктор зарплаты", "pb"), Button("📋 Анкета для подключения", "cd")],
          [Button("📨 Хочу так — отправить заявку", "sw:req")]]
    v.note("Набор под размер фирмы и что в него включено, оплата за год — «☑️ Выбрать набор». "
           "Как платите людям — «🧱 Конструктор зарплаты».")
    if not p["sales"] and any(p[c] for c in ("stock", "logistics", "office")):
        v.note("Без пакета «Продажи» торговые в приложение не входят — так можно: только склад или только водители.")
    return Reply(view=v, buttons=kb)


# ---------- окно выбора пакетов (Адам 31.10: «сначала выбрать, потом ОК или Отмена») ----------
# swe — открыть; swe:b:<набор> — набор (один); swe:t:<функция> — функция галочкой; swe:y — за год;
# swe:ok — применить; swe:no — выйти без изменений. Черновик — в сессии (pk_draft), суммы считаются по нему.



def base_of(ctx):
    """Набор, с которого зритель начал (размер фирмы); по умолчанию — тот, которому равны пакеты, иначе «Компания»."""
    b = ctx.session.get("base")
    if b in E.PRESETS:
        return b
    have = funcs_of(ctx.cfg)
    return next((c for c in E.ORDER if E.preset_functions(c) == have), "company")


@screen("swe")
def packages_window(ctx, act=None, value=None):
    """Окно (Адам 31.10): 1) набор — один, задаёт людей, роли и функции по умолчанию; 2) что включено — галочки, можно
    добавить или убрать что угодно, набор при этом остаётся («Старт + Склад и закупки»). «ОК» / «Отмена»."""
    if not ctx.demo:
        return Reply(text="Пакеты подключённой фирмы меняются через «💳 Тариф».")
    d = ctx.session.get("pk_draft")
    if act is None or not isinstance(d, dict) or "funcs" not in d:
        d = {"base": base_of(ctx), "funcs": sorted(funcs_of(ctx.cfg)), "year": bool(ctx.session.get("year"))}
    f = set(d["funcs"])
    if act == "no":
        ctx.session.pop("pk_draft", None)
        ctx.save()
        return build(ctx)
    if act == "ok":
        ctx.session.pop("pk_draft", None)
        ctx.session["year"] = d["year"]
        ctx.session["base"] = d["base"]
        if d.get("counts"):
            ctx.session["counts"] = d["counts"]
        ctx.save()
        return set_functions(ctx, f)
    if act == "b" and value in E.PRESETS:
        d["base"] = value
        f = E.preset_functions(value)
        d["counts"] = dict(E.PRESETS[value]["people"])
    elif act == "t" and value in E.FUNCS:
        f = E.toggle_function(f, value)
    elif act == "y":
        d["year"] = not d["year"]
    d["funcs"] = sorted(f)
    ctx.session["pk_draft"] = d
    ctx.save()
    counts = d.get("counts") or counts_of(ctx)
    q = quote_of(ctx, f, d["year"], counts)
    pr = P.prices(ctx.office)
    v = View("☑️ Выбрать набор", f"«{E.funcs_title(d['base'], f)}» · людей {sum(counts.values())}")
    if q["empty"]:
        v.tiles([("Подключение, один раз", "—", None), ("Дальше, в месяц", "—", None)])
    else:
        v.tiles([("Подключение, один раз", money(q["setup"]), None), ("Дальше, в месяц", money(q["monthly"]), "good")]
                + ([("За год: в месяц", money(q["monthly_year"]), "good")] if d["year"] else []))
    v.lines([f"«{E.PRESETS[c]['title']}» — {E.PRESETS[c]['size']}: {E.PRESETS[c]['what']}" for c in E.ORDER],
            title="Наборы — по умолчанию под размер фирмы")
    v.note("Сверху — набор (один): с ним по умолчанию ставятся люди, роли и функции. Ниже — функции по отделам: платите только "
           "за отмеченные, любую можно снять или добавить. Настройки внутри функции — бесплатно.")
    kb = []
    for c in E.ORDER:
        x = E.PRESETS[c]
        n = sum(x["people"].values())
        kb.append([Button(f"{'☑' if d['base'] == c else '☐'} Набор «{x['title']}» · {n} чел.", f"swe:b:{c}")])
    for fc, (grp, _) in E.FUNCS.items():
        icon = E.FUNC_GROUPS[grp][0]
        kb.append([Button(f"{'☑' if fc in f else '☐'} {icon} {E.FUNC_SHORT[fc]} · {P.fmt(pr['f_' + fc])}", f"swe:t:{fc}")])
    kb.append([Button(f"{'☑' if d['year'] else '☐'} 📅 Оплата за год — минус {P.fmt(pr['disc_year'])}%", "swe:y")])
    kb.append(ok_row("swe"))
    return Reply(view=v, buttons=kb)


def pay_screen(ctx):
    """«💵 Как платите» — теперь это «🧱 Конструктор зарплаты» (core/paybuilder.py): блоки по должностям."""
    from core import paybuilder as PB
    return PB.page(ctx)


def pay_toggle(ctx, code):
    from core import onboard as O
    pay = O.pay_of(ctx)
    if code == "other":
        return ctx.ask("pay_other", "Как у вас платят по-своему? Напишите одной-двумя фразами — например «премия 10 000 за каждого "
                                    "нового клиента» или «процент зависит от скидки». «-» — убрать.",
                       buttons=[[Button("✖️ Отмена", "sw:pay")]])
    if code == "plan":
        pay["plan"] = O.PLAN_ORDER[(O.PLAN_ORDER.index(pay["plan"]) + 1) % len(O.PLAN_ORDER)]
    elif code in O.PAY:
        pay[code] = not pay[code]
    ctx.session["pay"] = pay
    ctx.save()
    return pay_screen(ctx)


@text_input("pay_other")
def on_pay_other(ctx, text, data):
    from core import onboard as O
    pay = O.pay_of(ctx)
    t = " ".join((text or "").split())[:200]
    pay["other"] = "" if t in ("-", "—") else t
    ctx.session["pay"] = pay
    ctx.save()
    return pay_screen(ctx)


def set_functions(ctx, functions):
    ctx.session["funcs"] = sorted(functions)
    ctx.session.pop("packages", None)
    ctx.session.pop("edition", None)
    ctx.save()
    return build(ctx.app.ctx(ctx.user_id))


def set_packages(ctx, packages, base=None):
    ctx.session.pop("funcs", None)
    ctx.session["packages"] = E.normalize(packages)
    if base in E.PRESETS:
        ctx.session["base"] = base
    ctx.session.pop("edition", None)
    ctx.save()
    return build(ctx.app.ctx(ctx.user_id))


def _mods(level, below=None):
    have = E.modules_of({"sales": level}) - (E.modules_of({"sales": below}) if below else set())
    return ", ".join(E.MODULES[m][0].split(":")[0] for m in sorted(have) if E.package_of(m)[0] == "sales")


def ask_counts(ctx):
    c = counts_of(ctx)
    return ctx.ask("counts", "Сколько людей в приложении по отделам? Четыре числа через пробел:\n"
                             "продажи · склад и закупки · логистика · кадры и офис\n\n"
                             f"Сейчас: {c['sales']} {c['stock']} {c['logistics']} {c['office']}\n\n"
                             "Напишите «как было», чтобы вернуть числа показанной фирмы.",
                   buttons=[[Button("Как в показанной фирме", "sw:pn:reset")], [Button("✖️ Отмена", "sw:ed")]])


@text_input("counts")
def on_counts(ctx, text, data):
    t = (text or "").strip().lower()
    if t in ("как было", "-", "0"):
        ctx.session.pop("counts", None)
        ctx.save()
        return build(ctx)
    parts = t.replace(",", " ").split()
    if len(parts) != 4 or not all(x.isdigit() and int(x) <= 5000 for x in parts):
        return ask_counts(ctx)
    ctx.session["counts"] = dict(zip(DEPT_ORDER, (int(x) for x in parts)))
    ctx.save()
    return build(ctx)


@text_input("partner_req")
def on_partner_req(ctx, text, data):
    client = " ".join((text or "").split())[:120]
    if len(client) < 3:
        return request(ctx)
    return request(ctx, client)


def reset_counts(ctx):
    ctx.session.pop("counts", None)
    ctx.save()
    return build(ctx)


def what_view(ctx):
    """Вторая страница КП: какие функции подключены — по отделам, с ценой каждой."""
    pr = P.prices(ctx.office)
    have = funcs_of(ctx.cfg)
    v = View("Что входит", ctx.cfg.set_title)
    for grp, (icon, gtitle) in E.FUNC_GROUPS.items():
        fs = [f for f in E.FUNCS if f in have and E.FUNCS[f][0] == grp]
        if fs:
            v.lines([f"• {E.MODULES[f][0]} — {P.fmt(pr['f_' + f])} ₽/мес" for f in fs], title=f"{icon} {gtitle}")
    v.lines(["• Напоминания, журнал изменений, кабинеты сотрудников, «🐞 Не работает», Рамадан"], title="Бесплатно у всех")
    return v


def pdf(ctx):
    q = quote_of(ctx)
    if q["empty"]:
        return build(ctx)
    who = "показанная фирма" if ctx.demo else ctx.cfg.name
    v = quote_view(ctx, q, "Коммерческое предложение", f"KPI-бот · {ctx.cfg.set_title} · {ctx.today:%d.%m.%Y}")
    v.note(f"Расчёт на {sum(counts_of(ctx).values())} человек ({who}). Цены действуют на дату расчёта.")
    data = to_pdf([v, what_view(ctx)])
    return Reply(text="📄 Коммерческое предложение — расчёт и состав пакетов.",
                 files=[(f"КП KPI-бот {ctx.today:%d.%m.%Y}.pdf", data)], buttons=[[Button("‹ Собрать", "sw:ed")]])


def request(ctx, client=None):
    """«Хочу так» в демо: заявка администратору платформы с набором, людьми и суммами.
    Партнёр сначала пишет, для какого клиента; заявка клиента партнёра приходит и партнёру."""
    from core.demo import is_partner
    q = quote_of(ctx)
    if q["empty"]:
        return Reply(text="Сначала выберите хотя бы один пакет.", buttons=[[Button("🧩 Собрать", "sw:ed")]])
    partner = is_partner(ctx)
    if partner and not client:
        return ctx.ask("partner_req", "Для какого клиента заявка? Имя, компания и телефон — например: "
                                      "«Ахмед, Стройбаза, 8 928 000 00 00».", buttons=[[Button("✖️ Отмена", "sw:ed")]])
    counts = counts_of(ctx)
    guests = ctx.office.get("demo_guests") or {}
    g = guests.get(str(ctx.user_id)) or {}
    me = g.get("note") or g.get("name") or ctx.session.get("tgname") or f"id {ctx.user_id}"
    by = str(ctx.user_id) if partner else str(g.get("by") or "")
    pname = (guests.get(by) or {}).get("note") or "" if by else ""
    who = f"{client} (показ партнёра {me})" if partner else (f"{me} (клиент партнёра {pname})" if pname else me)
    text = (f"🧩 Заявка из показа: {who}\n{ctx.cfg.set_title}\n"
            f"Людей: продажи {counts['sales']}, склад {counts['stock']}, логистика {counts['logistics']}, офис {counts['office']}\n"
            f"Подключение {money(q['setup'])} · в месяц {money(q['monthly'])}")
    from core import onboard as O
    if partner:
        c = O.new_manual(ctx, client, ctx.user_id)
    else:
        c = O.ensure(ctx, ctx.user_id)
    O.snapshot(ctx, c)
    O.save(ctx.office, c["uid"], c)
    cid = c["uid"]
    reqs = ctx.office.get(REQ_KEY) or []
    reqs.append({"uid": ctx.user_id, "card": cid, "who": who, "set": ctx.cfg.set_title, "counts": counts, "setup": q["setup"],
                 "monthly": q["monthly"], "ts": datetime.now().strftime("%d.%m %H:%M"), "show": True, "partner": by})
    ctx.office.put(REQ_KEY, reqs[-100:])
    out = [Reply(text=text, to=a, buttons=[[Button("📋 Анкета клиента", f"cd:{cid}"), Button("📨 Заявки", "rqs")]])
           for a in ctx.app.admins if a != ctx.user_id]
    if by and not partner and by.isdigit():
        out.append(Reply(text="🎉 Ваш клиент нажал «Хочу так»:\n" + text, to=int(by),
                         buttons=[[Button("📋 Анкета клиента", f"cd:{cid}"), Button("🤝 Мои клиенты", "ptn")]]))
    tail = (f"Клиент «{client}» закреплён за вами — заполните его анкету." if partner
            else "Теперь 2 минуты на анкету — и мы подготовим подключение.")
    out.append(Reply(text=f"📨 Заявка отправлена: {ctx.cfg.set_title}, подключение {money(q['setup'])}, дальше {money(q['monthly'])} "
                          f"в месяц. {tail}", buttons=[[Button("📋 Анкета для подключения", f"cd:{cid}")], [Button("🧩 Собрать", "sw:ed")]]))
    return out


# ---------- тариф клиента: запросы ----------

@screen("tfr")
def tariff_request(ctx, code, level=None):
    if ctx.role not in ("owner", "admin") or code not in E.FUNCS:
        return tariff(ctx)
    have = funcs_of(ctx.cfg)
    q = quote_of(ctx, have | {code})
    q0 = quote_of(ctx, have)
    what = E.MODULES[code][0]
    text = (f"💳 {ctx.cfg.name}: {ctx.name()} просит подключить «{what}».\n"
            f"Было {money(q0['monthly'])} в месяц → станет {money(q['monthly'])}.")
    reqs = ctx.office.get(REQ_KEY) or []
    reqs.append({"uid": ctx.user_id, "who": ctx.name(), "set": what, "monthly": q["monthly"], "setup": 0,
                 "ts": datetime.now().strftime("%d.%m %H:%M"), "show": False})
    ctx.office.put(REQ_KEY, reqs[-100:])
    ctx.db.audit(ctx.author(), "tariff_req", what)
    out = [Reply(text=text, to=a) for a in ctx.app.admins if a != ctx.user_id]
    out.append(Reply(text=f"📨 Заявка отправлена: «{what}». Станет {money(q['monthly'])} в месяц. Администратор включит функцию "
                          "и подтвердит.", buttons=[[Button("💳 Тариф", "tf")]]))
    return out


@screen("tfp")
def tariff_pdf(ctx):
    if ctx.role not in ("owner", "admin"):
        return tariff(ctx)
    return pdf(ctx)


# ---------- цены (администратор) ----------

@screen("pr")
def prices_screen(ctx):
    if ctx.role != "admin" and not is_admin(ctx):
        return Reply(text="Цены видит администратор платформы.")
    pr = P.prices(ctx.office)
    ch = P.changed(ctx.office)
    v = View("💰 Цены", "стандартные" if not ch else f"изменено: {len(ch)}")
    v.table(["Что", "Цена"], [Row([title, Cell(f"{P.fmt(pr[code])} {unit}", "warn" if code in ch else None)],
                                 sub=f"стандарт {P.fmt(P.DEFAULT[code])}" if code in ch else "")
                              for code, title, _, unit in P.PRICES])
    v.note("Платим только за функции; настройки внутри функции — бесплатно. Цены действуют для всех расчётов сразу: "
           "конструктор, тариф, замки на экранах. Скидки — в процентах, пробный период — в днях.")
    if not is_admin(ctx):
        v.note("Менять цены может только администратор платформы.")
        return Reply(view=v, buttons=[[Button("🧩 Собрать", "sw:ed")]] if ctx.demo else [])
    kb = rows([Button(f"✏️ {title.split(' · ')[-1].split(' (')[0].replace(' — ', ': ')[:28]}",
                      f"pre:{code}") for code, title, _, _ in P.PRICES], 2)
    kb.append([Button("↩️ Стандартные цены", "prx")] if ch else [])
    kb.append([Button("‹ Пульт", "adm")] if ctx.demo and ctx.app.demo_gate else
              [Button("🧩 Собрать", "sw:ed")] if ctx.demo else [Button("💳 Тариф", "tf")])
    return Reply(view=v, buttons=[r for r in kb if r])


@screen("pre")
def price_edit(ctx, code):
    if not is_admin(ctx) or code not in P.DEFAULT:
        return prices_screen(ctx)
    title, unit = P.TITLES[code]
    cur = P.prices(ctx.office)[code]
    return ctx.ask("price", f"{title}\nСейчас: {P.fmt(cur)} {unit} (стандарт {P.fmt(P.DEFAULT[code])}).\n\nНапишите новое число.",
                   {"code": code}, buttons=[[Button("✖️ Отмена", "pr")]])


@text_input("price")
def on_price(ctx, text, data):
    if not is_admin(ctx):
        return prices_screen(ctx)
    t = (text or "").replace(" ", "").replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
    try:
        x = float(t)
    except ValueError:
        return ctx.ask("price", "Нужно число, например 5 000 или 15.", data, buttons=[[Button("✖️ Отмена", "pr")]])
    code = data["code"]
    if x < 0 or (code.startswith("disc_") and x > 90) or (code == "trial_days" and x > 365) or x > 10_000_000 \
            or (code == "setup_mult" and not 0.5 <= x <= 10):
        return ctx.ask("price", "Так не бывает: скидка до 90%, пробный период до 365 дней, подключение — от 0,5 до 10 месяцев, "
                                "цена не меньше нуля.", data,
                       buttons=[[Button("✖️ Отмена", "pr")]])
    P.set_price(ctx.office, code, int(x) if x == int(x) else x, who=ctx.author())
    return prices_screen(ctx)


@screen("prx")
def price_reset(ctx):
    if not is_admin(ctx):
        return prices_screen(ctx)
    P.reset(ctx.office, who=ctx.author())
    return prices_screen(ctx)
