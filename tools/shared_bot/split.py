"""Нарезка исходника на моменты для топ-3/5 (27.10.2026). Автоматика предлагает, человек правит на экране «Нарезка».

  analyze(path) → {w, h, duration, segs: [{start, end, crop: [x, y, w, h]}]}
    границы — смены сцены (ffmpeg scene), рамка у каждого куска — резкая область внутри размытого фона:
    по строкам/столбцам усредняем резкость (Лапласиан) по кадрам куска и прижимаем края к сильным прямым линиям.
  cut(path, seg, out) — кусок по времени + рамка → mp4 (как tools/top3/cut.py).
Без OpenCV (в наборе его может не быть) рамка = весь кадр, границы всё равно ищутся.
"""
import json
import re
import sys

import core

SCENE = 0.25        # порог смены сцены ffmpeg
MIN_SEG = 1.5       # куски короче — приклеиваем к соседнему
SAMPLES = 12        # кадров на кусок для рамки
SHARP = 0.35        # строка «резкая», если её резкость ≥ этой доли от медианы резких строк
SNAP = 40           # край рамки прижимаем к прямой линии не дальше стольких пикселей


def scenes(path):
    r = core.run([core.FFMPEG, "-hide_banner", "-i", path, "-vf", f"select='gt(scene,{SCENE})',showinfo", "-an",
                  "-f", "null", "-"], 900)
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def bounds(times, duration):
    cuts = [0.0]
    for t in sorted(times):
        if t - cuts[-1] >= MIN_SEG and duration - t >= MIN_SEG:
            cuts.append(round(t, 2))
    return list(zip(cuts, cuts[1:] + [round(duration, 2)]))


def _range(sharp, edge, size):
    """Профиль резкости по строкам (или столбцам) → (от, до). Резкая область — самый длинный кусок «резких» строк;
    края прижимаем к ближайшей сильной прямой линии (граница вставки поверх размытого фона)."""
    import numpy as np
    ref = np.median(sharp[sharp > np.percentile(sharp, 50)])
    mask = sharp >= SHARP * ref
    best, cur = (0, size), None
    runs = []
    for i, m in enumerate(mask):
        if m and cur is None:
            cur = i
        if not m and cur is not None:
            runs.append((cur, i))
            cur = None
    if cur is not None:
        runs.append((cur, size))
    runs = [r for r in runs if r[1] - r[0] > size * 0.15]
    if runs:
        best = max(runs, key=lambda r: r[1] - r[0])
    a, b = best

    def snap(p):
        lo, hi = max(1, p - SNAP), min(size - 1, p + SNAP)
        if hi <= lo:
            return p
        i = lo + int(np.argmax(edge[lo:hi]))
        return i + 1 if edge[i] > 3 * np.median(edge) else p
    a = 0 if a < 8 else snap(a)
    b = size if b > size - 8 else snap(b)
    return a, b


def edge_box(path, start, end):
    """Рамка по сильным прямым линиям: граница вставки поверх размытого фона даёт линию через всю ширину (или высоту).
    Берём самую верхнюю и самую нижнюю сильную линию — так в рамку попадают все блоки составного кадра
    (веб-камера над игрой), а не один. → (рамка, сила самой сильной линии) или None. Сильная линия (≥ EDGE_PEAK) —
    признак чистой вставки или составного кадра; слабая — надписи и контент тоже дают линии, и рамка может захватить лишнее."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    gx = gy = None
    k = 0
    for i in range(SAMPLES + 4):
        t = start + (end - start) * (i + 0.5) / (SAMPLES + 4)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        dx = np.median(np.abs(np.diff(g, axis=1)), axis=0)   # медиана, а не среднее: линия — когда резко вдоль всей длины
        dy = np.median(np.abs(np.diff(g, axis=0)), axis=1)
        gx, gy = (dx, dy) if gx is None else (gx + dx, gy + dy)
        k += 1
    h, w = fr.shape[:2] if k else (0, 0)
    cap.release()
    if not k:
        return None

    def strong(a, size):
        a = a / k
        thr = max(EDGE_MIN, EDGE_REL * float(a.max()))
        idx = [i + 1 for i in np.where(a >= thr)[0] if 3 < i < size - 3]
        return idx
    ys, xs = strong(gy, h), strong(gx, w)
    if len(ys) < 2 or ys[-1] - ys[0] < 0.3 * h:
        return None
    peak = float((gy / k)[3:-3].max())
    y0, y1 = ys[0], ys[-1]
    x0, x1 = (xs[0], xs[-1]) if len(xs) >= 2 and xs[-1] - xs[0] > 0.3 * w else (0, w)
    y0, y1, x0, x1 = (int(v) - int(v) % 2 for v in (y0, y1, x0, x1))
    return [x0, y0, x1 - x0, y1 - y0], peak


EDGE_PEAK = 75.0   # у составных кадров и чистых вставок пик 79–190, у проблемных исходников — 39–64
EDGE_MIN = 8.0     # линия слабее — не граница (текст, контент)
EDGE_REL = 0.08    # и слабее этой доли самой сильной линии кадра — тоже


def frame_box(path, start, end):
    """Рамка куска [x, y, w, h] по SAMPLES кадрам."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    rows = cols = ey = ex = None
    k = 0
    for i in range(SAMPLES):
        t = start + (end - start) * (i + 0.5) / SAMPLES
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
        r, c = lap.mean(1), lap.mean(0)
        dy = np.median(np.abs(np.diff(g, axis=0)), axis=1)
        dx = np.median(np.abs(np.diff(g, axis=1)), axis=0)
        rows, cols = (r, c) if rows is None else (rows + r, cols + c)
        ey, ex = (dy, dx) if ey is None else (ey + dy, ex + dx)
        k += 1
    cap.release()
    if not k:
        return None
    h, w = len(rows), len(cols)
    y0, y1 = _range(rows / k, ey / k, h)
    x0, x1 = _range(cols / k, ex / k, w)
    y0, y1, x0, x1 = (v - v % 2 for v in (y0, y1, x0, x1))
    return [x0, y0, x1 - x0, y1 - y0]


