"""Deciding whether an answer declined to answer.

This is load-bearing in two places — the validator sets ``grounded_ratio`` to
1.0 for a refusal rather than hunting for citations that a refusal correctly
does not have, and the grounding eval's headline number is the refusal rate on
unanswerable questions — and both had their own copy of a list of fixed
phrases. Fixed phrases do not survive contact with a model. All three of these
are refusals and none matched a list containing "does not contain":

    "The indexed documents do not specify the purge duration..."
    "The indexed documents do not cover vessel V-9999."
    "The current plant manager is not explicitly mentioned in the provided
     documents."

The eval scored them as the system inventing answers, at a 20% refusal rate,
when the true rate was 100%. A number that wrong in that direction would have
sent someone off to fix a hallucination problem that did not exist.

So the match is structural rather than literal: a *negated availability verb*
attached to a *document-ish subject*, in either order. That covers phrasings
nobody has written yet, which is the point.

The scan is limited to the opening sentences. That bound is deliberate and is
the asymmetry that matters here. A refusal leads with its limitation; a caveat
in the middle of a real answer ("the 2023 report does not contain this, but the
2029 survey shows 9.20 mm") is a caveat. Treating that caveat as a refusal
would hand the answer a grounded_ratio of 1.0 and skip the grounding check
entirely — a wrong answer marked fully grounded. Missing a late refusal only
costs a refusal its exemption, and it still scores well on coverage. Of the two
errors, only the first one ships a bad figure to an engineer.
"""

from __future__ import annotations

import re

#: What a refusal is about: the evidence, not the equipment.
_SUBJECT = (
    r"(?:indexed |provided |available |retrieved |supplied |given |cited |"
    r"attached |referenced )*"
    r"(?:document|source|corpus|record|material|evidence|context|excerpt|"
    r"passage|report|sop|procedure|log|register|manual|datasheet|file)s?"
)

#: Ways of saying "it isn't there".
_NEGATION = (
    r"(?:do(?:es)?\s+not|do(?:es)?n[''`]t|did\s+not|didn[''`]t|cannot|can[''`]t|"
    r"could\s+not|couldn[''`]t|is\s+not|are\s+not|isn[''`]t|aren[''`]t|"
    r"was\s+not|were\s+not|fails?\s+to|failed\s+to|lacks?|lacked|"
    r"contains?\s+no|have\s+no|has\s+no|had\s+no|include\s+no|omits?)"
)

_VERB = (
    r"(?:contain|cover|specify|mention|include|state|record|provide|address|"
    r"list|indicate|document|report|show|give|describe|detail|define|"
    r"reference|capture|available|present|found|specified|mentioned|stated|"
    r"recorded|documented|listed|covered|included|indicated|provided|"
    r"described|captured|referenced|information|data)"
)

#: "The documents do not specify ..." — subject, then the negated verb.
_SUBJECT_FIRST = re.compile(
    rf"\b{_SUBJECT}\b[^.!?]{{0,60}}?\b{_NEGATION}\b[^.!?]{{0,40}}?\b{_VERB}\b",
    re.IGNORECASE,
)

#: "... is not mentioned in the provided documents" — the mirror image.
_PREDICATE_FIRST = re.compile(
    rf"\b{_NEGATION}\b[^.!?]{{0,40}}?\b{_VERB}\b[^.!?]{{0,40}}?\b"
    rf"(?:in|within|among|across|from)\b[^.!?]{{0,40}}?\b{_SUBJECT}\b",
    re.IGNORECASE,
)

#: Forms that carry no document subject but are unambiguous on their own.
_STANDALONE = re.compile(
    r"\b(?:no\s+(?:record|mention|reference|information|data|evidence|"
    r"details?)\s+(?:of|on|about|regarding|for)"
    r"|(?:i\s+)?(?:cannot|can[''`]t|am\s+unable\s+to|was\s+unable\s+to)\s+"
    r"(?:answer|determine|find|locate|confirm|verify|establish)"
    # Passive: "this question cannot be answered from the passages".
    r"|(?:cannot|can[''`]t|could\s+not|couldn[''`]t)\s+be\s+"
    r"(?:answered|determined|found|located|established|confirmed|verified)"
    r"|(?:could\s+not|couldn[''`]t)\s+(?:find|locate|determine|identify)"
    r"|not\s+enough\s+(?:information|evidence|data)"
    r"|insufficient\s+(?:information|evidence|data))\b",
    re.IGNORECASE,
)

