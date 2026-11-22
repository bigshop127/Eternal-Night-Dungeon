"""overview.py（地城總覽自動選節點）與 engine.py 的總覽整合驗證。
全程用假擷取、假點擊、假拖曳、假時鐘，不開視窗、不動滑鼠、不碰真實遊戲；
設定／樣板／規則都寫在暫存資料夾。有真實錄製（sessions/20260920_003739）時另外做真實畫面的回歸驗證，沒有就略過。

    python test_overview.py
"""

import os
import shutil
import sys
import tempfile
import types

import numpy as np
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SANDBOX = tempfile.mkdtemp(prefix="endungeon_ovtest_")
os.environ["ENDUNGEON_DATA_DIR"] = SANDBOX   # 一定要在 import engine／overview 之前

import control as C  # noqa: E402
import engine as E  # noqa: E402
import overview as O  # noqa: E402
import vision_state as V  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


# ---------------------------------------------------------------- 合成畫面

W, H = 1478, 828
KIND_COLOR = {
    O.CHEST: (215, 165, 40),     # 金（刻意 R < 225，不會被黃色圖釘遮罩吃掉）
    O.HEAL: (60, 230, 90),       # 亮綠
    O.DEVICE: (170, 60, 220),    # 紫
    O.REVIVE: (60, 200, 240),    # 青藍
    O.ENEMY: (200, 70, 60),      # 紅棕
}
BG = (30, 40, 70)


def synth_map(pins=(), popup=False, hud_yellow=False, wide_yellow=False, bar=True):
    """pins: [(cx, bottom, kind)]。每個圖釘＝黃色 20×48 直條；圖釘底端往下 105px 處畫該種類顏色的 70×70 方塊。"""
    img = np.full((H, W, 3), BG, dtype=np.uint8)
    if bar:
        img[0:60, 0:520] = (90, 120, 200)          # 標題列（總覽的後備樣板靠它認）
    for cx, bottom, kind in pins:
        img[max(bottom - 47, 0):bottom + 1, cx - 10:cx + 10] = (255, 210, 60)
        ty = bottom + O.PIN_TO_NODE_DY
        img[max(ty - 45, 0):ty + 25, cx - 35:cx + 35] = KIND_COLOR[kind]
    if hud_yellow:                                   # 右下角介面上的黃色瘦長塊，不是圖釘
        img[700:760, 1300:1320] = (255, 210, 60)
    if wide_yellow:                                  # 寬扁的黃色塊（寶箱鑲邊之類），不是圖釘
        img[300:330, 600:670] = (255, 210, 60)
    if popup:                                        # 亮色彈窗：中央一大塊淺藍
        img[230:600, 400:1080] = (170, 200, 240)
    return img


def synth_classifier():
    clf = O.NodeClassifier()
    rng = np.random.default_rng(1)
    for kind in KIND_COLOR:
        for _ in range(4):
            cx, bottom = int(rng.integers(300, 1100)), int(rng.integers(150, 400))
            img = synth_map([(cx, bottom, kind)])
            noisy = np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
            clf.add(kind, O.node_patch(noisy, cx, bottom + O.PIN_TO_NODE_DY))
    return clf


def new_planner(**kw):
    return O.MapPlanner(synth_classifier(), **kw)


def observe_twice(pl, img, t=0.0):
    """第一輪圖釘都是新的（算「還在動」），第二輪才算穩定——引擎連續兩輪讀到同樣的圖就是這樣。"""
    pl.observe(img, t)
    return pl.observe(img, t + 0.15)


class FakeWorld:
    """會動的假地圖（測「拖地圖找另一隊」用）：一大張暗色方塊紋理當地面（拖了畫面就會變），鏡頭照手指拖的方向移動
    （內容跟著手指跑，沒有透視），超出 bounds 就卡在邊上（跟真的地圖邊一樣拖不動）。
    teams＝{隊: (世界 x, 世界 y)}：隊伍平台；nodes＝{隊: 種類}：該隊正前方有一個沒圖釘的節點（沒列的隊前面沒東西）。
    規劃器的 _scene（平台＋候選節點偵測）換成直接回報「現在畫面裡看得到的」——偵測本身另有真實畫面的測試。"""

    BLOCK = 16

    def __init__(self, teams=None, nodes=None, start=None, bounds=((-4000, 4000), (-4000, 4000)), seed=3):
        self.teams = dict(teams or {})
        self.nodes = dict(nodes or {})
        self.bounds = bounds
        span = 12000
        self.off = span // 2
        rng = np.random.default_rng(seed)
        n = span // self.BLOCK + 1
        self.tex = rng.integers(20, 80, (n, n, 3)).astype(np.uint8)
        side = start if start is not None else next(iter(self.teams), None)
        wx, wy = self.teams.get(side, (0, 0))
        self.cam = [0, 0]
        self._set(wx - O.STAND[0] * W, wy - O.STAND[1] * H)     # 一開始鏡頭對著 start 那隊（遊戲會跟著剛行動的隊）
        self.drags = []
        self.clicks = 0

    def _set(self, x, y):
        (x0, x1), (y0, y1) = self.bounds
        self.cam = [int(round(min(max(x, x0), x1))), int(round(min(max(y, y0), y1)))]

    def view(self):
        ys = (np.arange(H) + self.cam[1] + self.off) // self.BLOCK
        xs = (np.arange(W) + self.cam[0] + self.off) // self.BLOCK
        img = self.tex[ys][:, xs].copy()
        img[0:60, 0:520] = (90, 120, 200)        # 標題列（介面，不跟地圖動；也讓畫面不被當成「壓暗」）
        return img

    def at(self, wx, wy):
        return wx - self.cam[0], wy - self.cam[1]

    def drag(self, plan):
        self.drags.append(plan)
        self._set(self.cam[0] - (plan.x1 - plan.x), self.cam[1] - (plan.y1 - plan.y))

    def scene(self):
        plats, cands = [], []
        s = H / O.REF_H
        for side, (wx, wy) in self.teams.items():
            x, y = self.at(wx, wy)
            if not (40 <= x < W - 40 and 0.12 * H <= y < H - 30) or O._in_hud(x, y, W, H):
                continue
            plats.append(O.Platform(int(x), int(y), 140, 120, side if side in O.SIDES else None))    # 其他名字＝認不出是哪隊
            kind = self.nodes.get(side)
            if kind is None:
                continue
            cy = y - 190 * O.platform_scale(y, s) * s          # 正前方一格
            # 貼在畫面邊邊的節點會被切掉／落在按鈕禁區（真的偵測也是這樣）：只認畫面中間 84% 寬的
            if 0.08 * W <= x <= 0.92 * W and 0.09 * H <= cy < H - 2 and not O._in_zone(x, cy, W, H, O.KEEP_OUT_ZONES):
                cands.append(O.Cand(kind, int(x), int(cy), 70, 60))
        return plats, cands


def run_world(pl, world, n=400, dt=0.5, t=0.0, accept=lambda plan: True):
    """規劃器在假地圖上跑到點下去（accept 說這一下遊戲有反應）或停手。回傳 (每次拖的「往哪看」, 最後的規劃)。
    accept 回 False 的點擊＝遊戲沒反應：畫面不變、規劃器繼續。"""
    pl._scene = lambda img, now: world.scene()
    seen, plan = [], None
    for _ in range(n):
        img = world.view()
        plan = pl.decide(pl.observe(img, t), img.shape, t)
        if plan.kind == "drag":
            world.drag(plan)
            seen.append(O.look_name((plan.x / (W - 1), plan.y / (H - 1), plan.x1 / (W - 1), plan.y1 / (H - 1)))
                        if plan.tag == "scan" else "拖到中間")
        pl.commit(plan, img.shape, t, plan.side)
        if plan.kind == "click":
            world.clicks += 1
        if plan.kind == "stop" or (plan.kind == "click" and accept(plan)):
            break
        t += dt
    return seen, plan


# ---------------------------------------------------------------- 各節

def test_pins_real():
    print("== 1. 圖釘偵測（真實截圖）")
    path = os.path.join(HERE, "reference_notes", "12_overview_pins_0924.png")
    img = np.asarray(Image.open(path).convert("RGB"))
    pins = O.find_pins(img)
    check("使用者 9/24 截圖：5 個圖釘", len(pins) == 5, f"實際 {len(pins)}：{[(p.cx, p.bottom) for p in pins]}")
    want = [956, 1218, 736, 441, 73]
    check("位置對得上肉眼看到的 5 個", sorted(p.cx for p in pins) == sorted(want) or
          all(any(abs(p.cx - w) <= 6 for p in pins) for w in want))
    check("包含頂端被切掉一截的那個", any(p.top <= 2 for p in pins))
    q = np.asarray(Image.open(os.path.join(HERE, "reference_notes", "10_地圖總覽_重置按鈕位置.png")).convert("RGB"))
    check("金色問號（命運的契機）不算圖釘", O.find_pins(q) == [], f"{O.find_pins(q)}")
    check("這張截圖不是亮色彈窗", O.pale_fraction(img) <= O.PALE_MAX, f"{O.pale_fraction(img):.4f}")


def test_pins_synthetic():
    print("== 2. 圖釘偵測（合成畫面）")
    img = synth_map([(400, 200, O.CHEST), (900, 320, O.ENEMY)])
    pins = O.find_pins(img)
    check("兩個圖釘都找到", len(pins) == 2, f"{[(p.cx, p.bottom) for p in pins]}")
    check("位置準確", {(p.cx, p.bottom) for p in pins} == {(400, 200), (900, 320)} or
          all(abs(p.cx - c) <= 1 and abs(p.bottom - b) <= 1 for p, (c, b) in zip(pins, [(400, 200), (900, 320)])))
    check("介面右下角的黃色塊不算", O.find_pins(synth_map(hud_yellow=True)) == [])
    check("寬扁的黃色塊不算", O.find_pins(synth_map(wide_yellow=True)) == [])
    check("沒有圖釘就是空的", O.find_pins(synth_map()) == [])
    # 頂端被切掉：圖釘只剩下面一小截，仍要找得到，而且點位以底端為準
    clipped = synth_map([(700, 24, O.CHEST)])
    cp = O.find_pins(clipped)
    check("頂端被切掉一截的圖釘仍找得到", len(cp) == 1 and cp[0].top == 0, f"{cp}")
    x, y = O.node_point(cp[0], H)
    check("點位＝底端往下 105px（不受被切掉影響）", (x, y) == (700, 24 + round(105 * H / 818)), f"{(x, y)}")
    check("彈窗（淺藍面板）會被偵測到", O.pale_fraction(synth_map(popup=True)) > O.PALE_MAX)
    check("純地圖不會", O.pale_fraction(synth_map([(400, 200, O.CHEST)])) <= O.PALE_MAX)


def test_classifier():
    print("== 3. 節點分類器")
    clf = synth_classifier()
    ok = 0
    for kind in KIND_COLOR:
        img = synth_map([(555, 260, kind)])
        got, d = clf.classify(O.node_patch(img, 555, 260 + O.PIN_TO_NODE_DY))
        ok += got == O.KIND_ALIAS.get(kind, kind)
    check("五種合成節點全分對（藍色泉水＝復活色，9/25 起一律併進治療）", ok == 5, f"{ok}/5")
    ok_n, total, _ = clf.leave_one_out()
    check("留一驗證全對（合成）", ok_n == total, f"{ok_n}/{total}")
    noise = np.random.default_rng(7).integers(0, 256, (O.STORE, O.STORE, 3)).astype(np.uint8)
    got_n, d_n = clf.classify(noise)
    clf.reject = d_n * 0.5
    got, d_ok = clf.classify(O.node_patch(synth_map([(555, 260, O.HEAL)]), 555, 260 + O.PIN_TO_NODE_DY))
    check("正常節點在拒絕門檻內、照常分類", got == O.HEAL, f"{got} d={d_ok:.3f} reject={clf.reject:.3f}")
    got_far, _ = clf.classify(noise)
    check("離所有樣板太遠的東西（雜訊）回「認不得」", got_far is None, f"{got_far}")
    clf.reject = float("inf")
    d = os.path.join(SANDBOX, "clf")
    clf.save(d)
    back = O.NodeClassifier.load(d)
    check("存檔再讀回來：張數一樣", len(back) == len(clf))
    got2, _ = back.classify(O.node_patch(synth_map([(555, 260, O.DEVICE)]), 555, 260 + O.PIN_TO_NODE_DY))
    check("存檔再讀回來：還是分得對", got2 == O.DEVICE, f"{got2}")
    check("空資料夾讀出來是空的分類器", len(O.NodeClassifier.load(os.path.join(SANDBOX, "nope"))) == 0)
    got3, _ = O.NodeClassifier().classify(O.node_patch(synth_map(), 100, 100))
    check("沒有任何樣板時回「認不得」", got3 is None)


def mk_node(kind, x=100, y=200, bottom=100):
    return O.Node(x, y, O.Pin(x, bottom - 48, bottom, 20, 48, 700), kind, 0.5)


