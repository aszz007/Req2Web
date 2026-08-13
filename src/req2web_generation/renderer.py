from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path

from .schema import PAGE_SPEC_SCHEMA_VERSION, ComponentSpec, PageSpec


RENDER_MANIFEST_SCHEMA_VERSION = "req2web.render_manifest.v1"
SUPPORTED_COMPONENT_TYPES = (
    "primary_action",
    "media_input",
    "search_input",
    "form",
    "data_view",
    "location_picker",
    "status_panel",
)


@dataclass(frozen=True)
class RenderResult:
    page_id: str
    output_dir: Path
    index_html: Path
    styles_css: Path
    app_js: Path
    render_manifest: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "page_id": self.page_id,
            "output_dir": str(self.output_dir),
            "index_html": str(self.index_html),
            "styles_css": str(self.styles_css),
            "app_js": str(self.app_js),
            "render_manifest": str(self.render_manifest),
        }


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _javascript_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class DeterministicPageRenderer:
    """Render a validated PageSpec into an offline static page bundle."""

    def render(self, page_spec: PageSpec, output_dir: Path) -> RenderResult:
        if not isinstance(page_spec, PageSpec):
            raise TypeError("DeterministicPageRenderer input must be a PageSpec")

        # Validation intentionally happens before creating the output directory.
        page_spec.validate()
        destination = Path(output_dir)

        index_bytes = self._render_html(page_spec).encode("utf-8")
        styles_bytes = self._render_css().encode("utf-8")
        app_bytes = self._render_javascript(page_spec).encode("utf-8")
        rendered_files = (
            ("index.html", index_bytes),
            ("styles.css", styles_bytes),
            ("app.js", app_bytes),
        )
        manifest = {
            "files": [
                {"name": name, "sha256": _sha256(content)}
                for name, content in rendered_files
            ],
            "page_id": page_spec.page_id,
            "page_spec_schema_version": PAGE_SPEC_SCHEMA_VERSION,
            "schema_version": RENDER_MANIFEST_SCHEMA_VERSION,
        }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

        destination.mkdir(parents=True, exist_ok=True)
        for name, content in rendered_files:
            (destination / name).write_bytes(content)
        manifest_path = destination / "render_manifest.json"
        manifest_path.write_bytes(manifest_bytes)

        return RenderResult(
            page_id=page_spec.page_id,
            output_dir=destination,
            index_html=destination / "index.html",
            styles_css=destination / "styles.css",
            app_js=destination / "app.js",
            render_manifest=manifest_path,
        )

    def _render_html(self, page_spec: PageSpec) -> str:
        sections_by_id = {item.section_id: item for item in page_spec.sections}
        components_by_id = {
            item.component_id: item for item in page_spec.components
        }
        trigger_ids = {
            item.trigger_component_id for item in page_spec.interactions
        }
        sections = []
        for section_id in page_spec.layout.section_order:
            section = sections_by_id[section_id]
            component_markup = "\n".join(
                self._render_component(
                    components_by_id[component_id],
                    component_id in trigger_ids,
                )
                for component_id in section.component_ids
            )
            sections.append(
                "\n".join(
                    (
                        f'      <section class="page-section" id="{_escape(section.section_id)}" '
                        f'data-section-id="{_escape(section.section_id)}" '
                        f'data-use-case-ids="{_escape(" ".join(section.use_case_ids))}">',
                        '        <div class="section-heading">',
                        f"          <p class=\"section-kicker\">{_escape(section.purpose)}</p>",
                        f"          <h2>{_escape(section.title)}</h2>",
                        "        </div>",
                        '        <div class="component-list">',
                        component_markup,
                        "        </div>",
                        "      </section>",
                    )
                )
            )

        initial_state = next(
            (item for item in page_spec.states if item.name == "initial"),
            page_spec.states[0],
        )
        return "\n".join(
            (
                "<!doctype html>",
                f'<html lang="en" data-page-spec-schema="{PAGE_SPEC_SCHEMA_VERSION}">',
                "<head>",
                '  <meta charset="utf-8">',
                '  <meta name="viewport" content="width=device-width, initial-scale=1">',
                f"  <title>{_escape(page_spec.title)}</title>",
                '  <link rel="stylesheet" href="styles.css">',
                '  <script src="app.js" defer></script>',
                "</head>",
                f'<body data-page-id="{_escape(page_spec.page_id)}" '
                f'data-target-device="{_escape(page_spec.target_device)}" '
                f'data-state-id="{_escape(initial_state.state_id)}" '
                f'data-state-name="{_escape(initial_state.name)}">',
                f'  <main class="page-shell" data-layout-pattern="{_escape(page_spec.layout.pattern)}">',
                '    <header class="hero">',
                '      <div class="hero-copy">',
                '        <p class="eyebrow">Req2Web · offline prototype</p>',
                f"        <h1>{_escape(page_spec.title)}</h1>",
                f"        <p class=\"summary\">{_escape(page_spec.summary)}</p>",
                "      </div>",
                '      <dl class="page-meta">',
                f"        <div><dt>Device</dt><dd>{_escape(page_spec.target_device)}</dd></div>",
                f"        <div><dt>Page type</dt><dd>{_escape(page_spec.page_type)}</dd></div>",
                "      </dl>",
                '      <div class="page-state" id="page-state" role="status" aria-live="polite" '
                f'data-state-id="{_escape(initial_state.state_id)}" '
                f'data-state-name="{_escape(initial_state.name)}">',
                f"        <span class=\"state-dot\" aria-hidden=\"true\"></span><strong>{_escape(initial_state.name)}</strong>",
                f"        <span class=\"state-message\">{_escape(initial_state.description)}</span>",
                "      </div>",
                "    </header>",
                '    <div class="section-list">',
                *sections,
                "    </div>",
                '    <footer class="page-footer">',
                f"      <span>{_escape(page_spec.page_id)}</span>",
                f"      <span>{PAGE_SPEC_SCHEMA_VERSION}</span>",
                "    </footer>",
                "  </main>",
                "</body>",
                "</html>",
                "",
            )
        )

    def _render_component(
        self, component: ComponentSpec, has_interaction: bool
    ) -> str:
        component_id = _escape(component.component_id)
        label = _escape(component.label)
        purpose = _escape(component.purpose)
        component_type = _escape(component.component_type)
        trigger = (
            f' data-interaction-trigger="{component_id}"' if has_interaction else ""
        )
        content: list[str]

        if component.component_type == "primary_action":
            content = [
                f'          <button class="primary-button" type="button"{trigger}>{label}</button>'
            ]
        elif component.component_type == "media_input":
            content = [
                f'          <label class="file-control" for="input-{component_id}">Choose image or media</label>',
                f'          <input id="input-{component_id}" class="media-input" type="file" accept="image/*"{trigger}>',
                f'          <button class="secondary-button" type="button"{trigger}>Use sample input</button>',
            ]
        elif component.component_type == "search_input":
            content = [
                '          <div class="inline-control">',
                f'            <label class="sr-only" for="input-{component_id}">{label}</label>',
                f'            <input id="input-{component_id}" type="search" placeholder="Enter keywords">',
                f'            <button type="button"{trigger}>Search</button>',
                "          </div>",
            ]
        elif component.component_type == "form":
            form_trigger = (
                f' data-interaction-form="{component_id}"' if has_interaction else ""
            )
            content = [
                f'          <form class="compact-form" novalidate{form_trigger}>',
                f'            <label for="input-{component_id}">{label}</label>',
                f'            <input id="input-{component_id}" type="text" placeholder="Enter required information" required>',
                "            <button type=\"submit\">Submit</button>",
                "          </form>",
            ]
        elif component.component_type == "data_view":
            content = [
                '          <div class="metric-grid" role="group" aria-label="Data summary">',
                "            <div><strong>24</strong><span>Active items</span></div>",
                "            <div><strong>88%</strong><span>Completion</span></div>",
                "          </div>",
                f'          <button class="secondary-button" type="button"{trigger}>Refresh data</button>',
            ]
        elif component.component_type == "location_picker":
            content = [
                '          <div class="location-preview" aria-hidden="true"><span></span></div>',
                f'          <button class="secondary-button" type="button"{trigger}>Choose location</button>',
            ]
        elif component.component_type == "status_panel":
            content = [
                f'          <output class="component-feedback" data-default-feedback="{purpose}">{purpose}</output>'
            ]
            if has_interaction:
                content.append(
                    f'          <button class="secondary-button" type="button"{trigger}>Confirm status</button>'
                )
        else:
            content = [
                f'          <p class="fallback-note">Generic control: {component_type}</p>',
            ]
            if has_interaction:
                content.append(
                    f'          <button class="secondary-button" type="button"{trigger}>{label}</button>'
                )

        if has_interaction and component.component_type != "status_panel":
            content.append(
                '          <output class="action-feedback" aria-live="polite"></output>'
            )

        renderer_kind = (
            component.component_type
            if component.component_type in SUPPORTED_COMPONENT_TYPES
            else "fallback"
        )
        return "\n".join(
            (
                f'          <article class="component component-{_escape(renderer_kind)}" '
                f'id="{component_id}" data-component-id="{component_id}" '
                f'data-component-type="{component_type}" '
                f'data-renderer-kind="{_escape(renderer_kind)}" '
                f'data-has-interaction="{str(has_interaction).lower()}">',
                '            <div class="component-copy">',
                f"              <h3>{label}</h3>",
                f"              <p>{purpose}</p>",
                "            </div>",
                *content,
                "          </article>",
            )
        )

    def _render_javascript(self, page_spec: PageSpec) -> str:
        runtime_data = {
            "component_sections": {
                item.component_id: item.section_id for item in page_spec.components
            },
            "initial_state_id": next(
                (
                    item.state_id
                    for item in page_spec.states
                    if item.name == "initial"
                ),
                page_spec.states[0].state_id,
            ),
            "interactions": [
                {
                    "action": item.action,
                    "interaction_id": item.interaction_id,
                    "source_state_id": item.source_state_id,
                    "target_state_id": item.target_state_id,
                    "trigger_component_id": item.trigger_component_id,
                    "user_feedback": item.user_feedback,
                }
                for item in page_spec.interactions
            ],
            "page_id": page_spec.page_id,
            "states": [
                {
                    "description": item.description,
                    "name": item.name,
                    "state_id": item.state_id,
                    "visible_component_ids": item.visible_component_ids,
                }
                for item in page_spec.states
            ],
        }
        data = _javascript_json(runtime_data)
        return f'''"use strict";

const PAGE_DATA = Object.freeze({data});
const statesById = new Map(PAGE_DATA.states.map((state) => [state.state_id, state]));
const interactionsById = new Map(
  PAGE_DATA.interactions.map((interaction) => [interaction.interaction_id, interaction]),
);
let currentStateId = PAGE_DATA.initial_state_id;

function updateFeedback(interaction) {{
  const triggerRoot = document.getElementById(interaction.trigger_component_id);
  const section = triggerRoot ? triggerRoot.closest("[data-section-id]") : null;
  const inlineFeedback = triggerRoot
    ? triggerRoot.querySelector(".action-feedback")
    : null;
  if (inlineFeedback) {{
    inlineFeedback.textContent = interaction.user_feedback;
    inlineFeedback.dataset.interactionId = interaction.interaction_id;
  }}
  const localFeedback = section
    ? section.querySelector('[data-component-type="status_panel"] .component-feedback')
    : null;
  const feedback = localFeedback || document.querySelector(".component-feedback");
  if (feedback) {{
    feedback.textContent = interaction.user_feedback;
    feedback.dataset.interactionId = interaction.interaction_id;
  }}
  if (section) {{
    section.dataset.lastInteractionId = interaction.interaction_id;
  }}
}}

function applyState(stateId, message) {{
  const state = statesById.get(stateId);
  if (!state) {{
    throw new Error(`Unknown PageSpec state: ${{stateId}}`);
  }}
  currentStateId = stateId;
  document.body.dataset.stateId = state.state_id;
  document.body.dataset.stateName = state.name;

  const stateRegion = document.getElementById("page-state");
  stateRegion.dataset.stateId = state.state_id;
  stateRegion.dataset.stateName = state.name;
  stateRegion.querySelector("strong").textContent = state.name;
  stateRegion.querySelector(".state-message").textContent = message || state.description;

  const visible = new Set(state.visible_component_ids);
  document.querySelectorAll("[data-component-id]").forEach((element) => {{
    element.hidden = !visible.has(element.dataset.componentId);
  }});
}}

function runInteraction(interaction) {{
  updateFeedback(interaction);
  applyState(interaction.target_state_id, interaction.user_feedback);
}}

function runForComponent(componentId) {{
  const candidates = PAGE_DATA.interactions.filter(
    (interaction) => interaction.trigger_component_id === componentId,
  );
  const interaction =
    candidates.find((candidate) => candidate.source_state_id === currentStateId) ||
    candidates[0];
  if (interaction) {{
    runInteraction(interaction);
  }}
}}

document.addEventListener("click", (event) => {{
  const trigger = event.target.closest("[data-interaction-trigger]");
  if (!trigger || (trigger.tagName === "INPUT" && trigger.type === "file")) {{
    return;
  }}
  event.preventDefault();
  runForComponent(trigger.dataset.interactionTrigger);
}});

document.addEventListener("change", (event) => {{
  const trigger = event.target.closest("input[type=file][data-interaction-trigger]");
  if (trigger) {{
    runForComponent(trigger.dataset.interactionTrigger);
  }}
}});

document.addEventListener("submit", (event) => {{
  const form = event.target.closest("[data-interaction-form]");
  if (!form) {{
    return;
  }}
  event.preventDefault();
  const requiredInput = form.querySelector("input[required], textarea[required], select[required]");
  const component = form.closest("[data-component-id]");
  const inlineFeedback = component ? component.querySelector(".action-feedback") : null;
  if (requiredInput && !requiredInput.value.trim()) {{
    requiredInput.setAttribute("aria-invalid", "true");
    if (inlineFeedback) {{
      inlineFeedback.textContent = "Enter the required information before submitting.";
    }}
    requiredInput.focus();
    runForComponent(form.dataset.interactionForm);
    return;
  }}
  if (requiredInput) {{
    requiredInput.removeAttribute("aria-invalid");
  }}
  runForComponent(form.dataset.interactionForm);
}});

window.Req2WebRenderer = Object.freeze({{
  getState: () => currentStateId,
  transition: (interactionId) => {{
    const interaction = interactionsById.get(interactionId);
    if (!interaction) {{
      throw new Error(`Unknown PageSpec interaction: ${{interactionId}}`);
    }}
    runInteraction(interaction);
  }},
}});

applyState(PAGE_DATA.initial_state_id);
'''

    @staticmethod
    def _render_css() -> str:
        return '''/* Deterministic offline styles for req2web.page_spec.v1 */
:root {
  color-scheme: light;
  --canvas: #f4f6fb;
  --surface: #ffffff;
  --surface-soft: #eef2ff;
  --ink: #172033;
  --muted: #64708a;
  --line: #dbe2ef;
  --accent: #4f46e5;
  --accent-strong: #3730a3;
  --success: #087f5b;
  --error: #c92a2a;
  --empty: #b35c00;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }
body {
  min-width: 320px;
  margin: 0;
  color: var(--ink);
  background: radial-gradient(circle at top left, #e0e7ff 0, transparent 34rem), var(--canvas);
}
button, input { font: inherit; }
button { cursor: pointer; }
[hidden] { display: none !important; }

.page-shell {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding: 40px 0 24px;
}
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  padding: clamp(24px, 5vw, 48px);
  border: 1px solid rgba(255,255,255,.78);
  border-radius: 28px;
  background: rgba(255,255,255,.86);
  box-shadow: 0 24px 70px rgba(45,55,90,.12);
}
.hero-copy { max-width: 720px; min-width: 0; }
.eyebrow, .section-kicker {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: .75rem;
  font-weight: 800;
  letter-spacing: .12em;
  text-transform: uppercase;
}
h1 { margin: 0; font-size: clamp(2rem, 5vw, 4.2rem); line-height: 1.02; }
.summary { margin: 18px 0 0; color: var(--muted); font-size: 1.05rem; line-height: 1.75; }
.page-meta { display: grid; gap: 10px; min-width: 170px; margin: 0; }
.page-meta div { min-width: 0; padding: 13px 15px; border-radius: 14px; background: var(--surface-soft); }
.page-meta dt { color: var(--muted); font-size: .72rem; }
.page-meta dd { min-width: 0; margin: 3px 0 0; font-weight: 800; overflow-wrap: anywhere; }
.page-state {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto auto 1fr;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 12px 16px;
  border-radius: 14px;
  background: #edf2ff;
  color: #364152;
}
.state-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }
.state-message { min-width: 0; color: var(--muted); overflow-wrap: anywhere; }

.section-list { display: grid; gap: 20px; margin-top: 20px; }
.page-section {
  display: grid;
  grid-template-columns: minmax(190px, .7fr) minmax(0, 1.5fr);
  gap: 24px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: var(--surface);
  box-shadow: 0 12px 35px rgba(45,55,90,.07);
}
.section-heading, .component-copy { min-width: 0; }
.section-heading h2 { margin: 0; font-size: 1.35rem; overflow-wrap: anywhere; }
.section-kicker { line-height: 1.5; text-transform: none; letter-spacing: .03em; overflow-wrap: anywhere; }
.component-list { display: grid; gap: 12px; }
.component {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fbfcff;
}
.component-copy h3 { margin: 0; font-size: 1rem; overflow-wrap: anywhere; }
.component-copy p { margin: 5px 0 0; color: var(--muted); line-height: 1.55; overflow-wrap: anywhere; }

input[type="text"], input[type="search"] {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid #cbd3e1;
  border-radius: 10px;
  color: var(--ink);
  background: #fff;
}
button, .file-control {
  min-height: 42px;
  padding: 10px 15px;
  border: 0;
  border-radius: 10px;
  color: #fff;
  background: var(--accent);
  font-weight: 750;
  text-align: center;
  overflow-wrap: anywhere;
}
button:hover, button:focus-visible, .file-control:hover { background: var(--accent-strong); }
.secondary-button { color: var(--accent-strong); background: #e8eaff; }
.inline-control { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
.compact-form { display: grid; gap: 9px; }
.compact-form label { font-weight: 750; }
.media-input { width: 100%; color: var(--muted); }
.file-control { display: inline-grid; place-items: center; width: fit-content; }
.metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.metric-grid div { display: grid; gap: 2px; padding: 14px; border-radius: 12px; background: #eef2ff; }
.metric-grid strong { font-size: 1.45rem; }
.metric-grid span { color: var(--muted); font-size: .8rem; }
.location-preview { position: relative; min-height: 110px; overflow: hidden; border-radius: 12px; background: linear-gradient(135deg,#e7f5ff,#d3f9d8); }
.location-preview::before, .location-preview::after { content: ""; position: absolute; width: 140%; height: 2px; background: rgba(23,32,51,.18); transform: rotate(-20deg); top: 45%; left: -20%; }
.location-preview::after { transform: rotate(28deg); top: 60%; }
.location-preview span { position: absolute; z-index: 1; width: 18px; height: 18px; border: 5px solid var(--accent); border-radius: 50% 50% 50% 0; transform: rotate(-45deg); left: 50%; top: 42%; }
.component-status_panel { background: #f5f7ff; }
.component-feedback { display: block; color: var(--muted); line-height: 1.6; }
.action-feedback { display: block; color: var(--accent-strong); font-size: .86rem; font-weight: 700; line-height: 1.5; overflow-wrap: anywhere; }
.action-feedback:empty { display: none; }
.component-fallback { border-style: dashed; border-color: #d19a35; }
.fallback-note { margin: 0; color: var(--empty); font-weight: 750; overflow-wrap: anywhere; }
.page-footer { display: flex; justify-content: space-between; gap: 12px; padding: 18px 4px 0; color: var(--muted); font-size: .78rem; }
.page-footer span { min-width: 0; overflow-wrap: anywhere; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

body[data-state-name="success"] .page-state { color: #075c44; background: #dff7ed; }
body[data-state-name="success"] .state-dot { background: var(--success); }
body[data-state-name="error"] .page-state { color: #9c2020; background: #fff0f0; }
body[data-state-name="error"] .state-dot { background: var(--error); }
body[data-state-name="error"] .component-feedback { color: var(--error); font-weight: 700; }
body[data-state-name="empty"] .page-state { color: #8a4800; background: #fff4e6; }
body[data-state-name="empty"] .state-dot { background: var(--empty); }
body[data-state-name="empty"] .component-feedback { color: var(--empty); }
body[data-state-name="loading"] .state-dot { animation: pulse 1s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .35; transform: scale(.7); } }

body[data-target-device="mobile"] .page-shell { width: min(100% - 20px, 620px); padding-top: 10px; }
body[data-target-device="mobile"] .hero,
body[data-target-device="mobile"] .page-section { grid-template-columns: 1fr; border-radius: 20px; }
body[data-target-device="desktop"] .section-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
body[data-target-device="desktop"] .page-section { grid-template-columns: 1fr; align-content: start; }

@media (min-width: 900px) {
  body[data-target-device="responsive"] .section-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  body[data-target-device="responsive"] .page-section { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .page-shell { width: min(100% - 20px, 620px); padding-top: 10px; }
  .hero, .page-section { grid-template-columns: 1fr; padding: 20px; border-radius: 20px; }
  .page-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); min-width: 0; }
  .page-state { grid-template-columns: auto 1fr; }
  .state-message { grid-column: 1 / -1; }
  .inline-control { grid-template-columns: 1fr; }
  .page-footer { flex-direction: column; }
}
'''
