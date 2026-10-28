"""Ядро линии креативов: всё, что не зависит от Telegram (25.10.2026). Им пользуются бот-качалка (bot.py)
и настольное приложение — одна и та же логика, правится в одном месте.

Здесь: настройки и пути (у Димы и в наборе), разбор ссылок и обрезки, скачивание и рез (process), моменты стрима
Kick/Twitch и превью 360p, классический креатив 9:16 (make_creative), копия в out_dir, запуск прогона Edits на айфоне.
Ошибки для человека — JobError с причиной простыми словами.
"""
import asyncio
import html
import json
import logging
import msvcrt
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

# На чистой Windows в системном хранилище нет корня GoDaddy G2 (api.telegram.org): Windows догружает
# его сама, Python — нет, и urlopen падает на сертификате. certifi ставит install.ps1 (песочница 17.10.2026).
# ffmpeg (gnutls) упирается в то же самое на clips.kick.com — ему тот же файл через -ca_file.
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
    FF_TLS = ["-ca_file", certifi.where()]
except ImportError:
    FF_TLS = []

HERE = Path(__file__).resolve().parent
# Набор для других (share/downloader-bot-kit): рядом лежит kit.json — свои настройки в <набор>/data,
# программы в <набор>/bin, обновления сами с Диска Димы. Без kit.json — бот Димы в tools/shared_bot.
KIT_FILE = HERE / "kit.json"
KIT = json.loads(KIT_FILE.read_text(encoding="utf-8")) if KIT_FILE.exists() else None
if KIT:
    ROOT = HERE.parent
    DATA = ROOT / "data"
    TOOLS = None
    TOKEN_FILE = DATA / "token.txt"
    TG_CONFIG = DATA / "api.json"
    BIN = ROOT / "bin"
    FFMPEG = BIN / "ffmpeg.exe"
    KICK_CACHE = DATA / "kick_cache"
else:
    ROOT = DATA = HERE
    TOOLS = HERE.parent
    TOKEN_FILE = TOOLS / "shared-bot-token.txt"
    TG_CONFIG = TOOLS / "tg-config.json"
    BIN = HERE / "bin"
    FFMPEG = TOOLS / "fw" / "Faster-Whisper-XXL" / "ffmpeg.exe"
    KICK_CACHE = None  # как в kick_clips.py: tools/kick_cache
CONFIG = DATA / "config.json"
STATE = DATA / "state.json"
STOP_FLAG = DATA / "stop.flag"
LOCK = DATA / "bot.lock"
LOG = DATA / "bot.log"
SESSION = DATA / "bot"
YTDLP = BIN / "yt-dlp.exe"
YTDLP_PREV = BIN / "yt-dlp.prev.exe"
OWNER = "владелец"  # имя владельца в ошибках «… посмотрит»; из config.json (owner_name)

DEFAULTS = {
    "group_id": None,           # id группы (--setup / --wizard)
    "owner_id": None,           # кто может /stop (по умолчанию — chat_id Димы из tg-config.json)
    "owner_name": "владелец",
    "out_dir": str(ROOT / "clips"),  # копия каждого клипа
    "tmp_dir": str(ROOT / "tmp"),      # временные, чистятся после отправки
    "status_every": 60,         # как часто обновлять закреп, с
    "stale_after_min": 2,       # «если время старше N мин — бот выключен»
    "max_full_min": 15,         # длиннее — только с отрезком (записи стримов)
    "max_piece_min": 15,        # самый длинный отрезок
}

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: ffmpeg/yt-dlp без чёрных окон

PICK_TOPIC = "Выбор клипов"
DONE_TOPIC = "Отобранные"
PICK_BATCH = 10           # моментов за раз
PICK_MIN_CLIPS = 2        # момент, который заклипали один раз, — не показываем
KICK_CACHE_MAX_H = 1      # сохранённые клипы стрима старше часа — собрать заново (клипы ещё добавляются)
OK_WORDS = {"ок", "ok", "+", "да", "беру", "👍"}
EDITS_REPLY_RE = re.compile(r"^(\d{1,2}|повтори|стоп)$")  # ответы прогону Edits на iPhone
EDITS_WORDS = {"edits", "в edits", "эдитс", "едитс", "📱"}  # ответ на видео в «Отобранных» → прогнать через Edits
EDITS_GATHER_S = 20       # айфон свободен — подождать ещё нажатий столько секунд после последнего, потом пачкой

HINT = ("<b>Как пользоваться</b>\n"
        "Ссылка — видео целиком. Обрезка — на той же строке после ссылки:\n"
        "<code>-14</code> — убрать первые 14 с\n"
        "<code>-5 -3</code> — убрать 5 с в начале и 3 с в конце\n"
        "<code>до 58</code> — оставить только до 58-й секунды\n"
        "<code>1:20-1:45</code> — только этот кусок\n"
        "Время: <code>80</code> — секунды, <code>1:20</code> — мин:сек, <code>1:20:00</code> — ч:мин:сек\n"
        "<pre>kick.com/…/clip_AAA -14\n"
        "kick.com/…/videos/BBB 1:20:00-1:20:10</pre>\n"
        "<b>Выбор клипов</b>: ссылка на запись стрима Kick или Twitch → превью моментов. Кнопка «✅ Беру целиком» "
        "под превью (или ответ «ок») — клип в «Отобранные»; с обрезкой — ответь на превью: "
        "<code>-1 -5</code>, <code>до 20</code>. Ответь на превью текстом — придёт готовый креатив 9:16 с этой "
        "подписью (можно с обрезкой: <code>-1 -5 суд над гамосом</code>). «➡️ Ещё» — следующие моменты.")

