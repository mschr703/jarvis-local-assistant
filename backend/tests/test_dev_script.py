import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
spec = importlib.util.spec_from_file_location("jarvis_dev_script", path)
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def test_dev_reports_early_clean_child_exit_as_failure(tmp_path, monkeypatch, capsys):
    python_path = tmp_path / "backend" / ".venv" / "Scripts" / "python.exe"
    vite_path = tmp_path / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    python_path.parent.mkdir(parents=True)
    vite_path.parent.mkdir(parents=True)
    python_path.touch()
    vite_path.touch()
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "PID_FILE", tmp_path / ".jarvis-dev.json")

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _timeout):
            pass

        def connect_ex(self, _address):
            return 1

    class FakeProcess:
        def __init__(self, pid, returncode):
            self.pid = pid
            self.returncode = returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

    processes = iter((FakeProcess(101, 0), FakeProcess(102, None), FakeProcess(103, None)))
    calls = []
    monkeypatch.setenv("JARVIS_OPENROUTER_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr(dev.socket, "socket", FakeSocket)
    def fake_popen(*_args, **kwargs):
        calls.append(kwargs)
        return next(processes)

    monkeypatch.setattr(dev.subprocess, "Popen", fake_popen)
    assert dev.main() == 1
    assert "Backend wurde unerwartet beendet" in capsys.readouterr().err
    assert calls[0]["env"] is None
    assert "JARVIS_OPENROUTER_API_KEY" not in calls[1]["env"]
    assert "JARVIS_OPENROUTER_API_KEY" not in calls[2]["env"]
    assert not dev.PID_FILE.exists()
