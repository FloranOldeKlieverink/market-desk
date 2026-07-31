"""Price providers. One class per source, all returning the same shape:
       [{"date": "YYYY-MM-DD", "close": float}, ...]  oldest first.
Adding a provider means adding a class and listing it in ORDER.

Self-contained on purpose: it does its own HTTP so that adding a price source
never means touching a second file."""
import csv, io, json, time, datetime as _dt
import urllib.request, urllib.error
from common import log

# Some price sites refuse anything that does not look like a browser.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url, tries=3, timeout=30):
    """GET returning bytes. Errors carry the real reason, not a guess."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/csv,application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read()[:100].decode("utf-8", "replace").replace("\n", " ")
            except Exception:                       # noqa: BLE001
                pass
            last = RuntimeError(f"HTTP {e.code}" + (f" {body}" if body else ""))
            if e.code in (400, 401, 403, 404):      # retrying will not help
                break
        except Exception as e:                      # noqa: BLE001
            last = e
        if attempt < tries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise last


# stooq suffix -> yahoo suffix. Same exchanges, different naming.
YAHOO_SUFFIX = {".us": "", ".nl": ".AS", ".de": ".DE", ".fr": ".PA", ".be": ".BR",
                ".es": ".MC", ".it": ".MI", ".uk": ".L", ".ch": ".SW", ".pl": ".WA"}


def to_yahoo(stooq_symbol):
    """asml.nl -> ASML.AS, aapl.us -> AAPL, btcusd -> BTC-USD."""
    if not stooq_symbol:
        return None
    s = str(stooq_symbol).strip().lower()
    if s.endswith("usd") and "." not in s:          # stooq crypto: btcusd, ethusd
        return s[:-3].upper() + "-USD"
    for suf, ysuf in YAHOO_SUFFIX.items():
        if s.endswith(suf):
            return s[: -len(suf)].upper() + ysuf
    return s.upper()


class Yahoo:
    """No account, no key, covers European listings. Tried first.

    Also reports which currency the listing actually trades in, which matters:
    several Amsterdam-listed ETFs (CSPX, VWCE) are USD share classes, and
    assuming euros because the exchange is Dutch gets the valuation wrong."""
    name = "yahoo"
    key_setting = "yahoo"
    last_meta = {}

    @staticmethod
    def history(symbol, _key=None):
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{symbol}?range=5y&interval=1d")
        payload = json.loads(_get(url).decode("utf-8", "replace"))
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            raise ValueError(f"yahoo says: {str(chart['error'])[:90]}")
        result = (chart.get("result") or [None])[0]
        if not result:
            raise ValueError(f"yahoo returned no result for {symbol}")
        meta = result.get("meta") or {}
        Yahoo.last_meta = {"currency": (meta.get("currency") or "").upper() or None,
                           "exchange": meta.get("fullExchangeName") or meta.get("exchangeName")}
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        if not stamps or not closes:
            raise ValueError(f"yahoo returned no closing prices for {symbol}")
        out = []
        for ts, c in zip(stamps, closes):
            if c is None:
                continue
            d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")
            out.append({"date": d, "close": float(c)})
        if len(out) < 30:
            raise ValueError(f"yahoo gave only {len(out)} usable rows for {symbol}")
        out.sort(key=lambda r: r["date"])
        return out


class Stooq:
    """Free, no account. Kept as a second opinion."""
    name = "stooq"
    key_setting = "stooq"

    @staticmethod
    def history(symbol, _key=None):
        raw = _get(f"https://stooq.com/q/d/l/?s={symbol}&i=d").decode("utf-8", "replace").strip()
        if not raw:
            raise ValueError("empty response (blocked or throttled)")
        head = raw[:200].lower()
        if head.startswith("<!doctype") or "<html" in head:
            raise ValueError(f"a web page, not data: {raw[:60]!r}")
        if "no data" in head or "exceeded" in head:
            raise ValueError(f"stooq says: {raw[:80]!r}")
        rows = list(csv.DictReader(io.StringIO(raw)))
        if not rows or "Close" not in rows[0]:
            raise ValueError(f"unexpected format: {raw[:70]!r}")
        out = []
        for r in rows:
            try:
                out.append({"date": r["Date"], "close": float(r["Close"])})
            except (ValueError, KeyError, TypeError):
                continue
        if len(out) < 30:
            raise ValueError(f"only {len(out)} usable rows")
        return out


class AlphaVantage:
    """Last resort. Free key, 25 requests a day, so barely usable for prices."""
    name = "alphavantage"
    key_setting = "alphavantage"

    @staticmethod
    def history(symbol, key=None):
        if not key:
            raise ValueError("no Alpha Vantage key configured")
        url = ("https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
               f"&symbol={symbol}&outputsize=full&apikey={key}")
        payload = json.loads(_get(url).decode("utf-8", "replace"))
        msg = payload.get("Note") or payload.get("Information") or payload.get("Error Message")
        if msg:
            raise ValueError(f"Alpha Vantage: {str(msg)[:130]}")
        ts = payload.get("Time Series (Daily)")
        if not ts:
            raise ValueError(f"no series for {symbol}: {str(payload)[:100]}")
        out = [{"date": d, "close": float(v["4. close"])} for d, v in ts.items()]
        out.sort(key=lambda r: r["date"])
        return out


ORDER = [Yahoo, Stooq, AlphaVantage]


def symbol_for(inst, provider):
    """Yahoo symbols are derived from the stooq ones, so the settings file
    keeps a single column per instrument."""
    if provider.key_setting == "yahoo":
        return inst.get("yahoo") or to_yahoo(inst.get("stooq"))
    return inst.get(provider.key_setting)


# ---------------------------------------------------------------- resolution
def resolve(ticker, cfg, cache):
    """Work out which exchange a bare ticker trades on, by trying suffixes.
    You type SHEL; this discovers shel.uk / SHEL.L and remembers it."""
    key = str(ticker).upper()
    if key in cache:
        return cache[key]
    ar = cfg.get("auto_resolve", {})
    if not ar.get("enabled", True):
        return None
    base = key.lower().replace(".", "-")
    for suf in ar.get("suffixes", [".us"]):
        sym = base + suf
        for P in (Yahoo, Stooq):
            try:
                Yahoo.last_meta = {}
                probe = to_yahoo(sym) if P is Yahoo else sym
                rows = P.history(probe)
            except Exception:                       # noqa: BLE001
                continue
            detected = (Yahoo.last_meta or {}).get("currency") if P is Yahoo else None
            found = {"stooq": sym,
                     "currency": detected or ar.get("currency_by_suffix", {}).get(suf, "EUR"),
                     "points": len(rows), "last": rows[-1]["date"]}
            cache[key] = found
            log(f"   resolved {ticker} -> {sym} via {P.name} ({found['currency']})")
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


def history(inst, av_key=None, days=780, order=None):
    """Try each provider in turn. Returns (rows, provider_name, meta).

    meta carries whatever the feed knows about itself — currency and exchange —
    so the settings file does not have to be right about them."""
    errors = []
    for P in (order or ORDER):
        symbol = symbol_for(inst, P)
        if not symbol:
            continue
        try:
            Yahoo.last_meta = {}
            rows = P.history(symbol, av_key)
            meta = dict(Yahoo.last_meta) if P is Yahoo else {}
            meta["symbol"] = symbol
            return rows[-days:], P.name, meta
        except Exception as e:                      # noqa: BLE001
            errors.append(f"{P.name}({symbol}): {e}")
    raise RuntimeError(f"all providers failed for {inst.get('ticker','?')} — " + " | ".join(errors))
