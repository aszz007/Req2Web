"""Explicit, non-persistent provider selection for optional GUISpector use."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .guispector_bigmodel_provider import (
    BIGMODEL_API_MODEL,
    BigModelProviderError,
    test_bigmodel_connection,
)
from .guispector_gui_plus_provider import (
    GUI_PLUS_API_MODEL,
    GuiPlusProviderError,
    test_gui_plus_connection,
)


GUISPECTOR_OPTION_SCHEMA_VERSION = "req2web.guispector.option.v1"


class GUISpectorOptionalVerificationError(ValueError):
    """Raised when an optional provider action is unsafe or fails closed."""


_PROVIDERS = {
    "zhipu_bigmodel": {
        "provider_id": "zhipu_bigmodel",
        "display_name": "Zhipu BigModel",
        "model": BIGMODEL_API_MODEL,
        "workspace_id_required": False,
    },
    "alibaba_gui_plus": {
        "provider_id": "alibaba_gui_plus",
        "display_name": "Alibaba GUI Plus",
        "model": GUI_PLUS_API_MODEL,
        "workspace_id_required": True,
    },
}


def optional_provider_capabilities() -> dict[str, Any]:
    """Return public provider metadata without inspecting or exposing secrets."""

    return {
        "schema_version": GUISPECTOR_OPTION_SCHEMA_VERSION,
        "status": "available_but_disabled_by_default",
        "default_enabled": False,
        "credential_persistence": "disabled",
        "automatic_retry_count": 0,
        "providers": [dict(value) for value in _PROVIDERS.values()],
    }


def _safe_error_message(error: Exception, api_key: str) -> str:
    message = str(error).replace(api_key, "[REDACTED]")
    return re.sub(
        r"(?i)Bearer\s+[A-Za-z0-9._-]+",
        "Bearer [REDACTED]",
        message,
    )


def test_optional_provider_connection(
    value: object,
    *,
    bigmodel_test: Callable[[str], None] = test_bigmodel_connection,
    gui_plus_test: Callable[[str, str], None] = test_gui_plus_connection,
) -> dict[str, Any]:
    """Run one explicitly confirmed provider connection test and retain no key."""

    if not isinstance(value, Mapping):
        raise GUISpectorOptionalVerificationError("request body must be an object")
    if value.get("confirm_external_model_action") is not True:
        raise GUISpectorOptionalVerificationError(
            "explicit confirmation is required before an external model call"
        )
    provider_id = str(value.get("provider_id") or "").strip()
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        raise GUISpectorOptionalVerificationError("provider is not supported")
    model = str(value.get("model") or "").strip()
    if model != provider["model"]:
        raise GUISpectorOptionalVerificationError(
            "model must match the selected GUISpector provider profile"
        )
    api_key = str(value.get("api_key") or "").strip()
    if not api_key or len(api_key) > 4096 or any(
        character in api_key for character in "\r\n"
    ):
        raise GUISpectorOptionalVerificationError("API key is not configured or invalid")

    workspace_id = str(value.get("workspace_id") or "").strip()
    try:
        if provider_id == "zhipu_bigmodel":
            bigmodel_test(api_key)
        else:
            if not workspace_id or len(workspace_id) > 512:
                raise GUISpectorOptionalVerificationError(
                    "Alibaba workspace ID or Beijing endpoint is required"
                )
            gui_plus_test(api_key, workspace_id)
    except GUISpectorOptionalVerificationError:
        raise
    except (BigModelProviderError, GuiPlusProviderError, OSError, TimeoutError) as exc:
        raise GUISpectorOptionalVerificationError(
            _safe_error_message(exc, api_key)
        ) from exc

    return {
        "schema_version": GUISPECTOR_OPTION_SCHEMA_VERSION,
        "status": "connection_test_passed",
        "provider_id": provider_id,
        "provider_model": model,
        "external_model_call_count": 1,
        "automatic_retry_count": 0,
        "api_key_persisted": False,
        "guispector_verification_executed": False,
        "canonical_req2web_result_modified": False,
    }


__all__ = [
    "GUISPECTOR_OPTION_SCHEMA_VERSION",
    "GUISpectorOptionalVerificationError",
    "optional_provider_capabilities",
    "test_optional_provider_connection",
]
