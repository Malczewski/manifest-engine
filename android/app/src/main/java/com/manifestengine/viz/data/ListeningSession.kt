package com.manifestengine.viz.data

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File

/**
 * On-device continuous ASR via Vosk.  Vosk uses AudioRecord with
 * VOICE_RECOGNITION source internally — it never calls requestAudioFocus,
 * so Audible keeps playing while we listen.
 *
 * The Vosk model (~40 MB) must already exist at [context.filesDir]/vosk-model
 * before start() is called; see ReaderViewModel.ensureVoskModel().
 */
class ListeningSession(
    private val context: Context,
    private val onTranscript: (text: String) -> Unit,
    private val onUnavailable: () -> Unit = {},
    private val onStatus: (String) -> Unit = {},
) {
    private var speechService: SpeechService? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    fun start(): Boolean {
        val modelDir = File(context.filesDir, MODEL_DIR)
        if (!modelDir.exists()) { onUnavailable(); return false }
        return try {
            val model = Model(modelDir.absolutePath)
            val rec = Recognizer(model, SAMPLE_RATE)
            val svc = SpeechService(rec, SAMPLE_RATE)
            speechService = svc
            svc.startListening(object : RecognitionListener {
                override fun onResult(hypothesis: String?) {
                    val text = parseText(hypothesis) ?: return
                    mainHandler.post { onTranscript(text) }
                }
                override fun onFinalResult(hypothesis: String?) {}
                override fun onPartialResult(hypothesis: String?) {}
                override fun onError(e: Exception?) {
                    mainHandler.post { onStatus("ASR error: ${e?.message?.take(80)}") }
                }
                override fun onTimeout() {}
            })
            true
        } catch (e: Exception) {
            mainHandler.post { onStatus("ASR init failed: ${e.message?.take(80)}") }
            false
        }
    }

    fun stop() {
        speechService?.stop()
        speechService = null
    }

    private fun parseText(json: String?): String? =
        Regex("\"text\"\\s*:\\s*\"([^\"]*)\"")
            .find(json ?: "")?.groupValues?.getOrElse(1) { "" }?.trim()
            ?.takeIf { it.isNotBlank() }

    companion object {
        const val MODEL_DIR = "vosk-model"
        const val MODEL_ZIP_URL =
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        private const val SAMPLE_RATE = 16000.0f
    }
}
