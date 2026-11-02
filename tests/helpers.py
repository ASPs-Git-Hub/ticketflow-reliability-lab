import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

UTC = timezone.utc


def D(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def mail(mid, subject="Invoice question", body="Please help with my invoice", at=None, **kw):
    e = {"message_id": mid, "from": "alex@cust.example", "subject": subject, "body": body,
         "date": (at or D(2026, 3, 2, 9)).isoformat()}
    e.update(kw)
    return e
