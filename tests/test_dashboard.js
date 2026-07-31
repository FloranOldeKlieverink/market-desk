/* Boots the real dashboard against the fixture data with a stub DOM.
   Catches the failure mode that matters most: a data shape the page cannot render. */
const fs=require('fs'),path=require('path');
const ROOT=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(ROOT,'index.html'),'utf8');
const js=html.match(/<script>([\s\S]*)<\/script>/)[1];

let FAILS=[];
const ck=(n,c,e='')=>{console.log((c?'ok    ':'FAIL  ')+n+(e?'  '+e:''));if(!c)FAILS.push(n)};

function mkEl(id){
  const e={id,_html:'',textContent:'',className:'',hidden:false,value:'',min:'',max:'',
    style:{},dataset:{},children:[],
    setAttribute(k,v){e['_'+k]=v},getAttribute(k){return e['_'+k]||''},
    addEventListener(){},insertAdjacentHTML(pos,h){e._html+=h},
    querySelectorAll(){return []},appendChild(){},click(){}};
  Object.defineProperty(e,'innerHTML',{get(){return e._html},set(v){e._html=v}});
  return e;
}
const cache={};
global.document={getElementById:id=>cache[id]||(cache[id]=mkEl(id)),
  querySelectorAll:()=>[],createElement:()=>mkEl('tmp')};
global.alert=m=>{console.log('   [alert] '+m)};
global.confirm=()=>true;
const store={};
global.localStorage={getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=v},removeItem:k=>{delete store[k]}};
global.URL={createObjectURL:()=>'blob:',revokeObjectURL(){}};
global.Blob=class{constructor(){}};

function stubFetch(){
  global.fetch=async(u)=>{
    const f=u.split('?')[0];
    const rel=f.startsWith('data/')?f.slice(5):f;
    const fixture=path.join(ROOT,'tests','fixtures',rel);
    const p=fs.existsSync(fixture)?fixture:path.join(ROOT,f);
    if(!fs.existsSync(p))return {ok:false,status:404,json:async()=>({})};
    return {ok:true,status:200,json:async()=>JSON.parse(fs.readFileSync(p,'utf8'))};
  };
}

async function run(label,seedTxns){
  Object.keys(cache).forEach(k=>delete cache[k]);
  Object.keys(store).forEach(k=>delete store[k]);
  if(seedTxns)store['floran.transactions.v1']=JSON.stringify(seedTxns);
  stubFetch();
  const mod={exports:{}};
  new Function('module','exports','require',js+
    ';module.exports={ledger,rowsOf,buildAlerts,aiLoading,seriesOf,fxOn,corr,get mkt(){return mkt},get aiX(){return aiX},get NDAYS(){return NDAYS},get ISO(){return ISO},get FX(){return FX},get CONSENSUS(){return CONSENSUS},get STORIES(){return STORIES},get txns(){return txns},loadedSeries:()=>PRICES.series,catalogue:()=>CATALOG};')(mod,mod.exports,require);
  await new Promise(r=>setTimeout(r,120));           // let boot() settle
  console.log('\n--- '+label+' ---');
  return mod.exports;
}

