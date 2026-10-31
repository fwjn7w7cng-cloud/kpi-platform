"""Модуль «Маршрут и отчёт за день».

Торговый: точки маршрута на сегодня (или завтра) — клиент, город, когда брал, долг; по каждой точке
отмечает ✅ продал (сумма заявки — по желанию) / 👋 был без заказа / ❌ не был (причина).
Вечером — «📋 Сформировать отчёт»: он уходит супервайзеру команды и руководителю продаж.
СВ и руководитель: «📋 Отчёты дня» — по каждому торговому: сколько точек, где продал, кто не отчитался.
За прошлые дни рядом видно, что подтвердила 1С (продажа в этот день в этой точке).

Рассылки: утром торговому — сколько точек сегодня; вечером (schedule.evening) — напоминание, если отчёт не
сформирован; в route.close (по умолчанию 21:00) — сводка дня супервайзерам и руководителям.
"""
from datetime import date, datetime, timedelta

from core.calendar import day_title
from core.jobs.schedule import job
from core.kpi.engine import SELLERS
from core.ui.kit import Button, Reply, money, ok_row, rows
from core.ui.registry import menu, screen, text_input
from core.ui.screens import short
from core.ui.views import Card, Cell, Row, View

M = "route"
ST = {"sold": "✅ продал", "visited": "👋 был", "missed": "❌ не был"}
REASONS = ["Закрыто", "Нет денег", "Не успел", "Отказ", "Нет на месте", "Другое"]

menu("rt", "🚗 Маршрут", {"rep", "newbie"}, module=M, order=11)
menu("rtd", "📋 Отчёты дня", {"sv", "head", "owner", "admin"}, module=M, order=16,
     when=lambda c: c.role != "head" or c.dept_kind == "sales")


def points(ctx, emp_id, day):
    """Точки маршрута сотрудника на день (по ключам с датой передачи территории)."""
    k = ctx.kpi() if day[:7] == ctx.month else ctx.kpi(day[:7])
    seen, out = set(), []
    for d, c in k.route.get(emp_id, []):
        if d == day and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def marks(ctx, emp_id, day):
    return {r["client"]: r for r in ctx.db.q("SELECT * FROM visit_marks WHERE emp_id = ? AND day = ?", emp_id, day)}


def formed(ctx, emp_id, day):
    return ctx.db.one("SELECT * FROM visit_reports WHERE emp_id = ? AND day = ?", emp_id, day)


def sold_1c(ctx, emp_id, day):
    k = ctx.kpi(day[:7])
    return {c for d, c in k.sold_days.get(emp_id, ()) if d == day}


def _client(ctx, code):
    return ctx.db.one("SELECT name, town, phone FROM clients WHERE code = ?", code) or {"name": code, "town": "", "phone": ""}


def summary(ctx, emp_id, day):
    pts = points(ctx, emp_id, day)
    mk = marks(ctx, emp_id, day)
    n = {s: sum(1 for c in pts if mk.get(c, {}).get("status") == s) for s in ST}
    return {"points": len(pts), **n, "none": len(pts) - sum(n.values()),
            "sum": sum(mk[c]["amount"] for c in pts if c in mk), "formed": formed(ctx, emp_id, day)}


def _can_mark(ctx, emp_id, day):
    d = date.fromisoformat(day)
    return ctx.me and ctx.me["id"] == emp_id and ctx.today - timedelta(days=1) <= d <= ctx.today and \
        not formed(ctx, emp_id, day)


