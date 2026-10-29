"""Анкета клиента в виде таблицы (Google-таблица или Excel) ⇄ config.yaml.

Человек заполняет таблицу по-русски, с выпадающими списками; бот переводит её в config.yaml
и проверяет. Ошибки возвращаются с листом и строкой: «Зарплата, строка 4: …».

    tables = read_xlsx("анкета.xlsx")            # или read_gsheet(ссылка, ключ)
    raw, problems = to_raw(tables)               # перевод
    cfg, problems = check(tables, hooks)         # перевод + полная проверка анкеты
    tables = from_raw(raw)                       # обратно: config.yaml → листы (для шаблона)

Коды отделов, команд и должностей: колонка «Код» (можно оставить пустой — код сделается из названия).
Переименовали отдел у работающего клиента — оставьте прежний код, иначе сотрудников придётся перепривязать.
"""
import re

from core.access import editions as E
from core.access import roles as R
from core.config import INDUSTRIES, ConfigError, parse
from core.pay.components import UNIT_TITLES

# ---------- словари «как пишет человек» → «как понимает бот» ----------

INDUSTRY_RU = {v: k for k, v in INDUSTRIES.items()}
EDITION_RU = {E.EDITIONS[k]["title"]: k for k in E.ORDER}
MODULE_RU = {t: k for k, (t, _) in E.MODULES.items()}
DAYS_RU = {"Понедельник": "mon", "Вторник": "tue", "Среда": "wed", "Четверг": "thu", "Пятница": "fri",
           "Суббота": "sat", "Воскресенье": "sun"}
KIND_RU = {"Продажи": "sales", "Склад": "warehouse", "Логистика": "logistics", "Закупки": "purchasing", "Офис": "office"}
ROLE_RU = {"Менеджер": "rep", "Супервайзер": "sv", "Начальник отдела": "head", "Руководитель компании": "owner",
           "Бухгалтер": "accountant", "Сотрудник": "staff", "Новичок": "newbie"}
CUBE_RU = {"Оклад": ("salary", None), "% от продаж": ("sales_percent", None),
           "Бонус за план — ступени": ("plan_bonus", "steps"), "Бонус за план — пропорционально": ("plan_bonus", "linear"),
           "Фикс-бонус": ("fixed_bonus", None), "Удержание за просрочку": ("debt_penalty", None),
           "Удержание за просрочку — % от неё": ("debt_penalty", "percent"),
           "Удержание за просрочку — ступени": ("debt_penalty", "steps"),
           "Бонус за низкую просрочку": ("debt_bonus", None), "Бонус за низкую просрочку — ступени": ("debt_bonus", "steps"),
           "Штрафы табеля": ("timesheet", None), "Сдельно за единицу": ("per_unit", None),
           "Удержание за единицу": ("per_unit", "cut")}
METRIC_RU = {"Продажи": "sales", "АКБ": "akb", "SKU": "sku", "Сбор денег": "collect", "Фокусные группы": "focus",
             "Визиты с заказом": "visits"}
UNIT_RU = {t: k for k, (t, _) in UNIT_TITLES.items()}
DEBT_OF_RU = {"от продаж": "sales", "от продаж за 30 дней": "30d", "от долга": "debt"}
DEBT_BASE_RU = {"Продажи месяца": "month", "Продажи за 30 дней": "30d", "Весь долг": "debt"}
DEBT_EXAMPLES = ["Просрочка 30+", "Просрочка 60+", "Просрочка 90+", "Просрочка 60+ от долга", "Просрочка 30+ от продаж за 30 дней"]
WHOSE_RU = {"Свои": "self", "Команды": "team", "Отдела": "dept"}
SOURCE_RU = {"Почта": "mail", "Google Диск": "drive", "Выдуманные (показ)": "seed"}
FORMULA_RU = {"Продажи": "sales", "АКБ": "akb", "SKU": "sku", "Сбор": "collect", "Фокус": "focus", "Визиты": "visits",
              "Просрочка": "overdue"}
YES = {"да", "yes", "+", "1", "true"}


def _inv(d):
    return {v: k for k, v in d.items()}


# ---------- листы ----------

