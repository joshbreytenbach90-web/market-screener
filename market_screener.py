"""
Daily Market Screener for Power BI
==================================
Screens the JSE (Top 40 + large caps), NASDAQ-100 and S&P 500 and writes CSV files
that a Power BI report reads. Run it once a day (see run_daily.bat / README.md).

Outputs (in ./output):
  market_screen.csv    -> the 3 report columns: Top Companies | Undervalued | To Watch
  market_universe.csv  -> every company with all metrics (for drill-through / tooltips)
  run_log.csv          -> one line per run (timestamp, counts, failures)

Data source: Yahoo Finance via the free `yfinance` library (delayed data, best effort).
This is a quantitative screen, not investment advice.
"""
from __future__ import annotations

import io
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- settings
BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
CACHE = BASE / "ticker_cache"
TOP_N = 15                 # rows per column per market
MIN_ANALYSTS = 3           # analyst targets only count when at least this many analysts cover the stock
WORKERS = 3                # parallel Yahoo requests (keep low to avoid rate limits)

# JSE large caps (Yahoo uses the .JO suffix). Edit freely - invalid codes are skipped.
JSE_TICKERS = """
NPN PRX FSR SBK CFR BTI AGL BHG GLN MTN VOD CPI ABG NED SOL GFI ANG HAR IMP SSW VAL
KIO EXX SHP BID BVT CLS WHL MNP REM DSY SLM OMU ANH GRT NRP INP APN MRP PPH N91 OUT
NPH TFG RNI SPP TBS AVI MCG HMN
""".split()

US_FALLBACK = """AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA COST HD PG
JNJ NFLX WMT ABBV BAC CRM ORCL AMD KO CVX MRK PEP ADBE CSCO TMO LIN MCD ACN INTU QCOM""".split()

HEADERS = {"User-Agent": "Mozilla/5.0 (MarketScreener for Power BI)"}


