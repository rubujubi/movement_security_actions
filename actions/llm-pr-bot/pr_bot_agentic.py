import os
import json
import base64
import subprocess
import requests
import anthropic
from pathlib import Path
from typing import Any


# Load the GitHub event payload for this run
def load_event() -> dict:
    
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        raise RuntimeError("GITHUB_EVENT_PATH is not set or file does not exist")
    with open(event_path, "r") as f:
        return json.load(f)

# Get PR files locally using git
def get_pr_files_local(repo_path: str, base_ref: str, head_ref: str) -> list[dict]:
    try:
        # Get list of changed files with stats
        cmd = ["git", "diff", "--name-status", f"{base_ref}...{head_ref}"]
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return []

        files = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split('\t')
            if len(parts) < 2:
                continue

            status_code = parts[0]
            filename = parts[1]

            # Map git status to GitHub status
            status_map = {
                'A': 'added',
                'M': 'modified',
                'D': 'removed',
                'R': 'renamed',
                'C': 'copied'
            }
            status = status_map.get(status_code[0], 'modified')

            # Get diff stats (additions/deletions)
            stat_cmd = ["git", "diff", "--numstat", f"{base_ref}...{head_ref}", "--", filename]
            stat_result = subprocess.run(
                stat_cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            additions = 0
            deletions = 0
            if stat_result.returncode == 0 and stat_result.stdout.strip():
                stat_parts = stat_result.stdout.strip().split('\t')
                if len(stat_parts) >= 2:
                    try:
                        additions = int(stat_parts[0]) if stat_parts[0] != '-' else 0
                        deletions = int(stat_parts[1]) if stat_parts[1] != '-' else 0
                    except ValueError:
                        pass

            # Get patch/diff for the file
            patch_cmd = ["git", "diff", f"{base_ref}...{head_ref}", "--", filename]
            patch_result = subprocess.run(
                patch_cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            patch = patch_result.stdout if patch_result.returncode == 0 else ""

            files.append({
                "filename": filename,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "changes": additions + deletions,
                "patch": patch
            })

        return files

    except Exception as e:
        print(f"Error getting files locally: {e}")
        return []


# Fetch the list of files in a pull request (fallback to API)
def get_pr_files(owner: str, repo: str, pr_number: int, github_token: str) -> list[dict]:
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

# Fetch the full content of a file at a specific ref 
def get_file_content(owner: str, repo: str, ref: str, path: str, github_token: str) -> str:
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
        return base64.b64decode(data["content"])

    if isinstance(data, dict) and "content" in data:
        return data["content"]

    return ""


def post_issue_comment(owner: str, repo: str, pr_number: int, token: str, body: str) -> None:
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

#Load base system prompt from prompts/<mode>/<language>.txt
def load_language_prompt(language: str, mode: str = "agentic_tool") -> str:
    lang = (language or "generic").lower()
    base_dir = os.path.dirname(__file__)
    prompts_dir = os.path.join(base_dir, "prompts", mode)

    def read_prompt(name: str) -> str:
        path = os.path.join(prompts_dir, name)
        if os.path.exists(path):
            with open(path, "r") as f:
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
    return "You are a senior code reviewer."


def build_system_prompt() -> str:
    """Build the system prompt from language-specific file + extra instructions."""
    language = os.environ.get("LANGUAGE", "generic")
    mode = "agentic_tool"  # Always use agentic_tool mode for pr_bot_agentic.py
    base = load_language_prompt(language, mode)
    extra = os.environ.get("EXTRA_INSTRUCTIONS", "").strip()

    if extra:
        return base + "\n\n" + extra
    return base


# TOOLS definitions
TOOLS = [
    {
        "name": "read_file",
        "description": "Read the complete contents of ANY file from the repository. You have full access to all files in the codebase, not just changed files. Use this to examine files in detail, understand context, check dependencies, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file relative to repository root (e.g., 'src/main.py', 'tests/test_foo.py', 'README.md')"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "search_code",
        "description": "Search for a pattern in the codebase using grep. Returns matching lines with context. Use this to find function definitions, usages, or specific patterns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for"
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Optional glob pattern to filter files (e.g., '*.py', '*.move'). If not specified, searches all files."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching lines to return (default: 50)",
                    "default": 50
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and directories in a given path. Useful for exploring repository structure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to repository root (empty string for root)",
                    "default": ""
                }
            }
        }
    },
    {
        "name": "get_pr_context",
        "description": "Get detailed information about the pull request including title, description, changed files list, and diff summary.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]


# Tool Execution Handlers
class ToolContext:
    """Context object passed to tool handlers."""
    def __init__(self, owner: str, repo: str, pr_number: int, head_sha: str,
                 github_token: str, repo_path: str, pr_data: dict, pr_files: list[dict]):
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self.head_sha = head_sha
        self.github_token = github_token
        self.repo_path = repo_path
        self.pr_data = pr_data
        self.pr_files = pr_files


def execute_tool(tool_name: str, tool_input: dict, context: ToolContext) -> str:
    """Execute a tool and return the result as a string."""

    if tool_name == "read_file":
        file_path = tool_input["file_path"]
        try:
            # First, try to read from local filesystem (faster and more reliable)
            local_file_path = os.path.join(context.repo_path, file_path)

            if os.path.exists(local_file_path) and os.path.isfile(local_file_path):
                # Read from local checkout
                with open(local_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            else:
                # Fallback to GitHub API (useful if file doesn't exist locally)
                content = get_file_content(
                    context.owner,
                    context.repo,
                    context.head_sha,
                    file_path,
                    context.github_token
                )

            if not content:
                return f"File '{file_path}' not found or is empty."

            # Limit very large files
            if len(content) > 50000:
                content = content[:50000] + f"\n\n[... truncated, file is {len(content)} chars total]"

            return f"=== Content of {file_path} ===\n\n{content}"
        except Exception as e:
            return f"Error reading file '{file_path}': {str(e)}"

    elif tool_name == "search_code":
        pattern = tool_input["pattern"]
        file_pattern = tool_input.get("file_pattern", "*")
        max_results = min(tool_input.get("max_results", 50), 200)

        try:
            # Use ripgrep if available, otherwise fall back to grep
            cmd = ["rg", "-n", "-C", "2", "--max-count", str(max_results)]
            if file_pattern != "*":
                cmd.extend(["-g", file_pattern])
            cmd.append(pattern)

            result = subprocess.run(
                cmd,
                cwd=context.repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 1:  # No matches
                return f"No matches found for pattern '{pattern}'"
            elif result.returncode > 1:  # Error
                # Try fallback to grep
                cmd = ["grep", "-rn", "-C", "2", "-m", str(max_results), pattern, "."]
                result = subprocess.run(
                    cmd,
                    cwd=context.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

            output = result.stdout.strip()
            if not output:
                return f"No matches found for pattern '{pattern}'"

            # Limit output size
            if len(output) > 10000:
                output = output[:10000] + "\n\n[... truncated, too many results]"

            return f"=== Search results for '{pattern}' ===\n\n{output}"
        except subprocess.TimeoutExpired:
            return "Search timed out (>30s). Try a more specific pattern."
        except Exception as e:
            return f"Error searching: {str(e)}"

    elif tool_name == "list_directory":
        path = tool_input.get("path", "")
        full_path = os.path.join(context.repo_path, path)

        try:
            if not os.path.exists(full_path):
                return f"Directory '{path}' does not exist."

            if not os.path.isdir(full_path):
                return f"'{path}' is not a directory."

            entries = []
            for entry in sorted(os.listdir(full_path)):
                entry_path = os.path.join(full_path, entry)
                if os.path.isdir(entry_path):
                    entries.append(f"[DIR]  {entry}/")
                else:
                    size = os.path.getsize(entry_path)
                    entries.append(f"[FILE] {entry} ({size} bytes)")

            if not entries:
                return f"Directory '{path or 'root'}' is empty."

            return f"=== Contents of '{path or 'root'}' ===\n\n" + "\n".join(entries)
        except Exception as e:
            return f"Error listing directory '{path}': {str(e)}"

    elif tool_name == "get_pr_context":
        # Build a summary of the PR
        files_summary = []
        for f in context.pr_files[:20]:  # Limit to first 20 files
            files_summary.append(f"  - {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})")

        if len(context.pr_files) > 20:
            files_summary.append(f"  ... and {len(context.pr_files) - 20} more files")

        output = f"""=== Pull Request Context ===

Title: {context.pr_data['title']}

Description:
{context.pr_data.get('body') or '[No description provided]'}

Changed Files ({len(context.pr_files)} total):
{chr(10).join(files_summary)}

Base: {context.pr_data['base']['ref']}
Head: {context.pr_data['head']['ref']} ({context.head_sha[:8]})
"""
        return output

    else:
        return f"Unknown tool: {tool_name}"



def run_agentic_review_with_tools(
    pr: dict,
    files: list[dict],
    owner: str,
    repo: str,
    head_sha: str,
    github_token: str,
    repo_path: str,
) -> str:

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
    client = anthropic.Anthropic(api_key=api_key)

    # Create tool context
    context = ToolContext(
        owner=owner,
        repo=repo,
        pr_number=pr["number"],
        head_sha=head_sha,
        github_token=github_token,
        repo_path=repo_path,
        pr_data=pr,
        pr_files=files
    )

    # Load system prompt from prompts/<language>.txt
    system_prompt = build_system_prompt()

    # Build initial user prompt
    initial_prompt = "Please begin your code review."

    messages = [{"role": "user", "content": initial_prompt}]
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system_prompt,
            tools=TOOLS,
            messages=messages
        )

        # Add assistant response to messages
        messages.append({"role": "assistant", "content": response.content})

        # Check if we're done (no tool calls)
        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]

        if not tool_use_blocks:
            # Extract final text response
            text_blocks = [block.text for block in response.content if hasattr(block, "text")]
            final_review = "\n".join(text_blocks)
            print(f"\n[AGENTIC REVIEW] Completed after {iteration} iterations")
            return final_review

        # Execute tools
        print(f"[ITERATION {iteration}] Executing {len(tool_use_blocks)} tool(s)...")
        tool_results = []

        for tool_block in tool_use_blocks:
            tool_name = tool_block.name
            tool_input = tool_block.input
            tool_id = tool_block.id

            print(f"  - {tool_name}({json.dumps(tool_input, indent=2)})")

            result = execute_tool(tool_name, tool_input, context)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result
            })

        # Add tool results to messages
        messages.append({"role": "user", "content": tool_results})

        # Check stop reason
        if response.stop_reason == "end_turn":
            print("[AGENTIC REVIEW] Claude indicated end of turn")
            break

    # If we hit max iterations, ask for final summary
    if iteration >= max_iterations:
        print(f"\n[AGENTIC REVIEW] Hit max iterations ({max_iterations}), requesting final summary...")
        messages.append({
            "role": "user",
            "content": "Please provide your final code review summary now based on everything you've discovered."
        })

        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system_prompt,
            messages=messages
        )

        text_blocks = [block.text for block in response.content if hasattr(block, "text")]
        return "\n".join(text_blocks)

    return "Review completed but no final output was generated."

