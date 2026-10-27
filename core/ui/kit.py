"""Что экран отдаёт мессенджеру — без привязки к Telegram или MAX.

    Reply(view=View(...), buttons=[[Button("📈 План", "plan:12")]], files=[("ведомость.xlsx", b"...")])

Кнопка ведёт на экран «id:аргументы» (не длиннее 60 байт — ограничение Telegram 64).
Закрытая редакцией кнопка — ``Button(..., lock="debts")``: мессенджер покажет «🔒», нажатие — объяснение.
"""
from dataclasses import dataclass, field

from core.ui.views import Cell, Row, View


@dataclass
class Button:
    label: str
    to: str = ""
    lock: str = ""          # код модуля, если кнопка закрыта редакцией
    url: str = ""

    def __post_init__(self):
        if self.lock:
            self.to = f"lock:{self.lock}"
            if not self.label.startswith("🔒"):
                first, _, rest = self.label.partition(" ")
                self.label = "🔒 " + (rest if rest and not first[:1].isalnum() else self.label)
        if len(self.to.encode()) > 60:
            raise ValueError(f"слишком длинная кнопка: {self.to}")


@dataclass
class Reply:
    view: View = None           # экран (картинка / текст из одного описания)
    text: str = ""              # простой текст (когда экрана нет или в дополнение)
    buttons: list = field(default_factory=list)     # ряды кнопок
    files: list = field(default_factory=list)       # [(имя файла, байты)]
    ask: str = ""               # подсказка: бот ждёт ответ текстом
    to: int = 0                 # кому (для рассылок); 0 — тому, кто нажал
    edit: bool = True           # можно заменить предыдущее сообщение

    def all_buttons(self):
        return [b for row in self.buttons for b in row]


def rows(buttons, per_row=2):
    """Плоский список кнопок → ряды по per_row."""
    out, cur = [], []
    for b in buttons:
        if b is None:
            continue
        cur.append(b)
        if len(cur) == per_row:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


# ---------- числа ----------

def money(x, unit=" ₽"):
    return f"{round(x or 0):,}".replace(",", " ") + unit


def num(x):
    if x is None:
        return "—"
    if abs(x - round(x)) < 1e-9:
        return f"{round(x):,}".replace(",", " ")
    return f"{x:,.1f}".replace(",", " ").replace(".", ",")


def pct(x, digits=0):
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def fmt(value, unit):
    if unit == "₽":
        return money(value, "")
    if unit == "%":
        return pct(value)
    return num(value)


def metric_row(m, name=None):
    """Строка таблицы «показатель · план · факт · %» с полоской выполнения и меткой «нужно к сегодня»."""
    p = m.pct
    bar = (min(p, 1.5) / 1.5, min(m.need, 1.5) / 1.5) if p is not None and not m.lower_better else None
    if m.lower_better:
        cell = Cell("норма" if (p or 0) >= 1 else "выше", m.tone, chip=True) if p is not None else Cell("—")
    else:
        cell = Cell(pct(p) if p is not None else "—", m.tone, bar=bar)
    sub = ""
    if m.plan and not m.lower_better and m.left and m.left_days:
        sub = f"осталось {fmt(m.left, m.unit)} · в день {fmt(m.per_day, m.unit)}"
    elif m.lower_better and m.detail.get("debt"):
        sub = f"{money(m.detail['sum'])} из долга {money(m.detail['debt'])}"
    plan = fmt(m.plan, m.unit) if m.plan else "—"
    if m.lower_better:
        plan = f"≤ {pct(m.plan)}"
    return Row([name or m.title, plan, fmt(m.fact, m.unit), cell], sub=sub)


METRIC_HEAD = ["Показатель", "План", "Факт", "%"]
