"""👥 Завести людей по ролям — из пульта (Адам 20.11, эскиз HP.png).

Клиенту без 1С людей заводят заранее, без имён: «Продавец 1 · Барт», «Складской 2 · Склад 3». Потом в его приложении им
вписывают настоящие имена и раздают ссылки для входа. Форма одной страницей (`form_fields`), сохранение — `apply`:
- магазины: каждая должность отдела магазинов + водитель (логистика, команда магазина) + складской (склад, команда магазина,
  его склады `ship.shop_whs`);
- общие склады (`ship.owners` = ""): складские по каждому складу и завсклад на все общие;
- прочие отделы (опт, закупки, логистика, офис): по каждой должности;
- руководители: до 6 строк — «Руководитель магазинов» или «Владелец» + какие магазины видит (`retail.SEES_KEY`).
Повторное сохранение заводит только недостающих: у заведённого в `extra.auto` метка места и должности, `extra.n` — номер.
Лишних не увольняет (это делают в приложении клиента).
"""
from core.ui.retail import SEES_KEY, shop_dept, shops

LEADERS = 6


def _pos(cfg, dept, role=None, duty=None):
    return [p for p in cfg.positions if p["dept"] == dept and (role is None or p["role"] == role) and (duty is None or p.get("duty") == duty)]


def plan(ctx):
    """Что можно завести: [(раздел, [(ключ поля, подпись, должность, отдел, команда, склады)])]."""
    from modules.ship import owners, shop_whs, whs
    cfg = ctx.cfg
    sd = shop_dept(cfg)
    kinds = {d.id: d.kind for d in cfg.departments}
    wd = next((d for d, k in kinds.items() if k == "warehouse"), None)
    ld = next((d for d, k in kinds.items() if k == "logistics"), None)
    teams = {t.id for t in cfg.teams}
    keeper = next((p for p in _pos(cfg, wd, "staff")), None) if wd else None
    whead = next((p for p in _pos(cfg, wd, "head")), None) if wd else None
    driver = (next((p for p in _pos(cfg, ld, duty="driver")), None) or next((p for p in _pos(cfg, ld, "staff")), None)) if ld else None
    out = []
    for sid, sname in shops(cfg):
        order = {"seller": 0, None: 1, "shop": 2}                      # продавцы, администраторы, директор магазина
        ps = sorted((p for p in cfg.positions if p["dept"] == sd and p["role"] != "head"), key=lambda p: order.get(p.get("duty"), 1))
        rows = [(f"s|{sid}|{p['id']}", p["name"], p["id"], sd, sid, []) for p in ps]
        if driver:
            rows.append((f"s|{sid}|{driver['id']}", driver["name"], driver["id"], ld, f"{ld}_{sid}" if f"{ld}_{sid}" in teams else "", []))
        if keeper:
            rows.append((f"s|{sid}|{keeper['id']}", f"{keeper['name']} (склад магазина)", keeper["id"], wd,
                         f"{wd}_{sid}" if f"{wd}_{sid}" in teams else "", shop_whs(ctx, sid)))
        out.append((f"🏬 {sname}", rows))
    ow = owners(ctx) if (cfg.warehouses or ctx.db.get("warehouses")) else {}
    common = [(w, n) for w, n in whs(ctx) if not ow.get(w)] if ow else []
    if wd and keeper and common:
        rows = [(f"w|{w}|{keeper['id']}", f"{keeper['name']} · {n}", keeper["id"], wd, "", [w]) for w, n in common]
        if whead:
            rows.append((f"wa|{whead['id']}", f"{whead['name']} · все общие склады", whead["id"], wd, "", [w for w, _ in common]))
        out.append(("📦 Общие склады", rows))
    done = {sd, None}
    if common:
        done.add(wd)
    for d in cfg.departments:
        if d.id in done:
            continue
        own = [t.id for t in cfg.teams_of(d.id) if not any(t.id == f"{d.id}_{s}" for s, _ in shops(cfg))]
        rows = [(f"d|{d.id}|{p['id']}", p["name"], p["id"], d.id, own[0] if own else "", [])
                for p in cfg.positions if p["dept"] == d.id and p["role"] not in ("owner",)
                and not (d.id == wd and common and p["id"] in {(keeper or {}).get("id"), (whead or {}).get("id")})]
        if rows:
            out.append((d.name, rows))
    return out


