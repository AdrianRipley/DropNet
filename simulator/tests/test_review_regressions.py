# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent coordinator reproductions from implementation review."""
from pathlib import Path
import tempfile
import unittest
from dropnet.core import Message, Contact, Store, Simulator
from dropnet.experiments import interval, run_one


class ReviewRegressions(unittest.TestCase):
    def test_pause_preserves_contact_and_rng(self):
        def execute(split):
            with tempfile.TemporaryDirectory() as d:
                stores = {n: Store(Path(d)/n, n) for n in ('A', 'B')}
                try:
                    sim = Simulator(stores, loss_seed=8, trace=True)
                    messages = [Message(n, 'A', 'B', 0, 100, 'abcd') for n in ('m1','m2')]
                    contacts = [Contact(1, 'A', 'B', 25, 10, 1000, .2)]
                    sim.run(messages, contacts, horizon=2 if split else 30)
                    if split:
                        self.assertEqual(len(stores['B'].deliveries()), 0)
                        self.assertEqual(sim.counters['attempts'], 1)
                        sim.run(horizon=30)
                    return dict(sim.counters), sim.trace, stores['B'].deliveries()
                finally:
                    for s in stores.values(): s.close()
        self.assertEqual(execute(False), execute(True))

    def test_expiry_accounting_before_and_after_attempt(self):
        messages = [Message(n, 'N00', 'N01', 0, 2, 'abcd') for n in ('a','b')]
        result = run_one(messages, [Contact(1,'N00','N01',10,100,1000)], 'direct', 0, 1000, 20)
        self.assertEqual(result['delivered'], 0)
        self.assertEqual(result['counters']['attempts'], 1)
        self.assertEqual(result['counters']['outcome_ttl_rejected'], 1)
        self.assertEqual(result['counters']['preflight_ttl_rejected'], 1)

    def test_in_flight_is_not_delivery(self):
        result = run_one([Message('m','N00','N01',0,100,'abcd')],
                         [Contact(1,'N00','N01',20,10,1000)], 'direct',0,1000,2)
        self.assertEqual(result['in_flight'], 1)
        self.assertEqual(result['delivered'], 0)

    def test_small_sample_uncertainty(self):
        self.assertIsNone(interval([1])['sd'])
        self.assertIsNone(interval([1])['ci95'])
        # Two samples: df=1 t(0.975)=12.706205, standard error=0.5.
        self.assertAlmostEqual(interval([0,1])['ci95'][1], .5+12.706205*.5)
