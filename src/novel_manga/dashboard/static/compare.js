/* /compare: the same character drawn in several styles, as a character-by-style matrix.
   Lightbox helpers come from workbench.js (loaded first); a cell carries its own dir. */

async function renderCompare(){
  const d = await getJSON("/api/compare/samples");
  const head = document.getElementById("compare-head");
  if (!d.characters.length){
    head.innerHTML = `<div class="card"><div class="dim">还没有样卡目录（outputs/_sample-&lt;谁&gt;-&lt;画风&gt;/）。</div></div>`;
    return;
  }
  head.innerHTML = `<div class="stats">${stat("角色", d.characters.length, "个")}${stat("画风", d.styles.length, "种")}</div>`;
  const matrix = document.getElementById("matrix");
  matrix.innerHTML = `<table class="resp cmp"><tr><th>角色</th>${d.styles.map(s=>`<th>${esc(s)}</th>`).join("")}</tr>` +
    d.characters.map(c=>`<tr><td><b>${esc(c.name)}</b></td>` +
      d.styles.map(s=>{
        const cell = c.cells[s];
        if (!cell || !cell.images.length) return `<td class="dim" style="text-align:center">—</td>`;
        const thumb = `/thumb/${encodeURIComponent(cell.dir)}/series_assets/characters/${cell.id}/${encodeURIComponent(cell.images[0])}?w=240`;
        return `<td><img loading="lazy" class="cm" data-name="${esc(c.name)}" data-style="${esc(s)}" src="${thumb}" alt="${esc(c.name)} · ${esc(s)}"></td>`;
      }).join("") + `</tr>`).join("") + `</table>`;

  matrix.querySelectorAll("img.cm").forEach(img => img.addEventListener("click", ()=>{
    const row = d.characters.find(c=>c.name === img.dataset.name);
    const items = d.styles.filter(s=>row.cells[s] && row.cells[s].images.length)
      .map(s=>({id: row.cells[s].id, images: row.cells[s].images, _dir: row.cells[s].dir,
                spec: {...(row.cells[s].spec||{}), "画风": s}}));
    const i = Math.max(items.findIndex(it => it.spec["画风"] === img.dataset.style), 0);
    openLightbox(items[i]._dir, "characters", items, i);   // 每个 cell 的 _dir 覆盖 book
  }));
}

renderCompare().catch(e=>{
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<div class="card"><div class="label">加载失败</div><pre class="md">${esc(e.message)}</pre></div>`);
});
document.querySelector(".lb-back").addEventListener("click", closeLightbox);
document.addEventListener("keydown", e=>{
  const lb = document.getElementById("lb");
  if (!lb || lb.style.display === "none") return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") stepLightbox(-1);
  if (e.key === "ArrowRight") stepLightbox(1);
});
