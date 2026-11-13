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
.barrow{display:grid;grid-template-columns:1fr 112px;gap:12px;align-items:center;font-size:12px}
.track{height:12px;display:flex;gap:2px}.bar{height:100%;border-radius:0 3px 3px 0;min-width:2px}
.val{font-variant-numeric:tabular-nums;text-align:right;color:var(--ink-2);white-space:nowrap}.val b{color:var(--ink);font-weight:600}
.ok{color:var(--good);font-weight:500}.bad{color:var(--critical);font-weight:500}
.tablewrap{overflow:auto;max-height:520px;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}th{position:sticky;top:0;background:var(--surface);text-align:left;font-size:11px;letter-spacing:.06em;color:var(--muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--grid)}
td{padding:7px 12px;border-bottom:1px solid var(--grid);white-space:nowrap;color:var(--ink-2)}td.num,th.num{text-align:right}tr:hover td{background:var(--wash)}
.notes{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 32px;border-top:1px solid var(--ink);padding-top:16px;font-size:13.5px;color:var(--ink-2)}.notes p{margin:0 0 8px}
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
const steps=['handoff/init','handoff/write-fsync','handoff/read-fresh-vm','handoff/cleanup','sqlite/init','sqlite/commit-16','sqlite/reopen-fresh-vm','sqlite/cleanup','readfan/init','readfan/prepare','readfan/parallel-read','readfan/cleanup','writers/init','writers/disjoint-path','writers/reconcile','writers/cleanup'];
const rank=t=>{const i=steps.indexOf(t);return i<0?999:i;};
function selected(){const b=$('provider').value;return all.filter(o=>b==='all'||o.backend===b);}
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
   const v=document.createElement('div');v.className='val';v.innerHTML=(good===xs.length?'<span class="ok">✓ pass</span>':'<span class="bad">✕ '+(xs.length-good)+' fail</span>')+' · '+good+'/'+xs.length;br.append(tr,v);bars.appendChild(br);}
  row.append(n,bars);el.appendChild(row);}}
function update(){const obs=selected();const passed=obs.filter(o=>o.ok);
 $('n').textContent=obs.length;$('pass').textContent=obs.length?passed.length+'/'+obs.length:'—';
 const arch=passed.filter(o=>o.backend.startsWith('archil'));$('p50').textContent=arch.length?nice(median(arch.map(o=>o.client_wall_ms)))+' ms':'—';
 $('fresh').textContent=arch.length?nice(median(arch.filter(o=>/fresh-vm|parallel-read/.test(o.task)).map(o=>o.client_wall_ms)))+' ms':'—';
 correctness($('correctness'),obs);chart($('latency'),obs,'client_wall_ms');chart($('work'),obs,'work_ms');
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
<p class="lede">Each step runs in its own execution: one writes, another verifies. File handoff after <code>fsync</code>, a SQLite database reopened read-only, parallel readers of one file, and parallel writers to separate files — every result compared against the expected bytes.</p>
<div class="tiles"><div class="tile"><small>Steps recorded</small><strong id="n">—</strong></div><div class="tile"><small>Steps passed</small><strong id="pass">—</strong></div><div class="tile"><small>Archil step, median</small><strong id="p50">—</strong></div><div class="tile"><small>Archil fresh-VM verify, median</small><strong id="fresh">—</strong></div></div></header>
<div class="wrap"><div class="controls"><label>Environment <select id="provider"><option value="all">All</option></select></label><button id="export">Export CSV</button><span class="legend" id="legend"></span></div>
<section><div class="ph"><div class="eyebrow">01 · Correctness</div><div><h2>Did each step see the right data?</h2><p>Passed / attempted per step. Readers compare every byte against the deterministic payload; SQLite runs <code>integrity_check</code> and checks row count and sum.</p></div></div><div class="panel" id="correctness"></div></section>
<section><div class="ph"><div class="eyebrow">02 · End-to-end</div><div><h2>Time per step</h2><p>Median client round-trip of passing steps. On Archil this includes the API, a microVM from the warm pool and the commit at exit; locally, starting Python.</p></div></div><div class="panel" id="latency"></div></section>
<section><div class="ph"><div class="eyebrow">03 · Guest work</div><div><h2>Time inside the step</h2><p>Median time the guest measured for its own filesystem work — the comparable number across environments.</p></div></div><div class="panel" id="work"></div></section>
<section><div class="ph"><div class="eyebrow">04 · Raw evidence</div><div><h2>Every step</h2><p>Hover a row for its error text, if any.</p></div></div><div class="tablewrap"><table><thead><tr><th>ENVIRONMENT</th><th>STEP</th><th class="num">TRIAL</th><th class="num">FAN-OUT</th><th class="num">END-TO-END</th><th class="num">QUEUE</th><th class="num">EXECUTE</th><th class="num">WORK</th><th>RESULT</th></tr></thead><tbody id="tbody"></tbody></table></div></section>
<section><div class="ph"><div class="eyebrow">05 · Limits</div><div><h2>What this does and doesn't show</h2></div></div><div class="notes">
<p>A fresh microVM reading a file is <strong>fresh-execution visibility</strong>, not the directory-cache behaviour of a long-lived mounted client; that is a separate experiment.</p>
<p>Archil commits a writing exec at exit (~70 ms), so a fresh-VM read after <code>write-fsync</code> can't separate <code>fsync</code> durability from that commit. No host failure is injected.</p>
<p>SQLite uses <code>journal_mode=TRUNCATE</code>: the default <code>DELETE</code> and <code>WAL</code> fail across execs on Archil (<a href="https://github.com/ThilakShekharShriyan/diskscope/blob/main/ARCHIL-FINDINGS.md">finding #2</a>). Cleanup uses a <code>conditional</code> mount for the same reason.</p>
<p>Writers never share a file; this is single-writer-per-path. Small n — a smoke-level baseline, not a statistical result. No disks, branches or checkpoints are created.</p></div></section></div>
<footer class="wrap">DiskScope BranchLab · generated from raw JSON by <code>branchlab/report.py</code> · independent; not affiliated with Archil</footer>
<script type="application/json" id="branchlab-payload">{serialized}</script><script>{JS}</script></body></html>"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page)
    print(f"Wrote {args.output} from {len(reports)} report(s)")


if __name__ == "__main__":
    main()
