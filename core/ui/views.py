"""Экраны в трёх видах из одной «модели экрана» (View):
  • таблица текстом (моноширинный шрифт, ровные колонки — влезает в экран телефона);
  • карточки текстом (для звонков: телефон нажимается и копируется);
  • картинка PNG (цвета, полоски, плитки — читается одним взглядом).

Экран собирается так:
    v = View("Команда Север", "Октябрь 2026 · нужно 58%")
    v.tiles([("План", "61%", "good"), ("Фонд", "155 т ₽", None)])
    v.table(["Менеджер", "План", "ЗП"], [Row(["Иванов", Cell("72%", "good", bar=(0.72, 0.58)), "64т"])])
    v.cards([Card("ИП Ромашка", "1 162 009 ₽", ["Код 4356"], phone="8 900 000-00-00")])
    v.note("…")
Цвета: good / warn / bad / muted / None.
"""
import io
import os
import re
from html import escape

TEXT_W = 29          # ширина таблицы в символах — столько влезает в строку Telegram на телефоне
IMG_W = 1080
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣⌀-⏿]")

COLORS = {"good": "#1F9D55", "warn": "#D99A06", "bad": "#D64545", "muted": "#6B7280", None: "#1B1F24",
          "blue": "#2F6FEB", "off": "#D64545"}      # off — выходной в календаре: красный без подложки
SOFT = {"good": "#E7F6EE", "warn": "#FDF3DC", "bad": "#FDECEC", "muted": "#F1F2F4", None: "#F1F2F4", "blue": "#E8EFFD"}
DOT = {"good": "🟢", "warn": "🟡", "bad": "🔴"}


class Cell:
    def __init__(self, text, color=None, bar=None, chip=False, bold=False, alt=None):
        self.text, self.color, self.bar, self.chip, self.bold = str(text), color, bar, chip, bold
        self.alt = alt          # короче — для текстовой таблицы («Хамзатов А.»)


class Head(list):
    """Шапка таблицы с заданной шириной первой колонки (доля от ширины): Head(["Строка", "План"…], first=0.3)."""
    def __init__(self, items, first=None):
        super().__init__(items)
        self.first = first


class Grid(list):
    """Шапка «ровной» таблицы: все колонки одинаковой ширины, значения по центру."""
    even = True


def _cell(x):
    return x if isinstance(x, Cell) else Cell("" if x is None else x)


class Row:
    def __init__(self, cells, sub="", mark=None):
        self.cells = [_cell(c) for c in cells]
        self.sub, self.mark = sub, mark        # sub — серая строка под первой колонкой; mark — good/warn/bad


class Card:
    def __init__(self, title, value="", lines=(), phone="", color=None, bar=None, note="", note_color="bad"):
        self.title, self.value, self.lines, self.phone = title, value, list(lines), phone
        self.color, self.bar, self.note, self.note_color = color, bar, note, note_color


class View:
    def __init__(self, title, subtitle="", dense=False):
        self.title, self.subtitle, self.blocks = title, subtitle, []
        self.dense = dense          # одна длинная картинка: группы не разносятся по страницам (дашборд)

    def tiles(self, items):
        """[(подпись, значение, цвет)] — 2–3 плитки с главными цифрами."""
        if items:
            self.blocks.append(("tiles", list(items)))
        return self

    def table(self, head, rows, title=""):
        if rows:
            self.blocks.append(("table", (title, head if isinstance(head, (Grid, Head)) else list(head), list(rows))))
        return self

    def grid(self, head, rows, title=""):
        """Таблица с равными колонками по центру — календарь (табель)."""
        return self.table(Grid(head), rows, title)

    def cards(self, items, title=""):
        if items:
            self.blocks.append(("cards", (title, list(items))))
        return self

    def matrix(self, names, rows, title=""):
        """Таблица «строки × люди» («Кто где отстаёт»): names — фамилии, rows — [(подпись, [Cell…])].
        Клетка с цветом good/warn/bad — плашкой, без цвета — просто число."""
        if rows and names:
            self.blocks.append(("matrix", (title, list(names), list(rows))))
        return self

    def medals(self, items, title=""):
        """[(файл картинки без расширения, заголовок, подпись, текст справа)] — медали бейджей (картинки из assets/medals/)."""
        if items:
            self.blocks.append(("medals", (title, list(items))))
        return self

    def lines(self, items, title=""):
        """Простые строки (подсказки, списки)."""
        items = [x for x in items if x]
        if items:
            self.blocks.append(("lines", (title, items)))
        return self

    def note(self, text, where="all"):
        """where: all — везде, img — только на картинке, txt — только в тексте."""
        if text:
            self.blocks.append(("note", (text, where)))
        return self

    def size(self):
        """Сколько строк/карточек в самом длинном списке — для листания картинок."""
        return max([len(b[1][2]) if k in ("table", "matrix") else len(b[1][1]) for k, b in
                    [(b[0], b) for b in self.blocks] if k in ("table", "cards", "medals", "matrix")] or [0])


# ======================= текст =======================

def _is_head(s):
    """«Где команда теряет:» — подзаголовок списка."""
    s = _plain(s)
    return s.endswith(":") and not s.startswith("•") and len(s) <= 110


