#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
import time

from goofish_auction_helper.runtime import (
    host_frida_version,
    load_config,
    resolve_adb,
    resolve_device,
    resolve_frida_exe,
    resolve_frida_server_bin,
    run_text,
)


def main() -> int:
    config = load_config(warn=not any(arg in {"-h", "--help"} for arg in sys.argv[1:]))
    parser = argparse.ArgumentParser(description="Start or restart device-side frida-server as root.")
    parser.add_argument("--adb", help="adb executable. Defaults: GOOFISH_ADB > config.toml [env].adb > PATH adb.")
    parser.add_argument("--device", help="adb serial/address. Defaults: GOOFISH_DEVICE > config.toml [env].device.")
    parser.add_argument("--frida-exe", help="host frida CLI path used to infer the frida-server version.")
    parser.add_argument("--frida-server-bin", help="device-side frida-server path.")
    args = parser.parse_args()

    adb_path = resolve_adb(args.adb, config)
    device = resolve_device(args.device, config, adb_path)
    frida_exe = resolve_frida_exe(args.frida_exe, config)
    server_bin = resolve_frida_server_bin(
        args.frida_server_bin,
        config,
        adb_path=adb_path,
        device=device,
        frida_exe=frida_exe,
    )

    version = host_frida_version(frida_exe)
    print(f"[frida] adb={adb_path}")
    print(f"[frida] device={device}")
    print(f"[frida] host_frida={version or 'unknown'}")
    print(f"[frida] device_bin={server_bin}")
    if "<arch>" in server_bin:
        print(
            "[frida] cannot infer device ABI. Connect the device first, or set "
            "GOOFISH_FRIDA_SERVER_BIN / config.toml [env].frida_server_bin.",
            file=sys.stderr,
        )
        return 2

    if ":" in device:
        print("[1/4] adb connect")
        print(run_text([adb_path, "connect", device]) or "(no output)")

    script = f"#!/system/bin/sh\nsetsid {server_bin} >/dev/null 2>&1 </dev/null &\n"
    escaped = script.replace("'", "'\\''")
    print("[2/4] write remote start script")
    print(
        run_text(
            [
                adb_path,
                "-s",
                device,
                "shell",
                f"printf '%s' '{escaped}' > /data/local/tmp/fs_start.sh; chmod 755 /data/local/tmp/fs_start.sh",
            ]
        )
    )

    print("[3/4] restart frida-server as root")
    print(run_text([adb_path, "-s", device, "shell", "pkill -f frida-server 2>/dev/null; su -c /data/local/tmp/fs_start.sh"]))
    time.sleep(2)

    print("[4/4] verify")
    out = run_text([adb_path, "-s", device, "shell", "ps -A | grep -i frida || echo NOT_RUNNING"])
    print(out)
    return 0 if "frida" in out.lower() and "not_running" not in out.lower() else 2


if __name__ == "__main__":
    raise SystemExit(main())
