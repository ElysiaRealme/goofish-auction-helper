#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Goofish vendue sniper — poll-listen + last-moment trigger that keeps you winning.

Strategy (auction rule: a bid in the last 2 min extends the end to now+5min, so
endgame bids are minutes apart — no sub-second counter-bidding):
  * Watch the live `bid.get` poll stream for full state.
  * Fire ONLY when the server explicitly says I'm BEHIND (myBidStatusDTO == 落后)
    AND remaining <= 90s + random ±5s. Bid amount = nextBidPrice (server-validated
    min+1 step). LEADING / UNKNOWN never bid -> never raise your own price.
  * Detect outcome from the next bid.get polls (leading? outbid? timeout?) and
    retry on miss. Repeat until you win or a stop fires.

"Am I the current high bidder?" is read AUTHORITATIVELY from myBidStatusDTO (the
server computes it for the logged-in account) — not guessed from price/userId.
Cross-check: when leading, currentPrice must equal my last confirmed lead price.

bid.get fields (confirmed live from the app poll stream):
  currentPrice, nextBidPrice (=cur+increment; min step, rule-driven), marginPrice,
  myBidStatusDTO.statusDesc ("领先"/"落后"), bidEndTime (Beijing str), serverTime(ms),
  vendueRuleDesc, isDepositPaid, bidDOList. Unit = 分 (÷100 = 元).

Safety: DEFAULT is SIMULATE (no bids); --live to bid for real; --max-price (分) is
REQUIRED for --live (hard ceiling). Needs a rooted Android runtime + Goofish on the auction page.

Usage:
  python goofish_sniper.py                                  # simulate (no bids)
  python goofish_sniper.py --live --max-price 4000000       # real, capped
  python goofish_sniper.py --live --max-price 4000000 --aggression 2

WARNING: --live places REAL binding bids automatically; violates Goofish ToS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import shutil
from datetime import datetime
from pathlib import Path

from goofish_auction_helper.runtime import (
    PACKAGE,
    ROOT,
    config_source_label,
    host_frida_version,
    load_config,
    resolve_adb,
    resolve_device,
    resolve_frida_exe,
    resolve_frida_server_bin,
    version_from_frida_server_bin,
)

MARKER = "XYS"

