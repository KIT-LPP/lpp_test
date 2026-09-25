"""サーバが配るフィーチャーフラグを受け取る。

設計はサーバ側の `docs/feature-flags-plan.md` にある。

解決はサーバが持つ。ここは文脈 (課題と端末トークン) を渡して、解決済みの値を
受け取るだけにする。ホスト側の lpp_test は pipx で入れたまま更新されないので、
ルールをこちらに書くと後から直せない。同じ理由で、取得はコンテナの中
(`runner.run_pytest`) で行い、ホスト側では行わない。

**届かなければ既定値で動く。** 取得の失敗は画面に出さず、再送もしない。
オフラインの学生に、テストと関係のない警告を毎回見せることになるからである。
値のキャッシュも持たない。前回取得した「有効」がオフラインの端末で残り続ける。
"""

import threading
from typing import Callable, Dict, Optional

import httpx

from .api import LppApi
from .device import LppDevice

# 取得の全体の期限。pytest を起動する前に待つので、テストの開始がこの分だけ
# 遅れうる。DNS の失敗や接続の拒否はすぐ返るので、ここまで待つのは
# パケットが黙って捨てられるネットワークに限られる
DEADLINE = 3.0

# httpx の期限は段ごと (接続、読み出し) なので、全体の期限は別に掛ける
# (fetch_flags のスレッド)。ここは接続が詰まったときの後始末を早めるだけ
TIMEOUT = httpx.Timeout(DEADLINE)

# ---------------------------------------------------------------------------
# 自動提出
# ---------------------------------------------------------------------------
AUTO_SUBMIT = "auto_submit"

# 全テストが通ったら [y/N] で提出を尋ねる
PROMPT = "prompt"
# 尋ねない。行が無いとき、知らない値のときもこれになる
OFF = "off"
# サーバに届かず、既定値 (OFF) で動いた。試行の envLabels に残すための値で、
# 挙動は OFF と同じである
UNAVAILABLE = "unavailable"

# runner から pytest (プラグイン) へ実効値を渡す伝達路。道具の内部のもので、
# 学生が設定するものではない (`LPP_SESSION_ID` と同じ扱い)
AUTO_SUBMIT_ENV = "LPP_AUTO_SUBMIT"

AUTO_SUBMIT_MODES = (PROMPT, OFF, UNAVAILABLE)


def fetch_flags(
    assignment: Optional[str],
    device: Optional[LppDevice] = None,
    api_factory: Optional[Callable[[Optional[str]], LppApi]] = None,
    deadline: float = DEADLINE,
) -> Optional[Dict[str, str]]:
    """サーバから解決済みのフラグを受け取る。届かなければ None。

    接続できない、期限切れ、4xx、5xx、壊れた応答のどれも同じく None にする。
    401 (束縛が外れた) でも手元の束縛は手放さない。それはアップロードの側の
    仕事で、ここで手放すと、フラグを取れなかっただけで提出もできなくなる。

    取得は daemon のスレッドで行い、`deadline` 秒で見切る。daemon にしないと、
    接続が詰まったときにプロセスが終われない。
    """
    if device is None:
        device = LppDevice()
    token = device.device_token
    factory = api_factory or (lambda t: LppApi(token=t, timeout=TIMEOUT))

    result: Dict[str, Optional[Dict[str, str]]] = {"flags": None}

    def work():
        try:
            api = factory(token)
        except Exception:  # noqa: BLE001 - フラグのためにテストを止めない
            return
        try:
            flags = api.get_flags(assignment)
            if isinstance(flags, dict):
                result["flags"] = {
                    str(k): v for k, v in flags.items() if isinstance(v, str)
                }
        except Exception:  # noqa: BLE001 - 失敗の種類を問わず既定値に落とす
            pass
        finally:
            try:
                api.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=work, name="lpp-flags", daemon=True)
    thread.start()
    thread.join(deadline)
    return result["flags"]


def auto_submit_mode(flags: Optional[Dict[str, str]]) -> str:
    """自動提出の実効値。`prompt` / `off` / `unavailable`。

    `prompt` 以外の値は、知らない値も含めてすべて `off` として扱う。
    確認なしで提出する値は置いていない。
    """
    if flags is None:
        return UNAVAILABLE
    return PROMPT if flags.get(AUTO_SUBMIT) == PROMPT else OFF
