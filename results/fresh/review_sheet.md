# Fresh benchmark: review sheet

Numeric golds were computed from the filing's tagged (inline-XBRL) figures; *SEC XBRL API* says whether the SEC's API reports the same figures for the same filing and period. Text questions were drafted by Claude from one page and checked automatically (verbatim quote, figures on the page) and by a first-pass review.

To check an item, open the filing, search for the row label or a phrase from the quote (Ctrl+F), and confirm the figure, the period and the answer. Reply with the numbers of any items that are wrong or ambiguous, and why.

## 1. General Mills: derived, current_ratio (test)

- **Question:** What was General Mills' current ratio as of August 30, 2026, calculated as "Total current assets" divided by "Total current liabilities"? Round to two decimal places.
- **Gold:** 0.70
- **How:** "Total current assets" / "Total current liabilities" = 0.7038, using "Total current assets" 4,884.3 (balance sheet, page 5); "Total current liabilities" 6,940.0 (balance sheet, page 5).
- **Rows in our parse:**
  - `Total current assets | 4,884.3 | 4,618.3` (page 5)
  - `Total current liabilities | 6,940.0 | 6,774.1` (page 5)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-09-23](https://www.sec.gov/Archives/edgar/data/40704/000162828026063201/gis-20260830.htm)
- **ID:** `fresh_GENERALMILLS_2027Q1_10Q_current_ratio`

## 2. Adobe: extraction, current_liabilities (test)

- **Question:** What were Adobe's total current liabilities as of August 28, 2026? Answer in USD millions.
- **Gold:** $11888
- **How:** "Total current liabilities" as of August 28, 2026: 11,888 (balance sheet, page 2).
- **Rows in our parse:**
  - `Total current liabilities | 11,888 | 10,200` (page 2)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-09-22](https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm)
- **ID:** `fresh_ADOBE_2026Q3_10Q_current_liabilities`

## 3. Johnson & Johnson: derived, net_margin (test)

- **Question:** What was Johnson & Johnson's net profit margin in the three months ended September 27, 2026, calculated as "Net earnings" divided by "Sales to customers"? Answer in percent, rounded to one decimal place.
- **Gold:** 0.8%
- **How:** "Net earnings" / "Sales to customers" = 0.827%, using "Net earnings" 210 (income statement, page 6); "Sales to customers" 25,399 (income statement, page 6).
- **Rows in our parse:**
  - `Net earnings | $210 | 0.8% | $5,152 | 21.5%` (page 6)
  - `Sales to customers (Note 9) | $25,399 | 100.0% | $23,993 | 100.0%` (page 6)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-21](https://www.sec.gov/Archives/edgar/data/200406/000020040626000197/jnj-20260927.htm)
- **ID:** `fresh_JOHNSON_JOHNSON_2026Q3_10Q_net_margin`

## 4. Ulta Beauty: extraction, capex (test)

- **Question:** How much did Ulta Beauty spend on capital expenditures (purchases of property and equipment) in the six months ended August 1, 2026? Answer in USD thousands.
- **Gold:** $139534
- **How:** "Capital expenditures" in the six months ended August 1, 2026: 139,534 (cash flow statement, page 5).
- **Rows in our parse:**
  - `Capital expenditures | ​ | ​ | (139,534) | ​ | ​ | (155,988)` (page 5)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-08-27](https://www.sec.gov/Archives/edgar/data/1403568/000110465926102442/ulta-20260801x10q.htm)
- **ID:** `fresh_ULTABEAUTY_2026Q2_10Q_capex`

## 5. Amazon: derived, current_ratio (test)

- **Question:** What was Amazon's current ratio as of September 30, 2026, calculated as "Total current assets" divided by "Total current liabilities"? Round to two decimal places.
- **Gold:** 0.99
- **How:** "Total current assets" / "Total current liabilities" = 0.9888, using "Total current assets" 255,100 (balance sheet, page 5); "Total current liabilities" 257,982 (balance sheet, page 5).
- **Rows in our parse:**
  - `Total current assets | 229,083 | 255,100` (page 5)
  - `Total current liabilities | 218,005 | 257,982` (page 5)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-30](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000040/amzn-20260930.htm)
- **ID:** `fresh_AMAZON_2026Q3_10Q_current_ratio`

## 6. PayPal: extraction, revenue (test)

