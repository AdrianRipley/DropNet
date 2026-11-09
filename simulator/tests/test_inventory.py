# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory transport conformance, including stale snapshots and reserved budgets."""
from dataclasses import replace
import unittest
import test_core
from dropnet import Simulator


class InventoryTests(unittest.TestCase):
    setUp = test_core.ProtocolTests.setUp
    close = test_core.ProtocolTests.close
    nodes = test_core.ProtocolTests.nodes
    reopen = test_core.ProtocolTests.reopen
    msg = test_core.ProtocolTests.msg
    contact = test_core.ProtocolTests.contact
    def test_empty_inventory_still_costs_two_headers(self):
        sim = Simulator(self.nodes(), 'inventory')
        sim.run(contacts=[self.contact(1, 'A', 'B', budget=16)])
        self.assertEqual(sim.counters['control_bytes'], 16)
        self.assertEqual(sim.counters['control_completions'], 2)
        self.assertEqual(sim.counters['attempts'], 0)
        self.assertEqual(sim.counters['failed_handshakes'], 0)

    def test_control_and_payload_share_exact_budget(self):
        sim = Simulator(self.nodes(), 'inventory')
        sim.run([self.msg(destination='B')],
                [self.contact(1, 'A', 'B', budget=149)], horizon=5)
        self.assertEqual(sim.counters['control_bytes'], 50)
        self.assertEqual(sim.counters['attempts'], 0)
        self.assertEqual(sim.counters['budget_rejected'], 1)
        sim.run(contacts=[self.contact(6, 'A', 'B', budget=150)])
        self.assertEqual(sim.counters['control_bytes'], 100)
        self.assertEqual(sim.counters['attempted_bytes'], 100)
        self.assertEqual(sim.counters['local_delivery'], 1)
        self.assertAlmostEqual(self.stores['B'].deliveries()[0][1], 7.5)

    def test_union_expiry_eviction_restart_utf8(self):
        self.nodes(dedup_capacity=1)
        a = self.stores['A']
        a.accept(self.msg(id='queued'), 0)
        a.accept(self.msg(id='delivered', destination='A'), 0)
        self.assertEqual(a.inventory(0), {'queued', 'delivered'})
        self.reopen('A')
        a = self.stores['A']
        self.assertEqual(a.inventory(0), {'queued', 'delivered'})
        a.accept(self.msg(id='é'*20, destination='A', expires=2), 0)
        self.assertEqual(a.inventory(0), {'queued', 'é'*20})
        sim = Simulator(self.stores, 'inventory')
        sim.run(contacts=[self.contact(1, 'A', 'B', budget=0)])
        self.assertEqual(sim.counters['control_attempts'], 0)
        sim.run(contacts=[self.contact(1, 'A', 'B')], horizon=1.1)
        self.assertEqual(sim.counters['control_bytes'], 8+34+42)
        self.assertEqual(a.inventory(2), {'queued'})
        self.assertNotIn('delivered', a.inventory(2))  # audit is not an oracle

    def test_exact_frames_bidirectional_and_no_new_offer(self):
        sim = Simulator(self.nodes(), 'inventory', trace=True)
        sim.run([self.msg(), self.msg(id='reverse', source='B', destination='A')],
                [self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['control_attempts'], 2)
        self.assertEqual(sim.counters['control_completions'], 2)
        self.assertEqual(sim.counters['control_bytes'], 84)
        self.assertAlmostEqual(sim.counters['control_seconds'], .84)
        self.assertEqual(sim.counters['attempts'], 2)
        self.assertEqual(sim.counters['attempted_bytes'], 200)
        self.assertEqual(sim.counters['duplicates'], 0)
        self.assertEqual(self.stores['A'].deliveries(), [('reverse', 3.84, 0.0)])
        self.assertEqual([e['event'] for e in sim.trace if e['event'] in ('control_complete', 'attempt')],
                         ['control_complete', 'control_complete', 'attempt', 'attempt'])

    def test_loss_at_either_frame_aborts_without_payload(self):
        for draws, attempts in [([0], 1), ([1, 0], 2)]:
            sim = Simulator(self.nodes() if not self.stores else self.stores, 'inventory')
            values = iter(draws)
            sim.rng.random = lambda: next(values)
            sim.run([self.msg()], [self.contact(1, 'A', 'B', loss=.5)])
            self.assertEqual(sim.counters['control_attempts'], attempts)
            self.assertEqual(sim.counters['control_completions'], attempts)
            self.assertEqual(sim.counters['control_losses'], 1)
            self.assertEqual(sim.counters['failed_handshakes'], 1)
            self.assertEqual(sim.counters['attempts'], 0)
            self.assertEqual(sim.counters['control_bytes'], 42 + (8 if attempts == 2 else 0))
            self.close()
            # Same SQLite files retain source copy; no hidden delivery occurs.

    def test_budget_and_duration_at_both_stages(self):
        sim = Simulator(self.nodes(), 'inventory')
        sim.run([self.msg()], [self.contact(1, 'A', 'B', budget=41),
            self.contact(3, 'A', 'B', budget=49),
            self.contact(5, 'A', 'B', duration=.41),
            self.contact(7, 'A', 'B', duration=.49)])
        self.assertEqual(sim.counters['failed_handshakes'], 4)
        self.assertEqual(sim.counters['control_attempts'], 2)
        self.assertEqual(sim.counters['control_bytes'], 84)
        self.assertEqual(sim.counters['attempts'], 0)

    def test_expiry_during_exchange_and_payload(self):
        sim = Simulator(self.nodes(), 'inventory')
        sim.run([self.msg(expires=1.5)], [self.contact(1, 'A', 'C')])
        self.assertEqual(sim.counters['preflight_ttl_rejected'], 1)
        self.assertEqual(sim.counters['attempts'], 0)
        sim.run([self.msg(id='during', created=3, expires=4)],
                [self.contact(3, 'A', 'C')])
        self.assertEqual(sim.counters['outcome_ttl_rejected'], 1)
        self.assertFalse(self.stores['C'].deliveries())

    def test_stale_snapshot_no_receiver_query(self):
        sim = Simulator(self.nodes(), 'inventory')
        m = self.msg(destination='B')
        # Empty B at contact start; local generation at B during handshake gives
        # B the same ID. Filtering must still offer A's old candidate.
        local = replace(m, source='B', created=1.1)
        self.stores['A'].accept(m, 0)
        sim.run([local], [self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['attempts'], 1)
        self.assertEqual(sim.counters['duplicates'], 1)

    def test_retained_delivery_skipped_after_restart_both_directions(self):
        sim = Simulator(self.nodes(), 'inventory')
        m = self.msg(destination='B')
        self.stores['A'].accept(m, 0)
        self.stores['B'].accept(m, 0)
        reverse = self.msg(id='r', source='B', destination='A')
        self.stores['B'].accept(reverse, 0)
        self.stores['A'].accept(reverse, 0)
        self.reopen('B')
        sim.run(contacts=[self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['inventory_skips'], 2)
        self.assertEqual(sim.counters['attempts'], 0)

    def test_pause_resume_each_frame_and_payload_with_restart(self):
        def execute(pauses):
            self.close()
            # Unique paths per execution preserve independent initial conditions.
            suffix = 'split' if pauses else 'whole'
            from dropnet import Store
            from pathlib import Path
            self.stores.update({n: Store(Path(self.tmp.name)/(suffix+n), n) for n in 'ABC'})
            sim = Simulator(self.stores, 'inventory', loss_seed=8, trace=True)
            sim.run([self.msg()], [self.contact(1, 'A', 'C')], horizon=1.1 if pauses else 5)
            if pauses:
                self.assertEqual(sim.pending_control_attempts, 1)
                self.assertEqual(sim.counters['control_bytes'], 42)
                self.assertEqual(sim.counters['attempts'], 0)
                sim.run(horizon=1.45)
                self.assertEqual(sim.pending_control_attempts, 1)
                self.assertEqual(sim.counters['control_bytes'], 50)
                sim.run(horizon=1.7)
                self.assertEqual(sim.pending_control_attempts, 0)
                self.assertEqual(sim.counters['attempts'], 1)
                self.assertEqual(sim.counters['completed_transfers'], 0)
                old = self.stores['C']
                old.close()
                self.stores['C'] = Store(Path(self.tmp.name)/(suffix+'C'), 'C')
                sim.run(horizon=5)
            return dict(sim.counters), sim.trace, self.stores['C'].deliveries()
        self.assertEqual(execute(False), execute(True))
