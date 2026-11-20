"""🛠 Пульт Алапы (Адам 17.11: «чтобы управлялось через пульт всё: скидки со сроком или бессрочно, ссылки гостям и партнёрам,
новые приложения, функции у каждого клиента — и вообще все функции, что у меня есть»). Живёт в приложении управления
(admin.alapa.pro, core/control.py), только у администратора платформы. Макет — Design-артефакт 5fbLMMpAxm7wAZhDH3ctUZ.

Вкладки внизу: 🏠 Сводка (control.money_screen) · 🏢 Клиенты (control.clients_list / card) · 🧩 Функции (mfn) · 🎟 Доступы (mcg) · ☰ Ещё (mcm).

Карточка клиента (control.card → card_blocks): функции переключателями (mcf:<код>:<функция> — сразу в его billing.functions,
данные не пропадают), скидки (mds окно, mdp выбор, mdv сохранить, mdx убрать; billing.discounts — сами в счёте и снимаются в срок),
история (ключ panel_hist в базе офиса клиента).
Каталог (mfn[:функция]): цена (mfp — у всех клиентов сразу, sync_prices), у кого включена (mfc:<функция>:<код>).
Доступы (mcg): гости, партнёры (скидка его клиентам — mpd), заявки «Хочу так» → новое приложение.
Ещё (mcm): акция для новых (promo: скидка на подключение до даты, mpm), общие цены (pr), инструкции.
Новое приложение (mnw, mnp выбор, mns создать): анкета-карточка → onboard.create → записка дежурному data/_duty/<код>.new
(deploy/duty: служба, адрес в Caddy, fail2ban — сами, Альфред ставит дежурного один раз).
"""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from core import billing as B
from core import control as K
from core.access import editions as E
from core.access import pricing as P
from core.calendar import shift_month
from core.config import SEGMENTS
from core.ui.kit import Button, Reply, money
from core.ui.registry import screen, text_input
from core.ui.views import View

log = logging.getLogger("kpi")
HKEY = "panel_hist"
GROUPS = [g for g in E.FUNC_GROUPS]                       # all, sales, stock, logistics, office, max
SIZE_PEOPLE = {"start": 6, "business": 15, "control": 30, "company": 63, "max": 127}


def _deny(ctx):
    return K._deny(ctx)


def _stamp():
    return datetime.now().strftime("%d.%m %H:%M")


def who(ctx):
    return "администратор"             # пульт — только у администратора платформы; в демо-роли «директор» его не путаем


def hist(c):
    return c.office.get(HKEY) or []


def add_hist(c, text, by=""):
    c.office.put(HKEY, ([[_stamp(), text + (f" · {by}" if by else "")]] + hist(c))[:200])


def _known(ctx, code):
    return code in K.codes() and code in [k for k, _ in K.all_clients(ctx.app, ctx.today)]


# ---------- функции клиента ----------

def card_blocks(v, ctx, code, c):
    """Функции, скидки и история — живыми блоками в карточке клиента (control.card)."""
    st = B.state(c)
    fs = set(B.funcs(c))
    pr = P.prices(c.office)
    n = B.people(c, st)
    from core.ui.tariff import quote_of
    q = quote_of(c, fs, year=False, counts=B.counts_for(c, n) or {"sales": 0})
    ds = B.active_discounts(st, ctx.today)
    total = B.monthly(c, st=st)
    fsum = sum(pr[f"f_{f}"] for f in fs)
    how = f"функции {money(fsum)} + люди {n} × {money(pr['user'])}"
    if q["discount"]:
        how += f" − скидка за все отделы {money(q['discount'])}"
    if ds:
        how += f" − скидки {money(q['monthly'] - total)}"
    v.ui("hero", {"label": f"{c.cfg.name} · в месяц", "big": money(total), "sub": how, "id": f"pc{code}",
                  "pairs": [["Функций", f"{len(fs)} из {len(E.FUNCS)}", ""], ["Людей", str(n), ""],
                            ["Было бы без скидок", money(q["monthly"]), ""] if ds else ["Подключение", money(B.setup_sum(c, st)), ""]]},
         fallback=[f"В месяц: {money(total)}", how])
    dc, typical = B.dcounts(c, st)
    rows = B.people_rows(c)
    if B.real_people(c):                              # люди заведены — счёт по ним сам, вписывать нечего (Адам 20.11)
        items = [{"t": label, "v": f"{app} чел."} for key, label, _, app in rows if app]
        v.ui("rows", {"items": items, "right": f"за деньги {n}",
                      "foot": [["👥 Завести ещё по ролям", f"mch:{code}"]]}, "👥 Люди по отделам — как в приложении",
             fallback=[f"{x['t']}: {x['v']}" for x in items])
        v.note(f"Счёт идёт по людям в приложении: {n} × {money(pr['user'])} = {money(n * pr['user'])} в месяц. "
               "Админ клиента добавил или уволил человека — сумма поменяется сама. Владелец и администратор — бесплатно.")
    else:
        fields = [{"k": key, "l": label, "v": str(dc.get(key, 0)), "num": True} for key, label, _, app in rows]
        hint = f"Всего {n} · {n} × {money(pr['user'])} = {money(n * pr['user'])} в месяц. Директор и админ клиента — бесплатно."
        if typical:
            hint = "⚠️ Типовые числа размера, не настоящие — впишите, сколько людей в каждом отделе. " + hint
        v.ui("form", {"fields": fields, "send": ["Сохранить", f"mcps:{code}"], "hint": hint + " Когда людей заведут — счёт пойдёт по ним."},
             "👥 Люди по отделам", fallback=[f"{f['l']}: {f['v']}" for f in fields])
    from core import payedit as PE
    uses = PE.who_uses(c.cfg)
    sch = PE.schemes(c.cfg)
    pedit = c.db.get(PE.KEY) or {}
    sitems = [{"t": ", ".join(names), "s": PE.summary(sch[name]) + (" · поправлено в пульте" if name in pedit else ""),
               "chip": ["✏️", f"mce:{code}:{name}", ""]} for name, names in uses.items()]
    v.ui("rows", {"items": sitems, "empty": "Схем зарплаты нет."}, "💵 Как считаем зарплату",
         fallback=[f"{x['t']}: {x['s']}" for x in sitems] or ["Схем зарплаты нет."])
    for g in GROUPS:
        icon, title = E.FUNC_GROUPS[g]
        items = []
        for f, (grp, _) in E.FUNCS.items():
            if grp != g:
                continue
            on = f in fs
            items.append({"t": E.FUNC_SHORT[f], "s": f"{money(pr[f'f_{f}'])} в месяц",
                          "chip": ["вкл" if on else "выкл", f"mcf:{code}:{f}", "good" if on else ""]})
        v.ui("rows", {"items": items, "right": f"{sum(f in fs for f, (gg, _) in E.FUNCS.items() if gg == g)} из {len(items)}"},
             f"{icon} {title}", fallback=[f"{x['t']}: {x['chip'][0]}" for x in items])
    v.note("Включили — у клиента сразу появилось. Выключили — его данные не пропадают: включите снова — всё на месте.")
    ditems = [{"t": B.discount_title(x), "s": B.discount_term(x) + (f" · {x['why']}" if x.get("why") else "")
               + ("" if x in ds else " · ещё не началась" if (x.get("start") or "") > ctx.today.isoformat() else " · закончилась"),
               "chip": ["✕", f"mdx:{code}:{x['id']}", "bad"]} for x in st.get("discounts") or []]
    v.ui("rows", {"items": ditems, "empty": "Скидок нет.", "foot": [["＋ Скидка", f"mds:{code}"]]}, "Скидки",
         fallback=[f"{x['t']} — {x['s']}" for x in ditems] or ["Скидок нет."])
    h = hist(c)[:12]
    if h:
        v.ui("rows", {"items": [{"t": t, "s": when} for when, t in h]}, "История", fallback=[f"{w} {t}" for w, t in h])


