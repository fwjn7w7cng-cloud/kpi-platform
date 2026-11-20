"""🏬 Свои магазины (Адам 20.11: сегменты «Опт + розница» и «Розница»).

Магазин — команда в отделе, где есть должность с обязанностью «seller» (продавец). Директор магазина — обязанность «shop»,
сборщик — «assembler». Данные магазинов лежат в базе ключами (у демо их кладёт сидер, у клиента — «Учёт в Алапе»):
  rt:<месяц>   {checks: {сотрудник: {день: [чеков, позиций, сумма]}}, shifts: {сотрудник: [дни]},
                came: {день: {сотрудник: "09:52"}}, inst: {сотрудник: договоров рассрочки}, avg_bar: порог среднего чека}
  rt_orders    {stages: [...], asm_word: "Сборка", items: [{id, day, shop, seller, client, item, sum, pay, prepay, months, deliv, stage,
                paid, late, asm, complaint}]} — stage: 0 заказ, 1 оплата, 2 доставка, 3 сборка, 4 закрыт
  rt_inst      [{id, day, shop, seller, client, sum, first, months, late, late_days, debt}]
Продажи продавцов — обычные строки продаж (клиент «Покупатели · магазин»), поэтому % от продаж и план считает ядро.
Ядро не знает клиентов: названия магазинов и должностей — из анкеты.
"""
from datetime import date, timedelta

from core.calendar import MONTHS_GEN, month_of, month_title
from core.pay import components as C
from core.ui.kit import Reply, money, pct
from core.ui.registry import screen
from core.ui.views import View

STAGE_DONE = 4


# ---------- где магазины ----------

def shop_dept(cfg):
    """Отдел магазинов: где есть должность продавца. Нет — None."""
    p = next((p for p in cfg.positions if p.get("duty") == "seller"), None)
    return p["dept"] if p else None


def has_shops(cfg):
    return bool(shop_dept(cfg))


def shops(cfg):
    d = shop_dept(cfg)
    return [(t.id, t.name) for t in cfg.teams_of(d)] if d else []


def shop_name(cfg, team):
    return dict(shops(cfg)).get(team, team)


EDIT_KEY = "shops_edit"      # Адам 20.11: магазины правятся в приложении — {names: {id: название}, add: [[id, название]], off: [id]}
SEES_KEY = "sees_shops"      # «видит магазины» у руководителя: {id сотрудника: [id магазинов]}


def shop_teams(cfg, sid):
    """Команды одного магазина во всех отделах: s1 в «Магазинах» и <отдел>_s1 в складе, логистике, офисе."""
    return [t for t in cfg.teams if t.id == sid or t.id.endswith("_" + sid)]


def apply_edits(cfg, db):
    """Анкета с правками магазинов из приложения: переименованные, добавленные, убранные. Без правок — та же анкета."""
    e = db.get(EDIT_KEY) if db is not None else None
    if not e or not shop_dept(cfg):
        return cfg
    import copy
    from core.config import Team
    c = copy.copy(cfg)
    names, off = e.get("names") or {}, set(e.get("off") or [])
    sd = shop_dept(cfg)
    line = next((t.line for t in cfg.teams_of(sd)), "")
    teams = [copy.copy(t) for t in cfg.teams]
    others = sorted({t.dept for t in cfg.teams if "_s" in t.id and t.dept != sd})
    for sid, name in e.get("add") or []:
        teams.append(Team(sid, name, sd, line))
        teams += [Team(f"{d}_{sid}", name, d) for d in others]
    out = []
    for t in teams:
        sid = t.id if t.dept == sd else t.id.rsplit("_", 1)[-1] if "_s" in t.id else None
        if sid in off:
            continue
        if sid and sid in names:
            t.name = names[sid]
        out.append(t)
    c.teams = out
    return c