def test_choose():
    print("== 4. 優先序")
    nodes = [mk_node(O.ENEMY, 100, 300, 200), mk_node(O.HEAL, 200, 300, 200), mk_node(O.CHEST, 300, 400, 300)]
    check("有寶箱先點寶箱", O.choose(nodes).kind == O.CHEST)
    nodes = [mk_node(O.HEAL, 200, 300, 200), mk_node(O.ENEMY, 100, 300, 250), mk_node(O.DEVICE, 300, 300, 200)]
    check("沒寶箱：治療 > 怪物 > 裝置（使用者：寶箱、治療為主）", O.choose(nodes).kind == O.HEAL)
    nodes = [mk_node(O.DEVICE, 300, 300, 200), mk_node(O.ENEMY, 100, 300, 250), mk_node(O.REVIVE, 200, 300, 200)]
    check("沒寶箱也沒治療：怪物 > 復活 > 裝置", O.choose(nodes).kind == O.ENEMY)
    check("預設優先序＝寶箱、治療、怪物、復活、裝置",
          O.DEFAULT_PRIORITY == (O.CHEST, O.HEAL, O.ENEMY, O.REVIVE, O.DEVICE), f"{O.DEFAULT_PRIORITY}")
    check("治療比它旁邊的怪物優先（9/24 使用者回報的問題）",
          O.choose([mk_node(O.ENEMY, 200, 300, 200), mk_node(O.HEAL, 100, 300, 250)]).kind == O.HEAL)
    nodes = [mk_node(O.DEVICE, 300, 300, 200), mk_node(O.HEAL, 200, 300, 200)]
    check("只剩治療跟裝置：先治療", O.choose(nodes).kind == O.HEAL)
    nodes = [mk_node(O.DEVICE, 300, 300, 200), mk_node(O.REVIVE, 200, 300, 200)]
    check("復活比裝置優先（分類器 9/25 起不再產生復活，直接給節點才測得到）", O.choose(nodes).kind == O.REVIVE)
    check("只剩裝置才點裝置", O.choose([mk_node(O.DEVICE)]).kind == O.DEVICE)
    a, b = mk_node(O.CHEST, 100, 400, 300), mk_node(O.CHEST, 500, 300, 200)
    check("同一層有好幾個：取畫面最上面的", O.choose([a, b]) is b)
    check("認不得種類的節點不會被挑", O.choose([mk_node(None)]) is None)
    check("認不得的跟認得的一起：挑認得的", O.choose([mk_node(None, bottom=50), mk_node(O.HEAL)]).kind == O.HEAL)
    check("空清單回 None", O.choose([]) is None)
    check("可換優先序", O.choose([mk_node(O.HEAL), mk_node(O.CHEST)], (O.HEAL, O.CHEST)).kind == O.HEAL)


def test_planner():
    print("== 5. 規劃器（點／拖／等／停）")
    pins = [(400, 200, O.ENEMY), (900, 220, O.CHEST), (1100, 240, O.HEAL)]
    img = synth_map(pins)
    pl = new_planner()

    v = pl.observe(img, 0.0)
    check("第一輪：認得 3 個節點、種類都對",
          [n.kind for n in sorted(v.nodes, key=lambda n: n.x)] == [O.ENEMY, O.CHEST, O.HEAL],
          f"{[n.kind for n in v.nodes]}")
    check("第一輪圖釘都是新的，還不能算穩定", v.stable is False)
    plan = pl.decide(v, img.shape, 0.0)
    check("不穩定就等", plan.kind == "wait", plan.reason)
    v = pl.observe(img, 0.15)
    check("第二輪穩定", v.stable is True)
    plan = pl.decide(v, img.shape, 0.15)
    check("點寶箱", plan.kind == "click" and plan.node.kind == O.CHEST, f"{plan.kind} {plan.reason}")
    check("點位在寶箱節點上", abs(plan.x - 900) <= 1 and abs(plan.y - (220 + round(105 * H / 818))) <= 1, f"{(plan.x, plan.y)}")

    # 圖釘會上下浮動（±24px）：只有 y 變，不影響「穩定」
    bob = synth_map([(400, 200 + 18, O.ENEMY), (900, 220 - 20, O.CHEST), (1100, 240 + 9, O.HEAL)])
    v = pl.observe(bob, 0.3)
    check("圖釘上下浮動（±20px）仍算穩定", v.stable is True and len(v.nodes) == 3)
    # 上下跳超過浮動幅度＝地圖在動（例如往下拖的慣性捲動）
    jump = synth_map([(400, 200 + 80, O.ENEMY), (900, 220 + 80, O.CHEST), (1100, 240 + 80, O.HEAL)])
    check("上下跳 80px（超過浮動幅度）算地圖在動", pl.observe(jump, 0.45).stable is False)
    shifted = synth_map([(400 + 60, 200, O.ENEMY), (900 + 60, 220, O.CHEST), (1100 + 60, 240, O.HEAL)])
    check("整個地圖左右移動也算在動", new_planner().observe(img, 0.0) and pl.observe(shifted, 0.6).stable is False)

    # 圖釘閃爍：熄滅期間不能當成「沒圖釘」而去拖地圖
    pl = new_planner()
    observe_twice(pl, img, 0.0)
    empty = synth_map()
    v = pl.observe(empty, 0.6)                # 熄滅 0.45 秒：還在「現在有」的範圍內
    check("熄滅不到 fresh_seconds：節點仍算現在有（用最後看到的位置）", len(v.nodes) == 3 and v.hidden == 0, f"{len(v.nodes)}/{v.hidden}")
    v = pl.observe(empty, 1.4)                # 熄滅 1.25 秒：不算現在有，但記得
    check("熄滅超過 fresh_seconds：先當暫時看不到", v.nodes == [] and v.hidden == 3, f"{len(v.nodes)}/{v.hidden}")
    p = pl.decide(v, empty.shape, 1.4)
    check("暫時看不到時只等、不拖地圖", p.kind == "wait" and "閃爍" in p.reason, p.reason)
    v = pl.observe(empty, 1.9)
    check("熄滅 1.75 秒還沒過記憶時間（2.0），仍不拖", pl.decide(v, empty.shape, 1.9).kind == "wait")
    v = pl.observe(img, 1.95)                 # 亮回來
    check("亮回來：位置沒變，穩定，照常能點", v.stable is True and len(v.nodes) == 3 and pl.decide(v, img.shape, 1.95).kind == "click",
          f"stable={v.stable} n={len(v.nodes)}")

    # 點了沒反應：第 1、2 次照點，第 3 次換別的
    pl = new_planner()
    observe_twice(pl, img, 0.0)
    seq = []
    for i, t in enumerate((0.3, 3.0, 6.0)):
        v = pl.observe(img, t)
        plan = pl.decide(v, img.shape, t)
        seq.append(plan.node.kind if plan.kind == "click" else plan.kind)
        pl.commit(plan, img.shape, t)
    check("同一節點點 2 次沒反應後改點下一優先（怪物）", seq == [O.CHEST, O.CHEST, O.ENEMY], f"{seq}")
    v = pl.observe(img, 6.0 + 65.0)
    later = pl.decide(v, img.shape, 6.0 + 65.0)
    check("跳過的節點 60 秒後可以再試", later.kind == "click" and later.node.kind == O.CHEST, f"{later.kind}")
    pl.left_overview()
    observe_twice(pl, img, 80.0)
    check("離開過總覽（進了彈窗）就重新開始計次",
          pl.decide(pl.observe(img, 80.3), img.shape, 80.3).node.kind == O.CHEST)

    # 亮色彈窗疊著：不動
    pl = new_planner()
    pop = synth_map(pins, popup=True)
    v = observe_twice(pl, pop)
    check("彈窗疊著時看不到節點也不動", v.clean is False and v.nodes == [] and pl.decide(v, pop.shape, 0.0).kind == "wait")

    # 只有認不得種類的節點：等，等太久停
    clf = O.NodeClassifier()
    clf.add(O.CHEST, O.node_patch(synth_map([(500, 200, O.CHEST)]), 500, 200 + O.PIN_TO_NODE_DY))
    clf.reject = 1e-6
    pl = O.MapPlanner(clf, stuck_timeout=30.0)
    observe_twice(pl, img, 0.0)
    p1 = pl.decide(pl.observe(img, 0.3), img.shape, 0.3)
    check("只有認不得種類的節點：先等、提示請使用者點", p1.kind == "wait" and "請你自己點" in p1.reason, p1.reason)
    p2 = pl.decide(pl.observe(img, 31.0), img.shape, 31.0)
    check("等超過 stuck_timeout 就停手", p2.kind == "stop", p2.reason)

    # 沒圖釘：先確認 3.5 秒不是閃爍，才拖地圖；之後每次隔 1.5 秒；最多 N 次然後停
    pl = new_planner(max_scan_drags=3)
    kinds, t = [], 0.0
    for _ in range(40):
        v = pl.observe(empty, t)
        plan = pl.decide(v, empty.shape, t)
        kinds.append(plan.kind)
        pl.commit(plan, empty.shape, t)
        if plan.kind == "stop":
            break
        t += 0.5
    first_drag = kinds.index("drag")
    check("沒圖釘：等滿 no_pin_seconds（2 秒）才第一次拖（前面都在等）",
          first_drag == 4 and kinds[:4] == ["wait"] * 4, f"{kinds[:9]}")
    check("拖 3 次後停手", kinds.count("drag") == 3 and kinds[-1] == "stop", f"drags={kinds.count('drag')} last={kinds[-1]}")
    pl = new_planner()
    pl.decide(pl.observe(empty, 0.0), empty.shape, 0.0)       # 引擎每一輪都會呼叫，計時從第一次沒看到圖釘開始
    plan = pl.decide(pl.observe(empty, 2.1), empty.shape, 2.1)
    check("第一次拖：先「往下看」（手指往上拖，看被拋在後面的另一隊；9/25 使用者回報以前從不往下看）",
          plan.kind == "drag" and plan.y1 < plan.y and abs(plan.x1 - plan.x) <= 2 and "往下看" in plan.reason, f"{plan}")
    check("拖曳座標在畫面內", 0 <= plan.x < W and 0 <= plan.x1 < W and 0 <= plan.y < H and 0 <= plan.y1 < H)
    # 拖到看到圖釘就重置拖曳計數
    pl.commit(plan, empty.shape, 2.1)
    check("拖完舊的圖釘位置作廢", pl._tracks == [])
    observe_twice(pl, img, 4.0)
    plan = pl.decide(pl.observe(img, 4.3), img.shape, 4.3)
    pl.commit(plan, img.shape, 4.3)
    check("點了之後畫面還在總覽（可能沒反應）：拖曳計數不歸零，免得「點不動→重新找」一直循環", plan.kind == "click" and pl.drags == 1)
    pl.left_overview()
    check("點有效（畫面離開總覽）：拖曳計數歸零", pl.drags == 0)

    # 等太久（任何原因）就停：地圖一直在動（每一輪圖釘都出現在新位置）
    pl = new_planner(stuck_timeout=20.0)
    r = []
    for i in range(7):
        moving = synth_map([(400 + 70 * (i % 5), 200, O.ENEMY), (900 - 40 * (i % 4), 220, O.CHEST)])
        r.append(pl.decide(pl.observe(moving, i * 5.0), moving.shape, i * 5.0).kind)
    check("一直在動的地圖：等超過 20 秒後停手", "stop" in r, f"{r}")

    # 目標落在介面上／畫面外的節點要丟掉
    low = synth_map([(1300, 640, O.CHEST)])     # 點位在 745 附近、x=1300：右下角介面
    check("點位落在右下角介面上的節點被丟掉", new_planner().observe(low).nodes == [])
    wheel = synth_map([(1370, 110, O.CHEST)])   # 點位在 (1370, ~215)：右邊的獎勵輪盤六角圖示
    check("點位落在獎勵輪盤圖示上的節點被丟掉", new_planner().observe(wheel).nodes == [])
    fbtn = synth_map([(20, 190, O.CHEST)])      # 點位在 (20, ~295)：左邊浮動的「f」按鈕
    check("點位落在左邊「f」按鈕上的節點被丟掉", new_planner().observe(fbtn).nodes == [])
    ok_edge = synth_map([(73, 338, O.CHEST)])   # 你 9/24 截圖裡最左邊那個真圖釘：點位 (73, ~444)，不在禁區
    check("最左邊的真節點（x=73）不會被誤丟", len(new_planner().observe(ok_edge).nodes) == 1)


class FakeHeroes:
    """假的英雄偵測：直接回傳指定位置（測規劃器的左右隊邏輯，不必跑真的模板比對）。"""
    ready = True

    def __init__(self, **pos):
        self.pos = pos
        self.calls = 0

    def find(self, img):
        self.calls += 1
        return {side: O.Hero(side, x, y, 0.9) for side, (x, y) in self.pos.items()}


