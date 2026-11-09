# SPDX-License-Identifier: GPL-3.0-or-later
"""Binary compact summaries and independent, loss-tolerant contact exchange."""
from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import test_core
from dropnet import Simulator, Store
from dropnet.compact import (
    HEADER, Summary, contact_salt, control_budget, decode_summary,
    encode_summary, fingerprint, plan_frames,
)


class CodecTests(unittest.TestCase):
    def test_saved_cross_language_conformance_vectors(self):
        fixture = Path(__file__).parent / 'fixtures' / 'compact_vectors.json'
        vectors = json.loads(fixture.read_text(encoding='utf-8'))['vectors']
        for vector in vectors:
            with self.subTest(vector=vector['name']):
                tokens = [fingerprint(mid, vector['salt']) for mid in vector['ids']]
                self.assertEqual([t.hex() for t in tokens], vector['fingerprints'])
                self.assertEqual(encode_summary(vector['salt'], tokens).hex(), vector['frame_hex'])
                summary = decode_summary(bytes.fromhex(vector['frame_hex']))
                self.assertEqual(summary.salt, vector['salt'])
                self.assertEqual(summary.tokens, frozenset(bytes.fromhex(t) for t in vector['fingerprints']))

    def test_binary_golden_vector_and_membership(self):
        packet = encode_summary(0x0102030405060708, [b'abcdefgh', b'01234567'])
        self.assertEqual(packet.hex(),
                         '444e435301010002010203040506070830313233343536376162636465666768')
        self.assertEqual(decode_summary(packet),
                         Summary(0x0102030405060708, frozenset([b'abcdefgh', b'01234567'])))
        summary = decode_summary(encode_summary(19, [fingerprint('é' * 20, 19)]))
        self.assertTrue(summary.contains('é' * 20))
        self.assertFalse(summary.contains('different'))
        self.assertEqual(len(fingerprint('id', 0)), 8)

    def test_decoder_rejects_wrong_header_length_and_token_order(self):
        packet = encode_summary(1, [b'11111111', b'22222222'])
        bad = [b'', packet[:15], packet[:-1], packet + b'x',
               b'FAIL' + packet[4:], packet[:4] + b'\x02' + packet[5:],
               packet[:5] + b'\x02' + packet[6:],
               packet[:6] + b'\x00\x03' + packet[8:],
               packet[:16] + b'2222222211111111',
               packet[:16] + b'1111111111111111']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_summary(value)
        self.assertEqual(decode_summary(encode_summary(1, [])).tokens, frozenset())
        for salt in (-1, 2**64, True):
            with self.assertRaises(ValueError):
                fingerprint('x', salt)
        for mid in ('', 'é'*65, '\ud800', b'id'):
            with self.assertRaises(ValueError):
                fingerprint(mid, 0)

    def test_fixed_plan_bounds_repeats_and_sampling(self):
        self.assertEqual(plan_frames([], 1, 128), [])
        self.assertEqual(plan_frames(['a'], 1, 23), [])
        self.assertEqual(list(map(len, plan_frames(['a'], 1, 47))), [24])
        self.assertEqual(list(map(len, plan_frames(['a'], 1, 48))), [24, 24])
        ids = [f'm{i}' for i in range(40)]
        frames = plan_frames(ids, 7, 128)
        self.assertEqual(list(map(len, frames)), [128])
        summary = decode_summary(frames[0])
        self.assertEqual(summary.tokens, frozenset(sorted(fingerprint(mid, 7) for mid in ids)[:14]))
        self.assertEqual(frames, plan_frames(reversed(ids), 7, 128))
        self.assertNotEqual(summary.tokens, decode_summary(plan_frames(ids, 8, 128)[0]).tokens)

    def test_control_cap_reserves_data_and_obeys_time(self):
        self.assertEqual(control_budget(1024, 8, 128), 256)
        self.assertEqual(control_budget(1024, 4, 128), 0)
        self.assertEqual(control_budget(1000, 7, 100), 92)
        for budget in range(1100):
            allowance = control_budget(budget, 20, 100)
            self.assertLessEqual(allowance, budget // 4)
            self.assertGreaterEqual(budget-allowance, min(budget, 608))
        for args in [(True, 8, 128), (10, -1, 1), (10, 1, 0), (10, float('nan'), 1)]:
            with self.assertRaises(ValueError):
                control_budget(*args)

    def test_contact_salt_is_stable_and_rotates(self):
        a = contact_salt(1, 1.0, 'A', 'B')
        self.assertEqual(a, contact_salt(1, 1.0, 'A', 'B'))
        self.assertNotEqual(a, contact_salt(2, 1.0, 'A', 'B'))
        self.assertNotEqual(a, contact_salt(1, 2.0, 'A', 'B'))

    def test_fingerprint_collision_is_not_exact_dedup(self):
        with patch('dropnet.compact.fingerprint', return_value=b'collision'[:8]):
            summary = decode_summary(plan_frames(['known'], 1, 24)[0])
            self.assertTrue(summary.contains('unknown'))


class CompactTests(unittest.TestCase):
    setUp = test_core.ProtocolTests.setUp
    close = test_core.ProtocolTests.close
    nodes = test_core.ProtocolTests.nodes
    reopen = test_core.ProtocolTests.reopen
    msg = test_core.ProtocolTests.msg
    contact = test_core.ProtocolTests.contact

    def draws(self, sim, values):
        values = iter(values)
        sim.rng.random = lambda: next(values)

    def test_empty_inventory_and_small_contact_fallback(self):
        sim = Simulator(self.nodes(), 'compact')
        sim.run(contacts=[self.contact(1, 'A', 'B')], horizon=2)
        self.assertEqual(sim.counters['control_attempts'], 0)
        sim.run([self.msg(created=3, destination='B')],
                [self.contact(3, 'A', 'B', budget=100)])
        self.assertEqual(sim.counters['control_bytes'], 0)
        self.assertEqual(sim.counters['local_delivery'], 1)
        self.assertEqual(sim.counters['attempted_bytes'], 100)

    def test_all_summary_copies_lost_payload_still_delivers(self):
        sim = Simulator(self.nodes(), 'compact', trace=True)
        self.draws(sim, [0, 0, .9])
        sim.run([self.msg(destination='B')], [self.contact(1, 'A', 'B', loss=.5)])
        self.assertEqual(sim.counters['control_losses'], 2)
        self.assertEqual(sim.counters['compact_repeat_frames'], 1)
        self.assertEqual(sim.counters['control_bytes'], 48)
        self.assertEqual(sim.counters['local_delivery'], 1)
        self.assertEqual(sim.counters['failed_handshakes'], 0)
        self.assertAlmostEqual(self.stores['B'].deliveries()[0][1], 2.48)

    def test_directions_independent_and_repeat_is_unconditional(self):
        self.nodes()
        m = self.msg()
        for node in 'AB':
            self.stores[node].accept(m, 0)
        sim = Simulator(self.stores, 'compact', trace=True)
        # A's summary arrives twice, B's is lost twice. Only B->A is suppressed.
        self.draws(sim, [.9, .9, 0, 0, .9])
        sim.run(contacts=[self.contact(1, 'A', 'B', loss=.5)])
        self.assertEqual(sim.counters['control_attempts'], 4)
        self.assertEqual(sim.counters['compact_summary_received'], 2)
        self.assertEqual(sim.counters['compact_fallback_directions'], 1)
        self.assertEqual(sim.counters['inventory_skips'], 1)
        self.assertEqual(sim.counters['duplicates'], 1)
        self.assertEqual(sim.counters['attempts'], 1)
        self.assertEqual(sim.counters['control_bytes'], 96)
        self.assertEqual(sim.counters['compact_repeat_frames'], 2)

    def test_second_copy_recovers_first_loss_without_sender_feedback(self):
        self.nodes()
        m = self.msg(destination='B')
        for node in 'AB':
            self.stores[node].accept(m, 0)
        sim = Simulator(self.stores, 'compact')
        self.draws(sim, [0, .9, 0, .9])
        sim.run(contacts=[self.contact(1, 'A', 'B', loss=.5)])
        self.assertEqual(sim.counters['control_attempts'], 4)
        self.assertEqual(sim.counters['compact_summary_received'], 2)
        self.assertEqual(sim.counters['inventory_skips'], 1)
        self.assertEqual(sim.counters['attempts'], 0)

    def test_partial_summary_and_full_budget_accounting(self):
        sim = Simulator(self.nodes(), 'compact', trace=True)
        messages = [self.msg(id=f'm{i}', destination='B') for i in range(40)]
        sim.run(messages, [self.contact(1, 'A', 'B', budget=1024, duration=8, rate=128)])
        self.assertEqual(sim.counters['compact_partial_frames'], 1)
        self.assertEqual(sim.counters['control_bytes'], 128)
        self.assertEqual(sim.counters['attempts'], 8)
        self.assertEqual(sim.counters['attempted_bytes'], 800)
        self.assertLessEqual(sim.counters['control_seconds'] + sim.counters['transmission_seconds'], 8)
        self.assertEqual(sim.counters['control_completions'], 1)

    def test_stale_summary_and_new_copies_do_not_become_an_oracle(self):
        sim = Simulator(self.nodes(), 'compact')
        m = self.msg(destination='B')
        self.stores['A'].accept(m, 0)
        sim.run([replace(m, source='B', created=1.1)], [self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['attempts'], 1)
        self.assertEqual(sim.counters['duplicates'], 1)

    def test_retained_delivery_summary_survives_restart(self):
        self.nodes()
        m = self.msg(destination='B')
        for node in 'AB':
            self.stores[node].accept(m, 0)
        self.reopen('B')
        sim = Simulator(self.stores, 'compact')
        sim.run(contacts=[self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['inventory_skips'], 1)
        self.assertEqual(sim.counters['attempts'], 0)
        self.assertEqual(len(self.stores['B'].deliveries()), 1)

    def test_expiry_during_control_and_total_loss(self):
        sim = Simulator(self.nodes(), 'compact')
        sim.run([self.msg(expires=1.3)], [self.contact(1, 'A', 'B')], horizon=3)
        self.assertEqual(sim.counters['preflight_ttl_rejected'], 1)
        sim.run([self.msg(id='lost', created=4)], [self.contact(5, 'A', 'C', loss=1)])
        self.assertEqual(sim.counters['control_losses'], 2)
        self.assertEqual(sim.counters['lost_transfers'], 1)
        self.assertEqual(sim.counters['attempted_bytes'], 100)
        self.assertFalse(self.stores['C'].deliveries())

    def test_pause_resume_matches_single_execution(self):
        def execute(prefix, pauses):
            self.close()
            self.stores.update({n: Store(Path(self.tmp.name)/(prefix+n), n) for n in 'ABC'})
            sim = Simulator(self.stores, 'compact', loss_seed=9, trace=True)
            sim.run([self.msg()], [self.contact(1, 'A', 'C', loss=.25)],
                    horizon=1.1 if pauses else 5)
            if pauses:
                self.assertEqual(sim.pending_control_attempts, 1)
                self.assertEqual(sim.counters['control_bytes'], 24)
                sim.run(horizon=1.3)
                self.assertEqual(sim.pending_control_attempts, 1)
                self.assertEqual(sim.counters['control_bytes'], 48)
                sim.run(horizon=1.7)
                self.assertEqual(sim.pending_control_attempts, 0)
                self.assertEqual(sim.counters['attempts'], 1)
                sim.run(horizon=5)
            return dict(sim.counters), sim.trace, self.stores['C'].deliveries()
        self.assertEqual(execute('whole', False), execute('split', True))


if __name__ == '__main__':
    unittest.main()
