# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 5.x / CME-X5 Model B | :white_check_mark: Active |
| 3.x / MASIS V3 | :white_check_mark: Maintained |
| < 3.0 | :x: Deprecated |

## API Key & Capital Security Guidelines

1. **Never commit API credentials**: All keys must be configured via environment variables or a local `.env` file (which is git-ignored).
2. **Use Bybit API Key Permissions Wisely**:
   - For paper/telemetry testing, use **read-only** or **Demo Trading** API keys.
   - Never enable **Withdrawal** permissions on trading bot API keys.
   - Use Bybit's **IP Access Restriction** feature to bind your API key to your server IP.
3. **Safety Latch**: The engine enforces `MASIS_ALLOW_REAL_MONEY=0` by default. Real-money orders will be rejected unless explicitly unlocked.

## Reporting a Vulnerability

If you discover a security vulnerability or critical issue affecting funds/secrets, please do **NOT** open a public issue.
Contact the repository maintainers directly or submit a private security advisory via GitHub.
