"""🧱 Конструктор зарплаты (Адам 31.10): схема оплаты по должностям собирается из блоков на отдельной странице,
рядом — живой пример настоящим движком на человеке этой должности.

Каждый показатель — свой блок со своими вариантами (процент: единый / с порога / ступенями / до и сверх плана;
бонус за план: фикс / порог / ступени / пропорционально). Ни один блок не обязателен — «🧹 С нуля».
Блок → кубик ядра (`core/pay/components.py`); схема → `set.schemes` анкеты → config.yaml клиента.
Где лежит: сессия зрителя — `pb` = {должность: [{b: блок, v: вариант, p: {поле: значение}}]}, `pb_pos` — открытая.
У «Сдельно» и «Удержаний» вариант — единица работы (км, точки, ошибки…), можно несколько сразу.

Управление (Адам 31.10: «ничего не уносит, везде ОК / Отмена»): pb[:должность] — схема · pbs — выбор блоков галочками
(pbs:t:<блок>, pbs:ok / pbs:no) · pbe:<блок> — варианты блока галочками, цифры (:f:<вариант>:<поле>), выбор (:c:…), :ok / :no.
До «ОК» схема не меняется (черновики pb_sel / pb_blk в сессии); «‹ Назад» в окне — на страницу конструктора, как «Отмена» (`app.STAY`).
"""
import copy
import re

from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import screen, text_input
from core.ui.views import Card, View

# ---------- должности ----------
# код: (подпись, значок, роль в базе для примера, должности анкеты для примера, схема в анкете клиента)
POSITIONS = {
    "rep":    ("Торговый", "🧑‍💼", "rep", ("rep",), "rep"),
    "sv":     ("Супервайзер", "👥", "sv", ("sv",), "sv"),
    "head":   ("Руководитель продаж", "🏢", "head", ("rop",), "rop"),
    "wh":     ("Склад", "📦", "staff", ("storekeeper", "wh_head"), "warehouse"),
    "driver": ("Водитель", "🚚", "staff", ("driver",), "driver"),
    "buyer":  ("Закупщик", "🛒", "staff", ("buyer", "buy_head", "purchaser"), "buyer"),
    "office": ("Офис и бухгалтерия", "🏛", "staff", ("accountant", "manager", "hr", "dispatcher"), "salary"),
}
SCOPE_OF = {"sv": "team", "head": "dept"}           # по чьим продажам считается у руководителя

METRICS = {"sales": "продажи", "focus": "фокусные", "akb": "АКБ (клиенты)", "sku": "SKU (ассортимент)",
           "collect": "сбор денег", "visits": "визиты по маршруту"}
SCOPES = {"self": "свои", "team": "команды", "dept": "отдела", "all": "всей компании"}

# ---------- блоки ----------
# поле: (подпись, вид) — вид: money | pct | int | coef | text | steps_money | steps_coef | amounts | metric | scope | unit
F = {
    "amount": ("Сумма", "money"), "rate": ("Ставка", "pct"), "rate_above": ("Ставка сверх плана", "pct"),
    "min_plan": ("Порог плана", "pct"), "focus_rate": ("Ставка по фокусным", "pct"), "min": ("Порог", "pct"),
    "cap": ("Потолок", "pct"), "steps": ("Ступени", "steps_money"), "rate_steps": ("Ставки по плану", "steps_rate"), "coefs": ("Множители", "steps_coef"),
    "share": ("Допустимая доля", "pct"), "days": ("Просрочка от, дней", "int"), "max": ("Не больше", "money"),
    "debt_steps": ("Ступени просрочки", "steps_money"), "months": ("Не брал, месяцев", "int"), "norm": ("Норма скидки", "pct"),
    "amounts": ("За места", "amounts"), "name": ("Название", "text"), "cut_per_miss": ("Минус за пропуск", "money"),
    "min_total": ("Гарантированный минимум", "money"), "max_variable": ("Потолок премии", "money"),
    "metric": ("Показатель", "metric"), "scope": ("Чьи продажи", "scope"), "unit": ("За что", "unit"), "price": ("Цена", "money"),
}
SALES = ("rep", "sv", "head")
ALL = tuple(POSITIONS)
UNITS = {   # единица сдельной оплаты: (подпись, должности, по умолчанию, это удержание?)
    "km": ("за километр пробега", ("driver",), 4, False), "kg": ("за кг груза", ("driver",), 0.25, False),
    "m3": ("за м³ груза", ("driver",), 30, False), "points": ("за точку доставки", ("driver",), 25, False),
    "trips": ("за рейс", ("driver",), 300, False), "nights": ("за ночёвку в рейсе", ("driver",), 1000, False),
    "overtime": ("за минуту переработки", ("driver",), 3, False), "cash": ("за инкассацию у клиента", ("driver",), 70, False),
    "pickup": ("за привоз от поставщика", ("driver",), 200, False),
    "all_points": ("за отгрузку отдела (точку)", ("wh",), 5, False), "fuel_save": ("доля сэкономленного топлива", ("driver",), 0.5, False),
    "tasks_done": ("за задачу в срок", ALL, 500, False),
    "ret_err": ("ошибка в возврате", ("wh", "driver"), 500, True), "ret_own": ("доля возвратов по своей вине", ("rep",), 0.03, True),
    "fuel_over": ("доля перерасхода топлива", ("driver",), 1, True), "stockout": ("позиция «закончилось»", ("buyer",), 200, True),
    "stale": ("залежалая позиция", ("buyer",), 100, True), "tasks_late": ("задача не в срок", ALL, 300, True),
}
RATE_UNITS = {"ret_own", "fuel_save", "fuel_over"}
ONCE = {"salary", "timesheet", "limits", "seniority"}      # единица в ₽ — ставка долей, а не цена за штуку