# ---------- рекламные баннеры ----------
# Баннер в чужом креативе — прямоугольник с цветной рамкой поверх кадра (внутри он анимирован, поэтому целиком его по
# картинке не узнать, а рамка постоянная). Какие рамки считаем рекламой — banner_refs.json рядом с настройками:
# [{"name": "upgrader", "hsv": [h_от, h_до, s_от, v_от]}] (оттенок OpenCV 0–179). Рамку найдёт и в новом ролике,
# если цвет тот же; новый баннер — добавить строку (человек говорит, какой цвет рамки) или `split banner-add`.
BANNER_REFS = core.DATA / "banner_refs.json"
BANNER_DEFAULT = [{"name": "upgrader", "hsv": [22, 38, 120, 150]}]
BANNER_STEP = 0.25   # с между проверками кадра
BANNER_PAD = 0.3     # с запаса до появления и после ухода баннера (он «выезжает» и «уезжает»)


def banner_refs():
    return core.load_json(BANNER_REFS, None) or BANNER_DEFAULT


def has_frame(fr, ref):
    """В кадре есть прямоугольник с рамкой этого цвета: две длинные горизонтали и две вертикали, образующие его."""
    import cv2
    import numpy as np
    small = cv2.resize(fr, (540, int(fr.shape[0] * 540 / fr.shape[1])))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h0, h1, s0, v0 = ref["hsv"]
    m = ((hsv[..., 0] >= h0) & (hsv[..., 0] <= h1) & (hsv[..., 1] >= s0) & (hsv[..., 2] >= v0)).astype(np.uint8)
    H, W = m.shape
    rows = np.where(m.sum(1) > 0.45 * W)[0]
    if not len(rows):
        return False
    top, bot = rows.min(), rows.max()
    if bot - top < 0.08 * H:
        return False
    cols = np.where(m[top:bot + 1].sum(0) > 0.45 * (bot - top))[0]
    return len(cols) > 0 and cols.max() - cols.min() > 0.45 * W


FREEZE_STEP = 0.1     # с между кадрами при поиске стоп-кадра
FREEZE_MIN = 1.2      # с: стоп-кадр с движущимся прямоугольником короче — не реклама
FREEZE_BOX = 0.30     # меняется не больше этой доли кадра (по охватывающему прямоугольнику) — остальное стоит
FREEZE_TAIL = 4.0      # с: насколько максимум тянем вырез после баннера, пока видео стоит
FREEZE_SPIKE = 2      # шагов подряд с изменениями вне баннера, которые ещё терпим при продлении выреза
FREEZE_FILL = 0.30   # и этот прямоугольник заполнен изменениями: баннер анимирован целиком (0.48–0.56 у настоящих),
                      # а случайные мелкие движения на стоячей картинке — 0.07–0.24


