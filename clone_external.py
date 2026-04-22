#!/usr/bin/env python3
"""
clone_external.py - Clone external smart contract source code from Etherscan.

Usage (after running ./setup.sh):
    clone-external [--chain <name>] [--output <dir>] ethena:0xABC... maker:0xDEF...

    # Default chain is ethereum. Use --chain to target another EVM chain
    # supported by Etherscan V2 (arbitrum, base, optimism, polygon, bsc, ...).
    # Default output root is ./contracts (relative to the current working
    # directory). Use --output to write elsewhere.

Output:
    <output>/<chain>/<project>/   (default <output> is ./contracts)
        contracts/   - main contracts (.sol)
        interfaces/  - interfaces (.sol)
        lib/         - libraries (.sol)
        flattened/   - original flat file when source was monolithic
        metadata/    - Etherscan metadata + ABI per address (.json)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """
    Load ETHERSCAN_API_KEY from the first source that has it.
    Priority: real environment > CWD/.env > ~/.claude/skills/clone-contracts/.env.
    `override=False` keeps higher-priority sources from being clobbered.
    """
    for candidate in (
        Path.cwd() / ".env",
        Path.home() / ".claude" / "skills" / "clone-contracts" / ".env",
    ):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


_load_env()

ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
DEFAULT_OUTPUT_DIR = Path.cwd() / "contracts"

# Seconds to wait between Etherscan API calls (free tier: 5 req/s)
API_DELAY = 0.25

# EVM chains supported by Etherscan V2 (same endpoint + API key, different chainid).
# Keys are the CLI-facing names; values are Etherscan chain IDs.
CHAINS: dict[str, int] = {
    "ethereum": 1,
    "optimism": 10,
    "bsc": 56,
    "polygon": 137,
    "polygon-zkevm": 1101,
    "base": 8453,
    "arbitrum": 42161,
    "arbitrum-nova": 42170,
    "avalanche": 43114,
    "fantom": 250,
    "gnosis": 100,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324,
    "blast": 81457,
    "mantle": 5000,
    "celo": 42220,
    "moonbeam": 1284,
    "moonriver": 1285,
    "sepolia": 11155111,
    "holesky": 17000,
}

# ---------------------------------------------------------------------------
# Etherscan helpers
# ---------------------------------------------------------------------------

def etherscan_get(params: dict, chain_id: int) -> dict:
    """Execute an Etherscan API call and return the parsed JSON."""
    if not ETHERSCAN_API_KEY:
        sys.exit("ERROR: ETHERSCAN_API_KEY environment variable not set.")
    params["apikey"] = ETHERSCAN_API_KEY
    params["chainid"] = chain_id
    resp = requests.get(ETHERSCAN_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "0" and data.get("result") not in (
        "Contract source code not verified",
        "Max rate limit reached",
    ):
        # status=0 with a real error message
        pass  # we'll let callers inspect result
    return data


def fetch_source(address: str, chain_id: int) -> dict:
    """
    Fetch getSourceCode for an address on the given chain.
    Returns the first result dict from Etherscan (or raises on failure).
    """
    time.sleep(API_DELAY)
    data = etherscan_get({
        "module": "contract",
        "action": "getsourcecode",
        "address": address,
    }, chain_id)
    results = data.get("result", [])
    if not results or results[0].get("SourceCode") == "":
        raise ValueError(f"No verified source for {address}: {data.get('result')}")
    return results[0]


def resolve_proxy(address: str, record: dict, chain_id: int) -> tuple[str, dict]:
    """
    If `record` is a proxy (Implementation field is non-empty),
    fetch the implementation's source and return (impl_address, impl_record).
    Otherwise return the original (address, record).
    """
    impl = record.get("Implementation", "").strip()
    if impl and impl != "0x0000000000000000000000000000000000000000":
        print(f"  → Proxy detected; fetching implementation {impl}")
        impl_record = fetch_source(impl, chain_id)
        return impl, impl_record
    return address, record


# ---------------------------------------------------------------------------
# Source-code parsing
# ---------------------------------------------------------------------------

def parse_sources(raw_source: str, contract_name: str) -> tuple[dict[str, str], bool]:
    """
    Parse Etherscan's SourceCode field into a {virtual_path: content} mapping.

    Returns (files_dict, is_monolithic) where is_monolithic=True means the
    original was a single concatenated Solidity file that we split ourselves.

    Etherscan returns one of three formats:
      1. Plain Solidity string (monolithic)
      2. Standard JSON Input: {"language":"Solidity","sources":{...},...}
      3. Double-braced wrapper: {{"language":"Solidity","sources":{...},...}}
    """
    stripped = raw_source.strip()

    # Format 3: double-brace wrapper → strip outer braces and parse as JSON
    if stripped.startswith("{{"):
        stripped = stripped[1:-1]

    # Format 2 & 3: JSON object
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            # Malformed JSON — treat as monolithic
            return _split_monolithic(raw_source, contract_name), True

        # Standard JSON Input has a "sources" key
        sources = obj.get("sources", {})
        if sources:
            files: dict[str, str] = {}
            for path, entry in sources.items():
                content = entry.get("content", "")
                if content:
                    files[path] = content
            if files:
                return files, False

        # Some older formats wrap the source directly
        if "content" in obj:
            return _split_monolithic(obj["content"], contract_name), True

    # Format 1: plain Solidity
    return _split_monolithic(raw_source, contract_name), True


# Regex patterns for top-level Solidity declarations
_DECL_RE = re.compile(
    r"^(?:abstract\s+)?(?:contract|interface|library)\s+(\w+)",
    re.MULTILINE,
)
_SPDX_RE = re.compile(r"^// SPDX-License-Identifier:.*$", re.MULTILINE)
_PRAGMA_RE = re.compile(r"^pragma\s+solidity\s+[^;]+;", re.MULTILINE)


def _split_monolithic(source: str, main_name: str) -> dict[str, str]:
    """
    Split a monolithic Solidity file into individual files, one per
    top-level contract/interface/library declaration.

    Each split file gets the SPDX and pragma from the original header.
    """
    # Extract shared header (SPDX + pragma)
    header_lines = []
    spdx = _SPDX_RE.search(source)
    if spdx:
        header_lines.append(spdx.group(0))
    pragma = _PRAGMA_RE.search(source)
    if pragma:
        header_lines.append(pragma.group(0))
    header = "\n".join(header_lines) + ("\n\n" if header_lines else "")

    # Find all top-level declarations and their positions
    matches = list(_DECL_RE.finditer(source))
    if len(matches) <= 1:
        # Nothing to split — return as single file
        kind = _decl_kind(source, main_name)
        return {_canonical_path(main_name, kind): source}

    files: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        block = source[start:end].strip()
        name = m.group(1)

        # Determine declaration kind from the matched line
        line = m.group(0)
        if "interface" in line:
            kind = "interface"
        elif "library" in line:
            kind = "lib"
        else:
            kind = "contract"

        content = header + block + "\n"
        files[_canonical_path(name, kind)] = content

    return files


def _decl_kind(source: str, name: str) -> str:
    m = re.search(
        r"^(?:abstract\s+)?(contract|interface|library)\s+" + re.escape(name),
        source,
        re.MULTILINE,
    )
    if m:
        k = m.group(1)
        return "interface" if k == "interface" else ("lib" if k == "library" else "contract")
    return "contract"


def _canonical_path(name: str, kind: str) -> str:
    """Map a declaration name + kind to a canonical relative path."""
    if kind == "interface":
        return f"interfaces/{name}.sol"
    if kind == "lib":
        return f"lib/{name}.sol"
    return f"contracts/{name}.sol"


def _normalize_path(rel_path: str) -> str:
    """
    Normalize a path from Etherscan's source map into a plain relative path
    with no @ notation.

    Examples:
      @openzeppelin/contracts/token/ERC20/ERC20.sol
        → lib/openzeppelin-contracts/contracts/token/ERC20/ERC20.sol
      contracts/Foo.sol → contracts/Foo.sol  (unchanged)
    """
    path = rel_path.strip().lstrip("/")
    if path.startswith("@"):
        path = "lib/" + path[1:]
    return path


# Matches the quoted path inside any Solidity import statement.
_IMPORT_RE = re.compile(r"""(import\b[^"']*)(["'])([^"']+)\2""")


def _build_suffix_index(dest_map: dict[str, Path], project_dir: Path) -> dict[str, str]:
    """
    Build a mapping from path suffixes to project-relative paths.

    For a file at lib/openzeppelin-contracts/contracts/access/AccessControl.sol
    we index every trailing suffix:
      AccessControl.sol                               → lib/openzeppelin-contracts/...
      access/AccessControl.sol                        → lib/openzeppelin-contracts/...
      contracts/access/AccessControl.sol              → lib/openzeppelin-contracts/...
      ...

    This lets us resolve @openzeppelin/contracts/access/AccessControl.sol by
    looking up the portion after the package name (contracts/access/AccessControl.sol).
    """
    index: dict[str, str] = {}
    for dest in dest_map.values():
        parts = dest.relative_to(project_dir).parts
        proj_rel = "/".join(parts)
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix not in index:  # first/most-specific match wins
                index[suffix] = proj_rel
    return index


def _rewrite_imports(
    content: str,
    file_path: Path,
    project_dir: Path,
    suffix_index: dict[str, str],
) -> str:
    """
    Rewrite non-relative import paths inside a Solidity file to relative paths,
    using a suffix index built from the actual files written to disk.

    Handles three styles:
      @openzeppelin/contracts/access/AccessControl.sol  (npm/@ style)
      openzeppelin-contracts/contracts/access/AccessControl.sol  (Foundry bare style)
      solmate/utils/ReentrancyGuard.sol  (Foundry bare style, lib name = package name)
    All are resolved by looking up the path (or the within-package suffix) in the
    suffix index, which maps every trailing path segment to its actual location under lib/.
    """
    file_dir = file_path.parent

    def replace(m: re.Match) -> str:
        prefix, quote, imp_path = m.group(1), m.group(2), m.group(3)

        # Leave relative imports alone
        if imp_path.startswith("."):
            return m.group(0)

        if imp_path.startswith("@"):
            # @pkg/rest  →  strip @pkg, look up rest
            after_at = imp_path[1:]
            slash = after_at.find("/")
            within_pkg = after_at[slash + 1:] if slash != -1 else after_at
            proj_rel = suffix_index.get(within_pkg)
            if proj_rel is None:
                proj_rel = "lib/" + after_at
        else:
            # Bare Foundry-style: solmate/utils/Foo.sol or openzeppelin-contracts/contracts/Foo.sol
            # The full path is itself a suffix of lib/<pkg>/...
            proj_rel = suffix_index.get(imp_path)
            if proj_rel is None:
                # Also try stripping the first path segment (pkg name) as within-pkg lookup
                slash = imp_path.find("/")
                within_pkg = imp_path[slash + 1:] if slash != -1 else imp_path
                proj_rel = suffix_index.get(within_pkg)
            if proj_rel is None:
                # Nothing found — leave unchanged
                return m.group(0)

        target = project_dir / proj_rel
        try:
            rel = os.path.relpath(target, file_dir)
        except ValueError:
            return m.group(0)
        rel = rel.replace(os.sep, "/")
        if not rel.startswith("."):
            rel = "./" + rel
        return f"{prefix}{quote}{rel}{quote}"

    return _IMPORT_RE.sub(replace, content)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_project_files(
    project_dir: Path,
    address: str,
    resolved_address: str,
    record: dict,
    files: dict[str, str],
) -> None:
    """Write source files for one contract into project_dir."""
    contract_name = record.get("ContractName", "Unknown")
    is_proxy = resolved_address != address

    # First pass: determine final destination paths
    dest_map: dict[str, Path] = {}  # original rel_path → dest Path
    for rel_path, content in files.items():
        clean = _normalize_path(rel_path)
        if "/" not in clean:
            clean = _canonical_path(Path(clean).stem, _decl_kind(content, Path(clean).stem))
        dest_map[rel_path] = project_dir / clean

    # Build suffix index from the full dest_map so import rewriting can find
    # the correct lib/ path for any @package import.
    suffix_index = _build_suffix_index(dest_map, project_dir)

    # Second pass: write files with rewritten imports
    written: list[Path] = []
    for rel_path, content in files.items():
        dest = dest_map[rel_path]
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = _rewrite_imports(content, dest, project_dir, suffix_index)
        dest.write_text(content, encoding="utf-8")
        written.append(dest.relative_to(project_dir))

    print(f"    ✓ {contract_name} ({resolved_address})")
    print(f"      {len(written)} file(s) written: {', '.join(str(p) for p in written[:5])}"
          + (" …" if len(written) > 5 else ""))
    if is_proxy:
        print(f"      (proxy {address} → impl {resolved_address})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def clone_project(
    project_name: str,
    chain_name: str,
    chain_id: int,
    output_root: Path,
    entries: list[tuple[str, str]],
) -> None:
    project_dir = output_root / chain_name / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[{chain_name}/{project_name}]")

    for address, label in entries:
        print(f"  Fetching {label} ({address}) …")
        try:
            record = fetch_source(address, chain_id)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        resolved_address, resolved_record = resolve_proxy(address, record, chain_id)

        files, _ = parse_sources(
            resolved_record.get("SourceCode", ""),
            resolved_record.get("ContractName", label),
        )

        write_project_files(
            project_dir=project_dir,
            address=address,
            resolved_address=resolved_address,
            record=resolved_record,
            files=files,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clone verified contract source code from Etherscan V2.",
    )
    parser.add_argument(
        "--chain",
        default="ethereum",
        choices=sorted(CHAINS.keys()),
        help="EVM chain to query (default: ethereum). All chains use the same "
             "Etherscan V2 endpoint and API key.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="DIR",
        help=f"Output root directory (default: {DEFAULT_OUTPUT_DIR}). "
             "Contracts are written to <output>/<chain>/<project>/. Relative "
             "paths resolve against the current working directory.",
    )
    parser.add_argument(
        "pairs",
        nargs="+",
        metavar="project:address",
        help="One or more project:address pairs. Addresses in the same project "
             "land in the same folder.",
    )
    args = parser.parse_args()

    chain_id = CHAINS[args.chain]
    output_root = Path(args.output).resolve() if args.output else DEFAULT_OUTPUT_DIR

    work: dict[str, list[tuple[str, str]]] = {}
    for arg in args.pairs:
        if ":" not in arg:
            sys.exit(f"ERROR: expected project:address, got '{arg}'")
        project_name, address = arg.split(":", 1)
        work.setdefault(project_name, []).append((address, address))

    for project_name, entries in work.items():
        clone_project(project_name, args.chain, chain_id, output_root, entries)

    print("\nDone.")


if __name__ == "__main__":
    main()