#: A contrastive turn: what follows it is the answer's real content.
_CONTRAST = re.compile(
    r"\b(?:but|however|although|though|whereas|nevertheless|nonetheless|"
    r"that\s+said|on\s+the\s+other\s+hand)\b",
    re.IGNORECASE,
)

#: Any negation, used only to ask whether a contrast clause is itself negative.
_ANY_NEGATION = re.compile(
    rf"\b(?:{_NEGATION}|no|none|neither|nor|without|absent|unavailable|"
    r"unclear|unknown|unspecified|unstated|silent|nothing|lacking)\b",
    re.IGNORECASE,
)

#: A resolved citation marker. An answer that has already cited something
#: before it gets to a limitation has answered; the limitation is a caveat.
_CITATION = re.compile(r"\[\d+\]")

#: How far in to look. Three sentences covers a refusal that first restates the
#: question; beyond that it is an answer with a caveat.
LEADING_SENTENCES = 3


def _sentences(text: str) -> list[str]:
    """Split into sentences, treating line breaks as boundaries too.

    Answers are markdown. A table has no full stops, so splitting on
    punctuation alone welded three table rows and the sentence after them
    into one "sentence", which dragged a fourth-sentence caveat inside the
    window. Rows, rules and headings are not prose and are dropped.
    """
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    kept = []
    for part in parts:
        candidate = part.strip()
        if candidate.startswith(("|", "#", "---")):
            continue
        if len(re.findall(r"[A-Za-z]", candidate)) < 3:
            continue
        kept.append(candidate)
    return kept


def _leading(text: str, sentences: int = LEADING_SENTENCES) -> str:
    return " ".join(_sentences(text)[:sentences])


def _asserts_after_contrast(head: str) -> bool:
    """Whether a contrast clause makes a positive claim.

    This is the guard on the one error that actually costs something. Consider:

        "The 2023 report does not contain this figure, but the 2029 survey
         shows the minimum reading at CML-04 is 9.20 mm [2]."

    Every structural test for a refusal matches the first clause, and calling
    that a refusal would exempt a real load-bearing number from the grounding
    check and badge it fully grounded. Compare a genuine refusal, which also
    uses "but" but stays negative on the other side of it:

        "...outlines nitrogen purge criteria [1], but no duration is
         explicitly stated."

    So the discriminator is not the contrast marker, it is whether the clause
    after it is itself negated. An unnegated one means the answer went on to
    assert something, and an answer that asserts something is not a refusal.
    """
    for match in _CONTRAST.finditer(head):
        clause = head[match.end() :]
        # Stop at the next sentence or the next contrast, whichever is first.
        stop = re.search(r"[.!?]", clause)
        if stop:
            clause = clause[: stop.start()]
        if clause.strip() and not _ANY_NEGATION.search(clause):
            return True
    return False


def is_refusal(text: str, sentences: int = LEADING_SENTENCES) -> bool:
    """Whether the answer declines to answer from the available evidence.

    Args:
        text: The synthesised answer.
        sentences: How many leading sentences to consider. See the module
            docstring for why this is bounded.
    """
    if not text or not text.strip():
        # An empty answer is a failure, not an honest refusal. Calling it one
        # would let a crashed synthesis step score as a correct abstention.
        return False
    leading = _sentences(text)[:sentences]
    for index, sentence in enumerate(leading):
        if not (
            _SUBJECT_FIRST.search(sentence)
            or _PREDICATE_FIRST.search(sentence)
            or _STANDALONE.search(sentence)
        ):
            continue
        if _asserts_after_contrast(sentence):
            continue
        # "The design pressure is 18.0 barg [1]. Other sources do not specify
        # it." The second sentence is shaped exactly like a refusal, and the
        # answer is a good one. What separates it from a refusal that first
        # restates the question is that a restatement cites nothing.
        return not any(_CITATION.search(s) and not _ANY_NEGATION.search(s) for s in leading[:index])
    return False
