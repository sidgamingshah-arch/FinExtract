"""Translations for the demo project's dynamic content (en/zh/ar/fr).

A flat map from the English string to its localized forms, applied by the API when a
locale is requested. Statement line-item captions live in demo.LABELS_I18N; this
module covers integrity issues, the review queue (incl. suggested-fix prose), page
classifications, note titles/detail rows, template node config, and statement viewer
copy. Standard professional financial terminology for demonstration — a native
financial-language review is recommended before production.
"""
from __future__ import annotations

TR: dict[str, dict[str, str]] = {
    # ---- Integrity: grade / summary / stat labels & subs ----
    "Fair — proceed with review": {"zh": "一般 — 可在审核下继续", "ar": "مقبول — تابع مع المراجعة", "fr": "Correct — poursuivre avec révision"},
    "3 warnings · 1 blocking resolved": {"zh": "3 项警告 · 1 项阻断已解决", "ar": "3 تحذيرات · تم حل عائق واحد", "fr": "3 avertissements · 1 bloquant résolu"},
    "Pages": {"zh": "页数", "ar": "الصفحات", "fr": "Pages"},
    "Native / Scanned": {"zh": "原生 / 扫描", "ar": "أصلي / ممسوح", "fr": "Natif / Numérisé"},
    "Avg OCR quality": {"zh": "平均 OCR 质量", "ar": "متوسط جودة التعرف الضوئي", "fr": "Qualité OCR moyenne"},
    "Blocking issues": {"zh": "阻断问题", "ar": "مشكلات معيقة", "fr": "Problèmes bloquants"},
    "2 documents": {"zh": "2 个文档", "ar": "مستندان", "fr": "2 documents"},
    "27% scanned": {"zh": "27% 为扫描件", "ar": "27% ممسوح", "fr": "27 % numérisés"},
    "2 pages < 70%": {"zh": "2 页 < 70%", "ar": "صفحتان < 70%", "fr": "2 pages < 70 %"},
    "1 resolved": {"zh": "已解决 1 项", "ar": "تم حل واحدة", "fr": "1 résolu"},
    # Integrity issue titles
    "Low OCR confidence": {"zh": "OCR 置信度低", "ar": "ثقة تعرف ضوئي منخفضة", "fr": "Faible confiance OCR"},
    "Rotated pages": {"zh": "页面旋转", "ar": "صفحات مُدارة", "fr": "Pages pivotées"},
    "Password protected": {"zh": "密码保护", "ar": "محمي بكلمة مرور", "fr": "Protégé par mot de passe"},
    "Possible missing page": {"zh": "可能缺页", "ar": "صفحة مفقودة محتملة", "fr": "Page manquante possible"},
    "Duplicate page": {"zh": "重复页面", "ar": "صفحة مكررة", "fr": "Page en double"},
    "Currency & units detected": {"zh": "已检测币种与单位", "ar": "تم اكتشاف العملة والوحدات", "fr": "Devise et unités détectées"},
    # Integrity issue details
    "Faint scan, handwritten annotations": {"zh": "扫描模糊，含手写批注", "ar": "مسح باهت مع تعليقات مكتوبة بخط اليد", "fr": "Numérisation pâle, annotations manuscrites"},
    "Landscape schedules rotated 90°": {"zh": "横向明细表旋转 90°", "ar": "جداول أفقية مُدارة 90°", "fr": "Annexes en paysage pivotées de 90°"},
    "Owner password on source PDF": {"zh": "源 PDF 设有所有者密码", "ar": "كلمة مرور المالك على ملف PDF المصدر", "fr": "Mot de passe propriétaire sur le PDF source"},
    "Cash-flow statement continues off-sequence": {"zh": "现金流量表接续页序不连贯", "ar": "قائمة التدفقات النقدية تستمر خارج التسلسل", "fr": "Le tableau des flux se poursuit hors séquence"},
    "Balance sheet appears twice": {"zh": "资产负债表出现两次", "ar": "الميزانية العمومية تظهر مرتين", "fr": "Le bilan apparaît deux fois"},
    "Rupees in crore stated in header": {"zh": "表头注明单位为千万卢比（crore）", "ar": "الروبية بالكرور مذكورة في الترويسة", "fr": "Roupies en crore indiquées dans l'en-tête"},
    # Integrity issue notes (DETAIL column) + statuses
    "OCR quality 62%": {"zh": "OCR 质量 62%", "ar": "جودة التعرف الضوئي 62%", "fr": "Qualité OCR 62 %"},
    "Auto-corrected": {"zh": "已自动校正", "ar": "تم التصحيح تلقائيًا", "fr": "Corrigé automatiquement"},
    "Unlocked on import": {"zh": "导入时已解锁", "ar": "أُلغي القفل عند الاستيراد", "fr": "Déverrouillé à l'import"},
    "Verify p.150": {"zh": "核对第 150 页", "ar": "تحقق من الصفحة 150", "fr": "Vérifier p.150"},
    "Using p.142": {"zh": "使用第 142 页", "ar": "استخدام الصفحة 142", "fr": "Utilise p.142"},
    "INR · Crore": {"zh": "印度卢比 · 千万", "ar": "روبية هندية · كرور", "fr": "INR · Crore"},
    "Warning": {"zh": "警告", "ar": "تحذير", "fr": "Avertissement"},
    "Resolved": {"zh": "已解决", "ar": "تم الحل", "fr": "Résolu"},
    "OK": {"zh": "正常", "ar": "سليم", "fr": "OK"},

    # ---- Review queue ----
    "Balance sheet does not balance": {"zh": "资产负债表不平衡", "ar": "الميزانية العمومية غير متوازنة", "fr": "Le bilan n'est pas équilibré"},
    "Section subtotal mismatch — Non-current assets": {"zh": "分部小计不符 — 非流动资产", "ar": "عدم تطابق المجموع الفرعي — الأصول غير المتداولة", "fr": "Écart de sous-total — Actifs non courants"},
    "Sign anomaly — Finance costs positive": {"zh": "符号异常 — 财务费用为正", "ar": "شذوذ في الإشارة — تكاليف التمويل موجبة", "fr": "Anomalie de signe — Charges financières positives"},
    "Deposits paid for land acquisition": {"zh": "购地已付定金", "ar": "دفعات مقدمة لشراء أرض", "fr": "Acomptes versés pour acquisition de terrain"},
    "Consolidated · Assets vs Equity & Liabilities": {"zh": "合并 · 资产 对 权益与负债", "ar": "موحّد · الأصول مقابل حقوق الملكية والالتزامات", "fr": "Consolidé · Actif vs Capitaux propres et passif"},
    "Extracted 7,49,830 vs calculated 7,48,590": {"zh": "提取值 7,49,830 对 计算值 7,48,590", "ar": "المستخرج 7,49,830 مقابل المحسوب 7,48,590", "fr": "Extrait 7,49,830 vs calculé 7,48,590"},
    "Statement of P&L · expense shown as credit": {"zh": "利润表 · 费用列示为贷方", "ar": "قائمة الأرباح والخسائر · مصروف مُدرج كدائن", "fr": "Compte de résultat · charge présentée en crédit"},
    "Consolidated · p.142 · Non-current assets": {"zh": "合并 · 第142页 · 非流动资产", "ar": "موحّد · ص.142 · الأصول غير المتداولة", "fr": "Consolidé · p.142 · Actifs non courants"},
    "Unmapped": {"zh": "未映射", "ar": "غير مطابق", "fr": "Non rattaché"},
    "Blocking": {"zh": "阻断", "ar": "معيق", "fr": "Bloquant"},
    "High": {"zh": "高", "ar": "عالٍ", "fr": "Élevé"},
    "Medium": {"zh": "中", "ar": "متوسط", "fr": "Moyen"},
    "The 1,240 cr related-party receivable was netted from Trade receivables but not removed from Other financial assets. Apply the Note 12.3 netting rule to Other financial assets.": {
        "zh": "1,240 千万卢比的关联方应收款已从应收账款中抵减，但未从其他金融资产中剔除。请对其他金融资产应用附注 12.3 的抵减规则。",
        "ar": "تم صافي مبلغ 1,240 كرور من الذمم المدينة للأطراف ذات العلاقة من الذمم المدينة التجارية لكنه لم يُزَل من الأصول المالية الأخرى. طبّق قاعدة الصافي في الإيضاح 12.3 على الأصول المالية الأخرى.",
        "fr": "La créance de parties liées de 1 240 cr a été nettée des créances clients mais non retirée des autres actifs financiers. Appliquez la règle de nettage de la Note 12.3 aux autres actifs financiers.",
    },
    "A duplicated Loans line (Note 7) is counted in both current and non-current. Reassign the 1,240 cr to current per note reference.": {
        "zh": "一笔重复的贷款项目（附注 7）在流动和非流动中被重复计入。请根据附注引用将 1,240 千万卢比重新归类为流动。",
        "ar": "بند قروض مكرر (الإيضاح 7) محتسب ضمن المتداول وغير المتداول معًا. أعد تخصيص 1,240 كرور إلى المتداول وفقًا لمرجع الإيضاح.",
        "fr": "Une ligne Prêts dupliquée (Note 7) est comptée en courant et non courant. Réaffectez les 1 240 cr au courant selon la référence de note.",
    },
    # KEYED ON THE ENGLISH SOURCE STRING, so this entry and demo.REVIEW's `fix` change together or
    # the card falls back to English mid-sentence in three languages. The word "ontology" is gone
    # from the localized text too, not just the key: line items are the single configuration engine
    # and there is no ontology for a reader in any language to be shown.
    "Line-item sign rule for Finance costs is expense = negative. Flip sign to −18,400 to match statement convention.": {
        "zh": "财务费用行项目的符号规则为 费用 = 负值。请将符号改为 −18,400 以符合报表惯例。",
        "ar": "قاعدة إشارة البند لتكاليف التمويل هي مصروف = سالب. اقلب الإشارة إلى −18,400 لمطابقة عرف القائمة.",
        "fr": "La règle de signe du poste pour les charges financières est charge = négatif. Inversez le signe en −18 400 pour respecter la convention.",
    },
    "The rulebook placed this caption on a concept this template puts on no statement, so the figure appears on no line. Pick the correct template line item, or confirm the template needs the concept added.": {
        "zh": "规则手册将该标签映射到本模板未在任何报表中列示的概念，因此该数字未出现在任何行上。请选择正确的模板行项目，或确认模板需要新增该概念。",
        "ar": "أسند كتاب القواعد هذه التسمية إلى مفهوم لا يضعه هذا القالب في أي قائمة، فلا يظهر الرقم في أي سطر. اختر بند القالب الصحيح، أو أكّد أن القالب بحاجة إلى إضافة المفهوم.",
        "fr": "Le référentiel a rattaché ce libellé à un concept que ce modèle ne place sur aucun état : le montant n'apparaît donc sur aucune ligne. Choisissez le bon poste du modèle, ou confirmez que le concept doit y être ajouté.",
    },
    # Review reconciliation calc-row labels
    "Total assets": {"zh": "资产总计", "ar": "إجمالي الأصول", "fr": "Total de l'actif"},
    "Total equity & liabilities": {"zh": "权益及负债总计", "ar": "إجمالي حقوق الملكية والالتزامات", "fr": "Total capitaux propres et passif"},
    "Difference": {"zh": "差异", "ar": "الفرق", "fr": "Écart"},
    "Sum of extracted line items": {"zh": "提取项目合计", "ar": "مجموع البنود المستخرجة", "fr": "Somme des postes extraits"},
    "Reported subtotal": {"zh": "报告小计", "ar": "المجموع الفرعي المُبلَّغ", "fr": "Sous-total déclaré"},
    "Extracted value": {"zh": "提取值", "ar": "القيمة المستخرجة", "fr": "Valeur extraite"},
    "Expected sign (expense)": {"zh": "预期符号（费用）", "ar": "الإشارة المتوقعة (مصروف)", "fr": "Signe attendu (charge)"},
    "negative": {"zh": "负值", "ar": "سالب", "fr": "négatif"},
    "Line-item rule": {"zh": "行项目规则", "ar": "قاعدة البند", "fr": "Règle de poste"},
    "debit / negative": {"zh": "借方 / 负值", "ar": "مدين / سالب", "fr": "débit / négatif"},
    "Source label": {"zh": "原始标签", "ar": "التسمية الأصلية", "fr": "Libellé source"},
    "Mapped to": {"zh": "映射到", "ar": "مطابق إلى", "fr": "Rattaché à"},
    "Value": {"zh": "数值", "ar": "القيمة", "fr": "Valeur"},
    # Review chips. Two, matching the real route's, because the queue carries two shapes of
    # finding: the accounting checks and the one row-shaped kind. "Subtotals", "Sign anomalies"
    # and "Note reconciliation" were chips for types the real route does not serve, and went with
    # the sample's own check vocabulary.
    "All": {"zh": "全部", "ar": "الكل", "fr": "Tous"},
    "Checks": {"zh": "校验", "ar": "الفحوص", "fr": "Contrôles"},

    # ---- Page scope ----
    "Statement of P&L": {"zh": "利润表", "ar": "قائمة الأرباح والخسائر", "fr": "Compte de résultat"},
    "Cash Flow": {"zh": "现金流量表", "ar": "قائمة التدفقات النقدية", "fr": "Tableau des flux"},
    "Balance Sheet": {"zh": "资产负债表", "ar": "الميزانية العمومية", "fr": "Bilan"},
    "Notes 1-4": {"zh": "附注 1-4", "ar": "الإيضاحات 1-4", "fr": "Notes 1-4"},
    "Note 12": {"zh": "附注 12", "ar": "الإيضاح 12", "fr": "Note 12"},
    "Other": {"zh": "其他", "ar": "أخرى", "fr": "Autre"},
    "Duplicate": {"zh": "重复", "ar": "مكرر", "fr": "Doublon"},
    "Consolidated": {"zh": "合并", "ar": "موحّد", "fr": "Consolidé"},
    "Standalone": {"zh": "单体", "ar": "منفصل", "fr": "Individuel"},
    "PPE, CWIP": {"zh": "固定资产、在建工程", "ar": "ممتلكات وآلات، أعمال قيد التنفيذ", "fr": "Immob. corp., en cours"},
    "Trade receivables": {"zh": "应收账款", "ar": "الذمم المدينة التجارية", "fr": "Créances clients"},
    "Directors report": {"zh": "董事会报告", "ar": "تقرير مجلس الإدارة", "fr": "Rapport des administrateurs"},
    "Balance sheet (dup)": {"zh": "资产负债表（重复）", "ar": "الميزانية العمومية (مكرر)", "fr": "Bilan (doublon)"},
    "P&L": {"zh": "利润表", "ar": "الأرباح والخسائر", "fr": "Résultat"},
    "Notes": {"zh": "附注", "ar": "الإيضاحات", "fr": "Notes"},
    "Excluded": {"zh": "已排除", "ar": "مستبعد", "fr": "Exclu"},
    # The four Page Scope filter chips (app/services/page_scope.py FILTER_LABELS). "Notes" and
    # "Other" are already above; these two are the labels that set replaced.
    "All pages": {"zh": "全部页面", "ar": "كل الصفحات", "fr": "Toutes les pages"},
    "Face": {"zh": "表内", "ar": "وجه القوائم", "fr": "Face"},

    # ---- Notes ----
    "Property, plant & equipment": {"zh": "物业、厂房及设备", "ar": "الممتلكات والآلات والمعدات", "fr": "Immobilisations corporelles"},
    "Capital work-in-progress": {"zh": "在建工程", "ar": "أعمال رأسمالية قيد التنفيذ", "fr": "Immobilisations en cours"},
    "Goodwill & intangibles": {"zh": "商誉及无形资产", "ar": "الشهرة والأصول غير الملموسة", "fr": "Goodwill et incorporels"},
    "Non-current investments": {"zh": "非流动投资", "ar": "استثمارات غير متداولة", "fr": "Placements non courants"},
    "Loans": {"zh": "贷款", "ar": "قروض", "fr": "Prêts"},
    "Inventories": {"zh": "存货", "ar": "المخزون", "fr": "Stocks"},
    "Current investments": {"zh": "流动投资", "ar": "استثمارات متداولة", "fr": "Placements courants"},
    "Cash & bank balances": {"zh": "现金及银行存款", "ar": "النقد والأرصدة البنكية", "fr": "Trésorerie et soldes bancaires"},
    "Other equity": {"zh": "其他权益", "ar": "حقوق ملكية أخرى", "fr": "Autres capitaux propres"},
    "Borrowings": {"zh": "借款", "ar": "القروض", "fr": "Emprunts"},
    "Trade payables": {"zh": "应付账款", "ar": "الذمم الدائنة التجارية", "fr": "Dettes fournisseurs"},
    "Trade Receivables": {"zh": "应收账款", "ar": "الذمم المدينة التجارية", "fr": "Créances clients"},
    # Note 12 detail rows
    "Trade receivables - considered good": {"zh": "应收账款 — 视为良好", "ar": "ذمم مدينة تجارية — تُعتبر جيدة", "fr": "Créances clients — jugées saines"},
    "Receivables - significant increase in credit risk": {"zh": "应收款 — 信用风险显著增加", "ar": "ذمم مدينة — زيادة كبيرة في مخاطر الائتمان", "fr": "Créances — hausse notable du risque de crédit"},
    "Receivables - credit impaired": {"zh": "应收款 — 已发生信用减值", "ar": "ذمم مدينة — منخفضة القيمة ائتمانيًا", "fr": "Créances — dépréciées"},
    "Less: allowance for expected credit loss": {"zh": "减：预期信用损失准备", "ar": "ناقص: مخصص خسائر الائتمان المتوقعة", "fr": "Moins : provision pour pertes de crédit attendues"},
    "Less: related-party receivables (Note 12.3)": {"zh": "减：关联方应收款（附注 12.3）", "ar": "ناقص: ذمم مدينة لأطراف ذات علاقة (الإيضاح 12.3)", "fr": "Moins : créances de parties liées (Note 12.3)"},
    "Net - carried to face of Balance Sheet": {"zh": "净额 — 计入资产负债表表面", "ar": "الصافي — المحمول إلى وجه الميزانية العمومية", "fr": "Net — porté au bilan"},
    "Current assets → Trade receivables": {"zh": "流动资产 → 应收账款", "ar": "الأصول المتداولة ← الذمم المدينة التجارية", "fr": "Actifs courants → Créances clients"},
    "Note total ₹96,900 cr less related-party receivables of ₹12,400 cr (Note 12.3, also carried under Other financial assets) = ₹84,500 cr reported on the face of the Balance Sheet.": {
        "zh": "附注合计 ₹96,900 千万卢比，减去关联方应收款 ₹12,400 千万卢比（附注 12.3，亦列入其他金融资产）= 在资产负债表表面列报 ₹84,500 千万卢比。",
        "ar": "إجمالي الإيضاح ₹96,900 كرور ناقص الذمم المدينة للأطراف ذات العلاقة ₹12,400 كرور (الإيضاح 12.3، والمُدرجة أيضًا ضمن الأصول المالية الأخرى) = ₹84,500 كرور المُبلَّغ عنها على وجه الميزانية العمومية.",
        "fr": "Total de la note 96 900 cr moins les créances de parties liées de 12 400 cr (Note 12.3, également portées en autres actifs financiers) = 84 500 cr présentés au bilan.",
    },

    # ---- Template node config ----
    "Current assets → Financial assets": {"zh": "流动资产 → 金融资产", "ar": "الأصول المتداولة ← الأصول المالية", "fr": "Actifs courants → Actifs financiers"},
    "As reported (positive)": {"zh": "按列报（正值）", "ar": "كما هو مُبلَّغ (موجب)", "fr": "Tel que déclaré (positif)"},
    "Expense / contra — negative": {"zh": "费用 / 抵减 — 负值", "ar": "مصروف / مقابل — سالب", "fr": "Charge / contrepartie — négatif"},
    "Auto-detect from context": {"zh": "根据上下文自动检测", "ar": "كشف تلقائي من السياق", "fr": "Détection automatique selon le contexte"},
    "Monetary · ₹ Crore": {"zh": "货币 · ₹ 千万", "ar": "نقدي · ₹ كرور", "fr": "Monétaire · ₹ Crore"},
    "Leaf — sum of children": {"zh": "叶子 — 子项之和", "ar": "ورقة — مجموع البنود الفرعية", "fr": "Feuille — somme des enfants"},
    "When a line item is fetched from a note and an overarching item on the face references that note, subtract the overlapping detail so totals stay aligned.": {
        "zh": "当某一项目取自附注、而表面上引用该附注的总括项目时，需减去重叠的明细，以保持合计的一致。",
        "ar": "عندما يُستخرج بند من إيضاح ويشير بند شامل على الوجه إلى ذلك الإيضاح، اطرح التفصيل المتداخل لتبقى المجاميع متسقة.",
        "fr": "Lorsqu'un poste provient d'une note et qu'un poste global au bilan référence cette note, soustrayez le détail redondant pour aligner les totaux.",
    },

    # ---- Statement viewer subtitles + callouts ----
    "Consolidated Balance Sheet as at 31 March 2025": {"zh": "合并资产负债表（截至 2025 年 3 月 31 日）", "ar": "الميزانية العمومية الموحّدة كما في 31 مارس 2025", "fr": "Bilan consolidé au 31 mars 2025"},
    "Consolidated Statement of Profit and Loss for the year ended 31 March 2025": {"zh": "合并利润表（截至 2025 年 3 月 31 日止年度）", "ar": "قائمة الأرباح والخسائر الموحّدة للسنة المنتهية في 31 مارس 2025", "fr": "Compte de résultat consolidé pour l'exercice clos le 31 mars 2025"},
    "Consolidated Statement of Cash Flows for the year ended 31 March 2025": {"zh": "合并现金流量表（截至 2025 年 3 月 31 日止年度）", "ar": "قائمة التدفقات النقدية الموحّدة للسنة المنتهية في 31 مارس 2025", "fr": "Tableau des flux de trésorerie consolidé pour l'exercice clos le 31 mars 2025"},
    "↳ Linked to Note 12 — Trade receivables (p.171). Face value shown net of ₹12,400 cr related-party receivables reclassified under Note 12.3.": {
        "zh": "↳ 关联附注 12 — 应收账款（第 171 页）。表面值已扣除 ₹12,400 千万卢比关联方应收款（重分类至附注 12.3）。",
        "ar": "↳ مرتبط بالإيضاح 12 — الذمم المدينة التجارية (ص.171). القيمة الظاهرة صافية من ₹12,400 كرور ذمم أطراف ذات علاقة أُعيد تصنيفها ضمن الإيضاح 12.3.",
        "fr": "↳ Lié à la Note 12 — Créances clients (p.171). Valeur au bilan nette de 12 400 cr de créances de parties liées reclassées en Note 12.3.",
    },
    # Keyed on the English callout in api/routes/projects.py::_VIEWER — changed there in lockstep.
    "↳ Finance costs (Note 25) flagged: extracted as a credit; the line-item sign rule expects an expense (negative).": {
        "zh": "↳ 财务费用（附注 25）已标记：提取为贷方；行项目符号规则预期为费用（负值）。",
        "ar": "↳ تكاليف التمويل (الإيضاح 25) موسومة: مُستخرجة كدائن؛ قاعدة إشارة البند تتوقع مصروفًا (سالبًا).",
        "fr": "↳ Charges financières (Note 25) signalées : extraites en crédit ; la règle de signe du poste attend une charge (négatif).",
    },
    "↳ Closing cash ties to Note 13 (Cash & bank balances) and the face of the Balance Sheet.": {
        "zh": "↳ 期末现金与附注 13（现金及银行存款）以及资产负债表表面相衔接。",
        "ar": "↳ النقد الختامي يتطابق مع الإيضاح 13 (النقد والأرصدة البنكية) ووجه الميزانية العمومية.",
        "fr": "↳ La trésorerie de clôture se rattache à la Note 13 (Trésorerie et soldes bancaires) et au bilan.",
    },

    # ---- Financial-analysis commentary ----
    "Current ratio": {"zh": "流动比率", "ar": "نسبة التداول", "fr": "Ratio de liquidité générale"},
    "Debt-to-equity": {"zh": "债务权益比", "ar": "نسبة الدين إلى حقوق الملكية", "fr": "Ratio dette/capitaux propres"},
    "Equity ratio": {"zh": "权益比率", "ar": "نسبة حقوق الملكية", "fr": "Ratio de capitaux propres"},
    "Interest coverage": {"zh": "利息保障倍数", "ar": "تغطية الفوائد", "fr": "Couverture des intérêts"},
    "Net margin (PBT)": {"zh": "净利率（税前）", "ar": "الهامش (قبل الضريبة)", "fr": "Marge (avant impôt)"},
    "Revenue growth (YoY)": {"zh": "收入同比增长", "ar": "نمو الإيرادات (سنويًا)", "fr": "Croissance du CA (annuelle)"},
    "Cash ratio": {"zh": "现金比率", "ar": "نسبة النقدية", "fr": "Ratio de liquidité immédiate"},
    "Asset turnover": {"zh": "资产周转率", "ar": "معدل دوران الأصول", "fr": "Rotation de l'actif"},
    "Well-capitalised balance sheet with low leverage; profitability is improving.": {
        "zh": "资本充足、杠杆较低的资产负债表；盈利能力正在改善。",
        "ar": "ميزانية جيدة الرسملة برافعة مالية منخفضة؛ الربحية في تحسّن.",
        "fr": "Bilan bien capitalisé à faible levier ; la rentabilité s'améliore.",
    },
    "Sound fundamentals overall, with near-term liquidity and open review items to watch.": {
        "zh": "总体基本面稳健，但需关注短期流动性及待审核事项。",
        "ar": "أساسيات سليمة إجمالًا، مع مراقبة السيولة قصيرة الأجل وبنود المراجعة المفتوحة.",
        "fr": "Fondamentaux sains dans l'ensemble ; à surveiller : la liquidité à court terme et les points de révision ouverts.",
    },
    "Elevated risks — leverage and/or liquidity warrant close attention.": {
        "zh": "风险偏高——杠杆和/或流动性需密切关注。",
        "ar": "مخاطر مرتفعة — تستدعي الرافعة و/أو السيولة اهتمامًا وثيقًا.",
        "fr": "Risques élevés — le levier et/ou la liquidité exigent une attention particulière.",
    },
    "The entity shows a well-capitalised balance sheet with comfortably low leverage and improving revenues, and earnings that comfortably service finance costs. The main watch items are near-term liquidity and unresolved review flags that affect some reported figures.": {
        "zh": "该主体拥有资本充足的资产负债表，杠杆水平较低且收入不断增长，盈利足以覆盖财务费用。主要需关注的是短期流动性以及影响部分列报数据的未决审核事项。",
        "ar": "تُظهر المنشأة ميزانية جيدة الرسملة برافعة مالية منخفضة مريحة وإيرادات متحسّنة، وأرباحًا تغطّي تكاليف التمويل بأريحية. أبرز نقاط المراقبة هي السيولة قصيرة الأجل وإشارات المراجعة غير المحسومة التي تؤثر على بعض الأرقام المُبلَّغة.",
        "fr": "L'entité présente un bilan bien capitalisé, à levier confortablement faible, des revenus en progression et des bénéfices couvrant aisément les charges financières. Les principaux points de vigilance sont la liquidité à court terme et les signalements de révision non résolus affectant certains chiffres.",
    },
    "Revenue grew year-on-year, indicating healthy topline momentum.": {
        "zh": "收入同比增长，显示营收势头良好。", "ar": "نمت الإيرادات سنويًا، مما يشير إلى زخم صحي في الإيرادات.", "fr": "Le chiffre d'affaires progresse d'une année sur l'autre, signe d'une dynamique saine.",
    },
    "Low debt-to-equity — the balance sheet is conservatively financed.": {
        "zh": "债务权益比低——资产负债表融资稳健。", "ar": "نسبة دين إلى حقوق ملكية منخفضة — تمويل الميزانية متحفّظ.", "fr": "Faible ratio dette/capitaux propres — bilan financé de façon prudente.",
    },
    "Strong interest coverage; earnings comfortably service finance costs.": {
        "zh": "利息保障倍数高；盈利足以覆盖财务费用。", "ar": "تغطية فوائد قوية؛ الأرباح تغطّي تكاليف التمويل بأريحية.", "fr": "Forte couverture des intérêts ; les bénéfices couvrent aisément les charges financières.",
    },
    "Equity funds a large share of assets, providing a solid capital cushion.": {
        "zh": "权益为资产提供了较大占比的资金，形成稳固的资本缓冲。", "ar": "تموّل حقوق الملكية حصة كبيرة من الأصول، ما يوفّر وسادة رأسمالية متينة.", "fr": "Les capitaux propres financent une large part de l'actif, offrant un solide coussin de capital.",
    },
    "Current ratio above 1.5 — the working-capital position is adequate.": {
        "zh": "流动比率高于 1.5——营运资金状况充足。", "ar": "نسبة التداول أعلى من 1.5 — وضع رأس المال العامل ملائم.", "fr": "Ratio de liquidité supérieur à 1,5 — position de fonds de roulement adéquate.",
    },
    "Healthy pre-tax margin relative to total income.": {
        "zh": "相对于总收入，税前利润率良好。", "ar": "هامش قبل الضريبة صحي مقارنةً بإجمالي الدخل.", "fr": "Marge avant impôt saine par rapport au total des produits.",
    },
    "Low cash ratio — immediate liquidity is thin relative to current liabilities.": {
        "zh": "现金比率低——相对于流动负债，即时流动性偏薄。", "ar": "نسبة نقدية منخفضة — السيولة الفورية ضعيفة مقارنةً بالالتزامات المتداولة.", "fr": "Faible ratio de liquidité immédiate — trésorerie mince face aux passifs courants.",
    },
    "Negative working-capital movement weighed on operating cash flow.": {
        "zh": "营运资金变动为负，拖累了经营现金流。", "ar": "حركة سلبية في رأس المال العامل أثّرت على التدفق النقدي التشغيلي.", "fr": "Une variation négative du fonds de roulement a pesé sur les flux d'exploitation.",
    },
    "Goodwill on the balance sheet carries impairment risk if performance weakens.": {
        "zh": "资产负债表上的商誉在业绩走弱时存在减值风险。", "ar": "الشهرة في الميزانية تحمل مخاطر انخفاض القيمة إذا ضعف الأداء.", "fr": "Le goodwill au bilan présente un risque de dépréciation en cas de baisse de performance.",
    },
    "Open review items — including a balance-sheet discrepancy and a finance-cost sign anomaly — mean some reported figures are provisional pending sign-off.": {
        "zh": "存在待审核事项——包括资产负债表差异和财务费用符号异常——意味着部分列报数据在获批前为暂定。",
        "ar": "بنود مراجعة مفتوحة — تشمل تباينًا في الميزانية وشذوذًا في إشارة تكلفة التمويل — تعني أن بعض الأرقام المُبلَّغة مؤقتة بانتظار الاعتماد.",
        "fr": "Des points de révision ouverts — dont un écart de bilan et une anomalie de signe sur les charges financières — rendent certains chiffres provisoires en attente de validation.",
    },
    "Trade receivables are a large share of current assets; monitor collection risk.": {
        "zh": "应收账款占流动资产比重较大；需关注回收风险。", "ar": "تمثّل الذمم المدينة التجارية حصة كبيرة من الأصول المتداولة؛ راقب مخاطر التحصيل.", "fr": "Les créances clients représentent une large part de l'actif courant ; surveiller le risque de recouvrement.",
    },
    "This summary is generated from extracted figures with checks still open in the review queue; confirm flagged items before relying on the numbers.": {
        "zh": "本摘要由提取数据生成，审核队列中仍有待处理的检查；在依赖这些数字前请确认已标记的事项。",
        "ar": "أُنشئ هذا الملخّص من أرقام مستخرجة مع وجود فحوصات لا تزال مفتوحة في قائمة المراجعة؛ أكّد البنود الموسومة قبل الاعتماد على الأرقام.",
        "fr": "Ce résumé est généré à partir de chiffres extraits, des contrôles restant ouverts dans la file de révision ; confirmez les éléments signalés avant de vous fier aux chiffres.",
    },
    "consolidated · FY25 vs FY24 · ₹ crore": {
        "zh": "合并 · FY25 对 FY24 · ₹ 千万", "ar": "موحّد · FY25 مقابل FY24 · ₹ كرور", "fr": "consolidé · FY25 vs FY24 · ₹ crore",
    },
    # ---- Commentary: year-on-year trend labels ----
    "Revenue": {"zh": "营业收入", "ar": "الإيرادات", "fr": "Chiffre d'affaires"},
    "Profit before tax": {"zh": "税前利润", "ar": "الربح قبل الضريبة", "fr": "Résultat avant impôt"},
    "Net margin": {"zh": "净利率", "ar": "صافي الهامش", "fr": "Marge nette"},
    "Operating cash flow": {"zh": "经营现金流", "ar": "التدفق النقدي التشغيلي", "fr": "Flux de trésorerie d'exploitation"},
    "Equity": {"zh": "权益", "ar": "حقوق الملكية", "fr": "Capitaux propres"},
    # ---- Commentary: trend-derived strengths ----
    "Leverage reduced year-on-year, further strengthening an already conservative balance sheet.": {
        "zh": "杠杆同比下降，进一步巩固了本已稳健的资产负债表。",
        "ar": "انخفضت الرافعة المالية سنويًا، مما عزّز ميزانية متحفّظة أصلًا.",
        "fr": "Le levier a diminué d'une année sur l'autre, renforçant un bilan déjà prudent.",
    },
    "Margins expanded year-on-year, with pre-tax profit growing faster than revenue.": {
        "zh": "利润率同比扩大，税前利润增速快于收入。",
        "ar": "اتسعت الهوامش سنويًا، مع نمو الربح قبل الضريبة أسرع من الإيرادات.",
        "fr": "Les marges se sont élargies sur un an, le résultat avant impôt progressant plus vite que le chiffre d'affaires.",
    },
}


# The coverage contract's one sample-path sentence: the seeded project has no structural
# validation run, so the Review screen's coverage band says so instead of showing zeros.
TR.update({
    "The seeded sample project carries no structural validation run.":
        {"zh": "内置示例项目没有结构性校验运行记录。",
         "ar": "المشروع النموذجي المُهيَّأ لا يحتوي على تشغيل تحقق بنيوي.",
         "fr": "Le projet de démonstration ne comporte aucun traitement de validation structurelle."},
})


def tr(text: str | None, locale: str) -> str | None:
    """Translate a known English string; fall back to the original for en/unknown."""
    if text is None or locale == "en" or not locale:
        return text
    return TR.get(text, {}).get(locale, text)
