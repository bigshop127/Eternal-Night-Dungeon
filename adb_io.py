"""背景操控（2026-11-22）：跟水果2048輔助器一樣，畫面用 adb 從模擬器裡面抓、點擊／拖地圖用 adb 送進模擬器，
完全不碰滑鼠、不看桌面——模擬器被別的視窗蓋住、甚至縮到最小都照跑，你可以同時用電腦做別的事（打英雄聯盟也行）。

跟原本「螢幕擷取＋SendInput 點滑鼠」的差別只在這一層：Engine 拿到的仍然是一個「範圍」(Region) 跟範圍內的畫面，
這裡把範圍做成虛擬的 (0, 0, 寬, 高)，畫面是模擬器整個螢幕縮成這個大小（跟你框選的範圍一樣大，樣板與像素門檻都不用改），
點擊座標再等比換算回模擬器的解析度。

實測（11/22，BlueStacks「1」＝127.0.0.1:5555、1280×720、視窗縮到最小）：原始格式 screencap 約 0.15 秒、畫面是即時的。
BlueStacks 要在「設定 → 進階 → Android 偵錯橋（ADB）」打開（這台已經打開了，水果2048那時候開的）。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
from typing import Callable, List, Optional, Tuple

import numpy as np
from PIL import Image

import vision_state as V

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0     # 不然每呼叫一次 adb 就閃一個黑色主控台視窗
_DEVICE_LINE = re.compile(r"^(\S+)\s+(\w+)")

# BlueStacks 多開每個實例 +10（5555＝「1」、5565＝「2」…）；其他模擬器常見的埠也順便掃
_COMMON_PORTS = (5555, 5565, 5575, 5585, 5595, 7555, 16384, 16416, 62001, 62025)
_BLUESTACKS_CONF = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "BlueStacks_nxt", "bluestacks.conf")
_PORT_LINE = re.compile(r'^bst\.instance\.[^.]+\.status\.adb_port="(\d+)"', re.M)

DEFAULT_SERIAL = "127.0.0.1:5555"
DEFAULT_WORK_SIZE = (1161, 654)     # 沒框選過範圍時的工作尺寸（11/08 那輪真實使用的範圍大小）


class AdbError(OSError):
    """adb 出問題（找不到 adb、連不上模擬器、截圖失敗…）。是 OSError，Engine 既有的例外處理接得住。"""


def find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    cands = [os.path.expandvars(r"%LOCALAPPDATA%\Android\platform-tools\adb.exe")]
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        cands += [os.path.join(root, "BlueStacks_nxt", "HD-Adb.exe"), os.path.join(root, "BlueStacks", "HD-Adb.exe")]
    for c in cands:
        if c and "%" not in c and os.path.exists(c):
            return c
    raise AdbError("找不到 adb.exe。請安裝 Android Platform-Tools，或確認 BlueStacks 有裝在預設位置。")


def run(adb_path: str, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([adb_path, *args], capture_output=True, timeout=timeout,
                              creationflags=_CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb 沒回應（{timeout:.0f} 秒逾時）：{' '.join(args)}") from e
    except OSError as e:
        raise AdbError(f"執行 adb 失敗：{e}") from e


def list_devices(adb_path: str) -> List[Tuple[str, str]]:
    out = run(adb_path, "devices").stdout.decode("utf-8", "replace")
    devs = []
    for line in out.splitlines()[1:]:
        m = _DEVICE_LINE.match(line.strip())
        if m:
            devs.append((m.group(1), m.group(2)))
    return devs


def _port_open(serial: str, timeout: float = 0.3) -> bool:
    """先用 socket 看埠有沒有人在聽：adb connect 對沒開的埠要等好幾秒才放棄。"""
    host, _, port = serial.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def try_connect(adb_path: str, serial: str) -> bool:
    if ":" in serial and not _port_open(serial):
        return False
    if ":" in serial:
        try:
            run(adb_path, "connect", serial, timeout=4.0)
        except AdbError:
            return False
    try:
        return any(s == serial and st == "device" for s, st in list_devices(adb_path))
    except AdbError:
        return False


def emulator_ports() -> List[int]:
    ports: List[int] = []
    try:
        with open(_BLUESTACKS_CONF, encoding="utf-8", errors="replace") as f:
            ports += [int(x) for x in _PORT_LINE.findall(f.read())]
    except OSError:
        pass
    return list(dict.fromkeys(ports + list(_COMMON_PORTS)))


def decode_raw_screencap(data: bytes) -> np.ndarray:
    """`screencap`（不加 -p）的原始輸出：表頭 12 或 16 bytes（寬、高、格式[、色域]）＋RGBA。比 PNG 快很多（不用壓縮）。"""
    if len(data) < 12:
        raise AdbError(f"screencap 回傳的資料太短（{len(data)} bytes）")
    w, h, fmt = (int(v) for v in np.frombuffer(data[:12], dtype="<u4"))
    body = w * h * 4
    header = len(data) - body
    if w <= 0 or h <= 0 or header not in (12, 16) or fmt not in (1, 2):
        raise AdbError(f"看不懂 screencap 的格式（{w}x{h} 格式 {fmt}，{len(data)} bytes）")
    px = np.frombuffer(data, dtype=np.uint8, count=body, offset=header).reshape(h, w, 4)
    return px[..., :3].copy()


class AdbBackend:
    """畫面＋點擊＋拖曳全走 adb。介面對齊 Engine 需要的：grab(region)、click(x, y, hold)、swipe(x0, y0, x1, y1, duration, steps)。

    座標系＝「工作畫面」(work_size，跟你框選的範圍一樣大)：grab 回傳的就是這個大小，click／swipe 收到的也是這個座標，
    送進模擬器前才換算成模擬器的真實解析度。adb 路徑與連哪個模擬器都是第一次用到才決定，失敗丟 AdbError。
    設定的位址連不上時，自動改用開著 ADB 的那個模擬器（多開只有一台開著的情況；好幾台都開著時用 chooser 挑）。
    """

    backend = "adb"

    def __init__(self, serial: Optional[str] = None, work_size: Tuple[int, int] = DEFAULT_WORK_SIZE,
                 adb_path: Optional[str] = None, runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
                 chooser: Optional[Callable[[List[str], Callable[[str], np.ndarray]], Optional[str]]] = None) -> None:
        self.serial_wanted = serial or DEFAULT_SERIAL
        self.work_w, self.work_h = int(work_size[0]), int(work_size[1])
        self._adb_path = adb_path
        self._run = runner or run          # 測試用：換成假的 adb
        self._chooser = chooser
        self._serial: Optional[str] = None
        self._dev_size: Optional[Tuple[int, int]] = None
        self._lock = threading.Lock()      # 截圖跟點擊可能從不同執行緒來（面板「標記畫面」vs 背景偵測）

    # -- 連線 --
    @property
    def serial(self) -> Optional[str]:
        return self._serial

    def region(self) -> V.Region:
        return V.Region(0, 0, self.work_w, self.work_h)

    def _path(self) -> str:
        if self._adb_path is None:
            self._adb_path = find_adb()
        return self._adb_path

    def _resolve(self) -> str:
        if self._serial is not None:
            return self._serial
        path = self._path()
        if try_connect(path, self.serial_wanted):
            self._serial = self.serial_wanted
            return self._serial
        host = self.serial_wanted.rsplit(":", 1)[0] if ":" in self.serial_wanted else "127.0.0.1"
        alive = [f"{host}:{p}" for p in emulator_ports() if try_connect(path, f"{host}:{p}")]
        if not alive:
            raise AdbError(f"連不到模擬器 {self.serial_wanted}，也沒找到其他開著 ADB 的模擬器。"
                           f"確認 BlueStacks 有開、設定 → 進階 → ADB 有打開。")
        pick = alive[0]
        if len(alive) > 1 and self._chooser is not None:
            pick = self._chooser(alive, lambda s: self._screencap_from(path, s)) or alive[0]
        self._serial = pick
        return pick

    def forget(self) -> None:
        """連線出錯後重新找模擬器（下一次截圖時）。"""
        self._serial = None
        self._dev_size = None

    # -- 畫面 --
    def _screencap_from(self, path: str, serial: str) -> np.ndarray:
        proc = self._run(path, "-s", serial, "exec-out", "screencap", timeout=8.0)
        if proc.returncode != 0 or not proc.stdout:
            raise AdbError(f"screencap 失敗：{proc.stderr.decode('utf-8', 'replace').strip() or '沒有輸出'}")
        return decode_raw_screencap(proc.stdout)

    def device_frame(self) -> np.ndarray:
        with self._lock:
            serial = self._resolve()
            try:
                img = self._screencap_from(self._path(), serial)
            except AdbError:
                self.forget()
                raise
        self._dev_size = (img.shape[1], img.shape[0])
        return img

    def grab(self, region: Optional[V.Region] = None) -> np.ndarray:
        img = self.device_frame()
        if img.shape[1] == self.work_w and img.shape[0] == self.work_h:
            return img
        return np.asarray(Image.fromarray(img).resize((self.work_w, self.work_h), Image.BILINEAR))

    # -- 動作 --
    def to_device(self, x: float, y: float) -> Tuple[int, int]:
        if self._dev_size is None:
            self.device_frame()           # 還沒截過圖就先截一張量解析度（不能用猜的）
        dw, dh = self._dev_size
        dx = int(round(x * (dw - 1) / max(1, self.work_w - 1)))
        dy = int(round(y * (dh - 1) / max(1, self.work_h - 1)))
        return min(max(dx, 0), dw - 1), min(max(dy, 0), dh - 1)

    def _shell(self, *args: str, timeout: float = 8.0) -> None:
        with self._lock:
            serial = self._resolve()
            proc = self._run(self._path(), "-s", serial, "shell", *args, timeout=timeout)
        if proc.returncode != 0:
            self.forget()
            raise AdbError(f"adb {' '.join(args[:2])} 失敗：{proc.stderr.decode('utf-8', 'replace').strip()}")

    def click(self, x: int, y: int, hold: float = 0.06, restore_cursor: bool = True) -> None:
        dx, dy = self.to_device(x, y)
        self._shell("input", "tap", str(dx), str(dy))

    def swipe(self, x0: int, y0: int, x1: int, y1: int, duration: float = 0.5, steps: int = 25,
              restore_cursor: bool = True) -> None:
        a, b = self.to_device(x0, y0)
        c, d = self.to_device(x1, y1)
        ms = max(50, int(round(duration * 1000)))
        self._shell("input", "swipe", str(a), str(b), str(c), str(d), str(ms), timeout=8.0 + duration)


class AdbTracker:
    """取代 RegionTracker：範圍永遠是虛擬的 (0, 0, 寬, 高)，不看桌面、不需要框選，也不會被別的視窗「擋住」。"""

    def __init__(self, backend: AdbBackend) -> None:
        self.backend = backend

    def current(self) -> Tuple[Optional[V.Region], Optional[str]]:
        return self.backend.region(), None

    def blocker(self, x: int, y: int) -> Optional[str]:
        return None


__all__ = ["AdbError", "AdbBackend", "AdbTracker", "find_adb", "decode_raw_screencap", "emulator_ports",
           "DEFAULT_SERIAL", "DEFAULT_WORK_SIZE"]
