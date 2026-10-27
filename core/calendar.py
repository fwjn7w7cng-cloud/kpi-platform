"""Рабочий календарь клиента: выходные и праздники из config.yaml, сколько рабочих дней прошло и осталось.

От него — план на день, «нужно к сегодня», оклад за отработанные дни.
«Прошло» — рабочие дни до сегодняшнего (сегодняшний ещё идёт, данные 1С закрывают вчерашний день).
"""
from datetime import date, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def month_of(d):
    return f"{d.year:04d}-{d.month:02d}"


def first_day(month):
    y, m = map(int, month.split("-"))
    return date(y, m, 1)


def last_day(month):
    d = first_day(month)
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def shift_month(month, n):
    d = first_day(month)
    k = d.year * 12 + d.month - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def month_title(month):
    d = first_day(month)
    return f"{MONTHS[d.month - 1].capitalize()} {d.year}"


def day_title(d):
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


class Month:
    def __init__(self, cfg, month, today):
        self.month, self.today = month, today
        self.start, self.end = first_day(month), last_day(month)
        hol = getattr(cfg, "holidays", None) or {}
        self.holidays = {date.fromisoformat(k): v for k, v in hol.items()}
        off = set(cfg.weekend)
        self.days = [self.start + timedelta(i) for i in range((self.end - self.start).days + 1)]
        self.workdays = [d for d in self.days if WEEKDAYS[d.weekday()] not in off and d not in self.holidays]
        self.paid_holidays = [d for d, v in self.holidays.items() if v == "paid" and self.start <= d <= self.end]

    def is_workday(self, d):
        return d in set(self.workdays)

    @property
    def total(self):
        return len(self.workdays)

    @property
    def passed(self):
        if self.today > self.end:
            return self.total
        return sum(1 for d in self.workdays if d < self.today)

    @property
    def left(self):
        return self.total - self.passed

    @property
    def need(self):
        """Доля месяца, которую к сегодня надо было выполнить (по рабочим дням)."""
        return self.passed / self.total if self.total else 1.0

    @property
    def closed(self):
        return self.today > self.end

    def passed_days(self):
        return [d for d in self.workdays if d < self.today] if not self.closed else list(self.workdays)