COMPANY = [
    # (подпись, путь в анкете, вид, подсказка)
    ("Код клиента", "id", "text", "латиницей, без пробелов: stroymarket"),
    ("Название компании", "company.name", "text", ""),
    ("Отрасль", "company.industry", "industry", "выбор из списка"),
    ("Часовой пояс", "company.timezone", "text", "обычно Europe/Moscow"),
    ("Редакция", "edition", "edition", "Старт / Бизнес / Контроль"),
    ("Доп. модули", "addons", "modules", "сверх редакции, через запятую; пусто — нет"),
    ("Выходные дни", "work_week.weekend", "days", "через запятую: Воскресенье или Пятница"),
    ("Делитель оклада, дней", "work_week.salary_days", "int", "оклад ÷ это число × отработанные дни; обычно 26"),
    ("Порог АКБ, ₽", "kpi.akb_min", "money", "клиент входит в АКБ, если купил за месяц на эту сумму"),
    ("Фокусных групп", "kpi.focus_groups", "int", "сколько групп товаров в фокусе месяца"),
    ("Пороги просрочки, дней", "kpi.debt_days", "ints", "через запятую: 30, 60; главный — последний"),
    ("Норма просрочки", "kpi.debt_norm", "pct", "доля просрочки, выше — красная зона и «собрать до нормы»; обычно 10%"),
    ("База нормы просрочки", "kpi.debt_base", "debtbase", "Продажи месяца / Продажи за 30 дней / Весь долг"),
    ("Штраф за прогул, ₽", "timesheet.absent_fine", "money", ""),
    ("Утренняя рассылка", "schedule.morning", "text", "время 08:00; пусто — не слать"),
    ("Вечерняя рассылка", "schedule.evening", "text", "время 19:30; пусто — не слать"),
    ("Откуда отчёты 1С", "source.kind", "source", "Почта / Google Диск"),
    ("Оклад по умолчанию, ₽", "pay.salary", "money", "если в схеме не указан"),
]

HEADS = {
    "Компания": ["Параметр", "Значение", "Подсказка"],
    "Отделы": ["Отдел", "Вид", "Код"],
    "Команды": ["Команда", "Отдел", "Линия", "Код"],
    "Должности": ["Должность", "Отдел", "Роль", "Код"],
    "Зарплата": ["Должность", "Кубик", "Показатель", "Чей", "Значение", "Порог", "Потолок", "Сверх плана",
                 "Минус за пропуск", "Название"],
    "Опоздания": ["Опоздание от, мин", "Штраф, ₽"],
    "Праздники": ["Дата", "Оплачивается"],
    "Формулы": ["Показатель", "Формула", "Единица", "Код"],
    "Роли": ["Роль", "Как называют в компании"],
}

# что предлагать в выпадающих списках: лист → {колонка: варианты}
CHOICES = {
    "Отделы": {"Вид": list(KIND_RU)},
    "Должности": {"Роль": list(ROLE_RU)},
    "Зарплата": {"Кубик": list(CUBE_RU), "Показатель": list(METRIC_RU) + list(UNIT_RU) + DEBT_EXAMPLES, "Чей": list(WHOSE_RU)},
    "Праздники": {"Оплачивается": ["Да", "Нет"]},
    "Формулы": {"Единица": ["₽", "%", "шт."]},
    "Роли": {"Роль": list(ROLE_RU)},
}
COMPANY_CHOICES = {"industry": list(INDUSTRY_RU), "edition": list(EDITION_RU), "source": list(SOURCE_RU),
                   "debtbase": list(DEBT_BASE_RU)}

