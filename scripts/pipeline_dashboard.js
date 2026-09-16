function pipelineEscape(value){return String(value == null ? '' : value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function pipelineNumber(value){return value == null ? '—' : Number(value).toLocaleString('zh-CN');}
function pipelineTile(label,value,unit,hint){return `<div class="pipeline-metric"><div class="pk">${pipelineEscape(label)}</div><div class="pv">${pipelineNumber(value)}<span class="pu">${pipelineEscape(unit||'')}</span></div>${hint?`<div class="ph">${pipelineEscape(hint)}</div>`:''}</div>`;}
function pipelineLines(rows){return `<div class="pipeline-list">${rows.map(r=>`<div class="pipeline-line"><span>${pipelineEscape(r[0])}</span><span>${pipelineEscape(r[1])}</span></div>`).join('')}</div>`;}
function pipelineSection(title,body){return `<section class="pipeline-section"><div class="pipeline-title">${pipelineEscape(title)}</div>${body}</section>`;}
const PIPELINE_COLORS={passed:'#20966b',review:'#e7a23a',repair:'#d96869',finishing:'#5195d5',preparing:'#b6c2d2',active:'#7774ce'};
const PIPELINE_CHART_WINDOWS={},PIPELINE_CHART_DATA={};
function pipelinePct(value,total){return total>0?(100*value/total).toFixed(1):'0.0';}
function pipelineTime(at){return new Date(at*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});}
function pipelineDeliveryRows(p){
  const b=(p.inspection||{}).episode_buckets||{};
  const unchecked=(b.not_fully_checked||0)+(b.flash_confirmation||0)+(b.unreadable||0);
  const preparing=b.awaiting_preparation||0,failed=b.checked_with_errors||0;
  return [
    {label:'可交付',value:p.deliverable||0,color:PIPELINE_COLORS.passed},
    {label:'待生成／复审',value:unchecked,color:PIPELINE_COLORS.review},
    {label:'内容待修复',value:failed,color:PIPELINE_COLORS.repair},
    {label:'技术／发布收尾',value:Math.max(0,(p.total||0)-(p.deliverable||0)-unchecked-failed-preparing),color:PIPELINE_COLORS.finishing},
    ...(p.preparation?[{label:'开拍准备中',value:preparing,color:PIPELINE_COLORS.preparing}]:[])
  ];
}
function pipelineStack(rows,total,unit){
  const summary=rows.map(r=>`${r.label} ${r.value} ${unit}`).join('，');
  return `<div class="pc-stack" role="img" aria-label="${pipelineEscape(summary)}">${rows.filter(r=>r.value>0).map(r=>
    `<span style="width:${total>0?100*r.value/total:0}%;background:${r.color}" title="${pipelineEscape(r.label)}：${pipelineNumber(r.value)} ${unit}（${pipelinePct(r.value,total)}%）"></span>`).join('')}</div>`+
    `<div class="pc-legend">${rows.map(r=>`<span><i style="background:${r.color}"></i>${pipelineEscape(r.label)} <b>${pipelineNumber(r.value)}</b><small>${unit}</small></span>`).join('')}</div>`;
}
function pipelineDeliveryProgress(p){
  const rows=pipelineDeliveryRows(p),pc=pipelinePct(p.deliverable||0,p.total);
  return `<section class="pc-progress"><div class="pc-progress-head"><div><span class="pc-eyebrow">交付进度</span><strong>${pc}<small>%</small></strong></div>`+
    `<div class="pc-progress-count"><b>${pipelineNumber(p.deliverable)}</b> / ${pipelineNumber(p.total)} 集<span>还差 ${pipelineNumber(p.remaining)} 集</span></div></div>`+
    pipelineStack(rows,p.total,'集')+
    `<div class="pc-caption">${p.preparation?`准备通过自动开拍 · 已准入 ${pipelineNumber(p.preparation.admitted)} 集。`:'按当前成片和审查结果统计。'} 各状态互不重叠，绿色部分才是可交付。</div></section>`;
}
function pipelineColumns(rows,unit){
  const values=rows.filter(r=>r.value!=null).map(r=>r.value);
  const ceiling=v=>{if(v<=5)return Math.ceil(v);const step=Math.pow(10,Math.floor(Math.log10(v)))/2;return Math.ceil(v/step)*step;};
  const high=ceiling(Math.max(1,...values)*1.2),low=-ceiling(Math.max(0,...values.map(v=>-v))*1.2),span=high-low;
  const zero=100*(-low)/span;
  const ticks=[high,0,...(low<0?[low]:[])];
  const grid=ticks.map(v=>`<div class="pc-gridline" style="bottom:${100*(v-low)/span}%"><span>${v}</span></div>`).join('');
  const bars=rows.map((r,i)=>{
    const missing=r.value==null,value=missing?0:r.value,height=100*Math.abs(value)/span;
    const bottom=value<0?zero-height:zero;
    const tip=r.detail||`${r.label}：${missing?'未采样':pipelineNumber(value)+' '+unit}`;
    const labelBottom=value<0?Math.max(0,bottom-10):bottom+height;
    return `<button type="button" class="pc-column ${missing?'pc-missing':''} ${r.partial&&!missing?'pc-partial':''}" title="${pipelineEscape(tip)}" aria-label="${pipelineEscape(tip)}" data-chart-detail="${pipelineEscape(tip)}">`+
      `<span class="pc-bar" style="bottom:${bottom}%;height:${height}%;--bar-color:${value<0?PIPELINE_COLORS.repair:(r.color||PIPELINE_COLORS.passed)}"></span>`+
      `<span class="pc-bar-value" style="bottom:${missing?zero:labelBottom}%">${missing?'—':pipelineNumber(value)}</span>`+
      `<span class="pc-x-label">${pipelineEscape(r.tick===false?'':r.label)}</span></button>`;
  }).join('');
  return `<div class="pc-plot"><span class="pc-axis-unit">${unit}</span><div class="pc-plot-area">${grid}<div class="pc-columns">${bars}</div></div></div>`;
}
function pipelineTrendChart(key,net){
  PIPELINE_CHART_DATA[key]=net;
  const windows=(net||{}).windows||{};
  const six=windows['6'];
  const hours=PIPELINE_CHART_WINDOWS[key]||(six&&six.buckets.filter(b=>b.value!=null).length>=3?6:1);
  const window=windows[String(hours)],buckets=window?window.buckets:[];
  const buttons=[1,6,24].map(h=>`<button type="button" data-net-window="${h}" data-net-key="${pipelineEscape(key)}" aria-pressed="${hours===h}">${h}小时</button>`).join('');
  const rows=buckets.map((b,i)=>({value:b.value,partial:b.partial,label:pipelineTime(b.start),tick:i%2===0,
    detail:`${pipelineTime(b.start)}–${pipelineTime(b.end)}：${b.value==null?'暂无足够采样':(b.value>0?'+':'')+b.value+' 集净增'+(b.partial?'（仅覆盖部分时段）':'')}`+
      (b.observed_start!=null?`；实际采样 ${pipelineTime(b.observed_start)}–${pipelineTime(b.observed_end)}`:'')}));
  return `<section class="pc-chart pc-trend" id="pipeline-trend-${pipelineEscape(key)}"><div class="pc-chart-head"><div><h3>交付净增</h3><span>${window?'每柱 '+window.step_minutes+' 分钟':'等待采样'}</span></div><div class="pc-range" aria-label="净增图时间范围">${buttons}</div></div>`+
    (rows.length?pipelineColumns(rows,'集'):'<div class="pc-chart-empty">正在读取历史采样<br><small>有连续采样后显示柱状图，不补写历史产量。</small></div>')+
    `<div class="pc-detail" aria-live="polite">悬停或点击柱子查看具体时段</div><div class="pc-caption">绿色为净增，红色为回退；浅色虚线柱为不完整采样，— 表示缺数据。${net&&net.sampled_at?'最后采样 '+pipelineEscape(net.sampled_at):''}</div></section>`;
}
function pipelineWorkChart(p){
  const s=p.stages||{},c=(p.preparation||{}).counts||{};
  const prep=['auditing','repairing','replanning','building_cards','translating'].reduce((sum,k)=>sum+(c[k]||0),0);
  const rows=[...(p.preparation?[{label:'开拍准备',value:prep,color:PIPELINE_COLORS.active}]:[]),
    {label:'修复准备',value:s['running:prepare']||0,color:PIPELINE_COLORS.repair},
    {label:'生成合成',value:s['running:render']||0,color:PIPELINE_COLORS.finishing},
    {label:'内容复审',value:s['running:review']||0,color:PIPELINE_COLORS.review}];
  const q=p.status==='complete'?null:p.requests;
  const pool=q?`<div class="pc-pool"><span>共享 H3 请求池</span><b>${pipelineNumber(q.held)} / ${pipelineNumber(q.limit)}</b><div class="pc-pool-track"><i style="width:${Math.min(100,q.limit?100*q.held/q.limit:0)}%"></i></div></div>`:'';
  return `<section class="pc-chart"><div class="pc-chart-head"><div><h3>现在在做什么</h3><span>运行中的单集任务</span></div><b class="pc-live-count">${rows.reduce((sum,r)=>sum+r.value,0)} 个</b></div>`+
    pipelineColumns(rows,'集')+`<div class="pc-detail" aria-live="polite">${p.status==='complete'?'本轮已经完成。':'各步骤并行推进，完成一集就接下一集。'}</div>`+pool+
    '<div class="pc-caption">柱子表示单集任务数。H3 请求池由各本书共享。</div></section>';
}
function pipelineAuditMeter(label,a){
  const total=a.total||0,passed=a.passed||0,flagged=a.flagged||0;
  return `<div class="pc-audit-meter"><div class="pc-meter-head"><b>${pipelineEscape(label)}</b><span>已审 ${pipelineNumber(a.checked)} / ${pipelineNumber(total)} 段 <strong>${pipelinePct(a.checked||0,total)}%</strong></span></div>`+
    pipelineStack([{label:'通过',value:passed,color:PIPELINE_COLORS.passed},{label:'有问题',value:flagged,color:PIPELINE_COLORS.repair},
      {label:'待完成',value:Math.max(0,total-passed-flagged),color:PIPELINE_COLORS.preparing}],total,'段')+'</div>';
}
if(typeof document!=='undefined'){
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-net-window]');
    if(!button)return;
    const key=button.dataset.netKey,hours=Number(button.dataset.netWindow);
    PIPELINE_CHART_WINDOWS[key]=hours;
    document.getElementById('pipeline-trend-'+key).outerHTML=pipelineTrendChart(key,PIPELINE_CHART_DATA[key]);
    document.getElementById('pipeline-trend-'+key).querySelector(`[data-net-window="${hours}"]`).focus({preventScroll:true});
  });
  for(const type of ['mouseover','focusin','click'])document.addEventListener(type,event=>{
    const bar=event.target.closest('[data-chart-detail]');
    if(bar){const detail=bar.closest('.pc-chart').querySelector('.pc-detail');if(detail)detail.textContent=bar.dataset.chartDetail;}
  });
}
function pipelineRender(id,html){
  const root=document.getElementById(id);
  const opened=new Set(Array.from(root.querySelectorAll('details[data-panel][open]'),d=>d.getAttribute('data-panel')));
  root.innerHTML=html;
  for(const d of root.querySelectorAll('details[data-panel]'))if(opened.has(d.getAttribute('data-panel')))d.open=true;
}
function pipelineStage(stage){return ({check:'确认待修问题',repair:'修复准备',recover:'修复准备',note1:'再次修复准备',note2:'再次修复准备',render:'生成与合成',render1:'生成与合成',render2:'生成与合成',review:'当前成片复审',review1:'当前成片复审',review2:'当前成片复审',audit:'补充审查',confirm:'争议复核',shared_qwen:'Qwen 共同补查',shared_flash:'Flash 共同补查'})[stage]||stage;}
function pipelineStatus(status){return ({running:'运行中',pending:'排队中',waiting_plan:'等待计划修复',waiting_preparation:'等待后续章节准备完成',held:'已挂起',needs_attention:'需要处理',complete:'已结束',complete_with_errors:'已结束，有执行异常',stopped:'已停止',starting:'正在启动',paused:'已暂停',pausing:'停止派单，在途收尾',draining:'在途收尾',not_started:'未启动',monitoring_shared_audit:'等待补查新结果'})[status]||status||'状态待确认';}