# id: (раздел, значок, название, для кого, [(вариант, подпись, поля, шаблон кубика)], описание)
BRICKS = {
    "salary": ("Основа", "💼", "Оклад", ALL, [
        ("days", "за отработанные дни", {"amount": 30000}, {"type": "salary", "mode": "days"}),
        ("fixed", "фикс за месяц", {"amount": 30000}, {"type": "salary", "mode": "fixed"}),
        ("calendar", "по календарным дням", {"amount": 30000}, {"type": "salary", "mode": "calendar"}),
    ], "постоянная часть"),
    "company": ("За план", "🏆", "Доля от плана компании", ALL, [
        ("steps", "ступени", {"steps": [[0.9, 3000], [1.0, 7000]]}, {"type": "plan_bonus", "metric": "sales", "scope": "all", "mode": "steps"}),
        ("linear", "пропорционально", {"amount": 7000, "min": 0.8, "cap": 1.2},
         {"type": "plan_bonus", "metric": "sales", "scope": "all", "mode": "linear"}),
    ], "премия, если вся фирма делает план — офису, складу, логистике"),
    "debt_pen": ("Деньги и долги", "💵", "Удержание за просрочку", SALES, [
        ("fixed", "фикс, если просрочка выше нормы", {"share": 0.1, "amount": 3000, "days": 60}, {"type": "debt_penalty", "mode": "fixed"}),
        ("percent", "% от суммы просрочки", {"rate": 0.02, "max": 10000, "days": 60}, {"type": "debt_penalty", "mode": "percent"}),
        ("steps", "ступени по доле просрочки", {"debt_steps": [[0.05, 1000], [0.1, 3000], [0.2, 6000]], "days": 60},
         {"type": "debt_penalty", "mode": "steps"}),
    ], "минус, когда долги клиентов выше нормы"),
    "debt_bonus": ("Деньги и долги", "🟢", "Бонус за низкую просрочку", SALES, [
        ("fixed", "фикс, если просрочка в норме", {"share": 0.05, "amount": 3000, "days": 60}, {"type": "debt_bonus", "mode": "fixed"}),
        ("steps", "ступени", {"debt_steps": [[0.02, 5000], [0.05, 3000]], "days": 60}, {"type": "debt_bonus", "mode": "steps"}),
    ], "плюс за чистую дебиторку"),
    "new_clients": ("Клиенты", "🆕", "За новых клиентов", SALES, [
        ("each", "за каждого", {"amount": 1000, "months": 3}, {"type": "new_clients"}),
    ], "новый или вернувшийся — не брал несколько месяцев"),
    "rank": ("Клиенты", "🥇", "Место в рейтинге", SALES, [
        ("places", "за 1-е, 2-е, 3-е место", {"metric": "sales", "amounts": [5000, 3000, 1000]}, {"type": "rank_bonus"}),
    ], "соревнование внутри отдела"),
    "discount": ("Удержания", "🏷", "Лишние скидки", SALES, [
        ("fixed", "фикс, если скидка выше нормы", {"norm": 0.05, "amount": 2000}, {"type": "discount_penalty", "mode": "fixed"}),
        ("percent", "% от продаж, если выше нормы", {"norm": 0.05, "rate": 0.002}, {"type": "discount_penalty", "mode": "percent"}),
    ], "средняя скидка торгового выше допустимой"),
    "piece": ("Сдельно", "🚚", "Сдельно", ("driver", "wh", "office", "buyer", "rep"), [
        ("unit", "за единицу работы", {"unit": "km", "price": 4}, {"type": "per_unit"}),
    ], "км, кг, точки, рейсы, ночёвки, отгрузки, задачи…"),
    "cut": ("Удержания", "➖", "Удержание за ошибки", ALL, [
        ("unit", "за каждую", {"unit": "ret_err", "price": 500}, {"type": "per_unit", "cut": True}),
    ], "ошибки в возвратах, перерасход, «закончилось», задачи не в срок"),
    "discipline": ("Прочее", "⏰", "Бонус за дисциплину", ALL, [
        ("cut", "минус за каждый пропуск", {"name": "Без ошибок и пропусков", "amount": 5000, "cut_per_miss": 1000},
         {"type": "fixed_bonus"}),
    ], "фикс, который тает за пропуски"),
    "fixed": ("Прочее", "🎁", "Фикс-премия или компенсация", ALL, [
        ("plain", "каждый месяц", {"name": "Компенсация связи", "amount": 1000}, {"type": "fixed_bonus"}),
    ], "связь, ГСМ, питание, совмещение"),
    "seniority": ("Прочее", "🎖", "За стаж", ALL, [
        ("years", "за каждый полный год", {"amount": 1000, "max": 5000}, {"type": "seniority"}),
    ], "надбавка за годы в компании"),
    "timesheet": ("Основа", "📅", "Штрафы табеля", ALL, [
        ("rules", "опоздания и прогулы", {}, {"type": "timesheet"}),
    ], "по правилам табеля фирмы"),
    "mult": ("Множители и пределы", "✖️", "Множитель от плана", SALES, [
        ("bonus", "к бонусам", {"metric": "sales", "coefs": [[0.9, 0.8], [1.0, 1.0], [1.1, 1.2]]},
         {"type": "multiplier", "applies": "bonus"}),
        ("percent", "к процентам", {"metric": "sales", "coefs": [[0.9, 0.8], [1.0, 1.0], [1.1, 1.2]]},
         {"type": "multiplier", "applies": "percent"}),
        ("variable", "ко всей премии", {"metric": "sales", "coefs": [[0.9, 0.8], [1.0, 1.0], [1.1, 1.2]]},
         {"type": "multiplier", "applies": "variable"}),
    ], "выполнение плана умножает премию"),
    "mult_debt": ("Множители и пределы", "📉", "Множитель от просрочки", SALES, [
        ("variable", "ко всей премии", {"coefs": [[0.1, 0.9], [0.2, 0.7]], "days": 60},
         {"type": "multiplier", "metric": "overdue", "applies": "variable"}),
        ("bonus", "к бонусам", {"coefs": [[0.1, 0.9], [0.2, 0.7]], "days": 60},
         {"type": "multiplier", "metric": "overdue", "applies": "bonus"}),
    ], "большая просрочка урезает премию"),
    "limits": ("Множители и пределы", "🧱", "Минимум и потолок", ALL, [
        ("both", "гарантия и потолок", {"min_total": 40000, "max_variable": 150000}, {"type": "limits"}),
    ], "не меньше и не больше"),
}
SECTIONS = ["Основа", "Показатели", "За план", "Деньги и долги", "Клиенты", "Удержания", "Сдельно", "Множители и пределы", "Прочее"]
SECTION_ICON = {"Основа": "💼", "Показатели": "📊", "За план": "🎯", "Деньги и долги": "💵", "Клиенты": "🆕", "Удержания": "➖",
                "Сдельно": "🚚", "Множители и пределы": "✖️", "Прочее": "🎁"}


