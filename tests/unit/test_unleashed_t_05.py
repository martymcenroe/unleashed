import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "src" / "unleashed-t-05.py"


def load_module():
    spec = importlib.util.spec_from_file_location("unleashed_t_05", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_last_builds_native_codex_resume_command():
    module = load_module()

    wrapper = module.UnleashedT(resume_last=True, codex_args=["continue work"])

    assert wrapper._build_codex_invocation() == [
        "cmd",
        "/c",
        module.CODEX_CMD,
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-c",
        "shell_environment_policy.inherit=all",
        "--search",
        "resume",
        "--last",
        "continue work",
    ]
    assert wrapper.launch_mode == "codex resume --last -a never -s workspace-write"


def test_resume_id_builds_native_codex_resume_command():
    module = load_module()

    wrapper = module.UnleashedT(resume_id="abc123", codex_args=["continue work"])

    assert wrapper._build_codex_invocation() == [
        "cmd",
        "/c",
        module.CODEX_CMD,
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-c",
        "shell_environment_policy.inherit=all",
        "--search",
        "resume",
        "abc123",
        "continue work",
    ]
    assert wrapper.launch_mode == "codex resume abc123 -a never -s workspace-write"


def test_fork_last_builds_native_codex_fork_command():
    module = load_module()

    wrapper = module.UnleashedT(fork_last=True, codex_args=["branch here"])

    assert wrapper._build_codex_invocation() == [
        "cmd",
        "/c",
        module.CODEX_CMD,
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-c",
        "shell_environment_policy.inherit=all",
        "--search",
        "fork",
        "--last",
        "branch here",
    ]
    assert wrapper.launch_mode == "codex fork --last -a never -s workspace-write"


def test_existing_pass_through_behavior_still_works():
    module = load_module()

    wrapper = module.UnleashedT(codex_args=["resume", "--last"])

    assert wrapper._build_codex_invocation() == [
        "cmd",
        "/c",
        module.CODEX_CMD,
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-c",
        "shell_environment_policy.inherit=all",
        "--search",
        "resume",
        "--last",
    ]
    assert wrapper.launch_mode == "codex -a never -s workspace-write"
