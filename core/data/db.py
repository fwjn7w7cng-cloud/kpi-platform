"""База клиента: один файл SQLite. Открыть — ``DB.open(path)``, база сама догоняет миграции.

Запись фактов — только пачками (``load``); прочее — короткими методами. Чтения возвращают
словари/строки sqlite3.Row, чтобы ядру не тащить SQL в расчёты.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import astuple, fields
from datetime import date, datetime
from pathlib import Path

from core.data import migrations
from core.data.model import KINDS, Dataset


ROOT = Path(__file__).resolve().parent.parent.parent


class DataError(Exception):
    pass


def client_db_path(client_id):
    """Файл базы клиента: data/<клиент>/kpi.db (папку data/ можно перенести переменной KPI_DATA)."""
    base = Path(os.environ.get("KPI_DATA", ROOT / "data"))
    return base / client_id / "kpi.db"


def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def month_bounds(month):
    """'2026-10' → ('2026-10-01', '2026-10-31')."""
    y, m = map(int, month.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return f"{month}-01", date.fromordinal(nxt.toordinal() - 1).isoformat()


class DB:
    def __init__(self, conn):
        self.c = conn
        self._lock = threading.RLock()
        self._skip = 0           # изменения, которые не меняют цифры (сессии) — не сбрасывают кэш расчётов

    def rev(self):
        """Номер версии данных: меняется при любой записи, кроме сессий мессенджера. По нему живёт кэш расчётов."""
        return self.c.total_changes - self._skip

    @classmethod
    def open(cls, path):
        """path — файл базы или ':memory:' (для тестов)."""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        db = cls(conn)
        db.migrate()
        return db

    def close(self):
        self.c.close()

    # ---------- служебное ----------

    @contextmanager
    def tx(self):
        """Транзакция: всё или ничего."""
        with self._lock:
            self.c.execute("BEGIN IMMEDIATE")
            try:
                yield self.c
            except BaseException:
                self.c.execute("ROLLBACK")
                raise
            self.c.execute("COMMIT")

    def migrate(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, title TEXT, ts TEXT)")
        done = {r[0] for r in self.c.execute("SELECT version FROM schema_migrations")}
        applied = []
        for version, title, sql in migrations.STEPS:
            if version in done:
                continue
            with self.tx() as c:
                for stmt in _split(sql):
                    c.execute(stmt)
                c.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, title, _now()))
            applied.append(version)
        return applied

    def version(self):
        return self.c.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]

    def q(self, sql, *args):
        """Короткий запрос на чтение → список словарей."""
        return [dict(r) for r in self.c.execute(sql, [_iso(a) for a in args])]

    def one(self, sql, *args):
        r = self.c.execute(sql, [_iso(a) for a in args]).fetchone()
        return dict(r) if r else None

    def x(self, sql, *args):
        """Короткая запись → id новой строки."""
        with self._lock:
            return self.c.execute(sql, [_iso(a) for a in args]).lastrowid

    # ---------- сессии пользователей мессенджера ----------

    def session(self, user_id):
        r = self.c.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(r[0]) if r else {}

    def save_session(self, user_id, data):
        before = self.c.total_changes
        self.c.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
                       (user_id, json.dumps(data, ensure_ascii=False)))
        self._skip += self.c.total_changes - before

    # ---------- планы и табель ----------

    def plans(self, month):
        """{сотрудник: {показатель: план}}."""
        out = {}
        for r in self.c.execute("SELECT emp_id, metric, value FROM plans WHERE month = ?", (month,)):
            out.setdefault(r[0], {})[r[1]] = r[2]
        return out

    def set_plan(self, month, emp_id, metric, value, who="", src="ручной"):
        self.c.execute("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(month, emp_id, metric) DO UPDATE SET "
                       "value = excluded.value, src = excluded.src, who = excluded.who, ts = excluded.ts",
                       (month, emp_id, metric, float(value), src, str(who), _now()))

    def timesheet(self, start, end, emp_id=None):
        q, a = "SELECT * FROM timesheet WHERE day BETWEEN ? AND ?", [_iso(start), _iso(end)]
        if emp_id is not None:
            q, a = q + " AND emp_id = ?", a + [emp_id]
        return [dict(r) for r in self.c.execute(q + " ORDER BY day, id", a)]

    def add_timesheet(self, day, emp_id, kind, minutes=0, amount=0, note="", who=""):
        return self.x("INSERT INTO timesheet (day, emp_id, kind, minutes, amount, note, who, ts) VALUES (?,?,?,?,?,?,?,?)",
                      day, emp_id, kind, minutes, amount, note, who, _now())

    def key_map(self):
        """{ключ: [(с какой даты, сотрудник), …]} по возрастанию даты — для быстрого перевода фактов в людей."""
        out = {}
        for r in self.c.execute("SELECT key, since, emp_id FROM emp_keys ORDER BY key, since"):
            out.setdefault(r[0], []).append((r[1], r[2]))
        return out

    def audit(self, who, action, details=""):
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        self.c.execute("INSERT INTO audit (ts, who, action, details) VALUES (?, ?, ?, ?)", (_now(), str(who), action, details))

    # ---------- настройки ----------

    def get(self, name, default=None):
        r = self.c.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, name, value):
        self.c.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                       (name, json.dumps(value, ensure_ascii=False)))

    # ---------- сотрудники ----------

    def add_employee(self, fio, dept, team="", position="", role="staff", keys=(), **kw):
        allowed = {"tg_id", "salary", "rate", "hired", "fired", "active", "extra"}
        bad = set(kw) - allowed
        if bad:
            raise DataError(f"лишние поля сотрудника: {', '.join(sorted(bad))}")
        if "extra" in kw:
            kw["extra"] = json.dumps(kw["extra"], ensure_ascii=False)
        kw = {k: _iso(v) for k, v in kw.items()}
        cols = ["fio", "dept", "team", "position", "role", *kw]
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO employees ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            (fio, dept, team, position, role, *kw.values()))
            for k in keys:
                c.execute("INSERT INTO emp_keys (key, emp_id) VALUES (?, ?)", (norm_key(k), cur.lastrowid))
        return cur.lastrowid

    def bind_key(self, key, emp_id, since="0000-01-01"):
        """Привязать ключ источника к сотруднику с даты (новый торговый на старой территории)."""
        self.c.execute("INSERT OR REPLACE INTO emp_keys VALUES (?, ?, ?)", (norm_key(key), emp_id, _iso(since)))

    def employee(self, emp_id):
        r = self.c.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
        return _emp(r) if r else None

    def employee_by_tg(self, tg_id):
        r = self.c.execute("SELECT * FROM employees WHERE tg_id = ?", (tg_id,)).fetchone()
        return _emp(r) if r else None

    def employees(self, dept=None, team=None, active=True):
        q, a = "SELECT * FROM employees WHERE 1", []
        if dept is not None:
            q, a = q + " AND dept = ?", a + [dept]
        if team is not None:
            q, a = q + " AND team = ?", a + [team]
        if active is not None:
            q, a = q + " AND active = ?", a + [int(active)]
        return [_emp(r) for r in self.c.execute(q + " ORDER BY fio", a)]

    def emp_of_key(self, key, on):
        """Кто стоял за ключом на дату."""
        r = self.c.execute("SELECT emp_id FROM emp_keys WHERE key = ? AND since <= ? ORDER BY since DESC LIMIT 1",
                           (norm_key(key), _iso(on))).fetchone()
        return r[0] if r else None

    def unknown_keys(self, kind="sales"):
        """Ключи из фактов, не привязанные ни к кому — «нет в расчёте»."""
        table = KINDS[kind][1]
        return [r[0] for r in self.c.execute(
            f"SELECT DISTINCT emp_key FROM {table} WHERE emp_key NOT IN (SELECT key FROM emp_keys) ORDER BY 1")]

    # ---------- пачки фактов ----------

    def load(self, ds: Dataset):
        """Принять пачку. Возвращает ('skip', причина) или ('ok', id пачки, сколько строк заменено)."""
        problems = ds.check()
        if problems:
            raise DataError("пачка не принята:\n" + "\n".join(problems))
        if ds.fingerprint and self.c.execute("SELECT 1 FROM batches WHERE fingerprint = ?", (ds.fingerprint,)).fetchone():
            return ("skip", "такой файл уже принят")
        cls, table, _ = KINDS[ds.kind]
        names = [f.name for f in fields(cls)]
        cols = ["grp" if n == "group" else n for n in names]
        start, end = ds.start.isoformat(), ds.end.isoformat()
        with self.tx() as c:
            old = [r[0] for r in c.execute("SELECT id FROM batches WHERE kind = ? AND start = ? AND end = ?",
                                           (ds.kind, start, end))]
            replaced = 0
            if old:
                marks = ",".join("?" * len(old))
                replaced = c.execute(f"DELETE FROM {table} WHERE batch IN ({marks})", old).rowcount
                c.execute(f"DELETE FROM batches WHERE id IN ({marks})", old)
            bid = c.execute("INSERT INTO batches (kind, start, end, source, fingerprint, rows, ts) VALUES (?,?,?,?,?,?,?)",
                            (ds.kind, start, end, ds.source, ds.fingerprint, len(ds.rows), _now())).lastrowid
            verb = "INSERT OR REPLACE" if ds.kind in ("clients", "groups") else "INSERT"
            quoted = ", ".join(f'"{x}"' for x in ["batch", *cols])
            kis = [names.index(n) for n in ("emp_key", "who") if n in names]

            def row(r):
                v = [_iso(x) for x in astuple(r)]
                for ki in kis:
                    v[ki] = norm_key(v[ki])
                return (bid, *v)

            c.executemany(f"{verb} INTO {table} ({quoted}) VALUES ({', '.join('?' * (len(cols) + 1))})",
                          [row(r) for r in ds.rows])
        return ("ok", bid, replaced)

    def batches(self, kind=None):
        q = "SELECT * FROM batches" + (" WHERE kind = ?" if kind else "") + " ORDER BY kind, start, end"
        return [dict(r) for r in self.c.execute(q, (kind,) if kind else ())]

    # ---------- чтение фактов ----------

    def sales(self, start, end, emp_keys=None):
        q, a = "SELECT * FROM sales WHERE day BETWEEN ? AND ?", [_iso(start), _iso(end)]
        if emp_keys:
            q += f" AND emp_key IN ({','.join('?' * len(emp_keys))})"
            a += [norm_key(k) for k in emp_keys]
        return [dict(r) for r in self.c.execute(q, a)]

    def sales_by_emp(self, month):
        """{сотрудник: сумма} за месяц — ключи переведены в сотрудников на дату продажи."""
        s, e = month_bounds(month)
        out, unknown = {}, {}
        for r in self.c.execute("""
            SELECT s.emp_key, s.amount,
              (SELECT k.emp_id FROM emp_keys k WHERE k.key = s.emp_key AND k.since <= s.day
               ORDER BY k.since DESC LIMIT 1) AS emp
            FROM sales s WHERE s.day BETWEEN ? AND ?""", (s, e)):
            if r["emp"] is None:
                unknown[r["emp_key"]] = unknown.get(r["emp_key"], 0) + r["amount"]
            else:
                out[r["emp"]] = out.get(r["emp"], 0) + r["amount"]
        return out, unknown

    def debts_on(self, on):
        """Последний срез долгов не позже даты."""
        r = self.c.execute('SELECT MAX("on") FROM debts WHERE "on" <= ?', (_iso(on),)).fetchone()
        if not r or not r[0]:
            return None, []
        return r[0], [dict(x) for x in self.c.execute('SELECT * FROM debts WHERE "on" = ?', (r[0],))]

    def client_name(self, code):
        r = self.c.execute("SELECT name FROM clients WHERE code = ?", (code,)).fetchone()
        return r[0] if r else code

    # ---------- месяц ----------

    def month_status(self, month):
        r = self.c.execute("SELECT status FROM months WHERE month = ?", (month,)).fetchone()
        return r[0] if r else "open"

    def set_month_status(self, month, status, who, snapshot=None):
        if status not in ("open", "review", "approved"):
            raise DataError(f"нет такого статуса месяца: {status}")
        with self.tx() as c:
            c.execute("INSERT INTO months VALUES (?, ?, ?, ?) ON CONFLICT(month) DO UPDATE SET "
                      "status = excluded.status, ts = excluded.ts, snapshot = COALESCE(excluded.snapshot, months.snapshot)",
                      (month, status, _now(), json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None))
            c.execute("INSERT INTO audit (ts, who, action, details) VALUES (?, ?, ?, ?)",
                      (_now(), str(who), "month_status", f"{month} → {status}"))


def norm_key(k):
    """«Иванов  Пётр» → «иванов петр»: регистр, ё, лишние пробелы не мешают совпадению."""
    return " ".join(str(k).lower().replace("ё", "е").split())


def _emp(r):
    d = dict(r)
    d["extra"] = json.loads(d["extra"] or "{}")
    return d


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _split(sql):
    """Разбить SQL-скрипт на команды (в миграциях нет ';' внутри строк)."""
    out, cur = [], []
    for line in sql.splitlines():
        line = line.split("--", 1)[0]
        cur.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(cur).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            cur = []
    tail = "\n".join(cur).strip()
    if tail:
        out.append(tail)
    return out