def _fit(s, w):
    s = str(s)
    return s if len(s) <= w else s[:max(1, w - 1)] + "…"


def _plain(s):
    # перенос строки — в пробел: Pillow не меряет многострочный текст (Pillow падает на тексте с переводом строки)
    return " ".join(EMOJI.sub("", str(s)).split("\n")).strip()


MONO_TEXT = False     # текстовый вид — всегда карточки (моноширинные таблицы на телефоне читаются плохо)
TEXT_LIMIT = 3500     # символов в одном текстовом сообщении (у Telegram предел 4096)


def _text_groups(v):
    """Текстовый вид теми же группами, что и картинки: [(с заголовком?, заголовок, заголовок-продолжение, строки, pre-шапка)]."""
    gs, tiles_at = [], set()

    def cont(t):
        return t.rstrip(":") + " (продолжение)" + (":" if t.endswith(":") else "")
    for kind, b in v.blocks:
        if kind == "tiles":
            parts = [f"{escape(lab)}: <b>{escape(val)}</b>" + (" " + DOT[c] if c in DOT else "") for lab, val, c in b]
            plain = " · ".join(f"{lab}: {val}" for lab, val, _ in b)
            if gs:
                tiles_at.add(len(gs))
            gs.append((False, "", "", [" · ".join(parts)] if len(plain) <= 34 else parts, None))
        elif kind == "table":
            title, head, rows = b
            t = f"<b>{escape(title)}</b>" if title else ""
            ct = f"<b>{escape(cont(title))}</b>" if title else ""
            if MONO_TEXT and _table_fits(head, rows):
                hd, chunks = _table_parts(head, rows)
                gs.append((bool(title), t, ct, [escape(c) for c in chunks], escape(hd)))
            elif MONO_TEXT and _wrap_fits(head, rows):   # длинное название строкой выше, цифры — ровными столбцами под ним
                hd, chunks = _table_wrapped(head, rows)
                gs.append((bool(title), t, ct, [escape(c) for c in chunks], escape(hd)))
            else:                      # полные суммы не влезают в узкую таблицу — каждая строка блоком
                gs.append((bool(title), t, ct, _table_stacked(head, rows, chunks=True), None))
        elif kind == "cards":
            title, items = b
            out = []
            for i, c in enumerate(items):
                head = f"<b>{i + 1}. {escape(c.title)}</b>"
                if c.value:
                    head += f" — <b>{escape(c.value)}</b>" + (" " + DOT[c.color] if c.color in DOT else "")
                ln = [head] + [f"   {escape(x)}" for x in c.lines]
                if c.phone:
                    ln.append(f"   📞 {escape(c.phone)}")
                if c.note:
                    ln.append(f"   {escape(c.note)}")
                out.append("\n".join(ln))
            gs.append((bool(title), f"<b>{escape(title)}</b>" if title else "", f"<b>{escape(cont(title))}</b>" if title else "",
                       out, None))
        elif kind == "lines":
            title, items = b
            cur_t = f"<b>{escape(title)}</b>" if title else ""
            cur_c = f"<b>{escape(cont(title))}</b>" if title else ""
            cur = []
            for x in items:
                if _is_head(x):
                    if cur or cur_t:
                        gs.append((bool(cur_t), cur_t, cur_c, cur, None))
                    cur_t, cur_c, cur = f"<b>{escape(x)}</b>", f"<b>{escape(cont(x))}</b>", []
                else:
                    cur.append(escape(x))
            if cur or cur_t:
                gs.append((bool(cur_t), cur_t, cur_c, cur, None))
        elif kind == "matrix":
            title, names, rows = b
            out = []
            for lab, cells in rows:
                parts = [f"{escape(n)} <b>{escape(c.text)}</b>" + (" " + DOT[c.color] if c.color in DOT else "")
                         for n, c in zip(names, cells)]
                out.append(f"<b>{escape(lab)}</b>\n   " + "\n   ".join(parts))
            gs.append((bool(title), f"<b>{escape(title)}</b>" if title else "", f"<b>{escape(cont(title))}</b>" if title else "",
                       out, None))
        elif kind == "medals":
            title, items = b
            out = [f"<b>{escape(t)}</b>" + (f" — {escape(r)}" if r else "") + (f"\n   {escape(sub)}" if sub else "")
                   for _, t, sub, r in items]
            gs.append((bool(title), f"<b>{escape(title)}</b>" if title else "", f"<b>{escape(cont(title))}</b>" if title else "", out, None))
        elif kind == "note" and b[1] != "img":
            gs.append((False, "", "", [f"<i>{escape(b[0])}</i>"], None))
    # плитки посреди экрана стоят перед своим разделом — в тексте ставим их под заголовок раздела, а не в хвост прошлого
    out, i = [], 0
    while i < len(gs):
        if i in tiles_at and i + 1 < len(gs) and gs[i + 1][0] and gs[i + 1][4] is None:
            t, tt, ct, items, _ = gs[i + 1]
            out.append((t, tt, ct, gs[i][3] + items, None))
            i += 2
            continue
        out.append(gs[i])
        i += 1
    return out


