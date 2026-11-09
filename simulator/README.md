# DropNet offline reference simulator

GPL-3.0-or-later; see the [repository license](../LICENSE). Python 3.11+ standard
library only, with no package installation required. The complete workflow below
is tested on Linux. The process demonstration uses POSIX pipe polling and is not
currently a Windows-compatible runner. This is a reproducible protocol experiment,
not Bluetooth software or an emergency-ready communications system. It does not
implement encryption, signatures, anonymity, public SOS trust, malicious nodes,
battery consumption or physically calibrated mobility. A native Android/Kotlin
implementation would need a port and shared conformance vectors.

From a clean clone:

```sh
git clone https://github.com/AdrianRipley/DropNet.git
cd DropNet/simulator
python3 -m unittest discover -s tests -v
python3 scripts/run_local_demo.py --output scripts/local_outputs/first-demo
python3 scripts/run_experiments.py --output scripts/outputs/first-campaign
python3 scripts/validate_campaign.py scripts/outputs/first-campaign
```

Use a new output directory on each repeat; existing output directories are
refused. Omit `--output` to create a new timestamped directory automatically.
Open `scripts/outputs/first-campaign/report.html` in a browser for the complete,
self-contained report. It works offline and includes all four policy comparisons.
The report text is currently in Italian; the technical documentation is English.

The experiment command executes the full campaign: 30 paired seeds, 20 nodes,
40 synthetic text messages, three encounter densities (40, 100, 240 contacts
within 3600 seconds), three independent per-attempt loss probabilities
(0, 0.25, 0.75), two per-node queue capacities (2048, 65536 bytes), and
epidemic, direct, inventory and compact forwarding: **2160 runs across 18 paired conditions**.
No dependencies need installation. Outputs go to a new UTC timestamped directory
under `scripts/outputs/`. `--output PATH` selects a new directory; an existing
path is refused. Old output folders are preserved. Temporary campaign SQLite
stores are removed after each run; the demonstration databases are retained.

Each output folder contains:

- `report.html`: static self-contained Italian report and SVG plots, no network.
- `runs.json`, `runs.csv`: individual metrics and transport counters. Missing
  counter keys or empty CSV counter cells mean zero; other empty cells mean an
  undefined conditional metric (no deliveries).
- `summary.json`: mean, sample SD, extrema and 95% intervals over seeded runs.
- `paired.json`: within-seed epidemic minus direct delivery fractions and
  cumulative comparisons at 10, 20 and all seeds; inventory versus epidemic,
  and compact versus both epidemic and inventory, including total traffic.
- `config.json`: full configuration, assumptions, command, Python, SQLite and
  platform versions; `manifest.json`: SHA256 of sources, tests and specification.
- `inputs/contacts{count}_seed{seed}.json`: exact generated messages and contacts
  before replacing the loss parameter for each condition.
- `demo/trace.json`, `demo/result.json`, `demo/{A,B,C}.sqlite`: persistent relay
  restart demonstration and expected exact latency.
- `completion.json`: written only after all runs and reports complete.

After independent validation, `validation.json`, `unittest.txt` and twelve
accounting traces are added. The validator checks 2,160 current runs, all 1,620
v0.2 runs and all 1,080 v0.1 runs, plus the 90 exact synthetic input files.
Historical fixtures are committed under [fixtures/baselines](fixtures/baselines/README.md),
so no local outputs or private development environment are needed to reproduce
the comparisons. Generated databases and output directories are ignored by Git.
See [PUBLICATION_NOTES.md](PUBLICATION_NOTES.md) for the explicit packaging changes.
- `baseline_comparison.json`, `previous_version_comparison.json`: exact checks
  of original v0.1 and v0.2 fields, including all counters and saved inputs.

Replay one saved input with an explicit condition:

```sh
python3 scripts/run_experiments.py --replay scripts/outputs/OUTPUT/inputs/contacts100_seed0.json --policy epidemic --loss 0.25 --capacity 2048
```

The replay prints metrics without modifying saved inputs. Source hashes describe
the implementation that produced the campaign; reproduce with that source and
the saved parameters. JSON inputs are also readable with `Message(**item)` and
`Contact(**item)` for custom runs. `Simulator.run` accepts a finite observation
horizon, and supports subsequent calls at nondecreasing times. Stores must be
closed explicitly. Reopening requires identical node and capacity configuration.
The simulator's event queue, counters and RNG are not checkpointed by SQLite;
only node state is persistent. The restart demo reopens B inside the same event
simulation, with no pending transfer at restart.

