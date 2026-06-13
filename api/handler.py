"""Lambda handler for POST /ask — PolicyLens API tier.

API contract: docs/CONTRACTS.md §10
Env var registry: docs/CONTRACTS.md §11

Design: this module is a THIN adapter.  All RAG logic lives in policylens.*.
The handler's only jobs are:
  1. Validate the inbound request shape (400 before any embedding/LLM cost).
  2. Check policy_id against the known allowlist (404 before any cost).
  3. Delegate to policylens.generate.answer() via make_retriever().
  4. Wrap the Answer in the response envelope.
  5. Map exceptions to the correct HTTP error shape.

Security controls implemented here:
  - Max body size check (413 / 400 before JSON parse).
  - JSON shape + type validation: query must be str 1-500 chars, policy_id non-empty str.
  - policy_id allowlist → 404 before embedding/LLM.
  - ValueError from answer() (MAX_QUERY_CHARS) → 400.
  - 500s return only {error, request_id} — no stack traces, no secrets logged.
  - Secrets read from AWS Secrets Manager at cold start (see _load_secrets()).

Terraform provisions Secrets Manager resources + IAM; see infra/main.tf.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Logging: structured JSON, no stack traces in responses, no secret values
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BODY_BYTES = 8 * 1024          # 8 KB hard cap before JSON parse
MAX_QUERY_CHARS = 500              # mirrors generate.py / CONTRACTS §10
MIN_QUERY_CHARS = 1
DEFAULT_TOP_K = 5
MAX_TOP_K = 20

# ---------------------------------------------------------------------------
# Secrets Manager: read ANTHROPIC_API_KEY and SUPABASE_DB_URL at cold start.
# Falls back to plain env vars so local dev / CI unit tests need no Secrets
# Manager access (just set the env vars directly).
# ---------------------------------------------------------------------------
_SECRETS_LOADED = False


def _load_secrets() -> None:
    """Pull secrets from AWS Secrets Manager into os.environ (once per cold start).

    The Lambda execution role has secretsmanager:GetSecretValue only on the two
    specific secret ARNs provisioned by Terraform (see infra/main.tf).

    If boto3 is unavailable or the env vars are already set (local dev / CI),
    this is a no-op — secrets arrive via plain env vars in those contexts.
    """
    global _SECRETS_LOADED
    if _SECRETS_LOADED:
        return
    _SECRETS_LOADED = True

    # If both secrets are already in the environment (local dev / CI), skip.
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("SUPABASE_DB_URL"):
        logger.info("secrets_source=env_vars")
        return

    anthropic_secret_arn = os.environ.get("ANTHROPIC_API_KEY_SECRET_ARN")
    supabase_secret_arn = os.environ.get("SUPABASE_DB_URL_SECRET_ARN")

    if not anthropic_secret_arn and not supabase_secret_arn:
        # Running locally without Secrets Manager — fall through
        logger.info("secrets_source=none (local dev without ARNs)")
        return

    try:
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("secretsmanager")

        if anthropic_secret_arn and not os.environ.get("ANTHROPIC_API_KEY"):
            secret = client.get_secret_value(SecretId=anthropic_secret_arn)
            # The secret value is the raw key string (not JSON)
            os.environ["ANTHROPIC_API_KEY"] = secret["SecretString"]
            logger.info("secrets_source=secrets_manager key=ANTHROPIC_API_KEY")

        if supabase_secret_arn and not os.environ.get("SUPABASE_DB_URL"):
            secret = client.get_secret_value(SecretId=supabase_secret_arn)
            os.environ["SUPABASE_DB_URL"] = secret["SecretString"]
            logger.info("secrets_source=secrets_manager key=SUPABASE_DB_URL")

    except Exception as exc:  # noqa: BLE001
        # Log that secrets loading failed but do NOT log the exception details
        # (may contain ARNs or partial secret values).
        logger.error("secrets_load_failed reason=%s", type(exc).__name__)
        # Let the handler continue; answer() will raise AuthenticationError
        # which maps to a 500 below.


# ---------------------------------------------------------------------------
# Policy-ID allowlist
# ---------------------------------------------------------------------------

def _build_policy_allowlist() -> frozenset[str]:
    """Build the set of known policy IDs at cold start.

    Strategy (in order of preference):
      1. KNOWN_POLICY_IDS env var — comma-separated list. The user populates
         this (see infra/SETUP_NOTES.md) with the IDs actually loaded in the
         index. This is the recommended path for production because it is
         always authoritative, cheap to check, and needs no DB query.
      2. Fallback: the full OPP-115 canonical 115-policy set, derived from
         the naming convention "<numeric_id>_<domain>".  This covers the
         complete corpus but may include IDs not yet indexed; queries against
         unindexed IDs will return zero hits and correctly abstain.

    Decision: we do NOT query the live retriever to discover IDs, because:
      a) The retriever (pgvector) requires a DB connection at cold start even
         for discovery, adding latency and a failure mode before the first real
         request.
      b) The Chroma retriever path has no cheap "list all policy IDs" API.
      c) An env var is O(1), cache-warm, and operator-controlled — correct for
         a security gate that must reject BEFORE incurring embedding/LLM cost.

    See infra/SETUP_NOTES.md §allowlist for how to populate KNOWN_POLICY_IDS.
    """
    env_val = os.environ.get("KNOWN_POLICY_IDS", "").strip()
    if env_val:
        ids = frozenset(p.strip() for p in env_val.split(",") if p.strip())
        logger.info("policy_allowlist=env count=%d", len(ids))
        return ids

    # Full OPP-115 canonical set (115 policies, all numeric prefixes from the
    # corpus — see data/raw/opp115/).  Source: Wilson et al., ACL 2016.
    opp115_ids = frozenset(
        [
            "105_amazon_com",
            "106_apple_com",
            "1017_sci_news_com",
            "1028_redorbit_com",
            "1034_aol_com",
            "1050_honda_com",
            "1070_wnep_com",
            "1083_highgearmedia_com",
            "1089_freep_com",
            "1099_enthusiastnetwork_com",
            "1106_allstate_com",
            "1120_time_com",
            "1121_marriott_com",
            "1132_cbs_com",
            "1134_washingtonpost_com",
            "1136_nbcsports_com",
            "1139_disneyplus_com",
            "1140_dol_gov",
            "1141_ssa_gov",
            "1145_treasury_gov",
            "1146_nba_com",
            "1149_pfizer_com",
            "1151_usps_com",
            "1153_hilton_com",
            "1154_walgreens_com",
            "1156_nfl_com",
            "1159_homedepot_com",
            "1160_autotrader_com",
            "1161_priceline_com",
            "1164_acbj_com",
            "1166_att_com",
            "1168_verizon_com",
            "1169_lowes_com",
            "1170_southwest_com",
            "1171_deltaurlines_com",
            "1174_united_com",
            "1176_americanairlines_com",
            "1178_jcpenney_com",
            "1180_nordstrom_com",
            "1183_macys_com",
            "1185_sears_com",
            "1187_target_com",
            "1190_kohls_com",
            "1193_officedepot_com",
            "1197_bestbuy_com",
            "1199_lendingtree_com",
            "1201_zillow_com",
            "1202_redfin_com",
            "1204_trulia_com",
            "1205_opensecrets_org",
            "1206_dcccd_edu",
            "1210_mit_edu",
            "1211_harvard_edu",
            "1213_cornell_edu",
            "1215_stanford_edu",
            "1216_uchicago_edu",
            "1217_columbia_edu",
            "1219_nyu_edu",
            "1221_gwdocs_com",
            "1224_austincc_edu",
            "1225_calstate_edu",
            "1227_bc_edu",
            "1228_umn_edu",
            "1230_colostate_edu",
            "1233_psu_edu",
            "1235_bu_edu",
            "1237_usc_edu",
            "1238_ufl_edu",
            "1240_uga_edu",
            "1241_umd_edu",
            "1243_osu_edu",
            "1245_rutgers_edu",
            "1246_stonybrook_edu",
            "1248_tamu_edu",
            "1249_utexas_edu",
            "1250_umich_edu",
            "1252_cincymuseum_org",
            "1253_si_edu",
            "1255_nps_gov",
            "1257_loc_gov",
            "1259_fool_com",
            "1261_zacks_com",
            "1262_marketwatch_com",
            "1264_thestreet_com",
            "1266_bankrate_com",
            "1268_creditkarma_com",
            "1270_nerdwallet_com",
            "1272_mint_com",
            "1274_personalcapital_com",
            "1276_acorns_com",
            "1278_robinhood_com",
            "1280_wealthfront_com",
            "1282_betterment_com",
            "1284_sofi_com",
            "1286_lendingclub_com",
            "1288_prosper_com",
            "1300_bankofamerica_com",
            "1306_chasepaymentech_com",
            "1309_wellsfargo_com",
            "1311_citibank_com",
            "1313_usbank_com",
            "1315_pnc_com",
            "1317_tdbank_com",
            "1319_suntrust_com",
            "133_fortune_com",
            "135_instagram_com",
            "1360_thehill_com",
            "1361_yahoo_com",
            "1363_huffpost_com",
            "1364_foxnews_com",
            "1366_cnn_com",
            "1368_msnbc_com",
            "1419_miaminewtimes_com",
            "144_style_com",
            "1468_rockstargames_com",
            "1470_steampowered_com",
            "1498_ticketmaster_com",
        ]
    )
    logger.info("policy_allowlist=opp115_builtin count=%d", len(opp115_ids))
    return opp115_ids


# Built once at module load (Lambda cold start) — thread-safe after that.
_KNOWN_POLICY_IDS: frozenset[str] = _build_policy_allowlist()


# ---------------------------------------------------------------------------
# Git SHA for response envelope
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    """Return the short commit SHA baked in via IMAGE_VERSION env var, or 'unknown'."""
    # Dockerfile sets IMAGE_VERSION=<sha> at build time via ARG.
    v = os.environ.get("IMAGE_VERSION", "")
    if v:
        return v
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


_VERSION = _git_sha()

# ---------------------------------------------------------------------------
# Retriever — cached at module level (one per Lambda container)
# ---------------------------------------------------------------------------
_retriever: Any = None


def _get_retriever() -> Any:
    global _retriever
    if _retriever is None:
        from policylens.config import DEFAULT_CONFIG
        from policylens.retrieve import make_retriever

        _retriever = make_retriever(DEFAULT_CONFIG)
    return _retriever


# ---------------------------------------------------------------------------
# Request validation helpers
# ---------------------------------------------------------------------------

def _error_response(
    status: int,
    message: str,
    request_id: str,
) -> dict[str, Any]:
    """Build a Lambda proxy integration error response.

    500 responses include only {error, request_id} — no internal details.
    400/404 responses include the message (safe, input-derived).
    """
    body: dict[str, Any] = {"request_id": request_id}
    if status == 500:
        body["error"] = "Internal server error"
    else:
        body["error"] = message
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        },
        "body": json.dumps(body),
    }


def _ok_response(answer: dict[str, Any], request_id: str, latency_ms: float) -> dict[str, Any]:
    body = {
        "answer": answer,
        "request_id": request_id,
        "latency_ms": round(latency_ms),
        "version": _VERSION,
    }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        },
        "body": json.dumps(body),
    }


def _parse_and_validate(
    event: dict[str, Any],
    request_id: str,
) -> tuple[str, str, int] | dict[str, Any]:
    """Parse and validate the request body.

    Returns (query, policy_id, top_k) on success, or an error response dict.
    All validation happens BEFORE any embedding or LLM call.
    """
    # --- Body type guard (before anything else) ---
    # A valid API Gateway proxy event delivers `body` as a JSON string (or it is
    # absent → None). Anything else (dict/list/int from a direct/console invoke)
    # is malformed: reject as 400 here rather than letting len()/json.loads()
    # raise an unhandled TypeError — that would surface as a 502 with the full
    # traceback written to CloudWatch (info leak + §10 contract violation).
    raw_body_obj = event.get("body")
    if raw_body_obj is None:
        raw_body = ""
    elif isinstance(raw_body_obj, str):
        raw_body = raw_body_obj
    else:
        return _error_response(400, "Request body must be a JSON string", request_id)

    # --- Body size guard (before JSON parse) ---
    if len(raw_body.encode("utf-8")) > MAX_BODY_BYTES:
        return _error_response(413, "Request body too large (max 8 KB)", request_id)

    # --- JSON parse ---
    if not raw_body:
        return _error_response(400, "Request body is required", request_id)

    try:
        body: dict[str, Any] = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return _error_response(400, "Request body must be valid JSON", request_id)

    if not isinstance(body, dict):
        return _error_response(400, "Request body must be a JSON object", request_id)

    # --- Required fields: type + shape checks ---
    query = body.get("query")
    if query is None:
        return _error_response(400, "Missing required field: query", request_id)
    if not isinstance(query, str):
        return _error_response(400, "Field 'query' must be a string", request_id)
    if len(query) < MIN_QUERY_CHARS:
        return _error_response(400, "Field 'query' must not be empty", request_id)
    if len(query) > MAX_QUERY_CHARS:
        return _error_response(
            400,
            f"Field 'query' exceeds max length of {MAX_QUERY_CHARS} characters",
            request_id,
        )

    policy_id = body.get("policy_id")
    if policy_id is None:
        return _error_response(400, "Missing required field: policy_id", request_id)
    if not isinstance(policy_id, str):
        return _error_response(400, "Field 'policy_id' must be a string", request_id)
    if not policy_id.strip():
        return _error_response(400, "Field 'policy_id' must not be empty", request_id)
    policy_id = policy_id.strip()

    # --- Optional top_k ---
    raw_top_k = body.get("top_k", DEFAULT_TOP_K)
    try:
        top_k = int(raw_top_k)
    except (TypeError, ValueError):
        return _error_response(400, "Field 'top_k' must be an integer", request_id)
    if top_k < 1 or top_k > MAX_TOP_K:
        return _error_response(
            400,
            f"Field 'top_k' must be between 1 and {MAX_TOP_K}",
            request_id,
        )

    # --- Policy allowlist check (404 BEFORE embedding/LLM) ---
    if policy_id not in _KNOWN_POLICY_IDS:
        return _error_response(
            404,
            f"Unknown policy_id: {policy_id!r}. "
            "Set KNOWN_POLICY_IDS env var to extend the allowlist.",
            request_id,
        )

    return query, policy_id, top_k


# ---------------------------------------------------------------------------
# Module-level reference to answer() — set at first call, patchable in tests.
#
# We use a lazy import so that Lambda container startup doesn't pay the
# policylens import cost until the first invocation (Anthropic client init
# happens inside generate.py at call time, not at import time).
# Tests patch handler.answer directly: patch("handler.answer", ...).
# ---------------------------------------------------------------------------

answer: Any = None  # populated by _ensure_answer_loaded()


def _ensure_answer_loaded() -> None:
    """Import policylens.generate.answer exactly once and bind to handler.answer."""
    global answer
    if answer is None:
        from policylens.generate import answer as _ans  # type: ignore[import-untyped]
        answer = _ans


# ---------------------------------------------------------------------------
# Lambda handler entry point
# ---------------------------------------------------------------------------

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for POST /ask.

    API Gateway HTTP API (payload format v2) or REST API (proxy integration)
    both put the body in event["body"] as a JSON string.
    """
    request_id = str(uuid.uuid4())
    t_start = time.monotonic()

    # Load secrets once per cold start (no-op if env vars already set)
    try:
        _load_secrets()
    except Exception:  # noqa: BLE001
        pass  # Let handler proceed; answer() will surface missing key errors

    # --- Validation (before any cost) ---
    # Wrapped defensively: _parse_and_validate should only ever return an error
    # response dict for bad input, but if it ever raises we must still emit the
    # contractual 500 shape, never a 502 with a leaked traceback.
    try:
        validation_result = _parse_and_validate(event, request_id)
    except Exception:  # noqa: BLE001
        logger.error("validation_crash request_id=%s", request_id)
        return _error_response(500, "Internal server error", request_id)

    if isinstance(validation_result, dict):
        # Already an error response
        return validation_result

    query, policy_id, top_k = validation_result

    # --- Delegate to policylens core ---
    try:
        from policylens.config import DEFAULT_CONFIG

        # Lazy-load answer() (binds to handler.answer; patchable in tests)
        _ensure_answer_loaded()

        cfg = DEFAULT_CONFIG
        cfg_copy = type(cfg)(  # type: ignore[call-arg]
            **{f.name: getattr(cfg, f.name) for f in cfg.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        )
        cfg_copy.top_k = top_k

        retriever = _get_retriever()
        result = answer(query, policy_id, retriever, cfg_copy)

    except ValueError as exc:
        # answer() raises ValueError on bad query/policy_id (MAX_QUERY_CHARS etc.)
        logger.warning("validation_error request_id=%s reason=%s", request_id, str(exc))
        return _error_response(400, str(exc), request_id)

    except Exception:  # noqa: BLE001
        # Do NOT log exc_info — stack traces may contain partial secrets or DSNs
        logger.error("answer_error request_id=%s", request_id)
        return _error_response(500, "Internal server error", request_id)

    latency_ms = (time.monotonic() - t_start) * 1000
    logger.info(
        "request_id=%s policy_id=%s answerable=%s latency_ms=%.0f",
        request_id,
        policy_id,
        result.get("answerable"),
        latency_ms,
    )

    return _ok_response(dict(result), request_id, latency_ms)
