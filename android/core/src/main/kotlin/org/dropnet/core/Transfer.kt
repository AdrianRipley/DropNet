// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.core

import java.nio.ByteBuffer

data class Fragment(val transferId: Int, val index: Int, val count: Int, val payload: ByteArray)
data class CompletedFrame(val transferId: Int, val bytes: ByteArray)

/** ATT-value framing, independent of an Android Bluetooth implementation. */
object FragmentCodec {
    const val HEADER_BYTES = 12
    const val MAX_FRAME_BYTES = 1024
    const val MAX_FRAGMENTS = 128
    const val MAX_VALUE_BYTES = 512
    private const val MAGIC = 0x4446 // DF

    fun fragment(frame: ByteArray, transferId: Int, maxValueBytes: Int = 20): List<ByteArray> {
        require(frame.size in 1..MAX_FRAME_BYTES) { "Frame exceeds transport limit" }
        require(maxValueBytes in HEADER_BYTES + 1..MAX_VALUE_BYTES) { "Invalid ATT value size" }
        val chunkSize = maxValueBytes - HEADER_BYTES
        val count = (frame.size + chunkSize - 1) / chunkSize
        require(count <= MAX_FRAGMENTS) { "Too many fragments for negotiated value size" }
        return List(count) { index ->
            val first = index * chunkSize
            val last = minOf(frame.size, first + chunkSize)
            ByteBuffer.allocate(HEADER_BYTES + last - first).apply {
                putShort(MAGIC.toShort())
                put(1.toByte())
                put(0.toByte())
                putInt(transferId)
                putShort(index.toShort())
                putShort(count.toShort())
                put(frame, first, last - first)
            }.array()
        }
    }

    fun decode(value: ByteArray): Fragment {
        require(value.size in HEADER_BYTES + 1..MAX_VALUE_BYTES) { "Invalid fragment length" }
        val buffer = ByteBuffer.wrap(value)
        require((buffer.short.toInt() and 65535) == MAGIC && buffer.get().toInt() == 1 && buffer.get().toInt() == 0) {
            "Unsupported fragment header"
        }
        val transferId = buffer.int
        val index = buffer.short.toInt() and 65535
        val count = buffer.short.toInt() and 65535
        require(count in 1..MAX_FRAGMENTS && index < count) { "Invalid fragment index/count" }
        return Fragment(transferId, index, count, ByteArray(buffer.remaining()).also { buffer.get(it) })
    }
}

/** Bounded incomplete frames. Use a monotonic clock, not UTC timestamps. */
class Reassembler(
    private val maxTransfers: Int = 8,
    private val maxPerPeer: Int = 2,
    private val timeoutMillis: Long = 10000,
) {
    private data class Key(val peer: String, val id: Int)
    private data class Assembly(val started: Long, val parts: Array<ByteArray?>, var size: Int = 0)
    private val assemblies = LinkedHashMap<Key, Assembly>()
    private var lastNow = 0L

    init {
        require(maxTransfers in 1..32 && maxPerPeer in 1..maxTransfers)
        require(timeoutMillis in 1..60000)
    }

    val activeTransfers: Int get() = assemblies.size
    val bufferedBytes: Int get() = assemblies.values.sumOf { it.size }

    fun expire(nowMillis: Long) {
        require(nowMillis >= lastNow) { "Reassembly clock moved backwards" }
        lastNow = nowMillis
        assemblies.entries.removeAll { nowMillis - it.value.started >= timeoutMillis }
    }

    fun clearPeer(peer: String) {
        assemblies.keys.removeAll { it.peer == peer }
    }

    fun clear() = assemblies.clear()

    fun accept(peer: String, fragment: ByteArray, nowMillis: Long): CompletedFrame? {
        require(peer.isNotEmpty() && peer.length <= 128) { "Invalid transport peer" }
        expire(nowMillis)
        val parsed = FragmentCodec.decode(fragment)
        val key = Key(peer, parsed.transferId)
        var assembly = assemblies[key]
        if (assembly == null) {
            require(assemblies.size < maxTransfers) { "Reassembly capacity reached" }
            require(assemblies.keys.count { it.peer == peer } < maxPerPeer) { "Peer reassembly capacity reached" }
            assembly = Assembly(nowMillis, arrayOfNulls(parsed.count))
            assemblies[key] = assembly
        }
        if (assembly.parts.size != parsed.count) {
            assemblies.remove(key)
            throw IllegalArgumentException("Conflicting fragment count")
        }
        val existing = assembly.parts[parsed.index]
        if (existing != null) {
            if (!existing.contentEquals(parsed.payload)) {
                assemblies.remove(key)
                throw IllegalArgumentException("Conflicting duplicate fragment")
            }
            return null // Duplicate does not extend the fixed expiry.
        }
        if (assembly.size + parsed.payload.size > FragmentCodec.MAX_FRAME_BYTES) {
            assemblies.remove(key)
            throw IllegalArgumentException("Reassembled frame too large")
        }
        assembly.parts[parsed.index] = parsed.payload
        assembly.size += parsed.payload.size
        if (assembly.parts.any { it == null }) return null
        val completed = ByteArray(assembly.size)
        var offset = 0
        assembly.parts.forEach { part ->
            part!!.copyInto(completed, offset)
            offset += part.size
        }
        assemblies.remove(key)
        return CompletedFrame(parsed.transferId, completed)
    }
}

