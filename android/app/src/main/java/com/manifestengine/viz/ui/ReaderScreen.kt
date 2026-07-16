package com.manifestengine.viz.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowBackIosNew
import androidx.compose.material.icons.filled.ArrowForwardIos
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import coil.compose.AsyncImage
import coil.request.ImageRequest
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReaderScreen(bookId: String, onBack: () -> Unit, vm: ReaderViewModel = viewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    LaunchedEffect(bookId) { vm.load(bookId) }
    val context = LocalContext.current
    var menuOpen by remember { mutableStateOf(false) }

    val micPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) vm.setListening(true)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(ui.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    val current = ui.chapters.firstOrNull { it.idx == ui.chapterIdx }
                    TextButton(onClick = { menuOpen = true }) {
                        Text(current?.title?.take(24) ?: "Chapter", maxLines = 1)
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        ui.chapters.forEach { ch ->
                            DropdownMenuItem(
                                text = { Text(ch.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                                onClick = { vm.selectChapter(ch.idx); menuOpen = false },
                            )
                        }
                    }
                },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            // Listening toggle
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(if (ui.listening) "Listening (auto)" else "Manual")
                Switch(
                    checked = ui.listening,
                    onCheckedChange = { enabled ->
                        if (enabled) {
                            if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                                == PackageManager.PERMISSION_GRANTED
                            ) {
                                vm.setListening(true)
                            } else {
                                micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                            }
                        } else {
                            vm.setListening(false)
                        }
                    },
                )
            }

            // Scene image
            Box(Modifier.weight(1f).fillMaxWidth().background(Color.Black), Alignment.Center) {
                val scene = ui.currentScene
                if (scene != null) {
                    AsyncImage(
                        model = ImageRequest.Builder(context)
                            .data(File(scene.imagePath))
                            .crossfade(400)
                            .build(),
                        contentDescription = scene.summary,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    Text(
                        scene.summary,
                        color = Color.White,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .fillMaxWidth()
                            .background(Color(0x99000000))
                            .padding(12.dp),
                    )
                } else {
                    Text(ui.error ?: "No scenes", color = Color.White)
                }
            }

            // Controls
            if (ui.listening) {
                ListeningPanel(lastTranscript = ui.lastTranscript)
            } else {
                ManualControls(
                    position = ui.sceneIdx + 1,
                    total = ui.scenes.size,
                    onPrev = { vm.prev() },
                    onNext = { vm.next() },
                )
            }

            // Error banner (e.g. ASR unavailable) shown below controls when a scene is visible
            ui.error?.takeIf { ui.currentScene != null }?.let {
                Text(
                    it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ListeningPanel(lastTranscript: String) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Text(
            "Listening…",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        if (lastTranscript.isNotBlank()) {
            Text(
                "“$lastTranscript”",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

@Composable
private fun ManualControls(position: Int, total: Int, onPrev: () -> Unit, onNext: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onPrev) {
            Icon(Icons.Filled.ArrowBackIosNew, contentDescription = "Previous scene")
        }
        Text("Scene $position / $total")
        IconButton(onClick = onNext) {
            Icon(Icons.Filled.ArrowForwardIos, contentDescription = "Next scene")
        }
    }
}
