"""🔌 Связь с 1С: сетевая папка (WebDAV, как Google Диск) и прямая отправка файла из 1С, экран с адресом/паролем/PDF."""
import base64
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import onec_link as L
from core import reports as RP
from core.app import App
from core.transport.web import WebApi
from tests.test_month_ingest import sales_report

TODAY = date(2026, 11, 2)
ADMIN = 1


class Link(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.db = self.app.db()
        self.db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", keys=["иванов петр"], tg_id=500)
        self.api = WebApi(self.app, admin_key="ключ", base_url="https://firma.example.ru")
        self.s = L.secrets_of(self.db)
        self.auth = {"Authorization": "Basic " + base64.b64encode(f"1c:{self.s['password']}".encode()).decode()}
        self.file = sales_report("Период: 01.10.2026 - 31.10.2026", [
            ["Б2 ТП Иванов Пётр Сергеевич", 1500], ["  ИП Ромашка (код 1043)", 1500], ["      Кровля", 1500]])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dav(self, method, path="/dav/", body=b"", **h):
        return self.api.handle_raw(method, path, {**self.auth, **h}, body)

    def test_network_folder(self):
        self.assertEqual(self.api.handle_raw("OPTIONS", "/dav/", {}, b"")[1]["DAV"], "1, 2")
        self.assertEqual(self.api.handle_raw("PROPFIND", "/dav/", {}, b"")[0], 401)          # без пароля — нет
        bad = {"Authorization": "Basic " + base64.b64encode(b"1c:wrong").decode()}
        self.assertEqual(self.api.handle_raw("PROPFIND", "/dav/", bad, b"")[0], 401)
        code, h, xml = self.dav("PROPFIND", Depth="1")
        self.assertEqual(code, 207)
        # Windows: пишет во временный файл, потом переименовывает
        self.assertEqual(self.dav("PUT", "/dav/~tmp123.tmp", self.file)[0], 201)
        self.assertEqual(self.dav("MOVE", "/dav/~tmp123.tmp", Destination="https://firma.example.ru/dav/%D0%9F%D1%80%D0%BE%D0%B4%D0%B0%D0%B6%D0%B8.xlsx")[0], 201)
        inbox = L.inbox_dir(self.app)
        self.assertTrue((inbox / "Продажи.xlsx").exists())
        code, h, xml = self.dav("PROPFIND", Depth="1")
        self.assertIn("Продажи.xlsx".encode(), xml)
        self.assertEqual(self.dav("GET", "/dav/%D0%9F%D1%80%D0%BE%D0%B4%D0%B0%D0%B6%D0%B8.xlsx")[2], self.file)
        self.assertEqual(self.dav("LOCK", "/dav/x.xlsx")[0], 200)
        self.assertEqual(self.dav("PUT", "/dav/вирус.exe", b"MZ")[0], 415)                    # не отчёт — не принимаем
        self.assertEqual(self.dav("PUT", "/dav/sub/x.xlsx", b"1")[0], 403)                    # без вложенных папок
        self.assertIn(self.dav("PUT", "/dav/..%2F..%2Fkpi.db", b"1")[0], (403, 415))         # из папки не выйти
        self.assertFalse((L.inbox_dir(self.app).parent / "kpi.db").stat().st_size == 1)
        # фоновая работа забирает файл из папки (как с Google Диска)
        from core.jobs.worker import Worker, sources
        import os, time
        old = time.time() - 120
        os.utime(inbox / "Продажи.xlsx", (old, old))
        out = []
        Worker(self.app, out.extend, srcs=sources(self.app, env={})).pull()
        self.assertEqual(sum(r["amount"] for r in self.db.sales("2026-10-01", "2026-10-31")), 1500)
        self.assertEqual(RP.log_of(self.db)[0]["who"], "автоматически")

    def test_direct_from_1c(self):
        code, h, raw = self.api.handle_raw("POST", "/api/1c?name=%D0%9F%D1%80%D0%BE%D0%B4%D0%B0%D0%B6%D0%B8.xlsx", {"X-Key": "плохой"}, self.file)
        self.assertEqual(code, 403)
        code, h, raw = self.api.handle_raw("POST", "/api/1c?name=%D0%9F%D1%80%D0%BE%D0%B4%D0%B0%D0%B6%D0%B8.xlsx", {"X-Key": self.s["key"]}, self.file)
        self.assertEqual(code, 200, raw.decode())
        self.assertIn("✅ продажи", raw.decode())
        self.assertEqual(sum(r["amount"] for r in self.db.sales("2026-10-01", "2026-10-31")), 1500)
        self.assertEqual(RP.log_of(self.db)[0]["who"], "1С напрямую")
        self.assertTrue(self.db.get(f"inbox:{ADMIN}") or self.app.office().get(f"inbox:{ADMIN}"))   # админу — «📬 Новые отчёты»
        code, h, raw = self.api.handle_raw("POST", "/api/1c?name=x.xlsx", {"X-Key": self.s["key"]}, b"")
        self.assertEqual(code, 400)

    def test_screen_renew_and_pdf(self):
        r = self.app.press(ADMIN, "ucs")[0]
        text = str([x for k, b in r.view.blocks if k == "lines" for x in b[1]])
        self.assertIn(self.s["password"], text)
        self.assertIn("https://firma.example.ru/dav/", text)
        self.assertIn("\\\\\\\\firma.example.ru@SSL\\\\dav", text)
        self.app.press(ADMIN, "ucs:password:yes")
        self.assertNotEqual(L.secrets_of(self.db)["password"], self.s["password"])
        self.assertEqual(self.dav("PROPFIND")[0], 401)                                        # старый пароль больше не подходит
        f = self.app.press(ADMIN, "ucp")[0]
        self.assertTrue(f.files[0][1].startswith(b"%PDF"))
        from core.ui.views import pdf_pages
        self.assertGreaterEqual(len(pdf_pages(f.files[0][1])), 2)                            # страницы PDF — картинками в приложении
        self.assertTrue(pdf_pages(f.files[0][1])[0].startswith(b"\xff\xd8"))
        code, h, raw = self.api.handle("POST", "/api/start", {}, {"admin": "ключ"}, None)
        tok = h["Set-Cookie"].split("kid=")[1].split(";")[0]
        d = json.loads(self.api.handle("POST", "/api/press", {}, {"to": "ucp"}, tok)[2])
        url = next(m["files"][0][1] for m in d["msgs"] if m.get("files"))
        n = json.loads(self.api.handle("GET", url + "/pages", {}, {}, tok)[2])["n"]
        self.assertGreaterEqual(n, 2)
        code, h, img = self.api.handle("GET", url + "/p0", {}, {}, tok)
        self.assertEqual((code, h["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(self.api.handle("GET", url + f"/p{n}", {}, {}, tok)[0], 404)
        self.assertIn("администратор", self.app.press(500, "ucs")[0].text)                  # торговому — нет

    def test_demo(self):
        demo = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        demo.press(5, "sw:role:admin")
        r = demo.press(5, "ucs")[0]
        self.assertIn("демо", str(r.view.blocks).lower())
        self.assertEqual(WebApi(demo).handle_raw("PROPFIND", "/dav/", {}, b"")[0], 404)


if __name__ == "__main__":
    unittest.main()
