from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_provider.semantic_candidate import (
    MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
    ProviderRawResponse,
    parse_provider_raw_response,
)
from req2web_runtime.stateful_constrained_decoding import (
    STATEFUL_CONSTRAINED_DECODING_AUDIT_SCHEMA_VERSION,
    STATEFUL_CONSTRAINED_GENERATION_TREATMENT_ID,
    ConstraintLimits,
    StatefulConstrainedDecodingError,
    create_stateful_constrained_generation,
)


USE_CASE_IDS = ("UC-01", "UC-02")
GENERATION_EOS_TOKEN_ID = 999
MODEL_VOCABULARY_SIZE = 1024
LOCAL_QWEN_ROOT = Path(
    r"D:\Models\Req2Web\Qwen3.5-9B-c202236235762e1c871ad0ccb60c8ee5ba337b9a"
)


class CharacterTokenizer:
    def __init__(self, characters: str | None = None) -> None:
        characters = characters or "".join(chr(value) for value in range(32, 127))
        self._pieces = {
            index: character for index, character in enumerate(dict.fromkeys(characters))
        }
        self._ids = {value: key for key, value in self._pieces.items()}
        self.eos_token_id = 1000
        self.all_special_ids = [self.eos_token_id]

    def get_vocab(self) -> dict[str, int]:
        result = {value: key for key, value in self._pieces.items()}
        result["<eos>"] = self.eos_token_id
        return result

    def batch_decode(
        self,
        values: list[list[int]],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool = False,
    ) -> list[str]:
        return [
            self.decode(
                item,
                skip_special_tokens=skip_special_tokens,
                clean_up_tokenization_spaces=clean_up_tokenization_spaces,
            )
            for item in values
        ]

    def decode(
        self,
        values: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool = False,
    ) -> str:
        pieces: list[str] = []
        for value in values:
            if value == self.eos_token_id:
                if not skip_special_tokens:
                    pieces.append("<eos>")
                continue
            pieces.append(self._pieces[value])
        return "".join(pieces)

    def token_id(self, character: str) -> int:
        return self._ids[character]


class NonComposableCharacterTokenizer(CharacterTokenizer):
    def decode(
        self,
        values: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool = False,
    ) -> str:
        decoded = super().decode(
            values,
            skip_special_tokens=skip_special_tokens,
            clean_up_tokenization_spaces=clean_up_tokenization_spaces,
        )
        if len(values) > 1:
            return decoded + "!"
        return decoded


def candidate_payload() -> dict[str, object]:
    return {
        "schema_version": MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
        "title": "T",
        "layout": {
            "pattern": "P",
            "section_stable_ids": ["sec-main"],
        },
        "sections": [
            {
                "stable_id": "sec-main",
                "title": "S",
                "purpose": "P",
                "component_stable_ids": ["comp-main"],
                "use_case_ids": ["UC-01", "UC-02"],
            }
        ],
        "components": [
            {
                "stable_id": "comp-main",
                "section_stable_id": "sec-main",
                "component_type": "button",
                "label": "Go",
                "purpose": "P",
            }
        ],
        "states": [
            {
                "stable_id": "st-before",
                "name": "Before",
                "description": "Before",
                "visible_component_stable_ids": ["comp-main"],
            },
            {
                "stable_id": "st-after",
                "name": "After",
                "description": "After",
                "visible_component_stable_ids": ["comp-main"],
            },
        ],
        "interactions": [
            {
                "stable_id": "int-go",
                "trigger_component_stable_id": "comp-main",
                "source_state_stable_id": "st-before",
                "action": "Go",
                "target_state_stable_id": "st-after",
                "user_feedback": "Done",
                "use_case_ids": ["UC-01", "UC-02"],
            }
        ],
        "constraints": [],
        "acceptance_checks": [
            {
                "stable_id": "check-done",
                "description": "Complete",
                "use_case_ids": ["UC-01", "UC-02"],
                "state_stable_id": "st-after",
            }
        ],
        "use_case_mappings": [
            {
                "use_case_id": "UC-01",
                "section_stable_ids": ["sec-main"],
                "component_stable_ids": ["comp-main"],
                "interaction_stable_ids": ["int-go"],
            },
            {
                "use_case_id": "UC-02",
                "section_stable_ids": ["sec-main"],
                "component_stable_ids": ["comp-main"],
                "interaction_stable_ids": ["int-go"],
            },
        ],
        "claimed_attribution_edges": [],
    }


