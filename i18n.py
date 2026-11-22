"""介面語言（中文 / English）——跟水果2048輔助器同一套決定方式。

程式裡的字串都寫中文；英文版不改各處程式，而是在 Tk 設定文字的底層（_options、視窗標題、訊息框）統一翻譯，
所以按鈕、標籤、狀態列、動作記錄、對話框全部都會經過 T()，不會漏。
T() 的翻法（依序）：
  1. EXACT：整句對照（介面上的固定字句）
  2. PATTERNS：常見句型（路線步驟「第4段 右1」「正前的寶箱」、場數…），正規表示式
  3. PHRASES：片段對照（長的先換），處理由片段組起來的動態訊息
  4. 全形標點換成半形
test_i18n.py 拿 app_log.txt 的真實記錄＋程式裡所有中文字串當語料，確保翻完不留中文。

語言怎麼決定（先找到的算數）：
  1. 環境變數 EVERNIGHT_LANG=zh / en
  2. config.json 的 "language"
  3. 打包 exe 時寫進去的預設（_build_lang.py，tools/build_exe.ps1 產生）
  4. 都沒有就中文
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Tuple

SUPPORTED = ("zh", "en")


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.environ.get("ENDUNGEON_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))


def _detect() -> str:
    env = os.environ.get("EVERNIGHT_LANG", "").strip().lower()
    if env in SUPPORTED:
        return env
    try:
        with open(os.path.join(_app_dir(), "config.json"), "r", encoding="utf-8") as f:
            lang = str(json.load(f).get("language", "")).strip().lower()
        if lang in SUPPORTED:
            return lang
    except (OSError, ValueError, AttributeError):
        pass
    try:
        from _build_lang import LANG as built  # type: ignore
        if built in SUPPORTED:
            return built
    except ImportError:
        pass
    return "zh"


LANG = _detect()
_CJK = re.compile(r"[　-〿一-鿿＀-￯]")

# ---------------------------------------------------------------- 1. 整句
EXACT: Dict[str, str] = {
    "永夜迷城輔助器": "Evernight Labyrinth Assistant",
    "只有框起來的範圍會被辨識與操作　　Esc 取消": "Only the area you select is watched and clicked.    Esc = cancel",
    "方向鍵微調位置（Shift＝改大小，Ctrl＝一次 10px）　Enter 確定　重新拖曳可重框　Esc 取消":
        "Arrow keys nudge (Shift = resize, Ctrl = 10px steps)   Enter = OK   drag again to redo   Esc = cancel",
    "拖曳框出「模擬器裡的遊戲畫面」": "Drag a box around the game screen inside the emulator",
    "框太小了，請沿著遊戲畫面的邊緣重新拖一次": "Too small. Drag again along the edges of the game screen.",
    "還沒框出範圍——請先拖曳": "Nothing selected yet. Drag first.",
    "標記這個畫面": "Label this screen",
    "這是遊戲現在的畫面。在圖上點一下＝「看到這個畫面時要點的位置」。":
        "This is the game screen right now. Click on the picture = where to tap when this screen shows up.",
    "這個畫面叫做：": "Screen name:",
    "看到這個畫面就點一下（在上面的圖點出位置）": "Tap once when this screen appears (click the spot on the picture above)",
    "只辨識、不動作（戰鬥動畫等，等它自己過去）": "Recognise only, do nothing (battle animations etc.)",
    "地城總覽：自動找節點圖釘（沒圖釘就看隊伍前面）、依優先序點一個，都沒有就拖地圖":
        "Dungeon map: find node pins (or nodes in front of a team), tap one by priority, drag the map if none",
    "存檔": "Save", "取消": "Cancel", "刪除這個狀態": "Delete this screen", "刪除狀態": "Delete screen",
    "新的畫面名稱": "New screen name",
    "請在上面的圖點一下要點的位置，或改選「只辨識、不動作」":
        "Click the spot to tap on the picture above, or choose \"Recognise only, do nothing\"",
    "路線進度": "Route progress",
    "兩隊各自「已經走完」幾步（0＝還在起點）。照路線核對圖的編號算，裝置也算一步。":
        "How many steps each team has already done (0 = still at the start). Count like the route map; a device counts as a step.",
    "照固定路線走（關掉＝全程照原本的規則選節點）": "Follow the fixed route (off = pick nodes by the normal rules the whole way)",
    "全部歸零（新的一輪）": "Reset both to 0 (new run)",
    "這隊走完了": "This team is done",
    "左隊（野蠻人）": "Left team", "右隊（法師）": "Right team",
    "尚未框選範圍": "No area selected",
    "▶ 開始自動": "▶ Start auto", "■ 停止自動": "■ Stop auto",
    "● 開始錄製": "● Record", "■ 停止錄製": "■ Stop recording",
    "錄下你自己怎麼玩": "Record how you play",
    "背景操控，不會動到滑鼠": "Background control, your mouse stays free",
    "開啟後程式會自己點滑鼠": "When on, the program clicks the mouse by itself",
    "路線 從頭": "Route: restart", "調整…": "Adjust…", "最近動作": "Recent actions",
    "① 框選畫面範圍": "① Select area", "② 標記畫面": "② Label screen",
    "暫停": "Pause", "繼續": "Resume",
    "操控：背景 adb": "Mode: ADB", "操控：前景滑鼠": "Mode: mouse",
    "路線從頭": "Restart route",
    "把路線進度歸零、從第1段起點重新照路線走？\n\n（新的一輪會自動歸零：開自動時第一個畫面是起始畫面就會從頭。這個按鈕是給自動沒認出來的時候用的。）":
        "Reset route progress and start again from segment 1?\n\n(A new run resets by itself when the first screen after "
        "starting auto is the start screen. This button is for when that wasn't recognised.)",
    "路線已歸零，從第1段起點開始": "Route reset. Starting from segment 1.",
    "錄製中不能切換操控方式，請先停止錄製": "Stop recording before switching mode",
    "已切到背景操控：畫面跟點擊都走 adb，模擬器被蓋住或縮到最小都照跑，滑鼠可以拿去做別的事。":
        "Background mode: screen and taps go through ADB. Works even if the emulator is covered or minimised; your mouse is free.",
    "已切回前景操控：照框選的範圍擷取、用滑鼠點（模擬器要在最上層）。":
        "Foreground mode: reads the selected area and clicks with the mouse (keep the emulator on top).",
    "背景操控不用框選範圍（畫面直接從模擬器裡面抓）。要改回框選＋點滑鼠，按「操控」切換。":
        "Background mode doesn't need an area (it reads the screen from inside the emulator). Press \"Mode\" to switch to select-and-click.",
    "錄製中不能重框範圍，請先停止錄製": "Stop recording before selecting the area again",
    "在桌面上拖曳，框出模擬器裡的遊戲畫面（Esc 取消）": "Drag on the desktop around the game screen inside the emulator (Esc = cancel)",
    "已取消框選": "Selection cancelled",
    "錄製中請先停止錄製再標記畫面": "Stop recording before labelling a screen",
    "面板蓋在遊戲畫面範圍上，標記下來的畫面會把面板本身也拍進去。請先把模擬器視窗縮小、擺到螢幕左邊，再重新框選。":
        "The panel covers the game area, so the label would include the panel. Make the emulator smaller, move it left, and select again.",
    "抓不到畫面": "Can't read the screen",
    "自動操控已停止": "Auto stopped",
    "錄製中不能開自動操控，請先停止錄製": "Stop recording before starting auto",
    "請先框選畫面範圍": "Select the game area first",
    "請先按「② 標記畫面」，程式要看得懂畫面才能自己動": "Press \"② Label screen\" first. The program has to recognise screens before it can play.",
    "還沒有任何「點擊」動作。按「② 標記畫面」時在圖上點出要點的位置。":
        "No tap actions yet. In \"② Label screen\", click the spot to tap on the picture.",
    "面板擋住畫面了": "The panel is in the way",
    "這個面板正蓋在遊戲畫面範圍上。\n\n點擊會點到面板自己，而且擷取到的畫面也會被面板汙染。\n\n請把模擬器視窗縮小、擺到螢幕左邊（右邊留給面板），再重新框選範圍。":
        "This panel is covering the game area.\n\nClicks would hit the panel and the captured screen would include it.\n\n"
        "Make the emulator window smaller, move it to the left (leave the right side for the panel), and select the area again.",
    "開始自動操控": "Start auto",
    "錄製是記你用滑鼠點的位置，要在前景操控才錄得到。按「操控」切回前景再錄。":
        "Recording captures your mouse clicks, so it only works in foreground mode. Press \"Mode\" to switch first.",
    "面板蓋在遊戲畫面範圍上，請先把面板拖到範圍外再錄製": "The panel covers the game area. Move it away before recording.",
    "繼續偵測": "Watching again",
    "已暫停（畫面不會再更新）": "Paused (the screen won't update)",
    "範圍：尚未框選": "Area: not selected",
    "程式看到的畫面會顯示在這裡\n（只有你框選的範圍）": "What the program sees shows up here",
    "畫面變動中，等它穩定…": "Screen is changing, waiting for it to settle…",
    "畫面變動中…": "Screen is changing…", "等待中": "Waiting",
    "認不得這個畫面": "Unknown screen", "還沒教過畫面": "No screens taught yet", "出錯了": "Error",
    "已停止": "Stopped",
    "畫面範圍或樣板還沒準備好": "the game area or the screen templates aren't ready",
    "接著切到遊戲畫面，按「② 標記畫面」教它認。": "Now go to a game screen and press \"② Label screen\" to teach it.",
    "自動選節點": "auto-pick nodes", "不動作": "do nothing", "點擊": "tap",
    "尚未設定動作": "no action set", "不動作（等待）": "do nothing (wait)",
    "自動選節點（寶箱→治療→怪物…；沒圖釘就看隊伍前面，再沒有就拖地圖）":
        "auto-pick nodes (chest → heal → monster…; no pins: look in front of the teams; nothing: drag the map)",
    "請幫這個畫面取個名字": "Give this screen a name", "名稱前後不能有空白": "No spaces at the start or end of the name",
    "名稱太長了（最多 40 字）": "Name too long (max 40 characters)", "名稱不能以「.」開頭或結尾": "Name can't start or end with \".\"",
    "樣板庫是空的。切到遊戲裡的某個畫面，按「② 標記畫面」教它認。":
        "No screens taught yet. Go to a game screen and press \"② Label screen\".",
    "認不得這個畫面（還沒教過，或正在轉場）。": "Unknown screen (not taught yet, or a transition).",
    "還沒框選畫面範圍。按「① 框選畫面範圍」，拖曳框出模擬器裡的遊戲畫面。":
        "No game area yet. Press \"① Select area\" and drag around the game screen in the emulator.",
    "路線：已關閉（照原本規則選節點）": "Route: off (nodes picked by the normal rules)",
    "路線：已走完，之後照原本規則（新的一輪會從起點重來）": "Route: finished, normal rules from here (a new run starts over)",
    "路線：上一輪已經走完（之後照原本規則）。這是新的一輪的話，請在起始畫面重開自動，或按面板「路線 從頭」":
        "Route: the last run was finished (normal rules now). For a new run, start auto on the start screen, or press \"Route: restart\".",
    "地圖還在移動，等它停下來": "The map is still moving, waiting",
    "畫面上疊著彈窗或被壓暗，先不動": "A popup or dimmed overlay is on the map, waiting",
    "圖釘暫時看不到（閃爍或被遮住），等一下再看": "Pins hidden for a moment (blinking or covered), checking again",
    "沒看到圖釘，再確認一下不是閃爍": "No pins seen, double-checking it's not blinking",
    "找不到 adb.exe。請安裝 Android Platform-Tools，或確認 BlueStacks 有裝在預設位置。":
        "adb.exe not found. Install Android Platform-Tools, or check BlueStacks is installed in its default folder.",
    "（無標題）": "(untitled)", "輔助器自己的視窗": "the assistant's own window",
}

# ---------------------------------------------------------------- 2. 句型
_KIND = {"寶箱": "chest", "怪物": "monster", "治療": "heal", "泉水": "fountain", "復活": "revive", "裝置": "device",
         "空格": "empty tile", "認不得": "unknown", "？": "?"}
_DIR = {"正前": "ahead", "左前": "front-left", "右前": "front-right"}
_SIDE = {"左": "L", "右": "R"}
_TEAM = {"左隊": "left team", "右隊": "right team"}
_K = "|".join(sorted(_KIND, key=len, reverse=True))

# 在「長片段」之前先套的句型（不然片段會先把它們拆散）
EARLY: List[Tuple[re.Pattern, object]] = [
    (re.compile(r"(左隊|右隊)下一步 "), lambda m: f"{_TEAM[m.group(1)]} next: "),
    (re.compile(r"路線：左 (\d+)/(\d+)、右 (\d+)/(\d+)　?"), lambda m: f"Route: L {m.group(1)}/{m.group(2)}, R {m.group(3)}/{m.group(4)} | "),
]

PATTERNS: List[Tuple[re.Pattern, object]] = [
    (re.compile(r" ?要點(左隊|右隊)"), lambda m: f" should tap {_TEAM[m.group(1)]}'s "),
    (re.compile(r"沒看到圖釘"), lambda m: "no pins seen"),
    (re.compile(r"第(\d+)段 ([左右])(\d+)"), lambda m: f"Seg {m.group(1)} {_SIDE[m.group(2)]}{m.group(3)}"),
    (re.compile(r"(正前|左前|右前)的?(" + _K + ")"), lambda m: f"{_KIND[m.group(2)]} {_DIR[m.group(1)]}"),
    (re.compile(r"點(左隊|右隊)"), lambda m: f"tap {_TEAM[m.group(1)]}'s "),
    (re.compile(r"找(左隊|右隊)(?=[a-z]|正前|左前|右前)"), lambda m: f"find {_TEAM[m.group(1)]}'s "),
    (re.compile(r"(左隊|右隊)下一步 "), lambda m: f"{_TEAM[m.group(1)]} next: "),
    (re.compile(r"左隊 (\d+) 場、右隊 (\d+) 場"), lambda m: f"left {m.group(1)}, right {m.group(2)} fights"),
    (re.compile(r"自上次治療：左隊 (\d+) 場、右隊 (\d+) 場"), lambda m: f"since last heal: left {m.group(1)}, right {m.group(2)} fights"),
    (re.compile(r"看到 (\d+) 個節點："), lambda m: f"{m.group(1)} nodes seen: "),
    (re.compile(r"第 (\d+) 次"), lambda m: f"#{m.group(1)}"),
    (re.compile(r"往([左右]?)([上下]?)看"), lambda m: "look " + " ".join(
        x for x in ({"上": "up", "下": "down"}.get(m.group(2), ""), {"左": "left", "右": "right"}.get(m.group(1), "")) if x)),
    (re.compile(r"(\d+)/(\d+) 步"), lambda m: f"{m.group(1)}/{m.group(2)} steps"),
    (re.compile(r"路線：左 (\d+)/(\d+)、右 (\d+)/(\d+)"), lambda m: f"Route: L {m.group(1)}/{m.group(2)}, R {m.group(3)}/{m.group(4)}"),
]

# ---------------------------------------------------------------- 3. 片段（長的先換）
PHRASES: Dict[str, str] = {
    # 畫面名稱（使用者教的狀態；內建的這些有英文）
    "結算2": "Results 2", "結算": "Results", "總覽": "Map", "起始畫面": "Start screen", "前往彈窗": "Go popup",
    "守衛者彈窗": "Guardian popup", "點空白繼續": "Tap to continue", "提示框": "Tips", "五選一": "Pick 1 of 5",
    "獎勵輪盤": "Reward wheel", "戰敗": "Defeat", "復活視窗": "Revive window", "挑戰完成": "Challenge Complete",
    "累積獎勵": "Cumulative rewards",
    # 動作記錄
    "自動操控開始（--autostart）": "Auto started (--autostart)",
    "--autostart 沒有開成：": "--autostart didn't start: ",
    "蓋在遊戲畫面上，自動操控已停止。請把它移開（或把模擬器拉到最上層）再開始。":
        " is covering the game, auto stopped. Move it away (or bring the emulator to the front) and start again.",
    "自動操控開始 —— 按 ": "Auto started. Press ", " 可隨時停止": " to stop any time",
    "範圍存好了（": "Area saved (", "背景操控（adb）· 模擬器 ": "Background (ADB) · emulator ",
    " · 不用框選，模擬器縮到最小也能跑": " · no area needed, works with the emulator minimised",
    " · 已鎖定視窗「": " · locked to window \"", " 順便鎖定了視窗「": " Also locked to window \"",
    "」，搬動它不用重框（改變視窗大小要重框）。": "\", so moving it is fine (resizing needs a new selection).",
    "已刪除「": "Deleted \"", "要把「": "Delete all templates and the action for \"", "」的所有樣板和動作都刪掉嗎？": "\"?",
    "路線進度已更新：": "Route progress updated: ",
    "接下來程式會在背景透過 adb 自己操作模擬器（不會動到你的滑鼠，模擬器縮到最小也照跑）。\n\n隨時要停止：\n　• 按 ":
        "The program will now play the emulator in the background through ADB (your mouse is untouched, works minimised).\n\nTo stop any time:\n  • press ",
    "（在任何程式裡按都有效）\n　• 或再按一次「■ 停止自動」\n\n要開始嗎？": " (works in any program)\n  • or press \"■ Stop auto\"\n\nStart now?",
    "接下來程式會自己在框選的範圍內點擊滑鼠。\n\n隨時要停止：\n　• 按 ":
        "The program will now click inside the selected area by itself.\n\nTo stop any time:\n  • press ",
    "（不用切回面板也有效）\n　• 或再按一次「■ 停止自動」\n\n開始前請確認模擬器視窗在最上層、沒有被其他視窗擋住。\n\n要開始嗎？":
        " (works without switching to the panel)\n  • or press \"■ Stop auto\"\n\nMake sure the emulator is on top and not covered.\n\nStart now?",
    "開始錄製，照平常玩法點就好，玩完按 ": "Recording. Play as usual; when done press ",
    " 或「停止錄製」。存到：": " or \"Stop recording\". Saved to: ", "錄製結束，共 ": "Recording finished, ",
    " 次點擊，已存到 ": " clicks, saved to ", "錄製結束，": "Recording finished, ", "開始錄製": "Recording started",
    "無法開始錄製：": "Can't start recording: ", "已按下 ": "Pressed ", "自動操控": "auto", "錄製": "recording",
    "執行中 · 已點 ": "Running · ", " 次 · 按 ": " taps · press ", " 隨時停止": " to stop",
    "錄製中 · 已記 ": "Recording · ", " 次點擊 · 按 ": " clicks · press ", "（已點 ": " (", " 次）": " taps)",
    "範圍 ": "Area ", "　左上 (": "  top-left (", "樣板 ": "Templates ", " 張 / ": " / ", " 種畫面　動作規則 ": " screens  rules ",
    " 條　擷取 ": "  capture ", "內部錯誤：": "Internal error: ", "，動作：": ", action: ", "」已有 ": "\" has ",
    " 張樣板　目前動作：": " templates  current action: ", "　（差距 ": "  (distance ", " / 把握 ": " / margin ",
    "但右邊的面板蓋到這個範圍了。請把模擬器視窗縮小、擺到螢幕左邊（右邊留約 ":
        "But the panel on the right covers it. Make the emulator smaller and move it left (leave about ",
    "px 給面板），再按一次「① 框選畫面範圍」重框。": "px for the panel), then press \"① Select area\" again.",
    "已加入樣板（共 ": " added (", "跟既有樣板很像，沒重複收（共 ": " is very close to an existing template, not added (",
    " 張）": " total)", " 已走完": " done", "下一步：": "Next: ", "點擊（": "tap (",
    "看到起始畫面＝新的一輪": "start screen seen = new run", "，從第1段重新照路線走": ", following the route from segment 1",
    "上次的進度超過 ": "last progress is over ", " 小時沒動，當作新的一輪": " hours old, treating as a new run",
    "偵測到 ": "Detected ", "，自動操控已停止。": ", auto stopped.",
    "戰鬥勝利：": "Battle won: ", "記一場（自上次治療：": " +1 fight (since last heal: ", " 場）": ")",
    "｜自上次治療：": " | since last heal: ",
    "戰鬥打輸了（不在路線上），回總覽照規則繼續": "Battle lost (not on the route), back to the map",
    "戰鬥打輸了：路線退回 ": "Battle lost: route back to ", "），回總覽重打一次（第 ": "), retry it (loss #",
    " 次輸）": ")", "英雄都復活了，關掉復活視窗；": "All heroes revived, closing the revive window; ",
    "有英雄陣亡：點右下 Heart of Norheim；": "A hero is down: tapping Heart of Norheim (bottom right); ",
    "）連續打輸 ": ") lost ", " 次，先停下來——隊伍可能太弱或血不夠，請你看一下（換陣容／補血）再開自動。":
        " times in a row. Stopping: the team may be too weak or low on HP. Check it (change lineup / heal) and start auto again.",
    "　開自動會：": "  With auto on: ", "抓不到畫面：": "Can't read the screen: ", "連續 ": "",
    " 啟動 =====": " start =====", "沒圖釘": "no pins", " 次地圖都": " times, ", "或被壓暗，先不動": " or dimmed, waiting",
    " 要點": " should tap ", " 次點擊": " clicks", "回總覽重打一次": "back to the map to retry",
    "（第 ": " (#", "：剛剛打輸了，點": ": just lost, tap ",
    " 秒認不得畫面，自動操控已停止。這個畫面可能還沒教過——按「② 標記畫面」教它。":
        " s of unknown screens, auto stopped. This screen may not be taught yet: press \"② Label screen\".",
    " 走完——整條路線都走完了，之後照原本規則": " done. The whole route is finished; normal rules from here",
    "）有反應，算走完；": ") worked, step done; ", "VS 畫面有 ": "VS screen: ",
    " 位英雄陣亡（灰色），先關掉去用 Heart of Norheim 復活": " hero(es) down (grey). Closing it to revive with Heart of Norheim",
    "復活視窗：": "Revive window: ", " 位陣亡，點 Tap to use；": " down, tapping Tap to use; ",
    "這一輪永夜迷城打完了（Challenge Complete），自動操控已停止。「": "This Evernight run is finished (Challenge Complete), auto stopped. \"",
    "；這一輪打完了，自動停止": "; run finished, auto stopped", "；路線退回 ": "; route back to ", " 等一下重打": " to retry",
    "［畫面存檔 ": " [frame saved ", "在「": "On \"", "」連續點了 ": "\" tapped ",
    " 次畫面都沒變化，已自動停止。請確認模擬器視窗在最上層、沒有被別的視窗擋住。":
        " times with no change, auto stopped. Check the emulator is on top and not covered.",
    "送出點擊失敗：": "Tap failed: ", "送出": "Sending ", "失敗：": " failed: ",
    "找不到視窗「": "Can't find window \"", "」——可能被關掉或縮到最小了。還原視窗就會自動接回去。":
        "\". It may be closed or minimised; it reconnects when restored.",
    "視窗「": "Window \"", "」的大小變了（框選時 ": "\" changed size (was ", "，現在 ": ", now ",
    "），範圍對不上了。請按「① 框選畫面範圍」重框一次。": "). Press \"① Select area\" to select again.",
    "別的程式的視窗「": "another program's window \"",
    "畫面上疊著彈窗": "popup on the map", "沒看到節點圖釘": "no node pins seen",
    "前面沒有看到任何節點": "no node seen in front", "前面看到的是：": "in front there is: ",
    "沒有怪物可先打，治療照點）": "no monster to fight first, healing anyway)",
    "（上一步拖了畫面沒動＝到地圖邊了，換方向）": " (last drag didn't move the map = map edge, changing direction)",
    "能點的節點": "a node to tap", "（那格沒有圖釘，照位置點）": " (no pin there, tapping by position)",
    "（隊伍前面沒有認得出的節點）": " (no recognisable node in front of the team)", "的治療先留著：": " heal kept for later: ",
    "還沒打過，先讓": " hasn't fought yet, letting ", "打）": " fight first)",
    "在畫面邊邊、前面的節點看不全，先把它拖到中間": " is at the screen edge, dragging it to the centre first",
    "換邊：照上次記住的距離一次拖過去（": "switch: dragging the remembered distance (",
    "換邊：照記住的距離沒看到，先拖回原點再大步找": "switch: not found at the remembered spot, dragging back and searching",
    "換邊大步快拖，": "switch, big fast drag, ", "沒有可點的節點": "Nothing to tap", "，拖地圖": ", dragging the map to ",
    "（第 ": " (", " 次，": ", ", "（路線：": " (route: ", "：點": ": tap ", "（畫面裡看不到隊伍）": " (no team on screen)",
    "都在這個方向，分不出是哪個": " in this direction, can't tell which", "（圖釘那格小圖認成「": " (pin icon read as \"",
    "」，顏色看是": "\", colour says ", "）走過去了": ") walked over",
    "，但沒有可以自動選的（認不得種類，或點了都沒反應）——請你自己點": ", but none can be picked automatically (unknown type, or no response). Please tap one yourself.",
    "連拖 ": "Dragged ", " 次地圖畫面都沒動（模擬器可能沒收到拖曳，或被別的東西擋住）——請你接手。":
        " times without the map moving (the emulator may not get drags, or something is in the way). Please take over.",
    " 次地圖都沒找到": " times without finding ", "，請你接手。": ". Please take over.", " 要點": " should tap ",
    "，但": ", but ", "——請你看一下畫面。如果進度不對（不是這一步），可以按面板的「路線 調整…」改。":
        ". Please check the screen. If the progress is wrong, fix it with \"Adjust…\" on the panel.",
    "點了 ": "tapped ", " 次都沒反應——請你看一下畫面。": " times with no response. Please check the screen.",
    "·無圖釘": "·no pin", "已在等另一隊，看不到另一隊）": " is waiting for the other team, which isn't on screen)",
    "點「": "tap \"", "」節點": "\" node", "路線：兩隊都還不能走（": "Route: neither team can move yet (",
    "（英雄認不出是哪隊，靠路線下一步對得上）": " (team not recognised; matched by the route's next step)",
    "自上次治療還沒打過，先讓": " hasn't fought since the last heal, letting ", "在畫面邊邊，先拖到中間再找": " is at the edge, centring it to find ",
    "：找": ": looking for ", "），再看一下": "), checking again", "沒圖釘，隊伍前面有「": "No pin; in front of the team: \"",
    "總覽停了 ": "Map idle for ", " 秒沒有動作（": " s (", "）。請看一下畫面。": "). Please check the screen.",
    "：剛剛打輸了，點": ": just lost, tap ", "腳下那格重打": " own tile to retry ",
    "點了沒反應，先點中間那格空平台": " got no response, tapping the empty tile in between first",
    "路線走完了": "route finished", "等": "waiting for ", "走完第 ": " to finish stage ", " 階段": "",
    "段要": " must be completed first by ", "先整段走完": "", "已經點了裝置，等": "device tapped, waiting for ",
    "走完": " done", "下一步 ": "next ", "路線 ": "Route ", "路線：": "Route: ", "總覽：": "Map: ",
    "點擊 ": "Tap ", "」→ 點 (": "\" → tap (", "看到 ": "", " 個節點：": " nodes: ", " 個": "", "看到": "",
    "找": "find ", "隊伍": "team", "左隊": "left team", "右隊": "right team", "野蠻人": "barbarian", "法師": "mage",
    "寶箱": "chest", "怪物": "monster", "治療": "heal", "泉水": "fountain", "復活": "revive", "裝置": "device",
    "空格": "empty tile", "認不得": "unknown", "正前": "ahead", "左前": "front-left", "右前": "front-right",
    "拖曳": "drag", "戰鬥": "Battle", "路線": "Route", " 場、右隊 ": " fights, right team ", " 場": " fights",
    "上": "up", "下": "down", "左": "left", "右": "right", "往": "", "看": "", "的": " ", "第": "", "段 ": " ",
    " 步": " steps", "沒有輸出": "no output",
    "screencap 回傳的資料太短（": "screencap returned too little data (", "看不懂 screencap 的格式（": "unknown screencap format (",
    " 格式 ": " format ", "adb 沒回應（": "adb not responding (", " 秒逾時）：": " s timeout): ", "執行 adb 失敗：": "running adb failed: ",
    "連不到模擬器 ": "Can't connect to emulator ",
    "，也沒找到其他開著 ADB 的模擬器。確認 BlueStacks 有開、設定 → 進階 → ADB 有打開。":
        ", and no other emulator with ADB is running. Make sure BlueStacks is open and Settings → Advanced → ADB is on.",
    "screencap 失敗：": "screencap failed: ", " 失敗：": " failed: ",
    "區域必須落在 0~1 之內且右>左、下>上：": "box must be within 0~1 with right>left, bottom>top: ",
    "擷取範圍不合法：": "invalid capture area: ", "區域必須是 (左, 上, 右, 下) 四個數字：": "box must be 4 numbers (left, top, right, bottom): ",
    "SendInput 只送出 ": "SendInput sent only ", " 個事件（WinError ": " events (WinError ",
    "）。如果模擬器是用「以系統管理員身分執行」開的，這個輔助器也要用系統管理員身分開才送得進去。":
        "). If the emulator runs as administrator, run this assistant as administrator too.",
    "已經在錄製了": "already recording", "掛不上全域滑鼠鉤子（WinError ": "can't install the mouse hook (WinError ",
    "目前沒有可用的畫面範圍": "no usable game area right now", "掛全域滑鼠鉤子逾時": "mouse hook timed out",
    "名稱不能包含這些字元：": "Name can't contain: ", " 啟動 =====\n": " start =====\n", "停止": "stop", " 停止": " stop",
}
_PHRASE_KEYS = sorted(PHRASES, key=len, reverse=True)
_PHRASE_RE = re.compile("|".join(re.escape(k) for k in _PHRASE_KEYS))
_PUNCT = str.maketrans({"，": ", ", "。": ". ", "：": ": ", "；": "; ", "（": " (", "）": ") ", "「": "\"", "」": "\"",
                        "、": ", ", "！": "! ", "？": "? ", "　": "  ", "｜": " | ", "［": " [", "］": "] ", "…": "…",
                        "＝": " = ", "＋": " + ", "／": " / ", "～": "~", "→": "→"})


_LONG_RE = re.compile("|".join(re.escape(k) for k in _PHRASE_KEYS if len(k) >= 4))


def T(text: str) -> str:
    """英文模式時把中文翻成英文；中文模式原樣回傳。"""
    if LANG != "en" or not isinstance(text, str) or not _CJK.search(text):
        return text
    key = text.strip()
    if key in EXACT:
        return text.replace(key, EXACT[key])
    out = text
    for pat, fn in EARLY:
        out = pat.sub(fn, out)
    out = _LONG_RE.sub(lambda m: PHRASES[m.group(0)], out)      # 長片段先換（不然句型會把它們拆散）
    for pat, fn in PATTERNS:
        out = pat.sub(fn, out)
    out = _PHRASE_RE.sub(lambda m: PHRASES[m.group(0)], out)
    out = out.translate(_PUNCT)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" ([,.;:)\]])", r"\1", out)
    out = re.sub(r"\( ", "(", out)
    return out.strip(" ") if not text.endswith(("\n", " ")) else out


def tr(text: str, **kw) -> str:
    out = T(text)
    return out.format(**kw) if kw else out


def install_tk(tk, messagebox) -> None:
    """英文模式：讓所有 Tk 元件的 text／視窗標題／訊息框都經過 T()。中文模式什麼都不做。"""
    if LANG != "en":
        return
    orig_options = tk.Misc._options

    def _options(self, cnf, kw=None):
        def fix(d):
            if isinstance(d, dict) and isinstance(d.get("text"), str):
                d = dict(d)
                d["text"] = T(d["text"])
            return d
        return orig_options(self, fix(cnf), fix(kw))

    tk.Misc._options = _options
    orig_title = tk.Wm.wm_title

    def wm_title(self, string=None):
        return orig_title(self, T(string) if isinstance(string, str) else string)

    tk.Wm.wm_title = tk.Wm.title = wm_title
    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askquestion", "askretrycancel"):
        fn = getattr(messagebox, name)

        def wrapped(title=None, message=None, _fn=fn, **options):
            return _fn(T(title) if isinstance(title, str) else title, T(message) if isinstance(message, str) else message, **options)

        setattr(messagebox, name, wrapped)


__all__ = ["LANG", "T", "tr", "install_tk", "SUPPORTED"]
