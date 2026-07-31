"""Alert engine and the daily email.

Important consequence of the privacy split: this runs on GitHub and therefore
cannot see your holdings, which live only in your browser. So the email covers
what can be judged per instrument — unusual moves, estimate revisions, filings.
Portfolio-shape alerts (concentration, correlation, AI exposure) need position
sizes, so they stay in the browser and are never emailed. That is a real limit,
not an oversight: the alternative is putting your book in a public repo.
"""
import math, os, smtplib, sys, datetime as dt
from email.message import EmailMessage
from common import settings, read_json, write_json, log, guard, stamp, secret, load_prices

# ---------- small statistics, no numpy so the workflow needs no install ----------
def mean(a):
    return sum(a) / len(a) if a else 0.0


def cov(a, b):
    if len(a) < 2:
        return 0.0
    ma, mb = mean(a), mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)


def sd(a):
    return math.sqrt(max(cov(a, a), 0.0))


def eur_series(ticker, prices, fx, currency):
    """Convert a native-currency close series into euros, date by date."""
    ser = prices.get("series", {}).get(ticker) or {}
    rates = fx.get("euros_per_unit", {}).get(currency) or {}
    out = {}
    for d, px in ser.items():
        r = 1.0 if currency == "EUR" else rates.get(d)
        if r is None and currency != "EUR":
            earlier = [k for k in rates if k <= d]
            if not earlier:
                continue
            r = rates[max(earlier)]                 # carry the last published rate forward
        out[d] = px * r
    return out


def returns(series, dates):
    out = []
    for i in range(1, len(dates)):
        p0, p1 = series.get(dates[i - 1]), series.get(dates[i])
        if p0 and p1 and p0 > 0:
            out.append(p1 / p0 - 1.0)
        else:
            out.append(0.0)
    return out


def residual_check(ticker, inst, prices, fx, bench_eur, cfg):
    ser = eur_series(ticker, prices, fx, inst["currency"])
    dates = sorted(set(ser) & set(bench_eur))
    if len(dates) < 120:
        return None
    r = returns(ser, dates)
    m = returns(bench_eur, dates)
    vm = cov(m, m)
    beta = cov(r, m) / vm if vm else 0.0
    resid = [x - beta * y for x, y in zip(r, m)]
    sigma = sd(resid)
    last, last_r, last_m = resid[-1], r[-1], m[-1]
    z = last / sigma if sigma else 0.0
    a = cfg["alerts"]
    fires = abs(z) >= a["residual_sigma"] and abs(last) >= a["residual_floor"]
    return {"ticker": ticker, "beta": beta, "sigma": sigma, "resid": last,
            "z": z, "ret": last_r, "mkt": last_m, "fires": fires, "date": dates[-1]}


# ---------- rules ----------
def rule_residual(ctx):
    out = []
    for r in sorted(ctx["residuals"], key=lambda x: -abs(x["z"])):
        if not r["fires"]:
            continue
        out.append({
            "id": f"resid:{r['ticker']}", "kind": "Thesis check", "mail": True,
            "tone": "pos" if r["resid"] > 0 else "neg", "when": r["date"],
            "title": f"{r['ticker']}: {r['resid']*100:+.1f}% beyond what the market explains",
            "body": (f"Moved {r['ret']*100:+.1f}% while the market moved {r['mkt']*100:+.1f}%. "
                     f"Stripping out the {r['beta']:.2f} beta leaves a residual of {r['resid']*100:+.1f}%, "
                     f"or {abs(r['z']):.1f}σ against its own history."),
        })
    return out


def rule_revisions(ctx):
    out = []
    warn = ctx["cfg"]["alerts"]["estimate_cut_warn"]
    for t, c in ctx["consensus"].items():
        if c.get("cov") != "yes":
            continue
        e3 = c.get("e3")
        if e3 is None or e3 > warn:
            continue
        out.append({
            "id": f"rev:{t}", "kind": "Estimates", "mail": True, "tone": "act", "when": "90-day",
            "title": f"{t}: consensus target cut {abs(e3):.1f}% over three months",
            "body": ("Estimate revisions move slowly and often lead the price rather than follow it. "
                     f"Built from {c.get('history_days', 0)} days of snapshots taken by this dashboard."),
        })
    return out