def _set_funcs(c, fs, text, by):
    st = B.state(c)
    st["functions"] = sorted(fs)
    B.save(c, st)
    add_hist(c, text, by)


@screen("mcf")
def toggle_func(ctx, code, f=""):
    no = _deny(ctx)
    if no or not _known(ctx, code) or f not in E.FUNCS:
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    fs = set(B.funcs(c))
    new = E.toggle_function(fs, f)
    on, off = new - fs, fs - new
    parts = [f"включил «{E.FUNC_SHORT[x]}»" for x in sorted(on)] + [f"выключил «{E.FUNC_SHORT[x]}»" for x in sorted(off)]
    _set_funcs(c, new, ", ".join(parts).capitalize(), who(ctx))
    return K.card(ctx, code)


# ---------- люди по отделам (Адам 20.11: «не могу менять количество людей в отделах») ----------

@screen("mcps")
def people_save(ctx, code):
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    form = getattr(ctx, "form", None) or {}
    c = K.cctx(ctx, code)
    rows = B.people_rows(c)
    dc = {}
    for key, label, _, _ in rows:
        x = str(form.get(key, "0")).replace(" ", "").strip() or "0"
        if not x.isdigit() or int(x) > 5000:
            return K.card(ctx, code, f"Не сохранил: «{label}» — нужно целое число от 0 до 5 000.")
        dc[key] = int(x)
    st = B.state(c)
    st["dcounts"], st["people"] = dc, sum(dc.values())
    st.pop("counts", None)
    B.save(c, st)
    add_hist(c, "Люди по отделам: " + ", ".join(f"{label} {dc[k]}" for k, label, _, _ in rows if dc[k]) + f" — всего {st['people']}",
             who(ctx))
    return K.card(ctx, code, f"✅ Люди сохранены: всего {st['people']}.")


# ---------- 👥 завести людей по ролям (Адам 20.11, эскиз HP.png) ----------

@screen("mch")
def hire_screen(ctx, code, note=None, form=None):
    from core import hire as H
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    v = View(f"👥 Завести людей · {c.cfg.name}", "по ролям, без имён — имена и ссылки для входа потом в его приложении")
    if note:
        v.note(note)
    have = sum(1 for e in c.db.employees() if (e.get("extra") or {}).get("auto"))
    v.ui("hero", {"label": "Сейчас в приложении", "big": f"{len(c.db.employees())} чел.", "id": f"mch{code}",
                  "sub": f"за деньги {H.paid_people(c)} — владелец и администратор бесплатно" + (f" · заведено отсюда {have}" if have else ""),
                  "pairs": []}, fallback=["Завести людей"])
    fields = H.form_fields(c, form)
    v.ui("sform", {"fields": fields, "send": ["👥 Завести недостающих", f"mchs:{code}"], "err": "", "top": [],
                   "note": "Числа — сколько людей должно быть. Повторное нажатие заводит только недостающих; лишних увольняют в его приложении. "
                           "Администратора Алапы назначьте потом в его приложении: «👥 Сотрудники» → человек → «🎚 Роль»."},
         "", fallback=[f["l"] for f in fields])
    return Reply(view=v, buttons=[[Button("‹ Карточка", f"mcc:{code}")]])


@screen("mchs")
def hire_save(ctx, code):
    from core import hire as H
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    form = getattr(ctx, "form", None) or {}
    c = K.cctx(ctx, code)
    made, errs = H.apply(c, form)
    if errs:
        return hire_screen(ctx, code, note="Не заведено: " + "; ".join(errs[:5]), form=form)
    c = K.cctx(ctx, code)
    paid = H.sync_billing(c)                         # люди по отделам — теперь настоящие из приложения
    add_hist(c, f"Заведено людей по ролям: {made}, всего за деньги {paid}", who(ctx))
    return K.card(ctx, code, f"✅ Заведено {made} чел. Всего за деньги {paid}. Имена и ссылки — в его приложении «👥 Сотрудники».")


# ---------- как считаем зарплату (Адам 20.11) ----------

@screen("mce")
def pay_edit(ctx, code, name="", note=None):
    from core import payedit as PE
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    sch = PE.schemes(c.cfg)
    if name not in sch:
        return K.card(ctx, code)
    names = PE.who_uses(c.cfg).get(name) or [name]
    v = View(f"💵 {', '.join(names)}", f"{c.cfg.name} · как считаем зарплату")
    if note:
        v.note(note)
    v.lines([PE.describe(x) for x in sch[name] if isinstance(x, dict)], title="Сейчас")
    fs = PE.fields(sch[name])
    if fs:
        v.ui("form", {"fields": fs, "send": ["Сохранить", f"mcev:{code}:{name}"],
                      "hint": "Проценты — «0,4» или «0,4%», суммы — «25 000». Сразу в приложении клиента, со следующего пересчёта."},
             "Цифры")
    else:
        v.note("Цифр для правки нет.")
    v.note("Добавить или убрать блок (например, «бонус за АКБ») — пока через Джарвиса, следующим шагом — прямо здесь.")
    kb = [[Button("‹ Назад", f"mcc:{code}")]]
    if name in (c.db.get(PE.KEY) or {}):
        kb.insert(0, [Button("↩️ Вернуть как в анкете", f"mcex:{code}:{name}")])
    return Reply(view=v, buttons=kb)


@screen("mcev")
def pay_save(ctx, code, name=""):
    from core import payedit as PE
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    sch = PE.schemes(c.cfg)
    if name not in sch:
        return K.card(ctx, code)
    new, problems = PE.apply_form(sch[name], getattr(ctx, "form", None) or {})
    if problems:
        return pay_edit(ctx, code, name, note="Не сохранил: " + "; ".join(problems[:5]))
    if new == sch[name]:
        return K.card(ctx, code, "Ничего не поменялось.")
    ed = c.db.get(PE.KEY) or {}
    ed[name] = new
    c.db.put(PE.KEY, ed)
    names = ", ".join(PE.who_uses(c.cfg).get(name) or [name])
    add_hist(c, f"Зарплата «{names}»: {PE.summary(new)}", who(ctx))
    return K.card(ctx, code, f"✅ Зарплата «{names}» сохранена.")


@screen("mcex")
def pay_reset(ctx, code, name=""):
    from core import payedit as PE
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    ed = c.db.get(PE.KEY) or {}
    if ed.pop(name, None) is not None:
        c.db.put(PE.KEY, ed)
        add_hist(c, f"Зарплата «{name}»: вернул как в анкете", who(ctx))
    return K.card(ctx, code, "↩️ Вернул как в анкете.")


