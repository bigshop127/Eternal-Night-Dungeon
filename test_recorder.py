"""recorder.py 的驗證：真的掛全域滑鼠鉤子，對著另一個行程開的假視窗點幾下。

這支測試會動真實的滑鼠游標、按幾下左鍵——但**只點自己開的假視窗**：每一下之前都先用
window_at() 確認那個座標底下真的是假視窗，不是就整支中止，不會點到遊戲或任何別的東西。

    python test_recorder.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_rec_test_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX

import ctypes  # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)     # 跟 app.py 一樣，座標才對得上
except (AttributeError, OSError):
    pass

import control as C  # noqa: E402
import engine as E  # noqa: E402
import recorder as R  # noqa: E402
import vision_state as V  # noqa: E402

TITLE = "ENDTEST_REC_WIN"
FAKE_SRC = f"""
import tkinter as tk, sys
root = tk.Tk()
root.overrideredirect(True)
root.title("{TITLE}")
root.geometry("420x260+1360+720")
root.attributes("-topmost", True)
tk.Label(root, text="recorder 測試用假視窗", bg="#204060", fg="white").pack(fill="both", expand=True)
root.after(60000, root.destroy)
root.mainloop()
"""

_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def safe_click(hwnd, x, y):
    """只在 (x, y) 底下確實是假視窗時才點。"""
    under = C.window_at(x, y)
    if under != hwnd:
        raise SystemExit(f"中止：({x}, {y}) 底下不是假視窗（是 {under}），不敢點")
    C.click(x, y)


def main():
    fake = subprocess.Popen([sys.executable, "-c", FAKE_SRC])
    try:
        return run()
    finally:
        fake.terminate()
        shutil.rmtree(SANDBOX, ignore_errors=True)


def run():
    hwnd = None
    for _ in range(80):
        h = C.window_at(1360 + 200, 720 + 130)
        if h and C.window_title(h) == TITLE:
            hwnd = h
            break
        time.sleep(0.1)
    if hwnd is None:
        print("假視窗沒出現在預期的位置（螢幕解析度不同？），略過這支測試。")
        return 0
    rect = C.window_rect(hwnd)
    left, top, right, bottom = rect
    print(f"假視窗 {rect}")

    cfg = E.Config()
    # 範圍＝視窗右邊那一大塊；左邊 100px 窄條在視窗裡但在範圍外
    cfg.region = {"left": left + 100, "top": top, "width": (right - left) - 100, "height": bottom - top}
    tracker = E.RegionTracker(cfg)
    region = tracker.current()[0]

    print("1) 沒有範圍不能開始錄")
    rec0 = R.ClickRecorder(E.RegionTracker(E.Config()), os.path.join(SANDBOX, "s0"))
    try:
        rec0.start()
        check("沒範圍：丟 RecorderError", False)
    except R.RecorderError as e:
        check("沒範圍：丟 RecorderError 附原因", "框選" in str(e))
    check("沒在錄時 stop 是空操作", rec0.stop() == (0, ""))

    print("2) 錄範圍內的點擊、忽略範圍外的")
    out = os.path.join(SANDBOX, "sessions")
    rec = R.ClickRecorder(tracker, out, ignore_injected=False)
    folder = rec.start()
    check("開始錄製", rec.recording and os.path.isdir(folder))
    try:
        rec.start()
        check("重複開始被擋", False)
    except R.RecorderError:
        check("重複開始被擋", True)
    time.sleep(0.4)
    p1 = (region.left + 50, region.top + 40)
    p2 = (region.left + region.width - 30, region.top + region.height - 25)
    outside = (left + 40, top + 100)           # 在假視窗裡，但在範圍外
    safe_click(hwnd, *p1)
    time.sleep(0.35)
    safe_click(hwnd, *outside)
    time.sleep(0.35)
    safe_click(hwnd, *p2)
    time.sleep(0.5)
    check("即時計數只算範圍內的兩次", rec.count == 2, str(rec.count))
    n, path = rec.stop()
    check("stop 回傳點擊數與路徑", n == 2 and path.endswith("clicks.json") and os.path.exists(path))
    check("停止後不再是錄製中", not rec.recording)
    check("再 stop 一次是空操作", rec.stop() == (0, ""))

    print("3) 輸出內容")
    data = json.load(open(path, encoding="utf-8"))
    check("記下範圍", data["region"] == region.to_dict(), str(data["region"]))
    cl = data["clicks"]
    check("兩筆點擊", len(cl) == 2)
    check("範圍內座標正確", cl[0]["region_rel_xy"] == [50, 40]
          and cl[1]["region_rel_xy"] == [region.width - 30, region.height - 25], str([c["region_rel_xy"] for c in cl]))
    check("比例座標正確", abs(cl[0]["region_frac_xy"][0] - 50 / (region.width - 1)) < 1e-3
          and abs(cl[0]["region_frac_xy"][1] - 40 / (region.height - 1)) < 1e-3, str(cl[0]["region_frac_xy"]))
    check("螢幕絕對座標也留著", cl[0]["screen_xy"] == list(p1))
    check("時間遞增", 0 <= cl[0]["t"] < cl[1]["t"])
    from PIL import Image
    ok_imgs = True
    for c in cl:
        for k in ("before", "after"):
            fp = os.path.join(os.path.dirname(path), c[k] or "")
            if not (c[k] and os.path.exists(fp)):
                ok_imgs = False
                continue
            if Image.open(fp).size != (region.width, region.height):
                ok_imgs = False
    check("點擊前後截圖都在，而且大小＝範圍（不是整個螢幕）", ok_imgs)

    print("4) 程式自己送的點擊（injected）預設不錄")
    rec2 = R.ClickRecorder(tracker, os.path.join(SANDBOX, "s2"))    # ignore_injected 預設 True
    rec2.start()
    time.sleep(0.3)
    safe_click(hwnd, region.left + 60, region.top + 60)             # control.click 走 SendInput＝injected
    time.sleep(0.5)
    n2, _ = rec2.stop()
    check("SendInput 送出的點擊沒被記到", n2 == 0, str(n2))

    print("5) 框選範圍底下的視窗會被鎖定")
    cfg.window_anchor = E.RegionTracker.make_anchor(V.Region(**cfg.region))
    check("認得出底下是假視窗並鎖定", bool(cfg.window_anchor) and cfg.window_anchor["title"] == TITLE,
          str(cfg.window_anchor))

    print("6) 停止之後鉤子確實拔掉")
    check("鉤子執行緒已結束", rec._hook_thread is not None and not rec._hook_thread.is_alive())
    check("鉤子代碼已清掉", rec._hook_handle is None)

    print()
    if _failures:
        print(f"❌ {len(_failures)} 項失敗：{_failures}")
        return 1
    print("✅ 全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
