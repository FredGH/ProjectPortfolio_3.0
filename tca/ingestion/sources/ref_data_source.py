from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import dlt
from faker import Faker

fake = Faker("en_GB")

# Instrument master — must match INSTRUMENT_CONFIG in oms_source.py
_EQUITY_ISINS = [
    "DE000BASF111", "GB0002634946", "FR0000131104", "NL0000009355", "DE0005140008",
    "GB00B10RZP78", "FR0000120271", "DE000BAY0017", "NL0011821202", "DE0005493092",
    "GB0007188757", "SE0000148884", "CH0012221716", "DE0005140008", "IT0000072618",
    "ES0113900J37", "FI0009000681", "BE0003470755", "DK0060534915", "AT0000937503",
]
_EQUITY_NAMES = [
    "BASF SE", "GlaxoSmithKline PLC", "BNP Paribas SA", "Unilever NV", "Deutsche Bank AG",
    "Rolls-Royce Holdings PLC", "LVMH Moët Hennessy", "Bayer AG", "ING Groep NV", "Infineon AG",
    "Marks & Spencer Group PLC", "Volvo AB", "Roche Holding AG", "Deutsche Post AG", "ENI SpA",
    "Banco Santander SA", "Nokia OYJ", "Anheuser-Busch InBev", "Carlsberg AS", "OMV AG",
]
_FUTURE_NAMES = [
    "EURO STOXX 50 Mar25", "DAX Mar25", "FTSE 100 Mar25", "CAC 40 Mar25",
    "AEX Mar25", "SMI Mar25", "IBEX 35 Mar25", "OMXS30 Mar25", "BEL 20 Mar25", "ATX Mar25",
]
_BOND_ISINS = [
    "DE0001102481", "FR0013451507", "IT0005366987", "ES0000012A37", "GB00B54QLM75",
    "NL0015614552", "BE0000346894", "AT0000A1XML2", "FI0009005953", "PT0OE0200015",
]
_BOND_NAMES = [
    "Bund 2.5% 2030", "OAT 1.75% 2029", "BTP 3.0% 2031", "Bonos 2.35% 2029", "Gilt 1.625% 2028",
    "DSL 2.0% 2028", "OLO 1.9% 2027", "RAGB 2.1% 2030", "RFGB 1.875% 2029", "PORTUG 3.15% 2030",
]
_FX_FWD_NAMES = [
    "EUR/USD 1M", "EUR/USD 3M", "EUR/GBP 1M", "EUR/GBP 3M",
    "EUR/JPY 1M", "EUR/CHF 1M", "EUR/SEK 1M", "EUR/NOK 1M", "EUR/DKK 1M", "EUR/PLN 1M",
]

VENUES: list[dict] = [
    {"venue_id": "XLON", "name": "London Stock Exchange", "country": "GB", "type": "LIT"},
    {"venue_id": "XETR", "name": "Xetra", "country": "DE", "type": "LIT"},
    {"venue_id": "XPAR", "name": "Euronext Paris", "country": "FR", "type": "LIT"},
    {"venue_id": "XAMS", "name": "Euronext Amsterdam", "country": "NL", "type": "LIT"},
    {"venue_id": "BATE", "name": "BATS Europe", "country": "GB", "type": "MTF"},
    {"venue_id": "XEUR", "name": "Eurex", "country": "DE", "type": "DERIV"},
    {"venue_id": "BLTX", "name": "Bloomberg Fixed Income", "country": "US", "type": "OTC"},
    {"venue_id": "MFTR", "name": "MarketFinancials Trading", "country": "GB", "type": "OTC"},
    {"venue_id": "TRAX", "name": "Trax Reporting", "country": "GB", "type": "OTC"},
    {"venue_id": "GLMX", "name": "GLMX Repo", "country": "US", "type": "OTC"},
    {"venue_id": "FXALL", "name": "FXall", "country": "US", "type": "OTC"},
]

ALGOS: list[dict] = [
    {"algo_id": "VWAP", "name": "Volume Weighted Average Price", "family": "participation", "provider": "PrivateBank"},
    {"algo_id": "TWAP", "name": "Time Weighted Average Price", "family": "schedule", "provider": "PrivateBank"},
    {"algo_id": "IS",   "name": "Implementation Shortfall",   "family": "impact", "provider": "PrivateBank"},
    {"algo_id": "POV",  "name": "Percentage of Volume",       "family": "participation", "provider": "PrivateBank"},
    {"algo_id": "SNIPER","name": "Opportunistic Sniper",       "family": "opportunistic", "provider": "PrivateBank"},
    {"algo_id": "ARRIVAL","name": "Arrival Price",             "family": "impact", "provider": "PrivateBank"},
]

COUNTERPARTY_DETAILS: list[dict] = [
    {"counterparty_id": "CP_ABCD", "name": "Alpha Capital Management", "type": "ASSET_MANAGER", "country": "DE", "lei": "529900T8BM49AURSDO55"},
    {"counterparty_id": "CP_EFGH", "name": "European Fund Management SA", "type": "ASSET_MANAGER", "country": "FR", "lei": "969500H9GB1Z9ME9VT63"},
    {"counterparty_id": "CP_IJKL", "name": "Nordic Institutional Investors AB", "type": "PENSION_FUND", "country": "SE", "lei": "529900BKTH5P99BTST43"},
    {"counterparty_id": "CP_MNOP", "name": "Milan Asset Partners SpA", "type": "HEDGE_FUND", "country": "IT", "lei": "815600C0BAE5D9D5A627"},
    {"counterparty_id": "CP_QRST", "name": "Dublin Insurance Asset Management", "type": "INSURANCE", "country": "IE", "lei": "635400YSC9E9BXHP6Y40"},
]