URL_RE = re.compile(
    r"(https?://\S+|(?:www\.)?(?:kick\.com|(?:vm\.|vt\.)?tiktok\.com|instagram\.com|youtube\.com|"
    r"youtu\.be|(?:clips\.)?twitch\.tv)/\S+)", re.I)
TIME = r"\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?"

log = logging.getLogger("bot")


# ---------- настройки и состояние ----------

def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(p, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_config():
    c = dict(DEFAULTS)
    c.update(load_json(CONFIG, {}))
    if not c["owner_id"]:
        c["owner_id"] = load_json(TG_CONFIG, {}).get("chat_id")
    if c.get("edits_topic") is not None:  # айфон + Edits включены — недостающее по умолчанию (21.10.2026, для набора)
        titles = {"title": "Прогон Edits", "ask_title": "Вопросы прогона", "drop_title": "Кинуть в Edits"}
        if not c["edits_topic"]:  # install-iphone.ps1 пишет {} — названия тем в файл: их читают скрипты айфона
            raw = load_json(CONFIG, {})
            raw["edits_topic"] = c["edits_topic"] = dict(titles)
            save_json(CONFIG, raw)
        wda = (HERE / "wda") if KIT else (TOOLS / "wda")
        wdata = (DATA / "wda") if KIT else wda
        et = {**titles, "script": str(wda / "edits_iphone.py"), "jobs_dir": str(ROOT / "edits-jobs"),
              "replies": str(wdata / "replies.txt"), "data_dir": str(wdata), **c["edits_topic"]}
        c["edits_topic"] = et
    if not CONFIG.exists():
        save_json(CONFIG, c)
    return c


# ---------- разбор сообщения ----------

class CutError(Exception):
    pass


def secs(t):
    """«1:20:00», «1:20», «80», «12,5» → секунды. Лишние нули прощаем, 1:75 — нет."""
    parts = t.replace(",", ".").split(":")
    for p in parts[1:]:
        if float(p) >= 60:
            raise CutError(f"непонятный таймкод «{t}»")
    v = 0.0
    for p in parts:
        v = v * 60 + float(p)
    return v


def parse_cut(rest):
    """Текст после ссылки → {"start", "end", "tail"} в секундах (чего нет — того нет)."""
    s = rest.strip().lower().replace("—", "-").replace("–", "-")
    if not re.search(r"\d", s):  # пусто или просто комментарий («смешной момент») — целиком
        return {}
    m = re.fullmatch(rf"({TIME})\s*-\s*({TIME})", s)
    if m:
        a, b = secs(m[1]), secs(m[2])
        if b <= a:
            raise CutError("конец отрезка раньше начала")
        return {"start": a, "end": b}
    m = re.fullmatch(rf"до\s*({TIME})", s)
    if m:
        return {"end": secs(m[1])}
    m = re.fullmatch(rf"-\s*({TIME})(?:\s+-\s*({TIME}))?", s)
    if m:
        return {"start": secs(m[1]), "tail": secs(m[2]) if m[2] else 0.0}
    raise CutError(f"не понял «{rest.strip()}»")


CUT_HEAD = re.compile(rf"\s*(?:{TIME}\s*[-—–]\s*{TIME}|до\s*{TIME}|[-—–]\s*{TIME}(?:\s+[-—–]\s*{TIME})?)(?=\s|$)",
                      re.I)


def parse_reply(text):
    """Ответ на превью → (cut, подпись | None). «ок» — целиком; «-1 -5» — обрезка; «-1 -5 текст» или просто
    «текст» — креатив с этой подписью. Обрезка не разобралась («5-0 в нашу пользу») — весь текст подпись."""
    if text.strip().lower() in OK_WORDS:
        return {}, None
    m = CUT_HEAD.match(text)
    if m:
        try:
            cut = parse_cut(m[0])
        except CutError:
            if not text[m.end():].strip():
                raise  # «1:75-2» без текста — опечатка в обрезке, а не подпись
            return {}, text.strip()
        return cut, text[m.end():].strip() or None
    return {}, text.strip()


def norm_url(u):
    u = u.rstrip(".,;:!?)»\"'")
    if not u.lower().startswith("http"):
        u = "https://" + u
    p = urlsplit(u)
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = p.path.rstrip("/")
    if host in ("youtube.com", "music.youtube.com") and path == "/watch":
        v = parse_qs(p.query).get("v", [""])[0]
        return f"https://youtube.com/watch?v={v}"
    return f"https://{host}{path}"


def utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


def parse_message(text):
    """→ список (url, cut | CutError, quote, quote_offset). Отрезок пишется на той же строке после
    своей ссылки; quote — этот кусок сообщения как есть, бот цитирует его в ответе."""
    jobs = []
    pos = 0
    for line in text.splitlines(keepends=True):
        found = list(URL_RE.finditer(line))
        for i, m in enumerate(found):
            end = found[i + 1].start() if i + 1 < len(found) else len(line)
            try:
                cut = parse_cut(line[m.end():end])
            except CutError as e:
                cut = e
            quote = line[m.start():end].rstrip()
            jobs.append((norm_url(m[1]), cut, quote, utf16_len(text[:pos + m.start()])))
        pos += len(line)
    return jobs


def cut_key(url, cut):
    r = lambda x: None if x is None else round(x, 2)
    return f"{url}|{r(cut.get('start'))}|{r(cut.get('end'))}|{r(cut.get('tail'))}"


def tc(x):
    x = int(round(x))
    h, m, s = x // 3600, x // 60 % 60, x % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cut_label(cut):
    """Для подписи и имени файла."""
    if "end" in cut and "start" in cut:
        return f"{tc(cut['start'])}–{tc(cut['end'])}"
    if "end" in cut:
        return f"до {tc(cut['end'])}"
    if cut.get("start") or cut.get("tail"):
        parts = []
        if cut.get("start"):
            parts.append(f"−{cut['start']:g} с в начале")
        if cut.get("tail"):
            parts.append(f"−{cut['tail']:g} с в конце")
        return ", ".join(parts)
    return "целиком"


# ---------- внешние программы ----------

def run(cmd, timeout):
    log.info("RUN %s", " ".join(str(c) for c in cmd)[:400])
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


class JobError(Exception):
    pass


def explain(url, err):
    """Ошибка yt-dlp/ffmpeg → причина простыми словами (None — непонятная)."""
    e = err.lower()
    if "instagram" in url and any(w in e for w in ("login", "rate-limit", "cookies", "not available")):
        return "Instagram не пустил без входа"
    if "unsupported url" in e:
        return "этот сайт бот не знает"
    if "is live" in e or "live event" in e:
        return "это прямой эфир — дождись записи"
    if "private" in e:
        return "видео приватное"
    if "404" in e or "not found" in e or "does not exist" in e or "unavailable" in e:
        return "видео не найдено — удалено или ссылка не та"
    if "login" in e or "sign in" in e or "cookies" in e:
        return "сайт требует вход"
    if "403" in e or "forbidden" in e or "cloudflare" in e:
        return "сайт не пустил (защита) — попробуй позже"
    if "timed out" in e or "timeout" in e:
        return "сайт не ответил вовремя — кинь ещё раз"
    return None


def ytdlp(args, url, timeout):
    """yt-dlp; при непонятной ошибке — повтор на прошлой версии (если обновление что-то сломало)."""
    r = run([YTDLP, *args, url], timeout)
    if r.returncode == 0:
        return r
    if explain(url, r.stderr) is None and YTDLP_PREV.exists():
        log.warning("yt-dlp упал непонятно, пробую прошлую версию")
        r2 = run([YTDLP_PREV, *args, url], timeout)
        if r2.returncode == 0:
            log.warning("прошлая версия yt-dlp сработала")
            return r2
    log.error("yt-dlp: %s", r.stderr[-2000:])
    raise JobError(explain(url, r.stderr) or f"не скачал, {OWNER} посмотрит")


def update_ytdlp():
    BIN.mkdir(exist_ok=True)
    if not YTDLP.exists() and TOOLS:
        shutil.copy2(TOOLS / "yt-dlp.exe", YTDLP)
    shutil.copy2(YTDLP, YTDLP_PREV)
    try:
        r = run([YTDLP, "-U"], 180)
        log.info("yt-dlp -U: %s", (r.stdout + r.stderr).strip().splitlines()[-1:])
    except Exception as e:  # нет интернета и т.п. — работаем на старой
        log.warning("yt-dlp -U не вышло: %s", e)


def wait_for_net():
    """Автозапуск при входе в Windows бывает раньше, чем появился интернет: ждём сеть, а не падаем (18.10.2026)."""
    said = False
    while True:
        try:
            socket.create_connection(("api.telegram.org", 443), 5).close()
            if said:
                log.info("интернет появился")
            return True
        except OSError:
            if not said:
                log.info("нет интернета — жду")
                said = True
        if STOP_FLAG.exists():
            return False
        time.sleep(10)


def warm_certs():
    """Заход через curl.exe (schannel): Windows сама догружает корневые сертификаты Kick и Telegram,
    и ffmpeg идёт быстрым путём. На чистой Windows без этого превью — через fetch_hls (песочница 17.10.2026)."""
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    if not curl.exists():
        return
    try:
        run([curl, "-s", "-o", "NUL", "--max-time", "15",
             "https://clips.kick.com", "https://kick.com", "https://api.telegram.org"], 60)
    except Exception as e:
        log.warning("curl для сертификатов не вышел: %s", e)


def probe(path):
    out = run([FFMPEG, "-hide_banner", "-i", path], 60).stderr
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    v = re.search(r"Video: (\w+).*?(\d{2,5})x(\d{2,5})", out)
    fps = re.search(r"([\d.]+) fps", out)
    if not (d and v):
        raise JobError("скачался битый файл — кинь ещё раз")
    return {"duration": int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]), "codec": v[1],
            "w": int(v[2]), "h": int(v[3]), "fps": float(fps[1]) if fps else 0}


