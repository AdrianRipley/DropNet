# DropNet reference simulator v0.1

Goal: complete a reproducible offline store-carry-forward experiment before hardware exists.
Use Python >=3.11 standard library. This reference core validates protocol logic;
an eventual native Android implementation will need a port and shared conformance vectors.
Do not claim this Python module is directly usable as native Kotlin.

Model:
- Discrete chronological encounter events between named nodes; no hidden global route oracle.
- Message IDs unique, source/destination, creation and absolute expiry (seconds), UTF-8 text <=512 bytes,
  hop count and configurable hop limit. Fixed, explicitly assumed metadata size for wire accounting.
- Unicast only in this milestone. No encryption, signatures, anonymity, public SOS trust, actual BLE,
  battery model or physical mobility calibration. Clearly mark these unimplemented.
- Node queue persisted in SQLite on disk, reopen on restart; bounded payload+metadata storage.
  Explicit drop-new policy; expire before admissions. Bound dedup state too (e.g. expiry + capacity).
  Dedup means no duplicate application delivery while retained; explain bounded-cache tradeoff.
- Baseline epidemic forwarding among honest participants versus direct-delivery baseline.
  Budget contact bytes, finite duration and rate; failed transfers consume bytes/time.
  No partial durable transfers (all-or-nothing abstract transport), configurable loss probability.
  Sequential half-duplex transmission; do not allow packet to hop backwards in time.
  Destination acceptance must occur strictly before expiry. Include deterministic contact ordering.
  Transport counters separate attempts, accepted transfers, duplicates, lost transfers, byte budget,
  TTL/hop/storage rejection and local delivery. Delivery recorded at recipient, not relay receipt.
  Store acknowledgments need not propagate globally; do not make source know delivery magically.

Mandatory deterministic scenarios and assertions:
1 A->B, A goes offline, B closes/reopens persistent store, then B->C; one delivery, exact latency.
2 disconnected components: zero delivery.
3 expired before encounter AND expires during transfer: zero delivery.
4 repeated contacts/cycles: no duplicate app delivery; retained dedup survives restart.
5 tiny storage: explicit rejection, storage bound upheld.
6 100% link loss: zero delivery, retry bytes accounted.
7 zero/insufficient contact budget: no delivery.
8 hop limit enforced; destination-hop semantics documented.
9 malformed/oversize UTF-8 message rejection (512 bytes, not characters).
10 direct delivery as comparison, and no same-contact multi-hop artifact.

Stochastic campaign: at least 30 paired seeds per condition, 20 nodes, 40 generated messages,
fixed horizon, synthetic encounters with several contact densities and loss probabilities.
Both policies must use identical exogenous contacts/messages per seed. Specify loss RNG coupling;
do not imply exact same random losses if algorithms consume randomness differently.
Report per-run counts and aggregate delivery fraction, run-to-run spread and 95% confidence interval
over independent seeded runs, delivered-only median latency with survivor-bias caveat,
attempted bytes per generated/delivered message, peak queue bytes. No fabricated radio values.
Include storage-limited sweep and convergence/paired comparisons if practical.

Outputs: JSON/CSV metrics, event trace of relay restart demo, static self-contained HTML report
with SVG plots (no dependencies, no network) and an Italian interpretation in README or report.
Use timestamped output folders, never overwrite prior results. Preserve input seeds/config.
All report numbers must be derived from actual runs. Include command, environment/version,
source SHA256 manifest and schema/assumptions. Tests must check invariants and regressions.

## v0.2 inventory comparison (2026-11-08)

Add `inventory` policy, retain `direct` and `epidemic` unchanged. Each accepted contact
snapshots both queues and unexpired IDs in queue UNION bounded seen cache, including
locally delivered IDs still retained. No global ledger/oracle. Exchange two inventory
frames, a->b then b->a, sequentially sharing the contact byte/time budget and loss
probability. Frame size = 8 header bytes + sum(2 + max(32, UTF8 ID length)).
The 32-byte padded ID field is an explicit conservative abstract representation;
it avoids evaluating only tiny synthetic IDs. No claim of BLE measured overhead.
If either frame cannot fit or is lost, abort this contact, without payload or fallback;
only attempted frames consume budget/time, even when lost. At successful completion
of both frames, filter snapshot payload candidates against peer inventory. No payload
received during this contact is offered back. Preserve expiry, hop and storage checks.
Inventory snapshots may become stale; do not secretly query receiver state at send time.

