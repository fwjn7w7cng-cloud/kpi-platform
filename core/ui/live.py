"""Живые экраны (Адам 17.11: «как в прототипе — просто, живо, всё на одном экране, без телеграмных экранов»).

Каждый экран роли собирается из живых блоков View.ui (core/ui/views.py) — приложение рисует их само (web/app.html → UI):
  hero      — чёрная витрина: главная цифра, круг плана с отметкой «нужно к сегодня», пары внизу, кнопки;
  feed      — «Сейчас в фирме»: только то, что Алапа знает на самом деле (отчёты 1С, отметки людей, сигналы из отчётов);
  decide    — «Нужно ваше решение»: правки и заказы, утвердить / отклонить одним нажатием, экран остаётся на месте;
  tiles     — отделы плитками с главной цифрой;
  rows      — список со значком-отметкой (маршрут, приёмка, «скоро закончится»);
  explain   — зарплата строками, нажал — объяснение;
  team      — «кто где отстаёт»: люди × показатели цветом, нажал — подробно и действия;
  form      — форма на одном экране (отчёт дня, «не согласен»).
Нажатия, которые что-то меняют (lv…), возвращают тот же экран — приложение оставляет прокрутку на месте.
Ядро не знает клиентов: всё берётся из базы, анкеты и модулей, которые у клиента подключены.
"""
from datetime import date, datetime, timedelta

from core.calendar import MONTHS_GEN, month_title
from core.kpi.engine import SELLERS
from core.ui.kit import Reply, money, pct
from core.ui.registry import screen
from core.ui.views import View

ICON = {"sales": "📈", "warehouse": "📦", "logistics": "🚚", "purchasing": "🛒", "office": "🏛"}


def short(fio):
    p = (fio or "").split()
    return f"{p[0]} {p[1][0]}." if len(p) > 1 else (fio or "")


def _hm(ts, today):
    """Время события: «10:15» сегодня, «вчера 19:10», «16.11» раньше."""
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return ""
    if t.date() == today:
        return t.strftime("%H:%M")
    if t.date() == today - timedelta(days=1):
        return "вчера"
    return t.strftime("%d.%m")


def _tone(m):
    return {"good": "good", "warn": "warn", "bad": "bad"}.get(getattr(m, "tone", None), "")


def _sales_dept(ctx):
    return next((d for d in ctx.cfg.departments if d.kind == "sales"), None)


# ---------- витрина ----------

def hero(v, label, big, sub="", p=None, need=None, pairs=(), acts=(), md=False, ident=""):
    v.ui("hero", {"label": label, "big": big, "sub": sub, "p": p, "need": need, "md": md, "id": ident or label,
                  "pairs": [list(x) for x in pairs], "acts": [list(x) for x in acts]},
         fallback=[f"{label}: {big}", sub] + [f"{a}: {b}" for a, b, *_ in pairs])


def plan_hero(v, ctx, m, label, extra=(), md=False, ident=""):
    s = m["sales"]
    sub = f"план {money(s.plan)} · к сегодня нужно {pct(s.need)}" if s.plan else "план не задан"
    hero(v, label, money(s.fact), sub, p=s.pct if s.plan else None, need=s.need if s.plan else None,
         pairs=extra, md=md, ident=ident)


# ---------- «Сейчас в фирме» ----------

