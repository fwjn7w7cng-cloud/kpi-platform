"""Доступ к демо-боту: кто может смотреть и до какого числа.

Включается при запуске (App(demo_gate=True), в run.py — для клиента с source.kind: seed).
Два способа войти:
  • приглашение — админ жмёт «➕ Пригласить», выбирает срок, отправляет клиенту ссылку t.me/<бот>?start=<код>;
    ссылка одноразовая, доступ — на выбранный срок с первого входа;
  • заявка — случайный человек пишет /start, оставляет имя и компанию, админ жмёт ✅ 3 / 7 / 14 дней или ❌.
Срок кончился или админ нажал «🚫 Отключить» — бот больше не пускает, копия фирмы гостя стирается.
Всё хранится в базе «офиса» демо (app.office()): guests {id: {...}}, invites {код: {...}}.
"""
import secrets
from datetime import date, timedelta

from core.ui.kit import Button, Reply, rows
from core.ui.registry import screen
from core.ui.views import Card, View

DAYS = (3, 7, 14)
ST = {"on": "✅ смотрит", "wait": "🙋 ждёт ответа", "off": "🚫 отключён", "end": "⏳ срок кончился"}


def days_t(n):
    n = int(n)
    return f"{n} " + ("день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней")


def _dm(iso):
    return date.fromisoformat(iso).strftime("%d.%m") if iso else "—"


def _guests(ctx):
    return ctx.office.get("demo_guests") or {}


def _invites(ctx):
    return ctx.office.get("demo_invites") or {}


def _put(ctx, guests=None, invites=None):
    if guests is not None:
        ctx.office.put("demo_guests", guests)
    if invites is not None:
        ctx.office.put("demo_invites", invites)


def _is_admin(ctx):
    return ctx.user_id in ctx.app.admins


def has_access(app, office, user_id, today):
    """Есть ли у зрителя доступ (своя копия фирмы заводится только тогда)."""
    if not app.demo_gate or user_id in app.admins:
        return True
    g = (office.get("demo_guests") or {}).get(str(user_id)) or {}
    return g.get("status") == "on" and g.get("till", "") >= today.isoformat()


def _open(ctx, g, days):
    g.update(status="on", till=(ctx.today + timedelta(days=int(days))).isoformat())


def _notify_admins(ctx, text, buttons=None):
    return [Reply(text=text, to=a, buttons=buttons or []) for a in ctx.app.admins]


# ======================= вахтёр =======================

def gate(ctx, arg="", press=None, text=None):
    """None — пустить дальше; иначе — что ответить человеку без доступа."""
    if not ctx.app.demo_gate or _is_admin(ctx):
        return None
    guests = _guests(ctx)
    uid = str(ctx.user_id)
    me = guests.get(uid)
    today = ctx.today.isoformat()
    name = ctx.session.get("tgname", "")
    inv = _invites(ctx).get(arg) if arg else None
    if inv and not inv.get("used_by"):                       # пришёл по приглашению
        invites = _invites(ctx)
        invites[arg]["used_by"] = ctx.user_id
        me = me or {}
        me.update(name=me.get("name") or name, note=inv.get("note", ""), since=me.get("since") or today, via="ссылка",
                  presses=me.get("presses", 0))
        _open(ctx, me, inv["days"])
        guests[uid] = me
        _put(ctx, guests, invites)
        ctx.save()
        return None
    if me and me.get("status") == "on":
        if me.get("till", "") >= today:
            me["last"], me["presses"] = today, me.get("presses", 0) + 1
            guests[uid] = me
            _put(ctx, guests)
            return None
        me["status"] = "end"                                 # срок кончился
        guests[uid] = me
        _put(ctx, guests)
        ctx.app.drop_guest(ctx.user_id)
    # дальше — человек без доступа
    wait = ctx.session.get("await") or {}
    if text is not None and wait.get("kind") == "demo_req":
        return _request(ctx, guests, text)
    if press == "dmq":
        return _ask(ctx, "Напишите в ответ, как вас зовут и из какой вы компании — администратор откроет доступ.")
    st = (me or {}).get("status")
    if st == "wait":
        return [Reply(text="⏳ Ваша заявка у администратора. Как только откроет доступ — придёт сообщение сюда.")]
    if st in ("off", "end"):
        why = f"Доступ к демо закончился {_dm(me.get('till'))}." if st == "end" else "Доступ к демо закрыт."
        return [Reply(text=f"{why}\n\nХотите посмотреть ещё — попросите снова.",
                      buttons=[[Button("🙋 Попросить доступ", "dmq")]])]
    return _ask(ctx, "👋 Здравствуйте! Это демо KPI и зарплаты для оптовых компаний.\n\n"
                     "Демо открывается по приглашению. Напишите в ответ, как вас зовут и из какой вы компании — "
                     "администратор откроет доступ.")


