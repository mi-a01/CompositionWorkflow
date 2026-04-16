@echo off
chcp 65001 > nul
echo ===================================
echo  文字起こし取得ツール
echo ===================================
echo.

REM credentials.json の確認
if not exist credentials.json (
    echo [エラー] credentials.json が見つかりません。
    echo このフォルダに credentials.json を置いてください。
    echo.
    pause
    exit /b 1
)

REM 必要ライブラリのインストール
echo 必要なライブラリを確認中...
pip install gspread google-auth youtube-transcript-api -q
echo.

REM スクリプト実行
python fetch_transcripts.py

