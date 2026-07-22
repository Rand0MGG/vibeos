# Goal 04 fixed system-service runbook

This runbook operates only `vibeos-goal04-fixture.service`. Do not substitute
another user unit, a system unit or a real user service.

## Install the disabled fixture

From the intended VibeOS virtual environment:

```bash
python -m pip install -e .
bash scripts/install_goal04_fixture.sh
systemctl --user is-enabled vibeos-goal04-fixture.service
```

The final command must report `disabled`. The unit is installed under
`~/.config/systemd/user/`, has no network address family beyond `AF_UNIX`, and
can write only `~/.local/state/vibeos/goal04-fixture`.

## Prepare once before the task

```bash
vibe-goal04-fixture-controller prepare
```

Continue only when the JSON reports all of:

- `prepared: true`;
- `initial_start_failed: true`;
- `active_state: failed`;
- `synthetic_failure_lines: 1`;
- `controller_must_not_run_after_task_start: true`.

Do not run the controller again until the current Agent task has ended.

## Run or resume the governed task

Configure the provider route and SecretRef with the
[SecretRef runbook](goal04_secretref_runbook.md), then run:

```bash
vibe service recover-fixture --route ROUTE_ID --json
```

If the task waits because the keyring is locked, unlock the desktop keyring and
resume the same task:

```bash
vibe service resume TASK_ID --route ROUTE_ID --keyring-unlocked --json
```

A successful result must contain exactly one canonical receipt and a
TerminalOutcome with diagnosis, action, current state, evidence IDs, completion
judgment and unresolved risks. `systemctl` dispatch success alone is not an
acceptance result.

## Stop after evidence collection

```bash
systemctl --user stop vibeos-goal04-fixture.service
systemctl --user reset-failed vibeos-goal04-fixture.service
```

The unit remains disabled. Reinstalling the fixture also executes
`disable --now`, preventing an old token or a prior healthy process from being
started automatically at login.
