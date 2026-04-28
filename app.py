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
# Binance storage info (balances + withdrawals)
# ---------------------------------------------------------------------------
def fetch_storage_info(api_key, api_secret, assets):
    """Fetch Binance balances and withdrawal history for given assets."""
    storage = {a: {"binance": 0, "cold": 0} for a in assets}

    # Account balances
    result = binance_signed_request("/api/v3/account", {}, api_key, api_secret)
    if result and "balances" in result:
        for b in result["balances"]:
            if b["asset"] in storage:
                storage[b["asset"]]["binance"] = float(b.get("free", 0)) + float(b.get("locked", 0))

    # Completed withdrawals (status=6)
    withdrawals = binance_signed_request("/sapi/v1/capital/withdraw/history", {"status": 6}, api_key, api_secret)
    if withdrawals and isinstance(withdrawals, list):
        for w in withdrawals:
            if w.get("coin") in storage:
                storage[w["coin"]]["cold"] += float(w.get("amount", 0))

    return storage


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------
def build_html(trades, portfolio, prices):
    currency_symbols = {"EUR": "€", "USD": "$", "USDT": "$", "USDC": "$", "BUSD": "$"}

    # Precompute per-asset data
    asset_data = []
    total_invested = 0
    total_current_value = 0

    for asset_name in sorted(portfolio.keys()):
        p = portfolio[asset_name]
        sym = currency_symbols.get(p["cost_currency"], p["cost_currency"])
        total_invested += p["total_cost"]

        price_key = f"{asset_name}/{p['cost_currency']}"
        current_price = prices.get(price_key, 0)
        current_value = p["total_bought"] * current_price
        total_current_value += current_value
        pnl = current_value - p["total_cost"]
        pnl_pct = (pnl / p["total_cost"] * 100) if p["total_cost"] > 0 else 0

        # Monthly breakdown
        from collections import defaultdict
        monthly_cost = defaultdict(float)
        monthly_amount = defaultdict(float)
        for t in p["trades"]:
            m = t["date"][:7]
            monthly_cost[m] += t["total_cost"]
            monthly_amount[m] += t["amount"]

        # Trade size stats
        costs = [t["total_cost"] for t in p["trades"]]
        min_trade = min(costs) if costs else 0
        max_trade = max(costs) if costs else 0
        avg_trade = sum(costs) / len(costs) if costs else 0

        asset_data.append({
            "name": asset_name, "sym": sym, "p": p,
            "current_price": current_price, "current_value": current_value,
            "pnl": pnl, "pnl_pct": pnl_pct,
            "monthly_cost": dict(monthly_cost), "monthly_amount": dict(monthly_amount),
            "min_trade": min_trade, "max_trade": max_trade, "avg_trade": avg_trade,
        })

    # Fetch storage info
    api_key, api_secret = get_keys()
    storage = {}
    if api_key and api_secret:
        storage = fetch_storage_info(api_key, api_secret, list(portfolio.keys()))

    overall_pnl = total_current_value - total_invested
    overall_pnl_pct = (overall_pnl / total_invested * 100) if total_invested > 0 else 0
    overall_class = "profit" if overall_pnl >= 0 else "loss"
    overall_arrow = "↑" if overall_pnl >= 0 else "↓"

    # Allocation percentages for donut
    alloc_items = []
    alloc_colors = {"BTC": "#f7931a", "SOL": "#9945ff", "ETH": "#627eea", "XRP": "#23292f"}
    for ad in asset_data:
        pct = (ad["current_value"] / total_current_value * 100) if total_current_value > 0 else 0
        color = alloc_colors.get(ad["name"], "#58a6ff")
        alloc_items.append({"name": ad["name"], "pct": pct, "color": color, "value": ad["current_value"]})

    # Build donut gradient
    gradient_parts = []
    offset = 0
    for item in alloc_items:
        gradient_parts.append(f"{item['color']} {offset:.1f}% {offset + item['pct']:.1f}%")
        offset += item["pct"]
    donut_gradient = ", ".join(gradient_parts) if gradient_parts else "#30363d 0% 100%"

    # Allocation legend
    alloc_legend = ""
    for item in alloc_items:
        alloc_legend += f"""<div class="alloc-item">
            <span class="alloc-dot" style="background:{item['color']}"></span>
            <span class="alloc-name">{item['name']}</span>
            <span class="alloc-pct">{item['pct']:.1f}%</span>
            <span class="alloc-val">€{item['value']:,.2f}</span>
        </div>"""

    # All months across all assets
    all_months = set()
    for ad in asset_data:
        all_months.update(ad["monthly_cost"].keys())
    all_months = sorted(all_months)

    # Monthly DCA chart (stacked bar)
    max_monthly = 0
    for m in all_months:
        total = sum(ad["monthly_cost"].get(m, 0) for ad in asset_data)
        if total > max_monthly:
            max_monthly = total

    monthly_bars = ""
    for m in all_months:
        label = m  # e.g. "2026-01"
        segments = ""
        for ad in asset_data:
            val = ad["monthly_cost"].get(m, 0)
            if val > 0 and max_monthly > 0:
                h = val / max_monthly * 100
                color = alloc_colors.get(ad["name"], "#58a6ff")
                segments += f'<div class="bar-seg" style="height:{h}%;background:{color}" title="{ad["name"]}: €{val:,.0f}"></div>'
        total_m = sum(ad["monthly_cost"].get(m, 0) for ad in asset_data)
        monthly_bars += f"""<div class="bar-col">
            <div class="bar-amount">€{total_m:,.0f}</div>
            <div class="bar-stack">{segments}</div>
            <div class="bar-label">{label[5:]}</div>
        </div>"""

    # Build asset cards
    asset_cards = ""
    for ad in asset_data:
        p = ad["p"]
        sym = ad["sym"]
        asset_name = ad["name"]
        pnl_class = "profit" if ad["pnl"] >= 0 else "loss"
        pnl_arrow = "↑" if ad["pnl"] >= 0 else "↓"
        pnl_sign = "+" if ad["pnl"] >= 0 else ""
        icon = {"BTC": "₿", "SOL": "◎", "ETH": "⟠", "XRP": "✕"}.get(asset_name, "●")
        color = alloc_colors.get(asset_name, "#58a6ff")
        amt_fmt = 8 if asset_name == "BTC" else 4

        # Storage section
        storage_html = ""
        s = storage.get(asset_name, {})
        if s:
            binance_amt = s.get("binance", 0)
            cold_amt = s.get("cold", 0)
            binance_eur = binance_amt * ad["current_price"]
            cold_eur = cold_amt * ad["current_price"]
            total_accounted = binance_amt + cold_amt
            total_eur = total_accounted * ad["current_price"]

            # Proportional bar
            total_for_bar = max(total_accounted, 0.0001)
            binance_pct = binance_amt / total_for_bar * 100
            cold_pct = cold_amt / total_for_bar * 100

            storage_html = f"""
            <div class="storage-section">
                <div class="storage-title">📍 Where it is</div>
                <div class="storage-bar">
                    <div class="storage-bar-seg binance-seg" style="width:{binance_pct:.1f}%"></div>
                    <div class="storage-bar-seg cold-seg" style="width:{cold_pct:.1f}%"></div>
                </div>
                <div class="storage-grid">
                    <div class="storage-row">
                        <span class="storage-icon">🏦</span>
                        <span class="storage-label">Binance</span>
                        <span class="storage-amt">{binance_amt:.{amt_fmt}f} {asset_name}</span>
                        <span class="storage-eur">{sym}{binance_eur:,.2f}</span>
                    </div>
                    <div class="storage-row">
                        <span class="storage-icon">🔐</span>
                        <span class="storage-label">Cold Storage</span>
                        <span class="storage-amt">{cold_amt:.{amt_fmt}f} {asset_name}</span>
                        <span class="storage-eur">{sym}{cold_eur:,.2f}</span>
                    </div>
                </div>
            </div>"""

        # Trade rows with running totals
        trade_rows = ""
        running_amount = 0
        running_cost = 0
        for t in p["trades"]:
            running_amount += t["amount"]
            running_cost += t["total_cost"]
            running_avg = running_cost / running_amount if running_amount > 0 else 0
            trade_rows += f"""<tr>
                <td>{t['date']}</td>
                <td>{t['amount']:.{amt_fmt}f}</td>
                <td>{sym}{t['price_per_unit']:,.2f}</td>
                <td>{sym}{t['total_cost']:,.2f}</td>
                <td class="running">{running_amount:.{amt_fmt}f}</td>
                <td class="running">{sym}{running_cost:,.2f}</td>
                <td class="running">{sym}{running_avg:,.2f}</td>
            </tr>"""

        # Price vs avg comparison
        price_diff = ad["current_price"] - p["avg_price"]
        price_diff_pct = (price_diff / p["avg_price"] * 100) if p["avg_price"] > 0 else 0
        price_vs_class = "profit" if price_diff >= 0 else "loss"

        asset_cards += f"""
        <div class="card" style="border-left: 3px solid {color}">
            <div class="card-header">
                <div class="asset-name">
                    <span class="asset-icon">{icon}</span>
                    {asset_name}
                </div>
                <div class="pnl {pnl_class}">{pnl_arrow} {pnl_sign}{sym}{abs(ad['pnl']):,.2f} ({pnl_sign}{ad['pnl_pct']:.1f}%)</div>
            </div>

            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Holdings</div>
                    <div class="stat-value">{p['total_bought']:.{amt_fmt}f}</div>
                    <div class="stat-sub">{asset_name}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Invested</div>
                    <div class="stat-value">{sym}{p['total_cost']:,.2f}</div>
                    <div class="stat-sub">{p['num_buys']} buys</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Current Value</div>
                    <div class="stat-value">{sym}{ad['current_value']:,.2f}</div>
                    <div class="stat-sub {pnl_class}">{pnl_sign}{sym}{abs(ad['pnl']):,.2f}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Avg Buy Price</div>
                    <div class="stat-value">{sym}{p['avg_price']:,.2f}</div>
                    <div class="stat-sub">per {asset_name}</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Current Price</div>
                    <div class="stat-value">{sym}{ad['current_price']:,.2f}</div>
                    <div class="stat-sub {price_vs_class}">{pnl_sign}{price_diff_pct:.1f}% vs avg</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Avg Trade Size</div>
                    <div class="stat-value">{sym}{ad['avg_trade']:,.0f}</div>
                    <div class="stat-sub">{sym}{ad['min_trade']:,.0f} – {sym}{ad['max_trade']:,.0f}</div>
                </div>
            </div>

            <div class="card-meta">
                <span>First buy: {p['first_buy']}</span>
                <span>Last buy: {p['last_buy']}</span>
            </div>

            {storage_html}

            <details class="trades-detail">
                <summary>📋 Trade history ({p['num_buys']} trades)</summary>
                <table class="trades-table">
                    <thead><tr>
                        <th>Date</th><th>Amount</th><th>Price</th><th>Cost</th>
                        <th class="running-hdr">Running Amt</th><th class="running-hdr">Running Cost</th><th class="running-hdr">Running Avg</th>
                    </tr></thead>
                    <tbody>{trade_rows}</tbody>
                </table>
            </details>
        </div>
        """

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
        .container {{ max-width: 1000px; margin: 0 auto; }}

        h1 {{
            text-align: center;
            font-size: 32px;
            margin-bottom: 6px;
            background: linear-gradient(135deg, #f7931a, #ff6b00);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            text-align: center;
            color: #8b949e;
            margin-bottom: 28px;
            font-size: 13px;
        }}

        /* Actions */
        .actions {{
            text-align: center;
            margin-bottom: 28px;
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
            transition: all 0.2s;
        }}
        .btn:hover {{ background: #30363d; }}
        .btn-primary {{
            background: linear-gradient(135deg, #f7931a, #ff6b00);
            border: none;
            color: #fff;
            font-weight: 600;
        }}
        .btn-primary:hover {{ opacity: 0.9; }}

        /* Top row: overview + allocation */
        .top-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }}
        .panel {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 16px;
            padding: 24px;
        }}
        .panel-title {{
            font-size: 14px;
            font-weight: 600;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
        }}

        /* Overview panel */
        .overview-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }}
        .overview-stat {{ }}
        .overview-stat .stat-label {{ color: #8b949e; font-size: 12px; margin-bottom: 2px; }}
        .overview-stat .stat-value {{ font-size: 24px; font-weight: 700; }}
        .overview-stat .stat-value.big {{ font-size: 28px; }}
        .overview-stat.full {{ grid-column: 1 / -1; }}

        /* Allocation donut */
        .alloc-wrap {{
            display: flex;
            align-items: center;
            gap: 24px;
        }}
        .donut {{
            width: 120px;
            height: 120px;
            border-radius: 50%;
            background: conic-gradient({donut_gradient});
            position: relative;
            flex-shrink: 0;
        }}
        .donut::after {{
            content: '';
            position: absolute;
            top: 25px; left: 25px;
            width: 70px; height: 70px;
            border-radius: 50%;
            background: #161b22;
        }}
        .alloc-legend {{ flex: 1; }}
        .alloc-item {{
            display: grid;
            grid-template-columns: 12px auto 50px 1fr;
            gap: 8px;
            align-items: center;
            padding: 6px 0;
            border-bottom: 1px solid #21262d;
            font-size: 13px;
        }}
        .alloc-item:last-child {{ border-bottom: none; }}
        .alloc-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
        .alloc-name {{ font-weight: 600; }}
        .alloc-pct {{ color: #8b949e; text-align: right; }}
        .alloc-val {{ text-align: right; font-weight: 500; }}

        /* Monthly DCA chart */
        .chart-panel {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        .chart-bars {{
            display: flex;
            align-items: flex-end;
            gap: 8px;
            height: 140px;
            padding-top: 24px;
        }}
        .bar-col {{
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            height: 100%;
        }}
        .bar-amount {{
            font-size: 11px;
            color: #8b949e;
            margin-bottom: 4px;
            white-space: nowrap;
        }}
        .bar-stack {{
            flex: 1;
            width: 100%;
            max-width: 60px;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            border-radius: 6px 6px 0 0;
            overflow: hidden;
        }}
        .bar-seg {{
            width: 100%;
            min-height: 2px;
            transition: opacity 0.2s;
        }}
        .bar-seg:hover {{ opacity: 0.8; }}
        .bar-label {{
            font-size: 11px;
            color: #8b949e;
            margin-top: 6px;
        }}

        /* Asset Cards */
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
            font-size: 15px;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 20px;
        }}
        .pnl.profit {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
        .pnl.loss {{ background: rgba(248, 81, 73, 0.15); color: #f85149; }}
        .profit {{ color: #3fb950; }}
        .loss {{ color: #f85149; }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }}
        .stat {{
            background: #0d1117;
            border-radius: 10px;
            padding: 14px;
        }}
        .stat-label {{ color: #8b949e; font-size: 11px; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 16px; font-weight: 600; }}
        .stat-sub {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}

        .card-meta {{
            display: flex;
            gap: 20px;
            font-size: 12px;
            color: #8b949e;
            margin-bottom: 16px;
            padding: 10px 0;
            border-top: 1px solid #21262d;
            border-bottom: 1px solid #21262d;
        }}

        /* Storage section */
        .storage-section {{
            margin: 16px 0;
            padding: 16px;
            background: #0d1117;
            border-radius: 12px;
        }}
        .storage-title {{
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #8b949e;
        }}
        .storage-bar {{
            height: 8px;
            border-radius: 4px;
            background: #21262d;
            display: flex;
            overflow: hidden;
            margin-bottom: 12px;
        }}
        .storage-bar-seg {{ height: 100%; transition: width 0.3s; }}
        .binance-seg {{ background: #f7931a; }}
        .cold-seg {{ background: #3fb950; }}
        .storage-grid {{ }}
        .storage-row {{
            display: grid;
            grid-template-columns: 24px 100px 1fr auto;
            gap: 8px;
            align-items: center;
            padding: 6px 0;
            font-size: 13px;
        }}
        .storage-icon {{ font-size: 16px; }}
        .storage-label {{ color: #8b949e; }}
        .storage-amt {{ font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }}
        .storage-eur {{ font-weight: 600; text-align: right; }}

        /* Trade history */
        .trades-detail {{ margin-top: 12px; }}
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
            font-size: 12px;
        }}
        .trades-table th {{
            text-align: left;
            padding: 8px 10px;
            border-bottom: 1px solid #30363d;
            color: #8b949e;
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }}
        .trades-table td {{
            padding: 7px 10px;
            border-bottom: 1px solid #21262d;
            font-family: 'SF Mono', Menlo, monospace;
            font-size: 12px;
        }}
        .trades-table tr:hover td {{ background: #1c2333; }}
        .running {{ color: #58a6ff; }}
        .running-hdr {{ color: #58a6ff !important; }}

        .empty {{
            text-align: center;
            padding: 60px 20px;
            color: #8b949e;
        }}
        .empty h2 {{ margin-bottom: 12px; color: #e6edf3; }}

        .footer {{
            text-align: center;
            color: #484f58;
            font-size: 12px;
            margin-top: 30px;
            padding: 16px 0;
            border-top: 1px solid #21262d;
        }}

        @media (max-width: 700px) {{
            .top-row {{ grid-template-columns: 1fr; }}
            .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .alloc-wrap {{ flex-direction: column; }}
            .storage-row {{ grid-template-columns: 24px 80px 1fr; }}
            .storage-eur {{ display: none; }}
            .trades-table .running, .trades-table .running-hdr {{ display: none; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>₿ Crypto Portfolio Dashboard</h1>
        <p class="subtitle">Binance Convert · {CUTOFF_YEAR}+ · {len(trades)} trades across {len(portfolio)} assets</p>

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
        <div class="top-row">
            <div class="panel">
                <div class="panel-title">Portfolio Overview</div>
                <div class="overview-grid">
                    <div class="overview-stat">
                        <div class="stat-label">Total Invested</div>
                        <div class="stat-value big">€{total_invested:,.2f}</div>
                    </div>
                    <div class="overview-stat">
                        <div class="stat-label">Current Value</div>
                        <div class="stat-value big">€{total_current_value:,.2f}</div>
                    </div>
                    <div class="overview-stat full">
                        <div class="stat-label">Overall P&L</div>
                        <div class="stat-value pnl {overall_class}" style="display:inline-block;font-size:22px">{overall_arrow} €{abs(overall_pnl):,.2f} ({overall_pnl_pct:+.1f}%)</div>
                    </div>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">Allocation</div>
                <div class="alloc-wrap">
                    <div class="donut"></div>
                    <div class="alloc-legend">{alloc_legend}</div>
                </div>
            </div>
        </div>

        <div class="chart-panel">
            <div class="panel-title">Monthly DCA Spending</div>
            <div class="chart-bars">{monthly_bars}</div>
        </div>
        '''}

        {asset_cards}

        <div class="footer">
            Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · Auto-refreshes on page load
        </div>
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
