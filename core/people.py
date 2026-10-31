"""👥 Сотрудники и доступ — настоящий клиент заводит людей и пускает их в приложение (Адам 31.10).

Кто ведёт: администратор (ключ приложения — платформа; или сотрудник с ролью «Администратор» — «завхоз» бота у клиента)
и руководитель — всю фирму; начальник отдела — только свой отдел (без выдачи роли администратора).

Как человек попадает в приложение: ему выдают одноразовую ссылку-приглашение (`…/?start=<код>`, живёт INVITE_DAYS дней).
Открыл на своём телефоне — приложение запомнило устройство за ним (employees.tg_id = id устройства) и дальше показывает
только его роль. Новая ссылка — новое устройство, старое теряет вход. «🚫 Закрыть доступ» — tg_id пустой, ссылки отозваны.
Без приглашения приложение показывает только «вас пока нет в списке» (home для гостя) — запасной путь «напишите фамилию»
остаётся: заявки видны в «📨 Заявки» (`regs`).

Имена из 1С: торговые приходят в отчётах ключом «фамилия имя»; непривязанные — «🧾 Нет в расчёте» (`pak`): нажать имя →
выбрать человека или завести нового этим именем.

Хранение: сотрудники — таблица employees клиента; приглашения — настройка `invites` = {код: {emp, by, ts}}.
В демо приглашения не выдаются (у каждого гостя своя копия фирмы, вход и так есть) — экраны работают, кнопки ссылок скрыты.
"""
import secrets
from datetime import date, timedelta

from core.access import roles as R
from core.ui import pick
from core.ui.kit import Button, Reply, money, ok_row, rows
from core.ui.registry import menu, screen, text_input
from core.ui.views import Row, View
from core.ui.web import BRAND

INVITE_DAYS = 7
KEY = "invites"
MANAGERS = ("admin", "owner", "head")
# Роли, которые можно назначить человеку руками (новичок — отдельной кнопкой у торгового)
ROLE_CHOICES = ["rep", "sv", "head", "owner", "accountant", "staff", "admin"]


# ---------- права ----------

def manages(ctx, emp=None):
    """Может ли зритель вести людей; emp — конкретного человека (начальник — только свой отдел)."""
    if ctx.demo and ctx.role not in MANAGERS:
        return False
    if ctx.admin or ctx.role in ("admin", "owner"):
        return True
    if ctx.role == "head" and ctx.me:
        return emp is None or emp["dept"] == ctx.me["dept"]
    return False


def full(ctx):
    """Полные права: роль «Администратор» выдаёт только администратор или руководитель."""
    return ctx.admin or ctx.role in ("admin", "owner")


def _depts(ctx):
    if full(ctx):
        return list(ctx.cfg.departments)
    return [d for d in ctx.cfg.departments if ctx.me and d.id == ctx.me["dept"]]


def _emp(ctx, emp_id):
    try:
        e = ctx.db.employee(int(emp_id))
    except (TypeError, ValueError):
        return None
    return e if e and manages(ctx, e) else None


def _who(ctx):
    return ctx.name()


def _short(fio):
    p = fio.split()
    return p[0] + (f" {p[1][0]}." if len(p) > 1 else "")


# ---------- приглашения ----------

def invites(db):
    return db.get(KEY) or {}


def invite(ctx, emp):
    """Новая одноразовая ссылка; прежние ссылки этого человека отзываются."""
    inv = {c: i for c, i in invites(ctx.db).items() if i.get("emp") != emp["id"]}
    code = secrets.token_urlsafe(6)
    inv[code] = {"emp": emp["id"], "by": _who(ctx), "ts": ctx.today.isoformat()}
    ctx.db.put(KEY, inv)
    ctx.db.audit(_who(ctx), "invite", f"приглашение {emp['fio']}")
    return code


def revoke(ctx, emp):
    inv = {c: i for c, i in invites(ctx.db).items() if i.get("emp") != emp["id"]}
    ctx.db.put(KEY, inv)
    ctx.db.c.execute("UPDATE employees SET tg_id = NULL WHERE id = ?", (emp["id"],))
    ctx.db.audit(_who(ctx), "revoke", f"доступ закрыт: {emp['fio']}")


def pending(db, emp_id):
    return any(i.get("emp") == emp_id for i in invites(db).values())


