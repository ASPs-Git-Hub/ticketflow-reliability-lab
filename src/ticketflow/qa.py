"""Rubric QA scoring of SYNTHETIC support transcripts. Rule-based only: regular expressions and
word lists, no language model. It checks surface features, not whether an answer is correct in
substance. Known weak spots are listed in the README.

Transcript item: {"id", "topic", "turns": [[speaker, text], ...], "labels": {...}} where speaker
is "agent" or "customer". Only agent turns are scored.
"""
import math
import re

WEIGHTS = {"greeting": 10, "identification": 20, "correct_info": 25, "tone": 15,
           "resolution": 20, "closing": 10}
PASS_MARK = 70
CRITERIA = list(WEIGHTS) + ["critical_fail"]

GREETING = re.compile(r"\b(hello|hi|hey|good (morning|afternoon|evening)|welcome|"
                      r"thank(s| you) for (contacting|reaching))\b", re.I)
IDENTIFY = re.compile(r"\b(i understand|understood|to confirm|it sounds like|so (?:you|the|it)|"
                      r"you(?:'re| are) (?:unable|having|seeing|locked|trying)|"
                      r"(?:could|can) you (?:tell|share|confirm))\b", re.I)
COURTESY = re.compile(r"\b(sorry|thank|thanks|please|appreciate|glad|happy to|understand|"
                      r"certainly|of course|take care|you're welcome)\b", re.I)
RUDE = re.compile(r"calm down|not my problem|as i said|obviously|you should have|your fault|"
                  r"whatever|ridiculous|idiot|stupid|moron|useless|shut up", re.I)
ABUSE = re.compile(r"\b(idiot|moron|shut up)\b|\byou(?:'re| are)?\s+(?:\w+\s+){0,2}(stupid|dumb)\b", re.I)
ACTION = re.compile(
    r"\bi(?:'ve| have)\s+(?:\w+\s+){0,2}?(sent|reset|cancel+ed|submitted|escalated|raised|opened|"
    r"issued|refunded|emailed|unlocked|enabled|regenerated|corrected|scheduled|reported|flagged|"
    r"blocked|logged|cleared|turned|created|attached|stopped)\b"
    r"|\b(?:has|have) been (?:sent|reset|cancel+ed|submitted|escalated|refunded|issued)\b"
    r"|\bi(?:'ll| will) (?:refund|send|reset|escalate|cancel|email)\b", re.I)
INSTRUCT = re.compile(r"\b(please|you can|you may|just)\s+(use|open|try|check|sync|go|add|download|"
                      r"click|press|keep|choose)\b|\b(open|choose|add)\s+(the|a|one|billing|your|settings)\b", re.I)
DEFLECT = re.compile(r"try again later|look into it|get back to you|check the docs|can't help", re.I)
CLOSING = re.compile(r"anything else|have a (great|good|nice|wonderful|lovely)|feel free|let me know if|"
                     r"take care|thank you for (being|choosing|your (time|patience))|"
                     r"thanks for (letting|your patience)|you're welcome", re.I)
_SECRET = r"(?:your|the)\s+(?:full\s+)?(?:password|passcode|pin|card number|cvv|cvc|security code)\b"
# A request is critical when it is aimed at the agent: addressed to "me/us", or ending "here/in chat".
# "enter your password on the sign-in page" is an instruction about a page, not a request to the agent.
SECRET_ASK = re.compile(
    r"\b(?:send|give|share|tell|provide|read|paste|type|enter|email|text)\s+(?:it\s+)?(?:(?:to|with)\s+)?(?:me|us)\s+" + _SECRET
    + r"|\b(?:send|give|share|provide|paste|type|enter)\s+" + _SECRET
    + r"\s+(?:here\b|in (?:this |the )?chat\b|in (?:your )?repl(?:y|ies)\b|to (?:me|us)\b|by email\b|over (?:the )?chat\b)"
    + r"|\breply with\s+" + _SECRET + r"|\bwhat(?:'s| is)\s+" + _SECRET, re.I)
CLAUSE_SPLIT = re.compile(r"[.;!?,:]|\b(?:but|and|so|because|however)\b", re.I)
NEGATION = re.compile(r"\b(not|never|n't|don't|won't|no)\b", re.I)
ALLOWED_CAPS = frozenset("""API APIS HTTP HTTPS URL URLS PDF CSV JSON XML FAQ SMS SSO SLA OTP USD EUR GDPR VPN DNS TLS SSL
CPU GPU RAM SQL CLI SDK UI UX ID IDS OK KB MB GB TB CEO CTO IT AI QA ETA TBD ASAP""".split())

# topic -> (required groups, each a regex; forbidden regex or None)
KB = {
    "password_reset": ([r"reset link", r"15[ -]minute"], r"24 hours|one hour|1 hour"),
    "invoice": ([r"billing", r"pdf"], r"\bcsv\b|reports tab"),
    "api_limit": ([r"(60|sixty) requests"], r"600 requests|unlimited"),
    "cancel": ([r"end of the billing (period|cycle)"], r"immediately|lose access"),
    "two_factor": ([r"(recovery|backup) code"], None),
    "phishing": ([r"never ask"], None),
}


def _agent_turns(item):
    return [t for s, t in item["turns"] if s == "agent"]


