"""«Саша (❤️)» (28.10.2026): видео → кадр 9:16 + холодный цветокор + пословные субтитры + музыка.
Шаги: вырезка куска с запасом ±MARGIN с → расшифровка (Whisper, слова с таймингами) → ролик собирается сам →
мелкие правки (текст, зум, границы куска в пределах запаса, музыка из библиотеки) → «Пересобрать».
Сама картинка и звук делаются скриптами tools/subs (make.py, subs.py, config.json); здесь — очередь и шаги.
Без Telegram и без app.py: сохранение состояния приходит снаружи (save).

Времена слов (`words`) — по файлу куска с запасом (`src`); в ролик идёт только отрезок [ta, tb] этого файла.
"""
import json
import re
import shutil
import sys
import threading
from pathlib import Path

import core

SUBS = core.TOOLS / "subs"
sys.path.insert(0, str(SUBS))
import fixtext  # noqa: E402

BUSY = ("download", "transcribe", "render")
MARGIN = 3.0      # сколько секунд запаса качаем с каждой стороны выбранного куска (в ролик не входит, пока не подтянешь)
MIN_LEN = 1.0     # короче этого кусок сделать нельзя
MUSIC_DIR = core.DATA / "music"   # библиотека треков: сюда же «добавить трек»
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MUSIC_DEFAULT = ""   # трек по умолчанию выбирается на экране
STYLE_DEFAULT = "Классика"
FILTER_DEFAULT = "Холодный"
OUT_NAME = "Саша"   # папка готовых роликов в out_dir; на экране — «Саша (❤️)», эмодзи в имени папки не нужен


def _styles_all():
    """styles.json без стилей, у которых нет файла шрифта (шрифты в репозиторий не входят — см. tools/subs/fonts/README.md)."""
    d = json.loads((SUBS / "styles.json").read_text(encoding="utf-8"))
    return {n: ov for n, ov in d.items() if not ov.get("font_file") or (SUBS / ov["font_file"]).is_file()}


def python_exe():
    exe = Path(sys.executable)
    return exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe


