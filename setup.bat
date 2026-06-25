@echo off
chcp 65001 > nul
title メルカリ割安商品検出ツール — セットアップ

echo.
echo ============================================================
echo  メルカリ割安商品検出ツール セットアップ
echo ============================================================
echo.

:: ── Step 1: Python チェック ──
echo [Step 1/4] Python のインストールを確認中...
python --version 2>nul
if %errorlevel% neq 0 (
    echo.
    echo ❌ Python が見つかりません。
    echo.
    echo 以下の手順でインストールしてください:
    echo   1. https://www.python.org/downloads/ を開く
    echo   2. 「Download Python 3.12.x」をクリック
    echo   3. インストーラーを起動し、
    echo      ★「Add Python to PATH」に必ずチェックを入れる★
    echo   4. Install Now をクリック
    echo   5. インストール完了後、このファイルを再度実行する
    echo.
    pause
    exit /b 1
)
echo ✅ Python が見つかりました

:: ── Step 2: pip アップグレード ──
echo.
echo [Step 2/4] pip をアップグレード中...
python -m pip install --upgrade pip --quiet
echo ✅ pip 最新化完了

:: ── Step 3: Playwright インストール ──
echo.
echo [Step 3/4] Playwright をインストール中...
pip install playwright==1.44.0
if %errorlevel% neq 0 (
    echo ❌ Playwright のインストールに失敗しました
    pause
    exit /b 1
)
echo ✅ Playwright インストール完了

:: ── Step 4: Chromium ダウンロード ──
echo.
echo [Step 4/4] Chromium ブラウザをダウンロード中（数分かかります）...
playwright install chromium
if %errorlevel% neq 0 (
    echo ❌ Chromium のダウンロードに失敗しました
    pause
    exit /b 1
)
echo ✅ Chromium インストール完了

echo.
echo ============================================================
echo  ✅ セットアップが完了しました！
echo  次のステップ: start.bat をダブルクリックしてツールを起動
echo ============================================================
echo.
pause