# ---------- 🎨 свой бренд (Адам 20.11: «приложение в цветах логотипа», бесплатно у всех) ----------

def _brand_save(c, b, text, by):
    from core import brand as BR
    b = {**b, "ts": datetime.now().isoformat()}
    c.office.put(BR.KEY, b)
    add_hist(c, text, by)


@screen("mcb")
def brand_screen(ctx, code, note=None):
    from core import brand as BR
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    b = BR.get(c.office)
    ctx.session["upto"] = f"brand:{code}"
    ctx.save()
    v = View(f"🎨 Бренд · {c.cfg.name}", "логотип, название на значке и цвета приложения клиента")
    if note:
        v.note(note)
    files = [("значок.png", BR.icon(512, b))] if BR.logo_bytes(b) else []
    if not files:
        v.note("Логотипа пока нет — у клиента значок и цвета Алапы. Нажмите «Выбрать файлы» и пришлите PNG или JPG.")
    v.ui("form", {"fields": [{"k": "name", "l": "Название на значке телефона", "v": b.get("name") or c.cfg.name, "w": True},
                             {"k": "c1", "l": "Главный цвет (шапка, кнопки)", "v": b.get("c1", ""), "ph": "#0B1F4D"},
                             {"k": "c2", "l": "Второй цвет (полоса под шапкой)", "v": b.get("c2", ""), "ph": "#E0B04A"}],
                  "send": ["Сохранить", f"mcbs:{code}"],
                  "hint": "Цвета бот берёт из логотипа сам — поправьте, если не понравились. Приложение клиента обновится "
                          "при следующем открытии, значок на телефоне — тоже (на iPhone иногда только после переустановки)."}, "Как выглядит")
    kb = [[Button("‹ Назад", f"mcc:{code}")]]
    if b:
        kb.insert(0, [Button("↩️ Убрать бренд — как у Алапы", f"mcbx:{code}")])
    return Reply(view=v, buttons=kb, upload="image", files=files)


def brand_receive(ctx, code, name, data):
    from core import brand as BR
    if _deny(ctx) or not _known(ctx, code):
        return [Reply(text="Логотип загружает только администратор платформы.")]
    if not data or len(data) > 8 * 1024 * 1024:
        return [brand_screen(ctx, code, note="Файл пустой или больше 8 МБ.")]
    png, err = BR.prepare(data)
    if err:
        return [brand_screen(ctx, code, note=err)]
    c = K.cctx(ctx, code)
    c1, c2 = BR.colors(png)
    b = {**BR.get(c.office), "logo": __import__("base64").b64encode(png).decode(), "c1": c1, "c2": c2}
    b.setdefault("name", c.cfg.name)
    _brand_save(c, b, f"Бренд: новый логотип, цвета {c1} и {c2}", who(ctx))
    return [brand_screen(ctx, code, note=f"✅ Логотип загружен. Цвета из логотипа: {c1} и {c2}.")]


@screen("mcbs")
def brand_save(ctx, code):
    from core import brand as BR
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    form = getattr(ctx, "form", None) or {}
    c = K.cctx(ctx, code)
    b = dict(BR.get(c.office))
    name = str(form.get("name", "")).strip()[:30]
    cs = {}
    for k in ("c1", "c2"):
        x = str(form.get(k, "")).strip().upper()
        if x and not x.startswith("#"):
            x = "#" + x
        if x and not BR.HEX.fullmatch(x):
            return brand_screen(ctx, code, note=f"Цвет «{x}» — нужно как #0B1F4D (решётка и 6 знаков).")
        cs[k] = x
    b.update({"name": name or c.cfg.name, **{k: x for k, x in cs.items() if x}})
    _brand_save(c, b, f"Бренд: «{b['name']}», цвета {b.get('c1', '—')} и {b.get('c2', '—')}", who(ctx))
    return brand_screen(ctx, code, note="✅ Сохранено.")


@screen("mcbx")
def brand_drop(ctx, code):
    from core import brand as BR
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    c.office.put(BR.KEY, {"ts": datetime.now().isoformat()})
    add_hist(c, "Бренд убран — как у Алапы", who(ctx))
    return K.card(ctx, code, "↩️ Бренд убран: значок и цвета Алапы.")


# ---------- скидки ----------

def _draft(ctx, code):
    d = ctx.session.get("disc")
    if not isinstance(d, dict) or d.get("code") != code:
        d = {"code": code, "what": "all", "term": "date"}
        ctx.session["disc"] = d
        ctx.save()
    return d


@screen("mds")
def discount_screen(ctx, code, note=None):
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    d = _draft(ctx, code)
    fs = sorted(B.funcs(c), key=list(E.FUNCS).index)
    v = View(f"🏷 Скидка · {c.cfg.name}", f"сейчас {money(B.monthly(c))} в месяц")
    if note:
        v.note(note)
    what = [["На всё", f"mdp:{code}:w:all", d["what"] == "all"], ["На людей", f"mdp:{code}:w:people", d["what"] == "people"]]
    what += [[E.FUNC_SHORT[f], f"mdp:{code}:w:{f}", d["what"] == f] for f in fs]
    v.ui("choice", {"items": what}, "На что")
    v.ui("choice", {"items": [["До даты", f"mdp:{code}:t:date", d["term"] == "date"], ["На месяцы", f"mdp:{code}:t:months", d["term"] == "months"],
                              ["Бессрочно", f"mdp:{code}:t:forever", d["term"] == "forever"]]}, "Срок")
    fields = [{"k": "pct", "l": "Сколько, %", "ph": "10", "num": True}]
    if d["term"] == "date":
        last = date(ctx.today.year, 12, 31)
        fields.append({"k": "till", "l": "До какого числа", "ph": f"{last:%d.%m.%Y}", "v": f"{last:%d.%m.%Y}"})
    elif d["term"] == "months":
        fields.append({"k": "months", "l": "Сколько месяцев", "ph": "3", "v": "3", "num": True})
    fields.append({"k": "why", "l": "Причина (видите только вы)", "ph": "Например: первый клиент в городе", "w": True})
    v.ui("form", {"fields": fields, "send": ["Сохранить скидку", f"mdv:{code}"],
                  "hint": "Скидка сразу попадает в его счёт и снимается сама в последний день срока."}, "Скидка")
    return Reply(view=v)


@screen("mdp")
def discount_pick(ctx, code, kind="", value=""):
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    d = _draft(ctx, code)
    if kind == "w" and (value in ("all", "people") or value in E.FUNCS):
        d["what"] = value
    elif kind == "t" and value in ("date", "months", "forever"):
        d["term"] = value
    ctx.session["disc"] = d
    ctx.save()
    return discount_screen(ctx, code)


def parse_date(s, today):
    s = (s or "").strip().replace("/", ".").replace("-", ".")
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d.%m"):
        try:
            d = datetime.strptime(s, fmt).date()
            if fmt == "%d.%m":
                d = d.replace(year=today.year)
                if d < today:
                    d = d.replace(year=today.year + 1)
            return d
        except ValueError:
            continue
    return None


