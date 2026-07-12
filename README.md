# manifest-engine

Stories materializing as you listen.

While you listen to an audiobook, an Android app shows imagery that matches the
passage being narrated — visually consistent characters and locations across the
whole book.

## Components

- **[server/](server)** — pre-processing service (MacBook M5). Turns an EPUB into
  a `.bookpack`: scenes segmented from the text, generated consistent images, and
  a token index for narration matching. FastAPI + a plain-HTML upload UI. Runs
  fully offline by default (stub image backend + heuristic segmenter); plug in
  **Draw Things** (FLUX) and **Ollama** for real output.

- **[android/](android)** — companion app. Downloads a `.bookpack`, and (Phase 1)
  plays it back in **manual mode**: chapter picker + scene navigation with
  crossfaded images. Later phases add mic + on-device Whisper ASR to drive scenes
  automatically from Audible.

## Status

Phase 1 (skeleton + data contract) is implemented end to end: the server produces
`.bookpack`s and the app browses/downloads/navigates them. The audio-listening
(auto) path is stubbed behind a toggle. See the architecture plan for the full
roadmap.

Start with each component's README: [server/README.md](server/README.md),
[android/README.md](android/README.md).
