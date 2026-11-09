"""Ориентация в демо (core/ui/orient.py, Адам 09.11): приветствие гостю один раз, полоса демо с ролями и отраслью,
первый ряд плиток на главной, поиск по приложению с «дорогой»."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App
from core.ui import orient
from tests.test_partner import link_code

TODAY = date(2026, 11, 9)
ADMIN, PARTNER, CLIENT = 1, 100, 200


def labels(out):
    return [b.label for r in out for row in (r.buttons or []) for b in row]


class Orient(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp, demo_gate=True)
        self.app.web_url = "https://demo.example"
        self.app.press(ADMIN, "pta")
        r = self.app.text(ADMIN, "Ислам, Грозный")[0]
        self.app.start(PARTNER, arg=link_code(r.text))
        self.app.press(PARTNER, "pti")
        r = self.app.text(PARTNER, "Ахмед, Стройбаза, 8 928 000 00 00")[0]
        days = next(b.to for b in r.all_buttons() if b.to.startswith("ptk:7:"))
        self.code = link_code(self.app.press(PARTNER, days)[0].text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_welcome_once_then_home(self):
        first = [r for r in self.app.start(CLIENT, arg=self.code) if not r.to]
        self.assertTrue(any(r.view and "демо" in r.view.title.lower() for r in first))
        self.assertIn("👔 Начать с директора", labels(first))
        out = self.app.press(CLIENT, "wel:go")
        self.assertEqual(self.app.ctx(CLIENT).role, "owner")
        ls = labels(out)
        self.assertEqual(ls[:3], ["🧩 Собрать свой бот", "👀 Посмотреть по ролям", "📘 Что умеет"])
        again = self.app.press(CLIENT, "home")
        self.assertNotIn("👔 Начать с директора", labels(again))        # второй раз не показываем
        self.app.press(CLIENT, "sw:reset")                               # «Заново» — приветствие снова
        self.assertIn("👔 Начать с директора", labels(self.app.press(CLIENT, "home")))

    def test_admin_no_welcome_and_bar(self):
        out = self.app.press(ADMIN, "home")
        self.assertNotIn("👔 Начать с директора", labels(out))
        bar = orient.demo_bar(self.app.ctx(ADMIN))
        self.assertEqual(bar["cur"], f"sw:role:{self.app.ctx(ADMIN).role}")
        self.assertTrue(any(to == "sw:role:rep" for _, to in bar["roles"]))
        self.assertTrue(any(to.startswith("sw:pos:") for _, to in bar["roles"]))
        self.assertEqual(len(bar["people"]), orient.PEOPLE_IN_BAR)
        self.assertIn("Стройматериалы", bar["industry"])
        self.app.press(ADMIN, bar["roles"][0][1])
        self.assertEqual(self.app.ctx(ADMIN).role, "rep")
        self.assertEqual(orient.demo_bar(self.app.ctx(ADMIN))["cur"], "sw:role:rep")

    def test_find(self):
        self.app.press(ADMIN, "find")
        self.assertEqual(self.app.ctx(ADMIN).session["await"]["kind"], "find")
        out = self.app.text(ADMIN, "табель")[0]
        self.assertIn("найдено", out.view.subtitle)
        bs = out.all_buttons()
        self.assertTrue(any(b.to == "ts" for b in bs))                              # экран табеля
        self.assertTrue(any(b.to == "sw:ed" and "Табель и штрафы" in b.label for b in bs))   # блок зарплаты
        self.assertTrue(all(b.info.startswith("Где:") for b in bs if b.to != "find" and not b.to.startswith("bk:")))
        self.app.press(ADMIN, "find")
        out = self.app.text(ADMIN, "абракадабра")[0]
        self.assertIn("ничего", out.view.subtitle)
        self.assertEqual([b.to for b in out.all_buttons() if not b.to.startswith("bk:")], ["find"])

    def test_bottom_menu_has_find(self):
        from core.ui.screens import bottom_rows
        tos = [to for row in bottom_rows(self.app.ctx(ADMIN)) for _, to in row]
        self.assertIn("find", tos)


if __name__ == "__main__":
    unittest.main()
