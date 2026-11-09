#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent v0.3 campaign audit; writes evidence only in the selected output.

Usage: python3 scripts/validate_campaign.py scripts/outputs/OUTPUT
Does not use the campaign's aggregation, compact codec, or budget helpers.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dropnet import Contact, Message, Simulator, Store


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
                    encoding='utf-8')


def key(row):
    return tuple(row[k] for k in ('contacts', 'seed', 'loss', 'capacity', 'policy'))


def close(actual, expected):
    if expected is None:
        assert actual is None, (actual, expected)
    else:
        assert math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-10), (actual, expected)


def check_interval(saved, values):
    if not values:
        assert saved is None
        return
    n = len(values)
    mean = math.fsum(values) / n
    assert saved['n'] == n
    close(saved['mean'], mean)
    close(saved['minimum'], min(values))
    close(saved['maximum'], max(values))
    if n == 1:
        assert saved['sd'] is None and saved['ci95'] is None
        return
    sd = math.sqrt(math.fsum((x - mean)**2 for x in values) / (n - 1))
    close(saved['sd'], sd)
    # Published Student-t two-sided 95% critical values; same documented
    # rounding/conservative df=29 convention as the saved experiment.
    critical = (12.706205, 4.302653, 3.182446, 2.776445, 2.570582,
                2.446912, 2.364624, 2.306004, 2.262157, 2.228139,
                2.200985, 2.178813, 2.160369, 2.144787, 2.131450,
                2.119905, 2.109816, 2.100922, 2.093024, 2.085963,
                2.079614, 2.073873, 2.068658, 2.063899, 2.059539,
                2.055529, 2.051831, 2.048407, 2.045229642)
    half = critical[min(n - 2, 28)] * sd / math.sqrt(n)
    close(saved['ci95'][0], mean - half)
    close(saved['ci95'][1], mean + half)


def check_statistics(folder, runs):
    groups = defaultdict(list)
    conditions = defaultdict(lambda: defaultdict(dict))
    for row in runs:
        condition = tuple(row[k] for k in ('contacts', 'loss', 'capacity'))
        groups[condition + (row['policy'],)].append(row)
        conditions[condition][row['seed']][row['policy']] = row
    summaries = load(folder / 'summary.json')
    assert len(summaries) == len(groups) == 72
    for saved in summaries:
        rows = groups[tuple(saved[k] for k in ('contacts', 'loss', 'capacity', 'policy'))]
        for metric in ('delivery_fraction', 'bytes_per_generated', 'peak_queue_bytes',
                       'median_latency', 'bytes_per_delivered', 'total_bytes_per_generated',
                       'total_bytes_per_delivered', 'total_transmission_seconds'):
            check_interval(saved[metric], [r[metric] for r in rows if r[metric] is not None])
        delivered = sum(r['delivered'] for r in rows)
        for target, source in (('pooled_bytes_per_delivered', 'attempted_bytes'),
                               ('pooled_total_bytes_per_delivered', 'total_attempted_bytes')):
            close(saved[target], sum(r[source] for r in rows) / delivered if delivered else None)
        totals = saved['traffic_totals']
        assert totals['payload_bytes'] == sum(r['attempted_bytes'] for r in rows)
        assert totals['total_bytes'] == sum(r['total_attempted_bytes'] for r in rows)
        for counter in ('control_bytes', 'duplicates', 'failed_handshakes'):
            assert totals[counter] == sum(r['counters'].get(counter, 0) for r in rows)
    paired = load(folder / 'paired.json')
    assert len(paired) == len(conditions) == 18
    for saved in paired:
        condition = tuple(saved[k] for k in ('contacts', 'loss', 'capacity'))
        pairs = [conditions[condition][seed] for seed in sorted(conditions[condition])]
        differences = [p['epidemic']['delivery_fraction'] - p['direct']['delivery_fraction'] for p in pairs]
        check_interval(saved['epidemic_minus_direct'], differences)
        for point in saved['convergence']:
            check_interval(point, differences[:point['seeds']])
        for policy, reference, relative_key in (
                ('inventory', 'epidemic', 'relative_total_traffic_change'),
                ('compact', 'epidemic', 'compact_minus_epidemic_relative_traffic'),
                ('compact', 'inventory', 'compact_minus_inventory_relative_traffic')):
            prefix = policy + '_minus_' + reference
            check_interval(saved[prefix + '_delivery'], [
                p[policy]['delivery_fraction'] - p[reference]['delivery_fraction'] for p in pairs])
            check_interval(saved[prefix + '_total_bytes'], [
                p[policy]['total_attempted_bytes'] - p[reference]['total_attempted_bytes'] for p in pairs])
            denominator = sum(p[reference]['total_attempted_bytes'] for p in pairs)
            close(saved[relative_key], sum(p[policy]['total_attempted_bytes'] for p in pairs) /
                  denominator - 1 if denominator else None)
    return dict(summary_groups=len(summaries), paired_conditions=len(paired),
                independent_recalculation='means, sample SD, t intervals, paired deltas, traffic ratios and pooled efficiency')


