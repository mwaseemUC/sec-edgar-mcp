# sec-edgar-mcp eval results

- Run: 2026-10-16T00:18:17+00:00  
- Model requested: `claude-sonnet-5-5`; served: `claude-sonnet-5-5`
- Questions: 10

**Headline: 10/10 (100%) grounded correct** - every value correct, present in that question's own successful tool outputs, and the expected tools were called.

| Metric | Value |
|---|---|
| **Grounded correct** | 10/10 (100%) |
| Correct (strict; can be satisfied from memory) | 10/10 (100%) |
| Correct (partial credit) | 100% |
| Cited a tool-returned SEC URL | 100% |
| Used expected tools | 100% |
| Tool calls / question | 2.4 |
| Tokens in / out | 84 / 5,904 |
| Cache write / read tokens | 37,692 / 84,516 |
| Cost | $0.1703 |
| Latency mean / total | 6.6s / 66.5s |

| Q | Label | Expected | Model | OK | Grounded | Cited | Tools | Calls |
|---|---|---|---|---|---|---|---|---|
| q01 | AAPL FY2023 revenue | 383,285,000,000 | 383,285,000,000 | Y | Y | Y | Y | 2 |
| q02 | NVDA FY2024 revenue | 60,922,000,000 | 60,922,000,000 | Y | Y | Y | Y | 5 |
| q02 | AMD FY2024 revenue | 25,785,000,000 | 25,785,000,000 | Y | Y | Y | Y | 5 |
| q02 | NVDA revenue growth FY2022-FY2024 | 126.4 | 126.4 | Y | Y | Y | Y | 5 |
| q02 | AMD revenue growth FY2022-FY2024 | 9.3 | 9.3 | Y | Y | Y | Y | 5 |
| q03 | MSFT FY2024 diluted EPS | 11.8 | 11.8 | Y | Y | Y | Y | 2 |
| q04 | JPM total assets 2023-12-31 | 3,875,393,000,000 | 3,875,393,000,000 | Y | Y | Y | Y | 2 |
| q05 | TSLA FY2023 net income | 14,997,000,000 | 14,997,000,000 | Y | Y | Y | Y | 2 |
| q05 | TSLA FY2024 net income | 7,091,000,000 | 7,091,000,000 | Y | Y | Y | Y | 2 |
| q05 | TSLA net income change FY2023-FY2024 | -52.7 | -52.7 | Y | Y | Y | Y | 2 |
| q06 | GOOGL FY2023 operating income | 84,293,000,000 | 84,293,000,000 | Y | Y | Y | Y | 2 |
| q06 | GOOGL operating income growth FY2022-FY2023 | 12.6 | 12.6 | Y | Y | Y | Y | 2 |
| q07 | MSFT 10-K filed in 2024 accession | 0000950170-24-087843 | 0000950170-24-087843 | Y | Y | Y | Y | 2 |
| q08 | TSLA 10-K/A filed in 2025 accession | 0001104659-25-042659 | 0001104659-25-042659 | Y | Y | Y | Y | 2 |
| q08 | TSLA 10-K/A filed in 2025 filing date | 2025-04-30 | 2025-04-30 | Y | Y | Y | Y | 2 |
| q09 | NVDA FY2024 10-K reportable segments | 2 | 2 | Y | Y | Y | Y | 2 |
| q10 | MSFT employees 2024-06-30 | 228,000 | 228,000 | Y | Y | Y | Y | 3 |
