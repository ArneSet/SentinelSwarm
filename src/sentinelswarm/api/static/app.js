/* ===================================================================
   SENTINELSWARM — Fleet Command SPA
   Two isolated worlds (sim / real), hash-routed sub-pages,
   pan/zoom tactical map, interactive Three.js terrain,
   advanced interactive patrol planning (Zone Loiter & Waypoint Route),
   Safe-RTB calculation, terrain reset, and unit decommissioning.
   =================================================================== */

const CELL = 6; // coverage cell size (m); the map itself is unbounded
const FLYING = new Set(["TAKEOFF", "TRANSIT", "PATROLLING", "RETURNING"]);
const STATE_COL = {
  OFFLINE: "#cf5c6f", FAULT: "#cf5c6f", RECOVERING: "#d4a24f",
  IDLE: "#6b7280", CHARGING: "#7c93b0", ASSIGNED: "#8a9bae",
  TAKEOFF: "#8a9bae", TRANSIT: "#8a9bae", PATROLLING: "#6dba7a", RETURNING: "#9a8fc9",
};
const col = (s) => STATE_COL[s] || "#767676";
const ACCENT = { sim: "#8a9bae", real: "#b89a5a" };
const $ = (id) => document.getElementById(id);
window.$ = $;

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
let showRegForm = false; // fleet page: show unit registration form

// Interactive tactical map modes: "IDLE" | "DRAW_ZONE" | "DRAW_WAYPOINTS"
let mapMode = "IDLE";
let drawZone = { startX: 0, startY: 0, curX: 0, curY: 0, isDragging: false };
let waypointPts = []; // [{x, y, z}]
let isClosedLoop = false;
let selectedPatrolDrone = null;
let selectedPatrolDrones = [];
let patrolAllocMode = "single";
let pendingMissionParams = null;

// ---------------------------------------------------------------- scout filter
function isScoutDrone(d) {
  if (!d) return false;
  const m = (d.model || "").toLowerCase();
  const id = (d.id || "").toLowerCase();
  if (m.includes("heavy") || m.includes("cargo") || m.includes("transport")) return false;
  if (id.includes("heavy") || id.includes("cargo") || id.includes("transport")) return false;
  return m.includes("scout") || m.includes("mini") || m.includes("nano") || m.includes("racer") || id.includes("scout");
}
window.isScoutDrone = isScoutDrone;

// ---------------------------------------------------------------- modal helper
function showModal(html) {
  const mount = $("modalMount");
  if (!mount) return;
  mount.innerHTML = `<div class="modal-backdrop" id="activeModalBackdrop" onclick="if(event.target===this)closeModal()">${html}</div>`;
}
function closeModal() {
  const mount = $("modalMount");
  if (mount) mount.innerHTML = "";
}
window.closeModal = closeModal;
window.showModal = showModal;

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

/** Register a drone unit with the proper world-specific payload. */
function registerUnit(formData) {
  return api("/drones", formData);
}

