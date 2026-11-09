"""Ориентация в приложении (Адам 09.11: «чтобы клиент очень быстро сориентировался и я сам мог там ориентироваться»).

- Полоса демо (`demo_bar`) — на каждом экране демо: чьими глазами смотрим, отрасль, «Собрать», поиск. Приложение рисует её
  под шапкой; роли и отрасль — листом снизу, одно нажатие — экран перестроился.
- Приветствие (`wel`) — гостю демо один раз за сессию: три шага и «Начать с директора».
- Поиск (`find`) — всем, и настоящим клиентам: слово → экраны, функции, блоки зарплаты и справка с «дорогой», где это лежит.
"""
import re

from core.access import editions as E
from core.config import INDUSTRIES
from core.ui.kit import Button, Reply, View, rows
from core.ui.registry import MENU, screen, text_input

ROLE_LABELS = [("🧑‍💼 Торговый", "rep"), ("👥 Супервайзер", "sv"), ("🏢 Руководитель продаж", "head"),
               ("👔 Директор", "owner"), ("🧾 Бухгалтер", "accountant"), ("🛠 Администратор", "admin")]
PEOPLE_IN_BAR = 6


# ---------- полоса демо ----------

def demo_bar(ctx):
    """Данные для полосы под шапкой: текущая роль и отрасль, варианты для листов, ссылки."""
    from core.ui.screens import DEPT_ICON, _short
    from core.ui.tariff import base_of
    allowed = E.PRESETS[base_of(ctx)]["roles"]
    roles = [[t, f"sw:role:{r}"] for t, r in ROLE_LABELS if r in allowed]
    have = {e["position"] for e in ctx.db.employees()}
    for d in ctx.cfg.departments:
        if d.kind == "sales":
            continue
        for p in ctx.cfg.positions:
            if p["dept"] == d.id and p["id"] in have and p["role"] not in ("owner", "accountant", "sv", "rep", "newbie"):
                roles.append([f"{DEPT_ICON.get(d.kind, '🏬')} {p['name']}", f"sw:pos:{p['id']}"])
    people = [[f"{_short(e['fio'])} · {(ctx.cfg.position(e['position']) or {}).get('name', '')[:14]}", f"sw:as:{e['id']}"]
              for e in ctx.db.employees()[:PEOPLE_IN_BAR]]
    role_t = ctx.position_name if ctx.role in ("staff", "head") and ctx.position_name else \
        ctx.cfg.role_title(ctx.role) if ctx.role not in ("guest", "admin") else "Администратор"
    if ctx.me and ctx.session.get("as"):
        role_t = f"{role_t} · {_short(ctx.me['fio'])}"
    cur = f"sw:as:{ctx.session['as']}" if ctx.session.get("as") else f"sw:role:{ctx.role}"
    if ctx.me and ctx.role in ("staff", "head") and not ctx.session.get("as"):
        cur = f"sw:pos:{ctx.me['position']}"
    return {"role": role_t, "cur": cur, "roles": roles, "people": people, "more": "sw:who",
            "industry": INDUSTRIES[ctx.industry], "industries": [[t, f"sw:ind:{k}"] for k, t in INDUSTRIES.items()],
            "build": "sw:ed", "find": "find"}


def home_row(ctx):
    """Первый ряд плиток главной в демо: три действия клиента."""
    built = bool(ctx.session.get("built"))
    row = [] if built else [Button("🧩 Собрать свой бот", "sw:ed")]
    return row + [Button("👀 Посмотреть по ролям", "sw:role"), Button("📘 Что умеет", "help")]


# ---------- приветствие ----------

def welcome_due(ctx):
    """Гостю демо — один раз за сессию (после «🔄 Заново» — снова); админу — нет."""
    return bool(ctx.demo and ctx.app.demo_gate and ctx.user_id not in ctx.app.admins and not ctx.session.get("wel"))