def text_pages(v):
    """Текстовые сообщения: как на картинках — одна группа с заголовком на сообщение, длинная — с продолжением."""
    head = f"<b>{escape(v.title)}</b>" + (f"\n{escape(v.subtitle)}" if v.subtitle else "")
    pages_, cur, has_group = [], [head], False

    def flush():
        nonlocal cur, has_group
        pages_.append(cur)
        cur, has_group = [f"<b>{escape(v.title)}</b>"], False

    def block(title, items, pre):
        body = "\n".join(items)
        if pre is not None:
            body = "<pre>" + pre + "\n" + body + "</pre>"
        return "\n".join(x for x in (title, body) if x)
    for titled, title, ctitle, items, pre in _text_groups(v):
        if titled and has_group:
            flush()
        has_group = has_group or titled
        size = sum(len(x) for x in cur)
        part, t = [], title
        for it in items:
            if part and size + len(block(t, part + [it], pre)) > TEXT_LIMIT:
                cur.append(block(t, part, pre))
                flush()
                has_group, size, part, t = True, sum(len(x) for x in cur), [], ctitle
            part.append(it)
        cur.append(block(t, part, pre))
    if len(cur) > 1 or not pages_:
        pages_.append(cur)
    return ["\n\n".join(p_) for p_ in pages_]


def to_text(v):
    return "\n\n".join(text_pages(v))


def _widths(head, rows):
    n = len(head)
    data = [[_plain(c.alt or c.text) for c in r.cells] + [""] * (n - len(r.cells)) for r in rows]
    hd = [_plain(h) for h in head]
    w = [max([len(hd[i])] + [len(r[i]) for r in data]) for i in range(n)]
    rest = sum(w[1:]) + (n - 1) + (3 if any(r.mark for r in rows) else 0)
    return w, rest


def _table_fits(head, rows):
    """Моноширинная таблица — только если всё влезает целиком: без «…», без обрезанных шапок и подписей под строками.
    Иначе — карточки (текст должен читаться)."""
    w, rest = _widths(head, rows)
    return w[0] + rest <= TEXT_W and not any(r.sub for r in rows)


def _wrap_fits(head, rows):
    """Цифры (все столбцы, кроме названия) помещаются в ширину телефона — тогда таблица «название над цифрами»."""
    if len(head) < 2:
        return False
    _, rest = _widths(head, rows)
    return rest <= TEXT_W


