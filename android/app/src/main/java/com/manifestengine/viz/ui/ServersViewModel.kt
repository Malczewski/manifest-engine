package com.manifestengine.viz.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.manifestengine.viz.VizApp
import com.manifestengine.viz.data.Server
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class ServersUi(val servers: List<Server> = emptyList(), val activeId: String? = null)

class ServersViewModel : ViewModel() {
    private val store = VizApp.instance.serverStore

    val ui: StateFlow<ServersUi> =
        combine(store.servers, store.activeId) { servers, active -> ServersUi(servers, active) }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), ServersUi())

    fun add(name: String, baseUrl: String) = viewModelScope.launch { store.addServer(name, baseUrl) }
    fun remove(id: String) = viewModelScope.launch { store.removeServer(id) }
    fun setActive(id: String) = viewModelScope.launch { store.setActive(id) }
}
