"""The one HTTP client, against a mocked provider.

`local`, `domestic` and `external` must differ by nothing but `AI_BASE_URL`.
The moment one of them needs a special case, three deployments stop being
interchangeable and the spec's promise quietly breaks — so the first test here
compares the three request-for-request.

Everything runs through `httpx.MockTransport`: no socket is opened.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.config import Settings

EXTRACT = {"text": "فیلتر روغن پژو 206 اصلی ایساکو کد 1109AY", "schema_name": "offer_fields"}

GOOD_FIELDS = {
    "fields": {
        "brand": "ایساکو",
        "part_number": "1109AY",
        "part_type": "فیلتر روغن",
        "authenticity_claim": "genuine",
        "pack_quantity": None,
        "vehicle_hints": ["پژو 206"],
    },
    "confidences": {
        "brand": 0.9,
        "part_number": 0.9,
        "part_type": 0.9,
        "authenticity_claim": 0.8,
        "pack_quantity": 0.0,
        "vehicle_hints": 0.8,
    },
}


def chat_response(content: str, *, model: str = "qwen2.5:7b-instruct") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
        },
    )


class Recorder:
    """A mocked provider that remembers what it was asked."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def body(self, index: int = 0) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.requests[index].content)
        return payload


def http_client(
    make_client: Callable[..., TestClient], recorder: Recorder, **overrides: Any
) -> TestClient:
    """A client whose chat backend is the mocked provider.

    `ai_embed_backend="stub"` because these tests are about the chat client:
    a real backend would otherwise resolve embeddings to the local
    sentence-transformer, which is an optional install.
    """
    settings: dict[str, Any] = {
        "ai_backend": "local",
        "ai_base_url": "http://p/v1",
        "ai_embed_backend": "stub",
        **overrides,
    }
    return make_client(transport=recorder.transport, **settings)


@pytest.mark.parametrize("backend", ["local", "domestic", "external"])
def test_every_real_backend_sends_an_identical_request(
    make_client: Callable[..., TestClient], backend: str
) -> None:
    """Acceptance criterion 2 — the base URL is the only difference."""
    recorder = Recorder(chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)))
    client = http_client(
        make_client,
        recorder,
        ai_backend=backend,
        ai_base_url="http://provider.internal/v1",
        ai_api_key="secret",
        ai_model="qwen2.5:7b-instruct",
    )

    assert client.post("/v1/extract", json=EXTRACT).status_code == 200

    request = recorder.requests[0]
    assert str(request.url) == "http://provider.internal/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert recorder.body()["model"] == "qwen2.5:7b-instruct"
    assert recorder.body()["messages"][0]["role"] == "system"
    assert recorder.body()["response_format"] == {"type": "json_object"}


def test_switching_backend_changes_only_the_base_url(
    make_client: Callable[..., TestClient],
) -> None:
    bodies: list[dict[str, Any]] = []
    urls: list[str] = []
    for backend, base_url in (
        ("local", "http://localhost:11434/v1"),
        ("domestic", "https://api.domestic.ir/v1"),
        ("external", "https://api.external.com/v1"),
    ):
        recorder = Recorder(chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)))
        client = http_client(
            make_client, recorder, ai_backend=backend, ai_base_url=base_url, ai_api_key="k"
        )
        client.post("/v1/extract", json=EXTRACT)
        bodies.append(recorder.body())
        urls.append(str(recorder.requests[0].url))

    assert bodies[0] == bodies[1] == bodies[2]
    assert urls == [
        "http://localhost:11434/v1/chat/completions",
        "https://api.domestic.ir/v1/chat/completions",
        "https://api.external.com/v1/chat/completions",
    ]


def test_the_local_backend_defaults_to_ollama_on_the_host(
    make_settings: Callable[..., Settings],
) -> None:
    assert make_settings(ai_backend="local", ai_base_url=None).base_url == (
        "http://localhost:11434/v1"
    )


def test_markdown_fences_are_stripped_before_parsing(
    make_client: Callable[..., TestClient],
) -> None:
    fenced = "```json\n" + json.dumps(GOOD_FIELDS, ensure_ascii=False) + "\n```"
    recorder = Recorder(chat_response(fenced))
    client = http_client(make_client, recorder)

    body = client.post("/v1/extract", json=EXTRACT).json()

    assert body["fields"]["brand"] == "ایساکو"
    assert len(recorder.requests) == 1, "a fenced answer is not a repair case"


