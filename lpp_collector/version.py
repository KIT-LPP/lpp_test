"""ホストとコンテナの版のずれを知らせる。

コンテナは日次で pull されて更新されるが、ホスト側の wrapper は pipx で
入れた版のままである。新しい入口 (`lppsetup`) を足しても、既にホストに
入っている端末には存在しない。ずれていることを黙って進めない。
"""

from .config import LPP_HOST_VERSION


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("lpp-collector")
    except Exception:  # noqa: BLE001
        return "unknown"


def warn_on_version_skew() -> bool:
    """ずれていれば True を返し、一行で知らせる。"""
    container = package_version()
    if LPP_HOST_VERSION is None:
        print(
            "[lpp] ホスト側の lpptest が古い版です "
            "(`pipx install git+https://github.com/KIT-LPP/lpp_test --force` で入れ直せます)"
        )
        return True
    if LPP_HOST_VERSION != container:
        print(
            f"[lpp] ホスト側 {LPP_HOST_VERSION} とテスト環境 {container} の版が違います "
            "(`pipx install git+https://github.com/KIT-LPP/lpp_test --force` で入れ直せます)"
        )
        return True
    return False
