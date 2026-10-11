#!/usr/bin/env python3
"""Fetch cryptocurrency prices from CoinGecko API.

Usage: python crypto.py <symbol>
Example: python crypto.py btc
"""

from datetime import UTC, datetime
import json
import sys
import urllib.error
import urllib.request


def get_coin_id(symbol: str) -> str:
    """Map common symbols to CoinGecko IDs."""
    symbol_map = {
        "btc": "bitcoin",
        "bitcoin": "bitcoin",
        "eth": "ethereum",
        "ethereum": "ethereum",
        "sol": "solana",
        "solana": "solana",
        "xrp": "ripple",
        "ripple": "ripple",
        "ada": "cardano",
        "cardano": "cardano",
        "doge": "dogecoin",
        "dogecoin": "dogecoin",
    }
    return symbol_map.get(symbol.lower(), symbol.lower())


def fetch_crypto_price(coin_id: str) -> dict:
    """Fetch price from CoinGecko API."""
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd,krw&include_24hr_change=true"

    try:
        with urllib.request.urlopen(url) as response:
            data: dict = json.loads(response.read().decode())

        # Check if response contains error or empty data
        if not data or coin_id not in data:
            return {"error": f"Failed to fetch price for {coin_id}", "response": data}

        return data

    except urllib.error.URLError as e:
        return {
            "error": f"Network error while fetching price for {coin_id}",
            "details": str(e),
        }
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON response for {coin_id}", "details": str(e)}


def main() -> None:
    """Main entry point."""
    # Get symbol from command line, default to 'btc'
    symbol = sys.argv[1] if len(sys.argv) > 1 else "btc"

    # Map symbol to CoinGecko ID
    coin_id = get_coin_id(symbol)

    # Fetch price data
    response = fetch_crypto_price(coin_id)

    # Check for errors in response
    if "error" in response:
        print(json.dumps(response))
        sys.exit(1)

    # Add timestamp and format final response
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {"coin": coin_id, "timestamp": timestamp, "data": response}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
