"""What this workbench will not be asked to do.

This is a policy gate, not a content filter, and the difference matters when
explaining it. The system is already unable to help with most misuse by
construction: it has no route to the internet, it answers only from documents
the asker is cleared to read, and code runs in a container with no network
interface. None of that depends on reading the question.

What reading the question adds is *refusal with a record*. In a plant, someone
asking the assistant how to bypass a safety interlock, or for the credentials
to a control system, is an event the safety and security departments want to
know about — separately from whether the assistant would have been any use.
So a matched request is stopped before any model runs, answered plainly, and
written to the audit log with its category.

The matching is deliberately literal and narrow. Two reasons:

  - It must not fire on the work. "Bypass the vent valve", "override the
    controller setpoint" and "disable the pump" are ordinary things to read
    about in an SOP, so a rule keys on asking *how to defeat a protective
    function*, not on the verb alone. A gate that blocks real questions is one
    the plant switches off.
  - A regex is not a security boundary and this module does not pretend to be
    one. Someone determined to phrase around it will. The controls that do not
    care how the question is worded are the air gap, the clearance filter, the
    sandbox and the second-person approval; this gate is the policy layer that
    sits in front of them and leaves a trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class PolicyCategory:
    """Why a request was refused. Stored on the audit record."""

    SAFETY_BYPASS = "safety_bypass"
    SABOTAGE = "sabotage"
    CREDENTIALS = "credentials"
    INTRUSION = "intrusion"
    EXFILTRATION = "exfiltration"
    INJECTION = "injection"


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    allowed: bool
    category: str = ""
    #: Shown to the person who asked. Says what was refused and why, without
    #: lecturing and without hinting at a rephrasing that would pass.
    message: str = ""
    #: The phrase that matched, for the audit record. Never shown in the UI.
    matched: str = ""


#: Defeating a protective function. The protected thing has to be named — an
#: interlock, a trip, a relief device, a permit — because "override the
#: setpoint" is a Tuesday and "override the high-level trip" is not.
_PROTECTIONS = (
    r"interlock|trip|shutdown\s+system|sis\b|esd\b|safety\s+system|relief\s+(?:valve|device)|"
    r"psv|rupture\s+disc|alarm|permit(?:\s+to\s+work)?|lockout|tagout|loto|gas\s+detect\w*|"
    r"fire\s+(?:and\s+gas|suppression)|safeguard"
)
_DEFEAT = (
    r"bypass|by-pass|defeat|disable|deactivate|override|circumvent|inhibit|force|jumper|suppress"
)

#: Asking to do a thing, as opposed to asking about it. "Can we do hot work
#: without a permit" is a request; "what happens if hot work is done without a
#: permit" is a safety question with the same words in it, and refusing the
#: second would be refusing the training material the corpus exists to serve.
_INTENT = (
    r"can\s+(?:i|we)|could\s+(?:i|we)|may\s+(?:i|we)|how\s+(?:do|can|would)\s+(?:i|we)|"
    r"is\s+it\s+(?:ok|okay|fine|alright|acceptable)|any\s+way\s+to|way\s+(?:to|around)|"
    r"let\s+me|help\s+me|show\s+me\s+how"
)

#: Things in the plant that can be attacked. Kept broad, because the rules that
#: use it also require an attack verb — "the unit" on its own is every second
#: question anyone asks here.
_TARGETS = (
    r"unit|plant|refinery|vessel|tank|pipeline|facility|column|reactor|site|"
    r"terminal|storage|line|cdu|compressor|furnace|system"
)

#: Attacking the plant physically. Each alternative is a verb form, never a
#: noun, because the nouns are ordinary vocabulary here: a bomb calorimeter is
#: a fuel-testing instrument, "lower explosive limit" is on every gas test, and
#: a blast wall is a safety feature. Matching "bomb" or "explosive" as words
#: would refuse a quarter of the corpus.
_ATTACK = (
    r"bomb(?!\s*calorimeter)|blow\s+up|detonate|sabotage|torch\b|set\s+fire\s+to|"
    r"deliberately\s+(?:rupture|overpressur\w+|damage)|poison|contaminate\s+the|"
    r"cause\s+an?\s+(?:explosion|fire|leak|release|rupture)|start\s+a\s+fire"
)

#: Asking how to do something, as opposed to asking what it is.
_HOWTO = (
    r"how\s+(?:to|do\s+i|can\s+i|would\s+i|do\s+we|can\s+we)|ways?\s+to|"
    r"best\s+way\s+to|help\s+me\s+to|steps?\s+to|guide\s+to"
)

_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        PolicyCategory.SABOTAGE,
        re.compile(rf"\b(?:{_ATTACK})\b[^.?!]{{0,30}}\b(?:the\s+)?(?:{_TARGETS})\b", re.IGNORECASE),
        "This asks how to cause harm to the plant. The workbench will not help with that, "
        "and the request has been recorded.",
    ),
    (
        PolicyCategory.SABOTAGE,
        re.compile(rf"\b(?:{_HOWTO})\b[^.?!]{{0,25}}\b(?:{_ATTACK})\b", re.IGNORECASE),
        "This asks how to cause harm to the plant. The workbench will not help with that, "
        "and the request has been recorded.",
    ),
    (
        PolicyCategory.SAFETY_BYPASS,
        re.compile(rf"\b(?:{_DEFEAT})\b[^.?!]{{0,40}}\b(?:{_PROTECTIONS})\b", re.IGNORECASE),
        "This asks how to defeat a protective function. The workbench will not help with "
        "that, and the request has been recorded.",
    ),
    (
        PolicyCategory.SAFETY_BYPASS,
        re.compile(
            rf"\b(?:{_PROTECTIONS})\b[^.?!]{{0,30}}\bwithout\b[^.?!]{{0,30}}"
            r"\b(?:permit|approval|authorisation|authorization|sign[- ]?off)\b",
            re.IGNORECASE,
        ),
        "This asks how to work on a protected system without the authorisation that governs "
        "it. The workbench will not help with that, and the request has been recorded.",
    ),
    (
        PolicyCategory.SAFETY_BYPASS,
        re.compile(
            rf"\b(?:{_INTENT})\b[^.?!]{{0,60}}\bwithout\b[^.?!]{{0,30}}"
            r"\b(?:a\s+)?(?:permit|approval|authorisation|authorization|sign[- ]?off)\b",
            re.IGNORECASE,
        ),
        "This asks to carry out work without the permit that governs it. The workbench will "
        "not help with that, and the request has been recorded.",
    ),
    (
        PolicyCategory.CREDENTIALS,
        re.compile(
            r"\b(?:password|passwd|credential|api[\s_-]?key|private\s+key|secret\s+key|"
            r"access\s+token|ssh\s+key)s?\b[^.?!]{0,40}"
            r"\b(?:what|give|tell|show|list|find|get|need|share|send|reveal|dump)\b"
            r"|\b(?:what|give|tell|show|list|find|get|share|reveal|dump)\b[^.?!]{0,40}"
            r"\b(?:password|passwd|credential|api[\s_-]?key|private\s+key|secret\s+key|"
            r"access\s+token|ssh\s+key)s?\b",
            re.IGNORECASE,
        ),
        "This asks for credentials. The workbench never holds or hands out passwords, keys "
        "or tokens, and the request has been recorded.",
    ),
    (
        PolicyCategory.INTRUSION,
        re.compile(
            # "hack" needs no "into". \b keeps it off "hackathon", where a word
            # character follows — which matters rather a lot in this repository.
            r"\bhack(?:ing|s|ed)?\b[^.?!]{0,30}"
            rf"\b(?:into|the\s+)?(?:{_TARGETS}|network|scada|plc|dcs|hmi|server|"
            r"control|account|database|historian)\b"
            rf"|\b(?:{_HOWTO})\b[^.?!]{{0,20}}\bhack(?:ing|s)?\b"
            r"|\b(?:hack|break)\s+into\b|\bgain\s+(?:unauthorised|unauthorized|admin|root)\s+access\b"
            r"|\b(?:exploit|attack|breach|penetrate|compromise)\b[^.?!]{0,40}"
            r"\b(?:scada|plc|dcs|hmi|historian|network|server|system|control\s+system)\b"
            r"|\b(?:escalate\s+privileg|brute[\s-]?force|sql\s+inject)\w*",
            re.IGNORECASE,
        ),
        "This asks how to gain access to a system without authorisation. The workbench will "
        "not help with that, and the request has been recorded.",
    ),
    (
        PolicyCategory.EXFILTRATION,
        re.compile(
            r"\b(?:ignore|bypass|disable|get\s+around|work\s+around)\b[^.?!]{0,30}"
            r"\b(?:clearance|classification|access\s+control|permission|rbac|restriction)s?\b"
            r"|\b(?:show|list|give|read)\b[^.?!]{0,40}\bdocuments?\b[^.?!]{0,30}"
            r"\b(?:not\s+cleared|no\s+clearance|above\s+my|beyond\s+my|restricted\s+to\s+others)\b",
            re.IGNORECASE,
        ),
        "This asks the workbench to return documents outside your clearance. Clearance is "
        "applied inside the search itself and cannot be set aside, and the request has been "
        "recorded.",
    ),
    (
        PolicyCategory.INJECTION,
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
            r"(?:instruction|prompt|rule|direction)s?\b"
            r"|\b(?:reveal|show|print|repeat|output)\b[^.?!]{0,25}\b(?:system\s+prompt|"
            r"initial\s+instruction|your\s+instructions)\b"
            r"|\byou\s+are\s+now\s+(?:a|an|in)\b[^.?!]{0,30}\b(?:mode|assistant|model|dan)\b",
            re.IGNORECASE,
        ),
        "This asks the workbench to set aside the rules it runs under. Those are not "
        "negotiable from the question box, and the request has been recorded.",
    ),
)


def evaluate(user_input: str) -> PolicyVerdict:
    """Whether this request may proceed to the agent.

    Args:
        user_input: What the person typed, verbatim.
    """
    text = user_input.strip()
    if not text:
        return PolicyVerdict(allowed=True)

    for category, pattern, message in _RULES:
        match = pattern.search(text)
        if match:
            return PolicyVerdict(
                allowed=False,
                category=category,
                message=message,
                matched=match.group(0)[:120],
            )
    return PolicyVerdict(allowed=True)
