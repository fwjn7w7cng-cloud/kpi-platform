"""📥 Отчёты 1С в приложении настоящего клиента (Адам 31.10): загрузить файлы и увидеть, что пришло.

  «📥 Загрузить отчёты» (`up`)   — кнопка выбора файлов (приложение шлёт каждый файл в /api/upload → App.upload);
                                  ниже — что лежит в базе по месяцам и последние загрузки.
  «🧪 Что пришло» (`upl`)        — журнал загрузок: файл, кто, когда, что принято / пропущено / ошибки.
Приём — общий конвейер core/ingest/pipeline.ingest (переводчик клиента clients/<код>/parser.py, правила месяцев).
Загрузка руками считается «файлом от админа» (manual): за месяц на согласовании принимается, за утверждённый — нет.
Загружают: администратор (ключ или сотрудник с ролью «Администратор»), руководитель, бухгалтер. В демо — только объяснение.
Журнал — настройка `uploads` в базе клиента (последние LOG_MAX).
"""
from datetime import datetime

from core.calendar import month_of, month_title
from core.ingest.pipeline import KIND_TITLE, ingest
from core.ui.kit import Button, Reply, rows
from core.ui.registry import menu, screen
from core.ui.views import Row, View

KEY = "uploads"
LOG_MAX = 50
MAX_BYTES = 15 * 1024 * 1024
EXT = (".xls", ".xlsx", ".csv", ".txt", ".xml", ".mxl", ".html", ".htm")
ROLES = ("admin", "owner", "accountant")
KIND_TITLE = {**KIND_TITLE, "returns": "возвраты", "stock": "остатки", "item_sales": "продажи по товарам",
              "trips": "рейсы водителей", "trip_stops": "точки рейсов"}


def may(ctx):
    return ctx.admin or ctx.role in ROLES


def log_of(db):
    return db.get(KEY) or []


def receive(ctx, name, data):
    """Принять один файл: проверить права и размер, прогнать конвейер, записать в журнал. → [Reply]."""
    name = (name or "файл").replace("/", "_").replace("\\", "_")[:120]
    if ctx.demo:
        return [Reply(text="В демо цифры выдуманы — загружать некуда. У настоящего клиента здесь принимаются отчёты 1С.")]
    if not may(ctx):
        return [Reply(text="Отчёты загружают администратор, руководитель и бухгалтер.")]
    if not data:
        return [Reply(text=f"«{name}» пустой — нечего принимать.")]
    if len(data) > MAX_BYTES:
        return [Reply(text=f"«{name}» больше {MAX_BYTES // 1024 // 1024} МБ — сохраните отчёт за месяц, а не за год.")]
    if not name.lower().endswith(EXT):
        return [Reply(text=f"«{name}» — не похоже на отчёт 1С. Принимаются Excel (.xls, .xlsx), .csv и .mxl.")]
    res = ingest(ctx.cfg.id, ctx.db, [(name, data)], manual=True, hooks=ctx.app.hooks)[0]
    entry = {"ts": datetime.now().strftime("%d.%m.%Y %H:%M"), "who": ctx.name(), "name": name,
             "ok": res.ok, "skip": res.skipped, "err": res.errors}
    ctx.db.put(KEY, ([entry] + log_of(ctx.db))[:LOG_MAX])
    ctx.db.audit(ctx.name(), "upload", f"{name}: принято {len(res.ok)}, пропущено {len(res.skipped)}, ошибок {len(res.errors)}")
    v = View("🧪 Что пришло", name)
    _entry(v, entry)
    kb = []
    if ctx.db.unknown_keys():
        kb.append([Button("🧾 Нет в расчёте — привязать", "pak")])
    kb.append([Button("📥 Ещё файлы", "up"), Button("🧪 Все загрузки", "upl")])
    return [Reply(view=v, buttons=kb)]