def lead_positions(cfg):
    sd = shop_dept(cfg)
    head = next((p for p in cfg.positions if p["dept"] == sd and p["role"] == "head"), None) if sd else None
    owner = next((p for p in cfg.positions if p["role"] == "owner"), None)
    return head, owner


def _have(db):
    out = {}
    for e in db.employees(active=None):
        a = (e.get("extra") or {}).get("auto")
        if a and e["active"]:
            out[a] = out.get(a, 0) + 1
    return out


def form_fields(ctx, form=None):
    have = _have(ctx.db)
    form = form or {}
    fields = []
    for title, rows in plan(ctx):
        fields.append({"k": f"h{len(fields)}", "t": "head", "l": title})
        for key, label, *_ in rows:
            fields.append({"k": key, "t": "num", "sh": 1, "l": label + (f" · уже {have[key]}" if have.get(key) else ""),
                           "v": str(form.get(key, have.get(key, 0)))})
    head, owner = lead_positions(ctx.cfg)
    if shops(ctx.cfg) and (head or owner):
        fields.append({"k": "hL", "t": "head", "l": "👔 Руководители и владельцы — кто какие магазины видит"})
        roles = [["", "—"]] + ([["head", head["name"]]] if head else []) + ([["owner", "Владелец (бесплатно)"]] if owner else [])
        for i in range(LEADERS):
            fields.append({"k": f"L{i}r", "t": "seg", "l": f"{i + 1}.", "v": str(form.get(f"L{i}r", "")), "opts": roles})
            fields.append({"k": f"L{i}s", "t": "multi", "l": "видит магазины", "v": str(form.get(f"L{i}s", "")),
                           "opts": [[s, n] for s, n in shops(ctx.cfg)], "when": [f"L{i}r", ["head", "owner"]]})
    return fields


def parse(ctx, form):
    """→ (заказ [(ключ, подпись, должность, отдел, команда, склады, сколько)], руководители [(роль, [магазины])], ошибки)."""
    want, errs = [], []
    for _title, rows in plan(ctx):
        for key, label, pos, dept, team, whs_ in rows:
            x = str(form.get(key, "0")).replace(" ", "").strip() or "0"
            if not x.isdigit() or int(x) > 300:
                errs.append(f"«{label}» — целое число от 0 до 300")
                continue
            want.append((key, label, pos, dept, team, whs_, int(x)))
    leads = []
    ids = {s for s, _ in shops(ctx.cfg)}
    for i in range(LEADERS):
        r = str(form.get(f"L{i}r", "")).strip()
        if r not in ("head", "owner"):
            continue
        ss = [s for s in str(form.get(f"L{i}s", "")).split(",") if s in ids]
        if not ss:
            errs.append(f"Руководитель {i + 1}: отметьте, какие магазины он видит")
        leads.append((r, ss))
    return want, leads, errs


def apply(ctx, form):
    """Завести недостающих. → (сколько заведено, ошибки)."""
    want, leads, errs = parse(ctx, form)
    if errs:
        return 0, errs
    db, have, made = ctx.db, _have(ctx.db), 0
    names = {p["id"]: p["name"] for p in ctx.cfg.positions}
    from modules.ship import whs
    wn = dict(whs(ctx))
    sn = dict(shops(ctx.cfg))
    for key, label, pos, dept, team, whs_, n in want:
        place = sn.get(key.split("|")[1]) if key.startswith("s|") else wn.get(whs_[0]) if key.startswith("w|") else \
            "общие склады" if key.startswith("wa|") else next((d.name for d in ctx.cfg.departments if d.id == dept), dept)
        role = next((p["role"] for p in ctx.cfg.positions if p["id"] == pos), "staff")
        for i in range(have.get(key, 0), n):
            db.add_employee(f"{names.get(pos, pos)} {i + 1} · {place}", dept, team=team, position=pos, role=role,
                            extra={"auto": key, "n": i + 1, **({"whs": list(whs_)} if whs_ else {})})
            made += 1
    head, owner = lead_positions(ctx.cfg)
    sees = db.get(SEES_KEY) or {}
    for i, (r, ss) in enumerate(leads):
        key = f"L|{i}|{r}"
        p = head if r == "head" else owner
        e = next((x for x in db.employees() if (x.get("extra") or {}).get("auto") == key), None)
        if not e:
            title = "Владелец" if r == "owner" else p["name"]
            eid = db.add_employee(f"{title} {i + 1} · {', '.join(sn[s] for s in ss)}"[:80], p["dept"],
                                  team=ss[0] if r == "head" else "", position=p["id"], role=p["role"],
                                  extra={"auto": key, "n": i + 1})
            made += 1
        else:
            eid = e["id"]
        sees[str(eid)] = ss
    if leads:
        db.put(SEES_KEY, sees)
    return made, []


