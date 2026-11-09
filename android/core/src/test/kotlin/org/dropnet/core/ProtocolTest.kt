// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.core

import java.nio.ByteBuffer
import java.util.UUID
import org.junit.Assert.*
import org.junit.Test

class ProtocolTest {
    private fun unhex(value: String) = value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun hex(value: ByteArray) = value.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun rawLong(value: String) = java.lang.Long.parseUnsignedLong(value, 16)
    private fun resource(name: String) = javaClass.getResourceAsStream("/$name")!!.bufferedReader().use { it.readText() }
    private fun message() = Message(
        UUID.fromString("00112233-4455-6677-8899-aabbccddeeff"),
        UUID.fromString("11111111-2222-3333-4444-555555555555"),
        UUID.fromString("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        1000, 2000, "é\u0000🙂", 1, 8,
    )

    @Test fun pythonCompactVectorsAreByteIdentical() {
        val rows = resource("compact_vectors.tsv").lineSequence()
            .filter { it.isNotEmpty() && !it.startsWith("#") }.toList()
        assertEquals(4, rows.size)
        for (line in rows) {
            val columns = line.split('|')
            val salt = rawLong(columns[1])
            val ids = if (columns[2].isEmpty()) emptyList() else columns[2].split(',').map { utf8(unhex(it)) }
            val expected = if (columns[3].isEmpty()) emptyList() else columns[3].split(',').map { rawLong(it) }
            val tokens = ids.map { CompactCodec.fingerprint(it, salt) }
            assertEquals(columns[0], expected, tokens)
            assertEquals(columns[0], columns[4], hex(CompactCodec.encodeSummary(salt, tokens)))
            val summary = CompactCodec.decodeSummary(unhex(columns[4]))
            assertEquals(salt, summary.salt)
            assertEquals(expected.toSet(), summary.tokens.toSet())
            ids.forEach { assertTrue(summary.contains(it)) }
        }
        // Keep the source fixture shipped with the language-neutral extraction.
        assertTrue(resource("compact_vectors.json").contains("3270a4b90ce6fe4c"))
    }

    @Test fun summaryPlanIsBoundedAndDeterministic() {
        assertTrue(CompactCodec.planFrames(emptyList(), 0, 128).isEmpty())
        assertTrue(CompactCodec.planFrames(listOf("m"), 0, 23).isEmpty())
        assertEquals(listOf(24), CompactCodec.planFrames(listOf("m"), 0, 47).map { it.size })
        val repeated = CompactCodec.planFrames(listOf("m"), 0, 48)
        assertEquals(2, repeated.size)
        assertArrayEquals(repeated[0], repeated[1])
        val ids = (0 until 40).map { "m$it" }
        val partial = CompactCodec.planFrames(ids, 1, 128)
        assertEquals(listOf(128), partial.map { it.size })
        assertEquals(14, CompactCodec.decodeSummary(partial[0]).tokens.size)
        assertArrayEquals(partial[0], CompactCodec.planFrames(ids.reversed(), 1, 128)[0])
        assertFalse(partial[0].contentEquals(CompactCodec.planFrames(ids, 2, 128)[0]))
    }

    @Test fun summaryRejectsMalformedAndNoncanonicalFrames() {
        val valid = CompactCodec.encodeSummary(1, listOf(1, 2))
        val invalid = listOf(
            byteArrayOf(), valid.copyOf(15), valid.copyOf(valid.size - 1), valid + byteArrayOf(0),
            valid.copyOf().also { it[0] = 0 }, valid.copyOf().also { it[4] = 2 },
            valid.copyOf().also { it[5] = 2 }, valid.copyOf().also { it[7] = 3 },
            valid.copyOf().also { ByteBuffer.wrap(it).putLong(24, 1) },
            valid.copyOf().also { ByteBuffer.wrap(it).putLong(16, 3) },
        )
        invalid.forEach { assertThrows(IllegalArgumentException::class.java) { CompactCodec.decodeSummary(it) } }
        assertThrows(IllegalArgumentException::class.java) { CompactCodec.fingerprint("", 0) }
        assertThrows(IllegalArgumentException::class.java) { CompactCodec.fingerprint("é".repeat(65), 0) }
        assertThrows(IllegalArgumentException::class.java) { CompactCodec.fingerprint("\uD800", 0) }
    }

    @Test fun dataMatchesIndependentBinaryVector() {
        val expected = resource("data_vector.hex").trim()
        val encoded = DataCodec.encode(message())
        assertEquals(expected, hex(encoded))
        assertEquals(message(), DataCodec.decode(unhex(expected)))
        assertEquals(74 + 7, encoded.size)
    }

    @Test fun textLimitIsUtf8NotUtf16AndNulHasNoExpansion() {
        val base = message()
        for (text in listOf("é".repeat(256), "\u0000".repeat(512))) {
            val candidate = base.copy(text = text)
            assertEquals(586, DataCodec.encode(candidate).size)
            assertEquals(candidate, DataCodec.decode(DataCodec.encode(candidate)))
        }
        assertThrows(IllegalArgumentException::class.java) { base.copy(text = "é".repeat(257)) }
        assertThrows(IllegalArgumentException::class.java) { base.copy(text = "\uD800") }
    }

    @Test fun receiverRejectsExpiryFutureCreationAndZeroNetworkHops() {
        val frame = DataCodec.encode(message())
        assertEquals(message(), DataCodec.decodeForReceive(frame, 1999))
        assertThrows(IllegalArgumentException::class.java) { DataCodec.decodeForReceive(frame, 2000) }
        assertThrows(IllegalArgumentException::class.java) { DataCodec.decodeForReceive(frame, 999) }
        assertThrows(IllegalArgumentException::class.java) {
            DataCodec.decodeForReceive(DataCodec.encode(message().copy(hops = 0)), 1500)
        }
    }

    @Test fun hopsCountDestinationTraversalAndAreBounded() {
        val last = message().copy(hops = 0, hopLimit = 1).forwarded(1500)
        assertEquals(1, last.hops)
        assertThrows(IllegalArgumentException::class.java) { last.forwarded(1500) }
        assertThrows(IllegalArgumentException::class.java) { message().copy(hopLimit = 256) }
        assertThrows(IllegalArgumentException::class.java) { message().forwarded(2000) }
        assertThrows(IllegalArgumentException::class.java) { message().copy(expires = 1000) }
    }

    @Test fun dataRejectsMalformedLengthsFlagsUtf8AndTimes() {
        val valid = DataCodec.encode(message())
        val invalid = listOf(
            valid.copyOf(73), valid + byteArrayOf(0), valid.copyOf(valid.size - 1),
            valid.copyOf().also { it[4] = 2 }, valid.copyOf().also { it[5] = 1 },
            valid.copyOf().also { it[73] = 0 },
            valid.copyOf().also { it[74] = 0xFF.toByte() },
            valid.copyOf().also { ByteBuffer.wrap(it).putLong(54, -1) },
            valid.copyOf().also { it[70] = 9 },
        )
        invalid.forEach { assertThrows(IllegalArgumentException::class.java) { DataCodec.decode(it) } }
    }
}
