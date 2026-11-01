"""💰 Деньги и клиенты (Адам 01.11): пульт управления в демо — все клиенты, оплаты, заявки, неделя, утренняя сводка."""
import logging
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from core import config as C
from core import control
from core.app import App

logging.disable(logging.CRITICAL)
DAY = date(2026, 11, 16)


def text_of(r):
    out = [r.text or ""]
    for _, b in (r.view.blocks if r.view else []):
        out.append(str(b))
        for x in (b if isinstance(b, tuple) else ()):
            for item in (x if isinstance(x, list) else ()):
                if isinstance(item, str):
                    out.append(item)
                    continue
                out += [c.text for c in getattr(item, "cells", [])] + [str(getattr(item, "sub", "") or "")]
                out.append(str(getattr(item, "title", "") or ""))
    return " ".join(out)


class Control(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        clients = self.tmp / "clients"
        clients.mkdir()
        shutil.copytree(C.CLIENTS / "demo", clients / "demo")
        shutil.copytree(C.CLIENTS / "_template", clients / "_template")
        shutil.copytree(C.CLIENTS / "_template", clients / "acme")
        cfg = clients / "acme" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("id: _template", "id: acme", 1), encoding="utf-8")
        self.patch = mock.patch.object(C, "CLIENTS", clients)
        self.patch.start()
        self.data = self.tmp / "data"
        self.demo = App("demo", today=DAY, admins=[1], data_dir=self.data)
        self.acme = App("acme", today=DAY, admins=[1], data_dir=self.data)
        db = self.acme.db()
        db.add_employee("Хозяин Фирмы", "office", position="director", role="owner", tg_id=700)
        db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", tg_id=701)

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_admin(self):
        self.assertIn("администратора", self.demo.press(5, "mc")[0].text)

    def test_money_card_and_payments(self):
        r = self.demo.press(1, "mc")[0]
        self.assertIn("Деньги и клиенты", r.view.title)
        self.demo.press(1, "mca:acme:d")
        r = self.demo.text(1, "01.10.2026 05.10.2026")[0]
        self.assertIn("Даты записаны", text_of(r))
        self.assertIn("mcl:late", [b.to for b in self.demo.press(1, "mc")[0].all_buttons()])
        self.assertIn("acme", str([b.to for b in self.demo.press(1, "mcl:late")[0].all_buttons()]))
        out = control.digest(self.demo, DAY)
        self.assertTrue(out and out[0].to == 1 and "должен" in out[0].text)
        self.demo.press(1, "mca:acme:p")                                  # октябрь оплачен
        self.demo.press(1, "mca:acme:s")
        r = self.demo.text(1, "5 000")[0]                                 # ноябрь — частично
        self.assertIn("Отмечено 5 000", text_of(r))
        self.assertIn("осталось", text_of(self.acme.press(700, "bl")[0]))
        self.assertTrue(self.demo.press(1, "mcw")[0].view)

    def test_requests_approve_and_notify(self):
        self.acme.press(700, "blc")
        self.acme.press(700, "blc:t:stock")
        self.acme.press(700, "blc:ok")
        r = self.demo.press(1, "mcr")[0]
        self.assertIn("mcy:acme:1", [b.to for b in r.all_buttons()])
        self.demo.press(1, "mcy:acme:1")
        self.assertIn("stock", self.acme.ctx(700).cfg.modules)
        box = self.acme.office().get("inbox:700") or []
        self.assertTrue(any("Включено" in (m.get("text") or "") for m in box))

    def test_reject(self):
        self.acme.press(700, "blc")
        self.acme.press(700, "blc:p:5")
        self.acme.press(700, "blc:ok")
        self.demo.press(1, "mcn:acme:1")
        self.demo.text(1, "Сначала оплатите")
        box = self.acme.office().get("inbox:700") or []
        self.assertTrue(any("Сначала оплатите" in (m.get("text") or "") for m in box))

    def test_morning_once_a_day(self):
        self.demo.press(1, "mca:acme:d")
        self.demo.text(1, "01.10.2026 05.10.2026")
        got = []
        m = control.Morning(self.demo, got.extend)
        self.assertEqual(m.tick(datetime(2026, 11, 16, 8, 59)), [])
        self.assertTrue(m.tick(datetime(2026, 11, 16, 9, 0)))
        self.assertEqual(m.tick(datetime(2026, 11, 16, 9, 1)), [])


class Panel(Control):
    """Отдельное приложение управления (admin.<домен>): тот же демо-процесс, свой значок, только для администратора."""

    def test_panel_app(self):
        from core.transport.web import WebApi
        demo_api = WebApi(self.demo, admin_key="ключ")
        api = WebApi(self.demo, admin_key="ключ", panel=True, lock=demo_api.lock)
        code, h, raw = api.handle("GET", "/manifest.webmanifest", {}, {}, None)
        self.assertIn("Управление", raw.decode())
        code, h, raw = api.handle("POST", "/api/start", {}, {"admin": "ключ"}, None)
        import json
        data = json.loads(raw)
        self.assertEqual(data["company"], "Управление")
        self.assertIn("Деньги и клиенты", json.dumps(data["msgs"], ensure_ascii=False))
        self.assertIn(["📨 Заявки", "mcr"], data["tabs"])
        self.assertTrue(self.demo.office().get("panel_admins"))
        code, h, raw = api.handle("POST", "/api/start", {}, {}, None)          # чужой телефон без ключа
        self.assertIn("приложение управления", raw.decode())
        code, h, raw = demo_api.handle("POST", "/api/press", {}, {"to": "home"}, None)   # демо — без «Пульта» внизу
        self.assertNotIn('"adm"', raw.decode())


if __name__ == "__main__":
    unittest.main()