def compact(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class StatefulConstrainedDecodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = CharacterTokenizer()
        self.prompt_ids = [self.tokenizer.token_id("P")]

    def controller(self, **kwargs: object):
        return create_stateful_constrained_generation(
            self.tokenizer,
            len(self.prompt_ids),
            USE_CASE_IDS,
            generation_eos_token_id=GENERATION_EOS_TOKEN_ID,
            model_vocabulary_size=MODEL_VOCABULARY_SIZE,
            **kwargs,
        )

    def drive(self, controller, raw: str):
        output_ids = list(self.prompt_ids)
        for character in raw:
            allowed = controller.prefix_allowed_tokens_fn(0, output_ids)
            token_id = self.tokenizer.token_id(character)
            self.assertIn(
                token_id,
                allowed,
                msg=f"character {character!r} was rejected after {len(output_ids) - 1} raw tokens",
            )
            output_ids.append(token_id)
        allowed = controller.prefix_allowed_tokens_fn(0, output_ids)
        self.assertEqual([controller.generation_eos_token_id], allowed)
        output_ids.append(controller.generation_eos_token_id)
        return controller.finalize(output_ids)

    def first_rejection(self, controller, raw: str) -> tuple[int, str]:
        output_ids = list(self.prompt_ids)
        for index, character in enumerate(raw):
            allowed = controller.prefix_allowed_tokens_fn(0, output_ids)
            token_id = self.tokenizer.token_id(character)
            if token_id not in allowed:
                return index, character
            output_ids.append(token_id)
        self.fail("mutated candidate was not rejected")

    def test_valid_candidate_completes_with_unmodified_raw(self) -> None:
        raw = compact(candidate_payload())
        completion = self.drive(self.controller(), raw)

        self.assertTrue(completion.complete)
        self.assertTrue(completion.raw_unmodified)
        self.assertNotEqual(
            self.tokenizer.eos_token_id,
            completion.audit.generation_eos_token_id,
        )
        self.assertEqual(
            GENERATION_EOS_TOKEN_ID,
            completion.audit.generation_eos_token_id,
        )
        self.assertEqual(raw.encode("utf-8"), completion.raw_bytes)
        self.assertEqual(
            raw.encode("utf-8"),
            ProviderRawResponse.from_bytes(completion.raw_bytes).raw_bytes,
        )
        candidate = parse_provider_raw_response(
            ProviderRawResponse.from_bytes(completion.raw_bytes)
        )
        self.assertEqual({"UC-01", "UC-02"}, {
            item.use_case_id for item in candidate.use_case_mappings
        })
        self.assertEqual(
            STATEFUL_CONSTRAINED_DECODING_AUDIT_SCHEMA_VERSION,
            completion.audit.schema_version,
        )
        self.assertEqual(
            STATEFUL_CONSTRAINED_GENERATION_TREATMENT_ID,
            completion.audit.treatment_id,
        )
        self.assertEqual("complete", completion.audit.status)
        self.assertEqual("local_smoke", completion.audit.execution_profile)
        self.assertEqual(
            "stateful_constrained_generation_only",
            completion.audit.success_interpretation,
        )
        self.assertFalse(
            completion.audit.unconstrained_first_pass_model_success_claimed
        )
        self.assertFalse(completion.audit.formal_quality_claimed)
        self.assertFalse(completion.audit.h1_or_gold_used)
        self.assertFalse(completion.audit.raw_repair_performed)
        self.assertIn("title", completion.audit.model_owned_fields)
        self.assertIn(
            "interactions[*].action", completion.audit.model_owned_fields
        )
        for constraint_owned in (
            "json_shape_and_key_order",
            "duplicate_or_illegal_stable_id_exclusion",
            "undefined_reference_exclusion",
            "non_self_loop_exclusion",
            "use_case_and_acceptance_coverage",
            "coverage_forced_array_continuation",
            "use_case_mappings_derivation",
            "collection_string_token_and_elapsed_caps",
        ):
            self.assertIn(
                constraint_owned, completion.audit.constraint_owned_fields
            )
        self.assertEqual(
            (*completion.raw_token_ids, GENERATION_EOS_TOKEN_ID),
            completion.generated_token_ids,
        )
        self.assertGreater(completion.audit.masked_token_observations, 0)
        self.assertTrue(completion.audit.forced_token_positions)
        self.assertTrue(
            all(
                item.generated_position > 0
                for item in completion.audit.forced_token_positions
            )
        )

    def test_multiple_sections_interactions_and_checks_preserve_relations(self) -> None:
        payload = candidate_payload()
        payload["layout"]["section_stable_ids"] = ["sec-one", "sec-two"]  # type: ignore[index]
        payload["sections"] = [
            {
                "stable_id": "sec-one",
                "title": "One",
                "purpose": "P",
                "component_stable_ids": ["comp-one"],
                "use_case_ids": ["UC-01"],
            },
            {
                "stable_id": "sec-two",
                "title": "Two",
                "purpose": "P",
                "component_stable_ids": ["comp-two"],
                "use_case_ids": ["UC-02"],
            },
        ]
        payload["components"] = [
            {
                "stable_id": "comp-one",
                "section_stable_id": "sec-one",
                "component_type": "button",
                "label": "One",
                "purpose": "P",
            },
            {
                "stable_id": "comp-two",
                "section_stable_id": "sec-two",
                "component_type": "button",
                "label": "Two",
                "purpose": "P",
            },
        ]
        payload["states"][0]["visible_component_stable_ids"] = ["comp-one"]  # type: ignore[index]
        payload["states"][1]["visible_component_stable_ids"] = ["comp-two"]  # type: ignore[index]
        payload["interactions"] = [
            {
                "stable_id": "int-one",
                "trigger_component_stable_id": "comp-one",
                "source_state_stable_id": "st-before",
                "action": "One",
                "target_state_stable_id": "st-after",
                "user_feedback": "Done",
                "use_case_ids": ["UC-01"],
            },
            {
                "stable_id": "int-two",
                "trigger_component_stable_id": "comp-two",
                "source_state_stable_id": "st-after",
                "action": "Two",
                "target_state_stable_id": "st-before",
                "user_feedback": "Done",
                "use_case_ids": ["UC-02"],
            },
        ]
        payload["acceptance_checks"] = [
            {
                "stable_id": "check-one",
                "description": "Complete one",
                "use_case_ids": ["UC-01"],
                "state_stable_id": "st-after",
            },
            {
                "stable_id": "check-two",
                "description": "Complete two",
                "use_case_ids": ["UC-02"],
                "state_stable_id": "st-before",
            },
        ]
        payload["use_case_mappings"] = [
            {
                "use_case_id": "UC-01",
                "section_stable_ids": ["sec-one"],
                "component_stable_ids": ["comp-one"],
                "interaction_stable_ids": ["int-one"],
            },
            {
                "use_case_id": "UC-02",
                "section_stable_ids": ["sec-two"],
                "component_stable_ids": ["comp-two"],
                "interaction_stable_ids": ["int-two"],
            },
        ]

        completion = self.drive(self.controller(), compact(payload))

        parsed = parse_provider_raw_response(
            ProviderRawResponse.from_bytes(completion.raw_bytes)
        )
        self.assertEqual(2, len(parsed.sections))
        self.assertEqual(2, len(parsed.components))
        self.assertEqual(2, len(parsed.interactions))
        self.assertEqual(2, len(parsed.acceptance_checks))

    def test_undefined_component_reference_is_masked(self) -> None:
        payload = candidate_payload()
        payload["states"][0]["visible_component_stable_ids"] = ["missing"]  # type: ignore[index]
        raw = compact(payload)

        index, character = self.first_rejection(self.controller(), raw)

        self.assertEqual("m", character)
        self.assertIn('"visible_component_stable_ids":["', raw[: index + 1])

    def test_self_loop_target_is_masked(self) -> None:
        payload = candidate_payload()
        payload["interactions"][0]["target_state_stable_id"] = "st-before"  # type: ignore[index]
        raw = compact(payload)

        index, character = self.first_rejection(self.controller(), raw)

        self.assertEqual("b", character)
        self.assertIn('"target_state_stable_id":"st-b', raw[: index + 1])

    def test_duplicate_global_stable_id_cannot_close(self) -> None:
        payload = candidate_payload()
        payload["states"][1]["stable_id"] = "st-before"  # type: ignore[index]
        raw = compact(payload)

        index, character = self.first_rejection(self.controller(), raw)

        self.assertEqual('"', character)
        self.assertTrue(raw[:index].endswith('"stable_id":"st-before'))

    def test_exact_mapping_is_forced_and_attributed(self) -> None:
        completion = self.drive(self.controller(), compact(candidate_payload()))
        parsed = json.loads(completion.raw_bytes)

        self.assertEqual(
            candidate_payload()["use_case_mappings"],
            parsed["use_case_mappings"],
        )
        self.assertIn(
            "use_case_mappings",
            completion.audit.constraint_owned_fields,
        )
        derived = [
            item
            for item in completion.audit.symbol_table_transitions
            if item.event == "mapping_derived"
        ]
        self.assertEqual(list(USE_CASE_IDS), [item.stable_id for item in derived])

        payload = candidate_payload()
        payload["use_case_mappings"][0]["interaction_stable_ids"] = []  # type: ignore[index]
        index, _ = self.first_rejection(self.controller(), compact(payload))
        self.assertIn('"use_case_mappings":', compact(payload)[: index + 1])

    def test_missing_acceptance_coverage_cannot_end_array(self) -> None:
        payload = candidate_payload()
        payload["acceptance_checks"][0]["use_case_ids"] = ["UC-01"]  # type: ignore[index]
        raw = compact(payload)

        index, character = self.first_rejection(self.controller(), raw)

        self.assertEqual("]", character)
        self.assertIn('"state_stable_id":"st-after"}]', raw[: index + 1])

    def test_prefix_divergence_fails_closed(self) -> None:
        controller = self.controller()
        ids = list(self.prompt_ids)
        raw_prefix = (
            '{"schema_version":"req2web.provider.semantic_candidate.v1","title":"'
        )
        for character in raw_prefix:
            allowed = controller.prefix_allowed_tokens_fn(0, ids)
            token_id = self.tokenizer.token_id(character)
            self.assertIn(token_id, allowed)
            ids.append(token_id)
        base = list(ids)
        allowed = controller.prefix_allowed_tokens_fn(0, base)
        self.assertIn(self.tokenizer.token_id("A"), allowed)
        self.assertIn(self.tokenizer.token_id("B"), allowed)
        controller.prefix_allowed_tokens_fn(
            0, [*base, self.tokenizer.token_id("A")]
        )

        with self.assertRaises(StatefulConstrainedDecodingError) as raised:
            controller.prefix_allowed_tokens_fn(
                0, [*base, self.tokenizer.token_id("B")]
            )
        self.assertEqual("prefix_divergence", raised.exception.code)

    def test_batch_beam_sampling_and_multiple_sequences_are_rejected(self) -> None:
        with self.assertRaises(StatefulConstrainedDecodingError) as batch:
            self.controller(batch_size=2)
        self.assertEqual("unsupported_batch", batch.exception.code)
        with self.assertRaises(StatefulConstrainedDecodingError) as beam:
            self.controller(num_beams=2)
        self.assertEqual("unsupported_beam", beam.exception.code)
        with self.assertRaises(StatefulConstrainedDecodingError) as sampling:
            self.controller(do_sample=True)
        self.assertEqual("unsupported_sampling", sampling.exception.code)
        with self.assertRaises(StatefulConstrainedDecodingError) as returns:
            self.controller(num_return_sequences=2)
        self.assertEqual("unsupported_beam", returns.exception.code)

        controller = self.controller()
        with self.assertRaises(StatefulConstrainedDecodingError) as callback:
            controller.prefix_allowed_tokens_fn(1, self.prompt_ids)
        self.assertEqual("unsupported_batch", callback.exception.code)

    def test_no_legal_token_fails_closed(self) -> None:
        tokenizer = CharacterTokenizer("x")
        controller = create_stateful_constrained_generation(
            tokenizer,
            1,
            USE_CASE_IDS,
            generation_eos_token_id=GENERATION_EOS_TOKEN_ID,
            model_vocabulary_size=MODEL_VOCABULARY_SIZE,
        )

        with self.assertRaises(StatefulConstrainedDecodingError) as raised:
            controller.prefix_allowed_tokens_fn(0, [tokenizer.token_id("x")])
        self.assertEqual("no_legal_token", raised.exception.code)

    def test_tokenizer_eos_is_not_generation_authority(self) -> None:
        tokenizer = CharacterTokenizer()
        tokenizer.eos_token_id = None
        controller = create_stateful_constrained_generation(
            tokenizer,
            1,
            USE_CASE_IDS,
            generation_eos_token_id=GENERATION_EOS_TOKEN_ID,
            model_vocabulary_size=MODEL_VOCABULARY_SIZE,
        )
        self.assertEqual(
            GENERATION_EOS_TOKEN_ID, controller.generation_eos_token_id
        )

    def test_generation_eos_and_model_vocabulary_are_validated(self) -> None:
        invalid_pairs = (
            (-1, MODEL_VOCABULARY_SIZE),
            (MODEL_VOCABULARY_SIZE, MODEL_VOCABULARY_SIZE),
            (GENERATION_EOS_TOKEN_ID, 0),
            (True, MODEL_VOCABULARY_SIZE),
        )
        for generation_eos_token_id, model_vocabulary_size in invalid_pairs:
            with self.subTest(
                generation_eos_token_id=generation_eos_token_id,
                model_vocabulary_size=model_vocabulary_size,
            ):
                with self.assertRaises(
                    StatefulConstrainedDecodingError
                ) as raised:
                    create_stateful_constrained_generation(
                        self.tokenizer,
                        len(self.prompt_ids),
                        USE_CASE_IDS,
                        generation_eos_token_id=generation_eos_token_id,
                        model_vocabulary_size=model_vocabulary_size,
                    )
                self.assertEqual(
                    "invalid_configuration", raised.exception.code
                )

    def test_mask_counts_use_model_vocabulary_and_ids_stay_in_range(self) -> None:
        controller = self.controller()

        allowed = controller.prefix_allowed_tokens_fn(0, self.prompt_ids)
        audit = controller.audit_summary()

        self.assertEqual(MODEL_VOCABULARY_SIZE, audit.model_vocabulary_size)
        self.assertTrue(
            all(0 <= token_id < MODEL_VOCABULARY_SIZE for token_id in allowed)
        )
        self.assertEqual(
            MODEL_VOCABULARY_SIZE - len(set(allowed)),
            audit.masked_token_observations,
        )

    def test_transformers_prefix_processor_passes_one_dimensional_prefix(
        self,
    ) -> None:
        import torch
        from transformers import PrefixConstrainedLogitsProcessor

        controller = self.controller()
        processor = PrefixConstrainedLogitsProcessor(
            controller.prefix_allowed_tokens_fn, num_beams=1
        )
        input_ids = torch.tensor([self.prompt_ids], dtype=torch.long)
        scores = torch.zeros((1, MODEL_VOCABULARY_SIZE))

        processed = processor(input_ids, scores)

        opening_brace = self.tokenizer.token_id("{")
        self.assertTrue(torch.isfinite(processed[0, opening_brace]))
        self.assertTrue(
            torch.isneginf(processed[0, self.tokenizer.token_id("x")])
        )

    def test_model_text_allows_dollar_and_non_ascii_unicode(self) -> None:
        tokenizer = CharacterTokenizer(
            "".join(chr(value) for value in range(32, 127)) + "中文"
        )
        self.tokenizer = tokenizer
        self.prompt_ids = [tokenizer.token_id("P")]
        payload = candidate_payload()
        payload["title"] = "Price $ 中文"
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )

        completion = self.drive(self.controller(), raw)

        parsed = parse_provider_raw_response(
            ProviderRawResponse.from_bytes(completion.raw_bytes)
        )
        self.assertEqual("Price $ 中文", parsed.title)

    def test_all_legal_json_string_escapes_replay_unchanged(self) -> None:
        payload = candidate_payload()
        expected_title = 'A"B\\C/D\bE\fF\nG\rH\tI中'
        payload["title"] = expected_title
        raw = compact(payload).replace("C/D", r"C\/D", 1)

        completion = self.drive(self.controller(), raw)

        parsed = parse_provider_raw_response(
            ProviderRawResponse.from_bytes(completion.raw_bytes)
        )
        self.assertEqual(expected_title, parsed.title)
        self.assertIn(rb"\/", completion.raw_bytes)
        self.assertIn(rb"\u4e2d", completion.raw_bytes)

    def test_invalid_or_incomplete_json_escapes_are_masked(self) -> None:
        invalid_escape = compact(candidate_payload()).replace(
            '"title":"T"', r'"title":"A\xB"', 1
        )
        index, character = self.first_rejection(
            self.controller(), invalid_escape
        )
        self.assertEqual("x", character)
        self.assertTrue(invalid_escape[:index].endswith("\\"))

        incomplete_unicode = compact(candidate_payload()).replace(
            '"title":"T"', r'"title":"A\u12"', 1
        )
        index, character = self.first_rejection(
            self.controller(), incomplete_unicode
        )
        self.assertEqual('"', character)
        self.assertTrue(incomplete_unicode[:index].endswith(r"\u12"))

        tokenizer = CharacterTokenizer(
            "".join(chr(value) for value in range(32, 127)) + "\n"
        )
        self.tokenizer = tokenizer
        self.prompt_ids = [tokenizer.token_id("P")]
        raw_control = compact(candidate_payload()).replace(
            '"title":"T"', '"title":"A\nB"', 1
        )
        _, character = self.first_rejection(self.controller(), raw_control)
        self.assertEqual("\n", character)

    def test_noncompositional_tokenizer_causes_parser_state_drift(self) -> None:
        tokenizer = NonComposableCharacterTokenizer()
        prompt = [tokenizer.token_id("P")]
        controller = create_stateful_constrained_generation(
            tokenizer,
            len(prompt),
            USE_CASE_IDS,
            generation_eos_token_id=GENERATION_EOS_TOKEN_ID,
            model_vocabulary_size=MODEL_VOCABULARY_SIZE,
        )
        ids = list(prompt)
        for character in '{"':
            allowed = controller.prefix_allowed_tokens_fn(0, ids)
            token_id = tokenizer.token_id(character)
            self.assertIn(token_id, allowed)
            ids.append(token_id)

        with self.assertRaises(StatefulConstrainedDecodingError) as raised:
            controller.prefix_allowed_tokens_fn(0, ids)
        self.assertEqual("parser_state_drift", raised.exception.code)

    def test_generated_token_cap_fails_closed(self) -> None:
        controller = self.controller(
            limits=ConstraintLimits(max_generated_tokens=1)
        )
        ids = list(self.prompt_ids)
        for character in '{"':
            allowed = controller.prefix_allowed_tokens_fn(0, ids)
            token_id = self.tokenizer.token_id(character)
            self.assertIn(token_id, allowed)
            ids.append(token_id)

        with self.assertRaises(StatefulConstrainedDecodingError) as raised:
            controller.prefix_allowed_tokens_fn(0, ids)
        self.assertEqual("resource_cap_exceeded", raised.exception.code)

    def test_elapsed_time_cap_fails_closed(self) -> None:
        with patch(
            "req2web_runtime.stateful_constrained_decoding.time.monotonic",
            side_effect=(10.0, 12.0),
        ):
            controller = self.controller(
                limits=ConstraintLimits(max_elapsed_seconds=1.0)
            )
            with self.assertRaises(StatefulConstrainedDecodingError) as raised:
                controller.prefix_allowed_tokens_fn(0, self.prompt_ids)
        self.assertEqual("resource_cap_exceeded", raised.exception.code)

    def test_complete_prefix_allows_only_eos(self) -> None:
        raw = compact(candidate_payload())
        controller = self.controller()
        output_ids = list(self.prompt_ids)
        for character in raw:
            allowed = controller.prefix_allowed_tokens_fn(0, output_ids)
            token_id = self.tokenizer.token_id(character)
            self.assertIn(token_id, allowed)
            output_ids.append(token_id)

        self.assertEqual(
            [GENERATION_EOS_TOKEN_ID],
            controller.prefix_allowed_tokens_fn(0, output_ids),
        )
        with self.assertRaises(StatefulConstrainedDecodingError) as incomplete:
            controller.finalize(output_ids)
        self.assertEqual("incomplete_generation", incomplete.exception.code)


