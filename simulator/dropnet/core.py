# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic, chronological store-carry-forward reference model."""
from dataclasses import dataclass, asdict, replace
from collections import Counter
import heapq
import math
import random
import sqlite3

from .compact import control_budget, contact_salt, decode_summary, fingerprint, plan_frames

METADATA_BYTES = 96  # Assumed constant, not a measured or serialized BLE header.


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Message:
    id: str
    source: str
    destination: str
    created: float
    expires: float
    text: str
    hops: int = 0
    hop_limit: int = 8

    def __post_init__(self):
        for value in (self.id, self.source, self.destination):
            if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 128:
                raise ValueError('IDs and node names must contain 1..128 UTF-8 bytes')
        if not isinstance(self.text, str):
            raise ValueError('text must be a UTF-8 encodable string')
        try:
            size = len(self.text.encode('utf-8'))
        except UnicodeError as exc:
            raise ValueError('malformed UTF-8 text') from exc
        if size > 512:
            raise ValueError('text exceeds 512 UTF-8 bytes')
        if not finite(self.created) or not finite(self.expires) or self.created < 0 or self.expires <= self.created:
            raise ValueError('require finite 0 <= creation < expiry')
        if any(type(v) is not int or v < 0 for v in (self.hops, self.hop_limit)) or self.hops > self.hop_limit:
            raise ValueError('invalid hop count/limit')

    @property
    def size(self):
        return METADATA_BYTES + len(self.text.encode('utf-8'))


