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
from core.ui.registry import screen, text_input
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
        if inv.get("partner"):
            me.update(partner=True, status="on", till=FOREVER, via="партнёрская ссылка")
        else:
            _open(ctx, me, inv["days"])
            if inv.get("by"):
                me.update(by=inv["by"], via="приглашение партнёра")
        guests[uid] = me
        _put(ctx, guests, invites)
        ctx.save()
        if inv.get("by"):
            p = guests.get(str(inv["by"])) or {}
            if p.get("partner"):
                ctx.app.pending = [Reply(text=f"🎉 Ваш клиент «{me.get('note') or me.get('name') or 'клиент'}» открыл демо.",
                                         to=int(inv["by"]), buttons=[[Button("🤝 Мои клиенты", "ptn")]])]
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
    guests = {u: g for u, g in _sweep(ctx).items() if not g.get("partner")}
    names = {u: _title(g, u) for u, g in _guests(ctx).items() if g.get("partner")}
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
        lines.append(f"вход: {g.get('via', '—')}" + (f" · партнёр {names.get(str(g['by']), '?')}" if g.get("by") else ""))
        cards.append(Card(_title(g, uid), ST.get(st, ""), lines,
                          color="good" if st == "on" else "warn" if st == "wait" else None))
    v.cards(cards)
    if not guests:
        v.note("Гостей пока нет. Нажмите «🎟 Пригласить клиента» — бот даст ссылку.")
    free = [c for c, i in _invites(ctx).items() if not i.get("used_by")]
    if free:
        v.note(f"Неиспользованных приглашений: {len(free)}.")
    kb = rows([Button(f"{ST.get(g.get('status'), '')[:1]} {_title(g, uid)[:22]}", f"gso:{uid}") for uid, g in items[:15]], 2)
    kb.append([Button("🎟 Пригласить клиента", "gsi"), Button("‹ Пульт", "adm")])
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
    if g.get("partner"):
        guests = _guests(ctx)
        cl = _clients_of(guests, uid)
        lines = [f"Партнёр · вошёл {_dm(g.get('since'))}", f"Клиентов привёл: {len(cl)}",
                 f"Заявок «Хочу так»: {len(_reqs_of(ctx, {uid, *cl}))}",
                 f"Последний раз: {_dm(g.get('last'))} · нажатий {g.get('presses', 0)}"] + \
                [f"• {_title(c, u)} — {ST.get(c.get('status'), '')}" for u, c in list(cl.items())[:8]]
        v.lines(lines)
        kb = [[Button("🚫 Отключить партнёра", f"gsoff:{uid}")]] if st == "on" else [[Button("✅ Включить снова", f"gsa:{uid}:7")]]
        kb.append([Button("🤝 Партнёры", "ptl")])
        return Reply(view=v, buttons=kb)
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
        if g.get("partner"):
            g.update(status="on", till=FOREVER)
            return Reply(text="✅ Доступ партнёра снова открыт.", to=int(uid), buttons=[[Button("🏠 Открыть", "home")]])
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


# ======================= партнёры =======================
# Партнёр — человек, который показывает демо клиентам со своего телефона (решение Адама 30.10).
# Входит по партнёрской ссылке (админ: «🤝 Добавить партнёра»), доступ без срока, пока админ не отключит.
# Может: всё демо, «Начать заново», конструктор с ценами и КП, свои приглашения клиентам (клиент закрепляется за ним),
# заявки «Хочу так» от своих клиентов. Не может: менять цены, видеть чужих гостей, админку.
# В гостях: {"partner": True, ...}; у клиента партнёра — {"by": "<id партнёра>"}.

FOREVER = "2999-12-31"


def partner_of(office, user_id):
    """Запись партнёра, если этот зритель — действующий партнёр, иначе None."""
    g = (office.get("demo_guests") or {}).get(str(user_id)) or {}
    return g if g.get("partner") and g.get("status") == "on" else None


def is_partner(ctx):
    return bool(ctx.demo and ctx.app.demo_gate and partner_of(ctx.office, ctx.user_id))


def _link(ctx, code):
    if ctx.app.web_url:
        return f"{ctx.app.web_url}/?start={code}"
    if ctx.app.bot_username:
        return f"https://t.me/{ctx.app.bot_username}?start={code}"
    return f"/start {code}"


def _clients_of(guests, pid):
    return {u: g for u, g in guests.items() if str(g.get("by", "")) == str(pid)}


