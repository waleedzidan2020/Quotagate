from __future__ import annotations
import re
from . import shaping


def _tc_iface_ok_compat(iface, expected_count):
    """Verify QuotaGate tc state without depending on one iproute2 text format.

    Older antiX/iproute2 builds print fw filters/classes differently from newer
    Ubuntu runners.  The old verifier counted literal words such as ``fw`` and
    ``flowid 1:``, so a correctly-installed filter could be rejected and the
    fail-open path would immediately remove a working speed limit.

    All tc add/replace calls are already executed with check=True.  Here we only
    need to make sure the HTB root/classes still exist and that priority-10
    filter state is present; we deliberately do not depend on cosmetic output.
    """
    try:
        expected_count = int(expected_count or 0)
    except Exception:
        return False
    if expected_count <= 0:
        return True

    q = shaping._run(['tc', 'qdisc', 'show', 'dev', iface])
    if q.returncode:
        return False
    # Accept the common old/new forms, e.g.:
    #   qdisc htb 1: root ...
    #   htb 1: root ...
    if not re.search(r'\bhtb\s+1:\s+root\b', q.stdout or ''):
        return False

    classes = shaping._run(['tc', 'class', 'show', 'dev', iface])
    if classes.returncode:
        return False
    text = classes.stdout or ''
    # A shaping interface must contain the pass-through/default class plus at
    # least the expected number of limited classes.  Count class records rather
    # than relying on the exact token sequence used by a particular tc build.
    class_records = [ln for ln in text.splitlines() if re.search(r'\bclass\s+htb\s+1:[0-9a-fA-F]+\b', ln)]
    if len(class_records) < expected_count + 1:
        return False

    filters = shaping._run(['tc', 'filter', 'show', 'dev', iface, 'parent', '1:'])
    if filters.returncode:
        return False
    ftext = (filters.stdout or '').strip()
    if not ftext:
        return False
    # iproute2 versions vary between "pref 10", "prio 10", "fw", "handle",
    # "flowid" and "classid" wording.  Presence of priority-10 filter output is
    # enough here because the filter add command itself was checked synchronously.
    if not (re.search(r'\b(pref|prio)\s+10\b', ftext) or ' fw ' in (' ' + ftext + ' ') or 'handle ' in ftext):
        return False
    return True


def install():
    shaping._tc_iface_ok = _tc_iface_ok_compat
