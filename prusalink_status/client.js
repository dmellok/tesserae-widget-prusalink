// prusalink_status, Spectra status archetype. While a job is loaded the
// hero is a ring showing how far the print has got, with the state, the
// file name and the times beside it. With no job the ring gives way to
// the plain status hero. Temperatures sit underneath as a status grid.
// The two larger sizes also show the slicer's preview of the model, which
// server.py hands over as a data: URI.
//
// With the "Lead with: Model preview" option the picture becomes the hero:
// it fills the card on a sunken stage, and progress drops to a bar beneath
// it. A file with no picture falls back to the ring.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TONE_ACCENT = {
  printing: "var(--accent-3)",
  complete: "var(--accent-3)",
  paused: "var(--accent-2)",
  attention: "var(--accent-2)",
  error: "var(--accent-1)",
  stopped: "var(--text-secondary)",
  busy: "var(--text-secondary)",
  idle: "var(--text-secondary)",
  offline: "var(--text-muted)",
};

const TONE_ICON = {
  printing: "ph-printer",
  complete: "ph-check-circle",
  paused: "ph-pause",
  attention: "ph-hand-waving",
  error: "ph-warning-circle",
  stopped: "ph-stop-circle",
  busy: "ph-hourglass",
  idle: "ph-printer",
  offline: "ph-plug",
};

// What to say under the state when there is no job to name.
const TONE_SUB = {
  offline: "Printer is off or out of reach",
  error: "Check the printer's screen",
  attention: "The printer is waiting for you",
  busy: "Working on something",
};

