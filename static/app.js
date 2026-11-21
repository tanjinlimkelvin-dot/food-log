"use strict";
// Food log client. Every data string reaching innerHTML goes through esc(); CSP blocks inline script.
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const ICON = {
  star: '<path d="M12 3.8l2.5 5.2 5.7.8-4.1 4 1 5.6L12 16.7l-5.1 2.7 1-5.6-4.1-4 5.7-.8z"/>',
  left: '<path d="M14.5 6 8.5 12l6 6"/>', right: '<path d="M9.5 6l6 6-6 6"/>',
  cam: '<path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.8l1.4-2h4.6l1.4 2h1.8A2.5 2.5 0 0 1 20 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z"/><circle cx="12" cy="13" r="3.6"/>',
  spark: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>', bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
};
const ic = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[n]}</svg>`;

const NUM = ["kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "cholesterol_mg", "fiber_g", "soluble_fiber_g", "sugar_g", "sodium_mg"];
const LABEL = { kcal: "kcal", protein_g: "Protein g", carbs_g: "Carbs g", fat_g: "Fat g", sat_fat_g: "Sat fat g", cholesterol_mg: "Chol mg",
  fiber_g: "Fibre g", soluble_fiber_g: "Sol. fibre g", sugar_g: "Sugar g", sodium_mg: "Sodium mg" };
const MEALS = ["breakfast", "lunch", "dinner", "snack", "drink", "supplement"];
const MEAL_EMOJI = { breakfast: "🍳", lunch: "🍱", dinner: "🍲", snack: "🍪", drink: "🥤", supplement: "💊" };

const state = { tab: "today", date: null, month: null, months: {}, targets: null, today: null, models: null, foods: null };
const ls = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
             set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };

// ---------- utils ----------
const n0 = v => Math.round(+v || 0).toLocaleString("en-US");
const n1 = v => { const x = Math.round((+v || 0) * 10) / 10; return Number.isInteger(x) ? String(x) : x.toFixed(1); };
const sum = (rows, k) => rows.reduce((s, r) => s + (+r[k] || 0), 0);
const totals = rows => Object.fromEntries(NUM.map(k => [k, sum(rows, k)]));
const parseD = d => { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd); };
const isoD = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
const addDays = (d, n) => { const x = parseD(d); x.setDate(x.getDate() + n); return isoD(x); };
const dayName = d => {
  if (d === state.today) return "Today";
  if (d === addDays(state.today, -1)) return "Yesterday";
  return parseD(d).toLocaleDateString("en-GB", { weekday: "long" });
};
const longDate = d => parseD(d).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
const shortDate = d => parseD(d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const monthName = m => parseD(m + "-01").toLocaleDateString("en-GB", { month: "long", year: "numeric" });
const mealByClock = () => { const h = new Date().getHours(); return h < 11 ? "breakfast" : h < 15 ? "lunch" : h < 18 ? "snack" : h < 22 ? "dinner" : "snack"; };

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Food": "1" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, { credentials: "same-origin", ...opt });
  let j = {};
  try { j = await r.json(); } catch { /* non-JSON */ }
  if (r.status === 401) location.replace("/");   // signed out -> the server shows the sign-in page
  if (!r.ok) throw Object.assign(new Error(j.error || (r.status === 401 ? "Signed out" : `HTTP ${r.status}`)), { status: r.status });
  return j;
}

let toastT;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 2600);
}

async function loadMonth(m, force = false) {
  if (!force && state.months[m]) return state.months[m];
  const d = await api(`/api/meals${m ? `?month=${m}` : ""}`);
  state.months[d.month] = d;
  state.targets = d.targets; state.today = d.today;
  return d;
}
function invalidate() { state.months = {}; }
function rowsFor(date) { return (state.months[date.slice(0, 7)]?.rows || []).filter(r => r.date === date); }

// CSP forbids inline style attributes; widths go through CSSOM instead
function applyWidths(root) { $$("[data-w]", root).forEach(el => el.style.setProperty("--w", Math.max(0, Math.min(100, +el.dataset.w)) + "%")); }


// ---------- details sheets ----------
const LIMITS = ["sat_fat_g", "cholesterol_mg", "sodium_mg"];
const SHORT = { sat_fat_g: "Sat fat", cholesterol_mg: "Cholesterol", sodium_mg: "Sodium" };
const GROUPS = [["Energy & macros", ["kcal", "protein_g", "carbs_g", "fat_g"]],
                ["Heart health", ["sat_fat_g", "cholesterol_mg", "soluble_fiber_g"]],
                ["Other", ["fiber_g", "sugar_g", "sodium_mg"]]];
const NAME = { kcal: "Calories", protein_g: "Protein", carbs_g: "Carbs", fat_g: "Fat", sat_fat_g: "Saturated fat", cholesterol_mg: "Cholesterol",
  soluble_fiber_g: "Soluble fibre", fiber_g: "Fibre", sugar_g: "Sugar", sodium_mg: "Sodium" };
const UNIT = k => k === "kcal" ? " kcal" : k.endsWith("_mg") ? " mg" : " g";
const fmt = (k, v) => (k === "kcal" || k.endsWith("_mg") || v >= 30 ? n0(v) : n1(v));

function nutRow(k, v, T) {
  const tgt = T[k], limit = LIMITS.includes(k);
  if (!tgt) return `<div class="nut"><span class="nl">${NAME[k]}</span><span class="nv">${fmt(k, v)}${UNIT(k)}</span></div>`;
  const pct = Math.min(100, v / tgt * 100), over = limit && v > tgt;
  const st = limit ? (over ? `${fmt(k, v - tgt)} over` : `${fmt(k, tgt - v)} left`) : (v >= tgt ? "✓ reached" : `${fmt(k, tgt - v)} to go`);
  return `<div class="nut ${over ? "over" : ""}"><span class="nl">${NAME[k]}</span><span class="nv">${fmt(k, v)}<small> / ${limit ? "≤" : ""}${fmt(k, tgt)}${UNIT(k)}</small></span>
    <span class="nb"><span data-w="${pct.toFixed(1)}"></span></span><span class="ns">${st}</span></div>`;
}

function splitBar(t) {
  const p = t.protein_g * 4, c = t.carbs_g * 4, f = t.fat_g * 9, all = p + c + f;
  if (!all) return "";
  const pc = x => Math.round(x / all * 100);
  return `<div class="split" aria-label="Calories from protein ${pc(p)}%, carbs ${pc(c)}%, fat ${pc(f)}%">
    <div class="sb"><span class="sp" data-w="${pc(p)}"></span><span class="sc" data-w="${pc(c)}"></span><span class="sf" data-w="${pc(f)}"></span></div>
    <div class="sl"><span><i class="sp"></i>Protein ${pc(p)}%</span><span><i class="sc"></i>Carbs ${pc(c)}%</span><span><i class="sf"></i>Fat ${pc(f)}%</span></div></div>`;
}

function openDay(date) {
  const T = state.targets, rows = rowsFor(date), t = totals(rows);
  $("#day-title").textContent = `${dayName(date)} · ${shortDate(date).replace(/^\w+ /, "")}`;
  const body = $("#day-body");
  body.innerHTML = rows.length ? `
    ${splitBar(t)}
    ${GROUPS.map(([g, ks]) => `<h3 class="gh">${g}</h3><div class="panel nuts">${ks.map(k => nutRow(k, t[k], T)).join("")}</div>`).join("")}
    <h3 class="gh">By meal</h3>
    <div class="panel bym"><table><thead><tr><th>Meal</th><th>kcal</th><th>P</th><th>C</th><th>F</th></tr></thead><tbody>
      ${rows.map(r => `<tr data-meal="${r.id}" tabindex="0"><td>${MEAL_EMOJI[r.meal] || ""} ${esc(r.meal)}</td><td>${n0(r.kcal)}</td><td>${n0(r.protein_g)}</td><td>${n0(r.carbs_g)}</td><td>${n0(r.fat_g)}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="hint">Values are estimates. Targets come from profile.json.</p>`
    : `<div class="empty"><b>Nothing logged</b></div>`;
  applyWidths(body);
  $$("[data-meal]", body).forEach(tr => {
    const go2 = () => { $("#day").close(); openMeal(+tr.dataset.meal); };
    tr.onclick = go2; tr.onkeydown = e => { if (e.key === "Enter") go2(); };
  });
  if (!$("#day").open) $("#day").showModal();
}

function openMeal(id) {
  loadFoods().catch(() => {});
  const r = Object.values(state.months).flatMap(m => m.rows).find(x => x.id === id);
  if (!r) return;
  const T = state.targets;
  $("#meal-title").textContent = `${r.meal[0].toUpperCase()}${r.meal.slice(1)} · ${shortDate(r.date)}`;
  const body = $("#meal-body");
  const foods = String(r.foods || "").split(/;\s*/).filter(Boolean);
  const share = k => T[k] ? `<small>${Math.round((+r[k] || 0) / T[k] * 100)}% of day</small>` : "";
  body.innerHTML = `
    ${r.photos?.length ? `<div class="gallery ${r.photos.length > 1 ? "multi" : ""}">${r.photos.map((ph, i) => `<button type="button" class="mphoto" data-ph="${esc(ph)}" aria-label="View photo ${i + 1} of ${r.photos.length}"><img src="/photo/${esc(ph)}" alt="Meal photo ${i + 1}" ${i ? 'loading="lazy"' : ""}></button>`).join("")}</div>
      ${r.photos.length > 1 ? `<p class="hint gcount">${r.photos.length} photos · swipe</p>` : ""}` : ""}
    <div class="mtop"><div class="big">${n0(r.kcal)}<small>kcal</small></div>${share("kcal")}</div>
    <div class="macros">${["protein_g", "carbs_g", "fat_g"].map(k => `<div class="macro"><div class="k">${NAME[k]}</div><div class="v">${fmt(k, +r[k] || 0)}<small>g</small></div>${share(k)}</div>`).join("")}</div>
    ${splitBar(r)}
    <h3 class="gh">Foods${r.items.length ? ` <span class="gx">tap a food for its nutrients</span>` : ""}</h3>
    ${r.items.length ? `<div class="panel foodl">${r.items.map(it => foodHTML(it, r)).join("")}</div>
      ${r.items_stale ? `<p class="hint">The meal total was changed after this breakdown (now ${n0(r.kcal)} kcal vs ${n0(sum(r.items, "kcal"))} kcal in the foods). <button type="button" class="btn link" id="resplit">Redo breakdown</button></p>` : ""}`
    : `<ul class="panel flist">${foods.map(f => `<li>${esc(f)}</li>`).join("")}</ul>
       <button type="button" class="btn sm split-btn" id="split">${ic("spark")}Break down per food</button>`}
    <h3 class="gh">More nutrients</h3>
    <div class="panel nuts">${["sat_fat_g", "cholesterol_mg", "soluble_fiber_g", "fiber_g", "sugar_g", "sodium_mg"].map(k => {
      const v = +r[k] || 0, hot = (k === "sat_fat_g" && v >= 8) || (k === "cholesterol_mg" && v >= 200) || (k === "sodium_mg" && v >= 1000);
      return `<div class="nut ${hot ? "over" : ""}"><span class="nl">${NAME[k]}</span><span class="nv">${r[k] == null ? "—" : fmt(k, v) + UNIT(k)}</span>${T[k] ? `<span class="ns">${Math.round(v / T[k] * 100)}% of ${LIMITS.includes(k) ? "limit" : "target"}</span>` : ""}</div>`;
    }).join("")}</div>
    ${r.note ? `<h3 class="gh">Note</h3><p class="mnote">${esc(r.note)}</p>` : ""}
    <div class="row-actions two"><button type="button" class="btn" id="meal-del">${ic("trash")}Delete</button>
      <button type="button" class="btn" id="meal-edit">Edit</button></div>`;
  applyWidths(body);
  $$("[data-mstar]", body).forEach(b => b.onclick = () => starItem(r.items[+b.dataset.mstar], b));
  $$("[data-ph]", body).forEach(b => b.onclick = () => { $("#viewer-img").src = `/photo/${b.dataset.ph}`; $("#viewer").showModal(); });
  // meals with a per-food breakdown are edited food by food; others in the totals form
  $("#meal-edit").onclick = () => { $("#meal").close(); r.items.length && !r.items_stale ? editFoods(r, r.items, "manual") : openEdit(id); };
  let armed = false;
  $("#meal-del").onclick = async e => {
    const b = e.currentTarget;
    if (!armed) { armed = true; b.classList.add("dark"); b.lastChild.textContent = "Delete?"; return; }
    try { await api("/api/meals/delete", { id }); $("#meal").close(); invalidate(); toast("Deleted"); render(); } catch (x) { toast(x.message); }
  };
  const split = $("#split", body) || $("#resplit", body);
  if (split) split.onclick = async () => {
    split.disabled = true; split.textContent = "Splitting…";
    const model = ls.get("food.chatModel") || ls.get("food.model") || state.models?.default || (await modelsList()).default;
    try { const res = await api("/api/meals/split", { id, model }); $("#meal").close(); editFoods(r, res.items, res.model); }
    catch (x) { toast(x.message); split.disabled = false; split.textContent = "Break down per food"; }
  };
  if (!$("#meal").open) $("#meal").showModal();
}

function foodHTML(it, r) {
  const pct = r.kcal ? Math.round((it.kcal || 0) / r.kcal * 100) : 0;
  const v = k => it[k] == null ? "—" : fmt(k, +it[k]);
  return `<details class="food"><summary>
      <span class="fn"><b>${esc(it.name)}</b>${it.portion ? `<small>${esc(it.portion)}${it.grams ? ` · ${n0(it.grams)} g` : ""}</small>` : ""}</span>
      <span class="fk">${n0(it.kcal)}<small> kcal</small></span>
      <span class="fm">P ${v("protein_g")} · C ${v("carbs_g")} · F ${v("fat_g")}</span>
      <svg class="i chev" viewBox="0 0 24 24" aria-hidden="true">${ICON.right}</svg></summary>
    <div class="fd">
      <div class="fbar"><span data-w="${pct}"></span></div><p class="hint">${pct}% of this meal's calories${it.confidence === "low" ? " · AI was unsure" : ""}</p>
      <button type="button" class="btn sm favline ${isSaved(it.name) ? "on" : ""}" data-mstar="${r.items.indexOf(it)}">${ic("star")}${isSaved(it.name) ? "In My foods · update" : "Save to My foods"}</button>
      <div class="fgrid">${["protein_g", "carbs_g", "fat_g", "sat_fat_g", "cholesterol_mg", "fiber_g", "soluble_fiber_g", "sugar_g", "sodium_mg"].map(k => {
        const hot = (k === "sat_fat_g" && it[k] >= 5) || (k === "cholesterol_mg" && it[k] >= 150) || (k === "sodium_mg" && it[k] >= 600);
        return `<div class="${hot ? "hot" : ""}"><small>${NAME[k]}</small><b>${v(k)}<i>${UNIT(k).trim()}</i></b></div>`; }).join("")}</div>
    </div></details>`;
}

function editFoods(r, items, model) {
  Object.assign(add, { editId: r.id, keepNote: r.note || "", images: [], previews: (r.photos || []).map(x => `/photo/${x}`),
    date: r.date, meal: r.meal, mealTouched: true, text: r.foods,
    draft: { model, items: items.map(it => ({ portion: "", confidence: "high", ...it, portion: it.portion || "",
      ...Object.fromEntries(NUM.map(k => [k, +it[k] || 0])) })), assumptions: "", tip: "" },
    onSaved: () => { render(); } });
  paintReview();
  $("#add").showModal();
}

// ---------- My foods ----------
async function loadFoods() {
  if (!state.foods) state.foods = (await api("/api/foods")).foods;
  return state.foods;
}
const isSaved = name => !!state.foods?.some(f => f.name.toLowerCase() === String(name || "").trim().toLowerCase());

async function starItem(it, btn) {
  if (!it?.name?.trim()) { toast("Name the food first"); return; }
  const base = it.base || it, sv = it.base ? it.servings : 1;
  const payload = { name: it.name.trim(), portion: it.base ? base.portion : it.portion, grams: (+it.grams || 0) / sv,
    ...Object.fromEntries(NUM.map(k => [k, Math.round((+(it.base ? base[k] : it[k]) || 0) * 10) / 10])) };
  btn.disabled = true;
  try {
    const r = await api("/api/foods/save", payload);
    state.foods = r.foods;
    btn.classList.add("on"); btn.setAttribute("aria-pressed", "true");
    toast(`★ ${payload.name} saved to My foods`);
  } catch (e) { toast(e.message); }
  btn.disabled = false;
}

function setServings(it, n) {
  it.servings = n;
  for (const k of NUM) it[k] = Math.round((+it.base[k] || 0) * n * 10) / 10;
  it.grams = (+it.base.grams || 0) * n;
  it.portion = n === 1 ? (it.base.portion || "") : `${n} × ${it.base.portion || "serving"}`;
}
function fromSaved(f) {
  const it = { name: f.name, confidence: "high", saved_id: f.id, base: { ...f }, servings: 1 };
  setServings(it, 1);
  return it;
}

function quickAdd(f) {
  if (!f) return;
  Object.assign(add, { editId: null, keepNote: "", images: [], previews: [], date: state.today, meal: mealByClock(), mealTouched: true, text: "",
    draft: { model: "manual", items: [fromSaved(f)], assumptions: "", tip: "" },
    onSaved: () => go("today") });
  paintReview();
  $("#add").showModal();
}

// Picker sheet: tap a food to use it; "Edit" mode removes foods.
async function openFoods(onPick) {
  const dlg = $("#foods"), body = $("#foods-body");
  let editing = false, q = "";
  const paint = () => {
    const list = (state.foods || []).filter(f => !q || f.name.toLowerCase().includes(q));
    body.innerHTML = `${state.foods?.length ? `<input class="field" id="fq" type="search" placeholder="Search My foods" value="${esc(q)}" aria-label="Search My foods">` : ""}
      ${list.length ? `<div class="panel favl">${list.map(f => `<div class="fav-r">
          <button type="button" class="fpick" data-pick="${f.id}" ${editing ? "disabled" : ""}><span class="fn"><b>${esc(f.name)}</b><small>${esc(f.portion || "1 serving")}${f.uses ? ` · used ${f.uses}×` : ""}</small></span>
            <span class="fk">${n0(f.kcal)}<small> kcal</small></span><span class="fm">P ${fmt("protein_g", +f.protein_g || 0)} · C ${fmt("carbs_g", +f.carbs_g || 0)} · F ${fmt("fat_g", +f.fat_g || 0)}${f.sat_fat_g >= 5 ? ` · <span class="hot">sat ${n1(f.sat_fat_g)}</span>` : ""}</span></button>
          ${editing ? `<button type="button" class="rm" data-del="${f.id}" aria-label="Remove ${esc(f.name)}">${ic("trash")}</button>` : ""}</div>`).join("")}</div>`
        : `<div class="empty"><b>${q ? "No match" : "No saved foods yet"}</b>${q ? "" : "Tap ☆ on any food (in Review meal, an AI card, or a meal’s details) to save it here."}</div>`}
      ${state.foods?.length ? `<button type="button" class="btn link" id="fedit">${editing ? "Done" : "Edit list"}</button>` : ""}`;
    const fq = $("#fq", body);
    if (fq) fq.oninput = e => { q = e.target.value.trim().toLowerCase(); const pos = e.target.selectionStart; paint(); const n = $("#fq", body); n.focus(); n.setSelectionRange(pos, pos); };
    $$("[data-pick]", body).forEach(b => b.onclick = () => { dlg.close(); onPick(state.foods.find(f => f.id === +b.dataset.pick)); });
    $$("[data-del]", body).forEach(b => b.onclick = async () => {
      try { state.foods = (await api("/api/foods/delete", { id: +b.dataset.del })).foods; paint(); } catch (e) { toast(e.message); }
    });
    const fe = $("#fedit", body); if (fe) fe.onclick = () => { editing = !editing; paint(); };
  };
  try { await loadFoods(); } catch (e) { toast(e.message); return; }
  paint();
  if (!dlg.open) dlg.showModal();
}

// ---------- Today ----------
function macroCard(k, label, v, target, kind, unit) {
  const pct = Math.min(100, (v / target) * 100);
  const over = kind === "limit" && v > target;
  const good = kind === "limit" ? v > 0 && !over : pct >= 90;
  return `<div class="macro ${over ? "over" : good ? "good" : ""}">
    <div class="k"><span class="dot"></span>${label}</div>
    <div class="v">${k === "cholesterol_mg" || k === "kcal" || v >= 30 ? n0(v) : n1(v)}<small>${kind === "limit" ? "/≤" : "/"}${target}${unit}</small></div>
    <div class="bar"><span data-w="${pct.toFixed(1)}"></span></div></div>`;
}

function mealRow(r) {
  const thumb = r.photo ? `<img class="thumb" src="/photo/${esc(r.photo)}" alt="" loading="lazy" decoding="async">`
                        : `<span class="thumb" aria-hidden="true">${MEAL_EMOJI[r.meal] || "🍽"}</span>`;
  const flag = (+r.sat_fat_g || 0) >= 8 ? "⚠ sat fat" : (+r.cholesterol_mg || 0) >= 200 ? "⚠ chol" : "";
  return `<button type="button" class="meal" data-meal="${r.id}">${thumb}
    <span class="body"><span class="t"><span class="name">${esc(r.meal)}</span><span class="kc">${n0(r.kcal)} kcal</span></span>
    <span class="f">${esc(r.foods)}</span>
    <span class="m">P ${n0(r.protein_g)}g · C ${n0(r.carbs_g)}g · F ${n0(r.fat_g)}g${flag ? ` · <span class="hot">${flag}</span>` : ""}</span></span>
    <svg class="i chev" viewBox="0 0 24 24" aria-hidden="true">${ICON.right}</svg></button>`;
}

async function renderToday() {
  await loadMonth(state.date ? state.date.slice(0, 7) : "");
  if (!state.date) state.date = state.today;
  await loadMonth(state.date.slice(0, 7));
  const T = state.targets, rows = rowsFor(state.date), t = totals(rows);
  const pct = Math.min(100, t.kcal / T.kcal * 100);
  const left = T.kcal - t.kcal;
  const overs = LIMITS.filter(k => t[k] > T[k]).map(k => SHORT[k]);
  view.innerHTML = `
    <div class="day">
      <div><h2>${esc(dayName(state.date))}</h2><p class="sub">${esc(longDate(state.date))}</p></div>
      <div class="nav">
        <button type="button" class="icon-btn" data-day="-1" aria-label="Previous day">${ic("left")}</button>
        <button type="button" class="icon-btn" data-day="1" aria-label="Next day" ${state.date >= state.today ? "disabled" : ""}>${ic("right")}</button>
      </div>
    </div>
    <section class="panel hero" id="hero" aria-label="Day totals">
      <div class="label"><span>Calories</span><span class="left">${left >= 0 ? `${n0(left)} left` : `${n0(-left)} over`}</span></div>
      <div class="big">${n0(t.kcal)}<small>/ ${n0(T.kcal)} kcal</small></div>
      <div class="meter" role="meter" aria-valuemin="0" aria-valuemax="${T.kcal}" aria-valuenow="${Math.round(t.kcal)}" aria-label="Calories"><span data-w="${pct.toFixed(1)}"></span></div>
      <div class="macros">
        ${macroCard("protein_g", "Protein", t.protein_g, T.protein_g, "target", "g")}
        ${macroCard("carbs_g", "Carbs", t.carbs_g, T.carbs_g, "target", "g")}
        ${macroCard("fat_g", "Fat", t.fat_g, T.fat_g, "target", "g")}
      </div>
      <button type="button" class="more" id="day-more">
        <span>All nutrients</span>${overs.length ? `<span class="warn">${esc(overs.join(" · "))} over</span>` : rows.length ? `<span class="ok">Limits OK</span>` : ""}
        <svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICON.right}</svg></button>
    </section>
    <section class="section">
      <div class="section-h"><h2>Meals</h2><span class="aside">${rows.length ? `${rows.length} logged · ${left >= 0 ? n0(left) + " kcal to go" : n0(-left) + " kcal over"}` : ""}</span></div>
      <div class="panel meals">${rows.length ? rows.map(mealRow).join("") :
        `<div class="empty"><b>Nothing logged ${state.date === state.today ? "yet" : "this day"}</b>Tap the camera in the middle — the AI drafts the numbers.<br>
         <button type="button" class="btn primary" data-add>${ic("cam")}Snap a meal</button></div>`}</div>
    </section>`;
  $$("[data-day]").forEach(b => b.onclick = () => go("today", addDays(state.date, +b.dataset.day)));
  $$("[data-meal]").forEach(b => b.onclick = () => openMeal(+b.dataset.meal));
  $("#hero").onclick = () => openDay(state.date);   // whole card; the button inside is the accessible target
  $$("[data-add]").forEach(b => b.onclick = () => go("ask"));
}

// ---------- History ----------
function md(text) {
  const inline = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  return String(text || "").split(/\n{2,}/).map(block => {
    const lines = block.split("\n").filter(l => l.trim());
    if (!lines.length) return "";
    if (lines.length >= 2 && lines.every(l => /^\s*\|.*\|\s*$/.test(l)) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[1])) {
      const cells = l => l.trim().slice(1, -1).split("|").map(c => inline(c.trim()));
      return `<table><thead><tr>${cells(lines[0]).map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${
        lines.slice(2).map(l => `<tr>${cells(l).map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(block)) return "";
    if (lines.some(l => /^\s*[-*]\s+/.test(l)) && lines.every(l => /^\s*([-*]\s+|\s{2,})/.test(l))) return `<ul>${lines.map(l => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    if (lines.every(l => /^\s*[-*]\s+/.test(l))) return `<ul>${lines.map(l => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    const h = block.match(/^#{1,4}\s+(.*)/);
    if (h && lines.length === 1) return `<h3>${inline(h[1])}</h3>`;
    return `<p>${inline(block.replace(/^#{1,4}\s+/, "")).replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

async function renderHistory() {
  const d = await loadMonth(state.month || "");
  state.month = d.month;
  const T = d.targets;
  const days = {};
  d.rows.forEach(r => (days[r.date] ||= []).push(r));
  const dates = Object.keys(days).sort().reverse();
  const per = dates.map(x => totals(days[x]));
  const avg = k => per.length ? per.reduce((s, t) => s + t[k], 0) / per.length : 0;
  const satOver = per.filter(t => t.sat_fat_g > T.sat_fat_g).length;
  const i = d.months.indexOf(d.month);
  view.innerHTML = `
    <div class="month">
      <h2>${esc(monthName(d.month))}</h2>
      <div class="nav">
        <button type="button" class="icon-btn" data-m="${esc(d.months[i - 1] || "")}" aria-label="Previous month" ${i > 0 ? "" : "disabled"}>${ic("left")}</button>
        <button type="button" class="icon-btn" data-m="${esc(d.months[i + 1] || "")}" aria-label="Next month" ${i < d.months.length - 1 ? "" : "disabled"}>${ic("right")}</button>
      </div>
    </div>
    ${dates.length ? `
    <div class="avg">
      ${macroCard("kcal", "Avg kcal", avg("kcal"), T.kcal, "target", "")}
      ${macroCard("protein_g", "Avg protein", avg("protein_g"), T.protein_g, "target", "g")}
      ${macroCard("sat_fat_g", "Avg sat fat", avg("sat_fat_g"), T.sat_fat_g, "limit", "g")}
    </div>
    <p class="hint">${dates.length} day${dates.length > 1 ? "s" : ""} logged · sat fat over ${T.sat_fat_g} g on ${satOver} day${satOver === 1 ? "" : "s"}. Days with only some meals logged pull averages down.</p>` : ""}
    <section class="section">
      <div class="section-h"><h2>Days</h2></div>
      <div class="panel days">${dates.length ? dates.map((x, j) => {
        const t = per[j];
        return `<a class="row" href="#today/${x}"><span class="d">${esc(shortDate(x))}</span><span class="kc">${n0(t.kcal)} kcal</span>
          <span class="s"><span>${days[x].length} meal${days[x].length > 1 ? "s" : ""}</span><span>P ${n0(t.protein_g)}g</span>
          <span class="${t.sat_fat_g > T.sat_fat_g ? "hot" : ""}">sat ${n1(t.sat_fat_g)}g</span><span class="${t.cholesterol_mg > T.cholesterol_mg ? "hot" : ""}">chol ${n0(t.cholesterol_mg)}</span>
          <span>sol. fibre ${n1(t.soluble_fiber_g)}g</span></span></a>`;
      }).join("") : `<div class="empty"><b>No meals this month</b></div>`}</div>
    </section>`;
  $$("[data-m]").forEach(b => b.onclick = () => b.dataset.m && go("history", b.dataset.m));
}

// ---------- Add (photo -> AI -> review -> save) ----------
const add = { images: [], previews: [], draft: null, date: null, meal: null, text: "" };
const MAX_PHOTOS = 4;
const raw64 = imgs => imgs?.length ? imgs.map(x => x.split(",")[1]) : undefined;
// iOS photo picker with `multiple` lets you tick several photos at once
async function readPhotos(files, have = 0) {
  const list = [...(files || [])].slice(0, MAX_PHOTOS - have);
  if (files?.length > list.length) toast(`Up to ${MAX_PHOTOS} photos per meal`);
  return Promise.all(list.map(shrinkImage));
}
const strip = (imgs, rmAttr) => `<div class="pstrip">${imgs.map((src, i) => `<span class="pt"><img src="${esc(src)}" alt="Photo ${i + 1}">${rmAttr ?
  `<button type="button" ${rmAttr}="${i}" aria-label="Remove photo ${i + 1}">${ic("close")}</button>` : ""}</span>`).join("")}</div>`;

async function shrinkImage(file) {
  let src;
  try { src = await createImageBitmap(file); }
  catch {
    src = await new Promise((resolve, reject) => {
      const img = new Image(), url = URL.createObjectURL(file);
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Couldn’t read that image — try a JPEG")); };
      img.src = url;
    });
  }
  const scale = Math.min(1, 1280 / Math.max(src.width, src.height));
  const cv = document.createElement("canvas");
  cv.width = Math.round(src.width * scale); cv.height = Math.round(src.height * scale);
  cv.getContext("2d").drawImage(src, 0, 0, cv.width, cv.height);
  if (src.close) src.close();
  return cv.toDataURL("image/jpeg", 0.85);
}

async function modelsList() {
  if (!state.models) { try { state.models = await api("/api/models"); } catch { state.models = { default: "", models: [] }; } }
  return state.models;
}

function segHTML(name, value) {
  return `<div class="seg" role="group" aria-label="Meal">${MEALS.map(m => `<button type="button" data-${name}="${m}" aria-pressed="${m === value}">${MEAL_EMOJI[m]} ${m}</button>`).join("")}</div>`;
}
function wireSeg(root, name, set) {
  $$(`[data-${name}]`, root).forEach(b => b.onclick = () => {
    $$(`[data-${name}]`, root).forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    set(b.dataset[name]);
  });
}

function openAdd() {
  Object.assign(add, { editId: null, keepNote: "", job: null, onSaved: null, mealTouched: false, images: [], previews: [], draft: null, text: "", date: state.date || state.today, meal: mealByClock() });
  paintInput();
  $("#add").showModal();
}

async function paintInput(err = "") {
  const body = $("#add-body");
  const M = await modelsList();
  const chosen = M.models.includes(ls.get("food.model")) ? ls.get("food.model") : M.default;
  body.innerHTML = `
    ${add.images.length ? strip(add.images, "data-unph") : ""}
    ${add.images.length < MAX_PHOTOS ? `<label class="pick ${add.images.length ? "slim" : ""}">
      <span class="ic">${ic("cam")}</span><span><b>${add.images.length ? "Add another photo" : "Take or choose photos"}</b><br><small>Plate, packaging or nutrition label · up to ${MAX_PHOTOS}</small></span>
      <input type="file" id="photo" accept="image/*" multiple aria-label="Meal photos">
    </label>` : ""}
    <div class="gap"><label class="lbl" for="hint">Anything the photo can’t show? <span class="muted">(optional with a photo)</span></label>
      <textarea class="field" id="hint" name="hint" rows="2" placeholder="e.g. 2 eggs, kopi-o kosong, half the rice left">${esc(add.text)}</textarea></div>
    <div class="grid2">
      <div><label class="lbl" for="mdate">Date</label><input class="field" type="date" id="mdate" value="${esc(add.date)}" max="${esc(state.today)}"></div>
      <div><label class="lbl" for="msel">Model</label><select class="field" id="msel">${M.models.map(m => `<option ${m === chosen ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
    </div>
    <label class="lbl">Meal</label>
    ${segHTML("am", add.meal)}
    ${err ? `<p class="err" role="alert">${esc(err)}</p>` : ""}
    <div class="row-actions two">
      <button type="button" class="btn" id="manual">Type it in</button>
      <button type="button" class="btn primary" id="go">${ic("spark")}Analyze</button>
    </div>`;
  const ph = $("#photo"); if (ph) ph.onchange = async e => {
    if (!e.target.files.length) return;
    add.text = $("#hint").value;
    try { add.images.push(...await readPhotos(e.target.files, add.images.length)); add.previews = add.images; paintInput(); }
    catch (x) { paintInput(x.message); }
  };
  $$("[data-unph]", body).forEach(b => b.onclick = () => { add.text = $("#hint").value; add.images.splice(+b.dataset.unph, 1); add.previews = add.images; paintInput(); });
  wireSeg(body, "am", v => add.meal = v);
  $("#msel").onchange = e => ls.set("food.model", e.target.value);
  $("#mdate").onchange = e => add.date = e.target.value || state.today;
  $("#manual").onclick = () => {
    add.text = $("#hint").value;
    add.draft = { model: "manual", items: [blankItem($("#hint").value)], assumptions: "", tip: "" };
    paintReview();
  };
  $("#go").onclick = () => { add.text = $("#hint").value.trim(); runAnalyze(); };
}

function blankItem(name = "") { return { name, portion: "", grams: 0, confidence: "high", ...Object.fromEntries(NUM.map(k => [k, 0])) }; }

async function runAnalyze(correction = "") {
  if (!add.images.length && !add.text && !correction) { paintInput("Add a photo or describe the meal first."); return; }
  const body = $("#add-body");
  const model = $("#msel")?.value || ls.get("food.model") || state.models?.default;
  body.innerHTML = `<div class="thinking"><span class="spin" aria-hidden="true"></span><span>Looking at your meal with <b>${esc(model)}</b>…<br><span class="hint">Usually 5–20 seconds.</span></span></div>`;
  const dayRows = rowsFor(add.date);
  try {
    const res = await api("/api/analyze", {
      model, date: add.date, images: raw64(add.images),
      text: correction ? `${add.text}\nCorrection: ${correction}` : add.text,
      today: dayRows.length ? totals(dayRows) : undefined,
      previous: correction && add.draft ? { items: add.draft.items, assumptions: add.draft.assumptions } : undefined,
    });
    if (res.not_food || !res.items.length) { paintInput("That doesn’t look like food. Try another photo or type it in."); return; }
    if (res.meal && !correction && !add.mealTouched) add.meal = res.meal;
    add.draft = res;
    paintReview();
  } catch (e) {
    if (correction && add.draft) paintReview(e.message); else paintInput(e.message);
  }
}

function itemHTML(it, i) {
  const main = ["kcal", "protein_g", "carbs_g", "fat_g"], extra = NUM.filter(k => !main.includes(k));
  const inp = k => `<label>${LABEL[k]}<input inputmode="decimal" data-i="${i}" data-k="${k}" value="${n1(it[k])}" aria-label="${esc(it.name)} ${LABEL[k]}"></label>`;
  return `<div class="item">
    <div class="ih"><input data-i="${i}" data-k="name" value="${esc(it.name)}" aria-label="Food name" placeholder="Food">
      <button type="button" class="rm fav ${isSaved(it.name) ? "on" : ""}" data-star="${i}" aria-label="Save ${esc(it.name)} to My foods" aria-pressed="${isSaved(it.name)}">${ic("star")}</button>
      <button type="button" class="rm" data-rm="${i}" aria-label="Remove ${esc(it.name)}">${ic("close")}</button></div>
    ${it.base ? `<div class="portion serv"><span>${esc(it.base.portion || "1 serving")}</span>
        <span class="step"><button type="button" data-sv="${i}" data-d="-0.5" aria-label="Fewer servings">−</button><b>× ${+it.servings}</b><button type="button" data-sv="${i}" data-d="0.5" aria-label="More servings">+</button></span></div>`
      : `<div class="portion">Portion <input data-i="${i}" data-k="portion" value="${esc(it.portion)}" placeholder="e.g. 1 plate" aria-label="Portion">${it.confidence === "low" ? `<span class="conf low">unsure</span>` : ""}</div>`}
    <div class="nums">${main.map(inp).join("")}</div>
    <details class="more-n"><summary>Sat fat ${n1(it.sat_fat_g)}g · chol ${n0(it.cholesterol_mg)}mg · more</summary>
      <div class="nums">${extra.map(inp).join("")}</div></details>
  </div>`;
}

function paintTotals() {
  const t = totals(add.draft.items);
  const el = $("#rtot");
  if (el) el.innerHTML = `<div class="tot">${n0(t.kcal)}<small>kcal</small></div>
    <div class="mac">P ${n0(t.protein_g)}g · C ${n0(t.carbs_g)}g · F ${n0(t.fat_g)}g</div>
    <div class="mac ${t.sat_fat_g >= 8 ? "hot" : ""}">sat ${n1(t.sat_fat_g)}g · chol ${n0(t.cholesterol_mg)}mg</div>`;
}

function paintReview(err = "") {
  const d = add.draft, body = $("#add-body");
  $("#add-title").textContent = add.editId ? "Edit foods" : "Review meal";
  body.innerHTML = `
    <div class="result-top">${add.previews.length ? `<span class="rimg"><img src="${esc(add.previews[0])}" alt="">${add.previews.length > 1 ? `<b>+${add.previews.length - 1}</b>` : ""}</span>` : ""}<div id="rtot"></div></div>
    <div id="items">${d.items.map(itemHTML).join("")}</div>
    <div class="addrow"><button type="button" class="btn sm" id="additem">${ic("plus")}Add item</button>
      <button type="button" class="btn sm" id="addfav">${ic("star")}My foods</button></div>
    ${d.assumptions ? `<div class="note-box as">${ic("info")}<span>${esc(d.assumptions)}</span></div>` : ""}
    ${d.tip ? `<div class="note-box tip">${ic("bulb")}<span>${esc(d.tip)}</span></div>` : ""}
    ${d.model !== "manual" ? `<label class="lbl gap" for="fix">Something off? Tell the AI</label>
      <div class="refine"><input class="field" id="fix" placeholder="e.g. only 1 egg, no sauce"><button type="button" class="btn" id="refix" aria-label="Re-analyze">${ic("redo")}</button></div>` : ""}
    <div class="grid2"><div><label class="lbl" for="rdate">Date</label><input class="field" type="date" id="rdate" value="${esc(add.date)}" max="${esc(state.today)}"></div>
      <div><label class="lbl">Estimated by</label><div class="field static">${esc(d.model)}</div></div></div>
    <label class="lbl">Meal</label>
    ${segHTML("rm", add.meal)}
    ${err ? `<p class="err" role="alert">${esc(err)}</p>` : ""}
    <div class="row-actions two">
      <button type="button" class="btn" id="back">${add.onSaved ? "Cancel" : "Back"}</button>
      <button type="button" class="btn primary" id="save">${add.editId ? "Save changes" : "Save meal"}</button>
    </div>`;
  paintTotals();
  $("#items").addEventListener("input", e => {
    const t = e.target, i = +t.dataset.i, k = t.dataset.k;
    if (k == null || !d.items[i]) return;
    d.items[i][k] = NUM.includes(k) ? Math.max(0, parseFloat(t.value) || 0) : t.value;
    if (NUM.includes(k) || k === "name") delete d.items[i].base;   // hand-edited: no longer a plain multiple of the saved food
    paintTotals();
  });
  $$("[data-star]", body).forEach(b => b.onclick = () => starItem(d.items[+b.dataset.star], b));
  $$("[data-sv]", body).forEach(b => b.onclick = () => {
    const it = d.items[+b.dataset.sv];
    setServings(it, Math.max(0.5, Math.min(10, it.servings + +b.dataset.d)));
    paintReview();
  });
  $("#addfav").onclick = () => openFoods(f => { d.items = d.items.filter(x => x.name.trim()); d.items.push(fromSaved(f)); paintReview(); });
  $$("[data-rm]", body).forEach(b => b.onclick = () => { d.items.splice(+b.dataset.rm, 1); if (!d.items.length) d.items.push(blankItem()); paintReview(); });
  $("#additem").onclick = () => { d.items.push(blankItem()); paintReview(); $$("#items .ih input").at(-1)?.focus(); };
  wireSeg(body, "rm", v => { add.meal = v; add.mealTouched = true; });
  $("#rdate").onchange = e => add.date = e.target.value || state.today;
  $("#back").onclick = () => add.onSaved ? $("#add").close() : paintInput();
  const fix = $("#fix");
  if (fix) {
    const run = () => fix.value.trim() && runAnalyze(fix.value.trim());
    $("#refix").onclick = run;
    fix.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); run(); } };
  }
  $("#save").onclick = saveDraft;
}

async function saveDraft() {
  const d = add.draft;
  const items = d.items.filter(it => it.name.trim());
  if (!items.length) { paintReview("Add at least one food."); return; }
  const t = totals(items);
  const foods = items.map(it => it.portion ? `${it.name.trim()} (${it.portion.trim()})` : it.name.trim()).join("; ");
  const note = add.editId ? add.keepNote : [d.assumptions, d.model !== "manual" ? `AI: ${d.model}` : ""].filter(Boolean).join(" ");
  const btn = $("#save"); btn.disabled = true; btn.textContent = "Saving…";
  const body = { date: add.date, meal: add.meal, foods, ...t, estimated: true, note, items,
    saved_ids: items.filter(it => it.saved_id).map(it => it.saved_id) };
  try {
    if (add.editId) await api("/api/meals/update", { id: add.editId, ...body });
    else await api("/api/meals/add", { ...body, images: raw64(add.images), job: add.images.length ? undefined : add.job });
    $("#add").close();
    invalidate(); if (body.saved_ids.length) state.foods = null;
    toast(`Saved · ${n0(t.kcal)} kcal`);
    if (add.onSaved) { add.onSaved(); add.onSaved = null; } else go("today", add.date);
  } catch (e) { btn.disabled = false; btn.textContent = "Save meal"; paintReview(e.message); }
}

// ---------- Edit ----------
let editRow = null, delArmed = false;
function openEdit(id) {
  editRow = Object.values(state.months).flatMap(m => m.rows).find(r => r.id === id);
  if (!editRow) return;
  delArmed = false;
  paintEdit();
  $("#edit").showModal();
}
function paintEdit(err = "") {
  const r = editRow, body = $("#edit-body");
  body.innerHTML = `
    ${r.photo ? `<button type="button" class="pick has" id="ephoto" aria-label="View photo"><img src="/photo/${esc(r.photo)}" alt="Meal photo">${r.photos.length > 1 ? `<span class="swap">+${r.photos.length - 1} more</span>` : ""}</button>` : ""}
    <div class="gap"><label class="lbl" for="efoods">Foods</label><textarea class="field" id="efoods" rows="3">${esc(r.foods)}</textarea></div>
    <div class="grid2"><div><label class="lbl" for="edate">Date</label><input class="field" type="date" id="edate" value="${esc(r.date)}" max="${esc(state.today)}"></div><div></div></div>
    <label class="lbl">Meal</label>
    ${segHTML("em", r.meal)}
    <div class="nums gap">${NUM.map(k => `<label>${LABEL[k]}<input inputmode="decimal" data-ek="${k}" value="${r[k] == null ? "" : n1(r[k])}"></label>`).join("")}</div>
    <div class="gap"><label class="lbl" for="enote">Note</label><textarea class="field" id="enote" rows="2">${esc(r.note || "")}</textarea></div>
    <p class="hint">Row #${r.id}${r.created_at ? ` · added ${esc(r.created_at)} UTC` : ""}</p>
    ${err ? `<p class="err" role="alert">${esc(err)}</p>` : ""}
    <div class="row-actions two">
      <button type="button" class="btn ${delArmed ? "dark" : ""}" id="edel">${ic("trash")}${delArmed ? "Delete?" : "Delete"}</button>
      <button type="button" class="btn primary" id="esave">Save changes</button>
    </div>`;
  let meal = r.meal;
  wireSeg(body, "em", v => meal = v);
  const ph = $("#ephoto");
  if (ph) ph.onclick = () => { $("#viewer-img").src = `/photo/${r.photo}`; $("#viewer").showModal(); };
  $("#edel").onclick = async () => {
    if (!delArmed) { delArmed = true; paintEdit(); return; }
    try { await api("/api/meals/delete", { id: r.id }); $("#edit").close(); invalidate(); toast("Deleted"); render(); }
    catch (e) { paintEdit(e.message); }
  };
  $("#esave").onclick = async () => {
    const b = { id: r.id, date: $("#edate").value, meal, foods: $("#efoods").value, note: $("#enote").value, estimated: !!+r.estimated };
    $$("[data-ek]", body).forEach(i => b[i.dataset.ek] = i.value.trim() === "" ? null : i.value);
    try { await api("/api/meals/update", b); $("#edit").close(); invalidate(); toast("Saved"); go("today", b.date); }
    catch (e) { paintEdit(e.message); }
  };
}

// dialogs: close buttons + reset return values
$$("dialog [data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
// Safari lacks closedby: add tap-outside dismissal only there
if (!("closedBy" in HTMLDialogElement.prototype)) {
  const outside = (e, d) => { const r = d.getBoundingClientRect(); return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom; };
  for (const d of $$('dialog[closedby="any"]')) {
    let down = false;
    d.addEventListener("pointerdown", e => { down = outside(e, d); });
    d.addEventListener("pointerup", e => { if (down && outside(e, d)) d.close(); });
  }
}


// ---------- Food AI (center button) ----------
// One place for both: a photo gets an itemized estimate card (Save / Edit), text asks or changes things.
// History lives only on this phone; any change is a card and only saves when the user taps Save/Apply.
const chat = { msgs: ls.get("food.chat", []), images: [], get busy() { return this.msgs.some(m => m.pending); } };
// a send that never reached the server before the app was killed can't be resumed: offer Retry
chat.msgs.forEach(m => { if (m.pending && !m.job) Object.assign(m, { pending: false, error: true, retry: true, content: "Didn’t reach the server." }); });
const saveChat = () => ls.set("food.chat", chat.msgs.slice(-40).map(m => ({ ...m, image: undefined, images: undefined,
  nphotos: m.images?.length || m.nphotos || undefined })));
const FIELD = { date: "Date", meal: "Meal", foods: "Foods", note: "Note", ...LABEL };
const SUGGEST = ["How much sat fat do I have left today?", "Which day this week went over?", "I just had a black coffee", "Breakfast was only 1 egg, fix it"];

const grid6 = a => `<div class="grid6">${[["kcal", "kcal"], ["protein_g", "Protein"], ["carbs_g", "Carbs"], ["fat_g", "Fat"], ["sat_fat_g", "Sat fat"], ["cholesterol_mg", "Chol mg"]]
  .map(([k, l]) => `<div class="${(k === "sat_fat_g" && a[k] >= 8) || (k === "cholesterol_mg" && a[k] >= 200) ? "hot" : ""}"><small>${l}</small><b>${k === "kcal" || k === "cholesterol_mg" ? n0(a[k]) : n1(a[k])}</b></div>`).join("")}</div>`;

function propHTML(p, key) {
  const st = p.state ? `<div class="state ${p.state === "applied" ? "" : "x"}">${p.state === "applied" ? "✓ Saved to your log" : "Dismissed"}</div>` : "";
  const why = p.reason ? `<div class="why">${esc(p.reason)}</div>` : "";
  const acts = (...b) => p.state ? "" : `<div class="acts">${b.join("")}</div>`;
  const dismiss = `<button type="button" class="btn sm" data-dismiss="${key}">Dismiss</button>`;
  if (p.type === "add") {
    const a = p.after;
    const list = p.items?.length ? `<ul class="its">${p.items.map(it => `<li><span>${esc(it.name)}${it.portion ? ` <span class="pt">· ${esc(it.portion)}</span>` : ""}</span><span class="k">${n0(it.kcal)}</span>
        <button type="button" class="favb ${isSaved(it.name) ? "on" : ""}" data-pstar="${key}.${p.items.indexOf(it)}" aria-label="Save ${esc(it.name)} to My foods">${ic("star")}</button></li>`).join("")}</ul>`
                                 : `<div class="foods">${esc(a.foods)}</div>`;
    return `<div class="prop ${p.state ? "done" : ""}"><div class="ph"><span class="tag">New</span>${esc(a.meal)} · ${esc(shortDate(a.date))}<span class="kc">${n0(a.kcal)}</span></div>
      ${list}${grid6(a)}${why}
      ${acts(`<button type="button" class="btn sm" data-review="${key}">Edit</button>`, `<button type="button" class="btn sm primary" data-apply="${key}">Save</button>`)}${st}</div>`;
  }
  if (p.type === "edit") {
    return `<div class="prop ${p.state ? "done" : ""}"><div class="ph"><span class="tag">Edit</span>${esc(p.before.meal)} · ${esc(shortDate(p.before.date))}</div>
      ${p.changed.filter(k => !NUM.includes(k)).map(k => `<div class="tx"><span class="lb">${FIELD[k] || k}</span>
        <span class="old">${esc(p.before[k] || "—")}</span><span>${esc(p.after[k] || "—")}</span></div>`).join("")}
      ${p.changed.some(k => NUM.includes(k)) ? `<table>${p.changed.filter(k => NUM.includes(k)).map(k =>
        `<tr><td>${LABEL[k]}</td><td class="n"><span class="old">${n1(p.before[k])}</span> → <b>${n1(p.after[k])}</b></td></tr>`).join("")}</table>` : ""}
      ${why}${acts(dismiss, `<button type="button" class="btn sm primary" data-apply="${key}">Apply</button>`)}${st}</div>`;
  }
  return `<div class="prop ${p.state ? "done" : ""}"><div class="ph"><span class="tag del">Delete</span>${p.rows.length} meal${p.rows.length > 1 ? "s" : ""}</div>
    ${p.rows.map(r => `<div class="foods">${esc(shortDate(r.date))} · ${esc(r.meal)} · ${n0(r.kcal)} kcal — ${esc(r.foods)}</div>`).join("")}
    ${why}${acts(dismiss, `<button type="button" class="btn sm primary" data-apply="${key}">Delete</button>`)}${st}</div>`;
}

function msgHTML(m, i) {
  if (m.role === "user") {
    const imgs = m.images || (m.image ? [m.image] : []), picOnly = imgs.length && m.content === "📷";
    const gone = !imgs.length && m.nphotos ? `<span class="gone">📷 ${m.nphotos} photo${m.nphotos > 1 ? "s" : ""}</span>` : "";
    return `<div class="msg user ${picOnly ? "pic-only" : ""} ${imgs.length > 1 ? "pics" : ""}">${imgs.map((src, k) => `<img src="${esc(src)}" alt="Meal photo ${k + 1}">`).join("")}${gone}${picOnly || (gone && m.content === "📷") ? "" : esc(m.content)}</div>`;
  }
  if (m.pending) return `<div class="msg bot"><span class="typing" aria-label="Thinking"><i></i><i></i><i></i></span>
    <span class="bg-hint">${m.job ? "Working on it — you can leave the app, it keeps going." : "Sending…"}</span></div>`;
  if (m.error) return `<div class="msg bot err"><div class="md">${esc(m.content)}</div>
    ${m.retry !== false ? `<button type="button" class="btn sm retry" data-retry="${i}">${ic("redo")}Retry</button>` : ""}</div>`;
  return `${m.content ? `<div class="msg bot"><div class="md">${md(m.content)}</div></div>` : ""}
    ${(m.proposals || []).map((p, j) => propHTML(p, `${i}.${j}`)).join("")}
    ${m.model ? `<div class="via">${esc(m.model)}</div>` : ""}`;
}

function paintMsgs() {
  const box = $("#msgs"); if (!box) return;
  box.innerHTML = chat.msgs.length ? chat.msgs.map(msgHTML).join("")
    : `<label class="snap"><span class="ic">${ic("cam")}</span><span><b>Snap a meal</b><small>Plate, packaging or label — up to ${MAX_PHOTOS} photos. The AI splits it into foods and nutrients.</small></span>
         <input type="file" id="ask-snap" accept="image/*" multiple aria-label="Meal photos"></label>
       ${state.foods?.length ? `<p class="or">${ic("star")} My foods · one tap</p>
       <div class="chips favs">${state.foods.slice(0, 6).map(f => `<button type="button" data-fav="${f.id}">${esc(f.name)} <small>${n0(f.kcal)}</small></button>`).join("")}
         <button type="button" id="favs-all" class="all">All…</button></div>` : ""}
       <p class="or">or ask / tell me</p>
       <div class="chips">${SUGGEST.map(q => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
       <p class="fine">Nothing changes until you tap Save or Apply on a card.</p>`;
  // one photo -> send straight away; several -> attach so he can add a note first
  const snap = $("#ask-snap", box); if (snap) snap.onchange = e => pickPhoto(e.target.files, e.target.files.length === 1);
  $$("[data-q]", box).forEach(b => b.onclick = () => send(b.dataset.q));
  $$("[data-apply]", box).forEach(b => b.onclick = () => applyProp(b.dataset.apply, b));
  $$("[data-retry]", box).forEach(b => b.onclick = () => retryMsg(+b.dataset.retry, b));
  $$("[data-pstar]", box).forEach(b => b.onclick = () => { const k = b.dataset.pstar.split("."); starItem(propAt(`${k[0]}.${k[1]}`).items[+k[2]], b); });
  $$("[data-fav]", box).forEach(b => b.onclick = () => quickAdd(state.foods.find(f => f.id === +b.dataset.fav)));
  const allf = $("#favs-all", box); if (allf) allf.onclick = () => openFoods(quickAdd);
  $$("[data-review]", box).forEach(b => b.onclick = () => reviewProp(b.dataset.review));
  $$("[data-dismiss]", box).forEach(b => b.onclick = () => { propAt(b.dataset.dismiss).state = "dismissed"; saveChat(); paintMsgs(); });
  if (chat.msgs.length) requestAnimationFrame(() => scrollTo({ top: document.body.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }));
}
const propAt = key => { const [i, j] = key.split(".").map(Number); return chat.msgs[i].proposals[j]; };
const photosFor = key => { const m = chat.msgs[+key.split(".")[0] - 1]; return m?.images || (m?.image ? [m.image] : []); };

async function applyProp(key, btn) {
  const p = propAt(key);
  btn.disabled = true;
  try {
    if (p.type === "add") await api("/api/meals/add", { ...p.after, estimated: true, items: p.items?.length ? p.items : undefined, images: raw64(photosFor(key)), job: chat.msgs[+key.split(".")[0]].job });
    else if (p.type === "edit") await api("/api/meals/update", { id: p.row_id, ...p.after, estimated: !!+p.before.estimated });
    else for (const r of p.rows) await api("/api/meals/delete", { id: r.id });
    p.state = "applied"; saveChat(); invalidate(); toast(p.type === "add" ? `Saved · ${n0(p.after.kcal)} kcal` : "Saved to your log"); paintMsgs();
  } catch (e) { btn.disabled = false; toast(e.message); }
}

// Edit an estimate card in the full review sheet (per-item numbers), then Save from there.
function reviewProp(key) {
  const p = propAt(key), a = p.after, imgs = photosFor(key);
  const items = (p.items?.length ? p.items : [{ name: a.foods, portion: "", confidence: "high", ...Object.fromEntries(NUM.map(k => [k, a[k] || 0])) }])
    .map(it => ({ portion: "", confidence: "high", ...it, ...Object.fromEntries(NUM.map(k => [k, +it[k] || 0])) }));
  const lastBot = chat.msgs[+key.split(".")[0]];
  Object.assign(add, { editId: null, keepNote: "", images: imgs, previews: imgs, job: lastBot.job, date: a.date, meal: a.meal, mealTouched: true, text: "",
    draft: { model: lastBot.model || "manual", items, assumptions: "", tip: "" },
    onSaved: () => { p.state = "applied"; saveChat(); paintMsgs(); } });
  paintReview();
  $("#add").showModal();
}

async function pickPhoto(files, sendNow) {
  if (!files?.length) return;
  try { chat.images.push(...await readPhotos(files, chat.images.length)); } catch (x) { toast(x.message); return; }
  if (sendNow) send(""); else { paintComposer(); $("#ask-in").focus(); }
}

async function send(text) {
  text = (text ?? $("#ask-in")?.value ?? "").trim();
  if (chat.busy || (!text && !chat.images.length)) return;
  const images = chat.images; chat.images = [];
  chat.msgs.push({ role: "user", content: text || (images.length ? "📷" : ""), images });
  startJob(chat.msgs.length - 1);
}

const chatHistory = upto => chat.msgs.slice(0, upto + 1).filter(m => !m.error && !m.pending).map(m => ({ role: m.role,
  content: (m.content === "📷" ? "[sent meal photos]" : m.content) + (m.proposals?.length ?
    `\n[cards shown: ${m.proposals.map(p => `${p.type} ${p.type === "add" ? p.after.foods : ""} ${p.state || "pending"}`).join(", ")}]` : "") }));

// The AI runs as a server job: the phone only starts it and polls, so leaving the app (iOS suspends the page and
// drops open requests) no longer loses the answer.
async function startJob(ui) {
  const u = chat.msgs[ui];
  const model = $("#ask-model")?.value || ls.get("food.chatModel") || state.models?.default;
  const bot = { role: "assistant", pending: true, model };
  chat.msgs.splice(ui + 1, 0, bot);
  paintComposer(); paintMsgs(); saveChat();
  try {
    bot.job = (await api("/api/jobs/start", { kind: "chat", model, images: raw64(u.images), messages: chatHistory(ui) })).job;
    saveChat(); paintMsgs();
    pollJobs();
  } catch (e) {
    Object.assign(bot, { pending: false, error: true, content: e.status ? e.message : "Couldn’t reach the server (connection dropped)." });
    saveChat(); paintMsgs(); paintComposer();
  }
}

let polling = false;
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    while (document.visibilityState !== "hidden" && chat.msgs.some(m => m.pending && m.job)) {
      for (const m of chat.msgs.filter(x => x.pending && x.job)) {
        let st;
        try { st = await api(`/api/jobs?id=${m.job}`); }
        catch (e) {
          if (e.status === 404) Object.assign(m, { pending: false, error: true, content: "The server lost this request (it restarted)." });
          else continue;   // offline for a moment: keep waiting
        }
        if (st?.status === "done") Object.assign(m, { pending: false, content: st.result.reply, proposals: st.result.proposals, model: st.result.model });
        else if (st?.status === "error") Object.assign(m, { pending: false, error: true, content: st.error });
        if (!m.pending) {
          saveChat(); paintMsgs(); paintComposer();
          if (state.tab !== "ask") toast(m.error ? "Food AI: failed — open AI to retry" : "Food AI: estimate ready");
        }
      }
      await sleep(1500);
    }
  } finally { polling = false; }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pollJobs(); });
addEventListener("pageshow", () => pollJobs());

async function retryMsg(i, btn) {
  const m = chat.msgs[i], u = chat.msgs[i - 1];
  btn.disabled = true;
  if (m.job) {
    try {   // rerun the same request on the server: photos included, nothing to re-attach
      await api("/api/jobs/retry", { id: m.job });
      Object.assign(m, { pending: true, error: false, content: "" });
      saveChat(); paintMsgs(); paintComposer(); pollJobs();
      return;
    } catch (e) { if (e.status !== 400) { btn.disabled = false; toast(e.message); return; } }
  }
  if (u?.role !== "user") { btn.disabled = false; return; }
  if (u.nphotos && !u.images?.length) {   // job expired and the phone dropped the photos when the app closed
    chat.msgs.splice(i - 1, 2); saveChat(); paintMsgs();
    const inp = $("#ask-in"); if (inp && u.content !== "📷") inp.value = u.content;
    toast("Photos weren’t kept after the app closed — attach them again");
    return;
  }
  chat.msgs.splice(i, 1);
  startJob(i - 1);
}

function paintComposer() {
  const c = $("#composer"); if (!c) return;
  const keep = chat.busy ? "" : $("#ask-in")?.value || "";
  const n = chat.images.length;
  c.innerHTML = `${n ? `<div class="pv">${strip(chat.images, "data-unpic")}<span>${n} photo${n > 1 ? "s" : ""} · one meal${n < MAX_PHOTOS ? ` · tap ${ic("cam")} to add more` : ""}</span></div>` : ""}
    <form id="ask-form">
      <label class="cb attach" aria-label="Snap or attach a photo">${ic("cam")}<input type="file" id="ask-pic" accept="image/*" multiple></label>
      <button type="button" class="cb attach" id="ask-fav" aria-label="Add from My foods">${ic("star")}</button>
      <label class="sr" for="ask-in">Message</label>
      <textarea id="ask-in" rows="1" enterkeyhint="send" placeholder="${n ? "e.g. half the rice, no sauce" : "Ask, or tell me what to log or fix"}">${esc(keep)}</textarea>
      <button type="submit" class="cb send" aria-label="Send" ${chat.busy ? "disabled" : ""}>${ic("right")}</button>
    </form>`;
  $("#ask-form").onsubmit = e => { e.preventDefault(); send(); };
  $("#ask-in").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(hover: hover)").matches) { e.preventDefault(); send(); } };
  $("#ask-pic").onchange = e => pickPhoto(e.target.files, false);
  $("#ask-fav").onclick = () => openFoods(quickAdd);
  $$("[data-unpic]", c).forEach(b => b.onclick = () => { chat.images.splice(+b.dataset.unpic, 1); paintComposer(); });
}

async function renderAsk() {
  await Promise.all([loadMonth(""), loadFoods()]);
  const M = await modelsList();
  const chosen = M.models.includes(ls.get("food.chatModel")) ? ls.get("food.chatModel") : M.default;
  view.innerHTML = `<div class="ai-h"><span class="ai-t">${ic("spark")}Food AI</span>
      <label class="pill"><span id="ask-model-name">${esc(chosen)}</span><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10l5 5 5-5"/></svg>
        <select id="ask-model" aria-label="Model">${M.models.map(m => `<option ${m === chosen ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></label>
      <button type="button" class="icon-btn" id="ask-new" aria-label="New chat">${ic("plus")}</button></div>
    <div class="msgs" id="msgs" aria-live="polite"></div>
    <div class="composer" id="composer"></div>`;
  $("#ask-model").onchange = e => { ls.set("food.chatModel", e.target.value); $("#ask-model-name").textContent = e.target.value; };
  $("#ask-new").onclick = () => { if (chat.busy) return; chat.msgs = []; chat.images = []; saveChat(); paintMsgs(); paintComposer(); };
  paintMsgs(); paintComposer();
}

// ---------- router ----------
function go(tab, arg) {
  const h = `#${tab}${arg ? "/" + arg : ""}`;
  if (location.hash === h) render(); else location.hash = h;
}
async function render() {
  const [tab, arg] = location.hash.slice(1).split("/");
  state.tab = ["history", "ask"].includes(tab) ? tab : "today";
  $(".app").classList.toggle("asking", state.tab === "ask");
  if (state.tab === "today") state.date = /^\d{4}-\d{2}-\d{2}$/.test(arg || "") ? arg : null;
  if (state.tab === "history") state.month = /^\d{4}-\d{2}$/.test(arg || "") ? arg : state.month;
  $$(".tabs a").forEach(a => a.dataset.tab === state.tab ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  try {
    if (state.tab === "today") await renderToday(); else if (state.tab === "ask") await renderAsk(); else await renderHistory();
    applyWidths(view);
    $("#sync").innerHTML = `<span class="dot"></span>Synced`;
  } catch (e) {
    view.innerHTML = `<div class="panel empty"><b>Couldn’t load</b>${esc(e.message)}</div>`;
    $("#sync").textContent = "Offline";
  }
}
$("#fab").onclick = () => state.tab === "ask" ? $("#ask-pic")?.click() : go("ask");
addEventListener("hashchange", render);
document.addEventListener("visibilitychange", () => { if (!document.hidden && !$("dialog[open]") && state.tab !== "ask") { invalidate(); render(); } });
render();
pollJobs();   // resume any AI job still running from before the app was closed
})();