def parse_packet(packet_hex):
    """Decode using the written binary format, independently of dropnet.compact."""
    raw = bytes.fromhex(packet_hex)
    assert len(raw) >= 16
    magic, version, kind, count, salt = struct.unpack('>4sBBHQ', raw[:16])
    assert (magic, version, kind) == (b'DNCS', 1, 1)
    assert len(raw) == 16 + 8 * count
    tokens = [raw[i:i+8] for i in range(16, len(raw), 8)]
    assert tokens == sorted(set(tokens))
    return dict(salt=salt, tokens=set(tokens), raw=raw)


def check_trace(folder, by_key, policy, count, loss):
    data = load(folder / 'inputs' / f'contacts{count}_seed3.json')
    contacts = [Contact(**(c | {'loss': loss})) for c in data['contacts']]
    contact_by_start = {(c.start, frozenset((c.a, c.b))): c for c in contacts}
    with tempfile.TemporaryDirectory(prefix='dropnet-validation-') as tmp:
        stores = {f'N{i:02d}': Store(Path(tmp) / str(i), f'N{i:02d}', 2048, 256) for i in range(20)}
        try:
            sim = Simulator(stores, policy, 100003, trace=True)
            sim.run([Message(**m) for m in data['messages']], contacts, horizon=3600)
            assert [e['time'] for e in sim.trace] == sorted(e['time'] for e in sim.trace)
            active, states = {}, []
            byte_sums = dict(attempt=0, control_attempt=0)
            seconds = dict(attempt=0.0, control_attempt=0.0)
            counts = dict(attempt=0, control_attempt=0)
            skip_count = unfiltered_attempts = 0
            for event in sim.trace:
                kind = event['event']
                if kind == 'contact':
                    pair = frozenset((event['a'], event['b']))
                    c = contact_by_start[(event['time'], pair)]
                    state = dict(contact=c, spent=0, control_spent=0, frames=0, received={},
                                 controls={}, pending=None, last_finish=event['time'], filters={},
                                 by_direction=defaultdict(list))
                    active[pair] = state
                    states.append(state)
                    continue
                if kind not in ('compact_plan', 'control_complete', 'compact_summary_received',
                                'compact_filter', 'attempt', 'control_attempt'):
                    continue
                if kind == 'compact_plan':
                    state = active[frozenset((event['a'], event['b']))]
                    c = state['contact']
                    usable = min(c.budget, math.floor(c.duration * c.rate))
                    expected = min(usable // 4, max(0, usable - 608))
                    assert event['control_allowance'] == expected
                    assert event['per_direction_quota'] == expected // 2
                    state['allowance'] = expected
                    state['plan'] = event
                    continue
                pair = frozenset((event['sender'], event['receiver']))
                state = active[pair]
                c = state['contact']
                direction = (event['sender'], event['receiver'])
                if kind == 'control_complete':
                    pending = state['pending']
                    assert pending is not None and pending['direction'] == direction
                    assert pending['bytes'] == event['bytes']
                    close(pending['finish'], event['time'])
                    if not event['lost']:
                        state['frames'] += 1
                        if policy == 'compact':
                            state['controls'][event['sender']] = pending['packet']
                    state['pending'] = None
                elif kind == 'compact_summary_received':
                    received = state['controls'][event['sender']]
                    assert event['salt'] == received['salt']
                    assert set(event['tokens']) == {t.hex() for t in received['tokens']}
                    state['received'][event['sender']] = received
                elif kind == 'compact_filter':
                    summary = state['received'].get(event['receiver'])
                    assert event['summary_available'] == (summary is not None)
                    token = (hashlib.sha256(summary['salt'].to_bytes(8, 'big') +
                             event['id'].encode('utf-8')).digest()[:8] if summary else None)
                    expected = summary is not None and token in summary['tokens']
                    assert event['skipped'] == expected
                    state['filters'][direction + (event['id'],)] = expected
                    skip_count += int(expected)
                else:
                    assert event['time'] >= state['last_finish'] - 1e-10
                    state['last_finish'] = event['finish']
                    state['spent'] += event['bytes']
                    assert state['spent'] <= c.budget
                    assert event['finish'] <= c.start + c.duration + 1e-10
                    close(event['finish'] - event['time'], event['bytes'] / c.rate)
                    byte_sums[kind] += event['bytes']
                    seconds[kind] += event['finish'] - event['time']
                    counts[kind] += 1
                    if kind == 'control_attempt':
                        state['control_spent'] += event['bytes']
                        pending = dict(direction=direction, bytes=event['bytes'], finish=event['finish'])
                        if policy == 'compact':
                            packet = parse_packet(event['packet_hex'])
                            assert len(packet['raw']) == event['bytes']
                            assert packet['salt'] == state['plan']['salt']
                            assert state['control_spent'] <= state['allowance']
                            prior = state['by_direction'][event['sender']]
                            assert event['repeated'] == bool(prior)
                            if prior:
                                assert packet['raw'] == prior[0]
                            prior.append(packet['raw'])
                            assert len(prior) <= 2
                            assert sum(map(len, prior)) <= state['plan']['per_direction_quota']
                            pending['packet'] = packet
                        state['pending'] = pending
                    elif policy == 'inventory':
                        assert state['frames'] == 2, 'inventory data before both full frames'
                    else:
                        assert not state['filters'][direction + (event['id'],)]
                        unfiltered_attempts += int(event['receiver'] not in state['received'])
            row = by_key[(count, 3, loss, 2048, policy)]
            assert byte_sums['attempt'] == row['attempted_bytes']
            assert byte_sums['control_attempt'] == row['counters'].get('control_bytes', 0)
            assert counts['attempt'] == row['counters'].get('attempts', 0)
            assert counts['control_attempt'] == row['counters'].get('control_attempts', 0)
            close(sum(seconds.values()), row['total_transmission_seconds'])
            if policy == 'compact':
                assert skip_count == row['counters'].get('inventory_skips', 0)
                assert sum(2 - len(s['received']) for s in states) == row['counters'].get('compact_fallback_directions', 0)
                for state in states:
                    for sender, planned in state['plan']['frame_counts'].items():
                        assert len(state['by_direction'][sender]) == planned, 'loss changed fixed repetition schedule'
                assert unfiltered_attempts > 0, 'replay failed to exercise unfiltered payload fallback'
            write(folder / f'trace_{policy}_contacts{count}_loss{loss}_seed3.json', sim.trace)
            return dict(policy=policy, contacts=count, loss=loss, reserved_bytes=byte_sums,
                        attempts=counts, observed_fallback_data_attempts=unfiltered_attempts)
        finally:
            for store in stores.values():
                store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    runs = load(folder / 'runs.json')
    by_key = {key(row): row for row in runs}
    assert len(runs) == len(by_key) == 2160
    assert set(r['policy'] for r in runs) == {'direct', 'epidemic', 'inventory', 'compact'}
    baseline_checks = []
    baseline_root = ROOT / 'fixtures' / 'baselines'
    for name, digest in load(baseline_root / 'SHA256SUMS.json').items():
        assert hashlib.sha256((baseline_root / name).read_bytes()).hexdigest() == digest, name
    for version, expected in (('v0.1', 1080), ('v0.2', 1620)):
        old_folder = baseline_root / version
        old = load(old_folder / 'runs.json')
        assert len(old) == expected
        for row in old:
            assert all(by_key[key(row)][field] == value for field, value in row.items()), key(row)
        inputs = list((baseline_root / 'inputs').glob('*.json'))
        assert len(inputs) == 90
        for path in inputs:
            assert path.read_bytes() == (folder / 'inputs' / path.name).read_bytes(), path.name
        baseline_checks.append(dict(folder=str(old_folder.relative_to(ROOT)), compared_runs=expected, every_original_field_exact=True,
                                    byte_identical_inputs=len(inputs)))
    manifest = load(folder / 'manifest.json')
    for name, digest in manifest.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    for row in runs:
        counters = row['counters']
        assert row['in_flight'] == row['pending_control_attempts'] == 0
        assert counters.get('attempts', 0) == counters.get('completed_transfers', 0)
        assert counters.get('completed_transfers', 0) == sum(v for k, v in counters.items() if k.startswith('outcome_'))
        assert counters.get('control_attempts', 0) == counters.get('control_completions', 0)
        if row['policy'] == 'inventory':
            assert counters.get('failed_handshakes', 0) == counters.get('control_losses', 0) + counters.get('control_budget_rejected', 0)
        elif row['policy'] == 'compact':
            assert counters.get('failed_handshakes', 0) == 0
        assert row['total_attempted_bytes'] == row['attempted_bytes'] + counters.get('control_bytes', 0)
        close(row['total_transmission_seconds'], counters.get('transmission_seconds', 0) + counters.get('control_seconds', 0))
        close(row['total_attempted_bytes'] / 128, row['total_transmission_seconds'])
        assert row['total_attempted_bytes'] <= counters.get('offered_contact_bytes', 0)
        assert 0 <= row['delivered'] <= row['generated'] == 40
        assert row['peak_queue_bytes'] <= row['capacity']
    stats = check_statistics(folder, runs)
    traces = [check_trace(folder, by_key, policy, count, loss)
              for policy in ('inventory', 'compact') for count in (40, 100, 240) for loss in (0.0, .75)]
    tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                           cwd=ROOT, capture_output=True, text=True)
    (folder / 'unittest.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
    assert tests.returncode == 0, 'unit tests failed; see unittest.txt'
    totals = {}
    for policy in ('direct', 'epidemic', 'inventory', 'compact'):
        rows = [r for r in runs if r['policy'] == policy]
        assert len(rows) == 540
        totals[policy] = dict(runs=len(rows), generated=sum(r['generated'] for r in rows),
            delivered=sum(r['delivered'] for r in rows), total_bytes=sum(r['total_attempted_bytes'] for r in rows),
            payload_bytes=sum(r['attempted_bytes'] for r in rows),
            control_bytes=sum(r['counters'].get('control_bytes', 0) for r in rows),
            duplicates=sum(r['counters'].get('duplicates', 0) for r in rows))
    validation = dict(status='passed', baseline_checks=baseline_checks,
        source_hashes_verified=len(manifest), independent_statistics=stats, trace_replays=traces,
        validator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), policy_totals=totals,
        scope='Twelve independently accounted trace replays; compact binary frames decoded independently; successful peer summary required for skips; missing summary permits fallback; fixed repetitions; per-contact data/control time and byte bounds. Synthetic transport only, not BLE or security validation.')
    write(folder / 'validation.json', validation)
    print(json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
