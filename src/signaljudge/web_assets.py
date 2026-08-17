"""Self-contained, same-origin assets for the local SignalJudge application."""

APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Auditable sports prediction reconciliation">
  <title>SignalJudge · Live decision console</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main>
    <header class="hero">
      <div>
        <div class="eyebrow">SIGNALJUDGE / OPERATOR CONSOLE</div>
        <h1>Live fixtures.<br><span>Defensible decisions.</span></h1>
        <p>Independent model predictions reconciled against current, de-vigged bookmaker consensus—with every source choice explained.</p>
      </div>
      <div id="source-status" class="status neutral">Waiting for data</div>
    </header>

    <section class="toolbar" aria-label="Data controls">
      <div class="field mode-field">
        <span class="field-label">Data mode</span>
        <div class="segmented" role="group" aria-label="Data mode">
          <button id="mode-live" class="active" type="button">Live markets</button>
          <button id="mode-demo" type="button">Assessment demo</button>
        </div>
      </div>
      <label class="field" for="sport">
        <span class="field-label">Sport</span>
        <select id="sport" disabled><option>Loading sports…</option></select>
      </label>
      <label class="field" for="region">
        <span class="field-label">Bookmaker region</span>
        <select id="region" disabled></select>
      </label>
      <div class="field refresh-field">
        <span class="field-label">Market snapshot</span>
        <button id="refresh" class="primary" type="button">Refresh now</button>
      </div>
    </section>

    <div id="message" class="message" role="status" aria-live="polite" hidden></div>

    <section id="summary" class="summary" aria-label="Run summary"></section>

    <section class="board">
      <div class="board-head">
        <div>
          <div class="eyebrow" id="board-eyebrow">CURRENT RANKING</div>
          <h2 id="board-title">Choose a sport</h2>
          <p id="board-meta" class="muted">Fixtures and predictions will appear here.</p>
        </div>
        <div id="audit" class="audit neutral">Audit not checked</div>
      </div>

      <div class="filters">
        <label for="search" class="search-wrap">
          <span class="sr-only">Search teams</span>
          <input id="search" type="search" placeholder="Search team or event…" autocomplete="off">
        </label>
        <label for="filter" class="sr-only">Filter decisions</label>
        <select id="filter">
          <option value="ALL">All fixtures</option>
          <option value="RECONCILED">Reconciled only</option>
          <option value="CONFLICT">Material conflicts</option>
          <option value="MODEL">Model wins</option>
          <option value="MARKET">Market wins</option>
          <option value="ABSTAIN">Abstentions</option>
          <option value="UNAVAILABLE">Prediction unavailable</option>
        </select>
        <label for="sort" class="sr-only">Sort fixtures</label>
        <select id="sort">
          <option value="RANK">Reconciled rank</option>
          <option value="KICKOFF">Kickoff soonest</option>
          <option value="PROBABILITY">Highest final probability</option>
          <option value="CONFLICT">Largest disagreement</option>
        </select>
      </div>

      <div class="table-scroll">
        <table>
          <thead><tr>
            <th>Rank</th><th>Fixture</th><th>Kickoff</th><th>Prediction</th>
            <th>Model</th><th>Market</th><th>Final</th><th>Decision</th><th>Audit rationale</th>
          </tr></thead>
          <tbody id="ranking"></tbody>
        </table>
      </div>
      <div id="empty" class="empty" hidden>No fixtures match these filters.</div>
    </section>

    <footer>
      <p><strong>SignalJudge is an engineering demonstration, not betting advice.</strong> “Live” means the latest available provider snapshot; started fixtures are labelled separately.</p>
      <p id="run-id"></p>
    </footer>
  </main>
  <script src="/assets/app.js" defer></script>
