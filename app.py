import os
import io
import csv
import json
import re
import requests
from flask import Flask, render_template, request, Response, stream_with_context
import anthropic
from dotenv import load_dotenv

load_dotenv()

# PROXY_URL が設定されている場合、requests が自動的に使う環境変数に反映する
# youtube-transcript-api も内部で requests を使うため、バージョン問わず有効
_proxy = os.getenv("PROXY_URL")
if _proxy:
    os.environ.setdefault("HTTP_PROXY",  _proxy)
    os.environ.setdefault("HTTPS_PROXY", _proxy)

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ===== 設定 =====
MODEL          = "claude-opus-4-6"
MAX_ITERATIONS = 5
PASS_SCORE     = 75

# Chatwork
CW_TOKEN   = os.getenv("CHATWORK_API_TOKEN", "72db51102e6e4b4ea1515b3b869600c1")
CW_ROOM_ID = os.getenv("CHATWORK_ROOM_ID",   "433561844")

# Google Sheets（台本ワークフロー用）
SHEET_ID  = "1O4ydQcTkZsvIA4STKF9ZZpTVNOXuaij_e-F7qJWxWdo"
SHEET_GID = "766246461"

# Google Sheets（訴求プレゼント一覧）
APPEAL_SHEET_ID  = "1mgHqED-2FYJTYex1aaAe8fUvyNhFuQ4qc7L7H-zg338"
APPEAL_SHEET_GID = "0"


# ===== プロンプトテンプレート =====

# 設計書プロンプト（H/I/J列データを埋め込む）
DESIGN_DOC_PROMPT = """＃指示
あなたは「Shift AIチャンネル」の構成設計者です。以下の入力素材（企画意図/競合文字起こし/Xポスト/実体験メモ）から、まず台本を書く前の「設計図」だけを作ってください。台本本文は書かないでください。

目的は、視聴者の心理障壁を下げる言語的配慮と、理解が積み上がる階層的構成を両立しつつ、競合レベルの情報密度で最後まで離脱させないことです。

この動画は15分以上を想定している動画です。

■ 設計の必須条件
- 専門用語は最小限にする。使う場合は必ず同じ段落内で「日常語への言い換え＋イメージ例」を添える（用語をゼロにすることが目的ではない）
- 丁寧な敬語。威圧しない
- 主観コメントを最低2回、共感を最低2回、どの箇所に入れるかを明記
- 上回する内容は新規制が高いと推測されるものから優先的に紹介を行う
- 必須モジュール：導入、実演、総括
- 任意モジュール：全体像、基本、応用、上級、比較 から必要分だけ選ぶ（不要は省いて密度を上げる）
- 情報密度KPI：1分あたり最低「結論1＋理由（最大2文）＋具体2（うち1つは手順/設定、もう1つは注意点/失敗回避/制約条件/判断基準/数値/例）」を満たす設計にする
- 圧縮ルール：情報が増えない言い換えは禁止。ただし「結論の強調」「原理の言い直し」は許可する。1ブロック1論点。

■ 出力してほしい設計図（この順で）
- 到達点：視聴後にできる状態を1文
- 想定視聴者：前提レベルと悩みを1〜2文
- 動画タイプ：初心者ハウツー/時短ワザ/比較レビュー/応用ロードマップ/問題解決ストーリー から1つ
- 全体のモジュール順：導入→（任意）→実演→（任意）→総括 の形で列挙
- 可能な限り前半部分でも口頭だけでの説明を避け、スマホ画面を見せながら説明できるように構成
- 尺配分：各モジュールの目安分数を提示（文字数固定ではなく秒数で設計。目安の話速「400文字/分」も併記）
- 冒頭タイプ：危機感/共感/結論先出し/驚きデモ/権威
- 冒頭で言うこと（箇条書きで3〜4行）
└ この動画を見る圧倒的な理由（みないリスク、期待感が一瞬で上がる情報）
└ 到達点
└ 今日の範囲
└ すぐ本題に入る一言
- ブロック設計：全体で最低8ブロック以上を作り、各ブロックに「小結論」を1行ずつ書く（章の中では小結論の頻度は自然な範囲でよい）
- 実演パートの操作ステップ：3〜10個を箇条書きで、視聴者が詰まりやすい点と回避策も1つ添える
- 比較を入れる場合：比較観点を2〜5個列挙し、向き不向きの結論を1行
- 原理の一言：各モジュールごとに「重要なのは◯◯」を1文で用意（実演後は必ず入れる）
- 専門用語リスト（出る場合のみ）：用語→日常語言い換え→例 の3点セット
- 主観コメント挿入位置：どのブロックで何を言うか
- 共感挿入位置：どのブロックで何に共感するか
- 総括の形：まとめ/重要3点/次にやること1つ を、それぞれ1行で下書き
- つなぎの一言候補：モジュール間で不自然にならない接続フレーズを2〜3個用意

■ 製作者への確認事項
完成の台本は15分（6,000文字程度）を基準としてるため、これを越えそうにない場合に製作者に確認するようにしてください。
また、事前に15分以内でも大丈夫と製作者から指示があった場合は、その限りではありません。
ーーーーーーーーーーー
＃入力
■ 企画意図
{kikaku_ito}

■ 競合文字起こし
{transcripts}

■サムネイメージ
{samune_image}"""


