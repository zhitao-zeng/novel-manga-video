(() => {
  const root = document.getElementById('architecture');
  if (!root) return;
  const nodes = [
    {id:'source', title:'原文与人物资料', short:'章节原文 · 人物 / 场景资产', x:30, y:40, tag:'输入',
      description:'原文提供事实与因果，人物资料提供身份、别名和已有资产。它们共同构成规划输入。',
      input:'小说原文、小说配置、已有圣经与身份资料', output:'章节原文、人物与场景上下文',
      boundary:'人物资料不是剧本；已有资产也不能代替原文证据。', code:'application/planning · application/identity · application/assets'},
    {id:'plan', title:'剧本与分镜规划', short:'改编 → 拆镜 → 检查与修订', x:250, y:40, tag:'可独立',
      description:'根据原文规划剧情、对白和镜头，通过现有结构及语义检查修订，保留原文定位。',
      input:'章节原文、人物资料、规划方式与预算', output:'chapter_script.json：结构化剧本与分镜',
      boundary:'只规划不生成视频。规划通过不代表最终画面已经合格。', code:'application/planning · planning · planning/methods · story'},
    {id:'pack', title:'编译与请求准备', short:'切段 · 参考绑定 · 中文 / H3', x:470, y:40, tag:'交接',
      description:'将已经确定的剧本转换为片段计划，绑定参考素材。H3 路径还会完成英文请求准备与检查。',
      input:'结构化分镜、素材信息、片长和画幅配置', output:'clip_plan.json、中文编译稿、H3 请求与参考绑定',
      boundary:'保留人物、动作、对白和原文映射；不能用编译补写另一版剧情。', code:'application/packing · story/compilation.py · application/rendering/h3.py'},
    {id:'assets', title:'资产准备', short:'角色卡 · 场景卡 · 参考音频', x:690, y:40, tag:'按需',
      description:'准备本次请求需要的图片和音频，检查已有资产，复用可用素材。',
      input:'片段计划、人物与场景资产、声线配置', output:'本次生成实际引用的图片与音频',
      boundary:'只用缓存合成时，不为缺失素材重建资产。', code:'application/assets · application/preparation · media/assets.py'},
    {id:'generate', title:'片段生成', short:'缓存命中复用 · SD / H3', x:690, y:230, tag:'可独立',
      description:'先匹配缓存；需要生成时，调用已有视频供应商，按原策略处理技术与语音检查、重试及槽位释放。',
      input:'最终请求、参考图片与音频、并发和次数预算', output:'片段视频、请求记录、语音与技术结果',
      boundary:'只生成不自动加入内容审修；显式自动审修模式才接续内容审查与修复。', code:'application/rendering · media/generation.py · media/cache.py · providers'},
    {id:'assemble', title:'后期与合成', short:'字幕 · 心声混音 · 拼接 · 质检', x:470, y:230, tag:'可复用缓存',
      description:'用已选片段完成字幕、适用的心声混音、封面片尾、画幅与音量处理，输出成片和技术报告。',
      input:'已选片段、已有音轨与字幕数据、后期配置', output:'成片、封面、thin_media_report.json',
      boundary:'技术合格不等于人物、动作或整集叙事都正确。', code:'media/analysis.py · media/postprocess.py · application/rendering'},
    {id:'review', title:'成片内容审查', short:'看已有视频 · 对照剧本与证据', x:250, y:230, tag:'可独立',
      description:'审查实际生成的片段，对照当前剧本与原文等证据记录问题，并汇总当前成片的验收结果。',
      input:'已有片段、当前计划、原文与审查证据', output:'episode_review.json、修复反馈、验收汇总',
      boundary:'只 review 到此结束：不会生成或修复。它也不能代替完整的整集叙事验收。', code:'application/review · review · application/review/execution.py'},
    {id:'deliver', title:'验收结果', short:'技术结果 + 当前内容审查', x:30, y:230, tag:'结果',
      description:'结合当前技术结果、内容问题及审查完整度记录通过、待处理或阻塞。素材齐全不代表验收通过。',
      input:'当前成片报告、审查结果、计划与请求是否过期', output:'quality_review、episode_execution.json；交付汇总另由交付判定生成',
      boundary:'待审、审查异常、未解决问题或已知过期产物不能算通过。', code:'application/review/execution.py · application/production/reports.py'},
    {id:'repair', title:'修复决策与执行', short:'保留旧片 / 改请求 / 改剧本 / 重拍', x:250, y:410, tag:'按原因分流',
      description:'沿用已有路由与证据选择修法。候选通过原有检查后写回，仅更新必要片段，再生成、合成与复审。',
      input:'审查反馈、当前请求、原文证据、修复历史与剩余预算', output:'接受的修改、保留旧片的结论、重拍范围或具体阻塞',
      boundary:'不会保证每个问题都能修好。预算耗尽或准备阻塞会停止，不额外叠加批量重拍。', code:'repair/policy.py · application/repair · application/rendering/reviewed.py'}
  ];
  const modes = {
    all:{ids:nodes.map(n=>n.id), focus:'plan', text:'整条产线的关系总览。需要自动审修时显式开启；纯生成不会自动走完所有节点。'},
    plan:{ids:['source','plan','pack'], focus:'plan', text:'只规划：读取原文和资料，产出剧本与片段计划；H3 英文请求还需后续准备，不生成视频。'},
    generate:{ids:['assets','generate','assemble'], focus:'generate', text:'只生成：使用已有计划准备素材、生成并合成，保留原有技术与语音重试；不启动内容审查或整章重规划。'},
    review:{ids:['review','deliver'], focus:'review', text:'只审查：检查已有视频并记录验收结果；可以审不完整或过期素材，但不会因此判为可交付，也不会重拍。'},
    repair:{ids:['review','repair','pack','assets','generate','assemble','deliver'], focus:'repair', text:'自动修复：先审已有素材，再按原因处理必要片段并复审。可能保留旧片或因阻塞结束，不是整集全部重拍。'},
    cache:{ids:['assemble'], focus:'assemble', text:'只用缓存合成：使用与当前请求匹配的已有片段；缺缓存就报告，不生成或重建资产。若另选审查，可继续审已有素材。'}
  };
  let mode = 'all';
  let deep = 'plan';
  let expanded = false;
  const catalogue = window.ARCHITECTURE_DETAILS;
  const holder = document.getElementById('arch-nodes');
  const detail = document.getElementById('arch-detail');
  const make = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  function select(id) {
    const n = nodes.find(item=>item.id===id);
    holder.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.node===id)));
    detail.replaceChildren(make('div','环节详情','arch-eyebrow'), make('h3',n.title), make('p',n.description));
    const dl = make('dl');
    for (const [label,text] of [['输入',n.input],['输出',n.output]]) dl.append(make('dt',label),make('dd',text));
    detail.append(dl,make('p',n.boundary,'arch-boundary'));
    if (!modes[mode].ids.includes(id)) detail.append(make('p','此环节不在当前高亮范围内，仅查看说明。'));
    const code = make('details');
    code.append(make('summary','对应代码模块'),make('code',n.code));
    detail.append(code);
    const target = ({assets:'pack', deliver:'review'})[id] || id;
    const drill = make('button','展开内部步骤与失败分支 ↓','arch-drill-button');
    drill.type='button';
    drill.addEventListener('click',()=>{
      deep=target; expanded=false; renderDeep();
      document.getElementById('arch-internals').scrollIntoView({behavior:'smooth',block:'start'});
    });
    detail.append(drill);
  }
  nodes.forEach((n,index)=>{
    const button=make('button',null,'arch-node');
    button.type='button'; button.dataset.node=n.id; button.setAttribute('aria-pressed','false');
    button.style.left=`${n.x/9}%`; button.style.top=`${n.y/5.5}%`;
    const top=make('span',null,'arch-node-top');
    top.append(make('span',String(index+1).padStart(2,'0')),make('span',n.tag));
    button.append(top,make('b',n.title),make('small',n.short));
    button.addEventListener('click',()=>select(n.id)); holder.append(button);
  });
  function setMode(next) {
    mode=next;
    const current=modes[mode];
    root.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));
    holder.querySelectorAll('[data-node]').forEach(button=>button.classList.toggle('muted',!current.ids.includes(button.dataset.node)));
    root.querySelectorAll('[data-edge]').forEach(edge=>edge.classList.toggle('muted',!edge.dataset.edge.split(' ').every(id=>current.ids.includes(id))));
    document.getElementById('arch-mode-description').textContent=current.text;
    select(current.focus);
  }
  root.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.mode)));
  const labels = {source:'原文与身份', plan:'规划与补丁', pack:'编译与开拍准备', generate:'生成与重试', assemble:'后期与质检', review:'审查与终裁', repair:'修复与写回', operations:'管理与状态'};
  const tabs = document.getElementById('arch-deep-tabs');
  for (const [id,label] of Object.entries(labels)) {
    const button=make('button',label); button.type='button'; button.dataset.deep=id;
    button.addEventListener('click',()=>{deep=id;expanded=false;renderDeep();});
    tabs.append(button);
  }
  function renderDeep() {
    tabs.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(!expanded && b.dataset.deep===deep)));
    const expand = document.getElementById('arch-expand-all');
    expand.setAttribute('aria-pressed',String(expanded));
    expand.textContent=expanded?'收起为单个流程':'展开全部内部流程';
    const container=document.getElementById('arch-deep-content'); container.replaceChildren();
    for (const id of (expanded?Object.keys(catalogue):[deep])) {
      const flow=catalogue[id];
      const section=make('section',null,'arch-deep-section');
      section.append(make('h4',flow.title),make('p',flow.note,'arch-deep-note'));
      const list=make('ol',null,'arch-step-grid');
      flow.steps.forEach(([title,kind,description,input,output,code],i)=>{
        const step=make('li',null,'arch-step');
        const head=make('div',null,'arch-step-header');
        head.append(make('span',`${String(i+1).padStart(2,'0')} / ${flow.steps.length}`,'arch-step-number'),make('span',kind,'arch-step-kind'+(kind.includes('模型')?' model':'')));
        const io=make('div',null,'arch-step-io');
        io.append(make('span','输入'),make('span',input),make('span','输出'),make('span',output));
        const source=make('details');source.append(make('summary','实现位置'),make('code',code));
        step.append(head,make('h5',title),make('p',description),io,source);list.append(step);
      });
      const branches=make('div',null,'arch-branches');branches.append(make('h5','分支与失败去向'));
      flow.branches.forEach(([condition,destination])=>{
        const row=make('div',null,'arch-branch');row.append(make('b',condition+' →'),make('span',destination));branches.append(row);
      });
      section.append(list,branches);container.append(section);
    }
  }
  document.getElementById('arch-expand-all').addEventListener('click',()=>{expanded=!expanded;renderDeep();});
  setMode('all');
  renderDeep();
})();