def redeem(ctx, code):
    """Человек открыл ссылку: привязать его устройство к сотруднику. → сотрудник или None (нет такой / просрочена)."""
    inv = invites(ctx.db)
    i = inv.get(code)
    if not i:
        return None
    inv.pop(code)
    ctx.db.put(KEY, inv)
    if date.fromisoformat(i["ts"]) + timedelta(days=INVITE_DAYS) < ctx.today:
        return None
    emp = ctx.db.employee(i["emp"])
    if not emp or not emp["active"]:
        return None
    with ctx.db.tx() as c:
        c.execute("UPDATE employees SET tg_id = NULL WHERE tg_id = ?", (ctx.user_id,))   # устройство — только за одним
        c.execute("UPDATE employees SET tg_id = ? WHERE id = ?", (ctx.user_id, emp["id"]))
    ctx.db.audit(emp["fio"], "join", "вошёл по приглашению")
    return emp


def link(ctx, code):
    base = (ctx.app.web_url or "").rstrip("/")
    return f"{base}/?start={code}" if base else f"код приглашения: {code}"


# ---------- экраны ----------

def _status(ctx, e, short=False):
    """Доступ человека: коротко — в таблицу и плитку, длинно — в подписи."""
    if not e["active"]:
        return "⛔ уволен"
    if e["tg_id"]:
        return "🟢 есть" if short else "🟢 в приложении"
    if pending(ctx.db, e["id"]):
        return "🔗 выдана" if short else "🔗 ссылка выдана"
    return "⚪ нет" if short else "⚪ без доступа"


@screen("pa")
def people_access(ctx, dept_id=None, show=None):
    if not manages(ctx):
        return Reply(text="Сотрудников и доступ ведут администратор, руководитель и начальник отдела.")
    depts = _depts(ctx)
    if not dept_id and len(depts) > 1:
        v = View("👥 Сотрудники и доступ", "по отделам")
        rws = []
        for d in depts:
            es = ctx.db.employees(dept=d.id)
            on = sum(1 for e in es if e["tg_id"])
            rws.append(Row([d.name, str(len(es)), f"{on} из {len(es)}"]))
        v.table(["Отдел", "Человек", "В приложении"], rws)
        if ctx.demo:
            v.note("В демо приглашения не выдаются — у вас и так своя копия фирмы. У настоящего клиента здесь ссылки для входа.")
        kb = rows([Button(d.name, f"pa:{d.id}") for d in depts], 2)
        kb.append([Button("➕ Добавить сотрудника", "pad")] + _extra_buttons(ctx))
        return Reply(view=v, buttons=kb)
    d = ctx.cfg.dept(dept_id) if dept_id else depts[0]
    if not d or d.id not in {x.id for x in depts}:
        return people_access(ctx)
    es = ctx.db.employees(dept=d.id, active=None if show == "all" else True)
    v = View(f"👥 {d.name}", f"{len(es)} человек" if show != "all" else f"{len(es)} человек, с уволенными")
    v.table(["Сотрудник", "Должность", "Доступ"],
            [Row([e["fio"], (ctx.cfg.position(e["position"]) or {}).get("name", ctx.cfg.role_title(e["role"])), _status(ctx, e, short=True)])
             for e in es])
    if not es:
        v.note("Пока никого. Нажмите «➕ Добавить сотрудника» — имя, должность, и у него появится своя карточка.")
    elif not ctx.demo:
        v.note("Нажмите фамилию → «🔗 Пригласить» — получите ссылку, отправьте человеку. Открыл на своём телефоне — он в приложении.")
    kb = rows([Button(_short(e["fio"]), f"pae:{e['id']}") for e in es[:40]], 3)
    kb.append([Button("➕ Добавить", f"pad:{d.id}"),
               Button("👀 С уволенными", f"pa:{d.id}:all") if show != "all" else Button("👀 Только работающие", f"pa:{d.id}")])
    tail = _extra_buttons(ctx)
    if len(depts) > 1:
        tail.insert(0, Button("‹ Отделы", "pa"))
    if tail:
        kb.append(tail)
    return Reply(view=v, buttons=kb)


def _extra_buttons(ctx):
    out = []
    if not ctx.demo:
        if ctx.db.unknown_keys():
            out.append(Button("🧾 Нет в расчёте", "pak"))
        if ctx.admin and (ctx.db.get("register") or {}):
            out.append(Button("📨 Заявки", "regs"))
    return out


