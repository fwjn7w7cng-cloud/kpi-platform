"""👥 Сотрудники и доступ у настоящего клиента: добавить человека, пригласить одноразовой ссылкой, вход по ней,
закрыть доступ, уволить, роль «Администратор» у сотрудника клиента, начальник — только свой отдел, имена из 1С."""
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core import people as P
from core.app import App
from core.data.model import Dataset, Sale

TODAY = date(2026, 10, 31)
ADMIN = 1


def code_of(out):
    return out[0].text.split("?start=")[1].split("»")[0].strip()


def buttons(r):
    return [b.to for row in r.buttons for b in row]


def texts(r):
    """Весь текст экрана: ячейки таблиц, строки, плитки, заметки."""
    out = []
    for kind, data in r.view.blocks:
        if kind == "table":
            out += [c.text for row in data[2] for c in row.cells]
        else:
            out.append(str(data))
    return " | ".join(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.app.web_url = "https://firm.example.ru"
        self.db = self.app.db()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, who, dept, name, pos, team=None):
        self.app.press(who, f"pad:{dept}")
        self.app.text(who, name)
        out = self.app.press(who, f"padp:{pos}")
        if team:
            out = self.app.press(who, f"padt:{team}")
        card = next(r for r in out if r.view)
        return int(next(b for b in buttons(card) if b.startswith("pap:")).split(":")[1])


