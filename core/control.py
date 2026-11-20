"""💰 Управление клиентами (Адам 01.11, «приложение управления»): живёт в «🛠 Пульт» демо, только у администратора платформы.

Видит всех настоящих клиентов (папки clients/<код>/, кроме шаблонов и показов) и ведёт их оплату — то же, что «🛠 Даты и оплата» в
приложении клиента (core/billing), но для всех сразу:
  mc            💰 Деньги: сколько ждём в этом месяце, сколько пришло, кто должен; плитки по состояниям
  mcl:<что>     список клиентов (all / setup / due / late / ok)
  mcc:<код>     карточка: даты, сумма, счета по месяцам, кнопки mca:<код>:<действие> (c, l, p, s, u, h, x, d)
  mcr           заявки «добавить / убрать» всех клиентов: mcy:<код>:<№> одобрить, mcn:<код>:<№> отказать (с причиной)
  mcw           неделя вперёд по дням
Каждое утро в 09:00 — сводка администратору (digest; веб-провод демо зовёт её раз в день). Ограничения при неоплате включаются
сами в приложении клиента (billing.level / gate, вариант А); за день до них в сводке — «⏳ Отсрочка».
Сообщения клиенту (одобрено / отказано) кладутся прямо в его ленту и уходят 🔔 на телефон.
"""
import logging
from datetime import timedelta
from pathlib import Path

from core import billing as B
from core.calendar import month_of
from core import config as C
from core.ui.kit import Button, Reply, money, rows
from core.ui.registry import screen, text_input
from core.ui.views import Card, Cell, Row, View

log = logging.getLogger("kpi")
ADMIN = -1                      # от чьего имени пульт действует в приложении клиента (там он — администратор платформы)
BUCKETS = [("setup", "🛠 Настраиваем", None), ("due", "💳 Ждут оплаты", "warn"),
           ("late", "⚠️ Просрочка", "bad"), ("ok", "✅ Оплачено", "good")]
DOT = {"setup": "🛠", "due": "💳", "late": "⚠️", "ok": "✅"}


def _deny(ctx):
    return None if ctx.demo and ctx.user_id in ctx.app.admins else Reply(text="Это экран администратора.")


def codes(base=None):
    """Коды клиентов: папки с анкетой, кроме шаблона (показы с наполнителем отсекает all_clients)."""
    base = Path(base or C.CLIENTS)
    return [d.name for d in sorted(base.iterdir()) if d.is_dir() and not d.name.startswith((".", "_"))
            and (d / "config.yaml").exists()] if base.exists() else []


def client_app(app, code, today):
    """Приложение клиента (его анкета и база) — по одному на день, чтобы не открывать базу на каждое нажатие."""
    from core.app import App
    cache = app.__dict__.setdefault("_control", {})
    key = (code, today)
    if key not in cache:
        for k in [k for k in cache if k[0] == code]:
            cache.pop(k)
        cache[key] = App(code, today=today, admins=[ADMIN], data_dir=app.data_dir)
    return cache[key]


def cctx(ctx, code):
    return client_app(ctx.app, code, ctx.today).ctx(ADMIN)


def all_clients(app, today):
    """[(код, ctx клиента)] — только те, что открылись без ошибок (и не показы)."""
    out = []
    for code in codes():
        try:
            a = client_app(app, code, today)
            if a.demo:
                continue
            out.append((code, a.ctx(ADMIN)))
        except Exception:                             # noqa: BLE001 — один сломанный клиент не валит пульт
            log.exception("пульт: клиент %s", code)
    return out


def bucket(c, st):
    if not st.get("launch"):
        return "setup"
    if B.due_bills(c, st):
        return "late" if B.level(c, st) != "ok" or c.today > B.due_bills(c, st)[0][1][1] else "due"
    return "ok"


