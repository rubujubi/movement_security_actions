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
