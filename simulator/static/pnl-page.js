async function loadSummary() {
  const data = await getJSON("/api/trades");
  const s = data.summary;
  document.getElementById("pnl-starting").textContent = fmtNumber(s.starting_capital);
  document.getElementById("pnl-equity").textContent = fmtNumber(s.current_equity);
  const totalEl = document.getElementById("pnl-total");
  totalEl.textContent = fmtNumber(s.total_pnl);
  totalEl.className = "stat-value " + (s.total_pnl >= 0 ? "up" : "down");
  document.getElementById("pnl-winrate").textContent = s.win_rate + "%";
  document.getElementById("pnl-count").textContent = s.total_trades;
}

async function loadCurve() {
  const curve = await getJSON("/api/pnl_curve");
  const svg = document.getElementById("pnl-chart");
  renderLineChart(svg, curve.map((p) => ({ x: p.t, y: p.equity })), { emptyLabel: "No closed trades yet" });
}

loadSummary();
loadCurve();
setInterval(loadSummary, 15000);
setInterval(loadCurve, 15000);
