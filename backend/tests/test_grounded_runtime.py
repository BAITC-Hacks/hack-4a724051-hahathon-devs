"""Grounded assistant acceptance cases using the real HTTP queue and synthetic data.

No provider is instantiated: planners/documents used here are local recording fakes.
"""
import asyncio
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.contracts import ParsedDocument
from app.core.config import Settings
from app.main import create_app
from conftest import authenticate, conversation, submit


class RecordingPlanner:
    def __init__(self, **changes):
        self.calls = []
        self.result = {"intent": "search", "query": "", "queries": [], "product_ids": [], "items": [],
                       "topic_ids": [], "unresolved": [], "language": "ru", "clarification": "",
                       "cart_requested": False, **changes}

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    async def aclose(self):
        pass


class LocalDocuments:
    def __init__(self):
        self.image_calls = []

    async def resolve(self, session_id, asset_ids):
        return [ParsedDocument(asset_id=asset, status="ready", text="990100003_\nPRIVATE-DOCUMENT-CONTENT")
                for asset in asset_ids]

    async def images(self, session_id, asset_ids):
        self.image_calls.append((session_id, asset_ids))
        return []


@pytest.fixture
def grounded(tmp_path):
    services = build_container(Settings(_env_file=None, app_env="test", integration_mode="synthetic",
                                        local_db_path=tmp_path / "runtime.sqlite3"), documents=LocalDocuments())
    with TestClient(create_app(container=services)) as client:
        headers = authenticate(client)
        conv = conversation(client, headers)
        yield services, client, headers, conv


def turn(grounded, text, *, conv=None, **extra):
    services, client, headers, initial_conv = grounded
    response = submit(client, conv or initial_conv, headers, text=text, key=str(uuid4()), **extra)
    assert response.status_code == 202, response.text
    assert asyncio.run(services.worker.run_once())
    turn_id = response.json()["data"]["turn"]["id"]
    result = client.get(f"/api/v1/turns/{turn_id}")
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["status"] == "completed", data
    return data["output"]


def cart(grounded):
    return grounded[1].get("/api/v1/cart").json()["data"]


def test_known_article_renders_verified_facts_and_certificates(grounded):
    source = grounded[0].catalog.catalog.get_product(900003)
    answer = turn(grounded, source.article)
    assert answer["products"][0]["id"] == source.id
    assert source.article in answer["message"]
    assert str(source.price) in answer["message"]
    assert str(source.stock.sellable_quantity) in answer["message"]
    assert answer["products"][0]["attributes"]
    assert source.certificates
    mapped = asyncio.run(grounded[0].catalog.get_product(source.id))
    for certificate in mapped.certificates:
        assert certificate.url in answer["message"]
        assert certificate.url in answer["sources"]
        response = grounded[1].get(certificate.url)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF-")
        with TestClient(create_app(container=grounded[0])) as anonymous:
            assert anonymous.get(certificate.url).status_code == 401
    assert "catalog_coverage_is_partial_or_unknown" in answer["unknowns"]


def test_zero_stock_offers_only_explained_available_candidates(grounded):
    source = grounded[0].catalog.catalog.get_product(900015)
    answer = turn(grounded, source.article)
    assert answer["products"][0]["stock_status"] == "out_of_stock"
    alternatives = [product for product in answer["products"] if product["id"] != source.id]
    assert alternatives
    for alternative in alternatives:
        assert alternative["stock_status"] == "available"
        reason = answer["alternative_reasons"][str(alternative["id"])]
        assert reason and reason in answer["message"]
        assert "совместимость требует проверки" in reason


@pytest.mark.parametrize("question", ["есть аналог?", "А есть ещё аналоги?", "чем заменить?", "any alternatives?", "баламасы бар ма?"])
def test_followup_uses_primary_product_not_search_words_or_previous_alternatives(grounded, monkeypatch, question):
    first = turn(grounded, "990100015_")
    assert first["reference_product_id"] == 900015
    expected = set(first["alternative_reasons"])
    assert expected

    async def no_search(*args, **kwargs):
        pytest.fail("A reference-only follow-up must not run broad catalog search")

    monkeypatch.setattr(grounded[0].catalog, "search", no_search)
    answer = turn(grounded, question)
    assert answer["reference_product_id"] == 900015
    assert answer["products"][0]["id"] == 900015
    assert set(answer["alternative_reasons"]) == expected
    assert {str(item["id"]) for item in answer["products"]} == expected | {"900015"}
    repeated = turn(grounded, "есть аналог?")
    assert repeated["reference_product_id"] == 900015
    assert set(repeated["alternative_reasons"]) == expected