def limit_to(cfg, allowed):
    """Анкета, где видны только эти магазины (руководитель группы магазинов)."""
    import copy
    sd = shop_dept(cfg)
    c = copy.copy(cfg)
    c.teams = [t for t in cfg.teams if not ((t.dept == sd and t.id not in allowed) or
                                            ("_s" in t.id and t.dept != sd and t.id.rsplit("_", 1)[-1] not in allowed))]
    return c


def segment_hide(cfg, es, seg):
    """Кого не показывать в сегменте демо: опт — без магазинов и сборщиков, розница — без отдела продаж опта."""
    d = shop_dept(cfg)
    if not d or seg not in ("opt", "retail"):
        return set()
    out = set()
    for e in es:
        pos = cfg.position(e["position"]) or {}
        dep = cfg.dept(e["dept"])
        if seg == "opt" and (e["dept"] == d or pos.get("duty") == "assembler"):
            out.add(e["id"])
        if seg == "retail" and dep and dep.kind == "sales" and e["dept"] != d:
            out.add(e["id"])
    return out


def month_data(db, month):
    return db.get(f"rt:{month}") or {}


def orders(db):
    return db.get("rt_orders") or {}


def inst_list(db):
    return db.get("rt_inst") or []


def _n(k, one, few, many):
    from core.ui.live import _n as n_
    return n_(k, one, few, many)


def _num(x):
    """Сумма в тесной таблице — без «₽»."""
    return money(x).replace(" ₽", "").replace("\u00a0₽", "")


def _short(fio):
    p = (fio or "").split()
    return f"{p[0]} {p[1][0]}." if len(p) > 1 else (fio or "")


def _dm(iso):
    d = date.fromisoformat(iso)
    return f"{d.day}.{d.month:02d}"


# ---------- цифры продавца ----------

def seller_stats(db, month, emp_id, today):
    d = month_data(db, month)
    ch = (d.get("checks") or {}).get(str(emp_id), {})
    n = sum(x[0] for x in ch.values())
    pos = sum(x[1] for x in ch.values())
    tot = sum(x[2] for x in ch.values())
    shifts = (d.get("shifts") or {}).get(str(emp_id), [])
    came = ((d.get("came") or {}).get(today.isoformat()) or {})
    done = sum(1 for x in shifts if x < today.isoformat()) + (1 if str(emp_id) in came else 0)
    return {"checks": n, "pos": pos, "sum": tot, "avg": tot / n if n else 0, "per": pos / n if n else 0,
            "shifts": done, "shifts_all": len(shifts), "inst": (d.get("inst") or {}).get(str(emp_id), 0),
            "bar": d.get("avg_bar") or 0, "today": today.isoformat() in shifts, "came": came.get(str(emp_id))}


def _unit_shifts(pay, emp):
    return seller_stats(pay.db, pay.kpi.month, emp["id"], pay.cal.today)["shifts"] if pay.db.get(f"rt:{pay.kpi.month}") else 0


def _unit_inst(pay, emp):
    return (month_data(pay.db, pay.kpi.month).get("inst") or {}).get(str(emp["id"]), 0)


def _unit_avg(pay, emp):
    s = seller_stats(pay.db, pay.kpi.month, emp["id"], pay.cal.today)
    return 1 if s["bar"] and s["checks"] >= 5 and s["avg"] >= s["bar"] else 0


def _asm_done(db, month, emp_id):
    its = [o for o in orders(db).get("items", []) if o.get("asm") == emp_id and o["stage"] == STAGE_DONE and o["deliv"][:7] == month]
    return len(its), sum(1 for o in its if o.get("complaint"))


C.unit("shifts", _unit_shifts)
C.unit("inst", _unit_inst)
C.unit("avg_check", _unit_avg)
C.unit("assembly", lambda pay, emp: _asm_done(pay.db, pay.kpi.month, emp["id"])[0])
C.unit("complaints", lambda pay, emp: _asm_done(pay.db, pay.kpi.month, emp["id"])[1])


# ---------- заказы ----------

def stages(db):
    return orders(db).get("stages") or ["Заказ", "Оплата", "Доставка"]