# Каждый показатель — свой блок со своими вариантами (Адам 31.10: «каждый показатель — свой личный блок»).
# код: (значок, название, процент можно?, ставка по умолчанию, бонус по умолчанию, описание)
METRIC_BLOCKS = {
    "sales":   ("📈", "Продажи", True, 0.004, 15000, "процент с продаж и бонусы за план продаж"),
    "focus":   ("🎯", "Фокусные", True, 0.008, 5000, "процент с фокусных групп и бонусы за их план"),
    "collect": ("💵", "Сбор денег", True, 0.005, 5000, "процент с собранных денег и бонусы за план сбора"),
    "akb":     ("👥", "АКБ — клиенты", False, 0, 5000, "бонус за число покупавших клиентов"),
    "sku":     ("🧩", "SKU — ассортимент", False, 0, 3000, "бонус за ассортимент в точках"),
    "visits":  ("🚗", "Визиты по маршруту", False, 0, 3000, "бонус за визиты с заказом"),
}


def _metric_variants(code, pct_ok, rate, amount):
    v = []
    if pct_ok:
        t = {"type": "sales_percent", "base": code}
        v += [("pct", "% — план не учитывается", {"rate": rate}, dict(t)),
              ("pct_gate", "% — только с порога плана", {"rate": rate, "min_plan": 0.8}, dict(t)),
              ("pct_steps", "% — ставка растёт с планом", {"rate_steps": [[0.8, rate * 0.75], [1.0, rate], [1.2, rate * 1.5]]}, dict(t)),
              ("pct_split", "% — до плана и сверх плана", {"rate": rate, "rate_above": rate * 2}, dict(t))]
    b = {"type": "plan_bonus", "metric": code}
    v += [("fix", "фикс за выполнение плана", {"amount": amount}, {**b, "mode": "fixed", "min": 1.0}),
          ("gate", "фикс с порога плана", {"amount": amount, "min": 0.8}, {**b, "mode": "fixed"}),
          ("steps", "пороги (ступени)", {"steps": [[0.8, amount / 3], [1.0, amount], [1.2, amount * 4 / 3]]}, {**b, "mode": "steps"}),
          ("linear", "пропорционально выполнению", {"amount": amount, "min": 0.8, "cap": 1.2}, {**b, "mode": "linear"})]
    return v


for _code, (_ic, _t, _pct, _r, _a, _d) in METRIC_BLOCKS.items():
    BRICKS[_code] = ("Показатели", _ic, _t, SALES, _metric_variants(_code, _pct, _r, _a), _d)
BRICKS["margin"] = ("Показатели", "📊", "Наценка", SALES, [
    ("pct", "% от наценки (нужна себестоимость в 1С)", {"rate": 0.05}, {"type": "sales_percent", "base": "margin"})],
    "процент с наценки — если в отчёте 1С есть себестоимость")


def variants(bid, pos=None):
    """Варианты блока для должности: у «Сдельно» и «Удержаний» вариант — это единица работы (км, точки, ошибки…)."""
    if bid in ("piece", "cut"):
        cut = bid == "cut"
        return [(u, t[0], {"price": t[2]}, {"type": "per_unit", "unit": u, "cut": cut})
                for u, t in UNITS.items() if t[3] == cut and (pos is None or pos in t[1])]
    return BRICKS[bid][4]


def group(bid, v):
    """Группа выбора внутри блока: в одной группе — один вариант (переключатель), разные группы — можно вместе."""
    if bid in METRIC_BLOCKS:
        return "pct" if v.startswith("pct") else "bonus"
    if bid in ("piece", "cut"):
        return v
    return "main"


def brick(b, v=None, **p):
    if b in ("piece", "cut"):
        unit = p.pop("unit", None) or v or variants(b)[0][0]
        out = {"b": b, "v": unit, "p": {"price": UNITS[unit][2]}}
        out["p"].update(p)
        return out
    var = next(x for x in BRICKS[b][4] if v is None or x[0] == v)
    out = {"b": b, "v": var[0], "p": copy.deepcopy(var[2])}
    out["p"].update(p)
    return out