def months_end(start, n):
    """Последний день срока «на n месяцев» от start: 17.11 + 3 мес. → 16.02."""
    m = shift_month(start.strftime("%Y-%m"), n)
    y, mo = int(m[:4]), int(m[5:7])
    from calendar import monthrange
    day = min(start.day, monthrange(y, mo)[1])
    return date(y, mo, day) - timedelta(days=1)


@screen("mdv")
def discount_save(ctx, code):
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    form = getattr(ctx, "form", None) or {}
    d = _draft(ctx, code)
    try:
        pct = float(str(form.get("pct", "")).replace(",", ".").replace("%", "").strip())
    except ValueError:
        pct = 0
    if not 0 < pct <= 100:
        return discount_screen(ctx, code, "Впишите процент — от 1 до 100.")
    till = None
    if d["term"] == "date":
        t = parse_date(form.get("till"), ctx.today)
        if not t or t < ctx.today:
            return discount_screen(ctx, code, "Дата — как 31.12.2026, и не раньше сегодняшней.")
        till = t
    elif d["term"] == "months":
        try:
            n = int(str(form.get("months", "")).strip())
        except ValueError:
            n = 0
        if not 1 <= n <= 60:
            return discount_screen(ctx, code, "Месяцев — от 1 до 60.")
        till = months_end(ctx.today, n)
    c = K.cctx(ctx, code)
    st = B.state(c)
    ds = st.setdefault("discounts", [])
    x = {"id": max([int(y.get("id", 0)) for y in ds] + [0]) + 1, "what": d["what"], "pct": int(pct) if pct == int(pct) else pct,
         "start": ctx.today.isoformat(), "till": till.isoformat() if till else None, "why": str(form.get("why", "")).strip()[:120],
         "ts": _stamp(), "who": who(ctx)}
    ds.append(x)
    B.save(c, st)
    add_hist(c, f"Скидка {B.discount_title(x)} · {B.discount_term(x)}", who(ctx))
    ctx.session.pop("disc", None)
    ctx.save()
    return K.card(ctx, code, f"✅ Скидка {B.discount_title(x)} · {B.discount_term(x)} — уже в его счёте.")


@screen("mdx")
def discount_del(ctx, code, did=""):
    no = _deny(ctx)
    if no or not _known(ctx, code):
        return no or K.money_screen(ctx)
    c = K.cctx(ctx, code)
    st = B.state(c)
    x = next((y for y in st.get("discounts") or [] if str(y.get("id")) == did), None)
    if x:
        st["discounts"] = [y for y in st["discounts"] if y is not x]
        B.save(c, st)
        add_hist(c, f"Убрал скидку {B.discount_title(x)}", who(ctx))
    return K.card(ctx, code)


def ending_discounts(c, today, days=3):
    """Скидки, что кончаются в ближайшие days дней (для сводки)."""
    st = B.state(c)
    lim = (today + timedelta(days=days)).isoformat()
    return [x for x in st.get("discounts") or [] if x.get("till") and today.isoformat() <= x["till"] <= lim]


# ---------- каталог функций ----------

def sync_prices(ctx):
    """Цены демо (их правит администратор) → в базу каждого клиента: у всех одинаково и сразу."""
    own = ctx.office.get(P.KEY) or {}
    for code, c in K.all_clients(ctx.app, ctx.today):
        try:
            c.office.put(P.KEY, dict(own))
        except Exception:                             # noqa: BLE001
            log.exception("пульт: цены → %s", code)


@screen("mfn")
def catalog(ctx, f=None, note=None):
    no = _deny(ctx)
    if no:
        return no
    cl = K.all_clients(ctx.app, ctx.today)
    has = {code: set(B.funcs(c)) for code, c in cl}
    pr = P.prices(ctx.office)
    if f in E.FUNCS:
        grp, _ = E.FUNCS[f]
        icon, gt = E.FUNC_GROUPS[grp]
        v = View(f"{icon} {E.FUNC_SHORT[f]}", f"{gt} · {money(pr[f'f_{f}'])} в месяц")
        if note:
            v.note(note)
        from core.ui.screens import LOCK_TEXT
        if LOCK_TEXT.get(f):
            v.lines([LOCK_TEXT[f]], title="Что это")
        v.ui("form", {"fields": [{"k": "price", "l": "Цена в месяц, ₽", "v": str(int(pr[f"f_{f}"])), "num": True, "money": True}],
                      "send": ["Сохранить цену", f"mfp:{f}"],
                      "hint": "Новая цена — сразу у всех: в демо, в «Собрать» и в счетах клиентов. Кому оставить старую — дайте ему скидку."},
             "Цена")
        items = [{"t": c.cfg.name, "s": "включена" if f in has[code] else "выключена",
                  "chip": ["вкл" if f in has[code] else "выкл", f"mfc:{f}:{code}", "good" if f in has[code] else ""]} for code, c in cl]
        v.ui("rows", {"items": items, "empty": "Клиентов пока нет.", "right": f"{sum(f in h for h in has.values())} из {len(cl)}"},
             "У кого включена", fallback=[f"{x['t']}: {x['s']}" for x in items])
        return Reply(view=v)
    v = View("🧩 Все функции", f"{len(E.FUNCS)} функций · клиентов {len(cl)}")
    if note:
        v.note(note)
    for g in GROUPS:
        icon, title = E.FUNC_GROUPS[g]
        items = []
        for x, (grp, _) in E.FUNCS.items():
            if grp != g:
                continue
            n = sum(x in h for h in has.values())
            items.append({"t": E.FUNC_SHORT[x], "s": f"у {n} из {len(cl)}" if n else "ещё ни у кого",
                          "v": money(pr[f"f_{x}"]), "chip": ["›", f"mfn:{x}", ""]})
        v.ui("rows", {"items": items}, f"{icon} {title}", fallback=[f"{x['t']} — {x['v']}, {x['s']}" for x in items])
    v.note("Новая функция появляется здесь сама после обновления — «ещё ни у кого». Включите её нужным клиентам.")
    return Reply(view=v, buttons=[[Button("💰 Общие цены: люди, подключение, скидки", "pr")]])


@screen("mfc")
def catalog_toggle(ctx, f, code=""):
    no = _deny(ctx)
    if no or f not in E.FUNCS or not _known(ctx, code):
        return no or catalog(ctx)
    c = K.cctx(ctx, code)
    fs = set(B.funcs(c))
    new = E.toggle_function(fs, f)
    on, off = new - fs, fs - new
    parts = [f"включил «{E.FUNC_SHORT[x]}»" for x in sorted(on)] + [f"выключил «{E.FUNC_SHORT[x]}»" for x in sorted(off)]
    _set_funcs(c, new, ", ".join(parts).capitalize() + " (из каталога)", who(ctx))
    return catalog(ctx, f)


@screen("mfp")
def catalog_price(ctx, f):
    no = _deny(ctx)
    if no or f not in E.FUNCS:
        return no or catalog(ctx)
    raw = str((getattr(ctx, "form", None) or {}).get("price", "")).replace(" ", "").replace("\xa0", "").replace("₽", "")
    try:
        x = int(float(raw.replace(",", ".")))
    except ValueError:
        return catalog(ctx, f, "Цена — числом, например 3000.")
    if not 0 <= x <= 1_000_000:
        return catalog(ctx, f, "Цена — от 0 до 1 000 000.")
    P.set_price(ctx.office, f"f_{f}", x, who=who(ctx))
    sync_prices(ctx)
    return catalog(ctx, f, f"✅ Новая цена {money(x)} — у всех клиентов и в «Собрать».")


