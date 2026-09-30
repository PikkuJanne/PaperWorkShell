"""PWS-0408 generic pilot through the real desktop backend and restricted workers."""

from __future__ import annotations

import hashlib
import os
import zipfile
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.package_workflow import PackageWorkflowError, PackageWorkflowErrorCode
from paperworkshell.app.portal_constraints import ConstraintDraftInput
from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tests.integration.test_flattened_pdf_export_worker_windows import _stage_python_closure
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0408 pilot uses the supported Windows worker and storage boundary"
)

_SOURCE = Path(__file__).resolve().parents[1] / "fixtures/synthetic/v1/pdf/born-digital.pdf"


def _configure_staged_runtime(
    monkeypatch: pytest.MonkeyPatch,
    staged: WorkerTestResources,
) -> None:
    # The existing flattened-worker harness stages the exact locked dependency
    # closure. The complete backend then uses its ordinary production factories.
    _stage_python_closure(staged)
    monkeypatch.setattr(
        desktop,
        "_worker_runtime_resources",
        lambda: (
            staged.python_executable,
            None,
            staged.source_root / "paperworkshell/worker/host_bootstrap.py",
            (staged.runtime_root,),
            (staged.source_root,),
            staged.source_root,
        ),
    )
    monkeypatch.delenv("PWS_TESSERACT_EXECUTABLE", raising=False)
    monkeypatch.delenv("PWS_TESSDATA_ROOT", raising=False)


def _assign_pages(backend: OrganizerBackend, *, first: int, stop: int) -> None:
    editor = backend.begin_checklist()
    for index in range(first, stop):
        editor = editor.add_pages(
            editor.requirements[index].requirement_id,
            (editor.pages[index].page_id,),
        )
        editor = editor.confirm_assignment(editor.assignments[-1].assignment_id)
    backend.apply_checklist(editor)


def _configure_mode(backend: OrganizerBackend, mode: PortalOutputMode) -> None:
    editor = backend.begin_portal_constraints()
    constraints = editor.constraints
    stems = (
        ("application-form", "identity-evidence", "address-evidence", "supporting-evidence")
        if mode is PortalOutputMode.SEPARATE_PDFS
        else ("generic-application-packet",)
    )
    backend.apply_portal_constraints(
        editor.replace(
            ConstraintDraftInput(
                (mode,),
                constraints.encryption_policy,
                constraints.maximum_file_count,
                constraints.maximum_total_bytes,
                constraints.maximum_file_bytes,
                constraints.maximum_pages_per_file,
                stems,
            )
        )
    )


def _assert_reopened_outputs(summary: PackageSummary, destination: Path) -> None:
    assert summary.status is PackageSummaryStatus.COMMITTED
    assert all(value.state is PackageSummaryOutputState.COMMITTED for value in summary.outputs)
    top_level = tuple(value for value in summary.outputs if not value.archive_member)
    assert summary.saved_file_count == len(top_level)
    for output in top_level:
        artifact = destination / output.filename
        assert artifact.is_file() and artifact.stat().st_size == output.byte_size
        if artifact.suffix == ".zip":
            members = tuple(value for value in summary.outputs if value.archive_member)
            with zipfile.ZipFile(artifact) as archive:
                assert tuple(archive.namelist()) == tuple(value.filename for value in members)
                assert archive.testzip() is None
                for member in members:
                    payload = archive.read(member.filename)
                    assert len(payload) == member.byte_size
                    with pikepdf.Pdf.open(BytesIO(payload)) as pdf:
                        assert len(pdf.pages) == member.page_count
        else:
            with pikepdf.Pdf.open(artifact) as pdf:
                assert len(pdf.pages) == output.page_count
    pdf_outputs = tuple(value for value in summary.outputs if value.filename.endswith(".pdf"))
    assert sum(value.page_count for value in pdf_outputs) == 4
    assert all(any("render" in check.casefold() for check in item.checks) for item in pdf_outputs)