function pipelineHeadline(n){
  const p=n.pipeline;
  if(p.mode==='operations')return '批量产线状态';
  if(p.mode==='audit'){
    const a=p.audit||{};
    return `${a.scope||'片段审查'} · 已审 ${pipelineNumber(a.checked)} / ${pipelineNumber(a.total)} 段`;
  }
  return `待交付 ${pipelineNumber(p.remaining)} 集 · ${p.scope_label||'全书'} ${pipelineNumber(p.total)} 集`;
}

function pipelineHealth(n){
  const p=n.pipeline;
  if(p.mode==='operations')return Object.values(p.flows||{}).some(r=>r.blocked)?'warn':'ok';
  if(p.mode==='audit'){
    const a=p.audit||{};
    return a.error||a.alive===false&&a.status==='stopped'?'bad':a.counts&&a.counts.error||a.age_seconds>180?'warn':'ok';
  }
  if(['complete','paused','not_started'].includes(p.status))return 'ok';
  return p.controller_alive===false&&p.status==='running'?'bad':p.age_seconds>180||(p.held_episodes||[]).length||p.blocked_clips?'warn':'ok';
}

function pipelineBookOverview(novels){
  const repairs=novels.filter(n=>n.pipeline&&n.pipeline.mode!=='audit');
  return repairs.length?`<div class="pc-book-overview">${repairs.map(n=>`<a class="pc-book-link" href="#novel-${pipelineEscape(n.id)}"><div><span>${pipelineEscape(n.title)}</span><strong>${pipelinePct(n.pipeline.deliverable,n.pipeline.total)}%</strong></div>`+
    pipelineStack(pipelineDeliveryRows(n.pipeline),n.pipeline.total,'集')+`<div class="pc-book-scope" title="${pipelineEscape(n.pipeline.scope_label||'全书')}">${pipelineNumber(n.pipeline.deliverable)} / ${pipelineNumber(n.pipeline.total)} 集 · ${pipelineEscape(n.pipeline.scope_label||'全书')}</div></a>`).join('')}</div>`:'';
}

