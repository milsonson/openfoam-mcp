"""Portal renderer for OpenFOAM workflow result pages."""

from __future__ import annotations

import html
import json
from typing import Any


def _safe_json_for_script(data: dict[str, Any]) -> str:
    serialized = json.dumps(data, ensure_ascii=False)
    return serialized.replace("</", "<\\/")


def render_portal_html(manifest: dict[str, Any]) -> str:
    """Render a lightweight, self-contained result portal page."""
    job_id = str(manifest.get("job_id", "unknown"))
    status = str(manifest.get("status", "unknown"))
    payload_json = _safe_json_for_script(manifest)
    title = f"OpenFOAM Job {job_id}"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f3f6f8;
      --panel: #ffffff;
      --ink: #10212b;
      --muted: #4f6470;
      --accent: #0f766e;
      --warn: #b45309;
      --danger: #b91c1c;
      --line: #d7e2e9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at top right, #d9edf1, var(--bg) 40%);
      color: var(--ink);
    }}
    .container {{
      max-width: 1080px;
      margin: 24px auto 60px;
      padding: 0 16px;
    }}
    .hero {{
      background: linear-gradient(130deg, #0b3b4d, #0f766e);
      color: #eef8fb;
      border-radius: 14px;
      padding: 18px 20px;
      box-shadow: 0 12px 30px rgba(15, 118, 110, 0.24);
    }}
    .hero h1 {{ margin: 0; font-size: 20px; }}
    .hero p {{ margin: 8px 0 0; opacity: 0.92; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
    }}
    .card h3 {{
      font-size: 13px;
      color: var(--muted);
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .card .value {{
      font-weight: 700;
      font-size: 17px;
      word-break: break-word;
    }}
    .status {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #d9f3ef;
      color: #115e59;
    }}
    .status.warn {{ background: #fff2d6; color: var(--warn); }}
    .status.fail {{ background: #fee2e2; color: var(--danger); }}
    .section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      margin-top: 16px;
    }}
    .section h2 {{
      margin: 0 0 10px;
      font-size: 16px;
    }}
    .small {{
      font-size: 12px;
      color: var(--muted);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 8px 6px;
    }}
    a.download {{
      display: inline-block;
      text-decoration: none;
      border: 1px solid #1f8b81;
      background: #e8faf7;
      color: #0f5f59;
      border-radius: 8px;
      padding: 4px 9px;
      font-weight: 600;
      font-size: 12px;
    }}
    .muted {{ color: var(--muted); }}
    .timeline {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 8px;
    }}
    .timeline li {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      background: #fbfdfe;
    }}
    .timeline .stage {{
      font-weight: 700;
      margin-right: 6px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <section class="hero">
      <h1>OpenFOAM Simulation Portal</h1>
      <p>Job <code>{html.escape(job_id)}</code></p>
      <p>Status: <span id="status-pill" class="status">{html.escape(status)}</span></p>
      <p class="small">Stage: <span id="stage-text">{html.escape(str(manifest.get("stage", "unknown")))}</span></p>
    </section>

    <section class="grid" id="summary-grid"></section>

    <section class="section">
      <h2>Artifacts</h2>
      <table>
        <thead>
          <tr><th>Name</th><th>Type</th><th>Size</th><th>Download</th></tr>
        </thead>
        <tbody id="artifact-body"></tbody>
      </table>
    </section>

    <section class="section">
      <h2>Warnings / Failures</h2>
      <div id="risk-box" class="muted">None</div>
    </section>

    <section class="section">
      <h2>Progress Timeline</h2>
      <ul id="timeline" class="timeline"></ul>
    </section>
  </div>

  <script>
    let payload = {payload_json};
    const terminalStatuses = new Set(["completed", "completed_with_warnings", "partial_failed", "failed", "needs_input"]);
    const accessToken = typeof payload.access_token === "string" ? payload.access_token : "";
    const summaryGrid = document.getElementById("summary-grid");
    const artifactBody = document.getElementById("artifact-body");
    const riskBox = document.getElementById("risk-box");
    const statusPill = document.getElementById("status-pill");
    const stageText = document.getElementById("stage-text");
    const timeline = document.getElementById("timeline");
    const timelineSeen = new Set();

    function setStatusVisual(status) {{
      statusPill.textContent = status || "unknown";
      statusPill.classList.remove("warn", "fail");
      const statusText = String(status || "unknown");
      if (statusText.includes("warning")) statusPill.classList.add("warn");
      if (statusText.includes("fail")) statusPill.classList.add("fail");
    }}

    function renderSummary() {{
      summaryGrid.replaceChildren();
      const kpi = payload.kpi_summary || {{}};
      const quality = payload.quality_report || {{}};
      const summaryEntries = [
        ["Template", payload.template_id || "N/A"],
        ["Solver", payload.solver || "N/A"],
        ["Converged", kpi.converged === true ? "Yes" : (kpi.converged === false ? "No" : "N/A")],
        ["Residual Fields", Array.isArray(kpi.final_residual_fields) ? kpi.final_residual_fields.join(", ") : "N/A"],
        ["Preflight", quality.preflight_overall || "N/A"],
        ["Validation", quality.validation_passed === true ? "Passed" : (quality.validation_passed === false ? "Failed" : "N/A")]
      ];
      for (const [label, value] of summaryEntries) {{
        const card = document.createElement("article");
        card.className = "card";
        const title = document.createElement("h3");
        title.textContent = label;
        const body = document.createElement("div");
        body.className = "value";
        body.textContent = String(value);
        card.appendChild(title);
        card.appendChild(body);
        summaryGrid.appendChild(card);
      }}
    }}

    function safeDownloadUrl(url) {{
      if (!url) return null;
      try {{
        const parsed = new URL(String(url), window.location.origin);
        if (parsed.protocol === "http:" || parsed.protocol === "https:") {{
          return parsed.href;
        }}
      }} catch (_err) {{
        // Ignore invalid URLs.
      }}
      return null;
    }}

    function withToken(url) {{
      if (!accessToken) return String(url);
      try {{
        const parsed = new URL(String(url), window.location.origin);
        if (!parsed.searchParams.has("token")) {{
          parsed.searchParams.set("token", accessToken);
        }}
        return parsed.href;
      }} catch (_err) {{
        return String(url);
      }}
    }}

    function renderArtifacts() {{
      artifactBody.replaceChildren();
      const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
      if (artifacts.length === 0) {{
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 4;
        cell.className = "muted";
        cell.textContent = "No artifacts generated";
        row.appendChild(cell);
        artifactBody.appendChild(row);
        return;
      }}
      for (const item of artifacts) {{
        const row = document.createElement("tr");
        const size = item.size_bytes ? `${{item.size_bytes}} B` : "N/A";

        const nameCell = document.createElement("td");
        nameCell.textContent = String(item.name || "unknown");
        row.appendChild(nameCell);

        const typeCell = document.createElement("td");
        typeCell.textContent = String(item.type || "file");
        row.appendChild(typeCell);

        const sizeCell = document.createElement("td");
        sizeCell.textContent = size;
        row.appendChild(sizeCell);

        const actionCell = document.createElement("td");
        const safeUrl = safeDownloadUrl(withToken(item.url));
        if (safeUrl) {{
          const link = document.createElement("a");
          link.className = "download";
          link.href = safeUrl;
          link.setAttribute("download", "");
          link.rel = "noopener noreferrer";
          link.textContent = "Download";
          actionCell.appendChild(link);
        }} else {{
          actionCell.textContent = "N/A";
        }}
        row.appendChild(actionCell);

        artifactBody.appendChild(row);
      }}
    }}

    function renderRisks() {{
      const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      const failures = Array.isArray(payload.failures) ? payload.failures : [];
      const allRisks = [];
      if (warnings.length) allRisks.push("Warnings: " + warnings.join(", "));
      if (failures.length) allRisks.push("Failures: " + failures.join(", "));
      riskBox.textContent = allRisks.length ? allRisks.join(" | ") : "None";
    }}

    function appendTimelineEvent(evt) {{
      const stage = String(evt.stage || "unknown");
      const status = String(evt.status || "running");
      const message = String(evt.message || "");
      const ts = String(evt.timestamp || "");
      const key = `${{ts}}|${{stage}}|${{status}}|${{message}}`;
      if (timelineSeen.has(key)) return;
      timelineSeen.add(key);

      const li = document.createElement("li");
      const stageSpan = document.createElement("span");
      stageSpan.className = "stage";
      stageSpan.textContent = stage;

      const statusSpan = document.createElement("span");
      statusSpan.className = "small";
      statusSpan.textContent = status;

      const messageDiv = document.createElement("div");
      messageDiv.textContent = message || "";

      const timeDiv = document.createElement("div");
      timeDiv.className = "small";
      timeDiv.textContent = ts || "";

      li.appendChild(stageSpan);
      li.appendChild(document.createTextNode(" "));
      li.appendChild(statusSpan);
      li.appendChild(messageDiv);
      li.appendChild(timeDiv);
      timeline.prepend(li);
    }}

    function renderAll() {{
      setStatusVisual(payload.status);
      stageText.textContent = payload.stage || "unknown";
      renderSummary();
      renderArtifacts();
      renderRisks();
    }}

    async function refreshStatus() {{
      if (!payload.job_id) return;
      const resp = await fetch(withToken(`/jobs/${{encodeURIComponent(payload.job_id)}}/status`));
      if (!resp.ok) return;
      payload = await resp.json();
      renderAll();
    }}

    function startEventStream() {{
      if (!payload.job_id || typeof EventSource === "undefined") return false;
      const es = new EventSource(withToken(`/jobs/${{encodeURIComponent(payload.job_id)}}/events`));
      es.onmessage = (event) => {{
        if (!event.data) return;
        try {{
          const evt = JSON.parse(event.data);
          appendTimelineEvent(evt);
          refreshStatus();
          if (terminalStatuses.has(String(evt.status || ""))) {{
            es.close();
          }}
        }} catch (_err) {{
          // Ignore malformed event lines.
        }}
      }};
      es.onerror = () => {{
        es.close();
      }};
      return true;
    }}

    function startPollingFallback() {{
      if (!payload.job_id) return;
      const poll = async () => {{
        await refreshStatus();
        if (!terminalStatuses.has(String(payload.status || ""))) {{
          setTimeout(poll, 5000);
        }}
      }};
      setTimeout(poll, 5000);
    }}

    renderAll();
    const hasSSE = startEventStream();
    if (!hasSSE) {{
      startPollingFallback();
    }}
  </script>
</body>
</html>
"""