Keep legacy payload counters unchanged for exact baseline regression. Add separate
control attempts/completions/losses/bytes/time, inventory skips and failed-handshake
counts; total attempted bytes/time MUST include data and inventory, even failed frames.
Support pause/resume during handshake and payload; expose pending control attempts.
Document full-budget reservation at attempt start, as in v0.1.

Test queue/seen union, expired IDs, dedup retention and eviction, restart, both-direction
inventory, no payload before both success, frame loss at both stages, insufficient budget
and duration, exact control+data accounting, UTF8 sizes, expiry during exchange,
no new same-contact offers and single-shot vs paused execution.

Rerun original 18 conditions x30 seeds with ALL THREE policies =1620 runs. Keep generator,
ordering, loss model and seeds unchanged. Check all direct/epidemic legacy metrics equal
saved v0.1 results in fixtures/baselines/v0.1/runs.json. Report inventory vs epidemic
paired delivery delta, TOTAL bytes delta, relative total traffic change (ratio of totals),
payload/control breakdown, duplicates and handshake failure counts. Also keep direct
reference. Same seed does not imply same losses on corresponding attempts. Statistical
intervals describe seeded synthetic runs, not real-world uncertainty. Save all inputs,
hashes, new timestamped outputs and HTML report; never overwrite old results.

## v0.3 compact, loss-tolerant summaries (2026-11-08)

Retain all three older policies unchanged; add `compact`. Use a real, shared
binary codec: 16-byte header (magic, version, kind, token count, 64-bit salt)
and 8-byte SHA-256-derived salted fingerprints. These are not full identifiers:
a fingerprint collision can suppress a new message. This is not authentication.
The format is network byte order `>4sBBHQ`: magic `DNCS`, version 1, kind 1,
unique token count, unsigned salt; tokens strictly increasing, eight bytes each.
Fingerprint = SHA256(salt as 8 big-endian bytes || UTF-8 ID)[:8]. Decode rejects
bad magic/version/kind, length mismatch, duplicate or unsorted tokens. No frame
is emitted if not even one token fits. Equal fingerprints merge as one token;
their collision risk is part of the stated approximation, not resolved by IDs.
Salt changes per contact without consuming the loss RNG. Summaries include only
contact-start queue UNION live bounded seen state, never the delivery audit.
The simulator salt is the first eight SHA256 bytes of compact UTF-8 JSON
[contact ordinal, start, endpoint a, endpoint b], as an unsigned big-endian
integer. Both endpoints use that contact salt; decoders read the transmitted
salt and do not require identical host-side JSON number formatting.

Effective contact bytes B=min(byte budget, floor(duration*rate)). Control allowance
is min(B//4, max(0,B-608)), split equally between directions; 608 is the existing
maximum modeled DATA size, not an actual radio packet size. Empty inventories
send nothing. If a complete summary fits twice in its directional allowance,
send it twice unconditionally; otherwise send one summary with the lowest salted
fingerprints that fit. A partial summary makes no claim about omitted IDs.
There are no free ACKs and no loss-dependent retry decision. Charge every frame,
including both repeated copies, to the same byte/time/loss model as before.

Receive each direction independently. After control attempts, suppress DATA only
when a successfully received peer summary contains its fingerprint. Without a
peer summary, use ordinary epidemic forwarding. Never abort DATA solely because
control was lost. Preserve candidate snapshots, scheduling, expiry, hop, storage,
and loss assumptions. Log control cap, received summaries, skips and fallback;
support paused execution. Tests must include malformed codec, partial samples,
collision injection, unilateral/bilateral control loss, unconditional repetition,
budget/time bounds, restart, expiry and pause/resume.

Run the original 18 conditions x30 seeds with all four policies =2160 runs.
Compare every prior field/counter of all 1620 v0.2 runs and preserve the 90 input
files byte-for-byte. Report compact versus BOTH epidemic and inventory with
paired delivery/total-byte intervals, ratios of total traffic, and pooled total
bytes per delivered message (including zero-delivery runs). Keep source manifests
and previous results immutable. Do not tune on the reported campaign or select
only favorable scenarios. Smaller frames do not lower the model's per-frame loss
probability; savings are byte/time savings, not measured radio reliability.

Reality step: run a separate-process, loopback-only UDP proof with independent
SQLite stores and the same compact summary codec, actual serialized datagrams,
synthetic content, relay restart, loss injection and dedup. Save results and
actual datagram-byte counts separately from simulator accounting. It is not BLE,
not Android, and not authenticated/encrypted. A future native implementation can
reuse the documented format and conformance vectors; hardware validation remains
necessary before any claims about range, background behavior or energy.
