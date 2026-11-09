# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from dropnet import Contact
from dropnet.experiments import aggregate, inputs, interval, portable_command, report, restart_demo, run_one


class ExperimentTests(unittest.TestCase):
    def test_command_metadata_removes_absolute_paths_in_both_cli_forms(self):
        root = Path('/example/repository/simulator')
        arguments = [str(root / 'scripts/run_experiments.py'), '--output',
                     '/external/private/output', '--output=/external/private/output',
                     '--replay=' + str(root / 'fixtures/input.json'),
                     '--output=scripts/outputs/example', 'relative=name.json']
        self.assertEqual(portable_command(arguments, root), [
            'scripts/run_experiments.py', '--output', '<external-path>',
            '--output=<external-path>', '--replay=fixtures/input.json',
            '--output=scripts/outputs/example', 'relative=name.json'])

    def test_inputs_repeat_and_density_pairing(self):
        m1, c1 = inputs(7, 40)
        m2, c2 = inputs(7, 100)
        self.assertEqual(m1, m2)
        self.assertEqual(set(c1), set(c2).intersection(c1))
        self.assertEqual((m1, c1), inputs(7, 40))
        self.assertEqual(len(m1), 40)
        self.assertNotEqual(m1, inputs(8, 40)[0])

    def test_student_interval_and_zero_deliveries(self):
        s = interval([0, 1] * 15)
        self.assertAlmostEqual(s['mean'], .5)
        self.assertLess(s['ci95'][0], .5)
        self.assertGreater(s['ci95'][1], .5)
        self.assertIsNone(interval([1])['ci95'])
        messages, _ = inputs(0, 0)
        metrics = run_one(messages, [], 'epidemic', 17, 2048, 3600)
        self.assertEqual(metrics['delivered'], 0)
        self.assertIsNone(metrics['median_latency'])
        self.assertIsNone(metrics['bytes_per_delivered'])
        self.assertEqual(metrics['attempted_bytes'], 0)

    def test_full_run_deterministic_replay_and_accounting(self):
        messages, contacts = inputs(3, 100)
        contacts = [Contact(**(asdict(c) | {'loss': .25})) for c in contacts]
        a = run_one(messages, contacts, 'epidemic', 100003, 2048, 3600)
        b = run_one(messages, contacts, 'epidemic', 100003, 2048, 3600)
        self.assertEqual(a, b)
        self.assertLessEqual(a['peak_queue_bytes'], 2048)
        self.assertEqual(a['counters']['attempts'], a['counters']['completed_transfers'])
        self.assertEqual(a['delivered'], a['counters'].get('local_delivery', 0))

    def test_restart_demo_persists_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'demo'
            restart_demo(folder)
            for filename in ('trace.json', 'result.json', 'A.sqlite', 'B.sqlite', 'C.sqlite'):
                self.assertTrue((folder / filename).exists())

    def test_pooled_total_traffic_includes_zero_delivery_runs(self):
        rows = []
        for seed, delivered in ((0, 0), (1, 1)):
            for policy in ('direct', 'epidemic', 'inventory', 'compact'):
                rows.append(dict(seed=seed, contacts=40, loss=0, capacity=2048,
                    policy=policy, generated=40, delivered=delivered,
                    delivery_fraction=delivered/40, attempted_bytes=100,
                    total_attempted_bytes=1000 if seed == 0 else 100,
                    bytes_per_generated=2.5, bytes_per_delivered=100 if delivered else None,
                    total_bytes_per_generated=25 if seed == 0 else 2.5,
                    total_bytes_per_delivered=100 if delivered else None,
                    total_transmission_seconds=1, median_latency=None,
                    peak_queue_bytes=100, counters={'control_bytes':900 if seed == 0 else 0}))
        summaries, paired = aggregate(rows)
        self.assertEqual(paired[0]['compact_minus_epidemic_delivery']['mean'], 0)
        self.assertEqual(paired[0]['compact_minus_inventory_relative_traffic'], 0)
        for summary in summaries:
            self.assertEqual(summary['pooled_total_bytes_per_delivered'], 1100)
            self.assertEqual(summary['total_bytes_per_delivered']['mean'], 100)
        with tempfile.TemporaryDirectory() as tmp:
            report(Path(tmp), summaries, paired, dict(seeds=2, capacities=[2048]))
            text = (Path(tmp) / 'report.html').read_text()
            self.assertIn('1100.00', text)
            self.assertIn('punti % [IC 95%]', text)
            self.assertNotIn('&#x27;ci95&#x27;', text)

    def test_aggregation_pairing_and_offline_html(self):
        rows = []
        for seed in range(30):
            for policy, fraction in [('direct', .1), ('epidemic', .3)]:
                rows.append(dict(seed=seed, contacts=40, loss=0, capacity=2048, policy=policy,
                                 delivery_fraction=fraction, bytes_per_generated=10,
                                 peak_queue_bytes=100, median_latency=None, bytes_per_delivered=100,
                                 attempted_bytes=400, delivered=int(fraction*40)))
        summaries, paired = aggregate(rows)
        self.assertAlmostEqual(paired[0]['epidemic_minus_direct']['mean'], .2)
        self.assertEqual(len(paired[0]['convergence']), 3)
        with tempfile.TemporaryDirectory() as tmp:
            report(Path(tmp), summaries, paired, dict(seeds=30, capacities=[2048]))
            text = (Path(tmp) / 'report.html').read_text()
            self.assertIn('<svg', text)
            self.assertNotIn('<script', text)
            self.assertNotIn('https://', text)


if __name__ == '__main__':
    unittest.main()
