# Friday Image Pipeline

Persona likeness replication — Friday character in different situations/scenes.
Iterative generation + approval loop via ComfyUI.

## Prerequisites

- ComfyUI running at `http://127.0.0.1:8188` (default)
- SDXL checkpoint installed (juggernaut-xl-v9 or animagine-xl-4.0)
- Python 3.9+

## Commands

```bash
# Generate a new image
python pipeline.py generate --scene "Friday at her desk, coding, dramatic blue lighting"

# Generate with a specific model and pinned seed
python pipeline.py generate --scene "Friday in a coffee shop, warm afternoon light" --model animagine --seed 12345

# List all runs
python pipeline.py list

# Run stats summary (counts, model breakdown, recent activity)
python pipeline.py stats

# Health-check the whole setup (ComfyUI, models, GPU, Web UI)
python pipeline.py check

# Interactive review of pending (unapproved) runs: see metadata, approve or skip
python pipeline.py review
python pipeline.py review --non-interactive   # list only, no prompts

# Iterate on an existing run with feedback
python pipeline.py iterate --id abc12345 --feedback "make the background more bokeh, warmer tones"

# Approve a run (copies to output/approved/)
python pipeline.py approve --id abc12345

# Rerun with exact same seed (reproducible)
python pipeline.py rerun --id abc12345

# Batch generate from a preset pack
python pipeline.py batch --preset work
python pipeline.py batch --preset casual --dry-run  # preview without generating

# Batch from a custom text file (one scene per line)
python pipeline.py batch --scenes-file my-scenes.txt

# Compare multiple runs side-by-side
python pipeline.py compare --ids id1 id2 id3

# Export a run to a named folder
python pipeline.py export --id abc12345 --name friday-hero

# 🗂️ Export approved images as a LoRA training dataset (kohya_ss format)
python pipeline.py lora-export
python pipeline.py lora-export --trigger friday --repeats 15 --out ~/lora-dataset
python pipeline.py lora-export --all   # include unapproved runs too

# 🖼️ Generate an HTML gallery of all runs (open in browser)
python pipeline.py gallery
python pipeline.py gallery --embed              # self-contained (images embedded as base64)
python pipeline.py gallery --serve              # serve on http://localhost:8765 with browser open
```

## Scene Presets

| Pack | Scenes |
|---|---|
| `work` | desk/code, coffee shop, standing desk, server room |
| `casual` | reading, park, rooftop bar, kitchen |
| `professional` | boardroom, startup office, conference, podcast |
| `creative` | drawing tablet, music studio, photography, design studio |

## LoRA Training Dataset Export

When you have enough approved images, use `lora-export` to package them into a
[kohya_ss](https://github.com/bmaltais/kohya_ss)-compatible dataset for training
a Friday Likeness LoRA (card #38):

```bash
# Export approved images only (default)
python pipeline.py lora-export

# Custom trigger word and training repeats
python pipeline.py lora-export --trigger friday --repeats 15 --out ~/friday-lora

# Include all done runs (not just approved)
python pipeline.py lora-export --all
```

Output structure:

```
lora_dataset/
  img/
    10_friday/          ← training images + per-image .txt captions
  log/
  model/
  dataset_config.toml  ← pass to kohya_ss --dataset_config
  training_config.toml ← SDXL LoRA hyperparams (fp16, AdamW8bit, 10 epochs)
  README.md            ← full training walkthrough
  manifest.json        ← export metadata
```

Captions are auto-generated from the scene description with the trigger word prepended:
`friday, at her desk coding, cyberpunk neon lighting`

The more approved images you accumulate, the better the LoRA will generalise.
Aim for **20–30 images** across varied scenes, lighting, and angles before training.

## Gallery Viewer

`gallery.py` generates a dark-themed HTML gallery showing all runs with:
- Thumbnails of generated images
- Approval / version badges
- One-click copy of `approve`, `iterate`, and `export` commands
- Filter by approved/unapproved/done
- `--embed` for a fully self-contained `.html` file (easy to share)
- `--serve` to launch a local server and open in browser

## Deployment

### Run manually

```bash
./start-ui.sh       # starts on http://localhost:8765
./start-ui.sh 9000  # custom port
```

### Auto-start with systemd (recommended)

Runs the UI on login, restarts on failure, waits for ComfyUI to be ready first.

```bash
./install-service.sh                                # install + enable
systemctl --user start friday-image-pipeline        # start now
systemctl --user status friday-image-pipeline       # check status
journalctl --user -u friday-image-pipeline -f       # tail logs
```

### Docker Compose

If you want the pipeline UI containerized (ComfyUI still runs on host):

```bash
docker compose up -d          # start
docker compose logs -f        # logs
docker compose down           # stop
```

ComfyUI must be accessible at port 8188 on the host. The compose file sets
`extra_hosts: host.docker.internal:host-gateway` so the container can reach it.

To point at a different ComfyUI:
```bash
COMFY_URL=http://192.168.1.10:8188 docker compose up -d
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
4. **Gallery** — reads `output/state.json` and renders a browsable HTML viewer

## State

All run metadata is stored in `output/state.json` — version history, prompts, seeds, approval status. Images live in `output/<run-id>/`.

## Models

| Key | Model File | Notes |
|---|---|---|
| `juggernaut` | juggernaut-xl-v9.safetensors | Photorealistic, good for portraits |
| `animagine` | animagine-xl-4.0.safetensors | Anime/stylized |

## Friday Base Prompt

The pipeline always prepends a consistent character description before your scene:

> "Friday, a young woman, AI assistant persona, sleek dark hair, intelligent eyes, professional but approachable, modern aesthetic, high quality, detailed face"

Adjust `FRIDAY_BASE_PROMPT` in `pipeline.py` to refine the character definition.
