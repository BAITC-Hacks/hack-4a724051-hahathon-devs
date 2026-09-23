"""Grounded runtime: the model plans; only server facts become customer claims.

No model-generated prose is displayed. Model output cannot confirm a cart or
provide prices, stock, certificates or URLs. Search coverage stays explicit.
"""
import asyncio
import base64
import dataclasses
import re
from pathlib import Path

from app.contracts import CartItem, ProposalInput
from app.core.errors import AppError
from app.core.privacy import payment_data
from app.modules.actions.service import is_explicit_confirmation, is_explicit_rejection
from app.modules.assistant.state import AssistantState
from app.modules.assistant.references import followup_kind, reference_product_id
from app.modules.chat.models import AssistantOutput
from app.modules.knowledge.service import PurchaseTermsService
from app.modules.knowledge.translations import localized_topic, localized_title
from app.modules.search.service import identifier_candidates

PROMPT = (Path(__file__).parent / "prompts" / "planner.md").read_text(encoding="utf-8")


def bounded_model_context(context, warnings):
    """Bound combined attachments/history, not just each file, before token reservation."""
    remaining = 24000
    documents = []
    for doc in context.documents:
        encoded = doc.text.encode("utf-8")
        text = encoded[:remaining].decode("utf-8", errors="ignore")
        remaining -= len(text.encode("utf-8"))
        truncated = len(text) < len(doc.text)
        if truncated:
            warnings.append("model_document_context_truncated")
        documents.append({"text": text, "status": "partial" if truncated else doc.status,
                          "warnings": doc.warnings + (["model_document_context_truncated"] if truncated else [])})
    history = [{"role": m.role, "text": m.text.encode("utf-8")[:1000].decode("utf-8", errors="ignore")}
               for m in context.history[-6:]]
    return documents, history

