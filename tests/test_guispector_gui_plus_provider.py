from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.guispector_gui_plus_provider import (  # noqa: E402
    GUI_PLUS_API_MODEL,
    GUI_PLUS_API_URL_TEMPLATE,
    GUI_PLUS_SEED,
    GuiPlusProviderError,
    build_gui_plus_request,
    create_gui_plus_response,
    normalize_gui_plus_response,
    redact_gui_plus_secret,
    test_gui_plus_connection as check_gui_plus_connection,
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
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
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


def _response(name: str, arguments: dict[str, object], *, prefix: str = "") -> dict[str, object]:
    call = json.dumps({"name": name, "arguments": arguments})
    return {
        "id": "gui-completion-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"{prefix}<tool_call>\n{call}\n</tool_call>",
                }
            }
        ],
        "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
    }


class GuiPlusProviderTests(unittest.TestCase):
    def test_request_uses_official_model_image_mode_dimensions_and_seed(self) -> None:
        request = build_gui_plus_request(
            input_items=INPUT_ITEMS,
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(request["model"], GUI_PLUS_API_MODEL)
        self.assertTrue(request["vl_high_resolution_images"])
        self.assertTrue(request["enable_thinking"])
        self.assertEqual(request["seed"], GUI_PLUS_SEED)
        self.assertFalse(request["stream"])
        self.assertEqual(
            request["messages"][1]["content"][0]["image_url"]["url"],
            "data:image/png;base64,AAAA",
        )
        prompt = request["messages"][0]["content"]
        self.assertIn("exactly 1280x800", prompt)
        self.assertIn('"name":"computer_use"', prompt)
        self.assertIn('"name":"finish_verification"', prompt)

    def test_click_and_scroll_are_normalized_for_existing_runner(self) -> None:
        click = normalize_gui_plus_response(
            _response(
                "computer_use",
                {"action": "right_click", "coordinate": [440, 350]},
                prefix="Action: Open the context menu.\n",
            ),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(
            click["output"][0]["action"],
            {"type": "click", "x": 440, "y": 350, "button": "right"},
        )
        scroll = normalize_gui_plus_response(
            _response("computer_use", {"action": "scroll", "pixels": -600}),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(
            scroll["output"][0]["action"],
            {"type": "scroll", "x": 640, "y": 400, "scroll_x": 0, "scroll_y": 600},
        )
        self.assertEqual(click["usage"]["input_tokens"], 13)

    def test_key_and_wait_are_bounded(self) -> None:
        keypress = normalize_gui_plus_response(
            _response("computer_use", {"action": "key", "keys": ["ctrl", "a"]}),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(
            keypress["output"][0]["action"],
            {"type": "keypress", "keys": ["CTRL", "A"]},
        )
        wait = normalize_gui_plus_response(
            _response("computer_use", {"action": "wait", "time": 1.25}),
            computer_tools=COMPUTER_TOOLS,
        )
        self.assertEqual(wait["output"][0]["action"], {"type": "wait", "ms": 1250})
        with self.assertRaisesRegex(GuiPlusProviderError, "disallowed key"):
            normalize_gui_plus_response(
                _response("computer_use", {"action": "key", "keys": ["x;rm"]}),
                computer_tools=COMPUTER_TOOLS,
            )

    def test_finish_becomes_strict_assistant_json(self) -> None:
        normalized = normalize_gui_plus_response(
            _response(
                "finish_verification",
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

    def test_malformed_multiple_or_out_of_range_actions_fail_closed(self) -> None:
        with self.assertRaisesRegex(GuiPlusProviderError, "exactly one"):
            malformed = _response("computer_use", {"action": "wait", "time": 1})
            malformed["choices"][0]["message"]["content"] += "<tool_call>{}</tool_call>"
            normalize_gui_plus_response(malformed, computer_tools=COMPUTER_TOOLS)
        with self.assertRaisesRegex(GuiPlusProviderError, "outside"):
            normalize_gui_plus_response(
                _response(
                    "computer_use",
                    {"action": "wait", "time": 1},
                    prefix="Uncontracted prose\n",
                ),
                computer_tools=COMPUTER_TOOLS,
            )
        with self.assertRaisesRegex(GuiPlusProviderError, "out-of-range"):
            normalize_gui_plus_response(
                _response(
                    "computer_use",
                    {"action": "left_click", "coordinate": [1280, 20]},
                ),
                computer_tools=COMPUTER_TOOLS,
            )

    def test_missing_credentials_stop_before_network(self) -> None:
        calls: list[object] = []

        def post(*args: object, **kwargs: object) -> FakeResponse:
            calls.append((args, kwargs))
            return FakeResponse(200, {})

        with self.assertRaisesRegex(GuiPlusProviderError, "API key"):
            create_gui_plus_response(
                input_items=INPUT_ITEMS,
                computer_tools=COMPUTER_TOOLS,
                api_key="",
                workspace_id="workspace-1",
                post=post,
            )
        with self.assertRaisesRegex(GuiPlusProviderError, "workspace ID"):
            create_gui_plus_response(
                input_items=INPUT_ITEMS,
                computer_tools=COMPUTER_TOOLS,
                api_key="secret",
                workspace_id="https://unexpected.example",
                post=post,
            )
        self.assertEqual(calls, [])

    def test_http_error_is_one_attempt_and_never_echoes_secret_or_body(self) -> None:
        secret = "test-secret-value"
        calls: list[dict[str, object]] = []

        def post(url: str, **kwargs: object) -> FakeResponse:
            calls.append({"url": url, **kwargs})
            return FakeResponse(401, {"error": f"bad key {secret}"})

        with self.assertRaises(GuiPlusProviderError) as raised:
            create_gui_plus_response(
                input_items=INPUT_ITEMS,
                computer_tools=COMPUTER_TOOLS,
                api_key=secret,
                workspace_id="workspace-1",
                post=post,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["url"],
            GUI_PLUS_API_URL_TEMPLATE.format(workspace_id="workspace-1"),
        )
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("bad key", str(raised.exception))

    def test_connection_test_uses_fixed_model_endpoint_and_redaction(self) -> None:
        captured: dict[str, object] = {}

        def post(url: str, **kwargs: object) -> FakeResponse:
            captured.update({"url": url, **kwargs})
            return FakeResponse(200, {"choices": [{"message": {"content": "OK"}}]})

        check_gui_plus_connection("local-secret", "workspace-1", post=post)
        self.assertEqual(captured["json"]["model"], GUI_PLUS_API_MODEL)
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            redact_gui_plus_secret(
                "local-secret Authorization: Bearer another-token",
                "local-secret",
            ),
            "[REDACTED] Authorization: Bearer [REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
