"""
Crypto Average Price Calculator — Local Web UI
================================================
Run: python3 app.py
Open: http://localhost:8080
"""

import os
import json
import time
import hmac
import hashlib
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = 8080
ENV_FILE = Path(__file__).parent / ".env"
DATA_FILE = Path(__file__).parent / "trades.json"
BINANCE_BASE = "https://api.binance.com"
CUTOFF_YEAR = 2026  # Only include trades from this year onwards


def load_env():
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


def get_keys():
    load_env()
    return os.environ.get("BINANCE_API_KEY", ""), os.environ.get("BINANCE_API_SECRET", "")


# ---------------------------------------------------------------------------
# Binance API
# ---------------------------------------------------------------------------
def binance_signed_request(endpoint, params, api_key, api_secret):
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BINANCE_BASE}{endpoint}?{query}&signature={signature}"
    req = Request(url)
    req.add_header("X-MBX-APIKEY", api_key)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error_msg = str(e)
        if hasattr(e, "read"):
            error_msg = e.read().decode()
        print(f"  ⚠️  API error: {error_msg}")
        return None


def fetch_convert_trades(api_key, api_secret):
    """Fetch ALL convert trades from 2026 onwards."""
    all_trades = []
    end_time = int(time.time() * 1000)
    window = 30 * 24 * 60 * 60 * 1000
    earliest = int(datetime(CUTOFF_YEAR, 1, 1).timestamp() * 1000)

    print("  Fetching Convert trades...")
    while end_time > earliest:
        start_time = max(end_time - window, earliest)
        params = {"startTime": start_time, "endTime": end_time, "limit": 1000}
        result = binance_signed_request("/sapi/v1/convert/tradeFlow", params, api_key, api_secret)

        if result and "list" in result:
            for t in result["list"]:
                to_asset = t.get("toAsset", "")
                from_asset = t.get("fromAsset", "")
                from_amount = float(t.get("fromAmount", 0))
                to_amount = float(t.get("toAmount", 0))
                create_time = t.get("createTime", 0)

                # We want trades where you BOUGHT crypto (from fiat/stable → crypto)
                # e.g. EUR → BTC, EUR → SOL, USDT → BTC, etc.
                if to_amount > 0 and from_amount > 0:
                    price_per_unit = from_amount / to_amount if to_amount else 0
                    date_str = datetime.fromtimestamp(create_time / 1000).strftime("%Y-%m-%d %H:%M") if create_time else ""

                    all_trades.append({
                        "date": date_str,
                        "from_asset": from_asset,
                        "to_asset": to_asset,
                        "amount": to_amount,
                        "price_per_unit": price_per_unit,
                        "total_cost": from_amount,
                        "cost_currency": from_asset,
                        "source": "binance_convert",
                    })

        end_time = start_time
        time.sleep(0.2)

    all_trades.sort(key=lambda x: x["date"])
    print(f"  ✅ Found {len(all_trades)} Convert trades")
    return all_trades


