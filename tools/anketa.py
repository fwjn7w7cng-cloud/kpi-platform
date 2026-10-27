"""Анкета клиента: таблица ⇄ config.yaml.

    python3 tools/anketa.py check  анкета.xlsx                 # проверить, ничего не записывать
    python3 tools/anketa.py check  <ссылка на Google-таблицу>  # то же (нужен ключ GOOGLE_KEY)
    python3 tools/anketa.py apply  анкета.xlsx                 # проверить и записать clients/<код>/config.yaml
    python3 tools/anketa.py blank  шаблон.xlsx [demo]          # пустой шаблон (или выгрузка анкеты клиента)

Ошибки печатаются с листом и строкой; при check/apply рядом кладётся «…_проверка.xlsx» с пометками.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                  # noqa: E402

from core import anketa as A                 # noqa: E402
from core.config import load_client          # noqa: E402
from core.hooks import load_rules            # noqa: E402


def read(src):
    if src.startswith("http") or ("/" not in src and not src.endswith(".xlsx")):
        return A.read_gsheet(src, os.environ.get("GOOGLE_KEY", str(ROOT / "google_key.json")))
    return A.read_xlsx(src)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, src = argv[0], argv[1]
    if cmd == "blank":
        tables = A.from_raw(load_client(argv[2]).raw) if len(argv) > 2 else {k: [v] for k, v in A.HEADS.items()}
        if len(argv) <= 2:
            tables["Компания"] += [[label, "", hint] for label, _, _, hint in A.COMPANY]
        Path(src).write_bytes(A.write_xlsx(tables))
        print(f"✅ Шаблон анкеты: {src}")
        return 0
    tables = read(src)
    raw, _, _ = A.to_raw(tables)
    cfg, problems = A.check(tables, load_rules(raw["id"]) if raw.get("id") else None)
    if problems:
        print("❌ В анкете есть ошибки:\n" + A.problems_text(problems))
        if src.endswith(".xlsx"):
            out = src[:-5] + "_проверка.xlsx"
            Path(out).write_bytes(A.write_xlsx(tables, problems))
            print(f"Пометки рядом со строками: {out}")
        return 1
    print(f"✅ Анкета «{cfg.name}» в порядке: редакция {cfg.edition}, отделов {len(cfg.departments)}, "
          f"команд {len(cfg.teams)}, должностей {len(cfg.positions)}")
    if cmd == "apply":
        folder = ROOT / "clients" / cfg.id
        if not folder.exists():
            import shutil
            shutil.copytree(ROOT / "clients" / "_template", folder)
        path = folder / "config.yaml"
        head = f"# Анкета клиента «{cfg.name}». Сделано из таблицы-анкеты: правьте таблицу, а не этот файл.\n"
        path.write_text(head + yaml.safe_dump(cfg.raw, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        print(f"Записано: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
