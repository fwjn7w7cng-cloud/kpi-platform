"""Конфигурация клиента: clients/<id>/config.yaml → ClientConfig.

Всё, чем один клиент отличается от другого без кода, живёт здесь. Ошибки конфига пишутся
по-русски и все сразу, чтобы Адам поправил файл за один заход.
"""
import re
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
    holidays: dict = field(default_factory=dict)
    positions: list = field(default_factory=list)
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def modules(self):
        """Все модули, доступные клиенту: редакция + докупленные."""
        return E.edition_modules(self.edition) | set(self.addons)

    def has_module(self, code):
        return code in self.modules

    def position(self, pos_id):
        return next((p for p in self.positions if p["id"] == pos_id), None)

    def with_edition(self, edition, addons=None):
        """Копия анкеты с другой редакцией (демо: переключатель редакций)."""
        import copy
        c = copy.copy(self)
        c.edition = edition
        c.addons = list(self.addons if addons is None else addons)
        return c

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


def load_client(client_id, path=None, hooks=None):
    """Прочитать и проверить конфиг. Бросает ConfigError со списком всех проблем.
    hooks — крючки клиента (из его rules.py): нужны, чтобы признать его собственные кубики зарплаты."""
    path = Path(path) if path else client_dir(client_id) / "config.yaml"
    if not path.exists():
        raise ConfigError(client_id, [f"нет файла {path}"])
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(client_id, [f"файл не читается как YAML: {e}"])
    return parse(client_id, raw, hooks)


PCT_KEYS = {"rate", "rate_above", "share", "min", "cap"}       # доли: в анкете — «0,4%», «80%»
MONEY_KEYS = {"amount", "cut_per_miss", "salary", "akb_min", "absent_fine"}   # суммы: «25 000»


def _pct(v, where, p):
    """«0,4%» → 0.004; число оставляем как есть (старая запись долей: 0.004)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v).replace(" ", "").replace("\xa0", "").replace("\u202f", "").replace(",", ".")
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100
        except ValueError:
            pass
        p.append(f"{where} «{v}» — не понял число, пример: «0,4%»")
        return v
    p.append(f"{where} «{v}» — напишите со знаком %, например «0,4%» или «80%»")
    return v


def _money(v, where, p):
    """«25 000» / «25 000 ₽» → 25000."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v).replace(" ", "").replace("\xa0", "").replace("\u202f", "").replace("₽", "").replace("руб", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")                 # «25,000» — так таблица с английскими настройками пишет 25 000
    s = s.replace(",", ".")
    try:
        x = float(s)
        return int(x) if x == int(x) else x
    except ValueError:
        p.append(f"{where} «{v}» — нужна сумма, например «25 000»")
        return v


def human_numbers(raw, p):
    """Перевести человеческую запись в анкете (проценты со знаком %, суммы с пробелами) в числа.
    Меняет копию анкеты; ошибки дописывает в p."""
    import copy
    raw = copy.deepcopy(raw)
    for sname, comps in (raw.get("pay_schemes") or {}).items():
        for i, c in enumerate(comps or []):
            if not isinstance(c, dict):
                continue
            where = f"pay_schemes.{sname}[{i + 1}]"
            for k in list(c):
                if k in PCT_KEYS:
                    c[k] = _pct(c[k], f"{where}.{k}", p)
                elif k in MONEY_KEYS:
                    c[k] = _money(c[k], f"{where}.{k}", p)
            if isinstance(c.get("steps"), list):
                c["steps"] = [[_pct(s[0], f"{where}.steps", p), _money(s[1], f"{where}.steps", p)]
                              if isinstance(s, (list, tuple)) and len(s) == 2 else s for s in c["steps"]]
    for lid, line in (raw.get("lines") or {}).items():
        if isinstance(line, dict) and "rate" in line:
            line["rate"] = _pct(line["rate"], f"lines.{lid}.rate", p)
    for sec in ("kpi", "pay", "timesheet"):
        d = raw.get(sec)
        if isinstance(d, dict):
            for k in list(d):
                if k in MONEY_KEYS:
                    d[k] = _money(d[k], f"{sec}.{k}", p)
    ts = raw.get("timesheet") or {}
    if isinstance(ts.get("late"), list):
        ts["late"] = [[x[0], _money(x[1], "timesheet.late", p)] if isinstance(x, (list, tuple)) and len(x) == 2 else x
                      for x in ts["late"]]
    return raw


