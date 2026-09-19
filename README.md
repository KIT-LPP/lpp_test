# lpp/test

KIT言語処理プログラミング課題におけるテスト環境
[`pipx`](https://pipx.pypa.io/stable/installation/)によりインストールされた`lpptest`コマンドを用いてテストを行う．
`lpptest`コマンドは自動的にDockerを用いたテスト用環境を用意し，テストを行うことができる．

## インストール方法

[`pipx`](https://pipx.pypa.io/stable/installation/)に従う．
その後、以下のコマンドを実行する．

```bash
pipx install git+https://github.com/KIT-LPP/lpp_test --force
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
実行ファイルはデータディレクトリ (`LPP_DATA_DIR`。テスト環境の中では
`/lpp/data/build/`、ホストから見ると `~/.config/lpp/build/`) に実行ごとの
一時ディレクトリを作って置くので、`make` の成果物を上書きせず、
2 つの端末から同時に走らせても互いを消しません。実行時のカレントディレクトリは課題のディレクトリなので、
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

## 提出 (`lppsubmit`)

テストの結果とソースコードは毎回サーバへ送られますが、それだけでは提出には
なりません。どの実行を提出とするかはあなたが決めます。

`lpptest` でそのスイートのテストがすべて通ると、最後に確認が出ます。

```
[lpp] すべてのテストが通りました。01test を提出しますか? [y/N]:
```

`y` (または `yes`、`はい`) と答えたときだけ提出します。それ以外 (Enter だけ、
`n`、Ctrl-D) では提出しません。あとから出すこともできます。

この確認は、そのスイートを丸ごと走らせたときだけ出ます。テストケースを 1 つ
だけ指定したときや `-k`、`--lf` で絞ったときは出ません。走らせた分が通った
だけで「すべて通った」と言うことになるためです。

```bash
lppsubmit           # 最後に実行した課題を提出する
lppsubmit 01test    # 課題を指定して提出する
lppsubmit -y        # 確認を省く
```

`lppsubmit` は通らなかった実行でも提出できます。何を提出しようとしているか
(課題、実行した時刻、通った件数) を表示してから尋ねます。提出は履歴として
積まれるので、何度提出しても前の提出は消えません。

提出には端末の登録が要ります (`lppsetup`)。研究への同意は要りません。
テストのときにサーバへ届かなかった実行は、`lppsubmit` が送り直してから
提出します。回線が切れていた日のものも、後から出せます。

ホスト側で `lppsubmit` が見つからないときは、テスト環境を入れ直してください。

```bash
pipx install git+https://github.com/KIT-LPP/lpp_test --force
```

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

### 設定の環境変数

接続先 (`LPP_BASE_URL`) と実行の制限時間 (`LPP_RUN_TIMEOUT`) は、ホストで
設定するとコンテナへもそのまま渡ります。`lpptest` も `lpprun` もホスト側では
docker を起動するだけで、API を叩くのも課題を走らせるのもコンテナの中なので、
渡さないと設定しても既定値のまま動きます。

```bash
LPP_BASE_URL=http://lpp.example.test/lpp_api/ lpptest
```

値はコンテナの中から見た宛先です。コンテナの中の `127.0.0.1` はコンテナ自身
なので、手元で動かしているサーバの宛先をそのまま書いても届きません。Docker
Desktop では `host.docker.internal` で母艦に届きますが、Linux の docker では
`--add-host=host.docker.internal:host-gateway` が要り、今の wrapper はこれを
渡していません。手元のサーバに当てる確認は、コンテナを挟まない
`lpptest --run-pytest` か、このレポジトリの通し確認 (下記) で行ってください。

渡すものは `lpp_collector/docker.py` の `FORWARDED_ENV` が単一の情報源です。
`LPP_*` をまとめて渡すことはしません。`LPP_DATA_DIR` と `LPP_TARGET_PATH` は
ホスト側のパスで、コンテナの中ではマウント先 (`/lpp/data`, `/workspaces`) を
指していなければならないためです。どのコンテナを起動するかの設定
(`DOCKER_IMAGE`, `LPP_DOCKER_BASE`) も、起動した後の中では意味を持ちません。

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

### 提出の控え

`lpptest` を走らせると、プラグインが直前の試行を
`LPP_DATA_DIR/attempts/<課題>.json` に控えます (`lpp_collector/submit.py`)。
提出はこの控えを見て行います。控えには試行の id (サーバの応答)、冪等キー、
合否、実行の印が入ります。課題ごとに持つのは、`lpptest 01test` の後に
`lpptest 02test` を走らせてから `lppsubmit` を打ったときに、どちらを出すのか
決められるようにするためです。

実行の印 (`LPP_SESSION_ID`) は `runner.py` が pytest に渡します。テストの
直後の確認は、控えがその実行のものであるときだけ出します。pytest が途中で
殺されると控えは前回のまま残るので、印を見ないと前回の試行を今回のものとして
提出しかねません。

`POST /api/submission` の `auto` はサーバ側の印で、採点の一覧と Redmine の
コメントに「自動提出」と出るかどうかだけを決めます。テストを通した流れから
出したものを `true`、`lppsubmit` で明示的に出したものを `false` にしています。

### 収集時の文脈の申告 (`envLabels`)

記録には、そのとき何を使って書いていたかの申告が付きます。送るキーは
`lpp_collector/envlabels.py` の固定の一覧だけで、環境を丸ごと写すことはしません。
環境変数をそのまま送ると、API キーのような秘密情報が研究データに残るためです。

| キー | 値 | 何を見ているか |
| --- | --- | --- |
| `AGENT_NAME` | `claude_code` / `gemini_cli` / `codex` / `none` | 既知のエージェントの印となる環境変数があるか |
| `MANAGED_BY_GIT` | `true` / `false` / `unknown` | ホストで `git rev-parse --is-inside-work-tree` が通るか |

判定は Python の中で行い、印の**値**は送りません (印があったかどうかだけを見て、
こちらで決めた定数を送ります)。`none` は「エージェントを使っていない」ではなく
「この版が知っている印が無かった」、`unknown` は「git が入っていないか動かせず、
確かめられなかった」です。

git の判定は `.git` の有無ではなく git 自身に訊きます。知りたいのは学生が git を
使っているかで、課題のディレクトリがリポジトリの下の階層にあっても正しく出ます。
`git status` ではなく `rev-parse` を使うのは、status が index を書き戻しにいく
(学生のリポジトリに触る) ためです。

判定はホスト側で行います。エージェントの印はホスト側の環境変数にしかなく、git も
コンテナに入っているとは限らないのに対し、pytest はコンテナの中で走るためです。
結果は `LPP_HOST_ENV_LABELS` でコンテナへ渡します (`LPP_IMAGE_DIGEST` と同じ、
道具の内部の伝達路です)。

印を足すときは `AGENT_MARKERS` に 1 行足します。載せるのは、その道具の文書か
実際の環境で確かめた印だけにします。推測で足すと、使っていない学生が
使っていることにされます。

### 送信のキュー

試行は送る前に `~/.config/lpp/upload_queue/<冪等キー>/` へ書きます。
送れなかったものはそこに残り、次回の実行で送り直します。契約に合わずに
拒まれたものは `upload_failed/` へ移り、再送の対象から外れます。