def stage_steps(db, o):
    """Дорожка заказа: пройдено / сейчас / впереди. Оплата — «Предоплата» или «Рассрочка»."""
    out = []
    for i, t in enumerate(stages(db)):
        if i == 1:
            t = {"inst": "Рассрочка", "prepay": "Предоплата", "full": "Оплачен"}.get(o["pay"], t)
        out.append([t, "done" if i < o["stage"] else "cur" if i == o["stage"] else ""])
    return out


def order_row(ctx, o, who=True):
    names = {e["id"]: e["fio"] for e in ctx.db._employees(active=None)}
    pay = (f"предоплата {money(o['prepay'])}" if o["pay"] == "prepay" else f"рассрочка {o['months']} мес" if o["pay"] == "inst"
           else "оплачен")
    s = f"{money(o['sum'])} · {pay} · доставка {_dm(o['deliv'])}"
    if who:
        s += f" · {shop_name(ctx.cfg, o['shop'])}"
    if o.get("lines"):                                  # учёт в Алапе: Заказ → Погружен → Доставлен (modules/ship.py)
        from modules import ship as SH
        row = {"t": f"{o['item']} · {o['client']}", "s": s + SH.order_note(ctx.db, o), "steps": SH.new_steps(o)}
        if o.get("load") == "no" or (not o.get("driver") and o.get("load") != "gone"):
            row["chip"] = ["сменить водителя" if o.get("driver") else "назначить водителя", f"rdv:{o['id']}", "warn"]
        elif o.get("load") == "gone":
            row["chip"] = ["лист", f"rs:{o['sheet']}", ""]
        return row
    row = {"t": f"{o['item']} · {o['client']}", "s": s, "steps": stage_steps(ctx.db, o)}
    if o.get("late"):
        row["chip"] = ["опаздывает", "", "bad"]
    elif o["pay"] == "prepay" and not o.get("paid"):
        row["chip"] = ["ждёт предоплату", "", "warn"]
    if o.get("asm") and o["stage"] >= 3:
        row["s"] += f" · {o.get('asm') and _short(names.get(o['asm'], ''))}"
    return row


def open_orders(db, shop=None, seller=None):
    return [o for o in orders(db).get("items", []) if o["stage"] < STAGE_DONE and (shop is None or o["shop"] == shop)
            and (seller is None or o["seller"] == seller)]


def order_counts(db, today, shop=None):
    op = open_orders(db, shop)
    t = today.isoformat()
    deliv = [o for o in op if o["deliv"] == t]
    return {"open": len(op), "open_sum": sum(o["sum"] for o in op),
            "wait": [o for o in op if o["pay"] == "prepay" and not o.get("paid")],
            "deliv": len(deliv), "late": sum(1 for o in op if o.get("late")),
            "asm": sum(1 for o in op if o["stage"] == 2 and o["deliv"] < t)}


def inst_stats(db, month, shop=None):
    its = [x for x in inst_list(db) if shop is None or x["shop"] == shop]
    mine = [x for x in its if x["day"][:7] == month]
    late = [x for x in its if x["late"]]
    return {"n": len(mine), "sum": sum(x["sum"] for x in mine), "first": sum(x["first"] for x in mine),
            "ok": 1 - len(late) / len(its) if its else 1, "late_n": len(late), "late_sum": sum(x["debt"] for x in late),
            "late_10": sum(1 for x in late if x["late_days"] > 10)}


# ---------- главные ----------

def _shop_fact(ctx, team):
    k = ctx.kpi()
    return k.scope("team", team)["sales"]


