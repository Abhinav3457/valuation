/* =========================================================
   Stock Valuation Dashboard — frontend
   Vanilla JS. No build step. No CDN.
   ========================================================= */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const fmt = {
  money: (v) => "Rs " + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }),
  pct:   (v) => (v >= 0 ? "+" : "") + v.toFixed(1) + "%",
  bigNum: (v) => {
    if (v == null || isNaN(v)) return "-";
    const abs = Math.abs(v);
    if (abs >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (abs >= 1e7) return "Rs " + (v / 1e7).toFixed(2) + " Cr";
    if (abs >= 1e5) return "Rs " + (v / 1e5).toFixed(2) + " L";
    return "Rs " + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  },
  pctRaw: (v) => v != null ? (v * 100).toFixed(2) + "%" : "-",
  num2: (v) => v != null ? Number(v).toFixed(2) : "-",
  num0: (v) => v != null ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 }) : "-",
};

function recoClass(r) {
  return ({ "Strong Buy": "buy-strong", Buy: "buy", Hold: "hold", Avoid: "avoid" })[r] || "hold";
}
function gaugeColor(s) {
  if (s >= 75) return "#16c784";
  if (s >= 60) return "#5fd6a3";
  if (s >= 45) return "#f5b042";
  return "#ea4d5c";
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

// ===========================================================
// State
// ===========================================================
const state = {
  results: [],       // cached analyze-all response
  sort: { key: "score", dir: "desc" },
  filter: "",
  filters: {        // structured filters for the Browse tab
    reco: new Set(),  // multi-select: any of the active labels matches
    sector: "",
    scoreMin: null,
    scoreMax: null,
    gap: "",
  },
  selected: new Set(),    // symbols ticked via checkbox
  visibleRows: [],        // currently-rendered (filtered+sorted) rows
  compareSymbols: [],
  hasData: false,
};

// ===========================================================
// Tab switching
// ===========================================================
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `tab-${target}`));
    if (target === "browse" && state.hasData && !state.results.length) loadBrowse();
    if (target === "top" && state.hasData && !$("#top-cards .card")) loadTop();
  });
});

// ===========================================================
// Option A: One-click fetch NIFTY 50
// ===========================================================
$("#btn-fetch").addEventListener("click", async () => {
  const btn = $("#btn-fetch");
  const prog = $("#fetch-progress");
  btn.disabled = true;
  btn.textContent = "Fetching...";
  prog.classList.remove("hidden");
  $("#progress-text").textContent = "Connecting to Yahoo Finance...";
  $("#progress-fill").style.width = "0%";

  try {
    const limit = $("#fetch-limit").value;
    const start = await api(`/api/fetch-nifty?limit=${limit}`);
    const total = start.total;

    // Poll progress every 1s until done. During long fetches (500+
    // stocks) a single transient error shouldn't kill the poll —
    // tolerate up to N consecutive failures before bailing.
    let consecutiveErrors = 0;
    const MAX_POLL_ERRORS = 5;
    const poll = setInterval(async () => {
      try {
        const s = await api("/api/fetch-status");
        consecutiveErrors = 0;
        const pct = total > 0 ? Math.round((s.done / total) * 100) : 0;
        $("#progress-fill").style.width = pct + "%";
        const loadedTxt = s.loaded != null ? ` (${s.loaded} with data)` : "";
        $("#progress-text").textContent = `Fetching ${s.done} / ${total}${loadedTxt}...`;

        if (!s.running) {
          clearInterval(poll);
          // Always try to surface whatever data made it through —
          // even on "partial" or "error" status, we may have a
          // usable subset of companies already in UPLOADED.
          const meta = await api("/api/meta").catch(() => ({ count: 0 }));
          const haveData = (meta.count || 0) > 0;

          if (s.status === "done") {
            $("#progress-text").textContent = `Done! ${meta.count} companies loaded.`;
          } else if (haveData) {
            $("#progress-text").textContent =
              `Partial: ${meta.count}/${total} loaded. ${s.status}`;
          } else {
            $("#progress-text").textContent = `Error: ${s.status}`;
          }

          if (haveData) {
            state.hasData = true;
            // IMPORTANT: show the "View Results" prompt BEFORE attempting
            // to pre-fetch analyze-all. A single malformed company used
            // to crash that endpoint and leave the UI without a prompt —
            // now we always surface the button first, then try to warm
            // the cache; failures there are non-fatal.
            const label = s.status === "done"
              ? "Auto-fetched from Yahoo Finance"
              : `Auto-fetched (partial: ${meta.count}/${total})`;
            showUploadStatus(meta.count, label, []);
            try {
              state.results = await api("/api/analyze-all");
            } catch (err) {
              // Non-fatal — the Browse tab will re-fetch on click.
              state.results = [];
              console.warn("analyze-all warm-up failed:", err.message);
            }
          }

          btn.disabled = false;
          btn.textContent = "Fetch Data";
        }
      } catch (e) {
        consecutiveErrors++;
        if (consecutiveErrors < MAX_POLL_ERRORS) {
          // Transient failure — just skip this tick and try again.
          $("#progress-text").textContent =
            `Poll hiccup (${consecutiveErrors}/${MAX_POLL_ERRORS}) — retrying...`;
          return;
        }
        clearInterval(poll);
        // Even if polling itself fails repeatedly, check if we have
        // any data to show — the background fetch may have finished.
        try {
          const meta = await api("/api/meta");
          if ((meta.count || 0) > 0) {
            state.hasData = true;
            showUploadStatus(meta.count, `Auto-fetched (recovered after error)`, []);
            $("#progress-text").textContent =
              `Recovered ${meta.count} companies despite error: ${e.message}`;
            try { state.results = await api("/api/analyze-all"); }
            catch { state.results = []; }
          } else {
            $("#progress-text").textContent = "Poll error: " + e.message;
          }
        } catch {
          $("#progress-text").textContent = "Poll error: " + e.message;
        }
        btn.disabled = false;
        btn.textContent = "Fetch Data";
      }
    }, 1000);
  } catch (e) {
    prog.classList.add("hidden");
    btn.disabled = false;
    btn.textContent = "Fetch Data";
    alert("Fetch failed: " + e.message);
  }
});

