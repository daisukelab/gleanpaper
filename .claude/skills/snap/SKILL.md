---
name: snap
description: 論文URL（arXiv・OpenReview・IEEE Xplore）を受け取り、HTML/PDFを読み取って日本語6項目で要約し、digest/に保存する
argument-hint: <paper-url>
---

# snap — 論文即時要約

$ARGUMENTS に指定された論文 URL を読み取り、日本語 6 項目で要約して `digest/` に保存してください。

## 手順

1. **メタデータ取得**: URL の HTML ページを WebFetch で取得し、タイトル・著者・投稿日・アブストラクトを抽出する
2. **PDF 取得**: 可能であれば PDF も WebFetch で読み取り、本文の内容を把握する
   - arXiv の場合: `https://arxiv.org/pdf/{id}` を試みる
   - PDF が取得できない場合はアブストラクトのみで要約する
3. **要約**: 下記フォーマットで要約を作成する
4. **ファイル保存**: `digest/YYYY-MM/` 以下に保存する
5. **チャット出力**: 保存したファイルの内容をチャットにも表示する

## digest ファイル保存ルール

### 保存先
```
digest/{date_published の YYYY-MM}/{source}_{source_id}_{slug}.md
```

- `slug`: タイトルの最初のコロン（`:`）より前の部分を使い、記号を除いてスペースをハイフンに置換、先頭40文字
- 例: `arxiv_2602.06180_STACodec.md`

### ファイルフォーマット

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
score: null
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

### タグ候補
`config/interests.yaml` が存在する場合はそれを読んで topics のタグを参照し、論文内容に照合して付与する。
存在しない場合は論文の内容から適切なタグを判断する。

## 記述ルール

- **論文に書かれていることのみ**を記述する。推測・補足・類推は厳禁
- 各項目はできるだけ **1 行**で簡潔に。重要な点が複数ある場合は各 1 行で列挙
- 利用・言及された既存モデル／データセット／手法は、関連する行の**直後に箇条書き**で記載する（例: `  - HuBERT`）
- 情報が論文に記載されていない項目は「（論文に記載なし）」と書く
- `--skip-pdf` が $ARGUMENTS に含まれる場合、またはPDF取得に失敗した場合は「（アブストラクトからは不明）」と書く