function pipelineOverview(novels){
  const repairs=novels.filter(n=>n.pipeline&&n.pipeline.mode!=='audit');
  const audits=novels.map(n=>n.pipeline&&(n.pipeline.mode==='audit'?n.pipeline.audit:n.pipeline.sd_audit)).filter(a=>a&&!a.error);
  const sum=(rows,fn)=>rows.length?rows.reduce((s,n)=>s+(Number(fn(n))||0),0):null;
  const passed=sum(repairs,n=>n.pipeline.deliverable), left=sum(repairs,n=>n.pipeline.remaining);
  const running=sum(repairs,n=>Object.entries(n.pipeline.stages||{}).filter(([k])=>['running:prepare','running:render','running:review'].includes(k)).reduce((s,[,v])=>s+v,0));
  const checked=sum(audits,a=>a.checked), total=sum(audits,a=>a.total);
  return `<div class="stat"><div class="k">当前主线可交付</div><b>${pipelineNumber(passed)}</b><span class="u">集</span><div class="sub">按各书本轮生产范围统计</div></div>`+
    `<div class="stat"><div class="k">当前主线未交付</div><b>${pipelineNumber(left)}</b><span class="u">集</span><div class="sub">技术、内容审查和发布均需通过</div></div>`+
    `<div class="stat"><div class="k">正在修复处理</div><b>${pipelineNumber(running)}</b><span class="u">集</span><div class="sub">含准备、生成合成和复审</div></div>`+
    `<div class="stat"><div class="k">独立审片已完成</div><b>${pipelineNumber(checked)}</b><span class="u">段</span><div class="sub">本轮范围 ${pipelineNumber(total)} 段；不折算为交付集数</div></div>`+pipelineBookOverview(novels);
}