def _wrap_mono(text, width, indent=""):
    out, cur = [], ""
    for word in re.sub(r"(?<=\d) (?=\d)", "\u00a0", text).split(" "):
        if not word:
            continue
        if cur and len(cur) + 1 + len(word) > width - len(indent):
            out.append(indent + cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
        while len(cur) > width - len(indent):          # одно очень длинное слово — режем по ширине
            out.append(indent + cur[:width - len(indent)])
            cur = cur[width - len(indent):]
    if cur:
        out.append(indent + cur)
    return out


def _table_wrapped(head, rows):
    """Моноширинная таблица для длинных названий: строка названия целиком (с переносом), под ней цифры ровными
    столбцами по правому краю, ниже — подпись. Шапка — названия столбцов над цифрами. Ничего не обрезается."""
    n = len(head)
    data = [[_plain(c.alt or c.text) for c in r.cells] + [""] * (n - len(r.cells)) for r in rows]
    hd = [_plain(h) for h in head]
    w = [max([len(hd[i])] + [len(r[i]) for r in data]) for i in range(n)]

    def nums(cells):
        return " ".join(cells[i].rjust(w[i]) for i in range(1, n))
    numw = len(nums(hd))
    pad = TEXT_W - numw
    first = hd[0] if len(hd[0]) < pad else ""
    head_lines = ([] if first else [hd[0]]) + [first.ljust(pad) + nums(hd), "─" * TEXT_W]
    chunks = []
    for r, cells in zip(rows, data):
        mark = DOT.get(r.mark, "")
        name, sub = cells[0], _plain(r.sub) if r.sub else ""
        head_sub = sub.split(" · ", 1)
        stem = " ".join(name.rstrip("…. ").split())[:15]
        name = " ".join(name.split())
        if sub and stem and head_sub[0].startswith(stem) and len(head_sub[0]) >= len(stem):
            name, sub = head_sub[0], (head_sub[1] if len(head_sub) > 1 else "")   # полное название уже в подписи — берём его
        if len(name) < pad:            # короткое название — в одну строку с цифрами, как обычная таблица
            ln = [name.ljust(pad) + nums(cells) + (" " + mark if mark else "")]
        else:
            ln = _wrap_mono(name, TEXT_W - (3 if mark else 0))
            if mark:
                ln[-1] += " " + mark
            ln.append(" " * pad + nums(cells))
        if sub:
            ln += _wrap_mono(sub, TEXT_W, "  ")
        chunks.append("\n".join(ln))
    return "\n".join(head_lines), chunks


def _table_stacked(head, rows, chunks=False):
    out, res = [], []
    for r in rows:
        if chunks and out:
            res.append("\n".join(out))
            out = []
        cells = r.cells + [Cell("")] * (len(head) - len(r.cells))
        mark = (" " + DOT[r.mark]) if r.mark in DOT else ""
        name = f"<b>{escape(_plain(cells[0].text))}</b>"
        vals = [(_plain(h), _plain(c.text)) for h, c in zip(head[1:], cells[1:]) if _plain(c.text)]
        if len(vals) == 1:             # одно значение — в ту же строку: «Оклад — 21 923 ₽»
            h, x = vals[0]
            x = x + " ₽" if h == "₽" and "₽" not in x else x
            lab = "" if h in ("", "%", "₽") else escape(h) + " "
            out.append(f"{name} — {lab}<b>{escape(x)}</b>{mark}")
            vals = []
        else:
            out.append(name + mark)
        parts = [f"{escape(h)}: <b>{escape(x)}</b>" if h not in ("", "%") else f"<b>{escape(x)}</b>" for h, x in vals]
        if parts:
            one = " · ".join(parts)
            if len(re.sub("<[^>]+>", "", one)) <= 40:
                out.append("   " + one)
            else:                      # длинно — каждое значение своей строкой
                out += ["   " + x for x in parts]
        if r.sub:
            out.append(f"   <i>{escape(_plain(r.sub))}</i>")
    if chunks:
        return res + (["\n".join(out)] if out else [])
    return out


def _table_parts(head, rows):
    """(шапка из 2 строк, [строка таблицы с подписями]) — чтобы делить длинную таблицу по сообщениям."""
    lines = _table_text(head, rows, keep_rows=True)
    return "\n".join(lines[0]), ["\n".join(x) for x in lines[1]]


def _table_text(head, rows, keep_rows=False):
    n = len(head)
    data = [[_plain(c.alt or c.text) for c in r.cells] + [""] * (n - len(r.cells)) for r in rows]
    hd = [_plain(h) for h in head]
    w = [max([len(hd[i])] + [len(r[i]) for r in data]) for i in range(n)]
    rest = sum(w[1:]) + (n - 1) + (3 if any(r.mark for r in rows) else 0)
    w[0] = max(6, min(w[0], TEXT_W - rest))
    lines = []

    def fmt(cells, mark=""):
        s = _fit(cells[0], w[0]).rjust(w[0]) if getattr(head, "even", False) else _fit(cells[0], w[0]).ljust(w[0])
        for i in range(1, n):
            s += " " + _fit(cells[i], w[i]).rjust(w[i])
        return s + (" " + mark if mark else "")
    lines.append(fmt(hd))
    lines.append("─" * min(TEXT_W, sum(w) + n - 1))
    head_lines, per_row = list(lines), []
    for r, cells in zip(rows, data):
        start = len(lines)
        lines.append(fmt(cells, DOT.get(r.mark, "")))
        if r.sub:                    # подпись — переносом по словам, без обрезки
            cur = ""
            for word in re.sub(r"(?<=\d) (?=\d)", "\u00a0", _plain(r.sub)).split(" "):
                if cur and len(cur) + 1 + len(word) > TEXT_W - 2:
                    lines.append("  " + cur)
                    cur = word
                else:
                    cur = (cur + " " + word).strip()
            if cur:
                lines.append("  " + _fit(cur, TEXT_W - 2))
        per_row.append(lines[start:])
    if keep_rows:
        return head_lines, per_row
    return "\n".join(lines)


# ======================= картинка =======================

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")
FONT_DIR = os.path.join(ASSETS, "fonts")
_fonts = {}


def _f(weight, size):
    k = (weight, size)
    if k not in _fonts:
        from PIL import ImageFont
        path = os.path.join(FONT_DIR, f"Inter-{weight}.otf")
        try:
            _fonts[k] = ImageFont.truetype(path, size)
        except OSError:
            try:
                _fonts[k] = ImageFont.truetype("DejaVuSans-Bold.ttf" if weight in ("Bold", "SemiBold") else "DejaVuSans.ttf", size)
            except OSError:
                _fonts[k] = ImageFont.load_default()
    return _fonts[k]


class _Pen:
    """Рисует сверху вниз; первый проход меряет высоту, второй — рисует."""
    PAD = 64

    def __init__(self, draw=None):
        self.d, self.y = draw, 60

    def text(self, x, s, weight, size, color=None, anchor="la", y=None, maxw=None):
        s = _plain(s)
        f = _f(weight, size)
        if maxw and self.d and self.d.textlength(s, font=f) > maxw:
            lo, hi = 0, len(s)                 # двоичный поиск длины вместо обрезки по букве
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.d.textlength(s[:mid].rstrip() + "…", font=f) <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            s = (s[:lo].rstrip() + "…") if lo else ""
        if self.d:
            self.d.text((x, self.y if y is None else y), s, font=f, fill=COLORS.get(color, color or COLORS[None]),
                        anchor=anchor)

    def rr(self, box, r, fill):
        if self.d:
            self.d.rounded_rectangle(box, r, fill=fill)

    def line(self, y):
        if self.d:
            self.d.line([(self.PAD, y), (IMG_W - self.PAD, y)], fill="#E6E8EB", width=2)

    def paste(self, name, x, y, w):
        """Картинка медали (assets/medals/<name>.png) шириной w, с прозрачностью."""
        im = getattr(self, "im", None)
        if im is None:
            return
        m = medal_image(name, w)
        if m is not None:
            im.paste(m, (int(x), int(y)), m)


_medals = {}
MEDAL_DIR = os.path.join(ASSETS, "medals")


def medal_image(name, w):
    """Медаль нужной ширины (кэш). None, если файла нет."""
    k = (name, w)
    if k not in _medals:
        from PIL import Image
        path = os.path.join(MEDAL_DIR, f"{name}.png")
        try:
            im = Image.open(path).convert("RGBA")
            _medals[k] = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        except OSError:
            _medals[k] = None
    return _medals[k]


MAX_H = 1700         # высота картинки: ширина × ~1,6 — целиком помещается на экране телефона
DENSE_H = 3600       # дашборд одной картинкой (Telegram принимает фото до ширина+высота 10000)


def _groups(v):
    """Экран → группы: заголовок группы (повторяется на следующей картинке, если группа не влезла) + строки."""
    gs = []

    def cont(t):
        return t if t.endswith("(продолжение)") else t.rstrip(":") + " (продолжение)" + (":" if t.endswith(":") else "")
    for kind, b in v.blocks:
        if kind == "tiles":
            gs.append(([], [("gap", 18), ("tiles", b)], []))
        elif kind == "table":
            title, head, rows = b
            hd = [("gap", 18)] + ([("btitle", title)] if title else []) + [("thead", head)]
            ch = ([("btitle", cont(title))] if title else []) + [("thead", head)]
            gs.append((hd, [("trow", head, r) for r in rows] + [("tend",)], ch))
        elif kind == "cards":
            title, items = b
            mx = max([c.bar for c in items if c.bar] or [1])
            hd = [("gap", 18)] + ([("btitle", title)] if title else [])
            gs.append((hd, [("card", c, mx) for c in items], [("btitle", cont(title))] if title else []))
        elif kind == "lines":
            title, items = b
            cur_h, cur_b, cur_c = [("gap", 18)] + ([("btitle", title)] if title else []), [], \
                ([("btitle", cont(title))] if title else [])
            for x in items:
                if _is_head(x) and (cur_b or len(cur_h) > 1):
                    gs.append((cur_h, cur_b, cur_c))
                    cur_h, cur_b, cur_c = [("gap", 10), ("line", x)], [], [("line", cont(x))]
                elif _is_head(x):
                    cur_h, cur_c = cur_h + [("line", x)], cur_c + [("line", cont(x))]
                else:
                    cur_b.append(("line", x))
            gs.append((cur_h, cur_b + [("gap", 6)], cur_c))
        elif kind == "medals":
            title, items = b
            hd = [("gap", 18)] + ([("btitle", title)] if title else [])
            gs.append((hd, [("medal", it) for it in items], [("btitle", cont(title))] if title else []))
        elif kind == "matrix":
            title, names, rows = b
            hd = [("gap", 18)] + ([("btitle", title)] if title else []) + [("mhead", names)]
            ch = ([("btitle", cont(title))] if title else []) + [("mhead", names)]
            gs.append((hd, [("mrow", len(names), lab, cells) for lab, cells in rows] + [("tend",)], ch))
        elif kind == "note" and b[1] != "txt":
            gs.append(([], [("gap", 18), ("note", b[0])], []))
    return gs


def _draw_el(pen, el):
    P = pen.PAD
    k = el[0]
    if k == "gap":
        pen.y += el[1]
    elif k == "tiles":
        b = el[1]
        per = min(3, len(b))
        gap = 18
        tw = (IMG_W - 2 * P - gap * (per - 1)) / per
        for i, (lab, val, c) in enumerate(b):
            if i and i % per == 0:
                pen.y += 118
            x = P + (i % per) * (tw + gap)
            pen.rr((x, pen.y, x + tw, pen.y + 100), 22, "#F7F8FA")
            pen.text(x + 22, _plain(lab), "Medium", 23, "muted", y=pen.y + 14, maxw=tw - 40)
            pen.text(x + 22, _plain(val), "Bold", 36, c, y=pen.y + 46, maxw=tw - 40)
        pen.y += 118
    elif k == "btitle":
        pen.text(P, el[1], "SemiBold", 30)
        pen.y += 48
    elif k == "thead":
        _table_head(pen, el[1])
    elif k == "trow":
        _table_row(pen, el[1], el[2])
    elif k == "tend":
        pen.line(pen.y)
        pen.y += 16
    elif k == "card":
        _draw_card(pen, el[1], el[2])
    elif k == "line":
        s = el[1]
        head = _is_head(s)
        if head:
            pen.y += 10
        w_ = "SemiBold" if head else "Regular"
        for part in _wrap(pen, _plain(s), w_, 28, IMG_W - 2 * P):
            pen.text(P, part, w_, 28)
            pen.y += 40
    elif k == "note":
        for part in _wrap(pen, _plain(el[1]), "Regular", 24, IMG_W - 2 * P):
            pen.text(P, part, "Regular", 24, "muted")
            pen.y += 34
    elif k == "mhead":
        names = el[1]
        first, cw = _mcols(len(names))
        for i, nm in enumerate(names):
            t = _plain(nm)
            f = _f("SemiBold", 22)
            if pen.d:
                while len(t) > 3 and pen.d.textlength(t, font=f) > cw - 6:
                    t = t[:-2] + "."
            pen.text(P + first + cw * i + cw / 2, t, "SemiBold", 22, "muted", anchor="ma")
        pen.y += 44
    elif k == "mrow":
        n, lab, cells = el[1], el[2], el[3]
        first, cw = _mcols(n)
        rh = 72
        pen.line(pen.y)
        pen.text(P, lab, "SemiBold", 28, y=pen.y + rh / 2, anchor="lm", maxw=first - 14)
        for j, c in enumerate(cells[:n]):
            x0 = P + first + cw * j
            if c.color in ("good", "warn", "bad"):
                pen.rr((x0 + 5, pen.y + 10, x0 + cw - 5, pen.y + rh - 10), 14, SOFT[c.color])
            fs = 26
            if pen.d:
                while fs > 15 and pen.d.textlength(_plain(c.text), font=_f("Bold", fs)) > cw - 12:
                    fs -= 1
            pen.text(x0 + cw / 2, c.text, "Bold", fs, c.color, anchor="mm", y=pen.y + rh / 2)
        pen.y += rh
    elif k == "medal":
        img, title, sub, right = el[1]
        h = 112
        pen.paste(img, P, int(pen.y), 84)
        tx = P + 84 + 24
        rw = 0
        if right:
            rw = 260
            pen.text(IMG_W - P, _plain(right), "Bold", 30, "good", anchor="ra", y=pen.y + 22)
        pen.text(tx, _plain(title), "SemiBold", 30, y=pen.y + 14, maxw=IMG_W - P - tx - rw - 10)
        if sub:
            pen.text(tx, _plain(sub), "Regular", 24, "muted", y=pen.y + 58, maxw=IMG_W - P - tx - rw - 10)
        pen.y += h


def _mcols(n):
    """Матрица: ширина колонки подписей и колонки человека."""
    first = 250 if n > 5 else 330
    return first, (IMG_W - 2 * _Pen.PAD - first) / max(1, n)


def _head_draw(pen, v, page, total):
    P = pen.PAD
    if page == 0:
        mw = IMG_W - 2 * P - (110 if total > 1 else 0)
        size = 50 if not pen.d or pen.d.textlength(_plain(v.title), font=_f("Bold", 50)) <= mw else 40   # длинный — мельче
        pen.text(P, v.title, "Bold", size, maxw=mw)
        if total > 1:
            pen.text(IMG_W - P, f"1 / {total}", "Medium", 26, "muted", anchor="ra", y=pen.y + 16)
        pen.y += 66
        if v.subtitle:
            pen.text(P, v.subtitle, "Regular", 28, "muted", maxw=IMG_W - 2 * P)
            pen.y += 46
    else:
        pen.text(P, v.title, "SemiBold", 34, maxw=IMG_W - 2 * P - 120)
        pen.text(IMG_W - P, f"{page + 1} / {total}", "Medium", 26, "muted", anchor="ra", y=pen.y + 6)
        pen.y += 54


def _null_pen():
    from PIL import Image, ImageDraw
    real = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    class _Null:
        def __getattr__(self, k):
            if k == "textlength":
                return real.textlength
            return lambda *a, **kw: None
    return _Pen(_Null())


def _height(el):
    pen = _null_pen()
    pen.y = 0
    _draw_el(pen, el)
    return pen.y


def paginate(v):
    """Раскладывает экран по картинкам не выше MAX_H. Группа (заголовок + строки) целиком переносится на следующую
    картинку; если она длиннее картинки — делится, а на продолжении повторяется её заголовок."""
    first_head = 60 + 66 + (46 if v.subtitle else 0)
    next_head = 60 + 54
    limit = (DENSE_H if getattr(v, "dense", False) else MAX_H) - 80
    pages_, cur, h = [], [], first_head

    def H(els):
        return sum(_height(e) for e in els)

    def new_page():
        nonlocal cur, h, has_group
        pages_.append(cur)
        cur, h, has_group = [], next_head, False
    has_group = False          # на картинке уже есть группа с заголовком — следующая идёт на новую
    for head, body, chead in _groups(v):
        hh, bh = H(head), H(body)
        titled = any(e[0] in ("btitle", "thead", "mhead") or (e[0] == "line" and _is_head(e[1])) for e in head)
        if titled and has_group and cur and not getattr(v, "dense", False):
            new_page()
            head = [e for e in head if e[0] != "gap"]
            hh = H(head)
        if titled:
            has_group = True
        if h + hh + bh <= limit:
            cur += head + body
            h += hh + bh
            continue
        if cur and next_head + hh + bh <= limit:         # целиком влезет на новую картинку — переносим всю группу
            new_page()
            has_group = titled
            head = [e for e in head if e[0] != "gap"]
            cur += head + body
            h += H(head) + bh
            continue
        # длинная группа: делим, на каждой новой картинке — заголовок группы «(продолжение)»
        first_row = _height(body[0]) if body else 0
        if cur and h + hh + first_row > limit:
            new_page()
            has_group = titled
            head = [e for e in head if e[0] != "gap"]
        cur += head
        h += H(head)
        for el in body:
            eh = _height(el)
            if h + eh > limit and el[0] not in ("tend", "gap"):
                new_page()
                cur += chead
                h += H(chead)
                has_group = True
            cur.append(el)
            h += eh
    if cur or not pages_:
        pages_.append(cur)
    return [p_ for p_ in pages_ if p_] or [[]]


def _draw(v, pen, page, pg=None):
    pg = pg or paginate(v)
    page = max(0, min(page, len(pg) - 1))
    _head_draw(pen, v, page, len(pg))
    for el in pg[page]:
        _draw_el(pen, el)
    pen.y += 40


def _wrap(pen, s, weight, size, maxw):
    if not pen.d:
        return [s[i:i + 60] for i in range(0, max(1, len(s)), 60)]
    f, out, cur = _f(weight, size), [], ""
    s = re.sub(r"(?<=\d) (?=\d)|(?<=\d) (?=₽)", "\u00a0", s)      # «8 833 192 ₽» не разрывать
    for w in s.split(" "):
        t = (cur + " " + w).strip()
        if pen.d.textlength(t, font=f) <= maxw:
            cur = t
        else:
            if cur:
                out.append(cur)
            cur = w
    return out + ([cur] if cur else [])


def _cols(head):
    P = _Pen.PAD
    n = len(head)
    full = IMG_W - 2 * P
    first = full / n if getattr(head, "even", False) else full * (getattr(head, "first", None) or (0.42 if n >= 4 else 0.5 if n == 3 else 0.62))   # первая шире
    cw = (full - first) / max(1, n - 1)
    xs = [P + first + cw * (i + 1) for i in range(n - 1)]               # правые края колонок
    return n, full, first, cw, xs


def _table_head(pen, head):
    P = pen.PAD
    n, full, first, cw, xs = _cols(head)
    if getattr(head, "even", False):
        for i in range(n):
            pen.text(P + first * i + first / 2, head[i], "SemiBold", 23, "bad" if head[i] == "Пт" else "muted", anchor="ma")
        pen.y += 40
        return
    pen.text(P, head[0], "SemiBold", 23, "muted")
    for i in range(1, n):
        pen.text(xs[i - 1], head[i], "SemiBold", 23, "muted", anchor="ra")
    pen.y += 40


def _table_row(pen, head, r):
    P = pen.PAD
    n, full, first, cw, xs = _cols(head)
    pen.line(pen.y)
    if getattr(head, "even", False):           # календарь: клетки по центру колонок
        cy = pen.y + 20
        for i in range(n):
            c = r.cells[i] if i < len(r.cells) else Cell("")
            if c.color in ("good", "warn", "bad", "blue") and c.text:
                cx = P + first * i + first / 2
                pen.rr((cx - first / 2 + 6, cy - 8, cx + first / 2 - 6, cy + 46), 14, SOFT[c.color])
            pen.text(P + first * i + first / 2, c.text, "Bold" if c.color else "Medium", 30, c.color, anchor="ma", y=cy)
        pen.y += 76
        return
    wide = bool(r.sub) and pen.d and pen.d.textlength(_plain(r.sub), font=_f("Regular", 24)) > first - 16
    h = 124 if wide else 100 if r.sub else 76      # длинная подпись — отдельной строкой во всю ширину
    cy = pen.y + 20
    if r.mark:
        pen.rr((P - 22, cy + 6, P - 12, cy + 34), 5, COLORS[r.mark])
    pen.text(P, r.cells[0].text, "SemiBold", 31, r.cells[0].color, y=cy, maxw=first - 16)
    if r.sub:
        pen.text(P, r.sub, "Regular", 24, "muted", y=cy + (66 if wide else 42), maxw=(full if wide else first) - 16)
    for i in range(1, n):
        c = r.cells[i] if i < len(r.cells) else Cell("")
        x = xs[i - 1]
        if c.bar:
            p_, need = c.bar
            bw = cw - 20
            bx = x - bw
            pen.rr((bx, cy + 40, x, cy + 52), 6, "#EDEFF2")
            pen.rr((bx, cy + 40, bx + max(12, bw * min(max(p_, 0), 1)), cy + 52), 6, COLORS.get(c.color, COLORS["blue"]))
            if need and 0 < need < 1 and pen.d:
                nx = bx + bw * need
                pen.d.line([(nx, cy + 34), (nx, cy + 58)], fill=COLORS[None], width=3)
            pen.text(x, c.text, "Bold", 28, c.color, anchor="ra", y=cy - 4)
        elif c.chip and c.text:
            f = _f("Bold", 25)
            tw = pen.d.textlength(_plain(c.text), font=f) if pen.d else 80
            pen.rr((x - tw - 28, cy - 2, x, cy + 38), 19, SOFT.get(c.color, SOFT[None]))
            pen.text(x - 14, c.text, "Bold", 25, c.color, anchor="ra", y=cy + 5)
        else:
            pen.text(x, c.text, "Bold" if c.bold or i == n - 1 else "Medium", 30, c.color, anchor="ra", y=cy,
                     maxw=cw - 8)
    pen.y += h


def _draw_card(pen, c, mx):
    P = pen.PAD
    pen.line(pen.y)
    cy = pen.y + 20
    vw = 330 if c.value else 0
    pen.text(P, c.title, "SemiBold", 31, None, y=cy, maxw=IMG_W - 2 * P - vw - 20)
    if c.value:
        pen.text(IMG_W - P, c.value, "Bold", 31, c.color, anchor="ra", y=cy)
    y = cy + 44
    sub = list(c.lines) + ([c.phone] if c.phone else [])
    for s in sub[:3]:
        pen.text(P, s, "Regular", 25, "muted", y=y, maxw=IMG_W - 2 * P - (300 if c.bar else 0))
        y += 34
    if c.bar:
        bw = 260
        w = bw * min(1, c.bar / mx) if mx else 0
        pen.rr((IMG_W - P - bw, cy + 52, IMG_W - P, cy + 64), 6, "#EDEFF2")
        pen.rr((IMG_W - P - max(12, w), cy + 52, IMG_W - P, cy + 64), 6, COLORS[c.color] if c.color else "#F0A3A3")
    if c.note:
        pen.text(IMG_W - P, c.note, "Bold", 23, c.note_color, anchor="ra", y=cy + 76)
        y = max(y, cy + 110)
    pen.y = max(y, cy + 80) + 10


def to_image(v, page=0):
    """Страница экрана — картинкой PIL (для PNG и PDF)."""
    from PIL import Image, ImageDraw
    pg = paginate(v)
    m = _null_pen()
    _draw(v, m, page, pg)
    h = int(m.y) + 40
    im = Image.new("RGB", (IMG_W, h), "#F4F5F7")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24, 24, IMG_W - 24, h - 24), 36, fill="#FFFFFF")
    pen = _Pen(d)
    pen.im = im
    _draw(v, pen, page, pg)
    return im


