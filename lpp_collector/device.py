"""端末の識別子と端末トークンの保存。

`~/.config/lpp/device.json`（コンテナ内では `/lpp/data/device.json`）に置く。
演習室の端末は学生ごとのアカウントなので、識別の単位は端末と OS ユーザの組になる。

端末トークンは平文の資格情報なので 0600 で書く。
"""

import json
import os
from typing import Any, Dict, Optional

from .config import LPP_DATA_DIR

LPP_DEVICE_FILE = os.path.join(LPP_DATA_DIR, "device.json")


class LppDevice:
    """端末の状態。

    `device_id` は常にある。`device_token` はセットアップを終えた端末だけが持つ。
    トークンがなくても収集は続くので、ここが空でも異常ではない。
    """

    def __init__(self, path: str = LPP_DEVICE_FILE):
        self.path = path
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._data = json.load(f)
            # 旧版のファイルは {"device_id": ...} だけを持つ。識別子はそのまま
            # 引き継ぐ。作り直すと、サーバがこの端末の名義で預かっている
            # 未束縛の試行が迷子になり、セットアップで紐づかなくなる。
            if self._data.get("device_id"):
                return

        import uuid

        self._data.setdefault("device_id", str(uuid.uuid4()))
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        # 端末トークンは平文の資格情報なので、作る時点から 0600 にする。
        # open() のあとに chmod すると、その隙間だけ他人に読める
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f, indent=1)
        os.replace(tmp, self.path)

    @property
    def device_id(self) -> str:
        return self._data["device_id"]

    @property
    def device_token(self) -> Optional[str]:
        return self._data.get("device_token")

    @property
    def student_id(self) -> Optional[str]:
        return self._data.get("student_id")

    @property
    def binding_id(self) -> Optional[str]:
        return self._data.get("binding_id")

    def is_bound(self) -> bool:
        return bool(self.device_token)

    @property
    def setup_declined(self) -> bool:
        """`lpptest` の初回の案内を「今後は尋ねない」で閉じたか。

        「今回はしない」はここに残さない。答えを保留しただけの学生に、
        二度と案内が出ないのでは提出の入口が消える。
        """
        return bool(self._data.get("setup_declined_at"))

    def decline_setup(self):
        from datetime import datetime

        self._data["setup_declined_at"] = datetime.now().astimezone().isoformat()
        self._save()

    def bind(self, student_id: str, device_token: str, binding_id: str):
        self._data["student_id"] = student_id
        self._data["device_token"] = device_token
        self._data["binding_id"] = binding_id
        self._save()

    def forget_binding(self):
        """束縛の情報だけ落とす。

        端末の識別子は残す。束縛を解除しても端末は同じ端末で、以後の試行は
        未束縛として同じ名義に積まれる。ここで device.json ごと消すと識別子が
        変わり、それまでの未束縛の試行が別の端末のものになって、次の
        セットアップで紐づかなくなる。
        """
        for key in ("student_id", "device_token", "binding_id"):
            self._data.pop(key, None)
        self._save()
