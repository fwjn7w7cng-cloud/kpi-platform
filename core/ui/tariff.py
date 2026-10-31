"""Конструктор и цены: «🧩 Собрать» в демо, «💳 Тариф» у директора клиента, «💰 Цены» у администратора.

Конструктор (демо): зритель включает пакеты по отделам, задаёт, сколько у него людей, и сразу видит две суммы —
подключение (один раз) и дальше в месяц; «📄 Коммерческое предложение» — PDF с расчётом; «📨 Хочу так» — заявка админу.
Тариф (клиент): что подключено, сколько людей, сколько в месяц; «➕ Подключить…» — заявка администратору.
Цены правит только администратор платформы (ctx.user_id в app.admins) — в базе офиса, поверх стандартных.
"""
from datetime import datetime

from core.access import editions as E
from core.access import pricing as P
from core.ui.kit import Button, Reply, money, rows
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


def quote_of(ctx, packages=None):
    return P.quote(packages if packages is not None else ctx.cfg.packages, counts_of(ctx), P.prices(ctx.office),
                   year=bool(ctx.session.get("year")) if ctx.demo else False)


def quote_view(ctx, q, title, sub):
    """Экран расчёта: две главные суммы, что подключено по строкам, подключение, условия."""
    v = View(title, sub)
    if q["empty"]:
        v.tiles([("Подключение, один раз", "—", None), ("Дальше, в месяц", "—", None)])
        v.note("Ничего не выбрано — включите хотя бы один пакет.")
        return v
    tiles = [("Подключение, один раз", money(q["setup"]), None), ("Дальше, в месяц", money(q["monthly"]), "good")]
    if q["year"]:
        tiles.append(("За год: в месяц", money(q["monthly_year"]), "good"))
    v.tiles(tiles)
    counts = counts_of(ctx)
    pr = P.prices(ctx.office)
    rs = []
    codes = [c for c in DEPT_ORDER if q["packages"][c]]
    for (label, amount, detail), code in zip(q["lines"], codes):
        rs.append(Row([label, f"{counts.get(code, 0)} чел.", money(amount, "")], sub=detail))
    if q["discount"]:
        rs.append(Row(["Скидка: все 4 отдела", f"−{P.fmt(pr['disc_all'])}%", Cell("−" + money(q["discount"], ""), "good")]))
    if q["packages"]["max"]:
        label, amount, detail = q["lines"][-1]
        rs.append(Row([label, "—", money(amount, "")], sub=detail))
    rs.append(Row([Cell("Итого в месяц", bold=True), "", Cell(money(q["monthly"], ""), bold=True)]))
    v.table(["Что подключено", "Людей", "₽ в месяц"], rs)
    srows = [Row([a, money(b, "")]) for a, b in q["setup_lines"]]
    srows.append(Row([Cell("Итого подключение", bold=True), Cell(money(q["setup"], ""), bold=True)]))
    v.table(["Подключение — платится один раз", "₽"], srows)
    lines = [f"Первые {q['trial_days']} дней — бесплатно: смотрите и решайте." if q["trial_days"] else "",
             "Считаются люди в подключённых отделах; директор и администратор — бесплатно.",
             f"Оплата за год — минус {P.fmt(pr['disc_year'])}%: {money(q['monthly_year'])} в месяц, {money(q['year_total'])} за год.",
             "Отдел или уровень можно добавить потом — доплата только за него." if not q["all_depts"] else
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
    p = ctx.cfg.packages
    pr = P.prices(ctx.office)
    if p["sales"] and p["sales"] != E.LEVEL_ORDER[-1]:
        nxt = E.LEVEL_ORDER[E.LEVEL_ORDER.index(p["sales"]) + 1]
        kb.append([Button(f"⬆️ Продажи до «{E.LEVELS[nxt]}» — {P.fmt(pr['sales_' + nxt])} + {P.fmt(pr['sales_' + nxt + '_user'])}/чел.",
                          f"tfr:sales:{nxt}")])
    if not p["sales"]:
        kb.append([Button(f"➕ Продажи · Старт — {P.fmt(pr['sales_start'])} + {P.fmt(pr['sales_start_user'])}/чел.", "tfr:sales:start")])
    for code in ("stock", "logistics", "office"):
        if not p[code]:
            kb.append([Button(f"➕ {E.PACKAGES[code]['icon']} {E.PACKAGES[code]['title']} — {P.fmt(pr[code])} + {P.fmt(pr[code + '_user'])}/чел.",
                              f"tfr:{code}")])
    if not p["max"]:
        kb.append([Button(f"➕ ⭐ Максимум — {P.fmt(pr['max'])} в месяц", "tfr:max")])
    kb.append([Button("📄 Расчёт PDF", "tfp")])
    if is_admin(ctx):
        kb.append([Button("💰 Цены", "pr")])
    return Reply(view=v, buttons=kb)


def build(ctx):
    q = quote_of(ctx)
    counts = counts_of(ctx)
    people = sum(counts.values())
    preset = E.preset_of(ctx.cfg.packages)
    title = f"набор «{ctx.cfg.set_title}»" if preset != E.CUSTOM else "свой набор: " + ctx.cfg.set_title
    v = quote_view(ctx, q, "🧩 Собрать бота", f"{title} · людей в фирме {people}")
    p = ctx.cfg.packages
    kb = [[_pk_button(ctx, "sales"), _pk_button(ctx, "stock")], [_pk_button(ctx, "logistics"), _pk_button(ctx, "office")],
          [_pk_button(ctx, "max")],
          [Button(f"👥 Люди: {' / '.join(str(counts[c]) for c in DEPT_ORDER)} — изменить", "sw:pn")],
          [Button(("• " if ctx.session.get("year") else "") + "📅 Оплата за год", "sw:yr"),
           Button("📄 Коммерческое предложение", "sw:pdf")],
          [Button(("● " if preset == c else "") + E.PRESETS[c]["title"], f"sw:ed:{c}") for c in E.ORDER],
          [Button("🧱 Конструктор зарплаты", "pb"), Button("📋 Анкета для подключения", "cd")],
          [Button("📨 Хочу так — отправить заявку", "sw:req")]]
    from core import onboard as O
    v.note("Как платите людям — «🧱 Конструктор зарплаты»: схема по каждой должности кирпичиками, сразу видно сумму.")
    if not p["sales"] and any(p[c] for c in ("stock", "logistics", "office")):
        v.note("Без пакета «Продажи» торговые в приложение не входят — так можно: только склад или только водители.")
    return Reply(view=v, buttons=kb)


def pay_screen(ctx):
    """«💵 Как платите» — теперь это «🧱 Конструктор зарплаты» (core/paybuilder.py): кирпичики по должностям."""
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


def _pk_button(ctx, code):
    p = ctx.cfg.packages
    d = E.PACKAGES[code]
    if code == "sales":
        label = f"{d['icon']} Продажи: {E.LEVELS[p['sales']] if p['sales'] else 'нет'} ▸"
    else:
        label = f"{'✅' if p[code] else '⬜'} {d['icon']} {d['title']}"
    return Button(label, f"sw:pk:{code}")


def set_packages(ctx, packages):
    ctx.session["packages"] = E.normalize(packages)
    ctx.session.pop("edition", None)
    ctx.save()
    return build(ctx.app.ctx(ctx.user_id))


def toggle(ctx, code, value=None):
    """sw:pk:<пакет>[:уровень] — продажи: выбор уровня, остальные — включить / выключить."""
    if code not in E.PACKAGES:
        return build(ctx)
    p = dict(ctx.cfg.packages)
    if code == "sales":
        if value is None:
            pr = P.prices(ctx.office)
            kb = [[Button(("• " if p["sales"] == lv else "") + f"{E.LEVELS[lv]} — {P.fmt(pr['sales_' + lv])} + {P.fmt(pr['sales_' + lv + '_user'])}/чел.",
                          f"sw:pk:sales:{lv}")] for lv in E.LEVEL_ORDER]
            kb.append([Button(("• " if not p["sales"] else "") + "Без пакета «Продажи»", "sw:pk:sales:off")])
            v = View("📈 Продажи", "какой уровень")
            v.lines([f"Старт — {_mods('start')}", f"Бизнес — ещё {_mods('business', 'start')}",
                     f"Премиум — ещё {_mods('control', 'business')}"])
            return Reply(view=v, buttons=kb)
        p["sales"] = value if value in E.LEVELS else None
    else:
        p[code] = not p[code]
    return set_packages(ctx, p)


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


def toggle_year(ctx):
    ctx.session["year"] = not ctx.session.get("year")
    ctx.save()
    return build(ctx)


def what_view(ctx):
    """Вторая страница КП: что входит в каждый выбранный пакет."""
    p = ctx.cfg.packages
    v = View("Что входит", ctx.cfg.set_title)
    for code in E.PACKAGES:
        if not p[code]:
            continue
        d = E.PACKAGES[code]
        mods = [E.MODULES[m][0] for m in sorted(E.MODULES) if E.package_of(m)[0] == code and m in ctx.cfg.modules]
        head = f"{d['icon']} {d['title']}" + (f" · {E.LEVELS[p['sales']]}" if code == "sales" else "")
        v.lines([d["what"].capitalize() + "."] + [f"• {m}" for m in mods], title=head)
    common = [E.MODULES[m][0] for m in sorted(ctx.cfg.modules) if E.package_of(m)[0] in ("any", "ops")]
    if common:
        v.lines([f"• {m}" for m in common], title="Для всех подключённых отделов")
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
    if ctx.role not in ("owner", "admin") or code not in E.PACKAGES:
        return tariff(ctx)
    p = dict(ctx.cfg.packages)
    p[code] = level if code == "sales" else True
    q = quote_of(ctx, p)
    q0 = quote_of(ctx)
    what = f"{E.PACKAGES[code]['title']}" + (f" · {E.LEVELS[level]}" if level else "")
    text = (f"💳 {ctx.cfg.name}: {ctx.name()} просит подключить «{what}».\n"
            f"Было {money(q0['monthly'])} в месяц → станет {money(q['monthly'])}; доплата за подключение {money(q['setup'] - q0['setup'])}.")
    reqs = ctx.office.get(REQ_KEY) or []
    reqs.append({"uid": ctx.user_id, "who": ctx.name(), "set": what, "monthly": q["monthly"], "setup": q["setup"] - q0["setup"],
                 "ts": datetime.now().strftime("%d.%m %H:%M"), "show": False})
    ctx.office.put(REQ_KEY, reqs[-100:])
    ctx.db.audit(ctx.author(), "tariff_req", what)
    out = [Reply(text=text, to=a) for a in ctx.app.admins if a != ctx.user_id]
    out.append(Reply(text=f"📨 Заявка отправлена: «{what}». Станет {money(q['monthly'])} в месяц, подключение "
                          f"{money(q['setup'] - q0['setup'])} один раз. Администратор включит пакет и подтвердит.",
                     buttons=[[Button("💳 Тариф", "tf")]]))
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
    v.note("Цены действуют для всех расчётов сразу: конструктор, тариф, замки на экранах. "
           "Скидки — в процентах, пробный период — в днях.")
    if not is_admin(ctx):
        v.note("Менять цены может только администратор платформы.")
        return Reply(view=v, buttons=[[Button("🧩 Собрать", "sw:ed")]] if ctx.demo else [])
    kb = rows([Button(f"✏️ {title.replace(' — за человека', ': чел.').replace(' — пакет', '').replace(' — ', ': ')[:28]}",
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
