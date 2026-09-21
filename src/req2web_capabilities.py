"""Shared executable prototype capabilities; no business-data interpreter."""

CAPABILITY_REVISION = "req2web.prototype_capabilities.v2"
SUPPORTED_COMPONENT_TYPES = (
    "primary_action", "media_input", "search_input", "form", "data_view",
    "location_picker", "status_panel",
)
INTERACTIVE_COMPONENT_TYPES = frozenset(SUPPORTED_COMPONENT_TYPES) - {"status_panel"}


def require_supported_component_type(value: str) -> None:
    if value not in SUPPORTED_COMPONENT_TYPES:
        raise ValueError(f"Unsupported executable component type: {value}")
