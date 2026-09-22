/* 配方替代过敏原追踪 —— 原生 JS 前端 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------- 基础工具 ----------------

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function allergenTags(list, cls = "allergen") {
  if (!list || !list.length) return '<span class="tag muted">无</span>';
  return list.map((a) => `<span class="tag ${cls}">${esc(a)}</span>`).join("");
}

// ---------------- Tab 切换 ----------------

$$("#tabs .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("#tabs .tab").forEach((b) => b.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------------- 全局数据缓存 ----------------

const state = {
  ingredients: [],
  substitutions: [],
  recipes: [],
};

async function refreshAll() {
  await Promise.all([loadIngredients(), loadSubstitutions(), loadRecipes()]);
  fillIngredientSelects();
  fillRecipeSelects();
}

// ---------------- 原料 ----------------

async function loadIngredients() {
  state.ingredients = await api("/api/ingredients");
  const tbody = $("#tbody-ingredients");
  if (!state.ingredients.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无原料，请新增或在「导入导出」页载入示例数据</td></tr>';
    return;
  }
  tbody.innerHTML = state.ingredients.map((i) => `
    <tr>
      <td class="code-mono">${esc(i.code)}</td>
      <td>${esc(i.name)}</td>
      <td>${allergenTags(i.allergen_list)}</td>
      <td>${esc(i.note)}</td>
      <td>
        <button class="btn tiny" data-act="edit-ing" data-code="${esc(i.code)}">编辑</button>
        <button class="btn tiny danger" data-act="del-ing" data-code="${esc(i.code)}">删除</button>
      </td>
    </tr>`).join("");
}

$("#btn-new-ingredient").addEventListener("click", () => {
  $("#form-ingredient").classList.remove("hidden");
  $("#ing-orig-code").value = "";
  ["ing-code", "ing-name", "ing-allergens", "ing-note"].forEach((id) => ($("#" + id).value = ""));
  $("#ing-code").disabled = false;
  $("#ing-code").focus();
});

$("#btn-cancel-ingredient").addEventListener("click", () => $("#form-ingredient").classList.add("hidden"));

$("#form-ingredient").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    code: $("#ing-code").value.trim(),
    name: $("#ing-name").value.trim(),
    allergens: $("#ing-allergens").value.trim(),
    note: $("#ing-note").value.trim(),
  };
  const orig = $("#ing-orig-code").value;
  try {
    if (orig) {
      await api(`/api/ingredients/${encodeURIComponent(orig)}`, { method: "PUT", body });
      toast("原料已更新");
    } else {
      await api("/api/ingredients", { method: "POST", body });
      toast("原料已创建");
    }
    $("#form-ingredient").classList.add("hidden");
    await refreshAll();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#tbody-ingredients").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const code = btn.dataset.code;
  if (btn.dataset.act === "edit-ing") {
    const ing = state.ingredients.find((x) => x.code === code);
    $("#form-ingredient").classList.remove("hidden");
    $("#ing-orig-code").value = code;
    $("#ing-code").value = ing.code;
    $("#ing-name").value = ing.name;
    $("#ing-allergens").value = ing.allergen_list.join(", ");
    $("#ing-note").value = ing.note;
    $("#ing-code").disabled = false;
  } else if (btn.dataset.act === "del-ing") {
    if (!confirm(`确定删除原料 ${code}？相关替代边也会被移除。`)) return;
    try {
      await api(`/api/ingredients/${encodeURIComponent(code)}`, { method: "DELETE" });
      toast("已删除");
      await refreshAll();
    } catch (err) {
      toast(err.message, true);
    }
  }
});

// ---------------- 替代关系 ----------------

function fillIngredientSelects() {
  const opts = state.ingredients
    .map((i) => `<option value="${esc(i.code)}">${esc(i.code)} · ${esc(i.name)}</option>`)
    .join("");
  $("#sub-source").innerHTML = opts;
  $("#sub-target").innerHTML = opts;
}

async function loadSubstitutions() {
  state.substitutions = await api("/api/substitutions");
  const tbody = $("#tbody-substitutions");
  if (!state.substitutions.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无替代关系</td></tr>';
    return;
  }
  tbody.innerHTML = state.substitutions.map((s) => {
    const added = s.target_allergens.filter((a) => !s.source_allergens.includes(a));
    const removed = s.source_allergens.filter((a) => !s.target_allergens.includes(a));
    let change = "";
    if (added.length) change += added.map((a) => `<span class="tag new">+${esc(a)}</span>`).join("");
    if (removed.length) change += removed.map((a) => `<span class="tag muted">−${esc(a)}</span>`).join("");
    if (!change) change = '<span class="tag muted">过敏原无变化</span>';
    return `
    <tr>
      <td class="code-mono">${esc(s.source_code)}<br><small>${esc(s.source_name)}</small></td>
      <td class="arrow">→</td>
      <td class="code-mono">${esc(s.target_code)}<br><small>${esc(s.target_name)}</small></td>
      <td>${change}</td>
      <td>${esc(s.note)}</td>
      <td><button class="btn tiny danger" data-act="del-sub" data-id="${s.id}">删除</button></td>
    </tr>`;
  }).join("");
}

$("#form-substitution").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    source_code: $("#sub-source").value,
    target_code: $("#sub-target").value,
    note: $("#sub-note").value.trim(),
  };
  try {
    await api("/api/substitutions", { method: "POST", body });
    toast("替代边已添加");
    $("#sub-note").value = "";
    await loadSubstitutions();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#tbody-substitutions").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn || btn.dataset.act !== "del-sub") return;
  if (!confirm("确定删除该替代关系？")) return;
  try {
    await api(`/api/substitutions/${btn.dataset.id}`, { method: "DELETE" });
    toast("已删除");
    await loadSubstitutions();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------------- 配方版本 ----------------

async function loadRecipes() {
  state.recipes = await api("/api/recipes");
  const tbody = $("#tbody-recipes");
  if (!state.recipes.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无配方版本</td></tr>';
    return;
  }
  tbody.innerHTML = state.recipes.map((r) => `
    <tr>
      <td class="code-mono">${esc(r.code)}</td>
      <td>${esc(r.version)}</td>
      <td>${esc(r.name)}</td>
      <td>${r.item_count}</td>
      <td><small>${esc(r.created_at)}</small></td>
      <td>
        <button class="btn tiny" data-act="edit-rec" data-id="${r.id}">编辑</button>
        <button class="btn tiny danger" data-act="del-rec" data-id="${r.id}">删除</button>
      </td>
    </tr>`).join("");
}

function itemRowHtml(item = { ingredient_code: "", quantity: "", unit: "" }) {
  const opts = state.ingredients
    .map((i) => `<option value="${esc(i.code)}" ${i.code === item.ingredient_code ? "selected" : ""}>${esc(i.code)} · ${esc(i.name)}</option>`)
    .join("");
  return `
    <tr>
      <td><select class="item-code">${opts}</select></td>
      <td><input class="item-qty" type="number" step="any" value="${item.quantity ?? ""}" style="width:90px" /></td>
      <td><input class="item-unit" value="${esc(item.unit || "")}" style="width:80px" placeholder="g" /></td>
      <td><button type="button" class="btn tiny danger item-del">移除</button></td>
    </tr>`;
}

$("#btn-add-item").addEventListener("click", () => {
  $("#tbody-items").insertAdjacentHTML("beforeend", itemRowHtml());
});

$("#tbody-items").addEventListener("click", (e) => {
  if (e.target.classList.contains("item-del")) e.target.closest("tr").remove();
});

$("#btn-new-recipe").addEventListener("click", () => {
  $("#form-recipe").classList.remove("hidden");
  $("#rec-id").value = "";
  ["rec-code", "rec-version", "rec-name", "rec-note"].forEach((id) => ($("#" + id).value = ""));
  $("#tbody-items").innerHTML = itemRowHtml();
  $("#form-recipe").scrollIntoView({ behavior: "smooth" });
});

$("#btn-cancel-recipe").addEventListener("click", () => $("#form-recipe").classList.add("hidden"));

function collectRecipeBody() {
  const items = $$("#tbody-items tr").map((tr) => ({
    ingredient_code: tr.querySelector(".item-code").value,
    quantity: tr.querySelector(".item-qty").value === "" ? null : Number(tr.querySelector(".item-qty").value),
    unit: tr.querySelector(".item-unit").value.trim(),
  })).filter((x) => x.ingredient_code);
  return {
    code: $("#rec-code").value.trim(),
    version: $("#rec-version").value.trim(),
    name: $("#rec-name").value.trim(),
    note: $("#rec-note").value.trim(),
    items,
  };
}

$("#form-recipe").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = collectRecipeBody();
  const id = $("#rec-id").value;
  try {
    if (id) {
      await api(`/api/recipes/${id}`, { method: "PUT", body });
      toast("配方已更新");
    } else {
      await api("/api/recipes", { method: "POST", body });
      toast("配方已创建");
    }
    $("#form-recipe").classList.add("hidden");
    await loadRecipes();
    fillRecipeSelects();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#tbody-recipes").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.act === "edit-rec") {
    const rec = await api(`/api/recipes/${id}`);
    $("#form-recipe").classList.remove("hidden");
    $("#rec-id").value = rec.id;
    $("#rec-code").value = rec.code;
    $("#rec-version").value = rec.version;
    $("#rec-name").value = rec.name;
    $("#rec-note").value = rec.note;
    $("#tbody-items").innerHTML = rec.items.length
      ? rec.items.map((it) => itemRowHtml(it)).join("")
      : itemRowHtml();
    $("#form-recipe").scrollIntoView({ behavior: "smooth" });
  } else if (btn.dataset.act === "del-rec") {
    if (!confirm("确定删除该配方版本？")) return;
    try {
      await api(`/api/recipes/${id}`, { method: "DELETE" });
      toast("已删除");
      await loadRecipes();
      fillRecipeSelects();
    } catch (err) {
      toast(err.message, true);
    }
  }
});

// ---------------- 配方下拉 ----------------

function recipeOptions(selectEl, includeAllVersions = true) {
  const groups = {};
  state.recipes.forEach((r) => {
    (groups[r.code] ||= []).push(r);
  });
  selectEl.innerHTML = Object.entries(groups).map(([code, rs]) => `
    <optgroup label="${esc(code)}">
      ${rs.map((r) => `<option value="${r.id}">v${esc(r.version)} · ${esc(r.name)}</option>`).join("")}
    </optgroup>`).join("");
}

function fillRecipeSelects() {
  recipeOptions($("#analyze-recipe"));
  recipeOptions($("#cmp-a"));
  recipeOptions($("#cmp-b"));
  if (state.recipes.length >= 2) {
    $("#cmp-a").selectedIndex = 0;
    $("#cmp-b").selectedIndex = 1;
  }
}

// ---------------- 过敏原分析渲染 ----------------

function renderPath(path) {
  const nodeSpans = path.nodes.map((n, idx) => {
    const isLast = idx === path.nodes.length - 1;
    const repeat = path.cyclic && isLast;
    const cls = ["path-node"];
    if (isLast && !path.cyclic) cls.push("terminal");
    if (repeat) cls.push("repeat");
    const al = n.allergens.length ? `<span class="al">[${esc(n.allergens.join(","))}]</span>` : "";
    return `<span class="${cls.join(" ")}">${esc(n.code)}${al}</span>`;
  });
  let html = nodeSpans[0] || "";
  for (let i = 1; i < path.nodes.length; i++) {
    html += `<span class="path-edge"> → </span>` + nodeSpans[i];
  }

  const meta = [];
  meta.push(`<span class="path-id">#${path.id}</span>`);
  if (path.cyclic) meta.push(`<span class="badge cycle">循环! 回到 ${esc(path.cycle_node)}</span>`);
  if (path.missing) meta.push(`<span class="badge missing">缺失节点</span>`);
  if (path.truncated) meta.push(`<span class="badge missing">已达深度上限</span>`);
  if (path.terminal) meta.push(`<span class="tag ok">终止路径</span>`);
  if (path.new_allergens.length) meta.push(`<span class="tag new">经替代引入: ${esc(path.new_allergens.join(", "))}</span>`);

  return `<div class="path-line ${path.cyclic ? "cyclic" : path.missing ? "missing" : ""}">
    ${html}<span class="path-meta">${meta.join(" ")}</span></div>`;
}

function renderAnalysis(data) {
  const r = data.recipe;
  let html = `
    <div class="summary-bar">
      <div class="summary-box"><h4>配方</h4><strong>${esc(r.code)}</strong> v${esc(r.version)}<br><small>${esc(r.name)}</small></div>
      <div class="summary-box"><h4>直接过敏原</h4>${allergenTags(data.direct_allergens, "direct")}</div>
      <div class="summary-box"><h4>含替代链的全部过敏原</h4>${allergenTags(data.total_allergens, "allergen")}</div>
      <div class="summary-box"><h4>替代新引入</h4>${allergenTags(data.introduced_allergens, "new")}</div>
    </div>`;

  if (data.has_cycles) {
    html += `<div class="alert cycle"><strong>⚠ 检测到 ${data.cycles.length} 条循环替代路径：</strong><ul>`;
    html += data.cycles.map((c) =>
      `<li>路径 #${c.path_id}（起点 <span class="code-mono">${esc(c.root)}</span>）在 <span class="code-mono">${esc(c.cycle_node)}</span> 处成环：${c.nodes.map(esc).join(" → ")}</li>`
    ).join("");
    html += `</ul></div>`;
  }
  if (data.missing_nodes.length) {
    html += `<div class="alert warn"><strong>⚠ 存在缺失原料节点：</strong>${data.missing_nodes.map(esc).join("、")}（请在原料库补录）</div>`;
  }

  for (const root of data.roots) {
    const cyclicPaths = root.paths.filter((p) => p.cyclic).length;
    html += `<div class="root-block ${root.has_cycle ? "has-cycle" : ""}">
      <div class="root-title">
        <span class="code-mono">${esc(root.ingredient_code)}</span>
        <span>${esc(root.ingredient_name)}${root.quantity != null ? ` · ${root.quantity}${esc(root.unit || "")}` : ""}</span>
        ${root.has_cycle ? `<span class="badge cycle">含 ${cyclicPaths} 条循环路径</span>` : ""}
        ${!root.exists ? `<span class="badge missing">原料缺失</span>` : ""}
      </div>
      <div style="margin:6px 0">
        直接：${allergenTags(root.direct_allergens, "direct")}
        替代链总计：${allergenTags(root.total_allergens, "allergen")}
        ${root.introduced_allergens.length ? `新引入：${allergenTags(root.introduced_allergens, "new")}` : ""}
      </div>`;

    // 过敏原 -> 受影响路径表
    const idxEntries = Object.entries(root.allergen_index);
    if (idxEntries.length) {
      html += `<details><summary>过敏原受影响路径索引（${idxEntries.length} 种过敏原）</summary>
        <table class="data-table allergen-table" style="margin-top:8px">
          <thead><tr><th>过敏原</th><th>携带该过敏原的路径</th><th>路径数</th></tr></thead><tbody>`;
      html += idxEntries.map(([al, refs]) => `
        <tr>
          <td><span class="tag allergen">${esc(al)}</span></td>
          <td>${refs.map((p) => `<span class="tag ${p.via_substitution ? "new" : "direct"}">#${p.path_id} ${p.chain.map(esc).join("→")}${p.via_substitution ? `（由 ${esc(p.introduced_by)} 引入）` : ""}</span>`).join(" ")}</td>
          <td>${refs.length}</td>
        </tr>`).join("");
      html += `</tbody></table></details>`;
    }

    html += `<div class="path-list">${root.paths.map(renderPath).join("")}</div></div>`;
  }

  $("#analyze-result").innerHTML = html;
}

$("#btn-analyze").addEventListener("click", async () => {
  const id = $("#analyze-recipe").value;
  if (!id) return toast("请先选择配方", true);
  try {
    const data = await api(`/api/recipes/${id}/analyze`);
    renderAnalysis(data);
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------------- 版本对比 ----------------

$("#btn-compare").addEventListener("click", async () => {
  const a = $("#cmp-a").value;
  const b = $("#cmp-b").value;
  if (!a || !b) return toast("请选择两个配方版本", true);
  if (a === b) return toast("请选择不同的两个版本", true);
  try {
    const data = await api("/api/compare", {
      method: "POST",
      body: { recipe_a: { id: Number(a) }, recipe_b: { id: Number(b) } },
    });
    renderCompare(data);
  } catch (err) {
    toast(err.message, true);
  }
});

function diffItemRows(items, kind) {
  return items.map((x) => `
    <tr class="row-${kind === "added" ? "added" : "removed"}">
      <td class="code-mono">${esc(x.ingredient_code)}</td>
      <td>${esc(x.ingredient_name)}</td>
      <td>${x.quantity ?? ""}${esc(x.unit || "")}</td>
      <td>${allergenTags(x.direct_allergens, "direct")}</td>
      <td>${allergenTags(x.total_allergens, "allergen")}</td>
    </tr>`).join("");
}

function renderCompare(d) {
  let html = `
    <div class="summary-bar">
      <div class="summary-box"><h4>版本 A</h4><strong>${esc(d.recipe_a.code)}</strong> v${esc(d.recipe_a.version)}<br><small>${esc(d.recipe_a.name)}</small></div>
      <div class="summary-box"><h4>版本 B</h4><strong>${esc(d.recipe_b.code)}</strong> v${esc(d.recipe_b.version)}<br><small>${esc(d.recipe_b.name)}</small></div>
      <div class="summary-box"><h4>直接过敏原：新增 / 移除</h4>
        <div>新增 ${allergenTags(d.direct_allergens.added, "new")}</div>
        <div>移除 ${allergenTags(d.direct_allergens.removed, "ok")}</div>
      </div>
      <div class="summary-box"><h4>含替代链：新增 / 移除</h4>
        <div>新增 ${allergenTags(d.total_allergens.added, "new")}</div>
        <div>移除 ${allergenTags(d.total_allergens.removed, "ok")}</div>
      </div>
    </div>`;

  if (d.cycles.a.length || d.cycles.b.length) {
    html += `<div class="alert cycle">循环：A 版 ${d.cycles.a.length} 条，B 版 ${d.cycles.b.length} 条（可在分析页查看具体路径）</div>`;
  }
  if (d.missing_nodes.length) {
    html += `<div class="alert warn">缺失原料节点：${d.missing_nodes.map(esc).join("、")}</div>`;
  }

  html += `<div class="diff-section"><h3>原料变化</h3>
    <table class="data-table">
      <thead><tr><th>代码</th><th>名称</th><th>用量</th><th>直接过敏原</th><th>替代链过敏原</th></tr></thead>
      <tbody>
        <tr><td colspan="5" class="tag ok" style="border:none">B 版新增</td></tr>
        ${diffItemRows(d.items.added, "added") || '<tr><td colspan="5" class="empty">无</td></tr>'}
        <tr><td colspan="5" class="tag muted" style="border:none">A 版移除（B 版不再使用）</td></tr>
        ${diffItemRows(d.items.removed, "removed") || '<tr><td colspan="5" class="empty">无</td></tr>'}
      </tbody>
    </table></div>`;

  if (d.items.changed.length) {
    html += `<div class="diff-section"><h3>用量变化</h3>
      <table class="data-table"><thead><tr><th>原料</th><th>A 用量</th><th>B 用量</th></tr></thead><tbody>`;
    html += d.items.changed.map((c) => `
      <tr><td class="code-mono">${esc(c.ingredient_code)} · ${esc(c.ingredient_name)}</td>
      <td>${c.from.quantity ?? ""}${esc(c.from.unit || "")}</td>
      <td>${c.to.quantity ?? ""}${esc(c.to.unit || "")}</td></tr>`).join("");
    html += `</tbody></table></div>`;
  }

  $("#compare-result").innerHTML = html;
}

// ---------------- 导入 / 导出 / 示例 ----------------

$("#btn-export").addEventListener("click", async () => {
  const data = await api("/api/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `allergen-tracker-export-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  $("#io-message").textContent = "已导出 " + JSON.stringify({
    ingredients: data.ingredients.length,
    recipes: data.recipes.length,
    substitutions: data.substitutions.length,
  });
});

$("#import-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => { $("#import-text").value = reader.result; };
  reader.readAsText(file);
});

$("#btn-import").addEventListener("click", async () => {
  const text = $("#import-text").value.trim();
  if (!text) return toast("请先粘贴或选择 JSON 文件", true);
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    return toast("JSON 解析失败", true);
  }
  const mode = $("#import-mode").value;
  if (mode === "replace" && !confirm("replace 模式将清空现有全部数据，确定继续？")) return;
  try {
    const result = await api(`/api/import?mode=${mode}`, { method: "POST", body: payload });
    $("#io-message").textContent = "导入完成：" + JSON.stringify(result, null, 2);
    toast("导入完成");
    await refreshAll();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#btn-seed").addEventListener("click", async () => {
  try {
    const counts = await api("/api/seed", { method: "POST" });
    $("#io-message").textContent = "示例数据已载入：" + JSON.stringify(counts, null, 2);
    toast("示例数据已载入");
    await refreshAll();
  } catch (err) {
    toast(err.message, true);
  }
});

// ---------------- 启动 ----------------

refreshAll().catch((err) => toast("初始化失败: " + err.message, true));
