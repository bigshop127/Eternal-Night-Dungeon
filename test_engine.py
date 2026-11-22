"""engine.py 的驗證。全程用假的擷取來源、假的點擊函式、假的視窗查詢，
不開任何視窗、不動滑鼠、不碰真實遊戲。設定／樣板／規則都寫在暫存資料夾。

    python test_engine.py
"""

import os
import shutil
import sys
import tempfile
import types

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_test_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX   # 一定要在 import engine 之前

import control as C  # noqa: E402
import engine as E  # noqa: E402
import vision_state as V  # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def scene(kind: str) -> np.ndarray:
    """三種長得很不一樣的假畫面（240×140）。"""
    img = np.full((140, 240, 3), 30, dtype=np.uint8)
    if kind == "A":       # 左上一大塊亮的
        img[10:70, 10:110] = (230, 230, 230)
    elif kind == "B":     # 右下一大塊紅的
        img[70:130, 130:230] = (220, 40, 40)
    elif kind == "C":     # 一條綠的橫帶
        img[60:80, :] = (40, 200, 60)
    return img


class FakeGrabber:
    backend = "fake"

    def __init__(self):
        self.image = scene("A")
        self.asked = []          # 每次被要求擷取的範圍
        self.fail = None

    def grab(self, region):
        self.asked.append(region)
        if self.fail:
            raise self.fail
        return self.image.copy()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


def make_engine(**cfg_over):
    for name in ("config.json", "rules.json"):
        p = os.path.join(SANDBOX, name)
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(E.TEMPLATES_DIR, ignore_errors=True)
    cfg = E.Config()
    cfg.region = {"left": 100, "top": 50, "width": 240, "height": 140}
    cfg.action_interval = 0.0
    cfg.retry_after = 3.0
    cfg.stable_reads = 2
    cfg.route_enabled = False      # 這裡測一般規則；固定路線另有 test_route.py
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    grabber = FakeGrabber()
    clicks = []
    eng = E.Engine(cfg, grabber=grabber,
                   clicker=lambda x, y, hold=0.06: clicks.append((x, y)))
    return eng, grabber, clicks


def settle(eng, n=3):
    """連續跑幾步。中途出現 stopped／error 就回傳那一個（停手之後下一步會回到正常狀態，
    只看最後一步會漏掉），否則回傳最後一個狀態。"""
    st = None
    for _ in range(n):
        st = eng.step()
        if st.kind in ("stopped", "error"):
            return st
    return st


def main():
    real_panic = C.panic_pressed
    real_mouse = C.mouse_button_down
    real_time = E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False      # 測試不能被使用者「此刻」有沒有按著滑鼠影響
    try:
        run(clock)
    finally:
        C.panic_pressed = real_panic
        C.mouse_button_down = real_mouse
        E.time = real_time
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if _failures:
        print(f"❌ {len(_failures)} 項失敗：{_failures}")
        return 1
    print("✅ 全部通過")
    return 0


