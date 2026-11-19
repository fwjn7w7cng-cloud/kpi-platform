"""🕓 Как менялась зарплата (core/paysnap, Адам 19.11): снимок раз в час только при изменении, хранится 4 дня,
экран «было → сейчас» со строками, цифрами и тем, что пришло; в демо — история на лету."""
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from core import paysnap as PS
from core.app import App
from core.jobs.worker import Worker
from core.ui.web import reply_json

ADMIN = 1


def view(out):
    for r in out:
        d = reply_json(r)
        if d.get("view"):
            return d["view"]
    return {}


def ui(v):
    return {b["t"]: b for b in v.get("blocks", []) if b["k"] == "ui"}


class Real(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=date(2026, 11, 18), admins=[ADMIN], data_dir=self.tmp)
        self.db = self.app.db()
        pos = (self.app.cfg.raw.get("positions") or [{}])[0].get("id", "")
        dept = self.app.cfg.departments[0].id
        self.e = self.db.add_employee("Тестов Тест", dept, position=pos, role="staff", salary=26000, hired="2026-10-01")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hourly_only_changes_and_screen(self):
        t1 = datetime(2026, 11, 17, 10, 0)
        self.assertGreaterEqual(PS.take(self.app, t1), 1)
        self.assertEqual(PS.take(self.app, datetime(2026, 11, 17, 11, 0)), 0)          # ничего не поменялось — снимка нет
        self.db.add_timesheet("2026-11-16", self.e, "absent", who="Руководитель")
        self.assertGreaterEqual(PS.take(self.app, datetime(2026, 11, 17, 12, 0)), 1)
        first = self.db.one("SELECT id FROM pay_hist WHERE emp_id = ? ORDER BY id", self.e)["id"]
        v = view(self.app.press(ADMIN, f"pch:{self.e}:{first}"))
        b = ui(v)
        self.assertIn("было", b["hero"]["d"]["sub"])
        self.assertTrue(any(x[2] for x in b["choice"]["d"]["items"]))
        texts = " ".join(" ".join(map(str, x.get("items") or [])) for x in v["blocks"])
        self.assertIn("пропуск", texts)                                                   # что пришло: отметка табеля

    def test_keep_days(self):
        PS.take(self.app, datetime(2026, 11, 10, 10, 0))
        self.db.add_timesheet("2026-11-11", self.e, "absent")
        PS.take(self.app, datetime(2026, 11, 18, 10, 0))
        self.assertFalse(self.db.q("SELECT 1 FROM pay_hist WHERE ts < '2026-11-14'"))

    def test_worker_once_an_hour(self):
        self.app.demo = False
        w = Worker(self.app, lambda r: None, srcs=[])
        w.tick(datetime(2026, 11, 17, 10, 0))
        n = len(self.db.q("SELECT 1 FROM pay_hist"))
        self.db.add_timesheet("2026-11-12", self.e, "absent")
        w.tick(datetime(2026, 11, 17, 10, 30))                                            # тот же час — не снимает
        self.assertEqual(len(self.db.q("SELECT 1 FROM pay_hist")), n)
        w.tick(datetime(2026, 11, 17, 11, 0))
        self.assertGreater(len(self.db.q("SELECT 1 FROM pay_hist")), n)

    def test_no_access_to_others(self):
        self.db.add_employee("Чужой Человек", self.app.cfg.departments[0].id, role="staff", tg_id=777)
        out = self.app.press(777, f"pch:{self.e}")
        self.assertTrue(any("Нет доступа" in (reply_json(r).get("text") or "") for r in out))


class Demo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=date(2026, 11, 17), admins=[ADMIN], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_demo_history_on_the_fly(self):
        self.app.press(ADMIN, "sw:role:rep")
        me = self.app.ctx(ADMIN).me["id"]
        pay = reply_json(self.app.press(ADMIN, "pay")[-1])
        self.assertIn(f"pch:{me}", [b[1] for row in pay["buttons"] for b in row])
        b = ui(view(self.app.press(ADMIN, f"pch:{me}:1")))
        self.assertEqual(len(b["choice"]["d"]["items"]), PS.KEEP_DAYS)
        self.assertTrue(b["choice"]["d"]["items"][0][2])                                  # «вчера» выбрано
        self.assertIn("rows", b)


if __name__ == "__main__":
    unittest.main()
