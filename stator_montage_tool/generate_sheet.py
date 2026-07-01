#!/usr/bin/env python3
"""CLI: read a process CSV table and render the montage-process state sheet.

Usage:
    python3 -m stator_montage_tool.generate_sheet [csv_path] [out_path]

Defaults to processes_example.csv -> montage_process_sheet.png
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stator_montage_tool.states import load_processes_csv
from stator_montage_tool.sheet import render_sheet


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "processes_example.csv")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "montage_process_sheet.png")
    steps = load_processes_csv(csv_path)
    render_sheet(steps, out_path)
    print(f"{len(steps)} process steps -> {out_path}")


if __name__ == "__main__":
    main()
