"""Отчёты 1С из папки на Google Диске: 1С кладёт файлы в сетевую папку, она синхронизируется с Диском,
бот раз в 10 минут забирает новые и изменённые. Папке нужно открыть доступ сервисному аккаунту (читатель).

Нужны пакеты google-auth и requests (ставятся только там, где Диск используется).
"""
from datetime import datetime

API = "https://www.googleapis.com/drive/v3/files"
EXT = (".xls", ".xlsx")


class DriveFolder:
    def __init__(self, folder_id, key_file):
        self.folder_id, self.key_file = folder_id, key_file
        self._s = None

    def session(self):
        if self._s is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(self.key_file,
                                                          scopes=["https://www.googleapis.com/auth/drive.readonly"])
            self._s = AuthorizedSession(creds)
        return self._s

    def list_files(self):
        q = f"'{self.folder_id}' in parents and trashed=false"
        out, token = [], None
        while True:
            params = {"q": q, "fields": "nextPageToken,files(id,name,modifiedTime,size)", "pageSize": 200,
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            r = self.session().get(API, params=params, timeout=60)
            r.raise_for_status()
            d = r.json()
            out += [f for f in d.get("files", []) if f["name"].lower().endswith(EXT)]
            token = d.get("nextPageToken")
            if not token:
                return out

    def download(self, file_id):
        r = self.session().get(f"{API}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}, timeout=120)
        r.raise_for_status()
        return r.content

    def fetch(self, seen):
        """seen: {id: modifiedTime} уже забранных. → ([(имя, байты)], новый seen, [ошибки])."""
        files, new_seen, errors = [], dict(seen), []
        for f in self.list_files():
            if seen.get(f["id"]) == f["modifiedTime"]:
                continue
            try:
                files.append((f["name"], self.download(f["id"])))
                new_seen[f["id"]] = f["modifiedTime"]
            except Exception as e:                     # noqa: BLE001
                errors.append(f"{f['name']}: не скачался ({e})")
        return files, new_seen, errors


def stamp():
    return datetime.now().isoformat(timespec="minutes")
