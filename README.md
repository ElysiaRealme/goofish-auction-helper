# Goofish Auction Helper

简体中文 | [English](README.en.md)

> 风险声明: 本项目会对正在运行且已登录的闲鱼 App 进行动态插桩。`--price` 和 `--live` 会发起真实、具约束力的出价，可能触发账号风控、违反平台 ToS，赢拍后不付款也可能影响信用。仅限你自己的账号、你完全理解风险且自愿承担后果时使用。本仓库不包含闲鱼 APK、账号凭据、Cookie、Token 或签名密钥。

## 项目定位

这是一个基于 Frida 的闲鱼拍卖自动出价工具。它不复刻签名算法，也不保存账号态，而是注入到用户本机正在运行、已登录、停留在目标拍卖页的闲鱼 App，复用 App 自身的 MTOP 签名通道完成出价。

使用背景:

- 你是否还在因为最小加价幅度太小，手指点断了，结果又被别人的出价刷新，只能重新点一次？
- 你是否还在因为竞拍最后 2 分钟的加时陷入膀胱局，熬夜到没法睡觉？
- `goofish-auction-helper` 意在帮助想要一次性出价到位、或想要无人值守自动延时的闲鱼拍卖用户。

该项目提供两种模式:

- 单次出价模式: 读到当前拍卖上下文后发起一次指定价格的出价，适合想一次性出价到位的场景。
- 狙击模式: 持续监听拍卖轮询状态，在自己落后且进入配置的剩余时间窗口时自动加价一次，适合无人值守延时或守价。

运行前提:

- 需要使用 Android 模拟器或真机；推荐使用 MuMu，本项目主要按 MuMu 环境适配，其他环境不保证兼容性。
- 需要手动下载并部署与 host 端 `frida` CLI 版本、设备 CPU 架构一致的 frida-server；详情见 [FRIDA_SERVER.md](FRIDA_SERVER.md) 和下方配置说明。

关键参数快速理解:

- `max_price`: 真实出价价格上限。为防止对手无限加价导致价格被抬到不可接受的金额，真实模式必须设置最大承受金额。
- `trigger_sec`: 当服务端剩余时间小于等于该值、且自己处于落后状态时，触发一次加价。由于闲鱼机制是在最后 2 分钟内出价会触发 5 分钟延时，推荐将此值设置在 2 分钟以内，减少频繁加价。如果你希望对手一出价就立刻跟拍，可以设置到 5 分钟左右，用于常驻榜一。推荐值约为 `90` 秒；实测链路可能存在约 `±10s` 延迟，因此该值相对安全。

仓库以 [main.py](main.py) 为唯一主入口，源码收敛在 `goofish_auction_helper/` 包内:

- `tui`: 方向键 TUI，按配置、检查、启动 frida-server、dry-run、simulate/live 的线性流程引导。
- `fire`: 单次出价或 `--dry-run` 只读当前拍卖状态。
- `sniper`: 持续监听 `bid.get` 轮询，在确认自己落后且进入收尾窗口时按价格上限自动出价。

推荐仓库名: `goofish-auction-helper`。

## 核心机制

闲鱼拍卖页会持续轮询 `mtop.idle.vendue.itemdetail.bid.get`。该轮询请求对应一个完整的 `ApiBusiness` 对象，已经带有 App 当前账号的真实签名上下文和 callback。工具 hook `MtopSend.execute(IMtopBusiness)`，抓住一条 `bid.get` 轮询对象，把它转换成 `mtop.idle.vendue.itemdetail.bid.price` 出价请求，然后交回 App 原生 MTOP SDK 发送。

`sniper` 的成功/失败判定不依赖不稳定的 `bid.price` 响应 hook，而是读取后续 `bid.get` 轮询中的服务端状态:

- `myBidStatusDTO.statusDesc` 为 `领先` 时视为已领先。
- `myBidStatusDTO.statusDesc` 为 `落后` 时才允许进入出价判定。
- `UNKNOWN` 状态绝不出价，避免误抬自己的价格。
- 剩余时间使用服务端 `serverTime` 和 `bidEndTime` 计算，价格使用服务端 `nextBidPrice` 或配置的加价策略。

## 环境要求

| 依赖 | 要求 |
|---|---|
| Android 运行环境 | 已 root 的模拟器或真机，闲鱼 App 已登录并停在目标拍卖页 |
| adb | 能连接目标设备；可通过 `GOOFISH_ADB`、`config.toml` 或 PATH 提供 |
| frida-server | 在设备端以 root 运行，版本必须与 host 端 `frida` CLI 一致 |
| Python | 3.11+，项目使用 `tomllib` 读取配置 |
| uv | 用于同步 `.venv` 和执行脚本 |