DEFAULTS = {
    "rep": [brick("salary", amount=25000), brick("sales", "pct"), brick("sales", "steps", steps=[[0.8, 5000], [1.0, 15000], [1.2, 20000]]),
            brick("akb", "linear", min=0.7), brick("debt_pen"), brick("timesheet")],
    "sv": [brick("salary", amount=40000), brick("sales", "pct", rate=0.0015), brick("sales", "steps", steps=[[0.9, 10000], [1.0, 20000]]),
           brick("timesheet")],
    "head": [brick("salary", amount=60000), brick("sales", "pct", rate=0.0005), brick("sales", "fix", amount=30000), brick("timesheet")],
    "wh": [brick("salary", amount=35000), brick("discipline"), brick("cut"), brick("timesheet")],
    "driver": [brick("salary", amount=30000), brick("piece", unit="km", price=4), brick("piece", unit="points", price=25),
               brick("discipline"), brick("timesheet")],
    "buyer": [brick("salary", amount=40000), brick("company"), brick("cut", unit="stockout", price=200), brick("timesheet")],
    "office": [brick("salary", "calendar", amount=35000), brick("company"), brick("timesheet")],
}


# ---------- превращение в кубик ядра ----------

def to_comp(pos, item):
    vs = variants(item["b"])
    var = next((x for x in vs if x[0] == item["v"]), vs[0])
    c = copy.deepcopy(var[3])
    p = item["p"]
    for k, v in p.items():
        if k == "coefs":
            c["steps"] = v
        elif k == "rate_steps":
            c["rate_steps"] = v
        elif k == "debt_steps":
            c["steps"] = v
        else:
            c[k] = v
    if c["type"] in ("sales_percent", "plan_bonus", "multiplier", "debt_penalty", "debt_bonus") and "scope" not in c \
            and pos in SCOPE_OF:
        c["scope"] = SCOPE_OF[pos]
    if c["type"] == "per_unit":
        u = c["unit"]
        cut = UNITS.get(u, ("", (), 0, False))[3]
        c["cut"] = cut
        if u in RATE_UNITS:
            c["rate"] = c.pop("price")
        c.setdefault("name", ("Удержание: " if cut else "") + UNITS.get(u, (u,))[0])
    if item["b"] == "timesheet":
        c = {"type": "timesheet"}
    return c


def scheme(pos, items):
    return [to_comp(pos, i) for i in items]


def schemes(session):
    """{схема анкеты: [кубики]} — для анкеты клиента и «🚀 Создать клиента»."""
    pb = (session or {}).get("pb") or {}
    return {POSITIONS[pos][4]: scheme(pos, items) for pos, items in pb.items() if pos in POSITIONS}


def lines(session):
    """[«Торговый: оклад + % от продаж + …»] — коротко для анкеты клиента."""
    pb = (session or {}).get("pb") or {}
    return [f"{POSITIONS[pos][0]}: " + " + ".join(BRICKS[i["b"]][2].lower() for i in items)
            for pos, items in pb.items() if pos in POSITIONS and items]


def needs_data(session):
    """Блоки, которым нужны особые данные из 1С — для «⚠️ нестандарт» в анкете."""
    out = []
    for pos, items in ((session or {}).get("pb") or {}).items():
        for i in items:
            if i["b"] == "margin":
                out.append(f"{POSITIONS[pos][0]}: % от наценки — нужна себестоимость в отчёте 1С")
    return out


# ---------- подписи ----------

def _pct(x, digits=None):
    if x is None:
        return "—"
    v = x * 100
    s = f"{v:.2f}".rstrip("0").rstrip(".") if digits is None else f"{v:.{digits}f}"
    return s.replace(".", ",") + " %"


def _num(x):
    return f"{x:g}".replace(".", ",") if isinstance(x, float) and x != int(x) else money(x, "")


def fmt(field, v):
    kind = F[field][1]
    if kind == "money":
        return money(v)
    if kind == "pct":
        return _pct(v)
    if kind == "int":
        return str(int(v))
    if kind == "coef":
        return f"×{_num(v)}"
    if kind == "steps_money":
        return "; ".join(f"{_pct(a)} → {money(b)}" for a, b in v)
    if kind == "steps_coef":
        return "; ".join(f"{_pct(a)} → ×{_num(b)}" for a, b in v)
    if kind == "steps_rate":
        return "; ".join(f"{_pct(a)} → {_pct(b)}" for a, b in v)
    if kind == "amounts":
        return " / ".join(money(x) for x in v)
    if kind == "metric":
        return METRICS.get(v, v)
    if kind == "scope":
        return SCOPES.get(v, v)
    if kind == "unit":
        return UNITS.get(v, (v,))[0]
    return str(v)


def bname(item):
    """Название блока с уточнением: «Бонус за план: АКБ», «Сдельно: км пробега»."""
    b = BRICKS[item["b"]]
    p = item["p"]
    if item["b"] in METRIC_BLOCKS:
        short = {"pct": "%", "pct_gate": "% с порога", "pct_steps": "% по ступеням плана", "pct_split": "% до/сверх плана",
                 "fix": "фикс за план", "gate": "фикс с порога", "steps": "пороги", "linear": "пропорционально"}
        return f"{b[2]}: {short.get(item['v'], item['v'])}"
    if item["b"] in ("mult", "rank") and p.get("metric", "sales") != "sales":
        return f"{b[2]}: {METRICS[p['metric']].split(' (')[0]}"
    if item["b"] in ("piece", "cut"):
        return f"{b[2]}: " + UNITS.get(item["v"], (item["v"],))[0].replace("за ", "", 1)
    if item["b"] in ("fixed", "discipline") and p.get("name"):
        return p["name"]
    return b[2]


