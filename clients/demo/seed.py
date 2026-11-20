"""Наполнение базы демо выдуманной фирмой: люди, клиенты, 4 месяца продаж, долги, деньги, маршруты,
планы, табель, правки, задачи, отметки маршрута, тесты, склад, рейсы, возвраты.

    seed(db, cfg, industry="food", today=date.today())

Один и тот же seed даёт одни и те же цифры. Отрасль меняет только названия групп и клиентов —
люди и масштабы те же, чтобы демо выглядело одинаково живо в любой отрасли.

Характер команды заложен специально, чтобы экранам было что показать: двое лидеров, середняки,
двое отстающих, «засыпающие» клиенты, проблемные долги, опоздания и прогул.
"""
import random
from datetime import date, timedelta

from clients.demo import vocab as V
from core.calendar import Month, first_day, last_day, month_of, shift_month
from core.data.model import (Client, Dataset, Debt, Group, ItemSale, Payment, Return, Sale, Stock, Trip, TripStop,
                             Visit)

VERSION = 8          # поменялся наполнитель — поднять: демо-базы пересоздадутся сами при следующем открытии (20.11: склад и салоны, листы)

# команда → (сколько торговых, характеры: множитель темпа к плану)
TEAMS = {"north": [1.22, 1.06, 0.98, 0.93, 0.74], "south": [1.12, 1.01, 0.95, 0.88, 0.79], "key": [1.04, 0.97]}
STAFF = [("rop", "sales", "", 1), ("director", "office", "", 1), ("accountant", "office", "", 1),
         ("manager", "office", "", 1), ("wh_head", "warehouse", "", 1), ("storekeeper", "warehouse", "", 3),
         ("log_head", "logistics", "", 1), ("driver", "logistics", "", 4), ("buy_head", "purchasing", "", 1),
         ("buyer", "purchasing", "", 2)]
SUPPLIERS = ["Завод «Север»", "ТД «Восток»", "Импорт-Трейд", "Фабрика «Юг»", "Дистрибьютор «Центр»", "Опт-Снаб"]
VARIANTS = ["эконом", "стандарт", "премиум", "профи", "мини", "макси", "XL", "лайт", "плюс", "супер"]