def run(clock):
    print("1) 狀態名稱驗證")
    check("合法名稱", E.label_error("戰鬥_石巨人") is None)
    check("空名稱", E.label_error("") is not None and E.label_error("   ") is not None)
    check("不能有路徑字元", all(E.label_error(n) for n in ("a/b", "a\\b", "a:b", "a*b", "..", ".x", "x.")))
    check("前後空白", E.label_error(" x") is not None)
    check("太長", E.label_error("字" * 41) is not None)

    print("2) 規則簿")
    book = E.RuleBook.load()
    check("沒檔案是空的", len(book) == 0)
    book.set_click("A", 0.5, 0.25)
    book.set_wait("B")
    book.set_click("越界", 1.7, -3)
    book.save()
    again = E.RuleBook.load()
    check("存檔再讀回來一致", again.rules == book.rules and len(again) == 3)
    check("越界的座標被夾到 0~1", again.get("越界") == {"action": "click", "x": 1.0, "y": 0.0})
    check("describe 文字", "50%" in again.describe("A") and "等待" in again.describe("B")
          and "尚未" in again.describe("沒這個"))
    check("click_count", again.click_count == 2)
    with open(again.path, "w", encoding="utf-8") as f:
        f.write('{"rules": {"ok": {"action":"click","x":0.1,"y":0.2}, "bad1": {"action":"click","x":9,"y":0},'
                ' "bad2": {"action":"fly"}, "bad3": 5, "bad4": {"action":"click"}}}')
    loaded = E.RuleBook.load()
    check("壞掉的規則被丟掉、好的留著", list(loaded.rules) == ["ok"], str(loaded.rules))
    with open(again.path, "w", encoding="utf-8") as f:
        f.write("{不是 json")
    check("檔案壞掉不炸", len(E.RuleBook.load()) == 0)
    os.remove(again.path)

    print("3) 設定檔")
    cfg = E.Config()
    check("預設沒有範圍", cfg.region is None)
    cfg.region = {"left": 1, "top": 2, "width": 3, "height": 4}
    cfg.poll_ms = 77
    cfg.save()
    check("設定存了再讀回來", E.Config.load().region == cfg.region and E.Config.load().poll_ms == 77)
    with open(E.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("壞掉")
    check("設定檔壞掉退回預設", E.Config.load().region is None)
    os.remove(E.CONFIG_PATH)

    print("4) 範圍追蹤（視窗鎖定）")
    names = ["find_window", "window_rect", "window_is_minimized", "window_at", "window_title", "window_pid"]
    real = {n: getattr(C, n) for n in names}
    state = {"rect": (500, 300, 1500, 900), "min": False, "found": True}
    C.find_window = lambda title: 4242 if state["found"] else None
    C.window_rect = lambda h: state["rect"] if h == 4242 else None
    C.window_is_minimized = lambda h: state["min"]
    try:
        cfg = E.Config()
        tr = E.RegionTracker(cfg)
        r, err = tr.current()
        check("沒框選：回 None 並說明", r is None and "框選" in err)
        cfg.region = {"left": 600, "top": 350, "width": 800, "height": 500}
        r, err = tr.current()
        check("沒鎖視窗：原封不動", r == V.Region(600, 350, 800, 500) and err is None)
        cfg.window_anchor = {"title": "1", "dx": 100, "dy": 50, "w": 1000, "h": 600}
        r, err = tr.current()
        check("鎖視窗：位置依視窗算", r == V.Region(600, 350, 800, 500) and err is None)
        state["rect"] = (20, 10, 1020, 610)
        r, err = tr.current()
        check("視窗搬走：範圍跟著搬", r == V.Region(120, 60, 800, 500), str(r))
        state["rect"] = (20, 10, 1021, 611)
        check("大小差 1px 在容忍範圍內", tr.current()[0] is not None)
        state["rect"] = (20, 10, 1200, 610)
        r, err = tr.current()
        check("視窗大小變了：停手並說明", r is None and "大小變了" in err, str(err))
        state["rect"] = (20, 10, 1020, 610)
        state["min"] = True
        r, err = tr.current()
        check("視窗縮小：等待", r is None and "縮到最小" in err)
        state["min"], state["found"] = False, False
        r, err = tr.current()
        check("視窗被關掉：等待", r is None and "找不到視窗" in err)

        # blocker：點擊目標最上層的視窗是不是遊戲的
        state["found"] = True
        pids = {777: 111, 4242: 222, 888: 222}        # 777＝別的程式、4242＝遊戲、888＝遊戲的另一個視窗
        C.window_pid = lambda h: pids.get(h, os.getpid())
        C.window_title = lambda h: {777: "瀏覽器"}.get(h, "")
        cfg.window_anchor = {"title": "1", "dx": 100, "dy": 50, "w": 1000, "h": 600}
        C.window_at = lambda x, y: 777
        b = tr.blocker(700, 400)
        check("別的程式的視窗蓋在遊戲上：回傳描述", b is not None and "瀏覽器" in b, str(b))
        C.window_at = lambda x, y: 888
        check("遊戲同一個行程的其他視窗不算擋住", tr.blocker(700, 400) is None)
        C.window_at = lambda x, y: 4242
        check("最上層就是遊戲視窗：放行", tr.blocker(700, 400) is None)
        C.window_at = lambda x, y: 999999           # pids 查不到 → 當成本行程（輔助器自己）
        b = tr.blocker(700, 400)
        check("輔助器自己的視窗：回傳描述", b is not None and "輔助器自己" in b, str(b))
        C.window_at = lambda x, y: None
        check("那一點沒有任何視窗：不判斷", tr.blocker(700, 400) is None)
        cfg.window_anchor = None
        C.window_at = lambda x, y: 777
        check("沒鎖定視窗：不判斷別的程式（只擋自己）", tr.blocker(700, 400) is None)
        C.window_at = lambda x, y: 999999
        check("沒鎖定視窗：自己的視窗照擋", tr.blocker(700, 400) is not None)
        cfg.window_anchor = {"title": "1", "dx": 100, "dy": 50, "w": 1000, "h": 600}
        state["found"] = False
        C.window_at = lambda x, y: 777
        check("遊戲視窗找不到：不亂判斷別人", tr.blocker(700, 400) is None)
        state["found"] = True

        # make_anchor：範圍底下的視窗
        C.window_at = lambda x, y: 4242
        C.window_title = lambda h: "1"
        C.window_pid = lambda h: 1
        state["rect"] = (500, 300, 1500, 900)
        a = E.RegionTracker.make_anchor(V.Region(600, 350, 800, 500))
        check("make_anchor 記下位移與視窗大小",
              a == {"title": "1", "dx": 100, "dy": 50, "w": 1000, "h": 600}, str(a))
        C.window_pid = lambda h: os.getpid()
        check("底下是自己的視窗就不鎖", E.RegionTracker.make_anchor(V.Region(600, 350, 800, 500)) is None)
        C.window_pid = lambda h: 1
        C.window_title = lambda h: ""
        check("認不出視窗標題就不鎖", E.RegionTracker.make_anchor(V.Region(600, 350, 800, 500)) is None)
    finally:
        for n, fn in real.items():
            setattr(C, n, fn)

    print("5) 只看範圍內：擷取範圍＝框選範圍")
    eng, grab, clicks = make_engine()
    st = eng.step()
    check("樣板庫空的：說明並附畫面", st.kind == "empty" and st.image is not None)
    check("擷取的正是框選範圍，不是整個螢幕", grab.asked == [V.Region(100, 50, 240, 140)], str(grab.asked))
    eng.cfg.region = None
    st = eng.step()
    check("沒框選：waiting，而且沒去擷取", st.kind == "waiting" and len(grab.asked) == 1)
    eng.cfg.region = {"left": 100, "top": 50, "width": 240, "height": 140}
    grab.fail = OSError("boom")
    st = eng.step()
    check("擷取失敗：error 並停自動", st.kind == "error" and "boom" in st.message and not eng.autoplay)
    grab.fail = None

    print("6) 教學與辨識")
    eng, grab, clicks = make_engine()
    try:
        eng.teach("a/b", scene("A"), None)
        check("非法名稱被擋", False)
    except ValueError:
        check("非法名稱被擋", True)
    added, n = eng.teach("畫面A", scene("A"), (0.5, 0.25))
    check("第一張樣板", added and n == 1)
    added, n = eng.teach("畫面A", scene("A"), (0.5, 0.25))
    check("同樣的畫面不重複收", (not added) and n == 1)
    eng.teach("畫面B", scene("B"), None)
    eng.teach("畫面C", scene("C"), (0.1, 0.9))
    check("樣板與規則都存進資料夾",
          os.path.exists(os.path.join(E.TEMPLATES_DIR, "index.json"))
          and os.path.exists(os.path.join(E.TEMPLATES_DIR, "畫面A", "001.png"))
          and os.path.exists(E.RULES_PATH))
    eng2 = E.Engine(eng.cfg, grabber=grab)
    check("重開之後樣板與規則還在", len(eng2.db) == 3 and len(eng2.rules) == 3)
    grab.image = scene("A")
    check("第一次讀到：先 settling", eng.step().kind == "settling")
    st = eng.step()
    check("連續兩次讀到同一個：採信", st.kind == "ok" and st.label == "畫面A", f"{st.kind} {st.label}")
    check("沒開自動：一次都沒點", clicks == [])
    check("Status 帶規則與距離", st.rule == {"action": "click", "x": 0.5, "y": 0.25} and st.distance < 5)
    grab.image = scene("B")
    eng.step()
    check("換畫面：又要重新確認", eng.step().label == "畫面B")
    noisy = scene("A").astype(np.int16) + np.random.RandomState(1).randint(-8, 9, (140, 240, 3))
    grab.image = np.clip(noisy, 0, 255).astype(np.uint8)
    st = settle(eng, 3)
    check("有雜訊仍認得", st.label == "畫面A", f"{st.kind} {st.label} d={st.distance:.1f}")
    grab.image = np.full((140, 240, 3), 128, dtype=np.uint8)
    st = settle(eng, 3)
    check("完全不像：unknown", st.kind == "unknown" and st.label is None, st.kind)
    for shade in (0, 128, 255):
        grab.image = np.full((140, 240, 3), shade, dtype=np.uint8)
        st = settle(eng, 3)
        check(f"純色畫面（{shade}）不會被誤認成任何狀態", st.kind == "unknown",
              f"{st.kind} {st.label} d={st.distance:.1f}")
    dim = np.clip(scene("A").astype(np.int16) - 25, 0, 255).astype(np.uint8)
    grab.image = dim
    st = settle(eng, 3)
    check("整體變暗仍認得", st.label == "畫面A", f"{st.kind} {st.label} d={st.distance:.1f}")
    eng.teach("黑畫面", np.zeros((140, 240, 3), dtype=np.uint8), None)
    grab.image = np.zeros((140, 240, 3), dtype=np.uint8)
    st = settle(eng, 3)
    check("純黑畫面可以被教成一個狀態", st.label == "黑畫面", f"{st.kind} {st.label}")
    grab.image = np.full((140, 240, 3), 3, dtype=np.uint8)
    st = settle(eng, 3)
    check("接近純黑的畫面也算它", st.label == "黑畫面", f"{st.kind} {st.label}")
    eng.forget("黑畫面")

    print("7) 自動操控")
    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.25))
    eng.teach("畫面B", scene("B"), None)
    eng.teach("畫面C", scene("C"), (0.0, 1.0))
    grab.image = scene("A")
    eng.start_autoplay()
    eng.step()
    check("還在 settling 不點", clicks == [])
    st = eng.step()
    check("穩定後點一下", len(clicks) == 1)
    region = V.Region(100, 50, 240, 140)
    check("點的是範圍內的比例位置",
          clicks[0] == (region.left + round(0.5 * 239), region.top + round(0.25 * 139)), str(clicks))
    check("動作記進 Status.acted", "畫面A" in st.acted and st.actions_done == 1, st.acted)
    for _ in range(3):
        eng.step()
    check("點完不會連環點（要等 retry_after）", len(clicks) == 1)
    clock.t += 3.1
    settle(eng, 2)
    check("畫面沒變、過了 retry_after：補點一次", len(clicks) == 2)
    grab.image = scene("B")
    settle(eng, 3)
    clock.t += 10
    settle(eng, 3)
    check("換到 wait 規則的畫面：不點", len(clicks) == 2)
    grab.image = scene("C")
    settle(eng, 3)
    check("換到新畫面：馬上點（不用等 retry_after）", len(clicks) == 3)
    check("比例 (0,1) 落在範圍左下角", clicks[-1] == (region.left, region.top + region.height - 1), str(clicks[-1]))

    print("8) 卡住就停手")
    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    eng.start_autoplay()
    settle(eng, 3)
    stopped = None
    for _ in range(8):
        clock.t += 3.1
        st = settle(eng, 2)
        if st.kind == "stopped":
            stopped = st
            break
    check("連續沒反應：停手並說明", stopped is not None and "連續" in stopped.message and not eng.autoplay)
    check("點了 max_retries+1 次就停", len(clicks) == eng.cfg.max_retries + 1, str(len(clicks)))

    print("9) 認不得太久停手")
    eng, grab, clicks = make_engine(unknown_timeout=60.0)
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = np.full((140, 240, 3), 128, dtype=np.uint8)
    eng.start_autoplay()
    eng.step()
    clock.t += 30
    check("認不得 30 秒還撐著", eng.step().kind == "unknown" and eng.autoplay)
    clock.t += 31
    st = eng.step()
    check("超過 60 秒：停手", st.kind == "stopped" and not eng.autoplay and "認不得" in st.message)
    check("認不得期間沒亂點", clicks == [])
    eng2, grab2, _ = make_engine()
    eng2.teach("畫面A", scene("A"), (0.5, 0.5))
    grab2.image = np.full((140, 240, 3), 128, dtype=np.uint8)
    eng2.step()
    clock.t += 500
    check("沒開自動：認不得再久也不算錯", eng2.step().kind == "unknown")

    print("10) 安全閥")
    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    eng.guard = lambda x, y: "自己的視窗蓋住了遊戲畫面"
    eng.start_autoplay()
    st = settle(eng, 3)
    check("guard 擋下：停手、沒點", st.kind == "stopped" and "蓋住" in st.message and clicks == [] and not eng.autoplay)

    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    eng.start_autoplay()
    C.panic_pressed = lambda key="F8": True
    st = eng.step()
    C.panic_pressed = lambda key="F8": False
    check("緊急停止鍵：立刻停、沒點", st.kind == "stopped" and clicks == [] and not eng.autoplay)

    def boom(x, y, hold=0.06):
        raise C.InputError("被系統擋下")
    eng, grab, clicks = make_engine()
    eng._click = boom
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    eng.start_autoplay()
    st = settle(eng, 3)
    check("SendInput 被擋：error 並停手", st.kind == "error" and "擋" in st.message and not eng.autoplay)

    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    eng.start_autoplay()
    eng.cfg.window_anchor = {"title": "不存在的視窗_XYZ", "dx": 0, "dy": 0, "w": 100, "h": 100}
    st = settle(eng, 3)
    check("視窗不見：waiting、不點，autoplay 維持開著等它回來",
          st.kind == "waiting" and clicks == [] and eng.autoplay)
    eng.cfg.window_anchor = None
    settle(eng, 3)
    check("範圍恢復後接著跑", len(clicks) == 1)

    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), None)
    grab.image = scene("A")
    eng.start_autoplay()
    st = settle(eng, 4)
    check("wait 規則：辨識得出來但不點", st.kind == "ok" and st.label == "畫面A" and clicks == [])
    eng.rules.remove("畫面A")
    clock.t += 10
    st = settle(eng, 3)
    check("沒規則的畫面：也不點", clicks == [] and st.rule is None)

    print("11) 忘掉一個狀態")
    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    eng.teach("畫面B", scene("B"), None)
    eng.forget("畫面A")
    check("樣板、規則、資料夾都清掉",
          eng.db.labels == ["畫面B"] and eng.rules.get("畫面A") is None
          and not os.path.exists(os.path.join(E.TEMPLATES_DIR, "畫面A")))
    check("重開後仍然是忘掉的", E.Engine(eng.cfg, grabber=grab).db.labels == ["畫面B"])
    eng.forget("../外面")   # 非法名稱不能炸、也不能動到別的東西
    check("非法名稱靜默忽略", eng.db.labels == ["畫面B"])

    print("12) snapshot")
    eng, grab, clicks = make_engine()
    img = eng.snapshot()
    check("回傳範圍內的畫面", img.shape == (140, 240, 3) and grab.asked[-1] == V.Region(100, 50, 240, 140))
    eng.cfg.region = None
    try:
        eng.snapshot()
        check("沒範圍：丟 ValueError", False)
    except ValueError as e:
        check("沒範圍：丟 ValueError 附原因", "框選" in str(e))

    run_regions(clock)