# 台本作成プロンプト（設計書の出力を {design_doc} に埋め込む）
SCRIPT_CREATION_PROMPT = """＃指示
あなたは「Shift AIチャンネル」の台本制作者です。以下に貼る「設計図」に従って台本を書いてください。設計図にない要素を勝手に増やさないでください。ただし例外として「つなぎの一言」「視聴者の不安回収」「注意喚起の追記」は、設計図の意図を壊さない範囲で追加して構いません。各ブロックは情報密度KPIを満たし、冗長表現を避けてください。

■ 目標
- 視聴者が見終わった直後に「自分で再現できる」「失敗しにくい」「次にやることが分かる」満足感が高い状態にする
- 1分あたりの情報が"浅い一般論"に逃げないこと

■ 禁止事項（浅くなる原因を潰す）
- ふわっとした表現だけで終わる（例：便利です、効率化できます、すごいです）
- 理由が抽象だけ（例：時短になるから、精度が上がるから）で終わる
- 手順が飛ぶ、または具体操作がない
- 注意点がゼロ（落とし穴がない）

■ 必須ルール（設計図から再掲、実行する）
- 丁寧な敬語、威圧しない
- 専門用語は原則禁止。必要なら設計図の言い換えに従う
- 言い換え禁止、1ブロック1論点
- 理由は最大2文。3文目以降は手順/設定値/例/結果に移る
- 15〜40秒ごとに小結論が出る構造
- 主観コメントと共感は、設計図で指定された箇所にそのまま挿入
- 必ず総括（まとめ/重要3点/次にやること1つ）で締める

■ 注意事項

■ 台本の出力形式
- 冒頭：設計図参照
また、一言目は紹介ではなく、この動画を見る圧倒的な理由を強い言葉で説明して。
- 本文：設計図のブロック順に出力
└ 各ブロックは「小結論→理由（最大2文）→具体（手順/設定値/例/結果）→注意点または回避」で構成
- 実演：設計図の操作ステップを順番通りに
- 比較：設計図の比較観点と向き不向き結論に従う
- 終了：まとめ→重要3点→次にやること1つ

＃入力
{design_doc}"""


