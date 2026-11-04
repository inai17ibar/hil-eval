# hil-eval 要件定義（ドラフト v0.1）

> 仮称 `hil-eval`。名称は open-questions.md Q1 参照。

## 1. 概要
AI出力の評価に Human-in-the-Loop（HIL）レビューを「数行で」追加できる Python OSS フレームワーク。

フロー: **自動判定 → 低信頼度のアイテムだけ人間レビューへ → 人間の修正をデータとして蓄積 → 閾値を自動調整**

## 2. ゴール / 非ゴール
### ゴール
- G1: デコレータ1つで自動判定＋低信頼度アイテムのキュー投入ができる（導入5分以内）
- G2: テキストと画像（VLM出力）の両方をローカルUIでレビューできる
- G3: 人間の修正を評価セット / ファインチューニング形式（JSONL）でエクスポートできる
- G4: 人間の修正結果から信頼度閾値を自動チューニングする
- G5: 就職活動用ポートフォリオとして、設計・テスト・CI・ドキュメントの質を示せる

### 非ゴール（MVP）
- マルチユーザー認証・権限管理、ホスティング版（SaaS）
- トレーシング / オブザーバビリティ全般（LangSmith等の領域とは競合しない）
- 大規模分散キュー（Redis/Kafka等）、アノテーター間一致率などの高度なラベリング機能
- 音声・動画対応

## 3. ペルソナ
| ペルソナ | 状況 | ニーズ |
|---|---|---|
| P1 個人開発者（例: diet-mcp作者） | LLM/VLMアプリを個人で運用 | 重い基盤なしで怪しい出力だけ確認したい |
| P2 小規模チームのMLエンジニア | LLM-as-judge評価を運用中 | judgeの誤りを人間が直し、評価セットを育てたい |
| P3 ドメインエキスパート（非エンジニア） | 栄養士・医療者など | ブラウザで画像と出力を見て、直すだけにしたい |

## 4. ユーザーストーリー
- US1: P1として、既存関数に `@hil.review(...)` を付けるだけで低信頼度出力をキューに入れたい。
- US2: P3として、`hil-eval ui` で開いた画面で画像＋モデル出力を見て、承認/修正/却下したい。
- US3: P2として、修正済みデータを `hil-eval export --format openai-ft` でJSONL出力したい。
- US4: P2として、人間修正が溜まったら閾値の推奨値（と期待レビュー率・誤り見逃し率）を知りたい。
- US5: P1として、TypeScript製サービス（diet-mcp）からHTTP経由でアイテムを投入したい。
- US6: P2として、キーボードショートカットで高速にレビューしたい。

## 5. 機能要件（MVP）
### F1 デコレータ / SDK
- F1.1 `@review` デコレータ: 関数の入力・出力を記録し、judge関数で信頼度(0–1)を算出
- F1.2 信頼度 < 閾値 ならレビューキューへ。関数の戻り値はそのまま返す（非ブロッキング、デフォルト）
- F1.3 judge は (a) ユーザー定義関数 (b) 組込み: LLM-as-judge（OpenAI/Anthropic互換）、logprobベース、ルールベース
- F1.4 同期/非同期関数両対応、例外時もアプリを止めない（fail-open）
- F1.5 サンプリング: 高信頼度アイテムも `audit_rate`（例 2%）でランダムにレビューへ（閾値調整のための偏り補正）
- F1.6 画像入力: パス / bytes / PIL.Image / URL を受け、ローカルに content-addressed 保存

### F2 ストレージ
- F2.1 SQLite（デフォルト `./.hil/hil.db`）、画像は `./.hil/blobs/<sha256>`
- F2.2 ストレージ抽象 `Store` プロトコル（将来 Postgres 対応）

### F3 レビューUI
- F3.1 `hil-eval ui` でローカル起動（127.0.0.1 のみ）
- F3.2 キュー一覧（フィルタ: project / status / 信頼度範囲）
- F3.3 詳細画面: 入力（テキスト/画像）、出力、judge理由、信頼度。操作: Approve / Edit&Approve / Reject / Skip
- F3.4 出力がJSONの場合はフォーム編集（スキーマ推定）、テキストはdiff表示
- F3.5 キーボードショートカット（A/E/R/J/K）

