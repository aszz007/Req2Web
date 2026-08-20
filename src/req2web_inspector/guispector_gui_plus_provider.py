"""Fail-closed Alibaba GUI Plus adapter for the optional GUISpector sidecar.

GUI Plus returns an XML-wrapped textual tool call instead of the OpenAI
computer-call shape used by GUISpector. This module performs the narrow,
strict conversion without changing Req2Web's canonical generation or
acceptance authorities.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

try:
    from gui_spector.verfication.bigmodel_provider import (
        BigModelProviderError,
        _content_parts,
        _default_post,
        _display_dimensions,
        _validate_finish,
    )
except ModuleNotFoundError as exc:
    if exc.name != "gui_spector":
        raise
    from req2web_inspector.guispector_bigmodel_provider import (
        BigModelProviderError,
        _content_parts,
        _default_post,
        _display_dimensions,
        _validate_finish,
    )


GUI_PLUS_AGENT_ID = "alibaba-gui-plus-2026-02-26"
GUI_PLUS_API_MODEL = "gui-plus-2026-02-26"
GUI_PLUS_API_URL_TEMPLATE = (
    "https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
    "compatible-mode/v1/chat/completions"
)
GUI_PLUS_REQUEST_TIMEOUT_SECONDS = 180
GUI_PLUS_SEED = 20260820

_WORKSPACE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}\Z")
_BEIJING_HOST_PATTERN = re.compile(
    r"(?P<workspace_id>[A-Za-z0-9][A-Za-z0-9-]{0,127})"
    r"\.cn-beijing\.maas\.aliyuncs\.com\Z",
    re.IGNORECASE,
)
_CONNECTION_TEST_IMAGE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAAAAAB5Gfe6AAABN0lEQVR42u3QAQEAAAjDoPcv"
    "PYMIEVjPTYAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAA"
    "AQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAA"
    "AQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAA"
    "AQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAA"
    "AQIECBAgQIAAAQIECBAgQIAAAQIECBAgQIAAAQIECKgDag8O8kgPnOEAAAAASUVORK5CYII="
)
_TOOL_CALL_PATTERN = re.compile(
    r"\s*(?:Action:\s*[^\r\n]+\s*)?"
    r"<tool_call>\s*(\{.*\})\s*</tool_call>\s*",
    re.DOTALL,
)
_SAFE_KEYS = {
    "ALT",
    "BACKSPACE",
    "CTRL",
    "DELETE",
    "DOWN",
    "END",
    "ENTER",
    "ESC",
    "HOME",
    "LEFT",
    "PAGEDOWN",
    "PAGEUP",
    "RIGHT",
    "SHIFT",
    "SPACE",
    "TAB",
    "UP",
} | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


class GuiPlusProviderError(BigModelProviderError):
    """Raised when a GUI Plus request or response fails closed."""


def normalize_gui_plus_workspace_reference(workspace_reference: str) -> str:
    """Return the workspace ID from either an ID or a Beijing API host."""

    normalized = str(workspace_reference or "").strip()
    if _WORKSPACE_ID_PATTERN.fullmatch(normalized):
        return normalized

    candidate = normalized if "://" in normalized else f"https://{normalized}"
    parsed = urlparse(candidate)
    try:
        port = parsed.port
    except ValueError as exc:
        raise GuiPlusProviderError(
            "Alibaba Model Studio Beijing endpoint is not configured or invalid"
        ) from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or port:
        raise GuiPlusProviderError(
            "Alibaba Model Studio Beijing endpoint is not configured or invalid"
        )
    host_match = _BEIJING_HOST_PATTERN.fullmatch(parsed.hostname or "")
    if host_match is None:
        raise GuiPlusProviderError(
            "Alibaba Model Studio Beijing endpoint is not configured or invalid"
        )
    return host_match.group("workspace_id")


def _endpoint(workspace_reference: str) -> str:
    workspace_id = normalize_gui_plus_workspace_reference(workspace_reference)
    return GUI_PLUS_API_URL_TEMPLATE.format(workspace_id=workspace_id)


def _provider_error_detail(response: Any, api_key: str) -> str:
    """Return a bounded provider error without echoing credentials or payloads."""

    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError, AttributeError):
        return ""
    if not isinstance(body, Mapping):
        return ""
    error = body.get("error")
    error_mapping = error if isinstance(error, Mapping) else {}
    code = str(body.get("code") or error_mapping.get("code") or "").strip()
    message = str(
        body.get("message")
        or error_mapping.get("message")
        or (error if isinstance(error, str) else "")
    ).strip()
    parts = [part for part in (code, message) if part]
    if not parts:
        return ""
    detail = redact_gui_plus_secret(": ".join(parts), api_key)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:400]


def _computer_use_tool(width: int, height: int) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": (
                "Use one mouse or keyboard action in the current browser screenshot. "
                f"The exact screenshot resolution is {width}x{height}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "key",
                            "type",
                            "mouse_move",
                            "left_click",
                            "right_click",
                            "middle_click",
                            "double_click",
                            "scroll",
                            "wait",
                        ],
                    },
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "coordinate": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "pixels": {
                        "type": "number",
                        "description": (
                            "Signed vertical distance: negative scrolls down to later "
                            "page content; positive scrolls up to earlier content."
                        ),
                    },
                    "time": {"type": "number"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }


def _finish_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "finish_verification",
            "description": (
                "Finish only after judging every acceptance criterion from visible evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["met", "not_met", "partially_met"],
                    },
                    "explanation": {"type": "string"},
                    "detailed_summary": {"type": "string"},
                    "acceptance_criteria_results": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "criterion_name": {"type": "string"},
                                "met": {"type": "boolean"},
                                "evidence": {"type": "string"},
                            },
                            "required": ["criterion_name", "met", "evidence"],
                            "additionalProperties": False,
                        },
                    },
                    "final_url": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "status",
                    "explanation",
                    "detailed_summary",
                    "acceptance_criteria_results",
                    "final_url",
                ],
                "additionalProperties": False,
            },
        },
    }


def _gui_plus_history_and_screenshot(
    input_items: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Serialize prior actions as prose instead of GUISpector's private schema."""

    task_text = ""
    latest_screenshot = ""
    history: list[str] = []
    for item in input_items:
        item_type = str(item.get("type", ""))
        if item.get("role") == "user":
            texts, images = _content_parts(item.get("content"))
            if texts and not task_text:
                task_text = "\n".join(texts)
            if images:
                latest_screenshot = images[-1]
        elif item_type == "computer_call":
            action = item.get("action")
            if not isinstance(action, Mapping):
                continue
            action_type = str(action.get("type") or "unknown")
            if action_type == "scroll":
                scroll_y = action.get("scroll_y", 0)
                if isinstance(scroll_y, (int, float)) and not isinstance(scroll_y, bool):
                    direction = "down" if scroll_y > 0 else "up"
                    summary = f"scrolled {direction} by {abs(int(scroll_y))} pixels"
                else:
                    summary = "performed a bounded scroll"
            elif action_type in {"click", "double_click", "move"}:
                summary = (
                    f"{action_type.replace('_', ' ')} at "
                    f"({action.get('x')},{action.get('y')})"
                )
            elif action_type == "keypress":
                keys = action.get("keys")
                summary = f"pressed {keys}" if isinstance(keys, list) else "pressed a key"
            elif action_type == "type":
                summary = "typed text into the focused control"
            elif action_type == "wait":
                summary = "waited for the page to settle"
            else:
                summary = f"completed {action_type}"
            history.append(f"{len(history) + 1}. {summary}.")
        elif item_type == "computer_call_output":
            output = item.get("output")
            if isinstance(output, Mapping):
                image_value = output.get("image_url")
                if isinstance(image_value, str) and image_value:
                    latest_screenshot = image_value
                current_url = output.get("current_url")
                if current_url:
                    history.append(f"Current browser URL: {current_url}")
    if not task_text:
        raise GuiPlusProviderError("GUI Plus request has no verification task text")
    if not latest_screenshot:
        raise GuiPlusProviderError("GUI Plus request has no browser screenshot")
    history_text = "\n".join(history) if history else "No browser actions have been taken yet."
    return f"{task_text}\n\nExecuted action history:\n{history_text}", latest_screenshot


