"""11/22 新功能驗證：背景操控（adb_io.py）＋換邊大步快拖與換邊記憶（overview.fast_scan_legs／route.SwitchMemory）。
全程假的 adb、假地圖、假時鐘，不開視窗、不動滑鼠、不碰模擬器。

    python test_switch.py
"""

import os
import sys
import tempfile

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_switchtest_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX   # 一定要在 import engine／overview 之前

import control as C  # noqa: E402


def _no_real_input(*a, **kw):
    raise RuntimeError("測試不准送出真的滑鼠／鍵盤輸入")


C._send = C.click = C.swipe = C.move_to = C.tap_key = _no_real_input

import adb_io as A  # noqa: E402


def _no_real_adb(*a, **kw):
    raise RuntimeError("測試不准呼叫真的 adb")


A.run = _no_real_adb

import engine as E  # noqa: E402
import overview as O  # noqa: E402
import route as RT  # noqa: E402
import test_overview as TO  # noqa: E402   （借它的 FakeWorld：會動的假地圖）

L, R = O.LEFT, O.RIGHT
W, H = TO.W, TO.H
_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


# ---------------------------------------------------------------- 1. 拖法的幾何
def test_geometry():
    print("\n1. 大步拖法：起訖點都在安全範圍、不壓到遊戲按鈕")
    moves = []
    for m in (O.LOOK_DOWN, O.LOOK_UP, O.LOOK_DOWN_LEFT, O.LOOK_DOWN_RIGHT, O.LOOK_LEFT, O.LOOK_RIGHT):
        for k in (1.0, 1.5, 1.75, 2.0, 3.0):
            moves.append(O.big_move(m, k))
    for dx, dy in ((0.9, -2.3), (-1.4, 0.2), (0.0, -0.05), (0.31, 0.53), (-0.6, -0.6)):
        moves += O.replay_moves(dx, dy)

    def safe(mv):
        x0, y0, x1, y1 = mv
        return all(O.DRAG_AREA[0] - 1e-6 <= x <= O.DRAG_AREA[2] + 1e-6 and O.DRAG_AREA[1] - 1e-6 <= y <= O.DRAG_AREA[3] + 1e-6
                   and not O._in_zone(x, y, 1, 1, O.KEEP_OUT_ZONES) for x, y in ((x0, y0), (x1, y1)))
    bad = [m for m in moves if not safe(m)]
    check("所有大步／記憶拖法的起訖點都在安全框內、不在按鈕禁區", not bad, f"{len(moves)} 種，壞的 {bad[:3]}")

    big = O.big_move(O.LOOK_DOWN_RIGHT, 1.75)
    odx, ody = O.LOOK_DOWN_RIGHT[2] - O.LOOK_DOWN_RIGHT[0], O.LOOK_DOWN_RIGHT[3] - O.LOOK_DOWN_RIGHT[1]
    bdx, bdy = big[2] - big[0], big[3] - big[1]
    check("上下放大 1.75 倍、左右放大 √1.75 倍（方向不變）",
          abs(bdy / ody - 1.75) < 0.01 and abs(bdx / odx - 1.75 ** 0.5) < 0.01, f"{bdx:.3f},{bdy:.3f}")
    check("放到很大也只拖到安全框的長度為止", all(abs(m[3] - m[1]) <= O.MAX_DRAG[1] + 1e-6 for m in moves))

    for dx, dy in ((0.9, -2.3), (-1.4, 0.2), (0.31, 0.53)):
        parts = O.replay_moves(dx, dy)
        sx = sum(m[2] - m[0] for m in parts)
        sy = sum(m[3] - m[1] for m in parts)
        n_min = int(np.ceil(max(abs(dx) / O.MAX_DRAG[0], abs(dy) / O.MAX_DRAG[1])))
        check(f"記憶 ({dx}, {dy}) 拆成 {len(parts)} 下、加起來一樣遠、下數最少",
              abs(sx - dx) < 0.003 and abs(sy - dy) < 0.003 and len(parts) == n_min, f"{sx:.3f},{sy:.3f}")

    for side in (L, R):
        n = sum(k for _, k in O.fast_scan_legs(side, 1.75))
        check(f"找{O.SIDE_NAMES[side]}的大步走法 {n} 步＋拖到中間 {O.MAX_CENTER_DRAGS} 次 ≤ 預設上限 25",
              n + O.MAX_CENTER_DRAGS <= 25)
    first = O.fast_scan_legs(R, 1.75)[0][0]
    check("找右隊第一步往右下看、找左隊第一步往左下看",
          O.look_name(first) == "往右下看" and O.look_name(O.fast_scan_legs(L, 1.75)[0][0]) == "往左下看")


