"""Анкета клиента прямо в боте (Адам 30.10): всё, что нужно нам для подключения, собирается в одной карточке.

Откуда берётся:
  • из конструктора «🧩 Собрать» — пакеты, люди по отделам, как платят торговым, суммы (снимок обновляется сам,
    когда клиент открывает анкету или жмёт «Хочу так»);
  • остальное дописывают кнопками и текстом: клиент сам, партнёр для своего клиента, админ для любого.
Чего бот не умеет «из коробки» — помечается ⚠️ «нестандарт»: другая 1С, филиалы, зарплата «по-своему» и т. п. —
это подсказка нам: тут нужна настройка переводчика, правило клиента или доработка ядра.
Где лежит: база офиса, ключ client_cards = {id зрителя: {f: ответы, set: снимок конструктора, by: партнёр, ...}}.
Кто видит: клиент — свою, партнёр — своих клиентов, админ — все («🛠 Пульт → 📋 Анкеты»).
Админу: «📥 Анкета Excel» — заполненная таблица-анкета для tools/anketa.py apply (цифры схем — стандартные, уточнить).
"""
import io
from datetime import datetime

from core.access import editions as E
from core.access import pricing as P
from core.config import INDUSTRIES
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import screen, text_input
from core.ui.views import Card, Cell, Row, View, to_pdf

KEY = "client_cards"

# Группы вопросов: (код, подпись, [(поле, подпись, вид, обязательное, варианты или подсказка)])
#   вид: text — пишут текстом; choice — кнопки; варианты — список подписей
GROUPS = [
    ("firm", "🏢 Компания", [
        ("company", "Название компании", "text", True, "как называете сами, например «Стройбаза»"),
        ("city", "Город", "text", True, ""),
        ("industry", "Отрасль", "choice", True, list(INDUSTRIES.values())),
        ("site", "Сайт или соцсети", "text", False, "если есть"),
    ]),
    ("who", "📞 Контакты", [
        ("decider", "Кто решает: имя и должность", "text", True, "например «Ахмед, директор»"),
        ("phone", "Телефон для связи", "text", True, ""),
        ("admin", "Кто будет вести бот в компании", "text", False, "имя и должность — обычно бухгалтер или помощник директора"),
    ]),
    ("onec", "🖥 1С и отчёты", [
        ("onec", "Какая программа учёта", "choice", True, ["1С УТ 10.3", "1С УТ 11", "1С КА или ERP", "Другая 1С", "Не 1С"]),
        ("prog", "Программист 1С", "choice", True, ["Свой", "Приходящий", "Нет"]),
        ("exports", "Кто будет выгружать отчёты", "choice", False, ["1С сама по расписанию", "Человек вручную", "Пока не знаем"]),
        ("reports", "Какие отчёты уже выгружают", "text", False, "продажи, долги, деньги — что есть"),
        ("pay_now", "Как считают зарплату сейчас", "choice", False, ["В Excel вручную", "В 1С", "Другая программа", "На бумаге"]),
    ]),
    ("special", "⚙️ Особенности", [
        ("lines", "Несколько юрлиц или брендов", "choice", False, ["Нет", "Да"]),
        ("branches", "Филиалы в других городах", "choice", False, ["Нет", "Да"]),
        ("weekend", "Выходные", "choice", False, ["Воскресенье", "Суббота и воскресенье", "Пятница", "Другое"]),
        ("special", "Что считается по-своему, не как у всех", "text", False, "например «премия за новых клиентов раз в квартал»"),
    ]),
    ("start", "🚀 Старт", [
        ("start", "Когда готовы начать", "choice", True, ["Сразу", "В течение месяца", "Через 1–3 месяца", "Пока смотрим"]),
        ("comment", "Комментарий", "text", False, "что ещё важно знать"),
    ]),
]
FIELDS = {f: (g, label, kind, req, opts) for g, _, fs in GROUPS for f, label, kind, req, opts in fs}
GROUP_TITLE = {g: title for g, title, _ in GROUPS}

