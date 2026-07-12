package com.manifestengine.viz.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.ZipInputStream

/**
 * Downloads, unzips and tracks .bookpacks on device. Each pack is unpacked to
 * filesDir/packs/{bookId}/ (containing book.db + images/), enabling fully
 * offline playback afterwards.
 */
class PackManager(private val context: Context, private val api: ApiClient) {

    private val packsRoot: File get() = File(context.filesDir, "packs").apply { mkdirs() }

    /** Root directory containing one subdirectory per downloaded book. */
    fun localBooksDir(): File = packsRoot

    fun localBooks(): List<LocalBook> =
        packsRoot.listFiles { f -> f.isDirectory && File(f, "book.db").exists() }
            ?.mapNotNull { dir -> runCatching { BookPack.open(dir).use { it.toLocalBook(dir) } }.getOrNull() }
            ?: emptyList()

    fun isDownloaded(bookId: String): Boolean = File(packsRoot, "$bookId/book.db").exists()

    suspend fun download(baseUrl: String, bookId: String, onProgress: (Float) -> Unit): LocalBook =
        withContext(Dispatchers.IO) {
            val tmpZip = File(context.cacheDir, "$bookId.bookpack")
            api.downloadPack(baseUrl, bookId, tmpZip, onProgress)

            val dir = File(packsRoot, bookId)
            if (dir.exists()) dir.deleteRecursively()
            dir.mkdirs()
            unzip(tmpZip, dir)
            tmpZip.delete()

            BookPack.open(dir).use { it.toLocalBook(dir) }
        }

    fun delete(bookId: String) {
        File(packsRoot, bookId).deleteRecursively()
    }

    private fun unzip(zip: File, destDir: File) {
        ZipInputStream(zip.inputStream().buffered()).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val out = File(destDir, entry.name)
                // Guard against zip-slip.
                if (!out.canonicalPath.startsWith(destDir.canonicalPath + File.separator)) {
                    error("Unsafe zip entry: ${entry.name}")
                }
                if (entry.isDirectory) {
                    out.mkdirs()
                } else {
                    out.parentFile?.mkdirs()
                    out.outputStream().use { zis.copyTo(it) }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
    }
}
