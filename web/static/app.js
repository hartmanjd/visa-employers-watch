// Visa Employer Watch: a small hash-routed front end over the JSON API in web/api.py.
// Routes: #/ (lookup), #/watchlist?filters, #/employer/<id>, #/ask.
"use strict";

const app = document.getElementById("app");
const state = { back: { href: "#/", label: "Employer lookup" }, meta: null };

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n = (x) => (x == null || x === "" ? "–" : Number(x).toLocaleString("en-US"));
const money = (x) => "$" + Math.round(Number(x || 0)).toLocaleString("en-US");
const compactMoney = (x) => {
  const v = Number(x || 0);
  if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  if (v >= 1e4) return "$" + Math.round(v / 1e3) + "K";
  return money(v);
};
const day = (s) => (s ? String(s).slice(0, 10) : "–");
const titleCase = (s) => /[a-z]/.test(s || "") ? s : String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
const programBadge = (p) => `<span class="badge ${p === "H-2B" ? "h2b" : "h1b"}">${esc(p)}</span>`;
const icon = {
  search: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  back: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  chev: '<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
  info: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>',
  download: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0-4-4m4 4 4-4M4 21h16"/></svg>',
};

async function api(path, params) {
  const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)) : "";
  const r = await fetch("/api" + path + qs);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// A download link for one of the API's CSV files; every row carries the IDs of the records it came from.
function csvLink(path, params, title) {
  const qs = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)).toString() : "";
  return `<a class="btn csv-link" href="/api${path}${qs ? "?" + qs : ""}" download title="${esc(title)}">${icon.download} CSV</a>`;
}

function loading(text = "Loading…") {
  app.innerHTML = `<div class="loading"><div class="spinner"></div>${esc(text)}</div>`;
}

function parseHash() {
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  return { path: path || "/", params: new URLSearchParams(query || "") };
}

function setActiveNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
}

