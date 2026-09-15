import json
import os
import stat

from lpp_collector.device import LppDevice


def test_the_token_file_is_not_readable_by_others(tmp_path):
    path = tmp_path / "device.json"
    device = LppDevice(str(path))
    device.bind("s1", "secret-token", "b1")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_the_device_id_survives_a_reload(tmp_path):
    path = str(tmp_path / "device.json")
    first = LppDevice(path)
    again = LppDevice(path)
    assert first.device_id == again.device_id


def test_an_old_file_keeps_its_device_id(tmp_path):
    """旧版の device.json は識別子だけを持つ。

    作り直すと、サーバが預かっている未束縛の試行が迷子になり、
    セットアップで学生に紐づかなくなる。
    """
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"device_id": "legacy-id"}))
    assert LppDevice(str(path)).device_id == "legacy-id"


def test_forgetting_a_binding_keeps_the_device_id(tmp_path):
    path = str(tmp_path / "device.json")
    device = LppDevice(path)
    device_id = device.device_id
    device.bind("s1", "tok", "b1")
    device.forget_binding()

    assert device.device_token is None
    assert device.student_id is None
    assert device.device_id == device_id
    assert LppDevice(path).device_id == device_id
