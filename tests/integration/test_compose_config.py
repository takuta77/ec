import os
import shutil
import subprocess
from pathlib import Path


def test_docker_compose_config_parses():
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    if not env_path.exists():
        shutil.copyfile(repo_root / ".env.example", env_path)
    env = {**os.environ, "NEW_RELIC_LICENSE_KEY": "stub"}
    r = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "config"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    assert r.returncode == 0, r.stderr
