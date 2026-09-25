# 開発

学生向けの使い方は [README](../README.md) にあります。

課題のテストケースは `lpp_collector/testcases` に置いてあり、テスト環境の中では `/lpp/test` に配置されます。

## サーバとの契約

API の契約の単一の情報源はサーバ (`lpp_collector_v2`) の `src/api.ts` で、
その写しが `openapi.json` です。契約が変わったらサーバ側で
`bun run openapi:write` して、このレポジトリの `openapi.json` を置き換えます。

クライアントは手書き (`lpp_collector/api.py`) で、送る形が契約と一致することを
`tests/test_contract.py` が `openapi.json` と突き合わせて検査します。

## 設定の環境変数

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

## テスト

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

## テストケースの書き方 (`testkit`)

課題のテスト (`lpp_collector/testcases/`) は `lpp_collector/testkit.py` を通して
プログラムを動かし、`testkit.fail()` で落とします。`command()` と
`common_task()` を課題ごとに複製するのはやめました (11 ファイルにあり、
どれも終了コードを捨てていました)。

```python
executed = testkit.run_target("cr", mpl_file)   # 終了コードごと持ち帰る
testkit.save_raw(stem, executed)                # 生の出力を残す
testkit.compare_or_fail(lines, expected, input=mpl_file, stem=stem)
```

`testkit.fail()` は `pytest.fail(..., pytrace=False)` を呼びます。テストの
仕組み側の traceback は学生の助けにならないので出しません。組み立てた日本語の
説明がそのまま画面に出て、サーバにも `longrepr` として残ります。

分類の一覧は `testkit.KIND_TITLES`、表示の順序は
`student_report.KIND_ORDER` が単一の情報源です。先に直すと他がまとめて
動くものから並べています。

判定に関わる変更をしたときは、改修の前後で同じ課題ディレクトリに対して
`pytest -q --tb=no -rA` を走らせ、`PASSED`/`FAILED` の一覧を突き合わせ、
**変わった分がすべて意図したものであること**を確かめます。

## 判定の規則

合否を左右する判断のうち、間違えやすいものをここに書きます。

**終わり方そのものが異常なものは通しません** (`testkit.reject_abnormal_exit`)。
エラーを期待する入力 (`sample0*`) の判定は「標準エラー出力に何か出たか」
しか見ていなかったので、シェルが書いた `Segmentation fault` や
`./tc: not found` がエラーの報告として数えられ、**落ちたプログラムや、
実行ファイルが 1 つも無い提出が通って**いました。異常終了・時間切れ・
実行ファイルなしは、エラーが出ていても落とします。

**エラーの行番号は、パスの中の数字から読みません**
(`testkit.error_line_number`)。従来は出力全体の最初の数字を行番号と
みなしていたので、入力のパスを表示するプログラムでは、テスト環境のパスに
入っている `python3` の `3` が拾われ、**どんな入力でも「3 行目」と報告した
ことに**なっていました (3 行目にエラーのある課題だけが通る)。語ごとに見て
パスらしい語は飛ばし、`file.mpl:12` の形で付けているものはその数字を拾います。

前後 1 行までの許容は従来どおりです。

課題4 のテストは c2c2 (`/casljs`) を呼びます。手元で確かめるときは
`LPP_CASLJS_DIR` で場所を差し替えられます (テスト環境では使いません)。

```bash
cd casljs && npm ci
cd <課題4のディレクトリ>
LPP_CASLJS_DIR=<このレポジトリ>/casljs \
  lpptest --run-pytest 04test
```

## 実行の記録

`lpprun` の記録はテストの試行と同じキューに入り、`kind` で送り先が分かれます
(`/api/attempt` と `/api/run`)。記録にはビルドのフラグと実行時の環境
(`ASAN_OPTIONS` など) が入ります。サニタイザの条件が分からない行は
後から研究に使えないためです。

## 提出の控え

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

## サーバが配るフラグ

テストの直後に提出を尋ねるかどうかは、サーバが配るフィーチャーフラグ
`auto_submit` で決まります (`lpp_collector/flags.py`)。設計はサーバ側の
`docs/feature-flags-plan.md` にあり、値は管理画面の「フラグ」で変えます。

`runner.py` が pytest を起動する前に `GET /api/flags` を呼びます。取得の全体に
3 秒の期限があり、届かなければ `off` (尋ねない) で動きます。キャッシュは
持ちません。取得はコンテナの中だけで行い、ホスト側では行いません。ホスト側の
lpp_test は更新されないので、そこに取得を入れると後から直せないためです。

| 値 | 挙動 |
| --- | --- |
| `prompt` | 全テストが通ったら `[y/N]` で提出を尋ねる |
| `off` | 尋ねず、`lppsubmit` の案内も出さない。行が無いとき、知らない値のときもこれ |

