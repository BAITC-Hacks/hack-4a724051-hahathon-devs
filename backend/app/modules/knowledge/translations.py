"""Reviewed translations of the bundled purchase terms, pinned to source facts.

No new store policies are supplied here. A changed title, text, caveat or source
invalidates its translation; the caller must show the original with a warning.
"""
import hashlib
import json

from app.modules.knowledge.service import TermsTopic


SOURCE_HASHES = {
    "payment": "6872304efef6a1572dd3cd6ef78f69e76b522fbdd27290dd2f48662d331c7cd3",
    "delivery": "2c9771ecffeace047ae831536e45c1f84ee601ce75c860c2efd77603fda06305",
    "pickup": "7498e005b3e57f5f8506d573a5d189cf7886f64936c4c70088b7a0b5a644bc82",
    "min_order": "39c4962eef603e25c71c63c5b8f50c36722ae383a21486c2721e3c8ef96e7348",
    "installment": "821d9cf5ab43618b40f71d4308ac787fcec7c765f03cdf74986aeec841168b69",
    "returns": "37c7b46edbc51aa58917ceb22a60bc620a533830b91f0b82aae89a8bd02d68b2",
    "legal_entity": "6d28680bd4856457a160216ba2894f889aaa0e1788e86318731f790c57048d26",
    "online_price": "6faab88efb55ef79b291b21bdfb054eb98b18f61f0f0aed16c823d0d07482de1",
}