# Как платят торговым — вариации конструктора («💵 Как платите»). Код → (подпись, подсказка простыми словами)
PAY = {
    "salary": ("Оклад", "фиксированная часть за отработанные дни"),
    "percent": ("% от продаж", "процент с каждой продажи"),
    "plan": ("Бонус за план", "none — нет; fix — фикс при 100 %; steps — ступени 80 / 100 / 120 %; linear — пропорционально"),
    "akb": ("Бонус за клиентов (АКБ)", "за число покупавших клиентов"),
    "debt": ("Удержание за просрочку", "минус, если долги клиентов выше нормы"),
    "drivers": ("Водителям сдельно", "за километры и точки доставки"),
    "wh": ("Складу бонус «без ошибок»", "минус за каждую ошибку"),
}
PLAN_T = {"none": "нет", "fix": "фикс при 100 %", "steps": "ступени 80 / 100 / 120 %", "linear": "пропорционально плану"}
PLAN_ORDER = ["steps", "fix", "linear", "none"]
PAY_DEFAULT = {"salary": True, "percent": True, "plan": "steps", "akb": True, "debt": True, "drivers": True, "wh": True, "other": ""}


# ---------- хранение ----------

def cards(office):
    return office.get(KEY) or {}


def card(office, uid):
    return cards(office).get(str(uid)) or {}


def save(office, uid, c):
    allc = cards(office)
    c["updated"] = datetime.now().strftime("%d.%m %H:%M")
    allc[str(uid)] = c
    office.put(KEY, allc)


def new_manual(ctx, who, by):
    """Карточка клиента, которого партнёр ведёт сам (клиент ещё не входил в демо): id = «<партнёр>c<номер>»."""
    allc = cards(ctx.office)
    n = sum(1 for k in allc if k.startswith(f"{by}c")) + 1
    cid = f"{by}c{n}"
    c = {"uid": cid, "by": str(by), "f": {"decider": who}, "set": {}, "notes": [], "created": datetime.now().strftime("%d.%m %H:%M")}
    save(ctx.office, cid, c)
    return c


def by_partner(office, pid):
    return [c for c in cards(office).values() if c.get("by") == str(pid)]


def ensure(ctx, uid):
    """Карточка зрителя: завести, если нет; подхватить партнёра и его заметку из приглашения."""
    c = card(ctx.office, uid)
    if not c:
        g = (ctx.office.get("demo_guests") or {}).get(str(uid)) or {}
        c = {"uid": str(uid), "by": str(g.get("by") or ""), "f": {}, "set": {}, "notes": [],
             "created": datetime.now().strftime("%d.%m %H:%M")}
        if g.get("note") and g.get("by"):                    # партнёр приглашал: «Имя, компания, телефон»
            c["f"]["decider"] = g["note"]
        save(ctx.office, uid, c)
    return c


def pay_of(ctx):
    p = dict(PAY_DEFAULT)
    p.update(ctx.session.get("pay") or {})
    return p


def snapshot(ctx, c):
    """Снимок конструктора в карточку — только для своей карточки (данные лежат в сессии зрителя)."""
    from core.ui.tariff import counts_of, quote_of
    q = quote_of(ctx)
    counts = counts_of(ctx)
    c["set"] = {"packages": ctx.cfg.packages, "title": ctx.cfg.set_title, "counts": counts, "people": sum(counts.values()),
                "monthly": q["monthly"], "setup": q["setup"], "pay": pay_of(ctx), "industry": ctx.industry}
    if not c["f"].get("industry"):
        c["f"]["industry"] = INDUSTRIES.get(ctx.industry, "")
    return c


# ---------- кто видит ----------

def may(ctx, uid):
    if ctx.user_id in ctx.app.admins:
        return True
    if str(uid) == str(ctx.user_id):
        return True
    from core.demo import is_partner
    return is_partner(ctx) and card(ctx.office, uid).get("by") == str(ctx.user_id)


def own(ctx, uid):
    return str(uid) == str(ctx.user_id)


# ---------- что готово, что нестандарт ----------

def progress(c):
    req = [f for f, (_, _, _, r, _) in FIELDS.items() if r]
    done = [f for f in req if c["f"].get(f)]
    return len(done), len(req)


