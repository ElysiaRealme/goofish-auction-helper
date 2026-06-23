#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from goofish_auction_helper.cli import main as cli_main
from goofish_auction_helper.tui import main as tui_main


def main() -> int:
    if len(sys.argv) > 1:
        return cli_main()
    return tui_main()


if __name__ == "__main__":
    raise SystemExit(main())
