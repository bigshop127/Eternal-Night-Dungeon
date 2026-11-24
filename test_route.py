"""固定路線（route.py＋overview.MapPlanner._decide_route＋engine 的進度確認）驗證。
全程用假擷取、假點擊、假拖曳、假時鐘，不開視窗、不動滑鼠、不碰真實遊戲；進度檔寫在暫存資料夾。
有真實錄製（sessions/20260920_003739，跟使用者 10/01 截圖是同一張地圖）時，另外用真實畫面驗證「每一步會點到路線上的那個節點」。

    python test_route.py
"""

import datetime
import json
import math
import os
import shutil
import sys
import tempfile
import types

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_routetest_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX   # 一定要在 import engine／overview 之前

import control as C  # noqa: E402


def _no_real_input(*a, **kw):
    raise RuntimeError("測試不准送出真的滑鼠／鍵盤輸入")


# 9/27 教訓：離線工具忘了注入假的拖曳，送了兩次真的滑鼠拖曳到使用者的桌面。這個行程裡一律換成會炸的版本，
# 萬一哪裡漏注入，測試會失敗而不是去動使用者正在用的電腦
C._send = C.click = C.swipe = C.move_to = C.tap_key = _no_real_input

import engine as E  # noqa: E402
import overview as O  # noqa: E402
import route as RT  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
L, R = O.LEFT, O.RIGHT
_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def walk(r, side, n=1):
    """這隊照路線走 n 步（點了、而且遊戲有反應）。"""
    for _ in range(n):
        r.mark(side)
        r.confirm()


# ---------------------------------------------------------------- 1. 路線資料與進度規則

def test_tracker():
    print("== 1. 路線資料與進度規則（route.RouteTracker）")
    route = RT.ROUTE
    check("左隊 24 步、右隊 22 步（右隊第 2 段多一格荊棘消失後的空格）", len(route[L]) == 24 and len(route[R]) == 22,
          f"{len(route[L])} {len(route[R])}")
    check("每隊最後一步都是裝置", route[L][-1].kind == O.DEVICE and route[R][-1].kind == O.DEVICE)
    check("第 2 段左隊先走", RT.FIRST == {2: L})
    # 9/20 錄製在這四個地方走了另一個方向的同種節點：路線要記對方向
    check("第1段 右1＝左前的寶箱", (route[R][0].kind, route[R][0].dir) == (O.CHEST, RT.LFT))
    check("第3段 右3＝正前的怪物", route[R][11].name(R) == "第3段 右3" and (route[R][11].kind, route[R][11].dir) == (O.ENEMY, RT.FWD))
    check("第4段 左7＝右前的怪物", route[L][22].name(L) == "第4段 左7" and (route[L][22].kind, route[L][22].dir) == (O.ENEMY, RT.RGT))
    check("第4段 右8＝正前的怪物、右9＝左前的裝置",
          (route[R][20].kind, route[R][20].dir, route[R][21].kind, route[R][21].dir) == (O.ENEMY, RT.FWD, O.DEVICE, RT.LFT))
    check("第2段 右4＝空格（荊棘消失的那格）", route[R][8].kind == O.WALK and route[R][8].name(R) == "第2段 右4")
    stages = {sd: sorted({st.stage for st in route[sd]}) for sd in (L, R)}
    check("兩隊都是 3 個遊戲階段", stages == {L: [1, 2, 3], R: [1, 2, 3]}, f"{stages}")

    path = os.path.join(SANDBOX, "rp_test.json")
    r = RT.RouteTracker(path)
    check("一開始兩隊都能走", r.can_act(L) and r.can_act(R) and r.active and not r.finished)
    walk(r, R, 4)
    check("右隊走到第1段的裝置前還能走", r.can_act(R) and r.next(R).kind == O.DEVICE)
    walk(r, R)
    check("右隊點了裝置：換左隊（右隊等著）", r.wait == R and not r.can_act(R) and r.can_act(L), f"wait={r.wait}")
    check("等待的原因說得出來（右隊下一步已經是第2階段：等左隊走完第1階段）", "第 1 階段" in (r.blocked(R) or ""), r.blocked(R) or "")
    walk(r, L, 7)
    check("左隊也點了裝置：第1階段兩隊都走完、沒有誰在等誰", r.wait is None and r.stage_done(L, 1) and r.stage_done(R, 1))
    check("第2段：左隊能走、右隊要等左隊整段走完", r.can_act(L) and not r.can_act(R) and "先整段" in (r.blocked(R) or ""),
          r.blocked(R) or "")
    walk(r, L, 3)
    check("左隊還沒碰到第2段的裝置：右隊照樣等", not r.can_act(R))
    walk(r, L)
    check("左隊點了第2段的裝置：換右隊、左隊停著", r.wait == L and r.can_act(R) and not r.can_act(L))
    r.new_stage()
    check("就算看到過場畫面，左隊第2段那個裝置（只開荊棘）也不會解除等待", r.wait == L)
    walk(r, R, 3)
    check("右隊下一步是空格", r.next(R).kind == O.WALK)
    walk(r, R)
    check("右隊接著走第3段（左隊還在等）", r.next(R).seg == 3 and r.can_act(R) and not r.can_act(L))
    walk(r, R, 4)
    check("右隊點了第3段的裝置：換左隊", r.wait == R and r.can_act(L) and not r.can_act(R))
    walk(r, L, 5)
    check("左隊點了第3段的裝置：第2階段走完、兩隊都能走第4段", r.wait is None and r.can_act(L) and r.can_act(R))
    walk(r, L, 8)
    check("左隊第4段走完：等右隊", r.wait == L and not r.finished and r.can_act(R))
    walk(r, R, 9)
    check("右隊也走完：整條路線走完、不再照路線", r.finished and not r.active and not r.can_act(L))
    check("走完的說明", "走完" in r.summary(), r.summary())

    r2 = RT.RouteTracker.load(path)
    check("進度有存檔、讀回來一樣", r2.idx == r.idx and r2.wait == r.wait and r2.finished)
    r2.pending = (L, 3)
    check("待確認的步跟進度對不上（手動改過）：作廢", r2.confirm() is None and r2.idx[L] == 24)
    r2.restart()
    check("從頭：兩隊歸零並存檔", RT.RouteTracker.load(path).idx == {L: 0, R: 0})
    r2.set_progress(11, 8)
    check("手動調整：左隊停在第2段的裝置上＝左隊在等", r2.wait == L and r2.can_act(R), f"wait={r2.wait}")
    r2.set_progress(11, 13)
    check("手動調整：兩隊都停在裝置上、右隊那個比較晚＝右隊在等", r2.wait == R and r2.can_act(L), f"wait={r2.wait}")
    r2.set_progress(16, 13)
    check("手動調整：第2階段兩隊都走完＝沒有誰在等", r2.wait is None)
    r2.set_progress(999, -5)
    check("手動調整超出範圍會被夾住", r2.idx == {L: 24, R: 0})
    r2.restart()
    check("剛存的進度不算太舊", not r2.stale())
    r2.updated = (datetime.datetime.now() - datetime.timedelta(hours=RT.STALE_HOURS + 1)).isoformat(timespec="seconds")
    check(f"超過 {RT.STALE_HOURS:.0f} 小時沒動＝太舊", r2.stale())
    with open(path, "w", encoding="utf-8") as f:
        f.write("{壞掉")
    check("進度檔壞掉：從頭開始不炸", RT.RouteTracker.load(path).idx == {L: 0, R: 0})
    r3 = RT.RouteTracker(None, enabled=False)
    check("關掉路線：不照路線走", not r3.active and not r3.can_act(L) and "關閉" in r3.summary())


