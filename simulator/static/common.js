function fmtNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

async function getJSON(url) {
  const res = await fetch(url);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

function sparklinePath(values, width, height) {
  if (!values || values.length < 2) return { line: "", fill: "" };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => [i * stepX, height - ((v - min) / range) * height]);
  const line = "M" + points.map((p) => p.join(",")).join(" L");
  const fill = line + ` L${width},${height} L0,${height} Z`;
  return { line, fill };
}

function renderSparkline(svgEl, values) {
  const { line, fill } = sparklinePath(values, 240, 48);
  svgEl.innerHTML = fill ? `<path class="fill" d="${fill}"></path><path class="line" d="${line}"></path>` : "";
}

// Generic single-series line chart with a gridline/value axis, used by
// History and P&L. `points` is [{x: label, y: number}, ...].
function renderLineChart(svgEl, points, { width = 900, height = 260, padding = 34, emptyLabel = "No data" } = {}) {
  if (!points || points.length < 2) {
    svgEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svgEl.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle">${emptyLabel}</text>`;
    return;
  }
  svgEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const values = points.map((p) => p.y);
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const innerW = width - padding * 2, innerH = height - padding * 2;
  const stepX = innerW / (values.length - 1);

  const coords = values.map((v, i) => [padding + i * stepX, padding + innerH - ((v - min) / range) * innerH]);
  const line = "M" + coords.map((p) => p.join(",")).join(" L");
  const fill = line + ` L${padding + innerW},${padding + innerH} L${padding},${padding + innerH} Z`;

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const y = padding + innerH * f;
    const val = max - range * f;
    return `<line class="grid" x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}"></line>
            <text x="4" y="${y + 4}">${fmtNumber(val)}</text>`;
  }).join("");

  svgEl.innerHTML = `${gridLines}<path class="fill" d="${fill}"></path><path class="line" d="${line}"></path>`;
}

// ---- shared top bar (Oxygen budget, trade count, Safe to Trade) ----

async function pollTopbar() {
  try {
    const b = await getJSON("/api/topbar");
    document.getElementById("tb-trades").textContent = `${b.trades_used}/${b.trades_max} Trades`;
    document.getElementById("tb-oxygen-pct").textContent = b.oxygen_pct + "%";
    document.getElementById("tb-oxygen-bar").style.width = Math.max(0, Math.min(100, b.oxygen_pct)) + "%";

    const marketEl = document.getElementById("tb-market");
    if (marketEl) {
      marketEl.textContent = b.market_open_now ? "● Market Open" : "● Market Closed — last session's data";
      marketEl.className = "pill " + (b.market_open_now ? "pill-good" : "badge-neutral");
    }

    const safeEl = document.getElementById("tb-safe");
    if (!b.market_open_now) {
      safeEl.textContent = "⚠ Market Closed";
      safeEl.className = "pill pill-warning";
    } else {
      safeEl.textContent = b.safe_to_trade ? "✓ Safe to Trade" : "⚠ Trading Paused";
      safeEl.className = "pill " + (b.safe_to_trade ? "pill-good" : "pill-critical");
    }
  } catch (e) { /* keep last known values on transient errors */ }
}

async function pollProfileChip() {
  try {
    const p = await getJSON("/api/profile");
    const code = p.client_code || "?";
    document.getElementById("tb-avatar").textContent = code.slice(0, 1);
    document.getElementById("tb-clientcode").textContent = code;
  } catch (e) { /* ignore */ }
}

// ---- order event toasts ----

let _lastEventId = 0;

function showToast(type, title, message) {
  const stack = document.getElementById("toast-stack");
  const el = document.createElement("div");
  el.className = "toast " + type.toLowerCase();
  el.innerHTML = `<div class="toast-title"><span>${title}</span><button class="toast-close">&times;</button></div><div>${message}</div>`;
  el.querySelector(".toast-close").addEventListener("click", () => el.remove());
  stack.appendChild(el);
  setTimeout(() => el.remove(), 8000);
}

async function pollOrderEvents() {
  try {
    const events = await getJSON(`/api/order_events?since=${_lastEventId}`);
    for (const e of events) {
      _lastEventId = Math.max(_lastEventId, e.id);
      showToast(e.type, `Order #${e.trade_id} ${e.type}`, e.message);
    }
  } catch (e) { /* ignore */ }
}

function startSharedPolling() {
  pollTopbar();
  pollProfileChip();
  pollOrderEvents();
  setInterval(pollTopbar, 8000);
  setInterval(pollOrderEvents, 4000);
}

document.addEventListener("DOMContentLoaded", startSharedPolling);