// ---------- router ----------
async function route() {
  const { path, params } = parseHash();
  const m = path.match(/^\/employer\/(\d+)$/);
  const ma = path.match(/^\/application\/([A-Za-z0-9-]+)$/);
  let mr;
  try {
    if (ma) { setActiveNav((state.back.href.match(/^#\/(watchlist|map|industries|ask)/) || [, "search"])[1]); await applicationPage(ma[1]); }
    else if (m) { setActiveNav((state.back.href.match(/^#\/(watchlist|map|industries|ask)/) || [, "search"])[1]); await employerPage(+m[1]); }
    else if (path === "/watchlist") { setActiveNav("watchlist"); await watchlistPage(params); }
    else if (path === "/map") { setActiveNav("map"); await mapPage(params); }
    else if (path === "/industries") { setActiveNav("industries"); await industriesPage(params); }
    else if (path === "/ask") { setActiveNav("ask"); await askPage(); }
    else if (path === "/debarments") { setActiveNav(""); await debarmentsPage(params); }
    else if (path === "/representatives") { setActiveNav(""); await representativesPage(params); }
    else if ((mr = path.match(/^\/representative\/(firm|person)\/(.+)$/))) { setActiveNav(""); await representativePage(mr[1], decodeURIComponent(mr[2])); }
    else { setActiveNav("search"); searchPage(params); }
  } catch (err) {
    app.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(err.message)}</p></div>`;
  }
}
window.addEventListener("hashchange", route);
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-employer]");
  if (a) {
    const { path } = parseHash();
    if (path.startsWith("/representative/")) {
      state.back = { href: location.hash, label: document.querySelector(".emp-head h1")?.textContent || "Law firm or agent" };
    } else if (!path.startsWith("/employer") && !path.startsWith("/application")) {
      const labels = { "/watchlist": "Watchlist", "/map": "Map", "/industries": "Industries", "/ask": "Ask",
                       "/debarments": "Debarments", "/representatives": "Law firms and agents" };
      state.back = { href: location.hash || "#/", label: labels[path] || "Employer lookup" };
    }
  }
});

// ---------- search ----------
let searchTimer;
function searchPage(params) {
  const q = params.get("q") || "";
  app.innerHTML = `
    <section class="hero home">
      <div class="hero-text">
      <div class="eyebrow">H-1B and H-2B employers · Wage and Hour enforcement</div>
      <h1 class="page-title">Look up an employer</h1>
      <p class="lede">Search 80,950 employers that filed H-1B or H-2B applications in FY2025 by name, trade name or tax ID,
        or open one of their 612,374 applications by its DOL case number. See the jobs, worksites, wages and representatives
        on their filings and the Department of Labor Wage and Hour cases linked to them. Every figure traces back to its DOL record.</p>
      <div class="searchbox">${icon.search}
        <input id="q" type="search" placeholder="Company name, trade name, tax ID or DOL case number" autocomplete="off" value="${esc(q)}">
      </div>
      </div>
      <a class="card mini-map" id="mini-map" href="#/map" aria-label="Open the map"></a>
    </section>
    <div id="overview"></div>
    <div class="results" id="results"></div>`;
  const input = document.getElementById("q");
  const overview = document.getElementById("overview");
  overview.hidden = !!q.trim();
  homeOverview();
  const run = async (text) => {
    history.replaceState(null, "", text ? "#/?q=" + encodeURIComponent(text) : "#/");
    const box = document.getElementById("results");
    overview.hidden = !!text.trim();
    if (!text.trim()) { box.innerHTML = ""; return; }
    const hits = await api("/search", { q: text });
    if (input.value !== text) return; // a newer search is on its way
    const caseHit = hits.length && hits[0].case_number;
    box.innerHTML = hits.length ? `<div class="muted small">${caseHit ? `Application ${esc(hits[0].case_number)} was filed by` : `${hits.length === 50 ? "First 50" : hits.length} employers`}</div>` + hits.map((h) => `
      <a class="card result" href="${h.case_number ? "#/application/" + esc(h.case_number) : "#/employer/" + h.employer_id}" data-employer>
        <div>
          <div class="name">${esc(h.employer_name)}${h.dba ? ` <span class="muted">· DBA ${esc(h.dba)}</span>` : ""}</div>
          <div class="where">${esc(titleCase(h.city))}, ${esc(h.state)} ${programBadge(h.program)}
            ${h.case_number ? `<span class="badge">Open application ${esc(h.case_number)}</span>` : ""}
            ${h.willful_violator ? '<span class="badge danger">Self-reported willful violator</span>' : ""}</div>
        </div>
        <div class="stat"><b>${n(h.n_applications)}</b>applications</div>
        <div class="stat ${h.n_cases ? "hot" : ""}"><b>${n(h.n_cases)}</b>WHD cases</div>
      </a>`).join("") : `<div class="card empty">No employers match “${esc(text)}”.</div>`;
  };
  input.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => run(input.value), 180); });
  input.focus();
  if (q) run(q);
}

// Starting points under the search box, and a small map beside the headline.
// Both fill in after the page shows, so typing is never held up.
async function homeOverview() {
  const [o, states] = await Promise.all([api("/overview"), api("/states")]);
  const box = document.getElementById("overview"), mapBox = document.getElementById("mini-map");
  if (!box || !mapBox) return; // the user has moved on
  mapBox.innerHTML = miniMap(states);
  bindTips(mapBox, (st) => {
    const d = states.find((r) => r.state === st);
    return `<b>${esc(STATE_NAMES[st] || st)}</b>` + (d ? tipRows([["Visa employers", n(d.employers)], ["Linked cases", n(d.cases)],
      ["Cases per 100", d.cases_per_100.toFixed(1)], ["Back wages", compactMoney(d.back_wages)]]) : '<div class="tip-hint">No visa employers in the data.</div>');
  });
  const t = o.totals;
  const maxWage = Math.max(...o.top_back_wages.map((r) => r.back_wages), 1);
  const maxShare = Math.max(...o.sectors.map((r) => r.share_with_cases), 0.01);
  box.innerHTML = `
    <div class="tiles home-tiles">
      <div class="card tile"><div class="label">Visa employers</div><div class="value">${n(t.employers)}</div><div class="sub">filed in FY2025</div></div>
      <div class="card tile"><div class="label">Applications</div><div class="value">${n(t.applications)}</div><div class="sub">H-1B and H-2B, FY2025</div></div>
      <div class="card tile"><div class="label">Employers with WHD cases</div><div class="value">${n(t.employers_with_cases)}</div><div class="sub">${pct(t.employers_with_cases / t.employers)} of employers, ${n(t.cases)} cases</div></div>
      <div class="card tile"><div class="label">Back wages found</div><div class="value good-num">${compactMoney(t.back_wages)}</div><div class="sub">plus <span class="bad-text">${compactMoney(t.penalties)}</span> in penalties</div></div>
    </div>
    <div class="eyebrow start-h">Where to start</div>
    <div class="grid-3">
      <div class="card panel start">
        <h3>Most back wages found</h3>
        <div class="sub">Employers whose linked WHD cases found the most back wages owed to workers.</div>
        <div class="start-rows good">${o.top_back_wages.map((r) => `
          <a class="start-row" href="#/employer/${r.employer_id}" data-employer>
            <span class="who"><span class="nm">${esc(r.employer_name)}</span><span class="wh">${esc(titleCase(r.city))}, ${esc(r.state)} · ${n(r.n_cases)} case${r.n_cases === 1 ? "" : "s"}</span></span>
            <span class="v">${compactMoney(r.back_wages)}</span>
            <span class="track"><span class="fill" style="width:${Math.max(2, 100 * r.back_wages / maxWage)}%"></span></span></a>`).join("")}</div>
        <a class="more" href="#/watchlist">Full Watchlist →</a>
      </div>
      <div class="card panel start">
        <h3>Visa violators still filing</h3>
        <div class="sub">${n(o.n_visa_violators)} employers had WHD cases that found violations of their own visa program, and still filed in FY2025. Most applications first.</div>
        <div class="start-rows">${o.visa_violators.map((r) => `
          <a class="start-row" href="#/employer/${r.employer_id}" data-employer>
            <span class="who"><span class="nm">${esc(r.employer_name)}</span><span class="wh">${esc(titleCase(r.city))}, ${esc(r.state)} · ${n(r.visa_violations)} ${esc(r.program)} violation${r.visa_violations === 1 ? "" : "s"}</span></span>
            <span class="v">${n(r.n_applications)}<small>apps</small></span></a>`).join("")}</div>
        <a class="more" href="#/watchlist?visa_only=1&sort=n_applications">All visa violators →</a>
      </div>
      <div class="card panel start">
        <h3>Industries with the most cases</h3>
        <div class="sub">Share of each industry's visa employers with a linked WHD case (industries with 100+ employers).</div>
        <div class="start-rows">${o.sectors.map((r) => `
          <a class="start-row" href="#/watchlist?sector=${encodeURIComponent(r.sector)}">
            <span class="who"><span class="nm">${esc(r.name)}</span><span class="wh">${n(r.employers_with_cases)} of ${n(r.employers)} employers</span></span>
            <span class="v">${pct(r.share_with_cases)}</span>
            <span class="track"><span class="fill" style="width:${Math.max(2, 100 * r.share_with_cases / maxShare)}%"></span></span></a>`).join("")}</div>
        <a class="more" href="#/industries">All industries →</a>
      </div>
    </div>`;
}

function miniMap(states) {
  const byState = Object.fromEntries(states.map((r) => [r.state, r]));
  const rated = states.filter((r) => TILES[r.state] && r.employers >= MIN_EMPLOYERS);
  const breaks = quantileBreaks(rated.map((r) => r.cases_per_100));
  const S = 30, G = 3;
  const tiles = Object.entries(TILES).map(([st, [c, r]]) => {
    const d = byState[st], ok = d && d.employers >= MIN_EMPLOYERS;
    const cls = ok ? classOf(d.cases_per_100, breaks) : 0;
    return `<g class="st" data-tip="${st}"><rect x="${c * (S + G)}" y="${r * (S + G)}" width="${S}" height="${S}" style="fill:var(--seq-${cls})"/>
      <text class="c${cls}" x="${c * (S + G) + S / 2}" y="${r * (S + G) + S / 2 + 4}" text-anchor="middle">${st}</text></g>`;
  }).join("");
  return `<div class="mini-head"><span>Cases per 100 visa employers</span><span class="go">Map →</span></div>
    <svg class="tilemap mini" viewBox="0 0 ${12 * (S + G) - G} ${8 * (S + G) - G}" role="img" aria-label="Linked WHD cases per 100 visa employers by state">${tiles}</svg>
    <div class="mini-legend"><span>fewer</span><span class="ramp">${[1, 2, 3, 4, 5, 6, 7].map((i) => `<i style="background:var(--seq-${i})"></i>`).join("")}</span><span>more</span></div>`;
}

// ---------- employer ----------
// The willful violator answer is the employer's own Yes/No on each application. It counts as a flag
// only when most applications say Yes; a stray Yes is shown as a plain note with the count.
function willfulBadge(e) {
  if (!e.willful_yes) return "";
  const of = `Yes on ${n(e.willful_yes)} of ${n(e.n_applications)} application${e.n_applications === 1 ? "" : "s"}`;
  const tip = 'title="The employer\'s own answer to the willful violator question on its visa applications"';
  return e.willful_violator
    ? `<span class="badge danger" ${tip}><span class="dot"></span>Self-reported willful violator · ${of}</span>`
    : `<span class="badge" ${tip}>Willful violator box: ${of}</span>`;
}

async function employerPage(id) {
  loading("Loading employer…");
  const d = await api("/employers/" + id);
  const e = d.employer, s = d.summary;
  const flags = [
    programBadge(e.program),
    d.debarments.length ? `<a class="badge bad" href="#emp-debar"><span class="dot"></span>On a DOL debarment list</a>` : "",
    willfulBadge(e),
    e.h1b_dependent ? '<span class="badge warn">H-1B dependent</span>' : "",
    s.cases ? `<span class="badge danger">${n(s.cases)} WHD case${s.cases === 1 ? "" : "s"}</span>` : '<span class="badge ok">No linked WHD cases</span>',
  ].join("");
  const related = d.same_tax_id.length ? `
    <div class="related"><span class="muted small" style="align-self:center">Same tax ID elsewhere</span>
      ${d.same_tax_id.map((r) => `<a class="chip" href="#/employer/${r.employer_id}">${programBadge(r.program)}
        ${esc(r.employer_name)} · ${esc(titleCase(r.city))}, ${esc(r.state)} · ${n(r.n_applications)} apps</a>`).join("")}
    </div>` : "";

  app.innerHTML = `
    <a class="back" href="${esc(state.back.href)}">${icon.back} Back to ${esc(state.back.label)}</a>
    <section class="card emp-head">
      <div class="badges">${flags}</div>
      <h1>${esc(e.employer_name)}</h1>
      ${e.dba ? `<div class="muted">Also does business as ${esc(e.dba)}</div>` : ""}
      <div class="facts">
        <span><b>Address</b>${esc(e.address)}${e.address2 ? ", " + esc(e.address2) : ""}, ${esc(titleCase(e.city))}, ${esc(e.state)} ${esc(e.zip5)}</span>
        ${e.phone ? `<span><b>Phone</b><span class="mono">${esc(e.phone)}${e.phone_ext ? " ext. " + esc(e.phone_ext) : ""}</span></span>` : ""}
        <span><b>Tax ID</b><span class="mono">${esc(e.fein || "not given")}</span></span>
        <span><b>NAICS</b><span class="mono">${esc(e.naics || "–")}</span>${e.sector ? ` · <a href="#/watchlist?sector=${esc(e.sector)}">${esc(e.sector_name)}</a>` : ""}</span>
      </div>
      ${related}
    </section>
    ${debarmentPanel(d.debarments)}
    <div class="tiles">
      <div class="card tile"><div class="label">Applications</div><div class="value">${n(s.applications)}</div><div class="sub">FY2025 disclosure files</div></div>
      <div class="card tile"><div class="label">Certified</div><div class="value">${n(s.certified)}</div><div class="sub">${s.applications ? Math.round(100 * s.certified / s.applications) : 0}% of applications</div></div>
      <div class="card tile ${s.cases ? "alert" : ""}"><div class="label">Linked WHD cases</div><div class="value">${n(s.cases)}</div><div class="sub">all years, all laws</div></div>
      <div class="card tile ${s.back_wages ? "good" : ""}"><div class="label">Back wages found</div><div class="value">${compactMoney(s.back_wages)}</div><div class="sub">${money(s.back_wages)}</div></div>
      <div class="card tile ${s.penalties ? "bad" : ""}"><div class="label">Penalties assessed</div><div class="value">${compactMoney(s.penalties)}</div><div class="sub">civil money penalties</div></div>
    </div>

    ${redFlagsPanel(d.red_flags, e.program)}
    ${d.case_years.length ? `<div class="card panel" style="margin-top:16px"><h3>Linked cases by year</h3>
      <div class="sub">Year the WHD findings period ended. Hover a bar for its back wages.</div>${yearColumns(d.case_years)}</div>` : ""}

    ${profilePanels(d.profile, e.program)}
    ${repsPanel(d.representatives)}

    <h2 class="section">Wage and Hour cases <span class="count">${n(d.cases.length)}</span>
      ${d.cases.length ? csvLink(`/employers/${id}/cases.csv`, null, "Download these cases as CSV, with WHD case IDs and how each was linked") : ""}</h2>
    ${d.cases.length ? `
      <div class="callout">${icon.info}<div>WHD records carry no tax ID, so each case is linked by name and location.
        A <b style="color:var(--ok)">green</b> edge means the ZIP code matched too; <b style="color:var(--warn)">amber</b> means only the state did.
        Check the WHD name and address before relying on a link.</div></div>
      ${d.cases.map(caseCard).join("")}` : `<div class="card empty">No WHD enforcement case is linked to this employer.</div>`}

    <h2 class="section">Visa applications <span class="count" id="app-count"></span>
      ${s.applications ? csvLink(`/employers/${id}/applications.csv`, null, "Download every application as CSV, with DOL case numbers") : ""}</h2>
    <div class="card" id="apps"><div class="loading"><div class="spinner"></div></div></div>
    <p class="note">Each case number is DOL's own; click one to see every field of the application. The source column links to the
      DOL disclosure file the row came from. Wages are shown in the unit the employer gave (per year, hour, ...).</p>

    <details class="sources"><summary>Where this page's data comes from</summary>
      <div class="card" style="padding:16px 18px;margin-top:10px">
        <div class="source">Employer row: <span class="mono">${esc(e.source_file)}</span>
          (sha256 <span class="mono">${esc(e.source_sha256.slice(0, 12))}…</span>, built ${esc(e.source_created_utc)})<br>
          from ${e.source_urls.split(";").map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.split("/").pop())}</a>`).join(", ")}</div>
      </div>
    </details>`;
  window.scrollTo(0, 0);
  loadApplications(id, 1);
}

const plural = (k, one, many) => `${n(k)} ${k === 1 ? one : many}`;

// What a WHD case found, law by law, from the API's laws and details lists.
function lawBreakdown(v) {
  const d = v.details;
  const total = Number(v.case_violtn_cnt || 0);
  const stats = (r) => [
    r.violations ? `<span><b>${n(r.violations)}</b> ${r.violations === 1 ? "violation" : "violations"}</span>` : "",
    r.back_wages ? `<span class="good-text"><b>${money(r.back_wages)}</b> back wages</span>` : "",
    r.workers ? `<span><b>${n(r.workers)}</b> ${r.workers === 1 ? "worker" : "workers"} owed</span>` : "",
    r.penalties ? `<span class="bad-text"><b>${money(r.penalties)}</b> penalties</span>` : "",
  ].join("");
  const notes = [
    d.repeat_violator ? `<span class="badge danger">${esc(d.repeat_violator)} violator (FLSA)</span>` : "",
    d.child_labor_minors ? `<span class="badge warn">${plural(d.child_labor_minors, "minor", "minors")} employed in violation</span>` : "",
    d.minimum_wage_back_wages ? `<span class="badge">${money(d.minimum_wage_back_wages)} minimum wage back wages</span>` : "",
    d.overtime_back_wages ? `<span class="badge">${money(d.overtime_back_wages)} overtime back wages</span>` : "",
    d.retaliation_back_wages ? `<span class="badge">${money(d.retaliation_back_wages)} for retaliation</span>` : "",
  ].join("");
  const empty = total ? "WHD did not record which law these violations fall under."
    : "WHD investigated and recorded no violations.";
  return `
      <div class="laws">
        <div class="laws-head"><span class="mini-h">What WHD found</span>
          <span class="meta">${plural(total, "violation", "violations")} in all${v.ee_violtd_cnt ? ` · ${plural(v.ee_violtd_cnt, "worker", "workers")} affected` : ""}</span></div>
        ${v.laws.length ? v.laws.map((r) => `
        <div class="law"><div class="law-name">${esc(r.name)}</div><div class="law-stats">${stats(r)}</div></div>`).join("")
          : `<div class="law empty">${empty}</div>`}
        ${notes ? `<div class="law-notes">${notes}</div>` : ""}
      </div>`;
}

function caseCard(v) {
  const zip = v.method === "clean name + ZIP";
  const bw = Number(v.bw_atp_amt || 0);
  return `
  <details class="card case">
    <summary>
      <span class="strip ${zip ? "zip" : ""}" title="${esc(v.method)}"></span>
      <div>
        <div class="title">${esc(v.trade_nm || v.legal_name || "Unnamed")}</div>
        <div class="meta">Case <span class="mono">${esc(v.case_id)}</span> · findings ${day(v.findings_start_date)} to ${day(v.findings_end_date)}
          · ${esc(titleCase(v.cty_nm))}, ${esc(v.st_cd)}
          ${v.h1b_violtn_cnt ? `<span class="badge h1b">${n(v.h1b_violtn_cnt)} H-1B</span>` : ""}
          ${v.h2b_violtn_cnt ? `<span class="badge h2b">${n(v.h2b_violtn_cnt)} H-2B</span>` : ""}</div>
      </div>
      <div class="money ${bw ? "good-text" : "zero"}">${bw ? money(bw) : "$0"}<div class="meta">back wages</div></div>
      ${icon.chev}
    </summary>
    <div class="case-body">
      <dl class="kv">
        <dt>WHD legal name</dt><dd>${esc(v.legal_name || "–")}</dd>
        <dt>WHD trade name</dt><dd>${esc(v.trade_nm || "–")}</dd>
        <dt>WHD address</dt><dd>${esc(v.street_addr_1_txt)}, ${esc(v.cty_nm)}, ${esc(v.st_cd)} ${esc(v.zip_cd)}</dd>
        <dt>Penalties assessed</dt><dd class="${v.cmp_assd ? "bad-text" : ""}">${money(v.cmp_assd)}</dd>
        ${v.details.industry ? `<dt>WHD industry</dt><dd>${esc(v.details.industry)}</dd>` : ""}
      </dl>
      ${lawBreakdown(v)}
      <div class="match"><span class="badge ${zip ? "ok" : "warn"}">${zip ? "Name + ZIP" : "Name + state"}</span>
        <div>${esc(v.match_label)}. Compared the WHD ${esc(v.whd_name_field)} with the employer ${esc(v.employer_name_field)}.</div></div>
      <div class="source">Source: WHD enforcement API <a href="${esc(v.source_urls)}" target="_blank" rel="noopener">${esc(v.source_urls)}</a>,
        case_id <span class="mono">${esc(v.case_id)}</span>, loaded by DOL ${esc(v.load_dt)} ·
        file <span class="mono">${esc(v.source_file)}</span> (sha256 <span class="mono">${esc(v.source_sha256.slice(0, 12))}…</span>)</div>
    </div>
  </details>`;
}

async function loadApplications(id, page) {
  const box = document.getElementById("apps");
  const d = await api(`/employers/${id}/applications`, { page });
  document.getElementById("app-count").textContent = n(d.total);
  const pages = Math.max(1, Math.ceil(d.total / d.page_size));
  const statusBadge = (s) => {
    const cls = /^certified$/i.test(s) ? "ok" : /withdrawn/i.test(s) ? "" : /denied/i.test(s) ? "danger" : "warn";
    return `<span class="badge ${cls}">${esc(s)}</span>`;
  };
  box.innerHTML = d.total ? `
    <div class="table-wrap"><table>
      <thead><tr><th>Case number</th><th>Status</th><th>Decided</th><th>Job</th><th>Worksite</th><th class="num">Workers</th>
        <th class="num">Wage offered</th><th class="num">Prevailing wage</th><th>Represented by</th><th>DOL file</th></tr></thead>
      <tbody>${d.rows.map((a) => `<tr>
        <td class="mono nowrap"><a href="#/application/${esc(a.case_number)}">${esc(a.case_number)}</a></td><td>${statusBadge(a.case_status)}</td>
        <td class="nowrap">${day(a.decision_date)}</td>
        <td class="job-cell"><div>${esc(a.job_title || "–")}</div><div class="muted small">${esc(a.soc_title || "")}</div></td>
        <td>${esc(titleCase(a.worksite_city || ""))}${a.worksite_state ? ", " + esc(a.worksite_state) : ""}</td>
        <td class="num">${n(a.workers)}</td>
        <td class="num nowrap">${wage(a.wage_from, a.wage_unit, a.wage_to)}</td>
        <td class="num nowrap">${a.prevailing_wage != null ? wage(a.prevailing_wage, a.pw_unit_of_pay) + (a.pw_wage_level ? ` <span class="muted small">L${esc(a.pw_wage_level)}</span>` : "") : "–"}</td>
        <td class="small">${esc(a.lawfirm || "")}</td>
        <td class="small">${a.dol_file_url ? `<a href="${esc(a.dol_file_url)}" target="_blank" rel="noopener">${esc(a.source_file)}</a>` : esc(a.source_file)}</td>
      </tr>`).join("")}</tbody></table></div>
    ${pages > 1 ? `<div class="pager"><span>Page ${page} of ${n(pages)}</span><span class="btns">
      <button class="btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
      <button class="btn" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></span></div>` : ""}`
    : `<div class="empty">No applications.</div>`;
  box.querySelectorAll("[data-page]").forEach((b) => b.addEventListener("click", () => loadApplications(id, +b.dataset.page)));
}

const UNIT_SHORT = { Year: "yr", Hour: "hr", Month: "mo", Week: "wk", "Bi-Weekly": "2 wks", "Piece Rate": "piece" };
function wage(from, unit, to) {
  if (from == null || from === "") return "–";
  const f = (x) => "$" + Number(x).toLocaleString("en-US", { maximumFractionDigits: Number(x) < 1000 ? 2 : 0 });
  const range = to != null && to !== "" && Number(to) > Number(from) ? `${f(from)}–${f(to)}` : f(from);
  return `${range}<span class="muted small">/${esc(UNIT_SHORT[unit] || unit || "?")}</span>`;
}

// Ranked bars for the employer profile panels (plain rows, no links).
function rankBars(items, label = (r) => r.name, value = (r) => r.applications, sub = null) {
  const max = Math.max(...items.map(value), 1);
  return `<div class="hbars">${items.map((r) => `
    <div class="hbar static" title="${esc(label(r))}">
      <span class="name">${esc(label(r))}${sub && sub(r) ? `<span class="hbar-sub">${esc(sub(r))}</span>` : ""}</span>
      <span class="track"><span class="fill" style="width:${Math.max(1.5, 100 * value(r) / max)}%"></span></span>
      <span class="v">${n(value(r))}</span></div>`).join("")}</div>`;
}

function profilePanels(p, program) {
  if (!p || !(p.jobs.length || p.worksites.length || p.lawfirms.length)) return "";
  const wageTotal = p.wage_bands.reduce((a, b) => a + b.applications, 0);
  const maxBand = Math.max(...p.wage_bands.map((b) => b.applications), 1);
  const wagePanel = wageTotal ? `
    <div class="card panel">
      <h3>Offered wage against the prevailing wage</h3>
      <div class="sub">H-1B applications by how far the lowest offered wage is above the prevailing wage for the job and area
        (both turned into yearly amounts). Offers at or just above the prevailing wage are worth a closer look.</div>
      <div class="bands">${p.wage_bands.map((b, i) => `
        <div class="band ${i === 0 && b.applications ? "below" : ""}" data-tip="${i}">
          <div class="band-bar"><span style="height:${100 * b.applications / maxBand}%"></span></div>
          <div class="band-v">${n(b.applications)}</div><div class="band-l">${esc(b.band)}</div></div>`).join("")}</div>
      ${p.wage_levels.length ? `<div class="levels"><span class="muted small">Prevailing wage level</span>
        ${p.wage_levels.map((l) => `<span class="chip">Level ${esc(l.level)} <b>${n(l.applications)}</b></span>`).join("")}</div>` : ""}
    </div>` : "";
  const repPanel = p.lawfirms.length || p.attorneys.length ? `
    <div class="card panel">
      <h3>Who files their applications</h3>
      <div class="sub">Law firms or agents, and the attorney or agent named on the form, by number of applications.</div>
      ${p.lawfirms.length ? `<div class="mini-h">Law firm or agent business</div>${rankBars(p.lawfirms.slice(0, 6))}` : ""}
      ${p.attorneys.length ? `<div class="mini-h">Attorney or agent</div>${rankBars(p.attorneys.slice(0, 6))}` : ""}
    </div>` : "";
  return `
    <h2 class="section">What their applications ask for</h2>
    <div class="grid-2 even">
      ${p.jobs.length ? `<div class="card panel">
        <h3>Jobs</h3>
        <div class="sub">Occupations (SOC) by number of applications, with the job titles the employer used.</div>
        ${rankBars(p.jobs, (r) => r.soc_title, (r) => r.applications,
          (r) => r.job_titles.join(" · ") + (r.more_titles ? ` · +${r.more_titles} more` : ""))}
      </div>` : ""}
      ${p.worksites.length ? `<div class="card panel">
        <h3>Worksites</h3>
        <div class="sub">Where the workers would work, by number of applications${program === "H-2B" ? " (main worksite on the form)" : ""}.</div>
        ${rankBars(p.worksites, (r) => titleCase(r.name.split(", ")[0]) + ", " + (r.name.split(", ")[1] || ""))}
        ${p.worksite_states.length > 1 ? `<div class="levels"><span class="muted small">${n(p.worksite_states.length)} states</span>
          ${p.worksite_states.slice(0, 12).map((r) => `<span class="chip">${esc(r.name)} <b>${n(r.applications)}</b></span>`).join("")}</div>` : ""}
      </div>` : ""}
      ${wagePanel}
      ${repPanel}
    </div>`;
}
// Wage-band tooltips are bound after the employer page renders.
new MutationObserver(() => {
  document.querySelectorAll(".bands:not([data-bound])").forEach((el) => {
    el.dataset.bound = "1";
    const labels = [...el.querySelectorAll(".band-l")].map((x) => x.textContent);
    const vals = [...el.querySelectorAll(".band-v")].map((x) => x.textContent);
    bindTips(el, (i) => `<b>${esc(labels[i])}</b>` + tipRows([["H-1B applications", vals[i]]]));
  });
}).observe(app, { childList: true, subtree: true });

// ---------- application ----------
async function applicationPage(caseNumber) {
  loading("Loading application…");
  const d = await api("/applications/" + encodeURIComponent(caseNumber));
  const a = d.application, h = d.highlights;
  const status = a.case_status || "";
  const cls = /^certified$|certification$/i.test(status) ? "ok" : /withdrawn/i.test(status) ? "" : /denied|rejected/i.test(status) ? "danger" : "warn";
  const vs = h.wage_vs_prevailing;
  const fmt = (f) => {
    const v = f.value;
    if (/email/.test(f.field) && /@/.test(v)) return `<a href="mailto:${esc(v)}">${esc(v)}</a>`;
    if (/website/.test(f.field) && /^https?:/i.test(v)) return `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>`;
    if (typeof v === "number") return /wage|rate|prevailing/.test(f.field) ? "$" + v.toLocaleString("en-US") : n(v);
    return esc(v);
  };
  app.innerHTML = `
    <a class="back" href="#/employer/${a.employer_id}" data-employer>${icon.back} ${esc(a.employer_row_name)}</a>
    <section class="card emp-head">
      <div class="badges">${programBadge(a.program)}<span class="badge ${cls}">${esc(status)}</span></div>
      <h1>${esc(h.job_title || "Application")}</h1>
      <div class="muted">${esc(h.soc_title || "")}</div>
      <div class="facts">
        <span><b>Case number</b><span class="mono">${esc(a.case_number)}</span></span>
        <span><b>Employer</b><a href="#/employer/${a.employer_id}" data-employer>${esc(a.employer_row_name)}</a> · ${esc(titleCase(a.employer_row_city))}, ${esc(a.employer_row_state)}</span>
        ${h.lawfirm ? `<span><b>Represented by</b>${esc(h.lawfirm)}</span>` : ""}
      </div>
    </section>
    <div class="tiles">
      <div class="card tile"><div class="label">Workers</div><div class="value">${n(h.workers)}</div><div class="sub">${h.workers_certified != null ? n(h.workers_certified) + " certified" : "positions on the form"}</div></div>
      <div class="card tile"><div class="label">Wage offered</div><div class="value">${wage(h.wage_from, h.wage_unit, h.wage_to)}</div><div class="sub">lowest to highest offered</div></div>
      ${h.prevailing_wage != null ? `<div class="card tile ${vs != null && vs < 0.02 ? "alert" : ""}"><div class="label">Prevailing wage</div><div class="value">${wage(h.prevailing_wage, h.pw_unit)}</div>
        <div class="sub">${h.pw_wage_level ? "Level " + esc(h.pw_wage_level) + " · " : ""}${vs != null ? (vs >= 0 ? "offer is " + pct(vs) + " above" : "offer is " + pct(-vs) + " below") : ""}</div></div>` : ""}
      <div class="card tile"><div class="label">Employment period</div><div class="value small-value">${day(h.begin_date)}<br>to ${day(h.end_date)}</div><div class="sub">${esc(h.worksite || "")}</div></div>
    </div>
    <div class="form-sections">
      ${d.sections.map((sec) => `<section class="card panel">
        <h3>${esc(sec.title)}</h3>
        <dl class="kv">${sec.fields.map((f) => `<dt>${esc(f.label)}</dt><dd>${fmt(f)}</dd>`).join("")}</dl>
      </section>`).join("")}
    </div>
    <p class="note">Every field DOL published for this application, as it appears in the disclosure file. Blank fields are left out.</p>
    <div class="source">Source: <span class="mono">${esc(a.source_file)}</span>${a.dol_file_url ? ` from <a href="${esc(a.dol_file_url)}" target="_blank" rel="noopener">${esc(a.dol_file_url.split("/").pop())}</a>` : ""},
      table <span class="mono">${esc(a.table_file)}</span> (sha256 <span class="mono">${esc(a.table_sha256.slice(0, 12))}…</span>)</div>`;
  window.scrollTo(0, 0);
}

// ---------- watchlist ----------
const WL_COLUMNS = [
  { key: "employer_name", label: "Employer", type: "text" },
  { key: "back_wages", label: "Back wages", type: "num" },
  { key: "n_cases", label: "Cases", type: "num" },
  { key: "visa_violations", label: "Visa violations", type: "num" },
  { key: "violations", label: "All violations", type: "num" },
  { key: "workers", label: "Workers owed", type: "num" },
  { key: "penalties", label: "Penalties", type: "num" },
  { key: "zip_share", label: "ZIP-matched", type: "num" },
  { key: "n_applications", label: "Visa apps", type: "num" },
  { key: "latest_case", label: "Latest case", type: "text" },
];
const WL_PAGE = 50;
const wl = { key: null, rows: [], sort: "back_wages", dir: -1, page: 1 };

async function watchlistPage(params) {
  if (!state.meta) state.meta = await api("/meta");
  const f = {
    program: params.get("program") || "", state: params.get("state") || "", since: params.get("since") || "",
    visa_only: params.get("visa_only") === "1", min_apps: params.get("min_apps") || "1",
    sector: params.get("sector") || "",
  };
  wl.sort = params.get("sort") || "back_wages";
  wl.dir = params.get("dir") === "asc" ? 1 : -1;
  const key = JSON.stringify(f);
  const seg = (val, label) => `<button data-program="${val}" class="${f.program === val ? "on" : ""}">${label}</button>`;
  app.innerHTML = `
    <div class="eyebrow">Ranked by enforcement history</div>
    <h1 class="page-title">Watchlist</h1>
    <p class="lede">Every H-1B and H-2B employer with Wage and Hour cases linked to it. Sort by any column,
      or open an employer to check each case and its source.</p>
    <div class="card filters">
      <div class="field"><label>Program</label><div class="segmented" id="f-program">${seg("", "All")}${seg("H-1B", "H-1B")}${seg("H-2B", "H-2B")}</div></div>
      <div class="field"><label for="f-state">State</label><select id="f-state"><option value="">All states</option>
        ${state.meta.states.map((s) => `<option ${s === f.state ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></div>
      <div class="field"><label for="f-sector">Industry</label><select id="f-sector" style="max-width:220px"><option value="">All industries</option>
        ${Object.entries(state.meta.sectors).map(([k, v]) => `<option value="${k}" ${k === f.sector ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <div class="field"><label for="f-since">Cases ending</label><select id="f-since">
        ${[["", "Any year"], ["2020", "2020 or later"], ["2015", "2015 or later"], ["2010", "2010 or later"], ["2005", "2005 or later"]]
          .map(([v, l]) => `<option value="${v}" ${v === f.since ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><label for="f-min">Min. applications</label><input id="f-min" type="number" min="1" step="1" value="${esc(f.min_apps)}" style="width:110px"></div>
      <label class="toggle"><input id="f-visa" type="checkbox" ${f.visa_only ? "checked" : ""}> Only violations of the employer's visa program</label>
      <span class="spacer"></span>
      ${csvLink("/watchlist.csv", { ...f, visa_only: f.visa_only ? "true" : "" }, "Download these employers as CSV, with the WHD case IDs behind each row")}
    </div>
    <div class="tiles" id="wl-tiles"></div>
    <div class="card" style="margin-top:16px" id="wl-table"><div class="loading"><div class="spinner"></div>Loading watchlist…</div></div>
    <p class="note">WHD records have no tax ID, so every case is linked by name and location; “ZIP-matched” is the share linked by
      name + ZIP rather than the looser name + state. A company filing from several addresses, or in both programs, has a row for each,
      so a case can appear on more than one row. The totals above count each case once.</p>`;

  const go = (changes) => {
    const p = new URLSearchParams({ ...Object.fromEntries(params), ...changes });
    [...p.entries()].forEach(([k, v]) => { if (v === "" || (k === "min_apps" && v === "1") || (k === "visa_only" && v === "0")) p.delete(k); });
    location.hash = "#/watchlist" + (p.toString() ? "?" + p : "");
  };
  document.getElementById("f-program").addEventListener("click", (e) => { if (e.target.dataset.program != null) go({ program: e.target.dataset.program }); });
  document.getElementById("f-state").addEventListener("change", (e) => go({ state: e.target.value }));
  document.getElementById("f-since").addEventListener("change", (e) => go({ since: e.target.value }));
  document.getElementById("f-sector").addEventListener("change", (e) => go({ sector: e.target.value }));
  document.getElementById("f-min").addEventListener("change", (e) => go({ min_apps: String(Math.max(1, parseInt(e.target.value, 10) || 1)) }));
  document.getElementById("f-visa").addEventListener("change", (e) => go({ visa_only: e.target.checked ? "1" : "0" }));

  if (wl.key !== key) {
    const d = await api("/watchlist", { ...f, visa_only: f.visa_only ? "true" : "" });
    wl.rows = d.rows.map((r) => ({
      ...r,
      visa_violations: r.program === "H-1B" ? r.h1b_violations : r.h2b_violations,
      zip_share: r.n_cases ? r.zip_links / r.n_cases : 0,
      latest_case: r.latest_case ? r.latest_case.slice(0, 10) : "",
    }));
    wl.totals = d.totals;
    wl.key = key;
  }
  wl.page = Math.max(1, parseInt(params.get("page"), 10) || 1);
  const t = wl.totals;
  document.getElementById("wl-tiles").innerHTML = `
    <div class="card tile"><div class="label">Employers</div><div class="value">${n(wl.rows.length)}</div></div>
    <div class="card tile"><div class="label">Linked WHD cases</div><div class="value">${n(t.cases)}</div><div class="sub">each case counted once</div></div>
    <div class="card tile good"><div class="label">Back wages found</div><div class="value">${compactMoney(t.back_wages)}</div><div class="sub">${money(t.back_wages)}</div></div>
    <div class="card tile bad"><div class="label">Penalties assessed</div><div class="value">${compactMoney(t.penalties)}</div><div class="sub">${money(t.penalties)}</div></div>`;
  renderWatchlistTable(go);
}

function renderWatchlistTable(go) {
  const box = document.getElementById("wl-table");
  if (!wl.rows.length) { box.innerHTML = `<div class="empty">No employers match these filters.</div>`; return; }
  const col = WL_COLUMNS.find((c) => c.key === wl.sort) || WL_COLUMNS[1];
  const rows = [...wl.rows].sort((a, b) => {
    const x = a[col.key], y = b[col.key];
    return (col.type === "num" ? (x - y) : String(x).localeCompare(String(y))) * wl.dir || b.back_wages - a.back_wages;
  });
  const pages = Math.ceil(rows.length / WL_PAGE);
  const page = Math.min(wl.page, pages);
  const shown = rows.slice((page - 1) * WL_PAGE, page * WL_PAGE);
  const maxBw = Math.max(...wl.rows.map((r) => r.back_wages), 1);
  const th = (c) => `<th data-sort="${c.key}" class="${c.type === "num" ? "num" : ""} ${c.key === col.key ? "sorted" : ""}">${c.label}<span class="arrow">${c.key === col.key ? (wl.dir < 0 ? "↓" : "↑") : "↕"}</span></th>`;
  box.innerHTML = `
    <div class="table-wrap" style="max-height:70vh"><table class="sortable clickable">
      <thead><tr>${WL_COLUMNS.map(th).join("")}</tr></thead>
      <tbody>${shown.map((r) => `<tr data-id="${r.employer_id}">
        <td><a class="emp" href="#/employer/${r.employer_id}" data-employer title="${esc(r.employer_name)}">${esc(r.employer_name)}</a>
          <div class="emp-sub">${programBadge(r.program)} ${esc(titleCase(r.city))}, ${esc(r.state)}${r.willful_violator ? ' · <span style="color:var(--danger)">self-reported willful violator</span>' : ""}</div></td>
        <td class="num"><span class="bar"><i style="width:${Math.max(2, 100 * r.back_wages / maxBw)}%"></i><span>${money(r.back_wages)}</span></span></td>
        <td class="num">${n(r.n_cases)}</td>
        <td class="num">${r.visa_violations ? `<b>${n(r.visa_violations)}</b>` : '<span class="muted">0</span>'}</td>
        <td class="num">${n(r.violations)}</td>
        <td class="num">${n(r.workers)}</td>
        <td class="num ${r.penalties ? "bad-text" : ""}">${money(r.penalties)}</td>
        <td class="num"><span class="badge ${r.zip_share === 1 ? "ok" : r.zip_share === 0 ? "warn" : ""}">${r.zip_links} of ${r.n_cases}</span></td>
        <td class="num">${n(r.n_applications)}</td>
        <td class="mono small">${esc(r.latest_case || "–")}</td>
      </tr>`).join("")}</tbody></table></div>
    <div class="pager"><span>${n((page - 1) * WL_PAGE + 1)}–${n(Math.min(page * WL_PAGE, rows.length))} of ${n(rows.length)} employers</span>
      <span class="btns"><button class="btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
      <button class="btn" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></span></div>`;
  box.querySelectorAll("th[data-sort]").forEach((h) => h.addEventListener("click", () => {
    const k = h.dataset.sort, c = WL_COLUMNS.find((x) => x.key === k);
    const dir = k === wl.sort ? (wl.dir < 0 ? "asc" : "desc") : (c.type === "num" ? "desc" : "asc");
    go({ sort: k, dir, page: "" });
  }));
  box.querySelectorAll("[data-page]").forEach((b) => b.addEventListener("click", () => go({ page: b.dataset.page })));
  box.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    state.back = { href: location.hash, label: "Watchlist" };
    location.hash = "#/employer/" + tr.dataset.id;
  }));
}

// ---------- shared chart helpers ----------
const tip = document.getElementById("tooltip");
function showTip(html, ev) {
  tip.innerHTML = html;
  tip.classList.add("on");
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
function hideTip() { tip.classList.remove("on"); }
window.addEventListener("hashchange", hideTip);
const tipRows = (pairs) => pairs.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span>${v}</span></div>`).join("");
function bindTips(root, html) {
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("mousemove", (ev) => showTip(html(el.dataset.tip), ev));
    el.addEventListener("mouseleave", hideTip);
  });
}
const pct = (x, digits = 1) => (100 * x).toFixed(digits) + "%";
const niceMax = (v) => {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
};

function yearColumns(years) {
  const first = years[0].year, last = Math.max(years[years.length - 1].year, first);
  const all = [];
  for (let y = first; y <= last; y++) all.push(years.find((r) => r.year === y) || { year: y, cases: 0, back_wages: 0 });
  const W = 1100, H = 190, L = 34, B = 22, T = 8, max = niceMax(Math.max(...all.map((r) => r.cases)));
  const bw = (W - L) / all.length, gap = Math.min(6, bw * 0.25);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(all.length / 14);
  return `<svg class="columns" viewBox="0 0 ${W} ${H}" role="img" aria-label="Linked WHD cases per year">
    ${ticks.map((t) => `<line class="gridline" x1="${L}" x2="${W}" y1="${y(t)}" y2="${y(t)}"/><text class="axis" x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${n(t)}</text>`).join("")}
    ${all.map((r, i) => r.cases ? `<path class="bar" data-tip="${i}" d="${roundTop(L + i * bw + gap / 2, y(r.cases), bw - gap, y(0) - y(r.cases), 4)}"/>` : "").join("")}
    ${all.map((r, i) => i % labelEvery === 0 ? `<text class="axis" x="${L + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${r.year}</text>` : "").join("")}
  </svg>`.replace("<svg", `<svg data-years='${esc(JSON.stringify(all))}'`);
}
function roundTop(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
// Column-chart tooltips are bound after the employer page renders.
new MutationObserver(() => {
  document.querySelectorAll("svg.columns[data-years]:not([data-bound])").forEach((svg) => {
    svg.dataset.bound = "1";
    const all = JSON.parse(svg.dataset.years);
    bindTips(svg, (i) => `<b>${all[i].year}</b>` + tipRows([["Cases", n(all[i].cases)], ["Back wages", money(all[i].back_wages)]]));
  });
}).observe(app, { childList: true, subtree: true });

// ---------- map ----------
const TILES = {
  AK: [0, 0], ME: [11, 0], WI: [6, 1], VT: [10, 1], NH: [11, 1],
  WA: [1, 2], ID: [2, 2], MT: [3, 2], ND: [4, 2], MN: [5, 2], IL: [6, 2], MI: [7, 2], NY: [9, 2], MA: [10, 2],
  OR: [1, 3], NV: [2, 3], WY: [3, 3], SD: [4, 3], IA: [5, 3], IN: [6, 3], OH: [7, 3], PA: [8, 3], NJ: [9, 3], CT: [10, 3], RI: [11, 3],
  CA: [1, 4], UT: [2, 4], CO: [3, 4], NE: [4, 4], MO: [5, 4], KY: [6, 4], WV: [7, 4], VA: [8, 4], MD: [9, 4], DE: [10, 4],
  AZ: [2, 5], NM: [3, 5], KS: [4, 5], AR: [5, 5], TN: [6, 5], NC: [7, 5], SC: [8, 5], DC: [9, 5],
  OK: [4, 6], LA: [5, 6], MS: [6, 6], AL: [7, 6], GA: [8, 6],
  HI: [0, 7], TX: [4, 7], FL: [9, 7], PR: [11, 7],
};
const MIN_EMPLOYERS = 30;
const METRICS = {
  cases_per_100: { label: "Cases per 100 visa employers", fmt: (v) => v.toFixed(1), rate: true,
    note: "Linked WHD cases for every 100 visa employers in the state. Fairer than raw counts, because big states have more of everything." },
  share_with_cases: { label: "Share of employers with a case", fmt: (v) => pct(v), rate: true,
    note: "The share of the state's visa employers that have at least one linked WHD case." },
  back_wages: { label: "Back wages", fmt: (v) => compactMoney(v), rate: false,
    note: "Back wages found in the linked WHD cases, each case counted once." },
  cases: { label: "Linked cases", fmt: (v) => n(v), rate: false,
    note: "Number of WHD cases linked to the state's visa employers, each counted once." },
};
const STATE_NAMES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", PR: "Puerto Rico", GU: "Guam", VI: "U.S. Virgin Islands", MP: "Northern Mariana Islands", AS: "American Samoa" };

function groupFilters(params, base) {
  const program = params.get("program") || "", visa = params.get("visa_only") === "1";
  const go = (changes) => {
    const p = new URLSearchParams({ ...Object.fromEntries(params), ...changes });
    [...p.entries()].forEach(([k, v]) => { if (v === "" || (k === "visa_only" && v === "0")) p.delete(k); });
    location.hash = base + (p.toString() ? "?" + p : "");
  };
  const seg = (val, label) => `<button data-program="${val}" class="${program === val ? "on" : ""}">${label}</button>`;
  const html = `
      <div class="field"><label>Program</label><div class="segmented" id="g-program">${seg("", "All")}${seg("H-1B", "H-1B")}${seg("H-2B", "H-2B")}</div></div>
      <label class="toggle"><input id="g-visa" type="checkbox" ${visa ? "checked" : ""}> Only violations of the employer's visa program</label>`;
  const bind = () => {
    document.getElementById("g-program").addEventListener("click", (e) => { if (e.target.dataset.program != null) go({ program: e.target.dataset.program }); });
    document.getElementById("g-visa").addEventListener("change", (e) => go({ visa_only: e.target.checked ? "1" : "0" }));
  };
  const toWatchlist = (extra) => {
    const p = new URLSearchParams({ ...(program ? { program } : {}), ...(visa ? { visa_only: "1" } : {}), ...extra });
    return "#/watchlist?" + p;
  };
  return { program, visa, go, html, bind, toWatchlist };
}

// Quantile class breaks over the states that have enough employers to be meaningful.
function quantileBreaks(values, k = 7) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return [];
  return Array.from({ length: k - 1 }, (_, i) => v[Math.min(v.length - 1, Math.floor(((i + 1) * v.length) / k))]);
}
const classOf = (v, breaks) => 1 + breaks.filter((b) => v >= b).length;

async function mapPage(params) {
  loading("Loading map…");
  const g = groupFilters(params, "#/map");
  const metric = METRICS[params.get("metric")] ? params.get("metric") : "cases_per_100";
  const M = METRICS[metric];
  const data = await api("/states", { program: g.program, visa_only: g.visa ? "true" : "" });
  const byState = Object.fromEntries(data.map((r) => [r.state, r]));
  const enough = (r) => r && (!M.rate || r.employers >= MIN_EMPLOYERS);
  const rated = data.filter((r) => TILES[r.state] && enough(r));
  const breaks = quantileBreaks(rated.map((r) => r[metric]));
  const total = data.reduce((a, r) => ({ e: a.e + r.employers, c: a.c + r.cases, w: a.w + r.back_wages, x: a.x + r.employers_with_cases }), { e: 0, c: 0, w: 0, x: 0 });
  const S = 64, G = 4;
  const tiles = Object.entries(TILES).map(([st, [c, r]]) => {
    const d = byState[st];
    const ok = enough(d);
    const cls = ok ? classOf(d[metric], breaks) : 0;
    return `<a class="st ${ok ? "" : "na"}" href="${g.toWatchlist({ state: st })}" data-tip="${st}" aria-label="${esc(STATE_NAMES[st] || st)}">
      <rect x="${c * (S + G)}" y="${r * (S + G)}" width="${S}" height="${S}" style="fill:var(--seq-${cls})"/>
      <text class="c${cls}" x="${c * (S + G) + S / 2}" y="${r * (S + G) + S / 2 - 2}" text-anchor="middle">${st}</text>
      <text class="val c${cls}" x="${c * (S + G) + S / 2}" y="${r * (S + G) + S / 2 + 13}" text-anchor="middle">${ok ? M.fmt(d[metric]) : d ? "few" : "–"}</text>
    </a>`;
  }).join("");
  const W = 12 * (S + G), H = 8 * (S + G);
  const ranked = [...rated].sort((a, b) => b[metric] - a[metric]);
  const top = ranked.slice(0, 12), maxV = top.length ? top[0][metric] : 1;
  const others = data.filter((r) => !TILES[r.state]).map((r) => r.state);
  const lo = (i) => (i === 0 ? (rated.length ? Math.min(...rated.map((r) => r[metric])) : 0) : breaks[i - 1]);

  app.innerHTML = `
    <div class="eyebrow">Where enforcement history concentrates</div>
    <h1 class="page-title">Map</h1>
    <p class="lede">Each square is a state, colored by how much Wage and Hour enforcement is linked to its H-1B and H-2B employers.
      Hover for details, or click a state to open its employers in the Watchlist.</p>
    <div class="card filters">
      <div class="field"><label for="m-metric">Color states by</label><select id="m-metric">
        ${Object.entries(METRICS).map(([k, v]) => `<option value="${k}" ${k === metric ? "selected" : ""}>${v.label}</option>`).join("")}</select></div>
      ${g.html}
    </div>
    <div class="tiles">
      <div class="card tile"><div class="label">Visa employers</div><div class="value">${n(total.e)}</div><div class="sub">${n(total.x)} with a linked case</div></div>
      <div class="card tile"><div class="label">Cases per 100 employers</div><div class="value">${total.e ? (100 * total.c / total.e).toFixed(1) : "0"}</div><div class="sub">national rate</div></div>
      <div class="card tile good"><div class="label">Back wages found</div><div class="value">${compactMoney(total.w)}</div><div class="sub">${money(total.w)}</div></div>
      <div class="card tile"><div class="label">Highest state</div><div class="value">${ranked[0] ? esc(ranked[0].state) : "–"}</div><div class="sub">${ranked[0] ? esc(M.label.toLowerCase()) + ": " + M.fmt(ranked[0][metric]) : ""}</div></div>
    </div>
    <div class="grid-2">
      <div class="card panel">
        <h3>${esc(M.label)} by state</h3>
        <div class="sub">${esc(M.note)}</div>
        <svg class="tilemap" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(M.label)} by state">${tiles}</svg>
        <div class="legend">
          <div><div class="ramp">${[1, 2, 3, 4, 5, 6, 7].map((i) => `<span style="background:var(--seq-${i})" title="${M.fmt(lo(i - 1))} and up"></span>`).join("")}</div>
            <div class="ramp-labels"><span>${rated.length ? M.fmt(lo(0)) : ""}</span><span>${rated.length ? M.fmt(Math.max(...rated.map((r) => r[metric]))) : ""}</span></div></div>
          <span><span class="swatch" style="background:var(--seq-0)"></span>${M.rate ? `Fewer than ${MIN_EMPLOYERS} visa employers` : "No visa employers"}</span>
          <span>Seven equal-count groups: each shade holds about the same number of states.</span>
        </div>
        ${others.length ? `<div class="note">Also in the data: ${others.map((s) => `<a href="${g.toWatchlist({ state: s })}">${esc(STATE_NAMES[s] || s)}</a>`).join(", ")}.</div>` : ""}
      </div>
      <div class="card panel">
        <h3>Top states</h3>
        <div class="sub">${esc(M.label)}${M.rate ? `, states with ${MIN_EMPLOYERS}+ visa employers` : ""}</div>
        <div class="rank">${top.map((r) => `
          <a class="rank-row" href="${g.toWatchlist({ state: r.state })}" data-tip="${r.state}">
            <b>${esc(r.state)}</b><div class="track"><div class="fill" style="width:${Math.max(1.5, 100 * r[metric] / maxV)}%"></div></div>
            <span class="v">${M.fmt(r[metric])}</span></a>`).join("")}</div>
      </div>
    </div>
    <details class="sources"><summary>Show all states as a table</summary>
      <div class="card" style="margin-top:10px"><div class="table-wrap"><table>
        <thead><tr><th>State</th><th class="num">Visa employers</th><th class="num">With a case</th><th class="num">Share</th><th class="num">Cases</th><th class="num">Per 100</th><th class="num">Back wages</th><th class="num">Penalties</th></tr></thead>
        <tbody>${[...data].sort((a, b) => b.cases - a.cases).map((r) => `<tr>
          <td><a href="${g.toWatchlist({ state: r.state })}">${esc(STATE_NAMES[r.state] || r.state)}</a></td><td class="num">${n(r.employers)}</td>
          <td class="num">${n(r.employers_with_cases)}</td><td class="num">${pct(r.share_with_cases)}</td><td class="num">${n(r.cases)}</td>
          <td class="num">${r.cases_per_100.toFixed(1)}</td><td class="num good-text">${money(r.back_wages)}</td><td class="num bad-text">${money(r.penalties)}</td></tr>`).join("")}</tbody>
      </table></div></div>
    </details>
    <p class="note">A case counts toward a state when it is linked to a visa employer there. WHD cases have no tax ID, so links are by name and location; see each employer's page for how each case was linked.</p>`;
  g.bind();
  document.getElementById("m-metric").addEventListener("change", (e) => g.go({ metric: e.target.value }));
  bindTips(app, (st) => {
    const d = byState[st];
    if (!d) return `<b>${esc(STATE_NAMES[st] || st)}</b><div class="tip-hint">No visa employers in the data.</div>`;
    return `<b>${esc(STATE_NAMES[st] || st)}</b>` + tipRows([
      ["Visa employers", n(d.employers)], ["With a linked case", `${n(d.employers_with_cases)} (${pct(d.share_with_cases)})`],
      ["Linked cases", n(d.cases)], ["Cases per 100", d.cases_per_100.toFixed(1)], ["Back wages", money(d.back_wages)],
      ["Visa-program violations", n(d.visa_violations)],
    ]) + (enough(d) ? "" : `<div class="tip-hint">Fewer than ${MIN_EMPLOYERS} visa employers, so not shaded.</div>`) + `<div class="tip-hint">Click to see these employers</div>`;
  });
}

// Direct labels beside dots: right of the dot unless that runs off the chart, and skipped if they would overlap one already placed.
// Labels for scatter dots: each tries right, left, above, below and skips any
// spot that would cover another label or any dot in `obstacles`.
function placeLabels(items, right, obstacles = []) {
  const placed = obstacles.map((o) => [o.x - o.r, o.y - o.r, 2 * o.r, 2 * o.r]), out = [];
  for (const it of items.sort((a, b) => b.r - a.r)) {
    const w = it.text.length * 6.6, h = 14;
    const candidates = [
      { x: it.x + it.r + 5, y: it.y + 4, anchor: "start", box: [it.x + it.r + 5, it.y - 8, w, h] },
      { x: it.x - it.r - 5, y: it.y + 4, anchor: "end", box: [it.x - it.r - 5 - w, it.y - 8, w, h] },
      { x: it.x, y: it.y - it.r - 6, anchor: "middle", box: [it.x - w / 2, it.y - it.r - 18, w, h] },
      { x: it.x, y: it.y + it.r + 14, anchor: "middle", box: [it.x - w / 2, it.y + it.r + 2, w, h] },
    ];
    const hit = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
    const own = [it.x - it.r, it.y - it.r, 2 * it.r, 2 * it.r];
    const c = candidates.find((c) => c.box[0] >= 0 && c.box[0] + c.box[2] <= right
      && !placed.some((p) => !(p[0] === own[0] && p[1] === own[1] && p[2] === own[2]) && hit(p, c.box)));
    if (!c) continue;
    placed.push(c.box);
    out.push(`<text class="lbl" x="${c.x}" y="${c.y}" text-anchor="${c.anchor}">${esc(it.text)}</text>`);
  }
  return out.join("");
}

// ---------- industries ----------
async function industriesPage(params) {
  loading("Loading industries…");
  const g = groupFilters(params, "#/industries");
  const data = (await api("/sectors", { program: g.program, visa_only: g.visa ? "true" : "" })).filter((r) => r.employers > 0);
  const bySector = Object.fromEntries(data.map((r) => [r.sector, r]));
  const totE = data.reduce((a, r) => a + r.employers, 0), totX = data.reduce((a, r) => a + r.employers_with_cases, 0);
  const avgShare = totE ? totX / totE : 0;
  const shareRank = [...data].sort((a, b) => b.share_with_cases - a.share_with_cases);
  const wageRank = [...data].sort((a, b) => b.back_wages - a.back_wages);
  const maxShare = niceMax(Math.max(...data.map((r) => r.share_with_cases), 0.01));
  const maxWage = Math.max(...data.map((r) => r.back_wages), 1);
  const hbar = (r, value, max, label, extra = "") => `
    <a class="hbar" href="${g.toWatchlist({ sector: r.sector })}" data-tip="${r.sector}">
      <span class="name" title="${esc(r.name)}">${esc(r.name)}</span>
      <span class="track"><span class="fill" style="width:${Math.max(0.8, 100 * value / max)}%"></span>${extra}</span>
      <span class="v">${label}</span></a>`;

  // Scatter: how many visa employers a sector has (log x) against the share with a case (y).
  const W = 1100, H = 440, L = 52, R = 30, T = 28, B = 48;
  const xs = data.map((r) => r.employers), x0 = Math.log10(Math.max(1, Math.min(...xs)) / 1.5), x1 = Math.log10(Math.max(...xs) * 1.5);
  const sx = (v) => L + (W - L - R) * (Math.log10(v) - x0) / (x1 - x0);
  const sy = (v) => T + (H - T - B) * (1 - v / maxShare);
  const maxBw = Math.max(...data.map((r) => r.back_wages), 1);
  const rad = (bw) => 5 + 19 * Math.sqrt(bw / maxBw);
  const xticks = [100, 300, 1000, 3000, 10000, 30000].filter((t) => Math.log10(t) >= x0 && Math.log10(t) <= x1);
  const yticks = [0, maxShare / 4, maxShare / 2, (3 * maxShare) / 4, maxShare];
  const labelled = new Set([...shareRank.slice(0, 4), ...[...data].sort((a, b) => b.employers - a.employers).slice(0, 3)].map((r) => r.sector));
  const short = (name) => name.split(/[,(]/)[0];
  const scatter = `<svg class="scatter" viewBox="0 0 ${W} ${H}" role="img" aria-label="Visa employers against share with a WHD case, by industry">
    ${yticks.map((t) => `<line class="gridline" x1="${L}" x2="${W - R}" y1="${sy(t)}" y2="${sy(t)}"/><text class="axis" x="${L - 8}" y="${sy(t) + 4}" text-anchor="end">${pct(t, 0)}</text>`).join("")}
    ${xticks.map((t) => `<text class="axis" x="${sx(t)}" y="${H - B + 18}" text-anchor="middle">${n(t)}</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${sy(avgShare)}" y2="${sy(avgShare)}" stroke="var(--ink-3)" stroke-dasharray="4 4"/>
    <text class="axis" x="${W - R}" y="${sy(avgShare) - 6}" text-anchor="end">all industries ${pct(avgShare)}</text>
    <text class="axis" x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">Visa employers in the industry (log scale) →</text>
    ${[...data].sort((a, b) => b.back_wages - a.back_wages).map((r) => `<circle class="dot" data-tip="${r.sector}" cx="${sx(r.employers)}" cy="${sy(r.share_with_cases)}" r="${rad(r.back_wages)}"/>`).join("")}
    ${placeLabels(data.filter((r) => labelled.has(r.sector)).map((r) => ({ text: short(r.name), x: sx(r.employers), y: sy(r.share_with_cases), r: rad(r.back_wages) })), W - R, data.map((r) => ({ x: sx(r.employers), y: sy(r.share_with_cases), r: rad(r.back_wages) })))}
  </svg>`;

  app.innerHTML = `
    <div class="eyebrow">Enforcement history by industry</div>
    <h1 class="page-title">Industries</h1>
    <p class="lede">Visa employers grouped by industry (the first two digits of their NAICS code). Which industries have the most
      employers with Wage and Hour cases, and where the back wages are. Click any industry to open its employers in the Watchlist.</p>
    <div class="card filters">${g.html}</div>
    <div class="tiles">
      <div class="card tile"><div class="label">Industries</div><div class="value">${n(data.length)}</div><div class="sub">NAICS sectors</div></div>
      <div class="card tile"><div class="label">Share of employers with a case</div><div class="value">${pct(avgShare)}</div><div class="sub">across all industries</div></div>
      <div class="card tile alert"><div class="label">Highest share</div><div class="value">${shareRank[0] ? pct(shareRank[0].share_with_cases) : "–"}</div><div class="sub">${shareRank[0] ? esc(shareRank[0].name) : ""}</div></div>
      <div class="card tile"><div class="label">Most back wages</div><div class="value">${wageRank[0] ? compactMoney(wageRank[0].back_wages) : "–"}</div><div class="sub">${wageRank[0] ? esc(wageRank[0].name) : ""}</div></div>
    </div>
    <div class="card panel" style="margin-top:16px">
      <h3>Size against enforcement rate</h3>
      <div class="sub">Each circle is an industry: further right means more visa employers, higher means a larger share of them have a linked
        WHD case, and a bigger circle means more back wages. The dashed line is the rate across all industries.</div>
      ${scatter}
    </div>
    <div class="grid-2 even">
      <div class="card panel">
        <h3>Share of visa employers with a linked case</h3>
        <div class="sub">Dashed line: all industries (${pct(avgShare)})</div>
        <div class="hbars">${shareRank.map((r) => hbar(r, r.share_with_cases, maxShare, pct(r.share_with_cases),
          `<span class="avg-mark" style="left:${100 * avgShare / maxShare}%"></span>`)).join("")}</div>
      </div>
      <div class="card panel">
        <h3>Back wages found</h3>
        <div class="sub">In linked WHD cases, each case counted once per industry</div>
        <div class="hbars good">${wageRank.map((r) => hbar(r, r.back_wages, maxWage, compactMoney(r.back_wages))).join("")}</div>
      </div>
    </div>
    <details class="sources"><summary>Show all industries as a table</summary>
      <div class="card" style="margin-top:10px"><div class="table-wrap"><table>
        <thead><tr><th>Industry</th><th>NAICS</th><th class="num">Visa employers</th><th class="num">Applications</th><th class="num">With a case</th><th class="num">Share</th><th class="num">Cases</th><th class="num">Back wages</th><th class="num">Penalties</th></tr></thead>
        <tbody>${data.map((r) => `<tr><td><a href="${g.toWatchlist({ sector: r.sector })}">${esc(r.name)}</a></td><td class="mono">${esc(r.sector)}</td>
          <td class="num">${n(r.employers)}</td><td class="num">${n(r.applications)}</td><td class="num">${n(r.employers_with_cases)}</td>
          <td class="num">${pct(r.share_with_cases)}</td><td class="num">${n(r.cases)}</td><td class="num good-text">${money(r.back_wages)}</td><td class="num bad-text">${money(r.penalties)}</td></tr>`).join("")}</tbody>
      </table></div></div>
    </details>
    <p class="note">Industry comes from the NAICS code the employer gave on its visa applications. Employers with a missing or malformed code are left out.
      WHD cases are linked by name and location; see each employer's page for how each case was linked.</p>`;
  g.bind();
  bindTips(app, (sec) => {
    const d = bySector[sec];
    return `<b>${esc(d.name)}</b>` + tipRows([
      ["Visa employers", n(d.employers)], ["Applications", n(d.applications)],
      ["With a linked case", `${n(d.employers_with_cases)} (${pct(d.share_with_cases)})`], ["Linked cases", n(d.cases)],
      ["Back wages", money(d.back_wages)], ["Penalties", money(d.penalties)], ["Visa-program violations", n(d.visa_violations)],
    ]) + `<div class="tip-hint">Click to see these employers</div>`;
  });
}

// ---------- ask ----------
// Questions go to POST /api/ask, which streams one JSON line per lookup and then the answer.
const chat = { turns: [], busy: false };
const EXAMPLES = [
  "Which H-2B employers in Texas have the most back wages in their Wage and Hour cases?",
  "Did Cognizant have any H-1B violations? In which cases?",
  "Which industries have the highest share of visa employers with a Wage and Hour case?",
  "Is Tata Consultancy Services in Rockville a willful violator?",
];

// A tool call in plain words, for the "what I looked up" list.
function lookupText(c) {
  const a = c.input || {};
  const filters = [a.program, a.state, a.sector && (state.meta?.sectors?.[a.sector] || a.sector),
    a.since_year && `cases since ${a.since_year}`, a.visa_violations_only && "visa-program violations only",
    a.min_applications > 1 && `${a.min_applications}+ applications`,
    a.sort_by && `by ${a.sort_by.replace(/_/g, " ")}`].filter(Boolean);
  const f = filters.length ? ` <span class="muted">· ${esc(filters.join(" · "))}</span>` : "";
  switch (c.name) {
    case "search_employers": return `Searched employers for “${esc(a.query)}”`;
    case "get_employer": return `Opened <a href="#/employer/${+a.employer_id}" data-employer>employer ${+a.employer_id}</a>`;
    case "list_applications": return `Listed applications of <a href="#/employer/${+a.employer_id}" data-employer>employer ${+a.employer_id}</a>`;
    case "get_application": return `Opened application <a href="#/application/${encodeURIComponent(a.case_number)}">${esc(a.case_number)}</a>`;
    case "watchlist": return `Ranked employers with Wage and Hour cases${f}`;
    case "state_stats": return `Compared states${f}`;
    case "industry_stats": return `Compared industries${f}`;
    case "data_overview": return "Checked the database totals";
    case "representatives": return a.key ? `Listed the clients of ${esc(a.key)}` : `Ranked ${a.kind === "person" ? "attorneys and agents" : "law firms and agents"}${a.query ? ` matching “${esc(a.query)}”` : ""}`;
    default: return esc(c.name);
  }
}

// A small, safe Markdown renderer for answers: paragraphs, headings, lists, tables, bold, code and links.
// Everything is escaped first; links are kept only when they point at this site or an https page.
function inlineMd(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const local = url.match(/^https?:\/\/[^/]+\/(#\/.*)$/);
      if (local) url = local[1];
      else if (!/^(https:\/\/|#\/)/.test(url)) return label;
      const cite = /^(employer|WHD case|application) /.test(label);
      return `<a href="${url}"${url.startsWith("#/employer/") ? " data-employer" : ""}${cite ? ' class="cite"' : ""}>${label}</a>`;
    })
    .replace(/ \(unverified\)/g, ' <span class="badge warn">unverified</span>');
}
function md(src) {
  const out = [], lines = String(src || "").split("\n");
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r, k) => k > 0 && !/^[\s|:-]+$/.test(r)).map(cells);
      const num = (c) => /^[$\d][\d,.$%KMB\s-]*$/.test(c);
      out.push(`<div class="table-wrap md-table"><table class="mini"><thead><tr>${cells(rows[0]).map((c) => `<th>${inlineMd(c)}</th>`).join("")}</tr></thead><tbody>` +
        body.map((r) => `<tr>${r.map((c) => `<td${num(c) ? ' class="num"' : ""}>${inlineMd(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>");
      continue;
    }
    const h = line.match(/^#{1,4}\s+(.*)/);
    if (h) { out.push(`<h4>${inlineMd(h[1])}</h4>`); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line), items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((x) => `<li>${inlineMd(x)}</li>`).join("")}</${tag}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(\||#{1,4}\s|[-*]\s|\d+\.\s)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inlineMd(para.join(" "))}</p>`);
  }
  return out.join("");
}

function turnHtml(t, k) {
  const steps = t.calls.map((c) => `<li${c.error ? ' class="err"' : ""}>${lookupText(c)}${c.error ? ' <span class="muted">· no result</span>' : ""}</li>`).join("");
  return `
    <div class="turn">
      <div class="q"><span>${esc(t.question)}</span></div>
      <div class="card a">
        ${t.answer == null && !t.error ? `<div class="thinking"><div class="spinner"></div>${t.calls.length ? "Looking things up…" : "Reading the question…"}</div>
          ${steps ? `<ol class="steps live">${steps}</ol>` : ""}` : ""}
        ${t.error ? `<div class="callout">${icon.info}<div>${esc(t.error)}</div></div>` : ""}
        ${t.answer != null ? `<div class="md">${md(t.answer)}</div>` : ""}
        ${t.answer != null && t.calls.length ? `<details class="looked"><summary>${icon.chev}What I looked up <span class="muted">(${t.calls.length})</span></summary><ol class="steps">${steps}</ol></details>` : ""}
      </div>
    </div>`;
}

function renderChat() {
  const box = document.getElementById("chat");
  if (!box) return;
  box.innerHTML = chat.turns.map(turnHtml).join("");
  document.getElementById("ask-start").hidden = chat.turns.length > 0;
  document.getElementById("ask-new").hidden = !chat.turns.length || chat.busy;
  const btn = document.getElementById("ask-go");
  if (btn) btn.disabled = chat.busy || !chat.enabled;
}

async function ask(question) {
  question = question.trim();
  if (!question || chat.busy) return;
  const history = chat.turns.filter((t) => t.raw).slice(-3).flatMap((t) => [
    { role: "user", content: t.question }, { role: "assistant", content: t.raw }]);
  const turn = { question, calls: [], answer: null, raw: null, error: null };
  chat.turns.push(turn);
  chat.busy = true;
  renderChat();
  document.querySelector(".turn:last-child")?.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const ev = JSON.parse(buf.slice(0, nl));
        buf = buf.slice(nl + 1);
        if (ev.type === "tool") turn.calls.push(ev);
        else if (ev.type === "answer") { turn.answer = ev.answer; turn.raw = ev.raw; turn.calls = ev.tool_calls; }
        else if (ev.type === "error") turn.error = ev.message;
        renderChat();
      }
    }
    if (turn.answer == null && !turn.error) turn.error = "The answer was interrupted. Please try again.";
  } catch (err) {
    turn.error = err.message;
  }
  chat.busy = false;
  renderChat();
}

async function askPage() {
  app.innerHTML = `
    <section class="hero">
      <div class="eyebrow">Ask the data · answered by Claude from this database</div>
      <h1 class="page-title">Ask a question</h1>
      <p class="lede">Ask in plain English about the 80,950 visa employers, their FY2025 applications and the Wage and Hour cases
        linked to them. Claude answers only from this database, using the same lookups as the rest of the site, and links
        every employer, case and application it mentions so you can check it.</p>
    </section>
    <div id="chat" class="chat"></div>
    <form class="card composer" id="ask-form">
      <textarea id="ask-q" rows="2" maxlength="600" placeholder="For example: which H-2B employers in Florida had the most back wages?"></textarea>
      <div class="composer-bar">
        <span class="muted small" id="ask-state"></span>
        <span class="spacer"></span>
        <button type="button" class="btn" id="ask-new" hidden>New conversation</button>
        <button type="submit" class="btn primary" id="ask-go" disabled>Ask</button>
      </div>
    </form>
    <div id="ask-start">
      <div class="eyebrow start-h">Try asking</div>
      <div class="examples">${EXAMPLES.map((x) => `<button type="button" class="card example">${esc(x)}</button>`).join("")}</div>
      <div class="card panel how">
        <h3>How answers work</h3>
        <ul>
          <li><b>From the database only.</b> Claude looks records up with the site's own searches and rankings, then writes the answer. It doesn't use outside knowledge about companies.</li>
          <li><b>Every record is linked.</b> Each employer, Wage and Hour case and application in an answer links to its page here. A citation that no lookup returned is marked <span class="badge warn">unverified</span>.</li>
          <li><b>Check before you rely on it.</b> Cases are linked to employers by name and address, and about 3 to 4% of links are wrong. Answers can also misread a record, so open the linked page for anything important.</li>
        </ul>
      </div>
    </div>`;
  const input = document.getElementById("ask-q");
  const send = () => { const v = input.value; input.value = ""; ask(v); };
  document.getElementById("ask-form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
  document.getElementById("ask-new").addEventListener("click", () => { chat.turns = []; renderChat(); input.focus(); });
  renderChat();
  input.focus();
  const s = await api("/ask").catch(() => ({ enabled: false, reason: "offline" }));
  chat.enabled = s.enabled;
  const label = document.getElementById("ask-state");
  if (!label) return;
  label.innerHTML = s.enabled ? "Open to everyone · Enter to send, Shift+Enter for a new line"
    : s.reason === "daily_limit" ? '<span class="badge warn">Today\'s question budget is used up. It resets at midnight UTC.</span>'
    : '<span class="badge warn">Questions aren\'t switched on for this server yet.</span>';
  renderChat();
}

route();

// ---------- red flags ----------
// Patterns in the employer's own applications, each shown against the same share for the whole program.
function redFlagsPanel(flags, program) {
  if (!flags || !flags.length) return "";
  const bar = (v, cls) => `<span class="rf-track"><span class="rf-fill ${cls}" style="width:${Math.max(1, 100 * v)}%"></span></span>`;
  return `<section class="card panel rf" style="margin-top:16px">
    <h3>Red flags in its applications</h3>
    <div class="sub">Patterns in this employer's own FY2025 applications that are worth a closer look. None is
      evidence of a violation on its own. Each needs at least ${n(Math.min(...flags.map((f) => f.min_applications)))} applications to count.</div>
    <div class="rf-list">${flags.map((f) => `
      <div class="rf-item">
        <div class="rf-title"><span class="badge warn"><span class="dot"></span>${esc(f.title)}</span></div>
        <p>${esc(f.detail)}</p>
        <div class="rf-bars">
          <span class="rf-label">This employer</span>${bar(f.share, "this")}<b>${Math.round(100 * f.share)}%</b>
          <span class="rf-label">All ${esc(program)}</span>${bar(f.baseline || 0, "all")}<span class="muted">${f.baseline == null ? "–" : Math.round(100 * f.baseline) + "%"}</span>
        </div>
      </div>`).join("")}</div>
  </section>`;
}

// ---------- DOL debarments ----------
const debarStatus = (d) => d.status === "active" ? '<span class="badge bad">Active</span>'
  : d.status === "ended" ? '<span class="badge">Ended</span>' : '<span class="badge warn">Starts later</span>';
const debarPeriod = (d) => `${day(d.start_date)} to ${d.end_date === "permanent" ? "permanent" : day(d.end_date)}`;

function debarmentPanel(list) {
  if (!list.length) return "";
  return `<section class="card panel debar" id="emp-debar">
    <h3>On a DOL debarment list</h3>
    <div class="sub">Debarment is a Department of Labor enforcement finding: the entity may not use the program
      for the period shown. Matched by name${list.some((d) => d.state) ? " and state" : ""}, so confirm it is the same company.</div>
    <div class="table-wrap"><table class="mini debar-table">
      <thead><tr><th>DOL entry</th><th>Program</th><th>Period</th><th>Status</th><th>Reason</th><th class="num" title="This employer's FY2025 applications decided inside the debarment period">Apps in period</th></tr></thead>
      <tbody>${list.map((d) => `<tr>
        <td class="wrap">${esc(d.entity)}${d.location ? `<div class="muted small">${esc(d.location)}</div>` : ""}<div class="muted small">${esc(d.match)}</div></td>
        <td>${esc(d.program)}</td><td class="nowrap">${debarPeriod(d)}</td><td>${debarStatus(d)}</td>
        <td class="wrap">${esc(d.violation)}${d.cfr_citation ? `<div class="muted small">${esc(d.cfr_citation)}</div>` : ""}</td>
        <td class="num">${n(d.applications_during)}</td></tr>`).join("")}</tbody>
    </table></div>
    <div class="note">Source: <a href="${esc(list[0].source_url)}" target="_blank" rel="noopener">DOL debarment list</a>.
      <a href="#/debarments">See every DOL debarment</a>.</div>
  </section>`;
}

async function debarmentsPage(params) {
  loading("Loading DOL debarment lists…");
  const all = await api("/debarments");
  const program = params.get("program") || "";
  const rows = all.filter((d) => !program || d.program === program);
  const filing = all.filter((d) => d.employers.length);
  const active = all.filter((d) => d.status === "active").length;
  const progs = ["H-1B", "H-2A", "H-2B", "PERM"];
  app.innerHTML = `
    <div class="eyebrow">DOL enforcement</div>
    <h1 class="page-title">Debarments</h1>
    <p class="lede">Employers, agents and labor contractors that the Department of Labor has barred from a visa or
      labor certification program, from DOL's published lists. Entries are matched to the FY2025 visa filers by
      cleaned name, and by state when DOL gives one.</p>
    <div class="tiles" style="margin-top:20px">
      <div class="card tile"><div class="label">DOL debarment entries</div><div class="value">${n(all.length)}</div><div class="sub">${progs.map((p) => `${p} ${n(all.filter((d) => d.program === p).length)}`).join(" · ")}</div></div>
      <div class="card tile bad"><div class="label">Active now</div><div class="value">${n(active)}</div><div class="sub">period has not ended</div></div>
      <div class="card tile ${filing.length ? "bad" : ""}"><div class="label">Match an FY2025 visa filer</div><div class="value">${n(filing.length)}</div><div class="sub">same cleaned name${" "}(and state when given)</div></div>
    </div>
    <div class="chip-row">
      ${["", ...progs].map((p) => `<a class="chip ${p === program ? "active" : ""}" href="#/debarments${p ? "?program=" + p : ""}">${p || "All programs"}</a>`).join("")}
      ${csvLink("/debarments.csv", null, "Download every DOL debarment entry as CSV, with the IDs of matching employers")}
    </div>
    <div class="card"><div class="table-wrap" style="max-height:70vh"><table class="debar-table">
      <thead><tr><th>DOL entry</th><th>Type</th><th>Program</th><th>Period</th><th>Status</th><th>Reason</th><th>FY2025 visa filer</th></tr></thead>
      <tbody>${rows.sort((a, b) => b.employers.length - a.employers.length || (b.start_date || "").localeCompare(a.start_date || "")).map((d) => `<tr>
        <td class="wrap">${esc(d.entity)}${d.location ? `<div class="muted small">${esc(d.location)}</div>` : ""}</td>
        <td>${esc(d.entity_type)}</td><td>${esc(d.program)}</td><td class="nowrap">${debarPeriod(d)}</td><td>${debarStatus(d)}</td>
        <td class="wrap">${esc(d.violation)}</td>
        <td class="wrap">${d.employers.map((e) => `<a data-employer href="#/employer/${e.employer_id}">${esc(e.employer_name)}</a>
          <div class="muted small">${esc(e.program)} · ${esc(titleCase(e.city))}, ${esc(e.state)} · ${n(e.n_applications)} apps</div>`).join("") || '<span class="muted">–</span>'}</td></tr>`).join("")}</tbody>
    </table></div></div>
    <p class="note">Sources: <a href="https://www.dol.gov/agencies/eta/foreign-labor/program-debarments" target="_blank" rel="noopener">OFLC program debarments</a>
      (PERM, H-2A, H-2B) and <a href="https://www.dol.gov/agencies/whd/immigration/h1b/debarment" target="_blank" rel="noopener">WHD H-1B debarments</a>.
      DOL's H-1B list gives no addresses, so H-1B entries match on name alone.</p>`;
}

// ---------- shared representatives ----------
const repHref = (r) => `#/representative/${r.kind}/${encodeURIComponent(r.key)}`;
const timesBadge = (r) => r.times_typical == null || !r.clients_visa_violations ? '<span class="muted">–</span>'
  : `<span class="badge ${r.times_typical >= 3 ? "bad" : r.times_typical >= 1.5 ? "warn" : ""}" title="${n(r.clients_visa_violations)} clients with visa-program violations, against ${r.expected_visa_violations} expected for the same mix of H-1B and H-2B employers">${r.times_typical}×</span>`;
const REP_NOTE = `Representing an employer does not make a firm or attorney responsible for that employer's violations.
  A high count can simply mean a large practice. “× typical” compares the share of clients with visa-program violations
  with all employers in the same programs; with few clients it can swing a lot.`;

function repsPanel(reps) {
  if (!reps || !reps.length) return "";
  return `<section class="card panel reps" style="margin-top:16px">
    <h3>Who else their representatives file for</h3>
    <div class="sub">The law firms and attorneys or agents on this employer's applications, across every employer they file for.</div>
    <div class="table-wrap"><table class="mini">
      <thead><tr><th>Representative</th><th class="num">Clients</th><th class="num">With WHD cases</th><th class="num">With visa violations</th><th class="num">Against typical</th><th class="num">Debarred</th></tr></thead>
      <tbody>${reps.map((r) => `<tr>
        <td class="wrap"><a href="${repHref(r)}">${esc(r.name)}</a><div class="muted small">${r.kind === "firm" ? "Law firm or agent business" : "Attorney or agent"}</div></td>
        <td class="num">${n(r.clients)}</td><td class="num">${n(r.clients_with_cases)}</td>
        <td class="num">${n(r.clients_visa_violations)}</td><td class="num">${timesBadge(r)}</td>
        <td class="num">${r.clients_debarred ? `<span class="bad-text">${n(r.clients_debarred)}</span>` : "0"}</td></tr>`).join("")}</tbody>
    </table></div>
    <div class="note">${REP_NOTE} <a href="#/representatives">See all law firms and agents</a>.</div>
  </section>`;
}

const REP_SORTS = [["clients_visa_violations", "Clients with visa violations"], ["times_typical", "Against typical rate"],
  ["clients", "Clients"], ["clients_with_cases", "Clients with WHD cases"], ["back_wages", "Clients' back wages"],
  ["clients_debarred", "Debarred clients"]];

async function representativesPage(params) {
  const f = { kind: params.get("kind") || "firm", program: params.get("program") || "", q: params.get("q") || "",
              sort: params.get("sort") || "clients_visa_violations", min_clients: params.get("min_clients") || "10" };
  loading("Loading law firms and agents…");
  const d = await api("/representatives", f);
  const b = d.baseline.visa_violations;
  const go = (changes) => {
    const p = new URLSearchParams({ ...f, ...changes });
    [...p.entries()].forEach(([k, v]) => { if (!v || (k === "kind" && v === "firm") || (k === "min_clients" && v === "10") || (k === "sort" && v === "clients_visa_violations")) p.delete(k); });
    location.hash = "#/representatives" + (p.toString() ? "?" + p : "");
  };
  const seg = (attr, val, label, cur) => `<button data-${attr}="${val}" class="${cur === val ? "on" : ""}">${label}</button>`;
  const maxBw = Math.max(...d.rows.map((r) => r.back_wages), 1);
  app.innerHTML = `
    <div class="eyebrow">Shared representatives</div>
    <h1 class="page-title">Law firms and agents</h1>
    <p class="lede">The law firms, agent businesses and individual attorneys or agents named on FY2025 applications,
      across every employer they file for, and how many of those employers have Wage and Hour cases.</p>
    <div class="card filters">
      <div class="field"><label>Show</label><div class="segmented" id="r-kind">${seg("kind", "firm", "Firms and agent businesses", f.kind)}${seg("kind", "person", "Attorneys and agents", f.kind)}</div></div>
      <div class="field"><label>Clients in</label><div class="segmented" id="r-program">${seg("program", "", "All", f.program)}${seg("program", "H-1B", "H-1B", f.program)}${seg("program", "H-2B", "H-2B", f.program)}</div></div>
      <div class="field"><label for="r-q">Name</label><input id="r-q" type="search" value="${esc(f.q)}" placeholder="Part of the name" style="width:200px"></div>
      <div class="field"><label for="r-sort">Rank by</label><select id="r-sort">${REP_SORTS.map(([k, l]) => `<option value="${k}" ${k === f.sort ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><label for="r-min">Min. clients</label><input id="r-min" type="number" min="1" step="1" value="${esc(f.min_clients)}" style="width:100px"></div>
    </div>
    <div class="tiles" style="margin-top:16px">
      <div class="card tile"><div class="label">${f.kind === "firm" ? "Firms and businesses" : "Attorneys and agents"} listed</div><div class="value">${n(d.total)}</div><div class="sub">with at least ${n(f.min_clients)} client employers</div></div>
      <div class="card tile"><div class="label">Typical H-1B rate</div><div class="value">${pct(b["H-1B"])}</div><div class="sub">of H-1B employers have an H-1B violation</div></div>
      <div class="card tile"><div class="label">Typical H-2B rate</div><div class="value">${pct(b["H-2B"])}</div><div class="sub">of H-2B employers have an H-2B violation</div></div>
    </div>
    <div class="card" style="margin-top:16px">${d.rows.length ? `<div class="table-wrap" style="max-height:70vh"><table class="rep-table">
      <thead><tr><th>${f.kind === "firm" ? "Firm or agent business" : "Attorney or agent"}</th><th class="num">Clients</th><th class="num">With WHD cases</th>
        <th class="num">With visa violations</th><th class="num">Against typical</th><th class="num">Debarred</th><th class="num">Clients' back wages</th></tr></thead>
      <tbody>${d.rows.map((r) => `<tr>
        <td class="wrap"><a href="${repHref(r)}">${esc(r.name)}</a><div class="muted small">${Object.entries(r.programs).map(([p, c]) => `${p} ${n(c)}`).join(" · ")} · ${n(r.applications)} applications</div></td>
        <td class="num">${n(r.clients)}</td><td class="num">${n(r.clients_with_cases)}</td>
        <td class="num"><b>${n(r.clients_visa_violations)}</b> <span class="muted small">${pct(r.clients_visa_violations / r.clients, 0)}</span></td>
        <td class="num">${timesBadge(r)}</td>
        <td class="num">${r.clients_debarred ? `<span class="bad-text">${n(r.clients_debarred)}</span>` : "0"}</td>
        <td class="num"><span class="bar"><i style="width:${Math.max(2, 100 * r.back_wages / maxBw)}%"></i><span>${money(r.back_wages)}</span></span></td></tr>`).join("")}</tbody>
    </table></div>${d.total > d.rows.length ? `<div class="note" style="padding:10px 16px">Showing the first ${n(d.rows.length)} of ${n(d.total)}. Narrow by name or raise the minimum.</div>` : ""}`
    : `<div class="empty">No ${f.kind === "firm" ? "firms" : "attorneys or agents"} match these filters.</div>`}</div>
    <p class="note">${REP_NOTE} Firms are grouped by cleaned name, so spelling variants of one firm count together; people are grouped by
      name and the state on the form, so two people with the same name in one state count as one. WHD cases are linked to employers by
      name and location (about 3 to 4% of links are wrong).</p>`;
  document.getElementById("r-kind").addEventListener("click", (e) => { if (e.target.dataset.kind) go({ kind: e.target.dataset.kind }); });
  document.getElementById("r-program").addEventListener("click", (e) => { if (e.target.dataset.program != null) go({ program: e.target.dataset.program }); });
  document.getElementById("r-sort").addEventListener("change", (e) => go({ sort: e.target.value }));
  document.getElementById("r-min").addEventListener("change", (e) => go({ min_clients: String(Math.max(1, parseInt(e.target.value, 10) || 1)) }));
  document.getElementById("r-q").addEventListener("change", (e) => go({ q: e.target.value.trim() }));
}

async function representativePage(kind, key) {
  loading("Loading clients…");
  const r = await api(`/representatives/${kind}/${encodeURIComponent(key)}`);
  const CAP = 300;
  const shown = r.client_list.slice(0, CAP);
  const flagged = r.client_list.filter((c) => c.cases || c.debarred).length;
  app.innerHTML = `
    <a class="back" href="#/representatives${kind === "person" ? "?kind=person" : ""}">${icon.back} Law firms and agents</a>
    <section class="card emp-head">
      <div class="badges"><span class="badge">${kind === "firm" ? "Law firm or agent business" : "Attorney or agent"}</span>
        ${Object.entries(r.programs).map(([p, c]) => `<span class="badge ${p === "H-1B" ? "h1b" : "h2b"}">${p} · ${n(c)} clients</span>`).join("")}</div>
      <h1>${esc(r.name)}</h1>
      <div class="facts">
        ${r.name_variants.length > 1 ? `<span><b>Also written</b>${r.name_variants.slice(1, 5).map((v) => esc(v.name)).join(" · ")}</span>` : ""}
        ${r.feins.length ? `<span><b>Tax ID</b><span class="mono">${r.feins.slice(0, 3).map(esc).join(", ")}</span></span>` : ""}
        ${r.firms.length ? `<span><b>Firms on the form</b>${r.firms.slice(0, 3).map((f) => `<a href="#/representative/firm/${encodeURIComponent(f.key)}">${esc(f.name)}</a>`).join(" · ")}</span>` : ""}
      </div>
    </section>
    <div class="tiles">
      <div class="card tile"><div class="label">Client employers</div><div class="value">${n(r.clients)}</div><div class="sub">${n(r.applications)} applications</div></div>
      <div class="card tile ${r.clients_with_cases ? "alert" : ""}"><div class="label">Clients with WHD cases</div><div class="value">${n(r.clients_with_cases)}</div><div class="sub">${pct(r.clients_with_cases / r.clients, 0)} of clients · all laws</div></div>
      <div class="card tile ${r.times_typical >= 1.5 ? "alert" : ""}"><div class="label">With visa-program violations</div><div class="value">${n(r.clients_visa_violations)}</div><div class="sub">${r.times_typical == null ? "" : `${r.times_typical}× the typical rate (${r.expected_visa_violations} expected)`}</div></div>
      <div class="card tile ${r.clients_debarred ? "bad" : ""}"><div class="label">Debarred clients</div><div class="value">${n(r.clients_debarred)}</div><div class="sub">on a DOL debarment list</div></div>
      <div class="card tile ${r.back_wages ? "good" : ""}"><div class="label">Clients' back wages</div><div class="value">${compactMoney(r.back_wages)}</div><div class="sub">${n(r.cases)} linked cases</div></div>
    </div>
    <h2 class="section">Client employers <span class="count">${n(r.clients)}</span></h2>
    <div class="card"><div class="table-wrap" style="max-height:75vh"><table class="clickable">
      <thead><tr><th>Employer</th><th class="num">Apps filed by this ${kind === "firm" ? "firm" : "person"}</th><th class="num">WHD cases</th><th>Record</th><th class="num">Back wages</th></tr></thead>
      <tbody>${shown.map((c) => `<tr>
        <td class="wrap"><a data-employer href="#/employer/${c.employer_id}">${esc(c.employer_name)}</a><div class="muted small">${esc(c.program)} · ${esc(titleCase(c.city))}, ${esc(c.state)}</div></td>
        <td class="num">${n(c.applications_by_rep)}</td><td class="num">${n(c.cases)}</td>
        <td>${c.debarred ? '<span class="badge bad">Debarred</span> ' : ""}${c.visa_violations ? `<span class="badge warn">${esc(c.program)} violations</span>` : c.cases ? '<span class="muted small">other laws or none found</span>' : '<span class="muted">–</span>'}</td>
        <td class="num ${c.back_wages ? "good-text" : "muted"}">${c.back_wages ? money(c.back_wages) : "$0"}</td></tr>`).join("")}</tbody>
    </table></div>${r.clients > CAP ? `<div class="note" style="padding:10px 16px">Showing ${n(CAP)} of ${n(r.clients)} clients: all ${n(flagged)} with a WHD case or debarment come first.</div>` : ""}</div>
    <p class="note">${REP_NOTE} ${kind === "firm" ? "This firm is every spelling that cleans to the same name." : "This person is everyone with this name and state on the form."}
      WHD cases are linked to employers by name and location; open an employer to check each link.</p>`;
  window.scrollTo(0, 0);
}
