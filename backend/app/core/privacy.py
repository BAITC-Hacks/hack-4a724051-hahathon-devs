"""Reject obvious payment credentials before persisting extracted text.

This conservative pattern guard is not a general-purpose DLP/OCR classifier.
"""
import re


def payment_data(text: str) -> bool:
    return bool(re.search(r"\b(?:\d[ -]?){13,19}\b|\b(?:cvv|cvc)\s*[:=]?\s*\d{3,4}\b|\bsk-[A-Za-z0-9_-]{16,}", text, re.I))