def test_reference_survives_terms_and_processor_restart_and_reads_current_facts(grounded):
    from dataclasses import replace
    from decimal import Decimal
    from app.modules.assistant.runtime import GroundedAssistant

    turn(grounded, "990100015_")
    assert turn(grounded, "условия доставки")["reference_product_id"] == 900015
    services = grounded[0]
    original = services.catalog.catalog.get_product(900015)
    services.catalog.catalog.replace_product(replace(original, price=Decimal("9999")))
    services.worker.processor = GroundedAssistant(services.catalog, services.actions, services.documents, services.settings.local_db_path)
    answer = turn(grounded, "есть аналог?")
    assert answer["products"][0]["id"] == 900015
    assert answer["products"][0]["price_amount"] == "9999"
    assert answer["alternative_reasons"]


def test_followup_without_unique_product_clarifies_and_does_not_call_model(grounded):
    planner = RecordingPlanner(product_ids=[900003])
    grounded[0].worker.processor.planner = planner
    answer = turn(grounded, "есть аналог?")
    assert not answer["products"] and not answer["alternative_reasons"]
    assert "product_reference_required" in answer["warnings"]
    assert planner.calls == []
    grounded[0].worker.processor.planner = None
    multiple = turn(grounded, "автомат")
    assert len(multiple["products"]) > 1 and multiple["reference_product_id"] is None
    assert not turn(grounded, "есть аналог?")["products"]


def test_new_article_replaces_context_and_unknown_article_clears_it(grounded):
    turn(grounded, "990100015_")
    answer = turn(grounded, "990100003_")
    assert answer["reference_product_id"] == 900003
    assert turn(grounded, "характеристики")["products"][0]["id"] == 900003
    missing = turn(grounded, "NONEXISTENT990999999_")
    assert missing["reference_product_id"] is None
    assert not missing["products"]
    assert not turn(grounded, "есть аналог?")["products"]


def test_reference_does_not_cross_conversation_or_session(grounded):
    turn(grounded, "990100015_")
    services, client, headers, _ = grounded
    other = conversation(client, headers)
    assert not turn(grounded, "есть аналог?", conv=other)["products"]
    with TestClient(create_app(container=services)) as stranger:
        stranger_headers = authenticate(stranger)
        stranger_conv = conversation(stranger, stranger_headers)
        assert not turn((services, stranger, stranger_headers, stranger_conv), "есть аналог?")["products"]


def test_explicit_alternative_article_and_new_description_do_not_reuse_old_context(grounded):
    turn(grounded, "990100003_")
    explicit = turn(grounded, "аналог 990100015_")
    assert explicit["reference_product_id"] == 900015
    assert explicit["alternative_reasons"]
    from app.modules.assistant.references import followup_kind
    assert followup_kind("аналог кабеля 3x2.5") is None
    assert followup_kind("аналог розетки") is None


@pytest.mark.parametrize("question,topic", [("Расскажите про доставку", "delivery"),
                                           ("Какие способы оплаты?", "payment")])
def test_purchase_terms_are_sourced(grounded, question, topic):
    expected = grounded[0].worker.processor.terms.get([topic])[0]
    answer = turn(grounded, question)
    assert expected.text in answer["message"]
    assert set(expected.sources).issubset(answer["sources"])
    assert not answer["products"]


def test_stock_question_is_not_misclassified_as_cash_payment(grounded):
    answer = turn(grounded, "990100003_ есть в наличии?")
    payment = grounded[0].worker.processor.terms.get(["payment"])[0]
    assert answer["products"][0]["id"] == 900003
    assert payment.text not in answer["message"]


def test_buy_draft_then_standalone_yes_applies_once_with_link(grounded):
    draft = turn(grounded, "Купить 990100003_ 2 шт")
    proposal = draft["proposal"]
    assert proposal and proposal["status"] == "proposed"
    assert proposal["items"][0]["quantity"] == 2
    assert cart(grounded)["items"] == []
    for _ in range(2):
        answer = turn(grounded, "да, добавь")
        assert answer["proposal"]["status"] == "applied"
        assert answer["proposal"]["cart_url"] == "/api/v1/cart/view"
        assert "/api/v1/cart/view" in answer["message"]
    assert cart(grounded)["items"][0]["quantity"] == 2
    checkout = grounded[1].get(answer["proposal"]["cart_url"])
    assert checkout.status_code == 200
    assert "text/html" in checkout.headers["content-type"]
    assert proposal["items"][0]["name"] in checkout.text
    with TestClient(create_app(container=grounded[0])) as anonymous:
        assert anonymous.get(answer["proposal"]["cart_url"]).status_code == 401


