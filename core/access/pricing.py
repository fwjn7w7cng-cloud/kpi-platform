"""Цены конструктора: за пакет в месяц + за каждого человека в подключённых отделах, подключение — один раз.

  в месяц      Σ по пакетам (пакет + люди × цена за человека) − скидка «все четыре отдела» + «Максимум»
  подключение  первый пакет + каждый следующий (+ «Максимум» отдельно) — платится один раз
  за год       −disc_year % к месячной сумме
Директор и администратор в счёт не входят. Стандартные цены — PRICES; свои администратор правит в приложении
(«💰 Цены»), они лежат в базе офиса под ключом «prices» и перекрывают стандартные.
"""
from core.access import editions as E

# код, подпись, стандарт, единица
PRICES = [
    ("sales_start",         "Продажи Старт — пакет",            5000,  "₽/мес"),
    ("sales_start_user",    "Продажи Старт — за человека",      500,   "₽/мес за чел."),
    ("sales_business",      "Продажи Бизнес — пакет",           10000, "₽/мес"),
    ("sales_business_user", "Продажи Бизнес — за человека",     700,   "₽/мес за чел."),
    ("sales_control",       "Продажи Премиум — пакет",          15000, "₽/мес"),
    ("sales_control_user",  "Продажи Премиум — за человека",    1000,  "₽/мес за чел."),
    ("stock",               "Склад и закупки — пакет",          5000,  "₽/мес"),
    ("stock_user",          "Склад и закупки — за человека",    500,   "₽/мес за чел."),
    ("logistics",           "Логистика — пакет",                5000,  "₽/мес"),
    ("logistics_user",      "Логистика — за человека",          500,   "₽/мес за чел."),
    ("office",              "Кадры и офис — пакет",             3000,  "₽/мес"),
    ("office_user",         "Кадры и офис — за человека",       300,   "₽/мес за чел."),
    ("max",                 "Максимум — пакет, до 3 функций",   40000, "₽/мес"),
    ("custom_extra",        "Функция на заказ сверх трёх",      15000, "₽ за штуку"),
    ("setup_first",         "Подключение — первый пакет",       15000, "₽ один раз"),
    ("setup_next",          "Подключение — следующий пакет",    10000, "₽ один раз"),
    ("setup_max",           "Подключение «Максимума»",          30000, "₽ один раз"),
    ("disc_all",            "Скидка за все четыре отдела",      15,    "%"),
    ("disc_year",           "Скидка при оплате за год",         10,    "%"),
    ("trial_days",          "Пробный период",                   14,    "дней"),
]
DEFAULT = {code: value for code, _, value, _ in PRICES}
TITLES = {code: (title, unit) for code, title, _, unit in PRICES}
KEY = "prices"
ROUND = 100


def prices(db):
    """Действующие цены: стандартные, поверх — что поправил администратор."""
    own = db.get(KEY) or {}
    return {code: float(own.get(code, value)) for code, value in DEFAULT.items()}


def set_price(db, code, value, who=""):
    if code not in DEFAULT:
        raise ValueError(code)
    own = db.get(KEY) or {}
    own[code] = value
    db.put(KEY, own)
    db.audit(who, "price", f"{TITLES[code][0]}: {fmt(value)} {TITLES[code][1]}")


def reset(db, who=""):
    db.put(KEY, {})
    db.audit(who, "price", "цены сброшены к стандартным")


def changed(db):
    """Коды цен, которые отличаются от стандартных."""
    own = db.get(KEY) or {}
    return {c for c, v in own.items() if c in DEFAULT and float(v) != float(DEFAULT[c])}


def headcount(cfg, db):
    """Сколько людей в отделах каждого пакета — активные сотрудники без директора."""
    out = {c: 0 for c in E.PACKAGES}
    kinds = {d.id: d.kind for d in cfg.departments}
    for e in db.employees():
        if e["role"] == "owner":
            continue
        pkg = E.dept_package(kinds.get(e["dept"], ""))
        if pkg:
            out[pkg] += 1
    return out


def rnd(x):
    return int(round(x / ROUND)) * ROUND


def fmt(x):
    x = float(x)
    s = f"{int(round(x)):,}".replace(",", " ") if x == int(x) else f"{x:,.1f}".replace(",", " ").replace(".", ",")
    return s


def quote(packages, counts, pr, year=False):
    """Расчёт: {lines, dept_sum, discount, monthly, monthly_year, year_total, setup_lines, setup, people, all_depts}.
    lines — [(подпись, сумма в месяц, пояснение)] по пакетам."""
    p = E.normalize(packages)
    counts = counts or {}
    lines, setup_lines = [], []
    dept_sum, n_pkgs, people = 0.0, 0, 0
    for code in ("sales", "stock", "logistics", "office"):
        v = p[code]
        if not v:
            continue
        key = f"sales_{v}" if code == "sales" else code
        base, per = pr[key], pr[f"{key}_user"]
        n = int(counts.get(code, 0))
        amount = base + per * n
        title = E.PACKAGES[code]["title"] + (f" · {E.LEVELS[v]}" if code == "sales" else "")
        lines.append((f"{E.PACKAGES[code]['icon']} {title}", amount,
                      f"{fmt(base)} + {n} чел. × {fmt(per)}" if n else f"{fmt(base)}, людей пока нет"))
        dept_sum += amount
        n_pkgs += 1
        people += n
    all_depts = n_pkgs == 4
    discount = dept_sum * pr["disc_all"] / 100 if all_depts else 0.0
    max_sum = pr["max"] if p["max"] else 0.0
    if p["max"]:
        lines.append((f"{E.PACKAGES['max']['icon']} {E.PACKAGES['max']['title']}", max_sum, "бренд, 1С напрямую, филиалы, до 3 функций на заказ"))
    monthly = rnd(dept_sum - discount + max_sum)
    monthly_year = rnd(monthly * (1 - pr["disc_year"] / 100))
    if n_pkgs:
        setup_lines.append(("Первый пакет", pr["setup_first"]))
        if n_pkgs > 1:
            setup_lines.append((f"Ещё {n_pkgs - 1} × {fmt(pr['setup_next'])}", pr["setup_next"] * (n_pkgs - 1)))
    if p["max"]:
        setup_lines.append(("«Максимум»: бренд, связь с 1С", pr["setup_max"]))
    setup = sum(a for _, a in setup_lines)
    return {"lines": lines, "dept_sum": dept_sum, "discount": discount, "all_depts": all_depts, "monthly": monthly,
            "monthly_year": monthly_year, "year": bool(year), "year_total": monthly_year * 12, "setup_lines": setup_lines,
            "setup": setup, "people": people, "packages": p, "trial_days": int(pr["trial_days"]), "empty": not n_pkgs and not p["max"]}