# 評価プロンプト（台本は直前のuserメッセージとして別途渡す。ここには埋め込まない）
EVALUATION_PROMPT = """あなたは、YouTube動画の構成案・台本を査読する厳格な品質管理者です。
目的は「前回より改善したか」を見ることではなく、「この状態で本当に公開水準か」を絶対評価することです。

以下のチェック対象について、甘く採点せず、視聴者体験ベースで厳しく品質チェックしてください。

■評価の前提
- 修正前の版との比較はしない
- AIが前回提案した是正案が反映されていても、それ自体は加点理由にしない
- 「前回より良い」と「公開水準で強い」は別物として扱う
- 機能やテーマの違いではなく、「どう感じさせるか」「なぜ満足度に差が出るか」を見る
- 良くなった点よりも、まだ残っている弱点を優先して見つける
- 8点は十分高評価とする
- 10点は「明確に優れていて、目立つ弱点がほぼない」場合のみ付ける
- 少しでも改善余地が明確にあるなら、原則8点以下にする
- 迷ったら1段階低く採点する

■10点の厳格ルール
- 10点を付ける場合は必ず以下を明記する
└ なぜ8点ではなく10点なのか
└ どこが明確に優れているのか
└ 10点への反論を1つ出した上で、それでも10点が妥当な理由

■出力順
以下の順番で出力してください。

■1. 全体の弱点トップ5
- まず採点前に、この構成案・台本の弱点を重要度順に5つ挙げる
- 特に以下を厳しく見る
└ 冒頭だけ強くて後半が平坦になっていないか
└ 中盤以降にも見続ける理由があるか
└ 情報が小ネタの羅列になっていないか
└ 前半の理解が後半の理解に効いているか
└ 主観が納得の補強として機能しているか

■2. 項目ごとの10段階評価
各項目ごとに必ず以下をセットで出す
- 点数
- なぜその点数か
- 主な弱点
- そう感じさせた具体表現、構成、実演の見せ方
- 10点にできない理由
- 改善するなら何を直すべきか

■評価項目
□1. 難しそうに感じない
- 1: 専門用語だらけで置いていかれる
- 2: 専門用語が多く、説明があっても理解が追いつかない
- 3: 難しい言葉が頻出し、頻繁に理解を中断される
- 4: 説明はあるが、難しい言葉が続く
- 5: 難しい部分はあるが、補足で何とか理解できる
- 6: たまに難しいが、全体は理解できる
- 7: ほぼ日常語で迷わない
- 8: 完全に日常語で、専門用語は即座に言い換えられる
- 9: 中学生でも理解できる平易な言葉だけで進む
- 10: 初見でも安心して聞ける言葉だけで進む

□2. 前に進んでいる実感
- 1: 同じ話の繰り返しで止まっている
- 2: ほぼ同じ内容を角度を変えて繰り返している
- 3: 少し進むが、言い換えが多い
- 4: 進むが、補足説明で足踏みする箇所がある
- 5: 進むが、ところどころ停滞する
- 6: 各ブロックで着実に前進し、停滞感が少ない
- 7: 各ブロックで確実に前進する
- 8: 前進が明確で、次への期待が途切れない
- 9: テンポよく加速し、飽きる暇がない
- 10: ずっと加速して、飽きる暇がない

□3. 自分でもできそう
- 1: 見ても再現できる気がしない
- 2: 手順は分かるが、自分には難しすぎると感じる
- 3: 手順はあるが、どこか不安が残る
- 4: 説明を見返せば何とかできそう
- 5: 時間をかければできそう
- 6: 少し練習すれば一人でできそう
- 7: 見ながらならそのまま再現できそう
- 8: 手元に資料があれば迷わず再現できる
- 9: 動画を見終わった直後に試せる自信がある
- 10: 今すぐ一人でやれる確信が持てる

□4. 退屈区間がない
- 1: 離脱したくなる長い区間がある
- 2: 複数箇所で集中が切れそうになる
- 3: 退屈な部分が複数ある
- 4: 退屈な箇所が1～2箇所あるが、短い
- 5: 少し退屈だが、耐えられる
- 6: ほぼ退屈しないが、たまにテンポが落ちる
- 7: ほぼ退屈しない
- 8: 全体を通して興味が途切れない
- 9: 常に新しい情報や刺激があり、飽きない
- 10: ずっと聞いていたくなる密度とテンポ

□5. 信頼できる知人に見える
- 1: 売り込み・上から目線に感じる
- 2: ビジネスライクで距離を感じる
- 3: 丁寧だが、距離が遠い
- 4: 丁寧で、誠実さは伝わる
- 5: 丁寧で普通に信頼できる
- 6: 親しみやすく、共感できる部分がある
- 7: 共感と実体験が自然で親しみがある
- 8: 友人のように気軽に相談できそうな雰囲気
- 9: 信頼できる先輩や仲間のような安心感がある
- 10: この人のおすすめなら試すと思える

□6. 不要だと感じる部分がない
- 1: 不要な話が多く、削ってほしいと感じる
- 2: 不要な説明が何度も出てきて、イライラする
- 3: 不要な説明が何度も出てくる
- 4: 一部は不要だが、我慢できる範囲
- 5: 一部は不要だが、全体は許容できる
- 6: ほぼ無駄がなく、たまに冗長な箇所がある程度
- 7: ほぼ無駄がなく、必要な話だけで進む
- 8: すべての説明に意味があり、削る箇所が見当たらない
- 9: 一言一言が計算されていて、無駄が一切ない
- 10: 一言も無駄がなく、全部が意味を持っている

□7. リアルな主観が入っている
- 1: 完全に説明だけで、体験や好みが一切ない
- 2: 「便利です」程度の薄い主観しかない
- 3: 一応「おすすめ」等はあるが、理由がなく薄い
- 4: 主観はあるが、一般論との違いが曖昧
- 5: 主観はあるが、一般論と区別がつきにくい
- 6: 主観が具体的で、理由が添えられている
- 7: 主観が具体的で、納得できる理由とセットになっている
- 8: 主観に短いエピソードが添えられ、リアリティがある
- 9: 実体験が具体的で、信頼感が大きく増す
- 10: 主観が「実体験の短いエピソード」まで落ちていて、信頼が増す

□8. 主観の置き方が上手い
- 1: 主観が多すぎて本題を邪魔している、またはゼロで無機質
- 2: 主観が唐突で、流れを妨げている
- 3: 主観が突然出てきて、流れが途切れる
- 4: 主観はあるが、タイミングがやや不自然
- 5: 要所にあるが、効果が弱い
- 6: 適度な頻度で主観が入り、流れを損なわない
- 7: 各モジュールで1回程度、刺さる位置に入っている
- 8: 主観が理解を深めるタイミングで機能している
- 9: 主観が不安や疑問を解消する絶妙な位置に入っている
- 10: 主観が「理解の山場」か「不安の山場」を越えるために機能している

□9. 動画視聴直後に期待感が一瞬で上がる
- 1: ただの挨拶で始まり、価値が見えない
- 2: テーマは分かるが、興味を引く要素がない
- 3: テーマは分かるが、見たくなる強さがない
- 4: 便利そうだが、決定打に欠ける
- 5: 便利そうとは思うが、決定打が弱い
- 6: 見たら役立ちそうだと感じる
- 7: 見たら得する未来が具体的に想像できる
- 8: 見ることで得られるメリットが明確で魅力的
- 9: 冒頭の一言で「これは見るべきだ」と確信する
- 10: 一言で「これ見たい」と即決する

□10. 動画視聴直後に見ないリスクが伝わる
- 1: 見なくても困らない空気で始まる
- 2: 見なくても問題ない印象を受ける
- 3: 遠回りかも、程度で緊急性が弱い
- 4: 損するかもしれないが、実感が薄い
- 5: 損はしそうだが、実感が薄い
- 6: 今のやり方に問題があることが示唆される
- 7: 今のやり方のムダや事故が具体的に刺さる
- 8: 見ないと明確に損をすると感じる
- 9: 見ないことで起こる問題が鮮明にイメージできる
- 10: 離脱すると確実に損だと感じる

■3. 構成の山場設計の診断
以下を文章で診断する
- この構成の山場はどこか
- 山場が冒頭だけで終わっていないか
- 中盤以降に見続ける理由があるか
- 情報の強弱や順番が期待感の上昇に機能しているか
- 後半に向かうほど弱くなっていないか

■4. 理解の積み上がり方の診断
以下を文章で診断する
- 前半の説明が後半の理解に効いているか
- 各パートが独立しすぎていないか
- 小ネタの羅列ではなく、一本の流れになっているか
- 視聴後に全体像が残るか
- 途中で理解がリセットされる箇所がないか

■5. 総評
以下を簡潔にまとめる
- この台本の総合評価(100点満点中): XX点
- 強み3つ
- 弱み3つ
- 今のまま公開してよいか
- 最優先で直すべき箇所3つ
- 競合上位水準と比べて、まだ弱い点

■重要な採点ルール
- 点数を先に決めて理由を後付けしない
- まず弱点を洗い出してから採点する
- 10点を付けた項目は必ず再審査する
- 「改善された」は高得点の根拠にしない
- 「まだ弱点が残っていないか」を最優先で見る"""


