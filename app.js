const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "600519.SS", "000001.SZ", "0700.HK"];
const RANGE_LABELS = {
  "1d": "\u0031\u5929",
  "1wk": "\u0031\u5468",
  "1mo": "\u0031\u4e2a\u6708",
  "3mo": "\u0033\u4e2a\u6708",
  "6mo": "\u0036\u4e2a\u6708",
  "1y": "\u0031\u5e74"
};

const els = {
  authPanel: document.querySelector("#authPanel"),
  emailInput: document.querySelector("#emailInput"),
  passwordInput: document.querySelector("#passwordInput"),
  loginBtn: document.querySelector("#loginBtn"),
  registerBtn: document.querySelector("#registerBtn"),
  logoutBtn: document.querySelector("#logoutBtn"),
  appNav: document.querySelector(".app-nav"),
  authHint: document.querySelector("#authHint"),
  settingsHint: document.querySelector("#settingsHint"),
  userStatus: document.querySelector("#userStatus"),
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
  rangeTabs: document.querySelector(".range-tabs"),
  equityRangeTabs: document.querySelector(".equity-range-tabs"),
  equityChart: document.querySelector("#equityChart"),
  prevPageBtn: document.querySelector("#prevPageBtn"),
  nextPageBtn: document.querySelector("#nextPageBtn"),
  pageInfo: document.querySelector("#pageInfo"),
  exportTradesBtn: document.querySelector("#exportTradesBtn"),
  currentPasswordInput: document.querySelector("#currentPasswordInput"),
  newPasswordInput: document.querySelector("#newPasswordInput"),
  changePasswordBtn: document.querySelector("#changePasswordBtn"),
  statTotalTrades: document.querySelector("#statTotalTrades"),
  statWinRate: document.querySelector("#statWinRate"),
  statMaxProfit: document.querySelector("#statMaxProfit"),
  statMaxLoss: document.querySelector("#statMaxLoss"),
  statBuyValue: document.querySelector("#statBuyValue"),
  statSellValue: document.querySelector("#statSellValue")
};

const text = {
  realFeed: "\u771f\u5b9e\u884c\u60c5",
  feedFailed: "\u884c\u60c5\u83b7\u53d6\u5931\u8d25",
  quoteFailed: "\u771f\u5b9e\u884c\u60c5\u8fde\u63a5\u5931\u8d25",
  loginRequired: "\u8bf7\u5148\u767b\u5f55\u6216\u6ce8\u518c\u8d26\u6237\u3002",
  loadingAccount: "\u6b63\u5728\u52a0\u8f7d\u8d26\u6237...",
  historyFailed: "\u65e0\u6cd5\u52a0\u8f7d",
  trend: "\u8d70\u52bf",
  waitQuote: "\u7b49\u5f85\u771f\u5b9e\u884c\u60c5",
  waitChart: "\u7b49\u5f85\u771f\u5b9e\u8d70\u52bf\u6570\u636e",
  noPositions: "\u6682\u65e0\u6301\u4ed3",
  noHistory: "\u6682\u65e0\u4ea4\u6613\u8bb0\u5f55",
  shares: "\u80a1",
  avgPrice: "\u5747\u4ef7",
  marketValue: "\u5e02\u503c",
  pnl: "\u76c8\u4e8f",
  cash: "\u73b0\u91d1",
  deposit: "\u5165\u91d1",
  buy: "\u4e70\u5165",
  sell: "\u5356\u51fa",
  noPrice: "\u6ca1\u6709\u771f\u5b9e\u6700\u65b0\u4ef7\uff0c\u4e0d\u80fd\u6210\u4ea4\u3002",
  invalidOrder: "\u8bf7\u8f93\u5165\u6709\u6548\u6570\u91cf\u6216\u8ba2\u5355\u91d1\u989d\u3002",
  invalidDeposit: "\u8bf7\u8f93\u5165\u6709\u6548\u5165\u91d1\u91d1\u989d\u3002",
  filled: "\u5df2\u6309\u771f\u5b9e\u6700\u65b0\u4ef7",
  simulatedFill: "\u6a21\u62df\u6210\u4ea4",
  initialSaved: "\u521d\u59cb\u8d44\u91d1\u5df2\u4fdd\u5b58\u3002",
  deposited: "\u5df2\u589e\u52a0\u6a21\u62df\u8d44\u91d1",
  searching: "\u6b63\u5728\u641c\u7d22\u771f\u5b9e\u80a1\u7968...",
  noStock: "\u6ca1\u6709\u627e\u5230\u771f\u5b9e\u80a1\u7968",
  added: "\u5df2\u6dfb\u52a0",
  addFailed: "\u6dfb\u52a0\u5931\u8d25",
  loggedIn: "\u5df2\u767b\u5f55",
  loggedOut: "\u5df2\u767b\u51fa"
};

