# Cookie Web Provider

Free AI via browser cookie simulation. Supports DeepSeek, Claude, ChatGPT, Gemini, Kimi.

Works as **Hermes plugin** or **standalone OpenAI-compatible proxy**.

## Installation

### Hermes plugin

```bash
git clone git@github.com:stranger1sag/hermes-cookie-web.git \
  ~/.hermes/plugins/model-providers/cookie-web/
```

### Standalone

```bash
pip install git+ssh://git@github.com/stranger1sag/hermes-cookie-web.git
# Or just run directly:
pip install aiohttp
python proxy_server.py
```

## Quick start

### 1. Start Chrome in debug mode

```bash
google-chrome --remote-debugging-port=9222
```

Or use Chrome already running with `--remote-debugging-port=9222`.

### 2. Log into AI platforms

Open these in your debug Chrome and log in:

- https://chat.deepseek.com
- https://claude.ai
- https://chatgpt.com (optional)
- https://kimi.com (or use API key from https://platform.moonshot.ai)

### 3. Capture credentials

```bash
# Manual — paste cookies from browser
python cli.py login --provider deepseek --mode manual

# Auto — Chrome captures credentials for you
python cli.py login --provider claude --mode auto

# Check status
python cli.py status
```

### 4. Configure Hermes

Add to `~/.hermes/config.yaml`:

```yaml
model:
  provider: cookie-web
```

### 5. Run

```bash
hermes
```

The proxy server starts automatically when Hermes loads the plugin.

## Available models

| Model | Provider |
|-------|----------|
| `deepseek-chat` | DeepSeek Chat |
| `deepseek-reasoner` | DeepSeek Reasoner |
| `deepseek-chat-search` | DeepSeek Chat + web search |
| `deepseek-reasoner-search` | DeepSeek Reasoner + web search |
| `claude-sonnet-4-6` | Claude Sonnet |
| `claude-opus-4-6` | Claude Opus |
| `claude-haiku-4-6` | Claude Haiku |
| `gpt-4` | GPT-4 |
| `gpt-4-turbo` | GPT-4 Turbo |
| `gpt-4o` | GPT-4o |
| `kimi-k2.5` | Kimi K2.5 |
| `kimi-k2.6` | Kimi K2.6 |
| `kimi-k2-thinking` | Kimi K2 Thinking |
| `moonshot-v1-8k` | Moonshot v1 (8K) |
| `moonshot-v1-32k` | Moonshot v1 (32K) |
| `moonshot-v1-128k` | Moonshot v1 (128K) |

Switch provider in Hermes:

```
/model cookie-web/deepseek-chat
```

## Standalone usage (without Hermes)

Run the proxy server directly:

```bash
python proxy_server.py
# → Listening on http://127.0.0.1:13000
```

Then any OpenAI-compatible client can use it:

```bash
curl http://127.0.0.1:13000/v1/chat/completions \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello"}]}'
```

## How it works

1. You log into AI platforms in Chrome with remote debugging enabled
2. The plugin captures your session cookies via Chrome DevTools Protocol
3. Cookies are stored locally (`~/.hermes/cookie-web/credentials.json`)
4. The proxy server uses these cookies to make API calls to the web interfaces
5. Responses are translated back to OpenAI-compatible format

## Limitations

- Cookies expire (7-30 days) — re-login when they do
- Rate limits may apply per platform
- ChatGPT requires additional Sentinel bypass
- Chrome must be running with `--remote-debugging-port=9222` for auto-login