@screen("wel")
def welcome(ctx, act=None):
    from core.ui.screens import home
    if not ctx.demo:
        return home(ctx)
    ctx.session["wel"] = 1
    if act == "go":
        ctx.session["role"], ctx.session["as"] = "owner", None
        ctx.save()
        return home(ctx.app.ctx(ctx.user_id))
    ctx.save()
    v = View("👋 Это демо «Алапы»", "приложение для зарплаты и показателей оптовой фирмы")
    v.lines(["Фирма «" + ctx.cfg.name + "» и все цифры тут выдуманы — нажимайте всё, ничего не сломается.",
             "1️⃣ Посмотрите глазами людей: директор, торговый, кладовщик, водитель — у каждого свой экран. "
             "Переключатель — 👤 под шапкой.",
             "2️⃣ Соберите свой набор: отделы, функции, как платите. Цена считается сразу.",
             "3️⃣ Понравилось — «Хочу так»: мы свяжемся и подключим за несколько дней."])
    return Reply(view=v, buttons=[[Button("👔 Начать с директора", "wel:go")],
                                  [Button("🧩 Сразу собрать набор", "sw:ed"), Button("👀 Чьими глазами", "sw:role")]])


# ---------- поиск ----------

_WORD = re.compile(r"[a-zа-яё0-9]+")


def _words(s):
    return _WORD.findall(str(s).lower().replace("ё", "е"))


def _stem(w):
    return w[:5] if len(w) > 5 else w


def _hit(query, text):
    """Каждое слово запроса — начало какого-то слова текста (по основе: «табел» найдёт «табель» и «табеля»)."""
    ws = _words(text)
    return all(any(x.startswith(_stem(q)) for x in ws) for q in query)


def index(ctx):
    """[(подпись, экран, дорога, что внутри)] — всё, что видит этот зритель."""
    from core import paybuilder as PB
    from core.ui import screens as S
    out, seen = [], set()

    def add(label, to, path, desc=""):
        key = (label, to)
        if key not in seen:
            seen.add(key)
            out.append((label, to, path, desc))

    for row in S.bottom_rows(ctx):
        for label, to in row:
            add(label, to, "нижнее меню")
    for m, b in S._menu_items(ctx):
        g = S._group_of(ctx, m)
        title = next((t for gg, t, _ in S.MENU_GROUPS if gg == g), None)
        add(m.label, b.to or "", "Главная" + (f" → {title}" if title else ""), S.LOCK_TEXT.get(m.module, ""))
    for b in S._dept_buttons(ctx):
        add(b.label, b.to, "Главная")
    build = "sw:ed" if ctx.demo else "tf"
    where = "Собрать → Что нужно" if ctx.demo else "Тариф"
    for f, (grp, _) in E.FUNCS.items():
        icon, gt = E.FUNC_GROUPS[grp]
        add(f"{icon} {E.FUNC_SHORT.get(f, f)}", build, f"{where} → {gt}", S.LOCK_TEXT.get(f, ""))
    if ctx.demo:
        for code, (sect, icon, title, _, vs, *rest) in PB.BRICKS.items():
            add(f"{icon} {title}", "sw:ed", "Собрать → Как платим → Ещё способы оплаты",
                " ".join(v[1] for v in vs) + " " + (rest[0] if rest else ""))
    for k, t in INDUSTRIES.items():
        if ctx.demo:
            add(f"🏭 {t}", f"sw:ind:{k}", "полоса демо → отрасль")
    role = "rep" if ctx.role == "newbie" else ctx.role
    for line in S.HELP.get(role, S.HELP["staff"]):
        add(line[:60] + ("…" if len(line) > 60 else ""), "help", "Как пользоваться", line)
    return out


@screen("find")
def find(ctx):
    return ctx.ask("find", "🔍 Что ищем? Напишите слово: табель, зарплата, долги, маршрут, водители…",
                   buttons=[[Button("✖️ Отмена", "home")]])


@text_input("find")
def find_text(ctx, text, data):
    q = _words(text)[:4]
    if not q:
        return find(ctx)
    hits = [x for x in index(ctx) if _hit(q, x[0])] + \
           [x for x in index(ctx) if not _hit(q, x[0]) and _hit(q, x[0] + " " + x[3])]
    hits = hits[:12]
    v = View(f"🔍 «{text.strip()[:30]}»", f"найдено: {len(hits)} · ⓘ — где это лежит" if hits else "ничего не нашлось")
    if not hits:
        v.lines(["Попробуйте другое слово: «табель», «зарплата», «долги», «водител», «склад»."])
    kb = [[Button(label, to, info=f"Где: {path}" + (f"\n\n{desc}" if desc and not desc.startswith(label) else ""))]
          for label, to, path, desc in hits if to and to != "find"]
    kb.append([Button("🔍 Искать ещё", "find")])
    return Reply(view=v, buttons=kb)
