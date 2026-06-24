#!/usr/bin/env python
"""Django command-line entrypoint for the EVE trade GUI simulator."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eve_trade_simulator.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
