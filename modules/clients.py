"""Модуль «Клиенты»: кто перестал брать, кто берёт меньше, кто растёт, какие бренды бросили.
Карточка клиента — по кнопке или если просто написать боту код клиента (или часть названия).

Как считаем (база — последние 3 месяца, текущий — с поправкой на прошедшую долю месяца):
  😴 спящие   покупали минимум в 2 из 3 прошлых месяцев, в этом — ни разу (с трети месяца)
  📉 падают   берут меньше 60% от обычного (средний прошлый месяц × пройденная доля месяца)
  📈 растут   берут больше 150% от обычного и минимум на порог АКБ больше
  🧩 бренды   клиент покупает, но группу, которую брал все 3 прошлых месяца (заметную — от 10% его покупок),
              в этом месяце не взял (с половины месяца)
Клиент закреплён за тем, у кого он в маршруте этого месяца, иначе — за тем, кто продавал ему последним.
"""
from collections import defaultdict
from datetime import date

from core.calendar import day_title, month_title, shift_month
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money, pct, rows
from core.ui.registry import free_text, menu, screen
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "clients"
CATS = {"s": ("😴 Спящие", "перестали брать"), "f": ("📉 Падают", "берут меньше обычного"),
        "g": ("📈 Растут", "берут больше обычного"), "b": ("🧩 Бросили бренд", "покупают, но не эту группу")}
FALL, GROW = 0.6, 1.5

menu("cli", "😴 Клиенты", {"rep", "newbie", "sv", "head", "owner", "admin"}, module=M, order=14,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


class History:
    """Покупки клиентов за 3 прошлых месяца и текущий, по клиенту и группам."""

    def __init__(self, ctx):
        k = ctx.kpi()
        self.k = k
        self.prev = [shift_month(ctx.month, -i) for i in (3, 2, 1)]
        self.akb_min = k.akb_min
        self.p = k.cal.need if ctx.is_current() else 1.0
        self.by = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])           # клиент → [м-3, м-2, м-1, текущий]
        self.groups = defaultdict(lambda: defaultdict(lambda: [0.0] * 4))
        self.last_seller = {}
        for i, month in enumerate(self.prev + [ctx.month]):
            km = ctx.kpi(month)
            for emp, cl in km.by_client.items():
                for c, v in cl.items():
                    self.by[c][i] += v
                    if v > 0:
                        self.last_seller[c] = emp
            for r in ctx.db.c.execute("SELECT emp_key, client, grp, day, amount FROM sales WHERE day BETWEEN ? AND ?",
                                      (km.start, km.end)):
                self.groups[r[1]][r[2]][i] += r[4]
        self.owner = dict(self.last_seller)
        for emp, pts in k.route.items():
            for _, c in pts:
                self.owner[c] = emp

    def visible(self, ctx, emp_ids=None):
        """Клиенты, которых зритель может видеть (и, если задано, только этих сотрудников)."""
        out = []
        for c, emp in self.owner.items():
            if emp_ids is not None and emp not in emp_ids:
                continue
            if ctx.sees(emp):
                out.append(c)
        return out

    def base(self, c):
        prev = [x for x in self.by[c][:3] if x > 0]
        return sum(prev) / len(prev) if len(prev) >= 2 else 0.0

    def cat(self, c):
        """Список категорий клиента: [(код, ценность для сортировки, пояснение)]."""
        v = self.by[c]
        cur, base, p = v[3], self.base(c), self.p
        out = []
        if sum(1 for x in v[:3] if x > 0) >= 2 and cur <= 0 and p >= 0.33:
            out.append(("s", base, f"обычно {money(base)} в месяц, в этом — ни одной покупки"))
        if base >= self.akb_min and cur > 0 and p >= 0.15:
            ratio = cur / (base * p)
            if ratio < FALL:
                out.append(("f", base * p - cur, f"обычно {money(base)}/мес · сейчас {money(cur)} — {pct(ratio)} от темпа"))
            elif ratio > GROW and cur - base * p >= self.akb_min:
                out.append(("g", cur - base * p, f"обычно {money(base)}/мес · сейчас {money(cur)} — {pct(ratio)} от темпа"))
        if cur > 0 and p >= 0.5:
            lost = [(g, sum(a[:3]) / 3) for g, a in self.groups[c].items()
                    if g and min(a[:3]) > 0 and a[3] <= 0 and sum(a[:3]) / 3 >= 0.1 * base]
            if lost:
                lost.sort(key=lambda x: -x[1])
                names = ", ".join(self.k.group_name(g) for g, _ in lost[:3])
                out.append(("b", sum(x for _, x in lost), f"не взял: {names}"))
        return out

    def all(self, ctx, emp_ids=None):
        res = defaultdict(list)          # категория → [(клиент, ценность, пояснение)]
        for c in self.visible(ctx, emp_ids):
            for code, val, note in self.cat(c):
                res[code].append((c, val, note))
        for code in res:
            res[code].sort(key=lambda x: -x[1])
        return res