# ---------------------------------------------------------------- 2. 真實畫面：每一步點到路線上的節點

SAME, OTHER = "same", "other"
# (9/20 錄製第幾次點擊, 哪隊, 路線第幾步, same＝你那時點的就是路線要的／other＝你那時走了另一個方向，路線要的是另一個)
REAL_CASES = [
    (1, L, 0, SAME), (4, L, 1, SAME), (8, R, 0, OTHER), (16, R, 2, SAME), (19, R, 3, SAME), (23, R, 4, SAME),
    (27, L, 2, SAME), (30, L, 3, SAME), (34, L, 4, SAME), (37, L, 5, SAME), (41, L, 6, SAME),
    (45, L, 7, SAME), (48, L, 8, SAME), (51, L, 9, SAME), (55, L, 10, SAME), (57, L, 11, SAME), (60, L, 12, SAME),
    (65, R, 5, SAME), (69, R, 6, SAME), (72, R, 7, SAME), (76, R, 8, SAME), (77, R, 9, SAME), (81, R, 10, SAME),
    (84, R, 11, OTHER), (91, L, 13, SAME), (94, L, 14, SAME), (98, L, 15, SAME), (102, L, 16, SAME),
    (106, R, 13, SAME), (109, R, 14, SAME), (112, R, 15, SAME), (116, R, 16, SAME), (119, R, 17, SAME),
    (122, R, 18, SAME), (126, R, 19, SAME), (129, R, 20, OTHER), (137, L, 17, SAME), (140, L, 18, SAME),
    (143, L, 19, SAME), (147, L, 20, SAME), (151, L, 21, SAME), (154, L, 22, OTHER),
]


def test_real_frames():
    print("== 2. 真實畫面（9/20 錄製，同一張地圖）：照路線點到的是不是那個節點")
    S = os.path.join(HERE, "sessions", "20260920_003739")
    if not os.path.isdir(S):
        print("  （沒有錄製資料，略過）")
        return
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import build_overview_assets as B
    labels, clicks = B.load_session(S)
    clf = O.NodeClassifier.load(os.path.join(HERE, "overview_assets", "nodes"))
    hf = O.HeroFinder.load(os.path.join(HERE, "overview_assets", "heroes"))
    good = 0
    bad = []
    for i, side, idx, mode in REAL_CASES:
        img = B.frame(S, i)
        h, w = img.shape[:2]
        s = h / O.REF_H
        r = RT.RouteTracker(None)
        other = O.other_side(side)
        r.idx = {side: idx, other: r.total(other)}       # 只讓這一隊能走（另一隊當作走完了）
        fresh = labels.get(i - 1) != "總覽"               # 剛從彈窗／戰鬥回來＝鏡頭對著剛走完的這隊、還沒拖過
        r.last_actor = side if fresh and idx > 0 else None
        pl = O.MapPlanner(clf, hero_finder=hf, route=r)
        pl._fresh_actor = r.last_actor
        pl._drags = 0 if fresh else 1
        pl.observe(img, 0.0)
        plan = pl.decide(pl.observe(img, 0.15), img.shape, 0.2)
        cx, cy = clicks[i]["region_rel_xy"]
        st = r.route[side][idx]
        if plan.kind != "click" or plan.route != (side, idx):
            bad.append(f"#{i} {st.name(side)}：{plan.kind} {plan.reason}")
            continue
        at = math.hypot(plan.x - cx, plan.y - cy) <= 50 * s
        if at == (mode == SAME):
            good += 1
        else:
            bad.append(f"#{i} {st.name(side)}：點了 ({plan.x},{plan.y})，你那時點 ({cx},{cy})")
    check(f"{len(REAL_CASES)} 個畫面都點到路線要的節點（38 個跟 9/20 一樣、4 個是 9/20 走錯方向的地方）",
          good == len(REAL_CASES), f"{good}/{len(REAL_CASES)} " + "；".join(bad))


