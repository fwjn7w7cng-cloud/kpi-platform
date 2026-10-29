"""Модуль «Утверждение планов»: руководитель продаж готовит планы месяца → отправляет директору →
директор ✅ утверждает или ↩️ возвращает с комментарием → каждый торговый получает свой план.

Статус месяца — в настройке planst:<месяц> = {status: draft|wait|back|ok, note, hist: [...]}.
Утверждённые планы правками закрыты; директор может открыть их заново с причиной.
С 25-го числа экран по умолчанию показывает следующий месяц; пустой месяц можно заполнить «как в этом» (+0/5/10%).
"""
from datetime import datetime

from core.calendar import month_of, month_title, shift_month
from core.ui.kit import Button, Reply, money, num, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Cell, Row, View

M = "plans"
ST = {"draft": "черновик", "wait": "⏳ у директора", "back": "↩️ возвращён на доработку", "ok": "✅ утверждён"}
METRICS = [("sales", "Продажи", "продажи"), ("focus", "Фокус", "фокус"), ("akb", "АКБ", "акб"), ("sku", "SKU", "sku"),
           ("collect", "Сбор", "сбор")]
MONEY = {"sales", "focus", "collect"}

menu("pl", "📋 Планы", {"head", "owner", "admin"}, module=M, order=55, when=lambda c: c.role != "head" or c.dept_kind == "sales")


def state(db, month):
    return db.get(f"planst:{month}") or {"status": "draft", "note": "", "hist": []}


def _set(ctx, month, status, note=""):
    st = state(ctx.db, month)
    st["hist"] = (st.get("hist") or [])[-20:] + [{"ts": datetime.now().isoformat(timespec="minutes"), "who": ctx.author(),
                                                  "status": status, "note": note}]
    st["status"], st["note"] = status, note
    ctx.db.put(f"planst:{month}", st)
    ctx.db.audit(ctx.author(), "plans", f"{month} → {status} {note}".strip())


def editable(db, month):
    return state(db, month)["status"] in ("draft", "back")


def _default_month(ctx):
    cur = month_of(ctx.today)
    return shift_month(cur, 1) if ctx.today.day >= 25 else cur


def _reps(ctx, month):
    k = ctx.kpi(month) if month <= month_of(ctx.today) else ctx.kpi()
    dept = next(d for d in ctx.cfg.departments if d.kind == "sales")
    return [i for i in k.members("dept", dept.id) if k.employees[i]["active"]], k


@screen("pl", module=M)
def plans(ctx, month=None):
    if ctx.scope not in ("dept", "all"):
        return Reply(text="Планы готовит руководитель продаж.")
    month = month or _default_month(ctx)
    ids, k = _reps(ctx, month)
    p = ctx.db.plans(month)
    st = state(ctx.db, month)
    v = View(f"📋 Планы · {month_title(month)}", f"статус: {ST[st['status']]}")
    tot = {m: 0 for m, *_ in METRICS}
    rs = []
    for i in ids:
        pp = p.get(i, {})
        for m, *_ in METRICS:
            tot[m] += pp.get(m, 0)
        rs.append(Row([short(k.employees[i]["fio"]), *[money(pp.get(m, 0), "") if m in MONEY else num(pp.get(m, 0))
                                                         for m, *_ in METRICS]]))
    rs.append(Row([Cell("Всего", bold=True), *[Cell(money(tot[m], "") if m in MONEY else num(tot[m]), bold=True)
                                                for m, *_ in METRICS]]))
    v.table(["Менеджер", *[t for _, t, _ in METRICS]], rs)
    if st.get("note"):
        v.note(f"Комментарий: {st['note']}")
    kb = []
    head = ctx.role == "head" or ctx.role == "admin"
    boss = ctx.role in ("owner", "admin")
    empty = not any(p.get(i) for i in ids)
    if editable(ctx.db, month) and head:
        if empty:
            kb.append([Button(f"🧮 Как в {month_title(shift_month(month, -1)).split()[0].lower()} {g}", f"plc:{month}:{x}")
                       for g, x in (("+0%", 0), ("+5%", 5), ("+10%", 10))])
        else:
            kb += rows([Button(f"✏️ {short(k.employees[i]['fio'])}", f"ple:{month}:{i}") for i in ids], 3)
            kb.append([Button("📨 На утверждение", f"pls:{month}")])
    if st["status"] == "wait" and boss:
        kb.append([Button("✅ Утвердить", f"pla:{month}"), Button("↩️ Вернуть", f"plb:{month}")])
    if st["status"] == "ok" and boss:
        kb.append([Button("↩️ Открыть заново", f"plo:{month}")])
    nxt = shift_month(month, 1)
    nav = [Button(f"◀ {month_title(shift_month(month, -1)).split()[0]}", f"pl:{shift_month(month, -1)}")]
    if nxt <= shift_month(month_of(ctx.today), 1):
        nav.append(Button(f"{month_title(nxt).split()[0]} ▶", f"pl:{nxt}"))
    kb.append(nav)
    return Reply(view=v, buttons=kb)


@screen("plc", module=M)
def copy(ctx, month, growth):
    if ctx.role not in ("head", "admin") or not editable(ctx.db, month):
        return Reply(text="Планы этого месяца сейчас не меняются.")
    src = ctx.db.plans(shift_month(month, -1))
    ids, _ = _reps(ctx, month)
    from core.calendar import ramadan, ramadan_share
    g = 1 + int(growth) / 100
    share = ramadan_share(ctx.db, ctx.cfg, month)
    if share:                                   # Рамадан: дни поста — с поправкой плана
        g *= 1 - (1 - ramadan(ctx.db)["plan"]) * share
    for i in ids:
        for m, val in src.get(i, {}).items():
            rnd = -3 if m in MONEY or m.startswith("grp:") else 0
            ctx.db.set_plan(month, i, m, round(val * g, rnd), who=ctx.author(), src="копия")
    if ctx.db.get(f"focus:{shift_month(month, -1)}") and not ctx.db.get(f"focus:{month}"):
        ctx.db.put(f"focus:{month}", ctx.db.get(f"focus:{shift_month(month, -1)}"))
    return plans(ctx, month)


