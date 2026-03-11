"""
sync_widget.py
==============
Fetches your latest Convert trades from Binance, recalculates your portfolio,
updates scriptable_widget.js with the new amounts, then commits and pushes to GitHub.

Run manually:     python3 sync_widget.py
Run on schedule:  set up via launchd (see below) or add to cron
"""

import os
import re
import json
import time
import hmac
import hashlib
import subprocess
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
TRADES_FILE = BASE_DIR / "trades.json"
WIDGET_FILE = BASE_DIR / "scriptable_widget.js"
PORTFOLIO_JSON = BASE_DIR / "portfolio.json"
BINANCE_BASE = "https://api.binance.com"
CUTOFF_YEAR = 2026
FIAT = {"EUR", "USD", "USDT", "USDC", "BUSD"}

# ---------------------------------------------------------------------------
# Load env
# ---------------------------------------------------------------------------
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
def signed_request(endpoint, params, api_key, api_secret):
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BINANCE_BASE}{endpoint}?{query}&signature={sig}"
    req = Request(url)
    req.add_header("X-MBX-APIKEY", api_key)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠️  API error: {e}")
        return None

def fetch_convert_trades(api_key, api_secret):
    all_trades = []
    end_time = int(time.time() * 1000)
    window = 30 * 24 * 60 * 60 * 1000
    earliest = int(datetime(CUTOFF_YEAR, 1, 1).timestamp() * 1000)

    print("  Fetching Convert trades from Binance...")
    while end_time > earliest:
        start_time = max(end_time - window, earliest)
        result = signed_request("/sapi/v1/convert/tradeFlow", {
            "startTime": start_time, "endTime": end_time, "limit": 1000
        }, api_key, api_secret)

        if result and "list" in result:
            for t in result["list"]:
                from_asset = t.get("fromAsset", "")
                to_asset = t.get("toAsset", "")
                from_amount = float(t.get("fromAmount", 0))
                to_amount = float(t.get("toAmount", 0))
                create_time = t.get("createTime", 0)

                if from_asset in FIAT and to_asset not in FIAT and to_amount > 0:
                    all_trades.append({
                        "date": datetime.fromtimestamp(create_time / 1000).strftime("%Y-%m-%d %H:%M"),
                        "from_asset": from_asset,
                        "to_asset": to_asset,
                        "amount": to_amount,
                        "price_per_unit": from_amount / to_amount,
                        "total_cost": from_amount,
                        "cost_currency": from_asset,
                        "source": "binance_convert",
                    })

        end_time = start_time
        time.sleep(0.2)

    all_trades.sort(key=lambda x: x["date"])
    print(f"  ✅ Found {len(all_trades)} buy trades")
    return all_trades

# ---------------------------------------------------------------------------
# Compute portfolio
# ---------------------------------------------------------------------------
def compute_portfolio(trades):
    assets = {}
    for t in trades:
        a = t["to_asset"]
        if a not in assets:
            assets[a] = {"amount": 0.0, "cost": 0.0, "currency": t["cost_currency"]}
        assets[a]["amount"] += t["amount"]
        assets[a]["cost"] += t["total_cost"]
    return assets

# ---------------------------------------------------------------------------
# Write portfolio.json (fetched at runtime by the iOS widget)
# ---------------------------------------------------------------------------
def write_portfolio_json(portfolio):
    result = []
    for asset, data in sorted(portfolio.items()):
        currency = data["currency"]
        result.append({
            "asset": asset,
            "amount": round(data["amount"], 8),
            "totalCost": round(data["cost"], 2),
            "currency": currency,
            "pair": f"{asset}{currency}",
        })
    with open(PORTFOLIO_JSON, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  ✅ portfolio.json written with {len(result)} assets")
    return result

# ---------------------------------------------------------------------------
# Update scriptable_widget.js
# ---------------------------------------------------------------------------
def update_widget(portfolio):
    if not WIDGET_FILE.exists():
        print("  ⚠️  scriptable_widget.js not found, skipping widget update")
        return False

    content = WIDGET_FILE.read_text()

    # Build the new PORTFOLIO array
    lines = []
    for asset, data in sorted(portfolio.items()):
        currency = data["currency"]
        pair = f"{asset}{currency}"
        lines.append(
            f'  {{ asset: "{asset}", amount: {data["amount"]:.8f}, '
            f'totalCost: {data["cost"]:.2f}, currency: "{currency}", pair: "{pair}" }},'
        )

    new_portfolio = "const PORTFOLIO = [\n" + "\n".join(lines) + "\n];"

    # Replace the PORTFOLIO block in the file
    updated = re.sub(
        r"const PORTFOLIO = \[.*?\];",
        new_portfolio,
        content,
        flags=re.DOTALL
    )

    if updated == content:
        print("  ℹ️  Portfolio unchanged, no update needed")
        return False

    WIDGET_FILE.write_text(updated)
    print(f"  ✅ scriptable_widget.js updated with {len(portfolio)} assets: {', '.join(sorted(portfolio.keys()))}")
    return True

# ---------------------------------------------------------------------------
# Git commit & push
# ---------------------------------------------------------------------------
def git_push():
    try:
        subprocess.run(["git", "-C", str(BASE_DIR), "add", "scriptable_widget.js", "portfolio.json"], check=True)
        result = subprocess.run(
            ["git", "-C", str(BASE_DIR), "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode == 0:
            print("  ℹ️  Nothing to commit")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run([
            "git", "-C", str(BASE_DIR), "commit", "-m",
            f"sync: update portfolio widget {now}"
        ], check=True)
        subprocess.run(["git", "-C", str(BASE_DIR), "push", "origin", "main"], check=True)
        print("  ✅ Pushed to GitHub")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  Git error: {e}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"\n🔄 Syncing portfolio widget — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    api_key, api_secret = get_keys()
    if not api_key or not api_secret:
        print("  ❌ No API keys found. Set them in .env")
        return

    # Fetch trades
    trades = fetch_convert_trades(api_key, api_secret)
    if not trades:
        print("  ⚠️  No trades found, nothing to sync")
        return

    # Save locally
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)
    print(f"  💾 Saved {len(trades)} trades to trades.json")

    # Print portfolio summary
    portfolio = compute_portfolio(trades)
    print("\n  📊 Portfolio summary:")
    for asset, data in sorted(portfolio.items()):
        avg = data["cost"] / data["amount"] if data["amount"] > 0 else 0
        print(f"     {asset}: {data['amount']:.8f}  |  cost: {data['currency']}{data['cost']:.2f}  |  avg: {data['currency']}{avg:.2f}")

    # Write portfolio.json (always, so iOS widget always gets fresh data)
    print()
    write_portfolio_json(portfolio)

    # Update widget file
    changed = update_widget(portfolio)

    # Push to GitHub (portfolio.json always changes with new prices; widget only if portfolio changed)
    print()
    git_push()

    print("\n✅ Sync complete!\n")

if __name__ == "__main__":
    main()