def rule_spread(ctx):
    out = []
    warn = ctx["cfg"]["alerts"]["target_spread_warn"]
    for t, c in ctx["consensus"].items():
        if c.get("cov") != "yes" or not c.get("lo") or not c.get("hi") or not c.get("mid"):
            continue
        spread = (c["hi"] - c["lo"]) / c["mid"]
        if spread < warn:
            continue
        out.append({"id": f"disp:{t}", "kind": "Disagreement", "mail": False, "tone": "", "when": "Current",
                    "title": f"Analysts disagree sharply on {t} — targets span {spread*100:.0f}% of the mean",
                    "body": "A wide spread means the mean is an average of incompatible views, not a shared one."})
    return out


FORM_LABEL = {"4": "a Form 4", "8-K": "an 8-K", "SC 13D": "a 13D", "SC 13G": "a 13G"}


def rule_filings(ctx):
    out = []
    cutoff = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    for f in ctx["filings"]:
        if f["filed"] < cutoff or f["form"] not in ("8-K", "SC 13D"):
            continue
        out.append({"id": f"filing:{f['ticker']}:{f['filed']}:{f['form']}", "kind": "Filing",
                    "mail": f["form"] == "8-K", "tone": "act", "when": f["filed"],
                    "title": f"{f['ticker']} filed {FORM_LABEL.get(f['form'], 'a ' + f['form'])}",
                    "body": f["why"], "url": f.get("url", "")})
    return out


RULES = [rule_residual, rule_revisions, rule_spread, rule_filings]


# ---------- email ----------
def send_email(alerts, cfg):
    to = cfg["alerts"].get("email_to", "")
    if not cfg["alerts"].get("email_enabled") or not to or "PUT_YOUR" in to:
        log("   email not configured, skipping send")
        return False
    user, pw = secret("SMTP_USER"), secret("SMTP_PASS")
    if not user or not pw:
        log("   SMTP secrets absent, skipping send")
        return False

    lines = [f"{len(alerts)} thing{'s' if len(alerts) != 1 else ''} worth a look.", ""]
    for a in alerts:
        lines += [f"{a['kind'].upper()} — {a['title']}", f"  {a['body']}"]
        if a.get("url"):
            lines.append(f"  {a['url']}")
        lines.append("")
    lines += ["—",
              "Sent because each of these passed the thresholds in settings.json.",
              "Position sizes are not visible to this job, so concentration and",
              "correlation warnings appear in the dashboard only, never by email."]

    msg = EmailMessage()
    msg["Subject"] = f"Portfolio: {alerts[0]['title'][:60]}" if len(alerts) == 1 \
        else f"Portfolio: {len(alerts)} alerts"
    msg["From"], msg["To"] = user, to
    msg.set_content("\n".join(lines))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(user, pw)                           # never logged
        s.send_message(msg)
    log(f"   email sent, {len(alerts)} alert(s)")   # no address printed
    return True


@guard("alerts")
def main():
    cfg = settings()
    need = {i["ticker"].upper() for i in cfg["instruments"]} | {cfg["benchmark"]["ticker"].upper()}
    prices = load_prices(cfg, need)
    fx = read_json("fx.json", {}) or {}
    consensus = (read_json("consensus.json", {}) or {}).get("instruments", {})
    filings = (read_json("filings.json", {}) or {}).get("filings", [])
    if not prices.get("series"):
        raise RuntimeError("no price data available yet")

    b = cfg["benchmark"]
    bench_eur = eur_series(b["ticker"], prices, fx, b.get("currency", "EUR"))
    residuals = []
    for i in cfg["instruments"]:
        r = residual_check(i["ticker"], i, prices, fx, bench_eur, cfg)
        if r:
            residuals.append(r)
            log(f"   {i['ticker']}: beta {r['beta']:.2f}, residual {r['resid']*100:+.2f}%, {abs(r['z']):.1f}σ"
                + ("  <- fires" if r["fires"] else ""))

    ctx = {"cfg": cfg, "residuals": residuals, "consensus": consensus, "filings": filings}
    alerts = []
    for rule in RULES:
        try:
            alerts += rule(ctx) or []
        except Exception as e:                      # noqa: BLE001
            log(f"   rule {rule.__name__} failed: {type(e).__name__}: {e}")

    sent = False
    mailable = [a for a in alerts if a.get("mail")]
    if mailable and os.environ.get("SEND_EMAIL") == "1":
        sent = send_email(mailable, cfg)

    write_json("alerts.json", {"updated": stamp(), "alerts": alerts,
                               "emailed": sent, "residuals": residuals})
    log(f"   {len(alerts)} alert(s), {len(mailable)} of them email-worthy")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
