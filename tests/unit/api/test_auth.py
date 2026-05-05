"""Tests for API authentication module."""

from datetime import UTC

import pytest

from cortex.api.auth import _hash_token, generate_token


class TestTokenGeneration:
    """Test token generation and hashing."""

    def test_generate_token_returns_tuple(self):
        """Token generation returns (raw, hashed)."""
        raw, hashed = generate_token()
        assert isinstance(raw, str)
        assert isinstance(hashed, str)
        assert raw.startswith("ctx_")
        assert len(raw) > 30  # URL-safe base64

    def test_generate_token_hashed(self):
        """Hashed token matches SHA-256."""
        raw, hashed = generate_token()
        assert hashed == _hash_token(raw)

    def test_hash_token_deterministic(self):
        """Same token always produces same hash."""
        token = "test_token"
        hash1 = _hash_token(token)
        hash2 = _hash_token(token)
        assert hash1 == hash2

    def test_different_tokens_different_hashes(self):
        """Different tokens produce different hashes."""
        raw1, hashed1 = generate_token()
        raw2, hashed2 = generate_token()
        assert hashed1 != hashed2


class TestTokenSchema:
    """Test token request/response schemas."""

    def test_token_create_request_schema(self):
        """TokenCreateRequest requires name."""
        from cortex.api.schemas import TokenCreateRequest

        req = TokenCreateRequest(name="test-token")
        assert req.name == "test-token"

    def test_token_response_schema(self):
        """TokenResponse contains token and metadata."""
        from datetime import datetime

        from cortex.api.schemas import TokenResponse

        resp = TokenResponse(
            token="ctx_test123",
            name="test-token",
            created_at=datetime.now(UTC),
        )
        assert resp.token == "ctx_test123"
        assert resp.name == "test-token"

    def test_token_list_item_schema(self):
        """TokenListItem excludes secret token."""
        from datetime import datetime
        from uuid import uuid4

        from cortex.api.schemas import TokenListItem

        item = TokenListItem(
            id=uuid4(),
            name="test-token",
            created_at=datetime.now(UTC),
            last_used_at=None,
            revoked=False,
        )
        assert not hasattr(item, "token")


class TestAPIKeyAuth:
    """Test API key authentication."""

    @pytest.mark.asyncio
    async def test_get_api_key_rejects_missing_header(self):
        """Missing Authorization header returns 401."""
        from fastapi import HTTPException

        from cortex.api.auth import get_api_key

        with pytest.raises(HTTPException) as exc_info:
            await get_api_key(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_api_key_rejects_invalid_format(self):
        """Invalid header format returns 401."""
        from fastapi import HTTPException

        from cortex.api.auth import get_api_key

        with pytest.raises(HTTPException) as exc_info:
            await get_api_key("InvalidFormat")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_api_key_rejects_wrong_scheme(self):
        """Wrong auth scheme returns 401."""
        from fastapi import HTTPException

        from cortex.api.auth import get_api_key

        with pytest.raises(HTTPException) as exc_info:
            await get_api_key("Basic token123")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_api_key_rejects_unknown_token(self):
        """Unknown token returns 401."""
        from fastapi import HTTPException

        from cortex.api.auth import get_api_key

        with pytest.raises(HTTPException) as exc_info:
            await get_api_key("Bearer unknown_token_xyz")
        assert exc_info.value.status_code == 401