### F4 HTTP API
- F4.1 UIと同じ FastAPI で `POST /v1/items`, `GET /v1/items`, `POST /v1/items/{id}/review` を提供
- F4.2 オプションのAPIキー（ヘッダ）で非ローカル利用時に保護（diet-mcp連携用）

### F5 エクスポート
- F5.1 `hil-eval export --format {jsonl,openai-ft,eval-set,hf} --status corrected`
- F5.2 Python API `hil.export(...)`

### F6 閾値自動調整
- F6.1 `hil-eval tune` で推奨閾値とメトリクスを表示、`--apply` でプロジェクト設定に保存
- F6.2 デコレータは `threshold="auto"` で保存済み値を利用

## 6. API スケッチ
```python
import hil_eval as hil

hil.init(project="diet-mcp", store="sqlite:///.hil/hil.db")

def judge_calories(inp, out) -> hil.Judgment:
    # 任意のロジック / LLM-as-judge
    return hil.Judgment(confidence=0.62, reason="量の推定が曖昧", label="pass")

@hil.review(
    judge=judge_calories,          # または hil.judges.llm(model="gpt-4o-mini", rubric="...")
    threshold=0.8,                 # または "auto"
    audit_rate=0.02,
    inputs=["image", "text"],      # 画像として扱う引数名
    tags={"model": "vlm-v1"},
)
def estimate_meal(image: bytes, text: str) -> dict:
    ...

# デコレータを使わない場合
hil.submit(input={...}, output={...}, confidence=0.4)
```
CLI:
```
hil-eval ui [--port 8765]
hil-eval export --format openai-ft -o train.jsonl
hil-eval tune --project diet-mcp [--target-miss-rate 0.05] [--apply]
hil-eval serve --api-key $KEY      # HTTP API のみ（sidecar用）
```

## 7. 技術選定
- Python 3.10+（Q3）、パッケージ管理 uv、ビルド hatchling
- バックエンド: FastAPI + SQLModel(SQLAlchemy) + SQLite
- UI: **推奨 HTMX + Jinja2（ビルド不要・wheel同梱が容易）**。代替: Vite+React をビルド済み静的ファイルとして同梱（Q4）
- テスト: pytest, Playwright（UI E2E 最小限）

## 8. 閾値自動調整アルゴリズム v0
前提: 人間がレビューしたアイテムには「judgeの判定が正しかったか」(y∈{0,1}) が付く。

1. データ: レビュー済みアイテムの (confidence c_i, y_i)。閾値以上から `audit_rate` で抽出したものは逆確率重み w_i=1/audit_rate、閾値未満は w_i=1（選択バイアス補正）
2. 候補閾値 t を 0.00–1.00 で0.01刻みに走査し:
   - レビュー率 R(t) = P(c < t)
   - 見逃し率 M(t) = 重み付き P(y=0 | c ≥ t)
3. 制約 M(t) ≤ target_miss_rate（デフォルト5%）を満たす最小の t を推奨（=レビュー負荷最小）
4. 不確実性: M(t) の Wilson 95%上限で判定（保守的）。サンプル数 < 30 なら「データ不足」で現状維持
5. 急変防止: 1回の変更幅を ±0.1 に制限
- v1候補: isotonic regression による信頼度キャリブレーション、タグ（モデル/カテゴリ）別閾値

## 9. データモデル
```
Project(id, name, threshold, target_miss_rate, audit_rate, created_at)
Item(id, project_id, fn_name, input_json, output_json, confidence, judge_label,
     judge_reason, routed_by{low_conf|audit|manual}, status{pending|approved|corrected|rejected|skipped},
     tags_json, trace_id, created_at)
Asset(sha256, mime, path, width, height)   ItemAsset(item_id, sha256, role{input|output})
Review(id, item_id, reviewer, action, corrected_output_json, judge_correct(bool), note, created_at)
ThresholdHistory(id, project_id, old, new, n_samples, est_review_rate, est_miss_rate, created_at)
```