class Sasha:
    def __init__(self, saved, save, cfg):
        self.s = saved if saved and "items" in saved else {"items": [], "cur": None, "seq": 0,
                                                            "music": "", "use_music": False}
        resume_ids = []
        for e in self.s["items"]:
            e.setdefault("zoom", 1.0)
            if e.get("words"):
                e.setdefault("words0", json.loads(json.dumps(e["words"])))   # как расшифровал Whisper — для сброса
            if e["status"] in BUSY:   # приложение закрыли посреди работы — продолжаем сами (не больше двух раз подряд)
                if e.get("resumes", 0) >= 2:
                    e.update(status="error", error="работа прерывалась несколько раз подряд — нажми «повторить»")
                else:
                    e["resumes"] = e.get("resumes", 0) + 1
                    resume_ids.append(e["id"])
        self.save = save
        self.cfg = cfg
        self.out_dir = Path(cfg["out_dir"]) / OUT_NAME
        self.work = core.DATA / "sasha"
        self.version = 0
        self._waves = {}
        self.heavy = threading.Lock()      # расшифровка и рендер — по одному (видеокарта 6 ГБ)
        for e in self.s["items"]:          # куски из прошлых запусков: длительность и окно кадра — досчитать
            if e["src"] and e["status"] != "error":
                threading.Thread(target=self._prepare, args=(e["id"],), daemon=True).start()
        for eid in resume_ids:
            threading.Thread(target=self._resume, args=(eid,), daemon=True).start()

    def _resume(self, eid):
        """Продолжить прерванную перезапуском работу с того шага, на котором остановились."""
        e = self.entry(eid)
        if not e:
            return
        if e["status"] == "render" and e["words"]:
            self._render(eid)
        else:
            e["status"] = "download" if e["url"] and not e["src"] else "transcribe"
            self.changed()
            self._start(eid)

    def _prepare(self, eid):
        self._duration(eid)
        self._frame(eid)

    def _duration(self, eid):
        """Длительность файла куска и границы по умолчанию (весь файл)."""
        e = self.entry(eid)
        if not e or e.get("dur") or not e["src"]:
            return
        try:
            e["dur"] = round(core.probe(e["src"])["duration"], 2)
        except Exception:  # noqa: BLE001
            core.log.exception("sasha: длительность")
            e["dur"] = round(max((w["e"] for w in e["words"]), default=1.0), 2)
        e.setdefault("ta", 0.0)
        e["tb"] = min(e.get("tb") or e["dur"], e["dur"])
        self.changed()

    def _frame(self, eid):
        """Автоокно кадра для предпросмотра 9:16 (то же, что считает рендер): ищем лицо по куску, секунды.
        Не нашли / не вышло — frame = {}, предпросмотр показывает по центру."""
        e = self.entry(eid)
        if not e or e.get("frame") or not e["src"]:
            return
        try:
            import make
            e["frame"] = make.auto_window(e["src"]) or {}
        except Exception:  # noqa: BLE001
            core.log.exception("sasha: окно кадра")
            e["frame"] = {}
        self.changed()

    def set_zoom(self, eid, zoom):
        e = self.entry(eid)
        if e:
            e["zoom"] = round(max(0.3, min(float(zoom), 4.0)), 2)
            self.changed()

    def changed(self):
        self.version += 1
        self.save()

    def entry(self, eid):
        return next((e for e in self.s["items"] if e["id"] == eid), None)

    def active(self):
        e = self.entry(self.s.get("cur"))
        if not e and self.s["items"]:
            e = self.s["items"][-1]
            self.s["cur"] = e["id"]
        return e

    def select(self, eid):
        if self.entry(eid):
            self.s["cur"] = eid
            self.changed()

    # ---------- музыка: общая библиотека и выбор у каждого куска ----------

    def tracks(self):
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(f.name for f in MUSIC_DIR.iterdir() if f.suffix.lower() in AUDIO_EXT)

    def add_track(self, path, name=None):
        """Копия трека в библиотеку. → (имя в библиотеке, ошибка)."""
        src = Path(str(path).strip().strip('"'))
        if not src.is_file() or src.suffix.lower() not in AUDIO_EXT:
            return None, f"это не аудиофайл: {src}"
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        dst = MUSIC_DIR / re.sub(r'[\\/:*?"<>|]+', " ", name or src.name)
        i = 1
        while dst.exists() and dst.resolve() != src.resolve():
            dst = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
            i += 1
        if not dst.exists():
            shutil.copy2(src, dst)
        self.changed()
        return dst.name, None

    def default_music(self):
        """Имя трека по умолчанию для новых кусков ('' — без музыки)."""
        return Path(self.s["music"]).name if self.s["use_music"] and self.s["music"] else ""

    def music_of(self, e):
        m = e.get("music")
        return self.default_music() if m is None else m

    def set_music(self, path=None, use=None):
        """Общая музыка по умолчанию (для кусков, у которых выбор не задан)."""
        if path is not None:
            self.s["music"] = path.strip().strip('"')
        if use is not None:
            self.s["use_music"] = bool(use)
        self.changed()

    def wave(self, name):
        """Огибающая трека для дорожки музыки на таймлинии: {"dur": секунды, "peaks": [0..1] ×600}. Кэш на время работы."""
        f = MUSIC_DIR / name
        if name not in self.tracks():
            return None
        key = (name, f.stat().st_mtime)
        if key not in self._waves:
            import subprocess
            import numpy as np
            raw = subprocess.run([str(core.FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(f), "-ac", "1", "-ar", "4000",
                                  "-f", "s16le", "-"], capture_output=True, creationflags=core.NO_WINDOW).stdout
            a = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32))
            n = 600
            peaks = [float(x) for x in (np.array([c.max() if len(c) else 0 for c in np.array_split(a, n)]) / max(a.max(), 1))]
            self._waves[key] = {"dur": round(len(a) / 4000, 2), "peaks": [round(p, 3) for p in peaks]}
        return self._waves[key]

    def vol_of(self, e):
        """Громкость музыки, %: 100 — на 8 дБ тише голоса (как было), 0 — тишина, 200 — на 6 дБ громче."""
        return int(max(0, min(200, e.get("music_vol", 100))))

    def set_music_vol(self, eid, vol):
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        e["music_vol"] = int(max(0, min(200, float(vol))))
        self.changed()
        return None

    def set_music_off(self, eid, off):
        """Сдвиг музыки (секунды): > 0 — в начале ролика играет эта секунда трека, < 0 — музыка стартует позже на столько."""
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        e["music_off"] = round(float(off), 2)
        self.changed()
        return None

    def set_item_music(self, eid, name):
        """Трек этого куска: имя из библиотеки, '' — без музыки."""
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        if name and name not in self.tracks():
            return f"в библиотеке нет трека «{name}»"
        e["music"] = name or ""
        e["music_off"] = 0.0   # другой трек — сдвиг прежнего трека не нужен
        self.changed()
        return None

    # ---------- стиль субтитров: список в tools/subs/styles.json (имя → что поменять поверх config.json) ----------

    def styles(self):
        return list(_styles_all())

    def style_of(self, e):
        return e.get("style") if e.get("style") in self.styles() else STYLE_DEFAULT

    def set_style(self, eid, name):
        e = self.entry(eid)
        if not e or name not in self.styles():
            return f"нет такого стиля: {name}"
        e["style"] = name
        self.changed()
        return None

    def set_sub_adj(self, eid, adj):
        """Положение и размер субтитров, подвинутые на экране: {"ladder": [{dx, dy, s}, …], "single": {dx, dy, s}}.
        dx, dy — пиксели холста 1080×1920, s — масштаб. Пустые записи отбрасываем."""
        e = self.entry(eid)
        if not e or not isinstance(adj, dict):
            return "нет такого куска"

        fams = {f["family"] for f in self.fonts()}

        def clean(a):
            a = a if isinstance(a, dict) else {}
            out = {k: round(float(a[k]), 3 if k == "s" else 1 if k == "r" else 0) for k in ("dx", "dy", "s", "r")
                   if a.get(k) not in (None, 0, 0.0) and not (k == "s" and a[k] in (1, 1.0))}
            if a.get("font") in fams:
                out["font"] = a["font"]
            for ck in ("color", "oc", "gc"):                  # цвета: текст, обводка, сияние
                c = str(a.get(ck) or "")
                if len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
                    out[ck] = c.upper()
            for nk, lo, hi in (("o", 0, 100), ("ow", 0, 40), ("gi", 0, 100), ("gr", 0, 100)):   # непрозрачность, обводка, сияние
                if a.get(nk) is not None:
                    v = max(lo, min(hi, round(float(a[nk]), 1)))
                    if not (nk == "o" and v == 100) and not (nk == "gi" and v == 0):
                        out[nk] = v
            for k in ("b", "i", "u"):
                if isinstance(a.get(k), bool):
                    out[k] = a[k]
            return out

        lad = [clean(a) for a in (adj.get("ladder") or [])]
        while lad and not lad[-1]:
            lad.pop()
        out = {}
        if any(lad):
            out["ladder"] = lad
        if clean(adj.get("single")):
            out["single"] = clean(adj.get("single"))
        e["sub_adj"] = out
        self.changed()
        return None

    def font_css(self):
        """@font-face для всех шрифтов стилей (предпросмотр и список стилей рисуют ими). Имя семейства — как в стиле."""
        css, seen = [], set()
        styles = _styles_all()
        from urllib.parse import quote
        for ov in styles.values():
            f = SUBS / ov.get("font_file", "")
            if ov.get("font") and f.is_file() and f.parent == SUBS / "fonts" and ov["font"] not in seen:
                seen.add(ov["font"])
                css.append("@font-face{font-family:'%s';src:url('/sasha-font/%s')}" % (ov["font"], quote(f.name)))
        return "".join(css)

    def fonts(self):
        """Шрифты, которые можно выбрать для слова: [{name (как в списке стилей), family (для ASS/браузера)}]; Arial Black — базовый."""
        base = json.loads((SUBS / "config.json").read_text(encoding="utf-8"))["subtitles"]["font"]
        out, seen = [{"name": base, "family": base}], {base}
        for name, ov in _styles_all().items():
            if ov.get("font") and ov["font"] not in seen:
                seen.add(ov["font"])
                out.append({"name": name, "family": ov["font"]})
        return out

    def font_ems(self):
        """{семейство: em_ratio} — перевод ASS-размера (высота строки) в размер буквы для предпросмотра."""
        from PIL import ImageFont
        base = json.loads((SUBS / "config.json").read_text(encoding="utf-8"))["subtitles"]
        files = {base["font"]: base["font_file"]}
        for ov in _styles_all().values():
            if ov.get("font") and ov.get("font_file"):
                files[ov["font"]] = ov["font_file"]
        out = {}
        for fam, f in files.items():
            asc, desc = ImageFont.truetype(str(SUBS / f), 1000).getmetrics()
            out[fam] = round(1000 / (asc + desc), 4)
        return out

    def style_fonts(self):
        """{имя стиля: семейство шрифта} — чтобы список стилей показывал каждый его шрифтом."""
        base = json.loads((SUBS / "config.json").read_text(encoding="utf-8"))["subtitles"]["font"]
        styles = _styles_all()
        return {name: ov.get("font", base) for name, ov in styles.items()}

    # ---------- фильтр ролика (tools/subs/filters.json) ----------

    def filters(self):
        return list(json.loads((SUBS / "filters.json").read_text(encoding="utf-8")))

    def filter_of(self, e):
        return e.get("filter") if e.get("filter") in self.filters() else FILTER_DEFAULT

    def fint_of(self, e):
        return int(max(0, min(100, e.get("filter_int", 100))))

    def set_filter(self, eid, name=None, intensity=None):
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        if name is not None:
            if name not in self.filters():
                return f"нет такого фильтра: {name}"
            e["filter"] = name
        if intensity is not None:
            e["filter_int"] = int(max(0, min(100, float(intensity))))
        self.changed()
        return None

    def sub_cfg(self, e):
        """Настройки субтитров куска для предпросмотра: (config.json + стиль, субтитры выключены?)."""
        cfg = json.loads((SUBS / "config.json").read_text(encoding="utf-8"))["subtitles"]
        ov = dict(_styles_all().get(self.style_of(e), {}))
        off = bool(ov.pop("none", False))
        cfg = {**cfg, **ov}
        # ASS-размер шрифта — высота строки (подъём + спуск), а в браузере font-size — размер буквы:
        # переводим, иначе предпросмотр рисует текст в ~1.4 раза крупнее, чем в ролике
        from PIL import ImageFont
        asc, desc = ImageFont.truetype(str(SUBS / cfg["font_file"]), 1000).getmetrics()
        cfg["em_ratio"] = round(1000 / (asc + desc), 4)
        return cfg, off

    # ---------- очередь ----------

    def add(self, text):
        """Строки: «ссылка 1:20-1:45 2:10-2:40» (каждый отрезок «от-до» — отдельный кусок; качаем с запасом ±5 с,
        чтобы потом можно было расширить), «ссылка до 58» / «ссылка -5 -3» (как в «Загрузках»), «ссылка» (целиком)
        или путь к видео на диске. → (сколько кусков добавлено, ошибки)."""
        errors, jobs = [], []
        for line in (text or "").splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            if Path(line).is_file():
                jobs.append((None, {}, Path(line)))
                continue
            m = core.URL_RE.search(line)
            if not m:
                errors.append(f"не ссылка и не файл: {line[:60]}")
                continue
            url, rest = core.norm_url(m[1]), line[m.end():]
            try:
                pairs = re.findall(rf"({core.TIME})\s*-\s*({core.TIME})", rest.replace("—", "-").replace("–", "-"))
                cuts = [core.parse_cut(f"{a}-{b}") for a, b in pairs] or [core.parse_cut(rest)]
            except core.CutError as e:
                errors.append(f"{line[:60]}: {e}")
                continue
            jobs += [(url, c, None) for c in cuts]
        for url, cut, path in jobs:
            self.s["seq"] += 1
            e = {"id": self.s["seq"], "url": url, "cut": cut, "src": str(path) if path else "",
                 "name": path.stem if path else f"{self.s['seq']} {core.cut_label(cut)}".replace(":", "."),
                 "status": "download" if url else "transcribe", "error": "", "words": [], "text": "", "out": "",
                 "zoom": 1.0}
            self._apply_defaults_to(e)               # новый кусок сразу получает стиль по умолчанию, если он запомнен
            if cut.get("start") is not None and cut.get("end") is not None:   # «от-до» — берём с запасом
                a = max(0.0, cut["start"] - MARGIN)
                e["dl_cut"] = {"start": a, "end": cut["end"] + MARGIN}
                e["ta"], e["tb"] = round(cut["start"] - a, 2), round(cut["end"] - a, 2)
            self.s["items"].append(e)
            self.s["cur"] = e["id"]
            threading.Thread(target=self._start, args=(e["id"],), daemon=True).start()
        if jobs:
            self.changed()
        return len(jobs), errors

    def _start(self, eid):
        e = self.entry(eid)
        if e["url"] and not e["src"]:
            try:
                work = Path(self.cfg["tmp_dir"]) / f"sasha{eid}"
                cut = e.get("dl_cut") or e["cut"]
                try:
                    out, p = self._fetch(e["url"], cut, work)
                except core.JobError as ex:
                    if "в видео всего" not in str(ex) or "dl_cut" not in e:   # запас справа вылез за конец видео
                        raise
                    cut = {"start": cut["start"], "end": e["cut"]["end"]}
                    out, p = self._fetch(e["url"], cut, work)
                keep = self.work / str(eid)
                keep.mkdir(parents=True, exist_ok=True)
                e["src"] = str(shutil.copy2(out, keep / "cut.mp4"))
                e["name"] = re.sub(r'[\\/*?"<>|]+', " ", f"{p.get('streamer') or 'видео'} "
                                   f"{core.cut_label(e['cut'])}".replace(":", ".")).strip()
                e["status"] = "transcribe"
                self.changed()
            except Exception as ex:  # noqa: BLE001
                core.log.exception("sasha: загрузка")
                e.update(status="error", error=str(ex)[:300])
                self.changed()
                return
        self._duration(eid)
        self._frame(eid)
        self._transcribe(eid)

    def _fetch(self, url, cut, work):
        """Скачать и вырезать кусок. Длинное видео, которое общий загрузчик не режет (YouTube), — через yt-dlp."""
        try:
            return core.process(url, cut, self.cfg, work)
        except core.JobError as ex:
            if "длинного видео не вырезать" not in str(ex) or cut.get("end") is None:
                raise
            return self._section(url, cut, work)

    def remove(self, eid):
        e = self.entry(eid)
        if e and e["status"] not in BUSY:
            self.s["items"].remove(e)
            shutil.rmtree(self.work / str(eid), ignore_errors=True)
            self.changed()

    def retry(self, eid):
        e = self.entry(eid)
        if e and e["status"] == "error":
            e.update(status="download" if e["url"] and not e["src"] else "transcribe", error="")
            self.changed()
            threading.Thread(target=self._start, args=(eid,), daemon=True).start()

    # ---------- шаги ----------

    def _section(self, url, cut, work):
        """Кусок длинного видео через yt-dlp --download-sections (качает только нужные секунды). → (файл, сведения)."""
        work.mkdir(parents=True, exist_ok=True)
        a, b = cut.get("start", 0.0), cut["end"]
        core.ytdlp(["--no-playlist", "--no-warnings", "--no-progress", "--download-sections", f"*{a:.2f}-{b:.2f}",
                    "--force-keyframes-at-cuts", "-S", "res:1080,fps,vcodec:h264,acodec:aac",
                    "--merge-output-format", "mp4", "--ffmpeg-location", core.FFMPEG.parent,
                    "-o", str(work / "sec.%(ext)s")], url, 1800)
        out = next(iter(sorted(work.glob("sec.*"))), None)
        if not out:
            raise core.JobError("не вырезал кусок")
        return out, {"streamer": None}

    def _transcribe(self, eid):
        e = self.entry(eid)
        with self.heavy:
            try:
                exe = core.FFMPEG.parent / "faster-whisper-xxl.exe"
                d = self.work / str(eid)
                d.mkdir(parents=True, exist_ok=True)
                for old in d.glob("*.json"):
                    old.unlink()
                r = core.run([exe, e["src"], "--model", "large-v3", "--model_dir", core.FFMPEG.parent / "_models",
                              "--output_format", "json", "--output_dir", d, "--word_timestamps", "True",
                              "--vad_filter", "True"], 1800)
                f = next(iter(d.glob("*.json")), None)
                if not f:
                    raise RuntimeError("Whisper не дал результата: " + (r.stderr or r.stdout)[-300:])
                words = []
                for seg in json.loads(f.read_text(encoding="utf-8"))["segments"]:
                    for w in seg.get("words", []):
                        t = re.sub(r"[^\w\-]+", "", w["word"].strip())
                        if t:
                            words.append({"t": t, "s": float(w["start"]), "e": float(w["end"])})
                if not words:
                    raise RuntimeError("в видео не нашлось речи")
                e["words"] = words
                e["words0"] = json.loads(json.dumps(words))
                self._sync_text(e)
                e["status"] = "render"   # сразу собираем целиком (зум ×1, текст Whisper); правки — потом, из готового
            except Exception as ex:  # noqa: BLE001
                core.log.exception("sasha: расшифровка")
                e.update(status="error", error=str(ex)[:300])
        self.changed()
        if e["status"] == "render":
            self._render(eid)

    # ---------- текст и границы ----------

    def range_idx(self, e):
        """Номера слов, которые попадают в отрезок [ta, tb] (по середине слова)."""
        ta, tb = e.get("ta", 0.0), e.get("tb") or 1e9
        return [i for i, w in enumerate(e["words"]) if ta <= (w["s"] + w["e"]) / 2 <= tb]

    def _sync_text(self, e):
        e["text"] = fixtext.words_to_text([e["words"][i] for i in self.range_idx(e)])

    def set_text(self, eid, text):
        """Исправленный текст отрезка (слова в запасе за границами не трогаем). → ошибка или None."""
        e = self.entry(eid)
        if not e or e["status"] not in ("edit", "done", "error") or not e["words"]:
            return "сейчас текст править нельзя"
        if not text.split():
            return "текст пустой"
        idx = self.range_idx(e)
        new = fixtext.apply([e["words"][i] for i in idx], text)
        first = idx[0] if idx else len(e["words"])
        e["words"] = e["words"][:first] + new + e["words"][first + len(idx):]
        self._sync_text(e)
        self.changed()
        return None

    def reset_text(self, eid):
        """Слова обратно как расшифровал Whisper (границы куска остаются)."""
        e = self.entry(eid)
        if e and e.get("words0"):
            e["words"] = json.loads(json.dumps(e["words0"]))
            self._sync_text(e)
            self.changed()

    def set_trim(self, eid, ta, tb):
        """Границы отрезка в пределах скачанного (с запасом). → ошибка или None. Текст пересобирается из слов."""
        e = self.entry(eid)
        if not e or not e.get("dur"):
            return "кусок ещё не готов"
        ta, tb = max(0.0, float(ta)), min(float(e["dur"]), float(tb))
        if tb - ta < MIN_LEN:
            return f"короче {MIN_LEN:g} с нельзя"
        e["ta"], e["tb"] = round(ta, 2), round(tb, 2)
        self._sync_text(e)
        self.changed()
        return None

    # ---------- стиль по умолчанию и одобрение ----------

    def _sig(self, e):
        """Отпечаток всего, что влияет на ролик. Если после сборки он изменился — правки ещё не попали в файл."""
        idx = self.range_idx(e)
        key = {"z": e.get("zoom", 1.0), "ta": e.get("ta"), "tb": e.get("tb"), "m": self.music_of(e), "mo": e.get("music_off", 0.0), "mv": self.vol_of(e),
               "st": self.style_of(e), "adj": e.get("sub_adj") or {}, "f": self.filter_of(e), "fi": self.fint_of(e),
               "w": [(e["words"][i]["t"], round(e["words"][i]["s"], 2), round(e["words"][i]["e"], 2)) for i in idx]}
        return json.dumps(key, sort_keys=True, ensure_ascii=False)

    def is_dirty(self, e):
        return bool(e.get("rsig")) and e["rsig"] != self._sig(e)

    def save_defaults(self, eid):
        """Запомнить вид этого куска (положение, шрифт, цвет и размер субтитров, стиль, музыка) для всех новых кусков.
        Текст, границы и зум у каждого куска свои и не запоминаются; сдвиг и громкость музыки запоминаются."""
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        import datetime
        self.s["defaults"] = {"adj": json.loads(json.dumps(e.get("sub_adj") or {})), "style": self.style_of(e),
                              "music": self.music_of(e), "moff": e.get("music_off", 0.0), "mvol": self.vol_of(e), "filter": self.filter_of(e), "fint": self.fint_of(e), "from": e["name"],
                              "at": datetime.datetime.now().isoformat(timespec="minutes")}
        self.changed()
        return None

    def _apply_defaults_to(self, e):
        d = self.s.get("defaults")
        if not d:
            return
        e["sub_adj"] = json.loads(json.dumps(d["adj"]))
        e["style"] = d["style"]
        e["music"] = d["music"]
        e["music_off"] = d.get("moff", 0.0)          # сдвиг и громкость музыки тоже входят в стиль по умолчанию
        e["music_vol"] = d.get("mvol", 100)
        if d.get("filter"):
            e["filter"], e["filter_int"] = d["filter"], d.get("fint", 100)

    def clear_defaults(self):
        self.s.pop("defaults", None)
        self.changed()

    def count_unapproved(self):
        return sum(1 for e in self.s["items"] if not e.get("approved") and e["status"] != "render")

    def apply_defaults(self):
        """Стиль по умолчанию — на все непроверенные куски (одобренные не трогаем), готовые пересобираются. → сколько кусков."""
        n = 0
        for e in self.s["items"]:
            if e.get("approved") or e["status"] == "render":
                continue
            self._apply_defaults_to(e)
            n += 1
            if e["status"] in ("edit", "done", "error") and e["words"]:
                self.render(e["id"])
        self.changed()
        return n

    def _unapprove(self, e):
        e["approved"] = False
        if e.get("approved_file"):
            Path(e["approved_file"]).unlink(missing_ok=True)
        e["approved_file"] = ""

    def approve(self, eid, value=True):
        """Одобрить готовый кусок: копия файла уходит в `<out_dir>\Саша\одобрено\`. Снять одобрение — копия удаляется.
        Нельзя одобрить, пока в файл не попали последние правки. → ошибка или None."""
        e = self.entry(eid)
        if not e:
            return "нет такого куска"
        if not value:
            self._unapprove(e)
            self.changed()
            return None
        if e["status"] != "done" or not Path(e["out"]).is_file():
            return "ролик ещё не собран"
        if not e.get("rsig"):
            return "этот кусок собран до появления проверки — нажми «Пересобрать» один раз, чтобы я знал, что в файле последние правки"
        if self.is_dirty(e):
            return "есть правки, которых нет в файле — нажми «Пересобрать»"
        dst = self.out_dir / "одобрено" / Path(e["out"]).name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(e["out"], dst)
        e["approved"], e["approved_file"] = True, str(dst)
        self.changed()
        return None

    def next_pending(self, after_id):
        """Следующий готовый неодобренный кусок после этого (по кругу) или None."""
        items = self.s["items"]
        order = [x for x in items if x["id"] > after_id] + [x for x in items if x["id"] < after_id]
        return next((x["id"] for x in order if x["status"] == "done" and not x.get("approved")), None)

    # ---------- сборка ----------

    def render(self, eid):
        """Собрать ролик из текущих слов, границ, зума и музыки. → ошибка или None."""
        e = self.entry(eid)
        if not e or e["status"] not in ("edit", "done", "error") or not e["words"]:
            return "нечего собирать"
        name = self.music_of(e)
        if name and not (MUSIC_DIR / name).is_file():
            return f"нет файла музыки {MUSIC_DIR / name}"
        self._unapprove(e)                           # файл изменится — прежнее одобрение недействительно
        e.update(status="render", error="", out="")
        self.changed()
        threading.Thread(target=self._render, args=(eid,), daemon=True).start()
        return None

    def _render(self, eid):
        e = self.entry(eid)
        with self.heavy:
            try:
                e["rsig"] = self._sig(e)             # отпечаток настроек на момент сборки: после него правки считаются несохранёнными
                d = self.work / str(eid)
                ta, tb = e.get("ta", 0.0), e.get("tb") or e.get("dur")
                words = [{"t": e["words"][i]["t"], "s": max(e["words"][i]["s"] - ta, 0.0),
                          "e": min(e["words"][i]["e"] - ta, tb - ta)} for i in self.range_idx(e)]
                if not words:
                    raise RuntimeError("в выбранном отрезке нет слов")
                wjson = d / "words.json"
                fixtext.write_whisper_json(words, wjson)
                out = d / "out.mp4"
                cmd = [python_exe(), SUBS / "make.py", e["src"], wjson, out, "--zoom", e.get("zoom", 1.0),
                       "--ss", ta, "--seconds", tb - ta,
                       "--style", self.style_of(e), "--filter", self.filter_of(e), "--filter-int", self.fint_of(e)]
                if e.get("sub_adj"):
                    adj_file = d / "adj.json"
                    adj_file.write_text(json.dumps(e["sub_adj"]), encoding="utf-8")
                    cmd += ["--adj", adj_file]
                name = self.music_of(e)
                if name:
                    cmd += ["--music", MUSIC_DIR / name, "--music-offset", e.get("music_off", 0.0), "--music-vol", self.vol_of(e)]
                r = core.run(cmd, 3600)
                if r.returncode or not out.exists():
                    raise RuntimeError((r.stderr or r.stdout)[-400:])
                self.out_dir.mkdir(parents=True, exist_ok=True)
                dest = self.out_dir / f"{e['name']}_sasha.mp4"
                shutil.copy2(out, dest)
                e.update(status="done", out=str(dest), resumes=0)
            except Exception as ex:  # noqa: BLE001
                core.log.exception("sasha: рендер")
                e.update(status="error", error=str(ex)[:400])
        self.changed()
