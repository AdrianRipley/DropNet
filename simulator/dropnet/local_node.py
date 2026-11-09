# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only executable transport probe; not a Bluetooth or secure transport.

Each process owns a UDP socket and SQLite store. A test driver schedules contacts
through stdin, but suppression decisions use only datagrams received by the node.
TTL uses wall-clock seconds. UDP payload bytes are counted exactly; IP/UDP headers,
radio timing, authentication, discovery and encryption are not implemented.
"""
import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
import select
import socket
import struct
import sys
import time

from .core import Message, Store
from .compact import contact_salt, decode_summary, plan_frames

HEADER = struct.Struct('>4sQBH')
MAGIC = b'DNLP'
SUMMARY, DATA = 1, 2
# JSON escapes some legal UTF-8 control bytes to six ASCII bytes. This bound
# accommodates maximal IDs and 512-byte text without pretending JSON is compact.
MAX_DATAGRAM = 8192
MAX_WIRE_HOPS = 65535


def packet(session, kind, body):
    if type(session) is not int or not 0 <= session < 2**64 or kind not in (SUMMARY, DATA):
        raise ValueError('invalid local envelope')
    if not isinstance(body, bytes) or len(body) + HEADER.size > MAX_DATAGRAM:
        raise ValueError('invalid or oversized body')
    return HEADER.pack(MAGIC, session, kind, len(body)) + body


def unpack_packet(data):
    if not isinstance(data, bytes) or not HEADER.size <= len(data) <= MAX_DATAGRAM:
        raise ValueError('invalid datagram length')
    magic, session, kind, size = HEADER.unpack(data[:HEADER.size])
    if magic != MAGIC or kind not in (SUMMARY, DATA) or size != len(data) - HEADER.size:
        raise ValueError('invalid local envelope')
    return session, kind, data[HEADER.size:]


def encode_message(message):
    if message.hop_limit > MAX_WIRE_HOPS or message.hops > MAX_WIRE_HOPS:
        raise ValueError('local wire format limits hop fields to uint16')
    body = json.dumps(asdict(message), ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')
    if len(body) + HEADER.size > MAX_DATAGRAM:
        raise ValueError('message exceeds local datagram bound')
    return body


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON field')
        value[key] = item
    return value


def decode_message(data):
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError('message must be an object')
        # The on-disk time columns are REAL. Normalize numeric JSON times before
        # SQLite binding, while retaining Message's rejection of bool/string/NaN.
        for name in ('created', 'expires'):
            if type(value.get(name)) in (int, float):
                value[name] = float(value[name])
        message = Message(**value)
        if message.hop_limit > MAX_WIRE_HOPS or message.hops > MAX_WIRE_HOPS:
            raise ValueError('local wire format limits hop fields to uint16')
        return message
    except (UnicodeError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError('invalid message') from exc


class LocalNode:
    def __init__(self, store, sock):
        self.store, self.sock = store, sock
        self.counts = Counter()
        self.events = []
        self.session = None
        self.peer = None
        self.offers, self.frames, self.summaries = [], [], []
        self.drop = Counter()

    def log(self, event, **fields):
        self.events.append(dict(event=event, time=time.time(), **fields))

    def begin(self, peer_port, session, summary_quota=128):
        if (type(peer_port) is not int or not 1 <= peer_port <= 65535
                or type(session) is not int or not 0 <= session < 2**64
                or type(summary_quota) is not int or not 0 <= summary_quota <= 512):
            raise ValueError('invalid contact parameters')
        now = time.time()
        self.session, self.peer = session, ('127.0.0.1', peer_port)
        self.offers = self.store.messages(now)
        self.summaries = []
        salt = contact_salt(session, float(session), self.store.node, str(peer_port))
        self.frames = plan_frames(self.store.inventory(now), salt, summary_quota)
        self.drop.clear()
        self.log('begin', session=session, peer_port=peer_port,
                 queued=len(self.offers), summary_frames=len(self.frames))
        return dict(frames=len(self.frames), queued=len(self.offers))

    def send(self, kind, body):
        if self.peer is None:
            raise ValueError('contact not started')
        frame = packet(self.session, kind, body)
        sent = self.sock.sendto(frame, self.peer)
        if sent != len(frame):
            raise RuntimeError('partial datagram send')
        label = 'control' if kind == SUMMARY else 'data'
        self.counts[label + '_sent_bytes'] += len(frame)
        self.counts[label + '_sent_datagrams'] += 1
        self.log('sent', kind=label, bytes=len(frame), session=self.session)
        return len(frame)

    def send_summaries(self):
        return dict(datagrams=len(self.frames),
                    bytes=sum(self.send(SUMMARY, body) for body in self.frames))

    def offer(self, max_bytes=8192):
        if type(max_bytes) is not int or not 0 <= max_bytes <= 65536:
            raise ValueError('invalid offer budget')
        sent = skipped = used = 0
        if not self.summaries:
            self.counts['fallback_offers'] += 1
        # Consume each snapshot once, including candidates skipped for budget.
        offers, self.offers = self.offers, []
        for message in offers:
            now = time.time()
            if message.expires <= now or message.hops >= message.hop_limit:
                continue
            if any(summary.contains(message.id) for summary in self.summaries):
                skipped += 1
                self.counts['inventory_skips'] += 1
                continue
            try:
                body = encode_message(replace(message, hops=message.hops + 1))
            except ValueError:
                self.counts['wire_rejected'] += 1
                self.log('wire_rejected', id=message.id)
                continue
            if len(body) + HEADER.size > max_bytes - used:
                self.counts['data_budget_rejected'] += 1
                continue
            used += self.send(DATA, body)
            sent += 1
        return dict(datagrams=sent, bytes=used, skipped=skipped,
                    fallback=not bool(self.summaries))

    def receive(self, data, address):
        self.counts['received_bytes'] += len(data)
        self.counts['received_datagrams'] += 1
        try:
            session, kind, body = unpack_packet(data)
            if address != self.peer or session != self.session:
                raise ValueError('unexpected peer or stale contact')
            label = 'control' if kind == SUMMARY else 'data'
            if self.drop[label]:
                self.drop[label] -= 1
                self.counts['injected_' + label + '_drops'] += 1
                self.log('injected_drop', kind=label, bytes=len(data))
                return
            if kind == SUMMARY:
                summary = decode_summary(body)
                if summary not in self.summaries and len(self.summaries) < 2:
                    self.summaries.append(summary)
                self.counts['control_received'] += 1
                self.log('summary', tokens=len(summary.tokens), session=session)
            else:
                message = decode_message(body)
                if message.hops == 0:
                    raise ValueError('network copy must have traversed a hop')
                result = self.store.accept(message, time.time())
                self.counts[result] += 1
                self.log('message', id=message.id, result=result, hops=message.hops)
        except (ValueError, struct.error):
            self.counts['malformed_or_stale'] += 1
            self.log('rejected_datagram', bytes=len(data))

    def drain(self, timeout=.05):
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 1:
            raise ValueError('invalid drain timeout')
        deadline = time.monotonic() + timeout
        received = 0
        while received < 256:
            ready, _, _ = select.select([self.sock], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            data, address = self.sock.recvfrom(MAX_DATAGRAM + 1)
            self.receive(data, address)
            received += 1
            if time.monotonic() >= deadline:
                break
        return dict(received=received)

    def command(self, cmd):
        op = cmd['op']
        if op == 'begin':
            return self.begin(cmd['peer_port'], cmd['session'], cmd.get('summary_quota', 128))
        if op == 'seed':
            message = Message(**cmd['message'])
            if message.source != self.store.node or message.hops != 0:
                raise ValueError('only locally originated message can be seeded')
            return dict(result=self.store.accept(message, time.time()))
        if op == 'summaries':
            return self.send_summaries()
        if op == 'offer':
            return self.offer(cmd.get('max_bytes', 8192))
        if op == 'drain':
            return self.drain(cmd.get('timeout', .05))
        if op == 'drop':
            for label in ('control', 'data'):
                count = cmd.get(label, 0)
                if type(count) is not int or not 0 <= count <= 256:
                    raise ValueError('invalid drop count')
                self.drop[label] = count
            return dict(armed=dict(self.drop))
        if op == 'malformed':
            # Test-only injection from the expected peer, never a public endpoint.
            frame = packet(self.session, DATA, b'{')
            frame = frame[:-1] if cmd.get('truncated') else frame
            self.sock.sendto(frame, self.peer)
            self.counts['injected_malformed_sent_bytes'] += len(frame)
            return dict(bytes=len(frame))
        if op == 'status':
            self.store.check()
            return dict(node=self.store.node, queued=[m.id for m in self.store.messages(time.time())],
                        deliveries=self.store.deliveries(), counters=dict(self.counts), events=self.events)
        if op == 'stop':
            return dict(stopped=True)
        raise ValueError('unknown command')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    store = Store(args.database, args.node, capacity=65536, dedup_capacity=256)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('127.0.0.1', 0))
        node = LocalNode(store, sock)
        print(json.dumps(dict(ready=True, node=args.node, port=sock.getsockname()[1])), flush=True)
        for line in sys.stdin:
            try:
                command = json.loads(line)
                result = node.command(command)
                print(json.dumps(dict(ok=True, **result), allow_nan=False), flush=True)
                if command['op'] == 'stop':
                    break
            except (ValueError, KeyError, TypeError) as exc:
                print(json.dumps(dict(ok=False, error=str(exc))), flush=True)
    finally:
        sock.close()
        store.close()


if __name__ == '__main__':
    main()
