"""Редакции и модули платформы.

Редакция — набор модулей, который клиент получает пакетом. Модули сверх редакции клиент может
докупить отдельно (``addons`` в config.yaml). Модули из ``ADDON_ONLY`` в редакции не входят никогда.

Цены здесь не хранятся — только состав. Ядро спрашивает ``has_module(cfg, "debts")`` и рисует
замок «Доступно в Бизнес», если модуля нет.
"""

# Все модули платформы: код → (название для людей, с какой редакции входит; None — только отдельно)
MODULES = {
    "kpi":          ("KPI-ядро: план/факт, рейтинг, карточки, сводка", "start"),
    "reminders":    ("Напоминания и алерты", "start"),
    "debts":        ("Дебиторка", "business"),
    "achievements": ("Достижения и челленджи", "business"),
    "staff":        ("Кабинеты склада, водителей, офиса", "business"),
    "payroll":      ("Предварительный расчёт зарплаты", "business"),
    "tasks":        ("Задачи от руководителя", "business"),
    "approval":     ("Цепочка утверждения зарплаты и реестр", "control"),
    "audit":        ("Журнал изменений", "control"),
    "watchdog":     ("«Ночной сторож» аномалий", "control"),
    "forecast":     ("Прогноз плана и сравнение отделов", "control"),
    "call_today":   ("«Кому позвонить сегодня»", "control"),
    "formulas":     ("Свои формулы KPI", "control"),
    "ai":           ("AI-аналитик", None),
    "miniapp":      ("Mini App-дашборд", None),
    "direct_1c":    ("Прямой обмен с 1С вместо почты", None),
    "branches":     ("Несколько филиалов с общим рейтингом", None),
    "branding":     ("Брендирование", None),
}

# Порядок редакций: каждая включает всё из предыдущих
EDITIONS = {
    "start":    {"title": "Старт",    "users": 15},
    "business": {"title": "Бизнес",   "users": 50},
    "control":  {"title": "Контроль", "users": 150},
}
ORDER = list(EDITIONS)

ADDON_ONLY = {code for code, (_, since) in MODULES.items() if since is None}

# Модули, без которых другой модуль не работает
REQUIRES = {
    "approval": {"payroll"},
    "audit": {"payroll"},
    "call_today": {"debts"},
}


def edition_modules(edition):
    """Модули, входящие в редакцию (с учётом младших)."""
    if edition not in EDITIONS:
        raise ValueError(f"нет такой редакции: {edition}")
    upto = ORDER.index(edition)
    return {code for code, (_, since) in MODULES.items()
            if since is not None and ORDER.index(since) <= upto}


def min_edition(module):
    """С какой редакции модуль входит в пакет; None — только отдельной покупкой."""
    return MODULES[module][1]


def lock_text(module):
    """Подпись на закрытой кнопке: «Доступно в Бизнес» / «Подключается отдельно»."""
    since = min_edition(module)
    return f"Доступно в {EDITIONS[since]['title']}" if since else "Подключается отдельно"
