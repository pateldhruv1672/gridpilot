
import * as THREE from 'three@0.160.0';
import * as echarts from 'echarts@5';

const C = { bg: '#070B13', p1: '#101826', p2: '#141E2D', line: 'rgba(110,150,190,0.16)', line2: 'rgba(110,150,190,0.28)', cyan: '#43D9F5', mint: '#4FE0A5', amber: '#FFBB55', red: '#FF596E', text: '#E6EEF7', mute: '#8A9BB0', dim: '#56677C' };
const MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
const SANS = "Inter, 'Geist', ui-sans-serif, system-ui, sans-serif";
const H = 0.3, LX = 9.6, LZ = 20.4, NX = 32, NZ = 68, X0 = 1.2, PITCH = 0.7, DEPTH = 1.2, RACK_X1 = 6.8;
const rowZ0 = (r) => (r % 2 === 0 ? 1.2 + (r >> 1) * 4.8 : 3.6 + (r >> 1) * 4.8);
const COLD = [0, 1, 2, 3].map(k => [2.4 + 4.8 * k, 3.6 + 4.8 * k]);
const HOT = [[0, 1.2], [4.8, 6.0], [9.6, 10.8], [14.4, 15.6], [19.2, 20.4]];
const rackPos = (i) => { const r = Math.floor(i / 8), c = i % 8; return [X0 + c * PITCH + PITCH / 2, rowZ0(r) + DEPTH / 2]; };
const ROWS = 'ABCDEFGH';
function cellKind(x, z) {
  for (const [a, b] of COLD) if (z >= a && z < b) return 1;
  for (const [a, b] of HOT) if (z >= a && z < b) return 2;
  return x >= X0 && x < RACK_X1 ? 3 : 0;
}
const KIND = new Uint8Array(NX * NZ);
for (let j = 0; j < NZ; j++) for (let i = 0; i < NX; i++) KIND[j * NX + i] = cellKind((i + 0.5) * H, (j + 0.5) * H);

function ramp(t) { // 0..1 -> rgb (icy blue -> cyan -> mint -> amber -> coral)
  const stops = [[0, [0.25, 0.55, 1.0]], [0.22, [0.26, 0.85, 0.96]], [0.42, [0.31, 0.88, 0.65]], [0.62, [1.0, 0.73, 0.33]], [0.82, [1.0, 0.45, 0.25]], [1, [1.0, 0.25, 0.35]]];
  t = Math.max(0, Math.min(1, t));
  for (let k = 1; k < stops.length; k++) if (t <= stops[k][0]) { const [a, ca] = stops[k - 1], [b, cb] = stops[k]; const f = (t - a) / (b - a); return [ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f, ca[2] + (cb[2] - ca[2]) * f]; }
  return stops[stops.length - 1][1];
}
const tempN = (T) => (T - 19) / 22;
function sample(F, x, z) {
  let fx = x / H - 0.5, fz = z / H - 0.5; fx = Math.max(0, Math.min(NX - 1.001, fx)); fz = Math.max(0, Math.min(NZ - 1.001, fz));
  const i = fx | 0, j = fz | 0, tx = fx - i, tz = fz - j, a = j * NX + i;
  return (F[a] * (1 - tx) + F[a + 1] * tx) * (1 - tz) + (F[a + NX] * (1 - tx) + F[a + NX + 1] * tx) * tz;
}
function statusColor(s) { return s === 'critical' ? C.red : s === 'elevated' ? C.amber : C.mint; }

function DataCenterScene({ snapRef, opts, selected, onSelect, camCmd }) {
  const wrap = React.useRef(null), labelsRef = React.useRef(null);
  const optsRef = React.useRef(opts); optsRef.current = opts;
  const selRef = React.useRef(selected); selRef.current = selected;
  const cmdRef = React.useRef(null);
  React.useEffect(() => { cmdRef.current = camCmd; }, [camCmd]);
  React.useEffect(() => {
    const el = wrap.current; if (!el) return;
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setClearColor(C.bg);
    el.appendChild(renderer.domElement);
    renderer.domElement.style.display = 'block'; renderer.domElement.style.touchAction = 'none';
    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(C.bg, 30, 60);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);
    scene.add(new THREE.HemisphereLight(0x9fd8ff, 0x0a0f18, 0.55));
    const dl = new THREE.DirectionalLight(0xcfe8ff, 0.9); dl.position.set(-6, 14, 8); scene.add(dl);
    const fill = new THREE.PointLight(0x43d9f5, 0.6, 30); fill.position.set(9, 4, 10); scene.add(fill);
    // floor + grid
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(LX + 6, LZ + 6), new THREE.MeshStandardMaterial({ color: 0x0b111c, roughness: 0.85, metalness: 0.2 }));
    floor.rotation.x = -Math.PI / 2; floor.position.set(LX / 2, -0.01, LZ / 2); scene.add(floor);
    const grid = new THREE.GridHelper(Math.max(LX, LZ) + 6, Math.round((Math.max(LX, LZ) + 6) / 0.6), 0x1d2a3d, 0x131c2a);
    grid.position.set(LX / 2, 0.001, LZ / 2); scene.add(grid);
    // heatmap texture
    const hm = new Uint8Array(NX * NZ * 4);
    const tex = new THREE.DataTexture(hm, NX, NZ, THREE.RGBAFormat); tex.magFilter = THREE.LinearFilter; tex.minFilter = THREE.LinearFilter; tex.needsUpdate = true;
    const heat = new THREE.Mesh(new THREE.PlaneGeometry(LX, LZ), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, opacity: 0.9 }));
    heat.rotation.x = -Math.PI / 2; heat.position.set(LX / 2, 0.012, LZ / 2); scene.add(heat);
    // aisle floor markings
    const aisleMat = new THREE.MeshBasicMaterial({ color: 0x43d9f5, transparent: true, opacity: 0.05, depthWrite: false });
    COLD.forEach(([a, b]) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(RACK_X1 - X0 + 2.2, b - a - 0.1), aisleMat); m.rotation.x = -Math.PI / 2; m.position.set((X0 + RACK_X1) / 2 + 1, 0.006, (a + b) / 2); scene.add(m); });
    // racks
    const rackGeo = new THREE.BoxGeometry(0.62, 2.0, 1.1);
    const rackMat = new THREE.MeshStandardMaterial({ color: 0x1a2433, metalness: 0.75, roughness: 0.38 });
    const racks = new THREE.InstancedMesh(rackGeo, rackMat, 64);
    const m4 = new THREE.Matrix4(), col = new THREE.Color();
    for (let i = 0; i < 64; i++) { const [x, z] = rackPos(i); m4.makeTranslation(x, 1.0, z); racks.setMatrixAt(i, m4); racks.setColorAt(i, col.set(0x1a2433)); }
    scene.add(racks);
    // rack edges (merged, per-vertex color)
    const eg = new THREE.EdgesGeometry(new THREE.BoxGeometry(0.64, 2.02, 1.12));
    const ep = eg.attributes.position.array, per = ep.length / 3;
    const edgePos = new Float32Array(per * 3 * 64), edgeCol = new Float32Array(per * 3 * 64);
    for (let i = 0; i < 64; i++) { const [x, z] = rackPos(i); for (let v = 0; v < per; v++) { edgePos[(i * per + v) * 3] = ep[v * 3] + x; edgePos[(i * per + v) * 3 + 1] = ep[v * 3 + 1] + 1.0; edgePos[(i * per + v) * 3 + 2] = ep[v * 3 + 2] + z; } }
    const edgeGeo = new THREE.BufferGeometry(); edgeGeo.setAttribute('position', new THREE.BufferAttribute(edgePos, 3)); edgeGeo.setAttribute('color', new THREE.BufferAttribute(edgeCol, 3));
    const edges = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }));
    scene.add(edges);
    // status LEDs on the cold-aisle face
    const leds = new THREE.InstancedMesh(new THREE.BoxGeometry(0.4, 0.05, 0.02), new THREE.MeshBasicMaterial({ color: 0xffffff }), 64);
    for (let i = 0; i < 64; i++) { const r = Math.floor(i / 8); const [x, z] = rackPos(i); const face = r % 2 === 0 ? z + 0.57 : z - 0.57; m4.makeTranslation(x, 1.75, face); leds.setMatrixAt(i, m4); leds.setColorAt(i, col.set(C.mint)); }
    scene.add(leds);
    // selection box
    const selBox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(0.76, 2.16, 1.26)), new THREE.LineBasicMaterial({ color: 0x43d9f5, transparent: true, opacity: 1 }));
    selBox.visible = false; scene.add(selBox);
    // cooling units
    const crah = [];
    for (let k = 0; k < 4; k++) {
      const g = new THREE.Mesh(new THREE.BoxGeometry(0.9, 2.3, 1.1), new THREE.MeshStandardMaterial({ color: 0x152131, metalness: 0.6, roughness: 0.4, emissive: new THREE.Color(0x43d9f5), emissiveIntensity: 0.15 }));
      g.position.set(9.0, 1.15, 3.0 + 4.8 * k); scene.add(g);
      const grille = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 1.6), new THREE.MeshBasicMaterial({ color: 0x43d9f5, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false }));
      grille.rotation.y = -Math.PI / 2; grille.position.set(8.54, 1.0, 3.0 + 4.8 * k); scene.add(grille);
      crah.push({ g, grille });
    }
    // overhead trays
    const trayMat = new THREE.LineBasicMaterial({ color: 0x2a3b52, transparent: true, opacity: 0.8 });
    for (let r = 0; r < 8; r++) { const z = rowZ0(r) + 0.6; const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(X0 - 0.4, 2.55, z), new THREE.Vector3(9.2, 2.55, z)]); scene.add(new THREE.Line(g, trayMat)); }
    // particles
    const NP = 7000;
    const pPos = new Float32Array(NP * 3), pCol = new Float32Array(NP * 3);
    const P = { x: new Float32Array(NP), z: new Float32Array(NP), y: new Float32Array(NP), age: new Float32Array(NP), life: new Float32Array(NP), cy: new Float32Array(NP), hy: new Float32Array(NP) };
    const spriteC = document.createElement('canvas'); spriteC.width = spriteC.height = 32; const sg = spriteC.getContext('2d');
    const grd = sg.createRadialGradient(16, 16, 0, 16, 16, 16); grd.addColorStop(0, 'rgba(255,255,255,1)'); grd.addColorStop(0.35, 'rgba(255,255,255,0.55)'); grd.addColorStop(1, 'rgba(255,255,255,0)');
    sg.fillStyle = grd; sg.fillRect(0, 0, 32, 32);
    const pGeo = new THREE.BufferGeometry(); pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3)); pGeo.setAttribute('color', new THREE.BufferAttribute(pCol, 3));
    const pMat = new THREE.PointsMaterial({ size: 0.11, map: new THREE.CanvasTexture(spriteC), vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true });
    const points = new THREE.Points(pGeo, pMat); scene.add(points);
    let field = null;
    function spawn(n, snap) {
      const sup = snap ? snap.cooling.map(c => Math.max(0.05, c.aisle_supply_m3s)) : [1, 1, 1, 1];
      const tot = sup.reduce((a, b) => a + b, 0); let r = Math.random() * tot, k = 0;
      while (k < 3 && r > sup[k]) { r -= sup[k]; k++; }
      const [a, b] = COLD[k];
      P.x[n] = 8.35 + Math.random() * 0.25; P.z[n] = a + 0.08 + Math.random() * (b - a - 0.16); P.cy[n] = 0.15 + Math.random() * 1.7; P.hy[n] = 1.3 + Math.random() * 1.3;
      P.y[n] = P.cy[n]; P.age[n] = 0; P.life[n] = 9 + Math.random() * 9;
    }
    for (let n = 0; n < NP; n++) { spawn(n, null); P.age[n] = Math.random() * P.life[n]; }
    // plumes
    const plumes = [];
    for (let k = 0; k < 6; k++) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1, 24, 1, true), new THREE.MeshBasicMaterial({ color: 0xff6a4d, transparent: true, opacity: 0.0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
      m.rotation.x = Math.PI; m.visible = false; scene.add(m); plumes.push(m);
    }
    // migration arcs
    const arcs = [];
    for (let k = 0; k < 6; k++) {
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(48 * 3), 3));
      const line = new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x43d9f5, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending }));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 12), new THREE.MeshBasicMaterial({ color: 0xbff4ff }));
      line.visible = dot.visible = false; scene.add(line); scene.add(dot); arcs.push({ line, dot });
    }
    // camera rig
    const cam = { theta: 0.62, phi: 0.92, radius: 25, target: new THREE.Vector3(LX / 2, 0, LZ / 2), goal: null };
    const defaults = { theta: 0.62, phi: 0.92, radius: 25, target: new THREE.Vector3(LX / 2, 0, LZ / 2) };
    function placeCam() { const { theta, phi, radius, target } = cam; camera.position.set(target.x + radius * Math.sin(phi) * Math.sin(theta), target.y + radius * Math.cos(phi), target.z + radius * Math.sin(phi) * Math.cos(theta)); camera.lookAt(target); }
    function goTo(g) { cam.goal = { from: { theta: cam.theta, phi: cam.phi, radius: cam.radius, target: cam.target.clone() }, to: g, t: 0 }; }
    let drag = null, downAt = null;
    const dom = renderer.domElement;
    const onDown = (e) => { dom.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey }; downAt = { x: e.clientX, y: e.clientY }; e.stopPropagation(); };
    const onMove = (e) => {
      if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY; cam.goal = null;
      if (drag.pan) { const s = cam.radius * 0.0016; const right = new THREE.Vector3(Math.cos(cam.theta), 0, -Math.sin(cam.theta)); const fwd = new THREE.Vector3(Math.sin(cam.theta), 0, Math.cos(cam.theta)); cam.target.addScaledVector(right, -dx * s).addScaledVector(fwd, -dy * s); }
      else { cam.theta -= dx * 0.006; cam.phi = Math.max(0.05, Math.min(1.45, cam.phi - dy * 0.005)); }
    };
    const raycaster = new THREE.Raycaster(); const ndc = new THREE.Vector2();
    const onUp = (e) => {
      if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) < 5) {
        const r = dom.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
        raycaster.setFromCamera(ndc, camera); const hit = raycaster.intersectObject(racks)[0];
        if (hit && hit.instanceId != null) onSelect(hit.instanceId); else onSelect(null);
      }
      drag = null; downAt = null;
    };
    const onWheel = (e) => { e.preventDefault(); e.stopPropagation(); cam.goal = null; cam.radius = Math.max(5, Math.min(55, cam.radius * (1 + Math.sign(e.deltaY) * 0.08))); };
    const onCtx = (e) => { e.preventDefault(); e.stopPropagation(); };
    dom.addEventListener('pointerdown', onDown); dom.addEventListener('pointermove', onMove); dom.addEventListener('pointerup', onUp);
    dom.addEventListener('wheel', onWheel, { passive: false }); dom.addEventListener('contextmenu', onCtx);
    // labels
    const labelEls = [];
    if (labelsRef.current) { labelsRef.current.innerHTML = ''; for (let i = 0; i < 64; i++) { const d = document.createElement('div'); d.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;white-space:nowrap;font:500 10px ' + MONO + ';letter-spacing:.04em;transform:translate(-50%,-100%);display:none'; labelsRef.current.appendChild(d); labelEls.push(d); } }
    const rowLabels = [];
    if (labelsRef.current) for (let r = 0; r < 8; r++) { const d = document.createElement('div'); d.textContent = 'ROW ' + ROWS[r]; d.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;font:600 9px ' + MONO + ';letter-spacing:.18em;color:#56677C;transform:translate(-50%,-50%)'; labelsRef.current.appendChild(d); rowLabels.push(d); }
    const unitLabels = [];
    if (labelsRef.current) for (let k = 0; k < 4; k++) { const d = document.createElement('div'); d.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;font:600 10px ' + MONO + ';letter-spacing:.08em;transform:translate(-50%,-100%);padding:2px 5px;border-radius:4px;background:rgba(7,11,19,.72);border:1px solid rgba(110,150,190,.25)'; labelsRef.current.appendChild(d); unitLabels.push(d); }
    const v3 = new THREE.Vector3();
    function project(x, y, z) { v3.set(x, y, z).project(camera); const r = dom.getBoundingClientRect(); return [(v3.x * 0.5 + 0.5) * r.width, (-v3.y * 0.5 + 0.5) * r.height, v3.z < 1]; }
    // resize
    const ro = new ResizeObserver(() => { const w = el.clientWidth, h = el.clientHeight; if (!w || !h) return; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); });
    ro.observe(el);
    let raf = 0, last = performance.now(), lastField = null, clock = 0;
    const loop = (now) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - last) / 1000); last = now; clock += dt;
      const o = optsRef.current; const snap = snapRef.current;
      // camera commands
      const cmd = cmdRef.current;
      if (cmd && cmd.n !== loop._cmdN) {
        loop._cmdN = cmd.n;
        if (cmd.kind === 'reset' || cmd.kind === 'iso') goTo({ ...defaults, target: defaults.target.clone() });
        if (cmd.kind === 'top') goTo({ theta: 0.0001, phi: 0.06, radius: 27, target: defaults.target.clone() });
        if (cmd.kind === 'focus' && selRef.current != null) { const [x, z] = rackPos(selRef.current); goTo({ theta: cam.theta, phi: 0.85, radius: 9, target: new THREE.Vector3(x, 1, z) }); }
      }
      if (cam.goal) { const g = cam.goal; g.t = Math.min(1, g.t + dt / (reduced ? 0.01 : 0.7)); const e = 1 - Math.pow(1 - g.t, 3); cam.theta = g.from.theta + (g.to.theta - g.from.theta) * e; cam.phi = g.from.phi + (g.to.phi - g.from.phi) * e; cam.radius = g.from.radius + (g.to.radius - g.from.radius) * e; cam.target.lerpVectors(g.from.target, g.to.target, e); if (g.t >= 1) cam.goal = null; }
      placeCam();
      if (snap && snap.field && snap.field !== lastField) {
        lastField = snap.field; field = { T: Float32Array.from(snap.field.T), u: Float32Array.from(snap.field.u), w: Float32Array.from(snap.field.w) };
      }
      const mode = o.mode;
      // heatmap
      if (field) {
        const show = mode === 'thermal' ? 1 : mode === 'combined' ? 0.75 : mode === 'airflow' ? 0.18 : 0.12;
        for (let r = 0; r < NZ; r++) { const j = NZ - 1 - r; for (let i = 0; i < NX; i++) { const idx = j * NX + i; const T = field.T[idx]; const [cr, cg, cb] = ramp(tempN(T)); const q = (r * NX + i) * 4; const kind = KIND[idx]; const a = kind === 3 ? 0.25 : 0.78; hm[q] = cr * 255; hm[q + 1] = cg * 255; hm[q + 2] = cb * 255; hm[q + 3] = Math.max(0, Math.min(255, a * show * 255 * (0.35 + 0.65 * Math.max(0, Math.min(1, tempN(T) * 1.4))))); } }
        tex.needsUpdate = true;
      }
      // racks, edges, leds
      if (snap) {
        const sel = selRef.current;
        for (let i = 0; i < 64; i++) {
          const rk = snap.racks[i];
          let base;
          if (mode === 'power') base = ramp((rk.power_kw - 3) / 10);
          else if (mode === 'workload') base = ramp(rk.utilization * 0.85);
          else base = null;
          const sc = new THREE.Color(statusColor(rk.status));
          const pulse = rk.status === 'critical' && !reduced ? 0.55 + 0.45 * Math.sin(clock * 6) : 1;
          let ec = base ? new THREE.Color(base[0], base[1], base[2]) : (rk.status === 'normal' ? new THREE.Color(C.cyan).multiplyScalar(0.55) : sc);
          if (mode === 'thermal' || mode === 'combined') { const t = ramp((rk.gpu_temp_c - 55) / 40); ec = rk.status === 'normal' ? new THREE.Color(t[0], t[1], t[2]).multiplyScalar(0.6) : sc; }
          ec.multiplyScalar(pulse);
          for (let v = 0; v < per; v++) { const q = (i * per + v) * 3; edgeCol[q] = ec.r; edgeCol[q + 1] = ec.g; edgeCol[q + 2] = ec.b; }
          racks.setColorAt(i, col.set(base ? new THREE.Color(base[0] * 0.35, base[1] * 0.35, base[2] * 0.35) : new THREE.Color(rk.status === 'critical' ? 0x3a1b22 : rk.status === 'elevated' ? 0x2e2618 : 0x1a2433)));
          leds.setColorAt(i, col.set(sc).multiplyScalar(pulse));
        }
        edgeGeo.attributes.color.needsUpdate = true; racks.instanceColor.needsUpdate = true; leds.instanceColor.needsUpdate = true;
        if (sel != null) { const [x, z] = rackPos(sel); selBox.position.set(x, 1.0, z); selBox.visible = true; selBox.material.opacity = 0.6 + 0.4 * Math.sin(clock * 3); } else selBox.visible = false;
        snap.cooling.forEach((u, k) => { const bad = 1 - u.effectiveness; crah[k].g.material.emissive.set(bad > 0.4 ? C.red : bad > 0.15 ? C.amber : C.cyan); crah[k].g.material.emissiveIntensity = 0.12 + 0.25 * (bad > 0.15 ? (reduced ? 1 : 0.6 + 0.4 * Math.sin(clock * 4)) : 0.3); crah[k].grille.material.color.set(bad > 0.4 ? C.red : bad > 0.15 ? C.amber : C.cyan); crah[k].grille.material.opacity = 0.12 + 0.35 * u.flow_factor * u.fan_pct / 100; });
        // plumes on hot racks
        const hotR = snap.racks.map((r, i) => [r.gpu_temp_c, i]).filter(([t]) => t > 82).sort((a, b) => b[0] - a[0]).slice(0, 6);
        plumes.forEach((m, k) => { const h = hotR[k]; if (!h || mode === 'power' || mode === 'workload') { m.visible = false; return; } const sev = Math.min(1, (h[0] - 82) / 12); const [x, z] = rackPos(h[1]); m.visible = true; const wob = reduced ? 1 : 1 + 0.08 * Math.sin(clock * 3 + k); m.scale.set(0.8 + sev * 0.6, 1.2 + sev * 2.4 * wob, 0.8 + sev * 0.6); m.position.set(x, 2.0 + (1.2 + sev * 2.4) / 2, z); m.material.opacity = 0.08 + 0.22 * sev; });
        // migrations
        const migs = snap.migrations || [];
        arcs.forEach((a, k) => {
          const mg = migs[k]; if (!mg || !(mode === 'workload' || mode === 'combined' || mode === 'thermal' || mode === 'airflow')) { a.line.visible = a.dot.visible = false; return; }
          const ri = (id) => parseInt(id.slice(1), 10) - 1; const [x1, z1] = rackPos(ri(mg.from)); const [x2, z2] = rackPos(ri(mg.to));
          const arr = a.line.geometry.attributes.position.array; const mx = (x1 + x2) / 2, mz = (z1 + z2) / 2, top = 3.2 + Math.hypot(x2 - x1, z2 - z1) * 0.12;
          const pt = (t) => { const u1 = 1 - t; return [u1 * u1 * x1 + 2 * u1 * t * mx + t * t * x2, u1 * u1 * 2.05 + 2 * u1 * t * top + t * t * 2.05, u1 * u1 * z1 + 2 * u1 * t * mz + t * t * z2]; };
          for (let s = 0; s < 48; s++) { const p = pt(s / 47); arr[s * 3] = p[0]; arr[s * 3 + 1] = p[1]; arr[s * 3 + 2] = p[2]; }
          a.line.geometry.attributes.position.needsUpdate = true; a.line.visible = true;
          const f = ((clock * 0.6) % 1); const p = pt(f); a.dot.position.set(p[0], p[1], p[2]); a.dot.visible = !reduced; a.line.material.opacity = 0.35 + 0.6 * (1 - mg.progress);
        });
      }
      // particles
      const showP = mode === 'airflow' || mode === 'combined' || mode === 'thermal';
      points.visible = showP && !!field;
      const active = Math.floor(NP * (reduced ? Math.min(o.density, 0.35) : o.density) * (mode === 'combined' ? 0.6 : mode === 'thermal' ? 0.35 : 1));
      if (field && points.visible) {
        const adv = o.paused ? 0 : dt * o.intensity * 0.9;
        for (let n = 0; n < NP; n++) {
          if (n >= active) { pPos[n * 3 + 1] = -50; continue; }
          let x = P.x[n], z = P.z[n];
          if (adv > 0) {
            const u = sample(field.u, x, z), w = sample(field.w, x, z);
            x += u * adv; z += w * adv; P.age[n] += dt * o.intensity;
            const ci = Math.max(0, Math.min(NX - 1, (x / H) | 0)), cj = Math.max(0, Math.min(NZ - 1, (z / H) | 0)); const kind = KIND[cj * NX + ci];
            const ty = kind === 2 ? P.hy[n] : kind === 3 ? Math.min(1.9, P.cy[n] + 0.2) : P.cy[n];
            P.y[n] += (ty - P.y[n]) * Math.min(1, dt * 1.2);
            if (P.age[n] > P.life[n] || x < -0.3 || x > LX || z < 0 || z > LZ || (kind === 2 && x > 8.3)) { spawn(n, snap); x = P.x[n]; z = P.z[n]; }
            P.x[n] = x; P.z[n] = z;
          }
          const T = sample(field.T, x, z); const c3 = ramp(tempN(T));
          const fade = Math.min(1, P.age[n] * 1.5) * Math.min(1, (P.life[n] - P.age[n]) * 0.8);
          pPos[n * 3] = x; pPos[n * 3 + 1] = P.y[n]; pPos[n * 3 + 2] = z;
          pCol[n * 3] = c3[0] * fade; pCol[n * 3 + 1] = c3[1] * fade; pCol[n * 3 + 2] = c3[2] * fade;
        }
        pGeo.attributes.position.needsUpdate = true; pGeo.attributes.color.needsUpdate = true;
      }
      renderer.render(scene, camera);
      // labels
      if (snap && labelEls.length) {
        const sel = selRef.current;
        const hotSet = new Set(snap.racks.map((r, k) => [r.status === 'critical' ? 1000 + r.gpu_temp_c : r.status === 'elevated' ? r.gpu_temp_c : -1, k]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 4).map(x => x[1]));
        for (let i = 0; i < 64; i++) {
          const rk = snap.racks[i]; const d = labelEls[i]; const showIt = o.labels || i === sel || hotSet.has(i);
          if (!showIt) { d.style.display = 'none'; continue; }
          const [x, z] = rackPos(i); const [sx, sy, vis] = project(x, 2.25, z);
          if (!vis) { d.style.display = 'none'; continue; }
          d.style.display = 'block'; d.style.transform = 'translate(' + sx + 'px,' + sy + 'px) translate(-50%,-100%)';
          const strong = i === sel || hotSet.has(i);
          d.style.color = rk.status === 'critical' ? C.red : rk.status === 'elevated' ? C.amber : (i === sel ? C.cyan : '#7f93ab');
          d.style.padding = strong ? '2px 6px' : '0'; d.style.borderRadius = '4px'; d.style.background = strong ? 'rgba(7,11,19,.78)' : 'transparent'; d.style.border = strong ? '1px solid ' + (rk.status === 'critical' ? 'rgba(255,89,110,.5)' : rk.status === 'elevated' ? 'rgba(255,187,85,.45)' : 'rgba(67,217,245,.45)') : 'none';
          d.textContent = strong ? rk.rack_id + '  ' + rk.gpu_temp_c.toFixed(1) + '°C' : rk.rack_id;
        }
        rowLabels.forEach((d, r) => { const [sx, sy, vis] = project(X0 - 0.55, 0.05, rowZ0(r) + 0.6); d.style.display = vis ? 'block' : 'none'; d.style.transform = 'translate(' + sx + 'px,' + sy + 'px) translate(-50%,-50%)'; });
        unitLabels.forEach((d, k) => { const u = snap.cooling[k]; const [sx, sy, vis] = project(9.0, 2.5, 3.0 + 4.8 * k); d.style.display = vis ? 'block' : 'none'; d.style.transform = 'translate(' + sx + 'px,' + sy + 'px) translate(-50%,-100%)'; const bad = 1 - u.effectiveness; d.style.color = bad > 0.4 ? C.red : bad > 0.15 ? C.amber : C.cyan; d.textContent = u.unit_id + ' ' + Math.round(u.effectiveness * 100) + '% · ' + u.supply_temp_c.toFixed(1) + '°C'; });
      }
    };
    raf = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); dom.removeEventListener('pointerdown', onDown); dom.removeEventListener('pointermove', onMove); dom.removeEventListener('pointerup', onUp); dom.removeEventListener('wheel', onWheel); dom.removeEventListener('contextmenu', onCtx); renderer.dispose(); scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m.map) m.map.dispose(); m.dispose(); }); }); if (dom.parentNode) dom.parentNode.removeChild(dom); };
  }, []);
  return (
    <div className="relative w-full h-full overflow-hidden" style={{ background: C.bg }}>
      <div ref={wrap} className="absolute inset-0" />
      <div ref={labelsRef} className="absolute inset-0 pointer-events-none" style={{ overflow: 'hidden' }} />
    </div>
  );
}