@screen("pae")
def card(ctx, emp_id):
    e = _emp(ctx, emp_id)
    if not e:
        return people_access(ctx)
    d = ctx.cfg.dept(e["dept"])
    pos = ctx.cfg.position(e["position"]) or {}
    team = next((t for t in ctx.cfg.teams if t.id == e["team"]), None)
    v = View(e["fio"], f"{pos.get('name') or ctx.cfg.role_title(e['role'])} · {d.name if d else e['dept']}")
    v.tiles([("Доступ", _status(ctx, e, short=True), "good" if e["tg_id"] else None)]
            + ([("Команда", team.name, None)] if team else []))
    keys = [r[0] for r in ctx.db.c.execute("SELECT key FROM emp_keys WHERE emp_id = ? ORDER BY since", (e["id"],))]
    v.lines([f"Роль: {ctx.cfg.role_title(e['role'])} · доступ: {_status(ctx, e)}",
             f"Имя в 1С: {', '.join(keys) if keys else 'не привязано — появится, когда придут отчёты'}"]
            + ([f"Принят: {e['hired']}"] if e.get("hired") else []) + ([f"Уволен: {e['fired']}"] if e.get("fired") else []))
    if e["active"] and not e["tg_id"] and not ctx.demo:
        v.note("Человека ещё нет в приложении: «🔗 Пригласить» → отправьте ему ссылку. Ссылка одноразовая, живёт 7 дней.")
    kb = []
    if e["active"] and not ctx.demo:
        kb.append([Button("🔗 Пригласить" if not e["tg_id"] else "🔗 Новая ссылка (другой телефон)", f"pai:{e['id']}")])
    kb.append([Button(f"💰 Оклад: {money(e['salary']) if e.get('salary') is not None else 'как у должности'} — изменить",
                      f"pas:{e['id']}", info="Свой оклад человека. Работает, если в конструкторе зарплаты у оклада стоит "
                                             "«у каждого свой». 0 — вернуть «как у должности».")])
    row = [Button("✏️ Должность", f"pap:{e['id']}")]
    if d and d.kind == "sales" and ctx.cfg.teams_of(d.id):
        row.append(Button("👥 Команда", f"pat:{e['id']}"))
    if full(ctx):
        row.append(Button("🎚 Роль", f"par:{e['id']}"))
    kb.append(row)
    row = []
    if e["tg_id"] and not ctx.demo:
        row.append(Button("🚫 Закрыть доступ", f"pax:{e['id']}"))
    row.append(Button("🗑 Уволить", f"paf:{e['id']}") if e["active"] else Button("↩️ Вернуть на работу", f"paf:{e['id']}:back"))
    kb.append(row)
    kb.append([Button("‹ Сотрудники", f"pa:{e['dept']}"), Button("📊 Показатели", f"emp:{e['id']}")])
    return Reply(view=v, buttons=kb)


@screen("pai")
def invite_screen(ctx, emp_id):
    e = _emp(ctx, emp_id)
    if not e or not e["active"]:
        return people_access(ctx)
    if ctx.demo:
        return Reply(text="В демо ссылки не выдаются — у настоящего клиента здесь появится ссылка для входа сотрудника.",
                     buttons=[[Button("‹ Карточка", f"pae:{e['id']}")]])
    code = invite(ctx, e)
    url = link(ctx, code)
    return Reply(text=f"🔗 Приглашение для {e['fio']} — отправьте ему это сообщение:\n\n"
                      f"«{ctx.cfg.name}: ваш вход в приложение «{BRAND}» — откройте ссылку на своём телефоне и добавьте на экран "
                      f"«Домой»:\n{url}»\n\n"
                      f"Ссылка одноразовая, действует {INVITE_DAYS} дней. Открыл не тот — «🚫 Закрыть доступ» и новая ссылка.",
                 buttons=[[Button("🔁 Другая ссылка", f"pai:{e['id']}")], [Button("‹ Карточка", f"pae:{e['id']}")]])


@screen("pax")
def revoke_screen(ctx, emp_id, sure=None):
    e = _emp(ctx, emp_id)
    if not e:
        return people_access(ctx)
    if sure != "yes":
        return Reply(text=f"Закрыть {e['fio']} вход в приложение? Он перестанет видеть что-либо, пока не получит новую ссылку.",
                     buttons=[[Button("🚫 Да, закрыть", f"pax:{e['id']}:yes")], [Button("‹ Карточка", f"pae:{e['id']}")]])
    revoke(ctx, e)
    return [Reply(text=f"🚫 Доступ закрыт: {e['fio']}."), card(ctx, e["id"])]


