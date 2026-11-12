"""Han script folding — one canonical form for Traditional and Simplified Chinese.

Financial statements from Hong Kong and Taiwan are printed in **Traditional** characters
(``銷售成本``, ``收益``), while mainland filings and most authored ontologies use
**Simplified** (``销售成本``, ``营业收入``). Byte-comparing those never matches, so a
Simplified alias silently misses every HK filing and vice versa — the label looks identical
to a reader and identical in meaning, but not to ``==``.

Folding both sides to Simplified before matching removes the whole class of miss. This is a
*lexical* normalisation only: it never changes what is stored, displayed, or exported — the
source label is always preserved exactly as printed.

Uses OpenCC when it is installed (full Unihan coverage). Without it, falls back to a built-in
table of the variant pairs that actually occur in financial-statement vocabulary, so the
degraded path still handles real statements instead of failing closed.
"""
from __future__ import annotations

import re

# Index-aligned Traditional -> Simplified pairs, GENERATED with OpenCC over the Han
# characters that actually occur in financial-statement vocabulary (the statement and note
# pages of real filings, plus the shipped templates/ontologies). Generated rather than
# hand-written: a guessed table silently misses characters, and a miss here looks exactly
# like "this concept isn't in the ontology".
#
# ONE PAIR WAS MISSED AND IS ADDED BY HAND: 潤 -> 润 (U+6F64 -> U+6DA6, the Unihan simplified
# variant), the second character of 利潤, "profit". The generating corpus printed profit as 溢利,
# but China SCE, Grand Ming and BYD Electronic print 利潤, so with OpenCC absent "經營利潤" folded
# to "经营利潤", matched the alias 经营利润 nowhere, and the HKFRS operating-profit row bound no
# line (`tests/test_cas_operating_profit_is_not_net_operating_profit.py`).
_TRAD = (
    "並佈佔併來係俬個們備傢債僅僱價億儘償優儲兌內兩冊別則創劃動務勢匯區協參員問單國圍團執報場墊壓壽夠夥實審寬將專尋對導層屬島師帳帶幣廈廢廣強"
    "後從復徵悅慮慶憑應捨採揚換損撥擁擇擔據擬攤數斷於昇時暫書會東條棄業極構樂樓標橋機檢權歷歸決沒況淨減測準溝滙潛潤澤濟濱灣為無營狀獅獨獲獻現環"
    "產異當發盡監眾確碼礎稅種稱積範築簡簽約納級終組結給統經綜維線編緩縮總績織繳繼續義聯聲職脅臨與興舊華萬蓋蘇處號虧術裝製見規視親觀訂計討記設"
    "許註評詞試詩詮該詳認誠說課調談請論證識譯議護譽變讓豐負財貢貨貫責貴買貸費貿賃資賣賦質賬購贖跡躍軍軒較載輔輸轉辦這連週進運過達違遞適遲選還"
    "釋釐針銀銅銷錄錦錯鍵鑑鑼長門開閒間閩閱關闡陸陽階際險離響項須預頒頗頻額類顧顯風養餘饒馬馮駐駿體鳴黃點"
)
_SIMP = (
    "并布占并来系私个们备家债仅雇价亿尽偿优储兑内两册别则创划动务势汇区协参员问单国围团执报场垫压寿够伙实审宽将专寻对导层属岛师帐带币厦废广强"
    "后从复征悦虑庆凭应舍采扬换损拨拥择担据拟摊数断于升时暂书会东条弃业极构乐楼标桥机检权历归决没况净减测准沟汇潜润泽济滨湾为无营状狮独获献现环"
    "产异当发尽监众确码础税种称积范筑简签约纳级终组结给统经综维线编缓缩总绩织缴继续义联声职胁临与兴旧华万盖苏处号亏术装制见规视亲观订计讨记设"
    "许注评词试诗诠该详认诚说课调谈请论证识译议护誉变让丰负财贡货贯责贵买贷费贸赁资卖赋质账购赎迹跃军轩较载辅输转办这连周进运过达违递适迟选还"
    "释厘针银铜销录锦错键鉴锣长门开闲间闽阅关阐陆阳阶际险离响项须预颁颇频额类顾显风养余饶马冯驻骏体鸣黄点"
)
_T2S: dict[str, str] = dict(zip(_TRAD, _SIMP))

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# Resolve the converter once: OpenCC when importable, else the built-in table.
try:  # pragma: no cover - depends on the optional dependency being installed
    from opencc import OpenCC as _OpenCC

    _CC = _OpenCC("t2s")
except Exception:  # noqa: BLE001 - any import/config failure falls back to the table
    _CC = None


def has_han(text: str) -> bool:
    """True when the text contains Han characters (so callers can skip folding Latin-only)."""
    return bool(_CJK.search(text))


def to_simplified(text: str) -> str:
    """Fold Traditional Chinese to Simplified so variants of the same caption compare equal.

    Returns the text unchanged when it has no Han characters, so Latin captions cost nothing.
    """
    if not text or not has_han(text):
        return text
    if _CC is not None:
        try:
            return _CC.convert(text)
        except Exception:  # noqa: BLE001 - a converter failure must not break mapping
            pass
    return "".join(_T2S.get(ch, ch) for ch in text)
