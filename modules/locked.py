"""Модули, которые только продаются отдельно и пока делаются под заказ: Mini App, прямой обмен с 1С,
филиалы, брендирование. В меню они видны с замком (чтобы клиент знал о них); у купившего — экран «как подключить».
"""
from core.access import editions as E
from core.ui.kit import Reply
from core.ui.registry import menu, screen
from core.ui.views import View

EXTRA = {"miniapp": "📱 Панель с графиками", "direct_1c": "🔌 Прямой обмен с 1С", "branches": "🏢 Филиалы",
         "branding": "🎨 Свой стиль"}

for i, (code, label) in enumerate(EXTRA.items()):
    menu(code, label, {"owner", "admin"}, module=code, order=90 + i)


def _make(code):
    @screen(code, module=code)
    def placeholder(ctx):
        v = View(EXTRA[code], E.MODULES[code][0])
        v.lines(["Модуль подключён. Настройка делается под вашу компанию — свяжитесь с администратором бота."])
        return Reply(view=v)
    return placeholder


for _c in EXTRA:
    _make(_c)