def _ask(ctx, prompt):
    ctx.session["await"] = {"kind": "demo_req", "data": {}}
    ctx.save()
    return [Reply(text=prompt, ask=prompt)]


def _request(ctx, guests, text):
    text = " ".join((text or "").split())[:200]
    if len(text) < 3:
        return _ask(ctx, "Напишите имя и компанию, например: «Ахмед, Стройбаза Грозный».")
    uid = str(ctx.user_id)
    me = guests.get(uid) or {"since": ctx.today.isoformat(), "presses": 0}
    me.update(status="wait", note=text, name=ctx.session.get("tgname") or me.get("name", ""), via="заявка",
              asked=ctx.today.isoformat())
    guests[uid] = me
    _put(ctx, guests)
    ctx.session.pop("await", None)
    ctx.save()
    out = [Reply(text="✅ Заявка отправлена. Как только администратор откроет доступ — придёт сообщение сюда.")]
    out += _notify_admins(ctx, f"🙋 Заявка в демо\n{text}" + (f"\nИмя в мессенджере: {me['name']}" if me.get("name") else ""),
                          [[Button(f"✅ {d} дн.", f"gsa:{uid}:{d}") for d in DAYS], [Button("❌ Отказать", f"gsn:{uid}")]])
    return out


# ======================= экраны админа =======================

def _deny(ctx):
    return None if _is_admin(ctx) and ctx.demo else Reply(text="Это экран администратора демо.")


def _sweep(ctx):
    """Отключить тех, у кого кончился срок, и стереть их копии."""
    guests, today, changed = _guests(ctx), ctx.today.isoformat(), False
    for uid, g in guests.items():
        if g.get("status") == "on" and g.get("till", "") < today:
            g["status"], changed = "end", True
            ctx.app.drop_guest(int(uid))
    if changed:
        _put(ctx, guests)
    return guests


def _title(g, uid):
    return g.get("note") or g.get("name") or f"id {uid}"


@screen("gst")
def guests_list(ctx):
    no = _deny(ctx)
    if no:
        return no
    guests = _sweep(ctx)
    order = {"wait": 0, "on": 1, "end": 2, "off": 3}
    items = sorted(guests.items(), key=lambda t: (order.get(t[1].get("status"), 9), t[1].get("till", "")))
    v = View("👥 Гости демо", f"смотрят: {sum(g.get('status') == 'on' for g in guests.values())} · "
                             f"ждут: {sum(g.get('status') == 'wait' for g in guests.values())}")
    cards = []
    for uid, g in items[:15]:
        st = g.get("status")
        lines = [f"до {_dm(g.get('till'))}" if st == "on" else f"заявка {_dm(g.get('asked'))}" if st == "wait" else
                 f"доступ был до {_dm(g.get('till'))}"]
        if g.get("last"):
            lines.append(f"последний раз {_dm(g['last'])} · нажатий {g.get('presses', 0)}")
        lines.append(f"вход: {g.get('via', '—')}")
        cards.append(Card(_title(g, uid), ST.get(st, ""), lines,
                          color="good" if st == "on" else "warn" if st == "wait" else None))
    v.cards(cards)
    if not guests:
        v.note("Гостей пока нет. Нажмите «➕ Пригласить» — бот даст ссылку для клиента.")
    free = [c for c, i in _invites(ctx).items() if not i.get("used_by")]
    if free:
        v.note(f"Неиспользованных приглашений: {len(free)}.")
    kb = rows([Button(f"{ST.get(g.get('status'), '')[:1]} {_title(g, uid)[:22]}", f"gso:{uid}") for uid, g in items[:15]], 2)
    kb.append([Button("➕ Пригласить", "gsi")])
    return Reply(view=v, buttons=kb)


