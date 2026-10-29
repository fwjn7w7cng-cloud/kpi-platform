"""Нейтральный формат данных — то, во что парсер любого клиента переводит свою 1С (у demo — сидер).

Ядро считает только по этому формату и ничего не знает о том, откуда он пришёл.

Пачка (Dataset) — снимок одного вида данных за период: «продажи за октябрь», «долги на 14.10».
Новая пачка того же вида за тот же период заменяет прежнюю целиком (1С каждый раз выгружает месяц заново).

Люди в фактах — ключом из источника (``emp_key``, например «иванов пётр»). Ключ привязывается
к сотруднику отдельно (таблица ``emp_keys``), поэтому смена торгового не ломает историю.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Sale:
    """Продажа (или возврат с минусом) торгового клиенту по группе товаров за день."""
    day: date
    emp_key: str
    client: str           # код клиента в учёте клиента (КОД в 1С)
    group: str = ""       # код группы товаров / бренда
    amount: float = 0.0   # сумма, ₽ (возврат — с минусом)
    qty: float = 0.0
    discount: float = 0.0  # скидка от прайса, доля (0.05 = 5%)


@dataclass
class Debt:
    """Долг клиента на дату среза, по срокам."""
    on: date
    emp_key: str
    client: str
    total: float = 0.0
    d0_30: float = 0.0
    d31_60: float = 0.0
    d61_90: float = 0.0
    d91: float = 0.0       # 91 день и больше
    over: float = None     # просрочка сверх порога клиента (kpi.debt_days), если отчёт 1С даёт её готовой — точнее корзин


@dataclass
class Payment:
    """Деньги, занесённые клиентом (сбор)."""
    day: date
    emp_key: str
    client: str
    amount: float = 0.0


@dataclass
class Visit:
    """Плановая точка маршрута торгового на день."""
    day: date
    emp_key: str
    client: str


@dataclass
class Client:
    """Справочник клиентов: код → название."""
    code: str
    name: str
    town: str = ""
    phone: str = ""


@dataclass
class Group:
    """Справочник групп товаров: код → название, линия."""
    code: str
    name: str
    line: str = ""


@dataclass
class Return:
    """Возврат от клиента с причиной. Чья ошибка — решает переводчик клиента по комментарию 1С."""
    day: date
    emp_key: str          # торговый клиента
    client: str
    doc: str = ""         # номер документа возврата
    amount: float = 0.0   # сумма возврата, ₽ (положительная)
    reason: str = ""      # комментарий из 1С как есть
    fault: str = ""       # rep | warehouse | driver | client | other | "" (не разобрано)
    who: str = ""         # ключ виноватого сотрудника (склад, водитель), если назван


@dataclass
class Stock:
    """Остаток товара на дату."""
    on: date
    code: str             # код номенклатуры
    name: str
    supplier: str = ""
    qty: float = 0.0
    unit: str = "шт"


@dataclass
class ItemSale:
    """Продажи товара (по номенклатуре) за день или за месяц одной строкой."""
    day: date
    code: str
    qty: float = 0.0
    amount: float = 0.0


@dataclass
class Trip:
    """Рейс водителя: маршрутный лист."""
    day: date
    emp_key: str          # водитель
    trip: str             # номер маршрутного листа
    km: float = 0.0
    kg: float = 0.0
    m3: float = 0.0


@dataclass
class TripStop:
    """Точка рейса: клиент и накладная."""
    day: date
    emp_key: str
    trip: str
    client: str
    doc: str = ""


# вид пачки → (класс строки, таблица в базе, поле даты для проверки периода)
KINDS = {
    "sales":    (Sale, "sales", "day"),
    "debts":    (Debt, "debts", "on"),
    "payments": (Payment, "payments", "day"),
    "visits":   (Visit, "visits", "day"),
    "clients":  (Client, "clients", None),
    "groups":   (Group, "groups", None),
    "returns":  (Return, "returns", "day"),
    "stock":    (Stock, "stock", "on"),
    "item_sales": (ItemSale, "item_sales", "day"),
    "trips":    (Trip, "trips", "day"),
    "trip_stops": (TripStop, "trip_stops", "day"),
}


@dataclass
class Dataset:
    kind: str
    start: date             # период пачки, включительно
    end: date
    rows: list = field(default_factory=list)
    source: str = ""        # имя файла / «seed»
    fingerprint: str = ""   # хэш содержимого файла — повторно не принимаем

    def check(self):
        """Список проблем пачки (пустой — всё хорошо)."""
        p = []
        if self.kind not in KINDS:
            return [f"нет такого вида данных: {self.kind}"]
        cls, _, dfield = KINDS[self.kind]
        if self.end < self.start:
            p.append(f"период {self.start}–{self.end}: конец раньше начала")
        for i, r in enumerate(self.rows):
            if not isinstance(r, cls):
                p.append(f"строка {i + 1}: ждали {cls.__name__}, пришло {type(r).__name__}")
                continue
            if dfield:
                d = getattr(r, dfield)
                if not self.start <= d <= self.end:
                    p.append(f"строка {i + 1}: дата {d} вне периода пачки {self.start}–{self.end}")
            if len(p) >= 20:
                p.append("…дальше не проверяю")
                break
        return p
