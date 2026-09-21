from __future__ import annotations

import re
from typing import Iterable, Protocol

from .schema import RequirementUnderstanding, UseCase


class RequirementUnderstandingProvider(Protocol):
    """Replaceable requirement-understanding boundary."""

    def understand(
        self,
        original_requirement: str,
        *,
        target_device: str | None = None,
        task_type: str | None = None,
        constraints: Iterable[str] = (),
    ) -> RequirementUnderstanding: ...


DEVICE_RULES = (
    ("mobile", ("\u79fb\u52a8\u7aef", "\u624b\u673a", " app", "app ")),
    (
        "responsive_web",
        ("\u54cd\u5e94\u5f0f", "\u591a\u7aef", "desktop tablet mobile"),
    ),
    ("mobile", ("mobile",)),
    ("tablet", ("\u5e73\u677f", "tablet")),
    ("desktop", ("\u684c\u9762\u7aef", "desktop")),
    ("web", ("\u7f51\u9875", "\u7f51\u7ad9", "web", "browser")),
)

TASK_RULES = (
    (
        "ecommerce",
        (
            "\u7535\u5546",
            "\u5546\u54c1",
            "\u8d2d\u7269\u8f66",
            "\u7ed3\u7b97",
            "\u5546\u57ce",
            "commerce",
            "shop",
        ),
    ),
    (
        "location_service",
        ("\u5730\u56fe", "\u5730\u5740", "\u5b9a\u4f4d", "location", "map"),
    ),
    (
        "content_platform",
        (
            "\u6587\u7ae0",
            "\u5a92\u4f53",
            "\u89c6\u9891",
            "\u5185\u5bb9",
            "media",
            "content",
        ),
    ),
    (
        "social_communication",
        (
            "\u804a\u5929",
            "\u6d88\u606f",
            "\u793e\u4ea4",
            "\u8bc4\u8bba",
            "chat",
            "message",
        ),
    ),
    (
        "dashboard",
        (
            "\u540e\u53f0",
            "\u7ba1\u7406",
            "\u4eea\u8868\u76d8",
            "\u7edf\u8ba1",
            "dashboard",
            "admin",
        ),
    ),
    (
        "recognition_tool",
        (
            "\u8bc6\u522b",
            "\u68c0\u6d4b",
            "\u5206\u6790",
            "recognition",
            "detect",
            "analyze",
        ),
    ),
)

FEATURE_RULES = (
    (
        "Sign in and authenticate",
        (
            "\u767b\u5f55",
            "\u6ce8\u518c",
            "\u9274\u6743",
            "login",
            "sign in",
            "register",
        ),
        "Complete authentication and enter a usable signed-in state",
    ),
    (
        "Search, filter, and inspect details",
        (
            "\u641c\u7d22",
            "\u7b5b\u9009",
            "\u67e5\u8be2",
            "\u5217\u8868",
            "\u8be6\u60c5",
            "\u5546\u54c1",
            "\u76ee\u5f55",
            "search",
            "filter",
            "list",
            "detail",
            "product",
        ),
        "Locate relevant options quickly and review their key details",
    ),
    (
        "Manage the cart and complete checkout",
        (
            "\u8d2d\u7269\u8f66",
            "\u52a0\u8d2d",
            "\u7ed3\u7b97",
            "\u652f\u4ed8",
            "\u4e0b\u5355",
            "cart",
            "basket",
            "checkout",
            "payment",
            "order",
        ),
        "Keep selected items, complete the order, and receive clear feedback",
    ),
    (
        "Capture or upload media",
        (
            "\u62cd\u7167",
            "\u4e0a\u4f20",
            "\u56fe\u7247",
            "upload",
            "camera",
            "photo",
        ),
        "Provide media that the system can process",
    ),
    (
        "Run recognition or analysis",
        (
            "\u8bc6\u522b",
            "\u68c0\u6d4b",
            "\u5206\u6790",
            "recognition",
            "detect",
            "analyze",
        ),
        "Receive a clear and understandable analysis result",
    ),
    (
        "Review results and status",
        (
            "\u5c55\u793a\u7ed3\u679c",
            "\u67e5\u770b\u7ed3\u679c",
            "\u7ed3\u679c",
            "\u72b6\u6001",
            "result",
            "status",
        ),
        "Understand the result and the next available action",
    ),
    (
        "Complete and submit a form",
        (
            "\u8868\u5355",
            "\u586b\u5199",
            "\u63d0\u4ea4",
            "form",
            "submit",
            "input",
        ),
        "Provide valid input and receive submission feedback",
    ),
    (
        "Review a map and choose a location",
        (
            "\u5730\u56fe",
            "\u5730\u5740",
            "\u4f4d\u7f6e",
            "\u5b9a\u4f4d",
            "map",
            "address",
            "location",
        ),
        "Find the intended place and confirm the selected location",
    ),
    (
        "Review a data overview",
        (
            "\u4eea\u8868\u76d8",
            "\u7edf\u8ba1",
            "\u56fe\u8868",
            "dashboard",
            "chart",
            "analytics",
        ),
        "Understand the key metrics and current status quickly",
    ),
)

