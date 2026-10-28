"""Линия креативов из терминала — для ИИ (Claude Code, Codex, Cursor…) и для тех, кому так удобнее (26.10.2026).

Те же действия, что у кнопок приложения: команды идут в запущенное приложение (http://127.0.0.1:8765/api/…),
поэтому окно сразу показывает, что сделано. Приложение не запущено — запустит само (окно) и подождёт.
Что когда вызывать — app-AGENTS.md рядом.

  python app_cli.py status                          # всё разом: загрузки, очередь Edits, вопрос айфона, стрим
  python app_cli.py download URL [URL…] [--caption ТЕКСТ] [--pool ПОДБОРКА] [--wait]
        обрезка — в той же строке после ссылки, в кавычках: "kick.com/… -5 -3", "tiktok.com/… до 58",
        "kick.com/…/videos/… 1:20:00-1:20:10"; --caption — сразу креатив 9:16 с жёлтой плашкой
  python app_cli.py list [--all]                    # загрузки (по умолчанию последние 20)
  python app_cli.py cancel|remove|retry ID          # отменить / убрать из списка / скачать заново
  python app_cli.py wait ID [ID…]                   # ждать, пока загрузки закончатся
  python app_cli.py pick URL                        # запись стрима Kick/Twitch (или канал) → моменты
  python app_cli.py moments [--more]                # моменты текущего стрима (--more — показать ещё)
  python app_cli.py take N [--long] [--cut "-1 -5"] [--caption ТЕКСТ] [--pool ПОДБОРКА] [--wait]   # момент N → «Отобранные»
  python app_cli.py edits ID [ID…]                  # готовые вертикальные → очередь Edits на айфоне
  python app_cli.py unpend ID                       # убрать из ждущих отправки на айфон
  python app_cli.py mode phone|pc                   # готовое после Edits: только в галерее айфона | на ноутбук
  python app_cli.py job retry|cancel|stop|remove|up|down JOB    # пачка Edits: повторить / отменить ждущую / остановить идущую / выше / ниже
  python app_cli.py edits-start                     # начать прогон файлов, загруженных с компьютера
  python app_cli.py tops                            # подборки: пулы клипов, прогноз, что собрано
  python app_cli.py tops new-folder НАЗВАНИЕ        # создать пустую папку топ-3/5 (потом download --pool …)
  python app_cli.py tops add-folder СТРИМЕР ПАПКА   # все .mp4 папки → подборка стримера
  python app_cli.py tops add СТРИМЕР ФАЙЛ [--name НАЗВАНИЕ]
  python app_cli.py tops rename|remove СТРИМЕР N [НАЗВАНИЕ]   # N — номер клипа в подборке
  python app_cli.py tops set [--pool СТРИМЕР] [--k 3|5] [--level strong|medium|weak] [--shared N] [--title ТЕКСТ] [--tail ТЕКСТ]
  python app_cli.py tops build [--count N] [--wait] # собрать ролики топ-3 / топ-5 (без --count — все)
  python app_cli.py split open "URL [-5 -3]" "URL2"|ФАЙЛ … --pool ПАПКА [--wait]   # исходники в очередь (слитно тоже)
  python app_cli.py split sheet [ID]                # картинка-сводка: кадры, куски, рамки, реклама — для проверки ИИ
  python app_cli.py split select|remove ID          # какой исходник открыт в окне / убрать из очереди
  python app_cli.py split                           # очередь исходников и куски активного (время, рамка, название)
  python app_cli.py split set --segs JSON           # заменить куски (окно покажет); JSON как в `split --json`
  python app_cli.py split send [--wait]             # нарезать куски в папку топ-3/5 и в подборку
  python app_cli.py sasha add "ССЫЛКА 1:20-1:45 2:00-2:30" …   [--wait]   # экран «Саша (❤️)»: каждый отрезок «от-до» — свой кусок; сам: вырезка → Whisper → ГОТОВЫЙ ролик (или ПУТЬ к видео)
  python app_cli.py sasha                           # очередь; у выбранного — текст для правки (--json: весь)
  python app_cli.py sasha text ID "исправленный текст"   # поправить слова (тайминги сохранятся)
  python app_cli.py sasha zoom ID 1.3               # приближение кадра 9:16 (1 = автокадр по лицу, <1 отдалить, 0.3–4)
  python app_cli.py sasha render ID [--wait]        # ПЕРЕСОБРАТЬ после правок (текст / зум); первый раз ролик собирается сам
  python app_cli.py sasha trim ID 1:08.5 1:26       # границы куска В ВРЕМЕНИ ИСХОДНОГО ВИДЕО (скачано ±5 с запаса; смотри `sasha`)
  python app_cli.py sasha track ПУТЬ [--name ИМЯ]   # добавить трек в библиотеку музыки
  python app_cli.py sasha music ID ИМЯ_ТРЕКА|none   # музыка этого куска (список — в выводе `sasha`)
  python app_cli.py sasha music [ПУТЬ] [on|off]     # музыка по умолчанию для новых кусков
  python app_cli.py sasha style ID "Только по слову"  # стиль субтитров (список — в выводе `sasha`; файл tools/subs/styles.json)
  python app_cli.py sasha adj ID '{"ladder":[{"dx":-80,"dy":0,"s":1.2},{}],"single":{"dy":-150,"s":1.3}}'   # положение/размер субтитров (px холста 1080×1920, s — масштаб; ladder — по словам лесенки, single — все слова после неё сразу)
  python app_cli.py sasha moff ID 12.5             # сдвиг музыки, с: >0 — в начале играет эта секунда трека, <0 — музыка стартует позже
  python app_cli.py sasha defaults save ID          # запомнить вид куска (субтитры, стиль, музыка) как стиль по умолчанию для новых
  python app_cli.py sasha defaults clear            # забыть стиль по умолчанию
  python app_cli.py sasha defaults apply            # применить его ко всем непроверенным (пересборка по очереди)
  python app_cli.py sasha approve ID [--undo]       # одобрить готовый кусок (копия в «Саша/одобрено») / снять одобрение
  python app_cli.py sasha filter ID "Старый Голливуд" 70   # фильтр ролика и интенсивность 0–100 (список — в выводе `sasha`)
  python app_cli.py sasha mvol ID 80                # громкость музыки, %: 100 — на 8 дБ тише голоса, 0 — тишина, до 200
  python app_cli.py sasha reset ID                  # текст как расшифровал Whisper
  python app_cli.py sasha retry|remove ID
  python app_cli.py question                        # что спрашивает айфон (скрин — путь в "pic")
  python app_cli.py answer N|повтори|стоп|"клик X Y"   # ответ айфону (X Y — доли экрана 0–1)
  --json — ответ как есть, JSON.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765/api/"

if sys.stdout.encoding.lower() != "utf-8":  # консоль Windows: русский и эмодзи
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(path, body=None, start=True):
    req = urllib.request.Request(BASE + path, None if body is None else json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        if not start or isinstance(e, urllib.error.HTTPError):
            raise
    launch()
    return call(path, body, start=False)


def launch():
    """Приложение не отвечает — запустить окно (как app.bat) и ждать до 3 минут. Уже запускается (окно стартует ~20 с) —
    только ждать, второй экземпляр не плодить."""
    try:
        import psutil
        starting = any("app.py" in " ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"]))
    except Exception:  # noqa: BLE001
        starting = False
    if starting:
        print("приложение запускается — жду…", file=sys.stderr, flush=True)
    else:
        print("приложение не запущено — запускаю…", file=sys.stderr, flush=True)
        exe = Path(sys.executable)
        w = exe.with_name("pythonw.exe")
        subprocess.Popen([str(w if w.exists() else exe), "-I", str(HERE / "app.py")],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
    end = time.time() + 180
    while time.time() < end:
        time.sleep(2)
        try:
            urllib.request.urlopen(BASE + "status", timeout=3).read()
            return
        except OSError:
            pass
    sys.exit("приложение не запустилось за 3 мин — смотри app.log")


def line(it):
    st = {"wait": "⏳ ждёт", "work": "⚙️ " + (it.get("stage") or "в работе"), "done": "✅", "error": "❌"}[it["status"]]
    s = f"#{it['id']} {st} {it['url']}"
    if it.get("cut"):
        s += f" обрезка «{it['cut']}»"
    if it.get("caption"):
        s += f" · подпись «{it['caption']}»"
    if it.get("file"):
        s += f"\n    → {it['file']}"
        if it.get("w"):
            s += f" ({it['w']}×{it['h']}, {it['duration']} с" + (", вертикальное" if it["h"] > it["w"] else "") + ")"
    if it.get("error"):
        s += f"\n    ошибка: {it['error']}"
    if it.get("edits"):
        s += f"\n    Edits: {it['edits']}" + (f" → {it['edits_file']}" if it.get("edits_file") else "")
    return s


def show_question(q):
    if not q:
        print("айфон ничего не спрашивает")
        return
    print(f"❓ АЙФОН СПРАШИВАЕТ{' (уже ответили: ' + q['answered'] + ')' if q.get('answered') else ''}:\n{q['text']}")
    for i, o in enumerate(q.get("options") or [], 1):
        print(f"  {i} — {o}")
    if q.get("pic"):
        print(f"скрин: {q['pic']}")
    print('ответ: app_cli answer N | повтори | стоп | "клик X Y"')


def wait(ids):
    ids = set(ids)
    while True:
        items = {it["id"]: it for it in call("status")["downloads"]}
        left = [i for i in ids if i in items and items[i]["status"] in ("wait", "work")]
        if not left:
            for i in sorted(ids):
                print(line(items[i]) if i in items else f"#{i} — нет в списке (отменена?)")
            return
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser(description="Линия креативов из терминала", usage=__doc__)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--caption", default="")
    ap.add_argument("--cut", default="")
    ap.add_argument("--long", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--more", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--name", default="")
    ap.add_argument("--pool", default="")
    ap.add_argument("--k", type=int, default=0)
    ap.add_argument("--title", default=None)
    ap.add_argument("--tail", default=None)
    ap.add_argument("--level", default=None, choices=["strong", "medium", "weak"])
    ap.add_argument("--shared", type=int, default=None)
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--segs", default="")
    a = ap.parse_args()
    cmd, args = a.cmd, a.args

    def out(r, text=None):
        err = isinstance(r, dict) and r.get("error")
        if a.json or text is None:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("❌ " + err if err else text)
        if err:
            sys.exit(1)

    if cmd == "status":
        r = call("status")
        if a.json:
            return out(r)
        busy = [it for it in r["downloads"] if it["status"] in ("wait", "work")]
        print(f"загрузки: {len(r['downloads'])}, в работе {len(busy)}")
        for it in busy:
            print(line(it))
        e = r["edits"]
        print(f"\nEdits: готовое — {'только в галерее айфона' if e['phone_only'] else 'на ноутбук'}; "
              f"ждут отправки: {e['pending'] or 'нет'}; айфон: "
              + (", ".join(("▶ " if x["started"] else "ждёт ") + x["series"] for x in e["phone"]) or "свободен"))
        for j in e["jobs"][-5:]:
            print(f"  пачка {j['id']}: {j['status']}" + (f", в очереди {j['pos']}-я" if j.get("pos") else "")
                  + f", готово {j['done']}/{len(j['files'])}" + (", 📵 телефон не на связи" if j.get("offline") else ""))
        p = r["pick"]
        if p.get("loading"):
            print(f"\nстрим: собираю моменты {p['loading']}…")
        elif p.get("title"):
            print(f"\nстрим: {p['title']} — клипов {p['clips']}, моментов {p['moments']}, показано {p['shown']}")
        if p.get("error"):
            print(f"стрим: ошибка — {p['error']}")
        print()
        show_question(r["question"])
    elif cmd == "download":
        if not args:
            sys.exit("нужна ссылка")
        r = call("download", {"links": "\n".join(args), "caption": a.caption, "pool": a.pool})
        for e in r["errors"]:
            print("❌ " + e)
        if r["ids"]:
            print("в очереди: " + ", ".join(f"#{i}" for i in r["ids"]))
            if a.wait:
                wait(r["ids"])
        if a.json:
            out(r)
        if not r["ids"]:
            sys.exit(1)
    elif cmd == "list":
        items = call("status")["downloads"]
        if a.json:
            return out(items)
        for it in (items if a.all else items[-20:]):
            print(line(it))
    elif cmd in ("cancel", "remove", "retry"):
        out(call("item", {"id": int(args[0]), "action": cmd}), "ок")
    elif cmd == "wait":
        wait([int(x) for x in args])
    elif cmd == "pick":
        r = call("pick", {"link": " ".join(args)})
        if r.get("error"):
            return out(r, "❌ " + r["error"])
        print("собираю моменты…", flush=True)
        while True:
            time.sleep(3)
            p = call("status")["pick"]
            if not p.get("loading"):
                break
        if p.get("error"):
            sys.exit("❌ " + p["error"])
        print(f"{p['title']}: клипов {p['clips']}, моментов {p['moments']}, показано {p['shown']} — app_cli moments")
    elif cmd == "moments":
        if a.more:
            call("pick/more", {})
        r = call("moments")
        if a.json or r.get("error"):
            return out(r, None if a.json else "")
        print(f"{r['title']} — показано {r['shown']} из {r['total']}")
        for m in r["moments"]:
            print(f"{m['n']}. {'~' if m['approx'] else 'панч '}{m['punch']} · заклипали {m['clipped']} · "
                  f"клип {m['short_s']} с / длинный {m['long_s']} с · превью: {m['preview'] or m['preview_status']}")
            for it in m["taken"]:
                print("    взят: " + line(it).replace("\n", "\n    "))
    elif cmd == "take":
        r = call("pick/take", {"n": int(args[0]), "long": a.long, "cut": a.cut, "caption": a.caption, "pool": a.pool})
        if r.get("error"):
            return out(r, "❌ " + r["error"])
        print("взял: " + ", ".join(f"#{i}" for i in r["ids"]))
        if a.wait:
            wait(r["ids"])
    elif cmd == "edits":
        r = call("edits", {"ids": [int(x) for x in args]})
        if r.get("error"):
            return out(r, "❌ " + r["error"])
        for i, e in r["errors"].items():
            print(f"#{i}: ❌ {e}")
        ok = [x for x in args if x not in r["errors"]]
        if ok:
            print("в очередь Edits: " + ", ".join("#" + x for x in ok) + " — уйдут пачкой, когда айфон свободен")
    elif cmd == "unpend":
        out(call("edits/unpend", {"id": int(args[0])}), "ок")
    elif cmd == "mode":
        if not args or args[0] not in ("phone", "pc"):
            sys.exit("mode phone | pc")
        out(call("edits/mode", {"phone_only": args[0] == "phone"}), "ок")
    elif cmd == "edits-start":
        out(call("edits/start", {}), "ок")
    elif cmd == "job":
        out(call("edits/job", {"action": args[0], "job": args[1]}), "ок")
    elif cmd == "tops":
        sub = args[0] if args else "show"
        rest = args[1:]
        body = {"new-folder": lambda: {"action": "new_folder", "pool": " ".join(rest)},
                "add-folder": lambda: {"action": "add_folder", "pool": rest[0], "folder": rest[1]},
                "add": lambda: {"action": "add", "pool": rest[0], "file": rest[1], "name": a.name},
                "rename": lambda: {"action": "rename", "pool": rest[0], "i": int(rest[1]), "name": " ".join(rest[2:])},
                "remove": lambda: {"action": "remove", "pool": rest[0], "i": int(rest[1])},
                "set": lambda: {"action": "set", **({"pool": a.pool} if a.pool else {}),
                                **({"k": a.k} if a.k else {}), **({"title": a.title} if a.title is not None else {}),
                                **({"tail": a.tail} if a.tail is not None else {}),
                                **({"level": a.level} if a.level else {}),
                                **({"shared": a.shared} if a.shared is not None else {})},
                "build": lambda: {"action": "build", "count": a.count}}.get(sub)
        if body:
            try:
                r = call("tops", body())
            except IndexError:
                sys.exit("не хватает аргументов — смотри шапку app_cli.py")
            if r.get("error"):
                return out(r, "❌ " + r["error"])
            if sub == "build" and a.wait:
                print("собираю…", flush=True)
                while call("tops")["build"]:
                    time.sleep(5)
                last = call("tops")["runs"][-1]
                if last.get("error"):
                    sys.exit("❌ " + last["error"])
                print(f"готово: {last['count']} роликов → {last['dir']}")
            else:
                print("ок")
        else:
            r = call("tops")
            if a.json:
                return out(r)
            for p, cl in r["pools"].items():
                print(("▶ " if p == r["sel"] else "  ") + f"{p}: {len(cl)} клипов")
                for c_ in cl:
                    print(f"    {c_['i']}. {c_['name']}  ({c_['file']})")
            print(f"топ-{r['k']}, заголовок: «{r['title'] or 'авто'}», выйдет до {r['forecast']} роликов")
            if r["build"]:
                print(f"сборка: {r['build']['stage']}, готово {r['build']['done']}")
            for x in r["runs"][-3:]:
                print(f"  {x['at'][:16]} {x['pool']} топ-{x['k']}: {x['count']} → {x.get('dir') or x.get('error')}")
    elif cmd == "split":
        sub = args[0] if args else "show"
        if sub == "open":
            r = call("split", {"action": "open", "text": "\n".join(args[1:]), "pool": a.pool})
        elif sub == "set":
            r = call("split", {"action": "set", "segs": json.loads(a.segs)})
        elif sub == "send":
            r = call("split", {"action": "send"})
        elif sub == "sheet":
            r = call("split/sheet" + (f"?id={args[1]}" if len(args) > 1 else ""))
            if r.get("error"):
                return out(r, "❌ " + r["error"])
            return out(r) if a.json else print(r["path"])
        elif sub in ("select", "remove"):
            r = call("split", {"action": sub, "id": int(args[1])})
        else:
            r = call("split")
            if a.json:
                return out(r)
            for x in r["items"]:
                print(("▶ " if x["id"] == r["active"] else "  ") + f"#{x['id']} {x['status']} · {x.get('name', '')} → «{x['pool']}»"
                      + (f" · {x['segs_n']} кусков" if x.get("segs_n") else "") + (f" · {x['error']}" if x.get("error") else ""))
            for i, g in enumerate(r.get("segs", []), 1):
                print(f"    {i}. {g['start']:.2f}–{g['end']:.2f} с · рамка {g['crop']} · «{g.get('name', '')}»"
                      + (" · НЕ БРАТЬ" if g.get("skip") else "")
                      + (f"\n       сказано: {g['said']}" if g.get("said") else ""))
            return
        if r.get("error"):
            return out(r, "❌ " + r["error"])
        if a.wait:
            while any(x["status"] in ("download", "analyze", "cutting") for x in call("split")["items"]):
                time.sleep(3)
            for x in call("split")["items"]:
                print(f"#{x['id']} {x['status']} {x.get('error') or x.get('sent') or str(x.get('segs_n', 0)) + ' кусков'}")
        else:
            print("ок" + (f", добавлено {r['added']}" if r.get("added") is not None else ""))
    elif cmd == "sasha":
        sub = args[0] if args else "show"
        if sub == "add":
            r = call("sasha", {"action": "add", "paths": "\n".join(args[1:])})
            if r["errors"]:
                return out(r, "❌ " + "; ".join(r["errors"]))
            if a.wait:
                while any(x["status"] in ("download", "transcribe", "render") for x in call("sasha")["items"]):
                    time.sleep(5)
                for x in call("sasha")["items"][-r["added"]:]:
                    print(f"#{x['id']} {x['status']} · {x['name']} · " + (x["out"] or x["error"]))
                return
            return out(r, f"в очереди: {r['added']}")
        if sub == "text":
            r = call("sasha", {"action": "text", "id": int(args[1]), "text": " ".join(args[2:])})
        elif sub == "zoom":
            r = call("sasha", {"action": "zoom", "id": int(args[1]), "zoom": float(args[2])})
        elif sub == "render":
            r = call("sasha", {"action": "render", "id": int(args[1])})
            if not r.get("error") and a.wait:
                while next(x for x in call("sasha")["items"] if x["id"] == int(args[1]))["status"] == "render":
                    time.sleep(5)
                x = next(x for x in call("sasha")["items"] if x["id"] == int(args[1]))
                return out(x, "❌ " + x["error"] if x["status"] == "error" else "готово: " + x["out"])
        elif sub == "trim":
            def sec(t):
                x = [float(v) for v in t.replace(",", ".").split(":")]
                return sum(v * 60 ** i for i, v in enumerate(reversed(x)))
            it = next((x for x in call("sasha")["items"] if x["id"] == int(args[1])), None)
            if not it:
                return out({}, "❌ нет такого куска")
            r = call("sasha", {"action": "trim", "id": it["id"], "ta": sec(args[2]) - it["from"], "tb": sec(args[3]) - it["from"]})
        elif sub == "track":
            r = call("sasha", {"action": "track", "path": args[1], "name": a.name or None})
        elif sub == "style":
            r = call("sasha", {"action": "style", "id": int(args[1]), "name": " ".join(args[2:])})
        elif sub == "adj":
            r = call("sasha", {"action": "adj", "id": int(args[1]), "adj": json.loads(" ".join(args[2:]))})
        elif sub == "moff":
            r = call("sasha", {"action": "music_off", "id": int(args[1]), "off": float(args[2])})
        elif sub == "defaults":
            r = call("sasha", {"action": "save_defaults", "id": int(args[2])} if args[1:2] == ["save"]
                     else {"action": "clear_defaults"} if args[1:2] == ["clear"] else {"action": "apply_defaults"})
        elif sub == "approve":
            r = call("sasha", {"action": "approve", "id": int(args[1]), "value": not a.undo})
        elif sub == "filter":
            r = call("sasha", {"action": "filter", "id": int(args[1]), "name": args[2], "intensity": float(args[3]) if len(args) > 3 else None})
        elif sub == "mvol":
            r = call("sasha", {"action": "music_vol", "id": int(args[1]), "vol": float(args[2])})
        elif sub == "reset":
            r = call("sasha", {"action": "reset_text", "id": int(args[1])})
        elif sub == "music" and len(args) > 2 and args[1].isdigit():
            r = call("sasha", {"action": "music", "id": int(args[1]), "name": "" if args[2] == "none" else " ".join(args[2:])})
        elif sub == "music":
            use = {"on": True, "off": False}.get(args[-1]) if args[1:] else None
            path = next((x for x in args[1:] if x not in ("on", "off")), None)
            r = call("sasha", {"action": "music", "path": path, "use": use})
        elif sub in ("retry", "remove"):
            r = call("sasha", {"action": sub, "id": int(args[1])})
        else:
            r = call("sasha")
            if a.json:
                return out(r)
            print(f"музыка по умолчанию: {'вкл' if r['use_music'] else 'выкл'} · {r['music']}")
            print("библиотека треков:", ", ".join(r["tracks"]) or "пусто")
            print("стили субтитров:", ", ".join(r["styles"]))
            print("фильтры:", ", ".join(r["filters"]))
            for x in r["items"]:
                tcs = lambda t: f"{int(t // 60)}:{t % 60:04.1f}"  # noqa: E731
                print(f"#{x['id']} {x['status']} · {x['name']} · слов {x['words']}" + (f" · {x['error']}" if x["error"] else "")
                      + (f" · отрезок {tcs(x['from'] + x['ta'])}–{tcs(x['from'] + x['tb'])} (доступно {tcs(x['from'])}–{tcs(x['from'] + x['dur'])})"
                         if x.get("dur") else "") + f" · зум ×{x.get('zoom', 1):g} · музыка: {x.get('music') or 'нет'} · субтитры: {x.get('style')}"
                      + (" · ОДОБРЕНО" if x.get("approved") else " · есть правки без пересборки" if x.get("dirty") else "")
                      + (f" · {x['out']}" if x.get("out") else ""))
            if r["items"]:
                print("текст последнего:", r["items"][-1]["text"])
            return
        out(r, "❌ " + r["error"] if r.get("error") else "ок")
    elif cmd == "question":
        q = call("status")["question"]
        out(q) if a.json else show_question(q)
    elif cmd == "answer":
        out(call("answer", {"answer": " ".join(args)}), "ответ отправлен")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