def flags(c):
    """⚠️ Нестандарт — что потребует настройки или доработки, простыми словами."""
    f, s = c.get("f", {}), c.get("set", {})
    out = []
    if f.get("onec") == "Не 1С":
        out.append("Учёт не в 1С — нужен свой переводчик отчётов из их программы (или выгрузка в Excel).")
    elif f.get("onec") == "Другая 1С":
        out.append("Нестандартная 1С — проверить отчёты, возможно свой переводчик.")
    if f.get("prog") == "Нет":
        out.append("Нет программиста 1С — выгрузку отчётов настраиваем мы, заложить время.")
    if f.get("branches") == "Да":
        out.append("Филиалы в других городах — это пакет «Максимум» (несколько филиалов в одном приложении).")
    if f.get("lines") == "Да":
        out.append("Несколько юрлиц или брендов — заводим линии в анкете, планы и ставки по каждой.")
    if f.get("weekend") == "Другое":
        out.append("Нестандартные выходные — уточнить график, задать в анкете.")
    if (f.get("special") or "").strip():
        out.append(f"По-своему: «{f['special'].strip()}» — проверить, есть ли такое в ядре; иначе правило клиента или доработка.")
    pay = s.get("pay") or {}
    if (pay.get("other") or "").strip():
        out.append(f"Зарплата по-своему: «{pay['other'].strip()}» — нет среди готовых кубиков, нужна настройка или доработка.")
    if not pay.get("salary") and not pay.get("percent") and pay.get("plan", "steps") == "none":
        out.append("Торговым не платят ни оклад, ни процент, ни бонус за план — уточнить, из чего состоит зарплата.")
    return out


def pay_text(pay):
    parts = []
    if pay.get("salary"):
        parts.append("оклад")
    if pay.get("percent"):
        parts.append("% от продаж")
    if pay.get("plan", "steps") != "none":
        parts.append(f"бонус за план ({PLAN_T[pay.get('plan', 'steps')]})")
    if pay.get("akb"):
        parts.append("бонус за АКБ")
    if pay.get("debt"):
        parts.append("удержание за просрочку")
    if pay.get("other"):
        parts.append(f"по-своему: {pay['other']}")
    return " + ".join(parts) if parts else "не выбрано"


# ---------- экраны ----------

def _title(c):
    return c["f"].get("company") or c["f"].get("decider") or f"клиент id {c.get('uid', '')}"


@screen("cd")
def show(ctx, uid=None):
    uid = str(uid or ctx.user_id)
    if not may(ctx, uid):
        return Reply(text="Анкету видит сам клиент, его партнёр и администратор.")
    if ctx.user_id in ctx.app.admins and own(ctx, uid) and not card(ctx.office, uid):
        return cards_list(ctx)
    c = ensure(ctx, uid)
    if own(ctx, uid) and ctx.demo:
        snapshot(ctx, c)
        save(ctx.office, uid, c)
    done, total = progress(c)
    s = c.get("set") or {}
    v = View("📋 Анкета для подключения", f"{_title(c)} · заполнено {done} из {total} обязательных")
    if s:
        v.tiles([("Набор", s.get("title", "—"), None), ("Людей", str(s.get("people", 0)), None),
                 ("В месяц", money(s.get("monthly", 0)), "good"), ("Подключение", money(s.get("setup", 0)), None)])
        cnt = s.get("counts") or {}
        v.lines([f"Люди: продажи {cnt.get('sales', 0)} · склад и закупки {cnt.get('stock', 0)} · логистика {cnt.get('logistics', 0)} · "
                 f"офис {cnt.get('office', 0)}", f"Торговым платят: {pay_text(s.get('pay') or {})}"], title="Из конструктора")
    for g, title, fs in GROUPS:
        lines = []
        for f, label, kind, req, _ in fs:
            val = c["f"].get(f)
            lines.append(f"{'●' if val else ('○' if req else '·')} {label}: {val}" if val else
                         f"{'○' if req else '·'} {label}" + (" — обязательно" if req else ""))
        v.lines(lines, title=title)
    fl = flags(c)
    if fl:
        v.lines([f"⚠️ {x}" for x in fl], title="Нестандарт — потребует настройки")
    if c.get("notes"):
        v.lines([f"{n['ts']} · {n['who']}: {n['text']}" for n in c["notes"][-5:]], title="Заметки")
    if done < total:
        v.note("Заполните обязательные пункты (○) — и мы подготовим подключение. Каждый пункт — одна кнопка или одна строка.")
    else:
        v.note("Обязательное заполнено ✓. Остальное — по желанию, всё поможет подключить быстрее.")
    kb = rows([Button(f"✏️ {title.split(' ', 1)[1]}", f"cdg:{uid}:{g}") for g, title, _ in GROUPS], 2)
    if own(ctx, uid) and ctx.demo:
        kb.append([Button("🧩 Пакеты и люди", "sw:ed"), Button("💵 Как платите торговым", "sw:pay")])
    else:
        kb.append([Button("📝 Заметка", f"cdn:{uid}")])
    tail = [Button("📄 PDF", f"cdp:{uid}")]
    if ctx.user_id in ctx.app.admins:
        tail.append(Button("📥 Анкета Excel", f"cdx:{uid}"))
    kb.append(tail)
    from core.demo import is_partner
    if ctx.user_id in ctx.app.admins:
        kb.append([Button("📋 Все анкеты", "cds")])
    elif is_partner(ctx) and not own(ctx, uid):
        kb.append([Button("🤝 Мои клиенты", "ptn")])
    return Reply(view=v, buttons=kb)


