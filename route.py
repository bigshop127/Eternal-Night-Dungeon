"""使用者指定的固定路線（2026-10-01）：前三個遊戲階段（你截圖的第 1~4 段）每一步都照你畫的紅線走，之後照原本的規則。

你 10/01 傳的 13 張圖：前 6 張是原始地圖、後 7 張紅線是要走的路，對過之後整理成下面的 ROUTE
（對照圖在 reference_notes/21~26_路線核對_*.jpg，編號跟這裡的「第N段 左/右 第M步」一樣）。
你的規則（10/01 原話整理）：
  * 這整條路線**之後每一輪都照走**；走完（第 4 段最上面兩個裝置）就照原本的設定走。
  * 第 2 段「左邊先走」＝左隊整段走完、碰到裝置之後，才回頭走右邊；左隊點了那個裝置，右邊的荊棘就會消失。
  * 其他地方同理：**一隊碰到裝置之後，就換去另一半操作**。兩隊都能走的時候誰先，照舊（寶箱優先、治療要等另一隊打過）。
  * 走到某一步，畫面上找不到該走的那個節點（或種類對不上）＝停下來叫你，不要自己改選別的。

每一步記「種類＋方向」。方向是相對隊伍站的平台：正前（F）、左前（L）、右前（R）——地圖是固定的，同一個位置可能有兩個同種類的
節點（例如起點野蠻人前面正前方、左前方各一個寶箱），只記種類會選錯。方向是用你 9/20 那一輪 BlueStacks 錄製量的：
鏡頭停在隊伍上時，正前方的節點在 (0, −205)、左前 (−305, −125)、右前 (+305, −125)（除掉透視縮放後的位移，見 overview.direction_of），
9/20 錄製走過的步數（第 1 段左隊全部、第 2~4 段大部分）跟這裡每一步的方向都對得上；錄製跟你的路線不一樣的四個地方
（第 1 段右 1、第 3 段右 3、第 4 段左 7 與右 8）錄製正好走了另一個方向的同種節點，這就是要記方向的原因。
第 2 段右隊最後多一步「空格」：荊棘消失後留下的空平台，要點它走過去（不跳彈窗；9/20 錄製第 76 次點擊就是點它），
你的紅線沒畫，但圖 3 右上那叢荊棘就在路線上。

這裡只有路線資料與進度記帳（純邏輯、不碰畫面），在畫面上找節點的是 overview.MapPlanner._decide_route。
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Dict, List, NamedTuple, Optional, Tuple

import overview as O

FWD, LFT, RGT = "F", "L", "R"
DIR_NAMES = {FWD: "正前", LFT: "左前", RGT: "右前"}
_KIND_CODES = {"C": O.CHEST, "M": O.ENEMY, "H": O.HEAL, "D": O.DEVICE, "W": O.WALK}


class Step(NamedTuple):
    stage: int      # 遊戲的第幾階段（兩邊的裝置都點了、跳出「提示框」才進下一階段）
    seg: int        # 你截圖的第幾段（第 2、3 段在遊戲裡是同一階段：左隊第 2 段的裝置只是打開右邊的荊棘）
    num: int        # 這一段裡的第幾步（跟路線核對圖上的編號一樣）
    kind: str       # O.CHEST／O.ENEMY／O.HEAL／O.DEVICE（泉水算治療）
    dir: str        # FWD／LFT／RGT

    def name(self, side: str) -> str:
        return f"第{self.seg}段 {O.SIDE_NAMES[side][0]}{self.num}"

    def what(self) -> str:
        return f"{DIR_NAMES[self.dir]}的{O.KIND_NAMES[self.kind]}"


def _seg(stage: int, seg: int, spec: str) -> List[Step]:
    """spec：每一步兩個字母（種類 C寶箱/M怪物/H治療/D裝置＋方向 F/L/R），空白隔開。"""
    out = []
    for i, tok in enumerate(spec.split(), 1):
        out.append(Step(stage, seg, i, _KIND_CODES[tok[0]], tok[1]))
    return out


# 你畫的路線。每段後面的註解＝你截圖裡經過的東西（「泉水」就是藍色泉水，算治療）。
ROUTE: Dict[str, List[Step]] = {
    O.LEFT: (
        _seg(1, 1, "CF MF CF MF HF MF DL")           # 寶箱 怪物 寶箱 怪物 治療 怪物 裝置
        + _seg(2, 2, "CF CL MF DR")                  # 寶箱 寶箱 怪物 裝置（這個裝置打開右邊的荊棘）
        + _seg(2, 3, "CL CF HR MF DR")               # 寶箱 寶箱 治療 怪物 裝置
        + _seg(3, 4, "MF CF HF MF ML HF MR DL")      # 怪物 寶箱 泉水 怪物 怪物 治療 怪物 裝置
    ),
    O.RIGHT: (
        _seg(1, 1, "CL MF HR MF DL")                 # 寶箱 怪物 治療 怪物 裝置
        + _seg(2, 2, "MF HF MF WF")                  # 怪物 治療 怪物 空格（荊棘：左隊點了裝置才會消失，消失後那格點了直接走過去、不跳彈窗）
        + _seg(2, 3, "MF HF MF DR")                  # 怪物 泉水 怪物 裝置
        + _seg(3, 4, "CF HF MF CL CR MR CL MF DL")   # 寶箱 治療 怪物 寶箱 寶箱 怪物 寶箱 怪物 裝置
    ),
}
# 某一段要哪一隊先整段走完（走到那段最後的裝置）另一隊才能開始：你 10/01「第二段左邊先走」
FIRST: Dict[int, str] = {2: O.LEFT}

STALE_HOURS = 12.0      # 進度超過這麼久沒動＝上一輪留下來的（一輪走不了這麼久），開自動時當作新的一輪


class RouteTracker:
    """兩隊各自走到路線的第幾步、誰在等誰，存成 route_progress.json（輔助器重開也接得上）。

    「能不能走」（can_act）照你的規則：
      1. 遊戲階段要同步：另一隊這一階段之前的步數都走完了，這隊才能走下一階段的步（兩邊裝置都點了才會進下一階段）。
      2. FIRST：第 2 段左隊整段走完（含裝置），右隊才開始第 2 段。
      3. 碰到裝置就換另一隊：一隊點了裝置之後就停著，等另一隊也點了裝置（或這一階段兩隊都走完）才能再走。
    進度只在「點了之後畫面真的離開總覽」（跳出前往／守衛者彈窗）才前進一步——點了沒反應不算。"""

    def __init__(self, path: Optional[str], route: Optional[Dict[str, List[Step]]] = None,
                 first: Optional[Dict[int, str]] = None, enabled: bool = True) -> None:
        self.path = path
        self.route = route if route is not None else ROUTE
        self.first = dict(FIRST if first is None else first)
        self.enabled = enabled
        self.idx: Dict[str, int] = {O.LEFT: 0, O.RIGHT: 0}
        self.wait: Optional[str] = None             # 點了裝置、在等另一隊的那隊
        self.pending: Optional[Tuple[str, int]] = None   # 點了、還沒確認有反應的那一步 (隊, 第幾步)
        self.updated: Optional[str] = None          # 上次進度改變的時間（ISO 字串）
        self.last_actor: Optional[str] = None       # 最近一次真的走了一步的隊（回到總覽時鏡頭會對著它）

    # -- 進度 --
    def next(self, side: str) -> Optional[Step]:
        steps = self.route.get(side, [])
        i = self.idx.get(side, 0)
        return steps[i] if i < len(steps) else None

    @property
    def finished(self) -> bool:
        return all(self.idx.get(sd, 0) >= len(self.route.get(sd, [])) for sd in O.SIDES)

    @property
    def active(self) -> bool:
        return self.enabled and not self.finished

    def _done_through(self, side: str, pred) -> bool:
        """這隊符合 pred 的步都走完了嗎（路線是照順序走的，所以看最後一個符合的步在不在進度之前）。"""
        steps = self.route.get(side, [])
        last = max((i for i, st in enumerate(steps) if pred(st)), default=-1)
        return self.idx.get(side, 0) > last

    def stage_done(self, side: str, stage: int) -> bool:
        return self._done_through(side, lambda st: st.stage <= stage)

    def blocked(self, side: str) -> Optional[str]:
        """這隊現在不能走的原因；能走回 None。"""
        st = self.next(side)
        if st is None:
            return "路線走完了"
        other = O.other_side(side)
        if not self.stage_done(other, st.stage - 1):
            return f"等{O.SIDE_NAMES[other]}走完第 {st.stage - 1} 階段"
        lead = self.first.get(st.seg)
        if lead == other and not self._done_through(other, lambda s2: s2.seg <= st.seg):
            return f"第{st.seg}段要{O.SIDE_NAMES[other]}先整段走完"
        if self.wait == side:
            return f"已經點了裝置，等{O.SIDE_NAMES[other]}"
        return None

    def can_act(self, side: str) -> bool:
        return self.active and self.blocked(side) is None

    def mark(self, side: str) -> None:
        """點了這隊路線上的下一步（還沒確認有反應）。"""
        self.pending = (side, self.idx.get(side, 0))

    def confirm(self) -> Optional[Tuple[str, Step]]:
        """畫面離開總覽了＝剛剛點的那一步有效，進度往前一步。回傳 (隊, 走掉的那一步)；沒有待確認的回 None。"""
        if self.pending is None:
            return None
        side, i = self.pending
        self.pending = None
        if self.idx.get(side, 0) != i:
            return None          # 進度被手動改過了，這筆作廢
        st = self.route[side][i]
        self.idx[side] = i + 1
        self.last_actor = side
        if st.kind == O.DEVICE:
            # 碰到裝置就換另一隊；這一階段兩隊都走完了＝進下一階段，沒有誰在等誰
            self.wait = None if all(self.stage_done(sd, st.stage) for sd in O.SIDES) else side
        self._touch()
        self.save()
        return side, st

    def new_stage(self) -> None:
        """遊戲進了下一階段（提示框／五選一／獎勵輪盤）：不再有誰在等誰（第 2 段左隊那個裝置不會跳提示框，所以等待不會被它清掉）。"""
        if self.wait is not None and all(self.stage_done(sd, self._wait_stage()) for sd in O.SIDES):
            self.wait = None
            self.save()

    def _wait_stage(self) -> int:
        """在等的那隊最後點的裝置屬於第幾階段。"""
        side = self.wait
        i = self.idx.get(side, 0) - 1 if side else -1
        return self.route[side][i].stage if side and i >= 0 else 0

    def restart(self) -> None:
        self.idx = {O.LEFT: 0, O.RIGHT: 0}
        self.wait = None
        self.pending = None
        self.last_actor = None
        self._touch()
        self.save()

    def set_progress(self, left: int, right: int) -> None:
        """手動調整（面板「調整…」）：兩隊各自已經走完幾步。等待狀態依「剛走完的是不是裝置」推回來。"""
        self.idx = {O.LEFT: max(0, min(int(left), len(self.route[O.LEFT]))),
                    O.RIGHT: max(0, min(int(right), len(self.route[O.RIGHT])))}
        self.pending = None
        self.wait = None
        on_device = []
        for sd in O.SIDES:
            i = self.idx[sd] - 1
            if i >= 0 and self.route[sd][i].kind == O.DEVICE and not all(
                    self.stage_done(x, self.route[sd][i].stage) for x in O.SIDES):
                on_device.append((self.route[sd][i].seg, sd))
        if on_device:
            # 兩隊都停在裝置上：後點的那隊才是在等的（先點的那隊的等待被它解開了）。後點的＝段落比較後面的
            # （第 2 段左隊的裝置一定比右隊第 3 段的裝置早）；同一段分不出先後就取左隊
            self.wait = max(on_device, key=lambda t: (t[0], t[1] == O.LEFT))[1]
        self._touch()
        self.save()

    def stale(self, now: Optional[datetime.datetime] = None) -> bool:
        if not self.updated:
            return False
        try:
            t = datetime.datetime.fromisoformat(self.updated)
        except ValueError:
            return True
        now = now or datetime.datetime.now()
        return (now - t).total_seconds() > STALE_HOURS * 3600

    @property
    def started(self) -> bool:
        return any(self.idx.get(sd, 0) for sd in O.SIDES)

    def _touch(self) -> None:
        self.updated = datetime.datetime.now().isoformat(timespec="seconds")

    # -- 給人看 --
    def total(self, side: str) -> int:
        return len(self.route.get(side, []))

    def describe_side(self, side: str) -> str:
        st = self.next(side)
        if st is None:
            return f"{O.SIDE_NAMES[side]}走完"
        why = self.blocked(side)
        tail = f"（{why}）" if why else ""
        return f"{O.SIDE_NAMES[side]}下一步 {st.name(side)} {st.what()}{tail}"

    def summary(self) -> str:
        if not self.enabled:
            return "路線：已關閉（照原本規則選節點）"
        if self.finished:
            return "路線：已走完，之後照原本規則（新的一輪會從起點重來）"
        l, r = self.idx[O.LEFT], self.idx[O.RIGHT]
        return (f"路線：左 {l}/{self.total(O.LEFT)}、右 {r}/{self.total(O.RIGHT)}　"
                f"{self.describe_side(O.LEFT)}；{self.describe_side(O.RIGHT)}")

    def step_list(self, side: str) -> List[str]:
        return [f"{i + 1:2d}. {st.name(side)} {st.what()}" for i, st in enumerate(self.route.get(side, []))]

    # -- 存檔 --
    def save(self) -> None:
        if not self.path:
            return
        data = {"version": 1, "left": self.idx[O.LEFT], "right": self.idx[O.RIGHT], "wait": self.wait,
                "updated": self.updated, "last_actor": self.last_actor}
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass         # 存不了不影響這一輪（只是重開輔助器會接不上）

    @classmethod
    def load(cls, path: Optional[str], enabled: bool = True) -> "RouteTracker":
        r = cls(path, enabled=enabled)
        if not path or not os.path.exists(path):
            return r
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return r
        if not isinstance(data, dict):
            return r
        try:
            r.idx = {O.LEFT: max(0, min(int(data.get("left", 0)), r.total(O.LEFT))),
                     O.RIGHT: max(0, min(int(data.get("right", 0)), r.total(O.RIGHT)))}
        except (TypeError, ValueError):
            return cls(path, enabled=enabled)
        w = data.get("wait")
        r.wait = w if w in O.SIDES else None
        la = data.get("last_actor")
        r.last_actor = la if la in O.SIDES else None
        r.updated = data.get("updated") if isinstance(data.get("updated"), str) else None
        return r


class SwitchMemory:
    """換邊記憶（11/22）：每個換邊點（「鏡頭對著哪隊第幾步 → 要找哪隊第幾步」，見 overview.MapPlanner._switch_key_for）
    上次總共拖了多遠才找到另一隊（手指位移，範圍比例）。地圖固定、每一輪換邊的地方都一樣，下一輪直接照這個距離一次拖過去。
    存成 switch_memory.json；檔案壞了／刪掉＝重新學（第一輪照大步走法找），不影響其他功能。"""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self.data: Dict[str, Tuple[float, float]] = {}

    def get(self, key: Optional[str]) -> Optional[Tuple[float, float]]:
        return self.data.get(key) if key else None

    def put(self, key: str, dx: float, dy: float) -> None:
        self.data[key] = (round(float(dx), 4), round(float(dy), 4))
        self.save()

    def forget(self, key: str) -> None:
        if self.data.pop(key, None) is not None:
            self.save()

    def clear(self) -> None:
        self.data = {}
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "switches": {k: list(v) for k, v in self.data.items()}}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Optional[str]) -> "SwitchMemory":
        m = cls(path)
        if not path or not os.path.exists(path):
            return m
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("switches", {})
            for k, v in raw.items():
                dx, dy = float(v[0]), float(v[1])
                if abs(dx) <= 5 and abs(dy) <= 10:          # 合理範圍外的（手動亂改）不用
                    m.data[str(k)] = (dx, dy)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError, TypeError, ValueError, IndexError):
            m.data = {}
        return m


__all__ = ["FWD", "LFT", "RGT", "DIR_NAMES", "Step", "ROUTE", "FIRST", "RouteTracker", "STALE_HOURS", "SwitchMemory"]
