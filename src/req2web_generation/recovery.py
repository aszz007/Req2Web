from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorRecoveryScenario:
    """Deterministic UI copy and component targeting for an explicit recovery constraint."""

    kind: str
    preferred_component_types: tuple[str, ...]
    trigger_label: str
    trigger_purpose: str
    trigger_action: str
    error_feedback: str
    recovery_label: str
    recovery_purpose: str
    recovery_action: str
    recovery_feedback: str


_RECOVERY_MARKERS = (
    "\u6062\u590d",
    "\u91cd\u8bd5",
    "\u91cd\u65b0",
    "\u53ef\u64cd\u4f5c",
    "recover",
    "retry",
    "try again",
)
_PERMISSION_ERROR_MARKERS = (
    "\u6743\u9650\u88ab\u62d2\u7edd",
    "\u62d2\u7edd\u6743\u9650",
    "\u76f8\u673a\u6743\u9650",
    "permission denied",
    "camera permission",
)
_INPUT_ERROR_MARKERS = (
    "\u8f93\u5165\u9519\u8bef",
    "\u8f93\u5165\u65e0\u6548",
    "\u65e0\u6548\u8f93\u5165",
    "\u6821\u9a8c\u5931\u8d25",
    "invalid input",
    "validation error",
)
_GENERIC_ERROR_MARKERS = (
    "\u9519\u8bef",
    "\u5f02\u5e38",
    "\u5931\u8d25",
    "\u62d2\u7edd",
    "error",
    "failure",
    "failed",
    "denied",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def match_error_recovery_constraint(
    description: str,
) -> ErrorRecoveryScenario | None:
    """Classify only constraints that explicitly combine an error and recovery intent."""

    normalized = " ".join(description.casefold().split())
    if not normalized or not _contains_any(normalized, _RECOVERY_MARKERS):
        return None

    if _contains_any(normalized, _PERMISSION_ERROR_MARKERS):
        return ErrorRecoveryScenario(
            kind="permission",
            preferred_component_types=("media_input",),
            trigger_label="Simulate camera permission denial",
            trigger_purpose=(
                "Simulate denied camera permission offline without requesting "
                "real browser permission."
            ),
            trigger_action="Simulate camera permission denial",
            error_feedback=(
                "Camera permission was denied. Return to use sample input, or "
                "allow camera access and try again."
            ),
            recovery_label="Return and use sample input",
            recovery_purpose=(
                "Return to an actionable state and continue with offline "
                "sample input."
            ),
            recovery_action="Recover by returning to sample input",
            recovery_feedback=(
                "The page is actionable again. Continue with sample input."
            ),
        )

    if _contains_any(normalized, _INPUT_ERROR_MARKERS):
        return ErrorRecoveryScenario(
            kind="input",
            preferred_component_types=("search_input", "form"),
            trigger_label="Simulate invalid input",
            trigger_purpose=(
                "Simulate invalid input offline to verify the error reason "
                "and recovery action."
            ),
            trigger_action="Simulate invalid input",
            error_feedback=(
                "The input is invalid. Return, update the value, and try again."
            ),
            recovery_label="Update the input and try again",
            recovery_purpose=(
                "Return to an actionable state, correct the input, and rerun "
                "the normal flow."
            ),
            recovery_action="Recover by updating the input",
            recovery_feedback=(
                "The input state is available again. Update the value and "
                "try again."
            ),
        )

    if _contains_any(normalized, _GENERIC_ERROR_MARKERS):
        return ErrorRecoveryScenario(
            kind="generic",
            preferred_component_types=(
                "form",
                "search_input",
                "media_input",
                "primary_action",
                "data_view",
                "location_picker",
            ),
            trigger_label="Simulate task failure",
            trigger_purpose=(
                "Simulate task failure offline to verify a clear reason and "
                "recovery action."
            ),
            trigger_action="Simulate task failure",
            error_feedback=(
                "The task could not be completed. Return, check the input, "
                "and try again."
            ),
            recovery_label="Return and try again",
            recovery_purpose=(
                "Return to an actionable state and rerun the normal flow "
                "after correcting the issue."
            ),
            recovery_action="Recover by returning and trying again",
            recovery_feedback=(
                "The page is actionable again. You can retry the task."
            ),
        )

    return None
