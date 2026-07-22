"""受控的 Git 版本管理能力，供 Streamlit 界面调用。"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
from typing import Iterable


SAFE_NEW_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html",
    ".md", ".rst", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg",
    ".bat", ".cmd", ".ps1", ".sh",
}
SAFE_NEW_NAMES = {
    ".gitignore", ".gitattributes", "dockerfile", "makefile",
    "requirements.txt", "package.json", "package-lock.json", "tsconfig.json",
}
BLOCKED_PARTS = {
    "cache", "cache_result", "data", "dataset", "datasets", "output",
    "outputs", "logs", ".streamlit", ".venv", "venv", "env",
    "__pycache__", "dist", "build",
}
SENSITIVE_MARKERS = {
    "secret", "secrets", "credential", "credentials", "password", "passwd",
    "access_token", "private_key", "api_key",
}


@dataclass
class GitStatus:
    repo_path: Path
    branch: str = ""
    remote_url: str = ""
    changed_files: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    recent_commits: list[tuple[str, str, str]] = field(default_factory=list)
    error: str = ""


@dataclass
class GitActionResult:
    ok: bool
    message: str
    commit: str = ""
    pushed: bool = False
    details: str = ""


def _run_git(repo: Path, *args: str, timeout: int = 30, check: bool = True) -> str:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    completed = subprocess.run(
        ["git", *args], cwd=str(repo), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    if check and completed.returncode != 0:
        raise RuntimeError(output or f"git {' '.join(args)} 执行失败")
    return output


def _as_repo(path: Path) -> Path | None:
    try:
        root = _run_git(path, "rev-parse", "--show-toplevel")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    return Path(root.strip()).resolve() if root.strip() else None


def find_repository(app_dir: str | Path) -> Path | None:
    """查找当前项目仓库；离线运行目录可回退到同级 GitHub 主仓库。"""
    configured = os.environ.get("BANAUTE_GIT_REPO", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    app_path = Path(app_dir).resolve()
    candidates.extend((app_path, app_path.parent / "_github_banaute_tuiguang"))
    for candidate in candidates:
        if candidate.is_dir():
            repo = _as_repo(candidate)
            if repo is not None:
                return repo
    return None


def get_status(repo: str | Path, limit: int = 5) -> GitStatus:
    repo_path = Path(repo).resolve()
    status = GitStatus(repo_path=repo_path)
    try:
        status.branch = _run_git(repo_path, "branch", "--show-current") or "(游离状态)"
        status.remote_url = _run_git(repo_path, "config", "--get", "remote.origin.url", check=False)
        porcelain = _run_git(repo_path, "status", "--short", "--untracked-files=all")
        status.changed_files = [line for line in porcelain.splitlines() if line.strip()]
        counts = _run_git(
            repo_path, "rev-list", "--left-right", "--count", "@{upstream}...HEAD", check=False,
        ).split()
        if len(counts) == 2 and all(part.isdigit() for part in counts):
            status.behind, status.ahead = map(int, counts)
        log_text = _run_git(
            repo_path, "log", f"-{max(1, limit)}", "--date=short",
            "--pretty=format:%h%x09%s%x09%ad", check=False,
        )
        for line in log_text.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                status.recent_commits.append((parts[0], parts[1], parts[2]))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        status.error = str(exc)
    return status


def _safe_new_file(relative_path: str) -> bool:
    path = Path(relative_path)
    lowered_parts = {part.lower() for part in path.parts}
    lowered_name = path.name.lower()
    if lowered_parts & BLOCKED_PARTS:
        return False
    if lowered_name == ".env" or any(marker in lowered_name for marker in SENSITIVE_MARKERS):
        return False
    return lowered_name in SAFE_NEW_NAMES or path.suffix.lower() in SAFE_NEW_SUFFIXES


def _untracked_files(repo: Path) -> Iterable[str]:
    output = _run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    return (item for item in output.split("\0") if item)


def _stage_code_changes(repo: Path) -> None:
    # 已跟踪文件的修改/删除可安全纳入；新增文件仅纳入代码和项目配置白名单。
    _run_git(repo, "add", "-u", "--", ".")
    safe_new_files = [path for path in _untracked_files(repo) if _safe_new_file(path)]
    if safe_new_files:
        _run_git(repo, "add", "--", *safe_new_files)


def save_version(repo: str | Path, note: str, push: bool = True) -> GitActionResult:
    repo_path = Path(repo).resolve()
    clean_note = " ".join(note.strip().split())
    if len(clean_note) < 2:
        return GitActionResult(False, "请填写至少 2 个字的版本备注")
    if len(clean_note) > 100:
        return GitActionResult(False, "版本备注不能超过 100 个字")

    try:
        _stage_code_changes(repo_path)
        staged = _run_git(repo_path, "diff", "--cached", "--name-status")
        if not staged.strip():
            return GitActionResult(False, "没有可保存的代码改动；数据文件和缓存不会纳入版本")

        commit_message = (
            f"保存可回退版本：{clean_note}\n\n"
            "Constraint: In-app manual version snapshot\n"
            "Confidence: high\n"
            "Scope-risk: narrow\n"
            "Directive: Keep business data credentials and caches excluded\n"
            "Tested: Git staged-file validation\n"
            "Not-tested: Full application regression suite"
        )
        _run_git(repo_path, "commit", "-m", commit_message, timeout=60)
        commit = _run_git(repo_path, "rev-parse", "--short", "HEAD")

        if not push:
            return GitActionResult(True, "版本已保存在本机", commit=commit, details=staged)

        branch = _run_git(repo_path, "branch", "--show-current")
        remote = _run_git(repo_path, "config", "--get", "remote.origin.url", check=False)
        if not branch or not remote:
            return GitActionResult(
                True, "版本已保存在本机，但仓库未配置 GitHub 远端",
                commit=commit, details=staged,
            )
        try:
            _run_git(repo_path, "push", "origin", branch, timeout=120)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            return GitActionResult(
                False, "版本已保存在本机，但同步 GitHub 失败",
                commit=commit, details=str(exc),
            )
        return GitActionResult(
            True, "版本已保存并同步到 GitHub", commit=commit, pushed=True, details=staged,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return GitActionResult(False, "创建版本失败", details=str(exc))
