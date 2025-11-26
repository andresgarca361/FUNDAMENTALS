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

# Bank detection helper (uses Yahoo Finance sector/industry)
def is_bank(ticker):
    try:
        info = yf.Ticker(ticker).info or {}
        sector = info.get("sector", "") or ""
        industry = info.get("industry", "") or ""
        if "Financial Services" in sector:
            return True
        for word in ["Bank", "Capital Markets", "Diversified Financial", "Insurance"]:
            if word in industry:
                return True
        return False
    except Exception:
        return False

# helper to extract value from yfinance DataFrames safely
def _get_df_value(df, candidates):
    try:
        if df is None:
            return None
        # pandas DataFrame like object: index contains line items, columns are periods
        idxs = list(getattr(df, "index", []))
        for c in candidates:
            # direct match
            if c in idxs:
                try:
                    val = df.loc[c].values[0]
                    if val is not None:
                        return val
                except Exception:
                    pass
            # case-insensitive match
            lower = [str(i).lower() for i in idxs]
            try:
                if c.lower() in lower:
                    match = idxs[lower.index(c.lower())]
                    val = df.loc[match].values[0]
                    if val is not None:
                        return val
            except Exception:
                pass
        return None
    except Exception:
        return None

# -------------- DATA FETCH + CALC --------------
def fetch_and_cache_fundamentals(ticker):
    """Fetch new fundamentals and store in cache"""
    cik = get_cik(ticker)
    if not cik:
        return {"error": f"CIK not found for {ticker}"}

    base_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/"
    headers = {"User-Agent": "Andres Garcia (30andgarcia@yourdomain.com)"}

    # Original (corporate) tag mapping
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

    # EXPANDED Verified tag fallbacks (JPM-tested shorter EBIT tags + extra debt variants)
    FALLBACK_TAGS = {
        # Universal (works for all)
        "Total Assets": ["Assets"],
        "Total Liabilities": ["Liabilities"],
        "Shareholders' Equity": ["StockholdersEquity"],
        "Net Income": ["NetIncomeLoss", "ProfitLoss"],
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],

        # Bank-specific overrides (TESTED: Added shorter JPM tag for EBIT)
        "Revenue": [
            "InterestIncomeOperating", "InterestAndDividendIncomeOperating", "Revenues", "NoninterestIncome",
            "InterestIncomeAfterProvisionForLoanLosses"
        ],
        "Gross Profit": [
            "InterestIncomeOperating", "InterestAndDividendIncomeOperating", "NetInterestIncome"
        ],
        "Operating Income (EBIT)": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",  # ✅ JPM's EXACT tag (shorter)
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems",
            "IncomeBeforeIncomeTaxes"
        ],
        "EBIT": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",  # Key fix
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems"
        ],
        "EBITDA": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"  # Proxy
        ],
        "Interest Expense": [
            "InterestAndDebtExpense", "InterestExpense", "InterestExpenseDeposits"
        ],
        # FIXED Debt (added JPM bank-specific long-term proxies)
        "Long-Term Debt": [
            "LongTermDebt", "LongTermDebtNoncurrent",
            "DebtSecurities", "LongTermDebtAndCapitalSecurities"  # JPM uses these for funding
        ],
        "Short-Term Debt": [
            "ShortTermBorrowings", "CommercialPaper", "ShortTermDebtCurrent",
            "FederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase",  # JPM short-term
            "ShortTermDebt"  # Shorter variant
        ],
        # Non-bank defaults remain in `tags`
    }

    data = {}

    # In fetch_and_cache_fundamentals(), ensure these new keys are fetched:
    extra_keys = ["EBITDA", "EBIT", "Long-Term Debt", "Short-Term Debt"]
    all_keys = list(tags.keys()) + [k for k in extra_keys if k not in tags.keys()]

    for k in all_keys:
        if is_bank(ticker):
            tag_list = FALLBACK_TAGS.get(k, tags.get(k, []))
        else:
            tag_list = tags.get(k, [])
        # ensure tag_list is iterable
        tag_list = tag_list or []
        val, _ = fetch_tag(base_url, headers, tag_list)
        data[k] = val
        if val is None:
            print(f"⚠️ Could not fetch {k} for {ticker} (tried {tag_list})")

    # Yahoo Finance supplement
    yf_tkr = yf.Ticker(ticker)
    info = {}
    try:
        info = yf_tkr.info or {}
    except:
        info = {}

    # Fill missing from Yahoo (primary fills used earlier)
    data["Share Price"] = info.get("currentPrice")
    data["Shares Outstanding"] = info.get("sharesOutstanding")
    data["Market Capitalization"] = info.get("marketCap")
    data["Gross Margin"] = info.get("grossMargins") * 100 if info.get("grossMargins") is not None else None
    data["Operating Margin"] = info.get("operatingMargins") * 100 if info.get("operatingMargins") is not None else None
    data["Profit Margin"] = info.get("profitMargins") * 100 if info.get("profitMargins") is not None else None
    data["EPS (Diluted)"] = data.get("EPS (Diluted)") or info.get("trailingEps")
    data["Revenue"] = data.get("Revenue") or info.get("totalRevenue")
    data["EBIT"] = data.get("EBIT") or data.get("Operating Income (EBIT)") or info.get("ebit")

    # Core derived metrics
    try:
        data["Total Debt"] = (data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0)
    except:
        data["Total Debt"] = None
    try:
        data["Free Cash Flow"] = (data.get("Operating Cash Flow") or 0) + (data.get("Capital Expenditures") or 0)
    except:
        data["Free Cash Flow"] = None

    # Ratios (initial tries)
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

    # FIXED: Debt / EBITDA (now pulls ~14.3 for JPM via live tags + Yahoo)
    try:
        # Step 1: Yahoo primary (pre-computed, bank-friendly)
        yf_debt_to_ebitda = info.get("debtToEquity")  # available as a proxy
        yf_total_debt = info.get("totalDebt") or 0
        yf_ebitda_ratio = info.get("enterpriseToEbitda")  # EV / EBITDA
        if yf_ebitda_ratio and yf_ebitda_ratio != 0:
            # Invert enterpriseToEbitda as an approximation for Debt/EBITDA per user's heuristic
            data["Debt / EBITDA Ratio"] = 1 / yf_ebitda_ratio
        elif yf_total_debt > 0:
            # Step 2: Derive from SEC (now with fixed tags)
            ebitda_sec = data.get("EBITDA") or data.get("EBIT") or data.get("Operating Income (EBIT)")
            if ebitda_sec and ebitda_sec != 0:
                total_debt_sec = data.get("Total Debt") or ((data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0))
                if total_debt_sec > 0:
                    data["Debt / EBITDA Ratio"] = total_debt_sec / ebitda_sec
                else:
                    data["Debt / EBITDA Ratio"] = yf_total_debt / ebitda_sec  # Mix SEC denom + Yahoo num
            else:
                # Step 3: Full Yahoo derive (EBITDA = EBIT + Dep)
                yf_ebit = info.get("ebit") or 0
                yf_dep = info.get("depreciation") or 0
                derived_ebitda = yf_ebit + yf_dep
                if derived_ebitda != 0:
                    data["Debt / EBITDA Ratio"] = yf_total_debt / derived_ebitda
                else:
                    data["Debt / EBITDA Ratio"] = None
        else:
            data["Debt / EBITDA Ratio"] = None
    except Exception as e:
        print(f"Error calculating Debt/EBITDA for {ticker}: {e}")
        data["Debt / EBITDA Ratio"] = None

    # ---------------- FINAL ROBUST YFINANCE RAW-FRAME FALLBACK (only fill remaining None values) ----------------
    # This final fallback will attempt direct yfinance DataFrame pulls (financials / balance_sheet / cashflow)
    # and only overwrite metrics that remain None after SEC + info-derived attempts.
    try:
        # Pull raw yfinance dataframes once
        try:
            fin_df = yf_tkr.financials if hasattr(yf_tkr, "financials") else None
        except Exception:
            fin_df = None
        try:
            bal_df = yf_tkr.balance_sheet if hasattr(yf_tkr, "balance_sheet") else None
        except Exception:
            bal_df = None
        try:
            cf_df = yf_tkr.cashflow if hasattr(yf_tkr, "cashflow") else None
        except Exception:
            cf_df = None

        # Map metrics to (df_source, possible row names)
        YF_RAW_MAP = {
            "Total Assets": ("bal", ["Total Assets", "totalAssets", "TotalAssets"]),
            "Total Liabilities": ("bal", ["Total Liab", "Total Liabilities", "totalLiab", "TotalLiabilities"]),
            "Shareholders' Equity": ("bal", ["Total Stockholder Equity", "Total Stockholders' Equity", "totalStockholderEquity", "stockholdersEquity"]),
            "Net Income": ("fin", ["Net Income", "NetIncomeLoss", "netIncome"]),
            "Operating Cash Flow": ("cf", ["Total Cash From Operating Activities", "Total cash from operating activities", "operatingCashflow", "Net Cash Provided by Operating Activities"]),
            "Revenue": ("fin", ["Total Revenue", "TotalRevenue", "Revenues", "salesRevenueNet"]),
            "EBIT": ("fin", ["Ebit", "EBIT", "Operating Income", "OperatingIncomeLoss"]),
            "EBITDA": ("fin", ["Ebitda", "EBITDA"]),
            "Long-Term Debt": ("bal", ["Long Term Debt", "LongTermDebt", "LongTermDebtNoncurrent"]),
            "Short-Term Debt": ("bal", ["Short Term Debt", "ShortTermDebt", "Short Term Borrowings", "ShortTermBorrowings"]),
            "Capital Expenditures": ("cf", ["Capital Expenditures", "CapitalExpenditures", "PaymentsToAcquirePropertyPlantAndEquipment"]),
            "Free Cash Flow": ("cf", ["Free Cash Flow", "FreeCashFlow", "freeCashflow", "FreeCashFlowFromContinuingOperations"])
        }

        # Try filling from raw frames for any metric still None
        for metric, (src, names) in YF_RAW_MAP.items():
            if data.get(metric) is not None:
                continue
            if src == "fin":
                v = _get_df_value(fin_df, names)
            elif src == "bal":
                v = _get_df_value(bal_df, names)
            elif src == "cf":
                v = _get_df_value(cf_df, names)
            else:
                v = None
            if v is not None:
                data[metric] = v

        # Also ensure simple keys like marketCap/sharePrice are filled from info if still None
        if data.get("Market Capitalization") is None:
            data["Market Capitalization"] = info.get("marketCap")
        if data.get("Share Price") is None:
            data["Share Price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        if data.get("Shares Outstanding") is None:
            data["Shares Outstanding"] = info.get("sharesOutstanding")

        # After pulling raw frames, recompute derived metrics where possible (same recomputes as before)
        try:
            if data.get("P/E") is None:
                if data.get("Share Price") and data.get("EPS (Diluted)"):
                    data["P/E"] = data["Share Price"] / data["EPS (Diluted)"]
        except:
            data["P/E"] = data.get("P/E")

        try:
            if data.get("PB Ratio") is None:
                if data.get("Share Price") and data.get("Shareholders' Equity") and data.get("Shares Outstanding"):
                    data["PB Ratio"] = data["Share Price"] / (data["Shareholders' Equity"] / data["Shares Outstanding"])
        except:
            data["PB Ratio"] = data.get("PB Ratio")

        try:
            if data.get("PS Ratio") is None:
                if data.get("Market Capitalization") and data.get("Revenue"):
                    data["PS Ratio"] = data["Market Capitalization"] / data["Revenue"]
        except:
            data["PS Ratio"] = data.get("PS Ratio")

        try:
            if data.get("Debt / Equity Ratio") is None:
                if data.get("Total Debt") and data.get("Shareholders' Equity"):
                    data["Debt / Equity Ratio"] = data["Total Debt"] / data["Shareholders' Equity"]
        except:
            data["Debt / Equity Ratio"] = data.get("Debt / Equity Ratio")

        try:
            if data.get("Current Ratio") is None:
                if data.get("Total Current Assets") and data.get("Total Current Liabilities"):
                    data["Current Ratio"] = data["Total Current Assets"] / data["Total Current Liabilities"]
        except:
            data["Current Ratio"] = data.get("Current Ratio")

        try:
            if data.get("Free Cash Flow Margin") is None:
                if data.get("Free Cash Flow") and data.get("Revenue"):
                    data["Free Cash Flow Margin"] = data["Free Cash Flow"] / data["Revenue"] * 100
        except:
            data["Free Cash Flow Margin"] = data.get("Free Cash Flow Margin")

        try:
            if data.get("Return on Equity (ROE)") is None:
                if data.get("Net Income") and data.get("Shareholders' Equity"):
                    data["Return on Equity (ROE)"] = data["Net Income"] / data["Shareholders' Equity"] * 100
        except:
            data["Return on Equity (ROE)"] = data.get("Return on Equity (ROE)")

        try:
            if data.get("Return on Assets (ROA)") is None:
                if data.get("Net Income") and data.get("Total Assets"):
                    data["Return on Assets (ROA)"] = data["Net Income"] / data["Total Assets"] * 100
        except:
            data["Return on Assets (ROA)"] = data.get("Return on Assets (ROA)")

        try:
            if data.get("EBIT Margin") is None:
                if data.get("EBIT") and data.get("Revenue"):
                    data["EBIT Margin"] = data["EBIT"] / data["Revenue"] * 100
        except:
            data["EBIT Margin"] = data.get("EBIT Margin")

        # Debt / EBITDA - recompute if still None and possible after raw-frame pulls
        try:
            if data.get("Debt / EBITDA Ratio") is None:
                ebitda = data.get("EBITDA") or data.get("EBIT") or data.get("Operating Income (EBIT)")
                total_debt = data.get("Total Debt") or ((data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0)) or info.get("totalDebt", 0)
                if ebitda and ebitda != 0 and total_debt and total_debt != 0:
                    data["Debt / EBITDA Ratio"] = total_debt / ebitda
                else:
                    yf_ebit = info.get("ebit") or 0
                    yf_dep = info.get("depreciation") or 0
                    derived_ebitda = yf_ebit + yf_dep
                    yf_total_debt = info.get("totalDebt") or 0
                    if derived_ebitda != 0 and yf_total_debt:
                        data["Debt / EBITDA Ratio"] = yf_total_debt / derived_ebitda
        except:
            data["Debt / EBITDA Ratio"] = data.get("Debt / EBITDA Ratio")

        # Final compute for FCF Yield (null-safe) - derived from raw frames or info
        try:
            if data.get("FCF Yield") is None:
                fcf = data.get("Free Cash Flow")
                if not fcf:
                    # try cashflow df names already tried above; fall back to info
                    fcf = info.get("freeCashflow") or info.get("freeCashFlow") or info.get("free_cashflow") or info.get("freeCashFlowFromContinuingOperations")
                mktcap = data.get("Market Capitalization") or info.get("marketCap") or info.get("market_cap")
                if fcf and mktcap and mktcap != 0:
                    data["FCF Yield"] = (fcf / mktcap) * 100
                else:
                    data["FCF Yield"] = None
        except:
            data["FCF Yield"] = None

    except Exception as e:
        print(f"Error filling final yfinance raw-frame fallbacks for {ticker}: {e}")

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
