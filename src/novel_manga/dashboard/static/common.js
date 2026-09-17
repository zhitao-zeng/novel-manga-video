function deliverySummary(n){
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
