"""Mutation check: plant one-line defects in a temp copy of the lab, run the suite, require failure.

Exit 1 if any mutant survives, or if any pattern does not match exactly once, or if the
unmutated copy does not pass (control). Prints KILLED / SURVIVED per mutant and whether the kill
was by an assertion/test error or by an import/syntax crash (a crash kill proves less)."""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = "src/ticketflow/"
MUTANTS = [
    # sla.py
    ("saturday_is_workday", S + "sla.py", "WORKDAYS = (0, 1, 2, 3, 4)", "WORKDAYS = (0, 1, 2, 3, 4, 5)"),
    ("opens_at_8", S + "sla.py", "OPEN_HOUR = 9", "OPEN_HOUR = 8"),
    ("closes_at_18", S + "sla.py", "CLOSE_HOUR = 17", "CLOSE_HOUR = 18"),
    ("window_end_ignores_now", S + "sla.py", "lo, hi = max(ws, start), min(we, end)", "lo, hi = max(ws, start), we"),
    ("minutes_scaled", S + "sla.py", "total += (hi - lo).total_seconds() / 60.0", "total += (hi - lo).total_seconds() / 30.0"),
    ("capacity_boundary", S + "sla.py", "if cap >= remaining:", "if cap > remaining:"),
    ("remaining_grows", S + "sla.py", "remaining -= cap", "remaining += cap"),
    ("met_boundary", S + "sla.py", 'return "met" if elapsed <= target else "met_late"', 'return "met" if elapsed < target else "met_late"'),
    ("breach_boundary", S + "sla.py", "if elapsed > target:", "if elapsed >= target:"),
    ("at_risk_boundary", S + "sla.py", "if elapsed >= AT_RISK_RATIO * target:", "if elapsed > AT_RISK_RATIO * target:"),
    ("last_interval_empty", S + "sla.py", "end = evs[i + 1][0] if i + 1 < len(evs) else now", "end = evs[i + 1][0] if i + 1 < len(evs) else at"),
    ("pause_not_applied", S + "sla.py", 'if status != "open":', 'if status == "resolved":'),
    ("future_response_counts", S + "sla.py", 'responded = case["first_response_at"] is not None and case["first_response_at"] <= now', 'responded = case["first_response_at"] is not None'),
    ("resolved_means_waiting", S + "sla.py", 'resolved = bool(evs) and evs[-1][1] == "resolved"', 'resolved = bool(evs) and evs[-1][1] == "waiting_on_customer"'),
    ("paused_flag_wrong", S + "sla.py", '"paused": bool(evs) and evs[-1][1] == "waiting_on_customer"', '"paused": bool(evs) and evs[-1][1] == "open"'),
    ("end_date_in_utc", S + "sla.py", "last = end.astimezone(tz).date()", "last = end.date()"),
    ("add_returns_window_end", S + "sla.py", "return lo + timedelta(minutes=remaining)", "return we"),
    ("due_at_constant_target", S + "sla.py", 'add_business_minutes(case["created_at"], fr_target, tz)', 'add_business_minutes(case["created_at"], 60, tz)'),
    # common.py
    ("at_risk_ratio", S + "common.py", "AT_RISK_RATIO = 0.8", "AT_RISK_RATIO = 0.9"),
    ("urgent_resolution_target", S + "common.py", '"urgent": (60, 240),', '"urgent": (60, 300),'),
    ("cooldown_one_hour", S + "common.py", "ESCALATION_COOLDOWN_MINUTES = 120", "ESCALATION_COOLDOWN_MINUTES = 60"),
    ("rfc2822_date_dropped", S + "common.py", "dt = parsedate_to_datetime(text)", 'raise MalformedEmail("bad date: %r" % (value,))'),
    # escalation.py
    ("cooldown_boundary", S + "escalation.py", 'if now - alert["last_notified_at"] >= self.cooldown:', 'if now - alert["last_notified_at"] > self.cooldown:'),
    ("reactivation_ignores_cooldown", S + "escalation.py", 'if now - alert["last_notified_at"] < self.cooldown:', 'if now - alert["last_notified_at"] < timedelta(0):'),
    ("reactivation_keeps_old_clock", S + "escalation.py", '            alert["last_notified_at"] = now\n            alert["notify_count"] += 1\n            return {"alert_id": alert["id"], "kind": "reactivated"', '            alert["notify_count"] += 1\n            return {"alert_id": alert["id"], "kind": "reactivated"'),
    ("alert_key_by_severity", S + "escalation.py", "key = (case_id, rule)", "key = (case_id, severity)"),
    ("clear_keeps_active", S + "escalation.py", 'alert["active"] = False', 'alert["active"] = True'),
    ("breach_severity_warning", S + "escalation.py", '"first_response_breached": ("first_response", "breached", "critical"),', '"first_response_breached": ("first_response", "breached", "warning"),'),
    # routing.py
    ("tie_goes_last", S + "routing.py", "score > best[0]", "score >= best[0]"),
    ("security_not_raised", S + "routing.py", 'priority in ("normal", "low")', 'priority in ("low",)'),
    ("urgent_words_swapped", S + "routing.py", "if any(contains(text, w, True) for w in URGENT_WORDS):", "if any(contains(text, w, True) for w in HIGH_WORDS):"),
    ("keywords_are_substrings", S + "routing.py", 'rx = _CACHE[word] = re.compile(r"(?<!\\w)" + re.escape(word) + r"(?:s|es|d|ed|ing)?(?!\\w)")', "rx = _CACHE[word] = re.compile(re.escape(word))"),
    ("keyword_no_right_boundary", S + "routing.py", '(?:s|es|d|ed|ing)?(?!\\w)")', '(?:s|es|d|ed|ing)?")'),
    ("keyword_no_left_boundary", S + "routing.py", 'r"(?<!\\w)" + re.escape(word)', "re.escape(word)"),
    ("negated_urgency_ignored", S + "routing.py", "if honour_negation and NEGATED_BEFORE.search", "if False and NEGATED_BEFORE.search"),
    ("security_precedence_off", S + "routing.py", "if score > 0 and name in self.precedence:", "if False:"),
    # ingest.py
    ("idempotency_removed", S + "ingest.py", "self._seen.add(mid)", "pass"),
    ("unknown_case_ref_trusted", S + "ingest.py", "if m and self.resolve(m.group(1)) in self.cases:", "if m:"),
    ("references_ignored", S + "ingest.py", 'for mid in [msg["in_reply_to"]] + msg["references"]:\n            if mid and mid in self._index:', 'for mid in [msg["in_reply_to"]]:\n            if mid and mid in self._index:'),
    ("threading_not_authenticated", S + "ingest.py", 'if msg["from"] in self.cases[cid]["participants"]:', "if True:"),
    ("requester_not_a_participant", S + "ingest.py", '"participants": [msg["from"]],', '"participants": [],'),
    ("repliers_not_added_as_participants", S + "ingest.py", 'if msg["from"] not in case["participants"]:', "if False:"),
    ("outbound_id_not_indexed", S + "ingest.py", '        self._index[mid] = case_id\n        self.cases[case_id]["outbound_ids"].append(mid)', '        self.cases[case_id]["outbound_ids"].append(mid)'),
    ("late_parent_not_reconciled", S + "ingest.py", "waiting = self._pending.pop(mid, [])", "waiting = []"),
    ("missing_parents_not_remembered", S + "ingest.py", "waiting.append(case_id)", "pass"),
    ("reply_event_not_recorded", S + "ingest.py", 'add_event(case, msg["at"], "open", RANK_CUSTOMER, self.next_seq())', "pass"),
    ("reply_ranked_as_agent_event", S + "ingest.py", 'add_event(case, msg["at"], "open", RANK_CUSTOMER, self.next_seq())', 'add_event(case, msg["at"], "open", RANK_AGENT, self.next_seq())'),
    ("bounce_by_subject_alone", S + "ingest.py", "if local in MAILER_LOCALPARTS or", "if local in MAILER_LOCALPARTS or BOUNCE_SUBJECT.search(subject) or"),
    ("bounce_by_sender_ignored", S + "ingest.py", "if local in MAILER_LOCALPARTS or", "if False or"),
    ("delivery_status_report_ignored", S + "ingest.py", '("multipart/report" in ctype and "delivery-status" in ctype)', "False"),
    ("suppress_header_alone_is_auto", S + "ingest.py", "(suppress and auto_subject)", "suppress"),
    ("auto_subject_alone_is_auto", S + "ingest.py", "(suppress and auto_subject)", "auto_subject"),
    ("auto_submitted_parameters_not_parsed", S + "ingest.py", 'token = re.sub(r"\\([^)]*\\)", "", value).split(";")[0].strip().lower()', "token = value.strip().lower()"),
    ("auto_submitted_empty_is_auto", S + "ingest.py", 'return token not in ("", "no")', 'return token != "no"'),
    ("auto_submitted_header_ignored", S + "ingest.py", 'if "auto-submitted" in h and _auto_submitted_is_auto(h["auto-submitted"]):', "if False:"),
    ("quote_marker_wrong", S + "ingest.py", 'if s.startswith(">"):', 'if s.startswith("<"):'),
    ("attribution_ends_the_text", S + "ingest.py", "if ATTRIBUTION.match(s):\n            continue", "if ATTRIBUTION.match(s):\n            break"),
    ("from_line_always_a_quote_header", S + "ingest.py", "if FROM_LINE.match(s) and any(HEADER_LINE.match(n.strip()) for n in lines[i + 1:i + 5]):", "if FROM_LINE.match(s):"),
    ("wrapped_attribution_not_joined", S + "ingest.py", 'lines = _join_wrapped_attribution((text or "").splitlines())', 'lines = (text or "").splitlines()'),
    ("indented_signature_kept", S + "ingest.py", "if any(m.match(s) for m in SIGNATURE_MARKERS):", "if any(m.match(line) for m in SIGNATURE_MARKERS):"),
    ("signature_delimiter_wrong", S + "ingest.py", 're.compile(r"^--\\s*$")', 're.compile(r"^---\\s*$")'),
    ("no_final_strip", S + "ingest.py", 'return "\\n".join(kept).strip()', 'return "\\n".join(kept)'),
    ("tz_not_validated", S + "ingest.py", '"tz": get_tz(tz_name).key,', '"tz": tz_name,'),
    ("queue_calendar_ignored", S + "ingest.py", "tz_name = self.tz_by_queue.get(queue, msg[\"tz\"])", 'tz_name = msg["tz"]'),
    ("address_not_parsed", S + "ingest.py", "addr = parseaddr(frm_raw)[1].strip().lower()", "addr = frm_raw.strip().lower()"),
    ("headers_type_unchecked", S + "ingest.py", "if not isinstance(headers, dict):", "if False:"),
    ("references_type_unchecked", S + "ingest.py", "if any(not isinstance(r, str) for r in refs):", "if False:"),
    ("in_reply_to_type_unchecked", S + "ingest.py", "if irt is not None and not isinstance(irt, str):", "if False:"),
    # reducer.py
    ("reducer_uses_arrival_order", S + "reducer.py", 'log = sorted(case["event_log"], key=lambda e: (e["at"], e["rank"], e["seq"]))', 'log = list(case["event_log"])'),
    ("reducer_ignores_sequence", S + "reducer.py", 'key=lambda e: (e["at"], e["rank"], e["seq"])', 'key=lambda e: (e["at"], e["rank"])'),
    ("reducer_ignores_rank", S + "reducer.py", 'key=lambda e: (e["at"], e["rank"], e["seq"])', 'key=lambda e: (e["at"], e["seq"])'),
    ("reducer_dedupe_removed", S + "reducer.py", 'if e["at"] == at and e["status"] == status and e["rank"] == rank:', "if False:"),
    ("resolved_at_never_set", S + "reducer.py", 'case["resolved_at"] = last["at"] if last["status"] == "resolved" else None', 'case["resolved_at"] = None'),
    ("resolved_at_first_event", S + "reducer.py", 'case["resolved_at"] = last["at"] if', 'case["resolved_at"] = log[0]["at"] if'),
    # flow.py
    ("latest_agent_reply_wins", S + "flow.py", 'or at < case["first_response_at"]:', 'or at > case["first_response_at"]:'),
    ("bogus_status_accepted", S + "flow.py", "if case is None or status not in STATUSES:", "if case is None:"),
    ("pre_creation_events_accepted", S + "flow.py", 'if case is None or not is_aware(at) or at < case["created_at"]:', "if case is None or not is_aware(at):"),
    ("naive_times_accepted", S + "flow.py", 'if case is None or not is_aware(at) or at < case["created_at"]:', 'if case is None or at < case["created_at"]:'),
    ("reply_event_kind_wrong", S + "flow.py", 'kind = "created" if result["action"] == "created" else "customer_reply"', 'kind = "created"'),
    ("agent_event_kind_wrong", S + "flow.py", '"kind": "agent_reply"})', '"kind": "created"})'),
    ("outbound_id_not_registered", S + "flow.py", "self.ingestor.register_outbound(case_id, message_id)", "pass"),
    ("outbound_default_id_repeats", S + "flow.py", 'len(case["outbound_ids"]) + 1)', "1)"),
    ("status_sequence_ignored", S + "flow.py", "self.ingestor.next_seq() if seq is None else seq", "self.ingestor.next_seq()"),
    # metrics.py
    ("backlog_inverted", S + "metrics.py", 'if status != "resolved":\n            backlog += 1', 'if status == "resolved":\n            backlog += 1'),
    ("future_cases_counted", S + "metrics.py", 'if case["created_at"] > now:', 'if case["created_at"] < now:'),
    ("late_met_not_breach", S + "metrics.py", 'LATE = ("met_late", "breached")', 'LATE = ("breached",)'),
    ("open_clock_counts_as_decided_case", S + "metrics.py", "if state in DECIDED:", 'if state in DECIDED or state == "ok":'),
    # qa.py
    ("pass_mark_75", S + "qa.py", "PASS_MARK = 70", "PASS_MARK = 75"),
    ("weight_changed", S + "qa.py", '"correct_info": 25', '"correct_info": 20'),
    ("kappa_denominator", S + "qa.py", "return (po - pe) / (1 - pe)", "return (po - pe) / pe"),
    ("wilson_centre", S + "qa.py", "centre = (p + z * z / (2 * n)) / denom", "centre = (p + z * z / n) / denom"),
    ("abuse_not_critical", S + "qa.py", "if ABUSE.search(text):", "if False:"),
    ("mild_abuse_words_always_critical", S + "qa.py", "(idiot|moron|shut up)", "(idiot|moron|shut up|dumb)"),
    ("negation_guard_removed", S + "qa.py", "        m = SECRET_ASK.search(clause)\n        if m and not NEGATION.search(clause[:m.start()]):", "        m = SECRET_ASK.search(clause)\n        if m:"),
    ("negation_spans_whole_sentence", S + "qa.py", 'CLAUSE_SPLIT = re.compile(r"[.;!?,:]|\\b(?:but|and|so|because|however)\\b", re.I)', 'CLAUSE_SPLIT = re.compile(r"[.;!?]", re.I)'),
    ("password_on_a_page_is_critical", S + "qa.py", 'r"\\s+(?:here\\b|in (?:this |the )?chat\\b', 'r"\\s+(?:here\\b|on (?:the )?[\\w -]+ (?:page|screen)\\b|in (?:this |the )?chat\\b'),
    ("courtesy_negation_ignored", S + "qa.py", "        m = COURTESY.search(clause)\n        if m and not NEGATION.search(clause[:m.start()]):", "        m = COURTESY.search(clause)\n        if m:"),
    ("acronyms_count_as_shouting", S + "qa.py", "if w not in ALLOWED_CAPS", "if True"),
    ("closing_on_single_turn", S + "qa.py", "return len(turns) > 1 and bool(CLOSING.search(turns[-1]))", "return len(turns) > 0 and bool(CLOSING.search(turns[-1]))"),
    ("resolution_counts_first_turn", S + "qa.py", "turns = _agent_turns(item)[1:]", "turns = _agent_turns(item)[0:]"),
    ("shouting_threshold", S + "qa.py", '>= 3:\n            return False', '>= 4:\n            return False'),
    ("critical_recall_miscounted", S + "qa.py", "fn = sum(1 for a, h in zip(auto_c, human_c) if h and not a)", "fn = sum(1 for a, h in zip(auto_c, human_c) if h and a)"),
    # ingest.py: merging of pending cases
    ("pending_keeps_first_waiter_only", S + "ingest.py", "if case_id not in waiting:", "if not waiting:"),
    ("merge_skipped", S + "ingest.py", "self._merge(case_id, other, mid)", "pass"),
    ("merge_winner_is_highest_id", S + "ingest.py", "case_id = min(candidates)", "case_id = max(candidates)"),
    ("merge_drops_messages", S + "ingest.py", 'keep["messages"].extend(gone["messages"])', "pass"),
    ("merge_token_of_absorbed_case_unresolved", S + "ingest.py", "if m and self.resolve(m.group(1)) in self.cases:", "if m and m.group(1) in self.cases:"),
    ("pending_hides_indexed_parent_case", S + "ingest.py", "waiting + ([found] if found else [])", "(waiting or ([found] if found else []))"),
    ("merge_keeps_later_creation_time", S + "ingest.py", 'if gone["created_at"] < keep["created_at"]:', "if False:"),
    ("merge_keeps_no_first_response", S + "ingest.py", 'keep["first_response_at"] = min(frs) if frs else None', 'keep["first_response_at"] = keep["first_response_at"]'),
    ("merge_drops_agent_status_events", S + "ingest.py", 'add_event(keep, e["at"], e["status"], e["rank"], e["seq"])', "pass"),
    ("merge_drops_customer_open_events", S + "ingest.py", 'add_event(keep, m["at"], "open", RANK_CUSTOMER, self.next_seq())', "pass"),
    ("merge_drops_participants", S + "ingest.py", 'keep["participants"].append(p)', "pass"),
    ("duplicate_reports_absorbed_id", S + "ingest.py", '"case_id": self.resolve(self._index.get(mid))', '"case_id": self._index.get(mid)'),
    ("resolve_follows_one_hop_only", S + "ingest.py", "while case_id in self._merged and case_id not in seen:", "if case_id in self._merged and case_id not in seen:"),
    ("merge_alerts_of_absorbed_case_stay_active", S + "flow.py", "self.escalator.observe(gone, rule, severity, False, result[\"at\"])", "pass"),
    ("agent_reply_ignores_absorbed_id", S + "flow.py", "case = self.cases.get(self.ingestor.resolve(case_id))", "case = self.cases.get(case_id)"),
    ("sla_ignores_absorbed_id", S + "flow.py", "return sla.evaluate(self.cases[self.ingestor.resolve(case_id)], now, self.targets)", "return sla.evaluate(self.cases[case_id], now, self.targets)"),
    # sim.py (the audit itself must be able to fail)
    ("audit_ignores_notifications", S + "sim.py", "notes = list(notes or [])", "notes = []"),
    ("audit_cooldown_zero", S + "sim.py", "ORACLE_COOLDOWN = timedelta(minutes=120)", "ORACLE_COOLDOWN = timedelta(minutes=0)"),
    ("audit_never_reports_missing_alerts", S + "sim.py", "if cid is None or not recent:", "if False:"),
    ("audit_never_reports_spurious_alerts", S + "sim.py", 'if mid is None or n["rule"] not in expected.get((mid, n["at"]), ()):', "if False:"),
    ("audit_skips_resolution_elapsed", S + "sim.py", 'if abs(got["resolution"]["elapsed"] - res_el) > 1e-6:', "if False:"),
    ("audit_skips_first_response_elapsed", S + "sim.py", 'if abs(got["first_response"]["elapsed"] - fr_el) > 1e-6:', "if False:"),
    ("oracle_counts_saturday", S + "sim.py", "if local.weekday() < 5 and 9 <= local.hour < 17:", "if local.weekday() < 6 and 9 <= local.hour < 17:"),
    ("oracle_at_risk_ratio", S + "sim.py", "ORACLE_AT_RISK = 0.8", "ORACLE_AT_RISK = 0.9"),
    ("audit_skips_reply_threading", S + "sim.py", "if not (cases_with.get(rid, set()) & cases_with.get(parent, {None})):", "if False:"),
]


