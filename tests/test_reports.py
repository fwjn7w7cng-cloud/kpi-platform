"""📥 Отчёты 1С в приложении настоящего клиента: загрузка, «Что пришло», права, повтор, чужой файл, демо, веб-провод."""
import base64
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import reports as RP
from core.app import App
from tests.test_month_ingest import sales_report

TODAY = date(2026, 10, 31)
ADMIN = 1


def lines(r):
    return " | ".join(str(x) for k, b in r.view.blocks if k in ("lines", "note") for x in (b[1] if k == "lines" else [b[0]]))


class Upload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.db = self.app.db()
        self.db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", keys=["иванов петр"], tg_id=500)
        self.db.add_employee("Бухова Аза", "office", position="accountant", role="accountant", tg_id=600)
        self.file = sales_report("Период: 01.10.2026 - 26.10.2026", [
            ["Б2 ТП Иванов Пётр Сергеевич", 1500], ["  ИП Ромашка (код 1043)", 1500], ["      Сухие смеси", 1000],
            ["      Кровля", 500], ["Сидоров Олег", 300], ["  ООО Лето (код 77)", 300], ["      Кровля", 300]])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_screen_upload_and_log(self):
        r = self.app.press(ADMIN, "up")[0]
        self.assertTrue(r.upload)
        self.assertIn("пока ничего нет", lines(r))
        out = self.app.upload(ADMIN, "продажи октябрь.xlsx", self.file)
        r = out[0]
        self.assertEqual(r.view.title, "🧪 Что пришло")
        self.assertIn("✅ продажи", lines(r))
        self.assertIn("Сидоров Олег", lines(r))                                  # незнакомое имя — сказали
        self.assertIn("pak", [b.to for b in r.all_buttons()])                    # и кнопка привязать
        self.assertEqual(sum(x["amount"] for x in self.db.sales("2026-10-01", "2026-10-31")), 1800)
        r = self.app.press(ADMIN, "up")[0]
        self.assertIn("Октябрь", str([c.text for k, b in r.view.blocks if k == "table" for row in b[2] for c in row.cells]))
        again = self.app.upload(ADMIN, "копия.xlsx", self.file)[0]                # тот же файл второй раз
        self.assertIn("уже принят", lines(again))
        self.assertEqual(len(RP.log_of(self.db)), 2)
        r = self.app.press(ADMIN, "upl")[0]
        self.assertEqual(r.view.title, "🧪 Что пришло")
        self.assertIn("upl:1", [b.to for b in r.all_buttons()])
        self.assertIn("продажи октябрь", self.app.press(ADMIN, "upl:1")[0].view.subtitle)

    def test_foreign_and_bad_files(self):
        r = self.app.upload(ADMIN, "фото.jpg", b"\xff\xd8")[0]
        self.assertIn("не похоже на отчёт", r.text)
        r = self.app.upload(ADMIN, "пусто.xlsx", b"")[0]
        self.assertIn("пустой", r.text)
        other = sales_report("Период: 01.10.2026 - 26.10.2026", [["что-то", 1]])
        from openpyxl import load_workbook
        import io
        wb = load_workbook(io.BytesIO(other)); wb.active["A1"] = "Остатки товаров"; buf = io.BytesIO(); wb.save(buf)
        r = self.app.upload(ADMIN, "остатки.xlsx", buf.getvalue())[0]
        self.assertIn("не похоже", lines(r))
        self.assertIn("настроим переводчик", lines(r))

    def test_approved_month_is_closed(self):
        self.db.set_month_status("2026-10", "approved", "тест")
        r = self.app.upload(ADMIN, "продажи.xlsx", self.file)[0]
        self.assertIn("уже утверждён", lines(r))
        self.assertEqual(self.db.sales("2026-10-01", "2026-10-31"), [])

    def test_rights(self):
        self.assertTrue(self.app.upload(600, "продажи.xlsx", self.file)[0].view)    # бухгалтер может
        r = self.app.upload(500, "продажи2.xlsx", self.file)[0]                     # торговый — нет
        self.assertIn("загружают администратор", r.text)
        self.assertIn("загружают", self.app.press(500, "up")[0].text)

    def test_demo_only_explains(self):
        demo = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        demo.press(5, "sw:role:admin")
        r = demo.press(5, "up")[0]
        self.assertFalse(r.upload)
        self.assertIn("демо", lines(r).lower())
        self.assertIn("В демо", demo.upload(5, "продажи.xlsx", self.file)[0].text)

    def test_web_upload(self):
        from core.transport.web import WebApi
        api = WebApi(self.app, admin_key="ключ", base_url="https://firm.example.ru")
        code, h, raw = api.handle("POST", "/api/start", {}, {"admin": "ключ"}, None)
        tok = h["Set-Cookie"].split("kid=")[1].split(";")[0]
        code, h, raw = api.handle("POST", "/api/press", {}, {"to": "up"}, tok)
        self.assertEqual(json.loads(raw)["msgs"][0].get("upload"), 1)
        code, h, raw = api.handle("POST", "/api/upload", {},
                                  {"name": "продажи.xlsx", "data": base64.b64encode(self.file).decode()}, tok)
        d = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertEqual(d["msgs"][0]["view"]["title"], "🧪 Что пришло")
        self.assertEqual(sum(x["amount"] for x in self.db.sales("2026-10-01", "2026-10-31")), 1800)


if __name__ == "__main__":
    unittest.main()
