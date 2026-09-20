"""端末に尋ねる道具。

whiptail から InquirerPy に移した。whiptail は画面を全面に描き、抜けると
何も残らない。同意の文面は後から読み返せるほうがよいので、本文は端末に
そのまま印字し、選ぶところだけを InquirerPy に任せる。

ここで守っているのは「はい / いいえ / 中断」の区別である。whiptail では
ESC が返り値 255 で、それを `None` に写していた。InquirerPy では ctrl-c が
`KeyboardInterrupt` を上げ、ESC は `mandatory=False` の skip として `None` を
返す。どちらも「いいえ」ではない。詳しくは `consent._yes` を参照。
"""

from typing import List, Optional, Sequence, Tuple

from InquirerPy import inquirer
from InquirerPy.base.control import Choice

# ESC と ctrl-z は「中断」。whiptail の ESC と同じ位置に置く
SKIP_KEYS = [{"key": "escape"}, {"key": "c-z"}]


def _body(text: str) -> None:
    print(text.strip())
    print()


class Prompts:
    """画面を持たない、印字と選択だけの対話。"""

    def __init__(self, title: str):
        print(f"== {title} ==")
        print()

    def notice(self, text: str) -> None:
        """結果や失敗を伝える。whiptail の msgbox にあたる。

        whiptail は OK を押すまで止まっていたが、ここでは印字したものが
        端末に残るので、止める必要がない。
        """
        _body(text)

    def ask(
        self,
        text: str,
        yes: str = "はい",
        no: str = "いいえ",
        question: str = "選んでください",
    ) -> Optional[bool]:
        """はい / いいえ / 中断 (None) を返す。

        ボタンの文言を呼び出し側が決められるようにしてある。同意の画面では
        「はい」ではなく「同意する」と出したい。`inquirer.confirm` は y/n の
        一打鍵で文言を差し替えられないので、二択の select を使う。
        """
        _body(text)
        return self._execute(
            inquirer.select(
                message=question,
                choices=[Choice(True, name=yes), Choice(False, name=no)],
                default=True,  # whiptail も「はい」に合わせて開いていた
                mandatory=False,
                keybindings={"skip": SKIP_KEYS},
                long_instruction="ESC または ctrl-c で中断します",
            )
        )

    def secret(self, label: str) -> Optional[str]:
        """伏せ字で受け取る。中断したときは None。"""
        return self._execute(
            inquirer.secret(
                message=label,
                transformer=lambda _: "[入力しました]",
                mandatory=False,
                keybindings={"skip": SKIP_KEYS},
                long_instruction="ESC または ctrl-c で中断します",
            )
        )

    def menu(
        self,
        text: str,
        items: Sequence[Tuple[str, str]],
        question: str = "操作を選んでください",
    ) -> Optional[str]:
        """`(値, 表示)` の並びから一つ選ばせる。中断したときは None。"""
        _body(text)
        choices: List[Choice] = [Choice(value, name=label) for value, label in items]
        return self._execute(
            inquirer.select(
                message=question,
                choices=choices,
                mandatory=False,
                keybindings={"skip": SKIP_KEYS},
                long_instruction="ESC または ctrl-c で中断します",
            )
        )

    @staticmethod
    def _execute(prompt):
        """中断を例外ではなく None で返す。

        ctrl-c は KeyboardInterrupt、閉じた標準入力は EOFError になる。
        どちらも呼び出し側では「答えていない」であって「いいえ」ではない。
        """
        try:
            return prompt.execute()
        except (KeyboardInterrupt, EOFError):
            print()
            return None