TEXT = {
    "ru": {
        "unknown": "Неизвестно", "stock": "Остаток", "price": "Цена", "attrs": "Характеристики",
        "missing": "Нужны артикул или читаемая маркировка и требуемые параметры. По фото нельзя гарантировать совместимость.",
        "none": "В доступной выборке товар не найден. Это не означает, что его нет во всём магазине.",
        "partial": "Поиск выполнен по доступной выборке каталога; полнота не гарантируется.",
        "unavailable": "Источник каталога сейчас недоступен. Цена и наличие не подтверждены.",
        "cert": "Сертификат", "nocert": "Сертификат в доступных данных не указан.",
        "conflict": "Расхождение источников — уточните у менеджера", "alt": "Кандидаты на замену",
        "noalt": "Подтверждённый аналог не найден: не хватает данных или параметры не совпадают.",
        "draft": "Предложение подготовлено. Корзина не изменена. Проверьте состав, количество и сумму; подтвердите кнопкой или отдельным сообщением «да, добавь».",
        "quantity": "Уточните точный артикул и количество каждой позиции. Корзина не изменена.",
        "applied": "Товары добавлены в корзину после вашего подтверждения.",
        "inactive": "Предложение больше не активно. Нужно проверить данные и создать новое.",
        "rejected": "Предложение отклонено. Корзина не изменена.",
        "noprop": "Нет показанного предложения для подтверждения. Сначала выберите товар и количество.",
        "demo": "Демонстрационные данные: цены, остатки и корзина не относятся к реальному заказу EKT.",
        "consent": "Для анализа вложений через OpenAI нужно разрешение на передачу их содержимого. Локально извлечённый текст можно искать без передачи.",
        "files": "Разбор вложений ограничен; проверьте предупреждения по файлам. Нечитаемые позиции требуют уточнения.",
        "manager": "Для сложного подбора обратитесь к менеджеру через раздел «Контакты» сайта. Сообщение менеджеру автоматически не отправляется.",
        "terms": "Условия из справочника магазина; действительность уточняйте при оформлении.",
        "original": "Ниже оригинальный текст источника на русском языке.",
    },
    "kk": {
        "unknown": "Белгісіз", "stock": "Қалдық", "price": "Бағасы", "attrs": "Сипаттамалар",
        "missing": "Артикулды немесе анық таңбалауды және қажетті параметрлерді көрсетіңіз. Фото бойынша сәйкестікке кепілдік берілмейді.",
        "none": "Қолжетімді каталогтан тауар табылмады. Бұл бүкіл дүкенде тауар жоқ дегенді білдірмейді.",
        "partial": "Іздеу каталогтың қолжетімді бөлігінде орындалды; толықтығына кепілдік жоқ.",
        "unavailable": "Каталог қазір қолжетімсіз. Баға мен қалдық расталмады.",
        "cert": "Сертификат", "nocert": "Қолжетімді деректерде сертификат көрсетілмеген.",
        "conflict": "Деректер қайшы — менеджерден нақтылаңыз", "alt": "Баламалар",
        "noalt": "Расталған балама табылмады: деректер жеткіліксіз немесе параметрлер сәйкес емес.",
        "draft": "Ұсыныс дайын. Себет өзгерген жоқ. Тізімді, санын және сомасын тексеріп, батырмамен немесе «иә, қос» деп растаңыз.",
        "quantity": "Әр тауардың нақты артикулы мен санын көрсетіңіз. Себет өзгерген жоқ.",
        "applied": "Растауыңыздан кейін тауарлар себетке қосылды.",
        "inactive": "Ұсыныс белсенді емес. Деректерді тексеріп, жаңасын жасау керек.",
        "rejected": "Ұсыныс қабылданбады. Себет өзгерген жоқ.",
        "noprop": "Растайтын көрсетілген ұсыныс жоқ. Алдымен тауар мен санын таңдаңыз.",
        "demo": "Демонстрациялық деректер: бағалар, қалдықтар және себет нақты EKT тапсырысы емес.",
        "consent": "Файлдарды OpenAI арқылы талдау үшін олардың мазмұнын жіберуге рұқсат қажет.",
        "files": "Файл талдауы шектеулі; ескертулерді тексеріңіз. Оқылмаған позицияларды нақтылау қажет.",
        "manager": "Күрделі таңдау үшін сайттың «Байланыс» бөлімі арқылы менеджерге жүгініңіз. Хабарлама автоматты түрде жіберілмейді.",
        "terms": "Дүкен анықтамалығындағы шарттар; рәсімдеу кезінде нақтылаңыз.",
        "original": "Төменде дереккөздің орыс тіліндегі түпнұсқа мәтіні берілген.",
    },
    "en": {
        "unknown": "Unknown", "stock": "Available quantity", "price": "Price", "attrs": "Specifications",
        "missing": "Please provide the article or legible markings and required specifications. A photo cannot establish compatibility.",
        "none": "No match in the available catalog subset. This does not establish absence from the whole store.",
        "partial": "Search covers the available catalog subset; completeness is not guaranteed.",
        "unavailable": "The catalog source is unavailable. Price and stock are unverified.",
        "cert": "Certificate", "nocert": "No certificate is listed in the available data.",
        "conflict": "Conflicting source values — check with a manager", "alt": "Replacement candidates",
        "noalt": "No verified replacement: required facts are missing or specifications do not match.",
        "draft": "Draft prepared; the cart is unchanged. Check items, quantities and total, then use the confirmation button or reply 'yes, add'.",
        "quantity": "Specify the exact article and quantity for each item. The cart is unchanged.",
        "applied": "Items were added to the cart after your confirmation.",
        "inactive": "The proposal is no longer active. Check the data and create a new one.",
        "rejected": "Proposal rejected. The cart is unchanged.",
        "noprop": "There is no displayed proposal to confirm. Select items and quantities first.",
        "demo": "Demo data: prices, stock and cart do not represent a real EKT order.",
        "consent": "Analyzing attachments with OpenAI requires permission to transmit their contents. Extracted text can be searched locally.",
        "files": "Attachment processing is bounded; check file warnings. Unreadable items need clarification.",
        "manager": "For complex selection, contact a manager through the website Contacts section. No message is sent automatically.",
        "terms": "Store reference terms; reconfirm when placing the order.",
        "original": "The original Russian source text follows.",
    },
}


def language(text, requested="auto", previous="ru"):
    q = text.lower()
    if re.search(r"english|английск|ағылшын", q):
        return "en"
    if re.search(r"русск|орыс|russian", q):
        return "ru"
    if re.search(r"қазақ|казах|kazakh", q) or re.search(r"[әғқңөұүһі]", q):
        return "kk"
    if re.search(r"[а-яё]", q):
        return "ru"
    if re.search(r"\b(stock|please|delivery|payment|find|add|yes|hello|price)\b", q):
        return "en"
    if requested != "auto":
        return requested
    return previous if previous in TEXT else "ru"


