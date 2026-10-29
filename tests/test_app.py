"""Бот целиком на подставном мессенджере: робот прокликивает все роли и редакции; права; замки; рассылки;
подключение нового сотрудника у настоящего клиента."""
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from core.app import App
from core.jobs import schedule
from tools import crawl

TODAY = date(2026, 10, 27)


class Crawl(unittest.TestCase):
    def test_every_button_every_role(self):
        total, shots, errs = crawl.run("demo", today=TODAY)
        self.assertGreater(total, 500)
        self.assertGreater(shots, 300)
        self.assertEqual(errs, [], "\n".join(errs[:20]))


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.app = App("demo", today=TODAY, admins=[1], data_dir=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def labels(self, replies):
        return [b.label for r in replies for b in r.all_buttons()]

    def test_start_edition_shows_locks(self):
        self.app.press(10, "sw:ed:start")
        self.app.press(10, "sw:role:rep")
        labels = self.labels(self.app.start(10))
        self.assertIn("🔒 Клиенты", labels)
        self.assertIn("📈 Мой план", labels)
        self.assertIn("🔒 Возвраты", self.labels(self.app.press(10, "mg:sales")))   # длинная главная — в группах
        self.assertIn("💵 Долги", self.labels(self.app.press(10, "mg:sales")))      # дебиторка — уже в Старте
        self.assertIn("💰 Моя зарплата", labels)                                     # и зарплата
        r = self.app.press(10, "cli")                          # закрытый экран не открывается — объясняет
        self.assertIn("Доступно в Бизнес", r[0].view.subtitle)

    def test_long_home_is_grouped(self):
        self.app.press(15, "sw:ed:control")
        self.app.press(15, "sw:role:owner")
        menu = [b for r in self.app.start(15) for b in r.all_buttons() if not b.to.startswith(("sw:", "mon:"))]
        self.assertLessEqual(len(menu), 12)
        self.assertIn("mg:money", [b.to for b in menu])
        inside = self.labels(self.app.press(15, "mg:money"))
        self.assertIn("🗓 Месяц", inside)
        self.assertIn("📋 Планы", inside)

    def test_rep_cannot_see_colleague(self):
        self.app.press(11, "sw:ed:control")
        self.app.press(11, "sw:role:rep")
        ctx = self.app.ctx(11)
        other = next(e for e in ctx.db.employees() if e["role"] == "rep" and e["id"] != ctx.me["id"])
        self.assertIn("Нет доступа", self.app.press(11, f"emp:{other['id']}")[0].text)
        self.assertIn("Нет доступа", self.app.press(11, f"pay:{other['id']}")[0].text)

    def test_industry_switch_changes_groups(self):
        self.app.press(12, "sw:ind:pharma")
        ctx = self.app.ctx(12)
        names = [r["name"] for r in ctx.db.q("SELECT name FROM groups")]
        self.assertIn("Витамины и БАД", names)

    def test_edit_flow_with_text_input(self):
        self.app.press(13, "sw:ed:control")
        self.app.press(13, "sw:role:head")
        ctx = self.app.ctx(13)
        rep = next(e for e in ctx.db.employees() if e["role"] == "rep")
        ask = self.app.press(13, f"eda:{rep['id']}")
        self.assertTrue(ask[0].ask)
        out = self.app.text(13, "3000 помог на складе")
        self.assertTrue(any("на утверждение" in r.text for r in out))

    def test_morning_brief_preview(self):
        self.app.press(14, "sw:role:rep")
        r = schedule.preview(self.app.ctx(14), "morning_brief")
        self.assertIsNotNone(r.view)


class RealClient(unittest.TestCase):
    """Клиент из шаблона: человека нет — заявка — админ привязывает — человек видит своё."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[1], data_dir=self.tmp)
        self.emp = self.app.db().add_employee("Иванов Пётр Сергеевич", "sales", team="north", position="rep", role="rep",
                                              keys=["иванов петр"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registration(self):
        r = self.app.start(500)
        self.assertTrue(r[0].ask)
        out = self.app.text(500, "Иванов Пётр")
        self.assertTrue(any(x.to == 1 for x in out))               # админу ушла заявка
        pick = self.app.press(1, "regs:500")
        self.assertTrue(any("Иванов" in b.label for b in pick[0].all_buttons()))
        self.app.press(1, f"regs:500:{self.emp}")
        home = self.app.start(500)
        self.assertIn("Иванов Пётр Сергеевич", home[0].view.subtitle)

    def test_schedule(self):
        self.app.db().c.execute("UPDATE employees SET tg_id = 500 WHERE id = ?", (self.emp,))
        due = [j.name for j in schedule.due(self.app, datetime(2026, 10, 27, 8, 0))]
        self.assertIn("morning_brief", due)
        self.assertEqual(schedule.due(self.app, datetime(2026, 10, 25, 8, 0)), [])   # воскресенье — выходной
        j = next(x for x in schedule.JOBS if x.name == "morning_brief")
        out = schedule.run(self.app, j)
        self.assertTrue(any(r.to == 500 for r in out))


if __name__ == "__main__":
    unittest.main()