def main():
    work = tempfile.mkdtemp(prefix="tfmut_")
    bad_patterns, survivors, kills = [], [], {"assertion": 0, "crash": 0}

    def prepare():
        dst = os.path.join(work, "lab")
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "evidence", "__pycache__"))
        return dst

    def run_suite(dst):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=dst,
                               capture_output=True, text=True, timeout=240, env=env)
            return p.returncode, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    control, _ = run_suite(prepare())
    print("control (unmutated): %s" % ("PASS" if control == 0 else "FAIL (rc=%d)" % control))
    if control != 0:
        sys.exit(1)
    names = [m[0] for m in MUTANTS]
    if len(names) != len(set(names)):
        print("FAIL: duplicate mutant names")
        sys.exit(1)
    for name, rel, old, new in MUTANTS:
        dst = prepare()
        path = os.path.join(dst, rel)
        with open(path) as fh:
            text = fh.read()
        n = text.count(old)
        if n != 1:
            print("BADPATTERN %-38s matches %d times in %s" % (name, n, rel))
            bad_patterns.append(name)
            continue
        with open(path, "w") as fh:
            fh.write(text.replace(old, new))
        rc, err = run_suite(dst)
        if rc == 0:
            print("SURVIVED %s" % name)
            survivors.append(name)
        else:
            crashed = any(w in err for w in ("SyntaxError", "ImportError", "ModuleNotFoundError", "Ran 0 tests", "timeout"))
            kind = "crash" if crashed else "assertion"
            kills[kind] += 1
            print("KILLED   %-38s (%s)" % (name, kind))
    shutil.rmtree(work, ignore_errors=True)
    killed = kills["assertion"] + kills["crash"]
    print("\nmutants: %d, killed: %d (by assertion %d, by import or syntax crash %d), survived: %d, bad patterns: %d"
          % (len(MUTANTS), killed, kills["assertion"], kills["crash"], len(survivors), len(bad_patterns)))
    if survivors or bad_patterns:
        print("FAIL: survivors=%s bad_patterns=%s" % (survivors, bad_patterns))
        sys.exit(1)
    print("OK: every mutant killed")


if __name__ == "__main__":
    main()
