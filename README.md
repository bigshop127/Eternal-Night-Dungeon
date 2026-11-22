# 永夜迷城輔助器 · 使用說明 / Evernight Labyrinth Assistant · User Guide

> 中文說明在前，English below. 開發紀錄（架構、改版原因、驗證）在 [DEVELOPMENT.md](DEVELOPMENT.md)。

### ⬇️ 下載 / Download

兩個版本功能一樣，只差介面語言。 / Both versions are identical apart from the interface language.

| 檔案 / File | 說明 / Notes |
|---|---|
| [**EvernightAssistant_Chinese.zip**](https://github.com/bigshop127/Eternal-Night-Dungeon/releases/latest/download/EvernightAssistant_Chinese.zip) | 中文介面，免安裝 Python |
| [**EvernightAssistant_English.zip**](https://github.com/bigshop127/Eternal-Night-Dungeon/releases/latest/download/EvernightAssistant_English.zip) | English interface, no Python needed |

> ⚠️ **目前只在 BlueStacks 5 上測試過。 / Only tested on BlueStacks 5 so far.**

---

## 中文

### 這是什麼
幫你自動打「永夜迷城（困難模式）」的小工具。它看模擬器的畫面、認出現在是哪個畫面，然後自己點：

- **地城地圖**：照固定路線選節點（寶箱、怪物、治療、觸發裝置），兩隊輪流走，一隊碰到觸發裝置就自動拖地圖去找另一隊
- **彈窗與結算**：前往、挑戰、VS、勝利、提示框、五選一、獎勵輪盤、結算、累積獎勵，全部自動按
- **打輸了**：自動按 Quit、回地圖重打同一隻（同一步連輸 3 次才停下來叫你）
- **英雄陣亡（卡片變灰）**：自動用 **Heart of Norheim** 復活再重打（會消耗 Heart of Norheim）
- **背景操控**：透過模擬器的 ADB 操作，不搶你的滑鼠；模擬器被蓋住、甚至縮到最小都照跑
- 打完一輪（Challenge Complete）會自己停下來

### 你的隊伍跟別人不一樣也可以用
輔助器**不看英雄長相**來分左隊、右隊。它靠的是：鏡頭一定停在剛行動的那一隊身上、兩隊各走左右兩條路、拖地圖時推算隊伍被拖到哪裡。所以換英雄、換陣容都不用重新設定。

### 需要什麼
- Windows 10／11
- **BlueStacks 5**（其他模擬器沒測過，見「常見問題」）
- 遊戲語言：**繁體中文或英文**（這兩種的畫面都已經教過了）

### 第一次：在 BlueStacks 打開 ADB（只要做一次）
背景操控要透過 ADB（Android 偵錯橋）。
1. 打開 BlueStacks，點右側工具列的**齒輪「設定」**。
2. 左邊選 **「進階」**。
3. 打開 **「Android 偵錯橋（ADB）」** 開關。下面會顯示位址，例如 `127.0.0.1:5555`。
4. 按 **「儲存變更」**，照提示重新啟動 BlueStacks。
5. 用多開管理器開好幾個 BlueStacks 時，每個實例都有自己的位址（`5555`、`5565`…），**要玩的那個實例**要各自打開。

不用另外安裝 adb：輔助器會自動使用 BlueStacks 附的 `HD-Adb.exe`。
預設連 `127.0.0.1:5555`；連不上時會自動找開著 ADB 的那一台。

### 安裝與啟動
- **免安裝版（建議）**：下載 zip、整個解壓縮到一個資料夾，雙擊裡面的 `永夜迷城輔助器.exe`。
  Windows 跳出「Windows 已保護您的電腦」時，按「其他資訊」→「仍要執行」（程式沒有數位簽章才會這樣）。
  **exe 要跟 `templates`、`overview_assets` 資料夾、`rules.json` 放在一起**，設定與記錄也會存在這個資料夾。
- **Python 版**：裝 Python 3.10 以上，在資料夾裡執行 `pip install -r requirements.txt`，再執行 `python app.py`。

### 怎麼用
1. 在 BlueStacks 裡打開遊戲，進到**永夜迷城（困難模式）的地圖、兩隊都在起點**。
2. 打開輔助器。右邊會出現一個面板，上面的預覽圖就是它看到的畫面；面板最下面要顯示 **「操控：背景 adb」**。
3. 按 **「▶ 開始自動」**。接著你可以把 BlueStacks 縮到最小、去做別的事。
4. 隨時要停：在任何程式裡按 **F8**，或按面板上的「■ 停止自動」。

面板上的「路線：左 N/24、右 N/22」是固定路線的進度。進度跟遊戲對不上時（例如你中途自己點了幾步），按 **「調整…」** 改；
要新的一輪從頭，在起點畫面開自動就會自動歸零，或按 **「路線 從頭」**。

### 固定路線
前 3 個遊戲階段照作者畫好的路線走（左隊 24 步、右隊 22 步，路線對照圖在 `reference_notes/21~26_*.jpg`），走完之後照一般規則（寶箱 → 治療 → 怪物）。
**路線是照目前這張地圖畫的**。如果你的地圖跟圖上不一樣，輔助器找不到該走的節點就會停下來；
這時在「調整…」裡**取消勾選「照固定路線走」**，改用一般規則。

### 它會停下來叫你的情況
- 連續好一陣子認不得畫面（可能是沒見過的新畫面）：在那個畫面按「② 標記畫面」教它（取名＋在圖上點出要點的位置）
- 同一步連輸 3 次（隊伍太弱或血不夠）
- 路線上找不到該走的節點、拖了很多次地圖都找不到另一隊
- 一輪打完（Challenge Complete）

出問題時，資料夾裡的 `app_log.txt`（動作記錄）和 `debug_frames\`（當時的畫面）最有幫助。

### 常見問題
- **其他模擬器（雷電、MuMu、夜神）可以嗎？** 沒有測試過。輔助器會試著連這些模擬器常用的 ADB 位址，理論上打開模擬器的 ADB 就能用；
  但有些模擬器縮到最小時會暫停畫面，背景模式就會看不到東西。雷電模擬器的 ADB 連線方式比較不一樣，可能連不上。
- **畫面比例**：請用 16:9 的解析度（例如 1280×720、1600×900、1920×1080）。
- **不想用背景模式**：按面板下面的「操控」切成「前景滑鼠」，再按「① 框選畫面範圍」框出遊戲畫面。這時模擬器要留在最上層。
- **換英文介面**：在資料夾的 `config.json` 加上 `"language": "en"`（或下載英文版）。

### 它不會做的事
- 不改遊戲檔案、不讀記憶體、不連網；只透過模擬器的 ADB 截圖和點螢幕（跟你手動點一樣）
- 不會幫你花鑽石或其他付費資源；唯一會自動用掉的道具是 Heart of Norheim（英雄陣亡時）

---

## English

### What it is
A helper that plays **Evernight Labyrinth (Hard mode)** for you. It watches the emulator screen, recognises which screen it is on, and taps:

- **Dungeon map**: follows a fixed route (chests, monsters, heals, switches/devices); the two teams take turns, and when one team reaches a device it drags the map to find the other team
- **Popups and results**: Go, Challenge, VS, Victory, Tips, Pick 1 of 5, reward wheel, results, cumulative rewards — all tapped automatically
- **Lost a battle**: taps Quit and fights the same enemy again (stops and asks you after 3 losses on the same step)
- **Fallen heroes (grey cards)**: revives them with **Heart of Norheim** and fights again (this uses Heart of Norheim)
- **Background control**: works through the emulator's ADB, never touches your mouse, keeps running when the emulator is covered or even minimised
- Stops by itself when the run is finished (Challenge Complete)

### Works with any team
The assistant does **not** tell the left and right team apart by how the heroes look. It relies on facts that are true for everyone:
the camera always stops on the team that just moved, the two teams walk separate left and right paths, and when it drags the map it keeps track of where the teams moved.
Different heroes or lineups need no setup.

### Requirements
- Windows 10 / 11
- **BlueStacks 5** (other emulators are untested, see FAQ)
- Game language: **English or Traditional Chinese** (screens for both are already taught)

### First time: enable ADB in BlueStacks (once)
Background control uses ADB (Android Debug Bridge).
1. Open BlueStacks and click the **gear icon (Settings)** on the right-hand toolbar.
2. Choose **Advanced** on the left.
3. Turn on **Android Debug Bridge (ADB)**. An address is shown below it, e.g. `127.0.0.1:5555`.
4. Click **Save changes** and restart BlueStacks when asked.
5. If you run several BlueStacks instances (Multi-Instance Manager), each has its own address (`5555`, `5565`, …). Turn ADB on in **the instance you play in**.

No separate adb install is needed: the assistant uses the `HD-Adb.exe` that comes with BlueStacks.
It connects to `127.0.0.1:5555` by default and falls back to whichever emulator has ADB running.

### Install & start
- **No-install version (recommended)**: download the zip, extract the whole thing into a folder, and double-click `EvernightAssistant.exe`.
  If Windows shows "Windows protected your PC", click "More info" → "Run anyway" (the program isn't code-signed).
  **Keep the exe together with the `templates` and `overview_assets` folders and `rules.json`.** Settings and logs are saved in this folder too.
- **Python version**: install Python 3.10+, run `pip install -r requirements.txt` in the folder, then `python app.py`.
  To get the English interface, add `"language": "en"` to `config.json` (or set the environment variable `EVERNIGHT_LANG=en`).

### How to use
1. In BlueStacks, open the game and go to the **Evernight Labyrinth (Hard mode) map with both teams at their starting points**.
2. Start the assistant. A panel appears on the right; the preview shows what it sees. The bottom of the panel should say **"Mode: ADB"**.
3. Click **"▶ Start auto"**. You can now minimise BlueStacks and do something else.
4. To stop at any time: press **F8** in any program, or click "■ Stop auto" on the panel.

"Route: L N/24, R N/22" on the panel is the fixed-route progress. If it doesn't match the game (for example you tapped a few steps yourself), fix it with **"Adjust…"**.
A new run resets by itself when you start auto on the starting screen; you can also click **"Route: restart"**.

### The fixed route
For the first 3 game stages it follows the author's route (left team 24 steps, right team 22 steps; route pictures in `reference_notes/21~26_*.jpg`), then normal rules (chest → heal → monster).
**The route was drawn for the current map.** If your map is different, the assistant stops when it can't find the next node.
In that case open "Adjust…" and **untick "Follow the fixed route"** to use the normal rules instead.

### When it stops and asks you
- Unknown screen for a while (probably a screen it hasn't seen): on that screen click "② Label screen" and teach it (name it and click where to tap)
- 3 losses in a row on the same step (team too weak or low on HP)
- It can't find the route's next node, or can't find the other team after many drags
- The run is finished (Challenge Complete)

When something goes wrong, `app_log.txt` (action log) and `debug_frames\` (screenshots from that moment) in the folder are the most useful things to share.

### FAQ
- **Other emulators (LDPlayer, MuMu, Nox)?** Not tested. The assistant tries their usual ADB addresses, so it may work once ADB is enabled in the emulator.
  Some emulators pause rendering when minimised, which breaks background mode. LDPlayer exposes ADB differently and may not connect.
- **Screen ratio**: use a 16:9 resolution (e.g. 1280×720, 1600×900, 1920×1080).
- **Prefer not to use background mode**: switch "Mode" at the bottom of the panel to mouse, then click "① Select area" and drag around the game screen. The emulator must stay on top in this mode.

### What it never does
- No game file changes, no memory reading, no network access; it only takes screenshots and taps through the emulator's ADB (same as tapping by hand)
- It never spends diamonds or other paid resources; the only item it uses automatically is Heart of Norheim (when heroes fall)