function EChart({ option, height = 120 }) {
  const ref = React.useRef(null), inst = React.useRef(null);
  React.useEffect(() => { inst.current = echarts.init(ref.current, null, { renderer: 'canvas' }); const ro = new ResizeObserver(() => inst.current && inst.current.resize()); ro.observe(ref.current); return () => { ro.disconnect(); inst.current && inst.current.dispose(); }; }, []);
  React.useEffect(() => { if (inst.current && option) inst.current.setOption(option, { notMerge: false, lazyUpdate: true }); }, [option]);
  return <div ref={ref} style={{ width: '100%', height }} />;
}
const axisStyle = { axisLine: { lineStyle: { color: '#24334a' } }, axisLabel: { color: '#6f8299', fontFamily: MONO, fontSize: 9 }, splitLine: { lineStyle: { color: 'rgba(110,150,190,0.08)' } } };
function lineOpt(series, { yName, min, max, marks } = {}) {
  return { animation: false, grid: { left: 34, right: 10, top: 18, bottom: 18 }, tooltip: { trigger: 'axis', backgroundColor: '#0d1522', borderColor: '#24334a', textStyle: { color: '#E6EEF7', fontFamily: MONO, fontSize: 10 } },
    legend: { show: series.length > 1, top: 0, right: 4, itemWidth: 10, itemHeight: 2, textStyle: { color: '#8A9BB0', fontFamily: MONO, fontSize: 9 } },
    xAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v) => Math.round(v) + 's' }, min: 'dataMin', max: 'dataMax' },
    yAxis: { type: 'value', name: yName, nameTextStyle: { color: '#6f8299', fontSize: 9 }, scale: true, min, max, ...axisStyle },
    series: series.map(s => ({ type: 'line', name: s.name, data: s.data, showSymbol: false, smooth: true, lineStyle: { width: 1.6, color: s.color }, itemStyle: { color: s.color }, areaStyle: s.area ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: s.color + '55' }, { offset: 1, color: s.color + '00' }]) } : undefined,
      markLine: s.mark ? { silent: true, symbol: 'none', label: { color: s.mark.color, fontSize: 9, fontFamily: MONO, formatter: s.mark.label }, lineStyle: { color: s.mark.color, type: 'dashed', width: 1 }, data: [{ yAxis: s.mark.y }] } : undefined })) };
}

const BRIEF = "Create a reusable agent named \"GRIDPILOT Remediation Agent\" with exactly ONE input field, named `request` (long text).\n\nOn every run:\n1. Parse `request` as JSON. It contains task, incident_id, gridpilot_api (a base URL), tool_token, and task-specific fields (operator_suggestion, deterministic_evaluation, failure, rejection, approved_plan, invoice_id).\n2. Tools are HTTPS endpoints. POST JSON to {gridpilot_api}/{tool} with headers `X-GridPilot-Tool-Token: {tool_token}` and `Content-Type: application/json`. GET {gridpilot_api}/manifest lists every tool and the action schema. Call them with Python requests or curl from your sandbox. Never invent a tool result; if a call fails, report the error.\n3. Playbooks by task:\n- diagnose_and_plan: call get_incident_context, get_telemetry (incident rack and its neighbors), get_capacity and list_workloads (incident rack). Diagnose the cause from the evidence. Build 2 to 4 candidate action sets (GPU throttle, migrating workloads to healthy racks with free GPUs, cooling fan changes, combinations) and call evaluate_plan for each. Compare recovery time, incremental cost, SLA exposure and policy outcome. Then call propose_plan once with the best actions plus diagnosis, evidence, candidate_actions, expected_thermal_outcomes, expected_sla_risks, confidence (0 to 1) and reasoning_summary. If the policy decision is AUTO_ALLOWED, call request_execution for that plan version.\n- evaluate_alternative: read operator_suggestion and deterministic_evaluation, call get_incident_context, evaluate the operator's idea and at least one refinement with evaluate_plan, then call propose_plan with the best option. In reasoning_summary, say how the operator's suggestion changed the plan.\n- replan_after_failure: read failure or rejection, call get_incident_context and get_capacity, avoid failed targets, evaluate options and propose a revised plan.\n- execute_approved_plan: call get_incident_context, confirm the approval for approved_plan, call request_execution with exactly that plan_id and plan_version, then submit_report with kind execution_note.\n- post_incident_summary: call get_incident_context, get_reconciliation and get_cost_comparison, then submit_report with kind post_incident_summary (timeline, root cause, actions, MTTR, incurred costs versus conditional SLA exposure, invoice reconciliation).\nSpecial cases: when a case needs deeper expertise than these playbooks (an unexplained hardware signature, no feasible placement, an invoice that does not reconcile, protected customers at risk), call request_specialist {kind, question, mission}. kind is thermal_forensics, capacity_planner, finops_auditor, sla_guardian, or a new short name; a new kind also needs mission (what the specialist must work out). GRIDPILOT creates that CREAO agent on the fly and runs it. Specialist results appear in get_incident_context under specialists and in request.specialist_findings; use them, and cite them in reasoning_summary.\n4. Rules: you can never approve anything; the backend enforces policy and human approval. Use only rack, workload and unit IDs returned by the tools. Actions: migrate_workload {workload, to_rack}, set_gpu_power_limit {rack, watts 300 to 450}, set_cooling_fan {unit, fan_pct 60 to 130}.\n5. Final answer: one JSON object with keys incident_id, diagnosis, evidence, candidate_actions, expected_thermal_outcomes, expected_sla_risks, requested_action, confidence, reasoning_summary, requires_approval, plus plan_id and plan_version if you proposed a plan.";
const STEPS = [
  ['DETECTED', 'Detected', ['DETECTED']], ['DIAGNOSING', 'CREAO diagnosing', ['TRIAGING']], ['FINANCE', 'Financial evaluation', ['PLAN_PROPOSED']],
  ['PLAN', 'Plan ready · policy', ['POLICY_CHECKED']], ['HUMAN', 'Awaiting human', ['AWAITING_APPROVAL']], ['APPROVED', 'Approved', ['APPROVED']],
  ['EXEC', 'Execution', ['EXECUTING']], ['VALID', 'Validation', ['VALIDATING']], ['RECOVERY', 'Recovery', ['MONITORING_RECOVERY', 'RESOLVED', 'FINANCIAL_RECONCILIATION', 'CLOSED']],
];
const ORDER = ['DETECTED', 'TRIAGING', 'PLAN_PROPOSED', 'POLICY_CHECKED', 'AWAITING_APPROVAL', 'APPROVED', 'EXECUTING', 'VALIDATING', 'MONITORING_RECOVERY', 'RESOLVED', 'FINANCIAL_RECONCILIATION', 'CLOSED'];
const fmt$ = (v) => v == null ? '—' : (Number(v) < -0.004 ? '-$' : '$') + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtT = (s) => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? String(h).padStart(2, '0') + ':' : '') + String(m).padStart(2, '0') + ':' + String(x).padStart(2, '0'); };
const ago = (ts) => { const d = (Date.now() / 1000 - ts); return d < 60 ? Math.round(d) + 's ago' : d < 3600 ? Math.round(d / 60) + 'm ago' : Math.round(d / 3600) + 'h ago'; };
function actionText(a) {
  if (a.type === 'migrate_workload') return 'Migrate ' + a.workload + '  ' + a.from_rack + ' → ' + a.to_rack;
  if (a.type === 'set_gpu_power_limit') return 'GPU power limit ' + a.rack + '  ' + (a.from_watts || '?') + ' → ' + a.watts + ' W';
  if (a.type === 'set_cooling_fan') return 'Fan ' + a.unit + '  ' + (a.from_pct || '?') + ' → ' + a.fan_pct + '%';
  return a.type;
}

