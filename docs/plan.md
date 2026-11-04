# hil-eval 実装計画（3週間 MVP・ドラフト）

## 担当
| 担当 | 役割 |
|---|---|
| Claude Code | アーキテクチャ、コアAPI設計、閾値アルゴリズム、レビュー/エッジケース検討、PRレビュー |
| Codex | スキャフォールド、CLI、エクスポータ、テストの高速実装 |
| ひかり bot | レビューUIフロントエンド（Vite + React + TypeScript、ビルド済み静的ファイルを FastAPI から配信） |
| つち bot | ストレージ・HTTP APIバックエンド、パッケージング |
| かぜ bot | CI、GitHub Actions、PyPIリリース |
| オーナー（inatani） | open-questions の意思決定、最終レビュー、dogfood |

## git worktree による並列化
```
git worktree add ../hil-core     -b feat/core        # Claude Code: models/protocols/decorator/tuning
git worktree add ../hil-scaffold -b feat/scaffold    # Codex: CLI/exporters/tests
git worktree add ../hil-ui       -b feat/ui          # ひかり
git worktree add ../hil-backend  -b feat/backend     # つち
git worktree add ../hil-ci       -b feat/ci          # かぜ
```
ルール:
- Day 1–2 に `src/hil_eval/types.py`（Pydanticモデル）・`store/base.py`（Storeプロトコル）・`openapi.yaml` を Claude Code が確定し main にマージ → 全員そこから分岐（契約先行）
- ディレクトリ所有を分離（core/ → Claude, cli/ exporters/ → Codex, ui/ → ひかり, store/ api/ → つち, .github/ → かぜ）して衝突最小化
- 小さいPRを毎日 main へ rebase マージ、CI緑が必須。統合は毎週金曜

## Week 1: 基盤と契約
| 担当 | タスク |
|---|---|
| Claude Code | アーキ文書(docs/architecture.md)、型定義・Storeプロトコル・OpenAPI確定、`@review` デコレータ設計（sync/async, fail-open）、judge インターフェース |
| Codex | uv/hatchling スキャフォールド、Typer CLI骨格（ui/serve/export/tune）、pytest基盤・fixtures |
| つち | SQLite Store実装（SQLModel）、blob保存（sha256）、マイグレーション方針 |
| ひかり | Vite+React+TS 雛形（`ui/`）、UIワイヤーフレーム、キュー一覧画面（モックデータ） |
| かぜ | GitHub Actions（lint: ruff, type: mypy/pyright, test: 3.10–3.13 マトリクス）、pre-commit |
**マイルストーン M1**: `@review` で SQLite に低信頼度アイテムが入る。CI緑。

## Week 2: UI・API・エクスポート
| 担当 | タスク |
|---|---|
| Claude Code | 閾値調整 v0（IPW・Wilson上限・変化幅制限）実装と単体テスト、audit_rate サンプリング、エッジケース洗出し（巨大画像、非JSON出力、例外、並行書込み） |
| Codex | エクスポータ jsonl / openai-ft / eval-set、`hil-eval export`/`tune` CLI、テスト拡充 |
| つち | FastAPI: `/v1/items` CRUD、review、APIキー認証、画像配信、`hil-eval serve` |
| ひかり | 詳細画面（画像/テキスト/JSONフォーム編集/diff）、Approve/Edit/Reject、ショートカット |
| かぜ | CI で `vite build`→wheel に UI 静的資産同梱の検証、TestPyPI 公開ワークフロー（trusted publishing） |
**マイルストーン M2**: UIでレビュー→修正→JSONLエクスポートまで E2E で動く。

## Week 3: dogfood・仕上げ・リリース
| 担当 | タスク |
|---|---|
| Claude Code | 全体コードレビュー、README/クイックスタート監修 |
| Codex | examples/（text LLM, VLM画像）、カバレッジ80%到達 |
| つち | パッケージメタデータ、`hil-eval serve` のドキュメント |
| ひかり | UI磨き込み、Playwright E2E 最小、スクリーンショット/GIF（README用） |
| かぜ | v0.1.0 PyPI リリース＋GitHubリポジトリ public 化、リリースノート自動化、バッジ |
**マイルストーン M3**: `pip install hil-eval` 公開、リポジトリ public 化（v0.1.0）。

## MVP後
- diet-mcp dogfooding（HTTP sidecar を Fly.io、Volume, Dockerfile、TS 送信スニペット）、1週間分データで tune 実行、成果を記事化

## リスク対応 / バッファ
- 各週金曜は統合・バッファ。遅延時は HF形式、TSクライアント、UIの磨き込み（diff表示・ショートカット）を削る
- 契約変更は Claude Code の承認必須（ADRを docs/adr/ に残す）

## Expanded M3

The expanded M3 in [m3-plan.md](m3-plan.md) supersedes the original three-week-plan M3 milestone row. Phases 1–6 cover the contracts in [ADR 0003](adr/0003-m3-contract.md), webhooks, judges, Parquet exports, OpenAPI/TypeScript client, review UI, and CI/TestPyPI delivery.
