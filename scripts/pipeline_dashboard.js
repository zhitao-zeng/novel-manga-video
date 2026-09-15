function pipelineEscape(value){return String(value == null ? '' : value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function pipelineNumber(value){return value == null ? '—' : Number(value).toLocaleString('zh-CN');}
function pipelineTile(label,value,unit,hint){return `<div class="pipeline-metric"><div class="pk">${pipelineEscape(label)}</div><div class="pv">${pipelineNumber(value)}<span class="pu">${pipelineEscape(unit||'')}</span></div>${hint?`<div class="ph">${pipelineEscape(hint)}</div>`:''}</div>`;}
function pipelineLines(rows){return `<div class="pipeline-list">${rows.map(r=>`<div class="pipeline-line"><span>${pipelineEscape(r[0])}</span><span>${pipelineEscape(r[1])}</span></div>`).join('')}</div>`;}
function pipelineSection(title,body){return `<section class="pipeline-section"><div class="pipeline-title">${pipelineEscape(title)}</div>${body}</section>`;}
function pipelineRender(id,html){
  const root=document.getElementById(id);
  const opened=new Set(Array.from(root.querySelectorAll('details[data-panel][open]'),d=>d.getAttribute('data-panel')));
  root.innerHTML=html;
  for(const d of root.querySelectorAll('details[data-panel]'))if(opened.has(d.getAttribute('data-panel')))d.open=true;
}
function pipelineStage(stage){return ({check:'确认待修问题',repair:'修复准备',recover:'修复准备',note1:'再次修复准备',note2:'再次修复准备',render:'生成与合成',render1:'生成与合成',render2:'生成与合成',review:'当前成片复审',review1:'当前成片复审',review2:'当前成片复审',audit:'补充审查',confirm:'争议复核',shared_qwen:'Qwen 共同补查',shared_flash:'Flash 共同补查'})[stage]||stage;}
function pipelineStatus(status){return ({running:'运行中',pending:'排队中',waiting_plan:'等待计划修复',held:'已挂起',needs_attention:'需要处理',complete:'已结束',complete_with_errors:'已结束，有执行异常',stopped:'已停止',starting:'正在启动',paused:'已暂停',monitoring_shared_audit:'等待补查新结果'})[status]||status||'状态待确认';}

function pipelineHeadline(n){
  const p=n.pipeline;
  if(p.mode==='audit'){
    const a=p.audit||{};
    return `${a.scope||'片段审查'} · 已审 ${pipelineNumber(a.checked)} / ${pipelineNumber(a.total)} 段`;
  }
  return `待交付 ${pipelineNumber(p.remaining)} 集 · ${p.scope_label||'全书'} ${pipelineNumber(p.total)} 集`;
}

function pipelineHealth(n){
  const p=n.pipeline;
  if(p.mode==='audit'){
    const a=p.audit||{};
    return a.error||a.alive===false&&a.status==='stopped'?'bad':a.counts&&a.counts.error||a.age_seconds>180?'warn':'ok';
  }
  if(p.status==='complete')return 'ok';
  return p.controller_alive===false&&p.status==='running'?'bad':p.age_seconds>180||(p.held_episodes||[]).length||p.blocked_clips?'warn':'ok';
}

function pipelineOverview(novels){
  const repairs=novels.filter(n=>n.pipeline&&n.pipeline.mode!=='audit');
  const audits=novels.map(n=>n.pipeline&&(n.pipeline.mode==='audit'?n.pipeline.audit:n.pipeline.sd_audit)).filter(a=>a&&!a.error);
  const sum=(rows,fn)=>rows.length?rows.reduce((s,n)=>s+(Number(fn(n))||0),0):null;
  const passed=sum(repairs,n=>n.pipeline.deliverable), left=sum(repairs,n=>n.pipeline.remaining);
  const running=sum(repairs,n=>Object.entries(n.pipeline.stages||{}).filter(([k])=>['running:prepare','running:render','running:review'].includes(k)).reduce((s,[,v])=>s+v,0));
  const checked=sum(audits,a=>a.checked), total=sum(audits,a=>a.total);
  const names=repairs.map(n=>n.title+(n.pipeline.scope_label?'（'+n.pipeline.scope_label+'）':'')).join('、')||'尚无已接入的交付产线';
  return `<div class="stat"><div class="k">当前主线可交付</div><b>${pipelineNumber(passed)}</b><span class="u">集</span><div class="sub">${pipelineEscape(names)}</div></div>`+
    `<div class="stat"><div class="k">当前主线未交付</div><b>${pipelineNumber(left)}</b><span class="u">集</span><div class="sub">技术、内容审查和发布均需通过</div></div>`+
    `<div class="stat"><div class="k">正在修复处理</div><b>${pipelineNumber(running)}</b><span class="u">集</span><div class="sub">含准备、生成合成和复审</div></div>`+
    `<div class="stat"><div class="k">独立审片已完成</div><b>${pipelineNumber(checked)}</b><span class="u">段</span><div class="sub">本轮范围 ${pipelineNumber(total)} 段；不折算为交付集数</div></div>`;
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
    `<div class="pipeline-primary">${tiles}</div><div class="pipeline-grid">`+
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
      if(p.controller_alive===false&&p.status==='running'||p.status!=='complete'&&p.age_seconds>180)rows.push([n.title,'总控进程或数据更新需要检查']);
    }
  }
  return rows.length?pipelineLines(rows):'<div class="dim">当前产线暂无运行阻塞告警。</div>';
}