@screen("cdg")
def group(ctx, uid, g):
    if not may(ctx, uid) or g not in GROUP_TITLE:
        return show(ctx, uid)
    c = ensure(ctx, uid)
    fs = next(fs for code, _, fs in GROUPS if code == g)
    v = View(GROUP_TITLE[g], _title(c))
    v.lines([f"{'●' if c['f'].get(f) else '○'} {label}: {c['f'].get(f) or ('— обязательно' if req else '—')}"
             + (f"  ({hint})" if isinstance(hint, str) and hint and not c["f"].get(f) else "") for f, label, kind, req, hint in fs])
    kb = [[Button(f"✏️ {label[:30]}", f"cdf:{uid}:{f}")] for f, label, _, _, _ in fs]
    kb.append([Button("‹ Анкета", f"cd:{uid}")])
    return Reply(view=v, buttons=kb)


@screen("cdf")
def field(ctx, uid, f):
    if not may(ctx, uid) or f not in FIELDS:
        return show(ctx, uid)
    g, label, kind, req, opts = FIELDS[f]
    c = ensure(ctx, uid)
    cur = c["f"].get(f, "")
    if kind == "choice":
        kb = rows([Button(("• " if cur == o else "") + o, f"cdv:{uid}:{f}:{i}") for i, o in enumerate(opts)], 2)
        kb.append([Button("‹ Назад", f"cdg:{uid}:{g}")])
        return Reply(text=f"{label}:", buttons=kb)
    return ctx.ask("card_text", f"{label}" + (f" ({opts})" if opts else "") + (f"\n\nСейчас: {cur}" if cur else "") +
                   "\n\nНапишите в ответ. «-» — очистить.", {"uid": str(uid), "f": f},
                   buttons=[[Button("✖️ Отмена", f"cdg:{uid}:{g}")]])


@screen("cdv")
def choose(ctx, uid, f, i):
    if not may(ctx, uid) or f not in FIELDS or FIELDS[f][2] != "choice":
        return show(ctx, uid)
    opts = FIELDS[f][4]
    c = ensure(ctx, uid)
    if 0 <= int(i) < len(opts):
        c["f"][f] = opts[int(i)]
        save(ctx.office, uid, c)
    return group(ctx, uid, FIELDS[f][0])


@text_input("card_text")
def on_text(ctx, text, data):
    uid, f = data["uid"], data["f"]
    if not may(ctx, uid) or f not in FIELDS:
        return show(ctx, uid)
    t = " ".join((text or "").split())[:300]
    c = ensure(ctx, uid)
    if t in ("-", "—"):
        c["f"].pop(f, None)
    elif t:
        c["f"][f] = t
    save(ctx.office, uid, c)
    return group(ctx, uid, FIELDS[f][0])


@screen("cdn")
def note(ctx, uid):
    if not may(ctx, uid):
        return show(ctx, uid)
    return ctx.ask("card_note", "Заметка к клиенту (видят партнёр и администратор):", {"uid": str(uid)},
                   buttons=[[Button("✖️ Отмена", f"cd:{uid}")]])


@text_input("card_note")
def on_note(ctx, text, data):
    uid = data["uid"]
    if not may(ctx, uid):
        return show(ctx, uid)
    t = " ".join((text or "").split())[:300]
    if t:
        c = ensure(ctx, uid)
        c.setdefault("notes", []).append({"ts": datetime.now().strftime("%d.%m %H:%M"), "who": ctx.name()[:30], "text": t})
        c["notes"] = c["notes"][-20:]
        save(ctx.office, uid, c)
    return show(ctx, uid)


