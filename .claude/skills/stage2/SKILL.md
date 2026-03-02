---
name: stage2
description: review/YYYY-MM-DD.md のタグ付き論文を snap 相当の処理で一括 digest 化し、処理後に review/ と screened/ をアーカイブする
argument-hint: [YYYY-MM-DD]
---

# stage2 — タグ付き論文の一括 digest 化

`$ARGUMENTS` に日付（YYYY-MM-DD）が指定された場合はその review ファイルを、指定がなければ `review/` 以下で最新の `.md` ファイルを対象として処理します。

## 手順

### 1. review ファイルの特定

- 引数あり: `review/YYYY-MM-DD.md` を使用
- 引数なし: `review/*.md` を glob し、ファイル名降順で最新を選択

review ファイルが `review/` に存在しない場合（すでに `archive/review/` に移動済みの場合）は「処理済みです」と表示して終了する。

### 2. タグ付き論文の抽出

review ファイルを読み込み、**`tags:` 行が空でない論文**（ユーザーがタグを書いた論文）のみを処理対象にする。

review ファイルの構造:
```
<!-- source: arxiv | id: 2602.12345 | score: 98.0 -->
### タイトル
著者 | カテゴリ | [arxiv](...) | [PDF](...)
`suggested: タグ候補`
> アブストラクト抜粋...

tags: audio-repr, speech    ← ここが空でなければ処理対象
```

抽出した論文ごとに `source`・`source_id`・`tags`（カンマ区切りリスト）を記録する。

### 3. screened JSON から論文メタデータを取得

対象日付の `screened/YYYY-MM-DD.json`（なければ `archive/screened/YYYY-MM-DD.json`）を Read ツールで読み込み、`source_id` をキーに各論文の以下フィールドを取得する:
- `title`, `authors`, `abstract`, `url`, `pdf_url`, `date_published`, `score`

### 4. 各論文を snap 相当で処理

タグ付き論文を **score 降順**で処理する。各論文について:

1. **既存 digest の確認（スキップ判定）**
   処理前に `digest/YYYY-MM/source_sourceId_slug.md` が既に存在するか確認する。
   存在する場合は **スキップ**し、`[skip] タイトル` と表示して次の論文へ進む。

2. **PDF 取得**
   `pdf_url`（arXiv なら `https://arxiv.org/pdf/{id}`）から PDF をダウンロードし、Bash で `pypdf` を用いてテキストを抽出する。取得失敗時はアブストラクトのみで処理する。

3. **要約生成**
   snap と同じ記述ルールで6項目の日本語要約を作成する。PDF が取得できた場合は本文全体を参照し、失敗した場合はアブストラクトのみから作成する。

4. **`config/interests.yaml` を参照**
   存在する場合は `topics` のタグ定義を読み込み、review ファイルに書かれたタグと照合して適切なタグを確認する（基本的に review のタグをそのまま使う）。

5. **digest ファイルの書き込み**
   snap と同じフォーマットで `digest/YYYY-MM/` に保存する。

### 5. アーカイブ

全論文の処理が完了したら、以下のファイルを移動する:
- `review/YYYY-MM-DD.md` → `archive/review/YYYY-MM-DD.md`
- `screened/YYYY-MM-DD.json` → `archive/screened/YYYY-MM-DD.json`

移動先ディレクトリが存在しない場合は作成する。Bash の `mv` コマンドを使用する。

アーカイブ先に同名ファイルが既に存在する場合は警告を出して上書きせずスキップする。

### 6. 結果の表示

処理終了後、チャットに以下を表示する:
- 処理した日付
- 対象論文数（新規保存 / スキップ）
- 各 digest ファイルのパス
- アーカイブ先

## digest ファイルフォーマット（snap と共通）

```markdown
---
title: "{タイトル（ダブルクォートはシングルクォートに置換）}"
source: {arxiv / openreview / ieee}
source_id: "{ID}"
url: "{URL}"
authors:
  - "{著者1}"
  - "{著者2}"
  （5名超は最後に "et al." を追加）
date_published: "{YYYY-MM-DD}"
date_gleaned: "{今日の日付 YYYY-MM-DD}"
citation_count: null
tags:
  - {タグ1}
  - {タグ2}
score: {スコア数値}
---

# {タイトル}

**Authors**: {著者カンマ区切り}
**Published**: {YYYY-MM-DD}
**Citations**: 取得不可
**Tags**: `{タグ1}` `{タグ2}`
**URL**: {URL}
**PDF**: {PDF URL}

### 1. どんなもの？

### 2. 先行研究と比べてどこがすごい？

### 3. 技術や手法の肝はどこ？

### 4. どうやって有効だと検証した？

### 5. 議論はある？

### 6. 次に読むべき論文は？

## Abstract

{原文アブストラクト}
```

### slug の生成ルール
- タイトルの最初のコロン（`:`）より前の部分を使用
- 記号を除去し、スペースをハイフンに置換
- 先頭 40 文字
- 例: `arxiv_2602.21772_UniWhisper.md`

## 記述ルール（snap と共通）

- **論文に書かれていることのみ**を記述する。推測・補足・類推は厳禁
- 各項目はできるだけ **1 行**で簡潔に。重要な点が複数ある場合は各 1 行で列挙
- 利用・言及された既存モデル／データセット／手法は、関連する行の**直後に箇条書き**で記載する（例: `  - HuBERT`）
- PDF が取得できた場合は「（論文に記載なし）」、取得できなかった場合は「（アブストラクトからは不明）」を使う

## アーカイブの仕様

```
archive/
  review/
    YYYY-MM-DD.md     ← review/YYYY-MM-DD.md から移動
  screened/
    YYYY-MM-DD.json   ← screened/YYYY-MM-DD.json から移動
```

- ファイルが存在しない場合は警告を出してスキップし、処理を継続する
- アーカイブ先に同名ファイルが存在する場合は警告を出して上書きしない（スキップ）
