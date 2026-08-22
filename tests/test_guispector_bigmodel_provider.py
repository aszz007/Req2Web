from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.guispector_bigmodel_provider import (  # noqa: E402
    BIGMODEL_API_MODEL,
    BIGMODEL_API_URL,
    BIGMODEL_BROWSER_ACTION_BUDGET,
    BigModelProviderError,
    build_bigmodel_request,
    create_bigmodel_response,
    normalize_bigmodel_response,
    redact_bigmodel_secret,
    test_bigmodel_connection as check_bigmodel_connection,
)


COMPUTER_TOOLS = [
    {
        "type": "computer-preview",
        "display_width": 1280,
        "display_height": 800,
        "environment": "linux",
    }
]
INPUT_ITEMS = [
    {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Verify AC-1: the page has a Submit button."},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,AAAA",
            },
        ],
    }
]


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _tool_response(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": "completion-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


class BigModelProviderTests(unittest.TestCase):
    def test_request_uses_fixed_model_latest_screenshot_and_one_action_instruction(self) -> None:
        request = build_bigmodel_request(
            input_items=INPUT_ITEMS
            + [
                {
                    "type": "computer_call",
                    "call_id": "call-0",
                    "action": {"type": "click", "x": 10, "y": 20, "button": "left"},
                },
                {
                    "type": "computer_call_output",
                    "call_id": "call-0",
                    "output": {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,BBBB",
                    },
                },
            ],
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(request["model"], BIGMODEL_API_MODEL)
        self.assertFalse(request["stream"])
        self.assertEqual(
            request["messages"][1]["content"][0]["image_url"]["url"],
            "data:image/png;base64,BBBB",
        )
        self.assertIn("call exactly one supplied tool", request["messages"][0]["content"])
        self.assertIn("normalized screenshot coordinates from 0 through 999", request["messages"][0]["content"])
        self.assertIn("Do not return pixel coordinates", request["messages"][0]["content"])
        self.assertIn("do not repeat it again", request["messages"][0]["content"])
        self.assertIn("shortest evidence path", request["messages"][0]["content"])
        self.assertIn(
            "echoing each criterion name exactly",
            request["messages"][0]["content"],
        )
        self.assertIn("use at most one directed scroll", request["messages"][0]["content"])
        self.assertEqual(request["tools"][0]["function"]["name"], "left_click")
        click_properties = request["tools"][0]["function"]["parameters"]["properties"]
        self.assertEqual(click_properties["start_box"]["items"]["maximum"], 999)
        history_text = request["messages"][1]["content"][1]["text"]
        self.assertIn("click", history_text)
        self.assertNotIn('"x":', history_text)
        self.assertNotIn('"y":', history_text)

    def test_action_tool_call_is_normalized_for_existing_runner(self) -> None:
        normalized = normalize_bigmodel_response(
            _tool_response("left_click", {"start_box": [440, 350]}),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(normalized["id"], "completion-1")
        self.assertEqual(
            normalized["output"],
            [
                {
                    "type": "computer_call",
                    "call_id": "call-1",
                    "status": "completed",
                    "pending_safety_checks": [],
                    "action": {"type": "click", "x": 563, "y": 280, "button": "left"},
                }
            ],
        )
        self.assertEqual(normalized["usage"]["input_tokens"], 11)

    def test_action_budget_forces_a_model_decision_without_fabricating_one(self) -> None:
        history: list[dict[str, object]] = []
        for index in range(BIGMODEL_BROWSER_ACTION_BUDGET):
            history.extend(
                [
                    {
                        "type": "computer_call",
                        "call_id": f"call-{index}",
                        "action": {"type": "scroll", "scroll_y": 100},
                    },
                    {
                        "type": "computer_call_output",
                        "call_id": f"call-{index}",
                        "output": {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,SHOT{index}",
                        },
                    },
                ]
            )
        request = build_bigmodel_request(
            input_items=INPUT_ITEMS + history,
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(
            [tool["function"]["name"] for tool in request["tools"]],
            ["finish"],
        )
        self.assertIn("Do not request another browser action", request["messages"][0]["content"])
        self.assertNotIn("met", request["messages"][1]["content"][1]["text"].lower())

    def test_finish_tool_call_becomes_strict_assistant_json(self) -> None:
        normalized = normalize_bigmodel_response(
            _tool_response(
                "finish",
                {
                    "status": "met",
                    "explanation": "The button is visible.",
                    "detailed_summary": "The page shows the required control.",
                    "acceptance_criteria_results": [
                        {"criterion_name": "AC-1", "met": True, "evidence": "Submit is visible."}
                    ],
                    "final_url": "http://req2web-pages/cases/01/package/page/index.html",
                },
            ),
            computer_tools=COMPUTER_TOOLS,
        )
        output = normalized["output"][0]
        self.assertEqual(output["role"], "assistant")
        decision = json.loads(output["content"][0]["text"])
        self.assertEqual(decision["status"], "met")
        self.assertEqual(decision["notes"], "")

    def test_glm_desktop_scroll_contract_maps_to_runner_pixels(self) -> None:
        normalized = normalize_bigmodel_response(
            _tool_response(
                "scroll",
                {"start_box": [500, 500], "direction": "up", "step": 5},
            ),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(
            normalized["output"][0]["action"],
            {
                "type": "scroll",
                "x": 640,
                "y": 400,
                "scroll_x": 0,
                "scroll_y": -500,
            },
        )

    def test_multiple_actions_and_out_of_range_coordinates_fail_closed(self) -> None:
        multiple = _tool_response("left_click", {"start_box": [1, 2]})
        multiple["choices"][0]["message"]["tool_calls"].append(
            multiple["choices"][0]["message"]["tool_calls"][0]
        )
        with self.assertRaisesRegex(BigModelProviderError, "exactly one"):
            normalize_bigmodel_response(multiple, computer_tools=COMPUTER_TOOLS)
        with self.assertRaisesRegex(BigModelProviderError, "out-of-range"):
            normalize_bigmodel_response(
                _tool_response("left_click", {"start_box": [1000, 2]}),
                computer_tools=COMPUTER_TOOLS,
            )

    def test_null_only_extra_field_is_ignored_without_relaxing_non_null_extras(self) -> None:
        normalized = normalize_bigmodel_response(
            _tool_response("left_click", {"start_box": [440, 350], "unexpected": None}),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(
            normalized["output"][0]["action"],
            {"type": "click", "x": 563, "y": 280, "button": "left"},
        )
        with self.assertRaisesRegex(
            BigModelProviderError,
            r"received fields: start_box,unexpected; allowed fields: element_info,start_box",
        ):
            normalize_bigmodel_response(
                _tool_response(
                    "left_click",
                    {"start_box": [440, 350], "unexpected": "left"},
                ),
                computer_tools=COMPUTER_TOOLS,
            )

    def test_disallowed_keypress_cannot_reach_shell_adapter(self) -> None:
        with self.assertRaisesRegex(BigModelProviderError, "disallowed key"):
            normalize_bigmodel_response(
                _tool_response("key", {"keys": "CTRL+x;rm"}),
                computer_tools=COMPUTER_TOOLS,
            )

    def test_missing_key_stops_before_network(self) -> None:
        calls: list[object] = []

        def post(*args: object, **kwargs: object) -> FakeResponse:
            calls.append((args, kwargs))
            return FakeResponse(200, {})

        with self.assertRaisesRegex(BigModelProviderError, "not configured"):
            create_bigmodel_response(
                input_items=INPUT_ITEMS,
                computer_tools=COMPUTER_TOOLS,
                api_key="",
                post=post,
            )
        self.assertEqual(calls, [])

    def test_http_error_is_single_attempt_and_does_not_echo_secret_or_body(self) -> None:
        secret = "test-secret-value"
        calls: list[dict[str, object]] = []

        def post(url: str, **kwargs: object) -> FakeResponse:
            calls.append({"url": url, **kwargs})
            return FakeResponse(401, {"error": f"bad key {secret}"})

        with self.assertRaises(BigModelProviderError) as raised:
            create_bigmodel_response(
                input_items=INPUT_ITEMS,
                computer_tools=COMPUTER_TOOLS,
                api_key=secret,
                post=post,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], BIGMODEL_API_URL)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("bad key", str(raised.exception))

    def test_connection_test_uses_fixed_model_and_redaction_removes_bearer_token(self) -> None:
        captured: dict[str, object] = {}

        def post(url: str, **kwargs: object) -> FakeResponse:
            captured.update({"url": url, **kwargs})
            return FakeResponse(200, {"choices": [{"message": {"content": "OK"}}]})

        check_bigmodel_connection("local-secret", post=post)
        self.assertEqual(captured["json"]["model"], BIGMODEL_API_MODEL)
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            redact_bigmodel_secret(
                "local-secret Authorization: Bearer another-token",
                "local-secret",
            ),
            "[REDACTED] Authorization: Bearer [REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
