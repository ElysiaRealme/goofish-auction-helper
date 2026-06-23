#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
CONFIG_EXAMPLE_PATH = ROOT / "config.example.toml"
PACKAGE = "com.taobao.idlefish"
DEFAULT_NETWORK_DEVICE = "127.0.0.1:7555"


def load_config(*, warn: bool = False) -> dict:
    try:
        with CONFIG_PATH.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        if warn and CONFIG_EXAMPLE_PATH.exists():
            print(
                "[config] config.toml not found; using safe built-in defaults. "
                "Copy config.example.toml to config.toml to customize.",
                file=sys.stderr,
            )
        return {}
    except Exception as exc:
        if warn:
            print(f"[config] config.toml parse error ({exc}); using safe built-in defaults.", file=sys.stderr)
        return {}


def config_source_label() -> str:
    return "config.toml" if CONFIG_PATH.exists() else "safe built-in defaults"


def _env_config(config: dict | None) -> dict:
    config = config or {}
    env = config.get("env", {})
    return env if isinstance(env, dict) else {}


def _first_value(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def run_text(cmd: list[str], *, timeout: float = 8.0) -> str:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def resolve_frida_exe(cli_value: str | None = None, config: dict | None = None) -> str:
    env = _env_config(config)
    local = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("frida.exe" if os.name == "nt" else "frida")
    return _first_value(cli_value, os.environ.get("GOOFISH_FRIDA"), env.get("frida_exe"), local) or "frida"


def resolve_adb(cli_value: str | None = None, config: dict | None = None) -> str:
    env = _env_config(config)
    configured = _first_value(cli_value, os.environ.get("GOOFISH_ADB"), env.get("adb"), config.get("adb") if config else None)
    if configured:
        return configured
    return shutil.which("adb") or "adb"


def _connected_devices(adb_path: str) -> list[str]:
    output = run_text([adb_path, "devices"])
    devices: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def resolve_device(cli_value: str | None = None, config: dict | None = None, adb_path: str | None = None) -> str:
    env = _env_config(config)
    configured = _first_value(
        cli_value,
        os.environ.get("GOOFISH_DEVICE"),
        env.get("device"),
        config.get("device") if config else None,
    )
    if configured:
        return configured
    if adb_path:
        devices = _connected_devices(adb_path)
        if len(devices) == 1:
            return devices[0]
    return DEFAULT_NETWORK_DEVICE


def host_frida_version(frida_exe: str) -> str:
    output = run_text([frida_exe, "--version"])
    return output.splitlines()[0].strip() if output else ""


def device_abi(adb_path: str, device: str) -> str:
    output = run_text([adb_path, "-s", device, "shell", "getprop ro.product.cpu.abi"])
    for line in output.splitlines():
        value = line.strip()
        if value:
            return value
    return ""


def frida_arch_suffix(abi: str) -> str:
    abi = (abi or "").strip().lower()
    if abi in {"x86", "i686"}:
        return "x86"
    if abi in {"x86_64", "amd64"}:
        return "x86_64"
    if abi in {"arm64-v8a", "aarch64", "arm64"}:
        return "arm64"
    if abi.startswith("armeabi") or abi in {"arm", "armv7"}:
        return "arm"
    return ""


def resolve_frida_server_bin(
    cli_value: str | None = None,
    config: dict | None = None,
    *,
    adb_path: str | None = None,
    device: str | None = None,
    frida_exe: str | None = None,
) -> str:
    env = _env_config(config)
    configured = _first_value(
        cli_value,
        os.environ.get("GOOFISH_FRIDA_SERVER_BIN"),
        env.get("frida_server_bin"),
        config.get("frida_server_bin") if config else None,
    )
    if configured:
        return configured

    version = host_frida_version(frida_exe) if frida_exe else ""
    abi = device_abi(adb_path, device) if adb_path and device else ""
    arch = frida_arch_suffix(abi)
    if version and arch:
        return f"/data/local/tmp/frida-server-{version}-{arch}"
    if version:
        return f"/data/local/tmp/frida-server-{version}-<arch>"
    return "/data/local/tmp/frida-server"


def version_from_frida_server_bin(path: str) -> str:
    match = re.search(r"frida-server-([0-9]+(?:\.[0-9]+){1,3})", path or "")
    return match.group(1) if match else ""