- **Question:** What was PayPal's total revenue in the three months ended September 30, 2026? Answer in USD millions.
- **Gold:** $8584
- **How:** "Net revenues" in the three months ended September 30, 2026: 8,584 (income statement, page 4).
- **Rows in our parse:**
  - `Net revenues | $8,584 | $8,417 | $25,619 | $24,496` (page 4)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-27](https://www.sec.gov/Archives/edgar/data/1633917/000163391726000088/pypl-20260930.htm)
- **ID:** `fresh_PAYPAL_2026Q3_10Q_revenue`

## 7. American Water Works: derived, capex_to_cfo (dev)

- **Question:** What were American Water Works' capital expenditures as a share of its operating cash flow in the nine months ended September 30, 2026, calculated as "Capital expenditures" divided by "Net cash provided by operating activities"? Answer in percent, rounded to one decimal place.
- **Gold:** 147.2%
- **How:** "Capital expenditures" / "Net cash provided by operating activities" = 147.217%, using "Capital expenditures" 2,407 (cash flow statement, page 10); "Net cash provided by operating activities" 1,635 (cash flow statement, page 10).
- **Rows in our parse:**
  - `Capital expenditures | (2,407) | (2,078)` (page 10)
  - `Net cash provided by operating activities | 1,635 | 1,396` (page 10)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-28](https://www.sec.gov/Archives/edgar/data/1410636/000141063626000149/awk-20260930.htm)
- **ID:** `fresh_AMERICANWATERWORKS_2026Q3_10Q_capex_to_cfo`

## 8. Corning: extraction, total_liabilities (test)

- **Question:** What were Corning's total liabilities as of September 30, 2026? Answer in USD millions.
- **Gold:** $19838
- **How:** "Total liabilities" as of September 30, 2026: 19,838 (balance sheet, page 4).
- **Rows in our parse:**
  - `Total liabilities | 19,838 | 18,669` (page 4)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-10-29](https://www.sec.gov/Archives/edgar/data/24741/000002474126000279/glw-20260930.htm)
- **ID:** `fresh_CORNING_2026Q3_10Q_total_liabilities`

## 9. Walmart: derived, shareholder_returns (dev)

- **Question:** How much cash did Walmart return to shareholders in the six months ended July 31, 2026, calculated as "Dividends paid" plus "Purchase of Company stock"? Answer in USD millions.
- **Gold:** $9049
- **How:** "Dividends paid" + "Purchase of Company stock" = $9049, using "Dividends paid" 3,945 (cash flow statement, page 6); "Purchase of Company stock" 5,104 (cash flow statement, page 6).
- **Rows in our parse:**
  - `Dividends paid | (3,945) | (3,755)` (page 6)
  - `Purchase of Company stock | (5,104) | (6,200)` (page 6)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-08-28](https://www.sec.gov/Archives/edgar/data/104169/000010416926000154/wmt-20260731.htm)
- **ID:** `fresh_WALMART_2027Q2_10Q_shareholder_returns`

## 10. Oracle: extraction, cfo (test)

- **Question:** How much net cash did Oracle generate from operating activities in the three months ended August 31, 2026? Answer in USD millions.
- **Gold:** $23103
- **How:** "Net cash provided by operating activities" in the three months ended August 31, 2026: 23,103 (cash flow statement, page 6).
- **Rows in our parse:**
  - `Net cash provided by operating activities | 23,103 | 8,140` (page 6)
- **SEC XBRL API:** verified
- **Filing:** [10-Q, filed 2026-09-11](https://www.sec.gov/Archives/edgar/data/1341439/000119312526389274/orcl-20260831.htm)
- **ID:** `fresh_ORACLE_2027Q1_10Q_cfo`

## 11. Adobe: text, effective tax rate change (test)

- **Question:** What drove the increase in Adobe's effective tax rate for the nine months ended August 28, 2026 compared with the prior-year nine-month period?
- **Gold:** The effective tax rate rose by approximately five percentage points, primarily due to a decrease in the net tax benefit from effects of non-U.S. operations, an increase in net tax expense related to stock-based compensation in the current year, and an increase in the anticipated benefit from a foreign tax asset in the prior year.
- **Quote (page 33):** Our effective tax rate increased by approximately five percentage points for the nine months ended August 28, 2026, as compared to the nine months ended August 29, 2025, primarily due to a decrease in the net tax benefit from effects of non-U.S. operations, an increase in the net tax expense related to stock-based compensation in the current year, and an increase in the anticipated benefit from a foreign tax asset in the prior year.
- **Filing:** [10-Q, filed 2026-09-22](https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm)
- **ID:** `fresh_ADOBE_2026Q3_10Q_text`

## 12. Ulta Beauty: text, net sales drivers (test)

- **Question:** What drove Ulta Beauty's net sales growth in the 26 weeks ended August 1, 2026, and how did ticket and transactions contribute to comparable sales?
- **Gold:** Net sales increased $562.7 million, or 10.0%, primarily due to increased comparable sales, the acquisition of Space NK, and new store sales. The comparable sales increase of 4.6% was driven by a 3.8% increase in average ticket and a 0.8% increase in transactions.
- **Quote (page 23):** Net sales increased $562.7 million, or 10.0%, to $6.2 billion for the 26 weeks ended August 1, 2026, compared to $5.6 billion for the 26 weeks ended August 2, 2025. The net sales increase was primarily due to increased comparable sales, the acquisition of Space NK, and sales from new stores. The comparable sales increase of 4.6% for the 26 weeks ended August 1, 2026 was driven by a 3.8% increase in average ticket and a 0.8% increase in transactions.
- **Filing:** [10-Q, filed 2026-08-27](https://www.sec.gov/Archives/edgar/data/1403568/000110465926102442/ulta-20260801x10q.htm)
- **ID:** `fresh_ULTABEAUTY_2026Q2_10Q_text`

## 13. PayPal: text, consumer loan balance drivers (test)

- **Question:** What drove the change in PayPal's consumer loans and interest receivable balance between September 30, 2025 and September 30, 2026?
- **Gold:** The balance fell 19% to $4.2 billion from $5.2 billion, primarily due to the reclassification of certain Japan installment loans to held for sale in the third quarter of 2026. This was partially offset by growth of approximately $270 million in the U.K. revolving credit portfolio and approximately $170 million in the U.S. interest-bearing installment credit portfolio.
- **Quote (page 54):** The decline in consumer loans and interest receivable outstanding was primarily driven by the impact of the reclassification of certain Japan installment loans to held for sale in the third quarter of 2026, partially offset by growth in our revolving credit product portfolio in the United Kingdom (“U.K.”) and our interest-bearing installment credit product portfolio in the U.S. of approximately $270 million and $170 million, respectively.
- **Filing:** [10-Q, filed 2026-10-27](https://www.sec.gov/Archives/edgar/data/1633917/000163391726000088/pypl-20260930.htm)
- **ID:** `fresh_PAYPAL_2026Q3_10Q_text`

## 14. Corning: text, core net sales drivers (test)

- **Question:** What drove the increase in Corning's core net sales in the three months ended September 30, 2026 compared with the same period in 2025?
- **Gold:** Core net sales rose $809 million to $5.1 billion, primarily driven by increased segment sales of $727 million in Optical Communications and $117 million in Solar.
- **Quote (page 31):** For the three months ended September 30, 2026, we generated core net sales of $5.1 billion compared to $4.3 billion for the same period in 2025. The increase in core net sales of $809 million was primarily driven by increased segment sales of $727 million in Optical Communications and $117 million in Solar.
- **Filing:** [10-Q, filed 2026-10-29](https://www.sec.gov/Archives/edgar/data/24741/000002474126000279/glw-20260930.htm)
- **ID:** `fresh_CORNING_2026Q3_10Q_text`

## 15. Oracle: text, services revenue drivers (test)

- **Question:** What explained the change in Oracle's services revenues in the three months ended August 31, 2026 versus the prior-year quarter?
- **Gold:** Services revenues increased $65 million, attributable to an $88 million increase in consulting services revenues, partially offset by a $23 million decrease in customer success services revenues.
- **Quote (page 27):** In our services business, the increase in revenues was attributable to an $88 million increase in our consulting services revenues, partially offset by a $23 million decrease in our customer success services revenues.
- **Filing:** [10-Q, filed 2026-09-11](https://www.sec.gov/Archives/edgar/data/1341439/000119312526389274/orcl-20260831.htm)
- **ID:** `fresh_ORACLE_2027Q1_10Q_text`

## 16. Coca-Cola: text, segment operating income drivers (test)

- **Question:** What drove the increase in Coca-Cola's Bottling Investments operating income for the nine months ended October 2, 2026 compared with the prior-year period?
- **Gold:** Bottling Investments operating income rose to $487 million from $235 million, primarily driven by a 10% increase in unit case volume, favorable price/mix, lower commodity costs and lower operating expenses, partially offset by increased marketing spending.
- **Quote (page 44):** Bottling Investments’ operating income for the nine months ended October 2, 2026 and September 26, 2025 was $487 million and $235 million, respectively. The increase in operating income was primarily driven by an increase in unit case volume of 10%, favorable price/mix, lower commodity costs and lower operating expenses, partially offset by increased marketing spending.
- **Filing:** [10-Q, filed 2026-10-28](https://www.sec.gov/Archives/edgar/data/21344/000162828026067844/ko-20261002.htm)
- **ID:** `fresh_COCACOLA_2026Q3_10Q_text`

## 17. PG&E Corporation: text, Wildfire Fund expense (test)

- **Question:** Why did PG&E Utility's Wildfire Fund expense increase in the three and nine months ended September 30, 2026 versus the same periods of 2025?
- **Gold:** Wildfire Fund expense increased by $55 million, or 64%, and $98 million, or 36%, primarily due to accelerated amortization associated with SCE’s disclosure of a receivable from the Wildfire Fund related to the Eaton fire.
- **Quote (page 14):** The Utility’s Wildfire Fund expense increased by $55 million, or 64%, and $98 million, or 36%, in the three and nine months ended September 30, 2026, compared to the same periods in 2025. These increases were primarily due to accelerated amortization associated with SCE’s disclosure of a receivable from the Wildfire Fund related to the Eaton fire
- **Filing:** [10-Q, filed 2026-10-22](https://www.sec.gov/Archives/edgar/data/1004980/000100498026000055/pcg-20260930.htm)
- **ID:** `fresh_PG_E_2026Q3_10Q_text`

## 18. Walmart: text, gross margin change (dev)

- **Question:** What drove the change in Walmart U.S. segment gross profit rate in the three months ended July 31, 2026 compared to the prior-year period?
- **Gold:** Gross profit rate increased 158 basis points, primarily driven by tariff refunds, partially offset by price investments and higher fuel costs within the supply chain. It also benefited from continued growth in higher margin businesses, including advertising.
- **Quote (page 21):** Gross profit rate increased 158 and 95 basis points for the three and six months ended July 31, 2026, respectively, when compared to the same periods in the previous fiscal year. The increases were primarily driven by tariff refunds, partially offset by price investments and higher fuel costs within our supply chain. Gross profit rate also benefited from continued growth in higher margin businesses, including advertising.
- **Filing:** [10-Q, filed 2026-08-28](https://www.sec.gov/Archives/edgar/data/104169/000010416926000154/wmt-20260731.htm)
- **ID:** `fresh_WALMART_2027Q2_10Q_text`

## 19. Lockheed Martin: text, segment operating profit drivers (dev)

- **Question:** What drove the increase in Lockheed Martin's Aeronautics segment operating profit in the nine months ended September 27, 2026 compared to the prior-year period?
- **Gold:** Aeronautics operating profit rose $818 million, or 63%, primarily due to the absence of the $950 million reach-forward loss recognized on a classified contract in 2025 and higher sales volume. This was partially offset by higher unfavorable profit adjustments of $165 million on the F-16 program and $70 million on the C-130 program.
- **Quote (page 34):** Aeronautics’ operating profit during the nine months ended September 27, 2026 increased $818 million, or 63%, compared to the same period in 2025. The increase was primarily attributable to the $950 million reach-forward loss recognized on a classified contract in 2025 and the higher sales volume previously described; partially offset by higher unfavorable profit adjustments of $165 million on the F-16 program and $70 million on the C-130 program.
- **Filing:** [10-Q, filed 2026-10-22](https://www.sec.gov/Archives/edgar/data/936468/000162828026066928/lmt-20260927.htm)
- **ID:** `fresh_LOCKHEEDMARTIN_2026Q3_10Q_text`

## 20. American Express: text, credit loss provision drivers (dev)

- **Question:** What drove the change in American Express's provision for Card balance credit losses in the three months ended September 30, 2026 compared with the prior-year period?
- **Gold:** The provision decreased, primarily due to a reserve release in the current period versus a reserve build in the prior period, partially offset by higher net write-offs. The current-period reserve release was primarily driven by enhancements to the models that estimate expected credit losses, partially offset by sequential increases in Card balances.
- **Quote (page 17):** Provision for Card balance credit losses decreased for the three month period, primarily due to a reserve release in the current period versus a reserve build in the prior period, partially offset by higher net write-offs.
- **First-pass note:** The answer's second sentence (model enhancements behind the reserve release) is not in the quote but is on the same page.
- **Filing:** [10-Q, filed 2026-10-23](https://www.sec.gov/Archives/edgar/data/4962/000000496226000409/axp-20260930.htm)
- **ID:** `fresh_AMERICANEXPRESS_2026Q3_10Q_text`

