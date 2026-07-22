# DeepSeek API Setup

DeepSeek is an optional OpenAI-compatible provider for VibeOS semantic stages.
It is not an execution authority: host-owned planning, capability validation,
review policy, and registered tools remain in control.

Import the key through a TTY. Hidden input is sent to GNOME Keyring through
`secret-tool` stdin, never argv or an environment variable:

```bash
vibe secrets import deepseek \
  --model deepseek-v4-flash \
  --base-url https://api.deepseek.com
```

Check configuration without exposing credentials:

```bash
vibe secrets status deepseek --json
```

Use `vibe secrets delete deepseek` to remove both the keyring item and its
non-secret route metadata. If the keyring is locked, Gateway returns an
explainable durable wait condition; unlocking the session keyring permits the
task to resume without copying the key into Core state.

For one-time migration only, `--from-env DEEPSEEK_API_KEY` imports and removes
that value from the current process. There is no implicit environment or
`.env` fallback. Remove the old export or file entry from the launching shell
or service after migration.

For deterministic local verification, use offline mode instead of a live
provider:

```bash
vibe ask "search web for hello" --json --offline --dry-run
```

The Goal04 service diagnosis purpose uses Model Gateway v1. Legacy semantic
purposes are disabled at their old direct transport until Goal05 migrates them;
they cannot obtain a key from this SecretRef route.