def _reqs_of(ctx, uids):
    return [r for r in ctx.office.get("tariff_req") or [] if str(r.get("uid")) in uids or str(r.get("partner", "")) in uids]


# ---------- админ ----------

@screen("ptl")
def partners(ctx):
    no = _deny(ctx)
    if no:
        return no
    guests = _guests(ctx)
    ps = [(u, g) for u, g in guests.items() if g.get("partner")]
    v = View("🤝 Партнёры", f"работают: {sum(g.get('status') == 'on' for _, g in ps)} · всего {len(ps)}")
    cards = []
    for u, g in ps:
        cl = _clients_of(guests, u)
        rq = _reqs_of(ctx, {u, *cl})
        cards.append(Card(_title(g, u), "✅ работает" if g.get("status") == "on" else "🚫 отключён",
                          [f"клиентов привёл: {len(cl)} · смотрят сейчас: {sum(c.get('status') == 'on' for c in cl.values())}",
                           f"заявок «Хочу так»: {len(rq)}",
                           f"последний раз {_dm(g.get('last'))} · нажатий {g.get('presses', 0)}"],
                          color="good" if g.get("status") == "on" else None))
    v.cards(cards)
    if not ps:
        v.note("Партнёров пока нет. «🤝 Добавить партнёра» — бот даст ссылку, отправьте её партнёру.")
    kb = rows([Button(_title(g, u)[:24], f"gso:{u}") for u, g in ps], 2)
    kb.append([Button("🤝 Добавить партнёра", "pta"), Button("📘 Инструкция партнёра", "ptm")])
    kb.append([Button("‹ Пульт", "adm")])
    return Reply(view=v, buttons=kb)


@screen("pta")
def partner_add(ctx):
    no = _deny(ctx)
    if no:
        return no
    ctx.session["await"] = {"kind": "partner_name", "data": {}}
    ctx.save()
    prompt = "Как зовут партнёра и откуда он? Например: «Ислам, Грозный»."
    return Reply(text=prompt, ask=prompt, buttons=[[Button("✖️ Отмена", "ptl")]])


@text_input("partner_name")
def on_partner_name(ctx, text, data):
    if not _is_admin(ctx):
        return Reply(text="Это экран администратора демо.")
    name = " ".join((text or "").split())[:60]
    if len(name) < 2:
        return partner_add(ctx)
    code = "p" + secrets.token_hex(6)
    invites = _invites(ctx)
    invites[code] = {"partner": True, "note": name, "created": ctx.today.isoformat()}
    _put(ctx, invites=invites)
    return Reply(text=f"🤝 Партнёр «{name}». Отправьте ему эту ссылку:\n\n{_link(ctx, code)}\n\n"
                      "Ссылка одноразовая, доступ — без срока, пока вы не отключите. Внутри у партнёра «📘 Инструкция» — "
                      "что это за продукт и как его показывать.", buttons=[[Button("🤝 Партнёры", "ptl")]])


# ---------- партнёр ----------

def _deny_partner(ctx):
    return None if is_partner(ctx) else Reply(text="Это экран партнёра.")


@screen("ptn")
def my_clients(ctx):
    no = _deny_partner(ctx)
    if no:
        return no
    guests = _sweep(ctx)
    me = str(ctx.user_id)
    cl = sorted(_clients_of(guests, me).items(), key=lambda t: (t[1].get("status") != "on", t[1].get("till", "")))
    rq = _reqs_of(ctx, {me, *dict(cl)})
    v = View("🤝 Мои клиенты", f"привели: {len(cl)} · смотрят сейчас: {sum(g.get('status') == 'on' for _, g in cl)} · "
                              f"заявок: {len(rq)}")
    v.cards([Card(_title(g, u), ST.get(g.get("status"), ""),
                  [f"до {_dm(g.get('till'))}" if g.get("status") == "on" else f"доступ был до {_dm(g.get('till'))}",
                   f"последний раз {_dm(g.get('last'))} · нажатий {g.get('presses', 0)}"],
                  color="good" if g.get("status") == "on" else None) for u, g in cl[:15]])
    if rq:
        v.lines([f"{r['ts']} · {r['who']} · {r['set']} · {r['monthly']:,} ₽/мес".replace(",", " ") for r in rq[-5:]],
                title="Заявки «Хочу так»")
    if not cl:
        v.note("Клиентов пока нет. «➕ Пригласить клиента» — бот даст ссылку: клиент сам посмотрит демо у себя в телефоне.")
    from core import onboard as O
    my_cards = O.by_partner(ctx.office, me)
    if my_cards:
        v.lines([f"{'●' if O.progress(c)[0] == O.progress(c)[1] else '○'} {O._title(c)} — заполнено {O.progress(c)[0]} из {O.progress(c)[1]}"
                 + (f" · ⚠️ {len(O.flags(c))}" if O.flags(c) else "") for c in my_cards[:10]], title="Анкеты клиентов")
    kb = [[Button("➕ Пригласить клиента", "pti")]]
    kb += [[Button(f"➕ 7 дн. · {_title(g, u)[:18]}", f"ptx:{u}"), Button("🚫", f"pto:{u}")]
           for u, g in cl[:8] if g.get("status") in ("on", "end")]
    kb += rows([Button(f"📋 {O._title(c)[:22]}", f"cd:{c['uid']}") for c in my_cards[:10]], 2)
    kb.append([Button("📘 Инструкция", "ptm")])
    return Reply(view=v, buttons=kb)


