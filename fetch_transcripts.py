"""
fetch_transcripts.py
────────────────────
スプレッドシートのI列にあるYouTube URLを読み取り、
文字起こしをJ列に書き込むローカルスクリプト。

・J列にすでにデータがある行はスキップ
・複数URLがある場合はすべて取得して連結
"""

import re
import sys
import time

import gspread
from google.oauth2.service_account import Credentials
from youtube_transcript_api import YouTubeTranscriptApi

# ===== 設定 =====
SPREADSHEET_ID    = "1O4ydQcTkZsvIA4STKF9ZZpTVNOXuaij_e-F7qJWxWdo"
SHEET_GID         = 766246461          # シートのGID（URLの gid= の値）
CREDENTIALS_FILE  = "credentials.json" # サービスアカウントのJSONキーファイル

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 列番号（1始まり）
COL_I = 9   # YouTube URL列
COL_J = 10  # 文字起こし書き込み先


# ===== ユーティリティ =====

def extract_urls(text: str) -> list:
    return re.findall(
        r'https?://(?:www\.)?(?:youtube\.com/\S+|youtu\.be/\S+)',
        text
    )

def extract_video_id(url: str) -> str | None:
    patterns = [
        r'(?:youtube\.com/watch\?(?:.*&)?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def fetch_transcript(url: str) -> str | None:
    video_id = extract_video_id(url)
    if not video_id:
        print(f"    動画IDを取得できません: {url}")
        return None

    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
        target = None
        for t in transcript_list:
            if t.language_code.lower().startswith("ja"):
                target = t
                break
        target = target or next(iter(transcript_list), None)
        if target:
            fetched = target.fetch()
            return "\n".join(seg.text for seg in fetched)
        print(f"    字幕が見つかりません: {url}")
        return None
    except Exception as e:
        print(f"    取得エラー: {e}")
        return None


# ===== メイン処理 =====

def main():
    print("=" * 50)
    print("  文字起こし取得スクリプト")
    print("=" * 50)
    print()

    # Google Sheets 接続
    print("Google Sheetsに接続中...")
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
    except FileNotFoundError:
        print(f"\n[エラー] {CREDENTIALS_FILE} が見つかりません。")
        print("credentials.json をこのフォルダに置いてください。")
        input("\nEnterキーで終了...")
        sys.exit(1)
    except Exception as e:
        print(f"\n[エラー] 接続失敗: {e}")
        input("\nEnterキーで終了...")
        sys.exit(1)

    # GIDでシートを取得
    worksheet = None
    for ws in sh.worksheets():
        if ws.id == SHEET_GID:
            worksheet = ws
            break
    if worksheet is None:
        print(f"\n[エラー] GID={SHEET_GID} のシートが見つかりません。")
        input("\nEnterキーで終了...")
        sys.exit(1)

    print(f"シート「{worksheet.title}」を読み込み中...")
    all_values = worksheet.get_all_values()
    print(f"全{len(all_values)}行を読み込みました。\n")

    # 処理対象行を収集
    targets = []
    for row_idx, row in enumerate(all_values):
        row_num = row_idx + 1
        i_val = row[COL_I - 1].strip() if len(row) >= COL_I else ""
        j_val = row[COL_J - 1].strip() if len(row) >= COL_J else ""

        if not i_val:
            continue          # I列が空 → スキップ
        if j_val:
            print(f"行{row_num:3d}: J列に既存データあり → スキップ")
            continue          # J列に既データ → スキップ

        urls = extract_urls(i_val)
        if not urls:
            continue          # YouTubeのURLなし → スキップ

        targets.append((row_num, urls))

    if not targets:
        print("\n処理対象の行がありませんでした。")
        print("（I列にURLがある・かつJ列が空の行がありません）")
        input("\nEnterキーで終了...")
        return

    print(f"\n処理対象: {len(targets)}行\n")

    # 文字起こし取得 → J列書き込み
    success_count = 0
    for row_num, urls in targets:
        print(f"行{row_num:3d}: {len(urls)}件のURLを処理中...")
        parts = []
        for url in urls:
            print(f"  → {url}")
            text = fetch_transcript(url)
            if text:
                parts.append(f"【{url}】\n{text}")
                print(f"     ✓ 取得完了（{len(text)}文字）")
            else:
                print(f"     × 取得失敗")
            time.sleep(1)  # YouTube API の負荷対策

        if parts:
            combined = "\n\n".join(parts)
            try:
                worksheet.update_cell(row_num, COL_J, combined)
                print(f"     → J列に書き込み完了")
                success_count += 1
                time.sleep(0.5)  # Sheets API の制限対策
            except Exception as e:
                print(f"     [書き込みエラー] {e}")
        print()

    print("=" * 50)
    print(f"完了！ {success_count}/{len(targets)} 行を書き込みました。")
    print("=" * 50)
    input("\nEnterキーで終了...")


if __name__ == "__main__":
    main()