def purchase_request(text):
    if re.search(r"\b(?:не|not|don't|without)\b|қоспа", text, re.I):
        return False
    return bool(re.search(r"\b(добав[а-я]*|купить|закаж[а-я]*|хочу купить|add|buy|order|қос|сатып)\b", text.lower()))


class GroundedAssistant:
    def __init__(self, catalog, actions, documents, path, planner=None, image_reader=None):
        self.catalog, self.actions, self.documents = catalog, actions, documents
        self.state = AssistantState(path)
        self.terms = PurchaseTermsService()
        self.planner = planner
        # Распознавание фото и сканов (NVIDIA), участник 1: app/modules/documents/ocr.py
        self.image_reader = image_reader

    async def _read_images(self, context):
        """Фото и сканы превращаются в текст до поиска, только с согласия клиента на внешнюю обработку.

        Текст дописывается к своему файлу, поэтому дальше работает обычный путь:
        строки файла ищутся в каталоге и без модели OpenAI, а с моделью попадают в её план.
        """
        if not (self.image_reader and context.documents and context.payload.allow_external_analysis
                and hasattr(self.documents, "images")):
            return context
        owned = await self.documents.images(context.session_id, [str(d.asset_id) for d in context.documents])
        by_asset: dict = {}
        for image in owned:
            by_asset.setdefault(image.asset_id, []).append((image.mime_type, image.content))
        if not by_asset:
            return context
        documents = []
        for doc in context.documents:
            pages = by_asset.pop(doc.asset_id, None)
            if not pages:
                documents.append(doc)
                continue
            result = await self.image_reader.aread(pages, context.session_id)
            warnings = list(doc.warnings) + ["ocr_nvidia"] + (["ocr_pages_failed"] if result.pages_failed else [])
            text = "\n".join(x for x in (doc.text, result.text) if x)[:40000]
            status = "partial" if result.pages_failed or not result.text else doc.status
            documents.append(doc.model_copy(update={"text": text, "status": status, "warnings": warnings}))
        return dataclasses.replace(context, documents=tuple(documents))

    async def process(self, context):
        context = await self._read_images(context)
        previous = await asyncio.to_thread(self.state.previous, context.session_id, context.conversation_id)
        text = context.payload.text.strip()
        lang = language(text, context.payload.language, previous.get("language", "ru"))
        words = TEXT[lang]
        reference_id = reference_product_id(previous)
        followup = followup_kind(text) if not context.documents else None
        # A current, standalone customer confirmation only. Model/file text is never consulted.
        if not context.documents and (is_explicit_confirmation(text) or is_explicit_rejection(text)):
            draft = previous.get("proposal")
            if not draft:
                return AssistantOutput(message=words["noprop"], mode="action", language=lang,
                                       reference_product_id=reference_id)
            if is_explicit_confirmation(text):
                proposal = await self.actions.confirm(context.session_id, draft["id"], draft["version"],
                                                      "chat-confirm-" + context.turn_id)
                message = words["applied"] if proposal.status == "applied" else words["inactive"]
            else:
                proposal = await self.actions.reject(context.session_id, draft["id"])
                message = words["rejected"] if proposal.status == "rejected" else words["inactive"]
            if proposal.status == "applied" and proposal.cart_url:
                message += "\n" + proposal.cart_url
            return AssistantOutput(message=message, mode="action", proposal=proposal, language=lang,
                                   reference_product_id=reference_id)

        if followup and reference_id is None and not context.payload.page_product_id:
            clarification = {
                "ru": "Для какого товара? Укажите артикул или выберите одну карточку, чтобы я подобрал аналоги или уточнил характеристики.",
                "kk": "Қай тауар үшін? Артикулды көрсетіңіз немесе бір тауарды таңдаңыз.",
                "en": "Which product? Please provide its article or select one product card.",
            }
            return AssistantOutput(message=clarification[lang], mode="catalog_only", language=lang,
                                   warnings=["product_reference_required"])

        warnings = [w for d in context.documents for w in d.warnings]
        if any(d.status == "partial" for d in context.documents):
            warnings.append("partial_document_extraction")
        topics = self._topics(text)
        candidates = {}
        coverage = "unknown"
        unavailable = False
        document_ids = list(dict.fromkeys(token for d in context.documents for token in identifier_candidates(d.text)))
        try:
            if followup:
                item = await self.catalog.get_product(reference_id or context.payload.page_product_id)
                candidates[item.id] = item
            elif context.payload.page_product_id and not identifier_candidates(text):
                item = await self.catalog.get_product(context.payload.page_product_id)
                candidates[item.id] = item
            if not followup and (not topics or identifier_candidates(text)) and (not context.documents or identifier_candidates(text)):
                result = await self.catalog.search(text[:200], 8)
                candidates.update((p.id, p) for p in result.items)
                coverage = result.coverage
            # Resolve extracted articles before planning so document items can use real IDs.
            for query in document_ids[:20]:
                result = await self.catalog.search(query[:200], 3)
                candidates.update((p.id, p) for p in result.items)
            if len(document_ids) > 20:
                warnings.append("document_queries_truncated")
        except AppError:
            unavailable = True

        plan = None
        allow_files = context.payload.allow_external_analysis
        if self.planner and not followup and (not context.documents or allow_files):
            images = []
            if context.documents and hasattr(self.documents, "images"):
                images = await self.documents.images(context.session_id, [str(d.asset_id) for d in context.documents])
                images = [i if isinstance(i, dict) else {"media_type": i.mime_type, "data": base64.b64encode(i.content).decode("ascii")}
                          for i in images]
                if len(images) > 4:
                    warnings.append("vision_pages_truncated")
                    images = images[:4]
            model_documents, model_history = bounded_model_context(context, warnings)
            payload = {
                "request": text, "language": lang,
                "history": model_history,
                "candidates": [{"id": p.id, "article": p.article_original, "name": p.name} for p in candidates.values()],
                "topics": self.terms.topic_ids(),
                "documents": model_documents,
            }
            try:
                plan = await self.planner.plan(system=PROMPT, payload=payload, images=images,
                                               session_id=context.session_id, call_id=context.turn_id)
            except AppError as exc:
                warnings.append(exc.code)
            except Exception:
                warnings.append("llm_unavailable")
        elif context.documents and self.planner:
            warnings.append("external_analysis_consent_required")
        if context.documents and not plan and any(not d.text.strip() for d in context.documents):
            warnings.append("vision_analysis_unavailable")

        intent = followup or (plan or {}).get("intent", "search")
        if plan:
            # Plan IDs must come from pre-fetched catalog candidates, never model imagination.
            ids = plan.get("product_ids", [])
            if ids and all(pid in candidates for pid in ids):
                candidates = {pid: candidates[pid] for pid in ids}
            elif ids:
                warnings.append("unverified_model_product_id")
                intent = "clarify"
            if intent == "terms":
                topics = self.terms.get(plan.get("topic_ids", []))
        queries = (plan or {}).get("queries", []) or ([(plan or {}).get("query")] if (plan or {}).get("query") else [])
        if not plan and context.documents:
            queries = document_ids[:20] or [line.strip()[:200] for d in context.documents for line in d.text.splitlines() if line.strip()][:20]
        if intent == "search" or context.documents:
            for query in queries[:20]:
                try:
                    result = await self.catalog.search(query[:200], 3)
                    candidates.update((p.id, p) for p in result.items)
                    if not result.items:
                        warnings.append("unmatched_document_or_query_item")
                except AppError:
                    unavailable = True
        lines, sources, reasons = [], [], {}
        if topics:
            lines.append(words["terms"])
            for topic in topics:
                translated = localized_topic(topic, lang)
                if translated is None:
                    lines.append(words["original"])
                    content, caveats = topic.text, topic.caveats
                    warnings.append("translation_source_changed")
                else:
                    content, caveats = translated
                lines.append((localized_title(topic, lang) or topic.title) + ": " + content)
                lines.extend(caveats)
                sources.extend(topic.sources)
        if re.search(r"менеджер|manager|оператор", text, re.I):
            lines.append(words["manager"])
        # Capture the explicitly selected primary product BEFORE adding alternatives.
        # Terms retain it; ambiguous/new failed searches clear it instead of guessing.
        selected_reference = next(iter(candidates)) if len(candidates) == 1 else reference_id if topics and not candidates else None
        products = list(candidates.values())[:8]
        for p in list(products):
            if p.stock_status == "out_of_stock" or intent == "alternatives" or re.search(r"аналог|балама|alternative", text, re.I):
                try:
                    alternatives = await self.catalog.alternatives(p.id)
                    if not alternatives.items:
                        lines.append(words["noalt"])
                    for alt in alternatives.items:
                        candidates[alt.id] = alt
                        reasons[alt.id] = alternatives.reasons.get(alt.id, "")
                    warnings.extend(alternatives.warnings)
                except AppError:
                    lines.append(words["noalt"])
        products = list(candidates.values())[:12]
        if intent == "clarify":
            lines.append(words["missing"])
        proposal = None
        if purchase_request(text):
            items = (plan or {}).get("items", []) if intent == "propose" and plan.get("cart_requested") else []
            if not plan and len(products) == 1:
                match = re.search(r"(?<!\w)(\d+)\s*(?:шт\b|pcs\b|дана\b)", text, re.I)
                if match:
                    items = [{"product_id": products[0].id, "quantity": int(match[1])}]
            quantity_source = text + "\n" + "\n".join(d.text for d in context.documents)
            quantities_grounded = all(re.search(r"(?<!\d)" + str(i.get("quantity")) + r"(?!\d)", quantity_source) for i in items)
            if items and quantities_grounded and all(i.get("product_id") in candidates for i in items):
                try:
                    proposal = await self.actions.propose(context.session_id, ProposalInput(items=[CartItem(**i) for i in items]),
                                                          "chat-propose-" + context.turn_id)
                    lines.append(words["draft"])
                    lines.extend(f"{i.article_original}: {i.quantity} × {i.unit_price_amount} = {i.line_total_amount} {proposal.currency}"
                                 for i in proposal.items)
                    lines.append(f"Σ {proposal.total_amount} {proposal.currency}")
                except AppError as exc:
                    warnings.append(exc.code)
                    lines.append(words["quantity"])
            else:
                lines.append(words["quantity"])
        for p in products:
            lines.append(f"{p.article_original} — {p.name}")
            lines.append(f"{words['price']}: {p.price_amount or words['unknown']} {p.price_currency or ''}; "
                         f"{words['stock']}: {p.sellable_quantity if p.sellable_quantity is not None else words['unknown']}")
            if p.min_order is not None:
                lines.append(f"min: {p.min_order} {p.unit or ''}")
            for a in p.attributes[:12]:
                lines.append(f"{a.key}: {words['conflict'] if a.status == 'conflict' else a.value or words['unknown']}")
            if not p.certificates:
                lines.append(words["nocert"])
            for cert in p.certificates:
                lines.append(f"{words['cert']}: {cert.title} {cert.url}")
                sources.append(cert.url)
            if p.id in reasons and reasons[p.id]:
                lines.append(words["alt"] + ": " + reasons[p.id])
        if any("synthetic_demo_data" in p.warnings for p in products):
            lines.insert(0, words["demo"])
        if not products and not lines:
            lines.append(words["unavailable"] if unavailable else words["none"])
        unknowns = []
        if products and coverage != "complete":
            lines.append(words["partial"])
            unknowns.append("catalog_coverage_is_partial_or_unknown")
        if context.documents:
            if "external_analysis_consent_required" in warnings:
                lines.append(words["consent"])
            lines.append(words["files"])
        if (plan or {}).get("unresolved"):
            unknowns.append("document_or_request_needs_clarification")
            lines.append(words["missing"])
        answer = "\n".join(lines)
        if len(answer) > 8000:
            answer = answer[:7800] + "\n" + words["partial"]
            warnings.append("response_truncated")
        return AssistantOutput(message=answer, products=products, proposal=proposal, sources=list(dict.fromkeys(sources))[:30],
                               reference_product_id=selected_reference,
                               alternative_reasons=reasons, language=lang, unknowns=unknowns,
                               warnings=list(dict.fromkeys(warnings))[:30],
                               mode="grounded" if plan else "unavailable" if unavailable and not products else "catalog_only")

    def _topics(self, text):
        # Avoid substring 'налич' classifying stock questions as payment questions.
        mapping = {
            "payment": r"оплат|төле|payment|pay\b|безнал|сч[её]т",
            "delivery": r"достав|жеткіз|delivery|shipping",
            "min_order": r"минимал|кратн|minimum|minimal|ең аз|партия",
            "pickup": r"самовывоз|pickup|алып кет",
            "returns": r"возврат|қайтар|return|гарант",
            "installment": r"рассроч|бөліп|installment",
        }
        return self.terms.get([key for key, pattern in mapping.items() if re.search(pattern, text, re.I)])
