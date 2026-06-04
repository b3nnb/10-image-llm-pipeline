#!/usr/bin/env python3
"""
Friday Image Pipeline — Persona likeness replication with approval loop.

Usage:
  python pipeline.py generate --scene "Friday at her desk, coding, dramatic lighting"
  python pipeline.py generate --scene "Friday in a coffee shop, soft light" --model juggernaut
  python pipeline.py generate --scene "..." --seed 1234567  # pin seed for reproducibility
  python pipeline.py generate --scene "..." --faceid         # use FaceID reference image (if set)
  python pipeline.py list
  python pipeline.py iterate --id <run-id> --feedback "make her hair darker"
  python pipeline.py approve --id <run-id>
  python pipeline.py rerun --id <run-id>             # exact rerun with same seed
  python pipeline.py batch --scenes scene1.txt       # batch from file (one scene per line)
  python pipeline.py batch --preset work             # batch from built-in scene pack
  python pipeline.py compare --ids id1 id2 id3       # print side-by-side metadata
  python pipeline.py export --id <run-id>            # copy run to named folder
  python pipeline.py set-reference --image path/to/face.png  # set FaceID reference image
"""

import argparse
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.parse
import shutil
from pathlib import Path
from datetime import datetime

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
OUTPUT_DIR = Path(os.environ.get("PIPELINE_OUTPUT_DIR", "./output"))
STATE_FILE = OUTPUT_DIR / "state.json"
CONFIG_FILE = OUTPUT_DIR / "persona_config.json"

# Defaults — overridden by persona_config.json if it exists
_DEFAULT_BASE_PROMPT = (
    "Friday, a young woman, AI assistant persona, sleek dark hair, intelligent eyes, "
    "professional but approachable, modern aesthetic, high quality, detailed face"
)
_DEFAULT_NEGATIVE_PROMPT = (
    "blurry, low quality, distorted face, deformed, ugly, bad anatomy, "
    "watermark, text, signature, duplicate, multiple people"
)


