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

from core.ui.kit import Button, Reply, money, ok_row, rows
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
YESNO_GROW = {False: "не растёт", True: "растёт"}
WHEN = {"last": "на последнюю дату", "avg": "в среднем за месяц"}

# ---------- поля ----------
# поле: (подпись, вид[, варианты]) — вид: money | pct | int | text | steps_money | steps_coef | steps_rate | steps_min | amounts |
# choice (переключатель по кругу) | named (свой список «название — сумма») | groups (ставки по группам) | weights (KPI-карта) | coefs
F = {
    "amount": ("Сумма", "money"), "rate": ("Ставка", "pct"), "rate_above": ("Ставка сверх плана", "pct"),
    "min_plan": ("Порог плана", "pct"), "min": ("Порог", "pct"), "cap": ("Потолок", "pct"),
    "steps": ("Ступени", "steps_money"), "rate_steps": ("Ставки по плану", "steps_rate"), "coefs": ("Множители", "steps_coef"),
    "debt_rates": ("Ставки по просрочке", "steps_rate"),
    "share": ("Допустимая доля", "pct"), "days": ("Просрочка от, дней", "int"), "max": ("Не больше", "money"),
    "debt_steps": ("Ступени просрочки", "steps_money"), "months": ("Не брал, месяцев", "int"), "norm": ("Норма", "pct"),
    "norm_money": ("Средний чек от", "money"), "price": ("Цена", "money"),
    "amounts": ("За места", "amounts"), "name": ("Название", "text"), "cut_per_miss": ("Минус за пропуск", "money"),
    "cut_price": ("Минус за ошибку", "money"), "min_total": ("Гарантированный минимум", "money"),
    "max_variable": ("Потолок премии", "money"), "rate61": ("За 61–90 дней", "pct"), "rate91": ("За 91+ дней", "pct"),
    "rate_over": ("Сверх последней ступени", "pct"), "allow": ("Выходных на человека", "int"), "km": ("От, км в день", "int"),
    "truancy": ("Прогул", "money"), "l100": ("Расход, л на 100 км", "int"), "fuel_price": ("Цена литра", "money"), "late": ("Опоздания", "steps_min"), "fines": ("Свои штрафы", "named"),
    "groups": ("Свои ставки по группам", "groups"), "items": ("Показатели и веса", "weights"), "pcoefs": ("Коэффициенты", "coefs"),
    "metric": ("Показатель", "choice", METRICS), "scope": ("Чьи", "choice", SCOPES),
    "grow": ("Сверх 100 %", "choice", YESNO_GROW), "when": ("Просрочка", "choice", WHEN),
    "by_exec": ("× выполнение общего плана", "choice", {False: "нет", True: "да"}),
    "kind": ("Кого считать", "choice", {"both": "новых и вернувшихся", "new": "только новых", "back": "только вернувшихся"}),
    "hand": ("Выдаётся", "choice", {False: "в ведомости", True: "на руки, не в ведомость"}),
    "own": ("Оклад", "choice", {False: "один на всех", True: "у каждого свой"}),
    "extra_coef": ("Выход в выходной", "choice", {1: "день за день", 2: "день за два"}),
    "newbie_free": ("Новичкам", "choice", {True: "без штрафов", False: "штрафы как всем"}),
}
SALES = ("rep", "sv", "head")
ALL = tuple(POSITIONS)
STAFF = ("wh", "driver", "buyer", "office")
UNITS = {   # единица сдельной оплаты: (подпись, должности, цена по умолчанию, это удержание?)
    "km": ("за километр пробега", ("driver",), 5, False), "kg": ("за кг груза", ("driver",), 1, False),
    "m3": ("за м³ груза", ("driver",), 50, False), "points": ("за точку доставки", ("driver",), 50, False),
    "trips": ("за рейс", ("driver",), 500, False), "nights": ("за ночёвку в рейсе", ("driver",), 1000, False),
    "nights_day": ("за ночёвку — дневной оклад", ("driver",), 0, False),
    "long_day": ("за длинный день (от N км)", ("driver",), 500, False),
    "overtime": ("за минуту переработки", ("driver",), 5, False), "cash": ("за инкассацию у клиента", ("driver",), 100, False),
    "pickup": ("за привоз от поставщика", ("driver",), 500, False),
    "all_points": ("за отгрузку отдела (точку)", ("wh",), 10, False), "fuel_save": ("доля сэкономленного топлива", ("driver",), 0.5, False),
    "tasks_done": ("за задачу в срок", ALL, 500, False), "quiz_ok": ("за сданный тест", ALL, 500, False),
    "ret_err": ("ошибка в возврате", ("wh", "driver", "rep", "sv"), 500, True),
    "ret_own": ("доля возвратов по своей вине", ("rep",), 0.03, True),
    "fuel_over": ("доля перерасхода топлива", ("driver",), 1, True), "stockout": ("позиция «закончилось»", ("buyer",), 500, True),
    "stale": ("залежалая позиция", ("buyer",), 100, True), "tasks_late": ("задача не в срок", ALL, 500, True),
    "quiz_fail": ("несданный тест", ALL, 500, True),
}
UNIT_EXTRA = {"long_day": {"km": 400}}             # свои поля единицы
RATE_UNITS = {"ret_own", "fuel_save", "fuel_over"}
ONCE = {"salary", "timesheet", "limits", "seniority", "fuel"}

_LATE = [[10, 100], [30, 300], [60, 500]]
_FINES = [["Нет на собрании", 500], ["Нет отчёта", 500]]

