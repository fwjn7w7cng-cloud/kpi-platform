"""Цены конструктора (Адам 31.10): платим только за функции; отдел — папка; настройки внутри функции — бесплатно.

  в месяц      Σ цен выбранных функций + люди в подключённых отделах × цена за человека − скидка «все четыре отдела»
               + дополнительное (бренд, филиалы, функции на заказ — без скидки)
  подключение  месячная оплата × setup_mult (1,5), но не меньше setup_min (20 000), до тысячи — платится один раз
               (Адам 30.10: подключение должно быть выше месячной оплаты)
  за год       −disc_year % к месячной сумме
Директор и администратор в счёт не входят. Стандартные цены — PRICES; свои администратор правит в приложении
(«💰 Цены»), они лежат в базе офиса под ключом «prices» и перекрывают стандартные.
"""
from core.access import editions as E

# код, подпись, стандарт, единица
# Цены за функции (Адам 31.10): отдел — папка, платим только за функции; настройки внутри функции — бесплатно.
# Код цены функции — f_<код модуля>; подписи и отделы — editions.FUNCS / MODULES.
def _func_prices():
    out = []
    for code, (grp, price) in E.FUNCS.items():
        icon, gtitle = E.FUNC_GROUPS[grp]
        out.append((f"f_{code}", f"{icon} {gtitle} · {E.FUNC_SHORT[code]}", price, "₽/мес"))
    return out


PRICES = _func_prices() + [
    ("user",                "👥 За человека в приложении (кроме директора и админа)", 300, "₽/мес за чел."),
    ("custom_extra",        "Функция на заказ сверх трёх",      15000, "₽ за штуку"),
    ("setup_mult",          "Подключение — во сколько раз выше месяца", 1.5, "× месячной оплаты"),
    ("setup_min",           "Подключение — не меньше",          20000, "₽ один раз"),
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


def quote(sel, counts, pr, year=False):
    """Расчёт по функциям: {lines, dept_sum, discount, monthly, monthly_year, year_total, setup_lines, setup, people,
    all_depts, functions, packages}. sel — набор функций (множество / список) или, по-старому, словарь пакетов.
    lines — [(подпись, сумма в месяц, пояснение)]: отделы с их функциями, люди, дополнительное."""
    funcs = E.functions_of(sel) if isinstance(sel, dict) else {f for f in (sel or ()) if f in E.FUNCS}
    p = E.packages_of(funcs)
    counts = counts or {}
    lines, setup_lines = [], []
    dept_sum = 0.0
    for grp, (icon, gtitle) in E.FUNC_GROUPS.items():
        if grp == "max":
            continue
        fs = [f for f in E.FUNCS if f in funcs and E.FUNCS[f][0] == grp]
        if not fs:
            continue
        amount = sum(pr[f"f_{f}"] for f in fs)
        lines.append((f"{icon} {gtitle}", amount, f"{len(fs)} функц.: " + ", ".join(E.FUNC_SHORT[f] for f in fs)))
        dept_sum += amount
    people = sum(int(counts.get(d, 0)) for d in ("sales", "stock", "logistics", "office") if p.get(d))
    if people:
        lines.append(("👥 Люди в приложении", people * pr["user"], f"{people} чел. × {fmt(pr['user'])}"))
        dept_sum += people * pr["user"]
    all_depts = all(p.get(d) for d in ("sales", "stock", "logistics", "office"))
    discount = dept_sum * pr["disc_all"] / 100 if all_depts else 0.0
    extra = [f for f in E.FUNCS if f in funcs and E.FUNCS[f][0] == "max"]
    max_sum = sum(pr[f"f_{f}"] for f in extra)
    if extra:
        icon, gtitle = E.FUNC_GROUPS["max"]
        lines.append((f"{icon} {gtitle}", max_sum, ", ".join(E.FUNC_SHORT[f] for f in extra)))
    monthly = rnd(dept_sum - discount + max_sum)
    monthly_year = rnd(monthly * (1 - pr["disc_year"] / 100))
    setup = 0
    if funcs:
        raw = monthly * pr["setup_mult"]
        setup = max(int(round(raw / 1000)) * 1000, int(pr["setup_min"]))
        setup_lines.append((f"Месячная оплата × {fmt(pr['setup_mult'])}", setup) if setup > pr["setup_min"] else
                           (f"Минимум (месяц × {fmt(pr['setup_mult'])} = {fmt(rnd(raw))})", setup))
    return {"lines": lines, "dept_sum": dept_sum, "discount": discount, "all_depts": all_depts, "monthly": monthly,
            "monthly_year": monthly_year, "year": bool(year), "year_total": monthly_year * 12, "setup_lines": setup_lines,
            "setup": setup, "people": people, "packages": p, "functions": funcs, "trial_days": int(pr["trial_days"]),
            "empty": not funcs}

