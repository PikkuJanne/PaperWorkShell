# Localization and right-to-left preparation

PWS-0704 uses packaged UTF-8 JSON language catalogs for presentation text. The
localization package uses only the Python standard library. Translation never
changes case data, source documents, page geometry, export names, protocol values,
template identities, or configured numeric limits.

English (`en`) is the source language. `en-XA` expands and accents source text to
expose layout problems. `ar-XB` adds Arabic test text and right-to-left layout to
exercise direction, reading order, and locale formatting. These are test locales,
not reviewed English or Arabic translations for distribution.

## Translating application text

Catalogs live under `src/paperworkshell/localization/catalogs/`. Each resource has
`schema_version: 1`, its locale tag, `ltr` or `rtl` direction, number/date display
metadata, and a `messages` object mapping English source messages to translated
messages. Add or update a catalog as data; no application-code edit is needed for
translation updates. Include the resulting resource in the normal application
build and restart the application to use it. English fallback remains available
when an installed locale has no exact or base-language match.

Select a language before launch with the `PWS_UI_LOCALE` environment variable
(for example, `en-XA` for layout testing). Without an explicit selection the
desktop matches the system locale to installed resources, with English fallback.
`configure_ui_locale` applies the matching
Qt locale and layout direction before building the window. Language changes
take effect on a new application launch; existing widgets do not hot reload.

Translate the whole sentence. Preserve named placeholders such as `{count}` and
`{filename}`, including their format specifiers, and preserve the intended
keyboard mnemonic marked with `&`. Placeholders can move within the sentence.
Never add attribute lookup, indexing, nested formatting, or executable content.
Catalog validation rejects mismatched placeholders, duplicate JSON keys, unknown
versions/fields, invalid locale metadata, and oversized resources.

Keep established product terminology and the scope of validation claims. In
particular, a placed signature image is a visual mark, `Needs review` expresses
uncertainty, and an unsigned derivative does not retain digital-signature status.
Translations must preserve warnings and human-review requirements.

## Adding or changing text in code

Use `tr("Complete source sentence {count}", count=count)` where a presentation
value is constructed. Interpolate after translation. Use `mark("Source text")`
for deferred constants and translate the marked value at presentation time. Do
not translate enum values, JSON keys, error codes, paths, template IDs, filenames,
checksums, keyboard shortcut specifications, or user-entered document content.

Presentation strings originating in application/domain models are translated at
the display boundary or inside their presentation methods. Translate fixed
application vocabulary rather than raw library exceptions or arbitrary user
text. Never log interpolation values or failed catalog contents.

Run `python tools/dev.py validate-localization` after changing messages. The
validator inventories approved source messages, checks the packaged catalogs, and
detects missing entries and unmarked workflow text. Its explicit regeneration
mode refreshes English and the two synthetic test catalogs; translators must
review real-language changes separately. Run formatting, linting, typing, unit,
and accessibility checks through the repository developer commands.

Approved deferred sources include closed `*_MESSAGES` registries, preflight
finding definitions, display-state/phase enums, named presentation methods and
the explicit presentation-module list in the validator. Built-in template prose
comes from its JSON artifact. These sources keep canonical backend vocabulary
for validation; translate it before composing visible messages. Adding a source
to the inventory does not itself translate its use in a widget.

Refresh those three source/test catalogs with
`python -m tools.validate_localization --refresh`, then run
`python tools/dev.py validate-localization` to verify the complete inventory.

## Numbers, dates, and sizes

Use `format_number`, `format_date`, and `format_file_size` for human-readable
displays. Number grouping, decimal separators, digit shapes, and date order come
from the selected catalog. File sizes use IEC units (`KiB`, `MiB`, and so on);
where a limit requires exact interpretation, show the exact byte count as well.
The English date display retains ISO year-month-day ordering. Persist dates as
their existing ISO strings and sizes/counts as numbers. Locale formatting does
not change constraint parsing, output naming, protocol serialization, or case
schema versions.

Case revision/update timestamps retain ISO-8601 with an explicit offset, and
assisted handover/expiry timestamps retain explicitly labelled UTC. These exact
technical timestamps support comparison during recovery and handover; calendar
dates elsewhere use the selected date order and digits. Constraint text-entry
fields retain their documented numeric syntax independently of formatted review
labels. User-entered form dates and document text remain exactly as entered.

Template content has its own validated locale tag and version. The built-in
generic template remains the exact externally recorded byte artifact. Translate
its title, instructions, requirement titles/guidance, warnings and confirmation
statements only for display. Preserve its raw document, provenance assertions,
source locale, constraints, identity and trusted hash. A newly translated
standalone template is a separate reviewed artifact and must pass the existing
template schema, checksum, lifecycle and semantic checks. UI locale selection
does not approve it or make an unsupported template version valid.

## Direction, focus, and geometry

Set the application layout direction from the selected catalog before creating
widgets. Ordinary forms and workflow layouts follow that direction. Explicit
logical tab order stays in workflow order in both directions. Keyboard movement
commands named left/right retain physical document meaning.

Document previews and overlay/removal canvases use left-to-right physical page
coordinates. Locale direction must never reflect a preview, swap its crop axes,
change normalized coordinates, or mirror the exported document. User text is
displayed and stored exactly as entered; ordinary text widgets provide native
bidirectional editing. Numeric coordinate fields retain their defined units and
normalized intent.

`tests/accessibility/test_localization_layout.py` exercises expanded test text at
normal and doubled nine-point text sizes, critical controls in constrained
dialogs, RTL workflow focus, and crop/rotation pointer mapping. These automated
Qt checks do not claim human NVDA, reviewed natural-language translation, actual
Windows OS-setting, packaged-build, or physical-device certification. The M7 gate
retains its own human and release evidence requirements.

The Windows offscreen Qt backend can expose an empty font database. The
localization tests then temporarily admit the installed Segoe UI font and assert
real Latin and Arabic glyph coverage. They restore the earlier font/locale and
remove that admission afterward. This avoids accepting missing-glyph boxes as
successful text rendering and introduces no font redistribution or application
font override. Native operating-system file-picker labels continue to follow
their platform language; application-owned captions use the selected catalog.
