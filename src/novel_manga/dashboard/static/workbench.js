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
    <div class="dim" style="margin:4px 2px 0">书 <b>${esc(book)}</b> · <a href="/assets/${encodeURIComponent(book)}">资产库 →</a></div>`;
  document.getElementById("episodes").innerHTML =
    `<tr><th>集</th><th>成片</th><th>剧本</th><th>分镜</th><th>审片</th><th>质检</th><th>修复</th><th>更新时间</th></tr>` +
    rows.map(r=>`<tr>
      <td><a href="/episode/${encodeURIComponent(book)}/${r.episode}"><b class="num">${r.episode}</b></a></td>
      <td>${badge(r.video,"成片")}</td><td>${badge(r.script,"剧本")}</td><td>${badge(r.plan,"分镜")}</td>
      <td>${badge(r.review,"审片")}</td><td>${badge(r.qc,"质检")}</td><td>${r.repair_candidate?'<span class="pill warn-p">候选</span>':""}</td>
      <td class="dim num">${r.mtime?fmtTime(r.mtime):""}</td>
    </tr>`).join("");
}

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
  if (d.prompts && d.prompts.length){
    html += `<div class="card"><div class="label">提示词请求 · ${d.prompts.length} 次</div>` + d.prompts.map((p,i)=>{
      const body = p.json != null ? esc(JSON.stringify(p.json,null,2))
                 : p.text ? esc(p.text.text) + (p.text.truncated ? `\n…（截断，完整 ${fmtBytes(p.text.size)}）` : "") : "";
      return `<details><summary class="num">${esc(p.name)}</summary><pre class="md">${body}</pre></details>`;
    }).join("") + `</div>`;
  }
  html += textBlock("剧本 chapter_script.md", d.script_md);
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
  const film = document.getElementById("film");
  if (film) document.querySelectorAll(".cliprow").forEach(row =>
    row.addEventListener("click", ()=>{ film.currentTime = parseFloat(row.dataset.t)||0; film.play(); }));
}

/* ---------- /assets/<id> ---------- */
function specRows(spec){
  const skip = new Set(["asset_id"]);
  return Object.entries(spec||{}).filter(([k])=>!skip.has(k))
    .map(([k,v])=>`<tr><td class="dim" style="white-space:nowrap">${esc(k)}</td><td>${esc(typeof v==="object"?JSON.stringify(v):v)}</td></tr>`).join("");
}
function assetCard(book, kind, a){
  const spec = a.spec||{};
  const name = spec.name || spec.location || spec.title || a.id;
  const base = `/media/${encodeURIComponent(book)}/series_assets/${kind}/${a.id}`;
  const thumb = img => `/thumb/${encodeURIComponent(book)}/series_assets/${kind}/${a.id}/${encodeURIComponent(img)}`;
  const imgs = a.images.map(img=>({full:`${base}/${encodeURIComponent(img)}`, small:thumb(img)}));
  return `<div class="asset">
    ${imgs.length?`<a href="${imgs[0].full}" target="_blank"><img loading="lazy" src="${imgs[0].small}?w=520" alt="${esc(name)}"></a>`:""}
    <div class="asset-body">
      <div><b>${esc(name)}</b> ${spec.role?`<span class="pill">${esc(spec.role)}</span>`:""} <span class="dim num">${esc(a.id)}</span></div>
      ${spec.appearance?`<div class="dim small">${esc(spec.appearance)}</div>`:""}
      ${spec.wardrobe?`<div class="dim small">服装：${esc(spec.wardrobe)}</div>`:""}
      ${imgs.length>1?`<div class="thumbs">${imgs.slice(1).map(u=>`<a href="${u.full}" target="_blank"><img loading="lazy" src="${u.small}?w=240"></a>`).join("")}</div>`:""}
      <details><summary class="dim small">spec.json</summary><table class="kv">${specRows(spec)}</table></details>
    </div></div>`;
}
async function renderAssets(book){
  const d = await getJSON(`/api/book/${encodeURIComponent(book)}/assets`);
  let html = `<div class="dim" style="margin:0 2px"><a href="/novel/${encodeURIComponent(book)}">← ${esc(book)}</a> · 资产库</div>`;
  html += `<div class="stats">${stat("角色", d.characters.length, "张卡")}${stat("地点", d.locations.length, "张卡")}${stat("音色", d.voices.length, "条")}${stat("头像", d.avatars.length, "个")}</div>`;
  if (d.characters.length)
    html += `<div class="card"><div class="label">角色卡</div><div class="gallery">${d.characters.map(a=>assetCard(book,"characters",a)).join("")}</div></div>`;
  if (d.locations.length)
    html += `<div class="card"><div class="label">地点卡</div><div class="gallery">${d.locations.map(a=>assetCard(book,"locations",a)).join("")}</div></div>`;
  if (d.voices.length)
    html += `<div class="card"><div class="label">音色</div>` + d.voices.map(v=>
      `<div class="voice"><span class="num">${esc(v.name)}</span><audio controls preload="none" src="/media/${encodeURIComponent(book)}/series_assets/voices/${encodeURIComponent(v.name)}"></audio></div>`).join("") + `</div>`;
  if (d.avatars.length)
    html += `<div class="card"><div class="label">头像</div><div class="avatars">` + d.avatars.map(v=>
      `<a href="/media/${encodeURIComponent(book)}/series_assets/avatars/${encodeURIComponent(v.name)}" target="_blank"><img loading="lazy" src="/thumb/${encodeURIComponent(book)}/series_assets/avatars/${encodeURIComponent(v.name)}?w=160" title="${esc(v.name)}"></a>`).join("") + `</div></div>`;
  if (!d.characters.length && !d.locations.length && !d.voices.length && !d.avatars.length)
    html += `<div class="card"><div class="dim">series_assets 目录还没有资产</div></div>`;
  document.getElementById("assets").innerHTML = html;
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
