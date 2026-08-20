#!/usr/bin/env python3
"""Install the Req2Web BigModel adapter into the pinned GUISpector checkout.

The upstream checkout is intentionally ignored because the inspected revision
does not publish a license file. This script verifies that exact revision and
applies only the small local integration overlay needed by the optional
GUISpector sidecar. It never reads or writes an API key.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


EXPECTED_UPSTREAM_COMMIT = "1472b7027099402758337897db5ea0b8d4e6ed4e"
DEFAULT_RUNTIME_ROOT = Path("outputs/guispector_runtime_1472b702/upstream")


class OverlayInstallError(RuntimeError):
    pass


def _replace_once(path: Path, old: str, new: str, marker: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return False
    if text.count(old) != 1:
        raise OverlayInstallError(f"Unexpected source shape in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    return True


def _write_exact(path: Path, content: str) -> bool:
    normalized = content.rstrip() + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == normalized:
            return False
        raise OverlayInstallError(f"Refusing to overwrite unexpected file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8", newline="\n")
    return True


def _copy_exact(source: Path, destination: Path) -> bool:
    source_bytes = source.read_bytes()
    if destination.exists() and destination.read_bytes() == source_bytes:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def _git_head(runtime_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(runtime_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def install(repository_root: Path, runtime_root: Path) -> dict[str, object]:
    runtime_root = runtime_root.resolve()
    if _git_head(runtime_root) != EXPECTED_UPSTREAM_COMMIT:
        raise OverlayInstallError("GUISpector checkout is not the pinned inspected revision")

    required = [
        runtime_root / "gui_spector/src/gui_spector/verfication/agent.py",
        runtime_root / "gui_spector/src/gui_spector/verfication/config.py",
        runtime_root / "webapp/settings/models.py",
        runtime_root / "webapp/settings/views.py",
        runtime_root / "webapp/settings/templates/settings/settings.html",
        runtime_root / "webapp/setups/tasks.py",
        runtime_root / "docker-compose.req2web.yml",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise OverlayInstallError(f"GUISpector checkout is incomplete: {missing}")

    changed: list[str] = []

    provider_source = repository_root / "src/req2web_inspector/guispector_bigmodel_provider.py"
    provider_target = runtime_root / "gui_spector/src/gui_spector/verfication/bigmodel_provider.py"
    if _copy_exact(provider_source, provider_target):
        changed.append(str(provider_target.relative_to(runtime_root)))

    command_source = repository_root / "src/req2web_inspector/guispector_runtime_import_command.py"
    command_target = (
        runtime_root / "webapp/setups/management/commands/import_req2web_packet.py"
    )
    if _copy_exact(command_source, command_target):
        changed.append(str(command_target.relative_to(runtime_root)))
    for init_path in (
        runtime_root / "webapp/setups/management/__init__.py",
        runtime_root / "webapp/setups/management/commands/__init__.py",
    ):
        if _write_exact(init_path, "# Req2Web project-authored GUISpector overlay package."):
            changed.append(str(init_path.relative_to(runtime_root)))

    compose_path = runtime_root / "docker-compose.req2web.yml"
    if _replace_once(
        compose_path,
        '''    ports:
      - "127.0.0.1:5900:5900"
    volumes:
      - ./webapp:/app/webapp:ro
      - ./gui_spector:/app/gui_spector:ro
      - guispector_media:/app/webapp/media
    depends_on:
      mysql:
''',
        '''    ports:
      - "127.0.0.1:5900:5900"
    volumes:
      - ./webapp:/app/webapp:ro
      - ./gui_spector:/app/gui_spector:ro
      - ../../../release/phase6_reviewer_v17:/app/req2web_reviewer_v17:ro
      - guispector_media:/app/webapp/media
    depends_on:
      mysql:
''',
        "../../../release/phase6_reviewer_v17:/app/req2web_reviewer_v17:ro",
    ):
        changed.append(str(compose_path.relative_to(runtime_root)))
    if _replace_once(
        compose_path,
        '''      LOCAL_AGENT_EXEC: "1"
      NUM_DISPLAYS: "1"
      SCREEN_RESOLUTION: 1280x800x24
''',
        '''      LOCAL_AGENT_EXEC: "1"
      NUM_DISPLAYS: "1"
      DISPLAY_POOL_SIZE: "1"
      SCREEN_RESOLUTION: 1280x800x24
''',
        'DISPLAY_POOL_SIZE: "1"',
    ):
        if str(compose_path.relative_to(runtime_root)) not in changed:
            changed.append(str(compose_path.relative_to(runtime_root)))

    config_path = runtime_root / "gui_spector/src/gui_spector/verfication/config.py"
    if _replace_once(
        config_path,
        'OPENAI_COMPUTER_USE_PREVIEW = "computer-use-preview"\n',
        'OPENAI_COMPUTER_USE_PREVIEW = "computer-use-preview"\n'
        'BIGMODEL_GLM_4_6V = "zhipu-glm-4.6v"\n',
        'BIGMODEL_GLM_4_6V = "zhipu-glm-4.6v"',
    ):
        changed.append(str(config_path.relative_to(runtime_root)))
    if _replace_once(
        config_path,
        '    (OPENAI_COMPUTER_USE_PREVIEW, "OpenAI GPT-4o (CUA)"),\n',
        '    (OPENAI_COMPUTER_USE_PREVIEW, "OpenAI GPT-4o (CUA)"),\n'
        '    (BIGMODEL_GLM_4_6V, "Zhipu GLM-4.6V (Req2Web adapter)"),\n',
        '"Zhipu GLM-4.6V (Req2Web adapter)"',
    ):
        if str(config_path.relative_to(runtime_root)) not in changed:
            changed.append(str(config_path.relative_to(runtime_root)))

    agent_path = runtime_root / "gui_spector/src/gui_spector/verfication/agent.py"
    if _replace_once(
        agent_path,
        'from gui_spector.exceptions.acceptance_criteria_mismatch import AcceptanceCriteriaMismatchException\n',
        'from gui_spector.exceptions.acceptance_criteria_mismatch import AcceptanceCriteriaMismatchException\n'
        'from gui_spector.verfication.bigmodel_provider import (\n'
        '    BIGMODEL_AGENT_ID,\n'
        '    create_bigmodel_response,\n'
        ')\n',
        'from gui_spector.verfication.bigmodel_provider import (',
    ):
        changed.append(str(agent_path.relative_to(runtime_root)))
    old_response_call = '''            response = create_response(
                model=self.model,
                input=input_items + new_items,
                tools=self.tools,
                reasoning={"effort": "medium", "summary": "auto"},
                truncation="auto",
            )
'''
    new_response_call = '''            if self.model == BIGMODEL_AGENT_ID:
                response = create_bigmodel_response(
                    input_items=input_items + new_items,
                    computer_tools=self.tools,
                )
            else:
                response = create_response(
                    model=self.model,
                    input=input_items + new_items,
                    tools=self.tools,
                    reasoning={"effort": "medium", "summary": "auto"},
                    truncation="auto",
                )
'''
    if _replace_once(
        agent_path,
        old_response_call,
        new_response_call,
        "if self.model == BIGMODEL_AGENT_ID:\n                response = create_bigmodel_response(",
    ):
        if str(agent_path.relative_to(runtime_root)) not in changed:
            changed.append(str(agent_path.relative_to(runtime_root)))
    old_initial = '''        items = []
        items.append({"role": "user", "content": user_message})
        output_items = self.run_full_turn(
'''
    new_initial = '''        items = []
        if self.model == BIGMODEL_AGENT_ID:
            if self.computer is None:
                raise ValueError("GLM-4.6V verification requires a computer adapter")
            initial_screenshot = self.computer.screenshot()
            items.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_message},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{initial_screenshot}",
                        },
                    ],
                }
            )
        else:
            items.append({"role": "user", "content": user_message})
        output_items = self.run_full_turn(
'''
    if _replace_once(
        agent_path,
        old_initial,
        new_initial,
        'raise ValueError("GLM-4.6V verification requires a computer adapter")',
    ):
        if str(agent_path.relative_to(runtime_root)) not in changed:
            changed.append(str(agent_path.relative_to(runtime_root)))

    tasks_path = runtime_root / "webapp/setups/tasks.py"
    if _replace_once(
        tasks_path,
        'from gui_spector.verfication.agent import VerficationRunner, PROMPT_TEMPLATE_V1\n',
        'from gui_spector.verfication.agent import VerficationRunner, PROMPT_TEMPLATE_V1\n'
        'from gui_spector.verfication.bigmodel_provider import BIGMODEL_AGENT_ID\n',
        'from gui_spector.verfication.bigmodel_provider import BIGMODEL_AGENT_ID',
    ):
        changed.append(str(tasks_path.relative_to(runtime_root)))
    if _replace_once(
        tasks_path,
        "        max_retries = int(getattr(setup, 'max_retries', 2))\n",
        "        max_retries = (\n"
        "            0\n"
        "            if setup.agent_model == BIGMODEL_AGENT_ID\n"
        "            else int(getattr(setup, 'max_retries', 2))\n"
        "        )\n",
        "if setup.agent_model == BIGMODEL_AGENT_ID",
    ):
        if str(tasks_path.relative_to(runtime_root)) not in changed:
            changed.append(str(tasks_path.relative_to(runtime_root)))
    if _replace_once(
        tasks_path,
        '''    try:
        attempts = 0
        max_retries = (
''',
        '''    try:
        computer = None
        attempts = 0
        max_retries = (
''',
        "computer = None\n        attempts = 0",
    ):
        if str(tasks_path.relative_to(runtime_root)) not in changed:
            changed.append(str(tasks_path.relative_to(runtime_root)))
    if _replace_once(
        tasks_path,
        '''                print(f"Run error: {run_exc} attempts: {attempts}")
                computer.cleanup_browser()
                if attempts > max_retries:
''',
        '''                print(f"Run error: {run_exc} attempts: {attempts}")
                if computer is not None:
                    computer.cleanup_browser()
                if attempts > max_retries:
''',
        "if computer is not None:\n                    computer.cleanup_browser()",
    ):
        if str(tasks_path.relative_to(runtime_root)) not in changed:
            changed.append(str(tasks_path.relative_to(runtime_root)))
    if _replace_once(
        tasks_path,
        '''        try:
            computer.cleanup_browser()
            pool.release(disp)
''',
        '''        try:
            if computer is not None:
                computer.cleanup_browser()
            pool.release(disp)
''',
        "if computer is not None:\n                computer.cleanup_browser()",
    ):
        if str(tasks_path.relative_to(runtime_root)) not in changed:
            changed.append(str(tasks_path.relative_to(runtime_root)))
    if _replace_once(
        tasks_path,
        '''                    runner = VerficationRunner(
                        computer=computer,
''',
        '''                    runner = VerficationRunner(
                        model=setup.agent_model,
                        computer=computer,
''',
        "model=setup.agent_model,",
    ):
        if str(tasks_path.relative_to(runtime_root)) not in changed:
            changed.append(str(tasks_path.relative_to(runtime_root)))

    settings_model_path = runtime_root / "webapp/settings/models.py"
    if _replace_once(
        settings_model_path,
        '    openai_key = models.CharField(max_length=512)\n',
        '    openai_key = models.CharField(max_length=512)\n'
        '    zhipu_api_key = models.CharField(max_length=512, blank=True, null=True)\n',
        "zhipu_api_key = models.CharField",
    ):
        changed.append(str(settings_model_path.relative_to(runtime_root)))
    if _replace_once(
        settings_model_path,
        '        if latest_settings.anthropic_api_key:\n',
        '        if latest_settings.zhipu_api_key:\n'
        '            os.environ["ZHIPU_API_KEY"] = latest_settings.zhipu_api_key\n'
        '            print("ZHIPU_API_KEY configured")\n'
        '        if latest_settings.anthropic_api_key:\n',
        'os.environ["ZHIPU_API_KEY"]',
    ):
        if str(settings_model_path.relative_to(runtime_root)) not in changed:
            changed.append(str(settings_model_path.relative_to(runtime_root)))

    settings_views_path = runtime_root / "webapp/settings/views.py"
    settings_views = '''from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from gui_spector.verfication.bigmodel_provider import (
    BigModelProviderError,
    test_bigmodel_connection,
)

from .models import SettingsModel, set_api_keys_from_settings


class SettingsForm(forms.ModelForm):
    class Meta:
        model = SettingsModel
        fields = [
            "zhipu_api_key",
            "openai_key",
            "google_api_key",
            "anthropic_api_key",
            "num_workers",
        ]
        widgets = {
            "zhipu_api_key": forms.PasswordInput(
                attrs={"class": "form-control", "placeholder": "Paste your Zhipu API key"},
                render_value=False,
            ),
            "openai_key": forms.PasswordInput(
                attrs={"class": "form-control", "placeholder": "Optional OpenAI API key"},
                render_value=False,
            ),
            "google_api_key": forms.PasswordInput(
                attrs={"class": "form-control", "placeholder": "Optional Google API key"},
                render_value=False,
            ),
            "anthropic_api_key": forms.PasswordInput(
                attrs={"class": "form-control", "placeholder": "Optional Anthropic API key"},
                render_value=False,
            ),
            "num_workers": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 5, "step": 1}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("zhipu_api_key", "openai_key", "google_api_key", "anthropic_api_key"):
            self.fields[name].required = False
            if self.instance and getattr(self.instance, name, None):
                self.fields[name].widget.attrs["placeholder"] = (
                    "Configured locally. Leave blank to keep the saved value."
                )

    def clean_num_workers(self):
        value = self.cleaned_data["num_workers"]
        if not 1 <= value <= 5:
            raise forms.ValidationError("Parallel workers must be between 1 and 5.")
        return value


class SettingsView(View):
    template_name = "settings/settings.html"
    secret_fields = ("zhipu_api_key", "openai_key", "google_api_key", "anthropic_api_key")

    def _latest(self):
        return SettingsModel.objects.order_by("-created_at").first()

    def _context(self, form, settings_row):
        return {
            "form": form,
            "zhipu_configured": bool(settings_row and settings_row.zhipu_api_key),
        }

    def get(self, request):
        latest = self._latest()
        return render(
            request,
            self.template_name,
            self._context(SettingsForm(instance=latest), latest),
        )

    def post(self, request):
        latest = self._latest()
        form = SettingsForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._context(form, latest))

        settings_row = form.save(commit=False)
        for field_name in self.secret_fields:
            submitted = str(form.cleaned_data.get(field_name) or "").strip()
            if submitted:
                setattr(settings_row, field_name, submitted)
            elif latest is not None:
                setattr(settings_row, field_name, getattr(latest, field_name, None))
        settings_row.save()
        set_api_keys_from_settings()

        if request.POST.get("action") == "test_zhipu":
            try:
                test_bigmodel_connection(settings_row.zhipu_api_key or "")
            except BigModelProviderError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "GLM-4.6V connection test passed.")
            return redirect("settings:settings")

        messages.success(request, "Local settings saved.")
        return redirect("settings:settings")
'''
    existing_views = settings_views_path.read_text(encoding="utf-8")
    if "test_bigmodel_connection" not in existing_views:
        settings_views_path.write_text(settings_views, encoding="utf-8", newline="\n")
        changed.append(str(settings_views_path.relative_to(runtime_root)))

    settings_template_path = runtime_root / "webapp/settings/templates/settings/settings.html"
    settings_template = '''{% extends 'base.html' %}
{% block content %}
<div class="container py-4" style="min-height: 80vh;">
  <div class="row justify-content-center">
    <div class="col-lg-8">
      <div class="card shadow-sm rounded-4 border-0">
        <div class="card-body p-4 p-md-5">
          <div class="mb-4">
            <h2 class="fw-bold mb-2">Verification provider settings</h2>
            <p class="text-muted mb-0">Keys stay in this local Docker database and are sent only to the selected provider.</p>
          </div>

          {% if messages %}
            {% for message in messages %}
              <div class="alert {% if message.tags == 'error' %}alert-danger{% else %}alert-success{% endif %}" role="alert">{{ message }}</div>
            {% endfor %}
          {% endif %}

          <form method="post" autocomplete="off">
            {% csrf_token %}
            <div class="border rounded-3 p-3 p-md-4 mb-4">
              <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
                <div>
                  <h3 class="h5 fw-bold mb-1">Zhipu BigModel</h3>
                  <div class="text-muted small">GUISpector interactive verifier · fixed model: GLM-4.6V · one attempt per run</div>
                </div>
                <span class="badge {% if zhipu_configured %}text-bg-success{% else %}text-bg-secondary{% endif %}">
                  {% if zhipu_configured %}Configured{% else %}Not configured{% endif %}
                </span>
              </div>
              <label for="id_zhipu_api_key" class="form-label fw-semibold">Zhipu API key</label>
              {{ form.zhipu_api_key }}
              {% if form.zhipu_api_key.errors %}<div class="text-danger small mt-1">{{ form.zhipu_api_key.errors }}</div>{% endif %}
              <div class="form-text">The value is masked and is never rendered back into this page.</div>
              <button type="submit" name="action" value="test_zhipu" class="btn btn-outline-primary mt-3">Save and test GLM-4.6V</button>
            </div>

            <details class="mb-4">
              <summary class="fw-semibold mb-3">Other upstream providers</summary>
              <div class="mt-3">
                <label for="id_openai_key" class="form-label fw-semibold">OpenAI API key</label>
                {{ form.openai_key }}
                {% if form.openai_key.errors %}<div class="text-danger small mt-1">{{ form.openai_key.errors }}</div>{% endif %}
              </div>
              <div class="mt-3">
                <label for="id_google_api_key" class="form-label fw-semibold">Google API key</label>
                {{ form.google_api_key }}
                {% if form.google_api_key.errors %}<div class="text-danger small mt-1">{{ form.google_api_key.errors }}</div>{% endif %}
              </div>
              <div class="mt-3">
                <label for="id_anthropic_api_key" class="form-label fw-semibold">Anthropic API key</label>
                {{ form.anthropic_api_key }}
                {% if form.anthropic_api_key.errors %}<div class="text-danger small mt-1">{{ form.anthropic_api_key.errors }}</div>{% endif %}
              </div>
            </details>

            <div class="mb-4">
              <label for="id_num_workers" class="form-label fw-semibold">Parallel workers</label>
              {{ form.num_workers }}
              {% if form.num_workers.errors %}<div class="text-danger small mt-1">{{ form.num_workers.errors }}</div>{% endif %}
              <div class="form-text">GLM-4.6V runs are fail-closed and are not automatically retried.</div>
            </div>
            <button type="submit" name="action" value="save" class="btn btn-success px-4">Save settings</button>
          </form>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %}
'''
    existing_template = settings_template_path.read_text(encoding="utf-8")
    if "Save and test GLM-4.6V" not in existing_template:
        settings_template_path.write_text(settings_template, encoding="utf-8", newline="\n")
        changed.append(str(settings_template_path.relative_to(runtime_root)))

    migration_path = runtime_root / "webapp/settings/migrations/0002_settingsmodel_zhipu_api_key.py"
    migration = '''from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settings", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="settingsmodel",
            name="zhipu_api_key",
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]
'''
    if _write_exact(migration_path, migration):
        changed.append(str(migration_path.relative_to(runtime_root)))

    return {
        "schema_version": "req2web.guispector_bigmodel_overlay_install.v1",
        "upstream_commit": EXPECTED_UPSTREAM_COMMIT,
        "runtime_root": str(runtime_root),
        "changed_files": changed,
        "api_key_read": False,
        "api_call_made": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install the local GLM-4.6V adapter into the pinned GUISpector checkout."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Pinned GUISpector checkout root.",
    )
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    try:
        result = install(repository_root, args.runtime_root)
    except (OverlayInstallError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"installed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"installed": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