def main() -> None:
    event = load_event()

    if "pull_request" not in event:
        print("Not a pull_request event, skipping.")
        return

    pr = event["pull_request"]

    repo_full = os.environ.get("GITHUB_REPOSITORY")
    if not repo_full or "/" not in repo_full:
        raise RuntimeError("GITHUB_REPOSITORY is not set or invalid")

    owner, repo = repo_full.split("/")
    pr_number = pr["number"]

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise RuntimeError("GITHUB_TOKEN not set")

    # repository local path
    repo_path = os.environ.get("GITHUB_WORKSPACE", os.getcwd())

    head_sha = pr["head"]["sha"]
    base_ref = pr["base"]["ref"]
    head_ref = pr["head"]["ref"]

    files = get_pr_files_local(repo_path, base_ref, head_ref)

    if not files:
        print(f"Local git failed, falling back to GitHub API...")
        files = get_pr_files(owner, repo, pr_number, github_token)

    if not files:
        print("No changed files found, skipping.")
        return

    print(f"Found {len(files)} changed file(s)")

    review_text = run_agentic_review_with_tools(
        pr=pr,
        files=files,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        github_token=github_token,
        repo_path=repo_path,
    )

    # Determine output mode
    output_mode = os.environ.get("REVIEW_OUTPUT_MODE", "comment").lower()

    if output_mode == "log":
        print("REVIEW OUTPUT")
        print(review_text)
        return

    # Post to PR
    model_name = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
    comment = f"""###  Agentic Code Review (Claude with Tools)

**Model:** {model_name}

{review_text}

---
*This review was generated by Claude using iterative tool calling to explore the codebase, run tests, and gather context.*
"""

    post_issue_comment(owner, repo, pr_number, github_token, comment)
    print("\n✓ Review posted to PR")


if __name__ == "__main__":
    main()
