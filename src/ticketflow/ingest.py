"""Normalise inbound email-like dicts into cases. Idempotent on message-id.

Threading evidence, strongest first:
  1. In-Reply-To / References that match a message-id the system has indexed. Inbound customer
     ids and the ids of the agent's own outbound mail (registered through register_outbound) are
     both indexed. A message-id is treated as a capability: whoever holds it can thread. A signed
     reply token would be stronger and is NOT implemented.
  2. A "[TF-000001]" token in the subject, accepted ONLY when the sender is already a participant
     of that case. A subject token from an unrelated sender never attaches a message to somebody
     else's case: it opens a new, flagged case instead.
  3. A reply that arrives before its parent opens a case and remembers the missing parent id; when
     the parent arrives it joins that case instead of opening a second one.

Bounce and auto-reply detection needs strong evidence (see classify). A subject phrase alone is
weak evidence: the mail is kept as human mail and flagged, never silently dropped.
"""
import re
from email.utils import parseaddr

from .common import DEFAULT_TZ, MalformedEmail, get_tz, parse_dt
from .reducer import RANK_AGENT, RANK_CUSTOMER, add_event, reduce_case
from .routing import Router

CASE_REF = re.compile(r"\[(TF-\d{6})\]")
ATTRIBUTION = re.compile(r"^on .{5,200} wrote:\s*$", re.I)
HARD_QUOTE_MARKERS = (
    re.compile(r"^-{2,}\s*original message\s*-{2,}\s*$", re.I),
    re.compile(r"^_{5,}\s*$"),
)
FROM_LINE = re.compile(r"^from:\s.+@.+$", re.I)
HEADER_LINE = re.compile(r"^(sent|date|to|cc|subject):", re.I)
SIGNATURE_MARKERS = (re.compile(r"^--\s*$"), re.compile(r"^sent from my \w+", re.I))
BOUNCE_SUBJECT = re.compile(r"undeliverable|delivery status notification|mail delivery failed|returned mail|"
                            r"delivery failure|cannot fix delivery failure", re.I)
AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto:|auto-reply|out of office|autoreply)", re.I)
MAILER_LOCALPARTS = ("mailer-daemon", "postmaster")
AUTO_PRECEDENCE = ("bulk", "junk", "auto_reply")


def clean_message_id(value):
    if not isinstance(value, str):
        return None
    v = value.strip().strip("<>").strip().lower()
    return v or None


def _join_wrapped_attribution(lines):
    out, i = [], 0
    while i < len(lines):
        cur = lines[i]
        if i + 1 < len(lines) and re.match(r"^on .{5,200}$", cur.strip(), re.I) \
                and re.match(r"^wrote:\s*$", lines[i + 1].strip(), re.I):
            out.append(cur.strip() + " wrote:")
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def strip_quoted(text):
    """Keep what the sender newly wrote.

    Dropped: lines starting with '>', 'On ... wrote:' attribution lines (also when the client wraps
    them over two lines), and everything after an Outlook style header block, a separator line or a
    signature marker. Kept: text between quoted blocks, so a bottom-posted or interleaved reply is
    not reduced to nothing. A 'From:' line only counts as a quote header when header-like lines
    (Sent/Date/To/Cc/Subject) follow it."""
    lines = _join_wrapped_attribution((text or "").splitlines())
    kept = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">"):
            continue
        if ATTRIBUTION.match(s):
            continue
        if any(m.match(s) for m in HARD_QUOTE_MARKERS):
            break
        if FROM_LINE.match(s) and any(HEADER_LINE.match(n.strip()) for n in lines[i + 1:i + 5]):
            break
        if any(m.match(s) for m in SIGNATURE_MARKERS):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _auto_submitted_is_auto(value):
    """RFC 3834 section 5.1: the value is a token, optionally followed by ';' parameters and (comments).
    Only the token 'no' means 'not automatically generated'."""
    token = re.sub(r"\([^)]*\)", "", value).split(";")[0].strip().lower()
    return token not in ("", "no")


