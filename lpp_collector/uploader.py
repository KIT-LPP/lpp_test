"""試行をサーバへ送る。

**先に書いてから送る。** 送信は失敗するものとして、キューへ書き終えてから
送りにいく。本番では 47 端末のうち 42（11,785 行）が再送キューに滞留し、
`created_at` が受信時刻ではなく flush 時刻になって時間軸として使えなくなっていた。
今回は試行の時刻 (`deviceTime`) を記録に持たせ、送信の時刻 (`deviceSentAt`) を
送るたびに付け直すので、滞留しても時間軸は壊れない。
"""

import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .api import ApiError, LppApi
from .config import LPP_DATA_DIR
from .device import LppDevice

QUEUE_DIR = Path(LPP_DATA_DIR) / "upload_queue"
FAILED_DIR = Path(LPP_DATA_DIR) / "upload_failed"

RECORD_NAME = "attempt.json"
SOURCE_NAME = "source.tar"
SENDING_SUFFIX = ".sending"

# 学生を待たせないための上限。残りは次回の背景送信が引き取る
FOREGROUND_DEADLINE = 30.0


class Uploader:
    def __init__(
        self,
        device: LppDevice,
        queue_dir: Path = QUEUE_DIR,
        failed_dir: Path = FAILED_DIR,
        api_factory=None,
    ):
        self.device = device
        self.queue_dir = Path(queue_dir)
        self.failed_dir = Path(failed_dir)
        self._api_factory = api_factory or (lambda token: LppApi(token=token))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.errors: List[str] = []

    # -----------------------------------------------------------------
    def enqueue(self, record: Dict[str, Any], source_tar: bytes) -> Path:
        """試行をキューに書く。ここで冪等キーが確定する。

        冪等キーは記録と一緒に永続化する。送信の時点で作ると再送のたびに
        別の値になり、サーバ側で重複を畳めない。内容から導出すると、
        同一ソースの再実行（step0 で実際の行動と確認した 25.4%）が潰れる。
        """
        key = record["idempotencyKey"]
        target = self.queue_dir / key
        staging = self.queue_dir / (key + ".partial")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        (staging / SOURCE_NAME).write_bytes(source_tar)
        (staging / RECORD_NAME).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(staging, target)
        return target

    # -----------------------------------------------------------------
    def pending(self, newest_first: bool = False) -> List[Path]:
        if not self.queue_dir.exists():
            return []
        entries = [
            p
            for p in self.queue_dir.iterdir()
            if p.is_dir() and not p.name.endswith((SENDING_SUFFIX, ".partial"))
        ]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=newest_first)
        return entries

    def flush(self, newest_first: bool = False, deadline: Optional[float] = None) -> int:
        """キューを送る。送れた件数を返す。

        1 件の失敗で残りを止めない。**現在の試行が過去の失敗に巻き込まれない**
        ようにするのがこの関数の要件である。従来は背景の再送が失敗していると
        そのときのアップロードもキューへ回しており、一度の失敗が以降すべてを
        滞留させていた。
        """
        self._move_legacy_queue_files()
        sent = 0
        for entry in self.pending(newest_first=newest_first):
            if self._stop.is_set():
                break
            if deadline is not None and time.monotonic() > deadline:
                break
            if self._send_one(entry):
                sent += 1
        return sent

    def _send_one(self, entry: Path) -> bool:
        claimed = entry.with_name(entry.name + SENDING_SUFFIX)
        try:
            # 送っている間は名前を変えて持ち主を示す。別のプロセスの pytest が
            # 同時に走っても、同じ試行を二重に送らない
            os.replace(entry, claimed)
        except OSError:
            return False

        try:
            record = json.loads((claimed / RECORD_NAME).read_text(encoding="utf-8"))
            source_tar = (claimed / SOURCE_NAME).read_bytes()
        except (OSError, ValueError) as e:
            self._fail(claimed, f"キューの記録を読めませんでした: {e}")
            return False

        record["deviceSentAt"] = datetime.now().astimezone().isoformat()

        api = self._api_factory(self.device.device_token)
        try:
            api.post_attempt(record, source_tar)
        except ApiError as e:
            if e.status_code == 401:
                # 束縛が解除されたか、トークンが無効になっている。持ち続けても
                # 送り直すたびに同じ 401 になるので手放す。未束縛の試行としては
                # 受け入れられるので、記録はキューに残す
                self.device.forget_binding()
                self._note(
                    "端末トークンが無効になっていました。"
                    "`lppsetup` でセットアップし直してください。"
                )
                os.replace(claimed, entry)
                return False
            if e.is_retryable:
                self._note(f"アップロードを保留しました: {e}")
                os.replace(claimed, entry)
                return False
            self._fail(claimed, f"アップロードが拒否されました: {e}")
            return False
        except Exception as e:  # noqa: BLE001 - 想定外も握り潰さず残す
            self._note(f"アップロードに失敗しました: {e}")
            os.replace(claimed, entry)
            return False
        finally:
            api.close()

        shutil.rmtree(claimed, ignore_errors=True)
        return True

    # -----------------------------------------------------------------
    def _note(self, message: str):
        if message not in self.errors:
            self.errors.append(message)

    def _fail(self, entry: Path, message: str):
        """送り直しても通らないものを分けて残す。

        消さないのは、契約の取り違えでサーバが受け取らなかった場合に、
        何が送られようとしていたかを後から見られるようにするためである。
        """
        self._note(message)
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        target = self.failed_dir / entry.name.replace(SENDING_SUFFIX, "")
        shutil.rmtree(target, ignore_errors=True)
        try:
            os.replace(entry, target)
        except OSError:
            shutil.rmtree(entry, ignore_errors=True)

    def _move_legacy_queue_files(self):
        """旧版の pickle を片付ける。

        本番の端末には旧 API 宛の `*.dat` が溜まっている。中身は消えた経路の
        要求で、今の版では復元もできない。読もうとして再送が毎回落ちると
        新しい試行まで送れなくなるので、失敗側へ移して先へ進む。
        """
        if not self.queue_dir.exists():
            return
        legacy = sorted(self.queue_dir.glob("*.dat"))
        if not legacy:
            return
        target = self.failed_dir / "legacy"
        target.mkdir(parents=True, exist_ok=True)
        for path in legacy:
            try:
                os.replace(path, target / path.name)
            except OSError:
                pass
        self._note(
            f"旧版の送信待ち {len(legacy)} 件は今の API では送れないため "
            f"{target} へ移しました。"
        )

    # -----------------------------------------------------------------
    def start_background_retry(self):
        """溜まっている分をテストの裏で送る。

        現在の試行はここには依存しない。背景が失敗しても、`flush()` は
        そのときの試行を新しい順に送りにいく。
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._background, daemon=True)
        self._thread.start()

    def _background(self):
        try:
            self.flush()
        except Exception as e:  # noqa: BLE001
            self._note(f"背景の再送に失敗しました: {e}")

    def stop_background_retry(self, timeout: float = 1.0):
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._stop.clear()
        self._thread = None
