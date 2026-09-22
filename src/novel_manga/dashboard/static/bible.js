/* /bible/<book>: what the reading produced - cast, places, volumes, growth, confusions. */

async function renderBible(book){
  const d = await getJSON(`/api/book/${encodeURIComponent(book)}/bible`);
  const m = d.meta || {};
  let html = `<div class="dim" style="margin:0 2px"><a href="/novel/${encodeURIComponent(book)}">← ${esc(book)}</a> · 设定集</div>`;
  html += `<div class="stats">${stat("人物", d.characters.length, "个")}${stat("地点", d.locations.length, "个")}
    ${stat("卷总结", d.volumes.length, "卷")}${stat("成长记录", d.growth.length, "章")}</div>`;
  html += `<div class="card"><div class="label">概览</div><table class="kv">
    ${m.title?`<tr><td class="dim">书名</td><td>${esc(m.title)}</td></tr>`:""}
    ${m.genre?`<tr><td class="dim">题材</td><td>${esc(m.genre)}</td></tr>`:""}
    ${m.visual_style?`<tr><td class="dim">画风</td><td>${esc(m.visual_style)}</td></tr>`:""}
    ${m.palette?`<tr><td class="dim">色调</td><td>${esc(m.palette)}</td></tr>`:""}
    ${m.style_fingerprint?`<tr><td class="dim">指纹</td><td class="num">${esc(m.style_fingerprint)}</td></tr>`:""}
  </table></div>`;

  if (d.characters.length){
    html += `<div class="card"><div class="label">人物 · ${d.characters.length}
      <input class="afilter" id="char-filter" placeholder="按名字/角色/外貌过滤…"></div>
      <div class="twrap"><table class="resp" id="chars"><tr><th>名字</th><th>角色</th><th>性别/年龄</th><th>外貌</th><th>服装</th></tr>` +
      d.characters.map(c=>`<tr class="charow" data-name="${esc([c.name,c.role,c.appearance].join(" ").toLowerCase())}">
        <td><b>${esc(c.name||"")}</b></td><td class="dim">${esc(c.role||"")}</td>
        <td class="dim">${esc([c.gender,c.age].filter(Boolean).join("/"))}</td>
        <td class="dim small">${esc(c.appearance||"")}</td><td class="dim small">${esc(c.wardrobe||"")}</td></tr>`).join("") +
      `</table></div></div>`;
  }
  if ((d.props||[]).length){
    html += `<div class="card"><div class="label">道具 · ${d.props.length}</div><div class="twrap"><table class="resp">
      <tr><th>名字</th><th>类别</th><th>外观</th><th>材质</th><th>持有者</th><th>登场</th><th>标记</th></tr>` +
      d.props.map(p=>`<tr><td><b>${esc(p.name||"")}</b></td><td class="dim">${esc(p.category||"")}</td>
        <td class="dim small">${esc(p.appearance||"")}</td><td class="dim">${esc(p.material||"—")}</td>
        <td class="dim">${esc(p.owner||"—")}</td><td class="num">${p.first_chapter||""}</td>
        <td>${p.closeup?'<span class="pill">特写</span>':""}${p.wearable?'<span class="pill warn-p">可穿戴</span>':""}</td></tr>`).join("") +
      `</table></div></div>`;
  }
  if (d.locations.length){
    const loc = l => typeof l === "string" ? l : (l.name || JSON.stringify(l));
    html += `<div class="card"><div class="label">地点 · ${d.locations.length}</div><div class="twrap"><table class="resp">
      <tr><th>地点</th></tr>` + d.locations.map(l=>`<tr><td>${esc(loc(l))}</td></tr>`).join("") + `</table></div></div>`;
  }
  if (d.volumes.length){
    html += `<div class="card"><div class="label">卷总结 · ${d.volumes.length} 卷</div>` + d.volumes.map(v=>`
      <details><summary><b>第 ${v.from}–${v.to} 章</b> <span class="dim">${esc((v.summary||"").slice(0,40))}…</span></summary>
        <pre class="md">${esc(v.summary||"")}${v.open_threads&&v.open_threads.length?`\n\n悬而未决：\n`+v.open_threads.map(t=>"· "+t).join("\n"):""}${v.standing&&v.standing.length?`\n\n常态：\n`+v.standing.map(t=>"· "+t).join("\n"):""}</pre>
      </details>`).join("") + `</div>`;
  }
  if (d.growth.length){
    html += `<div class="card"><div class="label">成长时间线 · 每章新增</div><div class="twrap"><table class="resp">
      <tr><th>章</th><th>新增人物</th><th>新增地点</th><th></th></tr>` +
      d.growth.map(g=>`<tr><td class="num">${g.chapter}</td>
        <td>${esc(g.characters.join("、"))}</td><td>${esc(g.locations.join("、"))}</td>
        <td>${g.needs_human?'<span class="pill warn-p">需人工</span>':""}</td></tr>`).join("") + `</table></div></div>`;
  }
  if (d.aliases && Object.keys(d.aliases).length)
    html += `<div class="card"><div class="label">别名</div><div class="twrap"><table class="resp"><tr><th>叫法</th><th>指向</th></tr>` +
      Object.entries(d.aliases).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(typeof v==="object"?JSON.stringify(v):v)}</td></tr>`).join("") + `</table></div></div>`;
  if (d.confusable)
    html += jsonBlock("易混对 confusable_pairs.json", d.confusable);
  if (d.lookalikes)
    html += jsonBlock("撞脸预警 card_lookalikes.json", d.lookalikes);
  if (d.story_bible_md)
    html += textBlock("story_bible.md 原文", d.story_bible_md);
  if (!d.characters.length && !d.locations.length)
    html += `<div class="card"><div class="dim">还没有 story_bible.json——这本书可能还没走过读书阶段</div></div>`;
  document.getElementById("bible").innerHTML = html;

  const filter = document.getElementById("char-filter");
  if (filter) filter.addEventListener("input", ()=>{
    const q = filter.value.trim().toLowerCase();
    document.querySelectorAll(".charow").forEach(row=>{
      row.style.display = !q || row.dataset.name.includes(q) ? "" : "none";
    });
  });
}

renderBible(decodeURIComponent(parts[1])).catch(e=>{
  document.querySelector("main").insertAdjacentHTML("afterbegin",
    `<div class="card"><div class="label">加载失败</div><pre class="md">${esc(e.message)}</pre></div>`);
});