def _system_prompt(width: int, height: int) -> str:
    tools = [_computer_use_tool(width, height), _finish_tool()]
    serialized_tools = "\n".join(
        json.dumps(tool, ensure_ascii=False, separators=(",", ":")) for tool in tools
    )
    return f"""# Tools

You are a GUI verification agent. You receive the latest browser screenshot,
the complete acceptance task, and a short prose action history. Judge only the
web page content inside the browser. Ignore browser chrome, private-browsing
notices, operating-system notices, and unrelated tabs. Use only visible evidence.
The screenshot resolution is exactly {width}x{height}; x must be in
[0,{width - 1}] and y must be in [0,{height - 1}].

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{serialized_tools}
</tools>

For every response, output exactly one short Action line followed by exactly
one JSON function call inside <tool_call></tool_call>:
Action: <short imperative>
<tool_call>
{{"name":<function-name>,"arguments":<args-json-object>}}
</tool_call>

Take the shortest evidence path. Do not explore unrelated controls and do not
require every page feature to be exercised when the listed criteria are already
clear. Use computer_use for exactly one next action when more evidence is needed.
If a criterion names an interaction, exercise that interaction once and inspect
its visible feedback. Use finish_verification as soon as every listed criterion
can be judged, echoing each criterion name exactly as provided. Never invent
evidence, never emit more than one tool call, and never repeat an action that has
already produced no visible change twice. A new scroll action uses only action
and pixels, with an optional coordinate. Negative pixels scroll DOWN to later
page content; positive pixels scroll UP to earlier content.
"""