def classify(frm_addr, subject, headers, content_type=""):
    """Return (kind, warnings). kind is 'human', 'bounce' or 'auto_reply'.

    Strong evidence only:
      bounce      sender is a mailer-daemon/postmaster mailbox, or the content type is a
                  multipart/report delivery-status report (RFC 3464), or X-Failed-Recipients is set.
      auto_reply  an Auto-Submitted token other than 'no' (RFC 3834), Precedence bulk/junk/auto_reply,
                  or an auto-reply subject together with X-Auto-Response-Suppress.
    A bounce-like or auto-reply-like subject alone, or X-Auto-Response-Suppress alone (a sender
    preference, not proof of automated origin), is weak evidence: kind stays 'human' and a warning
    is returned."""
    local = frm_addr.rsplit("@", 1)[0]
    h = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    ctype = (content_type or h.get("content-type", "")).lower().replace(" ", "")
    warnings = []
    if local in MAILER_LOCALPARTS or ("multipart/report" in ctype and "delivery-status" in ctype) \
            or "x-failed-recipients" in h:
        return "bounce", warnings
    suppress = "x-auto-response-suppress" in h
    auto_subject = bool(AUTO_SUBJECT.search(subject))
    if "auto-submitted" in h and _auto_submitted_is_auto(h["auto-submitted"]):
        return "auto_reply", warnings
    if h.get("precedence", "").strip() in AUTO_PRECEDENCE or (suppress and auto_subject):
        return "auto_reply", warnings
    if BOUNCE_SUBJECT.search(subject):
        warnings.append("weak_bounce_subject")
    if auto_subject:
        warnings.append("weak_auto_reply_subject")
    if suppress:
        warnings.append("auto_response_suppress_preference")
    return "human", warnings


def _text_field(email, key):
    v = email.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise MalformedEmail("%s must be a string" % key)
    return v


def _references(email):
    refs = email.get("references")
    if refs is None:
        return []
    if isinstance(refs, str):
        refs = refs.split()
    if not isinstance(refs, (list, tuple)):
        raise MalformedEmail("references must be a string or a list of strings")
    if any(not isinstance(r, str) for r in refs):
        raise MalformedEmail("references must contain only strings")
    return [r for r in (clean_message_id(x) for x in refs) if r]


def normalise(email):
    if not isinstance(email, dict):
        raise MalformedEmail("email is not a dict")
    mid = clean_message_id(email.get("message_id"))
    if not mid:
        raise MalformedEmail("missing message_id")
    frm_raw = email.get("from")
    if not isinstance(frm_raw, str):
        raise MalformedEmail("missing or invalid from")
    addr = parseaddr(frm_raw)[1].strip().lower()
    if addr.count("@") != 1 or addr.startswith("@") or addr.endswith("@") or " " in addr:
        raise MalformedEmail("missing or invalid from")
    subject = _text_field(email, "subject")
    body = _text_field(email, "body")
    headers = email.get("headers")
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise MalformedEmail("headers must be a dict")
    irt = email.get("in_reply_to")
    if irt is not None and not isinstance(irt, str):
        raise MalformedEmail("in_reply_to must be a string")
    ctype = email.get("content_type")
    kind, warnings = classify(addr, subject, headers, ctype if isinstance(ctype, str) else "")
    return {
        "message_id": mid, "from": addr, "subject": subject.strip() or "(no subject)",
        "text": strip_quoted(body), "raw": body, "raw_empty": not body.strip(),
        "at": parse_dt(email.get("date")),
        "in_reply_to": clean_message_id(irt),
        "references": _references(email),
        "tz": email.get("tz") if isinstance(email.get("tz"), str) else DEFAULT_TZ,
        "priority": email.get("priority") if isinstance(email.get("priority"), str) else None,
        "kind": kind, "warnings": warnings,
    }