def load_persona_config() -> dict:
    """Load persona config from file, falling back to defaults."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        cfg.setdefault("base_prompt", _DEFAULT_BASE_PROMPT)
        cfg.setdefault("negative_prompt", _DEFAULT_NEGATIVE_PROMPT)
        return cfg
    return {"base_prompt": _DEFAULT_BASE_PROMPT, "negative_prompt": _DEFAULT_NEGATIVE_PROMPT}


def save_persona_config(cfg: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_base_prompt() -> str:
    return load_persona_config()["base_prompt"]


def get_negative_prompt() -> str:
    return load_persona_config()["negative_prompt"]


# Module-level aliases for backward compat (loaded once — use get_base_prompt() for live values)
FRIDAY_BASE_PROMPT = _DEFAULT_BASE_PROMPT
NEGATIVE_PROMPT = _DEFAULT_NEGATIVE_PROMPT

AVAILABLE_MODELS = {
    "animagine": "animagine-xl-4.0.safetensors",
    "juggernaut": "juggernaut-xl-v9.safetensors",
}

DEFAULT_MODEL = "juggernaut"


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"runs": {}}


def save_state(state: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_workflow(positive: str, model_file: str, seed: int = -1, width: int = 1024, height: int = 1024, steps: int = 25, batch_size: int = 1) -> dict:
    """Build a ComfyUI API workflow JSON."""
    if seed == -1:
        import random
        seed = random.randint(0, 2**32 - 1)

    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
            }
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model_file}
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_size}
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["4", 1],
                "text": positive
            }
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["4", 1],
                "text": get_negative_prompt()
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            }
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": "friday_pipeline"
            }
        }
    }


def upload_reference_image(image_path: str) -> str:
    """Upload a local image file to ComfyUI's input folder.

    Returns the filename as registered in ComfyUI (for use with LoadImage node).
    """
    import mimetypes
    import io
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"Reference image not found: {image_path}")

    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    boundary = "----FridayPipelineBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{p.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    # ComfyUI returns {"name": "filename.png", "subfolder": "", "type": "input"}
    return result["name"]


def get_reference_image_path() -> "str | None":
    """Return the local path to the FaceID reference image, or None if not set."""
    cfg = load_persona_config()
    ref_path = cfg.get("reference_image")
    if not ref_path:
        return None
    p = Path(ref_path)
    if not p.exists():
        print(f"⚠️  Reference image not found: {ref_path}")
        return None
    return str(p)


def build_faceid_workflow(positive: str, model_file: str, reference_image_filename: str, seed: int = -1, width: int = 1024, height: int = 1024, steps: int = 25, faceid_weight: float = 0.85) -> dict:
    """Build a ComfyUI workflow with IPAdapterFaceID for consistent face reference.

    Args:
        reference_image_filename: filename as returned by upload_reference_image()
            (the name ComfyUI registered in its input folder)

    Requires:
    - ComfyUI_IPAdapter_plus custom node
    - ip-adapter-faceid-plusv2_sdxl.bin in models/ipadapter/
    - ip-adapter-faceid-plusv2_sdxl_lora.safetensors in models/loras/
    """
    import random
    if seed == -1:
        seed = random.randint(0, 2**32 - 1)

    return {
        # Checkpoint loader
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model_file}
        },
        # Load reference image by filename (uploaded to ComfyUI input folder)
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": reference_image_filename}
        },
        # Unified IPAdapter loader for FaceID
        "3": {
            "class_type": "IPAdapterUnifiedLoaderFaceID",
            "inputs": {
                "model": ["1", 0],
                "preset": "FACEID PLUS V2",
                "lora_strength": 0.6,
                "provider": "CPU",
            }
        },
        # IPAdapterFaceID node
        "4": {
            "class_type": "IPAdapterFaceID",
            "inputs": {
                "model": ["3", 0],
                "ipadapter": ["3", 1],
                "image": ["2", 0],
                "weight": faceid_weight,
                "weight_faceidv2": 1.0,
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "V only",
            }
        },
        # CLIP encoders
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": positive}
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": get_negative_prompt()}
        },
        # Latent
        "7": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1}
        },
        # KSampler
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["5", 0],
                "negative": ["6", 0],
                "latent_image": ["7", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
            }
        },
        # Decode + Save
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["1", 2]}
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": "friday_faceid"}
        }
    }


def get_reference_image_b64() -> "str | None":
    """Load the FaceID reference image as base64, or None if not set.

    Deprecated: use get_reference_image_path() + upload_reference_image() instead.
    Kept for backward compat.
    """
    import base64
    ref_path = get_reference_image_path()
    if not ref_path:
        return None
    with open(ref_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def cmd_set_reference(args):
    """Set the FaceID reference image path in persona config."""
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        print(f"❌ Image not found: {image_path}")
        sys.exit(1)

    # Copy image into output dir for portability
    ref_dir = OUTPUT_DIR / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    dest = ref_dir / image_path.name
    shutil.copy2(image_path, dest)

    cfg = load_persona_config()
    cfg["reference_image"] = str(dest)
    save_persona_config(cfg)

    print(f"✅ Reference image set: {dest}")
    print(f"   Use --faceid flag on generate/batch to enable face-locked generation.")
    print(f"\n💡 Tip: Pick your favourite approved run, find its image in output/<run-id>/,")
    print(f"        then run: python pipeline.py set-reference --image <path>")


def queue_prompt(workflow: dict) -> str:
    """Queue a workflow and return the prompt_id."""
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY_URL}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["prompt_id"]


def wait_for_completion(prompt_id: str, timeout: int = 300) -> dict:
    """Poll until the prompt is done, return the history entry."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}")
            with urllib.request.urlopen(req) as resp:
                history = json.loads(resp.read())
            if prompt_id in history:
                return history[prompt_id]
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Prompt {prompt_id} did not complete within {timeout}s")


def fetch_image(filename: str, subfolder: str, dest_path: Path):
    """Download generated image from ComfyUI and save to dest_path."""
    params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
    req = urllib.request.Request(f"{COMFY_URL}/view?{params}")
    with urllib.request.urlopen(req) as resp:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(resp.read())


