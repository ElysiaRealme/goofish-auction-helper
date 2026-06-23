#!/usr/bin/env python3
# Recon: dump full bid.get response body via the PROVEN Popen(stdin=PIPE) path.
import argparse, json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from goofish_auction_helper.runtime import PACKAGE, load_config, resolve_adb, resolve_device, resolve_frida_exe

JS = r"""
'use strict';
function emit(o){ console.log('XYRECON ' + JSON.stringify(o)); }
Java.perform(function () {
  try {
    var DCB = Java.use('com.taobao.android.remoteobject.mtopsdk.MtopSDKHandler$DefaultCallBack');
    var JString = Java.use('java.lang.String');
    var n = 0;
    DCB.onMtopResponseFinished.implementation = function (resp) {
      try {
        var api = String(resp.getApi());
        if (api.indexOf('bid.get') !== -1 && n < 3) {
          n++;
          var bd = resp.getBytedata();
          var body = bd ? String(JString.$new(bd, 'UTF-8')) : '';
          emit({ ix: n, api: api, retCode: String(resp.getRetCode()), body: body });
        }
      } catch (e) { emit({ err: String(e) }); }
      return this.onMtopResponseFinished(resp);
    };
    emit({ armed: true });
  } catch (e) { emit({ armed_err: String(e) }); }
});
"""

def sh(c): return subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace").stdout.strip()

parser = argparse.ArgumentParser(description="Dump live bid.get response bodies for hook diagnostics.")
parser.add_argument("--adb")
parser.add_argument("--device")
parser.add_argument("--frida-exe")
args = parser.parse_args()
cfg = load_config(warn=True)
ADB = resolve_adb(args.adb, cfg)
DEVICE = resolve_device(args.device, cfg, ADB)
FRIDA = resolve_frida_exe(args.frida_exe, cfg)

pid = int((sh([ADB,"-s",DEVICE,"shell",f"pidof {PACKAGE}"]).split() or ["0"])[0])
if not pid: sys.exit("Goofish not running")
tmp = Path(tempfile.gettempdir())/f"recon_bidget_{os.getpid()}.js"
tmp.write_text(JS, encoding="utf-8")
proc = subprocess.Popen([str(FRIDA),"-U","-p",str(pid),"-l",str(tmp)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, encoding="utf-8", errors="replace")
lines=[];
def rd():
    for ln in proc.stdout: lines.append(ln.rstrip("\n"))
threading.Thread(target=rd, daemon=True).start()
deadline=time.time()+25; bodies=[]
try:
    while time.time()<deadline and len(bodies)<2:
        while lines:
            ln=lines.pop(0)
            if "XYRECON" in ln:
                try:
                    o=json.loads(ln.split("XYRECON",1)[1].strip())
                    if o.get("body"): bodies.append(o); print(f"[body#{o.get('ix')}] {o['body'][:1200]}")
                    else: print("[meta]", o)
                except Exception as e: print("[parse]", e, ln[:200])
        time.sleep(0.2)
finally:
    try: proc.terminate(); proc.wait(timeout=5)
    except Exception:
        try: proc.kill()
        except Exception: pass
    try: tmp.unlink()
    except Exception: pass
if not bodies: sys.exit("[TIMEOUT] no bid.get body — is the auction page open & polling?")
