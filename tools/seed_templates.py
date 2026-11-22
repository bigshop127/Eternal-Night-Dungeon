"""從一輪錄製（sessions/<時間>/ 的 before_NNN.png ＋ 人工標記 labels.json）建立樣板庫與規則。

為什麼有這支：錄製只是原始素材，不會自己變成「看到什麼畫面就點哪裡」。這支把「每一幀是
哪種畫面」的標記（labels.json）轉成樣板（templates/）與規則（rules.json），
所有「哪種畫面怎麼辨識、點哪裡」的決定都集中在下面的 SPEC，方便日後調整重跑。

    python tools/seed_templates.py sessions/20260920_003739            # 建立（先自動備份）
    python tools/seed_templates.py sessions/20260920_003739 --dry-run  # 只印摘要不寫檔

重跑是安全的：只會重建 SPEC 裡有的那些畫面；你自己用面板教過的其他畫面原樣保留。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402
import vision_state as V  # noqa: E402


class Spec:
    """一種畫面的處理方式。"""

    def __init__(self, click: Optional[Tuple[float, float]] = None, box: Optional[V.Box] = None,
                 fallback: bool = False, tolerance: Optional[float] = None, note: str = "",
                 overview: bool = False) -> None:
        self.click = click          # None＝只辨識、不點（wait）
        self.box = box              # None＝整張畫面比對；給了就只比那一塊
        self.fallback = fallback    # True＝其他畫面都認不得時才用
        self.tolerance = tolerance  # 這種畫面專用、比全域更嚴的距離門檻
        self.note = note
        self.overview = overview    # True＝地城總覽：規則設成 map（自動選節點，見 overview.py）


# 座標＝範圍內的比例（0~1）。量法：使用者實際點擊位置的中位數（見 sessions 的 clicks.json），
# 有代價的兩個確認畫面改用從截圖量出來的按鈕正中央。
SPEC: Dict[str, Spec] = {
    # 疊在地圖上的彈窗，四種外觀（寶箱／治療神符／神秘祭壇／復活泉水）共用同一個「前往」鈕
    "前往彈窗": Spec(click=(0.499, 0.627)),
    "守衛者彈窗": Spec(click=(0.504, 0.838), note="敵方陣容彈窗，黃色「挑戰」鈕"),
    "VS": Spec(click=(0.660, 0.846), note="右邊黃色「挑戰」，不是左邊藍色「調整陣位」"),
    "VICTORY": Spec(click=(0.629, 0.841), note="「退出」，不是「重播」"),
    "點空白繼續": Spec(click=(0.585, 0.716), note="恭喜／治療神符結果／復活泉水結果：點擊任意位置繼續"),
    # 有代價的確認：「已收集的寶石將消失」。只比彈窗的文字區（彈窗本體不透明，不受背景影響），
    # 門檻壓低——換了別段文字的同外框確認框（例如「確定要重置？」）一律認不得、不點。
    # 校準（9/25 重做）：9/20 錄製的三次真實出現距離 0.0，但那只是「樣板自己比自己」——9/25 使用者換了框選範圍
    # （1397×788，9/20 是 1450×818）之後同一個框距離 1.3~2.35，舊門檻 2.0 就整輪沒認出來、確定沒被自動按。
    # 現在門檻 6.0：錄製的 328 張畫面（含 after）＋參考圖裡，非提示框的最近距離是 12.85（總覽淡出的一幀）、
    # 其餘 ≥ 20，所以 6.0 有一倍以上的餘裕；另外收了使用者 9/25 的真畫面當第二張樣板（見 EXTRA_SAMPLES）。
    "提示框": Spec(click=(0.607, 0.696), box=(0.26, 0.40, 0.74, 0.52), tolerance=6.0,
                   note="確定（右邊紅鈕）。已收集的寶石會消失，使用者授權自動按"),
    # 觸發裝置確定之後跳出的「獎勵輪盤」（六顆寶石圍著大寶箱，背景是被壓暗的地圖）：使用者 9/25 說「出現就直接畫面正中間點一下」。
    # 只比正中間的寶箱本體（六顆寶石的顏色每次不同、背景地圖也不同，這兩個都不能比）。
    # 校準：離錄製／參考／使用者其他畫面的最近距離 ≥ 52；自己縮放 0.97 倍 0.8~2.7、位移 3px 5.4、放大 1.03 倍 10.4，
    # 門檻取 25（離別的畫面仍有一倍以上的餘裕）。
    "獎勵輪盤": Spec(click=(0.5, 0.5), box=(0.42, 0.36, 0.58, 0.64), tolerance=25.0,
                     note="六顆寶石圍著大寶箱；點畫面正中間（寶箱）"),
    "五選一": Spec(click=(0.499, 0.779), note="確定；沿用遊戲預設選項（使用者每次也都吃預設的第一個）"),
    # 地城總覽：每次長得都不一樣（3D 地圖會捲動、鏡頭會變），整張比對認不了，只能靠不變的標題列。
    # 後備狀態＝其他畫面都不像才會用它（彈窗疊在總覽上時標題列也在，不能讓它搶先）。
    # 校準：總覽幀離最近的其他總覽 ≤ 8.3；被壓暗的其他畫面（VS／VICTORY／確認框…）≥ 17.1，門檻取 12。
    "總覽": Spec(click=None, box=(0.0, 0.0, 0.36, 0.085), fallback=True, tolerance=12.0, overview=True,
                 note="地城總覽：自動找節點圖釘、依優先序點一個，沒圖釘就拖地圖（9/24 起；原本 9/20 是只等、由使用者自己點）"),
}


def load_labels(session: str) -> Dict[int, str]:
    with open(os.path.join(session, "labels.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): v for k, v in data["labels"].items()}


def frame(session: str, i: int, kind: str = "before") -> np.ndarray:
    return np.asarray(Image.open(os.path.join(session, f"{kind}_{i:03d}.png")).convert("RGB"))


def add_samples(db: V.StateDB, session: str, labels: Dict[int, str], only: Optional[set] = None) -> Dict[str, int]:
    """把標好的幀加進 db；only 給了就只用那些幀（回放驗證做交叉驗證用）。回傳每種畫面收了幾張。"""
    added: Dict[str, int] = {}
    for i in sorted(labels):
        if only is not None and i not in only:
            continue
        spec = SPEC.get(labels[i])
        if spec is None:
            continue
        if db.add(labels[i], frame(session, i), box=spec.box):
            added[labels[i]] = added.get(labels[i], 0) + 1
    return added


# 不在錄製裡的樣本（使用者另外給的畫面）：{畫面: [(圖檔相對專案資料夾, 裁切 (左, 上, 右, 下) 或 None)]}。
# 裁切＝把截圖裡不是遊戲畫面的部分（BlueStacks 工具列）去掉，剩下的跟框選範圍一樣。
EXTRA_SAMPLES: Dict[str, list] = {
    "提示框": [("reference_notes/17_prompt_dialog_0925.png", (0, 35, 1399, 821))],
    "獎勵輪盤": [("reference_notes/19_reward_wheel_0925.png", None)],
}


def add_extra_samples(db: V.StateDB) -> Dict[str, int]:
    added: Dict[str, int] = {}
    for label, items in EXTRA_SAMPLES.items():
        for rel, crop in items:
            path = os.path.join(E.APP_DIR, rel)
            if not os.path.exists(path):
                print(f"（找不到額外樣本 {rel}，略過）")
                continue
            img = np.asarray(Image.open(path).convert("RGB"))
            if crop is not None:
                l, t, r, b = crop
                img = img[t:b, l:r]
            if db.add(label, img, box=SPEC[label].box):
                added[label] = added.get(label, 0) + 1
    return added


def apply_spec(db: V.StateDB) -> None:
    for label, spec in SPEC.items():
        if db.count_for(label):
            db.set_fallback(label, spec.fallback)
            db.set_label_tolerance(label, spec.tolerance)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="sessions/<時間> 資料夾（要有 before_NNN.png 與 labels.json）")
    ap.add_argument("--dry-run", action="store_true", help="只印摘要，不寫任何檔案")
    args = ap.parse_args()

    session = os.path.abspath(args.session)
    labels = load_labels(session)
    unknown = sorted({l for l in labels.values() if l not in SPEC})
    if unknown:
        print("labels.json 裡有 SPEC 沒定義的畫面（會略過）：", "、".join(unknown))

    db = V.StateDB.load(E.TEMPLATES_DIR)
    kept = [l for l in db.labels if l not in SPEC]
    for label in SPEC:            # 重建 SPEC 裡的畫面；其他（使用者自己教的）原樣保留
        db.remove_label(label)
    added = add_samples(db, session, labels)
    for label, n in add_extra_samples(db).items():
        added[label] = added.get(label, 0) + n
    apply_spec(db)
    rules = E.RuleBook.load()
    for label, spec in SPEC.items():
        if not db.count_for(label):
            continue
        if spec.overview:
            rules.set_map(label)
        elif spec.click is None:
            rules.set_wait(label)
        else:
            rules.set_click(label, *spec.click)

    print(f"保留使用者自己教的畫面：{kept or '（無）'}")
    print("新建樣板：")
    for label in SPEC:
        if label in added:
            s = SPEC[label]
            how = "區域比對" if s.box else "整張比對"
            extra = "／後備" if s.fallback else ""
            extra += f"／專用門檻 {s.tolerance}" if s.tolerance is not None else ""
            act = rules.describe(label)
            print(f"  {label:6s} {added[label]:3d} 張  {how}{extra}  →  {act}")
    if args.dry_run:
        print("（--dry-run：沒有寫任何檔案）")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(E.APP_DIR, "backup", f"{stamp}_before_seed")
    os.makedirs(backup, exist_ok=True)
    if os.path.isdir(E.TEMPLATES_DIR):
        shutil.copytree(E.TEMPLATES_DIR, os.path.join(backup, "templates"))
    if os.path.exists(E.RULES_PATH):
        shutil.copy2(E.RULES_PATH, backup)
    for label in SPEC:            # 舊資料夾清掉再存，免得殘留改名前的樣板圖
        shutil.rmtree(os.path.join(E.TEMPLATES_DIR, label), ignore_errors=True)
    db.save(E.TEMPLATES_DIR)
    rules.save()
    print(f"已寫入 {E.TEMPLATES_DIR} 與 {E.RULES_PATH}；舊版備份在 {backup}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
