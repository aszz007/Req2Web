"""Fail-closed BigModel adapter for the optional GUISpector sidecar.

The adapter translates GUISpector's response-item history into one
GLM-4.6V Chat Completions request and translates exactly one structured tool
call back into GUISpector's existing computer-call shape. It is intentionally
independent from Req2Web's canonical generation and acceptance flow.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Mapping, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request


BIGMODEL_AGENT_ID = "zhipu-glm-4.6v"
BIGMODEL_API_MODEL = "glm-4.6v"
BIGMODEL_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
BIGMODEL_REQUEST_TIMEOUT_SECONDS = 180


class BigModelProviderError(RuntimeError):
    """Raised when a BigModel request or response fails closed."""


class _UrllibResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body.decode("utf-8"))


def _default_post(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: int,
    **kwargs: Any,
) -> _UrllibResponse:
    payload = kwargs.get("json")
    if not isinstance(payload, Mapping):
        raise BigModelProviderError("BigModel transport received no JSON request object")
    request = urllib_request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            return _UrllibResponse(int(response.status), response.read())
    except urllib_error.HTTPError as exc:
        return _UrllibResponse(int(exc.code), b"{}")


_KEYPRESS_KEYS = {
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
}


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    required: Sequence[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def bigmodel_tools(
    *,
    display_width: int | None = None,
    display_height: int | None = None,
) -> list[dict[str, Any]]:
    del display_width, display_height
    point = {
        "type": "array",
        "items": {"type": "integer", "minimum": 0, "maximum": 999},
        "minItems": 2,
        "maxItems": 2,
        "description": "GLM desktop coordinate [x, y], normalized to 0-999.",
    }
    element_info = {"type": "string", "maxLength": 300}
    return [
        _tool(
            "left_click",
            "Perform a left click at one visible element using the GLM desktop action format.",
            {"start_box": point, "element_info": element_info},
            ["start_box"],
        ),
        _tool(
            "left_double_click",
            "Perform a left double-click at one visible element.",
            {"start_box": point, "element_info": element_info},
            ["start_box"],
        ),
        _tool(
            "scroll",
            "Scroll a visible region up or down by a small number of wheel steps.",
            {
                "start_box": point,
                "direction": {"type": "string", "enum": ["up", "down"]},
                "step": {"type": "integer", "minimum": 1, "maximum": 10},
                "element_info": element_info,
            },
            ["start_box", "direction", "step"],
        ),
        _tool(
            "type",
            "Type text into the focused control.",
            {"content": {"type": "string", "maxLength": 4000}},
            ["content"],
        ),
        _tool(
            "key",
            "Press one key or a safe key combination.",
            {"keys": {"type": "string", "maxLength": 80}},
            ["keys"],
        ),
        _tool(
            "hover",
            "Move the pointer to one visible point.",
            {"start_box": point, "element_info": element_info},
            ["start_box"],
        ),
        _tool(
            "left_drag",
            "Drag from one visible point to another.",
            {
                "start_box": point,
                "end_box": point,
                "element_info": element_info,
            },
            ["start_box", "end_box"],
        ),
        _tool(
            "wait",
            "Wait briefly for the current page to settle.",
            {"ms": {"type": "integer", "minimum": 100, "maximum": 5000}},
            ["ms"],
        ),
        _tool(
            "finish",
            (
                "Finish verification as soon as the visible evidence is sufficient, "
                "with one decision covering every acceptance criterion."
            ),
            {
                "status": {
                    "type": "string",
                    "enum": ["met", "not_met", "partially_met"],
                },
                "explanation": {"type": "string"},
                "detailed_summary": {"type": "string"},
                "acceptance_criteria_results": {
                    "type": "array",
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
                    "minItems": 1,
                },
                "final_url": {"type": "string"},
                "notes": {"type": "string"},
            },
            [
                "status",
                "explanation",
                "detailed_summary",
                "acceptance_criteria_results",
                "final_url",
            ],
        ),
    ]


def _content_parts(content: Any) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    images: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, Mapping):
                continue
            part_type = str(part.get("type", ""))
            if part_type in {"input_text", "text"} and isinstance(part.get("text"), str):
                texts.append(str(part["text"]))
            if part_type in {"input_image", "image_url"}:
                image_value = part.get("image_url")
                if isinstance(image_value, Mapping):
                    image_value = image_value.get("url")
                if isinstance(image_value, str) and image_value:
                    images.append(image_value)
    return texts, images


def _history_and_screenshot(input_items: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
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
            if isinstance(action, Mapping):
                action_type = str(action.get("type", "action"))
                if action_type == "scroll":
                    amount = action.get("scroll_y", 0)
                    direction = "down" if isinstance(amount, int) and amount > 0 else "up"
                    description = f"Scrolled {direction}."
                elif action_type in {"click", "double_click"}:
                    description = f"Executed a {action_type.replace('_', '-')} on a visible point."
                elif action_type == "type":
                    description = f"Typed {json.dumps(str(action.get('text', '')), ensure_ascii=False)}."
                elif action_type == "keypress":
                    description = "Pressed the requested key or key combination."
                elif action_type == "drag":
                    description = "Dragged across the requested visible path."
                elif action_type == "move":
                    description = "Moved the pointer to a visible point."
                elif action_type == "wait":
                    description = "Waited for the page to settle."
                else:
                    description = f"Executed {action_type}."
                history.append(f"{len(history) + 1}. {description}")
        elif item_type == "computer_call_output":
            output = item.get("output")
            if isinstance(output, Mapping):
                image_value = output.get("image_url")
                if isinstance(image_value, str) and image_value:
                    latest_screenshot = image_value
                current_url = output.get("current_url")
                if current_url:
                    history.append(f"Current URL after the action: {current_url}")
    if not task_text:
        raise BigModelProviderError("BigModel request has no verification task text")
    if not latest_screenshot:
        raise BigModelProviderError("BigModel request has no browser screenshot")
    history_text = "\n".join(history) if history else "No browser actions have been taken yet."
    return f"{task_text}\n\nExecuted action history:\n{history_text}", latest_screenshot


def _display_dimensions(tools: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    for tool in tools:
        if tool.get("type") == "computer-preview":
            width = tool.get("display_width")
            height = tool.get("display_height")
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                return width, height
    raise BigModelProviderError("BigModel request has no valid display dimensions")


def build_bigmodel_request(
    *,
    input_items: Sequence[Mapping[str, Any]],
    computer_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    task_history, screenshot = _history_and_screenshot(input_items)
    width, height = _display_dimensions(computer_tools)
    system_text = (
        "You are a GUI verification agent controlling a browser from screenshots. "
        f"The current screenshot is {width} by {height} pixels, but every tool coordinate "
        "must use GLM normalized screenshot coordinates from 0 through 999: x=0 is the "
        "left edge, x=999 is the right edge, y=0 is the top edge, and y=999 is the "
        "bottom edge. Do not return pixel coordinates. "
        "Use the GLM desktop action fields exactly: start_box is [x, y], scroll uses "
        "direction plus step, type uses content, and key uses a key string. "
        "Judge only the web page content inside the browser; ignore browser chrome, "
        "private-browsing notices, operating-system notices, and unrelated tabs. "
        "Inspect the latest screenshot and call exactly one supplied tool. Take the "
        "shortest evidence path: do not explore unrelated controls, and do not require "
        "every page feature to be exercised when the listed criteria are already clear. "
        "Use one browser action only when more evidence is needed. If a criterion names "
        "an interaction, exercise that interaction once and inspect its visible feedback. "
        "After exercising the named interaction, use at most one directed scroll to the "
        "criterion's stated confirmation location; then call finish instead of exploring "
        "other page features. Never repeat an identical scroll when the screenshot is unchanged. "
        "Use finish as soon as every listed criterion can be judged, echoing each criterion "
        "name exactly as provided. Never invent visual evidence, "
        "never call multiple tools in one response, and never output prose instead of a tool call. "
        "If the same action produces no visible change twice, do not repeat it again; "
        "treat the unchanged state as evidence and finish with the supported decision."
    )
    return {
        "model": BIGMODEL_API_MODEL,
        "messages": [
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": screenshot}},
                    {"type": "text", "text": task_history},
                ],
            },
        ],
        "tools": bigmodel_tools(display_width=width, display_height=height),
        "tool_choice": "auto",
        "thinking": {"type": "enabled"},
        "temperature": 0.1,
        "stream": False,
    }


def _require_exact_keys(
    args: Mapping[str, Any],
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed_optional = optional or set()
    keys = set(args)
    if not required.issubset(keys) or not keys.issubset(required | allowed_optional):
        received = ",".join(sorted(keys)) or "none"
        expected = ",".join(sorted(required | allowed_optional)) or "none"
        raise BigModelProviderError(
            "BigModel tool arguments do not match the action contract "
            f"(received fields: {received}; allowed fields: {expected})"
        )


def _discard_null_extras(
    args: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    """Discard only semantically empty fields outside the declared action shape."""

    allowed = required | (optional or set())
    return {
        key: value
        for key, value in args.items()
        if key in allowed or value is not None
    }


def _coordinate(value: Any, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
        raise BigModelProviderError(
            f"BigModel returned an out-of-range normalized coordinate: {value!r}"
        )
    if not isinstance(limit, int) or limit <= 0:
        raise BigModelProviderError("GUISpector supplied invalid display dimensions")
    return round(value * (limit - 1) / 999)


def _point(value: Any, width: int, height: int) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise BigModelProviderError("BigModel returned an invalid normalized point")
    return _coordinate(value[0], width), _coordinate(value[1], height)


def _validate_action(name: str, args: Mapping[str, Any], width: int, height: int) -> dict[str, Any]:
    if name in {"left_click", "left_double_click", "hover"}:
        action = _discard_null_extras(
            args,
            required={"start_box"},
            optional={"element_info"},
        )
        _require_exact_keys(action, {"start_box"}, {"element_info"})
        x, y = _point(action["start_box"], width, height)
        if name == "left_click":
            return {"type": "click", "x": x, "y": y, "button": "left"}
        if name == "left_double_click":
            return {"type": "double_click", "x": x, "y": y}
        return {"type": "move", "x": x, "y": y}
    elif name == "scroll":
        action = _discard_null_extras(
            args,
            required={"start_box", "direction", "step"},
            optional={"element_info"},
        )
        _require_exact_keys(
            action,
            {"start_box", "direction", "step"},
            {"element_info"},
        )
        x, y = _point(action["start_box"], width, height)
        direction = action["direction"]
        step = action["step"]
        if direction not in {"up", "down"}:
            raise BigModelProviderError("BigModel returned an invalid scroll direction")
        if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= 10:
            raise BigModelProviderError("BigModel returned an invalid scroll step")
        amount = step * 100 * (1 if direction == "down" else -1)
        return {"type": "scroll", "x": x, "y": y, "scroll_x": 0, "scroll_y": amount}
    elif name == "type":
        action = _discard_null_extras(args, required={"content"})
        _require_exact_keys(action, {"content"})
        if not isinstance(action["content"], str) or len(action["content"]) > 4000:
            raise BigModelProviderError("BigModel returned invalid typing text")
        return {"type": "type", "text": action["content"]}
    elif name == "key":
        action = _discard_null_extras(args, required={"keys"})
        _require_exact_keys(action, {"keys"})
        keys = action["keys"]
        if not isinstance(keys, str):
            raise BigModelProviderError("BigModel returned an invalid key combination")
        normalized = [part.strip().upper() for part in keys.split("+") if part.strip()]
        if not 1 <= len(normalized) <= 4:
            raise BigModelProviderError("BigModel returned an invalid key combination")
        if any(key not in _KEYPRESS_KEYS for key in normalized):
            raise BigModelProviderError("BigModel returned a disallowed key")
        return {"type": "keypress", "keys": normalized}
    elif name == "left_drag":
        action = _discard_null_extras(
            args,
            required={"start_box", "end_box"},
            optional={"element_info"},
        )
        _require_exact_keys(action, {"start_box", "end_box"}, {"element_info"})
        start_x, start_y = _point(action["start_box"], width, height)
        end_x, end_y = _point(action["end_box"], width, height)
        return {
            "type": "drag",
            "path": [{"x": start_x, "y": start_y}, {"x": end_x, "y": end_y}],
        }
    elif name == "wait":
        action = _discard_null_extras(args, required={"ms"})
        _require_exact_keys(action, {"ms"})
        value = action["ms"]
        if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 5000:
            raise BigModelProviderError("BigModel returned an invalid wait duration")
        return {"type": "wait", "ms": value}
    else:
        raise BigModelProviderError("BigModel returned an unsupported browser action")


def _validate_finish(args: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "status",
        "explanation",
        "detailed_summary",
        "acceptance_criteria_results",
        "final_url",
    }
    _require_exact_keys(args, required, {"notes"})
    decision = dict(args)
    if decision["status"] not in {"met", "not_met", "partially_met"}:
        raise BigModelProviderError("BigModel returned an invalid final status")
    for key in ("explanation", "detailed_summary", "final_url"):
        if not isinstance(decision[key], str):
            raise BigModelProviderError("BigModel returned an invalid final decision")
    results = decision["acceptance_criteria_results"]
    if not isinstance(results, list) or not results:
        raise BigModelProviderError("BigModel returned no acceptance-criterion results")
    normalized_results: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise BigModelProviderError("BigModel returned an invalid criterion result")
        _require_exact_keys(result, {"criterion_name", "met", "evidence"})
        if not isinstance(result["criterion_name"], str) or not result["criterion_name"].strip():
            raise BigModelProviderError("BigModel returned an unnamed criterion result")
        if not isinstance(result["met"], bool) or not isinstance(result["evidence"], str):
            raise BigModelProviderError("BigModel returned an invalid criterion result")
        normalized_results.append(dict(result))
    decision["acceptance_criteria_results"] = normalized_results
    decision["notes"] = str(decision.get("notes", ""))
    return decision


def normalize_bigmodel_response(
    response_data: Mapping[str, Any],
    *,
    computer_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    choices = response_data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise BigModelProviderError("BigModel response has no single completion choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise BigModelProviderError("BigModel response has no assistant message")
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise BigModelProviderError("BigModel must return exactly one tool call")
    tool_call = tool_calls[0]
    if not isinstance(tool_call, Mapping) or not isinstance(tool_call.get("function"), Mapping):
        raise BigModelProviderError("BigModel returned a malformed tool call")
    function = tool_call["function"]
    name = str(function.get("name", ""))
    raw_arguments = function.get("arguments")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as exc:
        raise BigModelProviderError("BigModel returned invalid tool-call JSON") from exc
    if not isinstance(arguments, Mapping):
        raise BigModelProviderError("BigModel tool arguments are not an object")
    response_id = str(response_data.get("id") or "bigmodel-response")
    call_id = str(tool_call.get("id") or f"{response_id}-tool")
    usage = response_data.get("usage") if isinstance(response_data.get("usage"), Mapping) else {}
    normalized_usage = {
        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    if name == "finish":
        decision = _validate_finish(arguments)
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
    else:
        width, height = _display_dimensions(computer_tools)
        action = _validate_action(name, arguments, width, height)
        output = [
            {
                "type": "computer_call",
                "call_id": call_id,
                "status": "completed",
                "pending_safety_checks": [],
                "action": action,
            }
        ]
    return {
        "id": response_id,
        "output": output,
        "usage": normalized_usage,
        "provider": "zhipu_bigmodel",
        "provider_model": BIGMODEL_API_MODEL,
        "provider_raw_response": dict(response_data),
    }


def create_bigmodel_response(
    *,
    input_items: Sequence[Mapping[str, Any]],
    computer_tools: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    post: Callable[..., Any] = _default_post,
    timeout_seconds: int = BIGMODEL_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    key = (api_key if api_key is not None else os.getenv("ZHIPU_API_KEY", "")).strip()
    if not key:
        raise BigModelProviderError("Zhipu API key is not configured")
    request_data = build_bigmodel_request(input_items=input_items, computer_tools=computer_tools)
    try:
        response = post(
            BIGMODEL_API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request_data,
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError) as exc:
        raise BigModelProviderError("BigModel request failed before a response was received") from exc
    if getattr(response, "status_code", None) != 200:
        raise BigModelProviderError(
            f"BigModel request failed with HTTP {getattr(response, 'status_code', 'unknown')}"
        )
    try:
        response_data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise BigModelProviderError("BigModel returned non-JSON content") from exc
    if not isinstance(response_data, Mapping):
        raise BigModelProviderError("BigModel returned an invalid response object")
    return normalize_bigmodel_response(response_data, computer_tools=computer_tools)


def test_bigmodel_connection(
    api_key: str,
    *,
    post: Callable[..., Any] = _default_post,
    timeout_seconds: int = 30,
) -> None:
    key = str(api_key or "").strip()
    if not key:
        raise BigModelProviderError("Zhipu API key is not configured")
    request_data = {
        "model": BIGMODEL_API_MODEL,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "thinking": {"type": "disabled"},
        "temperature": 0.1,
        "max_tokens": 4,
        "stream": False,
    }
    try:
        response = post(
            BIGMODEL_API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request_data,
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError) as exc:
        raise BigModelProviderError("BigModel connection test could not reach the service") from exc
    if getattr(response, "status_code", None) != 200:
        raise BigModelProviderError(
            f"BigModel connection test failed with HTTP {getattr(response, 'status_code', 'unknown')}"
        )
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise BigModelProviderError("BigModel connection test returned non-JSON content") from exc
    if not isinstance(body, Mapping) or not isinstance(body.get("choices"), list) or not body["choices"]:
        raise BigModelProviderError("BigModel connection test returned no completion")


def redact_bigmodel_secret(text: str, api_key: str | None) -> str:
    """Return text with the exact configured key and bearer tokens removed."""

    redacted = str(text)
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED]")
    return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", redacted)
