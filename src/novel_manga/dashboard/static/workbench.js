/* Workbench pages: /workbench (books), /novel/<id> (episodes), /episode/<id>/<n> (detail),
   /assets/<id> (gallery).  All reads come from /api/book/... and /media/<book>/... . */

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const parts = location.pathname.split("/").filter(Boolean);
const fmtBytes = n => n > 1048576 ? (n/1048576).toFixed(1)+" MB" : n > 1024 ? (n/1024).toFixed(0)+" KB" : n+" B";
const fmtTime = t => { const d = new Date(t*1000); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; };

async function getJSON(url){
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

function badge(ok, label){ return ok ? `<span class="pill ok-p">${label}</span>` : `<span class="pill off">${label}</span>`; }

/* ---------- /workbench ---------- */
async function renderBooks(){
  const data = await getJSON("/api/workbench");
  const books = data.books;
  document.getElementById("stats").innerHTML =
    stat("磁盘上的书", books.length, "本") +
    stat("生产纳管", books.filter(b=>b.managed).length, "本") +
    stat("未纳管", books.filter(b=>!b.managed).length, "本", "没在 configs/pipeline.json 里，只在磁盘上") +
    stat("剧集合计", books.reduce((s,b)=>s+b.episodes,0), "集");
  document.getElementById("books").innerHTML =
    `<tr><th>书</th><th>状态</th><th>画风/画幅</th><th>剧集</th><th>可交付</th><th></th></tr>` +
    books.map(b=>`<tr>
      <td><b>${esc(b.title)}</b> <span class="dim">${esc(b.id)}</span></td>
      <td>${b.managed ? '<span class="pill ok-p">纳管</span>' : '<span class="pill warn-p">未纳管</span>'}
        ${b.backend && b.backend!=="local" ? `<span class="pill warn-p" title="planning_backend">${esc(b.backend)}</span>` : ""}</td>
      <td class="dim">${esc(b.style||"—")} · ${esc(b.frame||"—")}</td>
      <td class="num">${b.episodes}</td>
      <td>${b.deliverable==null ? '<span class="dim">未统计</span>' : `<b class="num">${b.deliverable}</b> / ${b.total}`}</td>
      <td class="links"><a href="/novel/${encodeURIComponent(b.id)}">剧集</a>
        <a href="/health/${encodeURIComponent(b.id)}">健康</a>
        <a href="/bible/${encodeURIComponent(b.id)}">设定</a>
        ${b.has_assets ? `<a href="/assets/${encodeURIComponent(b.id)}">资产</a>` : ""}</td>
    </tr>`).join("");
}
function stat(k, v, u, sub){
  return `<div class="stat"><div class="k">${k}</div><b>${v}</b><span class="u">${u}</span>${sub?`<div class="sub">${sub}</div>`:""}</div>`;
}

/* ---------- /novel/<id> ---------- */
async function renderNovel(book){
  const data = await getJSON(`/api/book/${encodeURIComponent(book)}/episodes`);
  const rows = data.episodes;
  const videos = rows.filter(r=>r.video).length;
  document.getElementById("book-head").innerHTML = `<div class="stats">
    ${stat("剧集", rows.length, "集")}${stat("有成片", videos, "集")}${stat("有剧本", rows.filter(r=>r.script).length, "集")}${stat("修复候选", rows.filter(r=>r.repair_candidate).length, "集")}
    </div>
    <div class="dim" style="margin:4px 2px 0">书 <b>${esc(book)}</b> · <a href="/assets/${encodeURIComponent(book)}">资产库 →</a> <a href="/health/${encodeURIComponent(book)}">健康 →</a> <a href="/bible/${encodeURIComponent(book)}">设定集 →</a></div>`;
  document.getElementById("episodes").innerHTML =
    `<tr><th>集</th><th>成片</th><th>剧本</th><th>分镜</th><th>审片</th><th>质检</th><th>修复</th><th>更新时间</th></tr>` +
    rows.map(r=>`<tr>
      <td><a href="/episode/${encodeURIComponent(book)}/${r.episode}"><b class="num">${r.episode}</b></a></td>
      <td>${badge(r.video,"成片")}</td><td>${badge(r.script,"剧本")}</td><td>${badge(r.plan,"分镜")}</td>
      <td>${badge(r.review,"审片")}</td><td>${badge(r.qc,"质检")}</td><td>${r.repair_candidate?'<span class="pill warn-p">候选</span>':""}</td>
      <td class="dim num">${r.mtime?fmtTime(r.mtime):""}</td>
    </tr>`).join("");
}

/* line diff for the prompt-attempt comparison (LCS; falls back to side-by-side when huge) */
function diffLines(a, b){
  const A=a.split("\n"), B=b.split("\n");
  if (A.length*B.length > 4_000_000) return null;   // too big for LCS; caller shows plain panes
  const n=A.length, m=B.length;
  const dp=Array.from({length:n+1},()=>new Uint32Array(m+1));
  for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
    dp[i][j]=A[i]===B[j]?dp[i+1][j+1]+1:Math.max(dp[i+1][j],dp[i][j+1]);
  const out=[]; let i=0,j=0;
  while(i<n&&j<m){
    if(A[i]===B[j]){out.push([" ",A[i]]);i++;j++;}
    else if(dp[i+1][j]>=dp[i][j+1]){out.push(["-",A[i]]);i++;}
    else{out.push(["+",B[j]]);j++;}
  }
  while(i<n)out.push(["-",A[i++]]);
  while(j<m)out.push(["+",B[j++]]);
  return out;
}
function promptText(p){ return p.json!=null ? JSON.stringify(p.json,null,2) : (p.text?p.text.text:""); }

/* ---------- /episode/<id>/<n> ---------- */
function textBlock(title, body){
  if (!body) return "";
  const note = body.truncated ? `<span class="dim">（截断展示，完整 ${fmtBytes(body.size)}）</span>` : "";
  return `<div class="card"><div class="label">${title} ${note}</div><pre class="md">${esc(body.text)}</pre></div>`;
}
function jsonBlock(title, obj){
  if (obj == null) return "";
  return `<details class="card"><summary class="label">${title}</summary><pre class="md">${esc(JSON.stringify(obj,null,2))}</pre></details>`;
}
async function renderEpisode(book, n){
  const d = await getJSON(`/api/book/${encodeURIComponent(book)}/episode/${n}`);
  const media = rel => `/media/${encodeURIComponent(book)}/${rel.split("/").map(encodeURIComponent).join("/")}`;
  const thumb = rel => `/thumb/${encodeURIComponent(book)}/${rel.split("/").map(encodeURIComponent).join("/")}`;
  let html = `<div class="dim" style="margin:0 2px"><a href="/novel/${encodeURIComponent(book)}">← ${esc(book)}</a> · 第 <b>${n}</b> 集
    ${d.backend && d.backend!=="local" ? `· <span class="pill warn-p">沙箱 Agent 规划</span>` : ""}
    ${d.report && d.report.model ? `· 规划模型 <span class="pill">${esc(d.report.model)}</span>` : ""}</div>`;
  if (d.video){
    const clipRows = (d.clips||[]).map(c=>`<tr data-t="${c.offset}" class="cliprow"><td class="num">${c.n}</td><td class="dim">${esc(c.kind||"")}</td><td class="num">${c.seconds}s</td><td>${esc(c.label)}</td></tr>`).join("");
    html += `<div class="card"><div class="label">成片</div>
      <video id="film" controls preload="metadata" poster="${d.cover?thumb(d.cover)+"?w=960":""}" src="${media(d.video)}"></video>
      ${d.previous_video ? `<div class="dim" style="margin-top:6px">修复前版本：<a href="${media(d.previous_video)}" target="_blank">previous_final.mp4</a></div>` : ""}
      ${clipRows ? `<div class="twrap" style="margin-top:10px"><table class="resp"><tr><th>#</th><th>类型</th><th>时长</th><th>内容</th></tr>${clipRows}</table><div class="dim" style="margin-top:4px">点任意行跳到该片段起点</div></div>` : ""}
    </div>`;
  } else {
    html += `<div class="card"><div class="label">成片</div><div class="dim">还没有成片文件</div></div>`;
  }
  if (d.video && d.previous_video){
    html += `<div class="card"><div class="label">版本对比 · 修复前 vs 当前（播放/拖动同步）</div>
      <div class="cmp2">
        <div><div class="dim small" style="text-align:center">修复前</div><video id="film-prev" controls preload="metadata" src="${media(d.previous_video)}"></video></div>
        <div><div class="dim small" style="text-align:center">当前</div><video id="film-cur" controls preload="metadata" src="${media(d.video)}"></video></div>
      </div></div>`;
  }
  if (d.prompts && d.prompts.length){
    html += `<div class="card"><div class="label">提示词请求 · ${d.prompts.length} 次</div>` + d.prompts.map((p,i)=>{
      const body = p.json != null ? esc(JSON.stringify(p.json,null,2))
                 : p.text ? esc(p.text.text) + (p.text.truncated ? `\n…（截断，完整 ${fmtBytes(p.text.size)}）` : "") : "";
      return `<details><summary class="num">${esc(p.name)}</summary><pre class="md">${body}</pre></details>`;
    }).join("") + `</div>`;
    if (d.prompts.length >= 2){
      const opts = d.prompts.map((p,i)=>`<option value="${i}">${esc(p.name)}</option>`).join("");
      html += `<div class="card"><div class="label">提示词 diff · 两次尝试改了什么</div>
        <div class="diffbar">左 <select id="diff-l">${opts}</select> 右 <select id="diff-r">${opts}</select>
        <span class="dim small" id="diff-stat"></span></div>
        <pre class="md diff" id="diff-out"></pre></div>`;
    }
  }
  const COLS = [["authored_id","镜号"],["location","场景"],["motion_prompt","画面内容 / 动作"],["authored_sound","台词 / 声音"],
                ["shot_scale","景别"],["camera_angle","摄影角度"],["camera","机位 / 运镜"],["narrative_purpose","叙事目的"],["edit_seconds","秒"]];
  if (d.agent_storyboard){
    const rec = d.agent_storyboard.record||{};
    const meta = [rec.skill&&`skill ${esc(rec.skill)}`, rec.attempt&&`第 ${esc(rec.attempt)} 次尝试`,
                  rec.accepted_at&&`接受于 ${esc(rec.accepted_at)}`, rec.status&&`状态 ${esc(rec.status)}`].filter(Boolean).join(" · ");
    const sheets = (d.agent_storyboard.sheets||[]).map(s=>{
      if (s.error) return `<div class="dim">工作表 ${esc(s.file)} 读取失败：${esc(s.error)}</div>`;
      return `<div class="dim small" style="margin:6px 0">${esc(s.file)} · ${esc(s.name)} · ${s.rows.length} 镜</div>
        <div class="twrap"><table class="resp sheet"><tr>${COLS.map(c=>`<th>${c[1]}</th>`).join("")}</tr>` +
        s.rows.map(r=>`<tr>${COLS.map(c=>`<td>${esc(r[c[0]]??"")}</td>`).join("")}</tr>`).join("") + `</table></div>`;
    }).join("");
    const scriptBody = d.script_md ? `<pre class="md">${esc(d.script_md.text)}</pre>` : `<div class="dim">没有 chapter_script.md</div>`;
    html += `<div class="card"><div class="label">剧本 · 两个来源可切换</div>
      <div class="tabs"><button class="tab on" data-tab="agent">Agent 原始分镜表</button><button class="tab" data-tab="final">正式剧本（出片用）</button></div>
      <div class="tabbody" data-tab="agent"><div class="dim small">${meta}</div>${sheets}</div>
      <div class="tabbody" data-tab="final" style="display:none">${scriptBody}</div></div>`;
  } else {
    html += textBlock("剧本 chapter_script.md（出片用）", d.script_md);
  }
  html += textBlock("分镜表 clip_plan.md", d.plan_md);
  html += jsonBlock("审片意见 episode_review.json", d.review);
  html += jsonBlock("媒体质检 media_qc_report.json", d.qc);
  html += jsonBlock("规划报告 chapter_script_report.json", d.report);
  const logKeys = Object.keys(d.logs||{});
  if (logKeys.length){
    html += `<div class="card"><div class="label">日志（各取末尾 200 行）</div>` +
      logKeys.map(k=>`<details><summary class="num">${esc(k)}</summary><pre class="md">${esc(d.logs[k].text)}</pre></details>`).join("") + `</div>`;
  }
  document.getElementById("episode").innerHTML = html;
  document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", ()=>{
    document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("on", b===btn));
    document.querySelectorAll(".tabbody").forEach(b=>b.style.display = b.dataset.tab===btn.dataset.tab ? "" : "none");
  }));
  const film = document.getElementById("film");
  if (film) document.querySelectorAll(".cliprow").forEach(row =>
    row.addEventListener("click", ()=>{ film.currentTime = parseFloat(row.dataset.t)||0; film.play(); }));
  const prev = document.getElementById("film-prev"), cur = document.getElementById("film-cur");
  if (prev && cur){
    let syncing = false;
    const pair = (x, y, ev, fn) => x.addEventListener(ev, ()=>{ if(syncing) return; syncing=true; fn(y); setTimeout(()=>syncing=false,0); });
    pair(prev, cur, "play", y=>y.play());  pair(cur, prev, "play", y=>y.play());
    pair(prev, cur, "pause", y=>y.pause()); pair(cur, prev, "pause", y=>y.pause());
    pair(prev, cur, "seeked", y=>{y.currentTime=prev.currentTime;}); pair(cur, prev, "seeked", y=>{y.currentTime=cur.currentTime;});
  }
  const dl = document.getElementById("diff-l"), dr = document.getElementById("diff-r");
  if (dl && dr){
    dl.value = "0"; dr.value = "1";
    const run = ()=>{
      const L = promptText(d.prompts[dl.value]), R = promptText(d.prompts[dr.value]);
      const rows = diffLines(L, R);
      const out = document.getElementById("diff-out");
      if (rows === null){
        out.innerHTML = `<div class="dim">内容太大，改为并排展示</div><div class="cmp2"><pre class="md">${esc(L)}</pre><pre class="md">${esc(R)}</pre></div>`;
        document.getElementById("diff-stat").textContent = "";
        return;
      }
      let add=0, del=0;
      out.innerHTML = rows.map(([t,s])=>{
        if(t==="+")add++; if(t==="-")del++;
        return t===" " ? esc(s)+"\n" : `<span class="${t==="+"?"dadd":"ddel"}">${esc(s)}</span>\n`;
      }).join("");
      document.getElementById("diff-stat").textContent = `+${add} / −${del} 行`;
    };
    dl.onchange = run; dr.onchange = run; run();
  }
}

/* ---------- /assets/<id> ---------- */
function specRows(spec){
  const skip = new Set(["asset_id"]);
  return Object.entries(spec||{}).filter(([k])=>!skip.has(k))
    .map(([k,v])=>`<tr><td class="dim" style="white-space:nowrap">${esc(k)}</td><td>${esc(typeof v==="object"?JSON.stringify(v):v)}</td></tr>`).join("");
}

/* Dense grid + lightbox: dozens of cards per screen, click one to enlarge. */
let LB = {items: [], i: 0, book: "", kind: ""};

function gridCells(book, kind, list){
  return list.map((a,i)=>{
    const spec = a.spec||{};
    const name = spec.name || spec.location || spec.title || a.id;
    const img = a.images.length ? `/thumb/${encodeURIComponent(book)}/series_assets/${kind}/${a.id}/${encodeURIComponent(a.images[0])}?w=240` : "";
    return `<div class="acell" data-name="${esc(String(name).toLowerCase())}" data-kind="${kind}" data-i="${i}">
      ${img ? `<img loading="lazy" src="${img}" alt="${esc(name)}">` : `<div class="acell-none dim">无图</div>`}
      <div class="aname" title="${esc(name)}">${esc(name)}</div></div>`;
  }).join("");
}

function openLightbox(book, kind, items, i){
  LB = {items, i, book, kind};
  drawLightbox();
  document.getElementById("lb").style.display = "flex";
  document.body.style.overflow = "hidden";
}
function closeLightbox(){
  document.getElementById("lb").style.display = "none";
  document.body.style.overflow = "";
}
function stepLightbox(d){ LB.i = (LB.i + d + LB.items.length) % LB.items.length; drawLightbox(); }

function drawLightbox(){
  const a = LB.items[LB.i], spec = a.spec||{};
  const name = spec.name || spec.location || spec.title || a.id;
  const dir = a._dir || LB.book;   // sample-matrix cells carry their own dir per style
  const base = `/media/${encodeURIComponent(dir)}/series_assets/${LB.kind}/${a.id}`;
  const tbase = `/thumb/${encodeURIComponent(dir)}/series_assets/${LB.kind}/${a.id}`;
  const main = a.images.length ? a.images[0] : null;
  document.getElementById("lb-box").innerHTML = `
    <div class="lb-img">${main ? `<img src="${tbase}/${encodeURIComponent(main)}?w=960" alt="${esc(name)}">` : ""}</div>
    <div class="lb-panel">
      <div><b style="font-size:16px">${esc(name)}</b> ${spec.role?`<span class="pill">${esc(spec.role)}</span>`:""}</div>
      <div class="dim small">${esc(a.id)} · ${LB.i+1} / ${LB.items.length}</div>
      <table class="kv">${specRows(spec)}</table>
      ${a.images.length>1 ? `<div class="thumbs">${a.images.slice(1).map(img=>`<a href="${base}/${encodeURIComponent(img)}" target="_blank"><img loading="lazy" src="${tbase}/${encodeURIComponent(img)}?w=240"></a>`).join("")}</div>` : ""}
      ${main ? `<div><a href="${base}/${encodeURIComponent(main)}" target="_blank">查看原图 ↗</a></div>` : ""}
      <div class="lb-nav"><button id="lb-prev">← 上一个</button><button id="lb-next">下一个 →</button></div>
    </div>`;
  document.getElementById("lb-prev").onclick = e=>{e.stopPropagation(); stepLightbox(-1);};
  document.getElementById("lb-next").onclick = e=>{e.stopPropagation(); stepLightbox(1);};
}

async function renderAssets(book){
  const d = await getJSON(`/api/book/${encodeURIComponent(book)}/assets`);
  let html = `<div class="dim" style="margin:0 2px"><a href="/novel/${encodeURIComponent(book)}">← ${esc(book)}</a> · 资产库</div>`;
  html += `<div class="stats">${stat("角色", d.characters.length, "张卡")}${stat("地点", d.locations.length, "张卡")}${stat("道具", (d.props||[]).length, "张卡")}${stat("音色", d.voices.length, "条")}${stat("头像", d.avatars.length, "个")}</div>`;
  for (const [kind, label, list] of [["characters","角色卡",d.characters],["locations","地点卡",d.locations],["props","道具卡",d.props||[]]]){
    if (!list.length) continue;
    html += `<div class="card"><div class="label">${label} · ${list.length} 张<span class="dim">（点击放大）</span>
      <input class="afilter" data-kind="${kind}" placeholder="按名字过滤…"></div>
      <div class="agrid" data-kind="${kind}">${gridCells(book, kind, list)}</div></div>`;
  }
  if (d.voices.length)
    html += `<div class="card"><div class="label">音色</div>` + d.voices.map(v=>
      `<div class="voice"><span class="num">${esc(v.name)}</span><audio controls preload="none" src="/media/${encodeURIComponent(book)}/series_assets/voices/${encodeURIComponent(v.name)}"></audio></div>`).join("") + `</div>`;
  if (d.avatars.length)
    html += `<div class="card"><div class="label">头像</div><div class="avatars">` + d.avatars.map(v=>
      `<a href="/media/${encodeURIComponent(book)}/series_assets/avatars/${encodeURIComponent(v.name)}" target="_blank"><img loading="lazy" src="/thumb/${encodeURIComponent(book)}/series_assets/avatars/${encodeURIComponent(v.name)}?w=160" title="${esc(v.name)}"></a>`).join("") + `</div></div>`;
  if (!d.characters.length && !d.locations.length && !(d.props||[]).length && !d.voices.length && !d.avatars.length)
    html += `<div class="card"><div class="dim">series_assets 目录还没有资产</div></div>`;
  html += `<div id="lb" class="lb" style="display:none"><div class="lb-back"></div><div class="lb-box" id="lb-box"></div></div>`;
  document.getElementById("assets").innerHTML = html;

  const sections = {characters: d.characters, locations: d.locations, props: d.props||[]};
  document.querySelectorAll(".acell").forEach(cell => cell.addEventListener("click", ()=>{
    const kind = cell.dataset.kind;
    openLightbox(book, kind, sections[kind], parseInt(cell.dataset.i));
  }));
  document.querySelectorAll(".afilter").forEach(input => input.addEventListener("input", ()=>{
    const q = input.value.trim().toLowerCase();
    document.querySelectorAll(`.agrid[data-kind="${input.dataset.kind}"] .acell`).forEach(cell=>{
      cell.style.display = !q || cell.dataset.name.includes(q) ? "" : "none";
    });
  }));
  document.querySelector(".lb-back").addEventListener("click", closeLightbox);
  document.addEventListener("keydown", e=>{
    if (document.getElementById("lb").style.display === "none") return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") stepLightbox(-1);
    if (e.key === "ArrowRight") stepLightbox(1);
  });
}

/* ---------- /experiments ---------- */
const STATUS_PILL = {"完成": "ok-p", "活跃": "warn-p", "存档": "off"};
async function renderExperiments(){
  const d = await getJSON("/api/experiments");
  const agents = d.agents || {books: [], agent_test: []};
  let agentHtml = `<div class="card"><div class="label">Agent 运行情况</div>`;
  if (agents.books.length)
    agentHtml += `<div style="margin-bottom:10px">` + agents.books.map(b=>
      `<span class="pill warn-p" title="planning_backend=${esc(b.backend)}">${esc(b.title)} · ${esc(b.backend)}</span>`).join(" ") + `</div>`;
  else
    agentHtml += `<div class="dim" style="margin-bottom:10px">当前没有书使用非本地规划后端（profile.json 的 planning_backend）。</div>`;
  if (agents.agent_test.length)
    agentHtml += `<div class="label">沙箱 agent 产物 · outputs/agent-test</div><div class="twrap"><table class="resp">
      <tr><th>目录/文件</th><th>内容数</th></tr>` +
      agents.agent_test.map(t=>`<tr><td class="num">${esc(t.name)}</td><td class="num">${t.files==null?"文件":t.files}</td></tr>`).join("") +
      `</table></div>`;
  agentHtml += `</div>`;
  document.getElementById("agents").innerHTML = agentHtml;

  const rows = d.experiments;
  document.getElementById("experiments").innerHTML = rows.length ? rows.map(e=>`
    <details class="exp">
      <summary>
        <span class="pill ${STATUS_PILL[e.status]||"off"}">${e.status}</span>
        ${e.registered ? "" : '<span class="pill off">未登记</span>'}
        <b>${esc(e.name)}</b>
        <span class="dim">${e.latest?fmtTime(e.latest):""}</span>
      </summary>
      <div class="exp-body">
        ${e.comparison ? `<div>${esc(e.comparison)}</div>` : ""}
        <div class="dim small">
          ${e.model ? `模型 ${esc(e.model)} · ` : ""}${e.head ? `冻结于 ${esc(String(e.head).slice(0,10))} · ` : ""}${e.created_at ? `建于 ${esc(e.created_at)}` : ""}
        </div>
        ${e.endpoints ? `<div class="dim small">端点：${esc(e.endpoints.join("、"))}</div>` : ""}
        ${e.results.length ? `<div class="small">产物：${e.results.map(r=>`<span class="pill">${esc(r)}</span>`).join(" ")}</div>` : '<div class="dim small">还没有结果文件</div>'}
      </div>
    </details>`).join("") : `<div class="dim">outputs/experiments 还没有实验目录</div>`;
}

/* ---------- dispatch ---------- */
(async ()=>{
  try{
    if (parts[0]==="workbench") await renderBooks();
    else if (parts[0]==="novel" && parts[1]) await renderNovel(decodeURIComponent(parts[1]));
    else if (parts[0]==="episode" && parts[1] && parts[2]) await renderEpisode(decodeURIComponent(parts[1]), parts[2]);
    else if (parts[0]==="assets" && parts[1]) await renderAssets(decodeURIComponent(parts[1]));
    else if (parts[0]==="experiments") await renderExperiments();
  }catch(e){
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      `<div class="card"><div class="label">加载失败</div><pre class="md">${esc(e.message)}</pre></div>`);
  }
})();