def summary(pos, item):
    """«ступени · продажи · 80 % → 5 000 ₽; …» — коротко, что внутри блока."""
    b = BRICKS[item["b"]]
    p = item["p"]
    if item["b"] in ("piece", "cut"):
        u = item["v"]
        price = p.get("price", 0)
        return UNITS.get(u, (u,))[0] + " — " + (_pct(price) if u in RATE_UNITS else money(price))
    var = next((x for x in b[4] if x[0] == item["v"]), b[4][0])
    parts = [var[1]] if len(b[4]) > 1 else []
    for k in ("metric", "amount", "rate", "rate_steps", "rate_above", "focus_rate", "min_plan", "min", "cap", "steps", "coefs", "share",
              "debt_steps", "days", "max", "months", "norm", "amounts", "cut_per_miss", "min_total", "max_variable"):
        if k in p:
            if k == "days":
                parts.append(f"просрочка {p[k]}+ дн.")
            elif k == "cut_per_miss":
                parts.append(f"−{money(p[k])} за пропуск")
            elif k == "min" and F.get(k):
                parts.append(f"от {_pct(p[k])}")
            elif k == "cap":
                parts.append(f"до {_pct(p[k])}")
            elif k == "share":
                parts.append(f"норма {_pct(p[k])}")
            elif k == "max":
                parts.append(f"не больше {money(p[k])}")
            elif k == "months":
                parts.append(f"не брал {p[k]} мес.")
            elif k == "norm":
                parts.append(f"норма скидки {_pct(p[k])}")
            elif k == "min_total":
                parts.append(f"не меньше {money(p[k])}")
            elif k == "max_variable":
                parts.append(f"премия до {money(p[k])}")
            elif k == "min_plan":
                parts.append(f"при плане от {_pct(p[k])}")
            elif k == "rate_above":
                parts.append(f"сверх плана {_pct(p[k])}")
            elif k == "focus_rate":
                parts.append(f"фокусные {_pct(p[k])}")
            else:
                parts.append(fmt(k, p[k]))
    if "name" in p and item["b"] in ("fixed", "discipline"):
        parts.insert(0, p["name"])
    return " · ".join(parts)


# ---------- состояние ----------

def state(ctx):
    pb = ctx.session.get("pb")
    if not isinstance(pb, dict):
        pb = {}
    return pb


def _upgrade(items):
    """Схемы, собранные до 31.10 (блоки «% от продаж» и «Бонус за план»), — в блоки показателей."""
    out = []
    for it in items:
        if it.get("b") == "percent":
            base = {"collect": "collect"}.get(it.get("v"), "sales")
            v = {"two": "pct_split", "gate": "pct_gate"}.get(it.get("v"), "pct")
            p = {k: x for k, x in it.get("p", {}).items() if k in ("rate", "rate_above", "min_plan")}
            out.append({"b": base, "v": v, "p": {**brick(base, v)["p"], **p}})
        elif it.get("b") == "plan":
            m = it.get("p", {}).get("metric", "sales")
            m = m if m in METRIC_BLOCKS else "sales"
            v = it.get("v") if it.get("v") in ("fixed", "steps", "linear") else "steps"
            v = "fix" if v == "fixed" else v
            p = {k: x for k, x in it.get("p", {}).items() if k != "metric"}
            out.append({"b": m, "v": v, "p": {**brick(m, v)["p"], **p}})
        elif it.get("b") in ("piece", "cut") and it.get("v") == "unit":
            u = it.get("p", {}).get("unit", "km")
            out.append({"b": it["b"], "v": u, "p": {"price": it.get("p", {}).get("price", UNITS.get(u, ("", (), 0))[2])}})
        elif it.get("b") in BRICKS:
            out.append(it)
    return out


def items_of(ctx, pos):
    pb = state(ctx)
    if pos not in pb:
        pb[pos] = copy.deepcopy(DEFAULTS[pos])
        ctx.session["pb"] = pb
    elif any(it.get("b") not in BRICKS or it.get("v") == "unit" for it in pb[pos]):
        pb[pos] = _upgrade(pb[pos])
        ctx.session["pb"] = pb
    return pb[pos]


def _save(ctx, pos, items):
    pb = state(ctx)
    pb[pos] = items
    ctx.session["pb"] = pb
    ctx.save()


def cur_pos(ctx, pos=None):
    if pos in POSITIONS:
        ctx.session["pb_pos"] = pos
        ctx.save()
        return pos
    p = ctx.session.get("pb_pos")
    return p if p in POSITIONS else "rep"


def _may(ctx):
    return ctx.demo or ctx.admin or ctx.role in ("owner", "admin")


# ---------- живой пример ----------