def test_generic_pilot_import_review_export_all_modes_and_visible_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configure_staged_runtime(monkeypatch, worker_test_resources)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(policy)
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=desktop._workspace_quota(policy),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    raw = tmp_path / "synthetic-input.pdf"
    with pikepdf.Pdf.open(_SOURCE) as source, pikepdf.Pdf.new() as packet:
        for _ in range(4):
            packet.pages.append(source.pages[0])
        packet.save(raw)
    source_digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    backend = desktop._build_backend(manager, resources=resources)
    try:
        state = backend.create_case()
        assert state.case_id is not None
        imported = backend.import_source(raw, "PWS-0408 PRIVATE synthetic source name.pdf")
        assert len(imported.state.pages) == 4
        constraints = backend.begin_portal_constraints().restore_generic_defaults()
        backend.apply_portal_constraints(constraints)

        # Complete the first three categories, then exercise the missing-item
        # review and keyboard-callable recovery path before filling the fourth.
        _assign_pages(backend, first=0, stop=3)
        missing = backend.begin_package_review()
        assert not missing.export_allowed
        assert missing.summary.status is PackageSummaryStatus.NEEDS_REVIEW
        assert missing.summary.unassigned_page_count == 1
        with pytest.raises(PackageWorkflowError) as blocked:
            backend.export_package(
                tmp_path,
                review_id=missing.review_id,
                signed_source_confirmed=False,
                human_review_confirmed=True,
            )
        assert blocked.value.code is PackageWorkflowErrorCode.BLOCKED
        backend.cancel_package_review()
        _assign_pages(backend, first=3, stop=4)

        expected_counts = {
            PortalOutputMode.COMBINED_PDF: 1,
            PortalOutputMode.SEPARATE_PDFS: 4,
            PortalOutputMode.ZIP: 1,
        }
        for mode, file_count in expected_counts.items():
            _configure_mode(backend, mode)
            destination = tmp_path / mode.value
            destination.mkdir()
            review = backend.begin_package_review(mode)
            assert review.export_allowed and review.human_review_required
            # Production package review now acquires current source inspection.
            # Remaining advisory checks still require explicit human review.
            assert "inspection is unavailable" not in review.summary.to_plain_text()
            assert len(review.summary.review_items) >= 3
            with pytest.raises(PackageWorkflowError) as unconfirmed:
                backend.export_package(
                    destination,
                    review_id=review.review_id,
                    signed_source_confirmed=False,
                    human_review_confirmed=False,
                )
            assert unconfirmed.value.code is PackageWorkflowErrorCode.REVIEW_REQUIRED
            assert tuple(destination.iterdir()) == ()

            with pytest.raises(PackageWorkflowError) as cancelled:
                backend.export_package(
                    destination,
                    review_id=review.review_id,
                    signed_source_confirmed=False,
                    human_review_confirmed=True,
                    cancellation=lambda: True,
                )
            assert cancelled.value.code is PackageWorkflowErrorCode.CANCELLED
            assert tuple(destination.iterdir()) == ()

            progress: list[tuple[int, int]] = []

            def record_progress(
                done: int, total: int, sink: list[tuple[int, int]] = progress
            ) -> None:
                sink.append((done, total))

            summary = backend.export_package(
                destination,
                review_id=review.review_id,
                signed_source_confirmed=False,
                human_review_confirmed=True,
                progress=record_progress,
            )
            _assert_reopened_outputs(summary, destination)
            assert summary.file_count == file_count
            assert progress[-1] == (4, 4)
            assert len(summary.review_items) >= len(review.summary.review_items)
            assert "PWS-0408 PRIVATE" not in summary.to_plain_text()
            assert str(state.case_id) not in summary.to_plain_text()
            summary_target = destination / "package-summary.txt"
            backend.save_package_summary(summary_target)
            assert summary_target.read_bytes() == summary.to_plain_text().encode("utf-8")
            assert len(tuple(destination.iterdir())) == file_count + 1
            backend.cancel_package_review()

        # A second attempt at the same names must report no new publication,
        # preserving every already verified derivative and its visible summary.
        prior_folder = tmp_path / PortalOutputMode.COMBINED_PDF.value
        before = {path.name: path.read_bytes() for path in prior_folder.iterdir()}
        _configure_mode(backend, PortalOutputMode.COMBINED_PDF)
        repeat_review = backend.begin_package_review(PortalOutputMode.COMBINED_PDF)
        repeat = backend.export_package(
            prior_folder,
            review_id=repeat_review.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
        assert repeat.status is PackageSummaryStatus.NOT_COMMITTED
        assert repeat.saved_file_count == 0
        assert {path.name: path.read_bytes() for path in prior_folder.iterdir()} == before
        backend.cancel_package_review()

        current = WindowsManifestRepository(manager).load(state.case_id).snapshot
        originals = manager.layout(state.case_id).originals
        assert tuple(originals.iterdir())
        assert all(
            hashlib.sha256(path.read_bytes()).hexdigest() == source_digest
            for path in originals.iterdir()
        )
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == source_digest
        assert current.document["export_history"] == {
            "storage_name": "export-history.json",
            "entries": [],
        }
        assert resources.heavy_operations.snapshot().active_operations == 0
        assert "PWS-0408 PRIVATE" not in caplog.text
    finally:
        backend.shutdown()