def test_heroes_real():
    print("== 5b. 英雄偵測與左右隊（真實截圖）")
    # 測試用暫存資料夾，但英雄樣板要讀專案裡真的那份（唯讀）
    hf = O.HeroFinder.load(os.path.join(HERE, "overview_assets", "heroes"))
    if not hf.ready:
        print("  （沒有英雄樣板 overview_assets/heroes，略過）")
        return
    check("兩張英雄樣板都載入（野蠻人＝left、法師＝right）", set(hf.templates) == {O.LEFT, O.RIGHT})
    ref = os.path.join(HERE, "reference_notes")
    cases = {          # 檔名: (野蠻人位置, 法師位置)——位置是用肉眼在圖上量的
        "12_overview_pins_0924.png": ((430, 564), (988, 238)),
        "13_overview_reframed_0924.png": ((464, 660), (1046, 288)),
        "16_overview_both_heroes_0924.png": ((532, 430), (1020, 166)),
    }
    for name, (lp, rp) in cases.items():
        img = np.asarray(Image.open(os.path.join(ref, name)).convert("RGB"))
        found = hf.find(img)
        ok = (O.LEFT in found and O.RIGHT in found
              and abs(found[O.LEFT].x - lp[0]) <= 40 and abs(found[O.LEFT].y - lp[1]) <= 40
              and abs(found[O.RIGHT].x - rp[0]) <= 40 and abs(found[O.RIGHT].y - rp[1]) <= 40)
        check(f"{name}：野蠻人／法師都找到、位置對", ok, f"{ {k: (v.x, v.y, round(v.score, 2)) for k, v in found.items()} }")
    # 圖釘 → 隊：使用者 9/24 的描述——野蠻人這邊 2 個圖釘、法師那邊 2~3 個
    img = np.asarray(Image.open(os.path.join(ref, "16_overview_both_heroes_0924.png")).convert("RGB"))
    found = hf.find(img)
    nodes = [O.Node(*O.node_point(p, img.shape[0]), p, None, 0.0) for p in O.find_pins(img)]
    sides = {n.pin.cx: O.side_of(n, found, img.shape[1]) for n in nodes}
    left_x = sorted(k for k, v in sides.items() if v == O.LEFT)
    right_x = sorted(k for k, v in sides.items() if v == O.RIGHT)
    check("野蠻人身邊的 2 個圖釘（x≈220、534）算左隊", len(left_x) == 2 and abs(left_x[0] - 220) <= 8 and abs(left_x[1] - 534) <= 8, f"{sides}")
    check("法師身邊的 2 個圖釘（x≈786、1225）算右隊", len(right_x) == 2 and abs(right_x[0] - 786) <= 8 and abs(right_x[1] - 1225) <= 8, f"{sides}")
    # 什麼都沒有的畫面：不能誤認
    blank = np.full((834, 1475, 3), (35, 45, 80), np.uint8)
    check("沒有英雄的畫面不會誤認", hf.find(blank) == {})


def test_sides_and_heal():
    print("== 5c. 左右隊判斷與治療閘門")
    n = lambda x, bottom: O.Node(x, bottom + 105, O.Pin(x, bottom - 48, bottom, 20, 48, 700), O.HEAL, 0.5)
    heroes = {O.LEFT: O.Hero(O.LEFT, 400, 600, 0.9), O.RIGHT: O.Hero(O.RIGHT, 1000, 300, 0.9)}
    check("離野蠻人近的圖釘＝左隊", O.side_of(n(300, 450), heroes, 1475) == O.LEFT)
    check("離法師近的圖釘＝右隊", O.side_of(n(1100, 150), heroes, 1475) == O.RIGHT)
    only_left = {O.LEFT: O.Hero(O.LEFT, 400, 600, 0.9)}
    check("只偵測到一位英雄、圖釘離它近：算它那隊", O.side_of(n(500, 480), only_left, 1475) == O.LEFT)
    check("只偵測到一位英雄、圖釘離它太遠：改用畫面右半＝右隊", O.side_of(n(1300, 100), only_left, 1475) == O.RIGHT)
    check("沒偵測到英雄：畫面左半＝左隊", O.side_of(n(200, 300), {}, 1475) == O.LEFT)
    check("沒偵測到英雄：畫面右半＝右隊", O.side_of(n(900, 300), {}, 1475) == O.RIGHT)

    # 治療閘門（choose_for_teams）：治療排在怪物前面，但要「另一隊」打過才輪到；沒輪到就讓另一隊先打，
    # 絕不能降級去點同一個岔路旁邊的怪物（那樣這條路的治療就沒了）
    heal_r = mk_node(O.HEAL, 1100, 320, 220)
    enemy_r = mk_node(O.ENEMY, 1230, 340, 240)
    enemy_l = mk_node(O.ENEMY, 300, 360, 260)
    heal_l = mk_node(O.HEAL, 350, 320, 220)
    chest_r = mk_node(O.CHEST, 1150, 300, 200)
    device_l = mk_node(O.DEVICE, 250, 380, 280)
    sd = {(heal_r.x, heal_r.y): O.RIGHT, (enemy_r.x, enemy_r.y): O.RIGHT, (enemy_l.x, enemy_l.y): O.LEFT,
          (heal_l.x, heal_l.y): O.LEFT, (chest_r.x, chest_r.y): O.RIGHT, (device_l.x, device_l.y): O.LEFT}
    P = O.DEFAULT_PRIORITY
    zero, left1, right1 = {O.LEFT: 0, O.RIGHT: 0}, {O.LEFT: 1, O.RIGHT: 0}, {O.LEFT: 0, O.RIGHT: 1}

    pick, why = O.choose_for_teams([heal_r, enemy_r, enemy_l], P, sd, zero)
    check("右隊治療、左隊還沒打：點左隊的怪物（讓左隊先打），不是右隊旁邊的怪物",
          pick is enemy_l and "右隊的治療先留著" in why and "左隊" in why, f"{pick.kind} x={pick.x} {why}")
    pick, why = O.choose_for_teams([heal_r, enemy_r, enemy_l], P, sd, left1)
    check("左隊打過了：右隊直接點治療（勝過旁邊的怪物）", pick is heal_r and why == "", f"{pick.kind} {why}")
    pick, _ = O.choose_for_teams([heal_r, enemy_r, enemy_l], P, sd, {O.LEFT: 1, O.RIGHT: 9})
    check("只看「另一隊」有沒有打過（右隊自己打幾場都不影響）", pick is heal_r)
    pick, _ = O.choose_for_teams([heal_r, enemy_r, enemy_l], P, sd, right1)
    check("反過來：只有右隊打過、左隊沒有 → 右隊的治療還是要等", pick is enemy_l)
    pick, why = O.choose_for_teams([heal_l, enemy_l, enemy_r], P, sd, zero)
    check("左隊治療、右隊還沒打：讓右隊先打", pick is enemy_r and "左隊的治療先留著" in why, f"{pick.x} {why}")
    pick, _ = O.choose_for_teams([heal_l, enemy_l, enemy_r], P, sd, right1)
    check("左隊治療、右隊打過了：點治療", pick is heal_l)

    pick, why = O.choose_for_teams([heal_r, enemy_r], P, sd, zero)
    check("另一隊沒有怪物可打：治療照點（不能為了等而放掉）", pick is heal_r and "沒有怪物可先打" in why, f"{pick.kind} {why}")
    pick, _ = O.choose_for_teams([heal_r, enemy_r, device_l], P, sd, zero)
    check("另一隊只有裝置（不會打架）：治療照點、也不去按裝置", pick is heal_r)
    pick, _ = O.choose_for_teams([heal_r, enemy_r, enemy_l], P, {}, zero)
    check("分不出隊：治療照點（不亂降級）", pick is heal_r)
    pick, _ = O.choose_for_teams([heal_r, chest_r, enemy_l], P, sd, zero)
    check("有寶箱：不管治療輪不輪到都先寶箱（使用者：寶箱盡可能都選）", pick is chest_r)
    pick, _ = O.choose_for_teams([enemy_r, enemy_l], P, sd, zero)
    check("沒有治療：照一般優先序（最上面的怪物）", pick is enemy_r)
    two_heals, sd2 = [heal_r, heal_l, enemy_r, enemy_l], sd
    pick, why = O.choose_for_teams(two_heals, P, sd2, zero)
    check("兩隊各有一個治療、都沒打過：讓另一隊先打怪（一個治療補兩隊，不會兩邊互等）",
          pick.kind == O.ENEMY and "先留著" in why, f"{pick.kind} x={pick.x} {why}")

    # 有把握的隊伍判斷（擋治療只用這個）：英雄與左右半邊不一致就不確定，不確定時治療照點
    both = {O.LEFT: O.Hero(O.LEFT, 400, 600, 0.9), O.RIGHT: O.Hero(O.RIGHT, 1000, 300, 0.9)}
    mid = lambda x: O.Node(x, 500, O.Pin(x, 300, 395, 20, 48, 700), O.HEAL, 0.5)
    check("有把握：英雄與左右半邊一致（x=300 左、x=1100 右）",
          O.side_of_sure(mid(300), both, 1475) == O.LEFT and O.side_of_sure(mid(1100), both, 1475) == O.RIGHT)
    check("沒偵測到英雄：只看左右半邊，一致（有把握）", O.side_of_sure(mid(300), {}, 1475) == O.LEFT)
    check("畫面正中央偏左（x=700 在左半），但離右隊英雄比較近 → 不一致＝不確定",
          O.side_of(mid(700), {O.LEFT: O.Hero(O.LEFT, 100, 600, 0.9), O.RIGHT: O.Hero(O.RIGHT, 900, 300, 0.9)}, 1475) == O.RIGHT
          and O.side_of_sure(mid(700), {O.LEFT: O.Hero(O.LEFT, 100, 600, 0.9), O.RIGHT: O.Hero(O.RIGHT, 900, 300, 0.9)}, 1475) is None)
    only_l = {O.LEFT: O.Hero(O.LEFT, 400, 600, 0.9)}
    check("只偵測到左隊英雄、右半邊的圖釘離它不遠：英雄說左、左右半邊說右 → 不確定",
          O.side_of(mid(1000), only_l, 1475) == O.LEFT and O.side_of_sure(mid(1000), only_l, 1475) is None)
    unsure_heal = mk_node(O.HEAL, 720, 320, 220)
    unsure_enemy = mk_node(O.ENEMY, 800, 340, 240)
    pick, why = O.choose_for_teams([unsure_heal, unsure_enemy], P, {(unsure_heal.x, unsure_heal.y): None,
                                                                    (unsure_enemy.x, unsure_enemy.y): O.RIGHT}, zero)
    check("同一個岔路的治療與怪物只要有一邊不確定：治療照點，不會誤點旁邊的怪物", pick is unsure_heal and why == "", f"{pick.kind} {why}")

    # 規劃器：有治療可選時才跑英雄偵測，結果暫存；戰鬥計數的生命週期
    pins = [(1100, 220, O.HEAL), (1230, 240, O.ENEMY), (300, 260, O.ENEMY)]
    img = synth_map(pins)
    fh = FakeHeroes(left=(300, 500), right=(1000, 300))
    pl = O.MapPlanner(synth_classifier(), hero_finder=fh)
    check("一開始兩隊都是 0 場", pl.fights == {O.LEFT: 0, O.RIGHT: 0})
    observe_twice(pl, img, 0.0)
    plan = pl.decide(pl.observe(img, 0.3), img.shape, 0.3)
    check("右隊治療＋右隊怪物＋左隊怪物、左隊沒打過：先點左隊怪物",
          plan.kind == "click" and plan.node.kind == O.ENEMY and plan.node.x == 300 and "右隊的治療先留著" in plan.reason,
          f"{plan.kind} {plan.node and (plan.node.kind, plan.node.x)} {plan.reason}")
    check("有判斷過的隊伍記在 planner.sides（給預覽標示用）",
          sorted(v for v in pl.sides.values()) == [O.LEFT, O.RIGHT, O.RIGHT], f"{pl.sides}")
    check("英雄偵測有暫存：這幾輪只跑一次", fh.calls == 1, f"calls={fh.calls}")
    side = pl.side_for(plan.node, img)
    check("點節點時用英雄位置判斷左右隊（x=300 離左隊英雄近）；沿用暫存不重跑", side == O.LEFT and fh.calls == 1, f"{side} calls={fh.calls}")
    pl.commit(plan, img.shape, 1.0, side)
    check("勝利畫面出現：左隊記一場", pl.note_victory() == O.LEFT and pl.fights == {O.LEFT: 1, O.RIGHT: 0})
    check("同一個勝利畫面連續多輪只記一次", pl.note_victory() is None and pl.fights[O.LEFT] == 1)
    pl.left_overview()
    check("離開總覽（進彈窗/戰鬥）不會洗掉戰鬥計數", pl.fights == {O.LEFT: 1, O.RIGHT: 0})
    observe_twice(pl, img, 3.0)
    plan = pl.decide(pl.observe(img, 3.3), img.shape, 3.3)
    check("左隊打過了：右隊點治療（不是旁邊的怪物）", plan.kind == "click" and plan.node.kind == O.HEAL and "先留著" not in plan.reason,
          f"{plan.reason}")
    check("英雄偵測暫存過期（>1.2 秒）就重跑", fh.calls == 2, f"calls={fh.calls}")
    pl.commit(plan, img.shape, 4.0, O.RIGHT)
    check("點了治療：兩隊計數歸零、要重新累積", pl.fights == {O.LEFT: 0, O.RIGHT: 0})
    check("點了治療之後勝利畫面不會誤記（沒有待記的隊）", pl.note_victory() is None)
    other = O.Node(1230, 340, O.Pin(1230, 192, 240, 20, 48, 700), O.ENEMY, 0.1)
    pl.commit(O.MapPlan("click", "x", 1, 1, node=other), img.shape, 5.0, None)
    check("分不出左右隊（side=None）時不記場數", pl.note_victory() is None and pl.fights == {O.LEFT: 0, O.RIGHT: 0})
    pl.reset()
    check("重新開始自動（reset）計數清空", pl.fights == {O.LEFT: 0, O.RIGHT: 0})

    # 歷史未知（輔助器在一輪遊戲中途才開）：兩隊計數 0 也不能害治療被放掉——最多讓另一隊先打一場
    pl = O.MapPlanner(synth_classifier(), hero_finder=FakeHeroes(left=(300, 500), right=(1000, 300)))
    only = synth_map([(1100, 220, O.HEAL), (1230, 240, O.ENEMY)])
    observe_twice(pl, only, 0.0)
    plan = pl.decide(pl.observe(only, 0.3), only.shape, 0.3)
    check("中途開自動、另一隊沒有怪物可先打：直接點治療", plan.kind == "click" and plan.node.kind == O.HEAL, f"{plan.reason}")

    # 拖曳：靜止動作量與鏡頭移動
    pl = new_planner()
    still = synth_map([(400, 200, O.CHEST)])
    pl.observe(still, 0.0)
    check("整張畫面沒變＝不算在動", pl.observe(still, 0.15).stable is True)
    panned = np.roll(still, 250, axis=0)                 # 整個畫面往下跑 250px（像拖曳）
    panned[:60, :520] = (90, 120, 200)
    noisy = still.copy()
    noisy[400:520, 700:900] = (200, 200, 200)             # 一小塊動畫（不到 3%）
    check("一小塊動畫（角色待機）不算鏡頭在動", new_planner().observe(still, 0.0) and pl.observe(noisy, 0.3).stable is True)
    moving = still.copy()
    moving[100:800, 100:1300] = (120, 130, 150)           # 大半個畫面都變了
    check("大半個畫面都變了＝鏡頭在動", pl.observe(moving, 0.45).stable is False)