実効値は `LPP_AUTO_SUBMIT` で pytest に渡し、試行の `envLabels` に
`AUTO_SUBMIT` として載せます。届かずに既定値で動いたときは `unavailable` に
なるので、分析で「サーバが off を返した」と区別できます。`lppsubmit` による
手動の提出はこのフラグの影響を受けません。

## 収集時の文脈の申告 (`envLabels`)

記録には、そのとき何を使って書いていたかの申告が付きます。送るキーは
`lpp_collector/envlabels.py` の固定の一覧だけで、環境を丸ごと写すことはしません。
環境変数をそのまま送ると、API キーのような秘密情報が研究データに残るためです。
値もほとんどは決まった定数で、環境変数の中身をそのまま送るのは下に書く
`AI_AGENT` (道具が自分を名乗るための変数) だけです。

| キー | 値 | 何を見ているか |
| --- | --- | --- |
| `AGENT_NAME` | `claude_code` / `gemini_cli` / `codex_cli` / `cursor` など / `other` / `none` / `unknown` | 既知のエージェントの印となる環境変数があるか |
| `AGENT_DECLARED` | `claude-code_2-1-278_agent` など / `none` | `AI_AGENT` で道具が名乗った文字列そのもの |
| `MANAGED_BY_GIT` | `true` / `false` / `unknown` | ホストで `git rev-parse --is-inside-work-tree` が通るか |
| `AUTO_SUBMIT` | `prompt` / `off` / `unavailable` | 自動提出のフラグの実効値。コンテナの runner が決める (「サーバが配るフラグ」を参照) |

エージェントの判定は [`detect-agent`](https://pypi.org/project/detect-agent/)
(Vercel の [`detect-agent`](https://github.com/vercel/detect-agent) の Python 移植)
に任せます。どの環境変数をどの名前に結び付けるかは、そのパッケージが持つ
`agents.json` が決めます。`AGENT_NAME` の値はそこにある名前で、上の表は代表的な
ものです。

判定は Python の中で行い、印の**値**は送りません (印があったかどうかだけを見て、
決まった名前を送ります)。値をそのまま送るのは `AI_AGENT` だけです。これは道具が
自分の名前を名乗るための変数で、秘密を入れる場所ではなく、版まで入ることがあり
(Claude Code は `claude-code_2-1-278_agent` と名乗ります)、その版が分かること自体に
意味があるためです。

ただし名乗りは `AGENT_NAME` には入れず、`AGENT_DECLARED` に分けます。版の入った
文字列を層別に使うキーへ入れると、同じ道具でも版ごとに名前が割れるためです。
`AGENT_NAME` は**先に印を見て**決め、名乗りは印で分からなかったときだけ、既知の
名前とそのまま一致する場合に限って使い、それ以外は `other` (既知ではない何かが
居た) に畳みます。畳んだ中身は `AGENT_DECLARED` に残るので、後から数えられます。
層別は `AGENT_NAME` で、版の違いを見たいときは `AGENT_DECLARED` で、という
使い分けです。

`none` は「エージェントを使っていない」ではなく「この版が知っている印が無かった」
(`AGENT_DECLARED` では「名乗りが無かった」)、`AGENT_NAME` の `unknown` は「判定
そのものができなかった」、`MANAGED_BY_GIT` の `unknown` は「git が入っていないか
動かせず、確かめられなかった」です。

git の判定は `.git` の有無ではなく git 自身に訊きます。知りたいのは学生が git を
使っているかで、課題のディレクトリがリポジトリの下の階層にあっても正しく出ます。
`git status` ではなく `rev-parse` を使うのは、status が index を書き戻しにいく
(学生のリポジトリに触る) ためです。

判定はホスト側で行います。エージェントの印はホスト側の環境変数にしかなく、git も
コンテナに入っているとは限らないのに対し、pytest はコンテナの中で走るためです。
結果は `LPP_HOST_ENV_LABELS` でコンテナへ渡します (`LPP_IMAGE_DIGEST` と同じ、
道具の内部の伝達路です)。

印が足りないときは `detect-agent` を上げます (印の一覧を自分で持たないのは、
推測で足すと使っていない学生が使っていることにされるためで、上流の
`agents.json` は実際に確かめられた印だけを載せています)。新しい名前が
増えてもこちらのコードは触りません。名前の一覧は `detect_agent.KNOWN_AGENTS`
から借りているので、そこに無い名前は `other` になります。

## 送信のキュー

試行は送る前に `~/.config/lpp/upload_queue/<冪等キー>/` へ書きます。
送れなかったものはそこに残り、次回の実行で送り直します。契約に合わずに
拒まれたものは `upload_failed/` へ移り、再送の対象から外れます。
