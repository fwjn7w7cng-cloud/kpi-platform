"""Пакеты, готовые наборы («редакции») и модули платформы.

Бот собирается как конструктор — по отделам фирмы (решение Адама 30.10):
  📈 sales      Продажи — три уровня: start «Старт» / business «Бизнес» / control «Премиум»
  📦 stock      Склад и закупки
  🚚 logistics  Логистика (водители, диспетчер)
  👤 office     Кадры и офис
  ⭐ max        Максимум: свой бренд, прямая связь с 1С, филиалы, функции на заказ
Клиент берёт только нужные пакеты — на всю фирму или на часть отделов — и платит за пакет и за людей
в подключённых отделах (core/access/pricing.py). Размер фирмы на доступ не влияет, только на цену.
Готовые наборы PRESETS (их по привычке зовём редакциями) — чтобы не собирать с нуля.

Набор пакетов (``packages``) — словарь {пакет: значение}: у продаж — уровень, у остальных — True/False.
Ядро спрашивает ``has_module(cfg, "debts")`` и рисует замок с ценой пакета, если модуля нет.
"""

PACKAGES = {
    "sales":     {"title": "Продажи",          "icon": "📈", "kinds": {"sales"},
                  "what": "план и факт, рейтинг, зарплата торговых, долги клиентов, маршруты, планы месяца"},
    "stock":     {"title": "Склад и закупки",  "icon": "📦", "kinds": {"warehouse", "purchasing"},
                  "what": "остатки, что заказать, заказы поставщикам с согласованием, приёмка, возвраты и чья ошибка"},
    "logistics": {"title": "Логистика",        "icon": "🚚", "kinds": {"logistics"},
                  "what": "рейсы водителей, «Мой день», сдельная оплата, двойные и дальние рейсы, ГСМ"},
    "office":    {"title": "Кадры и офис",     "icon": "👤", "kinds": {"office"},
                  "what": "кабинеты, табель и зарплата офиса, приём / перевод / увольнение по чек-листу, приказы"},
    "max":       {"title": "Максимум",         "icon": "⭐", "kinds": set(),
                  "what": "свой бренд и адрес, прямая связь с 1С, филиалы, до 3 функций на заказ в месяц"},
}
LEVELS = {"start": "Старт", "business": "Бизнес", "control": "Премиум"}
LEVEL_ORDER = list(LEVELS)
DEPT_PACKAGE = {"sales": "sales", "warehouse": "stock", "purchasing": "stock", "logistics": "logistics", "office": "office"}

# Все модули: код → (название для людей, пакет, уровень продаж)
#   пакет "any" — есть при любом купленном пакете; "ops" — при любом из склада / логистики / офиса
MODULES = {
    "kpi":          ("KPI-ядро: план/факт, рейтинг, карточки, сводка", "sales", "start"),
    "reminders":    ("Напоминания и алерты", "any", None),
    "debts":        ("Дебиторка", "sales", "start"),
    "payroll":      ("Зарплата: табель, оклад, сдельная, ГСМ, бонусы, штрафы", "any", None),
    "achievements": ("Достижения и челленджи", "sales", "business"),
    "staff":        ("Кабинеты склада, водителей, офиса", "ops", None),
    "tasks":        ("Задачи от руководителя", "sales", "business"),
    "clients":      ("Клиенты: спящие, падают, растут", "sales", "business"),
    "route":        ("Маршруты торговых и отчёт за день", "sales", "business"),
    "quiz":         ("Тесты и обучение новичков", "sales", "business"),
    "plans":        ("Утверждение планов", "sales", "control"),
    "approval":     ("Утверждение зарплаты и ведомость", "sales", "control"),
    "audit":        ("Журнал изменений", "sales", "control"),
    "watchdog":     ("«Ночной сторож» аномалий", "sales", "control"),
    "forecast":     ("Прогноз плана и сравнение отделов", "sales", "control"),
    "call_today":   ("«Кому позвонить сегодня»", "sales", "control"),
    "formulas":     ("Свои формулы KPI", "sales", "control"),
    "stock":        ("Закупки: остатки, заказы поставщикам, согласование", "stock", None),
    "warehouse":    ("Склад: приёмка поставок, отгрузки, ошибки", "stock", None),
    "returns":      ("Возвраты и чья ошибка", "stock", None),
    "logistics":    ("Логистика: рейсы, «Мой день» водителя, сдельная оплата", "logistics", None),
    "fuel":         ("Контроль топлива против топливной карты", "logistics", None),
    "hr":           ("Кадры: приём, перевод, увольнение, приказы", "office", None),
    "branding":     ("Свой бренд: логотип и название на значке, свой адрес", "max", None),
    "branches":     ("Несколько филиалов и юрлиц в одном приложении", "max", None),
    "custom":       ("Функции на заказ: до 3 в месяц", "max", None),
}

