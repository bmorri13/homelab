#!/usr/bin/env python3
"""Generate all infrastructure diagrams."""

import os
import sys

# Ensure the diagrams directory is on the path
sys.path.insert(0, os.path.dirname(__file__))

from config import OUTPUT_DIR


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Generating infrastructure diagrams...")
    print(f"Output directory: {OUTPUT_DIR}\n")

    import infrastructure_overview
    infrastructure_overview.generate()

    import data_logging_flow
    data_logging_flow.generate()

    import gitops_ci_pipeline
    gitops_ci_pipeline.generate()

    print("\nAll diagrams generated successfully.")


if __name__ == "__main__":
    main()
