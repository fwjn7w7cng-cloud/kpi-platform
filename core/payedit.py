"""💵 Как считаем зарплату — правка цифр схем клиента из пульта (Адам 20.11: «нет настройки как считать зарплаты»).

Схемы клиента лежат в его config.yaml (pay_schemes). Пульт файл не трогает — правленая схема целиком ложится в базу клиента
(ключ KEY = {схема: [кубики]}) и поверх анкеты в Ctx (`apply_cfg`). Так правка сразу видна в приложении клиента (другой процесс
читает ту же базу) и не пропадает при обновлении кода из git.
Правятся цифры: суммы, проценты, ступени «порог → сумма». Добавить / убрать блок — через конструктор (следующий шаг).
"""
import copy
import dataclasses

from core.config import MONEY_KEYS, PCT_KEYS, _money, _pct
from core.pay import components as C

KEY = "pay_edit"

TYPE_T = {
    "salary": "Оклад", "sales_percent": "% от продаж", "plan_bonus": "Бонус за план", "fixed_bonus": "Постоянный бонус",
    "debt_penalty": "Удержание за просрочку", "debt_bonus": "Бонус за просрочку", "timesheet": "Табель: опоздания и прогулы",
    "per_unit": "Сдельно", "new_clients": "Новые клиенты", "discount_penalty": "Лишние скидки", "rank_bonus": "Место в рейтинге",
    "seniority": "Стаж", "growth_bonus": "Рост к прошлому году", "kpi_card": "KPI-карта", "orders": "Заказы и средний чек",
    "team_days": "Команда отработала месяц", "old_debt": "Сбор старых долгов", "period_bonus": "За квартал / год",
    "awards": "Значки", "pool": "Котёл отдела", "fuel_comp": "ГСМ", "multiplier": "Множитель", "limits": "Гарантия и потолок",
}
KEY_T = {"amount": "сумма", "rate": "ставка", "rate_above": "ставка сверх плана", "share": "доля", "min": "от", "cap": "до",
         "debt_norm": "норма просрочки", "cut_per_miss": "минус за пропуск", "salary": "оклад", "price": "цена за единицу",
         "max": "не больше", "akb_min": "порог АКБ", "absent_fine": "за прогул"}
METRIC_T = {"sales": "продажи", "akb": "АКБ", "sku": "SKU", "collect": "сбор", "focus": "фокусные", "visits": "визиты"}
SCOPE_T = {"team": "команды", "dept": "отдела", "all": "фирмы"}


def pct(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v * 100:.4f}".rstrip("0").rstrip(".").replace(".", ",") + "%"
    return str(v)


