@echo off
chcp 65001 > nul
title メルカリ割安商品検出ツール

echo.
echo メルカリ割安商品検出ツールを起動中...
echo （ブラウザが自動で開きます。閉じないでください）
echo.

python main.py

if %errorlevel% neq 0 (
    echo.
    echo ❌ エラーが発生しました。
    echo setup.bat を先に実行してセットアップが完了しているか確認してください。
)
pause
