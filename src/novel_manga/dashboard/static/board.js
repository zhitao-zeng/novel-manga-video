
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
