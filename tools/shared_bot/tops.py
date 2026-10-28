"""Подборки «топ-N моментов» — логика без экранов (27.10.2026). Берёт готовые чистые клипы стримера и собирает из них
максимум роликов «ТОП 3 / ТОП 5 …», где любые два ролика делят не больше `shared` общих моментов.
Оформление — как одобренный пример (tools/top3/build.py, 24.10.2026): плашка-заголовок, список 3→1 внизу плашки
(название появляется, когда место показано), размытый фон, выравнивание громкости. Показ идёт с последнего места к первому.
"""
import itertools
import json
import math
import random
import time
from pathlib import Path

import core

W, H = 1080, 1920
YELLOW, PLATE = (236, 222, 30, 255), (28, 26, 30, 225)   # как CR_YELLOW / CR_PLATE в core
ROW = 74                                                  # шаг строки списка
MAX_CLIPS = 24                                            # больше — перебор не успевает


def limit(n, k, shared):
    """Верхняя граница числа роликов (граница Джонсона): каждый момент — не больше cap роликов."""
    cap = math.comb(n - 1, shared) // math.comb(k - 1, shared)
    return n * cap // k, cap


def plan(n, k, shared, seconds=6, seed=0):
    """→ (список роликов, граница). Ролик = кортеж из k номеров клипов в порядке ПОКАЗА (с последнего места к первому).
    Условие: у двух роликов общих клипов не больше `shared`; места каждого клипа распределены как можно ровнее."""
    if n < k:
        return [], 0
    bound, cap = limit(n, k, shared)
    subsets = list(itertools.combinations(range(n), k))
    deadline = time.monotonic() + seconds
    best = []
    rnd = random.Random(seed)
    while time.monotonic() < deadline and len(best) < bound:
        used, deg, res = set(), [0] * n, []
        balanced = rnd.random() < 0.5   # половина попыток — по наименее использованным клипам, половина — случайно
        rnd.shuffle(subsets)
        # жадно: сначала те наборы, где клипы меньше всего использованы (так загрузка выходит ровной)
        pending = subsets
        while True:
            pick = None
            low = None
            for t in pending:
                if any(deg[i] >= cap for i in t):
                    continue
                if any(p in used for p in itertools.combinations(t, shared + 1)):
                    continue
                s = sum(deg[i] for i in t) if balanced else 0
                if not balanced:
                    pick = t
                    break
                if low is None or s < low:
                    pick, low = t, s
                    if s == 0:
                        break
            if pick is None:
                break
            res.append(pick)
            used.update(itertools.combinations(pick, shared + 1))
            for i in pick:
                deg[i] += 1
        if len(res) > len(best):
            best = res
    return place(best, n, k, rnd), bound


def place(sets, n, k, rnd):
    """Порядок показа внутри каждого ролика: перебором ищем, где у каждого клипа места распределены ровнее всего."""
    if not sets:
        return []
    best = None
    for _ in range(4000):
        orders = [tuple(rnd.sample(t, k)) for t in sets]
        cnt = [[0] * k for _ in range(n)]
        for o in orders:
            for pos, i in enumerate(o):
                cnt[i][pos] += 1
        score = sum(max(c) - min(c) for c in cnt)
        if best is None or score < best[0]:
            best = (score, orders)
            if score <= n // 2:
                break
    out = best[1]
    rnd.shuffle(out)   # чтобы подряд не шли похожие
    return [tuple(o) for o in out]


def title_lines(text, font, draw, maxw):
    """Заголовок плашки: влезает в строку — одна строка, иначе делим по словам на две примерно равные."""
    text = " ".join(text.upper().split())
    if draw.textlength(text, font=font) <= maxw:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: abs(draw.textlength(" ".join(words[:i]), font=font)
                                                       - draw.textlength(" ".join(words[i:]), font=font)),
               default=0) if len(words) > 1 else 0
    return [" ".join(words[:best]), " ".join(words[best:])] if best else [text]


def layer(title, k, names, path):
    """Прозрачный слой 1080×1920: плашка + список 1…k (названия только у уже показанных мест). → низ списка."""
    Image, ImageDraw, ImageFont = core.pillow()
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(str(core.CR_FONT), 65)
    asc, desc = f.getmetrics()
    lh = round(65 * 1.22)
    lines = title_lines(title, f, d, W - 200)
    pw = max(d.textlength(l, font=f) for l in lines) + 80
    ph = asc + desc + (len(lines) - 1) * lh + 72
    x0, y0 = (W - pw) / 2, 70
    d.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=22, fill=PLATE)
    for i, l in enumerate(lines):
        d.text(((W - d.textlength(l, font=f)) / 2, y0 + 36 + i * lh), l, font=f, fill=YELLOW)
    y = y0 + ph + 34
    for n in range(1, k + 1):
        t, size = f"{n}. " + names.get(n, ""), 56
        while d.textlength(t, font=ImageFont.truetype(str(core.CR_FONT), size)) > W - 140 and size > 36:
            size -= 2   # длинное название уменьшаем, чтобы влезло в строку
        d.text((70, y + (56 - size) // 2), t, font=ImageFont.truetype(str(core.CR_FONT), size), fill=YELLOW,
               stroke_width=5, stroke_fill=(0, 0, 0, 255))
        y += ROW
    im.save(path)
    return y + 30


def build_one(clips, order, title, out, work):
    """clips — [{file, name}], order — номера клипов в порядке показа (последнее место первым). → путь ролика."""
    k = len(order)
    work.mkdir(parents=True, exist_ok=True)
    segs, names = [], {}
    for step, ci in enumerate(order):
        place_no = k - step
        names[place_no] = clips[ci]["name"].upper()
        png = work / f"l{step}.png"
        top = layer(title, k, names, png)
        bh = H - top - 40
        fc = (f"[0:v]fps=30,split[a][b];"
              f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
              f"boxblur=10:2,scale={W}:{H},eq=brightness=-0.06,setsar=1[bg];"
              f"[b]scale={W}:{bh}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fg];"
              f"[bg][fg]overlay=(W-w)/2:{top}+({bh}-h)/2[v0];[v0][1:v]overlay=0:0[v];"
              f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
        seg = work / f"s{step}.mp4"
        r = core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", clips[ci]["file"], "-i", png,
                      "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
                      "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", seg],
                     1800)
        if r.returncode != 0 or not seg.exists():
            core.log.error("ffmpeg подборка: %s", r.stderr[-2000:])
            raise core.JobError(f"не собрал «{clips[ci]['name']}»")
        segs.append(seg)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    r = core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                  "-c", "copy", "-movflags", "+faststart", out], 600)
    if r.returncode != 0 or not Path(out).exists():
        core.log.error("ffmpeg склейка подборки: %s", r.stderr[-2000:])
        raise core.JobError("не склеил ролик подборки")
    for s in segs:
        s.unlink(missing_ok=True)
    return out


def write_list(out_dir, rows):
    Path(out_dir, "список.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