def feed(ctx, limit=6):
    """События по-честному: что пришло из 1С, кто сдал отчёт дня, кто выехал и вернулся, что согласовали и приняли,
    у кого просрочка. Заказов «в реальном времени» тут нет — их нет и в данных (Адам 17.11)."""
    db, today, ev = ctx.db, ctx.today, []
    names = {e["id"]: short(e["fio"]) for e in db.employees(active=None)}
    last = db.q("SELECT kind, ts FROM batches ORDER BY id DESC LIMIT 30")
    if last:                                                   # один раз: какие отчёты пришли последними
        top = last[0]["ts"][:16]
        kinds = []
        for b in last:
            if b["ts"][:16] == top and b["kind"] in REPORTS and REPORTS[b["kind"]] not in kinds:
                kinds.append(REPORTS[b["kind"]])
        if kinds:
            ev.append((last[0]["ts"], "📊", f"Пришли отчёты 1С: {', '.join(kinds[:4])}{' и др.' if len(kinds) > 4 else ''} — цифры обновились"))
    if ctx.can("route"):
        since = (today - timedelta(days=1)).isoformat()
        for r in db.q("SELECT day, emp_id, ts FROM visit_reports WHERE day >= ? ORDER BY ts DESC LIMIT 12", since):
            if r["emp_id"] not in names:
                continue
            ms = db.q("SELECT status, amount FROM visit_marks WHERE day = ? AND emp_id = ?", r["day"], r["emp_id"])
            sold = sum(1 for x in ms if x["status"] == "sold")
            total = sum(x["amount"] or 0 for x in ms)
            ev.append((r["ts"], "📋", f"{names.get(r['emp_id'], '?')} сдал отчёт дня: {_n(sold, 'заказ', 'заказа', 'заказов')}" +
                       (f" на {money(total)}" if total else "")))
    if ctx.can("logistics"):
        from core.calendar import month_of
        allm = db.get(f"dm:{month_of(today)}") or {}
        for eid, days in allm.items():
            if int(eid) not in names:                          # не в этой сборке демо
                continue
            for d, m in days.items():
                if d < (today - timedelta(days=1)).isoformat():
                    continue
                who = names.get(int(eid), "Водитель")
                if m.get("back"):
                    ev.append((f"{d}T{m['back']}:00", "🏁", f"{who} вернулся из рейса"))
                elif m.get("out"):
                    ev.append((f"{d}T{m['out']}:00", "🚚", f"{who} выехал в рейс"))
    if ctx.can("stock") or ctx.can("warehouse"):
        import json
        for o in db.q("SELECT id, supplier, extra FROM stock_orders ORDER BY id DESC LIMIT 10"):
            for h in (json.loads(o["extra"] or "{}").get("hist") or [])[-1:]:
                ts = _hist_ts(h[0], today)
                if ts:
                    ev.append((ts, "📦", f"Заказ поставщику {o['supplier']}: {h[2]}"))
    if ctx.can("debts"):
        k = ctx.kpi()
        worst = []
        for eid, rs in k.debts.items():
            if eid not in names:
                continue
            for r in rs:
                over = r["d61_90"] + r["d91"]
                if over > 0:
                    worst.append((over, r["client"], eid))
        for over, code, eid in sorted(worst, reverse=True)[:2]:
            c = db.one("SELECT name FROM clients WHERE code = ?", code)
            ev.append((f"{today.isoformat()}T07:00:00", "⚠️", f"«{c['name'] if c else code}»: долг старше 60 дней {money(over)} · {names.get(eid, '')}"))
    ev.sort(key=lambda x: str(x[0]), reverse=True)
    return [[_hm(t, today), i, x] for t, i, x in ev[:limit]]


REPORTS = {"sales": "Продажи", "debts": "Дебиторка", "payments": "Оплаты", "visits": "Маршруты", "stock": "Остатки",
           "trips": "Рейсы водителей", "returns": "Возвраты", "item_sales": "Продажи по товарам"}


def _n(k, one, few, many):
    """«1 заказ», «3 заказа», «7 заказов»."""
    k = int(k)
    w = one if k % 10 == 1 and k % 100 != 11 else few if 2 <= k % 10 <= 4 and not 12 <= k % 100 <= 14 else many
    return f"{k} {w}"


def _hist_ts(s, today):
    """«17.11 10:15» из истории заказа → ISO."""
    try:
        d, t = s.split()
        day, mon = d.split(".")
        return f"{today.year}-{int(mon):02d}-{int(day):02d}T{t}:00"
    except ValueError:
        return ""


# ---------- «Нужно ваше решение» ----------

def decisions(ctx):
    out = []
    if ctx.can("approval") and ctx.role in ("owner", "admin"):
        from core.month import cycle
        names = {e["id"]: e["fio"] for e in ctx.db.employees(active=None)}
        for e in [x for x in cycle.edits(ctx.db, ctx.month, "wait") if x["emp_id"] in names][:5]:
            sign = "Премия" if e["amount"] > 0 else "Удержание"
            out.append({"t": f"{sign} {short(names.get(e['emp_id'], '?'))} {money(abs(e['amount']))}",
                        "why": f"{e['reason']} — {e['author']}",
                        "ok": ["✓ Утвердить", f"lvd:e:{e['id']}:1"], "no": ["Отклонить", f"lvd:e:{e['id']}:0"]})
    if ctx.can("stock"):
        try:
            from modules import stock as SM
            s = SM.load(ctx)
            for oid in [r["id"] for r in SM.waiting(ctx)][:3]:
                o = SM._order(ctx, oid)
                if not o or not SM.can_approve(ctx, o):
                    continue
                out.append({"t": f"Заказ поставщику {o['supplier']} на {money(SM.total(s, o))}",
                            "why": f"{_n(len(o['items']), 'позиция', 'позиции', 'позиций')} · {o['author']}",
                            "ok": ["✓ Согласовать", f"lvd:o:{o['id']}"], "more": [["Открыть", f"sko:{o['id']}"]]})
        except Exception:                                          # склад без остатков — решений по заказам нет
            pass
    for d in ctx.session.get("lv_done") or []:
        out.append({"t": d[0], "why": d[1], "done": d[2]})
    return out


