# Goofish Auction Helper

[简体中文](README.md) | English

> Risk notice: This project dynamically instruments a live, logged-in Goofish app. `--price` and `--live` place real binding bids, may trigger account risk controls, may violate platform ToS, and may affect credit if a won auction is not paid. Use only with your own account and only when you understand and accept the consequences. This repository does not include the Goofish APK, account credentials, cookies, tokens, or signing keys.

## Positioning

This is a Frida-based Goofish auction bidding tool. It does not reimplement request signing and does not store account state. Instead, it injects into the user's own running, logged-in Goofish app while it is open on the target auction page, then reuses the app's native MTOP signing path.

Background:

- Are you still repeatedly tapping because the minimum bid increment is tiny, only for another bidder's refreshed price to force you to tap again?
- Are you still trapped in final-two-minute extension rounds, staying up late because the auction keeps stretching?
- `goofish-auction-helper` is intended for Goofish auction users who want either one-shot target-price bidding or unattended automatic extension bidding.

The project provides two modes:

- One-shot bid mode: reads the current auction context and sends one bid at the specified price, suitable when you want to bid to your target price in one action.
- Sniper mode: continuously listens to auction polling state, then places one increment when the account is behind and the configured remaining-time window is reached, suitable for unattended extensions or rank guarding.

Runtime prerequisites:

- An Android emulator or device is required. MuMu is recommended; this project is primarily adapted around the MuMu environment, and other environments are not guaranteed to be compatible.
- You must manually download and deploy a device-side frida-server matching both the host `frida` CLI version and the device CPU architecture. See [FRIDA_SERVER.md](FRIDA_SERVER.md) and the configuration section below.

Key parameter quick notes:

- `max_price`: hard ceiling for real bid prices. To prevent opponents from driving the price beyond what you are willing to accept, real mode must set a maximum affordable amount.
- `trigger_sec`: when server remaining time is less than or equal to this value and the account is behind, the tool triggers one bid increment. Because Goofish extends the auction by 5 minutes when a bid is placed inside the final 2 minutes, this value is recommended to stay within 2 minutes to avoid frequent bidding. If you want to follow immediately after another bidder bids, set it around 5 minutes for a persistent leading-position behavior. The recommended value is about `90` seconds; observed runtime latency can be around `±10s`, so this is a safer default.

The repository uses [main.py](main.py) as the only primary entry point, while source code lives under the `goofish_auction_helper/` package:

- `tui`: arrow-key TUI for configuration, checks, frida-server startup, dry-run, and simulate/live flows.
- `fire`: one-shot bid fire, or `--dry-run` for read-only auction inspection.
- `sniper`: continuous `bid.get` polling, then automatic bidding only when the server says the user is behind and the auction is inside the configured endgame window.

Recommended repository name: `goofish-auction-helper`.

## Core Mechanism

The Goofish auction page continuously polls `mtop.idle.vendue.itemdetail.bid.get`. Each poll is a complete `ApiBusiness` object that already carries the app account's real signing context and callback. The tool hooks `MtopSend.execute(IMtopBusiness)`, captures one `bid.get` poll object, converts it into a `mtop.idle.vendue.itemdetail.bid.price` bid request, and lets the native app MTOP SDK send it.

`sniper` does not depend on the unstable `bid.price` response hook. It reads the following server-authoritative state from later `bid.get` polls:

- `myBidStatusDTO.statusDesc` is `领先`: the account is leading.
- `myBidStatusDTO.statusDesc` is `落后`: bidding is allowed only after other guards pass.
- `UNKNOWN`: never bid, to avoid raising the user's own price.
- Remaining time uses server `serverTime` and `bidEndTime`; price uses server `nextBidPrice` or the configured increment strategy.

## Requirements

| Dependency | Requirement |
|---|---|
| Android runtime | Rooted emulator or device, Goofish logged in and open on the target auction page |
| adb | Able to connect to the target device; provided by `GOOFISH_ADB`, `config.toml`, or PATH |
| frida-server | Running as root on the device; version must match the host `frida` CLI |
| Python | 3.11+ for stdlib `tomllib` |
| uv | Synchronizes `.venv` and runs commands |

