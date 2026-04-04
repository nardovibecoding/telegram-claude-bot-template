# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Stablecoin single-sided yield aggregator.

Sources:
  - Stablecoin market caps: CoinMarketCap stablecoin listing (internal API)
  - Yield opportunities:    DeFiLlama yields API
"""
import asyncio
import aiohttp

CMC_API = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
    "?start=1&limit=200&sortBy=market_cap&sortType=desc&convert=USD"
    "&cryptoType=all&tagType=all&tagSlugs=stablecoin"
)
DEFILLAMA_YIELDS_API = "https://yields.llama.fi/pools"

CMC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://coinmarketcap.com/",
}

MIN_MARKETCAP = 500_000_000   # $500 M
MIN_TVL       = 1_000_000     # $1 M pool TVL
TOP_PER_COIN  = 5             # top pools shown per stablecoin
MAX_APY       = 80            # ignore obviously broken/unsustainable yields


async def _fetch(session: aiohttp.ClientSession, url: str, headers: dict | None = None) -> dict:
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        return await r.json()


async def generate_yield_report() -> list[str]:
    async with aiohttp.ClientSession() as session:
        cmc_data, yield_data = await asyncio.gather(
            _fetch(session, CMC_API, headers=CMC_HEADERS),
            _fetch(session, DEFILLAMA_YIELDS_API),
        )

    # ── Step 1: stablecoins with market cap > $500M ───────────────────────────
    big_stables: dict[str, float] = {}   # symbol → market cap USD
    for coin in cmc_data.get("data", {}).get("cryptoCurrencyList", []):
        quotes = coin.get("quotes", [])
        mcap = quotes[0].get("marketCap", 0) if quotes else 0
        if mcap >= MIN_MARKETCAP:
            big_stables[coin["symbol"].upper()] = mcap

    if not big_stables:
        return ["⚠️ Could not retrieve stablecoin data from CoinMarketCap."]

    # ── Step 2: single-sided yield pools for those stablecoins ────────────────
    results: dict[str, list[dict]] = {}

    for pool in yield_data.get("data", []):
        raw_symbol = pool.get("symbol", "")
        symbol     = raw_symbol.upper()
        apy        = pool.get("apy") or 0
        tvl        = pool.get("tvlUsd") or 0
        il_risk    = pool.get("ilRisk", "")

        # Drop LP pairs
        if "-" in raw_symbol or "/" in raw_symbol:
            continue
        if il_risk == "yes":
            continue

        # Match against our stablecoin list (exact, or wrapped variant like aUSDT, USDT.E)
        matched = None
        for stable in big_stables:
            if symbol == stable or symbol.endswith(stable) or symbol == stable + ".E":
                matched = stable
                break
        if matched is None:
            continue

        if not (0 < apy <= MAX_APY):
            continue
        if tvl < MIN_TVL:
            continue

        results.setdefault(matched, []).append({
            "project": pool.get("project", "?"),
            "chain":   pool.get("chain", "?"),
            "apy":     apy,
            "tvl":     tvl,
            "pool_id": pool.get("pool", ""),
        })

    if not results:
        return ["No single-sided yield opportunities found matching the criteria."]

    # ── Step 3: format ────────────────────────────────────────────────────────
    lines: list[str] = [
        f"🏦 Stablecoin Yield Report",
        f"{len(big_stables)} stablecoins >$500M | single-sided | TVL >$1M | APY ≤{MAX_APY}%\n",
    ]

    for symbol in sorted(results.keys()):
        pools  = sorted(results[symbol], key=lambda x: x["apy"], reverse=True)[:TOP_PER_COIN]
        mcap   = big_stables[symbol]
        mcap_s = f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M"
        lines.append(f"── {symbol}  (mkt cap {mcap_s}) ──")
        for p in pools:
            tvl_s = f"${p['tvl']/1e6:.1f}M"
            link  = f"https://defillama.com/yields/pool/{p['pool_id']}"
            lines.append(f"{p['apy']:.2f}%  {p['project']}  {p['chain']}  TVL {tvl_s}\n{link}")
        lines.append("")

    # Split into Telegram-safe chunks
    messages: list[str] = []
    current = ""
    for line in lines:
        chunk = line + "\n"
        if len(current) + len(chunk) > 4000:
            messages.append(current.strip())
            current = chunk
        else:
            current += chunk
    if current.strip():
        messages.append(current.strip())

    return messages
