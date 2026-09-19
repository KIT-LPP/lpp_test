"""ホストからコンテナへ渡す環境変数の検査。

環境変数は必ず明示的に渡す。`os.environ` を読ませると、このテストを走らせた
端末の設定でだけ落ちるテストになる。
"""

from lpp_collector.docker import FORWARDED_ENV, forwarded_env_args


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