GUIDE = [
    "Как заполнять анкету",
    "• Листы по порядку: Компания → Отделы → Команды → Должности → Зарплата. Остальные — по необходимости.",
    "• Где есть выпадающий список — выбирайте из него.",
    "• Проценты — со знаком: 0,4%  80%  10%. Суммы — как удобно: 25 000.",
    "• Зарплата: одна строка — один кубик. У должности может быть сколько угодно строк; нет строк — зарплату бот не считает.",
    "• Бонус за план — ступени: в «Значение» пишем ступени через точку с запятой: 80% → 5 000; 100% → 15 000; 120% → 20 000.",
    "• Бонус за план — пропорционально: «Значение» — сумма за 100% плана, «Порог» — с какого % платим, «Потолок» — выше не платим.",
    "• % от продаж: «Порог» — платить, только если план выполнен на столько; «Сверх плана» — другая ставка на продажи сверх плана.",
    "• Чей: Свои — по своим продажам, Команды — по продажам команды (супервайзеру), Отдела — по всему отделу.",
    "• Сдельно за единицу: «Показатель» — единица (км, точки доставки…), «Значение» — сумма за единицу: 4; для единиц в ₽ — процент: 3%.",
    "• Удержание за единицу — то же со знаком минус: например, 500 за каждую ошибку в возвратах.",
    "• Просрочка: «Показатель» — «Просрочка 60+» (порог в днях) и, если нужно, «от долга» / «от продаж за 30 дней».",
    "  Удержание: фикс (Значение — сумма, Порог — доля), % от неё (Значение — 5%, Потолок — не больше суммы), ступени (5% → 1 000).",
    "  Бонус за низкую просрочку: фикс (Значение — сумма, Порог — не больше доли) или ступени (3% → 5 000; 5% → 3 000).",
    "• Колонки «Код» можно не трогать — бот заполнит сам.",
]


# ---------- id из названия ----------

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(s):
    s = "".join(_TR.get(ch, ch) for ch in str(s).lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:24] or "x"


# ---------- таблица → анкета ----------

def _rows(tables, sheet):
    """Строки листа как словари по заголовкам + номер строки в таблице (для ошибок). Пустые пропускаются."""
    rows = tables.get(sheet) or []
    if not rows:
        return []
    head = [str(h).strip() for h in rows[0]]
    out = []
    for n, r in enumerate(rows[1:], start=2):
        d = {h: (str(r[i]).strip() if i < len(r) and r[i] is not None else "") for i, h in enumerate(head) if h}
        if any(v for k, v in d.items() if k not in ("Код", "Подсказка")):
            out.append((n, d))
    return out


def _put(raw, path, value):
    cur = raw
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _list(v):
    return [x.strip() for x in re.split(r"[,;\n]", v) if x.strip()]