@screen("lvd")
def decide(ctx, kind, ident, ok=None):
    """Решение прямо на главной: экран остаётся, карточка показывает «утверждено»."""
    done = ctx.session.setdefault("lv_done", [])
    if kind == "e" and ctx.can("approval") and ctx.role in ("owner", "admin"):
        from core.month import cycle
        e = ctx.db.one("SELECT * FROM edits WHERE id = ?", int(ident))
        try:
            cycle.decide(ctx.db, int(ident), ok == "1", ctx.author())
            emp = ctx.db.employee(e["emp_id"])
            done.insert(0, [f"{'Премия' if e['amount'] > 0 else 'Удержание'} {short(emp['fio'])} {money(abs(e['amount']))}",
                            e["reason"], "✓ Утверждено — сотрудник увидит в своей зарплате" if ok == "1" else "✕ Отклонено"])
        except Exception as err:
            done.insert(0, ["Правка", str(err), "⚠️ Не получилось"])
    elif kind == "o" and ctx.can("stock"):
        from modules import stock as SM
        o = SM._order(ctx, int(ident))
        if o and o["status"] == "wait" and SM.can_approve(ctx, o):
            SM.order_approve(ctx, int(ident))
            done.insert(0, [f"Заказ поставщику {o['supplier']}", _n(len(o['items']), 'позиция', 'позиции', 'позиций'), "✓ Согласован — закупщику ушло уведомление"])
    ctx.session["lv_done"] = done[:4]
    ctx.save()
    from core.ui.screens import home
    return home(ctx)


# ---------- главная директора ----------

def dept_tiles(ctx):
    out = []
    k = ctx.kpi()
    for d in ctx.cfg.departments:
        if not ctx.cfg.dept_on(d.id):
            continue
        from core.ui.retail import shop_dept
        shop = d.id == shop_dept(ctx.cfg)
        to = "rto" if shop else "sales" if d.kind == "sales" else f"deptv:{d.kind}"
        n = len([e for e in k.employees.values() if e["dept"] == d.id and e["active"]])
        if d.kind == "sales" and not n:              # демо: отдел не в этом сегменте (опт без магазинов, розница без торговых)
            continue
        v, s = f"{n} чел.", ""
        if d.kind == "sales":
            m = k.scope("dept", d.id)
            v = pct(m["sales"].pct) if m["sales"].plan else money(m["sales"].fact)
            low = 0
            for e in k.employees.values():
                if e["dept"] == d.id and e["role"] in SELLERS and e["active"]:
                    es = k.emp(e["id"])["sales"]
                    low += bool(es.plan and es.pct < es.need * 0.8)
            s = (f"{low} {'продавцов' if shop else 'торговых'} отстают от графика" if low else "все идут по графику")
        elif d.kind in ("warehouse", "purchasing") and ctx.can("stock"):
            try:
                from modules import stock as SM
                st = SM.load(ctx)
                low = sum(1 for c in st.items if st.cat(c) in ("low", "out"))
                v, s = str(low), "позиций кончаются"
            except Exception:
                s = "склад"
        elif d.kind == "logistics" and ctx.can("logistics"):
            from modules import logistics as LG
            t = LG.trips(ctx)
            y = max((dd for rs in t.trips.values() for (dd, _n) in rs), default="")
            n_t = sum(1 for rs in t.trips.values() for (dd, _n) in rs if dd == y)
            v, s = f"{n_t} рейсов", f"за {date.fromisoformat(y).day} {MONTHS_GEN[date.fromisoformat(y).month - 1]}" if y else "рейсов нет"
        else:
            s = "в табеле"
        out.append([ICON.get(d.kind, "🏬"), d.name, v, s, to])
    return out


def owner_home(ctx, v):
    k = ctx.kpi()
    sd = _sales_dept(ctx)
    pairs = []
    if ctx.can("payroll"):
        fund = sum(ctx.pay().calc(e).total for e in k.employees.values() if e["active"])
        pairs.append(("Фонд зарплаты", money(fund), ""))
    if sd and ctx.can("debts"):
        o = k.scope("dept", sd.id)["overdue"]
        pairs.append(("Просрочка", pct(o.fact, 1), "bad" if o.tone == "bad" else ""))
    if sd:
        plan_hero(v, ctx, k.scope("dept", sd.id), f"Продажи · {month_title(ctx.month).lower()}", pairs, md=True, ident="own")
    v.ui("feed", {"items": feed(ctx), "live": "по отчётам и отметкам"}, "Сейчас в фирме")
    ds = decisions(ctx)
    if ds:
        v.ui("decide", {"items": ds}, "Ждёт решения")
    v.ui("tiles", {"items": dept_tiles(ctx)}, "Отделы")


