# Goal 04 SecretRef operations

Provider credentials belong in the logged-in user's freedesktop Secret Service
(GNOME Keyring), not `.env`, unit files or shell exports.

## Import and inspect

```bash
vibe secrets import goal04-provider \
  --model MODEL_NAME \
  --base-url https://PROVIDER.example/v1
vibe secrets status goal04-provider --json
```

Import requires a TTY and uses hidden input. Status returns only `available`,
`locked` or `missing`, the opaque SecretRef URI and non-secret route metadata.
It never prints the value.

For a one-time migration from an existing shell variable:

```bash
vibe secrets import goal04-provider \
  --model MODEL_NAME \
  --base-url https://PROVIDER.example/v1 \
  --from-env EXISTING_KEY_VARIABLE
```

This removes the value from the `vibe` process. Remove the export from the
parent shell, login profile or old `.env` after migration; VibeOS has no
implicit environment fallback.

## Locked keyring and resume

When Secret Service reports a locked collection, Gateway returns
`keyring_locked` and the task commits an explainable `WAITING` condition. Unlock
the user's keyring through the desktop session, verify `vibe secrets status`,
then deliver the matching `secret-service:unlocked:<secret-id>` event. The
Durable Task Engine resumes the same task; do not create a replacement task or
copy the key into task input.

## Delete

```bash
vibe secrets delete goal04-provider --json
```

Deletion clears both the Secret Service item and non-sensitive route metadata.
If the keyring is locked, metadata is retained so the failed deletion remains
recoverable and explainable.

## Controlled provider smoke

Use only the synthetic D0 fixture facts. Confirm that the result is a strict
`service_diagnosis/v1` response, the proposal names only
`vibeos-goal04-fixture.service`, and no secret canary appears in stdout, stderr,
task/event/outbox rows, traces or evidence. Record provider/model, time, request
ID and classified outcome, but never request/authorization payloads. Absence of
a user credential means this gate is `not run`, not passed.
