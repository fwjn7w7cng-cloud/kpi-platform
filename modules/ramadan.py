"""«🌙 Рамадан» — у всех клиентов, в любой редакции. Руководитель (директор, начальник отдела) или админ
включает режим на даты поста этого года. В эти дни:
  • опоздания отмечаются в табеле, но без штрафа (можно выключить);
  • утренняя и вечерняя рассылки — в своё время (по умолчанию 10:00 и 16:30, до ифтара);
  • планы «как в прошлом месяце» на месяц с Рамаданом — с поправкой (по умолчанию 85% за дни поста);
  • на главной — «Рамадан мубарак» и что изменилось.
Хранится в базе клиента (настройка ramadan), логика — core/calendar.py.
"""
import re
from datetime import date

from core.calendar import RAMADAN_DEFAULT, in_ramadan, ramadan
from core.ui.kit import Button, Reply, ok_row, pct
from core.ui.registry import menu, screen, text_input
from core.ui.views import Card, View

WHO = {"owner", "admin", "head"}

menu("rmd", "🌙 Рамадан", WHO, order=85)


def _d(iso):
    d = date.fromisoformat(iso)
    return f"{d:%d.%m.%Y}"


@screen("rmd")
def ramadan_screen(ctx, action=None, value=None):
    if ctx.role not in WHO:
        return Reply(text="Режим Рамадана включает руководитель.")
    r = ramadan(ctx.db)
    if action == "off" and r:
        ctx.db.put("ramadan", {})
        ctx.db.audit(ctx.author(), "ramadan", "выключен")
        r = None
    elif action == "late" and r:
        r = {**(ctx.db.get("ramadan") or {}), "late_free": not r.get("late_free")}
        ctx.db.put("ramadan", r)
        r = ramadan(ctx.db)
    elif action == "plan" and r and value:
        ctx.db.put("ramadan", {**(ctx.db.get("ramadan") or {}), "plan": int(value) / 100})
        r = ramadan(ctx.db)
    elif action == "on":
        return ctx.ask("ramadan_dates", "Напишите даты Рамадана в этом году: «18.02.2027 – 19.03.2027».")
    elif action == "time":
        return ctx.ask("ramadan_time", "Во сколько утренняя и вечерняя рассылки в Рамадан? Например: «10:00 16:30».")
    v = View("🌙 Рамадан", "режим для дней поста")
    if not r:
        v.lines(["Режим выключен.",
                 "Когда включите на даты поста: опоздания — без штрафа, рассылки — позже утром и до ифтара, "
                 f"планы на этот месяц — с поправкой {pct(RAMADAN_DEFAULT['plan'])} за дни поста."])
        return Reply(view=v, buttons=[[Button("🌙 Включить на даты", "rmd:on")]])
    now = in_ramadan(ctx.db, ctx.today)
    v.cards([Card(f"{_d(r['start'])} – {_d(r['end'])}", "идёт сейчас" if now else "включён",
                  [f"Рассылки: утро {r['morning']}, вечер {r['evening']}",
                   "Опоздания: без штрафа" if r.get("late_free") else "Опоздания: штраф как обычно",
                   f"Планы «как в прошлом месяце»: {pct(r['plan'])} за дни поста"], color="good" if now else None)])
    kb = [[Button("⚙️ План и опоздания", "rmw")],
          [Button("🕘 Время рассылок", "rmd:time"), Button("📅 Другие даты", "rmd:on")],
          [Button("✖️ Выключить", "rmq")]]
    return Reply(view=v, buttons=kb)


@screen("rmw")
def settings_window(ctx, act=None, value=None):
    """План в дни поста и штраф за опоздания — окно: отметить, «ОК» / «Отмена»."""
    if ctx.role not in WHO:
        return Reply(text="Режим Рамадана включает руководитель.")
    r = ramadan(ctx.db)
    if not r:
        return ramadan_screen(ctx)
    d = ctx.session.get("rmd_draft")
    if act is None or not isinstance(d, dict):
        d = {"plan": round(r["plan"] * 100), "late_free": bool(r.get("late_free"))}
    if act == "no":
        ctx.session.pop("rmd_draft", None)
        ctx.save()
        return ramadan_screen(ctx)
    if act == "ok":
        ctx.session.pop("rmd_draft", None)
        ctx.save()
        ctx.db.put("ramadan", {**(ctx.db.get("ramadan") or {}), "plan": d["plan"] / 100, "late_free": d["late_free"]})
        ctx.db.audit(ctx.author(), "ramadan", f"план {d['plan']}%, опоздания {'без штрафа' if d['late_free'] else 'со штрафом'}")
        return ramadan_screen(ctx)
    if act == "p" and (value or "").isdigit():
        d["plan"] = int(value)
    elif act == "l":
        d["late_free"] = not d["late_free"]
    ctx.session["rmd_draft"] = d
    ctx.save()
    v = View("⚙️ Рамадан: план и опоздания", "отметьте и нажмите «ОК»")
    kb = [[Button(f"{'☑' if d['plan'] == p else '☐'} План {p}% за дни поста", f"rmw:p:{p}")] for p in (100, 90, 85, 80)]
    kb.append([Button(f"{'☑' if d['late_free'] else '☐'} Опоздания без штрафа", "rmw:l")])
    kb.append(ok_row("rmw"))
    return Reply(view=v, buttons=kb)


@screen("rmq")
def confirm_off(ctx, act=None):
    if act == "ok":
        return ramadan_screen(ctx, "off")
    if act == "no":
        return ramadan_screen(ctx)
    return Reply(text="Выключить режим Рамадана? Опоздания снова со штрафом, рассылки — в обычное время.", buttons=[ok_row("rmq")])


@text_input("ramadan_dates")
def on_dates(ctx, text, data):
    ds = re.findall(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text or "")
    try:
        start, end = [date(int(y), int(m), int(d)) for d, m, y in ds[:2]]
    except (ValueError, TypeError):
        return ctx.ask("ramadan_dates", "Не понял даты. Пример: «18.02.2027 – 19.03.2027».")
    if end < start or (end - start).days > 35:
        return ctx.ask("ramadan_dates", "Конец раньше начала или больше 35 дней. Пример: «18.02.2027 – 19.03.2027».")
    ctx.db.put("ramadan", {**(ctx.db.get("ramadan") or {}), "start": start.isoformat(), "end": end.isoformat()})
    ctx.db.audit(ctx.author(), "ramadan", f"{start} – {end}")
    return ramadan_screen(ctx)


@text_input("ramadan_time")
def on_time(ctx, text, data):
    ts = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text or "")
    if len(ts) != 2:
        return ctx.ask("ramadan_time", "Нужно два времени: «10:00 16:30».")
    m, e = [f"{int(h):02d}:{mi}" for h, mi in ts]
    ctx.db.put("ramadan", {**(ctx.db.get("ramadan") or {}), "morning": m, "evening": e})
    return ramadan_screen(ctx)