def cmd_generate(args):
    """Generate a new image for the given scene."""
    state = load_state()

    model_file = AVAILABLE_MODELS.get(args.model, AVAILABLE_MODELS[DEFAULT_MODEL])
    full_prompt = f"{get_base_prompt()}, {args.scene}"

    # FaceID mode: use reference image for consistent face
    use_faceid = getattr(args, "faceid", False)
    ref_filename = None
    if use_faceid:
        ref_path = get_reference_image_path()
        if not ref_path:
            print("⚠️  FaceID requested but no reference image set. Run:")
            print("    python pipeline.py set-reference --image <path/to/face.png>")
            print("   Falling back to standard generation.")
            use_faceid = False
        else:
            try:
                print("   Uploading reference image to ComfyUI...")
                ref_filename = upload_reference_image(ref_path)
                print(f"   Reference uploaded as: {ref_filename}")
            except Exception as e:
                print(f"⚠️  Failed to upload reference image: {e}")
                print("   Falling back to standard generation.")
                use_faceid = False

    run_id = str(uuid.uuid4())[:8]
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"🎨 Generating image for scene: {args.scene}")
    print(f"   Model: {model_file}")
    print(f"   Run ID: {run_id}")
    print(f"   Prompt: {full_prompt[:120]}...")

    if use_faceid and ref_filename:
        print("   Mode: FaceID (reference image locked)")
        workflow = build_faceid_workflow(full_prompt, model_file, ref_filename, seed=getattr(args, 'seed', -1), steps=args.steps)
        seed = workflow["8"]["inputs"]["seed"]
    else:
        workflow = build_workflow(full_prompt, model_file, seed=getattr(args, 'seed', -1), steps=args.steps)
        seed = workflow["3"]["inputs"]["seed"]

    # Save the prompt info
    run_meta = {
        "id": run_id,
        "scene": args.scene,
        "prompt": full_prompt,
        "model": args.model,
        "model_file": model_file,
        "seed": seed,
        "steps": args.steps,
        "status": "generating",
        "version": 1,
        "parent_id": None,
        "faceid": use_faceid and bool(ref_filename),
        "created_at": datetime.utcnow().isoformat(),
        "images": [],
        "approved": False,
        "feedback_history": []
    }
    state["runs"][run_id] = run_meta
    save_state(state)

    print("   Queuing prompt...")
    prompt_id = queue_prompt(workflow)
    print(f"   Queued: {prompt_id}")
    print("   Waiting for completion...")

    try:
        history = wait_for_completion(prompt_id, timeout=args.timeout)
    except TimeoutError as e:
        print(f"❌ {e}")
        run_meta["status"] = "timeout"
        save_state(state)
        return

    # Collect images
    images_saved = []
    outputs = history.get("outputs", {})
    for node_id, node_output in outputs.items():
        for img in node_output.get("images", []):
            img_filename = img["filename"]
            img_subfolder = img.get("subfolder", "")
            dest = run_dir / img_filename
            try:
                fetch_image(img_filename, img_subfolder, dest)
                images_saved.append(str(dest))
                print(f"✅ Saved: {dest}")
            except Exception as e:
                print(f"⚠️  Could not fetch {img_filename}: {e}")

    run_meta["status"] = "done"
    run_meta["images"] = images_saved
    save_state(state)

    print(f"\n✅ Done! Run ID: {run_id}")
    print(f"   Images: {images_saved}")
    print(f"\nNext steps:")
    print(f"  python pipeline.py approve --id {run_id}        # mark as approved")
    print(f"  python pipeline.py iterate --id {run_id} --feedback 'change the lighting'")


def cmd_iterate(args):
    """Generate a new version based on feedback."""
    state = load_state()
    parent = state["runs"].get(args.id)
    if not parent:
        print(f"❌ Run not found: {args.id}")
        sys.exit(1)

    # Build on parent's prompt with feedback incorporated
    new_scene = f"{parent['scene']}, {args.feedback}"
    full_prompt = f"{FRIDAY_BASE_PROMPT}, {new_scene}"

    run_id = str(uuid.uuid4())[:8]
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    model_file = AVAILABLE_MODELS.get(parent["model"], AVAILABLE_MODELS[DEFAULT_MODEL])

    print(f"🔄 Iterating on run {args.id}")
    print(f"   Feedback: {args.feedback}")
    print(f"   New prompt: {full_prompt[:120]}...")

    workflow = build_workflow(full_prompt, model_file, steps=parent["steps"])
    seed = workflow["3"]["inputs"]["seed"]

    run_meta = {
        "id": run_id,
        "scene": new_scene,
        "prompt": full_prompt,
        "model": parent["model"],
        "model_file": model_file,
        "seed": seed,
        "steps": parent["steps"],
        "status": "generating",
        "version": parent["version"] + 1,
        "parent_id": args.id,
        "created_at": datetime.utcnow().isoformat(),
        "images": [],
        "approved": False,
        "feedback_history": parent["feedback_history"] + [args.feedback]
    }
    state["runs"][run_id] = run_meta
    save_state(state)

    prompt_id = queue_prompt(workflow)
    print(f"   Queued: {prompt_id}, waiting...")

    history = wait_for_completion(prompt_id, timeout=300)
    images_saved = []
    for node_id, node_output in history.get("outputs", {}).items():
        for img in node_output.get("images", []):
            dest = run_dir / img["filename"]
            try:
                fetch_image(img["filename"], img.get("subfolder", ""), dest)
                images_saved.append(str(dest))
                print(f"✅ Saved: {dest}")
            except Exception as e:
                print(f"⚠️  {e}")

    run_meta["status"] = "done"
    run_meta["images"] = images_saved
    save_state(state)

    print(f"\n✅ Done! New run ID: {run_id} (v{run_meta['version']})")
    print(f"  python pipeline.py approve --id {run_id}")
    print(f"  python pipeline.py iterate --id {run_id} --feedback 'more adjustments'")