# ---------- руководитель продаж и супервайзер ----------

def team_rows(ctx, es):
    k = ctx.kpi()
    rows = []
    for e in es:
        m = k.emp(e["id"])
        s, f, a, o = m["sales"], m["focus"], m["akb"], m["overdue"]
        left = max(s.plan - s.fact, 0)
        rows.append({"n": short(e["fio"]),
                     "cells": [[pct(s.pct) if s.plan else "—", _tone(s)], [pct(f.pct) if f.plan else "—", _tone(f)],
                               [pct(a.pct) if a.plan else "—", _tone(a)], [pct(o.fact) if o.fact is not None else "—", _tone(o)]],
                     "det": f"Продажи {money(s.fact)}" + (f" · до плана {money(left)} — по {money(left / max(s.left_days, 1))} в день" if left else " · план выполнен"),
                     "acts": [["📊 Подробно", f"emp:{e['id']}"]] + ([["📞 Кому позвонить", f"call:{e['id']}"]] if ctx.can("call_today") else [])})
    return rows


def head_home(ctx, v):
    k = ctx.kpi()
    if ctx.role == "sv" and ctx.me:
        m, es, label = k.scope("team", ctx.me["team"]), [e for e in k.employees.values() if e["team"] == ctx.me["team"]], "План команды"
    else:
        dept = ctx.me["dept"] if ctx.me else (_sales_dept(ctx).id if _sales_dept(ctx) else None)
        m, es, label = k.scope("dept", dept), [e for e in k.employees.values() if e["dept"] == dept], "План отдела"
    es = [e for e in es if e["active"] and e["role"] in SELLERS]
    s = m["sales"]
    hero(v, label, pct(s.pct) if s.plan else money(s.fact), f"{money(s.fact)} из {money(s.plan)} · к сегодня нужно {pct(s.need)}",
         p=s.pct if s.plan else None, need=s.need if s.plan else None, ident="head",
         pairs=[("До плана", money(max(s.plan - s.fact, 0)), ""), ("В день", money(s.per_day), "")])
    v.ui("team", {"head": ["Торговый", "План", "Фокус", "АКБ", "Долг"], "rows": team_rows(ctx, sorted(es, key=lambda e: e["fio"])),
                  "right": "нажмите на человека",
                  "note": "Зелёный — выполнено, жёлтый — дотягивает, красный — отстаёт. Долг — доля просрочки."}, "Кто где отстаёт",
         fallback=[f"{short(e['fio'])}: {pct(k.emp(e['id'])['sales'].pct)}" for e in es])
    if ctx.can("route"):
        from modules import route as RT
        day = ctx.today.isoformat()
        if not any(RT.formed(ctx, e["id"], day) for e in es):          # сегодня ещё никто не сдал — показываем вчера
            day = (ctx.today - timedelta(days=1)).isoformat()
        items = []
        for e in es:
            sm = RT.summary(ctx, e["id"], day)
            if not sm["points"]:
                continue
            items.append({"t": short(e["fio"]), "s": f"был в {sm.get('sold', 0) + sm.get('visited', 0)} из {sm['points']} · с заказом {sm.get('sold', 0)}",
                          "chip": ["сдал", f"rtv:{e['id']}:{day}", "good"] if sm["formed"] else ["ещё нет", "", "warn"]})
        if items:
            n = sum(1 for x in items if x["chip"][0] == "сдал")
            v.ui("rows", {"items": items, "right": f"{n} из {len(items)} сдали"},
                 "Отчёты дня" + ("" if day == ctx.today.isoformat() else " · вчера"))


# ---------- торговый ----------

# Нажатие по точке: отметить → заказ → был без заказа → не был → снова пусто (Адам 17.11: слова понятные без пояснений).
ST_NEXT = {None: "sold", "sold": "visited", "visited": "missed", "missed": None}
ST_CHIP = {None: ("отметить", ""), "sold": ("заказ", "good"), "visited": ("без заказа", "warn"), "missed": ("не был", "bad")}


