# Goal 03A unreconciled candidate checkpoint manifest

This document records the working-tree candidate preserved on
`codex/goal02-unreconciled`. It is an audit artifact for Goal 03A, not a release
claim and not evidence that the candidate is compatible with Goal 01.

## Freeze identity

- Baseline and current `main`: `a6d809ffb60a61c29380c04eebbbb134c7ddef9c`
- Remote baseline: `origin/main` at the same commit when the freeze started
- Checkpoint branch: `codex/goal02-unreconciled`
- Checkpoint commit: the commit containing this manifest (`self`); record the
  resolved commit ID in the Goal 03 reconciliation report
- Freeze date: 2026-07-17 (Asia/Shanghai)
- Candidate inventory before this audit artifact: 115 paths: 49 modified, 25
  deleted, and 41 untracked
- Tracked diff before this audit artifact: 74 files, 1,801 insertions and 11,741
  deletions
- Protected roadmap ownership: all changes below under
  `docs/goals/agent_native/` were supplied by the project manager and were
  inventoried without editing them

The manifest itself is necessarily excluded from its own pre-existing-candidate
hash inventory. Every other untracked source, migration, test, script, and
documentation file is hashed below.

## Pre-freeze evidence

- WSL full suite: `901 passed in 30.52s`
- `vibe doctor --json`: 4 `ok`, 8 `warn`, 0 `fail`
- `vibe capabilities --json`: 19 registered capabilities
- Offline dry run: exit 0, task status `dry_run`, terminal event
  `dry_run_completed`, 2 evidence IDs
- `git diff --check`: no whitespace errors; only two existing CRLF-to-LF
  conversion warnings for test files

These results describe the unreconciled candidate only. They do not replace the
Goal 03B compatibility matrix or the Goal 03C WSL, D-Bus, upgrade, crash, and
rollback gates.

## Migration freeze hashes

| Artifact | SHA-256 | Meaning |
|---|---|---|
| `a6d809f:migrations/versions/0001_core_foundation.py` | `82794445a47ac649959b9a8edb248ded6e9f082a5ad3a7fd1c4bb398bcb2275c` | Original Goal 01 migration blob |
| `migrations/versions/0001_core_foundation.py` | `2e680152dd5652af8bb522864bcba7e98037229738e29ab1b395209d7da25b35` | Goal 02 candidate freeze |
| `migrations/versions/0002_durable_task_engine.py` | `4c5fce1e573732a659edc82b7457d405a66a848243bed2438a349cd3e40f78f6` | Goal 02 candidate freeze |
| `migrations/versions/0003_repair_durable_task_semantics.py` | `383ed1772b36dcc764fea39ab4b88991f9912cf189f8a9da2b931187abfed70a` | Goal 02 candidate freeze |
| `migrations/versions/0004_goal_contract_version_index.py` | `b1171100fd8df34b4c133c9986f1f083c3c6880781c2033173dcff08835dffea` | Goal 02 candidate freeze |

The reconciliation branch must document the accepted historical-migration
rewrite in an ADR, make later migrations self-contained, prove Goal 01 database
upgrade behavior, and treat the resulting committed migration hashes as
immutable.

## Inventory legend

- Status: `M` modified, `D` deleted, `U` untracked.
- Source: `g02` Goal 02 candidate; `pm` project-manager roadmap update.
- Use/owner fields identify the reason the path is preserved and its reviewing
  owner. `-` means that SHA-256 is not required for a tracked path because its
  baseline and candidate content are both addressable through Git.