# id: (раздел, значок, название, для кого, [(вариант, подпись, поля, шаблон кубика)], описание)
BRICKS = {
    "salary": ("Основа", "💼", "Оклад", ALL, [
        ("days", "за отработанные дни", {"amount": 30000, "own": False}, {"type": "salary", "mode": "days"}),
        ("fixed", "фикс за месяц", {"amount": 30000, "own": False}, {"type": "salary", "mode": "fixed"}),
        ("calendar", "по календарным дням", {"amount": 30000, "own": False}, {"type": "salary", "mode": "calendar"}),
        ("shift", "за смену (выходы по табелю)", {"amount": 2000}, {"type": "salary", "mode": "shift"}),
        ("floor", "сдельная, но не меньше оклада", {"amount": 30000, "own": False}, {"type": "limits", "name": "Доплата до оклада"}),
        ("advance", "оклад как аванс — вычитается из сдельной", {"amount": 15000, "own": False}, {"type": "salary", "mode": "advance"}),
    ], "только оклад, только сдельная (без этого блока), оклад + сдельная, за смену, не меньше оклада"),
    "fuel": ("Основа", "⛽", "ГСМ", ALL, [
        ("fixed", "фикс в месяц", {"amount": 5000}, {"type": "fuel_comp", "mode": "fixed"}),
        ("day", "за отработанный день", {"price": 300}, {"type": "fuel_comp", "mode": "day"}),
        ("km", "за км (рейсы или вписанные км)", {"price": 5}, {"type": "fuel_comp", "mode": "km"}),
        ("norm", "по норме: км × расход × цена литра", {"l100": 10, "fuel_price": 60}, {"type": "fuel_comp", "mode": "norm"}),
    ], "в общую зарплату; км — из рейсов или вписанные руками (торговый сам за день)"),
    "timesheet": ("Основа", "📅", "Табель и штрафы", ALL, [
        ("rules", "опоздания, прогулы и свои штрафы",
         {"late": _LATE, "truancy": 1000, "fines": _FINES, "extra_coef": 1, "newbie_free": True}, {"type": "timesheet"}),
    ], "опоздания ступенями, прогул, свои штрафы с названиями, выход в выходной"),
    "company": ("За план", "🏆", "Доля от плана компании", ALL, [
        ("steps", "ступени", {"steps": [[0.9, 5000], [1.0, 10000]]}, {"type": "plan_bonus", "metric": "sales", "scope": "all", "mode": "steps"}),
        ("linear", "пропорционально", {"amount": 10000, "min": 0.8, "cap": 1.2, "grow": True},
         {"type": "plan_bonus", "metric": "sales", "scope": "all", "mode": "linear"}),
    ], "премия, если вся фирма делает план"),
    "growth": ("За план", "🚀", "Рост продаж", SALES, [
        ("ly", "к тому же месяцу прошлого года", {"steps": [[0.1, 5000], [0.2, 10000]], "rate_over": 0.01},
         {"type": "growth_bonus", "vs": "ly"}),
        ("pm", "к прошлому месяцу", {"steps": [[0.1, 5000], [0.2, 10000]], "rate_over": 0},
         {"type": "growth_bonus", "vs": "pm"}),
    ], "бонус за прирост, сверх последней ступени — процент"),
    "kpi_card": ("За план", "🎯", "KPI-карта с весами", SALES, [
        ("card", "премия × выполнение карты", {"items": [["sales", 0.5], ["akb", 0.3], ["collect", 0.2]], "amount": 20000,
                                                "min": 0.8, "cap": 1.2}, {"type": "kpi_card"}),
    ], "несколько показателей с весами — одна премия"),
    "period": ("За план", "🗓", "За квартал или год", SALES, [
        ("quarter", "за квартал", {"metric": "sales", "steps": [[1.0, 20000], [1.1, 30000]]}, {"type": "period_bonus", "period": "quarter"}),
        ("year", "за год", {"metric": "sales", "steps": [[1.0, 50000], [1.1, 100000]]}, {"type": "period_bonus", "period": "year"}),
    ], "платится в конце квартала или года по сумме месяцев"),
    "team_days": ("За план", "📆", "Команда отработала месяц", ("sv",), [
        ("days", "все отработали норму", {"amount": 5000, "allow": 3}, {"type": "team_days"}),
    ], "супервайзеру — если команда вышла на работу"),
    "debt_pen": ("Деньги и долги", "💵", "Удержание за просрочку", SALES, [
        ("fixed", "фикс, если просрочка выше нормы", {"share": 0.1, "amount": 5000, "days": 60, "when": "last"},
         {"type": "debt_penalty", "mode": "fixed"}),
        ("percent", "% от суммы просрочки", {"rate": 0.02, "max": 10000, "days": 60, "when": "last"},
         {"type": "debt_penalty", "mode": "percent"}),
        ("steps", "ступени по доле просрочки", {"debt_steps": [[0.05, 1000], [0.1, 5000], [0.2, 10000]], "days": 60, "when": "last"},
         {"type": "debt_penalty", "mode": "steps"}),
        ("buckets", "% по срокам: 61–90 и 91+ дней", {"rate61": 0.02, "rate91": 0.05}, {"type": "debt_penalty", "mode": "buckets"}),
    ], "минус, когда долги клиентов выше нормы"),
    "debt_bonus": ("Деньги и долги", "🟢", "Бонус за низкую просрочку", SALES, [
        ("fixed", "фикс, если просрочка в норме", {"share": 0.05, "amount": 5000, "days": 60, "when": "last"},
         {"type": "debt_bonus", "mode": "fixed"}),
        ("zero", "фикс, если просрочки нет совсем", {"amount": 5000, "days": 60}, {"type": "debt_bonus", "mode": "fixed", "share": 0}),
        ("steps", "ступени", {"debt_steps": [[0.02, 10000], [0.05, 5000]], "days": 60, "when": "last"}, {"type": "debt_bonus", "mode": "steps"}),
    ], "плюс за чистую дебиторку"),
    "old_debt": ("Деньги и долги", "💰", "Сбор старых долгов", SALES, [
        ("pct", "% от денег с должников", {"rate": 0.02, "days": 91}, {"type": "old_debt"}),
    ], "процент с денег, собранных с клиентов со старой просрочкой"),
    "new_clients": ("Клиенты", "🆕", "Новые и вернувшиеся клиенты", SALES, [
        ("each", "за каждого", {"amount": 1000, "months": 3, "kind": "both"}, {"type": "new_clients"}),
    ], "новый клиент или «спящий», который снова купил"),
    "orders": ("Клиенты", "🧾", "Заказы и средний чек", SALES, [
        ("each", "за каждый заказ", {"price": 100}, {"type": "orders", "mode": "each"}),
        ("avg", "фикс, если средний чек от суммы", {"amount": 5000, "norm_money": 10000}, {"type": "orders", "mode": "avg"}),
    ], "заказ — клиент × день с покупкой"),
    "rank": ("Клиенты", "🥇", "Место в рейтинге", SALES, [
        ("places", "за 1-е, 2-е, 3-е место", {"metric": "sales", "amounts": [10000, 5000, 3000]}, {"type": "rank_bonus"}),
    ], "соревнование внутри отдела"),
    "discount": ("Удержания", "🏷", "Лишние скидки", SALES, [
        ("fixed", "фикс, если скидка выше нормы", {"norm": 0.05, "amount": 5000}, {"type": "discount_penalty", "mode": "fixed"}),
        ("percent", "% от продаж, если выше нормы", {"norm": 0.05, "rate": 0.01}, {"type": "discount_penalty", "mode": "percent"}),
    ], "средняя скидка выше допустимой"),
    "cut": ("Удержания", "➖", "Удержание за ошибки", ALL, [
        ("unit", "за каждую", {"unit": "ret_err", "price": 500}, {"type": "per_unit", "cut": True}),
    ], "ошибки в возвратах, перерасход, «закончилось», задачи не в срок, тесты"),
    "piece": ("Сдельно", "🚚", "Сдельно", ("driver", "wh", "office", "buyer", "rep", "sv"), [
        ("unit", "за единицу работы", {"unit": "km", "price": 5}, {"type": "per_unit"}),
    ], "км, кг, точки, рейсы, ночёвки, длинные дни, отгрузки, задачи, тесты…"),
    "pool": ("Сдельно", "🧺", "Котёл отдела", STAFF, [
        ("points", "с отгрузок отдела", {"price": 20, "pcoefs": {}}, {"type": "pool", "base": "points"}),
        ("sales", "% от продаж компании", {"rate": 0.01, "pcoefs": {}}, {"type": "pool", "base": "sales"}),
    ], "общая сумма делится по коэффициентам должностей и отработанным дням"),
    "discipline": ("Прочее", "⏰", "Бонус «без ошибок и пропусков»", ALL, [
        ("miss", "минус за каждый пропуск", {"name": "Без ошибок и пропусков", "amount": 5000, "cut_per_miss": 1000, "hand": False},
         {"type": "fixed_bonus"}),
        ("err", "минус за каждую ошибку", {"name": "Без ошибок", "amount": 5000, "cut_price": 500, "hand": False},
         {"type": "fixed_bonus", "cut_unit": "ret_err"}),
        ("both", "минус за пропуск и за ошибку", {"name": "Без ошибок и пропусков", "amount": 5000, "cut_per_miss": 1000,
                                                    "cut_price": 500, "hand": False}, {"type": "fixed_bonus", "cut_unit": "ret_err"}),
    ], "фикс, который тает за пропуски и ошибки; можно «на руки»"),
    "fixed": ("Прочее", "🎁", "Фикс-премия или компенсация", ALL, [
        ("plain", "компенсация каждый месяц", {"name": "Компенсация связи", "amount": 1000, "hand": False}, {"type": "fixed_bonus"}),
        ("mentor", "за наставничество", {"name": "Наставник новичка", "amount": 5000, "hand": False}, {"type": "fixed_bonus"}),
        ("combo", "за совмещение", {"name": "Совмещение", "amount": 10000, "hand": False}, {"type": "fixed_bonus"}),
    ], "связь, ГСМ, питание, наставничество, совмещение"),
    "awards": ("Прочее", "🏅", "За значки доски почёта", SALES, [
        ("each", "за каждый значок", {"amount": 2000, "max": 10000}, {"type": "awards"}),
    ], "выполнил план, лидер, больше всех клиентов, чистые долги…"),
    "seniority": ("Прочее", "🎖", "За стаж", ALL, [
        ("years", "за каждый полный год", {"amount": 1000, "max": 5000}, {"type": "seniority"}),
    ], "надбавка за годы в компании"),
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
        ("hold", "половина премии — только при норме просрочки", {"coefs": [[0.1, 0.5]], "days": 60},
         {"type": "multiplier", "metric": "overdue", "applies": "variable"}),
    ], "большая просрочка урезает премию"),
    "limits": ("Множители и пределы", "🧱", "Минимум и потолок", ALL, [
        ("both", "гарантия и потолок", {"min_total": 40000, "max_variable": 200000}, {"type": "limits"}),
    ], "не меньше и не больше"),
}
SECTIONS = ["Основа", "Показатели", "За план", "Деньги и долги", "Клиенты", "Удержания", "Сдельно", "Множители и пределы", "Прочее"]


