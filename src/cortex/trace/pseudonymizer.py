"""Pseudonymizer interface + rizzo-pii HTTP implementation (issue #112 T2).

The pseudonymizer sits behind a one-method interface so capture code (the
recorder) is sidecar-agnostic: production uses the local rizzo-pii sidecar,
tests inject a fake.

Pinned rizzo-pii wire contract (verified from upstream source @ v2.0.0):

* ``POST {base_url}/analyze`` with JSON ``{"text": "...", "include_mapping": false}``
* 200 → ``{"anonymized_text": "...", "segments": [...], "mapping": {},
  "mapping_enabled": false, "n_chars": N, "n_entities": N, ...}``
* ``include_mapping=false`` keeps real values out of the consumed fields
  (mapping stays server-internal per request); placeholders are stable
  ``[TAG_N]`` within one request and numbering restarts per request.
* ``GET /health`` → 200 when the model is ready, 503 until then.

Any sidecar failure (unreachable, non-2xx, malformed body) raises — the
recorder turns failures into skip + log, never a partial result or raw-text
fallthrough.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class Pseudonymizer(ABC):
    """Abstract pseudonymization service: free text in, ``[TAG_N]`` out."""

    @abstractmethod
    async def anonymize(self, text: str) -> str:
        """Pseudonymize PII-bearing free text.

        Args:
            text: Free text that may contain personal data.

        Returns:
            The same text with PII spans replaced by stable ``[TAG_N]``
            placeholders (numbering restarts per request by sidecar design).

        Raises:
            On any sidecar failure (unreachable, non-2xx, malformed response).
            Implementations never return raw text on failure — the caller
            (TraceRecorder) treats a raise as "do not store this event".
        """
        ...


class RizzoPseudonymizer(Pseudonymizer):
    """HTTP pseudonymizer for a local rizzo-pii sidecar.

    One ``/analyze`` call per field; only ``anonymized_text`` is read back.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the sidecar client.

        Args:
            base_url: Sidecar root, e.g. ``http://127.0.0.1:5005``.
            timeout: Per-request timeout in seconds (httpx applies it per
                connect/read/write/pool step).
            client: Optional pre-built ``httpx.AsyncClient`` (tests inject one
                with a MockTransport); when omitted the impl builds its own
                against ``base_url``.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        if client is None:
            client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._client = client

    async def anonymize(self, text: str) -> str:
        """Pseudonymize ``text`` via ``POST /analyze`` (include_mapping=false).

        An empty string carries no PII and is returned as-is without a
        round-trip. Raises ``httpx.HTTPStatusError`` on non-2xx responses
        (e.g. 503 while the model loads) and httpx transport errors when the
        sidecar is unreachable.
        """
        if not text:
            return text
        response = await self._client.post(
            "/analyze",
            json={"text": text, "include_mapping": False},
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["anonymized_text"])