# ---------------------------------------------------------------- 3. 規劃器在假畫面上的決策

W, H = 1205, 673         # 使用者現在的範圍大小
S = H / O.REF_H
STAND = (int(O.STAND[0] * W), int(O.STAND[1] * H))


def vec(p, d, hops=1.0):
    """平台 p 往方向 d 一格（hops 格）的位置。"""
    sc = O.platform_scale(p.y, S) * S
    vx, vy = {"F": (0, -205), "L": (-305, -125), "R": (305, -125)}[d]
    return int(round(p.x + vx * sc * hops)), int(round(p.y + vy * sc * hops))


def node(x, y, kind, pinless=False):
    return O.Node(x, y, O.stand_in_pin(x, y, H), kind, 0.0, pinless)


def plat(x, y, side):
    return O.Platform(x, y, 140, 120, side)


class Scene:
    def __init__(self, plats=(), cands=()):
        self.plats, self.cands = list(plats), list(cands)


def planner_with(r, scene, **kw):
    pl = O.MapPlanner(O.NodeClassifier(), route=r, **kw)
    pl._scene = lambda img, now: (scene.plats, scene.cands)
    pl._img = np.zeros((H, W, 3), dtype=np.uint8)
    return pl


def view(nodes):
    return O.MapView(list(nodes), 0, True, True)