@screen("pti")
def partner_invite(ctx, days=None):
    no = _deny_partner(ctx)
    if no:
        return no
    if not days:
        ctx.session["await"] = {"kind": "partner_client", "data": {}}
        ctx.save()
        prompt = "Кому приглашение? Имя, компания и телефон клиента — например: «Ахмед, Стройбаза, 8 928 000 00 00»."
        return Reply(text=prompt, ask=prompt, buttons=[[Button("✖️ Отмена", "ptn")]])
    return my_clients(ctx)


@text_input("partner_client")
def on_partner_client(ctx, text, data):
    no = _deny_partner(ctx)
    if no:
        return no
    note = " ".join((text or "").split())[:120]
    if len(note) < 3:
        return partner_invite(ctx)
    return Reply(text=f"На сколько дней открыть демо для «{note}»? Срок — с первого входа.",
                 buttons=[[Button(days_t(d), f"ptk:{d}:{_stash(ctx, note)}") for d in DAYS], [Button("✖️ Отмена", "ptn")]])


def _stash(ctx, note):
    """Имя клиента до выбора срока — в сессии партнёра (в кнопку не влезет)."""
    key = secrets.token_hex(3)
    ctx.session.setdefault("pt_notes", {})[key] = note
    ctx.save()
    return key


@screen("ptk")
def partner_invite_make(ctx, days, key):
    no = _deny_partner(ctx)
    if no:
        return no
    note = (ctx.session.get("pt_notes") or {}).pop(key, "")
    ctx.save()
    code = "d" + secrets.token_hex(5)
    invites = _invites(ctx)
    me = partner_of(ctx.office, ctx.user_id)
    invites[code] = {"days": int(days), "created": ctx.today.isoformat(), "by": str(ctx.user_id), "note": note}
    _put(ctx, invites=invites)
    out = [Reply(text=f"🎟 Приглашение для «{note or 'клиента'}» на {days_t(days)}. Отправьте ему ссылку:\n\n{_link(ctx, code)}\n\n"
                      "Ссылка одноразовая. Когда клиент войдёт — он появится в «🤝 Мои клиенты».",
                 buttons=[[Button("🤝 Мои клиенты", "ptn")]])]
    out += _notify_admins(ctx, f"🤝 Партнёр {_title(me, ctx.user_id)} пригласил клиента: {note or '—'} ({days_t(days)})")
    return out


def _own(ctx, uid):
    g = _guests(ctx).get(uid)
    return g if g and str(g.get("by", "")) == str(ctx.user_id) else None


@screen("ptx")
def partner_extend(ctx, uid):
    no = _deny_partner(ctx)
    if no or not _own(ctx, uid):
        return no or my_clients(ctx)

    def fn(g):
        start = max(date.fromisoformat(g.get("till") or ctx.today.isoformat()), ctx.today)
        g.update(status="on", till=(start + timedelta(days=7)).isoformat())
        return Reply(text=f"➕ Доступ к демо продлён до {_dm(g['till'])}.", to=int(uid))
    msg = _change(ctx, uid, fn)
    return ([msg] if msg else []) + [my_clients(ctx)]


@screen("pto")
def partner_off(ctx, uid):
    no = _deny_partner(ctx)
    if no or not _own(ctx, uid):
        return no or my_clients(ctx)
    _change(ctx, uid, lambda g: g.update(status="off"))
    ctx.app.drop_guest(int(uid))
    return my_clients(ctx)


