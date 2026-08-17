(() => {
  "use strict";
  const data = window.REQ2WEB_REVIEW_DATA;
  if (!data || !Array.isArray(data.items)) throw new Error("Review data is unavailable.");
  const reannotation = window.REQ2WEB_LUNA_REANNOTATION;
  const recheckById = new Map((reannotation?.items || []).map((item) => [item.item_id, item]));
  const independentReview = window.REQ2WEB_INDEPENDENT_REVIEW;
  const independentById = new Map((independentReview?.items || []).map((item) => [item.item_id, item]));
  const storageKey = `req2web-luna-review:${data.source_luna_prelabels_sha256}`;
  const scale = data.relevance_scale;
  const state = JSON.parse(localStorage.getItem(storageKey) || "{}");
  let visible = [];
  let cursor = 0;
  let selectedGrade = null;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const save = () => localStorage.setItem(storageKey, JSON.stringify(state));
  const entry = (item) => state[item.item_id] || null;
  const stats = () => {
    const values = Object.values(state);
    const reviewed = values.filter((row) => row && Number.isInteger(row.final_relevance)).length;
    const pending = values.filter((row) => row?.review_status === "pending").length;
    const corrected = values.filter((row) => row?.review_status === "corrected").length;
    byId("reviewed-count").textContent = reviewed;
    byId("remaining-count").textContent = data.item_count - reviewed;
    byId("pending-count").textContent = pending;
    byId("corrected-count").textContent = corrected;
    byId("progress-bar").style.width = `${reviewed / data.item_count * 100}%`;
  };
  const populateFilters = () => {
    const cases = [...new Set(data.items.map((item) => item.case_id))].sort();
    const roles = [...new Set(data.items.map((item) => item.role))].sort();
    cases.forEach((value) => byId("case-filter").insertAdjacentHTML("beforeend", `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`));
    roles.forEach((value) => byId("role-filter").insertAdjacentHTML("beforeend", `<option value="${escapeHtml(value)}">${escapeHtml(value.replaceAll("_", " "))}</option>`));
  };
  const refreshVisible = (keepId = null) => {
    const status = byId("status-filter").value;
    const caseId = byId("case-filter").value;
    const role = byId("role-filter").value;
    const attention = byId("attention-filter").value;
    const independentFilter = byId("independent-filter").value;
    visible = data.items.filter((item) => {
      const row = entry(item);
      const independent = independentById.get(item.item_id);
      if (caseId !== "all" && item.case_id !== caseId) return false;
      if (role !== "all" && item.role !== role) return false;
      if (attention !== "all" && recheckById.get(item.item_id)?.attention_priority !== attention) return false;
      if (independentFilter === "strong" && (!independent || independent.absolute_delta < 2)) return false;
      if (independentFilter === "different" && (!independent || independent.absolute_delta < 1)) return false;
      if (independentFilter === "zero" && independent?.recommended_relevance !== 0) return false;
      if (independentFilter === "limited" && !["minimal_gesture_trace", "generic_responsive_prototype_metadata", "issue_only_without_fix_content", "path_only_implementation_stub"].includes(independent?.evidence_quality)) return false;
      if (status === "unreviewed" && row) return false;
      if (status === "pending" && row?.review_status !== "pending") return false;
      if (status === "corrected" && row?.review_status !== "corrected") return false;
      if (status === "low" && item.luna_judgment.confidence !== "low") return false;
      return true;
    });
    const located = keepId ? visible.findIndex((item) => item.item_id === keepId) : -1;
    cursor = located >= 0 ? located : Math.min(cursor, Math.max(0, visible.length - 1));
    render();
  };
  const factsHtml = (value) => Object.entries(value || {}).map(([key, raw]) => `<dt>${escapeHtml(key.replaceAll("_", " "))}</dt><dd>${escapeHtml(Array.isArray(raw) ? raw.join(", ") : typeof raw === "object" ? JSON.stringify(raw) : raw)}</dd>`).join("");
  const render = () => {
    stats();
    const hasItem = visible.length > 0;
    byId("empty-state").hidden = hasItem;
    byId("review-card").hidden = !hasItem;
    if (!hasItem) return;
    const item = visible[cursor];
    const judgment = item.luna_judgment;
    const saved = entry(item);
    selectedGrade = Number.isInteger(saved?.final_relevance) ? saved.final_relevance : judgment.suggested_relevance;
    byId("position").textContent = `${cursor + 1} of ${visible.length} · ${item.case_id} · ${item.role.replaceAll("_", " ")}`;
    byId("candidate-title").textContent = item.candidate.evidence.source_title || item.candidate.title || item.candidate.doc_id;
    byId("confidence").textContent = `${judgment.confidence} confidence`;
    const context = item.judgment_context;
    const uses = (context.use_cases || []).map((row) => `${row.title}: ${row.expected_outcome}`).join(" · ");
    byId("judgment-context").innerHTML = `<p>${escapeHtml(context.requirement_summary)}</p><dl class="facts"><dt>Device</dt><dd>${escapeHtml(context.target_device)}</dd><dt>Task</dt><dd>${escapeHtml(context.task_type)}</dd><dt>Role focus</dt><dd>${escapeHtml(context.role_focus)}</dd><dt>Use cases</dt><dd>${escapeHtml(uses)}</dd></dl>`;
    const evidence = item.candidate.evidence;
    byId("candidate-evidence").innerHTML = `<dl class="facts"><dt>Document</dt><dd>${escapeHtml(item.candidate.doc_id)}</dd><dt>Dataset</dt><dd>${escapeHtml(item.candidate.dataset)} / ${escapeHtml(item.candidate.subset)}</dd>${factsHtml(evidence.structured_facts)}</dl><p class="excerpt">${escapeHtml(evidence.content_excerpt)}</p>`;
    byId("luna-rationale").textContent = judgment.rationale;
    const recheck = recheckById.get(item.item_id);
    byId("reannotation-panel").hidden = !recheck;
    if (recheck) {
      const priorityCopy = {
        high: ["Review carefully", "The three grades differ by at least two points."],
        medium: ["Normal review", "The three grades differ by one point."],
        low: ["Quick consistency check", "All three independent grades agree."],
      }[recheck.attention_priority];
      byId("attention-badge").textContent = priorityCopy[0];
      byId("attention-badge").className = `attention-badge ${recheck.attention_priority}`;
      byId("three-run-grades").innerHTML = recheck.grades.map((grade, index) => `<span><small>Run ${index + 1}</small><strong>${grade}</strong></span>`).join("");
      byId("agreement-summary").textContent = `${priorityCopy[1]} Majority: ${recheck.majority_grade ?? "none"}.`;
      byId("independent-rationales").innerHTML = [recheck.independent_run_2, recheck.independent_run_3].map((row, index) => `<p><strong>Run ${index + 2}: ${row.suggested_relevance} · ${escapeHtml(row.confidence)}</strong><br>${escapeHtml(row.rationale)}</p>`).join("");
    }
    const independent = independentById.get(item.item_id);
    byId("independent-review-panel").hidden = !independent;
    if (independent) {
      byId("independent-grade").textContent = `Recommended ${independent.recommended_relevance}`;
      byId("independent-grade").className = `independent-grade grade-${independent.recommended_relevance}`;
      byId("independent-rationale").textContent = independent.rationale;
      byId("independent-quality").textContent = `Evidence: ${independent.evidence_quality.replaceAll("_", " ")}`;
      byId("independent-comparison").textContent = `Luna ${independent.luna_relevance} → strict ${independent.recommended_relevance}`;
    }
    byId("grade-buttons").innerHTML = [0,1,2,3].map((grade) => `<button type="button" data-grade="${grade}" aria-pressed="${grade === selectedGrade}" class="${grade === selectedGrade ? "selected" : ""}">${grade}<br><small>${escapeHtml(scale[String(grade)])}</small></button>`).join("");
    byId("grade-help").textContent = saved?.review_status === "pending"
      ? `Marked pending. Luna's suggested grade ${judgment.suggested_relevance} is selected until you resolve this item.`
      : saved
        ? `Saved as ${saved.review_status}.`
        : `Luna selected ${judgment.suggested_relevance}; change it only if the evidence warrants a correction.`;
    byId("grade-buttons").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
      selectedGrade = Number(button.dataset.grade);
      byId("grade-buttons").querySelectorAll("button").forEach((candidate) => {
        const isSelected = Number(candidate.dataset.grade) === selectedGrade;
        candidate.classList.toggle("selected", isSelected);
        candidate.setAttribute("aria-pressed", String(isSelected));
      });
      byId("grade-help").textContent = `Selected ${selectedGrade}; click Confirm and show next to save this decision.`;
    }));
    byId("use-independent-button").onclick = () => {
      if (!independent) return;
      selectedGrade = independent.recommended_relevance;
      byId("grade-buttons").querySelectorAll("button").forEach((button) => {
        const isSelected = Number(button.dataset.grade) === selectedGrade;
        button.classList.toggle("selected", isSelected);
        button.setAttribute("aria-pressed", String(isSelected));
      });
      byId("grade-help").textContent = `Selected strict recommendation ${selectedGrade}; click Confirm and show next to save your decision.`;
    };
  };
  const move = (offset) => { if (!visible.length) return; cursor = (cursor + offset + visible.length) % visible.length; render(); };
  byId("confirm-button").addEventListener("click", () => {
    const item = visible[cursor];
    const luna = item.luna_judgment.suggested_relevance;
    state[item.item_id] = { final_relevance: selectedGrade, review_status: selectedGrade === luna ? "confirmed" : "corrected" };
    save();
    const priorId = item.item_id;
    if (["pending", "unreviewed"].includes(byId("status-filter").value)) refreshVisible(); else { move(1); stats(); }
    if (!entry(item)) refreshVisible(priorId);
  });
  byId("clear-button").addEventListener("click", () => {
    const item = visible[cursor];
    state[item.item_id] = { review_status: "pending" };
    save();
    if (["unreviewed", "corrected"].includes(byId("status-filter").value)) refreshVisible(); else { move(1); stats(); }
  });
  byId("previous-button").addEventListener("click", () => move(-1));
  byId("next-button").addEventListener("click", () => move(1));
  ["status-filter", "case-filter", "role-filter", "attention-filter", "independent-filter"].forEach((id) => byId(id).addEventListener("change", () => refreshVisible()));
  byId("export-button").addEventListener("click", () => {
    const items = data.items.map((item) => {
      const row = entry(item);
      return Number.isInteger(row?.final_relevance) ? { item_id: item.item_id, luna_relevance: item.luna_judgment.suggested_relevance, final_relevance: row.final_relevance, review_status: row.review_status } : null;
    });
    const reviewed = items.filter(Boolean).length;
    const pending = data.items.filter((item) => entry(item)?.review_status === "pending").length;
    if (reviewed !== data.item_count) { alert(`Resolve all ${data.item_count} items before export. ${data.item_count - reviewed} remain, including ${pending} marked pending.`); return; }
    const packet = { schema_version: data.schema_version, status: "completed", source_request_sha256: data.source_request_sha256, source_luna_prelabels_sha256: data.source_luna_prelabels_sha256, reviewer_role: "project_owner", item_count: data.item_count, items };
    const blob = new Blob([JSON.stringify(packet, null, 2) + "\n"], {type: "application/json"});
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "req2web_retrieval_review_completed.json"; link.click(); URL.revokeObjectURL(link.href);
  });
  populateFilters(); refreshVisible();
})();
