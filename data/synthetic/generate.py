"""Генератор синтетического каталога в формате /api/products/detail ekt.kz.

Реальные карточки не используются. Структура полей, названия складов и стиль
наименований взяты по образцу API, а ID, артикулы, цены и остатки придуманы.

Запуск: python data/synthetic/generate.py
"""

import json
import random
from pathlib import Path

OUT = Path(__file__).parent
rng = random.Random(2026)

STORES = [
    (2, "Брак MEGALIGHT"), (3, "Шымкент (ул.Байдукова)"), (4, "Маркетинг MEGALIGHT"),
    (5, "Никонов"), (6, "РЦ MEGALIGHT"), (7, "Талдыкорган"), (8, "Тастак"),
    (9, "Торговый зал"), (10, "Востановленный продукт"), (11, "перемещение"),
    (12, "Основной склад"), (13, "Алматы"), (14, "ЩИТОВОЕ"), (15, "РЦ UNIT"),
    (16, "Образцы Отдел Закупа"), (17, "Витрина ТЗ"), (18, "Усть-Каменогорск"),
    (19, "Тараз"), (20, "Атырау"), (21, "Караганда"), (22, "Новосибирск"),
    (23, "Актау"), (24, "Нур-Султан"), (25, "Шымкент (Тассай)"),
]
CITY_STORES = [3, 7, 12, 13, 18, 19, 20, 21, 23, 24, 25]

products = []
next_id = 900001
next_article = 990100001


def slug(text):
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejziiklmnoprstufhccss_y_eua")
    out = "".join(c if c.isalnum() else "_" for c in text.lower().translate(table))
    return "_".join(p for p in out.split("_") if p)[:70]


def stock(mode):
    """mode: normal | zero | defect_only | low"""
    q = {sid: 0 for sid, _ in STORES}
    if mode == "normal":
        for sid in rng.sample(CITY_STORES, rng.randint(2, 6)):
            q[sid] = rng.randint(1, 60)
    elif mode == "low":
        q[rng.choice(CITY_STORES)] = rng.randint(1, 3)
    elif mode == "defect_only":
        q[2] = rng.randint(1, 5)
        q[16] = 1
    return [{"id": sid, "name": name, "quantity": q[sid]} for sid, name in STORES]


def add(name, category, price, props, description, mode="normal", cert=True, unit="шт"):
    global next_id, next_article
    pid, article = next_id, f"{next_article}_"
    next_id += 1
    next_article += 1
    stores = stock(mode)
    properties = {
        "CML2_ARTICLE": article,
        "CML2_TAXES": "16",
        "CML2_BAR_CODE": f"29{pid:011d}",
        "KRATNOST_MIN": "1",
        "EDINITSA_IZMERENIYA": unit,
        "NOVINKA": "Нет",
        "SPETSPREDLOZHENIE": "Нет",
        **props,
    }
    certificates = []
    if cert:
        certificates.append({
            "type": "Сертификат соответствия ТР ТС 004/2011",
            "number": f"ЕАЭС KZ SYN.{pid}",
            "valid_until": f"20{rng.randint(27, 30)}-{rng.randint(1, 12):02d}-01",
            "url": f"/certificates/SYN-{pid}.pdf",
        })
    products.append({
        "id": pid,
        "name": name,
        "article": article,
        "description": description,
        "price": price,
        "quantity": sum(s["quantity"] for s in stores),
        "stores": stores,
        "image": None,
        "url": f"/catalog/{category}/{slug(name)}/",
        "offers": [],
        "properties": properties,
        "certificates": certificates,
    })
    return pid


def breaker_description(brand, series, poles, current, curve, kA):
    return (
        f"Автоматический выключатель {series} {poles}P {current}А характеристика {curve} {brand} "
        f"для защиты линий от перегрузки и короткого замыкания.\r\n\r\n"
        f"Основные характеристики:\r\n\r\n"
        f"\tСерия: {series}\r\n\tКоличество полюсов: {poles}\r\n\tНоминальный ток: {current}А\r\n"
        f"\tХарактеристика срабатывания: {curve}\r\n\tОтключающая способность: {kA}\r\n"
        f"\tНоминальное напряжение: {'230' if poles == 1 else '400'}В\r\n\tПроизводитель: {brand}"
    )


