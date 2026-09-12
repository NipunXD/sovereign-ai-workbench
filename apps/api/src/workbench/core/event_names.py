"""The trace event vocabulary.

Separate from :mod:`workbench.core.events`, which owns the pub/sub machinery —
these are just the names that travel through it.

They live in ``core`` because the agent *emits* them and the API *serialises*
them, and the layering forbids the agent from importing the API. The names are
the contract between those two and with the frontend, which switches on these
exact strings, so they belong below both rather than inside either.

``api.sse`` re-exports the class, so the transport still reads as one module.
"""

from __future__ import annotations


class EventName:
    """The trace vocabulary. The frontend switches on these exact strings."""

    RUN_STARTED = "run_started"
    ROUTE_DECISION = "route_decision"
    PLAN_CREATED = "plan_created"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    RETRIEVAL_RESULT = "retrieval_result"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    #: Answer text. One per token-ish fragment.
    TOKEN = "token"  # noqa: S105 — an SSE event name, not a credential
    #: The model's internal monologue, on a separate channel so it can never be
    #: mistaken for the answer.
    REASONING = "reasoning"
    #: The complete answer with citation markers resolved to [1], [2].
    #: Citation markers span sentences, so they can only be rewritten once the
    #: whole text exists — the UI renders tokens live and swaps this in at the end.
    ANSWER = "answer"
    CITATION = "citation"
    ARTIFACT_CREATED = "artifact_created"
    VALIDATION = "validation"
    APPROVAL_REQUIRED = "approval_required"
    #: Something the run could not do, and why, stated for the user rather
    #: than for a log. Not an error: the run continues and answers.
    LIMITATION = "limitation"
    ERROR = "error"
    #: The request was refused by policy before any model ran. Distinct from a
    #: refusal to answer: nothing was searched and nothing was generated.
    POLICY_REFUSED = "policy_refused"
    RUN_FINISHED = "run_finished"
