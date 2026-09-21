let currentSymbol = null;
let currentChain = null;
let selected = null; // { strike, option_type, ltp, day_high, day_low, net_change, pct_change }
let orderMode = "MARKET";
let orderSide = "BUY";
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
    height: 320,
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
    for (const entry of entries) if (entry.contentRect.width > 0) chart.applyOptions({ width: entry.contentRect.width });
  }).observe(container);
  return true;
}

async function loadCandles() {
  if (!series || !currentSymbol) return;
  const interval = document.getElementById("candle-interval").value;
  try {
    const data = await getJSON(`/api/candles/${currentSymbol}?interval=${interval}`);
    if (!data.length) {
      showChartMessage(`No candles for ${currentSymbol} yet today — either the market's closed right now, or the engine hasn't polled enough ticks yet to build one.`);
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

async function loadWatchlistPrices() {
  const data = await getJSON("/api/spot");
  for (const el of document.querySelectorAll(".symbol-row")) {
    const symbol = el.dataset.symbol;
    const d = data[symbol];
    const px = document.getElementById(`wl-px-${symbol}`);
    if (d && d.ltp !== undefined) px.textContent = fmtNumber(d.ltp);
  }
}

function selectSymbol(symbol) {
  currentSymbol = symbol;
  document.querySelectorAll(".symbol-row").forEach((el) => el.classList.toggle("active", el.dataset.symbol === symbol));
  document.getElementById("chain-title").textContent = symbol + " Option Chain";
  selected = null;
  updateCurrentPriceCard();
  updateSubmitState();
  loadCandles();
  loadChain();
}

function renderChainTable(chain) {
  const strikes = [...new Set(chain.quotes.map((q) => q.strike))].sort((a, b) => a - b);
  const byStrike = {};
  for (const q of chain.quotes) {
    byStrike[q.strike] = byStrike[q.strike] || {};
    byStrike[q.strike][q.option_type] = q;
  }
  const atm = chain.spot
    ? strikes.reduce((a, b) => (Math.abs(b - chain.spot) < Math.abs(a - chain.spot) ? b : a), strikes[0])
    : null;

  const tbody = document.querySelector("#chain-table tbody");
  tbody.innerHTML = strikes.map((strike) => {
    const ce = byStrike[strike].CE;
    const pe = byStrike[strike].PE;
    const isAtm = strike === atm;
    const ceSel = selected && selected.strike === strike && selected.option_type === "CE";
    const peSel = selected && selected.strike === strike && selected.option_type === "PE";
    return `<tr class="option-row ${isAtm ? "atm" : ""} ${ceSel ? "selected-ce" : ""} ${peSel ? "selected-pe" : ""}">
      <td class="muted">${ce ? fmtNumber(ce.oi) : ""}</td>
      <td data-strike="${strike}" data-type="CE" class="chain-cell"><b>${ce ? fmtNumber(ce.ltp) : "—"}</b></td>
      <td class="muted small">CE</td>
      <td><strong>${fmtNumber(strike)}</strong></td>
      <td class="muted small">PE</td>
      <td data-strike="${strike}" data-type="PE" class="chain-cell"><b>${pe ? fmtNumber(pe.ltp) : "—"}</b></td>
      <td class="muted">${pe ? fmtNumber(pe.oi) : ""}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".chain-cell").forEach((cell) => {
    cell.addEventListener("click", () => selectOption(parseFloat(cell.dataset.strike), cell.dataset.type));
  });
}

function selectOption(strike, optionType) {
  const quote = currentChain.quotes.find((q) => q.strike === strike && q.option_type === optionType);
  if (!quote) return;
  selected = quote;
  renderChainTable(currentChain);
  updateCurrentPriceCard();
  updateSubmitState();

  document.getElementById("f-lots").value = 1;
  document.getElementById("f-sl").value = (quote.ltp * 0.8).toFixed(2);
  document.getElementById("f-target").value = (quote.ltp * 1.3).toFixed(2);
  document.getElementById("f-trigger").value = quote.ltp.toFixed(2);
  document.getElementById("form-message").textContent = "";
}

function updateCurrentPriceCard() {
  const src = selected || (currentChain ? { ltp: currentChain.spot, day_high: null, day_low: null, net_change: 0, pct_change: 0 } : null);
  if (!src) {
    document.getElementById("cp-ltp").textContent = "—";
    document.getElementById("cp-change").textContent = "";
    document.getElementById("cp-high").textContent = "—";
    document.getElementById("cp-low").textContent = "—";
    return;
  }
  document.getElementById("cp-ltp").textContent = fmtNumber(src.ltp);
  document.getElementById("cp-high").textContent = src.day_high ? fmtNumber(src.day_high) : "—";
  document.getElementById("cp-low").textContent = src.day_low ? fmtNumber(src.day_low) : "—";
  const up = (src.net_change || 0) >= 0;
  const changeEl = document.getElementById("cp-change");
  changeEl.textContent = `${up ? "+" : ""}${fmtNumber(src.net_change || 0)} (${(src.pct_change || 0).toFixed(2)}%)`;
  changeEl.className = "stat-delta " + (up ? "up" : "down");
}

const MONTHS = { JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5, JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11 };
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function expiryWithWeekday(expiryStr) {
  // Angel's format: "25SEP2026" (day, 3-letter month, year) - parsed here
  // rather than trusted against any assumed weekday, since NSE has changed
  // weekly expiry days over time (see strategy_rules.md §3's caveat).
  if (!expiryStr || expiryStr.length < 9) return expiryStr || "—";
  const day = parseInt(expiryStr.slice(0, 2), 10);
  const month = MONTHS[expiryStr.slice(2, 5).toUpperCase()];
  const year = parseInt(expiryStr.slice(5), 10);
  if (month === undefined) return expiryStr;
  const d = new Date(year, month, day);
  const daysLeft = Math.ceil((d - new Date()) / 86400000);
  return `${expiryStr} (${WEEKDAYS[d.getDay()]}, ${daysLeft}d)`;
}

async function loadChain() {
  if (!currentSymbol) return;
  const chain = await getJSON(`/api/option_chain/${currentSymbol}`);
  currentChain = chain;
  document.getElementById("chain-spot").textContent = chain.spot ? fmtNumber(chain.spot) : "waiting for data…";
  document.getElementById("chain-expiry").textContent = expiryWithWeekday(chain.expiry);
  document.getElementById("chain-lot").textContent = chain.lot_size || "—";
  if (chain.quotes.length) {
    renderChainTable(chain);
  } else {
    document.querySelector("#chain-table tbody").innerHTML =
      `<tr><td colspan="7" class="muted">No live option data for ${currentSymbol} right now — market may be closed, or the engine hasn't polled it yet.</td></tr>`;
  }
  if (!selected) updateCurrentPriceCard();
  applyStrikeSearch();
}

function applyStrikeSearch() {
  const query = document.getElementById("strike-search").value.trim();
  const rows = document.querySelectorAll("#chain-table tbody tr");
  if (!query) {
    rows.forEach((r) => { r.hidden = false; });
    return;
  }
  const target = parseFloat(query);
  rows.forEach((r) => {
    const strikeCell = r.querySelector("td:nth-child(4)");
    const strike = strikeCell ? parseFloat(strikeCell.textContent.replace(/,/g, "")) : NaN;
    r.hidden = Number.isNaN(strike) || Math.abs(strike - target) > 1;
  });
}

function updateSubmitState() {
  const btn = document.getElementById("f-submit");
  if (!selected) {
    btn.disabled = true;
    btn.textContent = "Select an option first";
  } else {
    btn.disabled = false;
    btn.textContent = "Execute Trade";
  }
}

function setSide(side) {
  orderSide = side;
  document.getElementById("side-buy").classList.toggle("active", side === "BUY");
  document.getElementById("side-sell").classList.toggle("active", side === "SELL");
}

function setOrderMode(mode) {
  orderMode = mode;
  document.querySelectorAll(".order-tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  document.getElementById("f-trigger-field").hidden = mode !== "GTT";
}

async function updateApprovalChecklist() {
  const budget = await getJSON("/api/topbar");
  const lots = parseInt(document.getElementById("f-lots").value, 10) || 0;
  const sl = parseFloat(document.getElementById("f-sl").value) || 0;
  const entry = selected ? selected.ltp : 0;
  const lotSize = currentChain ? currentChain.lot_size : 0;
  const tradeRisk = Math.abs(entry - sl) * lots * lotSize;

  const items = [
    { label: "Stop-loss present", ok: sl > 0 },
    { label: "Risk within allocation", ok: budget.oxygen_remaining <= 0 ? false : tradeRisk <= budget.oxygen_remaining },
    { label: "Trade count available", ok: budget.trades_used < budget.trades_max },
    { label: "Session unlocked", ok: budget.safe_to_trade },
  ];
  document.getElementById("approval-checklist").innerHTML = items.map((it) => `
    <div class="approval-item ${it.ok ? "ok" : "blocked"}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        ${it.ok ? '<circle cx="12" cy="12" r="10"/><path d="M8 12l3 3 5-6"/>' : '<circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/>'}
      </svg>
      ${it.label}
    </div>`).join("");

  document.getElementById("ap-budget").textContent = "₹" + fmtNumber(budget.per_trade_budget);
  document.getElementById("ap-risk").textContent = "₹" + fmtNumber(tradeRisk);
  document.getElementById("ap-oxygen").textContent = "₹" + fmtNumber(budget.oxygen_remaining);
}

async function submitTrade() {
  if (!selected || !currentChain) return;
  const lots = parseInt(document.getElementById("f-lots").value, 10);
  const qty = lots * currentChain.lot_size;
  const payload = {
    symbol: currentSymbol,
    strike: selected.strike,
    expiry: currentChain.expiry,
    option_type: selected.option_type,
    side: orderSide,
    qty,
    stop_loss: parseFloat(document.getElementById("f-sl").value),
    target: parseFloat(document.getElementById("f-target").value),
    order_type: orderMode,
    trigger_price: orderMode === "GTT" ? parseFloat(document.getElementById("f-trigger").value) : null,
  };
  const result = await postJSON("/api/trade", payload);
  const msg = document.getElementById("form-message");
  if (result.ok) {
    msg.textContent = result.pending
      ? `GTT order placed: ${result.symbol}`
      : `Position opened: ${result.symbol} @ ${result.entry_price}`;
    msg.style.color = "var(--good)";
  } else {
    msg.textContent = result.error;
    msg.style.color = "var(--critical)";
  }
  updateApprovalChecklist();
}

document.getElementById("symbol-list").addEventListener("click", (e) => {
  const row = e.target.closest(".symbol-row");
  if (row) selectSymbol(row.dataset.symbol);
});
document.getElementById("side-buy").addEventListener("click", () => setSide("BUY"));
document.getElementById("side-sell").addEventListener("click", () => setSide("SELL"));
document.querySelectorAll(".order-tab").forEach((t) => t.addEventListener("click", () => setOrderMode(t.dataset.mode)));
document.getElementById("candle-interval").addEventListener("change", loadCandles);
document.getElementById("f-submit").addEventListener("click", submitTrade);
document.getElementById("strike-search").addEventListener("input", applyStrikeSearch);
["f-lots", "f-sl"].forEach((id) => document.getElementById(id).addEventListener("input", updateApprovalChecklist));

window.addEventListener("DOMContentLoaded", () => {
  initCandleChart();
  loadWatchlistPrices();
  updateApprovalChecklist();
  const firstRow = document.querySelector(".symbol-row");
  if (firstRow) selectSymbol(firstRow.dataset.symbol);
  setInterval(loadWatchlistPrices, 5000);
  setInterval(loadChain, 5000);
  setInterval(updateApprovalChecklist, 8000);
});