def check_greeting(item):
    turns = _agent_turns(item)
    return bool(turns) and bool(GREETING.search(turns[0]))


def check_identification(item):
    return any(IDENTIFY.search(t) for t in _agent_turns(item))


def check_correct_info(item):
    required, forbidden = KB.get(item.get("topic"), ([], None))
    if item.get("topic") not in KB:
        return False
    text = " ".join(_agent_turns(item))
    if forbidden and re.search(forbidden, text, re.I):
        return False
    return all(re.search(g, text, re.I) for g in required)


def _clauses(text):
    return CLAUSE_SPLIT.split(text)


def _courteous(turn):
    """A courtesy word counts unless a negation sits before it in the same clause."""
    for clause in _clauses(turn):
        m = COURTESY.search(clause)
        if m and not NEGATION.search(clause[:m.start()]):
            return True
    return False


def check_tone(item):
    turns = _agent_turns(item)
    text = " ".join(turns)
    if RUDE.search(text):
        return False
    for t in turns:
        if sum(1 for w in re.findall(r"\b[A-Z]{3,}\b", t) if w not in ALLOWED_CAPS) >= 3:
            return False
    return any(_courteous(t) for t in turns)


def check_resolution(item):
    turns = _agent_turns(item)[1:]
    text = " ".join(turns)
    return bool(ACTION.search(text)) or (bool(INSTRUCT.search(text)) and not DEFLECT.search(text))


def check_closing(item):
    turns = _agent_turns(item)
    return len(turns) > 1 and bool(CLOSING.search(turns[-1]))


def check_critical(item):
    """Critical fail: abuse, or a request for a secret aimed at the agent. Negation counts only
    inside the clause that holds the request, so 'I never received your request, please send me
    your password' is still critical while 'please do not send me your password' is not."""
    text = " ".join(_agent_turns(item))
    if ABUSE.search(text):
        return True
    for clause in _clauses(text):
        m = SECRET_ASK.search(clause)
        if m and not NEGATION.search(clause[:m.start()]):
            return True
    return False


CHECKS = {"greeting": check_greeting, "identification": check_identification,
          "correct_info": check_correct_info, "tone": check_tone,
          "resolution": check_resolution, "closing": check_closing}


def total_from(flags, critical):
    if critical:
        return 0
    return sum(w for name, w in WEIGHTS.items() if flags.get(name))


def score_transcript(item):
    flags = {name: int(fn(item)) for name, fn in CHECKS.items()}
    critical = check_critical(item)
    total = total_from(flags, critical)
    return {"criteria": flags, "critical_fail": int(critical), "total": total,
            "verdict": "pass" if (total >= PASS_MARK and not critical) else "fail"}


def human_verdict(labels):
    """DERIVED verdict: the scorer's own weights applied to the human per-criterion labels. It is
    not an independent human overall judgement."""
    flags = {k: labels[k] for k in WEIGHTS}
    total = total_from(flags, labels["critical_fail"])
    return "pass" if (total >= PASS_MARK and not labels["critical_fail"]) else "fail"


def wilson(k, n, z=1.96):
    """Wilson score interval for a proportion. None when n == 0."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def cohen_kappa(a, b):
    """Cohen's kappa for two equal-length label lists. None when chance agreement is 1."""
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("need two equal-length non-empty lists")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def calibrate(items):
    """Compare automatic scores with human labels: per-criterion agreement with Wilson 95%
    interval and Cohen's kappa, plus the pass/fail verdict. Also lists every disagreement."""
    scored = [(it, score_transcript(it)) for it in items]
    report = {"n": len(items), "criteria": {}, "disagreements": []}
    for name in CRITERIA:
        auto = [s["critical_fail"] if name == "critical_fail" else s["criteria"][name] for _, s in scored]
        human = [it["labels"][name] for it in items]
        k = sum(1 for x, y in zip(auto, human) if x == y)
        report["criteria"][name] = {"agree": k, "n": len(items), "agreement": k / len(items),
                                    "wilson95": wilson(k, len(items)), "kappa": cohen_kappa(auto, human)}
        for (it, _), x, y in zip(scored, auto, human):
            if x != y:
                report["disagreements"].append({"id": it["id"], "criterion": name, "auto": x, "human": y})
    auto_c = [s["critical_fail"] for _, s in scored]
    human_c = [it["labels"]["critical_fail"] for it in items]
    tp = sum(1 for a, h in zip(auto_c, human_c) if a and h)
    fp = sum(1 for a, h in zip(auto_c, human_c) if a and not h)
    fn = sum(1 for a, h in zip(auto_c, human_c) if h and not a)
    report["critical"] = {"tp": tp, "fp": fp, "fn": fn,
                          "precision": (tp / (tp + fp)) if tp + fp else None,
                          "recall": (tp / (tp + fn)) if tp + fn else None}
    av = [s["verdict"] for _, s in scored]
    hv = [human_verdict(it["labels"]) for it in items]
    k = sum(1 for x, y in zip(av, hv) if x == y)
    report["verdict"] = {"agree": k, "n": len(items), "agreement": k / len(items),
                         "wilson95": wilson(k, len(items)), "kappa": cohen_kappa(av, hv)}
    return report
