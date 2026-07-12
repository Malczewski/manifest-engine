package com.manifestengine.viz.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.manifestengine.viz.VizApp
import com.manifestengine.viz.data.BookSummary
import com.manifestengine.viz.data.LocalBook
import com.manifestengine.viz.data.Server
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

data class LibraryUi(
    val activeServer: Server? = null,
    val remote: List<BookSummary> = emptyList(),
    val local: List<LocalBook> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val downloading: Map<String, Float> = emptyMap(), // bookId -> progress
)

class LibraryViewModel : ViewModel() {
    private val app = VizApp.instance
    private val _ui = MutableStateFlow(LibraryUi())
    val ui: StateFlow<LibraryUi> = _ui

    fun refresh() = viewModelScope.launch {
        _ui.value = _ui.value.copy(loading = true, error = null, local = app.packManager.localBooks())
        val servers = app.serverStore.servers.first()
        val activeId = app.serverStore.activeId.first()
        val active = servers.firstOrNull { it.id == activeId } ?: servers.firstOrNull()
        _ui.value = _ui.value.copy(activeServer = active)
        if (active == null) {
            _ui.value = _ui.value.copy(loading = false, error = "No server selected")
            return@launch
        }
        runCatching { app.api.listBooks(active.baseUrl) }
            .onSuccess { _ui.value = _ui.value.copy(remote = it, loading = false) }
            .onFailure { _ui.value = _ui.value.copy(loading = false, error = it.message ?: "Failed to reach server") }
    }

    fun download(book: BookSummary) = viewModelScope.launch {
        val server = _ui.value.activeServer ?: return@launch
        _ui.value = _ui.value.copy(downloading = _ui.value.downloading + (book.id to 0f))
        runCatching {
            app.packManager.download(server.baseUrl, book.id) { p ->
                _ui.value = _ui.value.copy(downloading = _ui.value.downloading + (book.id to p.coerceAtLeast(0f)))
            }
        }.onSuccess {
            _ui.value = _ui.value.copy(
                downloading = _ui.value.downloading - book.id,
                local = app.packManager.localBooks(),
            )
        }.onFailure {
            _ui.value = _ui.value.copy(
                downloading = _ui.value.downloading - book.id,
                error = "Download failed: ${it.message}",
            )
        }
    }

    fun isLocal(bookId: String) = app.packManager.isDownloaded(bookId)

    fun deleteLocal(bookId: String) = viewModelScope.launch {
        app.packManager.delete(bookId)
        _ui.value = _ui.value.copy(local = app.packManager.localBooks())
    }
}
