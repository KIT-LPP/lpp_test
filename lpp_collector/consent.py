"""セットアップと同意の対話。

`lppconsent` が入口である。まだセットアップしていない端末では、まず端末と
学籍番号を結びつけ、続けて同意を尋ねる。`lppsetup` は新しく入れた端末のための
別名で、中身は同じ流れを通る。

前学期は、同意の POST がボディなしで送られて検証で弾かれ、その失敗が
`except Exception` に消えていた。ここでは失敗を必ず表示する。
"""

import sys
from typing import Any, Dict, Optional

from whiptail import Whiptail

from .api import ApiError, LppApi
from .config import (
    IS_DOCKER_ENV,
    LPP_AFTER_CONSENT_TEXT,
    LPP_CONSENT_TEXT,
    LPP_INCLUDE_PRIOR_AGAIN_TEXT,
    LPP_INCLUDE_PRIOR_TEXT,
    LPP_PUBLICATION_TEXT,
    LPP_REVOKE_CONSENT_TEXT,
    LPP_SETUP_TEXT,
    LPP_UNBIND_TEXT,
)
from .device import LppDevice
from .docker import fix_permission, run_test_container, update
from .version import warn_on_version_skew

TITLE = "言語処理プログラミング 研究への同意"


def _yes(whiptail: Whiptail, text: str, yes: str = "はい", no: str = "いいえ") -> bool:
    response = whiptail.run(
        "yesno",
        text,
        extra_args=["--scrolltext", "--yes-button", yes, "--no-button", no],
    )
    return response.returncode == 0


def _period(prior: Dict[str, Any]) -> str:
    start, end = prior.get("from"), prior.get("to")
    if not start or not end:
        return "期間不明"
    return f"{start} 〜 {end}"


# ---------------------------------------------------------------------------
def run_setup(api_base: Optional[str], device: LppDevice, whiptail: Whiptail) -> bool:
    """端末と学籍番号を結びつける。成功したら True。"""
    whiptail.msgbox(LPP_SETUP_TEXT)

    # トークンは引数に載せない。bash_history がホストに永続化されるため、
    # コマンドラインに書くと後から読める形で残る
    token, code = whiptail.inputbox("セットアップトークン", password=True)
    if code != 0 or not token.strip():
        print("セットアップを中止しました")
        return False

    with LppApi() if api_base is None else LppApi(base_url=api_base) as api:
        try:
            result = api.setup(token.strip(), device.device_id)
        except ApiError as e:
            if e.status_code == 401:
                whiptail.msgbox(
                    "トークンが正しくないか、既に無効になっています。\n"
                    "Redmine の Wiki を開き直して確認してください。"
                )
            elif e.status_code == 409:
                whiptail.msgbox(
                    "この端末は別の学生に結びついています。\n"
                    "共有の端末では使えません。担当者に連絡してください。"
                )
            else:
                whiptail.msgbox(f"セットアップに失敗しました。\n\n{e}")
            return False

        device.bind(result["studentId"], result["deviceToken"], result["bindingId"])
        prior = result.get("priorAttempts") or {}
        whiptail.msgbox(
            f"学籍番号 {result['studentId']} としてこの端末を登録しました。"
        )
        return ask_consent(api_with_token(api_base, device), whiptail, prior=prior)


def api_with_token(api_base: Optional[str], device: LppDevice) -> LppApi:
    if api_base is None:
        return LppApi(token=device.device_token)
    return LppApi(base_url=api_base, token=device.device_token)


