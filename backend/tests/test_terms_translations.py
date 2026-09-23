from dataclasses import replace
import re

import pytest

from app.modules.knowledge.service import PurchaseTermsService
from app.modules.knowledge.translations import localized_title, localized_topic


def test_all_bundled_topics_have_source_pinned_translations():
    for topic in PurchaseTermsService().topics.values():
        for language in ("kk", "en"):
            translated = localized_topic(topic, language)
            assert translated is not None, (topic.id, language)
            text, caveats = translated
            assert text and localized_title(topic, language)
            assert len(caveats) == len(topic.caveats)
            # Dates, thresholds, ranges and the conflicting delivery threshold all remain.
            numeric = lambda s: re.findall(r"\d+(?:[ :\-]\d+)*", s)
            assert numeric(text) == numeric(topic.text)
            assert numeric(" ".join(caveats)) == numeric(" ".join(topic.caveats))


@pytest.mark.parametrize("field,value", [("text", "Changed policy"), ("title", "Changed title"),
                                        ("caveats", ("Changed caveat",)), ("sources", ("https://example.com/changed",))])
def test_changed_source_cannot_reuse_translation(field, value):
    topic = replace(PurchaseTermsService().topics["delivery"], **{field: value})
    assert localized_topic(topic, "en") is None
    assert localized_topic(topic, "kk") is None
    assert localized_title(topic, "en") is None
    assert localized_topic(topic, "ru") == (topic.text, list(topic.caveats))


def test_unknown_language_or_topic_requires_explicit_untranslated_fallback():
    topic = PurchaseTermsService().topics["payment"]
    assert localized_topic(topic, "de") is None
    assert localized_topic(replace(topic, id="future_topic"), "en") is None
