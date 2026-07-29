from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.model_text_stream import (  # noqa: E402
    MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION,
    ModelTextStreamError,
    ModelTextStreamEvent,
    ModelTextStreamSession,
    create_model_text_stream_sink,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.125
        return current


class ModelTextStreamTests(unittest.TestCase):
    def test_event_round_trip_and_exact_shape(self) -> None:
        event = ModelTextStreamEvent("case-a", 1, "text_delta", 125, "{\"a\":1}")
        parsed = ModelTextStreamEvent.from_dict(json.loads(event.canonical_bytes()))
        self.assertEqual(parsed, event)
        self.assertEqual(parsed.to_dict()["schema_version"], MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION)
        with self.assertRaisesRegex(ModelTextStreamError, "shape"):
            ModelTextStreamEvent.from_dict({**event.to_dict(), "extra": True})

    def test_event_rejects_invalid_lifecycle_shapes(self) -> None:
        invalid = (
            ModelTextStreamEvent("case-a", 1, "stream_start", 0),
            ModelTextStreamEvent("case-a", 0, "text_delta", 0, "x"),
            ModelTextStreamEvent("case-a", 1, "text_delta", 0, ""),
            ModelTextStreamEvent("case-a", 1, "stream_end", 0, "x"),
            ModelTextStreamEvent("case-a", 1, "unknown", 0),
        )
        for event in invalid:
            with self.subTest(event=event.event, sequence=event.sequence):
                with self.assertRaises(ModelTextStreamError):
                    event.validate()

    def test_console_mode_streams_human_readable_text(self) -> None:
        output = io.StringIO()
        session = ModelTextStreamSession(
            "case-a", create_model_text_stream_sink("console", stream=output), clock=_Clock()
        )
        session.start()
        session.text_delta("{\n")
        session.text_delta('  "ok": true\n}')
        session.end()
        rendered = output.getvalue()
        self.assertIn("MODEL OUTPUT BEGIN", rendered)
        self.assertIn('{\n  "ok": true\n}', rendered)
        self.assertIn("MODEL OUTPUT END", rendered)
        self.assertEqual(session.text, '{\n  "ok": true\n}')
        self.assertTrue(session.terminal)
        self.assertEqual(session.event_count, 4)

    def test_jsonl_mode_is_machine_readable_for_future_transport(self) -> None:
        output = io.StringIO()
        session = ModelTextStreamSession(
            "case-a", create_model_text_stream_sink("jsonl", stream=output), clock=_Clock()
        )
        session.start()
        session.text_delta("alpha")
        session.text_delta(" beta")
        session.end()
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["stream_start", "text_delta", "text_delta", "stream_end"])
        self.assertEqual([row["sequence"] for row in rows], [0, 1, 2, 3])
        self.assertEqual("".join(row["text"] for row in rows if row["event"] == "text_delta"), "alpha beta")

    def test_off_mode_and_lifecycle_fail_closed(self) -> None:
        session = ModelTextStreamSession("case-a", create_model_text_stream_sink("off"), clock=_Clock())
        with self.assertRaisesRegex(ModelTextStreamError, "not_active"):
            session.text_delta("x")
        session.start()
        session.error("generation_failed")
        self.assertTrue(session.terminal)
        with self.assertRaisesRegex(ModelTextStreamError, "not_active"):
            session.end()
        with self.assertRaisesRegex(ModelTextStreamError, "stream_mode"):
            create_model_text_stream_sink("websocket")


if __name__ == "__main__":
    unittest.main()
