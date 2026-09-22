/* /health/<book>: every episode's production state as a clickable list; attention first. */

const STATUS_LABEL = {no_plan:"未规划", plan_blocked:"规划受阻", pending:"待渲染", stale:"待重做",
                      clips_failed:"片段失败", done_with_warnings:"质检告警", done:"完成"};
const STATUS_CLASS = s => s==="done" ? "ok-p" : s==="pending" ? "off" : "warn-p";

async function renderHealth(book){
  const d = await getJSON(`/api/book/${encodeURIComponent(book)}/health`);
  const total = d.rows.length;
  document.getElementById("health-head").innerHTML = `<div class="stats">
    ${stat("剧集", total, "集")}${stat("完成", d.counts.done||0, "集")}
    ${stat("待渲染", d.counts.pending||0, "集")}${stat("待重做", d.counts.stale||0, "集")}
    ${stat("片段失败", d.counts.clips_failed||0, "集")}${stat("需处理", d.attention.length, "集")}
    </div>
    <div class="dim" style="margin:4px 2px 0"><a href="/novel/${encodeURIComponent(book)}">← ${esc(book)}</a>
    · 判定与产线调度器同源（episode_status）${d.h3_lane ? " · H3 车道" : ""}</div>`;

  const link = n => `<a href="/episode/${encodeURIComponent(book)}/${n}"><b class="num">${n}</b></a>`;
  document.getElementById("attention").innerHTML = d.attention.length
    ? `<tr><th>集</th><th>状态</th><th>原因</th><th>审查</th><th>已跑次数</th></tr>` +
      d.attention.map(r=>`<tr><td>${link(r.episode)}</td>
        <td><span class="pill ${STATUS_CLASS(r.status)}">${STATUS_LABEL[r.status]||r.status}</span></td>
        <td>${esc(r.reason)}</td><td class="dim">${esc(r.review)}</td><td class="num">${r.runs}</td></tr>`).join("")
    : `<tr><td class="dim">没有需要处理的剧集</td></tr>`;

  const states = [...new Set(d.rows.map(r=>r.status))];
  const filters = document.getElementById("filters");
  filters.innerHTML = `<a href="#" data-s="" class="flink on">全部 ${total}</a> ` +
    states.map(s=>`<a href="#" data-s="${s}" class="flink">${STATUS_LABEL[s]||s} ${d.counts[s]}</a>`).join(" · ");

  const draw = filter => {
    const rows = filter ? d.rows.filter(r=>r.status===filter) : d.rows;
    document.getElementById("rows").innerHTML =
      `<tr><th>集</th><th>状态</th><th>审查</th><th>已跑</th><th>原因</th><th>更新时间</th></tr>` +
      rows.map(r=>`<tr><td>${link(r.episode)}</td>
        <td><span class="pill ${STATUS_CLASS(r.status)}">${STATUS_LABEL[r.status]||r.status}</span></td>
        <td class="dim">${esc(r.review)}</td><td class="num">${r.runs}</td>
        <td class="dim">${esc(r.reason)}</td>
        <td class="dim num">${r.mtime?fmtTime(r.mtime):""}</td></tr>`).join("");
  };
  filters.querySelectorAll(".flink").forEach(a=>a.addEventListener("click", e=>{
    e.preventDefault();
    filters.querySelectorAll(".flink").forEach(x=>x.classList.toggle("on", x===a));
    draw(a.dataset.s);
  }));
  draw("");
}

renderHealth(decodeURIComponent(parts[1])).catch(e=>{
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<div class="card"><div class="label">加载失败</div><pre class="md">${esc(e.message)}</pre></div>`);
});
