// Редактор нарезки (экран «Нарезка», 27.10.2026): видео целиком, рамка куска (тянуть за края и углы, двигать
// за середину), под видео — таймлайн с разрезами (отметки двигаются мышью), справа — каждый кусок как он выйдет.
// Данные: {src, w, h, duration, segs: [{start, end, crop: [x, y, w, h], name, skip}]}. «Отправить» → emitEvent.
window.SplitEditor = (() => {
  let S = null, root, video, stage, box, bar, list, info, cur = -1, priming = false;
  const MIN = 0.5, MINPX = 40;
  const tc = t => { const m = Math.floor(t / 60), s = (t - m * 60).toFixed(1); return `${m}:${s.padStart(4, "0")}`; };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const CSS = `
  .se-wrap{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;color:#ddd;font-size:13px}
  .se-stage{position:relative;display:inline-block;background:#000;overflow:hidden;border-radius:8px;user-select:none}
  .se-video{display:block;max-height:62vh;max-width:min(100%,860px)}
  .se-box{position:absolute;border:2px solid #f5c518;box-shadow:0 0 0 9999px rgba(0,0,0,.6);cursor:move}
  .se-box.skip{border-color:#888}
  .se-h{position:absolute;width:14px;height:14px;background:#f5c518;border-radius:3px}
  .se-h.e,.se-h.w{top:50%;margin-top:-7px;cursor:ew-resize}.se-h.n,.se-h.s{left:50%;margin-left:-7px;cursor:ns-resize}
  .se-h.n{top:-8px}.se-h.s{bottom:-8px}.se-h.e{right:-8px}.se-h.w{left:-8px}
  .se-h.nw{left:-8px;top:-8px;cursor:nwse-resize}.se-h.se{right:-8px;bottom:-8px;cursor:nwse-resize}
  .se-h.ne{right:-8px;top:-8px;cursor:nesw-resize}.se-h.sw{left:-8px;bottom:-8px;cursor:nesw-resize}
  .se-bar{position:relative;height:38px;margin-top:10px;background:#1d2329;border-radius:6px;cursor:pointer}
  .se-seg{position:absolute;top:4px;bottom:4px;background:rgba(245,197,24,.35);border-radius:4px;
    display:flex;align-items:center;justify-content:center;font-weight:700;color:#111;overflow:hidden}
  .se-seg.cur{background:rgba(245,197,24,.85)}
  .se-seg.skip{background:repeating-linear-gradient(45deg,#444 0 6px,#333 6px 12px);color:#aaa}
  .se-mark{position:absolute;top:-4px;bottom:-4px;width:10px;margin-left:-5px;cursor:ew-resize;z-index:2}
  .se-mark::after{content:"";position:absolute;left:4px;top:0;bottom:0;width:2px;background:#fff}
  .se-keys kbd{background:#232a31;border:1px solid #3a434c;border-radius:3px;padding:0 5px;font:inherit;color:#c3cbd2}
  .se-play{position:absolute;top:-6px;bottom:-6px;width:2px;background:#ff4d4d;pointer-events:none;z-index:3}
  .se-tools{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  .se-tools button{background:#232a31;color:#eee;border:1px solid #3a434c;border-radius:6px;padding:5px 10px;cursor:pointer}
  .se-tools button:hover{border-color:#f5c518}
  .se-list{flex:1;min-width:260px;display:flex;flex-wrap:wrap;gap:12px;align-content:flex-start}
  .se-card{background:#1a1f24;border:2px solid #2b333b;border-radius:8px;padding:6px;width:150px}
  .se-card.cur{border-color:#f5c518}.se-card.skip{opacity:.45}
  .se-card canvas{display:block;width:100%;background:#000;border-radius:4px;cursor:pointer}
  .se-card input[type=text]{width:100%;margin-top:4px;background:#111;color:#f5c518;border:1px solid #3a434c;
    border-radius:4px;padding:3px 5px;font-weight:700;text-transform:uppercase}`;

  function mount(id, data) {
    S = data; cur = -1; history = [];
    root = document.getElementById(id);
    if (!root) return;
    if (!document.getElementById("se-css")) {
      const st = document.createElement("style"); st.id = "se-css"; st.textContent = CSS; document.head.appendChild(st);
    }
    root.innerHTML = `<div class="se-wrap"><div style="flex:0 0 auto;max-width:100%">
      <div class="se-stage"><video class="se-video" preload="auto" playsinline></video>
        <div class="se-box">${["n","s","e","w","nw","ne","sw","se"].map(h => `<div class="se-h ${h}" data-h="${h}"></div>`).join("")}</div></div>
      <div class="se-bar"></div>
      <div class="se-tools">
        <button data-a="play">▶ / ⏸ <small>(пробел)</small></button><button data-a="split">✂ Разрезать здесь <small>(Q)</small></button>
        <button data-a="merge">⇥ Склеить со следующим <small>(E)</small></button><button data-a="skip">🚫 Не брать / брать <small>(Del)</small></button>
        <button data-a="alt">⟲ Другая рамка <small>(R)</small></button><button data-a="full">⛶ Весь кадр <small>(F)</small></button><button data-a="all">⧉ Эту рамку всем</button>
        <button data-a="undo">↶ Отменить <small>(Ctrl+Z)</small></button></div>
      <div class="se-info" style="margin-top:6px;color:#9aa"></div>
      <div class="se-keys" style="margin-top:14px;color:#7d8791;font-size:11px;line-height:1.8">
        <b style="color:#9aa4ad">Горячие клавиши:</b> <kbd>Пробел</kbd> пуск / пауза · <kbd>←</kbd> <kbd>→</kbd> кадр назад / вперёд ·
        <kbd>Shift</kbd>+<kbd>←</kbd> <kbd>→</kbd> секунда · <kbd>Q</kbd> (Й) разрезать здесь · <kbd>E</kbd> (У) склеить со следующим ·
        <kbd>R</kbd> (К) другая рамка · <kbd>F</kbd> (А) весь кадр · <kbd>Del</kbd> не брать / брать · <kbd>Ctrl</kbd>+<kbd>Z</kbd> (Я) отменить последнее изменение ·
        <kbd>Ctrl</kbd>+<kbd>Enter</kbd> отправить · <kbd>E</kbd> перед вырезанной рекламой склеивает куски по обе её стороны · работают и в русской раскладке</div></div>
      <div class="se-list"></div></div>`;
    video = root.querySelector(".se-video"); stage = root.querySelector(".se-stage"); box = root.querySelector(".se-box");
    bar = root.querySelector(".se-bar"); list = root.querySelector(".se-list"); info = root.querySelector(".se-info");
    video.src = S.src;
    video.addEventListener("loadedmetadata", () => { layout(); prime(); });
    video.addEventListener("timeupdate", tick);
    video.addEventListener("seeked", tick);
    window.addEventListener("resize", layout);
    root.querySelector(".se-tools").addEventListener("click", e => { const a = e.target.dataset.a; if (a) { tool(a); e.target.blur(); } });
    if (window.__seKey) { window.removeEventListener("keydown", window.__seKey, true); window.removeEventListener("keyup", window.__seKey, true); }
    window.__seKey = keys;
    window.addEventListener("keydown", keys, true); window.addEventListener("keyup", keys, true);
    box.addEventListener("pointerdown", dragBox);
    bar.addEventListener("pointerdown", e => {
      if (e.target.classList.contains("se-mark")) return dragMark(e);
      const r = bar.getBoundingClientRect(); video.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * S.duration;
    });
    renderList(); renderBar();
  }

  const scale = () => video.clientWidth / S.w;
  const segAt = t => { const i = S.segs.findIndex(s => t >= s.start && t < s.end); return i < 0 ? S.segs.length - 1 : i; };


  // Горячие клавиши (по положению клавиши, поэтому работают и в русской раскладке):
  // Ctrl+Enter — отправить · Пробел — пуск/пауза · ← → — кадр (Shift — секунда) · Del — не брать · Q (Й) — разрезать · E (У) — склеить со следующим
  function keys(e) {
    if (!S || !video || !document.getElementById(root.id)) return;
    if ((e.ctrlKey || e.metaKey) && e.code === "Enter") {   // Ctrl+Enter — «Отправить» (работает и в поле имени куска)
      e.preventDefault(); if (e.type === "keydown" && !e.repeat) submit(); return;
    }
    const t = e.target, tag = (t.tagName || "").toLowerCase();
    if (tag === "input" && t.type !== "checkbox" || tag === "textarea" || tag === "select" || t.isContentEditable) return;
    if ((e.ctrlKey || e.metaKey) && e.code === "KeyZ" && !e.shiftKey && !e.altKey) {   // Ctrl+Z (и Ctrl+Я) — отменить
      e.preventDefault(); if (e.type === "keydown") undo(); return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const fps = S.fps || 30;
    if (e.code === "Space") { e.preventDefault(); if (e.type === "keydown") tool("play"); return; }
    if (e.type !== "keydown") return;
    if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
      e.preventDefault(); video.pause();
      const d = (e.shiftKey ? 1 : 1 / fps) * (e.code === "ArrowLeft" ? -1 : 1);
      video.currentTime = clamp(video.currentTime + d, 0, S.duration);
    } else if (e.code === "Delete") { e.preventDefault(); tool("skip"); }
    else if (e.code === "KeyQ") { e.preventDefault(); tool("split"); }
    else if (e.code === "KeyE") { e.preventDefault(); tool("merge"); }
    else if (e.code === "KeyR") { e.preventDefault(); tool("alt"); }
    else if (e.code === "KeyF") { e.preventDefault(); tool("full"); }
  }

  function layout() { renderBox(); renderBar(); }

  function tick() {
    const i = segAt(video.currentTime);
    if (i !== cur) { cur = i; renderBox(); renderList(); }
    renderBar();
    if (!priming) draw(cur);
  }

  function renderBox() {
    if (cur < 0 || !S.segs[cur]) { box.style.display = "none"; return; }
    const [x, y, w, h] = S.segs[cur].crop, k = scale();
    Object.assign(box.style, {display: "block", left: x * k + "px", top: y * k + "px", width: w * k + "px", height: h * k + "px"});
    box.classList.toggle("skip", !!S.segs[cur].skip);
    const s = S.segs[cur];
    info.textContent = `Кусок ${cur + 1} из ${S.segs.length}: ${tc(s.start)}–${tc(s.end)} (${(s.end - s.start).toFixed(1)} с) · рамка ${w}×${h}` +
      (s.skip ? " · НЕ БРАТЬ" : "");
  }

  function renderBar() {
    if (!S) return;
    const d = S.duration;
    let html = S.segs.map((s, i) => `<div class="se-seg${i === cur ? " cur" : ""}${s.skip ? " skip" : ""}"
      style="left:${s.start / d * 100}%;width:${(s.end - s.start) / d * 100}%">${i + 1}</div>`).join("");
    html += S.segs.slice(0, -1).map((s, i) => `<div class="se-mark" data-i="${i}" style="left:${s.end / d * 100}%"></div>`).join("");
    html += `<div class="se-play" style="left:${(video ? video.currentTime : 0) / d * 100}%"></div>`;
    bar.innerHTML = html;
  }

  function renderList() {
    list.innerHTML = "";
    S.segs.forEach((s, i) => {
      const c = document.createElement("div");
      c.className = "se-card" + (i === cur ? " cur" : "") + (s.skip ? " skip" : "");
      c.innerHTML = `<canvas></canvas><div style="margin-top:4px">${i + 1} · ${tc(s.start)}–${tc(s.end)}</div>
        <input type="text" placeholder="название" value="${(s.name || "").replace(/"/g, "&quot;")}">
        ${s.said ? `<div title="${s.said.replace(/"/g, "&quot;")}" style="margin-top:3px;font-size:11px;color:#8a949c;max-height:3.9em;overflow:hidden">«${s.said}»</div>` : ""}
        <label style="display:flex;gap:4px;align-items:center;margin-top:3px"><input type="checkbox" data-k="take" ${s.skip ? "" : "checked"}> брать</label>${i > 0 ? `<label style="display:flex;gap:4px;align-items:center;margin-top:2px" title="Клип продолжает предыдущий кусок (реклама между ними вырезана)"><input type="checkbox" data-k="join" ${s.join ? "checked" : ""}> 🔗 склеить с прошлым</label>` : ""}`;
      const cv = c.querySelector("canvas");
      cv.width = 150; cv.height = Math.min(266, Math.round(150 * s.crop[3] / s.crop[2]));
      if (s._img) cv.getContext("2d").putImageData(s._img, 0, 0);
      cv.onclick = () => { video.currentTime = s.start + 0.05; };
      c.querySelector("input[type=text]").oninput = e => { s.name = e.target.value; };
      c.querySelector("input[data-k=take]").onchange = e => { snap(); s.skip = !e.target.checked; renderBox(); renderBar(); c.classList.toggle("skip", s.skip); };
      const jn = c.querySelector("input[data-k=join]"); if (jn) jn.onchange = e => { snap(); s.join = e.target.checked; };
      list.appendChild(c);
    });
  }

  function draw(i) {   // кадр куска с его рамкой → карточка справа
    const s = S.segs[i], cv = list.children[i] && list.children[i].querySelector("canvas");
    if (!s || !cv || video.readyState < 2) return;
    const want = Math.min(266, Math.round(150 * s.crop[3] / s.crop[2]));
    if (cv.height !== want) cv.height = want;
    const ctx = cv.getContext("2d");
    ctx.drawImage(video, s.crop[0], s.crop[1], s.crop[2], s.crop[3], 0, 0, cv.width, cv.height);
    try { s._img = ctx.getImageData(0, 0, cv.width, cv.height); } catch (e) { /* без кэша */ }
  }

  function prime() {   // пробежать по серединам кусков, чтобы у каждой карточки была картинка
    priming = true;
    const q = S.segs.map((s, i) => i);
    const next = () => {
      if (!q.length) { video.removeEventListener("seeked", on); priming = false; video.currentTime = 0; return; }
      const i = q[0]; video.currentTime = (S.segs[i].start + S.segs[i].end) / 2;
    };
    const on = () => { draw(q.shift()); next(); };
    video.addEventListener("seeked", on);
    next();
  }

  function dragBox(e) {
    if (cur < 0) return;
    e.preventDefault(); e.stopPropagation(); snap();
    const h = e.target.dataset.h || "move", s = S.segs[cur], k = scale(), sx = e.clientX, sy = e.clientY;
    const [x0, y0, w0, h0] = s.crop;
    const move = ev => {
      const dx = (ev.clientX - sx) / k, dy = (ev.clientY - sy) / k;
      let x1 = x0, y1 = y0, x2 = x0 + w0, y2 = y0 + h0;
      if (h === "move") {
        x1 = clamp(x0 + dx, 0, S.w - w0); y1 = clamp(y0 + dy, 0, S.h - h0); x2 = x1 + w0; y2 = y1 + h0;
      } else {
        if (h.includes("w")) x1 = clamp(x0 + dx, 0, x2 - MINPX);
        if (h.includes("e")) x2 = clamp(x0 + w0 + dx, x1 + MINPX, S.w);
        if (h.includes("n")) y1 = clamp(y0 + dy, 0, y2 - MINPX);
        if (h.includes("s")) y2 = clamp(y0 + h0 + dy, y1 + MINPX, S.h);
      }
      const ev2 = v => Math.round(v / 2) * 2;
      s.crop = [ev2(x1), ev2(y1), ev2(x2 - x1), ev2(y2 - y1)];
      renderBox(); draw(cur);
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }

  function dragMark(e) {
    e.preventDefault(); snap();
    const i = +e.target.dataset.i, r = bar.getBoundingClientRect();
    const move = ev => {
      const t = clamp((ev.clientX - r.left) / r.width * S.duration, S.segs[i].start + MIN, S.segs[i + 1].end - MIN);
      S.segs[i].end = S.segs[i + 1].start = Math.round(t * 100) / 100;
      video.currentTime = t; renderBar();
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); renderList(); renderBox(); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }

  // История для «Отменить» (кнопка и Ctrl+Z / Ctrl+Я): снимок кусков перед каждым изменением, до 60 шагов.
  let history = [];
  function snap() {
    history.push(S.segs.map(g => ({...g, crop: [...g.crop], alts: g.alts && g.alts.map(a => [...a])})));
    if (history.length > 60) history.shift();
  }
  function undo() {
    if (!history.length) return;
    S.segs = history.pop(); cur = -1;
    tick(); renderList(); renderBar();
  }

  function tool(a) {
    const s = S.segs[cur], t = Math.round(video.currentTime * 100) / 100;
    if (a === "play") { video.paused ? video.play() : video.pause(); return; }
    if (a === "undo") { undo(); return; }
    if (!s) return;
    if (a === "split") {
      if (t - s.start < MIN || s.end - t < MIN) return;
      snap();
      S.segs.splice(cur + 1, 0, {start: t, end: s.end, crop: [...s.crop], name: "", skip: s.skip});
      s.end = t;
    } else if (a === "merge") {
      if (cur >= S.segs.length - 1) return;
      const nx = S.segs[cur + 1], after = S.segs[cur + 2];
      snap();
      if (nx.skip && after && !s.skip) {   // дальше вырезанная реклама: склеить куски по обе стороны от неё, без неё
        after.join = true; after.skip = false; after.crop = [...s.crop]; after._img = null;
        if (!after.name) after.name = "";
      } else {
        s.end = nx.end; S.segs.splice(cur + 1, 1);
      }
    } else if (a === "skip") { snap(); s.skip = !s.skip; }
    else if (a === "full") { snap(); s.crop = [0, 0, S.w, S.h]; }
    else if (a === "alt") {   // следующий вариант рамки, найденный автоматикой
      const al = s.alts || []; if (al.length < 2) return;
      snap();
      const i = al.findIndex(x => x.join() === s.crop.join());
      s.crop = [...al[(i + 1) % al.length]]; s._img = null; renderBox(); draw(cur); return;
    }
    else if (a === "all") { snap(); S.segs.forEach(o => { o.crop = [...s.crop]; o._img = null; }); }
    cur = -1; tick(); renderList();
  }

  function submit() {
    if (!S) return;
    emitEvent("split_submit", S.segs.map(s => ({start: s.start, end: s.end, crop: s.crop, name: s.name || "", skip: !!s.skip, join: !!s.join, said: s.said || "", alts: s.alts || []})));
  }

  return {mount, submit};
})();