def paid_people(ctx):
    """Сколько людей в приложении за деньги (без владельца и администратора)."""
    return sum(1 for e in ctx.db.employees() if e["role"] not in ("owner", "admin"))


def sync_billing(ctx):
    """После заведения людей: «люди по отделам» в оплате — настоящие из приложения."""
    from core import billing as B
    st = B.state(ctx)
    rows = B.people_rows(ctx)
    st["dcounts"] = {k: n for k, _, _, n in rows}
    st["people"] = sum(st["dcounts"].values())
    st.pop("counts", None)
    B.save(ctx, st)
    return st["people"]


def form_from_names(ctx, data):
    """Форма по названиям (tools/hire.py): {"shops": {магазин: {должность: n}}, "warehouses": {склад: n}, "wh_head": n,
    "depts": {отдел: {должность: n}}, "leaders": [["head"|"owner", [магазины]]]} → (форма, ошибки)."""
    norm = lambda t: " ".join(str(t).lower().replace("ё", "е").split())
    from modules.ship import whs
    sids = {norm(n): s for s, n in shops(ctx.cfg)}
    wids = {norm(n): w for w, n in whs(ctx)}
    pos_by_name = {norm(p["name"]): p["id"] for p in ctx.cfg.positions}
    dept_by_name = {norm(d.name): d.id for d in ctx.cfg.departments}
    keys = {k for _t, rows in plan(ctx) for k, *_ in rows}
    form, errs = {k: "0" for k in keys}, []

    def put(key, n, what):
        if key not in keys:
            errs.append(f"нет такого места в форме: {what}")
        else:
            form[key] = str(int(n))
    for sname, ps in (data.get("shops") or {}).items():
        sid = sids.get(norm(sname))
        if not sid:
            errs.append(f"нет магазина «{sname}» — есть: {', '.join(n for _, n in shops(ctx.cfg))}")
            continue
        for pname, n in ps.items():
            pid = pos_by_name.get(norm(pname))
            if not pid:
                errs.append(f"нет должности «{pname}» — есть: {', '.join(p['name'] for p in ctx.cfg.positions)}")
                continue
            put(f"s|{sid}|{pid}", n, f"{sname} · {pname}")
    keeper = next((k.split("|")[2] for k in keys if k.startswith("w|")), None)
    for wname, n in (data.get("warehouses") or {}).items():
        wid = wids.get(norm(wname))
        if not wid:
            errs.append(f"нет склада «{wname}» — есть: {', '.join(n_ for _, n_ in whs(ctx))}")
            continue
        put(f"w|{wid}|{keeper}", n, f"{wname} (общий склад)")
    if data.get("wh_head"):
        k = next((k for k in keys if k.startswith("wa|")), None)
        put(k or "wa|?", data["wh_head"], "завсклад общих складов")
    for dname, ps in (data.get("depts") or {}).items():
        did = dept_by_name.get(norm(dname))
        if not did:
            errs.append(f"нет отдела «{dname}» — есть: {', '.join(d.name for d in ctx.cfg.departments)}")
            continue
        for pname, n in ps.items():
            put(f"d|{did}|{pos_by_name.get(norm(pname), '?')}", n, f"{dname} · {pname}")
    for i, (role, ss) in enumerate(data.get("leaders") or []):
        bad = [x for x in ss if norm(x) not in sids]
        if bad or role not in ("head", "owner") or i >= LEADERS:
            errs.append(f"руководитель {i + 1}: {role} {ss}")
            continue
        form[f"L{i}r"], form[f"L{i}s"] = role, ",".join(sids[norm(x)] for x in ss)
    return form, errs
