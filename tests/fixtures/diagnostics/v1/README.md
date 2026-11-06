# Public synthetic support sample

Task: PWS-0805. `support-sample.zip` is a cleared, generated support sample,
not an actual incident or observation. It contains no document or image.
The fixed no-input recipe is `tests/helpers/diagnostic_bundle.py:sample_preview`.
Its timestamp, internal UUID, version, code, counts and duration are synthetic
literals; the recipe reads no environment, case, log, person or external file.

Expected behavior: the archive opens with exactly `summary.txt` and
`allowlist.txt`, with stored compression and fixed member timestamps. The summary
shows two synthetic records, one completed operation and a generic failure code;
these are demonstration values, not claims that a real operation ran. The
standalone `allowlist.txt` is byte-identical to the archive member.
`index.json` binds the archive and both members by byte count and SHA-256.

Archive SHA-256:
`4f5bb7b36f1b58f36901f31c18b721a6875a44bc731fb8da3779e39b77662304`.
The canonical unit test reopens every member, verifies the allowlist and recorded
hashes, and regenerates the complete archive byte-for-byte. No existing PDF/image
corpus index, original fixture, accepted evidence or golden was refreshed.
