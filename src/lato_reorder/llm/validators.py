"""Validators that hold the LLM to the engine. Any violation means the answer is not used as-is.

V1 schema          enforced by constrained decoding + Pydantic (a parse failure is a violation)
V2 part_key        the answer is about the part that was asked about
V3 echo            decision / tier / quantity / route equal the engine's
V4 cited rules     non-empty and a subset of rules_applied
V5 numbers         every number in the prose appears in the input; no currency, no "order more"
V6 evidence        every concern quotes the input verbatim, the quote supports the kind of concern, it is not
                   just an engine flag, no BOM concern once procurement set units per bike, bom qty in 1..4
"""

import math
import re

from lato_reorder.llm.schemas import PartNarrative, SummaryOutput
from lato_reorder.plan import ConcernCode
from lato_reorder.policy.decision import EngineDecision
from lato_reorder.policy.rules import Flag

NUMBER = re.compile(r"(?<![\w.\-])\d+(?:[.,]\d+)?(?![\w])")  # skips IDs such as TIER-1
CURRENCY = re.compile(r"[$€£¥]|\b(USD|EUR|GBP|dollars?|euros?)\b", re.IGNORECASE)
QUANTITY_ADVICE = re.compile(
    r"\b(order|buy|purchase)\s+(more|less|fewer|extra|additional)\b|\b(increase|reduce|lower|raise)\s+the\s+"
    r"(order|quantity)\b",
    re.IGNORECASE,
)
MAX_SUGGESTED_BOM = 4

# The quoted evidence must actually support the kind of concern raised.
EVIDENCE_SUPPORT = {
    ConcernCode.BOM_QTY_LIKELY_GT_1: re.compile(
        r"wheels?|pedals?|pairs?|set of|\btwo\b|\bboth\b|x\s?2", re.I
    ),
    ConcernCode.MODEL_STATUS_SIGNAL: re.compile(
        r"discontinu|phas(e|ed|ing) out|recall|replac|end[- ]of[- ]life|obsolete|withdraw|no longer", re.I
    ),
    ConcernCode.DATA_IMPLAUSIBLE: re.compile(r"\d"),
}


NUMBER_WORDS = {
    w: float(i)
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
NUMBER_WORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def _numbers(text: str) -> set[float]:
    """Numbers in prose, digits or words: "three parts" is a count just like "3 parts"."""
    out = {NUMBER_WORDS[w.lower()] for w in NUMBER_WORD.findall(text)}
    for token in NUMBER.findall(text):
        try:
            out.add(round(float(token.replace(",", "")), 2))
        except ValueError:
            continue
    return out


def allowed_numbers(*sources: str) -> set[float]:
    allowed: set[float] = set()
    for s in sources:
        allowed |= _numbers(s)
    # Shares are given as fractions; allow the same figure as a percentage.
    allowed |= {round(x * 100, 2) for x in allowed if 0 < x <= 1}
    # Rounding a given figure is not inventing one: 2222.2 cycles may be written as 2222 or 2222.2.
    decimals = {x for x in allowed if x != int(x)}
    allowed |= {float(math.floor(x)) for x in decimals} | {float(math.ceil(x)) for x in decimals}
    allowed |= {round(x, 1) for x in decimals}
    return allowed


# JSON quoting and escaping are not part of the data: the model may quote `"on_hand": 5` as `on_hand: 5`
# and a description containing `"` appears escaped as `\"` in the JSON it was shown.
JSON_QUOTING = str.maketrans("", "", '"\\')


def _norm(text: str) -> str:
    return " ".join(text.translate(JSON_QUOTING).casefold().split())


def _context(text: str, number: float) -> str:
    for m in NUMBER.finditer(text):
        if round(float(m.group().replace(",", "")), 2) == number:
            return text[max(0, m.start() - 30) : m.end() + 15].strip()
    for m in NUMBER_WORD.finditer(text):
        if NUMBER_WORDS[m.group().lower()] == number:
            return text[max(0, m.start() - 30) : m.end() + 15].strip()
    return ""


def check_prose(texts: list[str], allowed: set[float]) -> list[str]:
    violations = []
    for text in texts:
        invented = sorted(n for n in _numbers(text) if n not in allowed)
        if invented:
            where = "; ".join(repr(_context(text, n)) for n in invented)
            violations.append(f"V5 numbers not in the input: {invented} in {where}")
        if CURRENCY.search(text):
            violations.append("V5 mentions currency or cost")
        if QUANTITY_ADVICE.search(text):
            violations.append("V5 gives quantity advice")
    return violations


def validate_part(n: PartNarrative, d: EngineDecision, input_text: str) -> list[str]:
    v: list[str] = []
    if n.part_key != d.facts.part_key:
        v.append(f"V2 part_key {n.part_key!r} != {d.facts.part_key!r}")
    echo = n.echo
    expected = (d.decision, d.tier, d.quantity, d.route)
    if (echo.decision, echo.tier, echo.quantity, echo.route) != expected:
        v.append(
            f"V3 echo {echo.decision.value}/{echo.tier}/{echo.quantity}/{echo.route.value} != engine "
            f"{d.decision.value}/{d.tier}/{d.quantity}/{d.route.value}"
        )
    if not n.cited_rules:
        v.append("V4 no rule cited")
    extra = [r.value for r in n.cited_rules if r not in d.rules_applied]
    if extra:
        v.append(f"V4 cited rules not applied: {extra}")
    if not n.justification.strip() or not n.quantity_explanation.strip():
        v.append("V1 empty explanation")
    v += check_prose([n.justification, n.quantity_explanation], allowed_numbers(input_text))
    haystack = _norm(input_text)
    flag_names = {f.value.casefold() for f in Flag} | {f.value.casefold() for f in d.flags}
    for c in n.concerns:
        if c.evidence.strip().casefold() in flag_names:
            v.append(f"V6 {c.code.value} only restates an engine flag ({c.evidence.strip()})")
        elif len(c.evidence.strip()) < 3 or _norm(c.evidence) not in haystack:
            v.append(f"V6 {c.code.value} evidence is not a quote from the input: {c.evidence[:60]!r}")
        elif (pattern := EVIDENCE_SUPPORT.get(c.code)) and not pattern.search(c.evidence):
            v.append(f"V6 {c.code.value} evidence does not support that kind of concern: {c.evidence[:60]!r}")
    if d.facts.units_source == "OVERRIDE" and any(
        c.code is ConcernCode.BOM_QTY_LIKELY_GT_1 for c in n.concerns
    ):
        v.append("V6 BOM_QTY_LIKELY_GT_1 raised although procurement already set units per bike")
    if len(n.concerns) > 3:
        v.append("V6 more than 3 concerns")
    if n.suggested_bom_qty is not None and not 1 <= n.suggested_bom_qty <= MAX_SUGGESTED_BOM:
        v.append(f"V6 suggested_bom_qty {n.suggested_bom_qty} outside 1..{MAX_SUGGESTED_BOM}")
    return v


def validate_summary(s: SummaryOutput, input_text: str) -> list[str]:
    return check_prose([s.headline, *s.top_risks, *s.capital_notes], allowed_numbers(input_text))
