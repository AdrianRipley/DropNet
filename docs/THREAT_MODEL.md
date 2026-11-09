<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Threat model for the v0.4 demonstration

The v0.4 app is a foreground engineering demonstration using **synthetic
plaintext messages**. No security audit or real-device radio validation is
claimed. Do not use it for private communications, sensitive locations,
verified public alerts or emergency dependence.

## Assets and boundaries

Relevant assets are message contents and metadata, stored queue/dedup state,
device availability, radio/battery budget and the user's ability to stop the
experiment. The trust boundary is the BLE peer: received bytes, UUIDs, summaries,
timestamps, hop counts and acknowledgments are untrusted input. A device being
nearby or completing a GATT operation does not authenticate its owner.

The app's local test controls and JVM tests establish protocol behavior under
declared inputs. They do not establish that an arbitrary remote peer follows the
rules or that an operating system keeps the process alive.

## Implemented containment

- Fixed versions, strict field/length checks and UTF-8 validation reject malformed
  messages. UUID identifiers and bounded text avoid unbounded identifier/text
  allocations in the DATA format.
- Message expiry, hop bounds, local storage quotas and retained dedup state limit
  cooperative forwarding. These are local rules; an attacker can lie about them.
- Fragment size/count, total assembled size, simultaneous incomplete transfers,
  per-peer assemblies and first-fragment timeouts bound reassembly memory.
  Duplicate fragments cannot indefinitely extend an assembly's lifetime.
- Retries are finite. A missing summary causes ordinary forwarding rather than
  blocking every DATA transfer. Missing ACKs do not permit unbounded retransmission
  within one contact.
- Protocol parsers do not execute remote code or deserialize arbitrary objects.

These controls reduce particular accidental failures and some resource-amplifying
inputs. They are not a proof against denial of service. An attacker can repeatedly
occupy the allowed quota, reconnect under new transport identities, send valid
garbage or force the device to do bounded work many times.

## Explicitly absent protections

**Confidentiality:** DATA text is plaintext. Relays and observers with access to
the traffic or app database may read it. No end-to-end encryption is implemented.

**Authentication and integrity:** UUIDs are labels, not public keys. There are no
message signatures, authenticated peer identities or authenticated ACKs. A peer
can forge a source/destination, message ID, control summary or receipt, alter text
or suppress forwarding by claiming to possess a message. The protocol does not
prevent malicious rewriting of expiry or hop fields.

**Privacy and unlinkability:** source/destination UUIDs, message sizes, timing and
contact metadata can expose relationships. Salted 64-bit inventory fingerprints
are comparison tokens, not encryption; a public salt does not prevent testing
guessed IDs. Advertising/connection behavior may be observable. Rotating a salt
does not provide anonymity.

**Sybil and flood resistance:** no admission authority, anti-Sybil mechanism or
per-human fairness rule exists. Local quotas do not prevent many cooperating or
spoofed peers from using radio time, storage or battery.

**Exactly-once delivery forever:** deduplication is bounded by retention and
expiry. If a live ID is evicted and later reappears, it can be accepted again.
Same-ID conflicting contents are not protected by a signed content commitment.

**Clock trust:** expiry uses each phone's wall clock. Clock errors or deliberate
timestamp manipulation can change acceptance. Monotonic local assembly/retry
timers avoid ordinary wall-clock jumps but do not make remote time trustworthy.

**Availability and radio behavior:** a working APK, JVM test or local self-test
does not prove background execution, discovery, BLE role support, usable range,
delivery probability or energy consumption. Disconnected populations still need
a physical sequence of participating nodes to transport messages.

## Before private or public-safety use

Define authenticated identities and offline key exchange, choose established
end-to-end encryption/signature protocols and maintained cryptographic libraries,
and obtain an independent review. Do not invent an encryption scheme in this
prototype. Public alert authenticity must be distinguished from whether an alert
is true; signatures alone would not establish the truth of a report.

Run real-device tests with explicit consent and synthetic content first: relay
restart with the source absent, partial-transfer interruption, lost ACK, repeated
contacts, clock skew, storage exhaustion, screen-off/background behavior and
measured traffic/energy. Publish failures as well as successes and identify the
tested devices/OS versions. Findings from the synthetic simulator must remain
separate from those measurements.

Keep user start/stop control clear and collect only the diagnostics needed for
the experiment. An authenticated, encrypted design and physical tests would still
need further abuse, privacy and reliability evaluation before safety claims.
