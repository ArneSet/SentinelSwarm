/* ===================================================================
   SENTINELSWARM — Fleet Command SPA
   Two isolated worlds (sim / real), hash-routed sub-pages,
   pan/zoom tactical map, interactive Three.js terrain.
   =================================================================== */

const CELL = 6; // coverage cell size (m); the map itself is unbounded
const FLYING = new Set(["TAKEOFF", "TRANSIT", "PATROLLING", "RETURNING"]);
const STATE_COL = {
  OFFLINE: "#d1657a", FAULT: "#d1657a", RECOVERING: "#d6a95c",
  IDLE: "#6b7280", CHARGING: "#7c93b0", ASSIGNED: "#82b6cb",
  TAKEOFF: "#82b6cb", TRANSIT: "#82b6cb", PATROLLING: "#74ae83", RETURNING: "#9a8fc9",
};
const col = (s) => STATE_COL[s] || "#7c828c";
const ACCENT = { sim: "#82b6cb", real: "#c8a15e" };
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state
function makeCtx(env) {
  return {
    env,
    snap: { drones: [], missions: [], incidents: [], summary: {}, base: { x: 0, y: 0, z: 0 }, sensor_radius: 35, environment: env === "sim" ? "simulation" : "real", t: 0 },
    view: new Map(), cov: new Map(), covArea: 0,
    cam: null, ws: null, rate: 0, lastMsg: 0, conn: false,
  };
}
const ctx = { sim: makeCtx("sim"), real: makeCtx("real") };
let world = "sim";
let route = "overview";
let selectedId = null;
let frameN = 0;

// ---------------------------------------------------------------- websockets (both worlds live)
function wsUrl(env) { return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${env}`; }
function connect(env) {
  const c = ctx[env];
  let ws;
  try { ws = new WebSocket(wsUrl(env)); } catch (e) { setTimeout(() => connect(env), 1000); return; }
  c.ws = ws;
  ws.onopen = () => { c.conn = true; };
  ws.onmessage = (e) => { try { onSnap(env, JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => { c.conn = false; setTimeout(() => connect(env), 900); };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}
function onSnap(env, s) {
  const c = ctx[env];
  const now = performance.now();
  if (c.lastMsg && now > c.lastMsg) { const hz = Math.min(120, 1000 / (now - c.lastMsg)); c.rate = c.rate ? c.rate * 0.9 + hz * 0.1 : hz; }
  c.lastMsg = now;
  c.snap = s;
  for (const d of s.drones) if (!c.view.has(d.id)) c.view.set(d.id, { rx: d.x, ry: d.y, rz: d.z, trail: [] });
  // sensor-driven coverage — the map each world builds from its own drones
  const sr = s.sensor_radius || 35;
  for (const d of s.drones) {
    if (!FLYING.has(d.state) || d.z < 1) continue;
    const fr = sr * (0.55 + Math.min(1.6, d.z / 45));
    const gi0 = Math.floor((d.x - fr) / CELL), gi1 = Math.floor((d.x + fr) / CELL);
    const gj0 = Math.floor((d.y - fr) / CELL), gj1 = Math.floor((d.y + fr) / CELL);
    for (let gi = gi0; gi <= gi1; gi++) for (let gj = gj0; gj <= gj1; gj++) {
      const cx = (gi + 0.5) * CELL, cy = (gj + 0.5) * CELL;
      const dd = Math.hypot(cx - d.x, cy - d.y); if (dd > fr) continue;
      const key = gi + "," + gj, before = c.cov.get(key) || 0;
      const val = Math.min(1, before + 0.05 * (1 - dd / fr));
      if (before < 0.02 && val >= 0.02) c.covArea += CELL * CELL;
      c.cov.set(key, val);
    }
  }
}

// ---------------------------------------------------------------- interpolation
function interp() {
  const c = ctx[world], k = 0.2;
  for (const d of c.snap.drones) {
    const v = c.view.get(d.id); if (!v) continue;
    v.rx += (d.x - v.rx) * k; v.ry += (d.y - v.ry) * k; v.rz += (d.z - v.rz) * k;
    if (frameN % 3 === 0 && FLYING.has(d.state)) { v.trail.push([v.rx, v.ry]); if (v.trail.length > 160) v.trail.shift(); }
  }
}

// ---------------------------------------------------------------- helpers
function api(path, body) {
  return fetch(`/api/${world}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).catch(() => {});
}
function delApi(path) { return fetch(`/api/${world}${path}`, { method: "DELETE" }).catch(() => {}); }
const SS = {
  addUnit: () => api("/drones", {}),
  faultUnit: (id) => api(`/drones/${id}/fault`, { code: "comms_loss" }),
  removeUnit: (id) => delApi(`/drones/${id}`),
  newMission: () => { const a = Math.random() * Math.PI * 2, r = 70 + Math.random() * 90; return api("/missions", { type: "PATROL_ZONE", x: Math.round(r * Math.cos(a)), y: Math.round(r * Math.sin(a)), radius: 22 }); },
  scatter: () => { for (let i = 0; i < 5; i++) setTimeout(SS.newMission, i * 80); },
  select: (id) => { selectedId = selectedId === id ? null : id; paint(); },
  zoom: (f) => { const c = ctx[world]; if (c.cam) c.cam.scale = Math.max(0.3, Math.min(20, c.cam.scale * f)); },
  resetView: () => { const c = ctx[world]; c.cam = { cx: 0, cy: 0, scale: 2.4 }; },
  recenter3d: () => { if (three) { three.controls.target.set(0, 0, 0); three.camera.position.set(150, 150, 210); } },
};
window.SS = SS;
const pill = (s) => `<span class="pill" style="color:${col(s)};background:${col(s)}1e">${s}</span>`;
const short = (id) => (id || "").replace(/^mission-/, "").replace(/^inc-/, "");
const fmtArea = (m2) => m2 >= 1e6 ? (m2 / 1e6).toFixed(2) + " km²" : Math.round(m2).toLocaleString() + " m²";

