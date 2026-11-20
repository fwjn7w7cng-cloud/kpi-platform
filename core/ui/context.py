"""Контекст одного нажатия: кто смотрит, в какой роли, какой месяц, что ему видно.

Кто смотрит:
  настоящий клиент — сотрудник, чей мессенджер привязан (employees.tg_id); админы — из списка ADMIN_IDS;
  демо — любой зритель; роль, «как кто смотрим», отрасль и редакцию он выбирает сам (хранится в сессии).
"""
from core.access import editions as E
from core.access import roles as R
from core.calendar import month_of, shift_month
from core.kpi.engine import Kpi
from core.pay.engine import Payroll
from core.ui.kit import Reply


class Ctx:
    def __init__(self, app, user_id):
        self.app, self.user_id = app, user_id
        self.today = app.today()
        self.demo = app.demo
        self.session = self._load_session()
        self.industry = self.session.get("industry", app.cfg.industry) if self.demo else app.cfg.industry
        from core.config import SEGMENTS
        from core.ui.retail import has_shops
        seg = self.session.get("segment") if self.demo else None       # Адам 20.11: опт / опт + розница / розница
        if seg not in SEGMENTS:
            seg = app.cfg.segment if not self.demo and app.cfg.segment in SEGMENTS else None
        self.segment = seg or ("opt" if self.demo or not has_shops(app.cfg) else "mix")
        self.office = app.office()
        guest = None
        if self.demo and app.demo_gate:              # демо по приглашению: у каждого гостя своя копия фирмы
            from core.demo import has_access
            guest = user_id if has_access(app, self.office, user_id, self.today) else None   # без доступа — без копии
        self.db = app.db(self.industry, guest=guest)
        cfg = app.cfg
        if self.demo:                                 # конструктор демо: набор живёт в сессии зрителя
            if isinstance(self.session.get("funcs"), list):
                cfg = cfg.with_functions(self.session["funcs"], self.session.get("depts"))
            elif isinstance(self.session.get("packages"), dict):
                cfg = cfg.with_packages(self.session["packages"])
            elif self.session.get("edition") in E.PRESETS:
                cfg = cfg.with_edition(self.session["edition"])
            if not isinstance(self.session.get("funcs"), list):    # свой набор не собран — учёт в Алапе тоже показываем
                import dataclasses
                cfg = dataclasses.replace(cfg, shown=frozenset(E.ACC_FUNCS))
        else:                                         # одобренные Адамом добавления / удаления функций (core/billing)
            from core.billing import acc_upgrade
            b = acc_upgrade(cfg, self.office)
            if isinstance(b.get("functions"), list):
                cfg = cfg.with_functions(b["functions"], cfg.depts)
        from core.ui.retail import apply_edits
        self.cfg = apply_edits(cfg, self.db)          # магазины, переименованные / добавленные в приложении (Адам 20.11)
        if not self.demo:
            from core.payedit import apply_cfg
            self.cfg = apply_cfg(self.cfg, self.db)   # цифры зарплаты, поправленные в пульте (Адам 20.11)
        self.db.hide = self._hidden() if self.demo else frozenset()
        self.month = self.session.get("month") or month_of(self.today)
        if self.month > month_of(self.today):
            self.month = month_of(self.today)
        self._kpi, self._pay = {}, {}
        self.photo = ""
        self.form = None                  # данные формы одной страницей из приложения (core/ui/builder.py)
        self.shops_only = None
        self._resolve()
        self.shops_only = self._shops_limit()

    def _shops_limit(self):
        """«Видит магазины» (Адам 20.11): у руководителя отмечены не все магазины — видит только их. None — без ограничения."""
        from core.ui.retail import SEES_KEY, limit_to, shops
        if not self.me or self.role in ("admin", "accountant"):             # владелец — тоже, если ему отмечены магазины (Адам 20.11)
            return None
        mine = (self.db.get(SEES_KEY) or {}).get(str(self.me["id"]))
        if not mine:
            return None
        allowed = {s for s, _ in shops(self.cfg) if s in set(mine)}
        self.cfg = limit_to(self.cfg, allowed)
        return allowed

    def _hidden(self):
        """Демо (Адам 17.11: «что в сборке — то и везде»): отделы, выключенные в «Собрать», и люди сверх выбранного числа
        не показываются нигде — ни в фонде, ни в командах, ни в ролях. Директор, админ и бухгалтер остаются всегда."""
        from core.ui.retail import segment_hide
        seg = segment_hide(self.cfg, self.db._employees(active=None), self.segment)
        if not isinstance(self.session.get("funcs"), list):
            return frozenset(seg)
        counts = self.session.get("counts") or {}
        keep_roles = {"owner", "admin", "accountant"}
        lead = {"head": 0, "sv": 1}
        hide, by = set(seg), {}
        for e in self.db._employees(active=None):
            if e["id"] in seg:
                continue
            d = self.cfg.dept(e["dept"])
            code = E.dept_package(d.kind) if d else None
            if e["role"] in keep_roles or not code:
                continue
            if not self.cfg.dept_on(e["dept"]):
                hide.add(e["id"])
            else:
                by.setdefault(code, []).append(e)
        for code, es in by.items():
            n = counts.get(code)
            if n is None:
                continue
            es.sort(key=lambda e: (lead.get(e["role"], 2), e["id"]))      # руководители — первыми, остальные по порядку
            hide |= {e["id"] for e in es[int(n):]}
        return frozenset(hide)

    # ---------- сессия ----------

    def _store(self):
        """Где хранить сессии: в демо — в отдельной базе (общей для всех зрителей), иначе — в базе клиента."""
        return self.app.office()

    def _load_session(self):
        return self._store().session(self.user_id)

    def save(self):
        self._store().save_session(self.user_id, self.session)

    def ask(self, kind, prompt, data=None, buttons=None):
        """Попросить ввод текстом: следующий текст пользователя уйдёт в обработчик kind."""
        self.session["await"] = {"kind": kind, "data": data or {}}
        self.save()
        from core.ui.kit import Button
        return Reply(text=prompt, ask=prompt, buttons=buttons or [[Button("✖️ Отмена", "home")]])

    # ---------- кто смотрит ----------

    def _resolve(self):
        self.admin = self.user_id in self.app.admins
        self.me = None
        if self.demo:
            role = self.session.get("role", "owner")
            emp_id = self.session.get("as")
            emp = self.db.employee(emp_id) if emp_id and emp_id not in self.db.hide else None   # не в сборке — другой человек
            if role == "admin":
                self.admin, self.role, self.me = True, "admin", None
                return
            if not emp or (role != emp["role"] and not (role == "rep" and emp["role"] == "newbie")):
                emp = self._default_for(role)
            self.me, self.role = emp, (emp["role"] if emp else role)
            return
        emp = self.db.employee_by_tg(self.user_id)
        if emp and emp["active"] and emp["role"] == "admin":
            self.admin = True                          # «завхоз» бота у клиента: ведёт людей, доступ и данные
        view_as = self.session.get("as") if self.admin else None
        if view_as:
            emp = self.db.employee(view_as)
            self.me, self.role = emp, emp["role"] if emp else "admin"
            return
        if self.admin:
            self.me, self.role = emp, "admin"
            return
        if emp and emp["active"]:
            self.me, self.role = emp, emp["role"]
        else:
            self.me, self.role = None, "guest"

    def _default_for(self, role):
        es = self.db.employees()
        from core.ui.retail import shop_dept
        sd = shop_dept(self.cfg)
        if sd and self.segment != "retail":            # «Торговый» и «Супервайзер» — из опта; магазины — своими должностями
            es = [e for e in es if e["dept"] != sd] or es
        if role == "rep":
            reps = [e for e in es if e["role"] == "rep"]
            if reps:
                k = self.kpi()
                return max(reps, key=lambda e: (k.emp(e["id"])["sales"].pct or 0))
        if role == "head":
            heads = [e for e in es if e["role"] == "head"]
            sales = [e for e in heads if self.cfg.dept(e["dept"]) and self.cfg.dept(e["dept"]).kind == "sales"]
            return (sales or heads or [None])[0]
        return next((e for e in es if e["role"] == role), None)

    @property
    def scope(self):
        if self.role in ("admin", "owner", "accountant"):
            return "all"
        return R.ROLES.get(self.role, {}).get("scope", "self")

    @property
    def dept_kind(self):
        d = self.cfg.dept(self.me["dept"]) if self.me else None
        return d.kind if d else ""

    @property
    def duty(self):
        """Обязанность по должности: driver / dispatcher / hr / ''."""
        pos = self.cfg.position(self.me["position"]) if self.me else None
        return (pos or {}).get("duty", "")

    @property
    def position_name(self):
        pos = self.cfg.position(self.me["position"]) if self.me else None
        return (pos or {}).get("name", "")

    def name(self):
        if self.me:
            return self.me["fio"]
        return "Администратор" if self.role == "admin" else "Гость"

    def sees(self, emp):
        """Можно ли смотреть данные этого сотрудника."""
        if isinstance(emp, int):
            emp = self.db.employee(emp)
        if not emp:
            return False
        s = self.scope
        if s == "all":
            return True
        if not self.me:
            return False
        if getattr(self, "shops_only", None) is not None and emp["team"]:     # «видит магазины»: только люди своих магазинов
            from core.ui.retail import shop_dept
            t = emp["team"]
            sid = t if emp["dept"] == shop_dept(self.cfg) else t.rsplit("_", 1)[-1] if "_s" in t else None
            if sid is not None and sid not in self.shops_only:
                return False
        if s == "dept":
            return emp["dept"] == self.me["dept"]
        if s == "team":
            return emp["team"] == self.me["team"] and emp["dept"] == self.me["dept"]
        return emp["id"] == self.me["id"]

    def can(self, module):
        return module in self.cfg.modules or module == ""

    # ---------- расчёты (с кэшем на одно нажатие) ----------

    def kpi(self, month=None):
        month = month or self.month
        if month not in self._kpi:
            self._kpi[month] = self.app.memo(self, "kpi", lambda: Kpi(self.cfg, self.db, month, self.today, self.app.hooks),
                                             month)
        return self._kpi[month]

    def pay(self, month=None):
        month = month or self.month
        if month not in self._pay:
            self._pay[month] = Payroll(self.cfg, self.db, self.kpi(month), self.app.hooks)
        return self._pay[month]

    def prev_month(self):
        return shift_month(self.month, -1)

    def is_current(self):
        return self.month == month_of(self.today)

    def author(self):
        """Подпись для журнала и правок."""
        role = self.cfg.role_title(self.role) if self.role in R.ROLES else self.role
        return f"{self.name()} ({role})"