// ===========================================================
// Option B: Download template
// ===========================================================
$("#btn-download").addEventListener("click", () => {
  window.location.href = "/api/template";
});

// ===========================================================
// Step 3: Upload CSV
// ===========================================================
const dropZone = $("#drop-zone");
const fileInput = $("#file-input");

// Drag-and-drop visual feedback
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("hover"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("hover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("hover");
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});
dropZone.addEventListener("click", (e) => {
  if (e.target.tagName !== "INPUT") fileInput.click();
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

function handleFile(file) {
  const hint = $("#drop-hint");
  if (!file.name.toLowerCase().endsWith(".csv")) {
    hint.textContent = "Please upload a .csv file.";
    hint.className = "drop-hint error";
    return;
  }
  hint.textContent = `Reading ${file.name}...`;
  hint.className = "drop-hint";

  const reader = new FileReader();
  reader.onload = () => parseAndUpload(reader.result, file.name);
  reader.onerror = () => {
    hint.textContent = "Failed to read file.";
    hint.className = "drop-hint error";
  };
  reader.readAsText(file);
}

function parseAndUpload(csvText, fileName) {
  const hint = $("#drop-hint");
  try {
    const rows = parseCSV(csvText);
    if (!rows.length) {
      hint.textContent = "CSV has no data rows.";
      hint.className = "drop-hint error";
      return;
    }
    hint.textContent = `Uploading ${rows.length} companies...`;
    sendToServer(rows, fileName);
  } catch (e) {
    hint.textContent = `Parse error: ${e.message}`;
    hint.className = "drop-hint error";
  }
}

// Minimal CSV parser — handles commas in quoted fields
function parseCSV(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return [];
  const headers = splitCSVLine(lines[0]).map((h) => h.trim().toLowerCase());
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const vals = splitCSVLine(lines[i]);
    if (vals.length < headers.length) continue;
    const obj = {};
    headers.forEach((h, idx) => {
      let v = vals[idx].trim();
      // Try to convert numeric-looking fields
      if (v !== "" && !isNaN(Number(v))) v = Number(v);
      else if (v === "") v = null;
      obj[h] = v;
    });
    // Skip rows that lack a symbol or price
    if (!obj.symbol || (!obj.current_price && obj.current_price !== 0)) continue;
    rows.push(obj);
  }
  return rows;
}

function splitCSVLine(line) {
  const result = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQuotes = false;
      else cur += ch;
    } else {
      if (ch === '"') inQuotes = true;
      else if (ch === ',') { result.push(cur); cur = ""; }
      else cur += ch;
    }
  }
  result.push(cur);
  return result;
}

function showUploadStatus(count, source, errors) {
  const status = $("#upload-status");
  status.classList.remove("hidden");
  $("#status-count").textContent = `${count} companies loaded (${source})`;
  if (errors && errors.length) {
    status.classList.add("has-errors");
    $("#status-errors").textContent = `${errors.length} row(s) skipped: ${errors[0]}`;
  } else {
    status.classList.remove("has-errors");
    $("#status-errors").textContent = "All data loaded successfully.";
  }
}

async function sendToServer(rows, fileName) {
  const hint = $("#drop-hint");
  try {
    const resp = await api("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rows),
    });

    state.hasData = resp.loaded > 0;
    showUploadStatus(resp.loaded, fileName, resp.errors);
    hint.textContent = "";

    if (state.hasData) {
      state.results = await api("/api/analyze-all");
    }
  } catch (e) {
    hint.textContent = `Upload failed: ${e.message}`;
    hint.className = "drop-hint error";
  }
}

