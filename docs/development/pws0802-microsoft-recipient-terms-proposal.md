# PWS-0802 — proposed Microsoft-only recipient terms

**Historical proposal, superseded for new candidates.** Later authority permitted
correction and implementation, not verbatim approval of this text. The
[current result](pws0802-community-runtime-result.md) records the separately
authored, reviewed nine-path Microsoft-only agreement.

**PROPOSED — maintainer review required.** This document is not an accepted
agreement, licence grant, shipping asset or change to the sealed installer.
Reading or approving this proposal does not establish Visual Studio entitlement,
accept an unseen agreement or authorize distribution by itself.

The basis is the exact [Visual Studio Community 2022 agreement](https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Community-License-EN.docx),
EULAID `VS_Comm_2022_ENU.1033`, SHA-256
`41a207b10c8ab91d0d2f10a854715f73dca54509581692d2fe179aa3ffcb8540`.
Section 4(a)(i)/(iii), 4(b)(i)/(ii) and 4(c) govern the proposed redistribution;
sections 7, 10 and 12–14 supply related protection and statutory exceptions.
The [VS2022 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
separately requires a validly licensed Visual Studio copy and unmodified eligible
files. Actual owner acceptance and authenticated Community acquisition remain
pending; this proposal supplies neither.

## Exact scope

Only these five unmodified AMD64 runtime files, version **14.44.35211.0**, would
be covered. [The data-only review](pws0802-microsoft-runtime-preparation.md)
records the official package, CAB/MSI membership, sizes, signatures and full
SHA-256 values:

- `msvcp140.dll`
- `msvcp140_1.dll`
- `msvcp140_2.dll`
- `vcruntime140.dll`
- `vcruntime140_1.dll`

The proposed terms apply to those Microsoft objects only. They do not change
PaperWorkShell's first-party MIT licence, third-party notices or Qt/LGPL rights
to replace/relink LGPL components and reverse engineer the combined application
to debug those modifications. Normal application use remains free, including
commercial use, without a PaperWorkShell account, email, activation, licence key,
subscription or upload. End users do not need Visual Studio to run the app.

## Proposed recipient-facing text

> The listed Microsoft runtime components are licensed, not sold. You may use
> and copy them as needed to run PaperWorkShell, including commercially.
>
> Preserve Microsoft and supplier notices. Do not modify these Microsoft
> binaries, bypass their technical restrictions, or derive their source by reverse
> engineering, decompilation or disassembly, except where applicable law or
> applicable third-party licensing requirements permit otherwise.
>
> Redistribute them unmodified only with PaperWorkShell or derivative applications
> providing significant primary functionality beyond the runtime. Require
> downstream distributors and recipients to agree to these Microsoft-only
> protections. Do not offer the runtime separately, use Microsoft's trademarks in
> application names, imply Microsoft endorsement, distribute components marked
> Preview, Pre-release or Beta, or impose source-disclosure or modification-right
> obligations on Microsoft code. Use it lawfully and follow export restrictions.
>
> Microsoft and its suppliers provide these components as-is, without express
> warranties. Where law permits, implied merchantability, fitness and
> non-infringement warranties are excluded; recovery from Microsoft and its
> suppliers is limited to direct damages up to US $5, excluding other damages.
> Mandatory consumer and statutory rights remain unaffected. These restrictions
> do not limit the separate MIT or Qt/LGPL rights described above.

This is proposed distributor wording intended to satisfy section 4(b)(ii), not a
claim that Microsoft has approved it. It introduces no Microsoft indemnity from
the distributor: that obligation is absent from the inspected Community 2022
agreement. Before shipping, the approved exact text must be component-labelled,
included with the five-file inventory/notices and presented for recipient
agreement; merely burying it in a passive notice is not treated as agreement.
Any implementation must preserve the separate MIT/LGPL grants and must not
apply the Microsoft-only restrictions to the entire application.

## Separate retained pikepdf scope

`pikepdf.libs/msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll` is not added to this
proposal. Its bytes match the canonical `msvcp140.dll` above, but its retained
review uses the distinct historical VS2019 downstream route and conditions.
Byte identity does not relicense it, remove that route's obligations or establish
that a filename-renamed copy is covered by the new route. Its recipient terms
and distributor obligations still need separate reconciliation before a public
complete-runtime candidate can be approved. This proposal does not alter its
existing licence asset or inventory record.

## Smallest maintainer decision

Approve or correct the exact Microsoft-only recipient-facing text above for the
five specified files, preserving first-party MIT and Qt/LGPL rights. That approval
would authorize preparing the matching scoped terms asset and agreement flow
after the separate owner-acceptance/acquisition gates pass. It would not approve
the unresolved pikepdf route, grant redistribution rights, enroll in signing,
publish an unsigned build or change the current sealed candidate.