def freeze_ads(path, duration):
    """Универсальный признак рекламы: видео под баннером стоит (стоп-кадр), а меняется только небольшой прямоугольник —
    сам баннер, он анимирован. В обычном куске меняется почти весь кадр. → [(от, до)] без запаса."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    every = max(1, round(FREEZE_STEP * fps))
    prev, flags, n = None, [], 0
    masks, boxes = [], []          # что менялось на каждом шаге и где (по ним продлеваем вырез до конца стоп-кадра)
    while True:
        if not cap.grab():
            break
        if n % every == 0:
            ok, fr = cap.retrieve()
            if ok:
                g = cv2.cvtColor(cv2.resize(fr, (135, 240)), cv2.COLOR_BGR2GRAY).astype(np.int16)
                hit, d, bb = False, None, None
                if prev is not None:
                    d = np.abs(g - prev) > 6
                    r, c = np.where(d.sum(1) >= 2)[0], np.where(d.sum(0) >= 2)[0]
                    if len(r) and len(c) and d.mean() > 0.003:
                        box = (r.max() - r.min() + 1) * (c.max() - c.min() + 1)
                        hit = box / d.size <= FREEZE_BOX and d.sum() / box >= FREEZE_FILL
                        if hit:
                            bb = (r.min(), r.max(), c.min(), c.max())
                flags.append((n / fps, hit))
                masks.append(d)
                boxes.append(bb)
                prev = g
        n += 1
    cap.release()
    if not flags:
        return []
    # решение по окну в секунду: баннер «рваный» (в иных кадрах изменений мало), а случайные мелкие движения — единичные
    w = max(3, round(1.0 / FREEZE_STEP))
    hit = np.array([h for _, h in flags], dtype=float)
    avg = np.convolve(hit, np.ones(w) / w, mode="same")
    # гистерезис: ядро — окно ≥ 0.5, края продлеваем, пока в окне ≥ 0.12 (баннер заходит и уходит с вялыми кадрами)
    core_ = avg >= 0.5
    keep = core_.copy()
    for i in np.where(core_)[0]:
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(avg) and avg[j] >= 0.12 and not keep[j]:
                keep[j] = True
                j += step
    runs, cur = [], None
    for (t, _), k in zip(flags, keep):
        if k:
            cur = [t, t] if cur is None else [cur[0], t]
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    ts = [t for t, _ in flags]
    out = []
    for a, b in runs:
        if b - a < FREEZE_MIN:
            continue
        ia, ib = ts.index(a), ts.index(b)
        bbs = [boxes[i] for i in range(ia, ib + 1) if boxes[i]]
        if bbs:
            # после анимации баннер часто остаётся статичной заставкой (логотип): в нём ничего не меняется, и признак
            # «движется прямоугольник» её не видит. Тянем вырез дальше, пока остальной кадр стоит (видео ещё на паузе).
            r0, r1 = min(x[0] for x in bbs) - 2, max(x[1] for x in bbs) + 2
            c0, c1 = min(x[2] for x in bbs) - 2, max(x[3] for x in bbs) + 2
            # одиночные всплески (камера сверху, до FREEZE_SPIKE шагов подряд) терпим: заставка за ними стоит дальше
            j, bad, last = ib + 1, 0, ib
            while j < len(ts) and ts[j] - b <= FREEZE_TAIL and masks[j] is not None:
                o = masks[j].copy()
                o[max(0, r0):r1 + 1, max(0, c0):c1 + 1] = False
                if o.mean() > 0.004:
                    bad += 1
                    if bad > FREEZE_SPIKE:
                        break
                else:
                    bad, last = 0, j
                j += 1
            b = ts[last] if last > ib else b
        out.append((a, b))
    return out


def banners(path, duration):
    """→ [(от, до)] отрезки, где в кадре рамка рекламного баннера (с запасом). Без OpenCV — пусто."""
    try:
        import cv2
    except ImportError:
        return []
    refs = banner_refs()
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    hits, n = [], 0
    every = max(1, round(BANNER_STEP * fps))
    while True:   # подряд, без перемотки: перемотка в h264 в разы медленнее
        if not cap.grab():
            break
        if n % every == 0:
            ok, fr = cap.retrieve()
            if ok and any(has_frame(fr, r) for r in refs):
                hits.append(n / fps)
        n += 1
    cap.release()
    out = []
    for t in hits:
        if out and t - out[-1][1] <= 0.6:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(round(max(0, a - BANNER_PAD), 2), round(min(duration, b + BANNER_PAD), 2)) for a, b in out
            if b - a >= 0.4]   # вспышка жёлтого короче 0.4 с — не баннер


# ---------- название момента по сказанному ----------
# Faster-Whisper (если стоит рядом с ffmpeg) расшифровывает речь, каждому куску — его слова (`said`) и короткое название
# из первой фразы. Хорошее название чаще всего — смешная реплика; автоматика берёт первую фразу, ИИ или человек правит.

def asr(path):
    """→ [{start, end, text}] или [] (нет Whisper / не вышло). Язык определяется сам."""
    exe = core.FFMPEG.parent / "faster-whisper-xxl.exe"
    models = core.FFMPEG.parent / "_models"
    if not (exe.exists() and models.exists()):
        return []
    tmp = core.DATA / "asr_tmp"
    tmp.mkdir(exist_ok=True)
    for old in tmp.glob("*.json"):
        old.unlink(missing_ok=True)
    r = core.run([exe, path, "--model", "large-v3", "--model_dir", models, "--output_format", "json",
                  "--output_dir", tmp, "--vad_filter", "True"], 900)
    f = next(iter(tmp.glob("*.json")), None)
    if not f:
        core.log.warning("whisper: нет результата: %s", (r.stderr or r.stdout)[-500:])
        return []
    try:
        segs = json.loads(f.read_text(encoding="utf-8")).get("segments", [])
    except ValueError:
        return []
    finally:
        f.unlink(missing_ok=True)
    return [{"start": g["start"], "end": g["end"], "text": g["text"].strip()} for g in segs if g.get("text", "").strip()]


def short_name(said, words=4):
    """Первая фраза → короткое название (до `words` слов, без знаков по краям)."""
    first = re.split(r"[.?!…]+\s", said.strip(), maxsplit=1)[0]
    w = first.split()[:words]
    return " ".join(w).strip(" ,.;:!?…-—\"'«»")


def name_moments(segs, speech):
    for g in segs:
        if g.get("ad"):
            continue
        if g.get("join"):
            continue
        said = " ".join(x["text"] for x in speech if g["start"] <= (x["start"] + x["end"]) / 2 < g["end"]).strip()
        g["said"] = said
        if said and not g.get("name"):
            g["name"], g["name_src"] = short_name(said), "речь"


# ---------- названия с экрана ----------
# В подборках «ТОП N» вверху кадра список («1. Легендарна, 2. Постукав…»): строка появляется, когда показывают момент.
# Читаем его встроенным распознаванием текста Windows (язык «ru», ничего ставить не надо) и берём для куска ту строку,
# которая появилась нового. Нет Windows-распознавания — тихо пропускаем, останутся названия по речи.
OCR_PS = r"""
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Foundation, ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language('ru')))
if (-not $eng) { '{}'; exit }
$out = [ordered]@{}
foreach ($p in (Get-Content -LiteralPath '__LIST__' -Encoding UTF8)) {
  if (-not $p) { continue }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  $out[$p] = @($res.Lines | ForEach-Object {
    $y0 = ($_.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
    $y1 = ($_.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
    [ordered]@{ t = $_.Text; y = [int]$y0; h = [int]($y1 - $y0) } })
}
$out | ConvertTo-Json -Compress
"""
LIST_LINE = re.compile(r"^\s*[\dТтIlІ|ЗзZz]{1,2}\s*[.,)]\s*(\S.*)$")   # «3.», у распознавания это бывает «З.» (кириллица)
FIX = str.maketrans({"Ј": "і", "ј": "і", "ђ": "і", "Ђ": "І"})   # частые подмены движка в украинском


def ocr_lines(pngs):
    """Список картинок → {путь: [{t: текст, y, h}]} встроенным распознаванием Windows; нет его — {}."""
    import base64
    import subprocess
    if not pngs or not sys.platform.startswith("win"):
        return {}
    lst = core.DATA / "ocr_list.txt"
    lst.write_text("\n".join(str(p) for p in pngs), encoding="utf-8")
    script = OCR_PS.replace("__LIST__", str(lst))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                            base64.b64encode(script.encode("utf-16-le")).decode()],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                           creationflags=core.NO_WINDOW)
        data = json.loads(r.stdout.strip() or "{}")
    except Exception:   # noqa: BLE001
        core.log.exception("распознавание текста не вышло")
        return {}
    finally:
        lst.unlink(missing_ok=True)
    return {k: [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)] for k, v in data.items()}


def similar(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.7


BIN = "crop=iw:ih*0.45:0:0,scale=iw*2:-1:flags=lanczos,format=gray,lut=y='if(gt(val,185),255,0)',negate"
# белый текст на чёрно-белую картинку: на цветном фоне с обводкой движок теряет строки, а так читает их все


def text_lines(lines):
    """Строки с названиями → [(текст, был_номер)]; номер перед названием отрезаем (заголовок без номера — это не список);
    числа, мусор и короткое — нет."""
    out = []
    for ln in lines:
        t = ln["t"].strip()
        m = LIST_LINE.match(t)
        numbered = bool(m)
        t = (m[1] if m else t).strip().translate(FIX)
        t2 = re.sub(r"^\d{1,2}\s+", "", t)   # «30 на видстани» — номер «3.» прочитан как «30» и приклеился
        numbered = numbered or t2 != t
        t = t2
        letters = sum(c.isalpha() for c in t)
        if letters >= 4 and letters >= 0.7 * len(t.replace(" ", "")):
            out.append((t, numbered))
    return out


def same_title(a, b):
    """Одно название, хоть и прочитано по-разному: движок то теряет слово («Мила» / «Мила щббьонка»), то путает буквы."""
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a) or similar(a, b)


ROW_NUM = {"l": 1, "I": 1, "|": 1, "Т": 1, "т": 1, "З": 3, "з": 3, "Z": 3, "z": 3}
ROW_LINE = re.compile(r"^\s*([1-6lI|ТтЗзZz])\s*[.,)]\s*(.*)$")   # «2.», «3. макафончик», «з.» — с точкой после номера


def numbered_rows(lines):
    """Строки списка с номером → {номер: текст}; пустая строка («2.») — тоже, с пустым текстом. Короткие названия
    («1 vs 1») здесь не теряются, в отличие от поиска по буквам."""
    out = {}
    for ln in lines:
        m = ROW_LINE.match(ln["t"].strip())
        if not m:
            continue
        n = ROW_NUM.get(m[1]) or int(m[1])
        out.setdefault(n, m[2].strip().translate(FIX))
    return out


def best_text(texts):
    """Какой из прочтений строки брать: самое частое, при равенстве — длиннее."""
    from collections import Counter
    c = Counter(t.lower() for t in texts)
    top = max(c.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    return next(t for t in texts if t.lower() == top)


def title_marks(path, duration, step=1.0):
    """Появления названий в списке на экране → [(время, название)] — начала новых моментов (первое название, стоящее
    с начала, — с временем 0). Новое название — строка, которая
    появилась не с самого начала, держится до конца ролика и читается в большинстве кадров (так отличаем от заголовка,
    который стоит с начала, и от случайных ошибок распознавания). Грубо раз в `step` с, потом уточняем на 0.2 с."""
    work = core.DATA / "ocr_tmp"
    work.mkdir(exist_ok=True)
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)
    core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vf",
              f"fps=1/{step},{BIN}", work / "c_%04d.png"], 600)
    shots = sorted(work.glob("c_*.png"))
    read = ocr_lines(shots)
    if not read or len(shots) < 4:
        return []
    per = [text_lines(read.get(str(f), [])) for f in shots]
    rows_per = [numbered_rows(read.get(str(f), [])) for f in shots]
    row_events, row_initial = [], []        # по номерам строк: «2. 1 vs 1» — строка номер 2 заполнилась
    for n_row in range(1, 7):
        filled = [i for i, r in enumerate(rows_per) if len(r.get(n_row, "").replace(" ", "")) >= 2]
        if not filled or filled[-1] < len(shots) - 2:
            continue
        first = filled[0]
        if len(filled) < max(3, 0.6 * (filled[-1] - first + 1)):
            continue
        text = best_text([rows_per[i][n_row] for i in filled])
        (row_initial if first == 0 else row_events).append((first, text))
    tracks = []
    for i, lines in enumerate(per):
        for t, num in lines:
            tr = next((x for x in tracks if same_title(t, x["text"])), None)
            if tr:
                tr["idx"].append(i)
                tr["num"] = tr["num"] or num
                if len(t) > len(tr["text"]):
                    tr["text"] = t
            else:
                tracks.append({"text": t, "idx": [i], "num": num})
    n = len(shots)
    events, initial = [], []
    rtexts = [t for _, t in row_events + row_initial]
    for tr in tracks:
        first, last, idx = tr["idx"][0], tr["idx"][-1], set(tr["idx"])
        if last >= n - 2 and len(idx) >= max(3, 0.6 * (last - first + 1)):
            if any(same_title(tr["text"], x) for x in rtexts):
                continue                       # то же название уже найдено по номеру строки
            if first >= 1:
                events.append((first, tr["text"]))
            elif tr["num"]:
                initial.append(tr["text"])   # стоит с начала и с номером — название первого момента (заголовок без номера)
    events = sorted(row_events + events)
    initial = [t for _, t in row_initial] + initial
    fine = {}
    for i, text in events:
        for k in range(1, 5):
            tt = (i - 1) * step + k * 0.2 * step
            f = work / f"f_{i}_{k}.png"
            core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{tt:.2f}", "-i", path,
                      "-frames:v", "1", "-vf", BIN, f], 60)
            if f.exists():
                fine[(i, k)] = (tt, f)
    fread = ocr_lines([f for _, f in fine.values()]) if fine else {}
    marks = []
    for i, text in events:
        t = i * step                    # не нашли точнее — по грубому кадру
        for k in range(1, 5):
            if (i, k) in fine and (any(same_title(text, x) for x, _ in text_lines(fread.get(str(fine[(i, k)][1]), [])))
                                   or any(same_title(text, x) for x in numbered_rows(fread.get(str(fine[(i, k)][1]), [])).values() if x)):
                t = fine[(i, k)][0]
                break
        marks.append((round(t, 2), text))
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)
    marks = sorted(marks)
    if len(initial) == 1:
        marks.insert(0, (0.0, initial[0]))
    return marks


YELLOW = ("format=yuv444p,geq=lum='if(gt(lum(X,Y),140)*lt(cb(X,Y),118)*gt(cr(X,Y),125),0,255)':cb=128:cr=128,"
          "scale=iw*2:-1:flags=bilinear,format=gray")   # оставляем только жёлтое: чёрный текст на белом
CAPTION_STEP = 0.5
CAPTION_H = 50    # мин. высота строки при ×2 (подпись ≈85, а жёлтые надписи игры — названия точек на карте — до 26)


def caption_marks(path, duration):
    """Жёлтая подпись-заголовок поверх кадра (одно название на момент, держится пару секунд и пропадает; списка нет) →
    [(время появления, название)]. Читаем только жёлтый цвет: на цветном фоне обычное распознавание её теряет."""
    work = core.DATA / "ocr_tmp"
    work.mkdir(exist_ok=True)
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)
    core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vf",
              f"fps=1/{CAPTION_STEP},crop=iw:ih*0.45:0:0,{YELLOW}", work / "y_%05d.png"], 900)
    shots = sorted(work.glob("y_*.png"))
    read = ocr_lines(shots)
    tracks = []
    for i, f in enumerate(shots):
        big = [ln for ln in read.get(str(f), []) if ln.get("h", 0) >= CAPTION_H]
        for t, _ in text_lines(big):
            tr = next((x for x in tracks if same_title(t, x["text"]) and i - x["idx"][-1] <= 3), None)
            if tr:
                tr["idx"].append(i)
                tr["texts"].append(t)
            else:
                tracks.append({"text": t, "texts": [t], "idx": [i]})
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)
    return [(round(tr["idx"][0] * CAPTION_STEP, 2), best_text(tr["texts"])) for tr in tracks if len(tr["idx"]) >= 2]


def merge_cuts(scene_cuts, marks, near=0.8):
    """Смены сцены + появления строк списка. Метка рядом (≤ near с) со сменой сцены — это тот же разрез, берём смену сцены."""
    out = list(scene_cuts)
    for m in marks:
        if all(not (m - 3.0 <= c <= m + near) for c in out):   # название обычно появляется на 1–3 с позже смены сцены
            out.append(m)
    return sorted(out)


def ocr_names(path, segs):
    """Название куска = строка списка на экране, которая появилась новой по сравнению с предыдущим куском."""
    work = core.DATA / "ocr_tmp"
    work.mkdir(exist_ok=True)
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)
    shots = {}
    for i, g in enumerate(segs):
        if g.get("ad"):
            continue
        png = work / f"s{i}.png"
        t = g["start"] + 0.7 * (g["end"] - g["start"])
        core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                  "-vf", "crop=iw:ih*0.45:0:0", png], 60)
        if png.exists():
            shots[i] = png
    read = ocr_lines(list(shots.values()))
    # движок в общем кадре теряет слова с «і», «щ», «ь», а одну строку, увеличенную, читает целиком — второй проход по строкам
    bands = {}
    for i, png in shots.items():
        for k, ln in enumerate(read.get(str(png), [])):
            if LIST_LINE.match(ln["t"]):
                band = work / f"s{i}_l{k}.png"
                core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", png, "-vf",
                          f"crop=iw:{ln['h'] + 30}:0:{max(0, ln['y'] - 15)},scale=iw*2:-1:flags=lanczos", band], 60)
                if band.exists():
                    bands[(i, k)] = band
    again = ocr_lines(list(bands.values()))
    prev = []
    for i, g in enumerate(segs):
        if i not in shots:
            continue
        titles = []
        for k, ln in enumerate(read.get(str(shots[i]), [])):
            if not LIST_LINE.match(ln["t"]):
                continue
            text = ln["t"]
            if (i, k) in bands and again.get(str(bands[(i, k)])):
                text = again[str(bands[(i, k)])][0]["t"]
            m = LIST_LINE.match(text) or LIST_LINE.match(ln["t"])
            titles.append(m[1].strip().translate(FIX))
        new = [t for t in titles if len(t) >= 3 and not any(similar(t, p) for p in prev)]
        if g.get("join") and i > 0:
            pass                     # продолжение прежнего момента: название у него уже есть
        elif new:
            g["name"], g["name_src"] = new[-1], "экран"
        if titles:
            prev = titles
    for f in work.glob("*.png"):
        f.unlink(missing_ok=True)


def merge_ads(ads):
    out = []
    for a, b in sorted(ads):
        if out and a <= out[-1][1] + 0.5:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def cut_out(segs, ads):
    """Куски (start, end) → куски с вырезанными отрезками рекламы: кусок рвём по границам баннера, часть с баннером
    помечаем ad. Если баннер стоит посреди одной сцены (смены сцены нет), куски до и после — один момент: `join` у
    второго, при нарезке они склеиваются в один клип. → [(от, до, реклама, склеить_с_предыдущим)]."""
    out = []
    for a, b in segs:
        pos, kept = a, False
        for x, y in ads:
            if y <= pos or x >= b:
                continue
            if x - pos >= MIN_SEG / 2:
                out.append((pos, x, False, kept))
                kept = True
            out.append((max(x, pos), min(y, b), True, False))
            pos = min(y, b)
            keep_join = kept
        if b - pos > 0.05:
            out.append((pos, b, False, kept and pos > a))
    return [(round(a, 2), round(b, 2), ad, j) for a, b, ad, j in out]


def analyze(path, speech=True):
    p = core.probe(path)
    ads = merge_ads(banners(path, p["duration"]) + [(round(max(0, a - 0.1), 2), round(min(p["duration"], b + 0.1), 2))
                                                      for a, b in freeze_ads(path, p["duration"])])
    segs = []
    cuts = scenes(path)
    try:   # где в списке на экране появилось новое название (внутри рекламы не считаем — там тоже есть текст)
        marks = title_marks(path, p["duration"])
    except Exception:   # noqa: BLE001
        core.log.exception("метки списка не вышли")
        marks = []
    if not marks:   # списка нет — ищем жёлтые подписи-заголовки
        try:
            marks = caption_marks(path, p["duration"])
        except Exception:   # noqa: BLE001
            core.log.exception("подписи не вышли")
    parts = cut_out(bounds(merge_cuts(cuts, [t for t, _ in marks if t > 0.5]), p["duration"]), ads)
    merged = []
    for a, b, ad, join in parts:   # метка названия внутри рекламы разрезала её на два куска — это одна реклама
        if ad and merged and merged[-1][2]:
            merged[-1] = (merged[-1][0], b, True, False)
        else:
            merged.append((a, b, ad, join))
    for a, b, ad, join in merged:
        alts = []
        if not ad:
            eb, fb = edge_box(path, a, b), frame_box(path, a, b)
            first = [eb[0]] if eb and eb[1] >= EDGE_PEAK else []     # сильная линия — верим ей
            for x in first + [fb] + ([eb[0]] if eb else []):
                if x and x not in alts:
                    alts.append(x)
        box = alts[0] if alts else None
        g = {"start": a, "end": b, "crop": box or [0, 0, p["w"], p["h"]]}
        if len(alts) > 1:
            g["alts"] = alts   # другие варианты рамки: в редакторе переключаются клавишей R
        if ad:
            g.update(skip=True, name="реклама", ad=True)
        if join:
            g["join"] = True   # продолжение прошлого куска (между ними вырезана реклама)
        segs.append(g)
    # у рекламного куска рамка — как у соседнего (чтобы «вернуть» кусок в редакторе было нормально)
    for i, g in enumerate(segs):
        if g.get("ad"):
            nb = next((x for x in segs[i + 1:] + segs[:i][::-1] if not x.get("ad")), None)
            if nb:
                g["crop"] = list(nb["crop"])
    # у склеенного куска рамка — как у первой части (клип один)
    for i, g in enumerate(segs):
        if g.get("join"):
            prev = next((x for x in segs[:i][::-1] if not x.get("ad")), None)
            if prev:
                g["crop"] = list(prev["crop"])
                if prev.get("alts"):
                    g["alts"] = [list(x) for x in prev["alts"]]
    real = [x for x in segs if not x.get("ad")]
    for t, text in marks:   # название, появившееся на экране, — имя куска, который с него начинается
        near = min((x for x in real if t - 3.0 <= x["start"] <= t + 0.9), key=lambda x: abs(x["start"] - t), default=None)
        if near:
            g = near                       # метка у самого начала куска (разрез по сцене и появление названия рядом)
        else:                              # метка внутри куска или в рекламе → кусок, который идёт после неё
            g = next((x for x in real if x["end"] > t), None)
        if g:
            g["title_start"] = True
            g.pop("join", None)            # новое название — новый момент, даже если смены сцены не было
            g["name"], g["name_src"] = text, "экран"
    if not marks:   # меток нет (списка нет или не прочитался) — запасной способ по кадрам кусков
        try:
            ocr_names(path, segs)
        except Exception:   # noqa: BLE001
            core.log.exception("названия с экрана не вышли")
    if speech:
        try:
            name_moments(segs, asr(path))
        except Exception:   # noqa: BLE001 — названия не обязательны, разбор не должен из-за них падать
            core.log.exception("названия моментов не вышли")
    return {"w": p["w"], "h": p["h"], "duration": round(p["duration"], 2), "segs": segs, "ads": ads, "marks": [t for t, _ in marks], "fps": round(p["fps"], 2)}


def sheet(path, info, out, tiles=18, tw=240):
    """Сводка одной картинкой для ИИ и для глаз: кадры по времени, у каждого — номер и название куска, рамка (жёлтая;
    красная — реклама, серая — «не брать»), под кадром время. → путь к картинке."""
    Image, ImageDraw, ImageFont = core.pillow()
    dur, W, H = info["duration"], info["w"], info["h"]
    step = max(0.5, dur / tiles)
    work = core.DATA / "sheet_tmp"
    work.mkdir(exist_ok=True)
    for f in work.glob("*.jpg"):
        f.unlink(missing_ok=True)
    core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vf",
              f"fps=1/{step:.3f},scale={tw}:-2", "-q:v", "3", work / "%03d.jpg"], 600)
    frames = sorted(work.glob("*.jpg"))
    if not frames:
        raise core.JobError("не снял кадры для сводки")
    th = Image.open(frames[0]).height
    cols = 6
    rows = (len(frames) + cols - 1) // cols
    lab = 46
    im = Image.new("RGB", (cols * (tw + 6) + 6, rows * (th + lab + 6) + 6), (17, 20, 23))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(core.CR_FONT), 14)
    for i, f in enumerate(frames):
        t = (i + 0.5) * step
        k = next((j for j, g in enumerate(info["segs"]) if g["start"] <= t < g["end"]), len(info["segs"]) - 1)
        g = info["segs"][k]
        x0, y0 = 6 + (i % cols) * (tw + 6), 6 + (i // cols) * (th + lab + 6)
        im.paste(Image.open(f), (x0, y0))
        cx, cy, cw, ch = (v * tw / W for v in g["crop"])
        col = (255, 70, 70) if g.get("ad") else (130, 130, 130) if g.get("skip") else (245, 197, 24)
        d.rectangle((x0 + cx, y0 + cy, x0 + cx + cw, y0 + cy + ch), outline=col, width=3)
        d.text((x0, y0 + th + 2), f"{t:.1f} с · кусок {k + 1}" + (" · РЕКЛАМА" if g.get("ad") else ""), font=font, fill=col)
        d.text((x0, y0 + th + 22), (g.get("name") or "")[:28], font=font, fill=(220, 220, 220))
    im.save(out, quality=88)
    for f in frames:
        f.unlink(missing_ok=True)
    return out


def cut(path, seg, out):
    """seg: {start, end, crop: [x, y, w, h]} → out (mp4, ширина 1080 у вертикального / высота 1080 у горизонтального)."""
    x, y, w, h = (int(v) - int(v) % 2 for v in seg["crop"])
    sc = "scale=1080:-2" if w <= h else "scale=-2:1080"
    r = core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seg['start']:.3f}", "-i", path,
                  "-t", f"{seg['end'] - seg['start']:.3f}", "-vf", f"crop={w}:{h}:{x}:{y},{sc}:flags=lanczos,setsar=1",
                  "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], 1800)
    if r.returncode != 0:
        core.log.error("ffmpeg нарезка: %s", r.stderr[-2000:])
        raise core.JobError("не нарезал кусок")
    return out


def cut_many(path, parts, crop, out):
    """Несколько кусков [(от, до)] одним клипом (склейка вокруг вырезанной рекламы) с одной рамкой."""
    if len(parts) == 1:
        return cut(path, {"start": parts[0][0], "end": parts[0][1], "crop": crop}, out)
    x, y, w, h = (int(v) - int(v) % 2 for v in crop)
    sc = "scale=1080:-2" if w <= h else "scale=-2:1080"
    fc, ins = "", ""
    for i, (a, b) in enumerate(parts):
        fc += (f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y},{sc}:flags=lanczos,setsar=1[v{i}];"
               f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS[a{i}];")
        ins += f"[v{i}][a{i}]"
    fc += f"{ins}concat=n={len(parts)}:v=1:a=1[v][a]"
    r = core.run([core.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-filter_complex", fc,
                  "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], 1800)
    if r.returncode != 0:
        core.log.error("ffmpeg склейка кусков: %s", r.stderr[-2000:])
        raise core.JobError("не склеил куски одного момента")
    return out