JS_TEMPLATE = r"""
'use strict';
var P = {
  MAX_PRICE: __MAX_PRICE__, TRIGGER_S: __TRIGGER_S__, JITTER_S: __JITTER_S__,
  MAX_RETRIES: __MAX_RETRIES__, COOLDOWN_MS: __COOLDOWN_MS__, OUTCOME_MS: __OUTCOME_MS__,
  STEP: __STEP__, AGGR: __AGGR__, SIMULATE: __SIMULATE__, AU: __AU__
};
function log(tag, d) { console.log('XYS ' + JSON.stringify({ tag: tag, d: d })); }
function rnd(m) { return (Math.random() * 2 - 1) * m; }
function nowMs() { return Date.now(); }

Java.perform(function () {
  var MtopSend, ABCls, HashMap, Long, JString;
  try {
    MtopSend = Java.use('com.taobao.android.remoteobject.easy.MtopSend');
    ABCls = Java.use('com.taobao.android.remoteobject.easy.ApiBusiness').class;
    HashMap = Java.use('java.util.HashMap');
    Long = Java.use('java.lang.Long');
    JString = Java.use('java.lang.String');
  } catch (e) { log('FATAL', { stage: 'use', error: String(e) }); return; }

  function setField(biz, n, v) { var f = ABCls.getDeclaredField(n); f.setAccessible(true); f.set(biz, v); }

  var S = {
    auctionId: null, itemId: null, vendueId: null,
    currentPrice: null, nextBidPrice: null, increment: null,
    myStatus: 'UNKNOWN',        // 'LEADING' | 'BEHIND' | 'UNKNOWN'  (server-authoritative)
    myLeadPrice: null,          // price at which I last confirmed leading (cross-check)
    deposit: null,
    serverTime: null, bidEndTimeStr: null, bidEndTimeMs: null, remainingMs: null,
    jitterS: rnd(P.JITTER_S),
    firing: false, lastFirePrice: null, fireAttemptMs: 0,
    retries: 0, lastAttemptMs: 0, firedForEnd: null,
    wins: 0, fires: 0, stopped: null, lastWaitLog: 0
  };
  var wantFire = null;

  function parseBidGet(body) {
    try {
      var o = JSON.parse(body);
      var d = (o && o.data && o.data.data) || null;
      if (!d) return null;
      var st = o.data.serverTime ? parseInt(o.data.serverTime, 10) : null;
      var endMs = null;
      if (d.bidEndTime) { var iso = String(d.bidEndTime).replace(' ', 'T') + '+08:00'; endMs = Date.parse(iso); if (isNaN(endMs)) endMs = null; }
      var my = d.myBidStatusDTO || {};
      var ms = String(my.status || ''); var msd = String(my.statusDesc || '');
      var myStatus = 'UNKNOWN';
      if (ms === '2' || msd.indexOf('领先') !== -1) myStatus = 'LEADING';        // 领先
      else if (ms === '1' || msd.indexOf('落后') !== -1) myStatus = 'BEHIND';    // 落后
      var cur = d.currentPrice != null ? parseInt(d.currentPrice, 10) : null;
      var nxt = d.nextBidPrice != null ? parseInt(d.nextBidPrice, 10) : null;
      var inc = d.marginPrice != null ? parseInt(d.marginPrice, 10) : null;
      if (nxt != null && cur != null && inc == null) inc = nxt - cur;
      return {
        itemId: d.itemId ? String(d.itemId) : null,
        vendueId: d.vendueId ? String(d.vendueId) : null,
        currentPrice: cur, nextBidPrice: nxt, increment: inc,
        myStatus: myStatus, deposit: d.isDepositPaid,
        serverTime: st, bidEndTimeStr: d.bidEndTime ? String(d.bidEndTime) : null, bidEndTimeMs: endMs,
        remainingMs: (st != null && endMs != null) ? (endMs - st) : null
      };
    } catch (e) { return null; }
  }

  function computeBidPrice() {
    if (S.currentPrice != null && P.AGGR > 1 && (P.STEP || S.increment)) {
      return S.currentPrice + (P.STEP || S.increment) * P.AGGR;
    }
    if (S.nextBidPrice != null) return S.nextBidPrice;
    if (S.currentPrice != null && (P.STEP || S.increment)) return S.currentPrice + (P.STEP || S.increment);
    return null;
  }

  function decide() {
    if (S.stopped) return;

    // (0) resolve an in-flight fire from the poll stream (ground truth)
    if (S.firing) {
      if (S.myStatus === 'LEADING') {
        S.firing = false; S.wins++; S.firedForEnd = S.bidEndTimeStr;
        S.myLeadPrice = (S.lastFirePrice != null) ? S.lastFirePrice : S.currentPrice;
        log('SUCCESS', { price: S.lastFirePrice, currentPrice: S.currentPrice, wins: S.wins });
        return;
      }
      if (S.currentPrice != null && S.lastFirePrice != null && S.currentPrice > S.lastFirePrice) {
        S.firing = false; log('OUTBID_RACE', { theirs: S.currentPrice, mine: S.lastFirePrice });
      } else if ((nowMs() - S.fireAttemptMs) > P.OUTCOME_MS) {
        S.firing = false; log('OUTCOME_TIMEOUT', { mine: S.lastFirePrice, currentPrice: S.currentPrice });
      } else { return; }
      // fall through to maybe retry
    }

    // (1) deposit guard
    if (S.deposit === 'false' || S.deposit === false) { S.stopped = 'NO_DEPOSIT'; log('STOP_NO_DEPOSIT', {}); return; }

    // (2) can't compute remaining -> hold (safe: no bid)
    if (S.remainingMs == null) {
      if (nowMs() - S.lastWaitLog > 10000) { S.lastWaitLog = nowMs(); log('NO_REMAINING', { bidEndTimeStr: S.bidEndTimeStr }); }
      return;
    }

    // (3) auction ended?
    if (S.remainingMs <= 0) {
      S.stopped = (S.myStatus === 'LEADING') ? 'WON' : 'LOST_ENDED';
      log('AUCTION_END', { result: S.stopped, myStatus: S.myStatus, currentPrice: S.currentPrice });
      return;
    }

    // (4) LEADING -> NEVER bid. Cross-check price vs my recorded lead price (warn-only).
    if (S.myStatus === 'LEADING') {
      if (S.myLeadPrice != null && S.currentPrice != null && S.currentPrice !== S.myLeadPrice) {
        if (nowMs() - S.lastWaitLog > 10000) { S.lastWaitLog = nowMs(); log('LEAD_PRICE_MISMATCH', { currentPrice: S.currentPrice, myLeadPrice: S.myLeadPrice }); }
      }
      return;
    }

    // (5) UNKNOWN -> NEVER bid (could be a glitch; bidding risks raising own price)
    if (S.myStatus !== 'BEHIND') {
      if (nowMs() - S.lastWaitLog > 10000) { S.lastWaitLog = nowMs(); log('STATUS_UNKNOWN_HOLD', { myStatus: S.myStatus }); }
      return;
    }

    // (6) confirmed BEHIND. Price + ceiling + sanity.
    var bidPrice = computeBidPrice();
    if (bidPrice == null || bidPrice <= 0) return;
    if (S.currentPrice != null && bidPrice <= S.currentPrice) { log('SKIP_NOT_HIGHER', { bidPrice: bidPrice, currentPrice: S.currentPrice }); return; }
    if (P.MAX_PRICE != null && bidPrice > P.MAX_PRICE) { S.stopped = 'MAX_PRICE'; log('STOP_MAX_PRICE', { bidPrice: bidPrice, max: P.MAX_PRICE }); return; }

    // (7) timing
    var thrMs = (P.TRIGGER_S + S.jitterS) * 1000;
    if (S.remainingMs > thrMs) {
      if (nowMs() - S.lastWaitLog > 5000) { S.lastWaitLog = nowMs(); log('WAIT', { remS: Math.round(S.remainingMs / 1000), thrS: Math.round(thrMs / 1000), cur: S.currentPrice, next: S.nextBidPrice, myStatus: S.myStatus }); }
      return;
    }

    // (8) strike zone
    if (S.firing) return;
    if (nowMs() - S.lastAttemptMs < P.COOLDOWN_MS) return;
    if (S.retries >= P.MAX_RETRIES) { log('MAX_RETRIES_THIS_WINDOW', { n: S.retries }); return; }

    // (9) arm
    S.firing = (P.SIMULATE ? false : true);
    S.retries++; S.lastAttemptMs = nowMs(); S.fires++;
    wantFire = bidPrice;
    log('ARM_FIRE', { price: bidPrice, cur: S.currentPrice, next: S.nextBidPrice, inc: S.increment, remS: Math.round(S.remainingMs / 1000), retry: S.retries, simulate: P.SIMULATE });
    if (P.SIMULATE) { wantFire = null; S.firing = false; }
  }

  try {
    var DCB = Java.use('com.taobao.android.remoteobject.mtopsdk.MtopSDKHandler$DefaultCallBack');
    DCB.onMtopResponseFinished.implementation = function (resp) {
      try {
        var api = String(resp.getApi());
        var bd = resp.getBytedata();
        var body = bd ? String(JString.$new(bd, 'UTF-8')) : '';
        if (api.indexOf('bid.get') !== -1) {
          var st = parseBidGet(body);
          if (st) {
            if (S.bidEndTimeStr && st.bidEndTimeStr && S.bidEndTimeStr !== st.bidEndTimeStr) {
              log('NEW_WINDOW', { end: st.bidEndTimeStr, prev: S.bidEndTimeStr });
              S.jitterS = rnd(P.JITTER_S); S.retries = 0; S.firedForEnd = null;
            }
            S.auctionId = (P.AU && P.AU.auctionId) ? P.AU.auctionId : (st.vendueId || S.auctionId);
            S.itemId = (P.AU && P.AU.itemId) ? P.AU.itemId : (st.itemId || S.itemId);
            S.vendueId = (P.AU && P.AU.vendueId) ? P.AU.vendueId : (st.vendueId || S.vendueId);
            S.currentPrice = st.currentPrice; S.nextBidPrice = st.nextBidPrice; S.increment = st.increment;
            S.myStatus = st.myStatus; S.deposit = st.deposit;
            S.serverTime = st.serverTime; S.bidEndTimeStr = st.bidEndTimeStr; S.bidEndTimeMs = st.bidEndTimeMs;
            S.remainingMs = st.remainingMs;
            decide();
          }
        } else if (api.indexOf('bid.price') !== -1) {
          log('BIDPRICE_RESP', { retCode: String(resp.getRetCode()), body: body.slice(0, 240) });
        }
      } catch (e) { log('RESP_ERR', { error: String(e) }); }
      return this.onMtopResponseFinished(resp);
    };
  } catch (e) { log('FATAL', { stage: 'hook_resp', error: String(e) }); return; }

  try {
    MtopSend.execute.overload('com.taobao.android.remoteobject.easy.IMtopBusiness').implementation = function (biz) {
      try {
        var api = '<err>'; try { api = String(biz.getApiName()); } catch (e) {}
        if (api.indexOf('bid.get') !== -1) {
          try {
            var pp = Java.cast(biz.getParam(), HashMap);
            if (!P.AU) { S.auctionId = String(pp.get('auctionId')); S.itemId = String(pp.get('itemId')); S.vendueId = String(pp.get('vendueId')); }
          } catch (e) {}
          if (wantFire !== null) {
            var price = wantFire; wantFire = null;
            S.lastFirePrice = price; S.fireAttemptMs = nowMs();
            try {
              var pmap = Java.cast(biz.getParam(), HashMap);
              var np = HashMap.$new();
              np.put('auctionId', S.auctionId); np.put('bidPrice', Long.valueOf(price));
              np.put('itemId', S.itemId); np.put('vendueId', S.vendueId);
              setField(biz, 'apiName', 'mtop.idle.vendue.itemdetail.bid.price');
              setField(biz, 'version', '1.0');
              biz.setParam(np);
              try { biz.isCallBacked().set(false); } catch (e) {}
              log('FIRE', { price: price, cur: S.currentPrice, remS: S.remainingMs != null ? Math.round(S.remainingMs / 1000) : null });
            } catch (e) { log('CONVERT_ERR', { error: String(e) }); S.firing = false; }
          }
        }
      } catch (e) { log('SENDHOOK_ERR', { error: String(e) }); }
      return this.execute(biz);
    };
  } catch (e) { log('FATAL', { stage: 'hook_send', error: String(e) }); return; }

  log('ARMED', { simulate: P.SIMULATE, max_price: P.MAX_PRICE, trigger_s: P.TRIGGER_S, jitter_s: P.JITTER_S, max_retries: P.MAX_RETRIES, cooldown_ms: P.COOLDOWN_MS, aggression: P.AGGR });
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


def ensure_frida_server(adb_path: str, device: str, frida_server_bin: str) -> bool:
    """frida-server must run as root to inject the app. Auto-(re)start via su if down."""
    if sh([adb_path, "-s", device, "shell", "pgrep -f frida-server"]).strip():
        return True
    print("  [PREFLIGHT] frida-server not running on device; starting via su (root)...", file=sys.stderr)
    sh([adb_path, "-s", device, "shell", f'su -c "setsid {frida_server_bin} >/dev/null 2>&1 </dev/null &"'])
    time.sleep(2)
    return bool(sh([adb_path, "-s", device, "shell", "pgrep -f frida-server"]).strip())


def preflight(adb_path: str, device: str, frida_exe: str, frida_server_bin: str) -> int:
    problems = []
    if not Path(frida_exe).exists(): problems.append(f"frida CLI not found: {frida_exe}")
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
            problems.append(f"device {device} not connected (after connect). Is the emulator running? "
                            f"try: \"{adb_path}\" connect {device}")
    ver = host_frida_version(frida_exe)
    expected = version_from_frida_server_bin(frida_server_bin)
    if expected and expected not in ver:
        problems.append(f"frida CLI '{ver}' does not match device frida-server path '{frida_server_bin}'")
    pid = get_pid(adb_path, device)
    if not pid: problems.append(f"{PACKAGE} not running - open Goofish on the auction page")
    if "<arch>" in frida_server_bin:
        problems.append("cannot infer device ABI for frida-server path; connect the device or set --frida-server-bin")
    elif not ensure_frida_server(adb_path, device, frida_server_bin):
        problems.append("frida-server not running; could not auto-start via su (run: uv run python main.py frida)")
    for p in problems: print(f"  [PREFLIGHT] {p}", file=sys.stderr)
    return pid


def build_js(p) -> str:
    js = JS_TEMPLATE
    js = js.replace("__MAX_PRICE__", "null" if p.max_price is None else str(int(p.max_price)))
    js = js.replace("__TRIGGER_S__", str(int(p.trigger_sec)))
    js = js.replace("__JITTER_S__", str(int(p.jitter_sec)))
    js = js.replace("__MAX_RETRIES__", str(int(p.max_retries)))
    js = js.replace("__COOLDOWN_MS__", str(int(p.cooldown_ms)))
    js = js.replace("__OUTCOME_MS__", str(int(p.outcome_ms)))
    js = js.replace("__STEP__", "null" if p.step is None else str(int(p.step)))
    js = js.replace("__AGGR__", str(int(p.aggression)))
    js = js.replace("__SIMULATE__", "true" if p.simulate else "false")
    au = None
    if p.auction or p.item or p.vendue:
        au = {"auctionId": p.auction, "itemId": p.item, "vendueId": p.vendue}
    js = js.replace("__AU__", "null" if not au else json.dumps(au))
    return js


def main() -> int:
    cfg = load_config(warn=not any(arg in {"-h", "--help"} for arg in sys.argv[1:]))
    ap = argparse.ArgumentParser(description="Goofish vendue sniper (poll-listen + last-moment trigger). "
                                             "Defaults come from config.toml; CLI flags override.")
    ap.add_argument("--live", action="store_true", help="ACTUALLY bid (overrides config).")
    ap.add_argument("--simulate", action="store_true", help="explicit simulate / no bids (overrides config).")
    ap.add_argument("--max-price", type=int, default=cfg.get("max_price"), help="hard ceiling in fen/yuan*100 (REQUIRED for live).")
    ap.add_argument("--trigger-sec", type=int, default=cfg.get("trigger_sec", 90),
                    help="fire when remaining <= this + jitter (seconds). 90=1m30s, 300=5min, 60=1min.")
    ap.add_argument("--jitter-sec", type=int, default=cfg.get("jitter_sec", 5))
    ap.add_argument("--max-retries", type=int, default=cfg.get("max_retries", 3))
    ap.add_argument("--cooldown-ms", type=int, default=cfg.get("cooldown_ms", 3000))
    ap.add_argument("--outcome-ms", type=int, default=cfg.get("outcome_ms", 4000))
    ap.add_argument("--step", type=int, default=cfg.get("step"), help="override increment in fen/yuan*100 (default: server nextBidPrice)")
    ap.add_argument("--aggression", type=int, default=cfg.get("aggression", 1), help="jump N increments (default 1)")
    ap.add_argument("--auction", default=cfg.get("auction"), help="override auctionId")
    ap.add_argument("--item", default=cfg.get("item"), help="override itemId")
    ap.add_argument("--vendue", default=cfg.get("vendue"), help="override vendueId")
    ap.add_argument("--max-runtime-sec", type=int, default=0, help="auto-stop after N seconds (0=until ended/stopped)")
    ap.add_argument("--device", help="adb serial/address. Defaults: CLI > GOOFISH_DEVICE > config.toml [env].device > auto.")
    ap.add_argument("--frida-exe", help="host frida CLI path. Defaults to the local uv venv.")
    ap.add_argument("--adb", help="adb executable. Defaults: CLI > GOOFISH_ADB > config.toml [env].adb > PATH adb.")
    ap.add_argument("--frida-server-bin", help="device-side frida-server path.")
    args = ap.parse_args()

    args.adb = resolve_adb(args.adb, cfg)
    args.device = resolve_device(args.device, cfg, args.adb)
    args.frida_exe = resolve_frida_exe(args.frida_exe, cfg)
    args.frida_server_bin = resolve_frida_server_bin(
        args.frida_server_bin,
        cfg,
        adb_path=args.adb,
        device=args.device,
        frida_exe=args.frida_exe,
    )

    if args.live and args.simulate: ap.error("--live and --simulate are mutually exclusive")
    if args.live: live = True
    elif args.simulate: live = False
    else: live = bool(cfg.get("live", False))
    args.simulate = (not live)
    if live and args.max_price is None:
        ap.error("--max-price is required for live (set max_price in config.toml or pass --max-price, in fen/yuan*100)")

    src = config_source_label()
    mode = "SIMULATE (no bids)" if args.simulate else f"LIVE  ceiling={args.max_price}"
    print(f"== Goofish sniper [{mode}] ==  rules<{src}>  trigger={args.trigger_sec}+/-{args.jitter_sec}s  "
          f"aggr={args.aggression}  cooldown={args.cooldown_ms}ms  device={args.device}")
    print(f"  [env] adb={args.adb}")
    print(f"  [env] frida={args.frida_exe}")
    print(f"  [env] frida-server={args.frida_server_bin}")
    pid = preflight(args.adb, args.device, args.frida_exe, args.frida_server_bin)
    if not pid:
        print("[ABORT] preflight failed (see above).", file=sys.stderr); return 2
    print(f"  [ok] Goofish pid={pid}; ensure it is ON the target auction page.\n")

    js = build_js(args)
    tmp = Path(tempfile.gettempdir()) / f"goofish_sniper_{os.getpid()}.js"
    tmp.write_text(js, encoding="utf-8")
    logf = ROOT / "logs" / f"sniper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logf.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen([args.frida_exe, "-U", "-p", str(pid), "-l", str(tmp)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    lines: list[str] = []
    def reader():
        try:
            for ln in proc.stdout: lines.append(ln.rstrip("\n"))
        except Exception: pass
    threading.Thread(target=reader, daemon=True).start()

    stop_reason = None; start = time.time()
    try:
        with logf.open("w", encoding="utf-8") as lf:
            while True:
                if args.max_runtime_sec and (time.time() - start) > args.max_runtime_sec:
                    stop_reason = "max-runtime"; break
                while lines:
                    ln = lines.pop(0)
                    if (MARKER + " ") in ln:
                        try: obj = json.loads(ln.split(MARKER + " ", 1)[1])
                        except Exception: obj = {"tag": "?", "d": {"raw": ln}}
                        tag = obj.get("tag", "?"); d = obj.get("d", {})
                        lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {tag} {json.dumps(d, ensure_ascii=False)}\n"); lf.flush()
                        print(f"  [{tag}] " + json.dumps(d, ensure_ascii=False)[:300])
                        if tag in ("AUCTION_END", "STOP_MAX_PRICE", "STOP_NO_DEPOSIT", "FATAL") or str(tag).startswith("STOP"):
                            stop_reason = tag
                        if tag == "FATAL": break
                    elif "Failed to load script" in ln or "Failed to attach" in ln:
                        print(f"  [FRIDA-ERR] {ln.strip()}", file=sys.stderr); lf.write(ln + "\n"); stop_reason = "frida-error"; break
                if stop_reason: break
                time.sleep(0.15)
    except KeyboardInterrupt:
        stop_reason = "interrupted"; print("\n  [interrupted]")
    finally:
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
        try: tmp.unlink()
        except Exception: pass
    print(f"\n== STOP ({stop_reason}) ==  log -> {logf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