def history(ctx):
    return ctx.app.memo(ctx, "clients", lambda: History(ctx))


def _scope(ctx, kind=None, ident=None):
    """Чьи клиенты: e:<сотрудник> | t:<команда> | d (отдел) — по правам зрителя."""
    k = ctx.kpi()
    if not kind:
        if ctx.role in SELLERS and ctx.me:
            kind, ident = "e", str(ctx.me["id"])
        elif ctx.role == "sv" and ctx.me:
            kind, ident = "t", ctx.me["team"]
        else:
            kind = "d"
    if kind == "e":
        ids = [int(ident)]
        title = k.employees[int(ident)]["fio"] if int(ident) in k.employees else ""
    elif kind == "t":
        ids = k.members("team", ident)
        title = next((t.name for t in ctx.cfg.teams if t.id == ident), "")
    else:
        dept = next(d for d in ctx.cfg.departments if d.kind == "sales")
        ids, title = k.members("dept", dept.id), dept.name
    ids = {i for i in ids if ctx.sees(i)}
    return kind, ident, ids, title


@screen("cli", module=M)
def clients(ctx, kind=None, ident=None):
    kind, ident, ids, title = _scope(ctx, kind, ident)
    if not ids:
        return Reply(text="Нет доступа.")
    h = history(ctx)
    res = h.all(ctx, ids)
    v = View(f"😴 Клиенты · {title}", f"{month_title(ctx.month).lower()} · к прошлым 3 месяцам")
    v.tiles([(CATS[c][0], f"{len(res.get(c, []))}", "bad" if c in "sf" and res.get(c) else "good" if c == "g" else None)
             for c in "sfgb"])
    lost = sum(x[1] for x in res.get("s", [])) + sum(x[1] for x in res.get("f", []))
    if lost:
        v.lines([f"Недобор от спящих и падающих: ≈ {money(lost)} в этом месяце"])
    if h.p < 0.33 and ctx.is_current():
        v.note("Начало месяца: спящих покажем с трети месяца, брошенные бренды — с половины.")
    scope = f"{kind}:{ident}" if kind != "d" else "d:-"
    if kind != "e":
        per = defaultdict(lambda: [0, 0, 0, 0])
        for ci, c in enumerate("sfgb"):
            for cl, *_ in res.get(c, []):
                per[h.owner[cl]][ci] += 1
        v.table(["Менеджер", "Спят", "Падают", "Растут", "Бренд"],
                [Row([short(ctx.kpi().employees[i]["fio"]), *[Cell(str(n), "bad" if n and j < 2 else None)
                                                             for j, n in enumerate(per[i])]])
                 for i in sorted(ids, key=lambda i: -(per[i][0] + per[i][1]))], title="По менеджерам")
    kb = [[Button(CATS[c][0], f"cll:{c}:{scope}") for c in "sf"], [Button(CATS[c][0], f"cll:{c}:{scope}") for c in "gb"]]
    if kind != "e":
        kb += rows([Button(short(ctx.kpi().employees[i]["fio"]), f"cli:e:{i}") for i in sorted(ids)], 3)
    v.note("Карточка клиента: напишите боту его код или часть названия.")
    return Reply(view=v, buttons=kb)


