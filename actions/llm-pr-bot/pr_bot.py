import os
import json
import anthropic
from utils import (
    load_event,
    get_pr_files,
    get_file_content,
    post_issue_comment,
    load_language_prompt,
    build_diff,
    build_single_file_diff,
)


def build_instructions() -> str:
    language = os.environ.get("LANGUAGE", "generic")
    review_mode = os.environ.get("REVIEW_MODE", "simple")

    # Map review modes to prompt folders
    # TODO: Add prompts for simple and agentic modes later
    # For now, they'll use the hardcoded fallback or you can add those folders later
    mode_map = {
        "simple": "simple",
        "agentic": "agentic",
        "agentic_tools": "agentic_tool"
    }

    mode = mode_map.get(review_mode)
    base = load_language_prompt(language, mode)
    extra = os.environ.get("EXTRA_INSTRUCTIONS", "").strip()

    if extra:
        return base + "\n\n" + extra
    return base


# call llm and return response text with custom system prompt
def call_llm(prompt: str, system_prompt: str = None) -> str:
    """Call Claude with instructions + user prompt."""
    if system_prompt is None:
        system_prompt = build_instructions()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-latest")
    client = anthropic.Anthropic(api_key=api_key)

    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )

    texts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)
    return "\n".join(texts).strip()


def build_simple_review_prompt() -> str:
    """Build the system prompt for simple review mode."""
    language = os.environ.get("LANGUAGE", "generic")
    mode = "simple"
    base = load_language_prompt(language, mode)
    extra = os.environ.get("EXTRA_INSTRUCTIONS", "").strip()

    if extra:
        return base + "\n\n" + extra
    return base


def run_simple_review(pr: dict, files: list[dict]) -> str:
    diff = build_diff(files)
    system_prompt = build_simple_review_prompt()

    prompt = f"""
Title: {pr['title']}
Description: {pr.get('body') or '[no description provided]'}

Here is the diff:

{diff}
"""
    return call_llm(prompt, system_prompt)



# Agentic mode: plan -> per-file -> final summary
def build_agentic_review_prompt() -> str:
    """Build the system prompt for agentic review mode."""
    language = os.environ.get("LANGUAGE", "generic")
    mode = "agentic"
    base = load_language_prompt(language, mode)
    extra = os.environ.get("EXTRA_INSTRUCTIONS", "").strip()

    if extra:
        return base + "\n\n" + extra
    return base


def load_agentic_template(template_name: str) -> str:
    """Load a template file from prompts/agentic/ directory."""
    base_dir = os.path.dirname(__file__)
    template_path = os.path.join(base_dir, "prompts", "agentic", f"{template_name}.txt")

    if os.path.exists(template_path):
        with open(template_path, "r") as f:
            return f.read().strip()
    return ""


def run_agentic_review(
    pr: dict,
    files: list[dict],
    owner: str,
    repo: str,
    head_sha: str,
    github_token: str,
) -> str:
    system_prompt = build_agentic_review_prompt()

    # 1. Planning step
    full_diff = build_diff(files, limit=8000)
    planning_instructions = load_agentic_template("planning_template")

    planning_prompt = f"""
Here is the PR information:

Title: {pr['title']}
Description: {pr.get('body') or '[no description provided]'}

Here is a trimmed global diff:

{full_diff}

{planning_instructions}
"""

    plan_raw = call_llm(planning_prompt, system_prompt)

    try:
        plan = json.loads(plan_raw)
    except json.JSONDecodeError:
        print("Invalid plan JSON, falling back to simple review.")
        return run_simple_review(pr, files)

    summary = plan.get("summary", "")
    global_risks = plan.get("global_risks", [])
    focus_files = plan.get("focus_files", [])

    # Build filename index
    file_by_name = {f["filename"]: f for f in files}

    per_file_notes = []
    max_files = 5

    # 2. Per-file deep analysis
    for filename in focus_files[:max_files]:
        f = file_by_name.get(filename)
        if not f:
            continue

        file_diff = build_single_file_diff(f)
        file_content = get_file_content(
            owner=owner,
            repo=repo,
            ref=head_sha,
            path=filename,
            github_token=github_token,
        )

        if not file_diff and not file_content:
            continue

        per_file_instructions = load_agentic_template("per_file_template")
        per_file_prompt = f"""
File: {filename}
PR Title: {pr['title']}

Diff:
{file_diff or "[no diff]"}

Current full file content (may be truncated):

{per_file_instructions}
"""

        analysis = call_llm(per_file_prompt, system_prompt)

        per_file_notes.append({
            "filename": filename,
            "analysis": analysis,
        })

    # 3. Final synthesis
    per_file_md = ""
    for n in per_file_notes:
        per_file_md += f"\n\n---\n\n### File: `{n['filename']}`\n\n{n['analysis']}\n"

    final_instructions = load_agentic_template("final_template")
    final_prompt = f"""
Review plan:
{json.dumps(plan, indent=2)}

Per-file deep analysis:
{per_file_md or "[No per-file analysis]"}

{final_instructions}
"""

    return call_llm(final_prompt, system_prompt)


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

    review_mode = os.environ.get("REVIEW_MODE", "simple").lower()
    output_mode = os.environ.get("REVIEW_OUTPUT_MODE", "comment").lower()

    files = get_pr_files(owner, repo, pr_number, github_token)
    if not files:
        print("No changed files, skipping.")
        return

    head_sha = pr["head"]["sha"]

    # Choose review strategy
    if review_mode == "agentic":
        print("Running agentic review...")
        review_text = run_agentic_review(
            pr=pr,
            files=files,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            github_token=github_token,
        )
    else:
        print("Running simple review...")
        review_text = run_simple_review(pr, files)

    # Output handling
    if output_mode == "log":
        print("====== LLM REVIEW (log only) ======")
        print(review_text)
        print("====== END ======")
        return

    # Post to PR
    model_name = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-latest")
    comment = f"### 🤖 Claude Review (model: {model_name}, mode: {review_mode})\n\n{review_text}"
    post_issue_comment(owner, repo, pr_number, github_token, comment)
    print("Review posted to PR.")


if __name__ == "__main__":
    main()
