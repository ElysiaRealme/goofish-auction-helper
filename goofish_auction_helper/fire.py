#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Goofish vendue bid — one-click direct fire (tap-free, no intercept-rewrite).

What it does
------------
Injects Frida into the LIVE Goofish process, hijacks one `bid.get` status-poll
ApiBusiness (which already carries a usable callback + the app's real signing
context), rewrites it to `mtop.idle.vendue.itemdetail.bid.price` with your
bidPrice, and lets the app's own MTOP SDK sign + send it. The server response
(SUCCESS / 出价成功 or an error) is captured and printed.

Dependencies (all required)
----------------------------
* Device (rooted Android emulator or device): Goofish RUNNING, LOGGED IN, and sitting on the
  TARGET auction page (so `bid.get` is polling — that poll object is the carrier).
* `frida-server` running as root on the device, with the same version as the host `frida` CLI.
* Host: the `frida` CLI from frida-tools, version MATCHING the server.
  NOTE: the bare `frida` Python binding's create_script() has no `Java` global on
  frida 17, so we MUST drive the CLI (which bundles the Java bridge).
* adb connectivity to the device.

You do NOT pass any accountId / cookie / token / sign — the live logged-in app
provides all auth/signing. This is a ride-along on the app, not a standalone
client. It cannot run headless without the emulator+app.

Fields (bid.price data): {auctionId:str, bidPrice:int, itemId:str, vendueId:str}.
auctionId/itemId/vendueId are auto-read from the current page's bid.get poll
(bid on whatever auction the page is showing); override with --auction/--item/
--vendue. bidPrice is the raw integer the app uses (same scale as currentPrice;
likely 分, i.e. value/100 = 元 — verify against the app display; run --dry-run
first to see currentPrice and confirm the scale).

Usage
-----
  # 0) safe check: see current auction + currentPrice, NO bid placed
  uv run python main.py fire --dry-run

  # 1) fire a bid (real, binding)
  python goofish_bid_fire.py --price 2888800
  python goofish_bid_fire.py --price 3000000 --auction 76557299 --item 1059375760839 --vendue 76557299

WARNING: --price (without --dry-run) places a REAL, binding bid on a live
marketplace. Violates Goofish ToS (account risk). Use on your own account.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from goofish_auction_helper.runtime import (
    PACKAGE,
    host_frida_version,
    load_config,
    resolve_adb,
    resolve_device,
    resolve_frida_exe,
    resolve_frida_server_bin,
    version_from_frida_server_bin,
)


def ensure_frida_server(adb_path: str, device: str, frida_server_bin: str) -> bool:
    """frida-server must run as root to inject the app. Auto-(re)start via su if down."""
    if sh([adb_path, "-s", device, "shell", "pgrep -f frida-server"]).strip():
        return True
    print("  [PREFLIGHT] frida-server not running on device; starting via su (root)...", file=sys.stderr)
    sh([adb_path, "-s", device, "shell", f'su -c "setsid {frida_server_bin} >/dev/null 2>&1 </dev/null &"'])
    time.sleep(2)
    return bool(sh([adb_path, "-s", device, "shell", "pgrep -f frida-server"]).strip())

DONE_MARKER = "XYFIRE_DONE"
ARMED_MARKER = "XYFIRE_ARMED"
FIRE_MARKER = "XYFIRE_FIRE"