# 修正プロンプト（評価直後に会話形式で送る）
REVISION_PROMPT = (
    "今のフィードバックを全面的に反映するように台本を修正してください．6000文字以上で．\n"
    "「最優先で直すべき箇所3つ」を優先して、弱点を無くすように修正してください。"
)


# 訴求挿入プロンプト
APPEAL_PROMPT = """あなたは「Shift AIチャンネル」の台本編集者です。
以下の台本に、3か所のLINE訴求（CTA）を適切な位置に挿入した完成版台本を出力してください。

【プレゼント一覧（スプレッドシートより）】
{appeal_data}

━━━ 挿入ルール ━━━

■訴求①：冒頭〜全体像の説明が終わった直後に挿入
・プレゼント一覧から、この台本のテーマ・内容に最も合う商材を1つ選ぶ（この商材は③でも使う）
・テンプレートの「Googleの次世代AI Gemini 完全ガイド」の部分だけを選んだ商材名に置き換えて挿入する
・挿入後「それでは、いってみましょう。」で本題に入る

【訴求①テンプレート】
YouTubeだけでは紹介しきれないAI活用の情報を、概要欄の一番上にある公式LINEを友だち登録していただくと受け取ることができます。
さらに、AIを活用した月10万円以上の収益化方法や、（素材②）資料作成を2時間から15分に短縮する社内の業務効率化AIノウハウなどが学べるAI活用無料勉強会に、オンラインで参加できます。
（素材②）：以下よりダウンロードしてください（右上のGoogleアイコンマークだけモザイク）
CV訴求動画.mp4
また、この勉強会に参加すると、明日からすぐに使える「Googleの次世代AI Gemini 完全ガイド」をはじめとしたAIスキルが身につく、AI活用の豪華特典が無料で受け取れますので、概要欄の一番上にあるLINEをぜひ友だち登録してみてください。

それでは、いってみましょう。

■訴求②：台本の中盤（前半終了〜後半開始あたり）に挿入
・プレゼント一覧のキーワード列を確認し、台本のテーマ・内容に関連するものを最大4つ選ぶ
・テンプレートの「chatGPT大全集」を、選んだ商材名（複数なら「〇〇」や「〇〇」などをはじめとした）に置き換える
・台本の流れに違和感が出ないよう前後の接続文も自然に調整する

【訴求②テンプレート】
YouTubeだけでは紹介しきれないAI活用の情報を、概要欄の一番上にある公式LINEを友だち登録していただくと受け取ることができます。
さらに、AIを活用した月10万円以上の収益化方法や、（素材②）資料作成を2時間から15分に短縮する社内の業務効率化AIノウハウなどが学べるAI活用無料勉強会に、オンラインで参加できます。
（素材②）：以下よりダウンロードしてください（右上のGoogleアイコンマークだけモザイク）
CV訴求動画.mp4
また、この勉強会に参加すると、明日からすぐに使える「chatGPT大全集」をはじめとしたAIスキルが身につく、AI活用の豪華特典が無料で受け取れますので、概要欄の一番上にあるLINEをぜひ友だち登録してみてください。

■訴求③：台本の締め挨拶（チャンネル登録のお願いなど）の直前に挿入
・①で選んだ商材と同じものを使う
・テンプレートの「Googleの次世代AI Gemini 完全ガイド」と「『Googleの次世代AI Gemini 完全ガイド』」を選んだ商材名に置き換える
・訴求③の後に元々の締め挨拶が自然につながるよう調整する（元の挨拶文は削除せず必ず残す）

【訴求③テンプレート】
そして「AIをもっと使いこなしたい！」という方は、公式LINEでさらに詳しい実践ノウハウを発信しています。僕自身も会社員時代、流行りだからというなんとなくの理由でAIを学び始めましたが、今では副業で毎月20万円ほど稼げるようになりました。特別なスキルがあったわけではありません。正しい知識と実践の積み重ねがあれば、誰でも成果を出せると実感しています。登録していただいた方には、本日紹介した『Googleの次世代AI Gemini 完全ガイド』などの特別なプレゼントもご用意しています。ぜひ概要欄から登録して、毎日の業務に役立ててください。

ということで、最後までご視聴ありがとうございました。
このチャンネルでは、AIを活用して日常や仕事をもっと便利にするコツや活用術を発信しています。面白い・役に立ったと思っていただけた方は、ぜひチャンネル登録とグッドボタンもよろしくお願いします。
それでは、また次の動画でお会いしましょう。ありがとうございました。

━━━ 出力形式 ━━━
台本全文の冒頭に以下を記載してから、訴求を挿入した台本全文を出力してください：
[選定商材（①③用）: 商材名]
[訴求②選定商材: 商材名1、商材名2...（最大4つ）]
[選定理由: 簡潔に1〜2文]

---

"""


