import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
LAUNCH_SCRIPT = REPO_ROOT / "AIVA" / "launch.sh"


@pytest.fixture

def fake_aiva(tmp_path):
    aiva = tmp_path / "AIVA"
    aiva.mkdir()
    (aiva / "launch.sh").write_bytes(LAUNCH_SCRIPT.read_bytes())
    ros_setup = tmp_path / "ros_setup.bash"
    ros_setup.write_text("# fake ROS environment for launch-service test\n")
    venv_python = aiva / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env bash\nexit 0\n")
    venv_python.chmod(0o755)
    setup = aiva / "install" / "setup.bash"
    setup.parent.mkdir(parents=True)
    setup.write_text("# fake colcon workspace\n")
    marker = aiva / "install" / "fake_pkg" / "share" / "ament_index" / "resource_index" / "packages" / "fake_pkg"
    marker.parent.mkdir(parents=True)
    marker.touch()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    service_log = tmp_path / "services.log"
    colcon = bin_dir / "colcon"
    colcon.write_text("""#!/usr/bin/env bash
if [[ "$1" == "list" ]]; then printf 'fake_pkg\\n'; fi
exit 0
""")
    ros2 = bin_dir / "ros2"
    ros2.write_text(f"""#!/usr/bin/env bash
if [[ "$1 $2" == "pkg prefix" ]]; then exit 0; fi
printf '%s\\n' "$*" >> {service_log}
exec sleep 60
""")
    xdg = bin_dir / "xdg-open"
    xdg.write_text("#!/usr/bin/env bash\nexit 0\n")
    for command in (colcon, ros2, xdg):
        command.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "ROS_SETUP": str(ros_setup),
        "AIVA_VENV": str(aiva / ".venv"),
        "AIVA_LOG_DIR": str(tmp_path / "logs"),
        "AIVA_LLM_MODEL": "test-model",
        "AIVA_PRELOAD_LLM": "0",
        "TELEPATHIC_GATEWAY": "http://127.0.0.1:9999",
        "HOME": str(tmp_path),
        "NO_COLOR": "1",
    })
    return aiva / "launch.sh", env, service_log


def run_to_ready(fake_aiva, start_chat):
    script, env, service_log = fake_aiva
    env["AIVA_START_CHAT"] = str(start_chat)
    output_path = script.parent / "launch-output.log"
    with output_path.open("w") as output_file:
        process = subprocess.Popen(
            ["bash", str(script)], env=env, cwd=script.parent,
            stdout=output_file, stderr=subprocess.STDOUT, start_new_session=True,
        )
    output = ""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        output = output_path.read_text()
        if "WebUI is ready" in output or process.poll() is not None:
            break
        time.sleep(0.05)
    if "WebUI is ready" not in output:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        pytest.fail("launch.sh did not reach ready state:\\n" + output)
    return process, service_log


@pytest.mark.parametrize(
    ("start_chat", "expected"),
    [
        ("1", ["launch rosbridge_server", "run aiva_chat", "run telepathic_management"]),
        ("0", ["launch rosbridge_server", "run telepathic_management"]),
    ],
)
def test_launch_starts_expected_services_and_stops_them(fake_aiva, start_chat, expected):
    process, service_log = run_to_ready(fake_aiva, start_chat)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            actual = service_log.read_text().splitlines() if service_log.exists() else []
            if len(actual) >= len(expected):
                break
            time.sleep(0.05)
        actual_services = {" ".join(line.split()[:2]) for line in actual}
        assert actual_services == set(expected)
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=8)
    assert process.returncode == 143
