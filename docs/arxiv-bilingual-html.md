# arXiv HTML 論文の英語+対訳 HTML 化 手順書

対象: `https://arxiv.org/html/<arXiv ID>` で公開されている、LaTeXML 生成の arXiv HTML 論文。
すでに構造化された HTML（MathML 入り）をそのまま使い、`bilingual-paper arxiv` 1コマンドで完結する
（実装は `src/bilingual_paper/arxiv.py`）。

## できあがるもの

元の arXiv HTML ページの DOM 構造・CSS・MathML・図表・脚注リンクをすべてそのまま残し、
各段落・見出し・図表キャプションの直後に、対応する翻訳（既定は日本語、`--target-lang` で変更可）を
差し込んだ 1 つの HTML ファイル。数式・引用番号・相互参照リンク（例: "Section 3", "[5]", "Table 1"）は
原文のまま複製されるので、翻訳側でも数式や参照リンクがそのまま機能する。

## 前提

```bash
uv sync
gcloud auth application-default login   # 初回のみ
export GOOGLE_CLOUD_PROJECT=<GCPプロジェクトID>   # gcloud config get-value project で確認可
```

Gemini 呼び出しは Vertex AI 経由（API キー不要、ADC 認証）。`GOOGLE_CLOUD_LOCATION` は省略可（既定 `global`）。

## 実行

```bash
uv run bilingual-paper arxiv <SOURCE> [--output PATH] [--checkpoint PATH] [--model MODEL] [--base-href URL] [--target-lang CODE]
```

`--target-lang` は既定 `ja`（日本語）。`ko`（韓国語）、`zh-Hans`（簡体字中国語）、`zh-Hant`（繁体字中国語）など
`src/bilingual_paper/languages.py` の `LANGUAGES` に載っている言語コードを指定できる（それ以外はその場でエラー）。チャットからの依頼で
実行する場合は、ユーザーの指示文の言語から対象言語を推定して渡すこと（例: 韓国語で依頼されたら `--target-lang ko`）。

`SOURCE` には次のいずれかを渡せる。すべて内部で同じ処理に正規化される。

- 裸の arXiv ID: `2307.01412` / `2307.01412v4`
- arXiv の URL: `https://arxiv.org/abs/2307.01412`、`/pdf/...`、`/html/...` のどれでも可
- ローカルに保存済みの arXiv HTML ファイルのパス（ブラウザで Save As したものなど）

ID/URL を渡した場合は `https://arxiv.org/html/<id>` を直接 fetch する（ブラウザでの事前ダウンロードは不要）。
ローカルファイルを渡した場合は、ページ右上の透かし（`arXiv:2307.01412v4 [cs.DS] ...`）から ID を自動検出する
（透かしが無ければ `<article>` より前の `arXiv:<id>` / `arxiv.org/abs/<id>` を探す。本文中で引用されている他論文の
ID は拾わない）。arXiv に HTML 版が無い論文（404）はその旨のエラーになる。

出力先を省略すると:

- 本体: `outputs/<slug>-<lang>-bilingual.html`
- 翻訳チェックポイント: `work/<slug>/<lang>/translations.json`

（`<slug>` は検出できた arXiv ID、検出できなければローカルファイル名。`<lang>` は `--target-lang`）

どちらも言語ごとに分かれるので、同じ論文を別の言語で訳し直しても上書きや訳の混入は起きない。

進捗はバッチ単位（既定 12 件）でチェックポイントに保存されるので、API エラー等で中断しても再実行すれば
翻訳済み分はスキップされ、途中から再開できる。チェックポイントには対象言語と各ユニットの英文が記録されており、

- 言語が `--target-lang` と違うチェックポイントを渡すとエラーになる。
- 論文の改版などで英文が変わったユニットは、古い訳を使わずに訳し直す。
- 旧形式（言語・英文を記録していない）のチェックポイントはエラーになるので、削除してやり直す。

Gemini の一時的なエラー（5xx/429、空応答や JSON 不正）はバックオフ付きで自動リトライする。

### モデル選択

既定は `gemini-flash-lite-latest`（`bilingual_paper.gemini.DEFAULT_MODEL`）だが、数式や参照が密集した文が
多い論文では軽量モデルだとプレースホルダ（後述）を取りこぼしやすい。品質重視なら `--model gemini-2.5-flash`
を推奨する（実績: 2307.01412 で完走、警告 0 件）。

```bash
uv run bilingual-paper arxiv 2307.01412 --model gemini-2.5-flash
```

## 内部の仕組み（トラブル時に読む）

`src/bilingual_paper/arxiv.py` は次の順で処理する。