function Label({ children, color }) { return <div style={{ font: '600 9.5px ' + MONO, letterSpacing: '0.16em', textTransform: 'uppercase', color: color || C.dim }}>{children}</div>; }
function Btn({ children, onClick, tone = 'ghost', disabled, title, small }) {
  const tones = { primary: { background: C.cyan, color: '#04121a', border: '1px solid ' + C.cyan }, danger: { background: 'transparent', color: C.red, border: '1px solid rgba(255,89,110,.45)' },
    ghost: { background: 'rgba(255,255,255,0.02)', color: C.text, border: '1px solid ' + C.line2 }, mint: { background: C.mint, color: '#03140c', border: '1px solid ' + C.mint } };
  return <button title={title} disabled={disabled} onClick={onClick} className="rounded-full transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
    style={{ ...tones[tone], opacity: disabled ? 0.38 : 1, cursor: disabled ? 'not-allowed' : 'pointer', font: '600 ' + (small ? '10.5px ' : '11.5px ') + SANS, padding: small ? '4px 10px' : '6px 13px', whiteSpace: 'nowrap' }}>{children}</button>;
}
function Panel({ children, style, className = '' }) { return <div className={className} style={{ background: C.p1, border: '1px solid ' + C.line, borderRadius: 10, ...style }}>{children}</div>; }
function Modal({ title, onClose, children, wide }) {
  return <div className="absolute inset-0 z-40 flex items-center justify-center p-6" style={{ background: 'rgba(3,6,12,0.7)' }} onClick={onClose}>
    <div onClick={e => e.stopPropagation()} className="flex flex-col" style={{ width: wide ? 'min(1000px,96%)' : 'min(640px,96%)', maxHeight: '88%', background: C.p2, border: '1px solid ' + C.line2, borderRadius: 12, boxShadow: '0 30px 80px rgba(0,0,0,.5)' }}>
      <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}><div style={{ font: '600 13px ' + SANS, color: C.text }}>{title}</div><button onClick={onClose} style={{ color: C.mute, font: '500 18px ' + SANS }} aria-label="Close">×</button></div>
      <div className="overflow-auto p-4" style={{ color: C.text }}>{children}</div>
    </div></div>;
}
function Diff({ text }) {
  if (!text) return <div style={{ color: C.mute, font: '12px ' + MONO }}>No diff.</div>;
  return <pre style={{ font: '11px/1.55 ' + MONO, margin: 0, whiteSpace: 'pre-wrap' }}>{text.split('\n').map((l, i) => <div key={i} style={{ color: l.startsWith('+') && !l.startsWith('+++') ? C.mint : l.startsWith('-') && !l.startsWith('---') ? C.red : l.startsWith('@@') ? C.cyan : l.startsWith('diff') ? C.amber : '#9fb0c4', background: l.startsWith('+') && !l.startsWith('+++') ? 'rgba(79,224,165,.06)' : l.startsWith('-') && !l.startsWith('---') ? 'rgba(255,89,110,.06)' : 'transparent' }}>{l || ' '}</div>)}</pre>;
}

function useGridpilot() {
  const { url, wsUrl, error } = useIngressUrl('gridpilot', { ttlSeconds: 7 * 86400 });
  const snapRef = React.useRef(null);
  const [tickN, setTickN] = React.useState(0);
  const [state, setState] = React.useState(null);
  const [conn, setConn] = React.useState('connecting');
  const urlRef = React.useRef(url); urlRef.current = url;
  const api = React.useCallback(async (path, method = 'GET', body) => {
    if (!urlRef.current) throw new Error('backend not reachable yet');
    const tok = await FS.auth.currentUser.getIdToken();
    const r = await fetch(urlRef.current + path, { method, headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.message || j.error || ('HTTP ' + r.status));
    return j;
  }, []);
  React.useEffect(() => {
    if (!url || !wsUrl) return;
    let ws = null, dead = false, retry = 0, timer = null;
    const open = async () => {
      try {
        setConn('connecting');
        const { ticket } = await api('/api/ws-ticket');
        if (dead) return;
        ws = new WebSocket(wsUrl + '/ws?ticket=' + encodeURIComponent(ticket));
        ws.onopen = () => { retry = 0; setConn('live'); };
        ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.type === 'tick') { snapRef.current = m.snap; setTickN(n => n + 1); } else if (m.type === 'state') setState(m.state); };
        ws.onclose = () => { if (dead) return; setConn('reconnecting'); timer = setTimeout(open, Math.min(8000, 800 * 2 ** retry++)); };
      } catch (e) { setConn('error: ' + e.message); timer = setTimeout(open, 4000); }
    };
    open();
    return () => { dead = true; clearTimeout(timer); if (ws) ws.close(); };
  }, [url, wsUrl, api]);
  return { url, error, snapRef, tickN, state, conn, api };
}

function Metric({ label, value, unit, sub, tone }) {
  return <div className="flex-1 min-w-[150px] px-4 py-2.5" style={{ borderRight: '1px solid ' + C.line }}>
    <Label>{label}</Label>
    <div className="flex items-baseline gap-1.5 mt-1 gp-nw"><span style={{ font: '500 22px ' + MONO, color: tone || C.text, letterSpacing: '-0.02em' }}>{value}</span><span style={{ font: '500 11px ' + MONO, color: C.mute }}>{unit}</span></div>
    <div className="truncate" style={{ font: '10.5px ' + MONO, color: C.dim, marginTop: 1 }} title={sub}>{sub}</div>
  </div>;
}

function IncidentPanel({ g, setModal, toast }) {
  const st = g.state; const inc = st && st.incident; const snap = g.snapRef.current;
  const [alt, setAlt] = React.useState(''); const [busy, setBusy] = React.useState(null); const [code, setCode] = React.useState(null);
  const run = async (key, fn) => { setBusy(key); try { const r = await fn(); if (r && r.message) toast(r.message, r.ok === false ? 'err' : 'ok'); } catch (e) { toast(e.message, 'err'); } setBusy(null); };
  const apr = inc && inc.approvals && inc.approvals.slice(-1)[0];
  React.useEffect(() => { let t; const poll = async () => { if (inc && apr && apr.status === 'code_issued') { try { setCode(await g.api('/api/incidents/' + inc.id + '/approval-code')); } catch (e) { } } else setCode(null); }; poll(); t = setInterval(poll, 3000); return () => clearInterval(t); }, [inc && inc.id, apr && apr.status]);
  if (!inc) return <div className="p-5 flex flex-col gap-3">
    <Label color={C.mint}>All systems nominal</Label>
    <div style={{ font: '500 15px ' + SANS, color: C.text }}>No active incident</div>
    <div style={{ font: '12px/1.55 ' + SANS, color: C.mute }}>Deterministic anomaly detection watches every rack each tick. An incident opens on its own when a GPU hotspot stays above {st ? st.thresholds.gpu_critical_c : 88}°C for 3 consecutive ticks. Use the demo controls to degrade cooling unit C02.</div>
  </div>;
  const plan = inc.plans && inc.plans.slice(-1)[0];
  const rk = snap && snap.racks.find(r => r.rack_id === inc.rack);
  const idx = ORDER.indexOf(inc.state);
  const failed = (inc.timeline || []).filter(t => ['ROLLING_BACK', 'REPLAN', 'DENIED', 'ESCALATED'].includes(t.state));
  const runs = inc.agent_runs || [];
  const lastRun = runs.slice(-1)[0];
  const exec = (inc.executions || []).slice(-1)[0];
  const call = (inc.calls || []).slice(-1)[0];
  const exposure = inc.sla_exposure_now;
  const sevColor = inc.state === 'CLOSED' ? C.mint : C.red;
  return <div className="flex flex-col">
    <div className="px-4 pt-4 pb-3" style={{ borderBottom: '1px solid ' + C.line }}>
      <div className="flex items-center gap-2"><span className={inc.state !== 'CLOSED' ? 'animate-pulse' : ''} style={{ width: 7, height: 7, borderRadius: 9, background: sevColor, boxShadow: '0 0 10px ' + sevColor }} />
        <span style={{ font: '600 11px ' + MONO, letterSpacing: '0.12em', color: sevColor }}>{inc.severity} · {inc.id} · {inc.title}</span></div>
      <div className="grid grid-cols-3 gap-y-2 gap-x-3 mt-3">
        {[['Rack', inc.rack], ['GPU now', rk ? rk.gpu_temp_c.toFixed(1) + '°C' : '—'], ['Inlet now', rk ? rk.inlet_temp_c.toFixed(1) + '°C' : '—'], ['Detected', 't+' + Math.round(inc.detected_sim_t) + 's'], ['State', inc.state.replace(/_/g, ' ')], ['Exposure rate', exposure ? fmt$(exposure.usd_per_min_if_sustained) + '/min' : '—']].map(([k, v]) =>
          <div key={k}><Label>{k}</Label><div style={{ font: '500 12px ' + MONO, color: C.text, marginTop: 2 }}>{v}</div></div>)}
      </div>
      <div style={{ font: '11px/1.5 ' + SANS, color: C.mute, marginTop: 10 }}>{inc.probable_cause}</div>
    </div>
    <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}>
      <Label>Agent execution</Label>
      <div className="mt-2 flex flex-col">
        {STEPS.map(([key, label, states], i) => {
          const pos = Math.min(...states.map(s => ORDER.indexOf(s)));
          const done = idx > Math.max(...states.map(s => ORDER.indexOf(s))) || inc.state === 'CLOSED';
          const cur = states.includes(inc.state) && inc.state !== 'CLOSED';
          const color = done ? C.mint : cur ? C.cyan : C.dim;
          let note = '';
          if (key === 'DIAGNOSING' && lastRun) note = 'CREAO ' + lastRun.status + (lastRun.provider_run_id ? ' · run ' + lastRun.provider_run_id.slice(0, 8) : '');
          if (key === 'HUMAN' && call) note = 'call ' + call.status;
          if (key === 'RECOVERY' && inc.mttr_sim_s) note = 'MTTR ' + inc.mttr_sim_s + 's sim';
          return <div key={key} className="flex items-center gap-2.5" style={{ height: 22 }}>
            <span style={{ width: 9, height: 9, borderRadius: 9, border: '1.5px solid ' + color, background: done ? color : 'transparent', boxShadow: cur ? '0 0 0 4px rgba(67,217,245,.12)' : 'none' }} className={cur ? 'animate-pulse' : ''} />
            <span style={{ font: (cur ? '600 ' : '500 ') + '11.5px ' + SANS, color: done || cur ? C.text : C.dim }}>{label}</span>
            <span className="ml-auto truncate" style={{ font: '10px ' + MONO, color: C.dim, maxWidth: 170 }}>{note}</span>
          </div>;
        })}
        {failed.map((f, i) => <div key={i} className="flex items-center gap-2.5" style={{ height: 22 }}><span style={{ width: 9, height: 9, borderRadius: 2, background: C.red }} /><span style={{ font: '500 11.5px ' + SANS, color: C.red }}>{f.state.replace(/_/g, ' ')}</span><span className="ml-auto truncate" style={{ font: '10px ' + MONO, color: C.dim, maxWidth: 190 }} title={f.reason}>{f.reason}</span></div>)}
      </div>
      {lastRun && (lastRun.status === 'disconnected' || lastRun.status === 'failed') && <div className="mt-2 p-2 rounded" style={{ background: 'rgba(255,187,85,.07)', border: '1px solid rgba(255,187,85,.3)', font: '11px/1.45 ' + SANS, color: C.amber }}>
        CREAO {lastRun.status}: {(lastRun.data && lastRun.data.error) || 'see Agent Operations'}. No diagnosis is shown until a real CREAO run completes.
        <div className="mt-1.5"><Btn small onClick={() => run('retry', () => g.api('/api/incidents/' + inc.id + '/retry-agent', 'POST', {}))} disabled={busy}>Retry CREAO run</Btn></div></div>}
    </div>
    {plan && <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}>
      <div className="flex items-center justify-between"><Label color={C.cyan}>Remediation proposal</Label><span style={{ font: '600 10px ' + MONO, color: C.mute }}>{plan.plan_id} · v{plan.version} · {plan.status.replace('_', ' ')}</span></div>
      <div style={{ font: '10px ' + MONO, color: C.dim, marginTop: 3 }}>by {plan.author}</div>
      <div className="mt-2 flex flex-col gap-1">{plan.actions.map((a, i) => <div key={i} style={{ font: '11.5px ' + MONO, color: C.text }}>› {actionText(a)}</div>)}</div>
      <div className="grid grid-cols-2 gap-2 mt-3">
        {[['Forecast recovery', plan.forecast.recover_s != null ? '≈' + plan.forecast.recover_s + 's' : 'not within 10 min', plan.forecast.recover_s != null ? C.mint : C.red],
          ['End GPU (fcst)', plan.forecast.final_gpu_c + '°C', C.text], ['Incremental cost', fmt$(plan.finance.incremental_cost_usd), C.text],
          ['SLA exposure', fmt$(plan.finance.sla_exposure_usd) + ' vs ' + fmt$(plan.baseline.sla_exposure_usd) + ' idle', C.text], ['Operational risk', plan.operational_risk, plan.operational_risk === 'low' ? C.mint : C.amber],
          ['Policy', plan.policy.decision.replace('_', ' '), plan.policy.decision === 'AUTO_ALLOWED' ? C.mint : plan.policy.decision === 'FORBIDDEN' ? C.red : C.amber]].concat(st.desk && st.desk.budgets['5200'] ? [['Burst budget after', fmt$(st.desk.budgets['5200'].budget - st.desk.budgets['5200'].actual - plan.finance.incremental_cost_usd) + ' of ' + fmt$(st.desk.budgets['5200'].budget), (st.desk.budgets['5200'].budget - st.desk.budgets['5200'].actual - plan.finance.incremental_cost_usd) < 0 ? C.red : C.text]] : []).map(([k, v, c]) =>
          <div key={k} className="px-2.5 py-2 rounded" style={{ background: C.p2, border: '1px solid ' + C.line }}><Label>{k}</Label><div style={{ font: '500 12px ' + MONO, color: c, marginTop: 3 }}>{v}</div></div>)}
      </div>
      <div className="mt-2" style={{ font: '10.5px/1.5 ' + SANS, color: C.mute }}>{plan.policy.plan_rules.concat(plan.policy.actions).filter(r => r.decision !== 'AUTO_ALLOWED').map(r => r.rule).join(' · ')}</div>
      {plan.meta && plan.meta.reasoning_summary && <div className="mt-2 p-2.5 rounded" style={{ background: 'rgba(67,217,245,.05)', border: '1px solid rgba(67,217,245,.18)', font: '11.5px/1.5 ' + SANS, color: '#c7d6e6' }}><Label color={C.cyan}>CREAO reasoning</Label><div className="mt-1">{plan.meta.reasoning_summary}</div>{plan.meta.confidence != null && <div style={{ font: '10px ' + MONO, color: C.mute, marginTop: 4 }}>confidence {plan.meta.confidence}</div>}</div>}
      {code && code.code && <div className="mt-3 p-3 rounded text-center" style={{ background: 'rgba(79,224,165,.07)', border: '1px solid rgba(79,224,165,.4)' }}><Label color={C.mint}>Voice approval code · {code.plan_id} v{code.plan_version}</Label><div style={{ font: '600 28px ' + MONO, letterSpacing: '0.3em', color: C.mint, marginTop: 4 }}>{code.code}</div><div style={{ font: '10.5px ' + SANS, color: C.mute }}>Read this to the GRIDPILOT voice agent to authorize this exact plan version.</div></div>}
      <div className="flex flex-wrap gap-2 mt-3">
        <Btn onClick={() => setModal({ kind: 'diff' })}>View Diff</Btn>
        <Btn tone="mint" disabled={inc.state !== 'AWAITING_APPROVAL' || !!busy} onClick={() => run('approve', () => g.api('/api/incidents/' + inc.id + '/approve', 'POST', { plan_id: plan.plan_id, plan_version: plan.version }))}>Approve v{plan.version}</Btn>
        <Btn tone="danger" disabled={inc.state !== 'AWAITING_APPROVAL' || !!busy} onClick={() => setModal({ kind: 'reject' })}>Reject</Btn>
        <Btn disabled={!!busy || g.state.incident.state === 'CLOSED'} onClick={() => run('call', () => g.api('/api/incidents/' + inc.id + '/call', 'POST', {}))}>Call Operator</Btn>
        {inc.state === 'APPROVED' && <Btn tone="primary" disabled={!!busy} title="Normally CREAO initiates execution after approval; this lets the verified operator trigger it" onClick={() => run('exec', () => g.api('/api/incidents/' + inc.id + '/execute', 'POST', { plan_id: plan.plan_id, plan_version: plan.version }))}>Execute now</Btn>}
      </div>
      {!['CLOSED', 'EXECUTING', 'VALIDATING', 'MONITORING_RECOVERY'].includes(inc.state) && <div className="flex gap-2 mt-2">
        <input value={alt} onChange={e => setAlt(e.target.value)} placeholder="Request alternative, e.g. throttle R17 instead of migrating" className="flex-1 rounded-full px-3 outline-none" style={{ background: C.p2, border: '1px solid ' + C.line2, color: C.text, font: '11.5px ' + SANS, height: 30 }} onKeyDown={e => e.stopPropagation()} />
        <Btn disabled={!alt.trim() || !!busy} onClick={() => run('alt', async () => { const r = await g.api('/api/incidents/' + inc.id + '/alternative', 'POST', { suggestion: alt }); setAlt(''); return { ok: r.ok, message: r.ok ? 'Sent to CREAO for re-analysis' : 'CREAO not connected' }; })}>Request Alternative</Btn>
      </div>}
    </div>}
    {!plan && inc.state !== 'CLOSED' && <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line, font: '11.5px/1.5 ' + SANS, color: C.mute }}>Waiting for CREAO to propose a plan. Plans only appear from real agent tool calls (or an operator what-if in Agent Operations).</div>}
    {exec && <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}>
      <div className="flex justify-between"><Label>Execution {exec.id}</Label><span style={{ font: '10px ' + MONO, color: exec.state === 'rolled_back' ? C.red : C.mint }}>{exec.state}</span></div>
      <div className="mt-1.5 flex flex-col gap-0.5">{(exec.data.steps || []).map((s, i) => <div key={i} style={{ font: '10.5px ' + MONO, color: s.ok ? '#a9bbcf' : C.red }}>{s.ok ? '✓' : '✗'} {s.name} · {s.detail}</div>)}</div>
      {exec.data.validation && <div className="flex flex-wrap gap-1 mt-2">{exec.data.validation.map(c => <span key={c.name} title={c.detail} style={{ font: '9.5px ' + MONO, color: c.ok ? C.mint : C.red, border: '1px solid ' + (c.ok ? 'rgba(79,224,165,.35)' : 'rgba(255,89,110,.45)'), borderRadius: 4, padding: '1px 5px' }}>{c.ok ? '✓' : '✗'} {c.name}</span>)}</div>}
    </div>}
    {inc.finance && <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}><Label color={C.mint}>Financial closeout</Label><div style={{ font: '11.5px/1.6 ' + MONO, color: C.text, marginTop: 4 }}>Incurred (actual) {fmt$(inc.finance.incurred_usd)} · invoice {inc.finance.invoice_id} · {inc.finance.reconciliation_status}</div>{inc.mttr_sim_s && <div style={{ font: '11px ' + MONO, color: C.mute }}>Measured MTTR {inc.mttr_sim_s}s simulated ({inc.mttr_wall_s}s wall)</div>}</div>}
    {inc.summary && <div className="px-4 py-3" style={{ borderBottom: '1px solid ' + C.line }}><Label color={C.cyan}>Post-incident summary (CREAO)</Label><div style={{ font: '11.5px/1.55 ' + SANS, color: '#c7d6e6', marginTop: 4, whiteSpace: 'pre-wrap' }}>{inc.summary.text}</div></div>}
  </div>;
}

