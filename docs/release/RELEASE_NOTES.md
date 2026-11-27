# PaperWorkShell R3 candidate release notes

| Field | Value |
|---|---|
| Candidate | `pws0807-tab-focus-engineering-r3` |
| Embedded version and channel | `0.0.0` · `engineering` |
| Source revision | `54e0b916ac069ce00a0ce200c43e07f3a76d1784` |
| Installer | 513,023,918 bytes · SHA-256 `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889` · Authenticode `NotSigned` |
| Document revision | `pws0808-r3-notes-1` |

These notes accompany the exact R3 package in the M8 product-completion review. The gate report
records the disposition and the separate system-volume exhaustion check. External publication requires
separate owner authorization. [M8 gate report](../gates/m8-product-completion-gate.md) · [Artifact and evidence index](RELEASE_EVIDENCE.md)

## Workflow and installation

PaperWorkShell works offline with PDF, JPEG and PNG. Import creates immutable local copies.
The workbench supports page organization, local OCR, visual marks, checklist review and export as
a combined PDF, several PDFs or a ZIP. Save the optional text summary before closing if you need
it later. The support bundle previews its two files and saves locally after consent. [User guide](../user/USER_GUIDE.md) · [Care and recovery](../user/CARE_AND_RECOVERY.md)

R3 corrects the missing selected-tab focus outline that appeared with Aquatic high contrast at
200% scaling on R2. Standard-user observations covered keyboard focus at 100% and 200%, including
Aquatic at 200%, and NVDA 2026.1.1.55980 speech in ten workflow groups. [R3 qualification](../development/pws0807-r3-qualification.md)

The offline installed matrix covers Windows 11 Pro 26300.9711 on native x64, with a standard user
and Smart App Control Off. Windows policy determines whether the unsigned build can run. Check the
package against its trusted reference and keep Windows security policy in effect. Use the prepared
per-user installer for setup, same-build repair, or case-preserving replacement. The documented
replacement route preserves managed cases when you choose **Keep cases**. The live update channel
is disabled. [Release interface](RELEASE_INTERFACE.md) · [Windows support boundary](WINDOWS11_X64_SUPPORT.md)

## Known limitations

- Use **Add file** for PDF, JPEG or PNG. Operating-system file opening, file associations, portable
  case transfer, and direct portal submission are unsupported.
- **Place signature image** adds a visual mark. Modifying a cryptographically signed source produces
  an unsigned derivative when that operation is supported. Keep the original for signature purposes.
- Permanent removal supports the verified single-active-page PDF profile. Visual marks retain underlying
  content. The permanent-removal export must pass verification before the derivative is saved.
- OCR, quality findings, and compression require review. A requested size can be refused at the
  verified quality floor. Arbitrary interactive forms are unsupported. A checklist Combined PDF can
  refuse a group with active raster processing and a visual overlay. Use Separate PDFs for the tested
  mixed case.
- Display, reader, responsiveness, and resource claims apply to the recorded scenarios. Earlier
  reader observations retain their original dates and versions where independently matched to exact
  reproduced files. The artifact has no physical hardware certification or PDF/UA claim.

[Detailed limits and verification scope](RELEASE_INTERFACE.md#13-known-limitations)

## Synthetic sample

Use `PaperWorkShell-R3-synthetic-sample.zip`, SHA-256
`027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468`. The embedded manifest
has SHA-256 `a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90`.
Its expectations came from R2. Each R3 installation matched 11 workflow reference files. The R3
support archives match their own previews and the frozen allowlist. [Sample evidence](RELEASE_EVIDENCE.md#6-synthetic-release-sample-package)

Both 240 DPI, JPEG quality 85 compression reference PDFs retain all three pages. Pages 1 and 2
render identically to their baseline. Page 3 is reencoded, in color or grayscale according to the
chosen reference. This companion clarification applies to the unchanged ZIP and manifest.

## Support

Internal recipients can report an issue to the maintainer who supplied the package through the
same private channel. Include the candidate, Windows build, steps and sanitized error code. The
optional local support bundle is available for review before sharing. [Security reporting](../../SECURITY.md)