def rep_home(ctx, v):
    e = ctx.me
    k = ctx.kpi()
    m = k.emp(e["id"])
    s = m["sales"]
    left = max(s.plan - s.fact, 0)
    pairs = []
    if ctx.can("payroll"):
        pairs.append(("Зарплата сейчас", money(ctx.pay().calc(e).total), ""))
    if m["akb"].plan:
        pairs.append(("АКБ", f"{int(m['akb'].fact)} из {int(m['akb'].plan)}", ""))
    hero(v, "Мой план", pct(s.pct) if s.plan else money(s.fact),
         (f"до плана {money(left)} · по {money(left / max(s.left_days, 1))} в день" if left else "план выполнен 🎉") if s.plan else "",
         p=s.pct if s.plan else None, need=s.need if s.plan else None, pairs=pairs, ident="rep")
    if ctx.can("route"):
        from modules import route as RT
        day = ctx.today.isoformat()
        pts = RT.points(ctx, e["id"], day)
        if pts:
            mk = RT.marks(ctx, e["id"], day)
            formed = RT.formed(ctx, e["id"], day)
            items = []
            for c in pts:
                cl = RT._client(ctx, c)
                st = (mk.get(c) or {}).get("status")
                lab, col = ST_CHIP.get(st, ("отметить", ""))
                items.append({"t": cl["name"], "s": cl["town"] or "", "chip": [lab, "" if formed else f"lvr:{day}:{c}", col]})
            done = sum(1 for c in pts if (mk.get(c) or {}).get("status"))
            v.ui("rows", {"items": items, "right": f"отмечено {done} из {len(pts)}"},
                 "Маршрут сегодня")
        rep_form(ctx, v, day)
    if ctx.can("call_today"):
        calls = call_list(ctx, e)
        if calls:
            v.ui("rows", {"items": calls}, "Кому позвонить сегодня")


# Отчёт дня (Адам 17.11: «своими словами, без чужих сокращений»): что бот знает по отметкам — считает сам, торговый вписывает только то,
# чего бот знать не может. (ключ, подпись, подсказка, число, во всю ширину)
FIELDS = [("coll", "💵 Собрал денег, ₽", "0", True, False), ("km", "🚗 Проехал, км", "0", True, False),
          ("note", "💬 Что важно знать руководителю", "Например: «Стройдвор» просит отсрочку до пятницы", False, True)]


def _day_counts(sm):
    """«был в 9 из 12 · с заказом 6 · без заказа 3 · не был 1 · не отмечено 2» — по отметкам маршрута."""
    was = sm.get("sold", 0) + sm.get("visited", 0)
    out = [f"был в {was} из {sm['points']}", f"с заказом {sm.get('sold', 0)}", f"без заказа {sm.get('visited', 0)}"]
    if sm.get("missed"):
        out.append(f"не был {sm['missed']}")
    if sm.get("none"):
        out.append(f"не отмечено {sm['none']}")
    return " · ".join(out)


def rep_form(ctx, v, day):
    """Отчёт дня одной формой: сверху — что бот посчитал сам, ниже — три строки руками."""
    key = f"dayrep:{ctx.me['id']}:{day}"
    saved = ctx.db.get(key) or {}
    from modules import route as RT
    sm = RT.summary(ctx, ctx.me["id"], day)
    done = f"✓ Отправлен руководителю в {saved['ts']}" if saved.get("ts") else ""
    v.ui("form", {"top": ["Бот посчитал по отметкам", _day_counts(sm)],
                  "fields": [{"k": k, "l": l, "ph": ph, "num": n, "money": k == "coll", "area": k == "note", "w": w,
                              "v": saved.get(k, "")} for k, l, ph, n, w in FIELDS],
                  "send": ["Отправить отчёт", f"lvf:{day}"], "done": done, "right": "отправлен" if done else "до вечера"},
         "Отчёт за день", fallback=[f"Отчёт за день: {_day_counts(sm)}"])


def _money_in(x):
    s = str(x or "").lower().replace(" ", "").replace("\xa0", "").replace(",", ".").replace("₽", "").replace("км", "").replace("руб", "")
    mult = 1000 if s.endswith(("т", "к", "k")) else 1
    s = s.rstrip("ткk")
    try:
        return float(s) * mult if s else 0
    except ValueError:
        return None