const moneyCache = new Map();
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const ctx = els.chart.getContext("2d");
const TRADE_PAGE_SIZE = 10;

let currentUser = null;
let state = emptyState();
let activeSide = "buy";
let activeRange = "1d";
let plottedPoints = [];
let pointerIndex = null;
let pollTimer;
let clockTimer;
let tradePage = 1;
let equityChart;
let activeEquityRange = "today";
let activePage = "dashboard";

function emptyState() {
  return {
    startingCash: 100000,
    cash: 100000,
    baseCurrency: "USD",
    activeSymbol: "AAPL",
    symbols: [...DEFAULT_SYMBOLS],
    quotes: {},
    histories: {},
    positions: {},
    trades: [],
    deposits: [],
    equityHistory: [],
    dailySnapshots: [],
    stats: {
      totalTrades: 0,
      winRate: 0,
      maxProfitTrade: 0,
      maxLossTrade: 0,
      totalBuyValue: 0,
      totalSellValue: 0
    }
  };
}

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

function fmtDateTime(value) {
  if (!value) return "--";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function fmtDate(value) {
  if (!value) return "--";
  return new Date(value).toLocaleDateString("zh-CN");
}

function percentText(value) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "--";
  return `${numberValue.toFixed(2)}%`;
}

function startOfLocalDay(date = new Date()) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function startOfLocalWeek(date = new Date()) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const day = start.getDay() || 7;
  start.setDate(start.getDate() - day + 1);
  return start.getTime();
}

function equityRangeStart(range) {
  const now = new Date();
  if (range === "today") return startOfLocalDay(now);
  if (range === "week") return startOfLocalWeek(now);
  if (range === "month") return new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  return 0;
}