@screen("cll", module=M)
def cat_list(ctx, cat, kind, ident):
    kind, ident, ids, title = _scope(ctx, kind, None if ident == "-" else ident)
    if not ids or cat not in CATS:
        return Reply(text="Нет доступа.")
    h = history(ctx)
    items = h.all(ctx, ids).get(cat, [])
    v = View(f"{CATS[cat][0]} · {title}", f"{CATS[cat][1]} · {len(items)}")
    cards = []
    for c, val, note in items[:15]:
        cl = _client(ctx, c)
        lines = [note] + ([f"{cl['town']} · {short(ctx.kpi().employees[h.owner[c]]['fio'])}"] if kind != "e" else
                          ([cl["town"]] if cl["town"] else []))
        cards.append(Card(cl["name"], money(val) if cat != "b" else "", lines, phone=cl["phone"],
                          color="bad" if cat in "sf" else "good" if cat == "g" else "warn"))
    v.cards(cards)
    if not items:
        v.note("Таких клиентов нет 👍" if cat in "sfb" else "Пока нет.")
    elif len(items) > 15:
        v.note(f"Показаны 15 самых крупных из {len(items)}.")
    kb = rows([Button(f"🔎 {_client(ctx, c)['name'][:18]}", f"clc:{c}") for c, *_ in items[:9]], 3)
    kb.append([Button("‹ Клиенты", f"cli:{kind}:{ident}" if kind != "d" else "cli:d")])
    return Reply(view=v, buttons=kb)


def _client(ctx, code):
    return ctx.db.one("SELECT code, name, town, phone FROM clients WHERE code = ?", code) or \
        {"code": code, "name": code, "town": "", "phone": ""}


@screen("clc", module=M)
def card(ctx, code, h=None):
    h = h or history(ctx)
    owner = h.owner.get(code)
    if owner is None or not ctx.sees(owner):
        return Reply(text="Такого клиента нет среди ваших.")
    cl = _client(ctx, code)
    k = ctx.kpi()
    v = View(f"🏪 {cl['name']}", " · ".join(x for x in (f"код {code}", cl["town"], short(k.employees[owner]["fio"])) if x))
    vals = h.by[code]
    months = h.prev + [ctx.month]
    v.tiles([("Этот месяц", money(vals[3]), None), ("Обычно в месяц", money(h.base(code)), None)] +
            ([("Телефон", cl["phone"], None)] if cl["phone"] else []))
    v.table(["Месяц", "Покупки"], [Row([month_title(m), money(x)]) for m, x in zip(months, vals)], title="По месяцам")
    gs = sorted(h.groups[code].items(), key=lambda t: -sum(t[1]))
    if gs:
        v.table(["Группа", "Прошлый", "Этот"], [Row([k.group_name(g), money(a[2], ""), Cell(money(a[3], ""),
                                                     "bad" if a[2] > 0 and a[3] <= 0 else None)]) for g, a in gs[:10]],
                title="Что берёт")
    last = ctx.db.one("SELECT MAX(day) AS d FROM sales WHERE client = ? AND amount > 0", code)
    debt = [r for r in k.debts.get(owner, []) if r["client"] == code]
    notes = []
    if last and last["d"]:
        notes.append(f"Последняя покупка: {day_title(date.fromisoformat(last['d']))}")
    if debt:
        d = debt[0]
        notes.append(f"Долг {money(d['total'])}, просрочка 30+: {money(d['d31_60'] + d['d61_90'] + d['d91'])}")
    for c, _, note in h.cat(code):
        notes.append(f"{CATS[c][0]}: {note}")
    if notes:
        v.lines(notes)
    return Reply(view=v, buttons=[[Button("‹ Клиенты", "cli")]])


@free_text(module=M, order=20)
def find(ctx, text):
    """Код клиента (3–8 цифр) или часть названия (от 3 букв) → карточка или список."""
    t = (text or "").strip()
    if not t or ctx.role not in {"rep", "newbie", "sv", "head", "owner", "admin"}:
        return None
    h = None
    if t.isdigit() and 3 <= len(t) <= 8:
        h = history(ctx)
        if t in h.owner and ctx.sees(h.owner[t]):
            return card(ctx, t, h)
        if ctx.db.one("SELECT 1 AS x FROM clients WHERE code = ?", t):
            return Reply(text=f"Клиент с кодом {t} — не из ваших.")
        return None
    if len(t) < 3 or len(t) > 40:
        return None
    rs = ctx.db.q("SELECT code, name FROM clients WHERE name LIKE ? LIMIT 60", f"%{t}%")
    if not rs:
        rs = [r for r in ctx.db.q("SELECT code, name FROM clients") if t.lower() in r["name"].lower()][:60]
    if not rs:
        return None
    h = h or history(ctx)
    rs = [r for r in rs if r["code"] in h.owner and ctx.sees(h.owner[r["code"]])]
    if not rs:
        return None
    if len(rs) == 1:
        return card(ctx, rs[0]["code"], h)
    return Reply(text=f"Нашёл {len(rs)}:", buttons=rows([Button(f"{r['name'][:28]}", f"clc:{r['code']}") for r in rs[:12]], 1))