# ---------- доступы ----------

@screen("mcg")
def access(ctx):
    no = _deny(ctx)
    if no:
        return no
    from core import demo as D
    guests = D._guests(ctx)
    now = ctx.today.isoformat()
    gs = [(u, g) for u, g in guests.items() if not g.get("partner")]
    ps = [(u, g) for u, g in guests.items() if g.get("partner")]
    v = View("🎟 Доступы", "гости демо, партнёры, заявки «Хочу так»")
    v.ui("rows", {"items": [{"t": D._title(g, u), "s": ("до " + D._dm(g.get("till")) if g.get("status") == "on" and (g.get("till") or "") >= now
                                                       else "закрыт") + f" · нажатий {g.get('presses', 0)}",
                             "chip": ["›", f"gso:{u}", "good" if g.get("status") == "on" else ""]}
                            for u, g in sorted(gs, key=lambda t: t[1].get("last") or "", reverse=True)[:12]],
                  "empty": "Гостей пока нет.", "foot": [["＋ Ссылка гостю", "gsi"], ["Все гости", "gst"]]},
         "Гости демо")
    v.ui("rows", {"items": [{"t": D._title(g, u), "s": (("работает" if g.get("status") == "on" else "отключён") + " · " +
                                                       partner_disc_text(g)),
                             "chip": ["🏷", f"mpd:{u}", ""]} for u, g in ps],
                  "empty": "Партнёров пока нет.", "foot": [["＋ Добавить партнёра", "pta"], ["Все партнёры", "ptl"]]},
         "Партнёры", fallback=[D._title(g, u) for u, g in ps])
    reqs = [r for r in ctx.office.get("tariff_req") or [] if not r.get("created")]
    v.ui("rows", {"items": [{"t": r.get("who", "—"), "s": f"{money(r.get('monthly', 0))}/мес · {r.get('ts', '')}",
                             "chip": ["Создать", f"mnw:r{i}", "good"]} for i, r in enumerate(ctx.office.get("tariff_req") or [])
                            if not r.get("created")][-8:],
                  "empty": "Новых заявок нет.", "foot": [["📋 Анкеты", "cds"]]},
         f"Заявки «Хочу так» · {len(reqs)}")
    return Reply(view=v)


def partner_disc(g):
    d = g.get("disc") or {}
    try:
        return {"pct": float(d.get("pct") or 0), "months": int(d.get("months") or 0)} if float(d.get("pct") or 0) > 0 else None
    except (TypeError, ValueError):
        return None


def partner_disc_text(g):
    d = partner_disc(g)
    return f"его клиентам −{P.fmt(d['pct'])}% на {d['months']} мес." if d else "без скидки его клиентам"


@screen("mpd")
def partner_disc_screen(ctx, uid):
    no = _deny(ctx)
    if no:
        return no
    from core import demo as D
    g = D._guests(ctx).get(str(uid))
    if not g or not g.get("partner"):
        return access(ctx)
    return ctx.ask("p_disc", f"🏷 {D._title(g, uid)}: скидка его клиентам. Сейчас: {partner_disc_text(g)}.\n\n"
                             "Напишите процент и на сколько месяцев, например «10 3». «0» — без скидки.",
                   {"uid": str(uid)}, buttons=[[Button("✖️ Отмена", "mcg")]])


@text_input("p_disc")
def on_partner_disc(ctx, text, data):
    if _deny(ctx):
        return _deny(ctx)
    from core import demo as D
    nums = [x for x in (text or "").replace("%", " ").replace(",", ".").split() if x.replace(".", "", 1).isdigit()]
    guests = D._guests(ctx)
    g = guests.get(data["uid"])
    if not g:
        return access(ctx)
    if nums and float(nums[0]) == 0:
        g.pop("disc", None)
    elif len(nums) >= 2 and 0 < float(nums[0]) <= 50 and 1 <= int(float(nums[1])) <= 24:
        g["disc"] = {"pct": float(nums[0]), "months": int(float(nums[1]))}
    else:
        return ctx.ask("p_disc", "Так: «10 3» — 10% на 3 месяца (до 50% и до 24 месяцев), или «0».", data,
                       buttons=[[Button("✖️ Отмена", "mcg")]])
    D._put(ctx, guests=guests)
    return access(ctx)


# ---------- ещё: акция и общие ----------

@screen("mcm")
def more(ctx, note=None):
    no = _deny(ctx)
    if no:
        return no
    from core.ui.tariff import promo
    p = ctx.office.get("promo") or {}
    act = promo(ctx)
    v = View("☰ Ещё", "акция для новых, общие цены, инструкции")
    if note:
        v.note(note)
    till = p.get("till") or date(ctx.today.year, 12, 31).isoformat()
    v.ui("form", {"fields": [{"k": "pct", "l": "Скидка на подключение, %", "v": str(P.fmt(p.get("pct") or 50)), "num": True},
                             {"k": "till", "l": "Действует до", "v": f"{till[8:10]}.{till[5:7]}.{till[:4]}"}],
                  "send": ["Включить акцию" if not act else "Сохранить", "mpm:on"],
                  "hint": (f"Идёт: подключение −{P.fmt(act['pct'])}% до {B.dm(date.fromisoformat(act['till']))}. Новые видят это в «Собрать», "
                           "в последний день выключится сама." if act else
                           "Новые клиенты сразу видят сниженную цену подключения в «Собрать». После даты выключится сама.")},
         "Акция для новых клиентов" + (" · идёт" if act else ""))
    pr = P.prices(ctx.office)
    v.ui("rows", {"items": [{"t": "За человека в приложении", "v": money(pr["user"])},
                            {"t": "Подключение", "s": f"не меньше {money(pr['setup_min'])}", "v": f"месяц × {P.fmt(pr['setup_mult'])}"},
                            {"t": "Скидка за оплату на год", "v": f"{P.fmt(pr['disc_year'])}%"},
                            {"t": "Скидка за все четыре отдела", "v": f"{P.fmt(pr['disc_all'])}%"}],
                  "foot": [["✏️ Изменить", "pr"]]}, "Общие цены")
    kb = [[Button("📘 Моя инструкция", "adi"), Button("📘 Инструкция партнёра", "ptm")]]
    if act:
        kb.insert(0, [Button("⏹ Остановить акцию", "mpm:off")])
    return Reply(view=v, buttons=kb)