def to_raw(tables):
    """Листы → словарь анкеты (как config.yaml) + проблемы [(лист, строка, текст)] и карта мест для ошибок."""
    raw, p, where = {}, [], {}

    def bad(sheet, row, text):
        p.append((sheet, row, text))

    def pick(d, value, sheet, row, what):
        if not value:
            return None
        hit = next((v for k, v in d.items() if k.lower() == value.lower()), None)
        if hit is None:
            bad(sheet, row, f"{what} «{value}» — выберите из списка: {', '.join(d)}")
        return hit

    # Компания
    params = {d.get("Параметр", ""): (n, d.get("Значение", "")) for n, d in _rows(tables, "Компания")}
    for label, path, kind, _ in COMPANY:
        n, v = params.get(label, (0, ""))
        where[path] = ("Компания", n, label)
        if not v:
            continue
        if kind == "industry":
            v = pick(INDUSTRY_RU, v, "Компания", n, "Отрасль")
        elif kind == "edition":
            v = pick(EDITION_RU, v, "Компания", n, "Редакция")
        elif kind == "source":
            v = pick(SOURCE_RU, v, "Компания", n, "Откуда отчёты")
        elif kind == "debtbase":
            v = pick(DEBT_BASE_RU, v, "Компания", n, "База нормы просрочки")
        elif kind == "modules":
            v = [pick(MODULE_RU, x, "Компания", n, "Модуль") for x in _list(v)]
        elif kind == "days":
            v = [pick(DAYS_RU, x, "Компания", n, "День") for x in _list(v)]
        elif kind in ("int", "ints"):
            try:
                xs = [int(float(x.replace(" ", "").replace(",", "."))) for x in _list(v)]
                v = xs if kind == "ints" else xs[0]
            except ValueError:
                bad("Компания", n, f"{label} «{v}» — нужно целое число")
                continue
        if v is not None and v != [None]:
            _put(raw, path, [x for x in v if x] if isinstance(v, list) else v)
    raw.setdefault("company", {}).setdefault("timezone", "Europe/Moscow")
    raw["id"] = raw.get("id") or ""

    # Отделы
    depts, dept_by_name = [], {}
    for n, d in _rows(tables, "Отделы"):
        did = d.get("Код") or slug(d.get("Отдел"))
        kind = pick(KIND_RU, d.get("Вид", ""), "Отделы", n, "Вид отдела")
        depts.append({"id": did, "name": d.get("Отдел", ""), "kind": kind or ""})
        dept_by_name[d.get("Отдел", "").lower()] = did
        where[f"dept:{did}"] = ("Отделы", n, d.get("Отдел", ""))
    raw["departments"] = depts

    def dept_of(name, sheet, n):
        if not name:
            bad(sheet, n, "не указан отдел")
            return ""
        did = dept_by_name.get(name.lower())
        if did is None:
            bad(sheet, n, f"отдел «{name}» — нет такого на листе «Отделы»")
            return ""
        return did

    # Команды и линии
    teams, lines = [], {}
    company = (raw.get("company") or {}).get("name", "") or "Основная линия"
    for n, d in _rows(tables, "Команды"):
        tid = d.get("Код") or slug(d.get("Команда"))
        line_name = d.get("Линия") or company
        lid = slug(line_name) if d.get("Линия") else "main"
        lines.setdefault(lid, {"name": line_name})
        teams.append({"id": tid, "name": d.get("Команда", ""), "dept": dept_of(d.get("Отдел", ""), "Команды", n), "line": lid})
        where[f"team:{tid}"] = ("Команды", n, d.get("Команда", ""))
    raw["teams"] = teams
    raw["lines"] = lines or {"main": {"name": company}}

    # Должности
    positions, pos_by_name = [], {}
    for n, d in _rows(tables, "Должности"):
        pid = d.get("Код") or slug(d.get("Должность"))
        role = pick(ROLE_RU, d.get("Роль", ""), "Должности", n, "Роль")
        positions.append({"id": pid, "name": d.get("Должность", ""), "dept": dept_of(d.get("Отдел", ""), "Должности", n),
                          "role": role or "", "pay": "none"})
        pos_by_name[d.get("Должность", "").lower()] = pid
        where[f"pos:{pid}"] = ("Должности", n, d.get("Должность", ""))
    raw["positions"] = positions

    # Зарплата: строки → схемы, схема = должность
    schemes = {}
    for n, d in _rows(tables, "Зарплата"):
        pid = pos_by_name.get(d.get("Должность", "").lower())
        if pid is None:
            bad("Зарплата", n, f"должность «{d.get('Должность')}» — нет такой на листе «Должности»")
            continue
        cube = next((v for k, v in CUBE_RU.items() if k.lower() == d.get("Кубик", "").lower()), None)
        if cube is None:
            bad("Зарплата", n, f"кубик «{d.get('Кубик')}» — выберите из списка: {', '.join(CUBE_RU)}")
            continue
        t, mode = cube
        c = {"type": t}
        if mode and t == "per_unit":
            c["cut"] = True
        elif mode:
            c["mode"] = mode
        if t == "plan_bonus":
            mt = d.get("Показатель", "")
            m = METRIC_RU.get(next((k for k in METRIC_RU if k.lower() == mt.lower()), ""), None)
            if m is None and mt.lower().startswith("группа "):
                m = "grp:" + mt.split(maxsplit=1)[1].strip()
            if m is None:
                bad("Зарплата", n, f"показатель «{mt}» — выберите из списка или «Группа <код>»")
                continue
            c["metric"] = m
        if t == "per_unit":
            mt = d.get("Показатель", "")
            u = UNIT_RU.get(next((k for k in UNIT_RU if k.lower() == mt.lower()), ""), None)
            if u is None:
                bad("Зарплата", n, f"единица «{mt}» — выберите из списка: {', '.join(UNIT_RU)}")
                continue
            c["unit"] = u
        if t in ("debt_penalty", "debt_bonus"):
            mt = d.get("Показатель", "").strip().lower()
            if mt:
                mm = re.match(r"просрочка\s*(\d+)\s*\+?\s*(.*)$", mt)
                if not mm or (mm.group(2) and mm.group(2).strip() not in DEBT_OF_RU):
                    bad("Зарплата", n, f"показатель «{d.get('Показатель')}» — пишите так: {', '.join(DEBT_EXAMPLES)}")
                    continue
                c["days"] = int(mm.group(1))
                if mm.group(2).strip():
                    c["of"] = DEBT_OF_RU[mm.group(2).strip()]
        whose = d.get("Чей", "")
        if whose and t in ("sales_percent", "plan_bonus", "debt_penalty", "debt_bonus"):
            s = pick(WHOSE_RU, whose, "Зарплата", n, "Чей")
            if s and s != "self":
                c["scope"] = s
        val = d.get("Значение", "")
        if t == "salary" and val:
            c["amount"] = val
        elif t == "sales_percent":
            c["rate"] = val
            if d.get("Порог"):
                c["min_plan"] = d["Порог"]
            if d.get("Сверх плана"):
                c["rate_above"] = d["Сверх плана"]
        elif t == "plan_bonus" and mode == "steps":
            steps = []
            for part in re.split(r"[;\n]", val):
                if not part.strip():
                    continue
                mm = re.match(r"\s*([\d\s,.]+%)\s*(?:→|->|=|:|—|-)\s*(.+)$", part)
                if not mm:
                    bad("Зарплата", n, f"ступень «{part.strip()}» — пишите так: 80% → 5 000")
                    continue
                steps.append([mm.group(1).replace(" ", ""), mm.group(2).strip()])
            c["steps"] = steps
        elif t == "plan_bonus":
            c["amount"] = val
            if d.get("Порог"):
                c["min"] = d["Порог"]
            if d.get("Потолок"):
                c["cap"] = d["Потолок"]
        elif t == "fixed_bonus":
            c["amount"] = val
            c["name"] = d.get("Название") or "Бонус"
            if d.get("Минус за пропуск"):
                c["cut_per_miss"] = d["Минус за пропуск"]
        elif t == "debt_penalty" and mode == "percent":
            c["rate"] = val
            if d.get("Порог"):
                c["min"] = d["Порог"]
            if d.get("Потолок"):
                c["max"] = d["Потолок"]
        elif t in ("debt_penalty", "debt_bonus") and mode == "steps":
            steps = []
            for part in re.split(r"[;\n]", val):
                if not part.strip():
                    continue
                mm = re.match(r"\s*([\d\s,.]+%)\s*(?:→|->|=|:|—|-)\s*(.+)$", part)
                if not mm:
                    bad("Зарплата", n, f"ступень «{part.strip()}» — пишите так: 5% → 1 000")
                    continue
                steps.append([mm.group(1).replace(" ", ""), mm.group(2).strip()])
            c["steps"] = steps
        elif t in ("debt_penalty", "debt_bonus"):
            c["amount"] = val
            if d.get("Порог"):
                c["share"] = d["Порог"]
            elif t == "debt_penalty":
                c["share"] = "10%"
        if t in ("debt_penalty", "debt_bonus") and d.get("Название"):
            c["name"] = d["Название"]
        elif t == "per_unit":
            c["rate" if str(val).strip().endswith("%") else "price"] = val
            if d.get("Название"):
                c["name"] = d["Название"]
        schemes.setdefault(pid, []).append(c)
        where[f"pay_schemes.{pid}[{len(schemes[pid])}]"] = ("Зарплата", n, "")
    for pos in positions:
        if pos["id"] in schemes:
            pos["pay"] = pos["id"]
    raw["pay_schemes"] = schemes

    late = []
    for n, d in _rows(tables, "Опоздания"):
        try:
            late.append([int(float(d.get("Опоздание от, мин", "0").replace(",", "."))), d.get("Штраф, ₽", "0")])
        except ValueError:
            bad("Опоздания", n, "минуты — целым числом")
    if late:
        raw.setdefault("timesheet", {})["late"] = late

    hol = {}
    for n, d in _rows(tables, "Праздники"):
        dt = d.get("Дата", "")
        mm = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})$", dt)
        if mm:
            dt = f"{mm.group(3)}-{int(mm.group(2)):02d}-{int(mm.group(1)):02d}"
        elif not re.match(r"\d{4}-\d\d-\d\d", dt[:10]):
            bad("Праздники", n, f"дата «{dt}» — пишите 04.11.2026")
            continue
        hol[dt[:10]] = "paid" if d.get("Оплачивается", "").lower() in YES else "unpaid"
    if hol:
        raw.setdefault("work_week", {})["holidays"] = hol

    formulas = {}
    for n, d in _rows(tables, "Формулы"):
        expr = d.get("Формула", "")
        for ru, code in sorted(FORMULA_RU.items(), key=lambda t: -len(t[0])):
            expr = re.sub(rf"\b{ru}\b", code, expr, flags=re.I)
        fid = d.get("Код") or slug(d.get("Показатель"))
        unit = d.get("Единица", "")
        formulas[fid] = {"name": d.get("Показатель", ""), "expr": expr, "unit": "" if unit == "шт." else unit}
        where[f"formulas.{fid}"] = ("Формулы", n, d.get("Показатель", ""))
    if formulas:
        raw["formulas"] = formulas

    titles = {}
    for n, d in _rows(tables, "Роли"):
        r = pick(ROLE_RU, d.get("Роль", ""), "Роли", n, "Роль")
        if r and d.get("Как называют в компании"):
            titles[r] = d["Как называют в компании"]
    if titles:
        raw["roles"] = titles
    raw.setdefault("edition", "start")
    raw.setdefault("source", {"kind": "mail"})
    return raw, p, where