function unitRow(d, sel) {
  const c = col(d.state), bc = d.battery < 20 ? "#d1657a" : d.battery < 45 ? "#d6a95c" : "#74ae83";
  return `<div class="unit ${d.id === sel ? "sel" : ""}" onclick="SS.select('${d.id}')">
    <div class="g" style="color:${c}">${d.kind === "REAL" ? "◆" : "◇"}</div>
    <div><div class="id">${d.id} ${pill(d.state)}</div>
      <div class="meta"><span>ALT <b>${d.alt.toFixed(0)}m</b></span><span>RNG <b>${d.dist.toFixed(0)}m</b></span><span>${d.health}</span></div>
      <div class="bat"><i style="width:${d.battery}%;background:${bc}"></i></div></div>
    <button class="btn danger sm" onclick="event.stopPropagation();SS.faultUnit('${d.id}')">FAULT</button></div>`;
}

// ---------------------------------------------------------------- router + views
const routes = ["overview", "map", "terrain", "fleet", "missions", "incidents"];
function go() {
  const r = location.hash.replace("#/", "") || "overview";
  route = routes.includes(r) ? r : "overview";
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("on", a.dataset.r === route);
  renderView();
}

function renderView() {
  const v = $("view");
  if (route === "map") {
    v.innerHTML = `<div class="page full"><div class="surface"><canvas id="mapCanvas"></canvas></div>
      <div class="legend"><div><i style="background:#82b6cb"></i>AIRBORNE</div><div><i style="background:#74ae83"></i>PATROL</div><div><i style="background:#9a8fc9"></i>RTB</div><div><i style="background:#d1657a"></i>OFFLINE</div></div>
      <div class="overlay-controls"><button class="btn sm" onclick="SS.zoom(1.25)">+</button><button class="btn sm" onclick="SS.zoom(0.8)">−</button><button class="btn sm" onclick="SS.resetView()">RESET</button></div>
      <div class="overlay-tasking"><button class="btn sm" onclick="SS.addUnit()">+ UNIT</button><button class="btn sm" onclick="SS.newMission()">+ PATROL</button><button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button></div>
      <div class="hud br" id="mapHud"></div></div>`;
    mountMap();
  } else if (route === "terrain") {
    v.innerHTML = `<div class="page full"><div class="surface" id="terrainMount"></div>
      <div class="hud tl">3D RECONSTRUCTION · <span class="k">drag</span> orbit · <span class="k">wheel</span> zoom · <span class="k">right-drag</span> pan</div>
      <div class="overlay-controls"><button class="btn sm" onclick="SS.recenter3d()">RECENTER</button></div>
      <div class="overlay-tasking"><button class="btn sm" onclick="SS.addUnit()">+ UNIT</button><button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button></div>
      <div class="hud br" id="terHud"></div></div>`;
    mountTerrain();
  } else if (route === "fleet") {
    v.innerHTML = `<div class="page"><div class="grid" style="grid-template-columns:1fr 340px;height:100%">
      <div class="panel"><h3>Signal Sources <span class="r" id="fCount">0</span></h3><div class="bd" id="roster"></div></div>
      <div class="panel"><h3>Telemetry <span class="r" id="telTgt">FLEET</span></h3><div class="bd"><div class="read" id="telemetry"></div></div></div></div></div>`;
  } else if (route === "missions") {
    v.innerHTML = `<div class="page"><div class="panel" style="height:100%"><h3>Mission Board <span class="r" id="mCount">0</span></h3>
      <div class="bd"><div class="row" style="margin-bottom:12px"><button class="btn sm" onclick="SS.newMission()">+ PATROL</button><button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button></div><div id="missionList"></div></div></div></div>`;
  } else if (route === "incidents") {
    v.innerHTML = `<div class="page"><div class="panel" style="height:100%"><h3>Incident Log <span class="r" id="iCount">0</span></h3><div class="bd" id="incidentList"></div></div></div>`;
  } else {
    v.innerHTML = `<div class="page"><div class="kpis" id="kpis"></div>
      <div class="grid" style="grid-template-columns:1.5fr 1fr;margin-top:14px;height:calc(100% - 116px)">
        <div class="panel"><h3>Tactical Overview <span class="r" id="ovArea">0 m²</span></h3><div class="surface" style="position:relative;flex:1"><canvas id="miniCanvas"></canvas></div></div>
        <div class="panel"><h3>Recent Incidents <span class="r" id="ovInc">0</span></h3><div class="bd" id="ovIncidents"></div></div>
      </div></div>`;
  }
  paint();
}

