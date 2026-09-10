"""Access control: the confidentiality guarantee.

If any of these fail, a user can reach a document above their clearance — which
is the single worst outcome for a system whose premise is that confidential
refinery data stays controlled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.core.classification import Classification
from workbench.core.errors import AuthorizationError
from workbench.security.rbac import AccessFilter, Principal, RbacConfig

@pytest.fixture
def rbac(rbac_config_path: Path) -> RbacConfig:
    return RbacConfig.load(rbac_config_path)


def principal(rbac: RbacConfig, role: str, **kwargs: object) -> Principal:
    return Principal(
        user_id=f"user-{role}",
        username=role,
        roles=frozenset({role}),
        permissions=rbac.permissions_for({role}),
        clearance=rbac.clearance_for({role}),
        **kwargs,  # type: ignore[arg-type]
    )


# --- the classification ladder ----------------------------------------------
def test_classification_order_is_ascending() -> None:
    assert Classification.rank(Classification.PUBLIC) < Classification.rank(Classification.INTERNAL)
    assert Classification.rank(Classification.INTERNAL) < Classification.rank(
        Classification.CONFIDENTIAL
    )
    assert Classification.rank(Classification.CONFIDENTIAL) < Classification.rank(
        Classification.RESTRICTED
    )


def test_unknown_classification_fails_closed() -> None:
    """A typo must hide a document, never expose one."""
    assert Classification.rank("totally-made-up") > Classification.rank(Classification.RESTRICTED)
    assert Classification.outranks("totally-made-up", Classification.RESTRICTED)
    assert "totally-made-up" not in Classification.at_or_below(Classification.RESTRICTED)


# --- per-role visibility -----------------------------------------------------
@pytest.mark.parametrize(
    ("role", "classification", "expected"),
    [
        ("viewer", Classification.PUBLIC, True),
        ("viewer", Classification.INTERNAL, True),
        ("viewer", Classification.CONFIDENTIAL, False),
        ("viewer", Classification.RESTRICTED, False),
        ("engineer", Classification.CONFIDENTIAL, True),
        ("engineer", Classification.RESTRICTED, False),
        ("senior_engineer", Classification.RESTRICTED, True),
        ("auditor", Classification.CONFIDENTIAL, False),
        ("admin", Classification.RESTRICTED, True),
    ],
)
def test_read_matrix(rbac: RbacConfig, role: str, classification: str, expected: bool) -> None:
    assert principal(rbac, role).may_read(classification) is expected


def test_auditor_can_read_audit_but_not_generate(rbac: RbacConfig) -> None:
    """Oversight must not become a way to act.

    An auditor sees everything that happened without being able to produce
    artifacts or run code, which is what makes their read access safe to grant.
    """
    auditor = principal(rbac, "auditor")
    assert auditor.has("audit:read")
    assert not auditor.has("artifact:generate")
    assert not auditor.has("tool:code_exec")


def test_approver_cannot_generate_what_they_approve(rbac: RbacConfig) -> None:
    """Separation of duties: the approver is a second pair of eyes."""
    approver = principal(rbac, "approver")
    assert approver.has("artifact:approve")
    assert not approver.has("artifact:generate")


def test_only_senior_engineer_and_admin_may_execute_code(rbac: RbacConfig) -> None:
    for role in ("viewer", "engineer", "approver", "auditor"):
        assert not principal(rbac, role).has("tool:code_exec"), role
    for role in ("senior_engineer", "admin"):
        assert principal(rbac, role).has("tool:code_exec"), role


def test_require_raises_for_missing_permission(rbac: RbacConfig) -> None:
    viewer = principal(rbac, "viewer")
    with pytest.raises(AuthorizationError) as excinfo:
        viewer.require("artifact:generate")
    assert excinfo.value.extra["required_permission"] == "artifact:generate"


def test_admin_wildcard_expands_to_every_permission(rbac: RbacConfig) -> None:
    """`admin:*` must not silently miss permissions added later."""
    granted = rbac.permissions_for({"admin"})
    assert "eval:run" in granted
    assert "model:manage" in granted


# --- department scoping ------------------------------------------------------
def test_department_restriction_applies(rbac: RbacConfig) -> None:
    engineer = principal(rbac, "engineer", departments=frozenset({"inspection"}))
    assert engineer.may_read(Classification.INTERNAL, ["inspection"])
    assert not engineer.may_read(Classification.INTERNAL, ["hr"])


def test_document_with_no_department_is_plant_wide(rbac: RbacConfig) -> None:
    engineer = principal(rbac, "engineer", departments=frozenset({"inspection"}))
    assert engineer.may_read(Classification.INTERNAL, [])


# --- the retrieval filter ----------------------------------------------------
def test_filter_never_includes_classifications_above_clearance(rbac: RbacConfig) -> None:
    """This clause is injected into the vector search itself.

    If a classification above the principal's clearance ever appears here, the
    restricted chunk becomes a candidate inside the ANN search — and scores and
    result counts leak information even when text is withheld.
    """
    for role in ("viewer", "engineer", "senior_engineer", "auditor", "approver", "admin"):
        person = principal(rbac, role)
        allowed = AccessFilter.for_principal(person).classifications
        for label in allowed:
            assert not Classification.outranks(label, person.clearance), (role, label)


def test_viewer_filter_excludes_confidential(rbac: RbacConfig) -> None:
    qdrant = AccessFilter.for_principal(principal(rbac, "viewer")).to_qdrant()
    allowed = qdrant["must"][0]["match"]["any"]
    assert Classification.CONFIDENTIAL not in allowed
    assert Classification.RESTRICTED not in allowed


def test_filter_excludes_named_documents(rbac: RbacConfig) -> None:
    access = AccessFilter.for_principal(principal(rbac, "admin"), exclude_doc_ids=["doc_1"])
    assert access.to_qdrant()["must_not"][0]["key"] == "doc_id"


def test_anonymous_principal_has_nothing() -> None:
    from workbench.security.rbac import ANONYMOUS

    assert not ANONYMOUS.permissions
    assert not ANONYMOUS.may_read(Classification.INTERNAL)


# --- config integrity --------------------------------------------------------
def test_config_rejects_undefined_permission(tmp_path: Path) -> None:
    """A typo in rbac.yaml must fail at startup, not as a silent denial."""
    from workbench.core.errors import ConfigurationError

    bad = tmp_path / "rbac.yaml"
    bad.write_text(
        "permissions:\n"
        "  - {code: 'chat:use', description: ''}\n"
        "roles:\n"
        "  tester:\n"
        "    clearance: internal\n"
        "    permissions: ['chat:use', 'doc:raed']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="doc:raed"):
        RbacConfig.load(bad)


def test_unknown_clearance_grants_nothing() -> None:
    """The mirror of the document rule, and easy to get backwards.

    An unrecognised *document* label must be treated as maximally sensitive; an
    unrecognised *clearance* must be treated as no clearance. Sharing one
    ranking function between the two would hand an anonymous caller the highest
    clearance in the system.
    """
    stranger = Principal(user_id="x", username="x", clearance="not-a-real-level")
    for label in Classification.ORDER:
        assert not stranger.may_read(label), label
    assert Classification.at_or_below("not-a-real-level") == []


def test_principal_defaults_to_lowest_clearance() -> None:
    """Building a Principal without a clearance must not grant internal access."""
    assert Principal(user_id="x", username="x").clearance == Classification.PUBLIC
