// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.core

import java.nio.ByteBuffer
import org.junit.Assert.*
import org.junit.Test

class TransferTest {
    @Test fun defaultAttValueHandlesMaximumDataInReorderedFragments() {
        val frame = ByteArray(587) { (it % 251).toByte() } // Outer type plus maximum DATA.
        val fragments = FragmentCodec.fragment(frame, -1, 20)
        assertEquals(74, fragments.size)
        assertTrue(fragments.all { it.size <= 20 })
        val receiver = Reassembler()
        var completed: CompletedFrame? = null
        for (fragment in fragments.reversed()) {
            receiver.accept("peer", fragment, 100)?.let { completed = it }
        }
        assertEquals(-1, completed!!.transferId)
        assertArrayEquals(frame, completed!!.bytes)
        assertEquals(0, receiver.activeTransfers)
        assertEquals(0, receiver.bufferedBytes)
    }

    @Test fun missingFragmentNeverProducesAnIncompletePacket() {
        val frame = ByteArray(100) { it.toByte() }
        val fragments = FragmentCodec.fragment(frame, 1)
        val receiver = Reassembler(timeoutMillis = 10)
        fragments.dropLast(1).forEach { assertNull(receiver.accept("peer", it, 1)) }
        assertEquals(1, receiver.activeTransfers)
        receiver.expire(11)
        assertEquals(0, receiver.activeTransfers)
        assertEquals(0, receiver.bufferedBytes)
        assertNull(receiver.accept("peer", fragments.last(), 11))
    }

    @Test fun wholeFrameRetryRecoversLossWithDuplicateFragments() {
        val frame = ByteArray(64) { (255 - it).toByte() }
        val fragments = FragmentCodec.fragment(frame, 1)
        val receiver = Reassembler()
        fragments.dropLast(1).forEach { assertNull(receiver.accept("peer", it, 1)) }
        var delivered = 0
        fragments.forEach { fragment ->
            receiver.accept("peer", fragment, 2)?.let {
                assertArrayEquals(frame, it.bytes)
                delivered++
            }
        }
        assertEquals(1, delivered)
    }

    @Test fun duplicateCannotRefreshExpiryOrChangeContent() {
        val fragments = FragmentCodec.fragment(ByteArray(20), 2)
        val receiver = Reassembler(timeoutMillis = 10)
        receiver.accept("peer", fragments[0], 0)
        receiver.accept("peer", fragments[0], 9)
        receiver.expire(10)
        assertEquals(0, receiver.activeTransfers)
        receiver.accept("peer", fragments[0], 10)
        val changed = fragments[0].copyOf().also { it[it.lastIndex] = 1 }
        assertThrows(IllegalArgumentException::class.java) { receiver.accept("peer", changed, 10) }
        assertEquals(0, receiver.activeTransfers)
    }

    @Test fun activeTransferAndPerPeerBoundsHold() {
        val receiver = Reassembler(maxTransfers = 2, maxPerPeer = 1)
        fun fragment(id: Int) = FragmentCodec.fragment(ByteArray(20), id)[0]
        receiver.accept("A", fragment(1), 0)
        assertThrows(IllegalArgumentException::class.java) { receiver.accept("A", fragment(2), 0) }
        receiver.accept("B", fragment(1), 0)
        assertThrows(IllegalArgumentException::class.java) { receiver.accept("C", fragment(1), 0) }
        assertEquals(2, receiver.activeTransfers)
        assertEquals(16, receiver.bufferedBytes)
        receiver.clearPeer("A")
        assertEquals(1, receiver.activeTransfers)
        receiver.clear()
        assertEquals(0, receiver.bufferedBytes)
    }

    @Test fun inconsistentCountAndOversizedAssemblyAreRejected() {
        val receiver = Reassembler()
        val fragment = FragmentCodec.fragment(ByteArray(20), 1)[0]
        receiver.accept("A", fragment, 0)
        val changed = fragment.copyOf().also { ByteBuffer.wrap(it).putShort(10, 2) }
        assertThrows(IllegalArgumentException::class.java) { receiver.accept("A", changed, 0) }
        assertEquals(0, receiver.activeTransfers)
        val large = FragmentCodec.fragment(ByteArray(1024), 3, 512)
        receiver.accept("A", large[0], 0)
        receiver.accept("A", large[1], 0)
        val overflow = large[1].copyOf().also { ByteBuffer.wrap(it).putShort(8, 2) }
        assertThrows(IllegalArgumentException::class.java) { receiver.accept("A", overflow, 0) }
        assertEquals(0, receiver.activeTransfers)
    }