// ---------------------------------------------------------------- 2D tactical map
function W2S(cam, cv, x, y) { return [(x - cam.cx) * cam.scale + cv.clientWidth / 2, cv.clientHeight / 2 - (y - cam.cy) * cam.scale]; }
function S2W(cam, cv, sx, sy) { return { x: (sx - cv.clientWidth / 2) / cam.scale + cam.cx, y: cam.cy - (sy - cv.clientHeight / 2) / cam.scale }; }
function mountMap() {
  const cv = $("mapCanvas"), c = ctx[world];
  if (!c.cam) c.cam = { cx: 0, cy: 0, scale: 2.4 };
  let drag = null;
  cv.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, cx: c.cam.cx, cy: c.cam.cy }; cv.setPointerCapture(e.pointerId); };
  cv.onpointermove = (e) => { if (!drag) return; c.cam.cx = drag.cx - (e.clientX - drag.x) / c.cam.scale; c.cam.cy = drag.cy + (e.clientY - drag.y) / c.cam.scale; };
  cv.onpointerup = () => { drag = null; };
  cv.onwheel = (e) => {
    e.preventDefault();
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const b = S2W(c.cam, cv, mx, my);
    c.cam.scale = Math.max(0.3, Math.min(20, c.cam.scale * Math.exp(-e.deltaY * 0.0012)));
    const a = S2W(c.cam, cv, mx, my);
    c.cam.cx += b.x - a.x; c.cam.cy += b.y - a.y;
  };
}
function fit(cv) {
  const dpr = Math.min(2, window.devicePixelRatio || 1), r = cv.getBoundingClientRect();
  if (cv.width !== Math.floor(r.width * dpr) || cv.height !== Math.floor(r.height * dpr)) {
    cv.width = Math.max(1, Math.floor(r.width * dpr)); cv.height = Math.max(1, Math.floor(r.height * dpr));
  }
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); return g;
}
function drawMap(c, cv, opts = {}) {
  const g = fit(cv), w = cv.clientWidth, h = cv.clientHeight;
  g.clearRect(0, 0, w, h);
  if (!c.cam) c.cam = { cx: 0, cy: 0, scale: 2.4 };
  const cam = opts.mini ? { cx: 0, cy: 0, scale: Math.max(0.1, Math.min(w, h) / 2 / 190) } : c.cam;
  const sc = cam.scale, acc = ACCENT[world];
  // adaptive grid
  let step = 25; while (step * sc < 60) step *= 2; while (step * sc > 160) step /= 2;
  g.strokeStyle = "rgba(255,255,255,0.035)"; g.lineWidth = 1; g.beginPath();
  const tl = S2W(cam, cv, 0, 0), br = S2W(cam, cv, w, h);
  for (let x = Math.ceil(tl.x / step) * step; x < br.x; x += step) { const [sx] = W2S(cam, cv, x, 0); g.moveTo(sx, 0); g.lineTo(sx, h); }
  for (let y = Math.ceil(br.y / step) * step; y < tl.y; y += step) { const [, sy] = W2S(cam, cv, 0, y); g.moveTo(0, sy); g.lineTo(w, sy); }
  g.stroke();
  // coverage
  const cs = CELL * sc;
  for (const [key, val] of c.cov) {
    if (val < 0.02) continue;
    const [gi, gj] = key.split(",").map(Number);
    const [sx, sy] = W2S(cam, cv, gi * CELL, (gj + 1) * CELL);
    if (sx < -cs || sx > w || sy < -cs || sy > h) continue;
    g.fillStyle = acc + Math.round(30 + val * 150).toString(16).padStart(2, "0");
    g.fillRect(sx, sy, cs + 0.6, cs + 0.6);
  }
  // range rings around base
  const [bx, by] = W2S(cam, cv, 0, 0);
  g.font = "10px 'IBM Plex Mono', monospace"; g.textAlign = "left";
  for (const rr of [25, 50, 100, 150, 200, 300]) {
    if (rr * sc < 24 || rr * sc > Math.max(w, h)) continue;
    g.beginPath(); g.arc(bx, by, rr * sc, 0, Math.PI * 2);
    g.strokeStyle = rr % 100 === 0 ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.06)"; g.stroke();
    g.fillStyle = "rgba(180,184,191,0.5)"; g.fillText(rr + "m", bx + 4, by - rr * sc + 12);
  }
  // zones
  for (const m of c.snap.missions) {
    if (m.tx == null || m.status === "COMPLETED" || m.status === "CANCELLED") continue;
    const [sx, sy] = W2S(cam, cv, m.tx, m.ty), cc = m.status === "PENDING" ? "#d6a95c" : acc;
    g.strokeStyle = cc; g.setLineDash([4, 4]); g.globalAlpha = 0.8;
    g.beginPath(); g.arc(sx, sy, Math.max(8, (m.radius || 20) * sc), 0, Math.PI * 2); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1;
    g.beginPath(); g.moveTo(sx - 5, sy); g.lineTo(sx + 5, sy); g.moveTo(sx, sy - 5); g.lineTo(sx, sy + 5); g.stroke();
  }
  // trails
  for (const d of c.snap.drones) {
    const v = c.view.get(d.id); if (!v || v.trail.length < 2) continue;
    g.beginPath();
    for (let i = 0; i < v.trail.length; i++) { const [sx, sy] = W2S(cam, cv, v.trail[i][0], v.trail[i][1]); i ? g.lineTo(sx, sy) : g.moveTo(sx, sy); }
    g.strokeStyle = acc + "26"; g.lineWidth = 1; g.stroke();
  }
  // base
  g.save(); g.translate(bx, by);
  g.strokeStyle = acc; g.globalAlpha = 0.6; g.beginPath(); g.arc(0, 0, 7, 0, Math.PI * 2); g.stroke(); g.globalAlpha = 1;
  g.fillStyle = acc; g.beginPath(); g.moveTo(0, -4); g.lineTo(4, 0); g.lineTo(0, 4); g.lineTo(-4, 0); g.closePath(); g.fill();
  g.fillStyle = "rgba(180,184,191,0.8)"; g.fillText("BASE", 9, 3); g.restore();
  // drones
  for (const d of c.snap.drones) {
    const v = c.view.get(d.id); if (!v) continue;
    const [sx, sy] = W2S(cam, cv, v.rx, v.ry), cc = col(d.state);
    if (v.rz > 1) { g.strokeStyle = cc + "3a"; g.beginPath(); g.arc(sx, sy, 6 + v.rz * 0.2, 0, Math.PI * 2); g.stroke(); }
    if (d.id === selectedId) { g.strokeStyle = "#e9e7e1"; g.strokeRect(sx - 10, sy - 10, 20, 20); }
    g.save(); g.translate(sx, sy); g.fillStyle = cc; g.strokeStyle = cc; g.lineWidth = 1.4;
    if (d.kind === "REAL") { g.beginPath(); g.moveTo(0, -6); g.lineTo(5, 5); g.lineTo(0, 2); g.lineTo(-5, 5); g.closePath(); g.fill(); }
    else { g.beginPath(); g.moveTo(0, -6); g.lineTo(6, 0); g.lineTo(0, 6); g.lineTo(-6, 0); g.closePath(); g.stroke(); g.fillStyle = cc + "30"; g.fill(); }
    g.restore();
    if (!opts.mini) {
      g.fillStyle = "rgba(233,231,225,0.9)"; g.font = "10px 'IBM Plex Mono', monospace"; g.fillText(d.id, sx + 9, sy - 3);
      g.fillStyle = "rgba(124,130,140,0.9)"; g.font = "9px 'IBM Plex Mono', monospace"; g.fillText(`${d.alt.toFixed(0)}m ${d.battery.toFixed(0)}%`, sx + 9, sy + 7);
    }
  }
  if (opts.hud) { const hud = $(opts.hud); if (hud) hud.innerHTML = `SCALE 1px≈${(1 / sc).toFixed(1)}m<br>MAPPED <b>${fmtArea(c.covArea)}</b>`; }
}

