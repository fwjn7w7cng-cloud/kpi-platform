"""Проверка клиента перед запуском: анкета читается без ошибок, переводчик понимает его файлы.

    python3 tools/check_client.py <код>                 # только анкета
    python3 tools/check_client.py <код> отчёт1.xlsx …   # анкета + что бот понял из файлов (в базу не пишет)
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ConfigError, load_client        # noqa: E402
from core.hooks import load_rules                       # noqa: E402
from core.ingest.pipeline import parser_of              # noqa: E402


def main(client, files):
    try:
        cfg = load_client(client, hooks=load_rules(client))
    except ConfigError as e:
        print("❌", e)
        return 1
    print(f"✅ Анкета «{cfg.name}»: редакция {cfg.edition}, модулей {len(cfg.modules)}, отделов {len(cfg.departments)}, "
          f"команд {len(cfg.teams)}, должностей {len(cfg.positions)}")
    parse = parser_of(client)
    for f in files:
        sets = parse(Path(f).name, Path(f).read_bytes()) if parse else []
        if not sets:
            print(f"⏭ {f}: не узнал отчёт")
        for ds in sets:
            problems = ds.check()
            keys = Counter(getattr(r, "emp_key", "") for r in ds.rows)
            print(f"{'⚠️' if problems else '📄'} {f}: {ds.kind} {ds.start}–{ds.end}, строк {len(ds.rows)}, людей {len(keys)}")
            for p in problems[:5]:
                print("   ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