@screen("rt", module=M)
def route(ctx, day=None, emp_id=None):
    emp = ctx.db.employee(int(emp_id)) if emp_id else ctx.me
    if not emp or not ctx.sees(emp) or emp["role"] not in SELLERS:
        return Reply(text="Маршрут есть у торговых.")
    day = day or ctx.today.isoformat()
    d = date.fromisoformat(day)
    pts = points(ctx, emp["id"], day)
    mk = marks(ctx, emp["id"], day)
    k = ctx.kpi(day[:7])
    title = "сегодня" if d == ctx.today else "завтра" if d == ctx.today + timedelta(days=1) else day_title(d)
    v = View(f"🚗 Маршрут · {title}", f"{emp['fio']} · {day_title(d)} · точек: {len(pts)}")
    by_client = k.by_client.get(emp["id"], {})
    debts = {r["client"]: r for r in ctx.kpi().debts.get(emp["id"], [])}
    rs = []
    for n, c in enumerate(pts, 1):
        cl = _client(ctx, c)
        sub = []
        if by_client.get(c, 0) > 0:
            sub.append(f"в этом месяце {money(by_client[c])}")
        else:
            sub.append("в этом месяце не брал")
        dt = debts.get(c)
        if dt and dt["d31_60"] + dt["d61_90"] + dt["d91"] > 0:
            sub.append(f"просрочка {money(dt['d31_60'] + dt['d61_90'] + dt['d91'])}")
        m = mk.get(c)
        st = Cell(ST[m["status"]] + (f" {money(m['amount'], '')}" if m["amount"] else ""),
                  "good" if m["status"] == "sold" else "warn" if m["status"] == "visited" else "bad") if m else Cell("—")
        rs.append(Row([f"{n}. {cl['name'][:30]}", cl["town"], st], sub=" · ".join(sub)))
    v.table(["Точка", "Город", "Отметка"], rs) if rs else v.note("На этот день точек нет.")
    s = summary(ctx, emp["id"], day)
    if pts:
        v.lines([f"✅ продал {s['sold']} · 👋 был {s['visited']} · ❌ не был {s['missed']} · не отмечено {s['none']}"
                 + (f" · заявок на {money(s['sum'])}" if s["sum"] else "")])
    if s["formed"]:
        v.note("📋 Отчёт за этот день сформирован" + (" ботом автоматически." if s["formed"]["auto"] else "."))
    kb = []
    if _can_mark(ctx, emp["id"], day):
        kb += rows([Button(("✓ " if c in mk else "") + f"{n}. {_client(ctx, c)['name'][:16]}", f"rtp:{day}:{c}")
                    for n, c in enumerate(pts, 1)], 2)
        if pts:
            kb.append([Button("📋 Сформировать отчёт", f"rtr:{day}")])
        km = sum(x["amount"] or 0 for x in ctx.db.timesheet(day, day, emp["id"]) if x["kind"] == "km")
        kb.append([Button(f"🚗 Км за день: {km:g}" if km else "🚗 Вписать км за день (ГСМ)", f"rtk:{day}")])
    nav = []
    if d > ctx.today - timedelta(days=6):
        nav.append(Button("◀ Вчера" if d == ctx.today else "◀", f"rt:{(d - timedelta(days=1)).isoformat()}"
                          + (f":{emp['id']}" if emp_id else "")))
    if d < ctx.today + timedelta(days=1):
        nav.append(Button("Завтра ▶" if d == ctx.today else "▶", f"rt:{(d + timedelta(days=1)).isoformat()}"
                          + (f":{emp['id']}" if emp_id else "")))
    if nav:
        kb.append(nav)
    return Reply(view=v, buttons=kb)


@screen("rtp", module=M)
def point(ctx, day, code, act=None, value=None):
    """Отметка точки — окно: продал / был / не был (и почему), «ОК» / «Отмена». «Продал» после «ОК» спросит сумму заявки."""
    if not ctx.me or not _can_mark(ctx, ctx.me["id"], day) or code not in points(ctx, ctx.me["id"], day):
        return Reply(text="Отмечать можно свои точки за сегодня и вчера, пока отчёт не сформирован.")
    cl = _client(ctx, code)
    m = marks(ctx, ctx.me["id"], day).get(code)
    pre = f"rtp:{day}:{code}"
    d = ctx.session.get("rt_draft")
    if act is None or not isinstance(d, dict) or d.get("k") != pre:
        d = {"k": pre, "st": m["status"] if m else None, "why": m["reason"] if m else ""}
    if act == "no":
        ctx.session.pop("rt_draft", None)
        ctx.save()
        return route(ctx, day)
    if act == "s" and value in ST:
        d["st"] = value
    elif act == "r" and (value or "").isdigit() and int(value) < len(REASONS):
        d["why"] = REASONS[int(value)]
    if act == "ok" and d["st"]:
        ctx.session.pop("rt_draft", None)
        ctx.save()
        if d["st"] == "sold":
            return ctx.ask("rt_sum", f"✅ {cl['name']}: на какую сумму заявка? Напишите число или «-», если не знаете.",
                           {"day": day, "code": code})
        _save(ctx, day, code, d["st"], d["why"] if d["st"] == "missed" else "", 0)
        return route(ctx, day)
    ctx.session["rt_draft"] = d
    ctx.save()
    v = View(f"🏪 {cl['name']}", " · ".join(x for x in (cl["town"], day_title(date.fromisoformat(day))) if x))
    v.cards([Card("Сейчас", ST[m["status"]] if m else "не отмечено", [m["reason"]] if m and m["reason"] else [],
                  phone=cl["phone"])])
    if act == "ok":
        v.note("Сначала отметьте, что было в точке.")
    kb = [[Button(f"{'☑' if d['st'] == k else '☐'} {t}", f"{pre}:s:{k}")] for k, t in
          (("sold", "✅ Продал"), ("visited", "👋 Был, без заказа"), ("missed", "❌ Не был"))]
    if d["st"] == "missed":
        kb += rows([Button(("● " if d["why"] == r else "") + r, f"{pre}:r:{i}") for i, r in enumerate(REASONS)], 3)
    kb.append(ok_row(pre))
    return Reply(view=v, buttons=kb)