def build_gui_plus_request(
    *,
    input_items: Sequence[Mapping[str, Any]],
    computer_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    task_history, screenshot = _gui_plus_history_and_screenshot(input_items)
    width, height = _display_dimensions(computer_tools)
    return {
        "model": GUI_PLUS_API_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt(width, height)},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": screenshot}},
                    {"type": "text", "text": task_history},
                ],
            },
        ],
        "vl_high_resolution_images": True,
        "enable_thinking": True,
        "seed": GUI_PLUS_SEED,
        "temperature": 0.01,
        "max_tokens": 4096,
        "stream": False,
    }


def _exact_fields(args: Mapping[str, Any], required: set[str]) -> dict[str, Any]:
    normalized = {key: value for key, value in args.items() if value is not None}
    if set(normalized) != required:
        received = ",".join(sorted(normalized)) or "none"
        expected = ",".join(sorted(required))
        raise GuiPlusProviderError(
            "GUI Plus action fields do not match the contract "
            f"(received: {received}; expected: {expected})"
        )
    return normalized


def _coordinate_pair(value: Any, width: int, height: int) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise GuiPlusProviderError("GUI Plus returned an invalid coordinate pair")
    x, y = value
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
        or not 0 <= x < width
        or not 0 <= y < height
    ):
        raise GuiPlusProviderError("GUI Plus returned an out-of-range coordinate")
    return x, y


