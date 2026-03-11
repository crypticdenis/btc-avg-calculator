// ₿ Crypto Portfolio Widget for Scriptable (iOS)
// ================================================
// 1. Install "Scriptable" from the App Store (free)
// 2. Open Scriptable → tap "+" → paste this entire script
// 3. Name it "Crypto Portfolio"
// 4. Go to your home screen → long press → add widget → Scriptable
// 5. Choose the widget size → tap it → select "Crypto Portfolio"
//
// Portfolio data is fetched live from GitHub (portfolio.json) on every refresh.
// sync_widget.py on your Mac pushes updates automatically every hour.

// ─── Live portfolio from GitHub ─────────────────────────────
const PORTFOLIO_JSON_URL = "https://raw.githubusercontent.com/crypticdenis/btc-avg-calculator/main/portfolio.json";

async function loadPortfolio() {
  try {
    const req = new Request(PORTFOLIO_JSON_URL);
    // Bust cache so iOS doesn't serve a stale response
    req.headers = { "Cache-Control": "no-cache" };
    return await req.loadJSON();
  } catch (e) {
    console.log("⚠️ Could not fetch portfolio.json: " + e);
    // Fallback so the widget still renders if offline
    return [
      { asset: "BTC", amount: 0.03474734, totalCost: 2422.21, currency: "EUR", pair: "BTCEUR" },
      { asset: "SOL", amount: 14.37596880, totalCost: 1454.31, currency: "EUR", pair: "SOLEUR" },
    ];
  }
}
// ─────────────────────────────────────────────────────────────

const CURRENCY_SYMBOL = "€";

// Fetch live prices from Binance
async function getPrice(symbol) {
  try {
    const url = `https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`;
    const req = new Request(url);
    const res = await req.loadJSON();
    return parseFloat(res.price);
  } catch {
    return 0;
  }
}

// Format number with commas
function fmt(n, d = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

// Asset icons
function icon(asset) {
  const icons = { BTC: "₿", SOL: "◎", ETH: "⟠", XRP: "✕" };
  return icons[asset] || "●";
}

// Build the widget
async function createWidget() {
  // Fetch portfolio data live from GitHub
  const PORTFOLIO = await loadPortfolio();

  // Fetch all prices
  for (const p of PORTFOLIO) {
    p.currentPrice = await getPrice(p.pair);
    p.avgPrice = p.amount > 0 ? p.totalCost / p.amount : 0;
    p.currentValue = p.amount * p.currentPrice;
    p.pnl = p.currentValue - p.totalCost;
    p.pnlPct = p.totalCost > 0 ? (p.pnl / p.totalCost) * 100 : 0;
  }

  const totalInvested = PORTFOLIO.reduce((s, p) => s + p.totalCost, 0);
  const totalValue = PORTFOLIO.reduce((s, p) => s + p.currentValue, 0);
  const totalPnl = totalValue - totalInvested;
  const totalPnlPct = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  const w = new ListWidget();
  w.backgroundColor = new Color("#0d1117");
  w.setPadding(14, 16, 14, 16);

  // ── Header ──
  const header = w.addText("₿ Crypto Portfolio");
  header.font = Font.boldSystemFont(13);
  header.textColor = new Color("#f7931a");

  w.addSpacer(6);

  // ── Total Value ──
  const valueRow = w.addText(`${CURRENCY_SYMBOL}${fmt(totalValue)}`);
  valueRow.font = Font.boldSystemFont(22);
  valueRow.textColor = Color.white();

  // ── Total P&L ──
  const pnlColor = totalPnl >= 0 ? "#3fb950" : "#f85149";
  const pnlArrow = totalPnl >= 0 ? "▲" : "▼";
  const pnlSign = totalPnl >= 0 ? "+" : "";
  const pnlRow = w.addText(`${pnlArrow} ${pnlSign}${CURRENCY_SYMBOL}${fmt(Math.abs(totalPnl))}  (${pnlSign}${fmt(totalPnlPct, 1)}%)`);
  pnlRow.font = Font.semiboldSystemFont(12);
  pnlRow.textColor = new Color(pnlColor);

  w.addSpacer(10);

  // ── Per-asset rows ──
  for (const p of PORTFOLIO) {
    const assetPnlColor = p.pnl >= 0 ? "#3fb950" : "#f85149";
    const assetArrow = p.pnl >= 0 ? "▲" : "▼";
    const assetSign = p.pnl >= 0 ? "+" : "";

    const row = w.addStack();
    row.layoutHorizontally();
    row.centerAlignContent();

    // Left: icon + asset name + price
    const left = row.addStack();
    left.layoutVertically();

    const nameText = left.addText(`${icon(p.asset)} ${p.asset}`);
    nameText.font = Font.boldSystemFont(13);
    nameText.textColor = Color.white();

    const priceText = left.addText(`${CURRENCY_SYMBOL}${fmt(p.currentPrice)}  avg ${CURRENCY_SYMBOL}${fmt(p.avgPrice)}`);
    priceText.font = Font.systemFont(10);
    priceText.textColor = new Color("#8b949e");

    row.addSpacer();

    // Right: value + P&L
    const right = row.addStack();
    right.layoutVertically();

    const valText = right.addText(`${CURRENCY_SYMBOL}${fmt(p.currentValue)}`);
    valText.font = Font.semiboldSystemFont(13);
    valText.textColor = Color.white();
    valText.rightAlignText();

    const pctText = right.addText(`${assetArrow} ${assetSign}${fmt(p.pnlPct, 1)}%`);
    pctText.font = Font.semiboldSystemFont(10);
    pctText.textColor = new Color(assetPnlColor);
    pctText.rightAlignText();

    w.addSpacer(6);
  }

  // ── Footer ──
  w.addSpacer(4);
  const now = new Date();
  const timeStr = `${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`;
  const footer = w.addText(`Updated ${timeStr}`);
  footer.font = Font.systemFont(9);
  footer.textColor = new Color("#484f58");
  footer.rightAlignText();

  // Auto-refresh every 5 minutes (iOS minimum practical rate)
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);

  return w;
}

// Run
const widget = await createWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Preview when running in Scriptable app
  widget.presentMedium();
}

Script.complete();
