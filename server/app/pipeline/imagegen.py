"""Stage 4/6 — image generation behind a swappable interface.

`ImageGenerator` is the engine-agnostic seam the plan calls for: the pipeline
only knows this interface, so Draw Things can be swapped for ComfyUI (or the
offline stub) without touching anything else.

  * StubGenerator     — deterministic Pillow placeholder; no external service.
  * DrawThingsGenerator — Draw Things HTTP API (enable "API Server" in the app;
    it exposes an AUTOMATIC1111-compatible /sdapi/v1/txt2img + img2img).
"""

from __future__ import annotations

import base64
import hashlib
import io
import textwrap
from abc import ABC, abstractmethod

import httpx
from PIL import Image, ImageDraw

from ..config import settings


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


def get_generator() -> ImageGenerator:
    if settings.image_backend == "drawthings":
        return DrawThingsGenerator()
    return StubGenerator()
