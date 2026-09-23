"""Векторные иллюстрации для синтетических товаров.

Рисуем сами, без чужих фото и логотипов: так нет вопросов с авторскими правами,
а на картинке видны параметры карточки (полюса, номинал, ток утечки, сечение,
мощность), и проверяющему легко сверить изображение с описанием.
"""

import math
from html import escape

W, H = 480, 360
BG = "#f4f7fb"
BRAND_ACCENT = {
    "IEK": "#c8102e", "Legrand": "#4a4f55", "Schneider Electric": "#3a8d3f", "Chint": "#1f5fa8",
    "MEGALIGHT": "#e07b00", "Dekraft": "#6b4fa0",
}
CORE_COLORS = {3: ["#8b5a2b", "#2f6fd1", "#d4c21a"], 5: ["#8b5a2b", "#222222", "#8a8a8a", "#2f6fd1", "#d4c21a"]}


def _text(x, y, value, size=14, weight=400, fill="#1d2733", anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(str(value))}</text>')


def _frame(body: str, caption: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">'
        f'<title>{escape(caption)}</title>'
        '<defs><linearGradient id="plastic" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#dde3ea"/></linearGradient>'
        '<linearGradient id="dark" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#5a616b"/><stop offset="1" stop-color="#2b3036"/></linearGradient>'
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#1d2733" flood-opacity="0.18"/></filter></defs>'
        f'<rect width="{W}" height="{H}" fill="{BG}"/>'
        f'{body}'
        + _text(W - 12, H - 12, "демо-иллюстрация", 11, fill="#9aa5b1", anchor="end")
        + '</svg>'
    )


def _module(x, y, w, h, accent, top_label, big, small, toggle_bar=False):
    """Один модуль DIN-аппарата: корпус, винты клемм, рычаг, маркировка."""
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="url(#plastic)" stroke="#b8c2cd"/>',
        f'<rect x="{x + 6}" y="{y + 8}" width="{w - 12}" height="30" rx="4" fill="#e9edf2" stroke="#c9d1da"/>',
        f'<circle cx="{x + w / 2}" cy="{y + 23}" r="9" fill="#b9c1ca" stroke="#8e98a3"/>',
        f'<line x1="{x + w / 2 - 6}" y1="{y + 23}" x2="{x + w / 2 + 6}" y2="{y + 23}" stroke="#6d7781" stroke-width="2"/>',
        f'<rect x="{x + 6}" y="{y + h - 38}" width="{w - 12}" height="30" rx="4" fill="#e9edf2" stroke="#c9d1da"/>',
        f'<circle cx="{x + w / 2}" cy="{y + h - 23}" r="9" fill="#b9c1ca" stroke="#8e98a3"/>',
        f'<line x1="{x + w / 2 - 6}" y1="{y + h - 23}" x2="{x + w / 2 + 6}" y2="{y + h - 23}" stroke="#6d7781" stroke-width="2"/>',
        f'<rect x="{x + w / 2 - 11}" y="{y + 52}" width="22" height="44" rx="4" fill="url(#dark)"/>',
        f'<rect x="{x + w / 2 - 11}" y="{y + 52}" width="22" height="10" rx="3" fill="{accent}"/>',
        _text(x + w / 2, y + 118, top_label, 10, 700, accent),
        _text(x + w / 2, y + 142, big, 19, 700),
        _text(x + w / 2, y + 160, small, 10, 400, "#4d5965"),
    ]
    return "".join(parts)


def breaker(brand, series, poles, current, curve, ka):
    accent = BRAND_ACCENT.get(brand, "#1f5fa8")
    w, h = 62, 220
    total = w * poles
    x0, y0 = (W - total) / 2, 60
    ka_mark = str(int(float(ka.replace("кА", "").replace(",", ".")) * 1000))
    body = [f'<g filter="url(#shadow)">']
    for i in range(poles):
        body.append(_module(x0 + i * w, y0, w, h, accent, series if i == 0 else "", f"{curve}{current}", ka_mark))
    if poles > 1:
        body.append(f'<rect x="{x0 + w / 2 - 12}" y="{y0 + 70}" width="{total - w + 24}" height="7" rx="3" fill="#2b3036"/>')
    body.append("</g>")
    body.append(_text(W / 2, 36, f"{brand} · {poles}P · {curve}{current} · {ka}", 15, 700))
    return _frame("".join(body), f"Автоматический выключатель {series} {poles}P {curve}{current} {brand}")


def rcbo(brand, series, current, leak, ka):
    accent = BRAND_ACCENT.get(brand, "#1f5fa8")
    w, h = 124, 220
    x0, y0 = (W - w) / 2, 60
    body = [
        '<g filter="url(#shadow)">',
        _module(x0, y0, w, h, accent, series, f"C{current}", f"{leak} мА · тип AC"),
        f'<rect x="{x0 + w - 32}" y="{y0 + 58}" width="20" height="20" rx="4" fill="#f2c500" stroke="#b89400"/>',
        _text(x0 + w - 22, y0 + 73, "T", 12, 700, "#5a4700"),
        "</g>",
        _text(W / 2, 36, f"{brand} · 1P+N · C{current} · {leak} мА · {ka}", 15, 700),
    ]
    return _frame("".join(body), f"Дифференциальный автомат {series} C{current} {leak}мА {brand}")