TITLE_BOX = (0.0, 0.0, 0.5, 0.09)      # 假畫面的標題列（前 12 列）
TEXT_BOX = (0.2, 0.30, 0.8, 0.50)      # 假確認框的文字區


def ui_scene(kind: str, seed: int = 0) -> np.ndarray:
    """模擬遊戲畫面：上方一條固定的標題列，下面是每次都不同、但對比不高的「地圖」。
    popup＝疊在中央的固定彈窗；dim＝整體壓暗（像 VS／VICTORY 那種連標題列都蓋掉的畫面）；
    confirm＝不透明的確認框，text 決定框裡的「字」（一排排小方塊）。"""
    rng = np.random.RandomState(seed)
    img = np.zeros((140, 240, 3), dtype=np.uint8)
    img[:] = 55 + rng.randint(-6, 7, size=(1, 1, 3))
    for _ in range(6):
        y, x = rng.randint(14, 120), rng.randint(0, 200)
        img[y:y + rng.randint(10, 30), x:x + rng.randint(15, 40)] = 55 + rng.randint(-14, 15, size=3)
    img[0:12, :] = (25, 30, 60)
    for k in range(6):
        img[3:9, 8 + k * 14:8 + k * 14 + 9] = (235, 235, 240)
    if kind == "popup":
        img[35:105, 60:180] = (215, 225, 240)
        img[80:95, 90:150] = (240, 190, 40)
    elif kind.startswith("confirm"):
        img[20:120, 40:200] = (49, 113, 173)
        for k in range(12):                       # 「字」：12 個小方塊，confirm2 換掉最後 2 個（像只差幾個字的另一種確認框）
            on = k < 10 if kind == "confirm2" else True
            x0 = 52 + k * 11
            img[44:56, x0:x0 + 8] = (250, 250, 250) if on else (49, 113, 173)
            if kind == "confirm2" and k >= 10:
                img[60:70, x0:x0 + 8] = (250, 250, 250)
        img[90:108, 60:100] = (90, 170, 250)
        img[90:108, 140:180] = (225, 80, 80)
    elif kind == "dim":
        img = (img * 0.25).astype(np.uint8)
    return img


