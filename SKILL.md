---
name: clone-contracts
description: Clone verified source code for external contracts this project integrates with. Use when auditing an external integration, tracing a cross-contract call, or when you need the source of a contract that is not in the main codebase.
---

# EXTERNAL_CONTRACTS

> **Trigger**: Agent needs the source of an external contract address.
> **Inject Into**: Breadth agents, depth agents, recon agents — any agent that encounters an external integration callsite.

---

## Clone the external source

Run the `clone-external` command from any directory. Supply one or more `project:address` pairs.
The command calls the Etherscan V2 API, auto-resolves proxies to their implementation, and
rewrites all `@`-prefixed import paths to relative paths so the files are readable without
remappings.

```bash
clone-external [--chain <name>] [--output <dir>] <project>:<address> [<project>:<address> ...]
```

- `--chain` selects the EVM chain. Defaults to `ethereum`. Supported names:
  `ethereum`, `arbitrum`, `arbitrum-nova`, `optimism`, `base`, `polygon`, `polygon-zkevm`,
  `bsc`, `avalanche`, `fantom`, `gnosis`, `linea`, `scroll`, `zksync`, `blast`, `mantle`,
  `celo`, `moonbeam`, `moonriver`, `sepolia`, `holesky`. All chains use the same
  Etherscan V2 endpoint and the same `ETHERSCAN_API_KEY`.
- `--output` / `-o` sets the output root. Defaults to `./contracts` in the current working
  directory. Contracts always land in `<output>/<chain>/<project>/` regardless of the root.
- `<project>` is a short name you choose (e.g. `uniswap`, `aave`, `chainlink`). Contracts from the
  same protocol should share the same project name so they land in the same folder.
- `<address>` is the checksummed or lowercase address on the selected chain.
- If the address is a proxy the command prints `→ Proxy detected; fetching implementation <addr>`
  and writes the implementation source, not the proxy.
- The command handles adding new addresses to an existing project folder, so run it
  unconditionally without pre-checking whether the code is already present.

`ETHERSCAN_API_KEY` is read from (in priority order) the process environment, a `.env` in the
current working directory, then `~/.claude/skills/clone-contracts/.env`. The `<chain>` segment
keeps the same project name separated across chains
(e.g. `contracts/ethereum/uniswap/` vs `contracts/arbitrum/uniswap/`).

**Examples:**
```bash
# Ethereum mainnet (default output: ./contracts/ethereum/<project>/)
clone-external \
  uniswap:0x1F98431c8aD98523631AE4a59f267346ea31F984 \
  chainlink:0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419

# Arbitrum
clone-external --chain arbitrum \
  gmx:0x489ee077994B6658eAfA855C308275EAd8097C4A

# Custom output root — files land in ./audit-targets/base/morpho/
clone-external --chain base --output ./audit-targets \
  morpho:0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
```

## Installation

See `README.md` in this repository — run `./setup.sh` once after cloning.