# ===== ユーティリティ関数 =====

def extract_video_id(url: str) -> str:
    """YouTube URL から動画IDを抽出する"""
    patterns = [
        r'(?:youtube\.com/watch\?(?:.*&)?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(f"YouTube URLから動画IDを取得できませんでした: {url}")


def get_youtube_transcript(url: str) -> str:
    """YouTube動画の文字起こしを取得する。
    TRANSCRIPT_API_URL が設定されていれば外部APIを使用（クラウドIPブロック回避）。
    なければ youtube-transcript-api で直接取得（ローカル環境向け）。
    """
    video_id = extract_video_id(url)

    # ── 方法①: 外部文字起こしAPI（TRANSCRIPT_API_URL が設定されている場合）──
    transcript_api_url = os.getenv("TRANSCRIPT_API_URL")
    if transcript_api_url:
        api_endpoint = transcript_api_url.rstrip("/") + "/transcript"
        try:
            resp = requests.get(api_endpoint, params={"video_id": video_id}, timeout=30)
            data = resp.json()
            if resp.status_code == 200 and "transcript" in data:
                return data["transcript"]
            raise ValueError(data.get("error", f"API error: HTTP {resp.status_code}"))
        except requests.RequestException as e:
            raise ValueError(f"文字起こしAPI接続失敗: {e}")

    # ── 方法②: ローカル直接取得（TRANSCRIPT_API_URL 未設定時）──
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise ImportError("youtube-transcript-api がインストールされていません。")

    api = YouTubeTranscriptApi()
    last_error = None

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
    except Exception as e:
        last_error = e

    for langs in (["ja", "ja-JP"], ["en"], None):
        try:
            fetched = api.fetch(video_id, languages=langs) if langs else api.fetch(video_id)
            return "\n".join(seg.text for seg in fetched)
        except Exception as e:
            last_error = e

    raise ValueError(f"文字起こし取得失敗: {last_error}")


def get_sheet_row(row_number: int) -> dict:
    """
    Google Sheets CSV エクスポートから指定行のデータを取得する。
    H列=企画意図, I列=YouTube URLs（複数可）, J列=サムネイメージ
    ※ シートが「リンクを知っている人が閲覧可能」に設定されている必要があります。
    """
    export_url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/export?format=csv&gid={SHEET_GID}"
    )
    resp = requests.get(export_url, timeout=30)

    # 認証エラー（HTMLが返ってくる）を検出
    if "text/html" in resp.headers.get("Content-Type", ""):
        raise PermissionError(
            "スプレッドシートにアクセスできません。\n"
            "Googleスプレッドシートの共有設定を「リンクを知っている全員が閲覧可能」に変更してください。"
        )
    resp.raise_for_status()
    resp.encoding = "utf-8"

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)

    if row_number < 1 or row_number > len(rows):
        raise ValueError(f"行番号 {row_number} は範囲外です（シートの全行数：{len(rows)}行）")

    row = rows[row_number - 1]   # 1-indexed → 0-indexed

    # 列は 0-indexed: H=7, I=8, J=9（文字起こし）, K=10（サムネイメージ）
    kikaku_ito      = row[7].strip()  if len(row) > 7  else ""
    youtube_raw     = row[8].strip()  if len(row) > 8  else ""
    gas_transcripts = row[9].strip()  if len(row) > 9  else ""  # J列: GASが書き込んだ文字起こし
    samune_image    = row[10].strip() if len(row) > 10 else ""  # K列: サムネイメージ

    # I列から YouTube URL を抽出（複数 URL / 改行・カンマ区切りに対応）
    youtube_urls = re.findall(
        r'https?://(?:www\.)?(?:youtube\.com/\S+|youtu\.be/\S+)', youtube_raw
    )

    return {
        "kikaku_ito":      kikaku_ito,
        "youtube_urls":    youtube_urls,
        "samune_image":    samune_image,
        "gas_transcripts": gas_transcripts,  # GAS事前取得済みテキスト（あれば）
    }