def test_rules():
    print("== 6. 規則與設定")
    book = E.RuleBook(os.path.join(SANDBOX, "rules_t.json"))
    book.set_map("總覽")
    book.set_click("VS", 0.66, 0.85)
    book.set_wait("戰鬥")
    book.save()
    back = E.RuleBook.load(book.path)
    check("map 規則存檔再讀回來", back.get("總覽") == {"action": "map"})
    check("其他規則不受影響", back.get("VS")["action"] == "click" and back.get("戰鬥") == {"action": "wait"})
    check("describe 說得出是自動選節點", "自動選節點" in back.describe("總覽"))
    check("click_count 不把 map 算進去", back.click_count == 1)
    cfg = E.Config()
    cfg.node_priority = ["heal", "chest"]
    cfg.scan_moves = [[0.5, 0.4, 0.5, 0.6]]
    cfg.save()
    got = E.Config.load()
    check("總覽的設定欄位會存檔", got.node_priority == ["heal", "chest"] and got.scan_moves == [[0.5, 0.4, 0.5, 0.6]]
          and got.map_interval == 2.5 and got.scan_max_drags == O.DEFAULT_MAX_SCAN_DRAGS)
    os.remove(E.CONFIG_PATH)


class FakeGrabber:
    backend = "fake"

    def __init__(self):
        self.image = synth_map()

    def grab(self, region):
        return self.image.copy()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


REGION = {"left": 100, "top": 50, "width": W, "height": H}


def synth_victory():
    """勝利結算的假畫面：跟地圖、彈窗都不像（整張畫面一大塊綠、一小塊紅）。"""
    img = np.full((H, W, 3), (20, 60, 30), dtype=np.uint8)
    img[200:620, 300:1180] = (40, 170, 70)
    img[560:640, 800:1000] = (220, 60, 60)
    return img


def make_engine(**cfg_over):
    for name in ("config.json", "rules.json"):
        p = os.path.join(SANDBOX, name)
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(E.TEMPLATES_DIR, ignore_errors=True)
    cfg = E.Config()
    cfg.region = dict(REGION)
    cfg.action_interval = 0.0
    cfg.stable_reads = 2
    cfg.route_enabled = False      # 這裡測原本的選節點規則；固定路線另有 test_route.py
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    grabber = FakeGrabber()
    calls = []
    eng = E.Engine(cfg, grabber=grabber,
                   clicker=lambda x, y, hold=0.06: calls.append(("click", x, y)),
                   swiper=lambda x0, y0, x1, y1, **kw: calls.append(("drag", x0, y0, x1, y1)),
                   busy=lambda: False)
    eng.planner.classifier = synth_classifier()
    # 總覽靠標題列（區域樣板、後備）；彈窗整張比對、點「前往」
    eng.db.add("總覽", synth_map(), box=(0.0, 0.0, 0.36, 0.085))
    eng.db.set_fallback("總覽")
    eng.db.set_label_tolerance("總覽", 12.0)
    eng.db.add("前往彈窗", synth_map(popup=True))
    eng.rules.set_map("總覽")
    eng.rules.set_click("前往彈窗", 0.5, 0.63)
    return eng, grabber, calls


def run_steps(eng, clock, n=3, dt=0.2):
    st = None
    for _ in range(n):
        clock.t += dt
        st = eng.step()
        if st.kind in ("stopped", "error"):
            return st
    return st


def test_engine():
    print("== 7. 引擎整合（假擷取／假點擊）")
    real_panic, real_mouse, real_time = C.panic_pressed, C.mouse_button_down, E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False
    try:
        pins = [(400, 200, O.ENEMY), (900, 220, O.CHEST)]
        ty = 220 + round(105 * H / 818)

        # 關著自動：只顯示，不動
        eng, grab, calls = make_engine()
        grab.image = synth_map(pins)
        st = run_steps(eng, clock, 4)
        check("關著自動：辨識成總覽、不點也不拖", st.kind == "ok" and st.label == "總覽" and calls == [], f"{st.kind} {st.label} {calls}")
        check("預覽用的節點標記有給（含下一個目標）",
              st.overlay is not None and len(st.overlay) == 2 and sum(1 for o in st.overlay if o[3]) == 1, f"{st.overlay}")
        check("目標標記是寶箱", [o[2] for o in st.overlay if o[3]] == ["寶箱"], f"{st.overlay}")
        check("訊息說明會怎麼做", "開自動會" in st.message and "寶箱" in st.message, st.message)

        # 開自動：點寶箱，座標要加上範圍左上角
        eng.start_autoplay()
        st = run_steps(eng, clock, 4)
        check("開自動：點了寶箱節點", calls[:1] == [("click", REGION["left"] + 900, REGION["top"] + ty)], f"{calls}")
        check("記進動作次數與最近動作", eng.actions_done == 1, f"{eng.actions_done}")
        n_calls = len(calls)
        clock.t += 0.5
        run_steps(eng, clock, 4, dt=0.1)
        check("動作間隔內不會連點", len(calls) == n_calls, f"{calls}")

        # 出現彈窗（走一般規則點「前往」）→ 重置總覽計次
        grab.image = synth_map(popup=True)
        clock.t += 3
        st = run_steps(eng, clock, 4)
        check("彈窗出現：辨識成前往彈窗並照一般規則點",
              st.label == "前往彈窗" and calls[-1][0] == "click" and calls[-1][1] == REGION["left"] + round(0.5 * (W - 1)),
              f"{st.label} {calls[-1]}")
        check("離開總覽後規劃器的點擊記錄清空", eng.planner._attempts == [])

        # 使用者按著滑鼠：不點
        eng, grab, calls = make_engine()
        eng._busy = lambda: True
        grab.image = synth_map(pins)
        eng.start_autoplay()
        run_steps(eng, clock, 5)
        check("使用者按著滑鼠時不點", calls == [])

        # 點位被別的視窗蓋住：停手
        eng, grab, calls = make_engine()
        eng.guard = lambda x, y: "別的程式的視窗「Chrome」"
        grab.image = synth_map(pins)
        eng.start_autoplay()
        st = run_steps(eng, clock, 5)
        check("點位被蓋住：停手並說原因", st.kind == "stopped" and "Chrome" in st.message and calls == [], f"{st.kind} {st.message}")
        check("自動操控被關掉", eng.autoplay is False)

        # 點了沒反應：同一節點點 2 次後不再點它，只剩它時就等
        eng, grab, calls = make_engine(map_interval=1.0)
        grab.image = synth_map([(900, 220, O.CHEST)])
        eng.start_autoplay()
        for _ in range(40):
            run_steps(eng, clock, 1, dt=0.6)
        clicks = [c for c in calls if c[0] == "click"]
        check("點了沒反應的節點只點 2 次就放棄", len(clicks) == 2, f"{len(clicks)} 次")

        # 沒圖釘：拖地圖；拖完座標加範圍左上角；拖 N 次後停手
        eng, grab, calls = make_engine(map_interval=1.0, scan_max_drags=2)
        grab.image = synth_map()
        eng.start_autoplay()
        st = None
        for _ in range(30):
            st = run_steps(eng, clock, 1, dt=0.6)
            if st.kind == "stopped":
                break
        drags = [c for c in calls if c[0] == "drag"]
        check("沒圖釘：拖了 2 次", len(drags) == 2, f"{calls}")
        check("拖曳座標有加上範圍左上角",
              REGION["left"] <= drags[0][1] < REGION["left"] + W and REGION["top"] <= drags[0][2] < REGION["top"] + H, f"{drags[0]}")
        check("拖夠次數還是沒圖釘：停手並說明", st.kind == "stopped" and "拖" in st.message, f"{st.kind} {st.message}")

        # 彈窗疊著（沒被辨識成任何狀態、標題列還在）：不點
        eng, grab, calls = make_engine()
        eng.db.remove_label("前往彈窗")
        grab.image = synth_map(pins, popup=True)
        eng.start_autoplay()
        run_steps(eng, clock, 6)
        check("沒教過的亮色彈窗疊在總覽上：不亂點", calls == [], f"{calls}")

        # 認不得種類的節點（分類器是空的）：不點
        eng, grab, calls = make_engine()
        eng.planner.classifier = O.NodeClassifier()
        grab.image = synth_map(pins)
        eng.start_autoplay()
        st = run_steps(eng, clock, 6)
        check("分類器沒有樣板時不亂點", calls == [], f"{calls}")

        # 左右隊：點節點時判斷隊伍，勝利畫面出現時那隊記一場，面板訊息顯示場數
        eng, grab, calls = make_engine()
        eng.db.add("VICTORY", synth_victory())
        eng.rules.set_click("VICTORY", 0.63, 0.84)
        grab.image = synth_map([(1100, 220, O.CHEST)])        # 右半邊（x=1100 > 738）：沒有英雄樣板時退回左右半邊
        eng.start_autoplay()
        notes = []
        for _ in range(4):
            clock.t += 0.2
            st = eng.step()
            if st.acted:
                notes.append(st.acted)
        check("點節點：記下這一下是右隊", eng.planner._pending == O.RIGHT and any(c[0] == "click" for c in calls),
              f"{eng.planner._pending} {calls}")
        check("面板動作記錄註明是哪一隊", any("右隊" in n and "寶箱" in n for n in notes), f"{notes}")
        grab.image = synth_victory()
        clock.t += 3
        notes = []
        for _ in range(4):
            clock.t += 0.2
            st = eng.step()
            if st.acted:
                notes.append(st.acted)
        check("勝利畫面：右隊記一場、左隊沒有", eng.planner.fights == {O.LEFT: 0, O.RIGHT: 1}, f"{eng.planner.fights}")
        check("勝利那一步的動作記錄有寫「戰鬥勝利：右隊記一場」", any("戰鬥勝利" in n and "右隊記一場" in n for n in notes), f"{notes}")
        grab.image = synth_map([(1100, 220, O.CHEST)])
        clock.t += 3
        st = run_steps(eng, clock, 3)
        check("回到總覽：訊息顯示各隊場數", "右隊 1 場" in st.message, st.message)
        check("回到總覽：計數沒被洗掉", eng.planner.fights[O.RIGHT] == 1)
        eng.stop_autoplay()
        eng.start_autoplay()
        check("重新開始自動：計數清空", eng.planner.fights == {O.LEFT: 0, O.RIGHT: 0})

        # 9/24 使用者回報：右隊的治療被旁邊的怪物搶走。沒有英雄樣板時用畫面左右半邊分隊
        eng, grab, calls = make_engine()
        eng.db.add("VICTORY", synth_victory())
        eng.rules.set_click("VICTORY", 0.63, 0.84)
        grab.image = synth_map([(1100, 220, O.HEAL), (1230, 240, O.ENEMY), (300, 260, O.ENEMY)])
        st = run_steps(eng, clock, 4)
        tags = sorted(o[2] for o in st.overlay)
        check("預覽標出每個節點屬於哪隊", tags == sorted(["怪物·右", "怪物·左", "治療·右"]), f"{tags}")
        check("預覽說明：右隊的治療先留著、先讓左隊打", "先留著" in st.message and "左隊" in st.message, st.message)
        eng.start_autoplay()
        st = run_steps(eng, clock, 4)
        clicks = [c for c in calls if c[0] == "click"]
        check("開自動：第一下點左隊的怪物（不是右隊旁邊的怪物）", clicks and clicks[0][1] == REGION["left"] + 300, f"{clicks}")
        grab.image = synth_victory()
        clock.t += 3
        run_steps(eng, clock, 4)
        check("左隊打贏：左隊記一場", eng.planner.fights == {O.LEFT: 1, O.RIGHT: 0}, f"{eng.planner.fights}")
        grab.image = synth_map([(1100, 220, O.HEAL), (1230, 240, O.ENEMY), (300, 260, O.ENEMY)])
        clock.t += 3
        n0 = len(calls)
        notes = []
        for _ in range(4):
            clock.t += 0.2
            s2 = eng.step()
            if s2.acted:
                notes.append(s2.acted)
        clicks = [c for c in calls[n0:] if c[0] == "click"]
        check("左隊打過之後：右隊點治療", clicks and clicks[0][1] == REGION["left"] + 1100, f"{clicks}")
        check("動作記錄寫得出當時的判斷（點治療、右隊、看到哪些節點、場數）",
              any("治療" in n and "右隊" in n and "看到 3 個節點" in n and "自上次治療" in n for n in notes), f"{notes}")
        # 點了治療：兩隊計數歸零
        check("點了治療：計數歸零", eng.planner.fights == {O.LEFT: 0, O.RIGHT: 0}, f"{eng.planner.fights}")

        # teach 的 overview 旗標
        eng, grab, calls = make_engine()
        eng.teach("總覽2", synth_map(), None, overview=True)
        check("teach(overview=True) 設成 map 規則", eng.rules.get("總覽2") == {"action": "map"})
        eng.teach("等一下", synth_map(popup=True), None)
        check("teach 沒給點位＝等待（原行為不變）", eng.rules.get("等一下") == {"action": "wait"})
    finally:
        E.time = real_time
        C.panic_pressed, C.mouse_button_down = real_panic, real_mouse