@screen("rtk", module=M)
def km_day(ctx, day):
    """Торговый сам вписывает км за день — для ГСМ «за км» (в общую зарплату)."""
    if not ctx.me or not _can_mark(ctx, ctx.me["id"], day):
        return Reply(text="Км вписываются за сегодня и вчера.")
    return ctx.ask("rt_km", "Сколько км проехали за день? Числом, например 120. 0 — убрать.", {"day": day},
                   buttons=[[Button("✖️ Отмена", f"rt:{day}")]])


@text_input("rt_km")
def on_km(ctx, text, data):
    t = (text or "").replace(" ", "").replace(",", ".").replace("км", "")
    try:
        km = float(t)
        if not 0 <= km < 3000:
            raise ValueError
    except ValueError:
        return ctx.ask("rt_km", "Нужно число км, например 120.", data)
    day = data["day"]
    ctx.db.x("DELETE FROM timesheet WHERE day = ? AND emp_id = ? AND kind = 'km'", day, ctx.me["id"])
    if km:
        ctx.db.add_timesheet(day, ctx.me["id"], "km", amount=km, who=ctx.author())
    return route(ctx, day)


def _save(ctx, day, code, status, reason, amount):
    ctx.db.x("INSERT INTO visit_marks (day, emp_id, client, status, reason, amount, ts) VALUES (?,?,?,?,?,?,?) "
             "ON CONFLICT(day, emp_id, client) DO UPDATE SET status = excluded.status, reason = excluded.reason, "
             "amount = excluded.amount, ts = excluded.ts", day, ctx.me["id"], code, status, reason, amount,
             datetime.now().isoformat(timespec="seconds"))


@text_input("rt_sum")
def on_sum(ctx, text, data):
    t = (text or "").replace(" ", "").replace("\xa0", "").replace(",", ".").replace("₽", "")
    try:
        amount = float(t) if t not in ("", "-", "—") else 0
    except ValueError:
        return ctx.ask("rt_sum", "Не понял сумму. Напишите число, например 15000, или «-».", data)
    _save(ctx, data["day"], data["code"], "sold", "", amount)
    return route(ctx, data["day"])


def report_view(ctx, emp, day):
    d = date.fromisoformat(day)
    s = summary(ctx, emp["id"], day)
    v = View(f"📋 Отчёт за день · {short(emp['fio'])}", day_title(d))
    v.tiles([("Точек", str(s["points"]), None), ("✅ Продал", str(s["sold"]), "good" if s["sold"] else None),
             ("❌ Не был", str(s["missed"]), "bad" if s["missed"] else None), ("Заявки", money(s["sum"]), None)])
    mk = marks(ctx, emp["id"], day)
    confirmed = sold_1c(ctx, emp["id"], day) if d < ctx.today else set()
    rs = []
    for c in points(ctx, emp["id"], day):
        m = mk.get(c)
        note = (ST[m["status"]] + (f" · {m['reason']}" if m["reason"] else "")) if m else "не отмечено"
        rs.append(Row([_client(ctx, c)["name"][:30], Cell(note, "good" if m and m["status"] == "sold" else
                                                          "bad" if not m or m["status"] == "missed" else "warn"),
                       "✓" if c in confirmed else ""]))
    v.table(["Точка", "Отметка", "1С"], rs)
    if d < ctx.today:
        v.note("1С ✓ — в этот день в точке есть продажа по отчёту 1С.")
    return v


@screen("rtr", module=M)
def form(ctx, day):
    if not ctx.me or not _can_mark(ctx, ctx.me["id"], day):
        return Reply(text="Отчёт уже сформирован или день закрыт.")
    ctx.db.x("INSERT OR IGNORE INTO visit_reports (day, emp_id, ts) VALUES (?,?,?)", day, ctx.me["id"],
             datetime.now().isoformat(timespec="seconds"))
    v = report_view(ctx, ctx.me, day)
    out = [Reply(text="📋 Отчёт сформирован и отправлен руководителю."), Reply(view=v)]
    for e in _leads_of(ctx, ctx.me):
        if e["tg_id"]:
            out.append(Reply(view=report_view(ctx, ctx.me, day), to=e["tg_id"],
                             buttons=[[Button("📋 Все отчёты дня", f"rtd:{day}")]]))
    return out


