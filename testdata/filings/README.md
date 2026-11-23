# Test filings

Published annual reports kept in the repository so a run can be repeated on the same document.
Each one was downloaded from the issuer's or the exchange's public disclosure site. `_filings/`
stays ignored: it is where client documents are kept locally, and nothing in it is committed.

Which template a filing is tested on follows its region:

- China / Hong Kong: `output_csv_hk_v1_template.json` + `output_csv_hk_line_items.json`
- India: `output_csv_icon_v1_template.json` + `output_csv_icon_line_items.json`, or the Ind AS
  pair (`output_csv_indas_v1_template.json` + `output_csv_indas_line_items.json`) — the table says
  which a filing is measured on

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
| `000990_chengzhi_2024_prc.pdf` | 诚志股份有限公司 | 000990.SZ | 2024 | CAS | HK | — |
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
| `chemplast_sanmar_2024_25_ind.pdf` | Chemplast Sanmar Limited | NSE: CHEMPLASTS | FY 2024-25 | Ind AS | Ind AS | — |
| `hul_2024_25_ind.pdf` | Hindustan Unilever Limited | NSE: HINDUNILVR | FY 2024-25 | Ind AS | Ind AS | — |
| `jsw_steel_2024_25_ind.pdf` | JSW Steel Limited | NSE: JSWSTEEL | FY 2024-25 | Ind AS | Ind AS | — |
| `indigo_2024_25_ind.pdf` | InterGlobe Aviation Limited (IndiGo) | NSE: INDIGO | FY 2024-25 | Ind AS | Ind AS | — |
| `mukand_2024_25_ind.pdf` | Mukand Limited | NSE: MUKANDLTD | FY 2024-25 | Ind AS | Ind AS | — |

The five FY 2024-25 Indian reports were added together, each for a shape the others do not print:

- **Hindustan Unilever** (235 pages, the 2-up "Integrated Annual Report 2024-25" from hul.co.in)
  prints each statement across the two pages of a spread and titles only the left one: assets on
  the left, equity and liabilities on the right; OCI and earnings per share, and the financing
  cash flows, on the right page.
- **JSW Steel** (383 pages, 2-up, from JSW's investor page) sets its two years so close on a half
  page that, measured against the whole sheet, they read as one column; its cover reads "leading
  millions to follow".
- **Chemplast Sanmar** (272 pages, from chemplastsanmar.com) states its unit as "Indian Rupees in
  Crores" and prints its signatories' membership numbers under the value columns.
- **IndiGo** (322 pages) is a public mirror's copy (files.tijorifinance.com), not the company's or
  the exchange's: goindigo.in did not answer when it was fetched. It is a different edition from
  a 307-page copy run elsewhere.
- **Mukand** (236 pages, from mukand.com) is kept for its text layer: many captions and the
  statement titles are set in a font with no Unicode map, every character shifted by 29 code
  points (`3URSHUW\3ODQW` for "Property Plant"), so little of it maps. It is a different edition
  from a 347-page copy run elsewhere.

**诚志股份** (222 pages, the full 2024 annual report from cninfo.com.cn, announcement of
2025-03-27) was added with 湖北能源 and 四创电子 for the related-party, revenue and
contingent-liability lines: its parent company prints 关联方往来 inside 其他应收款, and its
related-party note lists nineteen guarantees under counterparty names that wrap onto two lines.

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