def example(ctx, pos, items):
    """(сотрудник, [(название, сумма, пояснение)]) — расчёт схемы движком на человеке этой должности."""
    _, _, role, posids, _ = POSITIONS[pos]
    es = [e for e in ctx.db.employees() if e["position"] in posids] or [e for e in ctx.db.employees() if e["role"] == role]
    if not es:
        return None, []
    if pos in SALES:
        k = ctx.kpi()
        es.sort(key=lambda e: (k.emp(e["id"])["sales"].pct or 0))
        emp = es[len(es) // 2]                                   # средний по выполнению плана — честный пример
    else:
        emp = es[0]
    from core.pay.engine import Payslip
    pay = ctx.pay()
    slip = Payslip(emp)
    try:
        pay.slip_rows(emp, scheme(pos, items), slip)
    except Exception as e:                                       # noqa: BLE001 — блок с неполными цифрами
        return emp, [("⚠️ Не считается", 0, str(e)[:80])]
    return emp, slip.rows


# ---------- экраны ----------

def _belongs(item, row):
    """Какие строки расчёта дал этот блок (по названию строки)."""
    t = item["b"]
    name = row[0]
    keys = {"salary": ("Оклад",), "company": ("(компания)",),
            "debt_pen": ("Просрочка",), "debt_bonus": ("низкая просрочка",), "new_clients": ("Новые клиенты",),
            "rank": ("рейтинге",), "discount": ("Лишние скидки",), "timesheet": ("Штрафы по табелю",),
            "mult": ("Множитель",), "mult_debt": ("Множитель",), "limits": ("Потолок премии", "гарантированного"),
            "seniority": ("За стаж",)}.get(t)
    if t in ("fixed", "discipline"):
        return name == item["p"].get("name")
    if t in ("piece", "cut"):
        u = item["v"]
        return name == (("Удержание: " if UNITS.get(u, ("", (), 0, False))[3] else "") + UNITS.get(u, (u,))[0])
    if t in METRIC_BLOCKS:
        pct_t = {"sales": "% от продаж", "focus": "% от фокусных", "collect": "% от собранных денег"}.get(t)
        bonus_t = {"sales": "Бонус: продажи", "focus": "Бонус: фокусные", "collect": "Бонус: сбор", "akb": "Бонус: АКБ",
                   "sku": "Бонус: sku", "visits": "Бонус: визиты"}[t]
        if "(компания)" in name:
            return False
        return (item["v"].startswith("pct") and pct_t and name.startswith(pct_t)) or \
            (not item["v"].startswith("pct") and name.startswith(bonus_t))
    if t == "margin":
        return name.startswith("% от наценки")
    if t == "mult":
        return name.startswith("Множитель") and item["v"] in ("bonus", "percent", "variable") and \
            {"bonus": "к бонусам", "percent": "к процентам", "variable": "к премии"}[item["v"]] in name
    return bool(keys) and any(k in name for k in keys)


HINT = {"money": "Сумма, например 25 000", "pct": "Процент, например 0,4 (это 0,4 %) или 80 (это 80 %)",
        "int": "Число, например 60", "text": "Название, например «Компенсация связи»",
        "steps_money": "Ступени через точку с запятой: «80 5000; 100 15000; 120 20000» — процент и сумма",
        "steps_coef": "Ступени: «90 0,8; 100 1; 110 1,2» — процент и множитель",
        "steps_rate": "Ступени: «80 0,3; 100 0,4; 120 0,6» — выполнение плана в % и ставка в %",
        "amounts": "Суммы через пробел за 1-е, 2-е, 3-е место: «5000 3000 1000»"}


def _parse_num(t):
    t = t.replace(" ", "").replace(" ", "").replace(",", ".").replace("₽", "").replace("%", "")
    return float(t)


def _parse_pct(t):
    x = _parse_num(t)
    return x / 100


def _pairs(text, second_pct=False):
    """«80 5000; 100 15 000; 120 20000» → [[0.8, 5000], …]; первое число в строке — процент, остальное — сумма или множитель."""
    out = []
    for part in re.split(r"[;\n]+", text):
        tokens = [t for t in re.split(r"[\s→:=]+", part.replace("%", " ").replace("×", " ").replace("₽", " ").strip()) if t]
        if len(tokens) < 2:
            continue
        a = _parse_num(tokens[0]) / 100
        b = _parse_num(tokens[1] if second_pct else "".join(tokens[1:]))
        out.append([a, b])
    if not out:
        raise ValueError
    return sorted(out)



# ---------- экраны: галочки и «✅ ОК / ✖️ Отмена» (Адам 31.10: никуда не уносит, назад — только ОК или Отмена) ----------
# Схема: pb[:должность]. Выбор блоков: pbs → галочки pbs:t:<блок> → pbs:ok / pbs:no.
# Блок: pbe:<блок> → варианты галочками pbe:<блок>:t:<вариант>, цифры pbe:<блок>:f:<вариант>:<поле>,
# выбор из списка pbe:<блок>:c:<вариант>:<поле>[:<значение>] → pbe:<блок>:ok / pbe:<блок>:no.
# Черновики — в сессии (pb_sel, pb_blk), до «ОК» схема не меняется.

ORDER = sorted(BRICKS, key=lambda b: (SECTIONS.index(BRICKS[b][0]) if BRICKS[b][0] in SECTIONS else 99, list(BRICKS).index(b)))


def _sort(items):
    """Порядок в схеме: как в каталоге; множители и пределы — в конце (так их и считает движок)."""
    return sorted(items, key=lambda it: (BRICKS[it["b"]][0] == "Множители и пределы", ORDER.index(it["b"])))


def _blocks_for(pos):
    return [bid for bid in ORDER if pos in BRICKS[bid][3] and variants(bid, pos)]


@screen("pb")
def page(ctx, pos=None):
    if not _may(ctx):
        return Reply(text="Конструктор зарплаты — у руководителя и администратора.")
    pos = cur_pos(ctx, pos)
    ctx.session.pop("pb_sel", None)
    ctx.session.pop("pb_blk", None)
    ctx.save()
    items = items_of(ctx, pos)
    title, icon, *_ = POSITIONS[pos]
    emp, rs = example(ctx, pos, items)
    total = round(max(sum(r[1] for r in rs), 0)) if rs else 0
    fixed = sum(r[1] for r in rs if r[0] == "Оклад")
    v = View("🧱 Конструктор зарплаты", f"{icon} {title}")
    if emp:
        kpi_note = ""
        if pos in SALES:
            m = ctx.kpi().emp(emp["id"])["sales"]
            kpi_note = f" · план {_pct(m.pct or 0, 0)}" if m.plan else ""
        v.tiles([("Пример за месяц", money(total), "good"), ("Оклад", money(fixed), None),
                 ("Сверх оклада", money(total - fixed), "good" if total - fixed > 0 else None)])
        v.note(f"Пример: {emp['fio']}{kpi_note} — по цифрам месяца этой фирмы.")
    cards, used = [], []
    for bid in ORDER:
        mine = [it for it in items if it["b"] == bid]
        if not mine:
            continue
        used.append(bid)
        b = BRICKS[bid]
        got = [r for r in rs for it in mine if _belongs(it, r)] if rs else []
        val = sum(r[1] for r in got)
        lines = [summary(pos, it) for it in mine] + [f"{r[0]}: {r[2]}" for r in got[:3] if r[2]]
        cards.append(Card(f"{b[1]} {b[2]}", money(val) if got else "", lines,
                          color="bad" if got and val < 0 else "good" if got and val > 0 else None))
    if cards:
        v.cards(cards, title="Из чего складывается")
    else:
        v.note("Схема пустая. Нажмите «☑️ Выбрать блоки» и отметьте галочками, что есть у вас.")
    other = ((ctx.session.get("pay") or {}).get("other") or "").strip()
    if other:
        v.note(f"✍️ По-своему: {other} — посмотрим при подключении.")
    kb = [[Button("☑️ Выбрать блоки", "pbs")]]
    kb += rows([Button(f"⚙️ {BRICKS[bid][2]}", f"pbe:{bid}") for bid in used], 2)
    tail = [Button("↺ Как было", "pbr"), Button("🧹 С нуля", "pbx")]
    if ctx.demo:
        tail.append(Button("✍️ У нас по-другому", "sw:py:other"))
    kb.append(tail)
    if ctx.demo:
        kb.append([Button("📋 В анкету", "cd")])
    top = Reply(text="Для кого схема:", buttons=[[Button(("● " if p == pos else "") + POSITIONS[p][1] + " " + POSITIONS[p][0], f"pb:{p}")
                                                  for p in POSITIONS]])
    return [top, Reply(view=v, buttons=kb)]


# ----- выбор блоков галочками -----

@screen("pbs")
def select(ctx, act=None, bid=None):
    if not _may(ctx):
        return page(ctx)
    pos = cur_pos(ctx)
    avail = _blocks_for(pos)
    sel = ctx.session.get("pb_sel")
    if sel is None or act is None:
        sel = sorted({it["b"] for it in items_of(ctx, pos)}, key=ORDER.index)
    if act == "t" and bid in avail:
        sel = [x for x in sel if x != bid] if bid in sel else sorted(sel + [bid], key=ORDER.index)
    if act == "no":
        return page(ctx)
    if act == "ok":
        items = items_of(ctx, pos)
        keep = [it for it in items if it["b"] in sel]
        for b in sel:
            if not any(it["b"] == b for it in keep):
                keep.append(brick(b, variants(b, pos)[0][0]))
        _save(ctx, pos, _sort(keep))
        return page(ctx)
    ctx.session["pb_sel"] = sel
    ctx.save()
    v = View("☑️ Выбрать блоки", f"{POSITIONS[pos][1]} {POSITIONS[pos][0]}")
    v.note("Отметьте, что есть у вас, и нажмите «ОК». Чего у фирмы нет — не отмечайте. Цифры — потом, внутри каждого блока.")
    kb = [[Button(f"{'☑' if b in sel else '☐'} {BRICKS[b][1]} {BRICKS[b][2]}", f"pbs:t:{b}")] for b in avail]
    kb.append([Button("✅ ОК", "pbs:ok"), Button("✖️ Отмена", "pbs:no")])
    return Reply(view=v, buttons=kb)


# ----- блок: варианты галочками и цифры -----

def _draft(ctx, bid, fresh=False):
    pos = cur_pos(ctx)
    d = ctx.session.get("pb_blk")
    if fresh or not d or d.get("b") != bid:
        d = {"b": bid, "items": copy.deepcopy([it for it in items_of(ctx, pos) if it["b"] == bid])}
        ctx.session["pb_blk"] = d
        ctx.save()
    return d


def _short(item):
    if item["b"] in ("piece", "cut"):
        return UNITS.get(item["v"], (item["v"],))[0]
    var = next((x for x in variants(item["b"]) if x[0] == item["v"]), None)
    return var[1] if var else item["v"]


@screen("pbe")
def block(ctx, bid=None, act=None, var=None, field=None, val=None):
    if not _may(ctx) or bid not in BRICKS:
        return page(ctx)
    pos = cur_pos(ctx)
    if act is None:
        d = _draft(ctx, bid, fresh=True)
    else:
        d = _draft(ctx, bid)
    vs = variants(bid, pos)
    if act == "no":
        return page(ctx)
    if act == "ok":
        items = [it for it in items_of(ctx, pos) if it["b"] != bid] + d["items"]
        _save(ctx, pos, _sort(items))
        return page(ctx)
    if act == "t" and any(x[0] == var for x in vs):
        have = [it for it in d["items"] if it["v"] == var]
        if have:
            d["items"] = [it for it in d["items"] if it["v"] != var]
        else:
            g = group(bid, var)
            d["items"] = [it for it in d["items"] if group(bid, it["v"]) != g] + [brick(bid, var)]
        d["items"].sort(key=lambda it: [x[0] for x in vs].index(it["v"]) if any(x[0] == it["v"] for x in vs) else 99)
        ctx.session["pb_blk"] = d
        ctx.save()
    if act == "f":
        it = next((x for x in d["items"] if x["v"] == var), None)
        if it and field in it["p"]:
            kind = F[field][1]
            hint = HINT.get(kind, "")
            if field == "debt_steps":
                hint = "Ступени: «5 1000; 10 3000» — доля просрочки в % и сумма"
            if bid == "mult_debt" and field == "coefs":
                hint = "Ступени: «10 0,9; 20 0,7» — просрочка выше % → множитель"
            return ctx.ask("pb_field", f"{_short(it)} — {F[field][0].lower()}: сейчас {fmt(field, it['p'][field])}.\n{hint}",
                           {"b": bid, "v": var, "f": field}, buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])
    if act == "c":
        it = next((x for x in d["items"] if x["v"] == var), None)
        kind = F.get(field, ("", ""))[1]
        opts = METRICS if kind == "metric" else SCOPES if kind == "scope" else {}
        if it and opts:
            if val in opts:
                it["p"][field] = val
                ctx.session["pb_blk"] = d
                ctx.save()
            else:
                return Reply(text=f"{_short(it)} — {F[field][0].lower()}:",
                             buttons=[[Button(("● " if it["p"].get(field) == k else "") + t, f"pbe:{bid}:c:{var}:{field}:{k}")
                                       for k, t in opts.items()], [Button("✖️ Отмена", f"pbe:{bid}:back")]])
    b = BRICKS[bid]
    v = View(f"{b[1]} {b[2]}", f"{POSITIONS[pos][0]} · {b[5]}")
    chosen = {it["v"] for it in d["items"]}
    multi = bid in ("piece", "cut")
    if bid in METRIC_BLOCKS and any(not x[0].startswith("pct") for x in vs) and any(x[0].startswith("pct") for x in vs):
        v.note("Отметьте один вид процента и/или один вид бонуса за план — можно оба, можно ничего. Цифры — кнопками ✏️ ниже.")
    elif multi:
        v.note("Отметьте, за что платить, — можно несколько. Цену каждого — кнопками ✏️ ниже.")
    else:
        v.note("Отметьте вид. Цифры — кнопками ✏️ ниже.")
    emp, rs = None, []
    if d["items"]:
        emp, rs = example(ctx, pos, _sort([it for it in items_of(ctx, pos) if it["b"] != bid] + d["items"]))
        got = [r for r in rs for it in d["items"] if _belongs(it, r)]
        if got and emp:
            v.lines([f"{r[0]}: {money(r[1])} — {r[2]}" for r in got], title=f"В примере ({emp['fio']})")
    else:
        v.note("Ничего не отмечено — после «✅ ОК» блок уберётся из схемы.")
    kb = [[Button(f"{'☑' if x[0] in chosen else '☐'} {x[1]}", f"pbe:{bid}:t:{x[0]}")] for x in vs]
    for it in d["items"]:
        if multi:
            pre = UNITS.get(it["v"], (it["v"],))[0] + " · "
        elif bid in METRIC_BLOCKS:
            pre = ("Процент · " if it["v"].startswith("pct") else "Бонус · ")
        else:
            pre = ""
        for k, val_ in it["p"].items():
            kind = F[k][1]
            label = pre + (F[k][0].lower() if pre else F[k][0]) + f": {fmt(k, val_)}"
            to = f"pbe:{bid}:{'c' if kind in ('metric', 'scope') else 'f'}:{it['v']}:{k}"
            kb.append([Button(("▸ " if kind in ("metric", "scope") else "✏️ ") + label, to)])
    kb.append([Button("✅ ОК", f"pbe:{bid}:ok"), Button("✖️ Отмена", f"pbe:{bid}:no")])
    return Reply(view=v, buttons=kb)


