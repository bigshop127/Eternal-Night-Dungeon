"""螢幕擷取 + 「現在是哪個畫面狀態」辨識。

跟水果2048的 vision.py 是同一個精神（樣板比對、正規化、寧可說不認得也不亂猜），
但這裡沒有規律的 4x4 棋盤要切，比對的對象是「整張畫面（或畫面上一塊固定區域）
長什麼樣子」，用來分類目前停在哪個畫面（地圖／戰鬥／彈窗／獎勵輪盤……）。

一個「狀態」可以有多張樣板（同一個彈窗因為背景動畫、光影而有好幾種樣子都算數），
比對用正規化後的平均像素差異，取最近的樣板；離所有樣板都太遠就回「不認得」，
交給呼叫端決定要停手還是怎麼處理——這支模組本身不猜。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

import numpy as np
from PIL import Image

THUMB = 96  # 樣板縮圖邊長；夠大才留得住畫面細節，又不至於比對太慢

# 區域樣板（box）：只拿畫面上某一塊固定區域來比對，(左, 上, 右, 下)，都是 0~1 的畫面比例。
# 用在兩種情況：①彈窗疊在會一直變動的背景上，只比彈窗本體才不會被背景拖累；
# ②整張畫面根本每次都不同（地城總覽），只能靠不變的那一小塊（標題列）認。
Box = Tuple[float, float, float, float]
BOX_W, BOX_H = 320, 180   # 區域樣板的標準尺度：整張畫面寬 1.0 = 320 px、高 1.0 = 180 px

# 區域樣板比對時容許的位移（畫面像素，x、y 各試這幾個值取最像的）。原因：使用者框選範圍不可能每次都準到
# 1~2px（9/24 實例：範圍上緣高了 2px、多包到模擬器工具列，總覽標題列的距離從 7.0 跳到 14.4 > 門檻 12，
# 整個輔助器認不得總覽）。區域樣板只有一小塊、邊緣對比強，特別怕位移；整張縮圖比對（96×96）幾乎不受影響。
# 9/25：間隔原本是 3px，「提示框」（只比一行文字、門檻 2.0）被使用者的框選位移 2px 卡掉、整輪沒自動按確定——
# 文字邊緣很銳利，位移一格沒踩到格點距離就爆掉。改成間隔 2px，並且把提示框的門檻放寬（見 tools/seed_templates.py）。
BOX_SHIFTS: Tuple[int, ...] = (-6, -4, -2, 0, 2, 4, 6)
# 完整位移搜尋（25 個位置）比較貴，所以平常只試「不位移」與「上次贏的位移」，
# 結果不夠好才做完整搜尋，而且限頻（第一次、之後每 BOX_FULL_EVERY 次呼叫一次），使用者重新框選範圍也會自己找回來。
BOX_FULL_EVERY = 12
BOX_GOOD = 0.85    # 距離 ≤ 該組最嚴門檻的這個比例就算「夠好」，不必再搜尋
# 「差一點」＝不位移的距離在門檻的 2 倍之內：很可能就是那個畫面、只是框選有偏差，這時候不受限頻，每次都做完整搜尋
# （9/25：提示框一出現只做不位移的比對，偏 2~4px 就落在 4.8~5.5、貼著門檻；完整搜尋一次約 1ms×位置數，很便宜，
#  而且只有「差一點」的畫面才會觸發，一般認不得的畫面照舊限頻）
BOX_NEAR = 2.0


class Region(NamedTuple):
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def to_dict(self) -> Dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, d: Dict[str, int]) -> "Region":
        return cls(int(d["left"]), int(d["top"]), int(d["width"]), int(d["height"]))


class ScreenGrabber:
    """包一層 mss，沒裝就退回 Pillow 的 ImageGrab（跟水果2048同一招）。"""

    def __init__(self) -> None:
        self._backend = "PIL"
        self._mss_mod = None
        try:
            import mss

            self._mss_mod = mss
            # mss 10 起 mss.mss() 標示棄用、改叫 mss.MSS()；舊版沒有 MSS，兩邊都要能用
            self._mss_factory = getattr(mss, "MSS", None) or mss.mss
            self._backend = "mss"
        except ImportError:
            pass

    @property
    def backend(self) -> str:
        return self._backend

    def grab(self, region: Region) -> np.ndarray:
        """回傳 (H, W, 3) 的 uint8 RGB 陣列。"""
        if region.width <= 0 or region.height <= 0:
            raise ValueError(f"擷取範圍不合法：{region}")

        if self._backend == "mss":
            with self._mss_factory() as sct:
                raw = sct.grab(region.to_dict())
                arr = np.frombuffer(raw.rgb, dtype=np.uint8)
                return arr.reshape(raw.height, raw.width, 3)

        from PIL import ImageGrab

        bbox = (region.left, region.top, region.right, region.bottom)
        return np.asarray(ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB"))


def _norm_vec(arr: np.ndarray) -> np.ndarray:
    """影像 → 減掉各色版平均值的特徵向量（亮度整體偏移不影響比對）。"""
    a = np.asarray(arr, dtype=np.float32)
    return (a - a.mean(axis=(0, 1), keepdims=True)).reshape(-1)


def to_feature(img: np.ndarray) -> np.ndarray:
    """畫面（任意尺寸）→ 固定大小的正規化特徵向量，用來比對。"""
    return _norm_vec(Image.fromarray(img).resize((THUMB, THUMB), Image.BILINEAR))


def clean_box(box) -> Box:
    """檢查並正規化 box；不合法丟 ValueError。"""
    try:
        l, t, r, b = (round(float(v), 4) for v in box)
    except (TypeError, ValueError):
        raise ValueError(f"區域必須是 (左, 上, 右, 下) 四個數字：{box!r}") from None
    if not (0.0 <= l < r <= 1.0 and 0.0 <= t < b <= 1.0):
        raise ValueError(f"區域必須落在 0~1 之內且右>左、下>上：{(l, t, r, b)}")
    return (l, t, r, b)


def crop_box(img: np.ndarray, box: Box, dx: int = 0, dy: int = 0) -> np.ndarray:
    """從畫面切出 box 那一塊，並縮成標準尺度（跟畫面實際解析度無關，同一塊永遠同尺寸）。

    dx、dy＝把切的位置平移幾個畫面像素（比對時容許框選範圍有小誤差用）；超出畫面邊緣的部分用邊緣像素補。"""
    h, w = img.shape[:2]
    l, t, r, b = box
    x0 = min(max(int(round(l * w)), 0), w - 1)
    x1 = min(max(int(round(r * w)), x0 + 1), w)
    y0 = min(max(int(round(t * h)), 0), h - 1)
    y1 = min(max(int(round(b * h)), y0 + 1), h)
    tw = max(1, int(round((r - l) * BOX_W)))
    th = max(1, int(round((b - t) * BOX_H)))
    if dx == 0 and dy == 0:
        piece = np.ascontiguousarray(img[y0:y1, x0:x1])
    else:
        rows = np.clip(np.arange(y0 + dy, y1 + dy), 0, h - 1)
        cols = np.clip(np.arange(x0 + dx, x1 + dx), 0, w - 1)
        piece = np.ascontiguousarray(img[rows][:, cols])
    return np.asarray(Image.fromarray(piece).resize((tw, th), Image.BILINEAR), dtype=np.uint8)


class StateMatch(NamedTuple):
    label: Optional[str]   # 辨識出的狀態名稱；None = 不認得
    distance: float
    margin: float           # 跟第二像的狀態差多少，越大越有把握


class StateDB:
    """已標記過的畫面狀態樣板庫。每個狀態名稱（label）底下可以有多張樣板。

    樣板有兩種：整張畫面縮圖（預設），或只取畫面一塊區域（box，見上）。兩種可以混用。
    另外兩個進階設定，都是「這個狀態要另外對待」：
      * fallback：後備狀態。只有其他所有狀態都認不得時才拿來比，永遠排在最後。
        地城總覽用它——總覽每次長得都不一樣，只能靠標題列認；但彈窗疊在總覽上時標題列
        也在，如果不排最後，彈窗會被誤判成總覽。
      * label_tolerance：這個狀態專用的、比全域更嚴的距離門檻。動作有代價的畫面
        （例如按下去會消耗東西的確認框）用它，寧可認不得也不誤認。
    """

    VERSION = 1

    # 「像不像」除了絕對距離門檻（tolerance），還要看相對於畫面本身有多少內容：
    # 特徵是減掉各色版平均值之後的樣子，一張純黑／純色的轉場畫面特徵幾乎是零向量，
    # 它跟任何「對比不高的樣板」的距離都只等於那張樣板自己的內容量——可能剛好小於
    # tolerance 而被誤認成那個狀態，自動操控就會在轉場時亂點。所以再加一條：
    # 距離不能超過「兩邊內容量較大者」的 MAX_REL 倍（同一個畫面遇到亮度／雜訊變化，
    # 這個比值實測 < 0.1；純色畫面對任何樣板都是 1.0）。
    MAX_REL = 0.5
    MIN_ENERGY = 4.0   # 內容量的下限，讓「純黑畫面」自己也能被教成一個狀態

    def __init__(self, tolerance: float = 18.0, min_margin: float = 2.0) -> None:
        self.tolerance = tolerance
        self.min_margin = min_margin
        self._labels: List[str] = []
        self._raw: List[np.ndarray] = []              # 整張縮圖 THUMBxTHUMBx3，或區域樣板的裁切圖；uint8、未正規化
        self._boxes: List[Optional[Box]] = []         # 每張樣板的區域；None = 整張畫面
        self.fallback: Set[str] = set()
        self.label_tolerance: Dict[str, float] = {}
        self.box_shifts: Tuple[int, ...] = BOX_SHIFTS   # 區域樣板容許的位移；(0,) 或 () ＝ 不容許（測試用）
        self._shift_hint: Dict[Box, Tuple[int, int]] = {}   # 每個區域上次贏的位移
        self._box_calls: Dict[Box, int] = {}
        self._groups: Optional[Dict[Optional[Box], tuple]] = None

    def __len__(self) -> int:
        return len(self._labels)

    @property
    def labels(self) -> List[str]:
        return sorted(set(self._labels))

    def count_for(self, label: str) -> int:
        return sum(1 for x in self._labels if x == label)

    def set_fallback(self, label: str, on: bool = True) -> None:
        (self.fallback.add if on else self.fallback.discard)(label)

    def set_label_tolerance(self, label: str, tolerance: Optional[float]) -> None:
        if tolerance is None:
            self.label_tolerance.pop(label, None)
        else:
            self.label_tolerance[label] = float(tolerance)

    def _groups_or_build(self) -> Dict[Optional[Box], tuple]:
        """依區域分組：同一個區域的樣板才能疊成一個矩陣一次算完。"""
        if self._groups is None:
            by_box: Dict[Optional[Box], List[int]] = {}
            for i, b in enumerate(self._boxes):
                by_box.setdefault(b, []).append(i)
            groups: Dict[Optional[Box], tuple] = {}
            for b, idx in by_box.items():
                mat = np.stack([_norm_vec(self._raw[i]) for i in idx])
                groups[b] = (np.asarray(idx), mat, np.abs(mat).mean(axis=1))
            self._groups = groups
        return self._groups

    def add(self, label: str, img: np.ndarray, dedup: float = 3.0, box: Optional[Box] = None) -> bool:
        """加一張樣板；跟同一個狀態既有的樣板太像就跳過。box 不給＝整張畫面。"""
        if box is None:
            small = np.asarray(Image.fromarray(img).resize((THUMB, THUMB), Image.BILINEAR), dtype=np.uint8)
        else:
            box = clean_box(box)
            small = crop_box(img, box)
        vec = _norm_vec(small)
        for lbl, existing, b in zip(self._labels, self._raw, self._boxes):
            if lbl == label and b == box and float(np.abs(_norm_vec(existing) - vec).mean()) < dedup:
                return False
        self._labels.append(label)
        self._raw.append(small)
        self._boxes.append(box)
        self._groups = None
        return True

    def remove_label(self, label: str) -> int:
        keep = [i for i, l in enumerate(self._labels) if l != label]
        removed = len(self._labels) - len(keep)
        self._labels = [self._labels[i] for i in keep]
        self._raw = [self._raw[i] for i in keep]
        self._boxes = [self._boxes[i] for i in keep]
        self.fallback.discard(label)
        self.label_tolerance.pop(label, None)
        self._groups = None
        return removed

    def _decide(self, dists: np.ndarray, q_energy: np.ndarray, s_energy: np.ndarray,
                mask: np.ndarray) -> StateMatch:
        """在 mask 圈出的樣板裡挑最像的，並套用全部的「夠不夠像、夠不夠有把握」檢查。"""
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return StateMatch(None, float("inf"), 0.0)
        order = idx[np.argsort(dists[idx], kind="stable")]
        best_i = int(order[0])
        best_label = self._labels[best_i]
        best_d = float(dists[best_i])

        margin = float("inf")
        for i in order[1:]:
            if self._labels[int(i)] != best_label:
                margin = float(dists[int(i)]) - best_d
                break

        content = max(float(q_energy[best_i]), float(s_energy[best_i]), self.MIN_ENERGY)
        tol = min(self.tolerance, self.label_tolerance.get(best_label, self.tolerance))
        if best_d > tol or best_d > self.MAX_REL * content or margin < self.min_margin:
            return StateMatch(None, best_d, margin)
        return StateMatch(best_label, best_d, margin)

    def _match_box(self, img: np.ndarray, box: Box, idx: np.ndarray, mat: np.ndarray):
        """區域樣板：在小範圍位移裡找最像的位置（每張樣板各取自己的最小距離，連同那個位置的內容量）。"""
        tol = min([self.tolerance] + [self.label_tolerance.get(self._labels[int(i)], self.tolerance) for i in idx])
        state = {"d": None, "e": None, "min": float("inf"), "shift": (0, 0)}

        def run(shifts):
            for dx, dy in shifts:
                v = _norm_vec(crop_box(img, box, dx, dy))
                d = np.abs(mat - v).mean(axis=1)
                e = float(np.abs(v).mean())
                if state["d"] is None:
                    state["d"], state["e"] = d, np.full(d.shape, e)
                else:
                    better = d < state["d"]
                    state["d"] = np.where(better, d, state["d"])
                    state["e"] = np.where(better, e, state["e"])
                if float(d.min()) < state["min"]:
                    state["min"], state["shift"] = float(d.min()), (dx, dy)

        hint = self._shift_hint.get(box)
        run([(0, 0)] + ([hint] if hint and hint != (0, 0) else []))
        calls = self._box_calls.get(box, 0)
        self._box_calls[box] = calls + 1
        grid = sorted(set(self.box_shifts) | {0})
        if len(grid) > 1 and state["min"] > tol * BOX_GOOD and (state["min"] <= tol * BOX_NEAR or calls % BOX_FULL_EVERY == 0):
            run([(dx, dy) for dy in grid for dx in grid])
        if state["min"] <= tol:
            self._shift_hint[box] = state["shift"]     # 認得出來的位移記下來，下次先試它
        return state["d"], state["e"]

    def match(self, img: np.ndarray) -> StateMatch:
        if not self._raw:
            return StateMatch(None, float("inf"), 0.0)
        groups = self._groups_or_build()
        n = len(self._raw)
        dists = np.empty(n, dtype=np.float64)
        q_energy = np.empty(n, dtype=np.float64)   # 「畫面那一側」的內容量（每張樣板各自對應的區域）
        s_energy = np.empty(n, dtype=np.float64)   # 樣板自己的內容量
        thumb_vec: Optional[np.ndarray] = None
        for box, (idx, mat, energy) in groups.items():
            if box is None:
                if thumb_vec is None:
                    thumb_vec = to_feature(img)
                v = thumb_vec
                dists[idx] = np.abs(mat - v).mean(axis=1)
                q_energy[idx] = float(np.abs(v).mean())
            else:
                dists[idx], q_energy[idx] = self._match_box(img, box, idx, mat)
            s_energy[idx] = energy

        is_fb = np.array([l in self.fallback for l in self._labels], dtype=bool)
        m = self._decide(dists, q_energy, s_energy, ~is_fb)
        if m.label is None and is_fb.any():
            fb = self._decide(dists, q_energy, s_energy, is_fb)
            if fb.label is not None:
                return fb
        return m

    # -- 存檔：樣板圖存成 templates/<label>/<n>.png，索引存 templates/index.json --
    def save(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)
        counters: Dict[str, int] = {}
        entries = []
        for lbl, raw, box in zip(self._labels, self._raw, self._boxes):
            counters[lbl] = counters.get(lbl, 0) + 1
            sub = os.path.join(dir_path, lbl)
            os.makedirs(sub, exist_ok=True)
            fname = f"{counters[lbl]:03d}.png"
            Image.fromarray(raw).save(os.path.join(sub, fname))
            entry = {"label": lbl, "file": os.path.join(lbl, fname)}
            if box is not None:
                entry["box"] = list(box)
            entries.append(entry)
        data = {"version": self.VERSION, "thumb": THUMB, "entries": entries,
                "tolerance": self.tolerance, "min_margin": self.min_margin}
        if self.fallback:
            data["fallback"] = sorted(self.fallback)
        if self.label_tolerance:
            data["label_tolerance"] = dict(sorted(self.label_tolerance.items()))
        tmp = os.path.join(dir_path, "index.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(dir_path, "index.json"))

    @classmethod
    def load(cls, dir_path: str, tolerance: float = 18.0, min_margin: float = 2.0) -> "StateDB":
        db = cls(tolerance, min_margin)
        index_path = os.path.join(dir_path, "index.json")
        if not os.path.exists(index_path):
            return db
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return db
        if data.get("thumb") != THUMB:
            return db
        db.tolerance = float(data.get("tolerance", tolerance))
        db.min_margin = float(data.get("min_margin", min_margin))
        for entry in data.get("entries", []):
            path = os.path.join(dir_path, entry["file"])
            if not os.path.exists(path):
                continue
            box: Optional[Box] = None
            if entry.get("box") is not None:
                try:
                    box = clean_box(entry["box"])
                except ValueError:
                    continue      # 區域壞掉的樣板寧可丟掉，也不要退化成整張畫面比對
            img = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
            db._labels.append(entry["label"])
            db._raw.append(img)
            db._boxes.append(box)
        present = set(db._labels)
        db.fallback = {l for l in data.get("fallback", []) if l in present}
        for l, t in (data.get("label_tolerance") or {}).items():
            try:
                if l in present:
                    db.label_tolerance[l] = float(t)
            except (TypeError, ValueError):
                pass
        db._groups = None
        return db


__all__ = [
    "Region", "ScreenGrabber", "StateDB", "StateMatch", "to_feature", "crop_box", "clean_box",
    "Box", "THUMB", "BOX_W", "BOX_H", "BOX_SHIFTS",
]