## Model and assumptions

Times are abstract seconds and rates abstract bytes/second, deliberately assumed
rather than measured. Message text must encode to at most 512 UTF-8 bytes (not
characters). IDs and node names are nonempty UTF-8 strings up to 128 bytes.
Message size is payload length plus **96 assumed metadata bytes**, used for both
wire and logical queue accounting. This is an abstract fixed header assumption,
not a serialization format: actual variable identifiers, protocol negotiation,
acknowledgments and radio overhead are not modeled. Inventory frames use the separate abstract sizes below. Text may be empty.
IDs are globally unique within generated simulation inputs; honest nodes assume
an existing ID always identifies identical immutable content.

A source copy has hop count zero. Each traversal, including the destination hop,
uses one hop. A copy at its hop limit cannot be sent, even to its destination.
Recipients accept only at completion times strictly before absolute expiry.
Expiry is applied before admission and queue snapshots. Source admission uses the
same drop-new policy; rejected generation remains in the generated denominator.
Source=destination is immediate local delivery without transport.

Contacts are ordered chronologically. At an equal time, completions precede
message creation, which precedes contact starts; ties within each class follow
stable input order. Contacts snapshot both queues at their start. Candidates
are transmitted in endpoint a-to-b order followed by b-to-a, each sorted by
creation time and ID; these are arbitrary deterministic scheduling assumptions.
Copies received during a contact cannot be forwarded within that contact.
The direct and epidemic baselines have no inventory exchange or magical end-to-end delivery
knowledge: duplicates consume transmission resources, and sources keep copies.

Within a contact, full messages transmit sequentially and half-duplex. A failed
attempt consumes the full message's byte allocation and size/rate duration.
A packet that cannot fit remaining bytes or duration is skipped without any
partial transfer; later smaller candidates may fit. No partial state is durable.
Both byte budget and finite duration apply; completion at the contact end is
allowed, while completion at expiry is rejected. The observation horizon pauses
event processing; pending transfers resume on the next run call without changing
the contact end. Full attempted byte/time budgets are charged at initiation;
`in_flight` counts attempts not yet completed at the observation horizon. These
are reserved budgets, not measurements of bytes emitted up to that instant.
The campaign's contacts finish before its horizon. Each snapshot candidate is attempted at most once per contact; retries
occur in later contacts. Loss is an independent Bernoulli draw per attempt.

Nodes can participate in only one active transmission/contact sequence. If a
contact starts while either endpoint is occupied, the whole new contact is
ignored; it is not delayed or resumed. A contact releases its endpoints as soon
as its candidate sequence finishes (it does not reserve idle radio time until
its nominal end). Independent contacts can run concurrently. This scheduling
rule is a model choice, not a claim about BLE connection management.

SQLite transactions store queues and retained dedup entries on disk; clean
close/reopen is tested. SQLite defaults for rollback journal and synchronous
writes are used. Power-loss recovery is not experimentally verified. Capacity
bounds queued payload plus assumed metadata, not physical SQLite file size.
Destination delivery bypasses the relay queue capacity and removes the need to
store its payload. The append-only delivery audit stores IDs and timestamps,
not message text, and is outside the queue quota; it is instrumentation, never
consulted by dedup or forwarding. Peak queue bytes are persisted per node.

Dedup entries expire with the message and have an independent count limit
(oldest admission evicted first; a duplicate does not refresh its age). A queued
ID also suppresses duplicates. **No duplicate application delivery is guaranteed
only while its dedup entry remains retained.** After capacity eviction, a live
copy can cause a second application delivery. This unavoidable bounded-cache
tradeoff is tested explicitly. Campaign dedup capacity is 256 for only 40
messages, so it does not evict live IDs. Source/recipient queue and dedup limits
are independently checked; the delivery audit is not a hidden dedup oracle.