const SS = {
  faultUnit: (id) => api(`/drones/${id}/fault`, { code: "comms_loss" }),
  removeUnit: (id) => delApi(`/drones/${id}`).then(() => { if (selectedId === id) selectedId = null; paint(); }),
  confirmRemove: (id) => {
    if (confirm(`Decommission and remove unit "${id}" from ${world.toUpperCase()} fleet?`)) {
      SS.removeUnit(id);
    }
  },
  resetCoverage: () => {
    if (confirm(`Reset and purge all terrain/coverage mapping data for ${world.toUpperCase()}?`)) {
      api("/coverage/reset", {});
      ctx[world].cov.clear();
      ctx[world].covArea = 0;
      if (three) resetThreeForWorld();
      paint();
    }
  },
  scatter: () => {
    const a0 = Math.random() * Math.PI * 2;
    for (let i = 0; i < 5; i++) {
      setTimeout(() => {
        const a = a0 + (i * Math.PI * 2) / 5 + (Math.random() * 0.2 - 0.1);
        const r = 80 + Math.random() * 90;
        api("/missions", {
          type: "PATROL_ZONE",
          x: Math.round(r * Math.cos(a)),
          y: Math.round(r * Math.sin(a)),
          radius: 24,
          patrol_duration_s: 20,
        });
      }, i * 90);
    }
  },
  select: (id) => { selectedId = selectedId === id ? null : id; paint(); },
  zoom: (f) => { const c = ctx[world]; if (c.cam) c.cam.scale = Math.max(0.3, Math.min(20, c.cam.scale * f)); },
  resetView: () => { const c = ctx[world]; c.cam = { cx: 0, cy: 0, scale: 2.4 }; },
  recenter3d: () => { if (three) { three.controls.target.set(0, 0, 0); three.camera.position.set(150, 150, 210); } },
  showRegister: () => { showRegForm = true; renderView(); },
  hideRegister: () => { showRegForm = false; renderView(); },
  submitRegister: () => {
    const form = $("regForm"); if (!form) return;
    const fd = new FormData(form);
    const body = {
      unit_type: fd.get("unit_type") || "simulated",
      drone_id: fd.get("drone_id") || null,
      name: fd.get("name") || null,
      model: fd.get("model") || null,
      host: fd.get("host") || null,
      port: fd.get("port") ? parseInt(fd.get("port")) : null,
      protocol: fd.get("protocol") || null,
      x: parseFloat(fd.get("x")) || 0,
      y: parseFloat(fd.get("y")) || 0,
      z: parseFloat(fd.get("z")) || 0,
      battery_pct: parseFloat(fd.get("battery_pct")) || 100,
    };
    for (const k of Object.keys(body)) if (body[k] === null || body[k] === "") delete body[k];
    registerUnit(body).then(() => { showRegForm = false; renderView(); });
  },

  // Tactical Patrol launcher & interactive modes
  setAllocMode: (mode) => {
    patrolAllocMode = mode;
    SS.openPatrolLauncher();
  },
  toggleSwarmDrone: (id) => {
    const idx = selectedPatrolDrones.indexOf(id);
    if (idx >= 0) {
      selectedPatrolDrones.splice(idx, 1);
    } else {
      selectedPatrolDrones.push(id);
    }
    SS.openPatrolLauncher();
  },
  quickAllocateSwarm: (count) => {
    const c = ctx[world];
    const scouts = (c.snap.drones || []).filter(isScoutDrone);
    if (count === "all") {
      selectedPatrolDrones = scouts.map((d) => d.id);
    } else {
      const n = parseInt(count) || 2;
      selectedPatrolDrones = scouts.slice(0, n).map((d) => d.id);
    }
    SS.openPatrolLauncher();
  },
  openPatrolLauncher: () => {
    const c = ctx[world];
    const drones = c.snap.drones || [];
    const scoutDrones = drones.filter(isScoutDrone);

    // Initial default for swarm selection if empty
    if (patrolAllocMode === "swarm" && selectedPatrolDrones.length === 0 && scoutDrones.length > 0) {
      selectedPatrolDrones = scoutDrones.slice(0, Math.min(3, scoutDrones.length)).map((d) => d.id);
    }

    let singleOpts = "";
    if (scoutDrones.length === 0) {
      singleOpts = `<option value="" disabled selected>No Scout drone available in fleet</option>`;
    } else {
      singleOpts = `<option value="">Auto-Assign (Best Available Scout)</option>`;
      for (const d of scoutDrones) {
        const modelLabel = d.model ? ` [${d.model}]` : "";
        const selAttr = selectedPatrolDrone === d.id ? "selected" : "";
        singleOpts += `<option value="${d.id}" ${selAttr}>${d.id}${modelLabel} · ${d.kind} · Battery: ${d.battery.toFixed(0)}% · ${d.state}</option>`;
      }
    }

    const disabledAttr = scoutDrones.length === 0 ? "disabled style='opacity:0.45;cursor:not-allowed'" : "";
    const swarmCount = selectedPatrolDrones.length;
    const isSwarm = patrolAllocMode === "swarm";

    showModal(`
      <div class="modal-box">
        <div class="modal-header">
          <h3>Tactical Patrol & Swarm Planning</h3>
          <button class="modal-close" type="button" onclick="closeModal()">✕</button>
        </div>
        <div class="modal-body">
          ${scoutDrones.length === 0 ? `
            <div style="font-family:var(--mono);font-size:11px;color:#cf5c6f;background:rgba(207,92,111,0.12);padding:10px 12px;border-radius:6px;border:1px solid rgba(207,92,111,0.3);margin-bottom:14px">
              ⚠️ No Scout-class units registered in ${world.toUpperCase()} fleet. Only Scout drones (sim-scout, mini, nano) can perform patrol reconnaissance. Register a scout drone under Fleet to proceed.
            </div>
          ` : `
            <div style="font-family:var(--mono);font-size:10px;color:var(--accent);margin-bottom:12px;display:flex;align-items:center;gap:6px">
              <span>✓ Scout filter active: ${scoutDrones.length} scout unit${scoutDrones.length > 1 ? "s" : ""} eligible</span>
            </div>
          `}

          <!-- Allocation Mode Toggle -->
          <div class="form-group">
            <label class="form-label">Fleet Allocation Mode</label>
            <div class="swarm-mode-toggle">
              <button type="button" class="btn sm ${!isSwarm ? "ac" : ""}" onclick="SS.setAllocMode('single')">Single Scout Unit</button>
              <button type="button" class="btn sm ${isSwarm ? "ac" : ""}" onclick="SS.setAllocMode('swarm')">Coordinated Swarm (${scoutDrones.length} Available)</button>
            </div>
          </div>

          ${!isSwarm ? `
            <div class="form-group">
              <label class="form-label">Target Unit (Scout Drones Only)</label>
              <select class="form-select" id="patrolDroneSelect" ${scoutDrones.length === 0 ? "disabled" : ""}>
                ${singleOpts}
              </select>
            </div>
          ` : `
            <div class="form-group">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <label class="form-label" style="margin:0">Assign Swarm Scout Units (${swarmCount} Selected)</label>
                <div class="swarm-quick-btns">
                  <button type="button" class="btn sm" onclick="SS.quickAllocateSwarm(2)">2 Scouts</button>
                  <button type="button" class="btn sm" onclick="SS.quickAllocateSwarm(3)">3 Scouts</button>
                  <button type="button" class="btn sm" onclick="SS.quickAllocateSwarm('all')">All Scouts</button>
                </div>
              </div>
              <div class="swarm-grid">
                ${scoutDrones.map(d => {
                  const isChecked = selectedPatrolDrones.includes(d.id);
                  const bc = d.battery < 25 ? "#cf5c6f" : d.battery < 50 ? "#d4a24f" : "#6dba7a";
                  return `
                    <div class="swarm-item ${isChecked ? "active" : ""}" onclick="SS.toggleSwarmDrone('${d.id}')">
                      <input type="checkbox" ${isChecked ? "checked" : ""} onclick="event.stopPropagation();SS.toggleSwarmDrone('${d.id}')" />
                      <div class="s-info">
                        <div class="s-id">${d.id} <span class="st-badge">${d.model || d.kind}</span></div>
                        <div class="s-meta">${d.state} · Bat: <b style="color:${bc}">${d.battery.toFixed(0)}%</b></div>
                      </div>
                    </div>
                  `;
                }).join("")}
              </div>

              ${swarmCount > 1 ? `
                <div class="swarm-boost-banner">
                  <div><b>SWARM BOOST ACTIVE:</b> Disjoint Spatial Partitioning into <b>${swarmCount} parallel slice corridors</b>.</div>
                  <div class="swarm-boost-tag"><b>${swarmCount}× FASTER RECON</b> · ZERO FLIGHT OVERLAP</div>
                </div>
              ` : `
                <div style="font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:6px">
                  Select 2 or more scouts to activate parallel disjoint slice partitioning.
                </div>
              `}
            </div>
          `}

          <div class="form-divider">Select Patrol Strategy</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
            <button type="button" class="strategy-btn" onclick="startZonePatrol()" ${disabledAttr}>
              <div class="st-title">
                <span>◎ Zone Loiter</span>
                <span class="st-badge">${isSwarm && swarmCount > 1 ? `SWARM ×${swarmCount}` : "CPP BOUTROPHEDON"}</span>
              </div>
              <div class="st-desc">${isSwarm && swarmCount > 1 ? `Dispatches ${swarmCount} scout drones in strictly disjoint parallel slices for rapid total coverage.` : "Optimized Boustrophedon sweep for single scout drone zone loiter."}</div>
              <div class="st-hint"><span>▶ Click to drag-draw zone on map</span></div>
            </button>

            <button type="button" class="strategy-btn" onclick="startWaypointPatrol()" ${disabledAttr}>
              <div class="st-title">
                <span>☍ Multi-Point</span>
                <span class="st-badge">ROUTE PATH</span>
              </div>
              <div class="st-desc">Sequential tactical waypoint path planning (A → B → C) with optional continuous Rundflug circuit loop.</div>
              <div class="st-hint"><span>▶ Click to place waypoints on map</span></div>
            </button>
          </div>

          <div style="display:flex;justify-content:flex-end;margin-top:18px">
            <button class="btn sm" type="button" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);
  },

  toggleClosedLoop: () => {
    if (waypointPts.length < 2) {
      alert("Please place at least 2 points on the tactical map before closing the loop.");
      return;
    }
    isClosedLoop = !isClosedLoop;
    updateWaypointHUD();
  },
  cancelMapMode: () => {
    mapMode = "IDLE";
    drawZone.isDragging = false;
    waypointPts = [];
    isClosedLoop = false;
    const banner = $("mapModeBanner");
    if (banner) banner.remove();
  },
  clearWaypoints: () => {
    waypointPts = [];
    isClosedLoop = false;
    updateWaypointHUD();
  },
  finishWaypoints: () => {
    if (waypointPts.length < 2) {
      alert("Please place at least 2 waypoints on the tactical map.");
      return;
    }
    const pts = [...waypointPts];
    if (isClosedLoop) {
      const first = waypointPts[0];
      const last = waypointPts[waypointPts.length - 1];
      if (Math.hypot(last.x - first.x, last.y - first.y) > 2.0) {
        pts.push({ x: first.x, y: first.y, z: first.z || 15 });
      }
    }
    const droneId = selectedPatrolDrone;
    const wasLoop = isClosedLoop;
    SS.cancelMapMode();
    openDurationDialog({ type: "WAYPOINT_ROUTE", waypoints: pts, target_drone_id: droneId, is_loop: wasLoop });
  },
};
window.SS = SS;

function startZonePatrol() {
  const sel = $("patrolDroneSelect");
  if (patrolAllocMode === "single") {
    selectedPatrolDrone = sel ? sel.value || null : null;
    selectedPatrolDrones = selectedPatrolDrone ? [selectedPatrolDrone] : [];
  } else {
    if (selectedPatrolDrones.length === 0) {
      const c = ctx[world];
      const scouts = (c.snap.drones || []).filter(isScoutDrone);
      if (scouts.length > 0) {
        selectedPatrolDrones = [scouts[0].id];
      }
    }
    selectedPatrolDrone = selectedPatrolDrones[0] || null;
  }
  closeModal();
  mapMode = "DRAW_ZONE";
  if (route !== "map") {
    location.hash = "#/map";
  } else {
    renderMapModeBanner();
  }
}

function startWaypointPatrol() {
  const sel = $("patrolDroneSelect");
  selectedPatrolDrone = sel ? sel.value || null : null;
  selectedPatrolDrones = selectedPatrolDrone ? [selectedPatrolDrone] : [];
  closeModal();
  mapMode = "DRAW_WAYPOINTS";
  waypointPts = [];
  isClosedLoop = false;
  if (route !== "map") {
    location.hash = "#/map";
  } else {
    renderMapModeBanner();
  }
}

function renderMapModeBanner() {
  const existing = $("mapModeBanner");
  if (existing) existing.remove();
  const v = $("view");
  if (!v) return;
  const banner = document.createElement("div");
  banner.id = "mapModeBanner";
  banner.className = "map-mode-hud";
  if (mapMode === "DRAW_ZONE") {
    const isSwarm = patrolAllocMode === "swarm" && selectedPatrolDrones.length > 1;
    const swarmTag = isSwarm ? `[SWARM ×${selectedPatrolDrones.length} UNITS] ` : "";
    banner.innerHTML = `<span class="status-dot"></span><span>ZONE LOITER: ${swarmTag}Click & hold to anchor center, drag to expand radius</span><button class="btn sm" onclick="SS.cancelMapMode()">CANCEL</button>`;
  } else if (mapMode === "DRAW_WAYPOINTS") {
    const loopBtnLabel = isClosedLoop ? "✓ RUNDFLUG (CLOSED)" : "☍ CLOSE LOOP (RUNDFLUG)";
    const loopBtnClass = isClosedLoop ? "btn sm ac" : "btn sm";
    banner.innerHTML = `<span class="status-dot"></span><span id="wpStatusText">WAYPOINT ROUTE: ${waypointPts.length} points</span>` +
      `<button class="${loopBtnClass}" id="loopToggleBtn" onclick="SS.toggleClosedLoop()">${loopBtnLabel}</button>` +
      `<button class="btn sm" onclick="SS.finishWaypoints()">DISPATCH ROUTE</button>` +
      `<button class="btn sm" onclick="SS.clearWaypoints()">CLEAR</button>` +
      `<button class="btn sm" onclick="SS.cancelMapMode()">CANCEL</button>`;
  }
  v.appendChild(banner);
}

function updateWaypointHUD() {
  const txt = $("wpStatusText");
  if (!txt) return;
  let dist = 0;
  for (let i = 0; i < waypointPts.length - 1; i++) {
    dist += Math.hypot(waypointPts[i+1].x - waypointPts[i].x, waypointPts[i+1].y - waypointPts[i].y);
  }
  if (isClosedLoop && waypointPts.length >= 2) {
    dist += Math.hypot(waypointPts[0].x - waypointPts[waypointPts.length - 1].x, waypointPts[0].y - waypointPts[waypointPts.length - 1].y);
  }
  const loopTag = isClosedLoop ? " · [RUNDFLUG / CIRCUIT]" : "";
  txt.textContent = `WAYPOINT ROUTE: ${waypointPts.length} points · Path: ${Math.round(dist)} m${loopTag}`;
  const loopBtn = $("loopToggleBtn");
  if (loopBtn) {
    loopBtn.textContent = isClosedLoop ? "✓ RUNDFLUG (CLOSED)" : "☍ CLOSE LOOP (RUNDFLUG)";
    loopBtn.className = isClosedLoop ? "btn sm ac" : "btn sm";
  }
}

// ---------------------------------------------------------------- Duration & Safe-RTB Dialog
function openDurationDialog(params) {
  pendingMissionParams = params;
  const c = ctx[world];
  const allDrones = c.snap.drones || [];

  const targetDroneIds = (params.target_drone_ids && params.target_drone_ids.length > 0)
    ? params.target_drone_ids
    : (params.target_drone_id ? [params.target_drone_id] : []);

  const assignedDrones = targetDroneIds
    .map((id) => allDrones.find((d) => d.id === id))
    .filter(Boolean);

  const fallbackScout = allDrones.find((d) => isScoutDrone(d) && d.state === "IDLE" && d.health === "HEALTHY")
    || allDrones.find((d) => isScoutDrone(d))
    || allDrones[0];

  const primaryDrone = assignedDrones[0] || fallbackScout;
  const isSwarm = targetDroneIds.length > 1;

  // Calculate lowest battery among the assigned units
  const minBat = assignedDrones.length > 0
    ? Math.min(...assignedDrones.map((d) => d.battery))
    : (primaryDrone ? primaryDrone.battery : 100);

  // Calculate max transit distance across all assigned drones to mission target
  let maxTotalDist = 0;
  const testUnits = assignedDrones.length > 0 ? assignedDrones : (primaryDrone ? [primaryDrone] : []);

  for (const u of testUnits) {
    let dOut = 0, dIn = 0, dist = 0;
    if (params.waypoints && params.waypoints.length > 0) {
      dOut = Math.hypot(params.waypoints[0].x - u.x, params.waypoints[0].y - u.y);
      let pathDist = 0;
      for (let i = 0; i < params.waypoints.length - 1; i++) {
        pathDist += Math.hypot(params.waypoints[i+1].x - params.waypoints[i].x, params.waypoints[i+1].y - params.waypoints[i].y);
      }
      const lastWp = params.waypoints[params.waypoints.length - 1];
      dIn = Math.hypot(lastWp.x, lastWp.y);
      dist = Math.round(dOut + pathDist + dIn);
    } else {
      dOut = Math.hypot(params.x - u.x, params.y - u.y);
      dIn = Math.hypot(params.x, params.y);
      dist = Math.round(dOut + dIn);
    }
    if (dist > maxTotalDist) maxTotalDist = dist;
  }

  const drainPerMeter = 0.02; // % per m
  const transitCostPct = maxTotalDist * drainPerMeter;
  const criticalReservePct = 15.0; // critical battery threshold
  const safetyMarginPct = 10.0; // safety margin
  const totalRtbReserve = Math.round(transitCostPct + criticalReservePct + safetyMarginPct);
  const availForPatrol = Math.max(0, minBat - totalRtbReserve);
  const maxSafeTimeS = Math.min(1800, Math.floor(availForPatrol / 0.01));
  const suggestedTimeS = Math.min(60, maxSafeTimeS);

  const unitTitle = isSwarm
    ? `Swarm (${targetDroneIds.length} Scout Units: ${targetDroneIds.join(", ")})`
    : (primaryDrone ? `${primaryDrone.id} (${primaryDrone.model || primaryDrone.kind})` : "Auto-Assigned Available Scout");

  showModal(`
    <div class="modal-box">
      <div class="modal-header">
        <h3>Mission Duration & Safe-RTB Budget</h3>
        <button class="modal-close" type="button" onclick="closeModal()">✕</button>
      </div>
      <div class="modal-body">
        <div class="calc-card">
          <div class="calc-row"><span>Assigned Units</span><span class="calc-val">${unitTitle}</span></div>
          <div class="calc-row"><span>Mission Profile</span><span class="calc-val">${
            params.type === "PATROL_ZONE"
              ? `Zone Exploration (R = ${params.radius}m)`
              : (params.is_loop ? `Rundflug / Circuit Loop (${params.waypoints ? params.waypoints.length : 0} pts)` : `Open Waypoint Path (${params.waypoints ? params.waypoints.length : 0} pts)`)
          }</span></div>
          <div class="calc-row"><span>Tactical Pattern</span><span class="calc-val">${
            params.type === "PATROL_ZONE"
              ? (isSwarm ? `Coordinated ${targetDroneIds.length}-Way Disjoint Slices (Boustrophedon CPP)` : "Continuous Boustrophedon CPP Sweep")
              : (params.is_loop ? "Continuous Closed Circuit Patrol" : "Single Pass Traversal")
          }</span></div>
          <div class="calc-row"><span>Limiting Battery</span><span class="calc-val ${minBat < 35 ? 'warn' : 'ok'}">${minBat.toFixed(1)}%</span></div>
          <div class="calc-row"><span>Transit & Return Dist</span><span>${maxTotalDist} m</span></div>
          <div class="calc-row"><span>Transit Battery Drain</span><span>${transitCostPct.toFixed(1)}%</span></div>
          <div class="calc-row"><span>Safe RTB Reserve (15% crit + 10% margin)</span><span>${totalRtbReserve}%</span></div>
          <div class="calc-row"><span>Max Safe On-Station Time</span><span class="calc-val ${maxSafeTimeS > 0 ? 'ok' : 'bad'}">${maxSafeTimeS} s (${(maxSafeTimeS / 60).toFixed(1)} min)</span></div>
        </div>

        <div class="form-group">
          <label class="form-label">On-Station Patrol Duration (Seconds)</label>
          <div style="display:flex;gap:8px">
            <input class="form-input" id="durationInput" type="number" min="5" max="${Math.max(5, maxSafeTimeS)}" value="${suggestedTimeS}" />
            <button class="btn sm" type="button" onclick="setDuration(${maxSafeTimeS})">MAX TIME</button>
          </div>
        </div>

        <div style="display:flex;gap:6px;margin-bottom:16px">
          <button class="btn sm" type="button" onclick="setDuration(Math.min(30, ${maxSafeTimeS}))">30s</button>
          <button class="btn sm" type="button" onclick="setDuration(Math.min(60, ${maxSafeTimeS}))">60s</button>
          <button class="btn sm" type="button" onclick="setDuration(Math.min(120, ${maxSafeTimeS}))">120s</button>
          <button class="btn sm" type="button" onclick="setDuration(${maxSafeTimeS})">MAX SAFE</button>
        </div>

        <div class="form-actions" style="justify-content:flex-end">
          <button class="btn" type="button" onclick="submitPlannedMission()">
            ${isSwarm ? `Dispatch Swarm (${targetDroneIds.length} Units)` : "Dispatch Mission"}
          </button>
          <button class="btn" type="button" onclick="closeModal()">Cancel</button>
        </div>
      </div>
    </div>
  `);
}

function setDuration(val) {
  const el = $("durationInput");
  if (el) el.value = val;
}

function submitPlannedMission() {
  if (!pendingMissionParams) return;
  const params = pendingMissionParams;
  const durInput = $("durationInput");
  const dur = durInput ? Math.max(5, parseInt(durInput.value) || 20) : 20;

  const targetDroneIds = (params.target_drone_ids && params.target_drone_ids.length > 0)
    ? params.target_drone_ids
    : (params.target_drone_id ? [params.target_drone_id] : null);

  const payload = {
    type: params.type,
    x: params.x != null ? params.x : null,
    y: params.y != null ? params.y : null,
    radius: params.radius != null ? params.radius : 20,
    patrol_duration_s: dur,
    target_drone_id: targetDroneIds && targetDroneIds.length === 1 ? targetDroneIds[0] : null,
    target_drone_ids: targetDroneIds && targetDroneIds.length > 1 ? targetDroneIds : null,
  };
  if (params.waypoints && Array.isArray(params.waypoints)) {
    payload.waypoints = params.waypoints.map(p => [p.x, p.y, p.z || 15]);
  }
  closeModal();
  pendingMissionParams = null;
  api("/missions", payload);
}

// Global window and SS bindings
window.closeModal = closeModal;
window.showModal = showModal;
window.startZonePatrol = startZonePatrol;
window.startWaypointPatrol = startWaypointPatrol;
window.openDurationDialog = openDurationDialog;
window.submitPlannedMission = submitPlannedMission;
window.setDuration = setDuration;
window.cancelMapMode = () => SS.cancelMapMode();
window.clearWaypoints = () => SS.clearWaypoints();
window.finishWaypoints = () => SS.finishWaypoints();
window.toggleClosedLoop = () => SS.toggleClosedLoop();
Object.defineProperty(window, "waypointPts", { get: () => waypointPts });
Object.defineProperty(window, "isClosedLoop", { get: () => isClosedLoop });
Object.defineProperty(window, "mapMode", { get: () => mapMode });

SS.closeModal = closeModal;
SS.showModal = showModal;
SS.startZonePatrol = startZonePatrol;
SS.startWaypointPatrol = startWaypointPatrol;
SS.openDurationDialog = openDurationDialog;
SS.submitPlannedMission = submitPlannedMission;

const pill = (s) => `<span class="pill" style="color:${col(s)};background:${col(s)}1e">${s}</span>`;
const short = (id) => (id || "").replace(/^mission-/, "").replace(/^inc-/, "");
const fmtArea = (m2) => m2 >= 1e6 ? (m2 / 1e6).toFixed(2) + " km²" : Math.round(m2).toLocaleString() + " m²";

function unitRow(d, sel) {
  const c = col(d.state), bc = d.battery < 20 ? "#cf5c6f" : d.battery < 45 ? "#d4a24f" : "#6dba7a";
  return `<div class="unit ${d.id === sel ? "sel" : ""}" onclick="SS.select('${d.id}')">
    <div class="g" style="color:${c}">${d.kind === "REAL" ? "◆" : "◇"}</div>
    <div><div class="id">${d.id} ${pill(d.state)}</div>
      <div class="meta"><span>ALT <b>${d.alt.toFixed(0)}m</b></span><span>RNG <b>${d.dist.toFixed(0)}m</b></span><span>${d.health}</span></div>
      <div class="bat"><i style="width:${d.battery}%;background:${bc}"></i></div></div>
    <div style="display:flex;gap:4px">
      <button class="btn danger sm" onclick="event.stopPropagation();SS.faultUnit('${d.id}')">FAULT</button>
      <button class="btn danger sm" onclick="event.stopPropagation();SS.confirmRemove('${d.id}')">REMOVE</button>
    </div></div>`;
}

// ---------------------------------------------------------------- router + views
const routes = ["overview", "map", "terrain", "fleet", "missions", "incidents"];
function go() {
  const r = location.hash.replace("#/", "") || "overview";
  route = routes.includes(r) ? r : "overview";
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("on", a.dataset.r === route);
  showRegForm = false;
  if (route !== "map") SS.cancelMapMode();
  renderView();
}

function renderView() {
  const v = $("view");
  if (route === "map") {
    v.innerHTML = `<div class="page full"><div class="surface"><canvas id="mapCanvas"></canvas></div>
      <div class="legend"><div><i style="background:#8a9bae"></i>AIRBORNE</div><div><i style="background:#6dba7a"></i>PATROL</div><div><i style="background:#9a8fc9"></i>RTB</div><div><i style="background:#cf5c6f"></i>OFFLINE</div></div>
      <div class="overlay-controls">
        <button class="btn sm" onclick="SS.zoom(1.25)">+</button>
        <button class="btn sm" onclick="SS.zoom(0.8)">−</button>
        <button class="btn sm" onclick="SS.resetView()">RESET VIEW</button>
        <button class="btn sm" onclick="SS.resetCoverage()">RESET TERRAIN</button>
      </div>
      <div class="overlay-tasking">
        <button class="btn sm" onclick="SS.openPatrolLauncher()">+ PATROL</button>
        <button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button>
      </div>
      <div class="hud br" id="mapHud"></div></div>`;
    mountMap();
    if (mapMode !== "IDLE") renderMapModeBanner();
  } else if (route === "terrain") {
    v.innerHTML = `<div class="page full"><div class="surface" id="terrainMount"></div>
      <div class="hud tl">3D RECONSTRUCTION · <span class="k">drag</span> orbit · <span class="k">wheel</span> zoom · <span class="k">right-drag</span> pan</div>
      <div class="overlay-controls">
        <button class="btn sm" onclick="SS.recenter3d()">RECENTER</button>
        <button class="btn sm" onclick="SS.resetCoverage()">RESET TERRAIN</button>
      </div>
      <div class="overlay-tasking">
        <button class="btn sm" onclick="SS.openPatrolLauncher()">+ PATROL</button>
        <button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button>
      </div>
      <div class="hud br" id="terHud"></div></div>`;
    mountTerrain();
  } else if (route === "fleet") {
    renderFleetPage(v);
  } else if (route === "missions") {
    v.innerHTML = `<div class="page"><div class="panel" style="height:100%"><h3>Mission Board <span class="r" id="mCount">0</span></h3>
      <div class="bd"><div class="row" style="margin-bottom:12px">
        <button class="btn sm" onclick="SS.openPatrolLauncher()">+ PATROL</button>
        <button class="btn sm" onclick="SS.scatter()">SCATTER ×5</button>
      </div><div id="missionList"></div></div></div></div>`;
  } else if (route === "incidents") {
    v.innerHTML = `<div class="page"><div class="panel" style="height:100%"><h3>Incident Log <span class="r" id="iCount">0</span></h3><div class="bd" id="incidentList"></div></div></div>`;
  } else {
    renderOverviewPage(v);
  }
  paint();
}

// ---------------------------------------------------------------- Overview page (Phase 2)
function renderOverviewPage(v) {
  v.innerHTML = `<div class="page">
    <div class="ov-grid ov-top" id="kpis"></div>
    <div class="ov-grid ov-mid" style="margin-top:14px;height:280px">
      <div class="panel" style="display:flex;flex-direction:column">
        <h3>Tactical Preview <span class="r" id="ovArea">0 m²</span></h3>
        <div class="surface" style="position:relative;flex:1"><canvas id="miniCanvas"></canvas></div>
        <div style="display:flex;border-top:1px solid var(--line)">
          <a class="panel-link" style="flex:1" href="#/map">Open Full Map</a>
          <button class="btn sm" style="margin:6px 12px;font-size:9px" onclick="SS.resetCoverage()">RESET TERRAIN</button>
        </div>
      </div>
      <div class="panel" style="display:flex;flex-direction:column">
        <h3>3D Terrain Preview <span class="r" id="ovCov">0 cells</span></h3>
        <div class="surface" style="position:relative;flex:1" id="miniTerrainMount"></div>
        <div style="display:flex;border-top:1px solid var(--line)">
          <a class="panel-link" style="flex:1" href="#/terrain">Open 3D Terrain</a>
          <button class="btn sm" style="margin:6px 12px;font-size:9px" onclick="SS.resetCoverage()">RESET TERRAIN</button>
        </div>
      </div>
    </div>
    <div class="ov-grid ov-bot" style="margin-top:14px">
      <div class="panel"><h3>Fleet Units <span class="r" id="ovUnitCount">0</span></h3>
        <div class="bd" id="ovUnits" style="padding:0"></div>
        <a class="panel-link" href="#/fleet">View All Units</a>
      </div>
      <div class="panel"><h3>Recent Missions <span class="r" id="ovMsnCount">0</span></h3>
        <div class="bd" id="ovMissions" style="padding:0"></div>
        <a class="panel-link" href="#/missions">View All Missions</a>
      </div>
    </div>
    <div class="ov-grid ov-full" style="margin-top:14px">
      <div class="panel"><h3>Recent Incidents <span class="r" id="ovInc">0</span></h3>
        <div class="bd" id="ovIncidents"></div>
      </div>
    </div>
  </div>`;
  mountMiniTerrain();
}

/** Mini 3D terrain for overview — auto-rotating, non-interactive */
let miniThree = null;
function mountMiniTerrain() {
  const mount = $("miniTerrainMount");
  if (!mount) return;
  ensureThree().then((ok) => {
    if (!ok || !mount) return;
    if (!miniThree) initMiniThree();
    mount.appendChild(miniThree.renderer.domElement);
    const w = mount.clientWidth, h = mount.clientHeight;
    miniThree.renderer.setSize(w, h, false);
    miniThree.camera.aspect = w / Math.max(1, h);
    miniThree.camera.updateProjectionMatrix();
  });
}
function initMiniThree() {
  const sc = new THREE.Scene();
  sc.background = new THREE.Color(0x0a0a0a);
  sc.fog = new THREE.Fog(0x0a0a0a, 300, 800);
  const cam = new THREE.PerspectiveCamera(50, 1, 1, 2000);
  cam.position.set(120, 120, 160);
  cam.lookAt(0, 0, 0);
  const rnd = new THREE.WebGLRenderer({ antialias: true });
  rnd.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  sc.add(new THREE.HemisphereLight(0x8fb3c9, 0x0a0c10, 0.7));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5); dir.position.set(100, 200, 120); sc.add(dir);
  const grid = new THREE.GridHelper(800, 80, 0x2a2f38, 0x181b21); grid.position.y = 0.01; sc.add(grid);
  const base = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 2, 16), new THREE.MeshStandardMaterial({ color: parseInt(ACCENT[world].slice(1), 16), emissive: 0x14323f }));
  base.position.y = 1; sc.add(base);
  const cap = 20000;
  const covGeo = new THREE.BoxGeometry(CELL * 0.9, 1, CELL * 0.9);
  const covMat = new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 0.05, roughness: 0.8 });
  const cov = new THREE.InstancedMesh(covGeo, covMat, cap); cov.count = 0;
  cov.instanceMatrix.setUsage(THREE.DynamicDrawUsage); sc.add(cov);
  miniThree = { scene: sc, camera: cam, renderer: rnd, base, cov, covIndex: new Map(), covCount: 0, covCap: cap, dummy: new THREE.Object3D(), color: new THREE.Color(), angle: 0 };
}
function updateMini3D(c) {
  if (!miniThree) return;
  const acc = new THREE.Color(ACCENT[world]), deep = new THREE.Color(ACCENT[world]).multiplyScalar(0.35);
  for (const [key, val] of c.cov) {
    let rec = miniThree.covIndex.get(key);
    if (!rec) { if (miniThree.covCount >= miniThree.covCap) continue; rec = { idx: miniThree.covCount++, v: -1 }; miniThree.covIndex.set(key, rec); miniThree.cov.count = miniThree.covCount; }
    if (Math.abs(val - rec.v) < 0.08) continue; rec.v = val;
    const [gi, gj] = key.split(",").map(Number), h = 0.6 + val * 12;
    miniThree.dummy.position.set((gi + 0.5) * CELL, h / 2, -(gj + 0.5) * CELL);
    miniThree.dummy.scale.set(1, h, 1); miniThree.dummy.updateMatrix();
    miniThree.cov.setMatrixAt(rec.idx, miniThree.dummy.matrix);
    miniThree.cov.setColorAt(rec.idx, miniThree.color.copy(deep).lerp(acc, Math.min(1, val * 1.2)));
  }
  miniThree.cov.instanceMatrix.needsUpdate = true;
  if (miniThree.cov.instanceColor) miniThree.cov.instanceColor.needsUpdate = true;
  // auto-orbit
  miniThree.angle += 0.003;
  const r = 180;
  miniThree.camera.position.set(r * Math.cos(miniThree.angle), 120, r * Math.sin(miniThree.angle));
  miniThree.camera.lookAt(0, 0, 0);
  miniThree.base.material.color.set(ACCENT[world]);
}

// ---------------------------------------------------------------- Fleet page
function renderFleetPage(v) {
  if (showRegForm) {
    v.innerHTML = `<div class="page"><div class="panel" style="max-width:560px;margin:0 auto">
      <h3>Register New Unit</h3>
      <div class="bd">${renderRegForm()}</div></div></div>`;
    return;
  }
  v.innerHTML = `<div class="page"><div class="grid" style="grid-template-columns:1fr 340px;height:100%">
    <div class="panel"><h3>Signal Sources <span class="r" id="fCount">0</span></h3>
      <div class="bd">
        <div class="row" style="margin-bottom:12px"><button class="btn sm" onclick="SS.showRegister()">+ REGISTER UNIT</button></div>
        <div id="roster"></div>
      </div>
    </div>
    <div class="panel"><h3>Telemetry <span class="r" id="telTgt">FLEET</span></h3><div class="bd"><div class="read" id="telemetry"></div></div></div></div></div>`;
}

function renderRegForm() {
  const isSim = world === "sim";
  return `<form id="regForm" onsubmit="event.preventDefault();SS.submitRegister()">
    <div class="form-group">
      <label class="form-label">Unit Identifier</label>
      <input class="form-input" name="drone_id" placeholder="e.g. ${isSim ? "sim-4" : "uav-bravo"}" />
    </div>
    <div class="form-group">
      <label class="form-label">Name (optional)</label>
      <input class="form-input" name="name" placeholder="e.g. Scout Alpha" />
    </div>
    <div class="form-group">
      <label class="form-label">Unit Type</label>
      <div class="form-radio">
        ${isSim ? `
          <label><input type="radio" name="unit_type" value="simulated" checked /> Simulated Agent</label>
        ` : `
          <label><input type="radio" name="unit_type" value="esp32" checked /> ESP32 Mini-Drone</label>
          <label><input type="radio" name="unit_type" value="px4_ros2" /> PX4 / ROS 2 Vehicle</label>
          <label><input type="radio" name="unit_type" value="custom" /> Custom Driver</label>
        `}
      </div>
    </div>
    ${!isSim ? `
      <div class="form-divider">Network / Communication</div>
      <div class="form-group">
        <label class="form-label">Host / IP Address</label>
        <input class="form-input" name="host" placeholder="192.168.1.x" required />
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Port</label>
          <input class="form-input" name="port" type="number" placeholder="8883" required />
        </div>
        <div class="form-group" style="grid-column: span 2">
          <label class="form-label">Protocol</label>
          <select class="form-select" name="protocol" required>
            <option value="mqtt">MQTT</option>
            <option value="serial">Serial</option>
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
      </div>
    ` : ""}
    <div class="form-divider">Vehicle Configuration</div>
    <div class="form-group">
      <label class="form-label">Model / Hardware Type</label>
      <select class="form-select" name="model">
        ${isSim ? `
          <option value="sim-generic">Simulated Generic</option>
          <option value="sim-scout">Simulated Scout</option>
          <option value="sim-heavy">Simulated Heavy Lifter</option>
        ` : `
          <option value="">— Select Model —</option>
          <option value="esp32-s3-mini">ESP32-S3 Mini</option>
          <option value="esp32-c6-nano">ESP32-C6 Nano</option>
          <option value="px4-mini-racer">PX4 Mini Racer</option>
          <option value="px4-x500">PX4 X500</option>
          <option value="custom">Custom / Other</option>
        `}
      </select>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Start X (m)</label>
        <input class="form-input" name="x" type="number" value="0" step="0.1" />
      </div>
      <div class="form-group">
        <label class="form-label">Start Y (m)</label>
        <input class="form-input" name="y" type="number" value="0" step="0.1" />
      </div>
      <div class="form-group">
        <label class="form-label">Battery (%)</label>
        <input class="form-input" name="battery_pct" type="number" value="100" min="0" max="100" />
      </div>
    </div>
    <input type="hidden" name="z" value="0" />
    <div class="form-actions">
      <button class="btn" type="submit">Register Unit</button>
      <button class="btn" type="button" onclick="SS.hideRegister()">Cancel</button>
    </div>
  </form>`;
}

// ---------------------------------------------------------------- 2D tactical map
function W2S(cam, cv, x, y) { return [(x - cam.cx) * cam.scale + cv.clientWidth / 2, cv.clientHeight / 2 - (y - cam.cy) * cam.scale]; }
function S2W(cam, cv, sx, sy) { return { x: (sx - cv.clientWidth / 2) / cam.scale + cam.cx, y: cam.cy - (sy - cv.clientHeight / 2) / cam.scale }; }

function mountMap() {
  const cv = $("mapCanvas"), c = ctx[world];
  if (!c.cam) c.cam = { cx: 0, cy: 0, scale: 2.4 };
  let drag = null;

  cv.onpointerdown = (e) => {
    const r = cv.getBoundingClientRect();
    const p = S2W(c.cam, cv, e.clientX - r.left, e.clientY - r.top);

    if (mapMode === "DRAW_ZONE") {
      drawZone = { startX: p.x, startY: p.y, curX: p.x, curY: p.y, isDragging: true };
      cv.setPointerCapture(e.pointerId);
      return;
    }

    if (mapMode === "DRAW_WAYPOINTS") {
      if (waypointPts.length >= 2) {
        const [firstSx, firstSy] = W2S(c.cam, cv, waypointPts[0].x, waypointPts[0].y);
        const clickSx = e.clientX - r.left;
        const clickSy = e.clientY - r.top;
        if (Math.hypot(clickSx - firstSx, clickSy - firstSy) < 24) {
          isClosedLoop = !isClosedLoop;
          updateWaypointHUD();
          return;
        }
      }
      waypointPts.push({ x: Math.round(p.x), y: Math.round(p.y), z: 15 });
      updateWaypointHUD();
      return;
    }

    // Normal pan drag
    drag = { x: e.clientX, y: e.clientY, cx: c.cam.cx, cy: c.cam.cy };
    cv.setPointerCapture(e.pointerId);
  };

  cv.onpointermove = (e) => {
    const r = cv.getBoundingClientRect();
    const p = S2W(c.cam, cv, e.clientX - r.left, e.clientY - r.top);

    if (mapMode === "DRAW_ZONE" && drawZone.isDragging) {
      drawZone.curX = p.x;
      drawZone.curY = p.y;
      return;
    }

    if (!drag) return;
    c.cam.cx = drag.cx - (e.clientX - drag.x) / c.cam.scale;
    c.cam.cy = drag.cy + (e.clientY - drag.y) / c.cam.scale;
  };

  cv.onpointerup = (e) => {
    if (mapMode === "DRAW_ZONE" && drawZone.isDragging) {
      drawZone.isDragging = false;
      const radius = Math.max(10, Math.round(Math.hypot(drawZone.curX - drawZone.startX, drawZone.curY - drawZone.startY)));
      const center = { x: Math.round(drawZone.startX), y: Math.round(drawZone.startY) };
      const drones = patrolAllocMode === "swarm" && selectedPatrolDrones.length > 0
        ? [...selectedPatrolDrones]
        : (selectedPatrolDrone ? [selectedPatrolDrone] : []);
      const primaryDroneId = drones[0] || null;
      SS.cancelMapMode();
      openDurationDialog({
        type: "PATROL_ZONE",
        x: center.x,
        y: center.y,
        radius,
        target_drone_id: primaryDroneId,
        target_drone_ids: drones.length > 0 ? drones : null,
      });
      return;
    }
    drag = null;
  };

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

  // Set cursor style based on active mode
  cv.style.cursor = mapMode !== "IDLE" ? "crosshair" : "grab";

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

  // Active missions
  for (const m of c.snap.missions) {
    if (m.status === "COMPLETED" || m.status === "CANCELLED") continue;
    const cc = m.status === "PENDING" ? "#d4a24f" : acc;

    // Multi-waypoint route missions
    if (m.waypoints && m.waypoints.length > 0) {
      g.strokeStyle = cc; g.lineWidth = 1.6; g.setLineDash([4, 4]);
      g.beginPath();
      for (let i = 0; i < m.waypoints.length; i++) {
        const [wx, wy] = W2S(cam, cv, m.waypoints[i][0], m.waypoints[i][1]);
        if (i === 0) g.moveTo(wx, wy); else g.lineTo(wx, wy);
      }
      g.stroke(); g.setLineDash([]);
      for (let i = 0; i < m.waypoints.length; i++) {
        const [wx, wy] = W2S(cam, cv, m.waypoints[i][0], m.waypoints[i][1]);
        g.fillStyle = cc; g.beginPath(); g.arc(wx, wy, 5, 0, Math.PI * 2); g.fill();
      }
    } else if (m.tx != null) {
      // Circular patrol zone
      const [sx, sy] = W2S(cam, cv, m.tx, m.ty);
      g.strokeStyle = cc; g.setLineDash([4, 4]); g.globalAlpha = 0.8;
      g.beginPath(); g.arc(sx, sy, Math.max(8, (m.radius || 20) * sc), 0, Math.PI * 2); g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
      g.beginPath(); g.moveTo(sx - 5, sy); g.lineTo(sx + 5, sy); g.moveTo(sx, sy - 5); g.lineTo(sx, sy + 5); g.stroke();
    }
  }

  // Interactive ZONE DRAWING preview
  if (mapMode === "DRAW_ZONE" && drawZone.isDragging) {
    const [sx, sy] = W2S(cam, cv, drawZone.startX, drawZone.startY);
    const rad = Math.hypot(drawZone.curX - drawZone.startX, drawZone.curY - drawZone.startY);
    g.strokeStyle = "#ededed"; g.lineWidth = 2; g.setLineDash([6, 4]);
    g.beginPath(); g.arc(sx, sy, Math.max(4, rad * sc), 0, Math.PI * 2); g.stroke();
    g.fillStyle = acc + "33"; g.fill();
    g.setLineDash([]);

    const assignedDrones = patrolAllocMode === "swarm" && selectedPatrolDrones.length > 1
      ? selectedPatrolDrones
      : (selectedPatrolDrone ? [selectedPatrolDrone] : []);
    const N = assignedDrones.length;

    // Swarm slice division visualization
    if (N > 1 && rad > 12) {
      g.setLineDash([3, 3]);
      g.lineWidth = 1.2;
      for (let i = 1; i < N; i++) {
        const xRel = -rad + (2 * rad * i) / N;
        const yRel = Math.sqrt(Math.max(0, rad * rad - xRel * xRel));
        const [x1, y1] = W2S(cam, cv, drawZone.startX + xRel, drawZone.startY + yRel);
        const [x2, y2] = W2S(cam, cv, drawZone.startX + xRel, drawZone.startY - yRel);
        g.strokeStyle = acc;
        g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      }
      g.setLineDash([]);
      g.font = "bold 9px 'IBM Plex Mono', monospace";
      g.textAlign = "center";
      for (let k = 0; k < N; k++) {
        const xRelMid = -rad + (2 * rad * (k + 0.5)) / N;
        const [mx, my] = W2S(cam, cv, drawZone.startX + xRelMid, drawZone.startY);
        const dId = assignedDrones[k] || `D${k + 1}`;
        g.fillStyle = "#ffffff";
        g.fillText(dId, mx, my - 2);
        g.fillStyle = acc;
        g.fillText(`CORRIDOR ${k + 1}/${N}`, mx, my + 10);
      }
      g.textAlign = "left";
    }

    // Radius label
    g.fillStyle = "#ffffff"; g.font = "bold 11px 'IBM Plex Mono', monospace";
    const swarmLabel = N > 1 ? ` · SWARM ×${N} (${(100 / N).toFixed(0)}% SLICES)` : "";
    g.fillText(`RADIUS: ${Math.round(rad)}m${swarmLabel}`, sx + 12, sy - 12);
    // Center point
    g.fillStyle = "#ededed"; g.beginPath(); g.arc(sx, sy, 4, 0, Math.PI * 2); g.fill();
  }

  // Interactive WAYPOINT DRAWING preview
  if (mapMode === "DRAW_WAYPOINTS" && waypointPts.length > 0) {
    g.strokeStyle = acc; g.lineWidth = 2; g.setLineDash([5, 5]);
    g.beginPath();
    for (let i = 0; i < waypointPts.length; i++) {
      const [wx, wy] = W2S(cam, cv, waypointPts[i].x, waypointPts[i].y);
      if (i === 0) g.moveTo(wx, wy); else g.lineTo(wx, wy);
    }
    if (isClosedLoop && waypointPts.length >= 2) {
      const [wx0, wy0] = W2S(cam, cv, waypointPts[0].x, waypointPts[0].y);
      g.lineTo(wx0, wy0);
    }
    g.stroke(); g.setLineDash([]);

    for (let i = 0; i < waypointPts.length; i++) {
      const [wx, wy] = W2S(cam, cv, waypointPts[i].x, waypointPts[i].y);
      const label = String.fromCharCode(65 + i);
      g.fillStyle = acc; g.beginPath(); g.arc(wx, wy, 9, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#000000"; g.font = "bold 10px 'IBM Plex Mono', monospace"; g.textAlign = "center";
      g.fillText(label, wx, wy + 3.5);
      g.textAlign = "left";
    }

    if (isClosedLoop && waypointPts.length >= 2) {
      const [wx0, wy0] = W2S(cam, cv, waypointPts[0].x, waypointPts[0].y);
      g.strokeStyle = acc; g.lineWidth = 1.5;
      g.beginPath(); g.arc(wx0, wy0, 14, 0, Math.PI * 2); g.stroke();
      g.fillStyle = acc; g.font = "bold 9px 'IBM Plex Mono', monospace";
      g.fillText("RUNDFLUG ☍", wx0 + 14, wy0 - 8);
    }
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
    if (d.id === selectedId) { g.strokeStyle = "#ededed"; g.strokeRect(sx - 10, sy - 10, 20, 20); }
    g.save(); g.translate(sx, sy); g.fillStyle = cc; g.strokeStyle = cc; g.lineWidth = 1.4;
    if (d.kind === "REAL") { g.beginPath(); g.moveTo(0, -6); g.lineTo(5, 5); g.lineTo(0, 2); g.lineTo(-5, 5); g.closePath(); g.fill(); }
    else { g.beginPath(); g.moveTo(0, -6); g.lineTo(6, 0); g.lineTo(0, 6); g.lineTo(-6, 0); g.closePath(); g.stroke(); g.fillStyle = cc + "30"; g.fill(); }
    g.restore();
    if (!opts.mini) {
      g.fillStyle = "rgba(237,237,237,0.9)"; g.font = "10px 'IBM Plex Mono', monospace"; g.fillText(d.id, sx + 9, sy - 3);
      g.fillStyle = "rgba(118,118,118,0.9)"; g.font = "9px 'IBM Plex Mono', monospace"; g.fillText(`${d.alt.toFixed(0)}m ${d.battery.toFixed(0)}%`, sx + 9, sy + 7);
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
  sc.background = new THREE.Color(0x0a0a0a);
  sc.fog = new THREE.Fog(0x0a0a0a, 400, 1100);
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
  const base = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 2, 16), new THREE.MeshStandardMaterial({ color: parseInt(ACCENT[world].slice(1), 16), emissive: 0x14323f }));
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
  if (miniThree) {
    miniThree.cov.count = 0; miniThree.covCount = 0; miniThree.covIndex.clear();
    miniThree.base.material.color.set(ACCENT[world]);
  }
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
      const body = new THREE.Mesh(new THREE.ConeGeometry(2.4, 5.5, 6), new THREE.MeshStandardMaterial({ color: parseInt(ACCENT[world].slice(1), 16), metalness: 0.3, roughness: 0.4 }));
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 1, 4), new THREE.MeshBasicMaterial({ color: parseInt(ACCENT[world].slice(1), 16), transparent: true, opacity: 0.4 }));
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
  const air = c.snap.drones.filter((d) => FLYING.has(d.state)).length;
  const rate = s.mission_success_rate;
  const b = s.battery || {};
  box.innerHTML =
    kpi(s.fleet_size ?? 0, "Fleet Size", "", `${air} airborne`) +
    kpi(s.healthy ?? 0, "Healthy", "ok", `${s.offline ?? 0} offline`) +
    kpi(air, "Airborne", "ac", `${c.snap.drones.filter(d => d.state === "TRANSIT").length} transit`) +
    kpi(s.active_missions ?? 0, "Active Missions", "ac", `${s.pending_missions ?? 0} pending`) +
    kpi(rate == null ? "—" : Math.round(rate * 100) + "%", "Success Rate", "ok", `avg ${s.mission_latency_avg != null ? s.mission_latency_avg.toFixed(1) + "s" : "—"}`) +
    kpi(s.incident_count ?? 0, "Incidents", s.incident_count > 0 ? "bad" : "", `last ${c.snap.incidents.length > 0 ? "recent" : "none"}`) +
    kpi(fmtArea(c.covArea), "Coverage", "ac", "") +
    kpi(b.avg != null ? b.avg.toFixed(0) + "%" : "—", "Avg Battery", b.avg != null && b.avg < 30 ? "warn" : "", b.min != null ? `min ${b.min.toFixed(0)}% / max ${(b.max ?? 100).toFixed(0)}%` : "");

  const ovArea = $("ovArea"); if (ovArea) ovArea.textContent = fmtArea(c.covArea);
  const ovCov = $("ovCov"); if (ovCov) ovCov.textContent = (miniThree ? miniThree.covCount : 0) + " cells";

  const ovInc = $("ovInc"); if (ovInc) ovInc.textContent = s.incident_count ?? 0;
  const incBox = $("ovIncidents");
  if (incBox) {
    const inc = (c.snap.incidents || []).slice(-6).reverse();
    incBox.innerHTML = inc.map(incRow).join("") || `<div class="empty">NO INCIDENTS</div>`;
  }

  const ovUnitCount = $("ovUnitCount"); if (ovUnitCount) ovUnitCount.textContent = c.snap.drones.length;
  const ovUnits = $("ovUnits");
  if (ovUnits) {
    const statePrio = { FAULT: 0, OFFLINE: 1, PATROLLING: 2, TRANSIT: 3, TAKEOFF: 4, RETURNING: 5, ASSIGNED: 6, IDLE: 7, CHARGING: 8 };
    const sorted = [...c.snap.drones].sort((a, b) => (statePrio[a.state] ?? 9) - (statePrio[b.state] ?? 9)).slice(0, 6);
    ovUnits.innerHTML = sorted.map(d => {
      const cc = col(d.state), bc = d.battery < 20 ? "#cf5c6f" : d.battery < 45 ? "#d4a24f" : "#6dba7a";
      return `<div class="unit-mini"><span class="g" style="color:${cc}">${d.kind === "REAL" ? "◆" : "◇"}</span><span class="id">${d.id}</span><span class="st">${pill(d.state)}</span><span class="bat-sm"><i style="width:${d.battery}%;background:${bc}"></i></span></div>`;
    }).join("") || `<div class="empty" style="margin:10px">NO UNITS</div>`;
  }

  const ovMsnCount = $("ovMsnCount"); if (ovMsnCount) ovMsnCount.textContent = c.snap.missions.length;
  const ovMissions = $("ovMissions");
  if (ovMissions) {
    const recent = c.snap.missions.filter(m => m.status !== "COMPLETED" && m.status !== "CANCELLED").slice(0, 5);
    ovMissions.innerHTML = recent.map(m => {
      const cc = m.status === "PENDING" ? "#d4a24f" : m.status === "FAILED" ? "#cf5c6f" : "#8a9bae";
      return `<div class="msn-mini"><span>${short(m.id)}</span><span class="pill" style="color:${cc};background:${cc}1e">${m.status}</span><span class="t">${m.type.replace("_", " ")}</span></div>`;
    }).join("") || `<div class="empty" style="margin:10px">NO ACTIVE MISSIONS</div>`;
  }
}

function paintFleet(c) {
  if (showRegForm) return;
  const roster = $("roster"); if (!roster) return;
  $("fCount").textContent = c.snap.drones.length;
  if (!c.snap.drones.length) roster.innerHTML = world === "real"
    ? `<div class="empty">NO HARDWARE UNITS LINKED<br><br>The REAL world connects to physical drone hardware<br>via ESP32 / PX4 / ROS 2 uplinks.<br><br>Register a hardware unit with its network<br>configuration to begin receiving telemetry.<br><br><button class="btn sm" onclick="SS.showRegister()">+ REGISTER HARDWARE UNIT</button></div>`
    : `<div class="empty">NO UNITS<br><br><button class="btn sm" onclick="SS.showRegister()">+ REGISTER UNIT</button></div>`;
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
    <div class="k">MISSION</div><div class="val">${short(sel.mission) || "—"}</div>
    <div style="grid-column: 1 / -1; margin-top: 14px; display: flex; gap: 8px;">
      <button class="btn danger sm" style="flex:1" onclick="SS.faultUnit('${sel.id}')">FAULT UNIT</button>
      <button class="btn danger sm" style="flex:1" onclick="SS.confirmRemove('${sel.id}')">DECOMMISSION</button>
    </div>`;
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
    const cc = m.status === "PENDING" ? "#d4a24f" : m.status === "FAILED" ? "#cf5c6f" : "#8a9bae";
    const dur = m.patrol_duration_s ? ` · ${m.patrol_duration_s}s` : "";
    return `<div class="mission"><div>${short(m.id)} <span class="t">${m.type.replace("_", " ")}</span></div>
      <span class="pill" style="color:${cc};background:${cc}1e">${m.status}${m.attempts > 1 ? " ×" + m.attempts : ""}</span>
      <div class="who">unit ${m.drone || "—"} · target ${m.tx != null ? m.tx.toFixed(0) + "," + m.ty.toFixed(0) : "route"}${dur}</div></div>`;
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
  world = w; document.body.dataset.world = w; selectedId = null; showRegForm = false;
  SS.cancelMapMode();
  closeModal();
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
    else if (route === "overview") {
      const cv = $("miniCanvas"); if (cv) drawMap(c, cv, { mini: true });
      if (miniThree && $("miniTerrainMount")) { updateMini3D(c); miniThree.renderer.render(miniThree.scene, miniThree.camera); }
    }
    else if (route === "terrain" && three) { update3D(c); three.controls.update(); three.renderer.render(three.scene, three.camera); }
  } catch (e) { /* prevent one frame error from breaking loop */ }
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------- boot
connect("sim"); connect("real");
window.addEventListener("hashchange", go);
document.addEventListener("pointermove", (e) => {
  const m = e.target.closest ? e.target.closest(".world-switch button, .btn, .kpi, .panel") : null;
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