// ---------------------------------------------------------------- 3D terrain (Three.js, lazy)
let THREE = null, OrbitControls = null, three = null, threeFailed = false;
async function ensureThree() {
  if (THREE) return true; if (threeFailed) return false;
  try { THREE = await import("three"); ({ OrbitControls } = await import("three/addons/controls/OrbitControls.js")); return true; }
  catch (e) { console.error("three load failed", e); threeFailed = true; return false; }
}
async function mountTerrain() {
  const mount = $("terrainMount");
  const ok = await ensureThree();
  if (!ok || !mount) { if (mount) mount.innerHTML = `<div class="page"><div class="notice">3D engine unavailable (vendored three.js failed to load).</div></div>`; return; }
  if (!three) initThree();
  mount.appendChild(three.renderer.domElement);
  resizeThree();
}
function initThree() {
  const sc = new THREE.Scene();
  sc.background = new THREE.Color(0x0b0c0e);
  sc.fog = new THREE.Fog(0x0b0c0e, 400, 1100);
  const cam = new THREE.PerspectiveCamera(50, 1, 1, 4000);
  cam.position.set(150, 150, 210);
  const rnd = new THREE.WebGLRenderer({ antialias: true });
  rnd.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const ctr = new OrbitControls(cam, rnd.domElement);
  ctr.enableDamping = true; ctr.dampingFactor = 0.08; ctr.maxPolarAngle = 1.52; ctr.minDistance = 40; ctr.maxDistance = 1400;
  ctr.target.set(0, 0, 0);
  sc.add(new THREE.HemisphereLight(0x8fb3c9, 0x0a0c10, 0.9));
  const dir = new THREE.DirectionalLight(0xffffff, 0.7); dir.position.set(120, 260, 160); sc.add(dir);
  const grid = new THREE.GridHelper(1200, 120, 0x2a2f38, 0x181b21); grid.position.y = 0.01; sc.add(grid);
  // base marker
  const base = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 2, 16), new THREE.MeshStandardMaterial({ color: 0x82b6cb, emissive: 0x14323f }));
  base.position.y = 1; sc.add(base);
  // coverage instanced mesh
  const cap = 60000;
  const covGeo = new THREE.BoxGeometry(CELL * 0.9, 1, CELL * 0.9);
  const covMat = new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 0.05, roughness: 0.8 });
  const cov = new THREE.InstancedMesh(covGeo, covMat, cap); cov.count = 0;
  cov.instanceMatrix.setUsage(THREE.DynamicDrawUsage); sc.add(cov);
  three = {
    scene: sc, camera: cam, renderer: rnd, controls: ctr, base, cov,
    covIndex: new Map(), covCount: 0, covCap: cap, drones: new Map(), dummy: new THREE.Object3D(), color: new THREE.Color(),
  };
  window.addEventListener("resize", () => { if (route === "terrain") resizeThree(); });
}
function resizeThree() {
  const mount = $("terrainMount"); if (!mount || !three) return;
  const w = mount.clientWidth, h = mount.clientHeight;
  three.renderer.setSize(w, h, false); three.camera.aspect = w / Math.max(1, h); three.camera.updateProjectionMatrix();
}
function resetThreeForWorld() {
  if (!three) return;
  three.cov.count = 0; three.covCount = 0; three.covIndex.clear();
  for (const rec of three.drones.values()) three.scene.remove(rec.g);
  three.drones.clear();
  three.base.material.color.set(ACCENT[world]);
}
function update3D(c) {
  const acc = new THREE.Color(ACCENT[world]), deep = new THREE.Color(ACCENT[world]).multiplyScalar(0.35);
  // coverage
  for (const [key, val] of c.cov) {
    let rec = three.covIndex.get(key);
    if (!rec) { if (three.covCount >= three.covCap) continue; rec = { idx: three.covCount++, v: -1 }; three.covIndex.set(key, rec); three.cov.count = three.covCount; }
    if (Math.abs(val - rec.v) < 0.04) continue; rec.v = val;
    const [gi, gj] = key.split(",").map(Number), h = 0.6 + val * 16;
    three.dummy.position.set((gi + 0.5) * CELL, h / 2, -(gj + 0.5) * CELL);
    three.dummy.scale.set(1, h, 1); three.dummy.updateMatrix();
    three.cov.setMatrixAt(rec.idx, three.dummy.matrix);
    three.cov.setColorAt(rec.idx, three.color.copy(deep).lerp(acc, Math.min(1, val * 1.2)));
  }
  three.cov.instanceMatrix.needsUpdate = true; if (three.cov.instanceColor) three.cov.instanceColor.needsUpdate = true;
  // drones
  const seen = new Set();
  for (const d of c.snap.drones) {
    const v = c.view.get(d.id); if (!v) continue; seen.add(d.id);
    let rec = three.drones.get(d.id);
    if (!rec) {
      const g = new THREE.Group();
      const body = new THREE.Mesh(new THREE.ConeGeometry(2.4, 5.5, 6), new THREE.MeshStandardMaterial({ color: 0x82b6cb, metalness: 0.3, roughness: 0.4 }));
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 1, 4), new THREE.MeshBasicMaterial({ color: 0x82b6cb, transparent: true, opacity: 0.4 }));
      g.add(body); g.add(stem); three.scene.add(g);
      rec = { g, body, stem }; three.drones.set(d.id, rec);
    }
    const alt = Math.max(0.2, v.rz), cc = new THREE.Color(col(d.state));
    rec.g.position.set(v.rx, 0, -v.ry);
    rec.body.position.y = alt + 3; rec.body.material.color.copy(cc);
    rec.stem.scale.y = alt; rec.stem.position.y = alt / 2; rec.stem.material.color.copy(cc);
  }
  for (const [id, rec] of three.drones) if (!seen.has(id)) { three.scene.remove(rec.g); three.drones.delete(id); }
  const hud = $("terHud"); if (hud) hud.innerHTML = `UNITS <b>${c.snap.drones.length}</b> · CELLS <b>${three.covCount}</b> · MAPPED <b>${fmtArea(c.covArea)}</b>`;
}