def check(tables, hooks=None):
    """Полная проверка: (ClientConfig или None, [(лист, строка, текст)])."""
    raw, p, where = to_raw(tables)
    if not raw.get("id"):
        p.append(("Компания", where.get("id", ("", 0))[1], "не указан код клиента"))
        return None, p
    try:
        cfg = parse(raw["id"], raw, hooks)
    except ConfigError as e:
        seen = {(s, r) for s, r, _ in p if r}
        for x in e.problems:
            place = _place(x, where, raw)
            key = (place[0], place[1])
            if place[1] and key in seen:
                continue                     # на эту строку уже есть понятная ошибка — не повторяем
            seen.add(key)
            p.append(place)
        return None, p
    return (None if p else cfg), p


COLUMN_OF = {"rate": "Значение", "amount": "Значение", "steps": "Значение", "min_plan": "Порог", "min": "Порог",
             "share": "Порог", "cap": "Потолок", "rate_above": "Сверх плана", "cut_per_miss": "Минус за пропуск",
             "metric": "Показатель", "scope": "Чей", "mode": "Кубик"}


def _human_field(text):
    """«.rate «0,4» — …» / «sales_percent.rate — …» → «Значение «0,4» — …»."""
    m = re.match(r"\.?(?:[a-z_]+\.)?([a-z_]+)\b(.*)$", text, re.S)
    if m and m.group(1) in COLUMN_OF:
        return COLUMN_OF[m.group(1)] + m.group(2)
    return text