function pipelineAuditSummary(n){
  const a=n.pipeline.audit||{};
  if(a.error)return `<div class="pipeline"><div class="pipeline-note warn-t">当前审查数据暂不可用，稍后自动重试。${pipelineEscape(a.error)}</div></div>`;
  const c=a.counts||{}, remaining=Math.max(0,a.total-a.checked-(c.superseded||0));
  const tiles=pipelineTile('本轮已审',a.checked,'段',`范围 ${pipelineNumber(a.total)} 段 · 涉及 ${pipelineNumber(a.episodes)} 集`)+
    pipelineTile('本轮尚未审完',remaining,'段','包含待领取、在审及执行异常')+
    pipelineTile('审查通过',a.passed,'段','仅表示本轮视觉审查通过')+
    pipelineTile('标记明显问题',a.flagged,'段',`涉及 ${pipelineNumber(a.flagged_episodes)} 集 · 已审占比 ${pipelineNumber(a.flagged_rate)}%`);
  const labels={same_person_twice:'人物重复',species_or_gender_wrong:'性别或物种不符',action_by_wrong_person:'动作或台词错人',actor_missing:'关键人物缺席',lead_face_swapped:'人物外貌不符'};
  const kinds=Object.entries(a.issues||{}).map(([k,v])=>[labels[k]||k,`${pipelineNumber(v)} 段`]);
  const excluded=Object.entries(a.excluded_models||{}).map(([k,v])=>`${k.replace('sd2_','SD2.')} ${pipelineNumber(v)} 段`).join('、');
  return `<div class="pipeline pipeline-audit"><div class="pipeline-head"><span>${pipelineEscape(a.scope)} · ${pipelineEscape(pipelineStatus(a.status))}</span><span>队列读取 ${pipelineEscape(a.sampled_at)}</span></div>`+
    pipelineAuditMeter('本轮审查进度',a)+`<div class="pipeline-primary">${tiles}</div><div class="pipeline-grid">`+
    pipelineSection('当前审查队列',pipelineLines([['审片并发',`${pipelineNumber(a.workers)} 路 Qwen`],['待领取 / 在审',`${pipelineNumber(c.pending||0)} / ${pipelineNumber(c.running||0)} 段`],
      ['执行异常 / 结果不完整',`${pipelineNumber(c.error||0)} / ${pipelineNumber(a.unclassified||0)} 段`],['旧视频换版，任务淘汰',`${pipelineNumber(c.superseded||0)} 段`],['复用之前已审结果',`${pipelineNumber(a.reused)} 段`]])+
      `<div class="pipeline-note">任务状态更新 ${pipelineEscape(a.updated_at)}。${a.age_seconds>180?'状态更新已超过 3 分钟，请核对进程。':''}</div>`)+
    pipelineSection('本轮审查发现',kinds.length?pipelineLines(kinds)+'<div class="pipeline-note">同一段可能命中多类问题，不能把各类相加。</div>':'<div class="pipeline-note">目前还没有标记明显问题。</div>')+
    `</div><div class="pipeline-note">${excluded?'本轮排除 '+pipelineEscape(excluded)+'。':''}按实际片段来源筛选；未审片段不计作通过。本轮审片进度不代表全书已交付，后续仍需技术与发布检查。</div></div>`;
}

