"""PWS-0408 adversarial plain-text summary and privacy regression tests."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import cast

import pytest

from paperworkshell.domain.package_summary import (
    MAX_SUMMARY_LINE_CODEPOINTS,
    MAX_SUMMARY_ROWS,
    PackageSummary,
    PackageSummaryCategory,
    PackageSummaryError,
    PackageSummaryErrorCode,
    PackageSummaryReviewItem,
    PackageSummaryReviewKind,
    PackageSummaryStatus,
)
from paperworkshell.export.package_summary import build_package_summary
from paperworkshell.preflight.engine import PreflightEngine
from paperworkshell.preflight.models import (
    PreflightDependency,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightInput,
    PreflightRemediationAction,
    PreflightScope,
    PreflightScopeKind,
    PreflightSeverity,
)
from paperworkshell.preflight.rules import PreflightRuleContext, PreflightRuleDescriptor
from paperworkshell.templates.models import TemplateSnapshot
from tests.unit.test_package_summary import CASE_ID, summary_inputs, summary_outcome

PRIVATE_SOURCE = "SYNTHETIC_PRIVATE_source_name_441.pdf"
PRIVATE_TEXT = "SYNTHETIC_PRIVATE_ocr_form_metadata_442"


def _template_with_label(label: str) -> TemplateSnapshot:
    template = summary_inputs().template
    document = template.document
    requirements = cast(list[dict[str, object]], document["requirements"])
    requirements[0]["title"] = label
    return TemplateSnapshot(
        key=template.key,
        byte_size=template.byte_size,
        sha256="e" * 64,
        freshness=template.freshness,
        presentation=template.presentation,
        document=document,
    )


def test_names_and_user_visible_labels_never_enter_routine_representations(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inputs = summary_inputs(
        source_name=PRIVATE_SOURCE, template=_template_with_label("SYNTHETIC_VISIBLE_label_443")
    )
    summary = inputs.build(summary_outcome(inputs))
    values = (summary, *summary.outputs, *summary.categories, *summary.review_items)
    with caplog.at_level(logging.INFO):
        for value in values:
            logging.getLogger("paperworkshell.summary.test").info("%r", value)
    assert PRIVATE_SOURCE not in summary.to_plain_text()
    assert "SYNTHETIC_VISIBLE_label_443" in summary.to_plain_text()
    assert "SYNTHETIC_VISIBLE_label_443" not in caplog.text
    assert "application-form.pdf" not in caplog.text
    assert PRIVATE_SOURCE not in caplog.text
    assert all(
        not hasattr(summary, attribute)
        for attribute in (
            "snapshot",
            "case_id",
            "authority",
            "plan",
            "outcome",
            "sha256",
            "source",
            "metadata",
        )
    )


def test_markup_remains_literal_plain_text_with_no_hidden_payload() -> None:
    label = '<a href="https://invalid.example/">Synthetic category</a>'
    summary = summary_inputs(template=_template_with_label(label)).build()
    text = summary.to_plain_text()
    assert label in text
    assert "&lt;a" not in text
    assert "<!--" not in text
    assert not hasattr(summary, "html")
    assert text.endswith("\n")


@pytest.mark.parametrize(
    "hostile",
    (
        "Hidden\nPackage saved.",
        "Hidden\rName",
        "Hidden\tName",
        "Hidden\x00Name",
        "Hidden\u202eName",
        "Hidden\u2066Name",
        "Hidden\u200bName",
        "Hidden\u2028Name",
        "Hidden\u2029Name",
        "Hidden\ud800Name",
    ),
)
def test_invisible_control_and_multiline_injection_fails_closed(hostile: str) -> None:
    inputs = summary_inputs(template=_template_with_label(hostile))
    with pytest.raises(PackageSummaryError) as error:
        inputs.build()
    assert error.value.code is PackageSummaryErrorCode.MODEL_INVALID
    assert "Hidden" not in repr(error.value)
    assert "Hidden" not in str(error.value)
    assert "Hidden" not in repr(error.value.args)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_line_and_row_capacity_fail_explicitly_instead_of_silent_warning_truncation() -> None:
    with pytest.raises(PackageSummaryError) as error:
        PackageSummaryReviewItem(
            PackageSummaryReviewKind.WARNING,
            "Package",
            "x" * (MAX_SUMMARY_LINE_CODEPOINTS + 1),
            "Review.",
        )
    assert error.value.code is PackageSummaryErrorCode.LIMIT_EXCEEDED
    review = PackageSummaryReviewItem(
        PackageSummaryReviewKind.WARNING, "Package", "Review required.", "Review."
    )
    with pytest.raises(PackageSummaryError) as error:
        PackageSummary(
            PackageSummaryStatus.NEEDS_REVIEW, review_items=(review,) * (MAX_SUMMARY_ROWS + 1)
        )
    assert error.value.code is PackageSummaryErrorCode.LIMIT_EXCEEDED


def test_direct_model_representations_and_errors_are_content_free() -> None:
    summary = summary_inputs().build()
    category: PackageSummaryCategory = replace(summary.categories[0], label=PRIVATE_TEXT)
    assert PRIVATE_TEXT not in repr(category)
    assert PRIVATE_TEXT not in str(category)
    error = PackageSummaryError(cast(PackageSummaryErrorCode, PRIVATE_TEXT))
    assert PRIVATE_TEXT not in repr(error) + str(error) + repr(error.args)


class _UntrustedWordingRule:
    descriptor = PreflightRuleDescriptor(
        "custom.review",
        1,
        (PreflightDependency.OUTPUT_PLAN,),
        (
            PreflightFindingDefinition(
                "PRIVATE_CUSTOM_DIAGNOSTIC",
                PreflightSeverity.WARNING,
                PreflightRemediationAction.REVIEW_CASE,
                PRIVATE_TEXT,
                PRIVATE_TEXT,
            ),
        ),
    )

    def evaluate(self, context: PreflightRuleContext, /) -> tuple[PreflightFindingTarget, ...]:
        del context
        return (
            PreflightFindingTarget(
                "PRIVATE_CUSTOM_DIAGNOSTIC", PreflightScope(PreflightScopeKind.CASE, CASE_ID)
            ),
        )


def test_unregistered_rule_wording_is_not_copied_as_summary_diagnostics() -> None:
    inputs = summary_inputs()
    assert inputs.plan is not None
    authority = inputs.preflight.authority
    # Canonical case facts are required to bind canonical output facts, and may
    # be present without a consuming rule. Read them from the same saved inputs.
    from paperworkshell.domain.import_precheck import DetectedMediaType
    from paperworkshell.preflight.facts import CasePreflightFacts, CaseSourcePreflightFact
    from tests.unit.test_package_summary import PAGE_IDS, SOURCE_ID

    case = CasePreflightFacts(
        CASE_ID,
        7,
        inputs.definition.binding,
        (CaseSourcePreflightFact(SOURCE_ID, 1, DetectedMediaType.PDF, "a" * 64),),
        PAGE_IDS,
    )
    preflight = PreflightEngine((_UntrustedWordingRule(),)).evaluate(
        (
            PreflightInput(authority, PreflightDependency.CASE, case),
            PreflightInput(authority, PreflightDependency.OUTPUT_PLAN, inputs.plan.output_facts),
        )
    )
    summary = build_package_summary(
        inputs.snapshot, inputs.template, inputs.definition, preflight, plan=inputs.plan
    )
    text = summary.to_plain_text()
    assert PRIVATE_TEXT not in text
    assert "PRIVATE_CUSTOM_DIAGNOSTIC" not in text
    assert "An additional local check requires review." in text


def test_prohibited_claims_are_not_present_as_positive_statements() -> None:
    inputs = summary_inputs()
    text = inputs.build(summary_outcome(inputs)).to_plain_text().lower()
    for phrase in (
        "everything is valid",
        "portal acceptance guaranteed",
        "legally sufficient",
        "signature is valid",
        "ocr is correct",
        "pdf/ua conformant",
        "securely erased",
        "permanently removed",
        "digitally signed by paperworkshell",
    ):
        assert phrase not in text
    assert "do not establish portal acceptance" in text