# Каждый показатель — свой блок со своими вариантами (Адам 31.10: «каждый показатель — свой личный блок»).
# код: (значок, название, процент можно?, ставка по умолчанию, бонус по умолчанию, описание)
METRIC_BLOCKS = {
    "sales":   ("📈", "Продажи", True, 0.01, 20000, "процент с продаж и бонусы за план продаж"),
    "focus":   ("🎯", "Фокусные", True, 0.02, 5000, "процент с фокусных групп и бонусы за их план"),
    "collect": ("💵", "Сбор денег", True, 0.01, 5000, "процент с собранных денег и бонусы за план сбора"),
    "akb":     ("👥", "АКБ — клиенты", False, 0, 10000, "бонус за число покупавших клиентов"),
    "sku":     ("🧩", "SKU — ассортимент", False, 0, 5000, "бонус за ассортимент в точках"),
    "visits":  ("🚗", "Визиты по маршруту", False, 0, 5000, "бонус за визиты с заказом"),
}
PCT_SHORT = {"pct": "%", "pct_gate": "% с порога", "pct_steps": "% по ступеням плана", "pct_split": "% до/сверх плана",
             "pct_debt": "% по просрочке", "pct_groups": "% по группам", "fix": "фикс за план", "gate": "фикс с порога",
             "steps": "пороги", "linear": "пропорционально", "below": "минус ниже порога", "each_group": "за каждую группу"}


def _metric_variants(code, pct_ok, rate, amount):
    v = []
    if pct_ok:
        t = {"type": "sales_percent", "base": code}
        v += [("pct", "% — план не учитывается", {"rate": rate}, dict(t)),
              ("pct_gate", "% — только с порога плана", {"rate": rate, "min_plan": 0.8}, dict(t)),
              ("pct_steps", "% — ставка растёт с планом", {"rate_steps": [[0.8, rate / 2], [1.0, rate], [1.2, rate * 2]]}, dict(t)),
              ("pct_split", "% — до плана и сверх плана", {"rate": rate, "rate_above": rate * 2}, dict(t))]
        if code in ("sales", "collect"):
            v.append(("pct_debt", "% — ставка зависит от просрочки",
                      {"debt_rates": [[0.1, rate], [0.2, rate / 2], [1.0, 0]], "days": 60, "when": "last"}, dict(t)))
        if code == "sales":
            v.append(("pct_groups", "% — своя ставка по группам товаров", {"rate": rate, "groups": {}}, dict(t)))
    b = {"type": "plan_bonus", "metric": code}
    v += [("fix", "фикс за выполнение плана", {"amount": amount, "grow": False}, {**b, "mode": "fixed", "min": 1.0}),
          ("gate", "фикс с порога плана", {"amount": amount, "min": 0.8, "grow": False}, {**b, "mode": "fixed"}),
          ("steps", "пороги (ступени)", {"steps": [[0.8, amount / 2], [1.0, amount], [1.2, amount * 1.5]], "grow": False},
           {**b, "mode": "steps"}),
          ("linear", "пропорционально выполнению", {"amount": amount, "min": 0.8, "cap": 1.2, "grow": True}, {**b, "mode": "linear"})]
    if code == "focus":
        v.append(("each_group", "фикс за каждую фокусную группу", {"amount": amount, "min": 1.0, "by_exec": False},
                  {**b, "mode": "each_group"}))
    v.append(("below", "минус, если план ниже порога", {"amount": amount / 2, "min": 0.8}, {**b, "mode": "below"}))
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
        return [(u, t[0], _unit_p(u), {"type": "per_unit", "unit": u, "cut": cut})
                for u, t in UNITS.items() if t[3] == cut and (pos is None or pos in t[1])]
    return BRICKS[bid][4]


