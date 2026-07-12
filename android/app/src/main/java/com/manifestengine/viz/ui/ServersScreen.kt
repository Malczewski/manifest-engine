package com.manifestengine.viz.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
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
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("http://") }

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
            Spacer(Modifier.width(8.dp))
            OutlinedTextField(
                value = url, onValueChange = { url = it },
                label = { Text("Base URL, e.g. http://192.168.1.20:8000") },
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = {
                    if (url.isNotBlank() && url != "http://") {
                        vm.add(name, url)
                        name = ""; url = "http://"
                    }
                },
                modifier = Modifier.padding(top = 8.dp),
            ) { Text("Add server") }

            LazyColumn(Modifier.padding(top = 16.dp)) {
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
            Row {} // spacer
        }
    }
}
