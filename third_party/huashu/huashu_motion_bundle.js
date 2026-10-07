// ===== huashu util.js =====
// 通用工具（最先加载，转场库与引擎共用）
(() => {
// ---------- 工具 ----------
window.U = {
  TAU: Math.PI * 2,
  clamp: (x, a = 0, b = 1) => Math.max(a, Math.min(b, x)),
  lerp: (a, b, p) => a + (b - a) * p,
  // smoothstep：x 在 [a,b] 内平滑地从 0 走到 1（开场写出、渐显、段内节奏都用它）
  ss: (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); },
  // 把 p 的 [a,b] 段线性拉成 0..1（转场里分阶段用）
  seg: (p, a, b) => Math.max(0, Math.min(1, (p - a) / (b - a))),
  // 步进帧率：把连续时间量化成 fps 步——「一顿一顿」是很多风格的味道本身：
  // 橡皮管卡通 12fps（一拍二）、皮影杆操 10fps、哈林跳舞小人 8fps 硬切、手绘线条沸腾 8–12fps、胶片层 24fps。
  stepTime: (t, fps) => Math.floor(t * fps) / fps,
  ease: {
    linear: p => p,
    inOut: p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2,
    out: p => 1 - Math.pow(1 - p, 3),
    in: p => p * p * p,
    outBack: p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
  },
  // 种子随机：mulberry32
  rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; },
  hash(i, j = 0) { let h = (i * 374761393 + j * 668265263) | 0; h = (h ^ (h >>> 13)) * 1274126177; return ((h ^ (h >>> 16)) >>> 0) / 4294967296; },
  // 点列 → Path2D（折线）
  poly(pts, closed = true) { const p = new Path2D(); pts.forEach((q, i) => i ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1])); if (closed) p.closePath(); return p; },
};


U.fontFamily = font => { const m = /"([^"]+)"/.exec(font) || /'([^']+)'/.exec(font); return m ? m[1] : String(font).replace(/^(italic\s+|bold\s+|\d+\s+)*[\d.]+px\s+/, '').trim(); };


// ===== huashu paint.js =====
// 纯代码绘画工具库：噪声、纹理、手绘线、风格渲染器（笔触/色点/马赛克/网点/像素/切面）、共享编舞。
// 约定：所有随机都用种子；「抖动线条」按 boilFps 换种子（手绘动画的 boiling line），其余保持帧间稳定。
(() => {
const W = 1920, H = 1080;
const P = window.PAINT = {};
const { clamp, lerp, ease, rng } = U;

// ---------- 画布 ----------
P.canvas = (w = W, h = H) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
const CACHE = {};
P.cached = (key, w, h, fn) => { if (!CACHE[key]) { const c = P.canvas(w, h); fn(c.getContext('2d'), c); CACHE[key] = c; } return CACHE[key]; };
P.scratch = (key) => { if (!CACHE['s_' + key]) CACHE['s_' + key] = P.canvas(); return CACHE['s_' + key]; };

// ---------- 噪声 ----------
const perm = new Uint8Array(512); { const r = rng(1337); const p = [...Array(256).keys()]; for (let i = 255; i > 0; i--) { const j = (r() * (i + 1)) | 0; [p[i], p[j]] = [p[j], p[i]]; } for (let i = 0; i < 512; i++) perm[i] = p[i & 255]; }
const fade = t => t * t * t * (t * (t * 6 - 15) + 10);
const grad = (h, x, y) => { const u = (h & 1) ? x : -x, v = (h & 2) ? y : -y; return (h & 4) ? u + v * 0.5 : u * 0.5 + v; };
P.noise = (x, y) => {                                   // Perlin 2D，约 [-1,1]
  const X = Math.floor(x) & 255, Y = Math.floor(y) & 255; x -= Math.floor(x); y -= Math.floor(y);
  const u = fade(x), v = fade(y), a = perm[X] + Y, b = perm[X + 1] + Y;
  return lerp(lerp(grad(perm[a], x, y), grad(perm[b], x - 1, y), u), lerp(grad(perm[a + 1], x, y - 1), grad(perm[b + 1], x - 1, y - 1), u), v);
};
P.fbm = (x, y, o = 4) => { let s = 0, a = 0.5, f = 1; for (let i = 0; i < o; i++) { s += a * P.noise(x * f, y * f); a *= 0.5; f *= 2; } return s; };

// ---------- 颜色 ----------
P.hex = h => { h = h.replace('#', ''); if (h.length === 3) h = h.split('').map(x => x + x).join(''); const n = parseInt(h, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
P.rgb = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
P.mix = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
P.jitter = (c, r, amt) => [c[0] + (r() - .5) * amt, c[1] + (r() - .5) * amt, c[2] + (r() - .5) * amt];

// ---------- 纹理（缓存） ----------
// 纸/墙/岩石：底色 + 多层噪声斑驳 + 细颗粒
P.texture = (key, base, opt = {}) => P.cached('tex_' + key, W, H, (g) => {
  const { scale = 0.004, amt = 26, grain = 14, dark = null, seed = 1 } = opt;
  const img = g.createImageData(W, H), d = img.data, b = P.hex(base), r = rng(seed);
  const dk = dark ? P.hex(dark) : null;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const n = P.fbm(x * scale + seed, y * scale, 5), gr = (r() - .5) * grain;
    const i = (y * W + x) * 4;
    let c = b;
    if (dk) c = P.mix(b, dk, clamp(n * 1.2 + 0.25));
    d[i] = c[0] + n * amt + gr; d[i + 1] = c[1] + n * amt + gr; d[i + 2] = c[2] + n * amt * 0.8 + gr; d[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
});
// 颗粒噪点叠层（扁平插画的 grain、旧印刷）：透明底上的随机点
P.grain = (key, density = 0.08, col = [0, 0, 0], alpha = 0.18) => P.cached('grain_' + key, W, H, (g) => {
  const img = g.createImageData(W, H), d = img.data, r = rng(key.length * 97 + 3);
  for (let i = 0; i < W * H; i++) if (r() < density) { d[i * 4] = col[0]; d[i * 4 + 1] = col[1]; d[i * 4 + 2] = col[2]; d[i * 4 + 3] = 255 * alpha * (0.4 + r() * 0.6); }
  g.putImageData(img, 0, 0);
});
// 龟裂纹（古典油画）：随机游走的细裂线网
P.craquelure = (key, alpha = 0.35) => P.cached('crack_' + key, W, H, (g) => {
  const r = rng(77); g.strokeStyle = `rgba(40,25,10,${alpha})`; g.lineWidth = 0.8;
  for (let k = 0; k < 900; k++) {
    let x = r() * W, y = r() * H, a = r() * Math.PI * 2; g.beginPath(); g.moveTo(x, y);
    for (let s = 0; s < 6 + r() * 10; s++) { a += (r() - .5) * 1.6; x += Math.cos(a) * (6 + r() * 14); y += Math.sin(a) * (6 + r() * 14); g.lineTo(x, y); }
    g.stroke();
  }
});

// ---------- 手绘线 ----------
// 把折线/曲线采样后加噪声抖动；boil = 当前时间换种子的频率（0 = 不抖）
P.boilSeed = (t, fps = 12) => Math.floor(t * fps);
P.roughPath = (c, pts, { amp = 2, seed = 1, closed = false, step = 6 } = {}) => {
  const r = rng(seed);
  // 先按 step 重采样
  const out = [];
  const N = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < N; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.round(L / step));
    for (let k = 0; k < n; k++) { const q = k / n; out.push([lerp(a[0], b[0], q) + (r() - .5) * amp, lerp(a[1], b[1], q) + (r() - .5) * amp]); }
  }
  if (!closed) out.push([pts[pts.length - 1][0] + (r() - .5) * amp, pts[pts.length - 1][1] + (r() - .5) * amp]);
  c.beginPath(); out.forEach((p, i) => i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])); if (closed) c.closePath();
};
// 三次贝塞尔采样成点列（便于 roughPath / 变形）
P.bez = (p0, p1, p2, p3, n = 24) => { const o = []; for (let i = 0; i <= n; i++) { const t = i / n, u = 1 - t; o.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]); } return o; };
// SVG path 字符串 → Path2D（写复杂轮廓最省事）
P.path = (d) => new Path2D(d);

// ---------- 风格渲染器（对一张底稿 canvas 做「重画」） ----------
// 1) 流场笔触（梵高/印象派）：在抖动网格上取底稿颜色，沿 angle(x,y,t) 画短笔触，带深色描边
//    cap：宽笔（width ≥ 30）密排时圆头会连成一排排「人头/鹅卵石」（迁移测试 D 蓝色时期），改 'butt'。
//    boil：只给母题区域沸腾，静止区（地板、道具、角色）boil=0 种子固定——全屏 8fps 换种子读成满屏闪烁（迁移测试 C 莫奈）。
P.strokes = (dst, src, { cell = 12, len = 22, width = 7, angle, seed = 1, t = 0, boil = 8, outline = 0.35, outlineCol = [20, 25, 60], outlineMix = 0.55, jitterCol = 18, mask, alphaMask = false, palette, cap = 'round' } = {}) => {
  const sd = src.getContext('2d').getImageData(0, 0, W, H).data;
  const r = rng(seed * 1000 + P.boilSeed(t, boil));
  dst.lineCap = cap;
  for (let y = 0; y < H + cell; y += cell) for (let x = 0; x < W + cell; x += cell) {
    const px = x + (r() - .5) * cell, py = y + (r() - .5) * cell;
    const ix = clamp(px | 0, 0, W - 1), iy = clamp(py | 0, 0, H - 1), i = (iy * W + ix) * 4;
    if (mask && !mask(px, py)) continue;
    if (alphaMask && sd[i + 3] < 200) continue;
    let col = [sd[i], sd[i + 1], sd[i + 2]];
    const pc = palette ? palette(px, py, col, r) : null; if (pc) col = pc;       // 区域色板：从色组里抽一个颜色
    const a = angle ? angle(px, py, t) : 0, l = len * (0.7 + r() * 0.6);
    const dx = Math.cos(a) * l / 2, dy = Math.sin(a) * l / 2;
    if (outline > 0) { dst.strokeStyle = P.rgb(P.mix(col, outlineCol, outlineMix), outline); dst.lineWidth = width + 2.5; dst.beginPath(); dst.moveTo(px - dx, py - dy); dst.lineTo(px + dx, py + dy); dst.stroke(); }
    dst.strokeStyle = P.rgb(pc ? col : P.jitter(col, r, jitterCol)); dst.lineWidth = width;
    dst.beginPath(); dst.moveTo(px - dx, py - dy); dst.lineTo(px + dx, py + dy); dst.stroke();
  }
};
// 色组工具：给一组 hex 和权重，返回按随机数抽色的函数（配 strokes 的 palette 用）；mixBase 把底稿色混进来保留明暗
P.swatch = (hexes, mixBase = 0.35) => { const cs = hexes.map(P.hex); return (col, r) => { const k = cs[(r() * cs.length) | 0]; return P.mix(k, col, mixBase); }; };
// 2) 色点（印象派）：椭圆短点，颜色带冷暖偏移、大小不一。注意这不是点彩——修拉的点彩是「等大点＋纯色板视觉混色」，用 P.pointillism（lib/render.js）
P.dabs = (dst, src, { cell = 10, size = 9, seed = 2, t = 0, boil = 8, angle = -0.6, shift = 22 } = {}) => {
  const sd = src.getContext('2d').getImageData(0, 0, W, H).data, r = rng(seed * 1000 + P.boilSeed(t, boil));
  for (let y = 0; y < H + cell; y += cell) for (let x = 0; x < W + cell; x += cell) {
    const px = x + (r() - .5) * cell * 1.2, py = y + (r() - .5) * cell * 1.2;
    const i = (clamp(py | 0, 0, H - 1) * W + clamp(px | 0, 0, W - 1)) * 4;
    const warm = r() < 0.5 ? [shift, shift * 0.5, -shift] : [-shift * 0.6, 0, shift];
    dst.fillStyle = `rgb(${sd[i] + warm[0]},${sd[i + 1] + warm[1]},${sd[i + 2] + warm[2]})`;
    dst.beginPath(); dst.ellipse(px, py, size * (0.8 + r() * 0.6), size * 0.45, angle + (r() - .5) * 0.5, 0, Math.PI * 2); dst.fill();
  }
};
// 3) 马赛克：带缝隙的小石块，颜色取中心，略有抖动与倒角
P.mosaic = (dst, src, { tile = 14, grout = '#3b352e', seed = 3, jitter = 0.25 } = {}) => {
  const sd = src.getContext('2d').getImageData(0, 0, W, H).data, r = rng(seed);
  dst.fillStyle = grout; dst.fillRect(0, 0, W, H);
  for (let y = 0; y < H; y += tile) { const off = (y / tile) % 2 ? tile * 0.5 * jitter : 0; for (let x = -tile; x < W; x += tile) {
    const cx = x + off + tile / 2, cy = y + tile / 2, i = (clamp(cy | 0, 0, H - 1) * W + clamp(cx | 0, 0, W - 1)) * 4;
    const c = P.jitter([sd[i], sd[i + 1], sd[i + 2]], r, 14), s = tile * (0.82 + r() * 0.1);
    dst.fillStyle = P.rgb(c); dst.fillRect(cx - s / 2 + (r() - .5) * 1.5, cy - s / 2 + (r() - .5) * 1.5, s, s);
    dst.fillStyle = 'rgba(255,255,255,.12)'; dst.fillRect(cx - s / 2, cy - s / 2, s, 2);
  } }
};
// 4) 网点（本戴点）：颜色取中心，半径随暗度
P.halftone = (dst, src, { cell = 14, paper = '#f4ecd8', seed = 4 } = {}) => {
  const sd = src.getContext('2d').getImageData(0, 0, W, H).data;
  dst.fillStyle = paper; dst.fillRect(0, 0, W, H);
  for (let y = 0; y < H + cell; y += cell) for (let x = 0; x < W + cell; x += cell) {
    const cx = x + ((y / cell) % 2) * cell / 2, i = (clamp(y | 0, 0, H - 1) * W + clamp(cx | 0, 0, W - 1)) * 4;
    const L = (sd[i] * .3 + sd[i + 1] * .59 + sd[i + 2] * .11) / 255;
    dst.fillStyle = `rgb(${sd[i]},${sd[i + 1]},${sd[i + 2]})`;
    dst.beginPath(); dst.arc(cx, y, cell * 0.72 * (0.3 + (1 - L) * 0.9), 0, Math.PI * 2); dst.fill();
  }
};
// 5) 像素：最近邻降采样，可选调色板量化
P.pixelate = (dst, src, { size = 8, palette } = {}) => {
  const w = Math.ceil(W / size), h = Math.ceil(H / size), s = P.scratch('px'), g = s.getContext('2d');
  g.imageSmoothingEnabled = false; g.clearRect(0, 0, w, h); g.drawImage(src, 0, 0, w, h);
  if (palette) { const pal = palette.map(P.hex), im = g.getImageData(0, 0, w, h), d = im.data;
    for (let i = 0; i < d.length; i += 4) { let best = 0, bd = 1e9; for (let k = 0; k < pal.length; k++) { const q = pal[k], dd = (d[i] - q[0]) ** 2 + (d[i + 1] - q[1]) ** 2 + (d[i + 2] - q[2]) ** 2; if (dd < bd) { bd = dd; best = k; } } d[i] = pal[best][0]; d[i + 1] = pal[best][1]; d[i + 2] = pal[best][2]; }
    g.putImageData(im, 0, 0); }
  dst.imageSmoothingEnabled = false; dst.drawImage(s, 0, 0, w, h, 0, 0, w * size, h * size); dst.imageSmoothingEnabled = true;
};
// 6) 立体主义切面：抖动三角网，每块取「偏移视角」的颜色做线性渐变，描暗边
P.facets = (dst, src, { nx = 22, ny = 13, seed = 5, t = 0, shiftAmt = 26, edge = 'rgba(40,30,20,.45)' } = {}) => {
  const sd = src.getContext('2d').getImageData(0, 0, W, H).data, r = rng(seed);
  const pts = []; for (let j = 0; j <= ny; j++) for (let i = 0; i <= nx; i++) { const e = i === 0 || j === 0 || i === nx || j === ny; pts.push([i / nx * W + (e ? 0 : (r() - .5) * W / nx * .9), j / ny * H + (e ? 0 : (r() - .5) * H / ny * .9)]); }
  const col = (x, y) => { const i = (clamp(y | 0, 0, H - 1) * W + clamp(x | 0, 0, W - 1)) * 4; return [sd[i], sd[i + 1], sd[i + 2]]; };
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const a = pts[j * (nx + 1) + i], b = pts[j * (nx + 1) + i + 1], c2 = pts[(j + 1) * (nx + 1) + i], d2 = pts[(j + 1) * (nx + 1) + i + 1];
    for (const tri of (r() < .5 ? [[a, b, d2], [a, d2, c2]] : [[a, b, c2], [b, d2, c2]])) {
      const cx = (tri[0][0] + tri[1][0] + tri[2][0]) / 3, cy = (tri[0][1] + tri[1][1] + tri[2][1]) / 3;
      const sh = Math.sin(t * 3 + cx * 0.01) * shiftAmt * r();
      const c0 = col(cx + sh, cy), c1 = P.mix(c0, [60, 45, 30], 0.35);
      const g = dst.createLinearGradient(tri[0][0], tri[0][1], tri[2][0], tri[2][1]); g.addColorStop(0, P.rgb(P.mix(c0, [255, 250, 235], 0.12))); g.addColorStop(1, P.rgb(c1));
      dst.fillStyle = g; dst.beginPath(); tri.forEach((p, k) => k ? dst.lineTo(p[0], p[1]) : dst.moveTo(p[0], p[1])); dst.closePath(); dst.fill();
      dst.strokeStyle = edge; dst.lineWidth = 1.5; dst.stroke();
    }
  }
};

// ---------- 共享编舞：16 个时代用同一套动作曲线，各自用自己的画法画出来 ----------
// lt = 本段局部时间（秒）。返回 0..1 或角度，时代代码按需取用。
// 端杯循环（全片时间）：2.4s 一轮——0.9s 举到嘴边、停 0.7s 喝、0.8s 放回
P.cupCycle = (t, period = 2.4) => { const q = ((t % period) + period) % period;
  if (q < 0.9) return 0.5 - 0.5 * Math.cos(q / 0.9 * Math.PI); if (q < 1.6) return 1; return 0.5 + 0.5 * Math.cos((q - 1.6) / 0.8 * Math.PI); };
P.choreo = (lt, t) => ({
  cupG: P.cupCycle(t),                                         // 按全片时间的端杯（跨段连续：速通片换段时姿势不重置）
  cup: 0.5 - 0.5 * Math.cos(Math.min(1, lt / 0.9) * Math.PI),   // 端杯：0=在桌上 1=在嘴边（约 0.9s 举起）
  sip: Math.max(0, Math.sin(lt * 6)) * 0.15,                    // 喝时头部微仰
  tail: Math.sin(t * 5.2) * 0.35 + Math.sin(t * 11) * 0.08,      // 猫尾摆（弧度）
  blink: (t % 2.3) > 2.18 ? 1 : 0,                              // 眨眼
  ear: Math.max(0, Math.sin(t * 9)) > 0.97 ? 1 : 0,
  breathe: Math.sin(t * 4) * 0.012,
  steam: t,                                                     // 热气用全局时间
});

// ---------- 通用元素 ----------
// 热气：几条随时间上行的正弦曲线（风格化的 S 形可传 width/color）
P.steam = (c, t, x, y, { h = 80, n = 3, color = 'rgba(255,255,255,.8)', width = 4, spread = 16, wobble = 10, seed = 1 } = {}) => {
  c.save(); c.lineCap = 'round'; c.lineWidth = width;
  for (let k = 0; k < n; k++) {
    const ox = (k - (n - 1) / 2) * spread, ph = t * 3 + k * 2.1 + seed;
    const g = c.createLinearGradient(0, y, 0, y - h); g.addColorStop(0, color); g.addColorStop(1, 'rgba(255,255,255,0)'); c.strokeStyle = g;
    c.beginPath(); for (let s = 0; s <= 20; s++) { const q = s / 20; const xx = x + ox + Math.sin(ph - q * 5) * wobble * q, yy = y - q * h; s ? c.lineTo(xx, yy) : c.moveTo(xx, yy); } c.stroke();
  }
  c.restore();
};
// 粒子：给定种子与时间，返回确定的粒子位置（飘落/上浮）
P.particles = (n, seed, t, { x0 = 0, x1 = W, y0 = -40, y1 = H + 40, speed = 80, drift = 30, life = 6 } = {}) => {
  const r = rng(seed), out = [];
  for (let i = 0; i < n; i++) { const bx = lerp(x0, x1, r()), ph = r() * life, sp = speed * (0.6 + r() * 0.8), q = ((t + ph) % life) / life;
    out.push({ x: bx + Math.sin((t + ph) * 1.7 + i) * drift, y: lerp(y0, y1, q), a: r() * Math.PI * 2 + t * (r() - .5) * 3, s: 0.6 + r() * 0.8, q, i }); }
  return out;
};
})();


// ===== huashu kit.js =====
// 跨场景共用的小工具：点列加密/等弧长重采样、可变线宽路径 ribbon、整幅行/列扭曲、手绘不规则圆、全片连续编舞、近侧手臂补盖、角标。
// 渲染器在 lib/brush.js（笔与水）、lib/render.js（网点/点彩/赛璐珞/光影/皮影）、lib/post.js（VHS/胶片/泛光/纹理叠角色）。
(() => {
const W = 1920, H = 1080;
const { clamp, lerp, rng } = U;
const K = window.KIT = {};

// Catmull-Rom 加密点列（开放）
K.densify = (pts, per = 6) => {
  const out = [], n = pts.length, P_ = i => pts[Math.max(0, Math.min(n - 1, i))];
  for (let i = 0; i < n - 1; i++) {
    const p0 = P_(i - 1), p1 = P_(i), p2 = P_(i + 1), p3 = P_(i + 2);
    for (let k = 0; k < per; k++) {
      const t = k / per, t2 = t * t, t3 = t2 * t;
      out.push([0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)]);
    }
  }
  out.push(pts[n - 1]); return out;
};
// 按弧长等距重采样（让噪声、飞白、行波的频率与笔画长短无关）
K.resample = (pts, step = 4) => {
  const out = [pts[0]]; let acc = 0;
  for (let i = 1; i < pts.length; i++) { const a = pts[i - 1], b = pts[i], L = Math.hypot(b[0] - a[0], b[1] - a[1]); let d = step - acc;
    while (d <= L) { const q = d / L; out.push([a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q]); d += step; } acc = L - (d - step); }
  out.push(pts[pts.length - 1]); return out;
};
// 可变线宽路径：点列两侧按 w(q) 偏移成闭合多边形（书法线、飘带、表现主义长笔）
K.ribbon = (pts, w) => {
  const n = pts.length, L = [], R = [];
  for (let i = 0; i < n; i++) {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(n - 1, i + 1)];
    let dx = b[0] - a[0], dy = b[1] - a[1]; const d = Math.hypot(dx, dy) || 1; dx /= d; dy /= d;
    const hw = (typeof w === 'function' ? w(i / (n - 1), i) : w) / 2;
    L.push([pts[i][0] - dy * hw, pts[i][1] + dx * hw]); R.push([pts[i][0] + dy * hw, pts[i][1] - dx * hw]);
  }
  const p = new Path2D(); L.forEach((q, i) => i ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]));
  for (let i = n - 1; i >= 0; i--) p.lineTo(R[i][0], R[i][1]); p.closePath(); return p;
};
// 少女部件按画序列出（远→近），给「不调 drawGirl、自己逐部件上色」的风格用
K.girlOrder = ['farSleeve', 'farHand', 'farCuff', 'hairDown', 'skirt', 'shoe', 'torso', 'neck', 'hairBack', 'bun', 'face', 'bangs', 'upperArm', 'foreArm', 'cuff', 'hand'];   // 完整画序（含五官、杯、钩子插槽名）见 RIG.GIRL_ORDER

// 把 src 按「水平条带位移」画到 dst：fx(y) 返回该行的 x 位移（蒙克式整个世界在流动）
// margin：每行横向多拉伸 2·margin 像素，位移后左右不露缝（margin ≥ 最大位移）
K.warpRows = (dst, src, fx, band = 6, margin = 0) => {
  for (let y = 0; y < H; y += band) { const dx = fx(y + band / 2); dst.drawImage(src, 0, y, W, band, dx - margin, y, W + 2 * margin, band); }
};
K.warpCols = (dst, src, fy, band = 6, margin = 0) => {
  for (let x = 0; x < W; x += band) { const dy = fy(x + band / 2); dst.drawImage(src, x, 0, band, H, x, dy - margin, band, H + 2 * margin); }
};

// n 角星（剪纸星、爆炸框、闪光）：外半径 r0、内半径 r1
K.star = (cx, cy, r0, r1, n, rot = 0) => { const pts = []; for (let i = 0; i < n * 2; i++) { const a = rot + i / (n * 2) * Math.PI * 2 - Math.PI / 2, r = i % 2 ? r1 : r0; pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); } return U.poly(pts); };
// 手绘不规则圆（草间的点不是完美圆）：n 个顶点、半径按种子起伏
K.blob = (c, x, y, r, seed, wob = 0.08, n = 14) => {
  const rr = rng(seed); const off = []; for (let i = 0; i < n; i++) off.push(1 + (rr() - .5) * 2 * wob);
  const pts = []; for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2; pts.push([x + Math.cos(a) * r * off[i], y + Math.sin(a) * r * off[i]]); }
  c.moveTo((pts[0][0] + pts[n - 1][0]) / 2, (pts[0][1] + pts[n - 1][1]) / 2);
  for (let i = 0; i < n; i++) { const p = pts[i], q = pts[(i + 1) % n]; c.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2); }
  c.closePath();
};

// 全片连续的编舞：端杯按全局时间走（周期 = 两段 = 2.34s，在嘴边和桌上各停一会儿），
// 换风格时姿势不重置——速通看的是「同一瞬间换了画风」，而不是每段重喝一次。转场正好落在杯子停住的时刻。
K.choreo = (lt, t) => {
  const ch = PAINT.choreo(lt, t), k = 0.5 - 0.5 * Math.cos(2 * Math.PI * t / 2.34375);
  let q = clamp((k - 0.15) / 0.7); q = q * q * (3 - 2 * q);
  ch.cup = q; ch.sip = Math.max(0, Math.sin(t * 6)) * 0.15 * q; return ch;
};
// drawGirl(mode:'line') 会把被近侧手臂挡住的躯干线画穿：线稿画完后，把近侧手臂、袖口、手、杯重新盖一遍
K.nearArm = (c, G, { fill, line = '#111', lw = 3, skin, cuff, cupBody, cupRim, pattern } = {}) => {
  const arm = new Path2D(); arm.addPath(G.upperArm); arm.addPath(G.foreArm);
  c.save(); c.lineJoin = 'round'; c.fillStyle = fill; c.fill(arm); if (pattern) { c.save(); c.clip(arm); pattern(c); c.restore(); }
  c.strokeStyle = line; c.lineWidth = lw; c.stroke(G.upperArm); c.stroke(G.foreArm);
  if (cuff) { c.fillStyle = cuff; c.fill(G.cuff); c.stroke(G.cuff); }
  c.fillStyle = skin; c.fill(G.hand); c.lineWidth = lw * 0.8; c.stroke(G.hand);
  RIG.drawCup(c, G.cup, { body: cupBody, rim: cupRim, line, lw: lw * 0.8, hw: 7 });
  c.fillStyle = skin; c.beginPath(); c.arc(G.thumb[0], G.thumb[1], 7, 0, Math.PI * 2); c.fill(); c.lineWidth = lw * 0.7; c.stroke();
  c.restore();
};
// 右上角角标：标题（中文）＋副标题；入场 0.18s 由各风格的 reveal(c,p) 决定怎么出现
K.label = (c, { title, sub, tFont, sFont, tCol, sCol, x = 1866, y = 128, sy = 182, shadow, before, after, spacing = 0 }) => {
  c.save(); c.textAlign = 'right'; c.textBaseline = 'alphabetic';
  if (before) before(c);
  if (shadow) { c.shadowColor = shadow.color; c.shadowOffsetX = shadow.x || 0; c.shadowOffsetY = shadow.y || 0; c.shadowBlur = shadow.blur || 0; }
  c.font = tFont; c.fillStyle = tCol; if (spacing) c.letterSpacing = spacing + 'px'; c.fillText(title, x, y); c.letterSpacing = '0px';
  if (sub) { c.font = sFont; c.fillStyle = sCol || tCol; c.fillText(sub, x, sy); }
  if (after) after(c);
  c.restore();
};
})();


// ===== huashu motion.js =====
// 动效时间库 MO：缓动、弹簧、步进、错开、拍号。全部是「时间 → 数值」的纯函数，不读时钟，确定性。
// 来源：YouTube 解说动画语法 8 支示范片（references/09-视频动画语法.md）。约定：p 是 0..1 的进度，t/lt 是秒。
//   选缓动的一句话：讲解（3b1b/白板）用 smooth/sineInOut 两端都停稳；发布会用 appleOut/弹簧；动态文字用 expoOut＋弹簧；
//   拼贴（Vox）相机用 sineInOut 或长尾 longTail，纸片按 12fps 步进；Kurzgesagt 动作用 k75，循环用 easyEase。
(() => {
const { clamp, lerp } = U;
const MO = window.MO = {};

// ---------- 画布尺寸（片段渲染用） ----------
// 引擎和全部示范片是 1920×1080。clip.html（口播管线的参数化片段）可能是竖屏 1080×1920：
// 用到屏幕尺寸的库（CAM / UI / CH / DG / CL）用 U.onStage 登记，clip.js 启动时 U.setStage(w, h) 一次性改掉。
const stageHooks = [];
window.STAGE = { W: 1920, H: 1080 };
U.onStage = f => { stageHooks.push(f); };
U.setStage = (w, h) => { window.STAGE = { W: w, H: h }; stageHooks.forEach(f => f(w, h)); };

// ---------- 时间片 ----------
MO.seg = (t, a, b) => clamp((t - a) / (b - a));                          // [a,b] → 0..1
MO.at = (t, t0, dur) => clamp((t - t0) / dur);                           // [t0, t0+dur] → 0..1
MO.anim = (lt, start, dur, ease = MO.smooth) => ease(clamp((lt - start) / dur));
// 步进时间：把连续时间量化成 fps 步。拼贴纸片/打字/荧光笔「一拍二」= 12fps；故事型角色 24fps；相机仍按 60fps 平滑。
MO.step = (t, fps = 12) => Math.floor(t * fps + 1e-6) / fps;

// ---------- manim 缓动（3b1b/manim rate_functions.py） ----------
// smooth = 6t⁵−15t⁴+10t³：两端速度、加速度都为 0。讲解类片子的默认缓动。
MO.smooth = t => { t = clamp(t); return t * t * t * (10 - 15 * t + 6 * t * t); };
const sig = x => 1 / (1 + Math.exp(-x));
MO.smoothCE = (t, inflection = 10) => { const e = sig(-inflection / 2); return clamp((sig(inflection * (t - 0.5)) - e) / (1 - 2 * e)); };   // ManimCE 版
MO.thereAndBack = t => MO.smooth(t < 0.5 ? 2 * t : 2 * (1 - t));          // Indicate 用：去了又回
MO.rushInto = t => 2 * MO.smooth(t / 2);                                  // 慢起、冲进终点
MO.rushFrom = t => 2 * MO.smooth(t / 2 + 0.5) - 1;                        // 冲出、慢收
MO.doubleSmooth = t => t < 0.5 ? 0.5 * MO.smooth(2 * t) : 0.5 * (1 + MO.smooth(2 * t - 1));
MO.wiggle = (t, wiggles = 2) => MO.thereAndBack(t) * Math.sin(wiggles * Math.PI * t);
// manim LaggedStart(lag_ratio=r)：n 个子动画里第 i 个在总进度 p 下的局部进度。总长 = 1+(n-1)·r 个单位，第 i 个占 [i·r, i·r+1]。
MO.lagged = (i, n, p, r = 0.05) => clamp(p * (1 + (n - 1) * r) - i * r);

// ---------- 常用解析缓动（AE / CSS 名字） ----------
MO.linear = p => p;
MO.sineInOut = p => -(Math.cos(Math.PI * p) - 1) / 2;
MO.cubicIn = p => p * p * p;
MO.cubicOut = p => 1 - Math.pow(1 - p, 3);
MO.cubicInOut = p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
MO.quartOut = p => 1 - Math.pow(1 - p, 4);
MO.quintOut = p => 1 - Math.pow(1 - p, 5);
MO.quintInOut = p => p < .5 ? 16 * p ** 5 : 1 - Math.pow(-2 * p + 2, 5) / 2;
MO.expoIn = p => p <= 0 ? 0 : Math.pow(2, 10 * p - 10);
MO.expoOut = p => p >= 1 ? 1 : 1 - Math.pow(2, -10 * p);                // 动效设计最常用的「快出慢停」
MO.expoInOut = p => p <= 0 ? 0 : p >= 1 ? 1 : p < .5 ? Math.pow(2, 20 * p - 10) / 2 : (2 - Math.pow(2, -20 * p + 10)) / 2;
// backOut 过冲：s=1.70158 约 10%；2.2 约 13%；2.6 约 17%；3.5 约 23%
MO.backOut = (p, s = 1.70158) => { const q = p - 1; return 1 + (s + 1) * q * q * q + s * q * q; };
// 弹性落定：overshoot 一次再回（贴纸「啪」地落下）
MO.elasticOut = p => p <= 0 ? 0 : p >= 1 ? 1 : Math.pow(2, -10 * p) * Math.sin((p * 10 - 0.75) * (2 * Math.PI / 3)) + 1;

// ---------- CSS cubic-bezier(x1,y1,x2,y2)：牛顿法由 x 求参数，再取 y ----------
MO.bezier = (x1, y1, x2, y2) => {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const X = s => ((ax * s + bx) * s + cx) * s, Y = s => ((ay * s + by) * s + cy) * s, dX = s => (3 * ax * s + 2 * bx) * s + cx;
  return p => {
    if (p <= 0) return 0; if (p >= 1) return 1;
    let s = p; for (let k = 0; k < 8; k++) { const e = X(s) - p, d = dX(s); if (Math.abs(e) < 1e-6 || Math.abs(d) < 1e-6) break; s -= e / d; }
    s = clamp(s); return Y(s);
  };
};
MO.appleOut = MO.bezier(0.25, 0.1, 0.25, 1);        // CSS `ease`：苹果官网/发布会过渡
MO.emphasized = MO.bezier(0.2, 0, 0, 1);            // Material 3 emphasized：长尾减速
MO.k75 = MO.bezier(0.75, 0, 0.25, 1);               // Kurzgesagt「其他动作用 75% influence」（y1 调研 §6.2）
MO.easyEase = MO.bezier(0.333, 0, 0.667, 1);        // AE Easy Ease：循环动作
MO.longTail = MO.bezier(0.33, 0, 0.2, 1);           // Vox「沿线追过去」「拉出揭示」：起步快、长尾落定（y2 调研）

// ---------- 弹簧 ----------
// SwiftUI 参数化（WWDC23 Animate with springs）：stiffness=(2π/duration)²，damping=4π(1-bounce)/duration → ζ=1-bounce，ω=2π/duration。
// 返回 0→1 的位移（可 >1 = 过冲）。t 单位秒。发布会 UI 用它：.smooth(bounce 0) / .snappy(0.15) / .bouncy(0.3)。
MO.spring = (t, { duration = 0.5, bounce = 0, v0 = 0 } = {}) => {
  if (t <= 0) return 0;
  const w = 2 * Math.PI / duration, z = 1 - bounce;
  let x;                                                                 // x = 剩余位移（1→0）
  if (Math.abs(z - 1) < 1e-4) x = Math.exp(-w * t) * (1 + (w - v0) * t);
  else if (z < 1) { const wd = w * Math.sqrt(1 - z * z); x = Math.exp(-z * w * t) * (Math.cos(wd * t) + ((z * w - v0) / wd) * Math.sin(wd * t)); }
  else { const r = Math.sqrt(z * z - 1), a = -w * (z - r), b = -w * (z + r); const A = (-v0 - b) / (a - b); x = A * Math.exp(a * t) + (1 - A) * Math.exp(b * t); }
  return 1 - x;
};
MO.smoothSpring = t => MO.spring(t, { duration: 0.5, bounce: 0 });
MO.snappy = t => MO.spring(t, { duration: 0.5, bounce: 0.15 });
MO.bouncy = t => MO.spring(t, { duration: 0.5, bounce: 0.3 });
// 频率/衰减参数化的欠阻尼弹簧阶跃响应（动态文字用）：freq Hz、decay 1/s。
//   freq 3 / decay 6 第一次过冲 ≈37%（砸进来的大字）；2.5 / 9 ≈16%（常规弹入）；2.2 / 10 ≈8%（主词落定）
MO.springHz = (t, freq = 2.5, decay = 9) => t <= 0 ? 0 : 1 - Math.exp(-decay * t) * Math.cos(2 * Math.PI * freq * t);
// 到位后的余振（Dan Ebberts 惯性回弹）：amp·sin(2πft)/e^(decay·t)。换姿势的身体挤压、屏幕震动都用它。
MO.settle = (t, amp, freq = 3, decay = 5) => t <= 0 ? 0 : amp * Math.sin(2 * Math.PI * freq * t) / Math.exp(decay * t);

// ---------- 持续微动 ----------
// 漂浮：两频正弦叠加。周期避开 5s 左右（HIG：~0.2Hz 的持续摆动让人不适）
MO.float = (t, amp = 6, period = 4, ph = 0) => amp * (0.75 * Math.sin(2 * Math.PI * t / period + ph) + 0.25 * Math.sin(2 * Math.PI * t / (period * 0.53) + ph * 1.7));

// ---------- 反查与拍号 ----------
// 缓动的反函数（二分）：「线头/计数在什么时刻到达 y」。例：折线按 smooth 画，求画到某个 x 的时刻（t3 镜 2 的事件标注）。
MO.invert = (ease, y, iters = 30) => { let a = 0, b = 1; for (let i = 0; i < iters; i++) { const m = (a + b) / 2; if (ease(m) < y) a = m; else b = m; } return (a + b) / 2; };
// 拍 → 秒
MO.beat = (n, bpm = 120) => n * 60 / bpm;
// 揭开那一帧落在拍上：段起点 = 小节线 − 揭开点 × 转场时长（y5：zoom 揭开点 .55、色带 .6、push 到 90% 位移在 .58）
MO.onBeat = (barTime, revealP, trDur) => barTime - revealP * trDur;
})();


// ===== huashu camera.js =====
// 世界相机 CAM：解说片不是「一段段画＋转场」，而是「一张连续的世界画布＋一台相机」。
// 铁律：相机只由全片时间（或本段时间）算出来，场景每帧按相机矢量重画——缩放转场永远清楚，不会把 1920 的位图放大 20 倍糊掉；
//       引擎在转场窗口里会把上一段继续按 t 画进 A 缓冲，所以「上一镜的相机继续推」天然成立（live 推进）。
// cam = { x, y, z, r }：世界点 (x,y) 落在画面中心，缩放 z（>1 推近），绕中心旋转 r（弧度）。缺省 x=960,y=540,z=1,r=0。
(() => {
let W = 1920, H = 1080; U.onStage((w, h) => { W = w; H = h; });   // 画布尺寸跟 U.setStage 走（默认 1920×1080）
const { clamp, lerp } = U;
const CAM = window.CAM = {};
const n = cam => ({ x: cam.x ?? W / 2, y: cam.y ?? H / 2, z: cam.z ?? 1, r: cam.r || 0 });

// 把相机变换作用到 c 上（不 save/restore，调用方负责）
CAM.apply = (c, cam) => { const k = n(cam); c.translate(W / 2, H / 2); if (k.r) c.rotate(k.r); c.scale(k.z, k.z); c.translate(-k.x, -k.y); };
// 在相机下画 fn(c)，自动 save/restore
CAM.with = (c, cam, fn) => { c.save(); CAM.apply(c, cam); fn(c); c.restore(); };
// 世界 → 屏幕 / 屏幕 → 世界
CAM.toScreen = (cam, x, y) => { const k = n(cam); let dx = (x - k.x) * k.z, dy = (y - k.y) * k.z; if (k.r) { const cs = Math.cos(k.r), sn = Math.sin(k.r); [dx, dy] = [dx * cs - dy * sn, dx * sn + dy * cs]; } return [W / 2 + dx, H / 2 + dy]; };
CAM.toWorld = (cam, sx, sy) => { const k = n(cam); let dx = sx - W / 2, dy = sy - H / 2; if (k.r) { const cs = Math.cos(-k.r), sn = Math.sin(-k.r); [dx, dy] = [dx * cs - dy * sn, dx * sn + dy * cs]; } return [k.x + dx / k.z, k.y + dy / k.z]; };

// ---------- 缩放 ----------
// 对数插值：z0→z1 每帧放大同样的倍数，观众感到「匀速穿越尺度」；线性插值会前慢后猛。所有推拉缩放都用它。
CAM.zlerp = (z0, z1, e) => z0 * Math.pow(z1 / z0, e);
// 「从一个半径 r 的圆形开口穿进去，开口要在转场结束前盖满屏」需要的最小放大倍数 = 开口中心到最远屏幕角的距离 / r。
// 开口在画面中心时 ≈ 1101/r（卡片里写的「放大倍数 ≥ 1100/半径」）。小了，最后一帧开口没盖满会跳。
CAM.coverZoom = (r, cx = W / 2, cy = H / 2) => Math.max(Math.hypot(cx, cy), Math.hypot(W - cx, cy), Math.hypot(cx, H - cy), Math.hypot(W - cx, H - cy)) / r;
// 让世界点 (wx,wy) 在缩放 z 下出现在屏幕 (sx,sy)：返回相机。推近某物时让它平滑移到画面中心就靠它。
CAM.anchor = (wx, wy, z, sx, sy) => ({ x: wx - (sx - W / 2) / z, y: wy - (sy - H / 2) / z, z });
// 推进某个世界点：从 base 相机出发，缩放按对数插值到 Z（进度 e）；该点在屏幕上从原位置移向画面中心（进度 k，默认 = e；k=0 = 绕它原地推）。
// 用于「钻进镜头」（y1）、「推满照片再硬切」（y2）、「推进最新数据点」（t3，k=0）。
CAM.pushTo = (base, wx, wy, Z, e, k = e, to = [W / 2, H / 2]) => {
  const s0 = CAM.toScreen(base, wx, wy), z = CAM.zlerp(n(base).z, Z, e);
  return CAM.anchor(wx, wy, z, lerp(s0[0], to[0], k), lerp(s0[1], to[1], k));
};

// ---------- 关键帧 ----------
// keys = [{t, x, y, z, r, ease}]；ease 作用在「从上一键到这一键」，默认 cubicInOut；z 用对数插值。
// 沿路径平移（Vox 桌面、白板上的平移/甩镜）就是一串关键帧：平滑移动用 sineInOut，远跳用 cubicInOut＋运动模糊，沿线追用 MO.longTail。
CAM.at = (keys, t) => {
  if (t <= keys[0].t) return { ...keys[0] };
  for (let i = 1; i < keys.length; i++) {
    const a = keys[i - 1], b = keys[i];
    if (t <= b.t) { const e = (b.ease || MO.cubicInOut)(MO.seg(t, a.t, b.t));
      return { x: lerp(a.x, b.x, e), y: lerp(a.y, b.y, e), z: CAM.zlerp(a.z, b.z, e), r: lerp(a.r || 0, b.r || 0, e), v: e }; }
  }
  return { ...keys[keys.length - 1] };
};

// ---------- 视差 ----------
// d=1 是主体平面，d<1 远、d>1 近。远层位移按 d 线性、缩放按 z^d：深推时远景几乎不动，这就是景深感（Kurzgesagt 5 层视差）。
CAM.layer = (c, cam, d, fn) => { const k = n(cam); c.save(); CAM.apply(c, { x: lerp(W / 2, k.x, d), y: lerp(H / 2, k.y, d), z: Math.pow(k.z, d) }); fn(c); c.restore(); };

// ---------- 运动 ----------
// 相机在屏幕上的瞬时速度（像素/帧，60fps）：camFn(t) → cam。给 motionBlur 用。
CAM.velocity = (camFn, t, dt = 1 / 60) => { const a = camFn(t), b = camFn(t - dt); const z = n(a).z; return [(n(a).x - n(b).x) * z, (n(a).y - n(b).y) * z]; };
// 动态模糊：沿速度方向把整帧叠 n 次（快摇/甩镜用；速度 <1.5px 自动退化成一次绘制）。模糊长度应短于相机位移（PremiumBeat）。
CAM.motionBlur = (c, src, vx, vy, k = 7) => {
  const sp = Math.hypot(vx, vy); if (sp < 1.5) { c.drawImage(src, 0, 0); return; }
  c.save(); for (let i = 0; i < k; i++) { const q = i / (k - 1) - 0.5; c.globalAlpha = i === 0 ? 1 : 1 / (i + 1); c.drawImage(src, -vx * q, -vy * q); } c.restore();
};
// 手持感微漂：两路不同周期的正弦（连续时间，不闪），返回 [dx, dy] 像素
CAM.drift = (t, amp = 6, seed = 0) => [amp * Math.sin(t * 0.9 + seed) + amp * 0.4 * Math.sin(t * 2.3 + seed * 2), amp * 0.7 * Math.sin(t * 1.1 + seed * 3)];
})();


// ===== huashu diagram.js =====
// 图解与形变 DG：点列、逐笔描出（draw-on）、揭开遮罩、手绘抖动线、manim 式 Transform / Write、白板笔画时间线＋跟随的笔、
// 神经网络（神经元/连线/信号闪过）、Indicate / Circumscribe / 大括号、3b1b 色板。
// 约定：点列 pts = [[x,y],...]；cum = DG.cum(pts) 是累计弧长；「画到 d」都按弧长算。确定性：随机全走种子。
(() => {
let W = 1920, H = 1080; U.onStage((w, h) => { W = w; H = h; });   // 画布尺寸跟 U.setStage 走（默认 1920×1080）
const { clamp, lerp, rng } = U;
const DG = window.DG = {};

// ---------- 点列 ----------
DG.cum = pts => { const L = [0]; for (let i = 1; i < pts.length; i++) L.push(L[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])); return L; };
DG.len = pts => { const L = DG.cum(pts); return L[L.length - 1]; };
// 折线上弧长 d 处的点
DG.pointAt = (pts, cum, d) => {
  if (d <= 0) return pts[0]; const n = pts.length; if (d >= cum[n - 1]) return pts[n - 1];
  let lo = 0, hi = n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (cum[m] < d) lo = m; else hi = m; }
  const q = (d - cum[lo]) / (cum[hi] - cum[lo] || 1); return [lerp(pts[lo][0], pts[hi][0], q), lerp(pts[lo][1], pts[hi][1], q)];
};
// 逐笔描出：画折线的前 d 长度（用当前 strokeStyle/lineWidth），返回笔尖位置。白板、红笔圈、红线连接都用它。
DG.drawPartial = (c, pts, cum, d) => {
  const n = pts.length; if (d <= 0 || n < 2) return pts[0];
  c.beginPath(); c.moveTo(pts[0][0], pts[0][1]);
  let i = 1; for (; i < n && cum[i] <= d; i++) c.lineTo(pts[i][0], pts[i][1]);
  let tip = pts[n - 1];
  if (i < n) { tip = DG.pointAt(pts, cum, d); c.lineTo(tip[0], tip[1]); }
  c.stroke(); return tip;
};
// 变换点列：平移 (dx,dy)、缩放 s
DG.xform = (pts, dx, dy, s = 1) => pts.map(([x, y]) => [dx + x * s, dy + y * s]);
// 椭圆弧点列（a0→a1）
DG.arcPts = (cx, cy, rx, ry, a0, a1, n = 24) => { const o = []; for (let i = 0; i <= n; i++) { const a = a0 + (a1 - a0) * i / n; o.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]); } return o; };
// 手画的圈：从 a0 起转 turns 圈（>1 首尾略交叉，像真的用笔圈）
DG.ellipsePts = (cx, cy, rx, ry, a0 = -Math.PI / 2, turns = 1.08, n = 64) => DG.arcPts(cx, cy, rx, ry, a0, a0 + turns * Math.PI * 2, n);
// 手绘抖动：Catmull-Rom 加密后按种子加低频抖动（静态，不沸腾——白板上的线画完就不动）
DG.hand = (pts, { per = 8, amp = 1.6, seed = 1, freq = 0.035 } = {}) => {
  const d = KIT.densify(pts, per), r = rng(seed), ph = r() * 100;
  return KIT.resample(d, 3).map(([x, y], i) => [x + PAINT.noise(i * freq + ph, 3.1) * amp * 2, y + PAINT.noise(i * freq + ph, 7.7) * amp * 2]);
};
// 揭开遮罩：沿折线前 d 长度，每段做成同向绕行的方条（方头延长 w/2），合成一条 Path2D 用来 clip。
// VideoScribe 的「Reveal」画法：之字形笔触从上往下揭开一张画；也用于逐字书写。同向绕行保证 nonzero 下重叠处不抵消。
DG.revealMask = (pts, cum, d, w) => {
  const p = new Path2D(), h = w / 2;
  for (let i = 1; i < pts.length && cum[i - 1] < d; i++) {
    const a = pts[i - 1], b0 = pts[i], L = cum[i] - cum[i - 1]; if (L <= 0) continue;
    const q = Math.min(1, (d - cum[i - 1]) / L), b = [a[0] + (b0[0] - a[0]) * q, a[1] + (b0[1] - a[1]) * q];
    const ux = (b0[0] - a[0]) / L, uy = (b0[1] - a[1]) / L, nx = -uy * h, ny = ux * h;
    const A = [a[0] - ux * h, a[1] - uy * h], B = [b[0] + ux * h, b[1] + uy * h];
    p.moveTo(A[0] + nx, A[1] + ny); p.lineTo(B[0] + nx, B[1] + ny); p.lineTo(B[0] - nx, B[1] - ny); p.lineTo(A[0] - nx, A[1] - ny); p.closePath();
  }
  return p;
};
// 之字形揭开路径：在框 (x,y,w,h) 里从上往下 rows 行来回
DG.zigzag = (x, y, w, h, rows) => { const o = []; for (let r = 0; r <= rows; r++) { const yy = y + h * r / rows; o.push(r % 2 ? [x + w, yy] : [x, yy]); } return o; };

// ---------- manim Transform：两边点数相同、起点对齐（都从左上方向起、顺时针）才能逐点插值 ----------
DG.rectPts = (cx, cy, w, h, n = 64) => {
  const per = 2 * (w + h), out = [];
  for (let i = 0; i < n; i++) {
    let d = i / n * per; let x, y;
    if (d < w) { x = cx - w / 2 + d; y = cy - h / 2; }
    else if ((d -= w) < h) { x = cx + w / 2; y = cy - h / 2 + d; }
    else if ((d -= h) < w) { x = cx + w / 2 - d; y = cy + h / 2; }
    else { d -= w; x = cx - w / 2; y = cy + h / 2 - d; }
    out.push([x, y]);
  }
  return out;
};
DG.circlePts = (cx, cy, r, n = 64) => { const out = []; for (let i = 0; i < n; i++) { const a = -3 * Math.PI / 4 + i / n * 2 * Math.PI; out.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); } return out; };
DG.morph = (A, B, p) => A.map((a, i) => [lerp(a[0], B[i][0], p), lerp(a[1], B[i][1], p)]);
DG.shape = (c, pts, { fill, stroke, lw = 2 } = {}) => {
  c.beginPath(); pts.forEach((q, i) => i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1])); c.closePath();
  if (fill) { c.fillStyle = fill; c.fill(); }
  if (stroke && lw > 0) { c.strokeStyle = stroke; c.lineWidth = lw; c.lineJoin = 'round'; c.stroke(); }
};

// ---------- 颜色（接受 '#hex' 或 [r,g,b]） ----------
DG.arr = h => Array.isArray(h) ? h : PAINT.hex(h);
DG.mixA = (h1, h2, t) => { const a = DG.arr(h1), b = DG.arr(h2); return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]; };
DG.rgba = (h, a = 1) => { const v = DG.arr(h); return `rgba(${v[0] | 0},${v[1] | 0},${v[2] | 0},${a})`; };
DG.mix = (h1, h2, t) => DG.rgba(DG.mixA(h1, h2, t));
// 3b1b 色板。Grant 的 custom_config 在 ffmpeg 导出时 saturation=1.5，照 manim 原色值直接用会发灰，所以 DG.MANIM 是饱和后的。
DG.MANIM_RAW = { BLUE_C: '#58C4DD', BLUE_D: '#29ABCA', BLUE_E: '#1C758A', TEAL_C: '#5CD0B3', GREEN_C: '#83C167', YELLOW: '#FFFF00', GOLD_C: '#F0AC5F',
  RED_C: '#FC6255', MAROON_C: '#C55F73', PURPLE_C: '#9A72AC', GREY_A: '#DDDDDD', GREY_B: '#BBBBBB', GREY_C: '#888888', GREY_D: '#444444', GREY_E: '#222222', WHITE: '#FFFFFF', BLACK: '#000000' };
DG.saturate = (hex, s = 1.5) => {                         // CSS/SVG saturate 矩阵
  const [r, g, b] = PAINT.hex(hex);
  const R = (0.213 + 0.787 * s) * r + (0.715 - 0.715 * s) * g + (0.072 - 0.072 * s) * b;
  const G = (0.213 - 0.213 * s) * r + (0.715 + 0.285 * s) * g + (0.072 - 0.072 * s) * b;
  const B = (0.213 - 0.213 * s) * r + (0.715 - 0.715 * s) * g + (0.072 + 0.928 * s) * b;
  const f = v => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0');
  return '#' + f(R) + f(G) + f(B);
};
DG.MANIM = {}; for (const k in DG.MANIM_RAW) DG.MANIM[k] = DG.saturate(DG.MANIM_RAW[k]);
DG.U = H / 8;                                             // manim：画面高 8 个单位 → 1 单位 = 135px（1080p）

// ---------- Write 与公式（CMU = LaTeX Computer Modern；中文用思源宋） ----------
DG.FONT = { rm: 'CMU-rm', it: 'CMU-it', zh: 'NotoSerifSC' };
const famOf = tk => tk.zh ? DG.FONT.zh : tk.it ? DG.FONT.it : DG.FONT.rm;
// tokens: [{s, it, zh, col, sub, sup, size, gap}]；返回每个 token 的 {x,w,sz}（相对起点）
DG.layout = (c, tokens, size) => {
  let x = 0; return tokens.map(tk => {
    const sz = (tk.size || 1) * size; c.font = `${sz}px "${famOf(tk)}"`;
    const w = c.measureText(tk.s).width; let sw = 0;
    if (tk.sub || tk.sup) { c.font = `${sz * 0.62}px "${DG.FONT.rm}"`; sw = c.measureText(tk.sub || tk.sup).width + sz * 0.04; }
    const r = { x, w: w + sw, sz }; x += w + sw + (tk.gap != null ? tk.gap : sz * 0.06); return r;
  });
};
// manim Write：每个 token 先沿轮廓描边（lineDash 推进），后半程填色、描边淡掉；token 间 lag = min(4/(n+1), 0.2)（3b1b creation.py）。
// 尊重外层 globalAlpha（FadeOut 时整条公式一起淡）。返回每个 token 的屏幕 {x,w}。
DG.math = (c, tokens, x, y, size, { p = 1, align = 'center', col = '#fff', lag } = {}) => {
  const L = DG.layout(c, tokens, size), total = L.length ? L[L.length - 1].x + L[L.length - 1].w : 0;
  const x0 = align === 'center' ? x - total / 2 : align === 'right' ? x - total : x;
  const n = tokens.length, r = lag != null ? lag : Math.min(4 / (n + 1), 0.2);
  const A0 = c.globalAlpha;
  c.save(); c.textBaseline = 'alphabetic'; c.textAlign = 'left';
  tokens.forEach((tk, i) => {
    const q = MO.lagged(i, n, clamp(p), r); if (q <= 0) return;
    const sz = L[i].sz, fx = x0 + L[i].x, colr = tk.col || col;
    const draw = (txt, xx, yy, f, mode) => { c.font = f; mode === 's' ? c.strokeText(txt, xx, yy) : c.fillText(txt, xx, yy); };
    const main = `${sz}px "${famOf(tk)}"`, small = `${sz * 0.62}px "${DG.FONT.rm}"`;
    c.font = main; const mw = c.measureText(tk.s).width;
    const parts = [[tk.s, fx, y, main]]; if (tk.sub) parts.push([tk.sub, fx + mw + sz * 0.02, y + sz * 0.22, small]); if (tk.sup) parts.push([tk.sup, fx + mw + sz * 0.02, y - sz * 0.42, small]);
    const qs = MO.doubleSmooth(q);
    if (qs < 1) { c.strokeStyle = colr; c.lineWidth = Math.max(1.2, sz * 0.025); c.setLineDash([sz * 5 * clamp(qs * 2), 1e5]); c.globalAlpha = A0 * (1 - clamp((qs - 0.6) / 0.4)); parts.forEach(pt => draw(pt[0], pt[1], pt[2], pt[3], 's')); c.setLineDash([]); }
    const fa = clamp((qs - 0.4) / 0.6); if (fa > 0) { c.globalAlpha = A0 * fa; c.fillStyle = colr; parts.forEach(pt => draw(pt[0], pt[1], pt[2], pt[3], 'f')); }
    c.globalAlpha = A0;
  });
  c.restore();
  return L.map(l => ({ x: x0 + l.x, w: l.w }));
};
// 纯文本的 Write（中文标题也走这个：非 ASCII 字自动用 DG.FONT.zh）
DG.write = (c, text, x, y, size, p, opt = {}) => DG.math(c, [...text].map(ch => ({ s: ch, zh: /[^\x00-\x7f]/.test(ch) || opt.zh, gap: 0 })), x, y, size, { ...opt, p });

// ---------- 神经网络 ----------
// 神经元：描边白 2px，填充不透明度 = 激活值（3b1b nn/part1.py 原做法）
DG.neuron = (c, x, y, r, a, { stroke = '#fff', fill = '#fff', lw = 2, sa = 1 } = {}) => {
  c.save(); c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2);
  if (a > 0) { c.globalAlpha = clamp(a); c.fillStyle = fill; c.fill(); }
  c.globalAlpha = sa; c.strokeStyle = stroke; c.lineWidth = lw; c.stroke(); c.restore();
};
// 圆周到圆周的连线端点
DG.edgeEnds = (ax, ay, ar, bx, by, br) => { const dx = bx - ax, dy = by - ay, d = Math.hypot(dx, dy) || 1; return [ax + dx / d * ar, ay + dy / d * ar, bx - dx / d * br, by - dy / d * br]; };
// 连线：正权重蓝、负权重红；线宽 = minW + maxW·|w|³（nn/part2.py：3·(|w|/max)³）；p = 画到哪（ShowCreation）
DG.edge = (c, e, w, p = 1, { maxW = 3.2, minW = 0.6, pos = DG.MANIM.BLUE_C, neg = DG.MANIM.RED_C, alpha = 0.85, from = 0 } = {}) => {
  if (p <= from) return;
  const [x1, y1, x2, y2] = e;
  c.save(); c.strokeStyle = w >= 0 ? pos : neg; c.globalAlpha = alpha; c.lineWidth = minW + maxW * Math.pow(Math.min(1, Math.abs(w)), 3);
  c.beginPath(); c.moveTo(lerp(x1, x2, from), lerp(y1, y2, from)); c.lineTo(lerp(x1, x2, p), lerp(y1, y2, p)); c.stroke(); c.restore();
};
// ShowPassingFlash（time_width=2.0）：信号沿线闪过，返回可见段 [lo, hi]（0..1）
DG.flash = (alpha, tw = 2) => { const up = lerp(0, 1 + tw, alpha); return [clamp(up - tw), clamp(up)]; };

// ---------- 提示 ----------
DG.indicate = p => ({ s: 1 + 0.2 * MO.thereAndBack(clamp(p)), k: MO.thereAndBack(clamp(p)) });   // 放大 1.2、染黄（k）、回来
// Circumscribe：矩形框沿周长画出再收走（time_width 0.3 的近似）
DG.circumscribe = (c, x, y, w, h, p, { col = DG.MANIM.YELLOW, lw = 4, tw = 0.3 } = {}) => {
  if (p <= 0 || p >= 1) return; const pts = DG.rectPts(x + w / 2, y + h / 2, w, h, 160);
  const hi = clamp(lerp(0, 1 + tw, p)), lo = clamp(hi - tw - (1 - p) * 0.7);
  c.save(); c.strokeStyle = col; c.lineWidth = lw; c.beginPath();
  const i0 = Math.floor(lo * 160), i1 = Math.floor(hi * 160); for (let i = i0; i <= Math.min(160, i1); i++) { const q = pts[i % 160]; i === i0 ? c.moveTo(q[0], q[1]) : c.lineTo(q[0], q[1]); } c.stroke(); c.restore();
};
// 大括号（竖直，开口朝右），从中间向两端长出
DG.brace = (c, x, y0, y1, { col = '#fff', lw = 3, depth = 22, p = 1 } = {}) => {
  if (p <= 0) return; const ym = (y0 + y1) / 2;
  c.save(); c.strokeStyle = col; c.lineWidth = lw; c.lineCap = 'round'; c.globalAlpha = clamp(p * 1.5);
  const k = MO.smooth(p), a0 = lerp(ym, y0, k), a1 = lerp(ym, y1, k);
  c.beginPath(); c.moveTo(x, a0); c.quadraticCurveTo(x - depth * 0.5, a0, x - depth * 0.5, a0 + Math.min(40, (ym - a0) * 0.3));
  c.lineTo(x - depth * 0.5, ym - Math.min(40, (ym - a0) * 0.3)); c.quadraticCurveTo(x - depth * 0.5, ym, x - depth, ym);
  c.quadraticCurveTo(x - depth * 0.5, ym, x - depth * 0.5, ym + Math.min(40, (a1 - ym) * 0.3));
  c.lineTo(x - depth * 0.5, a1 - Math.min(40, (a1 - ym) * 0.3)); c.quadraticCurveTo(x - depth * 0.5, a1, x, a1); c.stroke(); c.restore();
};

// ---------- 白板：笔画时间线 ＋ 一支跟着画的笔 ----------
// 按顺序往板上加笔画，每笔时长 = 长度 / 笔速，自动排成时间线；画的时候笔尖永远在最新那一笔的前沿，两笔之间沿弧线滑过去，笔从不离开画面。
//   const B = DG.board({ ink, lw, speed, font });  B.at(0.12); B.line(pts); B.text('猫？', x, y, 150, {rate: 7.5}); B.pop('×1000万张', x, y, 74); ...
//   每帧：const { tip, col } = B.draw(g, ft)（在世界坐标里画，调用方先套相机）；const [sx, sy] = B.penAt(ft, cam, tip)；DG.pen(c, sx, sy, col || B.penColor(ft))。
// 自定义笔画 B.push({ kind:'custom', t0, t1, col, draw(g, q, ft) → 笔尖|null, start() → 世界点, end() → 世界点, handed:true })。
// 速度口径（RSA 实测）：正文写字 ≈16 字/秒、标题 ≈7.5 字/秒；强调色关键词不带手、整词弹出（后期合成）。
DG.board = ({ ink = '#0A0503', lw = 6.5, speed = 2600, font = '"LXGWWenKai-500"' } = {}) => {
  const B = { S: [], cur: 0, ink, lw, speed, font };
  B.at = t => { B.cur = t; return B; };
  B.push = s => { B.S.push(s); return s; };
  B.line = (raw, { w = lw, col = ink, speed: sp = speed, seed = B.S.length + 1, amp = 1.4, smooth = true, min = 0.04, gap = 0.02 } = {}) => {
    const pts = smooth ? DG.hand(raw, { amp, seed, per: 8 }) : raw, cum = DG.cum(pts), len = cum[cum.length - 1];
    const d = Math.max(min, len / sp); B.S.push({ kind: 'line', pts, cum, len, w, col, t0: B.cur, t1: B.cur + d }); B.cur += d + gap;
  };
  // 一笔直线（不抖、固定时长，比如网络连线），不推进太多时间
  B.segment = (a, b, { w = 3, col = ink, dur = 0.06, gap = 0.012 } = {}) => {
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]); B.S.push({ kind: 'line', pts: [a, b], cum: [0, L], len: L, w, col, t0: B.cur, t1: B.cur + dur }); B.cur += gap;
  };
  // 手写字：rate 字/秒
  B.text = (str, x, y, size, { col = ink, rate = 16, align = 'left', dur } = {}) => {
    const d = dur || [...str].length / rate; B.S.push({ kind: 'text', str, x, y, size, col, align, t0: B.cur, t1: B.cur + d }); B.cur += d + 0.03;
  };
  // 强调词：不带手、0.3s 放大回落整词弹出
  B.pop = (str, x, y, size, { col = ink, align = 'center', dur = 0.3 } = {}) => { B.S.push({ kind: 'pop', str, x, y, size, col, align, t0: B.cur, t1: B.cur + dur }); B.cur += 0.12; };
  // 铺色（淡入，不推进时间）
  B.fill = (path, col, dur = 0.18) => { B.S.push({ kind: 'fill', path, col, t0: B.cur, t1: B.cur + dur }); };

  const drawOne = (g, s, ft) => {
    if (ft < s.t0) return null;
    const q = clamp((ft - s.t0) / (s.t1 - s.t0));
    if (s.kind === 'custom') return s.draw(g, q, ft);
    if (s.kind === 'fill') { g.save(); g.globalAlpha = q; g.fillStyle = s.col; g.fill(s.path); g.restore(); return null; }
    if (s.kind === 'line') { g.strokeStyle = s.col; g.lineWidth = s.w; return DG.drawPartial(g, s.pts, s.cum, s.len * MO.sineInOut(q)); }
    if (s.kind === 'pop') {
      g.save(); g.font = `${s.size}px ${B.font}`; g.fillStyle = s.col; g.textBaseline = 'alphabetic'; g.textAlign = s.align;
      const e = MO.backOut(q, 2.2); g.globalAlpha = clamp(q * 4); g.translate(s.x, s.y - s.size * 0.35); g.scale(e, e); g.fillText(s.str, 0, s.size * 0.35); g.restore(); return null;
    }
    if (s.kind === 'text') {
      // 逐字写：笔尖在字框里 3 行之字形来回，遮罩跟着笔尖揭开这个字（Reveal 画法用在单字上）
      g.save(); g.font = `${s.size}px ${B.font}`; g.fillStyle = s.col; g.textBaseline = 'alphabetic';
      const chars = [...s.str], ws = chars.map(ch => g.measureText(ch).width), w = ws.reduce((a, b) => a + b, 0), x0 = s.align === 'center' ? s.x - w / 2 : s.x;
      const n = chars.length, k = Math.min(n, q * n), done = Math.floor(k);
      let x = x0, tip = null;
      chars.forEach((ch, i) => {
        if (i < done) g.fillText(ch, x, s.y);
        else if (i === done && k < n) {
          const zz = DG.zigzag(x - 2, s.y - s.size * 0.95, ws[i] + 4, s.size * 1.15, 3), cum = DG.cum(zz), d = cum[cum.length - 1] * (k - done);
          g.save(); g.clip(DG.revealMask(zz, cum, d, s.size * 0.62)); g.fillText(ch, x, s.y); g.restore(); tip = DG.pointAt(zz, cum, d);
        }
        x += ws[i];
      });
      g.restore(); return tip || [x0 + w, s.y - s.size * 0.35];
    }
    return null;
  };
  // 画全部笔画（世界坐标）。返回正在画的那一笔的笔尖与墨色（没有在画的 → tip=null）
  B.draw = (g, ft) => {
    g.lineJoin = 'round'; g.lineCap = 'round';
    let tip = null, col = null;
    for (const s of B.S) { const p = drawOne(g, s, ft); if (p && ft < s.t1) { tip = p; col = s.col; } }
    return { tip, col };
  };
  const handed = () => B.S.filter(s => s.kind !== 'pop' && s.kind !== 'fill' && (s.kind !== 'custom' || s.handed !== false));
  const tw = s => s.size * [...s.str].length * 0.9;
  const endPt = s => s.kind === 'custom' ? s.end() : s.kind === 'line' ? s.pts[s.pts.length - 1] : [s.align === 'center' ? s.x + tw(s) / 2 : s.x + tw(s), s.y - s.size * 0.3];
  const startPt = s => s.kind === 'custom' ? s.start() : s.kind === 'line' ? s.pts[0] : [s.align === 'center' ? s.x - tw(s) / 2 : s.x, s.y - s.size * 0.3];
  // 笔在屏幕上的位置：在画 → 笔尖；两笔之间 → 从上一笔终点沿弧线滑到下一笔起点；开场 → 从右下入画
  B.penAt = (ft, cam, tip) => {
    if (tip) return CAM.toScreen(cam, tip[0], tip[1]);
    let prev = null, next = null;
    for (const s of handed()) { if (s.t1 <= ft) prev = s; else if (s.t0 > ft && !next) next = s; }
    if (prev && next) {
      const q = MO.sineInOut((ft - prev.t1) / (next.t0 - prev.t1)), a = CAM.toScreen(cam, ...endPt(prev)), b = CAM.toScreen(cam, ...startPt(next));
      return [lerp(a[0], b[0], q), lerp(a[1], b[1], q) - Math.sin(q * Math.PI) * Math.min(80, 20 + Math.hypot(b[0] - a[0], b[1] - a[1]) * 0.15)];
    }
    if (next) { const b = CAM.toScreen(cam, ...startPt(next)), q = MO.cubicOut(clamp(ft / next.t0)); return [lerp(W * 0.62, b[0], q), lerp(H * 0.7, b[1], q)]; }
    return CAM.toScreen(cam, ...endPt(prev));
  };
  // 空档里笔的颜色 = 下一笔的墨色（没有下一笔就用上一笔）
  B.penColor = ft => { const hs = handed(), nx = hs.find(s => s.t0 > ft), pv = [...hs].reverse().find(s => s.t1 <= ft); return (nx || pv || { col: ink }).col; };
  return B;
};
// 一支马克笔（屏幕空间，笔尖在 (x,y)，笔身朝右上）。笔尾帽 = 墨色；有高光和投影，浮在板上。用笔引导视线。
DG.pen = (c, x, y, col, tilt = 0) => {
  c.save(); c.translate(x, y); c.rotate(-0.72 + tilt);
  c.fillStyle = 'rgba(0,0,0,.12)'; c.beginPath(); c.roundRect(30, 14, 250, 34, 16); c.fill();
  c.fillStyle = col; c.beginPath(); c.moveTo(0, 0); c.lineTo(26, -8); c.lineTo(26, 8); c.closePath(); c.fill();
  c.fillStyle = '#e8e8e4'; c.beginPath(); c.roundRect(24, -12, 26, 24, 4); c.fill();
  c.fillStyle = '#26262a'; c.beginPath(); c.roundRect(48, -17, 190, 34, 12); c.fill();
  c.fillStyle = col; c.beginPath(); c.roundRect(212, -18, 46, 36, 10); c.fill();
  c.fillStyle = 'rgba(255,255,255,.22)'; c.beginPath(); c.roundRect(60, -12, 140, 7, 4); c.fill();
  c.restore();
};
})();


// ===== huashu typo.js =====
// 文字动效 TY：逐字/逐词弹入、遮罩升起、弹入、打字、从模糊浮出、荧光笔、下划线、主词砸入、关键词强调、数字计数与等宽数字。
// 约定：p 是 0..1 进度；lt/t 是秒。字体用 CSS 字体串或 family 名（中文默认 PuHui-*，开源版是思源黑体，见 lib/fonts.js）。
// 可读性底线（y5/y1 卡片）：主词 : 辅句 : 标签 ≈ 3.4 : 1 : 0.4；一句辅句在屏 ≥1.2s；入场慢（0.3–0.4s）、退场快（≈0.18s）。
(() => {
const { clamp, lerp } = U;
const TY = window.TY = {};

// ---------- 基础 ----------
TY.font = (size, fam = 'PuHui-Black') => `${size}px "${fam}"`;
// 画一段字：可带透明、缩放、旋转（绕 ox,oy）、字距 track、描边
TY.text = (c, s, x, y, { size = 120, fam = 'PuHui-Black', color = '#111', align = 'center', base = 'alphabetic', alpha = 1, scale = 1, rot = 0, track = 0, stroke, strokeW = 0, ox = x, oy = y } = {}) => {
  if (alpha <= 0 || scale <= 0) return;
  c.save(); c.globalAlpha *= alpha; c.translate(ox, oy); c.rotate(rot); c.scale(scale, scale); c.translate(-ox, -oy);
  c.font = TY.font(size, fam); c.textAlign = align; c.textBaseline = base; if (track) c.letterSpacing = track + 'px';
  if (stroke && strokeW) { c.lineJoin = 'round'; c.strokeStyle = stroke; c.lineWidth = strokeW; c.strokeText(s, x, y); }
  c.fillStyle = color; c.fillText(s, x, y); c.restore();
};
TY.width = (c, s, size, fam = 'PuHui-Black', track = 0) => { c.save(); c.font = TY.font(size, fam); if (track) c.letterSpacing = track + 'px'; const w = c.measureText(s).width; c.restore(); return w; };
// 折行：按 maxW 把一段字拆成多行（中文逐字可断；英文/数字按词断；行首不放中文标点）。font 是 CSS 字体串
TY.wrap = (c, text, maxW, font) => {
  c.save(); if (font) c.font = font;
  const toks = String(text).match(/[A-Za-z0-9.,%+\-'’]+\s*|\s+|./gu) || [], lines = []; let cur = '';
  for (const tk of toks) {
    if (tk === '\n') { lines.push(cur); cur = ''; continue; }
    if (cur && c.measureText(cur + tk.trimEnd()).width > maxW && !/^[，。、：；！？」』）》,.!?)]$/.test(tk)) { lines.push(cur.trimEnd()); cur = tk.trimStart(); }
    else cur += tk;
  }
  if (cur.trim()) lines.push(cur.trimEnd());
  c.restore(); return lines;
};
// 把字号从 size 往下缩，直到真放得下：折行后不超过 maxLines 行，而且量过每行宽 ≤ maxW（单个长英文词/长数字折不开，只数行数会超宽）。返回 {size, lines, ok}
// 先在 [min, 1] 倍里找；找不到时 soft:true 返回 ok:false 让调用方换方案（如改折两行），否则继续缩（最低 0.2 倍）。balance:true 时多行折成等长。
TY.fit = (c, text, maxW, size, fam, { maxLines = 2, min = 0.45, weight = '', soft = false, balance = false } = {}) => {
  const font = s => `${weight}${s}px "${fam}"`, ok = (s, lines) => { c.save(); c.font = font(s); const r = lines.length <= maxLines && lines.every(l => c.measureText(l).width <= maxW + 0.5); c.restore(); return r; };
  let s = size, lines = [String(text)], good = false;
  for (; s >= size * (soft ? min : 0.2) - 1e-9; s *= 0.94) { lines = TY.wrap(c, text, maxW, font(s)); if (ok(s, lines)) { good = true; break; } if (s < size * min && soft) break; }
  if (!good && !soft) s /= 0.94;
  if (good && balance && lines.length > 1) { const n = lines.length; let lo = maxW * 0.4, hi = maxW; for (let k = 0; k < 14; k++) { const m = (lo + hi) / 2; if (TY.wrap(c, text, m, font(s)).length > n) lo = m; else hi = m; } lines = TY.wrap(c, text, hi, font(s)); }
  return { size: s, lines, ok: good };
};

// ---------- 入场 ----------
// 逐字错开出现：每字 dur 秒，从 dy 往上、透明→不透明、可带缩放；Kurzgesagt 标题 0.5s/字、错开 45ms。返回总宽。
TY.charsIn = (c, str, x, y, lt, { font, color, dur = 0.35, stagger = 0.04, dy = 24, scale0 = 1, align = 'left', ease = MO.cubicOut, shadow, spacing = 0 } = {}) => {
  c.save(); c.font = font; c.textBaseline = 'alphabetic'; c.fillStyle = color; const A0 = c.globalAlpha;   // 乘上调用方的透明度（外面包一层淡出才生效）
  const chars = [...str]; const ws = chars.map(ch => c.measureText(ch).width + spacing); const tw = ws.reduce((a, b) => a + b, 0) - spacing;
  let cx = align === 'center' ? x - tw / 2 : align === 'right' ? x - tw : x;
  chars.forEach((ch, i) => {
    const q = clamp((lt - i * stagger) / dur); if (q > 0) {
      const e = ease(q); c.save(); c.globalAlpha = A0 * clamp(q * 2.2);
      c.translate(cx + ws[i] / 2, y + dy * (1 - e)); const s = lerp(scale0, 1, e); c.scale(s, s);
      if (shadow) { c.shadowColor = shadow.color; c.shadowOffsetX = shadow.x || 0; c.shadowOffsetY = shadow.y || 0; c.shadowBlur = shadow.blur || 0; }
      c.fillText(ch, -ws[i] / 2 + spacing / 2, 0); c.restore();
    }
    cx += ws[i];
  });
  c.restore(); return tw;
};
// 遮罩升起：字从基线下的「槽」里推上来（动态文字最常见的出字法）。p 0..1，o.dir=+1 往上出，-1 往下收。o 同 TY.text
TY.rise = (c, s, x, y, p, o = {}) => {
  const size = o.size || 120, e = MO.expoOut(clamp(p)), w = TY.width(c, s, size, o.fam, o.track), a = o.align || 'left';
  const x0 = a === 'left' ? x : a === 'center' ? x - w / 2 : x - w;
  c.save(); c.beginPath(); c.rect(x0 - 20, y - size * 1.05, w + 40, size * 1.32); c.clip();
  TY.text(c, s, x, y + size * 1.2 * (1 - e) * (o.dir || 1), { ...o, align: a }); c.restore();
};
// 弹入：从 0 放大到 1，backOut 过冲后停（以字中心为轴）。o.over = backOut 的 s
TY.pop = (c, s, x, y, p, o = {}) => {
  if (p <= 0) return; const size = o.size || 120, sc = MO.backOut(clamp(p), o.over ?? 2.2) * (o.scale || 1);
  TY.text(c, s, x, y, { ...o, scale: sc, ox: x, oy: y - size * 0.36 });
};
// 从模糊浮出（发布会标题）：模糊→清晰＋上移＋透明→不透明。用调用方设好的 font/fillStyle/textAlign。
TY.blurIn = (c, text, x, y, p, { blur = 18, dy = 28, scale = 0 } = {}) => {
  if (p <= 0) return;
  c.save(); c.globalAlpha *= clamp(p * 1.4);
  const b = (1 - p) * blur; if (b > 0.3) c.filter = `blur(${b.toFixed(2)}px)`;
  c.translate(x, y + (1 - p) * dy); if (scale) { const s = 1 + (1 - p) * scale; c.scale(s, s); }
  c.fillText(text, 0, 0); c.restore();
};
// 打字：按字符数截断（p 0..1）
TY.typed = (s, p) => [...s].slice(0, Math.round(clamp(p) * [...s].length)).join('');
// 打字机：t 时刻已打出的字数（rate 字/秒，可按 fps 步进；Vox 13 字/秒＋12fps，白板正文 16 字/秒）
TY.typedCount = (t, t0, rate, fps = 0) => Math.max(0, Math.floor(((fps ? MO.step(t, fps) : t) - t0) * rate + 1e-6) + 1);

// ---------- 强调 ----------
// 荧光笔：从左往右刷出的斜切色块，放在字后面（动态文字）。拼贴风的毛边 multiply 荧光笔见 CL.highlight
TY.marker = (c, x, y, w, h, p, color = '#FFE14D', skew = 0.18) => {
  if (p <= 0) return; const e = MO.expoOut(clamp(p)), ww = w * e;
  c.save(); c.fillStyle = color; c.beginPath(); c.moveTo(x + h * skew, y); c.lineTo(x + ww + h * skew, y); c.lineTo(x + ww, y + h); c.lineTo(x, y + h); c.closePath(); c.fill(); c.restore();
};
TY.underline = (c, x, y, w, p, color, lw = 10) => { if (p <= 0) return; c.save(); c.strokeStyle = color; c.lineCap = 'round'; c.lineWidth = lw; c.beginPath(); c.moveTo(x, y); c.lineTo(x + w * MO.expoOut(clamp(p)), y); c.stroke(); c.restore(); };
// 主词「砸」进来：从 from 倍缩到 1（弹簧 2.2Hz/10，过冲 ≈8%），前 0.06s 透明→不透明。lt = 本词出现后的秒数
TY.slam = (c, s, x, y, lt, o = {}) => {
  if (lt < 0) return; const size = o.size || 340, k = MO.springHz(lt, o.freq || 2.2, o.decay || 10), sc = lerp(o.from || 1.7, 1, k);
  TY.text(c, s, x, y, { size, fam: 'PuHui-Black', align: 'left', ...o, scale: sc * (o.scale || 1), alpha: clamp(lt / 0.06), ox: o.ox ?? x, oy: o.oy ?? y - size * 0.36 });
};
// 关键词：tIn 起从槽里升起，到「强调拍」tHit 放大到 1.35 倍并钉住（弹簧 2.5Hz/9）。返回当前缩放（给荧光笔/下划线跟着放大）。
// 字和动作说同一件事：「下一行」放大的那一拍正好按下 Tab（y5）。
TY.keyword = (c, s, x, y, t, tIn, tHit, o) => {
  if (t < tHit) { TY.rise(c, s, x, y, MO.at(t, tIn, 0.35), { ...o, align: 'left' }); return 1; }
  const sc = lerp(1, 1.35, MO.springHz(t - tHit, 2.5, 9));
  TY.text(c, s, x, y, { ...o, align: 'left', scale: sc, ox: x, oy: y });
  return sc;
};
// 关键词后面的荧光笔：跟关键词同一拍出现、同一缩放
TY.keywordMarker = (c, x, y, w, size, t, tHit, sc, col) => { if (t < tHit) return; TY.marker(c, x - 8, y - size * 0.98 * sc, (w + 16) * sc, size * 1.18 * sc, MO.at(t, tHit, 0.3), col); };

// ---------- 数字 ----------
// 千分位格式；负号用真减号「−」
TY.fmt = (v, dec = 0, sep = true) => { const s = Math.abs(v).toFixed(dec); const [i, f] = s.split('.'); const ii = sep ? i.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : i; return (v < 0 ? '−' : '') + ii + (f ? '.' + f : ''); };
// 计数：[start, start+dur] 内从 a 滚到 b；默认 expoOut（先快后慢，最后一位落定）。数字和动画只改「怎么出现」，不改口径。
TY.count = (lt, start, dur, a, b, ease = MO.expoOut) => lerp(a, b, ease(clamp((lt - start) / dur)));
// 等宽数字排布：canvas 开不了 tnum，滚动时数字宽度会抖，必须每个数字占同宽格子自己排。用调用方设好的 font/fillStyle。返回总宽
TY.tabular = (c, str, x, y, { align = 'left', digitW } = {}) => {
  const dw = digitW || c.measureText('0').width;
  const ws = [...str].map(ch => /[0-9]/.test(ch) ? dw : c.measureText(ch).width);
  const total = ws.reduce((s, w) => s + w, 0);
  let cx = align === 'right' ? x - total : align === 'center' ? x - total / 2 : x;
  const ta = c.textAlign; c.textAlign = 'center';
  [...str].forEach((ch, i) => { c.fillText(ch, cx + ws[i] / 2, y); cx += ws[i]; });
  c.textAlign = ta; return total;
};
})();


// ===== huashu chart.js =====
// 数据图表动画 CH：比例尺、好看的刻度、网格（先出）、柱（从 0 长出）、折线（按时间画出、头部圆点）、K 线（逐根）、
// 标注（点→引线→文字）、经济学人式版式（顶红线＋红色小旗＋标题＋单位副标题＋来源注＋「示意数据」角标）。
// 顺序铁律（准确性＋可读性）：坐标系先出 → 数据后出 → 标注最后、一镜只标一件事；柱状图基线必须是 0（纵轴不从 0 开始要在副标题写明）；
// 动画只改「怎么出现」，不改数值；单位写在副标题；虚构/示意数据每一镜角落都标。
(() => {
let W = 1920, H = 1080; U.onStage((w, h) => { W = w; H = h; });   // 画布尺寸跟 U.setStage 走（默认 1920×1080）
const { clamp, lerp } = U;
const CH = window.CH = {};
const seg = (t, a, b) => clamp((t - a) / (b - a));

// 线性比例尺：值 → 屏幕；f.inv 反查
CH.lin = (d0, d1, r0, r1) => { const f = v => r0 + (v - d0) / (d1 - d0) * (r1 - r0); f.inv = y => d0 + (y - r0) / (r1 - r0) * (d1 - d0); return f; };
// 1/2/2.5/5×10^k 的好看刻度
CH.ticks = (min, max, n = 5) => {
  const raw = (max - min) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), steps = [1, 2, 2.5, 5, 10];
  const step = steps.map(s => s * mag).find(s => s >= raw) || 10 * mag;
  const out = []; for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10)); return out;
};
CH.mixHex = (a, b, t) => { const A = PAINT.hex(a), B = PAINT.hex(b); return `rgb(${lerp(A[0], B[0], t) | 0},${lerp(A[1], B[1], t) | 0},${lerp(A[2], B[2], t) | 0})`; };

// ---------- 网格：水平线从左往右逐条画出（lag 错开），零线更深更粗；刻度值跟着淡入 ----------
// F = {x,y,w,h} 绘图区；ys = 刻度值；yS = 值→屏幕 y；p = 0..1
CH.grid = (c, F, ys, yS, p, { col = 'rgba(0,0,0,.14)', zeroCol = '#121212', lw = 1.5, zeroLw = 3, lag = 0.12, font = '28px sans-serif', labelCol = '#5b5b5b', fmt = v => String(v), side = 'right', labelDy = -10 } = {}) => {
  c.save(); c.font = font; c.textBaseline = 'alphabetic';
  ys.forEach((v, i) => {
    const q = MO.lagged(i, ys.length, p, lag), e = MO.quintOut(q); if (q <= 0) return;
    const y = Math.round(yS(v)) + 0.5, isZero = Math.abs(v) < 1e-9;
    c.strokeStyle = isZero ? zeroCol : col; c.lineWidth = isZero ? zeroLw : lw;
    c.beginPath(); c.moveTo(F.x, y); c.lineTo(F.x + F.w * e, y); c.stroke();
    c.globalAlpha = seg(q, 0.3, 1); c.fillStyle = labelCol;
    if (side === 'right') { c.textAlign = 'right'; c.fillText(fmt(v), F.x + F.w, y + labelDy); }
    else { c.textAlign = 'left'; c.fillText(fmt(v), F.x, y + labelDy); }
    c.globalAlpha = 1;
  });
  c.restore();
};
// x 轴标签（年份等）：逐个淡入上移。items = [{x, label}]
CH.xLabels = (c, items, y, p, { font = '28px sans-serif', col = '#3a3a3a', lag = 0.1 } = {}) => {
  c.save(); c.font = font; c.textAlign = 'center'; c.fillStyle = col;
  items.forEach((it, i) => { const q = MO.quintOut(MO.lagged(i, items.length, p, lag)); if (q <= 0) return; c.globalAlpha = q; c.fillText(it.label, it.x, y + (1 - q) * 14); });
  c.restore();
};

// ---------- 柱：从基线 0 长出（quintOut），数值标签与柱顶同步计数（等宽数字） ----------
// data: [{x, v}]；bw 柱宽；p 0..1 总进度；hi 高亮下标（其余按 hiP 过渡到 dimCol）。返回各柱顶 [[x,y]]
CH.bars = (c, data, yS, bw, p, { lag = 0.18, col = '#1f5c99', hi = -1, hiP = 0, hiCol, dimCol = '#b9c3cc', label = true, fmt = v => v.toFixed(0), font = '30px sans-serif', labelCol = '#121212' } = {}) => {
  const y0 = yS(0), tops = [];
  data.forEach((d, i) => {
    const q = MO.lagged(i, data.length, p, lag), e = MO.quintOut(q);
    const v = d.v * e, y = yS(v);
    let fill = col;
    if (hi >= 0) fill = i === hi ? (hiCol || col) : CH.mixHex(col, dimCol, hiP);
    c.fillStyle = fill; c.fillRect(d.x - bw / 2, y, bw, y0 - y);
    tops.push([d.x, y]);
    if (label && q > 0) {
      c.save(); c.font = font; c.textAlign = 'center'; c.fillStyle = labelCol; c.globalAlpha = seg(q, 0.05, 0.4);
      if (d.v < 0) { c.textBaseline = 'top'; TY.tabular(c, fmt(v), d.x, y + 12, { align: 'center' }); }   // 负数柱：数值写在柱底下方，别写进柱子里
      else TY.tabular(c, fmt(v), d.x, y - 14, { align: 'center' });
      c.restore();
    }
  });
  return tops;
};

// ---------- 折线：按 x（时间）推进画出；头部圆点；可选面积渐变；返回头部点 ----------
// pts: 屏幕坐标（x 递增）；p: 0..1 = 画到 x 范围的哪里。area = {top, base, c0, c1}
CH.line = (c, pts, p, { col = '#e3120b', lw = 5, head = true, headR = 9, area, glow } = {}) => {
  if (p <= 0) return null;
  const xEnd = lerp(pts[0][0], pts[pts.length - 1][0], clamp(p));
  const vis = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (pts[i][0] <= xEnd) vis.push(pts[i]);
    else { const a = pts[i - 1], b = pts[i], q = (xEnd - a[0]) / (b[0] - a[0]); vis.push([xEnd, lerp(a[1], b[1], q)]); break; }
  }
  const hp = vis[vis.length - 1];
  if (area) {
    const g = c.createLinearGradient(0, area.top, 0, area.base); g.addColorStop(0, area.c0); g.addColorStop(1, area.c1);
    c.save(); c.beginPath(); c.moveTo(vis[0][0], area.base); vis.forEach(q => c.lineTo(q[0], q[1])); c.lineTo(hp[0], area.base); c.closePath(); c.fillStyle = g; c.fill(); c.restore();
  }
  c.save(); c.strokeStyle = col; c.lineWidth = lw; c.lineJoin = 'round'; c.lineCap = 'round';
  if (glow) { c.shadowColor = col; c.shadowBlur = glow; }
  c.beginPath(); vis.forEach((q, i) => i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1])); c.stroke(); c.restore();
  if (head && p < 1.0001) { c.save(); c.fillStyle = col; c.beginPath(); c.arc(hp[0], hp[1], headR, 0, Math.PI * 2); c.fill();
    c.strokeStyle = '#fff'; c.lineWidth = 3; c.stroke(); c.restore(); }
  return hp;
};
// 折线在 x 处的 y（标注要钉在线上）
CH.yAt = (pts, x) => { for (let i = 1; i < pts.length; i++) if (pts[i][0] >= x) { const a = pts[i - 1], b = pts[i]; return lerp(a[1], b[1], (x - a[0]) / (b[0] - a[0])); } return pts[pts.length - 1][1]; };

// ---------- K 线：逐根出现，实体从开盘价长到收盘价，影线随后；默认红涨绿跌（A 股惯例，海外片换成绿涨红跌并在副标题写明） ----------
// rows: [{o,h,l,c}]；xs: 每根中心 x；cw 实体宽；p 0..1
CH.candles = (c, rows, xs, yS, cw, p, { up = '#e0322f', down = '#11a05a', lag = 0.06, hollowUp = false } = {}) => {
  rows.forEach((r, i) => {
    const q = MO.lagged(i, rows.length, p, lag); if (q <= 0) return;
    const e = MO.quintOut(seg(q, 0, 0.7)), w = MO.quintOut(seg(q, 0.35, 1));
    const isUp = r.c >= r.o, col = isUp ? up : down, x = xs[i];
    const yo = yS(r.o), yc = lerp(yo, yS(r.c), e);
    c.save(); c.strokeStyle = col; c.fillStyle = col; c.lineWidth = 2.5;
    if (w > 0) { const ym = (yo + yS(r.c)) / 2; c.beginPath(); c.moveTo(x, lerp(ym, yS(r.h), w)); c.lineTo(x, lerp(ym, yS(r.l), w)); c.stroke(); }
    const top = Math.min(yo, yc), hgt = Math.max(2, Math.abs(yc - yo));
    if (isUp && hollowUp) { c.fillStyle = '#fff'; c.fillRect(x - cw / 2, top, cw, hgt); c.strokeRect(x - cw / 2, top, cw, hgt); }
    else c.fillRect(x - cw / 2, top, cw, hgt);
    c.restore();
  });
};

// ---------- 标注：圆点弹出（弹簧）→ 脉冲环 → 引线画出 → 文字淡入。时间用秒，便于对口播 ----------
// lt 秒；t0 开始时刻；(x,y) 数据点；(tx,ty) 文字锚点；lines 第一行用 font、其余用 font2（数字先说，比较后说）
CH.callout = (c, lt, t0, { x, y, tx, ty, lines = [], col = '#121212', dotCol = '#e3120b', font = '600 30px sans-serif', font2, align = 'left', r = 11 }) => {
  const k = lt - t0; if (k <= 0) return;
  const s = MO.spring(k, { duration: 0.45, bounce: 0.25 });
  c.save();
  c.fillStyle = dotCol; c.beginPath(); c.arc(x, y, r * Math.max(0, s), 0, Math.PI * 2); c.fill();
  const pr = seg(k, 0, 0.6); if (pr > 0 && pr < 1) { c.strokeStyle = dotCol; c.globalAlpha = 1 - pr; c.lineWidth = 3; c.beginPath(); c.arc(x, y, r + pr * 34, 0, Math.PI * 2); c.stroke(); c.globalAlpha = 1; }
  const lp = MO.quintOut(seg(k, 0.12, 0.45));
  if (lp > 0) { c.strokeStyle = col; c.lineWidth = 2; c.beginPath(); c.moveTo(x, y); c.lineTo(lerp(x, tx, lp), lerp(y, ty, lp)); c.stroke(); }
  const fp = MO.quintOut(seg(k, 0.3, 0.7));
  if (fp > 0) {
    c.globalAlpha = fp; c.fillStyle = col; c.textAlign = align; c.textBaseline = 'alphabetic';
    const dx = align === 'left' ? 12 : align === 'right' ? -12 : 0;
    lines.forEach((L, i) => { c.font = i === 0 ? font : (font2 || font); c.fillText(L, tx + dx + (1 - fp) * (align === 'right' ? 16 : -16), ty + 8 + i * 40); });
  }
  c.restore();
};

// ---------- 版式 ----------
CH.tag = (c, x, y, w = 64, h = 14, col = '#e3120b') => { c.fillStyle = col; c.fillRect(x, y, w, h); };
// 「示意数据」角标：右下圆角框。虚构数据每一镜都要有
CH.demoBadge = (c, text = '示意数据', { x = W - 40, y = 1040, font = '600 24px sans-serif', col = '#7a7a7a', box = 'rgba(0,0,0,.06)' } = {}) => {
  c.save(); c.font = font; c.textAlign = 'right'; c.textBaseline = 'middle';
  const w = c.measureText(text).width + 28; c.fillStyle = box; c.beginPath(); c.roundRect(x - w, y - 22, w, 44, 8); c.fill();
  c.strokeStyle = col; c.globalAlpha = 0.5; c.lineWidth = 1.5; c.stroke(); c.globalAlpha = 1;
  c.fillStyle = col; c.fillText(text, x - 14, y + 1); c.restore();
};
// 经济学人式整页版式（每镜都画）：白底 → 顶部细红线从左画出 → 左上红色小旗 → 标题 → 副标题（单位/口径）→ 左下来源 → 右下「示意数据」。
// lt = 本镜时间（版式 0.6s 内全部到位，之后才出坐标系）。badge 传 false 不画角标（真实数据时）。
CH.frame = (c, lt, { title, sub, source = '来源：示意数据', badge = '示意数据', bg = '#FFFFFF', red = '#E3120B', ink = '#0C0C0C', subCol = '#4F5B61', srcCol = '#7c868b',
  font = '"NotoSansSC"', x = 90 } = {}) => {
  c.fillStyle = bg; c.fillRect(0, 0, W, H);
  const p = MO.quintOut(MO.seg(lt, 0, 0.45));
  c.fillStyle = red; c.fillRect(0, 0, W * p, 8);
  CH.tag(c, x, 56, 96 * p, 22, red);
  c.save(); c.textBaseline = 'alphabetic'; c.textAlign = 'left';
  const tp = MO.quintOut(MO.seg(lt, 0.05, 0.5));
  c.globalAlpha = tp; c.font = `700 58px ${font}`; c.fillStyle = ink; c.fillText(title, x, 150 + (1 - tp) * 18);
  const sp = MO.quintOut(MO.seg(lt, 0.15, 0.6));
  c.globalAlpha = sp; c.font = `400 34px ${font}`; c.fillStyle = subCol; c.fillText(sub, x, 205 + (1 - sp) * 14);
  c.globalAlpha = 1; c.font = `400 26px ${font}`; c.fillStyle = srcCol; c.fillText(source, x, 1040);
  c.restore();
  if (badge) CH.demoBadge(c, badge, { x: W - 60, y: 1030, font: `700 28px ${font}`, col: red, box: 'rgba(227,18,11,0.06)' });
};
})();


// ===== huashu ui.js =====
// 发布会式界面 UI：渐变光斑底（mesh gradient 近似）、毛玻璃卡片、伪 3D 透视（切片）、光扫过（sheen）、只画影子的投影、线性图标。
// 约定：坐标是 1920×1080 屏幕坐标；所有动画参数由调用方用时间算好传进来，本库不读时钟。
// 卡片展开成全屏、模糊推进是转场（transitions.js 的 expandRect / blurPush）。
// 坑：blur 会从画布外拉进透明像素——画进不清空的离屏会逐帧累积（qa 确定性 ✗），所以先铺原图再叠模糊版；
//     切片透视的条带取整像素首尾相接，别用「重叠 0.75px 防缝」，半透明下重叠处出竖纹。
(() => {
let W = 1920, H = 1080; U.onStage((w, h) => { W = w; H = h; });   // 画布尺寸跟 U.setStage 走（默认 1920×1080）
const { clamp, lerp } = U;
const UI = window.UI = {};
const cvs = {};
const scratch = (k, w, h) => { let c = cvs[k]; if (!c || c.width !== w || c.height !== h) { c = cvs[k] = document.createElement('canvas'); c.width = w; c.height = h; } return c; };
UI.scratch = scratch;

UI.rr = (c, x, y, w, h, r) => { c.beginPath(); c.roundRect(x, y, w, h, r); };
UI.rrPath = (x, y, w, h, r) => { const p = new Path2D(); p.roundRect(x, y, w, h, r); return p; };

// ---------- 渐变光斑底（mesh gradient 的廉价近似） ----------
// 小画布上画几团径向渐变（screen 叠加）→ 小画布上模糊 → 放大铺满 → 叠静态颗粒防色带。
// blobs: [{x,y,r,col:[r,g,b],a, ax,ay,period,ph}]  位置单位是屏幕像素，ax/ay 漂移振幅，period 秒
UI.mesh = (c, t, { base = '#05060a', blobs = [], blur = 40, scale = 0.25, grain = 0.04, key = 'mesh' } = {}) => {
  const sw = Math.round(W * scale), sh = Math.round(H * scale);
  const a = scratch(key + 'a', sw, sh), g = a.getContext('2d');
  g.globalCompositeOperation = 'source-over'; g.fillStyle = base; g.fillRect(0, 0, sw, sh);
  g.globalCompositeOperation = 'screen';
  for (const b of blobs) {
    const w = 2 * Math.PI / (b.period || 10), ph = b.ph || 0;
    const x = (b.x + (b.ax || 0) * Math.sin(w * t + ph)) * scale, y = (b.y + (b.ay || 0) * Math.cos(w * t * 0.83 + ph)) * scale, r = b.r * scale * (1 + 0.06 * Math.sin(w * t * 1.3 + ph));
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    const [R, G, B] = b.col, A = b.a == null ? 1 : b.a;
    gr.addColorStop(0, `rgba(${R},${G},${B},${A})`); gr.addColorStop(0.45, `rgba(${R},${G},${B},${A * 0.45})`); gr.addColorStop(1, `rgba(${R},${G},${B},0)`);
    g.fillStyle = gr; g.fillRect(0, 0, sw, sh);
  }
  const bb = scratch(key + 'b', sw, sh), gb = bb.getContext('2d');
  // 先铺不模糊的原图再叠模糊版：blur 会从画布外拉进透明像素，边缘半透明——画进不清空的离屏会逐帧累积（qa 确定性 ✗ 就是这么来的）
  gb.clearRect(0, 0, sw, sh); gb.drawImage(a, 0, 0); gb.filter = `blur(${blur * scale}px)`; gb.drawImage(a, 0, 0); gb.filter = 'none';
  c.save(); c.imageSmoothingQuality = 'high'; c.drawImage(bb, 0, 0, W, H);
  if (grain) { c.globalAlpha = grain; c.drawImage(UI.grainTex(), 0, 0); }
  c.restore();
};
// 静态颗粒（种子固定）：对抗 8bit 视频编码里大面积渐变的色带
UI.grainTex = () => PAINT.cached('gl_grain' + W + 'x' + H, W, H, (g) => {
  const r = U.rng(77), im = g.createImageData(W, H), d = im.data;
  for (let i = 0; i < d.length; i += 4) { const v = r() * 255; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
  g.putImageData(im, 0, 0);
});

// ---------- 背景的模糊副本（毛玻璃取样用） ----------
UI.backdrop = (src, { scale = 0.25, blur = 28, key = 'bd' } = {}) => {
  const sw = Math.round(W * scale), sh = Math.round(H * scale);
  const a = scratch(key, sw, sh), g = a.getContext('2d');
  g.clearRect(0, 0, sw, sh); g.drawImage(src, 0, 0, sw, sh); g.filter = `blur(${blur * scale}px)`; g.drawImage(src, 0, 0, sw, sh); g.filter = 'none';   // 同上：先垫原图防边缘透明
  return a;
};

// ---------- 阴影（只画影子，不画形状本身：形状移到屏外，影子偏移回来） ----------
UI.shadow = (c, x, y, w, h, r, { blur = 60, oy = 28, alpha = 0.45 } = {}) => {
  c.save(); c.shadowColor = `rgba(0,0,0,${alpha})`; c.shadowBlur = blur; c.shadowOffsetX = 4000; c.shadowOffsetY = oy;
  c.fillStyle = '#000'; UI.rr(c, x - 4000, y, w, h, r); c.fill(); c.restore();
};

// ---------- 毛玻璃卡片 ----------
// bd = UI.backdrop(...) 的结果；sample = 取样的屏幕矩形（默认与卡片同位，卡片画进离屏纹理时要传屏幕位置）
UI.glass = (c, { x, y, w, h, r = 28, bd, sample, tint = 0.07, stroke = 0.22, light = 0.16, shadow = true }) => {
  if (shadow) UI.shadow(c, x, y, w, h, r, { blur: 70, oy: 30, alpha: 0.5 });
  c.save(); UI.rr(c, x, y, w, h, r); c.clip();
  if (bd) { const s = bd.width / W, S = sample || { x, y, w, h }; c.drawImage(bd, S.x * s, S.y * s, S.w * s, S.h * s, x, y, w, h); }
  c.fillStyle = `rgba(255,255,255,${tint})`; c.fillRect(x, y, w, h);
  // 顶部受光：上缘一道柔和亮带（玻璃的厚度感）
  const g = c.createLinearGradient(0, y, 0, y + h * 0.5); g.addColorStop(0, `rgba(255,255,255,${light})`); g.addColorStop(1, 'rgba(255,255,255,0)');
  c.fillStyle = g; c.fillRect(x, y, w, h * 0.5);
  c.restore();
  // 1px 描边：左上亮、右下暗（光从左上来）
  c.save(); const sg = c.createLinearGradient(x, y, x + w, y + h);
  sg.addColorStop(0, `rgba(255,255,255,${stroke * 1.8})`); sg.addColorStop(0.5, `rgba(255,255,255,${stroke * 0.5})`); sg.addColorStop(1, `rgba(255,255,255,${stroke})`);
  c.strokeStyle = sg; c.lineWidth = 1.5; UI.rr(c, x + 0.75, y + 0.75, w - 1.5, h - 1.5, r); c.stroke(); c.restore();
};

// ---------- 光扫过（sheen） ----------
// 在 clip 区域里让一道斜向亮带从左扫到右；p∈[0,1]；band 宽 width；angle 弧度
// comp：默认 'lighter'（叠在画面上）；在只含文字的离屏上用 'source-atop'（光只落在字上）
UI.sheen = (c, clipPath, p, { x0 = 0, x1 = W, width = 260, angle = -0.35, alpha = 0.32, col = '255,255,255', comp = 'lighter' } = {}) => {
  if (p <= 0 || p >= 1) return;
  const cx = lerp(x0 - width * 2, x1 + width * 2, p);
  c.save(); if (clipPath) c.clip(clipPath);
  c.globalCompositeOperation = comp;
  c.translate(cx, H / 2); c.rotate(angle);
  const g = c.createLinearGradient(-width, 0, width, 0);
  g.addColorStop(0, `rgba(${col},0)`); g.addColorStop(0.5, `rgba(${col},${alpha})`); g.addColorStop(1, `rgba(${col},0)`);
  c.fillStyle = g; c.fillRect(-width, -H * 1.5, width * 2, H * 3);
  c.restore();
};

// ---------- 伪 3D：把一张纹理绕 Y 轴（竖条切片）或 X 轴（横条切片）转 ----------
// tex: 卡片纹理（任意分辨率），屏幕中心 cx,cy，屏幕尺寸 w×h（未旋转时），ry/rx 弧度，persp 透视距离（px）
// 远侧变暗 shade（0..1），模拟侧光。条宽 strip px。
// 切片边界取整像素、首尾相接（不重叠也不留缝）：重叠 0.75px 防缝的老办法，在半透明纹理/半透明绘制下重叠处透明度叠加，出一条条竖纹横纹。
// 另外先画进离屏再整体按 alpha 贴回，整卡淡入淡出时也不出纹。
UI.persp = (c0, tex, { cx, cy, w, h, ry = 0, rx = 0, persp = 1600, strip = 3, shade = 0.35, alpha = 1 }) => {
  const sc = scratch('persp', W, H), c = sc.getContext('2d'); c.reset();
  c.save();
  const tw = tex.width, th = tex.height;
  if (Math.abs(ry) >= Math.abs(rx)) {
    const n = Math.max(8, Math.ceil(w / strip)), cs = Math.cos(ry), sn = Math.sin(ry);
    const P = u => { const X = (u - 0.5) * w, z = X * sn, s = persp / (persp + z); return [cx + X * cs * s, s]; };
    for (let i = 0; i < n; i++) {
      const u0 = i / n, u1 = (i + 1) / n, [x0, s0] = P(u0), [x1, s1] = P(u1), s = (s0 + s1) / 2;
      const hh = h * s, xa = Math.round(Math.min(x0, x1)), xb = Math.round(Math.max(x0, x1)); if (xb > xa) c.drawImage(tex, u0 * tw, 0, (u1 - u0) * tw, th, xa, cy - hh / 2, xb - xa, hh);
    }
    if (shade && ry) { // 转离观众的一侧暗一点
      const [xa] = P(0), [xb] = P(1), g = c.createLinearGradient(xa, 0, xb, 0), k = Math.min(1, Math.abs(Math.sin(ry)) * 2) * shade;
      g.addColorStop(0, `rgba(0,0,0,${ry > 0 ? 0 : k})`); g.addColorStop(1, `rgba(0,0,0,${ry > 0 ? k : 0})`);
      c.globalCompositeOperation = 'source-atop'; c.fillStyle = g; c.fillRect(Math.min(xa, xb), cy - h, Math.abs(xb - xa), h * 2);
    }
  } else {
    const n = Math.max(8, Math.ceil(h / strip)), cs = Math.cos(rx), sn = Math.sin(rx);
    const P = v => { const Y = (v - 0.5) * h, z = -Y * sn, s = persp / (persp + z); return [cy + Y * cs * s, s]; };
    for (let i = 0; i < n; i++) {
      const v0 = i / n, v1 = (i + 1) / n, [y0, s0] = P(v0), [y1, s1] = P(v1), s = (s0 + s1) / 2;
      const ww = w * s, ya = Math.round(Math.min(y0, y1)), yb = Math.round(Math.max(y0, y1)); if (yb > ya) c.drawImage(tex, 0, v0 * th, tw, (v1 - v0) * th, cx - ww / 2, ya, ww, yb - ya);
    }
  }
  c.restore();
  c0.save(); c0.globalAlpha *= alpha; c0.drawImage(sc, 0, 0); c0.restore();
};

// ---------- 线性图标（SF Symbols 气质：圆头、等线宽） ----------
UI.icon = (c, kind, x, y, s, col, t = 0, lw = 0.075) => {
  c.save(); c.translate(x, y); c.scale(s, s); c.strokeStyle = col; c.fillStyle = col; c.lineWidth = lw; c.lineCap = 'round'; c.lineJoin = 'round';
  if (kind === 'wave') {                    // 语音：5 根会跳的竖条
    const hs = [0.35, 0.7, 1, 0.6, 0.3];
    hs.forEach((h0, i) => { const h = h0 * (0.75 + 0.25 * Math.sin(t * 7 + i * 1.3)); const xx = -0.4 + i * 0.2; c.beginPath(); c.moveTo(xx, -h * 0.45); c.lineTo(xx, h * 0.45); c.stroke(); });
  } else if (kind === 'memory') {           // 记忆：三层叠片
    for (let k = 0; k < 3; k++) { const yy = -0.28 + k * 0.24 + 0.02 * Math.sin(t * 2 + k); c.beginPath(); c.moveTo(-0.45, yy); c.lineTo(0, yy - 0.18); c.lineTo(0.45, yy); c.lineTo(0, yy + 0.18); c.closePath(); c.globalAlpha = k === 0 ? 1 : 0.6; c.stroke(); }
  } else if (kind === 'agent') {            // 智能体：四角星 + 小星
    const r = 0.42 * (1 + 0.05 * Math.sin(t * 3));
    c.beginPath(); c.moveTo(0, -r); c.quadraticCurveTo(0, 0, r, 0); c.quadraticCurveTo(0, 0, 0, r); c.quadraticCurveTo(0, 0, -r, 0); c.quadraticCurveTo(0, 0, 0, -r); c.fill();
    const r2 = 0.16; c.translate(0.36, -0.36); c.beginPath(); c.moveTo(0, -r2); c.quadraticCurveTo(0, 0, r2, 0); c.quadraticCurveTo(0, 0, 0, r2); c.quadraticCurveTo(0, 0, -r2, 0); c.quadraticCurveTo(0, 0, 0, -r2); c.globalAlpha = 0.7; c.fill();
  }
  c.restore();
};
})();


// ===== huashu collage.js =====
// 拼贴材料 CL（Vox / Missing Chapter / Borders 一路）：纸纹、撕纸边、网点照片、胶带、图钉、红线（下垂的线索绳）、
// 毛边荧光笔（multiply）、轻投影、带倾斜滑入放平、剪纸贴纸白边。
// 节奏铁律（y2 调研）：相机 60fps 平滑；桌上的纸片/荧光笔/红笔/打字按 12fps「一拍二」步进（MO.step）——stop-motion 手感的一半。
// 纸纹是贴死的（静态颗粒，不逐帧抖）。素材一次画好走 PAINT.cached，确定性。
(() => {
let W = 1920, H = 1080; U.onStage((w, h) => { W = w; H = h; });   // 画布尺寸跟 U.setStage 走（默认 1920×1080）
const { clamp, lerp, rng } = U;
const CL = window.CL = {};

// ---------- 纸 ----------
// 可平铺纸纹 tile：环面取样的 fbm 明暗（无缝）＋纤维短线＋细颗粒。用 c.createPattern(tile, 'repeat') 铺
CL.paperTile = (key, base, { size = 512, amt = 10, fibers = 260, grain = 10, fiberCol = [120, 100, 70], seed = 3 } = {}) => PAINT.cached('ytpaper_' + key, size, size, (g) => {
  const img = g.createImageData(size, size), d = img.data, b = PAINT.hex(base), r = rng(seed);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const ax = x / size * Math.PI * 2, ay = y / size * Math.PI * 2;
    const n = PAINT.fbm(Math.cos(ax) * 1.6 + 10, Math.sin(ax) * 1.6 + Math.cos(ay) * 1.6, 4) * 0.6 + PAINT.noise(Math.sin(ay) * 3 + 5, Math.cos(ay) * 3) * 0.4;
    const gr = (r() - .5) * grain, i = (y * size + x) * 4;
    d[i] = b[0] + n * amt + gr; d[i + 1] = b[1] + n * amt + gr; d[i + 2] = b[2] + n * amt * 0.9 + gr; d[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  g.lineCap = 'round';
  for (let k = 0; k < fibers; k++) { const x = r() * size, y = r() * size, a = r() * Math.PI, l = 4 + r() * 14;
    g.strokeStyle = PAINT.rgb(fiberCol, 0.05 + r() * 0.08); g.lineWidth = 0.6 + r() * 0.8;
    for (const ox of [0, -size, size]) for (const oy of [0, -size, size]) { g.beginPath(); g.moveTo(x + ox, y + oy); g.quadraticCurveTo(x + ox + Math.cos(a + 1) * l * 0.5, y + oy + Math.sin(a + 1) * l * 0.5, x + ox + Math.cos(a) * l, y + oy + Math.sin(a) * l); g.stroke(); } }
});
CL.fillPaper = (c, tile, x, y, w, h) => { c.save(); c.fillStyle = c.createPattern(tile, 'repeat'); c.fillRect(x, y, w, h); c.restore(); };
// 撕纸边：沿一条线生成锯齿＋纤维的点列（左→右），amp 起伏、seed 固定
CL.tornEdge = (x0, y0, x1, y1, { step = 9, amp = 10, seed = 5 } = {}) => {
  const r = rng(seed), L = Math.hypot(x1 - x0, y1 - y0), n = Math.max(2, Math.round(L / step)), nx = -(y1 - y0) / L, ny = (x1 - x0) / L, o = [];
  for (let i = 0; i <= n; i++) { const q = i / n, k = PAINT.noise(i * 0.18, seed) * amp + (r() - .5) * amp * 0.9; o.push([lerp(x0, x1, q) + nx * k, lerp(y0, y1, q) + ny * k]); }
  return o;
};
// 撕边矩形 (0,0,w,h)：只建路径（beginPath…closePath），调用方 fill / clip
CL.tornRect = (g, w, h, seed, amp = 5) => {
  const pts = [...CL.tornEdge(0, 0, w, 0, { amp, seed }), ...CL.tornEdge(w, 0, w, h, { amp, seed: seed + 1 }), ...CL.tornEdge(w, h, 0, h, { amp, seed: seed + 2 }), ...CL.tornEdge(0, h, 0, 0, { amp, seed: seed + 3 })];
  g.beginPath(); pts.forEach((p, i) => i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])); g.closePath();
};

// ---------- 网点照片 ----------
// 灰度网点：源图（canvas）按亮度转成 45° 网点，cell 像素一格；只留在源图不透明处。结果缓存（key 唯一）
CL.halftone = (key, src, { cell = 7, ink = '#1b1b1b', paper = '#efe9dc', angle = Math.PI / 4, gamma = 1, contrast = 1.15 } = {}) => PAINT.cached('ytht_' + key, src.width, src.height, (g) => {
  const w = src.width, h = src.height, sd = src.getContext('2d').getImageData(0, 0, w, h).data;
  g.fillStyle = paper; g.fillRect(0, 0, w, h); g.fillStyle = ink;
  const ca = Math.cos(angle), sa = Math.sin(angle), R = Math.hypot(w, h);
  g.beginPath();
  for (let v = -R; v < R; v += cell) for (let u = -R; u < R; u += cell) {
    const x = w / 2 + u * ca - v * sa, y = h / 2 + u * sa + v * ca; if (x < -cell || y < -cell || x > w + cell || y > h + cell) continue;
    const ix = clamp(x | 0, 0, w - 1), iy = clamp(y | 0, 0, h - 1), i = (iy * w + ix) * 4;
    if (sd[i + 3] < 10) continue;
    let L = (sd[i] * .3 + sd[i + 1] * .59 + sd[i + 2] * .11) / 255; L = clamp((L - 0.5) * contrast + 0.5); L = Math.pow(L, gamma);
    const rr = cell * 0.62 * Math.sqrt(1 - L); if (rr < 0.4) continue;
    g.moveTo(x + rr, y); g.arc(x, y, rr, 0, Math.PI * 2);
  }
  g.fill();
  g.globalCompositeOperation = 'destination-in'; g.drawImage(src, 0, 0); g.globalCompositeOperation = 'source-over';
});
// 网点照片贴纸：draw(g, w, h) 画源图 → 网点 → 加 border 宽的纸边、一层旧纸色（multiply）。返回缓存 canvas（尺寸 w+2b × h+2b）
CL.photo = (key, w, h, draw, { border = 14, cell = 6, paper = '#f2f0ea', ink = '#1a1a1a', htPaper = '#e8e4da', contrast = 1.3 } = {}) => PAINT.cached('clphoto_' + key, w + border * 2, h + border * 2, (g) => {
  const src = PAINT.canvas(w, h); draw(src.getContext('2d'), w, h);
  const ht = CL.halftone(key + '_ht', src, { cell, ink, paper: htPaper, contrast });
  g.fillStyle = paper; g.fillRect(0, 0, w + border * 2, h + border * 2); g.drawImage(ht, border, border);
  g.globalCompositeOperation = 'multiply'; g.fillStyle = 'rgba(214,205,185,.3)'; g.fillRect(0, 0, w + border * 2, h + border * 2); g.globalCompositeOperation = 'source-over';
});

// ---------- 摆放 ----------
CL.placed = (g, x, y, r, fn) => { g.save(); g.translate(x, y); g.rotate(r); fn(g); g.restore(); };
// 轻投影（Vox 桌面上留一点就够；黑底卡片几乎无投影）
CL.shadowed = (g, fn, { col = 'rgba(50,40,25,.28)', blur = 10, x = 3, y = 6 } = {}) => { g.save(); g.shadowColor = col; g.shadowBlur = blur; g.shadowOffsetX = x; g.shadowOffsetY = y; fn(); g.restore(); };
// 带倾斜滑入、到位放平（MC 实测：滑入带 5–15° 倾斜，约 0.6–0.8s），按 12fps 步进。返回进度 q
CL.slideIn = (g, t, t0, x, y, r, fn, { from = [420, -60], tilt = 0.2, dur = 0.6, fps = 12 } = {}) => {
  const q = clamp((MO.step(t, fps) - t0) / dur); if (q <= 0) return 0;
  const e = MO.quartOut(q);
  g.save(); g.translate(x + from[0] * (1 - e), y + from[1] * (1 - e)); g.rotate(r + tilt * (1 - Math.min(1, e * 1.15))); fn(g); g.restore(); return q;
};
// 胶带：一条半透明撕边纸条
CL.tape = (g, x, y, a, w = 120, col = 'rgba(226,220,200,.85)') => { g.save(); g.translate(x, y); g.rotate(a); g.fillStyle = col; g.translate(-w / 2, -17); CL.tornRect(g, w, 34, (x * 7 + y) | 0, 3); g.fill(); g.restore(); };
// 图钉
CL.pin = (g, x, y, col = '#b8433f') => { g.save(); g.translate(x, y); g.fillStyle = 'rgba(0,0,0,.25)'; g.beginPath(); g.arc(3, 5, 12, 0, 7); g.fill(); g.fillStyle = col; g.beginPath(); g.arc(0, 0, 12, 0, 7); g.fill(); g.fillStyle = 'rgba(255,255,255,.55)'; g.beginPath(); g.arc(-4, -4, 4, 0, 7); g.fill(); g.restore(); };
// 调查墙的线索绳：a→b 之间按长度 sag 下垂的二次曲线点列（配 DG.cum / DG.drawPartial 逐段描出）
CL.stringPts = (a, b, sag = 0.08, n = 48) => { const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2 + Math.hypot(b[0] - a[0], b[1] - a[1]) * sag; const o = []; for (let i = 0; i <= n; i++) { const t = i / n, u = 1 - t; o.push([u * u * a[0] + 2 * u * t * mx + t * t * b[0], u * u * a[1] + 2 * u * t * my + t * t * b[1]]); } return o; };
// 红线：带投影描出前 d 长度（d 用弧长；整根画完传 1e9）
CL.string = (g, pts, cum, d, { col = '#b8433f', lw = 5, shadow = 'rgba(0,0,0,.16)' } = {}) => {
  if (shadow) { g.save(); g.strokeStyle = shadow; g.lineWidth = lw; g.translate(3, 6); DG.drawPartial(g, pts, cum, d); g.restore(); }
  g.save(); g.strokeStyle = col; g.lineWidth = lw; g.lineCap = 'round'; DG.drawPartial(g, pts, cum, d); g.restore();
};
// 毛边荧光笔（multiply，压在印刷字上）：q 0..1 从左扫到右
CL.highlight = (g, x0, y0, w, h, q, col = 'rgba(218,207,8,.9)') => { if (q <= 0) return; g.save(); g.globalCompositeOperation = 'multiply'; g.fillStyle = col;
  g.beginPath(); const ww = w * q; g.moveTo(x0, y0 + 4); for (let i = 0; i <= 20; i++) g.lineTo(x0 + ww * i / 20, y0 + Math.sin(i * 1.7) * 2); g.lineTo(x0 + ww + 6, y0 + h / 2); g.lineTo(x0 + ww, y0 + h);
  for (let i = 20; i >= 0; i--) g.lineTo(x0 + ww * i / 20, y0 + h + Math.sin(i * 2.3) * 2); g.closePath(); g.fill(); g.restore(); };
// 剪纸贴纸白边：把 src（全屏离屏里的角色/物件）向四周膨胀 border 像素铺 col，返回同尺寸离屏；用法：先画返回值、再画 src
CL.stickerEdge = (src, key, border = 12, col = '#f4f2ec', dirs = 16) => {
  const sil = PAINT.scratch(key), sg = sil.getContext('2d'); sg.reset();
  for (let a = 0; a < dirs; a++) sg.drawImage(src, Math.cos(a / dirs * 6.283) * border, Math.sin(a / dirs * 6.283) * border);
  sg.globalCompositeOperation = 'source-in'; sg.fillStyle = col; sg.fillRect(0, 0, W, H);
  return sil;
};
})();


// ===== huashu toon.js =====
// 卡通与扁平插画 TOON：Kurzgesagt 式「无描边、双色分面」、柔光；故事型（storytime）极简角色「豆子花叔」、
// pose-to-pose 换姿势（快切＋身体挤压回弹）、眨眼时刻表、口型读口播包络（一拍二）、汗滴、漫画符号。
(() => {
const W = 1920, H = 1080;
const { clamp, lerp } = U;
const TOON = window.TOON = {};

// ---------- 扁平插画 ----------
// 双色分面（无描边、靠色块分面）：clip(S) → 整块阴影色 → 往光源方向平移 v 填高光 → 再往回挪 w 填本色：右下留阴影带、左上留细高光边
TOON.flat = (c, path, base, sh, hi, v = [10, 12], w = [4, 5]) => {
  c.save(); c.clip(path);
  c.fillStyle = sh || base; c.fill(path);
  c.save(); c.translate(-v[0], -v[1]); c.fillStyle = hi || base; c.fill(path); c.restore();
  c.save(); c.translate(-v[0] + w[0], -v[1] + w[1]); c.fillStyle = base; c.fill(path); c.restore();
  c.restore();
};
// 柔光：径向渐变方块（发光物、眼睛、信号脉冲）
TOON.glow = (c, x, y, r, col, a = 1) => { const g = c.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, PAINT.rgb(PAINT.hex(col), a)); g.addColorStop(1, PAINT.rgb(PAINT.hex(col), 0)); c.fillStyle = g; c.fillRect(x - r, y - r, 2 * r, 2 * r); };
// 竖向渐变（天空）
TOON.vgrad = (c, y0, y1, cols) => { const g = c.createLinearGradient(0, y0, 0, y1); cols.forEach((k, i) => g.addColorStop(i / (cols.length - 1), k)); return g; };
TOON.circle = (x, y, r) => { const p = new Path2D(); p.arc(x, y, r, 0, Math.PI * 2); return p; };
TOON.ellipse = (x, y, rx, ry, a = 0) => { const p = new Path2D(); p.ellipse(x, y, rx, ry, a, 0, Math.PI * 2); return p; };

// ---------- 口型：读口播包络 ----------
// window.VO_ENV = {fps, a:[...]}（0..1 的 RMS 包络，由音频脚本从口播 wav 生成）。口型「一拍二」：每 1/12 秒才换一次形。
TOON.env = t => { const E = window.VO_ENV; if (!E) return 0; const i = Math.round(t * E.fps); return i < 0 || i >= E.a.length ? 0 : E.a[i]; };
TOON.mouth = (t, fps = 12) => { const v = TOON.env(MO.step(t, fps)); return v < 0.06 ? 0 : v < 0.22 ? 1 : 2; };   // 0 闭 1 半 2 张

// ---------- 豆子花叔（故事型极简角色） ----------
// 正面、圆头、豆子身体、面条手臂。标志物：纯白渔夫帽、圆框黑眼镜（另：白T、卡其短裤、黑手表）。
// o: { x, y(脚底), s(头半径 px), pose:{l,r}(手臂目标点，相对肩，头半径单位), expr, mouth(0/1/2), blink, look:[dx,dy],
//      squash(竖向缩放，以脚底为轴，体积守恒), tilt(头歪), bob(头下沉), browUp, lw }
// expr: 'talk' | 'happy' | 'blank'(死鱼眼) | 'worry' | 'shock'。返回 { hl, hr(两手位置), head:[x,y] }
const SK = '#FFE2CC', LINE = '#1B1B1F', HAT = '#FFFFFF', BAND = '#E4E4E8', SHIRT = '#FFFFFF', SHORTS = '#CDBB97', HAIR = '#1B1B1F';
TOON.POSES = {
  rest:  { l: [-0.95, 1.05], r: [0.95, 1.05] },
  talk:  { l: [-0.95, 1.05], r: [1.05, 0.55] },          // 一只手微抬，口播默认
  point: { l: [-0.95, 1.05], r: [1.75, -0.15] },         // 指向画右
  type:  { l: [-0.55, 0.62], r: [0.55, 0.62] },          // 双手在胸前（打字）
  shrug: { l: [-1.45, -0.05], r: [1.45, -0.05], palms: 1 },
  stiff: { l: [-0.78, 1.25], r: [0.78, 1.25] },          // 僵住
};
TOON.bean = (c, o) => {
  const R = o.s, x = o.x, y = o.y, lw = o.lw || Math.max(4, R * 0.05), sq = o.squash || 1;
  const pose = o.pose || TOON.POSES.rest;
  c.save(); c.translate(x, y); c.scale(1 / Math.sqrt(sq), sq);
  c.lineJoin = 'round'; c.lineCap = 'round'; c.strokeStyle = LINE; c.lineWidth = lw;
  const bodyTop = -R * 1.32;
  const shoulderY = bodyTop + R * 0.32, shL = [-R * 0.42, shoulderY], shR = [R * 0.42, shoulderY];
  for (const sx of [-1, 1]) {                                          // 腿：两截短圆柱＋白鞋
    c.fillStyle = SK; c.beginPath(); c.roundRect(sx * R * 0.2 - R * 0.09, -R * 0.24, R * 0.18, R * 0.2, R * 0.05); c.fill(); c.stroke();
    c.fillStyle = '#FAFAF6'; c.beginPath(); c.ellipse(sx * R * 0.22, -R * 0.03, R * 0.17, R * 0.08, 0, 0, Math.PI * 2); c.fill(); c.stroke();
  }
  const body = new Path2D();                                           // 身体（豆子）
  body.moveTo(-R * 0.5, -R * 0.2);
  body.bezierCurveTo(-R * 0.62, -R * 0.75, -R * 0.55, bodyTop + R * 0.05, 0, bodyTop);
  body.bezierCurveTo(R * 0.55, bodyTop + R * 0.05, R * 0.62, -R * 0.75, R * 0.5, -R * 0.2);
  body.bezierCurveTo(R * 0.3, -R * 0.12, -R * 0.3, -R * 0.12, -R * 0.5, -R * 0.2); body.closePath();
  c.fillStyle = SHIRT; c.fill(body);
  c.save(); c.clip(body); c.fillStyle = SHORTS; c.fillRect(-R, -R * 0.42, R * 2, R * 0.5);
  c.strokeStyle = LINE; c.lineWidth = lw * 0.6; c.beginPath(); c.moveTo(0, -R * 0.36); c.lineTo(0, -R * 0.12); c.stroke(); c.restore();
  c.stroke(body);
  c.beginPath(); c.moveTo(-R * 0.6, -R * 0.42); c.quadraticCurveTo(0, -R * 0.36, R * 0.6, -R * 0.42); c.lineWidth = lw * 0.8; c.stroke(); c.lineWidth = lw;
  // 面条手臂：肩 → 手（二次曲线，肘自然下垂），双描边做出「管子」
  const arm = (sh, tgt, side) => {
    const hx = sh[0] + tgt[0] * R * 0.62, hy = sh[1] + tgt[1] * R * 0.62;
    const mx = (sh[0] + hx) / 2 + side * R * 0.08, my = Math.max(sh[1], hy) + R * 0.12;
    c.beginPath(); c.moveTo(sh[0], sh[1]); c.quadraticCurveTo(mx, my, hx, hy);
    c.strokeStyle = LINE; c.lineWidth = R * 0.17 + lw; c.stroke(); c.strokeStyle = SK; c.lineWidth = R * 0.17; c.stroke();
    c.beginPath(); c.moveTo(sh[0], sh[1]); const q = 0.28; c.lineTo(lerp(sh[0], mx, q * 2), lerp(sh[1], my, q * 2));
    c.strokeStyle = LINE; c.lineWidth = R * 0.26 + lw; c.stroke(); c.strokeStyle = SHIRT; c.lineWidth = R * 0.26; c.stroke();
    c.fillStyle = SK; c.strokeStyle = LINE; c.lineWidth = lw; c.beginPath(); c.arc(hx, hy, R * 0.13, 0, Math.PI * 2); c.fill(); c.stroke();
    if (side > 0) {                                                    // 近侧（画右）手腕黑手表
      const ang = Math.atan2(hy - my, hx - mx), wx = hx - Math.cos(ang) * R * 0.17, wy = hy - Math.sin(ang) * R * 0.17;
      c.save(); c.translate(wx, wy); c.rotate(ang + Math.PI / 2); c.fillStyle = '#1D1F22'; c.fillRect(-R * 0.1, -R * 0.04, R * 0.2, R * 0.08); c.restore();
    }
    return [hx, hy];
  };
  c.lineWidth = lw;
  const hl = arm(shL, pose.l, -1), hr = arm(shR, pose.r, 1);
  // 头：略宽的圆，压在身体上
  c.restore(); c.save(); c.translate(x, y); c.scale(1 / Math.sqrt(sq), sq);
  c.lineJoin = 'round'; c.lineCap = 'round'; c.strokeStyle = LINE; c.lineWidth = lw;
  const hy = bodyTop - R * 0.78 + (o.bob || 0);
  c.translate(0, hy); c.rotate(o.tilt || 0);
  c.fillStyle = HAIR;                                                  // 鬓角（帽檐下露一点黑发）
  for (const sx of [-1, 1]) { c.beginPath(); c.ellipse(sx * R * 0.86, -R * 0.12, R * 0.16, R * 0.3, sx * 0.2, 0, Math.PI * 2); c.fill(); }
  c.fillStyle = SK; c.beginPath(); c.ellipse(0, 0, R * 1.0, R * 0.93, 0, 0, Math.PI * 2); c.fill(); c.stroke();
  // 帽子：帽冠＋帽带＋往下斜的帽檐
  const crown = new Path2D(); crown.moveTo(-R * 0.78, -R * 0.42); crown.bezierCurveTo(-R * 0.8, -R * 1.25, R * 0.8, -R * 1.25, R * 0.78, -R * 0.42); crown.closePath();
  c.fillStyle = HAT; c.fill(crown); c.stroke(crown);
  c.save(); c.clip(crown); c.fillStyle = BAND; c.fillRect(-R, -R * 0.62, R * 2, R * 0.18); c.restore();
  c.beginPath(); c.moveTo(-R * 0.79, -R * 0.62); c.quadraticCurveTo(0, -R * 0.7, R * 0.79, -R * 0.62); c.lineWidth = lw * 0.7; c.stroke(); c.lineWidth = lw;
  const brim = new Path2D(); brim.moveTo(-R * 1.18, -R * 0.24); brim.quadraticCurveTo(0, -R * 0.66, R * 1.18, -R * 0.24);
  brim.quadraticCurveTo(R * 1.22, -R * 0.12, R * 1.08, -R * 0.12); brim.quadraticCurveTo(0, -R * 0.44, -R * 1.08, -R * 0.12); brim.quadraticCurveTo(-R * 1.22, -R * 0.12, -R * 1.18, -R * 0.24); brim.closePath();
  c.fillStyle = HAT; c.fill(brim); c.stroke(brim);
  // 眉毛（表情主载体）：[整体上下, 内端相对外端的高差]（负 = 内端抬高 = 担心/委屈）
  const ex = o.expr || 'talk', lk = o.look || [0, 0];
  const brow = { talk: [0, 0], happy: [-0.05, 0.03], blank: [0.03, 0], worry: [-0.02, -0.11], shock: [-0.11, 0.02] }[ex] || [0, 0];
  if (o.browUp) brow[0] -= o.browUp;
  c.lineWidth = lw * 1.4;
  for (const sx of [-1, 1]) { const by = -R * 0.13 + brow[0] * R; c.beginPath(); c.moveTo(sx * R * 0.52, by); c.lineTo(sx * R * 0.2, by + brow[1] * R); c.stroke(); }
  c.lineWidth = lw;
  // 眼镜：两只圆框（标志物，线比轮廓粗 1.4 倍）＋鼻梁；眼睛是黑点
  const gy = R * 0.2, gr = R * 0.27;
  for (const sx of [-1, 1]) {
    const gx = sx * R * 0.37;
    c.fillStyle = 'rgba(255,255,255,0.35)'; c.beginPath(); c.arc(gx, gy, gr, 0, Math.PI * 2); c.fill();
    const pr = ex === 'blank' ? R * 0.035 : ex === 'shock' ? R * 0.05 : R * 0.075;
    c.fillStyle = LINE;
    if (o.blink) { c.beginPath(); c.moveTo(gx - R * 0.08 + lk[0] * R * 0.08, gy + lk[1] * R * 0.06); c.lineTo(gx + R * 0.08 + lk[0] * R * 0.08, gy + lk[1] * R * 0.06); c.lineWidth = lw; c.stroke(); }
    else { c.beginPath(); c.ellipse(gx + lk[0] * R * 0.09, gy + lk[1] * R * 0.07, pr, pr * (ex === 'blank' ? 1 : 1.25), 0, 0, Math.PI * 2); c.fill();
      if (ex !== 'blank') { c.fillStyle = '#fff'; c.beginPath(); c.arc(gx + lk[0] * R * 0.09 - pr * 0.35, gy + lk[1] * R * 0.07 - pr * 0.45, pr * 0.32, 0, Math.PI * 2); c.fill(); } }
    c.lineWidth = lw * 1.5; c.strokeStyle = LINE; c.beginPath(); c.arc(gx, gy, gr, 0, Math.PI * 2); c.stroke();
  }
  c.beginPath(); c.moveTo(-R * 0.1, gy - R * 0.02); c.quadraticCurveTo(0, gy - R * 0.08, R * 0.1, gy - R * 0.02); c.stroke();
  c.lineWidth = lw;
  c.fillStyle = 'rgba(255,140,120,0.35)'; for (const sx of [-1, 1]) { c.beginPath(); c.ellipse(sx * R * 0.68, R * 0.5, R * 0.11, R * 0.06, 0, 0, Math.PI * 2); c.fill(); }
  // 嘴
  const m = o.mouth || 0, my = R * 0.6;
  c.strokeStyle = LINE; c.lineWidth = lw;
  if (ex === 'blank' && !m) { c.beginPath(); c.moveTo(-R * 0.1, my); c.lineTo(R * 0.1, my); c.stroke(); }
  else if (ex === 'worry' && !m) { c.beginPath(); c.moveTo(-R * 0.13, my + R * 0.03); c.quadraticCurveTo(-R * 0.05, my - R * 0.04, 0, my + R * 0.01); c.quadraticCurveTo(R * 0.06, my + R * 0.05, R * 0.13, my - R * 0.01); c.stroke(); }
  else if (!m) { c.beginPath(); c.moveTo(-R * 0.13, my - R * 0.02); c.quadraticCurveTo(0, my + R * 0.1, R * 0.13, my - R * 0.02); c.stroke(); }
  else {
    const mh = m === 1 ? R * 0.1 : R * 0.2, mw = m === 1 ? R * 0.14 : R * 0.18;
    c.fillStyle = '#6E2430'; c.beginPath(); c.moveTo(-mw, my - R * 0.03); c.quadraticCurveTo(0, my - R * 0.06, mw, my - R * 0.03); c.quadraticCurveTo(mw * 0.9, my + mh, 0, my + mh); c.quadraticCurveTo(-mw * 0.9, my + mh, -mw, my - R * 0.03); c.closePath(); c.fill();
    c.save(); c.clip(); c.fillStyle = '#F07B7B'; c.beginPath(); c.ellipse(0, my + mh, mw * 0.6, mh * 0.45, 0, 0, Math.PI * 2); c.fill(); c.restore(); c.stroke();
  }
  c.restore();
  return { hl, hr, head: [x, y + hy * sq] };
};

// ---------- pose-to-pose ----------
// keys = [[t, poseName, expr], ...]（同一条时间轴的秒）。换姿势：手臂 0.1s expoOut 过去（24fps 下 3 帧的「快切」），
// 身体同时挤压 7% 后按弹簧回弹（3.5Hz / 衰减 9）。返回 { pose, expr, squash, since }
TOON.poseAt = (keys, t, poses = TOON.POSES) => {
  let k = 0; for (let i = 0; i < keys.length; i++) if (t >= keys[i][0]) k = i;
  const cur = poses[keys[k][1]], prv = poses[keys[Math.max(0, k - 1)][1]];
  const dt = t - keys[k][0], q = k === 0 ? 1 : MO.expoOut(clamp(dt / 0.1));
  const mix = (a, b) => [lerp(a[0], b[0], q), lerp(a[1], b[1], q)];
  return { pose: { l: mix(prv.l, cur.l), r: mix(prv.r, cur.r), palms: cur.palms }, expr: keys[k][2], squash: k === 0 ? 1 : 1 + MO.settle(dt, -0.07, 3.5, 9), since: dt };
};
// 眨眼：固定时刻表（间隔 2.4–3.6s 的「随机」写死），闭眼 dur 秒（24fps 下 2 帧 ≈ 0.083s；「无语」的慢眨眼 3 帧）
TOON.blinkAt = (t, times, dur = 0.083) => times.some(b => t >= b && t < b + dur) ? 1 : 0;
// 活着：说话时头随口型轻点（每次张嘴下沉 0.015R）＋呼吸（1.6s 一周期，身体 ±1.2%）
TOON.alive = (t, R) => ({ bob: TOON.mouth(t) * R * 0.015, breathe: 1 + 0.012 * Math.sin(2 * Math.PI * t / 1.6) });

// ---------- 漫画符号 ----------
// 汗滴：p 0..1 沿脸侧滑下
TOON.sweat = (c, x, y, r, p) => {
  const yy = y + p * r * 3.2, a = p < 0.85 ? 1 : 1 - (p - 0.85) / 0.15;
  c.save(); c.globalAlpha = a; c.fillStyle = '#9ED8F5'; c.strokeStyle = LINE; c.lineWidth = Math.max(3, r * 0.12);
  c.beginPath(); c.moveTo(x, yy - r * 1.5); c.quadraticCurveTo(x + r * 1.05, yy + r * 0.1, x, yy + r); c.quadraticCurveTo(x - r * 1.05, yy + r * 0.1, x, yy - r * 1.5); c.fill(); c.stroke();
  c.fillStyle = '#fff'; c.beginPath(); c.ellipse(x - r * 0.3, yy - r * 0.05, r * 0.18, r * 0.32, 0, 0, Math.PI * 2); c.fill(); c.restore();
};
// 「？」「！」弹出（白描边，过冲 30%）
TOON.mark = (c, ch, x, y, p, size = 160, color = LINE) => { if (p <= 0) return; TY.pop(c, ch, x, y, p, { size, fam: 'PuHui-Black', color, stroke: '#fff', strokeW: size * 0.12, over: 3 }); };
})();


// ===== huashu clip y1_kurzgesagt.js =====
// 片段 · Kurzgesagt 式扁平科普（y1）：深靛紫夜空、发光的扁平星球＝要讲的那个概念；每个要点是一颗卫星节点，
// 带标签胶囊弹出（过冲 108%）、信号沿连线流动；画面永远有东西在轻轻动（星闪、浮动、光晕呼吸、相机慢推）。
// 招牌是尺度穿行：enter 让相机对数推进一颗卫星，推到它的颜色填满整屏，再从同色的新星球里拉出来——「里面是什么」。
// cues：title（顶部标题逐字上浮；text）· point（当前这一层的一个卫星节点＋标签：text；data.color 换强调色）
//       highlight（相机推近第 data.index 个节点（只数当前这一层的 point，从 0 起；默认最新），其余节点和标签变暗；1.2s 推完）
//       enter（钻进第 data.index 个节点（默认最新）：推进→满屏同色→从新的中心星球拉出；text = 新中心的名字（默认用那个节点的名字）；
//              之后的 point 都是新这一层的卫星；dur 默认 1.6s，at 这一帧开始推）
// data.center：第一层中心星球的名字。data.flow:true：节点按顺序串成一条链（中心→1→2→3），信号依次流过，节点带序号——讲「先后」时用。
// 节点排法横竖屏一致：≤3 个排在上半圈（从左到右），≥4 个绕一整圈（从左边起顺时针）。safe：版面只排在 top..H−bottom 之间。
CLIPS.y1_kurzgesagt = (() => {
const { clamp, lerp, rng } = U;
const P = { sky: ['#05020F', '#0E0631', '#2a1a6e'], star: '#F9FCFB', planet: '#F48301', line: 'rgba(125,211,220,.35)', pulse: '#32BAEE' };
const ACC = ['#32BAEE', '#FACC12', '#BE3A95', '#5CD0B3', '#FC6255', '#8A6CF0'];
const dark = (col, k = 0.72) => PAINT.rgb(PAINT.hex(col).map(v => v * k)), light = col => PAINT.rgb(PAINT.hex(col).map(v => v + (255 - v) * 0.4));
let S, levels, flow;
return {
  fonts: ['PuHui-Heavy', 'PuHui-Medium'],
  safe: true,
  init(ctx) {
    const { W, H, u, safe } = ctx, r = rng(11);
    flow = !!ctx.data.flow;
    const y0 = safe.top + (ctx.of('title').length ? (ctx.portrait ? 300 : 210) * u : 60 * u), y1 = H - safe.bottom - 40 * u;
    const R = (ctx.portrait ? 160 : 140) * u, cx = W / 2, cy = (y0 + y1) / 2 + (ctx.portrait ? 0 : 30 * u);
    S = { cx, cy, R, stars: Array.from({ length: 160 }, () => ({ x: r() * W * 1.4 - W * 0.2, y: r() * H * 1.4 - H * 0.2, r: (0.8 + r() * r() * 3) * u, ph: r() * 6.28, sp: 1 + r() * 2.5 })),
      motes: Array.from({ length: 24 }, () => ({ x: r() * W, y: r() * H, ph: r() * 6.28, d: 0.8 + r() * 0.5, s: (2 + r() * 3) * u })) };
    // 分层：enter 把后面的 point 放进新的一层
    levels = [{ start: 0, center: ctx.data.center || '', col: P.planet, pts: [], hls: [] }];
    for (const q of ctx.cues) {
      const L = levels[levels.length - 1];
      if (q.kind === 'point') L.pts.push(q);
      else if (q.kind === 'highlight') L.hls.push(q);
      else if (q.kind === 'enter') { const D = q.dur || 1.6; L.exit = { q, at: q.at, D, sw: q.at + D * 0.55, idx: q.data && q.data.index };
        levels.push({ start: q.at + D * 0.55, end0: q.at + D, center: '', col: null, pts: [], hls: [], from: L }); }
    }
    // 每层的节点位置（横竖屏同一套角度：≤3 上半圈左→右，≥4 从左边起顺时针一整圈）
    const lab = 46 * u + 70 * u + 45 * u;                               // 节点半径＋标签偏移＋半个标签高：最下面的标签也要在 y1 之内
    const rx = ctx.portrait ? W * 0.34 : Math.min(W * 0.32, 640 * u), ryHalf = Math.min(y1 - y0, H) * (ctx.portrait ? 0.3 : 0.42), ryRing = Math.min((y1 - y0) * (ctx.portrait ? 0.34 : 0.36), y1 - cy - lab, cy - y0 - 60 * u);
    levels.forEach((L, li) => {
      const n = L.pts.length, ring = n >= 4;
      L.nodes = L.pts.map((q, i) => { const ang = ring ? Math.PI + i / n * Math.PI * 2 : Math.PI + (i + 0.5) / n * Math.PI;
        const ry = ring ? ryRing : ryHalf; return { q, i, x: cx + Math.cos(ang) * rx, y: cy + Math.sin(ang) * ry, col: (q.data && q.data.color) || ACC[(i + li * 2) % ACC.length] }; });
      if (L.exit) { const nd = L.exit.idx != null ? L.nodes[L.exit.idx] : L.nodes.filter(z => z.q.at <= L.exit.at).pop() || L.nodes[L.nodes.length - 1];
        L.exit.node = nd; const N = levels[li + 1]; N.col = nd ? nd.col : ACC[0]; N.center = L.exit.q.text || (nd && nd.q.text) || ''; }
      // 本层的相机关键帧：慢推 ≈2%/s；highlight 推近节点（对数缩放、1.2s、目标点往中心星球偏一点，中心不被裁掉太多）
      const t0 = L.start, keys = [{ t: t0, x: W / 2, y: H / 2, z: 1 }];
      for (const h of L.hls) {
        const nd = h.data && h.data.index != null ? L.nodes[h.data.index] : L.nodes.filter(z => z.q.at <= h.at).pop(); if (!nd) continue;
        const last = keys[keys.length - 1], z0 = last.z * (1 + 0.02 * Math.max(0, h.at - last.t)), Z = (1 + 0.02 * (h.at - t0)) * (ctx.portrait ? 1.35 : 1.45);   // 推近倍数按本层起点算，连续两次 highlight 不叠乘
        const tx = lerp(nd.x, cx, 0.35), ty = lerp(nd.y, cy, 0.35);
        keys.push({ t: h.at, x: last.x, y: last.y, z: last.z > 1.2 ? last.z : z0, ease: MO.linear }); keys.push({ t: h.at + 1.2, x: tx, y: ty, z: Z, ease: MO.sineInOut }); h.node = nd;
      }
      const last = keys[keys.length - 1], tEnd = L.exit ? L.exit.at : ctx.dur; keys.push({ t: Math.max(last.t + 0.01, tEnd), x: last.x, y: last.y, z: last.z * (1 + 0.02 * Math.max(0, tEnd - last.t)), ease: MO.linear });
      L.keys = keys;
    });
  },
  draw(c, t, ctx) {
    const { W, H, u } = ctx;
    // 当前层：过了切换点就是新的一层；切换点 ±0.05s 两层交叉溶解（两边都是满屏同一种颜色，剪点看不见）
    let k = 0; levels.forEach((L, i) => { if (i > 0 && t >= L.start) k = i; });
    drawLevel(c, k, t, ctx);
    const L = levels[k], ex = L.exit;
    if (ex && t > ex.sw - 0.05 && t < ex.sw) { const buf = UI.scratch('clip_y1x', W, H), g = buf.getContext('2d'); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, W, H);
      drawLevel(g, k + 1, t, ctx); c.save(); c.globalAlpha = (t - (ex.sw - 0.05)) / 0.1; c.drawImage(buf, 0, 0); c.restore(); }
    // 标题：逐字上浮（屏幕层，不跟相机）；第一次 highlight / enter 时淡出
    const qt = ctx.of('title')[0];
    if (qt) { const nx = ctx.of('highlight', 'enter')[0], out = nx ? 1 - MO.sineInOut(clamp((t - nx.at) / 0.5)) : 1;
      if (out > 0) { c.save(); c.globalAlpha = out; const fs = TY.fit(c, qt.text || '', W * 0.88, (ctx.portrait ? 84 : 76) * u, 'PuHui-Heavy', { maxLines: 2, balance: true });
        fs.lines.forEach((ln, i) => TY.charsIn(c, ln, W / 2, ctx.safe.top + (ctx.portrait ? 200 : 130) * u + i * fs.size * 1.2, ctx.lt(t, qt.at) - i * 0.2, { font: `${fs.size}px "PuHui-Heavy"`, color: '#fff', dur: 0.5, stagger: 0.045, dy: 30 * u, align: 'center', shadow: { color: 'rgba(10,8,40,.6)', y: 6 * u, blur: ctx.alpha ? 14 * u : 0 } }));
        c.restore(); } }
  },
};
// 相机：本层关键帧；正在钻出去 → 推进那个节点到满屏；刚钻进来 → 从满屏的中心星球拉出
function camAt(L, t, ctx) {
  const { W, H } = ctx;
  if (L.from && t < L.end0) {                                            // 拉出：z 从「中心星球盖满屏」对数降到 1，星球从屏幕中心回到它的位置
    const e = MO.cubicInOut(clamp((t - L.start) / (L.end0 - L.start))), Z = CAM.coverZoom(S.R) * 1.15;
    return CAM.anchor(S.cx, S.cy, CAM.zlerp(Z, 1, e), lerp(W / 2, S.cx, e), lerp(H / 2, S.cy, e));
  }
  const ex = L.exit;
  if (ex && t >= ex.at && ex.node) {                                      // 推进：对数缩放，节点平滑移到画面中心，推到它的圆盖满屏
    const base = CAM.at(L.keys, ex.at), e = MO.cubicIn(clamp((t - ex.at) / (ex.sw - ex.at)) ) * 0.5 + MO.cubicInOut(clamp((t - ex.at) / (ex.sw - ex.at))) * 0.5;
    return CAM.pushTo(base, ex.node.x, ex.node.y, CAM.coverZoom(46 * ctx.u) * 1.2, e, e);
  }
  return CAM.at(L.keys, t);
}
function drawLevel(c, k, t, ctx) {
  const { W, H, u } = ctx, L = levels[k], cam = camAt(L, t, ctx), { cx, cy, R } = S;
  if (!ctx.alpha) {
    c.fillStyle = TOON.vgrad(c, 0, H, P.sky); c.fillRect(0, 0, W, H);
    CAM.layer(c, cam, 0.05, g => { for (const s of S.stars) { g.globalAlpha = 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(t * s.sp + s.ph)); g.fillStyle = P.star; g.beginPath(); g.arc(s.x, s.y, s.r, 0, 7); g.fill(); } g.globalAlpha = 1; });
  }
  // highlight 生效时：被推近的节点亮，其余节点和标签暗下去
  const hNow = [...L.hls].reverse().find(h => t >= h.at && h.node), dimK = hNow ? MO.smooth(clamp((t - hNow.at) / 0.6)) : 0;
  const col = L.col, fyP = MO.float(t, 6 * u, 4), pull = L.from && t < L.end0 ? MO.smooth(clamp(((t - L.start) / (L.end0 - L.start) - 0.45) / 0.45)) : 1;   // 刚钻进来：先是满屏纯色，名字和环在拉出后半段才出来
  CAM.layer(c, cam, 1, g => {
    // 连线＋信号脉冲（flow：中心→1→2→3 串成链，信号依次流过）
    L.nodes.forEach((nd, i) => {
      const l = ctx.lt(t, nd.q.at); if (l <= 0) return;
      const from = flow && i > 0 ? L.nodes[i - 1] : { x: cx, y: cy }, p = MO.cubicOut(clamp(l / 0.4)), ex = lerp(from.x, nd.x, p), ey = lerp(from.y, nd.y, p);
      g.save(); g.globalAlpha = 1 - 0.6 * dimK * (hNow && hNow.node === nd ? 0 : 1);
      g.strokeStyle = P.line; g.lineWidth = 4 * u; g.beginPath(); g.moveTo(from.x, from.y); g.lineTo(ex, ey); g.stroke();
      if (flow && l > 0.4) { const ax = lerp(from.x, nd.x, 0.55), ay = lerp(from.y, nd.y, 0.55), a = Math.atan2(nd.y - from.y, nd.x - from.x); g.fillStyle = 'rgba(125,211,220,.7)'; g.translate(ax, ay); g.rotate(a); g.beginPath(); g.moveTo(10 * u, 0); g.lineTo(-8 * u, -9 * u); g.lineTo(-8 * u, 9 * u); g.closePath(); g.fill(); }
      g.restore();
      for (let kk = 0; kk < 3; kk++) { if (l < 0.4) break; const q = flow ? ((t * 0.7 - i * 0.33 + kk / 3) % 1 + 1) % 1 : ((l * 0.8 + kk / 3) % 1); const x = lerp(from.x, nd.x, q), y = lerp(from.y, nd.y, q); TOON.glow(g, x, y, 20 * u, P.pulse, 0.6 * (1 - 0.7 * dimK)); g.fillStyle = '#e9fdff'; g.beginPath(); g.arc(x, y, 5 * u, 0, 7); g.fill(); }
    });
    // 中心星球：双色分面＋光晕呼吸＋环
    TOON.glow(g, cx, cy, R * (2.4 + 0.1 * Math.sin(t * 1.6)), light(col), 0.28);
    TOON.flat(g, TOON.circle(cx, cy + fyP, R), col, dark(col), light(col), [-18 * u, 14 * u], [-6 * u, 5 * u]);
    g.save(); g.globalAlpha = pull; g.strokeStyle = 'rgba(255,230,190,.55)'; g.lineWidth = 6 * u; g.beginPath(); g.ellipse(cx, cy, R * 1.55, R * 0.35, -0.25, Math.PI * 1.05, Math.PI * 1.95); g.stroke(); g.restore();
    if (L.center) { const f = TY.fit(g, L.center, R * 1.7, 56 * u, 'PuHui-Heavy', { maxLines: 2, min: 0.5, balance: true });   // 名字放进星球里：先缩字号，再折两行
      g.save(); g.globalAlpha = pull; g.font = `${f.size}px "PuHui-Heavy"`; g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle';
      f.lines.forEach((ln, i) => g.fillText(ln, cx, cy + fyP + (i - (f.lines.length - 1) / 2) * f.size * 1.1)); g.restore(); }
    // 节点＋标签胶囊（弹出 0.5s，过冲 108%）
    for (const nd of L.nodes) {
      const l = ctx.lt(t, nd.q.at); if (l <= 0) continue;
      const on = hNow && hNow.node === nd, a = 1 - 0.65 * dimK * (on ? 0 : 1);
      const pop = MO.backOut(clamp(l / 0.5), 1.1), fy = MO.float(t, 8 * u, 3.4 + nd.i * 0.4, nd.i), nr = 46 * u * pop * (on ? 1 + 0.15 * dimK : 1);
      g.save(); g.globalAlpha = a;
      TOON.glow(g, nd.x, nd.y + fy, nr * (on ? 4.2 : 2.6), nd.col, on ? 0.7 : 0.45);
      TOON.flat(g, TOON.circle(nd.x, nd.y + fy, nr), nd.col, dark(nd.col), '#ffffff', [-8 * u, -8 * u], [-3 * u, -3 * u]);
      if (flow) { g.font = `${30 * u * pop}px "PuHui-Heavy"`; g.fillStyle = '#1f1a3a'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(nd.i + 1), nd.x, nd.y + fy + 2 * u); }
      const gone = L.exit ? 1 - clamp((t - L.exit.at) / 0.2) : 1;   // 钻进去时：推进一开始这一层的标签全收掉（不然被放大成半屏白块，邻居的也会）
      const lp = MO.backOut(clamp((l - 0.15) / 0.5), 1.1) * gone; if (lp > 0) {
        const sc = lp * (on ? 1 + 0.18 * dimK : 1);
        const lf = TY.fit(g, nd.q.text || '', ctx.W * 0.44 - 56 * u, (ctx.portrait ? 44 : 38) * u, 'PuHui-Medium', { maxLines: 1 });   // 长标签先缩字号
        g.font = `${lf.size}px "PuHui-Medium"`; const tw = g.measureText(nd.q.text || '').width, bw = tw + 56 * u, bh = 70 * u;
        const ly = nd.y + fy + (nd.y < cy - R * 0.5 && ctx.portrait ? -nr - 70 * u : nr + 70 * u);   // 标签放节点下方（竖屏上半圈放上方）
        const z = cam.z || 1, vx0 = cam.x - ctx.W / 2 / z, vx1 = cam.x + ctx.W / 2 / z, hw = bw * sc / 2 + 20 * u / z;   // 当前镜头里看得见的世界范围（慢推会把边上的标签推出画）
        const lx = nd.x > vx0 && nd.x < vx1 ? (vx1 - vx0 > 2 * hw ? clamp(nd.x, vx0 + hw, vx1 - hw) : cam.x) : nd.x;   // 节点在画里，标签就夹回画里
        g.translate(lx, ly); g.scale(sc, sc);
        g.fillStyle = 'rgba(20,40,20,.25)'; g.beginPath(); g.roundRect(-bw / 2 + 6 * u, -bh / 2 + 8 * u, bw, bh, bh / 2); g.fill();
        g.fillStyle = '#ffffff'; g.beginPath(); g.roundRect(-bw / 2, -bh / 2, bw, bh, bh / 2); g.fill();
        if (on) { g.strokeStyle = nd.col; g.lineWidth = 5 * u * dimK; g.stroke(); }
        g.fillStyle = '#1f1a3a'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(nd.q.text || '', 0, 2 * u);
      }
      g.restore();
    }
    // 萤火微粒
    for (const m of S.motes) { const x = m.x + Math.sin(t * 0.8 + m.ph) * 30 * u, y = m.y - ((t * 22 * u + m.ph * 40 * u) % (120 * u)); g.globalAlpha = 0.4 + 0.4 * Math.sin(t * 2 + m.ph); g.fillStyle = '#fff6d0'; g.beginPath(); g.arc(x, y, m.s, 0, 7); g.fill(); }
    g.globalAlpha = 1;
  });
}
})();


// ===== huashu clip y2_vox.js =====
// 片段 · Vox 式拼贴（y2）：一张大桌面＝世界，素材按出场顺序摆在桌上、红线连起来；相机 60fps 平滑追过去，
// 桌上的东西（滑入、荧光笔、红圈、打字）按 12fps「一拍二」步进。
// cues：image | clip（截图/照片/剪报：image 路径；text 可选＝贴在下方的黑底标签；data.halftone:true 转成灰度网点，默认保留原色；at 这一帧开始滑入）
//       title（Borders 式黑底白字标签条，打字 13 字/秒）· point（横线索引卡，打字）
//       highlight（当前这张图上 data.rect=[x,y,w,h]（0..1，相对图片）：at 起荧光笔扫过 → 红笔圈住 → 相机推近那一块）
// 截图等比放（不裁），只加纸白边和胶带；横屏素材左→右排、竖屏上→下排，相机沿红线追（长尾缓动＋短运动模糊）。
CLIPS.y2_vox = (() => {
const { clamp, lerp, rng } = U;
const C = { desk: '#e4ddcf', ink: '#171716', red: '#b8433f', paper: '#eeedeb', card: '#f6f4ee' };
const BOLD = '"PuHui-Heavy"', TXT = '"PuHui-Bold"';
let items, keys, strings;
// 一拍二的片内时间：at 这一帧就 >0
const ls = (t, at) => MO.step(t - at + 1e-6, 12) + 1 / 12;
return {
  fonts: ['PuHui-Heavy', 'PuHui-Bold'],
  safe: true,
  init(ctx) {
    const { W, H, u, portrait } = ctx, r = rng(7), g = document.createElement('canvas').getContext('2d');
    items = []; let cur = 0, prev = null;
    for (const q of ctx.of('image', 'clip', 'title', 'point')) {
      let w, h, kind = q.kind === 'clip' ? 'image' : q.kind, lines = null, size = 0;
      if (kind === 'image') {
        const im = ctx.IMG[q.image]; if (!im) continue;
        const bw = W * (portrait ? 0.84 : 0.62), bh = H * (portrait ? 0.46 : 0.62), f = CLIP.fit(im.width, im.height, 0, 0, bw, bh);
        w = f.w + 32 * u; h = f.h + 32 * u + (q.text ? 70 * u : 0);
      } else {
        if (!String(q.text ?? '').trim()) { console.warn(`y2 ${q.kind} at=${q.at}：text 是空的，这张卡跳过`); continue; }   // 空卡宽度是 −Infinity，撕边点列会死循环
        size = (kind === 'title' ? 64 : 48) * u;
        lines = TY.wrap(g, q.text || '', W * (portrait ? 0.74 : 0.5), `${size}px ${kind === 'title' ? BOLD : TXT}`);
        g.font = `${size}px ${kind === 'title' ? BOLD : TXT}`;
        w = Math.max(...lines.map(l => g.measureText(l).width)) + (kind === 'title' ? 80 : 110) * u; h = lines.length * size * 1.45 + (kind === 'title' ? 50 : 100) * u;
      }
      const gap = 260 * u, jitter = (r() - 0.5) * (portrait ? W * 0.12 : H * 0.12);
      const x = portrait ? W / 2 + jitter : (prev ? prev.x + prev.w / 2 + gap + w / 2 : W / 2);
      const y = portrait ? (prev ? prev.y + prev.h / 2 + gap + h / 2 : H / 2) : H / 2 + jitter;
      const it = { q, kind, x, y, w, h, rot: (r() - 0.5) * 0.07, lines, size, seed: 11 + items.length * 7 };
      items.push(it); prev = it; cur++;
    }
    // 文字卡的打字速度与「读完」时刻：默认 13 字/秒；离下一件太近就提速（最多 40 字/秒），保证相机走之前打完、再停 0.5s 让人读完
    items.forEach((it, i) => {
      if (it.kind === 'image') { it.done = it.q.at + 0.6; return; }
      const nC = [...(it.q.text || '')].length, next = items[i + 1] ? items[i + 1].q.at : ctx.dur;
      it.cps = clamp(nC / Math.max(0.3, next - it.q.at - 0.95 - 0.5), 13, 40);
      it.done = it.q.at + nC / it.cps + 0.5;
    });
    // 红线：上一件的右缘（竖屏下缘）钉到下一件的左缘（上缘）
    strings = items.slice(1).map((b, i) => { const a = items[i];
      const pa = portrait ? [a.x + a.w * 0.2, a.y + a.h / 2 - 20 * u] : [a.x + a.w / 2 - 30 * u, a.y + a.h * 0.25];
      const pb = portrait ? [b.x - b.w * 0.2, b.y - b.h / 2 + 20 * u] : [b.x - b.w / 2 + 30 * u, b.y - b.h * 0.25];
      const pts = CL.stringPts(pa, pb, 0.06); return { a: pa, b: pb, pts, cum: DG.cum(pts), at: b.q.at }; });
    // 相机关键帧：每件到场前 ≈0.9s 起追过去（长尾），在它的 at 之前落定；高亮时推近那一块
    const Hs = H - ctx.safe.top - ctx.safe.bottom;                           // 让开字幕带后的可用高度；画面中心同步下移/上移（draw 里平移）
    const zFor = it => clamp(Math.min(W * 0.82 / it.w, Hs * 0.8 / it.h), 0.5, 1.6);
    keys = []; let last = null;
    const hls = ctx.of('highlight');
    const events = [...items.map(it => ({ at: it.q.at, it })), ...hls.map(h => ({ at: h.at, h }))].sort((a, b) => a.at - b.at);
    let curItem = null;
    for (const e of events) {
      if (e.it) {
        const tgt = { x: e.it.x, y: e.it.y, z: zFor(e.it) };
        if (!last) { keys.push({ t: 0, ...tgt }); }
        else { const t0 = Math.max(last.t + 0.25, e.at - 0.95, Math.min(curItem ? curItem.done : 0, e.at - 0.4)), t1 = Math.max(t0 + 0.35, e.at - 0.05);   // 上一张卡没读完相机不走（最晚 at−0.4 起跑）
          keys.push({ t: t0, x: last.x, y: last.y, z: last.z }); keys.push({ t: t1, ...tgt, ease: MO.longTail }); }
        last = keys[keys.length - 1]; curItem = e.it;
      } else if (curItem && curItem.kind === 'image' && e.h.data && e.h.data.rect) {
        const im = ctx.IMG[curItem.q.image], R = e.h.data.rect, ib = imgBox(curItem, im, u);
        const cx = curItem.x + (ib.x + (R[0] + R[2] / 2) * ib.w) - curItem.w / 2, cy = curItem.y + (ib.y + (R[1] + R[3] / 2) * ib.h) - curItem.h / 2;
        const rw = R[2] * ib.w, rh = R[3] * ib.h;
        let zFit = Math.min(W * 0.8 / (rw * 1.4 + 32 * u), Hs * 0.8 / (rh * 1.44 + 20 * u));   // 红圈（下面 drawItem 的椭圆）推近后整圈留在画里，左右各留一成（审片：0.9 时手抖的圈和运动模糊仍会出画）
        if (R[2] >= 0.5) zFit = Math.min(zFit, Math.max(last.z, W * 0.96 / curItem.w));          // 框占了半张图以上：推近只会把这张图两侧的字切掉，不推
        const z = Math.min(clamp(Math.min(W * (ctx.portrait ? 0.78 : 0.55) / rw, Hs * 0.55 / rh), last.z, last.z * 2.6), zFit);   // 竖屏宽是瓶颈：框推到约八成画宽
        // 框的起点（荧光笔从左缘扫起）在当前镜头里看得见：先画、at+0.2 再推近；看不见（上一次推近后它在画外）：
        // 先把镜头移过去、at 前一帧到位，再揭开——否则揭开那一帧在画外，观众晚 0.2s 以上才看到（2026-10 竖屏实测晚 7 帧）
        const x0 = cx - rw / 2, inView = Math.abs(x0 - last.x) * last.z < W / 2 - 20 * u && Math.abs(cy - last.y) * last.z < Hs / 2 - rh * last.z / 2;
        const t0 = inView ? Math.max(last.t + 0.1, e.at + 0.2) : Math.max(last.t + 0.1, e.at - 0.8), t1 = inView ? t0 + 1.0 : Math.max(t0 + 0.35, e.at - 1 / ctx.FPS);
        keys.push({ t: t0, x: last.x, y: last.y, z: last.z }); keys.push({ t: t1, x: cx, y: cy, z, ease: MO.sineInOut });
        last = keys[keys.length - 1];
      }
    }
    if (!keys.length) keys.push({ t: 0, x: W / 2, y: H / 2, z: 1 });
    const end = keys[keys.length - 1]; keys.push({ t: Math.max(end.t + 0.01, ctx.dur), x: end.x, y: end.y, z: end.z * (1 + 0.01 * Math.max(0, ctx.dur - end.t)), ease: MO.linear });   // 停住后 1%/s 慢推
  },
  draw(c, t, ctx) {
    const { W, H, u } = ctx, cam = CAM.at(keys, t);
    const buf = UI.scratch('clip_vox', W, H), g = buf.getContext('2d'); g.reset();
    g.save(); g.translate(0, (ctx.safe.top - ctx.safe.bottom) / 2); CAM.apply(g, cam);
    if (!ctx.alpha) { g.fillStyle = g.createPattern(CL.paperTile('clipdesk', C.desk, { amt: 12, fibers: 320, fiberCol: [110, 95, 70] }), 'repeat'); const s = 4 / cam.z; g.fillRect(cam.x - W * s, cam.y - H * s, W * 2 * s, H * 2 * s); }
    // 红线：跟着下一件的到场描出（12fps）
    for (const s of strings) { const q = MO.sineInOut(clamp(ls(t, s.at - 0.9) / 0.7)); if (t < s.at - 0.9) continue;
      CL.string(g, s.pts, s.cum, s.cum[s.cum.length - 1] * q, { col: C.red, lw: 5 * u }); CL.pin(g, s.a[0], s.a[1], C.red); if (q >= 1) CL.pin(g, s.b[0], s.b[1], C.red); }
    for (const it of items) {
      const q = it.q; if (t < q.at - 1e-6) continue;
      // 一拍二从 at 起算（ls），不从绝对 12fps 网格起算：按网格取整会让素材比 cue 早 1–2 帧露出（2026-10 竖屏实测早了 2 帧）
      CL.slideIn(g, q.at + ls(t, q.at), q.at, it.x, it.y, it.rot, gg => drawItem(gg, it, t, ctx), { from: ctx.portrait ? [120 * u, 360 * u] : [420 * u, -60 * u], tilt: 0.2, dur: 0.55, fps: 1e6 });
    }
    g.restore();
    const [vx, vy] = CAM.velocity(tt => CAM.at(keys, tt), t);
    if (Math.hypot(vx, vy) >= 1.5) c.drawImage(buf, 0, 0);   // 先垫一张不偏移的：motionBlur 第一张就是偏移的，画面四边会留一圈半透明（出片是灰边）
    CAM.motionBlur(c, buf, vx * 0.6, vy * 0.6, 7);
    if (!ctx.alpha) {                                                   // 暗角＋静态颗粒（纸纹是贴死的，不逐帧抖）
      const v = PAINT.cached('clip_vox_vig' + W + 'x' + H, W, H, gg => { const r = gg.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.45, W / 2, H / 2, Math.max(W, H) * 0.62); r.addColorStop(0, 'rgba(40,30,20,0)'); r.addColorStop(1, 'rgba(40,30,20,.34)'); gg.fillStyle = r; gg.fillRect(0, 0, W, H); });
      c.drawImage(v, 0, 0);
    }
  },
};
function imgBox(it, im, u) { const f = CLIP.fit(im.width, im.height, 16 * u, 16 * u, it.w - 32 * u, it.h - 32 * u - (it.q.text ? 70 * u : 0)); return f; }
function drawItem(g, it, t, ctx) {
  const { u } = ctx, q = it.q, w = it.w, h = it.h;
  if (it.kind === 'image') {
    const im = ctx.IMG[q.image], f = imgBox(it, im, u);
    CL.shadowed(g, () => { g.fillStyle = '#f2f0ea'; g.fillRect(-w / 2, -h / 2, w, h); }, { blur: 12 * u, x: 3 * u, y: 7 * u });
    if (q.data && q.data.halftone) { const src = PAINT.canvas(Math.round(f.w), Math.round(f.h)); src.getContext('2d').drawImage(im, 0, 0, src.width, src.height); g.drawImage(CL.halftone('clipimg' + it.seed, src, { cell: 5 * u, contrast: 1.25 }), -w / 2 + f.x, -h / 2 + f.y, f.w, f.h); }
    else g.drawImage(im, -w / 2 + f.x, -h / 2 + f.y, f.w, f.h);
    g.save(); g.globalCompositeOperation = 'multiply'; g.fillStyle = 'rgba(214,205,185,.18)'; g.fillRect(-w / 2, -h / 2, w, h); g.restore();   // 印在纸上：压一层旧纸色
    CL.tape(g, -w / 2 + 40 * u, -h / 2 + 8 * u, -0.6, 120 * u); CL.tape(g, w / 2 - 40 * u, -h / 2 + 8 * u, 0.5, 120 * u);
    if (q.text) { const fs = TY.fit(g, q.text, w - 72 * u, 34 * u, 'PuHui-Bold', { maxLines: 1, min: 0.5 }); g.font = `${fs.size}px ${TXT}`;   // 标签条按字宽量，放不下就缩字
      g.fillStyle = C.ink; g.fillRect(-w / 2 + 16 * u, h / 2 - 72 * u, g.measureText(q.text).width + 40 * u, 56 * u); g.fillStyle = C.paper; g.textBaseline = 'middle'; g.fillText(q.text, -w / 2 + 36 * u, h / 2 - 43 * u); }
    // 高亮：荧光笔（multiply，一拍二扫过）→ 红笔圈
    for (const hq of ctx.of('highlight')) {
      if (!hq.data || !hq.data.rect || hq.at < q.at) continue;
      const own = ctx.of('image', 'clip').filter(z => z.at <= hq.at).pop(); if (own !== q) continue;
      const l = t - hq.at; if (l < -1e-6) continue;
      const R = hq.data.rect, x0 = -w / 2 + f.x + R[0] * f.w, y0 = -h / 2 + f.y + R[1] * f.h, rw = R[2] * f.w, rh = R[3] * f.h;
      CL.highlight(g, x0 - 6 * u, y0 - 4 * u, rw + 12 * u, rh + 8 * u, MO.sineInOut(clamp(ls(t, hq.at) / 0.6)));
      const cq = MO.cubicOut(clamp((ls(t, hq.at) - 0.5) / 0.6));
      const newer = ctx.of('highlight').find(z => z.at > hq.at && z.at <= t + 1e-6 && z.data && z.data.rect && ctx.of('image', 'clip').filter(y => y.at <= z.at).pop() === q);
      const fa = newer ? 1 - 0.75 * clamp((t - newer.at) / 0.3) : 1;   // 同一张图圈了下一处：旧红圈退成淡痕，免得两个圈叠着、旧圈出画
      if (cq > 0) { const pts = DG.hand(DG.ellipsePts(x0 + rw / 2, y0 + rh / 2, rw * 0.7 + 16 * u, rh * 0.72 + 10 * u, -2.6, 1.08, 44), { amp: 2.2 * u, seed: it.seed }), L = DG.cum(pts);
        g.save(); g.globalAlpha *= fa; g.strokeStyle = C.red; g.lineWidth = 7 * u; g.lineCap = 'round'; g.lineJoin = 'round'; DG.drawPartial(g, pts, L, L[L.length - 1] * cq); g.restore(); }
    }
  } else {
    const n = Math.ceil(ls(t, q.at) * (it.cps || 13)), title = it.kind === 'title';
    CL.shadowed(g, () => { g.fillStyle = title ? C.ink : C.card; g.save(); g.translate(-w / 2, -h / 2); CL.tornRect(g, w, h, it.seed, 3 * u); g.fill(); g.restore(); }, { blur: 10 * u, x: 3 * u, y: 6 * u });
    if (!title) { g.strokeStyle = 'rgba(80,140,200,.3)'; g.lineWidth = 2 * u; for (let y = -h / 2 + 90 * u; y < h / 2; y += it.size * 1.45) { g.beginPath(); g.moveTo(-w / 2, y); g.lineTo(w / 2, y); g.stroke(); }
      g.strokeStyle = 'rgba(184,67,63,.5)'; g.beginPath(); g.moveTo(-w / 2, -h / 2 + 56 * u); g.lineTo(w / 2, -h / 2 + 56 * u); g.stroke(); }
    g.fillStyle = title ? C.paper : C.ink; g.font = `${it.size}px ${title ? BOLD : TXT}`; g.textBaseline = 'alphabetic';
    let k = 0; it.lines.forEach((ln, li) => { let x = -w / 2 + (title ? 40 : 55) * u; const y = -h / 2 + (title ? 25 * u : 70 * u) + (li + 1) * it.size * 1.3;
      for (const ch of ln) { if (k < n) g.fillText(ch, x, y); x += g.measureText(ch).width; k++; } });
  }
}
})();


// ===== huashu clip y3_whiteboard.js =====
// 片段 · 白板手绘（y3，RSA 型）：说一句写一句、讲到东西就画出来。一支马克笔在白板上写字、画图标、画箭头、圈重点，相机跟着往下走，最后拉远看全图。
// cues：title（标题，7.5 字/秒起，赶不上下一个 cue 会自动提速）· point（要点：先画一个小圈当项目符号再写，16 字/秒起）
//       draw（画一个手绘图标：data.icon 见下方 ICONS，text = 图标下的手写说明；和刚写的那条字并排；连续的 draw 用橙箭头连起来，像示范片「一摞图 → 网络」；data.arrow:false 不画箭头）
//       image（截图/草图：笔走之字形把它「揭开」，再描一圈手绘框；等比放，不裁）
//       highlight（橙圈圈住一条：data.index = 第几个要点（只数 point，从 0 起，和 t1/y1 同一口径）；
//                 data.target = 'title' | 'draw' | 'image' 改圈那一类（配 index 数那一类，默认最新）；不写 = 最新写出的那一条；data.style:'underline' 改成划线）
// 版式：横屏有 draw/image 时左文右图，竖屏有 draw 时图标在右侧窄栏；图标和刚写的那条字并排，连续的图标之间画往下的橙箭头。
//       横屏纯文字、要点 ≥5 条时分左右两栏。字号随内容多少在 0.72–1.2 倍之间取，尽量一屏放下并撑满；放不下才让相机往下走。
// 每一笔都在 at 这一帧落下第一点；写字速度＝max(基准速度, 字数 / 到下一个 cue 的间隔)，说到下一句时这句已写完。
// 笔：两笔之间空档 <0.6s 沿弧线滑过去；更长就 0.35s 退出右下屏外，下一笔前 0.3s 再进来；最后一笔画完一定退场，不留在字上。
// safe（spec.safe）：内容只排在 top..H−bottom 之间，收尾拉远也只拉进这一带。alpha：墨色外面描一圈白边，叠在深色画面上也看得见。
CLIPS.y3_whiteboard = (() => {
const { clamp, lerp } = U;
const INK = '#0A0503', ORANGE = '#EF7226', WASH = 'rgba(239,114,38,.22)', BOARD = '#FBFBFB';
const FONT = 'LXGWWenKai-500';
// ---------- 手绘图标：单位框 [-0.5,0.5]²（y 朝下）里的笔画；o:1 = 橙色强调，seg:1 = 快速直线（网络连线），fill = 铺浅橙 ----------
const A = (cx, cy, rx, ry, a0, a1, n = 22) => DG.arcPts(cx, cy, rx, ry, a0, a1, n);
const box = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0 + 0.01]];
const ICONS = {
  person: () => [{ p: A(0, -0.3, 0.13, 0.13, -Math.PI / 2, Math.PI * 1.55) }, { p: [[0, -0.17], [0, 0.14]] }, { p: [[-0.24, -0.04], [0, -0.1], [0.24, -0.04]] }, { p: [[-0.17, 0.44], [0, 0.14], [0.17, 0.44]] }],
  cat: () => { const k = 1 / 380, T = pts => pts.map(([x, y]) => [x * k, y * k + 0.44]);
    return [{ p: T([[-60, -250], [-92, -330], [-30, -300], [30, -300], [92, -330], [60, -250]]) }, { p: T(A(0, -230, 100, 82, -0.55, Math.PI + 0.55, 26).reverse()) },
      { p: T([[-80, -170], [-120, -60], [-110, 0], [110, 0], [120, -60], [80, -170]]), fill: T([[-80, -170], [-120, -60], [-110, 0], [110, 0], [120, -60], [80, -170], [0, -150]]) },
      { p: T([[110, -20], [190, -40], [210, -130], [170, -190]]) }, { p: T(A(-38, -240, 12, 16, 0, Math.PI * 2, 14)), w: 0.8 }, { p: T(A(38, -240, 12, 16, 0, Math.PI * 2, 14)), w: 0.8 },
      { p: T([[-30, -196], [-110, -206]]), w: 0.6 }, { p: T([[30, -196], [110, -206]]), w: 0.6 }]; },
  network: () => { const C = [[-0.38, [-0.28, 0, 0.28]], [0, [-0.33, -0.11, 0.11, 0.33]], [0.38, [-0.14, 0.14]]], r = 0.06, out = [];
    for (const [x, ys] of C) for (const y of ys) out.push({ p: A(x, y, r, r, -Math.PI / 2, Math.PI * 1.55, 14), fast: 1 });
    for (let a = 0; a < 2; a++) for (const y0 of C[a][1]) for (const y1 of C[a + 1][1]) out.push({ seg: [[C[a][0] + r, y0], [C[a + 1][0] - r, y1]] });
    return out; },
  doc: () => [{ p: [[0.14, -0.42], [-0.3, -0.42], [-0.3, 0.42], [0.3, 0.42], [0.3, -0.26], [0.14, -0.42], [0.14, -0.26], [0.3, -0.26]] },
    { p: [[-0.18, -0.12], [0.18, -0.12]], w: 0.7 }, { p: [[-0.18, 0.04], [0.18, 0.04]], w: 0.7 }, { p: [[-0.18, 0.2], [0.06, 0.2]], w: 0.7 }],
  chat: () => [{ p: [[-0.3, -0.36], [0.3, -0.36], [0.42, -0.24], [0.42, 0.1], [0.3, 0.22], [-0.06, 0.22], [-0.24, 0.4], [-0.2, 0.22], [-0.3, 0.22], [-0.42, 0.1], [-0.42, -0.24], [-0.3, -0.36]] },
    ...[-0.18, 0, 0.18].map(x => ({ p: A(x, -0.07, 0.035, 0.035, 0, Math.PI * 2, 10), w: 0.9, fast: 1 }))],
  bulb: () => [{ p: A(0, -0.12, 0.26, 0.26, Math.PI * 0.72, Math.PI * 2.28, 30), fill: A(0, -0.12, 0.26, 0.26, 0, Math.PI * 2, 30) }, { p: [[-0.11, 0.12], [-0.11, 0.27], [0.11, 0.27], [0.11, 0.12]] },
    { p: [[-0.08, 0.36], [0.08, 0.36]] }, ...[-2.4, -1.57, -0.74].map(a => ({ p: [[Math.cos(a) * 0.34, -0.12 + Math.sin(a) * 0.34], [Math.cos(a) * 0.46, -0.12 + Math.sin(a) * 0.46]], o: 1, fast: 1 }))],
  check: () => [{ p: [[-0.32, 0.0], [-0.08, 0.26], [0.36, -0.32]], o: 1, w: 1.5 }],
  cross: () => [{ p: [[-0.28, -0.28], [0.28, 0.28]], o: 1, w: 1.5 }, { p: [[0.28, -0.28], [-0.28, 0.28]], o: 1, w: 1.5 }],
  screen: () => [{ p: box(-0.44, -0.36, 0.44, 0.2) }, { p: [[0, 0.2], [0, 0.34]] }, { p: [[-0.2, 0.36], [0.2, 0.36]] },
    { p: [[-0.32, -0.22], [0.06, -0.22]], w: 0.6, fast: 1 }, { p: [[-0.24, -0.1], [0.24, -0.1]], w: 0.6, fast: 1 }, { p: [[-0.24, 0.02], [0.12, 0.02]], w: 0.6, fast: 1 }],
  stack: () => [2, 1, 0].map(i => ({ p: box(-0.4 + i * 0.09, -0.22 - i * 0.09, 0.2 + i * 0.09, 0.3 - i * 0.09), paper: 1 })).concat([{ p: [[-0.3, 0.2], [-0.14, 0.02], [0, 0.14], [0.1, 0.06], [0.2, 0.2]], w: 0.7 }]),
  gear: () => { const pts = []; for (let i = 0; i <= 64; i++) { const a = i / 64 * Math.PI * 2, r = (Math.floor(i / 4) % 2) ? 0.3 : 0.4; pts.push([Math.cos(a) * r, Math.sin(a) * r]); }
    return [{ p: pts }, { p: A(0, 0, 0.12, 0.12, 0, Math.PI * 2.1, 16) }]; },
  target: () => [{ p: A(0, 0.04, 0.4, 0.4, -Math.PI / 2, Math.PI * 1.55, 32) }, { p: A(0, 0.04, 0.25, 0.25, -Math.PI / 2, Math.PI * 1.55, 24) }, { p: A(0, 0.04, 0.09, 0.09, 0, Math.PI * 2, 12), fill: A(0, 0.04, 0.09, 0.09, 0, Math.PI * 2, 12) },
    { p: [[0.46, -0.42], [0.02, 0.02]], o: 1 }, { p: [[0.3, -0.42], [0.46, -0.42], [0.46, -0.26]], o: 1 }],
  clock: () => [{ p: A(0, 0, 0.4, 0.4, -Math.PI / 2, Math.PI * 1.55, 32) }, { p: [[0, -0.26], [0, 0], [0.18, 0.1]] }],
  chart: () => [{ p: [[-0.42, -0.42], [-0.42, 0.38], [0.44, 0.38]] }, { p: [[-0.32, 0.22], [-0.12, 0.04], [0.06, 0.12], [0.34, -0.26]], o: 1 }, { p: [[0.18, -0.26], [0.34, -0.26], [0.34, -0.1]], o: 1 }],
};
let B, keys, items, OFF, lastEnd, band;
const halo = (c, src, W, H, col, r) => {                          // 透明底：墨色外描一圈白边（8 个方向各压一次剪影）
  const s2 = UI.scratch('clip_y3halo', W, H), g = s2.getContext('2d'); g.globalCompositeOperation = 'source-over'; g.clearRect(0, 0, W, H);
  g.drawImage(src, 0, 0); g.globalCompositeOperation = 'source-in'; g.fillStyle = col; g.fillRect(0, 0, W, H); g.globalCompositeOperation = 'source-over';
  for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; c.drawImage(s2, Math.cos(a) * r, Math.sin(a) * r); }
  c.drawImage(src, 0, 0);
};
// 折行但各行等长：先按栏宽折出 n 行，再找「仍是 n 行」的最窄宽度重折（不留一个字孤零零挂在第二行）
const balance = (g, text, maxW, font) => { const n = TY.wrap(g, text, maxW, font).length; if (n < 2) return [text];
  let lo = maxW * 0.4, hi = maxW; for (let k = 0; k < 14; k++) { const m = (lo + hi) / 2; if (TY.wrap(g, text, m, font).length > n) lo = m; else hi = m; } return TY.wrap(g, text, hi, font); };
// 手绘矩形：先把四条边各加密成多点，再加抖动，角还是角（直接对 4 个角点做 Catmull-Rom 会画成一个圆团）
const rectPts = (x, y, w, h) => { const P = [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y + 3]], out = [];
  for (let i = 0; i < 4; i++) for (let k = 0; k < 12; k++) out.push([lerp(P[i][0], P[i + 1][0], k / 12), lerp(P[i][1], P[i + 1][1], k / 12)]); out.push(P[4]); return out; };
function layout(ctx, f) {
  const { W, H, u, FPS, safe } = ctx, pad = (ctx.portrait ? 80 : 150) * u, lw = 6.5 * u, g = document.createElement('canvas').getContext('2d');
  const cues = ctx.cues, next = q => { const i = cues.indexOf(q); const n = cues.slice(i + 1).find(z => z.at > q.at + 1e-6); return n ? n.at : ctx.dur; };
  const hasDraw = cues.some(q => q.kind === 'draw'), hasImg = cues.some(q => q.kind === 'image' && ctx.IMG[q.image]);
  // 栏：横屏有图就左文右图；竖屏有 draw 时图标排在右侧窄栏、和它说的那条字并排（截图仍整宽）
  const S = (ctx.portrait ? 250 : 210) * u * f, gap = 70 * u * f;
  const two = ctx.portrait ? hasDraw : (hasDraw || hasImg);
  const colP = !two ? null : ctx.portrait ? { x: W - pad - S, w: S } : { x: W * 0.55, w: W * 0.45 - pad };
  const nPt = cues.filter(q => q.kind === 'point').length, split = !ctx.portrait && !two && nPt >= 5 ? Math.ceil(nPt / 2) : 0;   // 横屏纯文字、要点 ≥5 条：分左右两栏写
  const colT = { x: pad, w: split ? W / 2 - pad - 40 * u : !two ? W - 2 * pad : ctx.portrait ? colP.x - gap - pad : W * 0.5 - pad };
  let kPt = 0, col0 = null;
  // 同一份清单的要点用同一个字号：取「放进一行」要缩到的最小那个（最小 0.72），还放不下的那条再折行
  const pSize = Math.min(74 * u * f, ...cues.filter(q => q.kind === 'point').map(q => { const r = TY.fit(g, q.text || '', colT.w - 80 * u, 74 * u * f, FONT, { maxLines: 1, min: 0.72, soft: true }); return r.ok ? r.size : 74 * u * f * 0.72; }));
  const B = DG.board({ ink: INK, lw, speed: 2600 * u, font: `"${FONT}"` }), items = [];
  const top = (ctx.portrait ? 240 : 130) * u + safe.top;
  let yT = top, yP = top, lastText = null, lastDraw = null;               // 文字流 / 图流的当前 y
  const start = q => B.at(Math.max(0, q.at - 1 / FPS));                  // 第一笔在 at 这一帧可见
  const capSz = (ctx.portrait ? 52 : 50) * u * Math.max(0.85, Math.min(1.1, f)), capRight = !ctx.portrait;   // 横屏说明写在图标右边，竖屏写在下面
  for (const q of cues) {
    if (q.kind === 'title' || q.kind === 'point') {
      const title = q.kind === 'title', indent = title ? 0 : 80 * u, colW = title ? W - 2 * pad : colT.w;
      const ft = title ? TY.fit(g, q.text || '', colW - indent, 112 * u * f, FONT, { maxLines: 1, min: 0.72, soft: true }) : { size: pSize, lines: TY.wrap(g, q.text || '', colW - indent, `${pSize}px "${FONT}"`) };   // 标题先缩字号放一行，放不下再折行
      const size = ft.size, lines = ft.lines.length > 1 ? balance(g, q.text || '', colW - indent, `${size}px "${FONT}"`) : ft.lines;
      const n = [...(q.text || '')].length, room = Math.max(0.35, next(q) - q.at - 0.15 - (title ? 0 : 0.15)), rate = Math.max(title ? 7.5 : 16, n / room);
      if (title) yT = Math.max(yT, yP);
      else { if (col0 == null) col0 = yT; if (split && kPt === split) { colT.x = W / 2 + 40 * u; yT = col0; } kPt++; }
      let y = yT + size * (title ? 1.1 : 1.0);
      start(q);
      if (!title) B.line(DG.ellipsePts(colT.x + 26 * u, y - size * 0.32, 15 * u, 15 * u, -1.6, 1.05, 18), { w: lw * 0.9, col: ORANGE, speed: 9000 * u, min: 0.08, gap: 0.02 });
      const x = (title ? pad : colT.x) + indent, it = { q, kind: q.kind, x, y0: y - size, lines, size };
      lines.forEach((ln, k) => B.text(ln, x, y + k * size * 1.3, size, { col: INK, rate }));
      g.font = `${size}px "${FONT}"`; it.w = Math.max(...lines.map(l => g.measureText(l).width)); it.y1 = y + (lines.length - 1) * size * 1.3 + size * 0.25;
      yT = it.y1 + (title ? 60 : 44) * u * f; items.push(it);
      if (title) yP = Math.max(yP, yT); else lastText = it;
    } else if (q.kind === 'draw') {
      const icon = (q.data && q.data.icon) || '', mk = ICONS[icon];
      if (!mk && icon) console.warn(`y3 draw：没有图标「${icon}」，可用：${Object.keys(ICONS).join(' ')}（只写说明字）`);
      // 和刚写的那条字并排（那条字还没配图、且在图流下面）；否则接在图流下面
      const pair = lastText && !lastText.pic && lastText.y0 >= yP - 1;
      const y0 = two ? (pair ? lastText.y0 + (lastText.y1 - lastText.y0) / 2 - S / 2 : yP) : yT;
      const x0 = two ? colP.x : pad, cx = x0 + S / 2, cy = Math.max(y0, two ? yP : y0) + S / 2;
      if (pair) lastText.pic = true;
      const prev = lastDraw && !(q.data && q.data.arrow === false) ? lastDraw : null;
      start(q);
      const room = Math.max(0.5, next(q) - q.at - 0.15), strokes = mk ? mk() : [];
      const P = pts => pts.map(([x, y]) => [cx + x * S, cy + y * S]);
      const capN = [...(q.text || '')].length, arrowL = prev ? Math.max(0, cy - S / 2 - prev.y1) : 0;
      const len = strokes.reduce((s, k) => s + (k.p ? DG.len(P(k.p)) : 0), 0) + arrowL;
      const budget = clamp(room - (capN ? Math.max(0.3, capN / 16) : 0) - 0.1, 0.4, 1.4), sp = Math.max(2600 * u, len / budget);
      if (prev && arrowL > 30 * u) { const ax = prev.cx, ay0 = prev.y1 + 8 * u, ay1 = cy - S / 2 - 8 * u;   // 箭头：上一个图标 → 这一个（往下）
        B.line([[ax, ay0], [ax + 6 * u, lerp(ay0, ay1, 0.5)], [ax, ay1]], { col: ORANGE, w: lw, speed: sp });
        const h = Math.min(20 * u, (ay1 - ay0) * 0.4); B.line([[ax - h, ay1 - h], [ax, ay1], [ax + h, ay1 - h]], { col: ORANGE, w: lw, speed: sp, smooth: false }); }
      for (const k of strokes) {
        if (k.seg) { B.segment(P([k.seg[0]])[0], P([k.seg[1]])[0], { w: 2.5 * u, dur: 0.05, gap: 0.008 }); continue; }
        const pts = P(k.p);
        if (k.paper) B.fill(U.poly(pts), BOARD, 0.01);
        B.line(pts, { col: k.o ? ORANGE : INK, w: lw * (k.w || 1), speed: sp * (k.fast ? 2 : 1), gap: 0.015 });
        if (k.fill) B.fill(U.poly(P(k.fill)), WASH);
      }
      B.at(B.cur + 0.02);
      const capMax = capRight ? W - pad - (x0 + S + 24 * u) : W - 2 * pad, cs = q.text ? TY.fit(g, q.text, capMax, capSz, FONT, { maxLines: 1 }).size : capSz;   // 说明字放不下先缩字号
      g.font = `${cs}px "${FONT}"`; const cw = q.text ? g.measureText(q.text).width : 0, crate = Math.max(16, capN / Math.max(0.3, room - (B.cur - q.at)));
      const capX = capRight ? x0 + S + 24 * u : clamp(cx, pad + cw / 2, W - pad - cw / 2), capY = capRight ? cy + cs * 0.35 : cy + S / 2 + cs * 1.05;   // 竖屏图标在右侧窄栏：居中的说明字夹回画内
      if (q.text) B.text(q.text, capX, capY, cs, { col: INK, align: capRight ? 'left' : 'center', rate: crate });
      const it = { q, kind: 'draw', x: x0, w: S, cx, y0: cy - S / 2, y1: cy + S / 2 + (capRight ? 0 : cs * 1.3),
        hx: capRight ? x0 : Math.min(x0, capX - cw / 2), hw: capRight ? S + 24 * u + cw : Math.max(x0 + S, capX + cw / 2) - Math.min(x0, capX - cw / 2) };
      items.push(it); lastDraw = it;
      if (two) { yP = it.y1 + gap; if (!ctx.portrait) yT = Math.max(yT, pair ? it.y1 + gap * 0.5 : yT); else yT = Math.max(yT, it.y1 + gap * 0.6); }
      else yT = it.y1 + gap;
    } else if (q.kind === 'image') {
      const im = ctx.IMG[q.image]; if (!im) continue;
      // 右栏的图只用剩下的高度，不让相机为右栏往下走、把左栏的字推出画
      const inCol = two && !ctx.portrait, y = (inCol ? yP : Math.max(yT, yP)) + 30 * u, box = inCol ? colP : { x: pad, w: W - 2 * pad };
      const f2 = CLIP.fit(im.width, im.height, box.x, y, box.w, inCol ? Math.max(H * 0.2, Math.min(H * 0.5 * Math.max(0.8, f), H - safe.bottom - 80 * u - y)) : H * (ctx.portrait ? 0.32 : 0.45) * Math.max(0.8, f)), it = { q, kind: 'image', x: f2.x, y0: f2.y, w: f2.w, y1: f2.y + f2.h };
      const zz = DG.zigzag(f2.x - 20 * u, f2.y - 10 * u, f2.w + 40 * u, f2.h + 20 * u, 7), cum = DG.cum(zz), rowH = (f2.h + 20 * u) / 7;
      start(q);
      const s = B.push({ kind: 'custom', t0: B.cur, t1: B.cur + Math.min(1.0, Math.max(0.4, next(q) - q.at - 0.5)), col: INK,
        draw(gg, p) { gg.save(); if (p < 1) gg.clip(DG.revealMask(zz, cum, cum[cum.length - 1] * p, rowH * 2.6)); gg.drawImage(im, f2.x, f2.y, f2.w, f2.h); gg.restore(); return p < 1 ? DG.pointAt(zz, cum, cum[cum.length - 1] * p) : null; },
        start() { return zz[0]; }, end() { return zz[zz.length - 1]; } });
      B.at(s.t1 + 0.02);
      B.line(rectPts(f2.x - 10 * u, f2.y - 10 * u, f2.w + 20 * u, f2.h + 20 * u), { w: lw * 0.8, speed: 9000 * u, amp: 1.0 * u });
      yP = f2.y + f2.h + 60 * u; if (!inCol) yT = yP; items.push(it); lastDraw = null;
    } else if (q.kind === 'highlight') {
      const d = q.data || {}, written = items.filter(z => z.q.at <= q.at), kind = d.target || (d.index != null ? 'point' : null);
      const pool = kind ? written.filter(z => z.kind === kind) : written, all = kind ? items.filter(z => z.kind === kind) : items;
      const it = d.index != null ? all[d.index] : pool[pool.length - 1];
      if (!it) { console.warn(`y3 highlight at=${q.at}：找不到要圈的条目（index=${d.index} target=${d.target || 'point'}）`); continue; }
      start(q);
      const hx = it.hx ?? it.x, hw = it.hw ?? it.w;
      if (d.style === 'underline') B.line([[hx - 10 * u, it.y1 + 12 * u], [hx + hw / 2, it.y1 + 18 * u], [hx + hw + 10 * u, it.y1 + 10 * u]], { col: ORANGE, w: lw * 1.3, speed: 3000 * u });
      else B.line(DG.ellipsePts(hx + hw / 2, (it.y0 + it.y1) / 2, hw * 0.56 + 30 * u, (it.y1 - it.y0) * 0.62 + 24 * u, -2.9, 1.08, 44), { col: ORANGE, w: lw, speed: 3600 * u, amp: 1.6 * u });
    }
  }
  const bottom = Math.max(...items.map(z => z.y1), top);
  return { B, items, bottom, top, pad };
}
return {
  fonts: [FONT],
  safe: true,
  init(ctx) {
    const { W, H, u, safe } = ctx;
    band = { y0: safe.top, y1: H - safe.bottom }; band.h = band.y1 - band.y0;
    // 字号系数 f 从 1.2 往下试：内容少就放大撑满版面，内容多就缩（最小 0.72），还放不下才让相机往下走
    let L; for (let f = 1.2; f >= 0.7; f -= 0.04) { L = layout(ctx, f); if (L.bottom + 70 * u <= band.y1) break; }   // 内容少就放大（最大 1.2）把版面撑满
    ({ B, items } = L);
    OFF = [W + 140 * u, H + 280 * u];
    lastEnd = Math.max(0, ...B.S.map(s => s.t1));
    // 相机：内容超出可用带就在下一条开写前平移过去（sineInOut）；最后有空就拉远看全图（只拉进 safe 带）
    keys = [{ t: 0, x: W / 2, y: H / 2, z: 1 }]; let cy = H / 2, panned = false;
    for (const it of items) {
      const need = it.y1 + 70 * u - (cy + band.y1 - H / 2); if (need <= 0) continue;
      panned = true; cy += need + band.h * 0.12; const t1 = Math.max(keys[keys.length - 1].t + 0.2, it.q.at), t0 = Math.max(keys[keys.length - 1].t + 0.01, t1 - 0.6);
      keys.push({ t: t0, x: W / 2, y: keys[keys.length - 1].y, z: 1 }); keys.push({ t: t1, x: W / 2, y: cy, z: 1, ease: MO.sineInOut });
    }
    const y0c = Math.min(L.top, ...items.map(z => z.y0)) - 50 * u, y1c = L.bottom + 40 * u, total = y1c - y0c;
    const zEnd = Math.min(0.94, band.h / total * 0.96);
    // 收尾拉远：最后一笔后有 ≥0.6s 才拉；相机走过了的话，就算时间紧也在最后 0.8s 拉（不然末帧标题被切在顶上）
    const tz0 = Math.max(keys[keys.length - 1].t + 0.01, panned ? Math.min(lastEnd + 0.1, ctx.dur - 0.8) : lastEnd + 0.1);
    if (ctx.dur - tz0 >= 0.6) {
      const prev = keys[keys.length - 1]; keys.push({ ...prev, t: tz0, ease: undefined });
      const midW = (y0c + y1c) / 2, ty = midW - ((band.y0 + band.y1) / 2 - H / 2) / zEnd;     // 全图中心落在 safe 带中心
      keys.push({ t: Math.min(ctx.dur - 0.15, tz0 + 0.8), x: W / 2, y: ty, z: zEnd, ease: MO.sineInOut });
    }
  },
  draw(c, t, ctx) {
    const { W, H, u } = ctx, cam = CAM.at(keys, t);
    const g = ctx.alpha ? (() => { const b = UI.scratch('clip_y3buf', W, H), x = b.getContext('2d'); x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, W, H); return x; })() : c;
    if (!ctx.alpha) { c.fillStyle = BOARD; c.fillRect(0, 0, W, H); }
    g.save(); CAM.apply(g, cam); const { tip, col } = B.draw(g, t); g.restore();
    const pen = penAt(t, cam, tip, ctx);
    if (pen) DG.pen(g, pen[0], pen[1], col || B.penColor(t), Math.sin(t * 7) * 0.03);
    if (ctx.alpha) halo(c, g.canvas, W, H, 'rgba(255,255,255,0.92)', 4 * u);
  },
};
// 笔的位置（屏幕）：在画 → 笔尖；空档短 → 沿弧线滑；空档长 → 退出右下再进来；画完最后一笔 → 0.35s 退场（返回 null = 不画）
function penAt(t, cam, tip, ctx) {
  if (tip) return CAM.toScreen(cam, tip[0], tip[1]);
  const hs = B.S.filter(s => s.kind !== 'pop' && s.kind !== 'fill' && (s.kind !== 'custom' || s.handed !== false));
  let prev = null, next = null; for (const s of hs) { if (s.t1 <= t) prev = s; else if (s.t0 > t && !next) next = s; }
  if (prev && next && next.t0 - prev.t1 <= 0.6) return B.penAt(t, cam, null);
  const end = s => B.penAt(s.t1, cam, null);                       // 上一笔终点（屏幕）
  if (next && next.t0 - t <= 0.3) { const b = CAM.toScreen(cam, ...(next.kind === 'custom' ? next.start() : next.kind === 'line' ? next.pts[0] : [next.x, next.y - next.size * 0.3])), q = MO.cubicOut(1 - (next.t0 - t) / 0.3); return [lerp(OFF[0], b[0], q), lerp(OFF[1], b[1], q)]; }
  if (prev && t - prev.t1 < 0.35) { const a = end(prev), q = MO.cubicIn((t - prev.t1) / 0.35); return [lerp(a[0], OFF[0], q), lerp(a[1], OFF[1], q)]; }
  return null;
}
})();


// ===== huashu clip y4_storytime.js =====
// 片段 · 故事型简笔角色（y4，storytime）：豆子花叔在小房间里对镜头讲，姿势快切（0.1s）后定住，笑点靠反应特写和插入镜头。
// cues：point | title（他说一句：at 这一帧气泡弹出、开始张嘴；嘴按 text 的字数说 ≈6.5 字/秒；data.pose 指定姿势 talk/point/shrug/type）
//         气泡里写什么：有 sub 就写 sub（≤12 字的短句最好），没有才写 text——口播原文交给管线的字幕，气泡别和字幕重复一遍
//       highlight（「？」「！」弹出＋摊手担心：data.mark 默认「？」）
//       react（反应镜头：at 这一帧跳到大特写，死鱼眼＋汗滴，一言不发；dur 默认到下一个 cue）
//       insert（插入镜头：at 这一帧切到桌上那台电脑的屏幕特写——sub = 他打的那一行（打字、发送），之后 AI 的回复像代码墙一样越滚越快，
//               text = 砸出来的大字（如「3000行」），在 data.hit（绝对秒，默认 at+1.3，没有 sub 时 at+0.4）砸出、屏幕一震；
//               data.count + data.unit 让右上角计数器在砸出那一刻停在这个数；dur 默认到下一个 cue）
// 房间里有桌上的电脑（插入镜头切进去的就是它的屏幕）、马克杯、墙上的书架和绿植，右边不再是空墙。
// 没有口播包络时，口型由字数和时间合成（一拍二，0/1/2 三张嘴）；有包络就把 window.VO_ENV 交给管线（TOON.mouth 自动读）。
// safe：气泡、插入镜头的大字排在 top..H−bottom 之间。alpha：不画房间，角色和气泡照画（气泡本来就是白底黑字）；插入镜头只画显示器和后脑勺，四周透明。
CLIPS.y4_storytime = (() => {
const { clamp, lerp, rng } = U;
const C = { wall: '#DCE9EC', wall2: '#CFE0E4', floor: '#E8DCC8', desk: '#E2C29B', deskSide: '#CDA97F', line: '#5B6470', sky: '#BFE3F5', screen: '#20222D' };
const COLS = ['#C792EA', '#82AAFF', '#C3E88D', '#F78C6C', '#89DDFF', '#FFCB6B', '#676E95'];
const LINES = (() => { const r = rng(42), out = []; let ind = 0;
  for (let i = 0; i < 400; i++) { if (r() < 0.18) ind = Math.min(4, ind + 1); else if (r() < 0.2) ind = Math.max(0, ind - 1);
    const toks = []; let x = ind * 34; const n = 1 + (r() * 4 | 0); for (let k = 0; k < n; k++) { const w = 30 + r() * 150; toks.push([x, w, COLS[(r() * COLS.length) | 0]]); x += w + 14; } out.push(toks); } return out; })();
let keys, says, L, shots;
return {
  fonts: ['PuHui-Bold', 'PuHui-Black'],
  safe: true,
  init(ctx) {
    const { W, H, u } = ctx;
    L = { cx: ctx.portrait ? W * 0.42 : W * 0.3, fy: ctx.portrait ? H * 0.8 : H * 1.02, R: (ctx.portrait ? 230 : 215) * u };
    L.dy = L.fy - L.R * 0.55;                                             // 桌面高度
    L.mon = ctx.portrait ? { x: W * 0.66, w: 330 * u, h: 240 * u } : { x: W * 0.6, w: 520 * u, h: 340 * u };   // 桌上的电脑（屏幕左上角 x、宽高）
    says = ctx.of('point', 'title').map(q => ({ q, end: q.at + Math.max(0.6, [...(q.text || '')].length / 6.5) }));
    keys = [[0, 'rest', 'happy']]; let n = 0;
    for (const q of ctx.cues) {
      if (q.kind === 'point' || q.kind === 'title') keys.push([q.at, (q.data && q.data.pose) || (n++ % 2 ? 'point' : 'talk'), 'talk']);
      if (q.kind === 'highlight') keys.push([q.at, 'shrug', 'worry']);
      if (q.kind === 'react' || q.kind === 'insert') keys.push([q.at, 'stiff', 'blank']);
    }
    // 特写镜头（react / insert）占的时间段
    shots = ctx.of('react', 'insert').map(q => { const nx = ctx.cues.find(z => z.at > q.at + 1e-6); const end = q.dur ? q.at + q.dur : nx ? nx.at : ctx.dur + 1;
      const hit = q.kind === 'insert' ? (q.data && q.data.hit != null ? +q.data.hit : q.at + (q.sub ? 1.3 : 0.4)) : 0; return { q, end, hit }; });
  },
  draw(c, t, ctx) {
    const { W, H, u } = ctx, ts = MO.step(t, 24);                         // 角色 24fps，镜头 60fps
    const shot = shots.find(s => t >= s.q.at - 1e-6 && t < s.end);
    if (shot && shot.q.kind === 'insert') return insert(c, t, ctx, shot);
    const inReact = !!shot;
    // 镜头：中景慢推 3%；反应＝同机位跳到 1.9 倍大特写（砸镜：前 0.3s 从 2.2 弹回）
    let z = 1.0 + 0.03 * MO.quintOut(clamp(t / 3)), cy = L.fy - L.R * 1.6;
    if (inReact) { const l = t - shot.q.at; z = 1.9 * (1 + 0.15 * Math.max(0, 1 - MO.springHz(l, 3, 11))) * (1 + 0.03 * clamp(l / 1.5)); cy = L.fy - L.R * 2.2; }
    const [dx, dy] = CAM.drift(t, 3 * u, 1);
    if (!ctx.alpha) { c.fillStyle = C.wall; c.fillRect(0, 0, W, H); }
    CAM.with(c, { x: (inReact ? L.cx : W / 2) + dx, y: (inReact ? cy : H / 2) + dy, z }, g => {
      if (!ctx.alpha) room(g, t, ctx);
      const st = TOON.poseAt(keys, ts), say = says.find(s => t >= s.q.at - 1e-6 && t < s.end);
      const mouth = window.VO_ENV ? TOON.mouth(t) : say ? [1, 2, 1, 0, 2, 1][Math.floor(MO.step(t - say.q.at + 1e-6, 12) * 12) % 6] : 0;
      const blink = TOON.blinkAt(ts, [0.9, 3.4, 6.1, 8.8, 11.5]), br = 1 + 0.012 * Math.sin(2 * Math.PI * t / 1.6);
      const info = TOON.bean(g, { x: L.cx, y: L.fy, s: L.R, pose: st.pose, expr: inReact ? 'blank' : st.expr, mouth: inReact ? 0 : mouth, blink, squash: st.squash * br, bob: mouth * L.R * 0.015, tilt: st.expr === 'worry' ? -0.06 : 0.02 * Math.sin(t), lw: inReact ? 12 * u : undefined });
      if (!ctx.alpha) desk(g, t, ctx);
      if (inReact) { const sw = clamp((t - shot.q.at - 0.3) / 0.9); if (sw > 0) TOON.sweat(g, L.cx + L.R * 0.88, info.head[1] - L.R * 0.1, 30 * u, MO.expoOut(sw) * 0.6 + sw * 0.4); }
      for (const h of ctx.of('highlight')) { const l = ctx.lt(t, h.at); if (l <= 0) continue;
        g.save(); g.translate(info.head[0] + L.R * 1.35, info.head[1] - L.R * 0.6); g.rotate(0.14 + 0.05 * Math.sin(t * 6)); TOON.mark(g, (h.data && h.data.mark) || '？', 0, 0, clamp(l / 0.26), 130 * u); g.restore(); }
    });
    // 对话气泡（屏幕空间，不跟镜头）：at 这一帧弹出，说完 0.6s 后收起；特写切回来后，特写之前的那句不再出现
    if (!inReact) for (const s of says) {
      const l = ctx.lt(t, s.q.at), out = clamp((t - s.end - 0.6) / 0.2); if (l <= 0 || out >= 1) continue;
      const nx = says[says.indexOf(s) + 1]; if (nx && t >= nx.q.at - 1e-6) continue;
      if (shots.some(sh => sh.q.at > s.q.at && sh.q.at <= t)) continue;
      const txt = s.q.sub || s.q.text || '';
      const bw0 = ctx.portrait ? W * 0.84 : W * 0.42, f = TY.fit(c, txt, bw0 - 80 * u, (ctx.portrait ? 64 : 62) * u, 'PuHui-Bold', { maxLines: 2, min: 0.72, balance: true });
      c.font = `${f.size}px "PuHui-Bold"`; const bw = Math.min(bw0, Math.max(...f.lines.map(x => c.measureText(x).width)) + 80 * u);
      const bh = f.lines.length * f.size * 1.3 + 56 * u, bx = ctx.portrait ? (W - bw) / 2 : W * 0.5, by = ctx.safe.top + (ctx.portrait ? H * 0.06 : H * 0.09);
      const sc = MO.backOut(clamp(l / 0.25), 2.2) * (1 - out);
      c.save(); c.translate(bx + 60 * u, by + bh); c.scale(sc, sc); c.translate(-(bx + 60 * u), -(by + bh));
      c.fillStyle = '#fff'; c.strokeStyle = '#1B1B1F'; c.lineWidth = 6 * u; c.lineJoin = 'round';
      c.beginPath(); c.roundRect(bx, by, bw, bh, 40 * u); c.moveTo(bx + 70 * u, by + bh); c.lineTo(bx + (ctx.portrait ? 40 : 10) * u, by + bh + 60 * u); c.lineTo(bx + 130 * u, by + bh); c.fill(); c.stroke();
      c.fillStyle = '#fff'; c.fillRect(bx + 76 * u, by + bh - 8 * u, 50 * u, 12 * u);
      c.fillStyle = '#1B1B1F'; c.textBaseline = 'alphabetic';
      f.lines.forEach((ln, i) => c.fillText(ln, bx + 40 * u, by + 26 * u + (i + 1) * f.size * 1.2));
      c.restore();
    }
  },
};
// 房间：墙、地、窗（慢飘的云）、书架、画框。只画一次的东西不缓存——片段短，画得起
function room(g, t, ctx) {
  const { W, H, u } = ctx, X0 = -W, Y0 = -H, fy = L.fy;
  g.fillStyle = C.wall; g.fillRect(X0, Y0, W * 3, H * 3);
  g.fillStyle = C.floor; g.fillRect(X0, fy + 10 * u, W * 3, H * 2);
  g.lineWidth = 3 * u; g.strokeStyle = C.line; g.lineJoin = 'round'; g.beginPath(); g.moveTo(X0, fy + 10 * u); g.lineTo(X0 + W * 3, fy + 10 * u); g.stroke();
  // 窗：天空＋慢飘的云
  const wx = ctx.portrait ? W * 0.08 : W * 0.05, wy = ctx.portrait ? H * 0.2 : H * 0.12, ww = ctx.portrait ? W * 0.3 : W * 0.17, wh = ww * 0.7;
  g.save(); g.beginPath(); g.roundRect(wx, wy, ww, wh, 12 * u); g.clip(); g.fillStyle = C.sky; g.fillRect(wx, wy, ww, wh);
  for (const [k, r, sp] of [[0.2, 46, 18], [0.7, 34, 12]]) { const x = wx - 60 * u + ((k * ww + t * sp * u) % (ww + 120 * u)), y = wy + wh * (0.35 + k * 0.3); g.fillStyle = '#fff'; g.beginPath(); g.arc(x, y, r * u, 0, 7); g.arc(x + r * 0.9 * u, y + 6 * u, r * 0.75 * u, 0, 7); g.arc(x - r * 0.9 * u, y + 8 * u, r * 0.65 * u, 0, 7); g.fill(); }
  g.restore(); g.strokeStyle = C.line; g.beginPath(); g.roundRect(wx, wy, ww, wh, 12 * u); g.moveTo(wx + ww / 2, wy); g.lineTo(wx + ww / 2, wy + wh); g.moveTo(wx, wy + wh / 2); g.lineTo(wx + ww, wy + wh / 2); g.stroke();
  // 墙上：横屏右边一块书架；竖屏书架在窗下方右侧
  const r = rng(9), sx = ctx.portrait ? W * 0.62 : W * 0.68, sy = ctx.portrait ? H * 0.3 : H * 0.36, sw = ctx.portrait ? W * 0.3 : W * 0.22;
  g.fillStyle = '#D9C3A0'; g.fillRect(sx, sy, sw, 16 * u); g.strokeRect(sx, sy, sw, 16 * u);
  let x = sx + 16 * u; while (x < sx + sw * 0.7) { const w = (22 + r() * 22) * u, h = (70 + r() * 40) * u; g.fillStyle = ['#8FB3C9', '#E7A37B', '#B9C98F', '#C6A0C9'][(r() * 4) | 0]; g.fillRect(x, sy - h, w, h); g.strokeRect(x, sy - h, w, h); x += w + 5 * u; }
  g.fillStyle = '#E7A37B'; const px = sx + sw * 0.83; g.beginPath(); g.moveTo(px - 30 * u, sy); g.lineTo(px - 38 * u, sy - 56 * u); g.lineTo(px + 38 * u, sy - 56 * u); g.lineTo(px + 30 * u, sy); g.closePath(); g.fill(); g.stroke();
  g.fillStyle = '#8DBF8B'; for (const [dx, a] of [[-18, -0.5], [0, 0], [18, 0.5]]) { g.beginPath(); g.ellipse(px + dx * u, sy - 100 * u, 16 * u, 46 * u, a, 0, 7); g.fill(); g.stroke(); }
}
// 桌子挡在腰前（角色线最黑最粗，背景线细而灰）；桌上的电脑、马克杯
function desk(g, t, ctx) {
  const { W, H, u } = ctx, X0 = -W, dy = L.dy; g.lineWidth = 3 * u; g.strokeStyle = C.line;
  // 电脑：屏幕微微发光，上面是滚动的代码色块（插入镜头切进去就是它）
  const M = L.mon, mx = M.x, my = dy - M.h - 60 * u;
  g.fillStyle = '#9AA0AC'; g.fillRect(mx + M.w / 2 - 18 * u, dy - 62 * u, 36 * u, 62 * u); g.strokeRect(mx + M.w / 2 - 18 * u, dy - 62 * u, 36 * u, 62 * u);
  g.beginPath(); g.ellipse(mx + M.w / 2, dy, 90 * u, 10 * u, 0, 0, 7); g.fill(); g.stroke();
  g.fillStyle = '#2B2D36'; g.beginPath(); g.roundRect(mx, my, M.w, M.h, 16 * u); g.fill(); g.stroke();
  g.save(); g.beginPath(); g.roundRect(mx + 14 * u, my + 14 * u, M.w - 28 * u, M.h - 28 * u, 6 * u); g.clip(); g.fillStyle = C.screen; g.fillRect(mx, my, M.w, M.h);
  const lh = 22 * u, off = (t * 30 * u) % lh; for (let i = 0; i < 20; i++) { const y = my + 30 * u + i * lh - off; for (const [x, w, col] of LINES[i % LINES.length]) { g.fillStyle = col; g.globalAlpha = 0.75; g.fillRect(mx + 26 * u + x * 0.3 * u, y, w * 0.3 * u, 8 * u); } }
  g.restore(); g.globalAlpha = 1;
  g.fillStyle = C.desk; g.fillRect(X0, dy, W * 3, 40 * u); g.strokeRect(X0, dy, W * 3, 40 * u);
  g.fillStyle = C.deskSide; g.fillRect(X0, dy + 40 * u, W * 3, H); g.strokeRect(X0, dy + 40 * u, W * 3, H);
  // 马克杯
  const cx = ctx.portrait ? W * 0.1 : L.cx - L.R * 1.9; g.fillStyle = '#F26B4F'; g.beginPath(); g.roundRect(cx, dy - 92 * u, 70 * u, 92 * u, 10 * u); g.fill(); g.stroke();
  g.beginPath(); g.arc(cx + 78 * u, dy - 52 * u, 22 * u, -1.2, 1.2); g.lineWidth = 9 * u; g.stroke(); g.lineWidth = 3 * u;
}
// 插入镜头：电脑屏幕特写（过肩），打字→发送→代码墙加速滚→大字砸出＋屏幕一震（和示范片 s2_screen 同一套）
function insert(c, t, ctx, shot) {
  const { W, H, u, safe } = ctx, q = shot.q, d = q.data || {}, t0 = q.at, msg = q.sub || '', T_SEND = t0 + (msg ? 0.1 + Math.min(0.9, [...msg].length / 14) + 0.1 : 0.05), T_HIT = Math.max(shot.hit, T_SEND + 0.2);
  const lt = t - t0, slam = t - T_HIT;
  const scrollAt = tt => { const k = Math.max(0, tt - T_SEND - 0.15), span = Math.max(0.3, T_HIT - T_SEND - 0.15); return 46 * u * (k * 6 + Math.pow(k / span * 1.8, 3) * 6); };
  const shake = slam > 0 && slam < 0.3 ? MO.settle(slam, 14 * u, 9, 14) : 0;
  const zoom = 1.06 - 0.06 * MO.expoOut(clamp(lt / 0.25)) + 0.02 * clamp(lt / 2.2) + (slam > 0 ? 0.04 * MO.expoOut(clamp(slam / 0.12)) : 0);   // 切进来：1.06 落到 1（像推过去的一下）
  if (!ctx.alpha) { c.fillStyle = '#15161C'; c.fillRect(0, 0, W, H); }   // 透明模式：只留显示器和后脑勺，口播画面从四周透出来
  const bx = W * 0.06, by = safe.top + H * 0.05, bw = W * 0.88, bh = H - safe.bottom - by - H * (ctx.portrait ? 0.2 : 0.06);
  CAM.with(c, { x: W / 2 + shake, y: H / 2 - shake * 0.5, z: zoom }, c => {
    c.fillStyle = '#2B2D36'; c.beginPath(); c.roundRect(bx, by, bw, bh, 34 * u); c.fill();
    const sx = bx + 40 * u, sy = by + 40 * u, sw = bw - 80 * u, sh = bh - 80 * u;
    c.save(); c.beginPath(); c.roundRect(sx, sy, sw, sh, 14 * u); c.clip(); c.fillStyle = C.screen; c.fillRect(sx, sy, sw, sh);
    const scr = scrollAt(t), top = sy + 64 * u;
    // 我那一行：右侧气泡，打字（光标闪），发送后变蓝
    if (msg) { const bubY = top + 110 * u - Math.min(scr, 600 * u), p = clamp((t - t0 - 0.1) / Math.max(0.2, T_SEND - t0 - 0.2));
      const mf = TY.fit(c, msg, sw * 0.7 - 60 * u, (ctx.portrait ? 44 : 40) * u, 'PuHui-Bold', { maxLines: 3, min: 0.75, balance: true }), fs = mf.size, lh = fs * 1.3;   // 长句折行（最多 3 行），气泡往下长
      const bwid = Math.max(...mf.lines.map(l => TY.width(c, l, fs, 'PuHui-Bold'))) + 60 * u, bht = 78 * u + (mf.lines.length - 1) * lh;
      if (bubY > sy) { c.fillStyle = t >= T_SEND ? '#3B82F6' : '#2F3342'; c.beginPath(); c.roundRect(sx + sw - 30 * u - bwid, bubY - 52 * u, bwid, bht, 24 * u); c.fill();
        let left = Math.round(p * mf.lines.reduce((a, l) => a + [...l].length, 0));   // 逐行打出来，光标跟在最后打到的那一行
        const ns = mf.lines.map(ln => { const n = Math.min(left, [...ln].length); left -= n; return n; }), curK = Math.max(0, ns.findLastIndex(n => n > 0)), blink = t < T_SEND && Math.floor(t * 4) % 2;
        mf.lines.forEach((ln, k) => { if (k > curK) return;
          TY.text(c, [...ln].slice(0, ns[k]).join('') + (blink && k === curK ? '|' : ''), sx + sw - 30 * u - bwid + 30 * u, bubY + 2 * u + k * lh, { size: fs, fam: 'PuHui-Bold', color: '#fff', align: 'left' }); }); } }
    // AI 的回复：代码墙，越滚越快
    if (t > T_SEND + 0.05) { const lh = 46 * u, y0 = top + (msg ? 220 : 40) * u - scr;
      for (let i = Math.max(0, Math.floor((top - y0) / lh)); ; i++) { const y = y0 + i * lh; if (y > sy + sh) break; if (y < top) continue;
        const shown = clamp((t - T_SEND - 0.05 - Math.min(i, 12) * 0.03) / 0.05); if (shown <= 0) continue;
        c.fillStyle = '#4A4F66'; c.fillRect(sx + 30 * u, y - 12 * u, 44 * u, 16 * u);
        for (const [x, w, col] of LINES[i % LINES.length]) { c.fillStyle = col; c.globalAlpha = 0.9; c.beginPath(); c.roundRect(sx + 110 * u + x * u, y - 14 * u, w * u * shown, 20 * u, 10 * u); c.fill(); } c.globalAlpha = 1; }
      const v = (scrollAt(t + 1 / 60) - scr) * 60; if (v > 1500 * u) { c.fillStyle = `rgba(32,34,45,${clamp((v - 1500 * u) / (6000 * u)) * 0.5})`; c.fillRect(sx, top, sw, sh); } }
    // 顶栏：三个圆点、标题、计数器（砸出那一刻停在 data.count）
    c.fillStyle = '#2A2D3A'; c.fillRect(sx, sy, sw, 64 * u);
    ['#FF5F57', '#FEBC2E', '#28C840'].forEach((col, i) => { c.fillStyle = col; c.beginPath(); c.arc(sx + 42 * u + i * 34 * u, sy + 32 * u, 10 * u, 0, 7); c.fill(); });
    TY.text(c, d.app || 'AI助手', sx + sw / 2, sy + 44 * u, { size: 30 * u, fam: 'PuHui-Bold', color: '#9AA3B8' });
    if (d.count != null && t > T_SEND + 0.1) { const N = +d.count, k = slam >= 0 ? N : Math.min(N - 1, Math.floor(Math.pow(clamp((t - T_SEND - 0.15) / Math.max(0.2, T_HIT - T_SEND - 0.15)), 2.4) * (N - 1)));
      TY.text(c, `${k}${d.unit || ''}`, sx + sw - 30 * u, sy + 44 * u, { size: 32 * u, fam: 'PuHui-Bold', color: slam >= 0 ? '#FFCB6B' : '#9AA3B8', align: 'right' }); }
    // 大字砸出（backOut 过冲）＋压暗
    if (slam > 0 && q.text) { c.fillStyle = `rgba(10,10,16,${0.6 * MO.expoOut(clamp(slam / 0.15))})`; c.fillRect(sx, sy, sw, sh);
      const fs = TY.fit(c, q.text, sw * 0.86, (ctx.portrait ? 200 : 230) * u, 'PuHui-Black', { maxLines: 2, min: 0.4, balance: true });
      fs.lines.forEach((ln, i) => TY.pop(c, ln, sx + sw / 2, sy + sh / 2 + fs.size * 0.35 + (i - (fs.lines.length - 1) / 2) * fs.size * 1.1, clamp(slam / 0.24), { size: fs.size, fam: 'PuHui-Black', color: '#FFCB6B', over: 3.5, stroke: '#15161C', strokeW: 18 * u })); }
    c.restore();
  });
  // 前景：他的后脑勺（过肩），随呼吸起伏，砸出时往后一缩
  const hx = W * (ctx.portrait ? 0.2 : 0.17), hy = by + bh - 60 * u + 6 * u * Math.sin(2 * Math.PI * t / 1.6) + (slam > 0 ? -MO.settle(slam, 18 * u, 4, 7) : 0), s = (ctx.portrait ? 0.8 : 0.75) * u;
  c.save(); c.translate(hx, hy); c.scale(s, s); c.lineWidth = 7; c.strokeStyle = '#1B1B1F'; c.lineJoin = 'round';
  c.fillStyle = '#FFFFFF'; c.beginPath(); c.ellipse(0, 300, 330, 200, 0, Math.PI, 0); c.fill(); c.stroke();
  c.fillStyle = '#FFE2CC'; c.beginPath(); c.roundRect(-70, 120, 140, 110, 30); c.fill(); c.stroke();
  for (const k of [-1, 1]) { c.beginPath(); c.ellipse(k * 196, 40, 34, 52, k * 0.2, 0, 7); c.fill(); c.stroke(); }
  c.fillStyle = '#1B1B1F'; c.beginPath(); c.ellipse(0, 40, 190, 175, 0, 0, 7); c.fill();
  c.fillStyle = '#FFFFFF'; c.beginPath(); c.moveTo(-180, -20); c.bezierCurveTo(-180, -290, 180, -290, 180, -20); c.closePath(); c.fill(); c.stroke();
  c.beginPath(); c.ellipse(0, -12, 280, 64, 0, 0, 7); c.fill(); c.stroke();
  c.restore();
}
})();


// ===== huashu clip y5_kinetic_type.js =====
// 片段 · 动态文字（y5）：字就是演员。一个 cue 一页：主词在 at 这一帧砸进来（弹簧过冲 ≈8%），辅句从槽里升起；
// 换页是色带擦除，新页面露出来的那一帧＝at（擦除在 at 之前 0.3s 开始）。
// cues：title | point（一页：text 主词（太长自动缩字号、最多两行），sub 辅句，data.label 左上小标签如「01」）
//       number（一页大数字：data {value, prefix, suffix, decimals}，at = 数字落定的时刻，页面提前 0.7s 出现开始滚）
//       highlight（当前页里 data.word 这个词刷荧光笔＋放大一下；不给 word 就刷整个主词）
//       sub 里的关键词：data.key 写辅句里的一个词，那个词用强调色（示范片「下一行」那样）
//       number 的说明写 text 或 sub 都行（clip.js 已统一）；data.label 照样有
// 两个以上的 point 页 = 一份清单：画进度轨（01/02/03，告诉观众第几个、一共几个；横屏右上、竖屏在下方）和背景巨型序号（低对比、斜向慢漂，data.num 可指定）。
// data.palette = [[底色, 字色], ...] 换配色。alpha 模式不画底，字白色带深色描边。safe：主词块在 top..H−bottom 之间居中，进度轨也在这一带里。
CLIPS.y5_kinetic_type = (() => {
const { clamp, lerp } = U;
const PAL = [['#FFD23F', '#111111'], ['#FF5A36', '#FFFFFF'], ['#14213D', '#FFD23F'], ['#F4EFE6', '#111111']];
let pages, LIST;
const accentOf = col => col[0] === '#FFD23F' || col[0] === '#F4EFE6' || col[1] === '#FFD23F' ? '#FF5A36' : '#FFD23F';   // 字本身是黄的（深蓝页）就用橙，不然着色看不出来
return {
  fonts: ['PuHui-Black', 'PuHui-Heavy', 'PuHui-Bold'],
  safe: true,
  init(ctx) {
    const { W, u } = ctx, pal = ctx.data.palette || PAL, g = document.createElement('canvas').getContext('2d');
    pages = ctx.of('title', 'point', 'number').map((q, i) => {
      const num = q.kind === 'number', nd = q.data || {};
      const text = num ? (nd.prefix || '') + TY.fmt(nd.value || 0, nd.decimals ?? 0) + (nd.suffix || '') : (q.text || '');
      const S0 = (ctx.portrait ? 250 : 300) * u;
      let f = TY.fit(g, text, W * 0.86, S0, 'PuHui-Black', { maxLines: 1, min: 0.55, soft: true });   // 先缩字号放一行，再不行折两行；单个长英文词再缩也要放得下
      if (!f.ok) f = num ? TY.fit(g, text, W * 0.86, S0, 'PuHui-Black', { maxLines: 1 })   // 数字不折行（滚动时逐字变宽，折开会跳）：只缩字号
                     : TY.fit(g, text, W * 0.86, S0, 'PuHui-Black', { maxLines: 2, min: 0.3, balance: true });
      const sf = q.sub ? TY.fit(g, q.sub, W * 0.86, (ctx.portrait ? 64 : 80) * u, 'PuHui-Heavy', { maxLines: 2, min: 0.6, balance: true }) : null;   // 辅句：放不下先缩、再折两行
      const show = num ? q.at - 0.7 : q.at;
      return { q, i, num, nd, text, f, sf, show, col: pal[i % pal.length] };
    });
    const pts = pages.filter(p => p.q.kind === 'point'); pts.forEach((p, k) => { p.ord = k + 1; });
    LIST = pts.length >= 2 ? pts : null;
    // 高亮的词不在页上：只告警（以前什么都不发生）
    for (const h of ctx.of('highlight')) { const w = h.data && h.data.word; if (!w) continue; const pg = [...pages].reverse().find(p => p.show <= h.at + 1e-6);
      if (pg && !pg.f.lines.some(l => l.includes(w))) console.warn(`y5 highlight at=${h.at}：主词里没有「${w}」（主词「${pg.text}」；荧光笔只刷主词，辅句里的词用 data.key 着色），这一下什么都不做`); }
  },
  draw(c, t, ctx) {
    const { W, H, u } = ctx;
    let k = -1; pages.forEach((p, i) => { if (t >= p.show - 1e-6) k = i; });
    const P = pages[Math.max(0, k)]; if (!P) return;
    const page = (p, c2) => drawPage(c2, p, t, ctx);
    // 底＋点阵
    if (!ctx.alpha) { c.fillStyle = P.col[0]; c.fillRect(0, 0, W, H); dots(c, t, P.col[1], 0.07, u, W, H); }
    if (k >= 0) {
      // 拍点微冲：每个 cue 落下时整屏放大 1.5%，0.15s 衰减
      let bump = 1; for (const q of ctx.cues) { const l = t - q.at; if (l >= 0 && l < 0.4) bump += 0.015 * Math.exp(-l * 22); }
      const [dx, dy] = CAM.drift(t, 5 * u, 2);
      CAM.with(c, { x: W / 2 + dx, y: H / 2 + dy, z: bump, r: 0.008 * Math.sin(t * 0.8) }, cc => { ghost(cc, P, t, ctx); page(P, cc); });
      track(c, P, t, ctx);
    }
    // 换页：两条色带从左扫到右，最后一条后面露出新页（露出那一帧＝新页的 show 时刻）
    const N = pages[k + 1];
    if (N && !ctx.alpha) {
      const p = (t - (N.show - 0.3)) / 0.36; if (p > 0 && p < 1) {
        const mid = ['#FFD23F', '#FF5A36', '#14213D', '#3A86FF'].find(x => x !== P.col[0] && x !== N.col[0]), cols = [mid, N.col[0]], sk = H * 0.35;   // 两条：一条和前后底色都不同的强调色＋新底色（以前第一条是字色，白字页前会整屏闪一帧白）
        const edge = q => lerp(-sk - 40, W + sk + 40, MO.expoInOut(clamp(q)));
        cols.forEach((col, i) => { const x = edge((p - i * 0.12) / 0.76); c.save(); c.beginPath(); c.moveTo(-sk - 60, 0); c.lineTo(x + sk, 0); c.lineTo(x, H); c.lineTo(-sk - 60, H); c.closePath(); c.clip(); c.fillStyle = col; c.fillRect(0, 0, W, H); c.restore(); });
      }
    }
  },
};
function dots(c, t, col, a, u, W, H) { const g = 64 * u, ox = (t * 36 * u) % g, oy = (t * 18 * u) % g; c.save(); c.globalAlpha = a; c.fillStyle = col; for (let y = -g; y < H + g; y += g) for (let x = -g; x < W + g; x += g) { c.beginPath(); c.arc(x + ox, y + oy, 4 * u, 0, Math.PI * 2); c.fill(); } c.restore(); }
function drawPage(c, P, t, ctx) {
  const { W, H, u } = ctx, q = P.q, fg = ctx.alpha ? '#FFFFFF' : P.col[1], lt = ctx.lt(t, P.num ? P.show : q.at);
  const stroke = ctx.alpha ? { stroke: '#111', strokeW: P.f.size * 0.06 } : {};
  const lines = P.num ? [P.text] : P.f.lines, size = P.f.size, lh = size * 1.08;
  const sub = q.sub, subSz = P.sf ? P.sf.size : 0, subL = P.sf ? P.sf.lines : [];
  const by0 = ctx.safe.top, by1 = H - ctx.safe.bottom - (ctx.portrait && LIST ? 220 * u : 0), cyB = ctx.portrait ? by0 + (by1 - by0) * 0.44 : (by0 + by1) / 2;
  const blockH = lines.length * lh + (sub ? subSz * (0.6 + subL.length * 1.15) : 0), y0 = cyB - blockH / 2 + size * 0.82;
  // 标签（左上）
  const label = q.data && q.data.label;
  if (label) TY.rise(c, label, (ctx.portrait ? 80 : 140) * u, ctx.safe.top + (ctx.portrait ? 260 : 210) * u, MO.at(lt, 0, 0.3), { size: (ctx.portrait ? 56 : 52) * u, fam: 'PuHui-Bold', color: fg, track: 6 * u, ...(ctx.alpha ? { stroke: '#111', strokeW: 6 * u } : {}) });
  // 高亮（画在字后面）
  const hq = ctx.of('highlight').filter(h => h.at >= (P.num ? P.show : q.at) - 1e-6).find(h => { const nx = ctx.of('title', 'point', 'number').find(z => z.at > q.at); return !nx || h.at < nx.at; });
  let hiScale = 1, knock = null;
  if (hq) {
    const hl = ctx.lt(t, hq.at), wd = hq.data && hq.data.word, found = !wd || lines.some(l => l.includes(wd));
    hiScale = hl > 0 && found ? 1 + 0.06 * MO.settle(hl, 1, 2.5, 6) : 1;                     // 词不在主词里：不刷也不放大（init 里已告警）
    if (hl > 0) lines.forEach((ln, i) => {
      const word = wd || ln, at = ln.indexOf(word); if (at < 0) return;
      const wPre = TY.width(c, ln.slice(0, at), size), wW = TY.width(c, word, size), wl = TY.width(c, ln, size), x = W / 2 - wl / 2 + wPre, y = y0 + i * lh;
      const mc = ctx.alpha ? '#FF5A36' : (P.col[0] === '#FFD23F' ? '#FF5A36' : '#FFD23F'), mr = [x - 12 * u, y - size * 0.86, wW + 24 * u, size * 1.0, MO.at(hl, 0, 0.3)];
      TY.marker(c, ...mr, mc);
      if (mc.toLowerCase() === fg.toLowerCase()) (knock = knock || []).push({ i, mr });   // 荧光笔和字同色（深蓝页黄字）：笔下面的字反白成底色，不然整个词被涂没
    });
  }
  // 主词：每行在 at（+0.08s/行）砸进来；数字页从 0 滚到落定
  lines.forEach((ln, i) => {
    let s = ln; if (P.num) { const k = MO.expoOut(clamp(lt / 0.7)); s = (P.nd.prefix || '') + TY.fmt((P.nd.value || 0) * k, P.nd.decimals ?? 0) + (P.nd.suffix || ''); }
    const w = TY.width(c, s, size), x = W / 2 - w / 2, y = y0 + i * lh;
    TY.slam(c, s, x, y, lt - i * 0.08, { size, color: fg, scale: hiScale, ox: W / 2, oy: y - size * 0.36, ...stroke });
    for (const k of (knock || []).filter(k => k.i === i)) {             // 只在荧光笔那块平行四边形里，用底色再砸一遍同一行（和 TY.marker 同一形状）
      const [mx, my, mw, mh, mp] = k.mr, ww = mw * MO.expoOut(clamp(mp)), sk = mh * 0.18;
      c.save(); c.beginPath(); c.moveTo(mx + sk, my); c.lineTo(mx + ww + sk, my); c.lineTo(mx + ww, my + mh); c.lineTo(mx, my + mh); c.closePath(); c.clip();
      TY.slam(c, s, x, y, lt - i * 0.08, { size, color: P.col[0], scale: hiScale, ox: W / 2, oy: y - size * 0.36 }); c.restore();
    }
  });
  if (sub) subL.forEach((ln, k) => {                                      // 辅句（可两行）；data.key 那个词用强调色
    const sw = TY.width(c, ln, subSz, 'PuHui-Heavy'), sy = y0 + (lines.length - 1) * lh + subSz * (1.7 + k * 1.15), sp = MO.at(lt, 0.35 + k * 0.08, 0.35), so = { size: subSz, fam: 'PuHui-Heavy', color: fg, ...(ctx.alpha ? { stroke: '#111', strokeW: subSz * 0.08 } : {}) };
    const key = q.data && q.data.key, ki = key ? ln.indexOf(key) : -1, x0 = W / 2 - sw / 2;
    if (ki < 0) return TY.rise(c, ln, x0, sy, sp, so);
    const pre = ln.slice(0, ki), post = ln.slice(ki + key.length), wp = TY.width(c, pre, subSz, 'PuHui-Heavy'), wk = TY.width(c, key, subSz, 'PuHui-Heavy');
    if (pre) TY.rise(c, pre, x0, sy, sp, so); TY.rise(c, key, x0 + wp, sy, sp, { ...so, color: ctx.alpha ? '#FFD23F' : accentOf(P.col) }); if (post) TY.rise(c, post, x0 + wp + wk, sy, sp, so);
  });
}
// 背景巨型序号：清单页右下角（竖屏落在下半屏），低对比、弹簧落位后斜向慢漂；不和主词抢（字色 12% 透明度）
function ghost(c, P, t, ctx) {
  if (!LIST || !P.ord || ctx.alpha) return;
  const { W, H, u } = ctx, lt = t - P.show, k = MO.springHz(lt, 2.0, 8), sc = lerp(1.35, 1, k), s = String((P.q.data && P.q.data.num) ?? P.ord);
  const size = (ctx.portrait ? 1000 : 900) * u, x = W - (ctx.portrait ? 40 : 60) * u + lt * 14 * u, y = H - ctx.safe.bottom + (ctx.portrait ? -120 : 70) * u - lt * 10 * u;
  TY.text(c, s, x, y, { size, fam: 'PuHui-Black', align: 'right', color: P.col[1], alpha: 0.12 * clamp(lt / 0.08), scale: sc, ox: x - size * 0.28, oy: y - size * 0.36 });
}
// 进度轨：01/02/03 三格（几个 point 页就几格），当前页那一格在换页露出那一帧点亮；画在相机外，不跟页面晃
function track(c, P, t, ctx) {
  if (!LIST) return;
  const { W, H, u } = ctx, n = LIST.length, k = LIST.filter(p => p.show <= t + 1e-6).length; if (k === 0) return;
  const fg = ctx.alpha ? '#FFFFFF' : P.col[1], gap = 18 * u, seg = Math.min((ctx.portrait ? 150 : 110) * u, ((ctx.portrait ? W * 0.86 : W * 0.4) - (n - 1) * gap) / n), tot = n * seg + (n - 1) * gap;   // 要点多时每格变窄，不出画
  const x0 = ctx.portrait ? (W - tot) / 2 : W - (ctx.portrait ? 80 : 140) * u - tot, y = ctx.portrait ? H - ctx.safe.bottom - 130 * u : ctx.safe.top + 120 * u;
  const a = MO.expoOut(clamp((t - LIST[0].show) / 0.3));
  c.save(); c.globalAlpha = a;
  for (let i = 0; i < n; i++) { const x = x0 + i * (seg + gap), on = i < k, fill = on ? MO.expoOut(clamp((t - LIST[i].show) / 0.3)) : 0;
    c.fillStyle = fg; c.globalAlpha = a * 0.25; c.fillRect(x, y, seg, 10 * u); c.globalAlpha = a; c.fillRect(x, y, seg * fill, 10 * u);
    TY.text(c, String(i + 1).padStart(2, '0'), x, y - 20 * u, { size: (ctx.portrait ? 40 : 32) * u, fam: 'PuHui-Bold', color: fg, align: 'left', alpha: on ? 1 : 0.4, track: 2 * u, ...(ctx.alpha ? { stroke: '#111', strokeW: 5 * u } : {}) }); }
  c.restore();
}
})();


// ===== huashu clip t1_3b1b.js =====
// 片段 · 3b1b / manim 式讲解（t1）：纯黑底，一次只放正在讲的那一个东西；字和公式都是 Write（先描边后填色），缓动一律 smooth。
// 招牌是 Transform：上一个概念直接变成下一个，不切、不淡出重来。
// cues：title（at 这一帧开始 Write；再来一个 title 时旧标题 FadeOut 上移；太长先缩字号、再折两行）
//       equation（data.tokens = DG.math 的 token 数组，或只给 text；颜色＝概念名：col 写 'BLUE_C' 等 manim 色名或 #hex）
//                 第二个起的 equation 不重新 Write，而是从上一个 Transform 过来（TransformMatchingTex：同名符号移到新位置，对不上的淡出/淡入；默认 1.5s）
//       point（要点一句一行，Write；新要点出来时旧的变灰）
//       line（坐标系先 Create，再画曲线：data.values = [y...]（等距）或 data.points = [[x,y]...]；text = 曲线标签；data.color）
//            第二个起的 line 是 Transform：上一条曲线逐点（按弧长对齐重采样）形变成这一条，颜色和标签一起换，坐标系不动
//       highlight（Indicate＋黄色框住并留住：data.index = 第几个要点（只数 point，从 0 起；默认最新）；data.target = 'equation' 时框公式）
// 布局：有 line 时横屏左文右图、竖屏上图下文；要点块在剩下的高度里竖直居中。safe：内容只排在 top..H−bottom 之间。
// 公式太宽就整体缩字号（所有公式同一字号，Transform 时不跳），竖屏长公式不出画。
// 字形：CMU 斜体把 θ 画成 ϑ，θ 改用 EB Garamond 斜体（闭合的 θ）；连续两个以上的字母（softmax、ReLU）按正体单词排，单个字母才是斜体变量。
// alpha：透明底时字外面描一圈深色边，压在亮画面上也看得见。
CLIPS.t1_3b1b = (() => {
const { clamp, lerp } = U;
let M, L;
const col = v => !v ? null : M[v] || v;
const isZh = ch => ch.codePointAt(0) >= 0x2E80;
// 一段字 → token：汉字/全角走普惠体；连续 ≥2 个字母是单词（正体）；单个字母斜体；其余（数字、符号、希腊字母）正体
const toks = text => (String(text).match(/[A-Za-z]{2,}|./gu) || []).flatMap(s => s.length > 1 && !/[a-z]/.test(s) ? [...s] : [s])   // 全大写的一串（QK）是几个变量，拆开
  .map(s => ({ s, zh: isZh(s), it: /^[a-zA-Z]$/.test(s), gap: 0 }));
// θ 用 EB Garamond 斜体画（CMU 的 θ 是 ϑ 字形）。只换这一个 token 的字体：逐 token 调 DG.math，期间临时改 DG.FONT
const THETA = /[θΘ]/;
const withFont = (tk, fn) => { if (!THETA.test(tk.s)) return fn(tk); const o = { ...DG.FONT }; DG.FONT.it = DG.FONT.rm = 'EBGaramond-400i';
  try { return fn({ ...tk, zh: false, it: true }); } finally { Object.assign(DG.FONT, o); } };
const layoutT = (c, tokens, size) => { let x = 0; return tokens.map(tk => { const w = withFont(tk, t2 => DG.layout(c, [{ ...t2, gap: 0 }], size)[0].w), sz = (tk.size || 1) * size;
  const r = { x, w }; x += w + (tk.gap != null ? tk.gap : sz * 0.06); return r; }); };
const widthT = (c, tokens, size) => { const l = layoutT(c, tokens, size); return l.length ? l[l.length - 1].x + l[l.length - 1].w : 0; };
// Write 一串 token（等价于 DG.math 整串调用：token 间按 manim LaggedStart 错开），返回每个 token 的屏幕 {x, w}
const writeT = (c, tokens, x, y, size, p, { align = 'center', col: cl = '#fff' } = {}) => {
  const Lt = layoutT(c, tokens, size), tot = Lt.length ? Lt[Lt.length - 1].x + Lt[Lt.length - 1].w : 0, x0 = align === 'center' ? x - tot / 2 : align === 'right' ? x - tot : x;
  const n = tokens.length, r = Math.min(4 / (n + 1), 0.2);
  tokens.forEach((tk, i) => { const q = MO.lagged(i, n, clamp(p), r); if (q > 0) withFont(tk, t2 => DG.math(c, [t2], x0 + Lt[i].x, y, size, { p: q, align: 'left', col: cl })); });
  return Lt.map(l => ({ x: x0 + l.x, w: l.w }));
};
// 按弧长等分重采样成 n 个点（Transform 前两条曲线要点数相同、起点对齐）
const resample = (pts, n) => { const cum = DG.cum(pts), T = cum[cum.length - 1]; return Array.from({ length: n }, (_, i) => DG.pointAt(pts, cum, T * i / (n - 1))); };
// 折行但各行等长（不让一个字母/单词孤零零挂到下一行）
const balance = (g, text, maxW, font) => { const n = TY.wrap(g, text, maxW, font).length; if (n < 2) return [text];
  let lo = maxW * 0.4, hi = maxW; for (let k = 0; k < 14; k++) { const m = (lo + hi) / 2; if (TY.wrap(g, text, m, font).length > n) lo = m; else hi = m; } return TY.wrap(g, text, hi, font); };
// 标题：先缩字号放一行（最小 0.75），放不下就等长折行（竖屏最多 3 行、横屏 2 行），再缩到每行放得下
const titleLines = (c, text, maxW, size, maxLines) => {
  let s = size; while (s > size * 0.75 && widthT(c, toks(text), s) > maxW) s *= 0.95;
  if (widthT(c, toks(text), s) <= maxW) return { size: s, lines: [text] };
  s = size * 0.9; let lines = balance(c, text, maxW, `${s}px "PuHui-Medium"`);
  while (lines.length > maxLines) { s *= 0.94; lines = balance(c, text, maxW, `${s}px "PuHui-Medium"`); }
  while (s > size * 0.45 && Math.max(...lines.map(l => widthT(c, toks(l), s))) > maxW) s *= 0.95;
  return { size: s, lines };
};
let BUF;
return {
  fonts: ['PuHui-Medium'],
  safe: true,
  init(ctx) {
    M = DG.MANIM; DG.FONT.zh = 'PuHui-Medium';                         // 任意中文：普惠体（思源宋子集只有示范片用到的字）
    const { W, H, u, safe } = ctx, g = document.createElement('canvas').getContext('2d');
    const pad = (ctx.portrait ? 70 : 140) * u, yTop = safe.top, yBot = H - safe.bottom - (ctx.portrait ? 90 : 70) * u;
    const hasLine = ctx.of('line').length > 0, hasEq = ctx.of('equation').length > 0;
    // 标题
    const titles = ctx.of('title').map(q => ({ q, ...titleLines(g, q.text || '', W - 2 * pad, (ctx.portrait ? 72 : 62) * u, ctx.portrait ? 3 : 2) }));
    const tH = titles.length ? Math.max(...titles.map(z => z.lines.length * z.size * 1.25)) : 0;
    const titleY = yTop + (ctx.portrait ? 170 : 130) * u;
    let y = titles.length ? titleY + tH : yTop + 90 * u;
    let eqSz = (ctx.portrait ? 72 : 66) * u;
    for (const q of ctx.of('equation')) { const tk = q.data && q.data.tokens ? q.data.tokens : toks(q.text || ''), w = widthT(g, tk, eqSz) + 60 * u; if (w > W - 2 * pad) eqSz *= (W - 2 * pad) / w; }
    const eqY = y + eqSz * (titles.length ? 1.15 : 0.9);
    if (hasEq) y = eqY + eqSz * 0.6;
    y += 30 * u;
    // 要点：预先折行（每条可能多行）
    const size0 = (hasLine ? (ctx.portrait ? 56 : 50) : (ctx.portrait ? 62 : 58)) * u;
    let text, plot = null;
    if (hasLine && ctx.portrait) { plot = { x: pad + 20 * u, y: y + 20 * u, w: W - 2 * pad - 20 * u, h: (yBot - y) * 0.5 }; text = { x: pad, y0: plot.y + plot.h + 70 * u, y1: yBot, w: W - 2 * pad }; }
    else if (hasLine) { plot = { x: W * 0.52, y: y + 20 * u, w: W * 0.48 - pad, h: yBot - y - 40 * u }; text = { x: pad, y0: y, y1: yBot, w: W * 0.47 - pad }; }
    else text = { x: pad, y0: y, y1: yBot, w: W - 2 * pad };
    // 要点放不下（竖屏有曲线、要点多）：先让曲线让位（竖屏 50% → 最少 30%），再缩字号（最小 0.6 倍），不压进 safe.bottom
    let points, blockH, size = size0; const gapP = 36 * u, room = yBot - y;
    for (let k = 1; ; k *= 0.94) {
      size = size0 * k;
      points = ctx.of('point').map(q => { const lines = balance(g, q.text || '', text.w - 50 * u, `${size}px "PuHui-Medium"`); return { q, lines, h: lines.length * size * 1.35 }; });
      blockH = points.reduce((s, p) => s + p.h + gapP, 0) - gapP;
      if (plot && ctx.portrait) { plot.h = Math.max(room * 0.3, Math.min(room * 0.5, room - 110 * u - blockH)); text.y0 = plot.y + plot.h + 70 * u; }
      if (blockH <= text.y1 - text.y0 || k < 0.6) break;
    }
    if (blockH > text.y1 - text.y0 + 1) console.warn(`t1：${points.length} 条要点缩到 0.6 倍字号仍放不下（超出 ${Math.round(blockH - (text.y1 - text.y0))}px）：新要点出来时整块上卷、最早的淡出；想全留在屏上就删几条或拆成两段`);
    let py = text.y0 + Math.max(0, (text.y1 - text.y0 - blockH) / 2) * (hasLine && !ctx.portrait ? 1 : 0.6);   // 要点块竖直居中（竖屏略偏上）
    for (const p of points) { p.y = py + size; py += p.h + gapP; p.need = Math.max(0, py - gapP - text.y1); }   // need：这一条出来时整块要上卷多少才不越过 y1
    // 曲线：坐标统一用所有 line 的数据范围（Transform 时坐标系不动）
    const lines = ctx.of('line').map(q => { const d = q.data || {}; return { q, d, pts0: d.points || (d.values || []).map((v, i) => [i, v]) }; }).filter(l => l.pts0.length >= 2);
    if (plot && lines.length) {
      const all = lines.flatMap(l => l.pts0), xs = all.map(p => p[0]), ys = all.map(p => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys); let y1 = Math.max(...ys); if (y1 === y0) y1 = y0 + 1;
      plot.X = v => plot.x + (v - x0) / ((x1 - x0) || 1) * plot.w; plot.Y = v => plot.y + plot.h - (v - y0) / (y1 - y0) * plot.h * 0.9;
      for (const l of lines) { const [lx0, lx1] = [Math.min(...l.pts0.map(p => p[0])), Math.max(...l.pts0.map(p => p[0]))];
        // 每条曲线横向铺满坐标系（不同 line 的 x 量纲可以不同：损失-参数 → 损失-步数）
        l.scr = l.pts0.map(([px, py]) => [plot.x + (px - lx0) / ((lx1 - lx0) || 1) * plot.w, plot.Y(py)]); l.rs = resample(l.scr, 160); }
    }
    L = { titleY, titles, eqY, eqSz, text, plot, points, size, lines };
  },
  draw(c0, t, ctx) {
    const { W, H, u } = ctx;
    const c = ctx.alpha ? (BUF = BUF || document.createElement('canvas'), BUF.width = W, BUF.height = H, BUF.getContext('2d')) : c0;
    if (!ctx.alpha) { c.fillStyle = '#000'; c.fillRect(0, 0, W, H); }
    // ---- 标题（同一时间只有一个，换标题 = 旧的上移淡出、新的 Write） ----
    L.titles.forEach((z, i) => {
      const q = z.q, nx = L.titles[i + 1], out = nx ? MO.smooth(MO.seg(t, nx.q.at - 0.45, nx.q.at)) : 0; if (out >= 1 || ctx.lt(t, q.at) <= 0) return;
      const n = [...(q.text || '')].length, dur = q.dur || clamp(n / 14, 0.6, 1.6);
      c.save(); c.globalAlpha = 1 - out; c.translate(0, -50 * u * out);
      z.lines.forEach((ln, k) => writeT(c, toks(ln), W / 2, L.titleY + k * z.size * 1.25, z.size, ctx.p(t, q.at + k * dur / z.lines.length, dur / z.lines.length), { col: col(q.data && q.data.color) || '#fff' }));
      c.restore();
    });
    // ---- 公式：第一个 Write，之后每个从上一个 TransformMatchingTex 过来 ----
    let eqBox = null;
    const eqs = ctx.of('equation').map(q => (q.data && q.data.tokens ? q.data.tokens : toks(q.text || '')).map(k => ({ ...k, col: col(k.col) })));
    const eqCues = ctx.of('equation'), sz = L.eqSz;
    let k = -1; eqCues.forEach((q, i) => { if (ctx.lt(t, q.at) > 0) k = i; });
    if (k >= 0) {
      const q = eqCues[k], tk = eqs[k], Ln = layoutT(c, tk, sz), tot = Ln.length ? Ln[Ln.length - 1].x + Ln[Ln.length - 1].w : 0, x0 = W / 2 - tot / 2;
      if (k === 0) writeT(c, tk, W / 2, L.eqY, sz, ctx.p(t, q.at, q.dur || 1.2));
      else {
        const p = MO.smooth(ctx.p(t, q.at, q.dur || 1.5)), old = eqs[k - 1], Lo = layoutT(c, old, sz), toto = Lo.length ? Lo[Lo.length - 1].x + Lo[Lo.length - 1].w : 0, xo = W / 2 - toto / 2;
        const used = new Set(), from = tk.map(nt => { const j = old.findIndex((ot, jj) => !used.has(jj) && ot.s === nt.s && (ot.sub || '') === (nt.sub || '') && (ot.sup || '') === (nt.sup || '')); if (j >= 0) used.add(j); return j; });
        const one = (tok, x, a, cl) => { if (a <= 0) return; c.save(); c.globalAlpha *= a; withFont(tok, t2 => DG.math(c, [{ ...t2, col: cl }], x, L.eqY, sz, { p: 1, align: 'left' })); c.restore(); };
        old.forEach((ot, j) => { if (!used.has(j)) { c.save(); c.translate(0, -30 * u * p); one(ot, xo + Lo[j].x, 1 - clamp(p * 1.6), ot.col || '#fff'); c.restore(); } });   // 对不上的旧符号：淡出上移
        tk.forEach((nt, i) => { const j = from[i];
          if (j >= 0) one(nt, lerp(xo + Lo[j].x, x0 + Ln[i].x, p), 1, DG.mix(old[j].col || '#ffffff', nt.col || '#ffffff', p));    // 同名符号：移到新位置，颜色插值
          else { c.save(); c.translate(0, 30 * u * (1 - p)); one(nt, x0 + Ln[i].x, clamp((p - 0.35) / 0.65), nt.col || '#fff'); c.restore(); } });   // 新符号：淡入
      }
      if (Ln.length) eqBox = { x: x0 - 20 * u, y: L.eqY - sz * 0.95, w: tot + 40 * u, h: sz * 1.35 };
    }
    // ---- 坐标系＋曲线：第一条 Create，之后每条从上一条逐点形变过来 ----
    const P = L.plot;
    if (P && L.lines.length) {
      const l0 = L.lines[0];
      if (ctx.lt(t, l0.q.at) > 0) {
        const ap = MO.smooth(ctx.p(t, l0.q.at, 0.6)), lw = 3 * u;
        c.save(); c.strokeStyle = M.GREY_B; c.lineWidth = lw; c.lineCap = 'round';
        c.beginPath(); c.moveTo(P.x, P.Y(0)); c.lineTo(P.x + P.w * ap, P.Y(0)); c.moveTo(P.x, P.y + P.h); c.lineTo(P.x, P.y + P.h - P.h * ap); c.stroke();
        c.globalAlpha = ap; for (let kk = 1; kk <= 4; kk++) { const xx = P.x + P.w * kk / 4; c.beginPath(); c.moveTo(xx, P.Y(0) - 8 * u); c.lineTo(xx, P.Y(0) + 8 * u); c.stroke(); }
        c.restore();
        let j = 0; L.lines.forEach((l, i) => { if (ctx.lt(t, l.q.at) > 0) j = i; });
        const l = L.lines[j], lc = col(l.d.color) || M.BLUE_C;
        c.save(); c.lineWidth = 5 * u; c.lineJoin = 'round'; c.lineCap = 'round';
        if (j === 0) {
          const cp = MO.smooth(ctx.p(t, l.q.at + 0.5, l.q.dur || 1.4));
          if (cp > 0) { const cum = DG.cum(l.scr); c.strokeStyle = lc; const tip = DG.drawPartial(c, l.scr, cum, cum[cum.length - 1] * cp);
            if (cp < 1) DG.neuron(c, tip[0], tip[1], 10 * u, 1, { fill: lc, stroke: '#fff', lw: 2 * u }); }
          label(c, l, MO.smooth(MO.seg(cp, 0.8, 1)), lc, u);
        } else {
          const pv = L.lines[j - 1], p = MO.smooth(ctx.p(t, l.q.at, l.q.dur || 1.5)), pc = col(pv.d.color) || M.BLUE_C;
          const pts = DG.morph(pv.rs, l.rs, p); c.strokeStyle = DG.mix(pc, lc, p); c.beginPath(); pts.forEach((q, i) => i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1])); c.stroke();
          label(c, pv, 1 - clamp(p * 2), pc, u); label(c, l, clamp(p * 2 - 1), lc, u);
        }
        c.restore();
      }
    }
    // ---- 要点 ----
    const hl = ctx.of('highlight'), size = L.size;
    const target = h => h.data && h.data.target === 'equation' ? -2 : h.data && h.data.index != null ? h.data.index : L.points.filter(p => p.q.at <= h.at).length - 1;
    const roll = Math.max(0, ...L.points.map(p => p.need * MO.smooth(ctx.p(t, p.q.at - 0.3, 0.4))));   // 放不下时：新要点出来前 0.3s 整块上卷，越过顶的淡出
    L.points.forEach((it0, i) => {
      const q = it0.q; if (ctx.lt(t, q.at) <= 0) return;
      const it = roll ? { ...it0, y: it0.y - roll } : it0, over = L.text.y0 - (it.y - size);
      if (over > 0 && clamp(1 - over / (size * 1.2)) <= 0) return;
      c.save(); if (over > 0) c.globalAlpha *= clamp(1 - over / (size * 1.2));
      const nx = L.points[i + 1], h = [...hl].reverse().find(z => target(z) === i && t >= z.at - 1e-6) || null, hOther = hl.some(z => t >= z.at && target(z) !== i && z.at > (h ? h.at : -1));
      const dimN = nx ? MO.smooth(ctx.p(t, nx.q.at, 0.4)) : 0;
      const n = [...(q.text || '')].length, dur = q.dur || clamp(n / 16, 0.6, 1.8);
      // Indicate：放大 1.2、染黄、回来；之后这一条保持黄色（被强调的留住），其余变灰
      const ip = h && !hOther ? ctx.p(t, h.at, 0.8) : 0, ind = DG.indicate(ip), keep = h && !hOther ? MO.smooth(clamp(ip * 2)) : 0;
      const dim = keep > 0 ? 0 : dimN;
      const base = DG.mixA(DG.mixA('#ffffff', M.GREY_C, dim), M.YELLOW, Math.max(keep, ind.k)), colr = DG.rgba(base);
      const bx = L.text.x + 14 * u, by = it.y - size * 0.36;
      c.save(); c.translate(bx, by); c.scale(ind.s, ind.s); c.translate(-bx, -by);
      DG.neuron(c, bx, by, 12 * u, 1 - dim * 0.6, { lw: 2 * u, stroke: colr, fill: col(q.data && q.data.color) || M.BLUE_C, sa: MO.smooth(ctx.p(t, q.at, 0.3)) });
      let wMax = 0;
      it.lines.forEach((ln, kk) => { const r = writeT(c, toks(ln), L.text.x + 50 * u, it.y + kk * size * 1.35, size, ctx.p(t, q.at + kk * dur / it.lines.length, dur / it.lines.length), { align: 'left', col: colr });
        if (r.length) wMax = Math.max(wMax, r[r.length - 1].x + r[r.length - 1].w - (L.text.x + 50 * u)); });
      c.restore();
      if (h && !hOther) box(c, L.text.x - 16 * u, it.y - size * 1.05, wMax + 90 * u, it.lines.length * size * 1.35 + 18 * u, ctx.lt(t, h.at), u);
      c.restore();
    });
    const he = [...hl].reverse().find(z => target(z) === -2 && t >= z.at);
    if (he && eqBox && !hl.some(z => t >= z.at && z.at > he.at)) box(c, eqBox.x, eqBox.y, eqBox.w, eqBox.h, ctx.lt(t, he.at), u);
    if (ctx.alpha) { c0.save(); c0.shadowColor = 'rgba(0,0,0,0.9)'; c0.shadowBlur = 10 * u; c0.drawImage(BUF, 0, 0); c0.shadowBlur = 3 * u; c0.drawImage(BUF, 0, 0); c0.restore(); }
  },
};
// 曲线标签：放在曲线末端上方
function label(c, l, a, colr, u) { if (!l.q.text || a <= 0) return; const e = l.scr[l.scr.length - 1];
  c.save(); c.globalAlpha *= a; c.font = `${40 * u}px "PuHui-Medium"`; c.fillStyle = colr; c.textAlign = 'right'; c.fillText(l.q.text, e[0], e[1] - 30 * u); c.restore(); }
// 强调框：Circumscribe 先绕一圈（1s），同时 SurroundingRectangle 画出来并留住（末帧有焦点）
function box(c, x, y, w, h, lt, u) {
  if (lt <= 0) return; DG.circumscribe(c, x - 8 * u, y - 8 * u, w + 16 * u, h + 16 * u, MO.seg(lt, 0, 1.0), { lw: 4 * u });
  const p = MO.smooth(MO.seg(lt, 0.3, 1.0)); if (p <= 0) return;
  const pts = DG.rectPts(x + w / 2, y + h / 2, w, h, 120); c.save(); c.strokeStyle = DG.MANIM.YELLOW; c.lineWidth = 3 * u; c.lineJoin = 'round';
  DG.drawPartial(c, [...pts, pts[0]], DG.cum([...pts, pts[0]]), DG.len([...pts, pts[0]]) * p); c.restore();
}
})();


// ===== huashu clip t2_keynote_ui.js =====
// 片段 · 发布会式界面（t2），做教程、功能、参数最顺手：深色光斑底，一屏一件事；字从模糊里浮出，卡片是毛玻璃、轻回弹落位。
// data: { eyebrow?（标题上方小字）, title?, subtitle?, accent?:'#3d6bff' }
// cues：title（at 这一帧标题开始从模糊里浮出；text/sub 覆盖 data）
//       card | step | image（一张卡：text 卡标题，sub 说明，image 截图路径；at 这一帧卡片开始弹入；步骤号按出现顺序 01、02…，data.step 可指定）
//         · 有 image：一屏一张，截图等比放进卡里（contain，不裁）。横图横卡；竖图（手机截图）在横屏里是「左字右图」，图撑满高度
//         · 没有 image：功能卡。连着的几张（最多 3 张）并排站在一屏里，新卡进来时旧卡让位（像示范片的三卡并列）；
//           卡上有图标（data.icon：wave memory agent clock phone layers chart code check play bolt lock，不写就画步骤号）、
//           大标题、说明，可选一个大数字（data.value / prefix / suffix / decimals ＋ data.label，卡片落定后计数）
//       highlight（框出当前截图卡里的一块：data.rect = [x, y, w, h]，相对截图的 0..1；text 可选标签）
//       number（大数字单独一屏：data {value, prefix, suffix, decimals, label?（数字上方小字）}；at = 数字落定时刻，提前 0.9s 开始计数，
//               上一屏这时开始退场，不叠在卡片上；说明写 text 或 sub 都认）
// 竖屏：第一张卡进来时标题缩成页眉留在顶上（不让上下大片空着）；卡片按剩余高度放大。safe：版面只排在 top..H−bottom 之间。
// alpha：不画光斑底；卡片垫一层深色、字带投影，压在亮画面上也看得清。
CLIPS.t2_keynote_ui = (() => {
const { clamp, lerp } = U;
const SANS = '"Inter", "PuHui-Medium"', SANSB = '"Inter", "PuHui-Bold"', ZH = '"PuHui-Medium"', ZHB = '"PuHui-Bold"';
let BL, slides, scr = {}, G;
const track = (c, size, em = -0.02) => { c.letterSpacing = (size * em).toFixed(2) + 'px'; };
// 本语法自带的几个线性图标（UI.icon 只有 wave/memory/agent）：单位框 ±0.5，圆头等线宽
const ICON = {
  clock: c => { c.beginPath(); c.arc(0, 0, 0.42, 0, 7); c.stroke(); c.beginPath(); c.moveTo(0, -0.24); c.lineTo(0, 0); c.lineTo(0.18, 0.1); c.stroke(); },
  phone: c => { c.beginPath(); c.roundRect(-0.25, -0.45, 0.5, 0.9, 0.1); c.stroke(); c.beginPath(); c.moveTo(-0.08, 0.32); c.lineTo(0.08, 0.32); c.stroke(); },
  layers: c => { for (let k = 0; k < 3; k++) { const y = -0.22 + k * 0.22; c.globalAlpha = k ? 0.6 : 1; c.beginPath(); c.moveTo(-0.45, y); c.lineTo(0, y - 0.18); c.lineTo(0.45, y); c.lineTo(0, y + 0.18); c.closePath(); c.stroke(); } c.globalAlpha = 1; },
  chart: c => { c.beginPath(); c.moveTo(-0.42, -0.42); c.lineTo(-0.42, 0.4); c.lineTo(0.44, 0.4); c.stroke(); c.beginPath(); c.moveTo(-0.28, 0.18); c.lineTo(-0.06, -0.04); c.lineTo(0.1, 0.06); c.lineTo(0.36, -0.26); c.stroke(); },
  code: c => { c.beginPath(); c.moveTo(-0.18, -0.28); c.lineTo(-0.42, 0); c.lineTo(-0.18, 0.28); c.moveTo(0.18, -0.28); c.lineTo(0.42, 0); c.lineTo(0.18, 0.28); c.moveTo(0.08, -0.38); c.lineTo(-0.08, 0.38); c.stroke(); },
  check: c => { c.beginPath(); c.arc(0, 0, 0.42, 0, 7); c.stroke(); c.beginPath(); c.moveTo(-0.2, 0); c.lineTo(-0.05, 0.16); c.lineTo(0.22, -0.16); c.stroke(); },
  play: c => { c.beginPath(); c.moveTo(-0.22, -0.34); c.lineTo(0.34, 0); c.lineTo(-0.22, 0.34); c.closePath(); c.fill(); },
  bolt: c => { c.beginPath(); c.moveTo(0.08, -0.46); c.lineTo(-0.28, 0.06); c.lineTo(0, 0.06); c.lineTo(-0.08, 0.46); c.lineTo(0.28, -0.06); c.lineTo(0, -0.06); c.closePath(); c.fill(); },
  lock: c => { c.beginPath(); c.roundRect(-0.32, -0.04, 0.64, 0.46, 0.08); c.stroke(); c.beginPath(); c.arc(0, -0.04, 0.2, Math.PI, 0); c.stroke(); },
};
const icon = (g, kind, x, y, s, col, t) => {
  if (['wave', 'memory', 'agent'].includes(kind)) return UI.icon(g, kind, x, y, s, col, t);
  const f = ICON[kind]; if (!f) return false;
  g.save(); g.translate(x, y); g.scale(s, s); g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 0.075; g.lineCap = 'round'; g.lineJoin = 'round'; f(g); g.restore();
};
// 放得下才算：TY.fit 量每行宽度，多行折成等长（说明文字不留一个字挂在下一行）
const fitB = (g, text, maxW, size, fam, o = {}) => TY.fit(g, text, maxW, size, fam, { ...o, balance: true });
const fmtNum = (d, k) => (d.prefix || '') + TY.fmt((+d.value || 0) * k, d.decimals ?? 0) + (d.suffix || '');
return {
  fonts: ['PuHui-Medium', 'PuHui-Bold'],
  safe: true,
  init(ctx) {
    const { W, H, u, safe } = ctx;
    BL = [{ x: .22 * W, y: .28 * H, r: .4 * Math.max(W, H), col: [150, 90, 238], a: .75, ax: .11 * W, ay: .11 * H, period: 11, ph: 0 },
      { x: .79 * W, y: .24 * H, r: .37 * Math.max(W, H), col: [56, 104, 255], a: .7, ax: .1 * W, ay: .15 * H, period: 13, ph: 2.1 },
      { x: .69 * W, y: .86 * H, r: .3 * Math.max(W, H), col: [120, 214, 255], a: .5, ax: .13 * W, ay: .09 * H, period: 9.5, ph: 4 },
      { x: .27 * W, y: .89 * H, r: .27 * Math.max(W, H), col: [255, 96, 84], a: .42, ax: .09 * W, ay: .08 * H, period: 12, ph: 1.3 }];
    const hasTitle = ctx.of('title').length > 0, hdr = ctx.portrait && hasTitle;
    G = { y0: safe.top + (hdr ? 250 * u : 40 * u), y1: H - safe.bottom - 40 * u, hdrY: safe.top + 150 * u, hdr };
    G.cy = (G.y0 + G.y1) / 2; G.bh = G.y1 - G.y0;
    // 分屏：截图卡一张一屏；连着的无图卡最多 3 张一屏；number 单独一屏
    slides = []; let k = 0;
    for (const q of ctx.cues) {
      if (['card', 'step', 'image'].includes(q.kind)) {
        const n = q.data && q.data.step != null ? q.data.step : ++k, im = q.image && ctx.IMG[q.image], cd = { q, n, im };
        const last = slides[slides.length - 1];
        if (!im && last && last.type === 'cards' && last.cards.length < 3) last.cards.push(cd);
        else slides.push({ type: im ? 'img' : 'cards', cards: [cd], start: q.at });
      } else if (q.kind === 'number') slides.push({ type: 'num', q, start: q.at - 0.9 });
    }
    slides.forEach((s, i) => { s.next = slides[i + 1] ? slides[i + 1].start : 1e9; });
    // 截图卡的版式：标题行 ＋ 截图区；截图区按截图比例定。竖图在横屏里改成「左字右图」
    const maxW = W * (ctx.portrait ? 0.9 : 0.8), maxH = G.bh * (ctx.portrait ? 0.92 : 0.86), head = (ctx.portrait ? 230 : 130) * u, pad = 36 * u;
    for (const s of slides) if (s.type === 'img') {
      const cd = s.cards[0], ar = cd.im.width / cd.im.height;
      if (!ctx.portrait && ar < 0.9) {                                         // 横屏放竖图：左字右图，图撑满卡高
        const ih = maxH - 2 * pad, iw = ih * ar, tw = 560 * u;
        cd.box = { w: tw + iw + 3 * pad, h: maxH, img: { x: tw + 2 * pad, y: pad, w: iw, h: ih }, side: tw };
      } else {
        let iw = maxW - 2 * pad, ih = iw / ar; if (ih > maxH - head - 2 * pad) { ih = maxH - head - 2 * pad; iw = ih * ar; }
        const cw = Math.max(iw + 2 * pad, Math.min(maxW, 760 * u)), ch = ih + head + 2 * pad;
        cd.box = { w: cw, h: ch, img: { x: (cw - iw) / 2, y: head + pad, w: iw, h: ih } };
      }
    }
  },
  draw(c, t, ctx) {
    const { W, H, u, data: d } = ctx, acc = d.accent || '#3d6bff';
    // ---- 底：光斑（只依赖片段时间，连续） ----
    let bd = null;
    if (!ctx.alpha) {
      const bgc = UI.scratch('clip_t2bg', W, H), g = bgc.getContext('2d'); g.clearRect(0, 0, W, H);   // 先清空：不清的话上一帧的残留会透过半透明边缘，同一时刻先后渲出来差 1 个色阶（qa 确定性 ✗）
      UI.mesh(g, t, { base: '#050508', blobs: BL, blur: 90, scale: 0.25, grain: 0.035, key: 'clip_t2mesh' });
      const vg = g.createRadialGradient(W / 2, H / 2, 100 * u, W / 2, H / 2, Math.max(W, H) * 0.55); vg.addColorStop(0, 'rgba(0,0,0,0.38)'); vg.addColorStop(1, 'rgba(0,0,0,0.1)');
      g.fillStyle = vg; g.fillRect(0, 0, W, H); c.drawImage(bgc, 0, 0); bd = UI.backdrop(bgc, { blur: 40, key: 'clip_t2bd' });
    }
    const shadowText = g => { if (ctx.alpha) { g.shadowColor = 'rgba(0,0,0,0.75)'; g.shadowBlur = 18 * u; g.shadowOffsetY = 3 * u; } };
    // ---- 标题：眉题 → 主标题（模糊→清晰＋上移）→ 副标题；第一屏进来时：横屏退到后面，竖屏缩成页眉留在顶上 ----
    const qt = ctx.of('title')[0], first = slides[0] ? slides[0].start : 1e9;
    if (qt) {
      const out = MO.appleOut(MO.seg(t, first - 0.15, first + 0.5));
      const keep = G.hdr, title = qt.text ?? d.title ?? '', eb = (qt.data && qt.data.eyebrow) ?? d.eyebrow, sub = qt.sub ?? d.subtitle;
      if (out < 1 || keep) {
        c.save(); c.textAlign = 'center'; c.textBaseline = 'alphabetic'; shadowText(c);
        let sz = TY.fit(c, title, W * 0.86, (ctx.portrait ? 150 : 190) * u, 'Inter', { maxLines: 1, min: 0.55, weight: '600 ', soft: true });   // 先缩字号放一行，放不下再折两行
        if (!sz.ok) sz = fitB(c, title, W * 0.86, (ctx.portrait ? 150 : 190) * u, 'Inter', { maxLines: 2, weight: '600 ' });
        const cy = G.cy - (sz.lines.length - 1) * sz.size * 0.55;
        // 竖屏：整块缩到页眉（主标题 → 约 64px），眉题和副标题淡出
        const hs = keep ? Math.min(1, 68 * u / sz.size) : 1, sc = keep ? lerp(1, hs, out) : lerp(1, 0.92, out), ty = keep ? lerp(0, G.hdrY - cy, out) : 0;
        c.translate(W / 2, cy + ty); c.scale(sc, sc); c.translate(-W / 2, -cy);
        const aSide = 1 - out;
        if (!keep) { c.globalAlpha = 1 - out; if (out > 0) c.filter = `blur(${(20 * out * u).toFixed(1)}px)`; }
        if (eb && aSide > 0) { c.save(); c.globalAlpha *= keep ? aSide : 1; c.font = `500 ${34 * u}px ${ZH}`; c.fillStyle = '#a7a7b4'; c.letterSpacing = `${6 * u}px`; TY.blurIn(c, eb, W / 2, cy - sz.size * 0.95, MO.appleOut(ctx.p(t, qt.at, 0.7)), { blur: 10 * u, dy: 16 * u }); c.restore(); }
        c.font = `600 ${sz.size}px ${SANSB}`; track(c, sz.size); c.fillStyle = '#f5f3ff';
        sz.lines.forEach((ln, i) => TY.blurIn(c, ln, W / 2, cy + i * sz.size * 1.1, MO.appleOut(ctx.p(t, qt.at + 0.15 + i * 0.12, 1.0)), { blur: 26 * u, dy: 40 * u }));
        if (sub && aSide > 0) { c.save(); c.globalAlpha *= keep ? aSide : 1; c.letterSpacing = '0px'; c.font = `400 ${46 * u}px ${ZH}`; c.fillStyle = '#c9c9d4';
          TY.wrap(c, sub, W * 0.84).forEach((ln, i) => TY.blurIn(c, ln, W / 2, cy + (sz.lines.length - 1) * sz.size * 1.1 + 110 * u + i * 62 * u, MO.appleOut(ctx.p(t, qt.at + 0.5, 0.9)), { blur: 12 * u, dy: 22 * u })); c.restore(); }
        c.restore();
      }
    }
    // ---- 每一屏：下一屏开始时往左后方退走（0.65s），不和下一屏的内容叠着 ----
    slides.forEach((s, si) => {
      if (t < s.start - 1e-6) return;
      const nx = slides[si + 1], leave = MO.appleOut(MO.seg(t, s.next - 0.15, s.next + (nx && nx.type === 'num' ? 0.3 : 0.5))); if (leave >= 1) return;
      if (s.type === 'num') return drawNumber(c, s, t, leave, ctx, shadowText);
      if (s.type === 'img') return drawCard(c, s.cards[0], 0, t, leave, ctx, bd, cardGeoImg(s.cards[0], ctx), 'i' + si);
      // 无图卡并排：n 张时的位置；新卡进来时旧卡按弹簧挪到新位置
      const vis = s.cards.filter(cd => t >= cd.q.at - 1e-6), n = vis.length, last = vis[n - 1]; ctx._stat = s.cards.some(cd => cd.q.data && cd.q.data.value != null);
      const rf = n > 1 ? MO.spring(t - last.q.at, { duration: 0.7, bounce: 0.12 }) : 1;
      vis.forEach((cd, j) => { const A = rowGeo(j, n, ctx, s.cards.length), Bp = j < n - 1 ? rowGeo(j, n - 1, ctx, s.cards.length) : A;
        const geo = { cx: lerp(Bp.cx, A.cx, rf), cy: lerp(Bp.cy, A.cy, rf), w: lerp(Bp.w, A.w, rf), h: lerp(Bp.h, A.h, rf), focus: j === n - 1 ? 1 : 1 - 0.25 * rf };
        drawCard(c, cd, j, t, leave, ctx, bd, geo, 'c' + si + '_' + j); });
    });
  },
};
// 截图卡的屏幕几何
function cardGeoImg(cd, ctx) { return { cx: ctx.W / 2, cy: G.cy, w: cd.box.w, h: cd.box.h, focus: 1 }; }
// 无图卡：横屏排一行（1 张时宽一点），竖屏叠成一列；total = 这一屏最终有几张（排版按当前张数，但尺寸上限按最终张数，挪位时不忽大忽小）
function rowGeo(j, n, ctx, total) {
  const { W, u } = ctx, gap = 44 * u;
  if (ctx.portrait) { const w = W * 0.86, h = Math.min(400 * u, (G.bh - (total - 1) * gap) / total), y0 = G.cy - (n * h + (n - 1) * gap) / 2; return { cx: W / 2, cy: y0 + j * (h + gap) + h / 2, w, h }; }
  const w = total === 1 ? 760 * u : Math.min(540 * u, (W * 0.9 - (total - 1) * gap) / total), h = Math.min((ctx._stat ? 660 : 540) * u, G.bh * 0.9), x0 = W / 2 - (n * w + (n - 1) * gap) / 2;
  return { cx: x0 + j * (w + gap) + w / 2, cy: G.cy, w, h };
}
// 一张卡：弹簧从下方升起＋绕 Y 轴转正；内容画进离屏纹理（毛玻璃按卡片的屏幕位置取样），再伪 3D 贴回
function drawCard(c, cd, i, t, leave, ctx, bd, geo, key) {
  const { u, data: d } = ctx, q = cd.q, lt = ctx.lt(t, q.at); if (lt <= 0) return;
  const sp = MO.spring(lt, { duration: 0.7, bounce: 0.15 }), acc = d.accent || '#3d6bff';
  const Bw = geo.w, Bh = geo.h, cx = geo.cx - leave * ctx.W * 0.35, cy = geo.cy + (1 - sp) * 170 * u + MO.float(t, 5 * u, 3.6, i);
  const sc = (0.9 + 0.1 * sp) * (1 - 0.12 * leave) * lerp(0.97, 1, geo.focus), ry = (1 - sp) * -0.45 + 0.03 * Math.sin(t * 0.9 + i) - 0.35 * leave, a = clamp(lt / 0.2) * (1 - leave) * lerp(0.6, 1, geo.focus);
  const pad = 60, tex = scr[key] || (scr[key] = document.createElement('canvas')); tex.width = Math.ceil(Bw + 2 * pad); tex.height = Math.ceil(Bh + 2 * pad);
  const g = tex.getContext('2d'); g.reset();
  const sw = Bw * sc, sh = Bh * sc;
  if (ctx.alpha) { g.fillStyle = 'rgba(12,12,22,0.62)'; g.beginPath(); g.roundRect(pad, pad, Bw, Bh, 34 * u); g.fill(); }
  UI.glass(g, { x: pad, y: pad, w: Bw, h: Bh, r: 34 * u, bd, sample: { x: cx - sw / 2, y: cy - sh / 2, w: sw, h: sh }, tint: ctx.alpha ? 0.08 : 0.075, stroke: 0.22, light: 0.14, shadow: false });
  g.save(); g.translate(pad, pad);
  if (cd.im) imgContent(g, cd, Bw, Bh, t, ctx, acc); else featContent(g, cd, Bw, Bh, lt, t, ctx, acc);
  UI.sheen(g, UI.rrPath(0, 0, Bw, Bh, 34 * u), MO.seg(lt, 0.4, 1.2), { x0: 0, x1: Bw, width: 120 * u, angle: -0.35, alpha: 0.16 });   // 光扫过整张卡（入场后 0.4s）
  g.restore();
  c.save(); c.globalAlpha = a;
  UI.shadow(c, cx - sw / 2 + 10 * u, cy - sh / 2 + 20 * u, sw - 20 * u, sh - 20 * u, 34 * u, { blur: 80 * u, oy: 40 * u, alpha: 0.55 });
  UI.persp(c, tex, { cx, cy, w: tex.width * sc, h: tex.height * sc, ry, persp: 1800 * u, strip: 3, shade: 0.35 });
  c.restore();
}
// 步骤号徽章
function badge(g, x, y, s, n, acc, u) { g.fillStyle = acc; g.beginPath(); g.roundRect(x, y, s, s, s * 0.28); g.fill();
  g.fillStyle = '#fff'; g.font = `700 ${s * 0.5}px ${SANSB}`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(n).padStart(2, '0'), x + s / 2, y + s / 2 + 2 * u); g.textAlign = 'left'; g.textBaseline = 'alphabetic'; }
// 截图卡的内容：步骤号＋标题＋说明，截图 contain；highlight 框出一块
function imgContent(g, cd, Bw, Bh, t, ctx, acc) {
  const { u } = ctx, q = cd.q, B = cd.box, I = B.img, hp = 40 * u, bs = 70 * u;
  badge(g, hp, hp, bs, cd.n, acc, u);
  if (B.side) {                                                           // 左字右图：标题、说明在左栏往下排
    const tw = B.side - hp, tl = fitB(g, q.text || '', tw, 58 * u, 'PuHui-Bold', { maxLines: 3, weight: '600 ' });
    g.font = `600 ${tl.size}px ${ZHB}`; g.fillStyle = '#f5f5f7'; tl.lines.forEach((ln, k) => g.fillText(ln, hp, hp + bs + 70 * u + k * tl.size * 1.25));
    if (q.sub) { const sl = fitB(g, q.sub, tw, 34 * u, 'PuHui-Medium', { maxLines: 5 }); g.font = `400 ${sl.size}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.66)';
      sl.lines.forEach((ln, k) => g.fillText(ln, hp, hp + bs + 70 * u + tl.lines.length * tl.size * 1.25 + 30 * u + k * sl.size * 1.4)); }
  } else {
    const tx = hp + bs + 26 * u, tw = Bw - tx - hp;
    if (ctx.portrait) {                                                    // 竖屏：卡头高一些，标题、说明字大一号
      const tl = fitB(g, q.text || '', tw, 56 * u, 'PuHui-Bold', { maxLines: 2, weight: '600 ', min: 0.7 }); g.font = `600 ${tl.size}px ${ZHB}`; g.fillStyle = '#f5f5f7';
      tl.lines.forEach((ln, k) => g.fillText(ln, tx, hp + tl.size * 0.85 + k * tl.size * 1.2));
      // 标题占两行时说明只留一行，卡头放得下
      if (q.sub) { const sl = fitB(g, q.sub, tw, 34 * u, 'PuHui-Medium', { maxLines: tl.lines.length > 1 ? 1 : 2, min: 0.75 }); g.font = `400 ${sl.size}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.66)';
        sl.lines.forEach((ln, k) => g.fillText(ln, tx, hp + tl.size * 0.85 + (tl.lines.length - 1) * tl.size * 1.2 + 24 * u + sl.size + k * sl.size * 1.35)); }
    } else {
    const tl = TY.fit(g, q.text || '', tw, 46 * u, 'PuHui-Bold', { maxLines: 1, weight: '600 ' });
    g.font = `600 ${tl.size}px ${ZHB}`; g.fillStyle = '#f5f5f7'; g.fillText(tl.lines[0] || '', tx, hp + (q.sub ? 32 * u : 48 * u));
    if (q.sub) { const sl = TY.fit(g, q.sub, tw, 28 * u, 'PuHui-Medium', { maxLines: 1 }); g.font = `400 ${sl.size}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.62)'; g.fillText(sl.lines[0] || '', tx, hp + 74 * u); }
    }
  }
  const f = CLIP.fit(cd.im.width, cd.im.height, I.x, I.y, I.w, I.h);
  g.save(); g.shadowColor = 'rgba(0,0,0,.45)'; g.shadowBlur = 30 * u; g.shadowOffsetY = 12 * u; g.fillStyle = '#000'; g.beginPath(); g.roundRect(f.x, f.y, f.w, f.h, 10 * u); g.fill(); g.restore();
  g.save(); g.beginPath(); g.roundRect(f.x, f.y, f.w, f.h, 10 * u); g.clip(); g.drawImage(cd.im, f.x, f.y, f.w, f.h); g.restore();
  g.strokeStyle = 'rgba(255,255,255,0.22)'; g.lineWidth = 1.5; g.beginPath(); g.roundRect(f.x + .75, f.y + .75, f.w - 1.5, f.h - 1.5, 10 * u); g.stroke();
  // 框出重点：at 这一帧框开始弹出，之后呼吸；一道光扫过框内（只认落在这张卡在屏期间的 highlight）
  const nxAt = (() => { const i = ctx.cues.indexOf(q); const n = ctx.cues.slice(i + 1).find(z => ['card', 'step', 'image', 'number'].includes(z.kind)); return n ? (n.kind === 'number' ? n.at - 0.9 : n.at) : 1e9; })();
  for (const qh of ctx.of('highlight')) {
    if (qh.at < q.at || qh.at >= nxAt || !qh.data || !qh.data.rect) continue;
    const hl = ctx.lt(t, qh.at); if (hl <= 0) continue;
    const R = CLIP.sub(qh.data.rect, f), s = MO.spring(hl, { duration: 0.45, bounce: 0.25 }), e = 8 * u * (1 - s);
    g.save(); g.fillStyle = 'rgba(0,0,0,0.38)'; g.beginPath(); g.rect(f.x, f.y, f.w, f.h); g.roundRect(R.x - e, R.y - e, R.w + 2 * e, R.h + 2 * e, 10 * u); g.globalAlpha = clamp(hl / 0.3); g.fill('evenodd'); g.restore();
    g.save(); g.strokeStyle = acc; g.lineWidth = 5 * u; g.shadowColor = acc; g.shadowBlur = 18 * u * (0.7 + 0.3 * Math.sin(hl * 5)); g.globalAlpha = clamp(hl / 0.15);
    g.beginPath(); g.roundRect(R.x - e, R.y - e, R.w + 2 * e, R.h + 2 * e, 10 * u); g.stroke(); g.restore();
    UI.sheen(g, UI.rrPath(R.x, R.y, R.w, R.h, 10 * u), MO.seg(hl, 0.25, 1.0), { x0: R.x, x1: R.x + R.w, width: Math.max(60 * u, R.w * 0.2), alpha: 0.35 });
    if (qh.text) { g.font = `600 ${30 * u}px ${ZHB}`; const lw = g.measureText(qh.text).width + 36 * u, ly = R.y + R.h + 16 * u + 48 * u > f.y + f.h ? R.y - 64 * u : R.y + R.h + 16 * u;
      g.save(); g.globalAlpha = clamp((hl - 0.15) / 0.25); g.fillStyle = acc; g.beginPath(); g.roundRect(clamp(R.x, f.x, f.x + f.w - lw), ly, lw, 48 * u, 24 * u); g.fill(); g.fillStyle = '#fff'; g.textBaseline = 'middle'; g.fillText(qh.text, clamp(R.x, f.x, f.x + f.w - lw) + 18 * u, ly + 25 * u); g.restore(); }
  }
}
// 无图功能卡的内容：图标（或步骤号）→ 大标题 → 说明 →（可选）大数字计数。横屏竖卡上下排，竖屏横卡左右排
function featContent(g, cd, Bw, Bh, lt, t, ctx, acc) {
  const { u } = ctx, q = cd.q, dd = q.data || {}, hasNum = dd.value != null, pad = 48 * u, tile = (ctx.portrait ? 110 : 120) * u;
  const drawTile = (x, y) => { g.fillStyle = 'rgba(255,255,255,0.10)'; g.beginPath(); g.roundRect(x, y, tile, tile, 26 * u); g.fill();
    if (!dd.icon || icon(g, dd.icon, x + tile / 2, y + tile / 2, tile * 0.6, '#f2f2f7', t) === false) {
      g.fillStyle = acc; g.beginPath(); g.roundRect(x, y, tile, tile, 26 * u); g.fill(); g.fillStyle = '#fff'; g.font = `700 ${tile * 0.46}px ${SANSB}`; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(String(cd.n).padStart(2, '0'), x + tile / 2, y + tile / 2 + 3 * u); g.textAlign = 'left'; g.textBaseline = 'alphabetic'; } };
  const num = (x, y, size, align) => { const v = MO.expoOut(clamp((lt - 0.35) / 0.9)); g.save(); g.font = `600 ${size}px ${SANSB}`; track(g, size, -0.03); g.fillStyle = '#fff';
    TY.tabular(g, fmtNum(dd, v), x, y, { align }); g.restore(); if (dd.label) { g.font = `400 ${28 * u}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.6)'; g.textAlign = align; g.fillText(dd.label, x, y + 46 * u); g.textAlign = 'left'; } };
  g.textAlign = 'left'; g.textBaseline = 'alphabetic';
  if (!ctx.portrait) {
    drawTile(pad, pad);
    const tl = fitB(g, q.text || '', Bw - 2 * pad, 64 * u, 'PuHui-Bold', { maxLines: 2, weight: '600 ', min: 0.6 });
    let y = pad + tile + 40 * u + tl.size; g.font = `600 ${tl.size}px ${ZHB}`; g.fillStyle = '#f5f5f7'; tl.lines.forEach((ln, k) => g.fillText(ln, pad, y + k * tl.size * 1.2)); y += (tl.lines.length - 1) * tl.size * 1.2;
    if (q.sub) { const sl = fitB(g, q.sub, Bw - 2 * pad, (hasNum ? 32 : 38) * u, 'PuHui-Medium', { maxLines: hasNum ? 2 : 4, min: 0.75 }); g.font = `400 ${sl.size}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.66)';
      sl.lines.forEach((ln, k) => g.fillText(ln, pad, y + 26 * u + sl.size + k * sl.size * 1.45)); }
    if (hasNum) { g.fillStyle = 'rgba(255,255,255,0.12)'; g.fillRect(pad, Bh - pad - 200 * u, Bw - 2 * pad, 1.5); num(pad, Bh - pad - 66 * u, Math.min(128 * u, (Bw - 2 * pad) / Math.max(3, fmtNum(dd, 1).length) * 1.6), 'left'); }
  } else {
    drawTile(pad, (Bh - tile) / 2);
    const tx = pad + tile + 34 * u, nw = hasNum ? Math.min(340 * u, Bw * 0.36) : 0, tw = Bw - tx - pad - nw;
    const tl = fitB(g, q.text || '', tw, 62 * u, 'PuHui-Bold', { maxLines: 2, weight: '600 ', min: 0.6 });   // 标题放不下就折两行，不再截掉后半句
    const sl = q.sub ? fitB(g, q.sub, tw, 38 * u, 'PuHui-Medium', { maxLines: tl.lines.length > 1 ? 1 : 2, min: 0.75 }) : { lines: [], size: 0 };
    const tH = tl.size + (tl.lines.length - 1) * tl.size * 1.15, blk = tH + (sl.lines.length ? 22 * u + sl.lines.length * sl.size * 1.4 : 0), y0 = (Bh - blk) / 2 + tl.size * 0.85;
    g.font = `600 ${tl.size}px ${ZHB}`; g.fillStyle = '#f5f5f7'; tl.lines.forEach((ln, k) => g.fillText(ln, tx, y0 + k * tl.size * 1.15));
    const ys = y0 + (tl.lines.length - 1) * tl.size * 1.15;
    g.font = `400 ${sl.size}px ${ZH}`; g.fillStyle = 'rgba(235,235,245,0.66)'; sl.lines.forEach((ln, k) => g.fillText(ln, tx, ys + 22 * u + sl.size + k * sl.size * 1.4));
    if (hasNum) num(Bw - pad, Bh / 2 + 20 * u, Math.min(96 * u, nw / Math.max(3, fmtNum(dd, 1).length) * 1.7), 'right');
  }
}
// 大数字一屏：at 落定（提前 0.9s 开始滚）；数字上方可有小字 label，下方说明（text 或 sub）
function drawNumber(c, s, t, leave, ctx, shadowText) {
  const { W, u } = ctx, q = s.q, nd = q.data || {}, k = MO.expoOut(clamp((t - s.start) / 0.9)), a = MO.appleOut(clamp((t - s.start - 0.12) / 0.3)) * (1 - leave);
  const cap = q.text ?? q.sub;
  c.save(); c.globalAlpha = a; shadowText(c); c.textAlign = 'center'; c.textBaseline = 'alphabetic';
  if (leave > 0) { c.filter = `blur(${(16 * leave * u).toFixed(1)}px)`; }
  const str = fmtNum(nd, k), full = fmtNum(nd, 1);
  c.font = `600 ${220 * u}px ${SANSB}`; track(c, 220 * u, -0.03); let size = 220 * u; const fw = c.measureText(full).width; if (fw > W * 0.88) size *= W * 0.88 / fw;
  c.font = `600 ${size}px ${SANSB}`; track(c, size, -0.03); c.fillStyle = '#fff';
  const y = G.cy + size * 0.32 - (cap ? 30 * u : 0);
  TY.tabular(c, str, W / 2, y, { align: 'center' });
  c.letterSpacing = '0px';
  if (nd.label) { c.font = `500 ${34 * u}px ${ZH}`; c.fillStyle = '#a7a7b4'; c.fillText(nd.label, W / 2, y - size * 0.85); }
  if (cap) { c.font = `400 ${(ctx.portrait ? 46 : 44) * u}px ${ZH}`; c.fillStyle = 'rgba(235,235,245,0.78)'; TY.wrap(c, cap, W * 0.84).forEach((ln, i) => c.fillText(ln, W / 2, y + 100 * u + i * 60 * u)); }
  c.restore();
}
})();


// ===== huashu clip t3_finance_chart.js =====
// 片段 · 财经图表（t3）：版式先出 → 坐标系 → 数据长出 → 只标一件事。真实数据驱动，单位/来源/角标全从 data 读。
// data: { title, unit（副标题：指标与单位）, source（来源，左下）, badge?（只有写了才画右下角标，如 "示意数据"），
//         chart: 'bar'|'line'|'candle', series: [{label, value}] 或 K 线 [{label, o, h, l, c}], decimals=1, prefix='', suffix='',
//         highlight?: {index, text, sub}, colors?: {main, accent, dim}, upDown?: 'cn'（红涨绿跌，默认）|'us' }
// cues（at 秒）：title（版式）· bar|line|candle（数据从 at 开始长出，dur 默认按根数；柱状图可写多个 bar cue，各带 data.index（数或数组）＝这几根在这一刻才长，让每根柱子踩自己的那个词）
//         series 每项可写 color（柱子自己的颜色，和片里别处同一个量的配色对上）· highlight（at 这一帧标注圆点弹出；text/sub 覆盖 data.highlight，data.index 指定第几根）
//         number（大数字：data {value, prefix, suffix, decimals}，at = 数字落定的时刻，提前 0.9s 开始滚）
// 规矩：柱一律从 0 起；折线/K 线纵轴不从 0 起时自动在副标题后加「纵轴未从 0 开始」。
CLIPS.t3_finance_chart = (() => {
const { clamp, lerp } = U;
const RED = '#E3120B', INK = '#0C0C0C', SUB = '#4F5B61', GRID = 'rgba(12,12,12,0.13)';
const CN = '"PuHui-Medium"', CNB = '"PuHui-Bold"', NUM = '"RobotoCondensed", "PuHui-Medium"';
let L, BUF;
const fmtv = (d, v) => (d.prefix || '') + TY.fmt(v, d.decimals ?? 1) + (d.suffix || '');
return {
  fonts: ['PuHui-Medium', 'PuHui-Bold'],
  safe: true,
  init(ctx) {
    const { W, H, u, data: d } = ctx, m = (ctx.portrait ? 70 : 90) * u, c = document.createElement('canvas').getContext('2d');
    const kind = (ctx.of('bar', 'line', 'candle')[0] || {}).kind || d.chart || 'bar';
    const S = d.series || [];
    const vals = kind === 'candle' ? S.flatMap(r => [r.h, r.l]) : S.map(r => r.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    let zeroBased = kind === 'bar' || lo <= 0 || (hi - lo) / hi > 0.6;
    if (zeroBased) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.08, ticks = CH.ticks(zeroBased ? lo : lo - pad, hi + pad, ctx.portrait ? 5 : 4);
    const step = ticks[1] - ticks[0];                                     // 刻度要把数据整个包住（CH.ticks 只给区间内的整刻度）
    while (ticks[ticks.length - 1] < hi + pad * 0.5) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    while (ticks[0] > (zeroBased ? lo : lo - pad * 0.5)) ticks.unshift(+(ticks[0] - step).toFixed(10));
    const titleSz = (ctx.portrait ? 64 : 58) * u, t = TY.fit(c, d.title || '', W - 2 * m, titleSz, 'PuHui-Bold', { maxLines: ctx.portrait ? 3 : 2, weight: '700 ' });
    const sub = (d.unit || '') + (!zeroBased ? (d.unit ? '　' : '') + '纵轴未从 0 开始' : '');
    // 副标题、来源行：放不下先缩一点，再折两行（竖屏长口径会出画）；来源行要让开右下角标
    const fsz = ctx.portrait ? 1.2 : 1, subF = TY.fit(c, sub, W - 2 * m, 32 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    c.font = `700 ${28 * u}px "PuHui-Medium"`; const badgeW = d.badge ? c.measureText(d.badge).width + 28 + 30 * u : 0;
    const srcF = TY.fit(c, d.source || '', W - 2 * m - badgeW, 26 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    const top = ctx.safe.top + 80 * u + t.lines.length * t.size * 1.25 + 60 * u + (subF.lines.length - 1) * subF.size * 1.3;
    const band = ctx.of('number').length ? 150 * u : 0;                 // 有 number：给大数字单独留一条，不压在图区里（折线一上涨，右上角正好是线头）
    const F = { x: m + 20 * u, y: top + 40 * u + band, w: W - 2 * m - 40 * u, h: H - top - 40 * u - band - (ctx.portrait ? 260 : 190) * u - ctx.safe.bottom - (srcF.lines.length - 1) * srcF.size * 1.3 };   // safe.bottom：字幕带整条让出来，来源行落在它上面
    const n = S.length, slot = (F.w - 80 * u) / n, xs = S.map((_, i) => F.x + slot * (i + 0.5));   // 右边留 80px 给刻度值，别压在最后一根柱上
    // x 轴：时间序列（折线/K 线，或 data.xAxis:'time'）按间隔抽稀；类目（柱状图默认）每根都要有名字，放不下就缩字、再放不下错成两行
    const category = (d.xAxis || (kind === 'bar' ? 'category' : 'time')) === 'category';
    const fs0 = ctx.portrait ? 1.2 : 1, xf = 30 * u * fs0;
    c.font = `400 ${xf}px "RobotoCondensed", "PuHui-Medium"`;
    const maxW = Math.max(1, ...S.map(r => c.measureText(String(r.label ?? '')).width));
    const xScale = category ? clamp(slot * 0.94 / maxW, 0.7, 1) : 1, stagger = category && slot * 0.94 < maxW * 0.7;
    // 刻度标签按步长定小数位：步长 2.5 就要 1 位，不然 −2.5/2.5/7.5 会被四舍五入印成 −3/3/8（刻度撒谎）
    let tdec = 0; while (tdec < 4 && Math.abs(step * 10 ** tdec - Math.round(step * 10 ** tdec)) > 1e-6) tdec++;
    if (kind === 'bar' && Math.min(...vals) < 0) {                       // 负数柱的数值写在柱底下方：最低那根下面留出一行，不然压到类目名
      const vmin = Math.min(...vals), need = 56 * u * fs0;
      while ((vmin - ticks[0]) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.unshift(+(ticks[0] - step).toFixed(10));
    }
    const qh1 = ctx.of('highlight')[0], H1 = d.highlight || {}, hiI = qh1 ? (qh1.data && qh1.data.index != null ? qh1.data.index : (H1.index != null ? H1.index : S.length - 1)) : -1;
    if (kind === 'bar' && hiI >= 0 && S[hiI] && S[hiI].value >= 0 && S.some((r, j) => j !== hiI && r.value >= S[hiI].value)) {   // 标注的不是最高那根：文字块要抬到邻柱之上，顶上留够一块（不够就加一档刻度）
      const nL = [C(qh1.text, H1.text), C(qh1.sub, H1.sub)].filter(Boolean).length, need = (52 * nL + 44 + 30) * u * fs0, vmax = Math.max(...vals);
      while ((ticks[ticks.length - 1] - vmax) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    }
    L = { subF, srcF, m, kind, S, zeroBased, ticks, tdec, yS: CH.lin(ticks[0], ticks[ticks.length - 1], F.y + F.h, F.y), F, xs, slot, t, sub, band, category, xFont: xf * xScale, stagger,
      every: category ? 1 : Math.max(1, Math.ceil(n / (ctx.portrait ? 6 : 10))) };
    const main = (d.colors && d.colors.main) || (kind === 'bar' ? '#006BA2' : RED);
    L.col = { main, accent: (d.colors && d.colors.accent) || main, dim: (d.colors && d.colors.dim) || '#C6D2D8' };
    L.updown = d.upDown === 'us' ? ['#1B9E5A', RED] : [RED, '#1B9E5A'];
  },
  draw(c0, t, ctx) {
    // alpha：图是白纸黑字，叠在深色画面上墨色看不见。先画进缓冲，再带一圈白色光晕合成（和 t1 同一招）
    const c = ctx.alpha ? (BUF = BUF || document.createElement('canvas'), BUF.width = ctx.W, BUF.height = ctx.H, BUF.getContext('2d')) : c0;
    const { W, H, u, data: d } = ctx, { m, F, yS, xs, S, kind } = L, fs = ctx.portrait ? 1.2 : 1;   // 竖屏在手机上看：小字放大两成
    const qt = ctx.of('title')[0] || { at: 0 }, lt = ctx.lt(t, qt.at);
    if (!ctx.alpha) { c.fillStyle = '#FFFFFF'; c.fillRect(0, 0, W, H); }
    // ---- 版式：顶红线 → 小旗 → 标题 → 副标题（单位/口径）→ 来源 → 角标 ----
    if (lt > 0) {
      const p = MO.quintOut(MO.seg(lt, 0, 0.45));
      c.fillStyle = RED; c.fillRect(0, 0, W * p, 8 * u); CH.tag(c, m, ctx.safe.top + 52 * u, 96 * p * u, 22 * u, RED);
      c.save(); c.textBaseline = 'alphabetic'; c.textAlign = 'left';
      const tp = MO.quintOut(MO.seg(lt, 0.05, 0.5)); c.globalAlpha = tp; c.fillStyle = INK; c.font = `700 ${L.t.size}px ${CNB}`;
      L.t.lines.forEach((ln, i) => c.fillText(ln, m, ctx.safe.top + 80 * u + (i + 1) * L.t.size * 1.2 + (1 - tp) * 18 * u));
      const sp = MO.quintOut(MO.seg(lt, 0.15, 0.6)), sy = ctx.safe.top + 80 * u + L.t.lines.length * L.t.size * 1.2 + 54 * u;
      c.globalAlpha = sp; c.font = `400 ${L.subF.size}px ${CN}`; c.fillStyle = SUB; L.subF.lines.forEach((ln, k) => c.fillText(ln, m, sy + k * L.subF.size * 1.3 + (1 - sp) * 14 * u));
      c.globalAlpha = MO.seg(lt, 0.2, 0.6); c.font = `400 ${L.srcF.size}px ${CN}`; c.fillStyle = '#7c868b';
      L.srcF.lines.forEach((ln, k, a) => c.fillText(ln, m, H - 40 * u - ctx.safe.bottom - (a.length - 1 - k) * L.srcF.size * 1.3));
      c.restore();
      if (d.badge) { c.save(); c.globalAlpha = MO.seg(lt, 0.2, 0.6); CH.demoBadge(c, d.badge, { x: W - m, y: H - 50 * u - ctx.safe.bottom, font: `700 ${28 * u}px ${CN}`, col: RED, box: 'rgba(227,18,11,0.06)' }); c.restore(); }
    }
    // ---- 坐标系（版式之后 0.25s） ----
    const qd = ctx.of('bar', 'line', 'candle')[0] || { at: qt.at + 0.75 }, gStart = Math.min(qt.at + 0.25, qd.at - 0.4);
    const gp = MO.seg(ctx.lt(t, gStart), 0, 0.6);
    const fmtTick = v => TY.fmt(v, L.tdec);
    CH.grid(c, F, L.ticks, yS, gp, { col: GRID, zeroCol: L.zeroBased ? INK : GRID, font: `400 ${28 * u * fs}px ${NUM}`, labelCol: '#5b666b', side: 'right', lw: 1.5 * u, zeroLw: 3 * u, fmt: fmtTick, labelDy: -10 * u });
    const qh0 = ctx.of('highlight')[0], hi0 = qh0 ? (qh0.data && qh0.data.index != null ? qh0.data.index : (d.highlight && d.highlight.index != null ? d.highlight.index : S.length - 1)) : -1;
    const keep = i => i % L.every === 0 || i === hi0 || (i === S.length - 1 && (S.length - 1) % L.every >= L.every / 2);   // 被标注的那根永远有名字；末尾标签离上一个太近就不硬塞
    const xp = MO.seg(ctx.lt(t, gStart + 0.15), 0, 0.5), xy = F.y + F.h + 46 * u, xo = { font: `400 ${L.xFont}px ${NUM}`, col: '#3a4246' };
    if (!L.stagger) CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: keep(i) ? String(r.label ?? '') : '' })), xy, xp, xo);
    else { CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? '' : String(r.label ?? '') })), xy, xp, xo); CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? String(r.label ?? '') : '' })), xy + L.xFont * 1.15, xp, xo); }
    // ---- 数据 ----
    const n = S.length, ddur = qd.dur || Math.min(2.2, Math.max(0.9, n * (kind === 'bar' ? 0.22 : 0.06)));
    const dp = ctx.p(t, qd.at, ddur);
    const qh = ctx.of('highlight')[0], hi = qh ? (qh.data && qh.data.index != null ? qh.data.index : (d.highlight && d.highlight.index != null ? d.highlight.index : n - 1)) : -1;
    const hiP = qh ? MO.smooth(ctx.p(t, qh.at, 0.4)) : 0;
    let anchor = null;
    if (kind === 'bar') {
      const bw = Math.min(150 * u, L.slot * 0.62);
      const on = qh && ctx.lt(t, qh.at) > 0, opt = { col: L.col.main, hiP, hiCol: L.col.accent, dimCol: L.col.dim,
        fmt: v => fmtv(d, v), font: `600 ${Math.max(24 * u * fs, Math.min(34 * u * fs, L.slot * 0.3))}px ${NUM}`, labelCol: INK, label: n <= 14 };
      const own = {};                                                      // 带 data.index 的 bar cue：这几根柱子从它的 at 起单独长
      for (const q of ctx.of('bar')) if (q.data && q.data.index != null) [].concat(q.data.index).forEach(i => { own[i] = q; });
      let tops;
      if (!Object.keys(own).length && !S.some(r => r.color)) tops = CH.bars(c, S.map((r, i) => ({ x: xs[i], v: r.value })), yS, bw, dp, { ...opt, lag: Math.min(0.22, 2 / n), hi: on ? hi : -1 });
      else tops = S.map((r, i) => {
        const q = own[i], p = q ? ctx.p(t, q.at, q.dur || 0.7) : MO.lagged(i, n, dp, Math.min(0.22, 2 / n));
        const col = r.color || L.col.main;
        return CH.bars(c, [{ x: xs[i], v: r.value }], yS, bw, p, { ...opt, lag: 0, col, hiCol: col, hi: on ? (i === hi ? 0 : 1) : -1 })[0];
      });
      if (hi >= 0) anchor = [xs[hi], tops[hi][1]];
      L.neg = hi >= 0 && S[hi].value < 0; L.tops = tops;
      L.barAbove = hi >= 0 && S[hi].value >= 0 && S.some((r, j) => j !== hi && r.value > S[hi].value * 1.5);   // 有更高的柱子挡在旁边：标注放在这根柱子正上方，别压进别的柱子
    } else if (kind === 'line') {
      const pts = S.map((r, i) => [xs[i], yS(r.value)]);
      const lp = MO.smooth(dp);
      const hp = CH.line(c, pts, lp, { col: L.col.main, lw: 5 * u, headR: 9 * u, area: { top: F.y, base: F.y + F.h, c0: 'rgba(227,18,11,0.12)', c1: 'rgba(227,18,11,0)' } });
      if (hp && lp < 1) { const v = yS.inv(hp[1]); c.save(); c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'left'; c.textBaseline = 'middle'; TY.tabular(c, fmtv(d, v), hp[0] + 20 * u, hp[1] - 4 * u); c.restore(); }
      else if (lp >= 1) { const v = S[n - 1].value; c.save(); c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'right'; c.textBaseline = 'alphabetic'; TY.tabular(c, fmtv(d, v), pts[n - 1][0], pts[n - 1][1] - 24 * u, { align: 'right' }); c.restore(); }
      if (hi >= 0) anchor = pts[hi];
    } else {
      const cw = Math.min(30 * u, L.slot * 0.6);
      CH.candles(c, S, xs, yS, cw, dp, { up: L.updown[0], down: L.updown[1], lag: Math.min(0.06, 1.5 / n) });
      if (hi >= 0) anchor = [xs[hi], yS(S[hi].c)];
    }
    // ---- 标注：一镜只标一件事 ----
    let above = L.barAbove;
    if (qh && anchor && kind === 'bar' && !above && !L.neg) {             // 预演斜向标注的文字框：压到别的柱子或它的数值，就改放这根柱子正上方
      const H0 = d.highlight || {}, ls = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean);
      const ax = anchor[0] - Math.min(150 * u, L.slot * 0.62) / 2, right = ax > F.x + F.w * 0.55, tx = right ? ax - 60 * u : ax + 60 * u, ty = Math.max(F.y + 60 * u, anchor[1] - 140 * u);
      c.save(); c.font = `700 ${50 * u}px ${NUM}`; let tw = c.measureText(ls[0] || '').width; c.font = `400 ${28 * u * fs}px ${CN}`; if (ls[1]) tw = Math.max(tw, c.measureText(ls[1]).width); c.restore();
      const bx0 = right ? tx - 12 - tw : tx + 12, bx1 = bx0 + tw, by1 = ty + (ls.length > 1 ? 56 : 16) * u;
      above = L.tops.some(([bx, by], j) => j !== hi && bx + L.slot * 0.45 > bx0 && bx - L.slot * 0.45 < bx1 && by - 44 * u * fs < by1);
    }
    if (qh && anchor && kind === 'bar' && above) {
      const H0 = d.highlight || {}, lines = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean), k = ctx.lt(t, qh.at);
      if (k > 0) {
        const s = MO.spring(k, { duration: 0.45, bounce: 0.25 }), lp = MO.quintOut(MO.seg(k, 0.12, 0.45)), fp = MO.quintOut(MO.seg(k, 0.3, 0.7));
        // 文字块放在这根柱子正上方：先量宽，横向夹在图区里；再抬到它横跨的所有柱子（含数值标签）之上，引线从本柱数值标签上沿连上去
        const [x, y] = anchor, lh = 52 * u * fs, vlab = 44 * u * fs;
        c.save(); c.font = `700 ${50 * u * fs}px ${NUM}`; let tw = c.measureText(lines[0]).width; c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
        const cx = clamp(x, F.x + tw / 2, F.x + F.w - 80 * u - tw / 2);
        const cover = L.tops.filter(([bx]) => bx > cx - tw / 2 - L.slot / 2 && bx < cx + tw / 2 + L.slot / 2).map(([, by]) => by);
        const bottom = Math.max(F.y + lh * lines.length, Math.min(y, ...cover) - vlab - 18 * u);   // 文字块底边
        c.fillStyle = RED; c.beginPath(); c.arc(x, y - 4 * u, 9 * u * Math.max(0, s), 0, 7); c.fill();
        const y0 = y - vlab, y1 = bottom + 8 * u;
        if (y0 - y1 > 6 * u) { c.strokeStyle = INK; c.lineWidth = 2 * u; c.beginPath(); c.moveTo(x, y0); c.lineTo(lerp(x, cx, lp), lerp(y0, y1, lp)); c.stroke(); }
        c.globalAlpha = fp; c.fillStyle = INK; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
        lines.forEach((ln, i) => { c.font = i === 0 ? `700 ${50 * u * fs}px ${NUM}` : `400 ${28 * u * fs}px ${CN}`; c.fillText(ln, cx, bottom - (lines.length - 1 - i) * lh * (i === 0 ? 1 : 0.62) + (1 - fp) * 12 * u); });
        c.restore();
      }
    } else if (qh && anchor) {
      if (kind === 'bar') anchor = [anchor[0] - Math.min(150 * u, L.slot * 0.62) / 2, anchor[1]];
      const H0 = d.highlight || {}, lines = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean);
      const right = anchor[0] > F.x + F.w * 0.55, tx = right ? anchor[0] - 60 * u : anchor[0] + 60 * u;
      const ty = L.neg ? Math.min(F.y + F.h - 90 * u, anchor[1] + 70 * u) : Math.max(F.y + 60 * u, anchor[1] - 140 * u);   // 负数柱：标注往下走，别压回零线上方的柱子
      CH.callout(c, ctx.lt(t, qh.at), 0, { x: anchor[0], y: anchor[1], tx, ty, lines, col: INK, dotCol: RED, font: `700 ${50 * u}px ${NUM}`, font2: `400 ${28 * u * fs}px ${CN}`, align: right ? 'right' : 'left', r: 9 * u });
    }
    // ---- 大数字（number cue：at = 落定时刻） ----
    for (const q of ctx.of('number')) {
      const nd = q.data || {}, k = MO.expoOut(clamp((t - (q.at - 0.9)) / 0.9)); if (t < q.at - 0.9) continue;
      c.save(); c.globalAlpha = clamp((t - (q.at - 0.9)) / 0.2); c.font = `700 ${120 * u}px ${NUM}`; c.fillStyle = RED; c.textAlign = 'right'; c.textBaseline = 'alphabetic';
      const numStr = (nd.prefix || '') + TY.fmt((nd.value || 0) * k, nd.decimals ?? 0) + (nd.suffix || ''), by = F.y - 62 * u;   // 落在图区上方专留的那一条（让开顶格刻度值）
      const nw = c.measureText((nd.prefix || '') + TY.fmt(nd.value || 0, nd.decimals ?? 0) + (nd.suffix || '')).width;
      TY.tabular(c, numStr, W - m, by, { align: 'right' });
      const cap = q.text ?? q.sub; if (cap) { c.font = `400 ${30 * u * fs}px ${CN}`; c.fillStyle = SUB; c.textAlign = 'right'; c.fillText(cap, W - m - nw - 24 * u, by - 10 * u); }
      c.restore();
    }
    if (ctx.alpha) { c0.save(); c0.shadowColor = '#FFFFFF'; for (const b of [8, 3, 1.5]) { c0.shadowBlur = b * ctx.u; c0.drawImage(BUF, 0, 0); } c0.restore(); }   // 三遍由宽到窄：外圈柔光＋贴字的实边
  },
};
function C(...a) { return a.find(v => v != null && v !== ''); }
})();
