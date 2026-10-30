"""Линия креативов — настольное приложение (NiceGUI, 25.10.2026). Те же функции, что у бота-качалки, но без
Telegram: вся логика — в core.py, здесь только экраны. Бот работает параллельно, они друг другу не мешают.

  python tools/shared_bot/app.py            # окно приложения
  python tools/shared_bot/app.py --browser  # то же в браузере: http://127.0.0.1:8765 (удобно для проверки)

Экраны: «Загрузки», «Выбор клипов» (ссылка на запись стрима → моменты с превью → «Взять»), «Отобранные»,
«Очередь Edits». Вопрос айфона — карточкой сверху на любом экране и красным кружком в меню.
ИИ управляет теми же действиями через /api/… → app_cli.py (описание для ИИ — app-AGENTS.md). Новая функция =
кнопка + команда app_cli + строка в app-AGENTS.md.
История загрузок и текущий стрим — app_state.json рядом с настройками бота, превью — app_previews/, лог — app.log.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # запуск с -I: папки скрипта в sys.path нет
if sys.stdout is None:  # pythonw (без консоли): NiceGUI и uvicorn пишут в stdout/stderr уже при запуске — иначе падают
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    NO_CONSOLE = True
else:
    NO_CONSOLE = False
import core  # noqa: E402
import sasha  # noqa: E402
import split  # noqa: E402
import tops  # noqa: E402
from core import CutError, JobError, cut_key, cut_label, parse_message  # noqa: E402

from nicegui import app, ui  # noqa: E402

PORT = 8765
STATE = core.DATA / "app_state.json"
LOG = core.DATA / "app.log"
THUMBS = core.DATA / "app_thumbs"   # обложки для плеера: <id загрузки>.jpg
PREVIEWS = core.DATA / "app_previews"   # превью моментов 360p: <сайт>_<id клипа>/preview.mp4 + thumb.jpg
KEEP = 100                  # сколько последних загрузок помнить
KEEP_PREVIEWS = 300         # больше — самые старые превью стираем
PREVIEW_THREADS = 3         # превью делаются по столько сразу (Kick: 10–20 с на одно)
KEEP_RUNS = 20              # сколько законченных пачек Edits помнить
YELLOW = "#f5c518"


SAVE_LOCK = threading.Lock()


def unglue(text):
    """Ссылки, вставленные слитно (https://a.comhttps://b.com) или без пробелов, — каждая с новой строки."""
    return re.sub(r"(?<=\S)(?=https?://)", "\n", text or "")


def save_state():
    """Сохраняют из разных потоков (загрузки, превью, Edits) — по одному: Windows не даёт заменить файл,
    который в этот же момент заменяет другой поток."""
    with SAVE_LOCK:
        data = {"downloads": DL.items, "pick": PK.s if PK else None,
                "edits": {"pending": ED.pending, "jobs": ED.jobs, "phone_only": ED.phone_only} if ED else None,
                "tops": TP.s if TP else None,
                "split": SP.s if SP else None,
                "sasha": SA.s if SA else None}
        for attempt in range(15):   # Windows: файл на миг занят (антивирус, Проводник, чтение другим процессом) — пробуем снова
            try:
                core.save_json(STATE, data)
                return
            except PermissionError:
                if attempt == 14:
                    raise
                time.sleep(0.1 * (attempt + 1))

log = logging.getLogger("bot")  # тот же логгер, что у core: его строки «RUN …» тоже попадут в app.log


# ---------- загрузки: очередь и работник ----------
# Как у бота: одна загрузка за раз, остальные ждут. Задание — словарь; экран раз в полсекунды перерисовывает
# список, если что-то поменялось (VERSION). Готовые и ошибки пишутся в app_state.json — переживают перезапуск.

class Downloads:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.version = 0
        self.seq = 0
        self.items = core.load_json(STATE, {}).get("downloads", [])
        for it in self.items:   # приложение закрыли посреди работы — эти уже не доделать
            if it["status"] in ("wait", "work"):
                it.update(status="error", error="приложение закрывали, пока качалось — добавь ещё раз")
        self.seq = max((it["id"] for it in self.items), default=0)
        threading.Thread(target=self.worker, daemon=True).start()

    def changed(self, save=True):
        self.version += 1
        if save:
            done = [it for it in self.items if it["status"] in ("done", "error")]
            keep = {it["id"] for it in done[-KEEP:]}
            self.items = [it for it in self.items if it["status"] in ("wait", "work") or it["id"] in keep]
            save_state()

    def add_pick(self, url, cut, caption, meta, pool=""):
        """Момент из «Выбора клипов» → в очередь; готовое показывается в «Отобранных» (dest), а не в «Загрузках»."""
        with self.lock:
            key = cut_key(url, cut) + "|" + caption
            if any(cut_key(it["url"], it["cut"]) + "|" + it.get("caption", "") == key
                   for it in self.items if it["status"] in ("wait", "work")):
                return False
            self.seq += 1
            self.items.append({"id": self.seq, "url": url, "cut": cut, "caption": caption, "status": "wait",
                               "stage": "", "added": datetime.now().isoformat(timespec="seconds"),
                               "dest": "picked", "meta": meta, **({"pool": pool} if pool else {})})
            self.changed()
        self.wake.set()
        return True

    def add_local(self, path):
        """Готовый файл с компьютера (кнопка в «Очереди Edits») → карточка «готово», dest=upload: в списках не видна,
        только ждёт в очереди айфона. → (id, ошибка)."""
        try:
            p = core.probe(str(path))
        except JobError as e:
            return None, str(e)
        with self.lock:
            self.seq += 1
            it = {"id": self.seq, "url": "", "cut": None, "caption": "", "status": "done", "stage": "",
                  "added": datetime.now().isoformat(timespec="seconds"), "dest": "upload", "file": str(path),
                  "w": p["w"], "h": p["h"], "fps": round(p["fps"]), "duration": round(p["duration"], 1),
                  "streamer": "компьютер"}
            self.items.append(it)
            self.changed()
        return it["id"], None

    def add(self, text, caption="", pool=""):
        """Текст из поля → задания. → список ошибок разбора (их показываем сразу, в очередь не ставим)."""
        errors, added = [], 0
        text = unglue(text)
        with self.lock:
            busy = {cut_key(it["url"], it["cut"]) + "|" + it.get("caption", "")
                    for it in self.items if it["status"] in ("wait", "work")}
            for url, cut, _quote, _o in parse_message(text):
                if isinstance(cut, CutError):
                    errors.append(f"{url}: {cut} — пример: ссылка 1:20-1:45")
                    continue
                key = cut_key(url, cut) + "|" + caption
                if key in busy:   # та же ссылка с той же обрезкой уже в очереди — второй раз не качаем
                    errors.append(f"{url}: уже в очереди")
                    continue
                busy.add(key)
                self.seq += 1
                self.items.append({"id": self.seq, "url": url, "cut": cut, "caption": caption, "status": "wait",
                                   "stage": "", "added": datetime.now().isoformat(timespec="seconds"),
                                   **({"pool": pool} if pool else {})})
                added += 1
            if added:
                self.changed()
        if not added and not errors:
            errors.append("ссылок не нашёл — вставь ссылку на видео (Kick, Twitch, TikTok, YouTube, Instagram…)")
        self.wake.set()
        return errors

    def cancel(self, item_id):
        """Ждёт — убрать; в работе — доделается, но результат выкинем (как «✖️ Отмена» в боте)."""
        with self.lock:
            for it in self.items:
                if it["id"] == item_id and it["status"] in ("wait", "work"):
                    it["cancelled"] = True
                    if it["status"] == "wait":
                        self.items.remove(it)
                    self.changed()
                    return

    def remove(self, item_id):
        with self.lock:
            self.items = [it for it in self.items if not (it["id"] == item_id and it["status"] in ("done", "error"))]
            self.changed()

    def stage(self, it, text):
        it["stage"] = text
        self.changed(save=False)

    def worker(self):
        while True:
            with self.lock:
                it = next((x for x in self.items if x["status"] == "wait"), None)
                if it:
                    it["status"] = "work"
                    self.changed()
            if not it:
                self.wake.wait(2)
                self.wake.clear()
                continue
            work = Path(self.cfg["tmp_dir"]) / f"app{it['id']}"
            try:
                self.stage(it, "качаю")
                out, p = core.process(it["url"], it["cut"], self.cfg, work)
                if it.get("caption"):
                    self.stage(it, "собираю креатив")
                    streamer = p["streamer"]
                    out, p = core.make_creative(out, it["caption"], work)
                    p["streamer"] = streamer
                if it.get("cancelled"):
                    continue
                self.stage(it, "сохраняю")
                dst = core.copy_out(self.cfg, out, p["streamer"], it["cut"], "_creative" if it.get("caption") else "")
                if p.get("thumb"):   # кадр, который ядро и так делает для Telegram, — обложка плеера
                    THUMBS.mkdir(exist_ok=True)
                    shutil.copy2(p["thumb"], THUMBS / f"{it['id']}.jpg")
                it.update(status="done", file=str(dst), w=p["w"], h=p["h"], fps=round(p["fps"]),
                          duration=round(p["duration"], 1), streamer=p["streamer"])
                log.info("приложение: готово %s → %s", it["url"], dst)
                if it.get("pool"):
                    TP.take_download(it)
                if it.get("split"):
                    SP.on_download(it["id"], it["file"])
            except JobError as e:
                it.update(status="error", error=str(e))
                if it.get("split"):
                    SP.on_download(it["id"], None, str(e))
            except Exception as e:
                log.exception("приложение: сбой на %s", it["url"])
                it.update(status="error", error=f"сбой: {e}")
                if it.get("split"):
                    SP.on_download(it["id"], None, f"сбой: {e}")
            finally:
                shutil.rmtree(work, ignore_errors=True)
                with self.lock:
                    it["stage"] = ""
                    if it.get("cancelled") and it in self.items:
                        self.items.remove(it)
                    self.changed()


# ---------- выбор клипов: моменты стрима и превью ----------
# Как тема «Выбор клипов» в боте: ссылка на запись Kick/Twitch (или канал = последний стрим, ?t= — с этого места)
# → моменты по клипам зрителей, самые заклипанные первыми, по PICK_BATCH за раз. Превью 360p делаются в фоне
# и лежат в app_previews/ — повторно тот же момент не качается.

class Picker:
    def __init__(self, cfg, saved):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.s = saved            # текущий стрим: сайт, канал, моменты, сколько показано (shown)…
        self.loading = None       # ссылка, по которой сейчас собираются моменты
        self.error = None
        self.sver = 0             # стрим сменился или показали ещё — экран дорисовывает карточки
        self.prev = {}            # ключ момента → {"st": wait|work|done|error, "err": …}
        self.pver = 0
        self.pool = ThreadPoolExecutor(PREVIEW_THREADS)
        if self.s:
            self.queue_previews()

    def key(self, m):
        return f"{self.s['site']}_{m['short']['id']}"

    def clip_url(self, clip):
        if self.s["site"] == "twitch":
            return f"https://clips.twitch.tv/{clip['id']}"
        return f"https://kick.com/{self.s['channel']}/clips/{clip['id']}"

    def find(self, text):
        """Ссылка из поля → собрать моменты в фоне. → текст ошибки или None."""
        text = (text or "").strip()
        jobs = parse_message(text)
        k = core.stream_link(jobs[0][0]) if jobs else None
        if not k:
            return "нужна ссылка на запись стрима Kick или Twitch (kick.com/<канал>/videos/…, twitch.tv/videos/…) " \
                   "или на канал"
        if self.loading:
            return "моменты ещё собираются — подожди"
        frm = core.link_start(jobs[0][2]) if k[2] else None
        self.loading, self.error = jobs[0][0], None
        self.sver += 1
        threading.Thread(target=self._load, args=(k, jobs[0][0], frm), daemon=True).start()
        return None

    def _load(self, k, url, frm):
        site, channel, rec = k
        try:
            s, total, moments = core.stream_moments(site, channel, rec)
            if frm:
                moments = [m for m in moments if m["punch"] >= frm]
            with self.lock:
                self.s = {"sid": time.time(), "url": url, "site": site, "channel": s.get("login") or channel,
                          "title": s["title"], "start": s["start"], "total": total, "from": frm,
                          "approx": bool(s.get("approx")), "moments": moments,
                          "shown": min(core.PICK_BATCH, len(moments))}
            save_state()
            self.prune()
            self.queue_previews()
        except JobError as e:
            self.error = str(e)
        except Exception as e:
            log.exception("приложение: сбор моментов %s", url)
            self.error = f"не собрал клипы: {e}"
        finally:
            self.loading = None
            self.sver += 1

    def more(self):
        with self.lock:
            self.s["shown"] = min(self.s["shown"] + core.PICK_BATCH, len(self.s["moments"]))
        save_state()
        self.queue_previews()
        self.sver += 1

    def take(self, m, long=False, cut_text="", caption="", pool=""):
        """Момент → в «Отобранные» (кнопка «Взять» и app_cli take). → текст ошибки или None."""
        try:
            c = core.parse_cut(cut_text or "")
        except CutError as e:
            return f"{e} — пример: -1 -5 · до 20"
        sh, lg = m["short"], m["long"]
        meta = {"key": self.key(m), "title": self.s["title"], "punch": m["punch"], "n": m["n"], "long": long,
                "approx": bool(m.get("approx")), "other": self.clip_url(sh if long else lg)}
        if not DL.add_pick(self.clip_url(lg if long else sh), c, (caption or "").strip(), meta, (pool or "").strip()):
            return "уже в работе"
        return None

    def queue_previews(self):
        for m in self.s["moments"][:self.s["shown"]]:
            key = self.key(m)
            if key in self.prev:
                continue
            if (PREVIEWS / key / "preview.mp4").exists():
                self.prev[key] = {"st": "done"}
            else:
                self.prev[key] = {"st": "wait"}
                self.pool.submit(self._preview, key, m["short"]["id"], self.s["site"])
        self.pver += 1

    def _preview(self, key, clip_id, site):
        self.prev[key] = {"st": "work"}
        self.pver += 1
        work = PREVIEWS / key
        try:
            core.make_preview(clip_id, work, site)
            (work / "clip.ts").unlink(missing_ok=True)   # куски Kick, если ffmpeg сам не справился
            self.prev[key] = {"st": "done"}
        except Exception as e:
            if not isinstance(e, JobError):
                log.exception("приложение: превью %s", key)
            shutil.rmtree(work, ignore_errors=True)
            self.prev[key] = {"st": "error", "err": str(e)}
        self.pver += 1

    def prune(self):
        try:
            dirs = sorted((d for d in PREVIEWS.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)
        except OSError:
            return
        for d in dirs[:-KEEP_PREVIEWS]:
            shutil.rmtree(d, ignore_errors=True)


# ---------- прогон через Edits на айфоне ----------
# Как у бота: «📱 Edits» под готовым вертикальным видео → ожидание; айфон свободен и с последнего нажатия прошло
# core.EDITS_GATHER_S → все ждущие одной пачкой (папка в edits_topic.jobs_dir, процесс edits_iphone.py). Очередь
# айфона общая с ботом (tools/wda/edits_queue.py), вопросы прогона — сверху на любом экране (question_box) и в Telegram.
# phone_only — готовое остаётся только в галерее айфона (как у «Отобранных» в боте); иначе экспорт возвращается
# на ноутбук и кладётся рядом с исходником: <имя>_edits.mp4.

def export_dir(cfg):
    """Папка готового после Edits (режим «забрать на ноутбук»)."""
    return Path(cfg["out_dir"]) / "готово-edits"


class EditsRuns:
    def __init__(self, cfg, saved):
        self.cfg = cfg
        self.enabled = bool(cfg.get("edits_topic"))
        saved = saved or {}
        self.pending = saved.get("pending", [])     # id загрузок, ждущих отправки
        self.jobs = saved.get("jobs", [])
        self.phone_only = saved.get("phone_only", True)
        self.last = 0                                # время последнего нажатия
        self.queue = []                              # общая очередь айфона (и пачки бота): для экрана
        self.version = 0
        self.lock = threading.Lock()
        if self.enabled:
            threading.Thread(target=self.loop, daemon=True).start()

    def changed(self):
        self.version += 1
        DL.changed()   # статус «📱» живёт в карточке загрузки, она же сохраняет всё в app_state.json

    def add(self, item_id):
        it = find_item(item_id)
        if not (it and it["status"] == "done" and it["h"] > it["w"] and Path(it["file"]).exists()):
            return "в Edits — только готовые вертикальные видео 9:16"
        with self.lock:
            if item_id in self.pending or it.get("edits") in ("pending", "queued"):
                return "уже в очереди"
            self.pending.append(item_id)
            it["edits"] = "pending"
            self.last = time.time()
        self.changed()
        return None

    def unpend(self, item_id):
        with self.lock:
            if item_id in self.pending:
                self.pending.remove(item_id)
                it = find_item(item_id)
                if it:
                    it.pop("edits", None)
        self.changed()

    def loop(self):
        while True:
            try:
                self.dispatch()
                self.watch()
            except Exception:
                log.exception("приложение: Edits")
            time.sleep(3)

    def dispatch(self):
        if not self.pending or time.time() - self.last < core.EDITS_GATHER_S:
            return
        if core.edits_busy(self.cfg, [j for j in self.jobs if j["status"] == "run"]):
            return
        with self.lock:
            take, self.pending = list(self.pending), []
        job = Path(self.cfg["edits_topic"]["jobs_dir"]) / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_app")
        job.mkdir(parents=True, exist_ok=True)
        files = []
        for i, item_id in enumerate(take, 1):
            it = find_item(item_id)
            if not it or not Path(it["file"]).exists():
                continue
            dst = job / f"{i:02d}_{Path(it['file']).stem[:40]}.mp4"
            try:
                os.link(it["file"], dst)           # тот же диск — мгновенно и без копии
            except OSError:
                shutil.copy2(it["file"], dst)
            files.append({"file": dst.name, "item": item_id})
            it["edits"] = "queued"
        if not files:
            shutil.rmtree(job, ignore_errors=True)
            return
        j = {"id": job.name, "dir": str(job), "files": files, "phone_only": self.phone_only, "status": "run",
             "at": datetime.now().isoformat(timespec="seconds")}
        j["pid"], j["created"] = core.edits_launch(self.cfg, job, phone_only=j["phone_only"])
        with self.lock:
            self.jobs.append(j)
        log.info("приложение: Edits пачка %s, %d шт", job.name, len(files))
        self.changed()

    def watch(self):
        eq = core.equeue(self.cfg)
        q = eq.edit()
        self.queue = [{"series": e.get("series", "?"), "started": bool(e.get("started")),
                       "mine": any(j["status"] == "run" and j.get("pid") == e["pid"] for j in self.jobs)}
                      for e in q]
        dirty = False
        for j in list(self.jobs):
            if j["status"] != "run":
                continue
            done = core.edits_done(j["dir"])
            for f in j["files"]:
                if f["file"] in done and not f.get("got"):
                    self.got(j, f)
                    dirty = True
            if core.proc_alive(j["pid"], j["created"]):
                pos, total = eq.position(j["pid"])
                if (pos, total) != (j.get("pos"), j.get("total")):
                    j["pos"], j["total"] = pos, total
                    dirty = True
                continue
            if all(f.get("got") for f in j["files"]):
                j["status"] = "done"
                shutil.rmtree(j["dir"], ignore_errors=True)
                # исходники прогон откладывает в tools/wda/backup/<папка> (диск C) — они и так есть у нас
                shutil.rmtree(Path(self.cfg["edits_topic"]["data_dir"]) / "backup" / Path(j["dir"]).name,
                              ignore_errors=True)
                log.info("приложение: Edits пачка %s готова", j["id"])
            else:   # ответ «стоп», сбой, телефон не на связи — ждём «🔁 Повторить»
                j["status"] = "stuck"
                j["offline"] = (Path(j["dir"]) / ".phone-offline").exists()
                for f in j["files"]:
                    it = find_item(f["item"])
                    if it and not f.get("got"):
                        it["edits"] = "stuck"
            dirty = True
        if dirty:
            with self.lock:
                fin = [j for j in self.jobs if j["status"] in ("done", "cancelled")]
                drop = {j["id"] for j in fin[:-KEEP_RUNS]}
                self.jobs = [j for j in self.jobs if j["id"] not in drop]
            self.changed()

    def set_mode(self, phone_only):
        """Режим «готовое — куда» для новых пачек и для идущих: edits_iphone.py перечитывает .edits-mode
        перед забором каждой пачки (26.10.2026)."""
        self.phone_only = phone_only
        for j in self.jobs:
            if j["status"] in ("run", "stuck"):
                j["phone_only"] = phone_only
                try:
                    (Path(j["dir"]) / ".edits-mode").write_text("phone" if phone_only else "pc", encoding="utf-8")
                except OSError as e:
                    log.warning("приложение: режим Edits для %s не записал: %s", j["id"], e)
        self.changed()

    def got(self, j, f):
        """Файл прошёл Edits: остался только в галерее айфона — только отметка; иначе экспорт → <исходник>_edits.mp4
        рядом с ним. Как именно его закончил айфон — по .iphone-phone-only.txt (режим могли сменить посреди прогона)."""
        f["got"] = True
        it = find_item(f["item"])
        if not it:
            return
        phone = Path(j["dir"]) / ".iphone-phone-only.txt"
        if f["file"] in (phone.read_text(encoding="utf-8").split() if phone.exists() else ()):
            it["edits"] = "phone"
            return
        src = Path(j["dir"]) / f["file"]
        exp = export_dir(self.cfg)
        exp.mkdir(parents=True, exist_ok=True)
        dst = exp / (Path(it["file"]).stem + "_edits.mp4")   # всё готовое после Edits — в одну папку (Дима, 27.10.2026)
        try:
            shutil.copy2(src, dst)
            it.update(edits="done", edits_file=str(dst))
        except OSError as e:
            log.warning("приложение: Edits не забрал %s: %s", src, e)
            it["edits"] = "stuck"

    def retry(self, job_id):
        j = next((j for j in self.jobs if j["id"] == job_id and j["status"] == "stuck"), None)
        if not j:
            return
        j["pid"], j["created"] = core.edits_launch(self.cfg, j["dir"], phone_only=j["phone_only"])
        j.update(status="run", offline=False, pos=None)
        for f in j["files"]:
            it = find_item(f["item"])
            if it and not f.get("got"):
                it["edits"] = "queued"
        self.changed()

    def move(self, job_id, step):
        j = next((j for j in self.jobs if j["id"] == job_id and j["status"] == "run"), None)
        if j and core.equeue(self.cfg).move(j["pid"], step):
            j["pos"] = None   # пересчитает watch
            self.changed()

    def staged(self):
        """Файлы с компьютера: загружены, но не отправлены — прогон начнётся только по кнопке."""
        return [it for it in DL.items if it.get("dest") == "upload" and it["status"] == "done" and not it.get("edits")]

    def staged_bad(self, item_id):
        it = find_item(item_id)
        return bool(it and not it["h"] > it["w"])

    def start_staged(self):
        """«Начать прогон»: всё загруженное → в очередь айфона сразу, без ожидания 20 с."""
        n = sum(1 for it in self.staged() if not self.add(it["id"]))
        if n:
            self.last = 0
        return n

    def remove_job(self, job_id):
        """Убрать карточку пачки из списка (кнопка ✕): не идущую. Папку задания стираем, файлы-исходники и готовое не трогаем."""
        j = next((j for j in self.jobs if j["id"] == job_id), None)
        if not j or j["status"] == "run":
            return "идущую пачку убрать нельзя — сначала остановить"
        self.jobs.remove(j)
        shutil.rmtree(j["dir"], ignore_errors=True)
        for f in j["files"]:
            it = find_item(f["item"])
            if it and it.get("edits") in ("stuck", "queued"):
                it.pop("edits", None)
        self.changed()
        return None

    def stop(self, job_id):
        """Остановить пачку, которая уже идёт на айфоне: убиваем процесс прогона с дочерними. Уже готовые остаются,
        watch() увидит мёртвый процесс и переведёт пачку в «застряла» — 🔁 продолжит с места остановки.
        Что было на экране айфона в этот момент (открытый проект Edits, видео в галерее) — на телефоне, чистить руками."""
        import psutil
        j = next((j for j in self.jobs if j["id"] == job_id and j["status"] == "run"), None)
        if not j:
            return "пачка уже не идёт"
        if core.proc_alive(j["pid"], j["created"]):
            p = psutil.Process(j["pid"])
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        log.info("приложение: Edits пачка %s остановлена кнопкой", j["id"])
        return None

    def cancel(self, job_id):
        """Только ждущую пачку: она ещё не трогала телефон, процесс можно просто остановить."""
        import psutil
        j = next((j for j in self.jobs if j["id"] == job_id and j["status"] == "run"), None)
        if not j or not j.get("pos"):
            return "уже идёт на айфоне — остановить: кнопка «Остановить прогон» или `job stop JOB`"
        if core.proc_alive(j["pid"], j["created"]):
            p = psutil.Process(j["pid"])
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        j["status"] = "cancelled"
        shutil.rmtree(j["dir"], ignore_errors=True)
        for f in j["files"]:
            it = find_item(f["item"])
            if it and not f.get("got"):
                it.pop("edits", None)
        self.changed()
        return None


# ---------- подборки «топ-N моментов» ----------
# Пул = чистые клипы одного стримера (файлы остаются где лежат). Из N клипов собираем максимум роликов «топ-3 / топ-5»,
# где два ролика делят не больше 1 (топ-3) / 2 (топ-5) общих моментов. Логика — tops.py.

SHARED = {3: 1, 5: 2}   # сколько общих моментов разрешено у двух роликов — «средняя» уникальность (по умолчанию)
# уровни уникальности (27.10.2026): сколько общих моментов у двух роликов; чем меньше — тем меньше роликов выходит
LEVELS = {"strong": {3: 0, 5: 1}, "medium": SHARED, "weak": {3: 2, 5: 3}}
LEVEL_NAMES = {"strong": "сильная уникальность", "medium": "средняя", "weak": "слабая"}


class Tops:
    def __init__(self, saved):
        s = saved or {}
        self.s = {"pools": s.get("pools", {}), "sel": s.get("sel"), "k": s.get("k", 3), "title": s.get("title", ""),
                  "runs": s.get("runs", [])[-10:], "tails": s.get("tails", {}),
                  "shared": {str(k): v for k, v in (s.get("shared") or {}).items()}}
        self.last = None
        self.build = None            # {"pool","k","total","done","stage","error"} пока идёт сборка
        self.version = 0
        self.lock = threading.Lock()

    def changed(self):
        self.version += 1
        save_state()

    def pool(self, name=None):
        return self.s["pools"].get(name or self.s["sel"], [])

    def shared(self, k=None):
        """Сколько общих моментов разрешено у двух роликов для топ-k (0…k-1)."""
        k = k or self.s["k"]
        return max(0, min(k - 1, int(self.s["shared"].get(str(k), SHARED[k]))))

    def set_shared(self, k, value):
        self.s["shared"][str(k)] = max(0, min(k - 1, int(value)))
        self.changed()

    def level(self, k=None):
        """strong / medium / weak, если число совпадает с пресетом, иначе None (своё значение)."""
        k = k or self.s["k"]
        return next((n for n, v in LEVELS.items() if v[k] == self.shared(k)), None)

    def add_clip(self, pool, file, name=""):
        """Клип в подборку стримера. → текст ошибки или None."""
        file = str(file)
        if not Path(file).is_file():
            return f"нет файла {file}"
        pool = (pool or "").strip() or "без стримера"
        with self.lock:
            clips = self.s["pools"].setdefault(pool, [])
            if any(c["file"] == file for c in clips):
                return None
            clips.append({"file": file, "name": (name or Path(file).stem).strip()})
            self.s["sel"] = self.s["sel"] or pool
        self.changed()
        return None

    def add_folder(self, pool, folder):
        d = Path(folder.strip().strip('"'))
        if not d.is_dir():
            return f"нет папки {d}"
        files = sorted(d.glob("*.mp4"))
        if not files:
            return "в папке нет .mp4"
        for f in files:
            self.add_clip(pool or d.name, f)
        self.s["sel"] = (pool or d.name).strip()
        self.changed()
        return None

    def rename(self, pool, i, name):
        clips = self.pool(pool)
        if 0 <= i < len(clips) and name.strip():
            clips[i]["name"] = name.strip()
            self.changed()

    def remove(self, pool, i):
        """Убрать из подборки (файл на диске остаётся)."""
        clips = self.pool(pool)
        if 0 <= i < len(clips):
            clips.pop(i)
            if not clips:
                self.s["pools"].pop(pool, None)
                if self.s["sel"] == pool:
                    self.s["sel"] = next(iter(self.s["pools"]), None)
            self.changed()

    def default_title(self, k):
        """«ТОП 3 МОМЕНТА» + хвост подборки, как его ввёл человек (стример в нужном падеже — сами не склоняем)."""
        tail = self.s["tails"].get(self.s["sel"], "").strip().upper()
        return f"ТОП {k} " + ("МОМЕНТА" if k < 5 else "МОМЕНТОВ") + (" " + tail if tail else "")

    def folder(self, pool):
        """Папка подборки: сюда копируются клипы, скачанные «в подборку»."""
        return Path(DL.cfg["out_dir"]) / "топ-клипы" / re.sub(r'[\\/:*?"<>|]+', " ", pool).strip()

    def folders(self):
        """Названия подборок: созданные в приложении + папки в топ-клипы (на случай, если создавали руками)."""
        root = Path(DL.cfg["out_dir"]) / "топ-клипы"
        found = [p.name for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
        return sorted(set(self.s["pools"]) | set(found))

    def create_folder(self, name):
        """Пустая папка подборки (появляется в списках выбора). → её название или "" если названия нет."""
        name = " ".join((name or "").split())
        if not name:
            return ""
        self.folder(name).mkdir(parents=True, exist_ok=True)
        DL.changed(save=False)   # списки на экране перерисуются и увидят новую папку
        self.version += 1
        return name

    def take_file(self, pool, file, name=""):
        """Готовый файл → копия в папку подборки и клип в подборке. Оригинал остаётся где был. → ошибка или None."""
        if not Path(file).is_file():
            return f"нет файла {file}"
        dst = self.folder(pool)
        dst.mkdir(parents=True, exist_ok=True)
        f = dst / Path(file).name
        if not f.exists():
            shutil.copy2(file, f)
        return self.add_clip(pool, f, name)

    def take_download(self, it):
        self.take_file(it["pool"], it["file"], it.get("caption", ""))

    def forecast(self):
        """Сколько роликов выйдет самое большее (по формуле); точное число — после расчёта при сборке."""
        n, k = len(self.pool()), self.s["k"]
        return tops.limit(n, k, self.shared(k))[0] if n >= k else 0

    def start(self, count=0):
        if self.build:
            return "уже собираю — дождись конца"
        pool, k = self.s["sel"], self.s["k"]
        clips = [dict(c) for c in self.pool()]
        if len(clips) < k:
            return f"для топ-{k} нужно минимум {k} клипов, в подборке {len(clips)}"
        if len(clips) > tops.MAX_CLIPS:
            return f"слишком много клипов ({len(clips)}), максимум {tops.MAX_CLIPS} — убери лишние"
        missing = [c["name"] for c in clips if not Path(c["file"]).is_file()]
        if missing:
            return "нет файла у: " + ", ".join(missing)
        title = (self.s["title"] or "").strip() or self.default_title(k)
        self.build = {"pool": pool, "k": k, "total": 0, "done": 0, "stage": "считаю, какие ролики выйдут", "error": ""}
        self.changed()
        threading.Thread(target=self.run, args=(pool, k, clips, title, count), daemon=True).start()
        return None

    def run(self, pool, k, clips, title, count):
        b = self.build
        try:
            order, bound = tops.plan(len(clips), k, self.shared(k))
            if count:
                order = order[:count]
            b["total"] = len(order)
            out = Path(DL.cfg["out_dir"]) / "подборки" / f"{pool}_топ{k}_{datetime.now():%Y-%m-%d_%H%M}"
            out.mkdir(parents=True, exist_ok=True)
            work = Path(DL.cfg["tmp_dir"]) / "tops"
            rows = []
            for v, o in enumerate(order, 1):
                b["stage"] = f"ролик {v} из {len(order)}"
                self.version += 1
                tops.build_one(clips, o, title, out / f"топ{k}_{v:02d}.mp4", work)
                rows.append(f"топ{k}_{v:02d}: " + " | ".join(f"{k - i}. {clips[c]['name']}" for i, c in enumerate(o)))
                b["done"] = v
            tops.write_list(out, rows)
            shutil.rmtree(work, ignore_errors=True)
            self.s["runs"].append({"pool": pool, "k": k, "count": len(order), "dir": str(out),
                                   "at": datetime.now().isoformat(timespec="seconds")})
            log.info("подборка: %s топ-%s, %s роликов → %s", pool, k, len(order), out)
        except Exception as e:   # noqa: BLE001
            log.exception("подборка не собралась")
            b["error"] = str(e) or e.__class__.__name__
            self.s["runs"].append({"pool": pool, "k": k, "count": b["done"], "dir": "", "error": b["error"],
                                   "at": datetime.now().isoformat(timespec="seconds")})
            return
        finally:
            self.last = b
            self.build = None
            self.changed()



# ---------- нарезка исходников на моменты (экран «Нарезка») ----------
# Очередь исходников: ссылки (с обрезкой по времени, как в «Загрузках») и файлы. Каждый качается (общая очередь загрузок),
# потом автоматика режет на куски по сменам сцены, ставит рамки и вырезает рекламу (split.py). Человек правит выбранный
# исходник в редакторе (split_editor.js) → «Отправить» → куски в папку топ-3/5 → выбирается следующий.

BUSY = ("download", "analyze", "cutting")


class Splitter:
    def __init__(self, saved):
        self.s = saved if saved and "items" in saved else {"items": [], "cur": None, "seq": 0}
        for e in self.s["items"]:
            if e["status"] in BUSY:   # закрыли посреди работы
                e.update(status="error", error="приложение закрывали посреди работы — добавь ещё раз")
        self.version = 0
        self.routes = {}                       # папка исходника → адрес для плеера
        self.heavy = threading.Lock()          # разбор и резка — по одному, чтобы не душить ноутбук

    def changed(self):
        self.version += 1
        save_state()

    def entry(self, eid):
        return next((e for e in self.s["items"] if e["id"] == eid), None)

    def active(self):
        e = self.entry(self.s.get("cur"))
        if not e and self.s["items"]:
            e = next((x for x in self.s["items"] if x["status"] != "sent"), self.s["items"][-1])
            self.s["cur"] = e["id"]
        return e

    def select(self, eid):
        if self.entry(eid):
            self.s["cur"] = eid
            self.changed()

    def add(self, text, pool):
        """Ссылки и пути (хоть слитно, хоть по строкам) → в очередь. → (сколько добавлено, ошибки)."""
        pool = (pool or "").strip()
        if not pool:
            return 0, ["выбери папку топ-3/5, куда пойдут куски"]
        errors, added = [], 0
        for line in unglue(text).splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            e = {"id": self.s["seq"] + 1, "pool": pool, "status": "download", "name": "", "src": "", "error": "",
                 "rev": 1, "text": line}
            if Path(line).is_file():
                self.s["seq"] += 1
                e["src"], e["name"], e["status"] = line, Path(line).name, "analyze"
                self.s["items"].append(e)
                self.s["cur"] = self.s.get("cur") or e["id"]
                threading.Thread(target=self._analyze, args=(e["id"],), daemon=True).start()
                added += 1
                continue
            before = DL.seq
            errs = DL.add(line)
            errors += errs
            for it in DL.items:
                if it["id"] > before:   # на одну строку — одна загрузка (несколько ссылок в строке — несколько)
                    self.s["seq"] += 1
                    x = dict(e, id=self.s["seq"], item=it["id"], name=it["url"])
                    it["split"] = True
                    self.s["items"].append(x)
                    added += 1
        if added:
            TP.create_folder(pool)
            self.active()
            self.changed()
        return added, errors

    def on_download(self, item_id, path, err=""):
        e = next((x for x in self.s["items"] if x.get("item") == item_id), None)
        if not e:
            return
        if err or not path:
            e.update(status="error", error=err or "не скачалось")
            self.changed()
            return
        e.update(src=str(path), name=Path(path).name, status="analyze")
        self.changed()
        threading.Thread(target=self._analyze, args=(e["id"],), daemon=True).start()

    def _analyze(self, eid):
        e = self.entry(eid)
        with self.heavy:
            try:
                a = split.analyze(e["src"])
                for g in a["segs"]:   # рекламные куски split.analyze уже пометил «не брать»
                    g.setdefault("name", "")
                    g.setdefault("skip", False)
                e.update(a, status="ready", rev=e["rev"] + 1)
                log.info("нарезка: %s → %s кусков, реклама: %s", e["src"], len(a["segs"]), a["ads"])
            except Exception as ex:   # noqa: BLE001
                log.exception("нарезка: разбор не вышел")
                e.update(status="error", error=str(ex) or ex.__class__.__name__)
        self.changed()

    def video_url(self, e):
        p = Path(e["src"])
        url = media_url(p)
        if url:
            return url
        d = str(p.parent.resolve())
        if d not in self.routes:
            self.routes[d] = f"/splitsrc{len(self.routes)}"
            app.add_media_files(self.routes[d], d)
        return self.routes[d] + "/" + p.name

    def remove(self, eid):
        e = self.entry(eid)
        if e and e["status"] != "cutting":
            self.s["items"].remove(e)
            if self.s.get("cur") == eid:
                self.s["cur"] = None
            self.changed()

    def send(self, segs, eid=None):
        """Куски из редактора → нарезать в фоне в папку подборки. → текст ошибки или None."""
        e = self.entry(eid) if eid else self.active()
        if not e or e["status"] != "ready":
            return "нечего резать — исходник ещё не разобран"
        take = [g for g in segs if not g.get("skip")]
        if not take:
            return "все куски помечены «не брать»"
        e["segs"] = segs
        e.update(status="cutting", done=0, total=len(take))
        self.changed()
        threading.Thread(target=self._cut, args=(e["id"], take), daemon=True).start()
        return None

    def _cut(self, eid, take):
        e = self.entry(eid)
        pool, src = e["pool"], e["src"]
        dst = TP.folder(pool)
        dst.mkdir(parents=True, exist_ok=True)
        stem = Path(src).stem[:40]
        with self.heavy:
            try:
                groups = []
                for g in take:   # куски с join склеиваются с предыдущим (между ними была вырезана реклама)
                    if g.get("join") and groups:
                        groups[-1].append(g)
                    else:
                        groups.append([g])
                e["total"] = len(groups)
                for i, grp in enumerate(groups, 1):
                    g = grp[0]
                    name = " ".join((g.get("name") or "").split()) or f"момент {i}"
                    safe = re.sub(r'[\\/:*?"<>|]+', " ", name).strip()
                    out = dst / f"{stem}_{i:02d}_{safe}.mp4"
                    split.cut_many(src, [(x["start"], x["end"]) for x in grp], g["crop"], out)
                    TP.add_clip(pool, out, name)
                    e["done"] = i
                    self.version += 1
                e.update(status="sent", sent=str(dst))
                log.info("нарезка: %s кусков → %s", len(take), dst)
                nxt = next((x for x in self.s["items"] if x["status"] in ("ready", "analyze", "download")), None)
                if nxt and self.s.get("cur") == eid:
                    self.s["cur"] = nxt["id"]   # дальше — следующий исходник из очереди
            except Exception as ex:   # noqa: BLE001
                log.exception("нарезка: резка не вышла")
                e.update(status="error", error=str(ex) or ex.__class__.__name__)
        self.changed()


def find_item(item_id):
    return next((it for it in DL.items if it["id"] == item_id), None)


# ---------- экраны ----------

def show_in_folder(path):
    subprocess.Popen(["explorer", "/select,", str(path)])


def media_url(path):
    """Файл из out_dir → адрес для плеера (/out/…); не из out_dir — None."""
    try:
        rel = Path(path).resolve().relative_to(Path(DL.cfg["out_dir"]).resolve())
    except ValueError:
        return None
    return "/out/" + rel.as_posix()


def downloads_page():
    ui.label("Загрузки").classes("text-2xl font-bold")
    with ui.card().classes("w-full gap-2"):
        links = ui.textarea(placeholder="Ссылка — видео целиком. Обрезка на той же строке после ссылки:\n"
                                        "kick.com/…/clip_AAA -14        убрать первые 14 с\n"
                                        "tiktok.com/… -5 -3             5 с в начале и 3 с в конце\n"
                                        "youtube.com/… до 58            только до 58-й секунды\n"
                                        "kick.com/…/videos/BBB 1:20:00-1:20:10   кусок записи стрима\n"
                                        "Можно несколько ссылок — каждая с новой строки.") \
            .props("outlined autogrow input-style='min-height:7.5em; font-family:monospace'").classes("w-full")
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            caption = ui.input("Подпись — тогда сразу креатив 9:16 с жёлтой плашкой (необязательно)") \
                .props("outlined dense clearable").classes("flex-grow")
            errors = ui.column().classes("gap-0")

            def submit():
                errors.clear()
                errs = DL.add(links.value or "", (caption.value or "").strip())
                with errors:
                    for e in errs:
                        ui.label("❌ " + e).classes("text-red-400 text-sm")
                if not errs:
                    links.value = ""

            ui.button("Скачать", icon="download", on_click=submit) \
                .props("unelevated color=amber text-color=black").classes("h-10")
        links.on("keydown.ctrl.enter", submit)
        with ui.row().classes("w-full items-center gap-3"):
            ui.icon("format_list_numbered").classes("text-amber-400")
            new_name = ui.input("Название новой папки", placeholder="например: Евелин") \
                .props("outlined dense clearable").classes("w-56")
            pool_sel = ui.select(TP.folders(), label="Папка топ-3 / топ-5", clearable=True, with_input=True) \
                .props("outlined dense").classes("w-56")

            def make_folder():
                name = TP.create_folder(new_name.value or "")
                if not name:
                    ui.notify("Впиши название папки", type="warning")
                    return
                pool_sel.set_options(TP.folders(), value=name)   # созданная сразу выбрана
                new_name.value = None
                ui.notify(f"Папка «{name}» создана и выбрана", type="positive")

            def to_pool():
                name = (pool_sel.value or "").strip()
                if not name:
                    ui.notify("Выбери папку из списка (или сначала создай)", type="warning")
                    return
                errors.clear()
                errs = DL.add(links.value or "", (caption.value or "").strip(), name)
                with errors:
                    for e in errs:
                        ui.label("❌ " + e).classes("text-red-400 text-sm")
                if not errs:
                    links.value = ""
                    ui.notify(f"Скачаю в папку «{name}»", type="positive")

            btn = "unelevated outline no-caps color=amber"
            ui.button("Создать папку", icon="create_new_folder", on_click=make_folder).props(btn) \
                .style("white-space:nowrap")
            ui.button("Скачать в папку", icon="folder", on_click=to_pool).props(btn).style("white-space:nowrap")
    ui.label("Ctrl+Enter — скачать. Готовое сохраняется в " + DL.cfg["out_dir"]).classes("text-xs text-gray-500")

    items_list(lambda it: it.get("dest") not in ("picked", "upload"), "Пока пусто — вставь ссылку выше")


def items_list(which, empty):
    """Список загрузок (which — какие), перерисовывается сам, когда что-то поменялось."""
    @ui.refreshable
    def items():
        rows = [it for it in reversed(DL.items) if which(it)]
        if not rows:
            ui.label(empty).classes("text-gray-500 py-6")
        for it in rows:
            item_card(it)

    seen = {"v": -1}

    def poll():
        if seen["v"] != DL.version:
            seen["v"] = DL.version
            items.refresh()

    with ui.column().classes("w-full gap-3"):
        items()
    ui.timer(0.5, poll)


def item_card(it):
    st = it["status"]
    with ui.card().classes("w-full p-3"):
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            if st == "done" and media_url(it["file"]):
                vertical = it["h"] > it["w"]
                poster = f"poster=/thumbs/{it['id']}.jpg" if (THUMBS / f"{it['id']}.jpg").exists() else ""
                ui.video(media_url(it["file"]), autoplay=False).props(f"preload=metadata {poster}") \
                    .classes("rounded-lg bg-black shrink-0") \
                    .style("height:260px;width:146px;object-fit:contain" if vertical
                           else "height:180px;width:320px;object-fit:contain")
            with ui.column().classes("gap-1 min-w-0 flex-grow"):
                meta = it.get("meta")
                if meta:   # из «Выбора клипов»: стрим и момент
                    ui.label(meta["title"]).classes("font-medium truncate w-full")
                    ui.label(f"{'~' if meta.get('approx') else 'панч '}{core.tc(meta['punch'])} · "
                             f"заклипали {meta['n']} раз · {'длинный' if meta.get('long') else 'клип'}") \
                        .classes("text-sm text-gray-400")
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    icon, color = {"wait": ("schedule", "text-gray-400"), "work": ("downloading", "text-amber-400"),
                                   "done": ("check_circle", "text-green-400"),
                                   "error": ("error", "text-red-400")}[st]
                    ui.icon(icon).classes(color)
                    ui.link(it["url"], it["url"], new_tab=True).classes("font-mono text-sm truncate text-gray-200")
                with ui.row().classes("items-center gap-2"):
                    ui.badge(cut_label(it["cut"])).props("outline color=grey")
                    if it.get("caption"):
                        ui.badge("креатив «" + it["caption"] + "»").props("outline color=amber")
                    if it.get("pool"):
                        ui.badge("подборка «" + it["pool"] + "»").props("outline color=amber")
                    if st == "done":
                        q = f"{it['h']}p{it['fps']}" if it["fps"] >= 50 else f"{it['h']}p"
                        ui.label(f"{it.get('streamer', '')} · {q} · {core.tc(it['duration'])}") \
                            .classes("text-sm text-gray-400")
                if st == "wait":
                    pos = [x["id"] for x in DL.items if x["status"] == "wait"].index(it["id"]) + 1
                    ui.label(f"в очереди: {pos}").classes("text-sm text-gray-400")
                elif st == "work":
                    with ui.row().classes("items-center gap-2"):
                        ui.spinner(size="sm", color="amber")
                        ui.label((it.get("stage") or "в работе") + "…").classes("text-sm text-amber-300")
                elif st == "error":
                    ui.label(it.get("error", "")).classes("text-sm text-red-300")
                elif st == "done":
                    ui.label(Path(it["file"]).name).classes("text-xs text-gray-500 font-mono truncate")
                    e = it.get("edits")
                    if e:
                        txt, color = {"pending": ("📱 Edits: ждёт отправки на айфон", "text-amber-300"),
                                      "queued": ("📱 Edits: в очереди айфона", "text-amber-300"),
                                      "phone": ("✅ Edits · в галерее айфона", "text-green-400"),
                                      "done": ("✅ Edits · " + Path(it.get("edits_file", "")).name, "text-green-400"),
                                      "stuck": ("⚠️ Edits остановился — «Очередь Edits» → 🔁", "text-red-300")}[e]
                        with ui.row().classes("items-center gap-1"):
                            ui.label(txt).classes(f"text-sm {color}")
                            if e == "done":
                                ui.button(icon="folder_open", on_click=lambda f=it["edits_file"]: show_in_folder(f)) \
                                    .props("flat round dense size=sm").tooltip("Готовое после Edits — в папке")
            with ui.row().classes("gap-1 shrink-0"):
                if st == "done" and ED.enabled and it["h"] > it["w"] and it.get("edits") not in ("pending", "queued"):
                    def to_edits(i=it["id"]):
                        err = ED.add(i)
                        ui.notify(err or "В очереди на айфон 📱", type="warning" if err else "positive")
                    ui.button(icon="phone_iphone", on_click=to_edits) \
                        .props("flat round dense color=amber").tooltip("Прогнать через Edits на айфоне")
                if st == "done":
                    def to_top(pool, x=it):
                        err = TP.take_file(pool, x["file"], x.get("caption", ""))
                        ui.notify(err or f"Добавлено в папку «{pool}»", type="negative" if err else "positive")
                    with ui.button(icon="library_add").props("flat round dense").tooltip("В папку топ-3 / топ-5"):
                        with ui.menu():
                            ui.menu_item("Выбери папку:").props("disable")
                            for fo in TP.folders():
                                ui.menu_item(fo, on_click=lambda fo=fo: to_top(fo))
                            if not TP.folders():
                                ui.menu_item("папок нет — создай в «Загрузках»").props("disable")
                    ui.button(icon="folder_open", on_click=lambda f=it["file"]: show_in_folder(f)) \
                        .props("flat round dense").tooltip("Показать в папке")
                if st in ("wait", "work"):
                    ui.button(icon="close", on_click=lambda i=it["id"]: DL.cancel(i)) \
                        .props("flat round dense color=red").tooltip("Отменить")
                else:
                    if st == "error":
                        ui.button(icon="refresh", on_click=lambda x=it: retry(x)) \
                            .props("flat round dense").tooltip("Ещё раз")
                    ui.button(icon="delete_outline", on_click=lambda i=it["id"]: DL.remove(i)) \
                        .props("flat round dense color=grey").tooltip("Убрать из списка (файл останется)")


def retry(it):
    DL.remove(it["id"])
    if it.get("dest") == "picked":
        DL.add_pick(it["url"], it["cut"], it.get("caption", ""), it["meta"])
        return
    line = it["url"] + ("" if not it["cut"] else " " + cut_text(it["cut"]))
    DL.add(line, it.get("caption", ""))


def cut_text(cut):
    """Обрезка обратно в тот вид, как её пишут после ссылки."""
    if "start" in cut and "end" in cut:
        return f"{core.tc(cut['start'])}-{core.tc(cut['end'])}"
    if "end" in cut:
        return f"до {cut['end']:g}"
    return f"-{cut.get('start', 0):g}" + (f" -{cut['tail']:g}" if cut.get("tail") else "")


def pick_page():
    ui.label("Выбор клипов").classes("text-2xl font-bold")
    with ui.row().classes("w-full items-start gap-3 no-wrap"):
        src = ui.input("Запись стрима Kick / Twitch или канал (= последний стрим)",
                       placeholder="kick.com/<канал>/videos/… · twitch.tv/videos/… · ?t= — моменты с этого места",
                       value=PK.s["url"] if PK.s else "").props("outlined dense clearable").classes("flex-grow")

        def find():
            err = PK.find(src.value)
            if err:
                ui.notify(err, type="negative")

        ui.button("Найти моменты", icon="search", on_click=find) \
            .props("unelevated color=amber text-color=black").classes("h-10")
        src.on("keydown.enter", find)

    @ui.refreshable
    def head():
        if PK.loading:
            with ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm", color="amber")
                ui.label("Собираю клипы стрима… первый раз Kick — до ~5 мин, Twitch — до минуты") \
                    .classes("text-amber-300")
        if PK.error:
            ui.label("❌ " + PK.error).classes("text-red-400")
        s = PK.s
        if s:
            frm = f" с {core.tc(s['from'])}" if s.get("from") else ""
            ui.label(f"📺 {s['title']} · {s['start'][:10]} · клипов {s['total']} → моментов "
                     f"{len(s['moments'])}{frm}, самые заклипанные первыми").classes("text-gray-300")
            if s.get("approx"):
                ui.label("Записи у канала нет — моменты по времени создания клипов, время примерное") \
                    .classes("text-sm text-gray-500")
            ui.label("«Взять» — клип в полном качестве уйдёт в «Отобранные». Обрезка: -1 -5 · до 20. "
                     "С подписью — сразу креатив 9:16.").classes("text-xs text-gray-500")
    head()

    grid = ui.grid(columns=3).classes("w-full gap-4")
    cards = {}                                  # ключ момента → (превью, статус, что показано)
    drawn = {"sid": None, "n": 0}

    @ui.refreshable
    def more_btn():
        s = PK.s
        if s and s["shown"] < len(s["moments"]):
            left = len(s["moments"]) - s["shown"]
            ui.button(f"Ещё {min(core.PICK_BATCH, left)} (осталось {left})", icon="expand_more", on_click=PK.more) \
                .props("outline color=amber")
        elif s and s["moments"]:
            ui.label("Это все моменты стрима.").classes("text-gray-500")
    more_btn()

    def draw():
        """Новый стрим — карточки заново; «ещё» — дорисовать снизу, не трогая набранное в полях."""
        s = PK.s
        if not s:
            return
        if drawn["sid"] != s["sid"]:
            grid.clear()
            cards.clear()
            drawn.update(sid=s["sid"], n=0)
        with grid:
            for i in range(drawn["n"], s["shown"]):
                m = s["moments"][i]
                cards[PK.key(m)] = moment_card(m, i, len(s["moments"]))
        drawn["n"] = s["shown"]

    seen = {"s": -1, "p": -1, "d": -1}

    def poll():
        if seen["s"] != PK.sver:
            seen["s"] = PK.sver
            head.refresh()
            more_btn.refresh()
            draw()
        if seen["p"] != PK.pver or seen["d"] != DL.version:
            seen["p"], seen["d"] = PK.pver, DL.version
            for key, (prev, status, shown) in cards.items():
                now = (PK.prev.get(key, {}).get("st"), status_sig(key))
                if shown[0] != now[0]:
                    prev.refresh()
                if shown[1] != now[1]:
                    status.refresh()
                shown[:] = now

    draw()
    ui.timer(0.5, poll)


def status_sig(key):
    return tuple((it["id"], it["status"], it["stage"]) for it in DL.items if (it.get("meta") or {}).get("key") == key)


def moment_card(m, i, total):
    key = PK.key(m)
    sh, lg, p = m["short"], m["long"], m["punch"]
    with ui.card().classes("p-2 gap-2 w-full"):
        @ui.refreshable
        def preview():
            st = PK.prev.get(key, {})
            if st.get("st") == "done":
                thumb = PREVIEWS / key / "thumb.jpg"
                ui.video(f"/previews/{key}/preview.mp4", autoplay=False) \
                    .props(f"preload=metadata {'poster=/previews/' + key + '/thumb.jpg' if thumb.exists() else ''}") \
                    .classes("w-full rounded-lg bg-black").style("aspect-ratio:16/9;object-fit:contain")
            else:
                with ui.element("div").classes("w-full rounded-lg bg-black/60 flex items-center justify-center") \
                        .style("aspect-ratio:16/9"):
                    if st.get("st") == "error":
                        ui.label("превью не вышло: " + st.get("err", "")).classes("text-xs text-red-300 p-2")
                    else:
                        ui.spinner(size="md", color="amber")
        preview()
        with ui.row().classes("w-full items-baseline justify-between no-wrap"):
            ui.label(f"{i + 1}/{total}").classes("font-bold")
            ui.label(f"{'~' if m.get('approx') else 'панч '}{core.tc(p)} · заклипали {m['n']} раз") \
                .classes("text-xs text-gray-400")
        sl, ll = int(sh["e"] - sh["s"]), int(lg["e"] - lg["s"])
        offs = "" if m.get("approx") else f", панч на {core.tc(p - sh['s'])}"
        with ui.row().classes("w-full gap-3 text-xs"):
            ui.link(f"клип {sl} с{offs}", PK.clip_url(sh), new_tab=True).classes("text-gray-300")
            ui.link(f"длинный {ll} с", PK.clip_url(lg), new_tab=True).classes("text-gray-300")
        which = ui.toggle({"short": f"клип {sl} с", "long": f"длинный {ll} с"}, value="short") \
            .props("dense no-caps toggle-color=amber toggle-text-color=black").classes("w-full")
        cut = ui.input(placeholder="обрезка: -1 -5 · до 20").props("dense outlined").classes("w-full")
        cap = ui.input(placeholder="подпись → креатив 9:16").props("dense outlined").classes("w-full")
        top_pool = ui.select(TP.folders(), label="папка топ-3/5 (выбери или впиши новую)", with_input=True,
                             new_value_mode="add-unique", clearable=True).props("dense outlined").classes("w-full")

        def take():
            err = PK.take(m, which.value == "long", cut.value, cap.value)
            if err:
                ui.notify(err, type="warning")

        def take_top():
            """В папку топ-3/5: без подписи, формат как есть — 9:16 с размытым фоном сделает сборка подборки."""
            name = (top_pool.value or "").strip()
            if not name:
                ui.notify("Выбери папку топ-3/5 или впиши новую", type="warning")
                return
            err = PK.take(m, which.value == "long", cut.value, "", name)
            ui.notify(err or f"Беру в папку «{name}»", type="warning" if err else "positive")

        @ui.refreshable
        def status():
            mine = [it for it in DL.items if (it.get("meta") or {}).get("key") == key]
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.button("Взять", icon="check", on_click=take) \
                    .props("unelevated dense color=amber text-color=black").classes("flex-grow")
                ui.button("В топ 3/5", icon="format_list_numbered", on_click=take_top) \
                    .props("unelevated dense outline color=amber")
                if mine:
                    last = mine[-1]
                    txt, color = {"wait": ("в очереди", "text-gray-400"),
                                  "work": ((last.get("stage") or "в работе") + "…", "text-amber-300"),
                                  "done": ("✅ в «Отобранных»", "text-green-400"),
                                  "error": ("❌ " + last.get("error", ""), "text-red-400")}[last["status"]]
                    ui.label(txt).classes(f"text-xs {color} truncate")
        status()
    return preview, status, [PK.prev.get(key, {}).get("st"), status_sig(key)]


def picked_page():
    ui.label("Отобранные").classes("text-2xl font-bold")
    ui.label("Моменты, взятые в «Выборе клипов». Файлы — в " + DL.cfg["out_dir"]).classes("text-xs text-gray-500")
    items_list(lambda it: it.get("dest") == "picked", "Пока пусто — возьми моменты в «Выборе клипов»")


def split_page():
    ui.label("Нарезка").classes("text-2xl font-bold")
    ui.label("Исходники с несколькими моментами (подборки из TikTok и т.п.) → куски для топ-3/5. Автоматика режет по "
             "сменам сцены, ставит рамку без размытого фона и вырезает рекламу — поправь и нажми «Отправить».") \
        .classes("text-xs text-gray-500")

    with ui.card().classes("w-full gap-2"):
        links = ui.textarea(placeholder="Ссылки — по одной в строку или слитно, можно с обрезкой по времени "
                                        "(ссылка -5 -3, ссылка до 58). Или путь к файлу.") \
            .props("outlined autogrow input-style='min-height:4.5em'").classes("w-full")
        errors = ui.column().classes("gap-0")
        with ui.row().classes("w-full items-center gap-3"):
            cur = SP.active()
            pool = ui.select(TP.folders(), label="Папка топ-3 / топ-5", value=cur["pool"] if cur else None,
                             with_input=True, new_value_mode="add-unique", clearable=True) \
                .props("outlined dense").classes("w-56")

            def go():
                errors.clear()
                n, errs = SP.add(links.value or "", pool.value)
                with errors:
                    for e in errs:
                        ui.label("❌ " + e).classes("text-red-400 text-sm")
                if n:
                    links.value = ""
                    ui.notify(f"В очереди: {n}", type="positive")
                elif not errs:
                    ui.notify("Ссылок не нашёл", type="warning")

            ui.button("Разобрать", icon="content_cut", on_click=go) \
                .props("unelevated no-caps color=amber text-color=black").style("white-space:nowrap")
        links.on("keydown.ctrl.enter", go)

    @ui.refreshable
    def queue():
        items = SP.s["items"]
        if not items:
            ui.label("Вставь ссылки или путь к файлу и выбери папку").classes("text-gray-500 py-4")
            return
        cur = SP.active()
        with ui.column().classes("w-full gap-1"):
            for e in items:
                icon, color = {"download": ("downloading", "text-amber-400"), "analyze": ("hourglass_top", "text-amber-400"),
                               "ready": ("edit", "text-gray-200"), "cutting": ("content_cut", "text-amber-400"),
                               "sent": ("check_circle", "text-green-400"), "error": ("error", "text-red-400")}[e["status"]]
                txt = {"download": "качаю…", "analyze": "ищу моменты, рамки и рекламу…",
                       "ready": f"{len(e.get('segs', []))} кусков" + (f", реклама вырезана: {len(e.get('ads', []))}"
                                                                      if e.get("ads") else ""),
                       "cutting": f"режу: {e.get('done', 0)} из {e.get('total', 0)}…",
                       "sent": "✅ в папке «" + e["pool"] + "»", "error": e.get("error", "")}[e["status"]]
                row = ui.row().classes("w-full items-center gap-2 no-wrap rounded px-2 py-1 cursor-pointer "
                                       + ("bg-white/10" if cur and e["id"] == cur["id"] else "hover:bg-white/5"))
                row.on("click", lambda eid=e["id"]: SP.select(eid))
                with row:
                    ui.icon(icon).classes(color)
                    ui.label(e["name"] or e["text"]).classes("text-sm truncate flex-grow font-mono")
                    ui.label(txt).classes("text-xs " + ("text-red-300" if e["status"] == "error" else "text-gray-400"))
                    if e["status"] == "sent":
                        ui.button(icon="folder_open", on_click=lambda d=e["sent"]: show_in_folder(d)) \
                            .props("flat round dense size=sm").on("click.stop", lambda: None)
                    if e["status"] != "cutting":
                        ui.button(icon="close", on_click=lambda eid=e["id"]: SP.remove(eid)) \
                            .props("flat round dense size=sm color=grey").on("click.stop", lambda: None)

    @ui.refreshable
    def editor():
        e = SP.active()
        if not e or e["status"] not in ("ready", "cutting", "sent") or not e.get("segs"):
            return
        box = ui.element("div").classes("w-full")
        data = {"src": SP.video_url(e), "w": e["w"], "h": e["h"], "duration": e["duration"], "segs": e["segs"],
                "fps": e.get("fps") or 30}
        mount = f"SplitEditor.mount('{box.html_id}', {json.dumps(data, ensure_ascii=False)})"
        ui.timer(0.3, lambda: ui.run_javascript(mount), once=True)
        with ui.row().classes("gap-3 mt-2"):
            ui.button("Отправить (Ctrl+Enter)", icon="send", on_click=lambda: ui.run_javascript("SplitEditor.submit()")) \
                .props(f"unelevated no-caps color=amber text-color=black {'disable' if e['status'] != 'ready' else ''}")
            ui.button("Как сделала автоматика", icon="restart_alt", on_click=lambda: ui.run_javascript(mount)) \
                .props("outline no-caps color=grey")

    def on_submit(ev):
        err = SP.send(ev.args)
        ui.notify(err or "Режу куски…", type="negative" if err else "positive")

    ui.on("split_submit", on_submit)
    queue()
    editor()
    sig = lambda: (lambda e: (e["id"], e["rev"], e["status"] == "ready") if e else None)(SP.active())   # noqa: E731
    seen = {"v": SP.version, "ed": sig()}

    def poll():
        if seen["v"] != SP.version:
            seen["v"] = SP.version
            queue.refresh()
            # редактор пересобираем только при смене исходника или новом разборе — иначе пропадут правки человека
            if seen["ed"] != sig():
                seen["ed"] = sig()
                editor.refresh()
        elif any(e["status"] == "download" for e in SP.s["items"]):
            queue.refresh()   # идёт загрузка — обновляем этап
    ui.timer(0.7, poll)


PREVIEW_W = 360   # ширина предпросмотра 9:16, px (холст ролика — 1080×1920)
LEFT_W = 600      # ширина левой колонки: кадр по центру, под ним таймлиния (30.10.2026, просьба Димы)


SASHA_UI = {}   # обработчики текущего окна правок (событие sasha_trim от таймлинии приходит из браузера)


def sasha_payload(e, tl_id):
    """Данные для живого предпросмотра и таймлинии (sasha_preview.js)."""
    from urllib.parse import quote
    sub, off = SA.sub_cfg(e)
    music = SA.music_of(e)
    return {"src": f"/sasha-file/{e['id']}/src", "frame": e.get("frame") or None, "zoom": e.get("zoom", 1.0),
            "ta": e.get("ta", 0.0), "tb": e.get("tb") or e.get("dur") or 1.0, "dur": e.get("dur") or 1.0,
            "base": (e.get("dl_cut") or {}).get("start", 0.0), "eid": e["id"], "tl": tl_id, "words": e["words"],
            "sub": sub, "subsOff": off, "adj": e.get("sub_adj") or {}, "moff": e.get("music_off", 0.0), "fonts": SA.fonts(), "ems": SA.font_ems(), "mvol": SA.vol_of(e),
            "filter": SA.filter_of(e), "fint": SA.fint_of(e), "music": f"/sasha-music/{quote(music)}" if music else "", "width": PREVIEW_W}


def sasha_workspace(e):
    """Одно окно, как в CapCut: слева кадр 9:16 (живой предпросмотр с субтитрами, цветом, зумом, музыкой или готовый
    ролик), справа правки — текст, приближение, границы куска, музыка. «Пересобрать» делает новый файл."""
    from urllib.parse import quote
    base = (e.get("dl_cut") or {}).get("start", 0.0)   # время начала скачанного куска в исходном видео
    dur = e.get("dur") or 1.0

    def tcs(t):
        return f"{int(t // 60)}:{t % 60:04.1f}"

    with ui.row().classes("w-full gap-6 no-wrap items-start"):
        with ui.column().classes("gap-2 shrink-0").style(f"width:{LEFT_W}px"):
            view = ui.toggle({"live": "Предпросмотр", "out": "Готовый ролик"}, value="live") \
                .props("dense no-caps unelevated color=grey-8 text-color=grey-3 toggle-color=amber "
                       "toggle-text-color=black").classes("rounded overflow-hidden")
            approve_row = ui.row().classes("gap-2 items-center")   # «Одобрить и дальше» — наверху, на уровне видео (30.10.2026)
            live = ui.element("div").style(f"width:{LEFT_W}px")
            out = ui.column().classes("gap-1").style(f"width:{PREVIEW_W}px")
            with out:
                if e["status"] == "done" and Path(e["out"]).is_file():
                    ui.video(f"/sasha-file/{e['id']}/out?v={int(Path(e['out']).stat().st_mtime)}", autoplay=False) \
                        .props("preload=metadata controls").style(f"width:{PREVIEW_W}px;height:{int(PREVIEW_W * 16 / 9)}px;"
                                                                  "background:#000;border-radius:8px")
                    ui.label("Это файл, собранный в последний раз. Правки справа видны во вкладке «Предпросмотр» сразу, "
                             "в файл попадут после «Пересобрать».").classes("text-xs text-gray-500")
                else:
                    ui.label("Готового ролика нет — нажми «Пересобрать».").classes("text-sm text-amber-300")

            def set_view(ev):
                live.set_visibility(ev.value == "live")
                out.set_visibility(ev.value == "out")
            view.on_value_change(set_view)
            live.set_visibility(view.value == "live")
            out.set_visibility(view.value == "out")
            ui.label("Предпросмотр приблизительный: цвет, шрифт и положение близки к ролику, но проверяй по готовому файлу. "
                     "🔊 на кадре — звук (музыка тише голоса).").classes("text-xs text-gray-500")

        def js(patch):
            ui.run_javascript(f"SashaPrev.update('{live.html_id}', {json.dumps(patch, ensure_ascii=False)})")

        with ui.column().classes("gap-3 flex-grow min-w-0"):
            ui.label(e["name"]).classes("text-lg font-bold")
            if e["status"] == "error":
                ui.label("❌ Не собралось: " + e["error"]).classes("text-red-300 text-sm")

            # ---- текст ----
            ui.label("Текст субтитров — исправь слова, тайминги сохранятся. Знаки препинания не нужны.") \
                .classes("text-xs text-gray-500")
            box = ui.textarea(value=e["text"]).props("outlined autogrow input-style='min-height:7em'").classes("w-full")

            def save_text(quiet=False):
                err = SA.set_text(e["id"], box.value or "")
                if err and not quiet:
                    ui.notify(err, type="negative")
                if not err:
                    js({"words": e["words"]})
                return err

            box.on("blur", lambda: save_text())
            box.on("update:model-value", lambda: save_text(True), throttle=0.8, leading_events=False)

            # ---- кадр ----
            zlabel = ui.label(f"Приближение: ×{e.get('zoom', 1.0):g}").classes("text-sm")

            def on_zoom(ev):
                e["zoom"] = round(float(ev.value), 2)
                zlabel.set_text(f"Приближение: ×{e['zoom']:g}")
                js({"zoom": e["zoom"]})

            zoom = ui.slider(min=0.5, max=2.5, step=0.05, value=e.get("zoom", 1.0), on_change=on_zoom) \
                .props("label").classes("w-full")
            zoom.on("change", lambda: SA.set_zoom(e["id"], e["zoom"]))   # сохраняем, когда отпустил ползунок
            with ui.row().classes("items-center gap-3"):
                ui.button("Сбросить (×1)", icon="center_focus_strong", on_click=lambda: zoom.set_value(1.0)) \
                    .props("outline no-caps color=grey dense")
                ui.label("×1 — кадр по лицу, как выбрал автомат. Сверху и снизу размытие делается само.") \
                    .classes("text-xs text-gray-500")
            if not e.get("frame"):
                ui.label("Лицо в куске не нашлось (или ещё ищу) — кадрирование без лица не сделано, ролик не соберётся.") \
                    .classes("text-xs text-amber-300")

            # ---- фильтр ролика: список и интенсивность ----
            flabel = ui.label(f"Интенсивность фильтра: {SA.fint_of(e)}%").classes("text-sm")
            fsel = ui.select(SA.filters(), value=SA.filter_of(e), label="Фильтр").props("outlined dense").classes("w-full")
            fslider = ui.slider(min=0, max=100, step=1, value=SA.fint_of(e)).props("label").classes("w-full")

            def on_filter(_=None):
                e["filter"], e["filter_int"] = fsel.value, int(fslider.value)
                flabel.set_text(f"Интенсивность фильтра: {int(fslider.value)}%")
                js({"filter": fsel.value, "fint": int(fslider.value)})
            fsel.on_value_change(lambda _=None: (on_filter(), SA.set_filter(e["id"], fsel.value, fslider.value)))
            fslider.on_value_change(on_filter)
            fslider.on("change", lambda: SA.set_filter(e["id"], fsel.value, fslider.value))   # сохраняем, когда отпустил ползунок

            # ---- границы куска ----
            ui.label(f"Границы куска — время в исходном видео. Тяни красные линии на таймлинии под предпросмотром; "
                     f"скачано с запасом ±{sasha.MARGIN:g} с, дальше расширить нельзя.") \
                .classes("text-xs text-gray-500")
            tinfo = ui.label().classes("text-xs text-gray-400")

            def tinfo_text():
                tinfo.set_text(f"отрезок {tcs(base + e['ta'])}–{tcs(base + e['tb'])}, длина {e['tb'] - e['ta']:.1f} с; "
                               f"доступно {tcs(base)}–{tcs(base + dur)}")

            tinfo_text()

            # ---- музыка ----
            cur = SA.music_of(e)
            opts = {"": "Без музыки", **{t: t for t in SA.tracks()}}
            if cur not in opts:
                opts[cur] = cur + " (нет файла)"
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                msel = ui.select(opts, value=cur, label="Музыка").props("outlined dense").classes("flex-grow")
                ui.button(icon="add", on_click=lambda: up.run_method("pickFiles")) \
                    .props("unelevated dense color=amber text-color=black").tooltip("Добавить свой трек в библиотеку (mp3, wav, m4a…)")

            def on_music(ev):
                err = SA.set_item_music(e["id"], ev.value or "")
                if err:
                    return ui.notify(err, type="negative")
                js({"music": f"/sasha-music/{quote(ev.value)}" if ev.value else "", "moff": 0})
            msel.on_value_change(on_music)
            mvlabel = ui.label(f"Громкость музыки: {SA.vol_of(e)}%  (100% — на 8 дБ тише голоса)").classes("text-sm")
            mvol = ui.slider(min=0, max=200, step=5, value=SA.vol_of(e)).props("label").classes("w-full")

            def on_mvol(_=None):
                e["music_vol"] = int(mvol.value)
                mvlabel.set_text(f"Громкость музыки: {int(mvol.value)}%  (100% — на 8 дБ тише голоса)")
                js({"mvol": int(mvol.value)})
            mvol.on_value_change(on_mvol)
            mvol.on("change", lambda: SA.set_music_vol(e["id"], mvol.value))   # сохраняем, когда отпустил ползунок

            async def on_upload(ev):
                tmp = core.DATA / "sasha_upload"
                tmp.mkdir(exist_ok=True)
                f = getattr(ev, "file", None)
                name = f.name if f else ev.name
                dst = tmp / Path(name).name
                if f:
                    await f.save(dst)
                else:
                    dst.write_bytes(ev.content.read())
                got, err = SA.add_track(dst)
                dst.unlink(missing_ok=True)
                if err:
                    return ui.notify(err, type="negative")
                msel.set_options({"": "Без музыки", **{t: t for t in SA.tracks()}}, value=got)
                ui.notify(f"Трек «{got}» добавлен в библиотеку", type="positive")

            ui.add_css(".sasha-up .q-uploader__list{display:none} .sasha-up{max-height:56px;min-height:0}")
            up = ui.upload(label="Добавить трек", auto_upload=True, on_upload=on_upload) \
                .props("accept=.mp3,.wav,.m4a,.aac,.ogg,.flac flat dense").classes("sasha-up").style("display:none")

            # ---- стиль субтитров (список — tools/subs/styles.json) ----
            def on_style(ev):
                err = SA.set_style(e["id"], ev.value)
                if err:
                    return ui.notify(err, type="negative")
                sub, off = SA.sub_cfg(e)
                js({"sub": sub, "subsOff": off})
            fonts_map = SA.style_fonts()
            sample = "Привет, это проверка 123"
            ssel = ui.select(SA.styles(), value=SA.style_of(e), label="Стиль субтитров (шрифт)", on_change=on_style) \
                .props("outlined dense options-dense").classes("w-full")
            fm = json.dumps(fonts_map, ensure_ascii=False)
            # в списке у каждого стиля — образец его же шрифтом
            ssel.add_slot("option", """<q-item v-bind="props.itemProps"><q-item-section>
                <q-item-label caption>{{ props.opt.label }}</q-item-label>
                <q-item-label :style='{fontFamily: ("%s", %s)[props.opt.label], fontSize: "24px", lineHeight: "1.5", color: "#fff"}'>%s</q-item-label>
                </q-item-section></q-item>""".replace('("%s", %s)', "(%s)") % (fm, sample))
            ssel.add_slot("selected-item", """<span :style='{fontFamily: (%s)[props.opt.label], fontSize: "18px"}'>{{ props.opt.label }}</span>""" % fm)
            style_view = ui.label(sample).style(f"font-family:'{fonts_map.get(SA.style_of(e))}';font-size:30px;color:#fff;"
                                                "background:#111;border-radius:6px;padding:6px 12px;width:100%")
            ssel.on_value_change(lambda ev: style_view.style(f"font-family:'{fonts_map.get(ev.value)}'"))

            # ---- кнопки ----
            def build():
                if save_text():
                    return
                err = SA.render(e["id"])
                ui.notify(err or "Собираю…", type="negative" if err else "positive")

            box.on("keydown.ctrl.enter", build)
            with ui.row().classes("gap-3"):
                ui.button("Пересобрать (Ctrl+Enter)", icon="movie_creation", on_click=build) \
                    .props("unelevated no-caps color=amber text-color=black")

                def reset_text():
                    SA.reset_text(e["id"])
                    box.value = e["text"]
                    js({"words": e["words"]})
                ui.button("Текст как расшифровал Whisper", icon="restart_alt", on_click=reset_text) \
                    .props("outline no-caps color=grey")
                if e["status"] == "done":
                    ui.button("Открыть файл", icon="play_arrow", on_click=lambda: os.startfile(e["out"])) \
                        .props("outline no-caps")

            # ---- проверка и стиль по умолчанию ----
            def approve():
                err = SA.approve(e["id"])
                if err:
                    return ui.notify(err, type="negative")
                nxt = SA.next_pending(e["id"])
                if nxt:
                    SA.select(nxt)
                    ui.notify("Одобрено, открыл следующий на проверку", type="positive")
                else:
                    ui.notify("Одобрено. Больше кусков на проверку нет ✅", type="positive")

            def save_defaults():
                SA.save_defaults(e["id"])
                ui.notify("Запомнил вид этого куска как стиль по умолчанию — новые куски будут собираться так", type="positive")

            async def apply_all():
                if not SA.s.get("defaults"):
                    return ui.notify("Сначала нажми «Запомнить как стиль по умолчанию»", type="warning")
                n = SA.count_unapproved()
                with ui.dialog() as dlg, ui.card():
                    ui.label(f"Применить стиль по умолчанию к {n} непроверенным кускам? У них заменятся положение, шрифт, цвет и размер "
                             "субтитров, стиль и музыка, ролики пересоберутся по очереди (~20 с на кусок). Одобренные не тронем. "
                             "Текст, границы и зум остаются как есть.").style("max-width:480px")
                    with ui.row():
                        ui.button("Да, применить", on_click=lambda: dlg.submit(True)).props("unelevated no-caps color=amber text-color=black")
                        ui.button("Отмена", on_click=lambda: dlg.submit(False)).props("flat no-caps")
                if await dlg:
                    ui.notify(f"Применил к {SA.apply_defaults()} кускам, пересобираю…", type="positive")

            with approve_row:
                if e.get("approved"):
                    ui.label("✓ Одобрено").classes("text-green-400")
                    ui.button("Снять одобрение", on_click=lambda: SA.approve(e["id"], False)).props("outline no-caps color=grey dense")
                elif e["status"] == "done":
                    ui.button("✓ Одобрить и дальше (У)", on_click=approve).props("unelevated no-caps color=green")
            with ui.row().classes("gap-3 items-center"):
                ui.button("★ Запомнить как стиль по умолчанию", on_click=save_defaults).props("outline no-caps")
                ui.button("Применить его ко всем непроверенным", on_click=apply_all).props("outline no-caps")
            d = SA.s.get("defaults")
            ui.label(f"Стиль по умолчанию: с куска «{d['from']}», {d['at'].replace('T', ' ')}; стиль «{d['style']}»" if d
                     else "Стиль по умолчанию ещё не запомнен: поправь субтитры на одном куске и нажми ★ — остальные соберутся так же") \
                .classes("text-xs text-gray-500")

    # таймлиния на всю ширину под плеером и правками: весь скачанный кусок, ролик между красными линиями
    tl = ui.element("div").classes("w-full")
    mount = f"SashaPrev.mount('{live.html_id}', {json.dumps(sasha_payload(e, tl.html_id), ensure_ascii=False)})"
    ui.timer(0.3, lambda: ui.run_javascript(mount), once=True)

    def on_music_off_event(args):   # дорожку музыки подвинули на таймлинии
        if args.get("id") == e["id"]:
            SA.set_music_off(e["id"], args.get("moff", 0))
    SASHA_UI["moff"] = on_music_off_event

    def on_adj_event(args):   # субтитры подвинули / изменили размер на кадре
        if args.get("id") == e["id"]:
            SA.set_sub_adj(e["id"], args.get("adj") or {})
    SASHA_UI["adj"] = on_adj_event

    def on_trim_event(args):   # красную линию отпустили на таймлинии
        if args.get("id") != e["id"]:
            return
        save_text(True)
        err = SA.set_trim(e["id"], args["ta"], args["tb"])
        if err:
            return ui.notify(err, type="negative")
        box.value = e["text"]
        tinfo_text()
        js({"words": e["words"]})
    SASHA_UI["trim"] = on_trim_event


def sasha_page():
    ui.label("Саша (❤️)").classes("text-2xl font-bold")
    ui.label("Видео → кадр 9:16, холодный цветокор, пословные белые субтитры и музыка. Приложение само расшифровывает речь и собирает ролик целиком, "
             "потом ты (или ИИ) вносишь мелкие правки — текст, приближение — и жмёшь «Пересобрать».").classes("text-xs text-gray-500")

    with ui.card().classes("w-full gap-2"):
        paths = ui.textarea(placeholder="Ссылка и время «от-до» — каждый отрезок станет отдельным куском:\nyoutube.com/… 1:20-1:45 3:00-3:30\nИли путь к видео на диске. По одной ссылке в строку.").props("outlined autogrow").classes("w-full")
        errors = ui.column().classes("gap-0")

        def go():
            errors.clear()
            n, errs = SA.add(paths.value or "")
            with errors:
                for e in errs:
                    ui.label("❌ " + e).classes("text-red-400 text-sm")
            if n:
                paths.value = ""
                ui.notify(f"В очереди: {n}", type="positive")

        with ui.row().classes("items-center gap-3 no-wrap w-full"):
            ui.button("Вырезать и расшифровать", icon="subtitles", on_click=go) \
                .props("unelevated no-caps color=amber text-color=black")
            dm = ui.select({"": "Без музыки", **{t: t for t in SA.tracks()}}, value=SA.default_music(),
                           label="Музыка по умолчанию для новых кусков").props("outlined dense").classes("flex-grow")
            dm.on_value_change(lambda ev: SA.set_music(path=str(sasha.MUSIC_DIR / ev.value) if ev.value else None,
                                                        use=bool(ev.value)))

    @ui.refreshable
    def queue():
        cur = SA.active()
        if not SA.s["items"]:
            ui.label("Вставь ссылку и время от-до").classes("text-gray-500 py-4")
            return
        ok = sum(1 for x in SA.s["items"] if x.get("approved"))
        ui.label(f"Одобрено {ok} из {len(SA.s['items'])} · одобренные копии лежат в «{sasha.OUT_NAME}\\одобрено»") \
            .classes("text-xs text-gray-500")
        for e in SA.s["items"]:
            icon, color = {"download": ("downloading", "text-amber-400"), "transcribe": ("hourglass_top", "text-amber-400"), "edit": ("edit", "text-gray-200"),
                           "render": ("movie_creation", "text-amber-400"), "done": ("check_circle", "text-green-400"),
                           "error": ("error", "text-red-400")}[e["status"]]
            txt = {"download": "качаю и режу…", "transcribe": "расшифровываю…", "edit": "поправь текст", "render": "собираю ролик…", "done": "готово — можно править",
                   "error": e["error"]}[e["status"]]
            if e.get("approved"):
                icon, color, txt = "verified", "text-green-400", "одобрено ✓"
            elif e["status"] == "done":
                txt = "готово — проверь" + (" · есть правки без пересборки" if SA.is_dirty(e) else "")
            row = ui.row().classes("w-full items-center gap-2 no-wrap rounded px-2 py-1 cursor-pointer "
                                   + ("bg-white/10" if cur and e["id"] == cur["id"] else "hover:bg-white/5"))
            row.on("click", lambda eid=e["id"]: SA.select(eid))
            with row:
                ui.icon(icon).classes(color)
                ui.label(e["name"]).classes("text-sm truncate flex-grow font-mono")
                ui.label(txt).classes("text-xs " + ("text-red-300" if e["status"] == "error" else "text-gray-400"))
                if e["status"] == "done":
                    ui.button(icon="folder_open", on_click=lambda f=e["out"]: show_in_folder(f)) \
                        .props("flat round dense size=sm").on("click.stop", lambda: None)
                if e["status"] == "error":
                    ui.button(icon="refresh", on_click=lambda eid=e["id"]: SA.retry(eid)) \
                        .props("flat round dense size=sm").on("click.stop", lambda: None)
                if e["status"] not in sasha.BUSY:
                    ui.button(icon="close", on_click=lambda eid=e["id"]: SA.remove(eid)) \
                        .props("flat round dense size=sm color=grey").on("click.stop", lambda: None)

    @ui.refreshable
    def editor():
        e = SA.active()
        # ролик сначала собирается целиком сам; правки (текст, кадр, границы, музыка) — из готового, потом «Пересобрать»
        if not e or not e["words"] or e["status"] not in ("done", "error", "edit") or not e.get("dur"):
            return
        sasha_workspace(e)

    ui.on("sasha_trim", lambda ev: SASHA_UI.get("trim") and SASHA_UI["trim"](ev.args))
    ui.on("sasha_adj", lambda ev: SASHA_UI.get("adj") and SASHA_UI["adj"](ev.args))
    ui.on("sasha_music_off", lambda ev: SASHA_UI.get("moff") and SASHA_UI["moff"](ev.args))
    queue()
    editor()

    def sig():
        e = SA.active()
        return (e["id"], bool(e["words"]), e["status"], e.get("frame") is not None, bool(e.get("dur")), bool(e.get("approved"))) if e else None   # статус сменился (собрано) — плеер готового

    seen = {"v": SA.version, "ed": sig()}

    def poll():
        if seen["v"] != SA.version:
            seen["v"] = SA.version
            queue.refresh()
            if seen["ed"] != sig():   # редактор пересобираем только при смене ролика — иначе пропадёт набранное
                seen["ed"] = sig()
                editor.refresh()
    ui.timer(0.7, poll)


def tops_page():
    ui.label("Топ 3/5").classes("text-2xl font-bold")
    ui.label("Топ-3 / топ-5 из чистых клипов одного стримера: приложение само считает, сколько роликов выйдет, "
             "чтобы два ролика не повторяли больше 1 (топ-3) / 2 (топ-5) общих моментов.").classes("text-xs text-gray-500")
    st = TP.s

    with ui.card().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            pool_in = ui.input("Подборка (стример)").props("outlined dense").classes("w-48")
            folder = ui.input("Папка с готовыми клипами (.mp4)", placeholder=r"C:\clips\clean") \
                .props("outlined dense clearable").classes("flex-grow")

            def add():
                err = TP.add_folder(pool_in.value or "", folder.value or "")
                ui.notify(err or "Клипы добавлены", type="negative" if err else "positive")
                if not err:
                    folder.value = ""

            ui.button("Добавить папку", icon="create_new_folder", on_click=add) \
                .props("unelevated color=amber text-color=black").classes("h-10")
        ui.label("Клипы остаются на своих местах — подборка хранит только ссылки. Из «Загрузок» клип можно скачать сразу в папку "
                 "подборки или добавить кнопкой 📚 на карточке.").classes("text-xs text-gray-500")

    @ui.refreshable
    def body():
        pools = list(st["pools"])
        if not pools:
            ui.label("Пока пусто — добавь папку с клипами выше").classes("text-gray-500 py-6")
            return
        if st["sel"] not in st["pools"]:
            st["sel"] = pools[0]
        clips = TP.pool()
        busy = TP.build
        with ui.card().classes("w-full gap-3"):
            with ui.row().classes("w-full items-center gap-3"):
                def pick_pool(e):
                    st["sel"] = e.value
                    TP.changed()
                ui.select(pools, value=st["sel"], label="Подборка", on_change=pick_pool) \
                    .props("outlined dense").classes("w-56")

                def set_k(e):
                    st["k"] = e.value
                    TP.changed()
                ui.toggle({3: "Топ-3", 5: "Топ-5"}, value=st["k"], on_change=set_k)
                tail = ui.input("Хвост заголовка (стример в нужном падеже)", value=st["tails"].get(st["sel"], ""),
                                placeholder="ЕВЕЛИНОВ") \
                    .props("outlined dense clearable").classes("w-64")
                tail.on("blur", lambda: st["tails"].__setitem__(st["sel"], tail.value or "") or TP.changed())
                title = ui.input("Заголовок целиком (если не хочешь авто)", value=st["title"],
                                 placeholder=TP.default_title(st["k"])) \
                    .props("outlined dense clearable").classes("flex-grow")
                title.on("blur", lambda: st.update(title=title.value or "") or TP.changed())
            k, n = st["k"], len(clips)
            with ui.row().classes("items-center gap-3"):
                def set_level(e):
                    if e.value in LEVELS:
                        TP.set_shared(st["k"], LEVELS[e.value][st["k"]])
                ui.toggle({n_: LEVEL_NAMES[n_] for n_ in LEVELS}, value=TP.level(k), on_change=set_level)                     .props("dense no-caps unelevated color=grey-8 text-color=grey-3 toggle-color=amber "
                           "toggle-text-color=black").classes("rounded overflow-hidden")

                def set_num(e):
                    if e.value is not None:
                        TP.set_shared(st["k"], int(e.value))
                ui.number(f"Общих моментов у двух роликов (0–{k - 1})", value=TP.shared(k), min=0, max=k - 1,
                          precision=0, on_change=set_num).props("outlined dense").classes("w-72")
            if n < k:
                ui.label(f"Для топ-{k} нужно минимум {k} клипов, сейчас {n}").classes("text-amber-300 text-sm")
            else:
                ui.label(f"{n} клипов → выйдет до {TP.forecast()} роликов (точное число — при сборке). "
                         f"Два ролика делят не больше {TP.shared(k)} общ. моментов.").classes("text-sm text-gray-300")
            with ui.row().classes("items-center gap-3"):
                count = ui.number("Сколько собрать (0 — все)", value=0, min=0, precision=0) \
                    .props("outlined dense").classes("w-56")

                def go():
                    err = TP.start(int(count.value or 0))
                    if err:
                        ui.notify(err, type="negative")
                ui.button("Собрать", icon="auto_awesome", on_click=go) \
                    .props(f"unelevated color=amber text-color=black {'disable' if busy or n < k else ''}") \
                    .classes("h-10")
                if busy:
                    ui.spinner(size="sm", color="amber")
                    ui.label(busy["stage"] + (f" · готово {busy['done']}" if busy["total"] else "")) \
                        .classes("text-sm text-amber-300")
        for i, c in enumerate(clips):
            with ui.card().classes("w-full p-2"):
                with ui.row().classes("w-full items-center gap-3 no-wrap"):
                    ui.label(str(i + 1)).classes("text-gray-500 w-6 text-right")
                    name = ui.input(value=c["name"]).props("outlined dense").classes("w-72")
                    name.on("blur", lambda e, i=i, nm=name: TP.rename(st["sel"], i, nm.value))
                    ui.label(c["file"]).classes("text-xs text-gray-500 font-mono truncate flex-grow")
                    ui.button(icon="play_arrow", on_click=lambda f=c["file"]: os.startfile(f)) \
                        .props("flat round dense").tooltip("Открыть клип")
                    ui.button(icon="delete_outline", on_click=lambda i=i: TP.remove(st["sel"], i)) \
                        .props("flat round dense color=grey").tooltip("Убрать из подборки (файл останется)")
        if st["runs"]:
            ui.label("Что собрано").classes("text-lg font-bold mt-3")
            for r in reversed(st["runs"]):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{r['at'][:16].replace('T', ' ')} · {r['pool']} · топ-{r['k']} · {r['count']} роликов") \
                        .classes("text-sm text-gray-300")
                    if r.get("error"):
                        ui.label("❌ " + r["error"]).classes("text-sm text-red-300")
                    elif r.get("dir"):
                        ui.button(icon="folder_open", on_click=lambda d=r["dir"]: show_in_folder(d)) \
                            .props("flat round dense size=sm").tooltip(r["dir"])

    body()
    seen = {"v": -1}

    def poll():
        if seen["v"] != TP.version:
            seen["v"] = TP.version
            body.refresh()
    ui.timer(0.5, poll)


def edits_page():
    ui.label("Очередь Edits").classes("text-2xl font-bold")
    if not ED.enabled:
        ui.label("Айфон не настроен — в config.json нет edits_topic (ставит install-iphone.ps1).") \
            .classes("text-gray-400")
        return
    with ui.row().classes("items-center gap-3"):
        ui.label("Готовое после Edits:").classes("text-gray-300")

        def mode(e):
            ED.set_mode(e.value == "phone")
        # невыбранная половина — на сером фоне, чтобы было видно, что это тоже кнопка (Дима, 26.10.2026)
        ui.toggle({"phone": "только в галерее айфона", "pc": "забрать на ноутбук"},
                  value="phone" if ED.phone_only else "pc", on_change=mode) \
            .props("dense no-caps unelevated color=grey-8 text-color=grey-3 "
                   "toggle-color=amber toggle-text-color=black").classes("rounded overflow-hidden")
    def open_export():
        d = export_dir(ED.cfg)
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(d)
    ui.button("📂 Готовое после Edits", on_click=open_export).props("flat no-caps color=amber") \
        .tooltip(str(export_dir(ED.cfg)))
    ui.label("Переключить можно и посреди прогона: сработает для всего, что айфон ещё не закончил.") \
        .classes("text-xs text-gray-500")
    async def uploaded(e):
        from nicegui import run
        d = Path(ED.cfg["out_dir"]) / "для-edits"
        d.mkdir(parents=True, exist_ok=True)
        name = Path(e.file.name).name
        dst = d / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
        await e.file.save(dst)
        item_id, err = await run.io_bound(DL.add_local, dst)
        if err:
            ui.notify(f"{name}: {err}", type="negative")
        elif ED.staged_bad(item_id):
            DL.remove(item_id)
            ui.notify(f"{name}: в Edits — только вертикальные 9:16", type="negative")
        else:
            ui.notify(f"{name}: загружен — жми «Начать прогон»", type="positive")
        ED.changed()

    with ui.row().classes("items-center gap-3"):
        ui.upload(label="Загрузить с компьютера", multiple=True, auto_upload=True, max_file_size=2 * 1024 ** 3,
                  on_upload=uploaded).props("flat accept=video/* color=amber").classes("w-72")
        ui.label("Готовые вертикальные 9:16 — прогон начнётся только по кнопке «Начать прогон».") \
            .classes("text-xs text-gray-500")
    ui.label("Кнопка с телефоном — под готовым вертикальным видео в «Загрузках» и «Отобранных». Нажатия копятся и уходят "
             f"одной пачкой, когда айфон свободен ({core.EDITS_GATHER_S} с после последнего). Если айфон застрянет, "
             "вопрос появится здесь сверху (и в Telegram, тема «"
             + (ED.cfg["edits_topic"].get("ask_title") or "Вопросы прогона") + "»), отвечать можно где удобнее.") \
        .classes("text-xs text-gray-500")

    @ui.refreshable
    def body():
        with ui.card().classes("w-full"):
            ui.label("Айфон сейчас").classes("font-medium")
            if not ED.queue:
                ui.label("свободен").classes("text-green-400 text-sm")
            for e in ED.queue:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("play_arrow" if e["started"] else "schedule") \
                        .classes("text-green-400" if e["started"] else "text-gray-400")
                    ui.label(("▶ на айфоне · " if e["started"] else "ждёт · ") + e["series"]
                             + ("" if e["mine"] else " · бот")).classes("text-sm")
        staged = ED.staged()
        if staged:
            with ui.card().classes("w-full border border-amber-500"):
                ui.label(f"Загружено, ждёт старта: {len(staged)} шт").classes("font-medium")
                for it in staged:
                    with ui.row().classes("items-center gap-2"):
                        ui.label(Path(it["file"]).name).classes("text-sm font-mono text-gray-300")
                        ui.button(icon="close", on_click=lambda x=it["id"]: (DL.remove(x), ED.changed())) \
                            .props("flat round dense size=sm color=red").tooltip("Убрать (файл останется в «для-edits»)")

                def start():
                    ui.notify(f"В очередь айфона: {ED.start_staged()} шт", type="positive")
                ui.button("▶ Начать прогон", on_click=start).props("color=amber text-color=black no-caps")
        if ED.pending:
            with ui.card().classes("w-full"):
                left = max(0, round(core.EDITS_GATHER_S - (time.time() - ED.last)))
                ui.label(f"Ждут отправки: {len(ED.pending)} шт — "
                         + (f"уйдут через {left} с, если айфон свободен" if left else "уйдут, как только айфон освободится")) \
                    .classes("font-medium")
                for i in ED.pending:
                    it = find_item(i)
                    if it:
                        with ui.row().classes("items-center gap-2"):
                            ui.label(Path(it["file"]).name).classes("text-sm font-mono text-gray-300")
                            ui.button(icon="close", on_click=lambda x=i: ED.unpend(x)) \
                                .props("flat round dense size=sm color=red").tooltip("Не отправлять")
        runs = list(reversed(ED.jobs))
        if not runs and not ED.pending:
            ui.label("Пачек пока не было").classes("text-gray-500 py-4")
        for j in runs:
            run_card(j)

    seen = {"v": -1, "q": None}

    def poll():
        q = [(e["series"], e["started"]) for e in ED.queue]
        if seen["v"] != ED.version or seen["q"] != q or ED.pending:
            seen["v"], seen["q"] = ED.version, q
            body.refresh()

    with ui.column().classes("w-full gap-3"):
        body()
    ui.timer(1, poll)


def run_card(j):
    n, ok = len(j["files"]), sum(1 for f in j["files"] if f.get("got"))
    where = "в галерею айфона" if j["phone_only"] else "на ноутбук"
    with ui.card().classes("w-full p-3"):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            st = j["status"]
            if st == "run" and j.get("pos"):
                icon, color, text = "schedule", "text-gray-400", f"в очереди айфона: {j['pos']}-я из {j['total']}"
            elif st == "run" and j.get("pos") == 0:
                icon, color, text = "phone_iphone", "text-amber-400", "▶ сейчас на айфоне"
            elif st == "run":
                icon, color, text = "hourglass_empty", "text-gray-400", "запускается…"
            elif st == "done":
                icon, color, text = "check_circle", "text-green-400", "готово"
            elif st == "cancelled":
                icon, color, text = "block", "text-gray-500", "отменена"
            elif j.get("offline"):
                icon, color, text = "phonelink_off", "text-red-400", \
                    "📵 телефон не на связи — подключи кабель, разблокируй айфон и нажми 🔁"
            else:
                icon, color, text = "error", "text-red-400", "прогон остановился — причина в «Вопросах прогона»; 🔁 продолжит"
            ui.icon(icon).classes(color)
            with ui.column().classes("gap-0 flex-grow min-w-0"):
                ui.label(f"{j['at'][5:16].replace('T', ' ')} · {n} шт · {ok} из {n} готово · {where}") \
                    .classes("font-medium")
                ui.label(text).classes(f"text-sm {color}")
                ui.label(", ".join(f["file"] for f in j["files"])).classes("text-xs text-gray-500 truncate")
            with ui.row().classes("gap-1 shrink-0"):
                if st == "run" and j.get("pos"):
                    ui.button(icon="arrow_upward", on_click=lambda x=j["id"]: ED.move(x, -1)) \
                        .props("flat round dense").tooltip("Выше")
                    ui.button(icon="arrow_downward", on_click=lambda x=j["id"]: ED.move(x, 1)) \
                        .props("flat round dense").tooltip("Ниже")

                    def cancel(x=j["id"]):
                        err = ED.cancel(x)
                        if err:
                            ui.notify(err, type="warning")
                    ui.button(icon="close", on_click=cancel).props("flat round dense color=red") \
                        .tooltip("Отменить (пока не началась)")
                if st == "run" and j.get("pos") == 0:
                    def stop(x=j["id"]):
                        err = ED.stop(x)
                        ui.notify(err or "Прогон остановлен — готовые остались, остальное продолжит 🔁", type="warning")
                    ui.button(icon="stop_circle", on_click=stop).props("flat round dense color=red") \
                        .tooltip("Остановить прогон на айфоне")
                if st == "stuck":
                    ui.button(icon="refresh", on_click=lambda x=j["id"]: ED.retry(x)) \
                        .props("flat round dense color=amber").tooltip("Повторить с места остановки")
                if st != "run":
                    def drop(x=j["id"]):
                        err = ED.remove_job(x)
                        if err:
                            ui.notify(err, type="warning")
                    ui.button(icon="delete_outline", on_click=drop).props("flat round dense color=grey") \
                        .tooltip("Убрать из списка (файлы остаются)")
                runlog = Path(j["dir"]) / "run.log"
                if runlog.exists():
                    ui.button(icon="description", on_click=lambda p=runlog: os.startfile(p)) \
                        .props("flat round dense").tooltip("Лог прогона")


def question_file():
    return Path(ED.cfg["edits_topic"]["data_dir"]) / "question.json" if ED and ED.enabled else None


def answer_question(q, ans):
    """Ответ на вопрос прогона — в тот же файл replies, что и ответы из Telegram (edits_iphone.py ждёт первый)."""
    with open(ED.cfg["edits_topic"]["replies"], "a", encoding="utf-8") as f:
        f.write(f"{max(int(time.time()), q['since'])}\t{ans}\n")
    log.info("вопрос прогона: ответ из приложения «%s»", ans)


ANSWERED = {}   # since вопроса → что ответили (карточка показывает «ответ отправлен» вместо кнопок)


def question_box():
    """Открытый вопрос прогона Edits (tools/wda/question.json, 26.10.2026) — поверх любого экрана: скрин айфона
    (нажать на нём = «нажми сюда»), надписи с экрана кнопками, «Повторить», «Стоп». Telegram для этого не нужен."""
    seen = {"q": None}

    @ui.refreshable
    def box():
        q = seen["q"]
        if not q:
            return

        def answer(ans, shown):
            answer_question(q, ans)
            ANSWERED[q["since"]] = shown
            box.refresh()
        done = ANSWERED.get(q["since"])
        with ui.card().classes("w-full border-2 border-amber-400 bg-[#2a2410]"):
            with ui.row().classes("w-full gap-4 no-wrap items-start"):
                pic = q.get("pic") and Path(q["pic"])
                if pic and pic.exists():
                    import base64
                    from PIL import Image
                    with Image.open(pic) as im:
                        w, h = im.size
                    src = "data:image/png;base64," + base64.b64encode(pic.read_bytes()).decode()

                    def click(e, w=w, h=h):
                        if not ANSWERED.get(q["since"]):
                            answer(f"клик {e.image_x / w:.4f} {e.image_y / h:.4f}", "нажать в точку на скрине")
                    ui.interactive_image(src, on_mouse=click, events=["click"], cross="#f5c518") \
                        .style("width:260px").classes("shrink-0 cursor-crosshair rounded")
                with ui.column().classes("gap-2 flex-grow min-w-0"):
                    ui.label("❓ Айфон спрашивает").classes("text-lg font-bold text-amber-300")
                    ui.label(q["text"]).classes("text-sm whitespace-pre-line")
                    if done:
                        with ui.row().classes("items-center gap-2"):
                            ui.spinner(size="sm", color="amber")
                            ui.label(f"✓ Ответ «{done}» отправлен — жду айфон").classes("text-green-400 font-medium")
                        return
                    if q.get("options"):
                        with ui.row().classes("gap-1 flex-wrap"):
                            for i, o in enumerate(q["options"], 1):
                                ui.button(o, on_click=lambda n=i, o=o: answer(str(n), o)) \
                                    .props("outline dense no-caps color=amber").classes("max-w-xs truncate")
                    if pic:
                        ui.label("Или нажми на скрине туда, куда надо нажать на айфоне.").classes("text-xs text-gray-400")
                    with ui.row().classes("gap-2"):
                        ui.button("Повторить", icon="refresh", on_click=lambda: answer("повтори", "повторить")) \
                            .props("unelevated no-caps color=amber text-color=black") \
                            .tooltip("Поправил сам на телефоне — повторить шаг")
                        ui.button("Стоп", icon="stop", on_click=lambda: answer("стоп", "стоп")) \
                            .props("flat no-caps color=red").tooltip("Остановить прогон")

    def poll():
        f = question_file()
        q = core.load_json(f, None) if f and f.exists() else None
        if q and not question_alive(q):
            q = None
        if q != seen["q"]:
            seen["q"] = q
            box.refresh()

    box()
    ui.timer(1.5, poll)


PAGES = {"Загрузки": ("download", downloads_page),
         "Выбор клипов": ("movie_filter", pick_page),
         "Отобранные": ("star", picked_page),
         "Нарезка": ("content_cut", split_page),
         "Топ 3/5": ("format_list_numbered", tops_page),
         "Саша (❤️)": ("subtitles", sasha_page),
         "Очередь Edits": ("phone_iphone", edits_page)}


# ---------- API для ИИ (app_cli.py, 26.10.2026) ----------
# Те же действия, что у кнопок, — через эти же объекты (DL, PK, ED), поэтому окно сразу показывает, что сделал ИИ.
# Только 127.0.0.1. Описание для ИИ — app-AGENTS.md.

def item_json(it):
    keys = ("id", "url", "cut", "caption", "status", "stage", "error", "file", "w", "h", "duration", "streamer",
            "dest", "edits", "edits_file")
    d = {k: it[k] for k in keys if it.get(k) not in (None, "", {})}
    if d.get("cut"):
        d["cut"] = cut_text(d["cut"])   # как пишут в поле: «-4 -5», «до 58»
    return d


def question_alive(q):
    """Вопрос открыт прогоном, который сейчас идёт: файл вопроса переживает убитый прогон, и тогда карточка
    висела бы вечно (27.10.2026). Живой = есть начатый прогон, и вопрос задан не раньше его старта."""
    try:
        started = [x for x in core.equeue(ED.cfg).edit() if x.get("started")]
    except Exception:
        return True
    return bool(started) and q["since"] >= min(x["created"] for x in started) - 2


def current_question():
    f = question_file()
    q = core.load_json(f, None) if f and f.exists() else None
    if q and not question_alive(q):
        return None
    if q:
        q["answered"] = ANSWERED.get(q["since"])
    return q


@app.get("/api/status")
def api_status():
    return {"downloads": [item_json(it) for it in DL.items],
            "edits": {"enabled": ED.enabled, "phone_only": ED.phone_only, "pending": ED.pending, "phone": ED.queue,
                      "jobs": [{k: j.get(k) for k in ("id", "status", "pos", "total", "phone_only", "offline", "at")}
                               | {"files": [f["file"] for f in j["files"]],
                                  "done": sum(1 for f in j["files"] if f.get("got"))} for j in ED.jobs]},
            "question": current_question(),
            "pick": {"loading": PK.loading, "error": PK.error,
                     **({k: PK.s.get(k) for k in ("url", "title", "channel", "shown")}
                        | {"clips": PK.s.get("total"), "moments": len(PK.s["moments"])} if PK.s else {})}}


@app.post("/api/download")
def api_download(body: dict):
    before = DL.seq
    errors = DL.add(body.get("links", ""), (body.get("caption") or "").strip(), (body.get("pool") or "").strip())
    return {"errors": errors, "ids": list(range(before + 1, DL.seq + 1))}


@app.post("/api/item")
def api_item(body: dict):
    """action: cancel (ждёт / качается) · remove (готовая или ошибка — убрать из списка) · retry (ошибка — заново)."""
    it = find_item(body.get("id"))
    if not it:
        return {"error": "нет такой загрузки"}
    act = body.get("action")
    if act == "cancel":
        DL.cancel(it["id"])
    elif act == "remove":
        DL.remove(it["id"])
    elif act == "retry":
        retry(it)
    else:
        return {"error": "action: cancel | remove | retry"}
    return {"ok": True}


def sasha_view(e):
    return {k: e[k] for k in ("id", "name", "src", "status", "error", "text", "out", "zoom", "ta", "tb", "dur", "music_off", "music_vol", "approved") if k in e} \
        | {"words": len(e["words"]), "music": SA.music_of(e), "style": SA.style_of(e), "filter": SA.filter_of(e), "fint": SA.fint_of(e), "dirty": SA.is_dirty(e), "from": (e.get("dl_cut") or {}).get("start", 0.0)}


@app.get("/sasha-file/{eid}/{kind}")
def sasha_file(eid: int, kind: str):
    """Плеер экрана «Саша»: kind = src (кусок до обработки) | out (готовый ролик). Range отдаёт сам Starlette."""
    from fastapi.responses import FileResponse, PlainTextResponse
    e = SA.entry(eid)
    f = e and (e["src"] if kind == "src" else e["out"] if kind == "out" else "")
    if not f or not Path(f).is_file():
        return PlainTextResponse("нет файла", status_code=404)
    return FileResponse(f, media_type="video/mp4")


@app.get("/sasha-font/{name}")
def sasha_font(name: str):
    """Шрифты стилей субтитров для браузера."""
    from fastapi.responses import FileResponse, PlainTextResponse
    f = sasha.SUBS / "fonts" / name
    if "/" in name or "\\" in name or not f.is_file():
        return PlainTextResponse("нет шрифта", status_code=404)
    return FileResponse(f)


@app.get("/sasha-wave/{name}")
def sasha_wave(name: str):
    """Огибающая трека для дорожки музыки на таймлинии."""
    w = SA.wave(name)
    if not w:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("нет трека", status_code=404)
    return w


@app.get("/sasha-music/{name}")
def sasha_music(name: str):
    """Трек из библиотеки для предпросмотра (только имена из библиотеки)."""
    from fastapi.responses import FileResponse, PlainTextResponse
    if name not in SA.tracks():
        return PlainTextResponse("нет трека", status_code=404)
    return FileResponse(sasha.MUSIC_DIR / name)


@app.get("/api/sasha")
def api_sasha():
    return {"music": SA.s["music"], "use_music": SA.s["use_music"], "tracks": SA.tracks(), "styles": SA.styles(), "filters": SA.filters(), "defaults": SA.s.get("defaults"),
            "items": [sasha_view(e) for e in SA.s["items"]]}


@app.post("/api/sasha")
def api_sasha_do(body: dict):
    """action: add (paths — строки «ссылка 1:20-1:45 2:00-2:30» или пути к видео) · text (id, text — исправленный текст) · render (id) ·
    zoom (id, zoom 0.3–4, 1 = автокадр) · trim (id, ta, tb — секунды ОТ НАЧАЛА СКАЧАННОГО куска, см. from) · track (path, name) — добавить трек в библиотеку · music_off (id, off — сдвиг музыки в секундах: >0 ролик начинается с этой секунды трека, <0 музыка стартует позже) · adj (id, adj — {"ladder": [{...} на слово лесенки], "single": {...} на все слова после лесенки}; поля: dx, dy (px холста 1080×1920), s (масштаб), r (поворот, градусы), font (семейство из `fonts`), color (#RRGGBB), b, i, u (true/false), o (непрозрачность %), oc/ow (цвет и толщина обводки), gc/gi/gr (сияние: цвет, интенсивность, диапазон)) · music_vol (id, vol 0–200 %, 100 = на 8 дБ тише голоса) · filter (id, name из `filters`, intensity 0–100) · approve (id, value true|false — одобрить готовый кусок) · save_defaults (id) — запомнить вид куска для новых · apply_defaults — на все непроверенные · style (id, name — стиль субтитров из `styles`) · music (id, name — трек куска, "" без музыки | path, use — по умолчанию) · retry (id) · remove (id)."""
    act, err = body.get("action"), None
    if act == "add":
        n, errs = SA.add(body.get("paths", ""))
        return {"added": n, "errors": errs}
    if act == "text":
        err = SA.set_text(body.get("id"), body.get("text", ""))
    elif act == "render":
        err = SA.render(body.get("id"))
    elif act == "zoom":
        SA.set_zoom(body.get("id"), body.get("zoom", 1))
    elif act == "music_off":
        err = SA.set_music_off(body.get("id"), body.get("off", 0))
    elif act == "adj":
        err = SA.set_sub_adj(body.get("id"), body.get("adj") or {})
    elif act == "style":
        err = SA.set_style(body.get("id"), body.get("name", ""))
    elif act == "music_vol":
        err = SA.set_music_vol(body.get("id"), body.get("vol", 100))
    elif act == "filter":
        err = SA.set_filter(body.get("id"), body.get("name"), body.get("intensity"))
    elif act == "approve":
        err = SA.approve(body.get("id"), body.get("value", True))
    elif act == "save_defaults":
        err = SA.save_defaults(body.get("id"))
    elif act == "clear_defaults":
        SA.clear_defaults()
    elif act == "apply_defaults":
        return {"ok": True, "applied": SA.apply_defaults()}
    elif act == "reset_text":
        SA.reset_text(body.get("id"))
    elif act == "trim":
        err = SA.set_trim(body.get("id"), body.get("ta", 0), body.get("tb", 0))
    elif act == "track":
        name, err = SA.add_track(body.get("path", ""), body.get("name"))
        if not err:
            return {"ok": True, "track": name}
    elif act == "music" and body.get("id") is not None:
        err = SA.set_item_music(body["id"], body.get("name", ""))
    elif act == "music":
        SA.set_music(body.get("path"), body.get("use"))
    elif act == "retry":
        SA.retry(body.get("id"))
    elif act == "remove":
        SA.remove(body.get("id"))
    else:
        err = "action: add | text | render | music | retry | remove"
    return {"error": err} if err else {"ok": True}


@app.get("/api/tops")
def api_tops():
    st = TP.s
    return {"sel": st["sel"], "k": st["k"], "title": st["title"], "shared": TP.shared(), "level": TP.level(),
            "tails": st["tails"], "folders": TP.folders(), "pools": {p: [{"i": i + 1, "name": c["name"], "file": c["file"]} for i, c in enumerate(cl)]
                      for p, cl in st["pools"].items()},
            "forecast": TP.forecast(), "build": TP.build, "runs": st["runs"]}


@app.post("/api/tops")
def api_tops_do(body: dict):
    """action: add_folder (pool, folder) · add (pool, file, name) · rename (pool, i, name) · remove (pool, i) ·
    set (pool, k, level strong|medium|weak, shared N, title) · build (count). i — номер клипа в подборке с 1."""
    act, pool = body.get("action"), body.get("pool") or TP.s["sel"]
    err = None
    if act == "new_folder":
        err = None if TP.create_folder(body.get("pool", "")) else "нужно название папки"
    elif act == "add_folder":
        err = TP.add_folder(body.get("pool", ""), body.get("folder", ""))
    elif act == "add":
        err = TP.add_clip(body.get("pool", ""), body.get("file", ""), body.get("name", ""))
    elif act == "rename":
        TP.rename(pool, int(body["i"]) - 1, body.get("name", ""))
    elif act == "remove":
        TP.remove(pool, int(body["i"]) - 1)
    elif act == "set":
        if body.get("pool") in TP.s["pools"]:
            TP.s["sel"] = body["pool"]
        if body.get("k") in (3, 5):
            TP.s["k"] = body["k"]
        if body.get("level") in LEVELS:
            TP.set_shared(TP.s["k"], LEVELS[body["level"]][TP.s["k"]])
        if body.get("shared") is not None:
            TP.set_shared(TP.s["k"], int(body["shared"]))
        if "title" in body:
            TP.s["title"] = body["title"] or ""
        if "tail" in body:
            TP.s["tails"][TP.s["sel"]] = body["tail"] or ""
        TP.changed()
    elif act == "build":
        err = TP.start(int(body.get("count") or 0))
    else:
        err = "action: add_folder | add | rename | remove | set | build"
    return {"error": err} if err else {"ok": True}


def split_view(e):
    return {k: e[k] for k in ("id", "pool", "status", "name", "src", "error", "w", "h", "duration", "segs", "ads",
                              "done", "total", "sent", "marks", "fps") if k in e}


@app.get("/api/split")
def api_split():
    """Очередь исходников + активный (его куски показывает окно) — поля активного и на верхнем уровне."""
    act = SP.active()
    return {**(split_view(act) if act else {}), "active": act["id"] if act else None,
            "items": [{k: v for k, v in split_view(e).items() if k not in ("segs",)} | {"segs_n": len(e.get("segs", []))}
                      for e in SP.s["items"]]}


@app.get("/api/split/sheet")
def api_split_sheet(id: int = 0):
    """Сводка кадров с кусками, рамками и рекламой одной картинкой → {"path": …} (для ИИ и для глаз)."""
    e = SP.entry(id) if id else SP.active()
    if not e or not e.get("segs"):
        return {"error": "нет разобранного исходника"}
    try:
        out = THUMBS / f"sheet_{e['id']}.jpg"
        THUMBS.mkdir(exist_ok=True)
        split.sheet(e["src"], e, out)
    except Exception as ex:   # noqa: BLE001
        log.exception("сводка не вышла")
        return {"error": str(ex) or ex.__class__.__name__}
    return {"path": str(out)}


@app.post("/api/split")
def api_split_do(body: dict):
    """action: open (text — ссылки/пути, хоть слитно; pool) · select (id) · set (segs активного) · send (segs, id) ·
    remove (id)."""
    act, err = body.get("action"), None
    if act == "open":
        n, errs = SP.add(body.get("text", ""), body.get("pool", ""))
        err = "; ".join(errs) if errs and not n else None
        return {"error": err} if err else {"ok": True, "added": n, "errors": errs}
    elif act == "select":
        SP.select(body.get("id"))
    elif act == "remove":
        SP.remove(body.get("id"))
    elif act == "set":
        e = SP.active()
        if not e or e["status"] != "ready":
            err = "куски можно менять, только когда разбор готов"
        else:
            e["segs"] = body.get("segs") or e["segs"]
            e["rev"] += 1
            SP.changed()
    elif act == "send":
        e = SP.entry(body.get("id")) if body.get("id") else SP.active()
        err = SP.send(body.get("segs") or (e or {}).get("segs") or [], e["id"] if e else None)
    else:
        err = "action: open | select | set | send | remove"
    return {"error": err} if err else {"ok": True}


@app.post("/api/pick")
def api_pick(body: dict):
    return {"error": PK.find(body.get("link", ""))}


@app.get("/api/moments")
def api_moments():
    if not PK.s:
        return {"error": "стрима нет — сначала app_cli pick <ссылка>", "loading": PK.loading}
    out = []
    for i, m in enumerate(PK.s["moments"][:PK.s["shown"]], 1):
        key, sh, lg = PK.key(m), m["short"], m["long"]
        mine = [it for it in DL.items if (it.get("meta") or {}).get("key") == key]
        prev = PREVIEWS / key / "preview.mp4"
        out.append({"n": i, "punch": core.tc(m["punch"]), "approx": bool(m.get("approx")), "clipped": m["n"],
                    "short_s": int(sh["e"] - sh["s"]), "long_s": int(lg["e"] - lg["s"]),
                    "short_url": PK.clip_url(sh), "long_url": PK.clip_url(lg),
                    "preview": str(prev) if prev.exists() else None,
                    "preview_status": PK.prev.get(key, {}).get("st"),
                    "taken": [item_json(it) for it in mine]})
    return {"title": PK.s["title"], "total": len(PK.s["moments"]), "shown": PK.s["shown"], "moments": out}


@app.post("/api/pick/more")
def api_pick_more():
    if not PK.s:
        return {"error": "стрима нет"}
    PK.more()
    return {"shown": PK.s["shown"]}


@app.post("/api/pick/take")
def api_pick_take(body: dict):
    n = int(body.get("n", 0))
    if not PK.s or not 1 <= n <= PK.s["shown"]:
        return {"error": f"нет момента {n} (показано {PK.s['shown'] if PK.s else 0})"}
    before = DL.seq
    err = PK.take(PK.s["moments"][n - 1], bool(body.get("long")), body.get("cut", ""), body.get("caption", ""),
                  body.get("pool", ""))
    return {"error": err, "ids": list(range(before + 1, DL.seq + 1))}


@app.post("/api/edits")
def api_edits(body: dict):
    if not ED.enabled:
        return {"error": "айфон не настроен (нет edits_topic в config.json)"}
    return {"errors": {str(i): e for i in body.get("ids", []) if (e := ED.add(int(i)))}}


@app.post("/api/edits/unpend")
def api_edits_unpend(body: dict):
    ED.unpend(int(body.get("id", 0)))
    return {"ok": True}


@app.post("/api/edits/start")
def api_edits_start():
    """Начать прогон файлов, загруженных с компьютера (кнопка «Начать прогон»)."""
    return {"started": ED.start_staged()} if ED.enabled else {"error": "айфон не настроен"}


@app.post("/api/edits/mode")
def api_edits_mode(body: dict):
    ED.set_mode(bool(body.get("phone_only")))
    return {"phone_only": ED.phone_only}


@app.post("/api/edits/job")
def api_edits_job(body: dict):
    """action: retry (застряла) · cancel (ждёт очереди) · up / down (место в очереди айфона)."""
    act, job = body.get("action"), body.get("job", "")
    if act == "retry":
        ED.retry(job)
    elif act == "cancel":
        err = ED.cancel(job)
        if err:
            return {"error": err}
    elif act == "remove":
        err = ED.remove_job(job)
        if err:
            return {"error": err}
    elif act == "stop":
        err = ED.stop(job)
        if err:
            return {"error": err}
    elif act in ("up", "down"):
        ED.move(job, -1 if act == "up" else 1)
    else:
        return {"error": "action: retry | cancel | stop | remove | up | down"}
    return {"ok": True}


@app.post("/api/answer")
def api_answer(body: dict):
    """Ответ на вопрос айфона: номер надписи (1…), «повтори», «стоп» или «клик X Y» (доли экрана 0–1)."""
    q = current_question()
    if not q:
        return {"error": "айфон сейчас ничего не спрашивает"}
    ans = str(body.get("answer", "")).strip()
    if not ans:
        return {"error": "пустой ответ"}
    answer_question(q, ans)
    opts = q.get("options") or []
    ANSWERED[q["since"]] = opts[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(opts) else ans
    return {"ok": True}


@ui.page("/")
def main_page(page: str = "Загрузки"):   # /?page=Отобранные — сразу нужный экран
    ui.dark_mode(True)
    # редактор «Нарезки»: экраны меняются без перезагрузки страницы, а скрипт, добавленный позже, браузер не выполнит
    ui.add_head_html("<style>" + SA.font_css() + "</style>")   # шрифты стилей субтитров: предпросмотр и список стилей
    ui.add_body_html("<script>" + (Path(__file__).resolve().parent / "sasha_preview.js").read_text(encoding="utf-8")
                     + "</script>")
    ui.add_body_html("<script>" + (Path(__file__).resolve().parent / "split_editor.js").read_text(encoding="utf-8")
                     + "</script>")
    ui.colors(primary=YELLOW)
    ui.query("body").style("background:#111417")
    current = {"name": page if page in PAGES else "Загрузки"}

    @ui.refreshable
    def nav():
        ask = asking()
        for title, (icon, _) in PAGES.items():
            with ui.button(title, icon=icon, on_click=lambda t=title: go(t)).props(
                    f'flat align=left no-caps {"color=amber" if title == current["name"] else "color=grey-4"}') \
                    .classes("w-full"):
                if ask and title == "Очередь Edits":   # красный кружок: айфон спрашивает (26.10.2026)
                    ui.badge("1", color="red").props("floating rounded").tooltip("Айфон ждёт ответа")
        current["ask"] = ask

    def asking():
        """Айфон ждёт ответа и его ещё не дали (ответ из Telegram тоже закроет вопрос — скрипт сотрёт файл)."""
        f = question_file()
        q = core.load_json(f, None) if f and f.exists() else None
        return bool(q and question_alive(q) and not ANSWERED.get(q["since"]))

    def nav_poll():
        if asking() != current.get("ask", False):
            nav.refresh()

    @ui.refreshable
    def content():
        PAGES[current["name"]][1]()

    def go(title):
        current["name"] = title
        nav.refresh()
        content.refresh()

    with ui.header().classes("items-center bg-[#151a1f] border-b border-white/10"):
        ui.label("Линия креативов").classes("text-lg font-bold").style(f"color:{YELLOW}")
    with ui.left_drawer(value=True).classes("bg-[#151a1f]").props("width=220 bordered"):
        nav()
    ui.timer(1.5, nav_poll)
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
        question_box()
        content()


def main():
    core.DATA.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG, encoding="utf-8")]
    if not NO_CONSOLE:  # под pythonw консоли нет
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        handlers.append(logging.StreamHandler())
    # force: NiceGUI уже повесил свои обработчики при импорте — без него basicConfig молча ничего не делает
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers,
                        force=True)
    # окно закрывает соединение — asyncio под Windows пишет ConnectionResetError с трассой; это шум, не сбой
    logging.getLogger("asyncio").addFilter(
        lambda r: not (r.exc_info and isinstance(r.exc_info[1], ConnectionResetError)))
    cfg = core.load_config()
    core.OWNER = cfg["owner_name"]
    Path(cfg["tmp_dir"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["out_dir"]).mkdir(parents=True, exist_ok=True)
    global DL, PK, ED, TP, SP, SA
    saved = core.load_json(STATE, {})
    DL = Downloads(cfg)
    ED = EditsRuns(cfg, saved.get("edits"))   # до Picker: тот сохраняет состояние, а с ED = None стёр бы очередь
    TP = Tops(saved.get("tops"))
    SP = Splitter(saved.get("split"))
    SA = sasha.Sasha(saved.get("sasha"), save_state, cfg)
    PK = Picker(cfg, saved.get("pick"))
    app.add_media_files("/out", cfg["out_dir"])   # плеер с перемоткой (Range)
    THUMBS.mkdir(exist_ok=True)
    PREVIEWS.mkdir(exist_ok=True)
    app.add_static_files("/thumbs", THUMBS)
    app.add_media_files("/previews", PREVIEWS)
    browser = "--browser" in sys.argv
    log.info("приложение запущено, %s", "в браузере" if browser else "окно")
    ui.run(title="Линия креативов", port=PORT, native=not browser, window_size=(1280, 860), reload=False,
           show=False, dark=True, favicon="🎬")


DL = PK = ED = TP = SP = SA = None
if __name__ == "__main__":  # не "__mp_main__": процесс окна заново читает этот файл — вторая очередь там не нужна
    main()
