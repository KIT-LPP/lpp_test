# lpp/test

KIT言語処理プログラミング課題におけるテスト環境
[`pipx`](https://pipx.pypa.io/stable/installation/)によりインストールされた`lpptest`コマンドを用いてテストを行う．
`lpptest`コマンドは自動的にDockerを用いたテスト用環境を用意し，テストを行うことができる．

## インストール方法

[`pipx`](https://pipx.pypa.io/stable/installation/)に従う．
その後、以下のコマンドを実行する．

```bash
pipx install git+https://github.com/f0reachARR/lpp_test --force
```

これにより，`lpptest`コマンドがインストールされる．
インストール後は，ソースコードのあるディレクトリで`lpptest`コマンドを実行することでテストを行うことができる．

## セットアップと同意

初めて使う端末では、まず端末とあなたの学籍番号を結びつけます。

```bash
lppsetup      # 既に lpptest を入れてある端末では lppconsent でも同じ流れになります
```

Redmine のあなたのプロジェクトの Wiki にある「セットアップ」のページから
トークンを貼り付けてください。トークンをコマンドの引数に書かないでください。
入力の履歴が端末に残ります。

続けて研究利用への同意を尋ねます。同意しない場合も課題の実施には影響せず、
成績評価にも影響しません。同意の変更・取り消しと、端末の結びつきの解除は
いつでも `lppconsent` から行えます。

**テストの結果とソースコードの収集は、同意の有無に依らず行われます。**
提出とフィードバックのために必要な処理だからです。研究に使うかどうかだけが
同意で決まります。

## 自分の入力で動かす (`lpprun`)

課題のディレクトリで、そこにある `*.c` をまとめてビルドして実行します。

```bash
lpprun sample01.mpl        # ビルドして ./a.out sample01.mpl を実行
lppc                       # ビルドだけ
lpprun --no-sanitize ...   # サニタイザなしでビルド
```

**サニタイザ (ASan / UBSan) を既定で有効にします。** メモリの範囲外アクセスや
未定義動作があると、その場で報告が出ます。`lpptest` の合否判定には計測を
入れていないので、`lpptest` の結果は変わりません。

Makefile は見ません。サニタイザはリンクにもフラグが要るためです。
実行ファイルは `~/.config/lpp/build/` に置くので、`make` の成果物を
上書きしません。実行時のカレントディレクトリは課題のディレクトリなので、
引数の相対パスはそのまま使えます。

この実行も収集の対象です (テストの試行とは別に記録されます)。

## テストの実行

### 課題1の場合

```bash
# テストの実行
lpptest 01test
```

* 00_tc_compile_test.py - コンパイルできるか，引数の有無での動作，無効なファイル名を与えた動作
* 01_tc_run_test.py - 空白を取り除いた出力を辞書順にソートしたものを比較する．

### 課題1拡張の場合

```bash
# テストの実行
lpptest 01test_ex
```

* 00_tc_compile_test.py - コンパイルできるか，引数の有無での動作，無効なファイル名を与えた動作
* 01_tc_run_test.py - 空白を取り除いた出力を辞書順にソートしたものを比較する．

### 課題2の場合

```bash
# テストの実行
lpptest 02test
```

* 00_pp_compile_test.py - コンパイルできるか，引数の有無での動作，無効なファイル名を与えた動作
* 01_pp_run_test.py - 与えられた仕様通りに出力できているかを見る．
* 02_pp_mm_test.py - 一度出力した内容を再度ppに通して，エラーが出ないかを見る．

### 課題3の場合

```bash
# テストの実行
lpptest 03test
```

* 00_cr_compile_test.py - コンパイルできるか，引数の有無での動作，無効なファイル名を与えた動作
* 01_cr_run_test.py - 仕様の順に出力した表から空白文字をすべて削除したものを比較する

### 課題4の場合

```bash
# テストの実行
lpptest 04test
```

* 00_mpplc_compile_test.py - コンパイルできるか，引数の有無での動作，無効なファイル名を与えた動作
* 01_mpplc_c2c2_run_test.py - コンパイルしたアセンブリプログラムがc2c2で実行できるかを見る．

## Docker内部のディレクトリ配置

各テストはDocker内部に置かれるため、普段意識する必要はない．
なお、このレポジトリにおいては`lpp_collector/testcases`に配置されている．

* /lpp/test   : テストケースが置かれているフォルダ
* /lpp/test/input0[123] : サンプルmplファイルが置いてある場所
  * `sample0*.mpl` は，実行時にエラーが出力されることが期待されている
* /lpp/test/0[1234]test : 各課題に対するテスト
* /lpp/test/0[1234]test/test_expects : 各課題に対するテストの期待される出力(オラクル)
  * 以下のテストでは，エラーが出力される想定のものは，エラーの出た行番号が同じであればPASSとなる．
* /lpp/test/coverage : C0カバレッジを上げていくためのテストケース(上記テストでは使わない)

## 開発

### サーバとの契約

API の契約の単一の情報源はサーバ (`lpp_collector_v2`) の `src/api.ts` で、
その写しが `openapi.json` です。契約が変わったらサーバ側で
`bun run openapi:write` して、このレポジトリの `openapi.json` を置き換えます。

クライアントは手書き (`lpp_collector/api.py`) で、送る形が契約と一致することを
`tests/test_contract.py` が `openapi.json` と突き合わせて検査します。

### テスト

```bash
uv sync
uv run pytest tests -c tests/pytest.ini
```

`tests/pytest.ini` で収集プラグインを外しています。付けたままだと、
テストを走らせるたびに `tests/` の実行が試行としてキューに積まれます。

実物のサーバに対する通し確認 (セットアップ → 未束縛のアップロード → 同意 →
束縛済みのアップロード → 提出) は、接続先とトークンを渡したときだけ走ります。

```bash
# サーバ側
cd ../lpp_collector_v2
DB_NAME=lpp_dev PORT=13459 bun run src/index.ts
bun run scripts/issue-tokens.ts roster.csv --commit --out tokens.tsv

# クライアント側
LPP_TEST_SERVER=http://127.0.0.1:13459 LPP_TEST_TOKEN=... \
  uv run pytest tests -c tests/pytest.ini
```

### 実行の記録

`lpprun` の記録はテストの試行と同じキューに入り、`kind` で送り先が分かれます
(`/api/attempt` と `/api/run`)。記録にはビルドのフラグと実行時の環境
(`ASAN_OPTIONS` など) が入ります。サニタイザの条件が分からない行は
後から研究に使えないためです。

### 送信のキュー

試行は送る前に `~/.config/lpp/upload_queue/<冪等キー>/` へ書きます。
送れなかったものはそこに残り、次回の実行で送り直します。契約に合わずに
拒まれたものは `upload_failed/` へ移り、再送の対象から外れます。
