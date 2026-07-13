from __future__ import annotations

import re
from typing import Iterable, Protocol

from .schema import RequirementUnderstanding, UseCase


class RequirementUnderstandingProvider(Protocol):
    """Replaceable requirement-understanding boundary; no remote service required."""

    def understand(
        self,
        original_requirement: str,
        *,
        target_device: str | None = None,
        task_type: str | None = None,
        constraints: Iterable[str] = (),
    ) -> RequirementUnderstanding: ...


DEVICE_RULES = (
    ("mobile", ("移动端", "手机", " app", "app ")),
    ("responsive_web", ("响应式", "多端", "desktop tablet mobile")),
    ("mobile", ("mobile",)),
    ("tablet", ("平板", "tablet")),
    ("desktop", ("桌面端", "desktop")),
    ("web", ("网页", "网站", "web", "browser")),
)

TASK_RULES = (
    ("ecommerce", ("电商", "商品", "购物车", "结算", "商城", "commerce", "shop")),
    ("location_service", ("地图", "地址", "定位", "location", "map")),
    ("content_platform", ("文章", "媒体", "视频", "内容", "media", "content")),
    ("social_communication", ("聊天", "消息", "社交", "评论", "chat", "message")),
    ("dashboard", ("后台", "管理", "仪表盘", "统计", "dashboard", "admin")),
    ("recognition_tool", ("识别", "检测", "分析", "recognition", "detect", "analyze")),
)

FEATURE_RULES = (
    (
        "登录与身份验证",
        ("登录", "注册", "鉴权", "login", "sign in", "register"),
        "完成身份验证并进入可用状态",
    ),
    (
        "搜索、筛选与查看详情",
        (
            "搜索",
            "筛选",
            "查询",
            "列表",
            "详情",
            "商品",
            "目录",
            "search",
            "filter",
            "list",
            "detail",
            "product",
        ),
        "快速定位候选内容并查看关键信息",
    ),
    (
        "购物车与结算",
        (
            "购物车",
            "加购",
            "结算",
            "支付",
            "下单",
            "cart",
            "basket",
            "checkout",
            "payment",
            "order",
        ),
        "保存待购买项目、完成订单并获得结果反馈",
    ),
    (
        "拍摄或上传素材",
        ("拍照", "上传", "图片", "upload", "camera", "photo"),
        "提交可供系统处理的素材",
    ),
    (
        "执行识别或分析",
        ("识别", "检测", "分析", "recognition", "detect", "analyze"),
        "获得清楚、可理解的分析结果",
    ),
    (
        "查看结果与状态",
        ("展示结果", "查看结果", "结果", "状态", "result", "status"),
        "理解处理结果及下一步操作",
    ),
    (
        "填写并提交表单",
        ("表单", "填写", "提交", "form", "submit", "input"),
        "完成有效输入并收到提交反馈",
    ),
    (
        "查看地图与选择位置",
        ("地图", "地址", "位置", "定位", "map", "address", "location"),
        "找到目标地点并确认位置",
    ),
    (
        "查看数据概览",
        ("仪表盘", "统计", "图表", "dashboard", "chart", "analytics"),
        "快速理解关键指标与状态",
    ),
)

CONSTRAINT_RULES = (
    (("响应式", "多端", "responsive"), "适配不同屏幕尺寸的响应式布局"),
    (("无障碍", "accessibility", "a11y"), "支持基础无障碍访问"),
    (("离线", "offline"), "支持离线或弱网场景"),
    (("权限", "permission"), "处理权限不足与拒绝授权"),
    (("错误", "异常", "失败", "error", "failure"), "提供输入错误和异常状态反馈"),
    (("性能", "快速", "performance"), "保持关键交互响应及时"),
)

DEVICE_LABELS = {
    "responsive_web": "响应式 Web",
    "mobile": "移动端",
    "tablet": "平板端",
    "desktop": "桌面端",
    "web": "Web",
    "unspecified": "未指定设备的",
}

TASK_LABELS = {
    "ecommerce": "电商应用",
    "location_service": "位置服务应用",
    "content_platform": "内容平台",
    "social_communication": "社交沟通应用",
    "dashboard": "数据管理应用",
    "recognition_tool": "识别分析工具",
    "web_application": "交互应用",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _infer(value: str, rules: tuple[tuple[str, tuple[str, ...]], ...], fallback: str) -> str:
    lowered = f" {value.casefold()} "
    for label, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return label
    return fallback


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize(str(value))
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _build_use_cases(requirement: str) -> list[UseCase]:
    lowered = requirement.casefold()
    selected: list[tuple[str, str]] = []
    for title, keywords, outcome in FEATURE_RULES:
        if any(keyword in lowered for keyword in keywords):
            selected.append((title, outcome))
        if len(selected) == 4:
            break

    fallbacks = (
        ("浏览核心功能入口", "找到主要操作入口和当前状态"),
        ("完成核心任务", "完成需求目标并获得明确反馈"),
        ("确认结果或异常", "理解成功、失败和可继续执行的操作"),
    )
    for item in fallbacks:
        if len(selected) >= 2:
            break
        if item not in selected:
            selected.append(item)

    return [
        UseCase(
            use_case_id=f"UC-{index:02d}",
            title=title,
            actor="用户",
            goal=title,
            expected_outcome=outcome,
        )
        for index, (title, outcome) in enumerate(selected[:4], 1)
    ]


class DeterministicRequirementProvider:
    """Rule-based first version that can later be replaced by another provider."""

    def understand(
        self,
        original_requirement: str,
        *,
        target_device: str | None = None,
        task_type: str | None = None,
        constraints: Iterable[str] = (),
    ) -> RequirementUnderstanding:
        requirement = _normalize(original_requirement)
        if not requirement:
            raise ValueError("original_requirement must not be empty")

        inferred_device = target_device or _infer(
            requirement, DEVICE_RULES, "unspecified"
        )
        inferred_task = task_type or _infer(
            requirement, TASK_RULES, "web_application"
        )
        use_cases = _build_use_cases(requirement)

        detected_constraints = [
            normalized
            for keywords, normalized in CONSTRAINT_RULES
            if any(keyword in requirement.casefold() for keyword in keywords)
        ]
        all_constraints = _deduplicate([*constraints, *detected_constraints])
        target_label = DEVICE_LABELS.get(inferred_device, inferred_device)
        task_label = TASK_LABELS.get(inferred_task, inferred_task)
        feature_summary = "、".join(use_case.title for use_case in use_cases)
        short_requirement = requirement[:180] + ("…" if len(requirement) > 180 else "")
        summary = (
            f"构建{target_label}{task_label}，围绕“{short_requirement}”，"
            f"核心流程包括{feature_summary}。"
        )
        return RequirementUnderstanding(
            requirement_summary=summary,
            target_device=inferred_device,
            task_type=inferred_task,
            constraints=all_constraints,
            use_cases=use_cases,
        )
