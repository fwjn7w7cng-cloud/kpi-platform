"""Мессенджер MAX — второй адаптер, по образцу telegram.py.

Ядро уже отдаёт ответы без привязки к мессенджеру (core/ui/kit.py: Reply, Button), поэтому для MAX нужно
только: получить нажатие/текст → вызвать app.start / app.press / app.text → показать Reply картинкой
с кнопками. Делается, когда первому клиенту понадобится MAX (Bot API MAX: боты, inline-кнопки, файлы).
"""


def build(app, token):
    raise NotImplementedError("Адаптер MAX ещё не подключён — см. описание в core/transport/max.py")
