package com.manifestengine.viz.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.manifestengine.viz.VizApp
import com.manifestengine.viz.data.Server
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket

enum class ScanState { Idle, Scanning, Done }

data class ServersUi(val servers: List<Server> = emptyList(), val activeId: String? = null)

class ServersViewModel : ViewModel() {
    private val store = VizApp.instance.serverStore

    val ui: StateFlow<ServersUi> =
        combine(store.servers, store.activeId) { servers, active -> ServersUi(servers, active) }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), ServersUi())

    private val _scanState = MutableStateFlow(ScanState.Idle)
    val scanState: StateFlow<ScanState> = _scanState.asStateFlow()

    private val _found = MutableStateFlow<List<String>>(emptyList())
    val found: StateFlow<List<String>> = _found.asStateFlow()

    /** e.g. "192.168.1" — null when not on WiFi or subnet undetectable. */
    val localSubnet: String? = detectSubnet()

    fun add(name: String, baseUrl: String) = viewModelScope.launch { store.addServer(name, baseUrl) }
    fun remove(id: String) = viewModelScope.launch { store.removeServer(id) }
    fun setActive(id: String) = viewModelScope.launch { store.setActive(id) }

    fun addFound(host: String) = viewModelScope.launch {
        store.addServer(name = host, baseUrl = "http://$host:8000")
    }

    fun scan() {
        if (_scanState.value == ScanState.Scanning) return
        val subnet = localSubnet ?: return
        viewModelScope.launch {
            _scanState.value = ScanState.Scanning
            _found.value = emptyList()
            val hits = mutableListOf<String>()
            val sem = Semaphore(40)
            withContext(Dispatchers.IO) {
                (1..254).map { i ->
                    async {
                        sem.withPermit {
                            val host = "$subnet.$i"
                            try {
                                Socket().use { it.connect(InetSocketAddress(host, 8000), 400) }
                                synchronized(hits) { hits.add(host) }
                            } catch (_: Exception) {}
                        }
                    }
                }.awaitAll()
            }
            _found.value = hits.sorted()
            _scanState.value = ScanState.Done
        }
    }

    private fun detectSubnet(): String? = try {
        NetworkInterface.getNetworkInterfaces()?.asSequence()
            ?.filter { !it.isLoopback && it.isUp }
            ?.flatMap { it.inetAddresses.asSequence() }
            ?.filterIsInstance<Inet4Address>()
            ?.filterNot { it.isLoopbackAddress }
            ?.mapNotNull { addr ->
                val parts = addr.hostAddress?.split(".") ?: return@mapNotNull null
                if (parts.size == 4) "${parts[0]}.${parts[1]}.${parts[2]}" else null
            }
            ?.firstOrNull()
    } catch (_: Exception) { null }
}
