"""Приложение одного клиента: анкета + крючки + база + экраны. Мессенджер сюда не входит —
он лишь передаёт нажатия и текст и показывает ответы (см. core/transport).

    app = App("<код_клиента>")
    replies = app.start(user_id)              # /start
    replies = app.press(user_id, "me")        # нажатие кнопки
    replies = app.text(user_id, "5000 за подмену")   # ввод текста, когда бот его ждёт

Показ (source.kind = seed): у каждой отрасли своя база (data/<клиент>/<отрасль>/kpi.db), заполняется сама
и обновляется в начале нового месяца. Каждый зритель сам выбирает роль, отрасль и редакцию.
"""
import importlib
import logging
import traceback
from datetime import date

from core import hooks as H
from core.access import roles as R
from core.config import INDUSTRIES, load_client
from core.data.db import DB, client_db_path
from core.calendar import month_of
from core.ui import registry as REG
from core.ui.kit import Button, Reply

log = logging.getLogger("kpi")

MODULES = ["debts", "reminders", "achievements", "staff", "payroll", "shifts", "documents", "tasks", "approval",
           "audit", "watchdog", "forecast", "call_today", "formulas", "ai", "locked"]


class App:
    def __init__(self, client_id, today=None, admins=(), data_dir=None):
        self.client_id = client_id
        self.hooks = H.load_rules(client_id)
        self.cfg = load_client(client_id, hooks=self.hooks)
        self.demo = self.cfg.source.get("kind") == "seed"
        self._today = today
        self.admins = {int(a) for a in admins}
        self.data_dir = data_dir
        self._dbs = {}
        importlib.import_module("core.ui.screens")
        importlib.import_module("core.jobs.briefs")
        for m in MODULES:
            importlib.import_module(f"modules.{m}")

    # ---------- время и база ----------

    def today(self):
        return self._today or date.today()

    def db(self, industry=None):
        if not self.demo:
            if None not in self._dbs:
                path = client_db_path(self.client_id) if not self.data_dir else self.data_dir / self.client_id / "kpi.db"
                self._dbs[None] = DB.open(path)
            return self._dbs[None]
        industry = industry if industry in INDUSTRIES else self.cfg.industry
        db = self._dbs.get(industry)
        meta = db.get("seeded") if db else None
        if db is None or not meta or meta.get("months", [""])[-1] != month_of(self.today()):
            db = self._reseed(industry)
        return db

    def _reseed(self, industry):
        seed = importlib.import_module(f"clients.{self.client_id}.seed").seed   # наполнитель — в папке клиента
        if self.data_dir:
            path = self.data_dir / self.client_id / industry / "kpi.db"
        else:
            path = client_db_path(self.client_id).parent / industry / "kpi.db"
        old = self._dbs.pop(industry, None)
        if old:
            old.close()
        if path.exists():
            db = DB.open(path)
            meta = db.get("seeded")
            if meta and meta.get("months", [""])[-1] == month_of(self.today()):
                self._dbs[industry] = db
                return db
            db.close()
            for p in path.parent.glob("kpi.db*"):
                p.unlink()
        db = DB.open(path)
        seed(db, self.cfg, industry, today=self.today())
        self._dbs[industry] = db
        return db

    def reset_demo(self, industry):
        """Кнопка «Начать заново»: стереть демо-базу отрасли и заполнить свежими цифрами."""
        db = self._dbs.pop(industry, None)
        if db:
            path = db.c.execute("PRAGMA database_list").fetchone()[2]
            db.close()
            from pathlib import Path
            for p in Path(path).parent.glob("kpi.db*"):
                p.unlink()
        return self.db(industry)

    # ---------- вход ----------

    def ctx(self, user_id):
        from core.ui.context import Ctx
        return Ctx(self, user_id)

    def start(self, user_id, name=""):
        ctx = self.ctx(user_id)
        ctx.session.pop("await", None)
        ctx.save()
        return self._run(ctx, "home", ())

    def press(self, user_id, data):
        ctx = self.ctx(user_id)
        ctx.session.pop("await", None)
        sid, *args = data.split(":")
        return self._run(ctx, sid, args)

    def text(self, user_id, text):
        ctx = self.ctx(user_id)
        wait = ctx.session.pop("await", None)
        ctx.save()
        if not wait:
            fn = REG.INPUTS.get("_free")
            if fn:
                r = fn(ctx, text, {})
                if r:
                    return self._finish(ctx, r, "free")
            return self._run(ctx, "home", ())
        fn = REG.INPUTS.get(wait["kind"])
        if not fn:
            return self._run(ctx, "home", ())
        try:
            return self._finish(ctx, fn(ctx, text, wait.get("data") or {}), wait["kind"])
        except Exception:
            log.error("ввод %s: %s", wait["kind"], traceback.format_exc())
            return [Reply(text="⚠️ Не получилось. Попробуйте ещё раз.", buttons=[[Button("🏠 Главная", "home")]])]

    def _run(self, ctx, sid, args):
        if sid not in REG.SCREENS:
            sid, args = "home", ()
        fn, module = REG.SCREENS[sid]
        if module and not ctx.can(module):
            sid, args, fn = "lock", (module,), REG.SCREENS["lock"][0]
        try:
            r = fn(ctx, *args)
        except Exception:
            log.error("экран %s %s: %s", sid, args, traceback.format_exc())
            r = Reply(text="⚠️ Экран не открылся. Мы уже знаем об этом.")
        return self._finish(ctx, r, sid)

    def _finish(self, ctx, r, sid):
        out = r if isinstance(r, list) else [r]
        last = out[-1]
        if sid != "home" and not last.ask and not any(b.to == "home" for b in last.all_buttons()):
            last.buttons = list(last.buttons) + [[Button("🏠 Главная", "home")]]
        ctx.save()
        return out

    # ---------- рассылки ----------

    def recipients(self, roles=None):
        """Сотрудники с привязанным мессенджером (для рассылок)."""
        db = self.db()
        return [e for e in db.employees() if e["tg_id"] and (roles is None or e["role"] in roles)]

    def role_title(self, role):
        return self.cfg.role_title(role) if role in R.ROLES else role