@screen("cds")
def cards_list(ctx):
    if ctx.user_id not in ctx.app.admins:
        return Reply(text="Список анкет видит администратор.")
    allc = cards(ctx.office)
    guests = ctx.office.get("demo_guests") or {}
    items = sorted(allc.values(), key=lambda c: c.get("updated", ""), reverse=True)
    v = View("📋 Анкеты клиентов", f"всего {len(items)} · ⚠️ с нестандартом: {sum(1 for c in items if flags(c))}")
    cs = []
    for c in items[:20]:
        done, total = progress(c)
        p = guests.get(c.get("by") or "") or {}
        s = c.get("set") or {}
        cs.append(Card(_title(c), f"{done}/{total}", [f"{s.get('title', '—')} · {money(s.get('monthly', 0))} в месяц",
                                                     (f"партнёр {p.get('note') or p.get('name')}" if p else "напрямую") + f" · {c.get('updated', '')}"]
                       + ([f"⚠️ {len(flags(c))} нестандарт"] if flags(c) else []),
                       color="good" if done == total else "warn" if flags(c) else None))
    v.cards(cs)
    if not items:
        v.note("Анкет пока нет. Анкета появляется, когда клиент или партнёр открывает «📋 Анкета» или жмёт «Хочу так».")
    kb = rows([Button(_title(c)[:24], f"cd:{c['uid']}") for c in items[:20]], 2)
    kb.append([Button("‹ Пульт", "adm")])
    return Reply(view=v, buttons=kb)


# ---------- выгрузка ----------

def card_view(c):
    v = View("Анкета клиента", _title(c))
    s = c.get("set") or {}
    if s:
        cnt = s.get("counts") or {}
        v.lines([f"Набор: {s.get('title', '—')} · людей {s.get('people', 0)}", f"В месяц {money(s.get('monthly', 0))} · "
                 f"подключение {money(s.get('setup', 0))}",
                 f"Люди: продажи {cnt.get('sales', 0)}, склад и закупки {cnt.get('stock', 0)}, логистика {cnt.get('logistics', 0)}, "
                 f"офис {cnt.get('office', 0)}", f"Торговым платят: {pay_text(s.get('pay') or {})}"], title="Из конструктора")
    for g, title, fs in GROUPS:
        v.lines([f"{label}: {c['f'].get(f) or '—'}" for f, label, _, _, _ in fs], title=title)
    if flags(c):
        v.lines([f"⚠️ {x}" for x in flags(c)], title="Нестандарт")
    if c.get("notes"):
        v.lines([f"{n['ts']} · {n['who']}: {n['text']}" for n in c["notes"]], title="Заметки")
    return v


@screen("cdp")
def pdf(ctx, uid):
    if not may(ctx, uid):
        return show(ctx, uid)
    c = ensure(ctx, uid)
    return Reply(text="📄 Анкета клиента", files=[(f"Анкета {_title(c)[:30]}.pdf", to_pdf([card_view(c)]))],
                 buttons=[[Button("‹ Анкета", f"cd:{uid}")]])


