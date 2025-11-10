from flask import Flask, request, jsonify
import requests
import yfinance as yf
from time import sleep, time

app = Flask(__name__)

CACHE = {}  # cache[ticker] = {"timestamp": float, "data": {...}}
CACHE_TTL = 3600  # refresh every hour

VALID_METRICS = {
    "P/E", "PE Ratio", "PB Ratio", "PS Ratio", "EV/EBITDA Ratio", "EV/Sales Ratio",
    "P/FCF Ratio", "Gross Margin", "Operating Margin", "Profit Margin",
    "Return on Equity (ROE)", "Return on Assets (ROA)", "Return on Capital (ROIC)",
    "Free Cash Flow Margin", "EBIT Margin", "Market Capitalization", "Total Debt",
    "Cash & Equivalents", "Effective Tax Rate", "Interest Expense", "Revenue Growth (YoY)",
    "Net Income Growth", "Free Cash Flow Growth", "Dividend Growth", "EPS Growth",
    "Debt / Equity Ratio", "EBIT", "Current Ratio", "Debt / EBITDA Ratio",
    "Short-Term Investments", "Total Current Liabilities", "Debt / FCF Ratio",
    "Total Current Assets", "Total Assets", "Retained Earnings", "Shareholders' Equity",
    "Total Liabilities", "Free Cash Flow", "Operating Cash Flow", "FCF Yield",
    "Capital Expenditures", "Net Income"
}

# ---------------- HELPERS ----------------
def get_cik(ticker):
    """Get SEC CIK for ticker"""
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "Andres Garcia (30andgarcia@yourdomain.com)"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    mapping = r.json()
    for v in mapping.values():
        if v["ticker"].upper() == ticker.upper():
            return str(v["cik_str"]).zfill(10)
    return None

def get_latest_value(js):
    units = js.get("units", {}).get("USD", [])
    if not units:
        return None, None
    latest = sorted(units, key=lambda x: x.get("end", ""))[-1]
    return latest["val"], latest.get("end")

def fetch_tag(base_url, headers, tag_list):
    for tag in tag_list:
        try:
            resp = requests.get(f"{base_url}{tag}.json", headers=headers)
            if resp.status_code == 200:
                js = resp.json()
                val, end = get_latest_value(js)
                if val is not None:
                    return val, end
        except Exception:
            pass
        sleep(0.15)
    return None, None