def run_regions(clock):
    print("13) 區域樣板／後備狀態／專用門檻（StateDB）")
    db = V.StateDB(18.0, 2.0)
    check("區域樣板：加得進去", db.add("總覽", ui_scene("map", 1), box=TITLE_BOX))
    check("同一區域幾乎一樣的不重複收", not db.add("總覽", ui_scene("map", 2), box=TITLE_BOX))
    check("box 不合法丟 ValueError",
          all(_raises(lambda b=b: db.add("X", ui_scene("map"), box=b))
              for b in ((0.5, 0, 0.4, 1), (0, 0, 1.2, 1), (0, 0, 1), "abc")))
    db.set_fallback("總覽")
    db.add("彈窗", ui_scene("popup", 3))
    m = db.match(ui_scene("map", 9))
    check("整張畫面每次都不同，但標題列相同 → 靠後備認成總覽", m.label == "總覽", str(m))
    m = db.match(ui_scene("popup", 8))
    check("疊著彈窗時標題列也在，但彈窗比後備優先", m.label == "彈窗", str(m))
    m = db.match(ui_scene("dim", 4))
    check("標題列被壓暗的畫面不是總覽", m.label is None, str(m))

    db2 = V.StateDB(18.0, 2.0)
    db2.add("總覽", ui_scene("map", 1), box=TITLE_BOX)
    db2.add("彈窗", ui_scene("popup", 3))
    check("沒設後備：總覽照樣是普通狀態（比距離），彈窗場景標題列一樣所以可能搶先——這正是要設後備的原因",
          db2.match(ui_scene("popup", 8)).distance < 1.0)

    db3 = V.StateDB(18.0, 2.0)
    db3.add("確認", ui_scene("confirm", 5), box=TEXT_BOX)
    same = db3.match(ui_scene("confirm", 6))
    check("區域樣板不受背景影響：同一個確認框、換了背景照樣認得", same.label == "確認" and same.distance < 0.5, str(same))
    other = ui_scene("confirm2", 7)
    d_other = float(np.abs(V._norm_vec(V.crop_box(other, TEXT_BOX)) - V._norm_vec(V.crop_box(ui_scene("confirm", 5), TEXT_BOX))).mean())
    check("前提：換了字的確認框，距離落在全域門檻(18)以內、專用門檻(2)以上", 2.0 < d_other < 18.0, f"距離 {d_other:.1f}")
    check("沒設專用門檻：換了字的確認框被誤認成同一個（危險）", db3.match(other).label == "確認")
    db3.set_label_tolerance("確認", 2.0)
    check("設了專用門檻：換了字的確認框認不得，真的仍認得",
          db3.match(other).label is None and db3.match(ui_scene("confirm", 6)).label == "確認")
    db3.tolerance = 1.0
    db3.set_label_tolerance("確認", 30.0)
    check("專用門檻只能比全域更嚴、不能放寬", db3.match(other).label is None)

    d = os.path.join(SANDBOX, "regions_db")
    shutil.rmtree(d, ignore_errors=True)
    full = V.StateDB(18.0, 2.0)
    full.add("總覽", ui_scene("map", 1), box=TITLE_BOX)
    full.add("彈窗", ui_scene("popup", 3))
    full.add("確認", ui_scene("confirm", 5), box=TEXT_BOX)
    full.set_fallback("總覽")
    full.set_label_tolerance("確認", 2.0)
    full.save(d)
    back = V.StateDB.load(d)
    check("存檔讀檔：box／後備／專用門檻都在", back.fallback == {"總覽"} and back.label_tolerance == {"確認": 2.0}
          and sorted(b for b in back._boxes if b) == sorted([V.clean_box(TITLE_BOX), V.clean_box(TEXT_BOX)]))
    # 換了字的確認框：不能被認成「確認」（會點下去）；它的標題列還亮著，所以落到後備的「總覽」＝只等、不點
    for name, scene_, want in (("map", ui_scene("map", 9), "總覽"), ("popup", ui_scene("popup", 8), "彈窗"),
                               ("confirm2", ui_scene("confirm2", 7), "總覽"), ("confirm", ui_scene("confirm", 6), "確認")):
        got = back.match(scene_)
        check(f"存檔讀檔後辨識結果一致：{name} → {want}", got.label == want, str(got))
    with open(os.path.join(d, "index.json"), "r", encoding="utf-8") as f:
        raw = f.read()
    with open(os.path.join(d, "index.json"), "w", encoding="utf-8") as f:
        f.write(raw.replace("[\n        0.0,\n        0.0,\n        0.5,\n        0.09\n      ]", "[0.9, 0.0, 0.1, 0.09]"))
    check("box 壞掉的樣板讀檔時丟掉、不退化成整張比對", len(V.StateDB.load(d)) == len(full) - 1)
    full.remove_label("總覽")
    check("刪掉狀態連帶清掉後備／專用門檻設定", "總覽" not in full.fallback and full.count_for("總覽") == 0)
    full.remove_label("確認")
    check("專用門檻一併清掉", full.label_tolerance == {})

    print("14) 使用者按著滑鼠時不動作")
    eng, grab, clicks = make_engine()
    eng.teach("畫面A", scene("A"), (0.5, 0.5))
    grab.image = scene("A")
    held = [True]
    eng._busy = lambda: held[0]
    eng.start_autoplay()
    st = settle(eng, 6)
    check("按著滑鼠：辨識照常、但一下都不點", clicks == [] and st.kind == "ok" and st.label == "畫面A")
    check("按著期間不算重試、沒有動作計數", eng.actions_done == 0 and eng._retry == 0)
    held[0] = False
    settle(eng, 3)
    check("放開之後才點，而且只點一次", len(clicks) == 1)

    print("15) 總覽當後備：使用者慢慢拖圖時不會被停手，畫面一出現彈窗就動作")
    eng, grab, clicks = make_engine()
    eng.db.add("前往彈窗", ui_scene("popup", 3))
    eng.db.add("總覽", ui_scene("map", 1), box=TITLE_BOX)
    eng.db.set_fallback("總覽")
    eng.rules.set_click("前往彈窗", 0.5, 0.6)
    eng.rules.set_wait("總覽")
    eng.start_autoplay()
    seed = 20
    stopped = None
    for _ in range(40):                      # 每步換一張不同的地圖、時間快轉 5 秒＝共 200 秒（遠超 90 秒）
        grab.image = ui_scene("map", seed)
        seed += 1
        clock.t += 5.0
        st = eng.step()
        if st.kind in ("stopped", "error"):
            stopped = st
            break
    check("在總覽停留 200 秒：自動操控沒被停、沒亂點", stopped is None and eng.autoplay and clicks == [])
    check("狀態顯示成「總覽」（不是認不得）", st.label == "總覽" and st.kind == "ok", f"{st.kind}/{st.label}")
    grab.image = ui_scene("popup", 50)
    settle(eng, 3)
    check("彈窗出現：只點一次、位置在規則上", len(clicks) == 1 and clicks[0] == (100 + int(round(0.5 * 239)), 50 + int(round(0.6 * 139))))

    eng, grab, clicks = make_engine()
    eng.db.add("前往彈窗", ui_scene("popup", 3))
    eng.rules.set_click("前往彈窗", 0.5, 0.6)
    eng.start_autoplay()
    stopped = None
    for _ in range(40):
        grab.image = ui_scene("map", seed)
        seed += 1
        clock.t += 5.0
        st = eng.step()
        if st.kind == "stopped":
            stopped = st
            break
    check("對照：沒有總覽狀態時，同樣情形 90 秒後會停手", stopped is not None and "認不得" in stopped.message)

    eng, grab, clicks = make_engine()
    eng.db.add("總覽", ui_scene("map", 1), box=TITLE_BOX)
    eng.db.add("確認", ui_scene("confirm", 5), box=TEXT_BOX)
    eng.db.set_fallback("總覽")
    eng.db.set_label_tolerance("確認", 2.0)
    eng.rules.set_wait("總覽")
    eng.teach("使用者新教的", scene("B"), (0.4, 0.4))          # 面板上教新畫面：走 teach() → db.save()
    again = E.Engine(eng.cfg, grabber=grab)
    check("面板教新畫面不會洗掉區域樣板／後備／專用門檻",
          again.db.fallback == {"總覽"} and again.db.label_tolerance == {"確認": 2.0}
          and sum(1 for b in again.db._boxes if b) == 2 and again.db.count_for("使用者新教的") == 1)
    check("而且重開後總覽照樣靠標題列認得", again.db.match(ui_scene("map", 33)).label == "總覽")
    eng.forget("使用者新教的")
    check("忘掉別的畫面不影響區域樣板", E.Engine(eng.cfg, grabber=grab).db.fallback == {"總覽"})


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
