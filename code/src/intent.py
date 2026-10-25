"""Deterministic multilingual request-intent classification.

Intent is decided from `request_text` alone, in this priority order:

1. An explicit refusal/negation about deleting the account or its data
   ("do not delete", "not asking for deletion", ...).
2. An explicit retention instruction ("leave the account active",
   "keep the account as it is", "without removing any of my records", ...).
3. Otherwise the message is treated as an erasure request.  Erasure follow-ups
   often contain no deletion verb at all (for example a request to confirm that
   a profile is "gone"), so absence of a verb is not evidence of non-deletion.

Incidental negatives such as "do not display the email" or "do not treat that
record as mine" are *not* refusals to delete and must not suppress the request.
Those are excluded by keeping the negation patterns specific to the act of
deletion itself.
"""

from __future__ import annotations

import re


# Refusals/negations of deletion. Deliberately narrow: these must name the act of
# deleting, erasing, removing, or closing the account/data.
NEGATION_PATTERNS = (
    r"\bnot asking for deletion\b",
    r"\bnot requesting (deletion|erasure)\b",
    r"\bdo not (delete|erase|remove|close)\b",
    r"\bdon't (delete|erase|remove|close)\b",
    r"\bdoes not (delete|erase)\b",
    r"\bnot (delete|erase|remove|close) (anything|my|the)\b",
    r"\bwithout (deleting|erasing)\b",
    # Non-English refusals, phrased directly against the act of deletion.
    r"\bno quiero que (borren|eliminen|supriman)\b",
    r"\bno (borren|eliminen|supriman)\b",
    r"\bsin (borrar|eliminar)\b",
    r"\bne (supprimez|effacez) pas\b",
    r"\bne pas (supprimer|effacer)\b",
    r"\bnicht (l[öo]schen|entfernen)\b",
    r"\bn[ãa]o (exclua|apague|excluir|apagar)\b",
    r"\bsem (excluir|apagar)\b",
    r"\bnon (cancellare|eliminare)\b",
    r"削除しないで|削除しないでください|削除しない",
    r"삭제하지\s*마",
    r"لا تحذف",
)

# Explicit "keep my data" instructions that accompany a different request type
# (access, correction, export, marketing opt-out).
RETENTION_PATTERNS = (
    r"\bleave the account active\b",
    r"\bleave my (profile|account)\b[^.]*\bin place\b",
    r"\bkeep the account as (it is|is)\b",
    r"\bkeep my (profile|account)\b[^.]*\bin place\b",
    r"\bwithout removing any of my records\b",
    r"\bcontinue using the service\b",
    r"\bstill need the account\b",
    r"\bstill use it\b",
)

# Multilingual erasure vocabulary, used for diagnostics and for any future case
# that needs a positive verb signal.
DELETION_PATTERNS = (
    r"\bdelet(e|ed|ing|ion)\b",
    r"\beras(e|ed|ing|ure)\b",
    r"\bremov(e|ed|ing|al)\b",
    r"\bclos(e|ed|ing)\b",
    r"\beliminat(e|ed|ing)\b",
    r"\bsupprim\w*\b",
    r"\beffac\w*\b",
    r"\bexclus\w*\b",
    r"\bexclu[íi]\w*\b",
    r"\belimin\w*\b",
    r"\bl[öo]sch\w*\b",
    r"\bcancell\w*\b",
    r"\brimuov\w*\b",
    r"\bverwijder\w*\b",
    r"\busu[ńn]\w*\b",
    r"删除|删掉|清除|銷除|消除",
    r"削除|消去",
    r"삭제|제거",
    r"حذف|إزالة",
)

_NEGATION_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in NEGATION_PATTERNS)
_RETENTION_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in RETENTION_PATTERNS)
_DELETION_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in DELETION_PATTERNS)


def matched_markers(text: str) -> dict[str, list[str]]:
    """Return the markers that fired, for auditability and tests."""
    return {
        "negation": [regex.pattern for regex in _NEGATION_RE if regex.search(text)],
        "retention": [regex.pattern for regex in _RETENTION_RE if regex.search(text)],
        "deletion": [regex.pattern for regex in _DELETION_RE if regex.search(text)],
    }


def is_deletion_authorized(text: str) -> bool:
    """True when the message authorises erasure of the subject's data."""
    markers = matched_markers(text)
    if markers["negation"]:
        return False
    if markers["retention"]:
        return False
    return True