@screen("ple", module=M)
def edit(ctx, month, emp_id):
    if ctx.role not in ("head", "admin") or not editable(ctx.db, month):
        return Reply(text="Планы этого месяца сейчас не меняются.")
    e = ctx.db.employee(int(emp_id))
    pp = ctx.db.plans(month).get(e["id"], {})
    now = ", ".join(f"{t} {money(pp.get(m, 0), '') if m in MONEY else num(pp.get(m, 0))}" for m, t, _ in METRICS)
    return ctx.ask("plan_edit", f"{e['fio']}: {now}.\n\nНапишите новый план: «1 250 000» — продажи, или с показателем: "
                                "«акб 30», «фокус 300 000», «сбор 1 100 000». Можно несколько строк.",
                   {"month": month, "emp": e["id"]})


@text_input("plan_edit")
def on_edit(ctx, text, data):
    names = {w: m for m, _, w in METRICS}
    done = []
    for line in (text or "").splitlines():
        parts = line.strip().lower().split(maxsplit=1)
        if not parts:
            continue
        metric = "sales"
        if parts[0] in names:
            metric, rest = names[parts[0]], parts[1] if len(parts) > 1 else ""
        else:
            rest = line
        try:
            val = float(rest.replace(" ", "").replace("\xa0", "").replace(",", ".").replace("₽", ""))
        except ValueError:
            return ctx.ask("plan_edit", f"Не понял «{line.strip()}». Пример: «1 250 000» или «акб 30».", data)
        ctx.db.set_plan(data["month"], data["emp"], metric, val, who=ctx.author(), src="правка")
        done.append(metric)
    if not done:
        return ctx.ask("plan_edit", "Пусто. Напишите, например, «1 250 000».", data)
    ctx.db.audit(ctx.author(), "plan_edit", f"{data['month']} сотрудник {data['emp']}: {', '.join(done)}")
    return plans(ctx, data["month"])


@screen("pls", module=M)
def send(ctx, month):
    if ctx.role not in ("head", "admin") or not editable(ctx.db, month):
        return Reply(text="Отправить можно черновик или возвращённый план.")
    _set(ctx, month, "wait")
    out = [Reply(text=f"📨 Планы на {month_title(month).lower()} отправлены директору.")]
    for e in ctx.db.employees():
        if e["role"] == "owner" and e["tg_id"]:
            out.append(Reply(text=f"📋 Планы на {month_title(month).lower()} ждут утверждения — от {ctx.author()}.", to=e["tg_id"],
                             buttons=[[Button("📋 Посмотреть", f"pl:{month}")]]))
    out.append(plans(ctx, month))
    return out


@screen("pla", module=M)
def approve(ctx, month):
    if ctx.role not in ("owner", "admin") or state(ctx.db, month)["status"] != "wait":
        return Reply(text="Утверждать нечего.")
    _set(ctx, month, "ok")
    out = [Reply(text=f"✅ Планы на {month_title(month).lower()} утверждены.")]
    p = ctx.db.plans(month)
    for e in ctx.db.employees():
        if not e["tg_id"]:
            continue
        if e["id"] in p and p[e["id"]].get("sales"):
            pp = p[e["id"]]
            lines = [f"{t}: {money(pp[m]) if m in MONEY else num(pp[m])}" for m, t, _ in METRICS if pp.get(m)]
            out.append(Reply(text=f"📋 Ваш план на {month_title(month).lower()}:\n" + "\n".join(lines), to=e["tg_id"]))
        elif e["role"] == "head" and ctx.cfg.dept(e["dept"]) and ctx.cfg.dept(e["dept"]).kind == "sales":
            out.append(Reply(text=f"✅ Директор утвердил планы на {month_title(month).lower()}.", to=e["tg_id"]))
    out.append(plans(ctx, month))
    return out


@screen("plb", module=M)
def back(ctx, month):
    if ctx.role not in ("owner", "admin") or state(ctx.db, month)["status"] != "wait":
        return Reply(text="Возвращать нечего.")
    return ctx.ask("plan_back", "Что поправить? Напишите комментарий — он уйдёт руководителю продаж.", {"month": month, "to": "back"})


@screen("plo", module=M)
def reopen(ctx, month):
    if ctx.role not in ("owner", "admin") or state(ctx.db, month)["status"] != "ok":
        return Reply(text="План не утверждён.")
    return ctx.ask("plan_back", "Почему открываем планы заново? Причина попадёт в журнал.", {"month": month, "to": "draft"})


@text_input("plan_back")
def on_back(ctx, text, data):
    _set(ctx, data["month"], data["to"], text.strip()[:300])
    out = []
    for e in ctx.db.employees():
        if e["role"] == "head" and e["tg_id"] and ctx.cfg.dept(e["dept"]) and ctx.cfg.dept(e["dept"]).kind == "sales":
            out.append(Reply(text=f"↩️ Планы на {month_title(data['month']).lower()}: {text.strip()}", to=e["tg_id"],
                             buttons=[[Button("📋 Планы", f"pl:{data['month']}")]]))
    out.append(plans(ctx, data["month"]))
    return out