def _unit_p(u):
    p = {} if u == "nights_day" else {"price": UNITS[u][2]}
    p.update(UNIT_EXTRA.get(u, {}))
    return p


def group(bid, v):
    """Группа выбора внутри блока: в одной группе — один вариант, разные группы — можно вместе."""
    if bid in METRIC_BLOCKS:
        return "pct" if v.startswith("pct") else "minus" if v == "below" else "bonus"
    if bid in ("piece", "cut"):
        return v
    return "main"


def brick(b, v=None, **p):
    if b in ("piece", "cut"):
        unit = p.pop("unit", None) or v or variants(b)[0][0]
        out = {"b": b, "v": unit, "p": _unit_p(unit)}
        out["p"].update(p)
        return out
    var = next(x for x in BRICKS[b][4] if v is None or x[0] == v)
    out = {"b": b, "v": var[0], "p": copy.deepcopy(var[2])}
    out["p"].update(p)
    return out


DEFAULTS = {
    "rep": [brick("salary", amount=30000), brick("sales", "pct"), brick("sales", "steps", steps=[[0.8, 10000], [1.0, 20000], [1.2, 30000]]),
            brick("akb", "linear"), brick("debt_pen"), brick("timesheet")],
    "sv": [brick("salary", amount=40000), brick("sales", "pct"), brick("sales", "steps", steps=[[0.9, 10000], [1.0, 20000]]),
           brick("team_days"), brick("timesheet")],
    "head": [brick("salary", amount=60000), brick("sales", "pct"), brick("sales", "fix", amount=30000), brick("timesheet")],
    "wh": [brick("salary", amount=30000), brick("discipline", "both"), brick("timesheet")],
    "driver": [brick("salary", amount=30000), brick("piece", unit="km", price=5), brick("piece", unit="points", price=50),
               brick("discipline", "both"), brick("timesheet")],
    "buyer": [brick("salary", amount=40000), brick("company"), brick("cut", unit="stockout", price=500), brick("timesheet")],
    "office": [brick("salary", "calendar", amount=30000), brick("company"), brick("timesheet")],
}


# ---------- превращение в кубик ядра ----------

def to_comp(pos, item):
    vs = variants(item["b"])
    var = next((x for x in vs if x[0] == item["v"]), vs[0])
    c = copy.deepcopy(var[3])
    for k, v in item["p"].items():
        if k in ("coefs", "debt_steps"):
            c["steps"] = v
        elif k == "norm_money":
            c["norm"] = v
        elif k == "pcoefs":
            c["coefs"] = v
        elif k == "groups":
            c["groups"] = {g: r for g, _, r in v}
        elif k == "hand" and not v:
            continue
        else:
            c[k] = v
    if c["type"] in ("sales_percent", "plan_bonus", "multiplier", "debt_penalty", "debt_bonus", "old_debt", "orders", "kpi_card") \
            and "scope" not in c \
            and pos in SCOPE_OF:
        c["scope"] = SCOPE_OF[pos]
    if c["type"] == "per_unit":
        u = c["unit"]
        cut = UNITS.get(u, ("", (), 0, False))[3]
        c["cut"] = cut
        if u == "nights_day":
            c["unit"], c["day_rate"] = "nights", True
            c.pop("price", None)
        if u in RATE_UNITS:
            c["rate"] = c.pop("price")
        nm = UNITS.get(u, (u,))[0]
        c.setdefault("name", ("Удержание: " + nm) if cut else nm[:1].upper() + nm[1:])
    if c["type"] == "fixed_bonus" and c.get("cut_price") is None:
        c.pop("cut_unit", None)
    if c["type"] == "limits" and "amount" in c:             # «сдельная, но не меньше оклада» — доплата до суммы
        c["min_total"] = c.pop("amount")
    return c


def scheme(pos, items):
    return [to_comp(pos, i) for i in items]


def schemes(session):
    """{схема анкеты: [кубики]} — для анкеты клиента и «🚀 Создать клиента»."""
    pb = (session or {}).get("pb") or {}
    return {POSITIONS[pos][4]: scheme(pos, _upgrade(items)) for pos, items in pb.items() if pos in POSITIONS}


def lines(session):
    """[«Торговый: оклад + % от продаж + …»] — коротко для анкеты клиента."""
    pb = (session or {}).get("pb") or {}
    return [f"{POSITIONS[pos][0]}: " + " + ".join(BRICKS[i["b"]][2].lower() for i in _upgrade(items))
            for pos, items in pb.items() if pos in POSITIONS and items]


def needs_data(session):
    """Блоки, которым нужны особые данные из 1С — для «⚠️ нестандарт» в анкете."""
    out = []
    for pos, items in ((session or {}).get("pb") or {}).items():
        for i in _upgrade(items):
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


def _opts(field):
    f = F[field]
    return f[2] if len(f) > 2 else {}


def fmt(field, v):
    kind = F[field][1]
    if kind == "money":
        return money(v)
    if kind == "pct":
        return _pct(v)
    if kind == "int":
        return str(int(v))
    if kind == "steps_money":
        return "; ".join(f"{_pct(a)} → {money(b)}" for a, b in v)
    if kind == "steps_coef":
        return "; ".join(f"{_pct(a)} → ×{_num(b)}" for a, b in v)
    if kind == "steps_rate":
        return "; ".join(f"{_pct(a)} → {_pct(b)}" for a, b in v)
    if kind == "steps_min":
        return "; ".join(f"{int(a)} мин → {money(b)}" for a, b in v) or "без штрафа"
    if kind == "amounts":
        return " / ".join(money(x) for x in v)
    if kind == "choice":
        o = _opts(field)
        return o.get(v, o.get(str(v), str(v)))
    if kind == "named":
        return "; ".join(f"{n} — {money(a)}" for n, a in v) or "нет"
    if kind == "groups":
        return "; ".join(f"{n} {'не в %' if not r else _pct(r)}" for _, n, r in v) or "как у всех"
    if kind == "weights":
        return "; ".join(f"{METRICS.get(m, m).split(' (')[0]} {_pct(w, 0)}" for m, w in v)
    if kind == "coefs":
        return "; ".join(f"{n} ×{_num(k)}" for n, k in v.items()) or "всем поровну"
    return str(v)


def bname(item):
    """Название блока с уточнением: «Продажи: пороги», «Сдельно: километр пробега»."""
    b = BRICKS[item["b"]]
    p = item["p"]
    if item["b"] in METRIC_BLOCKS:
        return f"{b[2]}: {PCT_SHORT.get(item['v'], item['v'])}"
    if item["b"] in ("mult", "rank") and p.get("metric", "sales") != "sales":
        return f"{b[2]}: {METRICS[p['metric']].split(' (')[0]}"
    if item["b"] in ("piece", "cut"):
        return f"{b[2]}: " + UNITS.get(item["v"], (item["v"],))[0].replace("за ", "", 1)
    if item["b"] in ("fixed", "discipline") and p.get("name"):
        return p["name"]
    return b[2]


