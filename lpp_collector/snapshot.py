"""提出物のスナップショットを tar に固める。"""

import os
import tarfile
from glob import glob
from io import BytesIO
from typing import List, Tuple

from .config import LPP_SOURCE_FILES


def collect_sources(source_dir: str) -> List[Tuple[str, str]]:
    """収集対象のファイルを (実際のパス, tar の中の名前) で返す。

    tar の中の名前は `source_dir` からの相対にする。絶対パスのまま入れると
    tarfile が先頭の "/" を落とし、端末ごとに違う名前で同じファイルが入る。
    サーバはパスを鍵にしてスナップショットを畳むので、ここが揺れると
    同じソースが別物になる。
    """
    found = sorted(
        set(
            sum(
                [glob(f"{source_dir}/**/{pat}", recursive=True) for pat in LPP_SOURCE_FILES],
                [],
            )
        )
    )
    files = []
    for path in found:
        if not os.path.isfile(path):
            continue
        arcname = os.path.relpath(path, source_dir)
        if arcname.startswith(".."):
            # source_dir の外は入れない
            continue
        files.append((path, arcname))
    return files


def build_tar(files: List[Tuple[str, str]]) -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for path, arcname in files:
            tf.add(path, arcname=arcname, recursive=False)
    return buf.getvalue()


def snapshot(source_dir: str) -> bytes:
    return build_tar(collect_sources(source_dir))
