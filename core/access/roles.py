"""Роли платформы. Набор ролей общий для всех клиентов; клиент может переименовать их подписи
(``roles:`` в config.yaml), но не придумать новые — новые права добавляются в ядро.

Область видимости (scope):
  self  — только своё
  team  — своя команда
  dept  — свой отдел
  all   — вся компания
"""

ROLES = {
    "rep":        {"title": "Менеджер",            "scope": "self"},
    "sv":         {"title": "Супервайзер",         "scope": "team"},
    "head":       {"title": "Начальник отдела",    "scope": "dept"},
    "owner":      {"title": "Руководитель",        "scope": "all"},
    "accountant": {"title": "Бухгалтер",           "scope": "all"},
    "staff":      {"title": "Сотрудник",           "scope": "self"},
    "newbie":     {"title": "Новичок",             "scope": "self"},
    "admin":      {"title": "Администратор",       "scope": "all"},
}

# Роли, которые есть в переключателе демо («Смотреть как…»)
DEMO_SWITCH = ["rep", "sv", "head", "owner", "admin"]
