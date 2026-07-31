"""Alert engine on synthetic but realistic price data. Still no network."""
import sys, os, json, math, random, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import common
FAILS=[]
def ck(n,c,e=""):
    print(("ok    " if c else "FAIL  ")+n+(f"  {e}" if e else ""))
    if not c: FAILS.append(n)

import build_alerts as BA

# --- build 400 business days of market + two stocks, one with a shock today ---
random.seed(7)
days=[]; d=dt.date(2024,1,1)
while len(days)<400:
    if d.weekday()<5: days.append(d.isoformat())
    d+=dt.timedelta(days=1)

mkt=[random.gauss(0.0003,0.008) for _ in days]
def build(beta,idio,shock=0.0):
    px=[100.0]; 
    for i in range(1,len(days)):
        r=beta*mkt[i]+random.gauss(0,idio)+(shock if i==len(days)-1 else 0)
        px.append(px[-1]*(1+r))
    return {days[i]:px[i] for i in range(len(days))}

prices={"series":{
    "BENCH": build(1.0,0.0001),
    "CALM":  build(1.0,0.010),
    "SHOCK": build(1.1,0.012,shock=-0.11),
}}
fx={"euros_per_unit":{"USD":{d:0.91 for d in days}}}
cfg=json.load(open(os.path.join(os.path.dirname(__file__),"..","settings.json")))
cfg["instruments"]=[{"ticker":"CALM","currency":"EUR"},{"ticker":"SHOCK","currency":"USD"}]
cfg["benchmark"]={"ticker":"BENCH","currency":"EUR"}

bench=BA.eur_series("BENCH",prices,fx,"EUR")
ck("benchmark series converted", len(bench)==400)
usd=BA.eur_series("SHOCK",prices,fx,"USD")
ck("USD series converted to euros", abs(usd[days[0]]-prices["series"]["SHOCK"][days[0]]*0.91)<1e-9)

# FX carry-forward when a rate is missing on a trading day
sparse={"euros_per_unit":{"USD":{days[0]:0.90, days[200]:0.95}}}
carried=BA.eur_series("SHOCK",prices,sparse,"USD")
ck("missing FX carries the last published rate forward",
   abs(carried[days[100]]-prices["series"]["SHOCK"][days[100]]*0.90)<1e-9 and
   abs(carried[days[300]]-prices["series"]["SHOCK"][days[300]]*0.95)<1e-9)

calm =BA.residual_check("CALM", cfg["instruments"][0],prices,fx,bench,cfg)
shock=BA.residual_check("SHOCK",cfg["instruments"][1],prices,fx,bench,cfg)
ck("beta recovered for the calm name", abs(calm["beta"]-1.0)<0.15, f"{calm['beta']:.2f}")
ck("beta recovered for the shocked name", abs(shock["beta"]-1.1)<0.20, f"{shock['beta']:.2f}")
ck("quiet day does not fire", not calm["fires"], f"z={calm['z']:.2f} resid={calm['resid']*100:.2f}%")
ck("11% idiosyncratic drop does fire", shock["fires"], f"z={shock['z']:.2f} resid={shock['resid']*100:.2f}%")
ck("residual is not just the raw return", abs(shock["resid"]-shock["ret"])>1e-6)

# a big move that is all market must NOT fire
allmkt=dict(prices["series"]["CALM"])
last,prev=days[-1],days[-2]
mkt_shift=0.06
prices2={"series":{**prices["series"],
    "BENCH":{**bench, last: bench[prev]*(1+mkt_shift)},
    "MOVER":{**allmkt, last: allmkt[prev]*(1+mkt_shift)}}}
b2=BA.eur_series("BENCH",prices2,fx,"EUR")
mover=BA.residual_check("MOVER",{"ticker":"MOVER","currency":"EUR"},prices2,fx,b2,cfg)
ck("a 6% fall alongside a 6% market fall does not fire", not mover["fires"],
   f"z={mover['z']:.2f} resid={mover['resid']*100:.2f}%")

# short history is skipped rather than guessed at
tiny={"series":{"T":{days[i]:100+i for i in range(30)}}}
ck("too little history returns nothing",
   BA.residual_check("T",{"ticker":"T","currency":"EUR"},tiny,fx,bench,cfg) is None)

# --- rules ---
ctx={"cfg":cfg,"residuals":[calm,shock],"consensus":{
        "AAPL":{"cov":"yes","e3":-2.4,"history_days":95,"lo":170,"hi":300,"mid":236},
        "MSFT":{"cov":"yes","e3":0.8,"history_days":95,"lo":None,"hi":None,"mid":498},
        "ADYEN":{"cov":"paid"}},
     "filings":[{"ticker":"MSFT","form":"8-K","filed":dt.date.today().isoformat(),
                 "why":"Material event.","url":"https://sec/1"},
                {"ticker":"AAPL","form":"4","filed":dt.date.today().isoformat(),"why":"Insider."},
                {"ticker":"MSFT","form":"8-K","filed":"2020-01-01","why":"Old."}]}
a=[]
for r in BA.RULES: a+=r(ctx)
kinds=[x["kind"] for x in a]
ck("shocked name produces a thesis-check alert", kinds.count("Thesis check")==1, str(kinds))
ck("estimate cut produces an alert", "Estimates" in kinds)
ck("uncut estimate does not", sum(1 for x in a if x["kind"]=="Estimates")==1)
ck("wide target spread flagged when data exists", "Disagreement" in kinds)
ck("paid-tier name is skipped, not crashed on", all("ADYEN" not in x["title"] for x in a))
ck("recent 8-K produces a filing alert", sum(1 for x in a if x["kind"]=="Filing")==1, str(kinds))
ck("old filing ignored", all("2020" not in x["when"] for x in a))
ck("only meaningful alerts are email-worthy",
   sorted({x["kind"] for x in a if x["mail"]})==["Estimates","Filing","Thesis check"],
   str(sorted({x['kind'] for x in a if x['mail']})))
ck("disagreement is shown but never emailed",
   all(not x["mail"] for x in a if x["kind"]=="Disagreement"))
ck("every alert has a unique id", len({x["id"] for x in a})==len(a))
ck("no alert text contains None or nan", not any("None" in x["title"]+x["body"] or "nan" in x["title"] for x in a))

# email must be skipped, never attempted, when unconfigured
cfg2=dict(cfg); cfg2["alerts"]=dict(cfg["alerts"]); cfg2["alerts"]["email_to"]="PUT_YOUR_EMAIL_HERE"
ck("unconfigured email is skipped rather than attempted", BA.send_email(a,cfg2) is False)
os.environ.pop("SMTP_USER",None); os.environ.pop("SMTP_PASS",None)
cfg2["alerts"]["email_to"]="someone@example.com"
ck("missing SMTP secrets skip the send", BA.send_email(a,cfg2) is False)

print("\n"+(f"{len(FAILS)} FAILURES: "+", ".join(FAILS) if FAILS else "all alert tests passed"))
sys.exit(1 if FAILS else 0)
