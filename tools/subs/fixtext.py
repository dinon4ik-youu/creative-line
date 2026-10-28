"""Правка текста перед рендером: Whisper путает слова, человек (или ИИ) исправляет текст, тайминги сохраняются.

  words_to_text(words) -> строка со словами через пробел (её и правят)
  apply(words, text)   -> новые слова [{t, s, e}]: совпавшие слова держат свои тайминги, заменённые делят время
                          заменённого куска поровну, добавленные — делят время соседей, удалённые пропадают
  write_whisper_json(words, path) — тот же формат, что у faster-whisper (его читает subs.words_from_json)
"""
import difflib
import json
import re
from pathlib import Path


def norm(w):
    return re.sub(r"[^\w\-]+", "", w.lower())


def words_to_text(words):
    return " ".join(w["t"] for w in words)


def apply(words, text):
    new = [re.sub(r"[^\w\-]+", "", t) for t in text.split()]
    new = [t for t in new if t]
    old_n = [norm(w["t"]) for w in words]
    new_n = [norm(t) for t in new]
    out = []
    sm = difflib.SequenceMatcher(None, old_n, new_n, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            for k in range(b1 - b0):
                out.append({"t": new[b0 + k], "s": words[a0 + k]["s"], "e": words[a0 + k]["e"]})
        elif b1 > b0:
            if a1 > a0:                                         # замена: время старых слов делим на новые
                s, e = words[a0]["s"], words[a1 - 1]["e"]
            else:                                               # вставка: между соседями
                s = words[a0 - 1]["e"] if a0 > 0 else (words[0]["s"] if words else 0)
                e = words[a0]["s"] if a0 < len(words) else s + 0.3 * (b1 - b0)
                e = max(e, s + 0.12 * (b1 - b0))
            step = (e - s) / (b1 - b0)
            for k in range(b1 - b0):
                out.append({"t": new[b0 + k], "s": s + k * step, "e": s + (k + 1) * step})
    return out


def write_whisper_json(words, path):
    seg = {"start": words[0]["s"] if words else 0, "end": words[-1]["e"] if words else 0,
           "text": " " + words_to_text(words),
           "words": [{"word": " " + w["t"], "start": w["s"], "end": w["e"]} for w in words]}
    Path(path).write_text(json.dumps({"segments": [seg]}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    ws = [{"t": "привет", "s": 0, "e": 1}, {"t": "мир", "s": 1, "e": 2}, {"t": "сегодня", "s": 2, "e": 3}]
    r = apply(ws, "привет большой мир сегодня")
    assert [w["t"] for w in r] == ["привет", "большой", "мир", "сегодня"], r
    r = apply(ws, "привет мор")
    assert [w["t"] for w in r] == ["привет", "мор"] and r[1]["s"] == 1 and r[1]["e"] == 3, r
    print("ok")