ENC = ["-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def streamer_of(url, info):
    p = urlsplit(url)
    seg = [s for s in p.path.split("/") if s]
    if "kick.com" in p.netloc and seg:
        name = seg[0]
    elif "tiktok.com" in p.netloc and seg and seg[0].startswith("@"):
        name = seg[0][1:]
    else:
        name = info.get("channel") or info.get("uploader_id") or info.get("uploader") or "video"
    return re.sub(r"[^\w.-]", "_", str(name))[:40]


def process(url, cut, cfg, work):
    """Скачать и порезать. → (путь к mp4, сведения для подписи). Блокирующая — гоняется в потоке."""
    work.mkdir(parents=True, exist_ok=True)
    info = json.loads(ytdlp(["-J", "--no-playlist", "--no-warnings"], url, 120).stdout)
    if info.get("is_live"):
        raise JobError("это прямой эфир — дождись записи")
    dur = info.get("duration")
    start, end, tail = cut.get("start", 0.0), cut.get("end"), cut.get("tail", 0.0)
    if dur:
        if end is not None and end > dur + 0.5:
            raise JobError(f"в видео всего {tc(dur)}")
        if start + tail >= dur:
            raise JobError(f"после обрезки ничего не остаётся — в видео всего {tc(dur)}")
    out = work / "out.mp4"

    if dur and dur > cfg["max_full_min"] * 60:
        # Длинное (запись стрима): тянем из HLS только нужный кусок.
        if end is None:
            raise JobError(f"это длинное видео ({tc(dur)}) — укажи отрезок, например 1:20:00-1:20:10")
        if end - start > cfg["max_piece_min"] * 60:
            raise JobError(f"отрезок длиннее {cfg['max_piece_min']} мин — возьми кусок поменьше")
        fmts = [f for f in info.get("formats", []) if f.get("vcodec") not in (None, "none")
                and f.get("acodec") not in (None, "none") and f.get("url")]
        if not fmts:
            raise JobError("с этого сайта кусок длинного видео не вырезать")
        f = max(fmts, key=lambda f: ((f.get("height") or 0), (f.get("fps") or 0), (f.get("tbr") or 0)))
        hdr = "".join(f"{k}: {v}\r\n" for k, v in (f.get("http_headers") or {}).items())
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *FF_TLS]
        if hdr:
            cmd += ["-headers", hdr]
        r = run(cmd + ["-ss", f"{start:.3f}", "-i", f["url"], "-t", f"{end - start:.3f}", *ENC, out], 1800)
        if r.returncode != 0 or not out.exists():
            log.error("ffmpeg кусок: %s", r.stderr[-2000:])
            raise JobError(explain(url, r.stderr) or f"не вырезал кусок, {OWNER} посмотрит")
    else:
        # у клипа Twitch есть «portrait-…» — вертикальная обрезка; по высоте она больше 1080 и выиграла бы
        fmt = ["-f", "b[format_id!^=portrait]/b"] if "twitch.tv" in url else []
        ytdlp(["--no-playlist", "--no-warnings", "--no-progress", *fmt, "-S", "res,fps,vcodec:h264,acodec:aac",
               "--merge-output-format", "mp4", "--remux-video", "mp4",
               "--ffmpeg-location", FFMPEG.parent, "-o", str(work / "src.%(ext)s")], url, 1800)
        src = next(iter(sorted(work.glob("src.*"))), None)
        if not src:
            raise JobError(f"не скачал, {OWNER} посмотрит")
        p = probe(src)
        d = p["duration"]
        e = (min(end, d) if end is not None else d) - tail
        if e - start <= 0.2:
            raise JobError(f"после обрезки ничего не остаётся — в видео всего {tc(d)}")
        if start < 0.05 and e >= d - 0.05 and p["codec"] == "h264":
            shutil.move(src, out)                       # целиком, как скачано
        elif start < 0.05 and p["codec"] == "h264":    # только хвост — без пережатия
            r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-t", f"{e:.3f}",
                     "-c", "copy", "-movflags", "+faststart", out], 600)
        else:                                          # начало — точно, с пережатием CRF 18
            r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", src,
                     "-t", f"{e - start:.3f}", *ENC, out], 1800)
        if not out.exists():
            log.error("ffmpeg рез: %s", r.stderr[-2000:])
            raise JobError(f"не порезал, {OWNER} посмотрит")

    p = probe(out)
    if out.stat().st_size > 1950 * 2**20:
        raise JobError("получилось больше 2 ГБ — возьми кусок поменьше")
    thumb = work / "thumb.jpg"
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(1, p['duration'] / 2):.2f}",
         "-i", out, "-frames:v", "1", "-vf", "scale=320:-2", thumb], 60)
    p["thumb"] = thumb if thumb.exists() else None
    p["streamer"] = streamer_of(url, info)
    return out, p


