#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("usage: main.py {tui,fire,sniper,frida} [options]")
        print()
        print("commands:")
        print("  tui     Open the guided terminal UI")
        print("  fire    Run one-shot bid fire / dry-run mode")
        print("  sniper  Run poll-based sniper / simulate mode")
        print("  frida   Start or restart device-side frida-server")
        return 0 if len(sys.argv) >= 2 else 2

    command, rest = sys.argv[1], sys.argv[2:]
    if command == "tui":
        from goofish_auction_helper.tui import main as tui_main

        sys.argv = ["tui.py", *rest]
        return tui_main()
    if command == "fire":
        from goofish_auction_helper.fire import main as fire_main

        sys.argv = ["main.py fire", *rest]
        return fire_main()
    if command == "sniper":
        from goofish_auction_helper.sniper import main as sniper_main

        sys.argv = ["main.py sniper", *rest]
        return sniper_main()
    if command == "frida":
        from goofish_auction_helper.frida_server import main as frida_main

        sys.argv = ["main.py frida", *rest]
        return frida_main()
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
