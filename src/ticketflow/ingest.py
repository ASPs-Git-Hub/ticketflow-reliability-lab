"""Normalise inbound email-like dicts into cases. Idempotent on message-id.

Threading evidence, strongest first:
  1. In-Reply-To / References that match a message-id the system has indexed. Inbound customer
     ids and the ids of the agent's own outbound mail (registered through register_outbound) are
     both indexed. A message-id is treated as a capability: whoever holds it can thread. A signed
     reply token would be stronger and is NOT implemented.
  2. A "[TF-000001]" token in the subject, accepted ONLY when the sender is already a participant
     of that case. A subject token from an unrelated sender never attaches a message to somebody
     else's case: it opens a new, flagged case instead.
  3. A reply that arrives before its parent opens a case and remembers the missing parent id. Every
     case waiting for the same missing id is remembered (not just the first). When that parent
     arrives, all those cases and the parent are merged into one: the case with the lowest id
     survives, the others are absorbed (their ids keep resolving to the survivor). The parent's
     own headers are checked too, so a parent that answers an indexed case joins that case as well.
  Merging happens only when a message that connects the cases arrives. Two replies that share a
  missing parent which never arrives stay in separate cases: the lab does not guess. Only message
  ids that are missing can trigger a merge; two already-indexed ids in different cases never do.

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
        self._pending = {}  # missing message-id -> [ids of the cases waiting for it]
        self._merged = {}   # absorbed case id -> case id it was merged into
        self.merges = []    # {"kept", "absorbed", "by"} for every merge, in order
        self.ignored = []
        self.flagged = []
        self._counter = 0
        self._seq = 0

    def next_seq(self):
        self._seq += 1
        return self._seq

    def resolve(self, case_id):
        """The live case id for an id that may have been absorbed by a merge."""
        seen = set()
        while case_id in self._merged and case_id not in seen:
            seen.add(case_id)
            case_id = self._merged[case_id]
        return case_id

    def register_outbound(self, case_id, message_id):
        case_id = self.resolve(case_id)
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
        if m and self.resolve(m.group(1)) in self.cases:
            cid = self.resolve(m.group(1))
            if msg["from"] in self.cases[cid]["participants"]:
                return cid, "subject_token"
            return None, "unauthorized_reference:" + cid
        return None, None

    def _remember_missing_parents(self, msg, case_id):
        for mid in [msg["in_reply_to"]] + msg["references"]:
            if mid and mid not in self._index:
                waiting = self._pending.setdefault(mid, [])
                if case_id not in waiting:
                    waiting.append(case_id)

    def _merge(self, keep_id, gone_id, by_mid):
        """Absorb case gone_id into keep_id. Ids still pointing at gone_id (index, waiting lists) are not
        rewritten: every lookup passes through resolve(). Nothing is lost: messages, participants, outbound ids,
        agent status events and the earliest first response all move to the surviving case."""
        keep, gone = self.cases[keep_id], self.cases.pop(gone_id)
        self._merged[gone_id] = keep_id
        self.merges.append({"kept": keep_id, "absorbed": gone_id, "by": by_mid})
        keep.setdefault("merged_from", []).append(gone_id)
        keep["merged_from"].extend(gone.get("merged_from", []))
        keep["messages"].extend(gone["messages"])
        keep["messages"].sort(key=lambda m: m["at"])
        for p in gone["participants"]:
            if p not in keep["participants"]:
                keep["participants"].append(p)
        keep["outbound_ids"].extend(gone["outbound_ids"])
        for f in gone["flags"]:
            if f not in keep["flags"]:
                keep["flags"].append(f)
        if gone["created_at"] < keep["created_at"]:
            keep["created_at"] = gone["created_at"]
            for e in keep["event_log"]:
                if e["seq"] == 0:
                    e["at"] = gone["created_at"]
        for e in gone["event_log"]:
            if e["seq"] != 0:  # the absorbed case's own creation event is replaced by its messages below
                add_event(keep, e["at"], e["status"], e["rank"], e["seq"])
        for m in gone["messages"]:
            add_event(keep, m["at"], "open", RANK_CUSTOMER, self.next_seq())
        frs = [t for t in (keep["first_response_at"], gone["first_response_at"]) if t is not None]
        keep["first_response_at"] = min(frs) if frs else None
        reduce_case(keep)

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
            return {"action": "duplicate", "case_id": self.resolve(self._index.get(mid)), "reason": "message_id seen"}
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
        waiting = self._pending.pop(mid, [])  # replies to this message arrived before it
        found, how = self._find_case(msg)
        candidates = list(dict.fromkeys(self.resolve(c) for c in waiting + ([found] if found else [])))
        if candidates:
            case_id = min(candidates)
            absorbed = [c for c in candidates if c != case_id]
            for other in absorbed:
                self._merge(case_id, other, mid)
            case = self.cases[case_id]
            self._attach(case, msg, entry)
            if waiting and msg["at"] < case["created_at"]:
                case["created_at"] = msg["at"]
                for e in case["event_log"]:
                    if e["seq"] == 0:
                        e["at"] = msg["at"]
                reduce_case(case)
            self._index[mid] = case_id
            self._remember_missing_parents(msg, case_id)
            reason = "parent_arrived" if waiting else "reply via " + how
            result = dict({"action": "threaded", "case_id": case_id, "reason": reason}, **extra)
            if absorbed:
                result["merged"] = absorbed
            return result
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
            "merged_from": [],
            "event_log": [{"at": msg["at"], "status": "open", "rank": RANK_AGENT, "seq": 0}],
            "status_events": [(msg["at"], "open")],
            "first_response_at": None, "resolved_at": None, "empty_body": msg["raw_empty"],
        }
        self._index[mid] = case_id
        self._remember_missing_parents(msg, case_id)
        if flags:
            self.flagged.append({"message_id": mid, "case_id": case_id, "flags": list(flags)})
        return dict({"action": "created", "case_id": case_id, "reason": "new"}, **extra)