def get_appeal_sheet() -> str:
    """訴求プレゼント一覧スプシを読み込んでテキスト形式で返す"""
    export_url = (
        f"https://docs.google.com/spreadsheets/d/{APPEAL_SHEET_ID}"
        f"/export?format=csv&gid={APPEAL_SHEET_GID}"
    )
    resp = requests.get(export_url, timeout=30)
    if "text/html" in resp.headers.get("Content-Type", ""):
        raise PermissionError(
            "訴求スプレッドシートにアクセスできません。\n"
            "共有設定を「リンクを知っている全員が閲覧可能」に変更してください。"
        )
    resp.raise_for_status()
    resp.encoding = "utf-8"

    reader = csv.reader(io.StringIO(resp.text))
    rows   = list(reader)

    # ヘッダー行 + データ行をパイプ区切りのテキストに変換
    lines = [" | ".join(cell.strip() for cell in row) for row in rows if any(c.strip() for c in row)]
    return "\n".join(lines)


def extract_score(text: str) -> int:
    """■5.総評の「総合評価(100点満点中): XX点」からスコアを抽出する。
    コロンの後の数字を取得することで「100点満点中」の100を誤検知しない。
    """
    # パターン1: 「総合評価〜: XX点」のコロン後を取得
    m = re.search(r'総合評価[^：:\n]*[：:]\s*(\d{1,3})点', text)
    if m:
        return min(int(m.group(1)), 100)
    # パターン2: 「スコア: XX点」フォールバック
    m = re.search(r'スコア[：:]\s*(\d+)', text)
    if m:
        return min(int(m.group(1)), 100)
    return 0


def call_claude(messages: list, system: str = None) -> str:
    """Claude API を呼び出してテキストを返す。
    system を渡すと台本などのコンテキストをシステムプロンプトで渡せる。"""
    kwargs = dict(model=MODEL, max_tokens=16000, messages=messages)
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    return response.content[0].text