def _map_computer_use(args: Mapping[str, Any], width: int, height: int) -> dict[str, Any]:
    action_name = args.get("action")
    if not isinstance(action_name, str):
        raise GuiPlusProviderError("GUI Plus returned no browser action")
    if action_name in {"left_click", "right_click", "middle_click"}:
        action = _exact_fields(args, {"action", "coordinate"})
        x, y = _coordinate_pair(action["coordinate"], width, height)
        return {
            "type": "click",
            "x": x,
            "y": y,
            "button": action_name.removesuffix("_click"),
        }
    if action_name in {"double_click", "mouse_move"}:
        action = _exact_fields(args, {"action", "coordinate"})
        x, y = _coordinate_pair(action["coordinate"], width, height)
        runner_name = "double_click" if action_name == "double_click" else "move"
        return {"type": runner_name, "x": x, "y": y}
    if action_name == "type":
        action = _exact_fields(args, {"action", "text"})
        text = action["text"]
        if not isinstance(text, str) or len(text) > 4000:
            raise GuiPlusProviderError("GUI Plus returned invalid typing text")
        return {"type": "type", "text": text}
    if action_name == "key":
        action = _exact_fields(args, {"action", "keys"})
        keys = action["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 4:
            raise GuiPlusProviderError("GUI Plus returned an invalid key combination")
        normalized_keys = [str(key).upper() for key in keys]
        if any(key not in _SAFE_KEYS for key in normalized_keys):
            raise GuiPlusProviderError("GUI Plus returned a disallowed key")
        return {"type": "keypress", "keys": normalized_keys}
    if action_name == "scroll":
        allowed = {key: value for key, value in args.items() if value is not None}
        internal_echo_fields = {"action", "type", "scroll_x", "scroll_y"}
        if set(allowed) == internal_echo_fields:
            if allowed["type"] != "scroll":
                raise GuiPlusProviderError("GUI Plus returned an invalid scroll type")
            scroll_values = (allowed["scroll_x"], allowed["scroll_y"])
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or abs(float(value)) > 5000
                for value in scroll_values
            ):
                raise GuiPlusProviderError("GUI Plus returned an invalid scroll amount")
            scroll_x, scroll_y = (int(round(float(value))) for value in scroll_values)
            if scroll_x == 0 and scroll_y == 0:
                raise GuiPlusProviderError("GUI Plus returned an invalid scroll amount")
            return {
                "type": "scroll",
                "x": width // 2,
                "y": height // 2,
                "scroll_x": scroll_x,
                "scroll_y": scroll_y,
            }
        if set(allowed) not in ({"action", "pixels"}, {"action", "pixels", "coordinate"}):
            received = ",".join(sorted(allowed)) or "none"
            raise GuiPlusProviderError(
                "GUI Plus scroll fields do not match the contract "
                f"(received: {received}; expected: action,pixels[,coordinate] "
                "or the exact GUISpector scroll echo)"
            )
        pixels = allowed.get("pixels")
        if (
            isinstance(pixels, bool)
            or not isinstance(pixels, (int, float))
            or not math.isfinite(float(pixels))
            or float(pixels) == 0
            or abs(float(pixels)) > 5000
        ):
            raise GuiPlusProviderError("GUI Plus returned an invalid scroll amount")
        if "coordinate" in allowed:
            x, y = _coordinate_pair(allowed["coordinate"], width, height)
        else:
            x, y = width // 2, height // 2
        return {
            "type": "scroll",
            "x": x,
            "y": y,
            "scroll_x": 0,
            "scroll_y": -int(round(float(pixels))),
        }
    if action_name == "wait":
        action = _exact_fields(args, {"action", "time"})
        seconds = action["time"]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
        ):
            raise GuiPlusProviderError("GUI Plus returned an invalid wait duration")
        milliseconds = int(round(float(seconds) * 1000))
        if not 100 <= milliseconds <= 5000:
            raise GuiPlusProviderError("GUI Plus returned an invalid wait duration")
        return {"type": "wait", "ms": milliseconds}
    raise GuiPlusProviderError(
        f"GUI Plus returned an unsupported browser action: {action_name!r}"
    )


def _parse_tool_call(content: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(content, str) or content.count("<tool_call>") != 1:
        raise GuiPlusProviderError("GUI Plus must return exactly one tool call")
    match = _TOOL_CALL_PATTERN.fullmatch(content)
    if match is None:
        raise GuiPlusProviderError("GUI Plus returned text outside the tool-call contract")
    try:
        tool_call = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise GuiPlusProviderError("GUI Plus returned invalid tool-call JSON") from exc
    if not isinstance(tool_call, Mapping) or set(tool_call) != {"name", "arguments"}:
        raise GuiPlusProviderError("GUI Plus returned a malformed tool call")
    name = tool_call["name"]
    arguments = tool_call["arguments"]
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        raise GuiPlusProviderError("GUI Plus returned a malformed tool call")
    return name, arguments


def normalize_gui_plus_response(
    response_data: Mapping[str, Any],
    *,
    computer_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    choices = response_data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise GuiPlusProviderError("GUI Plus response has no single completion choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise GuiPlusProviderError("GUI Plus response has no assistant message")
    name, arguments = _parse_tool_call(message.get("content"))
    response_id = str(response_data.get("id") or "gui-plus-response")
    usage = response_data.get("usage") if isinstance(response_data.get("usage"), Mapping) else {}
    normalized_usage = {
        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    if name == "finish_verification":
        try:
            decision = _validate_finish(arguments)
        except BigModelProviderError as exc:
            raise GuiPlusProviderError(str(exc).replace("BigModel", "GUI Plus")) from exc
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(decision, ensure_ascii=False, separators=(",", ":")),
                    }
                ],
            }
        ]
    elif name == "computer_use":
        width, height = _display_dimensions(computer_tools)
        action = _map_computer_use(arguments, width, height)
        output = [
            {
                "type": "computer_call",
                "call_id": f"{response_id}-tool",
                "status": "completed",
                "pending_safety_checks": [],
                "action": action,
            }
        ]
    else:
        raise GuiPlusProviderError("GUI Plus returned an unsupported tool name")
    return {
        "id": response_id,
        "output": output,
        "usage": normalized_usage,
        "provider": "alibaba_model_studio",
        "provider_model": GUI_PLUS_API_MODEL,
        "provider_raw_response": dict(response_data),
    }


