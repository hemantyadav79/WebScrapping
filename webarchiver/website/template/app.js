/* ============ Property Explorer — app logic ============ */
const PAGE_SIZE = 24;
const STATE = { all: [], view: [], page: 1, gallery: { imgs: [], i: 0 } };
let DETAILS = {};
const normUrl = u => String(u || "").trim().replace(/\/+$/, "").toLowerCase();
const detailFor = r => DETAILS[normUrl(G(r, "Listing URL"))];
const fmt = n => (n == null || n === "") ? "" : Number(n).toLocaleString();
const money = n => (n == null || n === "" || isNaN(+n)) ? "" : "$" + Number(n).toLocaleString();
const pct = n => (n == null || n === "") ? "" : (n > 0 ? "+" : "") + (Math.round(Number(n) * 10) / 10) + "%";
const fixUrl = u => { u = String(u || ""); return u.startsWith("//") ? "https:" + u : u; };
const pick = (o, paths) => { for (const p of paths) { let c = o; let ok = true; for (const k of p.split(".")) { c = (c && typeof c === "object") ? c[k] : undefined; if (c == null) { ok = false; break; } } if (ok && (typeof c === "string" || typeof c === "number")) return String(c); } return ""; };
const sec = (t, b) => b ? `<div class="section-title">${t}</div>${b}` : "";

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const G = (r, k) => (r && r[k] != null ? String(r[k]).trim() : "");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// Convert listing/detail descriptions (which contain raw HTML like <br>) into
// clean text so tags don't show literally in the modal.
const cleanDesc = (s) => String(s == null ? "" : s)
  .replace(/<br\s*\/?>/gi, "\n").replace(/<\/p>/gi, "\n\n").replace(/<[^>]+>/g, "")
  .replace(/&nbsp;/gi, " ").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();

const AU_STATE = /(.*?)[,\s]+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4}/i;
function suburbOf(addr) {
  const m = addr.match(AU_STATE); let pre = m ? m[1] : addr;
  const segs = pre.split(",").map(s => s.trim()).filter(Boolean);
  let cand = segs[segs.length - 1] || pre;
  if (/^\d/.test(cand)) cand = cand.split(/\s+/).slice(-1)[0] || cand;
  return cand;
}
function priceNum(s) { const d = String(s).replace(/[, ]/g, "").match(/\$?(\d{4,})/); return d ? +d[1] : null; }
function imgs(r) {
  // Prefer the REMOTE image URLs: they are unique per property and load from
  // the public CDN. The local files use generic names (image.jpg, image_1.jpg)
  // and content-dedup, so several listings can share one file -> wrong image.
  const remote = G(r, "Image URLs"), local = G(r, "Local Image Paths");
  let list = (remote || local).split(",").map(x => x.trim()).filter(Boolean);
  if (!list.length && local) list = local.split(",").map(x => x.trim()).filter(Boolean);
  return list;
}
function statusClass(s) { s = (s || "").toLowerCase(); if (s.includes("auction")) return "auction"; if (s.includes("withheld")) return "withheld"; if (s.includes("sold")) return "sold"; return ""; }
function statusLabel(s) { return (s || "for_sale").replace(/_/g, " "); }
function auctionLabel(r) {
  let a = r ? r["Auction"] : null;
  if (a == null || a === "") return "";
  if (typeof a === "string") return a.trim();
  // Older data stored the raw auction object -> dig out a readable label.
  const dt = (a && a.dateTime) ? a.dateTime : a;
  const lbl = (dt && dt.display && dt.display.longLabel) || (a.display && a.display.longLabel)
            || (dt && dt.value) || a.value || "";
  return typeof lbl === "string" ? lbl : "";
}
function dateVal(s) { const m = String(s).match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/); return m ? +(m[3] + m[2].padStart(2, "0") + m[1].padStart(2, "0")) : 0; }
function num(s) { const n = parseInt(String(s).replace(/[^\d]/g, ""), 10); return isNaN(n) ? null : n; }

