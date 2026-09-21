"""セットアップと同意の対話。

`lppconsent` が入口である。まだセットアップしていない端末では、まず端末と
学籍番号を結びつけ、続けて同意を尋ねる。`lppsetup` は新しく入れた端末のための
別名で、中身は同じ流れを通る。

前学期は、同意の POST がボディなしで送られて検証で弾かれ、その失敗が
`except Exception` に消えていた。ここでは失敗を必ず表示する。
"""

import sys
from typing import Any, Dict, Optional

from .api import ApiError, LppApi
from .config import (
    IS_DOCKER_ENV,
    LPP_AFTER_CONSENT_TEXT,
    LPP_CONSENT_TEXT,
    LPP_FIRST_RUN_TEXT,
    LPP_INCLUDE_PRIOR_AGAIN_TEXT,
    LPP_INCLUDE_PRIOR_FIRST_TEXT,
    LPP_INCLUDE_PRIOR_TEXT,
    LPP_PUBLICATION_TEXT,
    LPP_REVOKE_CONSENT_TEXT,
    LPP_SETUP_TEXT,
    LPP_UNBIND_TEXT,
)
from .device import LppDevice
from .docker import fix_permission, run_test_container, update
from .prompts import Prompts
from .version import warn_on_version_skew

TITLE = "言語処理プログラミング 研究への同意"


def _yes(
    ui: Prompts, text: str, yes: str = "はい", no: str = "いいえ"
) -> Optional[bool]:
    """はい / いいえ / 中断 を区別する。

    中断 (None) を「いいえ」として扱ってはならない。この POST は開いている
    同意を取り消して新しい行を積むので、画面から抜けただけで同意が取り消され、
    それまで研究に入っていた試行が黙って外れる。
    """
    return ui.ask(text, yes=yes, no=no)


def _period(prior: Dict[str, Any]) -> str:
    start, end = prior.get("from"), prior.get("to")
    if not start or not end:
        return "期間不明"
    return f"{start} 〜 {end}"


def _prior_summary(setup_result: Dict[str, Any]) -> Optional[str]:
    """セットアップ前に溜まっていた記録を数えて示す。

    テストの試行と lpprun の実行は別に数える。どちらも同じ端末の名義で
    溜まっていて、セットアップでまとめて学生に帰属するので、黙って
    含めてはならない。
    """
    lines = []
    for key, label in (("priorAttempts", "テストの実行"), ("priorRuns", "lpprun の実行")):
        prior = setup_result.get(key) or {}
        if prior.get("count"):
            lines.append(f"・{label} {prior['count']} 件 ({_period(prior)})")
    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
def run_setup(api_base: Optional[str], device: LppDevice, ui: Prompts) -> bool:
    """端末と学籍番号を結びつける。成功したら True。"""
    ui.notice(LPP_SETUP_TEXT)

    # トークンは引数に載せない。bash_history がホストに永続化されるため、
    # コマンドラインに書くと後から読める形で残る
    token = ui.secret("セットアップトークン")
    if not token or not token.strip():
        print("セットアップを中止しました")
        return False

    with LppApi() if api_base is None else LppApi(base_url=api_base) as api:
        try:
            result = api.setup(token.strip(), device.device_id)
        except ApiError as e:
            if e.status_code == 401:
                ui.notice(
                    "トークンが正しくないか、既に無効になっています。\n"
                    "Redmine の Wiki を開き直して確認してください。"
                )
            elif e.status_code == 409:
                ui.notice(
                    "この端末は別の学生に結びついています。\n"
                    "共有の端末では使えません。担当者に連絡してください。"
                )
            else:
                ui.notice(f"セットアップに失敗しました。\n\n{e}")
            return False

        device.bind(result["studentId"], result["deviceToken"], result["bindingId"])
        ui.notice(
            f"学籍番号 {result['studentId']} としてこの端末を登録しました。"
        )
        return ask_consent(
            api_with_token(api_base, device),
            ui,
            prior_summary=_prior_summary(result),
        )


# ---------------------------------------------------------------------------
FIRST_RUN_TITLE = "言語処理プログラミング 提出のセットアップ"


def offer_setup_on_first_run(
    api_base: Optional[str] = None,
    device: Optional[LppDevice] = None,
    ui: Optional[Prompts] = None,
    interactive: Optional[bool] = None,
) -> bool:
    """まだ登録していない端末で `lpptest` を走らせる前に持ちかける。

    提出には端末の登録が要るが、これまでは全部通った後に
    「`lppsetup` を実行してください」と出るだけで、そこから先は学生が
    別のコマンドを打ち直す必要があった。初回にここで尋ねておけば、
    そのまま提出まで進める。

    断る道を必ず残す。テストを走らせに来た学生を、登録しないと先へ
    進めない画面で止めてはならない。「今回はしない」と「今後は尋ねない」は
    分ける。前者は保留であって断りではない。
    """
    device = device or LppDevice()
    if device.is_bound() or device.setup_declined:
        return False

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        # 端末が無いところ (パイプ、CI、エージェント) では尋ねられない。
        # 毎回の案内も出さない。提出が要る場面になれば submit.py が書く
        return False

    ui = ui or Prompts(title=FIRST_RUN_TITLE)
    choice = ui.menu(
        LPP_FIRST_RUN_TEXT,
        [
            ("setup", "今すぐセットアップする"),
            ("later", "今はしない (次に実行したときにまた尋ねます)"),
            ("never", "今後は尋ねない"),
        ],
        question="どうしますか",
    )
    if choice == "never":
        device.decline_setup()
        print("[lpp] 以後この確認は出しません。`lppsetup` でいつでもセットアップできます")
        return False
    if choice != "setup":
        # ESC も「今はしない」も保留として扱う。答えていないことを
        # 断りとして残さない
        print("[lpp] セットアップしていません。`lppsetup` でいつでもできます")
        return False

    return run_setup(api_base, device, ui)


