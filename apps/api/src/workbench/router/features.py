"""Cheap lexical features for router stage 1.

No model call, no tokenizer, no network — just regexes over the prompt. The
point is to resolve the easy majority of requests in well under a millisecond so
that stage 2's LLM classifier is reserved for genuinely ambiguous input.

These features are deliberately shallow. They are not trying to understand the
request; they are trying to notice the handful of surface signals that reliably
separate "write me a script" from "what does the manual say".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from workbench.core.hashing import digest

# --- lexical signals ---------------------------------------------------------
_CODE_FENCE = re.compile(r"```|~~~")
_CODE_VERB = re.compile(
    r"\b(write|implement|refactor|debug|fix|script|function|class|compile|"
    r"unit test|parse|regex|algorithm|code)\b",
    re.IGNORECASE,
)
_FILE_EXTENSION = re.compile(
    r"\.(py|js|ts|sql|sh|json|ya?ml|csv|xlsx?|docx?|pptx?|pdf|ipynb)\b", re.IGNORECASE
)
_LIBRARY = re.compile(
    r"\b(pandas|numpy|scipy|matplotlib|openpyxl|sqlalchemy|fastapi|django|flask|"
    r"pytorch|tensorflow|sklearn)\b",
    re.IGNORECASE,
)
_COMPARISON_VERB = re.compile(
    r"\b(compare|contrast|why|evaluate|assess|analyse|analyze|explain|difference|"
    r"trade-?off|recommend|should we)\b",
    re.IGNORECASE,
)
# Allows adjectives between the verb and the noun, so "summarise the entire
# inspection report" matches as readily as "summarise the report".
_SUMMARISE_DOC = re.compile(
    r"\b(summari[sz]e|summary of|overview of|go through|read through|walk through)\b"
    r"(\s+\w+){0,4}?\s+"
    r"(document|report|manual|sop|procedure|file|deck|book|correspondence)s?\b",
    re.IGNORECASE,
)
_MULTI_DOC = re.compile(
    r"\b(across|between|all)\s+(the\s+)?(documents|reports|manuals|files|sops)\b",
    re.IGNORECASE,
)
_DRAWING_NOUN = re.compile(
    r"\b(p&?id|pid|drawing|diagram|schematic|photo|photograph|image|picture|sketch|"
    r"isometric|blueprint|scan|nameplate|gauge|screenshot)\b",
    re.IGNORECASE,
)
_SPATIAL_VERB = re.compile(
    r"\b(which valve|where (is|on|does)|point to|locate|upstream|downstream|"
    r"connected to|isolate|highlight|circled|marked|handwritten)\b",
    re.IGNORECASE,
)
_ARTIFACT_REQUEST = re.compile(
    r"\b(generate|create|build|produce|draft|make)\s+(me\s+)?(an?|the)?\s*"
    r"(report|deck|presentation|spreadsheet|workbook|memo|letter|document|chart|table)\b",
    re.IGNORECASE,
)
_ARTIFACT_CODE = re.compile(
    r"\b(generate|write|create)\s+(me\s+)?(a\s+)?(script|program|code|notebook)\b",
    re.IGNORECASE,
)

# Refinery quantities: unit density is a strong signal for calculation work.
_UNIT = re.compile(
    r"\b\d+(\.\d+)?\s*(bar|barg|psi|psig|kpa|mpa|°?[cf]\b|deg\s?[cf]|mm/?y(ea)?r|mm|"
    r"cm|m3/h|m³/h|kg/cm2|kg/cm²|t/h|tph|kw|mw|rpm|cst|ppm|wt%|%)\b",
    re.IGNORECASE,
)
#: Equipment tags such as V-1201, P-101A, E-2405. Highly characteristic of MRPL
#: documents and a good hint that retrieval will be needed.
_EQUIPMENT_TAG = re.compile(r"\b[A-Z]{1,3}-\d{3,5}[A-Z]?\b")


@dataclass(frozen=True, slots=True)
class PromptFeatures:
    """What stage 1 measured about a prompt."""

    word_count: int = 0
    has_code_fence: bool = False
    code_verb_hits: int = 0
    file_extension_hits: int = 0
    library_hits: int = 0
    comparison_verb_hits: int = 0
    summarise_document: bool = False
    multi_document: bool = False
    drawing_noun_hits: int = 0
    spatial_verb_hits: int = 0
    artifact_request: bool = False
    artifact_code_request: bool = False
    unit_hits: int = 0
    equipment_tag_hits: int = 0
    has_images: bool = False
    estimated_tokens: int = 0
    attachment_count: int = 0

    @property
    def is_very_short(self) -> bool:
        return self.word_count < 5

    @property
    def is_multi_clause(self) -> bool:
        return self.word_count > 40

    def as_dict(self) -> dict[str, Any]:
        return {
            "word_count": self.word_count,
            "has_code_fence": self.has_code_fence,
            "code_verb_hits": self.code_verb_hits,
            "file_extension_hits": self.file_extension_hits,
            "library_hits": self.library_hits,
            "comparison_verb_hits": self.comparison_verb_hits,
            "summarise_document": self.summarise_document,
            "multi_document": self.multi_document,
            "drawing_noun_hits": self.drawing_noun_hits,
            "spatial_verb_hits": self.spatial_verb_hits,
            "artifact_request": self.artifact_request,
            "artifact_code_request": self.artifact_code_request,
            "unit_hits": self.unit_hits,
            "equipment_tag_hits": self.equipment_tag_hits,
            "has_images": self.has_images,
            "estimated_tokens": self.estimated_tokens,
            "attachment_count": self.attachment_count,
        }

    def digest(self) -> str:
        """Stable fingerprint, stored with the routing decision for analysis."""
        return digest(self.as_dict())


def extract(
    text: str,
    *,
    has_images: bool = False,
    estimated_tokens: int = 0,
    attachment_count: int = 0,
) -> PromptFeatures:
    """Measure a prompt. Pure and allocation-light; called on every request."""
    return PromptFeatures(
        word_count=len(text.split()),
        has_code_fence=bool(_CODE_FENCE.search(text)),
        code_verb_hits=len(_CODE_VERB.findall(text)),
        file_extension_hits=len(_FILE_EXTENSION.findall(text)),
        library_hits=len(_LIBRARY.findall(text)),
        comparison_verb_hits=len(_COMPARISON_VERB.findall(text)),
        summarise_document=bool(_SUMMARISE_DOC.search(text)),
        multi_document=bool(_MULTI_DOC.search(text)),
        drawing_noun_hits=len(_DRAWING_NOUN.findall(text)),
        spatial_verb_hits=len(_SPATIAL_VERB.findall(text)),
        artifact_request=bool(_ARTIFACT_REQUEST.search(text)),
        artifact_code_request=bool(_ARTIFACT_CODE.search(text)),
        unit_hits=len(_UNIT.findall(text)),
        equipment_tag_hits=len(_EQUIPMENT_TAG.findall(text)),
        has_images=has_images,
        estimated_tokens=estimated_tokens,
        attachment_count=attachment_count,
    )


def equipment_tags(text: str) -> list[str]:
    """Equipment tags mentioned in the text, deduplicated in order.

    Used by query rewriting to add exact-match keyword terms, which is where
    sparse retrieval earns its place: ``V-1201`` is precisely the kind of token
    a dense embedding blurs away.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _EQUIPMENT_TAG.findall(text):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out
