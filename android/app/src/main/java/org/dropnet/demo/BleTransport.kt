// SPDX-License-Identifier: GPL-3.0-or-later
@file:Suppress("DEPRECATION", "MissingPermission")
package org.dropnet.demo

import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock
import org.dropnet.core.*
import java.nio.ByteBuffer
import java.security.SecureRandom
import java.util.ArrayDeque
import java.util.UUID

/** Foreground-only GATT experiment. Link success is not an end-to-end receipt. */
class BleTransport(private val context: Context, private val store: NodeStore,
                   private val update: (String) -> Unit) {
    companion object {
        val SERVICE: UUID = UUID.fromString("73ead001-d151-4c5e-a2d8-a4bfe374a300")
        private val RX = UUID.fromString("73ead002-d151-4c5e-a2d8-a4bfe374a300")
        private val TX = UUID.fromString("73ead003-d151-4c5e-a2d8-a4bfe374a300")
        private val CCC = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        private const val HELLO = 1
        private const val SUMMARY = 2
        private const val DATA = 3
        private const val ACK = 4
    }
    private val handler = Handler(Looper.getMainLooper())
    private val manager = context.getSystemService(BluetoothManager::class.java)
    private val adapter get() = manager.adapter
    private val random = SecureRandom()
    private val links = mutableMapOf<String, Link>()
    private val cooldown = mutableMapOf<String, Long>()
    private var server: BluetoothGattServer? = null
    private var tx: BluetoothGattCharacteristic? = null
    var running = false
        private set

    private inner class Link(val device: BluetoothDevice, val outgoing: Boolean) {
        val key = device.address + if (outgoing) "/client" else "/server"
        var gatt: BluetoothGatt? = null
        var rx: BluetoothGattCharacteristic? = null
        var ready = false
        var peerId: UUID? = null
        var seq = 0
        var busy = false
        var operation = 0
        val queue = ArrayDeque<ByteArray>()
        val earlyFragments = ArrayDeque<ByteArray>()
        val assembler = Reassembler()
        val summaries = mutableListOf<Summary>()
        var snapshot = emptyList<Message>()
        val candidates = ArrayDeque<Message>()
        var offered = false
        var pending: Message? = null
        var transfer: BoundedTransfer? = null
        var wireBytes = 0L
        var receivedBytes = 0L
        var closed = false

        fun activate() {
            if (ready || closed) return
            ready = true
            snapshot = store.queued()
            sendApp(byteArrayOf(HELLO.toByte()) + uuidBytes(store.nodeId))
            while (earlyFragments.isNotEmpty() && !closed) consume(earlyFragments.removeFirst())
            handler.postDelayed({ if (!closed && peerId == null) close("HELLO non ricevuto") }, 8000)
            handler.postDelayed({ if (!closed) close("Limite incontro: 90 secondi") }, 90000)
        }

        fun enqueue(fragments: List<ByteArray>) {
            if (closed) return
            if (queue.size + fragments.size > 512 || wireBytes + queue.sumOf { it.size } + fragments.sumOf { it.size } > 65536) {
                close("Quota di trasmissione raggiunta")
                return
            }
            fragments.forEach { queue.add(it) }
            pump()
        }

        fun sendApp(bytes: ByteArray) {
            enqueue(FragmentCodec.fragment(bytes, ++seq, 20))
        }

        fun pump() {
            if (closed || busy || !ready || queue.isEmpty()) return
            val value = queue.first
            busy = true
            val currentOperation = ++operation
            val started = runCatching { if (outgoing) {
                rx?.let { characteristic ->
                    characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                    characteristic.value = value
                    gatt?.writeCharacteristic(characteristic) == true
                } ?: false
            } else {
                tx?.let { characteristic ->
                    characteristic.value = value
                    server?.notifyCharacteristicChanged(device, characteristic, false) == true
                } ?: false
            } }.getOrDefault(false)
            if (!started) close("Invio GATT non avviato")
            else handler.postDelayed({ if (busy && !closed && operation == currentOperation) close("Timeout operazione GATT") }, 10000)
        }

        fun sent(status: Int) {
            if (closed || !busy) return
            if (status != BluetoothGatt.GATT_SUCCESS) { close("Errore GATT $status"); return }
            wireBytes += queue.removeFirst().size
            busy = false
            pump()
        }

        fun receive(fragment: ByteArray) {
            if (closed) return
            receivedBytes += fragment.size
            if (receivedBytes > 65536) { close("Quota di ricezione raggiunta"); return }
            if (!ready) {
                if (earlyFragments.size >= 64) close("Troppi frammenti prima della sottoscrizione")
                else earlyFragments.add(fragment.copyOf())
                return
            }
            consume(fragment)
        }

        fun consume(fragment: ByteArray) {
            try {
                assembler.accept(key, fragment, SystemClock.elapsedRealtime())?.let { handle(it.bytes) }
            } catch (_: IllegalArgumentException) { close("Frame non valido") }
            catch (_: android.database.SQLException) { close("Archivio locale non disponibile; nessuna presa in carico confermata") }
        }

        fun handle(frame: ByteArray) {
            require(frame.isNotEmpty())
            val kind = frame[0].toInt() and 255
            val body = frame.copyOfRange(1, frame.size)
            if (kind == HELLO) {
                require(body.size == 16 && peerId == null)
                val id = readUuid(body)
                require(id != store.nodeId)
                peerId = id
                update("Nodo vicino: ${id.toString().take(8)}; scambio attivo")
                CompactCodec.planFrames(store.inventory(), random.nextLong(), 128).forEach {
                    sendApp(byteArrayOf(SUMMARY.toByte()) + it)
                }
                // No summary is valid too: fallback must not wait indefinitely.
                handler.postDelayed({ if (!closed) beginOffers() }, 2000)
                return
            }
            require(peerId != null)
            when (kind) {
                SUMMARY -> {
                    if (summaries.size < 2) summaries.add(CompactCodec.decodeSummary(body))
                    // Allow the other bounded control copy to arrive before DATA.
                }
                DATA -> {
                    val message = DataCodec.decode(body)
                    require(message.hops > 0)
                    val result = try { store.accept(message) } catch (_: android.database.SQLException) { NodeStore.Acceptance.REJECTED }
                    sendApp(byteArrayOf(ACK.toByte()) + uuidBytes(message.id) + byteArrayOf(result.wire.toByte()))
                    update(when (result) {
                        NodeStore.Acceptance.DELIVERED -> "Messaggio ricevuto da questo destinatario"
                        NodeStore.Acceptance.RELAY -> "Messaggio salvato per inoltro"
                        NodeStore.Acceptance.DUPLICATE -> "Copia già presente: nessuna nuova consegna"
                        else -> "Messaggio rifiutato: memoria o scadenza"
                    })
                }
                ACK -> {
                    require(body.size == 17)
                    val id = readUuid(body.copyOfRange(0, 16))
                    val status = body[16].toInt() and 255
                    require(status in 0..3)
                    val message = pending ?: return
                    if (id != message.id) return
                    require(status != 2 || peerId == message.destination)
                    val currentTransfer = transfer ?: return
                    if (status == 0) currentTransfer.reject()
                    else currentTransfer.acknowledge(SystemClock.elapsedRealtime())
                    if (status != 0 && !currentTransfer.succeeded) {
                        update("Conferma tardiva fuori dal limite; copia conservata")
                        pending = null; transfer = null
                        nextMessage()
                        return
                    }
                    update(when (status) {
                        1 -> "Preso in carico dal relay; consegna finale sconosciuta"
                        2 -> "Ricezione confermata dal destinatario direttamente collegato"
                        3 -> "Il vicino conserva già questa copia"
                        else -> "Il vicino ha rifiutato la copia; conservata qui"
                    })
                    pending = null; transfer = null
                    nextMessage()
                }
                else -> throw IllegalArgumentException("Unknown packet")
            }
        }

        fun beginOffers() {
            if (offered || closed || peerId == null) return
            offered = true
            snapshot.filter { message -> !summaries.any { it.contains(message.id.toString()) } }
                .forEach { candidates.add(it) }
            if (summaries.isEmpty()) update("Riepilogo assente: inoltro normale, senza bloccare i dati")
            nextMessage()
        }

        fun nextMessage() {
            if (closed || pending != null) return
            while (candidates.isNotEmpty()) {
                val message = candidates.removeFirst()
                val forwarded = runCatching { message.forwarded(System.currentTimeMillis()) }.getOrNull() ?: continue
                pending = message
                val data = byteArrayOf(DATA.toByte()) + DataCodec.encode(forwarded)
                val pendingTransfer = BoundedTransfer(data, ++seq, 20, 2, 15000, 45000)
                transfer = pendingTransfer
                enqueue(pendingTransfer.start(SystemClock.elapsedRealtime()))
                pollTransfer(pendingTransfer)
                return
            }
            update("Incontro completato: copie locali conservate fino alla scadenza")
        }

        fun pollTransfer(expected: BoundedTransfer) {
            handler.postDelayed({
                if (closed || transfer !== expected) return@postDelayed
                if (!busy && queue.isEmpty()) enqueue(expected.poll(SystemClock.elapsedRealtime()))
                if (expected.finished) {
                    update("Nessuna conferma: copia conservata per un prossimo incontro")
                    transfer = null; pending = null
                    nextMessage()
                } else pollTransfer(expected)
            }, 500)
        }

        fun close(reason: String) {
            if (closed) return
            closed = true
            transfer?.reject(); assembler.clearPeer(key); queue.clear(); earlyFragments.clear()
            links.remove(key)
            cooldown[device.address] = SystemClock.elapsedRealtime() + 10000 + random.nextInt(3000)
            if (outgoing) {
                runCatching { gatt?.disconnect() }
                runCatching { gatt?.close() }
            } else runCatching { server?.cancelConnection(device) }
            update(reason)
        }
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) { handler.post { update("Visibile ai nodi DropNet; scansione attiva") } }
        override fun onStartFailure(errorCode: Int) { handler.post { update("Advertising non disponibile ($errorCode); sola scansione") } }
    }
    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            handler.post {
                if (!running || links.isNotEmpty() || (cooldown[result.device.address] ?: 0) > SystemClock.elapsedRealtime()) return@post
                val link = Link(result.device, true)
                links[link.key] = link
                link.gatt = result.device.connectGatt(context, false, clientCallback, BluetoothDevice.TRANSPORT_LE)
                handler.postDelayed({ if (!link.ready && !link.closed) link.close("Timeout connessione") }, 15000)
            }
        }
        override fun onScanFailed(errorCode: Int) { handler.post { update("Scansione non disponibile ($errorCode)") } }
    }

    private fun client(gatt: BluetoothGatt) = links[gatt.device.address + "/client"]?.takeIf { it.gatt === gatt }
    private val clientCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) { handler.post {
            val link = client(gatt) ?: return@post
            if (status != BluetoothGatt.GATT_SUCCESS || newState == BluetoothProfile.STATE_DISCONNECTED) link.close("Connessione terminata; dati conservati")
            else if (newState == BluetoothProfile.STATE_CONNECTED && !gatt.discoverServices()) link.close("Ricerca servizio fallita")
        } }
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) { handler.post {
            val link = client(gatt) ?: return@post
            val service = gatt.getService(SERVICE)
            link.rx = service?.getCharacteristic(RX)
            val notify = service?.getCharacteristic(TX)
            val descriptor = notify?.getDescriptor(CCC)
            if (status != BluetoothGatt.GATT_SUCCESS || link.rx == null || notify == null || descriptor == null) {
                link.close("Servizio DropNet incompatibile"); return@post
            }
            if (!gatt.setCharacteristicNotification(notify, true)) { link.close("Notifiche non attivabili"); return@post }
            descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            if (!gatt.writeDescriptor(descriptor)) link.close("Sottoscrizione non avviata")
        } }
        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) { handler.post {
            client(gatt)?.let { if (status == BluetoothGatt.GATT_SUCCESS) it.activate() else it.close("Sottoscrizione fallita") }
        } }
        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) { handler.post { client(gatt)?.sent(status) } }
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            val value = characteristic.value?.copyOf() ?: return
            handler.post { if (characteristic.uuid == TX) client(gatt)?.receive(value) }
        }
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray) {
            val copy = value.copyOf(); handler.post { if (characteristic.uuid == TX) client(gatt)?.receive(copy) }
        }
    }

    private val serverCallback = object : BluetoothGattServerCallback() {
        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) { handler.post {
            val key = device.address + "/server"
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                if (!running || links.isNotEmpty()) server?.cancelConnection(device)
                else {
                    val link = Link(device, false); links[key] = link
                    handler.postDelayed({ if (!link.ready && !link.closed) link.close("Timeout sottoscrizione") }, 15000)
                }
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) links[key]?.close("Vicino disconnesso; dati conservati")
        } }
        override fun onServiceAdded(status: Int, service: BluetoothGattService) { handler.post {
            if (!running) return@post
            if (status != BluetoothGatt.GATT_SUCCESS) { update("Servizio GATT non disponibile"); return@post }
            val advertiser = adapter.bluetoothLeAdvertiser
            if (advertiser == null) { update("Advertising non supportato: questo nodo può solo cercare vicini"); return@post }
            advertiser.startAdvertising(
                AdvertiseSettings.Builder().setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
                    .setConnectable(true).setTimeout(0).build(),
                AdvertiseData.Builder().addServiceUuid(ParcelUuid(SERVICE)).setIncludeDeviceName(false).build(), advertiseCallback)
        } }
        override fun onDescriptorWriteRequest(device: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray) { handler.post {
            val valid = descriptor.uuid == CCC && !preparedWrite && offset == 0 && value.contentEquals(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            if (responseNeeded) server?.sendResponse(device, requestId, if (valid) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
            if (valid) links[device.address + "/server"]?.activate()
        } }
        override fun onCharacteristicWriteRequest(device: BluetoothDevice, requestId: Int, characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray) { handler.post {
            val valid = characteristic.uuid == RX && !preparedWrite && offset == 0 && value.size in 13..20
            if (valid) links[device.address + "/server"]?.receive(value.copyOf())
            if (responseNeeded) server?.sendResponse(device, requestId, if (valid) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_INVALID_ATTRIBUTE_LENGTH, 0, null)
        } }
        override fun onNotificationSent(device: BluetoothDevice, status: Int) { handler.post { links[device.address + "/server"]?.sent(status) } }
    }

    fun start() {
        if (running) return
        if (adapter == null || !adapter.isEnabled) { update("Attiva Bluetooth nelle impostazioni del dispositivo"); return }
        running = true
        try {
            server = manager.openGattServer(context, serverCallback)
            val service = BluetoothGattService(SERVICE, BluetoothGattService.SERVICE_TYPE_PRIMARY)
            service.addCharacteristic(BluetoothGattCharacteristic(RX, BluetoothGattCharacteristic.PROPERTY_WRITE, BluetoothGattCharacteristic.PERMISSION_WRITE))
            tx = BluetoothGattCharacteristic(TX, BluetoothGattCharacteristic.PROPERTY_NOTIFY, BluetoothGattCharacteristic.PERMISSION_READ).apply {
                addDescriptor(BluetoothGattDescriptor(CCC, BluetoothGattDescriptor.PERMISSION_WRITE or BluetoothGattDescriptor.PERMISSION_READ))
            }
            service.addCharacteristic(tx)
            if (server?.addService(service) != true) update("GATT server non disponibile; tentativo di sola scansione")
            adapter.bluetoothLeScanner?.startScan(listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(SERVICE)).build()),
                ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_BALANCED).build(), scanCallback)
            update("Partecipazione attiva solo con questa schermata aperta")
        } catch (failure: RuntimeException) { stop(); update("Bluetooth non disponibile: ${failure.javaClass.simpleName}") }
    }

    fun stop() {
        running = false
        links.values.toList().forEach { it.close("Partecipazione sospesa") }
        runCatching { adapter?.bluetoothLeScanner?.stopScan(scanCallback) }
        runCatching { adapter?.bluetoothLeAdvertiser?.stopAdvertising(advertiseCallback) }
        runCatching { server?.close() }
        server = null; tx = null
        update("In pausa; messaggi conservati localmente")
    }

    private fun uuidBytes(id: UUID) = ByteBuffer.allocate(16).putLong(id.mostSignificantBits).putLong(id.leastSignificantBits).array()
    private fun readUuid(bytes: ByteArray): UUID { val buffer = ByteBuffer.wrap(bytes); return UUID(buffer.long, buffer.long) }
}
