# ADR 0006: PySide6 Essentials for the desktop UI runtime

- Status: Accepted
- Date: 2026-08-05
- Task: PWS-0111
- Decisions: PWS-D002, PWS-D012, PWS-D014, PWS-D030, PWS-D034, PWS-D037
- Dependency policy: [PySide6 and Qt](../development/dependency-policy.md#pyside6-and-qt)
- Architecture: [Dependency boundaries](../architecture/dependency-boundaries.md)

## Context

PWS-D014 fixes PySide6 as the initial desktop toolkit, but it does not authorize every package or
module in Qt for Python. PWS-0111 needs Qt's core object model, graphics primitives, and Widgets UI
without importing a second PDF renderer, a browser engine, networking APIs, or the large Addons
surface into product code.

Qt publishes a convenience `PySide6` meta-package that depends on both PySide6 Essentials and
PySide6 Addons. The Addons wheel contains the Qt PDF and Qt WebEngine bindings, among many other
modules. PWS-0106 evaluated that exact Addons closure only as a non-default renderer candidate and
selected pypdfium2/PDFium instead. Pulling the meta-package into the runtime would therefore undo
the renderer selection and enlarge the GUI and packaging surface without a PWS-0111 capability
need.

## Decision

Declare **`pyside6-essentials==6.11.1`** as the sole direct Qt for Python runtime requirement. Its
locked dependency is **`shiboken6==6.11.1`**. Do not declare or lock the `PySide6` convenience
meta-package. Keep **`pyside6-addons==6.11.1`** solely in the non-default
`renderer-evaluation` dependency group; Qt PDF and its PDFium build retain no product scope.

Product source may import only these exact toolkit modules, and only from the `ui` layer:

- `PySide6.QtCore`
- `PySide6.QtGui`
- `PySide6.QtWidgets`

Imports use the fully qualified module, such as `from PySide6.QtCore import Qt`. Importing from the
`PySide6` root is rejected because static analysis cannot prove which split-wheel module an alias
names. Qt Network, QML, Qt Quick, Qt PDF, Qt WebEngine, and every other PySide6 module remain
unapproved product imports even if a contributor's evaluation environment happens to contain
them.

The `ui` package remains a passive namespace and depends on toolkit-free application/domain
contracts plus GUI-safe worker client/protocol modules. The desktop entry point has one exact
composition exception: `paperworkshell.__main__` may import
`paperworkshell.worker.desktop_composition`. No application module, UI module, or other desktop
import receives general worker-host access.

PySide6 does not process source documents in the GUI. The UI constructs display images only from
the PWS-0108 fixed-layout, validated RGBA8 thumbnail contract. It does not load a PDF, filesystem
path, arbitrary encoded image, or renderer object through Qt.

## Artifact and runtime evidence

The exact Windows x86-64 artifacts were already resolved for the PWS-0106 comparison and are now
reclassified according to their distinct roles:

| Component | Artifact | Size | SHA-256 | Product role |
|---|---|---:|---|---|
| PySide6 Essentials 6.11.1 | `pyside6_essentials-6.11.1-cp310-abi3-win_amd64.whl` | 77,549,267 | `63311bd48e32c584599ab04b9ef7c324082374cd2c9fa533f978fb893bb47e40` | Approved GUI runtime; shipment planned |
| shiboken6 6.11.1 | `shiboken6-6.11.1-cp310-abi3-win_amd64.whl` | 1,223,647 | `c2c6863aa80ec18c0f82cea3417837b279cdc60024ac17123461dc9042577df7` | Approved transitive GUI runtime; shipment planned |
| PySide6 Addons 6.11.1 | `pyside6_addons-6.11.1-cp310-abi3-win_amd64.whl` | 168,816,308 | `0d13c4dfd671b050a48e4f8d8ddc724b7248f9c0437e7fc47fdf316278572923` | Evaluation-only; no active scope |

The supported CPython 3.14.6 Windows x86-64 environment imports PySide6/Qt 6.11.1 from the stable-
ABI wheel. Installed `RECORD` SHA-256 values are
`859898b05309b1ec139e12377c9ad0490260e10cbb7f18138002c3844ae3cb58` for Essentials and
`c4a6c7643da99ac93e8f5eb9a32ea90863993f87e76fe136cc6b11045b67badb` for shiboken6. The
Essentials record owns the QtCore, QtGui, and QtWidgets binding modules and does not own QtPdf,
QtPdfWidgets, or QtWebEngine binding modules.

The Addons and Essentials `RECORD` files also declare 64 identical PySide namespace/type-support
paths, including `PySide6/__init__.py`. In a previously expanded environment, removing Addons with
an ordinary sync deletes that shared bootstrap and QtCore then fails to initialize its DLL search
path. A fresh default sync works, as does explicitly reinstalling Essentials and shiboken6. The
renderer-evaluation workflow should therefore be disposable; this ownership overlap is not a
reason to retain Addons in the default runtime.

## License and Windows packaging route

The selected community route for the used Essentials/shiboken GUI surface is
**LGPL-3.0-only**. This is an engineering admission record, not legal advice. The exact immutable
LGPL text is bound to Qt for Python tag commit
`73fb12a067c2e8f7a464a310aaee2860fa2b64d2`. The official PySide 6.11.1 source archive is
`pyside-setup-everywhere-src-6.11.1.tar.xz`, 17,963,432 bytes, SHA-256
`6ffd9835bb0dd2c56f061d62f1616bb1707cfc0202b80e3165d6be087f3965e2`.

Admission does not approve redistributing the complete Essentials wheel. Its installed record has
2,492 paths, including 198 DLLs, 22 binding modules, plugins, QML content, and development tools.
Some unused Qt modules have different open-source availability. PWS-0801 must create a
file-by-file release inventory and a dynamic standalone subset containing only the reviewed
QtCore, QtGui, QtWidgets, Windows `qwindows` platform plugin, required image/style plugins,
shiboken support, and their exact native dependencies.

Before shipment, PWS-0801 must also provide the applicable LGPL and other shipped-file license
texts, notices, complete corresponding PySide/Qt source for every shipped library and plugin,
third-party notices, hashes, SBOM entries, and installation information that preserves library
replacement or relinking. A static or opaque one-file package is not presumed compliant. The
inventory therefore records both selected components as `planned`, not `bundled` or `shipped`.

## Alternatives considered

### PySide6 meta-package

Rejected. It would install Addons and make Qt PDF, WebEngine, multimedia, 3D, and other unrelated
native surfaces part of the default dependency closure.

### PySide6 Addons as a runtime dependency

Rejected. PWS-0111 needs none of its modules. Qt PDF also lost the PWS-0106 renderer selection and
must not enter the GUI as an incidental dependency.

### QML or Qt Quick for the organizer

Not selected for this vertical slice. Qt Widgets satisfies the required desktop controls and
keeps product imports to three Essentials modules. A future QML requirement needs its own
capability, boundary, accessibility, license, and packaging review.

### Another UI toolkit

Rejected because PWS-D014 already selects PySide6. Replacing that frozen decision would require
the decision-change procedure rather than an implementation shortcut.

## Consequences

- Ordinary `uv sync --locked` installs Essentials and shiboken; it does not install the
  meta-package or Addons.
- The renderer-evaluation group can still add Addons for PWS-0106 regression evidence, without
  granting product import or shipment authority.
- Removing the evaluation group from an in-place environment requires environment recreation or an
  exact Essentials/shiboken reinstall because the upstream split wheels share namespace files.
- Static boundary tests reject every non-UI PySide6 import and every UI import outside the three
  selected modules.
- Widget accessibility tests replace the no-UI sentinel when the first UI surface appears.
- Release packaging remains explicitly blocked on the PWS-0801 file, source, notice, relinking,
  and SBOM reconciliation.

## Primary sources

- [Qt for Python package layout](https://doc.qt.io/qtforpython-6/package_details.html)
- [PySide6 Essentials 6.11.1 published files](https://pypi.org/project/PySide6-Essentials/6.11.1/)
- [Qt for Python 6.11.1 release notes](https://doc.qt.io/qtforpython-6/release_notes/pyside6_release_notes.html)
- [Immutable LGPL-3.0-only text](https://raw.githubusercontent.com/pyside/pyside-setup/73fb12a067c2e8f7a464a310aaee2860fa2b64d2/LICENSES/LGPL-3.0-only.txt)
- [Official PySide 6.11.1 source archive and hash](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/pyside-setup-everywhere-src-6.11.1.tar.xz.mirrorlist)
- [Qt open-source LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)
- [Qt for Windows deployment](https://doc.qt.io/qt-6/windows-deployment.html)