def test_real_session():
    print("== 8. 真實錄製回歸（sessions/20260920_003739）")
    S = os.path.join(HERE, "sessions", "20260920_003739")
    if not os.path.isdir(S):
        print("  （沒有錄製資料，略過）")
        return
    import json
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import build_overview_assets as B
    labels, clicks = B.load_session(S)
    samples, missed = B.collect(S)
    clf = O.NodeClassifier()
    for _, kind, _, patch in samples:
        clf.add(kind, patch)
    ok, n, own = clf.leave_one_out()
    check("節點分類留一驗證 ≥ 90%", ok / n >= 0.90, f"{ok}/{n}")
    check("節點樣本 ≥ 35 個", n >= 35, f"{n}")

    # 每個「點節點進彈窗」的總覽幀：規劃器該點到你當時點的那個節點附近（同一個圖釘正下方）
    hit = clicked = seen = popup_clicks = 0
    total_pop = 0
    for i in sorted(clicks):
        lab = labels.get(i)
        img = B.frame(S, i)
        if lab == "總覽":
            pl = O.MapPlanner(clf)
            pl.observe(img)
            v = pl.observe(img)
            plan = pl.decide(v, img.shape, 0.0)
            if labels.get(i + 1) not in (None, "總覽"):      # 你這次點的是節點
                seen += 1
                if plan.kind == "click":
                    clicked += 1
                    px, py = clicks[i]["region_rel_xy"]
                    if abs(plan.x - px) <= 60 and abs(plan.y - py) <= 60:
                        hit += 1
        elif lab in ("前往彈窗", "守衛者彈窗"):
            total_pop += 1
            pl = O.MapPlanner(clf)
            pl.observe(img)
            plan = pl.decide(pl.observe(img), img.shape, 0.0)
            popup_clicks += plan.kind == "click"
    check("亮色彈窗畫面（46 張）一律不點", popup_clicks == 0, f"{popup_clicks}/{total_pop} 被點")
    check("你實際點節點的每一次（進彈窗的那些）裡，多數規劃器有得點", clicked / max(seen, 1) >= 0.7, f"{clicked}/{seen}")
    print(f"     （點到的節點跟你當時點的同一個：{hit}/{clicked}；優先序會挑不同的節點是預期的。"
          f"注意：分類器用這批畫面訓練，所以這一節偏樂觀，誠實的分類準確度看上面的留一驗證）")


def test_reframed():
    print("== 9. 框選範圍有小誤差也要認得總覽（9/24 事故的回歸）")
    tdir = os.path.join(HERE, "templates")
    if not os.path.exists(os.path.join(tdir, "index.json")):
        print("  （沒有樣板庫，略過）")
        return
    db = V.StateDB.load(tdir, 18.0, 2.0)
    if "總覽" not in db.labels:
        print("  （樣板庫沒有「總覽」，略過）")
        return
    img = np.asarray(Image.open(os.path.join(HERE, "reference_notes", "13_overview_reframed_0924.png")).convert("RGB"))
    m = db.match(img)
    check("使用者 9/24 重新框選的範圍（上緣高 2px）：認成總覽", m.label in ("總覽", "起始畫面"), f"{m.label} d={m.distance:.2f}")
    db0 = V.StateDB.load(tdir, 18.0, 2.0)
    db0.box_shifts = (0,)
    m0 = db0.match(img)
    check("（對照）沒有位移容忍就會掛，這就是當初的故障", m0.label is None, f"{m0.label} d={m0.distance:.2f}")
    # 位移記憶：認得之後下一輪還是認得，換個位置的畫面（重新框選）也會自己找回來
    check("連續多輪都穩定認得", all(db.match(img).label == m.label for _ in range(15)))
    big = np.pad(img, ((8, 8), (8, 8), (0, 0)), mode="edge")
    for dy in (-4, 0, 5):
        moved = big[8 - dy:8 - dy + img.shape[0], 8:8 + img.shape[1]]
        db2 = V.StateDB.load(tdir, 18.0, 2.0)
        mm = db2.match(moved)
        check(f"再位移 {dy:+d}px 也認得", mm.label in ("總覽", "起始畫面"), f"{mm.label} d={mm.distance:.2f}")


def test_offset_session():
    print("== 10. 整輪錄製畫面 × 框選位移：辨識率不能掉、也不能誤認")
    S = os.path.join(HERE, "sessions", "20260920_003739")
    if not os.path.isdir(S):
        print("  （沒有錄製資料，略過）")
        return
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import seed_templates as ST
    labels = ST.load_labels(S)
    db = V.StateDB(18.0, 2.0)
    ST.add_samples(db, S, labels)
    ST.apply_spec(db)
    frames = [(i, labels[i]) for i in sorted(labels) if i % 4 == 0]
    imgs = {i: ST.frame(S, i) for i, _ in frames}

    def run(shifts, dx, dy):
        db.box_shifts = shifts
        db._shift_hint.clear()
        db._box_calls.clear()
        right = wrong = unknown = 0
        for i, truth in frames:
            big = np.pad(imgs[i], ((8, 8), (8, 8), (0, 0)), mode="edge")
            moved = big[8 - dy:8 - dy + imgs[i].shape[0], 8 - dx:8 - dx + imgs[i].shape[1]]
            m = db.match(moved)
            if m.label == truth:
                right += 1
            elif m.label is None:
                unknown += 1
            else:
                wrong += 1
        return right, wrong, unknown

    n = len(frames)
    r0 = run(V.BOX_SHIFTS, 0, 0)
    check("沒位移：幾乎全認得", r0[0] >= n - 2 and r0[1] == 0, f"對 {r0[0]}/{n} 錯認 {r0[1]} 認不得 {r0[2]}")
    for dx, dy in ((0, 2), (0, -3), (2, 3), (-4, 0), (0, 5)):
        rt = run(V.BOX_SHIFTS, dx, dy)
        rn = run((0,), dx, dy)
        check(f"位移 ({dx:+d},{dy:+d})px：有位移容忍時辨識率不掉、零誤認", rt[0] >= n - 3 and rt[1] == 0,
              f"有容忍 對 {rt[0]}/{n} 錯認 {rt[1]}；無容忍 對 {rn[0]}/{n} 錯認 {rn[1]}")


# ---------------------------------------------------------------- 9/25：使用者回報的四件事

REF_DIR = os.path.join(HERE, "reference_notes")
SESSION = os.path.join(HERE, "sessions", "20260920_003739")


def ref_img(name, crop=None):
    im = np.asarray(Image.open(os.path.join(REF_DIR, name)).convert("RGB"))
    if crop is not None:
        l, t, r, b = crop
        im = im[t:b, l:r]
    return im


def real_assets():
    """真實的樣板庫／規則／節點分類器／英雄樣板（在專案資料夾，不是暫存夾）。缺任何一樣就回 None，該節略過。"""
    need = [os.path.join(HERE, "templates", "index.json"), os.path.join(HERE, "rules.json"),
            os.path.join(HERE, "overview_assets", "nodes", "index.json"),
            os.path.join(REF_DIR, "17_prompt_dialog_0925.png"), os.path.join(REF_DIR, "18_right_team_no_pins_0925.png"),
            os.path.join(REF_DIR, "19_reward_wheel_0925.png")]
    if not all(os.path.exists(p) for p in need):
        print("  （沒有真實樣板庫或 9/25 的參考截圖，略過）")
        return None
    db = V.StateDB.load(os.path.join(HERE, "templates"), 18.0, 2.0)
    if "提示框" not in db.labels or "獎勵輪盤" not in db.labels:
        print("  （樣板庫還沒有「提示框」「獎勵輪盤」，略過——先跑 tools/seed_templates.py）")
        return None
    rules = E.RuleBook.load(os.path.join(HERE, "rules.json"))
    clf = O.NodeClassifier.load(os.path.join(HERE, "overview_assets", "nodes"))
    heroes = O.HeroFinder.load(os.path.join(HERE, "overview_assets", "heroes"))
    return db, rules, clf, heroes


def fresh_db():
    return V.StateDB.load(os.path.join(HERE, "templates"), 18.0, 2.0)


def reframe(full, dx, dy, scale=1.0):
    """把使用者的截圖換個框法：位移 (dx, dy) 像素、縮放 scale 倍（模擬重新框選時範圍大小／位置不同）。"""
    big = np.pad(full, ((10, 10), (10, 10), (0, 0)), mode="edge")
    moved = big[10 - dy:10 - dy + full.shape[0], 10 - dx:10 - dx + full.shape[1]]
    if scale != 1.0:
        moved = np.asarray(Image.fromarray(moved).resize((int(moved.shape[1] * scale), int(moved.shape[0] * scale)),
                                                         Image.BILINEAR))
    return moved


def test_prompt_and_wheel():
    print("== 11. 提示框要點「確定」、獎勵輪盤點正中間（9/25 使用者貼的畫面）")
    real = real_assets()
    if real is None:
        return
    db, rules, _, _ = real
    prompt = ref_img("17_prompt_dialog_0925.png", (0, 35, 1399, 821))     # 去掉 BlueStacks 工具列＝框選範圍
    wheel = ref_img("19_reward_wheel_0925.png")
    overview = ref_img("18_right_team_no_pins_0925.png")

    for dx, dy, sc in ((0, 0, 1.0), (2, -2, 1.0), (-3, 3, 1.0), (4, 4, 1.0), (-5, 1, 1.0), (0, 0, 0.964), (3, -2, 0.964)):
        m = fresh_db().match(reframe(prompt, dx, dy, sc))
        check(f"提示框：框選位移 ({dx:+d},{dy:+d}) 縮放 {sc} 也認得", m.label == "提示框", f"{m.label} d={m.distance:.2f}")
    # 對照：9/25 之前的設定（門檻 2.0、只有 9/20 的樣板、位移格點 3px）——使用者那輪就是這樣整輪沒認出提示框
    if os.path.isdir(SESSION):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import seed_templates as ST
        labels = ST.load_labels(SESSION)
        old = V.StateDB(18.0, 2.0)
        old.box_shifts = (-6, -3, 0, 3, 6)
        ST.add_samples(old, SESSION, {i: l for i, l in labels.items() if l == "提示框"})
        old.set_label_tolerance("提示框", 2.0)
        mo = old.match(prompt)
        check("（對照）舊設定（門檻 2.0、只有 9/20 的樣板）認不得這張＝當初的故障", mo.label is None, f"{mo.label} d={mo.distance:.2f}")

    x, y = rules.get("提示框")["x"], rules.get("提示框")["y"]
    px, py = int(round(x * (prompt.shape[1] - 1))), int(round(y * (prompt.shape[0] - 1)))
    r, g, b = (int(v) for v in prompt[py, px])
    check("提示框的點位落在右邊紅色「確定」鈕上（不是左邊藍色「取消」）", r > 170 and g < 130 and b < 130 and r > b + 60, f"({px},{py}) = {(r, g, b)}")
    rl, gl, bl = (int(v) for v in prompt[py, int(round(0.30 * (prompt.shape[1] - 1)))])
    check("（對照）左邊同高度是藍色取消鈕", bl > rl + 40, f"{(rl, gl, bl)}")

    for dx, dy, sc in ((0, 0, 1.0), (3, -3, 1.0), (0, 0, 0.97), (-2, 2, 1.03)):
        m = fresh_db().match(reframe(wheel, dx, dy, sc))
        check(f"獎勵輪盤：位移 ({dx:+d},{dy:+d}) 縮放 {sc} 也認得", m.label == "獎勵輪盤", f"{m.label} d={m.distance:.2f}")
    check("獎勵輪盤的規則＝點畫面正中間", rules.get("獎勵輪盤") == {"action": "click", "x": 0.5, "y": 0.5}, f"{rules.get('獎勵輪盤')}")
    mo = fresh_db().match(overview)
    check("沒圖釘的右隊總覽仍認成總覽（沒被新樣板搶走）", mo.label == "總覽", f"{mo.label} d={mo.distance:.2f}")
    check("提示框畫面不會被認成別的", fresh_db().match(prompt).label == "提示框")

    if os.path.isdir(SESSION):
        import seed_templates as ST
        labels = ST.load_labels(SESSION)
        d2 = fresh_db()
        wrong = []
        for i in sorted(labels):
            if i % 2:
                continue
            m = d2.match(ST.frame(SESSION, i))
            if m.label in ("提示框", "獎勵輪盤") and labels[i] != m.label:
                wrong.append((i, labels[i], m.label))
        check("錄製的其他畫面（每兩張取一）沒有被誤認成提示框／獎勵輪盤", wrong == [], f"{wrong}")