def get_current_prices():
    """Fetch current prices for BTC and SOL in EUR and USD."""
    prices = {}
    pairs = [
        ("BTC", "BTCEUR"), ("BTC", "BTCUSDT"),
        ("SOL", "SOLEUR"), ("SOL", "SOLUSDT"), ("SOL", "SOLEUR"),
        ("ETH", "ETHEUR"), ("ETH", "ETHUSDT"),
        ("XRP", "XRPEUR"), ("XRP", "XRPUSDT"),
    ]
    for asset, symbol in pairs:
        try:
            url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol={symbol}"
            with urlopen(url) as resp:
                data = json.loads(resp.read().decode())
                quote = symbol.replace(asset, "")
                prices[f"{asset}/{quote}"] = float(data["price"])
        except Exception:
            pass
    return prices


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------
def save_trades(trades):
    with open(DATA_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def load_trades():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, ValueError):
            pass
    return []


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def compute_portfolio(trades):
    """Group trades by asset and compute averages."""
    # Separate buy vs sell per asset
    assets = {}
    for t in trades:
        to_asset = t.get("to_asset", "")
        from_asset = t.get("from_asset", "")

        # Buying crypto: from fiat → to crypto
        if to_asset and to_asset not in ("EUR", "USD", "USDT", "USDC", "BUSD"):
            if to_asset not in assets:
                assets[to_asset] = {"buys": [], "sells": []}
            assets[to_asset]["buys"].append(t)

        # Selling crypto: from crypto → to fiat
        if from_asset and from_asset not in ("EUR", "USD", "USDT", "USDC", "BUSD"):
            if from_asset not in assets:
                assets[from_asset] = {"buys": [], "sells": []}
            assets[from_asset]["sells"].append(t)

    portfolio = {}
    for asset, data in assets.items():
        buys = data["buys"]
        sells = data["sells"]

        total_bought = sum(t["amount"] for t in buys)
        total_cost = sum(t["total_cost"] for t in buys)
        total_sold_amount = sum(t["total_cost"] for t in sells)  # total_cost for sells = amount of crypto sold * price
        cost_currency = buys[0]["cost_currency"] if buys else "EUR"

        avg_price = total_cost / total_bought if total_bought > 0 else 0

        portfolio[asset] = {
            "asset": asset,
            "total_bought": total_bought,
            "total_cost": total_cost,
            "avg_price": avg_price,
            "cost_currency": cost_currency,
            "num_buys": len(buys),
            "num_sells": len(sells),
            "first_buy": min(t["date"] for t in buys) if buys else "",
            "last_buy": max(t["date"] for t in buys) if buys else "",
            "trades": buys,
        }

    return portfolio


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------
def build_html(trades, portfolio, prices):
    currency_symbols = {"EUR": "€", "USD": "$", "USDT": "$", "USDC": "$", "BUSD": "$"}

    # Build asset cards
    asset_cards = ""
    total_invested = 0
    total_current_value = 0

    for asset_name in sorted(portfolio.keys()):
        p = portfolio[asset_name]
        sym = currency_symbols.get(p["cost_currency"], p["cost_currency"])
        total_invested += p["total_cost"]

        # Find current price
        price_key = f"{asset_name}/{p['cost_currency']}"
        current_price = prices.get(price_key, 0)
        current_value = p["total_bought"] * current_price
        total_current_value += current_value
        pnl = current_value - p["total_cost"]
        pnl_pct = (pnl / p["total_cost"] * 100) if p["total_cost"] > 0 else 0
        pnl_class = "profit" if pnl >= 0 else "loss"
        pnl_arrow = "↑" if pnl >= 0 else "↓"

        # Build trades table rows
        trade_rows = ""
        for t in p["trades"]:
            trade_rows += f"""<tr>
                <td>{t['date']}</td>
                <td>{t['amount']:.8f}</td>
                <td>{sym}{t['price_per_unit']:,.2f}</td>
                <td>{sym}{t['total_cost']:,.2f}</td>
            </tr>"""

        asset_cards += f"""
        <div class="card">
            <div class="card-header">
                <div class="asset-name">
                    <span class="asset-icon">{"₿" if asset_name == "BTC" else "◎" if asset_name == "SOL" else "⟠" if asset_name == "ETH" else "✕" if asset_name == "XRP" else "●"}</span>
                    {asset_name}
                </div>
                <div class="pnl {pnl_class}">{pnl_arrow} {sym}{abs(pnl):,.2f} ({pnl_pct:+.1f}%)</div>
            </div>
            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Total Bought</div>
                    <div class="stat-value">{p['total_bought']:.8f} {asset_name}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Total Invested</div>
                    <div class="stat-value">{sym}{p['total_cost']:,.2f}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Avg Buy Price</div>
                    <div class="stat-value">{sym}{p['avg_price']:,.2f}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Current Price</div>
                    <div class="stat-value">{sym}{current_price:,.2f}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Current Value</div>
                    <div class="stat-value">{sym}{current_value:,.2f}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Trades</div>
                    <div class="stat-value">{p['num_buys']} buys</div>
                </div>
            </div>
            <details class="trades-detail">
                <summary>📋 View all trades ({p['num_buys']})</summary>
                <table class="trades-table">
                    <thead><tr><th>Date</th><th>Amount</th><th>Price</th><th>Cost</th></tr></thead>
                    <tbody>{trade_rows}</tbody>
                </table>
            </details>
        </div>
        """

    # Overall P&L
    overall_pnl = total_current_value - total_invested
    overall_pnl_pct = (overall_pnl / total_invested * 100) if total_invested > 0 else 0
    overall_class = "profit" if overall_pnl >= 0 else "loss"
    overall_arrow = "↑" if overall_pnl >= 0 else "↓"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Portfolio Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #e6edf3;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{ max-width: 900px; margin: 0 auto; }}

        h1 {{
            text-align: center;
            font-size: 28px;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #f7931a, #ff6b00);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            text-align: center;
            color: #8b949e;
            margin-bottom: 30px;
            font-size: 14px;
        }}

        /* Overview banner */
        .overview {{
            background: linear-gradient(135deg, #161b22, #1c2333);
            border: 1px solid #30363d;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            text-align: center;
        }}
        .overview .stat-label {{ color: #8b949e; font-size: 13px; margin-bottom: 4px; }}
        .overview .stat-value {{ font-size: 22px; font-weight: 700; }}

        /* Cards */
        .card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }}
        .card:hover {{ border-color: #58a6ff; }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}
        .asset-name {{
            font-size: 24px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .asset-icon {{ font-size: 28px; }}

        .pnl {{
            font-size: 16px;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 20px;
        }}
        .pnl.profit {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
        .pnl.loss {{ background: rgba(248, 81, 73, 0.15); color: #f85149; }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-bottom: 16px;
        }}
        .stat {{
            background: #0d1117;
            border-radius: 10px;
            padding: 14px;
        }}
        .stat-label {{ color: #8b949e; font-size: 12px; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 16px; font-weight: 600; }}

        /* Trades detail */
        .trades-detail {{
            margin-top: 12px;
        }}
        .trades-detail summary {{
            cursor: pointer;
            color: #58a6ff;
            font-size: 14px;
            padding: 8px 0;
        }}
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
            font-size: 13px;
        }}
        .trades-table th {{
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid #30363d;
            color: #8b949e;
            font-weight: 600;
        }}
        .trades-table td {{
            padding: 8px 12px;
            border-bottom: 1px solid #21262d;
        }}
        .trades-table tr:hover td {{ background: #1c2333; }}

        /* Actions */
        .actions {{
            text-align: center;
            margin-bottom: 24px;
            display: flex;
            gap: 12px;
            justify-content: center;
        }}
        .btn {{
            background: #21262d;
            color: #e6edf3;
            border: 1px solid #30363d;
            padding: 10px 24px;
            border-radius: 10px;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .btn:hover {{ background: #30363d; }}
        .btn-primary {{
            background: linear-gradient(135deg, #f7931a, #ff6b00);
            border: none;
            color: #fff;
            font-weight: 600;
        }}
        .btn-primary:hover {{ opacity: 0.9; }}

        .empty {{
            text-align: center;
            padding: 60px 20px;
            color: #8b949e;
        }}
        .empty h2 {{ margin-bottom: 12px; color: #e6edf3; }}

        .loading {{
            text-align: center;
            padding: 40px;
            color: #8b949e;
        }}

        @media (max-width: 600px) {{
            .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .overview {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>₿ Crypto Portfolio Dashboard</h1>
        <p class="subtitle">Binance Convert trades · {CUTOFF_YEAR}+ · Auto-updated</p>

        <div class="actions">
            <a href="/fetch" class="btn btn-primary" onclick="this.textContent='⏳ Fetching...'; this.style.pointerEvents='none';">🔄 Fetch from Binance</a>
            <a href="/" class="btn">↻ Refresh</a>
        </div>

        {"" if trades else '''
        <div class="empty">
            <h2>No trades yet</h2>
            <p>Click "Fetch from Binance" to pull your Convert trade history.</p>
        </div>
        '''}

        {"" if not trades else f'''
        <div class="overview">
            <div>
                <div class="stat-label">Total Invested</div>
                <div class="stat-value">€{total_invested:,.2f}</div>
            </div>
            <div>
                <div class="stat-label">Current Value</div>
                <div class="stat-value">€{total_current_value:,.2f}</div>
            </div>
            <div>
                <div class="stat-label">Overall P&L</div>
                <div class="stat-value pnl {overall_class}" style="display:inline-block">{overall_arrow} €{abs(overall_pnl):,.2f} ({overall_pnl_pct:+.1f}%)</div>
            </div>
        </div>
        '''}

        {asset_cards}

        <p class="subtitle" style="margin-top: 30px;">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/fetch":
            api_key, api_secret = get_keys()
            if not api_key or not api_secret:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return

            print("\n🔄 Fetching trades from Binance...")
            new_trades = fetch_convert_trades(api_key, api_secret)

            # Filter: only keep trades where you BUY crypto (from fiat/stable)
            fiat = {"EUR", "USD", "USDT", "USDC", "BUSD"}
            buy_trades = [t for t in new_trades if t["from_asset"] in fiat]

            # Load existing, filter out old-format entries, deduplicate, save
            existing = [t for t in load_trades() if "to_asset" in t]
            existing_keys = {(t["date"], t["to_asset"], t["amount"]) for t in existing}
            added = 0
            for t in buy_trades:
                key = (t["date"], t["to_asset"], t["amount"])
                if key not in existing_keys:
                    existing.append(t)
                    existing_keys.add(key)
                    added += 1

            save_trades(existing)
            print(f"✅ {added} new trades added ({len(buy_trades)} total found)\n")

            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

        elif self.path == "/":
            trades = load_trades()
            prices = get_current_prices()
            portfolio = compute_portfolio(trades)
            html = build_html(trades, portfolio, prices)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Quieter logging
        print(f"  {args[0]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n₿ Crypto Portfolio Dashboard")
    print(f"{'=' * 40}")
    print(f"  🌐 Open http://localhost:{PORT}")
    print(f"  ⏹  Press Ctrl+C to stop\n")

    server = HTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Bye!")
        server.server_close()
