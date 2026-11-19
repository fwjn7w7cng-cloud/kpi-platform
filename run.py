"""Запуск приложения одного клиента:  python3 run.py <код_клиента> [--web] [порт]

Только своё приложение (Адам 20.11: Telegram и MAX убраны совсем). Настройки и секреты — в clients/<код>/.env (в git не попадает):
  WEB_PORT, WEB_URL, WEB_ADMIN_KEY                 — порт, адрес и ключ входа администратора
  ADMIN_IDS        номера админов через запятую (необязательно)
  MAIL_USER, MAIL_PASSWORD, MAIL_HOST, MAIL_FROM   — если отчёты приходят на почту (source.kind: mail)
  DRIVE_FOLDER, GOOGLE_KEY                         — если из папки Google Диска (source.kind: drive)

Каждый клиент — отдельный процесс (deploy/kpi-web@.service): упал один — остальные работают.
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


def web_port_of(client):
    """Порт приложения клиента — из его clients/<код>/.env (18.11: раньше брался до чтения .env, и новый клиент садился на порт
    демо 8101). Нет WEB_PORT — не запускаемся: чужой порт занимать нельзя. Только демо по старинке — 8101."""
    load_env(ROOT / "clients" / client / ".env")
    raw = os.environ.get("WEB_PORT", "").strip()
    if not raw:
        if client == "demo":
            return 8101
        sys.exit(f"Нет WEB_PORT в clients/{client}/.env — приложение не запускаю, чтобы не занять чужой порт")
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"WEB_PORT в clients/{client}/.env — не число: {raw!r}")


def main(client, web_port=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env(ROOT / "clients" / client / ".env")
    from core.app import App
    from core.transport.web import WebApi, serve
    admins = [a for a in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if a]
    app = App(client, admins=admins, demo_gate=True)       # демо — только по приглашению или заявке (core/demo.py)
    api = WebApi(app, admin_key=os.environ.get("WEB_ADMIN_KEY", ""), base_url=os.environ.get("WEB_URL", ""))
    panel = None
    if app.demo:                                   # приложение управления — адрес admin.<домен> на том же порту (core/control.py)
        panel = WebApi(app, admin_key=os.environ.get("WEB_ADMIN_KEY", ""), panel=True, lock=api.lock)
    logging.getLogger("kpi").info("клиент %s: %s, порт %s", client, app.cfg.name, web_port)
    serve(api, port=web_port, panel=panel)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--web"]          # --web по старой памяти служб — принимаем и не требуем
    if len(args) in (1, 2):
        main(args[0], web_port=int(args[1]) if len(args) == 2 else web_port_of(args[0]))
    else:
        sys.exit("Запуск: python3 run.py <код_клиента> [порт]")