function CallOverlay({ g }) {
  const [hidden, setHidden] = React.useState(false);
  const [, setT] = React.useState(0); React.useEffect(() => { const t = setInterval(() => setT(x => x + 1), 1000); return () => clearInterval(t); }, []);
  const inc = g.state && g.state.incident; if (!inc) return null;
  const call = (inc.calls || []).slice(-1)[0];
  const live = call && !['failed', 'completed', 'ended', 'canceled', 'cancelled', 'no_answer', 'busy'].includes(call.status);
  if (!call || hidden || (Date.now() / 1000 - call.created > 1800)) return null;
  const pc = call.data.provider_call || {};
  const tr = pc.transcript || pc.messages || pc.turns || [];
  const evs = (g.state.events || []).filter(e => e.incident_id === inc.id && ['operator_suggestion', 'creao_run_started', 'creao_run_completed', 'state_transition', 'voice_tool_call'].includes(e.type) && e.ts >= call.created).reverse();
  const sug = evs.find(e => e.type === 'operator_suggestion');
  const chain = sug ? [['Human suggestion', true], ['CREAO re-analysis', evs.some(e => e.type === 'creao_run_started' && e.ts >= sug.ts)], ['Finance evaluation', evs.some(e => e.type === 'voice_tool_call' && e.ts >= sug.ts)], ['Revised plan', evs.some(e => e.type === 'state_transition' && e.data.to === 'PLAN_PROPOSED' && e.ts >= sug.ts)]] : null;
  const apr = (inc.approvals || []).slice(-1)[0]; const plan = (inc.plans || []).slice(-1)[0];
  return <div className="absolute z-30" style={{ right: 16, bottom: 16, width: 340 }}>
    <div style={{ background: 'rgba(16,24,38,.94)', border: '1px solid ' + (live ? 'rgba(67,217,245,.5)' : C.line2), borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,.5)', backdropFilter: 'blur(10px)' }}>
      <div className="flex items-center gap-2 px-3.5 py-2.5" style={{ borderBottom: '1px solid ' + C.line }}>
        <span className={live ? 'animate-pulse' : ''} style={{ width: 8, height: 8, borderRadius: 9, background: live ? C.cyan : call.status === 'failed' ? C.red : C.mute }} />
        <span style={{ font: '600 11px ' + MONO, letterSpacing: '.1em', color: C.text }}>CARTESIA · {call.data.operator || 'operator'}</span>
        <span className="ml-auto" style={{ font: '11px ' + MONO, color: C.mute }}>{call.status} · {fmtT(Date.now() / 1000 - call.created)}</span>
        <button onClick={() => setHidden(true)} style={{ color: C.mute, marginLeft: 6 }} aria-label="Hide call panel">×</button>
      </div>
      <div className="px-3.5 py-2.5 flex flex-col gap-2">
        <div style={{ font: '10.5px ' + MONO, color: C.mute }}>{inc.id} · plan {plan ? plan.plan_id + ' v' + plan.version : '—'} · approval {apr ? apr.status : 'none'}</div>
        {call.status === 'failed' && <div style={{ font: '11px/1.45 ' + SANS, color: C.amber }}>{call.data.error || 'Call not placed'}. No call was made.</div>}
        {chain && <div className="flex items-center gap-1 flex-wrap">{chain.map(([l, ok], i) => <React.Fragment key={l}><span style={{ font: '9.5px ' + MONO, padding: '2px 6px', borderRadius: 4, color: ok ? C.cyan : C.dim, border: '1px solid ' + (ok ? 'rgba(67,217,245,.45)' : C.line) }}>{l}</span>{i < 3 && <span style={{ color: C.dim }}>→</span>}</React.Fragment>)}</div>}
        <div className="overflow-auto flex flex-col gap-1" style={{ maxHeight: 150 }}>
          {Array.isArray(tr) && tr.length ? tr.slice(-12).map((t, i) => <div key={i} style={{ font: '11px/1.4 ' + SANS, color: (t.role || t.speaker) === 'user' ? C.text : '#9fd8ec' }}><b style={{ font: '600 9.5px ' + MONO, color: C.dim }}>{(t.role || t.speaker || '').toUpperCase()} </b>{t.text || t.content || t.message}</div>)
            : <div style={{ font: '10.5px/1.45 ' + SANS, color: C.dim }}>Live transcript appears here when Cartesia returns it. Tool calls from the call:</div>}
          {evs.filter(e => e.type === 'voice_tool_call').slice(0, 6).map(e => <div key={e.id} style={{ font: '10px ' + MONO, color: e.data.ok ? '#8fb3c9' : C.red }}>⟶ {e.data.tool}</div>)}
        </div>
      </div>
    </div>
  </div>;
}

function BottomStrip({ g, open, setOpen }) {
  const st = g.state; const s = (st && st.series) || [];
  const thr = st ? st.thresholds : { gpu_critical_c: 88, gpu_warning_c: 82 };
  const tempOpt = React.useMemo(() => lineOpt([{ name: 'R17 GPU °C', data: s.map(p => [p.t, p.r17_gpu]), color: C.red, mark: { y: thr.gpu_critical_c, color: 'rgba(255,89,110,.7)', label: 'critical' } }, { name: 'R17 inlet °C', data: s.map(p => [p.t, p.r17_inlet]), color: C.cyan }, { name: 'max inlet', data: s.map(p => [p.t, p.max_inlet]), color: C.amber }]), [st]);
  const powOpt = React.useMemo(() => lineOpt([{ name: 'IT kW', data: s.map(p => [p.t, p.it_kw]), color: C.cyan, area: true }, { name: 'Facility kW', data: s.map(p => [p.t, p.facility_kw]), color: '#7aa2ff' }]), [st]);
  const pueOpt = React.useMemo(() => lineOpt([{ name: 'PUE', data: s.map(p => [p.t, p.pue]), color: C.mint }, { name: 'C02 effectiveness', data: s.map(p => [p.t, p.c02_eff]), color: C.amber }]), [st]);
  const costOpt = React.useMemo(() => lineOpt([{ name: 'Electricity $', data: s.map(p => [p.t, p.electricity_usd]), color: C.cyan, area: true }, { name: 'Transfer $', data: s.map(p => [p.t, p.transfer_usd]), color: C.amber }]), [st]);
  const evs = ((st && st.events) || []).filter(e => !['agent_tool_call', 'voice_tool_call', 'duplicate_telemetry_suppressed'].includes(e.type)).slice(0, 40);
  return <div style={{ borderTop: '1px solid ' + C.line, background: C.p1 }}>
    <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-3 px-4" style={{ height: 30, color: C.mute }}>
      <Label>Operations strip</Label><span style={{ font: '10px ' + MONO, color: C.dim }}>{open ? '▾ collapse' : '▸ temperatures · power · airflow · timeline · finance'}</span>
      <span className="ml-auto" style={{ font: '10px ' + MONO, color: C.dim }}>{s.length ? 'sim t+' + Math.round(s[s.length - 1].t) + 's' : ''}</span>
    </button>
    {open && <div className="grid gap-3 px-3 pb-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
      <Panel style={{ padding: 8 }}><Label>R17 GPU hotspot vs inlet air</Label><EChart option={tempOpt} height={130} /></Panel>
      <Panel style={{ padding: 8 }}><Label>Power curve</Label><EChart option={powOpt} height={130} /></Panel>
      <Panel style={{ padding: 8 }}><Label>PUE · cooling health</Label><EChart option={pueOpt} height={130} /></Panel>
      <Panel style={{ padding: 8 }}><Label>Simulated spend (actual)</Label><EChart option={costOpt} height={130} /></Panel>
      <Panel style={{ padding: 8, gridColumn: 'span 2' }}><Label>Incident timeline</Label>
        <div className="overflow-auto mt-1" style={{ height: 130 }}>{evs.map(e => <div key={e.id} className="flex gap-2" style={{ font: '10.5px/1.6 ' + MONO }}>
          <span style={{ color: C.dim, width: 54, flexShrink: 0 }}>t+{Math.round(e.sim_t)}s</span><span style={{ color: e.type.includes('fail') || e.type.includes('rollback') ? C.red : e.type === 'state_transition' ? C.cyan : '#9fb0c4', flexShrink: 0 }}>{e.type === 'state_transition' ? e.data.from + ' → ' + e.data.to : e.type}</span>
          <span className="truncate" style={{ color: C.dim }}>{e.actor} {e.data.reason ? '· ' + e.data.reason : ''}</span></div>)}</div></Panel>
    </div>}
  </div>;
}

function ControlRoom({ g, setModal, toast, narrow }) {
  const [opts, setOpts] = React.useState({ mode: 'combined', density: 0.7, intensity: 1, paused: false, labels: false });
  const [sel, setSel] = React.useState(null); const [cmd, setCmd] = React.useState({ n: 0 }); const [strip, setStrip] = React.useState(true);
  const snap = g.snapRef.current; const selR = snap && sel != null ? snap.racks[sel] : null;
  const onSelect = React.useCallback((i) => setSel(i), []);
  const set = (k, v) => setOpts(o => ({ ...o, [k]: v }));
  const camBtn = (kind, label) => <button onClick={() => setCmd(c => ({ n: c.n + 1, kind }))} style={{ font: '500 10.5px ' + MONO, color: C.mute, padding: '4px 8px', borderRadius: 6, border: '1px solid ' + C.line }}>{label}</button>;
  const lat = snap ? snap.latency : {};
  return <div className={'flex flex-col min-h-0 flex-1' + (narrow ? ' overflow-auto' : '')}>
    <div className={narrow ? 'flex flex-col' : 'flex flex-1 min-h-0'}>
      <div className="relative min-w-0" style={narrow ? { height: 460, flexShrink: 0 } : { flex: '1 1 auto' }}>
        <DataCenterScene snapRef={g.snapRef} opts={opts} selected={sel} onSelect={onSelect} camCmd={cmd} />
        <div className="absolute left-3 top-3 flex flex-col gap-2 pointer-events-auto" style={{ maxWidth: narrow ? 'calc(100% - 24px)' : undefined }}>
          <div className="flex rounded-full p-0.5" style={{ background: 'rgba(16,24,38,.85)', border: '1px solid ' + C.line2 }}>
            {['thermal', 'airflow', 'power', 'workload', 'combined'].map(m => <button key={m} onClick={() => set('mode', m)} className="rounded-full" style={{ font: '600 10.5px ' + SANS, padding: '5px 11px', letterSpacing: '.02em', color: opts.mode === m ? '#04121a' : C.mute, background: opts.mode === m ? C.cyan : 'transparent' }}>{m[0].toUpperCase() + m.slice(1)}</button>)}
          </div>
          <div className="flex items-center gap-3 px-3 py-1.5 rounded-lg flex-wrap" style={{ background: 'rgba(16,24,38,.8)', border: '1px solid ' + C.line, font: '10px ' + MONO, color: C.mute, maxWidth: 520 }}>
            <label className="flex items-center gap-1.5">airflow <input type="range" min="0.2" max="3" step="0.1" value={opts.intensity} onChange={e => set('intensity', +e.target.value)} onPointerDown={e => e.stopPropagation()} style={{ width: 70, accentColor: C.cyan }} /></label>
            <label className="flex items-center gap-1.5">density <input type="range" min="0.1" max="1" step="0.05" value={opts.density} onChange={e => set('density', +e.target.value)} onPointerDown={e => e.stopPropagation()} style={{ width: 60, accentColor: C.cyan }} /></label>
            <button onClick={() => set('paused', !opts.paused)} style={{ color: opts.paused ? C.amber : C.mute }}>{opts.paused ? '▶ animate' : '❚❚ pause anim'}</button>
            <button onClick={() => set('labels', !opts.labels)} style={{ color: opts.labels ? C.cyan : C.mute }}>labels</button>
          </div>
        </div>
        <div className="absolute right-3 flex gap-1.5" style={{ top: 104, background: 'rgba(16,24,38,.8)', padding: 4, borderRadius: 8, border: '1px solid ' + C.line }}>
          {camBtn('reset', 'Reset')}{camBtn('iso', '3D iso')}{camBtn('top', '2D top')}{camBtn('focus', 'Focus')}
        </div>
        <div className="absolute left-3 bottom-3 px-3 py-2 rounded-lg" style={{ background: 'rgba(16,24,38,.82)', border: '1px solid ' + C.line, font: '10px ' + MONO, color: C.mute }}>
          <div className="flex items-center gap-2"><span>18°C</span><span style={{ width: 130, height: 6, borderRadius: 3, background: 'linear-gradient(90deg,#4087ff,#43D9F5,#4FE0A5,#FFBB55,#ff7340,#FF596E)' }} /><span>41°C</span></div>
          <div style={{ marginTop: 4, color: C.dim }}>Floor: air temperature field · Rack edges: {opts.mode === 'power' ? 'power kW' : opts.mode === 'workload' ? 'GPU utilization' : 'GPU hotspot / status'}</div>
          <div style={{ color: C.dim }}>Approximate 2D thermal/airflow model · not CFD</div>
        </div>
        {selR && <div className="absolute right-3 bottom-3 w-[260px] rounded-lg p-3" style={{ background: 'rgba(16,24,38,.92)', border: '1px solid rgba(67,217,245,.35)' }}>
          <div className="flex justify-between items-center"><span style={{ font: '600 13px ' + MONO, color: C.cyan }}>{selR.rack_id}</span><span style={{ font: '600 9.5px ' + MONO, color: statusColor(selR.status), letterSpacing: '.12em' }}>{selR.status.toUpperCase()}</span></div>
          <div style={{ font: '10px ' + MONO, color: C.dim }}>Row {ROWS[selR.row]} · {selR.zone}{selR.reserve_pool ? ' · reserve pool' : ''}</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-2">{[['GPU hotspot', selR.gpu_temp_c + '°C'], ['Inlet air', selR.inlet_temp_c + '°C'], ['Exhaust air', selR.exhaust_temp_c + '°C'], ['Power', selR.power_kw + ' kW'], ['Utilization', Math.round(selR.utilization * 100) + '%'], ['Power limit', selR.power_limit_w + ' W'], ['Airflow', selR.airflow_m3s + ' m³/s'], ['Cooling eff.', Math.round(selR.cooling_efficiency * 100) + '%']].map(([k, v]) => <div key={k}><div style={{ font: '9px ' + MONO, color: C.dim, textTransform: 'uppercase', letterSpacing: '.1em' }}>{k}</div><div style={{ font: '500 12px ' + MONO, color: C.text }}>{v}</div></div>)}</div>
          <div className="mt-2" style={{ font: '10px/1.5 ' + MONO, color: C.mute }}>{selR.workloads.map(w => <div key={w} className="flex justify-between"><span>{w}</span><span style={{ color: lat[w] && lat[w].breach ? C.red : C.dim }}>{lat[w] ? lat[w].p95_ms + '/' + lat[w].limit_ms + 'ms' : ''}</span></div>)}</div>
        </div>}
      </div>
      <div className="min-w-0 overflow-auto" style={narrow ? { borderTop: '1px solid ' + C.line, background: C.p1 } : { flex: '0 0 max(360px, 35%)', borderLeft: '1px solid ' + C.line, background: C.p1 }}>
        <IncidentPanel g={g} setModal={setModal} toast={toast} />
      </div>
    </div>
    <BottomStrip g={g} open={strip} setOpen={setStrip} />
  </div>;
}

const ST_COLOR = { awaiting_controller: '#FFBB55', held: '#FF596E', dispute_drafted: '#FF596E', disputed: '#FF596E', partially_disputed: '#FFBB55', approved: '#4FE0A5', paid: '#4FE0A5', received: '#8A9BB0', disputed_resolved: '#56677C' };
const INV_PRESETS = [
  ['Bank change', { vendor: 'NBT', number: 'NBT-5604', sku: 'INTERZONE-GB', qty: '40', rate: '0.25', remit_to: 'Chase Bank ••7781', from_email: 'billing@northbeam-transit-billing.com', note: 'We moved banks. Please pay this and future invoices to the new account today.' }],
  ['Price creep', { vendor: 'ACX', number: 'ACX-88301', sku: 'RESERVE-GPU-H100', qty: '6', rate: '3.95', note: '' }],
  ['Over-billed usage', { vendor: 'NBT', number: 'NBT-5611', sku: 'INTERZONE-GB', qty: '900', rate: '0.25', note: '' }],
  ['Unknown vendor', { vendor: 'ZEN', vendor_name: 'Zenith Facility Services', number: 'ZEN-0042', sku: 'JANITORIAL', qty: '1', rate: '780', remit_to: 'Wells Fargo ••2210', from_email: 'ar@zenith-fs.com', note: '' }],
];
function NewInvoice({ g, toast, onClose, onDone }) {
  const [f, setF] = React.useState({ vendor: 'ACX', vendor_name: '', number: '', incident: '', sku: 'RESERVE-GPU-H100', qty: '6', rate: '3.85', remit_to: '', from_email: '', note: '' });
  const [busy, setBusy] = React.useState(false);
  const set = (k, v) => setF(x => ({ ...x, [k]: v }));
  const field = { background: C.p1, border: '1px solid ' + C.line2, color: C.text, font: '11.5px ' + SANS, borderRadius: 8, padding: '6px 9px', outline: 'none', width: '100%' };
  const submit = async () => { setBusy(true); try { const r = await g.api('/api/finance/invoices', 'POST', { vendor: f.vendor, vendor_name: f.vendor_name, number: f.number, incident: f.incident, remit_to: f.remit_to, from_email: f.from_email, note: f.note, lines: [{ sku: f.sku, qty: +f.qty, rate: +f.rate }] }); toast(r.message, 'ok'); onDone(r.invoice_id); } catch (e) { toast(e.message, 'err'); } setBusy(false); };
  const row = (k, label, ph) => <label className="flex flex-col gap-1"><span style={{ font: '600 9px ' + MONO, letterSpacing: '.14em', color: C.dim, textTransform: 'uppercase' }}>{label}</span><input value={f[k]} placeholder={ph} onChange={e => set(k, e.target.value)} onKeyDown={e => e.stopPropagation()} style={field} /></label>;
  return <Modal title="Submit a test invoice" onClose={onClose}>
    <div style={{ font: '11.5px/1.55 ' + SANS, color: C.mute }}>Try your own scenario. The invoice lands in the inbox and goes through the same autonomous pass as everything else: three-way match against the contract, the meter and the vendor master, then approval limits and segregation of duties. Then use Ask why or Run AP review (CREAO).</div>
    <div className="flex flex-wrap gap-1.5 mt-3">{INV_PRESETS.map(([l, p]) => <Btn key={l} small onClick={() => setF(x => ({ ...x, vendor_name: '', remit_to: '', from_email: '', incident: '', ...p }))}>{l}</Btn>)}</div>
    <div className="grid gap-2.5 mt-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
      <label className="flex flex-col gap-1"><span style={{ font: '600 9px ' + MONO, letterSpacing: '.14em', color: C.dim }}>VENDOR</span><select value={['ACX', 'BPC', 'NBT', 'CTS'].includes(f.vendor) ? f.vendor : 'OTHER'} onChange={e => set('vendor', e.target.value === 'OTHER' ? 'NEW' : e.target.value)} style={field}><option value="ACX">ACX · Atlas Capacity Exchange</option><option value="BPC">BPC · Bay Power Co-op</option><option value="NBT">NBT · Northbeam Transit</option><option value="CTS">CTS · CoolTech Services</option><option value="OTHER">Other (not in vendor master)</option></select></label>
      {!['ACX', 'BPC', 'NBT', 'CTS'].includes(f.vendor) && row('vendor', 'Vendor code', 'e.g. ZEN')}
      {!['ACX', 'BPC', 'NBT', 'CTS'].includes(f.vendor) && row('vendor_name', 'Vendor name', 'Zenith Facility Services')}
      {row('number', 'Invoice number', 'auto if blank')}
      {row('incident', 'Incident (optional)', 'INC-041')}
      {row('sku', 'Line SKU', '')}{row('qty', 'Quantity', '')}{row('rate', 'Rate $', '')}
      {row('remit_to', 'Remit to', 'blank = bank account on file')}{row('from_email', 'Sent from', 'blank = vendor domain')}
    </div>
    <div className="mt-2.5">{row('note', 'Message on invoice', 'optional')}</div>
    <div className="flex justify-between items-center mt-3"><span style={{ font: '11px ' + MONO, color: C.mute }}>Total {fmt$((+f.qty || 0) * (+f.rate || 0))}</span><Btn tone="primary" disabled={busy || !f.vendor || !(+f.qty > 0) || !(+f.rate > 0)} onClick={submit}>{busy ? 'Submitting…' : 'Submit invoice'}</Btn></div>
  </Modal>;
}

function FinanceDesk({ g, toast, narrow }) {
  const st = g.state; const D = st && st.desk; const inc = st && st.incident;
  const [sub, setSub] = React.useState('inbox'); const [sel, setSel] = React.useState(null); const [note, setNote] = React.useState(''); const [busy, setBusy] = React.useState(false);
  const [groupBy, setGroupBy] = React.useState('service'); const [askQ, setAskQ] = React.useState(''); const [newInv, setNewInv] = React.useState(false);
  if (!D) return <div className="flex-1 p-6" style={{ background: C.bg, color: C.mute, font: '12px ' + SANS }}>Loading finance desk…</div>;
  const act = async (path, body, okMsg) => { setBusy(true); try { const r = await g.api(path, 'POST', body || {}); toast(r.message || okMsg, r.ok === false ? 'err' : 'ok'); } catch (e) { toast(e.message, 'err'); } setBusy(false); };
  const pending = D.invoices.filter(i => !['paid', 'disputed_resolved'].includes(i.status));
  const approved = D.invoices.filter(i => ['approved', 'partially_disputed'].includes(i.status));
  const inv = D.invoices.find(i => i.id === sel) || pending[0];
  const agentRecs = (i) => i.decisions.filter(d => d.kind === 'agent_recommendation');
  const drafts = (i) => i.decisions.filter(d => d.kind === 'dispute_draft');
  const apRun = (st.specialists || []).find(s => s.kind === 'ap_analyst');
  const tabs = [['inbox', 'Invoice inbox', pending.length], ['explorer', 'Cost explorer'], ['incidents', 'Incident costs'], ['ledger', 'Ledger'], ['proof', 'Controls & proof']];
  const chip = (txt, color) => <span style={{ font: '600 9.5px ' + MONO, color, border: '1px solid ' + color + '66', borderRadius: 4, padding: '1px 6px', whiteSpace: 'nowrap' }}>{txt}</span>;
  const dec = (d) => ({ approve: C.mint, short_pay: C.amber, dispute: C.red, hold: C.red }[d] || C.mute);
  // explorer data
  const rows = D.explorer; const months = [...new Set(rows.map(r => r.month))].sort();
  const keys = [...new Set(rows.map(r => r[groupBy]))];
  const palette = [C.cyan, C.amber, C.mint, '#b49cff', C.red, '#7aa2ff', '#f0a6ca', '#9fd8ec'];
  const explorerOpt = { animation: false, grid: { left: 64, right: 12, top: 30, bottom: 22 }, tooltip: { trigger: 'axis', backgroundColor: '#0d1522', borderColor: '#24334a', textStyle: { color: '#E6EEF7', fontFamily: MONO, fontSize: 10 }, valueFormatter: v => fmt$(v) },
    legend: { top: 0, textStyle: { color: '#8A9BB0', fontFamily: MONO, fontSize: 9 }, itemWidth: 10, itemHeight: 6 }, xAxis: { type: 'category', data: months, ...axisStyle }, yAxis: { type: 'log', logBase: 10, min: 10, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: v => '$' + (v >= 1000 ? Math.round(v / 1000) + 'k' : v) } },
    series: keys.map((k, i) => ({ name: k, type: 'bar', stack: 'a', barWidth: 26, itemStyle: { color: palette[i % palette.length] }, data: months.map(m => +rows.filter(r => r.month === m && r[groupBy] === k).reduce((a, r) => a + r.usd, 0).toFixed(2) || null) })) };
  const month = months[months.length - 1];
  const totals = keys.map(k => [k, rows.filter(r => r[groupBy] === k && r.month === month).reduce((a, r) => a + r.usd, 0), rows.filter(r => r[groupBy] === k).reduce((a, r) => a + r.usd, 0)]).sort((a, b) => b[2] - a[2]);
  // incident costs
  const incIds = [...new Set(rows.map(r => r.incident).concat(D.invoices.map(i => i.incident)).filter(x => x && x !== '-'))].sort();
  const incRows = incIds.map(id => { const invs = D.invoices.filter(i => i.incident === id); const acc = D.journal.filter(j => (j.source || '').startsWith('ACCRUAL-' + id)).reduce((a, j) => a + j.lines.filter(l => l.acct !== '2100').reduce((x, l) => x + (l.dr || 0), 0), 0);
    return { id, accrued: acc, booked: rows.filter(r => r.incident === id).reduce((a, r) => a + r.usd, 0), invoiced: invs.reduce((a, i) => a + i.amount, 0), paid: invs.filter(i => i.status === 'paid').reduce((a, i) => a + i.amount, 0), disputed: invs.filter(i => /disput|held/.test(i.status)).length, invs }; });
  const plan = inc && inc.plans && inc.plans.slice(-1)[0]; const b52 = D.budgets['5200'];
  return <div className="flex-1 min-h-0 flex flex-col" style={{ background: C.bg }}>
    <div className="flex items-center gap-1 px-4 pt-3 pb-2 flex-wrap" style={{ borderBottom: '1px solid ' + C.line }}>
      <div className="mr-3"><Label color={C.cyan}>Hyphenate track · the Hyphenated Enterprise</Label><div style={{ font: '500 12px ' + SANS, color: C.text, marginTop: 2 }}>Atlas Compute's AP analyst and cost controller seats, run autonomously. The human controller signs off.</div></div>
      {tabs.map(([k, l, n]) => <button key={k} onClick={() => setSub(k)} className="rounded-full" style={{ font: '600 11px ' + SANS, padding: '5px 12px', color: sub === k ? '#04121a' : C.mute, background: sub === k ? C.cyan : 'transparent', border: '1px solid ' + (sub === k ? C.cyan : C.line) }}>{l}{n != null ? ' · ' + n : ''}</button>)}
      <div className="ml-auto flex gap-2 items-center">
        <span style={{ font: '10.5px ' + MONO, color: C.mute }}>score {D.score.correct}/{D.score.total} · planted errors caught {D.score.caught}/{D.score.planted_errors}</span>
        <Btn small disabled={busy} onClick={() => setNewInv(true)}>+ Test invoice</Btn>
        <Btn small disabled={busy} onClick={() => act('/api/finance/ap-review', {}, 'AP review started')}>Run AP review (CREAO)</Btn>
        <Btn small tone="mint" disabled={busy || !approved.length} onClick={() => act('/api/finance/payment-run', {}, 'Payment run complete')}>Payment run · {approved.length}</Btn>
      </div>
    </div>
    {D.alerts.filter(a => a.status === 'open').slice(0, 3).map(a => <div key={a.id} className="px-4 py-1.5" style={{ font: '11px ' + MONO, color: a.severity === 'high' ? C.red : C.amber, background: a.severity === 'high' ? 'rgba(255,89,110,.06)' : 'rgba(255,187,85,.05)', borderBottom: '1px solid ' + C.line }}>▲ {a.kind.replace(/_/g, ' ')} · {a.text}</div>)}
    {newInv && <NewInvoice g={g} toast={toast} onClose={() => setNewInv(false)} onDone={(iid) => { setNewInv(false); setSub('inbox'); setSel(iid); }} />}
    {sub === 'inbox' && <div className={'flex-1 min-h-0 grid' + (narrow ? ' overflow-auto' : '')} style={{ gridTemplateColumns: narrow ? '1fr' : 'minmax(300px, 0.9fr) minmax(420px, 1.6fr)', gridAutoRows: narrow ? 'max-content' : undefined }}>
      <div className="overflow-auto" style={narrow ? { maxHeight: 280, borderBottom: '1px solid ' + C.line } : { borderRight: '1px solid ' + C.line }}>
        {apRun && <div className="px-3 py-2" style={{ borderBottom: '1px solid ' + C.line, font: '10.5px ' + MONO, color: C.mute }}>CREAO AP Analyst · {apRun.status}{apRun.creao_run_id ? ' · run ' + apRun.creao_run_id.slice(0, 8) : ''}</div>}
        {D.invoices.map(i => <button key={i.id} onClick={() => setSel(i.id)} className="w-full text-left px-3 py-2.5" style={{ borderBottom: '1px solid ' + C.line, background: inv && inv.id === i.id ? 'rgba(67,217,245,.06)' : 'transparent', opacity: ['paid', 'disputed_resolved'].includes(i.status) ? 0.55 : 1 }}>
          <div className="flex justify-between gap-2"><span style={{ font: '600 11.5px ' + MONO, color: C.text }}>{i.id}</span><span style={{ font: '500 11.5px ' + MONO, color: C.text }}>{fmt$(i.amount)}</span></div>
          <div className="flex justify-between gap-2 mt-0.5"><span className="truncate" style={{ font: '10.5px ' + SANS, color: C.mute }}>{i.vendor_name}{i.incident ? ' · ' + i.incident : ''} · {i.date}</span>{chip(i.status.replace(/_/g, ' '), ST_COLOR[i.status] || C.mute)}</div>
          {i.analysis && !['approved', 'paid'].includes(i.status) && <div className="mt-1" style={{ font: '10px ' + MONO, color: dec(i.analysis.decision) }}>→ {i.analysis.decision.replace('_', ' ')} {i.analysis.decision !== 'hold' ? fmt$(i.analysis.amount) : ''}{agentRecs(i).length ? ' · CREAO ' + agentRecs(i).slice(-1)[0].decision : ''}</div>}
        </button>)}
      </div>
      <div className={narrow ? 'p-4' : 'overflow-auto p-4'}>{inv ? <div className="flex flex-col gap-3">
        <div className="flex justify-between items-start gap-3"><div><div style={{ font: '600 15px ' + MONO, color: C.text }}>{inv.id} <span style={{ color: C.mute, fontWeight: 400 }}>· {inv.vendor_name}</span></div>
          <div style={{ font: '10.5px ' + MONO, color: C.dim, marginTop: 2 }}>issued {inv.date} · due {inv.due} · period {inv.period}{inv.po ? ' · ' + inv.po : ''} · from {inv.from_email || '—'} · remit {inv.remit_to || '—'}</div></div>{chip(inv.status.replace(/_/g, ' '), ST_COLOR[inv.status] || C.mute)}</div>
        {inv.note && <div className="p-2.5 rounded" style={{ background: 'rgba(255,89,110,.06)', border: '1px solid rgba(255,89,110,.3)', font: '11.5px/1.5 ' + SANS, color: '#ffc2ca' }}>Message on invoice: “{inv.note}”</div>}
        <Panel style={{ padding: 10 }}>{inv.lines.map((l, k) => <div key={k} className="flex justify-between" style={{ font: '11.5px ' + MONO, color: C.text }}><span style={{ color: C.mute }}>{l.sku}</span><span>{l.qty.toLocaleString()} × {fmt$(l.rate)} = {fmt$(l.usd)}</span></div>)}</Panel>
        {inv.analysis && <><Label>Three-way match (contract × meter × invoice)</Label>
          <div className="flex flex-col gap-1">{inv.analysis.checks.map((c, k) => <div key={k} className="flex gap-2" style={{ font: '11px/1.5 ' + MONO }}><span style={{ color: c.ok ? C.mint : (c.severity === 'info' ? C.mute : c.severity === 'high' ? C.red : C.amber), width: 12 }}>{c.ok ? '✓' : '✗'}</span><span style={{ color: C.text, width: 170, flexShrink: 0 }}>{c.name.replace(/_/g, ' ')}</span><span style={{ color: C.mute }}>{c.detail}{c.source ? <span style={{ color: C.dim }}> · {c.source}</span> : null}</span></div>)}</div>
          <div className="p-3 rounded" style={{ background: C.p2, border: '1px solid ' + dec(inv.analysis.decision) + '55' }}><div className="flex justify-between"><Label color={dec(inv.analysis.decision)}>Recommendation · {inv.analysis.decision.replace('_', ' ')} {inv.analysis.decision !== 'hold' ? fmt$(inv.analysis.amount) : ''}</Label><span style={{ font: '10px ' + MONO, color: C.mute }}>sign-off: {inv.analysis.approver_required}</span></div>
            <div style={{ font: '12px/1.55 ' + SANS, color: C.text, marginTop: 4 }}>{inv.analysis.reason}</div>{inv.analysis.segregation && <div style={{ font: '10.5px ' + MONO, color: C.amber, marginTop: 4 }}>Segregation of duties: {inv.analysis.segregation}</div>}</div></>}
        {agentRecs(inv).map((d, k) => <div key={k} className="p-3 rounded" style={{ background: 'rgba(67,217,245,.05)', border: '1px solid rgba(67,217,245,.2)' }}><Label color={C.cyan}>CREAO AP Analyst · {d.decision} {fmt$(d.amount)} · {d.actor}</Label><div style={{ font: '11.5px/1.55 ' + SANS, color: '#c7d6e6', marginTop: 4 }}>{d.reason}</div></div>)}
        {drafts(inv).slice(-1).map((d, k) => <div key={k} className="p-3 rounded" style={{ background: C.p2, border: '1px solid ' + C.line2 }}><Label>Dispute email draft (not sent)</Label><pre style={{ whiteSpace: 'pre-wrap', font: '11px/1.55 ' + SANS, color: C.text, margin: '6px 0 0' }}>{d.reason}</pre></div>)}
        {!['paid', 'disputed_resolved', 'approved'].includes(inv.status) && <div className="flex flex-col gap-2">
          <input value={note} onChange={e => setNote(e.target.value)} onKeyDown={e => e.stopPropagation()} placeholder={inv.analysis && inv.analysis.decision === 'hold' ? "verified: called vendor at number on file, confirmed…" : 'Note for the audit trail (optional)'} className="rounded-full px-3 outline-none" style={{ background: C.p2, border: '1px solid ' + C.line2, color: C.text, font: '11.5px ' + SANS, height: 30 }} />
          <div className="flex gap-2 flex-wrap"><Btn tone="mint" disabled={busy} onClick={() => act('/api/finance/invoices/' + inv.id + '/approve', { note }, 'Approved and posted')}>Approve as controller</Btn>
            <Btn tone="danger" disabled={busy} onClick={() => act('/api/finance/invoices/' + inv.id + '/dispute', { note }, 'Disputed')}>Dispute</Btn>
            <Btn disabled={busy} onClick={() => act('/api/finance/invoices/' + inv.id + '/hold', { note }, 'Held')}>Hold</Btn></div></div>}
        <div className="p-3 rounded" style={{ background: 'rgba(67,217,245,.04)', border: '1px solid rgba(67,217,245,.22)' }}>
          <Label color={C.cyan}>Ask why · CREAO Finance Explainer defends the call, citing its sources</Label>
          <div className="flex flex-col gap-2 mt-2">{inv.decisions.filter(d => d.kind === 'question' || d.kind === 'explanation').map((d, k) => <div key={k} style={{ font: (d.kind === 'question' ? '600 ' : '') + '11.5px/1.55 ' + SANS, color: d.kind === 'question' ? C.text : '#c7d6e6', paddingLeft: d.kind === 'question' ? 0 : 10, borderLeft: d.kind === 'question' ? 'none' : '2px solid rgba(67,217,245,.35)', whiteSpace: 'pre-wrap' }}>{d.kind === 'question' ? 'Q · ' : ''}{d.reason}<div style={{ font: '9.5px ' + MONO, color: C.dim, fontWeight: 400 }}>{d.actor} · {new Date(d.ts * 1000).toLocaleTimeString()}</div></div>)}
            {(() => { const qs = inv.decisions.filter(d => d.kind === 'question' || d.kind === 'explanation'); const last = qs[qs.length - 1]; return last && last.kind === 'question' && Date.now() / 1000 - last.ts < 600 ? <div className="animate-pulse" style={{ font: '10.5px ' + MONO, color: C.cyan }}>CREAO is reading the contract, meter, vendor history and decision log… (about a minute)</div> : null; })()}
          </div>
          <div className="flex gap-2 mt-2"><input value={askQ} onChange={e => setAskQ(e.target.value)} onKeyDown={e => { e.stopPropagation(); if (e.key === 'Enter' && askQ.trim()) { act('/api/finance/invoices/' + inv.id + '/ask', { question: askQ }, 'Asked'); setAskQ(''); } }} placeholder={'e.g. Why ' + (inv.analysis ? inv.analysis.decision.replace('_', ' ') : 'this') + ' instead of paying it?'} className="flex-1 rounded-full px-3 outline-none" style={{ background: C.p2, border: '1px solid ' + C.line2, color: C.text, font: '11.5px ' + SANS, height: 30 }} />
            <Btn small disabled={busy || !askQ.trim()} onClick={() => { act('/api/finance/invoices/' + inv.id + '/ask', { question: askQ }, 'Asked'); setAskQ(''); }}>Ask why</Btn></div>
        </div>
        <Label>Audit trail</Label>
        <div className="flex flex-col gap-0.5">{inv.decisions.map((d, k) => <div key={k} style={{ font: '10.5px/1.5 ' + MONO, color: C.mute }}>{new Date(d.ts * 1000).toLocaleString()} · <span style={{ color: C.text }}>{d.actor}</span> · {d.kind} · {d.decision}{d.amount ? ' ' + fmt$(d.amount) : ''}</div>)}</div>
      </div> : <div style={{ color: C.mute, font: '12px ' + SANS }}>No invoices.</div>}</div>
    </div>}
    {sub === 'explorer' && <div className="flex-1 overflow-auto p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2"><Label>Group by</Label>{['service', 'vendor', 'incident', 'acct'].map(k => <button key={k} onClick={() => setGroupBy(k)} className="rounded-full" style={{ font: '600 10.5px ' + SANS, padding: '3px 10px', color: groupBy === k ? '#04121a' : C.mute, background: groupBy === k ? C.cyan : 'transparent', border: '1px solid ' + C.line }}>{k}</button>)}<span className="ml-auto" style={{ font: '10px ' + MONO, color: C.dim }}>Posted expense lines from the general ledger · log scale</span></div>
      <Panel style={{ padding: 10 }}><EChart option={explorerOpt} height={260} /></Panel>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'minmax(320px,1.2fr) minmax(300px,1fr)' }}>
        <Panel style={{ padding: 12 }}><Label>{groupBy} · {month} vs all time</Label>{totals.map(([k, m, a]) => <div key={k} className="flex justify-between" style={{ font: '11.5px/1.9 ' + MONO, borderBottom: '1px solid ' + C.line }}><span style={{ color: C.text }}>{k}</span><span style={{ color: C.mute }}>{fmt$(m)} <span style={{ color: C.dim }}>· {fmt$(a)}</span></span></div>)}</Panel>
        <Panel style={{ padding: 12 }}><Label>October budgets</Label>{Object.entries(D.budgets).map(([a, b]) => { const pct = b.actual / b.budget; return <div key={a} className="mt-2"><div className="flex justify-between" style={{ font: '11px ' + MONO, color: C.text }}><span>{a} {b.name.replace('COGS - ', '')}</span><span style={{ color: pct > 1 ? C.red : pct > 0.8 ? C.amber : C.mute }}>{fmt$(b.actual)} / {fmt$(b.budget)}</span></div><div style={{ height: 5, background: C.p2, borderRadius: 3, marginTop: 3 }}><div style={{ width: Math.min(100, pct * 100) + '%', height: 5, borderRadius: 3, background: pct > 1 ? C.red : pct > 0.8 ? C.amber : C.cyan }} /></div></div>; })}</Panel>
      </div></div>}
    {sub === 'incidents' && <div className="flex-1 overflow-auto p-4 flex flex-col gap-3">
      {plan && inc.state !== 'CLOSED' && <Panel style={{ padding: 12 }}><Label color={C.amber}>Live · cost approval for {plan.plan_id} v{plan.version} ({inc.id})</Label>
        <div className="grid grid-cols-4 gap-2 mt-2">{[['Incremental cost', fmt$(plan.finance.incremental_cost_usd)], ['Burst budget left', fmt$(b52.budget - b52.actual)], ['After this plan', fmt$(b52.budget - b52.actual - plan.finance.incremental_cost_usd)], ['SLA exposure avoided', fmt$(Math.max(0, plan.baseline.sla_exposure_usd - plan.finance.sla_exposure_usd))]].map(([k, v]) => <div key={k} className="p-2 rounded" style={{ background: C.p2 }}><Label>{k}</Label><div style={{ font: '500 13px ' + MONO, color: C.text, marginTop: 3 }}>{v}</div></div>)}</div>
        <div style={{ font: '11px/1.5 ' + SANS, color: C.mute, marginTop: 6 }}>Policy {plan.policy.decision.replace('_', ' ')} · {plan.policy.plan_rules.filter(r => r.decision !== 'AUTO_ALLOWED').map(r => r.rule).join(' · ') || 'no cost rule triggered'}. On execution the cost is accrued (Dr 5200 / Cr 2100); the vendor invoice is matched against it at closeout.</div></Panel>}
      <Panel style={{ padding: 12 }}><Label>Post-incident cost report</Label>
        <table className="w-full mt-2" style={{ font: '11px ' + MONO, color: C.text, borderCollapse: 'collapse' }}><thead><tr style={{ color: C.dim, textAlign: 'left' }}>{['Incident', 'Accrued at execution', 'Booked expense', 'Invoiced', 'Paid', 'Exceptions', 'Invoices'].map(h => <th key={h} style={{ padding: 6, borderBottom: '1px solid ' + C.line, fontWeight: 500 }}>{h}</th>)}</tr></thead>
          <tbody>{incRows.map(r => <tr key={r.id} style={{ borderBottom: '1px solid ' + C.line }}><td style={{ padding: 6, color: C.cyan }}>{r.id}</td><td style={{ padding: 6 }}>{fmt$(r.accrued)}</td><td style={{ padding: 6 }}>{fmt$(r.booked)}</td><td style={{ padding: 6 }}>{fmt$(r.invoiced)}</td><td style={{ padding: 6 }}>{fmt$(r.paid)}</td><td style={{ padding: 6, color: r.disputed ? C.red : C.mute }}>{r.disputed || '—'}</td><td style={{ padding: 6, color: C.mute }}>{r.invs.map(i => i.id + ' (' + i.status.replace(/_/g, ' ') + ')').join(', ')}</td></tr>)}</tbody></table></Panel>
    </div>}
    {sub === 'ledger' && <div className="flex-1 overflow-auto p-4 grid gap-3" style={{ gridTemplateColumns: 'minmax(300px,0.8fr) minmax(420px,1.5fr)' }}>
      <div className="flex flex-col gap-3"><Panel style={{ padding: 12 }}><div className="flex justify-between"><Label>Trial balance</Label><span style={{ font: '600 10px ' + MONO, color: Math.abs(D.trial_balance.total_debit - D.trial_balance.total_credit) < 0.01 ? C.mint : C.red }}>{Math.abs(D.trial_balance.total_debit - D.trial_balance.total_credit) < 0.01 ? 'BALANCED' : 'OUT OF BALANCE'}</span></div>
        {D.trial_balance.rows.filter(r => r.debit || r.credit).map(r => <div key={r.acct} className="grid" style={{ gridTemplateColumns: '1fr 90px 90px', font: '11px/1.9 ' + MONO, borderBottom: '1px solid ' + C.line }}><span style={{ color: C.text }}>{r.acct} {r.name}</span><span style={{ textAlign: 'right' }}>{r.debit ? fmt$(r.debit) : ''}</span><span style={{ textAlign: 'right' }}>{r.credit ? fmt$(r.credit) : ''}</span></div>)}
        <div className="grid" style={{ gridTemplateColumns: '1fr 90px 90px', font: '600 11px/2 ' + MONO }}><span>Total</span><span style={{ textAlign: 'right' }}>{fmt$(D.trial_balance.total_debit)}</span><span style={{ textAlign: 'right' }}>{fmt$(D.trial_balance.total_credit)}</span></div></Panel>
        <Panel style={{ padding: 12 }}><Label>Bank feed (Mercury operating)</Label>{D.bank.map(b => <div key={b.id} className="flex justify-between" style={{ font: '10.5px/1.8 ' + MONO }}><span style={{ color: C.mute }}>{b.date} {b.counterparty}</span><span style={{ color: C.text }}>{fmt$(b.amount)} <span style={{ color: b.matched ? C.mint : C.amber }}>{b.matched ? '↔ ' + b.matched : 'unmatched'}</span></span></div>)}</Panel></div>
      <Panel style={{ padding: 12 }}><Label>Journal (newest first) · every entry balanced, attributed and sourced</Label>
        {D.journal.map(j => <div key={j.id} className="py-2" style={{ borderBottom: '1px solid ' + C.line }}><div className="flex justify-between gap-2"><span style={{ font: '600 11px ' + MONO, color: C.cyan }}>{j.id} <span style={{ color: C.mute, fontWeight: 400 }}>{j.date}</span></span><span style={{ font: '10px ' + MONO, color: C.dim }}>{j.actor}{j.approved_by ? ' · approved ' + j.approved_by : ''} · src {j.source}</span></div>
          <div style={{ font: '11px ' + SANS, color: C.text, margin: '2px 0' }}>{j.memo}</div>
          {j.lines.map((l, k) => <div key={k} className="grid" style={{ gridTemplateColumns: '1fr 90px 90px', font: '10.5px ' + MONO, color: C.mute }}><span>{l.cr ? '   ' : ''}{l.acct} {(D.trial_balance.rows.find(r => r.acct === l.acct) || {}).name}{l.incident ? ' · ' + l.incident : ''}</span><span style={{ textAlign: 'right' }}>{l.dr ? fmt$(l.dr) : ''}</span><span style={{ textAlign: 'right' }}>{l.cr ? fmt$(l.cr) : ''}</span></div>)}</div>)}</Panel>
    </div>}
    {sub === 'proof' && <div className="flex-1 overflow-auto p-4 grid gap-3" style={{ gridTemplateColumns: 'minmax(420px,1.4fr) minmax(300px,1fr)' }}>
      <Panel style={{ padding: 12 }}><div className="flex justify-between items-baseline"><Label color={C.mint}>Planted errors vs answer key</Label><span style={{ font: '600 12px ' + MONO, color: C.mint }}>{D.score.correct}/{D.score.total} correct · {D.score.caught}/{D.score.planted_errors} errors caught · {D.score.false_flags} false flags</span></div>
        <table className="w-full mt-2" style={{ font: '11px ' + MONO, color: C.text, borderCollapse: 'collapse' }}><thead><tr style={{ color: C.dim, textAlign: 'left' }}>{['Invoice', 'Planted', 'Expected', 'System', ''].map(h => <th key={h} style={{ padding: 6, borderBottom: '1px solid ' + C.line, fontWeight: 500 }}>{h}</th>)}</tr></thead>
          <tbody>{D.score.cases.map(c => <tr key={c.invoice_id} style={{ borderBottom: '1px solid ' + C.line }}><td style={{ padding: 6, color: C.cyan }}>{c.invoice_id}</td><td style={{ padding: 6, color: C.mute, maxWidth: 300 }}>{c.planted}</td><td style={{ padding: 6 }}>{c.expected} {fmt$(c.expected_amount)}</td><td style={{ padding: 6 }}>{c.system} {fmt$(c.system_amount)}</td><td style={{ padding: 6, color: c.correct ? C.mint : C.red }}>{c.correct ? '✓' : '✗'}</td></tr>)}</tbody></table>
        <div style={{ font: '10.5px/1.5 ' + SANS, color: C.dim, marginTop: 8 }}>The answer key is stored separately and never shown to the matcher or the CREAO agent. Scoring re-runs the deterministic analysis on every invoice.</div></Panel>
      <div className="flex flex-col gap-3"><Panel style={{ padding: 12 }}><Label>Controls</Label><div style={{ font: '11.5px/1.7 ' + SANS, color: C.text, marginTop: 4 }}>
        <div>· The autonomous AP seat approves only clean three-way matches up to {fmt$(D.policy.agent_approval_limit_usd)}.</div><div>· Everything else needs the controller ({D.people.controller}).</div><div>· The requester of a purchase cannot approve its invoice ({D.people.facilities}).</div><div>· Bank detail changes are held until a callback to the number on file is recorded.</div><div>· Price variances are disputed at any amount; quantity variances under {fmt$(D.policy.materiality_usd)} are approved and logged.</div><div>· Every journal entry must balance; the ledger refuses one that does not.</div><div>· Open vendor credits are applied to the earliest eligible invoice, even months later.</div></div></Panel>
        <Panel style={{ padding: 12 }}><Label>Real vs simulated</Label><div style={{ font: '11px/1.6 ' + SANS, color: C.mute, marginTop: 4 }}><div style={{ color: C.mint, font: '600 9.5px ' + MONO }}>REAL</div>Double-entry ledger and trial balance · three-way matching · approval limits and segregation of duties · payment runs · CREAO AP Analyst runs · audit trail · scoring<div style={{ color: C.amber, font: '600 9.5px ' + MONO, marginTop: 6 }}>SIMULATED</div>Vendors, invoices, bank feed and company history (Mar to Oct 2026), with planted errors</div></Panel></div>
    </div>}
  </div>;
}

