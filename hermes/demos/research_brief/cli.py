"""CLI entrypoint for the Research Brief vertical-slice demo.

Usage:
    python3 -m hermes.demos.research_brief.cli "your research topic"
"""
from __future__ import annotations

import argparse
import asyncio
import json

from hermes.demos.research_brief.runner import run_research_brief


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Hermes Research Brief vertical-slice demo end to end."
    )
    parser.add_argument("topic", help="The research topic to investigate.")
    args = parser.parse_args()

    brief = asyncio.run(run_research_brief(args.topic))
    print(json.dumps(brief, indent=2, default=str))


if __name__ == "__main__":
    main()
