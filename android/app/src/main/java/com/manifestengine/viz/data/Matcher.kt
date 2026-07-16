package com.manifestengine.viz.data

import android.database.sqlite.SQLiteDatabase

/**
 * Aligns a noisy ASR transcript against the trigram index in book.db to find
 * the current reading position.  Normalization mirrors server/app/pipeline/tokenize.py
 * exactly — lowercase, punctuation stripped, digits spelled out — so ASR output
 * can be matched against the stored token stream.
 */
object Matcher {

    private val TOKEN_RE = Regex("[a-z0-9']+")

    private val ONES = arrayOf(
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen",
    )
    private val TENS = arrayOf(
        "", "", "twenty", "thirty", "forty", "fifty",
        "sixty", "seventy", "eighty", "ninety",
    )

    /** Normalize ASR text to match the token stream stored in book.db. */
    fun normalize(text: String): List<String> {
        val words = mutableListOf<String>()
        for (m in TOKEN_RE.findAll(text.lowercase())) {
            val raw = m.value
            if (raw.all { it.isDigit() }) {
                words += intToWords(raw.toInt())
            } else {
                words += raw
            }
        }
        return words
    }

    private fun intToWords(n: Int): List<String> = when {
        n < 20 -> listOf(ONES[n])
        n < 100 -> buildList {
            add(TENS[n / 10])
            if (n % 10 != 0) add(ONES[n % 10])
        }
        n < 1000 -> buildList {
            add(ONES[n / 100]); add("hundred")
            if (n % 100 != 0) addAll(intToWords(n % 100))
        }
        n < 10000 -> buildList {
            addAll(intToWords(n / 1000)); add("thousand")
            if (n % 1000 != 0) addAll(intToWords(n % 1000))
        }
        else -> listOf(n.toString())
    }

    /**
     * Returns the estimated current token position in the book, or null if no
     * reliable match is found.
     *
     * Voting scheme: each transcript trigram at transcript index [i] that appears
     * in the book at position [j] implies anchor [j - i] (the book position
     * corresponding to word 0 of the transcript).  The winning anchor has the most
     * votes; the returned position is its midpoint so the result lands inside the
     * matched window.
     *
     * [minTokenPos] prevents backward jumps — only book positions >= this value
     * are queried.
     */
    fun findBestTokenPosition(
        words: List<String>,
        db: SQLiteDatabase,
        minTokenPos: Int = 0,
    ): Int? {
        if (words.size < 3) return null

        val transcriptGrams = ArrayList<Pair<String, Int>>(words.size)
        for (i in 0..words.size - 3) {
            transcriptGrams += "${words[i]} ${words[i + 1]} ${words[i + 2]}" to i
        }
        val distinctGrams = transcriptGrams.map { it.first }.distinct()
        val placeholders = distinctGrams.joinToString(",") { "?" }
        val args = (distinctGrams + minTokenPos.toString()).toTypedArray()

        val gramToPositions = HashMap<String, MutableList<Int>>()
        db.rawQuery(
            "SELECT gram, pos FROM trigrams WHERE gram IN ($placeholders) AND pos >= ?",
            args,
        ).use { c ->
            while (c.moveToNext()) {
                gramToPositions.getOrPut(c.getString(0)) { mutableListOf() } += c.getInt(1)
            }
        }
        if (gramToPositions.isEmpty()) return null

        val anchorVotes = HashMap<Int, Int>()
        for ((gram, transcriptIdx) in transcriptGrams) {
            for (bookPos in gramToPositions[gram] ?: continue) {
                val anchor = bookPos - transcriptIdx
                anchorVotes[anchor] = (anchorVotes[anchor] ?: 0) + 1
            }
        }

        val best = anchorVotes.maxByOrNull { it.value } ?: return null
        if (best.value < 2) return null

        return (best.key + words.size / 2).coerceAtLeast(minTokenPos)
    }
}