def seed(db, cfg, industry=None, today=None, rnd_seed=7, months=4):
    industry = industry or cfg.industry
    today = today or date.today()
    R = random.Random(rnd_seed)
    names = _names(R)
    months_list = [shift_month(month_of(today), -i) for i in reversed(range(months))]
    m0 = first_day(months_list[0])

    # ---------- люди ----------
    reps = []     # (emp_id, key, team, skill)
    for team, skills in TEAMS.items():
        if team != "key":
            fio, _ = next(names)
            db.add_employee(fio, "sales", team=team, position="sv", role="sv", hired=m0 - timedelta(days=700))
        for skill in skills:
            fio, key = next(names)
            hired = m0 - timedelta(days=R.randint(120, 1500))
            eid = db.add_employee(fio, "sales", team=team, position="rep", role="rep", keys=[key], hired=hired)
            reps.append((eid, key, team, skill))
    staff_keys = {}   # должность → [ключи]
    for pos, dept, team, n in STAFF:
        role = cfg.position(pos)["role"]
        for _ in range(n):
            fio, key = next(names)
            db.add_employee(fio, dept, team=team, position=pos, role=role, keys=[key],
                            hired=m0 - timedelta(days=R.randint(200, 2000)))
            staff_keys.setdefault(pos, []).append(key)
    # новичок: пришёл в текущем месяце на место ушедшего — ключ того же торговика с даты приёма
    leaver = next(r for r in reps if r[2] == "south" and r[3] < 0.85)
    start_new = first_day(months_list[-1]) + timedelta(days=min(9, (today - first_day(months_list[-1])).days // 2))
    fio, _ = next(names)
    newbie = db.add_employee(fio, "sales", team="south", position="rep", role="newbie", hired=start_new)
    db.c.execute("UPDATE employees SET fired = ?, active = 0 WHERE id = ?", ((start_new - timedelta(days=1)).isoformat(), leaver[0]))
    db.bind_key(leaver[1], newbie, since=start_new)
    rt = _retail_people(db, cfg, random.Random(rnd_seed + 300), industry, today, m0)     # 20.11: свои магазины

    # ---------- справочники ----------
    groups = V.GROUPS[industry]
    db.load(Dataset("groups", m0, last_day(months_list[-1]), source="seed",
                    rows=[Group(code, name, "main") for code, name, _ in groups]))
    clients = []      # (код, вес покупок, торговый, день маршрута 0–5, спит ли в этом месяце, проблемный долг)
    shops = V.SHOPS[industry]
    code = 1001
    for eid, key, team, skill in reps:
        n = 14 if team == "key" else R.randint(26, 32)
        for _ in range(n):
            size = R.lognormvariate(11.0 if team == "key" else 10.1, 0.55)
            clients.append([str(code), size, key, R.randrange(6), R.random() < 0.07, R.random() < 0.11])
            code += 1
    cli_rows = []
    for c in clients:
        shop = R.choice(shops)
        form = R.choice(V.FORMS)
        name = f"{form} «{shop}»" if form in ("ООО", "Магазин", "Маг.") else f"ИП {R.choice(V.SURNAMES_M)} · {shop}"
        cli_rows.append(Client(c[0], name, R.choice(V.TOWNS), f"8 000 000-{int(c[0]) % 100:02d}-{int(c[0]) % 97:02d}"))
    cli_rows += [Client(f"R-{t}", f"Покупатели · {n}", "", "") for t, n in rt["shops"]]
    db.load(Dataset("clients", m0, last_day(months_list[-1]), rows=cli_rows, source="seed"))

    skill_of = {key: skill for _, key, _, skill in reps}
    # 8% клиентов в этом месяце перестают брать одну из главных групп
    drops = {c[0]: R.choice([g[0] for g in groups[:3]]) for c in clients if R.random() < 0.08}
    weights = [w for _, _, w in groups]
    focus = [g[0] for g in groups[:(cfg.raw.get("kpi") or {}).get("focus_groups", 2)]]

    # ---------- продажи, деньги, маршруты, долги по месяцам ----------
    for mi, month in enumerate(months_list):
        cal = Month(cfg, month, today)
        current = mi == len(months_list) - 1
        workdays = cal.workdays
        sales, visits, pays, rets = [], [], [], []
        trend = 1 + 0.03 * mi
        for c in clients:
            ccode, size, key, wd, sleeping, _ = c
            skill = skill_of[key]
            for d in workdays:
                if d.weekday() != wd:
                    continue
                visits.append(Visit(d, key, ccode))
                if current and d >= today:
                    continue
                if current and sleeping:
                    continue
                if R.random() > min(0.55 + 0.25 * skill, 0.95):
                    continue
                order = size / 4.3 * skill * trend * R.uniform(0.6, 1.45)
                k = R.choice([1, 2, 2, 3])
                picked = set(R.choices([g[0] for g in groups], weights=weights, k=k))
                if current and drops.get(ccode) in picked and len(picked) > 1:
                    picked.discard(drops[ccode])          # «бросил бренд» в этом месяце
                for g in picked:
                    amt = round(order / len(picked) * R.uniform(0.7, 1.3), -1)
                    if g in focus:
                        amt = round(amt * (0.75 + 0.35 * skill), -1)
                    sales.append(Sale(d, key, ccode, g, amt, qty=max(1, round(amt / R.uniform(300, 1500))),
                                      discount=round(R.uniform(0, 0.08 if skill > 0.9 else 0.12), 3)))
                if R.random() < 0.08:
                    sales.append(Sale(d, key, ccode, R.choice(picked and list(picked)), -round(order * 0.15, -1)))
                    rets.append((d, key, ccode, round(order * 0.15, -1)))
        by_client = {}
        for s in sales:
            by_client[(s.emp_key, s.client)] = by_client.get((s.emp_key, s.client), 0) + s.amount
        for (key, ccode), total in by_client.items():
            if total <= 0:
                continue
            paid = total * min(0.97, 0.72 + 0.2 * skill_of[key]) * R.uniform(0.85, 1.1)
            d = R.choice([x for x in workdays if not current or x < today] or workdays[:1])
            pays.append(Payment(d, key, ccode, round(paid, -1)))
        end = min(cal.end, today - timedelta(days=1)) if current else cal.end
        rsales = _retail_month(db, rt, month, cal, today, groups, trend)
        if end >= cal.start:
            db.load(Dataset("sales", cal.start, cal.end, rows=[s for s in sales + rsales if s.day <= end], source="seed"))
            db.load(Dataset("payments", cal.start, cal.end, rows=[p for p in pays if p.day <= end], source="seed"))
        db.load(Dataset("visits", cal.start, cal.end, rows=visits, source="seed"))
        if end >= cal.start:
            _trips(db, R, cal, end, sales, staff_keys.get("driver", []))
            _returns(db, R, cal, end, rets, staff_keys)
        for snap in (7, 14, 21, cal.end.day):
            on = date(cal.start.year, cal.start.month, snap)
            if on >= today:
                break
            debts = []
            for c in clients:
                ccode, size, key, _, sleeping, problem = c
                recent = by_client.get((key, ccode), 0) * (snap / cal.end.day)
                fresh = max(recent * R.uniform(0.25, 0.5), 0)
                old = size * R.uniform(0.3, 0.9) if problem else (size * R.uniform(0, 0.12) if R.random() < 0.2 else 0)
                b = [fresh, old * 0.4, old * 0.35 if problem else 0, old * 0.25 if problem else 0]
                if sum(b) < 500:
                    continue
                b = [round(x) for x in b]
                debts.append(Debt(on, key, ccode, sum(b), *b))
            db.load(Dataset("debts", on, on, rows=debts, source="seed"))

        # ---------- планы ----------
        db.put(f"focus:{month}", focus)
        prev = {}
        for c in clients:
            prev[c[2]] = prev.get(c[2], 0) + c[1] * trend
        for eid, key, team, skill in reps:
            base = prev.get(key, 0)
            plan_sales = round(base * 0.82, -4)        # ≈ средний темп команды: лидеры выше, отстающие ниже
            mine = [c for c in clients if c[2] == key]
            plans = {
                "sales": plan_sales,
                "focus": round(plan_sales * sum(w for c_, _, w in groups if c_ in focus) / sum(weights) * 1.1, -3),
                "akb": round(len(mine) * 0.85),
                "sku": round(len(mine) * 2.4),
                "collect": round(plan_sales * 0.92, -4),
            }
            for g in focus:
                share = next(w for c_, _, w in groups if c_ == g) / sum(weights)
                plans[f"grp:{g}"] = round(plan_sales * share * 1.1, -3)
            for k, v in plans.items():
                if key == leaver[1] and current:
                    # план делится по дням: ушедшему — до его ухода, новичку — с первого дня
                    share_old = (start_new.day - 1) / cal.end.day
                    rnd = -3 if k in ("sales", "focus", "collect") or k.startswith("grp:") else 0
                    db.set_plan(month, eid, k, round(v * share_old, rnd), who="seed", src="план")
                    db.set_plan(month, newbie, k, round(v * (1 - share_old), rnd), who="seed", src="план")
                else:
                    db.set_plan(month, eid, k, v, who="seed", src="план")

        for eid, plan in rt["plans"].get(month, {}).items():
            db.set_plan(month, eid, "sales", plan, who="seed", src="план")

    # ---------- табель, правки, статусы месяцев ----------
    cur = months_list[-1]
    cal = Month(cfg, cur, today)
    passed = cal.passed_days()
    everyone = [e for e in db.employees() if e["dept"] != "shops" and e["position"] != "assembler"]   # магазины — свой генератор
    if passed:
        pick = R.sample(everyone, min(6, len(everyone)))
        for i, e in enumerate(pick):
            d = R.choice(passed)
            if i < 3:
                db.add_timesheet(d, e["id"], "late", minutes=R.choice([15, 35, 70]), who="seed")
            elif i == 3:
                db.add_timesheet(d, e["id"], "absent", note="по семейным", who="seed")
            elif i == 4:
                db.add_timesheet(d, e["id"], "truancy", who="seed")
            else:
                db.add_timesheet(d, e["id"], "comment", note="помог на складе", who="seed")
    rop = next(e for e in everyone if e["position"] == "rop")
    prev_m = months_list[-2] if len(months_list) > 1 else cur
    star = max(reps, key=lambda r: r[3])
    db.x("INSERT INTO edits (month, emp_id, amount, reason, author, status, decided_by, ts) VALUES (?,?,?,?,?,?,?,?)",
         prev_m, star[0], 10000, "Открыл 3 новые точки", rop["fio"], "ok", "Директор", f"{prev_m}-28T12:00:00")
    db.x("INSERT INTO edits (month, emp_id, amount, reason, author, status, ts) VALUES (?,?,?,?,?,?,?)",
         cur, reps[1][0], 5000, "Подменял коллегу на маршруте", rop["fio"], "wait", today.isoformat())
    db.x("INSERT INTO edits (month, emp_id, amount, reason, author, status, ts) VALUES (?,?,?,?,?,?,?)",
         cur, reps[-1][0], -2000, "Не сдал накладные вовремя", rop["fio"], "wait", today.isoformat())
    for m in months_list[:-2]:
        db.set_month_status(m, "approved", who="seed")
    if len(months_list) > 1:
        db.set_month_status(months_list[-2], "review" if today.day <= 5 else "approved", who="seed")

    # ---------- задачи, отпуска, документы ----------
    owner = next(e for e in everyone if e["role"] == "owner")
    task_texts = ["Выйти на новую точку в своём районе", "Собрать долг с проблемного клиента", "Выставить фокусную группу на полку",
                  "Сделать фото выкладки у ключевого клиента", "Проверить остатки у клиента перед акцией"]
    for i, (eid, key, team, skill) in enumerate(reps[:6]):
        due = today + timedelta(days=R.randint(-2, 5))
        status = "done" if i % 3 == 0 else "open"
        _task(db, rop if i % 2 else owner, [eid], task_texts[i % len(task_texts)], due, "ok" if status == "done" else "work", today)
    _route_marks(db, R, cfg, today)
    _stock(db, R, groups, months_list, today, PRICE_K.get(industry, 1))
    _quiz(db, R, today)
    _company(db, cfg, random.Random(rnd_seed + 100), today, months_list, newbie, start_new)
    _tasks_more(db, random.Random(rnd_seed + 200), today, owner, rop, reps)
    _retail_orders(db, rt, today)
    _accounting(db, random.Random(rnd_seed + 400), today, PRICE_K.get(industry, 1))                            # 20.11: учёт в Алапе
    _shop_sales(db, random.Random(rnd_seed + 500), today, rt)                           # 20.11: продажи в магазинах через приложение
    _locations(db, random.Random(rnd_seed + 600), today, rt)                            # 20.11: склад и салоны, погрузка, листы
    for m in months_list:
        db.put(f"planst:{m}", {"status": "ok", "note": "", "hist": [{"ts": f"{m}-01T09:00", "who": "Директор",
                                                                     "status": "ok", "note": ""}]})
    db.put("seeded", {"industry": industry, "seeded": today.isoformat(), "months": months_list, "v": VERSION})
    return {"reps": len(reps), "clients": len(clients), "months": months_list}


# ---------- свои магазины (Адам 20.11: сегменты «Опт + розница» и «Розница») ----------
# Всё со своим генератором случайностей: цифры опта от магазинов не сдвигаются.
SHOP_SELLERS = {"s1": 3, "s2": 3, "s3": 2}
AVG_CHECK = {"furniture": 24000, "electronics": 18000, "building": 7000}       # остальным отраслям — 2 500
BUYERS = ["Алиев Х.", "Байсаров И.", "Умаров М.", "Хасанов Р.", "Цакаев А.", "Исаев Т.", "Магомадов С.", "Абдулаев З.",
          "Эльмурзаев Б.", "Алиев Р.", "Хасанов М.", "Умаров А."]


def _retail_people(db, cfg, R, industry, today, m0):
    """Директора магазинов, продавцы, сборщики. Без должностей в анкете — магазинов нет."""
    out = {"R": R, "industry": industry, "shops": [], "sellers": [], "heads": {}, "asm": [], "plans": {}, "months": {}}
    if not (cfg.position("seller") and cfg.position("shop_head")):
        return out
    have = {" ".join(e["fio"].split()[:2]).lower() for e in db.employees(active=None)}
    gen = _names(R)

    def fresh():
        for fio, key in gen:
            if key.lower() not in have:
                have.add(key.lower())
                return fio, key
    shops = [{"id": t.id, "name": t.name} for t in cfg.teams_of("shops")]
    out["shops"] = [(t["id"], t["name"]) for t in shops]
    for t in shops:
        fio, key = fresh()
        out["heads"][t["id"]] = db.add_employee(fio, "shops", team=t["id"], position="shop_head", role="sv", keys=[key],
                                                hired=m0 - timedelta(days=R.randint(300, 1200)))
        for i in range(SHOP_SELLERS.get(t["id"], 2)):
            fio, key = fresh()
            eid = db.add_employee(fio, "shops", team=t["id"], position="seller", role="rep", keys=[key],
                                  hired=m0 - timedelta(days=R.randint(150, 1400)))
            out["sellers"].append((eid, key, t["id"], [1.12, 0.98, 0.86][i % 3] * R.uniform(0.95, 1.05), i % 2))
    if cfg.position("assembler") and V.ASSEMBLY.get(industry):          # сборщики — где после доставки собирают
        for _ in range(2):
            fio, key = fresh()
            out["asm"].append(db.add_employee(fio, "logistics", position="assembler", role="staff", keys=[key],
                                              hired=m0 - timedelta(days=R.randint(200, 900))))
    return out


def _retail_month(db, shop, month, cal, today, groups, trend):
    """Месяц магазинов: смены через день, чеки, рассрочка; продажи — строками по продавцу и группе (для % от продаж)."""
    if not shop["sellers"]:
        return []
    R = shop["R"]
    avg = AVG_CHECK.get(shop["industry"], 2500)
    inst_share = 0.22 if shop["industry"] in AVG_CHECK else 0.05
    weights = [w for _, _, w in groups]
    start, stop = cal.start, cal.end
    data = {"checks": {}, "shifts": {}, "came": {}, "inst": {}, "avg_bar": round(avg * 0.92, -2)}
    rows = []
    plans = {}
    for eid, key, team, skill, off in shop["sellers"]:
        days, checks, inst = [], {}, 0
        exp = 0
        d = start
        while d <= stop:
            if (d.toordinal() + off) % 2 == 0 or (d.weekday() == 5 and R.random() < 0.3):      # через день + субботы
                days.append(d.isoformat())
                n = max(1, round(55000 / avg * skill * trend * R.uniform(0.55, 1.45)))
                exp += 55000 / avg * skill * trend * avg
                if d < today:
                    tot, pos = 0, 0
                    by_g = {}
                    for _ in range(n):
                        amt = round(R.lognormvariate(0, 0.45) * avg * skill * 0.9, -2)
                        g = R.choices([x[0] for x in groups], weights=weights)[0]
                        by_g[g] = by_g.get(g, 0) + amt
                        tot += amt
                        pos += R.choice([1, 1, 2, 2, 3])
                        if R.random() < inst_share:
                            inst += 1
                    for g, a in by_g.items():
                        rows.append(Sale(d, key, f"R-{team}", g, a, qty=1, discount=round(R.uniform(0, 0.05), 3)))
                    checks[d.isoformat()] = [n, pos, tot]
            d += timedelta(days=1)
        data["shifts"][str(eid)] = days
        data["checks"][str(eid)] = checks
        data["inst"][str(eid)] = inst
        plans[eid] = round(exp * 0.95 * 0.92, -4)
    # сегодня — кто на смене и во сколько пришёл (один опоздал); кассы нет (Адам 20.11: «никакой кассы»)
    if month == today.strftime("%Y-%m"):
        came = {}
        on = [s_ for s_ in shop["sellers"] if today.isoformat() in data["shifts"][str(s_[0])]]
        for i, (eid, *_r) in enumerate(on):
            came[str(eid)] = f"10:{R.choice([18, 22, 26])}" if i == 1 else f"09:{R.randint(41, 58)}"
            if i == 1:
                db.add_timesheet(today, eid, "late", minutes=int(came[str(eid)][3:]), who="seed")
        data["came"] = {today.isoformat(): came}
    shop["plans"][month] = plans
    db.put(f"rt:{month}", data)
    return rows


STAGES = ["Заказ", "Оплата", "Доставка", "Сборка"]


def _retail_orders(db, shop, today):
    """Заказы с доставкой и рассрочка: открытые на всех этапах, закрытые за месяц — у сборщиков премия и жалобы."""
    import json  # noqa: F401
    if not shop["sellers"]:
        return
    R = random.Random(911)
    items = V.RETAIL_ITEMS.get(shop["industry"]) or [f"{g[1]} · комплект" for g in V.GROUPS[shop["industry"]][:6]]
    avg = AVG_CHECK.get(shop["industry"], 2500)
    asm_word = V.ASSEMBLY.get(shop["industry"], "")
    orders, inst = [], []
    n = 0
    for back in range(30, -12, -1):
        d = today - timedelta(days=back)
        for _ in range(R.choice([2, 3, 3, 4])):
            n += 1
            eid, key, team, *_r = R.choice(shop["sellers"])
            amt = round(avg * R.uniform(1.5, 8), -3)
            pay = R.choices(["prepay", "inst", "full"], weights=[50, 30, 20])[0]
            deliv = d + timedelta(days=R.randint(4, 16))
            if back < 0:                                 # будущее — не создаём
                continue
            if deliv < today - timedelta(days=1):
                stage = 4                                # закрыт
            elif deliv <= today:
                stage = 3 if R.random() < 0.5 else 2
            else:
                stage = 1 if R.random() < 0.6 else 2
            o = {"id": n, "day": d.isoformat(), "shop": team, "seller": eid, "client": R.choice(BUYERS), "item": R.choice(items),
                 "sum": amt, "pay": pay, "prepay": round(amt * 0.5, -3) if pay == "prepay" else 0,
                 "months": R.choice([6, 10, 12]) if pay == "inst" else 0, "deliv": deliv.isoformat(), "stage": stage,
                 "paid": pay != "prepay" or stage >= 2 or R.random() < 0.6, "late": deliv <= today and stage < 3 and R.random() < 0.5,
                 "asm": shop["asm"][n % len(shop["asm"])] if shop["asm"] and asm_word else None,
                 "complaint": stage == 4 and R.random() < 0.08}
            if not asm_word and o["stage"] == 3:
                o["stage"] = 4
            orders.append(o)
            if pay == "inst":
                late = R.random() < 0.12
                inst.append({"id": n, "day": d.isoformat(), "shop": team, "seller": eid, "client": o["client"], "sum": amt,
                             "first": round(amt * 0.25, -3), "months": o["months"], "late": late,
                             "late_days": R.randint(4, 25) if late else 0, "debt": round(amt / o["months"] * R.uniform(1, 2.5), -3) if late else 0})
    db.put("rt_orders", {"asm_word": asm_word, "stages": STAGES if asm_word else STAGES[:3], "items": orders})
    db.put("rt_inst", inst)


def _accounting(db, R, today, k=1):
    """📦 Учёт в Алапе: товары с себестоимостью из среза остатков, приходы (история, один ждёт склад, черновик),
    кому владелец открыл закрытые цифры и журнал просмотров (modules/acc.py)."""
    import json
    on = db.one('SELECT MAX("on") AS d FROM stock')["d"]
    if not on:
        return
    price = {x["code"]: x["a"] / x["q"] for x in db.q("SELECT code, SUM(qty) AS q, SUM(amount) AS a FROM item_sales "
                                                    "GROUP BY code HAVING q > 0")}
    grp = {g["code"]: g["name"] for g in db.q("SELECT code, name FROM groups")}
    goods = []
    for x in db.q('SELECT code, name, supplier, qty FROM stock WHERE "on" = ? ORDER BY code', on):
        p = round(price.get(x["code"]) or R.uniform(300, 3000), -1)
        cost = round(p / R.choice([1.25, 1.35, 1.45, 1.5, 1.55, 1.6, 1.7]), 2)
        qty = x["qty"] if k == 1 else max(round(x["qty"] / (k / 2)), 0) if x["qty"] else 0        # дорогой товар — штук меньше
        goods.append((x["code"], x["name"], x["name"].split(" · ")[0], p, cost, qty, x["supplier"]))
    db.c.executemany("INSERT OR REPLACE INTO goods (code, name, grp, price, cost, qty, supplier, ts) VALUES (?,?,?,?,?,?,?,?)",
                     [(*g, on) for g in goods])
    db.c.execute("UPDATE goods SET price_opt = ROUND((price + cost) / 2, -1)")      # оптовая — между закупочной и розничной (20.11)
    es = db.employees()
    pos = lambda p: [e for e in es if e["position"] == p]                   # noqa: E731
    short = lambda e: f"{e['fio'].split()[0]} {e['fio'].split()[1][0]}." if e else ""   # noqa: E731
    buyers, heads, acc = pos("buyer"), pos("buy_head"), pos("accountant")
    by_sup = {}
    for g in goods:
        by_sup.setdefault(g[6], []).append(g)
    sups = sorted(by_sup)
    n = 410
    for k in range(9):
        sup = sups[k % len(sups)]
        b = buyers[k % len(buyers)] if buyers else None
        lines = [[g[0], R.choice([4, 6, 10, 12, 20, 30]), round(g[4] * R.uniform(0.95, 1.06), -1)] for g in R.sample(by_sup[sup], 3)]
        if k < 7:
            st, day = "done", today - timedelta(days=24 - 3 * k)
            got = [[c, q, p, q if R.random() > 0.15 else q - 1] for c, q, p in lines]
            note = "Недостача: 1 шт" if any(x[3] < x[1] for x in got) else ""
        else:
            st, day = ("sent", today) if k == 7 else ("draft", today - timedelta(days=1))
            got, note = [[c, q, p, None] for c, q, p in lines], ""
        n += 1
        db.x("INSERT INTO receipts (id, no, day, supplier, status, pay, author, author_id, lines, note, ts, done_ts, done_by) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", n, f"{R.randint(1000, 4999)}", day.isoformat(), sup, st,
             R.choice(["debt", "debt", "paid"]), short(b), b["id"] if b else None, json.dumps(got), note,
             f"{day.isoformat()}T10:15:00", f"{day.isoformat()}T15:40:00" if st == "done" else None,
             short(pos("storekeeper")[0]) if st == "done" and pos("storekeeper") else "")
    people = [e["id"] for e in (heads[:1] + buyers[:1] + acc[:1])]
    db.put("secret", {"people": people, "support": True, "open": {}})
    y = today - timedelta(days=1)
    log = [(f"{y}T17:40:00", short(acc[0]) if acc else "Бухгалтер", "Бухгалтер", "товары с себестоимостью", "", 0),
           (f"{y}T14:32:00", "Поддержка Алапы", "admin", "открыла закрытые цифры на 30 мин", "настраивали приход по вашей просьбе", 1),
           (f"{today}T11:05:00", short(buyers[0]) if buyers else "Закупки", "Менеджер по закупкам", f"завёл приход №{n - 1} с ценами закупки", "", 0)]
    db.c.executemany("INSERT INTO secret_log (ts, who, role, what, why, support) VALUES (?,?,?,?,?,?)", log)


def _shop_sales(db, R, today, rt):
    """Сегодняшние продажи в магазинах через приложение (modules/sale.py): чтобы у директора был «день магазинов» с маржой.
    В строки продаж не пишем — сегодняшние чеки магазинов в демо уже посчитаны в _retail_month."""
    import json
    goods = db.q("SELECT * FROM goods WHERE qty > 0 ORDER BY code")
    if not goods or not rt["sellers"]:
        return
    for k in range(14):
        eid, key, team, *_r = R.choice(rt["sellers"])
        disc = R.choice([0, 0, 0, 0.03, 0.05]) if k != 5 else 0.42                 # одна — с большой скидкой, ниже себестоимости
        lines = [[g["code"], R.choice([1, 1, 1, 2]), round(g["price"] * (1 - disc), 2), g["cost"]] for g in R.sample(goods, R.choice([1, 1, 2]))]
        total = round(sum(n * p for _, n, p, _c in lines), -1)
        pay = R.choice(["cash", "card", "card", "transfer", "inst"])
        db.x("INSERT INTO shop_sales (day, ts, shop, seller, lines, disc, total, pay, take, buyer) VALUES (?,?,?,?,?,?,?,?,?,?)",
             today.isoformat(), f"{today.isoformat()}T{10 + k // 2:02d}:{R.randint(0, 59):02d}:00", team, eid, json.dumps(lines), disc,
             total, pay, R.choice(["self", "self", "deliv"]), R.choice(BUYERS) if pay == "inst" else "")


def _locations(db, R, today, rt):
    """Учёт по местам (modules/ship.py): остаток склада и салонов, образцы в залах; заказы с доставкой на сегодня у водителей —
    часть уже уехала маршрутным листом (одна точка доставлена), часть ждёт погрузки, один «не влез»; перемещения в салоны."""
    import json
    goods = db.q("SELECT code, qty FROM goods ORDER BY code")
    if not goods or not rt["shops"]:
        return
    rows = []
    for g in goods:
        wh = g["qty"]
        for team, _n in rt["shops"]:
            q = R.choice([0, 0, 0, 1, 1, 2, 3]) if wh > 3 else 0
            smp = 1 if R.random() < 0.25 else 0
            if q or smp:
                rows.append((g["code"], team, q + smp, smp))
                wh -= q
                db.x("UPDATE goods SET qty = qty + ? WHERE code = ?", smp, g["code"])
        rows.append((g["code"], "wh", wh, 0))
    db.c.executemany("INSERT INTO stock_loc (code, loc, qty, smp) VALUES (?,?,?,?)", rows)
    drv = [e for e in db.employees() if e["position"] == "driver"]
    sellers = rt["sellers"]
    if not drv or not sellers:
        return
    od = db.get("rt_orders") or {"items": [], "stages": ["Заказ", "Оплата", "Доставка"]}
    nid = max([o["id"] for o in od.get("items", [])] + [x["id"] for x in (db.get("rt_inst") or [])] + [2000]) + 1
    big = [g for g in db.q("SELECT g.code, g.name, g.price, s.qty FROM goods g JOIN stock_loc s ON s.code = g.code AND s.loc = 'wh' "
                           "WHERE s.qty >= 5 ORDER BY g.code")]
    streets = ["ул. Мира", "пр. Победы", "ул. Садовая", "ул. Лесная", "ул. Школьная", "ул. Кирова", "ул. Нагорная", "пр. Ленина"]
    plan = [(0, "gone"), (0, "gone"), (0, "gone"), (1, "on"), (1, "on"), (1, ""), (1, "no"), (2 % len(drv), ""), (None, "")]
    sheet_ids = []
    key0 = (db.one("SELECT key FROM emp_keys WHERE emp_id = ? ORDER BY since DESC LIMIT 1", drv[0]["id"]) or {}).get("key", "")
    sid = db.x("INSERT INTO route_sheets (day, driver, ts_out, orders, by) VALUES (?,?,?,?,?)", today.isoformat(), drv[0]["id"],
               f"{today.isoformat()}T09:42:00", "[]", "Склад")
    for k, (di, load) in enumerate(plan):
        g = R.choice(big)
        n = R.choice([1, 1, 2])
        eid, key, team, *_r = R.choice(sellers)
        o = {"id": nid, "day": (today - timedelta(days=R.randint(1, 4))).isoformat(), "shop": team, "seller": eid,
             "client": R.choice(BUYERS), "item": g["name"], "sum": round(g["price"] * n, -1), "pay": R.choice(["full", "inst", "prepay"]),
             "prepay": 0, "months": 0, "deliv": today.isoformat(), "stage": 0, "paid": True, "late": False, "asm": None,
             "complaint": False, "addr": f"{R.choice(streets)} {R.randint(1, 60)}" + (f", кв. {R.randint(1, 90)}" if R.random() < 0.6 else ""),
             "need_asm": R.random() < 0.4, "lines": [[g["code"], n, "wh"]], "driver": drv[di]["id"] if di is not None else None, "load": load}
        if o["pay"] == "prepay":
            o["prepay"] = round(o["sum"] * 0.5, -3)
        if o["pay"] == "inst":
            o["months"] = 10
        if load == "no":
            o["no_why"] = "не влез"
        if load == "gone":
            db.x("UPDATE stock_loc SET qty = qty - ? WHERE code = ? AND loc = 'wh'", n, g["code"])
            db.x("UPDATE goods SET qty = qty - ? WHERE code = ?", n, g["code"])
            o.update(stage=2, sheet=sid, loaded_ts="09:38")
            if not sheet_ids:
                o.update(dlv="ok", dlv_ts="11:05", stage=4 if not o["need_asm"] else 3)
            sheet_ids.append(nid)
            db.x("INSERT INTO trip_stops (batch, day, emp_key, trip, client, doc) VALUES (0,?,?,?,?,?)", today.isoformat(), key0,
                 f"Л-{sid}", f"Z{nid}", f"№{nid}")
        od.setdefault("items", []).append(o)
        nid += 1
    db.x("UPDATE route_sheets SET orders = ? WHERE id = ?", json.dumps(sheet_ids), sid)
    db.x("INSERT INTO trips (batch, day, emp_key, trip, km, kg, m3) VALUES (0,?,?,?,0,0,0)", today.isoformat(), key0, f"Л-{sid}")
    db.put("rt_orders", od)
    for team, status in ((rt["shops"][1][0], "ask"), (rt["shops"][0][0], "go")):
        g = R.choice(big)
        db.x("INSERT INTO moves (day, loc, lines, status, author, ts, go_ts) VALUES (?,?,?,?,?,?,?)", (today - timedelta(days=1)).isoformat(),
             team, json.dumps([[g["code"], 2, 0, None]]), status, "Директор магазина", f"{today - timedelta(days=1)}T16:00:00",
             f"{today.isoformat()}T09:10:00" if status == "go" else None)
        if status == "go":
            db.x("UPDATE stock_loc SET qty = qty - 2 WHERE code = ? AND loc = 'wh'", g["code"])


def _trips(db, R, cal, end, sales, drivers):
    """Рейсы: клиентов с продажами за день развозят водители, по 8–14 точек в рейс; изредка — двойной заезд."""
    if not drivers:
        return
    trips, stops = [], []
    by_day = {}
    for s in sales:
        if s.amount > 0 and s.day <= end:
            by_day.setdefault(s.day, {}).setdefault(s.client, 0)
            by_day[s.day][s.client] += s.amount
    n = 1000 + cal.start.month * 1000
    for d, cl in sorted(by_day.items()):
        codes = list(cl)
        R.shuffle(codes)
        chunks = [codes[i:i + 11] for i in range(0, len(codes), 11)]
        day_trips = []
        for i, chunk in enumerate(chunks):
            key = drivers[i % len(drivers)]
            n += 1
            kg = sum(cl[c] for c in chunk) / R.uniform(60, 110)
            trips.append(Trip(d, key, str(n), round(35 + len(chunk) * R.uniform(6, 14)), round(kg), round(kg / R.uniform(250, 400), 2)))
            day_trips.append((key, str(n)))
            for c in chunk:
                stops.append(TripStop(d, key, str(n), c, f"Н-{n}{c[-3:]}"))
        if len(day_trips) > 1 and R.random() < 0.35:          # двойной заезд: клиент первого рейса — ещё и в последнем
            c = R.choice(chunks[0])
            key, t = day_trips[-1]
            stops.append(TripStop(d, key, t, c, f"Н-{t}{c[-3:]}д"))
    db.load(Dataset("trips", cal.start, cal.end, rows=trips, source="seed"))
    db.load(Dataset("trip_stops", cal.start, cal.end, rows=stops, source="seed"))


def _returns(db, R, cal, end, rets, staff_keys):
    """Возвраты с комментарием, как пишут в 1С, и уже разобранной причиной (у настоящего клиента её ставит переводчик)."""
    rows = []
    store, drv = staff_keys.get("storekeeper", []), staff_keys.get("driver", [])
    for i, (d, key, client, amount) in enumerate(rets):
        if d > end:
            continue
        f = R.choices(["rep", "warehouse", "driver", "client", "other", ""], weights=[30, 20, 15, 20, 10, 5])[0]
        who = ""
        if f == "warehouse" and store and R.random() < 0.8:
            who = R.choice(store)
        if f == "driver" and drv and R.random() < 0.8:
            who = R.choice(drv)
        name = who.split()[0].capitalize() if who else ""
        reason = {"rep": R.choice(["Ошибка торгового: заказал не тот товар", "Ошибка торгового — не согласовал цену с клиентом"]),
                  "warehouse": f"Ошибка склада{' ' + name if name else ''}: пересорт",
                  "driver": f"Ошибка водителя{' ' + name if name else ''}: бой при доставке",
                  "client": R.choice(["Клиент отказался: нет денег", "Клиент передумал"]),
                  "other": R.choice(["Брак производителя", "Истёк срок годности"]), "": "возврат по звонку"}[f]
        rows.append(Return(d, key, client, f"ВЗ-{cal.start.month:02d}{i:03d}", amount, reason, f, who))
    db.load(Dataset("returns", cal.start, cal.end, rows=rows, source="seed"))


PRICE_K = {"furniture": 50, "electronics": 8}       # цены товаров отрасли: мебель — кухни от 150 000, а не 540 ₽ (20.11)


def _stock(db, R, groups, months_list, today, k=1):
    """Склад: по 10 товаров в группе, продажи по неделям за 4 месяца, остаток на вчера; заказы в пути и пришедшие."""
    import json
    items = []
    code = 100000
    for g, gname, _ in groups:
        for var in VARIANTS:
            code += 1
            speed = R.lognormvariate(1.2, 0.9)          # штук в день
            kind = R.choices(["ok", "low", "out", "stale"], weights=[76, 11, 6, 7])[0]
            items.append((str(code), f"{gname} · {var}", R.choice(SUPPLIERS), speed, kind, round(R.uniform(150, 3000), -1) * k))
    y = today - timedelta(days=1)
    for month in months_list:
        start, stop = first_day(month), min(last_day(month), y)
        if stop < start:
            continue
        rows = []
        d = start
        while d <= stop:
            for c, _, _, speed, kind, price in items:
                if kind == "stale" and (y - d).days < 100:
                    continue
                q = max(0, round(speed * 7 * R.uniform(0.6, 1.4)))
                if q:
                    rows.append(ItemSale(d, c, q, q * price))
            d += timedelta(days=7)
        db.load(Dataset("item_sales", first_day(month), last_day(month), rows=rows, source="seed"))
    stock = []
    for c, name, sup, speed, kind, _ in items:
        qty = {"ok": speed * R.uniform(20, 80), "low": speed * R.uniform(2, 12), "out": 0, "stale": R.uniform(20, 200)}[kind]
        stock.append(Stock(y, c, name, sup, round(qty)))
    db.load(Dataset("stock", y, y, rows=stock, source="seed"))
    low = [c for c, *_, kind, _ in items if kind in ("low", "out")]
    sup = next((s for _, _, s, _, kind, _ in items if kind in ("low", "out")), SUPPLIERS[0])
    mine = [[c, 50] for c, _, s, _, kind, _ in items if s == sup and c in low][:3]
    if mine:
        db.x("INSERT INTO stock_orders (supplier, items, status, author, ts, sent_ts) VALUES (?,?,?,?,?,?)", sup, json.dumps(mine),
             "sent", "Начальник закупок", (today - timedelta(days=3)).isoformat(), (today - timedelta(days=3)).isoformat())
    db.x("INSERT INTO stock_orders (supplier, items, status, author, ts, sent_ts, came_ts) VALUES (?,?,?,?,?,?,?)", SUPPLIERS[1],
         json.dumps([[items[1][0], 120]]), "came", "Начальник закупок", (today - timedelta(days=20)).isoformat(),
         (today - timedelta(days=20)).isoformat(), (today - timedelta(days=12)).isoformat())


# Люди, которых добавили позже (30.10): со своим генератором случайностей, чтобы прежние цифры демо не сдвинулись.
EXTRA_STAFF = [("dispatcher", "logistics"), ("hr", "office")]


def _company(db, cfg, R, today, months_list, newbie, start_new):
    """Вся фирма, а не только продажи: диспетчер и кадры, менеджеры закупок за поставщиками, заказы на всех этапах,
    отметки водителей, ГСМ, кадровые процессы, функции на заказ."""
    import json
    have = {" ".join(e["fio"].split()[:2]).lower() for e in db.employees(active=None)}
    names = _names(R)

    def fresh():
        for fio, key in names:
            if key.lower() not in have:
                have.add(key.lower())
                return fio, key
    for pos, dept in EXTRA_STAFF:
        if cfg.position(pos) and not any(e["position"] == pos for e in db.employees()):
            fio, key = fresh()
            db.add_employee(fio, dept, position=pos, role=cfg.position(pos)["role"], keys=[key],
                            hired=today - timedelta(days=R.randint(300, 1500)))
    es = db.employees()
    by_pos = {}
    for e in es:
        by_pos.setdefault(e["position"], []).append(e)
    buyers, heads = by_pos.get("buyer", []), by_pos.get("buy_head", [])

    # ---------- закупки: кто за каких поставщиков, заказы на всех этапах ----------
    on = db.one('SELECT MAX("on") AS d FROM stock')["d"]
    items = db.q('SELECT code, name, supplier, qty FROM stock WHERE "on" = ?', on) if on else []
    sups = sorted({x["supplier"] for x in items})
    if buyers:
        db.put("stock_owners", {s: buyers[i % len(buyers)]["id"] for i, s in enumerate(sups)})
    owner_of = {s: buyers[i % len(buyers)] for i, s in enumerate(sups)} if buyers else {}
    price = {x["code"]: x["a"] / x["q"] for x in db.q("SELECT code, SUM(qty) AS q, SUM(amount) AS a FROM item_sales "
                                                    "WHERE day > ? GROUP BY code HAVING q > 0",
                                                    (today - timedelta(days=50)).isoformat())}
    limit = float((cfg.raw.get("stock") or {}).get("approve_limit") or 300000)
    for o in db.q("SELECT id, supplier FROM stock_orders"):
        b = owner_of.get(o["supplier"]) or (heads[0] if heads else None)
        if b:
            db.x("UPDATE stock_orders SET emp_id = ?, author = ? WHERE id = ?", b["id"], b["fio"], o["id"])
    by_sup = {}
    for x in items:
        if x["code"] in price:
            by_sup.setdefault(x["supplier"], []).append(x)
    stamp = lambda d, t: f"{d.strftime('%d.%m')} {t}"                          # noqa: E731
    plan = [("draft", 3, 0, None), ("wait", 4, 0, None), ("wait", 6, 0, "big"), ("back", 3, 0, "back"), ("ok", 4, 0, None)]
    used = {o["supplier"] for o in db.q("SELECT supplier FROM stock_orders WHERE status IN ('sent', 'draft', 'back')")}
    free = [s for s in sups if s not in used and by_sup.get(s)]
    for (st, n, q, mark), sup in zip(plan, free):
        b = owner_of.get(sup)
        goods = sorted(by_sup[sup], key=lambda x: x["qty"])[:n]
        # обычный заказ — на 15–35% порога (согласует начальник закупок), крупный — на 130% (согласует директор)
        share = 1.3 if mark == "big" else R.uniform(0.15, 0.35)
        per = limit * share / sum(price[x["code"]] for x in goods)
        lines_ = [[x["code"], max(round(per * (1 + 0.2 * (i % 2))), 1)] for i, x in enumerate(goods)]
        d = today - timedelta(days=R.randint(0, 2))
        hist = [[stamp(d, "10:15"), b["fio"] if b else "", "создан"]]
        extra = {}
        if st in ("wait", "back", "ok"):
            hist.append([stamp(d, "11:40"), b["fio"] if b else "", "на согласование"])
        if st == "back":
            extra["note"] = "Позицию с наименьшим остатком возьмите вдвое больше — скоро сезон"
            hist.append([stamp(d, "15:05"), heads[0]["fio"] if heads else "Директор", "возвращён: " + extra["note"]])
        if st == "ok":
            hist.append([stamp(d, "14:20"), heads[0]["fio"] if heads else "Директор", "согласован"])
        extra["hist"] = hist
        db.x("INSERT INTO stock_orders (supplier, items, status, author, ts, emp_id, extra) VALUES (?,?,?,?,?,?,?)",
             sup, json.dumps(lines_), st, b["fio"] if b else "", d.isoformat(), b["id"] if b else None,
             json.dumps(extra, ensure_ascii=False))

    # ---------- дальние полупустые рейсы: пара в месяц (далеко, 2 точки, мало груза) ----------
    for m in months_list:
        ts_ = db.q("SELECT t.rowid AS r, t.day, t.trip, COUNT(s.client) AS n FROM trips t JOIN trip_stops s "
                   "ON s.day = t.day AND s.trip = t.trip WHERE t.day LIKE ? GROUP BY t.rowid ORDER BY n, t.day LIMIT 2", f"{m}%")
        for t in ts_:
            keep = [x["rowid"] for x in db.q("SELECT rowid FROM trip_stops WHERE day = ? AND trip = ? LIMIT 2", t["day"], t["trip"])]
            db.x(f"DELETE FROM trip_stops WHERE day = ? AND trip = ? AND rowid NOT IN ({','.join('?' * len(keep))})",
                 t["day"], t["trip"], *keep)
            db.x("UPDATE trips SET km = ?, kg = ?, m3 = ? WHERE rowid = ?", R.randint(170, 280), R.randint(150, 600),
                 round(R.uniform(0.6, 2.5), 1), t["r"])

    # ---------- водители: отметки «Мой день» и ГСМ ----------
    drivers = [e for e in es if (cfg.position(e["position"]) or {}).get("duty") == "driver"]
    keys = {r["key"]: r["emp_id"] for r in db.q("SELECT key, emp_id FROM emp_keys")}
    days = {}
    km = {}
    for r in db.q("SELECT day, emp_key, km FROM trips"):
        e = keys.get(r["emp_key"])
        if e and r["day"] < today.isoformat():
            days.setdefault((r["day"][:7], e), set()).add(r["day"])
            km[(r["day"][:7], e)] = km.get((r["day"][:7], e), 0) + r["km"]
    norm0 = float(str((cfg.raw.get("fuel") or {}).get("norm", 12)).replace(",", "."))
    lit = float((cfg.raw.get("fuel") or {}).get("price", 60))
    norms = {e["id"]: round(norm0 + R.choice([-1.5, 0, 0, 1, 2.5]), 1) for e in drivers}
    db.put("fuel_norms", {str(k): v for k, v in norms.items()})
    for m in months_list:
        marks, cards = {}, {}
        for i, e in enumerate(drivers):
            mine = {}
            for d in sorted(days.get((m, e["id"]), ())):
                x = {"out": f"0{R.randint(6, 7)}:{R.choice(['05', '20', '35', '50'])}",
                     "back": f"{R.choice([17, 18, 18, 19, 19, 20])}:{R.choice(['00', '15', '30', '45'])}"}
                if R.random() < 0.04:
                    x = {"out": x["out"], "night": 1}
                if R.random() < 0.3:
                    x["cash"] = R.randint(1, 4)
                if R.random() < 0.07:
                    x["pick"] = [[round(R.uniform(0.5, 5), 1), R.randint(80, 900), R.choice(["трубы", "фитинги", "коробки", ""])]]
                mine[d] = x
            if mine:
                marks[str(e["id"])] = mine
            if km.get((m, e["id"])):
                due = km[(m, e["id"])] * norms[e["id"]] / 100 * lit
                cards[str(e["id"])] = round(due * (1.14 if i == 0 else R.uniform(0.93, 1.04)), -1)
        if m == month_of(today):                    # сегодня утром уже выехали трое
            for e in drivers[:3]:
                marks.setdefault(str(e["id"]), {})[today.isoformat()] = {"out": f"07:{R.choice(['10', '25', '40', '55'])}"}
        db.put(f"dm:{m}", marks)
        db.put(f"fuel_card:{m}", cards)

    # ---------- кадры ----------
    hrs = by_pos.get("hr", [])
    hr_name = " ".join(hrs[0]["fio"].split()[:2]) if hrs else "Кадры"
    owner = next((e for e in es if e["role"] == "owner"), None)
    boss = " ".join(owner["fio"].split()[:2]) if owner else "Директор"
    ts = lambda d: d.strftime("%d.%m 10:00")                                  # noqa: E731
    from modules.hr import ITEMS as HR_ITEMS
    nb = db.employee(newbie)
    head_of = {e["dept"]: " ".join(e["fio"].split()[:2]) for e in es if e["role"] == "head"}
    signer = lambda w, dept: boss if w == "owner" else head_of.get(dept, boss) if w == "head" else hr_name  # noqa: E731
    procs = [{"id": 1, "kind": "hire", "fio": nb["fio"], "emp": newbie, "pos": nb["position"], "dept": nb["dept"],
              "team": nb["team"], "from_dept": None, "day": start_new.isoformat(), "salary": None, "status": "done",
              "items": [[t, w, signer(w, nb["dept"]), ts(start_new - timedelta(days=2))]
                        for t, w in HR_ITEMS["hire"]], "author": hr_name, "author_id": hrs[0]["id"] if hrs else None,
              "created": (start_new - timedelta(days=4)).strftime("%d.%m.%Y"), "order": "1-п"}]
    nxt = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    fio, _ = fresh()
    drv_pos = next((p for p in cfg.positions if p.get("duty") == "driver"), None)
    if drv_pos:
        it = [[t, w, "", ""] for t, w in HR_ITEMS["hire"]]
        it[0][2:], it[1][2:] = [boss, ts(today - timedelta(days=1))], [hr_name, ts(today)]
        procs.append({"id": 2, "kind": "hire", "fio": fio, "emp": None, "pos": drv_pos["id"], "dept": drv_pos["dept"], "team": "",
                      "from_dept": None, "day": nxt.isoformat(), "salary": 35000, "status": "open", "items": it,
                      "author": hr_name, "author_id": hrs[0]["id"] if hrs else None,
                      "created": (today - timedelta(days=1)).strftime("%d.%m.%Y")})
    store = by_pos.get("storekeeper", [])
    if store:
        x = store[-1]
        it = [[t, w, "", ""] for t, w in HR_ITEMS["fire"]]
        it[0][2:], it[1][2:] = [boss, ts(today - timedelta(days=3))], [hr_name, ts(today - timedelta(days=2))]
        procs.append({"id": 3, "kind": "fire", "fio": x["fio"], "emp": x["id"], "pos": x["position"], "dept": x["dept"],
                      "team": "", "from_dept": x["dept"], "day": last_day(month_of(today)).isoformat(), "salary": None,
                      "status": "open", "items": it, "author": hr_name, "author_id": hrs[0]["id"] if hrs else None,
                      "created": (today - timedelta(days=3)).strftime("%d.%m.%Y")})
    db.put("hr_procs", procs)
    db.put("hr_seq", {str(start_new.year): 1})

    # ---------- функции на заказ ----------
    prev = shift_month(month_of(today), -1)
    db.put("custom_req", [
        {"id": 1, "month": prev, "text": "Утром начальнику склада — список вчерашних заявок, которые не отгрузили",
         "author": owner["fio"] if owner else "Директор", "status": "done", "ts": f"12.{prev[5:]} 10:40",
         "note": "Готово: «📦 Не отгружено вчера» в 08:30"},
        {"id": 2, "month": month_of(today), "text": "Бонус торговому за новые точки: 500 ₽ за клиента, который купил впервые",
         "author": owner["fio"] if owner else "Директор", "status": "work", "ts": today.strftime("%d.%m 09:15"), "note": ""}])


def _task(db, author, emps, text, due, status, today, kind="once", rule=None, prio="", answer="", move=None, self_=False):
    """📌 Задание демо в таблицах модуля «Задания» (modules/tasks.py)."""
    import json
    names = {e["id"]: e["fio"] for e in db.employees(active=None)}
    ppl = [[e, " ".join(names.get(e, "").split()[:2])] for e in emps]
    an = " ".join((author or {}).get("fio", "Директор").split()[:2])
    ts = f"{today - timedelta(days=3)} 10:00:00"
    tid = db.x("INSERT INTO task_defs (text, author, author_name, kind, rule, prio, days, people, status, self, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               text, author["id"], an, kind, json.dumps(rule or {}, ensure_ascii=False), prio, 1, json.dumps(ppl, ensure_ascii=False),
               "active", 1 if self_ else 0, ts)
    for e, n in ppl:
        log = [[ts[:16], an, "выдано"]]
        ans_ts = f"{today} 09:40:00" if status in ("ok", "done", "fail") else None
        if status in ("ok", "done", "fail"):
            log.append([ans_ts[:16], n, {"fail": "не выполнено", "ok": "выполнено", "done": "выполнено"}[status] + (f": {answer}" if answer else "")])
        db.x("INSERT INTO task_items (task_id, who, name, deadline, status, answer, ans_ts, done_ts, move, log, ts, iday) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
             tid, e, n, due.isoformat(), status, answer, ans_ts, ans_ts if status == "ok" else None,
             json.dumps(move, ensure_ascii=False) if move else None, json.dumps(log, ensure_ascii=False), ts, (today - timedelta(days=3)).isoformat())
    return tid


def _tasks_more(db, R, today, owner, rop, reps):
    """Демо: доска заданий живая — повторы, ответ ждёт 👍, «не выполнено», просьба о переносе, заметки директора и склада."""
    es = db.employees()
    wh = next((e for e in es if e["role"] == "head" and e["dept"] != rop["dept"]), None)
    staff = [e for e in es if e["role"] == "staff" and wh and e["dept"] == wh["dept"]][:2]
    rid = [r[0] for r in reps]
    if len(rid) >= 3:
        _task(db, rop, rid[:3], "Отчёт за день до 19:00 — продажи, сбор, км", today, "work", today, kind="rep", rule={"type": "daily"})
        _task(db, rop, [rid[1]], "Сверка с ключевым клиентом за прошлый месяц", today - timedelta(days=1), "done", today,
              prio="hi", answer="Сверил, расхождений нет, акт подписан")
        _task(db, rop, [rid[2]], "Выставить фокусную группу у трёх клиентов", today, "fail", today, prio="mid",
              answer="Двое на ремонте, будут на следующей неделе")
        _task(db, owner, [rid[0]], "Закрыть долг 60+ у проблемного клиента", today + timedelta(days=1), "work", today, prio="hi",
              move={"to": (today + timedelta(days=4)).isoformat(), "why": "Клиент платит в пятницу", "state": "ask"})
    if wh:
        _task(db, owner, [wh["id"]], "Инвентаризация склада — итоги директору", today + timedelta(days=2), "work", today, prio="mid")
        if staff:
            _task(db, wh, [e["id"] for e in staff], "Порядок на складе и чистая зона отгрузки", today, "work", today,
                  kind="rep", rule={"type": "weekly", "days": [0, 3]})
    for k, txt in enumerate(["Позвонить в банк по кредитной линии", "Согласовать цены с новым поставщиком", "Планёрка с отделом продаж"]):
        _task(db, owner, [owner["id"]], txt, today + timedelta(days=k), "ok" if k == 0 else "work", today, self_=True,
              prio=["", "mid", ""][k])


def _route_marks(db, R, cfg, today):
    """Отметки маршрута: вчера почти все отчитались, сегодня лучший торговый уже отметил пару точек."""
    from core.calendar import WEEKDAYS
    y = today - timedelta(days=1)
    while WEEKDAYS[y.weekday()] in cfg.weekend:
        y -= timedelta(days=1)
    reasons = ["Закрыто", "Нет денег", "Не успел", "Нет на месте"]
    reps = [e for e in db.employees() if e["role"] in ("rep", "newbie") and e["dept"] != "shops"]
    got, _ = db.sales_by_emp(month_of(today))
    plans = db.plans(month_of(today))
    top = max(reps, key=lambda e: got.get(e["id"], 0) / (plans.get(e["id"], {}).get("sales") or 1e12))["id"]
    for n, e in enumerate(reps):
        for day in (y, today):
            rows = [r for r in db.q("SELECT emp_key, client FROM visits WHERE day = ?", day.isoformat())
                    if db.emp_of_key(r["emp_key"], day) == e["id"]]
            if day == today and e["id"] != top:
                continue
            if day == today:
                rows = rows[:2]
            for i, r in enumerate(rows):
                s = db.one("SELECT SUM(amount) AS s FROM sales WHERE day = ? AND client = ? AND amount > 0", day.isoformat(),
                           r["client"])["s"]
                if s:
                    st, why, amt = "sold", "", round(s * R.uniform(0.9, 1.15), -2)
                elif R.random() < 0.45:
                    st, why, amt = "visited", "", 0
                else:
                    st, why, amt = "missed", R.choice(reasons), 0
                if day == today:
                    st, why, amt = ("sold", "", round(R.uniform(8, 40)) * 1000) if i == 0 else ("visited", "", 0)
                db.x("INSERT OR REPLACE INTO visit_marks (day, emp_id, client, status, reason, amount, ts) VALUES (?,?,?,?,?,?,?)",
                     day.isoformat(), e["id"], r["client"], st, why, amt, f"{day.isoformat()}T18:30:00")
            if day == y and rows and n % 6 != 5:
                db.x("INSERT OR IGNORE INTO visit_reports (day, emp_id, ts) VALUES (?,?,?)", day.isoformat(), e["id"],
                     f"{day.isoformat()}T19:10:00")


def _quiz(db, R, today):
    """Результаты тестов: за 3 прошлые недели у всех, за эту — у половины; новичок прошёл первый урок."""
    import json
    people = [e for e in db.employees() if e["role"] in ("rep", "sv") and e["dept"] != "shops"]
    for back in range(4):
        d = today - timedelta(days=7 * back)
        y, w, _ = d.isocalendar()
        ref = f"{y}-W{w:02d}"
        for n, e in enumerate(people):
            if back == 0 and n % 2:
                continue
            score = min(10, max(4, round(R.gauss(8.2, 1.3))))
            wrong = [{"q": "Опоздание на 35 минут — какой штраф по табелю?", "a": "200 ₽"}] if score < 10 else []
            db.x("INSERT INTO quiz_results (emp_id, kind, ref, score, total, wrong, ts) VALUES (?,?,?,?,?,?,?)",
                 e["id"], "test", ref, score, 10, json.dumps(wrong, ensure_ascii=False), d.isoformat())
    for e in db.employees():
        if e["role"] == "newbie":
            db.x("INSERT INTO quiz_results (emp_id, kind, ref, score, total, wrong, ts) VALUES (?,?,?,?,?,?,?)",
                 e["id"], "lesson", "0", 3, 3, "[]", today.isoformat())


def _names(R):
    used = set()
    while True:
        female = False                      # Адам 29.10: в демо только мужчины
        sur = R.choice(V.SURNAMES_M)
        if female:
            sur = sur + "а" if not sur.endswith("ий") else sur[:-2] + "ая"
            first, patr = R.choice(V.NAMES_F), R.choice(V.PATRON_F)
        else:
            first, patr = R.choice(V.NAMES_M), R.choice(V.PATRON_M)
        key = f"{sur} {first}"
        if key in used:
            continue
        used.add(key)
        yield f"{sur} {first} {patr}", key