def test_dim():
    print("== 12. 畫面被暗遮罩壓暗時不能當成「沒圖釘」去拖地圖")
    real = real_assets()
    if real is None:
        return
    _, _, clf, heroes = real
    prompt = ref_img("17_prompt_dialog_0925.png", (0, 35, 1399, 821))
    wheel = ref_img("19_reward_wheel_0925.png")
    overview = ref_img("18_right_team_no_pins_0925.png")
    check("提示框畫面＝被壓暗", O.is_dimmed(prompt), f"{O.header_lum(prompt):.0f}")
    check("獎勵輪盤畫面＝被壓暗", O.is_dimmed(wheel), f"{O.header_lum(wheel):.0f}")
    check("正常的總覽不算被壓暗", not O.is_dimmed(overview), f"{O.header_lum(overview):.0f}")
    for name in ("12_overview_pins_0924.png", "13_overview_reframed_0924.png", "16_overview_both_heroes_0924.png"):
        check(f"參考總覽 {name[:2]} 不算被壓暗", not O.is_dimmed(ref_img(name)))
    if os.path.isdir(SESSION):
        import seed_templates as ST
        labels = ST.load_labels(SESSION)
        bad = [(i, l) for i, l in labels.items()
               if (l in ("總覽", "前往彈窗", "守衛者彈窗")) == O.is_dimmed(ST.frame(SESSION, i))]
        check("錄製：總覽與亮色彈窗都不算被壓暗、VS／勝利／提示框／點空白都算", bad == [], f"{bad[:6]}")
    pl = O.MapPlanner(clf, hero_finder=heroes)
    t, kinds = 0.0, []
    for _ in range(90):
        v = pl.observe(prompt, t)
        plan = pl.decide(v, prompt.shape, t)
        kinds.append(plan.kind)
        if plan.kind == "stop":
            break
        t += 1.0
    check("被壓暗的畫面：只等、不拖地圖、不點", "drag" not in kinds and "click" not in kinds, f"{sorted(set(kinds))}")
    check("等太久（60 秒）停手並說是畫面被壓暗", kinds[-1] == "stop", f"{kinds[-1]}")
    check("被壓暗時看不到節點（不會拿暗畫面的圖釘亂點）", pl.observe(prompt, 99.0).nodes == [])


def test_unpinned_real():
    print("== 13. 沒圖釘也找得到隊伍前面的節點（9/25 使用者貼的右隊畫面＋9/20 錄製）")
    real = real_assets()
    if real is None:
        return
    _, _, clf, heroes = real
    img = ref_img("18_right_team_no_pins_0925.png")
    check("這張畫面確實沒有黃色圖釘", O.find_pins(img) == [])
    plats = O.find_platforms(img)
    check("找到隊伍站的平台（法師腳下）", len(plats) == 1 and abs(plats[0].x - 780) <= 25 and abs(plats[0].y - 485) <= 30, f"{plats}")
    check("顏色認出是右隊（法師背對鏡頭，英雄樣板比對認不出這個姿勢）", plats and plats[0].side == O.RIGHT, f"{plats}")
    check("（對照）英雄樣板比對確實找不到這隻法師", heroes.find(img) == {})
    cands = O.find_candidates(img, plats)
    adj = O.adjacent_nodes(cands, plats, img.shape)
    check("鄰接隊伍的正好是前面兩個戰鬥", len(adj) == 2 and all(n.kind == O.ENEMY for n, _ in adj), f"{[(n.kind, n.x, n.y) for n, _ in adj]}")
    pts = [(n.x, n.y) for n, _ in adj]
    check("位置在兩隻怪的魔法陣中心（粉紅髮劍士與橘髮戰士）",
          any(abs(x - 766) <= 15 and abs(y - 292) <= 15 for x, y in pts) and any(abs(x - 1028) <= 15 and abs(y - 380) <= 15 for x, y in pts),
          f"{pts}")
    check("更遠的節點（上面的雕像、治療符文）找得到但不算鄰接", len(cands) >= 4 and len(adj) == 2, f"候選 {len(cands)}")

    pl = O.MapPlanner(clf, hero_finder=heroes)
    t, plan = 0.0, None
    waits = 0
    for _ in range(12):
        plan = pl.decide(pl.observe(img, t), img.shape, t)
        if plan.kind == "click":
            break
        waits += plan.kind == "wait"
        t += 0.6
    check("沒圖釘：先確認 2 秒不是閃爍，再點隊伍前面的節點（不是拖地圖）", plan.kind == "click" and waits >= 3, f"{plan.kind} {plan.reason} waits={waits}")
    check("點的是節點本人、標明沒圖釘、右隊", plan.node.pinless and plan.node.kind == O.ENEMY and plan.side == O.RIGHT and "沒圖釘" in plan.reason,
          f"{plan}")
    check("先點比較上面的那隻（跟有圖釘時的規則一樣）", abs(plan.x - 766) <= 15 and abs(plan.y - 292) <= 15, f"{(plan.x, plan.y)}")
    check("預覽標記：隊伍位置與兩個候選，其中一個是下一個目標",
          sorted(o[2] for o in pl.extra_overlay) == sorted(["隊伍右", "怪物·無圖釘", "怪物·無圖釘"]) and sum(1 for o in pl.extra_overlay if o[3]) == 1,
          f"{pl.extra_overlay}")
    pl.commit(plan, img.shape, t, plan.side)
    check("記下這一下是右隊（勝利畫面出現時記到右隊）", pl._pending == O.RIGHT)
    seq = [(plan.x, plan.y)]
    for k in range(6):
        t += 3.0
        pn = pl.decide(pl.observe(img, t), img.shape, t)
        if pn.kind != "click":
            seq.append(pn.kind)
            break
        seq.append((pn.x, pn.y))
        pl.commit(pn, img.shape, t, pn.side)
    check("點了沒反應：同一節點 2 次後換另一隻，兩隻都試過就改拖地圖",
          len(seq) == 5 and seq[0] == seq[1] and seq[2] == seq[3] and seq[0] != seq[2] and seq[4] == "drag", f"{seq}")

    # 雙邊觸發：點了裝置的那隊在等另一隊，它前面的路不再點；改去找另一隊（先往下看）
    pl = O.MapPlanner(clf, hero_finder=heroes)
    pl.observe(img, 0.0)
    dev = O.Node(766, 292, O.stand_in_pin(766, 292, img.shape[0]), O.DEVICE, 0.0)     # 法師前方的觸發裝置（有圖釘的情況）
    pl.commit(O.MapPlan("click", "x", dev.x, dev.y, node=dev), img.shape, 0.0, O.LEFT)   # 傳進來的隊伍（英雄樣板／畫面半邊）故意給錯
    check("點了觸發裝置：記下這隊在等另一隊（用鄰接平台的顏色判斷，不信傳進來的隊伍）", pl._stuck_side == O.RIGHT, f"{pl._stuck_side}")
    pl.left_overview()
    check("進了彈窗再回來：還記得誰在等", pl._stuck_side == O.RIGHT)
    t, kinds, plan = 0.0, [], None
    for _ in range(12):
        plan = pl.decide(pl.observe(img, t), img.shape, t)
        kinds.append(plan.kind)
        if plan.kind == "drag":
            break
        t += 0.6
    check("右隊在等：不去點右隊前面的怪物，改拖地圖找另一隊", "click" not in kinds and plan.kind == "drag", f"{kinds}")
    check("而且第一步是往左下看去找左隊（9/27：你自己找左隊就是往左下斜拖）、訊息說明右隊在等",
          plan.y1 < plan.y and plan.x1 > plan.x and "往左下看" in plan.reason and "找左隊" in plan.reason
          and "右隊已在等另一隊" in plan.reason, f"{plan.reason}")
    pl.new_stage()
    check("進了下一階段（提示框確定）：不再有誰在等誰", pl._stuck_side is None)
    left = O.Node(1, 1, O.Pin(1, 1, 1, 1, 1, 1), O.DEVICE, 0.0)
    pl2 = O.MapPlanner(clf)
    pl2.commit(O.MapPlan("click", "x", 1, 1, node=left), img.shape, 0.0, O.LEFT)
    check("沒看過畫面、判斷不了是哪一隊時不記（免得誤擋另一隊）", pl2._stuck_side is None)

    # 9/20 錄製裡「沒圖釘也點了」的節點：寶箱在英雄正前方（27、137）、怪物在法師前方（65）、治療符文（91）
    if os.path.isdir(SESSION):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import build_overview_assets as B
        _, clicks = B.load_session(SESSION)
        for i, kind in ((27, O.CHEST), (137, O.CHEST), (65, O.ENEMY), (91, O.HEAL)):
            im = B.frame(SESSION, i)
            pls = O.find_platforms(im)
            ad = O.adjacent_nodes(O.find_candidates(im, pls), pls, im.shape)
            cx, cy = clicks[i]["region_rel_xy"]
            got = [(n.kind, n.x, n.y) for n, _ in ad]
            check(f"錄製第 {i} 次點擊（沒圖釘）：找得到你點的那個{O.KIND_NAMES[kind]}",
                  O.find_pins(im) == [] and any(k == kind and ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= 45 for k, x, y in got),
                  f"你點 {(cx, cy)}，找到 {got}")
        import time as _time
        import eval_unpinned as EV
        t0 = _time.time()
        r = EV.evaluate(SESSION, heroes)
        check(f"整輪錄製：你點的 {r['total']} 個節點找回 ≥ 85%", r["hit"] / max(r["total"], 1) >= 0.85, f"{r['hit']}/{r['total']} 沒找到 {[m[:3] for m in r['misses']]}")
        check("鄰接候選不氾濫（平均每個總覽 ≤ 3 個）", float(np.mean(r["per_frame"])) <= 3.0, f"平均 {np.mean(r['per_frame']):.1f}")
        check("左右隊辨識零錯（對照英雄樣板高信心結果）", r["id_bad"] == 0 and r["id_ok"] >= 20, f"對 {r['id_ok']} 錯 {r['id_bad']} 看不準 {r['id_none']}")
        pf = (_time.time() - t0) / max(len(r["per_frame"]), 1)
        check("每個畫面的偵測時間夠快（含英雄樣板比對＝評估用，單純偵測遠低於這個）", pf < 1.5, f"{pf:.2f} 秒/張")
    t0 = time_now()
    O.find_candidates(img, O.find_platforms(img))
    check("單純偵測（平台＋候選）< 0.5 秒", time_now() - t0 < 0.5, f"{time_now() - t0:.3f} 秒")

    # 怪畫面：不能丟例外、不能卡住（9/25 用純雜訊壓測時，舊的兩兩合併寫法跑了十分鐘沒結束）
    rng = np.random.default_rng(0)
    weird = {"純黑": np.zeros((800, 1400, 3), np.uint8), "純白": np.full((800, 1400, 3), 255, np.uint8),
             "雜訊": rng.integers(0, 256, (800, 1400, 3)).astype(np.uint8),
             "奶油色": np.full((800, 1400, 3), (160, 150, 140), np.uint8), "亮綠": np.full((800, 1400, 3), (40, 200, 90), np.uint8),
             "很小": np.zeros((40, 60, 3), np.uint8)}
    for name, im in weird.items():
        t0 = time_now()
        pl_ = O.find_platforms(im)
        ad_ = O.adjacent_nodes(O.find_candidates(im, pl_), pl_, im.shape)
        check(f"怪畫面（{name}）：不出錯、1 秒內、不會找出東西", time_now() - t0 < 1.0 and pl_ == [] and ad_ == [], f"{time_now() - t0:.2f} 秒 {len(pl_)}/{len(ad_)}")


