"""Окно выбора одного варианта из нескольких (Адам 31.10: «где на одном листе несколько вариантов — ОК и Отмена»).

    res = pick.one(ctx, f"pap:{emp_id}", act, value, [(код, подпись), …], текущий, "Должность для Иванова")
    if res.done:            # нажали «ОК» — res.value выбранный код
        ...
    elif res.cancel:        # «Отмена»
        ...
    else:
        return res.reply    # само окно: варианты с отметкой ☑ / ☐, внизу «✅ ОК / ✖️ Отмена»

Кнопки: <prefix>:t:<код> — отметить, <prefix>:ok — применить, <prefix>:no — выйти. Черновик — в сессии (pick).
Стрелка «‹ Назад» в окне — на экран, откуда открыли (core/app.is_window).
"""
from dataclasses import dataclass

from core.ui.kit import Button, Reply, ok_row
from core.ui.views import View


@dataclass
class Picked:
    reply: object = None
    value: str = None
    done: bool = False
    cancel: bool = False


def one(ctx, prefix, act, value, options, current, title, sub="", note="", extra=None):
    d = ctx.session.get("pick")
    if act is None or not isinstance(d, dict) or d.get("k") != prefix:
        d = {"k": prefix, "v": current}
    codes = [str(c) for c, _ in options]
    if act == "no":
        ctx.session.pop("pick", None)
        ctx.save()
        return Picked(cancel=True)
    if act == "ok":
        ctx.session.pop("pick", None)
        ctx.save()
        return Picked(done=True, value=d["v"])
    if act == "t" and str(value) in codes:
        d["v"] = value
    ctx.session["pick"] = d
    ctx.save()
    v = View(title, sub) if sub or note else None
    if v is not None and note:
        v.note(note)
    kb = [[Button(f"{'☑' if str(c) == str(d['v']) else '☐'} {t}", f"{prefix}:t:{c}")] for c, t in options]
    kb += extra or []
    kb.append(ok_row(prefix))
    return Picked(reply=Reply(view=v, buttons=kb) if v is not None else Reply(text=title, buttons=kb))
