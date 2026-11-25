// LogiCore front end: talks to the Flask API. No data is stored in the browser.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const FIELDS = { avg: "General average", income: "Monthly income", failed: "Failing grade (1=yes, 0=no)", record: "Disciplinary record (1=yes, 0=no)" };
const OPS = [">=", "<=", ">", "<", "=="];

let csrf = null, me = null, editingId = null, SAMPLES = [], rulesCache = [];

// ---------- API helper ----------
async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  if (o.method !== "GET" && csrf) o.headers["X-CSRF-Token"] = csrf;
  const res = await fetch(path, o);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no JSON body */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || "Something went wrong.");
    err.status = res.status;
    throw err;
  }
  return data;
}

function notify(m) {
  const e = $("msg"); e.textContent = m; e.classList.remove("hidden");
  clearTimeout(notify.t); notify.t = setTimeout(() => e.classList.add("hidden"), 3500);
}
function fail(e) {
  if (e.status === 401) return showLogin("Your session ended. Please log in again.");
  notify(e.message);
}

// ---------- login / logout ----------
function showLogin(message) {
  me = null; csrf = null;
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
  $("loginMsg").textContent = message || "";
  $("password").value = "";
}
async function enterApp(data) {
  me = data.user; csrf = data.csrf;
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  $("userName").textContent = me.username;
  $("userRole").textContent = me.role === "admin" ? "Administrator" : "Staff";
  document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("hidden", me.role !== "admin"));
  openTab("eval");
  try {
    SAMPLES = (await api("/api/samples")).samples;
    $("samples").innerHTML = SAMPLES.map((s, i) => '<button type="button" class="chip" data-i="' + i + '">' + esc(s.label) + "</button>").join("");
  } catch (e) { fail(e); }
}
$("loginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  $("loginMsg").textContent = "";
  try {
    const data = await api("/api/login", { method: "POST", body: { username: $("username").value, password: $("password").value } });
    $("username").value = "";
    await enterApp(data);
  } catch (e) { $("loginMsg").textContent = e.message; }
});
$("logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* already logged out */ }
  showLogin("You have been logged out.");
});

// ---------- tabs ----------
function openTab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  ["eval", "rules", "report", "tests"].forEach(t => $(t).classList.toggle("hidden", t !== name));
  if (name === "rules") loadRules();
  if (name === "report") loadReport();
  if (name === "tests") loadTests();
}
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => openTab(b.dataset.tab)));

// ---------- evaluate ----------
function showResult(r) {
  $("empty").classList.add("hidden"); $("out").classList.remove("hidden"); $("msg").classList.add("hidden");
  const a = r.applicant, d = r.decision;
  $("who").textContent = a.name + ", average " + a.avg + ", income PHP " + Number(a.income).toLocaleString();
  $("decision").className = "decision " + d.tone;
  $("decision").style.animation = "none"; void $("decision").offsetWidth; $("decision").style.animation = "";
  $("decision").textContent = d.result;
  $("why").textContent = d.rule_code === "-" ? d.reason : "Because of " + d.rule_code + " (" + d.rule_name + "): " + d.reason;
  $("trace").innerHTML = r.trace.map(t =>
    '<li class="rung ' + (t.fired ? "fired " : "") + (t.won ? "won" : "") + '"><span class="head"><b>' + esc(t.code) + "</b> " + esc(t.name) + "</span>" +
    '<span class="state">' + (t.won ? "Decided" : t.fired ? "Matched, outranked" : "Not matched") + "</span>" +
    '<span class="when">' + esc(t.conditions) + "</span></li>").join("");
}
$("run").addEventListener("click", async () => {
  const body = { name: $("name").value.trim(), avg: $("avg").value, income: $("income").value, failed: $("failed").value, record: $("record").value };
  try { showResult(await api("/api/evaluate", { method: "POST", body })); } catch (e) { fail(e); }
});
$("samples").addEventListener("click", e => {
  const b = e.target.closest(".chip"); if (!b) return;
  const s = SAMPLES[b.dataset.i];
  $("name").value = s.name; $("avg").value = s.avg; $("income").value = s.income;
  $("failed").value = s.failed; $("record").value = s.record; $("run").click();
});
$("print").addEventListener("click", () => window.print());