```text
status path sha256 source use owner
M README.md - g02 project-doc docs
M architecture_baseline.json - g02 project-config core
M docs/README.md - g02 project-doc docs
M docs/architecture/capability_registry.md - g02 project-doc docs
M docs/architecture/core_foundation.md - g02 project-doc docs
M docs/architecture/current_status.md - g02 project-doc docs
M docs/architecture/runtime_convergence.md - g02 project-doc docs
M docs/architecture_completion_final_audit.md - g02 project-doc docs
M docs/goals/agent_native/02_durable_task_engine.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/03_model_gateway_and_secret_broker.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/04_machine_state_and_context_routing.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/05_unprivileged_action_fabric.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/06_privileged_control_and_rollback.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/07_desktop_and_linux_mvp.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/08_proactive_advisor.md - pm protected-goal-plan project-manager
D docs/goals/agent_native/09_extensions_delivery_and_distro_gate.md - pm protected-goal-plan project-manager
M docs/goals/agent_native/README.md - pm protected-goal-plan project-manager
M docs/zh_cn/01_overview.md - g02 project-doc docs
M docs/zh_cn/02_planning_and_execution.md - g02 project-doc docs
M docs/zh_cn/03_capabilities_and_permissions.md - g02 project-doc docs
M docs/zh_cn/04_linux_session_and_daemon.md - g02 project-doc docs
M docs/zh_cn/07_wsl_test_standard.md - g02 project-doc docs
M docs/zh_cn/README.md - g02 project-doc docs
M migrations/versions/0001_core_foundation.py - g02 schema-migration core
M pyproject.toml - g02 project-config core
M scripts/collect_vm_evidence.py - g02 operations-tool operations
M scripts/status_linux_session.sh - g02 operations-tool operations
M scripts/verify_foundation_dbus.py - g02 operations-tool operations
D src/vibeos/agent_runtime.py - g02 runtime-code core
M src/vibeos/audit.py - g02 runtime-code core
M src/vibeos/broker.py - g02 runtime-code core
M src/vibeos/cli.py - g02 runtime-code core
M src/vibeos/command_service.py - g02 runtime-code core
D src/vibeos/core/adapters/http.py - g02 runtime-code core
M src/vibeos/core/adapters/metadata.py - g02 runtime-code core
M src/vibeos/core/application/__init__.py - g02 runtime-code core
M src/vibeos/core/domain/__init__.py - g02 runtime-code core
M src/vibeos/daemon.py - g02 runtime-code core
M src/vibeos/dbus_service.py - g02 runtime-code core
M src/vibeos/domain_registry.py - g02 runtime-code core
D src/vibeos/goal_loop.py - g02 runtime-code core
D src/vibeos/goal_ports.py - g02 runtime-code core
D src/vibeos/legacy_review_migration.py - g02 runtime-code core
D src/vibeos/loop_models.py - g02 runtime-code core
D src/vibeos/loop_policy.py - g02 runtime-code core
D src/vibeos/loop_snapshot.py - g02 runtime-code core
M src/vibeos/observation_service.py - g02 runtime-code core
M src/vibeos/planner.py - g02 runtime-code core
M src/vibeos/planning_service.py - g02 runtime-code core
D src/vibeos/projections.py - g02 runtime-code core
M src/vibeos/result_projection.py - g02 runtime-code core
D src/vibeos/review_resume_service.py - g02 runtime-code core
M src/vibeos/review_service.py - g02 runtime-code core
D src/vibeos/reviews.py - g02 runtime-code core
D src/vibeos/run_ledger.py - g02 runtime-code core
M src/vibeos/runtime.py - g02 runtime-code core
M src/vibeos/runtime_composition.py - g02 runtime-code core
M src/vibeos/task_application.py - g02 runtime-code core
M src/vibeos/task_models.py - g02 runtime-code core
M tests/test_architecture.py - g02 verification quality
M tests/test_audit.py - g02 verification quality
M tests/test_broker.py - g02 verification quality
M tests/test_cli_current.py - g02 verification quality
M tests/test_core_foundation.py - g02 verification quality
M tests/test_core_supervisor.py - g02 verification quality
D tests/test_daemon_http.py - g02 verification quality
M tests/test_foundation_transports.py - g02 verification quality
D tests/test_goal_loop.py - g02 verification quality
D tests/test_goal_loop_flag.py - g02 verification quality
D tests/test_reviews.py - g02 verification quality
D tests/test_run_ledger.py - g02 verification quality
M tests/test_runtime.py - g02 verification quality
M tests/test_task_plan_boundaries.py - g02 verification quality
D tests/test_v06_agent_runtime.py - g02 verification quality
U docs/architecture/durable_task_benchmark.json 8e15944113291285c61f63c14e3269b8be02a4bb1f4edb9f4a722151e156e66d g02 project-doc docs
U docs/architecture/durable_task_engine.md 3e8106149c92a55563e5572e00cf2c159766bd674d9d5300340c29f935777f33 g02 project-doc docs
U docs/goals/agent_native/03_reconcile_goal01_goal02.md 56bcca87b5f3e8b7ba5ce7cc197adeff66588e5356b79fe1d487568a363c0fd2 pm protected-goal-plan project-manager
U docs/goals/agent_native/04_system_service_recovery_vertical_slice.md f06fbde2d54799508f62781a5547527d18e7f335a22f6efc6e649bbc21fe03cb pm protected-goal-plan project-manager
U docs/goals/agent_native/05_unprivileged_tasks_and_installable_runtime.md 2bb616cdaf9bdcbb116c06e9d7cb8b0f91d3f734c693e58526b2d2d724872933 pm protected-goal-plan project-manager
U docs/goals/agent_native/06_privileged_canary_and_rollback.md b431c30fa719db232d25f7fad42a00afb8afcf608dad25a1276ae1ec56828495 pm protected-goal-plan project-manager
U docs/goals/agent_native/07_gnome_mixed_task_mvp.md 8996a09a269f42d65dd1ae881764f43863da7d3964b8dfd312ed300aafaf6cd2 pm protected-goal-plan project-manager
U docs/goals/agent_native/08_proactive_service_advisor.md dd86c798d15dd836bf95a5ecfffa867f63b33f201ef30bb216e9d4ac456186e6 pm protected-goal-plan project-manager
U docs/goals/agent_native/09_runtime_delivery_extension_and_distro_gate.md 611aa2e687469f3bb356a1b2a4985475a95dbe5b3ed0716a79dba670b6e40bba pm protected-goal-plan project-manager
U migrations/versions/0002_durable_task_engine.py 4c5fce1e573732a659edc82b7457d405a66a848243bed2438a349cd3e40f78f6 g02 schema-migration core
U migrations/versions/0003_repair_durable_task_semantics.py 383ed1772b36dcc764fea39ab4b88991f9912cf189f8a9da2b931187abfed70a g02 schema-migration core
U migrations/versions/0004_goal_contract_version_index.py b1171100fd8df34b4c133c9986f1f083c3c6880781c2033173dcff08835dffea g02 schema-migration core
U scripts/benchmark_durable_tasks.py 4d33190ccab14ffbf39a0432b9d7e32dc4cea036609ea2663087832a1f9a7f6d g02 operations-tool operations
U src/vibeos/core/adapters/outbox_repository.py 3b864d7cbee3130e6ad1ca08b6849a8a795ad77329b9132e9249e707d4e4a418 g02 runtime-code core
U src/vibeos/core/adapters/task_codec.py 62e8f5104c7bc86e5db42604e1a5c7bfed7c9c26dc5d2843667668aed31bf85c g02 runtime-code core
U src/vibeos/core/adapters/task_contracts.py 8256c5ef524f568ab783a74d9b15d33af1ccaa932ddb443f90c89e165fd25af0 g02 runtime-code core
U src/vibeos/core/adapters/task_persistence.py 11acdc475e13c40eaead16c84d37b1ded66a3571654ec64f61f1212e4381f20a g02 runtime-code core
U src/vibeos/core/adapters/task_repository.py 38b710cd0bea08e8ce8dad8690c1540e218ea5ae197048bfc821c073eb604f3d g02 runtime-code core
U src/vibeos/core/adapters/task_rows.py cb1abfe8051fbe6a121c6ea80934001fbf6618827f445fd72b6e58d431f5d33f g02 runtime-code core
U src/vibeos/core/application/task_workers.py 389f3c4193cfddbdf5389631a6ef3bd040b34300bf3a8fa3f7f26f0a746a57d8 g02 runtime-code core
U src/vibeos/core/domain/task.py cee7eb96209d7894d325016afd48eef63a3d1d9860ec9bbdf18769b19165964b g02 runtime-code core
U src/vibeos/core/domain/task_transitions.py 708e0009bd68dd91131468fdda21d5cab624059a92e9b7d1c3340ff808d5cf7e g02 runtime-code core
U src/vibeos/durable_action_executor.py f4364402224407bffbe86620446699f23f1ad932e27afa0633d2c92404686a11 g02 runtime-code core
U src/vibeos/durable_task_driver.py 17b0c17e1d61b95b1e89f12a696562d55ecba87044a530799df56318727234e4 g02 runtime-code core
U src/vibeos/durable_task_engine.py f1a98f9156532381c8e72a4f878cf86bccf8c206ce8b40e04997c0f4f8b9cf00 g02 runtime-code core
U src/vibeos/durable_task_lease.py f2ff1a29bdf16f375f930ab7e5fae25a4f82feac91f5690f4eacd1d42b088e44 g02 runtime-code core
U src/vibeos/durable_task_models.py fd1fab50500d142bcfaa3d3a5aa18dc945adfd79670bc0af8f161c17ada139d9 g02 runtime-code core
U src/vibeos/durable_task_planning.py b428bfa7daa60ce158d9abf17f0b15955143eb66735fc11a3263b9c722f0d4f4 g02 runtime-code core
U src/vibeos/durable_task_recovery.py e12207749b789bc4eb0382233e8959b2a935eafe9a4bc7cbef144f5544a7a301 g02 runtime-code core
U src/vibeos/durable_task_results.py 97cf58a4c9a6bb0d721fbc69d1ba4ecd903882a67fa0c14895cbe81537184a76 g02 runtime-code core
U src/vibeos/durable_task_resumer.py ab75be4369ac067e271eaf5eaed1b849b3c0198c7a9b830ead075d52388701aa g02 runtime-code core
U src/vibeos/durable_task_support.py 18a70daabe618bb368c0f075d3729018c516688cd45620c7b4c3f01449851edd g02 runtime-code core
U src/vibeos/planning_models.py 58e7d1ab0c9f2cdeca062c8f8cb97c06b4e35d3ec8431d95211db57a4ef66b46 g02 runtime-code core
U src/vibeos/task_reconciliation.py d07c4e9739a4e2c8f39f451947f0c3bc8f45fc4a313b5ace65824064e5dff0d1 g02 runtime-code core
U tests/support_durable_crash_worker.py 4dd732c5251f0f4a8e646f54f1180db79fc1e04f0854eef540cd408ba0d5826a g02 verification quality
U tests/test_durable_capability_migration.py c6cb7f7f9be9fbd9792809fa29e14ac82fac30305b90d1f401708a159f74f05b g02 verification quality
U tests/test_durable_task_controls.py 4b105f454930384a00958a6a01fb08f2dc1d0e3ad8c3142619a7e44edec27f52 g02 verification quality
U tests/test_durable_task_crash_matrix.py b9050680a4794d648fa6173caec7cdf4a6fa9e452f9e779bed7535c3c14c5b5f g02 verification quality
U tests/test_durable_task_domain.py dadf8a18af755c8446896467676362fa4780db94ef1f9012c4df73aec639c2b7 g02 verification quality
U tests/test_durable_task_repository.py 492904554cb05f257b2188e55aa4d87caaaaeb07d1c814fb188d868b70005779 g02 verification quality
U tests/test_durable_task_workers.py 3310cc74d3f36029dfc8f8d26f381f5821e39764bca31b90f2df82d8fce64eff g02 verification quality
```

## Stage and publication rules

The checkpoint must stage exactly the reviewed paths above plus this manifest,
must contain no database, environment file, secret, log, cache, generated build
tree, or temporary patch, and must remain clearly non-release. Reconciliation
will selectively reproduce logical groups from the clean Goal 01 ancestor; this
checkpoint is never merged wholesale into the delivery branch.