def _place(text, where, raw):
    """Ошибка проверки анкеты → (лист, строка, понятный текст)."""
    names = {}
    for kind, items in (("dept", raw.get("departments", [])), ("team", raw.get("teams", [])),
                        ("pos", raw.get("positions", []))):
        for x in items:
            names[x["id"]] = x["name"]
    m = re.match(r"(pay_schemes\.[\w]+\[\d+\])", text)
    if m and m.group(1) in where:
        sheet, row, _ = where[m.group(1)]
        return sheet, row, _human_field(text[len(m.group(1)):].lstrip(": "))
    for prefix, sheet_key in (("должность «", "pos:"), ("команда «", "team:"), ("отдел «", "dept:")):
        if text.startswith(prefix):
            ident = text[len(prefix):].split("»")[0]
            if f"{sheet_key}{ident}" in where:
                sheet, row, label = where[f"{sheet_key}{ident}"]
                return sheet, row, text.replace(f"«{ident}»", f"«{label}»")
    m = re.match(r"([\w.]+)", text)
    for path, (sheet, row, label) in where.items():
        if m and (m.group(1) == path or m.group(1).startswith(path + ".")):
            return sheet, row, f"{label}: " + text[len(m.group(1)):].lstrip(" :")
    for ident, nm in names.items():
        text = text.replace(f"«{ident}»", f"«{nm}»")
    return "", 0, text


def problems_text(p):
    return "\n".join(f"• {s}, строка {r}: {t}" if s and r else f"• {s + ': ' if s else ''}{t}" for s, r, t in p)