frida-server 二进制不进入仓库。按 `uv run frida --version` 和 `adb shell getprop ro.product.cpu.abi` 到 [Frida Releases](https://github.com/frida/frida/releases) 下载匹配版本与架构，解压后推送到设备端。更多步骤见 [FRIDA_SERVER.md](FRIDA_SERVER.md)。

## 预编译版本

本项目以 TUI 为主轴。GitHub Release 提供两个 Windows onefile exe，推荐优先下载 TUI 版本:

| 文件 | 定位 | 适合场景 | 启动方式 |
|---|---|---|---|
| `goofish-auction-helper-tui-v0.1.0-windows-amd64.exe` | 推荐版本 / 主入口 | 大多数用户；需要按线性流程完成配置、环境检查、启动 frida-server、dry-run、simulate/live | 双击或命令行无参数运行，默认进入方向键菜单 |
| `goofish-auction-helper-cli-v0.1.0-windows-amd64.exe` | 高级命令行版本 | 熟悉参数、需要脚本化或只想直接调用 `fire` / `sniper` / `frida` 的用户 | 必须在命令行中追加子命令和参数，例如 `fire --dry-run` |

两个 exe 都只是封装本项目的 Python 逻辑，不包含 adb、frida-server、闲鱼 APK 或账号态。真实运行仍需要 MuMu/Android 设备、可用 adb、host 端 `frida` CLI，以及设备端 root 运行且版本匹配的 frida-server。

## 安装

```bat
uv sync
```

也可以使用传统 venv:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Conda 用户可以使用:

```bat
conda env create -f environment.yml
conda activate goofish-auction-helper
```

## 配置

复制安全示例配置:

```bat
copy config.example.toml config.toml
```

环境相关配置优先级为: 命令行显式参数 > 环境变量 > `config.toml` > 自动发现 > 安全兜底。

| 配置 | 环境变量 | `config.toml` 字段 | 说明 |
|---|---|---|---|
| adb 路径 | `GOOFISH_ADB` | `[env].adb` | 未设置时尝试 `shutil.which("adb")` |
| 设备地址 | `GOOFISH_DEVICE` | `[env].device` | 未设置且仅有一个 adb 设备时自动选择 |
| frida CLI | `GOOFISH_FRIDA` | `[env].frida_exe` | 默认使用 uv 创建的本地 `.venv` |
| 设备端 frida-server | `GOOFISH_FRIDA_SERVER_BIN` | `[env].frida_server_bin` | 未设置时由 host frida 版本和设备 ABI 推断 |

`config.toml` 被 `.gitignore` 忽略。不要提交包含 `live = true`、真实价格上限、设备地址或本地路径的个人配置。

### `config.toml` 参数说明

环境连接参数:

| 参数 | 类型 | 默认行为 | 说明 |
|---|---|---|---|
| `[env].adb` | 字符串 | 读取 `GOOFISH_ADB`，否则自动查找 PATH 中的 `adb` | adb 可执行文件路径；可填绝对路径，也可填 `adb` |
| `[env].device` | 字符串 | 读取 `GOOFISH_DEVICE`，否则尝试选择唯一已连接设备 | adb 设备 serial/address，例如模拟器端口或 USB 设备序列号 |
| `[env].frida_exe` | 字符串 | 读取 `GOOFISH_FRIDA`，否则使用 uv 虚拟环境中的 `frida` | host 端 Frida CLI 路径 |
| `[env].frida_server_bin` | 字符串 | 读取 `GOOFISH_FRIDA_SERVER_BIN`，否则根据 host Frida 版本和设备 ABI 推断 | 设备端 frida-server 路径，必须与已推送到设备的二进制一致 |

狙击触发与出价参数:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `trigger_sec` | 整数秒 | `90` | 当服务端剩余时间小于等于 `trigger_sec + 随机抖动` 且自己落后时进入出价判定；推荐约 `90` 秒，通常不超过 2 分钟 |
| `jitter_sec` | 整数秒 | `5` | 在 `[-jitter_sec, +jitter_sec]` 范围内加入随机抖动，避免固定时间点触发 |
| `aggression` | 整数 | `1` | 加价倍数；`1` 表示使用服务端给出的 `nextBidPrice`，更高值会按加价幅度跳档 |
| `step` | 整数，单位分 | 未设置 | 手动指定加价幅度；不设置时使用服务端 `nextBidPrice` / `marginPrice` |

重试与节奏参数:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_retries` | 整数 | `3` | 每个延时窗口内最多尝试出价次数 |
| `cooldown_ms` | 整数毫秒 | `3000` | 两次出价尝试之间的冷却时间 |
| `outcome_ms` | 整数毫秒 | `4000` | 出价后等待后续 `bid.get` 轮询确认结果的超时时间 |

安全与目标定位参数:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `live` | 布尔值 | `false` | `false` 为模拟模式，不出价；`true` 会允许真实自动出价 |
| `max_price` | 整数，单位分 | 未设置 | 真实出价价格上限；使用 `--live` 时必须提供，用于防止无限跟价到不可接受金额 |
| `auction` | 字符串 | 未设置 | 可选固定 `auctionId`；不设置时读取当前 App 页面 |
| `item` | 字符串 | 未设置 | 可选固定 `itemId`；不设置时读取当前 App 页面 |
| `vendue` | 字符串 | 未设置 | 可选固定 `vendueId`；不设置时读取当前 App 页面 |

## 使用

推荐从 TUI 开始。预编译 TUI 版直接运行 exe 即可；源码运行使用:

```bat
uv run python main.py tui
```

安全只读检查:

```bat
uv run python main.py fire --dry-run
```

单次真实出价:

```bat
uv run python main.py fire --price <price_in_fen>
```

启动或重启设备端 frida-server:

```bat
uv run python main.py frida --frida-server-bin /data/local/tmp/frida-server-<version>-<arch>
```

狙击模拟模式，默认不出价:

```bat
uv run python main.py sniper --simulate
```

真实狙击必须显式给出 live 和价格天花板:

```bat
uv run python main.py sniper --live --max-price <max_price_in_fen>
```

## 目录说明

| 路径 | 说明 |
|---|---|
| `main.py` | 唯一主入口，分发到 `tui`、`fire`、`sniper`、`frida` |
| `tui_main.py` | TUI onefile 发布入口；无参数打开 TUI，有参数时回退到 CLI 分发 |
| `goofish_auction_helper/cli.py` | CLI 分发器 |
| `goofish_auction_helper/tui.py` | 方向键 TUI |
| `goofish_auction_helper/fire.py` | 单次出价/只读检查核心逻辑，内嵌 Frida JS |
| `goofish_auction_helper/sniper.py` | 自动狙击核心逻辑，保留三态判定和 poll-convert 流程 |
| `goofish_auction_helper/runtime.py` | adb、设备、frida CLI、frida-server 路径的通用解析 |
| `goofish_auction_helper/frida_server.py` | 设备端 frida-server root 启停辅助 |
| `goofish_auction_helper/hooks/` | canonical Frida hook 参考脚本 |
| `goofish_auction_helper/tools/recon_bid_get.py` | `bid.get` 响应侦察工具 |
| `config.example.toml` | 安全默认配置示例 |
| `FRIDA_SERVER.md` | frida-server 下载、推送和版本匹配说明 |

## 已知限制

- 目标 App 必须处于运行、登录、打开目标拍卖页的状态；该工具不能无头运行。
- frida-server 必须以 root 身份运行，否则无法注入非 debuggable App。
- host `frida` CLI 与设备端 frida-server 版本必须一致。
- Frida 17 的裸 Python binding 在本项目路径中没有可靠 Java 桥，因此核心流程通过 `frida` CLI 执行。
- 闲鱼 App 更新可能改变类名、字段或 MTOP 封装；失效时用 `goofish_auction_helper/hooks/mtop_focus_hook.js` 和 `goofish_auction_helper/tools/recon_bid_get.py` 重新定位。
- `bid.price` 响应 hook 对 poll-convert 请求不稳定，`sniper` 以后续 `bid.get` 状态作为最终判定来源。

## 验证

```bat
uv lock --check
uv run python -B -m compileall -q main.py tui_main.py goofish_auction_helper
```

真实链路验证应从 `uv run python main.py fire --dry-run` 开始，确认能读到 `currentPrice` 后再考虑 live 命令。

## Git 边界

本仓库只提交源码、示例配置、README、依赖清单和必要的 reference hooks。以下内容不应公开:

- `config.toml`
- `logs/` 中的运行日志
- frida-server 二进制
- `.venv/`、`__pycache__/`
- `archive_data/` 中的本地归档、真实抓包、截图和大二进制

## 版权与下架联系

如本项目中存在侵犯版权方（或相关公司实体）合法权益的内容，请通过 Issue 或 ifireflyfans@gmail.com 与我联系，我将第一时间下架相关内容并删除本仓库。对此造成的不便，我深表歉意，感谢您的理解与包容。

## License

MIT License. 使用者自行承担平台规则、账号风控和真实出价后果。