# ---------- моменты стрима Kick (тема «Выбор клипов») ----------

def kick():
    """kick_clips.py: в наборе — рядом с ботом, у Димы — в tools/."""
    sys.path.insert(0, str(HERE if KIT else TOOLS))
    import kick_clips as kc
    if KICK_CACHE:
        kc.CACHE = KICK_CACHE
    return kc


def twitch():
    """twitch_clips.py — там же, кэш общий с Kick."""
    kick()
    import twitch_clips as tw
    if KICK_CACHE:
        tw.CACHE = KICK_CACHE
    return tw


def stream_link(url):
    """Ссылка на запись стрима или канал → (сайт, канал, запись); иначе None.
    kick.com/<канал>/videos/<uuid>, twitch.tv/videos/<id>; ссылка на канал = последний стрим."""
    p = urlsplit(url)
    seg = [s for s in p.path.split("/") if s]
    if "kick.com" in p.netloc and seg:
        if len(seg) == 1:
            return "kick", seg[0], None
        if len(seg) == 3 and seg[1] == "videos":
            return "kick", seg[0], seg[2]
    if p.netloc.lower().removeprefix("www.").removeprefix("m.") == "twitch.tv" and seg:
        if len(seg) == 2 and seg[0] == "videos" and seg[1].isdigit():
            return "twitch", None, seg[1]
        if len(seg) == 1 and re.fullmatch(r"\w{2,25}", seg[0]) and seg[0].lower() not in ("videos", "directory"):
            return "twitch", seg[0].lower(), None
    return None


