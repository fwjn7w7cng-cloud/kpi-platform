"""🧩 «Собрать бота» одной страницей (Адам 05.11: «как во втором приложении — набор собирается на одной-двух страницах, страница не
прыгает наверх»; выбраны эскизы 1 «Одна лента» и 2 «Две страницы»).

Экран `swb` отдаёт приложению всё сразу одним блоком `builder` (View.builder): наборы по размеру фирмы, отделы с функциями
и ценами, людей, цены платформы, блоки «🧱 Конструктора зарплаты» и схемы по должностям. Галочки, люди, цифры меняются прямо
на телефоне (web/app.html), сумма считается там же по тем же правилам, что `pricing.quote`. На сервер выбор уходит одним
JSON (`ctx.form`) — тихо после изменений (`swb:s:q`) и при «🧾 Пример расчёта» (`swb:s:ex:<должность>`), «📄 КП» (`:pdf`),
«📨 Хочу так» (`:req`), «📋 Анкета» (`:cd`), «Ступени подробно» (`:pb:<должность>`). Сохраняется туда же, где и раньше:
`funcs` / `counts` / `year` / `base` и `pb` (схемы зарплаты) в сессии зрителя.
"""
import copy

from core import paybuilder as PB
from core.access import editions as E
from core.access import pricing as P
from core.ui.kit import Reply
from core.ui.registry import screen
from core.ui.views import View

DEPTS = ("sales", "stock", "logistics", "office")
ROLES_OF = {"sales": ("rep", "sv", "head"), "stock": ("wh", "buyer"), "logistics": ("driver",), "office": ("office",)}
WHO = {"sales": "торговые, супервайзеры, руководитель", "stock": "кладовщики и закупщики",
       "logistics": "водители и диспетчер", "office": "бухгалтерия, касса, кадры"}
# частые способы оплаты — кнопки сверху у каждой должности: (блок, вариант, подпись)
QUICK = {
    "rep": [("salary", "days", "Оклад"), ("sales", "pct", "% от продаж"), ("sales", "steps", "Бонус за план"),
            ("debt_pen", "fixed", "Минус за просрочку"), ("fuel", "km", "ГСМ за км")],
    "sv": [("salary", "days", "Оклад"), ("sales", "pct", "% от продаж команды"), ("sales", "steps", "Бонус за план команды"),
           ("team_days", None, "Команда отработала месяц")],
    "head": [("salary", "days", "Оклад"), ("sales", "pct", "% от продаж отдела"), ("sales", "fix", "Бонус за план отдела")],
    "wh": [("salary", "days", "Оклад"), ("discipline", "both", "Бонус без ошибок и пропусков"), ("piece", "all_points", "За отгрузки отдела")],
    "buyer": [("salary", "days", "Оклад"), ("company", "steps", "Доля от плана компании"), ("cut", "stockout", "Минус за «закончилось»")],
    "driver": [("salary", "days", "Оклад"), ("piece", "km", "За км"), ("piece", "points", "За точку"),
               ("piece", "trips", "За рейс"), ("discipline", "both", "Бонус без ошибок и пропусков")],
    "office": [("salary", "calendar", "Оклад по календарю"), ("company", "steps", "Доля от плана компании")],
}
SIMPLE = {"money", "pct", "int", "choice", "text"}       # поля, которые меняются прямо на странице; остальное — «🧱 подробно»
MAX_ITEMS = 40


def _kind(bid, v, key):
    k = PB.F.get(key, ("", "text"))[1]
    if key == "price" and v in PB.RATE_UNITS:
        return "pct"
    return k


def _fields(bid, v, p):
    """[[поле, подпись, вид, варианты выбора | готовый текст]] — для приложения."""
    out = []
    for key, val in p.items():
        if key not in PB.F:
            continue
        label, kind = PB.F[key][0], _kind(bid, v, key)
        if kind == "choice":
            out.append([key, label, kind, [[k, t] for k, t in PB.F[key][2].items()]])
        elif kind in SIMPLE:
            out.append([key, label, kind, None])
        else:
            out.append([key, label, "fixed", PB.fmt(key, val)])
    return out


