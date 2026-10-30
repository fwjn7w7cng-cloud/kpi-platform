"""Фоновая работа приложения клиента (веб-провод): рассылки по расписанию и забор отчётов 1С без кнопки (Адам 31.10).

  каждую минуту  — рассылки, которым пора (core/jobs/schedule.py: утренняя сводка, напоминания…) → лента + 🔔 на телефон;
  раз в 10 минут — источники отчётов из анкеты (source.kind): folder — папка на сервере (core/ingest/folder.py),
                   mail — почта (MAIL_*), drive — Google Диск (DRIVE_FOLDER) → общий конвейер → журнал «🧪 Что пришло»;
                   если что-то принято или не прочиталось — администраторам сообщение «📬 Новые отчёты 1С».
Автоматическая пачка за месяц на согласовании не принимается (правило конвейера) — только кнопкой «📥 Отчёты».
В демо ничего этого нет: цифры даёт наполнитель, людей нет.
"""
import logging
import os
from datetime import datetime
from pathlib import Path

from core.ingest.pipeline import ingest
from core.jobs import schedule
from core.ui.kit import Button, Reply

log = logging.getLogger("kpi")
PULL_EVERY = 10          # минут


def sources(app, env=None):
    """Источники отчётов по анкете и .env клиента: [(подпись, источник)]."""
    env = env if env is not None else os.environ
    src = app.cfg.source or {}
    kind = src.get("kind")
    out = []
    if kind != "seed":                    # своя папка есть у каждого клиента: сетевая папка и курьер кладут файлы в неё
        from core.ingest.folder import InboxFolder
        from core.onec_link import inbox_dir
        out.append(("папка", InboxFolder(inbox_dir(app))))
    if kind == "mail" and env.get("MAIL_USER"):
        from core.ingest.mail import Mailbox
        out.append(("почта", Mailbox(env["MAIL_USER"], env.get("MAIL_PASSWORD", ""), env.get("MAIL_HOST", "imap.gmail.com"),
                                     env.get("MAIL_FROM", ""))))
    if kind == "drive" and env.get("DRIVE_FOLDER"):
        from core.ingest.drive import DriveFolder
        out.append(("Диск", DriveFolder(env["DRIVE_FOLDER"], env.get("GOOGLE_KEY", "google_key.json"))))
    return out


class Worker:
    def __init__(self, app, deliver, srcs=None):
        """deliver(replies) — разнести сообщения адресатам (веб: лента + уведомление)."""
        self.app, self.deliver = app, deliver
        self.srcs = sources(app) if srcs is None else srcs
        self.last_pull = None

    # ---------- рассылки ----------

    def tick(self, now: datetime):
        """Раз в минуту: рассылки, которым пора, и забор отчётов раз в PULL_EVERY минут."""
        if self.app.demo:
            return []
        out = []
        for j in schedule.due(self.app, now):
            try:
                out += schedule.run(self.app, j)
            except Exception:                         # noqa: BLE001
                log.exception("рассылка %s", j.name)
        if self.srcs and (self.last_pull is None or (now - self.last_pull).total_seconds() >= PULL_EVERY * 60 - 5):
            self.last_pull = now
            out += self.pull()
        if out:
            self.deliver(out)
        return out

    # ---------- отчёты ----------

    def pull(self):
        """Забрать файлы из всех источников, принять, записать в журнал. → сообщения администраторам."""
        from core import reports
        db = self.app.db()
        results = []
        for title, s in self.srcs:
            try:
                results += self._take(title, s, db)
            except Exception as e:                    # noqa: BLE001
                log.exception("источник отчётов: %s", title)
                results.append((f"{title}: не удалось забрать файлы", None, str(e)[:200]))
        if not results:
            return []
        lines, accepted, bad = [], 0, 0
        for name, res, err in results:
            if res is None:
                lines.append(f"⚠️ {name}: {err}")
                bad += 1
                continue
            reports.log_result(db, name, res, "автоматически")
            accepted += len(res.ok)
            bad += len(res.errors)
            mark = "✅" if res.ok else "⚠️" if res.errors else "⏭"
            lines.append(f"{mark} {name}: " + "; ".join((res.ok + res.errors + res.skipped)[:2]))
        if not accepted and not bad:
            return []                                 # одни повторы — не беспокоим
        text = "📬 Новые отчёты 1С\n" + "\n".join(lines[:15])
        kb = [[Button("🧪 Что пришло", "upl")]] + ([[Button("🧾 Нет в расчёте", "pak")]] if db.unknown_keys() else [])
        return [Reply(text=text[:3500], buttons=kb, to=a, edit=False) for a in self.app.admin_ids(db)]

    def _take(self, title, s, db):
        from core.ingest.folder import InboxFolder
        out = []
        if isinstance(s, InboxFolder):
            for name, data, path in s.fetch():
                res = ingest(self.app.client_id, db, [(name, data)], False, self.app.hooks)[0]
                s.done(path, ok=not res.errors)
                out.append((name, res, None))
            return out
        if hasattr(s, "mark_seen"):                   # почта
            files, uids = s.fetch()
            for res in ingest(self.app.client_id, db, files, False, self.app.hooks):
                out.append((res.name, res, None))
            if uids:
                s.mark_seen(uids)
            return out
        seen = db.get("drive_seen", {}) or {}         # Google Диск
        files, seen, errs = s.fetch(seen)
        for res in ingest(self.app.client_id, db, files, False, self.app.hooks):
            out.append((res.name, res, None))
        db.put("drive_seen", seen)
        out += [(f"Диск", None, e) for e in errs or []]
        return out
