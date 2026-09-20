"""対話そのものを、偽の端末に打鍵を流して見る。

同意の流れの試験は `FakePrompts` で台本を渡すので、ここが通らないと
「はい / いいえ / 中断」の写し方が実際の画面で合っているか誰も見ていない。
特に中断が None になることは、この道具の約束の中心である。
"""

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from lpp_collector.prompts import Prompts

ENTER = "\r"
DOWN = "\x1b[B"
ESC = "\x1b"
CTRL_C = "\x03"


def drive(keys, action):
    """打鍵を流して `action(ui)` を返す。"""
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        with create_app_session(input=pipe, output=DummyOutput()):
            return action(Prompts(title="試験"))


def test_the_first_choice_is_yes():
    """whiptail は「はい」に合わせて開いていた。そこを変えない。"""
    assert drive(ENTER, lambda ui: ui.ask("尋ねる")) is True


def test_moving_down_picks_no():
    assert drive(DOWN + ENTER, lambda ui: ui.ask("尋ねる")) is False


@pytest.mark.parametrize("key", [ESC, CTRL_C])
def test_interrupting_is_neither_yes_nor_no(key):
    """中断を「いいえ」にしてはならない。

    同意の POST は開いている同意を取り消して新しい行を積むので、
    抜けただけで False を送ると、研究に入っていた試行が黙って外れる。
    """
    assert drive(key, lambda ui: ui.ask("尋ねる")) is None


def test_the_labels_can_be_replaced():
    """同意の画面では「はい」ではなく「同意する」と出す。"""
    assert drive(ENTER, lambda ui: ui.ask("尋ねる", yes="同意する", no="同意しない"))


def test_secret_returns_what_was_typed():
    assert drive("a-token" + ENTER, lambda ui: ui.secret("トークン")) == "a-token"


@pytest.mark.parametrize("key", [ESC, CTRL_C])
def test_interrupting_the_secret_gives_nothing(key):
    """中断は空文字ではなく None。空のトークンで送りに行かせない。"""
    assert drive(key, lambda ui: ui.secret("トークン")) is None


MENU = [("consent", "同意の内容を決め直す"), ("quit", "終了する")]


def test_menu_returns_the_value_not_the_label():
    assert drive(DOWN + ENTER, lambda ui: ui.menu("現在", MENU)) == "quit"


@pytest.mark.parametrize("key", [ESC, CTRL_C])
def test_interrupting_the_menu_gives_nothing(key):
    assert drive(key, lambda ui: ui.menu("現在", MENU)) is None


def test_the_body_is_printed_so_it_stays_in_the_scrollback(capsys):
    """whiptail の画面は抜けると何も残らない。同意の文面は残す。"""
    drive(ENTER, lambda ui: ui.ask("よく読んでください"))
    assert "よく読んでください" in capsys.readouterr().out