def owner_home(ctx, v):
    """Директор фирмы с магазинами: опт + розница вместе, магазины полосками, заказы и рассрочка плитками."""
    from core.ui import live
    k = ctx.kpi()
    sd = live._sales_dept(ctx)
    rd = shop_dept(ctx.cfg)
    seg = getattr(ctx, "segment", "mix")
    r = k.scope("dept", rd)["sales"]
    o = k.scope("dept", sd.id)["sales"] if sd and seg != "retail" else None
    ins = inst_stats(ctx.db, ctx.month)
    fact = r.fact + (o.fact if o else 0)
    plan = r.plan + (o.plan if o else 0)
    p = fact / plan if plan else None
    if o:
        pairs = [("Опт", money(o.fact), ""), ("Розница", money(r.fact), ""), ("Рассрочка", money(ins["sum"]), "")]
    else:
        d0 = month_data(ctx.db, ctx.month)
        n = sum(x[0] for ch in (d0.get("checks") or {}).values() for x in ch.values())
        tot = sum(x[2] for ch in (d0.get("checks") or {}).values() for x in ch.values())
        pairs = [("Чеков", str(n), ""), ("Средний чек", money(tot / n) if n else "—", ""), ("Рассрочка", money(ins["sum"]), "")]
    label = "Выручка фирмы" if o else "Выручка магазинов"
    live.hero(v, f"{label} · {month_title(ctx.month).lower()}", money(fact),
              f"план {money(plan)} · нужно к сегодня {pct(r.need)}" if plan else "", p=p, need=r.need if plan else None,
              pairs=pairs, md=True, ident="rto")
    shop_rows(ctx, v)
    oc = order_counts(ctx.db, ctx.today)
    tiles = [["🚚", "Доставки сегодня", str(oc["deliv"]), f"{oc['late']} опаздывают" if oc["late"] else "все в срок", "rto"],
             ["💳", "Рассрочка · просрочка", str(ins["late_n"]), f"на {money(ins['late_sum'])}", "rto"],
             ["📦", "Заказов в работе", str(oc["open"]), f"на {money(oc['open_sum'])}", "rto"]]
    if o:
        tiles.append(["📈", "Опт · торговые", pct(o.pct) if o.plan else money(o.fact), "к плану", "sales"])
    v.ui("tiles", {"items": tiles}, "Заказы и рассрочка")
    try:
        from modules import sale as SL
        SL.owner_block(ctx, v)
    except ImportError:
        pass
    v.ui("feed", {"items": live.feed(ctx), "live": "по отчётам и отметкам"}, "Сейчас в фирме")
    ds = live.decisions(ctx)
    if ds:
        v.ui("decide", {"items": ds}, "Ждёт решения")
    v.ui("tiles", {"items": live.dept_tiles(ctx)}, "Отделы")


def group_home(ctx, v):
    """Руководитель магазинов (Адам 20.11): выручка его магазинов («видит магазины» или все), магазины полосками, заказы."""
    from core.ui import live
    ss = shops(ctx.cfg)
    sc = [_shop_fact(ctx, t) for t, _ in ss]
    fact, plan = sum(s.fact for s in sc), sum(s.plan for s in sc)
    need = sc[0].need if sc else None
    live.hero(v, f"{_n(len(ss), 'магазин', 'магазина', 'магазинов')} · {month_title(ctx.month).lower()}", money(fact),
              f"план {money(plan)} · нужно к сегодня {pct(need)}" if plan else "", p=fact / plan if plan else None,
              need=need if plan else None, md=True, ident="rto")
    shop_rows(ctx, v)
    cs = [order_counts(ctx.db, ctx.today, t) for t, _ in ss]
    v.ui("tiles", {"items": [["🚚", "Доставки сегодня", str(sum(c["deliv"] for c in cs)),
                              f"{sum(c['late'] for c in cs)} опаздывают" if any(c["late"] for c in cs) else "все в срок", "rto"],
                             ["📦", "Заказов в работе", str(sum(c["open"] for c in cs)), f"на {money(sum(c['open_sum'] for c in cs))}", "rto"]]},
         "Заказы")