// "View Results" button — switches to browse tab
$("#btn-view-results").addEventListener("click", () => {
  // Activate browse tab
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === "browse"));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "tab-browse"));
  renderBrowse();
});

// ===========================================================
// Browse tab
// ===========================================================
async function loadBrowse() {
  if (!state.hasData) return;
  try {
    if (!state.results.length) state.results = await api("/api/analyze-all");
    renderBrowse();
  } catch (e) {
    $("#browse-table tbody").innerHTML = `<tr><td colspan="7" class="muted">Failed: ${e.message}</td></tr>`;
  }
}

function applyFilters(results) {
  const q = state.filter.trim().toLowerCase();
  const f = state.filters;
  return results.filter((r) => {
    // Free-text search
    if (q && !(
      r.symbol.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q) ||
      (r.sector || "").toLowerCase().includes(q)
    )) return false;
    // Recommendation (multi-select; empty set = match all)
    if (f.reco.size && !f.reco.has(r.recommendation)) return false;
    // Sector
    if (f.sector && (r.sector || "") !== f.sector) return false;
    // Score range
    if (f.scoreMin != null && r.final_score < f.scoreMin) return false;
    if (f.scoreMax != null && r.final_score > f.scoreMax) return false;
    // Gap bucket
    const g = r.valuation_gap_pct;
    if (f.gap === "under" && !(g > 0)) return false;
    if (f.gap === "over" && !(g < 0)) return false;
    if (f.gap === "strong-under" && !(g > 25)) return false;
    if (f.gap === "strong-over" && !(g < -25)) return false;
    return true;
  });
}

function populateSectorFilter() {
  const sel = $("#filter-sector");
  if (!sel) return;
  const current = sel.value;
  const sectors = [...new Set(state.results.map((r) => r.sector).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">All</option>` +
    sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
  if (sectors.includes(current)) sel.value = current;
}

function renderBrowse() {
  if (!state.results.length) return;
  $("#browse-empty").classList.add("hidden");
  $("#browse-table-wrap").classList.remove("hidden");
  $("#browse-filters").classList.remove("hidden");
  populateSectorFilter();

  let rows = applyFilters(state.results);

  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    let av, bv;
    switch (key) {
      case "symbol": av = a.symbol; bv = b.symbol; break;
      case "name":   av = a.name; bv = b.name; break;
      case "sector": av = (a.sector || ""); bv = (b.sector || ""); break;
      case "price":  av = a.current_price; bv = b.current_price; break;
      case "score":  av = a.final_score; bv = b.final_score; break;
      case "gap":    av = a.valuation_gap_pct; bv = b.valuation_gap_pct; break;
      default:       av = 0; bv = 0;
    }
    if (av < bv) return dir === "asc" ? -1 : 1;
    if (av > bv) return dir === "asc" ? 1 : -1;
    return 0;
  });

  state.visibleRows = rows;

  const tbody = $("#browse-table tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No companies match.</td></tr>`;
  } else {
    tbody.innerHTML = rows
      .map((r) => {
        const checked = state.selected.has(r.symbol) ? "checked" : "";
        const selectedCls = state.selected.has(r.symbol) ? " row-selected" : "";
        return `
      <tr data-symbol="${r.symbol}" class="${selectedCls.trim()}">
        <td class="check-col"><input type="checkbox" class="row-check" data-symbol="${r.symbol}" ${checked} /></td>
        <td class="symbol">${r.symbol}</td>
        <td>${r.name}</td>
        <td class="muted">${r.sector || "-"}</td>
        <td class="num">${fmt.money(r.current_price)}</td>
        <td class="num">${r.final_score.toFixed(1)}</td>
        <td class="num" style="color:${r.valuation_gap_pct >= 0 ? "var(--buy-strong)" : "var(--avoid)"}">
          ${fmt.pct(r.valuation_gap_pct)}
        </td>
        <td><span class="badge ${recoClass(r.recommendation)}">${r.recommendation}</span></td>
      </tr>`;
      })
      .join("");

    // Row click → drawer (but not when clicking a checkbox)
    $$("#browse-table tbody tr").forEach((tr) =>
      tr.addEventListener("click", (e) => {
        if (e.target.closest("input[type='checkbox']")) return;
        openDrawer(tr.dataset.symbol);
      })
    );
    // Per-row checkbox handler
    $$(".row-check").forEach((cb) =>
      cb.addEventListener("change", () => {
        const sym = cb.dataset.symbol;
        if (cb.checked) state.selected.add(sym);
        else state.selected.delete(sym);
        cb.closest("tr").classList.toggle("row-selected", cb.checked);
        updateExportButtons();
        syncSelectAllState();
      })
    );
  }

  $("#browse-count").textContent = `${rows.length} of ${state.results.length} companies` +
    (state.selected.size ? ` · ${state.selected.size} selected` : "");
  syncSelectAllState();
  updateExportButtons();
}

