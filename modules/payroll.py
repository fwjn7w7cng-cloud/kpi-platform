"""Модуль «Предварительный расчёт зарплаты»: моя зарплата строка за строкой, фонд по команде/отделу/компании,
правки (премия или удержание с причиной).

Правки: без модуля «Цепочка утверждения» правку вносит руководитель компании или админ — сразу принята.
С модулем — руководитель отдела и супервайзер предлагают, руководитель компании решает (modules/approval).
"""
from core.calendar import month_title
from core.kpi.engine import SELLERS
from core.month import cycle
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Cell, Row, View

M = "payroll"

menu("pay", "💰 Моя зарплата", {"rep", "newbie", "sv", "staff", "head", "accountant"}, module=M, order=15,
     when=lambda c: c.me is not None)
menu("fund", "💰 Фонд зарплаты", {"head", "owner", "accountant", "admin"}, module=M, order=40)


@screen("pay", module=M)
def payslip(ctx, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    slip = ctx.pay().calc(emp)
    pos = ctx.cfg.position(emp["position"])
    st = cycle.status(ctx.db, ctx.month)
    v = View(f"💰 {emp['fio']}", f"{pos['name'] if pos else ''} · {month_title(ctx.month).lower()} · "
                                f"{'утверждено' if st == 'approved' else 'предварительно'}")
    from core.ui import live                                  # Адам 17.11: зарплата строками, нажал — объяснение
    own = bool(ctx.me and emp["id"] == ctx.me["id"])
    live.hero(v, f"{'Моя зарплата' if own else short(emp['fio'])} · {month_title(ctx.month).lower()}" +
              (", утверждено" if st == "approved" else ", на сегодня"), money(slip.total),
              f"{pos['name'] + ' · ' if pos and not own else ''}отработано {slip.days:g} из {slip.days_total} дн. · нажмите на строку — объясню",
              ident="pay")
    if slip.rows:
        live.explain(v, slip)
    if not slip.rows:
        v.note("Для этой должности зарплата в боте не считается.")
    elif st != "approved":
        v.note("Расчёт предварительный: обновляется с каждым отчётом. Официальная сумма — после утверждения месяца.")
    if own and slip.rows and st != "approved":
        live.disagree(v, ctx, emp, sent=bool(ctx.session.get(f"lvq:{ctx.month}")))
    kb = []
    if emp["role"] in SELLERS:
        kb.append([Button("📈 План", f"emp:{emp['id']}")])
    if _may_edit(ctx) and st != "approved" and (not ctx.me or emp["id"] != ctx.me["id"]):
        kb.append([Button("✏️ Премия / удержание", f"eda:{emp['id']}")])
    kb.append([Button("🕓 Как менялась зарплата", f"pch:{emp['id']}")])     # Адам 19.11
    if not emp_id:                                            # своя зарплата: месяц листается здесь (Адам 17.11 — не на главной)
        from core.ui.screens import _month_switch
        kb.append(_month_switch(ctx, "pay"))
    return Reply(view=v, buttons=kb)


@screen("fund", module=M)
def fund(ctx, kind=None, ident=None):
    p = ctx.pay()
    es = [e for e in ctx.kpi().employees.values() if ctx.sees(e)]
    es = [e for e in es if e["active"] or p.calc(e).total]
    if ctx.scope == "dept" and ctx.me and not kind:
        kind, ident = "dept", ctx.me["dept"]
    if ctx.scope == "team" and ctx.me:
        kind, ident = "t", ctx.me["team"]
    if kind == "t":
        es = [e for e in es if e["team"] == ident]
    elif kind == "dept":
        es = [e for e in es if e["dept"] == ident]
    total = sum(p.calc(e).total for e in es)
    title = {"t": next((t.name for t in ctx.cfg.teams if t.id == ident), ""),
             "dept": (ctx.cfg.dept(ident).name if ident and ctx.cfg.dept(ident) else "")}.get(kind, "вся компания")
    v = View("💰 Фонд зарплаты", f"{title} · {month_title(ctx.month).lower()}")
    from core.ui import live                                  # Адам 17.11: живой экран
    st = cycle.status(ctx.db, ctx.month)
    live.hero(v, f"Фонд зарплаты · {title} · {month_title(ctx.month).lower()}", money(total),
              f"{len(es)} чел. · " + {"approved": "месяц утверждён", "review": "месяц на согласовании"}.get(st, "месяц открыт, цифры обновляются"),
              ident="fund")
    if not kind and ctx.scope == "all":
        parts = [(d, [e for e in es if e["dept"] == d.id]) for d in ctx.cfg.departments]
        parts = [(d, ps, sum(p.calc(e).total for e in ps)) for d, ps in parts if ps]
        mx = max([x[2] for x in parts] or [1]) or 1
        # отдел не в наборе, а в нём директор / бухгалтер (они есть всегда) — так и подписываем (Адам 17.11)
        v.ui("rows", {"items": [{"t": d.name if ctx.cfg.dept_on(d.id) else "Руководство", "s": f"{len(ps)} чел.", "v": money(s), "bar": [s / mx, ""], "chip": ["›", f"fund:dept:{d.id}", ""]}
                                for d, ps, s in parts]}, "По отделам",
             fallback=[f"{d.name}: {money(s)} · {len(ps)} чел." for d, ps, s in parts])
        kb = []
    else:
        es.sort(key=lambda e: -p.calc(e).total)
        v.ui("rows", {"items": [{"t": e["fio"], "s": (ctx.cfg.position(e["position"]) or {}).get("name", ""), "v": money(p.calc(e).total),
                                 "chip": ["›", f"pay:{e['id']}", ""]} for e in es]}, "Люди",
             fallback=[f"{e['fio']}: {money(p.calc(e).total)}" for e in es])
        kb = []
        if ctx.scope == "all":
            kb.append([Button("‹ Все отделы", "fund")])
    kb.append([Button("✏️ Правки", "edits")] if _may_edit(ctx) or ctx.role in ("owner", "admin") else [])
    if not kind or ctx.scope != "all":
        from core.ui.screens import _month_switch
        kb.append(_month_switch(ctx, "fund"))
    return Reply(view=v, buttons=[r for r in kb if r])


# ---------- 🕓 как менялась (Адам 19.11, core/paysnap) ----------

@screen("pch", module=M)
def pay_changes(ctx, emp_id, sid=None):
    from core import paysnap as PS
    from core.ui import live
    emp = ctx.db.employee(int(emp_id)) if str(emp_id).isdigit() else None
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    own = bool(ctx.me and emp["id"] == ctx.me["id"])
    lst, s, total, rows, facts = PS.compare(ctx, emp, sid)
    v = View("🕓 Как менялась", f"{'моя зарплата' if own else emp['fio']} · {month_title(ctx.month).lower()}")
    back = [[Button("‹ К зарплате", "pay" if own else f"pay:{emp['id']}")]]
    if not s:
        live.hero(v, "Сейчас", money(total), "снимков пока нет", ident="pch")
        v.note(f"Бот запоминает расчёт каждый час — первые снимки появятся в течение часа и хранятся {PS.KEEP_DAYS} дня."
               if not ctx.demo else "История есть только у текущего месяца.")
        return Reply(view=v, buttons=back)
    delta = total - s["total"]
    sign = f"▲ +{money(delta)}" if delta > 0 else f"▼ −{money(-delta)}" if delta < 0 else "без изменений"
    live.hero(v, f"{PS.tlabel(ctx, s['ts'])} → сейчас", money(total), f"было {money(s['total'])} · {sign}", ident="pch")
    v.ui("choice", {"items": [[PS.tlabel(ctx, x["ts"]), f"pch:{emp['id']}:{x['id']}", x["id"] == s["id"]]
                              for x in reversed(lst[-PS.SHOW:])]},
         f"С какого времени сравнить · хранится {PS.KEEP_DAYS} дня",
         fallback=[PS.tlabel(ctx, x["ts"]) for x in reversed(lst[-PS.SHOW:])])
    ch = PS.diff(s["rows"], rows)
    if ch:
        v.ui("rows", {"items": [{"t": n, "s": f"{money(a)} → {money(b)}",
                                 "chip": [f"▲ +{money(b - a)}" if b > a else f"▼ −{money(a - b)}", "", "good" if b > a else "bad"]}
                                for n, a, b in ch]}, "Что изменилось в расчёте",
             fallback=[f"{n}: {money(a)} → {money(b)}" for n, a, b in ch])
    else:
        v.note("Строки расчёта не менялись.")
    fc = PS.fact_changes(s.get("facts") or {}, facts)
    if fc:
        def f(x, u):
            return f"{x:g} %".replace(".", ",") if u == "%" else money(x) if u == "₽" else f"{x:g} {u}"
        v.ui("rows", {"items": [{"t": t, "s": f"{f(a, u)} → {f(b, u)}",
                                 "chip": [("▲ +" if b > a else "▼ −") + (f(abs(b - a), u)), "",
                                          ("bad" if b > a else "good") if code_bad else ("good" if b > a else "bad")]}
                                for t, a, b, u in fc for code_bad in [u == "%"]]}, "Что изменилось в цифрах",
             fallback=[f"{t}: {f(a, u)} → {f(b, u)}" for t, a, b, u in fc])
    ev = PS.events(ctx, emp, s)
    if ev:
        v.lines(ev, title="Что пришло за это время")
    if ctx.demo:
        v.note("В демо история — расчёт вечером каждого из прошлых дней. У настоящей фирмы бот запоминает его каждый час.")
    return Reply(view=v, buttons=back)


# ---------- правки ----------

def _may_edit(ctx):
    if ctx.role in ("owner", "admin"):
        return True
    return ctx.can("approval") and ctx.role in ("head", "sv")


@screen("edits", module=M)
def edits(ctx):
    names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
    es = [e for e in cycle.edits(ctx.db, ctx.month) if ctx.sees(e["emp_id"])]
    v = View("✏️ Премии и удержания", month_title(ctx.month))
    icon = {"wait": "⏳", "ok": "✅", "no": "❌"}
    v.table(["Сотрудник", "Сумма", ""], [Row([short(names.get(e["emp_id"], "?")), Cell(money(e["amount"], ""),
                                              "good" if e["amount"] > 0 else "bad"), icon[e["status"]]],
                                             sub=f"{e['reason']} · {e['author']}") for e in es])
    if not es:
        v.note("Правок в этом месяце нет.")
    kb = []
    if ctx.role in ("owner", "admin") and ctx.can("approval") and any(e["status"] == "wait" for e in es):
        kb.append([Button("✅ Решить по правкам", "appr")])
    if _may_edit(ctx) and cycle.can_edit(ctx.db, ctx.month):
        kb.append([Button("➕ Новая правка", "eda")])
    return Reply(view=v, buttons=kb)


@screen("eda", module=M)
def edit_add(ctx, emp_id=None):
    if not _may_edit(ctx):
        return Reply(text="Правки вносит руководитель.")
    if not cycle.can_edit(ctx.db, ctx.month):
        return Reply(text="Месяц утверждён — правки закрыты.")
    if not emp_id:
        es = [e for e in ctx.db.employees() if ctx.sees(e) and (not ctx.me or e["id"] != ctx.me["id"])]
        return Reply(text="Кому правка?", buttons=rows([Button(short(e["fio"]), f"eda:{e['id']}") for e in es], 3))
    emp = ctx.db.employee(int(emp_id))
    return ctx.ask("edit", f"✏️ {emp['fio']}\n\nНапишите сумму и причину. Премия — просто число, удержание — с минусом:\n"
                           "«5000 подменял коллегу» или «-2000 опоздал с отчётом»", {"emp": emp["id"]})


@text_input("edit")
def on_edit(ctx, text, data):
    parts = text.strip().split(maxsplit=1)
    try:
        amount = float(parts[0].replace(" ", "").replace(",", ".").replace("−", "-"))
    except (ValueError, IndexError):
        return ctx.ask("edit", "Сначала сумма, потом причина: «5000 подменял коллегу»", data)
    reason = parts[1] if len(parts) > 1 else ""
    direct = ctx.role in ("owner", "admin") or not ctx.can("approval")
    try:
        cycle.propose(ctx.db, ctx.month, data["emp"], amount, reason, ctx.author(), direct=direct)
    except cycle.MonthError as e:
        return ctx.ask("edit", f"⚠️ {e}. Попробуйте ещё раз.", data)
    emp = ctx.db.employee(data["emp"])
    msg = f"{'✅ Принято' if direct else '📨 Отправлено на утверждение'}: {emp['fio']} {money(amount)} — {reason}"
    out = [Reply(text=msg)]
    if not direct:
        for o in ctx.db.employees():
            if o["role"] == "owner" and o["tg_id"]:
                out.append(Reply(text=f"✏️ Новая правка: {emp['fio']} {money(amount)} — {reason}\nПредложил: {ctx.author()}",
                                 to=o["tg_id"], buttons=[[Button("✅ Решить", "appr")]]))
    out.append(edits(ctx))
    return out
