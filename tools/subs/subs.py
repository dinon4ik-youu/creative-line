"""Субтитры: лесенка из первых слов, дальше по одному слову в центре. Слова с таймингами — из faster-whisper (json).

  python tools/subs/subs.py <whisper.json> <out.ass>
Стиль и числа — в tools/subs/config.json (шрифт, цвет, размер, сколько слов в лесенке).
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
W, H = 1080, 1920


def load_cfg():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))["subtitles"]


def ass_color(hex_rgb):
    """#RRGGBB -> &H00BBGGRR"""
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"


def ts(t):
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def words_from_json(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for seg in d["segments"]:
        for w in seg["words"]:
            txt = re.sub(r"[^\w\-]+", "", w["word"].strip())   # без знаков препинания
            if txt:
                out.append({"t": txt, "s": float(w["start"]), "e": float(w["end"])})
    return out


def shown(text, cfg):
    return text.upper() if cfg["case"] == "upper" else text.lower() if cfg["case"] == "lower" else text


def fit_size(text, size, cfg):
    """самое большое значение ≤ size, при котором слово влезает в max_text_width (меряем по файлу шрифта)"""
    from PIL import ImageFont
    f = ImageFont.truetype(str(HERE / cfg["font_file"]), 100)
    return min(size, int(100 * cfg["max_text_width"] / f.getlength(text)))


_FONT_FILES = None


def font_file_for(family, cfg):
    """файл шрифта по имени семейства (для расчёта ширины слова): шрифты стилей из styles.json, иначе шрифт конфига"""
    global _FONT_FILES
    if _FONT_FILES is None:
        _FONT_FILES = {}
        for ov in json.loads((HERE / "styles.json").read_text(encoding="utf-8")).values():
            if ov.get("font") and ov.get("font_file"):
                _FONT_FILES[ov["font"]] = str(HERE / ov["font_file"])
    return cfg["font_file"] if family == cfg["font"] else _FONT_FILES.get(family, cfg["font_file"])


def cfg_for(a, cfg):
    """настройки слова: если у него свой шрифт — с файлом этого шрифта"""
    if a.get("font") and a["font"] != cfg["font"]:
        return {**cfg, "font": a["font"], "font_file": font_file_for(a["font"], cfg)}
    return cfg


def tags(a, cfg):
    """ASS-теги правок слова: шрифт, цвет, жирный / курсив / подчёркнутый, поворот (r — градусы по часовой, как на экране)"""
    t = ""
    if a.get("font") and a["font"] != cfg["font"]:
        t += f"\\fn{a['font']}"
    if a.get("color"):
        h = a["color"].lstrip("#")
        t += f"\\1c&H{h[4:6]}{h[2:4]}{h[0:2]}&"
    for key, tag in (("b", "b"), ("i", "i"), ("u", "u")):
        if a.get(key) is not None:
            t += f"\\{tag}{1 if a[key] else 0}"
    if a.get("r"):
        t += f"\\frz{-a['r']:g}"
    if a.get("o") is not None:                                   # непрозрачность текста, %
        t += f"\\alpha&H{round((100 - a['o']) / 100 * 255):02X}&"
    if a.get("oc"):                                              # цвет обводки
        h = a["oc"].lstrip("#")
        t += f"\\3c&H{h[4:6]}{h[2:4]}{h[0:2]}&"
    if a.get("ow") is not None:                                  # толщина обводки, px холста
        t += f"\\bord{a['ow']:g}"
    return t


def build(words, cfg, adj=None):
    """adj — правки с экрана: {"ladder": [{...} на каждое слово лесенки], "single": {...} — один на все слова после лесенки}.
    Поля: dx, dy — сдвиг в пикселях холста 1080×1920, s — масштаб размера (1 = как есть), r — поворот в градусах,
    font — семейство шрифта, color — #RRGGBB, b / i / u — жирный, курсив, подчёркнутый (true / false);
    o — непрозрачность, %; oc, ow — цвет и толщина обводки (px); gc, gi, gr — сияние: цвет, интенсивность 0–100, диапазон 0–100."""
    adj = adj or {}
    lad_adj = adj.get("ladder") or []
    single_adj = adj.get("single") or {}
    n = cfg["ladder_words"]
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
        "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: W,{cfg['font']},{cfg['size']},{ass_color(cfg['color'])},&H000000FF,{ass_color(cfg['outline_color'])},"
        f"&H80000000,{-1 if cfg['bold'] else 0},{-1 if cfg['italic'] else 0},0,0,100,100,0,0,1,{cfg['outline']},"
        f"{cfg['shadow']},5,20,20,20,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    def line(start, end, txt, y, size, wipe=None, cx=W // 2, a=None):
        """wipe — сек: слово печатается слева направо за это время (лесенка); иначе короткое проявление (одиночные).
        Сияние (gi > 0) — вторая строка под текстом: размытая широкая обводка нужного цвета."""
        a = a or {}
        blur = f"\\blur{cfg['blur']}" if cfg["blur"] else ""
        # жёсткая граница (30.10.2026): ни одна буква не ближе 10 px к краю кадра — ни при каких условиях (как в предпросмотре)
        import math
        from PIL import ImageFont
        mfont = ImageFont.truetype(cfg_for(a, cfg)["font_file"], 100)
        asc, desc = mfont.getmetrics()
        em = 100 / (asc + desc)                                  # ASS-размер — высота строки, реальные буквы меньше
        tw100 = mfont.getlength(txt) / 100 * em
        mg = cfg.get("edge_margin", 10)
        rad = math.radians(a.get("r") or 0)
        ca, sa = abs(math.cos(rad)), abs(math.sin(rad))
        ol = 2 * (a["ow"] if a.get("ow") is not None else cfg["outline"])

        def bbox(sz):
            tw, th = tw100 * sz, sz * em * 1.1
            return tw * ca + th * sa + ol, tw * sa + th * ca + ol

        bw, bh = bbox(size)
        f = min((W - 2 * mg) / bw, (H - 2 * mg) / bh, 1)
        if f < 1:
            size = int(size * f)
            bw, bh = bbox(size)
        cx = int(min(max(cx, mg + bw / 2), W - mg - bw / 2))
        y = int(min(max(y, mg + bh / 2), H - mg - bh / 2))

        def anim(pad, vpad):
            if wipe:
                from PIL import ImageFont
                tw = ImageFont.truetype(str(HERE / cfg_for(a, cfg)["font_file"]), size).getlength(txt)
                over = size * 0.35   # наклонные/рукописные шрифты выступают за ширину текста — иначе маска срезает последние буквы
                x0, x1 = int(cx - tw / 2 - pad - over), int(cx + tw / 2 + pad + over)
                top, bot = int(y - size - vpad), int(y + size + vpad)
                return f"\\clip({x0},{top},{x0},{bot})\\t(0,{int(wipe * 1000)},\\clip({x0},{top},{x1},{bot}))"
            return f"\\fad({int(cfg['fade_in'] * 1000)},0)"

        head = f"\\an5\\pos({cx},{y}){blur}{tags(a, cfg)}\\fs{size}"
        out = ""
        if a.get("gi"):
            gr = a.get("gr", 40)
            bord, gblur = gr * 0.5, gr * 0.35 + (cfg["blur"] or 0)
            h = a.get("gc", "#FFFFFF").lstrip("#")
            alpha3 = 255 - round(round(min(a["gi"], 100) * 2.2) * a.get("o", 100) / 100)    # интенсивность → непрозрачность свечения
            glow = f"\\bord{bord:g}\\3c&H{h[4:6]}{h[2:4]}{h[0:2]}&\\3a&H{alpha3:02X}&\\1a&HFF&\\shad0\\blur{gblur:g}"
            out += (f"Dialogue: 0,{ts(start)},{ts(end)},W,,0,0,0,,"
                    f"{{{head}{glow}{anim(12 + round(bord), round(bord))}}}{txt}\n")
        return out + (f"Dialogue: {1 if a.get('gi') else 0},{ts(start)},{ts(end)},W,,0,0,0,,"
                      f"{{{head}{anim(12, 0)}}}{txt}\n")

    ev = []
    lad = words[:n]
    if lad:
        ladder_end = lad[-1]["e"] + cfg["ladder_hold"]         # все слова гаснут вместе, когда прозвучало последнее
        # один размер на всю лесенку: как у самого длинного слова, которое ещё влезает в кадр
        lsize = min(fit_size(shown(w["t"], cfg), cfg["ladder_size"], cfg_for((lad_adj[i] if i < len(lad_adj) else None) or {}, cfg))
                    for i, w in enumerate(lad))
        for i, w in enumerate(lad):
            a = (lad_adj[i] if i < len(lad_adj) else None) or {}
            dur = min(max(w["e"] - w["s"], cfg["wipe_min"]), cfg["wipe_max"])
            ev.append(line(w["s"], ladder_end, shown(w["t"], cfg),
                           cfg["ladder_y"] + i * cfg["ladder_step"] + round(a.get("dy", 0)), round(lsize * a.get("s", 1)),
                           wipe=dur, cx=W // 2 + round(a.get("dx", 0)), a=a))
    rest = words[n:]
    # один размер на все слова после лесенки (как у самого длинного, которое ещё влезает в кадр); single_uniform: false — по слову
    scfg = cfg_for(single_adj, cfg)
    usize = min((fit_size(shown(w["t"], cfg), cfg["single_size"], scfg) for w in rest), default=cfg["single_size"]) \
        if cfg.get("single_uniform", True) else None
    # Правило против наплывов (30.10.2026): слово, которому не хватает времени — экран ещё занят лесенкой или предыдущим словом,
    # и до конца слова меньше min_show — встаёт в одну строку со следующим («слово1 слово2»), а не налезает и не пропадает.
    def end_of(k):
        nxt = rest[k + 1]["s"] if k + 1 < len(rest) else rest[k]["e"] + 0.3
        return max(rest[k]["e"], min(nxt, rest[k]["e"] + cfg["max_gap"]))

    min_show = cfg.get("min_show", 0.3)
    free = lad[-1]["e"] + cfg["ladder_hold"] if lad else 0.0   # когда освободится экран
    i = 0
    while i < len(rest):
        start = max(rest[i]["s"], free)
        j = i + 1 if (end_of(i) - start < min_show and i + 1 < len(rest)) else i
        end = max(end_of(j), start + 0.15)
        txt = " ".join(shown(rest[k]["t"], cfg) for k in range(i, j + 1))
        size = usize or fit_size(txt, cfg["single_size"], scfg)
        if j > i:                                               # пара шире одного слова — размер по ней
            pair = fit_size(txt, cfg["single_size"], scfg)
            base = usize or cfg["single_size"]
            if pair < base * cfg.get("stack_ratio", 0.75):      # в строку не влезает без сильного уменьшения — слова друг над другом
                ws = [shown(rest[k]["t"], cfg) for k in range(i, j + 1)]
                sz = min(base, min(fit_size(w, cfg["single_size"], scfg) for w in ws))
                # один размер на оба слова: и с учётом границы кадра (длинное слово не должно делать другое мельче)
                from PIL import ImageFont
                mf = ImageFont.truetype(cfg_for(single_adj, cfg)["font_file"], 100)
                ol = 2 * (single_adj["ow"] if single_adj.get("ow") is not None else cfg["outline"])
                asc2, desc2 = mf.getmetrics()
                lim = min((W - 2 * cfg.get("edge_margin", 10) - ol) / (mf.getlength(w) / 100 * 100 / (asc2 + desc2)) for w in ws)
                fsz = int(min(sz * single_adj.get("s", 1), lim))
                for n, w in enumerate(ws):
                    yy = cfg["single_y"] + round(single_adj.get("dy", 0)) + round((n - (len(ws) - 1) / 2) * fsz * 100 / (asc2 + desc2) * cfg.get("stack_gap", 1.0))
                    ev.append(line(start, end, w, yy, fsz,
                                   cx=W // 2 + round(single_adj.get("dx", 0)), a=single_adj))
                free, i = end, j + 1
                continue
            size = min(size, pair)
        ev.append(line(start, end, txt, cfg["single_y"] + round(single_adj.get("dy", 0)),
                       round(size * single_adj.get("s", 1)),
                       cx=W // 2 + round(single_adj.get("dx", 0)), a=single_adj))
        free, i = end, j + 1
    return head + "".join(ev)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cfg = load_cfg()
    Path(sys.argv[2]).write_text(build(words_from_json(sys.argv[1]), cfg), encoding="utf-8-sig")
    print("готово:", sys.argv[2])


if __name__ == "__main__":
    main()
