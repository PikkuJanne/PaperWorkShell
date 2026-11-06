# PWS-0703 display accessibility

This audit covers application layout, contrast, target size and reduced motion.
It supplements the accepted [keyboard audit](pws-0701-keyboard-audit.md) and
[screen-reader audit](pws-0702-nvda-audit.md); it does not repeat their document
processing or NVDA observations.

## Application policy

`ui/presentation.py` wraps the installed native Qt style. It preserves the
application font and palette, including later palette changes. Menu painting
explicitly pairs the palette's window/text and selection/text roles because
Windows native menu painting can otherwise ignore an application contrast palette.
Focus uses the existing dashed palette-highlight outline, including programmatic
recovery focus. Status wording and control state remain available without color.
Document pages remain white; their schematic text uses black. Surrounding canvas
instructions use the application window text color.
Existing stylesheet widgets are repolished on an application palette change.
Explicit button minima prevent that repolish from shrinking a focused target;
rendered and native dark-to-light regressions prove both colors and geometry.

All measurements below are **Qt logical pixels**, before display scaling:

| Target | Application minimum |
| --- | --- |
| Push/tool buttons, editable fields, combo/spin boxes, list rows | 32 by 32 |
| Checkbox/radio indicators and scrollbar thickness | 24 by 24 / 24 thick |
| Font growth | Native size hint can increase either minimum |

The clickable checkbox/radio indicator is enlarged, rather than only painting a
larger decoration. Wrapped companion labels preserve complete consent wording,
accessible names and label buddies. Native menu entries, individual spin arrows,
scrollbar arrows, and visual canvas resize handles are exceptions to the 32-pixel
target: they retain native/document geometry and have keyboard or exact-field
alternatives. A canvas handle is never the only editing path.

Decorative motion is **always reduced**. Qt widget, menu, combo, tooltip and
spin-button animation effects are disabled; an unknown-length operation uses a
static activity bar with a truthful queued/starting/working label. Numeric progress
still updates from real progress facts. Cancellation and terminal announcements
retain their previous behavior. This policy satisfies a request for reduced motion
without storing another preference or polling Windows; there is no animated mode
to enable. Timers for autosave, bounded work and focus reveal remain functional.

## Layout review

The case chooser, organizer and status action rows wrap in reading order. The
existing outer scroll area reveals keyboard focus. Dense editor action rows and
properties reflow, and native acceptance/cancellation button boxes stack vertically
so long labels cannot push recovery actions off screen. Scan comparisons stack on
narrow windows and paint the existing bounded raster at its aspect ratio, without
retaining another scaled image. Signature and assisted-consent choices wrap.

Automated checks exercise 800 by 560 main windows with 100/150/200 percent text,
640 by 420 editor windows at 9 and 18 points, and the existing 520 by 360 package
review with 24-point text. They check actual target geometry, focus reveal,
unclipped footers, preserved intent, rendered contrast pixels, pointer hit testing,
and unknown-to-known progress transitions. Deliberately disabling the footer
adapter reproduces four dialog-width failures; a no-op presentation policy
reproduces three target-size failures.

## Native visual review method

An agent operates native Windows widgets through the computer-use skill using
only synthetic view states. The isolated presentation harness cannot initialize a
backend, read a user's case, import, process, or export a document. Each process
records actual Qt platform, device-pixel ratio, available geometry, font size and
exact UI/harness source hashes under ignored `output/pws0703/`. New observations
are recorded separately for each attempt. No old human observation becomes a pass.

The review uses separate processes requesting 100, 150 and 200 percent Qt display
scale. Dark/light contrast palettes and doubled text are applied only to these
processes; the user's Windows settings are unchanged. This proves the requested
Qt/native-widget scenarios, not every Windows contrast theme, mixed-monitor
transition, platform file picker, packaged build, or physical device. Those remain
part of normal M8 qualification. Restart after changing OS text preferences if
the running Qt application has not adopted the new font.

The agent reviews chooser/organizer reflow, the densest editor footers, scan
comparison, package summary/recovery, high-contrast focus and canvas guidance, and
static work status. Keyboard traversal reveals offscreen controls. Escape leaves
each review without applying changes. Completion details and limitations are in
[the task result](../development/pws0703-result.md).

No human test is being handed off by this helper. A future human regression
session must follow the [human-testing policy](../development/human-testing.md).
The planned short journey is one prepared synthetic case, three display views,
one contrast view and one activity/cancel view, with environment collection and
result recording automated. Budget approximately 12–18 deliberate actions and
4–6 active minutes, including launch, observation, recording and exit. Measure the
complete prepared journey before any actual handoff; preserve valid observations
and provide pause/problem recovery rather than repeating a completed review.
