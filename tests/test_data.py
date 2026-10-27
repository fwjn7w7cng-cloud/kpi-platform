"""База клиента: миграции, пачки фактов (замена, дубли), ключи людей, долги на дату, статус месяца."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.data.db import DB, DataError, month_bounds, norm_key
from core.data.model import Client, Dataset, Debt, Sale

OCT = (date(2026, 10, 1), date(2026, 10, 31))


def sales(*rows, fp="", src="test"):
    return Dataset("sales", *OCT, rows=[Sale(date(2026, 10, d), k, c, g, a) for d, k, c, g, a in rows],
                   source=src, fingerprint=fp)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = DB.open(":memory:")
        self.ivanov = self.db.add_employee("Иванов Пётр Сергеевич", "sales", team="north", role="rep",
                                           keys=["Иванов Пётр"])
        self.petrov = self.db.add_employee("Петров Иван", "sales", team="south", role="rep", keys=["петров иван"])

    def tearDown(self):
        self.db.close()


class Migrations(Base):
    def test_fresh_db_is_current(self):
        self.assertEqual(self.db.version(), 1)
        self.assertEqual(self.db.migrate(), [])          # повторный запуск ничего не делает

    def test_file_db_survives_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x" / "kpi.db"
            db = DB.open(path)
            db.put("hello", {"a": 1})
            db.close()
            db = DB.open(path)
            self.assertEqual(db.get("hello"), {"a": 1})
            self.assertEqual(db.version(), 1)
            db.close()


class Batches(Base):
    def test_load_and_sum_by_employee(self):
        st = self.db.load(sales((1, "ИВАНОВ ПЁТР", "101", "G1", 1000), (2, "Петров Иван", "102", "G1", 500),
                                (3, "иванов петр", "101", "G2", -200)))
        self.assertEqual(st[0], "ok")
        by, unknown = self.db.sales_by_emp("2026-10")
        self.assertEqual(by, {self.ivanov: 800, self.petrov: 500})
        self.assertEqual(unknown, {})

    def test_same_period_replaces_previous(self):
        self.db.load(sales((1, "иванов петр", "101", "G1", 1000), fp="a"))
        st = self.db.load(sales((1, "иванов петр", "101", "G1", 1500), (5, "иванов петр", "103", "G1", 100), fp="b"))
        self.assertEqual(st[2], 1)                           # заменена одна строка
        self.assertEqual(self.db.sales_by_emp("2026-10")[0], {self.ivanov: 1600})
        self.assertEqual(len(self.db.batches("sales")), 1)

    def test_same_file_twice_is_skipped(self):
        self.db.load(sales((1, "иванов петр", "101", "G1", 1000), fp="same"))
        self.assertEqual(self.db.load(sales((1, "иванов петр", "101", "G1", 9999), fp="same"))[0], "skip")
        self.assertEqual(self.db.sales_by_emp("2026-10")[0], {self.ivanov: 1000})

    def test_row_outside_period_rejected(self):
        bad = Dataset("sales", *OCT, rows=[Sale(date(2026, 11, 1), "иванов петр", "101", "", 1)])
        with self.assertRaises(DataError) as ctx:
            self.db.load(bad)
        self.assertIn("вне периода", str(ctx.exception))
        self.assertEqual(self.db.batches(), [])              # ничего не записано

    def test_unknown_key_reported(self):
        self.db.load(sales((1, "Сидоров Олег", "101", "", 300)))
        by, unknown = self.db.sales_by_emp("2026-10")
        self.assertEqual(unknown, {"сидоров олег": 300})
        self.assertEqual(self.db.unknown_keys(), ["сидоров олег"])


class KeyHandover(Base):
    def test_new_rep_takes_territory_from_date(self):
        """Ключ «иванов петр» с 15.10 — у нового сотрудника; продажи до 15-го остаются старому."""
        new = self.db.add_employee("Новиков Артём", "sales", team="north", role="rep")
        self.db.bind_key("Иванов Пётр", new, since=date(2026, 10, 15))
        self.db.load(sales((10, "иванов петр", "101", "", 100), (20, "иванов петр", "101", "", 40)))
        self.assertEqual(self.db.sales_by_emp("2026-10")[0], {self.ivanov: 100, new: 40})
        self.assertEqual(self.db.emp_of_key("иванов петр", date(2026, 10, 14)), self.ivanov)
        self.assertEqual(self.db.emp_of_key("иванов петр", date(2026, 10, 15)), new)


class DebtsAndRefs(Base):
    def test_latest_snapshot_not_after_date(self):
        for d, total in ((7, 1000), (14, 700)):
            on = date(2026, 10, d)
            self.db.load(Dataset("debts", on, on, rows=[Debt(on, "иванов петр", "101", total, d91=total / 2)]))
        on, rows = self.db.debts_on(date(2026, 10, 13))
        self.assertEqual((on, rows[0]["total"]), ("2026-10-07", 1000))
        on, rows = self.db.debts_on(date(2026, 10, 31))
        self.assertEqual((on, rows[0]["d91"]), ("2026-10-14", 350))
        self.assertEqual(self.db.debts_on(date(2026, 9, 1)), (None, []))

    def test_client_directory(self):
        self.db.load(Dataset("clients", *OCT, rows=[Client("101", "ИП Ромашка", "Город")]))
        self.db.load(Dataset("clients", date(2026, 10, 2), date(2026, 10, 2), rows=[Client("101", "ООО Ромашка")]))
        self.assertEqual(self.db.client_name("101"), "ООО Ромашка")
        self.assertEqual(self.db.client_name("999"), "999")


class Misc(Base):
    def test_month_status_and_audit(self):
        self.assertEqual(self.db.month_status("2026-10"), "open")
        self.db.set_month_status("2026-10", "approved", who="owner", snapshot={"fund": 1})
        self.assertEqual(self.db.month_status("2026-10"), "approved")
        with self.assertRaises(DataError):
            self.db.set_month_status("2026-10", "closed?", who="x")
        n = self.db.c.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        self.assertEqual(n, 1)

    def test_helpers(self):
        self.assertEqual(month_bounds("2026-02"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(month_bounds("2026-12"), ("2026-12-01", "2026-12-31"))
        self.assertEqual(norm_key("  Ёлкин   Пётр "), "елкин петр")

    def test_employee_fields_checked(self):
        with self.assertRaises(DataError):
            self.db.add_employee("Х", "sales", phone="123")
        e = self.db.employee(self.ivanov)
        self.assertEqual((e["team"], e["extra"]), ("north", {}))
        self.assertEqual([x["fio"] for x in self.db.employees(dept="sales")], ["Иванов Пётр Сергеевич", "Петров Иван"])


if __name__ == "__main__":
    unittest.main()
