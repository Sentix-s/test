"""Pure text helpers for the voice pipeline (no audio dependencies)."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, ё->е, strip punctuation and extra spaces."""
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


# Common ways speech recognition spells "Jarvis" (Russian and English).
WAKE_VARIANTS: dict[str, list[str]] = {
    "jarvis": ["jarvis", "джарвис", "джарвиз", "джервис", "жарвис", "jervis", "jarvys", "javis", "джаввис"],
}


def expand_wake_words(words: list[str]) -> list[str]:
    out: list[str] = []
    for w in words:
        n = normalize(w)
        if not n:
            continue
        out.append(n)
        for key, variants in WAKE_VARIANTS.items():
            if n == key or n in variants:
                out.extend(variants)
    return list(dict.fromkeys(out))


_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
             "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
             "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
             "э": "e", "ю": "yu", "я": "ya"}


def phonetic(text: str) -> str:
    """Rough sound-alike key, the same for "Джарвис", "Жарвис", "Чарвис", "Jarvis", "Джервис"."""
    t = "".join(_TRANSLIT.get(ch, ch) for ch in normalize(text)).replace(" ", "")
    for a, b in (("dzh", "j"), ("dj", "j"), ("zh", "j"), ("ch", "j"), ("ts", "s"), ("y", "i"), ("w", "v"),
                 ("z", "s"), ("e", "a"), ("o", "a")):
        t = t.replace(a, b)
    out: list[str] = []
    for ch in t:  # collapse doubled letters
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def consonants(key: str) -> str:
    """Consonant skeleton of a phonetic key: vowels are what ASR gets wrong most ("жарвец" ~ "jarvis")."""
    return key[:1] + "".join(ch for ch in key[1:] if ch not in "aeiou")


@dataclass(frozen=True)
class WakeMatch:
    word: str          # which wake word matched
    remainder: str     # text spoken after the wake word (may be a command)
    score: float


def _skeleton_score(a: str, b: str) -> float:
    ca, cb = consonants(a), consonants(b)
    if len(ca) < 4 or len(cb) < 4 or ca[0] != cb[0]:
        return 0.0
    return difflib.SequenceMatcher(None, ca, cb).ratio()


def find_wake_word(text: str, wake_words: list[str], threshold: float = 0.8) -> WakeMatch | None:
    """Fuzzy-find a wake word among the first words of ``text``.

    Only the first three words are checked: "Джарвис, открой Word" wakes,
    "я смотрел фильм про джарвиса вчера" (mentioned in passing) does not.
    """
    tokens = normalize(text).split()
    if not tokens:
        return None
    candidates = expand_wake_words(wake_words)
    best: WakeMatch | None = None
    for i, tok in enumerate(tokens[:3]):
        for size in (1, 2):  # wake phrases of two words ("эй джарвис") or a split word ("джар вис")
            if i + size > len(tokens):
                continue
            chunk = " ".join(tokens[i:i + size])
            joined = chunk.replace(" ", "")
            ph = phonetic(chunk)
            for cand in candidates:
                score = max(difflib.SequenceMatcher(None, chunk, cand).ratio(),
                            difflib.SequenceMatcher(None, joined, cand.replace(" ", "")).ratio(),
                            difflib.SequenceMatcher(None, ph, phonetic(cand)).ratio() if len(ph) >= 4 else 0.0,
                            _skeleton_score(ph, phonetic(cand)))
                if score >= threshold and (best is None or score > best.score):
                    best = WakeMatch(cand, " ".join(tokens[i + size:]), score)
    if best is None:
        return None
    # Keep the remainder with original casing/punctuation: cut the original text after the wake word.
    words_orig = text.strip().split()
    consumed = len(normalize(text).split()) - len(best.remainder.split())
    remainder = " ".join(words_orig[consumed:]).lstrip(",.!?:;- ") if consumed < len(words_orig) else ""
    return WakeMatch(best.word, remainder, best.score)


def is_stop_phrase(text: str, stop_phrases: list[str]) -> bool:
    """True if the utterance is (essentially) only a stop phrase like "Всё, спасибо"."""
    n = normalize(text)
    if not n:
        return False
    words = n.split()
    # ASR often garbles the short "Всё, спасибо" ("всего раз спасибо", "все спасибо вам").
    if len(words) <= 3 and words[0].startswith("вс") and "спасибо" in words[1:]:
        return True
    for phrase in stop_phrases:
        p = normalize(phrase)
        if n == p or (n.startswith(p) and len(n) - len(p) <= 12):
            return True
        if difflib.SequenceMatcher(None, n, p).ratio() >= 0.85:
            return True
    return False


# Whisper hallucinates these on silence/noise; never treat them as commands.
_HALLUCINATIONS = {
    "продолжение следует", "субтитры сделал dimatorzok", "субтитры создавал dimatorzok", "спасибо за просмотр",
    "редактор субтитров а синецкая корректор а егорова", "thank you", "thanks for watching", "you",
    "субтитры подготовлены", "пока", "ммм", "угу", "",
}


def is_noise_transcript(text: str) -> bool:
    n = normalize(text)
    return n in _HALLUCINATIONS or len(n) < 2 or n.startswith("субтитры")


_SENTENCE_END = re.compile(r"([.!?…]+[\"»)]?)(\s+|$)|(\n+)")


class SentenceSplitter:
    """Accumulates streamed text and yields complete sentences, so speech can
    start before the model has finished the whole answer."""

    def __init__(self, min_chars: int = 10) -> None:
        self._buf = ""
        self.min_chars = min_chars

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        start = 0
        for m in _SENTENCE_END.finditer(self._buf):
            end = m.end()
            # Do not split "т.е." / "3.5" / short fragments: wait for enough text.
            if end - start < self.min_chars and m.group(3) is None:
                continue
            if m.group(2) == "" and m.group(3) is None:
                continue  # punctuation at the very end of the buffer: more may follow ("3." -> "3.14")
            sentence = self._buf[start:end].strip()
            if sentence:
                out.append(sentence)
            start = end
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []


_MD_RE = [
    (re.compile(r"```.*?```", re.DOTALL), " (код показан на экране) "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"https?://\S+"), " ссылка "),
    (re.compile(r"^\s*#+\s*", re.MULTILINE), ""),
    (re.compile(r"^\s*[-*•]\s+", re.MULTILINE), ""),
    (re.compile(r"[*_~|>#]+"), " "),
]


def clean_for_speech(text: str) -> str:
    for rx, repl in _MD_RE:
        text = rx.sub(repl, text)
    return _SPACE_RE.sub(" ", text).strip()


def detect_language(text: str) -> str:
    """'ru' if Cyrillic dominates, 'en' if Latin dominates."""
    cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    lat = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    return "en" if lat > cyr * 2 and lat >= 3 else "ru"
