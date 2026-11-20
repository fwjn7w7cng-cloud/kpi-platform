"""🎨 Свой бренд клиента (Адам 20.11: «приложение в цветах логотипа»; бесплатно у всех).

Логотип и название на значке загружает Адам в пульте (карточка клиента → «🎨 Бренд», core/pult.py). Бренд лежит в базе клиента
(ключ KEY = {name, logo — PNG в base64, c1, c2, ts}); приложение клиента (core/transport/web.WebApi) перечитывает его на каждом
запросе по ts и пересобирает страницу, значки и манифест — перезапуск не нужен.
Цвета берутся из логотипа сами (`colors`): главный — шапка, витрина и кнопки, второй — акценты; поправить — вручную.
"""
import base64
import colorsys
import io
import re

KEY = "brand"
MAX_SIDE = 512
HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def get(db):
    b = db.get(KEY) or {}
    return b if isinstance(b, dict) else {}


def logo_bytes(b):
    try:
        return base64.b64decode(b["logo"]) if b.get("logo") else None
    except (ValueError, TypeError):
        return None


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(int(x) for x in rgb[:3])


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(h):
    """Относительная яркость 0…1 (WCAG)."""
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def shade(h, k):
    """k < 0 — темнее, k > 0 — светлее (доля пути к чёрному / белому)."""
    r, g, b = _rgb(h)
    if k < 0:
        return _hex((r * (1 + k), g * (1 + k), b * (1 + k)))
    return _hex((r + (255 - r) * k, g + (255 - g) * k, b + (255 - b) * k))


def readable(h):
    """Цвет под белый текст: темним, пока контраст с белым не станет ≥ 4,5."""
    for _ in range(12):
        if contrast(h, "#FFFFFF") >= 4.5:
            return h
        h = shade(h, -0.12)
    return h


def prepare(data):
    """Файл логотипа → (PNG байты ≤ 512 px, ошибка)."""
    from PIL import Image, UnidentifiedImageError
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "Не открылась картинка — пришлите PNG или JPG."
    im = im.convert("RGBA")
    bbox = _content_box(im)
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue(), ""


def _content_box(im):
    """Обрезать пустые поля (прозрачные или белые)."""
    from PIL import Image, ImageChops
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] < 250:
        return alpha.point(lambda a: 255 if a > 20 else 0).getbbox()
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im.convert("RGB"), bg).convert("L").point(lambda x: 255 if x > 24 else 0)
    return diff.getbbox()


def colors(png):
    """Два цвета логотипа: самый частый «настоящий» (не белый, не серый) и второй, заметно другой по оттенку.
    Логотип чёрно-белый — графит, как у Алапы."""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((120, 120))
    counts = {}
    for r, g, b, a in im.getdata():
        if a < 128:
            continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if l > 0.93 or (s < 0.18 and l > 0.12) or l < 0.05:
            continue
        key = (round(h * 24) % 24, round(l * 6), round(s * 4))
        c = counts.setdefault(key, [0, 0, 0, 0])
        c[0] += 1
        c[1] += r
        c[2] += g
        c[3] += b
    if not counts:
        return "#1C1C1C", "#6E6E6B"
    ranked = sorted(counts.items(), key=lambda kv: -kv[1][0])
    avg = lambda c: _hex((c[1] / c[0], c[2] / c[0], c[3] / c[0]))
    c1 = avg(ranked[0][1])
    hue1 = ranked[0][0][0]
    c2 = next((avg(c) for (hue, _, _), c in ranked[1:] if min(abs(hue - hue1), 24 - abs(hue - hue1)) >= 3 and c[0] >= ranked[0][1][0] * 0.05),
              None) or shade(c1, 0.35)
    return c1, c2


def css(b):
    """Переопределение цветов «Графита» под бренд (вставляется в конец <head>)."""
    c1, c2 = b.get("c1"), b.get("c2")
    if not (c1 and HEX.fullmatch(c1)):
        return ""
    main = readable(c1)
    acc2 = c2 if c2 and HEX.fullmatch(c2) else shade(main, 0.35)
    dark = shade(main, -0.25)
    return ("<style id=\"brandcss\">:root{--hdr:%s;--accent:%s;--accent-soft:%s;--hero1:%s;--hero2:%s;--uhero:%s;--brand2:%s}"
            "header{background:var(--hdr)} header .back,header .tools{background:%s}"
            "header .role{box-shadow:inset 0 0 0 1px %s} header{box-shadow:inset 0 -2px 0 var(--brand2)}"
            "header .hlogo{height:28px;width:auto;max-width:96px;object-fit:contain;background:#fff;border-radius:7px;margin-right:8px}"
            "</style>") % (main, main, shade(main, 0.88), main, dark, main, acc2, dark, shade(main, 0.3))


def icon(size, b, pad=None):
    """Значок: логотип клиента на белом."""
    from PIL import Image
    png = logo_bytes(b)
    if not png:
        return None
    img = Image.new("RGB", (size, size), "#FFFFFF")
    logo = Image.open(io.BytesIO(png)).convert("RGBA")
    full = logo.getchannel("A").getextrema()[0] > 250 and 0.85 < logo.width / logo.height < 1.18   # уже готовый значок — во весь размер
    if pad is None:
        pad = (0.07 if size >= 512 else 0.0) if full else (0.16 if size >= 512 else 0.1)
    w = int(size * (1 - 2 * pad))
    logo.thumbnail((w, w), Image.LANCZOS)
    img.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), logo)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