def link_start(raw):
    """?t= в ссылке на запись («Поделиться → Начало 23:28» на Kick; Twitch: ?t=1h6m55s) → секунды или None."""
    t = parse_qs(urlsplit(raw.split()[0] if raw.strip() else "").query).get("t", [""])[0].strip().lower()
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if t and m:
        return int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0)
    return None


def stream_moments(site, channel, rec):
    """→ (стрим, число клипов, моменты от самых заклипанных). Блокирующая — гоняется в потоке."""
    if site == "twitch":
        tw = twitch()
        try:
            s = tw.video(rec) if rec else next(iter(tw.streams(channel)), None)
        except OSError as e:
            raise JobError(f"Twitch не ответил: {e}")
        if not s:
            raise JobError("запись не найдена — на Twitch они живут 7–60 дней; нужна ссылка twitch.tv/videos/…")
        cache = tw.CACHE / f"twitch_{s['login']}_{s['id']}.json"
        collect = lambda fresh: tw.collect(s, fresh)
        group = lambda cl: tw.moments_of(s, cl)  # канал без записей — моменты по времени создания клипов
    else:
        kc = kick()
        st = kc.streams(channel)
        s = kc.find_stream(st, rec) if rec else (st[0] if st else None)
        if not s:
            raise JobError("стрим не найден — нужна ссылка на запись: kick.com/<канал>/videos/…")
        cache = kc.CACHE / f"{channel}_{s['id']}.json"
        collect = lambda fresh: kc.collect(channel, s, fresh)
        group = kc.moments
    fresh = not cache.exists() or time.time() - cache.stat().st_mtime > KICK_CACHE_MAX_H * 3600
    try:
        cl = collect(fresh)
    except OSError as e:
        if site != "twitch":
            raise
        log.warning("twitch: %s", e)
        raise JobError("Twitch долго не отвечал — кинь ссылку ещё раз через минуту")
    # поровну заклипанные — сначала тот, у кого главный клип смотрели больше
    ms = sorted((m for m in group(cl) if m["n"] >= PICK_MIN_CLIPS), key=lambda m: (-m["n"], -m["short"]["views"]))
    if site == "twitch" and s.get("approx"):
        s = {k: v for k, v in s.items() if k != "clips"}  # клипы не тащить в state.json
    return s, len(cl), ms


def fetch_hls(m3u8, dst):
    """HLS-плейлист → один .ts: куски .ts склеиваются подряд. Мастер-плейлист — берётся последний вариант."""
    def get(u):
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    text = get(m3u8).decode("utf-8", "replace")
    uris = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if "#EXT-X-STREAM-INF" in text:
        return fetch_hls(urljoin(m3u8, uris[-1]), dst)
    with open(dst, "wb") as f:
        for u in uris:
            f.write(get(urljoin(m3u8, u)))