def bricks_json():
    """Все блоки конструктора зарплаты: {блок: {g, ic, t, note, roles, vs: [{v, t, g, p, f, for}]}}."""
    out = {}
    for bid, (grp, ic, title, roles, _vs, note) in PB.BRICKS.items():
        vs = []
        for v, label, p, _tpl in PB.variants(bid):
            vs.append({"v": v, "t": label, "g": PB.group(bid, v), "p": copy.deepcopy(p), "f": _fields(bid, v, p),
                       "for": list(PB.UNITS[v][1]) if bid in ("piece", "cut") else None,
                       "i": PB.BLOCK_VARIANT_INFO.get((bid, v)) or PB.VARIANT_INFO.get(v, "")})
        out[bid] = {"g": grp, "ic": ic, "t": title, "note": note, "roles": list(roles), "vs": vs}
    return out


def _fixed_text(items):
    """Готовый текст сложных полей (ступени, свои штрафы…) у сохранённых схем — их правят в «🧱 подробно»."""
    for it in items:
        it["fx"] = {k: PB.fmt(k, val) for k, val in it.get("p", {}).items()
                    if k in PB.F and _kind(it["b"], it["v"], k) not in SIMPLE}
    return items


def example_json(ctx, pos):
    emp, rs, _ = PB.example(ctx, pos, PB.items_of(ctx, pos))
    if not emp:
        return {"who": "", "rows": [], "total": 0, "none": "В демо-фирме нет человека на этой должности."}
    rows = [[str(n), round(s), str(x or "")] for n, s, x in rs]
    return {"who": emp["fio"], "rows": rows, "total": round(max(sum(r[1] for r in rows), 0))}


def data(ctx, ex=None, ui=None):
    from core.ui import tariff as T
    pr = P.prices(ctx.office)
    funcs = sorted(T.funcs_of(ctx.cfg))
    depts = []
    for d in DEPTS:
        icon, title = E.FUNC_GROUPS[d]
        depts.append({"k": d, "ic": icon, "t": title, "who": WHO[d], "roles": list(ROLES_OF[d]),
                      "fs": [[f, E.FUNC_SHORT[f], pr["f_" + f], T.LOCK.get(f, E.MODULES[f][0])] for f in E.FUNCS if E.FUNCS[f][0] == d]})
    roles = []
    for d in DEPTS:
        for pos in ROLES_OF[d]:
            title, ic = PB.POSITIONS[pos][:2]
            roles.append({"pos": pos, "t": title, "ic": ic, "d": d, "items": _fixed_text(copy.deepcopy(PB.items_of(ctx, pos))),
                          "quick": [[b, v or PB.variants(b, pos)[0][0], t] for b, v, t in QUICK[pos]]})
    sizes = [[c, x["title"], x["size"], dict(x["people"]), sorted(E.preset_functions(c))] for c, x in E.PRESETS.items()]
    q = T.quote_of(ctx)
    return {
        "sid": "swb", "sizes": sizes, "base": T.base_of(ctx), "depts": depts,
        "all": [[f, E.FUNC_SHORT[f], pr["f_" + f], T.LOCK.get(f, E.MODULES[f][0])] for f in E.FUNCS if E.FUNCS[f][0] == "all"],
        "extra": [[f, E.FUNC_SHORT[f], pr["f_" + f], T.LOCK.get(f, E.MODULES[f][0])] for f in E.FUNCS if E.FUNCS[f][0] == "max"],
        "funcs": funcs, "counts": T.counts_of(ctx), "year": bool(ctx.session.get("year")),
        "prices": {k: pr[k] for k in ("user", "disc_all", "disc_year", "setup_mult", "setup_min", "trial_days")} | {"round": P.ROUND},
        "requires": {k: sorted(v) for k, v in E.REQUIRES.items()},
        "roles": roles, "bricks": bricks_json(), "fields": {k: [x[0], x[1]] for k, x in PB.F.items()},
        "example": {ex: example_json(ctx, ex)} if ex in PB.POSITIONS else {},
        "ui": ui if isinstance(ui, dict) else {}, "check": {"monthly": q["monthly"], "setup": q["setup"], "year": q["monthly_year"]},
    }


