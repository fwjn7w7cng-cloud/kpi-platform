"""Защита приложения (Адам 01.11): перебор ключей и приглашений, чужие вложения, заголовки браузеру, адрес уведомлений."""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App
from core.transport.web import AUTH_FAILS, REQ_LIMIT, CSP, SEC_HEADERS, Throttle, WebApi

TODAY = date(2026, 11, 2)


class Security(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[], data_dir=self.tmp, demo_gate=True)
        self.api = WebApi(self.app, admin_key="секрет-админа", base_url="https://demo.example.ru")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, body, ip, token=None):
        return self.api.handle("POST", "/api/start", {}, body, token, ip=ip)

    def test_throttle(self):
        t = Throttle(3, 60)
        self.assertFalse(t.hit("a", now=100)); self.assertFalse(t.hit("a", now=101)); self.assertFalse(t.hit("a", now=102))
        self.assertTrue(t.hit("a", now=103))                     # четвёртый за окно — лишний
        self.assertTrue(t.blocked("a", now=104))
        self.assertFalse(t.blocked("a", now=170))                 # окно прошло
        self.assertFalse(t.blocked("b", now=104))                 # другой адрес не задет

    def test_wrong_admin_key_counts_and_blocks(self):
        for i in range(AUTH_FAILS):
            code, h, raw = self.start({"admin": f"мимо-{i}"}, "1.2.3.4")
            self.assertEqual(code, 200)                            # не пустили, но ответили как гостю
        self.assertEqual(self.start({"admin": "секрет-админа"}, "1.2.3.4")[0], 429)    # даже с верным — адрес уже на паузе
        self.assertEqual(self.start({}, "1.2.3.4")[0], 200)        # обычный вход без ключа работает
        self.assertEqual(self.start({"admin": "секрет-админа"}, "5.6.7.8")[0], 200)    # другой адрес — пожалуйста

    def test_bad_invite_counts(self):
        for i in range(AUTH_FAILS):
            self.assertEqual(self.start({"arg": f"dнетакого{i}"}, "9.9.9.9")[0], 200)
        self.assertEqual(self.start({"arg": "dещё"}, "9.9.9.9")[0], 429)

    def test_request_flood(self):
        tok = None
        for i in range(REQ_LIMIT):
            code, h, raw = self.api.handle("POST", "/api/press", {}, {"to": "home"}, tok, ip="7.7.7.7")
            tok = tok or h.get("Set-Cookie", "").split("kid=")[1].split(";")[0]
            self.assertEqual(code, 200)
        self.assertEqual(self.api.handle("POST", "/api/press", {}, {"to": "home"}, tok, ip="7.7.7.7")[0], 429)
        self.assertEqual(self.api.handle("POST", "/api/press", {}, {"to": "home"}, None, ip="7.7.7.8")[0], 200)

    def test_file_only_for_owner(self):
        code, h, raw = self.start({"admin": "секрет-админа"}, "1.1.1.1")
        tok = h["Set-Cookie"].split("kid=")[1].split(";")[0]
        d = json.loads(self.api.handle("POST", "/api/press", {}, {"to": "ptm"}, tok, ip="1.1.1.1")[2])
        urls = [u for m in d["msgs"] for n, u in m.get("files", [])]
        if not urls:                                               # у инструкции партнёра файла нет — берём PDF памятки админа
            d = json.loads(self.api.handle("POST", "/api/press", {}, {"to": "ptp"}, tok, ip="1.1.1.1")[2])
            urls = [u for m in d["msgs"] for n, u in m.get("files", [])]
        self.assertTrue(urls, "нужен экран с файлом")
        url = urls[0]
        self.assertEqual(self.api.handle("GET", url, {}, {}, tok, ip="1.1.1.1")[0], 200)           # владелец
        code, h2, raw = self.start({}, "2.2.2.2")
        other = h2["Set-Cookie"].split("kid=")[1].split(";")[0]
        self.assertEqual(self.api.handle("GET", url, {}, {}, other, ip="2.2.2.2")[0], 404)        # чужой телефон
        self.assertEqual(self.api.handle("GET", url, {}, {}, None, ip="3.3.3.3")[0], 404)         # без cookie
        self.assertEqual(self.api.handle("GET", url + "/pages", {}, {}, other, ip="2.2.2.2")[2], b'{"n": 0}')

    def test_headers(self):
        code, h, raw = self.api.handle("GET", "/", {}, {}, None)
        self.assertEqual(h["Content-Security-Policy"], CSP)
        self.assertIn("frame-ancestors 'none'", CSP)
        self.assertIn("script-src 'self'", CSP)
        self.assertEqual(SEC_HEADERS["X-Frame-Options"], "DENY")
        self.assertIn('name="referrer" content="same-origin"', raw.decode())

    def test_push_endpoint_public_only(self):
        from core import push
        self.assertFalse(push.public_endpoint("http://push.example.com/x"))                 # не https
        self.assertFalse(push.public_endpoint("https://127.0.0.1/x"))                      # адрес цифрами
        self.assertFalse(push.public_endpoint("https://localhost/x"))                      # своя машина
        self.assertFalse(push.public_endpoint("https://push.example.com:8101/x"))          # чужой порт
        self.assertTrue(push.public_endpoint("https://push.example.com/send/abc"))


if __name__ == "__main__":
    unittest.main()