def summary(pos, item):
    """Вид и цифры блока одной строкой: «пороги · 80 % → 10 000 ₽; … · сверх 100 %: не растёт»."""
    b = BRICKS[item["b"]]
    p = item["p"]
    if item["b"] in ("piece", "cut"):
        u = item["v"]
        if u == "nights_day":
            return UNITS[u][0]
        price = p.get("price", 0)
        s_ = UNITS.get(u, (u,))[0] + " — " + (_pct(price) if u in RATE_UNITS else money(price))
        return s_ + (f" · от {p['km']} км" if "km" in p else "")
    var = next((x for x in b[4] if x[0] == item["v"]), b[4][0])
    parts = [var[1]] if len(b[4]) > 1 else []
    for k, v in p.items():
        if k == "name":
            continue
        parts.append(f"{F[k][0].lower()}: {fmt(k, v)}")
    if "name" in p:
        parts.insert(0, p["name"])
    return " · ".join(parts)


# ---------- состояние ----------

def state(ctx):
    pb = ctx.session.get("pb")
    if not isinstance(pb, dict):
        pb = {}
    return pb


def _upgrade(items):
    """Схемы, собранные раньше (блоки «% от продаж», «Бонус за план», единица в поле unit, поле metric у старых блоков), — в нынешние."""
    out = []
    for it in items:
        b = it.get("b")
        if b == "percent":
            base = {"collect": "collect"}.get(it.get("v"), "sales")
            v = {"two": "pct_split", "gate": "pct_gate"}.get(it.get("v"), "pct")
            p = {k: x for k, x in it.get("p", {}).items() if k in ("rate", "rate_above", "min_plan")}
            out.append({"b": base, "v": v, "p": {**brick(base, v)["p"], **p}})
        elif b == "plan":
            m = it.get("p", {}).get("metric", "sales")
            m = m if m in METRIC_BLOCKS else "sales"
            v = it.get("v") if it.get("v") in ("fixed", "steps", "linear") else "steps"
            v = "fix" if v == "fixed" else v
            p = {k: x for k, x in it.get("p", {}).items() if k != "metric"}
            out.append({"b": m, "v": v, "p": {**brick(m, v)["p"], **p}})
        elif b in ("piece", "cut") and it.get("v") == "unit":
            u = it.get("p", {}).get("unit", "km")
            out.append({"b": b, "v": u, "p": {"price": it.get("p", {}).get("price", UNITS.get(u, ("", (), 0))[2])}})
        elif b == "discipline" and it.get("v") == "cut":
            out.append({"b": b, "v": "miss", "p": {**brick(b, "miss")["p"], **it.get("p", {})}})
        elif b == "sales" and isinstance(it.get("p", {}).get("groups"), dict):
            out.append({**it, "p": {**it["p"], "groups": [[g, g, r] for g, r in it["p"]["groups"].items()]}})
        elif b == "salary" and "own" not in it.get("p", {}) and it.get("v") != "shift":
            out.append({**it, "p": {**it.get("p", {}), "own": False}})
        elif b == "timesheet" and not it.get("p"):
            out.append(brick("timesheet"))
        elif b in BRICKS:
            vs = [x[0] for x in variants(b)]
            out.append(it if it.get("v") in vs else brick(b))
    return out