def shop_rows(ctx, v):
    d = month_data(ctx.db, ctx.month)
    t = ctx.today.isoformat()
    came = (d.get("came") or {}).get(t) or {}
    items = []
    for team, name in shops(ctx.cfg):
        s = _shop_fact(ctx, team)
        sellers = [e for e in ctx.db.employees(team=team) if (ctx.cfg.position(e["position"]) or {}).get("duty") == "seller"]
        on = [e for e in sellers if t in ((d.get("shifts") or {}).get(str(e["id"])) or [])]
        here = [e for e in on if str(e["id"]) in came]
        today_n = sum(((d.get("checks") or {}).get(str(e["id"])) or {}).get(t, [0, 0, 0])[0] for e in sellers)
        y = (ctx.today - timedelta(days=1)).isoformat()
        y_sum = sum(((d.get("checks") or {}).get(str(e["id"])) or {}).get(y, [0, 0, 0])[2] for e in sellers)
        tone = "good" if s.plan and s.pct >= s.need else "warn" if s.plan and s.pct >= s.need - 0.1 else "bad"
        items.append({"t": f"🏬 {name}", "s": f"вчера {money(y_sum)} · смена сегодня: {len(here)} из {len(on)}"
                      + (f" · чеков {today_n}" if today_n else ""),
                      "chip": [pct(s.pct) if s.plan else money(s.fact), f"rts:{team}", tone if s.plan else ""],
                      "bar": [s.pct if s.plan else 0, tone]})
    v.ui("rows", {"items": items, "right": "нажмите на %"}, "Магазины")


def shop_home(ctx, v, team=None):
    """Директор магазина: выручка магазина, продавцы, смена сегодня, ближайшие доставки."""
    from core.ui import live
    team = team or (ctx.me or {}).get("team")
    k = ctx.kpi()
    m = k.scope("team", team)
    s = m["sales"]
    d = month_data(ctx.db, ctx.month)
    sellers = [e for e in ctx.db.employees(team=team) if (ctx.cfg.position(e["position"]) or {}).get("duty") == "seller"]
    st = {e["id"]: seller_stats(ctx.db, ctx.month, e["id"], ctx.today) for e in sellers}
    n = sum(x["checks"] for x in st.values())
    tot = sum(x["sum"] for x in st.values())
    pos = sum(x["pos"] for x in st.values())
    live.hero(v, f"{shop_name(ctx.cfg, team)} · {month_title(ctx.month).lower()}", money(s.fact),
              f"план {money(s.plan)} · нужно к сегодня {pct(s.need)}" if s.plan else "",
              p=s.pct if s.plan else None, need=s.need if s.plan else None, md=True, ident=f"rts{team}",
              acts=[["🧾 Продажа", "sale"], ["📋 Продажи сегодня", "sll"]] if ctx.duty == "shop" and ctx.can("stock") else [],
              pairs=[("Чеков", str(n), ""), ("Средний чек", money(tot / n) if n else "—", ""),
                     ("Позиций в чеке", f"{pos / n:.1f}".replace(".", ",") if n else "—", "")])
    rows = []
    for e in sorted(sellers, key=lambda e: -k.emp(e["id"])["sales"].fact):
        me = k.emp(e["id"])["sales"]
        x = st[e["id"]]
        tone = "good" if me.plan and me.pct >= me.need else "warn" if me.plan and me.pct >= me.need - 0.1 else "bad"
        rows.append({"n": _short(e["fio"]), "cells": [[_num(me.fact), ""], [pct(me.pct) if me.plan else "—", tone],
                                                      [_num(x["avg"]) if x["checks"] else "—", ""], [str(x["inst"]), ""]],
                     "det": f"Смен {x['shifts']} из {x['shifts_all']} · чеков {x['checks']}"
                            + (f" · до плана {money(max(me.plan - me.fact, 0))}" if me.plan else ""),
                     "acts": [["📊 Подробно", f"emp:{e['id']}"]]})
    v.ui("team", {"head": ["Продавец", "Выручка", "План", "Ср. чек", "Рассрочка"], "rows": rows, "right": "месяц"}, "Продавцы",
         fallback=[f"{r['n']}: {r['cells'][0][0]}" for r in rows])
    t = ctx.today.isoformat()
    came = (d.get("came") or {}).get(t) or {}
    items = []
    lates = {r["emp_id"]: r for r in ctx.db.timesheet(ctx.today, ctx.today) if r["kind"] == "late"}
    for e in sellers:
        if not st[e["id"]]["today"]:
            continue
        c = came.get(str(e["id"]))
        if not c:
            items.append({"t": _short(e["fio"]), "s": "ещё не пришёл", "chip": ["нет", "", "warn"]})
        elif e["id"] in lates:
            items.append({"t": _short(e["fio"]), "s": f"пришёл {c} · опоздание {lates[e['id']]['minutes']} мин", "chip": ["опоздал", "", "bad"]})
        else:
            items.append({"t": _short(e["fio"]), "s": f"пришёл {c}", "chip": ["на месте", "", "good"]})
    v.ui("rows", {"items": items, "empty": "Сегодня смены нет"}, "Смена сегодня")
    op = sorted(open_orders(ctx.db, team), key=lambda o: o["deliv"])[:4]
    if op:
        v.ui("rows", {"items": [order_row(ctx, o, who=False) for o in op], "foot": [["🚚 Все заказы и рассрочка", f"rto:{team}"]]},
             "Ближайшие доставки")