def api_with_token(api_base: Optional[str], device: LppDevice) -> LppApi:
    if api_base is None:
        return LppApi(token=device.device_token)
    return LppApi(base_url=api_base, token=device.device_token)


# ---------------------------------------------------------------------------
def ask_consent(
    api: LppApi, ui: Prompts, prior_summary: Optional[str] = None
) -> bool:
    """研究利用の同意を尋ねて記録する。"""
    with api:
        try:
            current = api.get_consent()
        except ApiError:
            current = None

        research = _yes(ui, LPP_CONSENT_TEXT, yes="同意する", no="同意しない")
        if research is None:
            print("同意の確認を中断しました。記録は変えていません")
            return False
        if not research:
            # 同意しない場合も記録する。「尋ねたが断られた」と
            # 「まだ尋ねていない」は別の状態である
            publication = False
            include_prior = False
        else:
            publication = _yes(ui, LPP_PUBLICATION_TEXT)
            if publication is None:
                print("同意の確認を中断しました。記録は変えていません")
                return False

            if prior_summary:
                text = LPP_INCLUDE_PRIOR_TEXT.format(summary=prior_summary)
            elif current is None:
                # この端末に前の分がなくても、別の端末の分がありうる
                text = LPP_INCLUDE_PRIOR_FIRST_TEXT
            else:
                text = LPP_INCLUDE_PRIOR_AGAIN_TEXT
            # include_prior は毎回尋ねる。この POST は開いている同意を取り消して
            # 新しい行を積むので、既定値で送ると、それまで研究に入っていた
            # 試行が黙って外れる
            include_prior = _yes(ui, text)
            if include_prior is None:
                print("同意の確認を中断しました。記録は変えていません")
                return False

        try:
            api.post_consent(research, publication, include_prior)
        except ApiError as e:
            ui.notice(f"同意の記録に失敗しました。\n\n{e}")
            return False

    ui.notice(
        LPP_AFTER_CONSENT_TEXT.format(
            research="同意する" if research else "同意しない",
            publication="はい" if publication else "いいえ",
            prior="含める" if include_prior else "含めない",
        )
    )
    return True


def revoke_consent(api: LppApi, ui: Prompts) -> bool:
    if _yes(ui, LPP_REVOKE_CONSENT_TEXT, yes="取り消す", no="やめる") is not True:
        return False
    with api:
        try:
            api.revoke_consent()
        except ApiError as e:
            ui.notice(f"同意の取り消しに失敗しました。\n\n{e}")
            return False
    ui.notice("研究への同意を取り消しました。")
    return True


def unbind_device(api: LppApi, device: LppDevice, ui: Prompts) -> bool:
    if _yes(ui, LPP_UNBIND_TEXT, yes="解除する", no="やめる") is not True:
        return False
    binding_id = device.binding_id
    with api:
        try:
            if binding_id:
                api.unbind_device(binding_id)
        except ApiError as e:
            if e.status_code != 401:
                ui.notice(f"解除に失敗しました。\n\n{e}")
                return False
            # サーバ側では既に解除されている。手元を揃えて進む
    # 端末の識別子は残す。詳細は LppDevice.forget_binding を参照
    device.forget_binding()
    ui.notice("この端末の結びつきを解除しました。")
    return True


# ---------------------------------------------------------------------------
class StaleToken(Exception):
    """サーバ側で束縛が解除されている。"""


def _state_summary(api: LppApi) -> str:
    with api:
        try:
            state = api.get_consent()
        except ApiError as e:
            if e.status_code == 401:
                raise StaleToken() from e
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
    if not sys.stdin.isatty():
        # 打鍵を読む道具なので、端末が無いところでは何も尋ねられない。
        # 黙って壊れた画面を出すより、どこで動かせばよいかを書く
        print(
            "[lpp] 同意とセットアップの画面には端末が要ります。"
            "エージェントや CI からではなく、ターミナルで直接 "
            "`lppsetup` を実行してください"
        )
        return 1

    device = LppDevice()
    ui = Prompts(title=TITLE)

    if not device.is_bound():
        return 0 if run_setup(api_base, device, ui) else 1

    while True:
        try:
            summary = _state_summary(api_with_token(api_base, device))
        except StaleToken:
            # 端末トークンが無効なままだと、どの項目も 401 になって
            # セットアップし直す道がなくなる
            device.forget_binding()
            ui.notice(
                "この端末の登録は解除されています。もう一度セットアップします。"
            )
            return 0 if run_setup(api_base, device, ui) else 1
        choice = ui.menu(
            summary,
            [
                ("consent", "同意の内容を決め直す"),
                ("revoke", "研究への同意を取り消す"),
                ("unbind", "この端末の結びつきを解除する"),
                ("quit", "終了する"),
            ],
        )
        if choice is None or choice == "quit":
            return 0
        if choice == "consent":
            ask_consent(api_with_token(api_base, device), ui)
        elif choice == "revoke":
            revoke_consent(api_with_token(api_base, device), ui)
        elif choice == "unbind":
            if unbind_device(api_with_token(api_base, device), device, ui):
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
