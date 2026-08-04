# Security Policy

## Supported versions

Security fixes are provided for the latest published release of Burhan Engine.

## Reporting a vulnerability

Please do not disclose security vulnerabilities in public issues or discussions.
Report them privately through
[GitHub Security Advisories](https://github.com/mwthrc23-ui/burhan-engine/security/advisories/new).

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Do not include real credentials, private source code, or sensitive
project data. You should receive an initial response within seven days.

## Isolation and sandbox policies

Burhan Engine applies several layers of protection when running proofs:

* **Docker image pinning** – all Docker images must be referenced by
  `image@sha256:<64-hex>` digest.  Images without a pinned digest are rejected
  before any container is started.
* **Network isolation** – containers run with `--network none` by default.
  Network access requires an explicit policy override.
* **Read-only source mount** – the project directory is mounted read-only inside
  the container; only a temporary copy is writable.
* **Capability dropping** – Linux capabilities are dropped in all proof
  containers.
* **Resource limits** – CPU, memory, and process limits are enforced per run.
* **Symlink and overwrite protection** – report files are written atomically.
  Writing to an existing file or through a symlink is rejected.

## External patch boundary

`verify-patch` accepts ordinary UTF-8 unified diffs without trusting whether
they came from Aider, OpenHands, Copilot, or another producer. It rejects
absolute and parent-traversal paths, secret and excluded files, links and
reparse points, binary data, renames, copies, metadata changes, file creation
or deletion, oversized input, ambiguous aliases, and context mismatches. Only
existing text files inside the temporary proof copy may change. Multi-file
writes are atomic and roll back on failure; the original project manifest is
checked again before a success verdict.

External dataset commands are evidence, not executable configuration. Burhan
never passes a raw BugsInPy `run_test.sh` or SWE-bench command to a shell. A
reproducible experiment must translate an attested command to an explicit
allowlisted argument vector, use `shell=False`, and run untrusted project code
inside the digest-pinned V2 Docker boundary.

## Secret file protection

The scanner and patcher never read, log, or transmit the contents of:

* `.env` and `.env.*` files
* `credentials.json`, `secrets.json`, `service-account.json`
* Private key files: `id_rsa`, `id_ed25519`, `*.pem`, `*.key`, `*.p12`, `*.pfx`

These files are skipped silently; a count of skipped files appears in the
analysis result but their content is never accessed.

## Memory and proof trust levels

Repair memory entries are classified by trust level:

| Level | Meaning |
|-------|---------|
| `raw_source` | Imported from external dataset; not locally verified |
| `unverified_local` | Proposed by the engine; not yet proven by tests |
| `locally_proven` | Proven by a genuine fail→pass cycle inside Docker |
| `human_reviewed` | Additionally reviewed and approved by a human operator |

The `memory-promote` gate is fail-closed: it does not accept a
`ProofResult` supplied by the user as evidence.  The engine must re-run the
proof itself.

## Optional intelligence provider

The intelligence provider subsystem is disabled by default.  When enabled:

* No raw source code is sent to external services.
* External calls require explicit `--allow-external-ai` consent.
* All LLM output is classified as `ASSUMED` trust and must be validated by
  the engine's own tools before use.
* The provider cannot override sandbox or scope policies.