function syncSelectAllState() {
  const visible = state.visibleRows;
  const checkAll = $("#check-all");
  if (!checkAll) return;
  if (!visible.length) { checkAll.checked = false; checkAll.indeterminate = false; return; }
  const selectedVisible = visible.filter((r) => state.selected.has(r.symbol)).length;
  checkAll.checked = selectedVisible === visible.length;
  checkAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
}

function updateExportButtons() {
  const btn = $("#export-selected");
  if (btn) btn.disabled = state.selected.size === 0;
}

$("#search").addEventListener("input", (e) => {
  state.filter = e.target.value;
  renderBrowse();
});

// ---- Filter inputs ----
// Recommendation multi-select pills — click to toggle each independently.
$$("#filter-reco .reco-pill").forEach((btn) => {
  btn.addEventListener("click", () => {
    const reco = btn.dataset.reco;
    if (state.filters.reco.has(reco)) {
      state.filters.reco.delete(reco);
      btn.classList.remove("active");
    } else {
      state.filters.reco.add(reco);
      btn.classList.add("active");
    }
    renderBrowse();
  });
});
$("#filter-sector").addEventListener("change", (e) => {
  state.filters.sector = e.target.value;
  renderBrowse();
});
$("#filter-score-min").addEventListener("input", (e) => {
  const v = e.target.value;
  state.filters.scoreMin = v === "" ? null : Number(v);
  renderBrowse();
});
$("#filter-score-max").addEventListener("input", (e) => {
  const v = e.target.value;
  state.filters.scoreMax = v === "" ? null : Number(v);
  renderBrowse();
});
$("#filter-gap").addEventListener("change", (e) => {
  state.filters.gap = e.target.value;
  renderBrowse();
});
$("#filter-reset").addEventListener("click", () => {
  state.filter = "";
  state.filters = {
    reco: new Set(),
    sector: "",
    scoreMin: null,
    scoreMax: null,
    gap: "",
  };
  $("#search").value = "";
  $$("#filter-reco .reco-pill").forEach((b) => b.classList.remove("active"));
  $("#filter-sector").value = "";
  $("#filter-score-min").value = "";
  $("#filter-score-max").value = "";
  $("#filter-gap").value = "";
  renderBrowse();
});

// ---- Select-all ----
$("#check-all").addEventListener("change", (e) => {
  const check = e.target.checked;
  state.visibleRows.forEach((r) => {
    if (check) state.selected.add(r.symbol);
    else state.selected.delete(r.symbol);
  });
  renderBrowse();
});

// ---- CSV export ----
function buildCSV(rows) {
  const headers = [
    "symbol", "name", "sector", "current_price",
    "intrinsic_value", "intrinsic_low", "intrinsic_high",
    "valuation_gap_pct", "final_score", "recommendation",
  ];
  const escape = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.join(",")];
  for (const r of rows) {
    lines.push([
      r.symbol, r.name, r.sector || "",
      r.current_price, r.intrinsic_value,
      r.intrinsic_value_range ? r.intrinsic_value_range[0] : "",
      r.intrinsic_value_range ? r.intrinsic_value_range[1] : "",
      r.valuation_gap_pct, r.final_score, r.recommendation,
    ].map(escape).join(","));
  }
  return lines.join("\n");
}

function downloadCSV(filename, csv) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

$("#export-filtered").addEventListener("click", () => {
  if (!state.visibleRows.length) {
    alert("No rows to export. Adjust your filters.");
    return;
  }
  downloadCSV(`stock-valuation-filtered-${timestamp()}.csv`, buildCSV(state.visibleRows));
});

$("#export-selected").addEventListener("click", () => {
  const rows = state.results.filter((r) => state.selected.has(r.symbol));
  if (!rows.length) return;
  downloadCSV(`stock-valuation-selected-${timestamp()}.csv`, buildCSV(rows));
});

$$("#browse-table th").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (!key) return;
    if (state.sort.key === key) state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
    else {
      state.sort.key = key;
      state.sort.dir = key === "symbol" || key === "name" || key === "sector" ? "asc" : "desc";
    }
    renderBrowse();
  });
});

// ===========================================================
// Top picks tab
// ===========================================================
async function loadTop() {
  if (!state.hasData) return;
  const n = Number($("#top-n").value) || 50;
  const grid = $("#top-cards");
  grid.innerHTML = `<div class="empty">Loading...</div>`;
  try {
    const results = await api(`/api/top?n=${n}`);
    if (!results.length) {
      grid.innerHTML = `<div class="empty">No undervalued companies found.</div>`;
      return;
    }
    grid.innerHTML = results.map((r, i) => cardHTML(r, i + 1)).join("");
    $$(".card", grid).forEach((c) =>
      c.addEventListener("click", () => openDrawer(c.dataset.symbol))
    );
  } catch (e) {
    grid.innerHTML = `<div class="empty">Failed: ${e.message}</div>`;
  }
}

