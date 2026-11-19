import pytest

from jarvis.instance import InstanceLock


def test_second_instance_cannot_take_same_data_lock(tmp_path):
    first = InstanceLock(tmp_path / "jarvis.lock")
    second = InstanceLock(tmp_path / "jarvis.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="bereits"):
            second.acquire()
        assert second.handle is None
    finally:
        first.release()
    second.acquire()
    second.release()
