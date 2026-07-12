package com.manifestengine.viz.ui

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
    var menuOpen by remember { mutableStateOf(false) }

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
            // --- listening toggle (auto vs manual) ---
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(if (ui.listening) "Listening (auto)" else "Manual")
                Switch(checked = ui.listening, onCheckedChange = { vm.setListening(it) })
            }

            Box(Modifier.weight(1f).fillMaxWidth().background(Color.Black), Alignment.Center) {
                val scene = ui.currentScene
                if (scene != null) {
                    val context = LocalContext.current
                    AsyncImage(
                        model = ImageRequest.Builder(context)
                            .data(File(scene.imagePath))
                            .crossfade(400)
                            .build(),
                        contentDescription = scene.summary,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    // Caption overlay.
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

            if (ui.listening) {
                // Phase 3: mic + Whisper + matcher drive the scene automatically.
                Text(
                    "Auto mode: microphone + on-device ASR will drive scenes (coming in Phase 3). " +
                        "Toggle off to navigate manually.",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(16.dp),
                )
            } else {
                ManualControls(
                    position = ui.sceneIdx + 1,
                    total = ui.scenes.size,
                    onPrev = { vm.prev() },
                    onNext = { vm.next() },
                )
            }
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