def info(code, c):
    st = B.state(c)
    b = bucket(c, st)
    nb = B.next_bill(c, st)
    due = B.due_bills(c, st)
    owe = sum(x[1][2] - B.got(st, x[0]) for x in due)
    if b == "setup":
        when = "договорились " + B.dm(B._d(st["contract"])) if st.get("contract") else "ещё не договорились"
    elif due:
        when = f"должен {money(owe)}, срок {B.dm(due[0][1][1])}"
    else:
        when = f"счёт {B.dm(nb[1][0])}" if nb else ""
    return {"code": code, "name": c.cfg.name, "b": b, "monthly": B.monthly(c, st=st), "when": when, "owe": owe,
            "level": B.level(c, st), "wait": [r for r in st.get("req") or [] if r.get("status") == "wait"], "st": st}


# ---------- 💰 Деньги ----------

@screen("mc")
def money_screen(ctx):
    """🏠 Сводка пульта (Адам 17.11): деньги месяца и что сделать сегодня — каждое дело своей кнопкой."""
    no = _deny(ctx)
    if no:
        return no
    from core import pult as PU
    cl = all_clients(ctx.app, ctx.today)
    items = [info(code, c) for code, c in cl]
    mo = month_of(ctx.today)
    expect = came = 0
    for code, c in cl:
        st = B.state(c)
        b = B.bill(c, st, mo) if st.get("launch") else None
        if b:
            expect += b[2]
            came += b[2] if B.is_paid(st, mo, b) else B.got(st, mo)
    owe = sum(i["owe"] for i in items)
    v = View("🏠 Сводка", f"{B.mname(mo).capitalize()} · клиентов {len(items)}")
    v.ui("hero", {"label": f"{B.mname(mo).capitalize()} · пришло денег", "big": money(came), "id": "pult",
                  "sub": f"из {money(expect)} по счетам этого месяца",
                  "pairs": [["Ждём", money(max(expect - came, 0)), ""], ["Должны", money(owe), "bad" if owe else ""],
                            ["В месяц всего", money(sum(i["monthly"] for i in items)), ""]]},
         fallback=[f"Пришло {money(came)} из {money(expect)}", f"Должны {money(owe)}"])
    todo = []
    for code, c in cl:
        for x in PU.ending_discounts(c, ctx.today):
            todo.append({"t": f"{c.cfg.name}: скидка {B.discount_title(x)} кончается", "s": B.discount_term(x) + " — продлить или пусть закончится",
                         "chip": ["Открыть", f"mcc:{code}", "warn"]})
        st = PU.duty_state(ctx.app, code)
        if st in ("wait", "bad"):
            todo.append({"t": f"{c.cfg.name}: " + ("дежурный ещё запускает приложение" if st == "wait" else "дежурный не смог запустить"),
                         "s": PU.duty_note(ctx.app, code) or "обычно до минуты", "chip": ["Открыть", f"mcc:{code}", "bad" if st == "bad" else "warn"]})
    for i in items:
        if i["wait"]:
            todo.append({"t": f"{i['name']}: просит {len(i['wait'])} изменени" + ("е" if len(i["wait"]) == 1 else "я"), "s": "добавить или убрать функции, людей",
                         "chip": ["Решить", "mcr", "warn"]})
        if i["b"] == "late":
            todo.append({"t": f"{i['name']}: просрочка", "s": i["when"], "chip": ["Открыть", f"mcc:{i['code']}", "bad"]})
    for d, what in sorted((d, f"{c.cfg.name}: {w}") for code, c in cl for d, w in events(c, B.state(c), ctx.today, days=1)):
        todo.append({"t": what, "s": "сегодня", "chip": ["", "", ""]})
    reqs = [r for r in ctx.office.get("tariff_req") or [] if not r.get("created")]
    if reqs:
        todo.append({"t": f"Заявки «Хочу так»: {len(reqs)}", "s": reqs[-1].get("who", ""), "chip": ["Открыть", "mcg", "good"]})
    from core.ui.tariff import promo
    pr = promo(ctx)
    if pr:
        todo.append({"t": f"Идёт акция: подключение −{B.P.fmt(pr['pct'])}%", "s": f"до {B.dm(B._d(pr['till']))}", "chip": ["Настроить", "mcm", ""]})
    for t in todo:
        if not t["chip"][1]:
            t.pop("chip")
    v.ui("rows", {"items": todo, "empty": "На сегодня дел нет."}, "Сегодня", fallback=[t["t"] for t in todo] or ["На сегодня дел нет."])
    v.ui("quick", {"items": [["📅 Неделя вперёд", "mcw"], ["📨 Заявки клиентов", "mcr"], ["＋ Новое приложение", "mnw:new"]]},
         fallback=["Неделя вперёд · Заявки клиентов · Новое приложение"])
    return Reply(view=v)


