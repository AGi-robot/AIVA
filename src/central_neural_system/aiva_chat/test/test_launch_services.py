import os
import signal
import select
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
    process = subprocess.Popen(
        ["bash", str(script)], env=env, cwd=script.parent,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
    )
    output = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.2)
        if ready:
            line = process.stdout.readline()
        else:
            line = ""
        if line:
            output.append(line)
            if "WebUI is ready" in line:
                break
        elif process.poll() is not None:
            break
    if not any("WebUI is ready" in line for line in output):
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        pytest.fail("launch.sh did not reach ready state:\\n" + "".join(output))
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