function cardHTML(r, rank) {
  const color = gaugeColor(r.final_score);
  return `
    <div class="card" data-symbol="${r.symbol}">
      <div class="card-head">
        <div>
          <div class="name">#${rank} - ${r.name}</div>
          <div class="sym">${r.symbol}</div>
        </div>
        <span class="badge ${recoClass(r.recommendation)}">${r.recommendation}</span>
      </div>
      <div class="gauge" style="--pct:${r.final_score};--color:${color}">
        <div class="gauge-text">
          <strong>${r.final_score.toFixed(0)}</strong>
          <small>SCORE</small>
        </div>
      </div>
      <div class="card-row"><span class="label">Price</span><span class="value">${fmt.money(r.current_price)}</span></div>
      <div class="card-row"><span class="label">Intrinsic</span><span class="value">${fmt.money(r.intrinsic_value)}</span></div>
      <div class="card-row"><span class="label">Gap</span>
        <span class="value" style="color:${r.valuation_gap_pct >= 0 ? "var(--buy-strong)" : "var(--avoid)"}">
          ${fmt.pct(r.valuation_gap_pct)}
        </span>
      </div>
    </div>
  `;
}

$("#top-refresh").addEventListener("click", loadTop);
$("#top-n").addEventListener("change", loadTop);

// ===========================================================
// Compare tab
// ===========================================================
$("#compare-input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const sym = e.target.value.trim().toUpperCase();
  if (!sym) return;
  if (!state.compareSymbols.includes(sym)) {
    state.compareSymbols.push(sym);
    renderCompareChips();
    loadCompare();
  }
  e.target.value = "";
});

$("#compare-clear").addEventListener("click", () => {
  state.compareSymbols = [];
  renderCompareChips();
  $("#compare-result").innerHTML = `<div class="empty">Add symbols above to compare.</div>`;
});

function renderCompareChips() {
  $("#compare-chips").innerHTML = state.compareSymbols
    .map((s) => `<span class="chip">${s}<button data-sym="${s}" title="Remove">&times;</button></span>`)
    .join("");
  $$("#compare-chips .chip button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.compareSymbols = state.compareSymbols.filter((x) => x !== btn.dataset.sym);
      renderCompareChips();
      loadCompare();
    })
  );
}