# -------------- DATA FETCH + CALC --------------
def fetch_and_cache_fundamentals(ticker):
    """Fetch new fundamentals and store in cache"""
    cik = get_cik(ticker)
    if not cik:
        return {"error": f"CIK not found for {ticker}"}

    base_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/"
    headers = {"User-Agent": "Andres Garcia (30andgarcia@yourdomain.com)"}

    tags = {
        "Total Assets": ["Assets"],
        "Total Liabilities": ["Liabilities"],
        "Shareholders' Equity": ["StockholdersEquity"],
        "Total Current Assets": ["AssetsCurrent"],
        "Total Current Liabilities": ["LiabilitiesCurrent"],
        "Cash & Equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
        "Short-Term Investments": ["MarketableSecuritiesCurrent", "ShortTermInvestments"],
        "Long-Term Debt": ["LongTermDebtNoncurrent"],
        "Short-Term Debt": ["ShortTermBorrowings", "CommercialPaper", "ShortTermDebtCurrent"],
        "Retained Earnings": ["RetainedEarningsAccumulatedDeficit"],
        "Revenue": ["Revenues", "SalesRevenueNet"],
        "Gross Profit": ["GrossProfit"],
        "Operating Income (EBIT)": ["OperatingIncomeLoss"],
        "Net Income": ["NetIncomeLoss"],
        "Interest Expense": ["InterestExpense"],
        "Income Tax Expense": ["IncomeTaxExpenseBenefit"],
        "EPS (Diluted)": ["EarningsPerShareDiluted"],
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Capital Expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "Dividends Paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    }

    data = {}
    for k, v in tags.items():
        val, _ = fetch_tag(base_url, headers, v)
        data[k] = val
        if val is None:
            print(f"⚠️ Could not fetch {k} for {ticker}")

    # Yahoo Finance supplement
    yf_tkr = yf.Ticker(ticker)
    info = {}
    try:
        info = yf_tkr.info or {}
    except:
        info = {}

    # Fill missing from Yahoo
    data["Share Price"] = info.get("currentPrice")
    data["Shares Outstanding"] = info.get("sharesOutstanding")
    data["Market Capitalization"] = info.get("marketCap")
    data["Gross Margin"] = info.get("grossMargins") * 100 if info.get("grossMargins") is not None else None
    data["Operating Margin"] = info.get("operatingMargins") * 100 if info.get("operatingMargins") is not None else None
    data["Profit Margin"] = info.get("profitMargins") * 100 if info.get("profitMargins") is not None else None
    data["EPS (Diluted)"] = data.get("EPS (Diluted)") or info.get("trailingEps")
    data["Revenue"] = data.get("Revenue") or info.get("totalRevenue")
    data["EBIT"] = data.get("Operating Income (EBIT)") or info.get("ebit") or data.get("Operating Income (EBIT)")

    # Core derived metrics
    try:
        data["Total Debt"] = (data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0)
    except:
        data["Total Debt"] = None
    try:
        data["Free Cash Flow"] = (data.get("Operating Cash Flow") or 0) + (data.get("Capital Expenditures") or 0)
    except:
        data["Free Cash Flow"] = None

    # Ratios
    try:
        data["P/E"] = data["Share Price"] / data["EPS (Diluted)"] if data.get("Share Price") and data.get("EPS (Diluted)") else None
    except:
        data["P/E"] = None

    try:
        data["PB Ratio"] = data["Share Price"] / (data["Shareholders' Equity"] / data["Shares Outstanding"]) if data.get("Share Price") and data.get("Shareholders' Equity") and data.get("Shares Outstanding") else None
    except:
        data["PB Ratio"] = None

    try:
        data["PS Ratio"] = data["Market Capitalization"] / data["Revenue"] if data.get("Market Capitalization") and data.get("Revenue") else None
    except:
        data["PS Ratio"] = None

    try:
        data["Debt / Equity Ratio"] = data["Total Debt"] / data["Shareholders' Equity"] if data.get("Total Debt") and data.get("Shareholders' Equity") else None
    except:
        data["Debt / Equity Ratio"] = None

    try:
        data["Current Ratio"] = data["Total Current Assets"] / data["Total Current Liabilities"] if data.get("Total Current Assets") and data.get("Total Current Liabilities") else None
    except:
        data["Current Ratio"] = None

    try:
        data["Free Cash Flow Margin"] = data["Free Cash Flow"] / data["Revenue"] * 100 if data.get("Free Cash Flow") and data.get("Revenue") else None
    except:
        data["Free Cash Flow Margin"] = None

    try:
        data["Return on Equity (ROE)"] = data["Net Income"] / data["Shareholders' Equity"] * 100 if data.get("Net Income") and data.get("Shareholders' Equity") else None
    except:
        data["Return on Equity (ROE)"] = None

    try:
        data["Return on Assets (ROA)"] = data["Net Income"] / data["Total Assets"] * 100 if data.get("Net Income") and data.get("Total Assets") else None
    except:
        data["Return on Assets (ROA)"] = None

    try:
        data["EBIT Margin"] = data["EBIT"] / data["Revenue"] * 100 if data.get("EBIT") and data.get("Revenue") else None
    except:
        data["EBIT Margin"] = None

    # Additional, possible ratios and metrics can be restored using similar logic

    CACHE[ticker] = {"timestamp": time(), "data": data}
    return data

def get_fundamentals(ticker):
    """Return cached data if fresh, else refresh"""
    now = time()
    if ticker in CACHE and now - CACHE[ticker]["timestamp"] < CACHE_TTL:
        return CACHE[ticker]["data"]
    else:
        return fetch_and_cache_fundamentals(ticker)

# -------------- ROUTE --------------
@app.route("/fundamental", methods=["GET"])
def get_metric():
    ticker = request.args.get("ticker", "").upper().strip()
    metric = request.args.get("metric", "").strip()
    if not ticker or not metric:
        return jsonify({"error": "Missing required parameters: ?ticker=XXX&metric=YYY"}), 400
    if metric not in VALID_METRICS:
        return jsonify({"error": f"Invalid metric '{metric}'. Must be one of: {sorted(list(VALID_METRICS))}"}), 400

    data = get_fundamentals(ticker)
    if "error" in data:
        return jsonify(data), 400

    return jsonify({"ticker": ticker, "metric": metric, "value": data.get(metric)})

# -------------- RUN --------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