def test_planner():
    print("== 3. 規劃器照路線做決定（假畫面）")
    shape = (H, W, 3)
    pc = plat(*STAND, L)

    # 起點：左隊前面正前方、左前方各一個寶箱 → 點正前方那個
    r = RT.RouteTracker(None)
    r.idx = {L: 0, R: r.total(R)}
    sc = Scene([pc])
    fx, fy = vec(pc, "F")
    lx, ly = vec(pc, "L")
    pl = planner_with(r, sc)
    p = pl.decide(view([node(lx, ly, O.CHEST), node(fx, fy, O.CHEST)]), shape, 0.0)
    check("同一格前面兩個寶箱：點正前方的（路線第1段左1）", p.kind == "click" and (p.x, p.y) == (fx, fy) and p.route == (L, 0),
          f"{p.kind} {p.x},{p.y} {p.reason}")
    check("說明寫得出是路線的哪一步", "第1段 左1" in p.reason and "正前的寶箱" in p.reason, p.reason)

    # 只看到左前方的寶箱（正前方那個沒偵測到）：不能改點左前方的，等一下、還是沒有就停手
    pl = planner_with(r, sc, route_miss_seconds=4.0)
    p = pl.decide(view([node(lx, ly, O.CHEST)]), shape, 0.0)
    check("要的節點不在：先等（不改點別的）", p.kind == "wait", f"{p.kind} {p.reason}")
    p = pl.decide(view([node(lx, ly, O.CHEST)]), shape, 4.5)
    check("等了 4 秒還是沒有：停手叫人、說明要點的是什麼", p.kind == "stop" and "第1段 左1" in p.reason and "正前的寶箱" in p.reason
          and "調整" in p.reason, f"{p.kind} {p.reason}")

    # 隊伍在畫面邊邊、前面找不到：先拖到中間再找
    pe = plat(int(0.18 * W), int(0.62 * H), L)
    pl = planner_with(r, Scene([pe]))
    p = pl.decide(view([]), shape, 0.0)
    check("隊伍在畫面邊邊、找不到要的節點：先拖到中間", p.kind == "drag" and p.tag == "center", f"{p.kind} {p.tag} {p.reason}")

    # 第2段：右隊在畫面中間、前面就有怪物，但左隊要先整段走完 → 不點右隊，拖地圖去找左隊
    r = RT.RouteTracker(None)
    r.idx = {L: 7, R: 5}
    pr = plat(*STAND, R)
    fx, fy = vec(pr, "F")
    pl = planner_with(r, Scene([pr]))
    p = pl.decide(view([node(fx, fy, O.ENEMY)]), shape, 0.0)
    check("第2段右隊不能先走：不點右隊的怪物，拖地圖找左隊", p.kind == "drag" and p.tag == "scan" and pl._target == L,
          f"{p.kind} {p.tag} target={pl._target} {p.reason}")
    # 找到左隊了
    pl2 = planner_with(r, Scene([plat(*STAND, L)]))
    fx, fy = vec(plat(*STAND, L), "F")
    p = pl2.decide(view([node(fx, fy, O.CHEST)]), shape, 0.0)
    check("看到左隊：點左隊第2段第1步（正前的寶箱）", p.kind == "click" and p.route == (L, 7), f"{p.kind} {p.route} {p.reason}")

    # 兩隊都看得到、都能走：照舊，寶箱優先
    r = RT.RouteTracker(None)
    r.idx = {L: 0, R: 1}           # 左隊下一步寶箱、右隊下一步怪物
    plA, plB = plat(int(0.30 * W), int(0.60 * H), L), plat(int(0.72 * W), int(0.40 * H), R)
    lx, ly = vec(plA, "F")
    rx, ry = vec(plB, "F")
    pl = planner_with(r, Scene([plA, plB]))
    p = pl.decide(view([node(lx, ly, O.CHEST), node(rx, ry, O.ENEMY)]), shape, 0.0)
    check("兩隊都能走：照舊寶箱優先（點左隊的寶箱）", p.kind == "click" and p.route == (L, 0), f"{p.route} {p.reason}")

    # 治療閘門：左隊下一步治療、右隊自上次治療還沒打過而且下一步就是怪物 → 先讓右隊打
    r = RT.RouteTracker(None)
    r.idx = {L: 4, R: 1}
    pl = planner_with(r, Scene([pc]))
    fx, fy = vec(pc, "F")
    p = pl.decide(view([node(fx, fy, O.HEAL)]), shape, 0.0)
    check("左隊的治療先留著：右隊不在畫面上 → 拖地圖找右隊", p.kind == "drag" and pl._target == R and "先留著" in p.reason,
          f"{p.kind} target={pl._target} {p.reason}")
    pl = planner_with(r, Scene([plA, plB]))
    hx, hy = vec(plA, "F")
    rx, ry = vec(plB, "F")
    p = pl.decide(view([node(hx, hy, O.HEAL), node(rx, ry, O.ENEMY)]), shape, 0.0)
    check("右隊也在畫面上：先點右隊的怪物", p.kind == "click" and p.route == (R, 1) and "先留著" in p.reason, f"{p.route} {p.reason}")
    pl = planner_with(r, Scene([pc]))
    pl._fights[R] = 1
    p = pl.decide(view([node(fx, fy, O.HEAL)]), shape, 0.0)
    check("右隊打過了：左隊點治療", p.kind == "click" and p.route == (L, 4), f"{p.kind} {p.reason}")
    r.idx[R] = 5
    r.wait = R
    pl = planner_with(r, Scene([pc]))
    p = pl.decide(view([node(fx, fy, O.HEAL)]), shape, 0.0)
    check("右隊在等（點過裝置）不能打：治療照點", p.kind == "click" and p.route == (L, 4), f"{p.kind} {p.reason}")

    # 能走的隊不在畫面上：拖地圖找它
    r = RT.RouteTracker(None)
    r.idx = {L: 7, R: 5}
    pl = planner_with(r, Scene([]))
    p = pl.decide(view([]), shape, 0.0)
    check("畫面上沒有能走的隊：拖地圖找左隊", p.kind == "drag" and p.tag == "scan" and pl._target == L, f"{p.kind} {pl._target}")

    # 分類器認錯：方向對、圖釘那格說是怪物，但節點本身的顏色說是治療 → 照樣點
    r = RT.RouteTracker(None)
    r.idx = {L: 4, R: r.total(R)}
    fx, fy = vec(pc, "F")
    pl = planner_with(r, Scene([pc], [O.Cand(O.HEAL, fx, fy + int(30 * S), 50, 60)]))
    p = pl.decide(view([node(fx, fy, O.ENEMY)]), shape, 0.0)
    check("圖釘小圖認錯種類、顏色看對：照路線點", p.kind == "click" and p.route == (L, 4) and "顏色看是治療" in p.reason, p.reason)
    pl = planner_with(r, Scene([pc]))
    p = pl.decide(view([node(fx, fy, O.ENEMY)]), shape, 9.0)
    p = pl.decide(view([node(fx, fy, O.ENEMY)]), shape, 14.0)
    check("兩套都說不是治療：停手（不硬點）", p.kind == "stop" and "怪物" in p.reason, f"{p.kind} {p.reason}")

    # 同方向兩個同種節點分不出來：寧可停
    r = RT.RouteTracker(None)
    r.idx = {L: 0, R: r.total(R)}
    a1 = vec(pc, "F")
    a2 = (a1[0] + int(8 * S), a1[1] - int(60 * S))
    pl = planner_with(r, Scene([pc]))
    p = pl.decide(view([node(*a1, O.CHEST), node(*a2, O.CHEST)]), shape, 0.0)
    check("同一個方向兩個寶箱分不出來：不點", p.kind == "wait" and "分不出" in p.reason, f"{p.kind} {p.reason}")

    # 另一隊的節點不能算到這隊頭上
    r = RT.RouteTracker(None)
    r.idx = {L: 0, R: r.total(R)}
    pL, pR = plat(int(0.30 * W), int(0.62 * H), L), plat(int(0.70 * W), int(0.62 * H), R)
    rx, ry = vec(pR, "L")          # 右隊左前方（離左隊也不遠，但離右隊比較近）
    pl = planner_with(r, Scene([pL, pR]))
    p = pl.decide(view([node(rx, ry, O.CHEST)]), shape, 0.0)
    check("離另一隊比較近的節點不算這隊的選項", p.kind != "click", f"{p.kind} {p.reason}")

    # 點了沒反應：一格遠的點 2 次就停；隔一格空平台的先點中間那格試一次
    r = RT.RouteTracker(None)
    r.idx = {L: 1, R: r.total(R)}
    fx, fy = vec(pc, "F")
    pl = planner_with(r, Scene([pc]))
    v = view([node(fx, fy, O.ENEMY)])
    for t in (0.0, 3.0):
        p = pl.decide(v, shape, t)
        pl.commit(p, shape, t, p.side)
    p = pl.decide(v, shape, 6.0)
    check("同一個節點點 2 次沒反應：停手", p.kind == "stop" and "沒反應" in p.reason, f"{p.kind} {p.reason}")
    r = RT.RouteTracker(None)
    r.idx = {L: 6, R: r.total(R)}          # 第1段左7 裝置（左前）
    far = vec(pc, "L", hops=1.35)
    pl = planner_with(r, Scene([pc]))
    v = view([node(*far, O.DEVICE)])
    for t in (0.0, 3.0):
        p = pl.decide(v, shape, t)
        pl.commit(p, shape, t, p.side)
    p = pl.decide(v, shape, 6.0)
    check("隔一格的點 2 次沒反應：先點中間那格", p.kind == "click" and p.route is None and "中間" in p.reason, f"{p.kind} {p.reason}")
    pl.commit(p, shape, 6.0, p.side)
    p = pl.decide(v, shape, 9.0)
    check("中間那格也試過了：停手", p.kind == "stop", f"{p.kind} {p.reason}")

    # 空格（荊棘消失那格）：沒有圖釘就照位置點；鏡頭跟著動了才算走過去
    r = RT.RouteTracker(None)
    r.idx = {L: r.total(L), R: 8}
    pr = plat(*STAND, R)
    pl = O.MapPlanner(O.NodeClassifier(), route=r)
    pl._scene = lambda img, now: ([pr], [])
    rng = np.random.default_rng(5)

    def ground():
        """暗色地面紋理（跟 test_overview 的 FakeWorld 一樣不會被當成亮色彈窗）＋夠亮的左上角返回鈕（不算「壓暗」）。"""
        img = np.kron(rng.integers(20, 80, (H // 16 + 1, W // 16 + 1, 3)), np.ones((16, 16, 1))).astype(np.uint8)[:H, :W]
        img[0:int(0.075 * H), 0:int(0.36 * W)] = (150, 160, 200)
        return img

    img1 = ground()
    pl.observe(img1, 0.0)
    v = pl.observe(img1, 0.2)
    p = pl.decide(v, shape, 0.3)
    wx, wy = vec(pr, "F")
    check("空格沒有圖釘：照位置點正前方一格", p.kind == "click" and p.route == (R, 8) and abs(p.x - wx) <= 2 and abs(p.y - wy) <= 2,
          f"{p.kind} {p.x},{p.y} vs {wx},{wy} {p.reason}")
    pl.commit(p, shape, 0.3, p.side)
    pl.observe(img1, 0.6)
    check("鏡頭還沒動：還不算走過去", r.idx[R] == 8)
    img2 = ground()            # 鏡頭跟著隊伍走了：整片地面都換了
    pl.observe(img2, 0.9)
    check("鏡頭跟著動了：空格算走完、下一步第3段右1", r.idx[R] == 9 and pl.route_event and "走過去" in pl.route_event,
          f"{r.idx} {pl.route_event}")

    # 路線走完／關掉：回到原本的規則
    r = RT.RouteTracker(None)
    r.idx = {L: r.total(L), R: r.total(R)}
    pl = planner_with(r, Scene([pc]))
    p = pl.decide(view([node(500, 300, O.ENEMY), node(800, 320, O.CHEST)]), shape, 0.0)
    check("路線走完：照原本規則（寶箱優先）", p.kind == "click" and p.route is None and (p.x, p.y) == (800, 320), f"{p.kind} {p.reason}")
    r = RT.RouteTracker(None, enabled=False)
    pl = planner_with(r, Scene([pc]))
    p = pl.decide(view([node(500, 300, O.ENEMY), node(800, 320, O.CHEST)]), shape, 0.0)
    check("路線關掉：照原本規則", p.kind == "click" and p.route is None and (p.x, p.y) == (800, 320), f"{p.kind} {p.reason}")

    # 點下去才記待確認，跟引擎的確認對上
    r = RT.RouteTracker(None)
    r.idx = {L: 0, R: r.total(R)}
    pl = planner_with(r, Scene([pc]))
    fx, fy = vec(pc, "F")
    p = pl.decide(view([node(fx, fy, O.CHEST)]), shape, 0.0)
    check("只規劃不點：沒有待確認的步", r.pending is None)
    pl.commit(p, shape, 0.0, p.side)
    check("點下去：記下待確認 (左隊, 第0步)", r.pending == (L, 0))


# ---------------------------------------------------------------- 4. 引擎：彈窗出現才算走完一步、新的一輪從頭

class FakeGrabber:
    backend = "fake"

    def __init__(self, image):
        self.image = image

    def grab(self, region):
        return self.image.copy()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


def synth_overview():
    img = np.full((H, W, 3), (30, 40, 70), dtype=np.uint8)
    img[0:50, 0:430] = (90, 120, 200)          # 標題列（總覽靠它認）
    return img


def synth_start():
    img = synth_overview()
    img[150:600, 100:1100] = (200, 160, 60)    # 跟一般總覽差很多（整張比對分得開），標題列一樣
    return img


def synth_popup():
    img = synth_overview()
    img[190:500, 320:880] = (170, 200, 240)
    return img


def make_engine(route_steps=None):
    for name in ("config.json", "rules.json", "route_progress.json"):
        p = os.path.join(SANDBOX, name)
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(E.TEMPLATES_DIR, ignore_errors=True)
    if route_steps is not None:
        with open(E.ROUTE_PATH, "w", encoding="utf-8") as f:
            json.dump(route_steps, f)
    cfg = E.Config()
    cfg.region = {"left": 100, "top": 50, "width": W, "height": H}
    cfg.action_interval = 0.0
    cfg.stable_reads = 2
    cfg.map_interval = 1.0
    grab = FakeGrabber(synth_overview())
    calls = []
    eng = E.Engine(cfg, grabber=grab, clicker=lambda x, y, hold=0.06: calls.append(("click", x, y)),
                   swiper=lambda *a, **kw: calls.append(("drag",) + a), busy=lambda: False, guard=lambda x, y: None)
    eng.db.add("總覽", synth_overview(), box=(0.0, 0.0, 0.36, 0.075))
    eng.db.set_fallback("總覽")
    eng.db.set_label_tolerance("總覽", 12.0)
    eng.db.add(E.START_LABEL, synth_start())
    eng.db.add("前往彈窗", synth_popup())
    eng.rules.set_map("總覽")
    eng.rules.set_map(E.START_LABEL)
    eng.rules.set_click("前往彈窗", 0.5, 0.63)
    return eng, grab, calls


def run(eng, clock, n=4, dt=0.2):
    out = []
    st = None
    for _ in range(n):
        clock.t += dt
        st = eng.step()
        if st.acted:
            out.append(st.acted)
    return st, out


def test_engine():
    print("== 4. 引擎：點了、跳出彈窗才算走完一步；新的一輪從頭")
    real_panic, real_mouse, real_time = C.panic_pressed, C.mouse_button_down, E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False
    try:
        def fixed_plan(eng, side=L):
            def decide(view_, shape, now):
                i = eng.route.idx[side]
                return O.MapPlan("click", f"路線測試 {i}", 300, 200, node=node(300, 200, O.CHEST), side=side, route=(side, i))
            eng.planner.decide = decide

        eng, grab, calls = make_engine()
        check("新裝好：路線開著、從起點開始", eng.route.active and eng.route.idx == {L: 0, R: 0})
        fixed_plan(eng)
        eng.start_autoplay()
        st, notes = run(eng, clock)
        check("點了路線的節點：還沒有彈窗＝還不算走完", calls and eng.route.idx[L] == 0 and eng.route.pending == (L, 0),
              f"{calls} {eng.route.idx} {eng.route.pending}")
        grab.image = synth_popup()
        clock.t += 2
        st, notes = run(eng, clock)
        check("跳出彈窗：這一步算走完、進度存檔", eng.route.idx[L] == 1 and RT.RouteTracker.load(E.ROUTE_PATH).idx[L] == 1,
              f"{eng.route.idx}")
        check("動作記錄寫了路線走到哪", any("路線" in n and "第1段 左1" in n for n in notes), f"{notes}")

        # 停手之後你自己點的不算
        grab.image = synth_overview()
        clock.t += 2
        run(eng, clock)
        check("回到總覽又點了一下（待確認第1步）", eng.route.pending == (L, 1), f"{eng.route.pending}")
        eng.stop_autoplay()
        check("停手：待確認的那一步作廢", eng.route.pending is None)
        grab.image = synth_popup()
        clock.t += 2
        run(eng, clock)
        check("關著自動時跳出彈窗：進度不動", eng.route.idx[L] == 1, f"{eng.route.idx}")

        # 新的一輪：開自動時第一個總覽是起始畫面 → 從頭
        eng, grab, calls = make_engine({"version": 1, "left": 5, "right": 3, "wait": None,
                                        "updated": datetime.datetime.now().isoformat(timespec="seconds")})
        check("讀到上次的進度", eng.route.idx == {L: 5, R: 3})
        eng.planner.decide = lambda v, s, n: O.MapPlan("wait", "測試")
        grab.image = synth_start()
        eng.start_autoplay()
        st, notes = run(eng, clock)
        check("開自動、第一個畫面是起始畫面：路線從頭", eng.route.idx == {L: 0, R: 0} and any("起始畫面" in n for n in notes),
              f"{eng.route.idx} {notes}")

        # 接著上次的進度（開自動時在一般總覽）：不歸零
        eng, grab, calls = make_engine({"version": 1, "left": 5, "right": 3, "wait": None,
                                        "updated": datetime.datetime.now().isoformat(timespec="seconds")})
        eng.planner.decide = lambda v, s, n: O.MapPlan("wait", "測試")
        eng.start_autoplay()
        run(eng, clock)
        grab.image = synth_start()       # 開自動之後才看到起始畫面（例如路線還沒走完時被誤認）：不歸零
        run(eng, clock)
        check("開自動時在一般總覽：接著上次的進度", eng.route.idx == {L: 5, R: 3}, f"{eng.route.idx}")

        # 進度太舊：當作新的一輪
        old = (datetime.datetime.now() - datetime.timedelta(hours=RT.STALE_HOURS + 2)).isoformat(timespec="seconds")
        eng, grab, calls = make_engine({"version": 1, "left": 5, "right": 3, "wait": None, "updated": old})
        eng.planner.decide = lambda v, s, n: O.MapPlan("wait", "測試")
        eng.start_autoplay()
        st, notes = run(eng, clock)
        check("進度超過 12 小時沒動：從頭", eng.route.idx == {L: 0, R: 0} and any("小時" in n for n in notes), f"{notes}")

        # 路線走完之後，再看到起始畫面（新的一輪）：從頭
        eng, grab, calls = make_engine({"version": 1, "left": 24, "right": 22, "wait": None,
                                        "updated": datetime.datetime.now().isoformat(timespec="seconds")})
        eng.planner.decide = lambda v, s, n: O.MapPlan("wait", "測試")
        eng.start_autoplay()
        st, notes = run(eng, clock)
        check("路線走完、開自動時在一般總覽：提醒、不歸零", eng.route.finished and any("走完" in n for n in notes), f"{notes}")
        grab.image = synth_start()
        st, notes = run(eng, clock)
        check("路線走完之後看到起始畫面：從頭", eng.route.idx == {L: 0, R: 0}, f"{eng.route.idx}")

        # 設定關掉路線：不從頭、規劃器不照路線
        eng, grab, calls = make_engine({"version": 1, "left": 5, "right": 3, "wait": None,
                                        "updated": datetime.datetime.now().isoformat(timespec="seconds")})
        eng.cfg.route_enabled = False
        eng.rebuild()
        grab.image = synth_start()
        eng.start_autoplay()
        run(eng, clock)
        check("路線關掉：起始畫面也不動進度", eng.route.idx == {L: 5, R: 3} and not eng.route.active)
        check("設定預設開著路線", E.Config().route_enabled is True)
    finally:
        E.time = real_time
        C.panic_pressed, C.mouse_button_down = real_panic, real_mouse


# ---------------------------------------------------------------- 5. 整條路線走一遍（鏡頭跟著行動的隊伍）

def test_full_route():
    print("== 5. 整條路線走一遍：假地圖，鏡頭跟著剛行動的隊，看得到才點、看不到就拖地圖去找")
    shape = (H, W, 3)
    r = RT.RouteTracker(None)
    pl = O.MapPlanner(O.NodeClassifier(), route=r)
    pl._img = np.zeros(shape, dtype=np.uint8)
    cam = {"on": None}             # None＝起始畫面（兩隊都在畫面上）；不然＝鏡頭對著哪隊
    decoy = {"F": "L", "L": "R", "R": "F"}
    other_kind = {O.CHEST: O.ENEMY, O.ENEMY: O.CHEST, O.HEAL: O.CHEST, O.DEVICE: O.ENEMY, O.WALK: O.ENEMY}

    def scene():
        plats, nodes = [], []
        sides = [L, R] if cam["on"] is None else [cam["on"]]
        for sd in sides:
            if cam["on"] is None:          # 起始畫面：野蠻人在左下、法師在右上（跟真的起點一樣）
                xy = (int(0.33 * W), int(0.62 * H)) if sd == L else (int(0.70 * W), int(0.40 * H))
            else:
                xy = STAND
            p = plat(xy[0], xy[1], sd)
            plats.append(p)
            st = r.next(sd)
            if st is None:
                continue
            if st.kind != O.WALK:                    # 空格沒有圖釘（照位置點）
                nodes.append(node(*vec(p, st.dir), st.kind))
            nodes.append(node(*vec(p, decoy[st.dir]), other_kind[st.kind]))   # 旁邊另一個方向的別種節點（不該被點）
        return plats, nodes

    log, stop, t = [], None, 0.0
    for _ in range(600):
        plats, nodes = scene()
        pl._scene = lambda img, now, _p=plats: (_p, [])
        pl._img = np.zeros(shape, dtype=np.uint8)      # 引擎每輪都先 observe（會設這張）；這裡直接給
        plan = pl.decide(view(nodes), shape, t)
        if plan.kind == "stop":
            stop = plan.reason
            break
        if plan.kind == "click" and plan.route is not None:
            side, i = plan.route
            st = r.route[side][i]
            pl.commit(plan, shape, t, plan.side)
            r.confirm()                              # 遊戲有反應（彈窗出現／走過去）
            log.append((side, i, st))
            pl.left_overview()
            if st.kind == O.ENEMY:
                pl.note_victory()
            cam["on"] = side                         # 鏡頭跟著剛行動的隊
        elif plan.kind == "drag":
            pl.commit(plan, shape, t, None)
            if plan.tag == "scan" and pl._target in (L, R):
                cam["on"] = pl._target               # 拖一下就找到（找法另有測試）
        if r.finished:
            break
        t += 3.0
    check("整條路線走完、沒有停手", r.finished and stop is None, f"stop={stop} idx={r.idx}")
    check("兩隊各自照路線的順序一步一步走", [i for sd, i, _ in log if sd == L] == list(range(24))
          and [i for sd, i, _ in log if sd == R] == list(range(22)), f"{len(log)} 步")
    seg2_l = [k for k, (sd, i, st) in enumerate(log) if sd == L and st.seg == 2]
    seg2_r = [k for k, (sd, i, st) in enumerate(log) if sd == R and st.seg == 2]
    check("第2段：左隊整段（含裝置）走完，右隊才開始", seg2_l and seg2_r and max(seg2_l) < min(seg2_r))
    switches = []
    for k, (sd, i, st) in enumerate(log[:-1]):
        if st.kind == O.DEVICE:
            switches.append(log[k + 1][0] != sd or all(r.stage_done(x, st.stage) for x in (L, R)))
    check("每次碰到裝置，下一步都換另一隊（或進了下一階段）", all(switches), f"{switches}")
    stage_order = [st.stage for _, _, st in log]
    check("遊戲階段不會倒退（下一階段的步一定在這一階段兩隊都走完之後）", stage_order == sorted(stage_order), f"{stage_order}")
    heals = [(k, sd) for k, (sd, i, st) in enumerate(log) if st.kind == O.HEAL]
    check("有治療的步都有走到（左 4、右 4）", sum(1 for _, sd in heals if sd == L) == 4 and sum(1 for _, sd in heals if sd == R) == 4,
          f"{heals}")


def test_snapback():
    print("== 6. 換邊拖完鏡頭又跳回剛行動那隊（11/24、11/22 第1段左7 裝置後的真實畫面）")
    from PIL import Image
    clf = O.NodeClassifier.load(os.path.join(HERE, "overview_assets", "nodes"))
    hf = O.HeroFinder.load(os.path.join(HERE, "overview_assets", "heroes"))
    for before, after in (("42_snapback_before_drag_1124.jpg", "43_snapback_camera_back_on_left_1124.jpg"),
                          ("44_snapback_before_drag_1122.jpg", "45_snapback_camera_back_1122.jpg")):
        a = np.asarray(Image.open(os.path.join(HERE, "reference_notes", before)).convert("RGB"))
        b = np.asarray(Image.open(os.path.join(HERE, "reference_notes", after)).convert("RGB"))
        r = RT.RouteTracker(None)
        r.idx = {L: 7, R: 0}
        r.last_actor = L
        pl = O.MapPlanner(clf, hero_finder=hf, route=r, switch_scale=1.75)
        pl._fresh_actor = L
        pl.observe(a, 0.0)
        p = pl.decide(pl.observe(a, 0.15), a.shape, 0.2)
        check(f"{before}：換邊第一下大步拖", p.kind == "drag", f"{p.kind} {p.reason}")
        pl.commit(p, a.shape, 0.2, p.side)
        kinds = []
        t = 3.0
        for _ in range(6):
            p = pl.decide(pl.observe(b, t), b.shape, t + 0.1)
            kinds.append(p.kind)
            if p.kind != "wait":
                break
            t += 2.0
        check(f"{after}：不停手、認出鏡頭跳回左隊，重新從左隊大步找右隊",
              "stop" not in kinds and p.kind == "drag" and pl._fresh_actor == L and pl.drags == 0,
              f"{kinds} {p.reason}")
        # 第二次又一樣（真的找不到）：照舊停手叫人，不會無限重來
        pl.commit(p, b.shape, t + 0.1, p.side)
        kinds = []
        for _ in range(8):
            t += 2.0
            p = pl.decide(pl.observe(b, t), b.shape, t + 0.1)
            kinds.append(p.kind)
            if p.kind != "wait":
                break
        check(f"{after}：同一次總覽第二次又跳回：不再重來（停手或照走法繼續拖）",
              pl._reanchor_n == 1 and p.kind in ("stop", "drag"), f"{kinds} {p.kind} {p.reason}")


def synth_info():
    img = synth_overview()
    img[120:560, 380:820] = (200, 170, 240)    # 紫色資訊框（沒有挑戰鈕），跟前往彈窗分得開
    return img


def test_unreachable():
    print("== 7. 點到兩隊都走不到的節點（11/24：只跳守衛者資訊、沒有挑戰鈕）")
    shape = (H, W, 3)
    pc = plat(*STAND, R)
    # 規劃器：猜出來的隊（發光平台）跟確定的隊都找到時，先點確定的
    r = RT.RouteTracker(None)
    r.idx = {L: 16, R: 18}                 # 第4段 左1 正前的怪物／右6 右前的怪物（11/24 23:32:51 的情況）
    rx, ry = vec(pc, "R")
    pl = planner_with(r, Scene([pc]))
    pl._fresh_actor = R
    gp = plat(250, 330, L)
    gx, gy = vec(gp, "F")
    gn = node(gx, gy, O.ENEMY)
    pl._glow_for = lambda sd, st, v, plats, cands, shp: (gp, O.RouteOpt(gn, 90.0, 200.0), "") if sd == L else None
    v = view([node(rx, ry, O.ENEMY), gn])
    p = pl.decide(v, shape, 0.0)
    check("右隊確定、左隊只是猜的：先點右隊右前的怪物", p.kind == "click" and p.route == (R, 18) and (p.x, p.y) == (rx, ry),
          f"{p.kind} {p.route} {p.reason}")
    # 規劃器：點了、跳資訊 → unreachable → 那一步不算、那格不再點
    pl = planner_with(r, Scene([pc]))
    pl._fresh_actor = R
    v = view([node(rx, ry, O.ENEMY)])
    p = pl.decide(v, shape, 0.0)
    pl.commit(p, shape, 0.0, p.side)
    check("點下去：待確認 (右隊, 18)", r.pending == (R, 18))
    pl.unreachable()
    check("走不到：待確認作廢", r.pending is None)
    p = pl.decide(v, shape, 2.0)
    check("同一個畫面不再點那格", not (p.kind == "click" and (p.x, p.y) == (rx, ry)), f"{p.kind} {p.reason}")
    pl.commit(O.MapPlan("drag", "測試", 500, 300, 520, 330, tag="scan"), shape, 3.0)
    p = pl.decide(v, shape, 5.0)
    check("拖過地圖（鏡頭動了）：禁點作廢", p.kind == "click" and (p.x, p.y) == (rx, ry), f"{p.kind} {p.reason}")

    # 引擎：跳出守衛者資訊＝不算走完，點旁邊關掉
    real_panic, real_mouse, real_time = C.panic_pressed, C.mouse_button_down, E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False
    try:
        eng, grab, calls = make_engine()
        eng.db.add(E.UNREACHABLE_LABEL, synth_info())
        eng.rules.set_click(E.UNREACHABLE_LABEL, 0.8125, 0.5556)
        eng.planner.decide = lambda v_, s_, n_: O.MapPlan("click", "路線測試", 300, 200, node=node(300, 200, O.ENEMY),
                                                          side=L, route=(L, eng.route.idx[L]))
        eng.start_autoplay()
        run(eng, clock)
        check("點了路線的節點：待確認 (左隊, 0)", eng.route.pending == (L, 0), f"{eng.route.pending}")
        grab.image = synth_info()
        clock.t += 2
        st, notes = run(eng, clock)
        check("跳出守衛者資訊：路線不前進", eng.route.idx[L] == 0 and eng.route.pending is None, f"{eng.route.idx} {eng.route.pending}")
        x, y = calls[-1][1], calls[-1][2]
        check("點旁邊空白關掉", calls[-1][0] == "click" and abs(x - (100 + 0.8125 * W)) <= 3 and abs(y - (50 + 0.5556 * H)) <= 3,
              f"{calls[-1]}")
        check("動作記錄寫了走不到", any("走不到" in n for n in notes), f"{notes}")
        check("那格記成禁點", len(eng.planner._banned) == 1, f"{eng.planner._banned}")
    finally:
        E.time = real_time
        C.panic_pressed, C.mouse_button_down = real_panic, real_mouse


def main():
    try:
        test_tracker()
        test_real_frames()
        test_planner()
        test_engine()
        test_full_route()
        test_snapback()
        test_unreachable()
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if _failures:
        print(f"失敗 {len(_failures)} 項：")
        for f in _failures:
            print("  -", f)
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
