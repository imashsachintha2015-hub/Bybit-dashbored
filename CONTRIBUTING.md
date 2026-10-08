# Contributing to MASIS Terminal

Thank you for your interest in contributing to the **MASIS Trading Terminal & Quantitative Engine**. We welcome contributions from quants, engineers, and traders.

## Core Engineering Principles

1. **Empirical Edge Over Hope**: We do not add indicators based on intuition. Changes to strategy rules must be accompanied by backtest evidence accounting for round-trip fees (12–14 bps) and realistic slippage.
2. **Capital Preservation First**: The Risk Governor (`agents/risk-governor.js`) and exchange-side stop loss rules are absolute. Never submit PRs that soften or disable risk constraints without clear, bounded controls.
3. **Zero Secrets in Git**: Never commit `.env` files, API keys, or private endpoints.

## Development Workflow

1. **Fork and Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Set Up Local Environment**:
   ```bash
   pip install -r requirements.txt
   npm install
   cp .env.example .env
   ```
3. **Run Tests**:
   ```bash
   pytest tests/
   ```
4. **Submit a Pull Request**: Use the provided PR template and clearly detail your testing methodology.
