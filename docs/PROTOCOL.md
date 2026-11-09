<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# DropNet v0.4 demonstration protocol

This is an experimental, unauthenticated plaintext protocol for synthetic test
messages. It is not suitable for private messages or emergency reliance. The
Android implementation is a new transport experiment; the earlier Python
simulation results are not measurements of this implementation.

The reusable JVM implementation is in
[`android/core`](../android/core/src/main/kotlin/org/dropnet/core/). All integers
are big-endian. Reject unknown versions, nonzero reserved fields, inconsistent
lengths and trailing bytes. Parsers do not interpret arbitrary serialized Java
objects, JSON code or executable content.

## Message identity and time

Message, source and destination identifiers are UUIDs, encoded as 16 raw bytes
each. The Android node must keep its own UUID across process restarts; a UUID is
an identifier, not proof of a person's identity. IDs in inventory fingerprints
use the lowercase canonical UUID string, including hyphens, encoded as UTF-8.

Creation and expiry are nonnegative signed 64-bit UTC epoch **milliseconds**.
Require `0 <= created < expires`. Network acceptance requires
`created <= now < expires`; equality at expiry rejects the message. Clock skew
between phones can therefore reject messages or alter their useful lifetime.
The prototype does not solve clock synchronization or authenticated time.

The original source copy has zero hops. Preparing each network copy increments
hops by one, including the final destination hop. A sender cannot forward when
`hops == hopLimit`. Both fields are unsigned bytes: `0 <= hops <= hopLimit <= 255`.
A received network copy requires `hops >= 1`. A hop limit of zero permits only
local handling, never network forwarding.

These are explicit v0.4 wire choices: the simulation uses abstract seconds and
variable string IDs, and its local UDP probe uses JSON and a wider hop bound.
Neither older DATA representation is byte-compatible with v0.4 DATA. The compact
inventory representation below **is** byte-compatible with v0.3.

## DATA representation

`DataCodec` encodes exactly 74 header bytes followed by at most 512 bytes of
strict UTF-8 text. Unpaired UTF-16 surrogates on encoding and malformed UTF-8 on
decoding are rejected. NUL and other control characters have their ordinary
UTF-8 encoding; there is no JSON escaping expansion.

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 4 | ASCII `DNDT` |
| 4 | 1 | Version: `1` |
| 5 | 1 | Reserved: `0` |
| 6 | 16 | Message UUID |
| 22 | 16 | Source UUID |
| 38 | 16 | Destination UUID |
| 54 | 8 | Creation, signed 64-bit epoch milliseconds |
| 62 | 8 | Expiry, signed 64-bit epoch milliseconds |
| 70 | 1 | Hops, unsigned |
| 71 | 1 | Hop limit, unsigned |
| 72 | 2 | Text byte length, unsigned, `0..512` |
| 74 | variable | UTF-8 text, exactly the declared length |

Maximum DATA length is **586 bytes**, before the app envelope, fragmentation,
GATT/ATT, link-layer overhead or retransmission. The simulator's assumed
96-byte metadata field must not be used to report actual Android wire cost.
`decode()` validates structure for persistence/replay; `decodeForReceive()` also
enforces network hop and current-time acceptance. The receiver must apply its
storage and dedup rules after successful parsing.

## Compact inventory representation

The format is the v0.3 `DNCS` summary, maintained for cross-language conformance:

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 4 | ASCII `DNCS` |
| 4 | 1 | Version: `1` |
| 5 | 1 | Kind: `1` |
| 6 | 2 | Unique token count, unsigned |
| 8 | 8 | Salt, raw unsigned 64-bit value |
| 16 | `8 * count` | Strictly increasing, unique 64-bit tokens |

A token is the first eight bytes of
`SHA-256(salt_as_8_big_endian_bytes || UTF8(message_id))`. Sorting compares the
token bytes as **unsigned** values; Kotlin's signed `Long` is only a container
for those bits. All salt bit patterns, including `ffffffffffffffff`, are valid.
The generic codec accepts ID strings containing `1..128` UTF-8 bytes so it can
read the existing Python fixtures; Android messages use canonical UUID strings.

