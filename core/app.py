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
           "quiz", "stock", "logistics", "returns", "warehouse", "fuel", "hr", "ramadan", "maximum"]


RESET = {"home", "mon"}          # главная и смена месяца: путь «Назад» начинается заново
TRAIL = 12
MAX_OPEN = 40                    # демо: сколько копий фирмы держать открытыми одновременно


class App:
    def __init__(self, client_id, today=None, admins=(), data_dir=None, demo_gate=False):
        self.client_id = client_id
        self.hooks = H.load_rules(client_id)
        self.cfg = load_client(client_id, hooks=self.hooks)
        self.demo = self.cfg.source.get("kind") == "seed"
        self._today = today
        self.admins = {int(a) for a in admins}
        self.data_dir = data_dir
        self.demo_gate = bool(demo_gate) and self.demo     # демо по приглашению / заявке (core/demo.py)
        self.bot_username = ""                             # ставит транспорт — для ссылок-приглашений
        self.web_url = ""                                  # адрес веб-приложения (веб-провод) — тоже для ссылок
        self.last_sid = ""                                 # какой экран открылся последним (веб-провод: главная или нет)
        self._dbs = {}
        self._memo = {}
        self.pending = []
        importlib.import_module("core.ui.screens")
        importlib.import_module("core.ui.tariff")
        importlib.import_module("core.onboard")
        importlib.import_module("core.people")
        importlib.import_module("core.reports")
        importlib.import_module("core.onec_link")
        importlib.import_module("core.demo")
        importlib.import_module("core.jobs.briefs")
        for m in MODULES:
            importlib.import_module(f"modules.{m}")

    # ---------- время и база ----------

    def today(self):
        return self._today or date.today()

    def db(self, industry=None, guest=None):
        """База клиента. В демо — своя копия выдуманной фирмы у каждого зрителя (guest) и отрасли;
        без guest — общая (для рассылок)."""
        if not self.demo:
            if None not in self._dbs:
                path = client_db_path(self.client_id) if not self.data_dir else self.data_dir / self.client_id / "kpi.db"
                self._dbs[None] = DB.open(path)
            return self._dbs[None]
        industry = industry if industry in INDUSTRIES else self.cfg.industry
        key = (guest, industry)
        db = self._dbs.pop(key, None)
        if db is not None:
            self._dbs[key] = db                       # самая свежая — в конец (закрываем самые старые)
        if db is None or not self._fresh(db.get("seeded")):
            db = self._reseed(industry, guest)
        return db

    def _seed_module(self):
        return importlib.import_module(f"clients.{self.client_id}.seed")

    def _fresh(self, meta):
        """Демо-база годится: заполнена в этом месяце и той же версией наполнителя (seed.VERSION)."""
        return bool(meta) and meta.get("months", [""])[-1] == month_of(self.today()) \
            and meta.get("v", 0) == getattr(self._seed_module(), "VERSION", 0)

    def _demo_base(self):
        return self.data_dir / self.client_id if self.data_dir else client_db_path(self.client_id).parent

    def _demo_path(self, industry, guest=None):
        base = self._demo_base()
        return (base / "guests" / str(guest) / industry if guest else base / industry) / "kpi.db"

    def office(self):
        """Где живут сессии, гости и отзывы: в демо — отдельная база (её не стирает «Начать заново»), иначе — база клиента."""
        if not self.demo:
            return self.db()
        if "office" not in self._dbs:
            self._dbs["office"] = DB.open(self._demo_base() / "demo.db")
        return self._dbs["office"]

    def _close_old(self, keep=MAX_OPEN):
        opened = [k for k in self._dbs if isinstance(k, tuple)]
        for k in opened[:max(0, len(opened) - keep)]:
            self._dbs.pop(k).close()
            self._memo.clear()

    def _reseed(self, industry, guest=None):
        seed = self._seed_module().seed                                         # наполнитель — в папке клиента
        path = self._demo_path(industry, guest)
        key = (guest, industry)
        self._memo.clear()
        old = self._dbs.pop(key, None)
        if old:
            old.close()
        self._close_old(MAX_OPEN - 1)
        if path.exists():
            db = DB.open(path)
            if self._fresh(db.get("seeded")):
                self._dbs[key] = db
                return db
            db.close()
            for p in path.parent.glob("kpi.db*"):
                p.unlink()
        db = DB.open(path)
        seed(db, self.cfg, industry, today=self.today())
        try:
            db.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")    # копия гостя — поменьше места на диске
        except Exception:                                     # noqa: BLE001
            pass
        self._dbs[key] = db
        return db

    def reset_demo(self, industry, guest=None):
        """Кнопка «Начать заново»: стереть демо-базу отрасли (у этого зрителя) и заполнить свежими цифрами."""
        self._memo.clear()
        db = self._dbs.pop((guest, industry), None)
        if db:
            db.close()
        for p in self._demo_path(industry, guest).parent.glob("kpi.db*"):
            p.unlink()
        return self.db(industry, guest)

    def drop_guest(self, guest):
        """Отключили гостя демо — его копии фирмы больше не нужны."""
        import shutil
        for k in [k for k in self._dbs if isinstance(k, tuple) and k[0] == guest]:
            self._dbs.pop(k).close()
        self._memo.clear()
        shutil.rmtree(self._demo_base() / "guests" / str(guest), ignore_errors=True)

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

    def commands(self):
        """Команды для кнопки «Меню» мессенджера: [(команда, подпись, экран)]."""
        out = [("start", "🏠 Главная", "home"), ("help", "❓ Как пользоваться", "help"),
               ("feedback", "🐞 Не работает / предложение", "fb")]
        if self.demo:
            out += [("role", "🎭 Сменить роль", "sw:role"), ("industry", "🏭 Сменить отрасль", "sw:ind"),
                    ("build", "🧩 Собрать бота и цена", "sw:ed"), ("restart", "🔄 Начать демо заново", "sw:reset")]
        return out

    def command(self, user_id, cmd):
        to = next((t for c, _, t in self.commands() if c == cmd), "home")
        return self.start(user_id) if to == "home" else self.press(user_id, to, top=True)

    def start(self, user_id, name="", arg=""):
        from core import demo
        ctx = self.ctx(user_id)
        if name:
            ctx.session["tgname"] = name[:60]
            ctx.save()
        g = demo.gate(ctx, arg=arg)
        if g is not None:
            return g
        hello = []
        if arg and not self.demo:                    # настоящий клиент: ссылка-приглашение сотруднику (core/people.py)
            from core import people
            emp = people.redeem(ctx, arg)
            hello = [Reply(text=f"✅ Здравствуйте, {emp['fio']}! Вы вошли в приложение — добавьте его на экран «Домой», "
                                "чтобы открывать одним нажатием." if emp else
                                "Ссылка-приглашение недействительна или устарела — попросите у администратора новую.")]
        ctx = self.ctx(user_id)                      # доступ мог только что открыться — уже со своей копией фирмы
        ctx.session.pop("await", None)
        ctx.session["trail"] = []
        ctx.save()
        extra, self.pending = self.pending, []         # сообщения другим людям, которые родились на входе (партнёру — «клиент вошёл»)
        return extra + hello + self._with_keyboard(user_id, self._run(ctx, "home", ()), force=True)

    def upload(self, user_id, name, data):
        """Файл отчёта из приложения («📥 Отчёты 1С») или мессенджера: принять и показать, что пришло (core/reports.py)."""
        from core import demo, reports
        ctx = self.ctx(user_id)
        g = demo.gate(ctx, press="up")
        if g is not None:
            return g
        self.last_sid = "up"
        return self._with_keyboard(user_id, self._finish(ctx, reports.receive(ctx, name, data), "up"))

    def press(self, user_id, data, top=False):
        """top — нажатие из нижнего меню: путь «Назад» начинается заново."""
        from core import demo
        ctx = self.ctx(user_id)
        g = demo.gate(ctx, press=data)
        if g is not None:
            return g
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
        from core import demo
        ctx = self.ctx(user_id)
        ctx.photo = photo
        g = demo.gate(ctx, text=text)
        if g is not None:
            return g
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
        self.last_sid = sid
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

    def admin_ids(self, db):
        """Кому идут сообщения администратору: ключ приложения (мы) + сотрудники с ролью «Администратор» у клиента."""
        out = set(self.admins)
        if not self.demo:
            out |= {e["tg_id"] for e in db.employees() if e["role"] == "admin" and e["tg_id"]}
        return sorted(out)

    def recipients(self, roles=None):
        """Сотрудники с привязанным мессенджером (для рассылок)."""
        db = self.db()
        return [e for e in db.employees() if e["tg_id"] and (roles is None or e["role"] in roles)]

    def role_title(self, role):
        return self.cfg.role_title(role) if role in R.ROLES else role
