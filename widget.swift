import Cocoa
import CommonCrypto

// ---------------------------------------------------------------------------
// Crypto Portfolio Menu Bar Widget
// Reads trades.json + fetches live prices from Binance
// Build: swiftc widget.swift -o CryptoWidget -framework Cocoa
// Run:   ./CryptoWidget
// ---------------------------------------------------------------------------

// MARK: - Data Models

struct StorageInfo {
    let asset: String
    var onBinance: Double = 0
    var onColdStorage: Double = 0
    var total: Double { onBinance + onColdStorage }
}

struct Trade: Codable {
    let date: String
    let from_asset: String
    let to_asset: String
    let amount: Double
    let price_per_unit: Double
    let total_cost: Double
    let cost_currency: String
    let source: String
}

struct BinancePrice: Codable {
    let symbol: String
    let price: String
}

struct AssetSummary {
    let asset: String
    let totalBought: Double
    let totalCost: Double
    let avgPrice: Double
    let currency: String
    let numTrades: Int
    var currentPrice: Double = 0
    var currentValue: Double { totalBought * currentPrice }
    var pnl: Double { currentValue - totalCost }
    var pnlPct: Double { totalCost > 0 ? (pnl / totalCost) * 100 : 0 }
}

// MARK: - Data Loading

func loadTrades() -> [Trade] {
    let url = URL(fileURLWithPath: NSString(string: "~").expandingTildeInPath)
        .appendingPathComponent("btc-avg-calculator")
        .appendingPathComponent("trades.json")
    guard let data = try? Data(contentsOf: url),
          let trades = try? JSONDecoder().decode([Trade].self, from: data) else {
        return []
    }
    return trades
}

func computePortfolio(_ trades: [Trade]) -> [AssetSummary] {
    let fiat: Set<String> = ["EUR", "USD", "USDT", "USDC", "BUSD"]
    let buyTrades = trades.filter { fiat.contains($0.from_asset) }

    var grouped: [String: (cost: Double, amount: Double, count: Int, currency: String)] = [:]
    for t in buyTrades {
        let key = t.to_asset
        var entry = grouped[key] ?? (0, 0, 0, t.cost_currency)
        entry.cost += t.total_cost
        entry.amount += t.amount
        entry.count += 1
        grouped[key] = entry
    }

    return grouped.map { (asset, data) in
        AssetSummary(
            asset: asset,
            totalBought: data.amount,
            totalCost: data.cost,
            avgPrice: data.amount > 0 ? data.cost / data.amount : 0,
            currency: data.currency,
            numTrades: data.count
        )
    }.sorted { $0.asset < $1.asset }
}

func fetchPrice(symbol: String) -> Double? {
    let urlStr = "https://api.binance.com/api/v3/ticker/price?symbol=\(symbol)"
    guard let url = URL(string: urlStr),
          let data = try? Data(contentsOf: url),
          let result = try? JSONDecoder().decode(BinancePrice.self, from: data),
          let price = Double(result.price) else {
        return nil
    }
    return price
}

func pricePair(for asset: String, currency: String) -> String {
    return "\(asset)\(currency)"
}

func currencySymbol(_ currency: String) -> String {
    switch currency {
    case "EUR": return "€"
    case "USD", "USDT", "USDC", "BUSD": return "$"
    default: return currency
    }
}

func assetIcon(_ asset: String) -> String {
    switch asset {
    case "BTC": return "₿"
    case "SOL": return "◎"
    case "ETH": return "⟠"
    case "XRP": return "✕"
    default: return "●"
    }
}

func formatNum(_ value: Double, decimals: Int = 2) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.minimumFractionDigits = decimals
    formatter.maximumFractionDigits = decimals
    formatter.groupingSeparator = ","
    return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
}

// MARK: - .env & Signed Binance API

func loadEnv() -> (key: String, secret: String) {
    let envURL = URL(fileURLWithPath: NSString(string: "~").expandingTildeInPath)
        .appendingPathComponent("btc-avg-calculator")
        .appendingPathComponent(".env")
    guard let content = try? String(contentsOf: envURL, encoding: .utf8) else { return ("", "") }
    var key = ""
    var secret = ""
    for line in content.components(separatedBy: .newlines) {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("BINANCE_API_KEY=") {
            key = String(trimmed.dropFirst("BINANCE_API_KEY=".count))
        } else if trimmed.hasPrefix("BINANCE_API_SECRET=") {
            secret = String(trimmed.dropFirst("BINANCE_API_SECRET=".count))
        }
    }
    return (key, secret)
}

func hmacSHA256(_ message: String, key: String) -> String {
    let keyData = Array(key.utf8)
    let messageData = Array(message.utf8)
    var hmacData = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
    CCHmac(CCHmacAlgorithm(kCCHmacAlgSHA256), keyData, keyData.count, messageData, messageData.count, &hmacData)
    return hmacData.map { String(format: "%02x", $0) }.joined()
}