@screen("gsi")
def invite(ctx, days=None):
    no = _deny(ctx)
    if no:
        return no
    if not days:
        return Reply(text="На сколько дней открыть демо по приглашению? Срок считается с первого входа.",
                     buttons=[[Button(days_t(d), f"gsi:{d}") for d in DAYS]])
    code = "d" + secrets.token_hex(5)
    invites = _invites(ctx)
    invites[code] = {"days": int(days), "created": ctx.today.isoformat()}
    _put(ctx, invites=invites)
    if ctx.app.web_url:
        link = f"{ctx.app.web_url}/?start={code}"
    elif ctx.app.bot_username:
        link = f"https://t.me/{ctx.app.bot_username}?start={code}"
    else:
        link = f"/start {code}"
    return Reply(text=f"🎟 Приглашение на {days_t(days)}. Отправьте клиенту эту ссылку:\n\n{link}\n\n"
                      "Ссылка одноразовая: по ней войдёт один человек. Кто вошёл — увидите в «👥 Гости демо».",
                 buttons=[[Button("👥 Гости демо", "gst")]])


@screen("gso")
def guest_card(ctx, uid):
    no = _deny(ctx)
    if no:
        return no
    g = _sweep(ctx).get(uid)
    if not g:
        return guests_list(ctx)
    st = g.get("status")
    v = View(_title(g, uid), ST.get(st, ""))
    lines = [f"Имя в мессенджере: {g.get('name') or '—'}", f"Вошёл: {g.get('via', '—')}, с {_dm(g.get('since'))}",
             f"Доступ до: {_dm(g.get('till'))}" if g.get("till") else "Доступа ещё не было",
             f"Последний раз: {_dm(g.get('last'))} · нажатий {g.get('presses', 0)}"]
    v.lines(lines)
    if st == "on":
        kb = [[Button(f"➕ {d} дн.", f"gsx:{uid}:{d}") for d in DAYS], [Button("🚫 Отключить", f"gsoff:{uid}")]]
    elif st == "wait":
        kb = [[Button(f"✅ {d} дн.", f"gsa:{uid}:{d}") for d in DAYS], [Button("❌ Отказать", f"gsn:{uid}")]]
    else:
        kb = [[Button(f"✅ Включить на {d} дн.", f"gsa:{uid}:{d}") for d in DAYS]]
    kb.append([Button("👥 Все гости", "gst")])
    return Reply(view=v, buttons=kb)


def _change(ctx, uid, fn):
    guests = _guests(ctx)
    g = guests.get(uid)
    if not g:
        return None
    msg = fn(g)
    guests[uid] = g
    _put(ctx, guests)
    return msg


@screen("gsa")
def approve(ctx, uid, days="7"):
    no = _deny(ctx)
    if no:
        return no

    def fn(g):
        _open(ctx, g, days)
        return Reply(text=f"✅ Доступ к демо открыт до {_dm(g['till'])}. Нажимайте и смотрите — всё выдумано, "
                          "ничего не сломаете.", to=int(uid), buttons=[[Button("🏠 Открыть демо", "home")]])
    msg = _change(ctx, uid, fn)
    return ([msg] if msg else []) + [guest_card(ctx, uid)]


@screen("gsx")
def extend(ctx, uid, days="7"):
    no = _deny(ctx)
    if no:
        return no

    def fn(g):
        start = max(date.fromisoformat(g.get("till") or ctx.today.isoformat()), ctx.today)
        g.update(status="on", till=(start + timedelta(days=int(days))).isoformat())
        return Reply(text=f"➕ Доступ к демо продлён до {_dm(g['till'])}.", to=int(uid))
    msg = _change(ctx, uid, fn)
    return ([msg] if msg else []) + [guest_card(ctx, uid)]


@screen("gsn")
def refuse(ctx, uid):
    no = _deny(ctx)
    if no:
        return no

    def fn(g):
        g["status"] = "off"
        return Reply(text="Спасибо за интерес! Сейчас открыть демо не получится — администратор свяжется с вами.",
                     to=int(uid))
    msg = _change(ctx, uid, fn)
    return ([msg] if msg else []) + [guests_list(ctx)]


@screen("gsoff")
def switch_off(ctx, uid):
    no = _deny(ctx)
    if no:
        return no
    _change(ctx, uid, lambda g: g.update(status="off"))
    ctx.app.drop_guest(int(uid))
    return guest_card(ctx, uid)