`planFrames(ids, salt, quota)` sorts and deduplicates tokens. An empty set or a
quota below 24 bytes produces no frame. A complete summary is repeated twice
when both copies fit the quota. Otherwise one bounded summary contains the
lowest tokens that fit. This schedule is decided before transmission; it never
uses hidden knowledge of whether a receiver saw the first copy. Repetition costs
bytes even if the first copy succeeded. A salt should change for each contact.

Each received direction is useful independently. An unavailable summary means
"suppress no candidate". A partial summary does not claim that omitted IDs are
absent. Candidate and local inventory snapshots come from the local queue and
retained, unexpired dedup entries; a historical delivery audit is not an oracle.

Fingerprints have collision risk. A collision can suppress a new message during
that exchange; this is not exact set reconciliation, authentication, encryption
or a privacy guarantee. Sampling and lost summaries can let duplicate DATA pass.
Full message-ID deduplication is still required at the receiver.

The generic codec bounds a frame at 65,535 tokens for compatibility. The Android
transport bounds an entire app frame at 1,024 bytes and uses a much smaller
summary quota. Do not pass the generic codec's maximum directly to GATT.

## Fragmentation and bounded reassembly

The Android app first wraps a body in a one-byte app type:

| Type | Body |
| --- | --- |
| `1` HELLO | Sender node UUID, 16 bytes |
| `2` SUMMARY | One complete `DNCS` summary |
| `3` DATA | One complete `DNDT` message |
| `4` ACK | Message UUID (16 bytes), then status (1 byte) |

ACK status `0` means rejected, `1` durably accepted by a relay, `2` durably
delivered by the recipient and `3` duplicate already retained. Status `2` is
recognized as recipient delivery only if the peer UUID equals the message's
destination UUID. HELLO and ACK are unauthenticated; this comparison provides
protocol consistency, not identity verification. There is no forwarded end-to-end
receipt when the destination is reached by a different relay at a later contact.

`FragmentCodec` divides an entire app frame into independently framed ATT values.
The caller supplies the permitted characteristic value size for its connection,
defaulting to **20 bytes** until a larger size is actually available. The codec
allows values between 13 and 512 bytes; a value too small to meet the maximum
fragment count rejects the transfer. It does not assume MTU negotiation succeeds.

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 2 | ASCII `DF` |
| 2 | 1 | Version: `1` |
| 3 | 1 | Reserved: `0` |
| 4 | 4 | Transfer ID, raw 32-bit value scoped to the peer/contact |
| 8 | 2 | Zero-based fragment index, unsigned |
| 10 | 2 | Fragment count, unsigned, `1..128` |
| 12 | variable | Nonempty fragment payload |

An app frame contains `1..1024` bytes. At the default 20-byte value size, 8 bytes
per fragment carry app content. A maximum DATA frame plus its one-byte app type
requires 74 fragments. This is a byte-count calculation, not a throughput or
battery measurement.

The default reassembler admits at most eight incomplete transfers globally and
two per peer. Each is bounded at 1,024 payload bytes and 128 fragments. Payload
buffering is therefore at most 8,192 bytes at those defaults, in addition to
bounded object/array metadata. Capacity exhaustion rejects the new assembly.

Fragments may arrive out of order. Equal duplicate fragments do not consume more
payload memory and do not refresh expiry. Conflicting duplicates or fragment
counts discard the corresponding assembly. A frame is emitted only after every
index is present. The receiver then validates the complete app type and body;
assembly completion alone does not imply valid DATA or successful storage.

Incomplete state expires ten seconds after the **first** fragment, using a
monotonic clock. It is also cleared when the peer/contact closes. Calls must
provide nondecreasing monotonic timestamps. Partial assemblies are deliberately
not persistent; after disconnect/restart, a sender retries a complete frame.

