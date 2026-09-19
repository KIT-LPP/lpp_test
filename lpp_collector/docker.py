import json
import os
from pathlib import Path
import subprocess
import time
from typing import List, Mapping, Optional
from lpp_collector.config import (
    DOCKER_IMAGE,
    LPP_DATA_DIR,
    LPP_UPDATE_INTERVAL,
    LPP_UPDATE_MARKER,
    TARGETPATH,
)
from .envlabels import RELAY_ENV, relay_value
from .version import package_version
import sys

# ホストで設定された値のうち、そのままコンテナへ渡すもの。
#
# 学生が触るコマンドはどれもホスト側で docker を起動するだけで、API を叩くのも
# 課題を走らせるのもコンテナの中である。渡さないと、ホストで設定しても何も
# 変わらない (既定値のまま動く) ので、設定した側から見ると黙って無視される。
#
# `LPP_*` をまとめて渡すことはしない。`LPP_DATA_DIR` と `LPP_TARGET_PATH` は
# ホスト側のパスで、コンテナの中ではマウント先 (`/lpp/data`, `/workspaces`) を
# 指していなければならない。渡すと `derive_data_dir()` と
# `derive_target_path()` がホストのパスを返し、そこには何も無い。
#
# 同じ理由で次のものも渡さない:
# - `DOCKER_IMAGE`, `LPP_DOCKER_BASE`: どのコンテナを起動するかの設定で、
#   起動した後のコンテナの中では意味を持たない
# - `LPP_HOST_VERSION`, `LPP_IMAGE_DIGEST`, `LPP_HOST_ENV_LABELS`: 道具の内部の
#   伝達路で、下でホスト側が値を決めて渡している
# - `LPP_TESTSUITE`: 課題名は `run_pytest` がコンテナの中で決める
FORWARDED_ENV = ("LPP_BASE_URL", "LPP_RUN_TIMEOUT")


def forwarded_env_args(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """ホストで設定されている分だけを `--env` の並びにする。

    設定されていないものは渡さない。空の値で渡すと、コンテナ側の
    「設定されていなければ既定値」(`config.py` の `LPP_BASE_URL` など) が
    「空文字列が設定されている」に変わり、既定値に戻らなくなる。
    """
    env = os.environ if environ is None else environ
    args: List[str] = []
    for name in FORWARDED_ENV:
        if name in env:
            args += ["--env", f"{name}={env[name]}"]
    return args


def image_digest() -> str:
    """走らせるイメージの digest。

    `testCases` を送っていないので、スイートの版はこれで同定する。
    """
    try:
        digests = (
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{json .RepoDigests}}", DOCKER_IMAGE],
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
        parsed = json.loads(digests)
        if parsed:
            return str(parsed[0])
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass
    return ""


def run_test_container(args: List[str]):
    data_dir = str(Path(LPP_DATA_DIR).absolute())
    os.makedirs(data_dir, exist_ok=True)
    target_path = str(Path(TARGETPATH).absolute())

    # print(f"Data directory: {data_dir}")
    # print(f"Target path: {target_path}")

    fix_perm_args = []

    if not sys.platform.startswith("win"):
        fix_perm_args = [
            "--env",
            f"TARGET_UID={os.getuid()}",
            "--env",
            f"TARGET_GID={os.getgid()}",
        ]

    # コンテナ側が版のずれと走っているイメージを知るための情報。
    # ホスト側の wrapper は pipx で入れた版のままなのでずれうる
    env_args = [
        "--env",
        f"LPP_HOST_VERSION={package_version()}",
        "--env",
        f"LPP_IMAGE_DIGEST={image_digest()}",
        # 収集時の文脈の申告。エージェントの印はホスト側の環境変数にしか
        # 無く、コンテナの中で探しても何も見えないので、ここで判定して渡す
        "--env",
        f"{RELAY_ENV}={relay_value(target_path)}",
        # ホストで設定された値。渡さないと設定が効かない
        *forwarded_env_args(),
    ]

    run_args = [
        "run",
        "-it",
        "--rm",
        "-v",
        f"{target_path}:/workspaces",
        "-v",
        f"{data_dir}:/lpp/data",
        # おまけ
        "-v",
        f"{data_dir}/bash_history:/root/.bash_history",
        "-w",
        "/workspaces",
        *fix_perm_args,
        *env_args,
        DOCKER_IMAGE,
        *args,
    ]

    # Run Docker container
    subprocess.call(["docker", *run_args])


def run_debug_build(base_dir: str):
    build_args = [
        "buildx",
        "build",
        "-t",
        DOCKER_IMAGE,
        base_dir,
    ]

    subprocess.call(["docker", *build_args])


def fix_permission():
    if "TARGET_UID" not in os.environ or "TARGET_GID" not in os.environ:
        return
    target_uid = os.environ.get("TARGET_UID")
    target_gid = os.environ.get("TARGET_GID")
    subprocess.call(["chown", "-R", f"{target_uid}:{target_gid}", TARGETPATH])
    subprocess.call(["chown", "-R", f"{target_uid}:{target_gid}", LPP_DATA_DIR])


def write_update_marker():
    with open(LPP_UPDATE_MARKER, "w") as f:
        f.write(str(time.time()))


def check_update():
    if not os.path.exists(LPP_UPDATE_MARKER):
        write_update_marker()
        return True

    last_update = os.path.getmtime(LPP_UPDATE_MARKER)
    should_update = (time.time() - last_update) > LPP_UPDATE_INTERVAL

    if should_update:
        write_update_marker()

    return should_update


def update(force: bool = False):
    if not check_update() and not force:
        return

    print("Updating LPP test environment...")
    try:
        previous_image_id = (
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.Id}}", DOCKER_IMAGE]
            )
            .decode("utf-8")
            .strip("\n ")
        )
    except subprocess.CalledProcessError:
        previous_image_id = ""

    subprocess.call(["docker", "pull", DOCKER_IMAGE])
    current_image_id = (
        subprocess.check_output(
            ["docker", "inspect", "--format", "{{.Id}}", DOCKER_IMAGE]
        )
        .decode("utf-8")
        .strip("\n ")
    )

    if previous_image_id != current_image_id and previous_image_id != "":
        print("Removing old image...")
        subprocess.call(["docker", "rmi", previous_image_id])

    print("Update complete.")