def time_now():
    import time as _t
    return _t.time()


def test_scan_moves():
    print("== 14. 拖地圖的走法：看不出該找哪隊時先往下、再往上、最後左右；知道要找哪隊就往它那側斜著找（9/27）")
    moves = O.DEFAULT_SCAN_MOVES
    names = [O.look_name(m) for m in moves]
    check("看不出該找哪隊（target=None）的走法跟以前一樣：前 4 步往下看", names[:4] == ["往下看"] * 4, f"{names[:6]}")
    check("scan_legs(None) 攤開＝DEFAULT_SCAN_MOVES", [m for m, n in O.scan_legs(None) for _ in range(n)] == list(moves))
    check("走法涵蓋上下左右四個方向", {"往下看", "往上看", "往左看", "往右看"} <= set(names), f"{sorted(set(names))}")
    legs_l, legs_r = O.scan_legs(O.LEFT), O.scan_legs(O.RIGHT)
    check("找左隊：先往左下斜拖 2 次（9/20 錄製裡你 3 次都這樣找到左隊）",
          legs_l[0] == (O.LOOK_DOWN_LEFT, 2) and O.look_name(O.LOOK_DOWN_LEFT) == "往左下看", f"{legs_l[0]}")
    check("找右隊：先往右下斜拖 2 次", legs_r[0] == (O.LOOK_DOWN_RIGHT, 2) and O.look_name(O.LOOK_DOWN_RIGHT) == "往右下看", f"{legs_r[0]}")
    check("找左隊之後：往下 4、回頭往上 9、萬一認反了再往另一側斜拖 4＋往下 4",
          [(O.look_name(m), n) for m, n in legs_l[1:]] == [("往下看", 4), ("往上看", 9), ("往右下看", 4), ("往下看", 4)], f"{legs_l[1:]}")
    check("左右兩套走法互為鏡像（斜拖方向相反、其他一樣）",
          [n for _, n in legs_l] == [n for _, n in legs_r] and legs_l[1:3] == legs_r[1:3] and legs_l[3][0] == O.LOOK_DOWN_RIGHT and legs_r[3][0] == O.LOOK_DOWN_LEFT)
    full = sum(n for _, n in legs_l)
    check("預設最多拖的次數＝一整套找隊走法＋最多 2 次「拖到中間」", O.DEFAULT_MAX_SCAN_DRAGS == full + O.MAX_CENTER_DRAGS
          and E.Config().scan_max_drags == O.DEFAULT_MAX_SCAN_DRAGS, f"{O.DEFAULT_MAX_SCAN_DRAGS}")
    check("斜拖每步跟你錄製的拖法同量級：手指往右 0.18 寬、往上 0.30 高",
          abs(O.LOOK_DOWN_LEFT[2] - O.LOOK_DOWN_LEFT[0] - 0.18) < 1e-9 and abs(O.LOOK_DOWN_LEFT[3] - O.LOOK_DOWN_LEFT[1] + 0.30) < 1e-9)
    check("look_name：手指往上拖＝往下看、手指往下拖＝往上看",
          O.look_name((0.6, 0.7, 0.6, 0.4)) == "往下看" and O.look_name((0.6, 0.4, 0.6, 0.7)) == "往上看")
    check("look_name：手指往右拖＝往左看、往左拖＝往右看",
          O.look_name((0.3, 0.5, 0.6, 0.5)) == "往左看" and O.look_name((0.6, 0.5, 0.3, 0.5)) == "往右看")
    check("look_name：斜著拖＝往左下／右下／左上／右上看",
          [O.look_name(m) for m in ((0.4, 0.7, 0.6, 0.4), (0.6, 0.7, 0.4, 0.4), (0.4, 0.4, 0.6, 0.7), (0.6, 0.4, 0.4, 0.7))]
          == ["往左下看", "往右下看", "往左上看", "往右上看"])
    every = set(moves) | {m for m, _ in legs_l} | {m for m, _ in legs_r}
    ok = all(0 <= a <= 1 and 0 <= b <= 1 and 0 <= c <= 1 and 0 <= d <= 1 for a, b, c, d in every)
    check("座標都在範圍內（0~1）", ok)
    bad = [m for m in every if O._in_zone(m[0] * W, m[1] * H, W, H, O.KEEP_OUT_ZONES) or O._in_zone(m[2] * W, m[3] * H, W, H, O.KEEP_OUT_ZONES)]
    check("起點終點都不在遊戲按鈕上（右下角圖示、獎勵輪盤、左邊的 f）", bad == [], f"{bad}")

    # 空地圖（會動的鏡頭，但什麼都沒有）：照走法走完才停手
    world = FakeWorld()
    pl = new_planner(max_scan_drags=len(moves))
    seen, plan = run_world(pl, world, 400)
    check("空地圖：整套走完才停手，順序對", seen == names and plan.kind == "stop", f"{seen[:6]} 共 {len(seen)} 次")
    check("停手時說明是連拖 N 次都沒找到能點的節點", "連拖" in plan.reason and "找到能點的節點" in plan.reason, plan.reason)
    # 完全不會動的畫面（模擬器沒收到拖曳）：連 3 次拖了都沒動就停手，不會把整套 20 幾次拖完
    pl = new_planner()
    empty = synth_map()
    t, drags = 0.0, 0
    for _ in range(200):
        plan = pl.decide(pl.observe(empty, t), empty.shape, t)
        pl.commit(plan, empty.shape, t)
        drags += plan.kind == "drag"
        if plan.kind == "stop":
            break
        t += 0.5
    check("拖了畫面都沒動：連 3 次就停手、說明原因", plan.kind == "stop" and drags == O.STILL_MAX and "畫面都沒動" in plan.reason,
          f"drags={drags} {plan.reason}")


def test_heal_alias():
    print("== 15. 藍色泉水一律視作治療（9/25 使用者：一定要選）")
    clf = O.NodeClassifier()
    clf.add(O.REVIVE, O.node_patch(synth_map([(555, 260, O.REVIVE)]), 555, 260 + O.PIN_TO_NODE_DY))
    check("分類器收進「復活」樣板時換成治療", clf.kinds == [O.HEAL], f"{clf.kinds}")
    check("KIND_ALIAS＝復活→治療", O.KIND_ALIAS == {O.REVIVE: O.HEAL})
    real = real_assets()
    if real is not None:
        rc = real[2]
        check("真實節點樣板庫裡已經沒有「復活」種類", O.REVIVE not in set(rc.kinds) and rc.kinds.count(O.HEAL) >= 7, f"{sorted(set(rc.kinds))}")
    # 泉水（復活色）跟怪物一起出現：先點泉水（治療 > 怪物）
    pl = new_planner()
    img = synth_map([(300, 200, O.REVIVE), (520, 230, O.ENEMY)])
    observe_twice(pl, img, 0.0)
    plan = pl.decide(pl.observe(img, 0.3), img.shape, 0.3)
    check("泉水跟怪物同時可選：點泉水", plan.kind == "click" and plan.node.kind == O.HEAL and abs(plan.x - 300) <= 1, f"{plan.reason} {(plan.x, plan.y)}")
    check("面板寫「治療」不是「復活」", "治療" in plan.reason and "復活" not in plan.reason, plan.reason)
    if os.path.isdir(SESSION):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import build_overview_assets as B
        check("build_overview_assets 的節點種類表已把泉水標成治療", all(B.NODE_KINDS[i] == O.HEAL for i in (81, 140)))


def test_engine_real_screens():
    print("== 16. 引擎整合（真實畫面：提示框、獎勵輪盤、沒圖釘的右隊）")
    real = real_assets()
    if real is None:
        return
    db, rules, clf, heroes = real
    prompt = ref_img("17_prompt_dialog_0925.png", (0, 35, 1399, 821))
    wheel = ref_img("19_reward_wheel_0925.png")
    overview = ref_img("18_right_team_no_pins_0925.png")
    real_panic, real_mouse, real_time = C.panic_pressed, C.mouse_button_down, E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False
    try:
        def build():
            eng, grab, calls = make_engine(map_interval=1.0)
            eng.db = fresh_db()
            eng.rules = E.RuleBook.load(os.path.join(HERE, "rules.json"))
            eng.planner = O.MapPlanner(clf, hero_finder=heroes)
            return eng, grab, calls

        eng, grab, calls = build()
        eng.planner._stuck_side = O.LEFT
        grab.image = prompt
        eng.start_autoplay()
        eng.planner._stuck_side = O.LEFT
        st = run_steps(eng, clock, 6)
        want = ("click", REGION["left"] + round(0.607 * (REGION["width"] - 1)), REGION["top"] + round(0.696 * (REGION["height"] - 1)))
        check("提示框：辨識出來並自動點右邊的「確定」", st.label == "提示框" and calls[:1] == [want], f"{st.label} {calls}")
        check("提示框（進下一階段）：規劃器不再記得誰在等誰", eng.planner._stuck_side is None)

        eng, grab, calls = build()
        grab.image = wheel
        eng.start_autoplay()
        st = run_steps(eng, clock, 6)
        want = ("click", REGION["left"] + round(0.5 * (REGION["width"] - 1)), REGION["top"] + round(0.5 * (REGION["height"] - 1)))
        check("獎勵輪盤：辨識出來並點畫面正中間", st.label == "獎勵輪盤" and calls[:1] == [want], f"{st.label} {calls}")

        eng, grab, calls = build()
        grab.image = overview
        st = run_steps(eng, clock, 4)
        check("關著自動：辨識成總覽，預覽標出右隊與兩個候選，說明開自動會點隊伍前面的怪物",
              st.label == "總覽" and calls == [] and st.overlay is not None, f"{st.label} {st.message}")
        for _ in range(8):
            st = run_steps(eng, clock, 1, dt=0.5)
        check("（關著自動也看得到）預覽有隊伍標記", any(o[2] == "隊伍右" for o in st.overlay or []), f"{st.overlay}")
        check("訊息說明開自動會怎麼做", "沒圖釘" in st.message and "怪物" in st.message and "右隊" in st.message, st.message)
        eng.start_autoplay()
        notes = []
        for _ in range(16):
            clock.t += 0.5
            s2 = eng.step()
            if s2.acted:
                notes.append(s2.acted)
        clicks = [c for c in calls if c[0] == "click"]
        check("開自動：點了隊伍前面的怪物（座標加上範圍左上角）",
              clicks and abs(clicks[0][1] - (REGION["left"] + 766)) <= 15 and abs(clicks[0][2] - (REGION["top"] + 292)) <= 15, f"{calls[:3]}")
        check("動作記錄寫得出「沒圖釘、隊伍前面、右隊」", any("沒圖釘" in n and "右隊" in n and "怪物" in n for n in notes), f"{notes[:2]}")
        check("記下這一下是右隊", eng.planner._pending == O.RIGHT)

        eng, grab, calls = build()
        grab.image = overview
        eng.start_autoplay()
        eng.planner._stuck_side = O.RIGHT
        for _ in range(14):
            clock.t += 0.5
            eng.step()
        drags = [c for c in calls if c[0] == "drag"]
        check("右隊在等另一隊：不點怪物、先往下看（手指往上拖）",
              [c for c in calls if c[0] == "click"] == [] and drags and drags[0][2] > drags[0][4], f"{calls[:3]}")
    finally:
        E.time = real_time
        C.panic_pressed, C.mouse_button_down = real_panic, real_mouse


