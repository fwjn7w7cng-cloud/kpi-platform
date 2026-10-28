"""Наполнение базы демо выдуманной фирмой: люди, клиенты, 3 месяца продаж, долги, деньги, маршруты,
планы, табель, правки, задачи, отпуска, документы.

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
from core.data.model import Client, Dataset, Debt, Group, Payment, Sale, Visit

# команда → (сколько торговых, характеры: множитель темпа к плану)
TEAMS = {"north": [1.22, 1.06, 0.98, 0.93, 0.74], "south": [1.12, 1.01, 0.95, 0.88, 0.79], "key": [1.04, 0.97]}
STAFF = [("rop", "sales", "", 1), ("director", "office", "", 1), ("accountant", "office", "", 1),
         ("manager", "office", "", 1), ("wh_head", "warehouse", "", 1), ("storekeeper", "warehouse", "", 3),
         ("log_head", "logistics", "", 1), ("driver", "logistics", "", 4)]


def seed(db, cfg, industry=None, today=None, rnd_seed=7, months=3):
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
    for pos, dept, team, n in STAFF:
        role = cfg.position(pos)["role"]
        for _ in range(n):
            fio, _ = next(names)
            db.add_employee(fio, dept, team=team, position=pos, role=role, hired=m0 - timedelta(days=R.randint(200, 2000)))
    # новичок: пришёл в текущем месяце на место ушедшего — ключ того же торговика с даты приёма
    leaver = next(r for r in reps if r[2] == "south" and r[3] < 0.85)
    start_new = first_day(months_list[-1]) + timedelta(days=min(9, (today - first_day(months_list[-1])).days // 2))
    fio, _ = next(names)
    newbie = db.add_employee(fio, "sales", team="south", position="rep", role="newbie", hired=start_new)
    db.c.execute("UPDATE employees SET fired = ?, active = 0 WHERE id = ?", ((start_new - timedelta(days=1)).isoformat(), leaver[0]))
    db.bind_key(leaver[1], newbie, since=start_new)

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
    db.load(Dataset("clients", m0, last_day(months_list[-1]), rows=cli_rows, source="seed"))

    skill_of = {key: skill for _, key, _, skill in reps}
    weights = [w for _, _, w in groups]
    focus = [g[0] for g in groups[:(cfg.raw.get("kpi") or {}).get("focus_groups", 2)]]

    # ---------- продажи, деньги, маршруты, долги по месяцам ----------
    for mi, month in enumerate(months_list):
        cal = Month(cfg, month, today)
        current = mi == len(months_list) - 1
        workdays = cal.workdays
        sales, visits, pays = [], [], []
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
                for g in picked:
                    amt = round(order / len(picked) * R.uniform(0.7, 1.3), -1)
                    if g in focus:
                        amt = round(amt * (0.75 + 0.35 * skill), -1)
                    sales.append(Sale(d, key, ccode, g, amt, qty=max(1, round(amt / R.uniform(300, 1500))),
                                      discount=round(R.uniform(0, 0.08 if skill > 0.9 else 0.12), 3)))
                if R.random() < 0.08:
                    sales.append(Sale(d, key, ccode, R.choice(picked and list(picked)), -round(order * 0.15, -1)))
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
        if end >= cal.start:
            db.load(Dataset("sales", cal.start, cal.end, rows=[s for s in sales if s.day <= end], source="seed"))
            db.load(Dataset("payments", cal.start, cal.end, rows=[p for p in pays if p.day <= end], source="seed"))
        db.load(Dataset("visits", cal.start, cal.end, rows=visits, source="seed"))
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

    # ---------- табель, правки, статусы месяцев ----------
    cur = months_list[-1]
    cal = Month(cfg, cur, today)
    passed = cal.passed_days()
    everyone = db.employees()
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
        db.x("INSERT INTO tasks (author_id, emp_id, text, due, status, ts) VALUES (?,?,?,?,?,?)",
             rop["id"] if i % 2 else owner["id"], eid, task_texts[i % len(task_texts)], due.isoformat(), status, today.isoformat())
    db.put("seeded", {"industry": industry, "seeded": today.isoformat(), "months": months_list})
    return {"reps": len(reps), "clients": len(clients), "months": months_list}


def _names(R):
    used = set()
    while True:
        female = R.random() < 0.3
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
