"""Privacy and inert-execution regressions for the PWS-0404 preflight boundary."""

from __future__ import annotations

import ast
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import (
    ChecklistDefinition,
    ChecklistProjection,
    ChecklistRequirement,
    ChecklistRequirementProjection,
    ChecklistStatus,
    RequirementCountUnit,
    TemplateBinding,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.preflight.engine import PreflightEngine
from paperworkshell.preflight.facts import (
    CasePreflightFacts,
    CaseSourcePreflightFact,
    ChecklistFactAvailability,
    ChecklistPreflightFacts,
    OutputItemPreflightFact,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
    QualityFactAvailability,
    QualityPreflightFacts,
    SourceInspectionAvailability,
    SourceInspectionFact,
    SourceInspectionsPreflightFacts,
    TemplateFactAvailability,
    TemplateNoticeFact,
    TemplateNoticeKind,
    TemplatePreflightFacts,
)
from paperworkshell.preflight.models import (
    PreflightAuthority,
    PreflightDependency,
    PreflightError,
    PreflightErrorCode,
    PreflightFactValue,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightInput,
    PreflightRemediationAction,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
    PreflightSeverity,
)
from paperworkshell.preflight.rules import PreflightRuleContext, PreflightRuleDescriptor

_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "paperworkshell" / "preflight"
_CASE_ID = UUID("94040000-0000-4000-8000-000000000001")
_SOURCE_ID = UUID("94040000-0000-4000-8000-000000000002")
_PAGE_ID = UUID("94040000-0000-4000-8000-000000000003")
_MANIFEST_SHA256 = "deadc0de" * 8
_TEMPLATE_SHA256 = "cab005e5" * 8
_SOURCE_SHA256 = "fee1dead" * 8
_PROFILE_SHA256 = "baddcafe" * 8
_RAW_EXCEPTION_CANARY = r"RAW-PREFLIGHT-ERROR-CANARY C:\private\applicant.pdf"
_PRIVATE_FACT_CANARY = "APPLICANT-OCR-TEXT-CANARY-0404"
_PRIVATE_FINDING_MESSAGE = r"PRIVATE FINDING MESSAGE C:\records\applicant.pdf"
_PRIVATE_REMEDIATION = "PRIVATE FORM VALUE REMEDIATION CANARY"
_PRIVATE_TEMPLATE_MESSAGE = "PRIVATE TEMPLATE NOTICE CANARY"
_FINDING_CODE = "SYNTHETIC_PRIVACY_REVIEW"

type _RuleMode = Literal["finding", "raw", "corrupted_exact", "subclass"]


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateFacts(PreflightFactValue):
    case_id: UUID
    manifest_revision: int
    private_text: str = field(repr=False)


class _HostilePreflightError(PreflightError):
    """Adapter-owned subclass that must not cross the engine boundary."""


class _PrivacyRule:
    __slots__ = ("_descriptor", "_mode")

    def __init__(self, descriptor: PreflightRuleDescriptor, mode: _RuleMode) -> None:
        self._descriptor = descriptor
        self._mode = mode

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return self._descriptor

    def evaluate(
        self,
        _context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        if self._mode == "raw":
            raise RuntimeError(_RAW_EXCEPTION_CANARY)
        if self._mode == "corrupted_exact":
            error = PreflightError(
                PreflightErrorCode.DEPENDENCY_UNDECLARED,
                "preflight.context_dependency",
            )
            error.safe_message = _RAW_EXCEPTION_CANARY
            error.args = (_RAW_EXCEPTION_CANARY,)
            raise error
        if self._mode == "subclass":
            error = _HostilePreflightError(
                PreflightErrorCode.DEPENDENCY_UNDECLARED,
                "preflight.context_dependency",
            )
            error.safe_message = _RAW_EXCEPTION_CANARY
            error.args = (_RAW_EXCEPTION_CANARY,)
            raise error
        return (
            PreflightFindingTarget(
                _FINDING_CODE,
                PreflightScope(PreflightScopeKind.CASE, _CASE_ID),
            ),
        )


def _authority() -> PreflightAuthority:
    return PreflightAuthority(
        case_id=_CASE_ID,
        manifest_revision=7,
        manifest_sha256=_MANIFEST_SHA256,
        template_binding=TemplateBinding(
            "privacy-template",
            "1.0.0",
            _TEMPLATE_SHA256,
        ),
        scope_catalog=PreflightScopeCatalog(
            case_id=_CASE_ID,
            scopes=(PreflightScope(PreflightScopeKind.CASE, _CASE_ID),),
        ),
    )


def _input() -> PreflightInput:
    return PreflightInput(
        authority=_authority(),
        dependency=PreflightDependency.CASE,
        value=_PrivateFacts(_CASE_ID, 7, _PRIVATE_FACT_CANARY),
    )


def _descriptor(
    *,
    message: str = "A case item needs review.",
    remediation: str = "Review the case item, then run preflight again.",
) -> PreflightRuleDescriptor:
    return PreflightRuleDescriptor(
        rule_id="synthetic.privacy",
        rule_version=1,
        dependencies=(PreflightDependency.CASE,),
        finding_definitions=(
            PreflightFindingDefinition(
                code=_FINDING_CODE,
                severity=PreflightSeverity.WARNING,
                remediation_action=PreflightRemediationAction.REVIEW_CASE,
                message=message,
                remediation=remediation,
            ),
        ),
    )


def _render_error(error: PreflightError) -> str:
    return "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            "".join(traceback.format_exception(error)),
        )
    )


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (
        ("raw", PreflightErrorCode.RULE_FAILED),
        ("corrupted_exact", PreflightErrorCode.DEPENDENCY_UNDECLARED),
        ("subclass", PreflightErrorCode.RULE_FAILED),
    ),
)
def test_hostile_rule_failures_are_rebuilt_without_private_detail_or_chains(
    mode: _RuleMode,
    expected_code: PreflightErrorCode,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    engine = PreflightEngine((_PrivacyRule(_descriptor(), mode),))

    with pytest.raises(PreflightError) as captured:
        engine.evaluate((_input(),))

    terminal = capsys.readouterr()
    rendered = f"{_render_error(captured.value)}\n{caplog.text}\n{terminal.out}\n{terminal.err}"
    assert captured.value.code is expected_code
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert _RAW_EXCEPTION_CANARY not in rendered
    assert "applicant.pdf" not in rendered
    assert caplog.records == []


def test_successful_and_failed_engine_evaluation_emit_no_logs_or_console_output(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    successful = PreflightEngine((_PrivacyRule(_descriptor(), "finding"),))
    failed = PreflightEngine((_PrivacyRule(_descriptor(), "raw"),))

    result = successful.evaluate((_input(),))
    with pytest.raises(PreflightError):
        failed.evaluate((_input(),))

    terminal = capsys.readouterr()
    assert len(result.report.findings) == 1
    assert caplog.records == []
    assert caplog.text == ""
    assert terminal.out == ""
    assert terminal.err == ""


def test_public_preflight_reprs_hide_messages_digests_and_private_fact_values() -> None:
    authority = _authority()
    selected_input = _input()
    definition = _descriptor(
        message=_PRIVATE_FINDING_MESSAGE,
        remediation=_PRIVATE_REMEDIATION,
    )
    engine = PreflightEngine((_PrivacyRule(definition, "finding"),))
    evaluation = engine.evaluate((selected_input,))
    binding = TemplateBinding("privacy-template", "1.0.0", _TEMPLATE_SHA256)
    notice = TemplateNoticeFact(
        "privacy-notice",
        TemplateNoticeKind.NEEDS_REVIEW,
        1,
        _PRIVATE_TEMPLATE_MESSAGE,
        "privacy-requirement",
    )
    case_facts = CasePreflightFacts(
        _CASE_ID,
        7,
        binding,
        (
            CaseSourcePreflightFact(
                _SOURCE_ID,
                1,
                DetectedMediaType.PDF,
                _SOURCE_SHA256,
            ),
        ),
        (_PAGE_ID,),
    )
    template_facts = TemplatePreflightFacts(
        _CASE_ID,
        7,
        TemplateFactAvailability.CURRENT,
        binding,
        (notice,),
    )
    requirement = ChecklistRequirement(
        "privacy-requirement",
        RequirementCountUnit.DOCUMENTS,
        1,
        1,
    )
    checklist_facts = ChecklistPreflightFacts(
        _CASE_ID,
        7,
        ChecklistFactAvailability.CURRENT,
        ChecklistDefinition(binding, (requirement,)),
        ChecklistProjection(
            (
                ChecklistRequirementProjection(
                    requirement,
                    (),
                    0,
                    ChecklistStatus.MISSING,
                ),
            ),
            (_PAGE_ID,),
            (_PAGE_ID,),
            (),
        ),
    )
    source = SourceInspectionFact(
        _SOURCE_ID,
        1,
        DetectedMediaType.PDF,
        SourceInspectionAvailability.UNAVAILABLE,
        _SOURCE_SHA256,
    )
    source_facts = SourceInspectionsPreflightFacts(_CASE_ID, 7, (source,))
    quality_facts = QualityPreflightFacts(
        _CASE_ID,
        7,
        QualityFactAvailability.CURRENT,
        (_PAGE_ID,),
        (),
        "scan-quality-v1",
        _PROFILE_SHA256,
    )
    output_item = OutputItemPreflightFact("packet-output", 1, (_PAGE_ID,), 987_654_321)
    output_facts = OutputPlanPreflightFacts(
        _CASE_ID,
        7,
        OutputPlanAvailability.CURRENT,
        "combined-pdf",
        (output_item,),
    )
    context = PreflightRuleContext(
        {PreflightDependency.CASE: selected_input},
        (PreflightDependency.CASE,),
    )
    rendered = "\n".join(
        repr(value)
        for value in (
            authority,
            authority.scope_catalog,
            selected_input,
            selected_input.value,
            definition,
            definition.finding_definitions[0],
            engine,
            evaluation,
            evaluation.report,
            evaluation.report.findings[0],
            context,
            binding,
            notice,
            case_facts,
            template_facts,
            checklist_facts,
            source,
            source_facts,
            quality_facts,
            output_item,
            output_facts,
        )
    )
    prohibited = (
        _PRIVATE_FACT_CANARY,
        _PRIVATE_FINDING_MESSAGE,
        _PRIVATE_REMEDIATION,
        _PRIVATE_TEMPLATE_MESSAGE,
        _MANIFEST_SHA256,
        _TEMPLATE_SHA256,
        _SOURCE_SHA256,
        _PROFILE_SHA256,
        authority.sha256,
        selected_input.revision_sha256,
        definition.sha256,
        engine.engine_sha256,
        str(_SOURCE_ID),
        str(_PAGE_ID),
        "987654321",
    )
    for value in prohibited:
        assert value not in rendered


def test_preflight_package_has_no_logging_execution_or_external_io_surface() -> None:
    prohibited_import_roots = {
        "aiohttp",
        "asyncio",
        "builtins",
        "ctypes",
        "ftplib",
        "glob",
        "http",
        "importlib",
        "logging",
        "locale",
        "multiprocessing",
        "os",
        "pathlib",
        "platform",
        "random",
        "requests",
        "runpy",
        "secrets",
        "shutil",
        "smtplib",
        "socket",
        "ssl",
        "subprocess",
        "sys",
        "tempfile",
        "time",
        "traceback",
        "urllib",
        "warnings",
        "webbrowser",
        "winreg",
    }
    prohibited_call_names = {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "input",
        "open",
        "print",
    }
    prohibited_method_names = {
        "Popen",
        "accept",
        "check_call",
        "check_output",
        "connect",
        "connect_ex",
        "create_connection",
        "critical",
        "debug",
        "error",
        "exception",
        "exec_module",
        "find_module",
        "find_spec",
        "fork",
        "getLogger",
        "glob",
        "info",
        "iterdir",
        "listdir",
        "listen",
        "log",
        "makedirs",
        "mkdir",
        "module_from_spec",
        "open",
        "read_bytes",
        "read_text",
        "recv",
        "recvfrom",
        "recv_into",
        "removedirs",
        "rename",
        "rglob",
        "rmdir",
        "run",
        "scandir",
        "send",
        "sendall",
        "sendto",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "startfile",
        "system",
        "unlink",
        "urlopen",
        "urlretrieve",
        "walk",
        "warning",
        "write_bytes",
        "write_text",
    }

    for source_path in sorted(_PACKAGE.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path.name)
        imported_roots: set[str] = set()
        called_names: set[str] = set()
        called_methods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_methods.add(node.func.attr)
        assert imported_roots.isdisjoint(prohibited_import_roots), source_path.name
        assert called_names.isdisjoint(prohibited_call_names), source_path.name
        assert called_methods.isdisjoint(prohibited_method_names), source_path.name
