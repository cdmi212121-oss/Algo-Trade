const SPOT_POLL_MS = 5000;

function fmtNumber(n) {
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function sparklinePath(values, width, height) {
  if (!values || values.length < 2) return { line: "", fill: "" };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * height;
    return [x, y];
  });
  const line = "M" + points.map((p) => p.join(",")).join(" L");
  const fill = line + ` L${width},${height} L0,${height} Z`;
  return { line, fill };
}

function renderSparkline(svgId, values) {
  const svg = document.getElementById(svgId);
  const { line, fill } = sparklinePath(values, 240, 48);
  svg.innerHTML = fill
    ? `<path class="fill" d="${fill}"></path><path class="line" d="${line}"></path>`
    : "";
}

async function pollSpot() {
  try {
    const res = await fetch("/api/spot");
    const data = await res.json();
    for (const symbol of Object.keys(data)) {
      if (symbol === "updated_at") continue;
      const d = data[symbol];
      if (!d || d.ltp === undefined) continue;

      const valueEl = document.getElementById(`value-${symbol}`);
      const deltaEl = document.getElementById(`delta-${symbol}`);
      if (valueEl) valueEl.textContent = fmtNumber(d.ltp);
      if (deltaEl) {
        const up = d.net_change >= 0;
        deltaEl.textContent = `${up ? "+" : ""}${fmtNumber(d.net_change)} (${up ? "+" : ""}${d.pct_change.toFixed(2)}%)`;
        deltaEl.className = "stat-delta " + (up ? "up" : "down");
      }
      renderSparkline(`spark-${symbol}`, d.sparkline);
    }
    const updated = document.getElementById("updated-at");
    if (data.updated_at) {
      updated.textContent = data.updated_at.startsWith("error")
        ? data.updated_at
        : "updated " + data.updated_at.split("T")[1];
    }
  } catch (e) {
    document.getElementById("updated-at").textContent = "connection error";
  }
}

function renderHistoryChart(points) {
  const svg = document.getElementById("history-chart");
  const width = 900, height = 260, padding = 30;
  if (!points || points.length < 2) {
    svg.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle">No data for this day</text>`;
    return;
  }
  const values = points.map((p) => p.ltp);
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const innerW = width - padding * 2, innerH = height - padding * 2;
  const stepX = innerW / (values.length - 1);

  const coords = values.map((v, i) => [
    padding + i * stepX,
    padding + innerH - ((v - min) / range) * innerH,
  ]);
  const line = "M" + coords.map((p) => p.join(",")).join(" L");
  const fill = line + ` L${padding + innerW},${padding + innerH} L${padding},${padding + innerH} Z`;

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const y = padding + innerH * f;
    const val = max - range * f;
    return `<line class="grid" x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}"></line>
            <text x="4" y="${y + 4}">${fmtNumber(val)}</text>`;
  }).join("");

  svg.innerHTML = `${gridLines}<path class="fill" d="${fill}"></path><path class="line" d="${line}"></path>`;
}

async function loadHistoryDates() {
  const symbol = document.getElementById("history-symbol").value;
  const res = await fetch(`/api/history/${symbol}/dates`);
  const dates = await res.json();
  const select = document.getElementById("history-date");
  select.innerHTML = dates.length
    ? dates.map((d) => `<option value="${d}">${d}</option>`).join("")
    : `<option value="">no data yet</option>`;
  await loadHistoryChart();
}

async function loadHistoryChart() {
  const symbol = document.getElementById("history-symbol").value;
  const date = document.getElementById("history-date").value;
  if (!date) { renderHistoryChart([]); return; }
  const res = await fetch(`/api/history/${symbol}?date=${date}`);
  const data = await res.json();
  renderHistoryChart(data.points);
}

async function loadTrades() {
  const res = await fetch("/api/trades");
  const data = await res.json();

  document.getElementById("summary-pnl").textContent = fmtNumber(data.summary.total_pnl);
  document.getElementById("summary-winrate").textContent = data.summary.win_rate + "%";
  document.getElementById("summary-count").textContent = data.summary.total_trades;

  const tbody = document.querySelector("#trades-table tbody");
  tbody.innerHTML = data.trades.map((t) => {
    const pnl = parseFloat(t.pnl);
    const cls = pnl >= 0 ? "pnl-up" : "pnl-down";
    return `<tr>
      <td>${t.exit_time || ""}</td>
      <td>${t.symbol || ""}</td>
      <td>${t.side || ""}</td>
      <td>${t.qty || ""}</td>
      <td>${t.entry_price || ""}</td>
      <td>${t.exit_price || ""}</td>
      <td>${t.exit_reason || ""}</td>
      <td class="${cls}">${fmtNumber(pnl)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">No trades yet</td></tr>`;
}

document.getElementById("history-symbol").addEventListener("change", loadHistoryDates);
document.getElementById("history-date").addEventListener("change", loadHistoryChart);

pollSpot();
setInterval(pollSpot, SPOT_POLL_MS);
loadHistoryDates();
loadTrades();
setInterval(loadTrades, 30000);
