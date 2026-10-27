"""Инструменты для переводчиков отчётов 1С: прочитать xls/xlsx в таблицу, найти шапку отчёта
(«Период / Показатели / Группировки / Отборы»), понять месяц и дату среза, числа с пробелами.

Переводчик клиента (clients/<id>/parser.py) пользуется ими и отдаёт нейтральные пачки (core.data.model).
Отчёт узнаётся по содержимому, а не по имени файла: имя 1С часто меняет сама.
"""
import calendar
import io
import re
from datetime import date, datetime, timedelta

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
HEAD_ROWS = 12


def read_grid(data: bytes):
    """Первый лист файла → список строк (пустые ячейки — "")."""
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":          # старый Excel .xls
        import xlrd
        sh = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        return [[("" if c is None else c) for c in sh.row_values(r)] for r in range(sh.nrows)]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    return [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)]


def cell(g, r, c):
    return g[r][c] if r < len(g) and c < len(g[r]) else ""


def num(v):
    """«1 234,50» / 1234.5 / "" → 1234.5 / 0."""
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def head_text(g, rows=HEAD_ROWS):
    """Шапка отчёта одной строкой: название, Период, Показатели, Группировки, Отборы."""
    out = []
    for r in g[:rows]:
        line = " ".join(str(x) for x in r if str(x).strip())
        if line:
            out.append(line)
    return "\n".join(out)


def title(g):
    for r in g[:5]:
        for x in r:
            if str(x).strip():
                return str(x).strip()
    return ""


DATE_BOUND = re.compile(r"(>=|<=|>|<)\s*(\d\d)\.(\d\d)\.(\d{4})(?:\s+(\d{1,2}):(\d\d):(\d\d))?")


def month_of_date_filter(head, field="Дата"):
    """Месяц из отбора по дате. 1С пишет границы по-разному («> 01.10.2026 0:00:00 И < 01.11.2026»,
    «> 30.09.2026 23:59:59 И < 01.11.2026», бывает и >= / <=). Надёжнее всего верхняя граница."""
    line = re.search(rf"{field}\s*((?:>=|<=|>|<)\s*\d\d\.\d\d\.\d{{4}}[^;\n]*)", head)
    if not line:
        return None
    bounds = {}
    for op, d, mo, y, hh, mm, ss in DATE_BOUND.findall(line.group(1)):
        try:
            bounds[op] = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            continue
    sec = timedelta(seconds=1)
    if "<" in bounds:
        t = bounds["<"] - sec
    elif "<=" in bounds:
        t = bounds["<="]
    elif ">" in bounds:
        t = bounds[">"] + sec
    elif ">=" in bounds:
        t = bounds[">="]
    else:
        return None
    return f"{t.year}-{t.month:02d}"


def detect_period(head):
    """(месяц «ГГГГ-ММ», дата среза или None) по шапке: отбор по дате, «на конец дня», «Период: … - …»,
    «Период: Октябрь 2026 г.»."""
    m = month_of_date_filter(head)
    if m:
        return m, None
    x = re.search(r"на конец дня\s*(\d\d)\.(\d\d)\.(\d{4})", head)
    if x:
        d = date(int(x.group(3)), int(x.group(2)), int(x.group(1)))
        return f"{d.year}-{d.month:02d}", d
    x = re.search(r"Период:\s*(\d\d)\.(\d\d)\.(\d{4})\s*-\s*(\d\d)\.(\d\d)\.(\d{4})", head)
    if x:
        d = date(int(x.group(6)), int(x.group(5)), int(x.group(4)))
        return f"{d.year}-{d.month:02d}", d
    x = re.search(r"Период:\s*([А-Яа-яЁё]+)\s+(\d{4})", head)
    if x:
        w = x.group(1).lower()
        for stem, n in sorted(MONTHS.items(), key=lambda t: -len(t[0])):
            if w.startswith(stem):
                y = int(x.group(2))
                return f"{y}-{n:02d}", date(y, n, calendar.monthrange(y, n)[1])
    return None, None


def person_key(text, words=2):
    """«Б2 ТП Иванов Пётр Сергеевич» → «иванов петр»: служебные приставки 1С отбрасываются,
    берутся первые слова, похожие на фамилию и имя."""
    parts = [p for p in str(text).replace("ё", "е").replace("Ё", "Е").split()
             if p[:1].isalpha() and p[:1].isupper() and not p.isupper() and not any(ch.isdigit() for ch in p)]
    return " ".join(parts[:words]).lower()