(async()=>{
  // ---------- 1. a repo that has run, but no trades recorded ----------
  await run('empty portfolio, real data');
  ck('status bar reports freshness', /Updated/.test(cache.statusbar.innerHTML), cache.statusbar.innerHTML.slice(0,90));
  ck('status bar names the failing source', /filings/.test(cache.statusbar.innerHTML));
  ck('history length reported', /\d+ days of history/.test(cache.statusbar.innerHTML));
  ck('holdings show an empty state', /No open positions/.test(cache.holdings.innerHTML));
  ck('news rendered from the feed', /slower 2027 build cycle/.test(cache.stories.innerHTML));
  ck('headlines link out', /href="https:\/\/ft\.com\/1"/.test(cache.stories.innerHTML));
  ck('AI theme chip present', /data-f="th:AI"/.test(cache.chips.innerHTML));
  ck('server-side alert shown even with no holdings', /beyond what the market explains/.test(cache.alerts.innerHTML));
  ck('cycle strip explains itself rather than faking numbers',
     /No cycle indicators wired up yet/.test(cache.cycle.innerHTML));
  ck('currency dropdown built from fetched FX', /EUR/.test(cache['f-ccy'].innerHTML)&&/USD/.test(cache['f-ccy'].innerHTML));
  ck('catalogue offered in the ticker suggestions', /SHEL|NESN/.test(cache.tickers.innerHTML));
  ck('status bar reports how many instruments are available', /instruments available/.test(cache.statusbar.innerHTML));
  ck('date field bounded by real history', cache['f-date'].min.length===10&&cache['f-date'].max.length===10,
     cache['f-date'].min+' .. '+cache['f-date'].max);

  // ---------- 2. with a transaction log ----------
  const A=await run('portfolio with trades',[
    {id:1,t:"IWDA",side:"buy", date:"2024-06-03",qty:120,price:78.40,ccy:"EUR",fx:1},
    {id:2,t:"ASML",side:"buy", date:"2024-09-10",qty:6,  price:598.00,ccy:"EUR",fx:1},
    {id:3,t:"MSFT",side:"buy", date:"2025-01-15",qty:14, price:301.50,ccy:"USD",fx:0.9163},
    {id:4,t:"AAPL",side:"buy", date:"2025-04-01",qty:30, price:189.20,ccy:"USD",fx:0.9040},
    {id:5,t:"AAPL",side:"sell",date:"2026-05-20",qty:12, price:224.60,ccy:"USD",fx:0.9100},
    {id:6,t:"BTC", side:"buy", date:"2025-02-10",qty:0.32,price:45000,ccy:"USD",fx:0.9155},
    {id:7,t:"ADYEN",side:"buy",date:"2025-06-02",qty:3,price:1620.00,ccy:"EUR",fx:1},
    {id:8,t:"SHEL", side:"buy",date:"2025-07-14",qty:200,price:27.40,ccy:"GBP",fx:1.184},
    {id:9,t:"ZZZZ", side:"buy",date:"2025-08-01",qty:5,price:100.00,ccy:"EUR",fx:1}
  ]);
  const rows=A.ledger().rows;
  ck('ledger derived from the stored log', rows.length===8, rows.map(r=>r.t).join(','));
  ck('sold-down position keeps the right quantity', rows.find(r=>r.t==='AAPL').qty===18);
  ck('realised P&L recorded', Math.abs(A.ledger().realisedAll)>1);
  ck('every holding with a feed is valued', rows.filter(r=>r.hasFeed).every(r=>r.val>0),
     rows.map(r=>r.t+'='+Math.round(r.val)).join(' '));
  ck('a ticker with no feed is flagged, not valued at zero silently',
     rows.find(r=>r.t==='ZZZZ')&&rows.find(r=>r.t==='ZZZZ').hasFeed===false
     &&/no feed/.test(cache.holdings.innerHTML));
  ck('a GBP holding converts through its own rate', rows.find(r=>r.t==='SHEL').val>0,
     'SHEL='+Math.round(rows.find(r=>r.t==='SHEL').val));
  ck('a catalogue name that is not held is never downloaded',
     !('NESN' in A.loadedSeries()) && ('NESN' in A.catalogue()),
     'downloaded: '+Object.keys(A.loadedSeries()).join(','));
  ck('everything downloaded is either held, the benchmark or the AI basket',
     Object.keys(A.loadedSeries()).every(t=>
       A.txns.some(x=>x.t.toUpperCase()===t)||t==='WORLD'||t.startsWith('_AI:')));
  ck('catalogue is larger than what was downloaded',
     Object.keys(A.catalogue()).length>Object.keys(A.loadedSeries()).length,
     Object.keys(A.catalogue()).length+' vs '+Object.keys(A.loadedSeries()).length);
  ck('portfolio total rendered', /Total/.test(cache.holdings.innerHTML));
  ck('currency attribution line present', /is currency movement rather than company performance/.test(cache.holdings.innerHTML));
  ck('allocation legend built', /IWDA/.test(cache.alloclegend.innerHTML));

  ck('consensus card renders for a covered name', /ASML/.test(cache.consensus.innerHTML));
  ck('free-tier spread shown as unavailable rather than blank',
     /Target range<\/span><b style="color:var\(--dimmer\)">paid tier/.test(cache.consensus.innerHTML));
  ck('paid-tier name explains itself', /No free-tier coverage/.test(cache.consensus.innerHTML));
  ck('not-applicable name explains itself', /No sell-side coverage applies/.test(cache.consensus.innerHTML));
  ck('revision row uses our own snapshot history', /Target revision 3m/.test(cache.consensus.innerHTML));

  const ret=cache.retmetrics.innerHTML, risk=cache.riskmetrics.innerHTML;
  ck('return metrics computed', /Money-weighted/.test(ret)&&!/NaN/.test(ret), ret.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').slice(0,110));
  ck('no NaN anywhere in the risk panel', !/NaN/.test(risk), risk.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').slice(0,120));
  ck('AI loading is finite and plausible', isFinite(A.aiLoading(rows))&&Math.abs(A.aiLoading(rows))<3, A.aiLoading(rows).toFixed(3));
  ck('AI factor is orthogonal to the market', Math.abs(A.corr(A.aiX,A.mkt))<1e-9, A.corr(A.aiX,A.mkt).toExponential(2));
  ck('portfolio series length matches the return series', A.seriesOf(rows).length===A.mkt.length);
  ck('correlation matrix rendered', /ASML/.test(cache.cormatrix.innerHTML)&&!/NaN/.test(cache.cormatrix.innerHTML));
  ck('a feedless holding is kept out of the correlation matrix', !/ZZZZ/.test(cache.cormatrix.innerHTML));
  ck('and is named as excluded rather than dropped quietly', /ZZZZ/.test(cache.excl.innerHTML), cache.excl.innerHTML.replace(/<[^>]+>/g,''));
  ck('efficient frontier drawn', /svg/.test(cache.frontier.innerHTML)&&!/NaN/.test(cache.frontier.innerHTML));
  ck('BTC excluded from the optimiser by default', /Excluded:/.test(cache.excl.innerHTML)&&/BTC/.test(cache.excl.innerHTML));
  ck('server residual replaces the browser one, not duplicates it',
     A.buildAlerts().filter(a=>a.kind==='Thesis check').length===1,
     A.buildAlerts().map(a=>a.kind).join(', '));
  ck('alerts merged from both sides', A.buildAlerts().length>=2,
     A.buildAlerts().map(a=>a.kind).join(', '));
  ck('no alert text contains undefined', !A.buildAlerts().some(a=>/undefined|NaN/.test(a.title+a.body)));

  // ---------- 3. a repo that has never run ----------
  global.fetch=async()=>({ok:false,status:404,json:async()=>({})});
  Object.keys(cache).forEach(k=>delete cache[k]);
  Object.keys(store).forEach(k=>delete store[k]);
  const mod={exports:{}};
  let threw=null;
  try{
    new Function('module','exports','require',js+';module.exports={};')(mod,mod.exports,require);
    await new Promise(r=>setTimeout(r,120));
  }catch(e){threw=e}
  console.log('\n--- no data at all ---');
  ck('page survives with every file missing', !threw, threw?threw.message:'');
  ck('status bar says so plainly', /No data fetched yet|waiting for the first run/.test(cache.statusbar.innerHTML),
     cache.statusbar.innerHTML);
  ck('news empty state is about missing data, not a filter',
     /No headlines fetched yet/.test(cache.stories.innerHTML));

  console.log('\n'+(FAILS.length?FAILS.length+' FAILURES: '+FAILS.join(', '):'all dashboard tests passed'));
  process.exit(FAILS.length?1:0);
})();
