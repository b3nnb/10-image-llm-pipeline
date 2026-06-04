#!/usr/bin/env python3
"""
Friday Image Pipeline — Persona likeness replication with approval loop.

Usage:
  python pipeline.py generate --scene "Friday at her desk, coding, dramatic lighting"
  python pipeline.py generate --scene "Friday in a coffee shop, soft light" --model juggernaut
  python pipeline.py list
  python pipeline.py iterate --id <run-id> --feedback "make her hair darker"
  python pipeline.py approve --id <run-id>
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

# Friday's base character description — consistent across all scenes
FRIDAY_BASE_PROMPT = (
    "Friday, a young woman, AI assistant persona, sleek dark hair, intelligent eyes, "
    "professional but approachable, modern aesthetic, high quality, detailed face"
)

NEGATIVE_PROMPT = (
    "blurry, low quality, distorted face, deformed, ugly, bad anatomy, "
    "watermark, text, signature, duplicate, multiple people"
)

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


def build_workflow(positive: str, model_file: str, seed: int = -1, width: int = 1024, height: int = 1024, steps: int = 25) -> dict:
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
            "inputs": {"width": width, "height": height, "batch_size": 1}
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
                "text": NEGATIVE_PROMPT
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
    full_prompt = f"{FRIDAY_BASE_PROMPT}, {args.scene}"

    run_id = str(uuid.uuid4())[:8]
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"🎨 Generating image for scene: {args.scene}")
    print(f"   Model: {model_file}")
    print(f"   Run ID: {run_id}")
    print(f"   Prompt: {full_prompt[:120]}...")

    workflow = build_workflow(full_prompt, model_file, steps=args.steps)
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


def main():
    parser = argparse.ArgumentParser(description="Friday Image Pipeline")
    sub = parser.add_subparsers(dest="cmd")

    gen = sub.add_parser("generate", help="Generate images for a scene")
    gen.add_argument("--scene", required=True, help="Scene description for Friday")
    gen.add_argument("--model", default=DEFAULT_MODEL, choices=list(AVAILABLE_MODELS.keys()))
    gen.add_argument("--steps", type=int, default=25)
    gen.add_argument("--timeout", type=int, default=300)

    it = sub.add_parser("iterate", help="Iterate on an existing run with feedback")
    it.add_argument("--id", required=True, help="Run ID to iterate from")
    it.add_argument("--feedback", required=True, help="What to change")

    ap = sub.add_parser("approve", help="Mark a run as approved")
    ap.add_argument("--id", required=True)

    sub.add_parser("list", help="List all runs")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "generate":
        cmd_generate(args)
    elif args.cmd == "iterate":
        cmd_iterate(args)
    elif args.cmd == "approve":
        cmd_approve(args)
    elif args.cmd == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
