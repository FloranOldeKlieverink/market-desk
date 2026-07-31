"""SEC EDGAR. Free, no key. A contact address in the header is required by the SEC,
not optional. Dutch names that do not file with the SEC are skipped, and the AFM
register is left as a known gap rather than pretended to be covered."""
import json, sys
from common import fetch, settings, write_json, log, guard, stamp

SUB = "https://data.sec.gov/submissions/CIK{cik}.json"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"

MEANING = {
    "4": "Insider transaction. Buying is a stronger signal than selling.",
    "8-K": "Material event. This is the form that changes a thesis.",
    "SC 13D": "5%+ stake with activist intent.",
    "SC 13G": "5%+ passive stake.",
}


@guard("filings")
def main():
    cfg = settings()
    fset = cfg.get("filings", {})
    if not fset.get("enabled", True):
        return True
    email = fset.get("user_agent_email", "")
    if not email or "PUT_YOUR" in email:
        raise RuntimeError("settings.json filings.user_agent_email must be a real address — the SEC requires it")
    headers = {"User-Agent": f"floran-investor-dashboard/1.0 ({email})"}
    wanted = set(fset.get("forms", ["4", "8-K"]))
    out, skipped = [], []

    for i in cfg["instruments"]:
        cik = i.get("sec_cik")
        if not cik:
            skipped.append(i["ticker"])
            continue
        try:
            p = json.loads(fetch(SUB.format(cik=cik.zfill(10)), headers=headers).decode("utf-8", "replace"))
            r = p.get("filings", {}).get("recent", {})
            forms = r.get("form", [])
            for idx in range(min(len(forms), 60)):
                form = forms[idx]
                if form not in wanted:
                    continue
                acc = r["accessionNumber"][idx]
                out.append({
                    "ticker": i["ticker"], "form": form,
                    "filed": r["filingDate"][idx],
                    "why": MEANING.get(form, ""),
                    "url": DOC.format(cik_int=int(cik), acc_nodash=acc.replace("-", ""),
                                      doc=r.get("primaryDocument", [""] * (idx + 1))[idx]),
                })
            log(f"   {i['ticker']}: scanned {len(forms)} recent filings")
        except Exception as e:                      # noqa: BLE001
            log(f"   {i['ticker']}: {type(e).__name__}: {e}")

    out.sort(key=lambda x: x["filed"], reverse=True)
    write_json("filings.json", {
        "updated": stamp(), "filings": out[:80], "no_sec_coverage": skipped,
        "gap": "Euronext-listed names do not file with the SEC. The AFM register "
               "(3% threshold, reportable without delay) is not yet wired in.",
    })
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