@screen("mcl")
def clients_list(ctx, which="all"):
    no = _deny(ctx)
    if no:
        return no
    from core import pult as PU
    items = [(info(code, c), c) for code, c in all_clients(ctx.app, ctx.today)]
    if which != "all":
        items = [x for x in items if x[0]["b"] == which]
    title = dict((k, t) for k, t, _ in BUCKETS).get(which, "🏢 Клиенты")
    v = View(title, f"{len(items)} клиент(ов)")
    tone = {"late": "bad", "due": "warn", "ok": "good", "setup": ""}
    rows_ = []
    for i, c in items:
        st = B.state(c)
        ds = B.active_discounts(st, ctx.today)
        duty = PU.duty_state(ctx.app, i["code"])
        sub = f"{len(B.funcs(c))} функций · {B.people(c, st)} чел. · {i['when']}" + (" · " + ", ".join(B.discount_title(x) for x in ds) if ds else "") \
            + (" · ⏳ запускается" if duty == "wait" else " · ⚠️ не запустилось" if duty == "bad" else "")
        rows_.append({"t": f"{DOT[i['b']]} {i['name']}", "s": sub, "v": money(i["monthly"]), "chip": ["›", f"mcc:{i['code']}", tone[i["b"]]]})
    v.ui("rows", {"items": rows_, "empty": "Клиентов пока нет — «＋ Новое приложение»."}, "",
         fallback=[f"{x['t']}: {x['v']}" for x in rows_])
    return Reply(view=v, buttons=[[Button("＋ Новое приложение", "mnw:new")]])


# ---------- карточка ----------

