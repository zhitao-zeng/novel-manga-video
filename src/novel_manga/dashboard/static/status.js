
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
