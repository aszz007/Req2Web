"""Transport-neutral incremental text events for model generation.

Partial stream events are observational only.  Downstream parsing and delivery
must continue to use the complete persisted raw response.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sys
import time
from typing import Protocol, TextIO

MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION = "req2web.runtime.model_text_stream_event.v1"
MODEL_TEXT_STREAM_MODES = ("off", "console", "jsonl")
_EVENT_KINDS = ("stream_start", "text_delta", "stream_end", "stream_error")


class ModelTextStreamError(ValueError):
    """Raised when stream events or lifecycle calls violate the interface."""


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ModelTextStreamError(f"{field}_invalid")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ModelTextStreamError(f"{field}_invalid")
    return value


@dataclass(frozen=True)
class ModelTextStreamEvent:
    case_id: str
    sequence: int
    event: str
    elapsed_ms: int
    text: str = ""
    schema_version: str = MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION:
            raise ModelTextStreamError("schema_version_invalid")
        _text(self.case_id, "case_id")
        _integer(self.sequence, "sequence")
        _integer(self.elapsed_ms, "elapsed_ms")
        if self.event not in _EVENT_KINDS:
            raise ModelTextStreamError("event_invalid")
        _text(self.text, "text", allow_empty=True)
        if self.event == "stream_start":
            if self.sequence != 0 or self.text:
                raise ModelTextStreamError("stream_start_invalid")
        elif self.event == "text_delta":
            if self.sequence < 1 or not self.text:
                raise ModelTextStreamError("text_delta_invalid")
        elif self.event == "stream_end":
            if self.sequence < 1 or self.text:
                raise ModelTextStreamError("stream_end_invalid")
        elif self.sequence < 1 or not self.text:
            raise ModelTextStreamError("stream_error_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "sequence": self.sequence,
            "event": self.event,
            "elapsed_ms": self.elapsed_ms,
            "text": self.text,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> "ModelTextStreamEvent":
        if type(value) is not dict or set(value) != {
            "schema_version",
            "case_id",
            "sequence",
            "event",
            "elapsed_ms",
            "text",
        }:
            raise ModelTextStreamError("event_shape_invalid")
        event = cls(
            schema_version=value["schema_version"],
            case_id=value["case_id"],
            sequence=value["sequence"],
            event=value["event"],
            elapsed_ms=value["elapsed_ms"],
            text=value["text"],
        )
        event.validate()
        return event


class ModelTextStreamSink(Protocol):
    def emit(self, event: ModelTextStreamEvent) -> None:
        """Consume one validated incremental event."""


class NullModelTextStreamSink:
    def emit(self, event: ModelTextStreamEvent) -> None:
        event.validate()


class ConsoleModelTextStreamSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def emit(self, event: ModelTextStreamEvent) -> None:
        event.validate()
        if event.event == "stream_start":
            self._stream.write(
                f"\n{'=' * 100}\nMODEL OUTPUT BEGIN  case={event.case_id}  "
                "console_preview_non_authoritative=true\n"
                f"{'=' * 100}\n"
            )
        elif event.event == "text_delta":
            self._stream.write(event.text)
        elif event.event == "stream_end":
            self._stream.write(
                f"\n{'=' * 100}\nMODEL OUTPUT END  case={event.case_id}\n"
                f"{'=' * 100}\n"
            )
        else:
            self._stream.write(
                f"\nMODEL OUTPUT ERROR  case={event.case_id}  error={event.text}\n"
            )
        self._stream.flush()


class JsonlModelTextStreamSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def emit(self, event: ModelTextStreamEvent) -> None:
        self._stream.write(event.canonical_bytes().decode("utf-8") + "\n")
        self._stream.flush()


def create_model_text_stream_sink(
    mode: str, *, stream: TextIO | None = None
) -> ModelTextStreamSink:
    if mode == "off":
        return NullModelTextStreamSink()
    if mode == "console":
        return ConsoleModelTextStreamSink(stream)
    if mode == "jsonl":
        return JsonlModelTextStreamSink(stream)
    raise ModelTextStreamError("stream_mode_invalid")


class ModelTextStreamSession:
    def __init__(
        self,
        case_id: str,
        sink: ModelTextStreamSink,
        *,
        clock=time.monotonic,
    ) -> None:
        self._case_id = _text(case_id, "case_id")
        self._sink = sink
        self._clock = clock
        self._started_at: float | None = None
        self._sequence = 0
        self._terminal = False
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._parts)

    @property
    def event_count(self) -> int:
        return self._sequence + (1 if self._started_at is not None else 0)

    @property
    def terminal(self) -> bool:
        return self._terminal

    def _elapsed_ms(self) -> int:
        if self._started_at is None:
            raise ModelTextStreamError("stream_not_started")
        return max(0, int((self._clock() - self._started_at) * 1000))

    def start(self) -> None:
        if self._started_at is not None:
            raise ModelTextStreamError("stream_already_started")
        self._started_at = self._clock()
        self._sink.emit(ModelTextStreamEvent(self._case_id, 0, "stream_start", 0))

    def text_delta(self, text: str) -> None:
        if self._started_at is None or self._terminal:
            raise ModelTextStreamError("stream_not_active")
        _text(text, "text_delta")
        self._sequence += 1
        self._parts.append(text)
        self._sink.emit(
            ModelTextStreamEvent(
                self._case_id,
                self._sequence,
                "text_delta",
                self._elapsed_ms(),
                text,
            )
        )

    def end(self) -> None:
        if self._started_at is None or self._terminal:
            raise ModelTextStreamError("stream_not_active")
        self._sequence += 1
        self._terminal = True
        self._sink.emit(
            ModelTextStreamEvent(
                self._case_id,
                self._sequence,
                "stream_end",
                self._elapsed_ms(),
            )
        )

    def error(self, message: str) -> None:
        if self._started_at is None or self._terminal:
            raise ModelTextStreamError("stream_not_active")
        _text(message, "stream_error")
        self._sequence += 1
        self._terminal = True
        self._sink.emit(
            ModelTextStreamEvent(
                self._case_id,
                self._sequence,
                "stream_error",
                self._elapsed_ms(),
                message,
            )
        )


__all__ = (
    "MODEL_TEXT_STREAM_EVENT_SCHEMA_VERSION",
    "MODEL_TEXT_STREAM_MODES",
    "ConsoleModelTextStreamSink",
    "JsonlModelTextStreamSink",
    "ModelTextStreamError",
    "ModelTextStreamEvent",
    "ModelTextStreamSession",
    "ModelTextStreamSink",
    "NullModelTextStreamSink",
    "create_model_text_stream_sink",
)