def items_of(ctx, pos):
    pb = state(ctx)
    if pos not in pb:
        pb[pos] = copy.deepcopy(DEFAULTS[pos])
        ctx.session["pb"] = pb
    else:
        up = _upgrade(pb[pos])
        if up != pb[pos]:
            pb[pos] = up
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
    """(сотрудник, [(название, сумма, пояснение)], [номер блока для каждой строки]) — расчёт схемы движком на человеке этой должности."""
    _, _, role, posids, _ = POSITIONS[pos]
    es = [e for e in ctx.db.employees() if e["position"] in posids] or [e for e in ctx.db.employees() if e["role"] == role]
    if not es:
        return None, [], []
    if pos in SALES:
        k = ctx.kpi()
        es.sort(key=lambda e: (k.emp(e["id"])["sales"].pct or 0))
        emp = es[len(es) // 2]                                   # средний по выполнению плана — честный пример
    else:
        emp = es[0]
    from core.pay.engine import Payslip
    pay = ctx.pay()
    slip = Payslip(emp)
    owners = []
    try:
        pay.slip_rows(emp, scheme(pos, items), slip, owners)
    except Exception as e:                                       # noqa: BLE001 — блок с неполными цифрами
        return emp, [("⚠️ Не считается", 0, str(e)[:80])], [-1]
    return emp, slip.rows, owners


# ---------- пояснения под значком ⓘ (Адам 31.10: «у каждой настройки — иконка i с объяснением») ----------
VARIANT_INFO = {
    "pct": "Процент от всей суммы, выполнение плана не важно.\nПример: 1 % от продаж 1 000 000 = 10 000.",
    "pct_gate": "Процент платится, только если план выполнен не меньше порога.\nПример: порог 80 %, план 75 % — процента нет.",
    "pct_steps": "Ставка растёт вместе с планом.\nПример: план 80 % — 0,5 %, 100 % — 1 %, 120 % — 2 %.",
    "pct_split": "До плана — одна ставка, всё, что сверх плана, — повышенная.\nПример: 1 % до плана и 2 % сверх.",
    "pct_debt": "Ставка зависит от просрочки: чем она меньше, тем выше процент.\nПример: просрочка до 10 % — 1 %, до 20 % — 0,5 %, больше — 0.",
    "pct_groups": "Своя ставка на отдельные группы товаров; ставка 0 — группа в процент не идёт.\nПример: «Кровля 2 %, Акция 0».",
    "fix": "Фиксированная сумма, если план выполнен на 100 %.",
    "gate": "Фиксированная сумма, если план выполнен не меньше порога.\nПример: порог 80 % — при 85 % сумма выплачивается.",
    "steps": "Чем выше выполнение, тем больше сумма.\nПример: 80 % — 10 000, 100 % — 20 000, 120 % — 30 000.",
    "linear": "Сумма × выполнение плана.\nПример: 20 000 при плане 90 % = 18 000. Ниже порога — 0, выше потолка не растёт.",
    "below": "Удержание, если план ниже порога.\nПример: порог 80 %, план 70 % — минус 10 000.",
    "each_group": "Фикс за каждую фокусную группу, где выполнен план.\nПример: 3 группы из 4 × 5 000 = 15 000.",
}
BLOCK_VARIANT_INFO = {
    ("salary", "days"): "Оклад делится на 26 и умножается на отработанные дни по табелю.",
    ("salary", "fixed"): "Полный оклад каждый месяц, дни не считаются.",
    ("salary", "calendar"): "Оклад ÷ дней в месяце × отработанные календарные дни — как в кассе и бухгалтерии.",
    ("salary", "shift"): "Платим за каждый выход по табелю.\nПример: 22 смены × 2 000 = 44 000.",
    ("salary", "floor"): "Человек получает сдельную часть, но если вышло меньше оклада — доплатим до оклада.",
    ("salary", "advance"): "Оклад выплачивается авансом в течение месяца, а в конце вычитается из начисленного.",
    ("fuel", "fixed"): "Одна сумма на бензин каждый месяц.",
    ("fuel", "day"): "Сумма за каждый отработанный день — для тех, у кого км не считаются.\nПример: 22 дня × 300 = 6 600.",
    ("fuel", "km"): "₽ за км. У водителей км из рейсов, у торговых — вписанные за день (в маршруте или в табеле).",
    ("fuel", "norm"): "По норме: км × расход на 100 км × цена литра.\nПример: 2 000 км × 10 л × 60 ₽ = 12 000.",
    ("debt_pen", "fixed"): "Минус фиксированная сумма, если просрочка выше нормы (доля от продаж).",
    ("debt_pen", "percent"): "Минус процент от суммы просрочки, не больше указанного предела.",
    ("debt_pen", "steps"): "Чем больше доля просрочки, тем больше удержание.\nПример: 5 % — 1 000, 10 % — 5 000, 20 % — 10 000.",
    ("debt_pen", "buckets"): "Процент от старых долгов по срокам.\nПример: 2 % от долга 61–90 дней и 5 % от 91+.",
    ("debt_bonus", "zero"): "Бонус, если просроченных долгов нет совсем.",
    ("timesheet", "rules"): "Опоздания ступенями, прогул, свои штрафы с названиями, выход в выходной, правило для новичков.",
    ("discipline", "miss"): "Бонус, который уменьшается за каждый пропуск дня.",
    ("discipline", "err"): "Бонус, который уменьшается за каждую ошибку (по возвратам).",
    ("discipline", "both"): "Бонус уменьшается и за пропуски, и за ошибки.",
    ("pool", "points"): "Общая сумма за отгрузки отдела делится между людьми по коэффициентам должностей и отработанным дням.",
    ("pool", "sales"): "Процент от продаж компании делится между людьми отдела по коэффициентам и дням.",
}
FIELD_INFO = {
    "own": "Один на всех — у всех на этой должности одна сумма (её задаёте здесь). У каждого свой — оклад вписывается в карточке "
           "сотрудника («👥 Сотрудники» → человек → «💰 Оклад»); если там не вписан — берётся сумма отсюда.",
    "grow": "Растёт — если план выше 100 %, бонус умножается на выполнение (120 % → бонус × 1,2). Не растёт — выше 100 % сумма та же.",
    "hand": "«На руки» — сумма есть в фонде зарплаты, но в ведомость кассы не попадает: её выдаёт руководитель.",
    "when": "На последнюю дату — по последнему отчёту о долгах. В среднем — среднее по всем отчётам месяца (7, 14, 21, конец).",
    "by_exec": "Да — сумма за группы ещё умножается на выполнение общего плана продаж.",
    "kind": "Новые — никогда не покупали. Вернувшиеся — покупали раньше, потом не брали несколько месяцев.",
    "extra_coef": "Работа в выходной: день за день или день за два.",
    "newbie_free": "Новичкам (роль «Новичок») штрафы табеля не начисляются.",
    "metric": "По какому показателю считать.",
    "scope": "Чьи цифры брать: свои, команды, отдела или всей компании.",
    "fines": "Свои штрафы фирмы: название и сумма. В табеле их выбирают по названию.",
    "late": "Опоздания ступенями: минуты и штраф. Пример: 10 мин — 100, 30 мин — 300, 1 час — 500.",
    "min": "Порог — с какого выполнения плана платим.", "cap": "Потолок — выше этого выполнения бонус не растёт.",
    "share": "Допустимая доля просрочки от продаж; выше — удержание.", "days": "С какого срока долг считается просрочкой.",
    "price": "Сколько платим за единицу (км, точку, рейс, ошибку…).", "l100": "Норма расхода топлива на 100 км.",
    "fuel_price": "Цена литра топлива.", "pcoefs": "Коэффициенты по должностям: у кого больше — тому больше доля котла.",
}


def variant_info(bid, v, label):
    if (bid, v) in BLOCK_VARIANT_INFO:
        return BLOCK_VARIANT_INFO[(bid, v)]
    if bid in METRIC_BLOCKS and v in VARIANT_INFO:
        return VARIANT_INFO[v]
    if bid in ("piece", "cut"):
        t = UNITS.get(v, (label,))[0]
        return (f"Удержание {t} — сумма за каждую." if bid == "cut" else f"Оплата {t} — сумма за каждую единицу.") + \
            " Можно отметить несколько единиц сразу."
    desc = BRICKS[bid][5]
    return f"{label[:1].upper()}{label[1:]}. {desc[:1].upper()}{desc[1:]}."


def field_info(k):
    return FIELD_INFO.get(k) or HINT.get(F[k][1], F[k][0])


HINT = {"money": "Сумма, например 10 000", "pct": "Процент, например 1 (это 1 %) или 80 (это 80 %)",
        "int": "Число, например 60", "text": "Название, например «Компенсация связи»",
        "steps_money": "Ступени через точку с запятой: «80 10000; 100 20000; 120 30000» — процент и сумма",
        "steps_coef": "Ступени: «90 0,8; 100 1; 110 1,2» — процент и множитель",
        "steps_rate": "Ступени: «80 0,5; 100 1; 120 2» — процент выполнения (или просрочки) и ставка в %",
        "steps_min": "Ступени: «10 100; 30 300; 60 500» — опоздание в минутах и штраф. 0 — без штрафа",
        "amounts": "Суммы через пробел за 1-е, 2-е, 3-е место: «10000 5000 3000»",
        "groups": "Группа и ставка через точку с запятой: «Кровля 2; Неликвид 5; Акция 0» — 0 значит не идёт в процент",
        "weights": "Показатели и веса: «продажи 50; АКБ 30; сбор 20» (показатели: продажи, фокусные, АКБ, SKU, сбор, визиты)",
        "coefs": "Должности и коэффициенты: «кладовщик 1,5; сборщик 1» — по части названия должности. 0 — всем поровну"}


def _parse_num(t):
    t = t.replace(" ", "").replace(" ", "").replace(",", ".").replace("₽", "").replace("%", "")
    return float(t)


def _parse_pct(t):
    return _parse_num(t) / 100


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


def _named_pairs(text):
    """«Кровля 2; Акция 0» → [("Кровля", 2.0), …]: название — всё до последнего числа."""
    out = []
    for part in re.split(r"[;\n]+", text):
        m = re.match(r"^\s*(.+?)\s+([\d\s.,]+)\s*[%₽]?\s*$", part)
        if m:
            out.append((m.group(1).strip(), _parse_num(m.group(2))))
    if not out:
        raise ValueError
    return out


_METRIC_WORDS = {"продаж": "sales", "фокус": "focus", "акб": "akb", "клиент": "akb", "sku": "sku", "ску": "sku",
                 "ассорт": "sku", "сбор": "collect", "деньг": "collect", "визит": "visits"}


def parse_field(kind, text):
    """Текст человека → значение поля. ValueError — не понял."""
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
    elif kind == "steps_min":
        val = [] if text.strip() == "0" else [[a * 100, b] for a, b in _pairs(text)]
    elif kind == "amounts":
        val = [_parse_num(x) for x in re.split(r"[\s;/]+", text.strip()) if x]
        if not val:
            raise ValueError
    elif kind == "groups":
        val = [] if text.strip() == "0" else [[n, n, r / 100] for n, r in _named_pairs(text)]
    elif kind == "coefs":
        val = {} if text.strip() == "0" else {n.lower(): r for n, r in _named_pairs(text)}
    elif kind == "weights":
        val = []
        for n, w in _named_pairs(text):
            code = next((c for word, c in _METRIC_WORDS.items() if word in n.lower()), None)
            if not code:
                raise ValueError
            val.append([code, w / 100])
    else:
        raise ValueError
    if isinstance(val, float) and val < 0:
        raise ValueError
    return val


ORDER = sorted(BRICKS, key=lambda b: (SECTIONS.index(BRICKS[b][0]) if BRICKS[b][0] in SECTIONS else 99, list(BRICKS).index(b)))


def _sort(items):
    """Порядок в схеме: как в каталоге; множители и пределы — в конце (так их и считает движок)."""
    return sorted(items, key=lambda it: (BRICKS[it["b"]][0] == "Множители и пределы", ORDER.index(it["b"])))


def _blocks_for(pos):
    return [bid for bid in ORDER if pos in BRICKS[bid][3] and variants(bid, pos)]


def _by_item(items, owners, rs):
    """{номер блока в схеме: [строки расчёта]}."""
    out = {}
    for n, r in zip(owners, rs):
        out.setdefault(n, []).append(r)
    return out


# ---------- экраны ----------
# Схема: pb[:должность] (должности — вкладки сверху). Окна выбора (Адам 31.10: «где на одном листе несколько вариантов — ОК и Отмена,
# стрелка остаётся»): pbs — блоки галочками (pbs:t:<блок>, pbs:ok / pbs:no); pbe:<блок> — варианты галочками (:t:<вариант>),
# цифры ✏️ (:f:<вариант>:<поле>), переключатели 🔁 (:c:<вариант>:<поле>), свои штрафы (:n:<вариант>:<поле>:<№|new>), :ok / :no.
# До «ОК» меняется только черновик (pb_sel / pb_blk); стрелка из окна — на страницу конструктора (core/app.is_window).

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
    emp, rs, owners = example(ctx, pos, items)
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
    by = _by_item(items, owners, rs)
    cards, used = [], []
    for bid in ORDER:
        idx = [n for n, it in enumerate(items) if it["b"] == bid]
        if not idx:
            continue
        used.append(bid)
        b = BRICKS[bid]
        got = [r for n in idx for r in by.get(n, [])]
        val = sum(r[1] for r in got)
        lines = [summary(pos, items[n]) for n in idx] + [f"{r[0]}: {r[2]}" for r in got[:3] if r[2]]
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
    tail_ = [Button("↺ Как было", "pbr"), Button("🧹 С нуля", "pbx")]
    if ctx.demo:
        tail_.append(Button("✍️ У нас по-другому", "sw:py:other"))
    kb.append(tail_)
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
    v = View("☑️ Выбрать блоки", f"{POSITIONS[pos][1]} {POSITIONS[pos][0]} · отмечено {len(sel)}")
    v.note("Отметьте, что есть у вас, и нажмите «ОК». Чего у фирмы нет — не отмечайте. Цифры — потом, внутри каждого блока.")
    kb = [[Button(f"{'☑' if b in sel else '☐'} {BRICKS[b][1]} {BRICKS[b][2]}", f"pbs:t:{b}",
                  info=BRICKS[b][5][:1].upper() + BRICKS[b][5][1:] + ".\nВарианты: " + ", ".join(x[1] for x in variants(b, pos)[:8]) + ".")]
          for b in avail]
    kb.append(ok_row("pbs"))
    return Reply(view=v, buttons=kb)


# ----- блок: варианты галочками, цифры, переключатели, свои штрафы -----

def _draft(ctx, bid, fresh=False):
    pos = cur_pos(ctx)
    d = ctx.session.get("pb_blk")
    if fresh or not d or d.get("b") != bid:
        d = {"b": bid, "items": copy.deepcopy([it for it in items_of(ctx, pos) if it["b"] == bid])}
        ctx.session["pb_blk"] = d
        ctx.save()
    return d


def _keep(ctx, d):
    ctx.session["pb_blk"] = d
    ctx.save()


def _short(item):
    if item["b"] in ("piece", "cut"):
        return UNITS.get(item["v"], (item["v"],))[0]
    var = next((x for x in variants(item["b"]) if x[0] == item["v"]), None)
    return var[1] if var else item["v"]


def _ask_field(ctx, bid, it, field, extra=None):
    kind = F[field][1]
    hint = HINT.get(kind, "")
    if field == "debt_steps":
        hint = "Ступени: «5 1000; 10 5000» — доля просрочки в % и сумма"
    if bid == "mult_debt" and field == "coefs":
        hint = "Ступени: «10 0,9; 20 0,7» — просрочка выше % → множитель"
    if field == "debt_rates":
        hint = "Ступени: «10 1; 20 0,5; 100 0» — просрочка до % → ставка %"
    if bid == "growth" and field == "steps":
        hint = "Ступени: «10 5000; 20 10000» — рост в % и сумма"
    if kind == "groups":
        names = sorted(r["name"] for r in ctx.db.q("SELECT name FROM groups"))[:15]
        if names:
            hint += "\nГруппы фирмы: " + ", ".join(names)
    return ctx.ask("pb_field", f"{_short(it)} — {F[field][0].lower()}: сейчас {fmt(field, it['p'][field])}.\n{hint}",
                   {"b": bid, "v": it["v"], "f": field}, buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])


