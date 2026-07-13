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
    "恢复",
    "重试",
    "重新",
    "可操作",
    "recover",
    "retry",
    "try again",
)
_PERMISSION_ERROR_MARKERS = (
    "权限被拒绝",
    "拒绝权限",
    "相机权限",
    "permission denied",
    "camera permission",
)
_INPUT_ERROR_MARKERS = (
    "输入错误",
    "输入无效",
    "无效输入",
    "校验失败",
    "invalid input",
    "validation error",
)
_GENERIC_ERROR_MARKERS = (
    "错误",
    "异常",
    "失败",
    "拒绝",
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
            trigger_label="模拟相机权限拒绝",
            trigger_purpose="离线模拟相机权限被拒绝，不调用真实浏览器权限。",
            trigger_action="模拟相机权限拒绝",
            error_feedback="相机权限已被拒绝。请返回后改用示例输入，或允许相机访问后重试。",
            recovery_label="返回并改用示例输入",
            recovery_purpose="返回可操作状态，继续使用离线示例输入完成识别。",
            recovery_action="恢复：返回并改用示例输入",
            recovery_feedback="已返回可操作状态，可使用示例输入继续识别。",
        )

    if _contains_any(normalized, _INPUT_ERROR_MARKERS):
        return ErrorRecoveryScenario(
            kind="input",
            preferred_component_types=("search_input", "form"),
            trigger_label="模拟输入错误",
            trigger_purpose="离线模拟无效输入，验证错误原因和恢复入口。",
            trigger_action="模拟输入错误",
            error_feedback="输入内容无效。请返回修改关键词后重新搜索。",
            recovery_label="修改输入并重试",
            recovery_purpose="返回可操作状态，修正输入后重新执行正常流程。",
            recovery_action="恢复：修改输入并重试",
            recovery_feedback="已返回输入状态，请修改关键词后重新搜索。",
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
            trigger_label="模拟任务失败",
            trigger_purpose="离线模拟任务失败，验证明确原因和恢复入口。",
            trigger_action="模拟任务失败",
            error_feedback="任务未能完成。请返回检查输入后重试。",
            recovery_label="返回并重试",
            recovery_purpose="返回可操作状态，修正问题后重新执行正常流程。",
            recovery_action="恢复：返回并重试",
            recovery_feedback="已返回可操作状态，可以重新执行任务。",
        )

    return None
