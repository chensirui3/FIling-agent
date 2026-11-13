"""SEC-shaped JSON fixtures (companyfacts, submissions) for a fictional filer, ACME CORP (CIK 1)."""

from __future__ import annotations

from typing import Any

M = 1_000_000
CAPEX = {2015: 1461, 2016: 1420, 2017: 1373, 2018: 1577, 2019: 1699}

# FY -> (accession, filed) of ACME's 10-K and 10-Q filings.
TEN_K = {
    2017: ("0000000001-18-000001", "2018-02-08"),
    2018: ("0000000001-19-000001", "2019-02-07"),
    2019: ("0000000001-20-000001", "2020-02-06"),
}
Q1_2018 = ("0000000001-18-000010", "2018-04-26")
Q1_2019 = ("0000000001-19-000010", "2019-04-25")
Q2_2019 = ("0000000001-19-000020", "2019-07-25")


def fact(
    start: str | None, end: str, val: float, filing: tuple[str, str], fy: int, fp: str, form: str
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "end": end,
        "val": val,
        "accn": filing[0],
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filing[1],
    }
    if start:
        record["start"] = start
    return record


def acme_companyfacts() -> dict[str, Any]:
    capex = [
        fact(f"{y}-01-01", f"{y}-12-31", CAPEX[y] * M, TEN_K[fy], fy, "FY", "10-K")
        for fy in TEN_K
        for y in (fy - 2, fy - 1, fy)
    ]
    revenues = [
        fact("2018-01-01", "2018-12-31", 32765 * M, TEN_K[2018], 2018, "FY", "10-K"),
        fact("2018-01-01", "2018-12-31", 32700 * M, TEN_K[2019], 2019, "FY", "10-K"),  # restated
        fact("2019-01-01", "2019-12-31", 32136 * M, TEN_K[2019], 2019, "FY", "10-K"),
        fact("2019-10-01", "2019-12-31", 7991 * M, TEN_K[2019], 2019, "FY", "10-K"),  # Q4
        fact("2018-01-01", "2018-03-31", 8278 * M, Q1_2018, 2018, "Q1", "10-Q"),
        fact("2018-01-01", "2018-03-31", 8278 * M, Q1_2019, 2019, "Q1", "10-Q"),
        fact("2019-01-01", "2019-03-31", 7863 * M, Q1_2019, 2019, "Q1", "10-Q"),
        fact("2019-04-01", "2019-06-30", 8171 * M, Q2_2019, 2019, "Q2", "10-Q"),
        fact("2019-01-01", "2019-06-30", 16034 * M, Q2_2019, 2019, "Q2", "10-Q"),  # YTD
        fact(
            "2019-01-01",
            "2019-12-31",
            1.0,
            ("0000000001-20-000099", "2020-01-28"),
            2019,
            "FY",
            "8-K",
        ),
    ]
    ppe_net = [
        fact(None, "2017-12-31", 8866 * M, TEN_K[2017], 2017, "FY", "10-K"),
        fact(None, "2017-12-31", 8866 * M, TEN_K[2018], 2018, "FY", "10-K"),
        fact(None, "2018-12-31", 8738 * M, TEN_K[2018], 2018, "FY", "10-K"),
        fact(None, "2018-12-31", 8738 * M, Q1_2019, 2019, "Q1", "10-Q"),
        fact(None, "2018-12-31", 8738 * M, TEN_K[2019], 2019, "FY", "10-K"),
        fact(None, "2019-03-31", 8715 * M, Q1_2019, 2019, "Q1", "10-Q"),
    ]
    return {
        "cik": 1,
        "entityName": "ACME CORP",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Entity Common Stock, Shares Outstanding",
                    "description": "Number of shares of common stock outstanding.",
                    "units": {
                        "shares": [
                            fact(None, "2019-01-31", 575 * M, TEN_K[2018], 2018, "FY", "10-K")
                        ]
                    },
                }
            },
            "us-gaap": {
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Payments to Acquire Property, Plant, and Equipment",
                    "description": "The cash outflow associated with the acquisition of "
                    "long-lived, physical assets used to produce goods and services.",
                    "units": {"USD": capex},
                },
                "Revenues": {
                    "label": "Revenues",
                    "description": "Amount of revenue recognized from goods sold and services "
                    "rendered.",
                    "units": {"USD": revenues},
                },
                "PropertyPlantAndEquipmentNet": {
                    "label": "Property, Plant and Equipment, Net",
                    "description": "Amount after accumulated depreciation of long-lived assets.",
                    "units": {"USD": ppe_net},
                },
            },
        },
    }


def retailer_companyfacts(cik: int, name: str, label_year: int) -> dict[str, Any]:
    """A 52-week fiscal year ending 2023-01-28, labelled by the company as `label_year`."""
    filing = (f"{cik:010d}-23-000001", "2023-03-20")
    return {
        "cik": cik,
        "entityName": name,
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            fact(
                                "2022-01-30", "2023-01-28", 9 * M, filing, label_year, "FY", "10-K"
                            )
                        ]
                    },
                }
            }
        },
    }


def acme_submissions() -> dict[str, Any]:
    return {
        "cik": "0000000001",
        "name": "ACME CORP",
        "tickers": ["ACME"],
        "fiscalYearEnd": "1231",
        "formerNames": [
            {"name": "ACME MANUFACTURING CO", "from": "1994-01-01", "to": "2002-01-01"}
        ],
        "filings": {
            "recent": {
                "accessionNumber": [TEN_K[2019][0], Q2_2019[0], Q1_2019[0], TEN_K[2018][0]],
                "form": ["10-K", "10-Q", "10-Q", "10-K"],
                "filingDate": [TEN_K[2019][1], Q2_2019[1], Q1_2019[1], TEN_K[2018][1]],
                "reportDate": ["2019-12-31", "2019-06-30", "2019-03-31", "2018-12-31"],
                "primaryDocument": [
                    "acme-10k2019.htm",
                    "acme-10q2.htm",
                    "acme-10q1.htm",
                    "acme-10k2018.htm",
                ],
                "isXBRL": [1, 1, 1, 1],
            },
            "files": [{"name": "CIK0000000001-submissions-001.json"}],
        },
    }


def acme_submissions_page() -> dict[str, Any]:
    return {
        "accessionNumber": [TEN_K[2017][0]],
        "form": ["10-K"],
        "filingDate": [TEN_K[2017][1]],
        "reportDate": ["2017-12-31"],
        "primaryDocument": ["acme-10k2017.htm"],
        "isXBRL": [1],
    }