function pipelineCurrentCard(n){
  const health=pipelineHealth(n);
  return `<div class="card ncard" id="novel-${pipelineEscape(n.id)}"><div class="nrow"><span class="nname"><i class="dot ${health}"></i>${pipelineEscape(n.title)}</span><span class="neta">${pipelineEscape(pipelineHeadline(n))}</span></div>${pipelineSummary(n)}</div>`;
}

function pipelineAlerts(novels){
  const rows=[];
  for(const n of novels){
    const p=n.pipeline;if(!p)continue;
    if(p.mode==='audit'){
      const a=p.audit||{};
      if(a.error||a.alive===false&&a.status==='stopped')rows.push([n.title,'审片任务需要恢复或检查']);
      if(a.counts&&a.counts.error)rows.push([n.title,`${a.counts.error} 段审查执行异常`]);
    }else{
      if((p.held_episodes||[]).length)rows.push([n.title,`暂停的章节：${p.held_episodes.join('、')}`]);
      if(p.blocked_clips)rows.push([n.title,`${p.blocked_clips} 段暂时无法继续自动修复，原因见修复明细`]);
      if(p.controller_alive===false&&p.status==='running'||!['complete','paused','not_started'].includes(p.status)&&p.age_seconds>180)rows.push([n.title,'总控进程或数据更新需要检查']);
    }
  }
  return rows.length?pipelineLines(rows):'<div class="dim">当前产线暂无运行阻塞告警。</div>';
}

function pipelineFlowStatus(p){
  if(!p.flows)return '';
  const names={production:'生产',prepare:'开拍准备',repair:'审查修复'};
  const body=Object.entries(p.flows).map(([name,r])=>{
    const counts=`在途 ${pipelineNumber(r.in_flight)} 个进程 · 待办 ${r.pending==null?'未统计':r.pending+' 集'} · 阻塞 ${pipelineNumber(r.blocked)} 集`;
    const reasons=(r.blocked_reasons||[]).slice(0,5).map(reason=>`<div class="pipeline-note warn-t">${pipelineEscape(reason)}</div>`).join('');
    return `<div class="pipeline-head"><b>${names[name]||pipelineEscape(name)} · ${pipelineEscape(pipelineStatus(r.status))}</b><span>${r.controller_alive?'管理器存活':'管理器未运行'}</span></div>`+
      `<div class="pipeline-note">${pipelineEscape(counts)} · 状态更新 ${pipelineEscape(r.updated_at||'无记录')}</div>`+reasons;
  }).join('');
  return pipelineSection('运行状态',body);
}

