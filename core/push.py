"""🔔 Уведомления на телефон из своего приложения (Web Push) — без Telegram и без сторонних сервисов (Адам 31.10).

Как работает:
  • приложение (web/app.html) по кнопке «🔔 Включить уведомления» просит разрешение у телефона и присылает «адрес»
    подписки → /api/push → хранится в базе офиса под ключом `push_subs` = {номер пользователя: [подписка…]};
  • когда кому-то приходит сообщение (рассылка утром, «месяц ждёт утверждения», ответ на отзыв) — провод кладёт его
    в ленту и шлёт короткое уведомление на все его телефоны (send_to);
  • на iPhone уведомления приходят, только если приложение добавлено на экран «Домой» (iOS 16.4+); на Android — всегда.

Шифрование и подпись — по стандартам RFC 8291 (aes128gcm) и RFC 8292 (VAPID), на библиотеке cryptography.
Ключ сервера (VAPID) создаётся один раз и хранится в базе офиса (`vapid`), в git не попадает.
Подписка умерла (телефон отписался, 404/410) — удаляется сама.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("kpi")
KEY = "push_subs"
VAPID = "vapid"
TTL = 24 * 3600
MAX_PER_USER = 5
CONTACT = os.environ.get("PUSH_CONTACT", "mailto:admin@kpibotsmith.ru")


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------- ключ сервера ----------

def _ec():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec


def keys(office):
    """(закрытый ключ, открытый ключ в base64url) — создаётся при первом обращении."""
    ec = _ec()
    from cryptography.hazmat.primitives import serialization
    raw = office.get(VAPID)
    if raw:
        priv = serialization.load_der_private_key(unb64u(raw), password=None)
    else:
        priv = ec.generate_private_key(ec.SECP256R1())
        office.put(VAPID, b64u(priv.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption())))
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, b64u(pub)


def public_key(office):
    return keys(office)[1]


def vapid_header(priv, pub_b64, endpoint, now=None):
    """Authorization: vapid t=<JWT ES256>, k=<открытый ключ>."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": CONTACT}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode()
    r, s = decode_dss_signature(priv.sign(signing, _ec().ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={pub_b64}"


# ---------- шифрование (RFC 8291) ----------

def _hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(sub, payload: bytes, salt=None, as_priv=None):
    """Зашифровать для подписки {endpoint, keys: {p256dh, auth}} → тело запроса (aes128gcm)."""
    ec = _ec()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub = unb64u(sub["keys"]["p256dh"])
    auth = unb64u(sub["keys"]["auth"])
    as_priv = as_priv or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    secret = as_priv.exchange(ec.ECDH(), peer)
    ikm = _hkdf(auth, secret, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!L", 4096) + bytes([len(as_pub)]) + as_pub + ct


# ---------- подписки ----------

def subs(office):
    return office.get(KEY) or {}


def subscribe(office, uid, sub):
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") or \
            not (sub.get("keys") or {}).get("p256dh") or not (sub.get("keys") or {}).get("auth"):
        return False
    sub = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}
    all_ = subs(office)
    mine = [s for s in all_.get(str(uid), []) if s["endpoint"] != sub["endpoint"]] + [sub]
    all_[str(uid)] = mine[-MAX_PER_USER:]
    office.put(KEY, all_)
    return True


def unsubscribe(office, uid, endpoint=None):
    all_ = subs(office)
    if endpoint:
        all_[str(uid)] = [s for s in all_.get(str(uid), []) if s["endpoint"] != endpoint]
    else:
        all_.pop(str(uid), None)
    office.put(KEY, {k: v for k, v in all_.items() if v})


def has(office, uid):
    return bool(subs(office).get(str(uid)))


# ---------- отправка ----------

def post(endpoint, body, headers, timeout=10):
    """→ код ответа сервиса уведомлений (201 — принято; 404/410 — подписки больше нет)."""
    req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def send(office, uid, title, body, url="/", tag="", sender=None):
    """Отправить уведомление на все телефоны пользователя. → сколько ушло. sender — подмена отправки в тестах."""
    mine = subs(office).get(str(uid)) or []
    if not mine:
        return 0
    priv, pub = keys(office)
    payload = json.dumps({"title": title[:60], "body": body[:180], "url": url, "tag": tag}, ensure_ascii=False).encode()
    sent, dead = 0, []
    for s in mine:
        try:
            data = encrypt(s, payload)
            code = (sender or post)(s["endpoint"], data, {
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL),
                "Urgency": "normal", "Authorization": vapid_header(priv, pub, s["endpoint"])})
        except Exception:                                   # noqa: BLE001
            log.exception("уведомление %s", uid)
            continue
        if code in (404, 410):
            dead.append(s["endpoint"])
        elif 200 <= code < 300:
            sent += 1
        else:
            log.warning("уведомление %s: ответ %s", uid, code)
    for e in dead:
        unsubscribe(office, uid, e)
    return sent


def summary(reply_json):
    """Заголовок и текст уведомления из сообщения приложения."""
    v = reply_json.get("view") or {}
    text = reply_json.get("text") or ""
    title = v.get("title") or (text.split("\n", 1)[0] if text else "Новое сообщение")
    body = v.get("sub") or (text.split("\n", 1)[1] if "\n" in text else "") or "Откройте приложение"
    return title, " ".join(body.split())


def send_async(office_fn, uid, title, body, url="/"):
    """В фоне, чтобы нажатие не ждало сервис уведомлений. office_fn — как получить базу офиса в потоке."""
    def go():
        try:
            send(office_fn(), uid, title, body, url)
        except Exception:                                   # noqa: BLE001
            log.exception("уведомление в фоне")
    threading.Thread(target=go, daemon=True).start()