def make_preview(clip_id, work, site="kick"):
    """Главный клип целиком в 360p (~1–2 МБ) — посмотреть прямо в Telegram.
    Kick — прямо из плейлиста клипа одним ffmpeg: ~10 с против ~40 через yt-dlp.
    Twitch — у клипа готовый mp4 360p, yt-dlp его просто скачивает."""
    work.mkdir(parents=True, exist_ok=True)
    if site == "twitch":
        prev = work / "preview.mp4"
        ytdlp(["--no-warnings", "--no-progress", "-f", "360/worst[height>=360]/worst", "-o", str(prev)],
              f"https://clips.twitch.tv/{clip_id}", 300)
        if not prev.exists():
            raise JobError("не сделал превью")
        return preview_done(prev, work)
    kc = kick()
    try:
        d = kc.get(f"https://kick.com/api/v2/clips/{clip_id}")
    except OSError:
        raise JobError("Kick не отдал клип")
    m3u8 = (d.get("clip") or d).get("clip_url")
    if not m3u8:
        raise JobError("клип удалён")
    prev = work / "preview.mp4"
    enc = ["-vf", "scale=-2:360", "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", prev]
    try:  # обычно 10–20 с; ffmpeg бывает виснет на плейлисте Kick насовсем (21.10.2026) — тогда качаем сами
        r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *FF_TLS, "-i", m3u8, *enc], 120)
        hung = False
    except subprocess.TimeoutExpired:
        log.warning("превью: ffmpeg завис на плейлисте, качаю куски сам")
        hung = True
    if hung or (r.returncode != 0 or not prev.exists()) and "certificate" in r.stderr:
        # -ca_file не доходит до кусков .ts внутри плейлиста — качаем их сами (urllib + certifi)
        if not hung:
            log.info("превью: ffmpeg не доверяет сертификату, качаю куски сам")
        ts = work / "clip.ts"
        try:
            fetch_hls(m3u8, ts)
            r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", ts, *enc], 120)
        except (OSError, subprocess.TimeoutExpired):
            raise JobError("Kick не отдал видео вовремя")
    if r.returncode != 0 or not prev.exists():
        log.error("превью: %s", r.stderr[-1000:])
        raise JobError("не сделал превью")
    return preview_done(prev, work)


def preview_done(prev, work):
    q = probe(prev)
    thumb = work / "thumb.jpg"
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", prev, "-frames:v", "1", thumb], 60)
    q["thumb"] = thumb if thumb.exists() else None
    return prev, q


# ---------- классический креатив (19.10.2026) ----------
# Вертикаль 1080×1920: клип по ширине в центре, сверху и снизу — его же размытая копия, над клипом —
# подпись жёлтым капсом на тёмной скруглённой плашке (стиль «криатор фаба» LevelX, «Суд над Гамосом»).

CR_W, CR_H = 1080, 1920
CR_FONT = HERE / "Montserrat-ExtraBold.ttf"
CR_SIZE, CR_MIN = 65, 40       # кегль: 65 — как в образце; длинную подпись уменьшаем до 40
CR_MAX_LINES = 3
CR_PADX, CR_PADY, CR_RADIUS = 40, 44, 22
CR_YELLOW, CR_PLATE = (236, 222, 30, 255), (28, 26, 30, 225)
CR_GAP = 95                    # от низа плашки до верха клипа


def pillow():
    """Pillow для плашки. В наборе его нет (install.ps1 у старых установок не обновляется) — ставим сами.
    raqm (склейка эмодзи: 👍🏻, семьи) в Pillow вшит, но ищет fribidi через PATH — DLL лежит рядом с bot.py
    (libfribidi-0.dll из MSYS2, LGPL)."""
    if str(HERE) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(HERE) + os.pathsep + os.environ.get("PATH", "")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.info("ставлю Pillow")
        r = run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "pillow"], 600)
        if r.returncode != 0:
            log.error("pip pillow: %s", r.stderr[-1000:])
            raise JobError(f"не поставил Pillow для подписи, {OWNER} посмотрит")
        from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


# Эмодзи (19.10.2026): в Montserrat их нет — были квадратики. Рисуем цветными из шрифта Windows.
# Склейки (👍🏻, семья через ZWJ, флаги) собирает raqm — смайлик целиком одним куском. Если raqm не поднялся
# (нет fribidi) — оттенок кожи, FE0F и всё после ZWJ отбрасываем: остаётся базовый смайлик.
CR_EMOJI_FONT = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / "seguiemj.ttf"
_EMO = ("[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF\u2190-\u21FF\u2300-\u23FF\u2460-\u24FF\u25A0-\u27BF"
        "\u2900-\u297F\u2B00-\u2BFF\u3030\u303D\u3297\u3299]")
_MOD = "[\U0001F3FB-\U0001F3FF\uFE0F\U000E0020-\U000E007F]"  # оттенок кожи, «как смайлик», теги флагов
EMOJI_RE = re.compile(f"[\U0001F1E6-\U0001F1FF]{{2}}|[0-9#*]\uFE0F?\u20E3|{_EMO}{_MOD}*(?:\u200D{_EMO}{_MOD}*)*")
# \u0444\u043B\u0430\u0433\u043E\u0432 \u0441\u0442\u0440\u0430\u043D \u0432 Segoe UI Emoji \u043D\u0435\u0442 (Windows \u0440\u0438\u0441\u0443\u0435\u0442 \u00ABUA\u00BB) \u2014 \u0431\u0435\u0440\u0451\u043C \u043A\u0430\u0440\u0442\u0438\u043D\u043A\u0443 Twemoji, \u043A\u044D\u0448 \u0440\u044F\u0434\u043E\u043C \u0441 \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0430\u043C\u0438
FLAG_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{}.png"
FLAG_CACHE = DATA / "emoji_cache"


