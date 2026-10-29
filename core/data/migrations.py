"""Миграции базы клиента. Список только дописывается в конец, старые шаги не меняются никогда.

Каждый шаг выполняется один раз; номер записывается в schema_migrations. Обновили ядро —
при старте база клиента сама догоняется до последнего шага, данные не трогаются.
"""

STEPS = [
    (1, "базовые таблицы", """
CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fio TEXT NOT NULL,
  dept TEXT NOT NULL,
  team TEXT NOT NULL DEFAULT '',
  position TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'staff',
  tg_id INTEGER UNIQUE,
  salary REAL,                       -- NULL — брать по умолчанию из config.yaml
  rate REAL,                         -- % от продаж; NULL — из линии команды
  hired TEXT, fired TEXT,            -- ISO-даты
  active INTEGER NOT NULL DEFAULT 1,
  extra TEXT NOT NULL DEFAULT '{}'   -- json: личные настройки
);

-- ключ из источника → сотрудник; с какой даты действует (смена торгового на территории)
CREATE TABLE emp_keys (
  key TEXT NOT NULL,
  emp_id INTEGER NOT NULL REFERENCES employees(id),
  since TEXT NOT NULL DEFAULT '0000-01-01',
  PRIMARY KEY (key, since)
);

CREATE TABLE batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '',
  rows INTEGER NOT NULL, ts TEXT NOT NULL
);
CREATE INDEX batches_kind ON batches(kind, start, end);
CREATE INDEX batches_fp ON batches(fingerprint);

CREATE TABLE sales (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, client TEXT NOT NULL,
  grp TEXT NOT NULL DEFAULT '', amount REAL NOT NULL, qty REAL NOT NULL DEFAULT 0, discount REAL NOT NULL DEFAULT 0);
CREATE INDEX sales_day ON sales(day);
CREATE INDEX sales_emp ON sales(emp_key, day);

CREATE TABLE debts (batch INTEGER NOT NULL, "on" TEXT NOT NULL, emp_key TEXT NOT NULL, client TEXT NOT NULL,
  total REAL NOT NULL, d0_30 REAL NOT NULL DEFAULT 0, d31_60 REAL NOT NULL DEFAULT 0,
  d61_90 REAL NOT NULL DEFAULT 0, d91 REAL NOT NULL DEFAULT 0);
CREATE INDEX debts_on ON debts("on");

CREATE TABLE payments (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, client TEXT NOT NULL,
  amount REAL NOT NULL);
CREATE INDEX payments_day ON payments(day);

CREATE TABLE visits (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, client TEXT NOT NULL);
CREATE INDEX visits_day ON visits(day, emp_key);

CREATE TABLE clients (batch INTEGER NOT NULL, code TEXT PRIMARY KEY, name TEXT NOT NULL, town TEXT NOT NULL DEFAULT '');
CREATE TABLE groups (batch INTEGER NOT NULL, code TEXT PRIMARY KEY, name TEXT NOT NULL, line TEXT NOT NULL DEFAULT '');

-- планы: metric = sales | grp:<код> | akb | sku | …
CREATE TABLE plans (month TEXT NOT NULL, emp_id INTEGER NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL,
  src TEXT NOT NULL DEFAULT '', who TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL,
  PRIMARY KEY (month, emp_id, metric));

-- табель: отклонения от «работал как обычно» (пропуск, полдня, опоздание, штраф, комментарий, работа в выходной)
CREATE TABLE timesheet (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, emp_id INTEGER NOT NULL,
  kind TEXT NOT NULL, minutes INTEGER NOT NULL DEFAULT 0, amount REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', who TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);
CREATE INDEX timesheet_day ON timesheet(emp_id, day);

-- цикл месяца: open → review → approved; snapshot — итог расчёта на момент утверждения (json)
CREATE TABLE months (month TEXT PRIMARY KEY, status TEXT NOT NULL, ts TEXT NOT NULL, snapshot TEXT);

CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, who TEXT NOT NULL,
  action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '');
"""),
    (2, "модули: правки, задачи, отпуска, документы, сессии", """
ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT '';

-- правки зарплаты: премия (+) или удержание (−) с обязательной причиной; wait → ok / no
CREATE TABLE edits (id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, emp_id INTEGER NOT NULL,
  amount REAL NOT NULL, reason TEXT NOT NULL, author TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'wait',
  decided_by TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL, decided_ts TEXT);
CREATE INDEX edits_month ON edits(month, emp_id);

-- задачи от руководителя: open → done / fail
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL, emp_id INTEGER NOT NULL,
  text TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL, done_ts TEXT);
CREATE INDEX tasks_emp ON tasks(emp_id, status);

-- отпуска и отгулы: wait → ok / no
CREATE TABLE leave (id INTEGER PRIMARY KEY AUTOINCREMENT, emp_id INTEGER NOT NULL, kind TEXT NOT NULL,
  start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'wait', decided_by TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL);

-- сроки документов сотрудников (медкнижка, права, договор…)
CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, emp_id INTEGER NOT NULL, title TEXT NOT NULL,
  until TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');

-- состояние пользователя мессенджера: ждём ли ввод текста, демо-переключатели
CREATE TABLE sessions (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL DEFAULT '{}');
"""),
    (3, "модули: отзывы, маршрут, тесты, склад, логистика, возвраты", """
-- 💬 предложения и «не работает»: new → answered / done
CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, emp_id INTEGER,
  kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
  answer TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);

-- 🚗 отметки торгового по точкам маршрута: sold / visited / missed (+ причина), сумма заявки
CREATE TABLE visit_marks (day TEXT NOT NULL, emp_id INTEGER NOT NULL, client TEXT NOT NULL, status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', amount REAL NOT NULL DEFAULT 0, ts TEXT NOT NULL, PRIMARY KEY (day, emp_id, client));
CREATE TABLE visit_reports (day TEXT NOT NULL, emp_id INTEGER NOT NULL, ts TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, emp_id));

-- 🎓 тесты и обучение: вопросы, добавленные в боте; результаты попыток
CREATE TABLE quiz_questions (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
  options TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, ts TEXT NOT NULL);
CREATE TABLE quiz_results (id INTEGER PRIMARY KEY AUTOINCREMENT, emp_id INTEGER NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL, total INTEGER NOT NULL, wrong TEXT NOT NULL DEFAULT '[]', ts TEXT NOT NULL);
CREATE INDEX quiz_results_emp ON quiz_results(emp_id, kind);

-- 📦 склад: остатки и продажи по товарам (пачки), заказы поставщикам
CREATE TABLE stock (batch INTEGER NOT NULL, "on" TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  supplier TEXT NOT NULL DEFAULT '', qty REAL NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'шт');
CREATE INDEX stock_on ON stock("on");
CREATE TABLE item_sales (batch INTEGER NOT NULL, day TEXT NOT NULL, code TEXT NOT NULL, qty REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0);
CREATE INDEX item_sales_day ON item_sales(day, code);
CREATE TABLE stock_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft', author TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL, sent_ts TEXT, came_ts TEXT);

-- 🚚 рейсы водителей и точки рейсов (пачки)
CREATE TABLE trips (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, trip TEXT NOT NULL,
  km REAL NOT NULL DEFAULT 0, kg REAL NOT NULL DEFAULT 0, m3 REAL NOT NULL DEFAULT 0);
CREATE INDEX trips_day ON trips(day, emp_key);
CREATE TABLE trip_stops (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, trip TEXT NOT NULL,
  client TEXT NOT NULL, doc TEXT NOT NULL DEFAULT '');
CREATE INDEX trip_stops_day ON trip_stops(day, client);

-- ↩️ возвраты с причиной (пачки)
CREATE TABLE returns (batch INTEGER NOT NULL, day TEXT NOT NULL, emp_key TEXT NOT NULL, client TEXT NOT NULL,
  doc TEXT NOT NULL DEFAULT '', amount REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', fault TEXT NOT NULL DEFAULT '',
  who TEXT NOT NULL DEFAULT '');
CREATE INDEX returns_day ON returns(day);
"""),
    (4, "дебиторка: готовая просрочка сверх порога клиента", """
ALTER TABLE debts ADD COLUMN over REAL;
"""),
]