def create_gui_plus_response(
    *,
    input_items: Sequence[Mapping[str, Any]],
    computer_tools: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    workspace_id: str | None = None,
    post: Callable[..., Any] = _default_post,
    timeout_seconds: int = GUI_PLUS_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    key = (api_key if api_key is not None else os.getenv("DASHSCOPE_API_KEY", "")).strip()
    if not key:
        raise GuiPlusProviderError("Alibaba Model Studio API key is not configured")
    endpoint = _endpoint(
        workspace_id
        if workspace_id is not None
        else os.getenv("DASHSCOPE_WORKSPACE_ID", "")
    )
    request_data = build_gui_plus_request(
        input_items=input_items,
        computer_tools=computer_tools,
    )
    try:
        response = post(
            endpoint,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request_data,
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError) as exc:
        raise GuiPlusProviderError("GUI Plus request failed before a response was received") from exc
    if getattr(response, "status_code", None) != 200:
        detail = _provider_error_detail(response, key)
        suffix = f": {detail}" if detail else ""
        raise GuiPlusProviderError(
            f"GUI Plus request failed with HTTP "
            f"{getattr(response, 'status_code', 'unknown')}{suffix}"
        )
    try:
        response_data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise GuiPlusProviderError("GUI Plus returned non-JSON content") from exc
    if not isinstance(response_data, Mapping):
        raise GuiPlusProviderError("GUI Plus returned an invalid response object")
    return normalize_gui_plus_response(response_data, computer_tools=computer_tools)


def test_gui_plus_connection(
    api_key: str,
    workspace_id: str,
    *,
    post: Callable[..., Any] = _default_post,
    timeout_seconds: int = 30,
) -> None:
    key = str(api_key or "").strip()
    if not key:
        raise GuiPlusProviderError("Alibaba Model Studio API key is not configured")
    endpoint = _endpoint(workspace_id)
    request_data = {
        "model": GUI_PLUS_API_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt(256, 256)},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _CONNECTION_TEST_IMAGE_DATA_URL
                        },
                    },
                    {"type": "text", "text": "Return one wait action."},
                ],
            },
        ],
        "vl_high_resolution_images": True,
        "enable_thinking": False,
        "temperature": 0.01,
        "max_tokens": 256,
        "stream": False,
    }
    try:
        response = post(
            endpoint,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request_data,
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError) as exc:
        raise GuiPlusProviderError("GUI Plus connection test could not reach the service") from exc
    if getattr(response, "status_code", None) != 200:
        detail = _provider_error_detail(response, key)
        suffix = f": {detail}" if detail else ""
        raise GuiPlusProviderError(
            "GUI Plus connection test failed with HTTP "
            f"{getattr(response, 'status_code', 'unknown')}{suffix}"
        )
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise GuiPlusProviderError("GUI Plus connection test returned non-JSON content") from exc
    if not isinstance(body, Mapping) or not isinstance(body.get("choices"), list) or not body["choices"]:
        raise GuiPlusProviderError("GUI Plus connection test returned no completion")


def redact_gui_plus_secret(text: str, api_key: str | None) -> str:
    """Return text with the exact configured key and bearer tokens removed."""

    redacted = str(text)
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED]")
    return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", redacted)