Counter schema is in `dropnet/core.py`. `attempts`, `attempted_bytes`,
`transmission_seconds` and `completed_transfers` retain legacy payload-only accounting (payload includes fixed metadata).
`accepted_transfers` includes successful relay admissions and new destination
deliveries; `local_delivery` counts application delivery, not relay acceptance.
`duplicates`, `lost_transfers`, `ttl_rejected`, `hop_rejected`,
`storage_rejected` and `budget_rejected` distinguish failure reasons.
`outcome_*` counts only attempted transfer outcomes; `preflight_ttl_rejected`
counts expired snapshot candidates, also included in `ttl_rejected`.
Hop/budget rejections consume no bytes. `source_*` describes generation
admission separately. `offered_contact_bytes` includes every scheduled contact,
including contacts rejected as busy; it is not an achievable global byte quota.
Zero counters may be absent. Every attempted transfer has exactly one outcome.

## Campaign and statistical interpretation

Each seed generates messages first, then uniformly timed unordered random-node
pair encounters (stored endpoint order is retained for scheduling). Message
creation is uniform in the first half of the 3600-second horizon; TTL is 1800
seconds, hop limit 8, text length varies reproducibly. Contact duration is 8
seconds, rate 128 bytes/second, budget 1024 bytes. These are synthetic design
parameters, not radio measurements. Contacts are independent of message traffic;
there is no physical trajectory model. Density prefixes share the same initial
contacts before sorting. The same messages are used across densities, loss and
storage sweeps, and both policies receive exactly identical exogenous inputs.
Different seeds are independent pseudorandom runs. A separate loss RNG uses
`100000 + seed` for each policy. Policies consume different numbers of draws:
sharing a loss seed does **not** mean identical per-contact/per-message losses.

Delivery fraction counts distinct IDs delivered at the correct recipient by the
horizon divided by all generated messages. Reported spread is the sample SD of
run-level metrics, not a binomial interval treating correlated messages as
independent trials. The 95% mean intervals use Student t critical values; n=30
uses df=29 (larger runs conservatively retain this value). Small conditional
samples use their corresponding df through 29; n=1 has no interval. These
intervals are descriptive approximations for bounded, possibly skewed metrics;
they are untruncated and are not simultaneous multiple-comparison guarantees.
Paired intervals use within-seed policy differences. Cumulative 10/20/30-seed
estimates are a convergence diagnostic, not proof of convergence.

Latency is completion minus creation for delivered messages only. Each run saves
its delivered-only median; aggregates describe those medians and their sample
count. This suffers survivor bias: low latency can arise because difficult
messages never arrive. Attempted bytes per generated message includes all
failures and duplicates. Per-run bytes per delivered message is undefined with
zero deliveries; summaries retain its valid-run sample count, and the HTML also
shows total bytes divided by total delivered across all runs. Peak queue bytes
is the maximum over nodes of each node's historical peak. None of these results
predict real-world coverage, battery life or emergency reliability.

## Interpretazione italiana

L'esperimento verifica la logica di memorizzazione, trasporto e inoltro in una
rete sintetica. Il riavvio del relay conserva il messaggio; componenti isolate,
scadenze, memoria insufficiente e perdita totale impediscono la consegna come
previsto. L'inoltro epidemico può raggiungere percorsi indiretti ma paga copie e
duplicati; la consegna diretta è il confronto di riferimento. Le differenze
quantitative, con variabilità tra semi, sono calcolate nei report effettivi.
Non sono state eseguite misure Bluetooth, prove su telefoni o stime energetiche.
La mediana delle latenze riguarda soltanto i consegnati: non dimostra che i
messaggi non consegnati sarebbero arrivati presto. Il modello resta privo delle
protezioni necessarie a messaggi privati e SOS reali.

## v0.2 inventory comparison

Inventory snapshots each accepted contact's two queues and queue UNION unexpired
bounded seen IDs, including locally delivered IDs still retained. The audit of
past deliveries is never consulted. Two sequential frames (a→b then b→a) cost
8 header bytes + sum(2 + max(32, UTF-8 ID length)) each. The 32-byte padding is
an assumed conservative representation, not measured BLE overhead. Each frame
uses the same remaining bytes, time and independent Bernoulli loss model as data.
A frame that cannot fit is not attempted. Either frame loss or inability to fit
aborts the contact without payload or fallback. Even empty inventories cost 8 bytes.
Only after both successes are snapshot candidates filtered against the exchanged
peer snapshot. No live receiver query occurs; snapshots can become stale during
exchange. Received or newly generated copies are never added to this contact's offers.
Expiry, hop and storage checks remain as in the baseline.