/* ---------------- data loading ---------------- */
async function loadData() {
  showState("Loading properties&hellip;", true);
  try {
    const res = await fetch("listings.json?_=" + Date.now());
    if (!res.ok) throw new Error(res.status);
    STATE.all = await res.json();
    DETAILS = {};
    try { const dr = await fetch("details.json?_=" + Date.now()); if (dr.ok) { (await dr.json() || []).forEach(d => { if (d && d.url) DETAILS[normUrl(d.url)] = d; }); } } catch (e) {}
    $("#banner").hidden = true;
    initFilters(); apply();
  } catch (e) {
    showFileFallback();
  }
}
function showFileFallback() {
  const b = $("#banner"); b.hidden = false;
  b.innerHTML = `<div class="box">Couldn&rsquo;t auto-load <code>listings.json</code> (browsers block file reads when opened directly).
    Easiest fix: double-click <code>serve.bat</code> then open <b>http://localhost:8000</b>.
    Or load it manually: <input type="file" id="filePick" accept=".json" style="margin-left:8px">.</div>`;
  $("#filePick").addEventListener("change", ev => {
    const f = ev.target.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = () => { try { STATE.all = JSON.parse(rd.result); b.hidden = true; initFilters(); apply(); } catch { alert("Invalid JSON"); } };
    rd.readAsText(f);
  });
  showState("Waiting for data&hellip;", false);
}

/* ---------------- filters ---------------- */
function uniqueSorted(key, transform) {
  const set = new Set();
  STATE.all.forEach(r => { let v = transform ? transform(r) : G(r, key); if (v) set.add(v); });
  return [...set].sort((a, b) => a.localeCompare(b));
}
function fillSelect(sel, items, anyLabel) {
  sel.innerHTML = `<option value="">${anyLabel}</option>` + items.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
}
function initFilters() {
  fillSelect($("#f-status"), uniqueSorted("Sale Status").map(statusLabel).filter((v,i,a)=>a.indexOf(v)===i), "All statuses");
  fillSelect($("#f-type"), uniqueSorted("Property Type"), "All types");
  fillSelect($("#f-suburb"), uniqueSorted(null, r => suburbOf(G(r, "Address"))), "All suburbs");
}