def _entry(v, e, title=""):
    lines = [f"✅ {x}" for x in e["ok"]] + [f"⏭ {x}" for x in e["skip"]] + [f"⚠️ {x}" for x in e["err"]]
    if not lines:
        lines = ["ничего не найдено"]
    v.lines(lines, title=title or f"{e['ts']} · {e['who']}")
    if not e["ok"] and not e["err"] and any("не похоже" in x for x in e["skip"]):
        v.note("Бот не узнал этот отчёт. Если это нужный отчёт 1С — пришлите его нам: настроим переводчик под вашу 1С.")


def _months(ctx):
    """Что лежит в базе: месяц → {вид: строк}."""
    out = {}
    for b in ctx.db.batches():
        m = b["end"][:7]
        out.setdefault(m, {})
        out[m][b["kind"]] = out[m].get(b["kind"], 0) + b["rows"]
    return dict(sorted(out.items(), reverse=True))


@screen("up")
def upload_screen(ctx):
    if not may(ctx):
        return Reply(text="Отчёты загружают администратор, руководитель и бухгалтер.")
    v = View("📥 Отчёты 1С", ctx.cfg.name)
    if ctx.demo:
        v.lines(["В демо все цифры выдуманы и наполняются сами — загружать сюда ничего не нужно.",
                 "У настоящего клиента здесь кнопка «Выбрать файлы»: бухгалтер сохраняет отчёт из 1С в Excel и загружает его, "
                 "бот сам узнаёт вид отчёта и месяц, показывает, что принял, и кого из торговых не узнал."])
        return Reply(view=v)
    months = _months(ctx)
    if months:
        v.table(["Месяц", "Что есть"], [Row([month_title(m), ", ".join(f"{KIND_TITLE.get(k, k)} {n}" for k, n in kinds.items())])
                                          for m, kinds in list(months.items())[:6]], title="В базе")
    else:
        v.note("В базе пока ничего нет. Загрузите первые отчёты — продажи за месяц, долги, деньги.")
    lg = log_of(ctx.db)
    if lg:
        e = lg[0]
        v.lines([f"{e['ts']} · {e['name']} — принято {len(e['ok'])}, пропущено {len(e['skip'])}, ошибок {len(e['err'])}"],
                title="Последняя загрузка")
    v.note("Как: в 1С сформируйте отчёт → «Сохранить как» Excel → здесь «Выбрать файлы». Можно сразу несколько. "
           "Тот же файл второй раз не примется; новый файл за тот же период заменит прежний. Утверждённый месяц закрыт.")
    st = ctx.db.month_status(month_of(ctx.today))
    if st == "review":
        v.note(f"{month_title(month_of(ctx.today))} на согласовании — файлы за него принимаются только отсюда, не автоматически.")
    kb = [[Button("🧪 Все загрузки", "upl")]]
    if ctx.db.unknown_keys():
        kb.append([Button("🧾 Нет в расчёте", "pak")])
    return Reply(view=v, buttons=kb, upload=True)


@screen("upl")
def log_screen(ctx, n=None):
    if not may(ctx):
        return Reply(text="Отчёты загружают администратор, руководитель и бухгалтер.")
    lg = log_of(ctx.db)
    if not lg:
        return Reply(text="Загрузок пока не было.", buttons=[[Button("📥 Загрузить", "up")]])
    if n is not None and n.isdigit() and int(n) < len(lg):
        e = lg[int(n)]
        v = View("🧪 Загрузка", e["name"])
        _entry(v, e)
        return Reply(view=v, buttons=[[Button("‹ Все загрузки", "upl")]])
    v = View("🧪 Что пришло", f"последние {min(len(lg), 20)} загрузок")
    v.table(["Когда", "Файл", "Итог"],
            [Row([e["ts"], e["name"][:40], f"✅ {len(e['ok'])} · ⏭ {len(e['skip'])}" + (f" · ⚠️ {len(e['err'])}" if e["err"] else "")])
             for e in lg[:20]])
    kb = rows([Button(f"{i + 1}. {e['name'][:22]}", f"upl:{i}") for i, e in enumerate(lg[:10])], 2)
    kb.append([Button("📥 Загрузить", "up")])
    return Reply(view=v, buttons=kb)


menu("up", "📥 Отчёты 1С", roles=set(ROLES), order=57)
