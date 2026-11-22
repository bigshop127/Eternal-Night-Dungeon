"""離線回放驗證：拿錄製下來的畫面逐張餵給辨識器，看「認得對不對、會點哪裡」——
全程只讀 PNG，不擷取螢幕、不動滑鼠、不碰遊戲，使用者在玩的時候也能跑。

    python tools/replay_session.py sessions/20260920_003739          # 用「現有樣板」回放（樣板就是從這些畫面建的，偏樂觀）
    python tools/replay_session.py sessions/20260920_003739 --cv     # 交叉驗證：前半當樣板測後半、後半當樣板測前半（較誠實）
    python tools/replay_session.py sessions/20260920_003739 --engine # 把畫面依序餵給「真正的引擎」（假時鐘＋假點擊），看它會點什麼

判讀重點：
  * 「錯認」＝辨識出的畫面跟人工標記不同。有點擊規則的畫面，錯認等於會點錯地方。
  * 「認不得」＝辨識器說不知道，自動操控時就是不動。可接受，但太多代表樣板不夠。
  * 「點擊位置」＝辨識正確時，規則要點的位置跟使用者當時實際點的位置差多少（0~1 比例；
    「點空白繼續」這類任意處都行的畫面，差很多是正常的）。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_templates as S  # noqa: E402

E, V = S.E, S.V


def evaluate(db: V.StateDB, session: str, labels, frames, clicks, rules: "E.RuleBook", kind: str = "before"):
    """回傳 (統計 dict, 錯認清單, 認不得清單)。"""
    stat = collections.defaultdict(lambda: collections.Counter())
    wrong, unknown = [], []
    click_dev = collections.defaultdict(list)
    for i in frames:
        truth = labels[i]
        m = db.match(S.frame(session, i, kind))
        if m.label is None:
            stat[truth]["認不得"] += 1
            unknown.append((i, truth, round(m.distance, 1)))
        elif m.label == truth:
            stat[truth]["對"] += 1
            r = rules.get(truth)
            if r and r["action"] == "click" and i in clicks:
                fx, fy = clicks[i]
                click_dev[truth].append(max(abs(r["x"] - fx), abs(r["y"] - fy)))
        else:
            stat[truth]["錯認"] += 1
            wrong.append((i, truth, m.label, round(m.distance, 1)))
    return stat, wrong, unknown, click_dev


def report(title, stat, wrong, unknown, click_dev):
    print(f"\n===== {title} =====")
    tot = collections.Counter()
    for label in sorted(stat):
        c = stat[label]
        n = sum(c.values())
        tot.update(c)
        dev = click_dev.get(label)
        dev_txt = f"  點擊位置差 中位 {np.median(dev):.3f} 最大 {max(dev):.3f}" if dev else ""
        print(f"  {label:6s} 共{n:3d}：對 {c['對']:3d}  認不得 {c['認不得']:3d}  錯認 {c['錯認']:3d}{dev_txt}")
    n = sum(tot.values())
    print(f"  合計 {n} 張：對 {tot['對']}（{tot['對'] / n:.1%}）／認不得 {tot['認不得']}／錯認 {tot['錯認']}")
    for w in wrong[:15]:
        print("   錯認:", w)
    if unknown:
        by = collections.Counter(t for _, t, _ in unknown)
        print("   認不得的分佈:", dict(by))
    return tot


def run_engine_sim(session, labels, clicks) -> int:
    """把每一幀依序餵給真正的 Engine：跑完整的穩定判定／動作間隔／重試流程，只是擷取用假的、
    點擊用假的、時鐘用假的（快轉）。幀與幀之間插一小段黑畫面當作轉場，貼近真實遊戲。
    比對「引擎會點的位置」跟「規則」：有點擊規則的畫面應該剛好點一下，其他畫面一下都不點。
    地城總覽（規則 map）由規劃器決定點哪個節點或拖地圖，這裡只確認不會出事（不停手），不逐一驗證點哪裡
    （那由 test_overview.py 與 tools/eval_unpinned.py 負責）。"""
    import types

    import control as C

    first = S.frame(session, sorted(labels)[0])
    h, w = first.shape[:2]
    cfg = E.Config.load()
    cfg.window_anchor = None
    cfg.region = {"left": 0, "top": 0, "width": w, "height": h}

    class Clock:
        t = 1000.0

    clock = Clock()
    real_time, real_panic = E.time, C.panic_pressed
    E.time = types.SimpleNamespace(monotonic=lambda: clock.t, sleep=real_time.sleep, time=real_time.time)
    C.panic_pressed = lambda key="F8": False
    try:
        class Grab:
            backend = "fake"
            image = first

            def grab(self, region):
                return self.image

        grab, fired, swiped = Grab(), [], []
        cfg.debug_frames = 0      # 回放的畫面是錄製檔，不要存進使用者的 debug_frames/
        cfg.route_enabled = False  # 錄製走的不是固定路線；而且照路線走會把進度寫進使用者真正的 route_progress.json
        # 點擊與拖地圖都要換成假的：9/27 發現這裡原本沒給 swiper，總覽拖地圖會送出「真的」滑鼠拖曳到使用者的桌面
        eng = E.Engine(cfg, grabber=grab, clicker=lambda x, y, hold=0.06: fired.append((x, y)),
                       swiper=lambda x0, y0, x1, y1, **kw: swiped.append((x0, y0, x1, y1)),
                       guard=lambda x, y: None, busy=lambda: False)
        eng.start_autoplay()
        black = np.zeros((h, w, 3), dtype=np.uint8)
        res = collections.Counter()
        problems, stops = [], []
        for i in sorted(labels):
            truth = labels[i]
            rule = eng.rules.get(truth)
            want_click = bool(rule and rule["action"] == "click")
            is_map = bool(rule and rule["action"] == "map")
            grab.image = S.frame(session, i)
            before = len(fired)
            seen = set()
            for _ in range(8):
                clock.t += 0.2
                st = eng.step()
                seen.add(st.label)
                if st.kind in ("stopped", "error"):
                    stops.append((i, st.message))
                    eng.start_autoplay()
            got = fired[before:]
            if want_click:
                # region.left/top 在這裡是 0，所以螢幕座標就是範圍內座標
                fx, fy = (got[0][0] / (w - 1), got[0][1] / (h - 1)) if got else (None, None)
                if len(got) == 1 and abs(fx - rule["x"]) < 0.01 and abs(fy - rule["y"]) < 0.01:
                    res["該點且點對"] += 1
                elif not got:
                    res["該點卻沒點"] += 1
                    problems.append((i, truth, "沒點", sorted(str(x) for x in seen)))
                else:
                    res["點了但位置/次數不對"] += 1
                    problems.append((i, truth, f"{len(got)} 下 {got}", sorted(str(x) for x in seen)))
            elif is_map:
                res["總覽（規劃器決定，不逐一驗證）"] += 1
            else:
                if got:
                    res["不該點卻點了"] += 1
                    problems.append((i, truth, f"亂點 {got}", sorted(str(x) for x in seen)))
                else:
                    res["不該點且沒點"] += 1
            grab.image = black          # 轉場：讓引擎離開這個畫面（重置「上次點過」的記憶）
            for _ in range(2):
                clock.t += 0.2
                eng.step()
        n = sum(res.values())
        print(f"引擎模擬 {n} 幀（每幀停留 1.6 秒、幀間 0.4 秒黑畫面轉場）：")
        for k in ("該點且點對", "不該點且沒點", "總覽（規劃器決定，不逐一驗證）", "該點卻沒點", "不該點卻點了", "點了但位置/次數不對"):
            if res[k]:
                print(f"  {k:12s} {res[k]:4d}")
        print(f"  自動操控中途被停手：{len(stops)} 次", stops[:3] if stops else "")
        for p_ in problems[:12]:
            print("   問題:", p_)
        bad = res["該點卻沒點"] + res["不該點卻點了"] + res["點了但位置/次數不對"] + len(stops)
        print("結論：", "✅ 引擎行為跟預期完全一致" if bad == 0 else f"❌ 有 {bad} 個偏差")
        return 0 if bad == 0 else 1
    finally:
        E.time, C.panic_pressed = real_time, real_panic


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--cv", action="store_true", help="2 折交叉驗證（前半↔後半）")
    ap.add_argument("--engine", action="store_true", help="把畫面依序餵給真正的引擎（假時鐘＋假點擊），看它實際會點什麼")
    ap.add_argument("--after", action="store_true", help="改用點擊『之後』的畫面 after_NNN.png 回放（沒標記，只看認成什麼）")
    args = ap.parse_args()

    session = os.path.abspath(args.session)
    labels = S.load_labels(session)
    ids = sorted(labels)
    with open(os.path.join(session, "clicks.json"), "r", encoding="utf-8") as f:
        clicks = {c["index"]: tuple(c["region_frac_xy"]) for c in json.load(f)["clicks"]}

    rules = E.RuleBook()
    for label, spec in S.SPEC.items():
        (rules.set_wait(label) if spec.click is None else rules.set_click(label, *spec.click))

    if args.engine:
        return run_engine_sim(session, labels, clicks)

    if args.after:
        db = V.StateDB.load(E.TEMPLATES_DIR)
        cnt = collections.Counter()
        for i in ids:
            m = db.match(S.frame(session, i, "after"))
            cnt[m.label or "(認不得)"] += 1
        print("點擊之後的畫面被辨識成：", dict(cnt))
        return 0

    if not args.cv:
        db = V.StateDB.load(E.TEMPLATES_DIR)
        report("現有樣板回放（in-sample，偏樂觀）", *evaluate(db, session, labels, ids, clicks, rules))
        return 0

    half = len(ids) // 2
    folds = [(ids[:half], ids[half:], "前半當樣板 → 測後半"), (ids[half:], ids[:half], "後半當樣板 → 測前半")]
    grand = collections.Counter()
    for train, test, title in folds:
        db = V.StateDB(18.0, 2.0)
        S.add_samples(db, session, labels, only=set(train))
        S.apply_spec(db)
        grand.update(report(title, *evaluate(db, session, labels, test, clicks, rules)))
    n = sum(grand.values())
    print(f"\n★ 交叉驗證總計 {n} 張：對 {grand['對']}（{grand['對'] / n:.1%}）／認不得 {grand['認不得']}／錯認 {grand['錯認']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
