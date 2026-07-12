package com.manifestengine.viz.data

import android.database.sqlite.SQLiteDatabase
import java.io.Closeable
import java.io.File

/**
 * Reads an unpacked .bookpack's book.db. This is the client side of the
 * contract defined in server/app/bookpack.py — keep column names in sync.
 */
class BookPack private constructor(
    private val db: SQLiteDatabase,
    private val packDir: File,
) : Closeable {

    companion object {
        fun open(packDir: File): BookPack {
            val db = SQLiteDatabase.openDatabase(
                File(packDir, "book.db").absolutePath, null, SQLiteDatabase.OPEN_READONLY,
            )
            return BookPack(db, packDir)
        }
    }

    private fun meta(key: String, default: String = ""): String {
        db.rawQuery("SELECT value FROM meta WHERE key = ?", arrayOf(key)).use { c ->
            return if (c.moveToFirst()) c.getString(0) else default
        }
    }

    fun toLocalBook(dir: File): LocalBook = LocalBook(
        bookId = dir.name,
        title = meta("title", "Untitled"),
        author = meta("author", "Unknown"),
        numScenes = meta("num_scenes", "0").toIntOrNull() ?: 0,
        packDir = dir.absolutePath,
    )

    fun chapters(): List<Chapter> {
        val out = ArrayList<Chapter>()
        db.rawQuery("SELECT idx, title FROM chapters ORDER BY idx", null).use { c ->
            while (c.moveToNext()) out.add(Chapter(c.getInt(0), c.getString(1) ?: "Chapter ${c.getInt(0) + 1}"))
        }
        return out
    }

    /** Scenes for a chapter, ordered by their sequence within it. */
    fun scenesForChapter(chapterIdx: Int): List<Scene> =
        query("SELECT id, chapter_idx, seq, start_token, end_token, summary, image_path " +
            "FROM scenes WHERE chapter_idx = ? ORDER BY seq", arrayOf(chapterIdx.toString()))

    /** All scenes ordered by reading position (used by the Phase 3 matcher). */
    fun allScenes(): List<Scene> =
        query("SELECT id, chapter_idx, seq, start_token, end_token, summary, image_path " +
            "FROM scenes ORDER BY start_token", null)

    private fun query(sql: String, args: Array<String>?): List<Scene> {
        val out = ArrayList<Scene>()
        db.rawQuery(sql, args).use { c ->
            while (c.moveToNext()) {
                val rel = c.getString(6) ?: ""
                out.add(
                    Scene(
                        id = c.getInt(0),
                        chapterIdx = c.getInt(1),
                        seq = c.getInt(2),
                        startToken = c.getInt(3),
                        endToken = c.getInt(4),
                        summary = c.getString(5) ?: "",
                        imagePath = File(packDir, rel).absolutePath,
                    )
                )
            }
        }
        return out
    }

    override fun close() = db.close()
}
