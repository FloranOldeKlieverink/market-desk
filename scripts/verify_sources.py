"""Run this first, and any time something looks stale.

Feed URLs move and free tiers change. This checks every configured source and
prints a plain-English report saying which ones answered and which need a new
URL. It changes nothing and writes nothing except the report.
"""
import sys, json
from common import fetch, settings, log, secret

OK, BAD = "  WORKS   ", "  BROKEN  "


def check(label, fn):
    try:
        detail = fn()
        print(OK + label + (f" — {detail}" if detail else ""))
        return True
    except Exception as e:                          # noqa: BLE001
        print(BAD + label + f" — {type(e).__name__}: {str(e)[:110]}")
        return False


def main():
    cfg = settings()
    results = []
    print("\nChecking every source in settings.json\n" + "=" * 62)

    print("\nExchange rates")
    for ccy in sorted({i["currency"] for i in cfg["instruments"]} - {"EUR"}):
        def f(c=ccy):
            import fetch_fx
            raw = fetch(fetch_fx.BASE.format(ccy=c)).decode("utf-8", "replace")
            s = fetch_fx.parse(raw)
            return f"{len(s)} days, latest {max(s)}"
        results.append(check(f"ECB {ccy}/EUR", f))

    print("\nPrices — the tracked instruments")
    import providers
    av = secret("ALPHAVANTAGE_KEY")
    uni = providers.parse_universe(cfg)
    targets = []
    for i in cfg["instruments"]:
        merged = dict(uni.get(i["ticker"].upper(), {}))
        merged.update({k: v for k, v in i.items() if v is not None})
        targets.append(merged)
    targets.append(cfg["benchmark"])
    for inst in targets:
        def f(i=inst):
            rows, prov = providers.history(i, av, cfg.get("history_days", 780))
            return f"{len(rows)} days via {prov}, last {rows[-1]['date']}"
        results.append(check(f"{inst['ticker']:<8}", f))

    print(f"\nUniverse — {len(uni)} preloaded instruments, spot-checking five")
    import random as _r
    for inst in _r.Random(0).sample(sorted(uni.values(), key=lambda x: x["ticker"]),
                                    min(5, len(uni))):
        def f(i=inst):
            rows, prov = providers.history(i, None, 400)
            return f"{len(rows)} days, last {rows[-1]['date']}"
        results.append(check(f"{inst['ticker']:<8}", f))

    print("\nNews feeds")
    import fetch_news
    for src in cfg["news_sources"]:
        if not src.get("enabled", True):
            print("  SKIPPED " + src["label"] + " — disabled in settings")
            continue
        url = src["url"]
        if src.get("per_ticker"):
            sym = next((i.get(src.get("symbol_field", "alphavantage"))
                        for i in cfg["instruments"] if i.get(src.get("symbol_field", "alphavantage"))), "AAPL")
            url = url.replace("{SYMBOL}", sym)
        def f(u=url):
            items = fetch_news.parse_feed(fetch(u))
            if not items:
                raise ValueError("feed parsed but contained no items")
            return f"{len(items)} items, newest: {items[0]['title'][:48]}"
        results.append(check(f"{src['label']:<8}", f))

    print("\nSEC filings")
    fs = cfg.get("filings", {})
    if not fs.get("enabled", True):
        print("  SKIPPED EDGAR — disabled in settings")
    elif "PUT_YOUR" in fs.get("user_agent_email", ""):
        print(BAD + "EDGAR — set filings.user_agent_email in settings.json to a real address")
        results.append(False)
    else:
        import fetch_filings
        for inst in [i for i in cfg["instruments"] if i.get("sec_cik")]:
            def f(i=inst):
                h = {"User-Agent": f"floran-investor-dashboard/1.0 ({fs['user_agent_email']})"}
                p = json.loads(fetch(fetch_filings.SUB.format(cik=i["sec_cik"].zfill(10)),
                                     headers=h).decode("utf-8", "replace"))
                return f"{len(p.get('filings', {}).get('recent', {}).get('form', []))} recent filings"
            results.append(check(f"EDGAR {inst['ticker']:<6}", f))

    print("\nAnalyst consensus")
    if not av:
        print("  SKIPPED Alpha Vantage — no ALPHAVANTAGE_KEY secret set yet")
    else:
        import fetch_consensus
        for inst in [i for i in cfg["instruments"] if i.get("alphavantage")]:
            def f(i=inst):
                p = json.loads(fetch(fetch_consensus.URL.format(sym=i["alphavantage"], key=av))
                               .decode("utf-8", "replace"))
                if "Note" in p or "Information" in p:
                    raise ValueError("daily request limit reached — try again tomorrow")
                if not p.get("Symbol"):
                    raise ValueError("no free-tier coverage for this listing")
                c = fetch_consensus.parse_overview(p)
                return f"{c['n']} analysts, target {c['mid']}"
            results.append(check(f"{inst['ticker']:<8}", f))

    bad = results.count(False)
    print("\n" + "=" * 62)
    if bad:
        print(f"{bad} source(s) need attention. Anything marked BROKEN above needs a new\n"
              "URL or symbol in settings.json. Everything else keeps working meanwhile.")
    else:
        print("Every configured source answered. Nothing to do.")
    return 0                                        # never fail the run; this is a report


if __name__ == "__main__":
    sys.exit(main())