@text_input("pb_field")
def on_field(ctx, text, data):
    bid, var, field = data.get("b"), data.get("v"), data.get("f", "")
    if bid not in BRICKS:
        return page(ctx)
    d = _draft(ctx, bid)
    it = next((x for x in d["items"] if x["v"] == var), None)
    if not it or field not in F:
        return block(ctx, bid, "back")
    kind = F[field][1]
    try:
        if kind == "money":
            val = _parse_num(text)
        elif kind == "pct":
            val = _parse_pct(text)
        elif kind == "int":
            val = int(_parse_num(text))
        elif kind == "text":
            val = text.strip()[:40]
            if not val:
                raise ValueError
        elif kind in ("steps_money", "steps_coef"):
            val = _pairs(text, second_pct=kind == "steps_coef")
        elif kind == "steps_rate":
            val = [[a, b / 100] for a, b in _pairs(text, second_pct=True)]
        elif kind == "amounts":
            val = [_parse_num(x) for x in re.split(r"[\s;/]+", text.strip()) if x]
            if not val:
                raise ValueError
        else:
            raise ValueError
        if isinstance(val, float) and val < 0:
            raise ValueError
    except (ValueError, IndexError):
        return ctx.ask("pb_field", f"Не понял. {HINT.get(kind, '')}", data, buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])
    it["p"][field] = val
    ctx.session["pb_blk"] = d
    ctx.save()
    return block(ctx, bid, "back")


@screen("pbr")
def reset(ctx):
    pos = cur_pos(ctx)
    _save(ctx, pos, copy.deepcopy(DEFAULTS[pos]))
    return page(ctx)


@screen("pbx")
def clear(ctx):
    """Собрать с нуля: у фирмы может не быть многих показателей — схема из того, что есть."""
    pos = cur_pos(ctx)
    _save(ctx, pos, [])
    return page(ctx)