@screen("mpm")
def promo_save(ctx, act="on"):
    no = _deny(ctx)
    if no:
        return no
    p = dict(ctx.office.get("promo") or {})
    if act == "off":
        p["on"] = False
        ctx.office.put("promo", p)
        ctx.office.audit(who(ctx), "promo", "акция остановлена")
        return more(ctx, "Акция остановлена.")
    form = getattr(ctx, "form", None) or {}
    try:
        pct = float(str(form.get("pct", "")).replace(",", ".").replace("%", "").strip())
    except ValueError:
        pct = 0
    till = parse_date(form.get("till"), ctx.today)
    if not 0 < pct <= 90:
        return more(ctx, "Скидка — от 1 до 90%.")
    if not till or till < ctx.today:
        return more(ctx, "Дата — как 31.12.2026, и не раньше сегодняшней.")
    ctx.office.put("promo", {"on": True, "pct": pct, "till": till.isoformat()})
    ctx.office.audit(who(ctx), "promo", f"акция: подключение −{P.fmt(pct)}% до {till:%d.%m.%Y}")
    return more(ctx, f"✅ Акция идёт: подключение −{P.fmt(pct)}% до {till:%d.%m.%Y}.")


# ---------- новое приложение ----------

DUTY_DIR = "_duty"


def duty_dir(app):
    from core.data.db import client_db_path
    base = app.data_dir if app.data_dir else client_db_path(app.client_id).parent.parent
    return Path(base) / DUTY_DIR


def duty_state(app, code):
    """Что с приложением на сервере: wait — записка лежит, ждём дежурного; ok — запущено; bad — дежурный не смог; '' — записки не было."""
    d = duty_dir(app)
    for st in ("ok", "bad", "new"):
        if (d / f"{code}.{st}").exists():
            return {"new": "wait"}.get(st, st)
    return ""


def duty_note(app, code):
    try:
        return (duty_dir(app) / f"{code}.bad").read_text(encoding="utf-8")[:300]
    except OSError:
        return ""


def _nw(ctx, fresh=None):
    d = ctx.session.get("nw")
    if fresh is not None or not isinstance(d, dict):
        d = fresh or {"size": "business", "depts": ["sales"], "req": None}
        ctx.session["nw"] = d
        ctx.save()
    return d


def _nw_funcs(d):
    fs = set(E.preset_functions(d["size"]))
    if d.get("src") == "alapa":                      # учёт в Алапе: товары, приходы, продажа — функции склада и закупок
        d["depts"] = [x for x in E.DEPT_CODES if x in set(d["depts"]) | {"stock"}]
    fs = {f for f in fs if E.FUNCS[f][0] in ("all", "max") or E.FUNCS[f][0] in d["depts"]}
    for dep in d["depts"]:
        if dep != "sales":
            fs |= {f for f, (g, _) in E.FUNCS.items() if g == dep}
    fs -= set(E.ACC_FUNCS)
    if d.get("src") == "alapa":                      # учёт в Алапе — свои функции (Адам 20.11)
        seg = d.get("seg", "opt")
        fs |= {"goods", "ship"} | ({"shop_sale"} if seg != "opt" else set()) | ({"opt_sale"} if seg != "retail" else set())
    return {f for f in fs if E.FUNCS[f][0] != "max"}


def _nw_counts(d):
    people = E.PRESETS[d["size"]]["people"]
    n = SIZE_PEOPLE[d["size"]]
    out = {k: (people.get(k) or 0) for k in d["depts"]}
    if not any(out.values()):
        out[d["depts"][0] if d["depts"] else "sales"] = n
    return out


# «Новое приложение» по шагам (Адам 20.11, эскиз WZ.png): всё, что доводили у первого клиента, — до кнопки «Создать».
# Черновик — session.nw: размер, отделы, src, seg, name, shops, whs, teams (текст), wo {склад: магазин|""}, people (поля hire),
# adm (строка hire → администратор клиента), icon, logo/c1/c2 (бренд), fx (функции, None — по выбору).
# Шаги показываются по ответам: магазинов нет — нет магазинов и складов-магазинов, один магазин — нет «кто какие видит».

class _StubDB:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        from modules.ship import OWNER_KEY
        if k == OWNER_KEY:
            return dict(self._d.get("wo") or {})
        return default

    def employees(self, *a, **kw):
        return []


class _Stub:
    """Будущий клиент — анкета из черновика, пустая база: для формы людей (core/hire) и складов (modules/ship)."""
    def __init__(self, cfg, d):
        self.cfg, self.db = cfg, _StubDB(d)


def _nw_card(d, fs=None, counts=None):
    from core import onboard as O
    seg = d.get("seg", "opt")
    return {"uid": "nw", "by": "", "f": {"company": d.get("name") or "Новый клиент"}, "notes": [],
            "set": {"packages": E.packages_of(fs if fs is not None else _nw_funcs(d), set(d["depts"])),
                    "functions": sorted(fs if fs is not None else _nw_funcs(d)), "depts": list(d["depts"]),
                    "counts": counts or {}, "segment": seg, "source": d.get("src", "1c"),
                    "shops": O.shop_list(d.get("shops", "")) if seg != "opt" else [], "whs": O.shop_list(d.get("whs", "")),
                    "teams": O.shop_list(d.get("teams", "")) if seg != "retail" else []}}


def _nw_cfg(d):
    """Анкета будущего клиента из черновика или (None, ошибки)."""
    from core import config as C
    from core import onboard as O
    raw = O.build_raw(_nw_card(d, _nw_fx(d)))
    try:
        return C.parse(raw["id"], raw), []
    except C.ConfigError as e:
        return None, list(e.problems)


def _nw_fx(d):
    return set(d["fx"]) if isinstance(d.get("fx"), list) else _nw_funcs(d)


def _nw_paid(d, stub):
    """Сколько людей за деньги: всё из формы людей минус владельцы и администратор."""
    from core import hire as H
    want, leads, _ = H.parse(stub, d.get("people") or {})
    n = sum(x[-1] for x in want if not x[0].startswith("o|")) + sum(1 for r, _ in leads if r == "head")
    return max(0, n - (1 if d.get("adm") else 0)), sum(x[-1] for x in want) + len(leads)


