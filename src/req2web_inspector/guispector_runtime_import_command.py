"""Django management command for model-free Req2Web packet import.

This project-authored command is copied into the pinned, ignored GUISpector
checkout by ``install_guispector_bigmodel_overlay.py``. It creates or validates
one GUISpector setup per frozen Req2Web case without invoking any LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gui_spector.llm.llm import LLM
from gui_spector.verfication.bigmodel_provider import BIGMODEL_AGENT_ID
from setups.models import AcceptanceCriterion, Requirement, Setup


PACKET_SCHEMA_VERSION = "req2web.guispector.evaluation.v1"
DEFAULT_PACKET = Path("/app/req2web_reviewer_v17/guispector_evaluation.json")
DEFAULT_BASE_URL = "http://req2web-pages"
TOKEN = "{REQ2WEB_INSPECTOR_BASE_URL}"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CommandError(f"{label} must be an object")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommandError(f"{label} must be a non-empty string")
    return value.strip()


def _load_packet(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandError("could not read the Req2Web GUISpector packet") from exc
    packet = _mapping(value, "packet")
    if packet.get("schema_version") != PACKET_SCHEMA_VERSION:
        raise CommandError("Req2Web GUISpector packet schema drifted")
    cases = packet.get("cases")
    if not isinstance(cases, list) or len(cases) != 12:
        raise CommandError("Req2Web GUISpector packet must contain exactly 12 cases")
    scope = _mapping(packet.get("scope"), "packet scope")
    if scope.get("result_package_count") != 12 or scope.get("acceptance_criterion_count") != 24:
        raise CommandError("Req2Web GUISpector packet scope drifted")
    return packet


def _setup_name(index: int, case_id: str, condition_id: str) -> str:
    name = f"Req2Web {index:02d} · {case_id} · {condition_id}"
    if len(name) > 255:
        raise CommandError("generated setup name exceeds the GUISpector limit")
    return name


def _priority(value: Any) -> str:
    normalized = str(value or "medium").lower()
    if normalized not in {"low", "medium", "high"}:
        raise CommandError("unsupported requirement priority")
    return normalized


def _expected_case(
    packet: Mapping[str, Any],
    raw_case: Any,
    expected_index: int,
    base_url: str,
) -> dict[str, Any]:
    case = _mapping(raw_case, f"case {expected_index}")
    if case.get("execution_index") != expected_index:
        raise CommandError("Req2Web GUISpector execution order drifted")
    case_id = _nonempty(case.get("case_id"), "case id")
    condition_id = _nonempty(case.get("condition_id"), "condition id")
    requirement = _mapping(case.get("guispector_requirement"), "GUISpector requirement")
    template = _nonempty(requirement.get("start_url"), "start URL")
    expected_prefix = TOKEN + "/"
    if not template.startswith(expected_prefix) or template.count(TOKEN) != 1:
        raise CommandError("GUISpector start URL template drifted")
    start_url = base_url.rstrip("/") + template[len(TOKEN) :]
    criteria = requirement.get("acceptance_criteria")
    if not isinstance(criteria, list) or len(criteria) != 2:
        raise CommandError("each frozen GUISpector case must contain exactly two criteria")
    normalized_criteria: list[dict[str, str]] = []
    for criterion_index, raw_criterion in enumerate(criteria, start=1):
        criterion = _mapping(raw_criterion, "acceptance criterion")
        expected_name = f"AC-{criterion_index}"
        if criterion.get("criterion_name") != expected_name:
            raise CommandError("acceptance criterion order drifted")
        normalized_criteria.append(
            {
                "name": expected_name,
                "text": _nonempty(criterion.get("description"), "criterion description"),
            }
        )
    tags = requirement.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise CommandError("requirement tags are invalid")
    metadata = {
        "source": "req2web.guispector.packet.import.v1",
        "evaluation_identity": _nonempty(packet.get("evaluation_identity"), "evaluation identity"),
        "execution_index": expected_index,
        "case_id": case_id,
        "condition_id": condition_id,
        "bundle_relative_start_url": _nonempty(
            case.get("bundle_relative_start_url"), "bundle-relative start URL"
        ),
        "model_invocation_performed": False,
    }
    return {
        "setup_name": _setup_name(expected_index, case_id, condition_id),
        "start_url": start_url,
        "setup_description": (
            "Model-free import from the frozen Req2Web GUISpector packet. "
            f"Evaluation identity: {metadata['evaluation_identity']}"
        ),
        "title": _nonempty(requirement.get("title"), "requirement title"),
        "description": _nonempty(requirement.get("description"), "requirement description"),
        "source": _nonempty(requirement.get("source"), "requirement source"),
        "tags": list(tags),
        "priority": _priority(requirement.get("priority")),
        "metadata": metadata,
        "criteria": normalized_criteria,
    }


def _verify_existing(setup: Setup, expected: Mapping[str, Any]) -> None:
    if (
        setup.start_url != expected["start_url"]
        or setup.description != expected["setup_description"]
        or setup.agent_model != BIGMODEL_AGENT_ID
        or setup.max_retries != 0
        or setup.max_reasoning_steps != 30
        or setup.agent_timeout_seconds != 300
    ):
        raise CommandError(f"existing setup drifted: {setup.name}")
    requirements = list(setup.requirements.all())
    if len(requirements) != 1:
        raise CommandError(f"existing setup requirement count drifted: {setup.name}")
    requirement = requirements[0]
    if (
        requirement.title != expected["title"]
        or requirement.description != expected["description"]
        or requirement.source != expected["source"]
        or requirement.tags_json != expected["tags"]
        or requirement.priority != expected["priority"]
        or requirement.metadata_json != expected["metadata"]
    ):
        raise CommandError(f"existing requirement drifted: {setup.name}")
    observed_criteria = [
        {"name": criterion.name, "text": criterion.text}
        for criterion in requirement.criteria.all()
    ]
    if observed_criteria != expected["criteria"]:
        raise CommandError(f"existing acceptance criteria drifted: {setup.name}")


class Command(BaseCommand):
    help = "Import or validate the frozen Req2Web GUISpector packet without calling a model."

    def add_arguments(self, parser):
        parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
        parser.add_argument("--base-url", default=DEFAULT_BASE_URL)

    @transaction.atomic
    def handle(self, *args, **options):
        packet = _load_packet(options["packet"])
        base_url = _nonempty(options["base_url"], "base URL")
        if not base_url.startswith(("http://", "https://")):
            raise CommandError("base URL must use HTTP or HTTPS")

        created = 0
        validated = 0
        setup_ids: list[int] = []
        for expected_index, raw_case in enumerate(packet["cases"], start=1):
            expected = _expected_case(packet, raw_case, expected_index, base_url)
            existing = Setup.objects.filter(name=expected["setup_name"]).first()
            if existing is not None:
                _verify_existing(existing, expected)
                validated += 1
                setup_ids.append(existing.id)
                continue

            setup = Setup.objects.create(
                name=expected["setup_name"],
                start_url=expected["start_url"],
                description=expected["setup_description"],
                tags_json=["req2web", "guispector", "frozen-phase5"],
                llm_model=LLM.MODEL_GPT_4_1,
                agent_model=BIGMODEL_AGENT_ID,
                max_reasoning_steps=30,
                agent_timeout_seconds=300,
                max_retries=0,
            )
            requirement = Requirement.objects.create(
                setup=setup,
                title=expected["title"],
                description=expected["description"],
                source=expected["source"],
                tags_json=expected["tags"],
                priority=expected["priority"],
                metadata_json=expected["metadata"],
            )
            AcceptanceCriterion.objects.bulk_create(
                [
                    AcceptanceCriterion(
                        requirement=requirement,
                        name=criterion["name"],
                        text=criterion["text"],
                    )
                    for criterion in expected["criteria"]
                ]
            )
            created += 1
            setup_ids.append(setup.id)

        self.stdout.write(
            json.dumps(
                {
                    "schema_version": "req2web.guispector.packet.import_receipt.v1",
                    "status": "imported_and_validated_model_free",
                    "evaluation_identity": packet["evaluation_identity"],
                    "created_setup_count": created,
                    "validated_setup_count": validated,
                    "setup_count": len(setup_ids),
                    "setup_ids": setup_ids,
                    "model_invocation_performed": False,
                    "api_key_read": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
