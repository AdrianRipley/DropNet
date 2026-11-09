# Public source packaging

This directory is the GPL-3.0-or-later release of the DropNet Python reference
simulator, binary summary codec, local UDP probe and tests. See the repository's
license. This release uses only the Python standard library.

The following changes were made when preparing the public source copy:

1. Added SPDX license identifiers to the project Python files.
2. Replaced references to historical output directories with committed synthetic
   fixtures under `fixtures/baselines/`. The v0.1 and v0.2 run metrics and their
   common 90 generated input files are preserved byte-for-byte.
3. Changed campaign manifests to include the public specification, documentation,
   conformance vectors and baseline fixtures. Private coordination material and
   local agent instructions are excluded.
4. Normalized absolute command arguments in generated campaign metadata to
   repository-relative paths, or `<external-path>` when outside the simulator.
   Both separate arguments and `--option=/absolute/path` syntax are covered by
   a regression test. This affects provenance display only, not experiment
   inputs or execution.
5. Updated documentation for a clean public checkout and added a CI workflow.

No forwarding, storage, transport-loss, timing, random-seed, metric, or statistical
algorithm was changed for publication. `SOURCE_PROVENANCE.json` records the SHA256
of each pre-publication source/specification file using relative names, allowing
the packaging changes to be distinguished from protocol changes.

AI-assisted development and review were used. Those reviews are not independent
security certification; executable tests and saved experimental evidence are the
validation basis. Private prompts, credentials, development-machine information
and outreach material are not part of this source release.

The synthetic simulator and local UDP probe have different traffic models: the
simulator retains its assumed 96-byte DATA metadata, while the UDP probe counts
its actual serialized application datagrams. Neither measures Bluetooth range,
background operation or energy consumption. The UDP probe is not encrypted or
authenticated and should use synthetic content only.
