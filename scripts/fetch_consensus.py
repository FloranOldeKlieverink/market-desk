"""Analyst consensus from Alpha Vantage's free tier.

The free plan gives rating counts and a mean target but no high/low spread and
no revision history. So we save one snapshot a day into the repo: after a few
weeks the revision history is ours, built from our own record rather than bought.
"""
import json, sys, time, datetime as dt
from common import fetch, settings, read_json, write_json, log, guard, stamp, secret

URL = "https://www.alphavantage.co/query?function=OVERVIEW&symbol={sym}&apikey={key}"


def f(v):
    try:
        x = float(v)
        return x if x == x else None                # NaN check
    except (TypeError, ValueError):
        return None


def parse_overview(p):
    dist = [int(f(p.get(k)) or 0) for k in
            ("AnalystRatingStrongBuy", "AnalystRatingBuy", "AnalystRatingHold",
             "AnalystRatingSell", "AnalystRatingStrongSell")]
    return {
        "n": sum(dist),
        "dist": dist,
        "mid": f(p.get("AnalystTargetPrice")),
        "epsLast": f(p.get("EPS")),
        "ccy": p.get("Currency") or None,
        "lo": None, "hi": None,                     # paid tier only — shown as unavailable
    }


def revisions(hist, sym, field, days):
    """Percentage change in a field over a window, from our own snapshot history."""
    today = dt.date.today()
    cut = (today - dt.timedelta(days=days)).isoformat()
    past = [(d, v.get(sym, {}).get(field)) for d, v in sorted(hist.items()) if d <= cut]
    past = [(d, v) for d, v in past if v]
    now = hist.get(today.isoformat(), {}).get(sym, {}).get(field)
    if not past or not now:
        return None
    old = past[-1][1]
    return round((now - old) / abs(old) * 100, 2) if old else None


@guard("consensus")
def main():
    cfg = settings()
    if not cfg.get("consensus", {}).get("enabled", True):
        log("   consensus disabled in settings")
        return True
    key = secret("ALPHAVANTAGE_KEY")
    today = dt.date.today().isoformat()
    hist = read_json("consensus_history.json", {}) or {}
    hist.setdefault(today, {})
    out, calls = {}, 0

    first = True
    for i in cfg["instruments"]:
        sym = i.get("alphavantage")
        t = i["ticker"]
        if not sym:
            out[t] = {"cov": "na", "why": "No sell-side coverage applies to this instrument."}
            continue
        if not key:
            out[t] = {"cov": "unknown", "why": "No Alpha Vantage key configured yet."}
            continue
        try:
            if not first:
                time.sleep(15)          # free tier allows about five a minute
            first = False
            payload = json.loads(fetch(URL.format(sym=sym, key=key)).decode("utf-8", "replace"))
            if "Note" in payload or "Information" in payload:
                raise ValueError("rate limit reached")
            if not payload or not payload.get("Symbol"):
                out[t] = {"cov": "paid", "why": "No free-tier coverage for this listing."}
                continue
            c = parse_overview(payload)
            calls += 1
            if not c["n"] and not c["mid"]:
                out[t] = {"cov": "paid", "why": "No free-tier coverage for this listing."}
                continue
            c["cov"] = "yes"
            c["ccy"] = c["ccy"] or i["currency"]
            hist[today][t] = {"mid": c["mid"], "eps": c["epsLast"], "dist": c["dist"]}
            c["e1"] = revisions(hist, t, "mid", 30)
            c["e3"] = revisions(hist, t, "mid", 90)
            c["history_days"] = len(hist)
            out[t] = c
            log(f"   {t}: {c['n']} analysts, target {c['mid']}")
        except Exception as e:                      # noqa: BLE001
            log(f"   {t}: {type(e).__name__}: {e}")
            out[t] = {"cov": "unknown", "why": "Could not be fetched on the last run."}

    keep = cfg["consensus"].get("snapshot_history_days", 400)
    hist = dict(sorted(hist.items())[-keep:])
    write_json("consensus_history.json", hist)
    write_json("consensus.json", {"updated": stamp(), "calls_used": calls,
                                  "snapshots": len(hist), "instruments": out})
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
