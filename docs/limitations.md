# Limitations and assumptions

- Scope is all rows and periods in publicly accessible company-page sections, including investor tabs. Login/paid content and annual-report PDF extraction are outside scope. No date or period count is hard-coded.
- The source shareholding chart does not state a reporting date. Its values therefore use `period: null` with a `period_unstated` warning. No date is inferred from nearby tables. Dated pledging and investor rows retain their own dates.
- The tested HDFC Bank page has no cash-flow section. Its sample is explicitly `partial`, with `cash_flow.status: "absent"`. No replacement provider is used.
- Live checks covered TCS, Infosys, HCL Technologies and HDFC Bank, plus TCS consolidated. Other company layouts may differ. Unknown structure, missing sections and empty tables are reported explicitly.
- Normal installed Google Chrome on Windows is required by the tested setup. Plain HTTP and headless Chrome were blocked during initial tests. Future source restrictions can still prevent access. Headless/server deployment has not been validated.
- Each request must select standalone or consolidated. A mismatched source basis fails; there is no silent fallback. Shareholding has no standalone/consolidated label in the source, so its table basis is null.
- Only explicitly stated units/currency are assigned. Crores are not converted. A stated EPS unit overrides a table's crore unit. Percentages stay in percentage points: `12.5%` becomes `12.5`.
- Integers are JSON integers and decimals are finite JSON floats. Original `raw_text` is retained for checking and exact decimal arithmetic. Unsupported numeric text stays unparsed with a warning, rather than being changed to zero.
- Layout whitespace is collapsed. Source HTML text is retained without applying CSS capitalization; source-truncated labels remain truncated. Duplicate labels are kept as separate rows using their positions.
- The service handles one scrape at a time per process. Chrome stays open until shutdown to reduce startup cost, while request contexts are isolated and closed after use. There is no financial-result cache, automatic retry, scheduler or database.
- The three main company samples were manually spot-checked against source screenshots. An additional automated comparison checked all 1,725 captured value positions across the five samples. This is extraction validation against Finology, not an independent accounting audit or an exhaustive manual review of every cell.
