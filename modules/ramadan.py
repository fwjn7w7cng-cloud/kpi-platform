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
from core.ui.kit import Button, Reply, pct
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
    kb = [[Button("🕘 Время рассылок", "rmd:time"),
           Button("⏰ Штраф за опоздания: " + ("выкл" if r.get("late_free") else "вкл"), "rmd:late")],
          [Button(("• " if abs(r["plan"] - p / 100) < 1e-9 else "") + f"План {p}%", f"rmd:plan:{p}") for p in (100, 90, 85, 80)],
          [Button("📅 Другие даты", "rmd:on"), Button("✖️ Выключить", "rmd:off")]]
    return Reply(view=v, buttons=kb)


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
