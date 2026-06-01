const STORAGE_KEY = "paperTradingDesk.real.v2";
const LEGACY_KEY = "paperTradingDesk.real.v1";
const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "600519.SS", "000001.SZ", "0700.HK"];
const RANGE_LABELS = {
  "1d": "1天",
  "1wk": "1周",
  "1mo": "1个月",
  "3mo": "3个月",
  "6mo": "6个月",
  "1y": "1年"
};

const els = {
  feedStatus: document.querySelector("#feedStatus"),
  clock: document.querySelector("#clock"),
  equityValue: document.querySelector("#equityValue"),
  cashValue: document.querySelector("#cashValue"),
  positionValue: document.querySelector("#positionValue"),
  pnlValue: document.querySelector("#pnlValue"),
  activeSymbol: document.querySelector("#activeSymbol"),
  activeName: document.querySelector("#activeName"),
  activePrice: document.querySelector("#activePrice"),
  activeMove: document.querySelector("#activeMove"),
  chart: document.querySelector("#priceChart"),
  chartTooltip: document.querySelector("#chartTooltip"),
  watchlist: document.querySelector("#watchlist"),
  symbolInput: document.querySelector("#symbolInput"),
  addSymbolBtn: document.querySelector("#addSymbolBtn"),
  startingCashInput: document.querySelector("#startingCashInput"),
  depositInput: document.querySelector("#depositInput"),
  depositBtn: document.querySelector("#depositBtn"),
  saveSettingsBtn: document.querySelector("#saveSettingsBtn"),
  buyTab: document.querySelector("#buyTab"),
  sellTab: document.querySelector("#sellTab"),
  quantityInput: document.querySelector("#quantityInput"),
  notionalInput: document.querySelector("#notionalInput"),
  tradeBtn: document.querySelector("#tradeBtn"),
  tradeHint: document.querySelector("#tradeHint"),
  positions: document.querySelector("#positions"),
  historyBody: document.querySelector("#historyBody"),
  clearHistoryBtn: document.querySelector("#clearHistoryBtn"),
  resetBtn: document.querySelector("#resetBtn"),
  rangeTabs: document.querySelector(".range-tabs")
};

const moneyCache = new Map();
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const ctx = els.chart.getContext("2d");

let state = loadState();
let activeSide = "buy";
let activeRange = "1d";
let plottedPoints = [];
let pointerIndex = null;
let pollTimer;
let clockTimer;

function currencyFormatter(currency = "USD") {
  const code = currency || "USD";
  if (!moneyCache.has(code)) {
    moneyCache.set(code, new Intl.NumberFormat("en-US", { style: "currency", currency: code }));
  }
  return moneyCache.get(code);
}

function fmtMoney(value, currency = "USD") {
  return currencyFormatter(currency).format(Number(value) || 0);
}

function loadState() {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  const legacy = JSON.parse(localStorage.getItem(LEGACY_KEY) || "null");
  const data = saved || legacy;
  if (data) {
    const existing = (data.symbols || []).map(normalizeSymbol);
    data.symbols = existing.length ? [...new Set([...existing, ...DEFAULT_SYMBOLS])] : [...DEFAULT_SYMBOLS];
    data.quotes = data.quotes || {};
    data.histories = data.histories || {};
    data.deposits = data.deposits || [];
    return data;
  }
  return {
    startingCash: 100000,
    cash: 100000,
    activeSymbol: "AAPL",
    symbols: [...DEFAULT_SYMBOLS],
    quotes: {},
    histories: {},
    positions: {},
    trades: [],
    deposits: []
  };
}

