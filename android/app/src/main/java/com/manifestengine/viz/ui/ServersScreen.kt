package com.manifestengine.viz.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServersScreen(onBack: () -> Unit, vm: ServersViewModel = viewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val scanState by vm.scanState.collectAsStateWithLifecycle()
    val found by vm.found.collectAsStateWithLifecycle()
    var name by remember { mutableStateOf("") }
    var url by remember {
        mutableStateOf(
            if (vm.localSubnet != null) "http://${vm.localSubnet}." else "http://"
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Servers") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).fillMaxSize()) {
            OutlinedTextField(
                value = name, onValueChange = { name = it },
                label = { Text("Name (optional)") }, modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = url, onValueChange = { url = it },
                label = { Text("Base URL, e.g. http://192.168.1.20:8000") },
                modifier = Modifier.fillMaxWidth(),
            )
            Row(
                modifier = Modifier.padding(top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = {
                        if (url.isNotBlank() && url != "http://") {
                            vm.add(name, url)
                            name = ""
                            url = if (vm.localSubnet != null) "http://${vm.localSubnet}." else "http://"
                        }
                    },
                ) { Text("Add") }
                OutlinedButton(
                    onClick = { vm.scan() },
                    enabled = scanState != ScanState.Scanning && vm.localSubnet != null,
                ) { Text(if (scanState == ScanState.Scanning) "Scanning…" else "Scan network") }
            }

            if (scanState == ScanState.Scanning) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
            }

            if (found.isNotEmpty() || scanState == ScanState.Done) {
                Text(
                    text = if (found.isEmpty()) "Nothing found on port 8000" else "Found on port 8000:",
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
                )
                found.forEach { host ->
                    val alreadyAdded = ui.servers.any { it.baseUrl == "http://$host:8000" }
                    ListItem(
                        headlineContent = { Text("http://$host:8000") },
                        trailingContent = {
                            if (alreadyAdded) {
                                Icon(Icons.Filled.CheckCircle, contentDescription = "Added")
                            } else {
                                IconButton(onClick = { vm.addFound(host) }) {
                                    Icon(Icons.Filled.Add, contentDescription = "Add")
                                }
                            }
                        },
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            if (ui.servers.isNotEmpty()) {
                Text("Saved servers", style = MaterialTheme.typography.labelMedium)
            }

            LazyColumn(Modifier.padding(top = 8.dp)) {
                items(ui.servers, key = { it.id }) { server ->
                    ListItem(
                        headlineContent = { Text(server.name) },
                        supportingContent = { Text(server.baseUrl) },
                        leadingContent = {
                            IconButton(onClick = { vm.setActive(server.id) }) {
                                if (server.id == ui.activeId) {
                                    Icon(Icons.Filled.CheckCircle, contentDescription = "Active")
                                } else {
                                    Icon(Icons.Outlined.RadioButtonUnchecked, contentDescription = "Set active")
                                }
                            }
                        },
                        trailingContent = {
                            IconButton(onClick = { vm.remove(server.id) }) {
                                Icon(Icons.Filled.Delete, contentDescription = "Remove")
                            }
                        },
                    )
                }
            }
        }
    }
}
