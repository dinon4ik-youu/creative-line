"""Ролик целиком: кадр 9:16 с человеком по центру, холодный цветокор, субтитры (лесенка + по слову).
Тайминги слов — json из faster-whisper. Музыка и нарезка на части — следующие шаги.

  python tools/subs/make.py <видео> <whisper.json> <выход.mp4> [--ss S --seconds N] [--zoom 1.3] [--style Классика] [--filter Холодный --filter-int 100] [--adj правки.json] [--music mp3 [--music-offset S] [--music-vol 100]]
Всё настраивается в tools/subs/config.json. Рабочие файлы — рядом с выходным.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

import subs

HERE = Path(__file__).parent
FFMPEG = HERE.parent / "fw" / "Faster-Whisper-XXL" / "ffmpeg.exe"
MODEL = HERE / "models" / "face_detection_yunet_2023mar.onnx"   # модель YuNet — см. README


# папка со шрифтами стилей; в фильтре ffmpeg двоеточие диска экранируется
FONTSDIR = (HERE / "fonts").as_posix().replace(":", r"\:")


def grade_chain(G, name, k):
    """Цепочка ffmpeg для фильтра `name` (filters.json) с интенсивностью k = 0…1: числа плавно идут от нейтрали к полному эффекту."""
    spec = json.loads((HERE / "filters.json").read_text(encoding="utf-8")).get(name, {"from_config": True})
    if spec.get("none"):
        return ""
    if spec.get("from_config"):                                      # «Холодный»: числа из config.json → grade, как было
        spec = {"sat": G["saturation"], "contrast": G["contrast"], "shadows_r": G["shadows_r"], "shadows_b": G["shadows_b"],
                "mid_r": G["mid_r"], "mid_b": G["mid_b"], "black_lift": G["black_lift"], "white_cap": G["white_cap"]}
    k = max(0.0, min(1.0, k))
    L = lambda neutral, key: neutral + (spec.get(key, neutral) - neutral) * k   # noqa: E731
    chain = (f"eq=saturation={L(1, 'sat'):.3f}:contrast={L(1, 'contrast'):.3f},"
             f"colorbalance=rs={L(0, 'shadows_r'):.3f}:bs={L(0, 'shadows_b'):.3f}:rm={L(0, 'mid_r'):.3f}:bm={L(0, 'mid_b'):.3f},"
             f"curves=all='0/{L(0, 'black_lift'):.3f} 1/{L(1, 'white_cap'):.3f}'")
    if spec.get("grain") and k > 0.02:                               # плёночное зерно
        chain += f",noise=alls={max(1, round(spec['grain'] * k))}:allf=t"
    return chain


def vignette_strength(name, k):
    """Насколько темнеют углы (0–1) у фильтра `name` при интенсивности k. Сама виньетка — готовая картинка-маска (см. vignette_png)."""
    spec = json.loads((HERE / "filters.json").read_text(encoding="utf-8")).get(name, {})
    return float(spec.get("vignette", 0)) * max(0.0, min(1.0, k))


def vignette_png(W, H, strength):
    """Маска виньетки: серая картинка W×H, темнеющая к углам. Делается один раз и лежит в cache/ (шаг 5 %). Кадр умножается на неё
    (только яркость, цвет не трогаем) — дешевле, чем расчёт виньетки фильтром ffmpeg (~5 с на ролик)."""
    kk = int(round(strength * 20)) * 5
    f = HERE / "cache" / f"vigm_{W}x{H}_{kk}.png"
    if not f.exists():
        f.parent.mkdir(exist_ok=True)
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2) / np.sqrt(2)   # 0 в центре, 1 в углу
        a = np.clip((r - 0.42) / 0.58, 0, 1) ** 1.6 * (kk / 100)                  # в углу непрозрачность = сила виньетки
        gray = np.clip((1 - a) * 255, 0, 255).astype(np.uint8)         # серая маска: белый — центр (без затемнения), темнее — к углам
        cv2.imwrite(str(f), gray)
    return f


def cfg_all():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def track_faces(video, width=960):
    """(t, x_центр, ширина_лица) в пикселях исходника; пропуски заполняются ближайшим известным.
    Результат кэшируется в <видео>.faces.json (пересборка того же куска не ищет лицо заново)."""
    video = Path(video)
    cache = video.with_name(video.name + ".faces.json")
    st = video.stat()
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("size") == st.st_size and d.get("mtime") == int(st.st_mtime):
            return [tuple(r) for r in d["rows"]], d["W"], d["H"], d["fps"]
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(fps / 10))                                   # ≈ 10 проб в секунду, как бы ни был велик fps
    k = width / W
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (width, int(H * k)), 0.7, 0.3, 5000)
    rows, i = [], 0
    while True:
        if not cap.grab():                                           # кадр, который не меряем, не конвертируем
            break
        if i % step == 0:
            ok, fr = cap.retrieve()
            small = cv2.resize(fr, (width, int(H * k)))
            _, faces = det.detect(small)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])          # самое крупное лицо
                rows.append((float(i / fps), float((f[0] + f[2] / 2) / k), float(f[2] / k)))
        i += 1
    cap.release()
    cache.write_text(json.dumps({"size": st.st_size, "mtime": int(st.st_mtime), "rows": rows, "W": W, "H": H,
                                 "fps": fps}), encoding="utf-8")
    return rows, W, H, fps


def smooth(rows, dur, alpha):
    """ровная траектория на сетку 15 кадров/с: линейная подстановка + экспоненциальное сглаживание"""
    t = np.array([r[0] for r in rows])
    xs = np.array([r[1] for r in rows])
    ws = np.array([r[2] for r in rows])
    grid = np.arange(0, dur, 1 / 15)
    gx, gw = np.interp(grid, t, xs), np.interp(grid, t, ws)
    for a in (gx, gw):
        for j in range(1, len(a)):
            a[j] = a[j - 1] + alpha * (a[j] - a[j - 1])
    return grid, gx, gw


def is_hdr(path):
    """HDR-съёмка телефона (BT.2020 / PQ / HLG) — только ей нужен тонмап; обычному видео он зря стоит ~30% времени."""
    r = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return bool(re.search(r"bt2020|smpte2084|arib-std-b67", r.stderr))


def lufs(path, af=""):
    """интегральная громкость файла (или его дорожки после фильтра af), LUFS"""

    cmd = [str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path)] + (["-af", af + ",ebur128"] if af else ["-af", "ebur128"])
    r = subprocess.run(cmd + ["-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


def window(rows, W, H, R):
    """Автоокно кадра: камера неподвижна, одно окно на весь ролик. Ширина = размах головы по кадру + тело (≈ N лиц),
    не уже 9:16 по высоте исходника; шире — добиваем размытым фоном сверху/снизу. Центр окна — середина размаха головы.
    → (cw, x0) в пикселях исходника."""
    fx = np.array([r[1] for r in rows])
    lo, hi = np.percentile(fx, 5), np.percentile(fx, 95)
    body = float(np.median([r[2] for r in rows])) * R["body_per_face"]
    cw = int(float(np.clip((hi - lo) + body, H * 9 / 16, W))) // 2 * 2
    return cw, int(np.clip((lo + hi) / 2 - cw / 2, 0, W - cw)), lo, hi


def fit(cw, x0, W, H, zoom=1.0):
    """Ручное приближение к автоокну. zoom > 1 — окно уже вокруг того же центра (кадр крупнее, может вылезти за верх и
    низ); zoom < 1 — окно то же, но картинка уже 1080 px (по бокам тоже размытый фон).
    → (cw, x0, ширина переднего плана на холсте 1080, его высота)."""
    zoom = max(0.3, min(float(zoom or 1), 4.0))
    if zoom > 1:
        c = x0 + cw / 2
        cw = max(int(cw / zoom) // 2 * 2, 64)
        x0 = int(np.clip(c - cw / 2, 0, W - cw))
    fw = int(round(1080 * min(zoom, 1.0) / 2)) * 2
    return cw, x0, fw, int(round(fw * H / cw / 2)) * 2


def auto_window(video):
    """Для предпросмотра в приложении: {W, H, cw, x0} автоокна (то же, что считает make.py)."""
    rows, W, H, _ = track_faces(video)
    if not rows:
        return None
    cw, x0, _, _ = window(rows, W, H, cfg_all()["reframe"])
    return {"W": W, "H": H, "cw": cw, "x0": x0}


def main():
    args =[a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 3:
        sys.exit(__doc__)
    video, wjson, out = Path(args[0]), args[1], Path(args[2])
    secs = float(sys.argv[sys.argv.index("--seconds") + 1]) if "--seconds" in sys.argv else None
    C = cfg_all()
    R, G = C["reframe"], C["grade"]
    work = out.with_suffix("")
    rows, W, H, fps = track_faces(video)
    if not rows:
        sys.exit("лицо не найдено — кадрирование по центру пока не сделано")
    dur = secs or rows[-1][0] + 0.5

    # камера неподвижна: одно окно на весь ролик. Ширина = размах головы по кадру + тело (≈ N лиц), не уже 9:16 по высоте
    # исходника; шире — добиваем размытым фоном сверху/снизу. Центр окна — середина размаха головы.
    cw, x0, lo, hi = window(rows, W, H, R)
    zoom = float(sys.argv[sys.argv.index("--zoom") + 1]) if "--zoom" in sys.argv else 1.0
    cw, x0, fg_w, fg_h = fit(cw, x0, W, H, zoom)

    sub = dict(C["subtitles"])                                       # стиль субтитров из styles.json: поверх config.json
    style = sys.argv[sys.argv.index("--style") + 1] if "--style" in sys.argv else None
    off = False
    if style:
        ov = dict(json.loads((HERE / "styles.json").read_text(encoding="utf-8"))[style])
        off = bool(ov.pop("none", False))
        sub.update(ov)
    ass = Path(str(work) + ".ass")
    if not off:
        adj = json.loads(Path(sys.argv[sys.argv.index("--adj") + 1]).read_text(encoding="utf-8")) if "--adj" in sys.argv else None
        ass.write_text(subs.build(subs.words_from_json(wjson), sub, adj), encoding="utf-8-sig")

    sdr = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p") if is_hdr(video) else "format=yuv420p"
    fname = sys.argv[sys.argv.index("--filter") + 1] if "--filter" in sys.argv else "Холодный"
    fint = float(sys.argv[sys.argv.index("--filter-int") + 1]) if "--filter-int" in sys.argv else 100.0
    chain = grade_chain(G, fname, fint / 100)
    vig_k = vignette_strength(fname, fint / 100)
    vig = vignette_png(1080, 1920, vig_k) if vig_k > 0.02 else None
    vig_f = vig.as_posix().replace(":", r"\:") if vig else ""         # путь для фильтра movie (двоеточие диска экранируем)
    fc = (f"[0:v]fps=30,{sdr}{',' + chain if chain else ''},split[a][b];"
          f"[a]scale=-2:480,crop=270:480,gblur=sigma={R['bg_blur'] / 4:g},eq=brightness={R['bg_dim']},"
          f"scale=1080:1920:flags=bilinear[bg];"
          f"[b]crop=w={cw}:h={H}:x={x0}:y=0,scale={fg_w}:{fg_h}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2" + (f"[c];movie='{vig_f}',loop=loop=-1:size=1,setpts=N/30/TB,format=yuv420p[vm];[c][vm]blend=c0_mode=multiply:c1_expr=A:c2_expr=A" if vig else "")
          + ("" if off else f",ass={ass.name}:fontsdir='{FONTSDIR}'") + "[v]")
    ss = sys.argv[sys.argv.index("--ss") + 1] if "--ss" in sys.argv else None   # отрезок внутри файла: с какой секунды
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y"] + (["-ss", ss] if ss else []) + ["-i", str(video)]
    if "--music" in sys.argv:
        music = sys.argv[sys.argv.index("--music") + 1]
        below = C["music"]["below_voice_db"]
        target = C["music"]["voice_target_lufs"]
        voice = lufs(video)
        vg = target - voice                                       # голос подтягиваем до целевой громкости
        gain = (target - below) - lufs(music)                     # музыка = голос − below дБ по LUFS
        mvol = float(sys.argv[sys.argv.index("--music-vol") + 1]) if "--music-vol" in sys.argv else 100.0
        gain += 20 * float(np.log10(max(mvol, 0.1) / 100))        # ползунок громкости: 100 % — как раньше, 0 % — тишина
        total = secs or dur
        # сдвиг музыки (--music-offset, секунды): > 0 — ролик начинается с этой секунды трека, < 0 — музыка стартует позже
        moff = float(sys.argv[sys.argv.index("--music-offset") + 1]) if "--music-offset" in sys.argv else 0.0
        cmd += ["-stream_loop", "-1"] + (["-ss", f"{moff:.2f}"] if moff > 0 else []) + ["-i", music]
        delay = f"adelay={int(-moff * 1000)}|{int(-moff * 1000)}," if moff < 0 else ""
        fc += (f";[0:a]volume={vg:.2f}dB[v0];"
               f"[1:a]{delay}atrim=0:{total},volume={gain:.2f}dB,afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[m];"
               f"[v0][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[aout]")
        amap = "[aout]"
        print(f"голос {voice:.1f} → {target} LUFS ({vg:+.1f} дБ), музыка {gain:+.1f} дБ к файлу, на {below} дБ тише голоса")
    else:
        amap = "0:a:0"
    if secs:
        cmd += ["-t", str(secs)]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", amap,
            "-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "10M", "-c:a", "aac", "-b:a", "160k", str(out)]
    print(f"голова ходит по x от {lo:.0f} до {hi:.0f}; окно {cw}x{H} с x={x0} из {W}x{H}, передний план {fg_w}x{fg_h}, зум {zoom:g}, размытый фон: {'да' if fg_h < 1920 or fg_w < 1080 else 'нет'}")
    subprocess.run(cmd, check=True, cwd=out.parent)
    print("готово:", out)


if __name__ == "__main__":
    main()
