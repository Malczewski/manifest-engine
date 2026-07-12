package com.manifestengine.viz.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    onOpenServers: () -> Unit,
    onOpenBook: (String) -> Unit,
    vm: LibraryViewModel = viewModel(),
) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.refresh() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Library") },
                actions = {
                    IconButton(onClick = { vm.refresh() }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                    }
                    IconButton(onClick = onOpenServers) {
                        Icon(Icons.Filled.Settings, contentDescription = "Servers")
                    }
                },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            ListItem(
                headlineContent = { Text(ui.activeServer?.name ?: "No server selected") },
                supportingContent = {
                    Text(ui.error ?: ui.activeServer?.baseUrl ?: "Add one in Settings")
                },
                trailingContent = { TextButton(onClick = onOpenServers) { Text("Change") } },
            )
            if (ui.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            HorizontalDivider()

            LazyColumn(Modifier.fillMaxSize()) {
                if (ui.local.isNotEmpty()) {
                    item { SectionHeader("On this device") }
                    items(ui.local, key = { it.bookId }) { book ->
                        ListItem(
                            headlineContent = { Text(book.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            supportingContent = { Text("${book.author} · ${book.numScenes} scenes") },
                            trailingContent = {
                                Row3(
                                    open = { onOpenBook(book.bookId) },
                                    delete = { vm.deleteLocal(book.bookId) },
                                )
                            },
                        )
                    }
                }

                item { SectionHeader("On the server") }
                items(ui.remote, key = { it.id }) { book ->
                    val progress = ui.downloading[book.id]
                    ListItem(
                        headlineContent = { Text(book.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        supportingContent = {
                            when {
                                progress != null -> LinearProgressIndicator(
                                    progress = { progress }, modifier = Modifier.fillMaxWidth(),
                                )
                                book.status != "done" -> Text("processing… (${book.status})")
                                else -> Text("${book.author} · ${book.num_scenes} scenes")
                            }
                        },
                        trailingContent = {
                            when {
                                progress != null -> CircularProgressIndicator()
                                vm.isLocal(book.id) -> TextButton(onClick = { onOpenBook(book.id) }) { Text("Open") }
                                book.has_pack -> Button(onClick = { vm.download(book) }) { Text("Download") }
                                else -> Text("—")
                            }
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionHeader(text: String) {
    Text(
        text,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
        style = androidx.compose.material3.MaterialTheme.typography.titleSmall,
    )
}

@Composable
private fun Row3(open: () -> Unit, delete: () -> Unit) {
    androidx.compose.foundation.layout.Row {
        TextButton(onClick = open) { Text("Open") }
        TextButton(onClick = delete) { Text("Delete") }
    }
}
