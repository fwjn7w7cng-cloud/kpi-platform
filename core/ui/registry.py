"""Реестр экранов, ввода текста и пунктов главного меню. Ядро и модули регистрируют своё одинаково:

    @screen("debts", module="debts")
    def debts_screen(ctx, *args): ...  → Reply

    @menu("debts", "💵 Долги", roles={"rep", "sv", "head", "owner"}, module="debts", order=30)

    @text_input("edit_reason")
    def on_reason(ctx, text, data): ...  → Reply

Пункт меню закрытого модуля не прячется, а показывается с замком — клиент видит, что можно докупить.
"""
from dataclasses import dataclass

SCREENS = {}       # id → (функция, модуль)
INPUTS = {}        # вид ввода → функция
MENU = []          # пункты главной


@dataclass
class MenuItem:
    to: str
    label: str
    roles: set
    module: str = ""
    order: int = 50
    depts: set = None          # только для отделов этого вида (sales, warehouse…); None — всем
    when: object = None        # доп. условие: функция (ctx) → bool


def screen(sid, module=""):
    def deco(fn):
        SCREENS[sid] = (fn, module)
        return fn
    return deco


def text_input(kind):
    def deco(fn):
        INPUTS[kind] = fn
        return fn
    return deco


def menu(to, label, roles, module="", order=50, depts=None, when=None):
    MENU.append(MenuItem(to, label, set(roles), module, order, set(depts) if depts else None, when))
    MENU.sort(key=lambda m: m.order)