def mccb(brand, series, current, ka):
    accent = BRAND_ACCENT.get(brand, "#1f5fa8")
    x0, y0, w, h = 130, 58, 220, 250
    body = [
        '<g filter="url(#shadow)">',
        f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="14" fill="url(#dark)"/>',
        f'<rect x="{x0 + 16}" y="{y0 + 14}" width="{w - 32}" height="26" rx="4" fill="#1d2126"/>',
        f'<rect x="{x0 + 16}" y="{y0 + h - 40}" width="{w - 32}" height="26" rx="4" fill="#1d2126"/>',
        f'<rect x="{x0 + 40}" y="{y0 + 60}" width="{w - 80}" height="120" rx="10" fill="#e6e9ed"/>',
        f'<rect x="{x0 + w / 2 - 16}" y="{y0 + 78}" width="32" height="70" rx="6" fill="#2b3036"/>',
        f'<rect x="{x0 + w / 2 - 16}" y="{y0 + 78}" width="32" height="14" rx="5" fill="{accent}"/>',
        _text(x0 + w / 2, y0 + 170, series, 12, 700, accent),
        _text(x0 + w / 2, y0 + 202, f"In = {current} A", 18, 700, "#ffffff"),
        "</g>",
        _text(W / 2, 36, f"{brand} · 3P · {current} A · {ka}", 15, 700),
    ]
    return _frame("".join(body), f"Автоматический выключатель в литом корпусе {series} {current}A {brand}")


def socket(brand, series, color):
    plate = "#3b3f45" if color == "антрацит" else "#ffffff"
    insert = "#2c3036" if color == "антрацит" else "#f1f3f6"
    hole = "#0f1114" if color == "антрацит" else "#4a525c"
    cx, cy = W / 2, 185
    body = [
        '<g filter="url(#shadow)">',
        f'<rect x="{cx - 115}" y="{cy - 115}" width="230" height="230" rx="26" fill="{plate}" stroke="#c3cad3"/>',
        f'<circle cx="{cx}" cy="{cy}" r="78" fill="{insert}" stroke="#c3cad3"/>',
        f'<circle cx="{cx}" cy="{cy}" r="60" fill="{plate}" stroke="#c3cad3"/>',
        f'<circle cx="{cx - 24}" cy="{cy}" r="8" fill="{hole}"/><circle cx="{cx + 24}" cy="{cy}" r="8" fill="{hole}"/>',
        f'<rect x="{cx - 7}" y="{cy - 62}" width="14" height="10" rx="2" fill="#b7bec7"/>',
        f'<rect x="{cx - 7}" y="{cy + 52}" width="14" height="10" rx="2" fill="#b7bec7"/>',
        "</g>",
        _text(W / 2, 40, f"{brand} {series} · 16 A · 250 В · {color}", 15, 700),
    ]
    return _frame("".join(body), f"Розетка с заземлением {series} {color} {brand}")


def cable(section, brand):
    cores, size = section.split("х")
    cores = int(cores)
    colors = CORE_COLORS.get(cores, ["#8b5a2b"] * cores)
    cx, cy, r_out = 190, 190, 108
    ring = 58 if cores > 3 else 48
    core_r = 26 if cores > 3 else 32
    parts = [
        '<g filter="url(#shadow)">',
        f'<circle cx="{cx}" cy="{cy}" r="{r_out}" fill="#2b3036"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r_out - 12}" fill="#d9dde2"/>',
    ]
    for i, color in enumerate(colors):
        angle = -math.pi / 2 + 2 * math.pi * i / cores
        x, y = cx + ring * math.cos(angle), cy + ring * math.sin(angle)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{core_r}" fill="{color}"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{core_r * 0.55:.1f}" fill="#c77b3b" stroke="#a45f25"/>')
    parts.append("</g>")
    parts.append(_text(385, 170, f"{cores} × {size} мм²", 22, 700, anchor="middle"))
    parts.append(_text(385, 198, "медь, 0,66 кВ", 13, 400, "#4d5965"))
    parts.append(_text(385, 220, "ВВГнг(А)-LS", 13, 700, "#4d5965"))
    parts.append(_text(W / 2, 40, f"Кабель ВВГнг(А)-LS {cores}×{size} · {brand}", 15, 700))
    return _frame("".join(parts), f"Кабель ВВГнг(А)-LS {section}")


def led_panel(brand, power, flux):
    accent = BRAND_ACCENT.get(brand, "#1f5fa8")
    x0, y0, s = 120, 70, 240
    cells = "".join(
        f'<rect x="{x0 + 18 + i * 51}" y="{y0 + 18 + j * 51}" width="45" height="45" rx="3" fill="#ffffff" opacity="0.9"/>'
        for i in range(4) for j in range(4)
    )
    body = [
        '<defs><radialGradient id="glow"><stop offset="0" stop-color="#fffbe6"/>'
        '<stop offset="1" stop-color="#e8eef5"/></radialGradient></defs>',
        '<g filter="url(#shadow)">',
        f'<rect x="{x0}" y="{y0}" width="{s}" height="{s}" rx="10" fill="#dfe5ec" stroke="#b8c2cd"/>',
        f'<rect x="{x0 + 10}" y="{y0 + 10}" width="{s - 20}" height="{s - 20}" rx="6" fill="url(#glow)"/>',
        cells,
        f'<rect x="{x0}" y="{y0 + s - 8}" width="{s}" height="8" rx="4" fill="{accent}"/>',
        "</g>",
        _text(W / 2, 44, f"{brand} · {power} Вт · {flux} лм · 4000K · IP40", 15, 700),
    ]
    return _frame("".join(body), f"Светильник светодиодный {power}W {brand}")


def render(spec: dict) -> str:
    kind = spec["kind"]
    args = {k: v for k, v in spec.items() if k != "kind"}
    return {"breaker": breaker, "rcbo": rcbo, "mccb": mccb, "socket": socket, "cable": cable,
            "led_panel": led_panel}[kind](**args)
