# PWS-0802 — authorized nonbinding Foundation eligibility inquiry

**Status: submitted once; official form acknowledgement observed.** The maintainer
authorized one factual inquiry on 2026-11-04 and supplied its reply email address.
The official General message form confirmed “Form submitted” and “Thank you, we'll
be in touch soon.” A response, eligibility and signing coverage remain unproved.
No provider account, agreement, identity document, installer upload, payment or
public release is authorized.

## Official route and observed requirements

The [Foundation application](https://signpath.org/apply.html) says that its name
and email fields create a SignPath user account. It also requires agreement to
the [Foundation conditions](https://signpath.org/terms.html) and personal-data
processing consent. Those steps exceed the inquiry authorization; that form has
not been filled or submitted.

The operator's [official general contact form](https://signpath.io/contact) offers
**General message**. Its required fields are first name, last name, reply email,
company name, type and message. The observed form has no account-creation or
agreement checkbox. Use **Janne**, **Vuorela**, **Independent developer —
PaperWorkShell (no company)** and **General message**. The email must be an
address the maintainer authorizes for this inquiry; never infer it from private
Git configuration, credentials or diagnostic logs. The Foundation is operated by
SignPath GmbH, as its [About page](https://signpath.org/about.html) records.

## Exact inquiry text

Subject: PaperWorkShell — nonbinding SignPath Foundation eligibility inquiry

Please route this factual inquiry to the SignPath Foundation eligibility team.
I am Janne Vuorela, the individual publisher and maintainer of PaperWorkShell,
an independent Windows 11 x64 desktop application for local document processing.
Project-owned code is MIT-licensed and official use is free, including commercial
use. Public repository: https://github.com/PikkuJanne/PaperWorkShell.
Documents and document-derived data remain local; the application requires no
product account and sends no document uploads, telemetry or automatic crash
reports. ARM64 and execution on ARM64 are outside the project scope.

We have an unsigned internal installer, revision
`pws0802-internal-20261103-r7` (512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`).
Its artifact audit and defined installation checks are complete, with clean
Windows 11 x64 standard-account, offline document-workflow and interruption/
recovery evidence within a documented unchanged runtime boundary. Additional
finite x64 qualification is in progress. There is no qualified public installer
release, and the internal installer is not presented as a public release. The
current local working tree is not represented as a verified public CI build.

Could you clarify:

1. Can the project qualify at this stage with an internally qualified installer
   and no qualified public installer release?
2. How would your existing-release requirement apply, given that trusted signing
   is required before our first public installer distribution?
3. How do bundled CPython, Qt/PySide6, PDF/image/OCR libraries and other upstream
   native binaries affect eligibility and signing coverage? In particular, the
   package contains five unmodified AMD64 Microsoft MSVC 14.44.35211.0 DLL objects
   at nine recorded paths (`msvcp140`, `msvcp140_1`, `msvcp140_2`, `vcruntime140`
   and `vcruntime140_1`), acquired under the accepted Visual Studio Community 2022
   individual-developer route. Would those files qualify for the System Libraries
   exception? Which project-owned artifacts could be signed, while upstream files
   retain their own bytes, notices and signatures?
4. What concrete prerequisites would remain, including build-origin verification,
   release evidence, reputation, contributor/approver roles and signing policy?

This is an eligibility inquiry only. It does not enroll an account, accept an
agreement, request a paid service, submit identity documents or upload installers,
documents, user data, credentials or private logs. We understand that admission is
your decision and that a Foundation certificate identifies SignPath Foundation
separately from the product publisher Janne Vuorela.

## Evidence and subsequent status

The original shorter draft remains in the retained R7 evidence epoch. This text
adds all four questions in the maintainer's current authorization and the x64-only
scope from [ADR 0019](../adr/0019-windows-11-x64-only-scope.md). The inquiry was
submitted once through `https://signpath.io/contact`, type **General message**,
with the explicitly supplied reply address. The saved acknowledgement observation
is timestamped **2026-11-04 05:37:57 UTC**. No additional account, agreement,
CAPTCHA, identity document or upload was required by the observed route.

The exact plain-text message preserves the content above with Markdown code
delimiters removed. Its SHA-256 is
`3c9792b52a3fa74bc104a3a5da6e64499493d9d75442d48c0709acc594d9bb34`.
The local observation receipt is
`64741af5c401ab0e8e34252cef0dba81684c38872bb839273ebed4ac74f48688`;
the acknowledgement screenshot is
`3076b496db23d2a113d0b15d52974a14c5c39801c79e9080697cd06f31664435`.
They are retained under the ignored local
`output/pws0802/x64-scope-20261104/foundation-inquiry-v1/` evidence directory.
The reply address remains in that local record rather than public source docs.
A sent inquiry is not provider acceptance; no Foundation answer has been observed.