@unittest.skipUnless(
    os.environ.get("REQ2WEB_RUN_QWEN_TOKENIZER_COMPAT") == "1",
    "set REQ2WEB_RUN_QWEN_TOKENIZER_COMPAT=1 for local tokenizer-only coverage",
)
class LocalQwenTokenizerCompatibilityTests(unittest.TestCase):
    def test_qwen2_tokenizer_prefix_callback_without_model_loading(self) -> None:
        if not LOCAL_QWEN_ROOT.is_dir():
            self.skipTest("local Qwen tokenizer directory is unavailable")
        from transformers import AutoConfig, AutoTokenizer, GenerationConfig

        model_config = AutoConfig.from_pretrained(
            LOCAL_QWEN_ROOT,
            local_files_only=True,
            trust_remote_code=False,
        )
        generation_config = GenerationConfig.from_model_config(model_config)
        tokenizer = AutoTokenizer.from_pretrained(
            LOCAL_QWEN_ROOT,
            local_files_only=True,
            trust_remote_code=False,
        )
        text_config = getattr(model_config, "text_config", model_config)
        model_vocabulary_size = getattr(text_config, "vocab_size", None)
        generation_eos_token_id = generation_config.eos_token_id
        self.assertIs(type(model_vocabulary_size), int)
        self.assertIs(type(generation_eos_token_id), int)
        self.assertEqual("Qwen2Tokenizer", type(tokenizer).__name__)
        self.assertNotEqual(
            tokenizer.eos_token_id, generation_eos_token_id
        )
        prompt_ids = tokenizer.encode("P", add_special_tokens=False)
        self.assertTrue(prompt_ids)
        controller = create_stateful_constrained_generation(
            tokenizer,
            len(prompt_ids),
            USE_CASE_IDS,
            generation_eos_token_id=generation_eos_token_id,
            model_vocabulary_size=model_vocabulary_size,
        )

        allowed = controller.prefix_allowed_tokens_fn(0, prompt_ids)

        self.assertTrue(allowed)
        decoded = {
            tokenizer.decode(
                [token_id],
                skip_special_tokens=False,
                clean_up_tokenization_spaces=False,
            )
            for token_id in allowed
        }
        self.assertIn("{", decoded)
        self.assertFalse(controller.is_complete)

        output_ids = list(prompt_ids)
        raw = compact(candidate_payload())
        character_ids: dict[str, int] = {}
        for character in set(raw):
            encoded = tokenizer.encode(character, add_special_tokens=False)
            self.assertEqual(
                1,
                len(encoded),
                msg=f"Qwen tokenizer has no singleton token for {character!r}",
            )
            self.assertEqual(
                character,
                tokenizer.decode(
                    encoded,
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ),
            )
            character_ids[character] = encoded[0]
        for character in raw:
            allowed = controller.prefix_allowed_tokens_fn(0, output_ids)
            self.assertIn(character_ids[character], allowed)
            output_ids.append(character_ids[character])
        self.assertEqual(
            [generation_eos_token_id],
            controller.prefix_allowed_tokens_fn(0, output_ids),
        )
        output_ids.append(generation_eos_token_id)
        completion = controller.finalize(output_ids)
        self.assertEqual(raw.encode("utf-8"), completion.raw_bytes)
        self.assertTrue(completion.raw_unmodified)
        self.assertEqual(
            generation_eos_token_id,
            completion.audit.generation_eos_token_id,
        )
        self.assertEqual(
            model_vocabulary_size, completion.audit.model_vocabulary_size
        )


if __name__ == "__main__":
    unittest.main()