function normalizeSymbol(symbol) {
  const value = String(symbol || "").trim().toUpperCase();
  if (value.endsWith(".HK")) {
    const code = value.slice(0, -3);
    if (/^\d{5}$/.test(code)) return `${code.slice(-4)}.HK`;
  }
  return value;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

async function apiGet(path) {
  const response = await fetch(path, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function historyKey(symbol = state.activeSymbol, range = activeRange) {
  return `${symbol}:${range}`;
}

function quoteMove(quote) {
  const change = quote.price - quote.previousClose;
  const pct = quote.previousClose ? (change / quote.previousClose) * 100 : 0;
  return { change, pct };
}

async function refreshQuotes() {
  if (!state.symbols.length) return;
  try {
    const data = await apiGet(`/api/quote?symbols=${encodeURIComponent(state.symbols.join(","))}`);
    data.quotes.forEach((quote) => {
      state.quotes[quote.symbol] = quote;
    });
    els.feedStatus.textContent = `真实行情 ${new Date(data.serverTime).toLocaleTimeString("zh-CN", { hour12: false })}`;
    els.feedStatus.className = "pill api";
    saveState();
    render();
    await loadActiveHistory();
  } catch (error) {
    els.feedStatus.textContent = "行情获取失败";
    els.feedStatus.className = "pill error";
    els.tradeHint.textContent = `真实行情连接失败：${error.message}`;
    render();
  }
}

async function loadActiveHistory() {
  const symbol = state.activeSymbol;
  try {
    const data = await apiGet(`/api/history?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(activeRange)}`);
    state.histories[historyKey(symbol, activeRange)] = data.points;
    saveState();
    renderMarket();
  } catch (error) {
    state.histories[historyKey(symbol, activeRange)] = [];
    els.tradeHint.textContent = `无法加载 ${symbol} ${RANGE_LABELS[activeRange]}走势：${error.message}`;
    renderMarket();
  }
}

function render() {
  renderClock();
  renderSettings();
  renderWatchlist();
  renderMarket();
  renderAccount();
  renderPositions();
  renderHistory();
}

function renderClock() {
  els.clock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function renderSettings() {
  els.startingCashInput.value = state.startingCash;
}

function renderWatchlist() {
  els.watchlist.innerHTML = "";
  state.symbols.forEach((symbol) => {
    const quote = state.quotes[symbol];
    const row = document.createElement("button");
    row.className = `symbol-row ${symbol === state.activeSymbol ? "active" : ""}`;
    row.type = "button";
    row.dataset.symbol = symbol;
    if (!quote) {
      row.innerHTML = `<strong>${symbol}</strong><span>--</span><small>等待真实行情</small><small>--</small>`;
    } else {
      const move = quoteMove(quote);
      row.innerHTML = `
        <strong>${quote.symbol}</strong>
        <span class="${move.change >= 0 ? "up" : "down"}">${move.pct.toFixed(2)}%</span>
        <small>${quote.name}</small>
        <small>${fmtMoney(quote.price, quote.currency)}</small>
      `;
    }
    els.watchlist.appendChild(row);
  });
}

function renderMarket() {
  const quote = state.quotes[state.activeSymbol];
  if (!quote) {
    els.activeSymbol.textContent = state.activeSymbol;
    els.activeName.textContent = "等待真实行情";
    els.activePrice.textContent = "--";
    els.activeMove.textContent = "--";
    drawChart([], true, "USD");
    return;
  }
  const move = quoteMove(quote);
  els.activeSymbol.textContent = quote.symbol;
  els.activeName.textContent = `${quote.name}${quote.exchange ? ` · ${quote.exchange}` : ""}${quote.marketState ? ` · ${quote.marketState}` : ""}`;
  els.activePrice.textContent = fmtMoney(quote.price, quote.currency);
  els.activeMove.textContent = `${fmtMoney(move.change, quote.currency)} ${move.pct.toFixed(2)}%`;
  els.activeMove.className = move.change >= 0 ? "up" : "down";
  drawChart(state.histories[historyKey()] || [], move.change >= 0, quote.currency);
}

function renderAccount() {
  const positionValue = Object.entries(state.positions).reduce((sum, [symbol, pos]) => {
    const quote = state.quotes[symbol];
    return sum + pos.qty * (quote?.price || pos.avgPrice);
  }, 0);
  const equity = state.cash + positionValue;
  const pnl = equity - state.startingCash - totalDeposits();
  els.cashValue.textContent = fmtMoney(state.cash);
  els.positionValue.textContent = fmtMoney(positionValue);
  els.equityValue.textContent = fmtMoney(equity);
  els.pnlValue.textContent = fmtMoney(pnl);
  els.pnlValue.className = pnl >= 0 ? "up" : "down";
}

function totalDeposits() {
  return (state.deposits || []).reduce((sum, item) => sum + item.amount, 0);
}

function renderPositions() {
  const entries = Object.entries(state.positions).filter(([, pos]) => pos.qty > 0);
  if (!entries.length) {
    els.positions.className = "positions empty";
    els.positions.textContent = "暂无持仓";
    return;
  }
  els.positions.className = "positions";
  els.positions.innerHTML = entries.map(([symbol, pos]) => {
    const quote = state.quotes[symbol];
    const price = quote?.price || pos.avgPrice;
    const currency = quote?.currency || pos.currency || "USD";
    const value = pos.qty * price;
    const pnl = (price - pos.avgPrice) * pos.qty;
    return `
      <div class="position-row">
        <div><strong>${symbol}</strong><span>${number.format(pos.qty)} 股</span></div>
        <div><span>均价</span><span>${fmtMoney(pos.avgPrice, currency)}</span></div>
        <div><span>市值</span><span>${fmtMoney(value, currency)}</span></div>
        <div><span>盈亏</span><span class="${pnl >= 0 ? "up" : "down"}">${fmtMoney(pnl, currency)}</span></div>
      </div>
    `;
  }).join("");
}

function renderHistory() {
  if (!state.trades.length && !state.deposits?.length) {
    els.historyBody.innerHTML = `<tr><td colspan="6">暂无交易记录</td></tr>`;
    return;
  }
  const trades = state.trades.map((trade) => ({ ...trade, kind: "trade" }));
  const deposits = (state.deposits || []).map((deposit) => ({ ...deposit, kind: "deposit" }));
  const rows = [...trades, ...deposits].sort((a, b) => b.time - a.time).slice(0, 160);
  els.historyBody.innerHTML = rows.map((item) => {
    if (item.kind === "deposit") {
      return `
        <tr>
          <td>${new Date(item.time).toLocaleString("zh-CN", { hour12: false })}</td>
          <td>现金</td>
          <td class="up">入金</td>
          <td>--</td>
          <td>--</td>
          <td>${fmtMoney(item.amount)}</td>
        </tr>
      `;
    }
    return `
      <tr>
        <td>${new Date(item.time).toLocaleString("zh-CN", { hour12: false })}</td>
        <td>${item.symbol}</td>
        <td class="${item.side === "buy" ? "up" : "down"}">${item.side === "buy" ? "买入" : "卖出"}</td>
        <td>${number.format(item.qty)}</td>
        <td>${fmtMoney(item.price, item.currency)}</td>
        <td>${fmtMoney(item.value, item.currency)}</td>
      </tr>
    `;
  }).join("");
}

function drawChart(points, positive = true, currency = "USD") {
  const dpr = window.devicePixelRatio || 1;
  const width = els.chart.width = Math.max(1, Math.floor(els.chart.clientWidth * dpr));
  const height = els.chart.height = Math.max(1, Math.floor(els.chart.clientHeight * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = width / dpr;
  const h = height / dpr;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#657166";
  ctx.font = "14px Segoe UI, Arial";
  plottedPoints = [];

  if (!points.length) {
    ctx.fillText("等待真实走势数据", 28, 40);
    return;
  }

  const pad = { top: 22, right: 32, bottom: 30, left: 34 };
  const prices = points.map((point) => point.p);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const innerW = w - pad.left - pad.right;
  const innerH = h - pad.top - pad.bottom;

  ctx.strokeStyle = "#dfe5dd";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const y = pad.top + (innerH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
  }

  ctx.beginPath();
  plottedPoints = points.map((point, index) => {
    const x = pad.left + (index / Math.max(1, points.length - 1)) * innerW;
    const y = h - pad.bottom - ((point.p - min) / range) * innerH;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
    return { ...point, x, y };
  });
  ctx.strokeStyle = positive ? "#167f55" : "#c23b43";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  ctx.fillStyle = "#657166";
  ctx.font = "12px Segoe UI, Arial";
  ctx.fillText(fmtMoney(max, currency), pad.left, 16);
  ctx.fillText(fmtMoney(min, currency), pad.left, h - 8);

  if (pointerIndex !== null && plottedPoints[pointerIndex]) {
    const point = plottedPoints[pointerIndex];
    ctx.strokeStyle = "#2867b2";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(point.x, pad.top);
    ctx.lineTo(point.x, h - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = "#2867b2";
    ctx.beginPath();
    ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
    ctx.fill();
  }
}

function showChartPoint(clientX) {
  if (!plottedPoints.length) return;
  const rect = els.chart.getBoundingClientRect();
  const x = Math.min(Math.max(clientX - rect.left, 0), rect.width);
  let closestIndex = 0;
  let closestDistance = Infinity;
  plottedPoints.forEach((point, index) => {
    const distance = Math.abs(point.x - x);
    if (distance < closestDistance) {
      closestDistance = distance;
      closestIndex = index;
    }
  });
  pointerIndex = closestIndex;
  const point = plottedPoints[closestIndex];
  const quote = state.quotes[state.activeSymbol];
  renderMarket();
  els.chartTooltip.hidden = false;
  els.chartTooltip.style.left = `${Math.min(Math.max(point.x, 78), rect.width - 78)}px`;
  els.chartTooltip.style.top = `${Math.max(18, point.y - 54)}px`;
  els.chartTooltip.innerHTML = `
    <strong>${fmtMoney(point.p, quote?.currency || "USD")}</strong>
    <span>${new Date(point.t).toLocaleString("zh-CN", { hour12: false })}</span>
  `;
}

function hideChartPoint() {
  pointerIndex = null;
  els.chartTooltip.hidden = true;
  renderMarket();
}

function setSide(side) {
  activeSide = side;
  els.buyTab.classList.toggle("active", side === "buy");
  els.sellTab.classList.toggle("active", side === "sell");
  els.tradeBtn.textContent = side === "buy" ? "买入" : "卖出";
  els.tradeBtn.classList.toggle("sell", side === "sell");
}

function placeTrade() {
  const symbol = state.activeSymbol;
  const quote = state.quotes[symbol];
  if (!quote) {
    els.tradeHint.textContent = "没有真实最新价，不能成交。";
    return;
  }
  const notional = Number(els.notionalInput.value);
  let qty = Number(els.quantityInput.value);
  if (notional > 0) qty = notional / quote.price;
  if (!Number.isFinite(qty) || qty <= 0) {
    els.tradeHint.textContent = "请输入有效数量或订单金额。";
    return;
  }

  const value = qty * quote.price;
  const pos = state.positions[symbol] || { qty: 0, avgPrice: 0, currency: quote.currency };

  if (activeSide === "buy") {
    if (value > state.cash) {
      els.tradeHint.textContent = "可用现金不足。";
      return;
    }
    const newQty = pos.qty + qty;
    pos.avgPrice = ((pos.qty * pos.avgPrice) + value) / newQty;
    pos.qty = newQty;
    pos.currency = quote.currency;
    state.positions[symbol] = pos;
    state.cash -= value;
  } else {
    if (qty > pos.qty) {
      els.tradeHint.textContent = "持仓数量不足。";
      return;
    }
    pos.qty -= qty;
    state.cash += value;
    if (pos.qty <= 0.000001) delete state.positions[symbol];
    else state.positions[symbol] = pos;
  }

  state.trades.unshift({
    time: Date.now(),
    symbol,
    side: activeSide,
    qty,
    price: quote.price,
    value,
    currency: quote.currency
  });

  els.tradeHint.textContent = `已按真实最新价 ${fmtMoney(quote.price, quote.currency)} 模拟成交 ${number.format(qty)} 股。`;
  els.notionalInput.value = "";
  saveState();
  render();
}

function saveSettings() {
  const nextCash = Number(els.startingCashInput.value);
  if (Number.isFinite(nextCash) && nextCash >= 100 && state.trades.length === 0 && !(state.deposits || []).length) {
    state.startingCash = nextCash;
    state.cash = nextCash;
    saveState();
    render();
    els.tradeHint.textContent = "初始资金已保存。";
  } else {
    els.tradeHint.textContent = "已有交易或入金记录时不能直接改初始资金，请重置账户后再设置。";
  }
}

function depositCash() {
  const amount = Number(els.depositInput.value);
  if (!Number.isFinite(amount) || amount <= 0) {
    els.tradeHint.textContent = "请输入有效入金金额。";
    return;
  }
  state.cash += amount;
  state.deposits = state.deposits || [];
  state.deposits.unshift({ time: Date.now(), amount });
  els.depositInput.value = "";
  els.tradeHint.textContent = `已增加模拟资金 ${fmtMoney(amount)}。`;
  saveState();
  render();
}

function resetAccount() {
  const startingCash = Number(els.startingCashInput.value) || 100000;
  const symbols = [...state.symbols];
  const quotes = { ...state.quotes };
  const histories = { ...state.histories };
  state = {
    startingCash,
    cash: startingCash,
    activeSymbol: symbols[0] || "AAPL",
    symbols,
    quotes,
    histories,
    positions: {},
    trades: [],
    deposits: []
  };
  saveState();
  render();
}

async function addSymbol() {
  const query = els.symbolInput.value.trim();
  if (!query) return;
  els.tradeHint.textContent = "正在搜索真实股票...";
  try {
    const data = await apiGet(`/api/search?q=${encodeURIComponent(query)}`);
    const normalized = query.toUpperCase();
    const exact = data.results.find((item) => item.symbol.toUpperCase() === normalized);
    const picked = exact || data.results[0];
    if (!picked) {
      els.tradeHint.textContent = `没有找到真实股票：${query}`;
      return;
    }
    if (!state.symbols.includes(picked.symbol)) state.symbols.push(picked.symbol);
    state.activeSymbol = picked.symbol;
    pointerIndex = null;
    els.chartTooltip.hidden = true;
    els.symbolInput.value = "";
    saveState();
    await refreshQuotes();
    els.tradeHint.textContent = `已添加 ${picked.symbol} · ${picked.name}`;
  } catch (error) {
    els.tradeHint.textContent = `添加失败：${error.message}`;
  }
}

els.watchlist.addEventListener("click", async (event) => {
  const row = event.target.closest(".symbol-row");
  if (!row) return;
  state.activeSymbol = row.dataset.symbol;
  pointerIndex = null;
  els.chartTooltip.hidden = true;
  saveState();
  render();
  await loadActiveHistory();
});
els.rangeTabs.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-range]");
  if (!button) return;
  activeRange = button.dataset.range;
  pointerIndex = null;
  els.chartTooltip.hidden = true;
  els.rangeTabs.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
  });
  renderMarket();
  await loadActiveHistory();
});
els.addSymbolBtn.addEventListener("click", addSymbol);
els.symbolInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") addSymbol();
});
els.buyTab.addEventListener("click", () => setSide("buy"));
els.sellTab.addEventListener("click", () => setSide("sell"));
els.tradeBtn.addEventListener("click", placeTrade);
els.saveSettingsBtn.addEventListener("click", saveSettings);
els.depositBtn.addEventListener("click", depositCash);
els.depositInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") depositCash();
});
els.resetBtn.addEventListener("click", resetAccount);
els.clearHistoryBtn.addEventListener("click", () => {
  state.trades = [];
  state.deposits = [];
  saveState();
  renderHistory();
});
els.chart.addEventListener("pointermove", (event) => showChartPoint(event.clientX));
els.chart.addEventListener("pointerdown", (event) => {
  els.chart.setPointerCapture(event.pointerId);
  showChartPoint(event.clientX);
});
els.chart.addEventListener("pointerleave", hideChartPoint);
window.addEventListener("resize", () => renderMarket());

render();
refreshQuotes();
pollTimer = window.setInterval(refreshQuotes, 15000);
clockTimer = window.setInterval(renderClock, 1000);
window.addEventListener("beforeunload", () => {
  window.clearInterval(pollTimer);
  window.clearInterval(clockTimer);
});