/**
 * Bounded whole-frame retry after missing application ACK. The caller must pace
 * fragments via GATT callbacks and match the ACK's message UUID before acknowledge.
 * Link write/notification success alone is not a persistence or delivery ACK.
 */
class BoundedTransfer(
    frame: ByteArray,
    val transferId: Int,
    maxValueBytes: Int = 20,
    private val maxAttempts: Int = 2,
    private val ackTimeoutMillis: Long = 3000,
    private val lifetimeMillis: Long = 15000,
) {
    private val fragments = FragmentCodec.fragment(frame.copyOf(), transferId, maxValueBytes)
    private var started: Long? = null
    private var lastAttempt = 0L
    private var lastNow = 0L
    var attempts = 0
        private set
    var finished = false
        private set
    var succeeded = false
        private set

    init {
        require(maxAttempts in 1..3)
        require(ackTimeoutMillis in 1..30000)
        require(lifetimeMillis in ackTimeoutMillis..60000)
    }

    fun start(nowMillis: Long): List<ByteArray> {
        require(started == null && !finished) { "Transfer already started" }
        require(nowMillis >= 0) { "Negative monotonic time" }
        started = nowMillis
        lastNow = nowMillis
        return attempt(nowMillis)
    }

    private fun attempt(nowMillis: Long): List<ByteArray> {
        lastAttempt = nowMillis
        attempts++
        return fragments.map { it.copyOf() }
    }

    fun poll(nowMillis: Long): List<ByteArray> {
        val first = checkNotNull(started) { "Transfer has not started" }
        require(nowMillis >= lastNow) { "Transfer clock moved backwards" }
        lastNow = nowMillis
        if (finished) return emptyList()
        if (nowMillis - first >= lifetimeMillis) {
            finished = true
            return emptyList()
        }
        if (nowMillis - lastAttempt < ackTimeoutMillis) return emptyList()
        if (attempts >= maxAttempts) {
            finished = true
            return emptyList()
        }
        return attempt(nowMillis)
    }

    /** Caller validates and matches an application ACK to this message first. */
    fun acknowledge(nowMillis: Long) {
        val first = started ?: return
        require(nowMillis >= lastNow) { "Transfer clock moved backwards" }
        lastNow = nowMillis
        if (nowMillis - first >= lifetimeMillis ||
            (attempts >= maxAttempts && nowMillis - lastAttempt >= ackTimeoutMillis)) {
            finished = true
        }
        if (!finished) {
            succeeded = true
            finished = true
        }
    }

    fun reject() {
        finished = true
    }
}