1. **`resolve_source`**: SOURCE を解決して HTML テキストと `<base href>` を決める。
   arXiv 由来なら `<base href="https://arxiv.org/html/">` を付与する。これにより、ページ内の
   相対パス（`/static/...` の CSS、`2307.01412v4/xxx.png` のような図版）が実際の arXiv 上のパスに
   解決され、ブラウザで開いたときにスタイルと画像が正しく表示される（起点ページ自体には `<base>`
   が無いため、これをしないと保存直後の HTML はレイアウト崩れ・画像欠落を起こす）。
2. **`collect_units`**: `<article>` 内から翻訳対象を DOM 順に収集する。対象は
   タイトル (`h1`)・見出し (`h2`–`h4`)・abstract 直下の段落・各 `div.ltx_para` の直下の `p.ltx_p`
   （箇条書きや定理を包む `ltx_para` の導入文も含む。入れ子の項目はそれぞれの `ltx_para` 側で1回だけ拾う）・
   `figcaption`・脚注本文 (`span.ltx_note_content`)。参考文献一覧 (`ltx_bibliography`) は対象外
   （書誌情報は翻訳しない）。
3. **`extract_segment`**: 各ユニットのテキストを取り出す際、数式 (`<math>`)・引用 (`<cite>`)・
   参照リンク (`<a class="ltx_ref">`)・番号タグ (`class` に `ltx_tag` を含む要素、例: 節番号や
   "Theorem 1." の見出し番号)・脚注 (`ltx_note`、脚注番号 `ltx_note_mark`) は "不透明" として扱い、`@@0@@`, `@@1@@`, ... という ASCII
   プレースホルダトークンに置き換える（元の HTML 断片は保持し、`id` 属性だけ除去してコピー先での
   ID 重複を防ぐ）。それ以外のテキスト（`em` など）は素のテキストとして残す。
4. Gemini にはプレースホルダを含む平文を渡し、「トークンを一字一句変えずに、対象言語として自然な位置に
   そのまま残して訳せ」という指示を与える（`build_instructions(target_lang)` が言語名を埋め込む）。
   IDのすり合わせ (`merge_invented_ids`) と、プレースホルダの欠落チェック (`missing_tokens`) を行い、
   欠落があれば同じユニットだけを個別に再送信（最大3回、より強い指示文で）。それでも欠落する場合は、
   失われるより見た目が悪い方がマシという判断で、欠けたプレースホルダの中身をそのまま文末に追記する
   フォールバックを行う（その際 stderr に warning を出す）。見出し (`tr-heading`) と脚注本文 (`tr-note`)
   だけは、節番号・脚注番号プレースホルダの欠落を許容する（すぐ上の英語側に番号が出ているため実害がない）。
5. **`reconstruct`**: 翻訳結果の `@@N@@` を、対応する元 HTML 断片に戻して翻訳側の HTML 断片を作る。
6. **`apply_translations`**: 各ユニットの直後 (`insert_after`) に、`class="tr-para"` /
   `"tr-heading"` / `"tr-caption"` / `"tr-note"` と `lang="<target_lang>"`・`dir`（アラビア語は `rtl`）を
   付けた新しい要素として翻訳を挿入する。元の英語要素は一切変更しない。フォントは `:lang()` で言語ごとに
   切り替える（中国語が日本語の字形で表示されないようにするため）。

## 検証手順（毎回やること）

1. コマンド実行後、stderr に `warning: ... kept placeholder token(s) ... unmerged` が出ていないか確認する。
   出ていたら該当ユニットを `work/<slug>/<lang>/translations.json` で探し、目視で許容できるか確認する。
2. ローカルで簡易サーバを立てて Chrome で開き、見た目を確認する（`file://` は拡張機能から開けないため
   簡易サーバ経由にする）。

   ```bash
   cd outputs && uv run python -m http.server 8791
   # 別タブで claude-in-chrome から http://localhost:8791/<slug>-<lang>-bilingual.html を開く
   ```

   確認ポイント: タイトル/見出しの直下に訳文が出ているか、数式がレンダリングされているか、
   図版の画像が表示されているか（`<base>` が効いているか）、引用 `[n]` や "Section n" のリンクが
   クリックできる状態のままか。
3. 確認後、簡易サーバは `pkill -f "http.server 8791"` などで止める。

## 既知の制約

- 参考文献一覧（Bibliography）は翻訳しない。
- 脚注本文は訳文を英語の脚注本文の直後に差し込む。段落の訳文側に複製される脚注（プレースホルダ経由）は
  英語のまま。実際の脚注付き論文での見た目は未検証なので、初回は目視確認すること。
- `<base href>` は「`https://arxiv.org/html/`」固定でよい（画像パスがバージョン付きディレクトリを
  自己完結して含むため）。バージョン違いや将来の arXiv 側のパス変更で崩れる場合は `--base-href` で上書きする。
