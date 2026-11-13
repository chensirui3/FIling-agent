"""A small inline-XBRL filing: hidden header, page breaks, split table cells."""

HEADER = """
<div style="display: none"><ix:header>
  <ix:hidden>
    <ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c-1">2027</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="c-1">Q2</ix:nonNumeric>
  </ix:hidden>
  <ix:resources>
    <xbrli:context id="c-1">
      <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000104169</xbrli:identifier>
      </xbrli:entity>
      <xbrli:period><xbrli:startDate>2026-05-01</xbrli:startDate>
        <xbrli:endDate>2026-07-31</xbrli:endDate></xbrli:period>
    </xbrli:context>
    <xbrli:context id="c-2">
      <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000104169</xbrli:identifier>
        <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >wmt:WalmartUSSegmentMember</xbrldi:explicitMember></xbrli:segment>
      </xbrli:entity>
      <xbrli:period><xbrli:instant>2026-07-31</xbrli:instant></xbrli:period>
    </xbrli:context>
  </ix:resources>
</ix:header></div>
"""

FILING = f"""<?xml version="1.0" encoding="utf-8"?>
<html><head><title>wmt-20260731</title><style>p {{ margin: 0 }}</style></head><body>
{HEADER}
<p>QUARTERLY REPORT</p><p>Walmart Inc.</p>
<hr style="page-break-after: always"/>
<p>Condensed Consolidated Statements of Income</p>
<table>
  <tr><td>(Amounts in millions, except per share data)</td><td></td><td>2026</td></tr>
  <tr><td>Net sales</td><td>$</td><td><ix:nonFraction name="us-gaap:Revenues" contextRef="c-1"
    unitRef="usd" decimals="-6" scale="6" format="ixt:num-dot-decimal">186,100</ix:nonFraction
    >&#160;</td><td></td></tr>
  <tr><td>Interest income</td><td>(</td><td><ix:nonFraction name="us-gaap:InvestmentIncomeInterest"
    contextRef="c-1" unitRef="usd" decimals="-6" scale="6" sign="-">92</ix:nonFraction></td>
    <td>)</td></tr>
  <tr><td>Gross margin</td><td><ix:nonFraction name="x:Margin" contextRef="c-2" unitRef="pure"
    decimals="3" scale="-2">24.5</ix:nonFraction></td><td>%</td></tr>
  <tr><td>Other</td><td><ix:nonFraction name="x:Other" contextRef="c-1" unitRef="usd" scale="6"
    format="ixt:fixed-zero">—</ix:nonFraction></td></tr>
</table>
<div style="page-break-before:always"></div>
<p>Net sales were $<ix:nonFraction name="us-gaap:Revenues" contextRef="c-1" unitRef="usd"
  decimals="-8" scale="9">186.1</ix:nonFraction> billion.</p>
</body></html>
"""