# Модульные автоматические выключатели: пересекаются по току/полюсам между брендами,
# чтобы для товара без остатка находились честные аналоги.
BREAKER_SERIES = [
    ("IEK", "ВА47-29", "4,5кА", 1.0),
    ("Legrand", "RX3", "6кА", 2.1),
    ("Schneider Electric", "Easy9", "4,5кА", 1.7),
    ("Chint", "NXB-63", "6кА", 1.3),
]
BASE_PRICE = {6: 1450, 10: 1350, 16: 1300, 20: 1350, 25: 1400, 32: 1600, 40: 2100, 63: 3400}
zero_breakers = {("Legrand", 1, 16), ("Schneider Electric", 3, 25), ("IEK", 1, 32)}
for brand, series, kA, k in BREAKER_SERIES:
    for poles in (1, 3):
        for current in (6, 10, 16, 20, 25, 32, 40, 63):
            if rng.random() < 0.35 and (brand, poles, current) not in zero_breakers:
                continue
            curve = "C"
            mode = "zero" if (brand, poles, current) in zero_breakers else rng.choice(["normal"] * 6 + ["low"])
            code = f"{rng.randint(100000, 999999)}"
            name = f"{code} АВ {series} {poles}P {current}А {curve} {kA} {brand}"
            add(
                name, "nizkovoltnaya_apparatura/modulnye_avtomaticheskie_vyklyuchateli",
                int(BASE_PRICE[current] * k * (2.8 if poles == 3 else 1) // 10 * 10),
                {
                    "ARTIKULPOSTAVSHCHIKA": code, "TORGOVAYA_MARKA": brand, "SERIYA": series,
                    "KOLICHESTVO_POLYUSOV": str(poles), "NOMINALNYY_TOK": f"{current}А",
                    "KHARAKTERISTIKA_SRABATYVANIYA": curve,
                    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": kA,
                    "NOMINALNOE_NAPRYAZHENIE": "230В" if poles == 1 else "400В",
                    "TIP_USTROYSTVA": "Автоматический выключатель",
                },
                breaker_description(brand, series, poles, current, curve, kA),
                mode=mode, cert=rng.random() < 0.7,
            )

# Силовой автомат с противоречием: в названии и описании 160 А, в свойствах 250 А.
add(
    "971300 АВ DRX250 MT 3ф 160А 18kA Legrand",
    "nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli", 61900,
    {
        "ARTIKULPOSTAVSHCHIKA": "971300", "TORGOVAYA_MARKA": "Legrand", "SERIYA": "DRX250",
        "KOLICHESTVO_POLYUSOV": "3", "NOMINALNYY_TOK": "250 А",
        "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "18кА", "NOMINALNOE_NAPRYAZHENIE": "400В",
        "TIP_USTROYSTVA": "Автоматический выключатель в литом корпусе",
    },
    "Автоматический выключатель DRX250 MT 3P 160А 18kA Legrand для распределительных сетей.\r\n\r\n"
    "Основные характеристики:\r\n\r\n\tСерия: DRX250 MT\r\n\tКоличество полюсов: 3\r\n"
    "\tНоминальный ток: 160А\r\n\tОтключающая способность: 18kA\r\n\tНоминальное напряжение: 400В AC",
)
add(
    "971301 АВ ВА88-35 3P 160А 35кА IEK",
    "nizkovoltnaya_apparatura/silovye_avtomaticheskie_vyklyuchateli", 48700,
    {
        "ARTIKULPOSTAVSHCHIKA": "971301", "TORGOVAYA_MARKA": "IEK", "SERIYA": "ВА88-35",
        "KOLICHESTVO_POLYUSOV": "3", "NOMINALNYY_TOK": "160А",
        "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "35кА", "NOMINALNOE_NAPRYAZHENIE": "400В",
        "TIP_USTROYSTVA": "Автоматический выключатель в литом корпусе",
    },
    "Автоматический выключатель ВА88-35 3P 160А 35кА IEK в литом корпусе.\r\n\r\n"
    "Основные характеристики:\r\n\r\n\tКоличество полюсов: 3\r\n\tНоминальный ток: 160А\r\n"
    "\tОтключающая способность: 35кА\r\n\tНоминальное напряжение: 400В",
)

# УЗО и дифавтоматы
for brand, series, price in [("IEK", "АД12", 6900), ("Legrand", "DX3", 18400), ("Chint", "NXBLE-32", 7600)]:
    for current, leak in [(16, 30), (25, 30), (32, 30)]:
        code = f"{rng.randint(100000, 999999)}"
        mode = "zero" if (brand, current) == ("Legrand", 25) else "normal"
        add(
            f"{code} Диф.авт. {series} 1P+N {current}А {leak}мА C {brand}",
            "nizkovoltnaya_apparatura/differentsialnye_avtomaty",
            price + (current - 16) * 90,
            {
                "ARTIKULPOSTAVSHCHIKA": code, "TORGOVAYA_MARKA": brand, "SERIYA": series,
                "KOLICHESTVO_POLYUSOV": "2", "NOMINALNYY_TOK": f"{current}А",
                "NOMINALNYY_OTKLYUCHAYUSHCHIY_DIFFERENTSIALNYY_TOK": f"{leak}мА",
                "KHARAKTERISTIKA_SRABATYVANIYA": "C", "NOMINALNOE_NAPRYAZHENIE": "230В",
                "TIP_USTROYSTVA": "Дифференциальный автоматический выключатель",
            },
            f"Дифференциальный автомат {series} 1P+N {current}А {leak}мА {brand}. Защищает от перегрузки, "
            f"короткого замыкания и токов утечки.\r\n\r\nОсновные характеристики:\r\n\r\n"
            f"\tНоминальный ток: {current}А\r\n\tТок утечки: {leak}мА\r\n\tХарактеристика: C",
            mode=mode,
        )

# Розетки и выключатели
for brand, series, color, price in [
    ("Legrand", "Mosaic", "белый", 2350), ("Legrand", "Valena Life", "белый", 1980),
    ("Schneider Electric", "AtlasDesign", "белый", 1650), ("Schneider Electric", "AtlasDesign", "антрацит", 1890),
]:
    code = f"{rng.randint(100000, 999999)}"
    add(
        f"{code} Розетка 2К+З {series} {color} {brand}",
        "rozetki_vyklyuchateli_korobki/rozetki", price,
        {
            "ARTIKULPOSTAVSHCHIKA": code, "TORGOVAYA_MARKA": brand, "SERIYA": series, "TSVET": color,
            "NOMINALNYY_TOK": "16А", "NOMINALNOE_NAPRYAZHENIE": "250В", "TIP_USTANOVKI": "скрытая",
            "TIP_USTROYSTVA": "Розетка с заземлением",
        },
        f"Розетка с заземлением {series} {brand}, цвет {color}, для скрытой установки.\r\n\r\n"
        f"Основные характеристики:\r\n\r\n\tНоминальный ток: 16А\r\n\tНапряжение: 250В\r\n\tЦвет: {color}",
        mode="zero" if series == "Mosaic" else "normal",
    )

# Кабель: единица «м», дробное количество не округляем.
for section, price in [("3х1,5", 410), ("3х2,5", 620), ("5х4", 1650)]:
    add(
        f"Кабель ВВГнг(А)-LS {section} ок(N,PE)-0,66 ГОСТ",
        "kabel_provod/kabel_silovoy", price,
        {
            "ARTIKULPOSTAVSHCHIKA": f"VVG-{section}", "TORGOVAYA_MARKA": "Кабельный завод",
            "SECHENIE": section, "NOMINALNOE_NAPRYAZHENIE": "660В",
        },
        f"Силовой кабель ВВГнг(А)-LS {section} с пониженным дымо- и газовыделением. Продаётся на метры.",
        unit="м",
    )

# Светильники: один товар лежит только на складе брака.
for brand, power, flux, price, mode in [
    ("MEGALIGHT", 36, 3600, 6400, "normal"), ("MEGALIGHT", 18, 1800, 3900, "defect_only"),
    ("IEK", 36, 3200, 5200, "normal"), ("Dekraft", 40, 4000, 5900, "low"),
]:
    code = f"{rng.randint(100000, 999999)}"
    add(
        f"{code} Светильник LED ДПО {power}W {flux}Lm 4000K IP40 {brand}",
        "svetilniki_lampy/svetilniki_ofisnye", price,
        {
            "ARTIKULPOSTAVSHCHIKA": code, "TORGOVAYA_MARKA": brand, "MOSHCHNOST": f"{power}Вт",
            "SVETOVOY_POTOK_LM": str(flux), "TSVETOVAYA_TEMPERATURA": "4000K", "STEPEN_ZASHCHITY": "IP40",
        },
        f"Светодиодный светильник {power}Вт {flux}Лм 4000K IP40 {brand} для офисов и общественных помещений.",
        mode=mode,
    )

catalog = {
    "meta": {
        "synthetic": True,
        "note": "Синтетические данные для разработки и демо. Структура повторяет /api/products/detail ekt.kz, "
                "значения придуманы. Поля certificates и properties.EDINITSA_IZMERENIYA добавлены для демо.",
        "count": len(products),
    },
    "items": products,
}
(OUT / "ekt_products.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")

# Заглушки сертификатов, чтобы ссылки из карточек открывались.
cert_dir = OUT / "certificates"
cert_dir.mkdir(exist_ok=True)
for p in products:
    for c in p["certificates"]:
        text = f"SYNTHETIC CERTIFICATE SYN.{p['id']} - demo data, not a real document"
        stream = f"BT /F1 14 Tf 40 780 Td ({text}) Tj ET".encode()
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        pdf, offsets = b"%PDF-1.4\n", []
        for i, body in enumerate(objs, 1):
            offsets.append(len(pdf))
            pdf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(pdf)
        pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        pdf += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        (cert_dir / f"SYN-{p['id']}.pdf").write_bytes(pdf)

print(f"{len(products)} товаров, {sum(len(p['certificates']) for p in products)} сертификатов")