def flag_image(Image, t):
    code = "-".join(f"{ord(c):x}" for c in t)
    p = FLAG_CACHE / f"{code}.png"
    if not p.exists():
        try:
            FLAG_CACHE.mkdir(exist_ok=True)
            with urllib.request.urlopen(FLAG_URL.format(code), timeout=15) as r:
                p.write_bytes(r.read())
        except OSError as e:
            log.warning("\u0444\u043B\u0430\u0433 %s \u043D\u0435 \u0441\u043A\u0430\u0447\u0430\u043B\u0441\u044F: %s", t, e)
            return None
    return Image.open(p).convert("RGBA")
EMOJI_JUNK = re.compile("[\U0001F3FB-\U0001F3FF\uFE0E\uFE0F\u20E3]|(?:\u200D[^\s\u200D]+)+")


class CaptionFont:
    """Montserrat для текста + Segoe UI Emoji для смайликов: ширина и отрисовка строки по кускам."""

    def __init__(self, Image, ImageFont, size):
        self.Image, self.size = Image, size
        self.flags = {}
        self.f = ImageFont.truetype(str(CR_FONT), size)
        self.ef = ImageFont.truetype(str(CR_EMOJI_FONT), size) if CR_EMOJI_FONT.exists() else None
        self.top, self.bottom = self.f.getbbox("Н")[1], self.f.getbbox("Н")[3]

    def runs(self, s):
        if not self.ef:
            return [(s, False)]
        out, last = [], 0
        for m in EMOJI_RE.finditer(s):
            if m.start() > last:
                out.append((s[last:m.start()], False))
            out.append((m[0], True))
            last = m.end()
        if last < len(s):
            out.append((s[last:], False))
        return out

    def flag(self, t):
        """Флаг страны картинкой Twemoji под размер шрифта (None — не флаг или не скачался)."""
        if not "\U0001F1E6" <= t[0] <= "\U0001F1FF":
            return None
        if t not in self.flags:
            im = flag_image(self.Image, t)
            self.flags[t] = im and im.resize((self.size, self.size), self.Image.LANCZOS)
        return self.flags[t]

    def width(self, t, e):
        if e and self.flag(t):
            return self.size * 1.08
        return (self.ef if e else self.f).getlength(t)

    def length(self, s):
        return sum(self.width(t, e) for t, e in self.runs(s))

    def draw(self, im, d, x, y, s):
        """y — верх заглавных букв строки."""
        for t, e in self.runs(s):
            fl = e and self.flag(t)
            if fl:
                im.alpha_composite(fl, (round(x + self.size * 0.04),
                                        round(y + (self.bottom - self.top - self.size) / 2)))
                x += self.width(t, e)
            elif e:  # смайлик — по центру высоты заглавных
                b = self.ef.getbbox(t)
                d.text((x, y + (self.bottom - self.top) / 2 - (b[1] + b[3]) / 2), t, font=self.ef,
                       embedded_color=True)
                x += self.ef.getlength(t)
            else:
                d.text((x, y - self.top), t, font=self.f, fill=CR_YELLOW)
                x += self.f.getlength(t)


def wrap(text, font, maxw):
    """Слова по строкам в ширину maxw; переносы строк, которые написал человек, сохраняем."""
    lines = []
    for para in text.splitlines():
        cur = ""
        for w in para.split():
            t = f"{cur} {w}".strip()
            if font.length(t) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def caption_plate(text, path):
    Image, ImageDraw, ImageFont = pillow()
    from PIL import features
    if not features.check("raqm"):
        log.warning("raqm не поднялся (нет libfribidi-0.dll?) — склейки эмодзи упрощаю")
        text = EMOJI_JUNK.sub("", text)
    text = "\n".join(" ".join(l.split()) for l in text.upper().splitlines() if l.strip())
    maxw = CR_W - 2 * 60 - 2 * CR_PADX
    size = CR_SIZE
    while True:
        f = CaptionFont(Image, ImageFont, size)
        lines = wrap(text, f, maxw)
        if (len(lines) <= CR_MAX_LINES and all(f.length(l) <= maxw for l in lines)) or size <= CR_MIN:
            break
        size -= 3
    lh = round(size * 1.22)
    pw = round(max(f.length(l) for l in lines) + 2 * CR_PADX)
    ph = round(f.bottom - f.top + (len(lines) - 1) * lh + 2 * CR_PADY)
    im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=CR_RADIUS, fill=CR_PLATE)
    for i, l in enumerate(lines):
        f.draw(im, d, (pw - f.length(l)) / 2, CR_PADY + i * lh, l)
    im.save(path)