const SPECIALIST_KINDS = [['thermal_forensics', 'Thermal forensics'], ['capacity_planner', 'Capacity placement'], ['finops_auditor', 'FinOps invoice audit'], ['sla_guardian', 'SLA guardian'], ['custom', 'New specialist…']];
function SpecialistsPanel({ g, inc }) {
  const st = g.state; const it = st && st.integrations; const cli = it && it.creao_cli;
  const list = ((st && st.specialists) || []).filter(s => !inc || s.incident_id === inc.id);
  const agents = (cli && cli.agents) || [];
  const [kind, setKind] = React.useState('thermal_forensics'); const [q, setQ] = React.useState(''); const [mission, setMission] = React.useState(''); const [msg, setMsg] = React.useState(null); const [busy, setBusy] = React.useState(false);
  const canPlan = inc && ['REPLAN', 'TRIAGING', 'DENIED', 'AWAITING_APPROVAL', 'ESCALATED', 'POLICY_CHECKED'].includes(inc.state);
  const field = { background: C.p2, border: '1px solid ' + C.line2, color: C.text, font: '11px ' + SANS, borderRadius: 8, padding: '6px 9px', outline: 'none', width: '100%' };
  const stop = e => e.stopPropagation();
  const ask = async () => { setBusy(true); setMsg(null); try { const r = await g.api('/api/incidents/' + inc.id + '/specialist', 'POST', { kind: kind === 'custom' ? (mission.split(/\s+/).slice(0, 3).join('-') || 'custom') : kind, question: q, mission: kind === 'custom' ? mission : undefined }); setMsg(r.ok ? 'Specialist ' + r.run_ref + ' requested' : r.message); if (r.ok) setQ(''); } catch (e) { setMsg(e.message); } setBusy(false); };
  const adopt = async (ref) => { setMsg(null); try { const r = await g.api('/api/specialists/' + ref + '/adopt', 'POST', {}); setMsg(r.ok ? 'Adopted as ' + r.plan_id + ' v' + r.plan_version + ' · ' + r.policy_decision : r.message); } catch (e) { setMsg(e.message); } };
  const tone = s => s === 'completed' ? C.mint : ['failed', 'cancelled'].includes(s) ? C.red : C.amber;
  return <Panel style={{ padding: 10 }}>
    <div className="flex justify-between items-baseline"><span style={{ font: '600 12px ' + SANS, color: C.text }}>CREAO specialists</span><span style={{ font: '600 9.5px ' + MONO, color: cli && cli.available ? C.mint : C.amber }}>{cli && cli.available ? agents.filter(a => a.status === 'ready').length + ' agents ready' : 'needs CLI key'}</span></div>
    <div style={{ font: '10px ' + MONO, color: C.dim }}>created on the fly for special cases · read + evaluate only</div>
    {list.map(s => <div key={s.run_ref} style={{ borderTop: '1px solid ' + C.line, marginTop: 8, paddingTop: 7 }}>
      <div className="flex justify-between gap-2"><span style={{ font: '600 11px ' + SANS, color: C.text }}>{(s.name || s.kind).replace('GRIDPILOT ', '')}</span><span style={{ font: '600 9.5px ' + MONO, color: tone(s.status) }}>{s.status.replace('_', ' ')}</span></div>
      <div style={{ font: '10px ' + MONO, color: C.dim }}>{s.run_ref}{s.creao_run_id ? ' · run ' + s.creao_run_id.slice(0, 8) : ''} · by {(s.requested_by || '').split(':')[0]}</div>
      {s.finding && <div style={{ font: '11px/1.5 ' + SANS, color: C.mute, marginTop: 4 }}>{s.finding}</div>}
      {s.avoid_racks && s.avoid_racks.length > 0 && <div style={{ font: '10px ' + MONO, color: C.amber, marginTop: 2 }}>avoid {s.avoid_racks.join(', ')}</div>}
      {s.evaluation && <div style={{ font: '10px/1.5 ' + MONO, color: C.text, marginTop: 4 }}>backend forecast: {s.evaluation.recover_s == null ? 'no recovery in 600 s' : 'recovers in ' + s.evaluation.recover_s + ' s'} · {s.evaluation.final_gpu_c}°C · ${s.evaluation.incremental_cost_usd} · {s.evaluation.policy_decision}</div>}
      {s.evaluation && canPlan && s.evaluation.policy_decision !== 'FORBIDDEN' && <div className="mt-1.5"><Btn small onClick={() => adopt(s.run_ref)}>Adopt as plan</Btn></div>}
    </div>)}
    {inc && inc.state !== 'CLOSED' && cli && cli.available && <div className="flex flex-col gap-1.5" style={{ borderTop: '1px solid ' + C.line, marginTop: 8, paddingTop: 8 }}>
      <select value={kind} onChange={e => setKind(e.target.value)} style={field}>{SPECIALIST_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      {kind === 'custom' && <textarea rows={3} value={mission} onChange={e => setMission(e.target.value)} onKeyDown={stop} placeholder="Mission for the new agent (what it must work out), at least 40 characters" style={{ ...field, resize: 'vertical' }} />}
      <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={stop} placeholder={'Question about ' + inc.id} style={field} />
      <Btn small disabled={busy || !q.trim() || (kind === 'custom' && mission.trim().length < 40)} onClick={ask}>{busy ? 'Requesting…' : 'Ask a specialist'}</Btn>
    </div>}
    {msg && <div style={{ font: '10.5px ' + MONO, color: C.mute, marginTop: 6 }}>{msg}</div>}
  </Panel>;
}

function AgentOps({ g, narrow }) {
  const st = g.state; const inc = st && st.incident; const evs = (st && st.events) || [];
  const [selE, setSelE] = React.useState(null);
  const runs = (inc && inc.agent_runs) || []; const calls = (inc && inc.calls) || []; const tools = (inc && inc.tool_calls) || [];
  const it = st && st.integrations;
  const graphTypes = ['state_transition', 'creao_run_started', 'creao_run_completed', 'creao_run_failed', 'creao_disconnected', 'creao_run_failed_to_start', 'agent_tool_call', 'voice_tool_call', 'call_started', 'call_failed', 'call_not_placed', 'call_status', 'operator_suggestion', 'approval_code_issued', 'usage_metered', 'invoice_reconciled', 'fault_injected', 'agent_report', 'demo_injection', 'specialist_requested', 'specialist_completed', 'specialist_recommendation_evaluated', 'creao_agent_creation_started', 'creao_agent_created', 'creao_agent_creation_failed', 'creao_agent_pending', 'specialist_unavailable', 'specialist_refused', 'specialist_timeout', 'agent_tool_refused'];
  const graph = evs.filter(e => graphTypes.includes(e.type) && (!inc || e.incident_id === inc.id || !e.incident_id)).slice(0, 120);
  const ev = graph.find(e => e.id === selE) || graph[0];
  const toolFor = ev && (ev.type === 'agent_tool_call' || ev.type === 'voice_tool_call') ? tools.filter(t => t.tool === ev.data.tool && Math.abs(t.ts - ev.ts) < 3).slice(-1)[0] : null;
  const plan = inc && inc.plans && inc.plans.slice(-1)[0]; const exec = inc && inc.executions && inc.executions.slice(-1)[0];
  const appr = inc && inc.approvals && inc.approvals.filter(a => a.status === 'approved').slice(-1)[0];
  const agent = (name, role, status, detail, ok) => <Panel style={{ padding: 10 }}><div className="flex justify-between items-start gap-2"><span style={{ font: '600 12px/1.35 ' + SANS, color: C.text }}>{name}</span><span className="gp-nw" style={{ font: '600 9.5px/1.6 ' + MONO, color: ok ? C.mint : ok === false ? C.red : C.amber }}>{status}</span></div><div style={{ font: '10px/1.45 ' + MONO, color: C.dim, marginTop: 2 }}>{role}</div><div style={{ font: '10.5px/1.5 ' + MONO, color: C.mute, marginTop: 4, overflowWrap: 'anywhere' }}>{detail}</div></Panel>;
  const lr = runs.slice(-1)[0]; const lc = calls.slice(-1)[0];
  return <div className={'flex-1 min-h-0 grid gap-3 p-3' + (narrow ? ' overflow-auto' : '')} style={{ gridTemplateColumns: narrow ? '1fr' : 'minmax(230px,.9fr) minmax(300px,1.3fr) minmax(300px,1.2fr)', gridAutoRows: narrow ? 'max-content' : undefined, background: C.bg }}>
    <div className="flex flex-col gap-2 overflow-auto">
      <Label>Active agents</Label>
      {agent('CREAO Remediation Agent', 'diagnosis · planning · finance reasoning · replanning · summaries', lr ? lr.status : (it && (it.creao.app_id_set || (it.creao_cli && it.creao_cli.available)) ? 'idle' : 'not connected'), lr ? lr.task + (lr.provider_run_id ? ' · run ' + lr.provider_run_id : '') + ' · ' + ago(lr.created) : 'one CREAO agent, invoked per task with a scoped tool token', lr ? lr.status === 'completed' : null)}
      <SpecialistsPanel g={g} inc={inc} />
      {agent('Finance engine', 'deterministic forecasts, rate cards, reconciliation', 'running', 'All arithmetic in backend code; CREAO explains and compares.', true)}
      {(() => { const opOk = it && it.cartesia.operator && it.cartesia.operator.consent; const stale = lc && lc.status === 'failed' && opOk && /operator/i.test(lc.data.error || ''); const useLc = lc && !stale;
        return agent('Cartesia Voice Operator', 'outbound call · Q&A · alternatives · approval intent', useLc ? lc.status : (it && it.cartesia.agent_id ? (opOk ? 'ready' : 'needs operator') : 'not set up'), useLc ? (lc.data.agent_call_id || lc.data.error || '') : (opOk ? 'Calls ' + it.cartesia.operator.name + ' ' + it.cartesia.operator.phone_masked + ' from +1 618-427-8064 when a plan needs approval' : 'Register the operator phone in Connections'), useLc ? lc.status !== 'failed' : (opOk ? true : null)); })()}
      {agent('Policy engine', 'deterministic authorization', 'enforcing', plan ? plan.plan_id + ' v' + plan.version + ': ' + plan.policy.decision : 'auto · approval · forbidden tiers', true)}
      {agent('Config executor', 'git config store · validation · rollback', exec ? exec.state : 'idle', exec ? (exec.data.applied_sha || '').slice(0, 10) + ' ' + (exec.data.rollback ? 'rolled back → ' + exec.data.rollback.sha.slice(0, 10) : '') : 'isolated worktrees, snapshots before apply', exec ? exec.state !== 'rolled_back' : null)}
      <Panel style={{ padding: 10 }}><Label color={C.cyan}>System evidence</Label>
        <div style={{ font: '10.5px/1.6 ' + SANS, color: C.mute, marginTop: 4 }}><div style={{ color: C.amber, font: '600 9.5px ' + MONO, letterSpacing: '.12em' }}>SIMULATED</div>{st && st.evidence.simulated.map(x => <div key={x}>· {x}</div>)}<div style={{ color: C.mint, font: '600 9.5px ' + MONO, letterSpacing: '.12em', marginTop: 6 }}>REAL</div>{st && st.evidence.real.map(x => <div key={x}>· {x}</div>)}</div></Panel>
    </div>
    <div className="flex flex-col min-h-0"><Label>Execution graph (newest first)</Label>
      <div className="overflow-auto mt-2 flex-1">{graph.map((e, i) => <button key={e.id} onClick={() => setSelE(e.id)} className="w-full text-left flex gap-2.5 items-start" style={{ padding: '5px 6px', borderRadius: 6, background: ev && ev.id === e.id ? 'rgba(67,217,245,.07)' : 'transparent' }}>
        <span style={{ marginTop: 5, width: 7, height: 7, borderRadius: 7, flexShrink: 0, background: e.type.includes('fail') || e.type.includes('not_placed') || (e.data.to && ['ROLLING_BACK', 'REPLAN', 'DENIED'].includes(e.data.to)) ? C.red : e.actor.startsWith('creao') ? C.cyan : e.actor.startsWith('cartesia') || e.actor.includes('voice') ? '#b49cff' : e.actor.startsWith('operator') ? C.amber : C.mint }} />
        <div className="min-w-0"><div style={{ font: '500 11.5px ' + SANS, color: C.text }}>{e.type === 'state_transition' ? e.data.from + ' → ' + e.data.to : e.type === 'agent_tool_call' || e.type === 'voice_tool_call' ? e.actor.split(':')[0] + ' called ' + e.data.tool : e.type.replace(/_/g, ' ')}</div>
          <div className="truncate" style={{ font: '10px ' + MONO, color: C.dim }}>t+{Math.round(e.sim_t)}s · {e.actor}{e.data.reason ? ' · ' + e.data.reason : ''}</div></div></button>)}
        {!graph.length && <div style={{ font: '11.5px ' + SANS, color: C.mute }}>No operations yet.</div>}</div></div>
    <div className="flex flex-col min-h-0"><Label>Evidence inspector</Label>
      <Panel className="mt-2 overflow-auto flex-1" style={{ padding: 12 }}>{ev ? <div className="flex flex-col gap-2" style={{ font: '11px/1.55 ' + MONO, color: C.text }}>
        {[['Incident', ev.incident_id || '—'], ['Event', ev.type + ' #' + ev.id], ['Actor', ev.actor], ['Timestamp', new Date(ev.ts * 1000).toLocaleString() + ' · sim t+' + ev.sim_t + 's'], ['Agent run', (ev.actor.startsWith('creao:') ? ev.actor.slice(6) : ev.data.creao_run_id || ev.data.run_ref || '—')], ['Plan', plan ? plan.plan_id + ' v' + plan.version : '—'], ['Approval identity', appr ? (appr.data.actor + ' via ' + appr.data.channel) : '—']].map(([k, v]) => <div key={k} className="grid" style={{ gridTemplateColumns: '120px 1fr' }}><span style={{ color: C.dim }}>{k}</span><span className="break-all">{v}</span></div>)}
        {toolFor && <><Label>Tool call input</Label><pre style={{ whiteSpace: 'pre-wrap', color: '#a9c3d9', margin: 0 }}>{JSON.stringify(toolFor.args, null, 1)}</pre><Label>Tool result</Label><pre style={{ whiteSpace: 'pre-wrap', color: '#9fb0c4', margin: 0, maxHeight: 220, overflow: 'auto' }}>{JSON.stringify(toolFor.result, null, 1)}</pre></>}
        <Label>Event data</Label><pre style={{ whiteSpace: 'pre-wrap', color: '#9fb0c4', margin: 0 }}>{JSON.stringify(ev.data, null, 1)}</pre>
        {exec && <><Label>Config diff ({exec.id})</Label><Diff text={exec.data.diff} />{exec.data.validation && <><Label>Validation output</Label>{exec.data.validation.map(c => <div key={c.name} style={{ color: c.ok ? C.mint : C.red }}>{c.ok ? '✓' : '✗'} {c.name}: {c.detail}</div>)}</>}</>}
        {runs.length > 0 && <><Label>CREAO runs</Label>{runs.map(r => <div key={r.id} style={{ color: C.mute }}>{r.id} · {r.task} · {r.status} · {r.provider_run_id || 'no provider id'}{r.data && r.data.structured_errors && r.data.structured_errors.length ? ' · output issues: ' + r.data.structured_errors.join(', ') : ''}</div>)}</>}
      </div> : <div style={{ color: C.mute, font: '11.5px ' + SANS }}>Select an operation.</div>}</Panel></div>
  </div>;
}

function Settings({ g, toast }) {
  const it = g.state && g.state.integrations;
  const [keys, setKeys] = React.useState([]); const [v, setV] = React.useState({}); const [test, setTest] = React.useState(null); const [busy, setBusy] = React.useState(false);
  const refresh = async () => { try { const l = await apiManager.list(); setKeys((l || []).map(k => typeof k === 'string' ? k : (k.name || k.key))); } catch (e) { } };
  React.useEffect(() => { refresh(); }, []);
  const saveKey = async (name, prefix) => { const val = (v[name] || '').trim(); if (!val.startsWith(prefix)) { toast('Expected a key starting with ' + prefix, 'err'); return; } await apiManager.save(name, val); setV(x => ({ ...x, [name]: '' })); refresh(); toast(name + ' saved to the vault', 'ok'); };
  const post = async (body) => { try { await g.api('/api/settings', 'POST', body); toast('Saved', 'ok'); } catch (e) { toast(e.message, 'err'); } };
  const inp = { background: C.p2, border: '1px solid ' + C.line2, color: C.text, font: '12px ' + MONO, height: 32, borderRadius: 8, padding: '0 10px', outline: 'none' };
  const badge = (ok, txt) => <span style={{ font: '600 10px ' + MONO, color: ok ? C.mint : C.amber }}>{ok ? '● ' : '○ '}{txt}</span>;
  const stop = e => e.stopPropagation();
  return <div className="flex-1 overflow-auto p-4" style={{ background: C.bg }}><div className="mx-auto flex flex-col gap-3" style={{ maxWidth: 900 }}>
    <Panel style={{ padding: 16 }}>
      <div className="flex justify-between items-baseline"><div><Label color={C.cyan}>CREAO · autonomous operations</Label><div style={{ font: '500 15px ' + SANS, color: C.text, marginTop: 4 }}>Remediation Agent</div></div>{badge(keys.includes('CREAO_API_KEY') && it && it.creao.app_id_set, keys.includes('CREAO_API_KEY') ? (it && it.creao.app_id_set ? 'key + app id set' : 'app id missing') : 'key missing')}</div>
      <ol style={{ font: '12px/1.7 ' + SANS, color: C.mute, marginTop: 8, paddingLeft: 18, listStyle: 'decimal' }}>
        <li>Open <a href="https://agent.creao.ai" target="_blank" rel="noreferrer" style={{ color: C.cyan }}>agent.creao.ai</a>, start a new chat, paste the agent brief below, then click <b>Create Agent</b>. Make sure the input form has a single long-text field named <code>request</code>.</li>
        <li>On the agent page, open API Trigger, create a key (starts with <code>capi_</code>) and copy the agent's App ID (the UUID in its API URL).</li>
        <li>Paste both here, then press Test connections.</li></ol>
      <div className="mt-2 flex gap-2"><input type="password" autoComplete="off" placeholder={keys.includes('CREAO_API_KEY') ? 'capi_… (saved, paste to rotate)' : 'capi_…'} value={v.CREAO_API_KEY || ''} onChange={e => setV(x => ({ ...x, CREAO_API_KEY: e.target.value }))} onKeyDown={stop} style={{ ...inp, flex: 1 }} /><Btn onClick={() => saveKey('CREAO_API_KEY', 'capi_')} disabled={!v.CREAO_API_KEY}>Save key</Btn></div>
      <div className="mt-2 flex gap-2"><input placeholder={it && it.creao.app_id ? it.creao.app_id : 'App ID (UUID)'} value={v.app || ''} onChange={e => setV(x => ({ ...x, app: e.target.value }))} onKeyDown={stop} style={{ ...inp, flex: 1 }} /><Btn onClick={() => post({ creao_app_id: v.app })} disabled={!v.app}>Save App ID</Btn></div>
      <details className="mt-3"><summary style={{ font: '600 11.5px ' + SANS, color: C.cyan, cursor: 'pointer' }}>Agent brief to paste into CREAO</summary>
        <pre style={{ whiteSpace: 'pre-wrap', font: '11px/1.55 ' + MONO, color: '#b7c7d8', background: C.p2, padding: 12, borderRadius: 8, marginTop: 8 }}>{BRIEF}</pre>
        <Btn small onClick={async () => { const r = await copyText(BRIEF); toast(r === 'failed' ? 'Copy failed' : 'Brief copied', r === 'failed' ? 'err' : 'ok'); }}>Copy brief</Btn></details>
    </Panel>
    <Panel style={{ padding: 16 }}>
      <div className="flex justify-between items-baseline"><div><Label color={C.cyan}>CREAO CLI · agents on the fly</Label><div style={{ font: '500 15px ' + SANS, color: C.text, marginTop: 4 }}>Specialist agent factory</div></div>{badge(keys.includes('CREAO_CLI_KEY') && it && it.creao_cli && it.creao_cli.available, keys.includes('CREAO_CLI_KEY') ? 'key saved' : 'key missing')}</div>
      <div style={{ font: '12px/1.6 ' + SANS, color: C.mute, marginTop: 6 }}>An account key from <a href="https://developer.creao.ai" target="_blank" rel="noreferrer" style={{ color: C.cyan }}>developer.creao.ai</a> (API Keys, starts with <code>cr_sk_</code>) lets GRIDPILOT create CREAO agents itself. Special cases (a failed remediation, a placement that fails validation, an invoice that does not reconcile, protected customers in breach) get a purpose-built specialist; the Remediation Agent and you can request new ones. With this key the Remediation Agent is also created automatically, so no App ID is needed.</div>
      <div className="mt-2 flex gap-2"><input type="password" autoComplete="off" placeholder={keys.includes('CREAO_CLI_KEY') ? 'cr_sk_… (saved, paste to rotate)' : 'cr_sk_…'} value={v.CREAO_CLI_KEY || ''} onChange={e => setV(x => ({ ...x, CREAO_CLI_KEY: e.target.value }))} onKeyDown={stop} style={{ ...inp, flex: 1 }} /><Btn onClick={() => saveKey('CREAO_CLI_KEY', 'cr_sk_')} disabled={!v.CREAO_CLI_KEY}>Save key</Btn>
        <Btn disabled={busy || !keys.includes('CREAO_CLI_KEY')} onClick={async () => { setBusy(true); try { const r = await g.api('/api/specialists/prepare', 'POST', {}); toast(r.ok ? 'Agent creation started in CREAO' : r.message, r.ok ? 'ok' : 'err'); } catch (e) { toast(e.message, 'err'); } setBusy(false); }}>Create agents now</Btn></div>
      {it && it.creao_cli && it.creao_cli.agents.length > 0 && <div className="mt-3 flex flex-col gap-1" style={{ font: '11px/1.5 ' + MONO }}>{it.creao_cli.agents.map(a => <div key={a.key} style={{ color: a.status === 'ready' ? C.mint : a.status === 'failed' ? C.red : C.amber }}>{a.status === 'ready' ? '●' : '○'} {a.name} · {a.status}{a.agent_id ? ' · ' + a.agent_id.slice(0, 8) : ''}</div>)}</div>}
    </Panel>
    <Panel style={{ padding: 16 }}>
      <div className="flex justify-between items-baseline"><div><Label color={C.cyan}>Cartesia · voice intervention</Label><div style={{ font: '500 15px ' + SANS, color: C.text, marginTop: 4 }}>Managed voice agent and operator line</div></div>{badge(keys.includes('CARTESIA_API_KEY'), keys.includes('CARTESIA_API_KEY') ? 'key saved' : 'key missing')}</div>
      <div style={{ font: '12px/1.6 ' + SANS, color: C.mute, marginTop: 6 }}>Standard key from <a href="https://play.cartesia.ai/keys" target="_blank" rel="noreferrer" style={{ color: C.cyan }}>play.cartesia.ai/keys</a> (sk_car_…). Test connections creates the voice agent, its webhook tools and a US phone number for you.</div>
      <div className="mt-2 flex gap-2"><input type="password" autoComplete="off" placeholder={keys.includes('CARTESIA_API_KEY') ? 'sk_car_… (saved, paste to rotate)' : 'sk_car_…'} value={v.CARTESIA_API_KEY || ''} onChange={e => setV(x => ({ ...x, CARTESIA_API_KEY: e.target.value }))} onKeyDown={stop} style={{ ...inp, flex: 1 }} /><Btn onClick={() => saveKey('CARTESIA_API_KEY', 'sk_car_')} disabled={!v.CARTESIA_API_KEY}>Save key</Btn></div>
      <div className="mt-3" style={{ font: '600 11.5px ' + SANS, color: C.text }}>Operator {it && it.cartesia.operator.name ? '· ' + it.cartesia.operator.name + ' ' + it.cartesia.operator.phone_masked : ''}</div>
      <div className="mt-1.5 flex gap-2 flex-wrap"><input placeholder="Name" value={v.opn || ''} onChange={e => setV(x => ({ ...x, opn: e.target.value }))} onKeyDown={stop} style={{ ...inp, width: 160 }} /><input placeholder="+14155550123" value={v.opp || ''} onChange={e => setV(x => ({ ...x, opp: e.target.value }))} onKeyDown={stop} style={{ ...inp, width: 170 }} />
        <label className="flex items-center gap-2" style={{ font: '11.5px ' + SANS, color: C.mute }}><input type="checkbox" checked={!!v.opc} onChange={e => setV(x => ({ ...x, opc: e.target.checked }))} />consents to automated AI calls</label>
        <Btn disabled={!v.opp || !v.opc} onClick={() => post({ operator: { name: v.opn || 'Operator', phone: (v.opp || '').trim(), consent: !!v.opc } })}>Register operator</Btn></div>
      <label className="flex items-center gap-2 mt-3" style={{ font: '11.5px ' + SANS, color: C.mute }}><input type="checkbox" checked={it ? it.cartesia.auto_call : true} onChange={e => post({ auto_call: e.target.checked })} />Call the operator automatically when a plan needs approval</label>
    </Panel>
    <Panel style={{ padding: 16 }}>
      <div className="flex items-center justify-between"><div><Label>Verification</Label><div style={{ font: '12px ' + SANS, color: C.mute, marginTop: 4 }}>Calls each provider with your saved keys through Mana's server-side proxy. Keys never reach this page or the backend process.</div></div>
        <Btn tone="primary" disabled={busy} onClick={async () => { setBusy(true); try { setTest(await g.api('/api/integrations/test', 'POST', {})); } catch (e) { toast(e.message, 'err'); } setBusy(false); }}>{busy ? 'Testing…' : 'Test connections'}</Btn></div>
      {test && <div className="mt-3 flex flex-col gap-1" style={{ font: '11.5px/1.5 ' + MONO }}>{Object.entries(test).map(([k, r]) => <div key={k} style={{ color: r.ok ? C.mint : C.red }}>{r.ok ? '✓' : '✗'} {k}: {r.detail}{r.setup ? ' · agent ' + r.setup.agent_id + ' · number ' + r.setup.from_number_id : ''}</div>)}</div>}
    </Panel>
  </div></div>;
}

function UserDiv() {
  const g = useGridpilot();
  const rootRef = React.useRef(null); const [W, setW] = React.useState(1400); const [fs, setFs] = React.useState(false);
  React.useEffect(() => { const el = rootRef.current; if (!el) return; const ro = new ResizeObserver(() => setW(el.clientWidth || 1400)); ro.observe(el); const onFs = () => setFs(document.fullscreenElement === el); document.addEventListener('fullscreenchange', onFs); return () => { ro.disconnect(); document.removeEventListener('fullscreenchange', onFs); }; });
  const narrow = W < 1180;
  const toggleFs = async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await rootRef.current.requestFullscreen(); } catch (e) { } };
  const [tab, setTab] = React.useState('control'); const [modal, setModal] = React.useState(null); const [toasts, setToasts] = React.useState([]); const [demoOpen, setDemoOpen] = React.useState(false);
  const [rej, setRej] = React.useState(''); const [diffs, setDiffs] = React.useState({});
  const toast = React.useCallback((msg, kind) => { const id = Math.random(); setToasts(t => [...t, { id, msg, kind }]); setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4200); }, []);
  React.useEffect(() => { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap'; document.head.appendChild(l); return () => l.remove(); }, []);
  const snap = g.snapRef.current; const st = g.state; const inc = st && st.incident;
  const tot = snap && snap.totals;
  const breaches = snap ? Object.values(snap.latency).filter(v => v.breach).length : 0;
  const crit = snap ? snap.racks.filter(r => r.status === 'critical').length : 0, elev = snap ? snap.racks.filter(r => r.status === 'elevated').length : 0;
  const health = crit ? ['CRITICAL', C.red] : elev ? ['DEGRADED', C.amber] : ['HEALTHY', C.mint];
  const exposure = inc && inc.state !== 'CLOSED' && inc.sla_exposure_now ? inc.sla_exposure_now.usd_per_min_if_sustained : 0;
  const demo = async (kind) => { setDemoOpen(false); try { if (kind === 'reset') await g.api('/api/simulation/reset', 'POST', {}); else await g.api('/api/simulation/inject', 'POST', { kind }); toast(kind === 'reset' ? 'Demo reset to baseline' : 'Injected: ' + kind.replace(/_/g, ' '), 'ok'); } catch (e) { toast(e.message, 'err'); } };
  const speed = async (s) => { try { await g.api('/api/simulation/speed', 'POST', { speed: s }); } catch (e) { toast(e.message, 'err'); } };
  const plan = inc && inc.plans && inc.plans.slice(-1)[0]; const exec = inc && inc.executions && inc.executions.slice(-1)[0];
  React.useEffect(() => { if (modal && modal.kind === 'diff' && st) { (st.config_log || []).slice(0, 6).forEach(async c => { if (diffs[c.sha]) return; try { const r = await g.api('/api/config/diff/' + c.sha); setDiffs(d => ({ ...d, [c.sha]: r.diff })); } catch (e) { } }); } }, [modal]);
  if (g.error) return <div className="w-full p-6" style={{ background: C.bg, color: C.red, font: '13px ' + MONO }}>Backend unreachable: {g.error.message}</div>;
  return <div ref={rootRef} className="gp-root w-full relative flex flex-col overflow-hidden" style={{ height: fs ? '100vh' : 'var(--mana-app-height, 82vh)', background: C.bg, color: C.text, fontFamily: SANS, backgroundImage: 'linear-gradient(rgba(67,217,245,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(67,217,245,0.025) 1px, transparent 1px)', backgroundSize: '28px 28px' }}>
    <style>{'.gp-root .gp-nw{white-space:nowrap} .gp-root button{white-space:nowrap}'}</style>
    <div className="flex items-center flex-wrap gap-x-4 gap-y-1.5 px-4 py-2 flex-shrink-0" style={{ minHeight: 52, borderBottom: '1px solid ' + C.line, background: 'rgba(10,15,25,.9)' }}>
      <div className="flex items-center gap-2"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="4" stroke={C.cyan} strokeWidth="1.6" /><path d="M7 15l3-3 3 2 4-5" stroke={C.cyan} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /><circle cx="17" cy="9" r="1.6" fill={C.red} /></svg>
        <span style={{ font: '600 13px ' + SANS, letterSpacing: '.14em' }}>GRIDPILOT</span></div>
      <span className="gp-nw" style={{ font: '500 10.5px ' + MONO, color: C.mute, letterSpacing: '.12em' }}>{narrow ? '' : 'ATLAS COMPUTE · SFO-01 · '}<span style={{ color: g.conn === 'live' ? C.mint : C.amber }}>{g.conn === 'live' ? 'SIMULATION LIVE' : g.conn.toUpperCase()}</span></span>
      <div className="flex gap-1 flex-wrap">{[['control', 'Control Room'], ['finops', narrow ? 'Finance' : 'Finance desk'], ['agents', narrow ? 'Agents' : 'Agent Operations'], ['settings', 'Connections']].map(([k, l]) => <button key={k} onClick={() => setTab(k)} style={{ font: '600 11.5px ' + SANS, padding: '6px 11px', borderRadius: 7, color: tab === k ? C.text : C.mute, background: tab === k ? 'rgba(67,217,245,.1)' : 'transparent', border: '1px solid ' + (tab === k ? 'rgba(67,217,245,.3)' : 'transparent') }}>{l}</button>)}</div>
      <div className="ml-auto flex items-center flex-wrap gap-x-4 gap-y-1 gp-hdr" style={{ font: '500 10.5px ' + MONO, color: C.mute }}>
        <span className="gp-nw">HEALTH <b style={{ color: health[1] }}>{health[0]}</b></span>
        <span className="gp-nw">INCIDENT <b style={{ color: inc && inc.state !== 'CLOSED' ? C.red : C.mint }}>{inc && inc.state !== 'CLOSED' ? inc.severity + ' ' + inc.id : 'NONE'}</b></span>
        <span className="gp-nw">T+{snap ? fmtT(snap.t) : '--:--'}</span>
        <Btn small onClick={toggleFs} title="Full screen (Esc to exit). Best for demos.">{fs ? '⤡ Exit full screen' : '⤢ Full screen'}</Btn>
        {!narrow && <span className="gp-nw">MODE <b style={{ color: C.cyan }}>AUTONOMOUS · HUMAN-GATED</b></span>}
        {userId === ownerId ? <span className="gp-nw" title="Verified Mana identity used for console approvals">OP {userId.slice(0, 6)}</span> : <span title="Live view for visitors. Approvals, payments and settings belong to the operator." style={{ color: C.amber }}>VISITOR · LIVE VIEW</span>}
        <div className="relative"><Btn small onClick={() => setDemoOpen(!demoOpen)}>Demo ▾</Btn>
          {demoOpen && <div className="absolute right-0 mt-2 z-50 flex flex-col py-1" style={{ width: 230, background: C.p2, border: '1px solid ' + C.line2, borderRadius: 10, boxShadow: '0 20px 50px rgba(0,0,0,.5)' }}>
            {[['thermal', 'Start thermal incident (C02 degrades)'], ['arm_post_apply_failure', 'Arm post-apply failure (rollback demo)'], ['repair_c02', 'Repair C02'], ['repair_rack_faults', 'Repair rack fan faults'], ['reset', 'Reset demo']].map(([k, l]) => <button key={k} onClick={() => demo(k)} className="text-left px-3 py-2 hover:bg-white/5" style={{ font: '11.5px ' + SANS, color: k === 'reset' ? C.amber : C.text }}>{l}</button>)}
            <div className="px-3 py-2 flex gap-1 items-center" style={{ borderTop: '1px solid ' + C.line }}><span style={{ font: '10px ' + MONO, color: C.dim }}>speed</span>{[1, 2, 4, 8].map(s => <button key={s} onClick={() => speed(s)} style={{ font: '10.5px ' + MONO, padding: '2px 7px', borderRadius: 5, color: st && st.speed === s ? '#04121a' : C.mute, background: st && st.speed === s ? C.cyan : 'transparent' }}>{s}×</button>)}</div>
            {st && st.fail_armed && <div className="px-3 pb-2" style={{ font: '10px ' + MONO, color: C.amber }}>failure armed for next apply</div>}
          </div>}</div>
      </div>
    </div>
    <div className="flex flex-wrap flex-shrink-0" style={{ borderBottom: '1px solid ' + C.line, background: 'rgba(16,24,38,.6)' }}>
      <Metric label="IT · facility power" value={tot ? tot.it_kw.toFixed(0) : '—'} unit={'kW · ' + (tot ? tot.facility_kw.toFixed(0) : '—') + ' kW'} sub="64 racks · 512 GPUs (simulated)" />
      <Metric label="GPU utilization" value={tot ? (tot.gpu_util * 100).toFixed(1) : '—'} unit="%" sub={crit + ' critical · ' + elev + ' elevated racks'} tone={crit ? C.red : undefined} />
      <Metric label="PUE" value={tot ? tot.pue.toFixed(3) : '—'} unit="" sub={snap ? 'C02 effectiveness ' + Math.round(snap.cooling[1].effectiveness * 100) + '%' : ''} tone={tot && tot.pue > 1.41 ? C.amber : undefined} />
      <Metric label="SLA risk · exposure" value={breaches} unit={breaches === 1 ? 'breach' : 'breaches'} sub={exposure ? fmt$(exposure) + '/min if sustained (conditional)' : 'no conditional exposure'} tone={breaches ? C.red : C.mint} />
    </div>
    {tab === 'control' && <ControlRoom g={g} setModal={setModal} toast={toast} narrow={W < 900} />}
    {tab === 'finops' && <FinanceDesk g={g} toast={toast} narrow={W < 860} />}
    {tab === 'agents' && <AgentOps g={g} narrow={W < 980} />}
    {tab === 'settings' && <Settings g={g} toast={toast} />}
    <CallOverlay g={g} />
    {modal && modal.kind === 'diff' && <Modal wide title={'Configuration changes' + (plan ? ' · ' + plan.plan_id + ' v' + plan.version : '')} onClose={() => setModal(null)}>
      {exec && exec.plan_version === (plan && plan.version) ? <div className="flex flex-col gap-3"><Label color={C.cyan}>Actual git diff · execution {exec.id} · {exec.state}</Label><Diff text={exec.data.diff} />
        {exec.data.rollback && <><Label color={C.red}>Rollback diff · {exec.data.rollback.sha.slice(0, 10)} · verified {String(exec.data.rollback.verified)}</Label><Diff text={exec.data.rollback.diff} /></>}</div>
        : <div className="flex flex-col gap-2"><Label color={C.amber}>Planned changes (no candidate built yet)</Label><div style={{ font: '12px/1.6 ' + SANS, color: C.mute }}>The real diff is produced when execution builds a candidate in an isolated git worktree. Planned edits:</div>{plan && plan.actions.map((a, i) => <div key={i} style={{ font: '12px ' + MONO }}>› {actionText(a)}</div>)}</div>}
      <div className="mt-5"><Label>Config store history</Label>{(st.config_log || []).map(c => <details key={c.sha} className="mt-1"><summary style={{ font: '11px ' + MONO, color: C.text, cursor: 'pointer' }}>{c.short} · {c.message}</summary><Diff text={diffs[c.sha]} /></details>)}</div>
    </Modal>}
    {modal && modal.kind === 'reject' && <Modal title="Reject plan" onClose={() => setModal(null)}>
      <div style={{ font: '12px ' + SANS, color: C.mute }}>The reason goes to CREAO for a revised plan.</div>
      <textarea value={rej} onChange={e => setRej(e.target.value)} onKeyDown={e => e.stopPropagation()} rows={3} className="w-full mt-2 p-2 rounded outline-none" style={{ background: C.p1, border: '1px solid ' + C.line2, color: C.text, font: '12px ' + SANS }} />
      <div className="flex gap-2 mt-3 justify-end"><Btn onClick={() => setModal(null)}>Cancel</Btn><Btn tone="danger" onClick={async () => { try { await g.api('/api/incidents/' + inc.id + '/reject', 'POST', { reason: rej || 'rejected from console' }); toast('Plan rejected', 'ok'); } catch (e) { toast(e.message, 'err'); } setModal(null); setRej(''); }}>Reject plan</Btn></div>
    </Modal>}
    <div className="absolute left-1/2 -translate-x-1/2 bottom-5 z-50 flex flex-col gap-2 items-center pointer-events-none">{toasts.map(t => <div key={t.id} style={{ background: C.p2, border: '1px solid ' + (t.kind === 'err' ? 'rgba(255,89,110,.5)' : 'rgba(79,224,165,.45)'), color: t.kind === 'err' ? C.red : C.mint, font: '12px ' + SANS, padding: '8px 14px', borderRadius: 999, boxShadow: '0 10px 30px rgba(0,0,0,.4)' }}>{t.msg}</div>)}</div>
  </div>;
}