class Ingestor:
    def __init__(self, router=None, tz_by_queue=None):
        """tz_by_queue: optional {queue: tz name}. SLA calendars belong to the servicing queue, so
        when a queue is listed here its calendar wins over the 'tz' field of the email (that field
        exists only so the lab can simulate customers in several zones; real mail has none)."""
        self.router = router or Router()
        self.tz_by_queue = dict(tz_by_queue or {})
        self.cases = {}
        self._seen = set()
        self._index = {}
        self._pending = {}
        self.ignored = []
        self.flagged = []
        self._counter = 0
        self._seq = 0

    def next_seq(self):
        self._seq += 1
        return self._seq

    def register_outbound(self, case_id, message_id):
        mid = clean_message_id(message_id)
        if not mid or case_id not in self.cases:
            return None
        self._index[mid] = case_id
        self.cases[case_id]["outbound_ids"].append(mid)
        return mid

    def _find_case(self, msg):
        """Return (case_id or None, how)."""
        for mid in [msg["in_reply_to"]] + msg["references"]:
            if mid and mid in self._index:
                return self._index[mid], "message_id"
        m = CASE_REF.search(msg["subject"])
        if m and m.group(1) in self.cases:
            cid = m.group(1)
            if msg["from"] in self.cases[cid]["participants"]:
                return cid, "subject_token"
            return None, "unauthorized_reference:" + cid
        return None, None

    def _remember_missing_parents(self, msg, case_id):
        for mid in [msg["in_reply_to"]] + msg["references"]:
            if mid and mid not in self._index:
                self._pending.setdefault(mid, case_id)

    def _attach(self, case, msg, entry):
        case["messages"].append(entry)
        case["messages"].sort(key=lambda m: m["at"])
        if msg["from"] not in case["participants"]:
            case["participants"].append(msg["from"])
        add_event(case, msg["at"], "open", RANK_CUSTOMER, self.next_seq())

    def ingest(self, email):
        try:
            msg = normalise(email)
        except MalformedEmail as exc:
            return {"action": "rejected", "case_id": None, "reason": str(exc)}
        mid = msg["message_id"]
        if mid in self._seen:
            return {"action": "duplicate", "case_id": self._index.get(mid), "reason": "message_id seen"}
        self._seen.add(mid)
        if msg["kind"] != "human":
            self.ignored.append({"message_id": mid, "kind": msg["kind"]})
            return {"action": "ignored", "case_id": None, "reason": msg["kind"]}
        entry = {"message_id": mid, "at": msg["at"], "from": msg["from"], "text": msg["text"],
                 "raw": msg["raw"], "direction": "customer"}
        flags = list(msg["warnings"])
        if msg["text"] == "" and not msg["raw_empty"]:
            flags.append("no_new_text_after_stripping")
        extra = {"at": msg["at"], "warnings": list(msg["warnings"])}
        if mid in self._pending:  # a reply to this message arrived first
            case_id = self._pending.pop(mid)
            case = self.cases[case_id]
            self._attach(case, msg, entry)
            if msg["at"] < case["created_at"]:
                case["created_at"] = msg["at"]
                for e in case["event_log"]:
                    if e["seq"] == 0:
                        e["at"] = msg["at"]
                reduce_case(case)
            self._index[mid] = case_id
            self._remember_missing_parents(msg, case_id)
            return dict({"action": "threaded", "case_id": case_id, "reason": "parent_arrived"}, **extra)
        case_id, how = self._find_case(msg)
        if case_id is not None:
            case = self.cases[case_id]
            self._attach(case, msg, entry)
            self._index[mid] = case_id
            return dict({"action": "threaded", "case_id": case_id, "reason": "reply via " + how}, **extra)
        if how:
            flags.append(how)
        self._counter += 1
        case_id = "TF-%06d" % self._counter
        queue, priority = self.router.assign(msg["subject"], msg["text"], msg["priority"])
        tz_name = self.tz_by_queue.get(queue, msg["tz"])
        self.cases[case_id] = {
            "id": case_id, "subject": msg["subject"], "requester": msg["from"], "created_at": msg["at"],
            "tz": get_tz(tz_name).key, "priority": priority, "queue": queue, "status": "open",
            "messages": [entry], "participants": [msg["from"]], "outbound_ids": [], "flags": flags,
            "event_log": [{"at": msg["at"], "status": "open", "rank": RANK_AGENT, "seq": 0}],
            "status_events": [(msg["at"], "open")],
            "first_response_at": None, "resolved_at": None, "empty_body": msg["raw_empty"],
        }
        self._index[mid] = case_id
        self._remember_missing_parents(msg, case_id)
        if flags:
            self.flagged.append({"message_id": mid, "case_id": case_id, "flags": list(flags)})
        return dict({"action": "created", "case_id": case_id, "reason": "new"}, **extra)