def make_creative(src, text, work):
    """Скачанный клип + подпись → вертикальный креатив. → (путь, сведения как у process)."""
    plate, out = work / "plate.png", work / "creative.mp4"
    caption_plate(text, plate)
    p = probe(src)
    fg_h = round(CR_W * p["h"] / p["w"] / 2) * 2
    # фон: уменьшить → размыть → растянуть обратно (в разы быстрее, чем размывать 1080×1920)
    fc = (f"[0:v]split[a][b];"
          f"[a]scale={CR_W // 4}:{CR_H // 4}:force_original_aspect_ratio=increase,crop={CR_W // 4}:{CR_H // 4},"
          f"boxblur=10:2,scale={CR_W}:{CR_H},eq=brightness=-0.06,setsar=1[bg];"
          f"[b]scale={CR_W}:{fg_h},setsar=1[fg];"
          f"[bg][fg]overlay=0:(H-h)/2[v0];"
          f"[v0][1:v]overlay=(W-w)/2:'max(40,(H-{fg_h})/2-{CR_GAP}-h)'[v]")
    r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-i", plate, "-filter_complex", fc,
             "-map", "[v]", "-map", "0:a?", *ENC, out], 1800)
    if r.returncode != 0 or not out.exists():
        log.error("ffmpeg креатив: %s", r.stderr[-2000:])
        raise JobError(f"не собрал креатив, {OWNER} посмотрит")
    q = probe(out)
    thumb = work / "creative.jpg"
    run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(1, q['duration'] / 2):.2f}",
         "-i", out, "-frames:v", "1", "-vf", "scale=-2:480", thumb], 60)
    q["thumb"] = thumb if thumb.exists() else None
    return out, q


# ---------- копия готового клипа ----------

def copy_out(cfg, out, streamer, cut, suffix=""):
    """Копия в out_dir: <стример>_<дата>_<обрезка><suffix>.mp4 (занято — _2, _3…). → путь копии."""
    d = Path(cfg["out_dir"])
    d.mkdir(parents=True, exist_ok=True)
    f = lambda x: tc(x).replace(":", "-")
    if "start" in cut and "end" in cut:
        lbl = f"{f(cut['start'])}_{f(cut['end'])}"
    elif "end" in cut:
        lbl = f"to_{f(cut['end'])}"
    elif cut.get("start") or cut.get("tail"):
        lbl = f"minus{cut.get('start', 0):g}s_{cut.get('tail', 0):g}s"
    else:
        lbl = "full"
    base = f"{streamer}_{datetime.now():%Y-%m-%d}_{lbl}{suffix}"
    dst, i = d / f"{base}.mp4", 2
    while dst.exists():
        dst, i = d / f"{base}_{i}.mp4", i + 1
    shutil.copy2(out, dst)
    return dst


# ---------- прогон через Edits на айфоне ----------
# Пачка = папка задания в edits_topic.jobs_dir с mp4 внутри; её прогоняет отдельный процесс edits_iphone.py
# (у него своя очередь — одна серия за раз на телефон, tools/wda/edits_queue.py). Готовое он отмечает в
# <папка>/.iphone-done.txt, до телефона не достучался — кладёт .phone-offline.

def edits_launch(cfg, job_dir, phone_only=False):
    """Запустить прогон папки. → (pid, время создания процесса) — по ним потом понятно, жив ли именно он."""
    import psutil
    script = Path(cfg["edits_topic"]["script"])
    exe = Path(sys.executable).with_name("python.exe")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with open(Path(job_dir) / "run.log", "a", encoding="utf-8") as out:
        # --phone-only: готовое остаётся только в галерее айфона, на ноутбук не забирается (Дима 20.10.2026)
        keep = ["--phone-only"] if phone_only else []
        pr = subprocess.Popen([str(exe), "-u", str(script), str(job_dir), *keep], stdout=out,
                              stderr=subprocess.STDOUT, cwd=str(script.parent), env=env,
                              # без DETACHED: скрытая консоль на весь прогон — иначе каждый ffmpeg/ffprobe/
                              # pymobiledevice3 открывал своё мелькающее окно (Дима, 20.10.2026)
                              creationflags=0x00000200 | NO_WINDOW, close_fds=True)
    return pr.pid, psutil.Process(pr.pid).create_time()


def proc_alive(pid, created):
    """Процесс pid жив и это тот самый (pid Windows переиспользует)."""
    import psutil
    try:
        return abs(psutil.Process(pid).create_time() - created) < 1
    except psutil.Error:
        return False


def equeue(cfg):
    """Модуль очереди айфона (tools/wda/edits_queue.py рядом со скриптом прогона)."""
    d = str(Path(cfg["edits_topic"]["script"]).parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    import edits_queue
    return edits_queue


def edits_busy(cfg, jobs):
    """Айфон занят: живой прогон из jobs (кроме остановившихся) или любой прогон в очереди edits_queue."""
    if any(proc_alive(j["pid"], j["created"]) for j in jobs if not j.get("stuck")):
        return True
    return bool(equeue(cfg).edit())


def edits_done(job_dir):
    """Имена файлов пачки, которые уже прошли Edits."""
    f = Path(job_dir) / ".iphone-done.txt"
    return set(f.read_text(encoding="utf-8").split()) if f.exists() else set()
