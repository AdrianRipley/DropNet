#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Three local worker processes with real UDP datagrams and independent SQLite.

No hardware, Internet, BLE discovery, radio rate or encryption is claimed.
The driver schedules contact phases and injected drops, never forwarding choices.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


class Worker:
    def __init__(self, name, folder, generation=1):
        self.name = name
        self.error_file = (folder / f'{name}-{generation}.stderr').open('w')
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'dropnet.local_node', '--node', name,
             '--database', str(folder / f'{name}.sqlite')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error_file,
            text=True, bufsize=1, cwd=ROOT)
        try:
            self.port = self.read()['port']
        except BaseException:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.error_file.close()
            raise

    def read(self):
        ready, _, _ = select.select([self.proc.stdout], [], [], 5)
        if not ready:
            raise RuntimeError(f'worker {self.name} did not respond within 5 seconds')
        text = self.proc.stdout.readline()
        if not text:
            raise RuntimeError(f'worker {self.name} exited unexpectedly; see stderr')
        result = json.loads(text)
        if result.get('ok') is False:
            raise RuntimeError(f'worker {self.name}: {result["error"]}')
        return result

    def ask(self, op, **args):
        self.proc.stdin.write(json.dumps(dict(op=op, **args)) + '\n')
        self.proc.stdin.flush()
        return self.read()

    def stop(self):
        try:
            if self.proc.poll() is None:
                self.ask('stop')
                self.proc.wait(timeout=3)
        finally:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.error_file.close()


def seed(worker, mid, destination):
    now = time.time()
    return worker.ask('seed', message=dict(id=mid, source=worker.name,
        destination=destination, created=now, expires=now + 600,
        text='Synthetic local transport probe', hops=0, hop_limit=8))


def contact(a, b, session, drop_controls=False):
    a.ask('begin', peer_port=b.port, session=session)
    b.ask('begin', peer_port=a.port, session=session)
    if drop_controls:
        a.ask('drop', control=256)
        b.ask('drop', control=256)
    control_a = a.ask('summaries')
    control_b = b.ask('summaries')
    a.ask('drain')
    b.ask('drain')
    offer_a = a.ask('offer')
    b.ask('drain')
    offer_b = b.ask('offer')
    a.ask('drain')
    return dict(session=session, a=a.name, b=b.name, dropped_controls=drop_controls,
                summaries_a=control_a, summaries_b=control_b,
                offer_a=offer_a, offer_b=offer_b)


def run(folder):
    folder.mkdir(parents=True, exist_ok=False)
    all_workers, records, contacts = [], {}, []
    started = time.monotonic()

    def launch(name, generation=1):
        worker = Worker(name, folder, generation)
        all_workers.append(worker)
        return worker

    try:
        a, b, c = launch('A'), launch('B'), launch('C')
        seed(a, 'relay-demo', 'C')
        contacts.append(contact(a, b, 1))
        state_b = b.ask('status')
        assert state_b['queued'] == ['relay-demo']
        assert not c.ask('status')['deliveries']
        records['A-before-exit'] = a.ask('status')
        a.stop()
        records['B-before-restart'] = state_b
        b.stop()
        b = launch('B', 2)
        assert b.ask('status')['queued'] == ['relay-demo']
        assert a.proc.poll() == 0

        contacts.append(contact(b, c, 2))
        assert [d[0] for d in c.ask('status')['deliveries']] == ['relay-demo']
        contacts.append(contact(b, c, 3))
        assert contacts[-1]['offer_a']['datagrams'] == 0
        assert contacts[-1]['offer_a']['skipped'] == 1

        seed(b, 'fallback-demo', 'C')
        contacts.append(contact(b, c, 4, drop_controls=True))
        assert contacts[-1]['offer_a']['fallback'] is True
        assert contacts[-1]['offer_a']['datagrams'] == 2
        state_c = c.ask('status')
        assert {d[0] for d in state_c['deliveries']} == {'relay-demo', 'fallback-demo'}
        assert state_c['counters']['duplicates'] == 1

        b.ask('malformed', truncated=True)
        b.ask('malformed', truncated=False)
        c.ask('drain')
        state_c = c.ask('status')
        assert state_c['counters']['malformed_or_stale'] == 2
        assert len(state_c['deliveries']) == 2
        records['C-before-restart'] = state_c
        c.stop()
        c = launch('C', 2)
        contacts.append(contact(b, c, 5, drop_controls=True))
        state_c = c.ask('status')
        assert state_c['counters']['duplicates'] == 2
        assert len(state_c['deliveries']) == 2
        records['B-final'] = b.ask('status')
        records['C-final'] = state_c
        counts = [r['counters'] for r in records.values()]
        totals = {key: sum(row.get(key, 0) for row in counts) for key in
                  ('control_sent_bytes', 'data_sent_bytes', 'injected_malformed_sent_bytes',
                   'received_bytes', 'injected_control_drops', 'inventory_skips', 'duplicates')}
        assert totals['received_bytes'] == (totals['control_sent_bytes'] +
            totals['data_sent_bytes'] + totals['injected_malformed_sent_bytes'])
        result = dict(status='passed', elapsed_wall_seconds=time.monotonic() - started,
            operating_system_processes=[w.proc.pid for w in all_workers],
            distinct_nodes=3, restarted_nodes=['B', 'C'],
            application_deliveries=2, checks=dict(
                source_stopped_before_destination_contact=True,
                relay_survives_process_restart=True,
                received_summary_suppresses_repeat=True,
                lost_control_falls_back_and_delivers=True,
                truncated_and_malformed_datagrams_rejected=True,
                recipient_dedup_survives_process_restart=True,
                sent_received_byte_balance=True),
            totals=totals,
            assumptions=[
                'Real UDP sockets bound only to 127.0.0.1; independent processes and SQLite databases.',
                'Driver schedules contact phases and test drops; no shared forwarding database.',
                'Summary bytes use the same compact codec/selection as the simulator; loss injection drops at receive.',
                'Bytes are actual UDP payloads including local envelope and JSON DATA, excluding IP/UDP headers.',
                'Local JSON wire frame is bounded to 8192 bytes and hop fields to uint16; unsendable copies are counted and retained in SQLite.',
                'Wall-clock TTL; no rate/bandwidth/duration emulation, BLE discovery, fragmentation, crypto or authentication.',
                'Control quota is per node and DATA budget is per offer; this is a transport conformance probe, not a campaign performance measurement.',
                'EOF/truncation here means an incomplete UDP application frame; no partial-state transfer resumption is implemented.'])
        write(folder / 'result.json', result)
        write(folder / 'contacts.json', contacts)
        write(folder / 'nodes.json', records)
        paths = [ROOT / 'dropnet' / name for name in ('core.py', 'compact.py', 'local_node.py')]
        paths.append(Path(__file__).resolve())
        write(folder / 'manifest.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        return result
    finally:
        for worker in all_workers:
            if worker.proc.poll() is None:
                worker.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new folder, never overwrite')
    args = parser.parse_args()
    folder = args.output or ROOT / 'scripts' / 'local_outputs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    result = run(folder.resolve())
    print(folder.resolve())
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
