# Cookie Web Provider

Free AI via browser cookie simulation. Supports DeepSeek, Claude, ChatGPT, Gemini, and Kimi.

## Installation

### As Hermes plugin

```bash
# Clone into hermes plugins directory
git clone <repo-url> ~/.hermes/plugins/model-providers/cookie-web/

# Or copy manually
cp -r cookie-web-provider ~/.hermes/plugins/model-providers/cookie-web/
```

### As standalone pip package

```bash
pip install aiohttp
# Then run proxy server directly:
python proxy_server.py
```

## Setup

### 1. Start Chrome in debug mode

```bash
google-chrome --remote-debugging-port=9222
```

Or use any Chrome instance with remote debugging enabled.

### 2. Log into platforms

- Open https://chat.deepseek.com and log in
- Open https://claude.ai and log in
- (Optional) Open https://chatgpt.com and log in
- Open https://kimi.com or get API key from https://platform.moonshot.ai

### 3. Configure Hermes

Edit `~/.hermes/config.yaml`:

```yaml
model:
  provider: cookie-web
```

### 4. Run Hermes

```bash
hermes
```

## Usage

### Switch to cookie-web provider

```
/model cookie-web/deepseek-chat
```

### Available models

- `deepseek-chat` - DeepSeek Chat
- `deepseek-reasoner` - DeepSeek Reasoner
- `deepseek-chat-search` - DeepSeek Chat with web search
- `deepseek-reasoner-search` - DeepSeek Reasoner with web search
- `claude-sonnet-4-6` - Claude Sonnet
- `claude-opus-4-6` - Claude Opus
- `claude-haiku-4-6` - Claude Haiku
- `gpt-4` - GPT-4
- `gpt-4-turbo` - GPT-4 Turbo
- `gpt-4o` - GPT-4o
- `kimi-k2.5` - Kimi K2.5
- `kimi-k2.6` - Kimi K2.6
- `kimi-k2-thinking` - Kimi K2 Thinking
- `moonshot-v1-8k` - Moonshot v1 (8K)
- `moonshot-v1-32k` - Moonshot v1 (32K)
- `moonshot-v1-128k` - Moonshot v1 (128K)

## CLI

```bash
# Manual login (paste cookies from browser)
python cli.py login --provider deepseek --mode manual

# Auto login (launches Chrome, captures credentials)
python cli.py login --provider claude --mode auto

# Check credential status
python cli.py status
```

## Limitations

- Cookies expire (7-30 days) - need to re-login
- Rate limits may apply
- Some providers (ChatGPT) have additional anti-bot measures
