from __future__ import annotations

import argparse

from okx_signal_system.config import project_paths
from okx_signal_system.data.quality import write_quality_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_15m_extended")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    output = args.output or str(project_paths().output_dir / "data_quality_report.csv")
    write_quality_report(output, dataset=args.dataset)


if __name__ == "__main__":
    main()
