"""Редакция «Максимум»: всё из «Компании» и сверх того — свой бренд, прямая связь с 1С, филиалы и функции на заказ.

В младших редакциях эти пункты видны с замком (клиент знает, что такое есть). В «Максимуме»:
  branding   экран «как выглядит ваше приложение»: название, значок, цвет, свой адрес
  direct_1c  что и когда пришло из 1С, что бот отправляет в 1С обратно
  branches   сводка по филиалам и юрлицам (в демо — одна фирма)
  custom     функции на заказ: до CUSTOM_MAX в месяц, каждая — до одного рабочего дня разработчика;
             заявку пишет директор, администратор платформы видит её сразу и отмечает «в работе» / «готово».
База: custom_req — список заявок {id, month, text, author, status, ts, note}.
"""
from datetime import datetime

from core.calendar import month_of, month_title
from core.ui.kit import Button, Reply, money, pct
from core.ui.registry import menu, screen, text_input
from core.ui.views import Card, Row, View

CUSTOM_MAX = 3
ST = {"new": "🆕 принята", "work": "🛠 в работе", "done": "✅ готово", "later": "⏭ на следующий месяц"}
ITEMS = [("custom", "🧩 Функции на заказ"), ("direct_1c", "🔌 Прямая связь с 1С"), ("branches", "🏢 Филиалы"),
         ("branding", "🎨 Свой бренд")]

for i, (code, label) in enumerate(ITEMS):
    menu(code, label, {"owner", "admin"}, module=code, order=90 + i)


def _admin(ctx):
    return ctx.role == "admin" or ctx.user_id in ctx.app.admins


# ---------- функции на заказ ----------

def requests(db):
    return db.get("custom_req") or []


@screen("custom", module="custom")
def custom(ctx):
    month = month_of(ctx.today)
    rs = requests(ctx.db)
    this = [r for r in rs if r["month"] == month and r["status"] != "later"]
    left = max(CUSTOM_MAX - len(this), 0)
    v = View("🧩 Функции на заказ", f"{month_title(month)} · осталось {left} из {CUSTOM_MAX}")
    v.lines(["Опишите своими словами, чего не хватает: новый отчёт, экран, напоминание, расчёт бонуса.",
             "Разработчик сделает с нуля под вашу компанию — каждая функция до одного рабочего дня.",
             "Больше одного дня — разобьём на части или посчитаем отдельно; не использованные заявки не переносятся."])
    v.cards([Card(f"Заявка №{r['id']}", ST[r["status"]], [r["text"], f"{r['author']} · {r['ts']}"] + ([r["note"]] if r.get("note") else []),
                  color="good" if r["status"] == "done" else "warn" if r["status"] == "work" else None)
             for r in reversed(rs[-8:])], title="Заявки")
    if not rs:
        v.note("Заявок пока нет.")
    kb = []
    if ctx.role in ("owner", "admin"):
        kb.append([Button("➕ Заказать функцию", "cuq")] if left else [])
    if _admin(ctx):
        kb += [[Button(f"🛠 №{r['id']}", f"cus:{r['id']}:work"), Button(f"✅ №{r['id']}", f"cus:{r['id']}:done")]
               for r in rs if r["status"] in ("new", "work")]
    return Reply(view=v, buttons=[r for r in kb if r])


@screen("cuq", module="custom")
def ask(ctx):
    if ctx.role not in ("owner", "admin"):
        return Reply(text="Заказывает директор.")
    month = month_of(ctx.today)
    if sum(1 for r in requests(ctx.db) if r["month"] == month and r["status"] != "later") >= CUSTOM_MAX:
        return Reply(text=f"В этом месяце уже {CUSTOM_MAX} заявки — следующая с 1-го числа.")
    return ctx.ask("custom_req", "Что сделать? Опишите, кто будет пользоваться и что должен видеть или получать.\n\n"
                                 "Например: «Утром начальнику склада — список вчерашних заявок, которые не отгрузили».",
                   buttons=[[Button("✖️ Отмена", "custom")]])