@screen("lvf")
def send_form(ctx, day):
    if not ctx.me or ctx.me["role"] not in SELLERS:
        return Reply(text="Отчёт дня сдаёт торговый.")
    form = getattr(ctx, "form", None) or {}
    from modules import route as RT
    from core.ui.screens import home
    out, nums, bad = {}, {}, []
    for k, l, _ph, n, _w in FIELDS:
        val = str(form.get(k, "")).strip()[:300 if k == "note" else 30]
        if n and val:
            num = _money_in(val)
            if num is None or num < 0:
                bad.append(l.split(" ", 1)[1].split(",")[0])
                continue
            nums[k] = num
        out[k] = val
    if bad:
        return [Reply(text=f"Не понял: {', '.join(bad)}. Напишите просто числом, например 45000."), home(ctx)]
    sm = RT.summary(ctx, ctx.me["id"], day)
    if not (sm.get("sold") or sm.get("visited") or sm.get("missed")) and not any(out.values()):
        return [Reply(text="Отметьте точки в маршруте или впишите, сколько собрали и проехали."), home(ctx)]
    if nums.get("km") and RT._can_mark(ctx, ctx.me["id"], day):
        ctx.db.x("DELETE FROM timesheet WHERE day = ? AND emp_id = ? AND kind = 'km'", day, ctx.me["id"])
        ctx.db.add_timesheet(day, ctx.me["id"], "km", amount=nums["km"], who=ctx.author())
    out["ts"] = datetime.now().strftime("%H:%M")
    ctx.db.put(f"dayrep:{ctx.me['id']}:{day}", out)
    if not RT.formed(ctx, ctx.me["id"], day):
        ctx.db.x("INSERT OR IGNORE INTO visit_reports (day, emp_id, ts, auto) VALUES (?,?,?,0)", day, ctx.me["id"],
                 datetime.now().isoformat(timespec="seconds"))
    was = sm.get("sold", 0) + sm.get("visited", 0)
    line = f"был в {was} из {sm['points']} точек, {_n(sm.get('sold', 0), 'заказ', 'заказа', 'заказов')}"
    if "coll" in nums:
        line += f", собрал {money(nums['coll'])}"
    if "km" in nums:
        line += f", {nums['km']:g} км"
    text = f"📋 {short(ctx.me['fio'])}: {line}" + (f"\n💬 {out['note']}" if out.get("note") else "")
    msgs = [Reply(text=text, to=x["tg_id"]) for x in RT._leads_of(ctx, ctx.me) if x.get("tg_id")]
    return msgs + [home(ctx)]


@screen("lvr")
def mark_point(ctx, day, code):
    """Отметка точки одним нажатием: отметить → заказ → без заказа → не был → снова пусто."""
    from modules import route as RT
    if not ctx.me or not RT._can_mark(ctx, ctx.me["id"], day) or code not in RT.points(ctx, ctx.me["id"], day):
        from core.ui.screens import home
        return [Reply(text="Отмечать можно свои точки за сегодня, пока отчёт не отправлен."), home(ctx)]
    m = RT.marks(ctx, ctx.me["id"], day).get(code)
    nxt = ST_NEXT.get(m["status"] if m else None)
    if nxt:
        RT._save(ctx, day, code, nxt, "", (m or {}).get("amount") or 0)
    else:
        ctx.db.x("DELETE FROM visit_marks WHERE day = ? AND emp_id = ? AND client = ?", day, ctx.me["id"], code)
    from core.ui.screens import home
    return home(ctx)


def call_list(ctx, e):
    k = ctx.kpi()
    out = []
    for r in sorted(k.debts.get(e["id"], []), key=lambda r: -(r["d31_60"] + r["d61_90"] + r["d91"]))[:2]:
        over = r["d31_60"] + r["d61_90"] + r["d91"]
        if over > 0:
            c = ctx.db.one("SELECT name, phone FROM clients WHERE code = ?", r["client"]) or {"name": r["client"], "phone": ""}
            out.append({"t": c["name"], "s": f"просрочка {money(over)}" + (f" · {c['phone']}" if c["phone"] else ""), "chip": ["💵", "", "bad"]})
    from core.calendar import shift_month
    p1 = ctx.kpi(shift_month(ctx.month, -1))
    now = k.by_client.get(e["id"], {})
    if (ctx.today - k.cal.start).days >= 10:
        for c, val in sorted(p1.by_client.get(e["id"], {}).items(), key=lambda t: -t[1])[:8]:
            if val > 0 and now.get(c, 0) <= 0:
                cl = ctx.db.one("SELECT name, phone FROM clients WHERE code = ?", c) or {"name": c, "phone": ""}
                out.append({"t": cl["name"], "s": f"в прошлом месяце {money(val)}, в этом — ничего" + (f" · {cl['phone']}" if cl["phone"] else ""),
                            "chip": ["😴", "", "warn"]})
            if len(out) >= 4:
                break
    return out


# ---------- склад, закупки ----------

def stock_rows(ctx, limit=6):
    from modules import stock as SM
    try:
        s = SM.load(ctx)
    except Exception:
        return []
    if not s.on:
        return []
    my = {c for _i, _s, cs in s.my_suppliers(ctx) for c in cs}
    rows = []
    for code in my:
        cat = s.cat(code)
        if cat not in ("out", "low"):
            continue
        d = s.days_left(code)
        rows.append((0 if cat == "out" else d or 0, code, cat, d))
    rows.sort()
    out = []
    for _k, code, cat, d in rows[:limit]:
        it = s.items[code]
        out.append({"t": it.get("name") or code, "s": ("нет на складе" if cat == "out" else f"хватит на {int(d)} дн.") +
                    (f" · нужно {s.need(code)} {it.get('unit') or 'шт'}" if s.need(code) else ""),
                    "bar": [0.02 if cat == "out" else min(1, (d or 0) / 30), "bad" if cat == "out" or (d or 0) < 7 else "warn"]})
    return out