@screen("paf")
def fire_screen(ctx, emp_id, what=None):
    e = _emp(ctx, emp_id)
    if not e:
        return people_access(ctx)
    if what == "back":
        ctx.db.c.execute("UPDATE employees SET active = 1, fired = NULL WHERE id = ?", (e["id"],))
        ctx.db.audit(_who(ctx), "rehire", e["fio"])
        return [Reply(text=f"↩️ {e['fio']} снова в списке. Доступ выдайте заново — «🔗 Пригласить»."), card(ctx, e["id"])]
    if what != "yes":
        return Reply(text=f"Уволить {e['fio']}? Он пропадёт из списков и потеряет вход; история продаж и расчётов останется.",
                     buttons=[[Button("🗑 Да, уволить", f"paf:{e['id']}:yes")], [Button("‹ Карточка", f"pae:{e['id']}")]])
    revoke(ctx, e)
    ctx.db.c.execute("UPDATE employees SET active = 0, fired = ? WHERE id = ?", (ctx.today.isoformat(), e["id"]))
    ctx.db.audit(_who(ctx), "fire", e["fio"])
    return [Reply(text=f"🗑 {e['fio']} уволен {ctx.today:%d.%m.%Y}. Вернуть можно в карточке («👀 С уволенными»)."),
            people_access(ctx, e["dept"])]


@screen("pas")
def salary_screen(ctx, emp_id):
    e = _emp(ctx, emp_id)
    if not e or not manages(ctx, e):
        return people_access(ctx)
    cur = money(e["salary"]) if e.get("salary") is not None else "как у должности"
    return ctx.ask("pa_salary", f"Оклад {e['fio']} — сейчас {cur}. Напишите сумму, например 35 000. 0 — как у должности.",
                   {"id": e["id"]}, buttons=[[Button("✖️ Отмена", f"pae:{e['id']}")]])


@text_input("pa_salary")
def on_salary(ctx, text, data):
    e = _emp(ctx, str(data["id"]))
    if not e or not manages(ctx, e):
        return people_access(ctx)
    t = (text or "").replace(" ", "").replace("\xa0", "").replace("₽", "")
    if not t.isdigit() or int(t) > 10_000_000:
        return ctx.ask("pa_salary", "Нужно число, например 35 000. 0 — как у должности.", data,
                       buttons=[[Button("✖️ Отмена", f"pae:{e['id']}")]])
    v = int(t) or None
    ctx.db.c.execute("UPDATE employees SET salary = ? WHERE id = ?", (v, e["id"]))
    ctx.db.audit(_who(ctx), "salary", f"{e['fio']}: {money(v) if v else 'как у должности'}")
    return card(ctx, e["id"])


@screen("pap")
def position_screen(ctx, emp_id, act=None, value=None):
    e = _emp(ctx, emp_id)
    if not e:
        return people_access(ctx)
    poss = [p for p in ctx.cfg.positions if p["dept"] == e["dept"] and (p["role"] != "admin" or full(ctx))]
    if not poss:
        return Reply(text="В этом отделе должности не описаны — роль меняется кнопкой «🎚 Роль».")
    res = pick.one(ctx, f"pap:{e['id']}", act, value, [(p["id"], p["name"]) for p in poss], e["position"],
                   f"✏️ Должность · {e['fio']}", "отметьте и нажмите «ОК»")
    if res.cancel:
        return card(ctx, e["id"])
    if res.done:
        p = ctx.cfg.position(res.value)
        if p and p["dept"] == e["dept"] and p["id"] != e["position"]:
            ctx.db.c.execute("UPDATE employees SET position = ?, role = ? WHERE id = ?", (p["id"], p["role"], e["id"]))
            ctx.db.audit(_who(ctx), "position", f"{e['fio']}: {p['name']}")
            return [Reply(text=f"✏️ {e['fio']} — {p['name']}."), card(ctx, e["id"])]
        return card(ctx, e["id"])
    return res.reply


@screen("pat")
def team_screen(ctx, emp_id, act=None, value=None):
    e = _emp(ctx, emp_id)
    if not e:
        return people_access(ctx)
    teams = ctx.cfg.teams_of(e["dept"])
    res = pick.one(ctx, f"pat:{e['id']}", act, value, [(t.id, t.name) for t in teams], e["team"],
                   f"👥 Команда · {e['fio']}", "отметьте и нажмите «ОК»")
    if res.cancel:
        return card(ctx, e["id"])
    if res.done:
        t = next((t for t in teams if t.id == res.value), None)
        if t and t.id != e["team"]:
            ctx.db.c.execute("UPDATE employees SET team = ? WHERE id = ?", (t.id, e["id"]))
            ctx.db.audit(_who(ctx), "team", f"{e['fio']}: {t.name}")
        return card(ctx, e["id"])
    return res.reply


