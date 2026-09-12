import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "resources" / "models"


def load_model_registry() -> dict[str, dict[str, str]]:
  import json

  registry_path = MODELS_DIR / "registry.json"
  if not registry_path.exists():
    raise RuntimeError(f"Model registry not found: {registry_path}")

  with open(registry_path, "r", encoding="utf-8") as f:
    return json.load(f)


def ensure_huggingface_hub() -> None:
  try:
    import huggingface_hub  # noqa: F401
  except ImportError:
    print(
      "huggingface_hub is not installed. Install it with:\n"
      "  pip install huggingface-hub",
      file=sys.stderr,
    )
    sys.exit(1)


def install_model(name: str) -> None:
  from huggingface_hub import snapshot_download

  models = load_model_registry()

  config = models[name]
  destination = MODELS_DIR / name
  destination.mkdir(parents=True, exist_ok=True)

  print(f"\nInstalling {name}")
  print(f"Repository : {config['repo']}")
  print(f"Size       : {config['size']}")
  print(f"Description: {config['description']}")
  print(f"Destination: {destination}")

  patterns = [config["file"]] if "file" in config else ["*.gguf"]
  snapshot_download(
    repo_id=config["repo"],
    local_dir=str(destination),
    allow_patterns=patterns,
  )

  gguf_files = list(destination.glob("*.gguf"))
  if not gguf_files:
    raise RuntimeError(f"No .gguf file found in {destination}")

  target = destination / "model.gguf"
  gguf_path = gguf_files[0]
  if gguf_path != target:
    if target.exists():
      target.unlink()
    gguf_path.rename(target)

  print(f"✓ {name} installed successfully.")


def main() -> None:
  models = load_model_registry()

  parser = argparse.ArgumentParser(
    prog="install_model",
    description="Install local LLM models into resources/models.",
  )
  parser.add_argument(
    "names",
    nargs="*",
    help="Model name(s) to install (use --list to see available).",
  )
  parser.add_argument(
    "--all",
    action="store_true",
    help="Install every model in the registry.",
  )
  parser.add_argument(
    "--list",
    action="store_true",
    help="List the model registry and exit.",
  )
  args = parser.parse_args()

  if args.list:
    for name, config in models.items():
      print(f"{name:28s} {config['size']:7s} {config['description']}")
    return

  if args.all:
    names = list(models.keys())
  elif args.names:
    names = args.names
  else:
    parser.print_usage()
    sys.exit(1)

  unknown = [name for name in names if name not in models]
  if unknown:
    parser.error(f"Unknown model(s): {', '.join(unknown)}")

  ensure_huggingface_hub()
  MODELS_DIR.mkdir(parents=True, exist_ok=True)

  for name in names:
    try:
      install_model(name)
    except Exception as exc:
      print(f"\n✗ Failed to install {name}: {exc}", file=sys.stderr)
      sys.exit(1)

  print("\nAll models installed.")
  print(f"Models directory: {MODELS_DIR}")


if __name__ == "__main__":
  main()
