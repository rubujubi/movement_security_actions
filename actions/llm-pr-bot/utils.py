"""
Shared utility functions for PR bot implementations.
"""
import os
import json
import base64
import requests


def load_event() -> dict:
    """Load the GitHub event payload for this run."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        raise RuntimeError("GITHUB_EVENT_PATH is not set or file does not exist")
    with open(event_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_pr_files(owner: str, repo: str, pr_number: int, github_token: str) -> list[dict]:
    """Fetch the list of files in a pull request via GitHub API."""
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }

    all_files = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls/"
            f"{pr_number}/files?page={page}&per_page=100"
        )
        res = requests.get(url, headers=headers)
        res.raise_for_status()

        page_files = res.json()
        all_files.extend(page_files)

        if len(page_files) < 100:
            break
        page += 1

    return all_files


def get_file_content(owner: str, repo: str, ref: str, path: str, github_token: str) -> str:
    """
    Fetch the full content of a file at a specific ref (commit SHA).
    Uses GitHub contents API.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    params = {"ref": ref}

    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 404:
        return ""  # File deleted or not found
    res.raise_for_status()

    data = res.json()

    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    if isinstance(data, dict) and "content" in data:
        return data["content"]

    return ""


def post_issue_comment(owner: str, repo: str, pr_number: int, token: str, body: str) -> None:
    """Post a regular issue comment to the PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    res = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"body": body},
    )
    res.raise_for_status()


def load_language_prompt(language: str, mode: str = None) -> str:
    """
    Load base system prompt from prompts/<mode>/<language>.txt.
    Fallback order:
    - prompts/<mode>/<language>.txt (if mode specified)
    - prompts/<mode>/generic.txt (if mode specified)
    - hardcoded fallback
    """
    lang = (language or "generic").lower()
    base_dir = os.path.dirname(__file__)

    # If mode is specified, look in mode-specific subfolder
    if mode:
        prompts_dir = os.path.join(base_dir, "prompts", mode)
    else:
        # Fallback to old behavior for backward compatibility
        prompts_dir = os.path.join(base_dir, "prompts")

    def read_prompt(name: str) -> str:
        path = os.path.join(prompts_dir, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    # 1) language-specific
    content = read_prompt(f"{lang}.txt")
    if content:
        return content

    # 2) generic fallback
    content = read_prompt("generic.txt")
    if content:
        return content

    # 3) last-resort fallback
    return "You are a strict but constructive senior code reviewer."


def build_diff(files: list[dict], limit: int = 12000) -> str:
    """Build a trimmed diff text from PR files, limited by character count."""
    text_parts = []
    current_len = 0

    for f in files:
        patch = f.get("patch")
        if not patch:
            continue

        chunk = (
            f"\n===== FILE: {f['filename']} ({f['status']}) =====\n"
            f"{patch}\n"
        )
        if current_len + len(chunk) > limit:
            text_parts.append("\n[... DIFF TRUNCATED ...]\n")
            break

        text_parts.append(chunk)
        current_len += len(chunk)

    return "".join(text_parts)


def build_single_file_diff(file: dict, limit: int = 4000) -> str:
    """Build a diff string for a single file with its own limit."""
    patch = file.get("patch")
    if not patch:
        return ""
    text = f"\n===== FILE: {file['filename']} ({file['status']}) =====\n{patch}\n"
    if len(text) > limit:
        return text[:limit] + "\n[... FILE DIFF TRUNCATED ...]\n"
    return text
