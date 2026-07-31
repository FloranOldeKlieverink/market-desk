"""Run this first, and any time something looks stale.

Feed URLs move and free tiers change. This checks every configured source and
prints a plain-English report saying which ones answered and which need a new
URL. It changes nothing and writes nothing except the report.
"""
import sys, json, time
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

    print("\nPrices — trying each provider separately so we can see who answers")
    import providers
    av = secret("ALPHAVANTAGE_KEY")
    uni = providers.parse_universe(cfg)
    targets = []
    for i in cfg["instruments"]:
        merged = dict(uni.get(i["ticker"].upper(), {}))
        merged.update({k: v for k, v in i.items() if v is not None})
        targets.append(merged)
    targets.append(cfg["benchmark"])

    scoreboard = {}
    for inst in targets:
        line = []
        for P in providers.ORDER:
            if P.name == "alphavantage":
                continue                            # only 25 calls a day; not spent on a health check
            sym = providers.symbol_for(inst, P)
            if not sym:
                line.append(f"{P.name}: no symbol")
                continue
            try:
                providers.Yahoo.last_meta = {}
                rows = P.history(sym)
                cur = (providers.Yahoo.last_meta or {}).get("currency") if P is providers.Yahoo else None
                warn = ""
                if cur and cur != inst.get("currency"):
                    warn = f"  <-- settings say {inst.get('currency')}, exchange says {cur}"
                line.append(f"{P.name}({sym}): {len(rows)} days to {rows[-1]['date']}"
                            + (f" in {cur}" if cur else "") + warn)
                scoreboard[P.name] = scoreboard.get(P.name, 0) + 1
            except Exception as e:                  # noqa: BLE001
                line.append(f"{P.name}({sym}): {str(e)[:70]}")
        good = any(": " in x and "days to" in x for x in line)
        print(("  WORKS   " if good else "  BROKEN  ") + f"{inst['ticker']:<8} — " + "  |  ".join(line))
        results.append(good)

    print("\n  Provider scoreboard: " + (", ".join(f"{k} answered {v} of {len(targets)}"
          for k, v in scoreboard.items()) or "nobody answered"))

    print(f"\nUniverse — {len(uni)} preloaded instruments, spot-checking five")
    import random as _r
    for inst in _r.Random(0).sample(sorted(uni.values(), key=lambda x: x["ticker"]),
                                    min(5, len(uni))):
        def f(i=inst):
            rows, prov, meta = providers.history(i, None, 400,
                                                 order=[p for p in providers.ORDER
                                                        if p.name != "alphavantage"])
            return f"{len(rows)} days via {prov}, {meta.get('currency') or '?'}, last {rows[-1]['date']}"
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
        _stop = []
        for inst in [i for i in cfg["instruments"] if i.get("alphavantage")]:
            if _stop:
                print("  SKIPPED " + f"{inst['ticker']:<8} — daily Alpha Vantage allowance used up")
                continue
            def f(i=inst):
                time.sleep(15)          # spread them out, as Alpha Vantage asks
                p = json.loads(fetch(fetch_consensus.URL.format(sym=i["alphavantage"], key=av))
                               .decode("utf-8", "replace"))
                m = p.get("Note") or p.get("Information") or p.get("Error Message")
                if m:
                    _stop.append(True)
                    raise ValueError(f"{str(m)[:120]}")
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