def cmd_approve(args):
    state = load_state()
    run = state["runs"].get(args.id)
    if not run:
        print(f"❌ Run not found: {args.id}")
        sys.exit(1)
    run["approved"] = True
    save_state(state)
    print(f"✅ Run {args.id} marked as approved.")
    if run["images"]:
        # Copy approved images to an approved/ folder
        approved_dir = OUTPUT_DIR / "approved"
        approved_dir.mkdir(exist_ok=True)
        for img_path in run["images"]:
            src = Path(img_path)
            if src.exists():
                dst = approved_dir / f"{args.id}_v{run['version']}_{src.name}"
                shutil.copy2(src, dst)
                print(f"   Copied to: {dst}")


def cmd_list(args):
    state = load_state()
    runs = state.get("runs", {})
    if not runs:
        print("No runs yet. Use: python pipeline.py generate --scene 'your scene'")
        return

    print(f"{'ID':<10} {'V':<3} {'Status':<12} {'Approved':<9} {'Scene'}")
    print("-" * 80)
    for run_id, run in sorted(runs.items(), key=lambda x: x[1]["created_at"]):
        approved = "✅" if run["approved"] else "  "
        scene_short = run["scene"][:45] + "..." if len(run["scene"]) > 45 else run["scene"]
        print(f"{run_id:<10} {run['version']:<3} {run['status']:<12} {approved:<9} {scene_short}")


def cmd_rerun(args):
    """Rerun a past run with the exact same seed (reproducible)."""
    state = load_state()
    parent = state["runs"].get(args.id)
    if not parent:
        print(f"❌ Run not found: {args.id}")
        sys.exit(1)

    # Reconstruct args-like object
    class RerunArgs:
        scene = parent["scene"]
        model = parent["model"]
        steps = parent["steps"]
        timeout = 300
        seed = parent["seed"]

    print(f"🔁 Rerunning {args.id} with seed {parent['seed']}")
    cmd_generate(RerunArgs())


def cmd_batch(args):
    """Batch generate from a file of scenes or a preset pack."""
    from scenes import get_pack, SCENE_PACKS

    if args.preset:
        try:
            scenes = get_pack(args.preset)
        except ValueError as e:
            print(f"❌ {e}")
            sys.exit(1)
        print(f"📦 Batch: {len(scenes)} scenes from preset '{args.preset}'")
    elif args.scenes_file:
        scenes_path = Path(args.scenes_file)
        if not scenes_path.exists():
            print(f"❌ File not found: {args.scenes_file}")
            sys.exit(1)
        scenes = [line.strip() for line in scenes_path.read_text().splitlines() if line.strip()]
        print(f"📄 Batch: {len(scenes)} scenes from {args.scenes_file}")
    else:
        print("❌ Provide --preset <pack> or --scenes-file <file.txt>")
        sys.exit(1)

    if args.dry_run:
        print("\n[DRY RUN] Would generate:")
        for i, scene in enumerate(scenes, 1):
            print(f"  {i}. {scene[:80]}")
        return

    results = []
    for i, scene in enumerate(scenes, 1):
        print(f"\n[{i}/{len(scenes)}] {scene[:60]}...")

        bargs = argparse.Namespace(
            scene=scene,
            model=args.model,
            steps=args.steps,
            timeout=args.timeout,
            seed=-1,
        )
        try:
            cmd_generate(bargs)
            results.append(("ok", scene))
        except Exception as e:
            print(f"   ⚠️ Failed: {e}")
            results.append(("error", scene))

    print(f"\n📊 Batch complete: {sum(1 for r, _ in results if r == 'ok')}/{len(scenes)} succeeded")