Device-side frida-server binaries are not committed. Use `uv run frida --version` and `adb shell getprop ro.product.cpu.abi`, then download the matching version and architecture from [Frida Releases](https://github.com/frida/frida/releases). See [FRIDA_SERVER.md](FRIDA_SERVER.md) for details.

## Installation

```bat
uv sync
```

Traditional venv:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Conda:

```bat
conda env create -f environment.yml
conda activate goofish-auction-helper
```

## Configuration

Copy the safe example:

```bat
copy config.example.toml config.toml
```

Environment resolution order is: explicit CLI argument > environment variable > `config.toml` > auto-discovery > safe fallback.

| Setting | Environment Variable | `config.toml` Field | Notes |
|---|---|---|---|
| adb path | `GOOFISH_ADB` | `[env].adb` | Falls back to `shutil.which("adb")` |
| Device serial/address | `GOOFISH_DEVICE` | `[env].device` | Auto-selects the only connected adb device when possible |
| frida CLI | `GOOFISH_FRIDA` | `[env].frida_exe` | Defaults to the local uv virtualenv |
| Device frida-server | `GOOFISH_FRIDA_SERVER_BIN` | `[env].frida_server_bin` | Inferred from host frida version and device ABI when unset |

`config.toml` is ignored by Git. Do not commit a personal config containing `live = true`, real price limits, device addresses, or local paths.

### `config.toml` Parameters

Environment connection parameters:

| Parameter | Type | Default Behavior | Description |
|---|---|---|---|
| `[env].adb` | string | Reads `GOOFISH_ADB`, then discovers `adb` on PATH | adb executable path; can be an absolute path or simply `adb` |
| `[env].device` | string | Reads `GOOFISH_DEVICE`, then tries to select the only connected device | adb device serial/address, such as an emulator port or USB device serial |
| `[env].frida_exe` | string | Reads `GOOFISH_FRIDA`, then uses the `frida` CLI from the uv virtualenv | Host-side Frida CLI path |
| `[env].frida_server_bin` | string | Reads `GOOFISH_FRIDA_SERVER_BIN`, then infers from host Frida version and device ABI | Device-side frida-server path; must match the binary pushed to the device |

Sniper trigger and bid parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `trigger_sec` | integer seconds | `90` | Enters bid decision when server remaining time is less than or equal to `trigger_sec + random jitter` and the account is behind; about `90` seconds is recommended and usually should stay under 2 minutes |
| `jitter_sec` | integer seconds | `5` | Adds random jitter within `[-jitter_sec, +jitter_sec]` to avoid a fixed trigger timestamp |
| `aggression` | integer | `1` | Bid increment multiplier; `1` uses the server-provided `nextBidPrice`, higher values jump by more increments |
| `step` | integer, fen | unset | Manually overrides the bid increment; when unset, the tool uses server `nextBidPrice` / `marginPrice` |

Retry and pacing parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_retries` | integer | `3` | Maximum bid attempts per extension window |
| `cooldown_ms` | integer milliseconds | `3000` | Cooldown between two bid attempts |
| `outcome_ms` | integer milliseconds | `4000` | Timeout for confirming a bid outcome through later `bid.get` polls |

Safety and target-selection parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `live` | boolean | `false` | `false` runs simulation only; `true` allows real automatic bidding |
| `max_price` | integer, fen | unset | Hard price ceiling for real bidding; required with `--live` to prevent unlimited follow-up bids beyond an acceptable amount |
| `auction` | string | unset | Optional fixed `auctionId`; when unset, the current app page is used |
| `item` | string | unset | Optional fixed `itemId`; when unset, the current app page is used |
| `vendue` | string | unset | Optional fixed `vendueId`; when unset, the current app page is used |

## Usage

Start with the TUI:

```bat
uv run python main.py tui
```

Safe read-only check:

```bat
uv run python main.py fire --dry-run
```

One-shot real bid:

```bat
uv run python main.py fire --price <price_in_fen>
```

Start or restart device-side frida-server:

```bat
uv run python main.py frida --frida-server-bin /data/local/tmp/frida-server-<version>-<arch>
```

Sniper simulate mode, no bids:

```bat
uv run python main.py sniper --simulate
```

Live sniper requires both `--live` and a hard price ceiling:

```bat
uv run python main.py sniper --live --max-price <max_price_in_fen>
```

## Layout

| Path | Purpose |
|---|---|
| `main.py` | Primary entry point for `tui`, `fire`, `sniper`, and `frida` |
| `goofish_auction_helper/cli.py` | CLI dispatcher |
| `goofish_auction_helper/tui.py` | Arrow-key TUI |
| `goofish_auction_helper/fire.py` | One-shot bid fire / read-only check core with embedded Frida JS |
| `goofish_auction_helper/sniper.py` | Automatic sniper core, preserving the tri-state decision logic |
| `goofish_auction_helper/runtime.py` | Shared adb, device, frida CLI, and frida-server resolution |
| `goofish_auction_helper/frida_server.py` | Root start/restart helper for device-side frida-server |
| `goofish_auction_helper/hooks/` | Canonical Frida hook reference scripts |
| `goofish_auction_helper/tools/recon_bid_get.py` | `bid.get` response reconnaissance tool |
| `config.example.toml` | Safe example configuration |
| `FRIDA_SERVER.md` | frida-server download, push, and version matching notes |

## Known Limits

- The target app must be running, logged in, and open on the target auction page; this is not a headless client.
- frida-server must run as root or injection into a non-debuggable app will fail.
- Host `frida` CLI and device-side frida-server versions must match.
- The Frida 17 Python binding path is not reliable for Java instrumentation here, so the core flow drives the `frida` CLI.
- Goofish app updates may change class names, fields, or MTOP wrappers; use `goofish_auction_helper/hooks/mtop_focus_hook.js` and `goofish_auction_helper/tools/recon_bid_get.py` to rediscover hooks.
- The `bid.price` response hook is unstable for poll-convert requests, so `sniper` treats later `bid.get` status as the source of truth.

## Verification

```bat
uv lock --check
uv run python -B -m compileall -q main.py goofish_auction_helper
```

Real runtime validation should start with `uv run python main.py fire --dry-run`. Only consider live commands after `currentPrice` is visible.

## Git Boundary

Commit only source code, example configuration, README files, dependency manifests, and necessary reference hooks. Do not publish:

- `config.toml`
- Runtime logs under `logs/`
- frida-server binaries
- `.venv/`, `__pycache__/`
- Local archive data under `archive_data/`, including real captures, screenshots, and binaries

## Copyright and Takedown Contact

If this project contains any content that infringes the lawful rights or interests of a copyright holder or related corporate entity, please contact me through an Issue or at ifireflyfans@gmail.com. I will remove the relevant content and delete this repository as soon as possible. I sincerely apologize for any inconvenience caused and appreciate your understanding and tolerance.

## License

MIT License. Users are responsible for platform-rule, account-risk, and real-bid consequences.