# ---------- анкета → таблица (шаблон, выгрузка работающего клиента) ----------

def from_raw(raw):
    """Анкета (config.yaml) → листы таблицы со всеми значениями по-русски."""
    inv = {"industry": _inv(INDUSTRY_RU), "edition": _inv(EDITION_RU), "source": _inv(SOURCE_RU),
           "debtbase": _inv(DEBT_BASE_RU)}
    t = {k: [list(v)] for k, v in HEADS.items()}

    def get(path):
        cur = raw
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return ""
            cur = cur[k]
        return cur

    for label, path, kind, hint in COMPANY:
        v = get(path)
        if kind in inv:
            v = inv[kind].get(v, v)
        elif kind == "modules":
            v = ", ".join(E.MODULES[m][0] for m in v or [] if m in E.MODULES)
        elif kind == "days":
            v = ", ".join(_inv(DAYS_RU).get(d, d) for d in v or [])
        elif kind == "ints":
            v = ", ".join(str(x) for x in v or [])
        elif kind == "pct":
            v = _pct(v)
        t["Компания"].append([label, _human(v), hint])
    kinds = _inv(KIND_RU)
    dname = {d["id"]: d["name"] for d in raw.get("departments", [])}
    for d in raw.get("departments", []):
        t["Отделы"].append([d["name"], kinds.get(d["kind"], d["kind"]), d["id"]])
    lines = raw.get("lines") or {}
    company = get("company.name")
    for x in raw.get("teams", []):
        ln = (lines.get(x.get("line")) or {}).get("name", "")
        t["Команды"].append([x["name"], dname.get(x["dept"], x["dept"]), "" if ln == company else ln, x["id"]])
    roles = _inv(ROLE_RU)
    pname = {}
    for p_ in raw.get("positions", []):
        t["Должности"].append([p_["name"], dname.get(p_["dept"], p_["dept"]), roles.get(p_["role"], p_["role"]), p_["id"]])
        pname[p_["id"]] = (p_["name"], p_.get("pay"))
    cubes = {v: k for k, v in CUBE_RU.items()}
    metrics, whose = _inv(METRIC_RU), _inv(WHOSE_RU)
    schemes = raw.get("pay_schemes") or {}
    for pid, (pn, scheme) in pname.items():
        for c in schemes.get(scheme, []) or []:
            ty = c["type"]
            key = (ty, c.get("mode", "steps") if ty == "plan_bonus" else ("cut" if c.get("cut") else None))
            if ty in ("debt_penalty", "debt_bonus"):
                key = (ty, None if c.get("mode", "fixed") == "fixed" else c["mode"])
            m = c.get("metric", "")
            shown = metrics.get(m, f"Группа {m[4:]}" if m.startswith("grp:") else "")
            if ty == "per_unit":
                shown = UNIT_TITLES.get(c.get("unit"), (c.get("unit", ""),))[0]
            if ty in ("debt_penalty", "debt_bonus") and (c.get("days") or c.get("of")):
                shown = f"Просрочка {c.get('days') or (get('kpi.debt_days') or [60])[-1]}+" + \
                    (f" {_inv(DEBT_OF_RU)[c['of']]}" if c.get("of") and c["of"] in _inv(DEBT_OF_RU) else "")
            row = [pn, cubes.get(key, ty), shown,
                   whose.get(c.get("scope", "self"), "") if ty in ("sales_percent", "plan_bonus", "debt_penalty", "debt_bonus") else "",
                "", "", "", "",
                   "", ""]
            if ty == "salary":
                row[4] = _human(c.get("amount", ""))
            elif ty == "sales_percent":
                row[4], row[5], row[7] = _pct(c.get("rate")), _pct(c.get("min_plan")), _pct(c.get("rate_above"))
            elif ty == "plan_bonus" and c.get("mode", "steps") == "steps":
                row[4] = "; ".join(f"{_pct(a)} → {_human(b)}" for a, b in c.get("steps", []))
            elif ty == "plan_bonus":
                row[4], row[5], row[6] = _human(c.get("amount")), _pct(c.get("min")), _pct(c.get("cap"))
            elif ty == "fixed_bonus":
                row[4], row[8], row[9] = _human(c.get("amount")), _human(c.get("cut_per_miss", "")), c.get("name", "")
            elif ty in ("debt_penalty", "debt_bonus") and c.get("mode") == "percent":
                row[4], row[5], row[6] = _pct(c.get("rate")), _pct(c.get("min")), _human(c.get("max", ""))
            elif ty in ("debt_penalty", "debt_bonus") and c.get("mode") == "steps":
                row[4] = "; ".join(f"{_pct(a)} → {_human(b)}" for a, b in c.get("steps", []))
            elif ty in ("debt_penalty", "debt_bonus"):
                row[4], row[5] = _human(c.get("amount")), _pct(c.get("share"))
            if ty in ("debt_penalty", "debt_bonus"):
                row[9] = c.get("name", "")
            elif ty == "per_unit":
                row[4] = _pct(c["rate"]) if "rate" in c else _human(c.get("price"))
                row[9] = c.get("name", "")
            t["Зарплата"].append(row)
    for mins, fine in (get("timesheet.late") or []):
        t["Опоздания"].append([mins, _human(fine)])
    for d, kind in (get("work_week.holidays") or {}).items():
        y, mo, dd = str(d).split("-")
        t["Праздники"].append([f"{dd}.{mo}.{y}", "Да" if kind == "paid" else "Нет"])
    back = _inv(FORMULA_RU)
    for fid, f in (raw.get("formulas") or {}).items():
        expr = f.get("expr", "")
        for code, ru in back.items():
            expr = re.sub(rf"\b{code}\b", ru, expr)
        t["Формулы"].append([f.get("name", fid), expr, f.get("unit", "") or "шт.", fid])
    for r, title in (raw.get("roles") or {}).items():
        if r in roles:
            t["Роли"].append([roles[r], title])
    return t


