"""ECB daily euro reference rates. Free, no account, no key, back to 1999.
ECB publishes units of foreign currency per euro; the dashboard wants the
inverse, euros per unit, so the conversion happens here once and not again."""
import csv, io, sys
from common import fetch, write_json, settings, log, guard, stamp

BASE = "https://data-api.ecb.europa.eu/service/data/EXR/D.{ccy}.EUR.SP00.A?format=csvdata"


def parse(raw):
    rows = list(csv.DictReader(io.StringIO(raw)))
    out = {}
    for r in rows:
        d, v = r.get("TIME_PERIOD"), r.get("OBS_VALUE")
        if not d or not v:
            continue
        try:
            per_eur = float(v)
        except ValueError:
            continue
        if per_eur > 0:
            out[d] = round(1.0 / per_eur, 8)     # euros per one unit
    return out


@guard("fx")
def main():
    cfg = settings()
    wanted = sorted({i["currency"] for i in cfg["instruments"]} - {"EUR"})
    days = cfg.get("history_days", 780)
    series = {"EUR": {}}
    for ccy in wanted:
        raw = fetch(BASE.format(ccy=ccy)).decode("utf-8", "replace")
        s = parse(raw)
        if len(s) < 30:
            raise ValueError(f"ECB returned only {len(s)} observations for {ccy}")
        keep = dict(sorted(s.items())[-days:])
        series[ccy] = keep
        log(f"   {ccy}: {len(keep)} days, latest {max(keep)} = {keep[max(keep)]:.4f} EUR")
    write_json("fx.json", {"updated": stamp(), "euros_per_unit": series})
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
