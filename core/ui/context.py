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
        self.office = app.office()
        guest = None
        if self.demo and app.demo_gate:              # демо по приглашению: у каждого гостя своя копия фирмы
            from core.demo import has_access
            guest = user_id if has_access(app, self.office, user_id, self.today) else None   # без доступа — без копии
        self.db = app.db(self.industry, guest=guest)
        cfg = app.cfg
        if self.demo and self.session.get("edition") in E.EDITIONS:
            cfg = cfg.with_edition(self.session["edition"], app.cfg.addons if self.session["edition"] == app.cfg.edition else [])
        self.cfg = cfg
        self.month = self.session.get("month") or month_of(self.today)
        if self.month > month_of(self.today):
            self.month = month_of(self.today)
        self._kpi, self._pay = {}, {}
        self.photo = ""
        self._resolve()

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
            emp = self.db.employee(emp_id) if emp_id else None
            if role == "admin":
                self.admin, self.role, self.me = True, "admin", None
                return
            if not emp or (role != emp["role"] and not (role == "rep" and emp["role"] == "newbie")):
                emp = self._default_for(role)
            self.me, self.role = emp, (emp["role"] if emp else role)
            return
        emp = self.db.employee_by_tg(self.user_id)
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
