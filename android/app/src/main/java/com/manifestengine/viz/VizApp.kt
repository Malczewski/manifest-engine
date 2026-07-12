package com.manifestengine.viz

import android.app.Application
import com.manifestengine.viz.data.ApiClient
import com.manifestengine.viz.data.PackManager
import com.manifestengine.viz.data.ServerStore

/** Tiny manual DI container — no framework needed for this scope. */
class VizApp : Application() {
    lateinit var serverStore: ServerStore
        private set
    lateinit var api: ApiClient
        private set
    lateinit var packManager: PackManager
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        api = ApiClient()
        serverStore = ServerStore(this)
        packManager = PackManager(this, api)
    }

    companion object {
        lateinit var instance: VizApp
            private set
    }
}