# ---------- памятка ----------

MEMO = [
    ("Что у вас в приложении", [
        "Приложение называется «Алапа» — по-чеченски «зарплата», ударение на первое «а». Так и представляйте: «Алапа — "
        "зарплата и показатели торговых из вашей 1С, у каждого в телефоне».",
        "Вы вошли как партнёр: доступ без срока. Внизу — «🎭 Роль», «🏭 Отрасль», «🧩 Собрать» для показа; в шапке кнопка "
        "«🤝 Партнёр» — ваши клиенты и эта инструкция.",
        "Всё, что вы нажимаете в демо, меняет только вашу копию фирмы. «🔄 Заново» — свежие цифры перед каждым показом.",
    ]),
    ("Показ клиенту за 10–15 минут", [
        "1. «🔄 Заново» → «🏭 Отрасль» — выберите отрасль клиента.",
        "2. «🎭 Роль → Торговый»: «Мой план» и «Зарплата» — торговый видит свои деньги сегодня. Это главное «вау».",
        "3. «🎭 Роль → Директор»: главная, отделы, «Фонд», «Утвердить».",
        "4. Если у клиента склад или водители — роли «Кладовщик», «Водитель», «Начальник закупок».",
        "5. «🧩 Собрать»: включите нужные пакеты, «👥 Люди» — впишите его людей, «💵 Как платите торговым» — отметьте, "
        "как у него устроена зарплата. На экране две цифры: подключение и в месяц.",
        "6. «📄 Коммерческое предложение» — PDF откроется прямо в приложении: «📤 Поделиться» → WhatsApp / Telegram клиенту, "
        "«✕ Закрыть» — назад к показу.",
    ]),
    ("Клиенту понравилось — что нажимать", [
        "«📨 Хочу так» прямо при клиенте → бот спросит, для кого заявка (имя, компания, телефон) → заявка уйдёт "
        "администратору, а у вас появится «📋 Анкета» этого клиента.",
        "Заполните анкету вместе с клиентом тут же: «✏️ Компания», «✏️ Контакты», «✏️ 1С и отчёты», «✏️ Особенности», «✏️ Старт». "
        "Каждый пункт — одна кнопка или одна строка. Обязательные отмечены ○.",
        "В анкете сразу видно «⚠️ Нестандарт» — то, что потребует настройки. Это не отказ, а подсказка нам.",
    ]),
    ("Клиент хочет посмотреть сам", [
        "«🤝 Партнёр → ➕ Пригласить клиента»: имя, компания, телефон → срок 3 / 7 / 14 дней → ссылка. Отправьте её клиенту.",
        "Клиент откроет ссылку и поставит приложение. У него своя копия демо; внизу главной у него «📋 Анкета для подключения» — "
        "он может заполнить сам.",
        "В «🤝 Мои клиенты» видно: вошёл ли, когда был последний раз, сколько нажимал, заполнил ли анкету. "
        "Там же продлить доступ на 7 дней или отключить.",
        "Через 2–3 дня позвоните: «посмотрели? какие вопросы?». Если клиент нажал «Хочу так» — вам придёт сообщение.",
    ]),
    ("Что отвечать про подключение", [
        "«Как это будет у нас?» — своё приложение фирмы со своим адресом (например stroybaza.kpibotsmith.ru), своя отдельная база.",
        "«Как зайдут сотрудники?» — директор или администратор фирмы добавляет человека и отправляет ему ссылку-приглашение; "
        "тот открывает её на своём телефоне и видит только своё. Уволился — доступ закрывают одной кнопкой.",
        "«Откуда цифры?» — из их 1С. Проще всего: на компьютере с 1С подключают сетевую папку, как Google Диск, и 1С сама по "
        "расписанию сохраняет туда отчёты. Программист не обязателен; если он есть — может настроить прямую отправку из 1С.",
        "«Напомнит ли?» — да, уведомления на телефон: утренняя сводка, маршрут, вечернее напоминание, что ждёт утверждения. "
        "На iPhone — когда приложение добавлено на экран «Домой».",
        "Сроки подключения не обещайте сами: зависят от 1С клиента — скажите, что администратор свяжется после анкеты.",
    ]),
    ("Что записать в анкету обязательно", [
        "Название компании и город; кто решает и телефон; какая 1С и есть ли программист; когда готовы начать.",
        "Люди по отделам и пакеты берутся из конструктора сами — просто соберите, как нужно клиенту.",
        "Остальное (сайт, кто будет вести бот, какие отчёты уже есть, особенности) — очень желательно: подключение пойдёт быстрее.",
    ]),
    ("Чего не делать", [
        "Не обещайте скидки, сроки и функции, которых нет в демо, — сначала спросите администратора.",
        "Не говорите, что цифры в демо настоящие: все люди и суммы выдуманы.",
        "Не пересылайте свою партнёрскую ссылку — она только для вас. Клиентам — только «➕ Пригласить клиента».",
    ]),
]


