"""Fixed installed files for the Windows x64 ordinary PDF export worker.

This inventory covers the reviewed embedded CPython runtime, PDF adapters,
decoder dependencies, and qpdf checker used by the structural PDF export
worker. It admits the tested shipped PaperWorkShell package inventory and
only the listed external package files. Changes to the installed runtime
or worker dependencies require reviewing this list and repeating the native
export and containment checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

_PDF_EXPORT_RUNTIME_FILES: Final = {
    # Embedded CPython and the fixed Windows runtime libraries.
    ".": (
        "_asyncio.pyd _bz2.pyd _ctypes.pyd _decimal.pyd _elementtree.pyd _hashlib.pyd "
        "_lzma.pyd _multiprocessing.pyd _overlapped.pyd _queue.pyd _remote_debugging.pyd "
        "_socket.pyd _sqlite3.pyd _ssl.pyd _uuid.pyd _wmi.pyd _zoneinfo.pyd _zstd.pyd "
        "libcrypto-3.dll libffi-8.dll libssl-3.dll LICENSE.txt msvcp140.dll msvcp140_1.dll "
        "msvcp140_2.dll pyexpat.pyd python.cat python.exe python3.dll python314._pth "
        "python314.dll python314.zip pythonw.exe select.pyd sqlite3.dll unicodedata.pyd "
        "vcruntime140.dll vcruntime140_1.dll winsound.pyd"
    ),
    # qpdf subprocess executable and its fixed runtime DLLs.
    "engines/qpdf/bin": "msvcp140.dll qpdf.exe qpdf30.dll vcruntime140.dll vcruntime140_1.dll",
    # Python PDF and image dependencies, including the native wheel libraries.
    "Lib/site-packages/defusedxml": "__init__.py common.py ElementTree.py",
    "Lib/site-packages/lxml": (
        "__init__.py _elementpath.cp314-win_amd64.pyd _elementpath.py apihelpers.pxi "
        "builder.cp314-win_amd64.pyd builder.py classlookup.pxi cleanup.pxi cssselect.py "
        "debug.pxi docloader.pxi doctestcompare.py dtd.pxi ElementInclude.py "
        "etree.cp314-win_amd64.pyd etree.h etree.pyx etree_api.h extensions.pxi iterparse.pxi "
        "lxml.etree.h lxml.etree_api.h nsclasses.pxi objectify.cp314-win_amd64.pyd "
        "objectify.pyx objectpath.pxi parser.pxi parsertarget.pxi proxy.pxi public-api.pxi "
        "pyclasslookup.py readonlytree.pxi relaxng.pxi sax.cp314-win_amd64.pyd sax.py "
        "saxparser.pxi schematron.pxi serializer.pxi usedoctest.py xinclude.pxi xmlerror.pxi "
        "xmlid.pxi xmlschema.pxi xpath.pxi xslt.pxi xsltext.pxi"
    ),
    "Lib/site-packages/lxml/html": (
        "__init__.py _diffcommand.py _difflib.cp314-win_amd64.pyd _difflib.py "
        "_html5builder.py _setmixin.py builder.py clean.py defs.py diff.cp314-win_amd64.pyd "
        "diff.py ElementSoup.py formfill.py html5parser.py soupparser.py usedoctest.py"
    ),
    "Lib/site-packages/lxml/includes": (
        "__init__.pxd __init__.py c14n.pxd config.pxd dtdvalid.pxd etree_defs.h "
        "etreepublic.pxd htmlparser.pxd lxml-version.h relaxng.pxd schematron.pxd tree.pxd "
        "uri.pxd xinclude.pxd xmlerror.pxd xmlparser.pxd xmlschema.pxd xpath.pxd xslt.pxd"
    ),
    "Lib/site-packages/lxml/includes/extlibs": "__init__.py zconf.h zlib.h",
    "Lib/site-packages/lxml/includes/libexslt": (
        "__init__.py exslt.h exsltconfig.h exsltexports.h libexslt.h"
    ),
    "Lib/site-packages/lxml/includes/libxml": (
        "__init__.py c14n.h catalog.h chvalid.h debugXML.h dict.h encoding.h entities.h "
        "globals.h hash.h HTMLparser.h HTMLtree.h list.h nanoftp.h nanohttp.h parser.h "
        "parserInternals.h pattern.h relaxng.h SAX.h SAX2.h schemasInternals.h schematron.h "
        "threads.h tree.h uri.h valid.h xinclude.h xlink.h xmlautomata.h xmlerror.h "
        "xmlexports.h xmlIO.h xmlmemory.h xmlmodule.h xmlreader.h xmlregexp.h xmlsave.h "
        "xmlschemas.h xmlschemastypes.h xmlstring.h xmlunicode.h xmlversion.h xmlwriter.h "
        "xpath.h xpathInternals.h xpointer.h"
    ),
    "Lib/site-packages/lxml/includes/libxslt": (
        "__init__.py attributes.h documents.h extensions.h extra.h functions.h imports.h "
        "keys.h libxslt.h namespaces.h numbersInternals.h preproc.h security.h templates.h "
        "transform.h transformInternals.h trio.h triodef.h variables.h win32config.h xslt.h "
        "xsltconfig.h xsltexports.h xsltInternals.h xsltlocale.h xsltutils.h"
    ),
    "Lib/site-packages/lxml/isoschematron": "__init__.py",
    "Lib/site-packages/lxml/isoschematron/resources/rng": "iso-schematron.rng",
    "Lib/site-packages/lxml/isoschematron/resources/xsl/iso-schematron-xslt1": (
        "iso_abstract_expand.xsl iso_dsdl_include.xsl iso_schematron_message.xsl "
        "iso_schematron_skeleton_for_xslt1.xsl iso_svrl_for_xslt1.xsl readme.txt"
    ),
    "Lib/site-packages/packaging": (
        "__init__.py _elffile.py _manylinux.py _musllinux.py _parser.py _structures.py "
        "_tokenizer.py dependency_groups.py direct_url.py errors.py markers.py metadata.py "
        "py.typed pylock.py requirements.py specifiers.py tags.py utils.py version.py"
    ),
    "Lib/site-packages/packaging/licenses": "__init__.py _spdx.py",
    "Lib/site-packages/pikepdf": (
        "__init__.py _augments.py _core.pyd _core.pyi _cpphelpers.py _data.py _exceptions.py "
        "_explicit_conv.py _io.py _methods.py _page_copy.py _version.py _xml.py canvas.py "
        "codec.py exceptions.py form.py jbig2.py jobs.py objects.py py.typed sanitize.py "
        "settings.py"
    ),
    "Lib/site-packages/pikepdf.libs": (
        "msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll qpdf30-eca0c1670a8d0fd8a8bc678bfc22fc30.dll"
    ),
    "Lib/site-packages/pikepdf/models": (
        "__init__.py _cal_icc.py _content_stream.py _transcoding.py ctm.py encryption.py "
        "outlines.py"
    ),
    "Lib/site-packages/pikepdf/models/image": (
        "__init__.py _bomb.py _classes.py _colorspace.py _extract.py _postprocess.py _shared.py"
    ),
    "Lib/site-packages/pikepdf/models/metadata": (
        "__init__.py _constants.py _converters.py _core.py _docinfo.py _xmp.py"
    ),
    "Lib/site-packages/PIL": (
        "__init__.py __main__.py _avif.cp314-win_amd64.pyd _avif.pyi _binary.py _deprecate.py "
        "_imaging.cp314-win_amd64.pyd _imaging.pyi _imagingcms.cp314-win_amd64.pyd "
        "_imagingcms.pyi _imagingft.cp314-win_amd64.pyd _imagingft.pyi "
        "_imagingmath.cp314-win_amd64.pyd _imagingmath.pyi _imagingmorph.cp314-win_amd64.pyd "
        "_imagingmorph.pyi _imagingtk.cp314-win_amd64.pyd _imagingtk.pyi _tkinter_finder.py "
        "_typing.py _util.py _version.py _webp.cp314-win_amd64.pyd _webp.pyi "
        "AvifImagePlugin.py BdfFontFile.py BlpImagePlugin.py BmpImagePlugin.py "
        "BufrStubImagePlugin.py ContainerIO.py CurImagePlugin.py DcxImagePlugin.py "
        "DdsImagePlugin.py EpsImagePlugin.py ExifTags.py features.py FitsImagePlugin.py "
        "FliImagePlugin.py FontFile.py FpxImagePlugin.py FtexImagePlugin.py GbrImagePlugin.py "
        "GdImageFile.py GifImagePlugin.py GimpGradientFile.py GimpPaletteFile.py "
        "GribStubImagePlugin.py Hdf5StubImagePlugin.py IcnsImagePlugin.py IcoImagePlugin.py "
        "Image.py ImageChops.py ImageCms.py ImageColor.py ImageDraw.py ImageDraw2.py "
        "ImageEnhance.py ImageFile.py ImageFilter.py ImageFont.py ImageGrab.py ImageMath.py "
        "ImageMode.py ImageMorph.py ImageOps.py ImagePalette.py ImagePath.py ImageQt.py "
        "ImageSequence.py ImageShow.py ImageStat.py ImageText.py ImageTk.py ImageTransform.py "
        "ImageWin.py ImImagePlugin.py ImtImagePlugin.py IptcImagePlugin.py "
        "Jpeg2KImagePlugin.py JpegImagePlugin.py JpegPresets.py McIdasImagePlugin.py "
        "MicImagePlugin.py MpegImagePlugin.py MpoImagePlugin.py MspImagePlugin.py "
        "PaletteFile.py PalmImagePlugin.py PcdImagePlugin.py PcfFontFile.py PcxImagePlugin.py "
        "PdfImagePlugin.py PdfParser.py PixarImagePlugin.py PngImagePlugin.py "
        "PpmImagePlugin.py PsdImagePlugin.py PSDraw.py py.typed QoiImagePlugin.py report.py "
        "SgiImagePlugin.py SpiderImagePlugin.py SunImagePlugin.py TarIO.py TgaImagePlugin.py "
        "TiffImagePlugin.py TiffTags.py WalImageFile.py WebPImagePlugin.py WmfImagePlugin.py "
        "XbmImagePlugin.py XpmImagePlugin.py XVThumbImagePlugin.py"
    ),
    "Lib/site-packages/pillow-12.3.0.dist-info": "METADATA top_level.txt WHEEL zip-safe",
    "Lib/site-packages/pillow-12.3.0.dist-info/licenses": "LICENSE",
    "Lib/site-packages/pypdfium2": (
        "__init__.py __main__.py _lazy.py _library_scope.py raw.py version.json version.py"
    ),
    "Lib/site-packages/pypdfium2/_helpers": (
        "__init__.py attachment.py bitmap.py document.py matrix.py misc.py page.py "
        "pageobjects.py sysfontinfo.py textpage.py unsupported.py"
    ),
    "Lib/site-packages/pypdfium2/internal": "__init__.py bases.py consts.py utils.py",
    "Lib/site-packages/pypdfium2_cfg": "__init__.py",
    "Lib/site-packages/pypdfium2_raw": "__init__.py bindings.py pdfium.dll version.json version.py",
    # PaperWorkShell modules and resources admitted in the reviewed export trial.
    "Lib/site-packages/paperworkshell": "__init__.py __main__.py",
    "Lib/site-packages/paperworkshell/app": (
        "__init__.py assisted_use.py case_lifecycle.py checklist.py core_overlay_tools.py "
        "diagnostic_bundle.py export_history.py flattened_pdf_export.py "
        "installed_image_runtime.py installed_notices.py installed_runtime.py "
        "manifest_repository.py ocr.py ocr_languages.py ordinary_pdf_export.py organizer.py "
        "package_pdf_export.py package_workflow.py page_previews.py pdf_export.py "
        "pdf_thumbnails.py portal_constraints.py post_export_deletion.py quality_analysis.py "
        "quality_ocr_review.py recovery.py redaction_export.py redaction_tools.py "
        "removal_export_review.py scan_review.py searchable_pdf_export.py signature_images.py "
        "signed_source_policy.py source_ingest.py source_inspection.py "
        "target_size_pdf_export.py uninstall_review.py"
    ),
    "Lib/site-packages/paperworkshell/diagnostics": (
        "__init__.py bundle.py codec.py errors.py logging.py models.py phase_observer.py summary.py"
    ),
    "Lib/site-packages/paperworkshell/domain": (
        "__init__.py assisted_use.py case_lifecycle.py checklist.py compression_review.py "
        "image_pages.py image_transforms.py import_precheck.py incremental_processing.py "
        "manifest_migrations.py manifest_repository.py manifest_validation.py ocr.py "
        "ocr_languages.py package_export_errors.py package_summary.py page_coordinates.py "
        "page_operations.py pdf_export.py pdf_export_history.py pdf_export_retry.py "
        "pdf_geometry.py pdf_image_stages.py pdf_inspection.py pdf_optimization.py "
        "pdf_rendering.py pdf_thumbnails.py post_export_deletion.py quality_analysis.py "
        "quality_ocr_review.py recovery.py redaction.py redaction_export.py "
        "redaction_export_candidate.py redaction_ocr.py redaction_reconstruction.py "
        "redaction_sanitization.py redaction_verification.py resource_policy.py "
        "searchable_pdf_export.py signature_images.py signed_source_policy.py "
        "source_ingest.py target_size_search.py text_companion.py uninstall_review.py"
    ),
    "Lib/site-packages/paperworkshell/export": (
        "__init__.py compression_review.py package_export.py package_service.py "
        "package_summary.py pdf_destination.py safe_zip.py target_size_export.py"
    ),
    "Lib/site-packages/paperworkshell/imaging": (
        "__init__.py pdf_image_encoder.py pillow_decoder.py pillow_transformer.py"
    ),
    "Lib/site-packages/paperworkshell/localization": "__init__.py catalog.py",
    "Lib/site-packages/paperworkshell/localization/catalogs": "ar-XB.json en-XA.json en.json",
    "Lib/site-packages/paperworkshell/ocr": "__init__.py ocrmypdf_adapter.py tesseract_runner.py",
    "Lib/site-packages/paperworkshell/overlays": (
        "__init__.py commands.py exporting.py models.py rendering.py signature_commands.py "
        "signature_rasters.py"
    ),
    "Lib/site-packages/paperworkshell/pdf": (
        "__init__.py export_adapter.py flattened_export_adapter.py image_stage_adapter.py "
        "image_stage_analysis.py image_stage_encoder.py ocr_candidate_verifier.py "
        "optimization_adapter.py overlay_renderer.py page_geometry.py pdfium_renderer.py "
        "pikepdf_adapter.py portal_policy.py qpdf_checker.py raster_pdf_page.py "
        "redaction_export.py redaction_export_ocr.py redaction_ocr.py "
        "redaction_reconstruction.py redaction_sanitization.py redaction_verification.py "
        "redaction_verification_linearized.py redaction_verification_resources.py "
        "redaction_verification_structure.py redaction_verification_viewer_metadata.py "
        "redaction_verification_visual.py renderer.py searchable_export_adapter.py "
        "text_companion.py text_presence.py"
    ),
    "Lib/site-packages/paperworkshell/preflight": (
        "__init__.py core_rules.py engine.py facts.py models.py package_planner.py "
        "portal_constraints.py rules.py"
    ),
    "Lib/site-packages/paperworkshell/storage": (
        "__init__.py _source_windows.py _windows.py assisted_use.py "
        "case-manifest-v1.schema.json case-manifest-v2.schema.json "
        "case-manifest-v3.schema.json case-manifest-v4.schema.json case_lifecycle.py "
        "diagnostic_bundle.py errors.py export-history-v1.schema.json export_destination.py "
        "export_history.py export_package_destination.py export_retry.py manifest_codec.py "
        "manifest_migration_backup.py manifest_repository.py permissions.py "
        "post_export_deletion.py prepared_source_registry.py quotas.py recovery.py "
        "signature_images.py source_ingest.py source_precheck.py template_repository.py "
        "thumbnail_cache.py uninstall_review.py workspace.py"
    ),
    "Lib/site-packages/paperworkshell/templates": (
        "__init__.py catalog.py codec.py loader.py models.py template-v1.schema.json validation.py"
    ),
    "Lib/site-packages/paperworkshell/templates/builtin": "generic-portal-application-v1.1.0.json",
    "Lib/site-packages/paperworkshell/ui": (
        "__init__.py announcements.py application.py assisted_session.py assisted_use.py "
        "backend.py checklist.py compression_review.py constraints.py diagnostic_bundle.py "
        "keyboard_help.py localization_layout.py localized_messages.py models.py notices.py "
        "ocr_languages.py overlay_canvas.py overlay_tools.py package_export.py "
        "page_delegate.py post_export.py presentation.py preview_comparison.py "
        "redaction_canvas.py redaction_export.py redaction_tools.py removal_export_review.py "
        "scan_review.py signature_images.py signed_pdf_confirmation.py uninstall_review.py "
        "wheel_safe.py window.py"
    ),
    "Lib/site-packages/paperworkshell/worker": (
        "__init__.py _windows_broker.py _windows_runtime.py compression_preview.py "
        "desktop_composition.py flattened_pdf_export_broker.py "
        "flattened_pdf_export_buffers.py flattened_pdf_export_composition.py "
        "flattened_pdf_export_operation.py flattened_pdf_export_plan.py "
        "flattened_pdf_export_report.py heavy_operations.py host.py host_bootstrap.py "
        "image_normalize_operation.py image_page_broker.py image_transform_broker.py "
        "image_transform_operation.py ocr_broker.py ocr_composition.py ocr_operation.py "
        "operations.py pdf_export_broker.py pdf_export_buffers.py pdf_export_operation.py "
        "pdf_export_plan.py pdf_export_report.py pdf_image_stage_broker.py "
        "pdf_image_stage_composition.py pdf_image_stage_operation.py pdf_image_stage_wire.py "
        "pdf_operation.py pdf_optimization.py pdf_source_inspector.py pdf_thumbnail_broker.py "
        "pdf_thumbnail_operation.py pdf_thumbnail_profile.py private_temp.py "
        "process_creation_policy.py quality_ocr_review_broker.py "
        "quality_ocr_review_buffers.py quality_ocr_review_composition.py "
        "quality_ocr_review_operation.py redaction_export_broker.py "
        "redaction_export_composition.py redaction_export_operation.py "
        "redaction_export_scratch.py redaction_export_wire.py redaction_ocr_broker.py "
        "redaction_ocr_composition.py redaction_ocr_operation.py redaction_ocr_wire.py "
        "redaction_reconstruction_broker.py redaction_reconstruction_composition.py "
        "redaction_reconstruction_operation.py redaction_reconstruction_wire.py "
        "redaction_sanitization_broker.py redaction_sanitization_composition.py "
        "redaction_sanitization_operation.py redaction_sanitization_wire.py runtime.py "
        "searchable_pdf_export_broker.py searchable_pdf_export_composition.py "
        "searchable_pdf_export_operation.py searchable_pdf_export_plan.py "
        "searchable_pdf_export_report.py searchable_pdf_export_status.py "
        "signature_image_raster_broker.py signature_image_raster_operation.py "
        "text_companion_broker.py text_companion_operation.py"
    ),
    "Lib/site-packages/paperworkshell/worker/client": (
        "__init__.py _acl_recovery.py _windows.py client.py errors.py models.py process.py "
        "tesseract-language-pack-catalog-v1.json tesseract-runtime-closure-v1.json "
        "tesseract-runtime-closure-v2.json tesseract_runtime.py"
    ),
    "Lib/site-packages/paperworkshell/worker/protocol": (
        "__init__.py bootstrap.py codec.py errors.py framing.py image_stage.py models.py "
        "operation_scope.py peer.py redaction_export.py redaction_ocr.py "
        "redaction_reconstruction.py redaction_sanitization.py state.py text_companion.py"
    ),
}


def installed_pdf_export_runtime_files(runtime_root: Path) -> tuple[Path, ...]:
    """Return the reviewed installed files without enumerating the runtime."""

    return tuple(
        sorted(
            runtime_root / directory / name
            for directory, names in _PDF_EXPORT_RUNTIME_FILES.items()
            for name in names.split()
        )
    )
