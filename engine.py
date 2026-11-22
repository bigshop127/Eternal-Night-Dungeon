"""永夜迷城輔助器的核心：鎖定使用者框選的範圍 → 只在範圍內擷取、辨識 → 依規則自動點擊。

跟水果2048 一樣的分工：UI（app.py）只負責顯示與按鈕，所有「看、判斷、動手」都在這裡，
而且刻意不碰 tkinter，測試時可以塞假的擷取來源與假的點擊函式，不必開視窗也不必碰真實遊戲。

核心原則：
  * **範圍就是全部。**擷取、辨識、點擊座標、錄製，全都以使用者框出來的那個矩形為準；
    範圍外的東西（桌面、其他視窗、模擬器的側邊工具列）不會被看到，也不會被點到。
  * **規則的座標用「範圍內的比例」存**（0~1），不存螢幕絕對座標，所以模擬器視窗搬到別處、
    或重新框選時範圍稍有出入，規則都還能用。
  * **寧可停手也不亂點。**認不得的畫面、點了沒反應、自己的視窗蓋住遊戲、視窗縮小或大小變了
    ——一律停下來說明原因，不會硬猜。
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PIL import Image

import adb_io as A
import control as C
import overview as O
import route as R
import vision_state as V

# 資料夾可以用環境變數換掉——測試不能去動使用者真正的設定與樣板庫
# （fruit2048 踩過「測試把真實 templates.json 寫壞」的坑）。
APP_DIR = os.environ.get("ENDUNGEON_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
RULES_PATH = os.path.join(APP_DIR, "rules.json")
SESSIONS_DIR = os.path.join(APP_DIR, "sessions")
DEBUG_DIR = os.path.join(APP_DIR, "debug_frames")   # 總覽拖地圖／停手當下的畫面（事後查「它當時看到什麼」用）
ROUTE_PATH = os.path.join(APP_DIR, "route_progress.json")   # 固定路線走到第幾步（route.py），輔助器重開也接得上
SWITCH_PATH = os.path.join(APP_DIR, "switch_memory.json")   # 換邊記憶：每個換邊點上次拖多遠才找到另一隊（route.SwitchMemory）

_BAD_LABEL_CHARS = set('\\/:*?"<>|')

# 11/22 使用者：隊伍裡有英雄陣亡（卡片變灰）時 Challenge 按了沒反應 → 先點右下「Heart of Norheim」→ 視窗中間「Tap to use」復活
VS_LABEL = "VS"
REVIVE_LABEL = "復活視窗"
VS_CARDS = ((0.233, 0.282, 0.288, 0.382), (0.319, 0.282, 0.373, 0.382),     # VS 畫面我方四張英雄卡的頭像（範圍比例）
            (0.233, 0.483, 0.288, 0.583), (0.319, 0.483, 0.373, 0.583))
REVIVE_CARDS = tuple((x0, 0.261, x0 + 0.058, 0.358) for x0 in (0.264, 0.351, 0.438, 0.525))   # 復活視窗裡的英雄卡
DEAD_SAT = 10.0            # 頭像平均色彩飽和度（最大−最小色版）低於這個＝灰色＝陣亡（實測陣亡 2.2、活著 ≥ 38）
VS_CLOSE = (0.962, 0.154)  # VS 畫面右上角 ✕
HEART_BTN = (0.922, 0.903)     # 總覽右下「Heart of Norheim」
REVIVE_USE = (0.521, 0.781)    # 復活視窗中間的愛心「Tap to use」
REVIVE_CLOSE = (0.785, 0.134)  # 復活視窗右上角 ✕


def dead_cards(img: np.ndarray, boxes) -> int:
    """這幾張英雄卡裡有幾張是灰的（陣亡）。"""
    h, w = img.shape[:2]
    n = 0
    for x0, y0, x1, y1 in boxes:
        p = img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].astype(np.int16)
        if p.size and float((p.max(axis=2) - p.min(axis=2)).mean()) < DEAD_SAT:
            n += 1
    return n


DONE_LABEL = "挑戰完成"      # 11/22：整輪打完的「Challenge Complete」畫面，點掉之後自動停手
DEFEAT_LABEL = "戰敗"         # 11/22：戰鬥打輸的結算畫面（點 Quit）；路線退回那一步重打，同一步連輸 MAX_DEFEATS 次就停手
MAX_DEFEATS = 3
VICTORY_LABEL = "VICTORY"    # 勝利結算畫面的名稱（tools/seed_templates.py 的 SPEC）；看到它就替剛點節點的那一隊記一場戰鬥
# 進入下一階段的過場畫面（觸發裝置 → 提示框確定 → 獎勵輪盤 → 五選一）：看到它們＝兩隊都有新路可走，
# 規劃器不必再記得「哪一隊在等另一隊」（見 MapPlanner.new_stage）
STAGE_LABELS = ("提示框", "獎勵輪盤", "五選一")
# 一輪的起點（使用者 9/1：「我每次腳本都會從這裡開始執行」）。開自動後第一個看到的總覽是它＝新的一輪，路線從頭走
START_LABEL = "起始畫面"


def label_error(name: str) -> Optional[str]:
    """狀態名稱會直接拿來當樣板資料夾名，所以要擋掉 Windows 不能用的字元。合法回 None。"""
    if not name or not name.strip():
        return "請幫這個畫面取個名字"
    if name != name.strip():
        return "名稱前後不能有空白"
    if len(name) > 40:
        return "名稱太長了（最多 40 字）"
    bad = sorted(c for c in set(name) if c in _BAD_LABEL_CHARS or ord(c) < 32)
    if bad:
        return "名稱不能包含這些字元：" + " ".join(bad)
    if name.startswith(".") or name.endswith("."):
        return "名稱不能以「.」開頭或結尾"
    return None


# ---------------------------------------------------------------- 設定檔

@dataclass
class Config:
    region: Optional[dict] = None          # 使用者框選的範圍（螢幕座標）
    window_anchor: Optional[dict] = None   # {title, dx, dy, w, h}：範圍相對模擬器視窗左上角的位移
    tolerance: float = 18.0                # 跟 StateDB 預設值一致
    min_margin: float = 2.0
    poll_ms: int = 150
    stable_reads: int = 2                  # 連續幾次讀到同一個狀態才採信（擋掉轉場動畫）
    click_hold: float = 0.06
    action_interval: float = 1.0           # 兩次動作之間至少隔多久
    retry_after: float = 3.0               # 點了之後畫面還是同一個狀態，隔多久再點一次
    max_retries: int = 3                   # 連續重試幾次仍沒反應就停手
    unknown_timeout: float = 90.0          # 自動操控時連續認不得多久就停手
    opacity: float = 0.96
    panic_key: str = C.DEFAULT_PANIC_KEY
    # 地城總覽（規則動作 map）：自動選節點與拖地圖找節點，細節見 overview.py
    map_interval: float = 2.5              # 總覽裡兩次動作（點節點／拖地圖）之間至少隔多久，讓鏡頭與彈窗有時間動完
    node_priority: Optional[list] = None   # 節點優先序，None＝預設（寶箱→治療→怪物→復活→裝置）
    scan_moves: Optional[list] = None      # 找不到節點時拖地圖的固定走法 [[x0,y0,x1,y1],…]（範圍比例），None＝依「要找哪一隊」自動決定
    scan_max_drags: int = O.DEFAULT_MAX_SCAN_DRAGS   # 一輪找另一隊最多拖幾次（預設＝走完一整套走法，見 overview.scan_legs）
    map_stuck_timeout: float = 60.0        # 總覽連續「等」超過幾秒就停手
    debug_frames: int = 150                # 總覽拖地圖／停手時把當下畫面存成 JPG（debug_frames/，只留最新這麼多張；0＝不存）
    route_enabled: bool = True             # 前三個階段照使用者 10/01 指定的固定路線走（route.py），走完照原本規則
    # 操控方式（11/22）：screen＝原本的「框選範圍＋螢幕擷取＋點滑鼠」；adb＝背景操控（adb 截圖／點擊，不碰滑鼠，
    # 模擬器被蓋住或縮到最小都照跑，見 adb_io.py）
    control_mode: str = "screen"
    adb_serial: str = A.DEFAULT_SERIAL     # BlueStacks「1」；連不上會自動改用開著 ADB 的那台
    # 換邊（一隊走完一段、碰到裝置，回頭找另一隊）大步快拖（11/22，見 overview.fast_scan_legs）
    switch_scale: float = 1.75             # 每一步拖多遠（上下 ×這個、左右 ×它的平方根）；1＝關掉，用原本的走法
    switch_interval: float = 0.9           # 換邊時兩次動作之間隔多久（取代 map_interval 2.5 秒）
    switch_memory: bool = True             # 記住每個換邊點上次拖多遠，下一輪直接一次拖過去（switch_memory.json）
    # 「現在是不是自動操控」刻意不存檔：每次啟動都要人工按一次才會動，不會一開就搶滑鼠。

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if not os.path.exists(CONFIG_PATH):
            return cfg
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return cfg
        if isinstance(data, dict):
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg

    def save(self) -> None:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------- 規則：畫面狀態 → 動作

class RuleBook:
    """「看到這個畫面就做這個動作」。動作有三種：

      click  點範圍內的某個位置（x、y 是 0~1 的比例）
      wait   什麼都不做（動畫、戰鬥中……這些畫面只需要辨識、等它自己結束）
      map    地城總覽：自動找節點圖釘、依優先序點一個，找不到就拖地圖（overview.py）

    沒有規則的狀態，自動操控也是不動——跟 wait 的差別只在畫面上會提示「還沒設定動作」。
    """

    def __init__(self, path: str = RULES_PATH) -> None:
        self.path = path
        self.rules: Dict[str, dict] = {}

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def click_count(self) -> int:
        return sum(1 for r in self.rules.values() if r["action"] == "click")

    def get(self, label: str) -> Optional[dict]:
        return self.rules.get(label)

    def set_click(self, label: str, x: float, y: float) -> None:
        self.rules[label] = {"action": "click",
                             "x": round(min(1.0, max(0.0, float(x))), 4),
                             "y": round(min(1.0, max(0.0, float(y))), 4)}

    def set_wait(self, label: str) -> None:
        self.rules[label] = {"action": "wait"}

    def set_map(self, label: str) -> None:
        self.rules[label] = {"action": "map"}

    def remove(self, label: str) -> None:
        self.rules.pop(label, None)

    def describe(self, label: str) -> str:
        r = self.rules.get(label)
        if r is None:
            return "尚未設定動作"
        if r["action"] == "click":
            return f"點擊（{r['x'] * 100:.0f}%，{r['y'] * 100:.0f}%）"
        if r["action"] == "map":
            return "自動選節點（寶箱→治療→怪物…；沒圖釘就看隊伍前面，再沒有就拖地圖）"
        return "不動作（等待）"

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": self.rules}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str = RULES_PATH) -> "RuleBook":
        book = cls(path)
        if not os.path.exists(path):
            return book
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return book
        raw = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return book
        for label, r in raw.items():
            if not isinstance(r, dict):
                continue
            if r.get("action") == "wait":
                book.rules[label] = {"action": "wait"}
            elif r.get("action") == "map":
                book.rules[label] = {"action": "map"}
            elif r.get("action") == "click":
                try:
                    x, y = float(r["x"]), float(r["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    book.set_click(label, x, y)
        return book


# ---------------------------------------------------------------- 範圍追蹤

class RegionTracker:
    """回傳「現在這一刻」使用者框選的範圍在螢幕上的位置。

    框選時如果認得出範圍底下是哪個模擬器視窗，就記下範圍相對視窗左上角的位移（anchor）；
    之後每次擷取前重新找一次那個視窗，把位移套回去——把模擬器挪到別處不用重新框。
    只跟著「搬動」，視窗**大小**變了就停手請使用者重框：大小一變，範圍裡每個東西的
    相對位置都變了，硬套原本的位移只會擷取到錯的內容、點到錯的地方。
    """

    SIZE_TOLERANCE = 2   # 視窗外框量到的大小允許差幾個像素（拖動時會有 1px 抖動）

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def current(self) -> Tuple[Optional[V.Region], Optional[str]]:
        if not self.cfg.region:
            return None, "還沒框選畫面範圍。按「① 框選畫面範圍」，拖曳框出模擬器裡的遊戲畫面。"
        base = V.Region.from_dict(self.cfg.region)
        anchor = self.cfg.window_anchor
        if not anchor:
            return base, None
        title = anchor["title"]
        hwnd = C.find_window(title)
        rect = C.window_rect(hwnd) if hwnd else None
        if rect is None or C.window_is_minimized(hwnd):
            return None, f"找不到視窗「{title}」——可能被關掉或縮到最小了。還原視窗就會自動接回去。"
        left, top, right, bottom = rect
        want_w, want_h = anchor.get("w"), anchor.get("h")
        if want_w and want_h and (abs((right - left) - want_w) > self.SIZE_TOLERANCE
                                  or abs((bottom - top) - want_h) > self.SIZE_TOLERANCE):
            return None, (f"視窗「{title}」的大小變了（框選時 {want_w}×{want_h}，現在 "
                          f"{right - left}×{bottom - top}），範圍對不上了。請按「① 框選畫面範圍」重框一次。")
        return V.Region(left + anchor["dx"], top + anchor["dy"], base.width, base.height), None

    def blocker(self, x: int, y: int) -> Optional[str]:
        """螢幕上 (x, y) 這一點最上層的視窗如果「不是遊戲的」，回傳它的描述；沒問題回 None。

        「不是遊戲的」有兩種：輔助器自己的視窗（面板、對話框），或另一個程式的視窗
        （瀏覽器、別的遊戲蓋在模擬器上面）。後者只有在框選時鎖定過視窗（知道遊戲屬於哪個
        行程）才判斷得了；同一個行程的其他視窗（模擬器自己的浮動工具列）不算擋住。
        自動操控點擊前與錄製時都用這個確認：點下去的東西必須真的是遊戲，不是壓在上面的別人。"""
        hwnd = C.window_at(x, y)
        if not hwnd:
            return None
        pid = C.window_pid(hwnd)
        if pid == os.getpid():
            return "輔助器自己的視窗"
        anchor = self.cfg.window_anchor
        if anchor:
            game = C.find_window(anchor["title"])
            if game and pid != C.window_pid(game):
                return f"別的程式的視窗「{C.window_title(hwnd) or '（無標題）'}」"
        return None

    @staticmethod
    def make_anchor(region: V.Region) -> Optional[dict]:
        """框選完成時試著記住範圍底下是哪個視窗。認不出來就回 None，退回畫面座標寫死的行為
        ——視窗鎖定是錦上添花，不該讓框選本身因此失敗。"""
        try:
            hwnd = C.window_at(region.left + region.width // 2, region.top + region.height // 2)
            if not hwnd or C.window_pid(hwnd) == os.getpid():
                return None
            title = C.window_title(hwnd)
            rect = C.window_rect(hwnd)
            if not title or rect is None:
                return None
            return {"title": title, "dx": region.left - rect[0], "dy": region.top - rect[1],
                    "w": rect[2] - rect[0], "h": rect[3] - rect[1]}
        except OSError:
            return None


# ---------------------------------------------------------------- 引擎

@dataclass
class Status:
    kind: str                       # ok / settling / unknown / empty / waiting / error / stopped
    message: str = ""
    label: Optional[str] = None     # 穩定辨識出的狀態名稱
    distance: float = 0.0
    margin: float = 0.0
    rule: Optional[dict] = None     # 這個狀態現有的規則（給預覽畫目標標記）
    image: Optional[np.ndarray] = None
    region: Optional[V.Region] = None
    autoplay: bool = False
    actions_done: int = 0
    acted: str = ""                 # 這一輪剛做的動作（UI 記進「最近動作」）
    overlay: Optional[list] = None  # 總覽：[(x, y, 種類名稱, 是不是下一個要點的)]，範圍內像素，預覽圖上標出來給使用者看


class Engine:
    """擷取 → 辨識 → （自動操控時）依規則點擊。"""

    def __init__(self, cfg: Config,
                 grabber=None,
                 clicker: Optional[Callable[..., None]] = None,
                 guard: Optional[Callable[[int, int], Optional[str]]] = None,
                 busy: Optional[Callable[[], bool]] = None,
                 swiper: Optional[Callable[..., None]] = None) -> None:
        self.cfg = cfg
        self.db = V.StateDB.load(TEMPLATES_DIR, cfg.tolerance, cfg.min_margin)
        self.rules = RuleBook.load()
        # 測試會塞假的擷取／點擊；有塞就照用，不管 control_mode
        self._injected = dict(grabber=grabber, clicker=clicker, busy=busy, swiper=swiper)
        # guard(x, y)：點之前最後一道檢查，回傳字串代表「不能點」（例如目標點被自己的視窗蓋住）
        self.guard = guard
        self.adb: Optional[A.AdbBackend] = None
        self.apply_backend()
        self.planner: Optional[O.MapPlanner] = None
        self.autoplay = False
        self.actions_done = 0
        self.last_image: Optional[np.ndarray] = None
        self.region: Optional[V.Region] = None
        self.route = R.RouteTracker.load(ROUTE_PATH, enabled=bool(cfg.route_enabled))
        self._defeats: Dict[Tuple[str, int], int] = {}   # 路線上每一步打輸幾次
        self.need_revive = False      # VS 看到陣亡英雄、關掉了：回到總覽要先用 Heart of Norheim 復活
        self._reload_at = 0.0
        self._file_stamp = self._stamp()
        self._route_check = False     # 剛開自動：第一個總覽畫面要看看是不是新的一輪（起始畫面）
        self.rebuild()

    @property
    def background(self) -> bool:
        """現在是不是背景操控（adb）。"""
        return self.adb is not None

    def apply_backend(self) -> None:
        """依 cfg.control_mode 換擷取／點擊的方式（面板切換「操控方式」時也叫這裡）。
        adb＝畫面跟點擊都走 adb：範圍是虛擬的、不會被別的視窗擋、使用者按著滑鼠也不影響（不搶滑鼠）。"""
        inj = self._injected
        if self.cfg.control_mode == "adb" and inj["grabber"] is None:
            size = (self.cfg.region["width"], self.cfg.region["height"]) if self.cfg.region else A.DEFAULT_WORK_SIZE
            self.adb = A.AdbBackend(self.cfg.adb_serial, size, chooser=self._choose_instance)
            self.grabber = self.adb
            self.tracker = A.AdbTracker(self.adb)
            self._click = inj["clicker"] or self.adb.click
            self._swipe = inj["swiper"] or self.adb.swipe
            self._busy = inj["busy"] or (lambda: False)
        else:
            self.adb = None
            self.grabber = inj["grabber"] or V.ScreenGrabber()
            self.tracker = RegionTracker(self.cfg)
            self._click = inj["clicker"] or C.click
            self._swipe = inj["swiper"] or C.swipe       # 總覽拖地圖用
            # busy()：使用者正在操作滑鼠（例如拖地圖）就回 True，這一輪先不點，別插進他的動作中間
            self._busy = inj["busy"] or C.mouse_button_down

    def _choose_instance(self, serials, capture) -> Optional[str]:
        """好幾台模擬器都開著 ADB：挑畫面認得出來（是這個遊戲的已知畫面）的那台；都認不出來就用第一台。"""
        w, h = (self.adb.work_w, self.adb.work_h) if self.adb else A.DEFAULT_WORK_SIZE
        for s in serials:
            try:
                img = np.asarray(Image.fromarray(capture(s)).resize((w, h), Image.BILINEAR))
            except (OSError, ValueError):
                continue
            if self.db.match(img).label is not None:
                return s
        return None

    def rebuild(self) -> None:
        """設定改過之後重新套用，並清掉判斷流程的暫存。"""
        self.db.tolerance = self.cfg.tolerance
        self.db.min_margin = self.cfg.min_margin
        self.route.enabled = bool(self.cfg.route_enabled)
        self.planner = O.MapPlanner(
            O.NodeClassifier.load(O.NODES_DIR),
            priority=self.cfg.node_priority or O.DEFAULT_PRIORITY,
            scan_moves=self.cfg.scan_moves or None,
            max_scan_drags=self.cfg.scan_max_drags,
            stuck_timeout=self.cfg.map_stuck_timeout,
            hero_finder=O.HeroFinder.load(O.HEROES_DIR),
            route=self.route,
            switch_scale=self.cfg.switch_scale,
            memory=R.SwitchMemory.load(SWITCH_PATH) if self.cfg.switch_memory else None)
        self._reset_flow()

    def _reset_flow(self) -> None:
        self._confirm_label: Optional[str] = None
        self._confirm_n = 0
        self._unknown_since: Optional[float] = None
        self._acted_label: Optional[str] = None   # 上次動手時的狀態；離開這個狀態就清掉
        self._acted_at = 0.0
        self._retry = 0
        self._note = ""
        self._vs_dead_prev = False       # 已經處理過「VS 畫面有陣亡英雄」（同一個 VS 畫面只處理一次）
        self._defeat_label_prev = False  # 上一個穩定畫面是不是戰敗（戰敗畫面會連續好幾輪，只處理一次）
        self._victory_note = ""       # 勝利畫面記的戰鬥場數；跟同一步的「點退出」動作記錄合併，不能被蓋掉
        self._route_note = ""         # 路線走完一步／從頭開始之類的記錄，同樣跟這一步的動作記錄合併
        self._in_map = False
        if self.planner is not None:
            self.planner.reset()

    def _status(self, kind: str, message: str = "", **kw) -> Status:
        return Status(kind, message, autoplay=self.autoplay, actions_done=self.actions_done,
                      region=self.region, **kw)

    def start_autoplay(self) -> None:
        self.autoplay = True
        self._acted_label = None
        self._retry = 0
        self._route_check = True
        if self.planner is not None:
            self.planner.reset()

    def stop_autoplay(self) -> None:
        self.autoplay = False
        self._acted_label = None
        self._retry = 0
        # 點了還沒確認的那一步作廢：停手之後你自己點的東西不能被算成路線的進度
        self.route.pending = None

    def _check_new_run(self, label: str) -> None:
        """路線要不要從頭：開自動後第一個總覽是「起始畫面」（你每一輪都從那裡開始）＝新的一輪；
        進度是很久以前的（一輪走不了 STALE_HOURS 那麼久）也當作新的一輪；路線走完之後再看到起始畫面一樣從頭。"""
        r = self.route
        if not r.enabled:
            self._route_check = False
            return
        first, self._route_check = self._route_check, False
        why = ""
        if r.started and label == START_LABEL and (first or r.finished):
            why = "看到起始畫面＝新的一輪"
        elif first and r.started and r.stale():
            why = f"上次的進度超過 {R.STALE_HOURS:.0f} 小時沒動，當作新的一輪"
        if why:
            r.restart()
            self._route_note = f"路線：{why}，從第1段重新照路線走"
        elif first and r.finished:
            self._route_note = "路線：上一輪已經走完（之後照原本規則）。這是新的一輪的話，請在起始畫面重開自動，或按面板「路線 從頭」"

    # -- 給 UI 用 --
    def snapshot(self) -> np.ndarray:
        """現在範圍內的畫面。範圍不能用（沒框選／視窗縮小）就丟 ValueError 附原因。"""
        region, err = self.tracker.current()
        if region is None:
            raise ValueError(err)
        return self.grabber.grab(region)

    def teach(self, label: str, image: np.ndarray,
              click: Optional[Tuple[float, float]], overview: bool = False) -> Tuple[bool, int]:
        """把目前畫面存成 label 的一張樣板，並設定這個狀態的動作（click＝點一下、overview＝地城總覽自動、都沒有＝等待）。

        回傳 (這張是不是新樣板, 這個狀態現在共有幾張)。跟既有樣板幾乎一樣的畫面
        不會重複收，但動作規則一律更新。"""
        err = label_error(label)
        if err:
            raise ValueError(err)
        added = self.db.add(label, image)
        self.db.save(TEMPLATES_DIR)
        if overview:
            self.rules.set_map(label)
        elif click is None:
            self.rules.set_wait(label)
        else:
            self.rules.set_click(label, click[0], click[1])
        self.rules.save()
        self._reset_flow()
        return added, self.db.count_for(label)

    def forget(self, label: str) -> None:
        if label_error(label):
            return
        self.db.remove_label(label)
        self.db.save(TEMPLATES_DIR)
        shutil.rmtree(os.path.join(TEMPLATES_DIR, label), ignore_errors=True)
        self.rules.remove(label)
        self.rules.save()
        self._reset_flow()

    @staticmethod
    def _stamp() -> tuple:
        out = []
        for p in (os.path.join(TEMPLATES_DIR, "index.json"), RULES_PATH):
            try:
                out.append(os.path.getmtime(p))
            except OSError:
                out.append(None)
        return tuple(out)

    def _maybe_reload(self) -> None:
        """樣板庫或規則檔在外面被改了（例如另外用工具教了新畫面）就重新載入，不用重開輔助器。每 2 秒看一次。"""
        now = time.monotonic()
        if now - self._reload_at < 2.0:
            return
        self._reload_at = now
        st = self._stamp()
        if st != self._file_stamp:
            self._file_stamp = st
            self.db = V.StateDB.load(TEMPLATES_DIR, self.cfg.tolerance, self.cfg.min_margin)
            self.rules = RuleBook.load()

    # -- 主迴圈的一步 --
    def step(self) -> Status:
        self._maybe_reload()
        if self.autoplay and C.panic_pressed(self.cfg.panic_key):
            self.stop_autoplay()
            return self._status("stopped", f"偵測到 {self.cfg.panic_key}，自動操控已停止。")

        region, err = self.tracker.current()
        if region is None:
            self.region = None
            self._reset_flow()
            return self._status("waiting", err or "")
        self.region = region

        try:
            img = self.grabber.grab(region)
        except (ValueError, OSError) as e:
            self.stop_autoplay()
            return self._status("error", f"抓不到畫面：{e}")
        self.last_image = img

        if len(self.db) == 0:
            return self._status("empty", "樣板庫是空的。切到遊戲裡的某個畫面，按「② 標記畫面」教它認。",
                                image=img)

        now = time.monotonic()
        m = self.db.match(img)

        if m.label is None:
            self._confirm_label, self._confirm_n = None, 0
            self._acted_label = None     # 已經離開上次動手時的畫面
            if self._unknown_since is None:
                self._unknown_since = now
            if self.autoplay and now - self._unknown_since > self.cfg.unknown_timeout:
                self.stop_autoplay()
                return self._status(
                    "stopped",
                    f"連續 {self.cfg.unknown_timeout:.0f} 秒認不得畫面，自動操控已停止。"
                    f"這個畫面可能還沒教過——按「② 標記畫面」教它。",
                    distance=m.distance, margin=m.margin, image=img)
            return self._status("unknown", "認不得這個畫面（還沒教過，或正在轉場）。",
                                distance=m.distance, margin=m.margin, image=img)
        self._unknown_since = None

        if m.label != self._acted_label:
            self._acted_label = None     # 換了新畫面，重試計數歸零
        if m.label == self._confirm_label:
            self._confirm_n += 1
        else:
            self._confirm_label, self._confirm_n = m.label, 1

        rule = self.rules.get(m.label)
        common = dict(label=m.label, distance=m.distance, margin=m.margin, rule=rule, image=img)
        if self._confirm_n < self.cfg.stable_reads:
            return self._status("settling", "畫面變動中…", **common)

        if self.autoplay:
            special = self._revive_flow(m.label, rule, region, img)
            if special is not None:
                return self._finish(special, common)

        if m.label in STAGE_LABELS:
            self.planner.new_stage()
            self.route.new_stage()

        if rule is not None and rule["action"] == "map":
            if self.autoplay and (self._route_check or self.route.finished):
                self._check_new_run(m.label)
            return self._step_map(region, img, now, common)
        if self._in_map:
            # 離開總覽（進了彈窗／戰鬥）＝剛剛那一下有效，重置「點了沒反應」的記錄；照路線點的那一步算走完
            self._in_map = False
            done = self.route.confirm() if self.autoplay else None
            self.planner.left_overview()
            if done is not None:
                side, st = done
                if self.route.finished:
                    self._route_note = f"路線：{st.name(side)} 走完——整條路線都走完了，之後照原本規則"
                else:
                    self._route_note = (f"路線：{st.name(side)}（{st.what()}）有反應，算走完；"
                                        f"{self.route.describe_side(O.LEFT)}；{self.route.describe_side(O.RIGHT)}")
        if m.label == DEFEAT_LABEL and not self._defeat_label_prev and self.autoplay:
            self._defeat_label_prev = True
            failure = self._on_defeat()
            if failure is not None:
                self._act(m.label, rule, region)          # 先按 Quit 離開戰敗畫面再停手
                self.stop_autoplay()
                return failure
        elif m.label != DEFEAT_LABEL:
            self._defeat_label_prev = False
        if m.label == VICTORY_LABEL:
            side = self.planner.note_victory()
            if side:
                f = self.planner.fights
                self._victory_note = (f"戰鬥勝利：{O.SIDE_NAMES[side]}記一場（自上次治療：左隊 {f[O.LEFT]} 場、右隊 {f[O.RIGHT]} 場）")

        if self.autoplay:
            failure = self._act(m.label, rule, region)
            if failure is not None:
                return failure
        note, self._note = self._note, ""
        vnote, self._victory_note = self._victory_note, ""
        if vnote:
            note = f"{vnote}；{note}" if note else vnote
        rnote, self._route_note = self._route_note, ""
        if rnote:
            note = f"{rnote}；{note}" if note else rnote
        return self._status("ok", "", acted=note, **common)

    def _finish(self, special, common: dict) -> Status:
        if isinstance(special, Status):
            return special
        note, self._note = self._note, ""
        return self._status("ok", "", acted=note, **common)

    def _revive_flow(self, label: str, rule: Optional[dict], region: V.Region, img: np.ndarray):
        """陣亡英雄的處理（使用者 11/22 教的步驟）。回 None＝不關這裡的事、照一般流程；回 True／Status＝這一輪已處理。"""
        if label != VS_LABEL:
            self._vs_dead_prev = False
        if label == VS_LABEL and dead_cards(img, VS_CARDS):
            if not self._vs_dead_prev:
                self._vs_dead_prev = True
                self.need_revive = True
                undone = self.route.undo_last() if self.route.active else None
                if undone is not None:
                    self.route.set_retry(undone[0])
                self._planner_retry_reset()
                self._victory_note = (f"VS 畫面有 {dead_cards(img, VS_CARDS)} 位英雄陣亡（灰色），先關掉去用 Heart of Norheim 復活"
                                      + (f"；路線退回 {undone[1].name(undone[0])} 等一下重打" if undone else ""))
            fail = self._act(label, {"action": "click", "x": VS_CLOSE[0], "y": VS_CLOSE[1]}, region)
            if self._victory_note:
                self._note = f"{self._victory_note}；{self._note}"
                self._victory_note = ""
            return fail or True
        if label == REVIVE_LABEL:
            dead = dead_cards(img, REVIVE_CARDS)
            if dead:
                fail = self._act(label, {"action": "click", "x": REVIVE_USE[0], "y": REVIVE_USE[1]}, region)
                if self._note:
                    self._note = f"復活視窗：{dead} 位陣亡，點 Tap to use；{self._note}"
                return fail or True
            self.need_revive = False
            fail = self._act(label, {"action": "click", "x": REVIVE_CLOSE[0], "y": REVIVE_CLOSE[1]}, region)
            if self._note:
                self._note = f"英雄都復活了，關掉復活視窗；{self._note}"
            return fail or True
        if label == DONE_LABEL and rule is not None and rule["action"] == "click":
            fail = self._act(label, rule, region)
            if fail is not None:
                return fail
            if self._note:          # 點下去了：這一輪地城打完，自動停手
                note, self._note = self._note, ""
                self.stop_autoplay()
                return self._status("stopped", f"這一輪永夜迷城打完了（Challenge Complete），自動操控已停止。「{label}」→ {note.split('→')[-1].strip()}",
                                    label=label, acted=f"「{label}」→ {note.split('→')[-1].strip()}；這一輪打完了，自動停止")
            return True
        if self.need_revive and rule is not None and rule["action"] == "map":
            fail = self._act(label, {"action": "click", "x": HEART_BTN[0], "y": HEART_BTN[1]}, region)
            if self._note:
                self._note = f"有英雄陣亡：點右下 Heart of Norheim；{self._note}"
            return fail or True
        return None

    def _planner_retry_reset(self) -> None:
        self.planner._retry_clicked = False

    def _on_defeat(self) -> Optional[Status]:
        """打輸了：這一場不算那隊打過、路線退回那一步（下次回到總覽重打同一個）；同一步連輸 MAX_DEFEATS 次就停手。"""
        self.planner._pending = None
        undone = self.route.undo_last() if self.route.active else None
        if undone is None:
            self._victory_note = "戰鬥打輸了（不在路線上），回總覽照規則繼續"
            return None
        side, st = undone
        self.route.set_retry(side)
        self.planner._retry_clicked = False
        key = (side, self.route.idx[side])
        self._defeats[key] = self._defeats.get(key, 0) + 1
        n = self._defeats[key]
        if n >= MAX_DEFEATS:
            return self._status("stopped", f"路線 {st.name(side)}（{st.what()}）連續打輸 {n} 次，先停下來——"
                                           f"隊伍可能太弱或血不夠，請你看一下（換陣容／補血）再開自動。")
        self._victory_note = f"戰鬥打輸了：路線退回 {st.name(side)}（{st.what()}），回總覽重打一次（第 {n} 次輸）"
        return None

    def _step_map(self, region: V.Region, img: np.ndarray, now: float, common: dict) -> Status:
        st = self._step_map_inner(region, img, now, common)
        rnote, self._route_note = self._route_note, ""      # 路線從頭開始之類的記錄（_check_new_run）
        if rnote:
            st.acted = f"{rnote}；{st.acted}" if st.acted else rnote
        return st

    def _step_map_inner(self, region: V.Region, img: np.ndarray, now: float, common: dict) -> Status:
        """地城總覽：看圖釘、認節點、挑一個點；沒圖釘就拖地圖。關著自動時只顯示「會怎麼做」。"""
        self._in_map = True
        view = self.planner.observe(img, now)
        if self.planner.route_event:
            self._route_note = (f"{self._route_note}；{self.planner.route_event}" if self._route_note
                                else self.planner.route_event)
            self.planner.route_event = None
        plan = self.planner.decide(view, img.shape, now)
        def tag(n):
            name = O.KIND_NAMES.get(n.kind, "？") if n.kind else "？"
            side = self.planner.sides.get((n.x, n.y))       # 只有畫面上有治療可選時才會判斷隊伍
            return f"{name}·{O.SIDE_NAMES[side][0]}" if side else name

        overlay = [(n.x, n.y, tag(n), plan.node is not None and n.x == plan.node.x and n.y == plan.node.y)
                   for n in view.nodes]
        overlay += list(self.planner.extra_overlay)        # 沒圖釘時：隊伍位置與鄰接的節點
        info = dict(common, overlay=overlay)
        seen = O.describe_nodes(view.nodes) if view.clean else "畫面上疊著彈窗"
        f = self.planner.fights
        seen += f"｜自上次治療：左隊 {f[O.LEFT]} 場、右隊 {f[O.RIGHT]} 場"

        if not self.autoplay:
            what = "" if plan.kind == "wait" else "　開自動會：" + plan.reason
            return self._status("ok", f"{seen}{what}" if plan.kind != "wait" else f"{seen}（{plan.reason}）", **info)

        if plan.kind == "stop":
            self.stop_autoplay()
            shot = self._save_frame(img, "stop")
            return self._status("stopped", plan.reason + (f"［畫面存檔 {shot}］" if shot else ""), **info)
        if plan.kind == "wait":
            return self._status("ok", f"{seen}（{plan.reason}）", **info)

        # click / drag：跟一般規則同樣的三道保險（使用者在操作滑鼠、動作間隔、點位被別的視窗蓋住）
        fast = plan.fast or self.planner.switching       # 換邊找另一隊：拖完、找到要點，都不用等那麼久
        if self._busy() or now - self._acted_at < (self.cfg.switch_interval if fast else self.cfg.map_interval):
            return self._status("ok", seen, **info)
        h, w = img.shape[:2]
        pts = [(plan.x, plan.y)] + ([(plan.x1, plan.y1)] if plan.kind == "drag" else [])
        pts = [(region.left + min(max(x, 0), w - 1), region.top + min(max(y, 0), h - 1)) for x, y in pts]
        if self.guard is not None:
            for x, y in pts:
                blocked = self.guard(x, y)
                if blocked:
                    self.stop_autoplay()
                    return self._status("stopped", blocked, **info)
        try:
            if plan.kind == "click":
                self._click(pts[0][0], pts[0][1], hold=self.cfg.click_hold)
            else:
                self._swipe(pts[0][0], pts[0][1], pts[1][0], pts[1][1], duration=0.35 if fast else 0.5, steps=25)
        except C.InputError as e:
            self.stop_autoplay()
            return self._status("error", str(e), **info)
        except OSError as e:
            self.stop_autoplay()
            return self._status("error", f"送出{'點擊' if plan.kind == 'click' else '拖曳'}失敗：{e}", **info)

        if plan.kind != "click":
            side = None
        elif plan.side is not None or plan.node is None or plan.node.pinless:
            side = plan.side          # 沒圖釘的節點／照路線點的：規劃時就從隊伍腳下的平台判斷好了
        else:
            side = self.planner.side_for(plan.node, img)
        self.planner.commit(plan, img.shape, now, side)
        self._acted_at = time.monotonic()
        self.actions_done += 1
        self._confirm_label, self._confirm_n = None, 0     # 畫面即將改變，重新確認
        self._acted_label = None
        told = plan.route is not None or plan.side is not None or (plan.node is not None and plan.node.pinless)
        who = f"（{O.SIDE_NAMES[side]}）" if side and not told else ""     # 沒圖釘／路線的說明本來就寫了隊伍
        if plan.kind == "click":
            note = f"總覽：{plan.reason}{who} → ({pts[0][0]}, {pts[0][1]})｜{seen}"
        else:
            shot = self._save_frame(img, f"drag{self.planner.drags:02d}")     # 拖之前看到的畫面（為什麼決定往這邊拖）
            note = f"總覽：{plan.reason}" + (f"［畫面存檔 {shot}］" if shot else "")
        return self._status("ok", seen, acted=note, **info)

    def _save_frame(self, img: np.ndarray, tag: str) -> str:
        """把總覽當下的畫面存成 debug_frames/月日_時分秒毫秒_tag.jpg，只留最新 cfg.debug_frames 張。回傳檔名（沒存回空字串）。
        9/27 使用者回報「右隊打完不會回頭找左隊」時，手上只有文字記錄、沒有畫面，看不出它當時到底看到什麼——之後就有了。"""
        keep = int(self.cfg.debug_frames or 0)
        if keep <= 0:
            return ""
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            name = datetime.datetime.now().strftime("%m%d_%H%M%S_%f")[:-3] + f"_{tag}.jpg"
            Image.fromarray(img).save(os.path.join(DEBUG_DIR, name), quality=85)
            old = sorted(f for f in os.listdir(DEBUG_DIR) if f.endswith(".jpg"))
            for f in old[:-keep]:
                os.remove(os.path.join(DEBUG_DIR, f))
            return name
        except (OSError, ValueError):
            return ""         # 存不了（磁碟滿、權限）不影響自動操控

    def _act(self, label: str, rule: Optional[dict], region: V.Region) -> Optional[Status]:
        """依規則動手。回傳非 None 代表出事、已停手，要顯示給使用者。"""
        if rule is None or rule["action"] != "click":
            return None
        if self._busy():
            return None     # 使用者按著滑鼠：讓他先做完。不算重試、不動計數，畫面沒變下一輪再看
        now = time.monotonic()
        if now - self._acted_at < self.cfg.action_interval:
            return None

        if label == self._acted_label:
            # 上次點完畫面還是同一個狀態 → 那一下可能沒生效。等一下再試，
            # 連續幾次都沒反應就停手，免得對著沒反應的視窗一直空點。
            if now - self._acted_at < self.cfg.retry_after:
                return None
            self._retry += 1
            if self._retry > self.cfg.max_retries:
                self.stop_autoplay()
                return self._status(
                    "stopped",
                    f"在「{label}」連續點了 {self.cfg.max_retries + 1} 次畫面都沒變化，已自動停止。"
                    f"請確認模擬器視窗在最上層、沒有被別的視窗擋住。", label=label)
        else:
            self._retry = 0

        x = region.left + int(round(rule["x"] * (region.width - 1)))
        y = region.top + int(round(rule["y"] * (region.height - 1)))
        if self.guard is not None:
            blocked = self.guard(x, y)
            if blocked:
                self.stop_autoplay()
                return self._status("stopped", blocked, label=label)
        try:
            self._click(x, y, hold=self.cfg.click_hold)
        except C.InputError as e:
            self.stop_autoplay()
            return self._status("error", str(e), label=label)
        except OSError as e:
            self.stop_autoplay()
            return self._status("error", f"送出點擊失敗：{e}", label=label)

        self._acted_label = label
        self._acted_at = time.monotonic()
        self.actions_done += 1
        self._confirm_label, self._confirm_n = None, 0   # 畫面即將改變，重新確認
        self._note = f"「{label}」→ 點 ({x}, {y})"
        return None


class Worker(threading.Thread):
    """在背景跑 Engine.step()，把結果丟進 queue。辨識與截圖放在 UI 執行緒會讓視窗卡住。"""

    def __init__(self, engine: Engine, lock: threading.Lock, out: "queue.Queue[Status]") -> None:
        super().__init__(daemon=True)
        self.engine = engine
        self.lock = lock
        self.out = out
        self._stop_evt = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def stop(self) -> None:
        self._stop_evt.set()
        self._resume.set()    # 免得停在 wait() 裡出不來

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self._resume.wait()
            if self._stop_evt.is_set():
                break
            with self.lock:
                try:
                    status = self.engine.step()
                except Exception as e:    # 背景執行緒炸掉就整個沒反應，一律轉成畫面上的訊息
                    self.engine.stop_autoplay()
                    status = Status("error", f"內部錯誤：{type(e).__name__}: {e}")
                poll = self.engine.cfg.poll_ms
            self.out.put(status)
            time.sleep(max(0.05, poll / 1000.0))


__all__ = [
    "APP_DIR", "CONFIG_PATH", "TEMPLATES_DIR", "RULES_PATH", "SESSIONS_DIR", "SWITCH_PATH",
    "Config", "RuleBook", "RegionTracker", "Status", "Engine", "Worker", "label_error",
]