def seller_home(ctx, v):
    """Продавец: план, зарплата из чего, заказы с доставкой."""
    from core.ui import live
    e = ctx.me
    k = ctx.kpi()
    s = k.emp(e["id"])["sales"]
    x = seller_stats(ctx.db, ctx.month, e["id"], ctx.today)
    left = max(s.plan - s.fact, 0)
    left_sh = max(x["shifts_all"] - x["shifts"], 1)
    p = ctx.pay().calc(e) if ctx.can("payroll") else None
    pairs = ([("Зарплата сейчас", money(p.total), "")] if p else []) + [
        ("Средний чек", money(x["avg"]) if x["checks"] else "—", ""), ("Смен", f"{x['shifts']} из {x['shifts_all']}", "")]
    live.hero(v, f"Мой план · {month_title(ctx.month).lower()}", pct(s.pct) if s.plan else money(s.fact),
              (f"{money(s.fact)} из {money(s.plan)} · в смену по {money(left / left_sh)}" if left else "план выполнен 🎉") if s.plan else "",
              p=s.pct if s.plan else None, need=s.need if s.plan else None, pairs=pairs, ident="sel",
              acts=[["🧾 Продажа", "sale"], ["📋 Продажи сегодня", "sll"]] if ctx.can("stock") else [])
    if p and p.rows:
        live.explain(v, p, "Из чего зарплата")
    mine = sorted(open_orders(ctx.db, seller=e["id"]), key=lambda o: o["deliv"])
    v.ui("rows", {"items": [order_row(ctx, o, who=False) for o in mine[:6]], "empty": "Открытых заказов нет",
                  "foot": [["🚚 Все мои заказы", "rto:me"]]}, "Мои заказы с доставкой")


def asm_home(ctx, v):
    """Сборщик: сборки и жалобы за месяц, ближайшие адреса."""
    from core.ui import live
    e = ctx.me
    p = ctx.pay().calc(e) if ctx.can("payroll") else None
    n, bad = _asm_done(ctx.db, ctx.month, e["id"])
    word = orders(ctx.db).get("asm_word") or "Сборка"
    live.hero(v, f"{word} у покупателей · {month_title(ctx.month).lower()}", str(n), "закрыто за месяц",
              pairs=([("Заработано", money(p.total), "")] if p else []) + [("Жалоб", str(bad), "bad" if bad else "")], ident="asm")
    nxt = sorted([o for o in open_orders(ctx.db) if o.get("asm") == e["id"] and o["stage"] >= 2], key=lambda o: o["deliv"])[:6]
    v.ui("rows", {"items": [order_row(ctx, o) for o in nxt], "empty": "Новых сборок нет"}, "Ближайшие")
    if p and p.rows:
        live.explain(v, p, "Из чего зарплата")


# ---------- экраны ----------

