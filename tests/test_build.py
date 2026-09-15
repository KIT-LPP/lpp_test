import json

from lpp_collector import build


def test_json_diagnostics_are_separated_from_the_rest():
    stderr = "\n".join(
        [
            "make: gcc -c main.c",
            json.dumps([{"kind": "warning", "message": "unused variable"}]),
            "collect2: error: ld returned 1 exit status",
        ]
    )
    diagnostics, rest = build.parse_diagnostics(stderr)
    assert diagnostics == [{"kind": "warning", "message": "unused variable"}]
    assert "collect2" in rest
    assert "unused variable" not in rest


def test_a_warning_is_not_a_failed_build(tmp_path):
    """従来は stderr に何か出ただけで「コンパイル失敗」と判定していた。"""
    (tmp_path / "main.c").write_text(
        "#include <stdio.h>\nint main(void){int unused; printf(\"hi\\n\"); return 0;}\n"
    )
    result = build.compile_target("a.out", str(tmp_path))
    assert result.ok
    assert result.exit_code == 0


def test_a_broken_source_fails_and_records_diagnostics(tmp_path):
    (tmp_path / "main.c").write_text("int main(void){ return }\n")
    result = build.compile_target("a.out", str(tmp_path))
    assert not result.ok

    record = build.take_build_record()
    assert record["exit"] != 0
    assert record["diagnostics"]["gcc"], "JSON の診断が取れていない"
    # 取り出しは 1 回だけ。同じセッションで二重に送らない
    assert build.take_build_record() is None
