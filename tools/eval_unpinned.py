"""離線驗證「沒圖釘也能找到隊伍前面的節點」：拿 9/20 那一輪錄製（sessions/<時間>/）逐張餵給 overview.py 的偵測，
只讀 PNG、不擷取螢幕、不動滑鼠，使用者在玩的時候也能跑。

    python tools/eval_unpinned.py sessions/20260920_003739

輸出三件事：
  1. 召回率：你實際點的每個節點（46 次，種類用彈窗標題認），偵測有沒有在「同種類、離你的點擊 45px 內、
     而且鄰接隊伍平台」的位置找到。錯過的會列出原因（沒偵測到／偵測到但不鄰接）。
  2. 每個畫面平均有幾個鄰接候選（越少越好；真的鄰接節點通常 2~3 個）。
  3. 隊伍辨識：跟英雄樣板比對（HeroFinder，信心夠高的才算）當標準答案，看用膚色／青綠披風分左右隊對不對。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overview as O  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overview_assets as B  # noqa: E402

HIT_PX = 45


def evaluate(session: str, heroes: "O.HeroFinder | None" = None) -> dict:
    """回傳 dict：hit／total（召回）、misses（沒找到的清單）、per_frame（每張總覽的鄰接候選數）、
    id_ok／id_bad／id_none（隊伍辨識對／錯／看不準）。"""
    labels, clicks = B.load_session(session)
    hf = heroes if heroes is not None else O.HeroFinder.load(O.HEROES_DIR)
    tot = hit = 0
    misses, per_frame = [], []
    id_ok = id_bad = id_none = 0
    for i in sorted(labels):
        if labels[i] != "總覽":
            continue
        img = B.frame(session, i)
        h = img.shape[0]
        s = h / O.REF_H
        plats = O.find_platforms(img)
        cands = O.find_candidates(img, plats)
        adj = O.adjacent_nodes(cands, plats, img.shape)
        per_frame.append(len(adj))
        nxt = labels.get(i + 1)
        kind = B.NODE_KINDS.get(i) or (O.ENEMY if nxt == "守衛者彈窗" else None)
        if kind is not None and i in clicks:
            kind = O.KIND_ALIAS.get(kind, kind)
            cx, cy = clicks[i]["region_rel_xy"]
            tot += 1

            def near(x, y):
                return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= HIT_PX * s

            if any(n.kind == kind and near(n.x, n.y) for n, _ in adj):
                hit += 1
            else:
                seen = any(c.kind == kind and near(c.x, c.y) for c in cands)
                misses.append((i, O.KIND_NAMES[kind], "偵測到但不鄰接任何隊伍平台" if seen else "沒偵測到", (cx, cy), len(plats)))
        for hero in hf.find(img).values():
            want = hero.side if hero.score >= (0.68 if hero.side == O.LEFT else 0.55) else None
            if want is None:
                continue
            close = [p for p in plats if ((p.x - hero.x) ** 2 + (p.y - hero.y) ** 2) ** 0.5 <= 150 * s]
            if not close:
                continue
            got = min(close, key=lambda p: (p.x - hero.x) ** 2 + (p.y - hero.y) ** 2).side
            if got is None:
                id_none += 1
            elif got == want:
                id_ok += 1
            else:
                id_bad += 1
    return {"hit": hit, "total": tot, "misses": misses, "per_frame": per_frame,
            "id_ok": id_ok, "id_bad": id_bad, "id_none": id_none}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    r = evaluate(os.path.abspath(sys.argv[1]))
    print(f"召回率：{r['hit']}/{r['total']} = {r['hit'] / max(r['total'], 1):.0%}（同種類、離你的點擊 {HIT_PX}px 內、鄰接隊伍平台）")
    for m in r["misses"]:
        print("  沒找到", m)
    pf = r["per_frame"]
    print(f"每個總覽畫面的鄰接候選數：平均 {np.mean(pf):.1f}、中位 {int(np.median(pf))}、最多 {max(pf)}")
    print(f"隊伍辨識（對照英雄樣板高信心結果）：對 {r['id_ok']}、錯 {r['id_bad']}、看不準(None) {r['id_none']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
