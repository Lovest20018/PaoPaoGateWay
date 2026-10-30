# Merge upstream October update implementation plan

> **For agentic workers:** Use executing-plans to implement the tasks below, with test-driven-development for compatibility fixes and verification-before-completion for final claims.

**Goal:** Merge upstream main into the existing Debian/HNM fork while preserving its deployment and observer behavior.

**Architecture:** Record the existing customization as a recoverable baseline, merge the upstream commit with its dependency lockfile, resolve integration conflicts, and rebuild against the new dashboard. Validate in an isolated Linux builder before committing the final merge. Running instances are outside this merge operation.

**Tech Stack:** Rust, Python, POSIX shell, precompiled Svelte dashboard, Debian/systemd.

**Spec:** `docs/validation/upstream-merge-assessment-2026-10-30.md`; user approved merging upstream after reading the assessment.

## Global Constraints

- Preserve Debian mount/network isolation, authenticated private explicit proxy defaults, and disabled Cloudflared.
- Preserve HNM identity, observations, source/policy/effective revisions, and safe hot reload locking.
- Preserve current full Clash PPsub edit/save/noop/rollback behavior and advanced feature metadata.
- Keep upstream Cargo.lock paired with its manifests and new modules.
- Do not deploy, push, or change live subscription configuration as part of this merge.
- Work in the existing user workspace; recovery commit and ignored archive protect the current customization.

## Review Focus

- Debian production configuration must not enable newly added RPS host tuning.
- Routable IPv6 and IP literals must retain their destination while FakeIP hostnames use upstream multi-address retry.
- New connection and logging paths must not discard HNM evidence or expose subscription credentials.
- Updated compiled dashboard symbols must still invoke the correct importer/exporter and mount cleanup.
- Updated shared connection JSON types and UDP budgets must coexist with safe reload identity and revision updates.

### Task 1: Preserve baseline and merge core

**Files:** `FILES/usr/bin/ppg.sh`; `sniffbox/crates/sniffbox/src/{production,outbound,runtime,engine,lib}.rs`; all upstream changed files.

**Interfaces:** Retain `SharedState.configuration_gate`, `integration::Identity`, evidence records. Consume upstream `UdpBudget`, `dest_routable`, `resolve_all_v4`, and `ConnectionsSource -> Arc<String>`.

- [x] Save ignored source archive with checksums and commit existing customizations; retain recovery reference.
- [x] Fetch and merge official main without committing; resolve four overlapping files.
- [x] Add regression coverage for Debian tuning and destination handling; run Linux Rust tests with the locked dependencies.

### Task 2: Adapt dashboard integration

**Files:** `scripts/prepare_dashboard.py`; `tests/test_dashboard.py`; browser fixture under `scripts/` if required.

**Interfaces:** Keep `prepare_dashboard(output)` and `window.ppgwPpsub` load/mount/import/export callbacks.

- [x] Reproduce packaging hook failure against the shipped upstream bundle in a test.
- [x] Update exact reviewed load, mount and identity anchors; keep fail-closed behavior for unknown bundles.
- [x] Run Python suite, API contract, JavaScript syntax and browser editor load/export/noop roundtrip on synthetic configuration.

### Task 3: Verify and finish merge

**Files:** `docs/validation/upstream-merge-2026-10-30.md` and build/validation artifacts under ignored `dist/`.

**Interfaces:** Final Git merge commit has local snapshot and official upstream as parents; release staging uses the normal packager.

- [x] Build release in an isolated Linux container; run shell syntax and package manifest checks.
- [x] Request an independent review of integration changes and address important findings.
- [x] Confirm no conflict markers, clean diff, upstream ancestry and preserved customized files.
- [x] Commit the merge and report commit, checks and any remaining validation limitations.