def cmd_compare(args):
    """Print side-by-side metadata for multiple runs."""
    state = load_state()
    ids = args.ids
    runs = [state["runs"].get(rid) for rid in ids]
    missing = [ids[i] for i, r in enumerate(runs) if r is None]
    if missing:
        print(f"❌ Run(s) not found: {', '.join(missing)}")
        sys.exit(1)

    print(f"\n{'Field':<20}", end="")
    for rid in ids:
        print(f"  {rid:<18}", end="")
    print()
    print("-" * (20 + len(ids) * 20))

    fields = ["version", "status", "approved", "model", "seed", "steps", "created_at"]
    for field in fields:
        print(f"{field:<20}", end="")
        for run in runs:
            val = str(run.get(field, ""))[:16]
            print(f"  {val:<18}", end="")
        print()

    print(f"\n{'scene':<20}")
    for rid, run in zip(ids, runs):
        print(f"  {rid}: {run['scene'][:80]}")


def cmd_export(args):
    """Copy a run's images to a named export folder."""
    state = load_state()
    run = state["runs"].get(args.id)
    if not run:
        print(f"❌ Run not found: {args.id}")
        sys.exit(1)

    name = args.name or f"export_{args.id}_v{run['version']}"
    export_dir = OUTPUT_DIR / "exports" / name
    export_dir.mkdir(parents=True, exist_ok=True)

    for img_path in run["images"]:
        src = Path(img_path)
        if src.exists():
            dst = export_dir / src.name
            shutil.copy2(src, dst)
            print(f"✅ {src.name} → {dst}")

    # Save metadata alongside
    meta_path = export_dir / "meta.json"
    with open(meta_path, "w") as f:
        json.dump(run, f, indent=2)
    print(f"📄 Metadata: {meta_path}")
    print(f"\nExported to: {export_dir}")


def cmd_stats(_args=None):
    """Print a stats summary of all pipeline runs."""
    state = load_state()
    runs = state.get("runs", {})

    if not runs:
        print("No runs yet. Generate some with: python pipeline.py generate --scene \"...\"")
        return

    total = len(runs)
    approved = [r for r in runs.values() if r.get("approved")]
    pending = [r for r in runs.values() if not r.get("approved") and r.get("status") == "done"]
    generating = [r for r in runs.values() if r.get("status") == "generating"]
    by_model: dict = {}
    by_scene: dict = {}
    for r in runs.values():
        m = r.get("model", "unknown")
        by_model[m] = by_model.get(m, 0) + 1
        scene = r.get("scene", "")[:60]
        by_scene[scene] = by_scene.get(scene, 0) + 1

    print(f"\n📊 Pipeline Stats\n")
    print(f"  Total runs:      {total}")
    print(f"  Approved:        {len(approved)}")
    print(f"  Pending review:  {len(pending)}")
    if generating:
        print(f"  Generating:      {len(generating)}")

    if by_model:
        print(f"\n  By model:")
        for model, count in sorted(by_model.items(), key=lambda x: -x[1]):
            print(f"    {model}: {count}")

    print(f"\n  Unique scenes:   {len(by_scene)}")

    # Top scenes by generation count
    top_scenes = sorted(by_scene.items(), key=lambda x: -x[1])[:5]
    if any(c > 1 for _, c in top_scenes):
        print(f"\n  Most iterated scenes:")
        for scene, count in top_scenes:
            if count > 1:
                print(f"    x{count}  {scene}...")

    # Latest runs
    sorted_runs = sorted(runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)[:5]
    print(f"\n  Recent runs:")
    for r in sorted_runs:
        ts = r.get("created_at", "")[:16].replace("T", " ")
        approved_mark = " ✅" if r.get("approved") else ""
        print(f"    {r.get('id', r.get('run_id', '?'))[:8]}  v{r.get('version', 1)}  {ts}{approved_mark}  {r.get('scene', '')[:55]}...")

    print()