function fmtSecs(s) {
  if (!Number.isFinite(s) || s < 0) return "-";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

function fmtTemp(t) {
  if (t == null) return "-";
  const v = Number(t);
  return Number.isFinite(v) ? `${Math.round(v)}°` : "-";
}

function ringSvg(pct, accent) {
  const r = 38;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const filled = circ * (clamped / 100);
  return `
    <div class="pl-ring">
      <svg viewBox="-45 -45 90 90" aria-hidden="true">
        <g transform="rotate(-90)">
          <circle r="${r}" fill="none" stroke="var(--surface-sunken)" stroke-width="7"/>
          <circle r="${r}" fill="none" stroke="${accent}" stroke-width="7"
                  stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"/>
        </g>
        <text x="0" y="8" text-anchor="middle" font-size="${clamped >= 99.5 ? 20 : 24}" font-weight="900"
              fill="var(--text-primary)" font-family="var(--font-family)"
              font-variant-numeric="tabular-nums">${Math.round(clamped)}<tspan font-size="13" dx="1">%</tspan></text>
      </svg>
    </div>`;
}

function tempCell(label, reading, accent) {
  const target = Number(reading?.target);
  const heating = Number.isFinite(target) && target > 0;
  return `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${accent}">${fmtTemp(reading?.actual)}${
        heating ? `<small class="pl-target">/ ${fmtTemp(target)}</small>` : ""
      }</span>
    </div>`;
}

function plainCell(label, value) {
  return `
    <div class="status-cell pl-extra">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v">${escapeHtml(value)}</span>
    </div>`;
}

const LAYOUT = `
  .pl-hero {
    display: flex;
    align-items: center;
    gap: var(--space-4);
    flex: 1 1 auto;
    min-height: 0;
  }
  .pl-ring {
    flex: 0 0 auto;
    height: 100%;
    max-height: 13em;
    min-height: 4.5em;
    aspect-ratio: 1;
  }
  .pl-ring svg {
    width: 100%;
    height: 100%;
    display: block;
  }
  .pl-text {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .pl-state {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--fs-value);
    font-weight: var(--fw-black);
    line-height: var(--lh-tight);
  }
  .pl-state i {
    font-size: .9em;
  }
  .pl-job {
    font-weight: var(--fw-semi);
    color: var(--text-secondary);
    font-size: var(--fs-body);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pl-times {
    display: flex;
    gap: var(--space-4);
  }
  .pl-time .u-label {
    white-space: nowrap;
  }
  .pl-time {
    display: flex;
    flex-direction: column;
    gap: 0.1em;
  }
  .pl-time .v {
    font-size: var(--fs-lead);
    font-weight: var(--fw-bold);
    font-variant-numeric: tabular-nums;
  }
  .pl-preview {
    flex: 0 0 23%;
    align-self: stretch;
    min-width: 0;
    min-height: 0;
  }
  .pl-preview img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  .size-md .pl-preview {
    flex-basis: 21%;
  }
  .size-md .pl-times {
    gap: var(--space-3);
  }
  .size-md .pl-hero {
    gap: var(--space-3);
  }
  .size-sm .pl-preview,
  .size-xs .pl-preview {
    display: none;
  }
  .pl-target {
    font-size: .6em;
    color: var(--text-muted);
    font-weight: var(--fw-semi);
    margin-left: .3em;
  }
  .pl-grid {
    flex: 0 0 auto;
  }
  .pl-grid.cols-4 {
    grid-template-columns: repeat(4, 1fr);
  }
  .status-cell .v {
    font-variant-numeric: tabular-nums;
  }

  .status-cell .v,
  .pl-time .v {
    white-space: nowrap;
  }

  /* ---- Lead with the model preview ---- */
  .pl-stage {
    flex: 1 1 auto;
    min-height: 0;
    background: var(--surface-sunken);
    border-radius: var(--radius-0);
    padding: var(--space-3);
    display: flex;
  }
  .pl-stage img {
    width: 100%;
    height: 100%;
    min-height: 0;
    object-fit: contain;
    display: block;
  }
  .pl-foot {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .pl-headline {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }
  .pl-headline .pl-state {
    font-size: var(--fs-lead);
    flex: 0 0 auto;
  }
  .pl-headline .pl-job {
    flex: 1 1 auto;
    min-width: 0;
  }
  .pl-pct {
    margin-left: auto;
    flex: 0 0 auto;
    font-size: var(--fs-value);
    font-weight: var(--fw-black);
    line-height: var(--lh-tight);
    font-variant-numeric: tabular-nums;
  }
  .pl-pct small {
    font-size: .55em;
    margin-left: .1em;
  }
  .pl-bar {
    height: .7em;
    background: var(--surface-sunken);
    border-radius: var(--pill-radius, 0);
    overflow: hidden;
  }
  .pl-bar span {
    display: block;
    height: 100%;
  }
  .pl-facts {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: 1fr;
    gap: var(--space-4);
  }
  .is-preview.w-body {
    gap: var(--space-3);
  }
  .size-lg .pl-headline .pl-state {
    font-size: var(--fs-value);
  }
  .size-lg .pl-pct {
    font-size: var(--fs-jumbo);
  }
  .pl-brief {
    display: none;
    flex: 1 1 auto;
    min-width: 0;
    font-weight: var(--fw-semi);
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .size-md .pl-brief,
  .size-sm .pl-brief {
    display: block;
  }
  .size-md .pl-brief,
  .size-sm .pl-brief {
    font-size: var(--fs-label);
  }
  .size-md .pl-pct {
    font-size: var(--fs-lead);
  }
  .size-md .pl-headline .pl-job,
  .size-sm .pl-headline .pl-job,
  .size-md .pl-facts,
  .size-sm .pl-facts,
  .size-xs .pl-facts,
  .size-xs .pl-headline .pl-state span {
    display: none;
  }
  .size-sm .pl-pct {
    font-size: var(--fs-lead);
  }
  .size-sm .pl-stage,
  .size-xs .pl-stage {
    padding: var(--space-2);
  }
  .size-xs .is-preview.w-body {
    gap: var(--space-2);
  }
  .size-xs .pl-headline .pl-state,
  .size-xs .pl-pct {
    font-size: var(--fs-body);
  }
  .size-xs .pl-bar {
    height: .5em;
  }

  /* Medium cells are short for their type size: two times, smaller state. */
  .size-md .pl-state {
    font-size: var(--fs-lead);
  }
  .size-md .pl-time:nth-child(n+3) {
    display: none;
  }
  .size-md .pl-text {
    gap: var(--space-1);
  }

  /* Small cells: the ring and the state carry it; the rest goes. */
  .size-sm .pl-job,
  .size-xs .pl-job,
  .size-xs .pl-times,
  .size-xs .pl-grid,
  .size-xs .status-sub {
    display: none;
  }
  .size-sm .pl-state {
    font-size: var(--fs-lead);
  }
  .size-sm .pl-time:nth-child(n+3) {
    display: none;
  }
  .size-xs .pl-hero {
    flex-direction: column;
    justify-content: center;
    gap: var(--space-2);
  }
  .size-xs .pl-ring {
    height: auto;
    width: 62%;
    min-height: 0;
  }
  .size-xs .pl-state {
    font-size: var(--fs-body);
  }
  .size-xs .status-hero {
    flex-direction: column;
    justify-content: center;
    text-align: center;
    flex: 1 1 auto;
  }
  .size-xs .status-state {
    font-size: var(--fs-lead);
  }
  .size-sm .status-state {
    font-size: var(--fs-value);
  }
`;

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size || "md";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w size-${escapeHtml(size)}" data-widget="prusalink_status">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>PrusaLink</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const state = data.state || { text: "-", tone: "idle" };
  const tone = state.tone || "idle";
  const accent = TONE_ACCENT[tone] || "var(--text-secondary)";
  const icon = TONE_ICON[tone] || "ph-printer";
  const title = data.title || "Prusa";
  const job = data.job || null;
  const temps = data.temps || {};
  const hasRing = !!job && Number.isFinite(Number(job.completion));

  const times = [];
  if (job) {
    if (Number.isFinite(job.remaining) && job.remaining > 0) {
      times.push(["Left", fmtSecs(job.remaining)]);
    }
    if (job.eta) times.push(["Ends", job.eta]);
    if (Number.isFinite(job.elapsed)) times.push(["Elapsed", fmtSecs(job.elapsed)]);
  }
  const timesHtml = times.length
    ? `<div class="pl-times">${times.map(([label, value]) => `
        <span class="pl-time">
          <span class="u-label">${escapeHtml(label)}</span>
          <span class="v">${escapeHtml(value)}</span>
        </span>`).join("")}</div>`
    : "";

  // Only ever a data: URI built by server.py from a PNG or JPEG.
  const thumb = typeof job?.thumbnail === "string" && /^data:image\/(png|jpeg);base64,/.test(job.thumbnail)
    ? job.thumbnail
    : "";
  const preview = thumb && (size === "lg" || size === "md")
    ? `<div class="pl-preview"><img src="${escapeHtml(thumb)}" alt=""></div>`
    : "";

  // ---- Lead with the model preview ----
  if (data.hero === "preview" && thumb && hasRing) {
    const pct = Math.max(0, Math.min(100, Number(job.completion) || 0));
    const facts = [];
    if (Number.isFinite(job.remaining) && job.remaining > 0) facts.push(["Left", fmtSecs(job.remaining)]);
    if (job.eta) facts.push(["Ends", job.eta]);
    if (!job.eta && Number.isFinite(job.elapsed)) facts.push(["Elapsed", fmtSecs(job.elapsed)]);
    const factCells = facts.map(([label, value]) => `
      <span class="pl-time">
        <span class="u-label">${escapeHtml(label)}</span>
        <span class="v">${escapeHtml(value)}</span>
      </span>`);
    // The middle sizes have no room for a row of facts, so one line stands in.
    const brief = Number.isFinite(job.remaining) && job.remaining > 0
      ? `${fmtSecs(job.remaining)} left${job.eta ? `, ends ${job.eta}` : ""}`
      : (Number.isFinite(job.elapsed) ? `${fmtSecs(job.elapsed)} elapsed` : "");
    // Temperatures join the row where there is width for them.
    if (size === "lg") {
      factCells.push(tempCell("Nozzle", temps.tool, "var(--accent-1)"));
      factCells.push(tempCell("Bed", temps.bed, "var(--accent-2)"));
    }
    shadow.innerHTML = `
      ${css}
      <style>${LAYOUT}</style>
      <div class="w size-${escapeHtml(size)}" data-widget="prusalink_status">
        <div class="w-title">
          <i class="ph-bold ph-printer"></i>
          <h3>${escapeHtml(title)}</h3>
          ${data.time ? `<span class="w-title-meta">${escapeHtml(data.time)}</span>` : ""}
        </div>
        <div class="w-body is-preview">
          <div class="pl-stage"><img src="${escapeHtml(thumb)}" alt=""></div>
          <div class="pl-foot">
            <div class="pl-headline">
              <span class="pl-state" style="color:${accent}"><i class="ph-bold ${icon}"></i><span>${escapeHtml(state.text)}</span></span>
              <span class="pl-job">${escapeHtml(job.name || "")}</span>
              <span class="pl-brief">${escapeHtml(brief)}</span>
              <span class="pl-pct">${Math.round(pct)}<small>%</small></span>
            </div>
            <div class="pl-bar"><span style="width:${pct.toFixed(1)}%;background:${accent}"></span></div>
            ${factCells.length ? `<div class="pl-facts">${factCells.join("")}</div>` : ""}
          </div>
        </div>
      </div>`;
    return;
  }

  const hero = hasRing
    ? `
      <div class="pl-hero">
        ${ringSvg(job.completion, accent)}
        <div class="pl-text">
          <span class="pl-state" style="color:${accent}"><i class="ph-bold ${icon}"></i>${escapeHtml(state.text)}</span>
          <span class="pl-job">${escapeHtml(job.name || "")}</span>
          ${timesHtml}
        </div>
        ${preview}
      </div>`
    : `
      <div class="pl-hero">
        <div class="status-hero">
          <i class="ph-bold ${icon}" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(state.text)}</span>
            <span class="status-sub">${escapeHtml(job?.name || TONE_SUB[tone] || "Nothing printing")}</span>
          </div>
        </div>
      </div>`;

  // The largest size has room for two more readings while a job is running.
  const extras = [];
  if (size === "lg") {
    if (job && Number.isFinite(Number(data.z))) extras.push(plainCell("Height", `${Number(data.z).toFixed(1)} mm`));
    if (job && Number.isFinite(Number(data.speed))) extras.push(plainCell("Speed", `${Math.round(Number(data.speed))}%`));
  }
  const grid = tone === "offline"
    ? ""
    : `<div class="status-grid pl-grid${extras.length === 2 ? " cols-4" : ""}">
        ${tempCell("Nozzle", temps.tool, "var(--accent-1)")}
        ${tempCell("Bed", temps.bed, "var(--accent-2)")}
        ${extras.join("")}
      </div>`;

  shadow.innerHTML = `
    ${css}
    <style>${LAYOUT}</style>
    <div class="w size-${escapeHtml(size)}" data-widget="prusalink_status">
      <div class="w-title">
        <i class="ph-bold ph-printer"></i>
        <h3>${escapeHtml(title)}</h3>
        ${data.time ? `<span class="w-title-meta">${escapeHtml(data.time)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        ${hero}
        ${grid}
      </div>
    </div>`;
}