# Готовые наборы. Каждый следующий включает предыдущий.
PRESETS = {
    "start":    {"title": "Старт",    "packages": {"sales": "start"},
                 "for": "небольшой отдел продаж: план, факт, зарплата, долги"},
    "business": {"title": "Бизнес",   "packages": {"sales": "business"},
                 "for": "продажи целиком: клиенты, маршруты, задачи, тесты"},
    "control":  {"title": "Премиум",  "packages": {"sales": "control"},
                 "for": "управление продажами: утверждение, прогноз, сторож, свои формулы"},
    "company":  {"title": "Компания", "packages": {"sales": "control", "stock": True, "logistics": True, "office": True},
                 "for": "вся фирма: продажи, склад, водители, кадры"},
    "max":      {"title": "Максимум", "packages": {"sales": "control", "stock": True, "logistics": True, "office": True, "max": True},
                 "for": "всё плюс свой бренд, филиалы и функции на заказ"},
}
# Готовый набор — под размер фирмы (Адам 31.10: «редакции — для размеров фирмы, а внутренние настройки — каждому
# индивидуально»): при выборе набора в демо люди по отделам ставятся типовые для такого размера, дальше правятся вручную.
# Набор задаёт, что стоит по умолчанию: людей, роли и функции; дальше в любом наборе можно включить что угодно.
_ALL_ROLES = {"owner", "admin", "rep", "sv", "head", "accountant", "staff"}
SIZES = {
    "start":    ("маленькая фирма · до 10 человек", {"sales": 6}, {"owner", "admin", "rep"},
                 "директор и торговые"),
    "business": ("небольшая · 10–25 человек", {"sales": 15}, {"owner", "admin", "rep", "sv"},
                 "плюс супервайзер; клиенты, маршруты, задачи, тесты"),
    "control":  ("растущий отдел продаж · 25–50 человек", {"sales": 30}, {"owner", "admin", "rep", "sv", "head", "accountant"},
                 "плюс руководитель продаж и бухгалтер; утверждение зарплаты, прогноз"),
    "company":  ("средняя фирма · 50–100 человек, все отделы", {"sales": 30, "stock": 12, "logistics": 15, "office": 6}, _ALL_ROLES,
                 "плюс склад, логистика, кадры и офис"),
    "max":      ("крупная · 100+ человек, филиалы", {"sales": 60, "stock": 25, "logistics": 30, "office": 12}, _ALL_ROLES,
                 "всё плюс свой бренд, филиалы, функции на заказ"),
}
for _k, (_size, _people, _roles, _what) in SIZES.items():
    PRESETS[_k]["size"] = _size
    PRESETS[_k]["people"] = {d: _people.get(d, 0) for d in ("sales", "stock", "logistics", "office")}
    PRESETS[_k]["roles"] = _roles
    PRESETS[_k]["what"] = _what
ORDER = list(PRESETS)
EDITIONS = PRESETS          # старое имя
CUSTOM = "custom"           # «свой набор» — не совпадает ни с одним готовым

# Модули, без которых другой модуль не работает (внутри пакетов выполняется само собой)
REQUIRES = {"approval": {"payroll"}, "audit": {"payroll"}, "call_today": {"debts"}, "fuel": {"logistics"}, "warehouse": {"stock"}}


def normalize(packages):
    """Любая запись набора → полный словарь: sales — уровень или None, остальные — True/False.
    Понимает «sales: true» (= Премиум), «stock: yes/да/1»."""
    packages = packages or {}
    out = {}
    for code in PACKAGES:
        v = packages.get(code)
        if code == "sales":
            if v in LEVELS:
                out[code] = v
            elif _yes(v):
                out[code] = "control"
            else:
                out[code] = None
        else:
            out[code] = _yes(v)
    return out


def _yes(v):
    if isinstance(v, str):
        return v.strip().lower() in ("да", "yes", "true", "1", "on", "control", "start", "business")
    return bool(v)


