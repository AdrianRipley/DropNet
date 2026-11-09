# Android demonstration

The native Android application is a **plaintext engineering demo**, not an
emergency messenger or a private communications product. Use synthetic text.
It implements a real BLE GATT transport path, but successful compilation and
emulator tests do not establish interoperability, range or reliability on phones.
The application has **no Internet permission** and no background service.

## Build

Requirements: JDK 17, Android SDK platform `android-36.1`, Build Tools `36.1.0`.
The checked-in wrapper selects Gradle 9.4.1; the Android Gradle plugin is 9.1.1.
The first build downloads normal Maven/Gradle dependencies. SDK auto-download is
disabled: install the stated SDK components deliberately before building.

```sh
cd android
export ANDROID_HOME=/path/to/your/android-sdk
export JAVA_HOME=/path/to/your/jdk-17
sh gradlew :core:test :app:assembleDebug :app:assembleDebugAndroidTest
```

The debug APK is `app/build/outputs/apk/debug/app-debug.apk`. This is a debug
signature, not a stable release signing identity. Do not distribute a private
signing key or local `local.properties`, caches or SDK directories.

The core tests run on the JVM without a phone. They include byte-level protocol
vectors, reassembly bounds, malformed data, expiry, duplicate fragments and
bounded retries. Build outputs and test reports are generated separately from
the source tree's tracked files.

## Run the native persistence test

In the app, tap **Esegui test A → B → C**. The test creates three disposable,
independent SQLite databases, persists a relay copy, closes the source and
reopens the relay, delivers once, checks summary membership and recipient dedup
after reopening. It also checks fragmented codec round trips and rejects
malformed DATA. It does not enable Bluetooth and cannot be used as radio evidence.
Only this test's uniquely named temporary databases are removed afterwards.

For a running Android emulator or connected test device:

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w org.dropnet.demo.test/org.dropnet.demo.SelfTestInstrumentation
```

Success explicitly reports `dropnet_status=passed` and a `PASS:` explanation.
This runner is deliberately independent of AndroidX and tests the native SQLite
and codec path. It does not drive or emulate BLE. A test failure reports
`dropnet_status=failed` and an error; do not infer success merely from process exit.

## Foreground phone experiment

Install the same APK on compatible participating Android phones (Android 8/API 26
or later). No unmodified nearby device becomes a relay. On older Android versions,
BLE scanning also depends on the system's location setting.

1. Copy C's UUID from its app and enter it on A as the destination. Save a
   synthetic message. The UI's initial TTL is 24 hours and hop limit is 8.
2. Keep C out of range or paused. Start participation on A and B and keep their
   screens open. Wait for B's **saved for forwarding** status.
3. Pause or turn off A. Restart B's app to exercise the real persisted queue.
4. Bring B near C, start participation on both, and check C's received message.
5. Repeat the encounter and check that C receives no second application copy.

Leaving the app screen stops scanning, advertising and active connections. Opening
another app to share an identifier also pauses participation; restart explicitly
when returning. No screen-off or always-on behavior is promised. Bluetooth power
and runtime permissions remain explicit device/user choices.

## Implemented transport and storage

- Advertising and filtered scanning identify the DropNet service UUID
  `73ead001-d151-4c5e-a2d8-a4bfe374a300`. Unsupported advertising is reported; a
  device may still act as a scanning client.
- GATT write-with-response carries client-to-server fragments; notifications
  carry server-to-client fragments after CCC subscription. The app uses a
  conservative fixed ATT value size of 20 bytes and does not assume a negotiated
  larger MTU. Characteristic callbacks pace the fragment queue.
- A HELLO exchanges node UUIDs. Compact summaries use the shared Kotlin/Python
  codec and a 128-byte raw summary quota per direction. A missing summary after
  a two-second phase causes ordinary forwarding, not a failed whole contact.
- The application envelope distinguishes HELLO, SUMMARY, DATA and ACK; shared
  fragments bound reassembly. DATA uses the actual native binary format, not the
  simulator's assumed 96-byte metadata. GATT, fragmentation, HELLO and ACK costs
  are additional and must not be compared as if they were simulation metrics.
- An application ACK is queued **only after SQLite's acceptance transaction
  returns**. A GATT operation callback alone never means durable storage.
  Status 1 means relay acceptance, 2 direct destination acceptance, 3 a retained
  duplicate and 0 rejection. A relay ACK does not become an end-to-end receipt.
  The sender retains its copy until expiry; no global deletion receipt exists.
- There is at most one active peer per device, a 90-second contact lifetime,
  64-KiB send and receive caps, at most 512 queued outgoing fragments and bounded
  incomplete reassembly. Individual GATT operations time out after ten seconds.
- A DATA transfer has at most two whole-frame attempts, a 15-second ACK timeout
  from attempt enqueue and a 45-second total lifetime. Retry polling waits until
  the GATT output queue is idle. Errors/disconnection keep the durable message
  for another encounter. These are engineering defaults, not measured radio values.
- The relay queue is capped at 65,536 actual encoded DATA bytes. Dedup entries
  expire with the message and have a separate limit of 256; queued IDs also
  suppress duplicates. The inbox retains the latest 100 messages independently.
  After dedup eviction a still-live message can be delivered again. SQLite file
  size and journal overhead are not equal to the logical queue limit.
- Native IDs are UUIDs, timestamps are UTC epoch **milliseconds**, and binary hop
  fields are uint8. This differs from the Python simulator's generic strings and
  abstract second-based times. The summary byte format is shared; native DATA is
  a separately versioned wire format. See [PROTOCOL.md](PROTOCOL.md).

The demo does not implement encryption, signatures, trusted contacts, source
authentication, anti-abuse admission, end-to-end receipt propagation, battery
measurement or production background operation. UUIDs and compact fingerprints
are not authentication. A nearby malicious participant may lie about inventory
or acknowledgments; see [THREAT_MODEL.md](THREAT_MODEL.md).

## License and platform references

DropNet source is GPL-3.0-or-later, without warranty. The app's **Sorgenti e licenza**
screen includes the full license and repository URL. Kotlin's standard library
is Apache-2.0; its license is also included. The Gradle wrapper is Apache-2.0 and
is a build tool, not DropNet application code.

The platform API references used for this implementation are Android's
[BLE data transfer guide](https://developer.android.com/develop/connectivity/bluetooth/ble/transfer-ble-data),
[GATT server callbacks](https://developer.android.com/reference/android/bluetooth/BluetoothGattServerCallback),
and [build configuration guide](https://developer.android.com/build).