@screen("rts")
def shop_screen(ctx, team=None):
    team = team if team in dict(shops(ctx.cfg)) else (ctx.me or {}).get("team")
    if ctx.role not in ("owner", "admin", "accountant", "head") and not (ctx.role == "sv" and (ctx.me or {}).get("team") == team):
        from core.ui.screens import home
        return home(ctx)
    v = View(shop_name(ctx.cfg, team))
    shop_home(ctx, v, team)
    return Reply(view=v)


@screen("rto")
def orders_screen(ctx, scope=None):
    """Заказы с доставкой и рассрочка: все магазины, один магазин или свои (продавец)."""
    seller = ctx.me["id"] if ctx.me and (ctx.duty == "seller" or scope == "me") else None
    shop = scope if scope in dict(shops(ctx.cfg)) else ((ctx.me or {}).get("team") if ctx.duty == "shop" else None)
    title = "Мои заказы" if seller else (shop_name(ctx.cfg, shop) if shop else "Заказы и рассрочка")
    v = View(title)
    oc = order_counts(ctx.db, ctx.today, shop)
    op = open_orders(ctx.db, shop, seller)
    v.ui("tiles", {"items": [
        ["📦", "Заказов в работе", str(len(op)), f"на {money(sum(o['sum'] for o in op))}", ""],
        ["⏳", "Ждут предоплату", str(len([o for o in op if o["pay"] == "prepay" and not o.get("paid")])),
         f"на {money(sum(o['sum'] for o in op if o['pay'] == 'prepay' and not o.get('paid')))}", ""],
        ["🚚", "Доставка сегодня", str(sum(1 for o in op if o["deliv"] == ctx.today.isoformat())),
         f"{sum(1 for o in op if o.get('late'))} опаздывают", ""],
        ["🔧", f"{orders(ctx.db).get('asm_word') or 'Сборка'} без даты", str(oc["asm"]), "ждут сборщика", ""]]}, "")
    if not seller:
        ins = inst_stats(ctx.db, ctx.month, shop)
        v.ui("rows", {"items": [
            {"t": f"Оформлено · {month_title(ctx.month).split()[0].lower()}", "s": f"{ins['n']} договоров · первый взнос {money(ins['first'])}",
             "v": money(ins["sum"])},
            {"t": "Платежи в срок", "s": "за месяц", "chip": [pct(ins["ok"]), "", "good" if ins["ok"] >= 0.9 else "warn"]},
            {"t": "Просрочка платежа", "s": f"{_n(ins['late_n'], 'покупатель', 'покупателя', 'покупателей')} · больше 10 дней — {ins['late_10']}",
             "chip": [money(ins["late_sum"]), "", "bad" if ins["late_n"] else "good"]}]}, "💳 Рассрочка")
    v.ui("rows", {"items": [order_row(ctx, o, who=not shop) for o in sorted(op, key=lambda o: o["deliv"])[:25]],
                  "empty": "Открытых заказов нет", "right": f"{len(op)} в работе"}, "Заказы с доставкой")
    if not seller and orders(ctx.db).get("asm_word"):
        names = {e["id"]: e for e in ctx.db.employees()}
        rows = []
        for e in names.values():
            if (ctx.cfg.position(e["position"]) or {}).get("duty") != "assembler":
                continue
            n, bad = _asm_done(ctx.db, ctx.month, e["id"])
            pay = ctx.pay().calc(e).total if ctx.can("payroll") else 0
            rows.append({"n": _short(e["fio"]), "cells": [[str(n), ""], [str(bad), "bad" if bad else ""], [_num(pay), "good"]],
                         "det": f"{n} закрыто в этом месяце, жалоб {bad}", "acts": [["📊 Подробно", f"emp:{e['id']}"]]})
        if rows:
            v.ui("team", {"head": ["Сборщик", "Сборок", "Жалоб", "Премия"], "rows": rows, "right": "премия за сборку"}, "🔧 Сборщики")
    return Reply(view=v)
