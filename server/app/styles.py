"""Named visual-style presets for image generation.

The selected preset's text becomes the style anchor prepended to every scene
prompt (and passed to the prompt composer). The user's free-text base prompt is
appended after it, so both work together. Default is an illustrated digital-
painting look — a book visualization, not a photo and not a flat cartoon.
"""

from __future__ import annotations

# key -> (display label, style prompt text)
STYLES: dict[str, tuple[str, str]] = {
    "digital_painting": (
        "Digital painting (illustration)",
        "digital painting, illustrated book art, detailed concept-art style, "
        "painterly digital rendering, soft shading",
    ),
    "painterly": (
        "Painterly / oil",
        "painterly oil-painting illustration, textured visible brushwork, rich color",
    ),
    "graphic_novel": (
        "Graphic novel",
        "graphic-novel illustration, bold inked linework, dramatic comic shading",
    ),
    "watercolor": (
        "Watercolor",
        "watercolor illustration, soft color washes, delicate linework",
    ),
    "storybook": (
        "Storybook",
        "storybook illustration, warm hand-drawn look, gentle colors",
    ),
    "cinematic": (
        "Cinematic (semi-real)",
        "cinematic semi-realistic illustration, filmic lighting, detailed",
    ),
    "photoreal": (
        "Photorealistic",
        "photorealistic, live-action film still, realistic detail",
    ),
    "anime": (
        "Anime",
        "anime illustration, cel shading, clean linework",
    ),
}

DEFAULT = "digital_painting"


def style_text(key: str) -> str:
    return STYLES.get(key, STYLES[DEFAULT])[1]