@screen("mnw")
def new_app(ctx, src=None, note=None):
    no = _deny(ctx)
    if no:
        return no
    reqs = ctx.office.get("tariff_req") or []
    if src and src.startswith("r") and src[1:].isdigit() and int(src[1:]) < len(reqs):     # из заявки «Хочу так»
        r = reqs[int(src[1:])]
        d = _nw(ctx, {"size": "business", "depts": ["sales"], "req": int(src[1:]), "name": (r.get("who") or "").split(",")[0].strip()})
    else:
        d = _nw(ctx, {"size": "business", "depts": ["sales"], "req": None}) if src == "new" else _nw(ctx)
    ctx.session["upto"] = "nwlogo"
    ctx.save()
    from core import hire as H
    from core.ui.tariff import quote_of
    seg, alapa = d.get("seg", "opt"), d.get("src") == "alapa"
    v = View("＋ Новое приложение", "всё, что нужно клиенту, — до кнопки «Создать»")
    if note:
        v.note(note)
    # 1 · Фирма — выборы кнопками (страница пересобирается)
    v.ui("choice", {"items": [["📊 Из 1С", "mnp:src:1c", not alapa], ["📦 Учёт в Алапе", "mnp:src:alapa", alapa]]}, "1 · Откуда цифры")
    v.ui("choice", {"items": [[t, f"mnp:seg:{k}", seg == k] for k, t in SEGMENTS.items()]}, "Как продают")
    v.ui("choice", {"items": [[E.FUNC_GROUPS[k][1], f"mnp:d:{k}", k in d["depts"]] for k in E.DEPT_CODES]}, "Отделы")
    v.ui("choice", {"items": [[E.SIZE_NAMES[k][0], f"mnp:s:{k}", d["size"] == k] for k in E.PRESETS]}, "Набор функций для начала")
    cfg, problems = _nw_cfg(d)
    stub = _Stub(cfg, d) if cfg else None
    # 2–6 · одна форма: фирма, магазины, склады, команды, люди, руководители, администратор, название на значке
    f = [{"k": "h1", "t": "head", "l": "Фирма"},
         {"k": "name", "t": "text", "l": "Название фирмы", "v": d.get("name", ""), "ph": "Например: Стройторг", "req": 1}]
    if seg != "opt":
        f += [{"k": "h2", "t": "head", "l": "2 · Магазины"},
              {"k": "shops", "t": "area", "l": "Каждый с новой строки (один магазин — одна строка)", "v": d.get("shops", ""), "ph": "Барт\nБарт Премиум"}]
    if alapa:
        f += [{"k": "h3", "t": "head", "l": "3 · Склады"},
              {"k": "whs", "t": "area", "l": "Каждый с новой строки. Пусто — один «Склад». Склад при магазине назовите с его именем: «Склад Фаида»",
               "v": d.get("whs", ""), "ph": "Склад 1\nСклад Фаида"}]
    if seg != "retail" and "sales" in d["depts"]:
        f += [{"k": "h3b", "t": "head", "l": "Команды опта"},
              {"k": "teams", "t": "area", "l": "Каждая с новой строки. Пусто — одна команда", "v": d.get("teams", ""), "ph": "Команда 1\nКоманда 2"}]
    if stub:
        from modules.ship import owners, whs
        from core.ui.retail import shops as shop_list
        shs = shop_list(cfg)
        if alapa and shs and len(whs(stub)) > 1:
            ow = owners(stub)
            f.append({"k": "h3c", "t": "head", "l": "Чьи склады — бот понял по названию, можно поправить"})
            f += [{"k": f"wo|{w}", "t": "seg", "l": n, "v": ow.get(w, ""), "opts": [["", "Общий"]] + [[x, y] for x, y in shs]}
                  for w, n in whs(stub)]
        f.append({"k": "h4", "t": "head", "l": "4 · Люди по ролям — сколько на каждом месте"})
        f += [dict(x, l="5 · " + x["l"]) if x["k"] == "hL" else x for x in H.form_fields(stub, d.get("people") or {})]
        f.append({"k": "h6", "t": "head", "l": "6 · Администратор клиента — вписывает имена и раздаёт ссылки (бесплатно)"})
        f.append({"k": "adm", "t": "seg", "l": "Первый заведённый на месте:", "v": d.get("adm", ""),
                  "opts": [["", "назначу потом"]] + H.admin_options(stub)})
    f += [{"k": "h7", "t": "head", "l": "7 · Бренд"},
          {"k": "icon", "t": "text", "l": "Название на значке телефона", "v": d.get("icon", "") or d.get("name", "")}]
    v.ui("sform", {"fields": f, "send": ["💾 Сохранить и пересчитать", "mnsv"], "err": "; ".join(problems[:3]), "top": [],
                   "note": "Поменяли магазины, склады или команды — нажмите «Сохранить»: появятся поля людей по ним."}, "",
         fallback=[x["l"] for x in f])
    files = []
    if d.get("logo"):
        from core import brand as BR
        files = [("значок.png", BR.icon(512, {"logo": d["logo"]}))]
        v.note(f"🎨 Логотип есть · цвета {d.get('c1')} и {d.get('c2')} (поправить — после создания, «🎨 Бренд»).")
    else:
        v.note("🎨 Логотип — кнопка «Выбрать логотип» внизу (можно и потом в карточке клиента).")
    # 8 · Функции и цена
    fs = _nw_fx(d)
    for g in GROUPS:
        icon, title = E.FUNC_GROUPS[g]
        items = [{"t": E.FUNC_SHORT[x], "s": f"{money(P.prices(ctx.office)[f'f_{x}'])} в месяц",
                  "chip": ["вкл" if x in fs else "выкл", f"mnf:{x}", "good" if x in fs else ""]}
                 for x, (grp, _) in E.FUNCS.items() if grp == g]
        if items:
            v.ui("rows", {"items": items, "right": f"{sum(x in fs for x, (gg, _) in E.FUNCS.items() if gg == g)} из {len(items)}"},
                 f"8 · {icon} {title}" if g == GROUPS[0] else f"{icon} {title}", fallback=[i["t"] for i in items])
    paid, total = _nw_paid(d, stub) if stub else (0, 0)
    q = quote_of(ctx, fs, year=False, counts={next((k for k in E.DEPT_CODES if k in d["depts"]), "sales"): paid}, depts=set(d["depts"]))
    v.ui("hero", {"label": f"{d.get('name') or 'Новый клиент'} · в месяц", "big": money(q["monthly"]), "id": "mnwq",
                  "sub": f"{len(fs)} функций + {paid} чел. × {money(P.prices(ctx.office)['user'])} · людей заводим {total}, бесплатно — владельцы и администратор",
                  "pairs": [["Подключение", money(q["setup"]), ""]] + ([["Акция", f"−{P.fmt(q['promo']['pct'])}%", ""]] if q.get("promo") else [])},
         fallback=[f"В месяц {money(q['monthly'])}"])
    if d.get("req") is not None and d["req"] < len(reqs):
        r = reqs[d["req"]]
        v.note(f"По заявке «Хочу так»: {r.get('who', '')} · {r.get('set', '')}")
    return Reply(view=v, buttons=[[Button("🚀 Создать приложение", "mns")]], upload="image", files=files)


def _nw_take_form(ctx, d):
    """Поля формы → черновик."""
    form = getattr(ctx, "form", None) or {}
    for k in ("name", "shops", "whs", "teams", "icon", "adm"):
        if k in form:
            d[k] = str(form.get(k, ""))[:2000]
    d["name"] = d.get("name", "").strip()[:80]
    wo = {k[3:]: str(x) for k, x in form.items() if k.startswith("wo|")}
    if wo:
        d["wo"] = wo
    ppl = {k: str(x) for k, x in form.items() if "|" in k and not k.startswith("wo|") or k[:1] == "L" and k[1:-1].isdigit()}
    if ppl:
        d["people"] = {**(d.get("people") or {}), **ppl}
    ctx.session["nw"] = d
    ctx.save()
    return d


@screen("mnsv")
def new_app_save(ctx):
    no = _deny(ctx)
    if no:
        return no
    _nw_take_form(ctx, _nw(ctx))
    return new_app(ctx, note="💾 Сохранено.")


@screen("mnf")
def new_app_func(ctx, f=""):
    no = _deny(ctx)
    if no:
        return no
    d = _nw(ctx)
    if f in E.FUNCS:
        d["fx"] = sorted(E.toggle_function(_nw_fx(d), f))
        ctx.session["nw"] = d
        ctx.save()
    return new_app(ctx)


