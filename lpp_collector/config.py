# Configuration

import os
import lpp_collector

LPP_BASE_URL = (
    os.environ["LPP_BASE_URL"]
    if "LPP_BASE_URL" in os.environ
    else "https://se.is.kit.ac.jp/lpp_api/"
)

LPP_SETUP_TEXT = """
この端末をあなたの学籍番号に結びつけます。

Redmine のあなたのプロジェクトの Wiki に「セットアップ」のページがあります。
そこに書かれているトークンを次の画面に貼り付けてください。

トークンはコマンドの引数には書かないでください。
入力の履歴が端末に残り、他の人に読まれることがあります。
"""

LPP_CONSENT_TEXT = """
(この画面は上下キーでスクロールできます)
言語処理プログラミングにおいて、ソフトウェア工学研究室が行う研究のための同意です。
以下をよく読み、同意する場合は「同意する」を選んでください。

【収集について】
課題のテスト (lpptest) を実行すると、提出とフィードバックのために次の情報が
毎回サーバへ送られます。これは課題の実施に必要な処理であり、研究への同意とは
別に行われます。

- テストの実行結果
- 実行したディレクトリにある、課題の C ファイルらしきもの (*.c, *.h, Makefile)
- 実行した環境の情報 (テスト環境の版など)
- テストを行った日時

【この画面で尋ねていること】
上のように集めた情報を、**研究に使ってよいか**です。
同意しない場合も課題の実施には影響せず、成績評価にも一切影響しません。

【取り消し】
いつでも同じコマンドで取り消せます。取り消すと、それまでに集まった分も
研究の対象から外れます。
"""

LPP_PUBLICATION_TEXT = """
研究の成果を発表する際に、あなたのソースコードの一部を、
学籍番号や氏名を伏せた形で例として引用してよいですか。

「いいえ」を選んでも、研究に使うこと自体には影響しません。
"""

LPP_INCLUDE_PRIOR_TEXT = """
この端末には、セットアップより前に実行したテストの記録が
{count} 件あります ({period})。

これらを研究の対象に含めてよいですか。

「いいえ」を選ぶと、今回より後の分だけが対象になります。
"""

LPP_INCLUDE_PRIOR_AGAIN_TEXT = """
この決定より前の記録も研究の対象に含めてよいですか。

「いいえ」を選ぶと、今回より後の分だけが対象になります。
これまで対象に含まれていた分も外れます。
"""

LPP_AFTER_CONSENT_TEXT = """
ご協力ありがとうございます。同意を記録しました。

研究利用: {research}
発表での引用: {publication}
今回より前の記録: {prior}

取り消したいときは、もう一度 lppconsent を実行してください。
"""

LPP_REVOKE_CONSENT_TEXT = """
研究への同意を取り消しますか。

取り消すと、それまでに集まった分も研究の対象から外れます。

なお、課題のテストの結果とソースコードは、提出とフィードバックのために
引き続き収集されます。これは課題の実施に必要な処理です。
"""

LPP_UNBIND_TEXT = """
この端末とあなたの学籍番号の結びつきを解除しますか。

解除すると、この端末からの記録はあなたのものとして扱われなくなります。
再び結びつけるには、もう一度セットアップが必要です。
"""

LPP_SOURCE_FILES = ["*.c", "*.h", "CMakelists.txt", "Makefile", "makefile"]

# ホスト側の wrapper (pipx で入れた lpptest) が渡す版。
# コンテナは日次で更新されるがホスト側はそのままなので、ずれたら知らせる
LPP_HOST_VERSION = os.environ.get("LPP_HOST_VERSION")

TEST_BASE_DIR = os.path.join(os.path.dirname(lpp_collector.__file__), "testcases")

# Docker environment
IS_DOCKER_ENV = os.path.exists("/.dockerenv")
DOCKER_IMAGE = (
    os.environ["DOCKER_IMAGE"]
    if "DOCKER_IMAGE" in os.environ
    else "ghcr.io/f0reacharr/lpp_test:latest"
)


def derive_data_dir():
    if "LPP_DATA_DIR" in os.environ:
        return os.environ["LPP_DATA_DIR"]

    if IS_DOCKER_ENV:
        return "/lpp/data"
    else:
        return os.path.expanduser("~/.config/lpp")


LPP_DATA_DIR = derive_data_dir()

LPP_UPDATE_MARKER = os.path.join(LPP_DATA_DIR, ".update_marker")
LPP_UPDATE_INTERVAL = 60 * 60 * 24  # 1 day


def derive_target_path():
    if "LPP_TARGET_PATH" in os.environ:
        return os.environ["LPP_TARGET_PATH"]

    if IS_DOCKER_ENV:
        return "/workspaces"
    else:
        return os.getcwd()


TARGETPATH = derive_target_path()