</body>
</html>
"""


APP_CSS = r"""
:root{--bg:#07111f;--panel:#0c1a2c;--panel2:#11243c;--ink:#edf6ff;--muted:#91a6bf;--line:#21364f;--teal:#2dd4bf;--blue:#60a5fa;--purple:#c4b5fd;--amber:#fbbf24;--red:#fb7185;--shadow:#0005}
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;min-width:0;background:radial-gradient(circle at 82% -5%,#173658 0,var(--bg) 36%);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
button,select,input{font:inherit}button,select{cursor:pointer}main{max-width:1520px;margin:auto;padding:38px 26px 58px}.hero{display:flex;justify-content:space-between;align-items:flex-start;gap:32px;margin-bottom:25px}.eyebrow{color:var(--teal);font-size:11px;font-weight:850;letter-spacing:.15em}.hero h1{font-size:46px;line-height:1.02;letter-spacing:-.035em;margin:10px 0}.hero h1 span{color:#a7cfff}.hero p{max-width:790px;color:var(--muted);font-size:16px;margin:0}
.status,.audit{border:1px solid var(--line);border-radius:999px;padding:9px 13px;white-space:nowrap;font-size:12px;font-weight:800}.good{color:#98f5e7;background:#0b2929;border-color:#1d5f57}.warning{color:#ffe08a;background:#332716;border-color:#775b1d}.bad{color:#fec4ce;background:#361a23;border-color:#803047}.neutral{color:#bfd0e4;background:#102137}
.toolbar{display:grid;grid-template-columns:1.35fr 1.2fr .8fr .75fr;gap:12px;background:rgba(12,26,44,.92);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 22px 55px var(--shadow)}.field{display:flex;flex-direction:column;gap:7px}.field-label{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:800}.segmented{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--line);border-radius:10px;padding:3px;background:#081525}.segmented button{border:0;background:transparent;color:var(--muted);padding:8px 10px;border-radius:7px}.segmented button.active{background:#19344f;color:var(--ink);box-shadow:0 2px 9px #0005}.primary,select,input{border:1px solid var(--line);background:#0a1828;color:var(--ink);border-radius:10px;padding:9px 11px;min-height:40px}.primary{background:linear-gradient(135deg,#0f766e,#0d9488);border-color:#2dd4bf;color:white;font-weight:850}.primary:hover{filter:brightness(1.1)}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #60a5fa88;outline-offset:2px}button:disabled,select:disabled{opacity:.5;cursor:not-allowed}
.message{margin:14px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#11243c;color:#cad8e8}.message.error{border-color:#803047;background:#361a23;color:#fec4ce}.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:18px 0}.card{min-height:106px;background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:15px;padding:15px}.card-label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}.card-value{font-size:27px;font-weight:900;margin:3px 0;font-variant-numeric:tabular-nums}.card-note{color:var(--muted);font-size:11px}
.board{background:rgba(12,26,44,.94);border:1px solid var(--line);border-radius:20px;box-shadow:0 24px 60px var(--shadow);overflow:hidden}.board-head{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;padding:21px 21px 14px}.board h2{margin:4px 0 1px;font-size:23px}.muted{color:var(--muted);margin:0}.filters{display:flex;justify-content:flex-end;gap:9px;padding:0 21px 15px}.search-wrap{flex:1}.filters input{width:100%;max-width:420px}.filters select{min-width:180px}.table-scroll{overflow:auto;border-top:1px solid var(--line)}table{width:100%;min-width:1230px;border-collapse:collapse;font-size:13px}th{position:sticky;top:0;background:#0b192a;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em;text-align:left;padding:11px 9px;border-bottom:1px solid var(--line)}td{padding:14px 9px;border-bottom:1px solid #1b3048;vertical-align:top}.rank{font-size:18px;font-weight:900}.fixture{min-width:190px}.fixture b{font-size:14px}.kickoff{min-width:132px}.selection{min-width:135px}.pct{font-weight:850;font-variant-numeric:tabular-nums}.pill{display:inline-block;padding:3px 8px;border-radius:99px;font-size:10px;font-weight:900;letter-spacing:.03em}.pill.model{background:#30245e;color:var(--purple)}.pill.market{background:#113d39;color:#78f4de}.pill.abstain{background:#4a2f16;color:#fcd34d}.pill.unavailable{background:#233247;color:#b9c7d8}.pill.started{background:#4a2330;color:#fda4af;margin-top:6px}.pill.upcoming{background:#173456;color:#93c5fd;margin-top:6px}.reason{min-width:280px;max-width:430px;color:#bac9da}.small{font-size:11px;color:var(--muted);margin-top:3px}.conflict{color:var(--amber);font-weight:800}.empty{padding:35px;text-align:center;color:var(--muted)}footer{display:flex;justify-content:space-between;gap:30px;color:var(--muted);font-size:11px;margin-top:16px}footer p{max-width:700px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:1050px){.toolbar{grid-template-columns:1fr 1fr}.summary{grid-template-columns:repeat(3,1fr)}}@media(max-width:680px){main{padding:24px 14px 40px}.hero{display:block}.hero h1{font-size:36px}.status{display:inline-block;margin-top:15px}.toolbar{grid-template-columns:1fr}.summary{grid-template-columns:repeat(2,1fr)}.board-head{display:block}.audit{display:inline-block;margin-top:12px}.filters{flex-direction:column}.filters input,.filters select{width:100%;max-width:none}.summary .card{min-height:94px}footer{display:block}}
"""


APP_JS = r"""
'use strict';
const state={mode:'live',sports:[],data:null,loading:false,liveSport:null,liveRegion:null};
const el=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=value=>value===null||value===undefined?'—':`${(Number(value)*100).toFixed(1)}%`;
const dateTime=value=>{if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(d)};
const number=value=>new Intl.NumberFormat().format(Number(value)||0);

async function api(path){const response=await fetch(path,{headers:{'X-SignalJudge-Request':'1'},cache:'no-store'});let body={};try{body=await response.json()}catch(_error){}if(!response.ok)throw new Error(body.error||`Request failed (${response.status})`);return body}
function setLoading(value){state.loading=value;el('refresh').disabled=value;el('sport').disabled=value||state.mode==='demo';el('region').disabled=value||state.mode==='demo';el('refresh').textContent=value?'Loading…':state.mode==='demo'?'Replay demo':'Refresh now'}
function message(text,error=false){const node=el('message');node.hidden=!text;node.textContent=text||'';node.className=`message${error?' error':''}`}
function setMode(mode){if(mode===state.mode)return;if(mode==='demo'){state.liveSport=el('sport').value;state.liveRegion=el('region').value;el('sport').value='baseball_mlb';el('region').value='us'}else{el('sport').value=state.liveSport||'soccer_epl';el('region').value=state.liveRegion||state.sports.find(item=>item.key===el('sport').value)?.default_region||'uk'}state.mode=mode;el('mode-live').classList.toggle('active',mode==='live');el('mode-demo').classList.toggle('active',mode==='demo');el('filter').value='ALL';el('search').value='';el('sport').disabled=mode==='demo';el('region').disabled=mode==='demo';el('refresh').textContent=mode==='demo'?'Replay demo':'Refresh now';load(false)}

async function boot(){try{const config=await api('/api/sports');state.sports=config.sports||[];const sport=el('sport');sport.innerHTML=state.sports.map(item=>`<option value="${esc(item.key)}">${esc(item.title)} · model ${esc(item.prediction_status)}</option>`).join('');sport.value=config.default_sport||state.sports[0]?.key||'';const region=el('region');region.innerHTML=(config.regions||[]).map(item=>`<option value="${esc(item)}">${esc(item.toUpperCase())}</option>`).join('');syncRegion();if(!config.live_configured)message('Live odds key is not configured in this terminal. Assessment demo remains fully available.',false);await load(false)}catch(error){message(error.message,true);setStatus('Application unavailable','bad')}}
function syncRegion(){const selected=state.sports.find(item=>item.key===el('sport').value);if(selected)el('region').value=selected.default_region}
async function load(refresh){if(state.loading)return;setLoading(true);if(refresh)message('Requesting a fresh provider snapshot…');try{const path=state.mode==='demo'?'/api/demo':`/api/rankings?sport=${encodeURIComponent(el('sport').value)}&region=${encodeURIComponent(el('region').value)}&refresh=${refresh?'1':'0'}`;state.data=await api(path);message(statusMessage(state.data));render()}catch(error){message(error.message,true);setStatus('Data unavailable','bad')}finally{setLoading(false)}}
function statusMessage(data){const p=data.prediction_source||{};if(data.market_source?.degraded)return `Provider unavailable: showing explicitly degraded cache (${Math.round(data.market_source.cache_age_seconds)}s old).`;if(state.mode==='live'&&p.status==='missing')return `Live fixtures loaded, but no independent ${data.sport_key} model or prediction file is installed. Fixtures remain visible without invented scores.`;if(state.mode==='live'&&data.reconciled_events===0)return `No prediction event IDs matched this live snapshot. ${p.loaded||0} prediction records were loaded and ${p.unmatched||0} did not match.`;if(state.mode==='live'&&p.status==='trained')return `${p.model_version} generated ${p.loaded} fixture predictions without reading bookmaker prices. Holdout accuracy ${pct(p.accuracy)} across ${number(p.sample_size)} matches.`;return data.response_cache?'Using the recent in-process snapshot to protect API quota. Use Refresh now for a provider call.':''}
function setStatus(text,kind){const node=el('source-status');node.textContent=text;node.className=`status ${kind}`}
function render(){const data=state.data;if(!data)return;const origin=data.market_source?.origin||'UNKNOWN';setStatus(data.market_source?.degraded?`DEGRADED ${origin}`:`${origin} DATA`,data.market_source?.degraded?'warning':'good');el('board-eyebrow').textContent=state.mode==='demo'?'GUARANTEED REPRODUCIBLE RUN':'CURRENT PROVIDER SNAPSHOT';el('board-title').textContent=data.title;const source=data.prediction_source||{};const fetched=data.fetched_at?dateTime(data.fetched_at):'No reconciled run yet';const model=source.model_version?` · ${source.model_version}`:'';el('board-meta').textContent=`${fetched} · ${data.region.toUpperCase()} region · ${source.loaded||0} independent predictions${model}`;const audit=data.audit||{};el('audit').textContent=`${audit.valid?'✓':'⚠'} Audit ${audit.valid?'verified':'invalid'} · ${number(audit.entries)} decisions`;el('audit').className=`audit ${audit.valid?'good':'bad'}`;renderSummary(data);renderRows();el('run-id').textContent=data.run_id?`Run ${data.run_id}${data.reused?' · idempotent replay':''}`:'No reconciliation run was created because no model prediction matched.'}
function renderSummary(data){const source=data.source_counts||{},model=data.prediction_source||{},quota=data.market_source?.quota||{};const cards=[['Fixtures',data.total_events,'Fetched provider events'],['Reconciled',data.reconciled_events,'Independent prediction matched'],['Conflicts',data.material_conflicts,'Probability or rank gap'],['Model wins',source.MODEL||0,'Explicit source selection'],['Market wins',source.MARKET||0,'Explicit source selection']];if(model.accuracy!==undefined)cards.push(['Model test',pct(model.accuracy),`${number(model.sample_size)} held-out matches`]);cards.push(['API quota',quota.remaining??'—','Requests remaining']);el('summary').innerHTML=cards.map(card=>`<article class="card"><div class="card-label">${esc(card[0])}</div><div class="card-value">${esc(card[1])}</div><div class="card-note">${esc(card[2])}</div></article>`).join('')}
function visibleRows(){const search=el('search').value.trim().toLowerCase(),filter=el('filter').value,sort=el('sort').value;let rows=[...(state.data?.matches||[])];rows=rows.filter(item=>{const haystack=`${item.home_team} ${item.away_team} ${item.selection||''} ${item.event_id}`.toLowerCase();if(search&&!haystack.includes(search))return false;if(filter==='ALL')return true;if(filter==='RECONCILED')return item.status==='RECONCILED';if(filter==='CONFLICT')return item.material_conflict;if(filter==='UNAVAILABLE')return !item.prediction_available;return item.winner===filter});const timestamp=item=>{const n=new Date(item.commence_time).getTime();return Number.isNaN(n)?Number.MAX_SAFE_INTEGER:n};const final=item=>item.reconciled_probability===null||item.reconciled_probability===undefined?-1:Number(item.reconciled_probability);const gap=item=>item.model_probability===null||item.market_probability===null?-1:Math.abs(Number(item.model_probability)-Number(item.market_probability));rows.sort((a,b)=>sort==='KICKOFF'?timestamp(a)-timestamp(b):sort==='PROBABILITY'?final(b)-final(a):sort==='CONFLICT'?gap(b)-gap(a):(a.final_rank??999999)-(b.final_rank??999999)||timestamp(a)-timestamp(b));return rows}
function renderRows(){const rows=visibleRows();el('empty').hidden=rows.length>0;el('ranking').innerHTML=rows.map(item=>{const winner=String(item.winner||'UNAVAILABLE').toLowerCase();const eventState=String(item.event_state||'UNKNOWN').toLowerCase();const opponent=item.selection===item.home_team?item.away_team:item.selection===item.away_team?item.home_team:item.selection==='Draw'?'Home and away teams':'—';const conflict=item.material_conflict?'<div class="small conflict">Material conflict</div>':'';const reasons=(item.reason_codes||[]).map(esc).join(' · ');return `<tr><td class="rank">${esc(item.final_rank??'—')}</td><td class="fixture"><b>${esc(item.home_team)} vs ${esc(item.away_team)}</b><div class="small">Home vs away · ${esc(item.sport_key)} · ${esc(item.event_id)}</div><span class="pill ${esc(eventState)}">${esc(item.event_state)}</span></td><td class="kickoff">${esc(dateTime(item.commence_time))}</td><td class="selection"><b>${esc(item.selection||'Prediction unavailable')}</b><div class="small">Opponent: ${esc(opponent)}</div></td><td class="pct">${pct(item.model_probability)}</td><td class="pct">${pct(item.market_probability)}${conflict}</td><td class="pct">${pct(item.reconciled_probability)}</td><td><span class="pill ${esc(winner)}">${esc(item.winner)}</span><div class="small">${item.decision_confidence===null||item.decision_confidence===undefined?'':`${pct(item.decision_confidence)} confidence`}</div></td><td class="reason">${esc(item.rationale)}<div class="small">${reasons}</div></td></tr>`}).join('')}

el('mode-live').addEventListener('click',()=>setMode('live'));el('mode-demo').addEventListener('click',()=>setMode('demo'));el('sport').addEventListener('change',()=>{syncRegion();load(false)});el('region').addEventListener('change',()=>load(false));el('refresh').addEventListener('click',()=>load(state.mode==='live'));el('search').addEventListener('input',renderRows);el('filter').addEventListener('change',renderRows);el('sort').addEventListener('change',renderRows);boot();
"""
