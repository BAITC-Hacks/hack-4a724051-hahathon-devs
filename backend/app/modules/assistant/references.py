"""Resolve short follow-up questions without treating their words as catalog search.

The selected product is a reference, not cached price/stock. Generated alternatives
must never silently replace the product the customer asked about.
"""
import re


def reference_product_id(previous: dict) -> int | None:
    if "reference_product_id" in previous:
        return previous["reference_product_id"]
    # Read old persisted responses without a migration or cross-dialog lookup.
    alternatives = {str(value) for value in previous.get("alternative_reasons", {})}
    primary = [item["id"] for item in previous.get("products", []) if str(item["id"]) not in alternatives]
    return primary[0] if len(primary) == 1 else None


def followup_kind(text: str) -> str | None:
    tokens = re.findall(r"[\w]+", text.casefold().replace("ё", "е"))
    if not tokens:
        return None
    # An article, parameters or a new product description must go through search.
    alternatives = {"аналог", "аналоги", "аналогов", "альтернатива", "альтернативы", "замена", "заменить",
                    "alternative", "alternatives", "replacement", "equivalent", "балама", "баламасы", "аналогы"}
    filler = {"а", "есть", "ли", "еще", "ему", "него", "нему", "этого", "этот", "товара", "товар", "его",
              "можно", "чем", "какие", "какой", "найди", "найдите", "подбери", "подберите", "покажи",
              "покажите", "пожалуйста", "для", "на", "у", "к", "тогда", "другой", "другие", "подходящий",
              "any", "an", "a", "is", "are", "there", "do", "you", "have", "please", "find", "show", "me",
              "for", "it", "this", "product", "other", "more", "бар", "ма", "ме", "осы", "тауарға", "оның"}
    if any(token in alternatives for token in tokens) and all(token in alternatives | filler for token in tokens):
        return "alternatives"
    phrase = " ".join(tokens)
    if phrase in {"характеристики", "его характеристики", "какие характеристики", "есть в наличии", "а наличие",
                  "какая цена", "цена", "остаток", "сертификат", "есть сертификат", "specifications", "in stock",
                  "price", "сипаттамалары", "бағасы"}:
        return "product"
    return None