def excel(c):
    """Заполненная таблица-анкета для tools/anketa.py apply: компания, пакеты, отделы и должности по набору,
    схемы зарплаты по вариациям конструктора со стандартными цифрами (их уточняем с клиентом)."""
    import yaml
    from core import anketa as A
    from core.config import client_dir
    raw = yaml.safe_load((client_dir("_template") / "config.yaml").read_text(encoding="utf-8"))
    f, s = c.get("f", {}), c.get("set", {})
    name = f.get("company") or "Новый клиент"
    raw["id"] = A.slug(name)
    raw["company"]["name"] = name
    ind = next((k for k, v in INDUSTRIES.items() if v == f.get("industry")), s.get("industry") or "building")
    raw["company"]["industry"] = ind
    pk = E.normalize(s.get("packages") or {"sales": "business"})
    raw.pop("edition", None)
    raw["packages"] = {k: (v or False) if k == "sales" else bool(v) for k, v in pk.items()}
    kinds_on = {"sales"} if pk["sales"] else set()
    kinds_on |= {"warehouse", "purchasing"} if pk["stock"] else set()
    kinds_on |= {"logistics"} if pk["logistics"] else set()
    kinds_on |= {"office"}
    raw["departments"] = [d for d in raw["departments"] if d["kind"] in kinds_on]
    if pk["stock"] and not any(d["kind"] == "purchasing" for d in raw["departments"]):
        raw["departments"].append({"id": "purchasing", "name": "Закупки", "kind": "purchasing"})
    dept_ids = {d["id"] for d in raw["departments"]}
    raw["positions"] = [p for p in raw["positions"] if p["dept"] in dept_ids]
    if pk["stock"]:
        raw["positions"] += [{"id": "buy_head", "name": "Начальник закупок", "dept": "purchasing", "role": "head", "pay": "salary"},
                             {"id": "buyer", "name": "Менеджер по закупкам", "dept": "purchasing", "role": "staff", "pay": "salary"}]
    raw["teams"] = [t for t in raw["teams"] if t["dept"] in dept_ids]
    raw["lines"]["main"]["name"] = name
    wk = {"Воскресенье": ["sun"], "Суббота и воскресенье": ["sat", "sun"], "Пятница": ["fri"]}.get(f.get("weekend"))
    if wk:
        raw["work_week"]["weekend"] = wk
    pay = dict(PAY_DEFAULT)
    pay.update(s.get("pay") or {})
    rep = []
    if pay.get("salary"):
        rep.append({"type": "salary", "amount": "25 000"})
    if pay.get("percent"):
        rep.append({"type": "sales_percent", "rate": "0,4%"})
    plan = pay.get("plan", "steps")
    if plan == "steps":
        rep.append({"type": "plan_bonus", "metric": "sales", "mode": "steps", "steps": [["80%", "5 000"], ["100%", "15 000"], ["120%", "20 000"]]})
    elif plan == "fix":
        rep.append({"type": "plan_bonus", "metric": "sales", "mode": "steps", "steps": [["100%", "15 000"]]})
    elif plan == "linear":
        rep.append({"type": "plan_bonus", "metric": "sales", "mode": "linear", "amount": "15 000", "min": "80%", "cap": "120%"})
    if pay.get("akb"):
        rep.append({"type": "plan_bonus", "metric": "akb", "mode": "linear", "amount": "5 000", "min": "70%", "cap": "120%"})
    if pay.get("debt"):
        rep.append({"type": "debt_penalty", "days": 60, "share": "10%", "amount": "3 000"})
    rep.append({"type": "timesheet"})
    raw["pay_schemes"]["rep"] = rep
    drv = [{"type": "salary", "amount": "30 000"}]
    if pay.get("drivers"):
        drv += [{"type": "per_unit", "unit": "km", "price": "4"}, {"type": "per_unit", "unit": "points", "price": "25"}]
    raw["pay_schemes"]["driver"] = drv + [{"type": "timesheet"}]
    wh = [{"type": "salary", "amount": "35 000"}]
    if pay.get("wh"):
        wh += [{"type": "fixed_bonus", "name": "Без ошибок", "amount": "5 000", "cut_per_miss": "1 000"},
               {"type": "per_unit", "unit": "ret_err", "price": "500", "cut": True, "name": "Ошибки склада в возвратах"}]
    raw["pay_schemes"]["warehouse"] = wh + [{"type": "timesheet"}]
    tables = A.from_raw(raw)
    qa = [["Вопрос", "Ответ"]]
    for g, title, fs in GROUPS:
        qa.append([title, ""])
        qa += [[label, f.get(fld, "")] for fld, label, _, _, _ in fs]
    cnt = s.get("counts") or {}
    qa += [["Из конструктора", ""], ["Набор", s.get("title", "")], ["В месяц, ₽", s.get("monthly", "")], ["Подключение, ₽", s.get("setup", "")],
           ["Людей: продажи / склад и закупки / логистика / офис",
            f"{cnt.get('sales', 0)} / {cnt.get('stock', 0)} / {cnt.get('logistics', 0)} / {cnt.get('office', 0)}"],
           ["Торговым платят", pay_text(pay)]]
    if pay.get("other"):
        qa.append(["Зарплата по-своему", pay["other"]])
    for x in flags(c):
        qa.append(["⚠️ Нестандарт", x])
    for n in c.get("notes") or []:
        qa.append([f"Заметка {n['ts']} ({n['who']})", n["text"]])
    tables = {"Анкета клиента": qa, **tables}
    return A.write_xlsx(tables)


@screen("cdx")
def xlsx(ctx, uid):
    if ctx.user_id not in ctx.app.admins:
        return Reply(text="Excel-анкету выгружает администратор.")
    c = ensure(ctx, uid)
    data = excel(c)
    return Reply(text="📥 Таблица-анкета: лист «Анкета клиента» — ответы и нестандарт; остальные листы — заготовка для "
                      "подключения (цифры схем стандартные — уточнить с клиентом), дальше tools/anketa.py check / apply.",
                 files=[(f"Анкета {_title(c)[:30]}.xlsx", data)], buttons=[[Button("‹ Анкета", f"cd:{uid}")]])