def _leads_of(ctx, emp):
    es = ctx.db.employees()
    sv = [e for e in es if e["role"] == "sv" and e["team"] == emp["team"]]
    heads = [e for e in es if e["role"] == "head" and e["dept"] == emp["dept"]]
    return sv + heads


@screen("rtd", module=M)
def day_reports(ctx, day=None):
    if ctx.scope == "self":
        return route(ctx)
    day = day or ctx.today.isoformat()
    d = date.fromisoformat(day)
    k = ctx.kpi(day[:7])
    dept = next(x for x in ctx.cfg.departments if x.kind == "sales")
    ids = [i for i in k.members("dept", dept.id) if ctx.sees(i) and k.employees[i]["active"]]
    v = View("📋 Отчёты дня", day_title(d))
    rs, tot = [], {"points": 0, "sold": 0, "missed": 0, "none": 0}
    for i in ids:
        s = summary(ctx, i, day)
        if not s["points"]:
            continue
        for x in tot:
            tot[x] += s[x]
        rs.append(Row([short(k.employees[i]["fio"]), str(s["points"]), Cell(str(s["sold"]), "good" if s["sold"] else None),
                       Cell(str(s["missed"]), "bad" if s["missed"] else None),
                       Cell("✓" if s["formed"] else "—", "good" if s["formed"] else "bad")],
                      sub=f"не отмечено {s['none']}" if s["none"] else ""))
    v.tiles([("Точек", str(tot["points"]), None), ("Продал", str(tot["sold"]), None),
             ("Не был", str(tot["missed"]), "bad" if tot["missed"] else None),
             ("Отчётов", f"{sum(1 for i in ids if formed(ctx, i, day))} из {len(rs)}", None)])
    v.table(["Менеджер", "Точек", "Продал", "Не был", "Отчёт"], rs) if rs else v.note("В этот день маршрутов нет.")
    kb = rows([Button(short(k.employees[i]["fio"]), f"rtv:{i}:{day}") for i in ids if points(ctx, i, day)], 3)
    nav = [Button("◀", f"rtd:{(d - timedelta(days=1)).isoformat()}")]
    if d < ctx.today:
        nav.append(Button("▶", f"rtd:{(d + timedelta(days=1)).isoformat()}"))
    kb.append(nav)
    return Reply(view=v, buttons=kb)


@screen("rtv", module=M)
def view_report(ctx, emp_id, day):
    emp = ctx.db.employee(int(emp_id))
    if not emp or not ctx.sees(emp):
        return Reply(text="Нет доступа.")
    return Reply(view=report_view(ctx, emp, day), buttons=[[Button("‹ Отчёты дня", f"rtd:{day}"),
                                                            Button("🚗 Маршрут", f"rt:{day}:{emp_id}")]])


# ---------- рассылки ----------

@job("route_morning", "morning", {"rep", "newbie"}, "Утром: маршрут на сегодня", module=M)
def morning(ctx):
    if not ctx.me:
        return None
    pts = points(ctx, ctx.me["id"], ctx.today.isoformat())
    if not pts:
        return None
    return Reply(text=f"🚗 Сегодня в маршруте {len(pts)} точек. Вечером отметьте, где были, и сформируйте отчёт.",
                 buttons=[[Button("🚗 Маршрут", "rt")]])


@job("route_remind", "evening", {"rep", "newbie"}, "Вечером: напомнить про отчёт за день", module=M)
def remind(ctx):
    if not ctx.me:
        return None
    day = ctx.today.isoformat()
    if not points(ctx, ctx.me["id"], day) or formed(ctx, ctx.me["id"], day):
        return None
    return Reply(text="📋 Не забудьте отметить точки и сформировать отчёт за день.", buttons=[[Button("🚗 Маршрут", "rt")]])


@job("route_close", lambda app: (app.cfg.raw.get("route") or {}).get("close", "21:00"), {"sv", "head", "owner"},
     "Вечером: сводка отчётов дня руководителям", module=M)
def close(ctx):
    if ctx.role == "head" and ctx.dept_kind != "sales":
        return None
    r = day_reports(ctx)
    return r if r.view and r.view.size() else None
