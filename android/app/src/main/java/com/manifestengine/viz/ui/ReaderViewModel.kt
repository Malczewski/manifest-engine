package com.manifestengine.viz.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.manifestengine.viz.VizApp
import com.manifestengine.viz.data.BookPack
import com.manifestengine.viz.data.Chapter
import com.manifestengine.viz.data.ListeningSession
import com.manifestengine.viz.data.Scene
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.ZipInputStream

data class ReaderUi(
    val title: String = "",
    val chapters: List<Chapter> = emptyList(),
    val chapterIdx: Int = 0,
    val scenes: List<Scene> = emptyList(),
    val sceneIdx: Int = 0,
    val listening: Boolean = false,
    val lastTranscript: String = "", // last ASR result shown to the user
    val error: String? = null,
) {
    val currentScene: Scene? get() = scenes.getOrNull(sceneIdx)
}

class ReaderViewModel : ViewModel() {
    private val app = VizApp.instance
    private val _ui = MutableStateFlow(ReaderUi())
    val ui: StateFlow<ReaderUi> = _ui

    // Kept open for the life of the ViewModel so matchTranscript() can query it.
    @Volatile private var pack: BookPack? = null
    @Volatile private var scenesByChapter: Map<Int, List<Scene>> = emptyMap()

    private var listeningSession: ListeningSession? = null
    private val transcriptBuffer = ArrayDeque<String>() // last N results for wider context
    @Volatile private var lastMatchedTokenPos: Int = 0

    override fun onCleared() {
        super.onCleared()
        listeningSession?.stop()
        pack?.close()
    }

    fun load(bookId: String) = viewModelScope.launch {
        val r = withContext(Dispatchers.IO) {
            val dir = File(app.packManager.localBooksDir(), bookId)
            if (!File(dir, "book.db").exists()) return@withContext null
            val p = BookPack.open(dir)
            (p to p.toLocalBook(dir).title) to (p.chapters() to p.allScenes())
        } ?: run {
            _ui.value = _ui.value.copy(error = "Book not found on device")
            return@launch
        }

        val (packAndTitle, chaptersAndScenes) = r
        val (newPack, title) = packAndTitle
        val (chapters, allScenes) = chaptersAndScenes

        pack?.close()
        pack = newPack
        scenesByChapter = allScenes.groupBy { it.chapterIdx }
        val firstChapter = chapters.firstOrNull()?.idx ?: 0
        _ui.value = ReaderUi(
            title = title,
            chapters = chapters,
            chapterIdx = firstChapter,
            scenes = scenesByChapter[firstChapter].orEmpty(),
            sceneIdx = 0,
        )
    }

    fun selectChapter(idx: Int) {
        _ui.value = _ui.value.copy(chapterIdx = idx, scenes = scenesByChapter[idx].orEmpty(), sceneIdx = 0)
    }

    fun next() {
        val s = _ui.value
        if (s.sceneIdx < s.scenes.lastIndex) {
            _ui.value = s.copy(sceneIdx = s.sceneIdx + 1)
        } else {
            val nextChapter = s.chapters.firstOrNull { it.idx > s.chapterIdx }
            if (nextChapter != null) selectChapter(nextChapter.idx)
        }
    }

    fun prev() {
        val s = _ui.value
        if (s.sceneIdx > 0) {
            _ui.value = s.copy(sceneIdx = s.sceneIdx - 1)
        } else {
            val prevChapter = s.chapters.lastOrNull { it.idx < s.chapterIdx }
            if (prevChapter != null) {
                val scenes = scenesByChapter[prevChapter.idx].orEmpty()
                _ui.value = s.copy(
                    chapterIdx = prevChapter.idx,
                    scenes = scenes,
                    sceneIdx = (scenes.size - 1).coerceAtLeast(0),
                )
            }
        }
    }

    fun setListening(enabled: Boolean) {
        if (enabled == _ui.value.listening) return
        if (!enabled) {
            listeningSession?.stop()
            listeningSession = null
            _ui.value = _ui.value.copy(listening = false, lastTranscript = "")
            return
        }
        _ui.value = _ui.value.copy(listening = true, lastTranscript = "", error = null)
        viewModelScope.launch {
            if (!ensureVoskModel()) return@launch
            transcriptBuffer.clear()
            lastMatchedTokenPos = _ui.value.currentScene?.startToken ?: 0
            val session = ListeningSession(
                context = app,
                onTranscript = ::onTranscript,
                onUnavailable = {
                    _ui.value = _ui.value.copy(listening = false, error = "ASR unavailable")
                },
                onStatus = { msg -> _ui.value = _ui.value.copy(lastTranscript = msg) },
            )
            listeningSession?.stop()
            listeningSession = session
            if (!session.start()) listeningSession = null
        }
    }

    /** Downloads + unzips the Vosk small-en model on first use (~40 MB, one-time). */
    private suspend fun ensureVoskModel(): Boolean {
        val modelDir = File(app.filesDir, ListeningSession.MODEL_DIR)
        if (modelDir.exists()) return true

        return withContext(Dispatchers.IO) {
            _ui.value = _ui.value.copy(lastTranscript = "Downloading ASR model (~40 MB, one-time)…")
            val tmpZip = File(app.cacheDir, "vosk-model.zip")
            try {
                val conn = java.net.URL(ListeningSession.MODEL_ZIP_URL).openConnection().apply {
                    connectTimeout = 30_000; readTimeout = 180_000
                }
                conn.inputStream.use { input ->
                    tmpZip.outputStream().use { input.copyTo(it) }
                }
                _ui.value = _ui.value.copy(lastTranscript = "Extracting model…")
                ZipInputStream(tmpZip.inputStream()).use { zis ->
                    var entry = zis.nextEntry
                    while (entry != null) {
                        val rel = entry.name.substringAfter('/')   // strip top-level dir
                        if (rel.isNotEmpty()) {
                            val target = File(modelDir, rel)
                            if (entry.isDirectory) target.mkdirs()
                            else { target.parentFile?.mkdirs(); target.outputStream().use { zis.copyTo(it) } }
                        }
                        zis.closeEntry(); entry = zis.nextEntry
                    }
                }
                tmpZip.delete()
                true
            } catch (e: Exception) {
                tmpZip.delete(); modelDir.deleteRecursively()
                _ui.value = _ui.value.copy(
                    listening = false,
                    error = "Model download failed: ${e.message?.take(80)}",
                )
                false
            }
        }
    }

    // Called on the main thread by ListeningSession (RecognitionListener callbacks).
    private fun onTranscript(text: String) {
        transcriptBuffer.addLast(text)
        if (transcriptBuffer.size > 3) transcriptBuffer.removeFirst()
        val combined = transcriptBuffer.joinToString(" ") // capture before IO dispatch
        _ui.value = _ui.value.copy(lastTranscript = text)

        viewModelScope.launch(Dispatchers.IO) {
            val p = pack ?: return@launch
            val scene = p.matchTranscript(combined, lastMatchedTokenPos) ?: return@launch
            lastMatchedTokenPos = scene.startToken
            val chapterScenes = scenesByChapter[scene.chapterIdx].orEmpty()
            val sceneIdx = chapterScenes.indexOfFirst { it.id == scene.id }.coerceAtLeast(0)
            _ui.value = _ui.value.copy(
                chapterIdx = scene.chapterIdx,
                scenes = chapterScenes,
                sceneIdx = sceneIdx,
            )
        }
    }
}
