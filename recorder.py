"""點擊錄製：使用者手動玩一輪，這個模組在背景記下「框選範圍內」的每一次左鍵點擊。

跟水果2048完全不同的地方在這裡——水果2048只需要「送出」滑鼠事件，這裡要「觀察」使用者
真的按了哪裡。用全域滑鼠鉤子（WH_MOUSE_LL）攔截系統層級的左鍵點擊，只留下落在使用者
框選範圍內的那些；範圍外（桌面、其他視窗、輔助器自己的面板）的點擊一律不記。

由 app.py 的面板控制開始／停止（不再是獨立的命令列程式）。

安全設計（跟水果2048的原則一致）：
  * 鉤子回呼（_hook_proc）只做最少的事——把點擊座標丟進 queue 就馬上回傳。低階鉤子是
    同步攔截「全系統」的滑鼠事件，回呼裡做慢動作（截圖、寫檔、查視窗）會讓整台電腦的
    滑鼠卡頓，Windows 甚至會因為逾時把鉤子直接拔掉。過濾、擷取、寫檔全在另一條執行緒。
  * 鉤子有自己專屬的執行緒＋訊息迴圈，跟 Tk 主迴圈互不干擾；停止時用 WM_QUIT 叫它收工。
  * 有一條背景執行緒固定每 150ms 擷取一次範圍當作 rolling 參考幀，點擊發生時「點擊前」
    的畫面幾乎不用等就能拿到（點擊當下才臨時擷取，拿到的其實已經是「點擊後」了）。
  * 程式自己送出的點擊（自動操控）帶有 injected 旗標，預設不記，錄到的一定是真人的操作。

輸出：sessions/<timestamp>/ 底下有 before_NNN.png / after_NNN.png 兩兩一組（都是範圍內的
畫面，不是整個螢幕），以及 clicks.json（每次點擊的時間、範圍內座標與比例、對應圖檔）。
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

import vision_state as V

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_QUIT = 0x0012
WM_USER = 0x0400
PM_NOREMOVE = 0x0000
LLMHF_INJECTED = 0x00000001

POLL_INTERVAL = 0.15     # rolling 參考幀的更新頻率（秒）
AFTER_DELAY = 0.7        # 點擊後等多久再存一張「塵埃落定」的畫面


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD), ("pt", POINT),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM,
                               ctypes.POINTER(MSLLHOOKSTRUCT))

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 64 位元下一定要明講 restype/argtypes：ctypes 對沒宣告的函式預設回傳值當 c_int（32 位元），
# 像 HHOOK / HMODULE 這種指標大小的 handle 會被硬生生截斷，SetWindowsHookExW 收到一個看起來
# 合法、實際是垃圾值的 hMod，掛勾直接失敗（WinError 126「找不到指定的模組」）。
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.argtypes = []

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]

user32.CallNextHookEx.restype = ctypes.c_ssize_t
# 第四個參數宣告成 c_void_p（不是 LPARAM）：HOOKPROC 回呼收到的 lParam 其實是
# POINTER(MSLLHOOKSTRUCT)，要轉呼給下一個鉤子時直接把這個指標傳回去，
# 宣告成整數型的 LPARAM 會讓 ctypes 沒辦法自動把指標塞進去而噴 ArgumentError。
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, ctypes.c_void_p]

user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]

user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]

user32.PeekMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]

user32.TranslateMessage.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]

user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]

user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class RecorderError(RuntimeError):
    pass


class ClickRecorder:
    """start() → 使用者玩 → stop()。tracker 提供「現在的範圍」（跟引擎共用同一份，
    所以模擬器視窗搬動之後錄到的座標仍然是對的）。"""

    def __init__(self, tracker, out_root: str, grabber=None, ignore_injected: bool = True,
                 tag: str = "") -> None:
        self.tracker = tracker
        self.out_root = out_root
        self.grabber = grabber or V.ScreenGrabber()
        self.ignore_injected = ignore_injected
        self.tag = tag

        self.session_dir = ""
        self.records: List[dict] = []
        self._region0: Optional[V.Region] = None
        self._click_q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._hook_thread: Optional[threading.Thread] = None
        self._poller: Optional[threading.Thread] = None
        self._hook_ready = threading.Event()
        self._hook_error: Optional[str] = None
        self._hook_tid = 0
        self._hook_handle = None
        self._hook_ref = None
        self._after_threads: List[threading.Thread] = []
        self._count = 0
        self._t0 = 0.0
        self._active = False

    @property
    def recording(self) -> bool:
        return self._active

    @property
    def count(self) -> int:
        return self._count

    # -------------------------------------------------------------- 開始／停止
    def start(self) -> str:
        """開始錄製，回傳輸出資料夾。範圍不能用（沒框選／視窗縮小）或掛不上鉤子會丟 RecorderError。"""
        if self._active:
            raise RecorderError("已經在錄製了")
        region, err = self.tracker.current()
        if region is None:
            raise RecorderError(err or "目前沒有可用的畫面範圍")
        self._region0 = region

        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_{self.tag}" if self.tag else stamp
        self.session_dir = os.path.join(self.out_root, name)
        os.makedirs(self.session_dir, exist_ok=True)

        self._stop.clear()
        self._hook_ready.clear()
        self._hook_error = None
        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()
        if not self._hook_ready.wait(3.0) or self._hook_error:
            self._stop.set()
            raise RecorderError(self._hook_error or "掛全域滑鼠鉤子逾時")

        self.records = []
        self._count = 0
        self._after_threads = []
        self._t0 = time.time()
        self._active = True
        self._poller = threading.Thread(target=self._poller_loop, daemon=True)
        self._poller.start()
        return self.session_dir

    def stop(self) -> Tuple[int, str]:
        """停止並存檔，回傳 (點擊數, clicks.json 路徑)。沒在錄就回 (0, "")。"""
        if not self._active:
            return 0, ""
        self._active = False
        self._stop.set()
        if self._hook_tid:
            user32.PostThreadMessageW(self._hook_tid, WM_QUIT, 0, 0)
        for t in (self._hook_thread, self._poller):
            if t is not None:
                t.join(timeout=2.0)
        for t in self._after_threads:      # 等「點擊後」的畫面存完，免得最後一筆缺圖
            t.join(timeout=AFTER_DELAY + 1.5)
        return len(self.records), self._save()

    # -------------------------------------------------------------- 鉤子執行緒
    def _hook_loop(self) -> None:
        self._hook_tid = kernel32.GetCurrentThreadId()
        msg = MSG()
        # 先叫系統替這條執行緒建好訊息佇列，之後 stop() 的 PostThreadMessage 才不會丟失
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)

        self._hook_ref = HOOKPROC(self._hook_proc)
        hmod = kernel32.GetModuleHandleW(None)
        handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_ref, hmod, 0)
        if not handle:
            self._hook_error = f"掛不上全域滑鼠鉤子（WinError {ctypes.get_last_error()}）"
            self._hook_ready.set()
            return
        self._hook_handle = handle
        self._hook_ready.set()
        try:
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(handle)
            self._hook_handle = None

    def _hook_proc(self, nCode, wParam, lParam):
        if nCode >= 0 and wParam == WM_LBUTTONDOWN:
            info = lParam.contents
            if not (self.ignore_injected and info.flags & LLMHF_INJECTED):
                try:
                    self._click_q.put_nowait((info.pt.x, info.pt.y, time.time()))
                except Exception:
                    pass
        return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

    # -------------------------------------------------------------- 背景執行緒
    def _poller_loop(self) -> None:
        """固定週期更新 rolling 參考幀，並處理 queue 裡的點擊。"""
        while not self._stop.is_set():
            region, _ = self.tracker.current()
            frame = None
            if region is not None:
                try:
                    frame = self.grabber.grab(region)
                except Exception:
                    frame = None
            try:
                x, y, t = self._click_q.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                continue
            self._handle_click(x, y, t, region, frame)

    def _inside(self, x: int, y: int, region: V.Region) -> bool:
        if not (region.left <= x < region.right and region.top <= y < region.bottom):
            return False
        # 那一點最上層如果是輔助器自己的視窗（標記對話框）、或別的程式蓋在遊戲上的視窗
        # （瀏覽器、別的遊戲），就不算玩家在玩遊戲
        try:
            return self.tracker.blocker(x, y) is None
        except OSError:
            return True

    def _handle_click(self, x: int, y: int, t: float, region: Optional[V.Region],
                      before: Optional[np.ndarray]) -> None:
        if region is None or not self._inside(x, y, region):
            return
        rel_x, rel_y = x - region.left, y - region.top

        self._count += 1
        idx = self._count
        before_name = f"before_{idx:03d}.png"
        after_name = f"after_{idx:03d}.png"
        if before is not None:
            Image.fromarray(before).save(os.path.join(self.session_dir, before_name))

        self.records.append({
            "index": idx,
            "t": round(t - self._t0, 3),
            "screen_xy": [x, y],
            "region_rel_xy": [rel_x, rel_y],
            # 比例是跟解析度無關的座標，之後寫進規則就直接用這個
            "region_frac_xy": [round(rel_x / max(1, region.width - 1), 4),
                               round(rel_y / max(1, region.height - 1), 4)],
            "before": before_name if before is not None else None,
            "after": after_name,
        })

        def _capture_after() -> None:
            time.sleep(AFTER_DELAY)
            try:
                now_region, _ = self.tracker.current()
                frame = self.grabber.grab(now_region or region)
                Image.fromarray(frame).save(os.path.join(self.session_dir, after_name))
            except Exception:
                pass

        th = threading.Thread(target=_capture_after, daemon=True)
        self._after_threads.append(th)
        th.start()

    def _save(self) -> str:
        r = self._region0
        data = {
            "region": r.to_dict() if r else None,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": round(time.time() - self._t0, 1),
            "clicks": self.records,
        }
        path = os.path.join(self.session_dir, "clicks.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path


__all__ = ["ClickRecorder", "RecorderError"]
