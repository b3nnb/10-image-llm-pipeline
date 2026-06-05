"""
Friday Scene Presets — curated scene descriptions for the image pipeline.

Run: python pipeline.py batch --preset <pack>
"""

SCENE_PACKS = {
    "work": [
        "Friday at her desk, multiple monitors with code, dramatic blue neon lighting, cyberpunk vibes, shallow depth of field",
        "Friday in a coffee shop, working on laptop, warm golden afternoon light, busy background bokeh",
        "Friday at a standing desk, headphones around neck, casual hoodie, soft morning light",
        "Friday in a server room, glowing rack lights casting colorful reflections, serious expression",
    ],
    "casual": [
        "Friday reading a book in an armchair, cozy home library, warm lamp light, relaxed pose",
        "Friday outside in a park, natural sunlight, casual outfit, slight smile",
        "Friday at a rooftop bar, city skyline at dusk behind her, social atmosphere",
        "Friday in a kitchen, making coffee, morning light, relaxed home setting",
    ],
    "professional": [
        "Friday in a boardroom, presenting to a team, confident posture, modern office background",
        "Friday in a startup office, surrounded by whiteboards with diagrams, collaborative energy",
        "Friday at a conference, badge visible, networking event, professional attire",
        "Friday recording a podcast, microphone in front, studio lighting, engaged expression",
    ],
    "creative": [
        "Friday at a drawing tablet, digital art on screen, creative studio, purple and orange accent lighting",
        "Friday in a music studio, headphones on, mixing board, focused expression",
        "Friday with a camera around her neck, urban photography setting, golden hour",
        "Friday designing on a large display, 3D model visible on screen, modern design studio",
    ],
    # LoRA training optimised — close-up portraits with diverse lighting & expressions.
    # These scenes are face-forward and detail-rich, ideal for identity training.
    "portrait": [
        "Friday portrait, close-up, looking directly at camera, soft studio lighting, neutral background, high detail",
        "Friday portrait, three-quarter view, natural window light, slight smile, sharp focus on face",
        "Friday portrait, dramatic side lighting, dark background, cinematic, professional headshot style",
        "Friday portrait, warm golden hour light, outdoor setting, hair catching light, candid expression",
        "Friday portrait, cool blue ambient light, tech environment, focused expression, shallow depth of field",
        "Friday portrait, overhead soft-box lighting, white background, clean professional look, minimal",
        "Friday portrait, rim light from behind, moody atmosphere, looking slightly off-camera, thoughtful",
        "Friday portrait, bright natural daylight, smiling, casual setting, relaxed and approachable",
    ],
}

def list_packs():
    for pack, scenes in SCENE_PACKS.items():
        print(f"\n[{pack}] — {len(scenes)} scenes:")
        for i, s in enumerate(scenes, 1):
            print(f"  {i}. {s[:80]}{'...' if len(s) > 80 else ''}")

def get_pack(name: str) -> list[str]:
    if name not in SCENE_PACKS:
        raise ValueError(f"Unknown pack '{name}'. Available: {', '.join(SCENE_PACKS.keys())}")
    return SCENE_PACKS[name]

if __name__ == "__main__":
    list_packs()