    @Test fun fragmentParserRejectsMalformedAndImpossibleFrames() {
        val frame = FragmentCodec.fragment(byteArrayOf(1), 0)[0]
        val bad = listOf(frame.copyOf(12), frame.copyOf().also { it[0] = 0 },
            frame.copyOf().also { it[2] = 2 }, frame.copyOf().also { it[3] = 1 },
            frame.copyOf().also { it[11] = 0 }, frame.copyOf().also { it[9] = 1 },
            ByteArray(513))
        bad.forEach { assertThrows(IllegalArgumentException::class.java) { FragmentCodec.decode(it) } }
        assertThrows(IllegalArgumentException::class.java) { FragmentCodec.fragment(ByteArray(1025), 0) }
        assertThrows(IllegalArgumentException::class.java) { FragmentCodec.fragment(ByteArray(129), 0, 13) }
        assertThrows(IllegalArgumentException::class.java) { FragmentCodec.fragment(ByteArray(1), 0, 12) }
    }

    @Test fun retryBudgetStopsAfterTwoUnacknowledgedAttempts() {
        val transfer = BoundedTransfer(byteArrayOf(1), 4, ackTimeoutMillis = 10, lifetimeMillis = 30)
        assertEquals(1, transfer.start(100).size)
        assertTrue(transfer.poll(109).isEmpty())
        assertEquals(1, transfer.poll(110).size)
        assertEquals(2, transfer.attempts)
        assertTrue(transfer.poll(120).isEmpty())
        assertTrue(transfer.finished)
        assertFalse(transfer.succeeded)
        assertTrue(transfer.poll(1000).isEmpty())
        assertEquals(2, transfer.attempts)
    }

    @Test fun ackStopsRetryAndLifetimeRemainsBounded() {
        val acknowledged = BoundedTransfer(byteArrayOf(1), 1)
        acknowledged.start(0)
        acknowledged.acknowledge(1)
        assertTrue(acknowledged.poll(10000).isEmpty())
        assertTrue(acknowledged.succeeded)
        assertEquals(1, acknowledged.attempts)
        val timed = BoundedTransfer(byteArrayOf(1), 2, ackTimeoutMillis = 10, lifetimeMillis = 10)
        timed.start(0)
        assertTrue(timed.poll(10).isEmpty())
        assertTrue(timed.finished)
        assertEquals(1, timed.attempts)
        timed.acknowledge(10)
        assertFalse(timed.succeeded)
    }

    @Test fun lateAckCannotBypassDeadlineWhenPollWasDelayed() {
        val timed = BoundedTransfer(byteArrayOf(1), 2, ackTimeoutMillis = 10, lifetimeMillis = 10)
        timed.start(0)
        timed.acknowledge(20)
        assertTrue(timed.finished)
        assertFalse(timed.succeeded)
        val exhausted = BoundedTransfer(byteArrayOf(1), 3, maxAttempts = 1,
            ackTimeoutMillis = 10, lifetimeMillis = 30)
        exhausted.start(0)
        exhausted.acknowledge(10)
        assertTrue(exhausted.finished)
        assertFalse(exhausted.succeeded)
    }

    @Test fun clocksAndAttemptConfigurationAreValidated() {
        assertThrows(IllegalArgumentException::class.java) { BoundedTransfer(byteArrayOf(1), 0, maxAttempts = 4) }
        val transfer = BoundedTransfer(byteArrayOf(1), 0)
        transfer.start(10)
        assertThrows(IllegalArgumentException::class.java) { transfer.poll(9) }
        val receiver = Reassembler()
        receiver.expire(10)
        assertThrows(IllegalArgumentException::class.java) { receiver.expire(9) }
        transfer.reject()
        assertTrue(transfer.finished)
        assertFalse(transfer.succeeded)
    }
}
