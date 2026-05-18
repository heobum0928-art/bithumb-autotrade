# Technology Stack

**Analysis Date:** 2026-05-19

## Languages

**Primary:**
- Python 3.x - Entire bot system, trading automation, monitoring, analysis

**Secondary:**
- YAML - Configuration files (config.yaml)
- SQL - SQLite database queries and schema

## Runtime

**Environment:**
- Python 3.7+ (based on f-strings and type hints)

**Package Manager:**
- pip
- Lockfile: `requirements.txt` present

## Frameworks

**Core Trading:**
- Custom REST API client (`bithumb/client.py`) - Bithumb exchange integration via requests

**Testing & Utilities:**
- None currently in use (no test framework detected)

**Build/Dev:**
- yaml==6.0.1 - YAML parsing for config management
- schedule==1.2.1 - Job scheduling for watchdog and periodic tasks

**Data & Storage:**
- sqlite3 (stdlib) - Trade logging, signal tracking, pump event recording

## Key Dependencies

**Critical:**
- requests==2.31.0 - HTTP client for all API calls (Bithumb, Telegram)
- pyyaml==6.0.1 - Configuration parsing
- PyJWT==2.8.1 - JWT token generation for Bithumb API 2.0 authentication
- python-dotenv==1.0.0 - Environment variable loading (.env files)

**Monitoring & Notification:**
- python-telegram-bot==20.7 - Telegram bot for command interface and alerts
- websocket-client==1.7.0 - WebSocket client for Bithumb real-time ticker feeds

**Process Management:**
- psutil (imported in alt_monitor.py) - Process monitoring for single-instance enforcement and watchdog

**AI Analysis:**
- anthropic (optional/installed separately) - Claude API for trading data analysis

## Configuration

**Environment:**
- Loads from `config.yaml` (checked into repo but should use .gitignore)
- `.env` support via python-dotenv (for sensitive credentials)
- Environment variables for Bithumb API keys, Telegram tokens, Anthropic API keys

**Key configs required:**
- bithumb.api_key - Bithumb REST API key
- bithumb.api_secret - Bithumb REST API secret (HMAC-SHA512)
- telegram.bot_token - Telegram bot token
- telegram.chat_id - Target Telegram chat ID
- anthropic_api_key - Claude API key for analysis
- trading.capital_krw - Operating capital in Korean Won
- monitor.poll_interval_sec - Polling frequency for new listings

**Build:**
- start_bots.bat - Windows batch file for starting bot system

## Platform Requirements

**Development:**
- Windows 11 Pro 10.0.26200 (current environment)
- PowerShell and Bash available
- Python 3.7+ with pip

**Production:**
- Bithumb exchange account with API credentials
- Telegram bot token and chat ID for notifications
- Anthropic API key for Claude analysis (optional)
- Internet connection for REST API and WebSocket connections

**Database:**
- SQLite (local file-based): `data/trades.db`
- Auto-creates schema via `bithumb.db.init_db()`

## Architecture Notes

**API Communication:**
- Bithumb API 2.0 uses JWT (HS256) authentication
- Private API: Authorization header with Bearer token
- Public API: No authentication required
- Base URL: https://api.bithumb.com

**Multi-Process Architecture:**
- `alt_monitor.py` - Main trading bot (REST API polling + WebSocket real-time)
- `tg_bot.py` - Telegram command interface
- `watchdog.py` - Process supervisor (keeps bots alive, manages restarts)
- `session_writer.py` - Daily log generator (reads DB, writes markdown)
- `ai_analyze.py` - Claude-powered analysis script

**Threading Model:**
- `alt_monitor.py` uses threading for non-blocking operations:
  - Main loop: position monitoring + signal detection
  - WebSocket subscriber thread: real-time price updates
  - Queue-based inter-thread communication for pullback tracking and entry signals

**Logging:**
- Rotating file handlers to `logs/` directory
- Structured JSON where applicable
- All timestamps use UTC or KST (UTC+9)

---

*Stack analysis: 2026-05-19*