def test_find_other_team():
    print("== 17. 一隊卡住後回頭找另一隊（9/27 使用者：右邊一路往上推進、沒得前進時不會回頭去跑左邊）")
    # 右隊在觸發裝置上等（前面還看得到一個節點，但不能點）；左隊落後在左下方、畫面外，前面有寶箱
    def world_right_stuck(**kw):
        return FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-620, 700)}, nodes={O.RIGHT: O.ENEMY, O.LEFT: O.CHEST},
                         start=O.RIGHT, **kw)

    w = world_right_stuck()
    check("（假地圖）一開始左隊在畫面外、右隊在畫面中間", len(w.scene()[0]) == 1 and w.scene()[0][0].side == O.RIGHT, f"{w.scene()[0]}")
    pl = new_planner()
    pl._stuck_side = O.RIGHT
    seen, plan = run_world(pl, w)
    check("右隊在等：往左下斜拖去找左隊（不點右隊前面的節點）", seen[:2] == ["往左下看", "往左下看"] and plan.kind == "click", f"{seen} {plan.kind} {plan.reason}")
    check("兩下就找到、點的是左隊前面的寶箱", len(seen) == 2 and plan.node.kind == O.CHEST and plan.side == O.LEFT,
          f"{len(seen)} {plan.node and plan.node.kind} {plan.side}")
    check("每一步的說明都寫「找左隊」", all("找左隊" in d.reason for d in w.drags), f"{[d.reason for d in w.drags][:1]}")
    pl.left_overview()
    check("點到節點、畫面離開總覽之後，找隊的進度歸零（下次卡住重新判斷要找誰）", pl.drags == 0 and pl._legs is None)

    # 半路上有個「看起來像隊伍」的東西（其實是怪物站在亮的平台上，錄製第 26 幀就有），它旁邊的節點點了遊戲沒反應：
    # 各試 2 次就放棄、接著照原本的進度找（第 2 次斜拖），不會從頭再來
    w = FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-620, 700), "decoy": (-150, 420)},
                  nodes={O.LEFT: O.CHEST, "decoy": O.CHEST}, start=O.RIGHT)
    pl = new_planner()
    pl._stuck_side = O.RIGHT
    seen, plan = run_world(pl, w, accept=lambda p: p.side == O.LEFT)
    check("點了沒反應的節點不會讓搜尋從頭來：之後的拖曳是「第 2 次」、最後點到左隊",
          plan.kind == "click" and plan.side == O.LEFT and seen == ["往左下看", "往左下看"]
          and "第 2 次" in w.drags[1].reason and w.clicks >= 2, f"{seen} {[d.reason[-12:] for d in w.drags]} 點了 {w.clicks} 下")

    # 對照：以前的固定走法（只往正上正下、最後才左右）在同一張假地圖上
    w = world_right_stuck()
    pl = new_planner(scan_moves=O.DEFAULT_SCAN_MOVES)
    pl._stuck_side = O.RIGHT
    old_seen, old_plan = run_world(pl, w)
    check("（對照）以前的走法：第一步只往正下看、不會往左", old_seen[:1] == ["往下看"], f"{old_seen[:4]}")

    # 鏡像：左隊在等、右隊落後在右下方
    w = FakeWorld(teams={O.LEFT: (0, 0), O.RIGHT: (600, 650)}, nodes={O.RIGHT: O.ENEMY}, start=O.LEFT)
    pl = new_planner()
    pl._stuck_side = O.LEFT
    seen, plan = run_world(pl, w)
    check("左隊在等：往右下斜拖、找到右隊前面的怪物", seen[:1] == ["往右下看"] and plan.kind == "click" and plan.side == O.RIGHT
          and plan.node.kind == O.ENEMY, f"{seen} {plan.kind} {plan.reason}")

    # 沒點過裝置（不知道誰在等）：鏡頭對著的那隊前面沒東西可點＝它走到底了，找另一隊
    w = FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-600, 650)}, nodes={O.LEFT: O.HEAL}, start=O.RIGHT)
    pl = new_planner()
    seen, plan = run_world(pl, w)
    check("不知道誰在等：鏡頭對著右隊、它前面沒東西 → 往左下找左隊", seen[:1] == ["往左下看"] and plan.kind == "click"
          and plan.side == O.LEFT, f"{seen} {plan.kind} {plan.reason}")

    # 找到左隊了，但它貼在畫面左邊、前面的節點被切掉：先把它拖到中間再點
    w = FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-640, 60)}, nodes={O.LEFT: O.CHEST}, start=O.RIGHT)
    x, y = w.at(-640, 60)
    check("（假地圖）左隊在畫面最左邊、看得到平台但節點被切掉", x < 0.08 * W and w.scene()[1] == [] and len(w.scene()[0]) == 2, f"{(x, y)}")
    pl = new_planner()
    pl._stuck_side = O.RIGHT
    seen, plan = run_world(pl, w)
    check("第一步：把左隊拖到畫面中間（手指往右拖），然後點它前面的寶箱",
          seen[:1] == ["拖到中間"] and w.drags[0].x1 > w.drags[0].x and plan.kind == "click" and plan.side == O.LEFT, f"{seen} {plan.reason}")
    check("拖到中間的說明寫得出是哪隊", "左隊在畫面邊邊" in w.drags[0].reason, w.drags[0].reason)

    # 到地圖邊（往下拖不動）：這一段剩下的步數跳過、改往上找（左隊其實在左上方）
    dl = O.LOOK_DOWN_LEFT
    bottom = -round(O.STAND[1] * H) + 2 * (round(dl[1] * (H - 1)) - round(dl[3] * (H - 1)))    # 斜拖兩下剛好到底
    w = FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-560, -900)}, nodes={O.LEFT: O.CHEST}, start=O.RIGHT,
                  bounds=((-4000, 4000), (-4000, bottom)))
    pl = new_planner()
    pl._stuck_side = O.RIGHT
    seen, plan = run_world(pl, w)
    downs = [i for i, d in enumerate(w.drags) if "往下看" in d.reason and "左下" not in d.reason]
    check("到地圖底了：往下看只試 1 次就換方向，往上找到左隊", len(downs) == 1 and "往上看" in seen and plan.kind == "click"
          and plan.side == O.LEFT, f"{seen} {plan.kind}")
    check("換方向時說明是到地圖邊了", any("到地圖邊" in d.reason for d in w.drags), f"{[d.reason for d in w.drags]}")

    # 左隊根本不在（或認不出來）：整套走完就停手，說清楚沒找到哪隊
    w = FakeWorld(teams={O.RIGHT: (0, 0)}, start=O.RIGHT)
    pl = new_planner()
    pl._stuck_side = O.RIGHT
    seen, plan = run_world(pl, w, n=2000)
    full = sum(n for _, n in O.scan_legs(O.LEFT))
    check("找不到：整套走完（不超過上限）才停手，說明沒找到左隊", plan.kind == "stop" and len(seen) == full and "沒找到左隊" in plan.reason,
          f"{len(seen)}/{full} {plan.reason}")

    # 點了裝置：回到總覽後用鏡頭正對的那隊確認是誰在等（點下去那一刻兩隊都入鏡時可能分錯）
    w = FakeWorld(teams={O.RIGHT: (0, 0), O.LEFT: (-600, 650)}, nodes={O.LEFT: O.CHEST}, start=O.RIGHT)
    pl = new_planner()
    pl._scene = lambda img, now: w.scene()
    dev = O.Node(700, 200, O.stand_in_pin(700, 200, H), O.DEVICE, 0.0, True)
    pl.commit(O.MapPlan("click", "x", 700, 200, node=dev, side=O.LEFT), (H, W, 3), 0.0, O.LEFT)    # 故意給錯
    check("點了裝置：先照點下去時的判斷記（左隊），等回到總覽再確認", pl._stuck_side == O.LEFT and pl._stuck_check)
    check("點裝置不用打仗：不會把下一場勝利記到這隊頭上", pl._pending is None)
    pl.left_overview()
    img = w.view()
    pl.decide(pl.observe(img, 5.0), img.shape, 5.0)
    check("回到總覽第一張畫面（還看不出鏡頭停了沒）：先不改", pl._stuck_side == O.LEFT and pl._stuck_check)
    pl.decide(pl.observe(img, 5.2), img.shape, 5.2)
    check("鏡頭停下來：正中間是右隊 → 改成右隊在等", pl._stuck_side == O.RIGHT and not pl._stuck_check, f"{pl._stuck_side}")
    seen, plan = run_world(pl, w, t=5.4)
    check("接著往左下找左隊並點它", seen[:1] == ["往左下看"] and plan.kind == "click" and plan.side == O.LEFT, f"{seen} {plan.reason}")
    pl.new_stage()
    check("進下一階段：誰在等、要不要確認都清掉", pl._stuck_side is None and not pl._stuck_check)


def test_recorded_search():
    print("== 18. 用你 9/20 親手找左隊的三段錄製重播（右隊點完觸發裝置 → 你往左下拖兩次 → 點左隊前面的節點）")
    real = real_assets()
    if real is None or not os.path.isdir(SESSION):
        print("  （沒有錄製檔，略過）")
        return
    _, _, clf, heroes = real
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import build_overview_assets as B
    labels, clicks = B.load_session(SESSION)
    # (右隊點裝置後回到總覽, 你拖第一下之後, 你拖第二下之後＝左隊出現), 你在最後那張點的節點種類
    for a, b, c, kind in ((25, 26, 27, O.CHEST), (89, 90, 91, O.HEAL), (135, 136, 137, O.CHEST)):
        frames = [B.frame(SESSION, i) for i in (a, b, c)]
        dev = a - 2
        check(f"第 {a}~{c} 幀：前面第 {dev} 次點擊確實是觸發裝置、之後沒進下一階段（另一隊還沒點）",
              B.NODE_KINDS.get(dev) == O.DEVICE and labels.get(a - 1) == "前往彈窗" and labels.get(a) == "總覽")
        pl = O.MapPlanner(clf, hero_finder=heroes)
        pl._stuck_side, pl._stuck_check = O.LEFT, True       # 點裝置那一刻故意記錯，看回到總覽能不能自己更正
        k, t, drags, plan, targets, extra = 0, 0.0, [], None, [], []
        for _ in range(200):
            img = frames[k]
            plan = pl.decide(pl.observe(img, t), img.shape, t)
            if plan.kind == "drag":
                drags.append(plan)
                targets.append(pl._target)
            pl.commit(plan, img.shape, t, plan.side)
            if plan.kind == "drag":
                k = min(k + 1, 2)          # 你那一輪每拖一下的結果就是下一張錄製畫面
            if plan.kind == "stop" or (plan.kind == "click" and k == 2):
                break
            if plan.kind == "click":
                extra.append((k, plan.node.kind, plan.x, plan.y))    # 半路上點的（重播裡當作遊戲沒反應：畫面不變）
            t += 0.5
        check(f"第 {a} 幀：回到總覽後更正成「右隊在等」、要找左隊", pl._stuck_side == O.RIGHT and targets[:1] == [O.LEFT],
              f"{pl._stuck_side} {targets[:1]}")
        check(f"第 {a} 幀：跟你一樣往左下斜拖兩次（手指往右上）",
              len(drags) == 2 and all(d.x1 > d.x and d.y1 < d.y and "往左下看" in d.reason for d in drags), f"{[d.reason for d in drags]}")
        check(f"第 {a}~{b} 幀：半路上最多試點 4 下（點了沒反應各試 2 次就放棄、繼續拖）", len(extra) <= 4, f"{extra}")
        cx, cy = clicks[c]["region_rel_xy"]
        ok = plan is not None and plan.kind == "click" and plan.node.kind == kind and ((plan.x - cx) ** 2 + (plan.y - cy) ** 2) ** 0.5 <= 45
        check(f"第 {c} 幀：點了你當時點的那個{O.KIND_NAMES[kind]}（左隊）", ok and plan.side == O.LEFT,
              f"你點 {(cx, cy)}，它 {plan.kind} {(plan.x, plan.y)} {plan.reason}；半路 {extra}")


def test_debug_frames():
    print("== 19. 拖地圖／停手時把畫面存檔（事後查它當時看到什麼）")
    real_panic, real_mouse, real_time = C.panic_pressed, C.mouse_button_down, E.time
    clock = Clock()
    E.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    C.mouse_button_down = lambda: False
    try:
        shutil.rmtree(E.DEBUG_DIR, ignore_errors=True)
        eng, grab, calls = make_engine(map_interval=1.0, debug_frames=3)
        grab.image = synth_map()
        eng.start_autoplay()
        notes = []
        st = None
        for _ in range(200):
            clock.t += 0.5
            st = eng.step()
            if st.acted:
                notes.append(st.acted)
            if st.kind in ("stopped", "error"):
                break
        files = sorted(os.listdir(E.DEBUG_DIR)) if os.path.isdir(E.DEBUG_DIR) else []
        drags = [c for c in calls if c[0] == "drag"]
        check("拖了地圖（假畫面不會動 → 3 次後停手）", len(drags) == O.STILL_MAX and st.kind == "stopped", f"{len(drags)} {st.kind} {st.message}")
        check("每次拖地圖的動作記錄都附上畫面存檔的檔名", len(notes) >= 3 and all("畫面存檔" in n and ".jpg" in n for n in notes[:3]), f"{notes[:1]}")
        check("停手訊息也附檔名", "畫面存檔" in st.message and "_stop.jpg" in st.message, st.message)
        check("只留最新 3 張（設定 debug_frames=3）", len(files) == 3 and files[-1].endswith("_stop.jpg"), f"{files}")
        with Image.open(os.path.join(E.DEBUG_DIR, files[-1])) as im:      # 要關檔，不然 Windows 刪不掉資料夾
            size = im.size
        check("存的是範圍內的畫面（大小一樣）", size == (W, H), f"{size}")
        shutil.rmtree(E.DEBUG_DIR, ignore_errors=True)
        eng, grab, calls = make_engine(map_interval=1.0, debug_frames=0)
        grab.image = synth_map()
        eng.start_autoplay()
        for _ in range(30):
            clock.t += 0.5
            if eng.step().kind == "stopped":
                break
        check("debug_frames=0：不存", not os.path.isdir(E.DEBUG_DIR) or os.listdir(E.DEBUG_DIR) == [])
    finally:
        E.time = real_time
        C.panic_pressed, C.mouse_button_down = real_panic, real_mouse


def main():
    for t in (test_pins_real, test_pins_synthetic, test_classifier, test_choose, test_planner,
              test_heroes_real, test_sides_and_heal, test_rules, test_engine, test_real_session,
              test_reframed, test_offset_session, test_prompt_and_wheel, test_dim, test_unpinned_real,
              test_scan_moves, test_heal_alias, test_engine_real_screens, test_find_other_team,
              test_recorded_search, test_debug_frames):
        t()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print()
    if _failures:
        print(f"失敗 {len(_failures)} 項：", "、".join(_failures))
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
