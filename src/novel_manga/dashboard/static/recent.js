/* /recent: the newest episodes across every book, with gate outcomes; then recent experiments. */

function outcomePills(r){
  const pills = [];
  pills.push(r.video ? `<span class="pill ok-p">成片</span>` : `<span class="pill off">无成片</span>`);
  if (r.plan_status) pills.push(`<span class="pill ${r.plan_status==="passed"?"ok-p":"warn-p"}">规划 ${esc(r.plan_status)}</span>`);
  if (r.qc_passed === true) pills.push(`<span class="pill ok-p">质检✓</span>`);
  else if (r.qc_passed === false) pills.push(`<span class="pill bad-p">质检✗</span>`);
  if (r.review_flags) pills.push(`<span class="pill warn-p">审片标记 ${r.review_flags}</span>`);
  return pills.join(" ");
}

async function renderRecent(){
  const d = await getJSON("/api/recent");
  document.getElementById("recent").innerHTML =
    `<tr><th>时间</th><th>书</th><th>集</th><th>结果</th><th>规划模型</th></tr>` +
    d.episodes.map(r=>`<tr>
      <td class="dim num" style="white-space:nowrap">${fmtTime(r.mtime)}</td>
      <td>${esc(r.title)} <span class="dim">${esc(r.book)}</span></td>
      <td><a href="/episode/${encodeURIComponent(r.book)}/${r.episode}"><b class="num">${r.episode}</b></a></td>
      <td>${outcomePills(r)}</td>
      <td class="dim">${esc(r.model||"—")}</td>
    </tr>`).join("");
  document.getElementById("recent-exp").innerHTML =
    `<tr><th>时间</th><th>实验</th></tr>` +
    d.experiments.map(e=>`<tr><td class="dim num" style="white-space:nowrap">${fmtTime(e.mtime)}</td>
      <td>${esc(e.name)}</td></tr>`).join("") ||
    `<tr><td class="dim">还没有实验目录</td></tr>`;
}

renderRecent().catch(e=>{
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<div class="card"><div class="label">加载失败</div><pre class="md">${esc(e.message)}</pre></div>`);
});
