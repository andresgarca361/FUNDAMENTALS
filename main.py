from flask import Flask, request, jsonify
import requests
import yfinance as yf
import pandas as pd
from time import sleep, time

app = Flask(__name__)

CACHE = {}
CACHE_TTL = 3600  # 1 hour

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

def safe_div(a, b, mult=1):
    try:
        if a is not None and b not in (None, 0):
            return (a / b) * mult
    except Exception:
        pass
    return None

def get_annual_from_yf(tkr):
    out = {}
    try:
        fin = tkr.financials
        if fin is not None and not fin.empty:
            df = fin.copy()
            for label in ["Total Revenue", "TotalRevenue", "Revenue"]:
                if label in df.index:
                    out["revenue_annual"] = df.loc[label].dropna().values.tolist()
                    break
            for label in ["Net Income", "NetIncome"]:
                if label in df.index:
                    out["netincome_annual"] = df.loc[label].dropna().values.tolist()
                    break
    except Exception:
        pass

    try:
        eps_df = tkr.earnings
        if eps_df is not None and not eps_df.empty:
            out["eps_annual"] = eps_df["Earnings"].dropna().tolist()
    except Exception:
        pass

    try:
        cf = tkr.cashflow
        if cf is not None and not cf.empty:
            for label in ["Total Cash From Operating Activities", "Net Cash Provided By Operating Activities"]:
                if label in cf.index:
                    out["ocf_annual"] = cf.loc[label].dropna().values.tolist()
                    break
            for label in ["Capital Expenditures", "Purchases of property, plant and equipment"]:
                if label in cf.index:
                    out["capex_annual"] = cf.loc[label].dropna().values.tolist()
                    break
    except Exception:
        pass

    try:
        divs = tkr.dividends
        if divs is not None and not divs.empty:
            out["dividends_series"] = divs.copy()
    except Exception:
        pass

    return out

# ---------------- FUNDAMENTAL FETCH ----------------
def fetch_and_cache_fundamentals(ticker):
    cik = get_cik(ticker)
    if not cik:
        return {"error": f"CIK not found for {ticker}"}

    base_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/"
    headers = {"User-Agent": "Andres Garcia (30andgarcia@yourdomain.com)"}

    tags = {
        "Total Assets": ["Assets"],
        "Total Liabilities": ["Liabilities"],
        "Shareholders' Equity": ["StockholdersEquity"],
        "Revenue": ["Revenues", "SalesRevenueNet"],
        "Gross Profit": ["GrossProfit"],
        "Operating Income (EBIT)": ["OperatingIncomeLoss"],
        "Net Income": ["NetIncomeLoss"],
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Capital Expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "Dividends Paid": ["PaymentsOfDividends"],
        "Interest Expense": ["InterestExpense"],
        "EPS (Diluted)": ["EarningsPerShareDiluted"]
    }

    data, sources = {}, {}
    for label, tag_list in tags.items():
        val, dt = fetch_tag(base_url, headers, tag_list)
        data[label] = val
        sources[label] = "SEC" if val is not None else None

    yf_tkr = yf.Ticker(ticker)
    try:
        info = yf_tkr.info or {}
    except Exception:
        info = {}

    # fallback fill
    def yfill(field, ykey, mult=1):
        if data.get(field) is None and info.get(ykey) is not None:
            data[field] = info[ykey] * mult
            sources[field] = f"YF.info.{ykey}"

    yfill("Revenue", "totalRevenue")
    yfill("Net Income", "netIncomeToCommon")
    yfill("Market Capitalization", "marketCap")
    yfill("Gross Margin", "grossMargins", 100)
    yfill("Operating Margin", "operatingMargins", 100)
    yfill("Profit Margin", "profitMargins", 100)

    data["Share Price"] = info.get("currentPrice")
    data["Shares Outstanding"] = info.get("sharesOutstanding")

    # compute margins safely
    if data.get("Gross Margin") is None and data.get("Gross Profit") and data.get("Revenue"):
        gm = safe_div(data["Gross Profit"], data["Revenue"], 100)
        if gm and 0 < gm < 100:
            data["Gross Margin"] = gm

    # growth metrics
    yf_annual = get_annual_from_yf(yf_tkr)

    def clean_growth(series, label):
        if series and len(series) >= 2:
            last, prev = series[0], series[1]
            growth = safe_div(last - prev, prev, 100)
            if growth is not None and not (-5 < growth < 200):
                growth = None
            data[label] = growth
            sources[label] = "YF.annual_series"
        else:
            data[label] = None

    clean_growth(yf_annual.get("revenue_annual"), "Revenue Growth (YoY)")
    clean_growth(yf_annual.get("netincome_annual"), "Net Income Growth")

    try:
        ocf, capex = yf_annual.get("ocf_annual"), yf_annual.get("capex_annual")
        if ocf and capex and len(ocf) >= 2 and len(capex) >= 2:
            fcf_last, fcf_prev = ocf[0] - capex[0], ocf[1] - capex[1]
            growth = safe_div(fcf_last - fcf_prev, fcf_prev, 100)
            if growth is not None and not (-5 < growth < 200):
                growth = None
            data["Free Cash Flow Growth"] = growth
            sources["Free Cash Flow Growth"] = "YF.cashflow"
    except Exception:
        data["Free Cash Flow Growth"] = None

    clean_growth(yf_annual.get("eps_annual"), "EPS Growth")

    # fixed Dividend Growth
    try:
        divs = yf_tkr.dividends
        if divs is not None and not divs.empty:
            divs.index = divs.index - pd.Timedelta(days=1)
            ann = divs.groupby(divs.index.year).sum().sort_index()
            this_year = pd.Timestamp.now().year
            if this_year in ann.index and len(ann.index) >= 3:
                ann = ann.drop(this_year)
            if len(ann) >= 2:
                prev, last = ann.iloc[-2], ann.iloc[-1]
                growth = safe_div(last - prev, prev, 100)
                if growth is not None and not (-5 < growth < 200):
                    growth = None
                data["Dividend Growth"] = growth
                sources["Dividend Growth"] = "YF.dividends_yearly_fixed"
            else:
                data["Dividend Growth"] = None
    except Exception:
        data["Dividend Growth"] = None

    data["_sources"] = sources
    CACHE[ticker] = {"timestamp": time(), "data": data}
    return data

def get_fundamentals(ticker):
    now = time()
    if ticker in CACHE and now - CACHE[ticker]["timestamp"] < CACHE_TTL:
        return CACHE[ticker]["data"]
    return fetch_and_cache_fundamentals(ticker)

@app.route("/fundamental", methods=["GET"])
def get_metric():
    ticker = request.args.get("ticker", "").upper().strip()
    metric = request.args.get("metric", "").strip()
    if not ticker or not metric:
        return jsonify({"error":"Missing required parameters: ?ticker=XXX&metric=YYY"}), 400
    if metric not in VALID_METRICS:
        return jsonify({"error": f"Invalid metric '{metric}'. Must be one of: {sorted(list(VALID_METRICS))}"}), 400

    data = get_fundamentals(ticker)
    if "error" in data:
        return jsonify(data), 400
    return jsonify({"ticker": ticker, "metric": metric, "value": data.get(metric)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