def cmd_check(_args=None):
    """Health-check the full pipeline setup and report status."""
    import urllib.request as urequest
    import urllib.error
    ok = True

    def check(label: str, passed: bool, detail: str = ""):
        symbol = "✅" if passed else "❌"
        line = f"  {symbol}  {label}"
        if detail:
            line += f"  — {detail}"
        print(line)
        return passed

    print("\n🔍 Friday Image Pipeline — Health Check\n")

    # 1. ComfyUI reachability
    comfy_ok = False
    stats: dict = {}
    try:
        req = urequest.urlopen(f"{COMFY_URL}/system_stats", timeout=5)
        stats = json.loads(req.read())
        version = stats.get("system", {}).get("comfyui_version", "unknown")
        comfy_ok = check("ComfyUI reachable", True, f"v{version} @ {COMFY_URL}")
    except Exception as e:
        check("ComfyUI reachable", False, f"{COMFY_URL} — {e}")
        ok = False

    # 2. GPU info
    if comfy_ok:
        try:
            devices = stats.get("devices", [])
            if devices:
                d = devices[0]
                check("GPU detected", True, f"{d.get('name', '?')} — {d.get('vram_free', 0)//1024//1024}MB free / {d.get('vram_total', 0)//1024//1024}MB total")
        except Exception:
            pass

    # 3. Models installed in ComfyUI
    if comfy_ok:
        try:
            oi_req = urequest.urlopen(f"{COMFY_URL}/object_info/CheckpointLoaderSimple", timeout=5)
            oi = json.loads(oi_req.read())
            installed = oi.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [{}])[0]
            for key, filename in AVAILABLE_MODELS.items():
                found = filename in installed
                check(f"Model: {key} ({filename})", found)
                if not found:
                    ok = False
        except Exception as e:
            check("Model list", False, str(e))
            ok = False

    # 4. FaceID node availability (optional — just warn)
    if comfy_ok:
        try:
            faceid_req = urequest.urlopen(f"{COMFY_URL}/object_info/IPAdapterUnifiedLoaderFaceID", timeout=5)
            faceid_data = json.loads(faceid_req.read())
            has_faceid = bool(faceid_data)
            check("FaceID node (IPAdapterUnifiedLoaderFaceID)", has_faceid, "optional — needed for --faceid flag")
        except Exception:
            check("FaceID node (IPAdapterUnifiedLoaderFaceID)", False, "optional — install ComfyUI-IPAdapter-plus if needed")

    # 5. State file + run count
    state = load_state()
    runs = state.get("runs", {})
    approved = sum(1 for r in runs.values() if r.get("approved"))
    check("Output state", True, f"{len(runs)} total runs, {approved} approved")

    # 6. Output directory writable
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        test_file = OUTPUT_DIR / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        check("Output dir writable", True, str(OUTPUT_DIR))
    except Exception as e:
        check("Output dir writable", False, str(e))
        ok = False

    # 7. Persona config
    cfg = load_persona_config()
    base_len = len(cfg.get("base_prompt", ""))
    ref = cfg.get("reference_image")
    ref_status = ""
    if ref:
        ref_path = Path(ref)
        ref_status = f"FaceID ref: {ref_path.name} ({'found' if ref_path.exists() else 'MISSING'})"
    check("Persona config", base_len > 0, ref_status or f"base prompt: {base_len} chars")

    # 8. Web UI
    try:
        ui_req = urequest.urlopen("http://localhost:8765/api/state", timeout=3)
        ui_data = json.loads(ui_req.read())
        ui_runs = len(ui_data.get("runs", {}))
        check("Web UI (localhost:8765)", True, f"serving {ui_runs} runs")
    except Exception:
        check("Web UI (localhost:8765)", False, "not running — start with ./start-ui.sh")

    print()
    if ok:
        print("✅ All checks passed.\n")
    else:
        print("⚠️  Some checks failed — see above.\n")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Friday Image Pipeline")
    sub = parser.add_subparsers(dest="cmd")

    gen = sub.add_parser("generate", help="Generate images for a scene")
    gen.add_argument("--scene", required=True, help="Scene description for Friday")
    gen.add_argument("--model", default=DEFAULT_MODEL, choices=list(AVAILABLE_MODELS.keys()))
    gen.add_argument("--steps", type=int, default=25)
    gen.add_argument("--timeout", type=int, default=300)
    gen.add_argument("--seed", type=int, default=-1, help="Pin seed for reproducibility (-1 = random)")
    gen.add_argument("--faceid", action="store_true", default=False, help="Use FaceID reference for consistent likeness")

    it = sub.add_parser("iterate", help="Iterate on an existing run with feedback")
    it.add_argument("--id", required=True, help="Run ID to iterate from")
    it.add_argument("--feedback", required=True, help="What to change")

    ap = sub.add_parser("approve", help="Mark a run as approved")
    ap.add_argument("--id", required=True)

    sub.add_parser("list", help="List all runs")

    rr = sub.add_parser("rerun", help="Exact rerun with same seed")
    rr.add_argument("--id", required=True)

    bt = sub.add_parser("batch", help="Batch generate from scene pack or file")
    bt.add_argument("--preset", default=None, help="Scene pack name (work/casual/professional/creative)")
    bt.add_argument("--scenes-file", dest="scenes_file", default=None, help="Text file with one scene per line")
    bt.add_argument("--model", default=DEFAULT_MODEL, choices=list(AVAILABLE_MODELS.keys()))
    bt.add_argument("--steps", type=int, default=25)
    bt.add_argument("--timeout", type=int, default=300)
    bt.add_argument("--dry-run", dest="dry_run", action="store_true", help="Print scenes without generating")

    cmp = sub.add_parser("compare", help="Compare metadata for multiple runs")
    cmp.add_argument("--ids", nargs="+", required=True, help="Run IDs to compare")

    exp = sub.add_parser("export", help="Export a run's images to a named folder")
    exp.add_argument("--id", required=True)
    exp.add_argument("--name", default=None, help="Export folder name")

    gal = sub.add_parser("gallery", help="Generate an HTML gallery of all runs")
    gal.add_argument("--output", default="gallery.html", help="Output HTML file (default: gallery.html)")
    gal.add_argument("--embed", action="store_true", help="Embed images as base64 (self-contained file)")
    gal.add_argument("--serve", action="store_true", help="Serve gallery on http://localhost:8765")

    cfg_cmd = sub.add_parser("config", help="View or edit persona config (base/negative prompts)")
    cfg_cmd.add_argument("--set-base", dest="set_base", default=None, help="Set base prompt text")
    cfg_cmd.add_argument("--set-negative", dest="set_negative", default=None, help="Set negative prompt text")

    setref = sub.add_parser("set-reference", help="Set FaceID reference image for consistent likeness")
    setref.add_argument("--image", required=True, help="Path to reference face image (PNG/JPG)")

    sub.add_parser("check", help="Health-check: verify ComfyUI, models, GPU, Web UI, and pipeline state")
    sub.add_parser("stats", help="Show run statistics: total, approved, by model, recent activity")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "config":
        cfg = load_persona_config()
        if args.set_base:
            cfg["base_prompt"] = args.set_base
        if args.set_negative:
            cfg["negative_prompt"] = args.set_negative
        if args.set_base or args.set_negative:
            save_persona_config(cfg)
            print("✅ Persona config saved.")
        print(f"\n📝 Base prompt:\n  {cfg['base_prompt']}")
        print(f"\n🚫 Negative prompt:\n  {cfg['negative_prompt']}")
        # Show reference image status
        ref = cfg.get("reference_image")
        if ref:
            p = Path(ref)
            print(f"\n🖼️  FaceID reference: {ref} ({'✅ found' if p.exists() else '❌ missing'})")
        else:
            print(f"\n🖼️  FaceID reference: not set (use set-reference to enable face-locked generation)")
        return

    if args.cmd == "set-reference":
        cmd_set_reference(args)
        return

    if args.cmd == "generate":
        cmd_generate(args)
    elif args.cmd == "iterate":
        cmd_iterate(args)
    elif args.cmd == "approve":
        cmd_approve(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "rerun":
        cmd_rerun(args)
    elif args.cmd == "batch":
        cmd_batch(args)
    elif args.cmd == "compare":
        cmd_compare(args)
    elif args.cmd == "export":
        cmd_export(args)
    elif args.cmd == "gallery":
        import subprocess
        import sys
        extra = []
        if args.output != "gallery.html":
            extra += ["--output", args.output]
        if args.embed:
            extra.append("--embed")
        if args.serve:
            extra.append("--serve")
        subprocess.run([sys.executable, str(Path(__file__).parent / "gallery.py")] + extra)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()

