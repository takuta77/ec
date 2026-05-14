"""DO NOT MERGE — used to validate that the sast required check fires."""
import os


def run_unsafe_command(user_input: str) -> None:
    # Semgrep should flag this as dangerous-system-call.
    os.system(user_input)
