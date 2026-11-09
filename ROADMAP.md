# Proposed roadmap

Milestones describe results to work toward, not release dates or completed work.

## Current priorities after v0.4.0-demo

The [demo release](https://github.com/AdrianRipley/DropNet/releases/tag/v0.4.0-demo)
now includes Android APK, source and validation reports. The foreground BLE path
is implemented; physical-phone interoperability, range and energy remain unverified.
Emulator persistence tests are separate evidence from radio tests.

1. Run the three-phone relay experiment using the same identified APK and synthetic text.
2. Obtain independent reproduction of the Python demo and documented checks.
3. Review message states: local save, relay acceptance, recipient receipt and expiry.
4. Design identity verification and end-to-end protection in parallel, comparing existing components.
5. After the foreground baseline, investigate screen-off/background behavior and measure energy.

See [TESTING.md](TESTING.md) for the first contribution and reporting checklist.
Local diagnostics should use explicit units and a separate schema from simulated
traffic; the simulator validator does not validate physical BLE measurements.

## 1. Reproducible source release

- GPL-3.0-or-later is selected; source provenance and third-party notices accompany the release.
- The released package contains the simulator, tests, specification and required regression fixtures.
- Release reports record successful local tests and the full experiment campaign.
- The private reporting channel is documented in [SECURITY.md](SECURITY.md).

The source package includes the simulator, tests and historical synthetic fixtures.
Local reproduction passes. Independent reproduction from the public repository
remains a welcome contribution; no private coordination records are required.

## 2. Compare existing approaches

Document which components can be reused. Compare neighbour discovery, delayed
forwarding, persistence, trust assumptions and Internet dependencies. Cite exact
versions and distinguish documentation from independently reproduced behaviour.

Completion means a justified choice of components and a list of unresolved questions.

## 3. Demonstrate a persistent relay on devices

A creates a message for C while only B is reachable. B stores it. A goes offline,
B restarts, and a later encounter between B and C delivers the message.

Record compatible devices, operating systems, permissions, delivery evidence,
actual transferred bytes and timing. Extend the experiment to background operation
and energy measurement. Use synthetic content while protections are incomplete.

## 4. Design and review protections

Specify adversaries, keys and contact verification, metadata exposure, spam limits
and behaviour after device loss. Use established cryptographic components.
Security work can begin alongside the earlier milestones; protected personal
communication requires implemented and reviewed protections.

## 5. Organize small local pilots

Once a device prototype is usable, recruit groups able to meet physically.
Document participation and pause controls, diagnostic collection and test procedures.
Separate message creation, relay receipt, confirmed recipient delivery and expiry.

Completion means repeatable reports from independent groups, including failures
and limitations, rather than download or follower counts alone.