// ---------------------------------------------------------------- DOM paint (throttled)
function paint() {
  const c = ctx[world], s = c.snap.summary || {}, env = (c.snap.environment || "").toUpperCase();
  $("classif").textContent = `UNCLASSIFIED // ${env || "—"}`;
  $("tUnits").textContent = c.snap.drones.length;
  $("tRate").textContent = `${c.rate ? c.rate.toFixed(0) : "—"} Hz`;
  $("tLed").className = "led " + (c.conn ? "on" : "off");
  $("tConn").textContent = c.conn ? "LINK LIVE" : "OFFLINE";
  $("wcName").textContent = env || "—";
  $("wcMeta").textContent = `${c.snap.drones.length} units · ${s.active_missions ?? 0} active`;
  $("wcArea").textContent = `${fmtArea(c.covArea)} mapped`;

  if (route === "overview") paintOverview(c, s);
  else if (route === "fleet") paintFleet(c);
  else if (route === "missions") paintMissions(c);
  else if (route === "incidents") paintIncidents(c);
}
function kpi(v, l, cls, sub) { return `<div class="kpi"><div class="v ${cls || ""}">${v}</div><div class="l">${l}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`; }
function paintOverview(c, s) {
  const box = $("kpis"); if (!box) return;
  const air = c.snap.drones.filter((d) => FLYING.has(d.state)).length, rate = s.mission_success_rate;
  box.innerHTML =
    kpi(s.fleet_size ?? 0, "Units", "", `${air} airborne`) +
    kpi(s.healthy ?? 0, "Healthy", "ok", `${s.offline ?? 0} offline`) +
    kpi(s.active_missions ?? 0, "Active", "ac", `${s.pending_missions ?? 0} pending`) +
    kpi(rate == null ? "—" : Math.round(rate * 100) + "%", "Success", "ok", `${s.incident_count ?? 0} incidents`);
  $("ovArea").textContent = fmtArea(c.covArea);
  $("ovInc").textContent = s.incident_count ?? 0;
  const inc = (c.snap.incidents || []).slice(-8).reverse();
  $("ovIncidents").innerHTML = inc.map(incRow).join("") || `<div class="empty">NO INCIDENTS</div>`;
}
function paintFleet(c) {
  const roster = $("roster"); if (!roster) return;
  $("fCount").textContent = c.snap.drones.length;
  if (!c.snap.drones.length) roster.innerHTML = world === "real"
    ? `<div class="empty">NO HARDWARE LINKED<br>AWAITING ESP32 / PX4 UPLINK<br><br><button class="btn sm" onclick="SS.addUnit()">+ ADD FIELD UNIT</button></div>`
    : `<div class="empty">NO UNITS<br><button class="btn sm" onclick="SS.addUnit()">+ ADD UNIT</button></div>`;
  else roster.innerHTML = c.snap.drones.map((d) => unitRow(d, selectedId)).join("");
  const sel = c.snap.drones.find((d) => d.id === selectedId);
  $("telTgt").textContent = sel ? sel.id.toUpperCase() : "FLEET";
  const t = $("telemetry");
  if (sel) t.innerHTML = `
    <div class="k">SOURCE</div><div class="val ac">${sel.kind}</div>
    <div class="k">STATE</div><div class="val">${sel.state}</div>
    <div class="k">HEALTH</div><div class="val ${sel.health === "HEALTHY" ? "ok" : "bad"}">${sel.health}</div>
    <div class="k">ALTITUDE</div><div class="val ac">${sel.alt.toFixed(1)} m</div>
    <div class="k">RANGE</div><div class="val">${sel.dist.toFixed(1)} m</div>
    <div class="k">POS X/Y</div><div class="val">${sel.x.toFixed(1)} / ${sel.y.toFixed(1)}</div>
    <div class="k">BATTERY</div><div class="val ${sel.battery < 25 ? "bad" : ""}">${sel.battery.toFixed(1)} %</div>
    <div class="k">MISSION</div><div class="val">${short(sel.mission) || "—"}</div>`;
  else { const s = c.snap.summary || {}, b = s.battery || {};
    t.innerHTML = `
    <div class="k">ENVIRONMENT</div><div class="val ac">${(c.snap.environment || "").toUpperCase()}</div>
    <div class="k">UNITS</div><div class="val">${s.fleet_size ?? 0}</div>
    <div class="k">AIRBORNE</div><div class="val ac">${c.snap.drones.filter((d) => FLYING.has(d.state)).length}</div>
    <div class="k">BATTERY MIN/AVG</div><div class="val">${b.min != null ? b.min.toFixed(0) : "—"} / ${b.avg != null ? b.avg.toFixed(0) : "—"} %</div>
    <div class="k">AREA MAPPED</div><div class="val ac">${fmtArea(c.covArea)}</div>
    <div class="k" style="color:var(--dim);margin-top:6px">SELECT A UNIT</div><div></div>`;
  }
}
function paintMissions(c) {
  const list = $("missionList"); if (!list) return;
  const ms = c.snap.missions.filter((m) => m.status !== "COMPLETED" && m.status !== "CANCELLED");
  $("mCount").textContent = ms.length;
  list.innerHTML = ms.map((m) => {
    const cc = m.status === "PENDING" ? "#d6a95c" : m.status === "FAILED" ? "#d1657a" : "#82b6cb";
    return `<div class="mission"><div>${short(m.id)} <span class="t">${m.type.replace("_", " ")}</span></div>
      <span class="pill" style="color:${cc};background:${cc}1e">${m.status}${m.attempts > 1 ? " ×" + m.attempts : ""}</span>
      <div class="who">unit ${m.drone || "—"} · target ${m.tx != null ? m.tx.toFixed(0) + "," + m.ty.toFixed(0) : "—"}</div></div>`;
  }).join("") || `<div class="empty">MISSION QUEUE EMPTY</div>`;
}
function incRow(i) {
  const warn = i.severity !== "CRITICAL";
  return `<div class="inc ${warn ? "warn" : ""}"><div class="cat">${i.category}</div><div class="d"><b>${i.drone || ""}</b> ${short(i.mission) || ""} · ${i.detail}</div></div>`;
}
function paintIncidents(c) {
  const list = $("incidentList"); if (!list) return;
  $("iCount").textContent = (c.snap.incidents || []).length;
  list.innerHTML = (c.snap.incidents || []).slice().reverse().map(incRow).join("") || `<div class="empty">NO INCIDENTS</div>`;
}

