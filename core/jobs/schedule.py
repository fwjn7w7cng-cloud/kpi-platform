"""Рассылки по расписанию. Каждая рассылка — функция (ctx) → Reply или None, где ctx — контекст
получателя: бот собирает сообщение так, как его увидел бы этот человек, со всеми правами и редакцией.

    @job("morning_brief", "morning", roles={"rep", "sv", "head", "owner"})
    def brief(ctx): ...

Время: "morning" / "evening" — из schedule в анкете клиента, или прямо "09:10".
По выходным из анкеты (work_week.weekend) рассылки с workdays=True не идут.
"""
from dataclasses import dataclass, field
from datetime import datetime

from core.calendar import WEEKDAYS

JOBS = []


@dataclass
class Job:
    name: str
    title: str
    when: str
    fn: object
    roles: set = field(default_factory=set)
    module: str = ""
    workdays: bool = True
    days: object = None        # дни недели (["sun"]) или функция (app) → дни; None — каждый день


def job(name, when, roles, title="", module="", workdays=True, days=None):
    """when — "morning" / "evening" / "09:10" или функция (app) → "10:00" (время из настроек модуля)."""
    def deco(fn):
        JOBS.append(Job(name, title or name, when, fn, set(roles), module, workdays, days))
        return fn
    return deco


def time_of(app, j):
    when = j.when(app) if callable(j.when) else j.when
    t = (app.cfg.raw.get("schedule") or {}).get(when, when)
    if not t or ":" not in str(t):
        return None
    h, m = str(t).split(":")
    return int(h), int(m)


def due(app, now: datetime):
    """Рассылки, которым пора в эту минуту."""
    out = []
    for j in JOBS:
        t = time_of(app, j)
        if t and (now.hour, now.minute) == t:
            if j.workdays and WEEKDAYS[now.weekday()] in app.cfg.weekend:
                continue
            days = j.days(app) if callable(j.days) else j.days
            if days and WEEKDAYS[now.weekday()] not in days:
                continue
            out.append(j)
    return out


def run(app, j):
    """Собрать сообщения рассылки всем подходящим получателям."""
    if j.module and j.module not in app.cfg.modules:
        return []
    out = []
    targets = [(e["tg_id"], e["role"]) for e in app.recipients()] + [(a, "admin") for a in app.admins]
    seen = set()
    for uid, role in targets:
        if uid in seen or role not in j.roles:
            continue
        seen.add(uid)
        ctx = app.ctx(uid)
        r = j.fn(ctx)
        if r:
            for x in (r if isinstance(r, list) else [r]):
                x.to = x.to or uid
                x.edit = False
                out.append(x)
    return out


def preview(ctx, name):
    """Как рассылку увидит текущий зритель (кнопка «📬 Пример рассылки» в демо и у админа)."""
    j = next((x for x in JOBS if x.name == name), None)
    if not j:
        return None
    return j.fn(ctx)