LEGAL_ENTITIES: list[dict] = [
    {"entity_id": "PB_DE", "name": "PrivateBank Bank Hamburg", "jurisdiction": "DE", "mifid_lei": "DGKL00HFI22345678901"},
    {"entity_id": "PB_UK", "name": "PrivateBank Capital Markets LLC UK", "jurisdiction": "GB", "mifid_lei": "DGKL00HFI22345678902"},
    {"entity_id": "BCM_US", "name": "PrivateBank Capital Markets LLC", "jurisdiction": "US", "mifid_lei": "DGKL00HFI22345678903"},
]


def _make_instruments() -> list[dict]:
    loaded_at = datetime.now(tz=timezone.utc)
    instruments = []

    for i, (isin, name) in enumerate(zip(_EQUITY_ISINS, _EQUITY_NAMES)):
        instruments.append({
            "instrument_id": f"EQTY-{i+1:03d}",
            "isin": isin,
            "name": name,
            "instrument_class": "equity",
            "currency": "EUR",
            "exchange": "XETR",
            "sector": fake.random_element(["Financials", "Healthcare", "Industrials", "Energy", "Technology"]),
            "country_of_risk": fake.random_element(["DE", "GB", "FR", "NL", "IT", "ES", "SE"]),
            "_loaded_at": loaded_at,
        })

    for i, name in enumerate(_FUTURE_NAMES):
        instruments.append({
            "instrument_id": f"FUTS-{i+1:03d}",
            "isin": None,
            "name": name,
            "instrument_class": "equity_future",
            "currency": "EUR",
            "exchange": "XEUR",
            "underlying_id": f"EQTY-{i+1:03d}",
            "expiry_date": "2025-03-21",
            "contract_size": 10,
            "sector": "Derivatives",
            "country_of_risk": "EU",
            "_loaded_at": loaded_at,
        })

    for i, (isin, name) in enumerate(zip(_BOND_ISINS, _BOND_NAMES)):
        instruments.append({
            "instrument_id": f"BOND-{i+1:03d}",
            "isin": isin,
            "name": name,
            "instrument_class": "fixed_income",
            "currency": "EUR",
            "exchange": "BLTX",
            "coupon_rate": round(float(Faker().random_element([1.5, 1.75, 2.0, 2.35, 2.5, 3.0, 3.15])), 4),
            "maturity_date": fake.date_between(start_date="+3y", end_date="+10y").isoformat(),
            "sector": "Government",
            "country_of_risk": fake.random_element(["DE", "FR", "IT", "ES", "GB", "NL", "BE"]),
            "_loaded_at": loaded_at,
        })

    for i, name in enumerate(_FX_FWD_NAMES):
        instruments.append({
            "instrument_id": f"FXFW-{i+1:03d}",
            "isin": None,
            "name": name,
            "instrument_class": "fx_derivative",
            "currency": "USD",
            "exchange": "FXALL",
            "base_currency": "EUR",
            "quote_currency": name.split("/")[1][:3],
            "tenor": name.split(" ")[1],
            "sector": "FX",
            "country_of_risk": "EU",
            "_loaded_at": loaded_at,
        })

    return instruments


def _make_traders() -> list[dict]:
    Faker.seed(42)
    loaded_at = datetime.now(tz=timezone.utc)
    traders = []
    asset_classes = ["equity", "equity_future", "fixed_income", "fx_derivative"]
    for i in range(1, 11):
        traders.append({
            "trader_id": f"TRD-{i:03d}",
            "name": fake.name(),
            "desk": fake.random_element(["Equity Execution", "Rates", "FX", "Derivatives"]),
            "legal_entity": fake.random_element(["PB_DE", "PB_UK"]),
            "primary_asset_class": fake.random_element(asset_classes),
            "seniority": fake.random_element(["JUNIOR", "MID", "SENIOR", "HEAD"]),
            "_loaded_at": loaded_at,
        })
    return traders


@dlt.source(name="ref_data")
def ref_data_source() -> Iterator:
    loaded_at = datetime.now(tz=timezone.utc)

    @dlt.resource(name="instruments", write_disposition="merge", primary_key="instrument_id")
    def instruments() -> Iterator[dict]:
        yield from _make_instruments()

    @dlt.resource(name="clients", write_disposition="merge", primary_key="counterparty_id")
    def clients() -> Iterator[dict]:
        for cp in COUNTERPARTY_DETAILS:
            yield {**cp, "_loaded_at": loaded_at}

    @dlt.resource(name="venues", write_disposition="merge", primary_key="venue_id")
    def venues() -> Iterator[dict]:
        for v in VENUES:
            yield {**v, "_loaded_at": loaded_at}

    @dlt.resource(name="algos", write_disposition="merge", primary_key="algo_id")
    def algos() -> Iterator[dict]:
        for a in ALGOS:
            yield {**a, "_loaded_at": loaded_at}

    @dlt.resource(name="traders", write_disposition="merge", primary_key="trader_id")
    def traders() -> Iterator[dict]:
        yield from _make_traders()

    @dlt.resource(name="legal_entities", write_disposition="merge", primary_key="entity_id")
    def legal_entities() -> Iterator[dict]:
        for le in LEGAL_ENTITIES:
            yield {**le, "_loaded_at": loaded_at}

    return instruments, clients, venues, algos, traders, legal_entities
