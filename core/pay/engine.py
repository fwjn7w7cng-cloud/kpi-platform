"""Расчёт зарплаты сотрудника за месяц: кубики его схемы + крючки клиента + принятые правки.

    pay = Payroll(cfg, db, kpi, hooks)
    slip = pay.calc(emp)          # Payslip: строки, итог, отработанные дни
    fund = pay.fund(ids)          # итог по людям

Схема берётся по должности сотрудника (positions[].pay в анкете); личные оклад и ставка — из карточки.
Табель (таблица timesheet) — отклонения от «работал как обычно»:
  absent   пропуск (день не оплачивается, без штрафа)
  truancy  прогул (день не оплачивается + штраф timesheet.absent_fine)
  half     полдня (оплачивается половина дня)
  late     опоздание, minutes → штраф по шкале timesheet.late
  fine     штраф суммой amount
  extra    работа в выходной (+1 оплачиваемый день)
  comment  просто комментарий
"""
from dataclasses import dataclass, field
from datetime import date

from core.pay import components as C

DAY_KINDS = {"absent", "truancy", "half", "late", "fine", "extra", "comment"}


@dataclass
class Payslip:
    emp: dict
    rows: list = field(default_factory=list)       # [(название, сумма, пояснение)]
    days: float = 0
    days_total: int = 0
    scheme: str = ""

    @property
    def total(self):
        return round(max(sum(r[1] for r in self.rows), 0))

    @property
    def minus(self):
        return [r for r in self.rows if r[1] < 0]


class Payroll:
    def __init__(self, cfg, db, kpi, hooks=None):
        self.cfg, self.db, self.kpi, self.hooks = cfg, db, kpi, hooks
        self.cal = kpi.cal
        self.salary_days = cfg.salary_days
        self.default_salary = (cfg.pay or {}).get("salary", 0)
        ts = (cfg.raw.get("timesheet") or {})
        self.late_scale = sorted(ts.get("late") or [])
        self.absent_fine = ts.get("absent_fine", 0)
        self._ts = {}
        for r in db.timesheet(self.cal.start, self.cal.end):
            self._ts.setdefault(r["emp_id"], []).append(r)
        self.positions = {p["id"]: p for p in (cfg.raw.get("positions") or [])}
        self.schemes = cfg.raw.get("pay_schemes") or {}
        self.edits = {}
        for r in db.q("SELECT * FROM edits WHERE month = ? AND status = 'ok'", kpi.month):
            self.edits.setdefault(r["emp_id"], []).append(r)
        self._cache = {}

    # ---------- дни ----------

    def _span(self, emp):
        """Рабочие дни месяца, когда человек числился (с даты приёма по дату увольнения), до сегодня."""
        hired = date.fromisoformat(emp["hired"]) if emp.get("hired") else date.min
        fired = date.fromisoformat(emp["fired"]) if emp.get("fired") else date.max
        days = [d for d in self.cal.passed_days() if hired <= d <= fired]
        paid = [d for d in self.cal.paid_holidays if hired <= d <= fired and (d < self.cal.today or self.cal.closed)]
        return days, paid

    def worked_days(self, emp):
        days, paid = self._span(emp)
        n = len(days) + len(paid)
        for r in self._ts.get(emp["id"], []):
            if r["kind"] in ("absent", "truancy"):
                n -= 1
            elif r["kind"] == "half":
                n -= 0.5
            elif r["kind"] == "extra":
                n += 1
        return max(n, 0)

    def missed_days(self, emp):
        return sum(1 for r in self._ts.get(emp["id"], []) if r["kind"] in ("absent", "truancy"))

    def fines(self, emp):
        """[(сумма, пояснение)] штрафов табеля."""
        if emp.get("role") == "newbie":
            return []
        out = []
        for r in self._ts.get(emp["id"], []):
            d = date.fromisoformat(r["day"])
            when = f"{d.day:02d}.{d.month:02d}"
            if r["kind"] == "late":
                fine = 0
                for minutes, amount in self.late_scale:
                    if r["minutes"] >= minutes:
                        fine = amount
                if fine:
                    out.append((fine, f"опоздание {when} {r['minutes']} мин"))
            elif r["kind"] == "truancy" and self.absent_fine:
                out.append((self.absent_fine, f"прогул {when}"))
            elif r["kind"] == "fine" and r["amount"]:
                out.append((r["amount"], f"{r['note'] or 'штраф'} {when}"))
        return out

    # ---------- расчёт ----------

    def scheme_of(self, emp):
        pos = self.positions.get(emp.get("position"), {})
        return emp.get("extra", {}).get("pay") or pos.get("pay") or "none"

    def calc(self, emp):
        if isinstance(emp, int):
            emp = self.kpi.employees[emp]
        if emp["id"] in self._cache:
            return self._cache[emp["id"]]
        name = self.scheme_of(emp)
        slip = Payslip(emp, days=self.worked_days(emp), days_total=self.cal.total, scheme=name)
        for comp in self.schemes.get(name, []):
            t = comp["type"]
            fn = C.BUILTIN.get(t)
            if fn is None and self.hooks and t in self.hooks.components:
                fn = self.hooks.components[t][0]
            if fn is None:
                slip.rows.append((f"⚠️ нет кубика «{t}»", 0, "проверьте анкету"))
                continue
            slip.rows.extend(_norm(fn(self, emp, comp)))
        if self.hooks:
            slip.rows.extend(_norm(self.hooks.run("pay_rows", self, emp, slip) or []))
        for e in self.edits.get(emp["id"], []):
            slip.rows.append(("Премия" if e["amount"] > 0 else "Удержание", e["amount"], e["reason"]))
        self._cache[emp["id"]] = slip
        return slip

    def fund(self, emps):
        return {e["id"] if isinstance(e, dict) else e: self.calc(e).total for e in emps}


def _norm(rows):
    out = []
    for r in rows:
        r = tuple(r)
        out.append((r[0], float(r[1]), r[2] if len(r) > 2 else ""))
    return out
