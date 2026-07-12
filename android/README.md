# AudiobookViz — Android app

Companion app for the [pre-processing server](../server). Downloads a book's
`.bookpack`, then shows the matching scene image as the audiobook plays.

**Phase 1 (implemented):** configure/select a server, browse & download books,
and a **manual-mode reader** — chapter picker + prev/next scene navigation with
crossfaded images. The manual mode doubles as the test harness for packs.

**Phase 3 (stubbed):** the "Listening (auto)" toggle is present but inert; mic
capture + on-device Whisper ASR + the fuzzy matcher land later.

## Build

Requires **Android Studio** (Koala/Ladybug or newer) or the Android SDK +
command-line tools. JDK 17 is used.

```bash
# From android/ — first generate the Gradle wrapper jar (not checked in),
# using a locally installed Gradle 8.9+:
gradle wrapper --gradle-version 8.9

# then build / install to a connected device or emulator:
./gradlew installDebug
```

Or simply **open the `android/` folder in Android Studio** and press Run — it
downloads the SDK, generates the wrapper, and resolves dependencies for you.

> Note: `gradle/wrapper/gradle-wrapper.jar` is intentionally absent (it is a
> binary). Android Studio or `gradle wrapper` regenerates it on first use.

## Use

1. Run the server (see [../server/README.md](../server/README.md)) and note the
   Mac's LAN IP, e.g. `http://192.168.1.20:8000`.
2. In the app: **Settings (gear) → Add server** with that URL, mark it active.
3. Back on **Library**: server books appear. **Download** a finished one.
4. Tap **Open** → the reader loads. With **Listening off** (default), use the
   chapter dropdown and the ‹ › buttons to step through scenes.

The phone and Mac must be on the same Wi-Fi. `usesCleartextTraffic` is enabled
for plain-HTTP LAN servers.

## Structure

```
app/src/main/java/com/manifestengine/viz/
  MainActivity.kt          Compose entry point
  VizApp.kt                manual DI container (ServerStore, ApiClient, PackManager)
  data/
    Models.kt              Server, BookSummary, LocalBook, Chapter, Scene
    ServerStore.kt         DataStore-backed server list + active selection
    ApiClient.kt           OkHttp: GET /books, download /books/{id}/pack
    PackManager.kt         download + unzip + local pack registry
    BookPack.kt            reads book.db (client side of the pack contract)
  ui/
    AppNav.kt              nav graph: Library / Servers / Reader
    Library{Screen,ViewModel}.kt
    Servers{Screen,ViewModel}.kt
    Reader{Screen,ViewModel}.kt   manual reader + listening toggle
    theme/AppTheme.kt
```

The `book.db` columns read here mirror `server/app/bookpack.py`; keep them in sync.