function chartYAxisBounds(values) {
  const finite = values.filter((value) => Number.isFinite(value));
  if (!finite.length) return {};
  const minValue = Math.min(...finite);
  const maxValue = Math.max(...finite);
  if (finite.length === 1 || minValue === maxValue) {
    const base = Math.max(Math.abs(maxValue), 1);
    const padding = Math.max(base * 0.01, 100);
    return { min: maxValue - padding, max: maxValue + padding };
  }
  const padding = Math.max((maxValue - minValue) * 0.08, Math.abs(maxValue) * 0.002, 50);
  return { min: minValue - padding, max: maxValue + padding };
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {})
    }
  });
  const body = await response.text();
  let data;
  try {
    data = body ? JSON.parse(body) : {};
  } catch {
    throw new Error(`Server returned non-JSON response (${response.status}): ${body.slice(0, 120)}`);
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function apiGet(path) {
  return apiRequest(path);
}

function apiPost(path, payload = {}) {
  return apiRequest(path, { method: "POST", body: JSON.stringify(payload) });
}

function mergeServerState(nextState) {
  const quotes = state.quotes || {};
  const histories = state.histories || {};
  state = {
    ...emptyState(),
    ...nextState,
    quotes,
    histories,
    symbols: nextState.symbols?.length ? nextState.symbols : [...DEFAULT_SYMBOLS]
  };
  state.activeSymbol = state.activeSymbol || state.symbols[0] || "AAPL";
}

function historyKey(symbol = state.activeSymbol, range = activeRange) {
  return `${symbol}:${range}`;
}

function quoteMove(quote) {
  const change = quote.price - quote.previousClose;
  const pct = quote.previousClose ? (change / quote.previousClose) * 100 : 0;
  return { change, pct };
}

async function initAuth() {
  renderClock();
  try {
    const data = await apiGet("/api/me");
    if (data.user) {
      currentUser = data.user;
      await loadAccountState();
    } else {
      showLoggedOut();
    }
  } catch (error) {
    showLoggedOut(error.message);
  }
}

function showLoggedOut(message = text.loginRequired) {
  currentUser = null;
  state = emptyState();
  els.authPanel.hidden = false;
  els.userStatus.hidden = true;
  els.logoutBtn.hidden = true;
  els.authHint.textContent = message;
  els.feedStatus.textContent = text.loginRequired;
  els.feedStatus.className = "pill";
  render();
}

function showLoggedIn() {
  els.authPanel.hidden = true;
  els.userStatus.hidden = false;
  els.logoutBtn.hidden = false;
  els.userStatus.textContent = `${text.loggedIn}: ${currentUser.email || currentUser.display_name}`;
}

async function loadAccountState() {
  els.authHint.textContent = text.loadingAccount;
  const data = await apiGet("/api/state");
  currentUser = data.user;
  mergeServerState(data.state);
  showLoggedIn();
  render();
  await refreshQuotes();
}

async function loginOrRegister(mode) {
  const email = els.emailInput.value.trim();
  const password = els.passwordInput.value;
  els.authHint.textContent = mode === "login" ? "\u6b63\u5728\u767b\u5f55..." : "\u6b63\u5728\u6ce8\u518c...";
  try {
    await apiPost(mode === "login" ? "/api/auth/login" : "/api/auth/register", { email, password });
    els.passwordInput.value = "";
    await loadAccountState();
  } catch (error) {
    els.authHint.textContent = error.message;
  }
}

async function logout() {
  await apiPost("/api/auth/logout");
  showLoggedOut(text.loggedOut);
}

async function refreshQuotes() {
  if (!currentUser || !state.symbols.length) return;
  try {
    const data = await apiGet(`/api/quote?symbols=${encodeURIComponent(state.symbols.join(","))}`);
    data.quotes.forEach((quote) => {
      state.quotes[quote.symbol] = quote;
    });
    els.feedStatus.textContent = `${text.realFeed} ${new Date(data.serverTime).toLocaleTimeString("zh-CN", { hour12: false })}`;
    els.feedStatus.className = "pill api";
    render();
    await loadActiveHistory();
  } catch (error) {
    els.feedStatus.textContent = text.feedFailed;
    els.feedStatus.className = "pill error";
    els.tradeHint.textContent = `${text.quoteFailed}: ${error.message}`;
    render();
  }
}

async function loadActiveHistory() {
  if (!currentUser) return;
  const symbol = state.activeSymbol;
  try {
    const data = await apiGet(`/api/history?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(activeRange)}`);
    state.histories[historyKey(symbol, activeRange)] = data.points || [];
    renderMarket();
  } catch (error) {
    state.histories[historyKey(symbol, activeRange)] = [];
    els.tradeHint.textContent = `${text.historyFailed} ${symbol} ${RANGE_LABELS[activeRange]}${text.trend}: ${error.message}`;
    renderMarket();
  }
}

function render() {
  renderClock();
  renderPage();
  renderSettings();
  renderWatchlist();
  renderMarket();
  renderAccount();
  renderStats();
  renderPositions();
  renderHistory();
  renderEquityChart();
}

function renderPage() {
  document.querySelectorAll(".page-view").forEach((view) => {
    view.classList.toggle("active", view.dataset.view === activePage);
  });
  els.appNav.querySelectorAll("button[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === activePage);
  });
  if (activePage === "dashboard") {
    window.requestAnimationFrame(() => equityChart?.resize());
  }
  if (activePage === "trading") {
    window.requestAnimationFrame(() => renderMarket());
  }
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
    const row = document.createElement("div");
    row.className = `symbol-row ${symbol === state.activeSymbol ? "active" : ""}`;
    row.tabIndex = 0;
    row.role = "button";
    row.dataset.symbol = symbol;
    if (!quote) {
      row.innerHTML = `<strong>${symbol}</strong><span>--</span><button class="remove-symbol" type="button" title="Remove" data-symbol="${symbol}">&times;</button><small>${text.waitQuote}</small><small>--</small>`;
    } else {
      const move = quoteMove(quote);
      row.innerHTML = `
        <strong>${quote.symbol}</strong>
        <span class="${move.change >= 0 ? "up" : "down"}">${move.pct.toFixed(2)}%</span>
        <button class="remove-symbol" type="button" title="Remove" data-symbol="${quote.symbol}">&times;</button>
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
    els.activeName.textContent = text.waitQuote;
    els.activePrice.textContent = "--";
    els.activeMove.textContent = "--";
    drawChart([], true, "USD");
    return;
  }
  const move = quoteMove(quote);
  els.activeSymbol.textContent = quote.symbol;
  els.activeName.textContent = `${quote.name}${quote.exchange ? ` - ${quote.exchange}` : ""}${quote.marketState ? ` - ${quote.marketState}` : ""}`;
  els.activePrice.textContent = fmtMoney(quote.price, quote.currency);
  els.activeMove.textContent = `${fmtMoney(move.change, quote.currency)} ${move.pct.toFixed(2)}%`;
  els.activeMove.className = move.change >= 0 ? "up" : "down";
  drawChart(state.histories[historyKey()] || [], move.change >= 0, quote.currency);
}

function totalDeposits() {
  return (state.deposits || []).reduce((sum, item) => sum + Number(item.amount), 0);
}

function renderAccount() {
  const positionValue = Object.entries(state.positions).reduce((sum, [symbol, pos]) => {
    const quote = state.quotes[symbol];
    return sum + Number(pos.qty) * (quote?.price || Number(pos.avgPrice));
  }, 0);
  const equity = Number(state.cash) + positionValue;
  const pnl = equity - Number(state.startingCash) - totalDeposits();
  els.cashValue.textContent = fmtMoney(state.cash);
  els.positionValue.textContent = fmtMoney(positionValue);
  els.equityValue.textContent = fmtMoney(equity);
  els.pnlValue.textContent = fmtMoney(pnl);
  els.pnlValue.className = pnl >= 0 ? "up" : "down";
}

function renderStats() {
  const stats = state.stats || {};
  els.statTotalTrades.textContent = number.format(stats.totalTrades || 0);
  els.statWinRate.textContent = `${Number(stats.winRate || 0).toFixed(2)}%`;
  els.statMaxProfit.textContent = fmtMoney(stats.maxProfitTrade || 0);
  els.statMaxProfit.className = Number(stats.maxProfitTrade || 0) >= 0 ? "up" : "down";
  els.statMaxLoss.textContent = fmtMoney(stats.maxLossTrade || 0);
  els.statMaxLoss.className = Number(stats.maxLossTrade || 0) >= 0 ? "up" : "down";
  els.statBuyValue.textContent = fmtMoney(stats.totalBuyValue || 0);
  els.statSellValue.textContent = fmtMoney(stats.totalSellValue || 0);
}

function renderEquityChart() {
  if (!els.equityChart || !window.Chart) return;
  const rangeStart = equityRangeStart(activeEquityRange);
  const points = (state.equityHistory || [])
    .filter((point) => Number(point.time) >= rangeStart)
    .map((point) => ({ ...point, equity: Number(point.equity) }))
    .filter((point) => Number.isFinite(point.equity));
  const sourcePoints = points.length ? points : (state.equityHistory || [])
    .map((point) => ({ ...point, equity: Number(point.equity) }))
    .filter((point) => Number.isFinite(point.equity))
    .slice(-1);
  const labels = sourcePoints.map((point) => fmtDateTime(point.time));
  const values = sourcePoints.map((point) => point.equity);
  const yBounds = chartYAxisBounds(values);
  if (!equityChart) {
    equityChart = new Chart(els.equityChart, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "\u8d26\u6237\u603b\u8d44\u4ea7",
          data: values,
          borderColor: "#2867b2",
          backgroundColor: "rgba(40, 103, 178, 0.12)",
          fill: true,
          tension: 0.25,
          pointRadius: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => fmtMoney(item.parsed.y)
            }
          }
        },
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: {
            min: yBounds.min,
            max: yBounds.max,
            ticks: { callback: (value) => fmtMoney(value) }
          }
        }
      }
    });
    return;
  }
  equityChart.data.labels = labels;
  equityChart.data.datasets[0].data = values;
  equityChart.options.scales.y.min = yBounds.min;
  equityChart.options.scales.y.max = yBounds.max;
  equityChart.update();
}

function renderPositions() {
  const entries = Object.entries(state.positions).filter(([, pos]) => Number(pos.qty) > 0);
  if (!entries.length) {
    els.positions.className = "positions empty";
    els.positions.textContent = text.noPositions;
    return;
  }
  els.positions.className = "positions";
  els.positions.innerHTML = entries.map(([symbol, pos]) => {
    const quote = state.quotes[symbol];
    const price = quote?.price || Number(pos.avgPrice);
    const currency = quote?.currency || pos.currency || "USD";
    const value = Number(pos.qty) * price;
    const pnl = (price - Number(pos.avgPrice)) * Number(pos.qty);
    const invested = Number(pos.avgPrice) * Number(pos.qty);
    const pnlRate = invested ? (pnl / invested) * 100 : 0;
    const openedAt = Number(pos.openedAt);
    const holdingDays = openedAt ? Math.max(1, Math.ceil((Date.now() - openedAt) / 86400000)) : "--";
    return `
      <div class="position-row">
        <div><strong>${symbol}</strong><span>${number.format(pos.qty)} ${text.shares}</span></div>
        <div><span>${text.avgPrice}</span><span>${fmtMoney(pos.avgPrice, currency)}</span></div>
        <div><span>\u5f53\u524d\u4ef7\u683c</span><span>${fmtMoney(price, currency)}</span></div>
        <div><span>${text.marketValue}</span><span>${fmtMoney(value, currency)}</span></div>
        <div><span>\u6d6e\u52a8\u76c8\u4e8f</span><span class="${pnl >= 0 ? "up" : "down"}">${fmtMoney(pnl, currency)}</span></div>
        <div><span>\u6d6e\u52a8\u6536\u76ca\u7387</span><span class="${pnl >= 0 ? "up" : "down"}">${percentText(pnlRate)}</span></div>
        <div><span>\u6301\u4ed3\u5929\u6570</span><span>${holdingDays}</span></div>
        <div><span>\u4e70\u5165\u65f6\u95f4</span><span>${fmtDate(openedAt)}</span></div>
      </div>
    `;
  }).join("");
}

function renderHistory() {
  if (!state.trades.length && !state.deposits?.length) {
    els.historyBody.innerHTML = `<tr><td colspan="12">${text.noHistory}</td></tr>`;
    els.pageInfo.textContent = "1 / 1";
    els.prevPageBtn.disabled = true;
    els.nextPageBtn.disabled = true;
    return;
  }
  const trades = state.trades.map((trade) => ({ ...trade, kind: "trade" }));
  const deposits = (state.deposits || []).map((deposit) => ({ ...deposit, kind: "deposit" }));
  const rows = [...trades, ...deposits].sort((a, b) => b.time - a.time).slice(0, 160);
  const totalPages = Math.max(1, Math.ceil(rows.length / TRADE_PAGE_SIZE));
  tradePage = Math.min(Math.max(1, tradePage), totalPages);
  const visibleRows = rows.slice((tradePage - 1) * TRADE_PAGE_SIZE, tradePage * TRADE_PAGE_SIZE);
  els.historyBody.innerHTML = visibleRows.map((item) => {
    if (item.kind === "deposit") {
      return `
        <tr>
          <td>D-${item.id}</td>
          <td>${fmtDateTime(item.time)}</td>
          <td>${text.cash}</td>
          <td class="up">${text.deposit}</td>
          <td>--</td>
          <td>--</td>
          <td>${fmtMoney(item.amount)}</td>
          <td>--</td>
          <td>--</td>
          <td>--</td>
          <td>--</td>
          <td>--</td>
        </tr>
      `;
    }
    const equityChange = Number(item.equityChange || 0);
    return `
      <tr>
        <td>${item.id}</td>
        <td>${fmtDateTime(item.time)}</td>
        <td>${item.symbol}</td>
        <td class="${item.side === "buy" ? "up" : "down"}">${item.side === "buy" ? text.buy : text.sell}</td>
        <td>${number.format(item.qty)}</td>
        <td>${fmtMoney(item.price, item.currency)}</td>
        <td>${fmtMoney(item.value, item.currency)}</td>
        <td>${item.accountBalanceAfter == null ? "--" : fmtMoney(item.accountBalanceAfter)}</td>
        <td>${item.positionQtyAfter == null ? "--" : number.format(item.positionQtyAfter)}</td>
        <td>${item.equityBefore == null ? "--" : fmtMoney(item.equityBefore)}</td>
        <td>${item.equityAfter == null ? "--" : fmtMoney(item.equityAfter)}</td>
        <td class="${equityChange >= 0 ? "up" : "down"}">${item.equityChange == null ? "--" : fmtMoney(item.equityChange)}</td>
      </tr>
    `;
  }).join("");
  els.pageInfo.textContent = `${tradePage} / ${totalPages}`;
  els.prevPageBtn.disabled = tradePage <= 1;
  els.nextPageBtn.disabled = tradePage >= totalPages;
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
    ctx.fillText(text.waitChart, 28, 40);
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
  els.tradeBtn.textContent = side === "buy" ? text.buy : text.sell;
  els.tradeBtn.classList.toggle("sell", side === "sell");
}

function setHint(message) {
  els.tradeHint.textContent = message;
  if (els.settingsHint) els.settingsHint.textContent = message;
}

function setPage(page) {
  activePage = page || "dashboard";
  renderPage();
  if (activePage === "trading") {
    pointerIndex = null;
    els.chartTooltip.hidden = true;
    renderMarket();
    loadActiveHistory();
  }
}

window.setPage = setPage;

async function placeTrade() {
  const quote = state.quotes[state.activeSymbol];
  if (!quote) {
    setHint(text.noPrice);
    return;
  }
  const notional = Number(els.notionalInput.value);
  let qty = Number(els.quantityInput.value);
  if (notional > 0) qty = notional / quote.price;
  if (!Number.isFinite(qty) || qty <= 0) {
    setHint(text.invalidOrder);
    return;
  }

  try {
    const data = await apiPost("/api/trade", { symbol: state.activeSymbol, side: activeSide, qty });
    mergeServerState(data.state);
    state.quotes[state.activeSymbol] = quote;
    setHint(`${text.filled} ${fmtMoney(data.fill.price, data.fill.currency)} ${text.simulatedFill} ${number.format(data.fill.qty)} ${text.shares}\u3002`);
    els.notionalInput.value = "";
    render();
  } catch (error) {
    setHint(error.message);
  }
}

async function saveSettings() {
  try {
    const nextCash = Number(els.startingCashInput.value);
    const data = await apiPost("/api/account/reset", { startingCash: nextCash });
    mergeServerState(data.state);
    setHint(text.initialSaved);
    render();
    await refreshQuotes();
  } catch (error) {
    setHint(error.message);
  }
}

async function depositCash() {
  const amount = Number(els.depositInput.value);
  if (!Number.isFinite(amount) || amount <= 0) {
    setHint(text.invalidDeposit);
    return;
  }
  try {
    const data = await apiPost("/api/account/deposit", { amount });
    mergeServerState(data.state);
    els.depositInput.value = "";
    setHint(`${text.deposited} ${fmtMoney(amount)}\u3002`);
    render();
  } catch (error) {
    setHint(error.message);
  }
}

async function resetAccount() {
  await saveSettings();
}

async function addSymbol() {
  const query = els.symbolInput.value.trim();
  if (!query) return;
  setHint(text.searching);
  try {
    const data = await apiGet(`/api/search?q=${encodeURIComponent(query)}`);
    const normalized = query.toUpperCase();
    const exact = data.results.find((item) => item.symbol.toUpperCase() === normalized);
    const picked = exact || data.results[0];
    if (!picked) {
      setHint(`${text.noStock}: ${query}`);
      return;
    }
    const next = await apiPost("/api/watchlist", { symbol: picked.symbol });
    mergeServerState(next.state);
    state.activeSymbol = picked.symbol;
    pointerIndex = null;
    els.chartTooltip.hidden = true;
    els.symbolInput.value = "";
    setHint(`${text.added} ${picked.symbol} - ${picked.name}`);
    render();
    await refreshQuotes();
  } catch (error) {
    setHint(`${text.addFailed}: ${error.message}`);
  }
}
async function setActiveSymbol(symbol) {
  state.activeSymbol = symbol;
  pointerIndex = null;
  els.chartTooltip.hidden = true;
  render();
  try {
    await apiPost("/api/account/active-symbol", { symbol });
  } catch (error) {
    setHint(error.message);
  }
  await loadActiveHistory();
}

async function removeSymbol(symbol) {
  try {
    const data = await apiRequest(`/api/watchlist?symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
    mergeServerState(data.state);
    pointerIndex = null;
    els.chartTooltip.hidden = true;
    render();
    await refreshQuotes();
  } catch (error) {
    setHint(error.message);
  }
}

