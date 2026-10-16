# sec-edgar-mcp dry run (oracle tool calls, no Claude)

| Q | Label | OK | Detail |
|---|---|---|---|
| q01 | AAPL FY2023 revenue | Y | 383285000000 found |
| q02 | NVDA FY2024 revenue | Y | 60922000000 found |
| q02 | AMD FY2024 revenue | Y | 25785000000 found |
| q02 | NVDA revenue growth FY2022-FY2024 | Y | inputs 2.6914e+10 and 6.0922e+10 found |
| q02 | AMD revenue growth FY2022-FY2024 | Y | inputs 2.3601e+10 and 2.5785e+10 found |
| q03 | MSFT FY2024 diluted EPS | Y | 11.8 found |
| q04 | JPM total assets 2023-12-31 | Y | 3875393000000 found |
| q05 | TSLA FY2023 net income | Y | 14997000000 found |
| q05 | TSLA FY2024 net income | Y | 7091000000 found |
| q05 | TSLA net income change FY2023-FY2024 | Y | inputs 1.4997e+10 and 7.091e+09 found |
| q06 | GOOGL FY2023 operating income | Y | 84293000000 found |
| q06 | GOOGL operating income growth FY2022-FY2023 | Y | inputs 7.4842e+10 and 8.4293e+10 found |
| q07 | MSFT 10-K filed in 2024 accession | Y | 0000950170-24-087843 found |
| q08 | TSLA 10-K/A filed in 2025 accession | Y | 0001104659-25-042659 found |
| q08 | TSLA 10-K/A filed in 2025 filing date | Y | 2025-04-30 found |
| q09 | NVDA FY2024 10-K reportable segments | Y | evidence found: 'We report our business results in two segments' |
| q10 | MSFT employees 2024-06-30 | Y | evidence found: 'we employed approximately 228,000 people' |

10/10 questions reproducible from tool output.