CONSTRAINT_RULES = (
    (
        ("\u54cd\u5e94\u5f0f", "\u591a\u7aef", "responsive"),
        "Use a responsive layout across supported screen sizes",
    ),
    (
        ("\u65e0\u969c\u788d", "accessibility", "a11y"),
        "Support baseline accessible operation",
    ),
    (
        ("\u79bb\u7ebf", "offline"),
        "Support offline or weak-network conditions",
    ),
    (
        ("\u6743\u9650", "permission"),
        "Handle missing or denied permissions",
    ),
    (
        (
            "\u9519\u8bef",
            "\u5f02\u5e38",
            "\u5931\u8d25",
            "error",
            "failure",
        ),
        "Show clear input and runtime error feedback",
    ),
    (
        ("\u6027\u80fd", "\u5feb\u901f", "performance"),
        "Keep critical interactions responsive",
    ),
)

DEVICE_LABELS = {
    "responsive_web": "responsive web",
    "mobile": "mobile",
    "tablet": "tablet",
    "desktop": "desktop",
    "web": "web",
    "unspecified": "device-neutral",
}

TASK_LABELS = {
    "ecommerce": "e-commerce application",
    "location_service": "location-service application",
    "content_platform": "content platform",
    "social_communication": "social communication application",
    "dashboard": "data-management application",
    "recognition_tool": "recognition and analysis tool",
    "web_application": "interactive application",
}

_CJK_TEXT = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002fa1f]"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _keyword_present(text: str, keyword: str) -> bool:
    """Match English words, not accidental substrings such as order/ordered."""
    if re.fullmatch(r"[a-zA-Z0-9 _-]+", keyword):
        return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text) is not None
    return keyword in text


def _infer(
    value: str,
    rules: tuple[tuple[str, tuple[str, ...]], ...],
    fallback: str,
) -> str:
    lowered = f" {value.casefold()} "
    for label, keywords in rules:
        if any(_keyword_present(lowered, keyword) for keyword in keywords):
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
        if any(_keyword_present(lowered, keyword) for keyword in keywords):
            selected.append((title, outcome))
        if len(selected) == 4:
            break

    fallbacks = (
        (
            "Review the primary workflow entry points",
            "Find the main actions and understand the current state",
        ),
        (
            "Complete the primary task",
            "Complete the requested goal and receive clear feedback",
        ),
        (
            "Confirm the result or recover from an error",
            "Understand success, failure, and the next available action",
        ),
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
            actor="User",
            goal=title,
            expected_outcome=outcome,
        )
        for index, (title, outcome) in enumerate(selected[:4], 1)
    ]


class DeterministicRequirementProvider:
    """Deterministic English requirement understanding for the active flow."""

    def __init__(
        self,
        *,
        output_language: str = "en",
        strict_english_input: bool = False,
    ) -> None:
        if output_language != "en":
            raise ValueError("the active requirement provider supports English only")
        self.output_language = output_language
        self.strict_english_input = strict_english_input

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

        explicit_constraints = list(constraints)
        if self.strict_english_input:
            publication_inputs = [
                requirement,
                target_device or "",
                task_type or "",
                *explicit_constraints,
            ]
            if any(_CJK_TEXT.search(value) for value in publication_inputs):
                raise ValueError(
                    "English publication understanding requires English inputs"
                )

        inferred_device = target_device or _infer(
            requirement,
            DEVICE_RULES,
            "unspecified",
        )
        inferred_task = task_type or _infer(
            requirement,
            TASK_RULES,
            "web_application",
        )
        use_cases = _build_use_cases(requirement)

        detected_constraints = [
            normalized
            for keywords, normalized in CONSTRAINT_RULES
            if any(keyword in requirement.casefold() for keyword in keywords)
        ]
        all_constraints = _deduplicate(
            [*explicit_constraints, *detected_constraints]
        )
        target_label = DEVICE_LABELS.get(inferred_device, inferred_device)
        task_label = TASK_LABELS.get(inferred_task, inferred_task)
        feature_summary = ", ".join(item.title for item in use_cases)
        if _CJK_TEXT.search(requirement):
            requirement_focus = "the supplied requirement"
        else:
            requirement_focus = requirement[:180]
            if len(requirement) > 180:
                requirement_focus += "..."
        summary = (
            f'Build a {target_label} {task_label} for "{requirement_focus}". '
            f"Core flows include {feature_summary}."
        )
        return RequirementUnderstanding(
            requirement_summary=summary,
            target_device=inferred_device,
            task_type=inferred_task,
            constraints=all_constraints,
            use_cases=use_cases,
        )