def _human(v):
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:,.0f}".replace(",", " ") if float(v).is_integer() else f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return str(v)


def _pct(v):
    if v in (None, ""):
        return ""
    if isinstance(v, str):
        return v
    return f"{v * 100:g}%".replace(".", ",")


# ---------- чтение и запись ----------

def read_xlsx(path_or_bytes):
    import io
    from openpyxl import load_workbook
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    wb = load_workbook(src, data_only=True)
    return {ws.title: [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)]
            for ws in wb.worksheets}


def read_gsheet(url_or_id, key_file):
    """Google-таблица по ссылке или id (нужен доступ сервисного аккаунта на чтение)."""
    import gspread
    gc = gspread.service_account(filename=key_file)
    m = re.search(r"/d/([\w-]+)", url_or_id)
    sh = gc.open_by_key(m.group(1) if m else url_or_id)
    return {ws.title: ws.get_all_values() for ws in sh.worksheets()}


def write_xlsx(tables, problems=None):
    """Таблицы → Excel с выпадающими списками, подсказками и листом «Как заполнять». Возвращает байты."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    guide = wb.active
    guide.title = "Как заполнять"
    for i, line in enumerate(GUIDE, 1):
        guide.cell(i, 1, line).font = Font(bold=(i == 1), size=14 if i == 1 else 11)
    guide.column_dimensions["A"].width = 120
    bad = {}
    for s, r, txt in problems or []:
        bad.setdefault(s, {}).setdefault(r, []).append(txt)
    for name, rows in tables.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E8EFFD")
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 60)
        head = rows[0]
        for col, opts in (CHOICES.get(name) or {}).items():
            if col in head:
                letter = ws.cell(1, head.index(col) + 1).column_letter
                dv = DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{letter}2:{letter}200")
        if name == "Компания":
            for i, (_, _, kind, _) in enumerate(COMPANY, start=2):
                if kind in COMPANY_CHOICES:
                    dv = DataValidation(type="list", formula1='"' + ",".join(COMPANY_CHOICES[kind]) + '"')
                    ws.add_data_validation(dv)
                    dv.add(f"B{i}")
        if name in bad:
            col = len(head) + 1
            ws.cell(1, col, "⚠️ Проверка").font = Font(bold=True, color="D64545")
            for r, msgs in bad[name].items():
                c = ws.cell(r, col, "; ".join(msgs))
                c.font = Font(color="D64545")
                c.alignment = Alignment(wrap_text=True)
            ws.column_dimensions[ws.cell(1, col).column_letter].width = 60
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
