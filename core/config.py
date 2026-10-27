"""Конфигурация клиента: clients/<id>/config.yaml → ClientConfig.

Всё, чем один клиент отличается от другого без кода, живёт здесь. Ошибки конфига пишутся
по-русски и все сразу, чтобы Адам поправил файл за один заход.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.access import editions as E
from core.access import roles as R

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "clients"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

INDUSTRIES = {
    "building":    "Стройматериалы",
    "food":        "Продукты питания",
    "auto":        "Автозапчасти",
    "apparel":     "Одежда и обувь",
    "electronics": "Электроника и техника",
    "cosmetics":   "Косметика и бытовая химия",
    "pharma":      "Аптечные и медтовары",
    "household":   "Хозтовары и канцелярия",
}

DEPT_KINDS = {"sales", "warehouse", "logistics", "purchasing", "office"}
SOURCES = {"seed", "mail", "drive", "api"}


class ConfigError(Exception):
    def __init__(self, client, problems):
        self.problems = problems
        super().__init__(f"config.yaml клиента «{client}»:\n" + "\n".join(f"  • {p}" for p in problems))


@dataclass
class Dept:
    id: str
    name: str
    kind: str


@dataclass
class Team:
    id: str
    name: str
    dept: str
    line: str = ""


@dataclass
class ClientConfig:
    id: str
    name: str
    industry: str
    timezone: str
    edition: str
    addons: list
    weekend: list
    salary_days: int
    departments: list
    teams: list
    role_titles: dict
    lines: dict
    source: dict
    pay: dict
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def modules(self):
        """Все модули, доступные клиенту: редакция + докупленные."""
        return E.edition_modules(self.edition) | set(self.addons)

    def has_module(self, code):
        return code in self.modules

    def role_title(self, role):
        return self.role_titles.get(role) or R.ROLES[role]["title"]

    def dept(self, dept_id):
        return next((d for d in self.departments if d.id == dept_id), None)

    def teams_of(self, dept_id):
        return [t for t in self.teams if t.dept == dept_id]

    @property
    def users_limit(self):
        return E.EDITIONS[self.edition]["users"]


def client_dir(client_id):
    return CLIENTS / client_id


def load_client(client_id, path=None):
    """Прочитать и проверить конфиг. Бросает ConfigError со списком всех проблем."""
    path = Path(path) if path else client_dir(client_id) / "config.yaml"
    if not path.exists():
        raise ConfigError(client_id, [f"нет файла {path}"])
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(client_id, [f"файл не читается как YAML: {e}"])
    return parse(client_id, raw)


def parse(client_id, raw):
    p = []   # проблемы
    company = raw.get("company") or {}
    if raw.get("id", client_id) != client_id:
        p.append(f"id в файле «{raw.get('id')}» не совпадает с папкой «{client_id}»")
    name = company.get("name") or ""
    if not name:
        p.append("company.name — название компании не задано")
    industry = company.get("industry", "")
    if industry not in INDUSTRIES:
        p.append(f"company.industry «{industry}» — допустимо: {', '.join(INDUSTRIES)}")
    tz = company.get("timezone", "Europe/Moscow")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        p.append(f"company.timezone «{tz}» — нет такого часового пояса")

    edition = raw.get("edition", "")
    if edition not in E.EDITIONS:
        p.append(f"edition «{edition}» — допустимо: {', '.join(E.EDITIONS)}")
    addons = list(raw.get("addons") or [])
    for a in addons:
        if a not in E.MODULES:
            p.append(f"addons: нет такого модуля «{a}»")
    if edition in E.EDITIONS:
        have = E.edition_modules(edition) | set(addons)
        for a in addons:
            if a in E.edition_modules(edition):
                p.append(f"addons: «{a}» уже входит в редакцию {edition} — убрать из addons")
        for mod, need in E.REQUIRES.items():
            if mod in have and not need <= have:
                p.append(f"модуль «{mod}» требует {', '.join(sorted(need - have))}")

    week = raw.get("work_week") or {}
    weekend = list(week.get("weekend") or ["sun"])
    for d in weekend:
        if d not in DAYS:
            p.append(f"work_week.weekend: «{d}» — дни: {', '.join(DAYS)}")
    if len(set(weekend)) >= 7:
        p.append("work_week.weekend: нет ни одного рабочего дня")
    salary_days = week.get("salary_days", 26)
    if not isinstance(salary_days, int) or not 1 <= salary_days <= 31:
        p.append(f"work_week.salary_days «{salary_days}» — целое от 1 до 31 (делитель оклада)")

    depts = []
    for d in raw.get("departments") or []:
        if d.get("kind") not in DEPT_KINDS:
            p.append(f"отдел «{d.get('id')}»: kind «{d.get('kind')}» — допустимо: {', '.join(sorted(DEPT_KINDS))}")
        depts.append(Dept(str(d.get("id", "")), d.get("name", ""), d.get("kind", "")))
    if not depts:
        p.append("departments — нужен хотя бы один отдел")
    if not any(d.kind == "sales" for d in depts):
        p.append("departments — нужен отдел продаж (kind: sales)")
    _dupes(p, "отделы", [d.id for d in depts])
    dept_ids = {d.id for d in depts}

    lines = raw.get("lines") or {}
    teams = []
    for t in raw.get("teams") or []:
        team = Team(str(t.get("id", "")), t.get("name", ""), t.get("dept", ""), t.get("line", ""))
        if team.dept not in dept_ids:
            p.append(f"команда «{team.id}»: нет отдела «{team.dept}»")
        if team.line and team.line not in lines:
            p.append(f"команда «{team.id}»: нет линии «{team.line}» в lines")
        teams.append(team)
    _dupes(p, "команды", [t.id for t in teams])

    titles = raw.get("roles") or {}
    for role in titles:
        if role not in R.ROLES:
            p.append(f"roles: нет такой роли «{role}» — роли: {', '.join(R.ROLES)}")

    source = raw.get("source") or {}
    if source.get("kind") not in SOURCES:
        p.append(f"source.kind «{source.get('kind')}» — допустимо: {', '.join(sorted(SOURCES))}")

    pay = raw.get("pay") or {}
    for key in ("salary", "rate"):
        v = pay.get(key)
        if v is not None and (not isinstance(v, (int, float)) or v < 0):
            p.append(f"pay.{key} «{v}» — неотрицательное число")
    for lid, line in lines.items():
        r = (line or {}).get("rate")
        if r is not None and (not isinstance(r, (int, float)) or not 0 <= r < 1):
            p.append(f"lines.{lid}.rate «{r}» — доля от 0 до 1 (0,4% = 0.004)")

    if p:
        raise ConfigError(client_id, p)
    return ClientConfig(
        id=client_id, name=name, industry=industry, timezone=tz, edition=edition, addons=addons,
        weekend=weekend, salary_days=salary_days, departments=depts, teams=teams, role_titles=titles,
        lines=lines, source=source, pay=pay, raw=raw)


def _dupes(p, what, ids):
    seen = set()
    for i in ids:
        if i in seen:
            p.append(f"{what}: id «{i}» повторяется")
        seen.add(i)
