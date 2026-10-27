"""Переводчик отчётов 1С клиента → нейтральные пачки ядра. Пишется под каждого клиента по образцам его файлов.

Этот образец понимает самый частый отчёт «Продажи» из 1С:УТ с группировками
Агент → Контрагент → Номенклатура.Основной поставщик (группа) и показателем «Сумма», за месяц:

    Продажи
    Период: 01.10.2026 - 31.10.2026
    Показатели: Сумма
    Группировки строк: Агент, Контрагент, Основной поставщик
    ...
    Иванов Пётр Сергеевич                 1 250 000
      ИП Ромашка (код 1043)                 300 000
        Сухие смеси                         120 000

Для настоящего клиента: взять 2–3 его файла, поправить распознавание строк, прогнать
python3 tools/check_client.py <код> файлы… — бот покажет, что понял.
"""
import re
from datetime import date

from core.data.model import Dataset, Sale
from core.ingest.excel import cell, detect_period, head_text, num, person_key, read_grid, title

CODE = re.compile(r"\((?:код|КОД)\s*(\d+)\)")


def parse(name, data):
    g = read_grid(data)
    head = head_text(g)
    if not title(g).lower().startswith("продажи"):
        return []                                  # не наш отчёт
    month, on = detect_period(head)
    if not month:
        return []
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    end = on or start
    rows, agent, client = [], None, None
    for r in range(len(g)):
        text = str(cell(g, r, 0))
        amount = num(cell(g, r, 1))
        level = len(text) - len(text.lstrip(" "))
        text = text.strip()
        if not text or text.startswith(("Период", "Показатели", "Группировки", "Отбор", "Продажи", "Итог")):
            continue
        if level == 0:
            agent = person_key(text)
        elif level <= 2:
            mm = CODE.search(text)
            client = mm.group(1) if mm else text
        elif agent and client:
            rows.append(Sale(end, agent, client, text, amount))
    return [Dataset("sales", start, end, rows=rows, source=name)]
