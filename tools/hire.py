"""👥 Завести людей по ролям у клиента — то же, что форма в пульте (core/hire.py), но по названиям из JSON.

    sudo -u alapa python3 tools/hire.py <код клиента> <файл.json>          # завести
    sudo -u alapa python3 tools/hire.py <код клиента> <файл.json> --dry    # только проверить названия

JSON: {"shops": {"Барт": {"Продавец": 2, "Администратор магазина": 1, "Водитель": 1, "Кладовщик": 0}},
       "warehouses": {"Склад 1": 2}, "wh_head": 1, "depts": {"Общий опт": {"Менеджер опта": 2}},
       "leaders": [["head", ["Барт", "Барт Премиум"]], ["owner", ["Барт"]]],
       "admin": ["Общий опт", "Менеджер опта"]}       # первый заведённый на этой должности — администратор клиента
Хоть одно название не найдено — ничего не заводит. Повтор заводит только недостающих.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import hire as H  # noqa: E402
from core.app import App  # noqa: E402
from core.control import ADMIN  # noqa: E402


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    code, path = argv[0], argv[1]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    app = App(code, today=date.today(), admins=[ADMIN])
    ctx = app.ctx(ADMIN)
    form, errs = H.form_from_names(ctx, data)
    if errs:
        print("❌ Ничего не заведено:")
        for e in errs:
            print("  -", e)
        return 1
    want, leads, errs = H.parse(ctx, form)
    total = sum(x[-1] for x in want) + len(leads)
    print(f"Должно быть: {total} чел. (руководителей и владельцев {len(leads)})")
    if "--dry" in argv:
        print("Проверка: все названия найдены. Для записи — без --dry.")
        return 0
    made, errs = H.apply(ctx, form)
    if errs:
        print("❌", "; ".join(errs))
        return 1
    ctx = app.ctx(ADMIN)
    if data.get("admin"):                                         # администратор клиента: имена, ссылки для входа
        dname, pname = data["admin"]
        n = lambda t: " ".join(str(t).lower().replace("ё", "е").split())
        did = next((d.id for d in ctx.cfg.departments if n(d.name) == n(dname)), None)
        pid = next((p["id"] for p in ctx.cfg.positions if n(p["name"]) == n(pname)), None)
        e = next((x for x in ctx.db.employees() if x["dept"] == did and x["position"] == pid
                  and (x.get("extra") or {}).get("n") == 1 and (x.get("extra") or {}).get("auto")), None)
        if e:
            ctx.db.c.execute("UPDATE employees SET role = 'admin' WHERE id = ?", (e["id"],))
            print(f"👤 Администратор клиента: {e['fio']}")
        else:
            print("⚠️ Не нашёл, кого сделать администратором")
        ctx = app.ctx(ADMIN)
    paid = H.sync_billing(ctx)
    print(f"✅ Заведено {made}. В приложении {len(ctx.db.employees())}, за деньги {paid}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
