"""📌 Задания (modules/tasks.py, Адам 19.11 — как в первом боте): поручить одной формой, ответ исполнителя, решения автора,
повтор одной карточкой, заметки себе, перенос на доске, похожее, поиск, кто кому даёт, напоминания и выдача в 09:00."""
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from core.app import App
from core.ui.web import reply_json
from modules import tasks as T

TODAY = date(2026, 11, 17)          # вторник
ADMIN = 1


def ui(out, kind):
    for r in out:
        d = reply_json(r)
        for b in (d.get("view") or {}).get("blocks", []):
            if b["k"] == "ui" and b["t"] == kind:
                return b["d"]
    return None


def texts(out):
    return " ".join((reply_json(r).get("text") or "") + " " + str((reply_json(r).get("view") or {}).get("title", "")) + " " + " ".join(str(b) for b in (reply_json(r).get("view") or {}).get("blocks", []))
                    for r in out)


class Tasks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.app.press(ADMIN, "sw:role:owner")
        self.ctx = self.app.ctx(ADMIN)
        self.db = self.ctx.db
        self.owner = self.ctx.me
        es = self.db.employees()
        self.rep = next(e for e in es if e["role"] == "rep")
        self.sv = next(e for e in es if e["role"] == "sv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def as_(self, emp):
        self.app.press(ADMIN, f"sw:as:{emp['id']}")
        return self.app.ctx(ADMIN)

    def give(self, form, who=None):
        self.as_(who or self.owner)
        return self.app.press(ADMIN, "tks", form=form)

    def last_item(self):
        return T.item(self.db, self.db.one("SELECT MAX(id) AS m FROM task_items")["m"])

    def test_give_answer_accept(self):
        out = self.give({"text": "Сверить остатки труб на складе", "people": str(self.rep["id"]), "kind": "once",
                         "dl": (TODAY + timedelta(days=1)).isoformat(), "prio": "hi"})
        self.assertIn("отправлено", texts(out))
        x = self.last_item()
        self.assertEqual((x["who"], x["status"]), (self.rep["id"], "work"))
        self.as_(self.rep)
        b = ui(self.app.press(ADMIN, "tasks"), "board")
        mine = [c for c in b["cards"] if c["id"] == x["id"]]
        self.assertTrue(mine and mine[0]["k"] == "me" and mine[0]["p"] == "hi")
        out = self.app.press(ADMIN, f"tkas:{x['id']}", form={"ans": "no", "text": ""})
        self.assertIn("Напишите комментарий", texts(out))                                   # ❌ без пояснения — нельзя
        self.app.press(ADMIN, f"tkas:{x['id']}", form={"ans": "ok", "text": "Сверил, всё сходится"})
        self.assertEqual(self.last_item()["status"], "done")
        self.as_(self.owner)
        b = ui(self.app.press(ADMIN, "tasks"), "board")
        c = next(c for c in b["cards"] if c["id"] == x["id"])
        self.assertTrue(c["wait"] and c["ck"] == f"tkac:{x['id']}")                         # «ждёт 👍», кружок — принять
        self.app.press(ADMIN, f"tkac:{x['id']}")
        self.assertEqual(T.item(self.db, x["id"])["status"], "ok")

    def test_fail_then_edit_and_move_request(self):
        self.give({"text": "Обзвонить спящих клиентов", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat()})
        x = self.last_item()
        self.as_(self.rep)
        self.app.press(ADMIN, f"tkas:{x['id']}", form={"ans": "mv", "to": (TODAY + timedelta(days=3)).isoformat(), "text": "Клиенты в отъезде"})
        self.assertEqual(T.item(self.db, x["id"])["move"]["state"], "ask")
        self.as_(self.owner)
        self.assertGreaterEqual(T.badge(self.app.ctx(ADMIN)), 1)                             # решение — в числе на кнопке
        self.app.press(ADMIN, f"tkma:{x['id']}:{(TODAY + timedelta(days=3)).isoformat()}")
        self.assertEqual(T.item(self.db, x["id"])["deadline"], (TODAY + timedelta(days=3)).isoformat())
        self.as_(self.rep)
        self.app.press(ADMIN, f"tkas:{x['id']}", form={"ans": "no", "text": "Не дозвонился"})
        self.as_(self.owner)
        self.app.press(ADMIN, f"tkeds:{x['id']}", form={"text": "Обзвонить и написать в WhatsApp", "dl": (TODAY + timedelta(days=2)).isoformat()})
        y = T.item(self.db, x["id"])
        self.assertEqual((y["status"], y["deadline"]), ("work", (TODAY + timedelta(days=2)).isoformat()))

    def test_repeat_one_card(self):
        self.give({"text": "Отчёт о кассе", "people": str(self.rep["id"]), "kind": "rep", "freq": "daily", "days": "1"})
        t = T.task(self.db, self.db.one("SELECT MAX(id) AS m FROM task_defs")["m"])
        n0 = len(T.items(self.db, "task_id=?", t["id"]))
        # копии сегодня может не быть (вне 9–17) — выдача в 09:00
        self.db.put("tk_issued", "")
        T.issue(self.app, datetime(2026, 11, 17, 9, 0))
        xs = T.items(self.db, "task_id=? AND status='work'", t["id"])
        self.assertEqual(len(xs), 1)
        self.as_(self.rep)
        self.app.press(ADMIN, f"tkas:{xs[0]['id']}", form={"ans": "ok", "text": ""})
        nxt = T.items(self.db, "task_id=? AND status='work'", t["id"])
        self.assertEqual(len(nxt), 1)
        self.assertEqual(nxt[0]["deadline"], (TODAY + timedelta(days=1)).isoformat())       # следующая — на завтра, одна
        self.assertGreaterEqual(len(T.items(self.db, "task_id=?", t["id"])), n0 + 1)

    def test_rules(self):
        cfg = self.app.cfg
        self.assertTrue(T.due_today(cfg, {"type": "weekly", "days": [1]}, TODAY))
        self.assertFalse(T.due_today(cfg, {"type": "weekly", "days": [0]}, TODAY))
        self.assertTrue(T.due_today(cfg, {"type": "monthly", "days": [17]}, TODAY))
        self.assertTrue(T.due_today(cfg, {"type": "quarterly", "day": 17, "from": "2026-08"}, TODAY))
        self.assertFalse(T.due_today(cfg, {"type": "quarterly", "day": 17, "from": "2026-09"}, TODAY))
        self.assertTrue(T.due_today(cfg, {"type": "every", "n": 7, "from": "2026-11-10"}, TODAY))
        sun = date(2026, 11, 22)                                                              # воскресенье — выходной демо
        self.assertFalse(T.due_today(cfg, {"type": "daily"}, sun))
        self.assertTrue(T.due_today(cfg, {"type": "monthly", "days": [22]}, sun + timedelta(days=1)))   # выпало на выходной — в понедельник

    def test_notes_and_move(self):
        self.as_(self.owner)
        self.app.press(ADMIN, f"tkq:{TODAY.isoformat()}", form={"text": "Позвонить юристу"})
        x = self.last_item()
        t = T.task(self.db, x["task_id"])
        self.assertTrue(t["self"])
        self.app.press(ADMIN, f"tkm:{x['id']}:{(TODAY + timedelta(days=2)).isoformat()}")
        self.assertEqual(T.item(self.db, x["id"])["deadline"], (TODAY + timedelta(days=2)).isoformat())
        self.app.press(ADMIN, f"tkx:{x['id']}:1")
        self.assertEqual(T.item(self.db, x["id"])["status"], "ok")
        self.as_(self.rep)                                                                    # чужую заметку не видно
        b = ui(self.app.press(ADMIN, "tasks"), "board")
        self.assertFalse(any(c["id"] == x["id"] for c in b["cards"]))

    def test_who_gives_whom(self):
        ctx = self.as_(self.sv)
        ids = {i for i, _, _ in T.people(ctx)}
        team = {e["id"] for e in self.db.employees() if e["role"] in ("rep", "newbie") and e["team"] == self.sv["team"]}
        self.assertEqual(ids, team)                                                           # СВ — только своим торговым
        ctx = self.as_(self.rep)
        self.assertFalse(T.is_author(ctx))
        out = self.app.press(ADMIN, "tkn")
        self.assertIn("руководители", texts(out))

    def test_similar_and_search(self):
        self.give({"text": "Сверка с поставщиком Юг за октябрь", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat()})
        out = self.give({"text": "Сверка с поставщиком Юг за ноябрь", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat()})
        self.assertIn("Проверьте", texts(out))                                               # похожее — сначала показать
        self.assertIn("tkgo:", str([b for r in out for b in reply_json(r).get("buttons", [])]))
        found = ui(self.app.press(ADMIN, "tkf", form={"q": "свер юг"}), "rows")
        self.assertTrue(found and found["items"])

    def test_reminders_and_pay_units(self):
        self.give({"text": "Срочно: акт сверки", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat(), "prio": "hi"})
        ctx = self.as_(self.rep)
        r = T.reminder(ctx)
        self.assertTrue(r and "акт сверки" in r.text)
        self.assertTrue(T.reminder(ctx, urgent_only=True))
        from core.pay import components as C
        self.assertIn("tasks_late", C.UNITS)

    def test_photo(self):
        png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
        self.give({"text": "Проверить выкладку по фото", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat(), "photo": png})
        t = T.task(self.db, self.db.one("SELECT MAX(id) AS m FROM task_defs")["m"])
        self.assertTrue(t["photo"])
        self.as_(self.rep)
        out = self.app.press(ADMIN, f"tko:{self.last_item()['id']}")
        self.assertTrue(any(reply_json(r).get("files") for r in out))
        out = self.give({"text": "Ещё раз", "people": str(self.rep["id"]), "kind": "once", "dl": TODAY.isoformat(), "photo": "data:text/html;base64,PGI+"})
        self.assertIn("фото не прикрепилось", texts(out))


if __name__ == "__main__":
    unittest.main()