def memo_view():
    v = View("📘 Инструкция партнёра", "что нажимать в приложении и в каком порядке")
    for title, lines in MEMO:
        v.lines(lines, title=title)
    return v


@screen("ptm")
def memo(ctx):
    if not (is_partner(ctx) or _is_admin(ctx)):
        return Reply(text="Инструкция — для партнёров.")
    return Reply(view=memo_view(), buttons=[[Button("📄 Инструкция PDF", "ptp")]] +
                 ([[Button("🤝 Мои клиенты", "ptn")]] if is_partner(ctx) else [[Button("‹ Пульт", "adm")]] if _is_admin(ctx) else []))


@screen("ptp")
def memo_pdf(ctx):
    if not (is_partner(ctx) or _is_admin(ctx)):
        return Reply(text="Инструкция — для партнёров.")
    from core.ui.views import to_pdf
    return Reply(text="📄 Инструкция партнёра", files=[("Инструкция партнёра.pdf", to_pdf([memo_view()]))])


# ======================= пульт админа демо =======================
# Всё «для нас» — в одном месте, отдельно от кнопок показа (роль, отрасль, собрать): гости, партнёры, заявки, цены, памятка.

@screen("adm")
def panel(ctx):
    no = _deny(ctx)
    if no:
        return no
    guests = _sweep(ctx)
    gs = [g for g in guests.values() if not g.get("partner")]
    ps = [g for g in guests.values() if g.get("partner")]
    reqs = ctx.office.get("tariff_req") or []
    v = View("🛠 Пульт", "только для вас: клиенты в демо, партнёры, заявки, цены")
    v.tiles([("Смотрят демо", str(sum(g.get("status") == "on" for g in gs)), None),
             ("Ждут ответа", str(sum(g.get("status") == "wait" for g in gs)), "warn" if any(g.get("status") == "wait" for g in gs) else None),
             ("Партнёров", str(sum(g.get("status") == "on" for g in ps)), None),
             ("Заявок «Хочу так»", str(len(reqs)), "good" if reqs else None)])
    v.lines(["🎟 Пригласить клиента — ссылка на демо на 3, 7 или 14 дней.",
             "🤝 Добавить партнёра — ссылка без срока: партнёр показывает демо и приглашает своих клиентов.",
             "👥 Гости — кто смотрит демо, до какого числа, сколько нажимал.",
             "📨 Заявки — кто нажал «Хочу так», какой набор и сколько стоит.",
             "📋 Анкеты — всё, что нужно для подключения каждого клиента; ⚠️ — где нестандарт; «📥 Анкета Excel» — для подключения.",
             "💰 Цены — меняете здесь, действуют сразу у всех."])
    from core import onboard as O
    n_cards = len(O.cards(ctx.office))
    return Reply(view=v, buttons=[[Button("🎟 Пригласить клиента", "gsi"), Button("🤝 Добавить партнёра", "pta")],
                                  [Button("👥 Гости", "gst"), Button(f"🤝 Партнёры ({len(ps)})", "ptl")],
                                  [Button(f"📨 Заявки ({len(reqs)})", "rqs"), Button(f"📋 Анкеты ({n_cards})", "cds")],
                                  [Button("💰 Цены", "pr"), Button("📘 Моя инструкция", "adi")],
                                  [Button("📘 Инструкция партнёра", "ptm")]])


@screen("rqs")
def requests_list(ctx):
    no = _deny(ctx)
    if no:
        return no
    reqs = list(reversed(ctx.office.get("tariff_req") or []))
    v = View("📨 Заявки «Хочу так»", f"всего {len(reqs)}")
    v.cards([Card(r.get("who", "—"), f"{r.get('monthly', 0):,} ₽/мес".replace(",", " "),
                  [r.get("set", ""), f"подключение {r.get('setup', 0):,} ₽".replace(",", " "), r.get("ts", "")])
             for r in reqs[:20]])
    if not reqs:
        v.note("Заявок пока нет. Они появляются, когда клиент или партнёр жмёт «📨 Хочу так» в «🧩 Собрать».")
    return Reply(view=v, buttons=[[Button("‹ Пульт", "adm")]])