def to_png(v, page=0):
    im = to_image(v, page)
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)   # optimize=True — в 3–4 раза дольше на сервере
    return buf.getvalue()


def pages(v):
    return len(paginate(v))


# ======================= короткие числа =======================

def short_money(x):
    """1 162 009 → «1,16м», 64 232 → «64т», 950 → «950»."""
    x = float(x or 0)
    a = abs(x)
    if a >= 1e6:
        s = f"{x / 1e6:.2f}".rstrip("0").rstrip(".") + "м"
    elif a >= 1e4:
        s = f"{round(x / 1e3):.0f}т"
    elif a >= 1e3:
        s = f"{x / 1e3:.1f}".rstrip("0").rstrip(".") + "т"
    else:
        s = f"{round(x):.0f}"
    return s.replace(".", ",")


def tone(r, need=1.0):
    """Цвет выполнения: выполнено — good, близко к нужному — warn, сильно ниже — bad."""
    if r is None:
        return None
    if r >= need - 1e-9:
        return "good"
    return "warn" if r >= need - 0.15 else "bad"


def debt_tone(share):
    if share is None:
        return None
    return "good" if share <= 0.10 else "warn" if share <= 0.20 else "bad"


def to_pdf(views):
    """Несколько экранов → один PDF, страница = экран (картинка во всю ширину телефона). Для работы без связи.
    Страницы рисуются по одной и сразу сжимаются в JPEG — в памяти не держим все картинки разом: иначе рассылка PDF
    многим людям сразу съедает память сервера."""
    jpgs = []
    for v in views:
        for i in range(pages(v)):
            im = to_image(v, i)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80, optimize=False)
            jpgs.append((im.width, im.height, buf.getvalue()))
            im.close()
            del im
    if not jpgs:
        return b""
    return _jpeg_pdf(jpgs)


