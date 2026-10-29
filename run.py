"""Запуск бота одного клиента:  python3 run.py <код_клиента>

Настройки и секреты — в clients/<код>/.env (в git не попадает):
  BOT_TOKEN        токен бота из @BotFather
  ADMIN_IDS        Telegram ID админов через запятую
  MESSENGER        telegram (по умолчанию) | max
  MAIL_USER, MAIL_PASSWORD, MAIL_HOST, MAIL_FROM   — если отчёты приходят на почту (source.kind: mail)
  DRIVE_FOLDER, GOOGLE_KEY                         — если из папки Google Диска (source.kind: drive)

Каждый клиент — отдельный процесс (deploy/kpi@.service): упал один — остальные работают.
"""
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(client):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater"):     # их адреса запросов содержат токен бота
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env(ROOT / "clients" / client / ".env")
    from core.app import App
    admins = [a for a in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if a]
    app = App(client, admins=admins)
    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit(f"Нет BOT_TOKEN в clients/{client}/.env")
    mailbox = drive = None
    kind = app.cfg.source.get("kind")
    if kind == "mail" and os.environ.get("MAIL_USER"):
        from core.ingest.mail import Mailbox
        mailbox = Mailbox(os.environ["MAIL_USER"], os.environ.get("MAIL_PASSWORD", ""),
                          os.environ.get("MAIL_HOST", "imap.gmail.com"), os.environ.get("MAIL_FROM", ""))
    if kind == "drive" and os.environ.get("DRIVE_FOLDER"):
        from core.ingest.drive import DriveFolder
        drive = DriveFolder(os.environ["DRIVE_FOLDER"], os.environ.get("GOOGLE_KEY", str(ROOT / "google_key.json")))
    if os.environ.get("MESSENGER", "telegram") == "max":
        from core.transport.max import build
        build(app, token)
        return
    from core.transport.telegram import build
    logging.getLogger("kpi").info("клиент %s: %s, редакция %s", client, app.cfg.name, app.cfg.edition)
    build(app, token, mailbox, drive).run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Запуск: python3 run.py <код_клиента>")
    main(sys.argv[1])
