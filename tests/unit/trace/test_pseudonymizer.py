"""Tests for the Pseudonymizer interface + rizzo-pii HTTP impl (issue #112 T2).

The contract under test is the pinned rizzo-pii /analyze response shape
(verified from upstream source at tag v2.0.0):

* POST ``{base_url}/analyze`` with JSON ``{"text": ..., "include_mapping": false}``
* 200 → ``{"anonymized_text": "...", "segments": [...], "mapping": {},
  "mapping_enabled": false, "n_chars": N, "n_entities": N, ...}``
* The client reads ONLY ``anonymized_text``; ``include_mapping=false`` keeps
  real values out of every field the client consumes.
* non-200 (503 while the model loads) and network errors propagate — the
  recorder turns them into skip + log, never a partial result.
"""

import json
from abc import ABC
from unittest.mock import patch

import httpx
import pytest

from cortex.trace.pseudonymizer import Pseudonymizer, RizzoPseudonymizer

EMAIL_SEED = "mario.rossi@example.com"


def pinned_analyze_response(text: str) -> dict:
    """A realistic pinned rizzo-pii /analyze body (include_mapping=false)."""
    return {
        "segments": [
            {"t": "Contact "},
            {
                "label": "EMAIL",
                "ph": "[EMAIL_1]",
                "src": "regex",
                "validated": True,
                # mapping_enabled=false -> segments never carry the real value
            },
            {"t": " today"},
        ],
        "anonymized_text": "Contact [EMAIL_1] today",
        "mapping": {},
        "mapping_enabled": False,
        "n_chars": len(text),
        "n_entities": 1,
        "n_unique": 1,
        "by_label": {"EMAIL": 1},
        "by_source": {"regex": 1},
        "excluded_tags": [],
    }


class TestPseudonymizerInterface:
    """The interface is the recorder's seam: one async method, failure = raise."""

    def test_interface_is_abstract(self):
        """Pseudonymizer is abstract and cannot be instantiated directly."""
        assert issubclass(Pseudonymizer, ABC)
        with pytest.raises(TypeError, match="abstract"):
            Pseudonymizer()

    def test_interface_declares_anonymize(self):
        """The seam is `async anonymize(text: str) -> str`."""
        assert callable(getattr(Pseudonymizer, "anonymize"))

    def test_rizzo_impl_is_concrete_subclass(self):
        assert issubclass(RizzoPseudonymizer, Pseudonymizer)


class TestRizzoPseudonymizerContract:
    """Wire contract against the pinned rizzo-pii response shape (fake HTTP)."""

    @pytest.mark.asyncio
    async def test_anonymize_returns_anonymized_text_and_asserts_request(self):
        """POST /analyze carries {"text", "include_mapping": false}; the pinned
        response's anonymized_text is returned (nothing else is read)."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["json"] = json.loads(request.content)
            seen["headers"] = request.headers
            return httpx.Response(200, json=pinned_analyze_response(EMAIL_SEED))

        pseudonymizer = RizzoPseudonymizer(
            base_url="http://127.0.0.1:5005",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://rizzo.test"
            ),
        )

        result = await pseudonymizer.anonymize(f"Contact {EMAIL_SEED} today")

        assert result == "Contact [EMAIL_1] today"
        assert seen["method"] == "POST"
        assert seen["url"] == "http://rizzo.test/analyze"
        # include_mapping=false is asserted on the wire (the acceptance pin).
        assert seen["json"] == {"text": f"Contact {EMAIL_SEED} today", "include_mapping": False}

    @pytest.mark.asyncio
    async def test_503_readiness_raises(self):
        """A 503 (model not ready) is a failure — never a partial/empty result."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"status": "loading"})

        pseudonymizer = RizzoPseudonymizer(
            base_url="http://127.0.0.1:5005",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://rizzo.test"
            ),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await pseudonymizer.anonymize("anything")

    @pytest.mark.asyncio
    async def test_http_500_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        pseudonymizer = RizzoPseudonymizer(
            base_url="http://127.0.0.1:5005",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://rizzo.test"
            ),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await pseudonymizer.anonymize("anything")

    @pytest.mark.asyncio
    async def test_network_error_propagates(self):
        """Unreachable sidecar (connection refused) raises instead of returning
        raw text — raw text must never silently pass through."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        pseudonymizer = RizzoPseudonymizer(
            base_url="http://127.0.0.1:5005",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://rizzo.test"
            ),
        )

        with pytest.raises(httpx.ConnectError):
            await pseudonymizer.anonymize(f"secret {EMAIL_SEED}")

    @pytest.mark.asyncio
    async def test_empty_text_skips_the_sidecar(self):
        """Empty strings carry no PII: no HTTP round-trip, empty string back."""
        hit = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal hit
            hit = True
            return httpx.Response(200, json=pinned_analyze_response(""))

        pseudonymizer = RizzoPseudonymizer(
            base_url="http://127.0.0.1:5005",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://rizzo.test"
            ),
        )

        assert await pseudonymizer.anonymize("") == ""
        assert hit is False

    @pytest.mark.asyncio
    async def test_own_client_built_from_base_url_when_none_injected(self):
        """Without an injected client the impl builds one against base_url
        (production path) — verified by patching httpx.AsyncClient."""
        import cortex.trace.pseudonymizer as pseudonymizer_module

        fake_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=pinned_analyze_response("x"))
            ),
            base_url="http://127.0.0.1:5005",
        )
        with patch.object(
            pseudonymizer_module.httpx, "AsyncClient", return_value=fake_client
        ) as client_cls:
            pseudonymizer = RizzoPseudonymizer(
                base_url="http://127.0.0.1:5005", timeout=3.0
            )
            assert await pseudonymizer.anonymize("hi") == "Contact [EMAIL_1] today"
        client_cls.assert_called_once_with(base_url="http://127.0.0.1:5005", timeout=3.0)
