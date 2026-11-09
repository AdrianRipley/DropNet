# Immutable synthetic regression fixtures

- `v0.1/runs.json`: 1,080 saved direct/epidemic simulation runs.
- `v0.2/runs.json`: 1,620 saved direct/epidemic/full-inventory simulation runs.
- `inputs/`: 90 synthetic input files, identical in both historical campaigns.
- `SHA256SUMS.json`: SHA256 of the two run files and all 90 input files.

These files contain generated messages, synthetic contact schedules and run
metrics. They contain no private conversations, devices or radio measurements.
They are copied byte-for-byte from the completed historical experiments; no
output values were regenerated or rounded for publication. Each input combines
one of 30 seeds with 40, 100 or 240 contacts. Loss and capacity sweeps reuse the
same exogenous input.

The full public campaign reproduces every original field/counter in these runs
and checks the newly generated input bytes. The independent validator verifies
the fixture hashes as well as both historical comparisons. Shared inputs are
stored once, rather than duplicated in both version directories.

These fixtures are distributed with DropNet under GPL-3.0-or-later.
