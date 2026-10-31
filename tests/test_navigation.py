"""Робот «стрелка и кнопки» (tools/nav_check.py): все экраны открываются, «‹ Назад» ведёт ровно на прошлый экран,
выбор из нескольких вариантов — окном с «✅ ОК / ✖️ Отмена», «Отмена» возвращает туда же, что и стрелка (Адам 31.10)."""
import unittest

from tools import nav_check


class Navigation(unittest.TestCase):
    def test_every_role(self):
        for role in nav_check.ROLES:
            with self.subTest(role=role):
                n, screens, problems = nav_check.check(role)
                self.assertGreater(screens, 10)
                self.assertEqual(problems, [], f"{role}: {problems[:5]}")


if __name__ == "__main__":
    unittest.main()
