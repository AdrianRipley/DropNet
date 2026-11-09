# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest

from dropnet import Message, Store
from dropnet.local_node import (DATA, SUMMARY, HEADER, MAX_DATAGRAM, LocalNode,
                                packet, unpack_packet, encode_message, decode_message)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, destination):
        self.sent.append((data, destination))
        return len(data)


class LocalTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stores = {n: Store(Path(self.tmp.name) / n, n, 65536, 256) for n in 'AB'}
        self.addCleanup(lambda: [s.close() for s in self.stores.values()])
        self.nodes = {n: LocalNode(self.stores[n], FakeSocket()) for n in 'AB'}
        self.nodes['A'].begin(22, 7)
        self.nodes['B'].begin(11, 7)

    def message(self, **kw):
        now = time.time()
        return Message(**(dict(id='m', source='A', destination='B', created=now-1,
                              expires=now+60, text='é' * 5) | kw))

    def transfer(self, sender='A', receiver='B'):
        sock = self.nodes[sender].sock
        data, sock.sent = sock.sent, []
        port = 11 if sender == 'A' else 22
        for body, _ in data:
            self.nodes[receiver].receive(body, ('127.0.0.1', port))

    def test_envelope_rejects_truncation_trailing_oversize(self):
        frame = packet(7, DATA, b'{}')
        self.assertEqual(unpack_packet(frame), (7, DATA, b'{}'))
        for invalid in (frame[:-1], frame+b'x', b'x', b'x'*(MAX_DATAGRAM+1)):
            with self.assertRaises(ValueError):
                unpack_packet(invalid)
        with self.assertRaises(ValueError):
            packet(7, DATA, b'x' * MAX_DATAGRAM)

    def test_message_json_strict_validation(self):
        msg = self.message()
        self.assertEqual(decode_message(encode_message(msg)), msg)
        for value in (b'{', b'{"id":"a","id":"b"}', b'[]', b'\xff', b'['*1100):
            with self.assertRaises(ValueError):
                decode_message(value)
        invalid = json.loads(encode_message(msg))
        invalid.update(created=10**400, expires=10**401)
        with self.assertRaises(ValueError):
            decode_message(json.dumps(invalid).encode())

    def test_maximal_legal_escaped_message_fits_datagram(self):
        message = self.message(id='\x00'*128, source='\x01'*128,
                               destination='\x02'*128, text='\x00'*512, hops=1)
        body = encode_message(message)
        frame = packet(7, DATA, body)
        self.assertGreater(len(frame), 2048)
        self.assertLessEqual(len(frame), MAX_DATAGRAM)
        self.assertEqual(decode_message(unpack_packet(frame)[2]), message)

    def test_wire_limit_rejection_does_not_block_other_messages(self):
        self.stores['A'].accept(self.message(id='unbounded', hop_limit=1000000), time.time())
        self.stores['A'].accept(self.message(id='ordinary'), time.time())
        self.nodes['A'].begin(22, 7)
        self.assertEqual(self.nodes['A'].offer()['datagrams'], 1)
        self.assertEqual(self.nodes['A'].counts['wire_rejected'], 1)
        self.assertEqual(len(self.stores['A'].messages(time.time())), 2)

    def test_independent_received_summary_suppresses_data(self):
        message = self.message()
        self.stores['A'].accept(message, time.time())
        self.stores['B'].accept(replace(message, hops=1), time.time())
        self.nodes['A'].begin(22, 8)
        self.nodes['B'].begin(11, 8)
        self.nodes['B'].send_summaries()
        self.transfer('B', 'A')
        result = self.nodes['A'].offer()
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['datagrams'], 0)
        self.assertFalse(result['fallback'])

    def test_missing_summaries_fall_back_and_deliver(self):
        message = self.message()
        self.stores['A'].accept(message, time.time())
        self.nodes['A'].begin(22, 7)
        result = self.nodes['A'].offer()
        self.assertTrue(result['fallback'])
        self.transfer()
        self.assertEqual([d[0] for d in self.stores['B'].deliveries()], ['m'])

    def test_control_drop_does_not_create_summary(self):
        self.stores['B'].accept(self.message(), time.time())
        self.nodes['B'].begin(11, 7)
        self.nodes['A'].drop['control'] = 256
        self.nodes['B'].send_summaries()
        self.transfer('B', 'A')
        self.assertFalse(self.nodes['A'].summaries)
        self.assertEqual(self.nodes['A'].counts['injected_control_drops'], 2)

    def test_stale_peer_malformed_and_zero_hop_rejected(self):
        receiver = self.nodes['B']
        frame = packet(7, DATA, encode_message(replace(self.message(), hops=1)))
        receiver.receive(frame, ('127.0.0.1', 999))
        receiver.receive(packet(6, DATA, encode_message(replace(self.message(), hops=1))), ('127.0.0.1', 11))
        receiver.receive(frame[:-1], ('127.0.0.1', 11))
        receiver.receive(packet(7, DATA, encode_message(self.message())), ('127.0.0.1', 11))
        self.assertFalse(self.stores['B'].deliveries())
        self.assertEqual(receiver.counts['malformed_or_stale'], 4)

    def test_snapshot_and_actual_data_budget(self):
        self.stores['A'].accept(self.message(), time.time())
        self.assertEqual(self.nodes['A'].offer()['datagrams'], 0)
        self.nodes['A'].begin(22, 7)
        # Timestamp length may differ, so inspect the frozen offer itself.
        size = HEADER.size + len(encode_message(replace(self.nodes['A'].offers[0], hops=1)))
        self.assertEqual(self.nodes['A'].offer(size-1)['datagrams'], 0)
        self.nodes['A'].begin(22, 7)
        result = self.nodes['A'].offer(size)
        self.assertEqual(result['bytes'], size)
        self.assertEqual(result['datagrams'], 1)
        self.assertEqual(self.nodes['A'].offer()['datagrams'], 0)

    def test_restart_keeps_recipient_dedup(self):
        body = packet(7, DATA, encode_message(replace(self.message(), hops=1)))
        self.nodes['B'].receive(body, ('127.0.0.1', 11))
        self.stores['B'].close()
        self.stores['B'] = Store(Path(self.tmp.name) / 'B', 'B', 65536, 256)
        self.nodes['B'] = LocalNode(self.stores['B'], FakeSocket())
        self.nodes['B'].begin(11, 7)
        self.nodes['B'].receive(body, ('127.0.0.1', 11))
        self.assertEqual(len(self.stores['B'].deliveries()), 1)
        self.assertEqual(self.nodes['B'].counts['duplicates'], 1)


if __name__ == '__main__':
    unittest.main()
