"""dashboard_ui_thin responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from pathlib import Path
import json
import dashboard_config_thin as dashboard_config

STYLE = """
:root{
  --bg:#f3f4f8; --surface:#ffffff; --surface-2:#f0f2f8; --line:#e2e5ee;
  --text:#1f2430; --dim:#667085; --faint:#98a0b3;
  --ok:#0e9f6e; --warn:#d97706; --bad:#e02424; --accent:#3b6fe0; --accent-2:#0ea5e9;
}
*{box-sizing:border-box}
html{color-scheme:light}
body{margin:0;background:
  radial-gradient(1200px 500px at 80% -10%, #d7e2f766, transparent),
  radial-gradient(900px 400px at 0% -10%, #e6dcf266, transparent),
  var(--bg);
  color:var(--text);font:14px/1.55 -apple-system,"SF Pro SC","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.num,.stat b{font-variant-numeric:tabular-nums}
header{position:sticky;top:0;z-index:10;backdrop-filter:blur(12px);
  background:#f3f4f8d9;border-bottom:1px solid var(--line);
  padding:14px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.02em}
nav{display:flex;gap:2px;background:var(--surface-2);border-radius:9px;padding:2px}
nav a{color:var(--dim);text-decoration:none;font-size:12.5px;padding:3px 12px;border-radius:7px;font-weight:600}
nav a.on{color:var(--text);background:var(--surface);box-shadow:0 1px 2px #1f243012}
.health{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;
  padding:3px 12px;border-radius:99px;border:1px solid var(--line);background:var(--surface)}
#stamp{margin-left:auto;color:var(--dim);font-size:12px}
#stamp.err{color:var(--bad)}
main{padding:20px 24px 40px;display:grid;gap:16px;max-width:1180px;margin:0 auto}
.card{background:var(--surface);
  border:1px solid var(--line);border-radius:14px;padding:16px 18px;
  box-shadow:0 1px 2px #1f24300a, 0 8px 24px #1f243008}
.label{font-size:11px;letter-spacing:.12em;color:var(--dim);text-transform:uppercase;margin-bottom:10px;font-weight:600}

/* status dot with glow */
.dot{display:inline-block;width:8px;height:8px;border-radius:99px;flex:none}
.dot.ok{background:var(--ok);box-shadow:0 0 6px #0e9f6e59}
.dot.warn{background:var(--warn);box-shadow:0 0 6px #d9770659}
.dot.bad{background:var(--bad);box-shadow:0 0 6px #e0242459}
.dot.idle{background:#b7bdc9;box-shadow:none}
.health.ok{color:var(--ok)}.health.warn{color:var(--warn)}.health.bad{color:var(--bad)}

/* hero stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--surface);border:1px solid var(--line);
  border-radius:14px;padding:14px 16px;box-shadow:0 1px 2px #1f24300a}
.stat .k{font-size:11.5px;color:var(--dim);letter-spacing:.06em;margin-bottom:4px}
.stat b{font-size:22px;font-weight:680;letter-spacing:-.01em}
.stat .u{font-size:12px;color:var(--dim);font-weight:500;margin-left:2px}
.stat .sub{font-size:11.5px;color:var(--dim);margin-top:2px}

/* novel cards */
.ncard{display:grid;gap:12px}
.nrow{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.nname{font-size:16px;font-weight:650;display:flex;align-items:center;gap:9px}
.pill{font-size:11.5px;font-weight:600;padding:2px 10px;border-radius:99px;border:1px solid var(--line);background:var(--surface-2);color:var(--dim)}
.pill.ok{color:var(--ok);border-color:#0e9f6e40}
.pill.warn{color:var(--warn);border-color:#d9770640}
.pill.bad{color:var(--bad);border-color:#e0242440}
.neta{font-size:13px;color:var(--dim)}
.neta b{color:var(--text);font-weight:650}
.nbody{display:grid;grid-template-columns:1fr 240px;gap:18px;align-items:end}
.track{height:7px;background:#e7eaf2;border-radius:99px;overflow:hidden;display:flex;margin:10px 0 8px}
.fill{background:linear-gradient(90deg,var(--accent),var(--accent-2));height:100%}
.fill.plan{background:#c6cddd;height:100%}
.nmeta{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--dim);font-size:12.5px}
.spark-label{font-size:11px;color:var(--dim);text-align:right;margin-top:4px}
.tick{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);
  word-break:break-all;border-top:1px dashed var(--line);padding-top:8px}
details{border-top:1px dashed var(--line);padding-top:6px}
summary{cursor:pointer;color:var(--dim);font-size:12.5px;list-style:none;user-select:none}
summary::before{content:"▸ ";font-size:10px}
details[open] summary::before{content:"▾ "}
summary:hover{color:var(--text)}

/* attention */
.attn-item{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.attn-item:last-child{border-bottom:0}
.attn-item .when{color:var(--dim);font-size:12px;margin-left:auto;flex:none}
.attn-raw{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim);
  word-break:break-all;margin:2px 0 6px 18px}
.all-clear{color:var(--ok);font-size:13px}

/* tables */
.twrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.06em;text-align:left;
  padding:6px 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid #eef0f6;white-space:nowrap}
td.rng{max-width:280px;overflow:hidden;text-overflow:ellipsis;cursor:default}
tbody tr{transition:background .15s}
tbody tr:hover{background:#1f243005}
tr:last-child td{border-bottom:0}
.dim{color:var(--dim)}.warn-t{color:var(--warn)}.ok-t{color:var(--ok)}
.pills{display:flex;gap:8px;flex-wrap:wrap}

/* tooltip + charts */
#tip{display:none;position:fixed;z-index:50;pointer-events:none;background:#1f2430;color:#f2f4f8;
  font-size:12px;padding:4px 10px;border-radius:8px;box-shadow:0 4px 16px #1f243040;white-space:nowrap}
#tip.long{white-space:normal;max-width:70vw;word-break:break-all}
.sparksvg{width:100%;display:block;overflow:visible}
.sparksvg .sb{fill:url(#sbg)}
.sparksvg .sb:hover{stroke:var(--accent);stroke-width:1.2}
.sparksvg .cur{fill:var(--accent-2)}
.sparksvg .avg{stroke:var(--dim);stroke-width:1;stroke-dasharray:3 3;opacity:.55}
.burnsvg{width:100%;height:auto;display:block;overflow:visible}
.burnsvg .area{fill:url(#areag)}
.burnsvg .bline{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}
.burnsvg .scope{stroke:var(--faint);stroke-width:1;stroke-dasharray:5 4}
.burnsvg .scope.p{stroke:var(--warn)}
.burnsvg .proj{stroke:var(--accent);stroke-width:1.6;stroke-dasharray:2 3;opacity:.8}
.burnsvg .projdot{fill:var(--accent)}
.burnsvg .slab,.burnsvg .plab{font-size:10px;fill:var(--dim)}
.burnsvg .plab{fill:var(--accent);font-weight:600}
.burnsvg .xlab{font-size:10px;fill:var(--faint)}
.donutwrap{display:flex;align-items:center;gap:14px}
.ring{fill:none;stroke-width:10}
.ring.base{stroke:var(--surface-2)}
.ring.p{stroke:var(--ok)}.ring.m{stroke:var(--warn)}.ring.f{stroke:var(--bad)}
.dnum{font-size:15px;font-weight:700;fill:var(--text)}
.dlab{font-size:9px;fill:var(--dim)}
.dleg{display:grid;gap:4px;font-size:12px;color:var(--dim)}
.dleg .dot{margin-right:6px}
.catrow{display:flex;align-items:center;gap:10px;padding:3px 0;font-size:12.5px}
.catname{width:64px;color:var(--dim);flex:none}
.catbar{flex:1;height:6px;background:var(--surface-2);border-radius:99px;overflow:hidden}
.catbar i{display:block;height:100%;background:linear-gradient(90deg,var(--warn),var(--bad));border-radius:99px}
.catn{width:34px;text-align:right;color:var(--dim)}
.board2{display:grid;grid-template-columns:340px 1fr;gap:22px;margin-top:6px}

@media (max-width:820px){
  main{padding:14px 12px 32px}
  header{padding:12px 14px}
  .nbody{grid-template-columns:1fr}
  .board2{grid-template-columns:1fr}
  table.resp thead{display:none}
  table.resp, table.resp tbody, table.resp tr, table.resp td{display:block;width:100%}
  table.resp tr{border:1px solid var(--line);border-radius:10px;margin-bottom:8px;padding:6px 12px}
  table.resp td{border-bottom:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px;white-space:normal}
  table.resp td::before{content:attr(data-l);color:var(--dim);font-size:12px;flex:none}
}
"""


DEFS = """<svg width="0" height="0" style="position:absolute"><defs>
<linearGradient id="sbg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#3b6fe0"/><stop offset="1" stop-color="#3b6fe0" stop-opacity=".3"/>
</linearGradient>
<linearGradient id="areag" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#3b6fe0" stop-opacity=".22"/><stop offset="1" stop-color="#3b6fe0" stop-opacity="0"/>
</linearGradient></defs></svg>"""


TIP_JS = """const tipEl=document.getElementById("tip");
document.addEventListener("mousemove",e=>{
  const t=e.target.closest&&e.target.closest("[data-tip]");
  if(!t){tipEl.style.display="none";return;}
  tipEl.textContent=t.dataset.tip;
  tipEl.className=t.dataset.tip.length>80?"long":"";
  tipEl.style.display="block";
  const w=tipEl.offsetWidth,h=tipEl.offsetHeight;
  let x=e.clientX+12,y=e.clientY-h-10;
  if(x+w>innerWidth-8)x=Math.max(8,e.clientX-w-12);
  if(y<8)y=e.clientY+14;
  tipEl.style.left=x+"px";tipEl.style.top=y+"px";
});"""


STATE_JS = """function deliverySummary(n){
  const d = n.delivery;
  if (!d) return `<div class="nmeta"><span class="dim">交付门槛未计算（审查批次后由 delivery_gate_thin.py 写 delivery.json）</span></div>`;
  return `<div class="nmeta"><span>可交付 <b class="num ${d.deliverable===d.total?'ok-t':'warn-t'}">${d.deliverable}</b> / ${d.total} · 技术挡 ${d.tech_blocked} · 审查挡 ${d.review_blocked}（${d.must_fix_clips} 段须重拍）</span>
    <span class="dim">剧本影子门 ${d.script_flagged} 集（不阻断）· 算于 ${d.generated_at}</span></div>`;
}
function stateSummary(n){
  if(n.pipeline) return pipelineSummary(n);
  const s = n.states || {};
  return deliverySummary(n) + `<div class="nmeta"><span>质检合格 <b class="num ok-t">${n.done}</b> · 视频文件 ${n.files} · 已规划 ${n.planned}</span>
    <span>未过质检 ${s.done_with_warnings||0} · 待重做 ${s.stale||0} · 生成失败 ${s.clips_failed||0}</span></div>
    <div class="nmeta"><span>待审查 ${n.review_pending} · 审查执行异常 ${n.review_errors}</span>
    <span class="${n.blocked?'warn-t':'dim'}">需处理 ${n.blocked} 集${n.uncertain ? `（提交结果不明 ${n.uncertain} 集）` : ''}</span></div>`;
}
function episodeAttention(n){
  const rows = n.attention || [];
  if (!rows.length) return '';
  const groups = new Map();
  for (const r of rows){
    if (!groups.has(r.reason)) groups.set(r.reason, []);
    groups.get(r.reason).push(r.chapter);
  }
  return `<details><summary>待处理章节（${rows.length} 集）</summary>` +
    [...groups].map(([reason, chapters])=>`<div class="dim" style="margin-top:8px;overflow-wrap:anywhere">${reason} · ${chapters.length} 集：<span class="num">${chapters.join('、')}</span></div>`).join('') + '</details>';
}
"""


STATE_JS = 'const DASHBOARD_VERSION='+json.dumps(dashboard_config.UI_VERSION)+';\n'+STATE_JS


STATE_JS += (Path(__file__).with_name('pipeline_dashboard.js')).read_text(encoding='utf-8')


STYLE += (Path(__file__).with_name('pipeline_dashboard.css')).read_text(encoding='utf-8')


def _page(active: str, header_extra: str, body: str) -> str:
    nav = ""
    for name, href in (("实时", "/"), ("看板", "/board")):
        nav += '<a href="%s" class="%s">%s</a>' % (href, "on" if name == active else "", name)
    return ("<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>小说成片进度</title><style>" + STYLE + "</style></head><body>"
            "<header><h1>小说成片进度</h1><nav>" + nav + "</nav>" + header_extra + "</header>"
            "<script>" + STATE_JS + "</script><main>" + body + "</main>" + DEFS + "<div id=\"tip\"></div>"
            "<script>" + TIP_JS + "</script></body></html>")


LIVE_BODY = """
<div class="stats" id="stats"></div>
<div id="novels" style="display:grid;gap:16px"></div>
<div class="card" id="attention-card"><div class="label">需要关注</div><div id="attention"></div></div>
<div class="card"><div class="label">运行明细 · 通道</div><div class="twrap"><table class="resp" id="lanes"></table></div></div>
<div class="card"><div class="label">运行明细 · 单集任务</div><div class="twrap"><table class="resp" id="workers"></table></div></div>
<div class="card" id="local-card" style="display:none"><div class="label">本地 H3 生成资源</div>
  <div class="twrap"><table class="resp" id="local"></table></div></div>
<div class="card"><div class="label">进程与在途 <span id="runtime-stamp" class="dim"></span></div><div class="pills" id="procs"></div>
  <div class="dim" style="margin-top:8px">进程数按当前运行入口统计；请求在途包含等待生成资源的任务。</div>
  <div class="twrap" style="margin-top:12px"><table class="resp" id="inflight"></table></div></div>
<script>
const $ = id => document.getElementById(id);
const pct = (a,b) => b ? Math.min(100, a*100/b) : 0;
const fmtETA = h => h == null ? "—" : (h < 1 ? Math.round(h*60)+" 分钟" : h < 48 ? h+" 小时" : (h/24).toFixed(1)+" 天");
const fmtAgo = s => s == null ? "—" : (s < 90 ? s+" 秒前" : s < 5400 ? Math.round(s/60)+" 分钟前" : (s/3600).toFixed(1)+" 小时前");
const laneHealth = l => l.age == null ? "warn" : l.age < 1200 ? "ok" : l.age < 3600 ? "warn" : "bad";
const workerHealth = w => { const s = w.idle != null ? w.idle : w.elapsed; return s < 1200 ? "ok" : s < 2400 ? "warn" : "bad"; };
const HEALTH_TEXT = {ok:"运行正常", warn:"有待处理任务", bad:"有执行异常"};
const WORST = {ok:0, warn:1, bad:2};

function spark(bars){
  const W=260, H=46, n=bars.length, gap=1.6;
  const max=Math.max(...bars,1);
  const bw=(W-gap*(n-1))/n;
  const total=bars.reduce((a,b)=>a+b,0), avg=total/n;
  const hour0=new Date(); hour0.setMinutes(0,0,0);
  const rects=bars.map((v,i)=>{
    const h=Math.max(2, v/max*(H-6));
    const s=new Date(hour0.getTime()-(n-1-i)*3600000);
    const when=i===n-1 ? "当前小时" : `${s.getMonth()+1}-${s.getDate()} ${s.getHours()}:00–${s.getHours()+1}:00`;
    return `<rect data-tip="${when} · ${v} 集" x="${(i*(bw+gap)).toFixed(2)}" y="${(H-h).toFixed(2)}" width="${bw.toFixed(2)}" height="${h.toFixed(2)}" rx="1.6" class="sb${i===n-1?" cur":""}"${v?"":' style="opacity:.18"'}></rect>`;
  }).join("");
  const avgY=(H-Math.max(2, avg/max*(H-6))).toFixed(2);
  return `<div><svg class="sparksvg" style="height:46px" viewBox="0 0 ${W} ${H}">` +
    (total?`<line class="avg" x1="0" x2="${W}" y1="${avgY}" y2="${avgY}"></line>`:"") + rects +
    `</svg><div class="spark-label">近 24 小时 · 共 ${total} 集 · 均值 ${avg.toFixed(1)}/时</div></div>`;
}

function novelCard(d, n){
  if(n.pipeline)return pipelineCurrentCard(n);
  if(n.history_loading)return `<div class="card"><b>${pipelineEscape(n.title)}</b><div class="dim">历史记录正在后台读取。</div></div>`;
  const lanes = d.lanes.filter(l => l.novel === n.title);
  const workers = d.workers.filter(w => w.novel === n.title);
  const pools = d.inflight.filter(i => i.novel === n.title);
  const health = n.review_errors ? "bad" : (n.blocked || n.attention.length) ? "warn"
    : lanes.length ? lanes.map(laneHealth).reduce((a,b)=>WORST[a]>WORST[b]?a:b) : (n.done ? "ok" : "warn");
  const last = n.last_final ? new Date(n.last_final*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}) : "—";
  const detailRows = (lanes.length + workers.length)
    ? `<table style="margin-top:8px"><tbody>` +
      lanes.map(l=>`<tr><td class="dim">${l.stage}通道</td><td class="num rng" data-tip="${l.range_full}">${l.range}</td><td>${l.mode}</td><td class="dim">${l.model||"—"}</td><td class="num">本轮 ${l.done} · 剩 ${l.total-l.covered}</td><td class="${l.age>1800?"warn-t":"dim"}">${fmtAgo(l.age)}</td></tr>`).join("") +
      workers.map(w=>`<tr><td class="dim">${w.kind}</td><td class="num">${w.what}</td><td colspan="2" class="dim">${w.detail}</td><td></td><td class="${w.elapsed>1800?"warn-t":"dim"}">已跑 ${fmtAgo(w.elapsed).replace("前","")}</td></tr>`).join("") +
      `</tbody></table>` : `<div class="dim" style="margin-top:8px;font-size:12.5px">这本书当前没有在跑的任务</div>`;
  return `<details class="card ncard pipeline-legacy" data-panel="${pipelineEscape(n.id)}-legacy"><summary>${pipelineEscape(n.title)} · 历史制作记录</summary><div class="pipeline-note">尚未接入当前产线或本轮精判，以下旧统计不参与当前交付汇总。</div><div>
    <div class="nrow">
      <span class="nname"><i class="dot ${health}"></i>${n.title}<span class="pill ${health}">${health==="ok"?"正常":health==="warn"?"待关注":"需处理"}</span></span>
      <span class="neta">${n.pipeline ? `待交付 <b>${n.pipeline.remaining}</b> 集 · 按下方当前成片结果统计` : `待技术合格 <b>${n.left}</b> 集 · ${n.blocked ? "有待处理章节，暂无总完成时间" : `约 <b>${fmtETA(n.eta_hours)}</b>`} · 最近技术合格 ${last}`}</span>
    </div>
    ${n.pipeline ? stateSummary(n) : ''}
    <div class="nbody">
      <div>
        ${n.pipeline ? '' : stateSummary(n)}
        <div class="nmeta"><span>全书 ${n.chapters} 章</span>
          <span>今日技术合格合成 ${n.today} · 近一小时 ${n.per_hour} 集 · 含重合成</span></div>
        <div class="track"><div class="fill" style="width:${pct(n.pipeline?n.pipeline.deliverable:n.done,n.chapters)}%"></div>
          <div class="fill plan" style="width:${pct(n.planned-(n.pipeline?n.pipeline.deliverable:n.done),n.chapters)}%"></div></div>
        <div class="nmeta"><span>30 秒片段计划 ${n.modes["30"]} 集 · 15 秒片段计划 ${n.modes["15"]} 集</span>
          <span>${pools.map(p=>`${p.pool} ${p.slots}/${p.limit}`).join(" · ")||"无在途通道"}</span></div>
      </div>
      ${spark(n.spark)}
    </div>
    ${n.tick ? `<div class="tick">tick: ${n.tick}</div>` : ""}
    ${episodeAttention(n)}
    <details><summary>这本书的运行明细（${lanes.length + workers.length} 个在跑）</summary>${detailRows}</details>
    </div></details>`;
}

function attention(d){
  const items = [];
  for (const n of d.novels) if (n.attention.length)
    items.push({level: n.review_errors ? "bad" : "warn", ts: null,
      html: `${n.title} · ${n.attention.length} 集待处理，其中 ${n.blocked} 集需人工处理 · 展开小说卡片查看章节与原因`});
  for (const l of d.lanes) if (l.age != null && l.age > 3600)
    items.push({level: l.age > 7200 ? "bad" : "warn", ts: Date.now()/1000 - l.age,
      html: `${l.novel} · ${l.stage}通道 <span class="num">${l.range}</span> — ${fmtAgo(l.age)}无产出（最后在 ${l.current||"?"} 章）`});
  for (const w of d.workers) if ((w.idle != null ? w.idle : w.elapsed) > 1800)
    items.push({level: (w.idle != null ? w.idle : w.elapsed) > 3600 ? "bad" : "warn", ts: null,
      html: `${w.novel} · ${w.kind} ${w.what} 已 ${fmtAgo(w.idle != null ? w.idle : w.elapsed).replace("前","")}无产出（开跑 ${fmtAgo(w.elapsed).replace("前","")}）`});
  for (const w of d.warnings)
    items.push({level: w.level, ts: w.ts, html: `${w.novel} · ${w.kind}`, raw: w.text});
  if (!items.length) return `<div class="all-clear">✓ 没有需要关注的情况</div>`;
  items.sort((a,b)=>WORST[b.level]-WORST[a.level] || (b.ts||0)-(a.ts||0));
  return items.map(i=>{
    const when = i.ts ? fmtAgo(Math.max(0, Date.now()/1000 - i.ts)) : "";
    return `<div class="attn-item"><i class="dot ${i.level}"></i><span>${i.html}</span><span class="when">${when}</span></div>` +
      (i.raw ? `<div class="attn-raw">${i.raw}</div>` : "");
  }).join("");
}

function laneRow(l){
  const h = laneHealth(l);
  return `<tr>
    <td data-l="状态"><i class="dot ${h}"></i></td>
    <td data-l="任务">${l.stage} · ${l.novel}</td>
    <td data-l="章节" class="num rng" data-tip="${l.range_full}">${l.range}${l.current?` · 在 ${l.current}`:""}</td>
    <td data-l="档位">${l.mode} <span class="dim">${l.model||""}</span></td>
    <td data-l="进度" class="num">本轮 ${l.done} · 剩 ${l.total-l.covered}</td>
    <td data-l="速度" class="num">${l.rate==null?"—":l.rate+"/时"}${l.eta_hours!=null?` · ${fmtETA(l.eta_hours)}`:""}</td>
    <td data-l="更新" class="${h==="ok"?"dim":"warn-t"}">${fmtAgo(l.age)}</td></tr>`;
}

function tickRuntime(){
  fetch('runtime.json',{cache:'no-store'}).then(r=>r.json()).then(d=>{
    $("procs").innerHTML = Object.entries(d.processes||{})
      .map(([k,v])=>`<span class="pill">${({runners:"渲染",planners:"规划",preparations:"开拍准备",repairs:"修复准备",cards:"角色卡",reviews:"审查",conductors:"调度器",uploads:"上传"})[k]||k} <b class="num">${v}</b></span>`).join("");
    $("inflight").innerHTML = `<thead><tr><th>小说</th><th>通道</th><th>在途/上限</th></tr></thead><tbody>` +
      (d.inflight||[]).map(i=>`<tr><td data-l="小说">${i.novel}</td><td data-l="通道">${i.pool}</td>
        <td data-l="在途" class="num">${i.slots} / ${i.limit}</td></tr>`).join("") + `</tbody>`;
    $('runtime-stamp').textContent=d.now;
  }).catch(()=>{ $('runtime-stamp').textContent='更新失败，保留上次采样'; });
}
tickRuntime(); setInterval(tickRuntime,15000);

function tick(){
  fetch("status.json",{cache:"no-store"}).then(r=>r.json()).then(d=>{
    if(d.ui_version&&d.ui_version!==DASHBOARD_VERSION){window.location.reload();return;}
    const current=d.novels.filter(n=>n.pipeline);
    const nowSec = Date.now()/1000;
    const states = [
      ...d.lanes.map(laneHealth), ...d.workers.map(workerHealth),
      ...d.novels.filter(n=>n.attention.length).map(n=>n.review_errors ? "bad" : "warn"),
      // only fresh warnings say something about right now; a 429 from hours ago doesn't
      ...d.warnings.filter(w=>w.ts && nowSec - w.ts < 7200).map(w=>w.level),
      ...(d.lanes.length||d.workers.length ? [] : ["warn"]),
    ];
    const overall = (current.length?current.map(pipelineHealth):states).reduce((a,b)=>WORST[a]>WORST[b]?a:b, "ok");
    $("health").className = "health " + overall;
    $("health").innerHTML = `<i class="dot ${overall}"></i>${HEALTH_TEXT[overall]}`;
    $("stats").innerHTML = pipelineOverview(d.novels);
    pipelineRender('novels',d.novels.map(n=>novelCard(d,n)).join(''));
    $("attention").innerHTML = current.length?pipelineAlerts(d.novels):attention(d);
    $("lanes").innerHTML = `<thead><tr><th></th><th>任务</th><th>章节</th><th>档位</th><th>进度</th><th>速度</th><th>更新</th></tr></thead><tbody>` +
      (d.lanes.length ? d.lanes.map(laneRow).join("") : `<tr><td class="dim">没有在跑的通道</td></tr>`) + `</tbody>`;
    $("workers").innerHTML = `<thead><tr><th>类型</th><th>小说</th><th>对象</th><th>进度</th><th>已跑</th></tr></thead><tbody>` +
      (d.workers.length ? d.workers.map(w=>`<tr>
        <td data-l="类型">${w.kind}</td><td data-l="小说">${w.novel}</td><td data-l="对象" class="num">${w.what}</td>
        <td data-l="进度" class="dim">${w.detail}</td>
        <td data-l="已跑" class="${w.elapsed>1800?"warn-t":"dim"}">${fmtAgo(w.elapsed).replace("前","")}</td></tr>`).join("")
        : `<tr><td class="dim">暂时没有</td></tr>`) + `</tbody>`;
    const L = d.local || [];
    $("local-card").style.display = L.length ? "" : "none";
    if (L.length) {
      const secs = L.reduce((a,l)=>a+l.seconds_hour,0), made = L.reduce((a,l)=>a+l.done_hour,0);
      $("local").innerHTML = `<thead><tr><th>机器与显卡</th><th>实例</th><th>小说</th><th>状态</th><th>待处理</th>` +
        `<th>近一小时片段</th><th>近一小时视频秒数</th><th>平均每段耗时</th></tr></thead><tbody>` +
        L.map(l=>`<tr><td data-l="机器与显卡"><b>${l.where || "—"}</b></td>
          <td data-l="实例" class="dim">${l.name}</td><td data-l="小说">${l.novel}</td>
          <td data-l="状态" class="${l.alive?"ok-t":"err"}">${l.alive?"在线":"离线"}</td>
          <td data-l="待处理" class="num">${l.pending}</td>
          <td data-l="片段" class="num">${l.done_hour}</td>
          <td data-l="视频秒数" class="num">${Math.round(l.seconds_hour)}</td>
          <td data-l="耗时" class="num">${l.avg_take==null?"—":l.avg_take+" 秒"}</td></tr>`).join("") +
        `<tr><td class="dim">合计</td><td class="dim"></td><td class="dim"></td><td class="dim"></td><td class="dim"></td>` +
        `<td class="num"><b>${made}</b></td><td class="num"><b>${Math.round(secs)}</b></td><td class="dim"></td></tr>` +
        `</tbody>`;
    }
    const ageSec = Math.max(0, Math.round((Date.now() - new Date(d.now.replace(" ","T")))/1000));
    $("stamp").className = "";
    $("stamp").textContent = `当前指标 ${d.live_at||d.now||"读取中"} · 每 15 秒同步${d.history_refreshing?" · 历史图表后台更新中":""}${d.history_error?" · 历史统计刷新失败，保留上次数据":""}`;
  }).catch(e=>{ $("stamp").textContent = "读取失败：" + e; $("stamp").className = "err"; });
}
tick(); setInterval(tick, 15000);
</script>"""


BOARD_BODY = """
<div id="book-overview" style="margin-bottom:16px"></div>
<div id="board" style="display:grid;gap:16px"></div>
<script>
const DAY = 86400000;
const mdT = t => { const d = new Date(t); return (d.getMonth()+1)+"/"+d.getDate(); };
const mdHm = t => { const d = new Date(t); return (d.getMonth()+1)+"/"+d.getDate()+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0"); };

function barsSVG(items, W, H){
  const n = items.length || 1, gap = Math.min(3, (W/n)*0.25), bw = Math.max(1, (W-gap*(n-1))/n);
  const max = Math.max(...items.map(i=>i.v), 1);
  const rects = items.map((it,i)=>{
    const h = Math.max(2, it.v/max*(H-6));
    return `<rect data-tip="${it.tip}" x="${(i*(bw+gap)).toFixed(2)}" y="${(H-h).toFixed(2)}" width="${bw.toFixed(2)}" height="${h.toFixed(2)}" rx="1.6" class="sb"${it.v?"":' style="opacity:.18"'}></rect>`;
  }).join("");
  return `<svg class="sparksvg" style="height:${H}px" viewBox="0 0 ${W} ${H}">${rects}</svg>`;
}

function burnup(n, series, projT, stepMs, fmt){
  if (!series.length) return `<div class="dim" style="padding:8px 0">还没有成片数据</div>`;
  const W=760, H=210, pl=8, pr=64, pt=16, pb=26;
  const first = series[0][0], last = series[series.length-1][0];
  const xEnd = Math.max(last + stepMs, projT || 0);
  const X = t => pl + (t-first)/((xEnd-first)||1)*(W-pl-pr);
  const yMax = Math.max(n.chapters||0, n.planned||0, series[series.length-1][1], 1) * 1.06;
  const Y = v => pt + (1 - v/yMax)*(H-pt-pb);
  const line = series.map((p,i) => (i?"L":"M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ");
  const area = line + ` L${X(last).toFixed(1)},${Y(0).toFixed(1)} L${X(first).toFixed(1)},${Y(0).toFixed(1)} Z`;
  let proj = "";
  if (projT){
    const done = series[series.length-1][1];
    proj = `<line class="proj" x1="${X(last).toFixed(1)}" y1="${Y(done).toFixed(1)}" x2="${X(projT).toFixed(1)}" y2="${Y(n.planned).toFixed(1)}"></line>
      <circle class="projdot" cx="${X(projT).toFixed(1)}" cy="${Y(n.planned).toFixed(1)}" r="3"></circle>
      <text class="plab" x="${X(projT).toFixed(1)}" y="${(Y(n.planned)-8).toFixed(1)}" text-anchor="middle">预计 ${fmt(projT)}</text>`;
  }
  const scope = (v,lab,cls) => v ? `<line class="${cls}" x1="${pl}" x2="${W-pr}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"></line>
    <text class="slab" x="${W-pr+6}" y="${(Y(v)+3).toFixed(1)}">${lab} ${v}</text>` : "";
  const ticks = [...new Set([first, series[Math.floor(series.length/2)][0], last, ...(projT?[projT]:[])])]
    .map(t => `<text class="xlab" x="${X(t).toFixed(1)}" y="${H-8}" text-anchor="middle">${fmt(t)}</text>`).join("");
  return `<svg class="burnsvg" viewBox="0 0 ${W} ${H}">
    ${scope(n.chapters,"全书","scope")}${n.planned && n.planned !== n.chapters ? scope(n.planned,"已规划","scope p") : ""}
    <path class="area" d="${area}"></path><path class="bline" d="${line}"></path>${proj}${ticks}</svg>`;
}

function donut(sev){
  const p = sev.pass||0, m = sev.minor||0, f = sev.fail||0, t = (p+m+f)||1;
  const C = 2*Math.PI*34;
  let off = 0;
  const seg = (v,cls) => {
    if (!v) return "";
    const len = v/t*C;
    const s = `<circle class="ring ${cls}" cx="42" cy="42" r="34" transform="rotate(-90 42 42)" stroke-dasharray="${len.toFixed(1)} ${(C-len).toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}"></circle>`;
    off += len; return s;
  };
  return `<div class="donutwrap"><svg width="84" height="84" viewBox="0 0 84 84">
    <circle class="ring base" cx="42" cy="42" r="34"></circle>${seg(p,"p")}${seg(m,"m")}${seg(f,"f")}
    <text class="dnum" x="42" y="40" text-anchor="middle">${p+m+f ? (100*p/t).toFixed(1)+"%" : "—"}</text>
    <text class="dlab" x="42" y="54" text-anchor="middle">通过</text></svg>
    <div class="dleg"><span><i class="dot ok"></i>通过 ${p}</span><span><i class="dot warn"></i>轻微问题 ${m}</span><span><i class="dot bad"></i>不通过 ${f}</span><span>审查失败 ${sev.review_error||0}（未计入通过率）</span></div></div>`;
}

function redoPanel(q){
  if (!q.clips) return `<div class="dim">还没有当前版本的审查数据</div>`;
  const r = q.redo_rate;
  const cls = r == null ? "dim" : r >= 15 ? "err" : r >= 8 ? "warn-t" : "ok-t";
  const recent = q.recent_redo == null ? "" : ` · 近 7 天 ${q.recent_redo}%`;
  return `<div style="margin:10px 0 4px"><b class="${cls}" style="font-size:30px">${r == null ? "—" : r+"%"}</b>
    <span class="dim" style="margin-left:8px">${q.must_fix} / ${q.clips} 段${recent}</span></div>
    <div class="dim" style="font-size:12px;margin-bottom:10px">其余 ${q.clips - q.must_fix} 段无需重拍${q.review_errors ? `；另有 ${q.review_errors} 段审查失败，未计入` : ""}。</div>`;
}

function catRow(name, v, maxV){
  return `<div class="catrow"><span class="catname">${name}</span><span class="catbar"><i style="width:${(v/maxV*100).toFixed(1)}%"></i></span><span class="num catn">${v}</span></div>`;
}

function laneTable(lanes){
  return `<table><thead><tr><th>最近运行模型</th><th>集数</th><th>片段数</th><th>平均尝试</th><th>需重拍比例</th></tr></thead><tbody>` +
    lanes.map(l=>`<tr><td>${l.model}</td><td class="num">${l.episodes}</td><td class="num">${l.clips}</td>
      <td class="num">${l.avg_attempts == null ? "—" : l.avg_attempts}</td>
      <td class="num">${l.redo_rate == null ? "—" : l.redo_rate+"%"} <span class="dim">(${l.must_fix} / ${l.reviewed} 段已审 · 严格比对通过 ${l.pass_rate == null ? "—" : l.pass_rate+"%"}${l.review_errors ? ` · ${l.review_errors} 段审查失败` : ""})</span></td></tr>`).join("") +
    `</tbody></table>`;
}

function viewerReview(v){
  if (!v) return `<div class="card"><div class="label">明显画面错误 · 复审与修复验收</div><div class="dim">尚无这套独立复审记录。上面的设定一致性通过率不能当作修复后的剩余问题比例。</div></div>`;
  const c = v.counts, r = v.repair_counts;
  const esc = text => String(text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const status = {confirmed:'仍确认有问题', clear:'复审未发现明显错误', one_vote:'单方存疑', needs_review:'待当前版本复审'};
  const remaining = v.rows.filter(row=>row.status!=='clear');
  const groups = ['confirmed','one_vote','needs_review'].map(key=>{
    const rows = remaining.filter(row=>row.status===key);
    if (!rows.length) return '';
    return `<details><summary>${status[key]}（${rows.length} 段）</summary>` + rows.map(row=>
      `<div class="dim" style="margin-top:8px;white-space:normal"><b>第 ${row.chapter} 集 · ${row.clip}</b> · ${esc(row.kind)}<br>${esc(row.status==='needs_review' ? row.reason : row.observation)}</div>`).join('') + '</details>';
  }).join('');
  return `<div class="card" style="box-shadow:none"><div class="label">明显画面错误 · 复审与修复验收</div>
    <div class="nmeta"><span>当前仍确认 <b class="warn-t">${c.confirmed||0}</b> 段 · 单方存疑 ${c.one_vote||0} 段 · 待复审 ${c.needs_review||0} 段</span>
    <span>跟踪范围内复审未发现明显错误 ${c.clear||0} 段</span></div>
    <div class="dim" style="font-size:12px;margin:8px 0">跟踪 ${v.tracked} 段历史候选和修复验收片段，不是全书错误率。当前结论核对了成片所选片段、视频更新时间和拆分编号；没有复审不算通过。</div>
    <div class="nmeta"><span>已保存修复验收 ${v.repair_checks} 段：当前有效的 ${r.clear||0} 段未发现明显错误、${r.confirmed||0} 段仍确认、${r.one_vote||0} 段存疑；${r.needs_review||0} 段需重验</span></div>
    <div class="dim" style="font-size:12px;margin:8px 0">历史清单 ${v.baseline_at||'—'}：双方确认 ${v.baseline_confirmed} 段、单方存疑 ${v.baseline_one_vote} 段。设定一致性与明显错误复审的标准不同，比例不能直接比较。</div>
    ${groups}</div>`;
}

function legacyBoardCard(n){
  const q = n.quality;
  // a book's whole run is a day or two, so short runs switch to hourly granularity
  const hourlyMode = n.daily.length <= 3;
  const src = hourlyMode ? n.hourly : n.daily;
  let cum = 0;
  const series = src.map(([k,c]) => { cum += c; return [Date.parse(k), cum]; });
  const projT = hourlyMode ? (n.projected_ts ? Date.parse(n.projected_ts) : null)
                           : (n.projected ? Date.parse(n.projected+"T00:00:00") : null);
  const fmt = hourlyMode ? mdHm : mdT;
  const stepMs = hourlyMode ? 3600000 : DAY;
  const bars = src.map(([k,c]) => ({v:c, tip: fmt(Date.parse(k)) + " · " + c + " 集"}));
  const etaTxt = n.blocked ? `${n.blocked} 集需处理，暂无总完成时间`
    : n.done > 0 && n.done >= n.planned ? (n.review_pending || n.review_errors ? "已规划部分质检合格，仍有审查待完成" : "已规划部分质检合格，审查已完成")
    : hourlyMode
    ? (n.projected_ts ? `按近 6 小时 <b>${n.rate_h}</b> 集/时，已规划部分预计 <b>${mdHm(Date.parse(n.projected_ts))}</b> 完成` : "暂无投影")
    : (n.projected ? `按近 7 天 <b>${n.rate7}</b> 集/天，已规划部分预计 <b>${mdT(Date.parse(n.projected+"T00:00:00"))}</b> 完成` : "暂无投影");
  const recent = q.recent_rate == null ? "" :
    ` · 近 7 天 <b class="${q.pass_rate != null && q.recent_rate >= q.pass_rate ? "ok-t" : "warn-t"}">${q.recent_rate}%</b>`;
  const cats = q.cats.length ? q.cats.map(([k,v]) => catRow(k, v, q.cats[0][1])).join("")
    : `<div class="dim">没有被判失败的类别</div>`;
  return `<div class="card ncard">
    <div class="nrow"><span class="nname">${n.title}</span>
      <span class="neta">${n.pipeline?`待交付 ${n.pipeline.remaining} 集 · 净增长见下方`:etaTxt}</span></div>
    ${stateSummary(n)}
    <div class="dim" style="font-size:12px">下面曲线为技术合格合成记录（含重合成）；净交付增长见上方独立指标。审查统计只使用当前版本的审查结果。</div>
    ${burnup(n, series, projT, stepMs, fmt)}
    ${bars.length ? barsSVG(bars, 760, 64) : ""}
    ${viewerReview(n.viewer_review)}
    <div class="board2">
      <div><div class="label">需要重拍的片段</div>
        <div class="dim" style="font-size:12px;margin-bottom:8px">只算观众看得出来的问题：肢体结构错误、主角画成别人、该在场的角色不见了。服装、发色、光线时段这类与设定卡的出入不计入，它们在下面的明细里。</div>
        ${redoPanel(q)}
        <details><summary>设定一致性明细（严格比对，多数不必重拍）</summary>
          <div class="dim" style="font-size:12px;margin:8px 0">逐段对照人物卡、地点和时段，含服装、发型等细节差异。这里的“不通过”只表示与卡片有出入，不代表成片有明显问题。</div>
          ${q.clips || q.review_errors ? donut(q.sev) : `<div class="dim">还没有当前版本的审查数据</div>`}
          <div class="dim" style="margin:8px 0 10px;font-size:12.5px">严格比对通过率 ${q.pass_rate == null ? "—" : q.pass_rate+"%"}（${q.clips} 段）${recent}</div>
          ${cats}
        </details></div>
      <div><div class="label">按最近运行模型汇总</div>
        ${n.lanes.length ? laneTable(n.lanes) : `<div class="dim">暂无</div>`}</div>
    </div>${episodeAttention(n)}</div>`;
}

function historicalProduction(n){
  if(n.history_loading)return '<div class="pipeline-note">历史制作曲线正在后台读取，当前产线指标已经可用。</div>';
  const hourlyMode=(n.daily||[]).length<=3,src=(hourlyMode?n.hourly:n.daily)||[];
  let cum=0;const series=src.map(([k,c])=>{cum+=c;return [Date.parse(k),cum];});
  const fmt=hourlyMode?mdHm:mdT,step=hourlyMode?3600000:DAY;
  const bars=src.map(([k,c])=>({v:c,tip:fmt(Date.parse(k))+' · '+c+' 集'}));
  return `<div class="pipeline-note">按现有技术合格成片的合成时间统计，包含重合成，不是净交付增长。历史数据计算于 ${pipelineEscape(n.history_at||'—')}。</div>`+
    burnup(n,series,null,step,fmt)+(bars.length?barsSVG(bars,760,64):'');
}

function boardCard(n){
  if(n.pipeline)return pipelineCurrentCard(n)+`<details class="card pipeline-history" data-panel="${pipelineEscape(n.id)}-history"><summary>${pipelineEscape(n.title)} · 历史制作曲线</summary>${historicalProduction(n)}</details>`;
  const body=n.history_loading?'<div class="pipeline-note">历史制作记录正在读取。</div>':legacyBoardCard(n);
  return `<details class="card pipeline-legacy" data-panel="${pipelineEscape(n.id)}-legacy"><summary>${pipelineEscape(n.title)} · 历史制作和旧审核参考</summary><div class="pipeline-note">尚未接入当前产线或本轮精判，以下旧统计不参与当前交付汇总。</div>${body}</details>`;
}

function load(){
  fetch("board.json", {cache:"no-store"}).then(r=>r.json()).then(d=>{
    if(d.ui_version&&d.ui_version!==DASHBOARD_VERSION){window.location.reload();return;}
    if (d.building&&!(d.novels||[]).some(n=>n.pipeline)){
      document.getElementById("board").innerHTML = `<div class="card dim">首次统计要扫一遍每集的审查和渲染报告，十几秒到一分钟，好了会自动出来…</div>`;
      setTimeout(load, 3000); return;
    }
    document.getElementById('book-overview').innerHTML=pipelineBookOverview(d.novels);
    pipelineRender('board',d.novels.map(boardCard).join(''));
    document.getElementById("stamp").textContent = `当前指标 ${d.live_at||d.now||"读取中"} · 每 15 秒同步${d.history_refreshing?" · 历史图表后台更新中":""}${d.history_error?" · 历史统计刷新失败，保留上次数据":""}`;
    setTimeout(load, 15000);
  }).catch(e=>{ const s=document.getElementById("stamp"); s.textContent="读取失败："+e; s.className="err"; setTimeout(load, 20000); });
}
load();
</script>"""


PAGE = _page("实时",
             '<span class="health" id="health"><i class="dot idle"></i>读取中</span><span id="stamp">加载中…</span>',
             LIVE_BODY)


PAGE_BOARD = _page("看板", '<span id="stamp"></span>', BOARD_BODY)
