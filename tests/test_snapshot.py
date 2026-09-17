import io
import tarfile

from lpp_collector.snapshot import collect_sources, snapshot


def test_paths_are_relative_to_the_source_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "main.c").write_text("int main(void){return 0;}")
    (tmp_path / "sub" / "util.h").write_text("#pragma once")
    (tmp_path / "notes.txt").write_text("ignored")

    names = sorted(arc for _, arc in collect_sources(str(tmp_path)))
    assert names == ["main.c", "sub/util.h"]

    with tarfile.open(fileobj=io.BytesIO(snapshot(str(tmp_path)))) as tf:
        # 絶対パスのまま入れると端末ごとに違う名前になり、サーバ側で
        # 同じソースが別物として溜まる
        assert sorted(tf.getnames()) == ["main.c", "sub/util.h"]
