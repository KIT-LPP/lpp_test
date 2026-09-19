"""ホストからコンテナへ渡す環境変数と、`docker run` の端末の指定の検査。

環境変数は必ず明示的に渡す。`os.environ` を読ませると、このテストを走らせた
端末の設定でだけ落ちるテストになる。標準入力も同じで、`sys.stdin` を読ませると
pytest の起動のしかたで結果が変わる。
"""

import io

from lpp_collector.docker import FORWARDED_ENV, forwarded_env_args, tty_args


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Broken:
    """閉じられた標準入力。`isatty()` が ValueError を上げる。"""

    def isatty(self) -> bool:
        raise ValueError("I/O operation on closed file")


def test_a_setting_made_on_the_host_reaches_the_container():
    """渡さないと、ホストで設定しても既定値のまま動く。"""
    args = forwarded_env_args({"LPP_BASE_URL": "http://127.0.0.1:13459"})
    assert args == ["--env", "LPP_BASE_URL=http://127.0.0.1:13459"]


def test_nothing_is_passed_when_nothing_is_set():
    assert forwarded_env_args({}) == []


def test_an_unset_setting_is_not_passed_as_empty():
    """空で渡すと、コンテナ側が既定値に戻れなくなる。"""
    args = forwarded_env_args({"LPP_RUN_TIMEOUT": "60"})
    assert "--env" in args
    assert not any(arg.startswith("LPP_BASE_URL") for arg in args)


def test_every_forwarded_setting_is_passed():
    env = {name: f"value-of-{name}" for name in FORWARDED_ENV}
    args = forwarded_env_args(env)
    assert len(args) == 2 * len(FORWARDED_ENV)
    for name in FORWARDED_ENV:
        assert f"{name}=value-of-{name}" in args


def test_host_paths_are_not_forwarded():
    """コンテナの中ではマウント先を指していなければならない。

    渡すと `derive_data_dir()` と `derive_target_path()` がホストのパスを
    返し、そこには何も無い。
    """
    args = forwarded_env_args(
        {
            "LPP_DATA_DIR": "/home/student/.config/lpp",
            "LPP_TARGET_PATH": "/home/student/kadai1",
        }
    )
    assert args == []


def test_host_only_settings_are_not_forwarded():
    """どのコンテナを起動するかは、起動した後の中では意味を持たない。"""
    args = forwarded_env_args(
        {"DOCKER_IMAGE": "ghcr.io/kit-lpp/lpp_test:dev", "LPP_DOCKER_BASE": "."}
    )
    assert args == []


def test_the_relays_are_not_forwarded_from_here():
    """道具の内部の伝達路は、ホスト側が値を決めて別に渡している。"""
    for relay in ("LPP_HOST_VERSION", "LPP_IMAGE_DIGEST", "LPP_HOST_ENV_LABELS"):
        assert relay not in FORWARDED_ENV


def test_the_environment_is_not_copied_wholesale():
    """環境を丸ごと渡すと、秘密情報がコンテナへ流れる。"""
    args = forwarded_env_args(
        {"LPP_BASE_URL": "http://127.0.0.1:13459", "AWS_SECRET_ACCESS_KEY": "s"}
    )
    assert not any("AWS_SECRET_ACCESS_KEY" in arg for arg in args)


def test_no_pseudo_terminal_is_asked_for_without_a_terminal():
    """`-t` を付けると docker が起動を拒み、エージェントや CI で何も走らない。"""
    assert "-t" not in tty_args(io.StringIO())


def test_standard_input_reaches_the_container_without_a_terminal():
    """外すと `echo 3 | lpprun` の入力が学生のプログラムへ届かない。"""
    assert tty_args(io.StringIO()) == ["-i"]


def test_a_terminal_still_gets_a_pseudo_terminal():
    """端末から動かす学生の側は今まで通り対話できる。"""
    assert tty_args(_Tty()) == ["-i", "-t"]


def test_an_unusable_standard_input_is_not_a_terminal():
    """判定で例外を上げると、道具そのものが起動しなくなる。"""
    assert tty_args(_Broken()) == ["-i"]
    # `isatty()` を持たない差し替え (stdin を閉じて起動された子プロセスなど)
    assert tty_args(object()) == ["-i"]