def rub(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{int(v):,}".replace(",", " ") if v == int(v) else f"{v:g}".replace(".", ",")
    return str(v)


def human(k, v):
    return pct(v) if k in PCT_KEYS else rub(v) if k in MONEY_KEYS else str(v)


def title(c):
    t = TYPE_T.get(c.get("type"), c.get("type", ""))
    if c.get("type") == "per_unit":
        t = c.get("name") or "Сдельно: " + C.UNIT_TITLES.get(c.get("unit"), (c.get("unit", ""), ""))[0]
    elif c.get("type") == "fixed_bonus" and c.get("name"):
        t = c["name"]
    elif c.get("type") == "plan_bonus":
        m = str(c.get("metric", "sales"))
        t += " · " + METRIC_T.get(m, m.replace("grp:", "группа "))
    if c.get("scope") in SCOPE_T:
        t += " " + SCOPE_T[c["scope"]]
    return t


def describe(c):
    """«% от продаж 0,4%», «Бонус за план · продажи: 80% → 5 000, 100% → 15 000»."""
    parts = [human(k, c[k]) if k in ("amount", "rate", "price") else f"{KEY_T[k]} {human(k, c[k])}"
             for k in c if k in KEY_T]
    if isinstance(c.get("steps"), list):
        parts += [f"{pct(s[0])} → {rub(s[1])}" for s in c["steps"] if isinstance(s, (list, tuple)) and len(s) == 2]
    return title(c) + (": " + ", ".join(parts) if parts else "")


def summary(comps):
    return " · ".join(describe(c) for c in comps if isinstance(c, dict) and c.get("type") != "timesheet") or "без начислений"


def schemes(cfg):
    return cfg.raw.get("pay_schemes") or {}


def who_uses(cfg):
    """{схема: [названия должностей]} — только схемы, которыми кто-то пользуется."""
    out = {}
    for p in cfg.positions:
        s = p.get("pay") or "none"
        if s != "none" and s in schemes(cfg):
            out.setdefault(s, []).append(p.get("name") or p["id"])
    return out


def fields(comps):
    """Поля формы: каждая цифра схемы отдельным полем «<№>.<ключ>», ступени — «<№>.s<j>.0 / .1»."""
    out = []
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            continue
        t = title(c)
        for k in c:
            if k in KEY_T and not isinstance(c[k], (list, dict)):
                out.append({"k": f"{i}.{k}", "l": f"{t} — {KEY_T[k]}", "v": human(k, c[k]), "num": True})
        for j, s in enumerate(c.get("steps") or []):
            if isinstance(s, (list, tuple)) and len(s) == 2:
                out.append({"k": f"{i}.s{j}.0", "l": f"{t} — ступень {j + 1}: от", "v": pct(s[0]), "num": True})
                out.append({"k": f"{i}.s{j}.1", "l": f"{t} — ступень {j + 1}: сумма", "v": rub(s[1]), "num": True})
    return out


def apply_form(comps, form):
    """Новые кубики по форме и список ошибок (тогда ничего не сохраняем)."""
    new, p = copy.deepcopy(comps), []
    for key, val in (form or {}).items():
        val = str(val).strip()
        bits = str(key).split(".")
        if not bits[0].isdigit() or int(bits[0]) >= len(new) or not isinstance(new[int(bits[0])], dict) or not val:
            continue
        c = new[int(bits[0])]
        where = title(c)
        if len(bits) == 2 and bits[1] in c and bits[1] in KEY_T:
            k = bits[1]
            if k in PCT_KEYS:
                c[k] = _pct(val if val.endswith("%") else val + "%", f"{where} — {KEY_T[k]}", p)
            else:
                c[k] = _money(val, f"{where} — {KEY_T[k]}", p)
        elif len(bits) == 3 and bits[1].startswith("s") and bits[1][1:].isdigit() and bits[2] in ("0", "1"):
            j = int(bits[1][1:])
            steps = c.get("steps") or []
            if j < len(steps):
                steps[j] = list(steps[j])
                steps[j][int(bits[2])] = (_pct(val if val.endswith("%") else val + "%", f"{where} — ступень {j + 1}", p)
                                          if bits[2] == "0" else _money(val, f"{where} — ступень {j + 1}", p))
    for c in new:
        if isinstance(c, dict):
            for k in c:
                if k in KEY_T and isinstance(c[k], (int, float)) and c[k] < 0:
                    p.append(f"{title(c)} — {KEY_T[k]}: не меньше нуля")
            if isinstance(c.get("steps"), list):
                c["steps"] = sorted(c["steps"], key=lambda s: s[0] if isinstance(s[0], (int, float)) else 0)
            if c.get("type") in C.BUILTIN or c.get("type") in C.POST:
                p += [f"{title(c)}: {x}" for x in C.check(c)]
    return new, p


def apply_cfg(cfg, db):
    """Анкета с правками схем из пульта."""
    ed = db.get(KEY) or {}
    if not ed:
        return cfg
    raw = dict(cfg.raw)
    raw["pay_schemes"] = {**(raw.get("pay_schemes") or {}), **{k: v for k, v in ed.items() if isinstance(v, list)}}
    return dataclasses.replace(cfg, raw=raw)
