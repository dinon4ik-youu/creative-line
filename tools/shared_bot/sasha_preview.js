// Живой предпросмотр экрана «Саша (❤️)» (28.10.2026): кадр 9:16 как в итоговом ролике — размытая копия на весь холст,
// чёткое видео поверх (приближение), пословные субтитры (лесенка + по слову), цвет «холодный» (приблизительно),
// музыка, цикл по отрезку [ta, tb] и ТАЙМЛИНИЯ: весь скачанный кусок, основной ролик между красными линиями, слева и справа
// затемнённые запасные куски — тяни красную линию, чтобы подтянуть запас. Всё считается в браузере, ничего не пересобирается.
// Геометрия кадра — те же формулы, что у tools/subs/make.py fit(); субтитры — как subs.py build().
// Данные: {src, frame:{W,H,cw,x0}|null, zoom, ta, tb, dur, base, eid, tl: id элемента под таймлинию,
//          words:[{t,s,e}] (время по файлу куска), music:url|"", sub:{...}, subsOff, width}
// Границы после перетаскивания уходят в Python событием sasha_trim {id, ta, tb}.
// Субтитры правятся прямо на кадре, без режимов: каждое слово лесенки двигается и масштабируется отдельно, все слова
// после лесенки — одним блоком. Правки adj = {ladder:[{dx,dy,s}], single:{dx,dy,s}} (пиксели холста 1080×1920, s — масштаб)
// уходят событием sasha_adj {id, adj} при отпускании мыши (сразу сохраняются). Музыка привязана к таймлинии: позиция музыки =
// позиция в отрезке (плюс сдвиг moff). Под таймлинией — дорожка музыки с волной: тянешь её мышкой, чтобы сдвинуть музыку
// относительно видео (событие sasha_music_off {id, moff}). Пробел — пауза / воспроизведение.
window.SashaPrev = (() => {
  const P = {};
  const MIN = 1.0;                                       // короче отрезок сделать нельзя (как в sasha.py MIN_LEN)
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const meas = document.createElement("canvas").getContext("2d");
  const textW = (txt, size, font) => { meas.font = `${size}px "${font}"`; return meas.measureText(txt).width; };
  const tc = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  const GRADE = "saturate(.8) contrast(1.06) brightness(.97) hue-rotate(-4deg)";

  const CSS = `
  .sp-box{position:relative;overflow:hidden;background:#000;border-radius:8px;user-select:none;cursor:pointer}
  .sp-box video{position:absolute;max-width:none}
  .sp-clip{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);overflow:hidden}
  .sp-tint{position:absolute;inset:0;background:rgba(25,50,110,.07);pointer-events:none}
  .sp-subs{position:absolute;inset:0;pointer-events:none}
  .sp-btn{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.55);color:#fff;border:0;border-radius:16px;
    padding:4px 10px;font-size:15px;cursor:pointer}
  .sp-pause{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:54px;color:#fff;opacity:.85;
    pointer-events:none;text-shadow:0 0 12px #000;display:none}
  .sp-tl{position:relative;height:150px;user-select:none;touch-action:none;color:#aaa;font:11px monospace}
  .sp-tl-track{position:absolute;left:0;right:0;top:40px;height:46px;background:#1b2128;border-radius:6px;overflow:hidden;cursor:pointer}
  .sp-tl-dim{position:absolute;top:0;bottom:0;background:rgba(0,0,0,.62)}
  .sp-tl-act{position:absolute;top:0;bottom:0;background:rgba(245,197,24,.16);border-top:2px solid #f5c518;border-bottom:2px solid #f5c518;
    box-sizing:border-box}
  .sp-tl-word{position:absolute;bottom:0;width:2px;height:9px;background:#54a8ff;opacity:.8}
  .sp-tl-head{position:absolute;top:0;bottom:0;width:2px;background:#fff;pointer-events:none}
  .sp-tl-h{position:absolute;top:30px;width:18px;height:66px;margin-left:-9px;cursor:ew-resize;z-index:3}
  .sp-tl-h::before{content:"";position:absolute;left:8px;top:0;bottom:0;width:3px;background:#ff3b30;border-radius:2px}
  .sp-tl-h::after{content:"";position:absolute;left:2px;top:24px;width:14px;height:18px;background:#ff3b30;border-radius:4px;
    box-shadow:0 0 0 2px rgba(0,0,0,.35)}
  .sp-tl-lab{position:absolute;top:10px;transform:translateX(-50%);color:#ff8a80;white-space:nowrap}
  .sp-tl-tick{position:absolute;top:30px;width:1px;height:8px;background:#555}
  .sp-tl-tickl{position:absolute;top:16px;transform:translateX(-50%);color:#666}
  .sp-tl-info{position:absolute;right:0;top:136px;color:#999}
  .sp-tl-hint{position:absolute;left:0;top:136px;color:#777}
  .sp-tl-music{position:absolute;left:0;right:0;top:94px;height:34px;background:#1b2128;border-radius:6px;overflow:hidden;cursor:grab}
  .sp-tl-music canvas{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}
  .sp-tl-mlab{position:absolute;left:8px;top:2px;color:#9fd39f;font:11px monospace;pointer-events:none;text-shadow:0 0 3px #000}
  .sp-ebtn{background:#2a323b;color:#ddd;border:1px solid #444;border-radius:6px;padding:3px 10px;cursor:pointer;font:13px sans-serif}
  .sp-ebtn:hover{background:#38424d}
  .sp-selbox{position:absolute;border:1.5px solid #fff;pointer-events:none;box-sizing:border-box;display:none;z-index:4}
  .sp-handle{position:absolute;width:12px;height:12px;border-radius:50%;background:#fff;pointer-events:auto;box-shadow:0 0 0 1px rgba(0,0,0,.45)}
  .sp-handle.nw{left:-7px;top:-7px;cursor:nwse-resize}.sp-handle.se{right:-7px;bottom:-7px;cursor:nwse-resize}
  .sp-handle.ne{right:-7px;top:-7px;cursor:nesw-resize}.sp-handle.sw{left:-7px;bottom:-7px;cursor:nesw-resize}
  .sp-rotline{position:absolute;left:50%;bottom:-18px;width:1.5px;height:18px;background:#fff;margin-left:-1px}
  .sp-rot{position:absolute;left:50%;bottom:-40px;margin-left:-11px;width:22px;height:22px;border-radius:50%;background:#fff;color:#111;
    font:15px/22px sans-serif;text-align:center;pointer-events:auto;cursor:grab;box-shadow:0 0 0 1px rgba(0,0,0,.45)}
  .sp-props{width:100%;margin-top:10px;color:#ccc;font:13px sans-serif;display:flex;flex-direction:column;gap:8px}
  .sp-props.sp-off .sp-prow{opacity:.4;pointer-events:none}
  .sp-psel{color:#f5c518;font:12px sans-serif;min-height:30px}
  .sp-prow{display:flex;align-items:center;gap:8px;position:relative}
  .sp-plab{width:62px;color:#999;flex-shrink:0}
  .sp-fontbtn{flex:1;text-align:left;background:#242b33;color:#fff;border:1px solid #444;border-radius:6px;padding:5px 10px;font-size:18px;cursor:pointer}
  .sp-fontlist{position:absolute;left:70px;right:0;bottom:36px;max-height:300px;overflow:auto;background:#1c2229;border:1px solid #444;border-radius:8px;
    z-index:20;box-shadow:0 8px 24px rgba(0,0,0,.5)}
  .sp-fontitem{padding:5px 12px;cursor:pointer;border-bottom:1px solid #2a323b}
  .sp-fontitem:hover{background:#2a323b}.sp-fontitem small{display:block;color:#999;font:11px sans-serif}
  .sp-fontitem div{font-size:22px;color:#fff;line-height:1.35}
  .sp-prow input[type=range]{flex:1;accent-color:#f5c518}
  .sp-prow input[type=color]{width:46px;height:28px;border:1px solid #444;border-radius:6px;background:#242b33;padding:0;cursor:pointer}
  .sp-sec{display:flex;align-items:center;gap:8px;color:#ddd;border-top:1px solid #2a323b;padding-top:8px;margin-top:2px}
  .sp-sty{width:34px;height:30px;background:#242b33;color:#ddd;border:1px solid #444;border-radius:6px;cursor:pointer;font:16px serif}
  .sp-sty.on{background:#f5c518;color:#111;border-color:#f5c518}
  `;

  function mount(id, d) {
    const root = document.getElementById(id);
    if (!root) return;
    if (!document.getElementById("sp-css")) {
      const st = document.createElement("style"); st.id = "sp-css"; st.textContent = CSS; document.head.appendChild(st);
    }
    if (P[id]) P[id].stop = true;                       // старый цикл этого окна — остановить
    root.innerHTML = "";
    const K = d.width / 1080;
    const S = P[id] = Object.assign({id, K, stop: false, sound: false, ev: [], dragging: false}, d);
    const box = document.createElement("div");
    box.className = "sp-box"; box.style.cssText = `width:${d.width}px;height:${Math.round(1920 * K)}px`;
    const mk = () => { const v = document.createElement("video"); v.src = d.src; v.muted = true; v.loop = false;
      v.playsInline = true; v.preload = "auto"; return v; };
    S.bg = mk(); S.bg.style.cssText = `left:0;top:0;width:100%;height:100%;object-fit:cover;filter:blur(14px) brightness(.9) ${GRADE};transform:scale(1.2)`;
    S.clip = document.createElement("div"); S.clip.className = "sp-clip";
    S.fg = mk(); S.fg.style.top = "0"; S.fg.style.filter = GRADE;
    S.clip.appendChild(S.fg);
    S.subs = document.createElement("div"); S.subs.className = "sp-subs";
    const tint = S.tint = document.createElement("div"); tint.className = "sp-tint";
    S.pause = document.createElement("div"); S.pause.className = "sp-pause"; S.pause.textContent = "⏸";
    S.snd = document.createElement("button"); S.snd.className = "sp-btn"; S.snd.textContent = "🔇";
    S.snd.onclick = ev => { ev.stopPropagation(); setSound(S, !S.sound); };
    S.selbox = buildSelBox(S);
    box.append(S.bg, S.clip, tint, S.subs, S.selbox, S.pause, S.snd);
    S.box = box; S.adj = S.adj || {};
    box.onclick = () => togglePlay(S);
    box.oncontextmenu = ev => { ev.preventDefault(); S.sel = null; editVisuals(S); };   // правый клик по пустому месту — снять выделение
    root.append(box);
    S.props = buildProps(S);
    root.append(S.props);
    window.__spActive = S;
    if (window.__spKey) document.removeEventListener("keydown", window.__spKey);
    window.__spKey = ev => {                              // пробел — пауза / воспроизведение
      if (ev.code !== "Space" || ev.ctrlKey || ev.altKey || ev.metaKey) return;
      const t = ev.target, a = window.__spActive;
      if (t && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable)) return;
      if (!a || a.stop || !document.body.contains(a.box) || a.box.offsetParent === null) return;
      if (document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur();   // пробел не должен нажать кнопку
      ev.preventDefault(); togglePlay(a);
    };
    document.addEventListener("keydown", window.__spKey);
    S.audio = null;
    layout(S); buildSubs(S); applyFilter(S); setMusic(S, d.music || ""); buildTimeline(S);
    S.fg.addEventListener("loadedmetadata", () => { if (!S.dur) S.dur = S.fg.duration; seek(S, S.ta); S.fg.play().catch(() => {}); S.bg.play().catch(() => {}); });
    const tick = () => {
      if (S.stop || !document.body.contains(box)) return;
      const t = S.fg.currentTime;
      if (!S.dragging) {
        if (!S.fg.paused && (t >= S.tb - 0.03 || t < S.ta - 0.3)) seek(S, S.ta);
        else if (Math.abs(S.bg.currentTime - t) > 0.2) S.bg.currentTime = t;
      }
      if (S.audio && S.sound && !S.fg.paused && !S.dragging) {                 // музыка идёт за видео, а не сама по себе
        syncAudio(S, t);
        if (S.audio.paused && (t - S.ta) + (S.moff || 0) >= 0) S.audio.play().catch(() => {});
      }
      drawSubs(S, t - S.ta);
      placeSel(S);
      placeTimeline(S, t);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  // ---------- фильтр ролика (приблизительно; точный — в сборке ffmpeg) ----------
  function applyFilter(S) {
    const k = clamp((S.fint === undefined ? 100 : S.fint) / 100, 0, 1), f = S.filter || "Холодный";
    let g = "", tint = "transparent";
    if (f === "Старый Голливуд") {
      g = `grayscale(${0.72 * k}) sepia(${0.55 * k}) contrast(${1 + 0.18 * k}) brightness(${1 - 0.05 * k})`;
      tint = `radial-gradient(ellipse at center, rgba(0,0,0,0) 42%, rgba(0,0,0,${0.7 * k}) 100%)`;   // виньетка
    } else if (f !== "Без фильтра") {
      g = `saturate(${1 - 0.2 * k}) contrast(${1 + 0.06 * k}) brightness(${1 - 0.03 * k}) hue-rotate(${-4 * k}deg)`;
      tint = `rgba(25,50,110,${0.07 * k})`;
    }
    S.fg.style.filter = g;
    S.bg.style.filter = `blur(14px) brightness(.9) ${g}`;
    S.tint.style.background = tint;
  }

  // ---------- таймлиния ----------
  function buildTimeline(S) {
    const host = S.tl && document.getElementById(S.tl);
    if (!host) return;
    host.innerHTML = "";
    const w = S.tlw = document.createElement("div"); w.className = "sp-tl";
    const track = S.track = document.createElement("div"); track.className = "sp-tl-track";
    S.dimL = Object.assign(document.createElement("div"), {className: "sp-tl-dim"});
    S.dimR = Object.assign(document.createElement("div"), {className: "sp-tl-dim"});
    S.act = Object.assign(document.createElement("div"), {className: "sp-tl-act"});
    S.head = Object.assign(document.createElement("div"), {className: "sp-tl-head"});
    track.append(S.act, S.dimL, S.dimR);
    S.words.forEach(wd => { const m = document.createElement("div"); m.className = "sp-tl-word";
      m.style.left = ((wd.s + wd.e) / 2 / S.dur * 100) + "%"; track.appendChild(m); S.wordEls = (S.wordEls || []).concat(m); });
    track.appendChild(S.head);
    track.onpointerdown = ev => { if (ev.target !== S.hA && ev.target !== S.hB) seekAt(S, ev); };
    w.appendChild(track);
    const mrow = S.mrow = document.createElement("div"); mrow.className = "sp-tl-music";
    S.wave = document.createElement("canvas");
    S.mlab = document.createElement("div"); S.mlab.className = "sp-tl-mlab";
    S.mhead = Object.assign(document.createElement("div"), {className: "sp-tl-head"});
    mrow.append(S.wave, S.mlab, S.mhead);
    mrow.onpointerdown = ev => startMusicDrag(S, ev);
    w.appendChild(mrow);
    const step = S.dur <= 40 ? 1 : S.dur <= 120 ? 5 : 10;
    for (let t = 0; t <= S.dur + 1e-6; t += step) {
      const x = t / S.dur * 100;
      const tk = document.createElement("div"); tk.className = "sp-tl-tick"; tk.style.left = x + "%"; w.appendChild(tk);
      if (step >= 5 || t % 2 === 0) { const l = document.createElement("div"); l.className = "sp-tl-tickl"; l.style.left = x + "%";
        l.textContent = tc(S.base + t).replace(/\.0$/, ""); w.appendChild(l); }
    }
    for (const k of ["A", "B"]) {
      const h = S["h" + k] = document.createElement("div"); h.className = "sp-tl-h";
      const lab = S["l" + k] = document.createElement("div"); lab.className = "sp-tl-lab";
      h.onpointerdown = ev => startDrag(S, k, h, ev);
      w.append(h, lab);
    }
    S.info = Object.assign(document.createElement("div"), {className: "sp-tl-info"});
    const hint = Object.assign(document.createElement("div"), {className: "sp-tl-hint",
      textContent: "красные линии — границы ролика; затемнённое — запас, тяни линию, чтобы подтянуть"});
    w.append(S.info, hint);
    host.appendChild(w);
    placeTimeline(S, S.fg.currentTime || 0);
    drawWave(S);
  }

  function placeTimeline(S, t) {
    if (!S.track) return;
    const pa = S.ta / S.dur * 100, pb = S.tb / S.dur * 100;
    S.dimL.style.left = "0"; S.dimL.style.width = pa + "%";
    S.dimR.style.left = pb + "%"; S.dimR.style.right = "0";
    S.act.style.left = pa + "%"; S.act.style.width = (pb - pa) + "%";
    S.hA.style.left = pa + "%"; S.hB.style.left = pb + "%";
    S.lA.style.left = pa + "%"; S.lB.style.left = pb + "%";
    S.lA.textContent = tc(S.base + S.ta); S.lB.textContent = tc(S.base + S.tb);
    S.head.style.left = clamp(t / S.dur * 100, 0, 100) + "%";
    S.mhead.style.left = clamp(t / S.dur * 100, 0, 100) + "%";
    const mo = S.moff || 0;
    S.mlab.textContent = S.audio ? `♪ ${S.musicName}  ·  ` + (mo > 0 ? `в начале играет ${tc(mo)} трека` : mo < 0 ? `музыка стартует через ${(-mo).toFixed(1)} с` : "с начала трека")
      : "без музыки";
    S.info.textContent = `позиция ${tc(clamp(t - S.ta, 0, S.tb - S.ta))} из ${tc(S.tb - S.ta)} · запас слева ${S.ta.toFixed(1)} с, справа ${(S.dur - S.tb).toFixed(1)} с`;
  }

  const timeAt = (S, ev) => {
    const r = S.track.getBoundingClientRect();
    return clamp((ev.clientX - r.left) / r.width, 0, 1) * S.dur;
  };

  function seekAt(S, ev) { seek(S, timeAt(S, ev)); }

  function startDrag(S, k, h, ev) {
    ev.preventDefault(); ev.stopPropagation();
    try { h.setPointerCapture(ev.pointerId); } catch (e) { /* синтетическое событие без активного указателя */ }
    S.dragging = true;
    const wasPlaying = !S.fg.paused;
    S.fg.pause(); S.bg.pause(); if (S.audio) S.audio.pause();
    const move = e2 => {
      const t = Math.round(timeAt(S, e2) * 10) / 10;
      if (k === "A") S.ta = clamp(t, 0, S.tb - MIN); else S.tb = clamp(t, S.ta + MIN, S.dur);
      const at = k === "A" ? S.ta : S.tb;
      S.fg.currentTime = at; S.bg.currentTime = at;         // листаем кадр под линией, как в монтажке
      syncAudio(S, at, true);
      layout(S); buildSubs(S); placeTimeline(S, at);
    };
    const up = () => {
      h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up);
      S.dragging = false;
      seek(S, S.ta);
      if (wasPlaying) {
        S.fg.play().catch(() => {}); S.bg.play().catch(() => {});
        if (S.audio && S.sound) { syncAudio(S, S.ta, true); S.audio.play().catch(() => {}); }   // музыка тоже, не только видео
      }
      if (typeof emitEvent === "function") emitEvent("sasha_trim", {id: S.eid, ta: S.ta, tb: S.tb});
    };
    h.addEventListener("pointermove", move); h.addEventListener("pointerup", up);
  }

  // ---------- дорожка музыки ----------
  function drawWave(S) {
    const cv = S.wave; if (!cv || !S.mrow) return;
    const W = Math.max(1, Math.round(S.mrow.clientWidth)), H = Math.round(S.mrow.clientHeight) || 34;
    if (cv.width !== W) cv.width = W;
    if (cv.height !== H) cv.height = H;
    const g = cv.getContext("2d"); g.clearRect(0, 0, W, H);
    const pk = S.peaks;
    if (!pk || !S.audio) return;
    const tm = S.ta - (S.moff || 0), D = pk.dur, N = pk.peaks.length;   // tm — где на таймлинии начинается трек
    for (let x = 0; x < W; x++) {
      const ft = x / W * S.dur, tt = ft - tm;
      if (tt < 0) continue;
      const h = Math.max(1, pk.peaks[Math.min(N - 1, Math.floor((tt % D) / D * N))] * (H - 8));
      g.fillStyle = ft >= S.ta && ft <= S.tb ? "rgba(110,205,120,.95)" : "rgba(110,205,120,.28)";   // слышно только внутри отрезка
      g.fillRect(x, (H - h) / 2, 1, h);
    }
    const xs = tm / S.dur * W;
    if (xs >= 0 && xs <= W) { g.fillStyle = "#fff"; g.fillRect(Math.round(xs), 0, 2, H); }               // начало трека
  }

  function startMusicDrag(S, ev) {
    if (!S.audio) return;
    ev.preventDefault();
    try { S.mrow.setPointerCapture(ev.pointerId); } catch (e) { /* синтетическое событие */ }
    const x0 = ev.clientX, m0 = S.moff || 0, width = S.mrow.getBoundingClientRect().width;
    const D = S.peaks ? S.peaks.dur : (S.audio.duration || 60), len = S.tb - S.ta;
    S.mrow.style.cursor = "grabbing";
    const move = e2 => {
      const dsec = (e2.clientX - x0) / width * S.dur;                  // вправо — музыка стартует позже
      S.moff = Math.round(clamp(m0 - dsec, -len + 0.5, D - 1) * 10) / 10;
      drawWave(S); placeTimeline(S, S.fg.currentTime); syncAudio(S, S.fg.currentTime, true);
    };
    const up = () => {
      S.mrow.removeEventListener("pointermove", move); S.mrow.removeEventListener("pointerup", up);
      S.mrow.style.cursor = "grab";
      if (typeof emitEvent === "function") emitEvent("sasha_music_off", {id: S.eid, moff: S.moff});
    };
    S.mrow.addEventListener("pointermove", move); S.mrow.addEventListener("pointerup", up);
  }

  // ---------- кадр ----------
  function geom(S) {                                     // как make.py fit()
    const f = S.frame;
    const z = clamp(S.zoom || 1, .3, 4);
    let {W, H, cw, x0} = f;
    if (z > 1) { const c = x0 + cw / 2; cw = Math.max(Math.floor(cw / z / 2) * 2, 64); x0 = clamp(c - cw / 2, 0, W - cw); }
    const fw = Math.round(1080 * Math.min(z, 1) / 2) * 2;
    return {fw, fh: fw * H / cw, s: fw / cw, x0};
  }

  function layout(S) {
    const K = S.K;
    if (S.frame && S.frame.W) {
      const g = geom(S);
      S.clip.style.width = g.fw * K + "px"; S.clip.style.height = g.fh * K + "px";
      Object.assign(S.fg.style, {width: S.frame.W * g.s * K + "px", height: S.frame.H * g.s * K + "px", left: -g.x0 * g.s * K + "px"});
    } else {                                            // лица не нашли — по центру на всю ширину
      S.clip.style.width = S.width + "px"; S.clip.style.height = S.width * 9 / 16 + "px";
      Object.assign(S.fg.style, {width: "100%", height: "100%", left: "0"});
    }
  }

  // ---------- субтитры (как subs.py) ----------
  const adjOf = (S, v) => {
    const a = v.kind === "ladder" ? ((S.adj.ladder || [])[v.i] || {}) : (S.adj.single || {});
    return {dx: a.dx || 0, dy: a.dy || 0, s: a.s || 1, r: a.r || 0, font: a.font, color: a.color, b: a.b, i: a.i, u: a.u,
            o: a.o, oc: a.oc, ow: a.ow, gc: a.gc, gi: a.gi, gr: a.gr};
  };
  const group = (S, v) => {                              // запись правок слова лесенки или общего блока
    if (v.kind === "ladder") {
      S.adj.ladder = S.adj.ladder || [];
      while (S.adj.ladder.length <= v.i) S.adj.ladder.push({});
      return S.adj.ladder[v.i] = S.adj.ladder[v.i] || {};
    }
    return (S.adj.single = S.adj.single || {});
  };
  const members = (S, v) => v.kind === "ladder" ? [v] : S.ev.filter(x => x.kind === "single");

  function place(S, v) {
    const a = adjOf(S, v), K = S.K, c = S.sub, st = v.el.style, fam = a.font || c.font;
    const em = (S.ems && S.ems[fam]) || c.em_ratio || 1;   // ASS-размер — высота строки, в браузере нужен размер буквы
    st.left = (540 + a.dx) * K + "px"; st.top = (v.y + a.dy) * K + "px";
    st.fontSize = v.size * a.s * K * em + "px"; st.fontFamily = `"${fam}"`; st.color = a.color || c.color;
    st.fontWeight = (a.b !== undefined ? a.b : !!c.bold) ? "700" : "400";
    st.fontStyle = (a.i !== undefined ? a.i : !!c.italic) ? "italic" : "normal";
    st.textDecoration = a.u ? "underline" : "none";
    st.transform = `translate(-50%,-50%) rotate(${a.r}deg)`;
    // обводка, сияние, непрозрачность — как панели «Обводка», «Сияние», «Смешивание» в CapCut
    st.webkitTextStroke = `${2 * (a.ow !== undefined ? a.ow : c.outline) * K}px ${a.oc || c.outline_color}`;
    const sh = [];
    if (a.gi > 0) {
      const gr = a.gr !== undefined ? a.gr : 40, rgb = hexRgb(a.gc || "#FFFFFF"), al = clamp(0.12 + a.gi / 100 * 0.88, 0, 1);
      for (let n = 0; n < 2; n++) sh.push(`0 0 ${gr * 0.5 * K * (1 + n)}px rgba(${rgb},${al})`);
    }
    sh.push(`0 ${c.shadow * K}px ${c.shadow * K}px rgba(0,0,0,.5)`);
    st.textShadow = sh.join(",");
    v.op = (a.o !== undefined ? a.o : 100) / 100;
  }

  const hexRgb = h => { h = h.replace("#", ""); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)).join(","); };

  function buildSubs(S) {
    S.subs.innerHTML = ""; S.ev = [];
    if (S.subsOff) return;
    const c = S.sub, K = S.K;
    const ladA = i => (S.adj.ladder || [])[i] || {}, sinA = S.adj.single || {};
    const used = new Set([c.font, ...(S.adj.ladder || []).map(a => a && a.font), sinA.font].filter(Boolean));
    for (const f of used) {                                 // шрифт ещё не загрузился: мерка слов выйдет по запасному — ждём и строим заново
      if (!document.fonts.check(`100px "${f}"`)) document.fonts.load(`100px "${f}"`).then(() => { if (!S.stop && S.sub === c) buildSubs(S); }).catch(() => {});
    }
    const W = S.words.filter(w => (w.s + w.e) / 2 >= S.ta && (w.s + w.e) / 2 <= S.tb)
      .map(w => ({t: w.t, s: Math.max(w.s - S.ta, 0), e: Math.min(w.e - S.ta, S.tb - S.ta)}));
    const shown = x => c.case === "upper" ? x.toUpperCase() : c.case === "lower" ? x.toLowerCase() : x;
    const fit = (txt, size, font) => Math.min(size, Math.floor(100 * c.max_text_width / textW(shown(txt), 100, font || c.font)));
    const add = (txt, s0, e0, y, size, wipe, kind, i) => {
      const el = document.createElement("div"); el.textContent = txt;
      el.style.cssText = `position:absolute;white-space:nowrap;display:none;touch-action:none;` +
        `paint-order:stroke fill;-webkit-text-stroke:${2 * c.outline * K}px ${c.outline_color};text-shadow:0 ${c.shadow * K}px ${c.shadow * K}px rgba(0,0,0,.5)`;
      const v = {el, s: s0, e: e0, wipe, kind, i, y, size, txt};
      el.onpointerdown = ev => editDown(S, v, ev);
      el.onwheel = ev => { ev.preventDefault(); scaleSel(S, v, ev.deltaY < 0 ? 1.05 : 1 / 1.05); };
      el.onclick = ev => ev.stopPropagation();           // клик по слову не должен ставить паузу
      el.oncontextmenu = ev => { ev.preventDefault(); ev.stopPropagation(); selectV(S, v); };   // правая кнопка / два пальца на тачпаде
      el.ondblclick = ev => { ev.stopPropagation(); selectV(S, v); };
      S.subs.appendChild(el); S.ev.push(v); place(S, v);
    };
    S.ladEnd = null;
    const n = c.ladder_words, lad = W.slice(0, n);
    if (lad.length) {
      const lend = lad[lad.length - 1].e + c.ladder_hold;
      S.ladEnd = lend;
      const ls = Math.min(...lad.map((w, i) => fit(w.t, c.ladder_size, ladA(i).font)));
      lad.forEach((w, i) => add(shown(w.t), w.s, lend, c.ladder_y + i * c.ladder_step, ls,
        Math.min(Math.max(w.e - w.s, c.wipe_min), c.wipe_max), "ladder", i));
    }
    const rest = W.slice(n);
    // один размер на все слова после лесенки (single_uniform), как в subs.py
    const usize = c.single_uniform === false || !rest.length ? null
      : Math.min(...rest.map(w => fit(w.t, c.single_size, sinA.font)));
    rest.forEach((w, i) => {
      const nxt = i + 1 < rest.length ? rest[i + 1].s : w.e + 0.3;
      const end = Math.max(w.e, Math.min(nxt, w.e + c.max_gap));
      const start = lad.length && i === 0 ? Math.max(w.s, lad[lad.length - 1].e + c.ladder_hold) : w.s;
      add(shown(w.t), start, end, c.single_y, usize || fit(w.t, c.single_size, sinA.font), 0, "single", i);
    });
    editVisuals(S);
  }

  function drawSubs(S, t) {
    // на паузе в пределах лесенки показываем её целиком — так удобно двигать слова; при воспроизведении всё как в ролике
    const ladderAll = S.fg.paused && S.ladEnd != null && t <= S.ladEnd;
    for (const v of S.ev) {
      const all = ladderAll && v.kind === "ladder";
      const on = all || (t >= v.s && t < v.e);
      v.el.style.display = on ? "block" : "none";
      if (!on) continue;
      const op = v.op === undefined ? 1 : v.op;
      if (v.wipe && !all) { const p = clamp((t - v.s) / v.wipe, 0, 1); v.el.style.clipPath = `inset(-60% ${(1 - p) * 100}% -60% -30%)`; v.el.style.opacity = op; }
      else if (v.wipe) { v.el.style.clipPath = "none"; v.el.style.opacity = op; }
      else { v.el.style.clipPath = "none"; v.el.style.opacity = op * clamp((t - v.s) / (S.sub.fade_in || .1), 0, 1); }
    }
  }

  // ---------- выбор слова, рамка с углами и поворотом ----------
  function selectV(S, v) {                               // выделить слово (правая кнопка, два пальца на тачпаде, двойной клик)
    if (!S.fg.paused) togglePlay(S);
    S.sel = v; editVisuals(S);
  }

  function editVisuals(S) {
    if (S.sel) S.sel = S.ev.find(x => x.kind === S.sel.kind && (x.kind === "single" || x.i === S.sel.i)) || null;   // после пересборки объекты новые
    for (const v of S.ev) { v.el.style.pointerEvents = "auto"; v.el.style.cursor = "move"; }
    syncProps(S); placeSel(S);
  }

  function buildSelBox(S) {
    const sb = document.createElement("div"); sb.className = "sp-selbox";
    for (const pos of ["nw", "ne", "sw", "se"]) {
      const h = document.createElement("div"); h.className = "sp-handle " + pos; h.onpointerdown = ev => startScale(S, ev); sb.appendChild(h);
    }
    const line = document.createElement("div"); line.className = "sp-rotline";
    const rot = document.createElement("div"); rot.className = "sp-rot"; rot.textContent = "⟳"; rot.onpointerdown = ev => startRotate(S, ev);
    sb.append(line, rot);
    sb.oncontextmenu = ev => ev.preventDefault();
    return sb;
  }

  function placeSel(S) {                                 // рамка по слову, которое сейчас на экране
    const sb = S.selbox, v = S.sel;
    const cur = v && S.ev.find(x => x.kind === v.kind && (v.kind === "single" || x.i === v.i) && x.el.style.display === "block");
    if (!cur) { sb.style.display = "none"; return; }
    const e = cur.el, a = adjOf(S, cur);
    sb.style.display = "block";
    sb.style.left = e.style.left; sb.style.top = e.style.top;
    sb.style.width = e.offsetWidth + 16 + "px"; sb.style.height = e.offsetHeight + 8 + "px";
    sb.style.transform = `translate(-50%,-50%) rotate(${a.r}deg)`;
  }

  function startScale(S, ev) {                           // угол рамки: размер — по расстоянию от центра, как в CapCut
    ev.preventDefault(); ev.stopPropagation();
    const v = S.sel; if (!v) return;
    const r = S.selbox.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const d0 = Math.hypot(ev.clientX - cx, ev.clientY - cy) || 1, g = group(S, v), s0 = g.s || 1, h = ev.target;
    try { h.setPointerCapture(ev.pointerId); } catch (e) { /* синтетическое событие */ }
    const move = e2 => {
      g.s = clamp(Math.round(s0 * Math.hypot(e2.clientX - cx, e2.clientY - cy) / d0 * 1000) / 1000, 0.3, 3);
      members(S, v).forEach(x => place(S, x)); syncProps(S);
    };
    const up = () => { h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); emitAdj(S); };
    h.addEventListener("pointermove", move); h.addEventListener("pointerup", up);
  }

  function startRotate(S, ev) {                          // кружок под рамкой: поворот вокруг центра (около нуля — прилипает)
    ev.preventDefault(); ev.stopPropagation();
    const v = S.sel; if (!v) return;
    const r = S.selbox.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const ang0 = Math.atan2(ev.clientY - cy, ev.clientX - cx), g = group(S, v), r0 = g.r || 0, h = ev.target;
    try { h.setPointerCapture(ev.pointerId); } catch (e) { /* синтетическое событие */ }
    const move = e2 => {
      let a = r0 + (Math.atan2(e2.clientY - cy, e2.clientX - cx) - ang0) * 180 / Math.PI;
      a = ((a + 540) % 360) - 180;
      g.r = Math.abs(a) < 3 ? 0 : Math.round(a * 2) / 2;
      members(S, v).forEach(x => place(S, x));
    };
    const up = () => { h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); emitAdj(S); };
    h.addEventListener("pointermove", move); h.addEventListener("pointerup", up);
  }

  // ---------- панель настроек выбранного слова или блока ----------
  function buildProps(S) {
    const wrap = document.createElement("div"); wrap.className = "sp-props";
    const row = label => { const r = document.createElement("div"); r.className = "sp-prow";
      const l = document.createElement("span"); l.className = "sp-plab"; l.textContent = label; r.appendChild(l); wrap.appendChild(r); return r; };
    S.elabel = document.createElement("div"); S.elabel.className = "sp-psel"; wrap.appendChild(S.elabel);
    // шрифт: список, где каждый показан своим начертанием
    const rf = row("Шрифт");
    S.fontbtn = document.createElement("button"); S.fontbtn.className = "sp-fontbtn"; S.fontbtn.tabIndex = -1;
    S.fontlist = document.createElement("div"); S.fontlist.className = "sp-fontlist"; S.fontlist.style.display = "none";
    (S.fonts || []).forEach(f => {
      const it = document.createElement("div"); it.className = "sp-fontitem";
      const nm = document.createElement("small"); nm.textContent = f.name;
      const smp = document.createElement("div"); smp.textContent = "Привет, это проверка 123"; smp.style.fontFamily = `"${f.family}"`;
      it.append(nm, smp);
      it.onclick = () => { S.fontlist.style.display = "none"; applyProp(S, "font", f.family === S.sub.font ? null : f.family); };
      S.fontlist.appendChild(it);
    });
    S.fontbtn.onclick = ev => { ev.stopPropagation(); if (S.sel) S.fontlist.style.display = S.fontlist.style.display === "none" ? "block" : "none"; };
    const closer = e => {
      if (!document.body.contains(S.fontlist)) { document.removeEventListener("pointerdown", closer); return; }
      if (!S.fontlist.contains(e.target) && e.target !== S.fontbtn) S.fontlist.style.display = "none";
    };
    document.addEventListener("pointerdown", closer);
    rf.append(S.fontbtn, S.fontlist);
    // размер
    const rs = row("Размер");
    S.size = document.createElement("input"); S.size.type = "range"; S.size.min = "0.3"; S.size.max = "3"; S.size.step = "0.05";
    S.sizeVal = document.createElement("span"); S.sizeVal.style.cssText = "width:48px;text-align:right";
    S.size.oninput = () => applyProp(S, "s", parseFloat(S.size.value) === 1 ? null : parseFloat(S.size.value), false);
    S.size.onchange = () => emitAdj(S);
    rs.append(S.size, S.sizeVal);
    // стиль
    const ry = row("Узор");
    S.sty = {};
    for (const [k, ch, st] of [["b", "B", "font-weight:700"], ["u", "U", "text-decoration:underline"], ["i", "I", "font-style:italic"]]) {
      const b = S.sty[k] = document.createElement("button"); b.className = "sp-sty"; b.textContent = ch; b.style.cssText = st; b.tabIndex = -1;
      b.onmousedown = e => e.preventDefault();
      b.onclick = () => { if (!S.sel) return; const a = adjOf(S, S.sel); const base = k === "b" ? !!S.sub.bold : k === "i" ? !!S.sub.italic : false;
        applyProp(S, k, !(a[k] !== undefined ? a[k] : base)); };
      ry.appendChild(b);
    }
    // цвет
    const rc = row("Цвет");
    S.colorIn = document.createElement("input"); S.colorIn.type = "color";
    S.colorIn.oninput = () => applyProp(S, "color", S.colorIn.value.toUpperCase(), false);
    S.colorIn.onchange = () => emitAdj(S);
    rc.appendChild(S.colorIn);
    // --- Смешивание, Обводка, Сияние (панели как в CapCut) ---
    S.fx = {};
    const head = (title, onToggle, onReset) => {
      const h = document.createElement("div"); h.className = "sp-sec";
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.tabIndex = -1; cb.style.accentColor = "#f5c518";
      const t = document.createElement("span"); t.textContent = title; t.style.flex = "1";
      const rs = document.createElement("button"); rs.className = "sp-ebtn"; rs.textContent = "↺"; rs.title = "сбросить"; rs.tabIndex = -1;
      rs.onmousedown = e => e.preventDefault(); rs.onclick = onReset;
      if (onToggle) cb.onchange = () => onToggle(cb.checked); else cb.style.display = "none";
      h.append(cb, t, rs); wrap.appendChild(h); return cb;
    };
    const sld = (label, min, max, key) => {
      const r = row(label), inp = document.createElement("input"), val = document.createElement("span");
      inp.type = "range"; inp.min = min; inp.max = max; inp.step = 1; val.style.cssText = "width:48px;text-align:right";
      inp.oninput = () => applyProp(S, key, parseFloat(inp.value), false); inp.onchange = () => emitAdj(S);
      r.append(inp, val); return {inp, val};
    };
    const clr = (label, key) => {
      const r = row(label), inp = document.createElement("input"); inp.type = "color";
      inp.oninput = () => applyProp(S, key, inp.value.toUpperCase(), false); inp.onchange = () => emitAdj(S);
      r.appendChild(inp); return inp;
    };
    const resetKeys = keys => () => { const v = S.sel; if (!v) return; const g = group(S, v); keys.forEach(k => delete g[k]); members(S, v).forEach(x => place(S, x)); syncProps(S); placeSel(S); emitAdj(S); };
    head("Смешивание", null, resetKeys(["o"]));
    S.fx.o = sld("Непрозр.", 0, 100, "o");
    S.fx.obox = head("Обводка", on => applyProp(S, "ow", on ? null : 0), resetKeys(["ow", "oc"]));
    S.fx.oc = clr("Цвет", "oc");
    S.fx.ow = sld("Толщина", 0, 30, "ow");
    S.fx.gbox = head("Сияние", on => {
      const v = S.sel; if (!v) return; const g = group(S, v);
      if (on) { g.gi = g.gi || 50; } else { delete g.gi; delete g.gr; delete g.gc; }
      members(S, v).forEach(x => place(S, x)); syncProps(S); placeSel(S); emitAdj(S);
    }, resetKeys(["gi", "gr", "gc"]));
    S.fx.gc = clr("Цвет", "gc");
    S.fx.gi = sld("Интенсивн.", 0, 100, "gi");
    S.fx.gr = sld("Диапазон", 0, 100, "gr");
    const rr = row("");
    const rb = document.createElement("button"); rb.className = "sp-ebtn"; rb.textContent = "↺ сбросить выбранное"; rb.tabIndex = -1;
    rb.onmousedown = e => e.preventDefault(); rb.onclick = () => S.sel && resetSel(S, S.sel);
    rr.appendChild(rb);
    const hint = document.createElement("div"); hint.style.cssText = "color:#777;font-size:12px";
    hint.textContent = "Выделить слово: клик, правая кнопка мыши или нажатие двумя пальцами на тачпаде. Тянешь слово — двигаешь, угол рамки — размер, " +
      "кружок под рамкой — поворот, колесо мыши — размер. Пробел — пауза. Всё сохраняется сразу.";
    wrap.appendChild(hint);
    return wrap;
  }

  function syncProps(S) {
    if (!S.props) return;
    const v = S.sel;
    S.props.classList.toggle("sp-off", !v);
    if (!v) { S.elabel.textContent = "Субтитры: выдели слово на кадре — тогда здесь появятся его настройки"; return; }
    const a = adjOf(S, v), c = S.sub, fam = a.font || c.font, f = (S.fonts || []).find(x => x.family === fam);
    S.elabel.textContent = v.kind === "ladder" ? `Лесенка · слово ${v.i + 1}: «${v.txt}»` : "Слова после лесенки — настройки сразу для всех";
    S.fontbtn.textContent = (f ? f.name : fam) + " ▾"; S.fontbtn.style.fontFamily = `"${fam}"`;
    S.size.value = a.s; S.sizeVal.textContent = "×" + a.s.toFixed(2);
    S.sty.b.classList.toggle("on", a.b !== undefined ? a.b : !!c.bold);
    S.sty.i.classList.toggle("on", a.i !== undefined ? a.i : !!c.italic);
    S.sty.u.classList.toggle("on", !!a.u);
    S.colorIn.value = (a.color || c.color).toLowerCase();
    const fx = S.fx;
    fx.o.inp.value = a.o !== undefined ? a.o : 100; fx.o.val.textContent = (a.o !== undefined ? a.o : 100) + "%";
    const ow = a.ow !== undefined ? a.ow : c.outline;
    fx.obox.checked = ow > 0; fx.ow.inp.value = ow; fx.ow.val.textContent = ow;
    fx.oc.value = (a.oc || c.outline_color).toLowerCase();
    const gi = a.gi || 0, gr = a.gr !== undefined ? a.gr : 40;
    fx.gbox.checked = gi > 0; fx.gi.inp.value = gi; fx.gi.val.textContent = gi; fx.gr.inp.value = gr; fx.gr.val.textContent = gr;
    fx.gc.value = (a.gc || "#FFFFFF").toLowerCase();
  }

  function applyProp(S, key, val, emit = true) {         // изменить настройку выбранного слова / блока; null — вернуть как в стиле
    const v = S.sel; if (!v) return;
    const g = group(S, v);
    if (key === "o" && val === 100) val = null;                                                     // значения «как в стиле» не храним
    if (key === "ow" && val === S.sub.outline) val = null;
    if (key === "oc" && val && val.toLowerCase() === String(S.sub.outline_color).toLowerCase()) val = null;
    if (key === "gi" && !val) val = null;
    if (val === null || val === undefined) delete g[key]; else g[key] = val;
    if (key === "font") buildSubs(S); else members(S, v).forEach(x => place(S, x));
    syncProps(S); placeSel(S);
    if (emit) emitAdj(S);
  }

  function emitAdj(S) {
    if (typeof emitEvent !== "function") return;
    emitEvent("sasha_adj", {id: S.eid, adj: {ladder: (S.adj.ladder || []).map(a => a || {}), single: S.adj.single || {}}});
  }

  function editDown(S, v, ev) {
    if (ev.button !== 0) return;                          // правая кнопка только выделяет (contextmenu), не тянет
    ev.preventDefault(); ev.stopPropagation();
    if (!S.fg.paused) togglePlay(S);                      // тянем на замершем кадре
    S.sel = v; editVisuals(S);
    try { v.el.setPointerCapture(ev.pointerId); } catch (e) { /* синтетическое событие */ }
    const g = group(S, v), x0 = ev.clientX, y0 = ev.clientY, a0 = {dx: g.dx || 0, dy: g.dy || 0};
    const move = e2 => {
      g.dx = Math.round(a0.dx + (e2.clientX - x0) / S.K); g.dy = Math.round(a0.dy + (e2.clientY - y0) / S.K);
      members(S, v).forEach(x => place(S, x)); placeSel(S);
    };
    const up = () => { v.el.removeEventListener("pointermove", move); v.el.removeEventListener("pointerup", up); emitAdj(S); };
    v.el.addEventListener("pointermove", move); v.el.addEventListener("pointerup", up);
  }

  function scaleSel(S, v, f) {
    const g = group(S, v);
    g.s = clamp(Math.round((g.s || 1) * f * 1000) / 1000, 0.3, 3);
    members(S, v).forEach(x => place(S, x));
    S.sel = v; editVisuals(S); emitAdj(S);
  }

  function resetSel(S, v) {
    const g = group(S, v);
    for (const k of ["dx", "dy", "s", "r", "font", "color", "b", "i", "u", "o", "oc", "ow", "gc", "gi", "gr"]) delete g[k];
    buildSubs(S); emitAdj(S);
  }

  // ---------- воспроизведение ----------
  // музыка в ролике начинается вместе с отрезком, поэтому её позиция = позиция в отрезке (по кругу, если трек короче)
  function syncAudio(S, t, force) {
    const a = S.audio; if (!a) return;
    const rel = (t - S.ta) + (S.moff || 0), d = a.duration;   // moff < 0 — музыка ещё не началась
    if (rel < 0) { a.pause(); try { a.currentTime = 0; } catch (e) { /* ещё не загрузилась */ } return; }
    const want = isFinite(d) && d > 0 ? rel % d : rel;
    if (force || Math.abs(a.currentTime - want) > 0.3) { try { a.currentTime = want; } catch (e) { /* ещё не загрузилась */ } }
  }

  function seek(S, t) {
    t = clamp(t, S.ta, S.tb);
    S.fg.currentTime = t; S.bg.currentTime = t;
    syncAudio(S, t, true);
  }

  function togglePlay(S) {
    const pause = !S.fg.paused;
    for (const v of [S.fg, S.bg]) pause ? v.pause() : v.play().catch(() => {});
    if (S.audio) { if (pause) S.audio.pause(); else if (S.sound) { syncAudio(S, S.fg.currentTime, true); S.audio.play().catch(() => {}); } }
    S.pause.style.display = pause ? "block" : "none";
  }

  function setSound(S, on) {
    S.sound = on; S.snd.textContent = on ? "🔊" : "🔇"; S.fg.muted = !on;
    if (S.audio) { if (on && !S.fg.paused) { syncAudio(S, S.fg.currentTime, true); S.audio.play().catch(() => {}); } else S.audio.pause(); }
  }

  const musicVol = S => clamp(0.3 * (S.mvol === undefined ? 100 : S.mvol) / 100, 0, 1);   // 100 % ≈ как в ролике (тише голоса)

  function setMusic(S, url) {
    if (S.audio) { S.audio.pause(); S.audio = null; }
    S.musicUrl = url; S.peaks = null;
    S.musicName = url ? decodeURIComponent(url.split("/").pop()) : "";
    if (url) fetch(url.replace("/sasha-music/", "/sasha-wave/")).then(r => r.json()).then(j => { S.peaks = j; drawWave(S); }).catch(() => {});
    drawWave(S);
    if (!url) return;
    S.audio = new Audio(url); S.audio.loop = true; S.audio.volume = musicVol(S);
    syncAudio(S, S.fg.currentTime || S.ta, true);
    if (S.sound && !S.fg.paused) S.audio.play().catch(() => {});
  }

  function update(id, patch) {
    const S = P[id]; if (!S) return;
    const old = [S.ta, S.tb];
    Object.assign(S, patch);
    if ("music" in patch) setMusic(S, patch.music || "");
    if ("filter" in patch || "fint" in patch) applyFilter(S);
    if ("moff" in patch) { drawWave(S); syncAudio(S, S.fg.currentTime, true); }
    if ("mvol" in patch && S.audio) S.audio.volume = musicVol(S);
    layout(S);
    if ("words" in patch || "ta" in patch || "tb" in patch || "sub" in patch || "subsOff" in patch || "adj" in patch) buildSubs(S);
    if ("words" in patch && S.track) { S.track.querySelectorAll(".sp-tl-word").forEach(e => e.remove());
      S.words.forEach(wd => { const m = document.createElement("div"); m.className = "sp-tl-word";
        m.style.left = ((wd.s + wd.e) / 2 / S.dur * 100) + "%"; S.track.insertBefore(m, S.head); }); }
    if ("ta" in patch || "tb" in patch) {
      const t = S.fg.currentTime;
      if ((patch.ta !== old[0] || patch.tb !== old[1]) && (t < S.ta || t > S.tb)) seek(S, S.ta);
    }
    placeTimeline(S, S.fg.currentTime);
  }

  return {mount, update, _state: P};   // _state — для проверок из консоли
})();
