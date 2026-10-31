"""Партнёры демо: вход по партнёрской ссылке без срока, свои приглашения клиентам, заявки «Хочу так»,
памятка; не видит чужих гостей и не правит цены; админ видит партнёров и отключает."""
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core.access import pricing as P
from core.app import App
from core.ui import screens

TODAY = date(2026, 10, 29)
ADMIN, PARTNER, CLIENT, STRANGER = 1, 100, 200, 300


def link_code(text):
    return text.split("?start=")[1].split()[0] if "?start=" in text else text.split("/start ")[1].split()[0]


class Partner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp, demo_gate=True)
        self.app.web_url = "https://demo.example"
        self.app.press(ADMIN, "pta")
        r = self.app.text(ADMIN, "Ислам, Грозный")[0]
        self.app.start(PARTNER, arg=link_code(r.text))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def guest(self, uid):
        return (self.app.office().get("demo_guests") or {}).get(str(uid))

    def test_partner_enters_without_term(self):
        g = self.guest(PARTNER)
        self.assertTrue(g["partner"])
        self.assertEqual(g["till"], "2999-12-31")
        ctx = self.app.ctx(PARTNER)
        self.assertIn(("🤝 Мои клиенты", "ptn"), [x for row in screens.bottom_rows(ctx) for x in row])
        self.assertTrue(self.app.press(PARTNER, "ptm")[0].view.blocks)
        f = self.app.press(PARTNER, "ptp")[0]
        self.assertTrue(f.files[0][1].startswith(b"%PDF"))

    def test_client_invite_request_and_notifications(self):
        self.app.press(PARTNER, "pti")
        r = self.app.text(PARTNER, "Ахмед, Стройбаза, 8 928 000 00 00")[0]
        days = next(b.to for b in r.all_buttons() if b.to.startswith("ptk:7:"))
        out = self.app.press(PARTNER, days)
        self.assertTrue(any(x.to == ADMIN for x in out))
        code = link_code(out[0].text)
        first = self.app.start(CLIENT, arg=code)
        self.assertTrue(any(x.to == PARTNER and "открыл демо" in x.text for x in first))
        g = self.guest(CLIENT)
        self.assertEqual((g["by"], g["status"]), (str(PARTNER), "on"))
        self.assertEqual(g["till"], (TODAY + timedelta(days=7)).isoformat())
        out = self.app.press(CLIENT, "sw:req")
        self.assertTrue(any(x.to == PARTNER and "Хочу так" in x.text for x in out))
        self.assertTrue(any(x.to == ADMIN and "клиент партнёра Ислам" in x.text for x in out))
        mine = self.app.press(PARTNER, "ptn")[0]
        self.assertIn("привели: 1", mine.view.subtitle)
        self.assertIn("заявок: 1", mine.view.subtitle)

    def test_partner_request_asks_client(self):
        r = self.app.press(PARTNER, "sw:req")[0]
        self.assertTrue(r.ask)
        out = self.app.text(PARTNER, "Магомед, Автодеталь")
        self.assertTrue(any(x.to == ADMIN and "Магомед, Автодеталь (показ партнёра Ислам" in x.text for x in out))
        self.assertIn("закреплён за вами", out[-1].text)

    def test_limits(self):
        self.assertIn("администратора", self.app.press(PARTNER, "gst")[0].text)
        self.assertIn("администратора", self.app.press(PARTNER, "ptl")[0].text)
        self.app.press(PARTNER, "sw:role:admin")
        self.app.press(PARTNER, "pre:f_stock")
        self.app.text(PARTNER, "1")
        self.assertEqual(P.prices(self.app.office())["f_stock"], P.DEFAULT["f_stock"])
        g = self.guest(PARTNER)
        g2 = dict(self.app.office().get("demo_guests"))
        g2[str(STRANGER)] = {"status": "on", "till": "2026-11-05", "by": "999"}
        self.app.office().put("demo_guests", g2)
        self.assertIn("Мои клиенты", self.app.press(PARTNER, f"ptx:{STRANGER}")[0].view.title)   # чужого не продлит
        self.assertEqual(self.guest(STRANGER)["till"], "2026-11-05")
        self.assertTrue(g)

    def test_panel_is_separate(self):
        rows = screens.bottom_rows(self.app.ctx(ADMIN))
        self.assertIn([("🛠 Пульт", "adm")], rows)
        self.assertNotIn("gst", [t for row in rows for _, t in row])
        tos = [b.to for b in self.app.press(ADMIN, "adm")[0].all_buttons()]
        for to in ("gsi", "pta", "gst", "ptl", "rqs", "pr", "ptm"):
            self.assertIn(to, tos)
        self.assertNotIn(("🛠 Пульт", "adm"), [x for row in screens.bottom_rows(self.app.ctx(PARTNER)) for x in row])
        self.assertIn("администратора", self.app.press(PARTNER, "adm")[0].text)
        self.app.press(PARTNER, "sw:req")
        self.app.text(PARTNER, "Магомед, Автодеталь")
        self.assertIn("всего 1", self.app.press(ADMIN, "rqs")[0].view.subtitle)

    def test_admin_sees_and_switches_off(self):
        r = self.app.press(ADMIN, "ptl")[0]
        self.assertIn("работают: 1", r.view.subtitle)
        self.assertNotIn(f"gso:{PARTNER}", [b.to for b in self.app.press(ADMIN, "gst")[0].all_buttons()])
        self.app.press(ADMIN, f"gsoff:{PARTNER}")
        self.assertEqual(self.guest(PARTNER)["status"], "off")
        self.assertFalse(self.app.start(PARTNER)[0].view)          # больше не пускает
        self.app.press(ADMIN, f"gsa:{PARTNER}:7")
        self.assertEqual(self.guest(PARTNER)["till"], "2999-12-31")


if __name__ == "__main__":
    unittest.main()