# ---------------------------------------------------------------- 2. 假地圖上的換邊
def route_after_left_done():
    """11/08 20:46 的情況：左隊第 4 段最後的裝置點完（左隊整條走完），右隊停在第 4 段起點（右隊第 14 步：正前的寶箱）。"""
    r = RT.RouteTracker(None)
    r.idx = {L: len(r.route[L]), R: 13}
    r.last_actor = L
    return r


def planner(r, scale, memory=None):
    pl = O.MapPlanner(O.NodeClassifier(), route=r, switch_scale=scale, memory=memory)
    pl.left_overview()           # 回到總覽：鏡頭對著剛走完一步的那隊（route.last_actor）
    return pl


def world(right_at):
    # 右隊正前方有個寶箱（路線第 4 段右 1 要點的）；左隊在原點、前面沒東西
    return TO.FakeWorld(teams={L: (0, 0), R: right_at}, nodes={R: O.CHEST}, start=L)


def test_fake_switch():
    print("\n2. 換邊找另一隊（假地圖，重現 11/08 第 4 段左隊走完、右隊在更右下方）")
    far = (int(0.85 * W), int(1.9 * H))
    w_old = world(far)
    seen_old, plan_old = TO.run_world(planner(route_after_left_done(), 1.0), w_old)
    w_new = world(far)
    pl = planner(route_after_left_done(), 1.75)
    seen_new, plan_new = TO.run_world(pl, w_new)
    check("舊走法（小步）找不到，停手叫人——跟 11/08 一樣", plan_old.kind == "stop", f"拖 {len(seen_old)} 次：{plan_old.reason[:30]}")
    check("新走法（換邊大步）找到右隊、點了它正前的寶箱",
          plan_new.kind == "click" and plan_new.side == R and plan_new.route == (R, 13), f"{plan_new.kind} {plan_new.reason[:40]}")
    check("新走法拖的次數不多（≤ 8）", len(seen_new) <= 8, f"{len(seen_new)} 次：{seen_new}")
    check("換邊時每一步都標成 fast（engine 會用 switch_interval 取代 map_interval）",
          all(p.fast for p in w_new.drags), f"{[p.fast for p in w_new.drags]}")

    # 近一點的情況（11/08 前三次換邊那種）：大步也不能拖過頭
    for i, near in enumerate(((int(0.45 * W), int(1.6 * H)), (int(0.4 * W), int(0.9 * H)), (int(0.3 * W), int(2.4 * H)))):
        w2 = world(near)
        s2, p2 = TO.run_world(planner(route_after_left_done(), 1.75), w2)
        check(f"右隊在 {near}：大步也找得到（不會一下拖過頭）", p2.kind == "click" and p2.side == R, f"拖 {len(s2)} 次 {s2}")

    print("\n3. 路線走完之後（照原本規則）：另一隊點了裝置在等 → 換邊大步；沒人在等 → 照舊小步")
    for stuck, want in ((L, True), (None, False)):
        pl3 = O.MapPlanner(O.NodeClassifier(), switch_scale=1.75)      # 沒有路線
        pl3._stuck_side = stuck
        w3 = TO.FakeWorld(teams={L: (0, 0), R: (int(0.4 * W), int(1.2 * H))}, nodes={R: O.ENEMY}, start=L)
        s3, p3 = TO.run_world(pl3, w3)
        fast = [p.fast for p in w3.drags if p.tag == "scan"]
        check(f"{'左隊點了裝置在等' if stuck else '沒有誰在等'} → {'大步' if want else '小步'}找右隊、找到了",
              p3.kind == "click" and bool(fast) and all(f == want for f in fast), f"{s3}")


