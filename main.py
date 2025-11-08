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
        resp = requests.get(f"{base_url}{tag}.json", headers=headers)
        if resp.status_code == 200:
            js = resp.json()
            val, end = get_latest_value(js)
            if val is not None:
                return val, end
        sleep(0.2)
    return None, None

# -------------- DATA FETCH + CALC --------------
def fetch_and_cache_fundamentals(ticker):
    """Fetch new fundamentals and store in cache"""
    print(f"🔄 Refreshing data for {ticker}")
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
    }

    data = {}
    for k, v in tags.items():
        val, _ = fetch_tag(base_url, headers, v)
        data[k] = val

    # Yahoo Finance supplement
    yf_tkr = yf.Ticker(ticker)
    info = yf_tkr.info
    data["Share Price"] = info.get("currentPrice")
    data["Shares Outstanding"] = info.get("sharesOutstanding")
    data["Market Capitalization"] = info.get("marketCap")

    # Derive EPS if missing
    if not data.get("EPS (Diluted)") and data.get("Net Income") and data.get("Shares Outstanding"):
        data["EPS (Diluted)"] = data["Net Income"] / data["Shares Outstanding"]

    # Core derived metrics
    data["Total Debt"] = (data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0)
    data["Free Cash Flow"] = (data.get("Operating Cash Flow") or 0) + (data.get("Capital Expenditures") or 0)

    # Ratios
    try:
        data["P/E"] = data["Share Price"] / data["EPS (Diluted)"]
    except Exception:
        data["P/E"] = None

    try:
        data["PB Ratio"] = data["Share Price"] / (data["Shareholders' Equity"] / data["Shares Outstanding"])
    except Exception:
        data["PB Ratio"] = None

    try:
        data["PS Ratio"] = data["Market Capitalization"] / data["Revenue"]
    except Exception:
        data["PS Ratio"] = None

    try:
        data["Debt / Equity Ratio"] = data["Total Debt"] / data["Shareholders' Equity"]
    except Exception:
        data["Debt / Equity Ratio"] = None

    try:
        data["Current Ratio"] = data["Total Current Assets"] / data["Total Current Liabilities"]
    except Exception:
        data["Current Ratio"] = None

    try:
        data["Free Cash Flow Margin"] = data["Free Cash Flow"] / data["Revenue"]
    except Exception:
        data["Free Cash Flow Margin"] = None

    try:
        data["Return on Equity (ROE)"] = data["Net Income"] / data["Shareholders' Equity"]
    except Exception:
        data["Return on Equity (ROE)"] = None

    try:
        data["Return on Assets (ROA)"] = data["Net Income"] / data["Total Assets"]
    except Exception:
        data["Return on Assets (ROA)"] = None

    try:
        data["Profit Margin"] = data["Net Income"] / data["Revenue"]
    except Exception:
        data["Profit Margin"] = None

    try:
        data["Operating Margin"] = data["Operating Income (EBIT)"] / data["Revenue"]
    except Exception:
        data["Operating Margin"] = None

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