def _jpeg_pdf(jpgs, dpi=150):
    """Простой PDF из готовых JPEG: каждая картинка — страница своего размера."""
    out = io.BytesIO()
    offs = []

    def obj(n, body, stream=None):
        offs.append((n, out.tell()))
        out.write(f"{n} 0 obj\n".encode())
        out.write(body.encode() if isinstance(body, str) else body)
        if stream is not None:
            out.write(b"\nstream\n" + stream + b"\nendstream")
        out.write(b"\nendobj\n")
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    n_pages = len(jpgs)
    kids = " ".join(f"{3 + i * 3} 0 R" for i in range(n_pages))
    obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")
    for i, (w, h, data) in enumerate(jpgs):
        pw, ph = w * 72 / dpi, h * 72 / dpi
        pn, im_n, cn = 3 + i * 3, 4 + i * 3, 5 + i * 3
        obj(pn, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
                f"/Resources << /XObject << /Im{i} {im_n} 0 R >> >> /Contents {cn} 0 R >>")
        obj(im_n, f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                  f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>", data)
        content = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im{i} Do Q".encode()
        obj(cn, f"<< /Length {len(content)} >>", content)
    xref = out.tell()
    total = 3 + n_pages * 3
    out.write(f"xref\n0 {total}\n0000000000 65535 f \n".encode())
    for n, o in sorted(offs):
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()

