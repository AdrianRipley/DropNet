// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.core

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.CharBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.CharacterCodingException
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.UUID

internal fun utf8(text: String): ByteArray {
    try {
        val encoded = StandardCharsets.UTF_8.newEncoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .encode(CharBuffer.wrap(text))
        return ByteArray(encoded.remaining()).also { encoded.get(it) }
    } catch (error: CharacterCodingException) {
        throw IllegalArgumentException("Malformed UTF-8 text", error)
    }
}

internal fun utf8(bytes: ByteArray): String = try {
    StandardCharsets.UTF_8.newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT)
        .decode(ByteBuffer.wrap(bytes)).toString()
} catch (error: CharacterCodingException) {
    throw IllegalArgumentException("Malformed UTF-8 bytes", error)
}

/** Synthetic plaintext message. Timestamps are UTC epoch milliseconds. */
data class Message(
    val id: UUID,
    val source: UUID,
    val destination: UUID,
    val created: Long,
    val expires: Long,
    val text: String,
    val hops: Int = 0,
    val hopLimit: Int = 8,
) {
    init {
        require(created >= 0 && expires > created) { "Require 0 <= creation < expiry" }
        require(hopLimit in 0..255 && hops in 0..hopLimit) { "Invalid hop bounds" }
        require(utf8(text).size <= 512) { "Text exceeds 512 UTF-8 bytes" }
    }

    fun isLive(now: Long): Boolean = now >= created && now < expires

    /** Increment only when preparing a network copy, including destination hop. */
    fun forwarded(now: Long): Message {
        require(isLive(now)) { "Message is not live" }
        require(hops < hopLimit) { "Hop limit reached" }
        return copy(hops = hops + 1)
    }
}

/** Actual DATA bytes. This does not use the simulator's assumed metadata size. */
object DataCodec {
    const val HEADER_BYTES = 74
    const val MAX_BYTES = HEADER_BYTES + 512
    private const val MAGIC = 0x444E4454 // DNDT

    private fun ByteBuffer.putUuid(uuid: UUID) {
        putLong(uuid.mostSignificantBits)
        putLong(uuid.leastSignificantBits)
    }

    private fun ByteBuffer.uuid() = UUID(long, long)

    fun encode(message: Message): ByteArray {
        val text = utf8(message.text)
        return ByteBuffer.allocate(HEADER_BYTES + text.size).order(ByteOrder.BIG_ENDIAN).apply {
            putInt(MAGIC)
            put(1.toByte())
            put(0.toByte()) // Reserved flags; require zero until versioned.
            putUuid(message.id)
            putUuid(message.source)
            putUuid(message.destination)
            putLong(message.created)
            putLong(message.expires)
            put(message.hops.toByte())
            put(message.hopLimit.toByte())
            putShort(text.size.toShort())
            put(text)
        }.array()
    }

    /** Structural validation; use decodeForReceive for network admission. */
    fun decode(frame: ByteArray): Message {
        require(frame.size in HEADER_BYTES..MAX_BYTES) { "Invalid DATA length" }
        val buffer = ByteBuffer.wrap(frame).order(ByteOrder.BIG_ENDIAN)
        require(buffer.int == MAGIC && buffer.get().toInt() == 1 && buffer.get().toInt() == 0) {
            "Unsupported DATA header"
        }
        val id = buffer.uuid()
        val source = buffer.uuid()
        val destination = buffer.uuid()
        val created = buffer.long
        val expires = buffer.long
        val hops = buffer.get().toInt() and 255
        val limit = buffer.get().toInt() and 255
        val size = buffer.short.toInt() and 65535
        require(size <= 512 && size == buffer.remaining()) { "DATA length mismatch" }
        val body = ByteArray(size).also { buffer.get(it) }
        return Message(id, source, destination, created, expires, utf8(body), hops, limit)
    }

    fun decodeForReceive(frame: ByteArray, now: Long): Message = decode(frame).also {
        require(it.hops > 0) { "Network copy must have traversed a hop" }
        require(it.isLive(now)) { "Message is expired or created in the future" }
    }
}

/** Salt and tokens use raw 64-bit values; negative Long values are valid bits. */
data class Summary(val salt: Long, val tokens: List<Long>) {
    fun contains(id: String): Boolean = CompactCodec.fingerprint(id, salt) in tokens
}

/** Byte-compatible with the v0.3 Python DNCS format, including unsigned order. */
object CompactCodec {
    const val HEADER_BYTES = 16
    const val TOKEN_BYTES = 8
    const val MAX_TOKENS = 65535
    const val MAX_BYTES = HEADER_BYTES + TOKEN_BYTES * MAX_TOKENS
    private const val MAGIC = 0x444E4353 // DNCS
    private val unsignedOrder = Comparator<Long> { a, b -> java.lang.Long.compareUnsigned(a, b) }

    fun fingerprint(id: String, salt: Long): Long {
        val encoded = utf8(id)
        require(encoded.size in 1..128) { "ID requires 1..128 UTF-8 bytes" }
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(ByteBuffer.allocate(8).putLong(salt).array())
        return ByteBuffer.wrap(digest.digest(encoded), 0, 8).long
    }

    fun encodeSummary(salt: Long, tokens: Collection<Long>): ByteArray {
        val sorted = tokens.distinct().sortedWith(unsignedOrder)
        require(sorted.size <= MAX_TOKENS) { "Too many summary tokens" }
        return ByteBuffer.allocate(HEADER_BYTES + TOKEN_BYTES * sorted.size).apply {
            putInt(MAGIC)
            put(1.toByte())
            put(1.toByte())
            putShort(sorted.size.toShort())
            putLong(salt)
            sorted.forEach { putLong(it) }
        }.array()
    }

    fun decodeSummary(frame: ByteArray): Summary {
        require(frame.size in HEADER_BYTES..MAX_BYTES) { "Invalid summary length" }
        val buffer = ByteBuffer.wrap(frame)
        require(buffer.int == MAGIC && buffer.get().toInt() == 1 && buffer.get().toInt() == 1) {
            "Unsupported summary header"
        }
        val count = buffer.short.toInt() and 65535
        val salt = buffer.long
        require(frame.size == HEADER_BYTES + TOKEN_BYTES * count) { "Summary length mismatch" }
        val tokens = List(count) { buffer.long }
        require(tokens.zipWithNext().all { (a, b) -> unsignedOrder.compare(a, b) < 0 }) {
            "Summary tokens must be unique and increasing"
        }
        return Summary(salt, tokens)
    }

    /** Fixed redundancy; never asks whether the receiver saw a previous copy. */
    fun planFrames(ids: Collection<String>, salt: Long, quota: Int): List<ByteArray> {
        require(quota >= 0) { "Negative summary quota" }
        val tokens = ids.map { fingerprint(it, salt) }.distinct().sortedWith(unsignedOrder)
        val fit = ((quota - HEADER_BYTES).coerceAtLeast(0) / TOKEN_BYTES).coerceAtMost(MAX_TOKENS)
        if (tokens.isEmpty() || fit == 0) return emptyList()
        val packet = encodeSummary(salt, tokens.take(fit))
        return if (tokens.size <= fit && packet.size <= quota / 2) {
            listOf(packet, packet.copyOf())
        } else listOf(packet)
    }
}
