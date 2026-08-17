"""Dependency-free interactive audit dashboard generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

from signaljudge.models import RunResult
from signaljudge.io import atomic_write_text


def _safe_json(value: Any) -> str:
    return (
        json.dumps(value, sort_keys=True)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def generate_dashboard(
    output: Path,
    opening: RunResult,
    latest: RunResult,
    metrics: Mapping[str, Mapping[str, float]],
    cases: List[Mapping[str, object]],
    audit_valid: bool,
    audit_entries: int,
    live_mode: bool = False,
) -> None:
    runs = [
        {
            "label": "Live market snapshot" if live_mode else "Latest snapshot",
            "run_id": latest.run_id,
            "fetched_at": latest.odds_fetched_at,
            "conflicts": latest.material_conflicts,
            "source_counts": latest.source_counts,
            "decisions": [decision.as_dict() for decision in latest.decisions],
        }
    ]
    if not live_mode:
        runs.insert(
            0,
            {
                "label": "Opening snapshot",
                "run_id": opening.run_id,
                "fetched_at": opening.odds_fetched_at,
                "conflicts": opening.material_conflicts,
                "source_counts": opening.source_counts,
                "decisions": [decision.as_dict() for decision in opening.decisions],
            },
        )
    payload = {
        "runs": runs,
        "metrics": metrics,
        "cases": cases,
        "audit": {"valid": audit_valid, "entries": audit_entries},
        "live": live_mode,
    }
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
    <title>SignalJudge</title>
<style>
:root{--bg:#07111f;--panel:#0d1b2d;--panel2:#12233a;--ink:#ecf4ff;--muted:#91a4bd;--teal:#2dd4bf;--amber:#fbbf24;--red:#fb7185;--line:#233750}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#122a46 0,var(--bg) 38%);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif}
    main{max-width:1500px;margin:auto;padding:42px 26px 70px}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:30px}.eyebrow{color:var(--teal);font-weight:800;letter-spacing:.15em;font-size:12px}.title{font-size:38px;line-height:1.05;margin:8px 0}.sub{color:var(--muted);max-width:760px}.audit{border:1px solid #1e594f;background:#0b2a29;padding:10px 14px;border-radius:12px;color:#9df3e6;white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:22px 0}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:17px}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:28px;font-weight:800;margin-top:3px}.small{font-size:12px;color:var(--muted)}
    .panel{background:rgba(13,27,45,.92);border:1px solid var(--line);border-radius:18px;padding:20px;margin-top:18px;box-shadow:0 18px 45px #0004;overflow-x:auto}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:16px}h2{font-size:19px;margin:0}input[type=range]{width:260px;accent-color:var(--teal)}
    table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding:11px 8px;border-bottom:1px solid var(--line)}td{padding:13px 8px;border-bottom:1px solid #1b2e45;vertical-align:top}.rank{font-weight:900;font-size:17px}.fixture{min-width:190px}.kickoff{min-width:130px}.pick{min-width:145px}.pct{font-variant-numeric:tabular-nums;font-weight:700}.pill{display:inline-block;border-radius:99px;padding:3px 8px;font-size:11px;font-weight:900}.model{background:#30245e;color:#c4b5fd}.market{background:#113d39;color:#78f4de}.abstain{background:#4a2f16;color:#fcd34d}.conflict{color:var(--amber)}.reason{min-width:250px;max-width:390px;color:#b9c8da}.delta-up{color:var(--teal)}.delta-down{color:var(--red)}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metric-title{display:flex;justify-content:space-between;font-weight:800}.bar{height:7px;background:#20334c;border-radius:9px;margin:12px 0;overflow:hidden}.bar span{height:100%;display:block;background:linear-gradient(90deg,var(--teal),#60a5fa)}.winner-card{border-color:#23685e}.cases{display:flex;gap:10px;flex-wrap:wrap}.case{background:#10233a;border:1px solid var(--line);border-radius:12px;padding:10px 12px}.yes{color:var(--teal);font-weight:800}
@media(max-width:900px){.grid,.metrics{grid-template-columns:1fr 1fr}.hide-sm{display:none}header{display:block}.audit{display:inline-block;margin-top:15px}}@media(max-width:600px){.grid,.metrics{grid-template-columns:1fr}.title{font-size:30px}.panel{overflow-x:auto}.panel-head{display:block}input[type=range]{width:100%;margin-top:12px}}
</style></head><body><main>
    <header><div><div class="eyebrow" id="eyebrow">SIGNALJUDGE / DECISION REPLAY LAB</div><h1 class="title" id="page-title">When signals disagree,<br>make the decision auditable.</h1><div class="sub" id="page-subtitle">A stateful reconciliation engine comparing calibrated model predictions with de-vigged, quality-scored market consensus.</div></div><div class="audit" id="audit"></div></header>
<section class="grid" id="summary"></section>
    <section class="panel"><div class="panel-head"><div><h2 id="run-label">Reconciled ranking</h2><div class="small" id="run-meta"></div></div><div id="timeline-control"><label class="small" for="timeline">Replay market update</label><br><input id="timeline" type="range" min="0" max="1" step="1" value="1"></div></div>
    <table><thead><tr><th>Rank</th><th>Fixture</th><th>Kickoff</th><th>Prediction</th><th>Model</th><th>Market</th><th>Final</th><th>Winner</th><th class="hide-sm">Change</th><th>Audit rationale</th></tr></thead><tbody id="ranking"></tbody></table></section>
    <section class="panel" id="metrics-panel"><div class="panel-head"><div><h2>Counterfactual replay</h2><div class="small">Lower Brier score and log loss are better. Settled fixture outcomes are never used by the decision engine.</div></div></div><div class="metrics" id="metrics"></div></section>
    <section class="panel" id="cases-panel"><div class="panel-head"><div><h2>Blind-source corrections</h2><div class="small">Cases where the reconciled decision corrected the classification made by a single-source baseline.</div></div></div><div class="cases" id="cases"></div></section>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
    const data=JSON.parse(document.getElementById('payload').textContent);const pct=v=>v===null||v===undefined?'—':(v*100).toFixed(1)+'%';
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const kickoff=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(d)};
    if(data.live){document.title='SignalJudge Live';document.getElementById('eyebrow').textContent='SIGNALJUDGE / LIVE DECISION BOARD';document.getElementById('page-title').innerHTML='Live fixtures.<br>Auditable probabilities.';document.getElementById('page-subtitle').textContent='Current bookmaker consensus reconciled with an independent local model prediction for every identified fixture.';}
    document.getElementById('audit').textContent=(data.audit.valid?'✓ Audit chain verified':'⚠ Audit chain invalid')+' · '+data.audit.entries+' decisions';
    function renderRun(index){const r=data.runs[index];document.getElementById('run-label').textContent=r.label;document.getElementById('run-meta').textContent=r.fetched_at+' · '+r.run_id;
     const changed=r.decisions.filter(d=>d.reconciled_probability!==null&&d.previous_probability!==null&&Math.abs(d.reconciled_probability-d.previous_probability)>=.01).length;
     document.getElementById('summary').innerHTML=[['Material conflicts',r.conflicts,'≥10pp or ≥3 ranks'],['Model wins',r.source_counts.MODEL,'Explicit source decisions'],['Market wins',r.source_counts.MARKET,'Explicit source decisions'],['Abstained',r.source_counts.ABSTAIN??0,'Unsafe or incomplete evidence'],['Ranking updates',changed,index?'Since opening snapshot':'Initial state']].map(x=>`<div class="card"><div class="label">${x[0]}</div><div class="value">${x[1]}</div><div class="small">${x[2]}</div></div>`).join('');
     document.getElementById('ranking').innerHTML=r.decisions.map(d=>{let delta='—',cls='';if(d.reconciled_probability!==null&&d.previous_probability!==null){const n=(d.reconciled_probability-d.previous_probability)*100;delta=(n>=0?'+':'')+n.toFixed(1)+'pp';cls=n>=0?'delta-up':'delta-down'}const opponent=d.selection===d.home_team?d.away_team:d.selection===d.away_team?d.home_team:'Home and away teams';return `<tr><td class="rank">${d.final_rank??'—'}</td><td class="fixture"><b>${esc(d.away_team)} at ${esc(d.home_team)}</b><div class="small">${esc(d.sport_key)} · ${esc(d.event_id)}</div></td><td class="kickoff"><b>${esc(kickoff(d.commence_time))}</b></td><td class="pick"><b>${esc(d.selection)}</b><div class="small">Opponent: ${esc(opponent)}</div></td><td class="pct">${pct(d.model_probability)}</td><td class="pct">${pct(d.market_probability)}${d.material_conflict?'<div class="small conflict">conflict</div>':''}</td><td class="pct">${pct(d.reconciled_probability)}</td><td><span class="pill ${esc(d.winner.toLowerCase())}">${esc(d.winner)}</span><div class="small">${pct(d.decision_confidence)} decision confidence</div></td><td class="hide-sm ${cls}">${delta}</td><td class="reason">${esc(d.rationale)}<div class="small">${d.reason_codes.map(esc).join(' · ')}</div></td></tr>`}).join('');}
    const metricEntries=Object.entries(data.metrics);if(metricEntries.length){const best=Math.min(...metricEntries.map(([,x])=>x.brier));document.getElementById('metrics').innerHTML=metricEntries.map(([name,m])=>`<div class="card ${m.brier===best?'winner-card':''}"><div class="metric-title"><span>${name==='AGENT'?'SignalJudge':name+' only'}</span><span>${m.brier===best?'BEST':''}</span></div><div class="bar"><span style="width:${Math.max(5,(1-m.brier)*100)}%"></span></div><div><b>Brier ${m.brier.toFixed(3)}</b> · Log loss ${m.log_loss.toFixed(3)}</div><div class="small">Selection accuracy ${pct(m.accuracy)} · n=${m.sample_size}</div></div>`).join('');}else{document.getElementById('metrics-panel').hidden=true;}
    const corrected=data.cases.filter(c=>c.corrected_model_only||c.corrected_market_only);if(data.cases.length){document.getElementById('cases').innerHTML=corrected.map(c=>`<div class="case"><b>${esc(c.selection)}</b><div class="small">${esc(c.event_id)}</div><div class="yes">✓ Corrected ${c.corrected_model_only?'model-only':'market-only'} baseline</div></div>`).join('')||'<div class="small">No corrected classifications in this replay.</div>';}else{document.getElementById('cases-panel').hidden=true;}
    const timeline=document.getElementById('timeline');timeline.max=String(Math.max(0,data.runs.length-1));timeline.value=timeline.max;if(data.runs.length<2)document.getElementById('timeline-control').hidden=true;timeline.addEventListener('input',e=>renderRun(Number(e.target.value)));renderRun(data.runs.length-1);
</script></main></body></html>"""
    atomic_write_text(output, html.replace("__PAYLOAD__", _safe_json(payload)))


def generate_live_dashboard(
    output: Path, result: RunResult, audit_valid: bool, audit_entries: int
) -> None:
    generate_dashboard(
        output,
        opening=result,
        latest=result,
        metrics={},
        cases=[],
        audit_valid=audit_valid,
        audit_entries=audit_entries,
        live_mode=True,
    )
