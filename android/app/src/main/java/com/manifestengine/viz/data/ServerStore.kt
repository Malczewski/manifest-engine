package com.manifestengine.viz.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import java.util.UUID

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("servers")

/**
 * Persists the list of configured servers and the active selection.
 * Designed so a future cloud base URL is just another Server entry.
 */
class ServerStore(private val context: Context) {
    private val serversKey = stringPreferencesKey("servers_json")
    private val activeKey = stringPreferencesKey("active_id")

    val servers: Flow<List<Server>> = context.dataStore.data.map { prefs ->
        prefs[serversKey]?.let { runCatching { Json.decodeFromString<List<Server>>(it) }.getOrNull() }
            ?: emptyList()
    }

    val activeId: Flow<String?> = context.dataStore.data.map { it[activeKey] }

    suspend fun addServer(name: String, baseUrl: String): Server {
        val server = Server(UUID.randomUUID().toString(), name.ifBlank { baseUrl }, baseUrl.trimEnd('/'))
        context.dataStore.edit { prefs ->
            val current = prefs[serversKey]
                ?.let { runCatching { Json.decodeFromString<List<Server>>(it) }.getOrNull() }
                ?: emptyList()
            prefs[serversKey] = Json.encodeToString(current + server)
            if (prefs[activeKey] == null) prefs[activeKey] = server.id
        }
        return server
    }

    suspend fun removeServer(id: String) {
        context.dataStore.edit { prefs ->
            val current = prefs[serversKey]
                ?.let { runCatching { Json.decodeFromString<List<Server>>(it) }.getOrNull() }
                ?: emptyList()
            val next = current.filterNot { it.id == id }
            prefs[serversKey] = Json.encodeToString(next)
            if (prefs[activeKey] == id) prefs[activeKey] = next.firstOrNull()?.id ?: ""
        }
    }

    suspend fun setActive(id: String) {
        context.dataStore.edit { it[activeKey] = id }
    }
}
