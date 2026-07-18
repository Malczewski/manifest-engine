"""Stage 4/6 — image generation behind a swappable interface.

`ImageGenerator` is the engine-agnostic seam the plan calls for: the pipeline
only knows this interface, so Draw Things can be swapped for ComfyUI (or the
offline stub) without touching anything else.

  * StubGenerator     — deterministic Pillow placeholder; no external service.
  * DrawThingsGenerator — Draw Things HTTP API (enable "API Server" in the app;
    it exposes an AUTOMATIC1111-compatible /sdapi/v1/txt2img + img2img).
  * GeminiImageGenerator — Google Gemini image models ("Nano Banana") via the
    generateContent REST API; hosted, no local GPU.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import textwrap
import time
from abc import ABC, abstractmethod

import httpx
from PIL import Image, ImageDraw

from ..config import settings
from ..log import get_logger

log = get_logger("imagegen")


class ImageGenerator(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        seed: int,
        init_image: bytes | None = None,
        ref_images: list[bytes] | None = None,
    ) -> bytes:
        """Return PNG bytes for the prompt.

        init_image  — optional img2img base (previous scene in same location).
        ref_images  — optional consistency references (bible entries).
        """


class StubGenerator(ImageGenerator):
    """Offline placeholder. Deterministic in (prompt, seed) so reruns are stable
    and the rest of the pipeline can be exercised with no models installed."""

    def generate(
        self,
        prompt: str,
        seed: int,
        init_image: bytes | None = None,
        ref_images: list[bytes] | None = None,
    ) -> bytes:
        w, h = settings.image_width, settings.image_height
        digest = hashlib.sha256(f"{seed}:{prompt}".encode()).digest()
        bg = (digest[0], digest[1], digest[2])
        fg = (255 - bg[0], 255 - bg[1], 255 - bg[2])
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        wrapped = textwrap.fill(prompt, width=max(20, w // 12))[:600]
        draw.multiline_text((16, 16), wrapped, fill=fg, spacing=4)
        draw.text((16, h - 24), f"seed={seed} refs={len(ref_images or [])}", fill=fg)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class DrawThingsGenerator(ImageGenerator):
    """Talks to a running Draw Things API server.

    NOTE: verify the endpoint/params against your Draw Things build the first
    time — the app follows the AUTOMATIC1111 shape but options can differ.
    """

    def __init__(self) -> None:
        # trust_env=False: localhost image server must not be proxied.
        self._client = httpx.Client(
            base_url=settings.drawthings_url, timeout=600, trust_env=False
        )

    def _payload(self, prompt: str, seed: int) -> dict:
        payload = {
            "prompt": prompt,
            "negative_prompt": settings.negative_prompt,
            "steps": settings.image_steps,
            "width": settings.image_width,
            "height": settings.image_height,
            "seed": seed,
        }
        # Draw Things selects the checkpoint via the "model" field (the filename
        # from /sdapi/v1/options). Blank => whatever is loaded in the app.
        if settings.image_model:
            payload["model"] = settings.image_model
        return payload

    def generate(
        self,
        prompt: str,
        seed: int,
        init_image: bytes | None = None,
        ref_images: list[bytes] | None = None,
    ) -> bytes:
        payload = self._payload(prompt, seed)
        if init_image is not None:
            # img2img: continuity from the previous scene in the same location.
            payload["init_images"] = [base64.b64encode(init_image).decode()]
            payload["denoising_strength"] = settings.img2img_denoise
            endpoint = "/sdapi/v1/img2img"
        else:
            endpoint = "/sdapi/v1/txt2img"
        # TODO(consistency): map ref_images to Draw Things image-prompt / control
        # units once the exact control payload for the installed build is confirmed.
        resp = self._client.post(endpoint, json=payload)
        resp.raise_for_status()
        images = resp.json().get("images", [])
        if not images:
            raise RuntimeError("Draw Things returned no image")
        return base64.b64decode(images[0])


class ImageRequestError(RuntimeError):
    """A non-retriable image failure: a 4xx bad request (bad config/prompt) or a
    content/policy refusal. The pipeline should not retry these."""


class ImagePromptRejected(RuntimeError):
    """The model wouldn't render THIS prompt but suggested rephrasing. Retrying the
    same text won't help; the caller can rewrite it via the LLM and try again.

    policy=True means it was refused for a CONTENT-POLICY reason (e.g. explicit /
    prohibited content) — the rewrite must SANITIZE it (remove nudity, sexual or
    graphic content) into a compliant illustration. policy=False is a plain "couldn't
    render, try rephrasing" (IMAGE_OTHER) and just needs a cleaner rewording."""

    def __init__(self, message: str, policy: bool = False):
        super().__init__(message)
        self.policy = policy


