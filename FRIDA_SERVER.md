# frida-server binaries

This repository does not commit device-side `frida-server` binaries.

Download the server that matches your host `frida` CLI version and Android CPU architecture from:

<https://github.com/frida/frida/releases>

Example workflow:

```bat
uv run frida --version
adb shell getprop ro.product.cpu.abi
adb push frida-server-<version>-android-<arch> /data/local/tmp/frida-server-<version>-<arch>
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server-<version>-<arch>'"
uv run python main.py frida --frida-server-bin /data/local/tmp/frida-server-<version>-<arch>
```

The host `frida` version and device `frida-server` version must match.
