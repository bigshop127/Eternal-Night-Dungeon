"""永夜迷城輔助器 —— 主程式。

跟水果2048輔助器同一種用法：一個永遠置頂的小面板，停靠在螢幕右邊，模擬器留在左邊。

    python app.py

流程：
  ① 框選畫面範圍  在桌面上拖曳，框出模擬器裡的遊戲畫面。之後所有辨識、點擊、錄製
                   都只發生在這個範圍裡，範圍外的東西程式完全看不到。
  ② 標記畫面      遊戲停在某個畫面時按一下，幫它取名（例如「地圖總覽」），並在圖上
                   點出「看到這個畫面要點哪裡」。同一個名字可以標很多次（同一個畫面
                   會有不同的樣子），標越多越準。
  ▶ 開始自動      程式看得懂目前畫面、且這個畫面有設定動作，就自己點下去。F8 隨時停止。
  ● 錄製          想讓 Claude 看你怎麼玩的時候用：你自己玩，程式記下範圍內的每一次點擊
                   （連同點擊前後的畫面），存在 sessions/ 底下。
"""

from __future__ import annotations

import ctypes
import gc
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque
from ctypes import wintypes
from typing import List, Optional, Tuple


def enable_dpi_awareness() -> None:
    """必須在建立任何 Tk 視窗之前呼叫。

    Windows 顯示縮放不是 100% 時，tkinter 回報的是邏輯座標、螢幕擷取拿到的是實體像素，
    兩者對不起來會讓框選出來的範圍整個歪掉。宣告 per-monitor DPI aware 之後兩邊就一致了。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


enable_dpi_awareness()

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageTk  # noqa: E402

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import control as C  # noqa: E402
import engine as E  # noqa: E402
import i18n  # noqa: E402
i18n.install_tk(tk, messagebox)   # 英文版：所有 Tk 文字／標題／訊息框經過 i18n.T()
import recorder as R  # noqa: E402
import vision_state as V  # noqa: E402

# ---------------------------------------------------------------- 外觀（跟水果2048同一套）

UI_FONT = "Segoe UI" if i18n.LANG == "en" else "Microsoft JhengHei UI"
BG = "#1c1f24"
BG_PANEL = "#262a31"
FG = "#e8eaed"
FG_DIM = "#9aa0a8"
ACCENT = "#5ac36a"
WARN = "#e0a33e"
ERROR = "#e05c5c"

PREVIEW_W, PREVIEW_H = 372, 224    # 面板裡的即時預覽框；範圍等比例縮進去
LOG_LINES = 6
GC_EVERY = 3.0                     # 主執行緒多久手動回收一次垃圾（秒），原因見 AssistApp.__init__


def btn_style(bg: str = BG_PANEL, fg: str = FG) -> dict:
    return dict(bg=bg, fg=fg, font=(UI_FONT, 10), relief="flat", bd=0,
                padx=12, pady=6, activebackground="#39404a", activeforeground=FG,
                cursor="hand2", highlightthickness=0)


def work_area() -> Tuple[int, int, int, int]:
    """主螢幕扣掉工作列之後的可用範圍 (left, top, right, bottom)。"""
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)


class _Tee:
    """輸出同時寫到原本的 stream 跟 log 檔並立刻 flush。

    用 pythonw 啟動時沒有主控台，錯誤訊息會直接消失；出事只能靠這個檔案事後對照。"""

    def __init__(self, *streams) -> None:
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


def setup_log() -> None:
    path = os.path.join(E.APP_DIR, "app_log.txt")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 300_000:
            os.remove(path)     # 純診斷用，太大就砍掉重來
        f = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 啟動 =====\n")
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)


# ---------------------------------------------------------------- 框選範圍

class RegionOverlay(tk.Toplevel):
    """蓋滿整個桌面的半透明遮罩，讓使用者拖曳框出「模擬器裡的遊戲畫面」。

    拖出框之後不會馬上結束：框還留著，可以用方向鍵微調（Shift＝改大小、Ctrl＝一次 10px），
    按 Enter 確定，或直接重新拖曳重框。一次框對比反覆重來省事。
    面板（AssistApp）刻意浮在遮罩上面，讓使用者全程看得到它。
    """

    MIN_SIZE = 40

    def __init__(self, master: tk.Misc, screen: V.Region, on_done) -> None:
        super().__init__(master)
        self.screen = screen
        self.on_done = on_done
        self.drag_start: Optional[Tuple[int, int]] = None
        self.sel: Optional[List[int]] = None       # 畫布座標 [x0, y0, x1, y1]

        self.overrideredirect(True)
        self.geometry(f"{screen.width}x{screen.height}+{screen.left}+{screen.top}")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.4)
        self.configure(bg="#000000")

        self.canvas = tk.Canvas(self, bg="#000000", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        mid = screen.width // 2
        self.canvas.create_text(mid, 54, fill="#ffffff", font=(UI_FONT, 24, "bold"),
                                text="拖曳框出「模擬器裡的遊戲畫面」")
        self.hint_id = self.canvas.create_text(mid, 96, fill="#c8ccd2", font=(UI_FONT, 14), text="")
        self.error_id = self.canvas.create_text(mid, 132, fill="#ff8a8a", font=(UI_FONT, 14, "bold"), text="")
        self._hint()

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Key>", self._key)
        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Return>", lambda _e: self.confirm())
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _hint(self, error: str = "") -> None:
        if self.sel is None:
            text = "只有框起來的範圍會被辨識與操作　　Esc 取消"
        else:
            text = "方向鍵微調位置（Shift＝改大小，Ctrl＝一次 10px）　Enter 確定　重新拖曳可重框　Esc 取消"
        self.canvas.itemconfig(self.hint_id, text=text)
        self.canvas.itemconfig(self.error_id, text=error)

    def _redraw(self) -> None:
        self.canvas.delete("sel")
        if self.sel is None:
            return
        x0, y0, x1, y1 = self.sel
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#5ac36a", width=2, tags="sel")
        label = f"{x1 - x0} × {y1 - y0}"
        ty = y1 + 16 if y1 + 34 < self.screen.height else y0 - 16
        self.canvas.create_text((x0 + x1) // 2, ty, text=label, fill="#5ac36a",
                                font=(UI_FONT, 13, "bold"), tags="sel")

    def _press(self, ev) -> None:
        self.focus_force()
        self.drag_start = (ev.x, ev.y)
        self.sel = None
        self._redraw()
        self._hint()

    def _drag(self, ev) -> None:
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        self.sel = [min(x0, ev.x), min(y0, ev.y), max(x0, ev.x), max(y0, ev.y)]
        self._redraw()

    def _release(self, ev) -> None:
        if self.drag_start is None:
            return
        self.drag_start = None
        if self.sel is None or (self.sel[2] - self.sel[0] < self.MIN_SIZE
                                or self.sel[3] - self.sel[1] < self.MIN_SIZE):
            self.sel = None
            self._redraw()
            self._hint("框太小了，請沿著遊戲畫面的邊緣重新拖一次")
            return
        self._hint()

    def _key(self, ev) -> None:
        if self.sel is None:
            return
        step = 10 if ev.state & 0x4 else 1
        dx = {"Left": -step, "Right": step}.get(ev.keysym, 0)
        dy = {"Up": -step, "Down": step}.get(ev.keysym, 0)
        if not (dx or dy):
            return
        x0, y0, x1, y1 = self.sel
        if ev.state & 0x1:      # Shift：動右下角＝改大小，左上角固定
            x1 = max(x0 + self.MIN_SIZE, x1 + dx)
            y1 = max(y0 + self.MIN_SIZE, y1 + dy)
        else:
            x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        self.sel = [x0, y0, x1, y1]
        self._redraw()

    def confirm(self) -> None:
        if self.sel is None:
            self._hint("還沒框出範圍——請先拖曳")
            return
        x0, y0, x1, y1 = self.sel
        region = V.Region(self.screen.left + x0, self.screen.top + y0, x1 - x0, y1 - y0)
        self.destroy()
        self.on_done(region)

    def cancel(self) -> None:
        self.destroy()
        self.on_done(None)


# ---------------------------------------------------------------- 標記畫面

class LabelDialog(tk.Toplevel):
    """把「現在範圍內的畫面」存成某個狀態的樣板，並設定這個狀態的動作。"""

    MAX_W, MAX_H = 640, 380

    def __init__(self, app: "AssistApp", image: np.ndarray, guess: Optional[str]) -> None:
        super().__init__(app)
        self.app = app
        self.image = image
        self.target: Optional[Tuple[float, float]] = None

        self.title("標記這個畫面")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        h, w = image.shape[:2]
        scale = min(self.MAX_W / w, self.MAX_H / h, 1.0)
        self.cw, self.ch = max(1, int(w * scale)), max(1, int(h * scale))
        self._photo = ImageTk.PhotoImage(Image.fromarray(image).resize((self.cw, self.ch), Image.BILINEAR))

        tk.Label(self, text="這是遊戲現在的畫面。在圖上點一下＝「看到這個畫面時要點的位置」。",
                 bg=BG, fg=FG_DIM, font=(UI_FONT, 10)).pack(anchor="w", padx=16, pady=(14, 6))
        self.canvas = tk.Canvas(self, width=self.cw, height=self.ch, bg="#12141a",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=16)
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.bind("<Button-1>", self._pick)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(row, text="這個畫面叫做：", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(side="left")
        self.name_var = tk.StringVar(value=guess or "")
        self.combo = ttk.Combobox(row, textvariable=self.name_var, width=30,
                                  values=app.engine.db.labels, font=(UI_FONT, 10))
        self.combo.pack(side="left", padx=(6, 0))
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._on_name())
        self.combo.bind("<KeyRelease>", lambda _e: self._on_name())

        self.info = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), anchor="w")
        self.info.pack(fill="x", padx=16, pady=(4, 0))

        self.action_var = tk.StringVar(value="click")
        acts = tk.Frame(self, bg=BG)
        acts.pack(fill="x", padx=16, pady=(8, 0))
        for value, text in (("click", "看到這個畫面就點一下（在上面的圖點出位置）"),
                            ("wait", "只辨識、不動作（戰鬥動畫等，等它自己過去）"),
                            ("map", "地城總覽：自動找節點圖釘（沒圖釘就看隊伍前面）、依優先序點一個，都沒有就拖地圖")):
            tk.Radiobutton(acts, text=text, value=value, variable=self.action_var,
                           bg=BG, fg=FG, selectcolor=BG_PANEL, activebackground=BG,
                           activeforeground=FG, font=(UI_FONT, 10), anchor="w",
                           command=self._draw_marker).pack(anchor="w")

        self.error = tk.Label(self, text="", bg=BG, fg=ERROR, font=(UI_FONT, 10), anchor="w",
                              wraplength=self.cw)
        self.error.pack(fill="x", padx=16, pady=(6, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=14)
        tk.Button(bar, text="存檔", command=self._save, **btn_style(bg="#2f6b38", fg="#dff5e2")).pack(side="left")
        tk.Button(bar, text="取消", command=self.destroy, **btn_style()).pack(side="left", padx=6)
        self.delete_btn = tk.Button(bar, text="刪除這個狀態", command=self._delete,
                                    **btn_style(bg="#5a2b2b", fg="#f7dede"))
        self.delete_btn.pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        self._on_name()
        self._place()

    def _place(self) -> None:
        """放在面板左邊的空地，不擋住面板。"""
        self.update_idletasks()
        panel_left = self.app.winfo_rootx()
        x = max(0, (panel_left - self.winfo_reqwidth()) // 2)
        self.geometry(f"+{x}+{60}")

    def _on_name(self) -> None:
        name = self.name_var.get().strip()
        rule = self.app.engine.rules.get(name)
        n = self.app.engine.db.count_for(name)
        if n:
            self.info.config(text=f"「{name}」已有 {n} 張樣板　目前動作：{self.app.engine.rules.describe(name)}")
            if rule is not None:
                if rule["action"] == "click":
                    self.action_var.set("click")
                    self.target = (rule["x"], rule["y"])
                elif rule["action"] == "map":
                    self.action_var.set("map")
                    self.target = None
                else:
                    self.action_var.set("wait")
                    self.target = None
            self.delete_btn.config(state="normal")
        else:
            self.info.config(text="新的畫面名稱" if name else "")
            self.delete_btn.config(state="disabled")
        self._draw_marker()

    def _pick(self, ev) -> None:
        self.target = (min(1.0, max(0.0, ev.x / max(1, self.cw - 1))),
                       min(1.0, max(0.0, ev.y / max(1, self.ch - 1))))
        self.action_var.set("click")
        self.error.config(text="")
        self._draw_marker()

    def _draw_marker(self) -> None:
        self.canvas.delete("marker")
        if self.target is None or self.action_var.get() != "click":
            return
        x, y = self.target[0] * (self.cw - 1), self.target[1] * (self.ch - 1)
        r = 11
        self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="#ff5050", width=2, tags="marker")
        self.canvas.create_line(x - r - 5, y, x + r + 5, y, fill="#ff5050", width=2, tags="marker")
        self.canvas.create_line(x, y - r - 5, x, y + r + 5, fill="#ff5050", width=2, tags="marker")

    def _save(self) -> None:
        name = self.name_var.get().strip()
        err = E.label_error(name)
        click = None
        if err is None and self.action_var.get() == "click":
            if self.target is None:
                err = "請在上面的圖點一下要點的位置，或改選「只辨識、不動作」"
            else:
                click = self.target
        if err:
            self.error.config(text=err)
            return
        self.app.teach(name, self.image, click, overview=self.action_var.get() == "map")
        self.destroy()

    def _delete(self) -> None:
        name = self.name_var.get().strip()
        if not self.app.engine.db.count_for(name):
            return
        if messagebox.askyesno("刪除狀態", f"要把「{name}」的所有樣板和動作都刪掉嗎？", parent=self):
            self.app.forget(name)
            self.destroy()


class RouteDialog(tk.Toplevel):
    """固定路線（route.py）的進度：兩隊各自已經走完幾步。輔助器停手後你自己點了幾步、或進度跟遊戲對不上時用。"""

    def __init__(self, app: "AssistApp") -> None:
        super().__init__(app)
        self.app = app
        r = app.engine.route
        self.title("路線進度")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        tk.Label(self, text="兩隊各自「已經走完」幾步（0＝還在起點）。照路線核對圖的編號算，裝置也算一步。",
                 bg=BG, fg=FG_DIM, font=(UI_FONT, 10), wraplength=560, justify="left").pack(anchor="w", padx=16, pady=(14, 6))
        self.enabled = tk.BooleanVar(value=r.enabled)
        tk.Checkbutton(self, text="照固定路線走（關掉＝全程照原本的規則選節點）", variable=self.enabled,
                       bg=BG, fg=FG, selectcolor=BG_PANEL, activebackground=BG, activeforeground=FG,
                       font=(UI_FONT, 10)).pack(anchor="w", padx=16)

        cols = tk.Frame(self, bg=BG)
        cols.pack(fill="x", padx=16, pady=(8, 0))
        self.vars = {}
        self.next_labels = {}
        for side, name in ((E.O.LEFT, "左隊（野蠻人）"), (E.O.RIGHT, "右隊（法師）")):
            col = tk.Frame(cols, bg=BG)
            col.pack(side="left", fill="y", padx=(0, 16))
            row = tk.Frame(col, bg=BG)
            row.pack(anchor="w")
            tk.Label(row, text=f"{name} 已走完", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(side="left")
            var = tk.IntVar(value=r.idx[side])
            tk.Spinbox(row, from_=0, to=r.total(side), width=4, textvariable=var, font=(UI_FONT, 10),
                       command=self._refresh).pack(side="left", padx=6)
            var.set(r.idx[side])       # Tk 的 Spinbox 建立時會把變數設回 from_，要再設一次
            tk.Label(row, text=f"/ {r.total(side)} 步", bg=BG, fg=FG_DIM, font=(UI_FONT, 10)).pack(side="left")
            self.vars[side] = var
            nl = tk.Label(col, text="", bg=BG, fg=ACCENT, font=(UI_FONT, 9), anchor="w")
            nl.pack(anchor="w", pady=(2, 4))
            self.next_labels[side] = nl
            tk.Label(col, text="\n".join(r.step_list(side)), bg="#15181d", fg=FG_DIM, font=(UI_FONT, 9),
                     justify="left", anchor="nw", padx=8, pady=4).pack(anchor="w")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=14)
        tk.Button(bar, text="存檔", command=self._save, **btn_style(bg="#2f6b38", fg="#dff5e2")).pack(side="left")
        tk.Button(bar, text="取消", command=self.destroy, **btn_style()).pack(side="left", padx=6)
        tk.Button(bar, text="全部歸零（新的一輪）", command=self._zero, **btn_style()).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self._refresh()
        self.update_idletasks()
        x = max(0, (app.winfo_rootx() - self.winfo_reqwidth()) // 2)
        self.geometry(f"+{x}+60")

    def _value(self, side: str) -> int:
        try:
            return int(self.vars[side].get())
        except (tk.TclError, ValueError):
            return 0

    def _refresh(self) -> None:
        r = self.app.engine.route
        for side, nl in self.next_labels.items():
            i = self._value(side)
            steps = r.route[side]
            nl.config(text=f"下一步：{steps[i].name(side)} {steps[i].what()}" if 0 <= i < len(steps) else "這隊走完了")

    def _zero(self) -> None:
        for v in self.vars.values():
            v.set(0)
        self._refresh()

    def _save(self) -> None:
        with self.app.lock:
            r = self.app.engine.route
            r.set_progress(self._value(E.O.LEFT), self._value(E.O.RIGHT))
            self.app.cfg.route_enabled = bool(self.enabled.get())
            self.app.cfg.save()
            r.enabled = self.app.cfg.route_enabled
            self.app.engine.planner.reset()
        self.app.flash("路線進度已更新：" + r.summary(), 8)
        self.app._sync_route()
        self.destroy()


# ---------------------------------------------------------------- 主面板

class AssistApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = E.Config.load()
        self.engine = E.Engine(self.cfg, guard=self._guard_click_target)
        self.lock = threading.Lock()
        self.queue: "queue.Queue[E.Status]" = queue.Queue()
        self.worker = E.Worker(self.engine, self.lock, self.queue)
        self.recorder: Optional[R.ClickRecorder] = None
        self.overlay: Optional[RegionOverlay] = None
        self.last_status: Optional[E.Status] = None
        self._flash_until = 0.0
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._log: "deque[str]" = deque(maxlen=LOG_LINES)

        # 垃圾回收只准在主執行緒做。對話框、遮罩關掉後留下的 Tk 物件（StringVar、PhotoImage）
        # 會互相參照成循環垃圾，等循環 GC 來收；而循環 GC 是「誰剛好配置記憶體就在誰的執行緒
        # 觸發」——背景偵測執行緒每輪都在配置畫面陣列，常常就是它。這樣 Variable.__del__ 會在
        # 背景執行緒裡呼叫 Tcl：輕則卡住等主執行緒回應，重則 UI 執行緒正拿著 lock 等背景執行緒
        # 的同時、背景執行緒也在等 UI 執行緒 → 死結（端到端測試實際抓到：背景偵測凍住好幾秒）。
        # 所以關掉自動 GC，改由 _pump 每 GC_EVERY 秒在主執行緒手動 gc.collect()。
        gc.disable()
        self._next_gc = time.monotonic() + GC_EVERY

        self.title("永夜迷城輔助器")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", self.cfg.opacity)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self.overlay.cancel() if self.overlay else None)
        icon = os.path.join(E.APP_DIR, "icon.ico")
        if os.path.exists(icon):
            try:
                self.iconbitmap(icon)
            except tk.TclError:
                pass

        self._build_ui()
        self._force_normal_window()
        self.update()      # 先讓視窗真的顯示出來，dock() 才量得到標題列與邊框的寬度
        self.dock()
        self.worker.start()
        self._pump_job = self.after(80, self._pump)
        if "--autostart" in sys.argv:
            # 啟動就直接開自動（不跳確認框）：給排程／遠端啟動用，例如 `pythonw app.py --autostart`
            self.after(2500, self._autostart)

    def _autostart(self) -> None:
        region, err = self.engine.tracker.current()
        if region is None or len(self.engine.db) == 0 or self._overlaps_region():
            self._push_log(f"--autostart 沒有開成：{err or '畫面範圍或樣板還沒準備好'}")
            return
        with self.lock:
            self.engine.start_autoplay()
            # 程式重開接著跑（鏡頭沒人動過）：鏡頭停在路線記錄裡最後行動的那隊身上
            if self.engine.route.active:
                self.engine.planner._fresh_actor = self.engine.route.last_actor
        self.worker.resume()
        self._sync_auto_note()
        self._push_log("自動操控開始（--autostart）")
        self.flash(f"自動操控開始 —— 按 {self.cfg.panic_key} 可隨時停止", 6)

    # -- 視窗 --
    def _force_normal_window(self) -> None:
        """不管是被什麼方式啟動的，都確保視窗是正常大小、看得到。

        桌面捷徑本身是「最小化」啟動（用來壓掉 PowerShell 黑視窗），這個顯示狀態會經由
        STARTUPINFO 一路繼承到這裡；前景有全螢幕獨佔的遊戲時 Windows 也會把新視窗壓成最小化。
        被壓成最小化的話 Tk 不會完成初始版面計算，還原之後會變成一個小殘塊。
        所以這裡明確還原狀態，再把 geometry 清掉讓 Tk 依內容重新量一次尺寸。
        """
        try:
            self.update_idletasks()
            if self.state() != "normal":
                self.state("normal")
            self.deiconify()
            self.geometry("")
            self.update_idletasks()
        except tk.TclError:
            pass

    def _panel_rect(self, x: int, y: int) -> Tuple[int, int, int, int]:
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        return x, y, x + w, y + h + 40      # 標題列不算在 reqheight 裡，多算一點

    def dock(self) -> None:
        """停靠在螢幕右邊、貼著工作區頂端。範圍剛好被右邊的面板蓋到才改停左邊。"""
        self.update_idletasks()
        left, top, right, bottom = work_area()
        w = self.winfo_reqwidth()
        border = max(0, self.winfo_rootx() - self.winfo_x())
        x_right = right - w - border
        x_left = left - border
        y = top
        x = x_right
        region, _ = self.engine.tracker.current()
        if region is not None and not self.engine.background:
            def hits(px: int) -> bool:
                l, t, r, b = self._panel_rect(px, y)
                return not (r <= region.left or l >= region.right or b <= region.top or t >= region.bottom)
            if hits(x_right) and not hits(x_left):
                x = x_left
        self.geometry(f"+{x}+{y}")

    def _overlaps_region(self) -> bool:
        """面板（含標題列）有沒有蓋在遊戲畫面範圍上。蓋住的話不只點擊會點到自己，
        擷取到的畫面也會被面板本身汙染，辨識就全歪了。背景操控（adb）不看桌面，怎麼蓋都沒關係。"""
        if self.engine.background:
            return False
        region, _ = self.engine.tracker.current()
        if region is None:
            return False
        self.update_idletasks()
        l, r = self.winfo_rootx(), self.winfo_rootx() + self.winfo_width()
        t, b = self.winfo_rooty() - 40, self.winfo_rooty() + self.winfo_height()
        return not (r <= region.left or l >= region.right or b <= region.top or t >= region.bottom)

    def _guard_click_target(self, x: int, y: int) -> Optional[str]:
        """點擊前最後一道檢查（在背景執行緒跑，只用 ctypes，不碰 Tk）：目標點最上層必須是
        遊戲，不能是自己的視窗（面板、標記對話框），也不能是別的程式壓在模擬器上面的視窗。"""
        who = self.engine.tracker.blocker(x, y)
        if who is None:
            return None
        return f"{who}蓋在遊戲畫面上，自動操控已停止。請把它移開（或把模擬器拉到最上層）再開始。"

    # -- 版面 --
    def _build_ui(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 0))
        self.dot = tk.Label(head, text="●", bg=BG, fg=FG_DIM, font=(UI_FONT, 14))
        self.dot.pack(side="left")
        self.state_name = tk.Label(head, text="尚未框選範圍", bg=BG, fg=FG_DIM,
                                   font=(UI_FONT, 20, "bold"), anchor="w")
        self.state_name.pack(side="left", padx=(8, 0))
        self.state_hint = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 10), anchor="w")
        self.state_hint.pack(fill="x", padx=16, pady=(0, 6))

        self.preview = tk.Canvas(self, width=PREVIEW_W, height=PREVIEW_H, bg="#12141a",
                                 highlightthickness=0)
        self.preview.pack(padx=14)
        self.region_info = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), anchor="w",
                                    wraplength=PREVIEW_W, justify="left")
        self.region_info.pack(fill="x", padx=16, pady=(4, 0))

        self.status = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 9),
                               wraplength=PREVIEW_W, justify="left", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(6, 0))

        auto = tk.Frame(self, bg=BG)
        auto.pack(fill="x", padx=16, pady=(10, 0))
        self.auto_btn = tk.Button(auto, text="▶ 開始自動", command=self.toggle_autoplay,
                                  **btn_style(bg="#2f6b38", fg="#dff5e2"))
        self.auto_btn.pack(side="left")
        self.auto_note = tk.Label(auto, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 9))
        self.auto_note.pack(side="left", padx=(10, 0))

        rt = tk.Frame(self, bg=BG)
        rt.pack(fill="x", padx=16, pady=(8, 0))
        tk.Button(rt, text="路線 從頭", command=self.route_restart, **btn_style()).pack(side="left")
        tk.Button(rt, text="調整…", command=self.open_route, **btn_style()).pack(side="left", padx=(6, 0))
        self.route_info = tk.Label(self, text="", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), anchor="w",
                                   wraplength=PREVIEW_W, justify="left")
        self.route_info.pack(fill="x", padx=16, pady=(4, 0))

        tk.Label(self, text="最近動作", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), anchor="w").pack(
            fill="x", padx=16, pady=(10, 0))
        self.log_label = tk.Label(self, text="", bg="#15181d", fg=FG_DIM, font=("Consolas", 9),
                                  anchor="nw", justify="left", height=LOG_LINES, padx=8, pady=4)
        self.log_label.pack(fill="x", padx=16, pady=(2, 0))

        rec = tk.Frame(self, bg=BG)
        rec.pack(fill="x", padx=16, pady=(10, 0))
        self.rec_btn = tk.Button(rec, text="● 開始錄製", command=self.toggle_record, **btn_style())
        self.rec_btn.pack(side="left")
        self.rec_note = tk.Label(rec, text="錄下你自己怎麼玩", bg=BG, fg=FG_DIM, font=(UI_FONT, 9))
        self.rec_note.pack(side="left", padx=(10, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=12)
        self.mode_btn = tk.Button(bar, text="", command=self.toggle_mode, **btn_style())
        self.mode_btn.pack(side="left", padx=2)
        tk.Button(bar, text="① 框選畫面範圍", command=self.start_select, **btn_style()).pack(side="left", padx=2)
        tk.Button(bar, text="② 標記畫面", command=self.open_labeler, **btn_style()).pack(side="left", padx=2)
        self.pause_btn = tk.Button(bar, text="暫停", command=self.toggle_pause, **btn_style())
        self.pause_btn.pack(side="left", padx=2)

        self.footer = tk.Label(self, text="", bg=BG, fg="#5c626b", font=("Consolas", 8))
        self.footer.pack(fill="x", padx=16, pady=(0, 8))

        self._draw_preview(None, None)
        self._sync_region_info()
        self._sync_auto_note()
        self._sync_route()
        self._sync_mode_btn()

    # -- 事件迴圈 --
    def _pump(self) -> None:
        # 緊急停止在 UI 執行緒也檢查一次：背景執行緒點擊的那一瞬間不會回頭看，
        # 這裡每 60ms 檢查一次，按下去的反應快得多。錄製也一併停。
        if C.panic_pressed(self.cfg.panic_key):
            stopped = []
            if self.engine.autoplay:
                self.engine.stop_autoplay()
                stopped.append("自動操控")
            if self.recorder is not None and self.recorder.recording:
                self._stop_record()
                stopped.append("錄製")
            if stopped:
                self._sync_auto_note()
                self.flash(f"已按下 {self.cfg.panic_key}，{'、'.join(stopped)}已停止")

        self._sync_pause_btn()
        if self.recorder is not None and self.recorder.recording:
            self._sync_rec_note()
        if time.monotonic() >= self._next_gc:
            gc.collect()
            self._next_gc = time.monotonic() + GC_EVERY

        latest: Optional[E.Status] = None
        try:
            while True:
                latest = self.queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._render(latest)
        self._pump_job = self.after(60, self._pump)

    def _sync_pause_btn(self) -> None:
        want = "繼續" if self.worker.paused else "暫停"
        if self.pause_btn.cget("text") != want:
            self.pause_btn.config(text=want)

    def _sync_auto_note(self) -> None:
        on = self.engine.autoplay
        self.auto_btn.config(text="■ 停止自動" if on else "▶ 開始自動",
                             bg="#7a3030" if on else "#2f6b38",
                             fg="#f7dede" if on else "#dff5e2")
        done = self.engine.actions_done
        if on:
            self.auto_note.config(text=f"執行中 · 已點 {done} 次 · 按 {self.cfg.panic_key} 隨時停止", fg=ACCENT)
        else:
            what = "背景操控，不會動到滑鼠" if self.engine.background else "開啟後程式會自己點滑鼠"
            self.auto_note.config(text=f"{what}（已點 {done} 次）" if done else what, fg=FG_DIM)

    def _sync_route(self) -> None:
        text = self.engine.route.summary()
        if self.route_info.cget("text") != text:
            self.route_info.config(text=text, fg=ACCENT if self.engine.route.active else FG_DIM)

    def route_restart(self) -> None:
        if not messagebox.askyesno("路線從頭", "把路線進度歸零、從第1段起點重新照路線走？\n\n"
                                               "（新的一輪會自動歸零：開自動時第一個畫面是起始畫面就會從頭。"
                                               "這個按鈕是給自動沒認出來的時候用的。）", parent=self):
            return
        with self.lock:
            self.engine.route.restart()
            self.engine.planner.reset()
        self._sync_route()
        self.flash("路線已歸零，從第1段起點開始", 6)

    def open_route(self) -> None:
        RouteDialog(self)

    def _sync_rec_note(self) -> None:
        rec = self.recorder
        if rec is not None and rec.recording:
            self.rec_btn.config(text="■ 停止錄製", bg="#7a3030", fg="#f7dede")
            self.rec_note.config(text=f"錄製中 · 已記 {rec.count} 次點擊 · 按 {self.cfg.panic_key} 停止", fg=ERROR)
        else:
            self.rec_btn.config(text="● 開始錄製", **{k: v for k, v in btn_style().items()
                                                       if k in ("bg", "fg")})
            self.rec_note.config(text="錄下你自己怎麼玩", fg=FG_DIM)

    def _sync_mode_btn(self) -> None:
        text = "操控：背景 adb" if self.engine.background else "操控：前景滑鼠"
        if self.mode_btn.cget("text") != text:
            self.mode_btn.config(text=text)

    def toggle_mode(self) -> None:
        """切換操控方式：背景（adb，不碰滑鼠、模擬器縮小也能跑）↔ 前景（框選範圍＋點滑鼠，原本的方式）。"""
        if self.recorder is not None and self.recorder.recording:
            self.flash("錄製中不能切換操控方式，請先停止錄製", 6, WARN)
            return
        to_adb = not self.engine.background
        with self.lock:
            self.engine.stop_autoplay()
            self.cfg.control_mode = "adb" if to_adb else "screen"
            self.cfg.save()
            self.engine.apply_backend()
            self.engine.rebuild()
        self._sync_auto_note()
        self._sync_mode_btn()
        self._sync_region_info()
        self.dock()
        if to_adb:
            self.flash("已切到背景操控：畫面跟點擊都走 adb，模擬器被蓋住或縮到最小都照跑，滑鼠可以拿去做別的事。", 10)
        else:
            self.flash("已切回前景操控：照框選的範圍擷取、用滑鼠點（模擬器要在最上層）。", 10)

    def _sync_region_info(self) -> None:
        if self.engine.background:
            a = self.engine.adb
            text = f"背景操控（adb）· 模擬器 {a.serial or a.serial_wanted} · 不用框選，模擬器縮到最小也能跑"
            if self.region_info.cget("text") != text:
                self.region_info.config(text=text)
            return
        region = self.engine.region
        if region is None and self.cfg.region:
            region = V.Region.from_dict(self.cfg.region)
        if region is None:
            self.region_info.config(text="範圍：尚未框選")
            return
        lock = ""
        if self.cfg.window_anchor:
            lock = f" · 已鎖定視窗「{self.cfg.window_anchor['title']}」"
        self.region_info.config(
            text=f"範圍 {region.width}×{region.height}　左上 ({region.left}, {region.top}){lock}")

    def _draw_preview(self, image: Optional[np.ndarray], rule: Optional[dict],
                      overlay: Optional[list] = None) -> None:
        c = self.preview
        c.delete("all")
        if image is None:
            self._photo = None
            c.create_text(PREVIEW_W // 2, PREVIEW_H // 2, fill=FG_DIM, font=(UI_FONT, 11), justify="center",
                          text="程式看到的畫面會顯示在這裡\n（只有你框選的範圍）")
            return
        h, w = image.shape[:2]
        scale = min(PREVIEW_W / w, PREVIEW_H / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        im = Image.fromarray(image).resize((nw, nh), Image.BILINEAR)
        if rule is not None and rule.get("action") == "click":
            d = ImageDraw.Draw(im)
            x, y, r = rule["x"] * (nw - 1), rule["y"] * (nh - 1), 9
            d.ellipse((x - r, y - r, x + r, y + r), outline=(255, 80, 80), width=2)
            d.line((x - r - 4, y, x + r + 4, y), fill=(255, 80, 80), width=2)
            d.line((x, y - r - 4, x, y + r + 4), fill=(255, 80, 80), width=2)
        self._photo = ImageTk.PhotoImage(im)
        c.create_image(PREVIEW_W // 2, PREVIEW_H // 2, image=self._photo)
        # 總覽：把偵測到的節點標出來（青色＝可選但不是這次要點的、紅色＝下一個要點的），文字用 Tk 畫才有中文
        ox, oy = (PREVIEW_W - nw) // 2, (PREVIEW_H - nh) // 2
        for x, y, name, target in overlay or ():
            px, py, r = ox + x * scale, oy + y * scale, 8
            color = "#ff5050" if target else "#5fd7d7"
            c.create_oval(px - r, py - r, px + r, py + r, outline=color, width=2)
            c.create_text(px + r + 3, py - r, text=name, anchor="w", fill=color, font=(UI_FONT, 9, "bold"))

    def _push_log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self._log.append(line)
        self.log_label.config(text="\n".join(self._log))
        print(line)         # 有 tee，會進 app_log.txt：事後查「當時為什麼這樣選」用

    def _render(self, st: E.Status) -> None:
        self.last_status = st
        self._sync_auto_note()
        self._sync_region_info()
        self._sync_route()
        if st.image is not None:
            self._draw_preview(st.image, st.rule, st.overlay)
        elif st.kind == "waiting":
            self._draw_preview(None, None)
        if st.acted:
            self._push_log(st.acted if st.acted.startswith(("總覽", "戰鬥", "路線")) else f"點擊 {st.acted}")

        if st.kind == "ok" and st.label:
            self.dot.config(fg=ACCENT)
            self.state_name.config(text=st.label, fg=FG)
            self.state_hint.config(text=f"→ {self.engine.rules.describe(st.label)}　"
                                        f"（差距 {st.distance:.1f} / 把握 {st.margin:.1f}）",
                                   fg=ACCENT if st.rule and st.rule["action"] in ("click", "map") else FG_DIM)
        elif st.kind == "settling":
            self.dot.config(fg=FG_DIM)
            self.state_name.config(text=st.label or "畫面變動中…", fg=FG_DIM)
            self.state_hint.config(text="畫面變動中，等它穩定…", fg=FG_DIM)
        else:
            text, color = {
                "unknown": ("認不得這個畫面", WARN),
                "empty": ("還沒教過畫面", WARN),
                "waiting": ("等待中" if self.cfg.region else "尚未框選範圍", FG_DIM),
                "stopped": ("已停止", WARN),
                "error": ("出錯了", ERROR),
            }.get(st.kind, ("等待中", FG_DIM))
            self.dot.config(fg=color)
            self.state_name.config(text=text, fg=color if st.kind != "waiting" else FG_DIM)
            self.state_hint.config(text="", fg=FG_DIM)

        if st.kind in ("stopped", "error"):
            self._push_log(st.message)
        if time.time() >= self._flash_until:
            color = {"error": ERROR, "stopped": WARN, "unknown": WARN, "empty": WARN}.get(st.kind, FG_DIM)
            self.status.config(text=st.message, fg=color)
        self.footer.config(
            text=f"樣板 {len(self.engine.db)} 張 / {len(self.engine.db.labels)} 種畫面　"
                 f"動作規則 {len(self.engine.rules)} 條　擷取 {self.engine.grabber.backend}")

    def flash(self, text: str, seconds: float = 4.0, color: str = ACCENT) -> None:
        self.status.config(text=text, fg=color)
        self._flash_until = time.time() + seconds

    def report_callback_exception(self, exc, val, tb) -> None:
        traceback.print_exception(exc, val, tb)     # 有 tee，會進 app_log.txt
        try:
            self.flash(f"內部錯誤：{exc.__name__}: {val}", 20, ERROR)
        except Exception:
            pass

    # -- ① 框選 --
    def start_select(self) -> None:
        if self.engine.background:
            self.flash("背景操控不用框選範圍（畫面直接從模擬器裡面抓）。要改回框選＋點滑鼠，按「操控」切換。", 10)
            return
        if self.recorder is not None and self.recorder.recording:
            self.flash("錄製中不能重框範圍，請先停止錄製", 6, WARN)
            return
        if self.overlay is not None:
            return
        self.engine.stop_autoplay()
        self._sync_auto_note()
        self.worker.pause()
        screen = V.Region(*C.virtual_screen())
        self.flash("在桌面上拖曳，框出模擬器裡的遊戲畫面（Esc 取消）", 999)

        def done(region: Optional[V.Region]) -> None:
            self.overlay = None
            if region is None:
                self.worker.resume()
                self.flash("已取消框選")
                return
            self.apply_region(region)

        self.overlay = RegionOverlay(self, screen, done)
        self.after(60, self.lift)      # 面板浮在遮罩上面，全程看得到

    def apply_region(self, region: V.Region) -> None:
        anchor = E.RegionTracker.make_anchor(region)
        with self.lock:
            self.cfg.region = region.to_dict()
            self.cfg.window_anchor = anchor
            self.cfg.save()
            self.engine.rebuild()
        self.dock()      # 範圍剛好落在面板底下的話，面板會自己讓開
        self.worker.resume()
        self._sync_region_info()

        note = (f" 順便鎖定了視窗「{anchor['title']}」，搬動它不用重框（改變視窗大小要重框）。"
                if anchor else "")
        head = f"範圍存好了（{region.width}×{region.height}）。"
        if self._overlaps_region():
            self.flash(head + f"但右邊的面板蓋到這個範圍了。請把模擬器視窗縮小、擺到螢幕左邊"
                              f"（右邊留約 {self.winfo_width()}px 給面板），再按一次「① 框選畫面範圍」重框。", 20, WARN)
        elif len(self.engine.db) == 0:
            self.flash(head + f"接著切到遊戲畫面，按「② 標記畫面」教它認。{note}", 12)
        else:
            self.flash(head + note, 8)

    # -- ② 標記 --
    def open_labeler(self) -> None:
        if self.overlay is not None:
            return
        if self.recorder is not None and self.recorder.recording:
            self.flash("錄製中請先停止錄製再標記畫面", 6, WARN)
            return
        if self._overlaps_region():
            self.flash("面板蓋在遊戲畫面範圍上，標記下來的畫面會把面板本身也拍進去。"
                       "請先把模擬器視窗縮小、擺到螢幕左邊，再重新框選。", 12, WARN)
            return
        self.engine.stop_autoplay()
        self._sync_auto_note()
        self.worker.pause()
        with self.lock:
            try:
                img = self.engine.snapshot()
                err = ""
            except (ValueError, OSError) as e:
                img, err = None, str(e)
        if img is None:
            self.worker.resume()
            self.flash(err or "抓不到畫面", 10, WARN)
            return
        guess = self.last_status.label if self.last_status and self.last_status.kind == "ok" else None
        dialog = LabelDialog(self, img, guess)
        dialog.bind("<Destroy>", lambda e: self.worker.resume() if e.widget is dialog else None)

    def teach(self, label: str, image: np.ndarray, click: Optional[Tuple[float, float]],
              overview: bool = False) -> None:
        with self.lock:
            added, n = self.engine.teach(label, image, click, overview=overview)
        act = "點擊" if click else "自動選節點" if overview else "不動作"
        self.flash(f"「{label}」" + (f"已加入樣板（共 {n} 張）" if added else f"跟既有樣板很像，沒重複收（共 {n} 張）")
                   + f"，動作：{act}", 8)

    def forget(self, label: str) -> None:
        with self.lock:
            self.engine.forget(label)
        self.flash(f"已刪除「{label}」", 6)

    # -- 自動操控 --
    def toggle_autoplay(self) -> None:
        if self.engine.autoplay:
            self.engine.stop_autoplay()
            self._sync_auto_note()
            self.flash("自動操控已停止")
            return
        if self.recorder is not None and self.recorder.recording:
            self.flash("錄製中不能開自動操控，請先停止錄製", 6, WARN)
            return
        region, err = self.engine.tracker.current()
        if region is None:
            self.flash(err or "請先框選畫面範圍", 8, WARN)
            return
        if len(self.engine.db) == 0:
            self.flash("請先按「② 標記畫面」，程式要看得懂畫面才能自己動", 8, WARN)
            return
        if self.engine.rules.click_count == 0:
            self.flash("還沒有任何「點擊」動作。按「② 標記畫面」時在圖上點出要點的位置。", 8, WARN)
            return
        if self._overlaps_region():
            messagebox.showwarning(
                "面板擋住畫面了",
                "這個面板正蓋在遊戲畫面範圍上。\n\n點擊會點到面板自己，而且擷取到的畫面也會被面板汙染。\n\n"
                "請把模擬器視窗縮小、擺到螢幕左邊（右邊留給面板），再重新框選範圍。", parent=self)
            return
        if self.engine.background:
            msg = (f"接下來程式會在背景透過 adb 自己操作模擬器（不會動到你的滑鼠，模擬器縮到最小也照跑）。\n\n"
                   f"隨時要停止：\n　• 按 {self.cfg.panic_key}（在任何程式裡按都有效）\n　• 或再按一次「■ 停止自動」\n\n要開始嗎？")
        else:
            msg = (f"接下來程式會自己在框選的範圍內點擊滑鼠。\n\n"
                   f"隨時要停止：\n　• 按 {self.cfg.panic_key}（不用切回面板也有效）\n　• 或再按一次「■ 停止自動」\n\n"
                   f"開始前請確認模擬器視窗在最上層、沒有被其他視窗擋住。\n\n要開始嗎？")
        if not messagebox.askyesno("開始自動操控", msg, parent=self):
            return
        self.engine.start_autoplay()
        self.worker.resume()
        self.pause_btn.config(text="暫停")
        self._sync_auto_note()
        self.flash(f"自動操控開始 —— 按 {self.cfg.panic_key} 可隨時停止", 6)

    # -- 錄製 --
    def toggle_record(self) -> None:
        if self.recorder is not None and self.recorder.recording:
            self._stop_record()
            return
        if self.overlay is not None:
            return
        if self.engine.background:
            self.flash("錄製是記你用滑鼠點的位置，要在前景操控才錄得到。按「操控」切回前景再錄。", 8, WARN)
            return
        region, err = self.engine.tracker.current()
        if region is None:
            self.flash(err or "請先框選畫面範圍", 8, WARN)
            return
        if self._overlaps_region():
            self.flash("面板蓋在遊戲畫面範圍上，請先把面板拖到範圍外再錄製", 8, WARN)
            return
        self.engine.stop_autoplay()
        self._sync_auto_note()
        rec = R.ClickRecorder(self.engine.tracker, E.SESSIONS_DIR)
        try:
            folder = rec.start()
        except R.RecorderError as e:
            self.flash(f"無法開始錄製：{e}", 10, ERROR)
            return
        self.recorder = rec
        self._sync_rec_note()
        self._push_log("開始錄製")
        self.flash(f"開始錄製，照平常玩法點就好，玩完按 {self.cfg.panic_key} 或「停止錄製」。存到：{folder}", 10)

    def _stop_record(self) -> None:
        rec = self.recorder
        if rec is None or not rec.recording:
            return
        n, path = rec.stop()
        self._sync_rec_note()
        self._push_log(f"錄製結束，{n} 次點擊")
        self.flash(f"錄製結束，共 {n} 次點擊，已存到 {os.path.dirname(path)}", 15)

    # -- 暫停／關閉 --
    def toggle_pause(self) -> None:
        if self.worker.paused:
            self.worker.resume()
            self.pause_btn.config(text="暫停")
            self.flash("繼續偵測")
        else:
            # 暫停時一併關掉自動操控，不然按「繼續」會冷不防又開始搶滑鼠
            self.engine.stop_autoplay()
            self._sync_auto_note()
            self.worker.pause()
            self.pause_btn.config(text="繼續")
            self.flash("已暫停（畫面不會再更新）", 999)

    def _on_close(self) -> None:
        self.engine.stop_autoplay()
        self._stop_record()
        self.worker.stop()
        self.worker.join(timeout=1.5)     # 等背景執行緒收工，之後 Tk 物件才會全在主執行緒清掉
        try:
            self.after_cancel(self._pump_job)
        except (tk.TclError, AttributeError):
            pass
        self.destroy()
        gc.collect()
        gc.enable()


def main() -> int:
    setup_log()
    app = AssistApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
