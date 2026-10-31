"""Анкета клиента в боте: снимок из конструктора, ответы кнопками и текстом, ⚠️ нестандарт, кто видит,
вариации зарплаты «💵 Как платите», Excel-анкета для подключения проходит проверку анкеты."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import anketa as A
from core import onboard as O
from core.app import App

TODAY = date(2026, 10, 29)
ADMIN, PARTNER, CLIENT, OTHER = 1, 100, 200, 300


def code_of(text):
    return text.split("?start=")[1].split()[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp, demo_gate=True)
        self.app.web_url = "https://demo.example"
        self.app.press(ADMIN, "pta")
        self.app.start(PARTNER, arg=code_of(self.app.text(ADMIN, "Ислам, Грозный")[0].text))
        self.app.press(PARTNER, "pti")
        r = self.app.text(PARTNER, "Ахмед, Стройбаза, 8 928 000 00 00")[0]
        out = self.app.press(PARTNER, next(b.to for b in r.all_buttons() if b.to.startswith("ptk:7:")))
        self.app.start(CLIENT, arg=code_of(out[0].text))
        self.app.press(ADMIN, "gsi:7")
        self.app.start(OTHER, arg=code_of(self.app.press(ADMIN, "gsi:7")[0].text))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def office(self):
        return self.app.office()


class ClientCard(Base):
    def test_client_fills_card_and_snapshot(self):
        r = self.app.start(CLIENT)
        self.assertIn("cd", [b.to for x in r for b in x.all_buttons()])          # кнопка анкеты на главной гостя
        self.app.press(CLIENT, "sw:ed:business")
        self.app.press(CLIENT, "sw:pn")
        self.app.text(CLIENT, "6 0 0 1")
        r = self.app.press(CLIENT, "cd")[0]
        self.assertEqual(r.view.title, "📋 Анкета для подключения")
        c = O.card(self.office(), CLIENT)
        self.assertEqual(c["by"], str(PARTNER))
        self.assertEqual(c["f"]["decider"], "Ахмед, Стройбаза, 8 928 000 00 00")   # заметка партнёра из приглашения
        self.assertEqual((c["set"]["title"], c["set"]["counts"]["sales"], c["set"]["monthly"]), ("Бизнес", 6, 14200))
        self.app.press(CLIENT, "cdf:200:company")
        self.app.text(CLIENT, "Стройбаза")
        self.app.press(CLIENT, "cdv:200:onec:4")                                   # «Не 1С»
        self.app.press(CLIENT, "cdv:200:start:0")
        c = O.card(self.office(), CLIENT)
        self.assertEqual((c["f"]["company"], c["f"]["onec"], c["f"]["start"]), ("Стройбаза", "Не 1С", "Сразу"))
        self.assertTrue(any("не в 1С" in x for x in O.flags(c)))
        done, total = O.progress(c)
        self.assertEqual((done, total), (5, 8))                                    # компания, отрасль (из демо), кто решает, 1С, старт
        self.app.press(CLIENT, "cdf:200:company")
        self.app.text(CLIENT, "-")
        self.assertNotIn("company", O.card(self.office(), CLIENT)["f"])

    def test_pay_variations_go_to_card_and_flag(self):
        self.app.press(CLIENT, "sw:py:percent")                                    # убрали процент
        self.app.press(CLIENT, "sw:py:plan")                                       # ступени → фикс
        self.app.press(CLIENT, "sw:py:other")
        self.app.text(CLIENT, "премия 10 000 за нового клиента")
        pay = O.pay_of(self.app.ctx(CLIENT))
        self.assertEqual((pay["percent"], pay["plan"], pay["other"]), (False, "fix", "премия 10 000 за нового клиента"))
        self.app.press(CLIENT, "cd")
        c = O.card(self.office(), CLIENT)
        self.assertIn("фикс при 100 %", O.pay_text(c["set"]["pay"]))
        self.assertTrue(any("по-своему" in x for x in O.flags(c)))
        r = next(x for x in self.app.press(CLIENT, "sw:pay") if x.view)              # теперь это конструктор зарплаты
        self.assertTrue(any("премия 10 000" in str(b) for b in r.view.blocks))

    def test_request_creates_card_and_notifies_with_button(self):
        out = self.app.press(CLIENT, "sw:req")
        admin_msg = next(x for x in out if x.to == ADMIN)
        self.assertIn(f"cd:{CLIENT}", [b.to for b in admin_msg.all_buttons()])
        partner_msg = next(x for x in out if x.to == PARTNER)
        self.assertIn(f"cd:{CLIENT}", [b.to for b in partner_msg.all_buttons()])
        self.assertTrue(O.card(self.office(), CLIENT)["set"])


class Visibility(Base):
    def test_partner_sees_own_client_only(self):
        self.app.press(CLIENT, "cd")
        self.app.press(OTHER, "cd")
        r = self.app.press(PARTNER, f"cd:{CLIENT}")[0]
        self.assertEqual(r.view.title, "📋 Анкета для подключения")
        self.assertIn("Анкету видит", self.app.press(PARTNER, f"cd:{OTHER}")[0].text)
        self.app.press(PARTNER, f"cdn:{CLIENT}")
        self.app.text(PARTNER, "Обещал решить до пятницы")
        self.assertEqual(O.card(self.office(), CLIENT)["notes"][0]["text"], "Обещал решить до пятницы")
        self.assertIn(f"cd:{CLIENT}", [b.to for b in self.app.press(PARTNER, "ptn")[0].all_buttons()])
        self.assertNotIn("cdx:", "".join(b.to for b in r.all_buttons()))           # Excel — только админ

    def test_partner_manual_client_card(self):
        self.app.press(PARTNER, "sw:req")
        out = self.app.text(PARTNER, "Магомед, Автодеталь")
        cid = next(b.to for x in out for b in x.all_buttons() if b.to.startswith("cd:")).split(":")[1]
        self.assertTrue(cid.startswith(f"{PARTNER}c"))
        c = O.card(self.office(), cid)
        self.assertEqual((c["by"], c["f"]["decider"]), (str(PARTNER), "Магомед, Автодеталь"))
        self.app.press(PARTNER, f"cdf:{cid}:city")
        self.app.text(PARTNER, "Грозный")
        self.assertEqual(O.card(self.office(), cid)["f"]["city"], "Грозный")
        self.assertIn("Анкету видит", self.app.press(OTHER, f"cd:{cid}")[0].text)

    def test_admin_list_and_excel(self):
        self.app.press(CLIENT, "cd")
        self.app.press(CLIENT, "cdf:200:company")
        self.app.text(CLIENT, "Стройбаза")
        self.app.press(CLIENT, "cdv:200:branches:1")
        self.app.press(CLIENT, "cdv:200:weekend:2")                                 # пятница
        r = self.app.press(ADMIN, "cds")[0]
        self.assertIn("всего 1", r.view.subtitle)
        self.assertIn(f"cd:{CLIENT}", [b.to for b in r.all_buttons()])
        f = self.app.press(ADMIN, f"cdx:{CLIENT}")[0]
        self.assertTrue(f.files[0][0].endswith(".xlsx"))
        tables = A.read_xlsx(f.files[0][1])
        self.assertIn("Анкета клиента", tables)
        cfg, problems = A.check({k: v for k, v in tables.items() if k != "Анкета клиента"})
        self.assertEqual(problems, [])
        self.assertEqual((cfg.name, cfg.id, cfg.weekend), ("Стройбаза", "stroybaza", ["fri"]))
        self.assertEqual(cfg.packages["sales"], "control")                         # у клиента в демо — Максимум
        self.assertTrue(cfg.has_module("hr") and cfg.has_module("stock"))
        self.assertIn("плата", str(self.app.press(ADMIN, "adi")[0].view.blocks).lower() or "плата")
        self.assertIn("Анкеты", str([b.label for b in self.app.press(ADMIN, "adm")[0].all_buttons()]))


class CreateClient(Base):
    """«🚀 Создать клиента»: админ из анкеты делает папку клиента — config.yaml по набору и .env с портом, адресом и ключом."""
    def setUp(self):
        super().setUp()
        from core import config as C
        self.clients = self.tmp / "clients"
        self.clients.mkdir()
        shutil.copytree(C.CLIENTS / "_template", self.clients / "_template")
        shutil.copytree(C.CLIENTS / "demo", self.clients / "demo")
        self._real = C.CLIENTS
        C.CLIENTS = self.clients
        self.app.web_url = "https://demo.example.ru"                             # адреса клиентов — <код>.example.ru

    def tearDown(self):
        from core import config as C
        C.CLIENTS = self._real
        super().tearDown()

    def test_create_from_card(self):
        from core import config as C
        uid = str(CLIENT)
        self.app.press(CLIENT, "sw:ed:company")                                   # клиент собрал «Компанию»
        self.app.press(CLIENT, "cd")
        r = self.app.press(ADMIN, f"cdc:{uid}")[0]
        self.assertIn("название компании", r.text)                                # без названия — не создаём
        self.app.press(ADMIN, f"cdf:{uid}:company")
        self.app.text(ADMIN, "Стройбаза Юг")
        r = self.app.press(ADMIN, f"cdc:{uid}")[0]
        self.assertEqual(r.view.title, "🚀 Создать клиента")
        lines = [x for k, b in r.view.blocks if k == "lines" for x in b[1]]
        self.assertTrue(any("stroybaza_yug" in x and "stroybaza-yug" in x for x in lines))
        self.assertTrue(any("Склад" in x for x in lines))
        out = self.app.press(ADMIN, f"cdc:{uid}:go")
        self.assertEqual(out[-1].view.title, "🚀 Клиент создан")
        folder = self.clients / "stroybaza_yug"
        self.assertTrue((folder / "config.yaml").exists() and (folder / "parser.py").exists())
        env = (folder / ".env").read_text(encoding="utf-8")
        self.assertIn("WEB_PORT=8102", env)                                       # 8101 — демо
        self.assertIn("WEB_URL=https://stroybaza-yug.example.ru", env)            # домен — от адреса демо, дефис вместо _
        self.assertIn("WEB_ADMIN_KEY=", env)
        cfg = C.load_client("stroybaza_yug")
        self.assertEqual((cfg.name, cfg.edition), ("Стройбаза Юг", "company"))
        self.assertEqual({d.kind for d in cfg.departments}, {"sales", "warehouse", "purchasing", "logistics", "office"})
        info = O.card(self.office(), uid)["client"]
        self.assertEqual(info["port"], 8102)
        self.assertIn(info["key"], self.app.press(ADMIN, f"cdk:{uid}")[0].text)  # ссылка администратора — с ключом
        r = self.app.press(ADMIN, f"cd:{uid}")[0]
        self.assertIn(f"cdw:{uid}", [b.to for b in r.all_buttons()])            # вместо «Создать» — «Что дальше»
        self.assertNotIn(f"cdc:{uid}", [b.to for b in r.all_buttons()])
        r = self.app.press(ADMIN, f"cdc:{uid}:go")[0]                             # повторно не создаёт
        self.assertEqual(r.view.title, "📋 Анкета для подключения")
        app2 = App("stroybaza_yug", today=TODAY, admins=[ADMIN], data_dir=self.tmp)   # новый клиент стартует
        self.assertEqual(app2.cfg.name, "Стройбаза Юг")
        # второй клиент с тем же названием — код занят
        self.app.press(ADMIN, f"cdf:{OTHER}:company")
        self.app.text(ADMIN, "Стройбаза Юг")
        r = self.app.press(ADMIN, f"cdc:{OTHER}:go")[0]
        self.assertIn("уже занят", str([x for k, b in r.view.blocks if k == "lines" for x in b[1]]))
        self.assertFalse(O.card(self.office(), str(OTHER)).get("client"))

    def test_partner_and_client_cannot_create(self):
        uid = str(CLIENT)
        self.app.press(CLIENT, "cd")
        for who in (PARTNER, CLIENT):
            self.assertIn("администратор", self.app.press(who, f"cdc:{uid}:go")[0].text)
        self.assertFalse(list(self.clients.glob("stroy*")))


if __name__ == "__main__":
    unittest.main()