def staff_home(ctx, v):
    e = ctx.me
    p = ctx.pay().calc(e) if ctx.can("payroll") else None
    if ctx.duty == "driver" and ctx.can("logistics"):
        return driver_home(ctx, v, p)
    hero(v, "Зарплата сейчас" if p else "Рабочих дней", money(p.total) if p else f"{ctx.pay().worked_days(e):g}",
         f"отработано {p.days:g} из {p.days_total} дн." if p else "", ident="staff")
    if ctx.dept_kind in ("warehouse", "purchasing") and (ctx.can("stock")):
        try:
            from modules import stock as SM
            if SM.ok(ctx):
                rs = stock_rows(ctx)
                if rs:
                    v.ui("rows", {"items": rs, "foot": [["📦 Весь склад", "sk"]]}, "Скоро закончится")
        except Exception:
            pass
    if ctx.dept_kind == "warehouse" and ctx.can("warehouse"):
        import json
        items = []
        for o in ctx.db.q("SELECT id, supplier, items, sent_ts FROM stock_orders WHERE status = 'sent' ORDER BY id DESC LIMIT 4"):
            n = len(json.loads(o["items"] or "[]"))
            sent = str(o["sent_ts"] or "")
            sent = f" · отправлен {sent[8:10]}.{sent[5:7]}" if len(sent) >= 10 else ""
            items.append({"t": o["supplier"], "s": f"{_n(n, 'позиция', 'позиции', 'позиций')}{sent}", "chip": ["принять", f"wrc:{o['id']}", "warn"]})
        v.ui("rows", {"items": items, "empty": "Поставок в пути нет"}, "Приёмка")
    if ctx.dept_kind == "warehouse":                    # учёт в Алапе: погрузка по водителям и перемещения в салоны (modules/ship.py)
        from modules import ship as SH
        if SH.active(ctx):
            orders = SH.to_load(ctx)
            items = []
            for d in SH.drivers(ctx):
                mine = [o for o in orders if o.get("driver") == d["id"]]
                if mine:
                    on = sum(1 for o in mine if o.get("load") == "on")
                    items.append({"t": f"🚚 {short(d['fio'])}", "s": f"{_n(len(mine), 'заказ', 'заказа', 'заказов')}" + (f" · погружено {on}" if on else ""),
                                  "chip": ["грузить", f"ld:d:{d['id']}", "warn"]})
            nod = [o for o in orders if not o.get("driver")]
            if nod:
                items.append({"t": "Без водителя", "s": f"{_n(len(nod), 'заказ', 'заказа', 'заказов')} · назначит менеджер", "chip": ["смотреть", "ld:d:0", ""]})
            mv = ctx.db.one("SELECT COUNT(*) AS n FROM moves WHERE status = 'ask'")["n"]
            if mv:
                items.append({"t": "🔁 В салоны", "s": f"{_n(mv, 'перемещение', 'перемещения', 'перемещений')} ждут отгрузки", "chip": ["открыть", "mv", "warn"]})
            v.ui("rows", {"items": items, "empty": "Сегодня грузить нечего"}, "Погрузка сегодня")
    if ctx.dept_kind == "warehouse":                    # учёт в Алапе: приходы от закупок ждут пересчёта (modules/acc.py)
        rs = ctx.db.q("SELECT id, supplier, lines, day FROM receipts WHERE status = 'sent' ORDER BY day, id LIMIT 6")
        if rs:
            import json
            v.ui("rows", {"items": [{"t": f"Приход №{r['id']} · {r['supplier']}",
                                     "s": f"{_n(len(json.loads(r['lines'])), 'позиция', 'позиции', 'позиций')} · пересчитать и принять",
                                     "chip": ["принять", f"rca:{r['id']}", "warn"]} for r in rs]}, "Приходы ждут склад")


