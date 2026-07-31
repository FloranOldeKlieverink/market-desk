"""Offline tests. No network: every parser is fed a fixture that matches the
real response format, including the malformed cases that actually happen."""
import sys, os, json, io, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

FAILS = []
def ck(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name + (f"  {extra}" if extra else ""))
    if not cond: FAILS.append(name)

# ---------------- ECB exchange rates ----------------
import fetch_fx
ECB = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-05-22,1.1595,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-05-25,1.1643,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-05-26,,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-05-27,-,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-05-28,1.1617,A
"""
fx = fetch_fx.parse(ECB)
ck("ECB: skips blank and dash observations", len(fx) == 3, str(sorted(fx)))
ck("ECB: inverts to euros per unit", abs(fx["2026-05-22"] - 1/1.1595) < 1e-7, f"{fx['2026-05-22']:.8f}")
ck("ECB: a euro buys more than one dollar", 0.5 < fx["2026-05-22"] < 1.0)

# ---------------- Stooq prices ----------------
import providers
class FakeResp:
    def __init__(s, b): s.b = b
    def read(s): return s.b
    def __enter__(s): return s
    def __exit__(s, *a): return False

STOOQ = "Date,Open,High,Low,Close,Volume\n" + "".join(
    f"2024-01-{d:02d},1,1,1,{100+d}.5,1000\n" for d in range(1, 32)) + "".join(
    f"2024-02-{d:02d},1,1,1,{130+d}.5,1000\n" for d in range(1, 29))
import common
common.fetch = lambda url, **k: STOOQ.encode()
providers.fetch = common.fetch
rows = providers.Stooq.history("aapl.us")
ck("Stooq: parses every row", len(rows) == 59, str(len(rows)))
ck("Stooq: oldest first", rows[0]["date"] < rows[-1]["date"])
ck("Stooq: close parsed as float", isinstance(rows[0]["close"], float) and rows[0]["close"] == 101.5)

providers.fetch = lambda url, **k: b"<!DOCTYPE html><html>404</html>"
try:
    providers.Stooq.history("nope.us"); ck("Stooq: rejects an HTML error page", False)
except ValueError: ck("Stooq: rejects an HTML error page", True)

providers.fetch = lambda url, **k: b"Date,Close\n2024-01-01,10\n"
try:
    providers.Stooq.history("thin.us"); ck("Stooq: rejects a too-short series", False)
except ValueError: ck("Stooq: rejects a too-short series", True)

# fallback chain
calls = []
class Boom:
    name, key_setting = "boom", "stooq"
    @staticmethod
    def history(sym, key=None):
        calls.append("boom"); raise ValueError("down")
class Good:
    name, key_setting = "good", "alphavantage"
    @staticmethod
    def history(sym, key=None):
        calls.append("good")
        return [{"date": f"2024-01-{d:02d}", "close": 10.0} for d in range(1, 29)]
providers.ORDER = [Boom, Good]
rows, prov = providers.history({"ticker": "X", "stooq": "x.us", "alphavantage": "X"}, "key", 780)
ck("providers: falls through to the next one", prov == "good" and calls == ["boom", "good"], str(calls))
providers.ORDER = [Boom]
try:
    providers.history({"ticker": "X", "stooq": "x.us"}, None, 780)
    ck("providers: raises when all fail", False)
except RuntimeError as e:
    ck("providers: raises when all fail", "all providers failed" in str(e))

# ---------------- RSS and Atom ----------------
import fetch_news
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>ASML orders point to slower 2027 chip cycle</title>
<link>https://x/1</link><description>&lt;p&gt;Lithography &amp;amp; deposition   orders.&lt;/p&gt;</description>
<pubDate>Wed, 29 Jul 2026 08:00:00 GMT</pubDate></item>
<item><title>Unrelated story about shipping</title><link>https://x/2</link>
<description>Freight rates.</description><pubDate>Wed, 29 Jul 2026 07:00:00 GMT</pubDate></item>
</channel></rss>"""
items = fetch_news.parse_feed(RSS)
ck("RSS: both items parsed", len(items) == 2)
ck("RSS: html stripped and entities decoded",
   items[0]["summary"] == "Lithography & deposition orders.", repr(items[0]["summary"]))

XSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><item>
<title>Headline &amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt; tail</title>
<link>https://x/3</link><description>&amp;lt;img src=x onerror=alert(1)&amp;gt; body</description>
<pubDate>Wed, 29 Jul 2026 06:00:00 GMT</pubDate></item></channel></rss>"""
bad = fetch_news.parse_feed(XSS)[0]
ck("double-encoded markup cannot survive into the title",
   "<" not in bad["title"] and ">" not in bad["title"] and bad["title"].startswith("Headline"),
   repr(bad["title"]))
ck("double-encoded markup cannot survive into the summary",
   "<" not in bad["summary"] and ">" not in bad["summary"], repr(bad["summary"]))
ck("RSS: date converts to ISO", fetch_news.to_iso(items[0]["published"]).startswith("2026-07-29"))

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Nvidia results lift the AI complex</title>
<link href="https://y/1"/><summary>Data centre demand.</summary>
<updated>2026-07-29T09:00:00Z</updated></entry></feed>"""
a = fetch_news.parse_feed(ATOM)
ck("Atom: parsed by the same function", len(a) == 1 and a[0]["link"] == "https://y/1")

cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "settings.json")))
tick, themes = fetch_news.tag_story(items[0], cfg)
ck("tagging: finds the ticker in the headline", tick == ["ASML"], str(tick))
ck("tagging: finds the AI theme", "AI" in themes, str(themes))
tick2, themes2 = fetch_news.tag_story(items[1], cfg)
ck("tagging: leaves unrelated stories untagged", tick2 == [] and themes2 == [], str(tick2 + themes2))

# ---------------- consensus ----------------
import fetch_consensus
OV = {"Symbol": "MSFT", "Currency": "USD", "EPS": "11.80", "AnalystTargetPrice": "498.4",
      "AnalystRatingStrongBuy": "31", "AnalystRatingBuy": "17", "AnalystRatingHold": "6",
      "AnalystRatingSell": "0", "AnalystRatingStrongSell": "0"}
c = fetch_consensus.parse_overview(OV)
ck("consensus: rating counts read", c["dist"] == [31, 17, 6, 0, 0] and c["n"] == 54, str(c["dist"]))
ck("consensus: mean target read", c["mid"] == 498.4)
ck("consensus: spread marked unavailable on the free tier", c["lo"] is None and c["hi"] is None)
c2 = fetch_consensus.parse_overview({"Symbol": "X", "AnalystTargetPrice": "None", "EPS": "-"})
ck("consensus: junk numbers become None", c2["mid"] is None and c2["epsLast"] is None)

today = dt.date.today()
hist = {(today - dt.timedelta(days=95)).isoformat(): {"MSFT": {"mid": 520.0}},
        (today - dt.timedelta(days=20)).isoformat(): {"MSFT": {"mid": 505.0}},
        today.isoformat(): {"MSFT": {"mid": 498.4}}}
ck("consensus: 3-month revision computed from our own snapshots",
   abs(fetch_consensus.revisions(hist, "MSFT", "mid", 90) - (-4.15)) < 0.02,
   str(fetch_consensus.revisions(hist, "MSFT", "mid", 90)))
ck("consensus: no revision until there is history",
   fetch_consensus.revisions({today.isoformat(): {"MSFT": {"mid": 1}}}, "MSFT", "mid", 90) is None)

print("\n" + (f"{len(FAILS)} FAILURES: " + ", ".join(FAILS) if FAILS else "all parser tests passed"))
sys.exit(1 if FAILS else 0)