def page(ctx, ex=None, ui=None):
    if not ctx.demo:
        return Reply(text="Набор подключённой фирмы меняется через «💳 Тариф».")
    v = View("🧩 Собрать бота", "Отметьте нужное — сумма внизу считается сразу")
    v.builder(data(ctx, ex, ui))
    v.note("Откройте демо в приложении — там бот собирается на одной странице.", where="txt")
    return Reply(view=v)


# ---------- сохранение выбора ----------

def _num(x, lo=0.0, hi=1e9):
    try:
        f = float(str(x).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return min(max(f, lo), hi)


def _clean_items(pos, items, old):
    out = []
    for it in (items if isinstance(items, list) else [])[:MAX_ITEMS]:
        if not isinstance(it, dict) or it.get("b") not in PB.BRICKS:
            continue
        b = it["b"]
        vs = {x[0]: x for x in PB.variants(b, pos)}
        v = it.get("v") if it.get("v") in vs else None
        if v is None or pos not in PB.BRICKS[b][3]:
            continue
        base = PB.brick(b, v)
        prev = next((o for o in old if o.get("b") == b and o.get("v") == v), None)
        p = copy.deepcopy(prev["p"]) if prev else base["p"]
        got = it.get("p") if isinstance(it.get("p"), dict) else {}
        for key in base["p"]:
            if key not in got:
                continue
            kind = _kind(b, v, key)
            val = got[key]
            if kind in ("money", "int"):
                n = _num(val, 0, 10_000_000)
                if n is not None:
                    p[key] = int(n) if kind == "int" or n == int(n) else n
            elif kind == "pct":
                n = _num(val, 0, 10)
                if n is not None:
                    p[key] = round(n, 6)
            elif kind == "choice":
                opts = PB.F[key][2]
                for k in opts:
                    if k == val or str(k) == str(val):
                        p[key] = k
            elif kind == "text":
                p[key] = " ".join(str(val).split())[:60]
        out.append({"b": b, "v": v, "p": p})
    return out


def save(ctx, form):
    """Выбор с телефона → сессия зрителя. Всё проверяется: чужие функции, блоки, цифры вне разумного — отбрасываются."""
    from core.ui import tariff as T
    if not isinstance(form, dict):
        return
    fs = {f for f in (form.get("funcs") or []) if f in E.FUNCS}
    for need, deps in E.REQUIRES.items():
        if need in fs:
            fs |= {d for d in deps if d in E.FUNCS}
    counts = dict(T.counts_of(ctx))
    for d in DEPTS:
        n = _num((form.get("counts") or {}).get(d), 0, 5000)
        if n is not None:
            counts[d] = int(n)
    ctx.session["counts"] = counts
    ctx.session["year"] = bool(form.get("year"))
    if form.get("base") in E.PRESETS:
        ctx.session["base"] = form["base"]
    pb = PB.state(ctx)
    for pos, items in (form.get("pb") or {}).items():
        if pos in PB.POSITIONS:
            pb[pos] = _clean_items(pos, items, PB.items_of(ctx, pos))
    ctx.session["pb"] = pb
    ctx.session["funcs"] = sorted(fs)
    ctx.session.pop("packages", None)
    ctx.session.pop("edition", None)
    ctx.session.pop("pk_draft", None)
    ctx.save()


@screen("swb")
def builder(ctx, act=None, *args):
    """swb — открыть; swb:s[:q | :ex:<должность> | :pdf | :req | :cd | :pb:<должность>] — сохранить выбор (ctx.form) и дальше."""
    if not ctx.demo:
        return page(ctx)
    form = getattr(ctx, "form", None)
    ui = form.get("ui") if isinstance(form, dict) else None
    if act == "s":
        save(ctx, form)
        ctx = ctx.app.ctx(ctx.user_id)
        nxt = args[0] if args else ""
        from core.ui import tariff as T
        if nxt == "pdf":
            return T.pdf(ctx)
        if nxt == "req":
            return T.request(ctx)
        if nxt == "cd":
            from core.ui.registry import SCREENS
            return SCREENS["cd"][0](ctx)
        if nxt == "pb" and len(args) > 1 and args[1] in PB.POSITIONS:
            return PB.page(ctx, args[1])
        if nxt == "ex" and len(args) > 1:
            return page(ctx, ex=args[1], ui=ui)
    return page(ctx, ui=ui)
