# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import tempfile
import unittest

from dropnet import Message, Contact, Store, Simulator


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stores = {}
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.close)

    def close(self):
        for store in self.stores.values():
            store.close()
        self.stores.clear()

    def nodes(self, names='ABC', capacity=10000, dedup_capacity=100):
        for name in names:
            self.stores[name] = Store(Path(self.tmp.name) / (name + '.sqlite'), name, capacity, dedup_capacity)
        return self.stores

    def reopen(self, name):
        old = self.stores[name]
        old.close()
        self.stores[name] = Store(Path(self.tmp.name) / (name + '.sqlite'), name, old.capacity, old.dedup_capacity)

    def msg(self, **kw):
        return Message(**(dict(id='m', source='A', destination='C', created=0, expires=100,
                              text='test', hop_limit=8) | kw))

    def contact(self, start, a, b, **kw):
        return Contact(**(dict(start=start, a=a, b=b, duration=10, rate=100, budget=1000) | kw))

    def test_restart_and_exact_latency(self):
        sim = Simulator(self.nodes(), trace=True)
        sim.run([self.msg()], [self.contact(1, 'A', 'B')], horizon=5)
        self.reopen('B')
        sim.run(contacts=[self.contact(10, 'B', 'C')], horizon=20)
        self.assertEqual(self.stores['C'].deliveries(), [('m', 11.0, 0.0)])
        self.assertEqual(sim.counters['local_delivery'], 1)

    def test_disconnected(self):
        sim = Simulator(self.nodes('ABCD'))
        sim.run([self.msg(destination='D')], [self.contact(1, 'A', 'B'), self.contact(20, 'C', 'D')])
        self.assertEqual(sim.counters['local_delivery'], 0)

    def test_expired_before_and_during_transfer(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg(expires=1)], [self.contact(1, 'A', 'C')])
        self.assertEqual(sim.counters['attempts'], 0)
        sim.run([self.msg(id='during', created=2, expires=3)], [self.contact(2.5, 'A', 'C')])
        self.assertEqual(sim.counters['ttl_rejected'], 1)
        self.assertFalse(self.stores['C'].deliveries())

    def test_expiry_equality_rejected(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg(expires=2)], [self.contact(1, 'A', 'C')])
        self.assertEqual(sim.counters['ttl_rejected'], 1)

    def test_cycles_dedup_restart(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg()], [self.contact(1, 'A', 'B'), self.contact(5, 'B', 'C')], horizon=10)
        self.reopen('C')
        sim.run(contacts=[self.contact(11, 'A', 'C'), self.contact(20, 'B', 'A'),
                          self.contact(30, 'B', 'C')])
        self.assertEqual(len(self.stores['C'].deliveries()), 1)
        self.assertGreaterEqual(sim.counters['duplicates'], 3)

    def test_drop_new_storage_and_expire_before_admission(self):
        self.nodes(capacity=100)
        a = self.stores['A']
        self.assertEqual(a.accept(self.msg(expires=2), 0), 'accepted_transfers')
        self.assertEqual(a.accept(self.msg(id='second'), 1), 'storage_rejected')
        self.assertEqual(a.accept(self.msg(id='second'), 2), 'accepted_transfers')
        self.assertEqual(a.used, 100)
        a.check()

    def test_receiver_storage_rejection(self):
        self.nodes()
        self.stores['B'].close()
        self.stores['B'] = Store(Path(self.tmp.name) / 'tiny.sqlite', 'B', 99)
        sim = Simulator(self.stores)
        sim.run([self.msg()], [self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['storage_rejected'], 1)
        self.assertEqual(sim.counters['attempted_bytes'], 100)

    def test_total_loss_retry_accounting(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg()], [self.contact(t, 'A', 'C', loss=1) for t in (1, 20, 40)])
        self.assertEqual(sim.counters['attempts'], 3)
        self.assertEqual(sim.counters['lost_transfers'], 3)
        self.assertEqual(sim.counters['attempted_bytes'], 300)
        self.assertFalse(self.stores['C'].deliveries())

    def test_insufficient_budget_and_duration(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg()], [self.contact(1, 'A', 'C', budget=0),
                              self.contact(20, 'A', 'C', budget=99),
                              self.contact(40, 'A', 'C', duration=.99)])
        self.assertEqual(sim.counters['attempts'], 0)
        self.assertEqual(sim.counters['budget_rejected'], 3)

    def test_hop_limit_counts_destination(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg(hop_limit=1)], [self.contact(1, 'A', 'B'), self.contact(20, 'B', 'C')])
        self.assertFalse(self.stores['C'].deliveries())
        self.assertEqual(sim.counters['hop_rejected'], 1)
        sim.run(contacts=[self.contact(40, 'A', 'C')])
        self.assertEqual(len(self.stores['C'].deliveries()), 1)

    def test_zero_hop_limit(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg(hop_limit=0)], [self.contact(1, 'A', 'C')])
        self.assertEqual(sim.counters['hop_rejected'], 1)

    def test_validation_utf8(self):
        self.assertEqual(self.msg(text='é' * 256).size, 608)
        for text in ('é' * 257, '\ud800', b'test'):
            with self.assertRaises(ValueError):
                self.msg(text=text)
        for kw in (dict(expires=0), dict(created=float('nan')), dict(hops=2, hop_limit=1),
                   dict(hop_limit=True)):
            with self.assertRaises(ValueError):
                self.msg(**kw)

    def test_direct_baseline(self):
        sim = Simulator(self.nodes(), policy='direct')
        sim.run([self.msg()], [self.contact(1, 'A', 'B'), self.contact(20, 'B', 'C')])
        self.assertEqual(sim.counters['attempts'], 0)
        sim.run(contacts=[self.contact(40, 'A', 'C')])
        self.assertEqual(len(self.stores['C'].deliveries()), 1)

    def test_overlapping_contact_no_backwards_hop(self):
        sim = Simulator(self.nodes(), trace=True)
        sim.run([self.msg()], [self.contact(1, 'A', 'B'), self.contact(1.5, 'B', 'C')])
        self.assertEqual(sim.counters['busy_contacts'], 1)
        self.assertFalse(self.stores['C'].deliveries())
        self.assertEqual([e['time'] for e in sim.trace], sorted(e['time'] for e in sim.trace))

    def test_snapshot_excludes_new_copy(self):
        sim = Simulator(self.nodes())
        sim.run([self.msg()], [self.contact(1, 'A', 'B')])
        self.assertEqual(sim.counters['attempts'], 1)
        self.assertEqual(sim.counters['duplicates'], 0)

    def test_generation_during_transfer_chronological(self):
        sim = Simulator(self.nodes(), trace=True)
        sim.run([self.msg(), self.msg(id='later', created=1.5)], [self.contact(1, 'A', 'B')])
        self.assertEqual([e['time'] for e in sim.trace], sorted(e['time'] for e in sim.trace))
        self.assertEqual([m.id for m in self.stores['B'].messages(3)], ['m'])

    def test_bounded_dedup_tradeoff(self):
        self.nodes(dedup_capacity=1)
        c = self.stores['C']
        m = self.msg()
        self.assertEqual(c.accept(m, 1), 'local_delivery')
        c.accept(self.msg(id='other'), 2)
        self.reopen('C')
        self.assertEqual(self.stores['C'].accept(m, 3), 'local_delivery')
        self.stores['C'].check()

    def test_deterministic_replay(self):
        def replay(folder):
            stores = {n: Store(Path(self.tmp.name) / (folder+n), n) for n in 'ABC'}
            sim = Simulator(stores, loss_seed=17, trace=True)
            sim.run([self.msg()], [self.contact(t, 'A', 'C', loss=.4) for t in range(1, 80, 10)])
            result = (dict(sim.counters), sim.trace, stores['C'].deliveries())
            for s in stores.values():
                s.close()
            return result
        self.assertEqual(replay('first'), replay('second'))

    def test_input_errors(self):
        sim = Simulator(self.nodes())
        with self.assertRaises(ValueError):
            sim.run([self.msg(), self.msg()])
        with self.assertRaises(ValueError):
            self.contact(0, 'A', 'B', rate=0)
        with self.assertRaises(ValueError):
            self.contact(0, 'A', 'B', loss=float('nan'))


if __name__ == '__main__':
    unittest.main()
