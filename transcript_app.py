import os
import re
from flask import Flask, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)


def extract_video_id(url_or_id: str) -> str:
    patterns = [
        r'(?:youtube\.com/watch\?(?:.*&)?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url_or_id)
        if m:
            return m.group(1)
    # URLではなくIDそのものが渡された場合
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    raise ValueError(f"動画IDを取得できませんでした: {url_or_id}")


@app.route('/transcript')
def get_transcript():
    """
    GET /transcript?video_id=<YouTubeのURLまたは動画ID>
    成功: {"transcript": "...文字起こしテキスト..."}
    失敗: {"error": "...エラーメッセージ..."}
    """
    raw = request.args.get('video_id', '').strip()
    if not raw:
        return jsonify({"error": "video_id パラメータが必要です"}), 400

    try:
        video_id = extract_video_id(raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    api = YouTubeTranscriptApi()

    # list() で利用可能な字幕一覧を取得し、日本語優先で選ぶ
    try:
        transcript_list = api.list(video_id)
        target = None
        for t in transcript_list:
            if t.language_code.lower().startswith('ja'):
                target = t
                break
        target = target or next(iter(transcript_list), None)

        if target is None:
            return jsonify({"error": "この動画には字幕がありません"}), 404

        fetched = target.fetch()
        text = "\n".join(seg.text for seg in fetched)
        return jsonify({"transcript": text, "language": target.language_code})

    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route('/health')
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    app.run(port=8080, debug=True)