JS_TEMPLATE = r"""
'use strict';
var TARGET_PRICE = __PRICE__;
var OVERRIDE = __OVERRIDE__;   // {auctionId,itemId,vendueId} or null
var DRY = __DRY__;
function emit(marker, obj){ console.log(marker + ' ' + JSON.stringify(obj)); }

Java.perform(function () {
  try {
    var MtopSend = Java.use('com.taobao.android.remoteobject.easy.MtopSend');
    var ABCls = Java.use('com.taobao.android.remoteobject.easy.ApiBusiness').class;
    var HashMap = Java.use('java.util.HashMap');
    var Long = Java.use('java.lang.Long');
    var fired = false;

    function setField(biz, name, val) {
      var f = ABCls.getDeclaredField(name);
      f.setAccessible(true);
      f.set(biz, val);
    }
    function bytesToStr(bd) {
      if (!bd) return '';
      var s = '';
      for (var i = 0; i < bd.length; i++) s += String.fromCharCode(bd[i] & 0xff);
      return s;
    }

    // Capture mtop responses (bid.price verdict, or bid.get currentPrice in dry mode).
    var DCB = Java.use('com.taobao.android.remoteobject.mtopsdk.MtopSDKHandler$DefaultCallBack');
    DCB.onMtopResponseFinished.implementation = function (resp) {
      try {
        var api = String(resp.getApi());
        var body = bytesToStr(resp.getBytedata());
        var m1 = body.match(/"currentPrice":"(\d+)"/);
        var m2 = body.match(/"success":"(\w+)"/);
        var m3 = body.match(/"ret":\[([^\]]*)\]/);
        if (DRY && api.indexOf('bid.get') !== -1 && stashed) {
          emit('__DONE__', {
            dry: true, retCode: String(resp.getRetCode()),
            auctionId: stashed.auctionId, itemId: stashed.itemId, vendueId: stashed.vendueId,
            currentPrice: m1 ? m1[1] : null, note: 'no bid placed'
          });
        } else if (!DRY && api.indexOf('bid.price') !== -1) {
          emit('__DONE__', {
            retCode: String(resp.getRetCode()),
            currentPrice: m1 ? m1[1] : null,
            success: m2 ? m2[1] : null,
            ret: m3 ? m3[1] : null,
            body: body
          });
        }
      } catch (e) { emit('__DONE__', { retCode: 'HOOK_ERROR', error: String(e) }); }
      return this.onMtopResponseFinished(resp);
    };

    var stashed = null;
    MtopSend.execute.overload('com.taobao.android.remoteobject.easy.IMtopBusiness').implementation = function (biz) {
      var api = '<err>';
      try { api = String(biz.getApiName()); } catch (e) {}
      if (api.indexOf('bid.get') !== -1) {
        if (DRY) {
          if (!stashed) {
            try {
              var p = Java.cast(biz.getParam(), HashMap);
              stashed = { auctionId: String(p.get('auctionId')), itemId: String(p.get('itemId')), vendueId: String(p.get('vendueId')) };
            } catch (e) {}
          }
        } else if (!fired) {
          fired = true;
          try {
            var pmap = Java.cast(biz.getParam(), HashMap);
            var au = (OVERRIDE && OVERRIDE.auctionId) ? OVERRIDE.auctionId : String(pmap.get('auctionId'));
            var it = (OVERRIDE && OVERRIDE.itemId) ? OVERRIDE.itemId : String(pmap.get('itemId'));
            var ve = (OVERRIDE && OVERRIDE.vendueId) ? OVERRIDE.vendueId : String(pmap.get('vendueId'));
            var np = HashMap.$new();
            np.put('auctionId', au);
            np.put('bidPrice', Long.valueOf(TARGET_PRICE));
            np.put('itemId', it);
            np.put('vendueId', ve);
            setField(biz, 'apiName', 'mtop.idle.vendue.itemdetail.bid.price');
            setField(biz, 'version', '1.0');
            biz.setParam(np);
            try { biz.isCallBacked().set(false); } catch (e) {}
            emit('__FIRE__', { auctionId: au, itemId: it, vendueId: ve, bidPrice: TARGET_PRICE });
          } catch (e) { emit('__DONE__', { retCode: 'CONVERT_ERROR', error: String(e) }); }
        }
      }
      return this.execute(biz);
    };

    emit('__ARMED__', { price: TARGET_PRICE, dry: DRY, override: OVERRIDE });
  } catch (e) {
    emit('__DONE__', { retCode: 'SCRIPT_ERROR', error: String(e) });
  }
});
"""


