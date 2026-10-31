"""🔄 Процессы отдела: описания по шагам, запуск, отметки окном с «ОК», следующий шаг — ответственному, просрочки,
задания отделу, свои описания текстом; функция — в «⭐ Дополнительно», цена правится, входит в «Максимум»."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.access import editions as E
from core.access import pricing as P
from core.app import App

TODAY = date(2026, 10, 31)


class Processes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[1], data_dir=self.tmp)
        self.app.press(1, "sw:ed:max")
        self.app.press(1, "sw:role:owner")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runs(self):
        return self.app.ctx(1).db.get("proc_runs")

    def test_function_in_max_with_price(self):
        self.assertIn("processes", E.preset_functions("max"))
        self.assertNotIn("processes", E.preset_functions("company"))
        self.assertEqual(P.DEFAULT["f_processes"], 5000)

    def test_director_sees_departments_and_marks_steps(self):
        hub = self.app.press(1, "prc")[0]
        self.assertEqual(hub.view.title, "🔄 Процессы отделов")
        self.assertTrue(any("⚠️" in b.label for b in hub.all_buttons()))           # демо: есть просрочка
        r = next(x for x in self.runs() if x["kind"] == "purchasing")
        n_done = sum(1 for s in r["steps"] if s["by"])
        self.app.press(1, f"prk:{r['id']}")
        self.app.press(1, f"prk:{r['id']}:t:{n_done}")
        self.assertEqual(sum(1 for s in next(x for x in self.runs() if x["id"] == r["id"])["steps"] if s["by"]), n_done)  # до «ОК» — нет
        self.app.press(1, f"prk:{r['id']}:ok")
        self.assertEqual(sum(1 for s in next(x for x in self.runs() if x["id"] == r["id"])["steps"] if s["by"]), n_done + 1)

    def test_start_task_and_own_template(self):
        self.app.press(1, "prc")
        before = len(self.runs() or [])
        self.app.press(1, "prn")
        self.app.press(1, "prn:t:1")
        self.assertTrue(self.app.press(1, "prn:ok")[-1].ask)
        self.assertIn("Опт-Снаб", self.app.text(1, "Опт-Снаб")[-1].view.title)
        self.app.press(1, "prq")
        self.app.text(1, "Инвентаризация витрины до 05.11")
        self.app.press(1, "prqd:t:warehouse")
        self.app.press(1, "prqd:ok")
        rs = self.runs()
        self.assertEqual(len(rs), before + 2)
        self.assertEqual((rs[-1]["kind"], rs[-1]["steps"][0]["due"]), ("warehouse", "2026-11-05"))
        self.app.press(1, "prdn:purchasing")
        self.app.text(1, "Возврат поставщику\nСобрать брак — склад — 1\nСогласовать — начальник — 1\nВернуть — Менеджер по закупкам — 2")
        t = self.app.ctx(1).db.get("proc_tpl")[-1]
        self.assertEqual([s[1] for s in t["steps"]], ["staff:warehouse", "head", "pos:buyer"])

    def test_locked_without_function(self):
        self.app.press(1, "sw:ed:company")
        self.assertIn("Процессы", self.app.press(1, "prc")[0].view.title)                # замок с описанием и ценой


if __name__ == "__main__":
    unittest.main()