def test_malformed_json_triggers_exactly_one_repair_retry_then_422(
    make_client: Callable[..., TestClient], read_metric: Callable[..., float]
) -> None:
    """Acceptance criterion 6."""
    recorder = Recorder(chat_response('{"fields": {broken'))
    client = http_client(make_client, recorder)
    before = read_metric("yadakchi_ai_model_invocations_total", op="extract", backend="local")

    response = client.post("/v1/extract", json=EXTRACT)
    after = read_metric("yadakchi_ai_model_invocations_total", op="extract", backend="local")

    assert response.status_code == 422
    assert response.json()["code"] == "extraction_invalid"
    assert len(recorder.requests) == 2, "one call, one repair retry, and no more"
    assert after - before == 2
    assert "rejected" in recorder.body(1)["messages"][1]["content"]


def test_a_repair_retry_that_succeeds_returns_the_answer(
    make_client: Callable[..., TestClient],
) -> None:
    recorder = Recorder(
        chat_response("sorry, I cannot"),
        chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)),
    )
    client = http_client(make_client, recorder)

    response = client.post("/v1/extract", json=EXTRACT)

    assert response.status_code == 200
    assert response.json()["fields"]["part_number"] == "1109AY"
    assert len(recorder.requests) == 2


def test_a_schema_violation_is_a_repair_case_too(
    make_client: Callable[..., TestClient],
) -> None:
    wrong = {"fields": {"brand": "ایساکو", "authenticity_claim": "very-genuine"}}
    recorder = Recorder(chat_response(json.dumps(wrong, ensure_ascii=False)))
    client = http_client(make_client, recorder)

    response = client.post("/v1/extract", json=EXTRACT)

    assert response.status_code == 422
    assert response.json()["code"] == "extraction_invalid"
    assert len(recorder.requests) == 2


def test_a_judgement_without_a_persian_reason_is_repaired_then_refused(
    make_client: Callable[..., TestClient],
) -> None:
    english = {"is_same": True, "confidence": 0.9, "reason_fa": "same brand and code"}
    recorder = Recorder(chat_response(json.dumps(english)))
    client = http_client(make_client, recorder)

    response = client.post("/v1/judge", json={"a": "لنت پراید", "b": "لنت پراید اصلی"})

    assert response.status_code == 422
    assert response.json()["code"] == "judgement_invalid"
    assert len(recorder.requests) == 2


def test_a_five_hundred_is_retried_then_reported_as_unavailable(
    make_client: Callable[..., TestClient],
) -> None:
    recorder = Recorder(httpx.Response(503, text="overloaded"))
    client = http_client(make_client, recorder, ai_max_attempts=3)

    response = client.post("/v1/extract", json=EXTRACT)

    assert response.status_code == 503
    assert response.json()["code"] == "backend_unavailable"
    assert len(recorder.requests) == 3


def test_a_timeout_is_retried_and_then_recovered_from(
    make_client: Callable[..., TestClient],
) -> None:
    recorder = Recorder(
        httpx.ReadTimeout("too slow"),
        chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)),
    )
    client = http_client(make_client, recorder)

    assert client.post("/v1/extract", json=EXTRACT).status_code == 200
    assert len(recorder.requests) == 2


def test_a_four_hundred_is_not_retried(make_client: Callable[..., TestClient]) -> None:
    recorder = Recorder(httpx.Response(400, text="bad model name"))
    client = http_client(make_client, recorder)

    response = client.post("/v1/extract", json=EXTRACT)

    assert response.status_code == 502
    assert response.json()["code"] == "backend_error"
    assert len(recorder.requests) == 1, "our fault, not theirs — retrying only wastes budget"


def test_json_mode_can_be_turned_off_for_a_provider_that_rejects_it(
    make_client: Callable[..., TestClient],
) -> None:
    recorder = Recorder(chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)))
    client = http_client(make_client, recorder, ai_json_mode=False)
    client.post("/v1/extract", json=EXTRACT)

    assert "response_format" not in recorder.body()


def test_tokens_are_counted_from_the_provider_usage(
    make_client: Callable[..., TestClient], read_metric: Callable[..., float]
) -> None:
    recorder = Recorder(chat_response(json.dumps(GOOD_FIELDS, ensure_ascii=False)))
    client = http_client(make_client, recorder)
    before = read_metric("yadakchi_ai_tokens_total", direction="prompt")

    client.post("/v1/extract", json=EXTRACT)

    assert read_metric("yadakchi_ai_tokens_total", direction="prompt") - before == 120


def test_health_reports_an_unreachable_provider(make_client: Callable[..., TestClient]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = make_client(
        transport=httpx.MockTransport(refuse),
        ai_backend="domestic",
        ai_base_url="https://api.domestic.ir/v1",
        ai_embed_backend="stub",
    )
    body = client.get("/health").json()

    assert body["reachable"] is False
    assert body["status"] == "degraded"
    assert body["backend"] == "domestic"
