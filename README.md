# movement_security_actions

This is a collection of `.yaml` files used to set up Github Actions for code audit, currently supporting:

- Smart contracts in move
- Smart contracts in Solidity
- Rust-based infrastructure implementation

The collection contains the following approaches:

- Static Analyzer (semgrep)
- Fuzzer (aptos-fuzz)
- LLM review (WIP)

## Smart Contracts

### Move

#### 1. Semgrep Move Scan


Automated security scanning for Move smart contracts using Semgrep with Aptos Move security rules. 


##### Triggers

- **Push events:** Runs on pushes to `main` branch when `.move` files are modified
- **Pull requests:** Runs when `.move` files are changed in any PR
- **Manual trigger:** Can be manually triggered via `workflow_dispatch` by users with write access

##### Steps

1. **Checks out the repository** and the [aptos-labs/semgrep-move-rules](https://github.com/aptos-labs/semgrep-move-rules) repository
2. **Installs Semgrep** security scanner
3. **Scans all Move files** using Aptos security rules with dataflow analysis enabled
4. **Uploads results** to:
   - GitHub Security tab (Code Scanning alerts)
   - Workflow artifacts (downloadable SARIF file)

##### Reference

- Semgrep aptos-move rules repo https://github.com/aptos-labs/semgrep-move-rules
- Medium tutorial for semgrep on move https://medium.com/aptoslabs/semgrep-support-for-move-on-aptos-39f9109f2266

### Solidity

#### 1. Semgrep Solidity Scan

Automated security scanning for Solidity smart contracts using Semgrep with community Solidity rulepacks.

##### Triggers

- **Push events:** Runs on pushes to `main` branch when `.sol` files are modified
- **Pull requests:** Runs when `.sol` files are changed in any PR
- **Manual trigger:** Can be manually triggered via `workflow_dispatch` by users with write access

##### Steps

1. **Checks out the repository** containing Solidity contracts
2. **Runs Semgrep** inside the official Semgrep container using the Solidity rulepack (`r/solidity`) and auto-detected rules, outputting SARIF
3. **Uploads results** to:
   - GitHub Security tab (Code Scanning alerts)
   - Workflow artifacts (downloadable SARIF file)

##### Reference

- Semgrep Solidity rules https://semgrep.dev/explore?lang=solidity

## Rust Infrastructure

### 1. Aptos Core Fuzzer

Continuous fuzzing of Aptos Core Rust components using `cargo fuzz` across all configured targets.

#### Triggers

- **Push events:** Runs on pushes to `main`
- **Pull requests:** Runs on PRs targeting `main`
- **Manual trigger:** `workflow_dispatch` accepts optional `duration` (seconds per target) and `targets` (comma-separated list) inputs

#### Steps

1. **Lists fuzz targets** from `testsuite/fuzzer/fuzz/Cargo.toml` or uses manually supplied targets
2. **Installs Rust nightly** toolchain and build dependencies
3. **Builds fuzz targets** with required features and AddressSanitizer enabled
4. **Runs cargo-fuzz** for each target with timeouts and optional pre-seeded corpora
5. **Uploads artifacts** including crashes, corpora, and fuzz logs for triage

## LLM Audit

[Report from Anthropic](https://red.anthropic.com/2025/smart-contracts/) provided an example of LLM's capability in security reviews in Ethereum smart contracts. The prompt used in the experiment is also in the post. In summary, they built an agent running in sandbox, with accessability to the following tools: foundry, uniswap-smart-path, slither, etc. The average cost per agent run is $1.22 (calculated with gpt-5), the agent is given a time limit of 1 hour to finish the work.

Currently our target for integrating LLM tool into the audit process is to have LLM as an assistance to replace some manual inspection work. We want to use LLM alongside development. (i.e. we are dealing with contracts that are WIP, not contracts that are already deployed. )

In that case we care more about knowing the existence of potential attack vector, instead to build a fully automated pipeline (since we have human in the loop)

Therefore, two lines of effort will be provided here, leveraging performance and the amount of human in the loop involved.

- A set of dedicated prompts to be used in Movement development context. 
   - This can be used as a template for any LLM interaction and it serves as some prompt engineering effort that enforces standard and detailed information to be collected from LLM output.

- An automated pipeline that will be trigged by PR and automatically fetch relevant context based on file diffs.

### 1. LLM PR Review Bot

Automated code review for Pull Requests using LLMs (currently only support claude).

#### Triggers

- **Pull requests:** Runs when PRs are opened or synchronized

#### Steps

##### Agentic tools

1. **Checks out the repository** with full git history
2. **Loads language-specific prompt** from `prompts/<language>.txt` (e.g., `move.txt` for Move security audits)
3. **Analyzes PR changes** using one of three modes:
   - **Simple:** One-shot review of PR diff
   - **Agentic:** Multi-step review with planning and synthesis
   - **Agentic Tools:** Claude iteratively explores codebase using tools (read files, search code, list directories)
4. **Outputs results** either as PR comment or as log output (configurable via `review_output_mode`)

#### Setup

**1. Set Secret Variables:**

Repository -> Settings -> Security -> Secrets and variables -> Actions -> Secrets

Add secret:
```
ANTHROPIC_API_KEY=your-api-key-here
```

**2. Set Repository Variables:**

Repository -> Settings -> Security -> Secrets and variables -> Actions -> Variables

Add variable:
```
# Choose the claude model you want to use
CLAUDE_MODEL=claude-sonnet-4-5-20250929
```

**3. Configure Workflow:**

Example set up in `.github/workflows/llm-pr-review.yml`:



**Key Parameters:**
- `language`: Loads prompt from `prompts/<language>.txt` (e.g., `move`, `rust`, `solidity`, `generic`)
- `review_mode`: `simple`, `agentic`, or `agentic_tools`
- `extra_instructions`: Additional custom review instructions
- `llm_provider`: `claude` or `gpt` (Currently only support claude)
- `claude_model`: Claude model name (recommended to use `${{ vars.CLAUDE_MODEL }}` repository variable)
- `openai_model`: OpenAI model name (if using GPT, also recommended to use repository variable)

#### Language-Specific Prompts

The bot supports custom security audit templates via the `prompts/` directory, currently providing:

Agentic tool:

- `prompts/agentic_tool/move.txt` - Comprehensive Aptos Move security audit prompt

- `prompts/agentic_tool/generic.txt` - Default general code review prompt

Create custom prompts for other languages by adding `prompts/<review_mode>/<language>.txt` and setting `language: <language>`.

