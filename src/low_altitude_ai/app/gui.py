"""Launch the standalone PyQt6 monitoring and management shell."""

from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obstacle-gui")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Use the Qt offscreen backend for smoke tests.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Create and render one frame, then exit successfully.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    from low_altitude_ai.ui.qt import MainWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = MainWindow()
    window.show()
    if args.smoke_test:
        application.processEvents()
        window.close()
        return 0
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
