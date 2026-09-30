# Bounded resource behavior

PaperWorkShell runs document processing in a restricted worker and uses a bounded
software profile. No particular CPU, RAM size, GPU, disk, or physical device is
certified by these tests. A discrete GPU is not required.

The constrained profile allows one heavy operation at a time, a combined 128 MiB
thumbnail/preview cache, 512 MiB per worker process, 768 MiB for its complete Job,
512 MiB of private temporary storage, and a 256 MiB free-space safety reserve.
Operations can reject a document below a headline input limit when its decoded
pixels and temporary working buffers would exceed the selected budget.

## JPEG and PNG

- Each source represents one page. Images are copied and hashed before full decode.
- Encoded files are at most 512 MiB; either dimension is at most 32,768 pixels.
  Pixel, decoded-byte, expansion, metadata, memory, and time limits also apply.
- The preliminary ceiling is 268,435,456 source pixels and 1 GiB estimated decoded
  bytes. The actual worker planner applies stricter memory and pixel limits before
  allocation; these ceilings are not a promise that an image of that size opens.
- Metadata has a 16 MiB aggregate ceiling and at most 4,096 structural records.
  A compressed PNG text chunk expands to at most 1 MiB. Unsupported embedded color
  profiles are rejected before native decode.
- Image preview output is at most 4,000,000 pixels, with a 60-second worker deadline
  and 45-second CPU ceiling. The application remains cancelable during worker setup.
- Preview generation processes one page at a time and releases full-resolution
  pixels after producing the bounded result. Transparent areas become white.

See the [enforced resource policy](docs/performance/LOW_RESOURCE_POLICY.md),
[image intake contract](docs/architecture/image-intake.md), and
[known limitations](KNOWN_LIMITATIONS.md). These are development facts; packaged
Windows support and release evidence remain in the [release documents](docs/release/README.md).
