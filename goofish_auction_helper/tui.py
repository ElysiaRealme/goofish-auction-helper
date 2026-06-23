#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from goofish_auction_helper.runtime import (
    CONFIG_EXAMPLE_PATH,
    CONFIG_PATH,
    ROOT,
    config_source_label,
    host_frida_version,
    is_packaged,
    load_config,
    resolve_adb,
    resolve_device,
    resolve_frida_exe,
    resolve_frida_server_bin,
    run_text,
)

COLORS = {
    "reset": "\033[0m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def color(text: str, name: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"{COLORS.get(name, '')}{text}{COLORS['reset']}"


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def log(level: str, message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_color = {
        "SUCCESS": "green",
        "INFO": "cyan",
        "WARN": "yellow",
        "ERROR": "red",
    }.get(level, "cyan")
    print(f"{color(now, 'green')} | {color('[goofish]', 'cyan')} | {color(level.ljust(7), level_color)} | {message}")


def pause() -> None:
    input("\n按 Enter 返回主菜单...")


def read_key() -> str:
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\x1b", "q", "Q"):
            return "quit"
        return ch
    ch = sys.stdin.read(1)
    if ch == "\n":
        return "enter"
    if ch in {"q", "Q", "\x1b"}:
        return "quit"
    return ch


def choose(title: str, options: list[tuple[str, str]]) -> str:
    if not sys.stdin.isatty():
        print(title)
        for idx, (label, _) in enumerate(options, 1):
            print(f"{idx}. {label}")
        raw = input("选择编号: ").strip()
        return options[max(0, min(len(options) - 1, int(raw or "1") - 1))][1]

    index = 0
    while True:
        clear()
        log("SUCCESS", f"配置来源: {config_source_label()}")
        log("INFO", "使用方向键选择，Enter 确认，Q/Esc 返回或退出")
        print()
        print(color(f"? {title} (Use arrow keys)", "bold"))
        for i, (label, _) in enumerate(options):
            marker = color(" » ", "green") if i == index else "   "
            print(f"{marker}{label}")
        key = read_key()
        if key == "up":
            index = (index - 1) % len(options)
        elif key == "down":
            index = (index + 1) % len(options)
        elif key == "enter":
            return options[index][1]
        elif key == "quit":
            return "back"


def ask(prompt: str, default: str = "", *, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print(color("该项必填。", "yellow"))


def ask_int(prompt: str, default: int | None = None, *, required: bool = False) -> int | None:
    while True:
        raw = ask(prompt, "" if default is None else str(default), required=required)
        if raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            print(color("请输入整数。", "yellow"))


def toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def confirm_exact(prompt: str, word: str) -> bool:
    print(color(prompt, "yellow"))
    value = input(f"输入 {word} 确认，其他任意输入取消: ").strip()
    return value == word


def run_command(args: list[str]) -> None:
    clear()
    cmd = [sys.executable, *args] if is_packaged() else [sys.executable, str(ROOT / "main.py"), *args]
    log("INFO", "即将执行:")
    print(" ".join(f'"{part}"' if " " in part else part for part in cmd))
    print()
    code = subprocess.call(cmd, cwd=ROOT)
    print()
    if code == 0:
        log("SUCCESS", f"命令结束: exit={code}")
    else:
        log("ERROR", f"命令失败: exit={code}")
    pause()


def environment_check() -> None:
    clear()
    config = load_config(warn=False)
    adb = resolve_adb(None, config)
    device = resolve_device(None, config, adb)
    frida = resolve_frida_exe(None, config)
    server = resolve_frida_server_bin(None, config, adb_path=adb, device=device, frida_exe=frida)

    log("INFO", "环境解析结果")
    print(f"config      : {config_source_label()}")
    print(f"adb         : {adb}")
    print(f"device      : {device}")
    print(f"frida CLI   : {frida}")
    print(f"frida-server: {server}")
    print()

    adb_exists = Path(adb).exists() or shutil.which(adb) is not None
    frida_exists = Path(frida).exists() or shutil.which(frida) is not None
    log("SUCCESS" if adb_exists else "WARN", f"adb {'可用' if adb_exists else '未找到'}")
    log("SUCCESS" if frida_exists else "WARN", f"frida CLI {'可用' if frida_exists else '未找到'}")
    if "<arch>" in server:
        log("WARN", "未能探测设备 ABI；请先连接设备，或在 config.toml / GOOFISH_FRIDA_SERVER_BIN 中指定 frida-server 路径。")
    if adb_exists:
        print("\nadb devices:")
        print(run_text([adb, "devices"]) or "(no output)")
    if frida_exists:
        print("\nfrida --version:")
        print(host_frida_version(frida) or "(no output)")
    pause()


def config_wizard() -> None:
    clear()
    log("INFO", "配置向导会写入本地 config.toml；该文件已被 .gitignore 忽略。")
    if CONFIG_PATH.exists() and not confirm_exact("检测到已有 config.toml，覆盖前请确认。", "OVERWRITE"):
        return

    adb = ask("adb 路径，留空则使用 PATH/自动发现")
    device = ask("设备 serial/address，留空则自动选择唯一 adb 设备")
    frida_bin = ask("设备端 frida-server 路径，留空则按版本和 ABI 自动推断")
    trigger = ask_int("狙击触发秒数 trigger_sec", 90) or 90
    jitter = ask_int("随机抖动秒数 jitter_sec", 5) or 5
    max_retries = ask_int("每个延时窗口最大重试 max_retries", 3) or 3
    cooldown = ask_int("出价冷却毫秒 cooldown_ms", 3000) or 3000

    env_lines = ["[env]"]
    if adb:
        env_lines.append(f'adb = "{toml_string(adb)}"')
    if device:
        env_lines.append(f'device = "{toml_string(device)}"')
    if frida_bin:
        env_lines.append(f'frida_server_bin = "{toml_string(frida_bin)}"')
    if len(env_lines) == 1:
        env_lines.append("# adb = \"adb\"")
        env_lines.append("# device = \"127.0.0.1:7555\"")
        env_lines.append("# frida_server_bin = \"/data/local/tmp/frida-server-<version>-<arch>\"")

    body = "\n".join(
        [
            "# Local config generated by tui.py. Do not commit this file.",
            "# Amounts are integers in fen/yuan*100.",
            "",
            *env_lines,
            "",
            f"trigger_sec = {trigger}",
            f"jitter_sec = {jitter}",
            "aggression = 1",
            f"max_retries = {max_retries}",
            f"cooldown_ms = {cooldown}",
            "outcome_ms = 4000",
            "",
            "# Safe default: simulate only.",
            "live = false",
            "# max_price = 4000000",
            "",
            "# Optional fixed auction ids. Leave commented to use the current app page.",
            "# auction = \"\"",
            "# item = \"\"",
            "# vendue = \"\"",
            "",
        ]
    )
    CONFIG_PATH.write_text(body, encoding="utf-8")
    log("SUCCESS", f"已写入 {CONFIG_PATH}")
    pause()


def fire_flow() -> None:
    action = choose(
        "单次出价",
        [
            ("只读检查 currentPrice，不出价", "dry"),
            ("真实单次出价，需要二次确认", "live"),
            ("返回主菜单", "back"),
        ],
    )
    if action in {"back", "dry"}:
        if action == "dry":
            run_command(["fire", "--dry-run"])
        return

    clear()
    price = ask_int("真实出价金额 price，单位 fen/yuan*100", required=True)
    auction = ask("auctionId 可选，留空自动读取当前页")
    item = ask("itemId 可选，留空自动读取当前页")
    vendue = ask("vendueId 可选，留空自动读取当前页")
    if not confirm_exact("这会发起真实、具约束力的单次出价。", "FIRE"):
        return
    args = ["fire", "--price", str(price)]
    if auction:
        args += ["--auction", auction]
    if item:
        args += ["--item", item]
    if vendue:
        args += ["--vendue", vendue]
    run_command(args)


def sniper_flow() -> None:
    action = choose(
        "自动狙击",
        [
            ("模拟运行，不出价", "simulate"),
            ("真实自动出价，需要价格上限和二次确认", "live"),
            ("返回主菜单", "back"),
        ],
    )
    if action == "back":
        return

    clear()
    trigger = ask_int("trigger_sec，留空使用配置/默认", None)
    max_runtime = ask_int("max-runtime-sec，留空不限时", None)
    args = ["sniper", "--simulate" if action == "simulate" else "--live"]
    if trigger is not None:
        args += ["--trigger-sec", str(trigger)]
    if max_runtime is not None:
        args += ["--max-runtime-sec", str(max_runtime)]

    if action == "live":
        max_price = ask_int("max-price 价格上限，单位 fen/yuan*100", required=True)
        args += ["--max-price", str(max_price)]
        if not confirm_exact("这会在条件满足时自动发起真实出价。", "LIVE"):
            return

    run_command(args)


def frida_flow() -> None:
    clear()
    server = ask("设备端 frida-server 路径，留空按 config/版本/ABI 推断")
    args = ["frida"]
    if server:
        args += ["--frida-server-bin", server]
    run_command(args)


def about() -> None:
    clear()
    print(
        textwrap.dedent(
            """
            Goofish Auction Helper TUI

            推荐流程:
              1. 配置向导写入本地 config.toml
              2. 环境检查确认 adb / frida CLI / 设备
              3. 启动 frida-server
              4. 单次出价先 dry-run，能读 currentPrice 后再考虑 live
              5. sniper 先 simulate，再使用 --live + max-price
            """
        ).strip()
    )
    pause()


def main() -> int:
    while True:
        choice = choose(
            "主菜单",
            [
                ("环境检查", "check"),
                ("配置向导", "config"),
                ("启动 / 重启 frida-server", "frida"),
                ("单次出价 fire", "fire"),
                ("自动狙击 sniper", "sniper"),
                ("说明", "about"),
                ("退出", "exit"),
            ],
        )
        if choice in {"exit", "back"}:
            clear()
            log("INFO", "退出")
            return 0
        if choice == "check":
            environment_check()
        elif choice == "config":
            config_wizard()
        elif choice == "frida":
            frida_flow()
        elif choice == "fire":
            fire_flow()
        elif choice == "sniper":
            sniper_flow()
        elif choice == "about":
            about()


if __name__ == "__main__":
    raise SystemExit(main())
