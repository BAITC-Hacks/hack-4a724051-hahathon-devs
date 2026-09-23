"""Safe non-LLM path. Does not receive credentials or a write-capable port."""
from app.core.errors import AppError
from app.integrations.ports import CatalogPort
from app.modules.assistant.ports import ProcessingContext
from app.modules.chat.models import AssistantOutput


class CatalogOnlyProcessor:
    def __init__(self, catalog: CatalogPort):
        self.catalog = catalog

    async def process(self, context: ProcessingContext) -> AssistantOutput:
        language = context.payload.language
        disabled = {
            "ru": "ИИ-ассистент пока не подключён. Доступен поиск по подключённому каталогу.",
            "en": "The AI assistant is not connected yet. Connected catalog search remains available.",
            "kk": "ЖИ көмекшісі әлі қосылмаған. Қосылған каталогтан іздеуге болады.",
        }[language]
        if context.payload.asset_ids:
            return AssistantOutput(
                message=disabled, mode="unavailable",
                unknowns=["attachment_analysis_requires_llm_integration"],
            )
        try:
            if context.payload.page_product_id is not None:
                products = [await self.catalog.get_product(context.payload.page_product_id)]
                warnings = []
            else:
                result = await self.catalog.search(context.payload.text, 5)
                products, warnings = result.items, result.warnings
                if result.coverage != "complete":
                    warnings = [*warnings, "catalog_coverage_is_partial_or_unknown"]
        except AppError as error:
            if error.code != "requires_integration":
                raise
            return AssistantOutput(message=disabled, mode="unavailable",
                                   unknowns=["catalog_requires_integration"])
        message = {
            "ru": "Результаты поиска показаны в карточках. ИИ-консультация пока не подключена.",
            "en": "Search results are shown in the cards. AI consultation is not connected yet.",
            "kk": "Іздеу нәтижелері карточкаларда көрсетілген. ЖИ кеңесі әлі қосылмаған.",
        }[language]
        # Facts and identifiers are copied from the trusted server adapter, never invented.
        return AssistantOutput(message=message, products=products, mode="catalog_only",
                               unknowns=warnings[:20])
