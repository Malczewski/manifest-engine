package com.manifestengine.viz.data

import kotlinx.serialization.Serializable

/** A configured pre-processing server (matches the plan's manual host entry). */
@Serializable
data class Server(
    val id: String,
    val name: String,
    val baseUrl: String, // e.g. http://192.168.1.20:8000
)

/** Mirrors the server's BookSummary JSON (see server/app/models.py). */
@Serializable
data class BookSummary(
    val id: String,
    val title: String = "Untitled",
    val author: String = "Unknown",
    val status: String = "queued",
    val num_scenes: Int = 0,
    val has_pack: Boolean = false,
)

/** A book already downloaded and unpacked on the device. */
data class LocalBook(
    val bookId: String,
    val title: String,
    val author: String,
    val numScenes: Int,
    val packDir: String, // absolute path to the unzipped .bookpack directory
)

/** One chapter row from book.db. */
data class Chapter(
    val idx: Int,
    val title: String,
)

/** One scene row from book.db, with an absolute image path resolved for Coil. */
data class Scene(
    val id: Int,
    val chapterIdx: Int,
    val seq: Int,
    val startToken: Int,
    val endToken: Int,
    val summary: String,
    val imagePath: String, // absolute file path
)