@screen("pbe")
def block(ctx, bid=None, act=None, var=None, field=None, val=None):
    if not _may(ctx) or bid not in BRICKS:
        return page(ctx)
    pos = cur_pos(ctx)
    d = _draft(ctx, bid, fresh=act is None)
    vs = variants(bid, pos)
    if act == "no":
        return page(ctx)
    if act == "ok":
        items = [it for it in items_of(ctx, pos) if it["b"] != bid] + d["items"]
        _save(ctx, pos, _sort(items))
        return page(ctx)
    it = next((x for x in d["items"] if x["v"] == var), None)
    if act == "t" and any(x[0] == var for x in vs):
        if it:
            d["items"] = [x for x in d["items"] if x["v"] != var]
        else:
            g = group(bid, var)
            d["items"] = [x for x in d["items"] if group(bid, x["v"]) != g] + [brick(bid, var)]
        order = [x[0] for x in vs]
        d["items"].sort(key=lambda x: order.index(x["v"]) if x["v"] in order else 99)
        _keep(ctx, d)
    elif act == "f" and it and field in it["p"]:
        return _ask_field(ctx, bid, it, field)
    elif act == "c" and it and field in it["p"] and F[field][1] == "choice":
        keys = list(_opts(field))
        cur = it["p"][field]
        it["p"][field] = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
        _keep(ctx, d)
    elif act == "n" and it and field in it["p"]:
        lst = it["p"][field]
        n = None if val == "new" else int(val) if (val or "").isdigit() and int(val) < len(lst) else None
        cur = f"сейчас: {lst[n][0]} — {money(lst[n][1])}. " if n is not None else ""
        return ctx.ask("pb_named", f"{cur}Напишите название и сумму штрафа: «Нет на собрании 500»."
                       + (" Чтобы убрать — напишите 0." if n is not None else ""),
                       {"b": bid, "v": var, "f": field, "i": n}, buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])
    b = BRICKS[bid]
    v = View(f"{b[1]} {b[2]}", f"{POSITIONS[pos][0]} · {b[5]}")
    chosen = {x["v"] for x in d["items"]}
    multi = bid in ("piece", "cut")
    if bid in METRIC_BLOCKS and any(x[0].startswith("pct") for x in vs):
        v.note("Отметьте один вид процента и/или один вид бонуса за план (и, если нужно, минус ниже порога) — можно всё, можно ничего.")
    elif bid in METRIC_BLOCKS:
        v.note("Отметьте один вид бонуса за план и, если нужно, минус ниже порога.")
    elif multi:
        v.note("Отметьте, за что платить, — можно несколько. Цену каждого — кнопками ✏️ ниже.")
    else:
        v.note("Отметьте вид. Цифры — кнопками ✏️, переключатели — 🔁.")
    if d["items"]:
        all_items = _sort([x for x in items_of(ctx, pos) if x["b"] != bid] + d["items"])
        emp, rs, owners = example(ctx, pos, all_items)
        mine = [n for n, x in enumerate(all_items) if x["b"] == bid]
        by = _by_item(all_items, owners, rs)
        got = [r for n in mine for r in by.get(n, [])]
        if emp and got:
            v.lines([f"{r[0]}: {money(r[1])} — {r[2]}" for r in got], title=f"В примере ({emp['fio']})")
        elif emp:
            v.note(f"В примере ({emp['fio']}) этот блок пока ничего не дал — нет данных или не дотянул до порога.")
    else:
        v.note("Ничего не отмечено — после «✅ ОК» блок уберётся из схемы.")
    kb = [[Button(f"{'☑' if x[0] in chosen else '☐'} {x[1]}", f"pbe:{bid}:t:{x[0]}", info=variant_info(bid, x[0], x[1]))] for x in vs]
    for it in d["items"]:
        if multi:
            pre = UNITS.get(it["v"], (it["v"],))[0] + " · "
        elif bid in METRIC_BLOCKS:
            pre = {"pct": "Процент · ", "minus": "Минус · "}.get(group(bid, it["v"]), "Бонус · ")
        else:
            pre = ""
        for k, val_ in it["p"].items():
            kind = F[k][1]
            name = F[k][0].lower() if pre else F[k][0]
            if kind == "choice":
                kb.append([Button(f"🔁 {pre}{name}: {fmt(k, val_)}", f"pbe:{bid}:c:{it['v']}:{k}", info=field_info(k))])
            elif kind == "named":
                for n, (nm, am) in enumerate(val_):
                    kb.append([Button(f"✏️ {nm} — {money(am)}", f"pbe:{bid}:n:{it['v']}:{k}:{n}")])
                kb.append([Button(f"➕ Добавить штраф ({len(val_)})", f"pbe:{bid}:n:{it['v']}:{k}:new", info=field_info(k))])
            else:
                kb.append([Button(f"✏️ {pre}{name}: {fmt(k, val_)}", f"pbe:{bid}:f:{it['v']}:{k}", info=field_info(k))])
    kb.append(ok_row(f"pbe:{bid}"))
    return Reply(view=v, buttons=kb)


