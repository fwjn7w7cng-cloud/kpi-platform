"""Робот «стрелка и кнопки» (Адам 31.10: «проверь, что стрелка ведёт только назад, а кнопки выполняют свои функции»).

За каждую роль демо: главная и нижнее меню → каждый экран → каждая кнопка на нём. Проверяет:
  * экран открылся (нет «⚠️ Экран не открылся»);
  * «‹ Назад» ведёт ровно на экран, с которого пришли;
  * окно выбора (несколько вариантов на листе) — с «✅ ОК / ✖️ Отмена», а «Отмена» возвращает туда же, что и стрелка.

    python3 tools/nav_check.py            # все роли, отчёт в консоль
    python3 tools/nav_check.py owner rep  # только эти роли
"""
import logging
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.app import REPLACE, STEPLESS, App, window_of   # noqa: E402
from core.ui.screens import bottom_rows         # noqa: E402

ROLES = ["sw:role:owner", "sw:role:rep", "sw:role:sv", "sw:role:head", "sw:role:accountant", "sw:role:admin",
         "sw:pos:storekeeper", "sw:pos:driver", "sw:pos:buyer"]
# меняют общее состояние демо или уходят наружу — проверяются своими тестами
SKIP = re.compile(r"^(sw:reset|sw:role|sw:pos|sw:as|sw:who|viewas|month:|monq:ok|slips|bk:|adm|gs|pt|rqs|inv|cdc|cdk|sw:req|tfr|"
                  r"hrq:ok|hrz:ok|rmq:ok|paf:\d+:yes|pax|pai|sw:pdf|tfp|ucs:(password|key))")
TODAY = date(2026, 10, 31)


def sig(out):
    mine = [r for r in out if not r.to]
    if not mine:
        return "∅"
    for r in mine:
        if r.view:
            return "V:" + r.view.title
    return "T:" + (mine[-1].text or "")[:40]


def last(out):
    mine = [r for r in out if not r.to]
    return mine[-1] if mine else None


def buttons(r):
    return [(b.label, b.to) for b in (r.all_buttons() if r else []) if b.to]


def check(role, limit=4000, log=print):
    logging.disable(logging.CRITICAL)
    app = App("demo", today=TODAY, admins=[1], data_dir=Path(tempfile.mkdtemp()))
    uid = 1
    app.press(uid, role)
    problems, n = [], 0

    def go(path):
        nonlocal n
        out = app.press(uid, "home", top=True)
        for p in path:
            out = app.press(uid, p)
            n += 1
        return out

    roots = [to for row in bottom_rows(app.ctx(uid)) for _, to in row if to != "home" and not SKIP.match(to)]
    roots += [to for _, to in buttons(last(app.press(uid, "home", top=True))) if not SKIP.match(to)]
    seen = set()
    queue = [[r] for r in dict.fromkeys(roots)]
    while queue and n < limit:
        path = queue.pop(0)
        out = go(path)
        s = sig(out)
        r = last(out)
        if "Экран не открылся" in s:
            problems.append(("не открылся", path, s))
            continue
        key = (path[-1].split(":")[0], s)
        if key in seen:
            continue
        seen.add(key)
        back_to = path[:-1]
        while len(back_to) >= 1 and back_to[-1].split(":")[0] == path[-1].split(":")[0] and path[-1].split(":")[0] in REPLACE:
            back_to = back_to[:-1]                       # вкладки одного экрана — «назад» уходит с экрана целиком
        if path[-1].split(":")[0] in STEPLESS:          # прохождение теста — «назад» к списку тестов
            while back_to and back_to[-1].split(":")[0] in STEPLESS:
                back_to = back_to[:-1]
        prev = sig(go(back_to)) if back_to else sig(app.press(uid, "home", top=True))
        out = go(path)
        r = last(out)
        bk = [to for _, to in buttons(r) if to.startswith("bk:")]
        if bk and not (r and r.ask):
            got = sig(app.press(uid, bk[0]))
            n += 1
            if got != prev:
                problems.append(("стрелка не туда", path, f"{s} → {got}, ждали {prev}"))
        w = window_of(r) if r else None
        marks = [lb for lb, _ in buttons(r) if lb[:1] in ("☑", "☐")]
        if len(marks) >= 2 and not w:
            problems.append(("выбор без «ОК / Отмена»", path, s))
        if w:
            go(path)
            got = sig(app.press(uid, f"{w}:no"))
            n += 1
            if got != prev and not got.startswith("T:"):
                problems.append(("«Отмена» не туда", path, f"{s} → {got}, ждали {prev}"))
        if len(path) < 3:
            for _, to in buttons(r):
                if not SKIP.match(to) and to != "home" and not to.endswith((":ok", ":no")):
                    queue.append(path + [to])
    return n, len(seen), problems


def main(roles):
    bad = 0
    for role in roles:
        t = time.time()
        n, screens, problems = check(role)
        print(f"{role}: нажатий {n}, экранов {screens}, замечаний {len(problems)} ({time.time() - t:.0f} с)")
        for kind, path, what in problems[:20]:
            print(f"   {kind}: {' → '.join(path)} · {what}")
        bad += len(problems)
    return bad


if __name__ == "__main__":
    args = sys.argv[1:]
    roles = [r for r in ROLES if any(r.endswith(":" + a) for a in args)] if args else ROLES
    sys.exit(1 if main(roles) else 0)
