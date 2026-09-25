# Pulls the Ollama models Ultron needs (use --skip-vision to skip moondream).
import argparse
import shutil
import subprocess
import sys

MODELS = ["qwen2.5:1.5b-instruct", "nomic-embed-text", "qwen2.5-coder:3b", "moondream"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Ultron's Ollama models.")
    parser.add_argument("--skip-vision", action="store_true", help="skip the moondream vision model")
    args = parser.parse_args()

    if shutil.which("ollama") is None:
        print("ERROR: 'ollama' was not found on PATH. Install Ollama and open a NEW terminal, then retry.")
        return 1

    models = [m for m in MODELS if not (args.skip_vision and m == "moondream")]
    for model in models:
        print(f"==> ollama pull {model}")
        result = subprocess.run(["ollama", "pull", model])
        if result.returncode != 0:
            print(f"ERROR: failed to pull {model} (exit code {result.returncode}).")
            return result.returncode

    print("\n==> ollama list")
    subprocess.run(["ollama", "list"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
