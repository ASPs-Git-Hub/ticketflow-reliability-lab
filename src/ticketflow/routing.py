"""Rule-based queue assignment. Deterministic; always returns a queue.

Keywords match on word boundaries (with a short inflection suffix), so 'capital' does not contain
'api' and 'rapid' does not contain 'api'. A queue listed in Router.precedence wins as soon as one
of its keywords matches, so an explicit security report is not outvoted by account keywords."""
import re

from .common import DEFAULT_QUEUES, FALLBACK_QUEUE, PRIORITIES

URGENT_WORDS = ("outage", "production down", "down for everyone", "urgent", "data loss")
HIGH_WORDS = ("cannot", "can't", "failing", "locked out", "error")
NEGATED_BEFORE = re.compile(r"(?:\bnot|\bnon-?|n't|\bno)\s*(?:really\s+|very\s+|that\s+)?$")
_CACHE = {}


def word_regex(word):
    rx = _CACHE.get(word)
    if rx is None:
        rx = _CACHE[word] = re.compile(r"(?<!\w)" + re.escape(word) + r"(?:s|es|d|ed|ing)?(?!\w)")
    return rx


def contains(text, word, honour_negation=False):
    for m in word_regex(word).finditer(text):
        if honour_negation and NEGATED_BEFORE.search(text[max(0, m.start() - 14):m.start()]):
            continue
        return True
    return False


def infer_priority(subject, body, explicit=None):
    if isinstance(explicit, str) and explicit.lower() in PRIORITIES:
        return explicit.lower()
    text = ("%s %s" % (subject or "", body or "")).lower()
    if any(contains(text, w, True) for w in URGENT_WORDS):
        return "urgent"
    if any(contains(text, w, True) for w in HIGH_WORDS):
        return "high"
    return "normal"


class Router:
    def __init__(self, queues=None, fallback=FALLBACK_QUEUE, precedence=("security",)):
        self.queues = dict(queues if queues is not None else DEFAULT_QUEUES)
        self.fallback = fallback
        self.precedence = tuple(precedence)

    def assign(self, subject, body, explicit_priority=None):
        """Return (queue, priority). Highest keyword hit count wins; ties go to the
        queue name that sorts first, so the result never depends on dict order."""
        text = ("%s %s" % (subject or "", body or "")).lower()
        best = None
        for name in sorted(self.queues):
            keywords = self.queues[name][0]
            score = sum(1 for k in keywords if contains(text, k))
            if score > 0 and (best is None or score > best[0]):
                best = (score, name)
            if score > 0 and name in self.precedence:
                best = (float("inf"), name)
                break
        queue = best[1] if best else self.fallback
        priority = infer_priority(subject, body, explicit_priority)
        if queue == "security" and priority in ("normal", "low"):
            priority = "high"
        return queue, priority

    def known_queues(self):
        return set(self.queues) | {self.fallback}
