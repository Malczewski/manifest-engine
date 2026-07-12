package com.manifestengine.viz.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.manifestengine.viz.VizApp
import com.manifestengine.viz.data.BookPack
import com.manifestengine.viz.data.Chapter
import com.manifestengine.viz.data.Scene
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

data class ReaderUi(
    val title: String = "",
    val chapters: List<Chapter> = emptyList(),
    val chapterIdx: Int = 0,
    val scenes: List<Scene> = emptyList(), // scenes in the current chapter
    val sceneIdx: Int = 0,
    val listening: Boolean = false, // Phase 3: mic+ASR auto mode (stub for now)
    val error: String? = null,
) {
    val currentScene: Scene? get() = scenes.getOrNull(sceneIdx)
}

class ReaderViewModel : ViewModel() {
    private val app = VizApp.instance
    private val _ui = MutableStateFlow(ReaderUi())
    val ui: StateFlow<ReaderUi> = _ui

    private var scenesByChapter: Map<Int, List<Scene>> = emptyMap()

    fun load(bookId: String) = viewModelScope.launch {
        val result = withContext(Dispatchers.IO) {
            val dir = File(app.packManager.localBooksDir(), bookId)
            if (!File(dir, "book.db").exists()) return@withContext null
            BookPack.open(dir).use { pack ->
                Triple(pack.toLocalBook(dir).title, pack.chapters(), pack.allScenes())
            }
        } ?: run {
            _ui.value = _ui.value.copy(error = "Book not found on device")
            return@launch
        }
        val (title, chapters, allScenes) = result
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

    /** Manual navigation; rolls over chapter boundaries. */
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
                _ui.value = s.copy(chapterIdx = prevChapter.idx, scenes = scenes, sceneIdx = (scenes.size - 1).coerceAtLeast(0))
            }
        }
    }

    /** Phase 3 will start/stop the mic+ASR service here; for now it's inert. */
    fun setListening(enabled: Boolean) {
        _ui.value = _ui.value.copy(listening = enabled)
    }
}