// ---------- rules (admin) ----------
async function loadRules() {
  try {
    rulesCache = (await api("/api/rules")).rules;
    $("ruleRows").innerHTML = rulesCache.map(r =>
      "<tr><td>" + esc(r.code) + "</td><td>" + esc(r.name) + "</td><td>" + esc(r.text) + "</td><td>" + esc(r.result) + "</td><td>" + r.priority +
      '</td><td><button class="edit" data-id="' + r.id + '">Edit</button><button class="del" data-id="' + r.id + '" data-code="' + esc(r.code) + '">Delete</button></td></tr>').join("");
    if (!$("conds").children.length) addCondRow();
  } catch (e) { fail(e); }
}
$("ruleRows").addEventListener("click", async e => {
  const edit = e.target.closest(".edit"), del = e.target.closest(".del");
  if (edit) return startEdit(Number(edit.dataset.id));
  if (del && confirm("Delete rule " + del.dataset.code + "?")) {
    try { await api("/api/rules/" + del.dataset.id, { method: "DELETE" }); notify("Rule deleted."); loadRules(); } catch (err) { fail(err); }
  }
});
function addCondRow(c) {
  const d = document.createElement("div"); d.className = "cond";
  d.innerHTML = '<select class="cf" aria-label="Field">' + Object.entries(FIELDS).map(([k, v]) => '<option value="' + k + '">' + v + "</option>").join("") + "</select>" +
    '<select class="co" aria-label="Operator">' + OPS.map(o => "<option>" + o + "</option>").join("") + "</select>" +
    '<input class="cv" type="number" step="any" placeholder="value" aria-label="Value">';
  $("conds").appendChild(d);
  if (c) { d.querySelector(".cf").value = c.field; d.querySelector(".co").value = c.operator; d.querySelector(".cv").value = c.value; }
}
$("addCond").addEventListener("click", () => addCondRow());
function readRuleForm() {
  const conditions = [...document.querySelectorAll("#conds .cond")].map(d => ({
    field: d.querySelector(".cf").value, operator: d.querySelector(".co").value, value: d.querySelector(".cv").value
  })).filter(c => c.value !== "");
  return { name: $("rName").value.trim(), result: $("rResult").value.trim(), priority: $("rPri").value, reason: $("rReason").value.trim(), conditions };
}
function stopEdit() {
  editingId = null; $("formTitle").textContent = "Add a new rule";
  $("cancelEdit").classList.add("hidden"); $("saveRule").textContent = "Save rule";
  ["rName", "rResult", "rReason"].forEach(i => $(i).value = ""); $("rPri").value = 30;
  $("conds").innerHTML = ""; addCondRow();
}
function startEdit(id) {
  const r = rulesCache.find(x => x.id === id); if (!r) return;
  editingId = id; $("formTitle").textContent = "Edit rule " + r.code;
  $("rName").value = r.name; $("rResult").value = r.result; $("rPri").value = r.priority; $("rReason").value = r.reason;
  $("conds").innerHTML = ""; r.conditions.forEach(c => addCondRow(c));
  $("cancelEdit").classList.remove("hidden"); $("saveRule").textContent = "Update rule";
  $("rName").scrollIntoView({ behavior: "smooth", block: "center" });
}
$("cancelEdit").addEventListener("click", stopEdit);
$("saveRule").addEventListener("click", async () => {
  const body = readRuleForm();
  try {
    if (editingId) await api("/api/rules/" + editingId, { method: "PUT", body });
    else await api("/api/rules", { method: "POST", body });
    notify(editingId ? "Rule updated." : "Rule saved.");
    stopEdit(); loadRules();
  } catch (e) { fail(e); }
});
$("resetRules").addEventListener("click", async () => {
  if (!confirm("Replace all rules with the default rules?")) return;
  try { await api("/api/rules/reset", { method: "POST" }); stopEdit(); notify("Default rules restored."); loadRules(); } catch (e) { fail(e); }
});

// ---------- history and report ----------
async function loadReport() {
  try {
    const [rep, hist] = await Promise.all([api("/api/report"), api("/api/history")]);
    const stat = (v, label) => '<div class="stat"><b>' + v + "</b>" + label + "</div>";
    $("stats").innerHTML = stat(rep.total, "Total evaluated") + stat(rep.granted, "Scholarships granted") +
      stat(rep.avg_grade === null ? "-" : rep.avg_grade, "Average grade") + stat(rep.approval_rate === null ? "-" : rep.approval_rate + "%", "Approval rate");
    $("bars").innerHTML = rep.by_result.map(r =>
      '<div style="margin:8px 0"><div>' + esc(r.result) + " (" + r.count + ')</div><div class="bar" style="width:' + (r.count / rep.total * 100) + '%"></div></div>').join("") ||
      '<p class="sub">No data yet.</p>';
    $("history").innerHTML = hist.history.map(h =>
      "<tr><td>" + esc(new Date(h.decided_at).toLocaleString()) + "</td><td>" + esc(h.name) + "</td><td>" + h.avg + "</td><td>" + Number(h.income).toLocaleString() +
      "</td><td>" + esc(h.result) + "</td><td>" + esc(h.rule_code) + "</td><td>" + esc(h.decided_by || "") + "</td></tr>").join("") ||
      '<tr><td colspan="7" style="color:var(--muted)">No evaluations yet.</td></tr>';
  } catch (e) { fail(e); }
}
$("clear").addEventListener("click", async () => {
  if (!confirm("Delete all saved decisions?")) return;
  try { await api("/api/history", { method: "DELETE" }); notify("History cleared."); loadReport(); } catch (e) { fail(e); }
});

// ---------- test cases ----------
async function loadTests() {
  try {
    const t = await api("/api/tests");
    $("testRows").innerHTML = t.rows.map(s =>
      "<tr><td>" + esc(s.name) + "</td><td>" + s.avg + "</td><td>" + s.income.toLocaleString() + "</td><td>" + (s.failed ? "Yes" : "No") +
      "</td><td>" + (s.record ? "Yes" : "No") + "</td><td>" + esc(s.expect) + "</td><td>" + esc(s.got) + '</td><td class="' + (s.pass ? "ok" : "bad") + '">' + (s.pass ? "Pass" : "Fail") + "</td></tr>").join("");
    $("testSummary").textContent = t.passed + " of " + t.total + " tests passed. Last run: " + new Date().toLocaleTimeString() + ".";
    $("testSummary").className = "decision-line " + (t.passed === t.total ? "ok" : "bad");
  } catch (e) { fail(e); }
}
$("runTests").addEventListener("click", async () => { await loadTests(); notify("Tests run again."); });

// ---------- start ----------
(async function start() {
  try {
    const data = await api("/api/me");
    if (data.user) await enterApp(data); else showLogin();
  } catch (e) { showLogin(); }
})();
