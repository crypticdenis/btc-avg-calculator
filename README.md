# ₿ Crypto Portfolio Dashboard

A local portfolio tracker for your Binance Convert trades. Includes a **web dashboard** and a **macOS menu bar widget** — no cloud, no third parties, your keys stay on your machine.

![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue) ![Swift](https://img.shields.io/badge/swift-5.9+-orange) ![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green)

## Features

- 📊 **Web Dashboard** — beautiful dark-mode UI at `localhost:8080`
- 🖥️ **Menu Bar Widget** — native macOS widget showing live P&L in your menu bar
- 🔄 **Auto-refresh** — prices update every 60 seconds
- 💱 **Multi-asset** — tracks BTC, SOL, ETH, XRP, and any other Convert trades
- 🇪🇺 **EUR support** — auto-detects your currency (EUR, USD, etc.)
- 🔒 **Fully local** — API keys never leave your machine
- 📦 **Zero dependencies** — uses only Python standard library + Swift

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/crypticdenis/btc-avg-calculator.git
cd btc-avg-calculator
```

### 2. Set up your API keys

Copy the example file and add your real Binance API keys:

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
```

> **How to get API keys:** Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management), create a new key, and enable **Read Only** permissions. That's all you need.

### 3. Run the Web Dashboard

```bash
python3 app.py
```

Open [http://localhost:8080](http://localhost:8080) in your browser and click **"🔄 Fetch from Binance"** to pull your trades.

### 4. (Optional) Build the macOS Menu Bar Widget

```bash
swiftc widget.swift -o CryptoWidget -framework Cocoa
./CryptoWidget
```

A `₿` icon will appear in your menu bar showing your portfolio value and P&L. Click it for a full breakdown.

**To auto-start on login:** Drag the `CryptoWidget` file into **System Settings → General → Login Items**.

## How It Works

The app fetches your **Convert** trade history from the Binance API (the trades you make via Binance's "Convert" feature, e.g. EUR → BTC, EUR → SOL). It then:

1. Groups trades by asset (BTC, SOL, etc.)
2. Calculates your **average buy price** per asset
3. Fetches **live prices** from Binance
4. Shows your **P&L** (profit & loss) per asset and overall

> **Note:** Only Binance Convert trades are supported. Spot market trades use a different API endpoint. Only trades from 2026 onwards are included by default.

## Files

| File | Description |
|---|---|
| `app.py` | Web dashboard server (runs on `localhost:8080`) |
| `widget.swift` | macOS menu bar widget (native Swift) |
| `btc_avg_calculator.py` | Original CLI version |
| `.env.example` | Template for API keys |
| `sample_trades.csv` | Example CSV for manual import |
| `trades.json` | Local cache of fetched trades (auto-generated) |

## Security

- Your `.env` file with API keys is **gitignored** and never committed
- API keys only need **Read Only** permissions
- Everything runs **locally** — no data is sent anywhere except Binance's API
- `trades.json` stores your trade history locally for offline access

## Requirements

- **macOS** (for the menu bar widget)
- **Python 3.9+** (pre-installed on macOS)
- **Swift** (pre-installed via Xcode Command Line Tools)
- A **Binance** account with API keys

## License

MIT