def test_another_conversation_cannot_confirm_displayed_draft(grounded):
    draft = turn(grounded, "Купить 990100003_ 2 шт")
    assert draft["proposal"]
    _, client, headers, _ = grounded
    other = conversation(client, headers)
    answer = turn(grounded, "да, добавь", conv=other)
    assert answer["proposal"] is None
    assert cart(grounded)["items"] == []
    assert turn(grounded, "да, добавь")["proposal"]["status"] == "applied"


def test_other_session_cannot_confirm_or_read_draft(grounded):
    services, _, _, _ = grounded
    draft = turn(grounded, "Купить 990100003_ 2 шт")["proposal"]
    with TestClient(create_app(container=services)) as stranger:
        headers = authenticate(stranger)
        conv = conversation(stranger, headers)
        answer = turn((services, stranger, headers, conv), "да, добавь")
        assert answer["proposal"] is None
        assert stranger.get(f"/api/v1/proposals/{draft['id']}").status_code == 404
    assert cart(grounded)["items"] == []


def test_model_unverified_id_and_fabricated_price_prose_never_surface(grounded):
    planner = RecordingPlanner(intent="product", product_ids=[987654321],
                               clarification="FORGED PRODUCT price 0.01 certificate https://attacker.invalid/fake")
    grounded[0].worker.processor.planner = planner
    answer = turn(grounded, "990100003_")
    assert len(planner.calls) == 1
    assert "unverified_model_product_id" in answer["warnings"]
    assert all(p["id"] != 987654321 for p in answer["products"])
    assert "FORGED PRODUCT" not in answer["message"]
    assert "attacker.invalid" not in str(answer)
    assert answer["products"][0]["price_amount"] == str(grounded[0].catalog.catalog.get_product(900003).price)


def test_planner_alone_cannot_create_cart_proposal(grounded):
    grounded[0].worker.processor.planner = RecordingPlanner(intent="propose", product_ids=[900003],
                                                          items=[{"product_id": 900003, "quantity": 99}])
    answer = turn(grounded, "990100003_ характеристики")
    assert answer["proposal"] is None
    assert cart(grounded)["items"] == []


def test_out_of_contract_model_quantity_requests_clarification_without_failing_turn(grounded):
    # This satisfies the planner's positive-integer schema but exceeds the HTTP/domain bound.
    grounded[0].worker.processor.planner = RecordingPlanner(intent="propose", product_ids=[900003],
                                                          items=[{"product_id": 900003, "quantity": 100001}])
    answer = turn(grounded, "Купить 990100003_ 2 шт")
    assert answer["proposal"] is None
    assert cart(grounded)["items"] == []


def test_conversation_switches_kazakh_then_english_and_retains_choice(grounded):
    kazakh = turn(grounded, "Қазақ тілінде 990100003_")
    assert kazakh["language"] == "kk"
    assert "Бағасы" in kazakh["message"]
    english = turn(grounded, "Please answer in English: 990100003_")
    assert english["language"] == "en"
    assert "Price:" in english["message"]
    assert turn(grounded, "990100003_")["language"] == "en"


def test_kazakh_instruction_to_switch_to_english_is_honored(grounded):
    answer = turn(grounded, "Ағылшын тілінде 990100003_")
    assert answer["language"] == "en"


def test_external_analysis_consent_false_keeps_documents_from_planner(grounded):
    planner = RecordingPlanner()
    grounded[0].worker.processor.planner = planner
    answer = turn(grounded, "Найди позиции из документа", asset_ids=[str(uuid4())], allow_external_analysis=False)
    assert planner.calls == []
    assert grounded[0].documents.image_calls == []
    assert "external_analysis_consent_required" in answer["warnings"]
    assert any(product["id"] == 900003 for product in answer["products"])


@pytest.mark.parametrize("private", ["4111 1111 1111 1111", "CVV: 123", "sk-private_test_secret_123456789"])
def test_payment_or_secret_data_is_rejected_before_persistence(grounded, private):
    services, client, headers, conv = grounded
    response = submit(client, conv, headers, text=private)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sensitive_data_rejected"
    with sqlite3.connect(services.settings.local_db_path) as db:
        assert db.execute("SELECT count(*) FROM turns").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