def parse(client_id, raw, hooks=None):
    p = []   # проблемы
    raw = human_numbers(raw, p)
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

    holidays = week.get("holidays") or {}
    for d, kind in holidays.items():
        try:
            from datetime import date as _date
            _date.fromisoformat(str(d))
        except ValueError:
            p.append(f"work_week.holidays: «{d}» — дата в виде 2026-11-04")
        if kind not in ("paid", "unpaid"):
            p.append(f"work_week.holidays.{d}: «{kind}» — paid или unpaid")

    schemes = raw.get("pay_schemes") or {}
    from core.pay import components as C
    custom = set((hooks.components if hooks else {}).keys())
    for sname, comps in schemes.items():
        for i, comp in enumerate(comps or []):
            t = (comp or {}).get("type")
            where = f"pay_schemes.{sname}[{i + 1}]"
            if t in C.BUILTIN:
                p += [f"{where}: {x}" for x in C.check(comp)]
            elif t in custom:
                chk = hooks.components[t][1]
                p += [f"{where}: {x}" for x in (chk(comp) if chk else [])]
            else:
                p.append(f"{where}: нет кубика «{t}» — есть: {', '.join(sorted(set(C.BUILTIN) | custom))}")

    positions = []
    for pos in raw.get("positions") or []:
        pid = pos.get("id", "")
        if pos.get("dept") not in dept_ids:
            p.append(f"должность «{pid}»: нет отдела «{pos.get('dept')}»")
        if pos.get("role") not in R.ROLES:
            p.append(f"должность «{pid}»: нет роли «{pos.get('role')}» — роли: {', '.join(R.ROLES)}")
        if pos.get("pay", "none") not in schemes and pos.get("pay", "none") != "none":
            p.append(f"должность «{pid}»: нет схемы зарплаты «{pos.get('pay')}» в pay_schemes")
        positions.append({"id": pid, "name": pos.get("name", pid), "dept": pos.get("dept"),
                          "role": pos.get("role"), "pay": pos.get("pay", "none")})
    _dupes(p, "должности", [x["id"] for x in positions])

    from core.kpi import formula
    names = {"sales", "akb", "sku", "collect", "focus", "visits", "overdue"}
    for code, f in (raw.get("formulas") or {}).items():
        err = formula.check(str((f or {}).get("expr", "")), names)
        if err:
            p.append(f"formulas.{code}: {err}")

    ts = raw.get("timesheet") or {}
    for pair in ts.get("late") or []:
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, (int, float)) for x in pair)):
            p.append("timesheet.late — список пар [минут, штраф], например [[10, 100], [30, 200]]")
            break

    for key, val in (raw.get("schedule") or {}).items():
        if val and not _is_time(str(val)):
            p.append(f"schedule.{key} «{val}» — время в виде 08:00")

    if p:
        raise ConfigError(client_id, p)
    return ClientConfig(
        id=client_id, name=name, industry=industry, timezone=tz, edition=edition, addons=addons,
        weekend=weekend, salary_days=salary_days, departments=depts, teams=teams, role_titles=titles,
        lines=lines, source=source, pay=pay, holidays={str(k): v for k, v in holidays.items()},
        positions=positions, raw=raw)


def _is_time(s):
    try:
        h, m = s.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False


def _dupes(p, what, ids):
    seen = set()
    for i in ids:
        if i in seen:
            p.append(f"{what}: id «{i}» повторяется")
        seen.add(i)
