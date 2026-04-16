@echo off
echo ===================================
echo  Shift AI Channel 構成書ワークフロー
echo ===================================
echo.

REM .envファイルの確認
if not exist .env (
    echo [エラー] .env ファイルが見つかりません。
    echo .env.example をコピーして .env を作成し、APIキーを設定してください。
    pause
    exit /b 1
)

REM ライブラリのインストール確認
echo ライブラリを確認・インストールしています...
pip install -r requirements.txt -q

echo.
echo アプリを起動しています...
echo ブラウザで http://localhost:5000 を開いてください
echo 終了するには Ctrl+C を押してください
echo.

python app.py
pause