@screen("par")
def role_screen(ctx, emp_id, act=None, value=None):
    e = _emp(ctx, emp_id)
    if not e or not full(ctx):
        return people_access(ctx)
    opts = [(r, ctx.cfg.role_title(r)) for r in ROLE_CHOICES]
    if e["role"] in ("rep", "newbie"):
        opts.append(("newbie", "🎓 Новичок (обучение)"))
    res = pick.one(ctx, f"par:{e['id']}", act, value, opts, e["role"], f"🎚 Роль · {e['fio']}",
                   "что он видит и решает", note="«Администратор» — ведёт людей, доступ и данные, зарплату не утверждает.")
    if res.cancel:
        return card(ctx, e["id"])
    if res.done:
        role = res.value
        if (role in ROLE_CHOICES or role == "newbie") and role != e["role"]:
            ctx.db.c.execute("UPDATE employees SET role = ? WHERE id = ?", (role, e["id"]))
            ctx.db.audit(_who(ctx), "role", f"{e['fio']}: {ctx.cfg.role_title(role)}")
            return [Reply(text=f"🎚 {e['fio']} — {ctx.cfg.role_title(role)}."), card(ctx, e["id"])]
        return card(ctx, e["id"])
    return res.reply


# ---------- добавить ----------

@screen("pad")
def add_start(ctx, dept_id=None, act=None, value=None):
    """➕ Добавить: отдел (окно, если отделов несколько) → фамилия и имя → должность и команда (окно) → готово."""
    if not manages(ctx):
        return people_access(ctx)
    depts = _depts(ctx)
    ids = {d.id for d in depts}
    if dept_id in ids and act is None:
        ctx.session["pa_new"] = {"dept": dept_id}
        ctx.save()
        return ctx.ask("pa_name", "Фамилия и имя нового сотрудника — как в 1С, если он торговый:",
                       buttons=[[Button("✖️ Отмена", f"pa:{dept_id}")]])
    if len(depts) == 1:
        return add_start(ctx, depts[0].id)
    act, value = (dept_id, act) if dept_id in ("t", "ok", "no") else (act, value)
    res = pick.one(ctx, "pad", act, value, [(d.id, d.name) for d in depts], None, "➕ Новый сотрудник: в какой отдел?",
                   "отметьте и нажмите «ОК»")
    if res.cancel or (res.done and res.value not in ids):
        return people_access(ctx)
    if res.done:
        return add_start(ctx, res.value)
    return res.reply


@text_input("pa_name")
def on_name(ctx, text, data):
    name = " ".join(text.split())[:80]
    st = ctx.session.get("pa_new") or {}
    if len(name) < 3 or not st.get("dept"):
        return add_start(ctx, st.get("dept"))
    st["name"] = name
    ctx.session["pa_new"] = st
    ctx.save()
    if not [p for p in ctx.cfg.positions if p["dept"] == st["dept"] and (p["role"] != "admin" or full(ctx))]:
        return _add_finish(ctx, st, None)
    return add_details(ctx)


@screen("padw")
def add_details(ctx, act=None, value=None):
    """Должность и команда нового сотрудника — одно окно, «ОК» добавляет."""
    st = ctx.session.get("pa_new") or {}
    if not st.get("name") or not manages(ctx):
        return people_access(ctx)
    poss = [p for p in ctx.cfg.positions if p["dept"] == st["dept"] and (p["role"] != "admin" or full(ctx))]
    teams = ctx.cfg.teams_of(st["dept"])
    if act == "no":
        ctx.session.pop("pa_new", None)
        ctx.save()
        return people_access(ctx, st["dept"])
    if act == "t" and any(p["id"] == value for p in poss):
        st["pos"] = value
    elif act == "m" and (value == "-" or any(t.id == value for t in teams)):
        st["team"] = "" if value == "-" else value
    pos = ctx.cfg.position(st.get("pos", "")) if st.get("pos") else None
    need_team = bool(teams) and pos is not None and pos["role"] in ("rep", "sv", "newbie")
    if act == "ok" and pos:
        if not need_team:
            st.pop("team", None)
        return _add_finish(ctx, st, pos)
    ctx.session["pa_new"] = st
    ctx.save()
    v = View(f"➕ {st['name']}", "должность" + (" и команда" if teams else "") + " — отметьте и нажмите «ОК»")
    if act == "ok":
        v.note("Сначала отметьте должность.")
    kb = [[Button(f"{'☑' if st.get('pos') == p['id'] else '☐'} {p['name']}", f"padw:t:{p['id']}")] for p in poss]
    if need_team:
        kb += rows([Button(("● " if st.get("team") == t.id else "") + t.name, f"padw:m:{t.id}") for t in teams]
                   + [Button(("● " if not st.get("team") else "") + "Без команды", "padw:m:-")], 3)
    kb.append(ok_row("padw"))
    return Reply(view=v, buttons=kb)


