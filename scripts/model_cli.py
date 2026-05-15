from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.llm.local_engine import LocalLLMEngine


def cmd_list(args):
    engine = LocalLLMEngine()
    status = engine.get_status_sync()
    print(f"Engine: {status.engine}")
    print(f"Model:  {status.model}")
    print(f"Loaded: {status.model_loaded}")
    print(f"Running: {status.running}")
    if status.vram_used_mb > 0:
        print(f"VRAM:   {status.vram_used_mb:.0f} MB")
    if status.error:
        print(f"Error:  {status.error}")


def cmd_download(args):
    model_id = args.model
    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading model: {model_id}")
    print(f"Target directory:  {target_dir}")

    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(
            repo_id=model_id,
            local_dir=str(target_dir / model_id.replace("/", "_")),
            local_dir_use_symlinks=False,
        )
        print(f"Downloaded to: {path}")
    except ImportError:
        print("Error: huggingface_hub not installed. Run: pip install huggingface_hub")
        sys.exit(1)
    except Exception as e:
        print(f"Download failed: {e}")
        sys.exit(1)


def cmd_switch(args):
    engine_type = args.engine
    model_path = args.model_path

    print(f"Switching to engine: {engine_type}")
    print(f"Model path: {model_path}")

    config_file = Path("configs/local_llm.json")
    config_file.parent.mkdir(parents=True, exist_ok=True)

    config = {}
    if config_file.exists():
        config = json.loads(config_file.read_text())

    config["engine"] = engine_type
    config["model_path"] = model_path
    config_file.write_text(json.dumps(config, indent=2))

    print(f"Configuration saved to {config_file}")
    print("Restart the application to apply changes.")


def cmd_status(args):
    engine = LocalLLMEngine()
    try:
        import asyncio
        status = asyncio.get_event_loop().run_until_complete(engine.get_status())
    except RuntimeError:
        import asyncio
        status = asyncio.run(engine.get_status())

    print(json.dumps({
        "engine": status.engine,
        "model": status.model,
        "model_loaded": status.model_loaded,
        "running": status.running,
        "vram_used_mb": status.vram_used_mb,
        "error": status.error,
    }, indent=2))


def cmd_benchmark(args):
    engine = LocalLLMEngine()
    import asyncio
    import time

    async def run_bench():
        if not engine._model_loaded:
            print("Model not loaded. Please start the engine first.")
            return

        messages = [{"role": "user", "content": "Hello, how are you?"}]
        warmup = await engine.generate(messages)

        times = []
        for i in range(args.iterations):
            start = time.perf_counter()
            result = await engine.generate(messages)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            print(f"  Iteration {i + 1}: {result.tokens_per_second:.1f} tok/s ({elapsed:.2f}s)")

        avg_tps = sum(r.tokens_per_second for r in [warmup]) / max(1, 1)
        if times:
            avg_time = sum(times) / len(times)
            print(f"\nAverage: {avg_time:.2f}s per request")

    asyncio.run(run_bench())


def main():
    parser = argparse.ArgumentParser(
        prog="auto-agent-model",
        description="Auto-Agent Workflow - Local Model Management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    list_parser = subparsers.add_parser("list", help="List available models and status")
    list_parser.set_defaults(func=cmd_list)

    download_parser = subparsers.add_parser("download", help="Download a model from HuggingFace")
    download_parser.add_argument("model", help="HuggingFace model ID (e.g., 'Qwen/Qwen2.5-7B-Instruct')")
    download_parser.add_argument("--target-dir", default="models", help="Target directory for downloaded models")
    download_parser.set_defaults(func=cmd_download)

    switch_parser = subparsers.add_parser("switch", help="Switch active engine and model")
    switch_parser.add_argument("--engine", choices=["mlx", "vllm", "auto"], default="auto", help="Inference engine")
    switch_parser.add_argument("--model-path", default="", help="Path to model weights")
    switch_parser.set_defaults(func=cmd_switch)

    status_parser = subparsers.add_parser("status", help="Show current engine status (JSON)")
    status_parser.set_defaults(func=cmd_status)

    bench_parser = subparsers.add_parser("benchmark", help="Run inference benchmark")
    bench_parser.add_argument("--iterations", type=int, default=3, help="Number of benchmark iterations")
    bench_parser.set_defaults(func=cmd_benchmark)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
