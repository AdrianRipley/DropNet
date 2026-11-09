// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.demo

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.dropnet.core.DataCodec
import org.dropnet.core.Message
import java.util.UUID

/** Logical queue quota, independently bounded dedup and inbox. No delivery oracle. */
class NodeStore(context: Context, name: String, val nodeId: UUID,
                private val capacity: Int = 65536, private val dedupCapacity: Int = 256) :
    SQLiteOpenHelper(context, name, null, 1) {
    enum class Acceptance(val wire: Int) { REJECTED(0), RELAY(1), DELIVERED(2), DUPLICATE(3) }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE queue(id TEXT PRIMARY KEY, created INTEGER, expires INTEGER, body BLOB, size INTEGER)")
        db.execSQL("CREATE TABLE seen(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, expires INTEGER)")
        db.execSQL("CREATE TABLE inbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, received INTEGER, body BLOB)")
        db.execSQL("CREATE TABLE identity(id TEXT NOT NULL)")
        db.execSQL("INSERT INTO identity VALUES(?)", arrayOf(nodeId.toString()))
    }
    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        error("No migration is defined; preserve the existing database")
    }
    override fun onOpen(db: SQLiteDatabase) {
        super.onOpen(db)
        db.rawQuery("SELECT id FROM identity", null).use {
            check(it.moveToFirst() && it.getString(0) == nodeId.toString()) { "Node identity changed" }
        }
    }

    private fun expire(db: SQLiteDatabase, now: Long) {
        db.delete("queue", "expires<=?", arrayOf(now.toString()))
        db.delete("seen", "expires<=?", arrayOf(now.toString()))
    }

    @Synchronized fun accept(message: Message, now: Long = System.currentTimeMillis()): Acceptance {
        if (!message.isLive(now)) return Acceptance.REJECTED
        val body = DataCodec.encode(message)
        val db = writableDatabase
        var result = Acceptance.REJECTED
        db.beginTransaction()
        try {
            expire(db, now)
            val duplicate = db.rawQuery("SELECT id FROM seen WHERE id=? UNION SELECT id FROM queue WHERE id=?",
                arrayOf(message.id.toString(), message.id.toString())).use { it.moveToFirst() }
            if (duplicate) result = Acceptance.DUPLICATE
            else {
                val used = db.rawQuery("SELECT COALESCE(SUM(size),0) FROM queue", null).use { it.moveToFirst(); it.getLong(0) }
                val destination = message.destination == nodeId
                if (destination || used + body.size <= capacity) {
                    val seen = ContentValues().apply { put("id", message.id.toString()); put("expires", message.expires) }
                    db.insertOrThrow("seen", null, seen)
                    db.execSQL("DELETE FROM seen WHERE seq IN (SELECT seq FROM seen ORDER BY seq DESC LIMIT -1 OFFSET ?)", arrayOf(dedupCapacity))
                    val values = ContentValues().apply { put("id", message.id.toString()); put("body", body) }
                    if (destination) {
                        values.put("received", now)
                        db.insertOrThrow("inbox", null, values)
                        db.execSQL("DELETE FROM inbox WHERE seq IN (SELECT seq FROM inbox ORDER BY seq DESC LIMIT -1 OFFSET 100)")
                        result = Acceptance.DELIVERED
                    } else {
                        values.put("created", message.created); values.put("expires", message.expires); values.put("size", body.size)
                        db.insertOrThrow("queue", null, values)
                        result = Acceptance.RELAY
                    }
                }
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
        // This method returns only AFTER the SQLite transaction has committed.
        return result
    }

    @Synchronized fun queued(now: Long = System.currentTimeMillis()): List<Message> {
        val db = writableDatabase
        expire(db, now)
        return db.rawQuery("SELECT body FROM queue ORDER BY created,id", null).use { cursor ->
            buildList { while (cursor.moveToNext()) add(DataCodec.decode(cursor.getBlob(0))) }
        }
    }

    @Synchronized fun inventory(now: Long = System.currentTimeMillis()): List<String> {
        val db = writableDatabase
        expire(db, now)
        return db.rawQuery("SELECT id FROM queue UNION SELECT id FROM seen", null).use { cursor ->
            buildList { while (cursor.moveToNext()) add(cursor.getString(0)) }
        }
    }

    @Synchronized fun inbox(): List<Message> = readableDatabase.rawQuery("SELECT body FROM inbox ORDER BY seq DESC", null).use { cursor ->
        buildList { while (cursor.moveToNext()) add(DataCodec.decode(cursor.getBlob(0))) }
    }

    @Synchronized fun usedBytes(): Long = readableDatabase.rawQuery("SELECT COALESCE(SUM(size),0) FROM queue", null).use {
        it.moveToFirst(); it.getLong(0)
    }
}
