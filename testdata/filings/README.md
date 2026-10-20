# Test filings

Published annual reports kept in the repository so a run can be repeated on the same document.
Each one was downloaded from the issuer's or the exchange's public disclosure site. `_filings/`
stays ignored: it is where client documents are kept locally, and nothing in it is committed.

Which template a filing is tested on follows its region:

- China / Hong Kong: `output_csv_hk_v1_template.json` + `output_csv_hk_line_items.json`
- India: `output_csv_icon_v1_template.json` + `output_csv_icon_line_items.json`

## Reference corpus

The five filings `CLAUDE.md` says every configuration change is measured against.

| file | company | code | period | standard | pages |
| --- | --- | --- | --- | --- | ---: |
| `688008_montage_2024_prc.pdf` | 澜起科技股份有限公司 | 688008.SH | 2024 | CAS | 256 |
| `000709_hesteel_2024_prc.pdf` | 河钢股份有限公司 | 000709.SZ | 2024 | CAS | 208 |
| `300319_maijie_2024_prc.pdf` | 深圳市麦捷微电子科技股份有限公司 | 300319.SZ | 2024 | CAS | 188 |
| `hkex_1966_ar.pdf` | China SCE Group Holdings (中駿集團) | 1966.HK | 2023 | HKFRS | 270 |
| `hkex_kaming_2025_26.pdf` | 佳明集團控股有限公司 | 1271.HK | FY to 31 March 2026 | HKFRS | 162 |

## Further filings

| file | company | code | period | standard | template | answer key |
| --- | --- | --- | --- | --- | --- | --- |
| `000883_hubei_energy_2024_prc.pdf` | 湖北能源集团股份有限公司 | 000883.SZ | 2024 | CAS | HK | `000883_hubei_energy_2024_prc.key.json` |
| `600990_sun_create_2024_prc.pdf` | 四创电子股份有限公司 | 600990.SH | 2024 | CAS | HK | — |
| `688411_hyperstrong_2024_prc.pdf` | 北京海博思创科技股份有限公司 | 688411.SH | 2024 | CAS | HK | — |
| `301577_meixin_2024_prc.pdf` | 广东美信科技股份有限公司 | 301577.SZ | 2024 | CAS | HK | — |
| `300391_changjiang_pharma_2024_prc.pdf` | 长江医药控股股份有限公司 (*ST) | 300391.SZ | 2024 | CAS | HK | — |
| `002273_crystal_optech_2024_prc.pdf` | 浙江水晶光电科技股份有限公司 | 002273.SZ | 2024 | CAS | HK | — |
| `000685_zhongshan_public_utilities_2024_prc.pdf` | 中山公用事业集团股份有限公司 | 000685.SZ | 2024 | CAS | HK | — |
| `300257_kaishan_2024_prc.pdf` | 开山集团股份有限公司 | 300257.SZ | 2024 | CAS | HK | — |
| `hkex_0857_petrochina_2024.pdf` | PetroChina Company Limited | 0857.HK | 2024 | IFRS | HK | — |
| `hkex_0285_byd_electronic_2024.pdf` | BYD Electronic (International) | 0285.HK | 2024 | HKFRS | HK | — |
| `hkex_0191_lai_sun_garment_2024_25.pdf` | Lai Sun Garment (International) | 0191.HK | FY to 31 July 2025 | HKFRS | HK | — |
| `hkex_0160_hon_kwok_land_2023_24.pdf` | Hon Kwok Land Investment | 0160.HK | 2023/24 | HKFRS | HK | — |
| `hkex_3360_far_east_horizon_2024.pdf` | Far East Horizon | 3360.HK | 2024 | HKFRS | HK | — |
| `laurus_labs_2025_26_ind.pdf` | Laurus Labs Limited | NSE: LAURUSLABS | FY 2025-26 | Ind AS | ICON | — |
| `asian_paints_2025_26_ind.pdf` | Asian Paints Limited | NSE: ASIANPAINT | FY 2025-26 | Ind AS | ICON | — |

An answer key gives the consolidated figures the filing prints, with page and caption, in the
template's own sign convention (an expense or a minority share that a rollup deducts is negative).
`null` means the filing prints nothing for that line. A filing with no key yet can still be
captured, linted and audited; the key is what the `report --key` comparison needs.

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
