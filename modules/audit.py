"""Модуль «Журнал изменений»: кто, что и когда менял — табель, правки, статус месяца, решения."""
from core.ui.kit import Reply
from core.ui.registry import menu, screen
from core.ui.views import Row, View

M = "audit"
ACTIONS = {"month_status": "Статус месяца", "month_reopen": "Месяц открыт заново", "edit_add": "Новая правка",
           "edit_ok": "Правка принята", "edit_no": "Правка отклонена", "timesheet": "Табель"}

menu("log", "📜 Журнал", {"owner", "admin"}, module=M, order=85)


@screen("log", module=M)
def journal(ctx):
    rs = ctx.db.q("SELECT * FROM audit ORDER BY id DESC LIMIT 30")
    v = View("📜 Журнал изменений", "последние 30 записей")
    v.table(["Когда", "Кто", "Что"], [Row([r["ts"][8:10] + "." + r["ts"][5:7] + " " + r["ts"][11:16], r["who"][:28],
                                           ACTIONS.get(r["action"], r["action"])], sub=r["details"][:120]) for r in rs])
    if not rs:
        v.note("Записей пока нет.")
    return Reply(view=v)