def modules_of(packages):
    """Какие модули даёт набор пакетов."""
    p = normalize(packages)
    bought = {c for c, v in p.items() if v}
    if not bought:
        return set()
    out = set()
    for code, (_, pkg, level) in MODULES.items():
        if pkg == "any":
            out.add(code)
        elif pkg == "ops":
            if bought & {"stock", "logistics", "office"}:
                out.add(code)
        elif pkg == "sales":
            if p["sales"] and LEVEL_ORDER.index(p["sales"]) >= LEVEL_ORDER.index(level):
                out.add(code)
        elif p.get(pkg):
            out.add(code)
    return out


def edition_modules(preset):
    """Модули готового набора (старое имя)."""
    if preset not in PRESETS:
        raise ValueError(f"нет такого набора: {preset}")
    return modules_of(PRESETS[preset]["packages"])


def preset_of(packages):
    """Код готового набора, которому равен этот набор, или CUSTOM."""
    p = normalize(packages)
    return next((code for code, d in PRESETS.items() if normalize(d["packages"]) == p), CUSTOM)


def base_title(base, packages):
    """«Старт + Склад и закупки» — набор, с которого начали, и что поменяли галочками."""
    if base not in PRESETS:
        return title_of(packages)
    have, was = normalize(packages), normalize(PRESETS[base]["packages"])
    if have == was:
        return PRESETS[base]["title"]
    parts = []
    if have["sales"] != was["sales"]:
        parts.append(("+ Продажи " + LEVELS[have["sales"]]) if have["sales"] else "− Продажи")
    for code in ("stock", "logistics", "office", "max"):
        if have[code] != was[code]:
            parts.append(("+ " if have[code] else "− ") + PACKAGES[code]["title"])
    return PRESETS[base]["title"] + " " + " ".join(parts)


def title_of(packages):
    """«Бизнес» / «Компания» / «Продажи Бизнес + Склад и закупки» / «ничего не подключено»."""
    code = preset_of(packages)
    if code != CUSTOM:
        return PRESETS[code]["title"]
    parts = package_titles(packages)
    return " + ".join(parts) if parts else "ничего не подключено"


def package_titles(packages):
    p = normalize(packages)
    out = []
    for code, d in PACKAGES.items():
        v = p[code]
        if not v:
            continue
        out.append(f"{d['title']} {LEVELS[v]}" if code == "sales" else d["title"])
    return out


def package_of(module):
    """(пакет, уровень) модуля."""
    _, pkg, level = MODULES[module]
    return pkg, level


def lock_text(module):
    """Подпись на закрытом экране: отдел функции (31.10 — платим за функции)."""
    if module in FUNCS:
        icon, gtitle = FUNC_GROUPS[FUNCS[module][0]]
        return f"Функция · {icon} {gtitle}"
    pkg, level = package_of(module)
    if pkg == "sales":
        return f"Продажи — уровень {LEVELS[level]} и выше"
    if pkg == "any":
        return "Входит в любой пакет"
    if pkg == "ops":
        return "Пакеты «Склад и закупки», «Логистика» или «Кадры и офис»"
    return f"Пакет «{PACKAGES[pkg]['title']}»"


def min_edition(module):
    """Самый младший готовый набор, где модуль есть (для текстов и совместимости)."""
    return next((code for code in ORDER if module in edition_modules(code)), None)


def dept_package(kind):
    """Какой пакет отвечает за отдел этого вида."""
    return DEPT_PACKAGE.get(kind)


def bought(packages):
    """Коды купленных пакетов по порядку."""
    p = normalize(packages)
    return [c for c in PACKAGES if p[c]]


def upgrade(packages, code):
    """Следующее состояние пакета при нажатии в конструкторе: продажи идут по кругу
    Нет → Старт → Бизнес → Премиум → Нет, остальные — включить / выключить."""
    p = normalize(packages)
    if code == "sales":
        cur = p["sales"]
        nxt = LEVEL_ORDER[0] if cur is None else (LEVEL_ORDER[LEVEL_ORDER.index(cur) + 1] if cur != LEVEL_ORDER[-1] else None)
        p["sales"] = nxt
    elif code in p:
        p[code] = not p[code]
    return p


# ---------- функции (Адам 31.10: платим только за функции; отдел — папка, настройки внутри функции — бесплатно) ----------
# код модуля: (отдел для группировки, цена по умолчанию ₽/мес). Отдел подключён, если в нём выбрана хоть одна функция.
FUNC_GROUPS = {"all": ("💰", "Все отделы"), "sales": ("📈", "Продажи"), "stock": ("📦", "Склад и закупки"),
               "logistics": ("🚚", "Логистика"), "office": ("👤", "Кадры и офис"), "max": ("⭐", "Дополнительно")}