/* ---------------- apply (search + filter + sort) ---------------- */
function apply() {
  const q = $("#search").value.trim().toLowerCase();
  const fStatus = $("#f-status").value, fType = $("#f-type").value, fSub = $("#f-suburb").value;
  const fBeds = num($("#f-beds").value), fBaths = num($("#f-baths").value);
  const fMin = num($("#f-min").value), fMax = num($("#f-max").value);

  STATE.view = STATE.all.filter(r => {
    if (q) {
      const hay = [G(r,"Address"),G(r,"Agent"),G(r,"Agents"),G(r,"Agency"),G(r,"Description"),G(r,"Property Type")].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (fStatus && statusLabel(G(r,"Sale Status")) !== fStatus) return false;
    if (fType && G(r,"Property Type") !== fType) return false;
    if (fSub && suburbOf(G(r,"Address")) !== fSub) return false;
    if (fBeds != null && (num(G(r,"Bedrooms")) || 0) < fBeds) return false;
    if (fBaths != null && (num(G(r,"Bathrooms")) || 0) < fBaths) return false;
    const p = priceNum(G(r,"Price"));
    if (fMin != null && (p == null || p < fMin)) return false;
    if (fMax != null && (p == null || p > fMax)) return false;
    return true;
  });

  const s = $("#sort").value;
  const cmp = {
    "price-desc": (a,b)=>(priceNum(G(b,"Price"))||0)-(priceNum(G(a,"Price"))||0),
    "price-asc": (a,b)=>(priceNum(G(a,"Price"))||1e15)-(priceNum(G(b,"Price"))||1e15),
    "beds-desc": (a,b)=>(num(G(b,"Bedrooms"))||0)-(num(G(a,"Bedrooms"))||0),
    "date-desc": (a,b)=>dateVal(G(b,"Contract Date"))-dateVal(G(a,"Contract Date")),
    "addr": (a,b)=>G(a,"Address").localeCompare(G(b,"Address")),
  }[s];
  if (cmp) STATE.view.sort(cmp);

  STATE.page = 1;
  $("#countNum").textContent = STATE.all.length.toLocaleString();
  render();
}

/* ---------------- render grid + pager ---------------- */
function render() {
  const total = STATE.view.length, pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  STATE.page = Math.min(STATE.page, pages);
  const start = (STATE.page - 1) * PAGE_SIZE, slice = STATE.view.slice(start, start + PAGE_SIZE);
  const grid = $("#grid"); grid.innerHTML = "";

  $("#showing").innerHTML = total
    ? `Showing <b>${start + 1}&ndash;${start + slice.length}</b> of <b>${total.toLocaleString()}</b> ${total === STATE.all.length ? "" : "matching "}properties`
    : "";
  if (!total) { grid.innerHTML = `<div class="state"><h3>No properties match your filters</h3><p>Try widening the price range or clearing filters.</p></div>`; $("#pager").innerHTML = ""; return; }

  slice.forEach((r, i) => grid.appendChild(card(r, start + i)));
  renderPager(pages);
}
function card(r, idx) {
  const im = imgs(r), first = im[0] || "";
  const st = G(r, "Sale Status"), price = G(r, "Price") || "Contact Agent";
  const beds = G(r, "Bedrooms"), baths = G(r, "Bathrooms"), cars = G(r, "Parking");
  const addr = G(r, "Address"), street = addr.split(",")[0];
  const c = el("div", "card");
  c.innerHTML = `
    <div class="card-media">
      ${first ? `<img loading="lazy" src="${esc(first)}" alt="${esc(street)}" onerror="this.style.display='none'">` : `<div class="skeleton" style="width:100%;height:100%"></div>`}
      <span class="badge ${statusClass(st)}">${esc(statusLabel(st))}</span>
      ${im.length > 1 ? `<span class="photo-count">&#128247; ${im.length}</span>` : ""}
    </div>
    <div class="card-body">
      <div class="ptype">${esc(G(r, "Property Type") || "Property")}</div>
      <div class="price">${esc(price)}</div>
      <div class="addr"><span class="street">${esc(street)}</span>${addr.includes(",") ? ", " + esc(addr.slice(addr.indexOf(",") + 1).trim()) : ""}</div>
      <div class="specs">
        ${beds ? `<span><span class="ico">&#128716;</span>${esc(beds)} Bed</span>` : ""}
        ${baths ? `<span><span class="ico">&#128705;</span>${esc(baths)} Bath</span>` : ""}
        ${cars ? `<span><span class="ico">&#128663;</span>${esc(cars)} Car</span>` : ""}
        ${G(r, "Land Size") ? `<span><span class="ico">&#9633;</span>${esc(G(r, "Land Size"))}</span>` : ""}
      </div>
    </div>`;
  c.addEventListener("click", () => openModal(r));
  return c;
}
function renderPager(pages) {
  const p = $("#pager"); p.innerHTML = ""; if (pages <= 1) return;
  const mk = (label, page, opts = {}) => { const b = el("button", opts.active ? "active" : ""); b.innerHTML = label; if (opts.disabled) b.disabled = true; else b.onclick = () => { STATE.page = page; render(); window.scrollTo({ top: 0, behavior: "smooth" }); }; return b; };
  p.appendChild(mk("&laquo;", STATE.page - 1, { disabled: STATE.page === 1 }));
  const win = []; const cur = STATE.page;
  for (let i = 1; i <= pages; i++) { if (i === 1 || i === pages || (i >= cur - 2 && i <= cur + 2)) win.push(i); else if (win[win.length - 1] !== "…") win.push("…"); }
  win.forEach(i => { if (i === "…") { const d = el("button", "", "&hellip;"); d.disabled = true; p.appendChild(d); } else p.appendChild(mk(i, i, { active: i === cur })); });
  p.appendChild(mk("&raquo;", STATE.page + 1, { disabled: STATE.page === pages }));
}

/* ---------------- detail modal ---------------- */
function compRow(c) {
  const a = pick(c, ["address.formattedFull", "address.line1", "formattedFull", "line1", "address", "name"]);
  const p = pick(c, ["price.display", "transfer.price", "soldPrice", "price", "displayPrice"]);
  const d = pick(c, ["transfer.contractDate", "soldDate", "date"]).slice(0, 10);
  if (!a && !p) return "";
  return `<div class="comp"><span class="comp-addr">${esc(a)}</span><span class="comp-price">${esc(money(p) || p)}</span><span class="comp-date">${esc(d)}</span></div>`;
}
function renderDetail(d) {
  let h = "";
  if (Array.isArray(d.key_details) && d.key_details.length)
    h += sec("Key details", `<div class="kv">` + d.key_details.map(k => `<div class="kv-row"><span>${esc(k.label)}</span><span>${esc(k.value)}${k.approximate ? " (approx)" : ""}</span></div>`).join("") + `</div>`);
  const b = d.block_information || {};
  if (b.block_size || b.unimproved_value) {
    const rows = [["Block size", b.block_size ? b.block_size + " m²" : ""], ["Block / Section", [b.block, b.section].filter(Boolean).join(" / ")], ["Legal description", b.legal_description], ["Unimproved value", b.unimproved_value]];
    const uv = (b.unimproved_value_history || []).map(e => `<div class="kv-row"><span>${esc(e.year || e.label || "")}</span><span>${esc(e.value || e.formatted || e.amount || money(e.unimprovedValue) || "")}</span></div>`).join("");
    h += sec("Block information", `<div class="kv">` + rows.filter(r => r[1]).map(r => `<div class="kv-row"><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("") + `</div>` + (uv ? `<div class="mini-title">Unimproved value history</div><div class="kv">` + uv + `</div>` : "") + (b.block_map_image ? `<img class="block-map" src="${esc(fixUrl(b.block_map_image))}" alt="Block map" onerror="this.style.display='none'">` : ""));
  }
  const n = d.nearby_sales || {};
  if (n.count || n.priceMedian) {
    const rows = [["Comparable sales", n.count], ["Search radius", n.searchRadius ? n.searchRadius + " km" : ""], ["Type / Beds", [n.propertyType, n.bedrooms && n.bedrooms + " bed"].filter(Boolean).join(" · ")], ["Price range", (n.priceLow && n.priceHigh) ? `${money(n.priceLow)} – ${money(n.priceHigh)}` : ""], ["Median", money(n.priceMedian)]];
    h += sec("Nearby sales", `<div class="kv">` + rows.filter(r => r[1] != null && r[1] !== "").map(r => `<div class="kv-row"><span>${esc(r[0])}</span><span>${esc(r[1])}</span></div>`).join("") + `</div>` + (n.searchUrl ? `<a class="ext-link" href="${esc(n.searchUrl)}" target="_blank" rel="noopener">View nearby sales &#8599;</a>` : ""));
  }
  if (Array.isArray(d.sold_comparables) && d.sold_comparables.length) {
    const rows = d.sold_comparables.slice(0, 8).map(compRow).filter(Boolean).join("");
    if (rows) h += sec("Recent comparable sales", `<div class="comp-list">` + rows + `</div>`);
  }
  const p = d.risks_and_heritage || {};
  if ("floodZone" in p || "bushfireZone" in p || "heritageZone" in p) {
    const chip = (l, v) => `<span class="chip ${v ? "on" : "off"}">${esc(l)}: ${v ? "Yes" : "No"}</span>`;
    h += sec("Risks &amp; heritage", `<div class="chips">${chip("Flood zone", p.floodZone)}${chip("Bushfire zone", p.bushfireZone)}${chip("Heritage", p.heritageZone)}</div>` + (p.source && p.source.name ? `<div class="src">Source: ${esc(p.source.name)}</div>` : ""));
  }
  if (Array.isArray(d.whats_nearby) && d.whats_nearby.length)
    h += sec("What&rsquo;s nearby", `<div class="kv">` + d.whats_nearby.slice(0, 12).map(poi => `<div class="kv-row"><span>${esc(poi.name || poi.title || "")}</span><span>${esc(poi.category || poi.type || "")}${poi.distance ? " · " + esc(poi.distance) : ""}</span></div>`).join("") + `</div>`);
  const su = (d.suburb_information || {}).statistics || {};
  if (su && Object.keys(su).length) {
    const c = (k, v) => v ? `<div class="mc"><div class="mc-k">${esc(k)}</div><div class="mc-v">${esc(v)}</div></div>` : "";
    const cd = (k, raw) => (raw == null || raw === "") ? "" : `<div class="mc"><div class="mc-k">${esc(k)}</div><div class="mc-v ${raw >= 0 ? "up" : "down"}">${esc(pct(raw))}</div></div>`;
    h += sec(esc((d.suburb_information.name || "Suburb")) + " information", `<div class="mini-cards">` + c("Median house price", money(su.houseMedianPrice)) + cd("House 12mo", su.housePriceYearlyDelta) + c("Median unit price", money(su.unitMedianPrice)) + cd("Unit 12mo", su.unitPriceYearlyDelta) + `</div>`);
  }
  if (Array.isArray(d.sale_history) && d.sale_history.length) {
    const rows = d.sale_history.map(ev => { const t = ev.transfer || {}; const dt = String(ev.date || t.contractDate || "").slice(0, 10); const pr = t.price ? money(t.price) : (t.label || ""); return `<div class="comp"><span class="comp-date">${esc(dt)}</span><span class="comp-price">${esc(pr)}</span><span>${esc(t.label || "")}</span></div>`; }).join("");
    h += sec("Sale history", `<div class="comp-list">` + rows + `</div>`);
  }
  return h;
}
function openModal(r) {
  const det = detailFor(r);
  let im = imgs(r);
  if (det) { const di = (det.image_urls && det.image_urls.length) ? det.image_urls : (det.local_image_paths || []); if (di.length) im = di; }
  STATE.gallery = { imgs: im, i: 0 };
  const addr = G(r, "Address");
  const cells = [
    ["Price", G(r, "Price") || "Contact Agent"], ["Type", G(r, "Property Type")],
    ["Bedrooms", G(r, "Bedrooms")], ["Bathrooms", G(r, "Bathrooms")], ["Parking", G(r, "Parking")],
    ["Land Size", G(r, "Land Size")], ["Building Size", G(r, "Building Size")], ["EER", G(r, "EER")],
    ["Sale Status", statusLabel(G(r, "Sale Status"))], ["Contract Date", G(r, "Contract Date")],
    ["Listed Date", G(r, "Listed Date")], ["Days on Market", G(r, "Days On Market")],
    ["Unimproved Value", G(r, "Unimproved Value")],
    ["Property ID", G(r, "Property ID")], ["Listing ID", G(r, "Listing ID")],
    ["Page Views", G(r, "Page Views") ? Number(G(r, "Page Views")).toLocaleString() : ""],
    ["Last Updated", (G(r, "Last Updated") || "").slice(0, 10)],
  ].filter(([, v]) => v);

  // Agents: prefer the rich detail list (with photos), else fall back to the row text.
  let agentRows = "";
  const detAgents = det && Array.isArray(det.agents) ? det.agents : null;
  if (detAgents && detAgents.length) {
    agentRows = detAgents.map(a => `<div class="a">
      ${a.profile_photo ? `<img class="agent-photo" src="${esc(fixUrl(a.profile_photo))}" alt="${esc(a.name || "")}" onerror="this.style.display='none'">` : `<span class="agent-photo ph-fallback">${esc((a.name || "?").slice(0,1))}</span>`}
      <span class="a-info"><span class="a-name">${esc(a.name || "")}</span>${a.phone ? `<span class="ph">${esc(a.phone)}</span>` : ""}</span></div>`).join("");
  } else {
    const photo = G(r, "Agent Photo");
    const agents = G(r, "Agents") || G(r, "Agent");
    agentRows = agents ? agents.split(";").map((a, idx) => { const m = a.match(/(.*?)\s*\(([^)]+)\)/); const nm = m ? m[1].trim() : a.trim(); const phn = m ? m[2] : ""; const img = idx === 0 && photo ? `<img class="agent-photo" src="${esc(fixUrl(photo))}" alt="${esc(nm)}" onerror="this.style.display='none'">` : `<span class="agent-photo ph-fallback">${esc((nm || "?").slice(0,1))}</span>`; return `<div class="a">${img}<span class="a-info"><span class="a-name">${esc(nm)}</span>${phn ? `<span class="ph">${esc(phn)}</span>` : ""}</span></div>`; }).join("") : "";
  }
  const agencyName = G(r, "Agency"), agencyLogo = G(r, "Agency Logo") || (det && det.agency && det.agency.logo) || "";
  const agencyBlock = agencyName ? `<div class="agency">${agencyLogo ? `<img class="agency-logo" src="${esc(fixUrl(agencyLogo))}" alt="${esc(agencyName)}" onerror="this.style.display='none'">` : ""}<span>${esc(agencyName)}</span></div>` : "";

  const featuresList = G(r, "Property Features") || ((det && (det.property_features || det.features_list)) || []).join("; ");
  const featuresBlock = featuresList ? `<div class="feat-chips">${featuresList.split(";").map(f => f.trim()).filter(Boolean).map(f => `<span class="feat-chip">${esc(f)}</span>`).join("")}</div>` : "";
  const auction = auctionLabel(r);
  const insp = G(r, "Inspections"), desc = cleanDesc(G(r, "Description") || (det && det.description) || ""), link = G(r, "Listing URL");

  $("#modalCard").innerHTML = `
    <div class="modal-hero">
      <button class="modal-close" id="mClose" aria-label="Close">&times;</button>
      ${im.length ? `<img id="heroImg" src="${esc(im[0])}" alt="${esc(addr)}">` : `<div class="skeleton" style="height:300px"></div>`}
      ${im.length > 1 ? `<button class="nav-arrow prev" id="mPrev">&#8249;</button><button class="nav-arrow next" id="mNext">&#8250;</button>` : ""}
    </div>
    ${im.length > 1 ? `<div class="thumbs" id="thumbs">${im.map((u, i) => `<img src="${esc(u)}" data-i="${i}" class="${i === 0 ? "active" : ""}">`).join("")}</div>` : ""}
    <div class="modal-body">
      <h2>${esc(addr.split(",")[0])}</h2>
      <div class="sub">${esc(addr.split(",").slice(1).join(",").trim())}</div>
      <div class="modal-price">${esc(G(r, "Price") || "Contact Agent")}</div>
      ${agencyBlock}
      <div class="spec-grid">${cells.map(([k, v]) => `<div class="cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>
      ${featuresBlock ? `<div class="section-title">Property features</div>${featuresBlock}` : ""}
      ${agentRows ? `<div class="section-title">Agents</div><div class="agents">${agentRows}</div>` : ""}
      ${auction ? `<div class="section-title">Auction</div><div class="desc">${esc(auction)}</div>` : ""}
      ${insp ? `<div class="section-title">Inspections</div><div class="desc">${esc(insp)}</div>` : ""}
      ${desc ? `<div class="section-title">Description</div><div class="desc">${esc(desc)}</div>` : ""}
      ${det ? renderDetail(det) : ""}
      ${link ? `<a class="ext-link" href="${esc(link)}" target="_blank" rel="noopener">View original listing &#8599;</a>` : ""}
    </div>`;
  $("#modal").classList.add("open"); document.body.style.overflow = "hidden";
  $("#mClose").onclick = closeModal;
  if (im.length > 1) {
    $("#mPrev").onclick = () => gal(-1); $("#mNext").onclick = () => gal(1);
    $("#thumbs").querySelectorAll("img").forEach(t => t.onclick = () => { STATE.gallery.i = +t.dataset.i; paintGallery(); });
  }
}
function gal(d) { const g = STATE.gallery; g.i = (g.i + d + g.imgs.length) % g.imgs.length; paintGallery(); }
function paintGallery() { const g = STATE.gallery; const hi = $("#heroImg"); if (hi) hi.src = g.imgs[g.i]; const t = $("#thumbs"); if (t) t.querySelectorAll("img").forEach((im, i) => im.classList.toggle("active", i === g.i)); }
function closeModal() { $("#modal").classList.remove("open"); document.body.style.overflow = ""; }

/* ---------------- wire up ---------------- */
let t; const debounce = (fn) => { clearTimeout(t); t = setTimeout(fn, 180); };
$("#search").addEventListener("input", () => debounce(apply));
["#f-status", "#f-type", "#f-suburb", "#f-beds", "#f-baths", "#sort"].forEach(s => $(s).addEventListener("change", apply));
["#f-min", "#f-max"].forEach(s => $(s).addEventListener("input", () => debounce(apply)));
$("#clear").addEventListener("click", () => { ["#search","#f-min","#f-max"].forEach(s=>$(s).value=""); ["#f-status","#f-type","#f-suburb","#f-beds","#f-baths"].forEach(s=>$(s).value=""); $("#sort").value="relevance"; apply(); });
$("#reload").addEventListener("click", loadData);
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => { if (!$("#modal").classList.contains("open")) return; if (e.key === "Escape") closeModal(); if (e.key === "ArrowLeft" && STATE.gallery.imgs.length > 1) gal(-1); if (e.key === "ArrowRight" && STATE.gallery.imgs.length > 1) gal(1); });

function showState(msg, loading) {
  $("#grid").innerHTML = `<div class="state">${loading ? '<div class="skeleton" style="height:6px;width:160px;margin:0 auto 14px;border-radius:3px"></div>' : ""}<h3>${msg}</h3></div>`;
  $("#pager").innerHTML = ""; $("#showing").textContent = "";
}
loadData();