class Store:
    """Bounded logical queue and dedup cache, persisted atomically in SQLite.

    Capacity counts payload plus assumed metadata, not SQLite file overhead.
    Delivered payloads leave the queue. Dedup eviction is oldest admission first.
    """
    def __init__(self, path, node, capacity=65536, dedup_capacity=2048):
        if type(capacity) is not int or capacity < 0 or type(dedup_capacity) is not int or dedup_capacity < 1:
            raise ValueError('invalid storage limits')
        self.node, self.capacity, self.dedup_capacity = node, capacity, dedup_capacity
        self.db = sqlite3.connect(str(path))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS config(node TEXT, capacity INTEGER, dedup_capacity INTEGER);
            CREATE TABLE IF NOT EXISTS queue(
                id TEXT PRIMARY KEY, source TEXT, destination TEXT, created REAL,
                expires REAL, text TEXT, hops INTEGER, hop_limit INTEGER, size INTEGER);
            CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, expires REAL, ordinal INTEGER);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT, time REAL, created REAL);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value INTEGER);
            INSERT OR IGNORE INTO state VALUES('peak',0);
            INSERT OR IGNORE INTO state VALUES('ordinal',0);
        ''')
        config = self.db.execute('SELECT * FROM config').fetchone()
        if config is None:
            self.db.execute('INSERT INTO config VALUES(?,?,?)', (node, capacity, dedup_capacity))
        elif config != (node, capacity, dedup_capacity):
            self.db.close()
            raise ValueError('reopen requires identical node/storage configuration')
        self.db.commit()

    def close(self):
        self.db.close()

    @property
    def used(self):
        return self.db.execute('SELECT COALESCE(SUM(size),0) FROM queue').fetchone()[0]

    @property
    def peak(self):
        return self.db.execute("SELECT value FROM state WHERE key='peak'").fetchone()[0]

    def expire(self, now):
        with self.db:
            self.db.execute('DELETE FROM queue WHERE expires <= ?', (now,))
            self.db.execute('DELETE FROM seen WHERE expires <= ?', (now,))

    def messages(self, now):
        self.expire(now)
        return [Message(*row) for row in self.db.execute(
            'SELECT id,source,destination,created,expires,text,hops,hop_limit FROM queue ORDER BY created,id')]

    def inventory(self, now):
        """Only locally retained, live IDs; the delivery audit is never consulted."""
        self.expire(now)
        return frozenset(row[0] for row in self.db.execute(
            'SELECT id FROM queue UNION SELECT id FROM seen'))

    def accept(self, message, now):
        if now < message.created:
            raise ValueError('cannot admit before creation')
        self.expire(now)
        with self.db:
            if now >= message.expires:
                return 'ttl_rejected'
            if self.db.execute('SELECT 1 FROM seen WHERE id=? UNION SELECT 1 FROM queue WHERE id=?',
                               (message.id, message.id)).fetchone():
                return 'duplicates'
            delivered = self.node == message.destination
            if not delivered and self.used + message.size > self.capacity:
                return 'storage_rejected'
            ordinal = self.db.execute("SELECT value FROM state WHERE key='ordinal'").fetchone()[0] + 1
            self.db.execute("UPDATE state SET value=? WHERE key='ordinal'", (ordinal,))
            self.db.execute('INSERT INTO seen VALUES(?,?,?)', (message.id, message.expires, ordinal))
            self.db.execute('DELETE FROM seen WHERE id IN (SELECT id FROM seen ORDER BY ordinal LIMIT ?)',
                            (max(0, self.db.execute('SELECT COUNT(*) FROM seen').fetchone()[0] - self.dedup_capacity),))
            if delivered:
                self.db.execute('INSERT INTO deliveries VALUES(?,?,?)', (message.id, now, message.created))
            else:
                self.db.execute('INSERT INTO queue VALUES(?,?,?,?,?,?,?,?,?)',
                                (*asdict(message).values(), message.size))
                self.db.execute("UPDATE state SET value=MAX(value,?) WHERE key='peak'", (self.used,))
            return 'local_delivery' if delivered else 'accepted_transfers'

    def deliveries(self):
        return list(self.db.execute('SELECT id,time,created FROM deliveries ORDER BY time,id'))

    def check(self):
        assert 0 <= self.used <= self.capacity
        assert self.db.execute('SELECT COUNT(*) FROM seen').fetchone()[0] <= self.dedup_capacity
        assert self.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@dataclass(frozen=True)
class Contact:
    start: float
    a: str
    b: str
    duration: float
    rate: float
    budget: int
    loss: float = 0.0

    def __post_init__(self):
        if (not all(finite(v) for v in (self.start, self.duration, self.rate, self.loss))
                or self.start < 0 or self.duration < 0 or self.rate <= 0
                or type(self.budget) is not int or self.budget < 0 or not 0 <= self.loss <= 1
                or self.a == self.b):
            raise ValueError('invalid contact')


class Simulator:
    """Contact snapshots, sequential half duplex, independent completion events.

    Simultaneous starts ordered by input order; an occupied node declines overlap.
    Loss RNG draws once per attempted full transfer, in attempt order.
    """
    def __init__(self, stores, policy='epidemic', loss_seed=0, trace=False):
        if policy not in ('epidemic', 'direct', 'inventory', 'compact'):
            raise ValueError('unknown policy')
        self.stores, self.policy = stores, policy
        self.rng = random.Random(loss_seed)
        self.counters = Counter()
        self.trace = [] if trace else None
        self.events, self.serial, self.busy = [], 0, set()
        self.now = 0.0
        self.message_ids = set()

    def log(self, kind, **fields):
        if self.trace is not None:
            self.trace.append(dict(time=self.now, event=kind, **fields))

    def push(self, time, priority, kind, data):
        self.serial += 1
        heapq.heappush(self.events, (time, priority, self.serial, kind, data))

    def run(self, messages=(), contacts=(), horizon=math.inf):
        if horizon < self.now:
            raise ValueError('time cannot run backwards')
        for message in messages:
            if message.id in self.message_ids or message.created < self.now:
                raise ValueError('duplicate message ID or past creation')
            if message.source not in self.stores or message.destination not in self.stores:
                raise ValueError('unknown message endpoint')
            self.message_ids.add(message.id)
            self.push(message.created, 1, 'generate', message)
        for contact in contacts:
            if contact.start < self.now or contact.a not in self.stores or contact.b not in self.stores:
                raise ValueError('past contact or unknown endpoint')
            self.push(contact.start, 2, 'contact', contact)
        while self.events and self.events[0][0] <= horizon:
            self.now, _, _, kind, data = heapq.heappop(self.events)
            if kind == 'generate':
                result = self.stores[data.source].accept(data, self.now)
                self.counters['generated'] += 1
                self.counters['source_' + result] += 1
                if result == 'local_delivery':
                    self.counters['local_delivery'] += 1
                self.log('generate', id=data.id, node=data.source, result=result)
            elif kind == 'contact':
                self.start_contact(data, horizon)
            elif kind == 'control_complete':
                state, sender, receiver, packet, lost = data
                size = len(packet) if isinstance(packet, bytes) else packet
                self.counters['control_completions'] += 1
                self.counters['control_losses'] += int(lost)
                self.log('control_complete', sender=sender, receiver=receiver, bytes=size, lost=lost)
                if state.get('compact'):
                    if not lost:
                        summary = decode_summary(packet)
                        state['received_summaries'][sender] = summary
                        self.counters['compact_summary_received'] += 1
                        self.log('compact_summary_received', sender=sender, receiver=receiver,
                                 salt=summary.salt, tokens=[t.hex() for t in sorted(summary.tokens)])
                    self.advance_compact_control(state)
                elif lost:
                    self.fail_handshake(state)
                else:
                    self.advance_control(state)
            else:
                state, sender, receiver, message, lost = data
                if lost:
                    result = 'lost_transfers'
                else:
                    result = self.stores[receiver].accept(replace(message, hops=message.hops + 1), self.now)
                    if result == 'local_delivery':
                        self.counters['accepted_transfers'] += 1
                self.counters[result] += 1
                self.counters['completed_transfers'] += 1
                self.counters['outcome_' + result] += 1
                self.log('transfer_complete', id=message.id, sender=sender, receiver=receiver, result=result)
                self.advance(state)
        if math.isfinite(horizon):
            self.now = horizon
        for store in self.stores.values():
            store.expire(self.now)
            store.check()
        return self.counters

    def start_contact(self, contact, horizon):
        self.counters['contacts'] += 1
        self.counters['offered_contact_bytes'] += contact.budget
        if contact.a in self.busy or contact.b in self.busy:
            self.counters['busy_contacts'] += 1
            return
        candidates = []
        for sender, receiver in ((contact.a, contact.b), (contact.b, contact.a)):
            for message in self.stores[sender].messages(self.now):
                if self.policy in ('epidemic', 'inventory', 'compact') or message.destination == receiver:
                    candidates.append((sender, receiver, message))
        self.busy.update((contact.a, contact.b))
        # An observation boundary pauses the event loop, not the physical contact.
        state = dict(contact=contact, end=contact.start + contact.duration,
                     remaining=contact.budget, candidates=iter(candidates))
        self.log('contact', a=contact.a, b=contact.b, budget=contact.budget)
        if self.policy == 'inventory':
            state['inventories'] = {n: self.stores[n].inventory(self.now) for n in (contact.a, contact.b)}
            state['frames'] = iter(((contact.a, contact.b), (contact.b, contact.a)))
            self.advance_control(state)
        elif self.policy == 'compact':
            self.start_compact_control(state)
        else:
            self.advance(state)

    def start_compact_control(self, state):
        c = state['contact']
        allowance = control_budget(c.budget, c.duration, c.rate)
        quota = allowance // 2
        salt = contact_salt(self.counters['contacts'], c.start, c.a, c.b)
        frames = []
        frame_counts = {}
        for sender, receiver in ((c.a, c.b), (c.b, c.a)):
            ids = self.stores[sender].inventory(self.now)
            packets = plan_frames(ids, salt, quota)
            available = len({fingerprint(mid, salt) for mid in ids})
            frame_counts[sender] = len(packets)
            for number, packet in enumerate(packets):
                partial = len(decode_summary(packet).tokens) < available
                frames.append((sender, receiver, packet, partial, number > 0))
        state.update(compact=True, received_summaries={}, frames=iter(frames))
        self.log('compact_plan', a=c.a, b=c.b, control_allowance=allowance,
                 per_direction_quota=quota, frame_counts=frame_counts, salt=salt)
        self.advance_compact_control(state)

    def advance_compact_control(self, state):
        """Predetermined repeats; each received direction works independently."""
        c = state['contact']
        frame = next(state['frames'], None)
        if frame is None:
            summaries = state['received_summaries']
            self.counters['compact_fallback_directions'] += sum(
                node not in summaries for node in (c.a, c.b))
            candidates = []
            for sender, receiver, message in state['candidates']:
                summary = summaries.get(receiver)
                skip = summary is not None and summary.contains(message.id)
                self.log('compact_filter', id=message.id, sender=sender, receiver=receiver,
                         summary_available=summary is not None, skipped=skip)
                if skip:
                    self.counters['inventory_skips'] += 1
                else:
                    candidates.append((sender, receiver, message))
            state['candidates'] = iter(candidates)
            self.advance(state)
            return
        sender, receiver, packet, partial, repeated = frame
        size = len(packet)
        finish = self.now + size / c.rate
        if size > state['remaining'] or finish > state['end']:
            # Defensive: a planned schedule already fits both contact bounds.
            self.counters['control_budget_rejected'] += 1
            self.advance_compact_control(state)
            return
        self.counters['control_attempts'] += 1
        self.counters['control_bytes'] += size
        self.counters['control_seconds'] += size / c.rate
        self.counters['compact_partial_frames'] += int(partial)
        self.counters['compact_repeat_frames'] += int(repeated)
        state['remaining'] -= size
        lost = self.rng.random() < c.loss
        self.log('control_attempt', sender=sender, receiver=receiver, bytes=size,
                 finish=finish, compact=True, partial=partial, repeated=repeated,
                 packet_hex=packet.hex())
        self.push(finish, 0, 'control_complete', (state, sender, receiver, packet, lost))

    def fail_handshake(self, state):
        self.counters['failed_handshakes'] += 1
        c = state['contact']
        self.busy.difference_update((c.a, c.b))
        self.log('handshake_failed', a=c.a, b=c.b)

    def advance_control(self, state):
        c = state['contact']
        frame = next(state['frames'], None)
        if frame is None:
            # Filter only against the exchanged snapshots, never live receiver state.
            candidates = []
            for sender, receiver, message in state['candidates']:
                if message.id in state['inventories'][receiver]:
                    self.counters['inventory_skips'] += 1
                else:
                    candidates.append((sender, receiver, message))
            state['candidates'] = iter(candidates)
            self.advance(state)
            return
        sender, receiver = frame
        size = 8 + sum(2 + max(32, len(mid.encode('utf-8')))
                       for mid in state['inventories'][sender])
        finish = self.now + size / c.rate
        if size > state['remaining'] or finish > state['end']:
            self.counters['control_budget_rejected'] += 1
            self.fail_handshake(state)
            return
        self.counters['control_attempts'] += 1
        self.counters['control_bytes'] += size
        self.counters['control_seconds'] += size / c.rate
        state['remaining'] -= size
        lost = self.rng.random() < c.loss
        self.log('control_attempt', sender=sender, receiver=receiver, bytes=size, finish=finish)
        self.push(finish, 0, 'control_complete', (state, sender, receiver, size, lost))

    @property
    def pending_control_attempts(self):
        return sum(event[3] == 'control_complete' for event in self.events)

    def advance(self, state):
        c = state['contact']
        for sender, receiver, message in state['candidates']:
            if message.expires <= self.now:
                self.counters['ttl_rejected'] += 1
                self.counters['preflight_ttl_rejected'] += 1
                continue
            if message.hops >= message.hop_limit:
                self.counters['hop_rejected'] += 1
                continue
            finish = self.now + message.size / c.rate
            if message.size > state['remaining'] or finish > state['end']:
                self.counters['budget_rejected'] += 1
                continue
            self.counters['attempts'] += 1
            self.counters['attempted_bytes'] += message.size
            self.counters['transmission_seconds'] += message.size / c.rate
            state['remaining'] -= message.size
            lost = self.rng.random() < c.loss
            self.log('attempt', id=message.id, sender=sender, receiver=receiver,
                     bytes=message.size, finish=finish)
            self.push(finish, 0, 'complete', (state, sender, receiver, message, lost))
            return
        self.busy.difference_update((c.a, c.b))