class Access(Base):
    def test_add_invite_enter_revoke(self):
        r = self.app.press(ADMIN, "pa")[0]
        self.assertEqual(r.view.title, "👥 Сотрудники и доступ")
        emp = self.add(ADMIN, "sales", "Иванов Пётр", "rep", "north")
        e = self.db.employee(emp)
        self.assertEqual((e["role"], e["team"], e["position"], e["hired"]), ("rep", "north", "rep", TODAY.isoformat()))
        out = self.app.press(ADMIN, f"pai:{emp}")
        self.assertIn("https://firm.example.ru/?start=", out[0].text)
        code = code_of(out)
        self.assertTrue(P.pending(self.db, emp))
        r = self.app.press(ADMIN, f"pae:{emp}")[0]
        self.assertIn("🔗 выдана", texts(r))
        # человек открыл ссылку на своём телефоне
        out = self.app.start(500, arg=code)
        self.assertIn("Здравствуйте, Иванов Пётр", out[0].text)
        ctx = self.app.ctx(500)
        self.assertEqual((ctx.role, ctx.me["fio"]), ("rep", "Иванов Пётр"))
        self.assertFalse(P.pending(self.db, emp))                                # ссылка одноразовая
        out = self.app.start(601, arg=code)
        self.assertIn("недействительна", out[0].text)
        self.assertEqual(self.app.ctx(601).role, "guest")
        # новая ссылка — новый телефон, старый теряет вход
        code2 = code_of(self.app.press(ADMIN, f"pai:{emp}"))
        self.app.start(502, arg=code2)
        self.assertEqual(self.app.ctx(502).me["fio"], "Иванов Пётр")
        self.assertEqual(self.app.ctx(500).role, "guest")
        # закрыть доступ
        self.assertIn(f"pax:{emp}:yes", buttons(self.app.press(ADMIN, f"pax:{emp}")[0]))
        self.app.press(ADMIN, f"pax:{emp}:yes")
        self.assertEqual(self.app.ctx(502).role, "guest")
        self.assertIsNone(self.db.employee(emp)["tg_id"])

    def test_expired_invite(self):
        emp = self.add(ADMIN, "office", "Офисов Олег", "manager")
        code = code_of(self.app.press(ADMIN, f"pai:{emp}"))
        inv = self.db.get(P.KEY)
        inv[code]["ts"] = (TODAY - timedelta(days=P.INVITE_DAYS + 1)).isoformat()
        self.db.put(P.KEY, inv)
        out = self.app.start(503, arg=code)
        self.assertIn("устарела", out[0].text)
        self.assertEqual(self.app.ctx(503).role, "guest")

    def test_fire_and_rehire(self):
        emp = self.add(ADMIN, "sales", "Петров Иван", "rep", "south")
        self.app.start(504, arg=code_of(self.app.press(ADMIN, f"pai:{emp}")))
        out = self.app.press(ADMIN, f"paf:{emp}:yes")
        self.assertIn("уволен", out[0].text)
        e = self.db.employee(emp)
        self.assertEqual((e["active"], e["fired"], e["tg_id"]), (0, TODAY.isoformat(), None))
        self.assertEqual(self.app.ctx(504).role, "guest")
        self.assertNotIn("Петров", texts(self.app.press(ADMIN, "pa:sales")[0]))
        self.assertIn("Петров", texts(self.app.press(ADMIN, "pa:sales:all")[0]))
        self.assertIn("⛔ уволен", texts(self.app.press(ADMIN, "pa:sales:all")[0]))
        self.app.press(ADMIN, f"paf:{emp}:back")
        self.assertEqual(self.db.employee(emp)["active"], 1)

    def test_position_team_role(self):
        emp = self.add(ADMIN, "sales", "Сидоров Али", "rep", "north")
        self.app.press(ADMIN, f"pap:{emp}:sv")
        self.assertEqual(self.db.employee(emp)["role"], "sv")
        self.app.press(ADMIN, f"pat:{emp}:south")
        self.assertEqual(self.db.employee(emp)["team"], "south")
        self.app.press(ADMIN, f"par:{emp}:newbie")
        self.assertEqual(self.db.employee(emp)["role"], "newbie")
        self.app.press(ADMIN, f"pap:{emp}:storekeeper")                          # чужого отдела — нельзя
        self.assertEqual(self.db.employee(emp)["position"], "sv")

    def test_client_admin_runs_people_but_rep_cannot(self):
        acc = self.add(ADMIN, "office", "Бухгалтерова Аза", "accountant")
        self.app.press(ADMIN, f"par:{acc}:admin")
        self.app.start(700, arg=code_of(self.app.press(ADMIN, f"pai:{acc}")))
        ctx = self.app.ctx(700)
        self.assertTrue(ctx.admin)
        self.assertEqual(ctx.role, "admin")
        self.assertIn("pa", [t for row in __import__("core.ui.screens", fromlist=["x"]).bottom_rows(ctx) for _, t in row])
        emp = self.add(700, "sales", "Новый Торговый", "rep", "key")             # админ клиента заводит людей
        self.assertIn("?start=", self.app.press(700, f"pai:{emp}")[0].text)
        self.assertIn(700, self.app.admin_ids(self.db))                           # и получает «🐞 Не работает»
        self.app.start(800, arg=code_of(self.app.press(700, f"pai:{emp}")))
        self.assertIn("администратор", self.app.press(800, "pa")[0].text)       # торговый — нет
        self.assertIn("администратор", self.app.press(800, f"pai:{emp}")[0].text)

    def test_head_only_own_department(self):
        wh = self.add(ADMIN, "warehouse", "Складов Семён", "wh_head")
        self.app.start(900, arg=code_of(self.app.press(ADMIN, f"pai:{wh}")))
        self.assertEqual(self.app.ctx(900).role, "head")
        rep = self.add(ADMIN, "sales", "Чужой Отдел", "rep", "north")
        r = self.app.press(900, "pa")[0]
        self.assertEqual(r.view.title, "👥 Склад")                                # сразу свой отдел
        self.assertNotIn(f"pai:{rep}", str(self.app.press(900, f"pae:{rep}")))  # чужого не видит
        mine = self.add(900, "warehouse", "Грузчиков Руслан", "storekeeper")
        self.assertIn("?start=", self.app.press(900, f"pai:{mine}")[0].text)
        self.assertNotIn(f"par:{mine}", buttons(self.app.press(900, f"pae:{mine}")[0]))   # роли не раздаёт

    def test_unknown_1c_names(self):
        emp = self.add(ADMIN, "sales", "Иванов Пётр", "rep", "north")
        ds = Dataset(kind="sales", start=date(2026, 10, 1), end=date(2026, 10, 31), source="тест",
                     rows=[Sale(day=date(2026, 10, 5), emp_key="иванов петр", client="c1", group="g1", amount=1000),
                           Sale(day=date(2026, 10, 6), emp_key="новиков саид", client="c2", group="g1", amount=2000)])
        self.db.load(ds)
        self.assertEqual(self.db.unknown_keys(), ["иванов петр", "новиков саид"])
        self.assertIn("pak", buttons(self.app.press(ADMIN, "pa:sales")[0]))
        self.app.press(ADMIN, f"pak:иванов петр:{emp}")
        self.assertEqual(self.db.emp_of_key("иванов петр", TODAY), emp)
        out = self.app.press(ADMIN, "pak:новиков саид:new")
        self.assertIn("Заведён новый торговый", out[0].text)
        self.assertEqual(self.db.unknown_keys(), [])
        new = next(e for e in self.db.employees() if e["fio"] == "Новиков Саид")
        self.assertEqual((new["role"], new["dept"]), ("rep", "sales"))

    def test_guest_home_and_demo_has_no_links(self):
        out = self.app.start(999)
        self.assertIn("ссылке-приглашению", out[0].text)
        demo = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        demo.press(5, "sw:role:owner")
        r = demo.press(5, "pa")[0]
        self.assertIn("В демо приглашения не выдаются", texts(r))
        emp = demo.ctx(5).db.employees()[0]
        self.assertNotIn(f"pai:{emp['id']}", buttons(demo.press(5, f"pae:{emp['id']}")[0]))


if __name__ == "__main__":
    unittest.main()