function pipelineSummary(n){
  if(n.pipeline.mode==='audit')return pipelineAuditSummary(n);
  const p=n.pipeline, inspection=p.inspection||{}, buckets=inspection.episode_buckets||{}, clips=inspection.clips||{};
  const tech=p.technical||{}, stats=p.repair, audit=p.shared_audit||{}, ac=audit.counts||{}, cap=p.capacity||{};
  const checked=(clips.passed||0)+(clips.failed||0);
  const phases=p.stages||{}, net=p.net_delivery, resources=p.resources;
  const extraBuckets=(buckets.flash_confirmation||0)+(buckets.unreadable||0);
  const unchecked=(buckets.not_fully_checked||0)+extraBuckets;
  const finishing=Math.max(0,p.total-p.deliverable-(buckets.checked_with_errors||0)-unchecked);
  const split=pipelineTile('可交付',p.deliverable,'集',`${p.scope_label||'全书'} ${pipelineNumber(p.total)} 集 · 未交付 ${pipelineNumber(p.remaining)} 集`)+
    pipelineTile('已查完整，仍有问题',buckets.checked_with_errors,'集','这部分每段都已有审查结论')+
    pipelineTile('当前版本尚未查完整',unchecked,'集','包括重生成待复审、待确认或状态不明')+
    pipelineTile('技术或发布收尾',finishing,'集','含技术检查与修复结果待发布');
  const technical=pipelineSection('技术状态',pipelineLines([
    ['语音检查',p.speech_gate==='observe'?'只观察，不阻挡交付':'参与交付检查'],
    ['技术质检合格',`${pipelineNumber(tech.done)} 集`],['未过技术检查',`${pipelineNumber(tech.done_with_warnings||0)} 集`],
    ['计划已更新，成片待重做',`${pipelineNumber(tech.stale||0)} 集`],['生成失败',`${pipelineNumber(tech.clips_failed||0)} 集`],
    ['请求结构阻塞',`${pipelineNumber(p.plan_blocked_clips)} 段`],p.scope_label?['本轮修复范围',`${pipelineNumber(p.total)} 集`]:['已有视频文件 / 已规划',`${pipelineNumber(n.files)} / ${pipelineNumber(n.planned)} 集`]])+
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
  const uploadHtml=upload?pipelineSection('ModelScope 上传',pipelineLines([
    ['状态',({starting:'准备中',uploading:'上传中',complete:'已上传并核验',error:'上传中断，等待续传',verification_failed:'远端核验未通过'})[upload.status]||upload.status],
    ['已传输视频',`${pipelineNumber(upload.transferred_episodes)} / ${pipelineNumber(upload.episode_count)} 集`],
    ['远端已提交视频',`${pipelineNumber(upload.remote_episodes)} / ${pipelineNumber(upload.episode_count)} 集`],
    ['视频数据',`${((upload.remote_video_bytes||0)/1e9).toFixed(2)} / ${((upload.video_bytes||0)/1e9).toFixed(2)} GB`]])+
    `<div class="pipeline-note">数量按远端已提交文件计数，成批更新。<a href="${pipelineEscape(upload.url||'https://modelscope.cn')}" target="_blank" rel="noopener">查看 ModelScope 数据集</a></div>`):'';
  const sd=p.sd_audit;
  const sdHtml=sd?pipelineSection('SD2.0 / SD2.5 片段审查',sd.error?'<div>审查状态暂不可用</div>':
    `<div class="pipeline-scroll"><table class="pipeline-table"><thead><tr><th>来源</th><th>已审 / 总数</th><th>审查通过</th><th>确认有问题</th><th>待出结论</th></tr></thead><tbody>${Object.entries(sd.models||{}).map(([model,row])=>`<tr><td>${pipelineEscape(model.toUpperCase())}</td><td>${pipelineNumber(row.checked)} / ${pipelineNumber(row.total)}</td><td>${pipelineNumber(row.passed)}</td><td>${pipelineNumber(row.flagged)}</td><td>${pipelineNumber(row.total-row.checked)}</td></tr>`).join('')}</tbody></table></div>`+
    pipelineLines([['当前状态',pipelineStatus(sd.status)],['范围',`${pipelineNumber(sd.total)} 段 · ${pipelineNumber(sd.episodes)} 集`],['审查工位 / 正在审',`${pipelineNumber(sd.workers)} / ${pipelineNumber((sd.counts||{}).running||0)}`],['复用已有结果',`${pipelineNumber(sd.reused)} 段`],['审查执行异常',`${pipelineNumber((sd.counts||{}).error||0)} 段`]])+
    `<div class="pipeline-note">本轮审查已有 SD 视频，结果逐段保存；只审查，不自动重拍。H3 修复继续单独推进。更新于 ${pipelineEscape(sd.updated_at)}。</div>`):'';
  return `<div class="pipeline"><div class="pipeline-head"><span>统一产线 · ${pipelineEscape(pipelineStatus(p.status))}</span><span class="${p.status!=='complete'&&p.age_seconds>180?'warn-t':''}">${p.status==='complete'?'完成时间':'总控更新'} ${pipelineEscape(p.updated_at)}${p.status==='complete'?'':' · '+pipelineNumber(p.age_seconds)+' 秒前'}</span></div>`+
    `<div class="pipeline-primary">${split}</div><div class="pipeline-note">四项互不重叠，合计 ${pipelineNumber(p.total)} 集。${extraBuckets?`待查项中含 ${pipelineNumber(buckets.flash_confirmation||0)} 集待确认、${pipelineNumber(buckets.unreadable||0)} 集状态无法读取。`:''}</div>`+
    `${uploadHtml}${sdHtml}<div class="pipeline-grid">${technical}${review}${repair}${speed}${auditBody}${workers}</div>${diagnostics}</div>`;
}
