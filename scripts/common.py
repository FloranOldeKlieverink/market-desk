"""Shared helpers. Standard library only, so the workflow needs no install step."""
import json, os, sys, time, urllib.request, urllib.error, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

UA = "floran-investor-dashboard/1.0 (personal, non-commercial)"


def settings():
    with open(ROOT / "settings.json", encoding="utf-8") as f:
        return json.load(f)


def instruments(cfg):
    return [i for i in cfg["instruments"]]


def log(msg):
    """Never pass a secret to this. Workflow logs are public on a public repo."""
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(url, headers=None, tries=3, timeout=30):
    """GET with retries. Returns bytes, or raises the last error."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001 - any failure is retryable
            last = e
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


def read_json(name, default=None):
    p = DATA / name
    if not p.exists():
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(name, payload):
    """Atomic write, so a crash mid-write cannot leave the dashboard with half a file."""
    p = DATA / name
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)
    log(f"wrote data/{name} ({p.stat().st_size:,} bytes)")


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def status(name, ok, detail=""):
    """Every fetcher records whether it worked, so the dashboard can say so honestly."""
    s = read_json("status.json", {}) or {}
    s[name] = {"ok": bool(ok), "detail": detail[:300], "at": stamp()}
    write_json("status.json", s)


def guard(name):
    """Decorator: a failing source must never take the whole run down with it."""
    def outer(fn):
        def inner(*a, **k):
            try:
                out = fn(*a, **k)
                status(name, True, "")
                return out
            except Exception as e:                  # noqa: BLE001
                log(f"!! {name} failed: {type(e).__name__}: {e}")
                status(name, False, f"{type(e).__name__}: {e}")
                return None
        return inner
    return outer


def load_prices(cfg, tickers=None):
    """Reassemble the per-ticker price files into the one shape the rest of the
    code expects. Pass a ticker list to load only what is needed."""
    idx = read_json("index.json", {}) or {}
    inst = idx.get("instruments", {})
    want = [t for t in inst if tickers is None or t in tickers]
    series = {}
    for t in want:
        blob = read_json(inst[t]["file"], {}) or {}
        if blob.get("series"):
            series[t] = blob["series"]
    return {"updated": idx.get("updated"), "series": series,
            "meta": inst, "failed": idx.get("failed", [])}


def secret(key, required=False):
    v = os.environ.get(key, "").strip()
    if required and not v:
        raise RuntimeError(f"missing repository secret {key}")
    return v