def nw_logo(ctx, name, data):
    """Логотип в черновик нового приложения (reports.receive по session.upto = nwlogo)."""
    import base64
    from core import brand as BR
    if _deny(ctx):
        return [Reply(text="Только администратор платформы.")]
    png, err = BR.prepare(data or b"")
    if err:
        return [new_app(ctx, note=err)]
    d = _nw(ctx)
    d["c1"], d["c2"] = BR.colors(png)
    d["logo"] = base64.b64encode(png).decode()
    ctx.session["nw"] = d
    ctx.save()
    return [new_app(ctx, note=f"✅ Логотип в черновике. Цвета: {d['c1']} и {d['c2']}.")]


@screen("mnp")
def new_app_pick(ctx, kind="", value=""):
    no = _deny(ctx)
    if no:
        return no
    d = _nw(ctx)
    if kind == "s" and value in E.PRESETS:
        d["size"] = value
    elif kind == "src" and value in ("1c", "alapa"):
        d["src"] = value
    elif kind == "seg" and value in SEGMENTS:
        d["seg"] = value
    elif kind == "d" and value in E.DEPT_CODES:
        ds = set(d["depts"]) ^ {value}
        d["depts"] = [x for x in E.DEPT_CODES if x in ds] or ["sales"]
    d.pop("fx", None)                                      # выбор поменялся — функции снова по выбору
    ctx.session["nw"] = d
    ctx.save()
    return new_app(ctx)


@screen("mns")
def new_app_create(ctx):
    no = _deny(ctx)
    if no:
        return no
    from core import onboard as O
    from core import hire as H
    from core.ui.tariff import quote_of
    d = _nw_take_form(ctx, _nw(ctx))
    name, seg = d.get("name", ""), d.get("seg", "opt")
    shops, whs_ = O.shop_list(d.get("shops", "")), O.shop_list(d.get("whs", ""))
    bad = ("Впишите название фирмы." if len(name) < 2 else
           "Впишите магазины — каждый с новой строки." if seg != "opt" and not shops else
           "Магазин и склад не могут называться одинаково." if {x.lower() for x in shops} & {x.lower() for x in whs_} else "")
    cfg, problems = _nw_cfg(d)
    if not bad and problems:
        bad = "Не получилось: " + "; ".join(problems[:4])
    stub = _Stub(cfg, d) if cfg else None
    if not bad and stub and d.get("people"):
        _, _, errs = H.parse(stub, d["people"])
        bad = "Люди: " + "; ".join(errs[:3]) if errs else ""
    if bad:
        return new_app(ctx, note=bad)
    fs = _nw_fx(d)
    paid, total = _nw_paid(d, stub) if d.get("people") else (0, 0)
    counts = _nw_counts(d) if not d.get("people") else {next((k for k in E.DEPT_CODES if k in d["depts"]), "sales"): paid}
    q = quote_of(ctx, fs, year=False, counts=counts, depts=set(d["depts"]))
    reqs = ctx.office.get("tariff_req") or []
    r = reqs[d["req"]] if d.get("req") is not None and d["req"] < len(reqs) else None
    uid = str(r.get("uid")) if r and r.get("uid") else f"pult{datetime.now():%m%d%H%M%S}"
    c = O.ensure(ctx, uid) if r and r.get("uid") else {"uid": uid, "by": "", "f": {}, "set": {}, "notes": [], "created": _stamp()}
    if c.get("client"):
        return new_app(ctx, note=f"По этой заявке приложение уже есть: {c['client']['url']}.")
    card = _nw_card(d, fs, counts)
    c["f"]["company"] = name
    c["set"] = {**(c.get("set") or {}), **card["set"], "people": sum(counts.values()), "monthly": q["monthly"], "setup": q["setup"]}
    info, problems = O.create(c, who(ctx), O.domain(ctx))
    if problems:
        return new_app(ctx, note="Не получилось: " + "; ".join(problems[:4]))
    c["client"] = info
    c.setdefault("notes", []).append({"ts": info["created"], "who": who(ctx), "text": f"создан клиент {info['code']} ({info['url']}) из пульта"})
    O.save(ctx.office, uid, c)
    if r is not None:
        r["created"] = info["code"]
        ctx.office.put("tariff_req", reqs)
    ctx.office.audit(who(ctx), "client", f"создан клиент {info['code']} из пульта")
    done = []
    try:                                                    # всё из черновика — в его базу
        capp = K.client_app(ctx.app, info["code"], ctx.today)
        cc = capp.ctx(K.ADMIN)
        st = B.state(cc)
        st["people"], st["setup"], st["functions"] = sum(counts.values()), q["setup"], sorted(fs)
        st["counts"] = {k: int(counts.get(k) or 0) for k in E.DEPT_CODES}
        from core import demo as D
        pid = str(c.get("by") or (r or {}).get("partner") or "")
        pg = D._guests(ctx).get(pid) if pid else None
        pdisc = partner_disc(pg) if pg else None
        if pdisc:
            st["discounts"] = [{"id": 1, "what": "all", "pct": pdisc["pct"], "start": ctx.today.isoformat(),
                                "till": months_end(ctx.today, pdisc["months"]).isoformat(), "why": f"клиент партнёра {D._title(pg, pid)}",
                                "ts": _stamp(), "who": who(ctx)}]
        B.save(cc, st)
        if d.get("wo"):
            from modules.ship import OWNER_KEY
            cc.db.put(OWNER_KEY, dict(d["wo"]))
        cc = capp.ctx(K.ADMIN)
        if d.get("people"):
            made, _ = H.apply(cc, d["people"])
            done.append(f"заведено людей {made}")
            if d.get("adm"):
                fio = H.make_admin(cc, d["adm"])
                if fio:
                    done.append(f"администратор — {fio}")
            cc = capp.ctx(K.ADMIN)
            H.sync_billing(cc)
        if d.get("logo") or d.get("icon"):
            from core import brand as BR
            cc.office.put(BR.KEY, {"name": (d.get("icon") or name)[:30], **({"logo": d["logo"], "c1": d.get("c1"), "c2": d.get("c2")} if d.get("logo") else {}),
                                   "ts": datetime.now().isoformat()})
            done.append("бренд")
        add_hist(cc, f"Создано приложение {info['url']}" + (" · " + ", ".join(done) if done else ""), who(ctx))
    except Exception:                                 # noqa: BLE001 — приложение создано, настройки поправим в карточке
        log.exception("пульт: стартовые настройки %s", info["code"])
    # записка дежурному на сервере: запустить службу, адрес в Caddy, защиту
    try:
        dd = duty_dir(ctx.app)
        dd.mkdir(parents=True, exist_ok=True)
        (dd / f"{info['code']}.new").write_text(json.dumps({"code": info["code"], "port": info["port"], "url": info["url"],
                                                            "ts": info["created"]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("пульт: записка дежурному")
    ctx.session.pop("nw", None)
    ctx.session.pop("upto", None)
    ctx.save()
    return K.card(ctx, info["code"], f"✅ Создано: {info['url']}" + (" · " + ", ".join(done) if done else "") +
                  ". Дежурный запускает приложение — обычно до минуты. Ваша ссылка администратора — кнопка ниже (не пересылать; людям клиента — приглашения из «Сотрудников»).")
