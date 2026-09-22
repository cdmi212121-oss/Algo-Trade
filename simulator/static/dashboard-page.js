const SPOT_POLL_MS = 5000;

async function pollSpot() {
  try {
    const data = await getJSON("/api/spot");
    for (const el of document.querySelectorAll(".stat-tile[data-symbol]")) {
      const symbol = el.dataset.symbol;
      const d = data[symbol];
      if (!d || d.ltp === undefined) continue;
      document.getElementById(`value-${symbol}`).textContent = fmtNumber(d.ltp);
      const deltaEl = document.getElementById(`delta-${symbol}`);
      const up = d.net_change >= 0;
      deltaEl.textContent = `${up ? "+" : ""}${fmtNumber(d.net_change)} (${up ? "+" : ""}${(d.pct_change ?? 0).toFixed(2)}%)`;
      deltaEl.className = "stat-delta " + (up ? "up" : "down");
      renderSparkline(document.getElementById(`spark-${symbol}`), d.sparkline);
    }
    const updatedEl = document.getElementById("updated-at");
    if (data.updated_at) {
      updatedEl.textContent = "updated " + data.updated_at.split("T")[1];
    }
  } catch (e) {
    document.getElementById("updated-at").textContent = "connection error";
  }
}

async function pollSummary() {
  const [positions, trades] = await Promise.all([getJSON("/api/positions"), getJSON("/api/trades")]);
  document.getElementById("summary-open").textContent = positions.filter((p) => p.status === "OPEN").length;
  const pnlEl = document.getElementById("summary-pnl");
  pnlEl.textContent = fmtNumber(trades.summary.total_pnl);
  pnlEl.className = "stat-value " + (trades.summary.total_pnl >= 0 ? "up" : "down");
  document.getElementById("summary-equity").textContent = fmtNumber(trades.summary.current_equity);
  document.getElementById("summary-winrate").textContent = trades.summary.win_rate + "%";
}

async function loadEquityCurve() {
  const curve = await getJSON("/api/pnl_curve");
  const svg = document.getElementById("pnl-chart");
  renderLineChart(svg, curve.map((p) => ({ x: p.t, y: p.equity })), { width: 900, height: 240, emptyLabel: "No closed trades yet" });
}

let chart, series;

function showChartMessage(text) {
  const container = document.getElementById("candles-chart");
  let el = container.querySelector(".chart-placeholder");
  if (!el) {
    el = document.createElement("div");
    el.className = "chart-placeholder";
    container.appendChild(el);
  }
  el.textContent = text;
  el.hidden = false;
}

function hideChartMessage() {
  const el = document.getElementById("candles-chart").querySelector(".chart-placeholder");
  if (el) el.hidden = true;
}

function initCandleChart() {
  const container = document.getElementById("candles-chart");

  if (typeof LightweightCharts === "undefined") {
    showChartMessage("Chart library failed to load from the CDN — check your internet connection, then reload this page.");
    return false;
  }

  const style = getComputedStyle(document.body);
  chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 360,
    layout: { background: { color: "transparent" }, textColor: style.getPropertyValue("--text-secondary").trim() },
    grid: {
      vertLines: { color: style.getPropertyValue("--border").trim() },
      horzLines: { color: style.getPropertyValue("--border").trim() },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
  });
  series = chart.addCandlestickSeries({
    upColor: "#17a35a", downColor: "#e0393f",
    borderUpColor: "#17a35a", borderDownColor: "#e0393f",
    wickUpColor: "#17a35a", wickDownColor: "#e0393f",
  });
  new ResizeObserver((entries) => {
    for (const entry of entries) {
      if (entry.contentRect.width > 0) chart.applyOptions({ width: entry.contentRect.width });
    }
  }).observe(container);
  return true;
}

async function loadCandles() {
  if (!series) return;
  const symbol = document.getElementById("candle-symbol").value;
  const interval = document.getElementById("candle-interval").value;
  try {
    const data = await getJSON(`/api/candles/${symbol}?interval=${interval}`);
    if (!data.length) {
      showChartMessage(`No candles for ${symbol} yet — the dashboard needs to run a while to build up price history (it polls every few seconds).`);
      series.setData([]);
      return;
    }
    hideChartMessage();
    series.setData(data);
    chart.timeScale().fitContent();
  } catch (e) {
    showChartMessage("Could not load candle data: " + e);
  }
}

document.getElementById("candle-symbol").addEventListener("change", loadCandles);
document.getElementById("candle-interval").addEventListener("change", loadCandles);

window.addEventListener("DOMContentLoaded", () => {
  if (initCandleChart()) loadCandles();
  pollSpot();
  pollSummary();
  loadEquityCurve();
  setInterval(pollSpot, SPOT_POLL_MS);
  setInterval(pollSummary, 10000);
  setInterval(loadCandles, 30000);
  setInterval(loadEquityCurve, 20000);
});
