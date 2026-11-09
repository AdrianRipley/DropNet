// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.demo

import android.content.Context
import org.dropnet.core.CompactCodec
import org.dropnet.core.DataCodec
import org.dropnet.core.FragmentCodec
import org.dropnet.core.Message
import org.dropnet.core.Reassembler
import java.util.UUID

/** Native SQLite + real codec test. No Bluetooth packets or radio claims. */
object RelaySelfTest {
    fun run(context: Context): String {
        val prefix = "selftest-${UUID.randomUUID()}"
        val ids = List(3) { UUID.randomUUID() }
        val names = List(3) { "$prefix-$it.sqlite" }
        val stores = names.mapIndexed { index, name -> NodeStore(context, name, ids[index]) }.toMutableList()
        try {
            val now = System.currentTimeMillis()
            val message = Message(UUID.randomUUID(), ids[0], ids[2], now - 1000, now + 60000, "DropNet synthetic relay")
            check(stores[0].accept(message, now) == NodeStore.Acceptance.RELAY)
            val sent = message.forwarded(now)
            val fragments = FragmentCodec.fragment(DataCodec.encode(sent), 1, 20)
            val assembler = Reassembler()
            var complete: ByteArray? = null
            for (fragment in fragments.reversed()) {
                assembler.accept("A", fragment, 0)?.let { complete = it.bytes }
            }
            check(complete != null)
            check(stores[1].accept(DataCodec.decodeForReceive(complete!!, now), now) == NodeStore.Acceptance.RELAY)
            stores[0].close() // A is unavailable for the second contact.
            stores[1].close()
            stores[1] = NodeStore(context, names[1], ids[1])
            val relayed = stores[1].queued(now).single().forwarded(now)
            check(stores[2].accept(DataCodec.decodeForReceive(DataCodec.encode(relayed), now), now) == NodeStore.Acceptance.DELIVERED)
            check(stores[2].inbox().single().id == message.id)
            val summaries = CompactCodec.planFrames(stores[2].inventory(now), 7L, 128)
            check(summaries.isNotEmpty())
            check(CompactCodec.decodeSummary(summaries.first()).contains(message.id.toString()))
            stores[2].close()
            stores[2] = NodeStore(context, names[2], ids[2])
            check(stores[2].accept(relayed, now) == NodeStore.Acceptance.DUPLICATE)
            check(stores[2].inbox().size == 1)
            check(runCatching { DataCodec.decode(byteArrayOf(1, 2)) }.isFailure)
            return "PASS: A → B; A offline; SQLite di B riaperto; B → C; una consegna; deduplica dopo riavvio; riepilogo e frammenti verificati. Test sintetico, senza radio Bluetooth."
        } finally {
            stores.forEach { runCatching { it.close() } }
            // Only this run's explicitly disposable synthetic databases.
            names.forEach { context.deleteDatabase(it) }
        }
    }
}
