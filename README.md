# Friday Image Pipeline

Persona likeness replication — Friday character in different situations/scenes.
Iterative generation + approval loop via ComfyUI.

## Prerequisites

- ComfyUI running at `http://127.0.0.1:8188` (default)
- SDXL checkpoint installed (juggernaut-xl-v9 or animagine-xl-4.0)
- Python 3.9+

## Usage

```bash
# Generate a new image
python pipeline.py generate --scene "Friday at her desk, coding, dramatic blue lighting"

# Generate with a specific model
python pipeline.py generate --scene "Friday in a coffee shop, warm afternoon light" --model animagine

# List all runs
python pipeline.py list

# Iterate on an existing run with feedback
python pipeline.py iterate --id abc12345 --feedback "make the background more bokeh, warmer tones"

# Approve a run (copies to output/approved/)
python pipeline.py approve --id abc12345
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI API endpoint |
| `PIPELINE_OUTPUT_DIR` | `./output` | Where to save images + state |

## How it Works

1. **Generate** — builds a ComfyUI workflow with Friday's base character prompt + your scene, queues it, waits for completion, saves images locally
2. **Iterate** — takes a run ID + feedback text, appends feedback to the scene description, generates a new version, links parent/child for traceability
3. **Approve** — marks a run as approved and copies images to `output/approved/`

## State

All run metadata is stored in `output/state.json` — version history, prompts, seeds, approval status.

## Models

| Key | Model File | Notes |
|---|---|---|
| `juggernaut` | juggernaut-xl-v9.safetensors | Photorealistic, good for portraits |
| `animagine` | animagine-xl-4.0.safetensors | Anime/stylized |

## Friday Base Prompt

The pipeline always prepends a consistent character description before your scene:

> "Friday, a young woman, AI assistant persona, sleek dark hair, intelligent eyes, professional but approachable, modern aesthetic, high quality, detailed face"

This ensures consistency across scenes. Adjust `FRIDAY_BASE_PROMPT` in `pipeline.py` to refine the character definition.