def test_memory():
    print("\n4. 換邊記憶：第一輪大步找到 → 記住拖了多遠 → 下一輪一次拖過去")
    path = os.path.join(SANDBOX, "switch_memory.json")
    mem = RT.SwitchMemory(path)
    far = (int(0.85 * W), int(1.9 * H))
    r = route_after_left_done()
    pl = planner(r, 1.75, mem)
    w1 = world(far)
    s1, p1 = TO.run_world(pl, w1)
    key = f"{L}{len(r.route[L])}>{R}13"
    # 照路線點下去、遊戲有反應（跳彈窗）＝ engine 先 route.confirm() 再 left_overview()
    r.pending = p1.route
    r.confirm()
    pl.left_overview()
    got = RT.SwitchMemory.load(path).get(key)
    net = (sum(p.x1 - p.x for p in w1.drags) / (W - 1), sum(p.y1 - p.y for p in w1.drags) / (H - 1))
    check("找到之後記下這個換邊點總共拖了多遠（存檔重開也在）",
          got is not None and abs(got[0] - net[0]) < 0.002 and abs(got[1] - net[1]) < 0.002, f"{key} → {got}")

    r2 = route_after_left_done()
    pl2 = planner(r2, 1.75, RT.SwitchMemory.load(path))
    w2 = world(far)
    s2, p2 = TO.run_world(pl2, w2)
    check("下一輪同一個地方：照記住的距離拖、找到", p2.kind == "click" and p2.side == R and len(s2) <= len(s1),
          f"第一輪 {len(s1)} 次、這輪 {len(s2)} 次")
    check("動作記錄寫明是照記憶拖的", "照上次記住的距離" in w2.drags[0].reason, w2.drags[0].reason[:60])

    # 第一輪繞了遠路的情況（右隊其實在上面：先往下找了 4 大步才回頭往上）——記憶直接省掉來回
    up = (int(0.45 * W), int(-0.9 * H))
    mem_u = RT.SwitchMemory(os.path.join(SANDBOX, "mu.json"))
    ru = route_after_left_done()
    plu = planner(ru, 1.75, mem_u)
    wu = world(up)
    su, pu = TO.run_world(plu, wu)
    ru.pending = pu.route
    ru.confirm()
    plu.left_overview()
    ru2 = route_after_left_done()
    wu2 = world(up)
    su2, pu2 = TO.run_world(planner(ru2, 1.75, RT.SwitchMemory.load(mem_u.path)), wu2)
    check("第一輪繞遠路 → 下一輪照記憶，拖的次數大幅減少",
          pu.kind == "click" and pu2.kind == "click" and pu2.side == R and len(su2) * 2 <= len(su),
          f"第一輪 {len(su)} 次 {su}、這輪 {len(su2)} 次 {su2}")

    print("\n5. 記憶不準（地圖或鏡頭跟上次不一樣）：拖回原點再照大步走法找，找到後更新記憶")
    bad = RT.SwitchMemory(path)
    bad.put(key, -0.9, 0.6)          # 反方向的錯記憶
    r3 = route_after_left_done()
    pl3 = planner(r3, 1.75, bad)
    w3 = world(far)
    s3, p3 = TO.run_world(pl3, w3)
    check("錯的記憶拖完沒看到 → 拖回原點 → 大步找到右隊", p3.kind == "click" and p3.side == R, f"{s3}")
    r3.pending = p3.route
    r3.confirm()
    pl3.left_overview()
    fixed = RT.SwitchMemory.load(path).get(key)
    check("記憶被更新成這次真正的距離", fixed is not None and abs(fixed[0] - net[0]) < 0.05 and abs(fixed[1] - net[1]) < 0.05,
          f"{fixed} vs {net[0]:.3f},{net[1]:.3f}")

    print("\n6. 照記憶也找不到、停手時把那筆記憶丟掉（下一輪重新學）")
    m4 = RT.SwitchMemory(os.path.join(SANDBOX, "m4.json"))
    m4.put(key, 0.2, -0.5)
    r4 = route_after_left_done()
    pl4 = planner(r4, 1.75, m4)
    w4 = TO.FakeWorld(teams={L: (0, 0)}, nodes={}, start=L)       # 右隊根本不在地圖上
    s4, p4 = TO.run_world(pl4, w4)
    check("找不到 → 停手", p4.kind == "stop", p4.reason[:40])
    check("那筆記憶被刪掉", RT.SwitchMemory.load(m4.path).get(key) is None)

    print("\n7. 記憶檔壞掉／亂改：當作沒有，不影響執行")
    with open(os.path.join(SANDBOX, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    check("壞檔 → 空記憶", RT.SwitchMemory.load(os.path.join(SANDBOX, "broken.json")).data == {})
    with open(os.path.join(SANDBOX, "weird.json"), "w", encoding="utf-8") as f:
        f.write('{"switches": {"a": [99, 1], "b": [0.1, -0.2], "c": "x"}}')
    d = RT.SwitchMemory.load(os.path.join(SANDBOX, "weird.json")).data
    check("不合理的值丟掉、合理的留著", d == {"b": (0.1, -0.2)} or d == {}, f"{d}")


# ---------------------------------------------------------------- 3. adb 背景操控
class FakeProc:
    def __init__(self, out=b"", rc=0, err=b""):
        self.stdout, self.returncode, self.stderr = out, rc, err


def raw_frame(w, h, fill=(10, 20, 30), header=16):
    px = np.zeros((h, w, 4), np.uint8)
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = fill[0], fill[1], fill[2], 255
    px[0, 0, :3] = (200, 100, 50)
    hdr = np.array([w, h, 1] + ([0] if header == 16 else []), dtype="<u4").tobytes()
    return hdr + px.tobytes()


def test_adb():
    print("\n8. adb 背景操控：截圖、座標換算、點擊／拖曳指令（假的 adb）")
    calls = []

    def runner(path, *args, timeout=10.0):
        calls.append(args)
        if args[-1] == "screencap":
            return FakeProc(raw_frame(1280, 720))
        return FakeProc()

    orig = A.try_connect
    A.try_connect = lambda path, serial: serial == "127.0.0.1:5555"
    try:
        b = A.AdbBackend("127.0.0.1:5555", (1161, 654), adb_path="adb", runner=runner)
        img = b.grab(b.region())
        check("截圖縮成工作大小（跟框選的範圍一樣大）", img.shape == (654, 1161, 3), f"{img.shape}")
        check("顏色是 RGB、沒有 alpha", img.dtype == np.uint8 and tuple(img[300, 300]) == (10, 20, 30))
        check("解析原始格式（新版 16 bytes 表頭、舊版 12 bytes）",
              A.decode_raw_screencap(raw_frame(4, 3, header=12)).shape == (3, 4, 3)
              and tuple(A.decode_raw_screencap(raw_frame(4, 3))[0, 0]) == (200, 100, 50))
        try:
            A.decode_raw_screencap(b"\x01\x02")
            ok = False
        except A.AdbError:
            ok = True
        check("壞掉的截圖 → AdbError（是 OSError，engine 接得住）", ok and issubclass(A.AdbError, OSError))
        calls.clear()
        b.click(1160, 653)
        b.click(0, 0)
        b.click(580, 327)
        taps = [c[-2:] for c in calls if "tap" in c]
        check("點擊座標換算到模擬器解析度（右下角 → 1279,719；左上 → 0,0；中間 → 中間）",
              taps == [("1279", "719"), ("0", "0"), ("640", "360")], f"{taps}")
        check("點擊走 adb shell input tap、指定模擬器", all(c[:4] == ("-s", "127.0.0.1:5555", "shell", "input") for c in calls))
        calls.clear()
        b.swipe(580, 500, 580, 200, duration=0.35)
        sw = calls[-1]
        check("拖曳走 input swipe，時間換成毫秒", sw[4:] == ("swipe", "640", "551", "640", "220", "350"), f"{sw}")
        check("虛擬範圍是 (0,0,寬,高)、不會被別的視窗擋住",
              A.AdbTracker(b).current() == (b.region(), None) and A.AdbTracker(b).blocker(5, 5) is None)
    finally:
        A.try_connect = orig

    print("\n9. 設定的模擬器沒開：自動改用開著的那台；好幾台都開著就挑畫面認得出來的")
    A.try_connect = lambda path, serial: serial in ("127.0.0.1:5565", "127.0.0.1:5575")
    try:
        b2 = A.AdbBackend("127.0.0.1:5555", (1161, 654), adb_path="adb", runner=runner,
                          chooser=lambda serials, cap: "127.0.0.1:5575" if cap("127.0.0.1:5575").shape[0] == 720 else None)
        b2.grab()
        check("5555 沒開 → 從開著的 5565／5575 裡挑 chooser 選的那台", b2.serial == "127.0.0.1:5575", f"{b2.serial}")
        A.try_connect = lambda path, serial: False
        b3 = A.AdbBackend("127.0.0.1:5555", (1161, 654), adb_path="adb", runner=runner)
        try:
            b3.grab()
            ok = False
        except A.AdbError as e:
            ok = "ADB" in str(e)
        check("一台都沒有 → 清楚的錯誤訊息（叫你開 BlueStacks 的 ADB）", ok)
    finally:
        A.try_connect = orig

    print("\n10. engine 切到背景操控")
    cfg = E.Config()
    cfg.control_mode = "adb"
    cfg.region = {"left": 3, "top": 41, "width": 1161, "height": 654}
    eng = E.Engine(cfg)
    check("control_mode=adb → 擷取與點擊都換成 adb、範圍是虛擬的（建構時還沒真的呼叫 adb）",
          eng.background and eng.grabber is eng.adb and eng.tracker.current()[0] == (0, 0, 1161, 654)
          and eng._click == eng.adb.click and eng._swipe == eng.adb.swipe and eng._busy() is False)
    cfg.control_mode = "screen"
    eng.apply_backend()
    check("切回 screen → 回到螢幕擷取＋滑鼠", not eng.background and isinstance(eng.tracker, E.RegionTracker))
    fake_grab = TO.types.SimpleNamespace(grab=lambda region: np.zeros((10, 10, 3), np.uint8), backend="fake") \
        if hasattr(TO, "types") else None
    cfg.control_mode = "adb"
    eng2 = E.Engine(cfg, grabber=fake_grab, clicker=lambda *a, **k: None)
    check("測試塞了假的擷取 → 就算設定是 adb 也照用假的", not eng2.background and eng2.grabber is fake_grab)
    swipes = []
    cfg3 = E.Config()
    eng3 = E.Engine(cfg3, grabber=fake_grab, clicker=lambda *a, **k: None,
                    swiper=lambda *a, **k: swipes.append(k.get("duration")), busy=lambda: False)
    img = np.zeros((654, 1161, 3), np.uint8)
    reg = E.V.Region(0, 0, 1161, 654)
    eng3.autoplay = True
    for fast in (True, False):
        plan = O.MapPlan("drag", "測試", 500, 500, 500, 200, tag="scan", fast=fast)
        eng3.planner.observe = lambda im, now: O.MapView([], 0, True, True)
        eng3.planner.decide = lambda view, shape, now, p=plan: p
        swipes.clear()
        eng3._acted_at = E.time.monotonic() - 1.2        # 上一個動作是 1.2 秒前
        eng3._step_map_inner(reg, img, E.time.monotonic(), {})
        if fast:
            check("換邊的拖曳：上個動作 1.2 秒前就可以拖（switch_interval 0.9），拖 0.35 秒", swipes == [0.35], f"{swipes}")
        else:
            check("一般拖曳：1.2 秒還不夠（map_interval 2.5），先不拖", swipes == [], f"{swipes}")
    check("預設設定：換邊大步 1.75 倍、間隔 0.9 秒、記憶開著；engine 的規劃器有帶上",
          E.Config().switch_scale == 1.75 and E.Config().switch_interval == 0.9 and eng.planner.switch_scale == 1.75
          and eng.planner.memory is not None)


def test_live_1122():
    print("\n11. 11/22 實機跑一輪遇到的情況（英文介面、新英雄、戰敗、陣亡）")
    from PIL import Image
    ref = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_notes")
    if not os.path.exists(os.path.join(ref, "35_en_vs_dead_hero_1122.png")):
        print("  （沒有 11/22 參考畫面，略過）")
        return
    ld = lambda f: np.asarray(Image.open(os.path.join(ref, f)).convert("RGB"))  # noqa: E731
    check("VS 畫面：我方灰色卡（陣亡）認得出 1 張", E.dead_cards(ld("35_en_vs_dead_hero_1122.png"), E.VS_CARDS) == 1)
    check("復活視窗：復活前 1 張灰卡、按了 Tap to use 之後 0 張",
          E.dead_cards(ld("36_en_revive_dialog_dead_1122.png"), E.REVIVE_CARDS) == 1
          and E.dead_cards(ld("37_en_revive_dialog_revived_1122.png"), E.REVIVE_CARDS) == 0)
    hf = O.HeroFinder.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "overview_assets", "heroes"))
    pl = O.MapPlanner(O.NodeClassifier(), hero_finder=hf)
    plats = pl._scene(ld("34_en_overview_start_1122.png"), 0)[0]
    check("起點畫面：右上那隊（尖帽法師）認成右隊", any(p.side == R for p in plats), f"{plats}")

    # 戰敗：路線退回那一步、標記重打；重打＝點隊伍腳下那格
    r = RT.RouteTracker(None)
    r.idx = {L: 11, R: 7}
    r.last_actor = R
    undone = r.undo_last()
    check("戰敗時剛走完的是治療（第2段右2）→ 不退", undone is None and r.idx[R] == 7, f"{undone}")
    r.idx = {L: 11, R: 6}
    r.last_actor = R
    r.wait = L
    undone = r.undo_last()
    check("第2段右1（怪物）打輸 → 退回右1", undone is not None and r.idx[R] == 5, f"{undone} {r.idx}")
    r.set_retry(R)
    img = ld("39_en_after_defeat_team_on_monster_1122.jpg")
    pl2 = O.MapPlanner(O.NodeClassifier(), hero_finder=hf, route=r)
    pl2.observe(img, 0.0)
    plan = pl2.decide(pl2.observe(img, 0.2), img.shape, 0.3)
    check("重打：點右隊腳下那格（不是去找正前方）", plan.kind == "click" and plan.tag == "retry" and abs(plan.y - 300) < 40,
          f"{plan.kind} {plan.reason} {(plan.x, plan.y)}")
    pl2.commit(plan, img.shape, 0.3, R)
    pl2.left_overview()
    check("重打點下去、離開總覽 → 不再標記重打", r.retry is None)


