"""Price providers. One class per source, all returning the same shape:
       [{"date": "YYYY-MM-DD", "close": float}, ...]  oldest first.
Adding a provider means adding a class and listing it in ORDER."""
import csv, io, json
from common import fetch, log


class Stooq:
    """Free, no account, no key. Primary provider."""
    name = "stooq"
    key_setting = "stooq"

    @staticmethod
    def history(symbol, _key=None):
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        raw = fetch(url).decode("utf-8", "replace").strip()
        if not raw or raw.lower().startswith("<!doctype") or "no data" in raw.lower():
            raise ValueError(f"stooq returned no data for {symbol}")
        rows = list(csv.DictReader(io.StringIO(raw)))
        if not rows or "Close" not in rows[0]:
            raise ValueError(f"stooq gave an unexpected format for {symbol}: {raw[:80]!r}")
        out = []
        for r in rows:
            try:
                out.append({"date": r["Date"], "close": float(r["Close"])})
            except (ValueError, KeyError, TypeError):
                continue
        if len(out) < 30:
            raise ValueError(f"stooq gave only {len(out)} usable rows for {symbol}")
        return out


class AlphaVantage:
    """Fallback. Free key, 25 requests a day, so use sparingly."""
    name = "alphavantage"
    key_setting = "alphavantage"

    @staticmethod
    def history(symbol, key=None):
        if not key:
            raise ValueError("no Alpha Vantage key configured")
        url = ("https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
               f"&symbol={symbol}&outputsize=full&apikey={key}")
        payload = json.loads(fetch(url).decode("utf-8", "replace"))
        if "Note" in payload or "Information" in payload:
            raise ValueError("Alpha Vantage rate limit reached")
        ts = payload.get("Time Series (Daily)")
        if not ts:
            raise ValueError(f"Alpha Vantage returned no series for {symbol}: {str(payload)[:120]}")
        out = [{"date": d, "close": float(v["4. close"])} for d, v in ts.items()]
        out.sort(key=lambda r: r["date"])
        return out


ORDER = [Stooq, AlphaVantage]


# ---------------------------------------------------------------- resolution
def resolve(ticker, cfg, cache):
    """Work out which exchange a bare ticker trades on, by trying suffixes.

    You type SHEL; this discovers shel.uk. The answer is written to
    data/symbols.json so it is looked up once, not every run."""
    key = ticker.upper()
    if key in cache:
        return cache[key]
    ar = cfg.get("auto_resolve", {})
    if not ar.get("enabled", True):
        return None
    base = key.lower().replace(".", "-")
    for suf in ar.get("suffixes", [".us"]):
        sym = base + suf
        try:
            rows = Stooq.history(sym)
        except Exception:                           # noqa: BLE001
            continue
        found = {"stooq": sym,
                 "currency": ar.get("currency_by_suffix", {}).get(suf, "EUR"),
                 "points": len(rows), "last": rows[-1]["date"]}
        cache[key] = found
        log(f"   resolved {ticker} -> {sym} ({found['currency']})")
        return found
    log(f"   could not resolve {ticker} on any exchange")
    cache[key] = None
    return None


def parse_universe(cfg):
    """universe entries look like  'shel.uk|GBP|Shell'."""
    out = {}
    for tk, spec in (cfg.get("universe") or {}).items():
        if tk.startswith("_") or not isinstance(spec, str):
            continue
        parts = spec.split("|")
        if len(parts) != 3:
            log(f"   skipping malformed universe entry {tk}: {spec!r}")
            continue
        sym, ccy, name = (p.strip() for p in parts)
        out[tk.upper()] = {"ticker": tk.upper(), "name": name, "currency": ccy,
                           "stooq": sym, "alphavantage": None, "sec_cik": None,
                           "ai_revenue_share": None, "include_in_optimiser": True}
    return out


def history(inst, av_key=None, days=780):
    """Try each provider in turn. Returns (rows, provider_name)."""
    errors = []
    for P in ORDER:
        symbol = inst.get(P.key_setting)
        if not symbol:
            continue
        try:
            rows = P.history(symbol, av_key)
            return rows[-days:], P.name
        except Exception as e:                      # noqa: BLE001
            errors.append(f"{P.name}: {e}")
            log(f"   {inst['ticker']} via {P.name} failed, trying next")
    raise RuntimeError(f"all providers failed for {inst['ticker']} — " + " | ".join(errors))