@text_input("custom_req")
def on_request(ctx, text, data):
    t = " ".join((text or "").split())[:500]
    if len(t) < 10:
        return ctx.ask("custom_req", "Опишите чуть подробнее — хотя бы одно предложение.", buttons=[[Button("✖️ Отмена", "custom")]])
    rs = requests(ctx.db)
    rid = max([r["id"] for r in rs] or [0]) + 1
    rs.append({"id": rid, "month": month_of(ctx.today), "text": t, "author": ctx.name(), "status": "new",
               "ts": datetime.now().strftime("%d.%m %H:%M"), "note": ""})
    ctx.db.put("custom_req", rs)
    ctx.db.audit(ctx.author(), "custom_req", t[:120])
    out = [Reply(text=f"🧩 Заявка №{rid} принята. Разработчик ответит здесь же — статус виден в «Функции на заказ».")]
    out += [Reply(text=f"🧩 {ctx.cfg.name}: заявка №{rid} от {ctx.name()}\n\n{t}", to=a,
                  buttons=[[Button("🛠 В работу", f"cus:{rid}:work"), Button("✅ Готово", f"cus:{rid}:done")]])
            for a in sorted(ctx.app.admins) if a != ctx.user_id]
    return out + [custom(ctx)]


@screen("cus", module="custom")
def set_status(ctx, rid, st):
    if not _admin(ctx) or st not in ST:
        return Reply(text="Статус меняет разработчик.")
    rs = requests(ctx.db)
    r = next((x for x in rs if x["id"] == int(rid)), None)
    if not r:
        return custom(ctx)
    r["status"] = st
    ctx.db.put("custom_req", rs)
    return custom(ctx)


# ---------- прямая связь с 1С ----------

TITLES = {"sales": "Продажи", "debts": "Долги", "payments": "Деньги", "visits": "Визиты", "returns": "Возвраты",
          "stock": "Остатки", "item_sales": "Продажи по товарам", "trips": "Рейсы", "trip_stops": "Точки рейсов",
          "clients": "Клиенты", "groups": "Группы товаров"}


@screen("direct_1c", module="direct_1c")
def direct(ctx):
    last = {}
    for b in ctx.db.batches():
        if b["kind"] in TITLES and (b["kind"] not in last or b["ts"] > last[b["kind"]]["ts"]):
            last[b["kind"]] = b
    v = View("🔌 Прямая связь с 1С", "без выгрузок файлов: 1С сама отдаёт данные, бот сам отправляет документы")
    v.table(["Что", "Последний раз", "Строк"], [Row([TITLES[k], b["ts"][:16].replace("T", " "), f"{b['rows']:,}".replace(",", " ")])
                                               for k, b in sorted(last.items(), key=lambda t: TITLES[t[0]])],
            title="Из 1С в приложение")
    v.lines(["✅ Согласованный заказ поставщику → документ «Заказ поставщику» в 1С.",
             "✅ Принятая поставка с недостачей → комментарий к приходной накладной.",
             "⏱ Обмен каждые 15 минут; ночью — полная сверка за месяц."], title="Из приложения в 1С")
    v.note("Ставится расширением 1С на сервере клиента: файлы, почта и папки больше не нужны.")
    return Reply(view=v)


# ---------- филиалы ----------

@screen("branches", module="branches")
def branches(ctx):
    k = ctx.kpi()
    sales = next((d for d in ctx.cfg.departments if d.kind == "sales"), None)
    m = k.scope("dept", sales.id) if sales else None
    fund = sum(ctx.pay().calc(e).total for e in k.employees.values() if e["active"]) if ctx.can("payroll") else 0
    v = View("🏢 Филиалы и юрлица", month_title(ctx.month))
    v.table(["Филиал", "Продажи", "План", "Фонд"],
            [Row([ctx.cfg.name, money(m["sales"].fact, "") if m else "—", pct(m["sales"].pct) if m else "—", money(fund, "")],
                 sub="главный офис")], title="Сводка по сети")
    v.lines(["Каждый филиал или юрлицо — своя строка: продажи, план, долги, фонд зарплаты.",
             "Общий рейтинг торговых по всей сети и доска почёта.",
             "Директор переходит в филиал одной кнопкой; руководитель филиала видит только свой."])
    v.note("В демо одна фирма — у клиента с филиалами здесь будет строка на каждый.")
    return Reply(view=v)


# ---------- свой бренд ----------

@screen("branding", module="branding")
def branding(ctx):
    url = ctx.app.web_url or "https://<компания>.kpibotsmith.ru"
    v = View("🎨 Свой бренд", "приложение выглядит как ваше собственное")
    v.tiles([("Название", ctx.cfg.name, None), ("Адрес", url.replace("https://", ""), None)])
    v.lines(["Значок на телефоне — ваш логотип и ваше название под ним.",
             "Цвета приложения — фирменные: шапка, кнопки, графики.",
             "Свой адрес: <компания>.kpibotsmith.ru или ваш домен.",
             "Файлы — ведомость, приказы, заказы поставщикам — с вашей шапкой."])
    v.note("Пришлите логотип (PNG или SVG) и цвет — настроим за день.")
    return Reply(view=v)
