# DeepSeek API Setup

DeepSeek is an optional OpenAI-compatible provider for VibeOS semantic stages.
It is not an execution authority: host-owned planning, capability validation,
review policy, and registered tools remain in control.

```bash
cp .env.example .env
```

Then set the provider values in `.env`:

```env
VIBEOS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

Check configuration without exposing credentials:

```bash
vibe doctor --json
```

For deterministic local verification, use offline mode instead of a live
provider:

```bash
vibe ask "search web for hello" --json --offline --dry-run
```

Provider failures are represented in the structured planning/trace result;
they must not grant a model direct desktop, D-Bus, shell, or arbitrary tool
access.