## Retry and acknowledgment responsibilities

The core `BoundedTransfer` helper limits a complete-frame transmission to two
attempts by default, with at most three configurable attempts. Its default ACK
wait is three seconds and its total lifetime is fifteen seconds. These are test
parameters, not validated radio timings. It exposes a fixed fragment list for
each attempt; the Android transport must serialize GATT operations through their
completion callbacks and stop pending work on disconnect, rejection or timeout.
The helper's wait begins when an attempt is emitted; callers must not poll it to
enqueue overlapping attempts while the current fragment queue is still active.

A successful GATT write or notification callback is not evidence that a relay
persisted a message, and is not an end-to-end delivery receipt. A positive app
ACK must follow validation and a completed storage/delivery transaction. Match
its message UUID against the outstanding transfer before treating it as success,
and pass current monotonic time to `acknowledge(nowMillis)`. The helper rejects
late ACKs after lifetime or the final exhausted attempt's deadline even when its
poll timer was delayed.
Relay custody, recipient delivery and duplicate acceptance must remain distinct
diagnostic states. ACKs are currently unauthenticated and can be forged.

An ACK may be lost after successful storage. A later complete-frame retry can
therefore reconstruct the same DATA again; the receiver's retained full-ID dedup
state prevents another application delivery while retained. The receiver can ACK
that duplicate again. Sender retention after relay custody and bounded dedup
eviction must be explicit local policies; this protocol does not distribute a
global delivery ledger or guarantee exactly-once delivery forever.

## Current Android demonstration profile

The app serves one peer at a time. It snapshots the queue when GATT is ready,
uses a 128-byte compact-summary body quota per direction, keeps at most two
received summaries, and waits two seconds for summaries before offering data.
This is a fixed demo interval with independent fallback, not a negotiated
guarantee that summaries have finished arriving.

ATT characteristic values remain fixed at 20 bytes in this profile. The outbound
queue is capped at 512 fragments, sent characteristic-value bytes at 64 KiB per
contact, contact lifetime at 90 seconds and a pending ATT operation at ten
seconds. Application-ACK transfers use at most two attempts, a fifteen-second
wait and a forty-five-second total lifetime from enqueue. Retry polling occurs
only after the current output queue is empty. These app parameters override the
core helper defaults described above and require physical testing before tuning.

The local queue is limited to 64 KiB of encoded DATA, dedup entries to 256 plus
expiry, and the inbox to 100 messages. The UI creates synthetic messages with a
24-hour TTL and hop limit eight. Dropping a retained ID or an inbox record is a
local bounded-storage decision, not a signed deletion or global delivery signal.

Traffic counters must name their boundary: characteristic-value bytes include
the 12-byte fragment headers and app bodies, but exclude ATT/link overhead and
controller retransmissions. The 128-byte summary **body** quota excludes app and
fragment headers. Thus Android traffic is not directly comparable to the earlier
simulation's control/body byte accounting without a new measured model.

## Conformance and test scope

`android/core/src/test/resources/compact_vectors.json` preserves four v0.3
fixtures. The adjacent TSV is a fixed, language-neutral extraction loaded by JVM
tests without adding a JSON dependency. It covers empty inventory, Unicode IDs,
multiple unsigned tokens and a maximum-length UTF-8 ID. `data_vector.hex` is an
independently constructed v0.4 DATA vector. Test expectations are saved constants,
not regenerated through the implementation under test.

JVM tests cover malformed fields, UTF-8 bounds, expiry/hops, missing/reordered
fragments, duplicate conflicts, memory/time bounds and bounded retry. They do
not validate a Bluetooth controller, Android background execution, physical
power-loss recovery, range, congestion, interoperability or adversarial safety.
Any build or emulator result must be reported separately from tests on real
phones. See [THREAT_MODEL.md](THREAT_MODEL.md).
