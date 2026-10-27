"""Отчёты 1С из почтового ящика клиента (IMAP). Настройки — в .env клиента:
MAIL_USER, MAIL_PASSWORD (пароль приложения), MAIL_HOST (по умолчанию imap.gmail.com),
MAIL_FROM — с каких адресов принимать (через запятую; пусто — с любых).

Письмо помечается прочитанным только после обработки — при сбое оно не потеряется.
"""
import email
import imaplib
import io
import zipfile
from email.policy import default as policy

EXT = (".xls", ".xlsx")


def files_of(msg):
    out = []
    for part in msg.walk():
        name = part.get_filename()
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        low = name.lower()
        if low.endswith(EXT):
            out.append((name, data))
        elif low.endswith(".zip"):                     # 1С может упаковать отчёты в архив
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        if n.lower().endswith(EXT) and not n.startswith("__MACOSX"):
                            out.append((n.rsplit("/", 1)[-1], z.read(n)))
            except zipfile.BadZipFile:
                pass
    return out


def parse_message(raw):
    msg = email.message_from_bytes(raw, policy=policy)
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    return sender, files_of(msg)


class Mailbox:
    def __init__(self, user, password, host="imap.gmail.com", allowed=""):
        self.user, self.password, self.host = user, password, host
        self.allowed = {a.strip().lower() for a in allowed.split(",") if a.strip()}

    def fetch(self):
        """Непрочитанные письма → ([(имя, байты)], [uid писем])."""
        out, uids = [], []
        with imaplib.IMAP4_SSL(self.host, timeout=60) as m:
            m.login(self.user, self.password)
            m.select("INBOX")
            _, ids = m.uid("SEARCH", None, "UNSEEN")
            for u in (ids[0].split() if ids and ids[0] else []):
                _, data = m.uid("FETCH", u, "(BODY.PEEK[])")
                raw = next((d[1] for d in data if isinstance(d, tuple)), b"")
                sender, files = parse_message(raw)
                if self.allowed and sender not in self.allowed:
                    continue
                out += files
                uids.append(u)
        return out, uids

    def mark_seen(self, uids):
        if not uids:
            return
        with imaplib.IMAP4_SSL(self.host, timeout=60) as m:
            m.login(self.user, self.password)
            m.select("INBOX")
            m.uid("STORE", b",".join(uids), "+FLAGS", "(\\Seen)")
