"""Отчёты 1С из папки на нашем сервере: 1С (или «курьер» — rclone с Яндекс.Диска / Google Диска / FTP клиента) кладёт
файлы в `data/<клиент>/inbox/`, бот раз в 10 минут забирает их и переносит в `inbox/done/<дата>/` (или `inbox/bad/`,
если файл не прочитался). Файл, который ещё дописывается (менялся меньше минуты назад), ждёт следующего раза.

Анкета: source: {kind: folder} — папка по умолчанию; можно указать свою: source: {kind: folder, path: /srv/1c/firma}.
"""
import shutil
import time
from datetime import datetime
from pathlib import Path

EXT = (".xls", ".xlsx", ".csv", ".txt", ".xml", ".mxl", ".html", ".htm")
SETTLE = 60          # секунд без изменений — файл дописан


class InboxFolder:
    def __init__(self, path):
        self.path = Path(path)

    def fetch(self, now=None):
        """→ [(имя, байты, путь)] — готовые файлы (без подпапок done/ и bad/)."""
        now = now or time.time()
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)
            return []
        out = []
        for f in sorted(self.path.iterdir()):
            if not f.is_file() or f.name.startswith((".", "~$")) or not f.name.lower().endswith(EXT):
                continue
            if now - f.stat().st_mtime < SETTLE:
                continue
            out.append((f.name, f.read_bytes(), f))
        return out

    def done(self, f, ok=True):
        """Убрать обработанный файл: done/<дата>/ или bad/; одноимённый — с временем в имени."""
        sub = self.path / ("done" if ok else "bad") / (datetime.now().strftime("%Y-%m-%d") if ok else "")
        sub.mkdir(parents=True, exist_ok=True)
        dest = sub / f.name
        if dest.exists():
            dest = sub / f"{f.stem} {datetime.now():%H%M%S}{f.suffix}"
        shutil.move(str(f), str(dest))
        return dest
