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

MODULES = ["debts", "reminders", "achievements", "staff", "payroll", "tasks", "approval",
           "audit", "watchdog", "forecast", "call_today", "formulas", "feedback", "clients", "route", "plans",
           "quiz", "stock", "logistics", "returns", "ramadan", "locked"]


RESET = {"home", "mon"}          # главная и смена месяца: путь «Назад» начинается заново
TRAIL = 12


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
        self._memo = {}
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
        self._memo.clear()
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
        self._memo.clear()
        db = self._dbs.pop(industry, None)
        if db:
            path = db.c.execute("PRAGMA database_list").fetchone()[2]
            db.close()
            from pathlib import Path
            for p in Path(path).parent.glob("kpi.db*"):
                p.unlink()
        return self.db(industry)

    def memo(self, ctx, name, fn, month=None):
        """Кэш тяжёлых расчётов между нажатиями: живёт, пока в базе ничего не поменялось.
        Ключ — расчёт, база, месяц, сегодня, редакция и версия данных."""
        db = ctx.db
        key = (name, id(db), month or ctx.month, ctx.today, ctx.cfg.edition, frozenset(ctx.cfg.modules), db.rev())
        if key not in self._memo:
            if len(self._memo) > 400:
                self._memo.clear()
            self._memo[key] = fn()
        return self._memo[key]

    # ---------- вход ----------

    def ctx(self, user_id):
        from core.ui.context import Ctx
        return Ctx(self, user_id)

    def start(self, user_id, name=""):
        ctx = self.ctx(user_id)
        ctx.session.pop("await", None)
        ctx.session["trail"] = []
        ctx.save()
        return self._with_keyboard(user_id, self._run(ctx, "home", ()), force=True)

    def press(self, user_id, data, top=False):
        """top — нажатие из нижнего меню: путь «Назад» начинается заново."""
        ctx = self.ctx(user_id)
        ctx.session.pop("await", None)
        trail = [] if top else list(ctx.session.get("trail") or [])
        sid, *args = data.split(":")
        if sid == "bk":                              # «‹ Назад»: вернуться на экран из пути
            n = int(args[0]) if args and args[0].isdigit() else 0
            trail = trail[:n]
            data = trail[-1] if trail else "home"
            sid, *args = data.split(":")
        if sid in RESET:
            trail = []
        rev = ctx.db.rev()
        out = self._run(ctx, sid, args, finish=False)
        mine = [r for r in out if not r.to]
        # Экран можно открыть ещё раз кнопкой «Назад», только если он ничего не менял и никому не писал.
        safe = sid not in RESET and ctx.db.rev() == rev and len(mine) == len(out) and not (mine and mine[-1].ask)
        if safe and (not trail or trail[-1] != data):
            trail.append(data)
        ctx.session["trail"] = trail[-TRAIL:]
        back = len(trail) - 1 if safe else len(trail)
        return self._with_keyboard(user_id, self._finish(ctx, out, sid, back=back))

    def _with_keyboard(self, user_id, out, force=False):
        """Нижнее меню: прислать заново, когда оно поменялось (сменилась роль, редакция) или по /start."""
        from core.ui.screens import bottom_keyboard
        ctx = self.ctx(user_id)
        kb = bottom_keyboard(ctx)
        if kb and (force or ctx.session.get("kb") != kb):
            ctx.session["kb"] = kb
            ctx.save()
            first = next((r for r in out if not r.to), None)
            if first is not None:
                first.keyboard = kb
        return out

    def text(self, user_id, text, photo=""):
        """Текст (или фото с подписью — photo: id файла в мессенджере)."""
        ctx = self.ctx(user_id)
        ctx.photo = photo
        from core.ui.screens import bottom_target
        to = bottom_target(ctx, text) if not photo else None
        if to:                                       # кнопка нижнего меню — даже если бот ждал ответ текстом
            return self.press(user_id, to, top=True)
        wait = ctx.session.pop("await", None)
        ctx.save()
        if not wait:
            for _, module, fn in REG.FREE:
                if module and not ctx.can(module):
                    continue
                try:
                    r = fn(ctx, text)
                except Exception:
                    log.error("свободный текст: %s", traceback.format_exc())
                    r = None
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

    def _run(self, ctx, sid, args, finish=True):
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
        out = r if isinstance(r, list) else [r]
        return self._finish(ctx, out, sid) if finish else out

    def _finish(self, ctx, r, sid, back=None):
        """Под последним своим сообщением — «‹ Назад» (на прошлый экран пути; «Главная» есть в нижнем меню)."""
        out = r if isinstance(r, list) else [r]
        mine = [x for x in out if not x.to]
        last = mine[-1] if mine else None
        if back is None:                             # ответ на ввод текста — назад на экран, с которого спросили
            back = len(ctx.session.get("trail") or [])
        if last and sid not in RESET and not last.ask and \
                not any(b.to == "home" or (b.to or "").startswith("bk:") for b in last.all_buttons()):
            last.buttons = list(last.buttons) + [[Button("‹ Назад", f"bk:{max(back, 0)}")]]
        ctx.save()
        return out

    # ---------- рассылки ----------

    def recipients(self, roles=None):
        """Сотрудники с привязанным мессенджером (для рассылок)."""
        db = self.db()
        return [e for e in db.employees() if e["tg_id"] and (roles is None or e["role"] in roles)]

    def role_title(self, role):
        return self.cfg.role_title(role) if role in R.ROLES else role