## 10. エクスポート形式
- `jsonl`: 1行1アイテム（input, output, corrected_output, confidence, judge_correct, tags）
- `openai-ft`: `{"messages":[{"role":"user","content":[text, image_url(base64)]},{"role":"assistant","content":corrected}]}`
- `eval-set`: `{"input":..., "expected":..., "metadata":...}`（promptfoo / Braintrust / Langfuse dataset 取込互換を目指す）
- `hf`: HuggingFace datasets 形式（Parquet + 画像列）※MVPではstretch

## 11. 成功指標
- 導入: README通りに `pip install` → 初レビューまで 5分以内、追加コード ≤ 5行
- 品質: テストカバレッジ 80%+、CI緑、PyPI公開
- 有効性（diet-mcp dogfood）: 閾値調整後、見逃し率 ≤5% を保ちつつレビュー率を固定閾値比 30%以上削減
- 外部反応（3か月）: GitHub ★100、外部コントリビュータ1名以上、技術記事1本（Zenn/英語ブログ）

## 12. 競合比較
| 観点 | hil-eval | LangSmith | Braintrust | Langfuse |
|---|---|---|---|---|
| 形態 | OSSライブラリ（ローカル） | SaaS（一部セルフホスト有償） | SaaS | OSS + SaaS |
| HIL導入の手軽さ | デコレータ1つ・低信頼度のみ自動振分 | Annotation Queue（手動/ルール投入） | 人手レビュー機能あり | Annotation Queue |
| 信頼度ベース自動ルーティング | ◎ 中核機能 | △ ルール設定 | △ | △ |
| 閾値自動調整 | ◎ | × | × | × |
| 画像/VLM出力レビュー | ◎ 第一級 | ○ 表示可 | ○ | ○ |
| 修正→FT用JSONL | ◎ | ○ Dataset経由 | ○ | ○ |
| トレーシング | × 非ゴール | ◎ | ◎ | ◎ |
| 依存の軽さ | pip一発・SQLite | 要アカウント | 要アカウント | Docker/Postgres等 |
※競合機能は2026年時点で要再確認。共存（エクスポートで取込）を打ち出す。

## 13. diet-mcp との連携（dogfooding）
diet-mcp は TypeScript / Fly.io のため Python デコレータは直接使えない。
- **案A（推奨・MVP）HTTP sidecar**: `hil-eval serve` を別 Fly アプリ（または同一マシンの別プロセス）で起動し、diet-mcp がカロリー推定後に `POST /v1/items` を fire-and-forget。信頼度は diet-mcp 側のLLM自己評価 or hil-eval 側 judge で算出
- 案B: ログ/JSONLをバッチ取込（`hil-eval import`）— 最小改修
- 案C（MVP後）: 薄い TS クライアント `@hil-eval/client`（OpenAPIから生成）
- 注意: Fly上のSQLiteは Volume 必須。画像を含むため個人データの扱い（ローカル限定・保持期間）を明記

## 14. リスク
| リスク | 影響 | 対策 |
|---|---|---|
| 競合が同等機能を追加 | 差別化消失 | 「軽さ」「閾値自動調整」「VLM」に集中、エクスポートで共存 |
| judge信頼度がキャリブレーションされていない | 振分精度低下 | audit_rate + 逆確率重み、v1でキャリブレーション |
| スコープ膨張（3週超過） | 未完成 | 非ゴール厳守、HF形式/TSクライアントはstretch |
| UIのwheel同梱が複雑化 | 配布難 | HTMX採用でビルド不要化 |
| 画像に個人情報 | 法的/信頼 | ローカル保存デフォルト、APIはキー必須、保持期間設定 |
| 複数エージェント並行開発での衝突 | 手戻り | インターフェース先行確定・worktree分離・契約テスト |