def driver_home(ctx, v, p):
    from modules import logistics as LG
    e, d = ctx.me, ctx.today
    m = LG._get(ctx, e["id"], d)
    state = "Вернулся" if m.get("back") else "В пути" if m.get("out") else "Не выехал"
    sub = (f"выехал в {m['out']}" if m.get("out") else "отметьте выезд одной кнопкой") + (f" · вернулся в {m['back']}" if m.get("back") else "")
    acts = [] if m.get("back") else [["🏁 Вернулся", f"lvm:r"]] if m.get("out") else [["🚚 Выехал", f"lvm:o"]]
    t = LG.trips(ctx)
    month_trips = t.trips.get(e["id"], {})
    pairs = [("Зарплата сейчас", money(p.total), "")] if p else []
    pairs.append(("Рейсов за месяц", str(len(month_trips)), ""))
    hero(v, f"Мой день · {d.day} {MONTHS_GEN[d.month - 1]}", state, sub, pairs=pairs, acts=acts, ident="drv")
    last = max((dd for (dd, _n) in month_trips), default="")
    items = []
    for (dd, n), r in sorted(month_trips.items()):
        if dd == last:
            items.append({"t": f"Рейс №{n}", "s": f"{_n(len(r['stops']), 'точка', 'точки', 'точек')} · {round(r['kg'])} кг", "v": f"{round(r['km'])} км"})
    if items:
        v.ui("rows", {"items": items}, f"Рейсы · {date.fromisoformat(last).day} {MONTHS_GEN[date.fromisoformat(last).month - 1]}")
    from modules import ship as SH
    if SH.active(ctx):                                   # учёт в Алапе: маршрутный лист из погрузки
        s = ctx.db.one("SELECT * FROM route_sheets WHERE driver = ? AND status = 'open' ORDER BY id DESC LIMIT 1", e["id"])
        if s:
            import json
            ids = json.loads(s["orders"])
            v.ui("rows", {"items": [{"t": f"🗺 Маршрутный лист №{s['id']}", "s": f"{_n(len(ids), 'точка', 'точки', 'точек')} · выехал {s['ts_out'][11:16]}",
                                     "chip": ["открыть", f"rs:{s['id']}", "warn"]}]}, "Сегодня везу")
        else:
            wait = [o for o in SH.to_load(ctx) if o.get("driver") == e["id"]]
            if wait:
                v.ui("rows", {"items": [{"t": "Ждут погрузки на складе", "s": _n(len(wait), "заказ", "заказа", "заказов")}]}, "Сегодня везу")
    if p and p.rows:
        explain(v, p, "Заработано за месяц")


@screen("lvm")
def driver_mark(ctx, what):
    from modules import logistics as LG
    from core.ui.screens import home
    if not ctx.me or ctx.duty != "driver":
        return home(ctx)
    now = datetime.now().strftime("%H:%M")
    m = dict(LG._get(ctx, ctx.me["id"], ctx.today))
    if what == "o":
        m["out"] = now
    elif what == "r":
        m["back"], m["night"] = now, 0
    LG._put(ctx, ctx.me["id"], ctx.today, {k: x for k, x in m.items() if x})
    return home(ctx)


# ---------- зарплата строками ----------

def explain(v, slip, title=""):
    v.ui("explain", {"items": [[r[0], ("− " if r[1] < 0 else "") + money(abs(r[1])), r[2], r[1] < 0] for r in slip.rows],
                     "total": ["Итого", money(slip.total)]}, title,
         fallback=[f"{r[0]}: {money(r[1])}" for r in slip.rows] + [f"Итого: {money(slip.total)}"])


def disagree(v, ctx, emp, sent=False):
    v.ui("form", {"fields": [{"k": "text", "l": "Что не так?", "ph": "Например: 15-го был у клиента, а в табеле пропуск", "area": True, "w": True}],
                  "send": ["✋ Отправить руководителю", f"lvq:{emp['id']}"], "done": "✓ Ушло руководителю — ответ придёт сюда же" if sent else ""},
         "Не согласен с расчётом")


@screen("lvq")
def send_disagree(ctx, emp_id):
    from modules.payroll import payslip
    emp = ctx.db.employee(int(emp_id))
    text = str((getattr(ctx, "form", None) or {}).get("text", "")).strip()[:600]
    if not emp or not ctx.me or emp["id"] != ctx.me["id"]:
        return payslip(ctx, emp_id)
    if len(text) < 3:
        return [Reply(text="Напишите, что не так, — хотя бы пару слов."), payslip(ctx, emp_id)]
    es = ctx.db.employees()
    leads = [x for x in es if (x["role"] == "sv" and x["team"] == emp["team"]) or (x["role"] == "head" and x["dept"] == emp["dept"])
             or x["role"] == "owner"]
    ctx.db.audit(ctx.author(), "pay_disagree", f"{ctx.month}: {text}")
    ctx.session[f"lvq:{ctx.month}"] = 1
    ctx.save()
    msgs = [Reply(text=f"✋ {emp['fio']} не согласен с расчётом за {month_title(ctx.month).lower()}:\n{text}", to=x["tg_id"])
            for x in leads if x.get("tg_id")]
    return msgs + [payslip(ctx, emp_id)]