# Words in a no-image response (finish message or the model's own text) that mean it
# refused on content grounds — route these to a sanitizing rewrite.
_POLICY_WORDS = (
    "prohibited", "explicit", "sexual", "nsfw", "naked", "nude", "nudity", "porn",
    "can't generate", "cannot generate", "won't generate", "can't create",
    "cannot create", "not able to generate", "unable to generate",
)


# finishReason / blockReason values that mean a content/policy refusal — retrying
# won't help (e.g. depicting a real person or a minor). Everything else (transient
# empty response, server hiccup) is worth a retry.
_CONTENT_BLOCK_TOKENS = (
    "SAFETY", "PROHIBITED", "BLOCKLIST", "RECITATION", "IMAGE_SAFETY", "SPII", "PERSON",
)


def _is_content_block(reason: str) -> bool:
    r = (reason or "").upper()
    return any(tok in r for tok in _CONTENT_BLOCK_TOKENS)


def _error_detail(resp) -> str:
    """Extract Gemini's structured error message from a failed response body."""
    try:
        err = resp.json().get("error", {})
        msg = err.get("message") or ""
        status = err.get("status") or ""
        return f"{status}: {msg}"[:500] if (msg or status) else resp.text[:500]
    except Exception:
        return (resp.text or "")[:500]


