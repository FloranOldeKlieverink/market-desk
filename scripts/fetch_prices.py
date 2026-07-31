"""Daily closes for everything in the universe, one small file per ticker.

Split per ticker so the browser downloads only the handful you actually hold,
while the repo can carry hundreds of instruments. Stooq publishes end-of-day
data, so there is nothing to gain from fetching more than once a day."""
import sys, json
import providers
from common import settings, write_json, read_json, log, guard, stamp, secret, DATA


def targets(cfg, cache):
    """Universe, then the detailed instruments on top, then benchmark and AI basket."""
    out = providers.parse_universe(cfg)
    for i in cfg.get("instruments", []):
        t = i["ticker"].upper()
        merged = dict(out.get(t, {}))
        merged.update({k: v for k, v in i.items() if v is not None})
        merged["ticker"] = t
        if not merged.get("stooq"):
            r = providers.resolve(t, cfg, cache)
            if r:
                merged["stooq"] = r["stooq"]
                merged.setdefault("currency", r["currency"])
        out[t] = merged
    for t in cfg.get("watchlist", []):
        t = t.upper()
        if t in out:
            continue
        r = providers.resolve(t, cfg, cache)
        if r:
            out[t] = {"ticker": t, "name": t, "currency": r["currency"],
                      "stooq": r["stooq"], "ai_revenue_share": None, "include_in_optimiser": True}
    b = cfg.get("benchmark")
    if b:
        out[b["ticker"].upper()] = {**b, "ticker": b["ticker"].upper()}
    for sym in cfg.get("ai_factor", {}).get("basket", []):
        out["_AI:" + sym] = {"ticker": "_AI:" + sym, "name": "AI basket member",
                             "currency": "USD" if sym.endswith(".us") else "EUR",
                             "stooq": sym, "include_in_optimiser": False}
    return out


@guard("prices")
def main():
    cfg = settings()
    days = cfg.get("history_days", 780)
    av = secret("ALPHAVANTAGE_KEY")
    cache = read_json("symbols.json", {}) or {}
    px_dir = DATA / "px"
    px_dir.mkdir(exist_ok=True)

    tgts = targets(cfg, cache)
    index, failed, fresh, unchanged = {}, [], 0, 0

    for t, inst in sorted(tgts.items()):
        fname = "px/" + t.replace("/", "_").replace(":", "_") + ".json"
        try:
            rows, provider = providers.history(inst, av, days)
            series = {r["date"]: r["close"] for r in rows}
            prev = read_json(fname, {}) or {}
            if prev.get("series") == series:
                unchanged += 1
            else:
                write_json(fname, {"ticker": t, "name": inst.get("name", t),
                                   "ccy": inst.get("currency", "EUR"),
                                   "provider": provider, "series": series})
                fresh += 1
            index[t] = {"name": inst.get("name", t), "ccy": inst.get("currency", "EUR"),
                        "file": fname, "last": rows[-1]["date"], "points": len(rows),
                        "ai": inst.get("ai_revenue_share"),
                        "opt": inst.get("include_in_optimiser", True)}
        except Exception as e:                      # noqa: BLE001
            prev = read_json(fname, {}) or {}
            if prev.get("series"):
                index[t] = {"name": inst.get("name", t), "ccy": prev.get("ccy", "EUR"),
                            "file": fname, "last": max(prev["series"]),
                            "points": len(prev["series"]), "stale": True,
                            "ai": inst.get("ai_revenue_share"),
                            "opt": inst.get("include_in_optimiser", True)}
            failed.append(t)
            log(f"!! {t}: {str(e)[:100]}")

    if not index:
        raise RuntimeError("no price series could be fetched at all")
    write_json("symbols.json", cache)
    write_json("index.json", {"updated": stamp(), "instruments": index,
                              "failed": failed, "written": fresh, "unchanged": unchanged,
                              "benchmark": (cfg.get("benchmark") or {}).get("ticker"),
                              "ai_basket": ["_AI:" + s for s in cfg.get("ai_factor", {}).get("basket", [])]})
    log(f"   {len(index)} instruments · {fresh} updated · {unchanged} unchanged · {len(failed)} failed")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
