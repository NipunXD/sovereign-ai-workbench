"""Provenance: what a generated document must say about itself.

A Word report produced by an AI and emailed on looks exactly like one a person
wrote. Once it leaves this system nothing travels with it — not the sources, not
the models, not whether anyone checked it. So the provenance block is written
*into* the document rather than kept beside it in a database.

It records what a reader needs in order to decide how much to trust the thing in
front of them: which models produced it, which documents it drew on and at which
pages, whether any of that text came from a poor scan, who approved it, and a
digest of the file so a later copy can be shown to be the same one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from workbench.core.clock import now
from workbench.rag.citations import Citation

#: Shown on every generated artifact. Deliberately plain: a hedge nobody reads
#: is worse than a sentence that says what actually happened.
AI_NOTICE = (
    "This document was assembled by the MRPL Sovereign AI Workbench from the "
    "sources listed below. The figures and conclusions have not been verified by "
    "a person unless an approver is named above. Check it against the cited "
    "sources before acting on it."
)


@dataclass
class Provenance:
    """The record that travels inside the document."""

    run_id: str = ""
    generated_by: str = ""
    generated_at: str = field(default_factory=lambda: now().isoformat(timespec="seconds"))
    #: logical -> physical, for every model that contributed.
    models: dict[str, str] = field(default_factory=dict)
    citations: list[Citation] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    approved_by: str | None = None
    approved_at: str | None = None
    #: Filled in after the bytes exist, so it identifies the exact file.
    sha256: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[dict[str, Any]]:
        """One entry per source document, with the pages actually used."""
        by_document: dict[str, dict[str, Any]] = {}
        for citation in self.citations:
            entry = by_document.setdefault(
                citation.doc_id,
                {
                    "title": citation.doc_title,
                    "doc_type": citation.doc_type,
                    "pages": set(),
                    "lowest_confidence": 1.0,
                },
            )
            entry["pages"].add(citation.page_no)
            entry["lowest_confidence"] = min(entry["lowest_confidence"], citation.confidence)

        return [
            {
                "title": entry["title"],
                "doc_type": entry["doc_type"],
                "pages": sorted(entry["pages"]),
                "lowest_confidence": entry["lowest_confidence"],
            }
            for entry in by_document.values()
        ]

    @property
    def has_uncertain_sources(self) -> bool:
        """Whether anything here rests on poorly recognised text.

        Surfaced prominently, because a number transcribed from a bad photocopy
        at 61% confidence deserves a second look before it reaches a decision.
        """
        return any(source["lowest_confidence"] < 0.85 for source in self.sources)

    def lines(self) -> list[tuple[str, str]]:
        """The block as label/value pairs, for whichever format renders it."""
        rows: list[tuple[str, str]] = [
            ("Generated", self.generated_at),
            ("Requested by", self.generated_by or "—"),
            ("Run", self.run_id or "—"),
        ]
        if self.models:
            rows.append(("Models", ", ".join(f"{k} → {v}" for k, v in sorted(self.models.items()))))
        if self.tools_used:
            rows.append(("Tools", ", ".join(sorted(set(self.tools_used)))))
        rows.append(
            (
                "Approved by",
                f"{self.approved_by} on {self.approved_at}" if self.approved_by else "not approved",
            )
        )
        if self.sha256:
            rows.append(("Document digest", self.sha256))
        return rows

    def source_lines(self) -> list[str]:
        """Sources as readable references."""
        lines = []
        for source in self.sources:
            pages = ", ".join(str(page) for page in source["pages"])
            suffix = ""
            if source["lowest_confidence"] < 0.85:
                suffix = (
                    f"  [text recognised from a scan at "
                    f"{source['lowest_confidence']:.0%} confidence — verify against the original]"
                )
            lines.append(
                f"{source['title']} — page{'s' if len(source['pages']) > 1 else ''} {pages}{suffix}"
            )
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_by": self.generated_by,
            "generated_at": self.generated_at,
            "models": self.models,
            "tools_used": self.tools_used,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "sha256": self.sha256,
            "sources": [{**source, "pages": list(source["pages"])} for source in self.sources],
            "has_uncertain_sources": self.has_uncertain_sources,
            "notes": self.notes,
        }