def _link(code):
    """Ссылка администратора в приложение клиента — из его .env (WEB_URL, WEB_ADMIN_KEY)."""
    env = {}
    try:
        for line in (C.CLIENTS / code / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    except OSError:
        return ""
    return f"{env['WEB_URL']}/?admin={env['WEB_ADMIN_KEY']}" if env.get("WEB_URL") and env.get("WEB_ADMIN_KEY") else ""


@screen("mcc")
def card(ctx, code, note=None):
    no = _deny(ctx)
    if no or code not in [k for k, _ in all_clients(ctx.app, ctx.today)]:
        return no or money_screen(ctx)
    c = cctx(ctx, code)
    st = B.state(c)
    i = info(code, c)
    from core import pult as PU
    v = View(f"{DOT[i['b']]} {c.cfg.name}", (_link(code).split("/?")[0].split("//")[-1] or code))
    if note:
        v.note(note)
    duty = PU.duty_state(ctx.app, code)
    if duty == "wait":
        v.note("⏳ Дежурный на сервере запускает приложение — обычно до минуты. Обновите экран.")
    elif duty == "bad":
        v.note("⚠️ Дежурный не смог запустить: " + (PU.duty_note(ctx.app, code) or "причина в журнале сервера") + ". Скажите Альфреду.")
    PU.card_blocks(v, ctx, code, c)
    v.lines([B.status(c, st)[0]], title="Деньги и даты")
    lv = {"remind": "напоминание", "view": "👀 только просмотр", "off": "⛔ доступ закрыт"}.get(i["level"])
    if lv:
        v.note(f"У клиента сейчас: {lv}.")
    v.table(["Когда и что", "₽"], B.sched_rows(c, st))
    if st.get("hold"):
        v.lines([f"⏳ Отсрочка до {B.dm(B._d(st['hold']))} включительно."])
    kb = [[Button("🤝 Договорились сегодня", f"mca:{code}:c"), Button("🚀 Запуск сегодня", f"mca:{code}:l")]]
    pay_mo = B.pay_month(c, st)
    if pay_mo:
        kb.append([Button(f"✅ Оплачено всё за {B.mname(pay_mo)}", f"mca:{code}:p"), Button("✏️ Частично", f"mca:{code}:s")])
    if st.get("marks") or st.get("paid"):
        kb.append([Button("↩️ Снять последнюю отметку", f"mca:{code}:u")])
    kb.append([Button(f"⏳ Отсрочка на {B.HOLD_DAYS} дней", f"mca:{code}:h")] +
              ([Button("✖️ Снять отсрочку", f"mca:{code}:x")] if st.get("hold") else []))
    kb.append([Button("✏️ Вписать даты", f"mca:{code}:d")])
    kb.insert(0, [Button("🎨 Бренд: логотип и цвета", f"mcb:{code}")])
    kb.insert(0, [Button("👥 Завести людей по ролям", f"mch:{code}")])
    if i["wait"]:
        kb.append([Button(f"📨 Заявки ({len(i['wait'])})", "mcr")])
    url = _link(code)
    if url:
        kb.insert(0, [Button("🔑 Открыть его приложение", url=url)])
        v.lines([url], title="🔑 Ваша ссылка администратора — не пересылать никому")
    return Reply(view=v, buttons=kb)


@screen("mca")
def card_action(ctx, code, act=""):
    no = _deny(ctx)
    if no or code not in [k for k, _ in all_clients(ctx.app, ctx.today)]:
        return no or money_screen(ctx)
    c = cctx(ctx, code)
    st = B.state(c)
    if act == "s":
        mo = B.pay_month(c, st)
        if not mo:
            return card(ctx, code, "Счетов к оплате нет.")
        b = B.bill(c, st, mo)
        return ctx.ask("mc_sum", f"{c.cfg.name}: сколько пришло за {B.mname(mo)}? Счёт {money(b[2])}, уже отмечено "
                                 f"{money(B.got(st, mo))}. Напишите сумму, например 22 500.", {"code": code, "mo": mo},
                       buttons=[[Button("✖️ Отмена", f"mcc:{code}")]])
    if act == "d":
        return ctx.ask("mc_dates", f"{c.cfg.name}: даты через пробел — когда договорились и запуск, например "
                                   "«16.11.2026 20.11.2026». Запуска ещё не было — только первую дату.", {"code": code},
                       buttons=[[Button("✖️ Отмена", f"mcc:{code}")]])
    if act in ("c", "l", "p", "u", "h", "x"):
        r = B.manage(c, act)
        notes = [b[0] for k, b in (r.view.blocks if r and r.view else []) if k == "note"]
        return card(ctx, code, notes[0] if notes else None)
    return card(ctx, code)


@text_input("mc_sum")
def on_sum(ctx, text, data):
    if _deny(ctx):
        return _deny(ctx)
    x = B.parse_sum(text)
    if x <= 0:
        return ctx.ask("mc_sum", "Нужна сумма числом, например 22 500.", data, buttons=[[Button("✖️ Отмена", f"mcc:{data['code']}")]])
    B.apply_sum(cctx(ctx, data["code"]), data["mo"], x)
    return card(ctx, data["code"], f"✅ Отмечено {money(x)} за {B.mname(data['mo'])}.")


@text_input("mc_dates")
def on_dates(ctx, text, data):
    if _deny(ctx):
        return _deny(ctx)
    if not B.apply_dates(cctx(ctx, data["code"]), text):
        return ctx.ask("mc_dates", "Не понял. Пример: «16.11.2026 20.11.2026».", data,
                       buttons=[[Button("✖️ Отмена", f"mcc:{data['code']}")]])
    return card(ctx, data["code"], "Даты записаны.")


# ---------- заявки ----------

def notify(capp, replies):
    """Сообщения людям клиента — в их ленту в приложении клиента и 🔔 на телефон."""
    from core import push
    from core.ui.web import reply_json
    office = capp.office()
    for r in replies:
        if not r.to:
            continue
        item = reply_json(r, {})
        key = f"inbox:{int(r.to)}"
        office.put(key, ((office.get(key) or []) + [item])[-50:])
        try:
            if push.has(office, r.to):
                title, body = push.summary(item)
                push.send_async(capp.office, r.to, title, body)
        except Exception:                             # noqa: BLE001 — уведомление не главное
            log.exception("пульт: уведомление")


@screen("mcr")
def requests_screen(ctx, note=None):
    no = _deny(ctx)
    if no:
        return no
    v = View("📨 Заявки клиентов", "добавить или убрать функции и людей")
    if note:
        v.note(note)
    kb, cards = [], []
    for code, c in all_clients(ctx.app, ctx.today):
        for r in [r for r in B.state(c).get("req") or [] if r.get("status") == "wait"]:
            d = r["new"] - r["old"]
            cards.append(Card(f"{c.cfg.name}: {B.req_title(r)}", B.signed(d) + " в месяц",
                              [f"{r['ts']} · {r.get('who', '')}", f"было {money(r['old'])} → станет {money(r['new'])}",
                               f"за остаток месяца: {money(r['now'])}" if r.get("now") else "в этом месяце без доплаты"]))
            kb.append([Button(f"✅ {c.cfg.name}: одобрить", f"mcy:{code}:{r['id']}"), Button("❌ Отказать", f"mcn:{code}:{r['id']}")])
    if cards:
        v.cards(cards)
    else:
        v.note("Новых заявок нет.")
    kb.append([Button("💰 Деньги", "mc")])
    return Reply(view=v, buttons=kb)


@screen("mcy")
def approve(ctx, code, rid=""):
    no = _deny(ctx)
    if no or code not in [k for k, _ in all_clients(ctx.app, ctx.today)]:
        return no or requests_screen(ctx)
    capp = client_app(ctx.app, code, ctx.today)
    out = B.approve(capp.ctx(ADMIN), rid)
    notify(capp, [r for r in out if r.to])
    capp._memo.clear()
    return requests_screen(ctx, f"✅ {capp.cfg.name}: одобрено, клиенту ушло уведомление.")


@screen("mcn")
def reject(ctx, code, rid=""):
    no = _deny(ctx)
    if no or code not in [k for k, _ in all_clients(ctx.app, ctx.today)]:
        return no or requests_screen(ctx)
    return ctx.ask("mc_no", "Почему отказ? Напишите причину для клиента или «-» — без комментария.", {"code": code, "id": rid},
                   buttons=[[Button("✖️ Отмена", "mcr")]])


@text_input("mc_no")
def on_reject(ctx, text, data):
    if _deny(ctx):
        return _deny(ctx)
    capp = client_app(ctx.app, data["code"], ctx.today)
    out = B.do_reject(capp.ctx(ADMIN), data.get("id"), text)
    if out is None:
        return requests_screen(ctx, "Эта заявка уже решена.")
    notify(capp, out)
    return requests_screen(ctx, f"❌ {capp.cfg.name}: отказ отправлен клиенту.")


# ---------- неделя ----------

def events(c, st, start, days=7):
    """[(дата, что)] по клиенту на days дней вперёд."""
    out = []
    end = start + timedelta(days=days - 1)
    fp = B.first_paid(c, st)
    if fp:
        mo = max(B.month_of(fp), B.shift_month(B.month_of(start), -1))
        for _ in range(4):
            b = B.bill(c, st, mo)
            if b and not B.is_paid(st, mo, b):
                if start <= b[0] <= end:
                    out.append((b[0], f"счёт {money(b[2])}"))
                if start <= b[1] <= end and b[1] != b[0]:
                    out.append((b[1], f"срок оплаты {money(b[2] - B.got(st, mo))}"))
            mo = B.shift_month(mo, 1)
    lim = B.limit_dates(c, st)
    if lim:
        for d, what in zip(lim, ("👀 включится только просмотр", "⛔ закроется доступ")):
            if start <= d <= end:
                out.append((d, what))
    return out


@screen("mcw")
def week(ctx):
    no = _deny(ctx)
    if no:
        return no
    ev = {}
    for code, c in all_clients(ctx.app, ctx.today):
        st = B.state(c)
        for d, what in events(c, st, ctx.today):
            ev.setdefault(d, []).append(f"{c.cfg.name}: {what}")
    v = View("📅 Неделя вперёд", f"с {B.dm(ctx.today)}")
    for i in range(7):
        d = ctx.today + timedelta(days=i)
        if ev.get(d):
            v.lines(ev[d], title=("Сегодня, " if i == 0 else "Завтра, " if i == 1 else "") + B.dm(d))
    if not ev:
        v.note("На неделю ничего: ни счетов, ни сроков.")
    return Reply(view=v, buttons=[[Button("💰 Деньги", "mc")]])


# ---------- утренняя сводка ----------

def digest(app, today):
    """Сводка администратору на утро: счета и сроки сегодня, просрочка, что ограничится завтра, новые заявки. None — если пусто."""
    lines, kb = [], []
    tomorrow = today + timedelta(days=1)
    waits = 0
    for code, c in all_clients(app, today):
        st = B.state(c)
        name = c.cfg.name
        for d, what in events(c, st, today, days=2):
            if d == today:
                lines.append(f"• {name}: сегодня {what}")
            elif "включится" in what or "закроется" in what:
                lines.append(f"• {name}: завтра {what}")
                kb.append([Button(f"⏳ {name}: отсрочка {B.HOLD_DAYS} дней", f"mca:{code}:h")])
        lv = B.level(c, st)
        if lv in ("remind", "view", "off"):
            due = B.due_bills(c, st)
            owe = sum(x[1][2] - B.got(st, x[0]) for x in due)
            lines.append(f"• {name}: должен {money(owe)} — " + {"remind": "напоминание", "view": "сейчас только просмотр",
                                                                  "off": "доступ закрыт"}[lv])
        waits += sum(r.get("status") == "wait" for r in st.get("req") or [])
    if waits:
        lines.append(f"• Новых заявок: {waits}")
        kb.append([Button(f"📨 Заявки ({waits})", "mcr")])
    if not lines:
        return None
    kb.append([Button("💰 Деньги и клиенты", "mc")])
    to = app.office().get("panel_admins") or sorted(app.admins)     # в приложение управления, если в нём уже входили
    return [Reply(text="☀️ Клиенты на сегодня, " + B.dm(today) + ":\n" + "\n".join(lines), to=a, buttons=kb)
            for a in sorted(set(int(x) for x in to)) if a > 0]


class Morning:
    """Работник веб-провода демо: раз в день в DIGEST_AT — сводка администратору (остальной фоновой работы в демо нет)."""
    DIGEST_AT = (9, 0)

    def __init__(self, app, deliver):
        self.app, self.deliver, self.srcs = app, deliver, []

    def tick(self, now):
        office = self.app.office()
        day = now.date().isoformat()
        if (now.hour, now.minute) < self.DIGEST_AT or office.get("control_digest_day") == day:
            return []
        office.put("control_digest_day", day)
        try:
            out = digest(self.app, now.date()) or []
        except Exception:                             # noqa: BLE001
            log.exception("пульт: утренняя сводка")
            out = []
        if out:
            self.deliver(out)
        return out
