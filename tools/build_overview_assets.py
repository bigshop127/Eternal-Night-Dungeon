"""從一輪錄製建立「總覽節點分類器」的樣板（overview_assets/nodes/），並把總覽的規則切成「自動選節點」。

    python tools/build_overview_assets.py sessions/20260920_003739             # 建立（先備份 rules.json）
    python tools/build_overview_assets.py sessions/20260920_003739 --dry-run   # 只印驗證數字，不寫檔

做的事：
  1. 每一次「在總覽點節點→出現彈窗」的點擊，找出你點的那個節點上方的黃色圖釘，用「圖釘往下 105px」
     （推論時同一個算法）切出節點小圖，種類用彈窗標題認（NODE_KINDS）。
  2. 留一驗證（每張拿掉自己去分類）＋用「地圖上隨便一塊沒有節點的地方」量拒絕門檻。
  3. 把「總覽」「起始畫面」的規則設成 map（自動選節點）。

之後在別的 session 標到新節點種類（例如命運的契機、復活泉水多幾張），在下面 NODE_KINDS 補一行、
重跑即可；換了視窗大小也可以再錄一輪重跑，樣板跟畫面解析度無關（切圖時等比縮放）。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402
import overview as O  # noqa: E402

# 節點種類：{錄製的第 N 次點擊: 種類}，種類是看點完之後彈窗的標題認的（前往彈窗的四種＋觸發裝置兩種）：
#   秘境寶箱→chest／治療神符→heal／觸發裝置（雙邊/單邊）→device／復活泉水→heal
# （使用者 9/25：「藍色泉水一律視作治癒，一定要選」——原本分成 revive，現在併進 heal；O.KIND_ALIAS 也會擋一層）
# 點完出「守衛者彈窗」的一律是 enemy（不用列在這裡，程式從 labels.json 抓）。
# 這張表是 2026-09-20 那一輪（sessions/20260920_003739）看彈窗標題一張張對出來的。
NODE_KINDS = {
    **{i: O.CHEST for i in (1, 27, 45, 48, 57, 60, 84, 106, 116, 119, 126, 137)},
    **{i: O.HEAL for i in (16, 34, 69, 91, 109, 151)},
    **{i: O.DEVICE for i in (23, 41, 55, 87, 98, 133, 158)},
    **{i: O.HEAL for i in (81, 140)},      # 藍色泉水（原本分成復活）
}
MAP_LABELS = ("總覽", "起始畫面")     # 這兩個畫面都是「地城總覽」，規則都設成自動選節點


def load_session(session: str):
    with open(os.path.join(session, "labels.json"), "r", encoding="utf-8") as f:
        labels = {int(k): v for k, v in json.load(f)["labels"].items()}
    with open(os.path.join(session, "clicks.json"), "r", encoding="utf-8") as f:
        clicks = {c["index"]: c for c in json.load(f)["clicks"]}
    return labels, clicks


def frame(session: str, i: int) -> np.ndarray:
    return np.asarray(Image.open(os.path.join(session, f"before_{i:03d}.png")).convert("RGB"))


def collect(session: str):
    """回傳 [(點擊序號, 種類, 節點點位, 小圖)] 與 找不到圖釘的點擊序號清單。"""
    labels, clicks = load_session(session)
    samples, missed = [], []
    for i in sorted(clicks):
        if labels.get(i) != "總覽":
            continue
        nxt = labels.get(i + 1)
        kind = NODE_KINDS.get(i) or (O.ENEMY if nxt == "守衛者彈窗" else None)
        if kind is None:
            continue
        img = frame(session, i)
        h = img.shape[0]
        s = h / O.REF_H
        px, py = clicks[i]["region_rel_xy"]
        best = None
        for p in O.find_pins(img):
            x, y = O.node_point(p, h)
            if abs(px - x) <= 60 * s and abs(py - y) <= 45 * s:
                d = abs(px - x) + abs(py - y)
                if best is None or d < best[0]:
                    best = (d, x, y)
        if best is None:
            missed.append(i)
            continue
        samples.append((i, kind, (best[1], best[2]), O.node_patch(img, best[1], best[2])))
    return samples, missed


def junk_patches(session: str, per_frame: int = 6, seed: int = 0):
    """地圖上「沒有節點」的隨機小圖，用來量拒絕門檻（認不得種類就該離所有樣板夠遠）。"""
    labels, _ = load_session(session)
    rng = np.random.default_rng(seed)
    out = []
    for i in sorted(labels):
        if labels[i] != "總覽":
            continue
        img = frame(session, i)
        h, w = img.shape[:2]
        pins = O.find_pins(img)
        pts = [O.node_point(p, h) for p in pins]
        for _ in range(per_frame):
            x = int(rng.integers(int(w * 0.08), int(w * 0.75)))
            y = int(rng.integers(int(h * 0.22), int(h * 0.75)))
            if any(abs(x - a) < 70 and abs(y - b) < 70 for a, b in pts):
                continue
            out.append(O.node_patch(img, x, y))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="sessions/<時間> 資料夾（要有 before_NNN.png、labels.json、clicks.json）")
    ap.add_argument("--dry-run", action="store_true", help="只印驗證數字，不寫任何檔案")
    args = ap.parse_args()
    session = os.path.abspath(args.session)

    samples, missed = collect(session)
    print(f"節點樣本 {len(samples)} 個（{dict(collections.Counter(k for _, k, _, _ in samples))}）")
    if missed:
        print(f"找不到圖釘、沒收進來的點擊：{missed}")

    clf = O.NodeClassifier()
    for _, kind, _, patch in samples:
        clf.add(kind, patch)
    ok, n, own = clf.leave_one_out()
    print(f"留一驗證：{ok}/{n} = {ok / n:.0%}")

    # 拒絕門檻＝留一驗證最近距離的 p95 再放寬 30%：正常節點幾乎都過，明顯不像任何樣板的東西被擋。
    # 不用「地圖上隨便切的小圖」來校準——它們有不少其實切在沒圖釘的真節點（寶箱、英雄）上，
    # 分布本來就會跟正常節點重疊（實測），拿來量門檻只會把門檻壓到把正常節點也擋掉。
    junk = [clf.classify(p)[1] for p in junk_patches(session)]
    own_p95 = float(np.percentile(own, 95))
    print(f"正常節點最近距離：中位 {np.median(own):.3f}／p95 {own_p95:.3f}／最大 {max(own):.3f}")
    print(f"（參考）地圖上隨機切的小圖最近距離：最小 {min(junk):.3f}／中位 {np.median(junk):.3f}（{len(junk)} 張，"
          f"含沒圖釘的真節點，僅供參考）")
    clf.reject = round(own_p95 * 1.3, 3)
    print(f"拒絕門檻 {clf.reject}：留一驗證中正常節點被拒 {sum(d > clf.reject for d in own)}/{len(own)}；"
          f"隨機小圖被拒 {sum(d > clf.reject for d in junk)}/{len(junk)}")

    if args.dry_run:
        print("（--dry-run：沒有寫任何檔案）")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(E.APP_DIR, "backup", f"{stamp}_before_overview")
    os.makedirs(backup, exist_ok=True)
    if os.path.exists(E.RULES_PATH):
        shutil.copy2(E.RULES_PATH, os.path.join(backup, "rules.json"))
    if os.path.isdir(O.ASSETS_DIR):
        shutil.copytree(O.ASSETS_DIR, os.path.join(backup, "overview_assets"), dirs_exist_ok=True)
    shutil.rmtree(O.NODES_DIR, ignore_errors=True)
    clf.save(O.NODES_DIR)

    rules = E.RuleBook.load()
    for label in MAP_LABELS:
        rules.set_map(label)
    rules.save()
    print(f"已寫入 {O.NODES_DIR}；規則 {'、'.join(MAP_LABELS)} → 自動選節點；備份在 {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
