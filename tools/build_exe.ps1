# 把輔助器打包成「免裝 Python」的版本給朋友用：中文版、英文版各一個 zip。
# Build no-Python-needed packages for friends: one Chinese and one English zip.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 [-Python <python.exe>] [-Lang zh|en|all]
#
# 不給 -Python 就在 %TEMP%\evernight-buildenv 建一個乾淨的 venv（只裝 numpy/pillow/mss/pyinstaller）。
# 產出 dist\ 底下：
#   EvernightAssistant_Chinese\永夜迷城輔助器.exe ＋ templates\ overview_assets\ rules.json  → EvernightAssistant_Chinese.zip
#   EvernightAssistant_English\EvernightAssistant.exe ＋ 同上                                → EvernightAssistant_English.zip
# exe 執行時把資料（設定、樣板、路線進度、記錄）放在 exe 旁邊，所以畫面樣板與節點樣板跟 exe 放同一個資料夾。
# 語言是打包時寫進 exe 的預設值（暫時產生 _build_lang.py），使用者之後可在 config.json 加 "language": "zh" / "en" 換。
param([string]$Python = '', [ValidateSet('zh', 'en', 'all')][string]$Lang = 'all')
$ErrorActionPreference = 'Stop'
$AppDir = Split-Path -Parent $PSScriptRoot

if (-not $Python) {
    $venv = Join-Path $env:TEMP 'evernight-buildenv'
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        python -m venv $venv
        & "$venv\Scripts\python.exe" -m pip install -q -r "$AppDir\requirements.txt" pyinstaller
    }
    $Python = "$venv\Scripts\python.exe"
}

$targets = @(
    @{ Lang = 'zh'; Name = '永夜迷城輔助器'; Folder = 'EvernightAssistant_Chinese' },
    @{ Lang = 'en'; Name = 'EvernightAssistant'; Folder = 'EvernightAssistant_English' }
) | Where-Object { $Lang -eq 'all' -or $_.Lang -eq $Lang }

$langFile = Join-Path $AppDir '_build_lang.py'
Push-Location $AppDir
try {
    foreach ($t in $targets) {
        Set-Content -Path $langFile -Value "LANG = `"$($t.Lang)`"" -Encoding ascii
        & $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
            --name $t.Name --icon (Join-Path $AppDir 'icon.ico') `
            --hidden-import _build_lang `
            --workpath "$env:TEMP\evernight-pyi-build" --specpath "$env:TEMP\evernight-pyi-build" `
            --distpath "dist\$($t.Folder)" app.py
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($($t.Name), $LASTEXITCODE)" }
        $out = Join-Path $AppDir "dist\$($t.Folder)"
        Copy-Item -Recurse -Force templates, overview_assets, rules.json, icon.ico -Destination $out
        Copy-Item -Force README.md -Destination $out
        $zip = Join-Path $AppDir "dist\$($t.Folder).zip"
        if (Test-Path $zip) { Remove-Item $zip -Force }
        Compress-Archive -Path "$out\*" -DestinationPath $zip
        Write-Host "OK: $zip"
    }
} finally {
    Remove-Item $langFile -ErrorAction SilentlyContinue
    Pop-Location
}
