# Test filings

Published annual reports kept in the repository so a run can be repeated on the same document.
Each one was downloaded from the exchange's public disclosure site. `_filings/` stays ignored: it is
where client documents and the reference corpus are kept locally, and nothing in it is committed.

| file | company | code | year | standard | template and line-item set | answer key |
| --- | --- | --- | --- | --- | --- | --- |
| `000883_hubei_energy_2024_prc.pdf` | 湖北能源集团股份有限公司 (Hubei Energy) | 000883.SZ | 2024 | CAS | `output_csv_hk_v1_template.json` + `output_csv_hk_line_items.json` | `000883_hubei_energy_2024_prc.key.json` |

Which template a filing is tested on follows its region:

- China / Hong Kong: `output_csv_hk_v1_template.json` + `output_csv_hk_line_items.json`
- India: `output_csv_icon_v1_template.json` + `output_csv_icon_line_items.json`

The answer key gives the consolidated figures the filing prints, with page and caption, for about
sixty template lines, in the template's own sign convention (an expense or a minority share that a
rollup deducts is negative). `null` means the filing prints nothing for that line.

## Running the stand-in on a filing

From `backend/` (see `docs/testing/llm-standin.md` for the full procedure):

```powershell
$F = "..\testdata\filings\000883_hubei_energy_2024_prc.pdf"
$D = "..\_standin\000883_hubei_energy_2024_prc__output_csv_hk_v1"
.\.venv\Scripts\python.exe scripts/llm_standin.py capture $F --template output_csv_hk_v1_template.json --set output_csv_hk_line_items.json --out $D --focus all
.\.venv\Scripts\python.exe scripts/llm_standin.py lint $D
.\.venv\Scripts\python.exe scripts/llm_standin.py report $D --key ..\testdata\filings\000883_hubei_energy_2024_prc.key.json
```

`--focus all` captures every line's request; the default `config` captures only the focus lines.
