# PDF 論文の英語+対訳 HTML 化 手順書

対象: arXiv HTML 版が無い論文の PDF（学会予稿など）。arXiv HTML 版があるならそちら（`bilingual-paper arxiv`、
`docs/arxiv-bilingual-html.md`）の方が数式・図表の再現度が高いので優先する。実装は `src/bilingual_paper/pdf.py`。

## できあがるもの

PDF をレイアウト解析して Markdown（数式は LaTeX）に変換し、それを HTML にしたうえで、各見出し・段落・
箇条書き項目・図表キャプションの直後に訳文（既定は日本語、`--target-lang` で変更可）を差し込んだ 1 つの
HTML ファイル。数式は MathJax（CDN）でブラウザ描画し、図は base64 で HTML に埋め込まれるので単体で開ける。

## 前提

翻訳側は `bilingual-paper arxiv` と同じ（Vertex AI の Gemini、ADC 認証、`GOOGLE_CLOUD_PROJECT` 必須）。
加えて PDF → Markdown 変換ツールを **別途** インストールする（重いモデル依存とライセンスを本体に持ち込まない
ため、外部コマンドとして呼ぶ）。

```bash
uv tool install "mineru[core]"   # 既定。mineru-kit コマンドが入る。初回実行時にモデル約 3GB を取得
uv tool install docling          # 代替（--converter docling）
```

| 変換ツール | 段落の結合（段・ページ跨ぎ） | 行内数式 | 別行数式 | コード | 所要時間（11 ページ, M4） | ライセンス |
|---|---|---|---|---|---|---|
| MinerU（既定） | ○ | LaTeX 化（余計なアクセント記号が付くことがある） | ○ | ○ | 約 2 分 | Apache-2.0 + 追加条項（月間 1 億 MAU / 月商 2,000 万ドル超は商用ライセンス要） |
| docling | × | 変換されない（元テキストのまま） | △（ゴミ混入あり） | ○ | 約 3.5 分（数式込み） | MIT |

Marker は現行版が `llama-server`（llama.cpp）のシステムインストールを要求するため対象外にした。

## 実行

```bash
uv run bilingual-paper pdf <PDF> [--converter mineru|docling] [--converter-bin PATH] [--converter-arg ARG ...] \
    [--reconvert] [--output PATH] [--checkpoint PATH] [--model MODEL] [--target-lang CODE]
```

- `--target-lang` の扱いは `arxiv` と同じ。チャットからの依頼では指示文の言語から推定して渡す。
- 変換結果は `work/<slug>/<converter>/<slug>.md` にキャッシュされ、再実行時は変換を飛ばして翻訳だけ行う。
  変換をやり直すときは `--reconvert`（`--converter-arg` を変えたときも必要）。
- `--converter-arg` は変換ツールにそのまま渡す追加引数（複数可）。例: スキャン PDF を MinerU で OCR させる
  `--converter-arg=--ocr-mode --converter-arg=ocr`、docling の OCR 言語指定など。
- 変換ツールが PATH に無い場合は `--converter-bin` で実行ファイルを直接指定できる。
- 出力先・チェックポイントの既定値、チェックポイントからの再開、Gemini のリトライ、`--model` の選び方は
  `arxiv` と同じ（`<slug>` は PDF のファイル名）。

MinerU は `--remote` を付けない限りローカルで解析する（本ツールは付けない）。`mineru-kit parse` はテレメトリを
送らない（テレメトリは別コマンド `mineru` の文書ライブラリサーバ側の機能）。

## 内部の仕組み（トラブル時に読む）

1. **`convert_pdf`**: 変換ツールを実行し Markdown を得る。MinerU は既定で先頭 10 ページしか解析しないため
   `-p all` を付ける。docling は既定の `docling_parse` バックエンドだと LaTeX 由来の詰まったフォントで単語間の
   空白が消えるため `--pdf-backend pypdfium2` を付け、`--enrich-formula` で数式を LaTeX 化する。
2. **`markdown_to_html`**: markdown-it（CommonMark + 表 + `dollarmath`）で HTML 化する。`$...$` / `$$...$$` は
   `<span class="math inline">\(...\)</span>` / `<div class="math block">\[...\]</div>` にして MathJax に渡す。
   変換ツールが出す生 HTML（`<table>`, `<sup>` など）は通すが、`script` 等と `on*` 属性・`javascript:` リンクは除去する。
3. **`collect_units`**: 見出し・段落・箇条書き項目を文書順に集める。`pre`（コード）と `table` の中、
   文字を含まない段落（ページ番号など）は対象外。"Abstract" 見出しがあれば、それより前の段落（著者・所属）は
   訳さない。"References" / "Bibliography" 見出しから次の見出しまでの段落（書誌）も訳さない。
   `Figure 1:` / `Table 2.` / `Listing 3:` などで始まる段落はキャプション (`tr-caption`) として扱う。
4. 以降は `arxiv` と共通: 数式・`code`・リンク・`sub`/`sup` を `@@N@@` プレースホルダにして Gemini に渡し
   (`extract_segment`, `translate_units`)、訳文を元要素の直後（箇条書き項目は `<li>` の中）に挿入する
   (`apply_translations`)。

## 検証手順（毎回やること）

`arxiv` と同じ（placeholder の warning 確認、簡易サーバ経由でブラウザ表示確認）。加えて:

- 段落が段・ページの切れ目で分断されていないか、逆に別段落が結合されていないか。
- 数式が MathJax で描画されているか（赤字の TeX エラーが出ていないか）。

## 既知の制約

- 数式は変換ツールの認識結果に依存する。MinerU は行内数式に余計な `\dot{}` / `\tilde{}` / `\vec{}` を付けたり、
  等幅フォントのコード片を数式と誤認して CJK 文字を混ぜたりすることがある（例: `$乜 _{i}$`）。本ツールは修正しない。
- 表は翻訳しない（数値主体で、複雑な表は変換時点で崩れていることがあるため）。キャプションは訳す。
- コードリスト内のキャプション（MinerU がコードブロックに含めた `Listing N: ...`）は訳されない。
- 著者名の分音記号が崩れることがある（例: `Köppl` → `Koppl ¨`）。