def _add_finish(ctx, st, p):
    ctx.session.pop("pa_new", None)
    ctx.save()
    role = p["role"] if p else "staff"
    emp_id = ctx.db.add_employee(st["name"], st["dept"], team=st.get("team", ""), position=p["id"] if p else "", role=role,
                                 hired=ctx.today.isoformat())
    ctx.db.audit(_who(ctx), "hire", f"{st['name']} — {p['name'] if p else ctx.cfg.role_title(role)}")
    msg = f"✅ {st['name']} добавлен." + ("" if ctx.demo else " Теперь «🔗 Пригласить» — и отправьте ему ссылку.")
    return [Reply(text=msg), card(ctx, emp_id)]


# ---------- имена из 1С ----------

@screen("pak")
def unknown_screen(ctx, key=None, act=None, value=None):
    if not manages(ctx):
        return people_access(ctx)
    keys = ctx.db.unknown_keys()
    if key and key not in keys:
        return unknown_screen(ctx)
    emp_id = None
    if key and act not in (None, "t", "ok", "no"):
        emp_id = act                                     # старый адрес pak:<имя>:<сотрудник|new>
    elif key:
        es = [e for e in ctx.db.employees() if manages(ctx, e)]
        words = key.split()
        close = [e for e in es if any(w in e["fio"].lower() for w in words)]
        opts = [(str(e["id"]), e["fio"]) for e in (close or es)[:30]] + [("new", "➕ Новый торговый с этим именем")]
        res = pick.one(ctx, f"pak:{key}", act, value, opts, None, f"🧾 «{key.title()}» в отчётах 1С — кто это?",
                       "отметьте и нажмите «ОК»")
        if res.cancel or (res.done and not res.value):
            return unknown_screen(ctx)
        if not res.done:
            return res.reply
        emp_id = res.value
    if key and emp_id == "new":
        d = next((x for x in ctx.cfg.departments if x.kind == "sales"), None) or ctx.cfg.departments[0]
        if not manages(ctx) or d.id not in {x.id for x in _depts(ctx)}:
            return unknown_screen(ctx)
        pos = next((p for p in ctx.cfg.positions if p["dept"] == d.id and p["role"] == "rep"), None)
        new_id = ctx.db.add_employee(key.title(), d.id, position=pos["id"] if pos else "", role="rep", keys=[key],
                                     hired=ctx.today.isoformat())
        ctx.db.audit(_who(ctx), "hire", f"{key.title()} — из отчёта 1С")
        return [Reply(text=f"✅ Заведён новый торговый {key.title()}; имя из 1С привязано."), card(ctx, new_id)]
    if key and emp_id:
        e = _emp(ctx, emp_id)
        if not e:
            return unknown_screen(ctx, key)
        ctx.db.bind_key(key, e["id"])
        ctx.db.audit(_who(ctx), "bind", f"{key} → {e['fio']}")
        return [Reply(text=f"🔑 «{key.title()}» — это {e['fio']}. Его цифры из отчётов теперь считаются ему."), unknown_screen(ctx)]
    if not keys:
        return Reply(text="Все имена из отчётов привязаны к людям.", buttons=[[Button("‹ Сотрудники", "pa")]])
    v = View("🧾 Нет в расчёте", f"имён из отчётов 1С без человека: {len(keys)}")
    v.lines([k.title() for k in keys[:30]])
    v.note("Нажмите имя и выберите, кто это. Пока имя не привязано, его продажи и долги никому не считаются.")
    return Reply(view=v, buttons=rows([Button(k.title(), f"pak:{k}") for k in keys[:30]], 2) + [[Button("‹ Сотрудники", "pa")]])


menu("pa", "👥 Сотрудники и доступ", roles={"admin", "owner", "head"}, order=58)