async function clearHistory() {
  try {
    const data = await apiPost("/api/history/clear");
    mergeServerState(data.state);
    tradePage = 1;
    render();
  } catch (error) {
    setHint(error.message);
  }
}

function csvCell(value) {
  const textValue = String(value ?? "");
  return `"${textValue.replace(/"/g, '""')}"`;
}

function exportTrades() {
  const rows = [
    ["\u65f6\u95f4", "\u80a1\u7968", "\u4e70\u5356\u65b9\u5411", "\u6570\u91cf", "\u4ef7\u683c", "\u91d1\u989d", "\u6210\u4ea4\u540e\u4f59\u989d"],
    ...state.trades.map((trade) => [
      fmtDateTime(trade.time),
      trade.symbol,
      trade.side === "buy" ? text.buy : text.sell,
      Number(trade.qty),
      Number(trade.price),
      Number(trade.value),
      trade.accountBalanceAfter == null ? "" : Number(trade.accountBalanceAfter)
    ])
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `trades-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function changePassword() {
  const currentPassword = els.currentPasswordInput.value;
  const newPassword = els.newPasswordInput.value;
  if (!currentPassword || newPassword.length < 6) {
    setHint("\u8bf7\u8f93\u5165\u5f53\u524d\u5bc6\u7801\uff0c\u5e76\u8bbe\u7f6e\u81f3\u5c11 6 \u4f4d\u7684\u65b0\u5bc6\u7801\u3002");
    return;
  }
  try {
    await apiPost("/api/auth/password", { currentPassword, newPassword });
    els.currentPasswordInput.value = "";
    els.newPasswordInput.value = "";
    setHint("\u5bc6\u7801\u5df2\u66f4\u65b0\u3002");
  } catch (error) {
    setHint(error.message);
  }
}

els.loginBtn.addEventListener("click", () => loginOrRegister("login"));
els.registerBtn.addEventListener("click", () => loginOrRegister("register"));
els.logoutBtn.addEventListener("click", logout);
els.appNav.querySelectorAll("button[data-page]").forEach((button) => {
  button.addEventListener("click", () => setPage(button.dataset.page));
});
els.passwordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loginOrRegister("login");
});
els.watchlist.addEventListener("click", (event) => {
  const remove = event.target.closest(".remove-symbol");
  if (remove) {
    event.stopPropagation();
    removeSymbol(remove.dataset.symbol);
    return;
  }
  const row = event.target.closest(".symbol-row");
  if (row) setActiveSymbol(row.dataset.symbol);
});
els.watchlist.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const row = event.target.closest(".symbol-row");
    if (row) setActiveSymbol(row.dataset.symbol);
  }
});
els.rangeTabs.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-range]");
  if (!button) return;
  activeRange = button.dataset.range;
  pointerIndex = null;
  els.chartTooltip.hidden = true;
  els.rangeTabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderMarket();
  await loadActiveHistory();
});
els.equityRangeTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-equity-range]");
  if (!button) return;
  activeEquityRange = button.dataset.equityRange;
  els.equityRangeTabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderEquityChart();
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
els.clearHistoryBtn.addEventListener("click", clearHistory);
els.exportTradesBtn.addEventListener("click", exportTrades);
els.changePasswordBtn.addEventListener("click", changePassword);
els.newPasswordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") changePassword();
});
els.prevPageBtn.addEventListener("click", () => {
  tradePage = Math.max(1, tradePage - 1);
  renderHistory();
});
els.nextPageBtn.addEventListener("click", () => {
  tradePage += 1;
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
initAuth();
pollTimer = window.setInterval(refreshQuotes, 15000);
clockTimer = window.setInterval(renderClock, 1000);
window.addEventListener("beforeunload", () => {
  window.clearInterval(pollTimer);
  window.clearInterval(clockTimer);
});