def _dump(payload: dict) -> str:
    """JSON dump of a response for logging, with any inline image bytes elided."""
    def scrub(obj):
        if isinstance(obj, dict):
            return {k: ("<image bytes>" if k in ("data",) else scrub(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [scrub(v) for v in obj]
        return obj
    try:
        return json.dumps(scrub(payload), default=str)[:1200]
    except Exception:
        return str(payload)[:1200]


class GeminiImageGenerator(ImageGenerator):
    """Google Gemini image models ("Nano Banana") via generateContent.

    Each call renders ONE image for one prompt (scenes have distinct prompts, so
    there is no single-call batch — the pipeline loops per scene, which is also
    what makes it resumable). The model has no seed/negative-prompt knob, so
    consistency rests on the verbatim identity descriptor in the prompt; the
    negative prompt is folded in as an "Avoid:" clause. init/ref images are passed
    as inline image parts for continuity when enabled.
    """

    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            log.warning("IMAGE_BACKEND=gemini but GEMINI_API_KEY is not set; images will FAIL")
        self._client = httpx.Client(timeout=180, trust_env=False)

    def _parts(self, prompt: str, init_image: bytes | None, ref_images: list[bytes] | None):
        text = prompt
        if settings.negative_prompt:
            text = f"{prompt}\nAvoid: {settings.negative_prompt}"
        parts: list[dict] = [{"text": text}]
        for img in (ref_images or []) + ([init_image] if init_image else []):
            parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(img).decode()}})
        return parts

    def generate(
        self,
        prompt: str,
        seed: int,
        init_image: bytes | None = None,
        ref_images: list[bytes] | None = None,
    ) -> bytes:
        url = self._ENDPOINT.format(model=settings.gemini_image_model)
        # Ask for TEXT too: when the model declines to draw it returns a text reason
        # instead of an image, which we surface rather than failing blankly.
        gen_config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config: dict = {}
        if settings.gemini_aspect:
            image_config["aspectRatio"] = settings.gemini_aspect
        if settings.gemini_image_size:  # blank for gemini-2.5-flash-image (fixed size)
            image_config["imageSize"] = settings.gemini_image_size
        if image_config:
            gen_config["imageConfig"] = image_config
        body = {
            "contents": [{"parts": self._parts(prompt, init_image, ref_images)}],
            "generationConfig": gen_config,
        }
        last_reason = "empty response"
        for attempt in range(4):
            resp = self._client.post(url, params={"key": settings.gemini_api_key}, json=body)
            if resp.status_code == 429 and attempt < 3:
                wait = 10 * (2 ** attempt)
                log.info("Gemini image quota hit; retrying in %ds", wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                # Surface the API's error detail (NOT the URL — it carries the key).
                detail = _error_detail(resp)
                if resp.status_code < 500:  # bad request: config/prompt — won't fix on retry
                    raise ImageRequestError(f"Gemini image API {resp.status_code}: {detail}")
                last_reason = f"HTTP {resp.status_code}: {detail}"  # 5xx: transient
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise RuntimeError(f"Gemini image API {resp.status_code}: {detail}")
            data = resp.json()
            png = self._maybe_png(data)
            if png is not None:
                return png
            # No image. Log the FULL response (minus any image bytes) so the actual
            # cause — safety ratings, finishReason/message, refusal text — is visible.
            last_reason = self._no_image_reason(data)
            log.warning("Gemini returned no image (%s) [attempt %d/4]; response=%s",
                        last_reason, attempt + 1, _dump(data))
            kind = self._classify_no_image(data)
            # policy   -> sanitize (drop explicit content, keep the scene)
            # rephrase -> reword (model declined/couldn't render, or replied in text)
            # transient-> retry the same prompt
            if kind == "policy":
                raise ImagePromptRejected(f"Gemini refused the prompt ({last_reason})", policy=True)
            if kind == "rephrase":
                raise ImagePromptRejected(f"Gemini would not render the prompt ({last_reason})", policy=False)
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"Gemini returned no image ({last_reason})")

    @staticmethod
    def _classify_no_image(payload: dict) -> str:
        """Classify a no-image 200 response using the FULL body (finish reason/message,
        safety ratings, and any text the model returned): 'policy' (needs sanitizing),
        'rephrase' (reword), or 'transient' (retry the same prompt)."""
        fb = payload.get("promptFeedback") or {}
        texts: list[str] = []
        finish = ""
        blocked_safety = False
        for cand in payload.get("candidates") or []:
            finish = cand.get("finishReason") or finish
            if cand.get("finishMessage"):
                texts.append(cand["finishMessage"])
            if any(r.get("blocked") for r in cand.get("safetyRatings", [])):
                blocked_safety = True
            for part in cand.get("content", {}).get("parts", []):
                if part.get("text"):
                    texts.append(part["text"])
        blob = " ".join([fb.get("blockReason") or "", finish, *texts]).lower()
        if (_is_content_block(f"{finish} {fb.get('blockReason', '')}")
                or blocked_safety or any(w in blob for w in _POLICY_WORDS)):
            return "policy"
        if "rephras" in blob or "could not generate" in blob or "unable to" in blob:
            return "rephrase"
        # The model replied with text instead of drawing -> reword and try again.
        if any(t.strip() for t in texts):
            return "rephrase"
        return "transient"

    @staticmethod
    def _maybe_png(payload: dict) -> bytes | None:
        for cand in payload.get("candidates") or []:
            for part in cand.get("content", {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    raw = base64.b64decode(blob["data"])
                    # Normalize to PNG. Gemini renders at a size TIER (0.5K/1K/…),
                    # not exact pixels, so cap the result to IMAGE_WIDTH×IMAGE_HEIGHT
                    # (aspect preserved) — keeps packs small and matching Draw Things.
                    img = Image.open(io.BytesIO(raw)).convert("RGB")
                    if settings.image_width and settings.image_height:
                        img.thumbnail((settings.image_width, settings.image_height))
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
        return None

    @staticmethod
    def _no_image_reason(payload: dict) -> str:
        """Best-effort explanation for a no-image response: prompt block reason,
        candidate finishReason (+ message + blocked safety categories), or the
        model's own refusal text."""
        fb = payload.get("promptFeedback") or {}
        if fb.get("blockReason"):
            return f"blocked: {fb['blockReason']}"
        for cand in payload.get("candidates") or []:
            fr = cand.get("finishReason")
            msg = cand.get("finishMessage") or ""
            blocked = [r.get("category") for r in cand.get("safetyRatings", []) if r.get("blocked")]
            if fr and fr not in ("STOP", "MAX_TOKENS"):
                bits = [f"finishReason={fr}"]
                if blocked:
                    bits.append(f"blockedCategories={blocked}")
                if msg:
                    bits.append(f"msg={msg[:160]!r}")
                return " ".join(bits)
            for part in cand.get("content", {}).get("parts", []):
                if part.get("text"):
                    return f"model said: {part['text'][:160]!r}"
        return "empty response"


def get_generator() -> ImageGenerator:
    if settings.image_backend == "drawthings":
        return DrawThingsGenerator()
    if settings.image_backend == "gemini":
        return GeminiImageGenerator()
    return StubGenerator()