`control_attempts`, `control_completions`, `control_losses`, `control_bytes`,
`control_seconds`, `control_budget_rejected`, `failed_handshakes` and
`inventory_skips` describe the new work. `pending_control_attempts` exposes
uncompleted frames at an observation horizon. Both data and control reserve their
full bytes and duration at initiation, including failures and in-flight attempts.
`total_attempted_bytes` and `total_transmission_seconds` add payload and control;
`total_bytes_per_generated/delivered` use those totals. The legacy byte metrics
remain payload-only. Pause/resume retains the event queue and RNG in memory;
SQLite restart persists node state, not the simulator event queue.

The report includes inventory minus epidemic paired delivery and TOTAL-byte
intervals, relative traffic change as ratio of totals minus one, and cumulative
payload/control bytes, duplicates and failed handshakes. Direct remains a
reference. Shared seeds do not imply matching losses on corresponding attempts.
Intervals describe seeded synthetic runs, not real-world uncertainty.
`baseline_comparison.json` checks every saved original field, including counters,
for all 1080 direct/epidemic runs against `fixtures/baselines/v0.1/runs.json`.
`manifest.json` records actual SHA256 source hashes before running the campaign.

## v0.3 compact summaries and local process prototype

`compact` adds a shared binary control format in `dropnet/compact.py`: a 16-byte
header followed by 8-byte salted SHA-256 fingerprints. This reduces the cost of
represented IDs from at least 34 bytes in v0.2 to 8 bytes. Fingerprints are not
full IDs and are not authentication: collisions can suppress a new message.
The salt changes per contact, independently of the packet-loss RNG. An honest
node still assumes that the same ID means the same immutable message content.

Let B=min(contact byte budget, floor(duration*rate)). Total control allowance is
min(B//4, max(0,B-608)); each direction gets half, rounded down. The 608-byte
reservation is the maximum DATA size in this simulator, not a physical MTU.
Empty inventories send nothing. Complete summaries repeat twice unconditionally
if both copies fit the directional allowance; otherwise one summary contains
the lowest salted fingerprints that fit. At the campaign budget of 1024 bytes,
each direction gets 128 bytes: up to 14 fingerprints once, or up to 6 twice.
This bounded sample can omit known IDs and therefore still allow duplicates.

Each successfully received summary is useful independently of the reverse
direction. A missing summary suppresses no candidates: ordinary epidemic DATA
forwarding continues. There is no mandatory two-way handshake, free ACK, or
retry conditioned on knowledge of a receiver's loss. All planned repeats use
time and bytes even if an earlier copy arrived. Payload snapshots and ordering
are unchanged; expiry/hop/storage limits still apply. Smaller frames do not
change the assumed, size-independent per-frame probability of loss.

The campaign preserves the prior input generator and 30 seeds. It compares all
1620 previous v0.2 runs field-for-field and preserves all 90 saved input files.
`compact` is an additional experimental policy, not silently a new default.
To independently validate a completed output, including trace reconstruction:

```sh
python3 scripts/validate_campaign.py scripts/outputs/OUTPUT
```

The local prototype takes the next step beyond virtual nodes:

```sh
python3 scripts/run_local_demo.py
```

It uses separate operating-system processes, real loopback UDP datagrams,
independent SQLite stores and the same compact-summary codec. Its scripted
contacts use synthetic content and exercise relay persistence, process restart,
lost-summary fallback and duplicate suppression. The command writes a new
timestamped output directory and closes its child processes when finished.
Actual serialized datagram payload bytes are recorded separately from the
simulation's assumed 96-byte DATA metadata. UDP/IP and link-layer overhead are
not included in these application-byte counts. The probe uses a per-node summary
quota and a separate DATA offer budget, without emulating the simulator's shared
contact duration or rate. Its local envelope is bounded to 8192 bytes (JSON can
expand escaped text); wire hop fields are limited to unsigned 16-bit values.
Unsupported wire values are rejected without dropping other queued messages.
These are local transport limits; the simulation parameters remain unchanged.
This is a transport integration
proof on one computer, not BLE, Android background operation or radio testing.
Messages are not encrypted or authenticated; use synthetic content only.

The binary layout and deterministic test vectors are the bridge to a future
native client. Next physical milestone: three compatible participating nodes,
A→B while C is absent, stop A and restart B, then B→C; measure delivered IDs,
actual byte counts, latency, background suspension and energy. None of those
hardware measurements can be inferred from this synthetic campaign.
