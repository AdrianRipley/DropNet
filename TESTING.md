<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Help test the DropNet demo

Start with [v0.4.0-demo](https://github.com/AdrianRipley/DropNet/releases/tag/v0.4.0-demo),
which provides a debug Android APK, source, checksums and validation reports.
Use synthetic messages only: there is no encryption or authentication. The app
participates only while its screen remains open. It is not an emergency messenger.

## Three-phone relay experiment

Use three compatible Android phones (Android 8/API 26 or later). Follow the
[Android guide](docs/ANDROID.md), including permissions and device limitations.

1. Install the same identified release APK on A, B and C. Record its version/checksum.
2. Keep C paused or unreachable. Create a synthetic message on A addressed to C.
3. Start participation on A and B; wait for B's saved-for-forwarding status.
4. Stop or turn off A. Restart B's app, then explicitly restart participation.
5. Bring B and C together with both app screens open. Record whether C receives it.
6. Repeat the encounter and observe duplicate suppression. Retention is bounded;
   this does not establish exactly-once delivery forever.

Report each attempt, including failures. A relay acceptance ACK is not proof that
C received the message. This experiment does not test a propagated final receipt
back to A. A single success does not establish general compatibility or reliability.

## Independent Python reproduction

On another computer, follow the public [README](README.md) and
[simulator guide](simulator/README.md) for tests, local UDP demo and campaign.
Record the source revision, OS, Python version, commands and outcomes. Use new
output directories; preserve previous runs. No AI service or API key is required.

## Report format

Open an issue using the experiment template, or join [Discussions](https://github.com/AdrianRipley/DropNet/discussions).
Include:

- Release/tag or commit and APK checksum if applicable.
- Phone models, Android versions, role A/B/C, permissions and relevant settings;
  for Python, OS and runtime version instead.
- Exact steps, which devices were participating, and restart/stop timing.
- Expected result and observed result for every attempt, including failures.
- Repetition count, elapsed time and whether duplicate application copies appeared.
- Minimal diagnostic evidence with synthetic content and personal identifiers removed.

If a value was not measured, say so. Application bytes, ATT fragments and total
radio traffic are different quantities. Do not present simulation results as
Bluetooth range or battery measurements. Follow [SECURITY.md](SECURITY.md) for
non-public vulnerability reports.

## Other useful contributions

Android/BLE developers can investigate reproducible discovery, transfer and
persistence failures. UX contributors can check whether pause and delivery states
are understandable. Security contributors can review [the threat model](docs/THREAT_MODEL.md)
and propose offline contact verification and end-to-end protection using maintained
components. A proposed design or AI review is not a security certification.

Watch the repository for updates or follow [AdrianRipley](https://github.com/AdrianRipley).
No purchase, donation, star or follow is required to contribute.