// ---------------------------------------------------------------- world switch + clock + loop
$("worldSwitch").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setWorld(b.dataset.w); });
function setWorld(w) {
  if (w === world) return;
  world = w; document.body.dataset.world = w; selectedId = null;
  for (const b of document.querySelectorAll("#worldSwitch button")) b.classList.toggle("on", b.dataset.w === w);
  if (three) resetThreeForWorld();
  renderView();
}
function tickClock() {
  const t = ctx[world].snap.t || 0;
  const p = (n) => String(Math.floor(n)).padStart(2, "0");
  $("tClock").textContent = `${p(t / 3600 % 24)}:${p(t / 60 % 60)}:${p(t % 60)}`;
}
function loop() {
  try {
    frameN++; interp();
    const c = ctx[world];
    if (route === "map") { const cv = $("mapCanvas"); if (cv) drawMap(c, cv, { hud: "mapHud" }); }
    else if (route === "overview") { const cv = $("miniCanvas"); if (cv) drawMap(c, cv, { mini: true }); }
    else if (route === "terrain" && three) { update3D(c); three.controls.update(); three.renderer.render(three.scene, three.camera); }
  } catch (e) { /* one bad frame must not kill the render loop */ }
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------- boot
connect("sim"); connect("real");
window.addEventListener("hashchange", go);
// liquid-metal cursor halo tracking
document.addEventListener("pointermove", (e) => {
  const m = e.target.closest ? e.target.closest(".world-switch button, .btn") : null;
  if (!m) return;
  const r = m.getBoundingClientRect();
  m.style.setProperty("--mx", (((e.clientX - r.left) / r.width) * 100).toFixed(1) + "%");
  m.style.setProperty("--my", (((e.clientY - r.top) / r.height) * 100).toFixed(1) + "%");
});
if (!location.hash) location.hash = "#/overview";
go();
setInterval(paint, 200);
setInterval(tickClock, 250);
requestAnimationFrame(loop);