# ---------- инструкция администратора ----------

ADMIN_MEMO = [
    ("Как всё устроено", [
        "demo.kpibotsmith.ru — одно демо-приложение для всех: каждый вошедший получает свою копию выдуманной фирмы, цифры общие "
        "только у цен. Внизу у всех — показ: «🎭 Роль», «🏭 Отрасль», «🧩 Собрать». У вас в шапке — «🛠 Пульт», у партнёра — «🤝 Партнёр».",
        "Три вида людей в демо: гость (клиент, доступ на 3 / 7 / 14 дней), партнёр (без срока, приглашает своих клиентов), вы.",
    ]),
    ("Клиент напрямую", [
        "1. «🛠 Пульт → 🎟 Пригласить клиента» → срок → ссылка клиенту. Или клиент сам пишет в приложение — заявка придёт вам: "
        "✅ 3 / 7 / 14 дней или ❌.",
        "2. Клиент смотрит демо, собирает набор в «🧩 Собрать», жмёт «📨 Хочу так» — вам приходит заявка с суммами и кнопкой «📋 Анкета клиента».",
        "3. Анкету заполняет клиент сам («📋 Анкета для подключения» внизу главной) или вы за него из «📋 Анкеты».",
    ]),
    ("Клиент через партнёра", [
        "1. «🛠 Пульт → 🤝 Добавить партнёра» → имя → ссылка партнёру. Он открывает её на своём телефоне — и он партнёр без срока.",
        "2. Партнёр показывает демо сам или шлёт клиенту приглашение из «🤝 Партнёр → ➕ Пригласить клиента». Такой клиент закреплён за партнёром.",
        "3. «Хочу так» от партнёра или его клиента приходит вам с пометкой, чей клиент. Анкету партнёр заполняет сам вместе с клиентом; вы её видите.",
        "4. В «🤝 Партнёры» видно по каждому: клиентов, заявок, когда заходил. Отключить и включить снова — одной кнопкой.",
    ]),
    ("Анкета → подключение", [
        "«📋 Анкеты»: заполнено X из Y обязательных, ⚠️ нестандарт — то, что потребует настройки или доработки (другая 1С, филиалы, "
        "зарплата по-своему).",
        "Когда обязательное заполнено: откройте анкету → «📥 Анкета Excel». Лист «Анкета клиента» — ответы; остальные листы — заготовка "
        "таблицы-анкеты для подключения с данными из конструктора (цифры схем стандартные — уточните с клиентом).",
        "Дальше, как обычно: правите Excel → tools/anketa.py check → apply → папка клиента, переводчик отчётов, его приложение.",
    ]),
    ("Цены и условия", [
        "«💰 Цены» — пакеты, за человека, подключение (× месяца и минимум), скидки, пробный период. Меняете — действует сразу у всех: "
        "в конструкторе, замках, тарифе, КП.",
        "Партнёр цены видит, менять не может. Гость в роли «Администратор» видит только демо-экраны, Пульт ему не показывается.",
    ]),
    ("Если что-то пошло не так", [
        "Клиент «не видит» экраны — проверьте в «👥 Гости», не кончился ли срок; «➕ 7 дн.» продлит.",
        "Партнёр вошёл как гость (есть срок) — дайте ему партнёрскую ссылку, пусть откроет на том же телефоне.",
        "«🐞 Не работает» — сообщения от людей приходят в «💬 Отзывы».",
    ]),
]


def admin_memo_view():
    v = View("📘 Инструкция администратора", "как вести клиентов и партнёров в демо")
    for title, lines in ADMIN_MEMO:
        v.lines(lines, title=title)
    return v


@screen("adi")
def admin_memo(ctx):
    no = _deny(ctx)
    if no:
        return no
    return Reply(view=admin_memo_view(), buttons=[[Button("📄 PDF", "adp")], [Button("‹ Пульт", "adm")]])


@screen("adp")
def admin_memo_pdf(ctx):
    no = _deny(ctx)
    if no:
        return no
    from core.ui.views import to_pdf
    return Reply(text="📄 Инструкция администратора", files=[("Инструкция администратора.pdf", to_pdf([admin_memo_view()]))])