FUNCS = {
    "payroll": ("all", 3000), "approval": ("all", 1000),
    "kpi": ("sales", 3000), "route": ("sales", 2000), "debts": ("sales", 2000), "clients": ("sales", 2000),
    "tasks": ("sales", 1000), "quiz": ("sales", 1000), "achievements": ("sales", 1000), "plans": ("sales", 1000),
    "forecast": ("sales", 1000), "call_today": ("sales", 1000), "watchdog": ("sales", 1000), "formulas": ("sales", 2000),
    "stock": ("stock", 3000), "warehouse": ("stock", 2000), "returns": ("stock", 1000),
    "logistics": ("logistics", 3000), "fuel": ("logistics", 1000),
    "hr": ("office", 2000),
    "branding": ("max", 10000), "branches": ("max", 10000), "custom": ("max", 20000),
}
FUNC_SHORT = {
    "payroll": "Зарплата: табель, оклад, ГСМ", "approval": "Утверждение зарплаты",
    "kpi": "План и факт, рейтинг", "route": "Маршруты и отчёт дня", "debts": "Дебиторка", "clients": "Клиенты",
    "tasks": "Задачи", "quiz": "Тесты и обучение", "achievements": "Достижения", "plans": "Утверждение планов",
    "forecast": "Прогноз", "call_today": "Кому позвонить", "watchdog": "Ночной сторож", "formulas": "Свои формулы",
    "stock": "Закупки и заказы", "warehouse": "Склад: приёмка", "returns": "Возвраты", "logistics": "Рейсы водителей",
    "fuel": "Контроль топлива", "hr": "Кадры и приказы", "branding": "Свой бренд", "branches": "Филиалы",
    "custom": "Функции на заказ",
}
FREE = {"reminders", "audit", "staff"}          # бесплатно у всех (кабинеты — когда подключён отдел вне продаж)


def functions_of(packages):
    """Платные функции, которые даёт набор пакетов (готовые наборы так и заданы)."""
    return {m for m in modules_of(packages) if m in FUNCS}


def packages_of(functions):
    """Набор пакетов по выбранным функциям: отдел включён, если в нём есть функция; уровень продаж — по самой старшей."""
    fs = set(functions or ())
    out = {code: False for code in PACKAGES}
    out["sales"] = None
    levels = [MODULES[f][2] for f in fs if f in MODULES and MODULES[f][1] == "sales" and MODULES[f][2]]
    if any(FUNCS.get(f, ("",))[0] == "sales" for f in fs):
        out["sales"] = max(levels, key=LEVEL_ORDER.index) if levels else "start"
    for code in ("stock", "logistics", "office", "max"):
        out[code] = any(FUNCS.get(f, ("",))[0] == code for f in fs)
    if not any(v for k, v in out.items() if k != "max") and "payroll" in fs:
        pass
    return out


def modules_from(functions, packages):
    """Модули клиента, если функции выбраны поштучно: выбранные + бесплатные (кабинеты — при отделе вне продаж)."""
    out = {f for f in functions if f in MODULES}
    out |= {"reminders", "audit"}
    p = normalize(packages)
    if p["stock"] or p["logistics"] or p["office"]:
        out.add("staff")
    for need, deps in REQUIRES.items():
        if need in out:
            out |= deps
    return out


def toggle_function(functions, code):
    """Отметить / снять функцию с учётом связей: включили «Склад» — включатся «Закупки»; сняли «Закупки» — снимется «Склад»."""
    fs = set(functions)
    if code in fs:
        fs.discard(code)
        fs -= {f for f, deps in REQUIRES.items() if code in deps}
    else:
        fs.add(code)
        fs |= {d for d in REQUIRES.get(code, ()) if d in FUNCS}
    return fs


def preset_functions(code):
    return functions_of(PRESETS[code]["packages"])


def funcs_title(base, functions):
    """«Старт + Склад: приёмка …» — набор, с которого начали, и что добавили или убрали (коротко)."""
    if base not in PRESETS:
        return f"своих функций: {len(functions)}"
    have, was = set(functions), preset_functions(base)
    if have == was:
        return PRESETS[base]["title"]
    plus, minus = len(have - was), len(was - have)
    return PRESETS[base]["title"] + (f" + {plus}" if plus else "") + (f" − {minus}" if minus else "") + " функц."
