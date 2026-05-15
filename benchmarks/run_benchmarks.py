from benchmarks.core_benchmarks import run_all_benchmarks

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Auto-Agent Workflow benchmarks")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON file path")
    parser.add_argument("--benchmark", "-b", type=str, default="all",
                        choices=["all", "pattern", "workflow", "grounding"],
                        help="Which benchmark to run")
    args = parser.parse_args()

    if args.benchmark == "all":
        result = run_all_benchmarks(output_path=args.output)
    else:
        from benchmarks.core_benchmarks import (
            GroundingBenchmark,
            PatternDetectionBenchmark,
            WorkflowGenerationBenchmark,
        )

        bench_map = {
            "pattern": PatternDetectionBenchmark,
            "workflow": WorkflowGenerationBenchmark,
            "grounding": GroundingBenchmark,
        }
        bench = bench_map[args.benchmark]()
        results = bench.run()
        result = {"results": [r.to_dict() for r in results]}

    import json
    print(json.dumps(result, indent=2, default=str))
