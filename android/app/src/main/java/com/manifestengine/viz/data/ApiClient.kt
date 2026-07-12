package com.manifestengine.viz.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/** Thin HTTP client for the pre-processing server's REST API. */
class ApiClient {
    private val json = Json { ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun listBooks(baseUrl: String): List<BookSummary> = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$baseUrl/books").build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) error("Server returned ${resp.code}")
            val body = resp.body?.string() ?: "[]"
            json.decodeFromString<List<BookSummary>>(body)
        }
    }

    /** Streams the .bookpack to [dest], reporting 0..1 progress (or -1 if unknown). */
    suspend fun downloadPack(
        baseUrl: String,
        bookId: String,
        dest: File,
        onProgress: (Float) -> Unit,
    ) = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$baseUrl/books/$bookId/pack").build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) error("Download failed: ${resp.code}")
            val body = resp.body ?: error("Empty response")
            val total = body.contentLength()
            dest.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    var read: Int
                    var written = 0L
                    while (input.read(buf).also { read = it } != -1) {
                        out.write(buf, 0, read)
                        written += read
                        onProgress(if (total > 0) written.toFloat() / total else -1f)
                    }
                }
            }
        }
    }
}
