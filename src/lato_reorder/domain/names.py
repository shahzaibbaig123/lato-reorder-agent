"""Name normalisation for joining part→product references and detecting near-duplicates.

Normalised keys are only used for matching and dedupe checks. The ItemName sent to SAP is always
the exact original string from the latest snapshot (the PO tool is case-sensitive).
"""

import re
import unicodedata
from difflib import SequenceMatcher

_NON_WORD = re.compile(r"[^\w]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    return _NON_WORD.sub(" ", text).strip()


def has_control_chars(name: str) -> bool:
    return bool(_CONTROL.search(name))


def similarity(a: str, b: str) -> float:
    """Token-sorted similarity in [0, 1] on normalised names."""
    ta = " ".join(sorted(normalise(a).split()))
    tb = " ".join(sorted(normalise(b).split()))
    return SequenceMatcher(None, ta, tb).ratio()