def send_chatwork(message: str) -> None:
    """Chatwork の指定ルームへ通知を送る（失敗してもワークフローは止めない）"""
    try:
        url = f"https://api.chatwork.com/v2/rooms/{CW_ROOM_ID}/messages"
        requests.post(
            url,
            headers={"X-ChatWorkToken": CW_TOKEN},
            data={"body": message},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        print(f"[Chatwork通知エラー] {e}")


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def human_needed_msg(score: int) -> str:
    return (
        f"評価・修正を{MAX_ITERATIONS}回繰り返しましたが、\n"
        f"スコアが {score}点 で目標の{PASS_SCORE}点に達しませんでした。\n\n"
        "次のアクションを選んでください。"
    )


def eval_revise_loop(script: str, context_label: str = ""):
    """
    評価 → 修正 を最大 MAX_ITERATIONS 回繰り返すジェネレータ。
    SSE イベントを yield しながら、最終的に
      {"type": "complete", ...} または {"type": "human_needed", ...} を yield する。

    毎回の呼び出しは独立した固定サイズのメッセージリストを使用する。
    ・評価: [台本(user), 確認(assistant), 評価プロンプト(user)]          → 3メッセージ固定
    ・修正: [台本(user), 確認(assistant), 評価プロンプト(user),
             評価結果(assistant), 修正プロンプト(user)]                  → 5メッセージ固定
    履歴を積み上げないのでイテレーションが増えてもトークン消費量が変わらない。

    context_label: ラベルに付与する追記文字列（例: "（継続）"）
    """
    for i in range(1, MAX_ITERATIONS + 1):

        # ── 評価（台本を先頭メッセージで渡し、評価プロンプトのみ最後に投げる）──
        label_e = f"評価 第{i}回{context_label}"
        yield sse({"type": "step", "step": 3, "label": label_e,
                   "message": f"台本を評価しています（{label_e}）..."})

        yield sse({"type": "message", "role": "user",
                   "label": f"評価プロンプト（自動・{label_e}）", "content": EVALUATION_PROMPT})

        # 台本をシステムプロンプトで渡し、評価プロンプトのみ user として投げる
        eval_messages = [
            {"role": "user", "content": EVALUATION_PROMPT},
        ]
        evaluation = call_claude(
            eval_messages,
            system=f"以下の台本が評価・修正の対象です。\n\n{script}",
        )
        yield sse({"type": "message", "role": "assistant",
                   "label": f"評価結果（{label_e}）", "content": evaluation})

        score = extract_score(evaluation)
        yield sse({"type": "score", "score": score, "iteration": i})

        # ── 合格判定 ──
        if score >= PASS_SCORE:
            msg = f"✅ スコア {score}点 で目標達成！（{label_e}）ワークフロー完了。"
            yield sse({"type": "complete", "success": True, "message": msg, "final_script": script})
            send_chatwork(
                f"[info][title]✅ 台本ワークフロー完了[/title]"
                f"スコア：{score}点（{label_e}でクリア）\n\n"
                f"最終台本をアプリで確認してください。[/info]"
            )
            return

        # ── 上限到達 → 選択肢を提示 ──
        if i >= MAX_ITERATIONS:
            yield sse({"type": "human_needed", "score": score,
                       "message": human_needed_msg(score), "final_script": script})
            send_chatwork(
                f"[info][title]⚠️ 台本ワークフロー：人間の確認が必要です[/title]"
                f"スコア：{score}点（目標：{PASS_SCORE}点以上）\n\n"
                f"評価・修正を{MAX_ITERATIONS}回繰り返しましたが、目標スコアに達しませんでした。\n"
                f"アプリで「さらに4回繰り返す」または「修正点を自分で指摘」を選択してください。[/info]"
            )
            return

        # ── 修正（台本＋評価結果を渡し、修正プロンプトのみ最後に投げる）──
        label_r = f"修正 第{i}回{context_label}"
        yield sse({"type": "step", "step": 4, "label": label_r,
                   "message": f"台本を修正しています（{label_r}）..."})

        yield sse({"type": "message", "role": "user",
                   "label": f"修正プロンプト（自動・{label_r}）", "content": REVISION_PROMPT})

        # 台本をシステムプロンプトで渡し、評価→修正の流れをメッセージで表現
        revision_messages = [
            {"role": "user",      "content": EVALUATION_PROMPT},
            {"role": "assistant", "content": evaluation},
            {"role": "user",      "content": REVISION_PROMPT},
        ]
        script = call_claude(
            revision_messages,
            system=f"以下の台本が評価・修正の対象です。\n\n{script}",
        )
        yield sse({"type": "message", "role": "assistant",
                   "label": f"修正済み台本（{label_r}）", "content": script})


# ===== ルーティング =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/workflow", methods=["POST"])
def run_workflow():
    data = request.get_json()
    row_number = data.get("row_number")

    if not row_number or not isinstance(row_number, int) or row_number < 1:
        return {"error": "有効な行番号を指定してください"}, 400

    def generate():
        try:
            # ────────────────────────────────────────────
            # Step 0: スプレッドシートからデータ取得
            # ────────────────────────────────────────────
            yield sse({"type": "step", "step": 0, "label": "データ取得",
                       "message": f"スプレッドシート {row_number}行目 のデータを取得しています..."})

            row = get_sheet_row(row_number)

            yield sse({"type": "message", "role": "user",
                       "label": f"企画意図（H列・{row_number}行目）",
                       "content": row["kikaku_ito"] or "（空欄）"})

            urls_text = "\n".join(row["youtube_urls"]) if row["youtube_urls"] else "（URLなし）"
            yield sse({"type": "message", "role": "user",
                       "label": f"競合YouTube URL（I列・{row_number}行目）",
                       "content": urls_text})

            yield sse({"type": "message", "role": "user",
                       "label": f"サムネイメージ（J列・{row_number}行目）",
                       "content": row["samune_image"] or "（空欄）"})

            # ────────────────────────────────────────────
            # Step 0-b: YouTube 文字起こし取得
            # K列にGASが事前取得済みの場合はそれを優先使用する
            # ────────────────────────────────────────────
            transcripts = []

            if row.get("gas_transcripts"):
                # ── GAS事前取得済み文字起こしを使用 ──
                yield sse({"type": "step", "step": 0, "label": "文字起こし読み込み",
                           "message": "K列のGAS取得済み文字起こしを使用します"})
                yield sse({"type": "message", "role": "assistant",
                           "label": f"文字起こし（GAS取得済み・K列）",
                           "content": row["gas_transcripts"]})
                transcripts.append(row["gas_transcripts"])
            else:
                # ── FlaskアプリでYouTubeから直接取得 ──
                for url in row["youtube_urls"]:
                    yield sse({"type": "step", "step": 0, "label": "文字起こし取得",
                               "message": f"YouTube文字起こしを取得しています...\n{url}"})
                    try:
                        transcript = get_youtube_transcript(url)
                        transcripts.append(f"【{url}】\n{transcript}")
                        yield sse({"type": "message", "role": "assistant",
                                   "label": f"文字起こし：{url}",
                                   "content": transcript})
                    except Exception as e:
                        err_msg = (
                            f"⚠️ 文字起こしを取得できませんでした（クラウド環境ではYouTubeのIP制限により取得できない場合があります）。\n"
                            f"K列にGASで事前取得した文字起こしを貼ると確実です。\n"
                            f"詳細: {e}"
                        )
                        transcripts.append(f"【{url}】\n（文字起こし取得失敗のため省略）")
                        yield sse({"type": "message", "role": "assistant",
                                   "label": f"文字起こし取得エラー：{url}",
                                   "content": err_msg})

            combined_transcripts = "\n\n" + "=" * 40 + "\n\n".join(transcripts) if transcripts else "（文字起こしデータなし）"

            # ────────────────────────────────────────────
            # Step 1: 設計書プロンプト組み立て → 設計書生成
            # ────────────────────────────────────────────
            yield sse({"type": "step", "step": 1, "label": "ステップ1",
                       "message": "設計書プロンプトを組み立てて、AIに設計書を生成させています..."})

            design_prompt = DESIGN_DOC_PROMPT.format(
                kikaku_ito=row["kikaku_ito"],
                transcripts=combined_transcripts,
                samune_image=row["samune_image"],
            )
            yield sse({"type": "message", "role": "user",
                       "label": "設計書プロンプト（自動生成）",
                       "content": design_prompt})

            design_doc = call_claude([{"role": "user", "content": design_prompt}])
            yield sse({"type": "message", "role": "assistant",
                       "label": "設計書", "content": design_doc})

            # ────────────────────────────────────────────
            # Step 2: 台本生成
            # ────────────────────────────────────────────
            yield sse({"type": "step", "step": 2, "label": "ステップ2",
                       "message": "台本を生成しています..."})

            script_prompt = SCRIPT_CREATION_PROMPT.format(design_doc=design_doc)
            yield sse({"type": "message", "role": "user",
                       "label": "台本作成プロンプト（自動）", "content": script_prompt})

            script = call_claude([{"role": "user", "content": script_prompt}])
            yield sse({"type": "message", "role": "assistant",
                       "label": "台本 初版", "content": script})

            # ────────────────────────────────────────────
            # Step 3+: 評価 → 修正ループ（共通関数に委譲）
            # ────────────────────────────────────────────
            yield from eval_revise_loop(script)

        except Exception as e:
            yield sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/continue", methods=["POST"])
def continue_workflow():
    """
    human_needed 後のユーザー選択を処理するエンドポイント。
    action = "repeat"  → さらに4回の評価・修正ループを実行
    action = "manual"  → ユーザーの修正指示を AI に渡して台本を修正 → 評価 → ループ
    """
    data = request.get_json()
    action           = data.get("action", "repeat")          # "repeat" | "manual"
    script           = data.get("script", "")
    user_instructions = data.get("user_instructions", "").strip()

    if not script:
        return {"error": "台本データがありません"}, 400

    def generate():
        nonlocal script
        try:
            if action == "manual":
                # ── ユーザーの修正指示で台本を改訂 ──
                yield sse({"type": "step", "step": 4, "label": "手動修正",
                           "message": "ご指摘を元に台本を修正しています..."})

                manual_prompt = (
                    "【ユーザーからの修正指示】\n"
                    f"{user_instructions}\n\n"
                    "上記の指示内容をすべて反映した台本全文を出力してください。6000文字以上で。"
                )
                yield sse({"type": "message", "role": "user",
                           "label": "手動修正プロンプト", "content": manual_prompt})

                # 台本をシステムプロンプトで渡し、修正指示のみ user として投げる
                script = call_claude(
                    [{"role": "user", "content": manual_prompt}],
                    system=f"以下の台本が修正の対象です。\n\n{script}",
                )
                yield sse({"type": "message", "role": "assistant",
                           "label": "手動修正済み台本", "content": script})

                # 手動修正済み台本で評価・修正ループへ
                yield from eval_revise_loop(script, context_label="（手動修正後）")

            else:
                # ── さらに5回繰り返す ──
                yield from eval_revise_loop(script, context_label="（継続）")

        except Exception as e:
            yield sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/appeal", methods=["POST"])
def insert_appeal():
    """
    合格台本（75点以上 or ユーザー貼り付け）に訴求3か所を挿入するエンドポイント。
    script: 訴求を挿入する台本テキスト
    """
    data   = request.get_json()
    script = data.get("script", "").strip()

    if not script:
        return {"error": "台本データがありません"}, 400

    def generate():
        try:
            yield sse({"type": "step", "step": 5, "label": "訴求挿入",
                       "message": "プレゼントスプシを読み込んで訴求を挿入しています..."})

            appeal_data = get_appeal_sheet()

            prompt = APPEAL_PROMPT.format(appeal_data=appeal_data) + script
            yield sse({"type": "message", "role": "user",
                       "label": "訴求挿入プロンプト（自動）", "content": prompt})

            result = call_claude([{"role": "user", "content": prompt}])
            yield sse({"type": "message", "role": "assistant",
                       "label": "✅ 訴求入り完成台本", "content": result})

            yield sse({"type": "appeal_complete",
                       "message": "✅ 訴求挿入が完了しました！最終台本が完成しました。",
                       "final_script": result})

            send_chatwork(
                f"[info][title]✅ 訴求挿入完了 - 台本が完成しました[/title]"
                f"訴求（CTA）3か所の挿入が完了しました。\n"
                f"アプリで最終台本をご確認ください。[/info]"
            )

        except Exception as e:
            yield sse({"type": "error", "message": str(e)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
