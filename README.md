# DropNet

**Experimental store-carry-forward text communication between participating devices.**

DropNet is initiated by Adrian Ripley. A participating node keeps a message when
its recipient is unreachable and forwards it during a later encounter. Delivery
requires a suitable sequence of contacts; disconnected communities still need a path.

Original DropNet code, tests and documentation are **GPL-3.0-or-later**.
See [COPYING.md](COPYING.md), [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Reproducible demo

The Python demo needs Python 3.11 or newer and its standard library only.
No AI account, API key, Internet service or package installation is needed to run it.

```sh
git clone https://github.com/AdrianRipley/DropNet.git
cd DropNet/simulator
python3 -m unittest discover -s tests -v
python3 scripts/run_local_demo.py
python3 scripts/run_experiments.py --output scripts/outputs/my-run
python3 scripts/validate_campaign.py scripts/outputs/my-run
```

Use a new output directory for each run. The local demo uses UDP sockets bound to
127.0.0.1, independent processes and SQLite databases. It verifies delivery via a
restarted relay, duplicate suppression and fallback after lost inventory summaries.
The full campaign runs 2,160 experiments and compares every historical baseline
field using the committed synthetic fixtures. Old outputs are never overwritten.
See [simulator documentation](simulator/README.md) for assumptions and interpretation.

## Android development demo

The Android source is in [android/](android/) with a shared Kotlin core, compact
summary conformance tests, bounded fragmentation and an experimental foreground
BLE transport. Build and device-test instructions are in [docs/ANDROID.md](docs/ANDROID.md).
The app is for synthetic text only. Radio interoperability and background operation
require physical-device testing; an APK build is not evidence of those properties.

```sh
cd android
# Set JAVA_HOME to a JDK 17 installation and ANDROID_HOME to your Android SDK.
sh gradlew :core:test :app:assembleDebug
```

First builds download pinned dependencies from official repositories. The Python
demo remains runnable offline. The simulator's assumed DATA sizes are distinct
from the Android binary format: see [protocol specification](docs/PROTOCOL.md).

## Validation and limits

The public Python package was checked with 66 tests, a complete 2,160-run campaign,
12 independently accounted traces and exact regression against 1,620 previous runs.
In one synthetic case (20 nodes, 40 messages, 240 encounters, 64 KiB/node, no loss),
compact summaries delivered 83.67% compared with 64.67% for full inventories and
58.33% for epidemic forwarding. These values are **not measured Bluetooth performance**.
The compact policy does not dominate every condition; the reports include failures
and total control/data traffic.

The Android APK and its instrumentation APK compile locally; all 19 Kotlin core
tests pass. The automated Android workflow separately checks native SQLite
persistence on an emulator. Consult the workflow result for that run; emulator
success does not validate Bluetooth radio behavior.

Reproducibility here means checked source, explicit build instructions and
repeatable simulation results. Debug APK signatures and build timestamps may
differ between machines; byte-identical APK reproduction is not claimed.

This is an early research and development release. It has no end-to-end encryption,
authentication, anonymity guarantee, radio-range measurement or energy measurement.
It is not ready for sensitive messages or emergency use. Retained deduplication
state is bounded; do not infer unlimited exactly-once delivery.

## Participate

Read the [manifesto](MANIFESTO.md), [roadmap](ROADMAP.md),
[contribution guide](CONTRIBUTING.md) and [governance proposal](GOVERNANCE.md).
Use issues for reproducible problems and Discussions for project ideas.
See [SECURITY.md](SECURITY.md) for responsible reporting.

GitHub hosts development; it is not a proposed requirement for participating
devices to communicate. Source and experiments can be mirrored elsewhere.