func signedRequest(endpoint: String, params: [String: String], apiKey: String, apiSecret: String) -> Data? {
    var allParams = params
    allParams["timestamp"] = "\(Int(Date().timeIntervalSince1970 * 1000))"
    let query = allParams.sorted(by: { $0.key < $1.key }).map { "\($0.key)=\($0.value)" }.joined(separator: "&")
    let signature = hmacSHA256(query, key: apiSecret)
    let urlStr = "https://api.binance.com\(endpoint)?\(query)&signature=\(signature)"
    guard let url = URL(string: urlStr) else { return nil }
    var request = URLRequest(url: url)
    request.setValue(apiKey, forHTTPHeaderField: "X-MBX-APIKEY")
    request.timeoutInterval = 10
    return try? NSURLConnection.sendSynchronousRequest(request, returning: nil)
}

struct BinanceBalance: Codable {
    let asset: String
    let free: String
    let locked: String
}

struct BinanceAccount: Codable {
    let balances: [BinanceBalance]
}

struct BinanceWithdrawal: Codable {
    let coin: String
    let amount: String
    let status: Int
}

func fetchStorageInfo(assets: [String]) -> [String: StorageInfo] {
    let (apiKey, apiSecret) = loadEnv()
    guard !apiKey.isEmpty, !apiSecret.isEmpty else { return [:] }

    var result: [String: StorageInfo] = [:]
    for a in assets { result[a] = StorageInfo(asset: a) }

    // Fetch account balances
    if let data = signedRequest(endpoint: "/api/v3/account", params: [:], apiKey: apiKey, apiSecret: apiSecret),
       let account = try? JSONDecoder().decode(BinanceAccount.self, from: data) {
        for b in account.balances {
            if result.keys.contains(b.asset) {
                let free = Double(b.free) ?? 0
                let locked = Double(b.locked) ?? 0
                result[b.asset]?.onBinance = free + locked
            }
        }
    }

    // Fetch completed withdrawals (status=6)
    if let data = signedRequest(endpoint: "/sapi/v1/capital/withdraw/history", params: ["status": "6"], apiKey: apiKey, apiSecret: apiSecret),
       let withdrawals = try? JSONDecoder().decode([BinanceWithdrawal].self, from: data) {
        for w in withdrawals {
            if result.keys.contains(w.coin) {
                let amt = Double(w.amount) ?? 0
                result[w.coin]?.onColdStorage += amt
            }
        }
    }

    return result
}