function pipelineSummary(n){
  if(n.pipeline.mode==='operations')return pipelineFlowStatus(n.pipeline);
  if(n.pipeline.mode==='audit')return pipelineFlowStatus(n.pipeline)+pipelineAuditSummary(n);
  const p=n.pipeline, inspection=p.inspection||{}, buckets=inspection.episode_buckets||{}, clips=inspection.clips||{};
  const tech=p.technical||{}, stats=p.repair, audit=p.shared_audit||{}, ac=audit.counts||{}, cap=p.capacity||{};
  const checked=(clips.passed||0)+(clips.failed||0);
  const phases=p.stages||{}, net=p.net_delivery, resources=p.resources;
  const technical=pipelineSection('技术状态',pipelineLines([
    ['语音检查',p.speech_gate==='observe'?'只观察，不阻挡交付':'参与交付检查'],
    ['技术质检合格',`${pipelineNumber(tech.done)} 集`],['未过技术检查',`${pipelineNumber(tech.done_with_warnings||0)} 集`],
    ['计划已更新，成片待重做',`${pipelineNumber(tech.stale||0)} 集`],['生成失败',`${pipelineNumber(tech.clips_failed||0)} 集`],
    ['请求结构阻塞',`${pipelineNumber(p.plan_blocked_clips)} 段`],p.scope_label?['本轮生产范围',`${pipelineNumber(p.total)} 集`]:['已有视频文件 / 已规划',`${pipelineNumber(n.files)} / ${pipelineNumber(n.planned)} 集`]])+
    `<div class="pipeline-note">${p.speech_gate==='observe'?'语音问题保留记录，暂不触发重拍或阻挡交付；文件完整性、黑屏等技术检查继续执行。':'技术合格表示音视频和台词完整度等过关，内容审查另外计算。'}</div>`);
  const review=pipelineSection('当前片段审查',`<div class="pipeline-primary">`+
    pipelineTile('当前片段总数',clips.total,'段')+pipelineTile('当前审查通过',clips.passed,'段')+
    pipelineTile('已确认有问题',clips.failed,'段')+pipelineTile('尚未查完',clips.unchecked,'段')+'</div>'+
    pipelineLines([['当前审查覆盖率',clips.total?`${(100*checked/clips.total).toFixed(1)}%`:'—'],
      ['已审中的问题比例',checked?`${(100*(clips.failed||0)/checked).toFixed(2)}%`:'—'],
      ['未查部分中的旧候选',`${pipelineNumber(clips.unconfirmed_candidates||0)} 段`],
      ['补漏发现，待确认',`${pipelineNumber(clips.flash_pending||0)} 段`],
      ['存在已确认问题的章节',`${pipelineNumber(inspection.episodes_with_confirmed_errors)} 集`]])+
    '<div class="pipeline-note">片段“通过＋有问题＋未查”合计为总数。问题不都需要重拍，修法由原文和请求复核决定。候选和待确认不另加到总数。</div>');
  let repair=pipelineLines([['符合派单条件',`${pipelineNumber(p.ready_clips)} 段`],['修复阻塞',`${pipelineNumber(p.blocked_clips)} 段`],
    ['挂起的章节任务',`${pipelineNumber((p.held_episodes||[]).length)} 集`],['新入口有效生成上限','默认每段 3 次；修正输入后的单段追加另记']]);
  if(stats&&!stats.error){
    repair+=`<div class="pipeline-primary" style="margin-top:10px">`+pipelineTile('已进入新修复入口',stats.tracked,'段')+
      pipelineTile('其中当前已过关',stats.passed,'段')+pipelineTile('实际新生成',stats.generated,'次')+
      pipelineTile('原文复核后保留',stats.retained,'段','当前仍在使用的旧视频')+'</div>'+
      pipelineLines([['已过关段平均新生成',`${pipelineNumber(stats.avg_generations_passed)} 次/段`],
        ['总新生成 / 当前已过关段',`${pipelineNumber(stats.total_cost_per_passed)} 次/段`]]);
    const actions=stats.actions||{};
    repair+=pipelineLines([['最近选择：原文 / 请求复核',`${pipelineNumber(actions.source||0)} 段`],['最近选择：重写分镜',`${pipelineNumber(actions.reframe||0)} 段`],['最近选择：直接重拍',`${pipelineNumber(actions.retake||0)} 段`]]);
    const blocks=stats.blocks||[];
    if(blocks.length)repair+=`<details data-panel="${pipelineEscape(n.id)}-blocks"><summary>阻塞明细快照（${blocks.length} 段，${pipelineEscape(stats.updated_at)}）</summary><div class="pipeline-scroll"><table class="pipeline-table"><thead><tr><th>章节</th><th>片段</th><th>原因</th></tr></thead><tbody>${blocks.map(b=>`<tr><td>${b.episode}</td><td>${pipelineEscape(b.clip)}</td><td title="${pipelineEscape(b.reason)}">${pipelineEscape(b.category)}</td></tr>`).join('')}</tbody></table></div></details>`;
    repair+=`<div class="pipeline-note">只统计新修复入口；旧整集轮次不当作段级实际生成次数。第一项平均值含保留原素材的 0 次生成，第二项包含仍未过关片段的投入。明细更新于 ${pipelineEscape(stats.updated_at)}。</div>`;
    repair+='<div class="pipeline-note">修法数量来自各段最近的准备记录，历史结构修复可能没有对应路由记录。</div>';
  }else repair+=`<div class="pipeline-note">${stats&&stats.error?'修复明细暂不可用：'+pipelineEscape(stats.error):'修复明细正在后台统计…'}</div>`;
  repair=pipelineSection('修复方式与实际开销',repair);
  let netBody='';
  for(const minutes of [15,60]){
    const r=net&&net.rates&&net.rates[String(minutes)];
    netBody+=pipelineTile(r&&r.complete_window?`近 ${minutes} 分钟净交付增长`:`${minutes} 分钟窗口 · 观察中`,r?r.delta:null,'集',
      r?`实测 ${r.minutes} 分钟，折合 ${r.per_hour} 集/小时`:'至少累计 5 分钟采样后显示；不填推测值');
  }
  const speed=pipelineSection('交付净增长与技术产出',`<div class="pipeline-primary">${netBody}</div>`+
    pipelineLines([['技术合格成片近一小时',`${pipelineNumber(n.per_hour)} 集`],['技术合格近 15 分钟折合',`${pipelineNumber(n.recent_per_hour)} 集/小时`]])+
    `<div class="pipeline-note">净增长按“可交付数量的变化”计算，允许为负；技术产出按合成时间计算，包含重合成。${net&&net.since?'净增长采样始于 '+pipelineEscape(net.since):'净增长采样正在启动'}。</div>`);
  const laneDone={};for(const row of audit.lanes||[])if(row.status==='done')laneDone[row.lane]=(laneDone[row.lane]||0)+row.count;
  const auditBody=pipelineSection('Qwen / Flash 共同补查',pipelineLines([
    ['本轮任务总数',`${pipelineNumber(audit.total)} 条`],['已完成',`${pipelineNumber(ac.done||0)} 条`],
    ['待领取 / 正在查',`${pipelineNumber(ac.pending||0)} / ${pipelineNumber(ac.running||0)} 条`],
    ['审查执行异常',`${pipelineNumber(ac.error||0)} 条`],['视频换版，旧任务淘汰',`${pipelineNumber(ac.superseded||0)} 条`],
    ['Qwen 完成 / Flash 完成',`${pipelineNumber(laneDone.qwen||0)} / ${pipelineNumber(laneDone.flash||0)} 条`]])+
    '<div class="pipeline-note">这是本轮补查任务进度，不等于全书全部片段的审查覆盖率。</div>');
  let workers=pipelineLines([['运行中：修复准备',`${pipelineNumber(phases['running:prepare']||0)} 集`],
    ['运行中：生成与合成',`${pipelineNumber(phases['running:render']||0)} 集`],['运行中：审查与复核',`${pipelineNumber(phases['running:review']||0)} 集`],
    ['修复全流程在途上限',`${pipelineNumber(cap.repair_episodes)} 集`]])+
    '<div class="pipeline-note">集数是流程任务数，并非同时生成的视频请求数。实例接单位通常包含排队中的请求。</div>';
  if(p.requests)workers+=pipelineLines([['共享请求占用 / 全局上限',`${pipelineNumber(p.requests.held)} / ${pipelineNumber(p.requests.limit)}`]]);
  const capacityNames={repair_model:'修复准备与复审共用',repair_render:'修复生成与合成',fill_model:'补渲复审',fill_render:'补渲生成与合成',confirm:'争议确认',audit:'补充审查'};
  if(cap.stage_capacity)workers+=`<details data-panel="${pipelineEscape(n.id)}-capacity"><summary>分阶段并发上限</summary>${pipelineLines(Object.entries(cap.stage_capacity).map(([key,value])=>[capacityNames[key]||key,`${value} 集`]))}</details>`;
  if(resources&&!resources.error){
    workers+=pipelineLines([['确认可用生成实例',`${pipelineNumber(resources.available_instances)} 个`],
      ['健康状态待确认',`${pipelineNumber(resources.unconfirmed_instances||0)} 个`],
      ['可用实例接单位合计',`${pipelineNumber(resources.available_slots)} 个`],['夜班接单实例',`${pipelineNumber(resources.night_instances)} 个`]]);
    workers+=`<div class="pipeline-scroll"><table class="pipeline-table"><thead><tr><th>实例</th><th>状态</th><th>占用 / 接单位</th><th>GPU 利用率</th></tr></thead><tbody>${(resources.instances||[]).map(r=>`<tr><td>${pipelineEscape(r.name)}<br><span class="dim">${pipelineEscape(r.source)}</span></td><td>${!r.enabled?'已停用':r.available===true?'可接单':r.available===false?'不可接单':'状态待确认'}</td><td>${r.held} / ${r.slots}</td><td>${r.gpu_percent==null?'—':r.gpu_percent+'%'}</td></tr>`).join('')}</tbody></table></div>`;
    workers+=`<div class="pipeline-note">GPU 采样距今 ${pipelineNumber(resources.gpu_sample_age)} 秒。API 健康来自最近的生产探测；空闲较久时可能显示待确认。</div>`;
  }else workers+='<div class="pipeline-note">实例容量正在读取…</div>';
  workers+=`<details data-panel="${pipelineEscape(n.id)}-jobs"><summary>全部任务状态（${(p.jobs||[]).length} 项）</summary><div class="pipeline-scroll"><table class="pipeline-table"><thead><tr><th>章节</th><th>步骤</th><th>状态</th></tr></thead><tbody>${(p.jobs||[]).map(j=>`<tr><td>${j.episodes.length?j.episodes.join('、'):'全书补查'}</td><td>${pipelineEscape(pipelineStage(j.stage))}</td><td>${pipelineEscape(pipelineStatus(j.status))}</td></tr>`).join('')}</tbody></table></div></details>`;
  workers=pipelineSection('运行阶段与实际算力',workers);
  const d=n.delivery||{};
  const diagnostics=`<details data-panel="${pipelineEscape(n.id)}-diagnostics"><summary>历史与辅助诊断参考</summary>`+
    pipelineLines([['角色名称匹配疑点（旧“剧本影子门”）',`${pipelineNumber(d.script_flagged)} 集；不阻止交付`],
      ['历史整集两轮后残留',`${pipelineNumber((p.legacy_residual_episodes||[]).length)} 集；新派单按段级记录决定`],
      ['旧交付汇总',`${pipelineNumber(d.deliverable)} 集 · ${d.generated_at||'未计算'}`],
      ['审查执行异常（看板读取）',`${pipelineNumber(n.review_errors)} 集；不代表内容未过的总集数`]])+'</details>';
  const upload=p.modelscope_upload;
  const uploadProgress=upload&&upload.episode_count>0&&Number.isFinite(upload.remote_episodes)?pipelineStack([
    {label:'远端已提交',value:upload.remote_episodes,color:PIPELINE_COLORS.finishing},
    {label:'尚未提交',value:Math.max(0,upload.episode_count-upload.remote_episodes),color:PIPELINE_COLORS.preparing}],upload.episode_count,'集'):'';
  const uploadHtml=upload?pipelineSection('ModelScope 上传',uploadProgress+pipelineLines([
    ['状态',({starting:'准备中',uploading:'上传中',complete:'已上传并核验',error:'上传中断，等待续传',verification_failed:'远端核验未通过'})[upload.status]||upload.status],
    ['已传输视频',`${pipelineNumber(upload.transferred_episodes)} / ${pipelineNumber(upload.episode_count)} 集`],
    ['远端已提交视频',`${pipelineNumber(upload.remote_episodes)} / ${pipelineNumber(upload.episode_count)} 集`],
    ['视频数据',`${((upload.remote_video_bytes||0)/1e9).toFixed(2)} / ${((upload.video_bytes||0)/1e9).toFixed(2)} GB`]])+
    `<div class="pipeline-note">数量按远端已提交文件计数，成批更新。<a href="${pipelineEscape(upload.url||'https://modelscope.cn')}" target="_blank" rel="noopener">查看 ModelScope 数据集</a></div>`):'';
  const sd=p.sd_audit;
  const sdHtml=sd?pipelineSection('SD2.0 / SD2.5 片段审查',sd.error?'<div>审查状态暂不可用</div>':
    Object.entries(sd.models||{}).map(([model,row])=>pipelineAuditMeter(model.toUpperCase(),row)).join('')+
    pipelineLines([['当前状态',pipelineStatus(sd.status)],['范围',`${pipelineNumber(sd.total)} 段 · ${pipelineNumber(sd.episodes)} 集`],['审查工位 / 正在审',`${pipelineNumber(sd.workers)} / ${pipelineNumber((sd.counts||{}).running||0)}`],['复用已有结果',`${pipelineNumber(sd.reused)} 段`],['审查执行异常',`${pipelineNumber((sd.counts||{}).error||0)} 段`]])+
    `<div class="pipeline-note">本轮审查已有 SD 视频，结果逐段保存；只审查，不自动重拍。H3 修复继续单独推进。更新于 ${pipelineEscape(sd.updated_at)}。</div>`):'';
  return pipelineFlowStatus(p)+`<div class="pipeline"><div class="pipeline-head"><span>统一产线 · ${pipelineEscape(pipelineStatus(p.status))}</span><span class="${!['complete','paused','not_started'].includes(p.status)&&p.age_seconds>180?'warn-t':''}">${p.status==='complete'?'完成时间':'总控更新'} ${pipelineEscape(p.updated_at)}${p.status==='complete'?'':' · '+pipelineNumber(p.age_seconds)+' 秒前'}</span></div>`+
    pipelineDeliveryProgress(p)+`<div class="pc-charts">${pipelineTrendChart(n.id,net)}${pipelineWorkChart(p)}</div>`+
    `${uploadHtml}${sdHtml}<details class="pc-more" data-panel="${pipelineEscape(n.id)}-metrics"><summary>查看技术检查、修复明细与资源详情</summary><div class="pipeline-grid">${technical}${review}${repair}${speed}${auditBody}${workers}</div>${diagnostics}</details></div>`;
}