def sh(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace", timeout=10)
    except FileNotFoundError:
        return f"__CMD_NOT_FOUND__ {cmd[0]}"
    except subprocess.TimeoutExpired:
        return f"__CMD_TIMEOUT__ {cmd[0]}"
    except Exception as exc:
        return f"__CMD_ERROR__ {cmd[0]}: {exc}"
    return r.stdout.strip()


def get_pid(adb_path: str, device: str) -> int:
    raw = sh([adb_path, "-s", device, "shell", f"pidof {PACKAGE}"])
    if raw.startswith("__CMD_"):
        return 0
    first = raw.split()[0] if raw.split() else ""
    return int(first) if first.isdigit() else 0


def preflight(adb_path: str, device: str, frida_exe: str, frida_server_bin: str) -> int:
    problems = []
    if not Path(frida_exe).exists():
        problems.append(f"frida CLI not found: {frida_exe}")
    adb_is_file = Path(adb_path).exists()
    adb_on_path = bool(shutil.which(adb_path))
    if not adb_is_file and not adb_on_path:
        problems.append(f"adb not found: {adb_path}")
    devs = sh([adb_path, "devices"])
    if devs.startswith("__CMD_NOT_FOUND__"):
        problems.append(f"cannot run adb: {adb_path}")
    elif devs.startswith("__CMD_TIMEOUT__"):
        problems.append(f"adb command timed out: {adb_path}")
    elif device not in devs:
        if ":" in device:  # network adb device — auto-(re)connect after daemon restart
            print(f"  [PREFLIGHT] device {device} not connected; running 'adb connect'...", file=sys.stderr)
            sh([adb_path, "connect", device])
            devs = sh([adb_path, "devices"])
        if device not in devs:
            problems.append(f"device {device} not in adb devices (after connect):\n{devs}\n"
                            f"  -> is the emulator running? try: \"{adb_path}\" connect {device}")
    try:
        ver = host_frida_version(frida_exe)
        expected = version_from_frida_server_bin(frida_server_bin)
        if expected and expected not in ver:
            problems.append(f"frida CLI '{ver}' does not match device frida-server path '{frida_server_bin}'")
    except Exception as e:
        problems.append(f"cannot run frida --version: {e}")
    pid = get_pid(adb_path, device)
    if not pid:
        problems.append(f"{PACKAGE} not running - open Goofish and stay on the auction page")
    if "<arch>" in frida_server_bin:
        problems.append("cannot infer device ABI for frida-server path; connect the device or set --frida-server-bin")
    elif not ensure_frida_server(adb_path, device, frida_server_bin):
        problems.append("frida-server not running on device and could not auto-start via su "
                         f"(run: uv run python main.py frida, or: adb shell su -c \"{frida_server_bin} &\")")
    for p in problems:
        print(f"  [PREFLIGHT] {p}", file=sys.stderr)
    return pid


def build_js(price: int, override: dict | None, dry: bool) -> str:
    js = JS_TEMPLATE
    js = js.replace("__PRICE__", str(int(price)))
    js = js.replace("__OVERRIDE__", "null" if not override else json.dumps(override))
    js = js.replace("__DRY__", "true" if dry else "false")
    js = js.replace("__DONE__", DONE_MARKER).replace("__ARMED__", ARMED_MARKER).replace("__FIRE__", FIRE_MARKER)
    return js


def run_fire(price: int, override: dict | None, dry: bool, adb_path: str,
             device: str, frida_exe: str, timeout: float) -> dict | None:
    pid = get_pid(adb_path, device)
    if not pid:
        print(f"[ERROR] {PACKAGE} not running; open Goofish on the auction page first.", file=sys.stderr)
        return None
    js = build_js(price, override, dry)
    tmp = Path(tempfile.gettempdir()) / f"goofish_fire_{os.getpid()}.js"
    tmp.write_text(js, encoding="utf-8")

    proc = subprocess.Popen(
        [frida_exe, "-U", "-p", str(pid), "-l", str(tmp)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace",
    )
    lines: list[str] = []
    result: dict | None = None
    armed_seen = False

    def reader():
        try:
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            while lines:
                ln = lines.pop(0)
                if ARMED_MARKER in ln and not armed_seen:
                    armed_seen = True
                    print(f"  [hook] {ln.split(ARMED_MARKER)[-1].strip()}")
                elif FIRE_MARKER in ln:
                    print(f"  [fire] {ln.split(FIRE_MARKER)[-1].strip()}")
                elif DONE_MARKER in ln:
                    try:
                        result = json.loads(ln.split(DONE_MARKER, 1)[1].strip())
                    except Exception:
                        result = {"raw": ln}
                elif any(k in ln for k in ("Failed to load script", "Failed to attach",
                                           "unable to connect", "unable to find process",
                                           "frida-server", "process crashed", "device not found")):
                    print(f"  [FRIDA-ERR] {ln.strip()}", file=sys.stderr)
                    if result is None:
                        result = {"retCode": "FRIDA_ERROR", "error": ln.strip()[:400]}
            if result is not None:
                break
            time.sleep(0.2)
    finally:
        try:
            proc.terminate(); proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            tmp.unlink()
        except Exception:
            pass
    return result


def main() -> int:
    config = load_config(warn=not any(arg in {"-h", "--help"} for arg in sys.argv[1:]))
    ap = argparse.ArgumentParser(description="One-click tap-free direct Goofish vendue bid fire.")
    ap.add_argument("--price", type=int, help="bidPrice integer in the same scale as currentPrice, usually fen/yuan*100. Required unless --dry-run.")
    ap.add_argument("--dry-run", action="store_true", help="only read current auction + currentPrice; do NOT bid")
    ap.add_argument("--auction", help="override auctionId (else auto-read from current page)")
    ap.add_argument("--item", help="override itemId")
    ap.add_argument("--vendue", help="override vendueId")
    ap.add_argument("--device", help="adb serial/address. Defaults: CLI > GOOFISH_DEVICE > config.toml [env].device > auto.")
    ap.add_argument("--frida-exe", help="host frida CLI path. Defaults to the local uv venv.")
    ap.add_argument("--adb", help="adb executable. Defaults: CLI > GOOFISH_ADB > config.toml [env].adb > PATH adb.")
    ap.add_argument("--frida-server-bin", help="device-side frida-server path.")
    ap.add_argument("--timeout", type=float, default=25.0)
    args = ap.parse_args()

    args.adb = resolve_adb(args.adb, config)
    args.device = resolve_device(args.device, config, args.adb)
    args.frida_exe = resolve_frida_exe(args.frida_exe, config)
    args.frida_server_bin = resolve_frida_server_bin(
        args.frida_server_bin,
        config,
        adb_path=args.adb,
        device=args.device,
        frida_exe=args.frida_exe,
    )

    if not args.dry_run and args.price is None:
        ap.error("--price is required (or use --dry-run)")

    mode = "DRY-RUN" if args.dry_run else f"FIRE price={args.price}"
    print(f"== Goofish bid direct fire [{mode}] ==  device={args.device}")
    print(f"  [env] adb={args.adb}")
    print(f"  [env] frida={args.frida_exe}")
    print(f"  [env] frida-server={args.frida_server_bin}")
    pid = preflight(args.adb, args.device, args.frida_exe, args.frida_server_bin)
    if not pid:
        print("[ABORT] preflight failed (see above).", file=sys.stderr)
        return 2
    print(f"  [ok] Goofish pid={pid}; ensure it is on the target auction page (bid.get polling).")

    override = None
    if args.auction or args.item or args.vendue:
        override = {"auctionId": args.auction, "itemId": args.item, "vendueId": args.vendue}

    print(f"  [..] attaching + waiting for one bid.get poll (up to {args.timeout:.0f}s)...")
    res = run_fire(args.price or 0, override, args.dry_run, args.adb, args.device, args.frida_exe, args.timeout)
    if not res:
        print("[TIMEOUT] no response. Is the auction page open & polling? frida-server alive & version-matched?", file=sys.stderr)
        return 3

    print("\n== RESULT ==")
    if res.get("dry"):
        print(f"  mode          : DRY-RUN (no bid placed)")
        print(f"  auctionId     : {res.get('auctionId')}")
        print(f"  itemId        : {res.get('itemId')}")
        print(f"  vendueId      : {res.get('vendueId')}")
        print(f"  currentPrice  : {res.get('currentPrice')}   <- use the SAME integer scale for --price")
        return 0
    ok = str(res.get("retCode")) == "SUCCESS"
    print(f"  retCode       : {res.get('retCode')}")
    print(f"  success       : {res.get('success')}")
    print(f"  currentPrice  : {res.get('currentPrice')}")
    print(f"  ret           : {res.get('ret')}")
    if res.get("error"):
        print(f"  error         : {res.get('error')}")
    print("  body          : " + str(res.get("body", ""))[:400])
    print("\n  => " + ("✅ 出价成功 (SUCCESS)" if ok else "❌ 未通过 (see retCode/ret)"))
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