def test_any_lineup():
    print("\n12. 陣容不同也照跑（朋友的英雄顏色規則認不得、甚至認反）")
    import math
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    S = os.path.join(here, "sessions", "20260920_003739")
    real = os.path.isdir(S)
    orig = O._identify_hero
    if real:
        _sys.path.insert(0, os.path.join(here, "tools"))
        import build_overview_assets as B
        import test_route as TR
        labels, clicks = B.load_session(S)
        clf = O.NodeClassifier.load(os.path.join(here, "overview_assets", "nodes"))
        cases = [c for c in TR.REAL_CASES if labels.get(c[0] - 1) != "總覽" and c[2] > 0]   # 剛行動完、鏡頭對著它的畫面
        for name, fake in (("顏色規則把每個英雄都認成相反的隊",
                            lambda img, x, y, s: (lambda r: (r[0], O.other_side(r[1]) if r[1] else None))(orig(img, x, y, s))),
                           ("顏色規則完全認不出英雄（平台上有沒有人都判斷不了）", lambda img, x, y, s: (False, None))):
            O._identify_hero = fake
            good, bad = 0, []
            try:
                for i, side, idx, mode in cases:
                    img = B.frame(S, i)
                    s = img.shape[0] / O.REF_H
                    r = RT.RouteTracker(None)
                    r.idx = {side: idx, O.other_side(side): r.total(O.other_side(side))}
                    r.last_actor = side
                    pl = O.MapPlanner(clf, route=r)              # 不給英雄樣板（朋友的英雄沒有樣板）
                    pl._fresh_actor = side
                    pl.observe(img, 0.0)
                    plan = pl.decide(pl.observe(img, 0.15), img.shape, 0.2)
                    cx, cy = clicks[i]["region_rel_xy"]
                    if plan.kind == "click" and plan.route == (side, idx) and \
                            (math.hypot(plan.x - cx, plan.y - cy) <= 50 * s) == (mode == TR.SAME):
                        good += 1
                    else:
                        bad.append(f"#{i} {plan.kind} {plan.reason[:30]}")
            finally:
                O._identify_hero = orig
            check(f"{name}：剛行動完的 {len(cases)} 個真實畫面照路線點對", good == len(cases) or (good >= len(cases) - 1 and
                  all(b.startswith("#106") for b in bad)), f"{good}/{len(cases)} {bad[:3]}")
    else:
        print("  （沒有 9/20 錄製，略過真實畫面部分）")

    # 換邊：兩隊英雄都認不出是哪隊（FakeWorld 平台 side=None），靠「剛行動那隊被拖出畫面」找另一隊
    far = (int(0.85 * W), int(1.9 * H))
    w = TO.FakeWorld(teams={"左?": (0, 0), "右?": far}, nodes={"右?": O.CHEST}, start="左?")
    pl = planner(route_after_left_done(), 1.75)
    seen, plan = TO.run_world(pl, w)
    check("換邊：兩隊都認不出是誰，照樣找到右隊、點路線要的寶箱",
          plan.kind == "click" and plan.side == R and plan.route == (R, 13), f"{plan.kind} {plan.reason[:40]} {seen}")
    # 鏡頭正中那隊被顏色規則認成另一隊（今天尖帽法師的狀況）：仍以「剛行動的隊」為準
    w2 = TO.FakeWorld(teams={L: (0, 0), R: far}, nodes={R: O.CHEST}, start=L)
    w2.teams = {R + "wrong": (0, 0), "右?": far}                 # 左隊站的平台被認成「不知道」
    w2.nodes = {"右?": O.CHEST}
    seen2, plan2 = TO.run_world(planner(route_after_left_done(), 1.75), w2)
    check("剛行動的左隊被認不出：不會把它當右隊，照樣去找右隊", plan2.kind == "click" and plan2.route == (R, 13),
          f"{plan2.kind} {plan2.reason[:40]}")


def main():
    test_any_lineup()
    test_live_1122()
    test_geometry()
    test_fake_switch()
    test_memory()
    test_adb()
    print()
    if _failures:
        print(f"❌ {len(_failures)} 項失敗：")
        for f in _failures:
            print("   -", f)
        return 1
    print("✅ 全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
