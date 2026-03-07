"""
Bitcoin Average Price Calculator
================================
Pulls your BTC buy history from Binance API and calculates your
true average purchase price — even after you send BTC to a hardware wallet.

Usage:
  1. Set your Binance API key/secret in .env file
  2. Run: python btc_avg_calculator.py
  3. Optionally import from CSV if you have older trades

Your withdrawal to a hardware wallet doesn't affect your avg buy price,
since it's still your BTC — just in cold storage.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import csv
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).parent / ".env"
DATA_FILE = Path(__file__).parent / "trades.json"

BINANCE_BASE = "https://api.binance.com"
# Change to "https://api.binance.us" if you use Binance US

TRADING_PAIRS = ["BTCUSDT", "BTCBUSD", "BTCUSDC", "BTCEUR"]
# Add more pairs if you buy BTC with other currencies


def load_env():
    """Load API keys from .env file."""
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


def get_keys():
    load_env()
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    return api_key, api_secret


# ---------------------------------------------------------------------------
# Binance API helpers
# ---------------------------------------------------------------------------
def binance_signed_request(endpoint: str, params: dict, api_key: str, api_secret: str):
    """Make a signed GET request to Binance API."""
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        api_secret.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
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
        print(f"  ⚠️  API error for {endpoint}: {error_msg}")
        return None


def fetch_all_trades(api_key: str, api_secret: str):
    """Fetch all BTC buy trades from Binance across common pairs."""
    all_buys = []

    for symbol in TRADING_PAIRS:
        print(f"  Fetching spot trades for {symbol}...")
        from_id = 0
        while True:
            params = {"symbol": symbol, "limit": 1000}
            if from_id:
                params["fromId"] = from_id

            trades = binance_signed_request("/api/v3/myTrades", params, api_key, api_secret)

            if not trades:
                break

            for t in trades:
                is_buyer = t.get("isBuyer", False)
                trade_time = t.get("time", 0)
                # Only include trades from 2026 onwards
                if is_buyer and trade_time >= datetime(2026, 1, 1).timestamp() * 1000:
                    all_buys.append({
                        "date": datetime.fromtimestamp(trade_time / 1000).strftime("%Y-%m-%d %H:%M"),
                        "pair": symbol,
                        "btc_amount": float(t["qty"]),
                        "price_per_btc": float(t["price"]),
                        "total_cost": float(t["quoteQty"]),
                        "fee": float(t["commission"]),
                        "fee_asset": t["commissionAsset"],
                        "source": "binance_spot",
                    })

            if len(trades) < 1000:
                break
            from_id = trades[-1]["id"] + 1
            time.sleep(0.2)  # rate limit

    all_buys.sort(key=lambda x: x["date"])
    return all_buys


def fetch_convert_trades(api_key: str, api_secret: str):
    """Fetch BTC buy trades made via Binance Convert."""
    all_buys = []
    # Binance Convert API requires startTime/endTime, max 30 days per request.
    # We'll walk backwards from today in 30-day windows, starting from 2026.
    end_time = int(time.time() * 1000)
    window_days = 30
    # Only fetch trades from Jan 1, 2026 onwards
    earliest = int(datetime(2026, 1, 1).timestamp() * 1000)

    print("  Fetching Convert trades...")

    while end_time > earliest:
        start_time = end_time - (window_days * 24 * 60 * 60 * 1000)
        if start_time < earliest:
            start_time = earliest

        params = {
            "startTime": start_time,
            "endTime": end_time,
            "limit": 1000,
        }

        result = binance_signed_request("/sapi/v1/convert/tradeFlow", params, api_key, api_secret)

        if not result or "list" not in result:
            end_time = start_time
            time.sleep(0.2)
            continue

        trades = result["list"]
        for t in trades:
            # Determine which side is BTC (could be buying or selling)
            from_asset = t.get("fromAsset", "")
            to_asset = t.get("toAsset", "")
            from_amount = float(t.get("fromAmount", 0))
            to_amount = float(t.get("toAmount", 0))

            # We want trades where you BOUGHT BTC (toAsset == BTC)
            if to_asset == "BTC":
                btc_amount = to_amount
                total_cost = from_amount
                price_per_btc = total_cost / btc_amount if btc_amount > 0 else 0
                pair = f"BTC{from_asset}"
                create_time = t.get("createTime", 0)
                date_str = datetime.fromtimestamp(create_time / 1000).strftime("%Y-%m-%d %H:%M") if create_time else ""

                all_buys.append({
                    "date": date_str,
                    "pair": pair,
                    "btc_amount": btc_amount,
                    "price_per_btc": price_per_btc,
                    "total_cost": total_cost,
                    "fee": 0,  # Convert fees are baked into the rate
                    "fee_asset": from_asset,
                    "source": "binance_convert",
                })

        if len(trades) < 1000:
            end_time = start_time
        else:
            # Move window back
            end_time = start_time
        time.sleep(0.2)

    count = len(all_buys)
    if count:
        print(f"  ✅ Found {count} Convert trades")
    else:
        print("  ⚠️  No Convert trades found")

    all_buys.sort(key=lambda x: x["date"])
    return all_buys


# ---------------------------------------------------------------------------
# CSV import (fallback / supplement)
# ---------------------------------------------------------------------------
def import_from_csv(csv_path: str):
    """
    Import trades from a CSV file.
    Expected columns: date, btc_amount, price_per_btc, total_cost
    (You can export this from Binance trade history)
    """
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "date": row.get("date", row.get("Date", "")),
                "pair": row.get("pair", "BTCUSDT"),
                "btc_amount": float(row.get("btc_amount", row.get("Executed", row.get("Amount", 0)))),
                "price_per_btc": float(row.get("price_per_btc", row.get("Price", 0))),
                "total_cost": float(row.get("total_cost", row.get("Total", 0))),
                "fee": float(row.get("fee", row.get("Fee", 0))),
                "fee_asset": row.get("fee_asset", "USDT"),
                "source": "csv_import",
            })
    return trades


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------
def manual_entry():
    """Let the user add trades manually."""
    trades = []
    print("\n📝 Manual Trade Entry (type 'done' to finish)")
    print("-" * 45)
    while True:
        date = input("  Date (YYYY-MM-DD) or 'done': ").strip()
        if date.lower() == "done":
            break
        try:
            btc = float(input("  BTC amount bought: "))
            price = float(input("  Price per BTC (USD): "))
            total = btc * price
            confirm = input(f"  → {btc} BTC @ ${price:,.2f} = ${total:,.2f}  Correct? (y/n): ")
            if confirm.lower() == "y":
                trades.append({
                    "date": date,
                    "pair": "BTCUSD",
                    "btc_amount": btc,
                    "price_per_btc": price,
                    "total_cost": total,
                    "fee": 0,
                    "fee_asset": "USD",
                    "source": "manual",
                })
                print("  ✅ Added!\n")
        except ValueError:
            print("  ❌ Invalid input, try again.\n")
    return trades


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_trades(trades: list):
    with open(DATA_FILE, "w") as f:
        json.dump(trades, f, indent=2)
    print(f"  💾 Saved {len(trades)} trades to {DATA_FILE.name}")


def load_trades():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, ValueError):
            print("  ⚠️  trades.json was corrupted, starting fresh.")
    return []


# ---------------------------------------------------------------------------
# Calculation & Display
# ---------------------------------------------------------------------------
def calculate_avg(trades: list):
    """Calculate the weighted average purchase price."""
    if not trades:
        return 0, 0, 0

    total_btc = sum(t["btc_amount"] for t in trades)
    total_cost = sum(t["total_cost"] for t in trades)

    # Subtract BTC fees (when fee is paid in BTC, you received less)
    btc_fees = sum(t["fee"] for t in trades if t.get("fee_asset") == "BTC")
    net_btc = total_btc - btc_fees

    avg_price = total_cost / net_btc if net_btc > 0 else 0
    return avg_price, net_btc, total_cost


def get_current_price(symbol="BTCEUR"):
    """Fetch current BTC price from Binance public API."""
    try:
        url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol={symbol}"
        with urlopen(url) as resp:
            data = json.loads(resp.read().decode())
            return float(data["price"]), symbol
    except Exception:
        # Fallback to BTCUSDT if EUR pair fails
        try:
            url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol=BTCUSDT"
            with urlopen(url) as resp:
                data = json.loads(resp.read().decode())
                return float(data["price"]), "BTCUSDT"
        except Exception:
            return None, symbol


def detect_currency(trades: list):
    """Detect the main currency used in trades."""
    currencies = {}
    for t in trades:
        asset = t.get("fee_asset", "USD")
        if asset == "BTC":
            continue
        currencies[asset] = currencies.get(asset, 0) + 1
    if currencies:
        return max(currencies, key=currencies.get)
    return "USD"


def display_summary(trades: list):
    """Print a beautiful summary."""
    avg_price, total_btc, total_cost = calculate_avg(trades)
    currency = detect_currency(trades)
    sym = "€" if currency == "EUR" else "$"
    price_pair = f"BTC{currency}" if currency in ("EUR", "USDT", "USDC", "BUSD") else "BTCUSDT"
    current_price, used_pair = get_current_price(price_pair)

    print("\n" + "=" * 55)
    print("  ₿  BITCOIN AVERAGE PRICE CALCULATOR  ₿")
    print("=" * 55)

    print(f"\n  📊 Total Purchases:     {len(trades)} trades")
    print(f"  ₿  Total BTC:           {total_btc:.8f} BTC")
    print(f"  💰 Total Invested:      {sym}{total_cost:,.2f}")
    print(f"  📈 Avg Buy Price:       {sym}{avg_price:,.2f}")

    if trades:
        first_date = min(t["date"] for t in trades)
        last_date = max(t["date"] for t in trades)
        print(f"\n  📅 First Buy:           {first_date}")
        print(f"  📅 Last Buy:            {last_date}")

    if current_price:
        current_value = total_btc * current_price
        pnl = current_value - total_cost
        pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0
        emoji = "🟢" if pnl >= 0 else "🔴"

        print(f"\n  💵 Current BTC Price:   {sym}{current_price:,.2f} ({used_pair})")
        print(f"  🏦 Current Value:       {sym}{current_value:,.2f}")
        print(f"  {emoji} P&L:                {sym}{pnl:,.2f} ({pnl_pct:+.1f}%)")

    print("\n" + "=" * 55)

    # Per-year breakdown
    if trades:
        print("\n  📆 Yearly Breakdown:")
        print("  " + "-" * 50)
        years = {}
        for t in trades:
            year = t["date"][:4]
            if year not in years:
                years[year] = {"btc": 0, "cost": 0, "count": 0}
            years[year]["btc"] += t["btc_amount"]
            years[year]["cost"] += t["total_cost"]
            years[year]["count"] += 1

        for year in sorted(years):
            y = years[year]
            y_avg = y["cost"] / y["btc"] if y["btc"] > 0 else 0
            print(f"  {year}: {y['count']:>3} trades | {y['btc']:.6f} BTC | "
                  f"{sym}{y['cost']:>10,.2f} | avg {sym}{y_avg:>10,.2f}")

    print()


# ---------------------------------------------------------------------------
# Main Menu
# ---------------------------------------------------------------------------
def main():
    print("\n₿ Bitcoin Average Price Calculator")
    print("=" * 40)

    trades = load_trades()
    if trades:
        print(f"  📂 Loaded {len(trades)} existing trades\n")

    while True:
        print("  Options:")
        print("  [1] 🔄 Fetch trades from Binance (Spot + Convert)")
        print("  [2] 📄 Import from CSV file")
        print("  [3] 📝 Add trades manually")
        print("  [4] 📊 Show summary / avg price")
        print("  [5] 🗑️  Clear all trades")
        print("  [6] 🚪 Exit\n")

        choice = input("  Choose (1-6): ").strip()

        if choice == "1":
            api_key, api_secret = get_keys()
            if not api_key or not api_secret:
                print("\n  ❌ Set BINANCE_API_KEY and BINANCE_API_SECRET in .env file first!")
                print("  (See .env.example)\n")
                continue
            print("\n  🔄 Fetching from Binance (Spot + Convert)...\n")
            spot_trades = fetch_all_trades(api_key, api_secret)
            convert_trades = fetch_convert_trades(api_key, api_secret)
            new_trades = spot_trades + convert_trades
            if new_trades:
                # Deduplicate by date+amount
                existing_keys = {(t["date"], t["btc_amount"], t["total_cost"]) for t in trades}
                added = 0
                for t in new_trades:
                    key = (t["date"], t["btc_amount"], t["total_cost"])
                    if key not in existing_keys:
                        trades.append(t)
                        existing_keys.add(key)
                        added += 1
                save_trades(trades)
                print(f"  ✅ Added {added} new trades ({len(new_trades)} total found)\n")
            else:
                print("  ⚠️  No trades found.\n")

        elif choice == "2":
            csv_path = input("  Path to CSV file: ").strip()
            if os.path.exists(csv_path):
                new_trades = import_from_csv(csv_path)
                trades.extend(new_trades)
                save_trades(trades)
                print(f"  ✅ Imported {len(new_trades)} trades\n")
            else:
                print("  ❌ File not found\n")

        elif choice == "3":
            new_trades = manual_entry()
            trades.extend(new_trades)
            if new_trades:
                save_trades(trades)

        elif choice == "4":
            display_summary(trades)

        elif choice == "5":
            confirm = input("  Are you sure? (yes/no): ").strip()
            if confirm.lower() == "yes":
                trades = []
                save_trades(trades)
                print("  🗑️  All trades cleared.\n")

        elif choice == "6":
            print("  👋 Bye!\n")
            sys.exit(0)

        else:
            print("  ❌ Invalid option\n")


if __name__ == "__main__":
    main()
