#!/usr/bin/env python3
"""Render ONLY observed BranchLab JSON data to an offline correctness dashboard.

Usage: python3 branchlab/report.py results/state-local.json results/state-archil.json --output docs/branchlab.html
"""
import argparse
import html
import json
from pathlib import Path

STYLE = r"""
:root{color-scheme:light;--plane:#f6f3ec;--surface:#fffdf8;--ink:#161513;--ink-2:#4b4842;--muted:#8b867c;--grid:#ebe6dc;--border:rgba(22,21,19,.09);--wash:#f1ede4;
--s-archil:#a33a0b;--s-local:#ef9a55;--accent:#a33a0b;--good:#3f6b3a;--critical:#9c3a2e;
font-family:Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased}
*{box-sizing:border-box}body{margin:0;background:var(--plane);color:var(--ink);font-size:15px;line-height:1.6}
.wrap{max-width:1080px;margin:0 auto;padding:0 28px}a{color:var(--ink);text-decoration-color:var(--muted);text-underline-offset:3px}
code{font:12.5px ui-monospace,'SF Mono',Menlo,monospace;background:var(--wash);padding:1px 5px;border-radius:4px}
.topnav{border-bottom:1px solid var(--border)}.topnav .wrap{display:flex;justify-content:space-between;align-items:center;height:54px;font-size:13px}
.brand{font-weight:650;font-size:15px}.topnav a{color:var(--muted);text-decoration:none}.topnav a:hover{color:var(--ink)}
header{padding:64px 0 8px}.eyebrow{font-size:11.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(32px,4.6vw,48px);line-height:1.05;letter-spacing:-.035em;margin:14px 0 16px;font-weight:650}h1 .soft{color:var(--muted)}
.lede{font-size:17px;color:var(--ink-2);max-width:760px;margin:0}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);margin:36px 0 0;border-top:1px solid var(--ink)}
.tile{padding:16px 20px 4px 0}.tile+.tile{padding-left:20px;border-left:1px solid var(--border)}
.tile small{font-size:12px;color:var(--muted)}.tile strong{display:block;font-size:32px;font-weight:600;letter-spacing:-.03em;margin:4px 0;font-variant-numeric:tabular-nums}
.controls{display:flex;gap:16px;align-items:center;margin:32px 0 0;padding:12px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);flex-wrap:wrap;font-size:12px;color:var(--muted)}
select,button{font:inherit;font-size:13px;color:var(--ink);background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 10px}button{cursor:pointer}button:hover{border-color:var(--muted)}
.legend{display:flex;gap:16px;margin-left:auto;color:var(--ink-2)}.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
section{padding:44px 0 4px}.ph{display:grid;grid-template-columns:200px 1fr;gap:6px 36px;margin-bottom:18px}
h2{font-size:21px;letter-spacing:-.02em;margin:0 0 4px;font-weight:600}.ph p{margin:0;color:var(--ink-2);font-size:14px;max-width:660px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 20px}
.task{display:grid;grid-template-columns:210px 1fr;gap:4px 16px;padding:9px 0;border-bottom:1px solid var(--grid)}.task:last-child{border-bottom:0}
.tname{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-top:1px}.bars{display:grid;gap:5px}
.barrow{display:grid;grid-template-columns:1fr 132px;gap:12px;align-items:center;font-size:12px}
.track{height:12px;display:flex;gap:2px}.bar{height:100%;border-radius:0 3px 3px 0;min-width:2px}
.val{font-variant-numeric:tabular-nums;text-align:right;color:var(--ink-2);white-space:nowrap}.val b{color:var(--ink);font-weight:600}
.ok{color:var(--good);font-weight:500}.bad{color:var(--critical);font-weight:500}
.tablewrap{overflow:auto;max-height:520px;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}th{position:sticky;top:0;background:var(--surface);text-align:left;font-size:11px;letter-spacing:.06em;color:var(--muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--grid)}
td{padding:7px 12px;border-bottom:1px solid var(--grid);white-space:nowrap;color:var(--ink-2)}td.num,th.num{text-align:right}tr:hover td{background:var(--wash)}
.notes{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 32px;border-top:1px solid var(--ink);padding-top:16px;font-size:13.5px;color:var(--ink-2)}.notes p{margin:0 0 8px}
.vis{width:100%;height:auto;display:block;font-family:inherit}.vis .g{stroke:var(--grid)}.vis .sep{stroke:var(--grid)}.vis .ax{fill:var(--muted);font-size:11px}.vis .lab{fill:var(--ink);font-size:12.5px;font-weight:600;dominant-baseline:middle}.vis .ref{stroke:var(--ink-2);stroke-dasharray:3 3}.vis .reft{fill:var(--ink-2);font-size:11px}.vis .win{stroke-width:2;opacity:.55}.vis .tick{stroke-width:2}.vis .dot{stroke-width:2}
.flat{border:0;max-height:none}.flat td{white-space:normal;vertical-align:top}.docs{color:var(--muted);font-size:12px}.n{color:var(--muted);font-size:11.5px}.foot{font-size:12.5px;color:var(--muted);margin:12px 0 0}
footer{color:var(--muted);font-size:12.5px;padding:40px 0 64px}.empty{color:var(--muted);padding:32px 0;font-size:14px}
@media(max-width:820px){.wrap{padding:0 16px}.tiles{grid-template-columns:repeat(2,1fr)}.tile:nth-child(3){border-left:0;padding-left:0}.ph,.task,.notes{grid-template-columns:1fr}.legend{margin-left:0}}
"""
JS = r"""
(()=>{'use strict';
const reports=JSON.parse(document.getElementById('branchlab-payload').textContent);
const $=id=>document.getElementById(id);
const all=reports.flatMap(r=>(r.observations||[]).map(o=>({...o,backend:r.backend,run_id:r.run_id})));
const color=b=>b.startsWith('archil')?'var(--s-archil)':'var(--s-local)';
const label=b=>b.startsWith('archil')?'Archil · fresh microVM per step':'Local · fresh process per step';
const nice=v=>Number.isFinite(v)?(v<10?v.toFixed(2):v.toFixed(v<100?1:0)):'—';
const median=a=>{const xs=a.filter(Number.isFinite).sort((x,y)=>x-y);if(!xs.length)return NaN;const m=xs.length>>1;return xs.length%2?xs[m]:(xs[m-1]+xs[m])/2;};
const backends=[...new Set(all.map(o=>o.backend))].sort((a,b)=>(a.startsWith('archil')?0:1)-(b.startsWith('archil')?0:1));
for(const b of backends){const o=document.createElement('option');o.value=b;o.textContent=label(b);$('provider').appendChild(o);
 const k=document.createElement('span');k.innerHTML='<i class="key" style="background:'+color(b)+'"></i>';k.append(label(b));$('legend').appendChild(k);}
const steps=['handoff/init','handoff/write-fsync','handoff/read-fresh-vm','handoff/cleanup','sqlite/init','sqlite/commit-16','sqlite/reopen-fresh-vm','sqlite/cleanup','readfan/init','readfan/prepare','readfan/parallel-read','readfan/cleanup','writers/init','writers/disjoint-path','writers/reconcile','writers/cleanup','trial/cleanup'];
const rank=t=>{const i=steps.indexOf(t);return i<0?999:i;};
function selected(){const b=$('provider').value;return all.filter(o=>b==='all'||o.backend===b);}
const core=o=>!/^(semantics|visibility)\//.test(o.task);
function tasks(obs){return [...new Set(obs.map(o=>o.task))].sort((a,b)=>rank(a)-rank(b)||a.localeCompare(b));}
function chart(el,obs,metric){el.innerHTML='';const ts=tasks(obs);if(!ts.length){el.innerHTML='<div class="empty">No measurements loaded.</div>';return;}
 const rows=ts.map(t=>({t,per:backends.map(b=>{const xs=obs.filter(o=>o.task===t&&o.backend===b);return{b,n:xs.length,good:xs.filter(o=>o.ok).length,v:median(xs.filter(o=>o.ok).map(o=>o[metric]))};}).filter(p=>p.n)}));
 const max=Math.max(...rows.flatMap(r=>r.per.map(p=>Number.isFinite(p.v)?p.v:0)),1e-3);
 for(const r of rows){const row=document.createElement('div');row.className='task';const n=document.createElement('div');n.className='tname';n.textContent=r.t;n.title=r.t;const bars=document.createElement('div');bars.className='bars';
  for(const p of r.per){const br=document.createElement('div');br.className='barrow';const tr=document.createElement('div');tr.className='track';const bar=document.createElement('div');bar.className='bar';bar.style.background=color(p.b);bar.style.width=(Number.isFinite(p.v)?100*p.v/max:0)+'%';bar.title=label(p.b)+': median '+nice(p.v)+' ms of '+p.good+' passing';tr.appendChild(bar);
   const v=document.createElement('div');v.className='val';v.innerHTML='<b>'+nice(p.v)+' ms</b> · n='+p.good;br.append(tr,v);bars.appendChild(br);}
  row.append(n,bars);el.appendChild(row);}}
function correctness(el,obs){el.innerHTML='';const ts=tasks(obs);if(!ts.length){el.innerHTML='<div class="empty">No measurements loaded.</div>';return;}
 for(const t of ts){const row=document.createElement('div');row.className='task';const n=document.createElement('div');n.className='tname';n.textContent=t;const bars=document.createElement('div');bars.className='bars';
  for(const b of backends){const xs=obs.filter(o=>o.task===t&&o.backend===b);if(!xs.length)continue;const good=xs.filter(o=>o.ok).length;const br=document.createElement('div');br.className='barrow';
   const tr=document.createElement('div');tr.className='track';const a=document.createElement('div');a.className='bar';a.style.background=color(b);a.style.width=(100*good/xs.length)+'%';tr.appendChild(a);
   if(good<xs.length){const f=document.createElement('div');f.className='bar';f.style.background='var(--critical)';f.style.width=(100*(xs.length-good)/xs.length)+'%';tr.appendChild(f);}
   const v=document.createElement('div');v.className='val';const ind=new Set(xs.map(o=>o.run_id+'/'+o.trial)).size;v.innerHTML=(good===xs.length?'<span class="ok">✓ '+good+'/'+xs.length+'</span> · ≤'+Math.round(100*(1-Math.pow(.05,1/ind)))+'% fail':'<span class="bad">✕ '+(xs.length-good)+' fail</span> · '+good+'/'+xs.length);v.title=good===xs.length?'0 failures in '+ind+' independent trials (parallel calls in one trial share a write, so they count once): 95% upper bound on the failure rate is 1 − 0.05^(1/n)':'';br.append(tr,v);bars.appendChild(br);}
  row.append(n,bars);el.appendChild(row);}}
const DOCS={'overwrite':'✓ open-for-write takes a delegation','append':'✓ open-for-write takes a delegation','create-file':'✓ create takes a delegation','mkdir':'✓ create takes a delegation','chmod':'not stated','rename':'not stated','tmp+rename':'not stated','unlink':'✕ needs a delegation on the parent','unlink-in-subdir':'✕ needs a delegation on the parent','sqlite-create':'not stated'};
const OPS=Object.keys(DOCS);
function semantics(el,obs){const probes=obs.filter(o=>o.detail&&o.detail.ops);if(!probes.length){el.innerHTML='<div class="empty">No semantics probes in these runs.</div>';return;}
 const cols=[];for(const b of backends)for(const t of ['semantics/probe-plain','semantics/probe-checkout']){const xs=probes.filter(o=>o.backend===b&&o.task===t);if(xs.length&&!(t.endsWith('checkout')&&!b.startsWith('archil')))cols.push({b,t,xs});}
 const head='<tr><th>OPERATION · ON ENTRIES AN EARLIER EXEC CREATED</th>'+cols.map(c=>'<th>'+(c.b.startsWith('archil')?'ARCHIL':'LOCAL')+(c.t.endsWith('checkout')?' + <code>archil checkout</code>':'')+'</th>').join('')+'<th>ARCHIL DOCS</th></tr>';
 const cell=xs=>{const tally={};for(const o of xs){const v=o.detail.ops[cell.op]??'missing';tally[v]=(tally[v]||0)+1;}return Object.entries(tally).map(([v,n])=>(v==='ok'?'<span class="ok">✓ works</span>':'<span class="bad">✕ '+esc(v.replace(/^OperationalError: /,''))+'</span>')+' <span class="n">'+n+'/'+xs.length+'</span>').join('<br>');};
 const co=probes.filter(o=>o.detail.checkout_ms!=null).map(o=>o.detail.checkout_ms);
 el.innerHTML='<div class="tablewrap flat"><table>'+head+OPS.map(op=>{cell.op=op;return '<tr><td><b>'+op+'</b></td>'+cols.map(c=>'<td>'+cell(c.xs)+'</td>').join('')+'<td class="docs">'+DOCS[op]+'</td></tr>';}).join('')+'</table></div>'+(co.length?'<p class="foot">Median <code>archil checkout</code> inside the exec: <b>'+nice(median(co))+' ms</b> (n='+co.length+').</p>':'');}
const esc=t=>String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function visibility(el,allObs){
 const localTrials=allObs.filter(o=>o.task==='visibility/reader'&&o.detail&&o.detail.first_seen&&!o.backend.startsWith('archil'));
 const obs=allObs.filter(o=>o.backend.startsWith('archil'));
 const readers=obs.filter(o=>o.task==='visibility/reader'&&o.detail&&o.detail.first_seen);
 const trials=readers.map(r=>{const w=obs.find(o=>o.task==='visibility/writer'&&o.run_id===r.run_id&&o.trial===r.trial&&o.backend===r.backend);return w&&w.detail?{r,w,b:r.backend,t0:r.detail.t_prime,write:w.detail.t_fsynced-r.detail.t_prime,cap:r.detail.cap_s,ok:r.ok}:null;}).filter(t=>t&&t.ok);
 // When this probe's own file was fsynced (newer runs record it per directory; older runs: the last fsync).
 const wAt=(t,pr)=>{const by=t.w.detail.t_fsynced_by_dir,dirs=t.r.detail.dirs;return (by&&dirs&&by[dirs[pr]]!=null?by[dirs[pr]]:t.w.detail.t_fsynced)-t.t0;};
 if(!trials.length){el.innerHTML='<div class="empty">No valid visibility trials in these runs.</div>';return;}
 const probes=Object.keys(trials[0].r.detail.first_seen);
 const xmax=Math.max(35,...trials.map(t=>Math.min(t.cap,Math.max(t.write,...Object.values(t.r.detail.first_seen).map(v=>v==null?t.cap:v-t.t0)))))*1.04;
 const W=980,L=230,R=24,lane=8,rowH=trials.length*lane+18,T=26,H=T+probes.length*rowH+34,x=v=>L+(W-L-R)*v/xmax;
 const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('class','vis');svg.setAttribute('role','img');svg.setAttribute('aria-label','Seconds from the reader priming its cache to first seeing each change');
 const add=(tag,at,txt)=>{const e=document.createElementNS(ns,tag);for(const k in at)e.setAttribute(k,at[k]);if(txt!=null)e.textContent=txt;svg.appendChild(e);return e;};
 const step=xmax>120?30:xmax>60?10:5;
 for(let v=0;v<=xmax;v+=step){add('line',{x1:x(v),x2:x(v),y1:T-6,y2:H-30,class:'g'});add('text',{x:x(v),y:H-12,class:'ax','text-anchor':'middle'},v+' s');}
 add('line',{x1:x(30),x2:x(30),y1:T-14,y2:H-30,class:'ref'});add('text',{x:x(30)+5,y:T-10,class:'reft'},'30 s after the reader looked');
 const delays=[...new Set(trials.map(t=>Math.round(t.write)))].sort((a,b)=>a-b);
 const shade=t=>{const i=delays.indexOf(Math.round(t.write));const ramp=['#ef9a55','#dd6a22','#a33a0b','#6e270a'];return ramp[Math.min(ramp.length-1,Math.round(i*(ramp.length-1)/Math.max(1,delays.length-1)))];};
 probes.forEach((pr,i)=>{const y0=T+i*rowH;if(i)add('line',{x1:0,x2:W,y1:y0,y2:y0,class:'sep'});add('text',{x:0,y:y0+rowH/2,class:'lab'},pr);
  trials.forEach((t,j)=>{const y=y0+9+j*lane+lane/2;const seen=t.r.detail.first_seen[pr];const c=shade(t);
   const end=seen==null?t.cap:seen-t.t0,wp=wAt(t,pr);add('line',{x1:x(wp),x2:x(end),y1:y,y2:y,stroke:c,class:'win'});add('line',{x1:x(wp),x2:x(wp),y1:y-4,y2:y+4,stroke:c,class:'tick'});
   const d=add('circle',{cx:x(end),cy:y,r:3.5,fill:seen==null?'var(--surface)':c,stroke:c,class:'dot'});
   const lag=seen==null?'not seen within '+t.cap+' s':nice(1000*(end-wp))+' ms after the write';
   d.appendChild(Object.assign(document.createElementNS(ns,'title'),{textContent:pr+' · trial '+(t.r.trial+1)+'\nwrite at +'+wp.toFixed(1)+' s · seen at +'+end.toFixed(1)+' s\n'+lag}));});});
 el.innerHTML='';el.appendChild(svg);
 const k=document.createElement('p');k.className='foot';k.innerHTML='Tick = writer’s <code>fsync</code> returned · dot = reader first saw the change · line = how long the reader saw stale state. Shade = when the writer ran (lighter = earlier). Hollow dot = not seen before the reader gave up. Clocks are two microVMs’ wall clocks; skew is negligible at this scale.';el.appendChild(k);
 if(localTrials.length){const lw=r=>allObs.find(o=>o.task==='visibility/writer'&&o.run_id===r.run_id&&o.trial===r.trial&&o.backend===r.backend);let lt=0,tot=0;for(const r of localTrials){const w=lw(r);if(!w||!w.detail)continue;for(const [pr,v] of Object.entries(r.detail.first_seen)){tot++;const by=w.detail.t_fsynced_by_dir,dirs=r.detail.dirs;const wt=by&&dirs&&by[dirs[pr]]!=null?by[dirs[pr]]:w.detail.t_fsynced;if(v!=null&&v-wt<0.1)lt++;}}const q=document.createElement('p');q.className='foot';q.innerHTML='Local control (same probes, separate processes on this Mac): '+lt+' of '+tot+' changes seen within 100 ms of the write (the reader polls every 20 ms).';el.appendChild(q);}
 const tbl=document.createElement('div');tbl.className='tablewrap flat';tbl.innerHTML='<table><tr><th>ACCESS PATH</th><th class="num">STALE FOR (MEDIAN)</th><th class="num">SEEN AT, SINCE READER LOOKED (MEDIAN)</th><th class="num">NOT SEEN</th></tr>'+probes.map(pr=>{const lag=[],at=[];let miss=0;for(const t of trials){const v=t.r.detail.first_seen[pr];if(v==null){miss++;continue;}lag.push(v-t.t0-wAt(t,pr));at.push(v-t.t0);}return '<tr><td>'+esc(pr)+'</td><td class="num">'+(lag.length?(median(lag)<0.05?'≈0 s':nice(median(lag))+' s'):'> '+Math.round(Math.min(...trials.map(t=>t.cap-t.write)))+' s')+'</td><td class="num">'+(at.length?nice(median(at))+' s':'—')+'</td><td class="num">'+miss+'/'+trials.length+'</td></tr>';}).join('')+'</table>';el.appendChild(tbl);}
function update(){const obs=selected();const passed=obs.filter(o=>o.ok);
 $('n').textContent=obs.length;$('pass').textContent=obs.length?passed.length+'/'+obs.length:'—';
 const arch=passed.filter(o=>o.backend.startsWith('archil')&&core(o));$('p50').textContent=arch.length?nice(median(arch.map(o=>o.client_wall_ms)))+' ms':'—';
 $('fresh').textContent=arch.length?nice(median(arch.filter(o=>/fresh-vm|parallel-read/.test(o.task)).map(o=>o.client_wall_ms)))+' ms':'—';
 const c=obs.filter(core);correctness($('correctness'),c);chart($('latency'),c,'client_wall_ms');chart($('work'),c,'work_ms');semantics($('semantics'),obs);visibility($('visibility'),obs);
 $('tbody').innerHTML='';for(const o of obs){const tr=document.createElement('tr');tr.title=o.error||'';
  for(const [v,c] of [[label(o.backend),''],[o.task,''],[o.trial+1,'num'],[o.fanout,'num'],[nice(o.client_wall_ms),'num'],[nice(o.remote_queue_ms),'num'],[nice(o.remote_execute_ms),'num'],[nice(o.work_ms),'num'],[o.ok?'✓ pass':'✕ fail',o.ok?'ok':'bad']]){const td=document.createElement('td');td.textContent=v;td.className=c;tr.appendChild(td);}$('tbody').appendChild(tr);}}
$('provider').addEventListener('change',update);
$('export').addEventListener('click',()=>{const rows=selected(),h=['backend','task','trial','fanout','ok','client_wall_ms','remote_queue_ms','remote_execute_ms','work_ms','error'];const cell=x=>'"'+String(x??'').replace(/"/g,'""')+'"';const csv=[h.join(','),...rows.map(o=>h.map(k=>cell(o[k])).join(','))].join('\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='branchlab-observations.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),500);});
update();
})();
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("inputs", nargs="*", type=Path)
    p.add_argument("--output", default="docs/branchlab.html", type=Path)
    args = p.parse_args()
    reports = []
    for inp in args.inputs:
        data = json.loads(inp.read_text())
        if data.get("schema_version") != 1 or not isinstance(data.get("observations"), list):
            raise SystemExit(f"Not a DiskScope schema v1 report: {inp}")
        reports.append(data)
    serialized = json.dumps(reports, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DiskScope BranchLab</title><meta name="description" content="Cross-microVM state correctness on Archil: file handoff, SQLite reopen, parallel readers and disjoint writers."><style>{STYLE}</style></head><body>
<nav class="topnav"><div class="wrap"><span class="brand">DiskScope · BranchLab</span><span><a href="index.html">← Performance lab</a> &nbsp;·&nbsp; <a href="https://github.com/ThilakShekharShriyan/diskscope">GitHub ↗</a></span></div></nav>
<header class="wrap"><div class="eyebrow">Phase 2 · State correctness</div><h1>Does state survive the hop to a fresh microVM? <span class="soft">Checked byte for byte.</span></h1>
<p class="lede">Each step runs in its own execution: one writes, another verifies, byte for byte. Then two sharper questions: what can a second exec <em>change</em> in files the first one made, and when does a reader that's already running <em>see</em> a change?</p>
<div class="tiles"><div class="tile"><small>Steps recorded</small><strong id="n">—</strong></div><div class="tile"><small>Steps passed</small><strong id="pass">—</strong></div><div class="tile"><small>Archil step, median</small><strong id="p50">—</strong></div><div class="tile"><small>Archil fresh-VM verify, median</small><strong id="fresh">—</strong></div></div></header>
<div class="wrap"><div class="controls"><label>Environment <select id="provider"><option value="all">All</option></select></label><button id="export">Export CSV</button><span class="legend" id="legend"></span></div>
<section><div class="ph"><div class="eyebrow">01 · Correctness</div><div><h2>Did each step see the right data?</h2><p>Passed / attempted per step. Readers compare every byte against the deterministic payload; SQLite runs <code>integrity_check</code> and checks row count and sum.</p></div></div><div class="panel" id="correctness"></div></section>
<section><div class="ph"><div class="eyebrow">02 · Semantics</div><div><h2>What can a second exec change?</h2><p>One exec creates files and folders; a fresh exec then tries ten everyday operations on them — once as-is, once after <code>archil checkout</code> of the folder. Archil's default exec mount is shared mode, where deleting needs a delegation on the parent.</p></div></div><div class="panel" id="semantics"></div></section>
<section><div class="ph"><div class="eyebrow">03 · Visibility</div><div><h2>When does a long-lived reader see a change?</h2><p>A reader exec primes its caches, then polls; a separate exec creates and overwrites files. Each access path has its own directory, so probes can't refresh each other. Archil's guide says a new file takes <a href="https://archil.com/guides/storage-for-agents/what-archil-does">about 30 s</a> to appear on another machine; this shows what that window is measured from.</p></div></div><div class="panel" id="visibility"></div></section>
<section><div class="ph"><div class="eyebrow">04 · End-to-end</div><div><h2>Time per step</h2><p>Median client round-trip of passing steps. On Archil this includes the API, a microVM from the warm pool and the commit at exit; locally, starting Python.</p></div></div><div class="panel" id="latency"></div></section>
<section><div class="ph"><div class="eyebrow">05 · Guest work</div><div><h2>Time inside the step</h2><p>Median time the guest measured for its own filesystem work — the comparable number across environments.</p></div></div><div class="panel" id="work"></div></section>
<section><div class="ph"><div class="eyebrow">06 · Raw evidence</div><div><h2>Every step</h2><p>Hover a row for its error text, if any.</p></div></div><div class="tablewrap"><table><thead><tr><th>ENVIRONMENT</th><th>STEP</th><th class="num">TRIAL</th><th class="num">FAN-OUT</th><th class="num">END-TO-END</th><th class="num">QUEUE</th><th class="num">EXECUTE</th><th class="num">WORK</th><th>RESULT</th></tr></thead><tbody id="tbody"></tbody></table></div></section>
<section><div class="ph"><div class="eyebrow">07 · Limits</div><div><h2>What this does and doesn't show</h2></div></div><div class="notes">
<p>Steps 01, 04 and 05 use a <strong>fresh microVM per step</strong>, whose caches start empty — fresh-execution visibility. Section 03 is the long-lived-reader case, with its own caveats: n = 6, one region, wall clocks of two microVMs.</p>
<p>Archil commits a writing exec at exit (~70 ms), so a fresh-VM read after <code>write-fsync</code> can't separate <code>fsync</code> durability from that commit. No host failure is injected.</p>
<p>Correctness steps use <code>journal_mode=TRUNCATE</code> and clean up with a <code>conditional</code> mount, because of what section 02 shows. Full repros: <a href="https://github.com/ThilakShekharShriyan/diskscope/blob/main/ARCHIL-FINDINGS.md">ARCHIL-FINDINGS.md</a>.</p>
<p>“≤31% fail” is the 95% upper bound on the failure rate after 0 failures in n independent trials (1 − 0.05^(1/n); 31% for n = 8). Parallel calls within one trial count once. Passing every trial is evidence, not proof. Writers never share a file. No disks, branches or checkpoints are created.</p></div></section></div>
<footer class="wrap">DiskScope BranchLab · generated from raw JSON by <code>branchlab/report.py</code> · independent; not affiliated with Archil</footer>
<script type="application/json" id="branchlab-payload">{serialized}</script><script>{JS}</script></body></html>"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page)
    print(f"Wrote {args.output} from {len(reports)} report(s)")


if __name__ == "__main__":
    main()
