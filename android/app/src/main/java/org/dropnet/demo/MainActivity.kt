// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.demo

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import org.dropnet.core.Message
import java.util.UUID

class MainActivity : Activity() {
    private lateinit var store: NodeStore
    private lateinit var transport: BleTransport
    private lateinit var state: TextView
    private lateinit var contents: TextView
    private lateinit var journal: TextView
    private lateinit var participation: Button
    private var foreground = false
    private var closing = false
    private val events = ArrayDeque<String>()
    private val teal = Color.rgb(0, 92, 91)
    private val ink = Color.rgb(30, 47, 49)
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val preferences = getSharedPreferences("node", MODE_PRIVATE)
        val identity = preferences.getString("id", null)?.let(UUID::fromString) ?: UUID.randomUUID().also {
            preferences.edit().putString("id", it.toString()).commit()
        }
        store = NodeStore(this, "dropnet.sqlite", identity)
        val scroll = ScrollView(this).apply { setBackgroundColor(Color.rgb(244, 248, 246)); isFillViewport = true }
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(42), dp(22), dp(30))
        }
        scroll.addView(column)
        setContentView(scroll)
        fun label(text: String, size: Float = 16f, bold: Boolean = false): TextView = TextView(this).apply {
            this.text = text; textSize = size; setTextColor(ink)
            if (bold) setTypeface(typeface, Typeface.BOLD)
            setPadding(0, dp(8), 0, dp(8))
            column.addView(this)
        }
        fun button(text: String, action: () -> Unit): Button = Button(this).apply {
            this.text = text; isAllCaps = false; setTextColor(teal)
            column.addView(this, LinearLayout.LayoutParams(-1, dp(52)).apply { topMargin = dp(6) })
            setOnClickListener { action() }
        }
        label("DROPNET / DEMO 0.4", 13f, true).setTextColor(teal)
        label("Un messaggio,\nun passaggio alla volta.", 28f, true)
        label("Prototipo in chiaro: usa solo testi di prova. Funziona tra nodi compatibili; non è un servizio di emergenza.", 14f)
        state = label("In pausa", 17f, true)
        state.setTextColor(teal)
        transport = BleTransport(this, store) { message ->
            runOnUiThread { if (!closing && !isDestroyed) show(message) }
        }
        participation = button("Avvia partecipazione Bluetooth") { toggleParticipation() }
        label("La partecipazione si ferma uscendo da questa schermata. I messaggi restano salvati.", 13f)
        label("Il tuo identificativo", 18f, true)
        label(identity.toString(), 13f).apply { typeface = Typeface.MONOSPACE; setTextIsSelectable(true) }
        button("Copia identificativo") {
            getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("DropNet ID", identity.toString()))
            show("Identificativo copiato: condividilo con chi deve scriverti")
        }
        label("Messaggio di prova", 18f, true)
        val destination = EditText(this).apply {
            hint = "Identificativo UUID del destinatario"
            textSize = 14f; setSingleLine(true); inputType = android.text.InputType.TYPE_CLASS_TEXT
            column.addView(this, LinearLayout.LayoutParams(-1, -2))
        }
        val messageText = EditText(this).apply {
            hint = "Testo (massimo 512 byte UTF-8)"
            minLines = 2; maxLines = 5
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
            gravity = Gravity.TOP
            column.addView(this, LinearLayout.LayoutParams(-1, -2))
        }
        button("Salva per l'inoltro") {
            try {
                val target = UUID.fromString(destination.text.toString().trim())
                val now = System.currentTimeMillis()
                val message = Message(UUID.randomUUID(), identity, target, now, now + 86400000,
                    messageText.text.toString(), 0, 8)
                when (store.accept(message, now)) {
                    NodeStore.Acceptance.RELAY -> { messageText.text.clear(); show("Salvato: scade tra 24 ore, massimo 8 passaggi") }
                    NodeStore.Acceptance.DELIVERED -> { messageText.text.clear(); show("Destinatario locale: ricevuto su questo nodo") }
                    else -> show("Non salvato: quota locale esaurita")
                }
            } catch (_: IllegalArgumentException) { show("Controlla l'UUID e il limite di 512 byte UTF-8") }
            catch (_: android.database.SQLException) { show("Salvataggio non riuscito; nessuna conferma di presa in carico") }
        }
        label("Test sul dispositivo", 18f, true)
        label("Verifica tre nodi sintetici, riapertura del relay, codec e deduplicazione. Non accende la radio.", 14f)
        button("Esegui test A → B → C") {
            show("Test locale in corso…")
            Thread {
                val result = runCatching { RelaySelfTest.run(this) }.getOrElse { "FAIL: ${it.javaClass.simpleName}: ${it.message}" }
                runOnUiThread { if (!closing && !isDestroyed) show(result) }
            }.start()
        }
        label("Memoria e messaggi ricevuti", 18f, true)
        contents = label("", 14f)
        label("Attività recente", 18f, true)
        journal = label("Nessun evento", 13f)
        button("Sorgenti e licenza") {
            val notice = TextView(this).apply {
                text = "DropNet demo 0.4.0\nGPL-3.0-or-later · senza garanzia\n\nSorgenti:\nhttps://github.com/AdrianRipley/DropNet\n\n" +
                    assets.open("LICENSE").bufferedReader().use { it.readText() } +
                    "\n\nKotlin standard library — JetBrains, Apache License 2.0:\n" +
                    assets.open("Apache-2.0.txt").bufferedReader().use { it.readText() }
                setPadding(dp(18), dp(10), dp(18), dp(10)); textSize = 13f; setTextIsSelectable(true)
            }
            AlertDialog.Builder(this).setTitle("DropNet · software libero")
                .setView(ScrollView(this).apply { addView(notice) }).setPositiveButton("Chiudi", null).show()
        }
        refresh()
    }

    private fun permissions(): Array<String> = if (Build.VERSION.SDK_INT >= 31)
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE)
    else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun toggleParticipation() {
        if (!foreground || closing) return
        if (transport.running) { transport.stop(); refresh(); return }
        val missing = permissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 41)
        else { transport.start(); refresh() }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (closing || isDestroyed) return
        if (requestCode == 41) {
            if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                if (foreground) transport.start()
                else show("Permessi acquisiti: torna alla schermata e avvia esplicitamente la partecipazione")
            }
            else show("Permessi non concessi: test locale e memoria restano disponibili")
            refresh()
        }
    }

    private fun show(message: String) {
        state.text = message
        events.addFirst(message)
        while (events.size > 12) events.removeLast()
        if (::journal.isInitialized) journal.text = events.joinToString("\n\n")
        refresh()
    }

    private fun refresh() {
        if (::participation.isInitialized) participation.text = if (transport.running) "Metti in pausa" else "Avvia partecipazione Bluetooth"
        if (::contents.isInitialized) {
            val queued = store.queued()
            val received = store.inbox()
            contents.text = "${queued.size} copie in attesa · ${store.usedBytes()} / 65536 byte\n${received.size} messaggi ricevuti (ultimi 100)" +
                received.take(5).joinToString("", prefix = if (received.isEmpty()) "" else "\n\n") {
                    "Da ${it.source.toString().take(8)}: ${it.text}\n\n"
                }
        }
    }

    override fun onStart() { super.onStart(); foreground = true }
    override fun onStop() { foreground = false; if (::transport.isInitialized) transport.stop(); super.onStop() }
    override fun onDestroy() {
        closing = true
        if (::transport.isInitialized) transport.stop()
        if (::store.isInitialized) store.close()
        super.onDestroy()
    }
}
