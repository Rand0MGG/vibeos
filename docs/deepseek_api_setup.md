# DeepSeek API Setup

VibeOS can use DeepSeek through the existing OpenAI-compatible intent broker.

Recommended local setup:

```bash
cp .env.example .env
```

Then edit `.env`:

```env
VIBEOS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

Then test intent parsing:

```bash
vibe ask "打开浏览器" --dry-run --json
vibe ask "关闭浏览器" --dry-run --json
```

Notes:

- VibeOS only sends the user command and a narrow system prompt asking for JSON intent.
- The model still cannot execute Linux APIs directly.
- The capability broker validates the returned JSON and rejects unsupported actions.
- If no DeepSeek API key is configured, VibeOS falls back to the local rule parser.

DeepSeek also supports JSON output with `response_format={"type": "json_object"}`. VibeOS uses that mode for intent parsing.
