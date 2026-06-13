"""Lambda authorizer for the PolicyLens HTTP API (POST /ask).

Enforces API-key auth IN CODE so it cannot be skipped: API Gateway will not
invoke the main handler unless this authorizer returns isAuthorized=true. This
is the primary control against an anonymous caller spamming the endpoint and
running up Anthropic / Lambda cost.

Design:
  - HTTP API v2 "REQUEST" authorizer, simple response format (isAuthorized bool).
  - Reads the expected key from AWS Secrets Manager (ARN in API_KEY_SECRET_ARN),
    cached for the container lifetime so we don't call Secrets Manager per request.
  - Compares the caller's `x-api-key` header using a constant-time comparison.
  - Fails CLOSED: any error (missing secret, missing header, boto3 unavailable)
    returns isAuthorized=false. There is no env-var bypass in production — the
    only accepted key is the one in Secrets Manager.

This Lambda is intentionally dependency-light (boto3 only, no torch/ML) so it
ships as a zip and stays cheap/fast on the auth hot path.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_CACHED_KEY: str | None = None


def _load_expected_key() -> str | None:
    """Fetch the expected API key from Secrets Manager once per container.

    Returns None if it cannot be loaded — the caller then denies (fail closed).
    """
    global _CACHED_KEY
    if _CACHED_KEY is not None:
        return _CACHED_KEY

    secret_arn = os.environ.get("API_KEY_SECRET_ARN")
    if not secret_arn:
        logger.error("authorizer_misconfigured reason=no_secret_arn")
        return None

    try:
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=secret_arn)
        _CACHED_KEY = resp["SecretString"]
        return _CACHED_KEY
    except Exception as exc:  # noqa: BLE001
        # Never log the secret or exception detail (may carry ARNs/values).
        logger.error("authorizer_secret_load_failed reason=%s", type(exc).__name__)
        return None


def _extract_api_key(event: dict[str, Any]) -> str:
    """Pull the x-api-key header from an HTTP API v2 authorizer event (lowercased keys)."""
    headers = event.get("headers") or {}
    # HTTP API normalizes header names to lowercase.
    return headers.get("x-api-key", "") or ""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """HTTP API v2 simple-response authorizer. Returns {"isAuthorized": bool}."""
    expected = _load_expected_key()
    presented = _extract_api_key(event)

    # Fail closed on any missing piece; constant-time compare otherwise.
    if not expected or not presented:
        authorized = False
    else:
        authorized = hmac.compare_digest(presented, expected)

    if not authorized:
        logger.info("authz_denied")
    return {"isAuthorized": authorized}
