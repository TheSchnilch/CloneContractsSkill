# clone-contracts

A Claude Code skill (plus standalone CLI) for cloning verified Solidity source code
from Etherscan V2. It auto-resolves proxies to their implementation and rewrites
`@`-prefixed imports to relative paths so the cloned code is readable without
Foundry/Hardhat remappings.

Works on 20+ EVM chains (Ethereum, Arbitrum, Base, Optimism, Polygon, BSC, zkSync,
Linea, Scroll, Blast, Avalanche, …) — all via the single Etherscan V2 endpoint.

## Installation

Requirements: `python3` (>= 3.9), `git`, and an
[Etherscan API key](https://etherscan.io/apis) (free).

```bash
git clone https://github.com/<you>/clone-contracts-skill.git ~/clone-contracts-skill
cd ~/clone-contracts-skill
./setup.sh
```

`setup.sh` is idempotent — re-run it any time to repair an install or pick up
changes after `git pull`.

What it sets up:

- `~/.claude/skills/clone-contracts/` — install directory. `SKILL.md` and
  `clone_external.py` are symlinked here from your clone so updates propagate.
- `~/.claude/skills/clone-contracts/.venv` — isolated Python venv with
  `requests` + `python-dotenv`. Does not touch system Python.
- `~/.local/bin/clone-external` — wrapper command on your `PATH`.
- `~/.claude/skills/clone-contracts/.env` — stores your `ETHERSCAN_API_KEY`.
  The setup script prompts for it if it's not already in your environment.

If `~/.local/bin` is not on your `PATH`, add this to your shell rc:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Usage

```bash
clone-external [--chain <name>] [--output <dir>] <project>:<address> [<project>:<address> ...]
```

### Examples

```bash
# Ethereum mainnet — writes to ./contracts/ethereum/<project>/
clone-external \
  uniswap:0x1F98431c8aD98523631AE4a59f267346ea31F984 \
  chainlink:0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419

# Arbitrum
clone-external --chain arbitrum \
  gmx:0x489ee077994B6658eAfA855C308275EAd8097C4A

# Custom output root
clone-external --chain base --output ./audit-targets \
  morpho:0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
```

Run `clone-external --help` for the full option list and supported chains.

### Output layout

```
<output>/<chain>/<project>/
├── contracts/     # main contracts (.sol)
├── interfaces/    # interfaces (.sol)
├── lib/           # libraries + npm-style @deps rewritten to relative paths
```

## How `ETHERSCAN_API_KEY` is resolved

Priority order (first hit wins):

1. Process environment (`export ETHERSCAN_API_KEY=...`)
2. `.env` in the current working directory
3. `~/.claude/skills/clone-contracts/.env` (written by `setup.sh`)

## Using as a Claude Code skill

Once installed, Claude Code automatically discovers the skill at
`~/.claude/skills/clone-contracts/SKILL.md`. Agents that encounter an external
integration callsite (breadth, depth, recon) can invoke the `clone-external`
command directly — no per-project configuration needed.

## Updating

```bash
cd ~/clone-contracts-skill
git pull
./setup.sh   # only needed if dependencies changed; safe to skip otherwise
```

Because `SKILL.md` and `clone_external.py` are symlinked, `git pull` alone is
enough for content updates. Re-run `setup.sh` if the dependency list changes.

## Uninstall

```bash
rm ~/.local/bin/clone-external
rm -rf ~/.claude/skills/clone-contracts
```

Then delete your local clone of this repo if you no longer want it.