# Each value contains a translated title, the text, and every source caveat.
TRANSLATIONS = {
    "payment": {
        "en": ("Payment", "Individuals can pay online on the website by bank card (Visa or Mastercard, through the secure AirbaPay page with 3-D Secure), in cash upon receipt, or in cash/by card through a POS terminal when collecting from the showroom. Legal entities receive an invoice for bank transfer after placing an order. The availability of a payment method is confirmed when placing the order.", ()),
        "kk": ("Төлем", "Жеке тұлғалар сайтта банк картасымен онлайн төлей алады (Visa, Mastercard, 3-D Secure арқылы қорғалған AirbaPay беті), тауарды алған кезде қолма-қол немесе сауда залынан өздері алып кеткенде қолма-қол/POS-терминал арқылы картамен төлей алады. Заңды тұлғаларға тапсырыс рәсімделгеннен кейін қолма-қол ақшасыз төлеуге шот беріледі. Төлем тәсілінің қолжетімділігі тапсырыс рәсімделгенде расталады.", ()),
    },
    "delivery": {
        "en": ("Delivery", "Goods agreed with the manager are delivered within 48 hours, during the day from 9:00 to 17:00; the driver calls in advance. Within the city, orders costing more than 30 000 tenge are delivered free of charge. Delivery to other cities in Kazakhstan is free for orders over 400 000 tenge. In other cases, the manager calculates the cost and delivery time based on the address, weight and volume of the order.",
               ("The page https://ekt.kz/about/howto/ gives a different threshold for free city delivery (15 000 tenge). We use the value from the delivery page; the manager confirms the exact amount when the order is placed.",)),
        "kk": ("Жеткізу", "Менеджермен келісілген тауар 48 сағат ішінде, күндіз 9:00-ден 17:00-ге дейін жеткізіледі; жүргізуші алдын ала қоңырау шалады. Қала ішінде құны 30 000 теңгеден асатын тапсырыс тегін жеткізіледі. Қазақстанның басқа қалаларына сомасы 400 000 теңгеден асатын тапсырыс тегін жеткізіледі. Қалған жағдайларда жеткізу құны мен мерзімін менеджер тапсырыстың мекенжайына, салмағына және көлеміне қарай есептейді.",
               ("https://ekt.kz/about/howto/ бетінде қала ішіндегі тегін жеткізудің басқа шегі (15 000 теңге) көрсетілген. Жеткізу бетіндегі мәнді қолданамыз; нақты соманы менеджер тапсырыс рәсімделгенде растайды.",)),
    },
    "pickup": {
        "en": ("Collection", "Collection is available in every city where the company operates: Astana, Almaty, Shymkent, Aktau, Atyrau, Karaganda, Ust-Kamenogorsk, Taraz and Taldykorgan. It is best to arrive after the manager confirms that the order is ready.", ()),
        "kk": ("Өзі алып кету", "Компания жұмыс істейтін барлық қалаларда тапсырысты өзі алып кетуге болады: Астана, Алматы, Шымкент, Ақтау, Атырау, Қарағанды, Өскемен, Тараз, Талдықорған. Менеджер тапсырыстың дайын екенін растағаннан кейін келген дұрыс.", ()),
    },
    "min_order": {
        "en": ("Minimum order quantity", "There is no overall minimum order on the website. A minimum quantity and required order multiples may apply to an individual product; check the specific product card. The manager agrees the terms for large wholesale orders.",
               ("The KRATNOST_MIN field is present in the API for most products, but the partner has not yet confirmed its exact meaning.",)),
        "kk": ("Ең аз тапсырыс мөлшері", "Сайтта жалпы тапсырыс үшін ең төменгі шек жоқ. Жеке тауарға ең аз сан және тапсырыс санының еселігі белгіленуі мүмкін; оларды нақты тауар карточкасынан қарау керек. Ірі көтерме тапсырыстың шарттарын менеджер келіседі.",
               ("API-дегі KRATNOST_MIN өрісі тауарлардың көбінде бар, бірақ серіктес оның нақты мағынасын әлі растаған жоқ.",)),
    },
    "installment": {
        "en": ("Installments", "A 0-0-4 installment plan is available through Bank CenterCredit: 4 months without overpayment, for an order amount from 6 000 to 200 000 tenge, for Kazakhstan residents aged 18 to 63. The bank makes its decision individually.", ()),
        "kk": ("Бөліп төлеу", "Банк ЦентрКредит арқылы 0-0-4 бөліп төлеу бар: артық төлемсіз 4 ай, тапсырыс сомасы 6 000-нан 200 000 теңгеге дейін, Қазақстанның 18-ден 63 жасқа дейінгі резиденттеріне арналған. Банк әр өтінім бойынша жеке шешім қабылдайды.", ()),
    },
    "returns": {
        "en": ("Returns and exchanges", "Goods of inadequate quality can be returned within 14 days of purchase after an expert assessment at the point of sale. The goods must include all supplied components and be in their original packaging. Goods damaged by improper use, goods with damaged single-use packaging, and goods sold by length (wires and cables) cannot be returned.", ()),
        "kk": ("Қайтару және айырбастау", "Сапасы тиісті талаптарға сай емес тауарды сатып алған күннен бастап 14 күн ішінде, сату орнында сараптамадан кейін қайтаруға болады. Тауар толық жиынтықта және түпнұсқа қаптамасында болуы керек. Дұрыс пайдаланбаудан бүлінген, бір рет қолданылатын қаптамасы бұзылған тауарлар, сондай-ақ метрлеп сатылатын тауарлар (сымдар, кабельдер) қайтарылмайды.", ()),
    },
    "legal_entity": {
        "en": ("Purchasing as a legal entity", "When ordering for a legal entity, provide the company's details; an invoice is issued after the order is placed. To collect the goods, a representative usually needs an identity document, a power of attorney with a current date, and the order or invoice details. It is best to agree the exact set of documents with the manager in advance.", ()),
        "kk": ("Заңды тұлға атынан сатып алу", "Заңды тұлға атынан тапсырыс бергенде компанияның деректемелерін көрсету керек; рәсімделгеннен кейін шот беріледі. Тауарды алу үшін өкілге әдетте жеке басын куәландыратын құжат, өзекті күні көрсетілген сенімхат және тапсырыс немесе шот деректері қажет. Құжаттардың нақты тізімін менеджермен алдын ала келіскен дұрыс.", ()),
    },
    "online_price": {
        "en": ("Website prices", "Website prices include a special discount that applies when ordering online from the online store. A price list can be requested through the website form by specifying the city and buyer type.", ()),
        "kk": ("Сайттағы бағалар", "Сайттағы бағаларға интернет-дүкенде онлайн тапсырыс бергенде қолданылатын арнайы жеңілдік кіреді. Қала мен сатып алушы түрін көрсетіп, сайттағы өтінім арқылы прайс-парақты сұратуға болады.", ()),
    },
}


def source_fingerprint(topic: TermsTopic) -> str:
    body = {key: getattr(topic, key) for key in ("id", "title", "text", "sources", "caveats")}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def localized_topic(topic: TermsTopic, language: str) -> tuple[str, list[str]] | None:
    """Return source for ru, pinned translation for kk/en, or None for unsafe fallback.

    None means the caller must use the original text and disclose that it is
    untranslated. This includes an unknown topic/language or changed source.
    """
    if language == "ru":
        return topic.text, list(topic.caveats)
    if source_fingerprint(topic) != SOURCE_HASHES.get(topic.id):
        return None
    translated = TRANSLATIONS.get(topic.id, {}).get(language)
    return (translated[1], list(translated[2])) if translated else None


def localized_title(topic: TermsTopic, language: str) -> str | None:
    """Title with the same source-change guard as localized_topic."""
    if language == "ru":
        return topic.title
    if localized_topic(topic, language) is None:
        return None
    return TRANSLATIONS[topic.id][language][0]
