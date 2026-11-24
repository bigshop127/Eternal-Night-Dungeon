"""地城總覽（3D 可捲動地圖）的自動化：找節點 → 認種類 → 依優先序挑一個 → 沒有就拖地圖找。

總覽每次長得都不一樣，整張比對認不了（見 vision_state 的「後備狀態」說明），但它有一個穩定的
線索：**可選的節點上方會浮著一個黃色小人圖釘**。用你 9/20 那一輪錄製驗證過——你在總覽點的節點，
50 次裡有 47 次落在最近圖釘的正下方約 105px（圖釘底端往下），剩下幾次是圖釘被頂端標題列遮住一半，
偵測放寬就補上了。圖釘底下那塊小圖（寶箱／綠色治療符文／紫光裝置／藍水晶泉水／帶魔法陣的怪物）
視覺差異很大，用錄製時的彈窗標題當標準答案做成小樣板，留一驗證約 92% 分對。

跟 engine.py 一樣的分工：這裡全是純函式/純資料，不碰 tkinter、不碰滑鼠、不擷取螢幕，
測試時直接餵圖片就好。點擊與拖曳由 engine.py 執行。

核心原則（沿用「寧可停手也不亂點」）：
  * 畫面上疊著亮色彈窗（淺藍面板）、或整個畫面被壓暗（暗遮罩彈窗）、或地圖還在移動 → 一律不動作。
  * 認不得種類的節點（例如金色問號「命運的契機」）**不自動點**——那裡面有你 9/4 訂的取捨規則，
    要人決定。
  * 同一個節點點了沒反應就換別的，全部都沒反應就停手，不會對著同一點一直空點。

9/25 增補：**遊戲有時本來就不顯示圖釘**（例如左隊卡在觸發裝置、把畫面拉到右隊那邊時），但隊伍前面的戰鬥／寶箱／治療
照樣能點。所以「沒圖釘」不等於沒得選：改找「隊伍腳下發亮的平台」＋「平台前方（鄰接）的節點本身的樣子」
（怪物腳下的魔法陣光環、金色寶箱、綠色治療符文、藍色泉水、紫色光柱），找得到就點；還是沒有才拖地圖，
而且會先「往下看」（找被拋在後面的另一隊）再往上、往左右。所有色彩門檻都是用你 9/20 那一輪錄製
（46 次點節點）與 9/25 貼的畫面量出來的，見 tools/eval_unpinned.py。
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

# 資料夾可以用環境變數換掉（跟 engine.py 同一個約定）——測試不能去動使用者真正的樣板
APP_DIR = os.environ.get("ENDUNGEON_DATA_DIR") or (
    os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)     # 打包成 exe：資料放在 exe 旁邊
    else os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(APP_DIR, "overview_assets")
NODES_DIR = os.path.join(ASSETS_DIR, "nodes")
HEROES_DIR = os.path.join(ASSETS_DIR, "heroes")

REF_H = 818.0   # 下面所有像素常數都是在「範圍高 818」的畫面上量的，實際使用時依高度等比縮放

# 節點種類（key 用英文，畫面上顯示用 KIND_NAMES）
CHEST, ENEMY, HEAL, REVIVE, DEVICE = "chest", "enemy", "heal", "revive", "device"
# 空格：沒有東西、點了直接走過去（不跳彈窗）的平台。只有固定路線用得到（第 2 段右隊荊棘消失後那一格，9/20 錄製第 76 次點擊），
# 分類器不會認出這個種類
WALK = "walk"
KIND_NAMES = {CHEST: "寶箱", ENEMY: "怪物", HEAL: "治療", REVIVE: "復活", DEVICE: "裝置", WALK: "空格"}

# 優先序（前面的先點）：使用者 9/24 的規則——寶箱、治療為主，寶箱盡可能都選；治療要等左右兩隊都打過才最划算
# （兩隊會同時回血，另一隊沒受傷就浪費了，見 choose_for_teams 的閘門）；
# 觸發裝置一按就進下一階段（已收集的寶石會消失），所以放最後。
DEFAULT_PRIORITY: Tuple[str, ...] = (CHEST, HEAL, ENEMY, REVIVE, DEVICE)

# 使用者 9/25：「藍色泉水（原本我分成復活）一律視作治癒，一定要選」——遊戲裡它就是治療的另一種外觀。
# 所以分類時一律併進治療（REVIVE 仍保留在常數裡，舊的樣板檔載入時會被換成 HEAL）。
KIND_ALIAS = {REVIVE: HEAL}

# 左右兩隊（使用者 9/24 說明）：畫面分左右兩邊、各派一隊進攻，用英雄的長相分辨——
# 野蠻人（光著上身、金色法杖）＝左隊；法師（藍色披風、青色寶珠法杖）＝右隊。
# 閃爍的黃色圖釘＝該隊下一步能選的節點；沒有圖釘的節點就是還沒走到。
LEFT, RIGHT = "left", "right"
SIDES = (LEFT, RIGHT)
SIDE_NAMES = {LEFT: "左隊", RIGHT: "右隊"}
HERO_NAMES = {LEFT: "野蠻人", RIGHT: "法師"}
# 英雄樣板是從高 834 的範圍裁出來的；英雄在畫面上會因透視大小不同，所以多尺度比對
HERO_REF_H = 834.0
HERO_SCALES = (0.85, 1.0, 1.15)
HERO_MIN_SCORE = {LEFT: 0.62, RIGHT: 0.52}    # 實測：對的偵測 野蠻人 ≥0.68／法師 ≥0.55，錯的 ≤0.54／≤0.47
HERO_FAR = 0.55        # 只偵測到一位英雄時，離它超過畫面寬的這個比例的圖釘，不算它那隊（改用左右半邊判斷）

# 圖釘（黃色小人）偵測
PIN_W = (9, 46)         # 寬（參考尺度像素）
PIN_H = (10, 78)        # 高；被頂端遮住一半的圖釘會矮很多，所以下限放低
PIN_MIN_ASPECT = 1.4    # 高/寬；小人是瘦高的（實測 1.6~3.1），寶箱鑲邊之類的黃色塊比較寬扁
PIN_FULL_MIN_H = 36     # 完整圖釘的最矮高度（實測 41~58）
PIN_MIN_FILL = 0.43     # 面積/外框面積（實測 0.46~0.63）
PIN_MIN_AREA = 60
HEADER_BAND = 0.075     # 畫面最上面這一段是標題列（實測約 0.068），圖釘在它後面會被蓋住
PIN_TO_NODE_DY = 105    # 圖釘「底端」到節點上東西正中央的距離（用底端當基準，頂端被切掉也準）
# 介面上會有黃色但不是圖釘的地方：右下三大圖示＋獎勵、左下兩個小圖示
HUD_ZONES = ((0.62, 0.66, 1.0, 1.0), (0.0, 0.78, 0.22, 1.0))
# 節點點位絕對不能落在的地方＝上面那些，加上遊戲自己的按鈕（點到會誤開別的功能）：
# 右邊的獎勵輪盤六角圖示、左邊浮動的「f」按鈕（實測都在你的截圖上）
KEEP_OUT_ZONES = HUD_ZONES + ((0.88, 0.18, 0.97, 0.36), (0.0, 0.26, 0.03, 0.40))

# 彈窗檢查：亮色彈窗的淺藍面板佔中央區域的比例。實測 前往彈窗 0.24、守衛者彈窗 0.18~0.24，總覽 ≤ 0.001
PALE_MAX = 0.03

# 節點小圖：以節點中心為準，切 WIN 見方（參考尺度），縮成 STORE 存檔、FEAT 做特徵
WIN, STORE, FEAT = 112, 56, 28
PATCH_DY = -10          # 小圖中心比節點點擊點高一點（東西站在平台上，重心偏上）


class Pin(NamedTuple):
    cx: int
    top: int
    bottom: int
    w: int
    h: int
    area: int


class Node(NamedTuple):
    x: int                  # 要點的位置（範圍內像素）
    y: int
    pin: Pin
    kind: Optional[str]     # None＝認不得種類
    distance: float
    pinless: bool = False   # True＝沒有圖釘、靠節點本身的樣子找到的（pin 是湊出來的替身，只拿來排序）


class Hero(NamedTuple):
    side: str               # LEFT／RIGHT
    x: int                  # 英雄在範圍內的位置
    y: int
    score: float            # 樣板相關係數（0~1，越高越像）


class MapPlan(NamedTuple):
    kind: str               # click / drag / wait / stop
    reason: str             # 給使用者看的說明
    x: int = 0
    y: int = 0
    x1: int = 0             # drag 的終點
    y1: int = 0
    node: Optional[Node] = None
    side: Optional[str] = None      # 沒圖釘的節點／路線上的節點：規劃時就已經知道是哪一隊的（從隊伍腳下的平台判斷）
    tag: str = ""                   # drag 的用途：scan＝照走法找另一隊、center＝把找到的隊伍拖到畫面中間
    route: Optional[Tuple[str, int]] = None     # 照路線點的：(隊, 路線第幾步)，畫面離開總覽才算走完這一步（route.py）
    fast: bool = False              # 換邊找另一隊（11/22）：大步拖、拖完不必等那麼久（engine 用 switch_interval 取代 map_interval）


class MapView(NamedTuple):
    nodes: List[Node]       # 「現在有」的節點（最近 fresh_seconds 內看到過）
    hidden: int             # 記得但暫時看不到的圖釘數（閃爍／被遮住）
    clean: bool             # 沒有彈窗疊在地圖上
    stable: bool            # 沒有新出現的圖釘、沒有圖釘大幅移動（鏡頭沒在動）


def _scale(img: np.ndarray) -> float:
    return img.shape[0] / REF_H


# ---------------------------------------------------------------- 圖釘偵測

def _components(mask: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
    """8 連通區域標記（不用 scipy，專案本來只依賴 numpy/Pillow）。只走有值的像素，很快。"""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return []
    pts = set(zip(ys.tolist(), xs.tolist()))
    out = []
    while pts:
        seed = pts.pop()
        stack, comp = [seed], [seed]
        while stack:
            y, x = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    q = (y + dy, x + dx)
                    if q in pts:
                        pts.remove(q)
                        stack.append(q)
                        comp.append(q)
        arr = np.array(comp)
        out.append((arr[:, 0], arr[:, 1]))
    return out


def _in_zone(cx: float, cy: float, w: int, h: int, zones) -> bool:
    for l, t, r, b in zones:
        if l * w <= cx <= r * w and t * h <= cy <= b * h:
            return True
    return False


def _in_hud(cx: float, cy: float, w: int, h: int) -> bool:
    return _in_zone(cx, cy, w, h, HUD_ZONES)


def find_pins(img: np.ndarray) -> List[Pin]:
    """找出畫面上所有黃色小人圖釘，由上到下排序。"""
    h, w = img.shape[:2]
    s = _scale(img)
    r = img[..., 0].astype(np.int16)
    g = img[..., 1].astype(np.int16)
    b = img[..., 2].astype(np.int16)
    mask = (r > 225) & (g > 180) & (b < 110) & (r - b > 140)
    pins: List[Pin] = []
    for ys, xs in _components(mask):
        area = int(ys.size)
        pw, ph = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
        top, bottom = int(ys.min()), int(ys.max())
        cx = int(round(xs.mean()))
        # 頂端落在畫面最上緣／標題列高度內＝被切掉或被標題列蓋住一截，形狀不完整，只認「窄長」
        clipped = top <= HEADER_BAND * h
        if not (PIN_W[0] * s <= pw <= PIN_W[1] * s):
            continue
        if not (PIN_H[0] * s <= ph <= PIN_H[1] * s):
            continue
        if area < PIN_MIN_AREA * s * s * (0.3 if clipped else 1.0):
            continue
        if not clipped:
            # 完整的圖釘是瘦長小人：夠高、夠瘦、填得夠滿。地圖上「命運的契機」的金色問號顏色一樣，
            # 但比較矮、鉤子形所以填充率低（實測 高 32／填充 0.38，圖釘 高 41~58／填充 ≥ 0.46）
            if ph / pw < PIN_MIN_ASPECT or ph < PIN_FULL_MIN_H * s or area / (pw * ph) < PIN_MIN_FILL:
                continue
        if _in_hud(cx, (top + bottom) / 2, w, h):
            continue
        pins.append(Pin(cx, top, bottom, pw, ph, area))
    pins.sort(key=lambda p: (p.bottom, p.cx))
    return pins


def node_point(pin: Pin, img_h: int) -> Tuple[int, int]:
    """圖釘 → 節點上要點的位置（圖釘正下方）。"""
    return pin.cx, pin.bottom + int(round(PIN_TO_NODE_DY * img_h / REF_H))


def pale_fraction(img: np.ndarray) -> float:
    """亮色彈窗的淺藍面板佔畫面中央的比例。地圖本身是暗藍灰，幾乎是 0。"""
    h, w = img.shape[:2]
    c = img[int(h * 0.12):int(h * 0.90), int(w * 0.10):int(w * 0.90)].astype(np.int16)
    m = (c[..., 2] > 170) & (c[..., 1] > 150) & (c[..., 0] > 110) & (c[..., 2] >= c[..., 1]) \
        & (c[..., 2] - c[..., 0] > 15)
    return float(m.mean())


# 暗遮罩彈窗（提示框、勝利結算、獎勵輪盤…）會把整個畫面壓暗，連左上角的返回箭頭也一起變暗；
# 這個箭頭是固定不動的介面，實測：總覽／亮色彈窗＝147.9~161、被壓暗的畫面＝23~96.5（±8px 框選誤差內都一樣）。
# 圖釘在暗遮罩下看不到、隊伍平台也會變暗，這時候絕不能當成「沒圖釘」去拖地圖或亂點。
DIM_MIN = 110.0


def header_lum(img: np.ndarray) -> float:
    """左上角返回箭頭那一塊的平均亮度（0~255）。"""
    h, w = img.shape[:2]
    return float(img[int(0.005 * h):int(0.07 * h), int(0.01 * w):int(0.075 * w)].mean())


def is_dimmed(img: np.ndarray) -> bool:
    return header_lum(img) < DIM_MIN


# ---------------------------------------------------------------- 節點分類

def node_patch(img: np.ndarray, x: int, y: int) -> np.ndarray:
    """節點（x, y）周圍的小圖，固定 STORE×STORE 的 uint8，跟畫面解析度無關。"""
    h, w = img.shape[:2]
    s = _scale(img)
    half = WIN * s / 2.0
    cy = y + PATCH_DY * s
    x0, x1 = int(round(x - half)), int(round(x + half))
    y0, y1 = int(round(cy - half)), int(round(cy + half))
    pad = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
    xa, xb, ya, yb = max(x0, 0), min(x1, w), max(y0, 0), min(y1, h)
    if xb > xa and yb > ya:
        pad[ya - y0:yb - y0, xa - x0:xb - x0] = img[ya:yb, xa:xb]
    return np.asarray(Image.fromarray(pad).resize((STORE, STORE), Image.BILINEAR), dtype=np.uint8)


def _raw_feat(patch: np.ndarray) -> np.ndarray:
    p = np.asarray(Image.fromarray(patch).resize((FEAT, FEAT), Image.BILINEAR), dtype=np.float32).reshape(-1)
    return (p - p.mean()) / (p.std() + 1e-6)


def _color_feat(patch: np.ndarray) -> np.ndarray:
    a = patch.reshape(-1, 3).astype(np.float32) / 255.0
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    mx, mn = a.max(1), a.min(1)
    sat = (mx - mn) / (mx + 1e-6)
    return np.array([
        ((r > .55) & (g > .40) & (b < .35) & (r > b * 1.6)).mean(),          # 金色（寶箱鑲邊）
        ((g > r * 1.25) & (g > b * 1.05) & (g > .35)).mean(),                 # 亮綠（治療符文）
        ((b > .40) & (r > .35) & (g < .5 * b) & (r < b * 1.1)).mean(),        # 紫（觸發裝置光柱）
        ((b > .50) & (g > .50) & (r < .45 * b)).mean(),                       # 青藍（復活泉水）
        (mx > .75).mean(), mx.mean(), sat.mean(),
    ], dtype=np.float32)


class NodeClassifier:
    """最近鄰：把節點小圖跟已標好種類的樣板比，最近的種類就是答案；離所有樣板都太遠回 None。"""

    def __init__(self) -> None:
        self.kinds: List[str] = []
        self.patches: List[np.ndarray] = []
        self.reject: float = float("inf")       # 距離超過這個就說「認不得」
        self._raw: Optional[np.ndarray] = None
        self._col: Optional[np.ndarray] = None
        self._col_mu: Optional[np.ndarray] = None
        self._col_sd: Optional[np.ndarray] = None

    def __len__(self) -> int:
        return len(self.kinds)

    def add(self, kind: str, patch: np.ndarray) -> None:
        self.kinds.append(KIND_ALIAS.get(kind, kind))
        self.patches.append(np.ascontiguousarray(patch, dtype=np.uint8))
        self._raw = None

    def _build(self) -> None:
        self._raw = np.stack([_raw_feat(p) for p in self.patches])
        col = np.stack([_color_feat(p) for p in self.patches])
        self._col_mu = col.mean(0)
        self._col_sd = col.std(0) + 1e-3
        self._col = (col - self._col_mu) / self._col_sd

    def distances(self, patch: np.ndarray) -> np.ndarray:
        """跟每張樣板的距離（原始像素差 + 顏色特徵差，兩者各自壓到相近尺度再相加）。"""
        if self._raw is None:
            self._build()
        d_raw = np.abs(self._raw - _raw_feat(patch)).mean(axis=1)
        c = (_color_feat(patch) - self._col_mu) / self._col_sd
        d_col = np.linalg.norm(self._col - c, axis=1) / np.sqrt(c.size)
        return d_raw + d_col

    def classify(self, patch: np.ndarray) -> Tuple[Optional[str], float]:
        if not self.kinds:
            return None, float("inf")
        d = self.distances(patch)
        i = int(np.argmin(d))
        best = float(d[i])
        return (self.kinds[i] if best <= self.reject else None), best

    def leave_one_out(self) -> Tuple[int, int, List[float]]:
        """留一驗證：每張樣板拿掉自己去分類。回 (對幾張, 總張數, 每張的最近距離)。"""
        if self._raw is None:
            self._build()
        ok, dists = 0, []
        for k in range(len(self.kinds)):
            d = self.distances(self.patches[k])
            d[k] = np.inf
            j = int(np.argmin(d))
            ok += self.kinds[j] == self.kinds[k]
            dists.append(float(d[j]))
        return ok, len(self.kinds), dists

    # -- 存檔：nodes/<kind>/NNN.png ＋ index.json --
    def save(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)
        counters: Dict[str, int] = {}
        entries = []
        for kind, patch in zip(self.kinds, self.patches):
            counters[kind] = counters.get(kind, 0) + 1
            sub = os.path.join(dir_path, kind)
            os.makedirs(sub, exist_ok=True)
            fname = f"{counters[kind]:03d}.png"
            Image.fromarray(patch).save(os.path.join(sub, fname))
            entries.append({"kind": kind, "file": f"{kind}/{fname}"})
        tmp = os.path.join(dir_path, "index.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "store": STORE, "reject": self.reject, "entries": entries},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(dir_path, "index.json"))

    @classmethod
    def load(cls, dir_path: str) -> "NodeClassifier":
        c = cls()
        index = os.path.join(dir_path, "index.json")
        if not os.path.exists(index):
            return c
        try:
            with open(index, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return c
        if data.get("store") != STORE:
            return c
        for e in data.get("entries", []):
            path = os.path.join(dir_path, e["file"])
            if os.path.exists(path):
                c.add(e["kind"], np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8))
        try:
            c.reject = float(data.get("reject", float("inf")))
        except (TypeError, ValueError):
            pass
        return c


# ---------------------------------------------------------------- 英雄偵測（分左右隊）

def _box_sum(a: np.ndarray, h: int, w: int) -> np.ndarray:
    """每個 h×w 視窗的總和（積分圖）。回傳 (H-h+1, W-w+1)。"""
    c = np.cumsum(np.cumsum(np.pad(a, ((1, 0), (1, 0))), 0), 1)
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def _ncc(img: np.ndarray, t: np.ndarray) -> np.ndarray:
    """聯合三個色版的正規化相關係數（FFT 做相關；不用 OpenCV／scipy，專案只依賴 numpy/Pillow）。
    img (H,W,3) float32、t (h,w,3) float32 → (H-h+1, W-w+1)，值在 -1~1。"""
    H, W = img.shape[:2]
    h, w = t.shape[:2]
    Hp, Wp = 1 << int(np.ceil(np.log2(H))), 1 << int(np.ceil(np.log2(W)))
    tz = t - t.mean(axis=(0, 1), keepdims=True)
    tn = float((tz ** 2).sum())
    num = np.zeros((H - h + 1, W - w + 1), np.float32)
    var = np.zeros_like(num)
    n = h * w
    for c in range(3):
        F = np.fft.rfft2(img[..., c], s=(Hp, Wp))
        T = np.fft.rfft2(tz[..., c], s=(Hp, Wp))
        num += np.fft.irfft2(F * np.conj(T), s=(Hp, Wp))[:H - h + 1, :W - w + 1]
        s1 = _box_sum(img[..., c], h, w)
        var += _box_sum(img[..., c] ** 2, h, w) - s1 * s1 / n
    out = num / (np.sqrt(np.maximum(var, 1e-6) * tn) + 1e-6)
    # 視窗內幾乎沒有變化（純色區塊）時，分子分母都是浮點雜訊，比值會亂跳（11/22：新英雄樣板在純色畫面上給出 0.60）→ 當 0
    out[var < 4.0 * n] = 0.0
    return out


class HeroFinder:
    """用兩位英雄的小圖（overview_assets/heroes/left.png＝野蠻人、right.png＝法師）在畫面上找他們。

    多尺度模板比對，1/2 縮圖上做，一次約 0.3~0.5 秒——所以只在「要點節點」那一刻才跑，不是每一輪都跑。
    誠實的限度（用你 9/20 那一輪 55 張總覽驗證）：姿勢動畫、金色塵土特效、或英雄根本不在畫面內時會找不到
    （回傳裡就沒有那一隊）；找到的則靠「兩個樣板在同一位置競爭、分數要過門檻」擋掉誤認。
    """

    DOWN = 2

    def __init__(self, templates: Optional[Dict[str, object]] = None) -> None:
        # 每隊可以有好幾張樣板（11/22：使用者換了英雄——左隊換成拿雙環刃的女英雄、右隊換成尖帽法師，
        # left_2.png／right_2.png；舊的野蠻人／法師樣板留著，換回去也認得）
        self.templates: Dict[str, List[np.ndarray]] = {
            k: (list(v) if isinstance(v, (list, tuple)) else [v]) for k, v in (templates or {}).items()}

    @property
    def ready(self) -> bool:
        return bool(self.templates)

    @classmethod
    def load(cls, dir_path: str) -> "HeroFinder":
        t: Dict[str, List[np.ndarray]] = {}
        if os.path.isdir(dir_path):
            names = sorted(os.listdir(dir_path))
            for side in SIDES:
                for f in names:
                    if f.lower().endswith(".png") and (f[:-4] == side or f.startswith(side + "_")):
                        t.setdefault(side, []).append(np.asarray(Image.open(os.path.join(dir_path, f)).convert("RGB"),
                                                                 dtype=np.uint8))
        return cls(t)

    def find(self, img: np.ndarray) -> Dict[str, Hero]:
        if not self.templates:
            return {}
        d = self.DOWN
        small = np.asarray(Image.fromarray(img).resize((img.shape[1] // d, img.shape[0] // d), Image.BILINEAR),
                           dtype=np.float32)
        base = img.shape[0] / HERO_REF_H
        best: Dict[str, Tuple[float, int, int]] = {}
        for side, tmpls in self.templates.items():
            top = (-1.0, 0, 0)
            for tmpl, sc in ((t, sc) for t in tmpls for sc in HERO_SCALES):
                th = max(8, int(round(tmpl.shape[0] * sc * base / d)))
                tw = max(8, int(round(tmpl.shape[1] * sc * base / d)))
                if th >= small.shape[0] or tw >= small.shape[1]:
                    continue
                t = np.asarray(Image.fromarray(tmpl).resize((tw, th), Image.BILINEAR), dtype=np.float32)
                m = _ncc(small, t)
                i = np.unravel_index(int(np.argmax(m)), m.shape)
                if m[i] > top[0]:
                    top = (float(m[i]), (i[1] + tw // 2) * d, (i[0] + th // 2) * d)
            best[side] = top
        # 過門檻的才算；兩隊找到同一個位置（同一個人被兩個樣板搶）→ 只留相對門檻贏比較多的那個
        found = {s: Hero(s, int(x), int(y), float(sc)) for s, (sc, x, y) in best.items() if sc >= HERO_MIN_SCORE[s]}
        if len(found) == 2:
            a, b = found[LEFT], found[RIGHT]
            if abs(a.x - b.x) <= 60 * base and abs(a.y - b.y) <= 60 * base:
                if a.score / HERO_MIN_SCORE[LEFT] >= b.score / HERO_MIN_SCORE[RIGHT]:
                    del found[RIGHT]
                else:
                    del found[LEFT]
        return found


def side_of(node: "Node", heroes: Dict[str, Hero], img_w: int) -> Optional[str]:
    """這個圖釘（節點）屬於哪一隊。有偵測到英雄就看離哪位英雄近；只偵測到一位而且圖釘離它太遠、
    或一位都沒偵測到，就退回畫面左半＝左隊、右半＝右隊（使用者說畫面分左右兩邊各派一隊）。"""
    px, py = node.pin.cx, node.pin.bottom
    if heroes:
        near = min(heroes.values(), key=lambda h: (h.x - px) ** 2 + (h.y - py) ** 2)
        far = ((near.x - px) ** 2 + (near.y - py) ** 2) ** 0.5 > HERO_FAR * img_w
        if len(heroes) == 2 or not far:
            return near.side
    return LEFT if px < img_w / 2 else RIGHT


def side_of_sure(node: "Node", heroes: Dict[str, Hero], img_w: int) -> Optional[str]:
    """拿來擋治療用的「有把握」版：英雄判斷跟畫面左右半邊一致才算，不一致就回 None（不確定）。

    擋治療會讓路線改點「另一隊」的怪物；如果把同一個岔路的兩個選項誤分到不同隊，就會點到旁邊的怪物、把治療放掉
    （9/24 使用者回報的症狀）。畫面正中央那一欄（錄製裡很常見 x≈724）、或只偵測到一位英雄時最容易分錯，
    所以不確定就當作分不出隊——治療照點，寧可少擋一次。"""
    by_hero = side_of(node, heroes, img_w)
    by_half = LEFT if node.pin.cx < img_w / 2 else RIGHT
    return by_hero if by_hero == by_half else None


# ---------------------------------------------------------------- 沒圖釘的節點（隊伍腳邊的下一步）
#
# 為什麼要有這一段：遊戲有時本來就不顯示圖釘（使用者 9/25：左隊卡住、把畫面拉到右隊時就沒有圖釘），但隊伍
# 前面的兩個戰鬥照樣能點。9/20 錄製裡「沒圖釘也點了」的 46 次節點點擊，有 11 次就是這種情況
# （寶箱在英雄正前方、怪物在法師前方…）。
#
# 做法（全是顏色／形狀特徵，不靠樣板，也不靠英雄長相——英雄轉身、換姿勢樣板就找不到，實測法師背對時樣板比對失敗）：
#   1. 隊伍站的那格平台會發出暖白色的光，是畫面上最大的一塊「低飽和暖灰」（隊伍平台）。
#      平台上方的膚色（野蠻人）／青綠披風（法師）像素多寡，分左隊右隊。
#   2. 節點本身：怪物腳下的魔法陣光環（橢圓、綠／藍／紫）、寶箱（暗金色塊）、治療符文（亮黃綠）、
#      藍色泉水（白青光柱，治療的另一種外觀）、觸發裝置（紫色光柱）。
#   3. 只留下「鄰接」隊伍平台的：把節點相對平台的位置除以該處的透視縮放（越靠畫面下方越大，
#      地平線約在參考尺度 y=48）之後，落在「正前方」或「左前／右前」兩個位置的才算能點。
# 46 次錄製點擊裡，這套找回 40 次（最後有 3 次是偵測不到：光柱被標題列切掉、怪物光環被擋…），平均每個畫面 1.7 個候選。
# 找錯的代價很低：點了不能點的節點遊戲沒反應，規劃器點兩次沒反應就換別的。

MAX_MASK_PX = 20000     # 半解析度下色彩遮罩的亮點數上限；超過＝整片被染色（特效、遮罩），這個偵測不可信、直接放棄
MAX_COMPONENTS = 1500   # 遮罩的碎片數上限（實測總覽畫面 ≤ 405）；超過＝雜訊，放棄
PLAT_MIN_W, PLAT_MIN_H, PLAT_MIN_AREA = 90, 30, 2300    # 隊伍平台的亮光：寬、高、面積（參考尺度；實測 135×114、149×149、153×135）
HERO_PIX_MIN = 250      # 平台上方的膚色＋青綠像素至少這麼多，才當作真的有英雄站在上面（挑掉別的發亮東西）
# 平台是圓的，位置量的是亮光外框中心；鄰接的位置（實測 46 次點擊，正規化到英雄所在深度後）：
#   正前方  |dx| ≤ 100（多數 ±45）、dy −160~−220（一律往上一格）
#   左前／右前  |dx| 240~393、dy −60~−135
# 下面各放寬一點，兩個視窗都不含更遠的兩步之外節點（實測 dy ≤ −280 或 |dx| ≥ 500）。
PLAT_HORIZON = 48.0     # 透視：畫面 y（參考尺度）越大離鏡頭越近、間距越大，間距 ∝ (y − 48)
PLAT_DEPTH_REF = 442.0  # y=490 時縮放＝1
ADJ_AHEAD = (130.0, -285.0, -125.0)         # |dx| ≤、dy 介於（下限, 上限）
ADJ_SIDE = (190.0, 440.0, -170.0, -35.0)    # |dx| 介於、dy 介於


class Platform(NamedTuple):
    x: int                   # 平台亮光外框中心（範圍內像素）
    y: int
    w: int
    h: int
    side: Optional[str]      # LEFT／RIGHT／None（看不準）


class Cand(NamedTuple):
    kind: str
    x: int                   # 要點的位置
    y: int
    w: int
    h: int


def _blobs(mask: np.ndarray, down: int, s: float, gap: float) -> List[List[int]]:
    """遮罩的連通區塊 [x0, y0, x1, y1, 面積, 重心x, 重心y]（都是原尺寸像素）；相距不超過 gap（參考尺度）的併成一塊。
    重心＝所有亮點的平均位置：外框中心會被零星的雜點（金色塵土特效）拉歪，重心不會。
    雜訊多到不成樣（亮點太多、碎片太多）就直接放棄回空——這時候的畫面本來就不是能靠顏色認節點的畫面，
    而且碎片數量會讓合併變成災難（9/25 用純雜訊畫面壓測：兩兩比對的舊寫法跑了十分鐘）。"""
    m = mask[::down, ::down]
    if int(m.sum()) > MAX_MASK_PX:
        return []
    comps = [c for c in _components(m) if c[0].size >= 2]      # 單一亮點是雜訊
    if not comps or len(comps) > MAX_COMPONENTS:
        return []
    # 每列 [x0, y0, x1, y1, 亮點數, x總和, y總和]
    arr = np.array([[xs.min() * down, ys.min() * down, xs.max() * down, ys.max() * down,
                     ys.size, xs.sum() * down, ys.sum() * down] for ys, xs in comps], dtype=np.int64)
    g = int(gap * s)
    for _ in range(8):
        n = len(arr)
        x0, y0, x1, y1 = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        near = ((x0[:, None] - g <= x1[None, :]) & (x0[None, :] - g <= x1[:, None])
                & (y0[:, None] - g <= y1[None, :]) & (y0[None, :] - g <= y1[:, None]))
        labels = np.arange(n)
        while True:      # 連通分量：每個人取鄰居裡最小的標籤，直到不再變
            new = np.where(near, labels[None, :], n).min(axis=1)
            if (new == labels).all():
                break
            labels = new
        groups = np.unique(labels)
        if len(groups) == n:
            break
        merged = []
        for gl in groups:
            sel = arr[labels == gl]
            merged.append([sel[:, 0].min(), sel[:, 1].min(), sel[:, 2].max(), sel[:, 3].max(),
                           sel[:, 4].sum(), sel[:, 5].sum(), sel[:, 6].sum()])
        arr = np.array(merged, dtype=np.int64)          # 併過之後外框變大，可能又跟別的相接，再來一輪
    return [[int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]) * down * down, int(r[5] // r[4]), int(r[6] // r[4])]
            for r in arr]


def _wh(b: Sequence[int], s: float) -> Tuple[float, float]:
    """區塊外框換成參考尺度的 (寬, 高)。"""
    return (b[2] - b[0] + 1) / s, (b[3] - b[1] + 1) / s


def _identify_hero(img: np.ndarray, px: int, py: int, s: float) -> Tuple[bool, Optional[str]]:
    """平台上站的是誰。回 (有沒有英雄, 哪一隊)。野蠻人光著上身（大量膚色）、法師穿青綠披風。
    平台上方的窄視窗只含英雄本人（放寬到旁邊會混進怪物的膚色）；兩種顏色都很多或都不夠＝看不準（side=None）。"""
    h, w = img.shape[:2]
    y0, y1 = max(0, int(py - 105 * s)), min(h, int(py + 15 * s))
    x0, x1 = max(0, int(px - 55 * s)), min(w, int(px + 55 * s))
    a = img[y0:y1, x0:x1].astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = int(((r > 140) & (r - b > 60) & (g - b > 25) & (r - g > 15) & (r - g < 80)).sum() / s / s)
    teal = int(((r < 90) & (g > 85) & (b > 105) & (g - r > 30) & (b - g < 45)).sum() / s / s)
    if skin + teal < HERO_PIX_MIN:
        return False, None
    if skin >= 500 and teal <= 0.6 * skin:
        return True, LEFT
    if teal >= 300 and skin <= 0.5 * teal:
        return True, RIGHT
    return True, None


def find_platforms(img: np.ndarray, any_glow: bool = False) -> List[Platform]:
    """隊伍現在站的平台（發暖白色光的那一大塊）。畫面上有兩隊時可能找到兩個。"""
    h, w = img.shape[:2]
    s = h / REF_H
    a = img.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    spread = a.max(axis=2) - a.min(axis=2)
    glow = (r >= b - 3) & (r >= 120) & (g >= 100) & (spread < 50)
    glow[:int(h * 0.09)] = False
    out: List[Platform] = []
    for bb in _blobs(glow, 2, s, 6):
        bw, bh = _wh(bb, s)
        if bw < PLAT_MIN_W or bh < PLAT_MIN_H or bb[4] / s / s < PLAT_MIN_AREA:
            continue
        cx, cy = bb[5], bb[6]
        if _in_hud(cx, cy, w, h):
            continue
        is_hero, side = _identify_hero(img, cx, cy, s)
        if is_hero or any_glow:       # any_glow：英雄不是顏色規則認得的那組時，給路線比對用（見 MapPlanner._glow_for）
            out.append(Platform(cx, cy, int(bw * s), int(bh * s), side if is_hero else None))
    out.sort(key=lambda p: (p.x, p.y))
    return out


def find_candidates(img: np.ndarray, platforms: Sequence[Platform] = ()) -> List[Cand]:
    """畫面上所有「看起來像節點」的東西（不管有沒有圖釘、鄰不鄰接隊伍），種類由顏色形狀決定。"""
    h, w = img.shape[:2]
    s = h / REF_H
    a = img.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    top = int(h * 0.09)

    def cut(m: np.ndarray) -> np.ndarray:
        m[:top] = False
        return m

    ring = cut(((g - r > 45) & (g > 110) & (g - b < 50)) | ((b > 170) & (b - r > 90) & (b - g > 35) & (r < 120))
               | ((b > 120) & (r > 90) & (b - g > 60) & (r < b * 1.05) & (r > g + 25)))     # 綠／藍／紫魔法陣
    ochre = cut((r > 100) & (r < 215) & (r - b > 70) & (g - b > 45) & (r >= g) & (g >= 0.72 * r))   # 暗金（夜景裡的寶箱）
    lime = cut((g > 170) & (g - b > 60) & (g - r > 40) & (r > 60))                                   # 治療符文
    fountain = cut((b > 200) & (g > 150) & (r < 130))                                                # 泉水的白青光柱
    beam = cut((b > 110) & (r > 70) & (b - g > 50) & (r < b * 1.05) & (r > g + 15))                  # 觸發裝置的紫色光柱

    out: List[Cand] = []

    def add(kind: str, cx: int, cy: int, bw: float, bh: float) -> None:
        if cy >= h - 2 or not (0 <= cx < w) or _in_zone(cx, cy, w, h, KEEP_OUT_ZONES):
            return      # 畫面外、或落在遊戲按鈕上（右下角圖示、獎勵輪盤、左邊「f」）
        out.append(Cand(kind, int(cx), int(cy), int(bw * s), int(bh * s)))

    rings = []
    for bb in _blobs(ring, 2, s, 10):
        bw, bh = _wh(bb, s)
        if 60 <= bw <= 160 and 26 <= bh <= 100 and 1.4 <= bw / bh <= 3.0 and bb[4] / s / s >= 250:
            cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
            rings.append((cx, cy, bw * s / 2, bh * s / 2))
            add(ENEMY, cx, cy, bw, bh)
    for bb in _blobs(ochre, 2, s, 12):
        bw, bh = _wh(bb, s)
        if not (30 <= bw <= 130 and 20 <= bh <= 90 and 0.85 <= bw / bh <= 2.3 and bb[4] / s / s >= 200):
            continue
        cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
        if any(abs(cx - rx) <= rw and abs(cy - ry) <= rh for rx, ry, rw, rh in rings):
            continue        # 在怪物的魔法陣裡＝怪物身上的金色盔甲，不是寶箱
        if any(abs(cx - p.x) <= p.w / 2 + 30 * s and abs(cy - p.y) <= p.h / 2 + 60 * s for p in platforms):
            continue        # 隊伍平台上＝英雄手上的金色法杖
        add(CHEST, cx, cy, bw, bh)
    for bb in _blobs(lime, 2, s, 10):
        bw, bh = _wh(bb, s)
        if 35 <= bw <= 80 and 45 <= bh <= 95 and bb[4] / s / s >= 200:
            add(HEAL, (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2 + int(42 * s), bw, bh)    # 符文飄在平台上方，平台中心在它下方
    for bb in _blobs(fountain, 2, s, 8):
        bw, bh = _wh(bb, s)
        if 18 <= bw <= 50 and 55 <= bh <= 105 and bb[4] / s / s >= 500:
            add(HEAL, (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2 + int(44 * s), bw, bh)
    for bb in _blobs(beam, 2, s, 8):
        bw, bh = _wh(bb, s)
        if 30 <= bw <= 140 and bh >= 90 and bh / bw >= 1.2 and bb[4] / s / s >= 1500:
            add(DEVICE, (bb[0] + bb[2]) // 2, bb[3] + int(20 * s), bw, bh)                  # 點光柱底端的平台
    return out


def platform_scale(py: float, s: float) -> float:
    """平台所在深度的透視縮放（y=490 時＝1）。"""
    return max(0.35, (py / s - PLAT_HORIZON) / PLAT_DEPTH_REF)


def is_ahead(ndx: float, ndy: float) -> bool:
    """已經除掉透視縮放的位移 (ndx, ndy) 是不是「隊伍平台的鄰接節點」（正前方，或左前／右前）。"""
    ax = abs(ndx)
    if ax <= ADJ_AHEAD[0] and ADJ_AHEAD[1] <= ndy <= ADJ_AHEAD[2]:
        return True
    return ADJ_SIDE[0] <= ax <= ADJ_SIDE[1] and ADJ_SIDE[2] <= ndy <= ADJ_SIDE[3]


def stand_in_pin(x: int, y: int, img_h: int) -> Pin:
    """沒圖釘的節點湊一個假圖釘（位置就在真圖釘該在的地方），讓排序、預覽等既有程式碼不用分兩套。"""
    s = img_h / REF_H
    return Pin(x, y - int(155 * s), y - int(PIN_TO_NODE_DY * s), int(20 * s), int(50 * s), 0)


def adjacent_nodes(cands: Sequence[Cand], platforms: Sequence[Platform],
                   img_shape: Tuple[int, ...]) -> List[Tuple[Node, Platform]]:
    """挑出鄰接隊伍平台的候選，回 [(節點, 它鄰接的那個平台)]（同一個候選鄰接兩個平台時取離較近的）。"""
    h = img_shape[0]
    s = h / REF_H
    out = []
    for c in cands:
        best = None
        for p in platforms:
            sc = platform_scale(p.y, s) * s
            ndx, ndy = (c.x - p.x) / sc, (c.y - p.y) / sc
            if is_ahead(ndx, ndy):
                d = ndx * ndx + ndy * ndy
                if best is None or d < best[0]:
                    best = (d, p)
        if best is not None:
            out.append((Node(c.x, c.y, stand_in_pin(c.x, c.y, h), c.kind, 0.0, True), best[1]))
    return out


# ---------------------------------------------------------------- 路線：節點在隊伍的哪個方向（route.py 用）
# 10/01 使用者指定固定路線，每一步要分得出「正前／左前／右前」（同一格前面常有兩個同種類的節點）。
# 用 9/20 那一輪錄製 55 次點節點量的（隊伍平台→點的位置，除掉透視縮放後取角度，0°＝正右、90°＝正上）：
#   鏡頭對著隊伍（平台在畫面中間）時  正前 74~98°、左前 154~161°、右前 14~25°；
#   隊伍在畫面右邊時透視會把「正前」往左歪（#8 123°、#106 118°）；隔一格空平台的裝置／怪物落在 132°（#23）。
# 所以三個方向的範圍互相重疊一點（重疊的地方兩個方向都算），同一個方向有兩個同種節點時取離該方向中心最近的。
DIR_BANDS = {"F": (55.0, 128.0), "L": (122.0, 215.0), "R": (-35.0, 58.0)}
DIR_CENTER = {"F": 90.0, "L": 158.0, "R": 22.0}
ROUTE_ND = (110.0, 480.0)    # 選項離隊伍平台的距離（除掉透視縮放）：鄰接一格約 190~340；隔一格空平台的約 400
ROUTE_AMBIGUOUS = 12.0       # 同方向兩個同種節點，離方向中心的角度差不到這麼多＝分不出來，寧可停


class RouteOpt(NamedTuple):
    node: Node
    angle: float             # 相對隊伍平台的角度（度；0＝右、90＝正前、180＝左）
    dist: float              # 除掉透視縮放的距離


def direction_of(x: int, y: int, plat: "Platform", img_h: int) -> Tuple[float, float]:
    """(x, y) 相對隊伍平台的 (角度, 距離)。角度落在 −90°~270°（正左附近不會跳號）。"""
    s = img_h / REF_H
    sc = platform_scale(plat.y, s) * s
    ndx, ndy = (x - plat.x) / sc, (y - plat.y) / sc
    ang = math.degrees(math.atan2(-ndy, ndx))
    if ang < -90:
        ang += 360
    return ang, math.hypot(ndx, ndy)


def in_dir(angle: float, d: str) -> bool:
    lo, hi = DIR_BANDS[d]
    return lo <= angle <= hi


def route_options(nodes: Sequence[Node], plats: Sequence["Platform"], plat: "Platform", img_h: int,
                  extra: Sequence[Node] = ()) -> List[RouteOpt]:
    """這隊（站在 plat 上）現在可以選的節點：有圖釘的節點裡「離這個平台比離畫面上其他隊伍平台近」、而且在它前方的，
    再加上 extra（沒圖釘、靠節點本身樣子找到、鄰接這個平台的；跟圖釘節點重複的不算）。"""
    s = img_h / REF_H
    out: List[RouteOpt] = []

    def owner_ok(n: Node, d0: float) -> bool:
        for q in plats:
            if q is plat or (q.x == plat.x and q.y == plat.y):
                continue
            a, d = direction_of(n.x, n.y, q, img_h)
            if ROUTE_ND[0] <= d < d0 and -40 <= a <= 220:
                return False        # 離另一隊的平台更近（而且也在它前方）＝另一隊的選項
        return True

    for n in list(nodes) + [e for e in extra
                            if not any(abs(e.x - m.x) <= 70 * s and abs(e.y - m.y) <= 90 * s for m in nodes)]:
        a, d = direction_of(n.x, n.y, plat, img_h)
        if not (ROUTE_ND[0] <= d <= ROUTE_ND[1]) or not (-40 <= a <= 220):
            continue
        if not owner_ok(n, d):
            continue
        out.append(RouteOpt(n, a, d))
    return out


def match_route_step(kind: str, d: str, opts: Sequence[RouteOpt], cands: Sequence["Cand"] = (),
                     img_h: int = int(REF_H)) -> Tuple[Optional[RouteOpt], str]:
    """路線這一步（種類 kind、方向 d）在這些選項裡是哪一個。回 (選項, 說明)；找不到回 (None, 原因)。

    方向對、種類也對的才算。圖釘節點的種類是小圖分類器認的（偶爾會認錯，9/25 那輪約 3%）——
    方向對、但分類器說是別的種類（或認不得）的時候，再用「節點本身的顏色形狀」（find_candidates，沒圖釘模式用的那套）
    看一次，顏色也說是這個種類才算；兩套都說不是＝這一格真的不是路線要的東西，回 None（由呼叫端停下來叫人）。"""
    s = img_h / REF_H
    c0 = DIR_CENTER[d]
    in_band = [o for o in opts if in_dir(o.angle, d)]
    same = sorted((o for o in in_band if o.node.kind == kind), key=lambda o: abs(o.angle - c0))
    if same:
        if len(same) >= 2 and abs(abs(same[1].angle - c0) - abs(same[0].angle - c0)) < ROUTE_AMBIGUOUS:
            return None, f"{len(same)} 個{KIND_NAMES[kind]}都在這個方向，分不出是哪個"
        return same[0], ""
    for o in sorted(in_band, key=lambda o: abs(o.angle - c0)):
        n = o.node
        if any(c.kind == kind and abs(c.x - n.x) <= 70 * s and -60 * s <= c.y - n.y <= 110 * s for c in cands):
            seen = KIND_NAMES.get(n.kind, "？") if n.kind else "認不得"
            return o, f"（圖釘那格小圖認成「{seen}」，顏色看是{KIND_NAMES[kind]}）"
    if not opts:
        return None, "前面沒有看到任何節點"
    seen = "、".join(f"{_dir_word(o.angle)}{KIND_NAMES.get(o.node.kind, '？') if o.node.kind else '？'}"
                     for o in sorted(opts, key=lambda o: -o.angle))
    return None, f"前面看到的是：{seen}"


def _dir_word(angle: float) -> str:
    names = {"L": "左前", "F": "正前", "R": "右前"}
    return names[min(DIR_CENTER, key=lambda k: abs(angle - DIR_CENTER[k]))]


# ---------------------------------------------------------------- 挑節點

def other_side(side: str) -> str:
    return RIGHT if side == LEFT else LEFT


def choose(nodes: Sequence[Node], priority: Sequence[str] = DEFAULT_PRIORITY) -> Optional[Node]:
    """依優先序挑一個。同一層有好幾個時取畫面最上面的（關卡一路往上，你 27 次多選一裡有 19 次也是選較上面的）。
    認不得種類的節點不會被挑。沒得挑回 None。"""
    rank = {k: i for i, k in enumerate(priority)}
    known = [n for n in nodes if n.kind in rank]
    if not known:
        return None
    return min(known, key=lambda n: (rank[n.kind], n.pin.bottom, n.x))


def choose_for_teams(nodes: Sequence[Node], priority: Sequence[str], sides: Dict[Tuple[int, int], Optional[str]],
                     fights: Dict[str, int]) -> Tuple[Optional[Node], str]:
    """挑節點＋治療閘門。回傳 (節點, 給人看的補充說明)。

    使用者規則：寶箱、治療為主（治療排在怪物前面）；治療兩隊同時回血，所以選治療之前要先確保**另一隊**也打過
    （自上次治療以來至少一場），治療才最大化。

    治療是「多選一」的其中一個選項，選了旁邊的怪物，這條路的治療就沒了。所以治療還沒輪到時**不能降級去點同一個岔路的怪物**，
    要改成讓另一隊先前進（點另一隊的怪物，讓它打一場），這一隊的治療留著等。
    另一隊沒有怪物可打（或分不出隊）時，就直接點治療，不能為了等而把治療放掉。"""
    best = choose(nodes, priority)
    if best is None or best.kind != HEAL:
        return best, ""
    side = sides.get((best.x, best.y))
    if side is None:
        return best, ""
    other = other_side(side)
    if fights.get(other, 0) >= 1:
        return best, ""
    rivals = [n for n in nodes if n.kind == ENEMY and sides.get((n.x, n.y)) == other]
    alt = choose(rivals, priority)
    if alt is not None:
        return alt, f"（{SIDE_NAMES[side]}的治療先留著：{SIDE_NAMES[other]}還沒打過，先讓{SIDE_NAMES[other]}打）"
    return best, f"（{SIDE_NAMES[other]}沒有怪物可先打，治療照點）"


def describe_nodes(nodes: Sequence[Node]) -> str:
    if not nodes:
        return "沒看到節點圖釘"
    names = [KIND_NAMES.get(n.kind, "？") if n.kind else "？" for n in nodes]
    return f"看到 {len(nodes)} 個節點：" + "、".join(names)


# ---------------------------------------------------------------- 規劃器

# 找不到圖釘、也找不到隊伍前面的節點時拖地圖的走法（範圍比例；每步是「從哪裡拖到哪裡」）。
# 9/25 使用者回報：左隊一路打到卡住之後，畫面該「回頭往下看」去找被拋在後面的另一隊（右隊），
# 之前的走法只有往上、往左、往右——從來沒往下看過。所以現在**先往下看**，再回頭往上看，最後左右看。
# **9/24 在使用者的真遊戲上實機校準過**（BlueStacks 範圍 1475×834、拖 0.5 秒 25 步）：
#   * 手指往下拖 250px（0.30 高）→ 地圖往下跑、上方進畫面（新圖釘冒出來）；3D 透視所以近處移得比手指多
#     （野蠻人 +290px）、遠處移得少（法師 +150px）；反向拖回去視角還原（誤差 ≤ 6px）。
#   * 手指往右拖 369px（0.25 寬）→ 近處內容右移 ~490px（1.33 倍）、右邊的英雄被推出畫面；反向拖回還原（誤差 18px）。
#   * 拖完 0.35 秒與 1.75 秒的位置幾乎一樣＝沒有慣性，等 ~1.5 秒就夠。
LOOK_DOWN = (0.56, 0.72, 0.56, 0.42)     # 手指往上拖 0.30 高 → 內容往上跑 → 看到「下方」（被拋在後面的一隊）
LOOK_UP = (0.56, 0.42, 0.56, 0.72)       # 手指往下拖 → 看到「上方」（關卡一路往上）
LOOK_LEFT = (0.30, 0.55, 0.55, 0.55)     # 手指往右拖 → 看到左邊
LOOK_RIGHT = (0.55, 0.55, 0.30, 0.55)    # 手指往左拖 → 看到右邊
# 9/27 使用者回報：「右邊一路往上推進、到沒有可以前進的時候，不會自動回頭去執行左邊」。
# 9/20 那一輪錄製裡這件事發生 3 次（右隊先點到觸發裝置：第 23、87、133 次點擊），你 3 次都是**往左下斜拖兩次**
# 就找到左隊（右隊從畫面中間被推到右上角，左隊出現在畫面中下方）。兩隊各走左右兩條路，永遠隔著約 0.4 個畫面寬，
# 所以只往正下方看，左隊會一直貼在畫面最左邊（節點被切掉、或落在左邊按鈕的禁區）認不出來——9/25 那輪就是連拖 12 次沒找到。
LOOK_DOWN_LEFT = (0.40, 0.72, 0.58, 0.42)    # 手指往右上拖 → 看左下（你的拖法：內容每次往右上跑約 (+0.18 寬, −0.25 高)）
LOOK_DOWN_RIGHT = (0.60, 0.72, 0.42, 0.42)   # 手指往左上拖 → 看右下（起點 x 0.60：再右就壓到右下角「英靈」圖示）
DEFAULT_SCAN_MOVES: Tuple[Tuple[float, float, float, float], ...] = (
    LOOK_DOWN, LOOK_DOWN, LOOK_DOWN, LOOK_DOWN,                      # 1~4：先往下看（另一隊多半被拋在下面）
    LOOK_UP, LOOK_UP, LOOK_UP, LOOK_UP, LOOK_UP, LOOK_UP, LOOK_UP, LOOK_UP,   # 5~12：回頭往上，過了原點再往上看 4 步
    LOOK_DOWN, LOOK_DOWN, LOOK_DOWN, LOOK_DOWN,                      # 13~16：回到原點
    LOOK_LEFT, LOOK_RIGHT, LOOK_RIGHT, LOOK_LEFT,                    # 17~20：左右看，最後回到中間
)
MOTION_MAX = 0.03      # 相鄰兩輪畫面「變化像素比例」超過這個＝鏡頭在動。靜止的地圖實測中位 0.003、最大 0.004
                       # （只有圖釘浮動與角色待機動畫），拖曳中遠大於這個
STAND = (0.50, 0.495)  # 鏡頭跟著隊伍時隊伍平台在畫面上的位置（範圍比例）：9/20 錄製每次行動完都停在 (725, 405)±20（範圍 1450×818）
CENTER_BOX = (0.15, 0.15)   # 平台離 STAND 這麼近（寬、高的比例）就算「鏡頭正對著它」
MAX_CENTER_DRAGS = 2        # 一輪搜尋裡最多幾次「把找到的隊伍拖到畫面中間」
MAX_REANCHOR = 1            # 一次總覽裡最多幾次「拖完鏡頭又跳回剛行動那隊 → 重新從它開始找」（11/24）
DRAG_GAIN = 1.15            # 拖地圖時內容移動÷手指移動（推算剛行動那隊被拖到哪裡用）
STILL_MAX = 3               # 連續幾次拖了畫面都沒動就停手（模擬器沒收到拖曳；到地圖邊只會卡一個方向，換方向就會動）


def scan_legs(target: Optional[str]) -> List[Tuple[Tuple[float, float, float, float], int]]:
    """沒有可點的節點時，拖地圖的走法：[(一步怎麼拖, 最多幾步)]，一段一段走；某一段拖了畫面沒動（到地圖邊）就提早換下一段。

    target＝要找的那一隊（另一隊在等它，或畫面上只看得到另一隊）：
      1. 往它那一側斜拖 2 次（你 9/20 找左隊的拖法，3 次都是兩下就找到）
      2. 再往下 4 次（落後很多的時候；地圖到底就提早結束）
      3. 回頭往上 9 次（它其實在上面／同一層）
      4. 萬一隊伍認反了：往另一側斜拖 4 次、再往下 4 次
    target 是 None（看不出該找哪隊）：跟以前一樣——先往下、再往上、最後左右。"""
    if target is None:
        return [(LOOK_DOWN, 4), (LOOK_UP, 8), (LOOK_DOWN, 4), (LOOK_LEFT, 1), (LOOK_RIGHT, 2), (LOOK_LEFT, 1)]
    toward, away = (LOOK_DOWN_LEFT, LOOK_DOWN_RIGHT) if target == LEFT else (LOOK_DOWN_RIGHT, LOOK_DOWN_LEFT)
    return [(toward, 2), (LOOK_DOWN, 4), (LOOK_UP, 9), (away, 4), (LOOK_DOWN, 4)]


DEFAULT_MAX_SCAN_DRAGS = max(sum(n for _, n in scan_legs(LEFT)), len(DEFAULT_SCAN_MOVES)) + MAX_CENTER_DRAGS

# ---- 換邊大步快拖（使用者 11/22：「一邊的路線走完一個階段之後，要回去另外一邊的速度、幅度都可以加大、加快」）----
# 11/08 那輪真實記錄：4 次換邊各拖了 4~6 下、每下約 3.2 秒（一次 12~20 秒），第 4 段左隊點完最後的裝置後連拖 23 下
# 都沒找到右隊（右隊在更右邊、往右下斜拖兩下還不夠右，之後只往正下／正上找）→ 你手動接手。
# 換邊時：每一步拖得比較遠（上下放大 switch_scale 倍、左右只放大它的平方根——兩隊左右距離大致固定，左右放太大會一下拖過頭），
# 拖完不用等 2.5 秒（engine 改用 switch_interval），而且記住這個換邊點上次總共拖了多遠（route.SwitchMemory），
# 下一輪同一個地方直接照那個距離一次拖過去（地圖固定、鏡頭每次都停在剛行動那隊身上，同樣的拖法會看到同樣的畫面）。
DRAG_AREA = (0.24, 0.24, 0.76, 0.80)     # 大步拖的起訖點都要在這個框裡（避開上面標題列、左邊「f」、下面兩排圖示）
MAX_DRAG = (DRAG_AREA[2] - DRAG_AREA[0], DRAG_AREA[3] - DRAG_AREA[1])
_DRAG_CENTERS = ((0.50, 0.52), (0.46, 0.52), (0.54, 0.52), (0.42, 0.52), (0.58, 0.52), (0.50, 0.48), (0.46, 0.48))


def place_drag(dx: float, dy: float) -> Tuple[float, float, float, float]:
    """手指要拖 (dx, dy)（範圍比例）→ 從哪拖到哪：太長就縮到 MAX_DRAG 內，起訖點避開遊戲按鈕（HUD_ZONES、KEEP_OUT_ZONES）。
    地圖平移只看手指移動多少、跟從哪裡開始拖無關（9/24 校準：拖過去再拖回來，誤差 ≤ 6~18px），所以可以挑安全的位置拖。"""
    f = min(1.0, MAX_DRAG[0] / abs(dx) if dx else 1.0, MAX_DRAG[1] / abs(dy) if dy else 1.0)
    dx, dy = dx * f, dy * f
    for cx, cy in _DRAG_CENTERS:
        pts = ((cx - dx / 2, cy - dy / 2), (cx + dx / 2, cy + dy / 2))
        if all(DRAG_AREA[0] <= x <= DRAG_AREA[2] and DRAG_AREA[1] <= y <= DRAG_AREA[3]
               and not _in_zone(x, y, 1, 1, KEEP_OUT_ZONES) for x, y in pts):
            return (round(pts[0][0], 4), round(pts[0][1], 4), round(pts[1][0], 4), round(pts[1][1], 4))
    cx, cy = 0.46, 0.52        # 理論上到不了（MAX_DRAG 內的拖法在 0.46 附近都放得下），保險用
    return (round(cx - dx / 2, 4), round(cy - dy / 2, 4), round(cx + dx / 2, 4), round(cy + dy / 2, 4))


def big_move(move: Sequence[float], scale: float) -> Tuple[float, float, float, float]:
    """把一步校準過的拖法放大：上下 ×scale、左右 ×√scale。"""
    x0, y0, x1, y1 = move
    return place_drag((x1 - x0) * scale ** 0.5, (y1 - y0) * scale)


def fast_scan_legs(target: str, scale: float) -> List[Tuple[Tuple[float, float, float, float], int]]:
    """換邊找另一隊的大步走法：往它那側斜拖 2 大步 → 往下 4 → 回頭往上 8 → 萬一認反往另一側斜拖 3 → 往下 3。
    （步數比 scan_legs 少，因為每一步走得比較遠；加上拖到中間最多 2 次，仍在 scan_max_drags 25 以內。）"""
    toward, away = (LOOK_DOWN_LEFT, LOOK_DOWN_RIGHT) if target == LEFT else (LOOK_DOWN_RIGHT, LOOK_DOWN_LEFT)
    return [(big_move(toward, scale), 2), (big_move(LOOK_DOWN, scale), 4), (big_move(LOOK_UP, scale), 8),
            (big_move(away, scale), 3), (big_move(LOOK_DOWN, scale), 3)]


def replay_moves(dx: float, dy: float) -> List[Tuple[float, float, float, float]]:
    """上次這個換邊點總共拖了 (dx, dy)：拆成最少幾下（每下不超過 MAX_DRAG）一口氣拖過去。"""
    n = max(1, int(np.ceil(max(abs(dx) / MAX_DRAG[0], abs(dy) / MAX_DRAG[1]) - 1e-9)))
    return [place_drag(dx / n, dy / n) for _ in range(n)]


def look_name(move: Sequence[float]) -> str:
    """拖曳走法 → 「鏡頭往哪看」的說明（手指往上拖＝內容往上跑＝看下方；斜著拖＝看左下／右下…）。"""
    x0, y0, x1, y1 = move
    dx, dy = x1 - x0, y1 - y0
    vert = "下" if dy < 0 else "上"
    horiz = "左" if dx > 0 else "右"
    if abs(dx) >= 0.4 * abs(dy) and abs(dy) >= 0.4 * abs(dx):
        return f"往{horiz}{vert}看"
    if abs(dy) >= abs(dx):
        return f"往{vert}看"
    return f"往{horiz}看"


class MapPlanner:
    """看地圖 → 決定下一步（點哪個節點／拖地圖／等／停）。時間由呼叫端傳進來，方便測試。

    圖釘「閃爍」＝上下浮動（9/24 實機連拍 8 秒 219 幀：4 個圖釘每一幀都在、只是底端上下動 23~27px、週期約 1.5 秒，
    不會熄滅），所以不能拿圖釘的 y 判斷穩不穩。規劃器替每個圖釘記一筆「軌跡」（左右位置＋最後看到的時間），
    仍保留短期記憶當保險（被遮住／偵測漏掉一兩輪不算沒圖釘）：
      * 最近 fresh_seconds 內看到過的才算「現在有」，可以點；
      * 全部圖釘都超過 no_pin_seconds 沒看到，才算「真的沒有圖釘」，才會去拖地圖。
    鏡頭有沒有在動，看「整張畫面相鄰兩輪的變化像素比例」（靜止實測 ≤ 0.004、拖曳中遠大於 MOTION_MAX）。
    左右隊：用 HeroFinder 找英雄判斷節點屬於哪隊（有治療可選、以及真的要點的時候才跑，約 0.35 秒，結果暫存 1.2 秒）；
    點下去的節點記下屬於哪隊，等到勝利畫面出現就算那隊打過一場。治療排在怪物前面，但要「另一隊」自上次治療以來
    打過至少一場才輪到；沒輪到時留著這一隊的治療、改讓另一隊先打（見 choose_for_teams），治療一點下去兩隊的計數歸零。
    其他內部狀態都是為了「不對著同一個地方空點、也不會一直卡著不動」：
      * 每個節點點過幾次（點了兩次畫面還在總覽 → 這個節點先跳過）
      * 連續拖了幾次地圖都沒看到圖釘
      * 已經連續「等」多久（超過 stuck_timeout 就停手，別讓使用者以為它還在工作）
    沒有能點的節點時拖地圖「找另一隊」（9/27）：先判斷要找哪一隊（誰在等誰），往那一隊的方向斜著找（scan_legs）；
    看到那隊但它貼在畫面邊邊就先把它拖到中間；拖了畫面沒動＝到地圖邊，換下一段走法。
    """

    def __init__(self, classifier: NodeClassifier, priority: Sequence[str] = DEFAULT_PRIORITY,
                 scan_moves: Optional[Sequence[Sequence[float]]] = None,
                 max_scan_drags: int = DEFAULT_MAX_SCAN_DRAGS, max_attempts: int = 2, skip_seconds: float = 60.0,
                 stuck_timeout: float = 60.0, fresh_seconds: float = 1.2, no_pin_seconds: float = 2.0,
                 rescan_seconds: float = 1.5, hero_finder: Optional[HeroFinder] = None,
                 hero_ttl: float = 1.2, unpinned: bool = True, route=None,
                 route_miss_seconds: float = 4.0, switch_scale: float = 1.0, fast_rescan: float = 0.6,
                 memory=None) -> None:
        self.classifier = classifier
        # 換邊大步快拖（11/22）：switch_scale ≤ 1＝關掉（舊走法、舊速度）；memory＝route.SwitchMemory（記住每個換邊點拖多遠），None＝不記
        self.switch_scale = float(switch_scale or 1.0)
        self.fast_rescan = fast_rescan
        self.memory = memory
        self.heroes = hero_finder
        # 固定路線（route.RouteTracker；None＝不照路線）。走完或關掉之後就是原本的規則
        self.route = route
        self.route_miss_seconds = route_miss_seconds   # 隊伍在畫面上、卻找不到路線要的那個節點，等這麼久還是沒有就停手叫人
        self.hero_ttl = hero_ttl
        self.priority = tuple(priority)
        # scan_moves 沒給＝依「要找哪一隊」決定走法（scan_legs）；給了＝照這串固定走法一步一步拖（設定檔可以自訂）
        self.scan_moves = [tuple(m) for m in scan_moves] if scan_moves else None
        self.max_scan_drags = max_scan_drags
        self.max_attempts = max_attempts
        self.skip_seconds = skip_seconds
        self.stuck_timeout = stuck_timeout
        self.fresh_seconds = fresh_seconds
        self.no_pin_seconds = no_pin_seconds
        self.rescan_seconds = rescan_seconds
        self.unpinned = unpinned      # 沒圖釘時是否改找隊伍前面的節點（測試／出問題時可以關）
        self.reset()

    def reset(self) -> None:
        self._tracks: List[dict] = []                # [{"node": Node, "seen": 時間}]
        self._attempts: List[List[float]] = []       # [x, y, 次數, 最後一次的時間]
        self._idle_since: Optional[float] = None
        self._no_pin_since: Optional[float] = None
        self._prev_small: Optional[np.ndarray] = None
        self._fights = {LEFT: 0, RIGHT: 0}           # 自上次治療以來各隊打過幾場（勝利畫面出現才算）
        self._pending: Optional[str] = None          # 剛點的節點屬於哪隊；勝利畫面出現時記到那隊頭上
        self._now = 0.0
        self._img: Optional[np.ndarray] = None
        self._hero_cache: Optional[Tuple[float, Dict[str, Hero]]] = None
        self.sides: Dict[Tuple[int, int], Optional[str]] = {}   # 最近一次判斷的 (x, y) → 隊；沒判斷過（沒有治療可選）就是空的
        self._stuck_side: Optional[str] = None       # 點了「觸發裝置」的那隊：雙邊觸發要等另一隊，它自己已經沒事可做
        self._stuck_check = False                    # 剛點了裝置：回到總覽、鏡頭停下來後，用畫面正中間那隊再確認一次是誰在等
        self._scene_cache: Optional[Tuple[float, list, list]] = None    # (時間, 隊伍平台, 候選節點)，找一次要 ~0.1 秒
        self.extra_overlay: List[tuple] = []         # 沒圖釘模式下給預覽畫的標記 [(x, y, 名稱, 是不是下一個要點的)]
        self._frames = 0                             # 這次進總覽後看了幾張畫面（第一張沒得比，判斷不了鏡頭有沒有在動）
        self._miss_since: Optional[float] = None     # 路線：隊伍在畫面上但找不到要點的節點，從什麼時候開始
        self._stones: set = set()                    # 路線：已經試過「先點中間那格空平台」的 (隊, 第幾步)
        self._walk_since: Optional[float] = None     # 路線：點了「空格」的時間（不跳彈窗，鏡頭跟著隊伍動了才算走過去）
        self._fresh_actor: Optional[str] = None      # 路線：剛走完一步的隊（回到總覽時鏡頭對著它）；開自動／拖過地圖就不算
        self._actor_at: Optional[List[float]] = None  # 剛走完一步那隊現在在畫面上的推算位置（拖地圖時跟著手指位移推算）
        self.route_event: Optional[str] = None       # 路線：規劃器自己確認走完的那一步（空格），給 engine 寫進動作記錄
        self._retry_clicked = False                  # 點了「戰敗後重打」（重打的隊記在 route.retry，存檔）
        self._reanchor_n = 0                         # 這次總覽「鏡頭跳回剛行動那隊、重新找」做過幾次（_snapped_back）
        self._reset_scan()

    def _reset_scan(self) -> None:
        """一輪「拖地圖找另一隊」的進度。畫面離開總覽（點的節點有反應）或重新開自動才重來；點了沒反應不算。"""
        self._drags = 0
        self._legs: Optional[List[Tuple[Tuple[float, ...], int]]] = None    # 這一輪的走法；None＝還沒開始找
        self._leg = 0                                # 走到第幾段
        self._leg_n = 0                              # 這一段已經拖了幾步
        self._last_leg: Optional[int] = None         # 上一步是哪一段的（拖了沒動就跳過那一段剩下的步數）
        self._target: Optional[str] = None           # 這一輪要找哪一隊（None＝看不出來）
        self._center_n = 0                           # 這一輪「把隊伍拖到中間」做過幾次
        self._pre_drag: Optional[np.ndarray] = None  # 拖之前那張畫面的縮圖（拖完比一比有沒有動）
        self._pre_tag = ""
        self._still = 0                              # 連續幾次拖了畫面都沒動
        self._edge_note = ""
        self._fast = False                           # 這一輪是「換邊找另一隊」：大步快拖
        self._switch_key: Optional[str] = None       # 這個換邊點的代號（記憶用，見 _switch_key_for）；None＝不記
        self._net = [0.0, 0.0]                       # 這一輪總共拖了多遠（手指位移，範圍比例）
        self._replay_n = 0                           # 走法最前面有幾步是「照上次記住的距離」

    @property
    def switching(self) -> bool:
        """正在換邊找另一隊（engine 用來縮短動作間隔：拖完、找到了要點，都不用等 map_interval）。"""
        return self._fast and self._legs is not None

    def _switch_key_for(self, target: str) -> Optional[str]:
        """換邊點的代號＝「鏡頭對著哪隊、它走到第幾步 → 要找哪隊、它走到第幾步」。地圖固定、鏡頭停在剛行動那隊身上，
        同一個代號每一輪看到的畫面都一樣，所以上次拖多遠這次照拖就對。只在照路線走、確定鏡頭對著誰的時候才有。"""
        r = self.route
        actor = self._fresh_actor
        if r is None or not r.active or actor is None or actor == target or self._drags:
            return None
        return f"{actor}{r.idx.get(actor, 0)}>{target}{r.idx.get(target, 0)}"

    def _remember_switch(self, side: Optional[str]) -> None:
        """換邊找到了（要找的那隊照路線走出一步）：記下這一輪總共拖了多遠，下一輪同一個換邊點直接拖過去。"""
        if (self.memory is not None and self._switch_key and side == self._target and self._drags
                and (abs(self._net[0]) > 1e-3 or abs(self._net[1]) > 1e-3)):
            self.memory.put(self._switch_key, self._net[0], self._net[1])

    def left_overview(self) -> None:
        """畫面離開過總覽（進了彈窗／戰鬥）＝上一次點擊有效，重置「點了沒反應」的記錄。
        各隊的戰鬥計數、還有「哪一隊在等另一隊」要跨過總覽保留（它們就是為了跨好幾輪總覽累積的）。"""
        if self.route is not None:
            self._remember_switch(self.route.last_actor)
        if self._retry_clicked and self.route is not None:     # 點了重打、畫面離開總覽＝重打開始了
            self.route.set_retry(None)
        fights, pending, stuck, check = self._fights, self._pending, self._stuck_side, self._stuck_check
        self.reset()
        self._fights, self._pending, self._stuck_side, self._stuck_check = fights, pending, stuck, check
        if self.route is not None:
            self._fresh_actor = self.route.last_actor    # engine 先 route.confirm() 再叫這裡：剛走完的那隊

    def new_stage(self) -> None:
        """進了下一階段（提示框確定／五選一／獎勵輪盤出現）：兩隊都有新路可走，不再有誰在等誰。"""
        self._stuck_side = None
        self._stuck_check = False
        self._attempts = []

    # -- 左右隊 --
    @property
    def fights(self) -> Dict[str, int]:
        return dict(self._fights)

    @property
    def drags(self) -> int:
        """這一輪找另一隊已經拖了幾次地圖。"""
        return self._drags

    def _heroes_in(self, img: np.ndarray) -> Dict[str, Hero]:
        """英雄偵測要 0.35 秒，鏡頭靜止時英雄不會動，所以暫存 hero_ttl 秒；拖地圖後會作廢。"""
        if self.heroes is None or not self.heroes.ready:
            return {}
        if self._hero_cache is not None and self._now - self._hero_cache[0] <= self.hero_ttl:
            return self._hero_cache[1]
        found = self.heroes.find(img)
        self._hero_cache = (self._now, found)
        return found

    def side_for(self, node: Node, img: np.ndarray) -> Optional[str]:
        """這個節點屬於哪隊（英雄偵測結果暫存中就不重跑）。"""
        return side_of(node, self._heroes_in(img), img.shape[1])

    def note_victory(self) -> Optional[str]:
        """看到勝利畫面：剛點的那個節點所屬的隊伍記一場。回傳記到哪隊（沒有就 None）。只記一次。"""
        side, self._pending = self._pending, None
        if side in self._fights:
            self._fights[side] += 1
            return side
        return None

    # -- 看 --
    def _track_for(self, n: Node, s: float) -> Optional[dict]:
        best, best_d = None, None
        for t in self._tracks:
            dx = abs(t["node"].pin.cx - n.pin.cx)
            dy = abs(t["node"].pin.bottom - n.pin.bottom)
            if dx <= 10 * s and dy <= 70 * s:
                d = dx + dy
                if best is None or d < best_d:
                    best, best_d = t, d
        return best

    def observe(self, img: np.ndarray, now: float = 0.0) -> MapView:
        self._now, self._img = now, img
        self._frames += 1
        h, w = img.shape[:2]
        s = h / REF_H
        clean = pale_fraction(img) <= PALE_MAX and not is_dimmed(img)
        current: List[Node] = []
        if clean:
            for p in find_pins(img):
                x, y = node_point(p, h)
                if y >= h - 2 or x < 0 or x >= w or _in_zone(x, y, w, h, KEEP_OUT_ZONES):
                    continue        # 點位落在畫面外或遊戲按鈕上（右下角圖示、獎勵輪盤…），不點
                kind, dist = self.classifier.classify(node_patch(img, x, y))
                current.append(Node(x, y, p, kind, dist))
        moved = False
        # 整張畫面相鄰兩輪的變化：鏡頭在動（拖曳、走路運鏡）時遠大於靜止時的動畫（圖釘浮動、角色待機）
        small = img[int(h * 0.10):int(h * 0.85):4, int(w * 0.05):int(w * 0.95):4].mean(axis=2).astype(np.float32)
        prev, self._prev_small = self._prev_small, small
        if prev is not None and prev.shape == small.shape:
            if float((np.abs(small - prev) > 20).mean()) > MOTION_MAX:
                moved = True
        for n in current:
            t = self._track_for(n, s)
            if t is None:
                self._tracks.append({"node": n, "seen": now})
                moved = True                 # 新冒出來的圖釘（第一次看到／鏡頭移動帶進來的）
            else:
                if abs(t["node"].pin.bottom - n.pin.bottom) > 45 * s:
                    moved = True             # 上下跳超過浮動幅度＝地圖在動
                t["node"], t["seen"] = n, now
        self._tracks = [t for t in self._tracks if now - t["seen"] <= self.no_pin_seconds]
        fresh = [t["node"] for t in self._tracks if now - t["seen"] <= self.fresh_seconds]
        if self._walk_since is not None:
            self._check_walk(moved and clean, now)
        return MapView(fresh, len(self._tracks) - len(fresh), clean, not moved)

    def _check_walk(self, moved: bool, now: float) -> None:
        """點了路線的「空格」之後：鏡頭跟著隊伍動了＝走過去了，這一步算完（不會有彈窗讓 engine 確認）。
        5 秒都沒動＝沒走過去，交給下一輪重點（點的次數有記，點兩次都不動就停手）。"""
        r = self.route
        if r is None or r.pending is None:
            self._walk_since = None
            return
        if moved and now - self._walk_since >= 0.15:
            self._walk_since = None
            done = r.confirm()
            if done is not None:
                side, st = done
                self.route_event = f"路線：{st.name(side)}（{st.what()}）走過去了"
                self._attempts = []       # 走過去之後下一個節點會出現在同一個畫面位置，別把剛剛那一下算到它頭上
                self._remember_switch(side)
                self._fresh_actor = side
                self._drags = 0
                self._reset_scan()
        elif now - self._walk_since > 5.0:
            self._walk_since = None

    # -- 想 --
    def _attempt(self, n: Node, s: float) -> Optional[List[float]]:
        for a in self._attempts:
            if abs(a[0] - n.x) <= 40 * s and abs(a[1] - n.y) <= 40 * s:      # 圖釘會飄，點位誤差要放寬
                return a
        return None

    def decide(self, view: MapView, img_shape: Tuple[int, ...], now: float) -> MapPlan:
        plan = self._decide(view, img_shape, now)
        if plan.kind == "wait":
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since > self.stuck_timeout:
                return MapPlan("stop", f"總覽停了 {self.stuck_timeout:.0f} 秒沒有動作（{plan.reason}）。請看一下畫面。")
        elif plan.kind != "stop":
            self._idle_since = None
        return plan

    def _decide(self, view: MapView, img_shape: Tuple[int, ...], now: float) -> MapPlan:
        h, w = img_shape[:2]
        s = h / REF_H
        self.extra_overlay = []
        if self.route is not None and self.route.active:
            return self._decide_route(view, img_shape, now)
        if not view.clean:
            return MapPlan("wait", "畫面上疊著彈窗或被壓暗，先不動")
        if self._stuck_check and view.stable and self._frames >= 2 and self._img is not None:
            self._confirm_stuck(w, h)
        if view.nodes:
            self._no_pin_since = None
            if not view.stable:
                return MapPlan("wait", "地圖還在移動，等它停下來")
            usable = []
            for n in view.nodes:
                a = self._attempt(n, s)
                if a is not None and a[2] >= self.max_attempts and now - a[3] < self.skip_seconds:
                    continue     # 點了好幾次都沒反應，先放著
                usable.append(n)
            self.sides = {}
            img = self._img
            if img is not None and any(n.kind == HEAL for n in usable):
                heroes = self._heroes_in(img)
                self.sides = {(n.x, n.y): side_of_sure(n, heroes, w) for n in usable}
            pick, why = choose_for_teams(usable, self.priority, self.sides, self._fights)
            if pick is not None:
                return MapPlan("click", f"點「{KIND_NAMES[pick.kind]}」節點{why}", pick.x, pick.y, node=pick)
            # 有圖釘，但都認不得種類或都點過沒反應：這種要人來（等太久由 decide 收尾）
            return MapPlan("wait", f"{describe_nodes(view.nodes)}，但沒有可以自動選的（認不得種類，或點了都沒反應）——請你自己點")

        if view.hidden:
            return MapPlan("wait", "圖釘暫時看不到（閃爍或被遮住），等一下再看")

        # 完全沒圖釘：先確認「一直沒有」（不是閃爍）
        if self._no_pin_since is None:
            self._no_pin_since = now
        need = self.no_pin_seconds if self._drags == 0 else (self.fast_rescan if self._fast else self.rescan_seconds)
        if now - self._no_pin_since < need:
            return MapPlan("wait", "沒看到圖釘，再確認一下不是閃爍")
        # 遊戲有時本來就不顯示圖釘（使用者 9/25），但隊伍前面的節點照樣能點：改找隊伍腳邊的節點
        note = ""
        plats: List[Platform] = []
        if self.unpinned and self._img is not None:
            if not view.stable:
                return MapPlan("wait", "地圖還在移動，等它停下來")
            plan, note = self._plan_unpinned(img_shape, now)
            if plan is not None:
                return plan
            plats = self._scene(self._img, now)[0]
        # 這個畫面沒有可點的：拖地圖去找另一隊
        return self._scan(plats, w, h, note)

    # -- 拖地圖找另一隊 --
    def _centered(self, plats: Sequence[Platform], w: int, h: int) -> Optional[Platform]:
        """鏡頭正對著的那個隊伍平台（鏡頭平常跟著剛行動的那隊，停在畫面中間偏下一點；見 STAND）。"""
        sx, sy = STAND[0] * w, STAND[1] * h
        near = [p for p in plats if abs(p.x - sx) <= CENTER_BOX[0] * w and abs(p.y - sy) <= CENTER_BOX[1] * h]
        return min(near, key=lambda p: (p.x - sx) ** 2 + (p.y - sy) ** 2) if near else None

    def _confirm_stuck(self, w: int, h: int) -> None:
        """點了觸發裝置、回到總覽之後：那隊走到裝置上，鏡頭會跟著它停在畫面中間——用它腳下平台的顏色再確認一次是誰在等。
        點下去那一刻的判斷（裝置鄰接哪個平台）在兩隊都入鏡時可能分錯；這裡看不準（中間沒有平台／認不出隊伍）就維持原判斷。"""
        self._stuck_check = False
        plats, _ = self._scene(self._img, self._now)
        c = self._centered(plats, w, h)
        if c is not None and c.side is not None:
            self._stuck_side = c.side

    def _guess_target(self, plats: Sequence[Platform], w: int, h: int) -> Optional[str]:
        """這一輪要找哪一隊：另一隊在等它就找它；不然看鏡頭正對著哪一隊（它前面沒東西可點，另一隊才有事做）；
        畫面上只看得到一隊也一樣；都看不出來回 None。"""
        if self._stuck_side is not None:
            return other_side(self._stuck_side)
        c = self._centered(plats, w, h)
        if c is not None and c.side is not None:
            return other_side(c.side)
        sides = {p.side for p in plats if p.side is not None}
        if len(sides) == 1:
            return other_side(next(iter(sides)))
        return None

    def _check_edge(self) -> None:
        """上一步拖完，比一比拖之前跟現在的畫面：幾乎沒變＝鏡頭沒動（到地圖邊了，或模擬器沒收到拖曳）。
        照走法拖的那一步沒動，就把這一段剩下的同方向步數跳過，換下一段。"""
        before, self._pre_drag = self._pre_drag, None
        cur = self._prev_small
        if before is None or cur is None:
            return
        moved = before.shape != cur.shape or float((np.abs(cur - before) > 20).mean()) > MOTION_MAX
        if moved:
            self._still = 0
            return
        self._still += 1
        if self._pre_tag == "scan" and self._legs is not None and self._last_leg == self._leg and self._leg < len(self._legs):
            self._leg += 1
            self._leg_n = 0
            self._edge_note = "（上一步拖了畫面沒動＝到地圖邊了，換方向）"

    def _center_plan(self, plats: Sequence[Platform], w: int, h: int) -> Optional[MapPlan]:
        """看到要找的那隊了、但它貼在畫面邊邊（前面的節點被切掉或壓在按鈕禁區）：把它拖到畫面中間再看一次。"""
        if self._target is None or self._center_n >= MAX_CENTER_DRAGS:
            return None
        mine = [p for p in plats if p.side == self._target]
        if not mine:
            return None
        sx, sy = STAND[0] * w, STAND[1] * h
        p = min(mine, key=lambda q: (q.x - sx) ** 2 + (q.y - sy) ** 2)
        dx, dy = sx - p.x, sy - p.y
        if abs(dx) <= CENTER_BOX[0] * w and abs(dy) <= CENTER_BOX[1] * h:
            return None          # 已經在中間了還是沒有能點的：繼續照走法找（別對著同一個畫面來回拖）
        # 手指拖多遠內容就跟著跑多遠（透視下近處多一點）；一次最多拖跟校準過的走法一樣遠，起訖點都留在畫面中央一帶
        dx = float(np.clip(dx, -0.25 * w, 0.25 * w))
        dy = float(np.clip(dy, -0.30 * h, 0.30 * h))
        cx, cy = 0.48 * w, 0.55 * h
        return MapPlan("drag", f"看到{SIDE_NAMES[self._target]}在畫面邊邊、前面的節點看不全，先把它拖到中間",
                       int(round(cx - dx / 2)), int(round(cy - dy / 2)), int(round(cx + dx / 2)), int(round(cy + dy / 2)),
                       tag="center")

    def _is_switch(self, target: Optional[str]) -> bool:
        """這一輪搜尋是不是「換邊」：要找的是另一隊（照路線走時：不是鏡頭正對著、剛走完一步的那隊；
        路線走完之後：另一隊點了觸發裝置在等它）。換邊才大步快拖——找同一隊（鏡頭只是沒對準）還是小步慢慢找。"""
        if self.switch_scale <= 1.0 or target is None:
            return False
        if self.route is not None and self.route.active:
            return self._fresh_actor is not None and target != self._fresh_actor
        return self._stuck_side == other_side(target)

    def _start_legs(self, plats: Sequence[Platform], w: int, h: int, target: Optional[str]) -> None:
        """這一輪的第一步（鏡頭還對著剛行動完的那隊）：決定要找哪一隊、照哪套走法。"""
        self._replay_n = 0
        if self.scan_moves is not None:
            self._target = None
            self._legs = [(m, 1) for m in self.scan_moves]
            return
        self._target = target if target is not None else self._guess_target(plats, w, h)
        self._fast = self._is_switch(self._target)
        if not self._fast:
            self._legs = scan_legs(self._target)
            return
        self._switch_key = self._switch_key_for(self._target)
        legs: List[Tuple[Tuple[float, ...], int]] = []
        mem = self.memory.get(self._switch_key) if (self.memory is not None and self._switch_key) else None
        if mem:
            # 上次這裡拖了多遠就一次拖過去；萬一沒看到（地圖鏡頭偶爾停得不一樣），先拖回原點再照大步走法找，
            # 從原點找才符合走法的假設（另一隊在剛行動那隊的左下／右下）
            go = replay_moves(*mem)
            back = replay_moves(-mem[0], -mem[1])
            legs = [(m, 1) for m in go] + [(m, 1) for m in back]
            self._replay_n = len(go)
        self._legs = legs + fast_scan_legs(self._target, self.switch_scale)

    def _scan(self, plats: Sequence[Platform], w: int, h: int, note: str, target: Optional[str] = None) -> MapPlan:
        if self._legs is None:
            self._start_legs(plats, w, h, target)
        else:
            self._check_edge()
        if self._still >= STILL_MAX:
            return self._scan_stop(f"連拖 {self._still} 次地圖畫面都沒動（模擬器可能沒收到拖曳，或被別的東西擋住）——請你接手。{note}")
        goal = SIDE_NAMES[self._target] if self._target else "能點的節點"
        extra = sum(n for _, n in self._legs[:2 * self._replay_n]) if self._replay_n else 0
        if self._drags >= self.max_scan_drags + extra:
            return self._scan_stop(f"連拖 {self._drags} 次地圖都沒找到{goal}，請你接手。{note}")
        center = self._center_plan(plats, w, h)
        if center is not None:
            return center._replace(fast=self._fast)
        if self._leg >= len(self._legs):
            return self._scan_stop(f"連拖 {self._drags} 次地圖都沒找到{goal}，請你接手。{note}")
        move = self._legs[self._leg][0]
        x0, y0, x1, y1 = move
        find = f"找{goal}" if self._target else "找"
        if self._fast and self._leg < self._replay_n:
            how = f"換邊：照上次記住的距離一次拖過去（{self._leg + 1}/{self._replay_n}）"
        elif self._fast and self._leg < 2 * self._replay_n:
            how = "換邊：照記住的距離沒看到，先拖回原點再大步找"
        elif self._fast:
            how = f"換邊大步快拖，{look_name(move)}"
        else:
            how = look_name(move)
        return MapPlan("drag", f"沒有可點的節點{note}{self._edge_note}，拖地圖{find}（第 {self._drags + 1} 次，{how}）",
                       int(round(x0 * (w - 1))), int(round(y0 * (h - 1))),
                       int(round(x1 * (w - 1))), int(round(y1 * (h - 1))), tag="scan", fast=self._fast)

    def _scan_stop(self, reason: str) -> MapPlan:
        """找不到、停手叫人。這一輪如果是照記住的距離拖的，那筆記憶不可靠了，丟掉（下一輪重新學）。"""
        if self._replay_n and self.memory is not None and self._switch_key:
            self.memory.forget(self._switch_key)
        return MapPlan("stop", reason)

    # -- 照固定路線走（route.py；10/01 使用者指定） --
    def _route_plats(self, plats: Sequence[Platform], w: int, h: int) -> List[Platform]:
        """隊伍平台是哪一隊——不看英雄長相（11/22：使用者要分享給朋友，每個人的陣容不同；同一天自己換了英雄，
        顏色規則就把尖帽法師當成左隊）。只靠這幾個跟長相無關的事實：
          * 隊伍只有兩支，站的平台都會發光；
          * 鏡頭一定停在剛行動的那隊身上（STAND）：正中間那個是它，畫面上其他有人的平台就是另一隊；
          * 拖地圖時用手指位移推算它被拖到哪：還在畫面上就認位置最近的，被拖出畫面了，看得到的都是另一隊；
          * 一輪剛開始兩隊各在起點：左邊那個是左隊。
        平台沒偵測到也一樣（英雄不是顏色規則認得的那組）：直接當剛行動那隊站在 STAND。
        顏色規則／英雄樣板給的隊伍，只在這些都用不上的時候（例如重開自動、不知道鏡頭對著誰）才算數。"""
        actor = self._fresh_actor      # 只信「這次開自動之後、剛剛真的走了一步」的隊（重開自動前你可能自己拖過地圖）
        out = list(plats)
        r = self.route
        if (r is not None and not self._drags and len(out) == 2 and all(r.idx.get(sd, 0) == 0 for sd in SIDES)
                and {p.side for p in out} != {LEFT, RIGHT}):
            a, b = sorted(out, key=lambda p: p.x)
            out = [a._replace(side=LEFT), b._replace(side=RIGHT)]
        if actor is None:
            return out
        other = other_side(actor)
        if not self._drags:
            c = self._centered(out, w, h)
            if c is not None and c.side == other and self._img is not None:
                # 顏色說是另一隊、英雄樣板也在那裡找到那一隊的英雄：才信（9/20 錄製 #106 鏡頭正中真的是另一隊的野蠻人）
                far = 150 * h / REF_H
                hero = self._heroes_in(self._img).get(other)
                if hero is not None and ((hero.x - c.x) ** 2 + (hero.y - c.y) ** 2) ** 0.5 <= far:
                    self._actor_at = None
                    return out
            if c is None:
                mine = [p for p in out if p.side == actor]
                if mine:
                    c = min(mine, key=lambda p: (p.x - STAND[0] * w) ** 2 + (p.y - STAND[1] * h) ** 2)
                else:
                    c = Platform(int(STAND[0] * w), int(STAND[1] * h), int(140 * h / REF_H), int(120 * h / REF_H), actor)
                    out.append(c)
            self._actor_at = [float(c.x), float(c.y)]
            return [p._replace(side=actor) if p is c else p._replace(side=other) for p in out]
        if self._actor_at is None:
            return out
        ax, ay = self._actor_at
        if ax < -0.08 * w or ax > 1.08 * w or ay < -0.08 * h or ay > 1.08 * h:
            return [p._replace(side=other) for p in out]        # 剛行動那隊已被拖出畫面：看得到的都是另一隊
        tol = (0.12 + 0.04 * self._drags) * w
        near = [p for p in out if ((p.x - ax) ** 2 + (p.y - ay) ** 2) ** 0.5 <= tol]
        c = min(near, key=lambda p: (p.x - ax) ** 2 + (p.y - ay) ** 2) if near else None
        if c is None:
            return [p._replace(side=other) if p.side != actor else p for p in out]
        return [p._replace(side=actor) if p is c else p._replace(side=other) for p in out]

    def _glow_for(self, side: str, st, view: "MapView", plats: Sequence[Platform], cands: Sequence["Cand"],
                  img_shape: Tuple[int, ...]) -> Optional[Tuple[Platform, "RouteOpt", str]]:
        """找不到 side 這隊（英雄不是顏色規則認得的那組，平台上「有沒有人」都判斷不出來）：看畫面上每個發光平台，
        前面剛好有路線下一步要的節點（種類＋方向都對）的那個就是它。剛好一個對得上才算，多個就不猜。"""
        if self._img is None:
            return None
        h = img_shape[0]
        s = h / REF_H
        known = list(plats)
        hits = []
        for g in find_platforms(self._img, any_glow=True):
            if any(abs(g.x - p.x) <= 60 * s and abs(g.y - p.y) <= 60 * s for p in known):
                continue
            if self._actor_at is not None and ((g.x - self._actor_at[0]) ** 2 + (g.y - self._actor_at[1]) ** 2) ** 0.5 <= 120 * s:
                continue
            g = g._replace(side=side)
            loose = [n for n, _ in adjacent_nodes(cands, [g], img_shape)]
            opts = route_options(view.nodes, known + [g], g, h, extra=loose)
            if st.kind == WALK:
                continue
            o, why = match_route_step(st.kind, st.dir, opts, cands, h)
            if o is not None:
                hits.append((g, o, why))
        return hits[0] if len(hits) == 1 else None

    def _team_plat(self, plats: Sequence[Platform], side: str, w: int, h: int) -> Optional[Platform]:
        mine = [p for p in plats if p.side == side]
        if not mine:
            return None
        sx, sy = STAND[0] * w, STAND[1] * h
        return min(mine, key=lambda p: (p.x - sx) ** 2 + (p.y - sy) ** 2)

    def _is_centered(self, p: Platform, w: int, h: int) -> bool:
        return abs(p.x - STAND[0] * w) <= CENTER_BOX[0] * w and abs(p.y - STAND[1] * h) <= CENTER_BOX[1] * h

    def _center_on(self, p: Platform, side: str, w: int, h: int, why: str) -> MapPlan:
        """把這隊拖到畫面中間（跟 _center_plan 同一種拖法）。"""
        sx, sy = STAND[0] * w, STAND[1] * h
        dx = float(np.clip(sx - p.x, -0.25 * w, 0.25 * w))
        dy = float(np.clip(sy - p.y, -0.30 * h, 0.30 * h))
        cx, cy = 0.48 * w, 0.55 * h
        return MapPlan("drag", why, int(round(cx - dx / 2)), int(round(cy - dy / 2)),
                       int(round(cx + dx / 2)), int(round(cy + dy / 2)), side=side, tag="center")

    def _decide_route(self, view: MapView, img_shape: Tuple[int, ...], now: float) -> MapPlan:
        """照路線：哪隊能走（route.can_act）→ 在畫面上找到那隊 → 它前面「路線下一步的種類＋方向」那個節點 → 點。
        兩隊都能走、都看得到時照舊：寶箱優先、治療要等另一隊自上次治療打過（而且知道另一隊下一步就是怪物時，先去讓它打）。
        能走的隊看不到＝拖地圖去找它；看得到但前面沒有路線要的節點＝先把它拖到中間再看，還是沒有就停下來叫人（使用者 10/01 同意）。"""
        r = self.route
        h, w = img_shape[:2]
        s = h / REF_H
        if not view.clean:
            return MapPlan("wait", "畫面上疊著彈窗或被壓暗，先不動")
        if not view.stable or self._img is None:
            return MapPlan("wait", "地圖還在移動，等它停下來")
        free = [sd for sd in SIDES if r.can_act(sd)]
        if not free:
            return MapPlan("wait", "路線：兩隊都還不能走（" + "；".join(r.describe_side(sd) for sd in SIDES) + "）")
        plats, cands = self._scene(self._img, now)
        raw = {(p.x, p.y): p.side for p in plats}     # 顏色／英雄樣板原本說的隊（_snapped_back 用）
        plats = self._route_plats(plats, w, h)
        if r.retry in free and not self._drags:
            # 剛打輸、鏡頭還沒拖過：遊戲鏡頭停在剛戰鬥的那隊身上，畫面正中間那個平台就是它（擋在前面的怪物會讓顏色判錯隊）
            c = self._centered(plats, w, h)
            if c is not None and c.side != r.retry:
                plats = [p._replace(side=r.retry) if p is c else (p._replace(side=None) if p.side == r.retry else p)
                         for p in plats]
        if r.retry in free and not any(p.side == r.retry for p in plats):
            # 戰敗要重打的隊站在那隻怪物的格子上，怪物擋在英雄前面，顏色／樣板都認不出是誰（11/22 實測：
            # 右隊被紅盔甲斯巴達擋住，連拖 23 次都說找不到）。畫面上只有一個認不出的隊伍平台＝就是它
            unknown = [p for p in plats if p.side is None]
            if not unknown and not r.can_act(other_side(r.retry)):
                # 怪物的紅盔甲還會被顏色規則當成「左隊」。另一隊此刻不能走（停在觸發裝置上等），它腳下有紫色光柱；
                # 腳下沒有裝置光柱的那個平台才是要重打的隊
                beams = [c for c in cands if c.kind == DEVICE]
                unknown = [p for p in plats if not any(abs(c.x - p.x) <= 70 * s and abs(c.y - p.y) <= 90 * s for c in beams)]
            if len(unknown) == 1:
                plats = [p._replace(side=r.retry) if p is unknown[0] else p for p in plats]
        loose =[n for n, _ in adjacent_nodes(cands, plats, img_shape)]     # 沒圖釘的節點（遊戲有時不顯示圖釘）
        found: Dict[str, Tuple[RouteOpt, str]] = {}
        missing: Dict[str, Tuple[Platform, str]] = {}
        marks: List[tuple] = [(p.x, p.y, "隊伍" + (SIDE_NAMES[p.side][0] if p.side else "?"), False) for p in plats]
        self.sides = {}
        for sd in free:
            p = self._team_plat(plats, sd, w, h)
            if p is None:
                continue
            st = r.next(sd)
            opts = route_options(view.nodes, plats, p, h, extra=loose)
            for o in opts:
                self.sides[(o.node.x, o.node.y)] = sd
                if o.node.pinless:
                    marks.append((o.node.x, o.node.y, f"{KIND_NAMES.get(o.node.kind, '？')}·無圖釘", False))
            if st.kind == WALK:
                o, why = self._walk_option(opts, p, st.dir, h)
            else:
                o, why = match_route_step(st.kind, st.dir, opts, cands, h)
            if o is not None:
                found[sd] = (o, why)
            else:
                missing[sd] = (p, why)
        for sd in free:
            if sd in found or sd in missing or (r.retry == sd):
                continue
            st = r.next(sd)
            g = self._glow_for(sd, st, view, plats, cands, img_shape) if st is not None else None
            if g is not None:
                gp, o, why = g
                plats = list(plats) + [gp]
                found[sd] = (o, why + "（英雄認不出是哪隊，靠路線下一步對得上）")
                marks.append((gp.x, gp.y, "隊伍" + SIDE_NAMES[sd][0] + "?", False))

        self.extra_overlay = marks

        rt = r.retry
        if rt in free:
            p = self._team_plat(plats, rt, w, h)
            st = r.next(rt)
            if p is not None and st is not None and st.kind in (ENEMY, CHEST):
                x, y = p.x, p.y - int(25 * s)
                n = Node(x, y, stand_in_pin(x, y, h), st.kind, 0.0, True)
                a = self._attempt(n, s)
                if a is None or a[2] < self.max_attempts:
                    return MapPlan("click", f"路線 {st.name(rt)}：剛剛打輸了，點{SIDE_NAMES[rt]}腳下那格重打{st.what()}",
                                   x, y, node=n, side=rt, route=(rt, r.idx.get(rt, 0)), tag="retry")
                r.set_retry(None)               # 點腳下兩次都沒反應：怪物不在那格，照一般找法（正前方）

        snap = self._snapped_back(plats, raw, found, missing, w, h)
        if snap is not None:
            return snap

        if found:
            rank = {k: i for i, k in enumerate(self.priority)}
            side = min(found, key=lambda sd: (rank.get(r.next(sd).kind, 99), found[sd][0].node.pin.bottom))
            o, why = found[side]
            st = r.next(side)
            if st.kind == HEAL:
                other = other_side(side)
                ost = r.next(other)
                if (self._fights.get(other, 0) == 0 and r.can_act(other) and ost is not None and ost.kind == ENEMY
                        and other not in missing):
                    hold = f"（{SIDE_NAMES[side]}的治療先留著：{SIDE_NAMES[other]}自上次治療還沒打過，先讓{SIDE_NAMES[other]}打）"
                    if other in found:
                        side, (o, why), st = other, found[other], ost
                        why = hold + why
                    else:
                        return self._scan(plats, w, h, hold, target=other)
            self._miss_since = None
            return self._route_click(side, st, o, why, now, s)

        if missing:
            side = min(missing, key=lambda sd: (missing[sd][0].x - STAND[0] * w) ** 2 + (missing[sd][0].y - STAND[1] * h) ** 2)
            p, why = missing[side]
            st = r.next(side)
            if self._miss_since is None:
                self._miss_since = now
            if not self._is_centered(p, w, h) and self._center_n < MAX_CENTER_DRAGS:
                return self._center_on(p, side, w, h, f"路線 {st.name(side)}：{SIDE_NAMES[side]}在畫面邊邊，先拖到中間再找{st.what()}")
            if now - self._miss_since < self.route_miss_seconds:
                return MapPlan("wait", f"路線 {st.name(side)}：找{SIDE_NAMES[side]}{st.what()}（{why}），再看一下")
            return MapPlan("stop", f"路線 {st.name(side)} 要點{SIDE_NAMES[side]}{st.what()}，但{why}——請你看一下畫面。"
                                   f"如果進度不對（不是這一步），可以按面板的「路線 調整…」改。")

        # 能走的隊都不在畫面上：拖地圖去找（兩隊都能走又都看不到：找剛走完那隊以外的那隊；什麼都不知道就照預設走法）
        if len(free) == 1:
            target = free[0]
        else:
            target = other_side(self._fresh_actor) if self._fresh_actor else None
        need = "、".join(r.describe_side(sd) for sd in free)
        return self._scan(plats, w, h, f"（路線：{need}）", target=target)

    def _snapped_back(self, plats: Sequence[Platform], raw: Dict[Tuple[int, int], Optional[str]],
                      found: Dict[str, tuple], missing: Dict[str, tuple], w: int, h: int) -> Optional[MapPlan]:
        """拖完地圖，鏡頭又被遊戲拉回剛行動那隊（11/24 實測：第1段左7 點完裝置、換邊第一下大步拖之後，
        遊戲把鏡頭重新對準野蠻人，拖曳等於白拖；推算位置以為它被拖出畫面，就把它當成右隊，找不到「右1」停手。
        11/22 同一個地方也停過）。

        跡象：拖過地圖之後，要找的那隊「正好站在畫面正中間」（只有遊戲自己會把鏡頭對準隊伍），而且
          * 顏色／英雄樣板原本就說它是剛行動那隊（＋英雄樣板在它身上也找到那隊的英雄，避免新陣容顏色認反）；或
          * 它前面根本對不上要找那隊的路線下一步，顏色也沒說它是要找的那隊。
        就當成鏡頭跳回去了：從剛行動那隊重新開始找（一次總覽最多 MAX_REANCHOR 次，第二次照舊停手叫人）。"""
        actor = self._fresh_actor
        if not self._drags or actor is None or self._reanchor_n >= MAX_REANCHOR or self.route is None:
            return None
        far = 150 * h / REF_H
        for sd in [x for x in SIDES if x in found or x in missing]:
            if sd == actor:
                continue
            p = self._team_plat(plats, sd, w, h)
            if p is None or not self._is_centered(p, w, h):
                continue
            said = raw.get((p.x, p.y))
            snapped = False
            if sd in missing and said != sd:
                snapped = True
            elif said == actor and self._img is not None:
                hero = self._heroes_in(self._img).get(actor)
                snapped = hero is not None and ((hero.x - p.x) ** 2 + (hero.y - p.y) ** 2) ** 0.5 <= far
            if not snapped:
                continue
            self._reanchor_n += 1
            self._reset_scan()
            self._actor_at = None
            self._miss_since = None
            self._tracks.clear()
            self._scene_cache = None
            self._hero_cache = None
            return MapPlan("wait", f"拖完地圖鏡頭又跳回{SIDE_NAMES[actor]}（遊戲把剛剛的拖曳蓋掉了），"
                                   f"從{SIDE_NAMES[actor]}重新找{SIDE_NAMES[sd]}")
        return None

    def _walk_option(self, opts: Sequence[RouteOpt], p: Platform, d: str, img_h: int) -> Tuple[RouteOpt, str]:
        """路線的「空格」：那個方向一格遠的平台。有圖釘（9/20 錄製那次有）就點圖釘那格，不管分類器說它是什麼；
        沒有就照位置點（鄰接一格的標準位移，見 DIR_CENTER 的量法：正前 (0, −205)、左前 (−305, −125)、右前 (+305, −125)）。"""
        near = sorted((o for o in opts if in_dir(o.angle, d) and o.dist <= 300), key=lambda o: abs(o.angle - DIR_CENTER[d]))
        if near:
            return near[0], ""
        s = img_h / REF_H
        sc = platform_scale(p.y, s) * s
        vx, vy = {"F": (0, -205), "L": (-305, -125), "R": (305, -125)}[d]
        x, y = int(round(p.x + vx * sc)), int(round(p.y + vy * sc))
        a, dist = direction_of(x, y, p, img_h)
        return RouteOpt(Node(x, y, stand_in_pin(x, y, img_h), WALK, 0.0, True), a, dist), "（那格沒有圖釘，照位置點）"

    def _route_click(self, side: str, st, o: RouteOpt, why: str, now: float, s: float) -> MapPlan:
        """點路線這一步的節點。同一個節點點了 max_attempts 次都沒反應：隔一格的（中間有空平台，例如第 3 段右隊荊棘消失後那格）
        先點中間那格試一次，還是不行就停手。"""
        r = self.route
        n = o.node
        key = (side, r.idx.get(side, 0))
        a = self._attempt(n, s)
        if a is not None and a[2] >= self.max_attempts:
            if o.dist >= 300 and key not in self._stones:
                self._stones.add(key)
                p = self._team_plat(self._route_plats(self._scene(self._img, now)[0], self._img.shape[1], self._img.shape[0]),
                                    side, self._img.shape[1], self._img.shape[0]) if self._img is not None else None
                if p is not None:
                    mx, my = (p.x + n.x) // 2, (p.y + n.y) // 2
                    return MapPlan("click", f"路線 {st.name(side)}：{st.what()}點了沒反應，先點中間那格空平台", mx, my, side=side)
            return MapPlan("stop", f"路線 {st.name(side)}：{SIDE_NAMES[side]}{st.what()}點了 {int(a[2])} 次都沒反應——請你看一下畫面。")
        return MapPlan("click", f"路線 {st.name(side)}：點{SIDE_NAMES[side]}{st.what()}{why}", n.x, n.y,
                       node=n, side=side, route=key)

    # -- 沒圖釘：找隊伍腳邊的節點 --
    def _scene(self, img: np.ndarray, now: float) -> Tuple[List[Platform], List[Cand]]:
        """隊伍平台＋候選節點。找一次約 0.1 秒，靜止畫面 0.5 秒內不重找；拖地圖後作廢。
        平台上的顏色分不出是哪一隊時，改用英雄樣板比對補上（只有這時候才多花 0.35 秒）。"""
        if self._scene_cache is not None and now - self._scene_cache[0] <= 0.5:
            return self._scene_cache[1], self._scene_cache[2]
        plats = find_platforms(img)
        if any(p.side is None for p in plats):
            # 顏色認不出的才用英雄樣板補（11/22 實測：鏡頭拉遠時英雄很小，樣板分數低、還會被別隊的樣板誤配；
            # 顏色規則對換上的雙環刃女英雄仍判得出「左隊」）。不用「左右位置」補：站著怪物的發亮平台會被當成另一隊
            heroes = self._heroes_in(img)
            far = 150 * img.shape[0] / REF_H
            fixed = []
            for p in plats:
                if heroes and p.side is None:
                    near = min(heroes.values(), key=lambda hh: (hh.x - p.x) ** 2 + (hh.y - p.y) ** 2)
                    if ((near.x - p.x) ** 2 + (near.y - p.y) ** 2) ** 0.5 <= far:
                        p = p._replace(side=near.side)
                fixed.append(p)
            plats = fixed
        cands = find_candidates(img, plats)
        self._scene_cache = (now, plats, cands)
        return plats, cands

    def _team_at(self, node: Node) -> Optional[str]:
        """這個節點鄰接的是哪一隊的平台（看不準、或沒有平台鄰接就是 None）。"""
        if self._img is None:
            return None
        plats, _ = self._scene(self._img, self._now)
        best = None
        s = self._img.shape[0] / REF_H
        for p in plats:
            sc = platform_scale(p.y, s) * s
            ndx, ndy = (node.x - p.x) / sc, (node.y - p.y) / sc
            if is_ahead(ndx, ndy) and (best is None or ndx * ndx + ndy * ndy < best[0]):
                best = (ndx * ndx + ndy * ndy, p.side)
        return best[1] if best else None

    def _plan_unpinned(self, img_shape: Tuple[int, ...], now: float) -> Tuple[Optional[MapPlan], str]:
        """回 (規劃, 補充說明)。規劃是 None＝這個畫面沒有可點的（補充說明會接在拖地圖的訊息後面）。"""
        h, w = img_shape[:2]
        s = h / REF_H
        plats, cands = self._scene(self._img, now)
        adj = adjacent_nodes(cands, plats, img_shape)
        usable: List[Tuple[Node, Platform]] = []
        waiting = 0
        for n, p in adj:
            if self._stuck_side is not None and p.side == self._stuck_side:
                waiting += 1             # 這隊已經站在觸發裝置上等另一隊，前面的路點了也沒用
                continue
            a = self._attempt(n, s)
            if a is not None and a[2] >= self.max_attempts and now - a[3] < self.skip_seconds:
                continue                 # 點了好幾次都沒反應，先放著
            usable.append((n, p))
        if self._target is not None and any(p.side == self._target for _, p in usable):
            # 正在找某一隊、而且找到了：先點它前面的，別先去試認不出隊伍的「平台」旁邊的節點
            # （錄製第 26 幀：站在亮平台上的怪物被當成認不出是誰的隊伍）
            usable = [(n, p) for n, p in usable if p.side == self._target]
        self.sides = {(n.x, n.y): p.side for n, p in usable}
        pick, why = choose_for_teams([n for n, _ in usable], self.priority, self.sides, self._fights)
        marks = [(p.x, p.y, "隊伍" + (SIDE_NAMES[p.side][0] if p.side else "?"), False) for p in plats]
        marks += [(n.x, n.y, f"{KIND_NAMES.get(n.kind, '？')}·無圖釘", pick is not None and n.x == pick.x and n.y == pick.y)
                  for n, _ in usable]
        self.extra_overlay = marks
        if pick is not None:
            side = self.sides.get((pick.x, pick.y))
            who = f"（{SIDE_NAMES[side]}）" if side else ""
            return MapPlan("click", f"沒圖釘，隊伍前面有「{KIND_NAMES[pick.kind]}」節點{who}{why}",
                           pick.x, pick.y, node=pick, side=side), ""
        if not plats:
            return None, "（畫面裡看不到隊伍）"
        if waiting and not usable:
            return None, f"（{SIDE_NAMES[self._stuck_side]}已在等另一隊，看不到另一隊）"
        return None, "（隊伍前面沒有認得出的節點）"

    # -- 做完之後記帳 --
    def commit(self, plan: MapPlan, img_shape: Tuple[int, ...], now: float, side: Optional[str] = None) -> None:
        s = img_shape[0] / REF_H
        if plan.kind == "click" and plan.route is not None and self.route is not None:
            self.route.pending = plan.route     # 畫面離開總覽（engine 呼叫 route.confirm）才算走完這一步
            nxt = self.route.next(plan.route[0])
            if nxt is not None and nxt.kind == WALK:
                self._walk_since = now          # 空格不跳彈窗：observe 看到鏡頭跟著動了才算走過去
        if plan.kind == "click" and plan.tag == "retry":
            self._retry_clicked = True
        if plan.kind == "click" and plan.node is not None:
            if plan.node.kind == HEAL:
                self._fights = {LEFT: 0, RIGHT: 0}    # 治療一點下去兩隊的計數歸零（重新累積）
                self._pending = None
            elif plan.node.kind == DEVICE:
                self._pending = None      # 裝置不用打（9/25 那輪：點完裝置你自己去打另一隊，勝利被記到點裝置的那隊）
            else:
                self._pending = side
            if plan.node.kind == DEVICE:
                # 觸發裝置要雙邊都觸發：這隊到位了，之後只等另一隊。哪一隊要很有把握（判錯會擋到不該擋的隊、
                # 反而去找已經在等的那隊）：用「裝置鄰接的是哪個隊伍平台」的顏色判斷；沒圖釘的節點本來就是這樣判的；
                # 其他情況（看不準、沒看過畫面）寧可不記。回到總覽後再用鏡頭正對著的那隊確認一次（_confirm_stuck）
                self._stuck_side = self._team_at(plan.node) if not plan.node.pinless else side
                self._stuck_check = True
            a = self._attempt(plan.node, s)
            if a is None:
                self._attempts.append([plan.node.x, plan.node.y, 1, now])
            else:
                a[2] += 1
                a[3] = now
            # 找另一隊的進度（拖了幾次、走到第幾段）不在這裡歸零：點了遊戲沒反應的話要接著找，不能從頭再來
            # （不然「點不動的節點 → 重新斜拖」會一直循環、永遠拖不到上限）。點有效＝畫面離開總覽，left_overview 會整個重來。
        elif plan.kind == "drag":
            self._drags += 1
            h, w = img_shape[:2]
            self._net[0] += (plan.x1 - plan.x) / max(1, w - 1)      # 這一輪總共拖了多遠（換邊記憶用）
            self._net[1] += (plan.y1 - plan.y) / max(1, h - 1)
            if plan.tag == "center":
                self._center_n += 1
            elif self._legs is not None and self._leg < len(self._legs):
                self._last_leg = self._leg
                self._leg_n += 1
                if self._leg_n >= self._legs[self._leg][1]:
                    self._leg += 1
                    self._leg_n = 0
            self._pre_drag, self._pre_tag = self._prev_small, plan.tag
            self._edge_note = ""
            self._miss_since = None       # 路線：拖完重新看（拖到中間之後要重新等一段時間才算「真的找不到」）
            self._tracks.clear()          # 拖完鏡頭會動，舊的圖釘位置作廢
            if self._actor_at is not None:
                # 地圖內容跟著手指跑（9/24 校準：近處 1.16~1.33 倍、遠處較少；取 1.15）
                self._actor_at[0] += (plan.x1 - plan.x) * DRAG_GAIN
                self._actor_at[1] += (plan.y1 - plan.y) * DRAG_GAIN
            self._hero_cache = None
            self._scene_cache = None
            self._no_pin_since = now      # 拖完等一小段再看有沒有圖釘


__all__ = [
    "APP_DIR", "ASSETS_DIR", "NODES_DIR",
    "REF_H", "CHEST", "ENEMY", "HEAL", "REVIVE", "DEVICE", "KIND_NAMES", "DEFAULT_PRIORITY",
    "Pin", "Node", "MapPlan", "MapView", "find_pins", "node_point", "pale_fraction", "node_patch",
    "NodeClassifier", "choose", "choose_for_teams", "other_side", "describe_nodes", "MapPlanner", "DEFAULT_SCAN_MOVES", "PALE_MAX",
    "Hero", "HeroFinder", "side_of", "side_of_sure", "LEFT", "RIGHT", "SIDES", "SIDE_NAMES", "HERO_NAMES", "HEROES_DIR", "MOTION_MAX",
    "KIND_ALIAS", "DIM_MIN", "header_lum", "is_dimmed", "Platform", "Cand", "find_platforms", "find_candidates",
    "adjacent_nodes", "is_ahead", "platform_scale", "stand_in_pin", "look_name", "LOOK_DOWN", "LOOK_UP", "LOOK_LEFT",
    "LOOK_RIGHT", "DEFAULT_MAX_SCAN_DRAGS", "LOOK_DOWN_LEFT", "LOOK_DOWN_RIGHT", "scan_legs", "STAND", "CENTER_BOX",
    "MAX_CENTER_DRAGS", "STILL_MAX", "DIR_BANDS", "DIR_CENTER", "RouteOpt", "direction_of", "in_dir",
    "route_options", "match_route_step",
    "DRAG_AREA", "MAX_DRAG", "place_drag", "big_move", "fast_scan_legs", "replay_moves",
]