# ---------------------------------------------------------------------------
def ask_consent(
    api: LppApi, whiptail: Whiptail, prior: Optional[Dict[str, Any]] = None
) -> bool:
    """研究利用の同意を尋ねて記録する。"""
    with api:
        research = _yes(whiptail, LPP_CONSENT_TEXT, yes="同意する", no="同意しない")
        if not research:
            # 同意しない場合も記録する。「尋ねたが断られた」と
            # 「まだ尋ねていない」は別の状態である
            publication = False
            include_prior = False
        else:
            publication = _yes(whiptail, LPP_PUBLICATION_TEXT)
            if prior and prior.get("count"):
                text = LPP_INCLUDE_PRIOR_TEXT.format(
                    count=prior["count"], period=_period(prior)
                )
            else:
                text = LPP_INCLUDE_PRIOR_AGAIN_TEXT
            # include_prior は毎回尋ねる。この POST は開いている同意を取り消して
            # 新しい行を積むので、既定値で送ると、それまで研究に入っていた
            # 試行が黙って外れる
            include_prior = _yes(whiptail, text)

        try:
            api.post_consent(research, publication, include_prior)
        except ApiError as e:
            whiptail.msgbox(f"同意の記録に失敗しました。\n\n{e}")
            return False

    whiptail.msgbox(
        LPP_AFTER_CONSENT_TEXT.format(
            research="同意する" if research else "同意しない",
            publication="はい" if publication else "いいえ",
            prior="含める" if include_prior else "含めない",
        )
    )
    return True


def revoke_consent(api: LppApi, whiptail: Whiptail) -> bool:
    if not _yes(whiptail, LPP_REVOKE_CONSENT_TEXT, yes="取り消す", no="やめる"):
        return False
    with api:
        try:
            api.revoke_consent()
        except ApiError as e:
            whiptail.msgbox(f"同意の取り消しに失敗しました。\n\n{e}")
            return False
    whiptail.msgbox("研究への同意を取り消しました。")
    return True


def unbind_device(api: LppApi, device: LppDevice, whiptail: Whiptail) -> bool:
    if not _yes(whiptail, LPP_UNBIND_TEXT, yes="解除する", no="やめる"):
        return False
    binding_id = device.binding_id
    with api:
        try:
            if binding_id:
                api.unbind_device(binding_id)
        except ApiError as e:
            whiptail.msgbox(f"解除に失敗しました。\n\n{e}")
            return False
    # 端末の識別子は残す。詳細は LppDevice.forget_binding を参照
    device.forget_binding()
    whiptail.msgbox("この端末の結びつきを解除しました。")
    return True


# ---------------------------------------------------------------------------
def _state_summary(api: LppApi) -> str:
    with api:
        try:
            state = api.get_consent()
        except ApiError as e:
            return f"現在の同意状態を取得できませんでした: {e}"
    if state is None:
        return "現在: 研究利用への同意は記録されていません"
    return (
        "現在: 研究利用 {research} / 発表での引用 {publication} / "
        "決定より前の記録 {prior}\n決定日時 {at}"
    ).format(
        research="同意" if state["researchOk"] else "同意しない",
        publication="はい" if state["publicationOk"] else "いいえ",
        prior="含める" if state["includePrior"] else "含めない",
        at=state["decidedAt"],
    )


def interactive(api_base: Optional[str] = None) -> int:
    device = LppDevice()
    whiptail = Whiptail(title=TITLE)

    if not device.is_bound():
        return 0 if run_setup(api_base, device, whiptail) else 1

    while True:
        summary = _state_summary(api_with_token(api_base, device))
        choice, code = whiptail.menu(
            summary,
            [
                ("consent", "同意の内容を決め直す"),
                ("revoke", "研究への同意を取り消す"),
                ("unbind", "この端末の結びつきを解除する"),
                ("quit", "終了する"),
            ],
        )
        if code != 0 or choice == "quit":
            return 0
        if choice == "consent":
            ask_consent(api_with_token(api_base, device), whiptail)
        elif choice == "revoke":
            revoke_consent(api_with_token(api_base, device), whiptail)
        elif choice == "unbind":
            if unbind_device(api_with_token(api_base, device), device, whiptail):
                return 0


# ---------------------------------------------------------------------------
def main():
    if IS_DOCKER_ENV:
        warn_on_version_skew()
        try:
            code = interactive()
        finally:
            fix_permission()
        sys.exit(code)

    update()
    run_test_container(["lppconsent", *sys.argv[1:]])


def setup_main():
    """`lppsetup`。入口の名前が違うだけで中身は同じ流れを通る。"""
    if IS_DOCKER_ENV:
        warn_on_version_skew()
        try:
            code = interactive()
        finally:
            fix_permission()
        sys.exit(code)

    update()
    run_test_container(["lppconsent", *sys.argv[1:]])
