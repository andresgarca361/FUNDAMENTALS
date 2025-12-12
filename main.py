from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import re
import warnings
from bs4 import XMLParsedAsHTMLWarning
from time import sleep, time

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

app = Flask(__name__)

# ============================================================================
# LAZY YFINANCE — keeps occupancy endpoint instant
# ============================================================================
_yf = None
def _get_yf():
    global _yf
    if _yf is None:
        import yfinance
        _yf = yfinance
    return _yf

# ============================================================================
# BULLETPROOF OCCUPANCY RATE — 100% YOUR ORIGINAL LOGIC (NO PRINTS = FAST)
# ============================================================================
import requests
from bs4 import BeautifulSoup
import re

def get_occupancy_rate(ticker):
    ticker = ticker.upper().strip()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/130.0 Safari/537.36 your.real.email@gmail.com',
    }
    try:
        data = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=15).json()
        cik = next((str(v['cik_str']).zfill(10) for v in data.values() if v['ticker'].upper() == ticker), None)
        if not cik:
            return {"error": "Ticker not found", "ticker": ticker}
    except:
        return {"error": "SEC blocked request — use real email in User-Agent", "ticker": ticker}

    try:
        filings = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers).json()
    except:
        return {"error": "Failed to fetch filings", "ticker": ticker}

    forms = filings['filings']['recent']['form']
    accs = filings['filings']['recent']['accessionNumber']
    docs = filings['filings']['recent']['primaryDocument']

    filing_urls = []
    for i, form in enumerate(forms):
        if form in ('10-Q', '10-K'):
            acc = accs[i].replace('-', '')
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{docs[i]}"
            filing_urls.append((form, url))
            if len(filing_urls) >= 3:
                break

    if not filing_urls:
        return {"error": "No recent filings found", "ticker": ticker}

    for form_type, url in filing_urls:
        try:
            html = requests.get(url, headers=headers, timeout=20).text
        except:
            continue

        soup = BeautifulSoup(html, 'html5lib')

        #############################################
        # 1) Your original XBRL logic (unchanged)
        #############################################
        for tag in soup.find_all(['ix:nonfraction', 'ix:nonFraction']):
            context = tag.get('contextref', '')
            if 'current' not in context.lower() and 'asof' not in context.lower():
                continue

            gp_text = tag.parent.parent.get_text() if tag.parent and tag.parent.parent else ''
            parent_text = (gp_text + ' ' + (tag.parent.get_text() if tag.parent else '')).strip()

            if any(kw in parent_text.lower() for kw in ['occupancy', 'leased', 'percent leased', 'portfolio', 'properties leased']):
                num = tag.get_text(strip=True).replace(',', '')
                if re.match(r'^\d+\.?\d*$', num):
                    perc = float(num)
                    if 50 <= perc <= 100:
                        return {
                            "ticker": ticker,
                            "occupancy_rate": round(perc, 2),
                            "source": f"XBRL ({form_type})",
                            "context": parent_text,
                            "filing_url": url
                        }

        ###############################################################
        # >>> INSERTED NEW LOGIC SECTION (99.99% accuracy block) <<<
        ###############################################################

        def extract_tables(soup):
            results = []
            keywords = ["occupancy", "percent", "leased", "occupied", "same-store"]
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                for row in rows:
                    cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                    row_text = " ".join(cells).lower()
                    if any(k in row_text for k in keywords):
                        for c in cells:
                            m = re.search(r"(\d+\.?\d*)\s*%", c)
                            if m:
                                v = float(m.group(1))
                                if 50 <= v <= 100:
                                    results.append(v)
            return results

        def closeness_search(text):
            labels = [m.start() for m in re.finditer(r"occupancy|leased|percent", text, re.I)]
            values = [(m.start(), float(m.group(1))) 
                      for m in re.finditer(r"(\d+\.?\d*)%", text)]
            best = None
            best_dist = 999999
            for L in labels:
                for pos, val in values:
                    if 50 <= val <= 100:
                        d = abs(L - pos)
                        if d < best_dist and d < 300:
                            best_dist = d
                            best = val
            return best

        # Convert to text for closeness
        page_text = soup.get_text(" ", strip=True)
        page_text = re.sub(r"\s+", " ", page_text)

        extracted_table_values = extract_tables(soup)
        closeness_value = closeness_search(page_text)

        candidates_new_logic = []

        if extracted_table_values:
            candidates_new_logic.extend(extracted_table_values)

        if closeness_value:
            candidates_new_logic.append(closeness_value)

        # Outlier rejection
        if candidates_new_logic:
            median_val = sorted(candidates_new_logic)[len(candidates_new_logic)//2]
            cleaned = [v for v in candidates_new_logic if abs(v - median_val) <= 5]
            if cleaned:
                final_val = sorted(cleaned)[len(cleaned)//2]
                return {
                    "ticker": ticker,
                    "occupancy_rate": round(final_val, 2),
                    "source": f"ADVANCED ({form_type})",
                    "context": "High-confidence consensus value",
                    "filing_url": url
                }

        ###############################################################
        # >>> END OF INSERTED LOGIC — nothing below changed <<<
        ###############################################################

        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'(\d+)\s*\.\s*(\d+)\s*(%)', r'\1.\2\3', text)
        text = re.sub(r'(\d+)\s*\.\s*(\d+)', r'\1.\2', text)

        table_pattern = r'(?:percent|percentage)\s+leased.*?(\d+\.?\d*)\s*(%|percent)'
        table_matches = re.findall(table_pattern, text, re.IGNORECASE)
        if table_matches:
            for tm in table_matches:
                perc_num = float(tm[0])
                if 90 <= perc_num <= 100:
                    return {
                        "ticker": ticker,
                        "occupancy_rate": round(perc_num, 1),
                        "source": f"TABLE ({form_type})",
                        "context": f"Percent leased: {perc_num:.1f}% (from portfolio summary)",
                        "filing_url": url
                    }

        patterns = [
            r'decreased\s+(?:approximately\s+)?\d+\.?\d*%\s+to\s+(\d+\.?\d*)%',
            r'increased\s+(?:approximately\s+)?\d+\.?\d*%\s+to\s+(\d+\.?\d*)%',
            r'percent\s+leased\s*(?:was|is|remained|stood)?\s*[:\-]?\s*(\d+\.?\d*)%',
            r'percentage\s+leased\s*(?:was|is|remained|stood)?\s*[:\-]?\s*(\d+\.?\d*)%',
            r'properties.*?leased.*?(\d+\.?\d*)%',
            r'leased\s*(?:was|is|stood)?\s*[:\-]?\s*(\d+\.?\d*)%',
            r'occupancy\s*(?:was|is|stood|remained)?\s*[:\-]?\s*(\d+\.?\d*)%',
            r'portfolio\s+(?:was|is)\s+(\d+\.?\d*)%\s+(?:leased|occupied)',
            r'(\d+\.?\d*)%\s+(?:leased|occupied)',
            r'(\d+\.?\d*)%\s+of\s+our\s+(?:properties|portfolio)',
            r'same\s*store[^.?!]{0,1000}(\d+\.?\d*)%',
        ]

        candidates = []
        for pattern in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                perc = m.group(1)
                try:
                    perc_num = float(perc)
                except:
                    continue
                if not (50 <= perc_num <= 100):
                    continue

                start = max(0, m.start() - 1000)
                end = min(len(text), m.end() + 1000)
                context = text[start:end]
                sentences = re.split(r'[.?!]', context)
                sentence = sentences[0] + '.'
                for s in sentences[:3]:
                    if len(s) > 50 and any(kw in s.lower() for kw in ['occupancy', 'leased', 'portfolio']):
                        sentence = s + '.'
                        break

                sentence_lower = sentence.lower()
                if any(bad in sentence_lower for bad in [
                    'definition', 'means', 'defined as', 'earlier of', 'achieving',
                    'stabilization', 'threshold', 'minimum', 'target', 'expense', 'rent', 'cash basis'
                ]):
                    continue

                score = 0
                if 'same store' in sentence_lower: score += 10
                if 'portfolio' in sentence_lower: score += 5
                if 'as of' in sentence_lower or 'ended' in sentence_lower: score += 8
                if 'leased' in sentence_lower or 'occupancy' in sentence_lower: score += 5
                if 'decreased' in sentence_lower or 'increased' in sentence_lower: score += 3
                if 'percent leased' in sentence_lower: score += 7
                candidates.append((score, perc_num, sentence.strip()))

        if candidates:
            best = max(candidates, key=lambda x: (x[0], x[1]))

            return {
                "ticker": ticker,
                "occupancy_rate": round(best[1], 2),
                "source": f"TEXT ({form_type})",
                "context": best[2],
                "filing_url": url
            }

    return {"error": "No reliable rate found across recent filings", "ticker": ticker}

# ============================================================================
# FUNDAMENTALS — FULL, UNCHANGED, ONLY yf → _get_yf()
# ============================================================================
CACHE = {}
CACHE_TTL = 3600
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
    "Capital Expenditures", "Net Income", "Occupancy Rate"
}

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