def _group_codes(ctx, val):
    """[[название, название, ставка]] → [[код, название, ставка]] по справочнику групп фирмы (код или часть названия)."""
    k = ctx.kpi()
    known = {}
    for r in ctx.db.q("SELECT code, name FROM groups"):
        known[r["code"]] = r["name"]
    out = []
    for _, n, r in val:
        low = n.lower()
        code = next((c for c, nm in known.items() if c.lower() == low or low in nm.lower()), None)
        if code is None:
            raise ValueError
        out.append([code, known.get(code) or k.group_name(code), r])
    return out


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
        val = parse_field(kind, text)
        if kind == "groups":
            val = _group_codes(ctx, val)
    except (ValueError, IndexError):
        return ctx.ask("pb_field", f"Не понял. {HINT.get(kind, '')}", data, buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])
    it["p"][field] = val
    _keep(ctx, d)
    return block(ctx, bid, "back")


@text_input("pb_named")
def on_named(ctx, text, data):
    bid, var, field, n = data.get("b"), data.get("v"), data.get("f", ""), data.get("i")
    if bid not in BRICKS:
        return page(ctx)
    d = _draft(ctx, bid)
    it = next((x for x in d["items"] if x["v"] == var), None)
    if not it or field not in it["p"]:
        return block(ctx, bid, "back")
    lst = list(it["p"][field])
    if text.strip() in ("0", "-", "удалить", "убрать") and n is not None and n < len(lst):
        lst.pop(n)
    else:
        try:
            name, amount = _named_pairs(text)[0]
        except ValueError:
            return ctx.ask("pb_named", "Не понял. Название и сумма: «Нет на собрании 500».", data,
                           buttons=[[Button("✖️ Отмена", f"pbe:{bid}:back")]])
        if n is not None and n < len(lst):
            lst[n] = [name[:40], amount]
        else:
            lst.append([name[:40], amount])
    it["p"][field] = lst
    _keep(ctx, d)
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
