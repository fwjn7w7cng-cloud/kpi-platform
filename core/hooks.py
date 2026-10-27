"""Крючки: места в ядре, куда клиент вставляет свои правила, не трогая ядро.

В папке клиента ``clients/<id>/rules.py`` есть функция ``setup(hooks)``:

    def setup(hooks):
        @hooks.on("pay_rows")
        def bonus_for_volume(ctx, emp, result):
            if emp["position"] == "driver":
                return [("За кубометры", ctx.kpi.extra.get("m3", 0) * 30)]

        hooks.component("my_bonus", my_bonus_fn, check=my_check)   # свой кубик зарплаты

Крючки ядра (имя → когда вызывается → что вернуть):
  pay_rows        после расчёта зарплаты сотрудника → список (название, сумма)
  kpi_extra       после расчёта KPI сотрудника → {код: Metric}
  morning_lines   утренняя сводка → список строк
  ingest_after    после приёма пачки данных → ничего
"""
import importlib
from collections import defaultdict

KNOWN = {"pay_rows", "kpi_extra", "morning_lines", "ingest_after"}


class Hooks:
    def __init__(self):
        self._fns = defaultdict(list)
        self.components = {}      # свои кубики зарплаты клиента: тип → (функция, проверка)

    def on(self, name):
        if name not in KNOWN:
            raise ValueError(f"нет такого крючка: {name} (есть: {', '.join(sorted(KNOWN))})")

        def deco(fn):
            self._fns[name].append(fn)
            return fn
        return deco

    def component(self, kind, fn, check=None):
        self.components[kind] = (fn, check)

    def run(self, name, *args):
        """Вызвать все функции крючка, списки склеить, словари объединить, None пропустить."""
        out = None
        for fn in self._fns[name]:
            r = fn(*args)
            if r is None:
                continue
            if isinstance(r, dict):
                out = {**(out or {}), **r}
            elif isinstance(r, (list, tuple)):
                out = (out or []) + list(r)
        return out

    def has(self, name):
        return bool(self._fns[name])


def load_rules(client_id):
    """Крючки клиента из clients/<id>/rules.py (если файла нет — пустые)."""
    hooks = Hooks()
    try:
        mod = importlib.import_module(f"clients.{client_id}.rules")
    except ModuleNotFoundError as e:
        if e.name not in (f"clients.{client_id}.rules", f"clients.{client_id}"):
            raise
        return hooks
    if hasattr(mod, "setup"):
        mod.setup(hooks)
    return hooks