async function loadCompare() {
  const result = $("#compare-result");
  if (!state.compareSymbols.length) {
    result.innerHTML = `<div class="empty">Add 2 or more symbols to compare.</div>`;
    return;
  }
  try {
    const data = await api(`/api/compare?symbols=${state.compareSymbols.join(",")}`);
    result.innerHTML = `
      <div class="compare-table">
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Name</th>
              <th class="num">Price</th><th class="num">Intrinsic</th>
              <th class="num">Gap %</th><th class="num">Score</th>
              <th>Recommendation</th>
            </tr>
          </thead>
          <tbody>
            ${data
              .map(
                (r) => `
              <tr data-symbol="${r.symbol}">
                <td class="symbol">${r.symbol}</td>
                <td>${r.name}</td>
                <td class="num">${fmt.money(r.current_price)}</td>
                <td class="num">${fmt.money(r.intrinsic_value)}</td>
                <td class="num" style="color:${r.valuation_gap_pct >= 0 ? "var(--buy-strong)" : "var(--avoid)"}">
                  ${fmt.pct(r.valuation_gap_pct)}
                </td>
                <td class="num">${r.final_score.toFixed(1)}</td>
                <td><span class="badge ${recoClass(r.recommendation)}">${r.recommendation}</span></td>
              </tr>
            `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `;
    $$("#compare-result tbody tr").forEach((tr) =>
      tr.addEventListener("click", () => openDrawer(tr.dataset.symbol))
    );
  } catch (e) {
    result.innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

// ===========================================================
// Detail drawer
// ===========================================================
async function openDrawer(symbol) {
  const drawer = $("#drawer");
  const overlay = $("#overlay");
  drawer.classList.remove("hidden");
  overlay.classList.remove("hidden");
  $("#drawer-content").innerHTML = `<div class="empty">Loading ${symbol}...</div>`;
  try {
    const r = await api(`/api/detail/${symbol}`);
    $("#drawer-content").innerHTML = drawerHTML(r);
  } catch (e) {
    $("#drawer-content").innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#overlay").classList.add("hidden");
}
$("#drawer-close").addEventListener("click", closeDrawer);
$("#overlay").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

function stripProtocol(url) {
  return url ? url.replace(/^https?:\/\//, "") : url;
}

function _v(val, formatter) {
  if (val == null || val === "" || (typeof val === "number" && isNaN(val))) return "-";
  return formatter ? formatter(val) : val;
}

function kvRow(label, value) {
  return `<div class="kv"><div class="k">${label}</div><div class="v">${value}</div></div>`;
}

function sectionTitle(title) {
  return `<h3 class="drawer-section-title">${title}</h3>`;
}

function drawerHTML(r) {
  const y = r.yahoo || {};
  const color = gaugeColor(r.final_score);
  const gapClass = r.valuation_gap_pct >= 0 ? "pos" : "neg";
  const lo = r.intrinsic_value_range[0];
  const hi = r.intrinsic_value_range[1];

  // Model breakdown bars
  const bars = Object.entries(r.model_scores)
    .map(([name, ms]) => {
      const pct = Math.max(0, Math.min(100, ms.score));
      return `
      <div class="bar-row">
        <div class="bar-head">
          <span><strong>${name}</strong></span>
          <span>${ms.score.toFixed(0)}/100 &middot; w ${(ms.weight * 100).toFixed(0)}%</span>
        </div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
        <div class="bar-note">${ms.notes}</div>
      </div>`;
    }).join("");

  // Price position within 52-week range
  const low52 = y.fiftyTwoWeekLow;
  const high52 = y.fiftyTwoWeekHigh;
  const price = r.current_price;
  let rangePct = 50;
  if (low52 && high52 && high52 > low52) {
    rangePct = Math.round(((price - low52) / (high52 - low52)) * 100);
    rangePct = Math.max(0, Math.min(100, rangePct));
  }

  // Analyst recommendation text
  const recoMap = { buy: "Buy", strongbuy: "Strong Buy", strong_buy: "Strong Buy", hold: "Hold", sell: "Sell", underperform: "Underperform" };
  const yReco = recoMap[y.recommendationKey] || y.recommendationKey || "-";

  // External links
  const sym = r.symbol;
  const tickertapeUrl = `https://www.tickertape.in/stocks/${sym}`;
  const screenerUrl = `https://www.screener.in/company/${sym}/`;
  const moneycontrolSearch = `https://www.moneycontrol.com/stocks/cptmarket/compsearchnew.php?search_data=${sym}&search_str=`;

  // Company summary (truncated)
  let summary = y.longBusinessSummary || "";
  if (summary.length > 300) summary = summary.slice(0, 300) + "...";

  return `
    <!-- HEADER -->
    <div class="drawer-header">
      <div>
        <h2>${r.name}</h2>
        <div class="drawer-subtitle">
          <span class="sym-line">${r.symbol}</span>
          ${y.sector ? `<span class="drawer-tag">${y.sector}</span>` : ""}
          ${y.industry ? `<span class="drawer-tag dim">${y.industry}</span>` : ""}
        </div>
      </div>
      <span class="badge ${recoClass(r.recommendation)} badge-lg">${r.recommendation}</span>
    </div>

    ${summary ? `<p class="drawer-summary">${summary}</p>` : ""}

    <!-- EXTERNAL LINKS -->
    <div class="drawer-links">
      <a href="${tickertapeUrl}" target="_blank" rel="noopener" class="btn secondary btn-sm">Tickertape</a>
      <a href="${screenerUrl}" target="_blank" rel="noopener" class="btn secondary btn-sm">Screener.in</a>
      <a href="${moneycontrolSearch}" target="_blank" rel="noopener" class="btn secondary btn-sm">Moneycontrol</a>
      ${y.website ? `<a href="${y.website}" target="_blank" rel="noopener" class="btn ghost btn-sm">Company Website</a>` : ""}
    </div>

    <!-- SCORE GAUGE -->
    <div class="drawer-score-row">
      <div class="gauge" style="--pct:${r.final_score};--color:${color}">
        <div class="gauge-text">
          <strong>${r.final_score.toFixed(0)}</strong>
          <small>SCORE</small>
        </div>
      </div>
      <div class="drawer-score-summary">
        <div class="kv compact"><div class="k">Intrinsic Value</div><div class="v">${fmt.money(r.intrinsic_value)}</div></div>
        <div class="kv compact"><div class="k">Intrinsic Range</div><div class="v">${fmt.money(lo)} - ${fmt.money(hi)}</div></div>
        <div class="kv compact"><div class="k">Valuation Gap</div><div class="v ${gapClass}">${fmt.pct(r.valuation_gap_pct)}</div></div>
      </div>
    </div>

    <!-- PRICE OVERVIEW -->
    ${sectionTitle("Price Overview")}
    <div class="kv-grid cols-3">
      ${kvRow("Current Price", fmt.money(price))}
      ${kvRow("Previous Close", _v(y.previousClose, fmt.money))}
      ${kvRow("Open", _v(y.open, fmt.money))}
      ${kvRow("Day Low", _v(y.dayLow, fmt.money))}
      ${kvRow("Day High", _v(y.dayHigh, fmt.money))}
      ${kvRow("Volume", _v(y.volume, fmt.num0))}
      ${kvRow("Avg Volume (10d)", _v(y.averageDailyVolume10Day, fmt.num0))}
      ${kvRow("50-Day Avg", _v(y.fiftyDayAverage, fmt.money))}
      ${kvRow("200-Day Avg", _v(y.twoHundredDayAverage, fmt.money))}
    </div>

    <!-- 52-WEEK RANGE BAR -->
    ${(low52 && high52) ? `
    <div class="range-bar-wrap">
      <div class="range-labels"><span>${fmt.money(low52)}</span><span>52-Week Range</span><span>${fmt.money(high52)}</span></div>
      <div class="range-track">
        <div class="range-marker" style="left:${rangePct}%">
          <div class="range-marker-dot"></div>
          <div class="range-marker-label">${fmt.money(price)}</div>
        </div>
      </div>
    </div>` : ""}

    <!-- VALUATION RATIOS -->
    ${sectionTitle("Valuation Ratios")}
    <div class="kv-grid cols-3">
      ${kvRow("Trailing P/E", _v(y.trailingPE, fmt.num2))}
      ${kvRow("Forward P/E", _v(y.forwardPE, fmt.num2))}
      ${kvRow("P/B Ratio", _v(y.priceToBook, fmt.num2))}
      ${kvRow("PEG Ratio", _v(y.trailingPegRatio, fmt.num2))}
      ${kvRow("P/S (TTM)", _v(y.priceToSalesTrailing12Months, fmt.num2))}
      ${kvRow("EV/Revenue", _v(y.enterpriseToRevenue, fmt.num2))}
      ${kvRow("EV/EBITDA", _v(y.enterpriseToEbitda, fmt.num2))}
      ${kvRow("Market Cap", _v(y.marketCap, fmt.bigNum))}
      ${kvRow("Enterprise Value", _v(y.enterpriseValue, fmt.bigNum))}
    </div>

    <!-- FINANCIALS -->
    ${sectionTitle("Financials")}
    <div class="kv-grid cols-3">
      ${kvRow("Revenue (TTM)", _v(y.totalRevenue, fmt.bigNum))}
      ${kvRow("Net Income", _v(y.netIncomeToCommon, fmt.bigNum))}
      ${kvRow("EBITDA", _v(y.ebitda, fmt.bigNum))}
      ${kvRow("Gross Profit", _v(y.grossProfits, fmt.bigNum))}
      ${kvRow("Revenue/Share", _v(y.revenuePerShare, fmt.num2))}
      ${kvRow("EPS (TTM)", _v(y.trailingEps, fmt.num2))}
      ${kvRow("EPS (Forward)", _v(y.forwardEps, fmt.num2))}
      ${kvRow("Profit Margin", _v(y.profitMargins, fmt.pctRaw))}
      ${kvRow("Operating Margin", _v(y.operatingMargins, fmt.pctRaw))}
      ${kvRow("Gross Margin", _v(y.grossMargins, fmt.pctRaw))}
      ${kvRow("EBITDA Margin", _v(y.ebitdaMargins, fmt.pctRaw))}
      ${kvRow("Revenue Growth", _v(y.revenueGrowth, fmt.pctRaw))}
    </div>

    <!-- BALANCE SHEET & CASH FLOW -->
    ${sectionTitle("Balance Sheet & Cash Flow")}
    <div class="kv-grid cols-3">
      ${kvRow("Total Cash", _v(y.totalCash, fmt.bigNum))}
      ${kvRow("Total Debt", _v(y.totalDebt, fmt.bigNum))}
      ${kvRow("Debt/Equity", _v(y.debtToEquity, fmt.num2))}
      ${kvRow("Current Ratio", _v(y.currentRatio, fmt.num2))}
      ${kvRow("Quick Ratio", _v(y.quickRatio, fmt.num2))}
      ${kvRow("Book Value/Share", _v(y.bookValue, fmt.num2))}
      ${kvRow("Operating Cash Flow", _v(y.operatingCashflow, fmt.bigNum))}
      ${kvRow("Free Cash Flow", _v(y.freeCashflow, fmt.bigNum))}
      ${kvRow("Cash/Share", _v(y.totalCashPerShare, fmt.num2))}
    </div>

    <!-- GROWTH & RETURNS -->
    ${sectionTitle("Growth & Returns")}
    <div class="kv-grid cols-3">
      ${kvRow("ROE", _v(y.returnOnEquity, fmt.pctRaw))}
      ${kvRow("ROA", _v(y.returnOnAssets, fmt.pctRaw))}
      ${kvRow("Earnings Growth", _v(y.earningsGrowth, fmt.pctRaw))}
      ${kvRow("Quarterly Earnings Growth", _v(y.earningsQuarterlyGrowth, fmt.pctRaw))}
      ${kvRow("Revenue Growth", _v(y.revenueGrowth, fmt.pctRaw))}
      ${kvRow("52-Week Change", _v(y["52WeekChange"], fmt.pctRaw))}
      ${kvRow("Beta", _v(y.beta, fmt.num2))}
    </div>

    <!-- DIVIDENDS -->
    ${sectionTitle("Dividends")}
    <div class="kv-grid cols-3">
      ${kvRow("Dividend Rate", _v(y.dividendRate, fmt.num2))}
      ${kvRow("Dividend Yield", _v(y.dividendYield, fmt.pctRaw))}
      ${kvRow("Payout Ratio", _v(y.payoutRatio, fmt.pctRaw))}
      ${kvRow("5Y Avg Yield", y.fiveYearAvgDividendYield != null ? y.fiveYearAvgDividendYield.toFixed(2) + "%" : "-")}
      ${kvRow("Ex-Dividend Date", y.exDividendDate ? new Date(y.exDividendDate * 1000).toLocaleDateString("en-IN") : "-")}
    </div>

    <!-- ANALYST ESTIMATES -->
    ${sectionTitle("Analyst Estimates")}
    <div class="kv-grid cols-3">
      ${kvRow("Analyst Consensus", yReco)}
      ${kvRow("# Analysts", _v(y.numberOfAnalystOpinions, fmt.num0))}
      ${kvRow("Target Low", _v(y.targetLowPrice, fmt.money))}
      ${kvRow("Target Mean", _v(y.targetMeanPrice, fmt.money))}
      ${kvRow("Target Median", _v(y.targetMedianPrice, fmt.money))}
      ${kvRow("Target High", _v(y.targetHighPrice, fmt.money))}
    </div>

    <!-- OWNERSHIP -->
    ${sectionTitle("Ownership & Shares")}
    <div class="kv-grid cols-3">
      ${kvRow("Shares Outstanding", _v(y.sharesOutstanding, fmt.bigNum))}
      ${kvRow("Float Shares", _v(y.floatShares, fmt.bigNum))}
      ${kvRow("Insider Holding", _v(y.heldPercentInsiders, fmt.pctRaw))}
      ${kvRow("Institutional Holding", _v(y.heldPercentInstitutions, fmt.pctRaw))}
    </div>

    <!-- COMPANY INFO -->
    ${(y.fullTimeEmployees || y.city || y.country) ? `
    ${sectionTitle("Company Info")}
    <div class="kv-grid cols-3">
      ${y.fullTimeEmployees ? kvRow("Employees", fmt.num0(y.fullTimeEmployees)) : ""}
      ${y.city ? kvRow("Headquarters", y.city + (y.country ? ", " + y.country : "")) : ""}
      ${y.website ? kvRow("Website", `<a href="${y.website}" target="_blank" rel="noopener">${stripProtocol(y.website)}</a>`) : ""}
    </div>` : ""}

    <!-- MODEL BREAKDOWN -->
    ${sectionTitle("Valuation Model Breakdown")}
    <div class="bars">${bars}</div>
  `;
}

// ===========================================================
// Auto-resume previous session
// ===========================================================
function formatAge(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  return `${Math.floor(seconds / 86400)} days ago`;
}

async function resumeSession() {
  try {
    const meta = await api("/api/meta");
    if (!meta.count || meta.count <= 0) return;

    state.hasData = true;
    const ageStr = meta.session_age_seconds != null
      ? `last updated ${formatAge(meta.session_age_seconds)}`
      : "from previous session";
    showUploadStatus(meta.count, `Resumed: ${meta.count} companies (${ageStr})`, []);

    // Add a Clear button next to View Results, only if not already there
    if (!$("#btn-clear-session")) {
      const btn = document.createElement("button");
      btn.id = "btn-clear-session";
      btn.className = "btn ghost btn-sm";
      btn.textContent = "Clear & Start Fresh";
      btn.style.marginLeft = "10px";
      btn.addEventListener("click", clearSession);
      $("#upload-status").appendChild(btn);
    }

    // Pre-warm the results cache so Browse opens instantly
    try {
      state.results = await api("/api/analyze-all");
    } catch (e) {
      state.results = [];
      console.warn("analyze-all warm-up failed:", e.message);
    }
  } catch (e) {
    console.warn("resume check failed:", e.message);
  }
}

async function clearSession() {
  if (!confirm("Clear all loaded data? This cannot be undone.")) return;
  try {
    await api("/api/clear");
    state.hasData = false;
    state.results = [];
    state.selected.clear();
    $("#upload-status").classList.add("hidden");
    const clr = $("#btn-clear-session");
    if (clr) clr.remove();
    // Clear browse view
    $("#browse-empty").classList.remove("hidden");
    $("#browse-table-wrap").classList.add("hidden");
    $("#browse-filters").classList.add("hidden");
    $("#browse-table tbody").innerHTML = "";
  } catch (e) {
    alert("Clear failed: " + e.message);
  }
}

// Run on page load
resumeSession();