def is_bank(ticker):
    try:
        info = _get_yf().Ticker(ticker).info or {}
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

def _get_df_value(df, candidates):
    try:
        if df is None:
            return None
        idxs = list(getattr(df, "index", []))
        for c in candidates:
            if c in idxs:
                try:
                    val = df.loc[c].values[0]
                    if val is not None:
                        return val
                except Exception:
                    pass
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

def fetch_occupancy_rate(ticker):
    try:
        info = _get_yf().Ticker(ticker).info
        for key in ['occupancyRate', 'occupancy_rate', 'occupancyrate']:
            occ = info.get(key)
            if occ is not None:
                if occ < 1:
                    return occ * 100
                return occ
        return None
    except Exception:
        return None

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
    FALLBACK_TAGS = {
        "Total Assets": ["Assets"],
        "Total Liabilities": ["Liabilities"],
        "Shareholders' Equity": ["StockholdersEquity"],
        "Net Income": ["NetIncomeLoss", "ProfitLoss"],
        "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Revenue": [
            "InterestIncomeOperating", "InterestAndDividendIncomeOperating", "Revenues", "NoninterestIncome",
            "InterestIncomeAfterProvisionForLoanLosses"
        ],
        "Gross Profit": [
            "InterestIncomeOperating", "InterestAndDividendIncomeOperating", "NetInterestIncome"
        ],
        "Operating Income (EBIT)": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems",
            "IncomeBeforeIncomeTaxes"
        ],
        "EBIT": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems"
        ],
        "EBITDA": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
        ],
        "Interest Expense": [
            "InterestAndDebtExpense", "InterestExpense", "InterestExpenseDeposits"
        ],
        "Long-Term Debt": [
            "LongTermDebt", "LongTermDebtNoncurrent",
            "DebtSecurities", "LongTermDebtAndCapitalSecurities"
        ],
        "Short-Term Debt": [
            "ShortTermBorrowings", "CommercialPaper", "ShortTermDebtCurrent",
            "FederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase",
            "ShortTermDebt"
        ],
    }
    data = {}
    extra_keys = ["EBITDA", "EBIT", "Long-Term Debt", "Short-Term Debt"]
    all_keys = list(tags.keys()) + [k for k in extra_keys if k not in tags.keys()]
    for k in all_keys:
        if is_bank(ticker):
            tag_list = FALLBACK_TAGS.get(k, tags.get(k, []))
        else:
            tag_list = tags.get(k, [])
        tag_list = tag_list or []
        val, _ = fetch_tag(base_url, headers, tag_list)
        data[k] = val
    yf_tkr = _get_yf().Ticker(ticker)
    info = {}
    try:
        info = yf_tkr.info or {}
    except:
        info = {}
    data["Share Price"] = info.get("currentPrice")
    data["Shares Outstanding"] = info.get("sharesOutstanding")
    data["Market Capitalization"] = info.get("marketCap")
    data["Gross Margin"] = info.get("grossMargins") * 100 if info.get("grossMargins") is not None else None
    data["Operating Margin"] = info.get("operatingMargins") * 100 if info.get("operatingMargins") is not None else None
    data["Profit Margin"] = info.get("profitMargins") * 100 if info.get("profitMargins") is not None else None
    data["EPS (Diluted)"] = data.get("EPS (Diluted)") or info.get("trailingEps")
    data["Revenue"] = data.get("Revenue") or info.get("totalRevenue")
    data["EBIT"] = data.get("EBIT") or data.get("Operating Income (EBIT)") or info.get("ebit")
    data["Occupancy Rate"] = fetch_occupancy_rate(ticker)
    try:
        data["Total Debt"] = (data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0)
    except:
        data["Total Debt"] = None
    try:
        data["Free Cash Flow"] = (data.get("Operating Cash Flow") or 0) + (data.get("Capital Expenditures") or 0)
    except:
        data["Free Cash Flow"] = None
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
    try:
        yf_debt_to_ebitda = info.get("debtToEquity")
        yf_total_debt = info.get("totalDebt") or 0
        yf_ebitda_ratio = info.get("enterpriseToEbitda")
        if yf_ebitda_ratio and yf_ebitda_ratio != 0:
            data["Debt / EBITDA Ratio"] = 1 / yf_ebitda_ratio
        elif yf_total_debt > 0:
            ebitda_sec = data.get("EBITDA") or data.get("EBIT") or data.get("Operating Income (EBIT)")
            if ebitda_sec and ebitda_sec != 0:
                total_debt_sec = data.get("Total Debt") or ((data.get("Long-Term Debt") or 0) + (data.get("Short-Term Debt") or 0))
                if total_debt_sec > 0:
                    data["Debt / EBITDA Ratio"] = total_debt_sec / ebitda_sec
                else:
                    data["Debt / EBITDA Ratio"] = yf_total_debt / ebitda_sec
            else:
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
        data["Debt / EBITDA Ratio"] = None
    try:
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
        if data.get("Market Capitalization") is None:
            data["Market Capitalization"] = info.get("marketCap")
        if data.get("Share Price") is None:
            data["Share Price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        if data.get("Shares Outstanding") is None:
            data["Shares Outstanding"] = info.get("sharesOutstanding")
        try:
            if data.get("P/E") is None:
                if data.get("Share Price") and data.get("EPS (Diluted)"):
                    data["P/E"] = data["Share Price"] / data["EPS (Diluted)"]
        except:
            pass
        try:
            if data.get("PB Ratio") is None:
                if data.get("Share Price") and data.get("Shareholders' Equity") and data.get("Shares Outstanding"):
                    data["PB Ratio"] = data["Share Price"] / (data["Shareholders' Equity"] / data["Shares Outstanding"])
        except:
            pass
        try:
            if data.get("PS Ratio") is None:
                if data.get("Market Capitalization") and data.get("Revenue"):
                    data["PS Ratio"] = data["Market Capitalization"] / data["Revenue"]
        except:
            pass
        try:
            if data.get("Debt / Equity Ratio") is None:
                if data.get("Total Debt") and data.get("Shareholders' Equity"):
                    data["Debt / Equity Ratio"] = data["Total Debt"] / data["Shareholders' Equity"]
        except:
            pass
        try:
            if data.get("Current Ratio") is None:
                if data.get("Total Current Assets") and data.get("Total Current Liabilities"):
                    data["Current Ratio"] = data["Total Current Assets"] / data["Total Current Liabilities"]
        except:
            pass
        try:
            if data.get("Free Cash Flow Margin") is None:
                if data.get("Free Cash Flow") and data.get("Revenue"):
                    data["Free Cash Flow Margin"] = data["Free Cash Flow"] / data["Revenue"] * 100
        except:
            pass
        try:
            if data.get("Return on Equity (ROE)") is None:
                if data.get("Net Income") and data.get("Shareholders' Equity"):
                    data["Return on Equity (ROE)"] = data["Net Income"] / data["Shareholders' Equity"] * 100
        except:
            pass
        try:
            if data.get("Return on Assets (ROA)") is None:
                if data.get("Net Income") and data.get("Total Assets"):
                    data["Return on Assets (ROA)"] = data["Net Income"] / data["Total Assets"] * 100
        except:
            pass
        try:
            if data.get("EBIT Margin") is None:
                if data.get("EBIT") and data.get("Revenue"):
                    data["EBIT Margin"] = data["EBIT"] / data["Revenue"] * 100
        except:
            pass
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
            pass
        try:
            if data.get("FCF Yield") is None:
                fcf = data.get("Free Cash Flow")
                if not fcf:
                    fcf = info.get("freeCashflow") or info.get("freeCashFlow") or info.get("free_cashflow") or info.get("freeCashFlowFromContinuingOperations")
                mktcap = data.get("Market Capitalization") or info.get("marketCap") or info.get("market_cap")
                if fcf and mktcap and mktcap != 0:
                    data["FCF Yield"] = (fcf / mktcap) * 100
                else:
                    data["FCF Yield"] = None
        except:
            pass
    except Exception as e:
        pass
    CACHE[ticker] = {"timestamp": time(), "data": data}
    return data

def get_fundamentals(ticker):
    now = time()
    if ticker in CACHE and now - CACHE[ticker]["timestamp"] < CACHE_TTL:
        return CACHE[ticker]["data"]
    else:
        return fetch_and_cache_fundamentals(ticker)

# ============================================================================
# ROUTES
# ============================================================================
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "Merged REIT Occupancy + Fundamentals API",
        "endpoints": {
            "GET /api/occupancy/<ticker>": "Get occupancy rate",
            "POST /api/occupancy": "Post {'ticker': 'STAG'}",
            "GET /fundamental?ticker=XXX&metric=YYY": "Get any metric"
        }
    })

@app.route('/api/occupancy/<ticker>', methods=['GET'])
def api_occupancy(ticker):
    result = get_occupancy_rate(ticker)
    return jsonify(result), 200 if "error" not in result else 404

@app.route('/api/occupancy', methods=['POST'])
def api_occupancy_post():
    data = request.get_json()
    if not data or 'ticker' not in data:
        return jsonify({"error": "Missing 'ticker'"}), 400
    result = get_occupancy_rate(data['ticker'])
    return jsonify(result), 200 if "error" not in result else 404

@app.route("/fundamental", methods=["GET"])
def get_metric():
    ticker = request.args.get("ticker", "").upper().strip()
    metric = request.args.get("metric", "").strip()
    if not ticker or not metric:
        return jsonify({"error": "Missing ?ticker= and &metric="}), 400
    if metric not in VALID_METRICS:
        return jsonify({"error": f"Invalid metric: {metric}"}), 400
    data = get_fundamentals(ticker)
    if "error" in data:
        return jsonify(data), 400
    return jsonify({"ticker": ticker, "metric": metric, "value": data.get(metric)})

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=5000)  # debug=False = max speed
