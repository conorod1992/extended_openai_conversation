---
name: crypto
description: Get real-time cryptocurrency prices. Use when users ask about Bitcoin, Ethereum, or other crypto prices and market data.
---

# Cryptocurrency Price Checker

## Instructions

1. **Identify the cryptocurrency** the user is asking about (Bitcoin, Ethereum, Solana, etc.)

2. **Execute the appropriate script**
   - `python3 scripts/crypto.py btc` - Get Bitcoin price
   - `python3 scripts/crypto.py eth` - Get Ethereum price
   - `python3 scripts/crypto.py sol` - Get Solana price
   - `python3 scripts/crypto.py doge` - Get Dogecoin price
   - `python3 scripts/crypto.py xrp` or `python3 scripts/crypto.py ada` - Get XRP or Cardano prices
   - `python3 scripts/crypto.py <coin_id>` - Get another coin's price using its CoinGecko coin ID, not an arbitrary ticker symbol

   Supported ticker aliases are BTC, ETH, SOL, XRP, ADA and DOGE (case-insensitive). For other coins use the exact CoinGecko ID.

3. **Format the response** with:
   - USD price (and KRW if useful); the script returns USD and KRW, not arbitrary local currencies
   - 24-hour price change percentage
   - Source attribution to CoinGecko
   - Note that crypto prices are volatile and change rapidly

## Examples

**User:** "What's the current Bitcoin price?"
**Action:** Execute `python3 scripts/crypto.py btc`
**Response:** "Bitcoin is currently trading at $67,234. It has changed +2.3% in the last 24 hours. (Source: CoinGecko)"

**User:** "How much is Ethereum worth?"
**Action:** Execute `python3 scripts/crypto.py eth`
**Response:** "Ethereum is currently priced at $3,456, down -1.2% over the past 24 hours. (Source: CoinGecko)"

**User:** "Check Solana and Dogecoin prices"
**Action:** Execute `python3 scripts/crypto.py sol` and `python3 scripts/crypto.py doge`
**Response:** "Current prices - Solana: $145 (+5.7% 24h), Dogecoin: $0.12 (+0.8% 24h). (Source: CoinGecko)"
