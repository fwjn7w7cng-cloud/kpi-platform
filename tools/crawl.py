"""Робот-тестировщик: проходит бота за каждую роль (и редакцию), нажимает все кнопки до заданной глубины,
рисует каждый экран картинкой. Ловит ошибки, которые человек нашёл бы только тыкая вручную.

    python3 tools/crawl.py demo            # отчёт в консоль
    python3 tools/crawl.py demo --png out  # ещё и картинки всех экранов в папку out
"""
import logging
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.app import App                     # noqa: E402
from core.ui.views import to_png             # noqa: E402

SKIP = {"sw:reset", "month:approve", "month:review", "month:reopen", "slips"}   # меняют общее состояние — отдельные тесты


class Errors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.items = []

    def emit(self, record):
        self.items.append(record.getMessage())


def crawl(app, uid, depth=4, png_dir=None, tag="", limit=500):
    seen, errors, shots = set(), [], 0
    queue = [(r, 0, "start") for r in app.start(uid)]
    while queue and len(seen) < limit:
        reply, d, path = queue.pop(0)
        if reply.view is not None:
            try:
                img = to_png(reply.view)
                shots += 1
                if png_dir:
                    name = f"{tag}_{path}".replace(":", "-").replace("/", "_")[:90]
                    (png_dir / f"{name}.png").write_bytes(img)
            except Exception as e:                         # noqa: BLE001
                errors.append(f"{tag} {path}: картинка не рисуется: {e!r}")
        if reply.text.startswith("⚠️"):
            errors.append(f"{tag} {path}: {reply.text}")
        if d >= depth or reply.to:
            continue
        for b in reply.all_buttons():
            if not b.to or b.to in seen or b.to in SKIP or b.to.startswith(("sw:", "mon:", "appr:", "bk:")):
                continue
            seen.add(b.to)
            for r in app.press(uid, b.to):
                queue.append((r, d + 1, b.to))
    return seen, errors, shots


def run(client="demo", png=None, today=None):
    handler = Errors()
    logging.getLogger("kpi").addHandler(handler)
    tmp = Path(tempfile.mkdtemp())
    app = App(client, today=today or date(2026, 10, 27), admins=[999], data_dir=tmp)
    png_dir = Path(png) if png else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)
    total, errs, shots = 0, [], 0
    roles = ["rep", "sv", "head", "owner", "accountant", "staff", "admin"]
    for ed in ("control", "start"):
        for i, role in enumerate(roles):
            uid = 1000 + i + (100 if ed == "start" else 0)
            app.press(uid, f"sw:ed:{ed}")
            app.press(uid, f"sw:role:{role}")
            seen, e, s = crawl(app, uid, png_dir=png_dir if ed == "control" else None, tag=f"{ed}-{role}")
            total += len(seen)
            errs += e
            shots += s
    errs += [f"лог: {x[:300]}" for x in handler.items]
    return total, shots, errs


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--png") + 1] if "--png" in sys.argv else None
    total, shots, errs = run(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "demo", out)
    print(f"нажато кнопок: {total}, нарисовано экранов: {shots}, ошибок: {len(errs)}")
    for e in errs[:40]:
        print(" •", e)
