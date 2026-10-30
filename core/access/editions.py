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
    "payroll":      ("Расчёт зарплаты по отчётам и табелю", "any", None),
    "achievements": ("Достижения и челленджи", "sales", "business"),
    "staff":        ("Кабинеты склада, водителей, офиса", "ops", None),
    "tasks":        ("Задачи от руководителя", "sales", "business"),
    "clients":      ("Клиенты: спящие, падают, растут", "sales", "business"),
    "route":        ("Маршрут и отчёт за день", "sales", "business"),
    "quiz":         ("Тесты и обучение новичков", "sales", "business"),
    "plans":        ("Утверждение планов", "sales", "control"),
    "approval":     ("Цепочка утверждения зарплаты и реестр", "sales", "control"),
    "audit":        ("Журнал изменений", "sales", "control"),
    "watchdog":     ("«Ночной сторож» аномалий", "sales", "control"),
    "forecast":     ("Прогноз плана и сравнение отделов", "sales", "control"),
    "call_today":   ("«Кому позвонить сегодня»", "sales", "control"),
    "formulas":     ("Свои формулы KPI", "sales", "control"),
    "stock":        ("Закупки: остатки, заказы поставщикам, согласование", "stock", None),
    "warehouse":    ("Склад: приёмка поставок, отгрузки, ошибки", "stock", None),
    "returns":      ("Возвраты и чья ошибка", "stock", None),
    "logistics":    ("Логистика: рейсы, «Мой день» водителя, сдельная оплата", "logistics", None),
    "fuel":         ("ГСМ водителей: топливо против карты", "logistics", None),
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
    """Подпись на закрытом экране: «Пакет «Склад и закупки»» / «Продажи — уровень Бизнес» / «Любой пакет»."""
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