# --------------------------------------------------------------------------- tickers
def _wiki_table(url: str, col_options: tuple[str, ...], min_rows: int) -> list[str]:
    import requests
    html = requests.get(url, headers=HEADERS, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        col = next((c for c in col_options if c in t.columns), None)
        if col and len(t) >= min_rows:
            return [str(s).strip().replace(".", "-") for s in t[col].dropna()]
    raise ValueError(f"No ticker table found at {url}")


def _cached(name: str, loader) -> list[str]:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{name}.txt"
    try:
        tickers = loader()
        f.write_text("\n".join(tickers))
        return tickers
    except Exception as e:  # network/Wikipedia problem -> use last known list
        print(f"  ! could not refresh {name} list ({e}); using cache", file=sys.stderr)
        if f.exists():
            return f.read_text().split()
        return US_FALLBACK


def get_universe() -> dict[str, list[str]]:
    sp = _cached("sp500", lambda: _wiki_table(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol",), 400))
    nq = _cached("nasdaq100", lambda: _wiki_table(
        "https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Symbol"), 90))
    return {"JSE": [f"{t}.JO" for t in JSE_TICKERS], "NASDAQ-100": nq, "S&P 500": sp}


# --------------------------------------------------------------------------- fetching
def fetch_info(ticker: str, tries: int = 3) -> dict | None:
    import yfinance as yf
    for i in range(tries):
        try:
            info = yf.Ticker(ticker).info
            if info and (info.get("currentPrice") or info.get("regularMarketPrice")):
                return info
            return None
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def fetch_history(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        d = yf.download(chunk, period="1y", interval="1d", auto_adjust=True,
                        progress=False, threads=True, group_by="column")
        close = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": chunk[0]})
        frames.append(close)
    return pd.concat(frames, axis=1)


_fx_cache: dict[str, float] = {}


def fx(src: str, dst: str) -> float:
    """Units of dst per 1 unit of src (e.g. fx('USD','ZAR') ~ 18)."""
    if not src or not dst or src == dst:
        return 1.0
    key = f"{src}{dst}"
    if key not in _fx_cache:
        import yfinance as yf
        try:
            _fx_cache[key] = float(yf.Ticker(f"{key}=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            _fx_cache[key] = float("nan")
    return _fx_cache[key]


# --------------------------------------------------------------------------- metrics
MINOR_UNITS = {"ZAc": ("ZAR", 100), "GBp": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100)}


def _num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _bounded(x, lo, hi):
    return x if (pd.notna(x) and lo < x <= hi) else np.nan


def normalise(ticker: str, market: str, info: dict, closes: pd.Series | None, fxfn=fx) -> dict:
    """Turn a raw Yahoo `info` dict into clean, currency-consistent metrics.
    JSE prices are quoted in cents (ZAc) while financials are often in ZAR/USD,
    so all ratios are recalculated in one currency instead of trusting Yahoo's."""
    cur = info.get("currency") or ""
    trade_cur, div = MINOR_UNITS.get(cur, (cur, 1))
    fin_cur = info.get("financialCurrency") or trade_cur
    fin_cur, fin_div = MINOR_UNITS.get(fin_cur, (fin_cur, 1))
    conv = fxfn(fin_cur, trade_cur) / fin_div      # financial-currency amount -> trading major currency

    raw_price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    price = raw_price / div
    shares = _num(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
    mcap = price * shares if pd.notna(shares) else _num(info.get("marketCap"))
    eps = _num(info.get("trailingEps")) * conv
    fwd_eps = _num(info.get("forwardEps")) * conv
    bvps = _num(info.get("bookValue")) * conv
    fcf = _num(info.get("freeCashflow")) * conv

    pe = _bounded(price / eps if eps and eps > 0 else np.nan, 0, 150)
    fpe = _bounded(price / fwd_eps if fwd_eps and fwd_eps > 0 else np.nan, 0, 150)
    pb = _bounded(price / bvps if bvps and bvps > 0 else np.nan, 0, 40)
    peg = _bounded(_num(info.get("trailingPegRatio") or info.get("pegRatio")), 0, 10)
    fcf_y = _bounded(fcf / mcap if mcap else np.nan, -0.5, 0.5)
    dy = _num(info.get("dividendYield"))
    dy = dy / 100 if pd.notna(dy) else _num(info.get("trailingAnnualDividendYield"))
    dy = _bounded(dy, -1e-9, 0.25)

    target = _num(info.get("targetMeanPrice"))
    analysts = _num(info.get("numberOfAnalystOpinions"))
    upside = target / raw_price - 1 if (pd.notna(target) and raw_price and (analysts or 0) >= MIN_ANALYSTS) else np.nan
    upside = _bounded(upside, -0.9, 3)

    r = dict(Market=market, Ticker=ticker, Name=info.get("shortName") or info.get("longName") or ticker,
             Sector=info.get("sector") or "Other", Industry=info.get("industry") or "",
             Currency=trade_cur, Price=price, MarketCap=mcap,
             MarketCapUSD=mcap / fxfn("USD", trade_cur) if trade_cur != "USD" else mcap,
             PE=pe, ForwardPE=fpe, PB=pb, PEG=peg, FCFYield=fcf_y, DividendYield=dy,
             ROE=_bounded(_num(info.get("returnOnEquity")), -5, 5),
             ProfitMargin=_num(info.get("profitMargins")),
             DebtToEquity=_num(info.get("debtToEquity")),
             EarningsGrowth=_bounded(_num(info.get("earningsGrowth")), -5, 10),
             RevenueGrowth=_bounded(_num(info.get("revenueGrowth")), -5, 10),
             AnalystUpside=upside, AnalystCount=analysts,
             RecommendationMean=_num(info.get("recommendationMean")),
             Recommendation=(info.get("recommendationKey") or "").replace("_", " ").title())

    # price momentum from history (unit-free, so cents vs rand doesn't matter)
    s = closes.dropna() if closes is not None else pd.Series(dtype=float)
    def ret(days):
        return s.iloc[-1] / s.iloc[-days - 1] - 1 if len(s) > days else np.nan
    r.update(Return1M=ret(21), Return3M=ret(63), Return6M=ret(126), Return1Y=ret(min(len(s) - 1, 252)) if len(s) > 200 else np.nan,
             AboveMA50=bool(len(s) >= 50 and s.iloc[-1] > s.tail(50).mean()),
             AboveMA200=bool(len(s) >= 200 and s.iloc[-1] > s.tail(200).mean()),
             PctOf52wHigh=s.iloc[-1] / s.max() if len(s) else np.nan)
    return r


# --------------------------------------------------------------------------- scoring
def _pct(s: pd.Series, higher_is_better=True) -> pd.Series:
    return s.rank(pct=True, ascending=higher_is_better)


def _fmt_pct(x, sign=True):
    return "" if pd.isna(x) else (f"{x:+.0%}" if sign else f"{x:.1%}")


def score_market(df: pd.DataFrame, top_n: int = TOP_N) -> pd.DataFrame:
    """Return long-format rows for the three report columns for ONE market."""
    df = df.copy()
    out = []

    # 1) TOP COMPANIES - largest by market value
    top = df.dropna(subset=["MarketCap"]).nlargest(top_n, "MarketCap").copy()
    top["Score"] = top["MarketCap"]
    top["Reason"] = top.apply(lambda r: f"Mkt cap {r.Currency} {r.MarketCap/1e9:,.0f}bn"
                              + (f", 1Y {_fmt_pct(r.Return1Y)}" if pd.notna(r.Return1Y) else ""), axis=1)
    top["Category"] = "Top Companies"
    out.append(top)

    # 2) UNDERVALUED - cheap vs peers in the same market, profitable, not a value trap
    sector_pe = df.groupby("Sector")["PE"].transform("median")
    df["PEvsSector"] = df["PE"] / sector_pe
    value_parts = pd.concat([
        _pct(df["PE"], False), _pct(df["ForwardPE"], False), _pct(df["PB"], False),
        _pct(df["PEG"], False), _pct(df["FCFYield"]), _pct(df["DividendYield"]),
        _pct(df["PEvsSector"], False)], axis=1)
    quality = pd.concat([_pct(df["ROE"]), _pct(df["DebtToEquity"], False)], axis=1).mean(axis=1)
    df["ValueScore"] = 0.75 * value_parts.mean(axis=1) + 0.25 * quality.fillna(0.5)
    ok = (df["PE"].notna() & (value_parts.notna().sum(axis=1) >= 3)
          & (df["PEvsSector"].fillna(1) <= 1.0)          # must be cheaper than its sector
          & (df["ProfitMargin"].fillna(0) > 0) & (df["ROE"].fillna(0.1) > 0.08)
          & (df["Return1Y"].fillna(0) > -0.5))           # avoid collapsing stocks
    under = df[ok].nlargest(top_n, "ValueScore").copy()
    under["Score"] = under["ValueScore"]
    under["Reason"] = under.apply(lambda r: ", ".join(x for x in [
        f"P/E {r.PE:.1f} vs sector {r.PE / r.PEvsSector:.1f}" if pd.notna(r.PEvsSector) else "",
        f"P/B {r.PB:.1f}" if pd.notna(r.PB) else "",
        f"Div {_fmt_pct(r.DividendYield, False)}" if pd.notna(r.DividendYield) and r.DividendYield > 0 else "",
        f"ROE {r.ROE:.0%}" if pd.notna(r.ROE) else ""] if x), axis=1)
    under["Category"] = "Undervalued"
    out.append(under)

    # 3) TO WATCH - momentum + analyst conviction + growth, excluding names already listed
    taken = set(top["Ticker"]) | set(under["Ticker"])
    mom = pd.concat([_pct(df["Return3M"]), _pct(df["Return6M"]), _pct(df["PctOf52wHigh"]),
                     df["AboveMA200"].astype(float)], axis=1).mean(axis=1)
    analyst = pd.concat([_pct(df["AnalystUpside"]), _pct(df["RecommendationMean"], False)], axis=1).mean(axis=1)
    growth = pd.concat([_pct(df["EarningsGrowth"]), _pct(df["RevenueGrowth"])], axis=1).mean(axis=1)
    df["WatchScore"] = 0.4 * mom.fillna(0.5) + 0.3 * analyst.fillna(0.4) + 0.3 * growth.fillna(0.4)
    ok = ~df["Ticker"].isin(taken) & df["AboveMA50"] & (df["Return3M"].fillna(-1) > 0)
    watch = df[ok].nlargest(top_n, "WatchScore").copy()
    watch["Score"] = watch["WatchScore"]
    watch["Reason"] = watch.apply(lambda r: ", ".join(x for x in [
        f"3M {_fmt_pct(r.Return3M)}" if pd.notna(r.Return3M) else "",
        f"analyst upside {_fmt_pct(r.AnalystUpside)}" if pd.notna(r.AnalystUpside) else "",
        f"EPS growth {_fmt_pct(r.EarningsGrowth)}" if pd.notna(r.EarningsGrowth) else "",
        f"rev growth {_fmt_pct(r.RevenueGrowth)}" if pd.notna(r.RevenueGrowth) else "",
        "near 52w high" if pd.notna(r.PctOf52wHigh) and r.PctOf52wHigh > 0.95 else ""] if x), axis=1)
    watch["Category"] = "To Watch"
    out.append(watch)

    res = pd.concat(out, ignore_index=True)
    res["Rank"] = res.groupby("Category").cumcount() + 1
    res["CategoryOrder"] = res["Category"].map({"Top Companies": 1, "Undervalued": 2, "To Watch": 3})
    res["Display"] = res["Name"] + " (" + res["Ticker"].str.replace(".JO", "", regex=False) + ")"
    return res


# --------------------------------------------------------------------------- main
def main() -> None:
    t0 = datetime.now()
    OUT.mkdir(exist_ok=True)
    print(f"[{t0:%H:%M}] Loading ticker lists...")
    universe = get_universe()
    pairs = [(m, t) for m, ts in universe.items() for t in dict.fromkeys(ts)]
    all_tickers = list(dict.fromkeys(t for _, t in pairs))
    print(f"  {len(all_tickers)} unique tickers")

    print("Downloading 1 year of prices...")
    hist = fetch_history(all_tickers)

    print("Downloading fundamentals (takes ~10 min)...")
    infos: dict[str, dict] = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch_info, t): t for t in all_tickers}
        for i, f in enumerate(as_completed(futs), 1):
            if (info := f.result()):
                infos[futs[f]] = info
            if i % 50 == 0:
                print(f"  {i}/{len(all_tickers)}")

    rows = []
    for market, t in pairs:
        if t in infos:
            try:
                rows.append(normalise(t, market, infos[t], hist[t] if t in hist else None))
            except Exception as e:
                print(f"  ! {t}: {e}", file=sys.stderr)
    uni = pd.DataFrame(rows)
    stamp = datetime.now().replace(microsecond=0)
    uni.insert(0, "RunTimestamp", stamp)

    screen = pd.concat([score_market(g) for _, g in uni.groupby("Market")], ignore_index=True)
    cols = ["RunTimestamp", "Market", "Category", "CategoryOrder", "Rank", "Ticker", "Name", "Display",
            "Sector", "Currency", "Price", "MarketCap", "MarketCapUSD", "PE", "ForwardPE", "PB", "PEG",
            "DividendYield", "FCFYield", "ROE", "EarningsGrowth", "RevenueGrowth", "Return1M", "Return3M",
            "Return1Y", "AnalystUpside", "Recommendation", "Score", "Reason"]

    # write to temp files first so Power BI never reads a half-written CSV
    for df, name in [(screen[cols], "market_screen.csv"), (uni, "market_universe.csv")]:
        tmp = OUT / (name + ".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8")
        tmp.replace(OUT / name)

    log = pd.DataFrame([dict(RunTimestamp=stamp, Tickers=len(all_tickers), Loaded=len(infos),
                             Failed=len(all_tickers) - len(infos),
                             Minutes=round((datetime.now() - t0).seconds / 60, 1))])
    lp = OUT / "run_log.csv"
    log.to_csv(lp, mode="a", header=not lp.exists(), index=False)
    print(f"Done: {len(infos)}/{len(all_tickers)} loaded, {len(screen)} screen rows -> {OUT}")


if __name__ == "__main__":
    main()
