"""Приём файлов: файл → переводчик клиента → нейтральные пачки → база.

У каждого клиента свой переводчик ``clients/<id>/parser.py`` с функцией

    def parse(name: str, data: bytes) -> list[Dataset]   # пусто — «это не наш отчёт»

Правила приёма (общие для всех):
  • тот же файл повторно (по хэшу содержимого) не принимается;
  • пачка за утверждённый месяц не принимается никогда;
  • автоматическая пачка за месяц на согласовании не принимается (ручной файл от админа — принимается);
  • пустая пачка прежние данные не затирает.
"""
import hashlib
import importlib
import logging
from dataclasses import dataclass, field

from core.calendar import month_of, month_title
from core.data.db import DataError

log = logging.getLogger("kpi")

KIND_TITLE = {"sales": "продажи", "debts": "долги", "payments": "деньги", "visits": "маршруты",
              "clients": "клиенты", "groups": "группы товаров"}


@dataclass
class Result:
    name: str
    ok: list = field(default_factory=list)        # строки «принято»
    skipped: list = field(default_factory=list)   # строки «пропущено, потому что…»
    errors: list = field(default_factory=list)

    def text(self):
        lines = [f"📄 {self.name}"]
        lines += [f"  ✅ {x}" for x in self.ok] + [f"  ⏭ {x}" for x in self.skipped] + [f"  ⚠️ {x}" for x in self.errors]
        return "\n".join(lines)


def parser_of(client_id):
    try:
        return importlib.import_module(f"clients.{client_id}.parser").parse
    except ModuleNotFoundError:
        return None


def ingest(client_id, db, files, manual=False, hooks=None):
    """files — [(имя, байты)]. Возвращает [Result] — что принято и что нет, по-человечески."""
    parse = parser_of(client_id)
    out = []
    for name, data in files:
        res = Result(name)
        out.append(res)
        if parse is None:
            res.errors.append("у клиента нет переводчика отчётов (clients/<id>/parser.py)")
            continue
        fp = hashlib.sha256(data).hexdigest()
        try:
            sets = parse(name, data) or []
        except Exception as e:                            # noqa: BLE001
            log.exception("переводчик %s: %s", client_id, name)
            res.errors.append(f"не разобрался в файле: {e}")
            continue
        if not sets:
            res.skipped.append("не похоже на отчёт для бота")
            continue
        for n, ds in enumerate(sets):
            what = f"{KIND_TITLE.get(ds.kind, ds.kind)} {ds.start:%d.%m}–{ds.end:%d.%m}"
            month = month_of(ds.end)
            st = db.month_status(month)
            if st == "approved":
                res.skipped.append(f"{what}: {month_title(month)} уже утверждён")
                continue
            if st == "review" and not manual:
                res.skipped.append(f"{what}: {month_title(month)} на согласовании — только файлом от админа")
                continue
            if not ds.rows and ds.kind not in ("clients", "groups"):
                res.skipped.append(f"{what}: пусто — прежние данные оставлены")
                continue
            ds.fingerprint = f"{fp}:{n}"
            ds.source = ds.source or name
            try:
                st = db.load(ds)
            except DataError as e:
                res.errors.append(f"{what}: {e}")
                continue
            if st[0] == "skip":
                res.skipped.append(f"{what}: {st[1]}")
                continue
            res.ok.append(f"{what}: строк {len(ds.rows)}" + (f" (заменено {st[2]})" if st[2] else ""))
            if hooks:
                hooks.run("ingest_after", db, ds)
        unknown = db.unknown_keys()
        if unknown and res.ok:
            res.skipped.append("нет в списке сотрудников: " + ", ".join(k.title() for k in unknown[:5]) + ("…" if len(unknown) > 5 else ""))
    return out
