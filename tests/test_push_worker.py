"""🔔 Уведомления (Web Push: шифрование, подпись, подписки) и фоновая работа (рассылки по расписанию, забор отчётов из папки)."""
import base64
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

from core import push as PU
from core import reports as RP
from core.app import App
from core.ingest.folder import InboxFolder
from core.jobs.worker import Worker
from tests.test_month_ingest import sales_report

TODAY = date(2026, 11, 2)
ADMIN = 1


def fake_subscription():
    """Подписка, как её даёт браузер, + закрытый ключ телефона — чтобы расшифровать и проверить."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return {"endpoint": "https://push.example.com/send/abc", "keys": {"p256dh": PU.b64u(pub), "auth": PU.b64u(auth)}}, priv


def decrypt(body, ua_priv, sub):
    """Расшифровка на стороне телефона (RFC 8291) — для проверки, что мы шифруем правильно."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, idlen = body[:16], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    ua_pub, auth = PU.unb64u(sub["keys"]["p256dh"]), PU.unb64u(sub["keys"]["auth"])
    secret = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = PU._hkdf(auth, secret, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = PU._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = PU._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    assert pt.endswith(b"\x02")
    return pt[:-1]


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.office = self.app.office()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_encrypt_roundtrip_and_vapid(self):
        sub, ua_priv = fake_subscription()
        body = PU.encrypt(sub, "Привет 🔔".encode())
        self.assertEqual(decrypt(body, ua_priv, sub).decode(), "Привет 🔔")
        priv, pub = PU.keys(self.office)
        self.assertEqual(PU.keys(self.office)[1], pub)                       # ключ сервера один и тот же
        self.assertEqual(len(PU.unb64u(pub)), 65)
        h = PU.vapid_header(priv, pub, sub["endpoint"], now=1_800_000_000)
        token = h.split("t=")[1].split(",")[0]
        head, claims, sig = token.split(".")
        c = json.loads(PU.unb64u(claims))
        self.assertEqual(c["aud"], "https://push.example.com")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        raw = PU.unb64u(sig)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        priv.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))   # подпись верна

    def test_subscribe_send_and_dead(self):
        sub, ua_priv = fake_subscription()
        self.assertFalse(PU.subscribe(self.office, 77, {"endpoint": "http://bad"}))
        self.assertTrue(PU.subscribe(self.office, 77, sub))
        self.assertTrue(PU.has(self.office, 77))
        got = []
        n = PU.send(self.office, 77, "Утренняя сводка", "План 140 000", sender=lambda url, body, h: got.append((url, body, h)) or 201)
        self.assertEqual(n, 1)
        url, body, h = got[0]
        self.assertEqual(h["Content-Encoding"], "aes128gcm")
        self.assertTrue(h["Authorization"].startswith("vapid t="))
        self.assertEqual(json.loads(decrypt(body, ua_priv, sub))["title"], "Утренняя сводка")
        PU.send(self.office, 77, "x", "y", sender=lambda *a: 410)              # телефон отписался — подписка удалена
        self.assertFalse(PU.has(self.office, 77))

    def test_web_endpoints_and_delivery_pushes(self):
        from core.transport.web import WebApi
        api = WebApi(self.app, admin_key="ключ", base_url="https://firm.example.ru")
        code, h, raw = api.handle("POST", "/api/start", {}, {"admin": "ключ"}, None)
        tok = h["Set-Cookie"].split("kid=")[1].split(";")[0]
        self.assertIs(json.loads(raw)["push"], False)                        # админ ещё не подписан — баннер покажется
        key = json.loads(api.handle("GET", "/api/push-key", {}, {}, tok)[2])["key"]
        self.assertEqual(key, PU.public_key(self.office))
        sub, _ = fake_subscription()
        d = json.loads(api.handle("POST", "/api/push", {}, {"sub": sub}, tok)[2])
        self.assertTrue(d["ok"])
        uid = int(self.office.get("web_devices")[tok])
        self.assertTrue(PU.has(self.office, uid))
        sent = []
        orig = PU.send_async
        PU.send_async = lambda office_fn, to, title, body, url="/": sent.append((to, title, body))
        try:
            from core.ui.kit import Reply
            api._deliver(0, [Reply(text="📬 Новые отчёты 1С\n✅ продажи", to=uid)])
        finally:
            PU.send_async = orig
        self.assertEqual(sent, [(uid, "📬 Новые отчёты 1С", "✅ продажи")])
        self.assertIn("sw.js", api.static)
        self.assertIn(b'addEventListener("push"', api.static["sw.js"][0])
        api.handle("POST", "/api/push", {}, {"off": 1}, tok)
        self.assertFalse(PU.has(self.office, uid))


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.db = self.app.db()
        self.db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", keys=["иванов петр"], tg_id=500)
        self.inbox = InboxFolder(self.tmp / "inbox")
        self.out = []
        self.w = Worker(self.app, self.out.extend, srcs=[("папка", self.inbox)])
        self.file = sales_report("Период: 01.10.2026 - 31.10.2026", [
            ["Б2 ТП Иванов Пётр Сергеевич", 1500], ["  ИП Ромашка (код 1043)", 1500], ["      Кровля", 1500]])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, data, age=120):
        self.inbox.path.mkdir(parents=True, exist_ok=True)
        f = self.inbox.path / name
        f.write_bytes(data)
        t = time.time() - age
        os.utime(f, (t, t))
        return f

    def test_pull_from_folder(self):
        self.put("Продажи октябрь.xlsx", self.file)
        fresh = self.put("Ещё пишется.xlsx", self.file, age=5)                 # дописывается — ждёт
        self.put("readme.pdf", b"%PDF")                                        # не отчёт — не трогаем
        msgs = self.w.pull()
        self.assertEqual(sum(r["amount"] for r in self.db.sales("2026-10-01", "2026-10-31")), 1500)
        self.assertTrue(fresh.exists())
        self.assertFalse((self.inbox.path / "Продажи октябрь.xlsx").exists())
        self.assertTrue(list((self.inbox.path / "done").rglob("Продажи октябрь.xlsx")))
        self.assertEqual(RP.log_of(self.db)[0]["who"], "автоматически")
        self.assertTrue(any(m.to == ADMIN and "Новые отчёты" in m.text for m in msgs))
        self.assertEqual(self.w.pull(), [])                                    # нечего — тишина

    def test_auto_skips_month_on_review_and_bad_files(self):
        self.db.set_month_status("2026-10", "review", "тест")
        self.put("Продажи.xlsx", self.file)
        self.put("битый.xlsx", b"not an excel")
        msgs = self.w.pull()
        self.assertEqual(self.db.sales("2026-10-01", "2026-10-31"), [])       # на согласовании — только кнопкой
        log = {e["name"]: e for e in RP.log_of(self.db)}
        self.assertIn("на согласовании", " ".join(log["Продажи.xlsx"]["skip"]))
        self.assertTrue(log["битый.xlsx"]["err"])
        self.assertTrue(list((self.inbox.path / "bad").glob("битый.xlsx")))
        self.assertTrue(msgs)

    def test_tick_runs_jobs_and_pull_every_10_minutes(self):
        self.put("Продажи.xlsx", self.file)
        morning = self.app.cfg.raw.get("schedule", {}).get("morning", "08:00")
        h, m = map(int, morning.split(":"))
        now = datetime(2026, 11, 2, h, m)                                      # понедельник
        out = self.w.tick(now)
        self.assertTrue(any(r.to == 500 for r in out))                         # торговому — утренняя сводка
        self.assertTrue(any("Новые отчёты" in (r.text or "") for r in out))
        self.assertEqual(self.out, out)
        self.put("Продажи 2.xlsx", self.file)
        later = datetime(2026, 11, 2, h, m + 3) if m < 55 else datetime(2026, 11, 2, h + 1, 1)
        self.w.tick(later)
        self.assertTrue((self.inbox.path / "Продажи 2.xlsx").exists())          # 3 минуты спустя — ещё не забираем

    def test_demo_has_no_background(self):
        demo = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        from core.transport.web import WebApi
        self.assertIsNone(WebApi(demo).background())
        self.assertEqual(Worker(demo, self.out.extend, srcs=[]).tick(datetime(2026, 11, 2, 8, 0)), [])

    def test_sources_from_config(self):
        from core.jobs.worker import sources
        self.app.cfg.source = {"kind": "folder"}
        s = sources(self.app, env={})
        self.assertEqual(s[0][0], "папка")
        self.assertEqual(s[0][1].path, self.tmp / "_template" / "inbox")
        self.app.cfg.source = {"kind": "mail"}
        self.assertEqual([t for t, _ in sources(self.app, env={})], ["папка"])   # почты нет в .env — только своя папка


if __name__ == "__main__":
    unittest.main()