// MARK: - App Delegate

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
    var portfolio: [AssetSummary] = []
    var storageInfo: [String: StorageInfo] = [:]

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "₿ Loading..."
        statusItem.button?.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .medium)

        refreshData()

        // Refresh every 60 seconds
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            self?.refreshData()
        }
    }

    func refreshData() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let trades = loadTrades()
            var summaries = computePortfolio(trades)

            // Fetch live prices
            for i in 0..<summaries.count {
                let pair = pricePair(for: summaries[i].asset, currency: summaries[i].currency)
                if let price = fetchPrice(symbol: pair) {
                    summaries[i].currentPrice = price
                }
            }

            // Fetch storage breakdown (Binance vs cold wallet)
            let assets = summaries.map { $0.asset }
            let storage = fetchStorageInfo(assets: assets)

            DispatchQueue.main.async {
                self?.portfolio = summaries
                self?.storageInfo = storage
                self?.updateMenu()
                self?.updateTitle()
            }
        }
    }

    func updateTitle() {
        let totalInvested = portfolio.reduce(0) { $0 + $1.totalCost }
        let totalValue = portfolio.reduce(0) { $0 + $1.currentValue }
        let pnl = totalValue - totalInvested
        let arrow = pnl >= 0 ? "▲" : "▼"
        let sign = pnl >= 0 ? "+" : ""
        let pnlPct = totalInvested > 0 ? (pnl / totalInvested) * 100 : 0

        if portfolio.isEmpty {
            statusItem.button?.title = "₿ No trades"
        } else {
            statusItem.button?.title = "₿ €\(formatNum(totalValue))  \(arrow) \(sign)\(formatNum(pnlPct, decimals: 1))%"
        }
    }

    func updateMenu() {
        let menu = NSMenu()

        // Header
        let header = NSMenuItem(title: "📊 Crypto Portfolio", action: nil, keyEquivalent: "")
        header.isEnabled = false
        header.attributedTitle = NSAttributedString(
            string: "📊 Crypto Portfolio",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 14)]
        )
        menu.addItem(header)
        menu.addItem(NSMenuItem.separator())

        // Overall totals
        let totalInvested = portfolio.reduce(0) { $0 + $1.totalCost }
        let totalValue = portfolio.reduce(0) { $0 + $1.currentValue }
        let totalPnl = totalValue - totalInvested
        let totalPnlPct = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0
        let pnlEmoji = totalPnl >= 0 ? "🟢" : "🔴"
        let sign = totalPnl >= 0 ? "+" : ""

        addInfoItem(menu, "💰 Invested:     €\(formatNum(totalInvested))")
        addInfoItem(menu, "🏦 Value:          €\(formatNum(totalValue))")
        addInfoItem(menu, "\(pnlEmoji) P&L:            \(sign)€\(formatNum(abs(totalPnl)))  (\(sign)\(formatNum(totalPnlPct, decimals: 1))%)")
        menu.addItem(NSMenuItem.separator())

        // Per-asset breakdown
        for p in portfolio {
            let sym = currencySymbol(p.currency)
            let icon = assetIcon(p.asset)
            let arrow = p.pnl >= 0 ? "▲" : "▼"
            let pSign = p.pnl >= 0 ? "+" : ""

            let assetHeader = NSMenuItem(title: "", action: nil, keyEquivalent: "")
            assetHeader.attributedTitle = NSAttributedString(
                string: "\(icon) \(p.asset)",
                attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
            )
            assetHeader.isEnabled = false
            menu.addItem(assetHeader)

            let decimals = p.asset == "BTC" ? 8 : 4
            addInfoItem(menu, "    Amount:    \(formatNum(p.totalBought, decimals: decimals)) \(p.asset)")
            addInfoItem(menu, "    Avg Price: \(sym)\(formatNum(p.avgPrice))")
            addInfoItem(menu, "    Now:       \(sym)\(formatNum(p.currentPrice))  \(arrow) \(pSign)\(formatNum(p.pnlPct, decimals: 1))%")
            addInfoItem(menu, "    Value:     \(sym)\(formatNum(p.currentValue))  (\(pSign)\(sym)\(formatNum(abs(p.pnl))))")
            menu.addItem(NSMenuItem.separator())
        }

        // ── Storage Breakdown ──
        if !storageInfo.isEmpty {
            let storageHeader = NSMenuItem(title: "", action: nil, keyEquivalent: "")
            storageHeader.isEnabled = false
            storageHeader.attributedTitle = NSAttributedString(
                string: "📍 Where Your Crypto Is",
                attributes: [.font: NSFont.boldSystemFont(ofSize: 14)]
            )
            menu.addItem(storageHeader)
            menu.addItem(NSMenuItem.separator())

            for p in portfolio {
                guard let info = storageInfo[p.asset] else { continue }
                let icon = assetIcon(p.asset)
                let decimals = p.asset == "BTC" ? 8 : 4

                let assetTitle = NSMenuItem(title: "", action: nil, keyEquivalent: "")
                assetTitle.isEnabled = false
                assetTitle.attributedTitle = NSAttributedString(
                    string: "\(icon) \(p.asset)",
                    attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
                )
                menu.addItem(assetTitle)

                addInfoItem(menu, "    🏦 Binance:      \(formatNum(info.onBinance, decimals: decimals))")
                addInfoItem(menu, "    🔐 Cold Storage:  \(formatNum(info.onColdStorage, decimals: decimals))")
                addInfoItem(menu, "    📊 Total:         \(formatNum(info.total, decimals: decimals))")
                menu.addItem(NSMenuItem.separator())
            }
        }

        // Actions
        let refreshItem = NSMenuItem(title: "🔄 Refresh", action: #selector(refreshClicked), keyEquivalent: "r")
        refreshItem.target = self
        menu.addItem(refreshItem)

        let dashboardItem = NSMenuItem(title: "🌐 Open Dashboard", action: #selector(openDashboard), keyEquivalent: "d")
        dashboardItem.target = self
        menu.addItem(dashboardItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    func addInfoItem(_ menu: NSMenu, _ text: String) {
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.isEnabled = false
        item.attributedTitle = NSAttributedString(
            string: text,
            attributes: [.font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)]
        )
        menu.addItem(item)
    }

    @objc func refreshClicked() {
        statusItem.button?.title = "₿ Updating..."
        refreshData()
    }

    @objc func openDashboard() {
        // Start the dashboard server if it's not already running
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let appPath = "\(home)/btc-avg-calculator/app.py"

        // Check if port 8080 is already in use
        let checkTask = Process()
        checkTask.executableURL = URL(fileURLWithPath: "/bin/sh")
        checkTask.arguments = ["-c", "lsof -ti :8080"]
        let pipe = Pipe()
        checkTask.standardOutput = pipe
        checkTask.standardError = FileHandle.nullDevice
        try? checkTask.run()
        checkTask.waitUntilExit()
        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""

        if output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            // Server not running — start it via shell in the background
            let serverTask = Process()
            serverTask.executableURL = URL(fileURLWithPath: "/bin/sh")
            serverTask.arguments = ["-c", "cd \(home)/btc-avg-calculator && python3 app.py &"]
            serverTask.standardOutput = FileHandle.nullDevice
            serverTask.standardError = FileHandle.nullDevice
            try? serverTask.run()
            // Give the server a moment to start
            Thread.sleep(forTimeInterval: 1.5)
        }

        NSWorkspace.shared.open(URL(string: "http://localhost:8080")!)
    }

    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

// MARK: - Main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory) // No dock icon
app.run()
