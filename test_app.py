"""app.py 的驗證：面板停靠、框選遮罩、標記對話框、自動操控、錄製按鈕、緊急停止。

會開真的 Tk 視窗（面板、遮罩、對話框），但擷取來源／點擊函式／錄製器都是假的：
不會去點任何東西、不會掛全域滑鼠鉤子，設定與樣板寫在暫存資料夾。

    python test_app.py
"""

import gc
import os
import shutil
import sys
import tempfile
import threading
import time
import types

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_app_test_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX   # 一定要在 import engine／app 之前

import tkinter as tk  # noqa: E402

import control as C  # noqa: E402
import engine as E  # noqa: E402
import vision_state as V  # noqa: E402
import app as A  # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def scene(kind):
    img = np.full((160, 260, 3), 30, dtype=np.uint8)
    if kind == "A":
        img[10:80, 10:120] = (230, 230, 230)
    elif kind == "B":
        img[80:150, 140:250] = (220, 40, 40)
    return img


class FakeGrabber:
    backend = "fake"

    def __init__(self):
        self.image = scene("A")

    def grab(self, region):
        return self.image.copy()


class FakeRecorder:
    def __init__(self, tracker, out_root, **kw):
        self.recording = False
        self.count = 0
        self.stopped_with = None

    def start(self):
        self.recording = True
        return os.path.join(SANDBOX, "sessions", "fake")

    def stop(self):
        self.recording = False
        self.stopped_with = self.count
        return self.count, os.path.join(SANDBOX, "sessions", "fake", "clicks.json")


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def wait_for(app, cond, timeout=3.0):
    """視窗搬動是非同步的，系統忙的時候可能要好幾百毫秒才生效，不能只等固定時間。"""
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def drag(overlay, x0, y0, x1, y1):
    cv = overlay.canvas
    cv.event_generate("<ButtonPress-1>", x=x0, y=y0)
    cv.event_generate("<B1-Motion>", x=(x0 + x1) // 2, y=(y0 + y1) // 2)
    cv.event_generate("<B1-Motion>", x=x1, y=y1)
    cv.event_generate("<ButtonRelease-1>", x=x1, y=y1)


def main():
    real_panic = C.panic_pressed
    real_anchor = E.RegionTracker.make_anchor
    real_recorder = A.R.ClickRecorder
    real_ask, real_warn = A.messagebox.askyesno, A.messagebox.showwarning
    C.panic_pressed = lambda key="F8": False
    E.RegionTracker.make_anchor = staticmethod(lambda region: None)   # 不依賴真實桌面上剛好有什麼視窗
    A.R.ClickRecorder = FakeRecorder
    app = None
    try:
        app = A.AssistApp()
        run(app)
    finally:
        C.panic_pressed = real_panic
        E.RegionTracker.make_anchor = real_anchor
        A.R.ClickRecorder = real_recorder
        A.messagebox.askyesno, A.messagebox.showwarning = real_ask, real_warn
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if _failures:
        print(f"❌ {len(_failures)} 項失敗：{_failures}")
        return 1
    print("✅ 全部通過")
    return 0


def run(app):
    grab = FakeGrabber()
    clicks = []
    app.engine.grabber = grab
    app.engine._click = lambda x, y, hold=0.06: clicks.append((x, y))
    app.cfg.poll_ms = 50
    app.cfg.action_interval = 0.0
    app.cfg.retry_after = 0.5
    app.update()

    print("1) 面板停靠在螢幕右邊")
    left, top, right, bottom = work_area = A.work_area()
    l, r = app.winfo_rootx(), app.winfo_rootx() + app.winfo_width()
    check("面板貼著工作區右緣", right - r <= 24 and r <= right + 16, f"面板右緣 {r}，工作區右緣 {right}")
    check("面板在螢幕右半邊", l > (left + right) // 2, f"面板左緣 {l}")
    check("面板不超出工作區高度", app.winfo_rooty() + app.winfo_height() <= bottom + 8,
          f"底 {app.winfo_rooty() + app.winfo_height()} / {bottom}")
    check("置頂", bool(app.attributes("-topmost")))
    check("一開始沒框選：顯示尚未框選", app.state_name.cget("text") == "尚未框選範圍")

    print("2) 沒框選範圍就不能標記／自動／錄製")
    pump(app, 0.3)
    app.toggle_autoplay()
    check("開始自動被擋並說明", not app.engine.autoplay and "框選" in app.status.cget("text"))
    app.toggle_record()
    check("錄製被擋並說明", not (app.recorder and app.recorder.recording) and "框選" in app.status.cget("text"))
    app.open_labeler()
    check("標記被擋並說明", "框選" in app.status.cget("text") or "還沒" in app.status.cget("text"))

    print("3) 框選範圍遮罩")
    app.start_select()
    pump(app, 0.3)
    ov = app.overlay
    check("遮罩開了、背景偵測暫停", ov is not None and app.worker.paused)
    check("遮罩蓋滿整個桌面", (ov.winfo_width(), ov.winfo_height()) == (ov.screen.width, ov.screen.height)
          or abs(ov.winfo_width() - ov.screen.width) <= 2)
    drag(ov, 5, 5, 20, 20)
    app.update()
    check("框太小：不接受並提示", ov.sel is None
          and "太小" in ov.canvas.itemcget(ov.error_id, "text"))
    check("還沒框好按確定：不會結束", (ov.confirm(), app.overlay is ov)[1])
    drag(ov, 200, 150, 460, 310)
    app.update()
    check("拖出的框留著等確認", ov.sel == [200, 150, 460, 310], str(ov.sel))
    ov._key(types.SimpleNamespace(keysym="Right", state=0))
    ov._key(types.SimpleNamespace(keysym="Down", state=0x4))
    check("方向鍵平移（Ctrl＝10px）", ov.sel == [201, 160, 461, 320], str(ov.sel))
    ov._key(types.SimpleNamespace(keysym="Right", state=0x1))
    ov._key(types.SimpleNamespace(keysym="Down", state=0x1 | 0x4))
    check("Shift＋方向鍵改大小、左上角不動", ov.sel == [201, 160, 462, 330], str(ov.sel))
    ov._key(types.SimpleNamespace(keysym="Left", state=0x1 | 0x4))
    ov._key(types.SimpleNamespace(keysym="Left", state=0x1 | 0x4))
    ov._key(types.SimpleNamespace(keysym="Left", state=0x1 | 0x4))
    check("縮小不會小過最小尺寸", ov.sel[2] - ov.sel[0] >= ov.MIN_SIZE, str(ov.sel))
    drag(ov, 300, 200, 560, 360)      # 重新拖曳＝重框
    app.update()
    check("重新拖曳可重框", ov.sel == [300, 200, 560, 360])
    sx, sy = ov.screen.left, ov.screen.top
    ov.confirm()
    pump(app, 0.4)
    check("確定後遮罩關掉、背景偵測恢復", app.overlay is None and not app.worker.paused)
    want = {"left": sx + 300, "top": sy + 200, "width": 260, "height": 160}
    check("範圍存進設定（螢幕座標）", app.cfg.region == want, str(app.cfg.region))
    check("設定檔真的寫進硬碟", E.Config.load().region == want)
    check("引擎範圍同步", app.engine.tracker.current()[0] == V.Region(**want))
    check("資訊列顯示範圍大小", "260×160" in app.region_info.cget("text"))
    check("提示接著標記畫面", "標記畫面" in app.status.cget("text") or app.state_name.cget("text") == "還沒教過畫面")

    app.start_select()
    pump(app, 0.2)
    app.overlay.cancel()
    pump(app, 0.2)
    check("Esc 取消：範圍不變、偵測恢復", app.cfg.region == want and app.overlay is None and not app.worker.paused)

    print("4) 預覽只顯示範圍內的畫面")
    pump(app, 0.4)
    check("樣板庫空的時顯示還沒教過", app.state_name.cget("text") == "還沒教過畫面")
    check("預覽有畫面", app._photo is not None)
    check("預覽尺寸等比例縮進框裡", app._photo.width() <= A.PREVIEW_W and app._photo.height() <= A.PREVIEW_H
          and abs(app._photo.width() / app._photo.height() - 260 / 160) < 0.05,
          f"{app._photo.width()}×{app._photo.height()}")

    print("5) 標記對話框")
    app.open_labeler()
    pump(app, 0.3)
    dlg = [w for w in app.winfo_children() if isinstance(w, A.LabelDialog)][0]
    check("開對話框時偵測暫停", app.worker.paused)
    dlg.name_var.set("")
    dlg._save()
    check("沒取名：擋下", "取個名字" in dlg.error.cget("text"))
    dlg.name_var.set("a/b")
    dlg._save()
    check("非法名稱：擋下", "不能包含" in dlg.error.cget("text"))
    dlg.name_var.set("畫面A")
    dlg._save()
    check("要點擊卻沒點位置：擋下", "點一下" in dlg.error.cget("text"))
    dlg._pick(types.SimpleNamespace(x=dlg.cw // 2, y=dlg.ch // 4))
    check("點圖設定目標", dlg.target is not None and abs(dlg.target[0] - 0.5) < 0.01 and abs(dlg.target[1] - 0.25) < 0.02,
          str(dlg.target))
    dlg._save()
    pump(app, 0.4)
    check("存檔後對話框關掉、偵測恢復", not dlg.winfo_exists() and not app.worker.paused)
    check("樣板與規則寫進引擎", app.engine.db.count_for("畫面A") == 1
          and app.engine.rules.get("畫面A")["action"] == "click")
    check("已存到硬碟", os.path.exists(E.RULES_PATH) and os.path.exists(os.path.join(E.TEMPLATES_DIR, "index.json")))
    pump(app, 0.5)
    check("面板認得出這個畫面", app.state_name.cget("text") == "畫面A", app.state_name.cget("text"))
    check("顯示動作說明", "點擊" in app.state_hint.cget("text"), app.state_hint.cget("text"))

    grab.image = scene("B")
    app.open_labeler()
    pump(app, 0.2)
    dlg = [w for w in app.winfo_children() if isinstance(w, A.LabelDialog)][0]
    dlg.name_var.set("畫面B")
    dlg._on_name()
    dlg.action_var.set("wait")
    dlg._save()
    pump(app, 0.3)
    check("「只辨識不動作」也能存", app.engine.rules.get("畫面B") == {"action": "wait"})

    # 再開一次，選既有名稱會帶出原本的動作
    app.open_labeler()
    pump(app, 0.2)
    dlg = [w for w in app.winfo_children() if isinstance(w, A.LabelDialog)][0]
    dlg.name_var.set("畫面A")
    dlg._on_name()
    check("選既有名稱：帶出樣板數與動作", "1 張" in dlg.info.cget("text") and dlg.action_var.get() == "click"
          and dlg.target is not None)
    check("既有名稱可刪除", str(dlg.delete_btn.cget("state")) == "normal")
    dlg.name_var.set("全新的")
    dlg._on_name()
    check("新名稱不能刪除", str(dlg.delete_btn.cget("state")) == "disabled")
    dlg.destroy()
    pump(app, 0.3)

    print("6) 自動操控（假點擊）")
    grab.image = scene("A")
    A.messagebox.askyesno = lambda *a, **k: False
    app.toggle_autoplay()
    check("確認視窗按否：不會開始", not app.engine.autoplay and clicks == [])
    A.messagebox.askyesno = lambda *a, **k: True
    app.toggle_autoplay()
    check("確認後開始", app.engine.autoplay and "停止自動" in app.auto_btn.cget("text"))
    pump(app, 1.0)
    check("點下去了", len(clicks) >= 1)
    region = V.Region(**want)
    check("點在範圍內的正確位置",
          clicks[0] == (region.left + round(0.5 * (region.width - 1)), region.top + round(0.25 * (region.height - 1))),
          str(clicks[:1]))
    check("最近動作有記錄", "點擊" in app.log_label.cget("text") and "畫面A" in app.log_label.cget("text"))
    n = len(clicks)
    grab.image = scene("B")
    pump(app, 1.5)
    check("換到「只辨識」的畫面：不再點", len(clicks) == n)
    app.toggle_autoplay()
    check("按停止：關掉", not app.engine.autoplay and "開始自動" in app.auto_btn.cget("text"))

    print("7) 面板蓋住範圍時不准動")
    app.update_idletasks()
    px, py = app.winfo_rootx(), app.winfo_rooty()
    saved = app.cfg.region, app.engine.tracker.cfg.region
    app.cfg.region = {"left": px + 10, "top": py + 10, "width": 200, "height": 120}
    app.engine.rebuild()
    check("偵測到面板蓋住範圍", app._overlaps_region())
    warned = []
    A.messagebox.showwarning = lambda *a, **k: warned.append(a)
    app.toggle_autoplay()
    check("開始自動被擋並警告", not app.engine.autoplay and len(warned) == 1)
    app.toggle_record()
    check("錄製也被擋", not (app.recorder and app.recorder.recording) and "蓋在" in app.status.cget("text"))
    app.open_labeler()
    check("標記畫面也被擋（不然樣板會學進面板本身）", not any(isinstance(w, A.LabelDialog) for w in app.winfo_children())
          and "蓋在" in app.status.cget("text") and not app.worker.paused)
    app.dock()
    check("dock：範圍在右邊時面板改停左邊", wait_for(app, lambda: app.winfo_rootx() < 300), f"x={app.winfo_rootx()}")
    check("讓開之後不再重疊", wait_for(app, lambda: not app._overlaps_region()),
          f"面板 x={app.winfo_rootx()} y={app.winfo_rooty()} w={app.winfo_width()} h={app.winfo_height()}，"
          f"範圍 {app.cfg.region}")
    app.cfg.region = saved[0]
    app.engine.rebuild()
    app.dock()
    check("範圍離開後回到右邊", wait_for(app, lambda: app.winfo_rootx() > (left + right) // 2),
          f"面板 x={app.winfo_rootx()}，範圍 {app.cfg.region}")

    print("8) 點擊前最後一道檢查（用假的視窗查詢，不依賴真實桌面上剛好有什麼視窗）")
    saved_fns = {n: getattr(C, n) for n in ("window_at", "window_pid", "window_title", "find_window")}
    try:
        pids = {555: os.getpid(), 666: 4321, 999: 8888}   # 555＝自己的面板、666＝別的程式、999＝遊戲
        C.window_at = lambda x, y: 555
        C.window_pid = lambda h: pids[h]
        C.window_title = lambda h: "瀏覽器"
        C.find_window = lambda t: 999
        msg = app._guard_click_target(100, 100)
        check("目標點在自己的面板上：擋下並說明", bool(msg) and "輔助器自己的視窗" in msg, str(msg))
        C.window_at = lambda x, y: 666
        app.cfg.window_anchor = {"title": "遊戲", "dx": 0, "dy": 0, "w": 100, "h": 100}
        msg = app._guard_click_target(100, 100)
        check("別的程式的視窗蓋在遊戲上：擋下並說明", bool(msg) and "瀏覽器" in msg and "蓋在遊戲畫面上" in msg, str(msg))
        C.window_at = lambda x, y: 999
        check("最上層就是遊戲：放行", app._guard_click_target(100, 100) is None)
    finally:
        for n, fn in saved_fns.items():
            setattr(C, n, fn)
        app.cfg.window_anchor = None

    print("9) 錄製按鈕與緊急停止鍵")
    pump(app, 0.2)
    app.toggle_record()
    check("開始錄製", app.recorder is not None and app.recorder.recording and "停止錄製" in app.rec_btn.cget("text"))
    app.recorder.count = 3
    pump(app, 0.3)
    check("點擊數即時更新", "3 次" in app.rec_note.cget("text"))
    app.toggle_autoplay()
    check("錄製中不能開自動", not app.engine.autoplay)
    app.start_select()
    check("錄製中不能重框範圍", app.overlay is None and "錄製" in app.status.cget("text"))
    C.panic_pressed = lambda key="F8": True
    pump(app, 0.3)
    C.panic_pressed = lambda key="F8": False
    check("F8 停止錄製並存檔", not app.recorder.recording and app.recorder.stopped_with == 3)
    check("面板顯示已停止", "已停止" in app.status.cget("text") or "錄製結束" in app.status.cget("text"))
    check("按鈕恢復", "開始錄製" in app.rec_btn.cget("text"))

    grab.image = scene("A")
    app.engine.stop_autoplay()
    A.messagebox.askyesno = lambda *a, **k: True
    pump(app, 0.2)
    app.toggle_autoplay()
    clicks.clear()
    C.panic_pressed = lambda key="F8": True
    pump(app, 0.3)
    C.panic_pressed = lambda key="F8": False
    check("F8 停止自動操控", not app.engine.autoplay and "自動操控" in app.status.cget("text"))

    print("10) 暫停與刪除狀態")
    app.toggle_pause()
    check("暫停：偵測停、按鈕變繼續", app.worker.paused and "繼續" in app.pause_btn.cget("text"))
    app.toggle_pause()
    check("繼續", not app.worker.paused)
    app.forget("畫面B")
    check("刪除狀態：樣板與規則都不見", app.engine.db.count_for("畫面B") == 0 and app.engine.rules.get("畫面B") is None)

    print("11) Tk 物件只在主執行緒被回收（背景執行緒不能碰 Tcl）")
    # 端到端測試抓到的真 bug：對話框關掉後留下循環垃圾，循環 GC 若在背景偵測執行緒觸發，
    # Variable.__del__ 就會在背景執行緒呼叫 Tcl —— 卡住好幾秒，最壞情況跟 UI 執行緒的 lock 互等成死結。
    main_thread = threading.main_thread()
    spied = []
    patched = []
    for cls in (tk.Variable, tk.Image, A.ImageTk.PhotoImage):
        orig = cls.__del__

        def spy(self, _orig=orig):
            spied.append(threading.current_thread() is main_thread)
            try:
                _orig(self)
            except Exception:
                pass
        cls.__del__ = spy
        patched.append((cls, orig))
    try:
        for _ in range(3):
            app.open_labeler()
            pump(app, 0.2)
            dlg = [w for w in app.winfo_children() if isinstance(w, A.LabelDialog)][0]
            dlg.destroy()
            pump(app, 0.1)
        del dlg
        stop_churn = threading.Event()

        def churn():          # 另一條執行緒瘋狂配置循環垃圾：自動 GC 開著的話會在它身上觸發
            while not stop_churn.is_set():
                for _ in range(3000):
                    a = []
                    a.append(a)
                np.zeros((400, 400, 3), dtype=np.uint8)
        th = threading.Thread(target=churn, daemon=True)
        th.start()
        pump(app, A.GC_EVERY + 2.0)
        stop_churn.set()
        th.join()
    finally:
        for cls, orig in patched:
            cls.__del__ = orig
    check("自動 GC 已關掉（改由主執行緒定期回收）", not gc.isenabled())
    check("對話框留下的 Tk 物件確實被回收了", len(spied) > 0, f"{len(spied)} 個")
    check("全部都在主執行緒回收", all(spied), f"{spied.count(False)} 次跑在別的執行緒")

    print("12) 重新開啟：設定、樣板、規則都還在")
    app.engine.stop_autoplay()
    app._on_close()
    app2 = A.AssistApp()
    try:
        app2.update()
        check("範圍還在", app2.cfg.region == want)
        check("樣板與規則還在", app2.engine.db.labels == ["畫面A"] and app2.engine.rules.click_count == 1)
        check("重開後自動操控一定是關的", not app2.engine.autoplay)
    finally:
        app2.engine.stop_autoplay()
        app2._on_close()


if __name__ == "__main__":
    sys.exit(main())
