# Fresh benchmark: review sheet

Numeric golds were computed from the filing's tagged (inline-XBRL) figures; *SEC XBRL API* says whether the SEC's API reports the same figures for the same filing and period. Text questions were drafted by Claude from one page and checked automatically (verbatim quote, figures on the page) and by a first-pass review.

To check an item, open the filing, search for the row label or a phrase from the quote (Ctrl+F), and confirm the figure, the period and the answer. Reply with the numbers of any items that are wrong or ambiguous, and why.

## 1. Microsoft: breakdown, breakdown_segment_operating_income (test)

- **Question:** What operating income did Microsoft's Agents and Infra segment report in the first quarter of fiscal 2027? Answer in USD millions.
- **Gold:** $40558
- **How:** Agents and Infra operating income: "Operating income | $40,558 | $32,718" (us-gaap:OperatingIncomeLoss, page 25; dimensional, so not in the SEC XBRL API).
- **Rows in our parse:**
  - `Operating income | $40,558 | $32,718` (page 25)
- **SEC XBRL API:** not_in_api
- **Filing:** [10-Q, filed 2026-10-28](https://www.sec.gov/Archives/edgar/data/789019/000119312526436133/msft-20260930.htm)
- **ID:** `fresh_hard_MICROSOFT_2027Q1_10Q_breakdown_segment_operating_income`

## 2. PepsiCo: definition, definition_free_cash_flow (dev)

- **Question:** What was PepsiCo's free cash flow in the first nine months of fiscal 2026? Answer in USD millions.
- **Gold:** $5768
- **How:** "Net Cash Provided by Operating Activities" 7,950 - "Capital spending" 2,182 = $5768 (page 4).
- **Rows in our parse:**
  - `Net Cash Provided by Operating Activities | 7,950 | 5,468` (page 4)
  - `Capital spending | (2,182) | (2,499)` (page 4)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-08](https://www.sec.gov/Archives/edgar/data/77476/000007747626000048/pep-20260905.htm)
- **ID:** `fresh_hard_PEPSICO_2026Q3_10Q_definition_free_cash_flow`

## 3. Lockheed Martin: history, history_revenue (dev)

- **Question:** By what percentage did Lockheed Martin's total revenue change from the third quarter of fiscal 2024 to the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place. Use a negative number for a decrease.
- **Gold:** 21.1%
- **How:** 20,711 ("Total sales", page 2) against 1.71e+10 for the third quarter of fiscal 2024 (SEC XBRL API, first reported in 10-Q 0000936468-24-000110, filed 2024-10-22) = 21.089%.
- **Rows in our parse:**
  - `Total sales | 20,711 | 18,609 | 58,795 | 54,727` (page 2)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-22](https://www.sec.gov/Archives/edgar/data/936468/000162828026066928/lmt-20260927.htm)
- **ID:** `fresh_hard_LOCKHEEDMARTIN_2026Q3_10Q_history_revenue`

## 4. 3M: breakdown, breakdown_region_revenue (test)

- **Question:** How much revenue did 3M generate in Asia Pacific in the third quarter of fiscal 2026? Answer in USD millions.
- **Gold:** $1715
- **How:** Asia Pacific revenue: "Asia Pacific | 1,715 | 1,834 | 5,368 | 5,338" (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, page 7; dimensional, so not in the SEC XBRL API).
- **Rows in our parse:**
  - `Asia Pacific | 1,715 | 1,834 | 5,368 | 5,338` (page 7)
- **SEC XBRL API:** not_in_api
- **Filing:** [10-Q, filed 2026-10-20](https://www.sec.gov/Archives/edgar/data/66740/000006674026000311/mmm-20260930.htm)
- **ID:** `fresh_hard_3M_2026Q3_10Q_breakdown_region_revenue`

## 5. Netflix: definition, definition_net_margin (test)

- **Question:** What was Netflix's net profit margin in the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place.
- **Gold:** 28.5%
- **How:** "Net income" 3,663,927 / "Revenues" 12,874,176 = 28.460% (page 2).
- **Rows in our parse:**
  - `Net income | $3,663,927 | $2,546,916 | $12,348,132 | $8,562,680` (page 2)
  - `Revenues | $12,874,176 | $11,510,307 | $37,683,871 | $33,132,274` (page 2)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-21](https://www.sec.gov/Archives/edgar/data/1065280/000106528026000291/nflx-20260930.htm)
- **ID:** `fresh_hard_NETFLIX_2026Q3_10Q_definition_net_margin`

## 6. Block: history, history_revenue (dev)

- **Question:** By what percentage did Block's total revenue change from the third quarter of fiscal 2024 to the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place. Use a negative number for a decrease.
- **Gold:** 12.4%
- **How:** 6,719,359 ("Total net revenue", page 4) against 5.976e+09 for the third quarter of fiscal 2024 (SEC XBRL API, first reported in 10-Q 0001628280-24-046294, filed 2024-11-07) = 12.443%.
- **Rows in our parse:**
  - `Total net revenue | 6,719,359 | 6,114,952 | 19,393,893 | 17,941,205` (page 4)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-11-03](https://www.sec.gov/Archives/edgar/data/1512673/000162828026069524/xyz-20260930.htm)
- **ID:** `fresh_hard_BLOCK_2026Q3_10Q_history_revenue`

## 7. Best Buy: breakdown, breakdown_segment_revenue (test)

- **Question:** What revenue did Best Buy's International segment report in the second quarter of fiscal 2027? Answer in USD millions.
- **Gold:** $709
- **How:** International revenue: "Total International revenue | 709 | 740 | 1,396 | 1,380" (us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax, page 13; dimensional, so not in the SEC XBRL API).
- **Rows in our parse:**
  - `Total International revenue | 709 | 740 | 1,396 | 1,380` (page 13)
- **SEC XBRL API:** not_in_api
- **Filing:** [10-Q, filed 2026-09-04](https://www.sec.gov/Archives/edgar/data/764478/000076447826000042/bby-20260801.htm)
- **ID:** `fresh_hard_BESTBUY_2027Q2_10Q_breakdown_segment_revenue`

## 8. Kraft Heinz: definition, definition_gross_margin (dev)

- **Question:** What was Kraft Heinz's gross margin in the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place.
- **Gold:** 32.5%
- **How:** "Gross profit" 1,988 / "Net sales" 6,115 = 32.510% (page 4).
- **Rows in our parse:**
  - `Gross profit | 1,988 | 1,990 | 6,235 | 6,237` (page 4)
  - `Net sales | $6,115 | $6,237 | $18,424 | $18,588` (page 4)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-11-04](https://www.sec.gov/Archives/edgar/data/1637459/000163745926000081/khc-20260926.htm)
- **ID:** `fresh_hard_KRAFTHEINZ_2026Q3_10Q_definition_gross_margin`

## 9. McDonald's: history, history_revenue (test)

- **Question:** By what percentage did McDonald's total revenue change from the third quarter of fiscal 2024 to the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place. Use a negative number for a decrease.
- **Gold:** 4.1%
- **How:** 7,157 ("Total revenues", page 3) against 6.873e+09 for the third quarter of fiscal 2024 (SEC XBRL API, first reported in 10-Q 0000063908-24-000156, filed 2024-11-07) = 4.132%.
- **Rows in our parse:**
  - `Total revenues | 7,157 | 7,078 | 20,773 | 19,876` (page 3)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-11-06](https://www.sec.gov/Archives/edgar/data/63908/000006390826000083/mcd-20260930.htm)
- **ID:** `fresh_hard_MCDONALDS_2026Q3_10Q_history_revenue`

## 10. Apple: breakdown, breakdown_product_revenue (test)

- **Question:** How much revenue did Apple earn from iPad in fiscal 2026? Answer in USD millions.
- **Gold:** $27929
- **How:** iPad revenue: "iPad | 27,929 | 28,023 | 26,694" (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, page 40; dimensional, so not in the SEC XBRL API).
- **Rows in our parse:**
  - `iPad | 27,929 | 28,023 | 26,694` (page 40)
- **SEC XBRL API:** not_in_api
- **Filing:** [10-K, filed 2026-11-03](https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm)
- **ID:** `fresh_hard_APPLE_2026_10K_breakdown_product_revenue`

## 11. JPMorgan: definition, definition_effective_tax_rate (dev)

- **Question:** What was JPMorgan's effective tax rate in the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place.
- **Gold:** 24.0%
- **How:** "Income tax expense" 4,829 / "Income before income tax expense" 20,134 = 23.984% (page 91).
- **Rows in our parse:**
  - `Income tax expense | 4,829 | 4,350 | 15,175 | 11,412` (page 91)
  - `Income before income tax expense | 20,134 | 18,743 | 68,129 | 55,435` (page 91)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-11-03](https://www.sec.gov/Archives/edgar/data/19617/000162828026069523/jpm-20260930.htm)
- **ID:** `fresh_hard_JPMORGAN_2026Q3_10Q_definition_effective_tax_rate`

## 12. American Express: history, history_net_income (dev)

- **Question:** By what percentage did the net income attributable to American Express change from the third quarter of fiscal 2024 to the third quarter of fiscal 2026? Answer in percent, rounded to one decimal place. Use a negative number for a decrease.
- **Gold:** 48.1%
- **How:** 3,712 ("Net income", page 40) against 2.507e+09 for the third quarter of fiscal 2024 (SEC XBRL API, first reported in 10-Q 0000004962-24-000068, filed 2024-10-18) = 48.065%.
- **Rows in our parse:**
  - `Net income | $3,712 | $2,902` (page 40)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-23](https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm)
- **ID:** `fresh_hard_AMERICANEXPRESS_2026Q3_10Q_history_net_income`

