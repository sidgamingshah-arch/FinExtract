import type { Locale } from "../../types";

/** Export-screen strings.
 *
 *  Only two `e.opt.*` options remain: `confidence` and `notes_sheet` are the only two keys
 *  `build_xlsx` actually reads, so formulas / note_refs / audit / hyperlinks were switches wired
 *  to nothing and their labels went with them. `e.footer.notes` went too — the screen has no
 *  served notes count to put in front of it, and inventing one is what the footer used to do.
 *
 *  `e.footer.flagged` is gone as well, replaced by `e.footer.unmappedOrFlagged` and
 *  `e.footer.inReview`. It labelled the real path's rows-with-no-mapping-or-a-flag AND the
 *  sample's review backlog: one word over two different quantities, which is how a stale 12
 *  went on reading as "flagged" long after the literal was deleted from the markup.
 *
 *  `e.footer.lines` is the same lesson applied to the footer's FIRST figure, which kept the fault
 *  after the second one lost it: `e.footer.lineitems` labelled the sample's served count of item
 *  rows AND, on the real path, every row a run serialized — the subtotals the mapper promotes
 *  included. A subtotal is a line and is not a line item, so those are two populations, and each now
 *  has its own word. Both keys are live: the sample path counts line items, the real path counts
 *  lines. The zh and ar `unmappedOrFlagged` moved with it — they carry the noun ("项" / "بند", item)
 *  and it now has to be the same noun as the figure beside it, since both count the same rows; fr
 *  agrees its participles with `lignes` for the same reason.
 *
 *  `e.col.source` was REFERENCED by the real Excel preview's sixth column header and defined in
 *  none of the four dicts, so that header rendered the literal key text "e.col.source" to every
 *  user in every language — translate() falls back to English and then to the key itself, which
 *  is loud in the console-free place nobody looks. The column prints `provStr` (a sheet!cell for
 *  a spreadsheet, `p.N` for a PDF page), i.e. WHERE in the filing the figure was read, so the
 *  label is the same word the ExtractionView column of the same content already uses
 *  (`ex.col.source`) rather than a second spelling of one quantity.
 *
 *  `e.col.value` is the other half of that same header array, and it was the reverse failure: the
 *  third column header was the hardcoded English literal "Value" sitting between five localized
 *  ones, so the zh preview header read "行项目 | Value | 附注 | 置信度 | 来源". Fixing the missing key
 *  and leaving the untranslated literal in the same expression fixed half a row. The wording
 *  matches `ex.col.value`, which already labels this exact quantity on the Extraction view. */
export const exportScreen: Record<Locale, Record<string, string>> = {
  en: {
    "e.title": "Export",
    "e.subhead":
      "Deliver the extracted, reviewed and reconciled dataset. Items still open in the review queue are flagged in the output.",
    "e.format": "Format",
    "e.excel": "Excel",
    "e.excelSub": "formatted .xlsx",
    "e.json": "JSON",
    "e.jsonSub": "structured tree",
    "e.csv": "CSV",
    "e.csvSub": "flat grid, one sheet",
    "e.workbook": "Workbook",
    "e.workbookSub": "the template's own layout",
    "e.include": "Include",
    "e.sheet.note_details": "Note details",
    "e.sheet.ratios": "Ratios",
    "e.sheet.disclosures": "Disclosures",
    "e.sourceUnits": "Source units",
    "e.unitsAsReported": "As reported",
    "e.units.absolute": "Absolute",
    "e.units.thousands": "Thousands",
    "e.units.millions": "Millions",
    "e.units.lakh": "Lakh",
    "e.units.crore": "Crore",
    "e.opt.confidence": "Confidence score column",
    "e.opt.notes_sheet": "Separate All-notes sheet",
    "e.presentation": "Presentation",
    "e.dataset": "Dataset",
    "e.currency": "Currency",
    "e.units": "Units",
    "e.both": "Both",
    "e.preview": "Preview —",
    "e.previewMeta": "Consolidated · ₹ Crore",
    // Shown where a mock of five hardcoded rows used to be. There is nothing to preview until a
    // document is loaded, and inventing a filing to fill the panel is what this replaced.
    "e.previewNeedsDoc":
      "Upload and extract a document to preview the file it will produce. This panel shows that extraction's own rows — it never shows sample figures.",
    "e.col.lineitem": "Line item",
    "e.col.value": "Value",
    "e.col.note": "Note",
    "e.col.conf": "Conf.",
    "e.col.source": "Source",
    "e.footer.lines": "lines",
    "e.footer.lineitems": "line items",
    "e.footer.unmappedOrFlagged": "unmapped or flagged",
    "e.footer.inReview": "in review",
    "e.download": "Download",
    "e.sendForReview": "Send for review",
    "e.sending": "Sending…",
    "e.submitted": "Sent for review",
  },
  zh: {
    "e.title": "导出",
    "e.subhead":
      "交付已提取、审核并核对的数据集。审核队列中尚未解决的项目将在输出中被标记。",
    "e.format": "格式",
    "e.excel": "Excel",
    "e.excelSub": "带格式 .xlsx",
    "e.json": "JSON",
    "e.jsonSub": "结构化树",
    "e.csv": "CSV",
    "e.csvSub": "扁平网格，单一工作表",
    "e.workbook": "模板工作簿",
    "e.workbookSub": "模板自带的版式",
    "e.include": "包含内容",
    "e.sheet.note_details": "附注明细",
    "e.sheet.ratios": "财务比率",
    "e.sheet.disclosures": "披露事项",
    "e.sourceUnits": "原始单位",
    "e.unitsAsReported": "按原文",
    "e.units.absolute": "绝对值",
    "e.units.thousands": "千",
    "e.units.millions": "百万",
    "e.units.lakh": "十万 (Lakh)",
    "e.units.crore": "千万 (Crore)",
    "e.opt.confidence": "置信度得分列",
    "e.opt.notes_sheet": "单独的全部附注工作表",
    "e.presentation": "呈现设置",
    "e.dataset": "数据集",
    "e.currency": "币种",
    "e.units": "单位",
    "e.both": "两者",
    "e.preview": "预览 —",
    "e.previewMeta": "合并 · ₹ 千万卢比",
    "e.previewNeedsDoc":
      "请先上传并提取文档，以预览将生成的文件。此面板仅显示该次提取自身的数据行，不会显示示例数字。",
    "e.col.lineitem": "项目",
    "e.col.value": "数值",
    "e.col.note": "附注",
    "e.col.conf": "置信度",
    "e.col.source": "来源",
    "e.footer.lines": "行",
    "e.footer.lineitems": "个项目",
    "e.footer.unmappedOrFlagged": "行未映射或带标记",
    "e.footer.inReview": "项待审核",
    "e.download": "下载",
    "e.sendForReview": "提交审核",
    "e.sending": "提交中…",
    "e.submitted": "已提交审核",
  },
  ar: {
    "e.title": "تصدير",
    "e.subhead":
      "تسليم مجموعة البيانات المستخرجة والمراجَعة والمسوّاة. البنود التي لا تزال مفتوحة في قائمة المراجعة يتم وضع علامة عليها في المخرجات.",
    "e.format": "الصيغة",
    "e.excel": "Excel",
    "e.excelSub": "‎.xlsx منسّق",
    "e.json": "JSON",
    "e.jsonSub": "شجرة منظّمة",
    "e.csv": "CSV",
    "e.csvSub": "شبكة مسطّحة، ورقة واحدة",
    "e.workbook": "مصنّف القالب",
    "e.workbookSub": "تخطيط القالب نفسه",
    "e.include": "التضمين",
    "e.sheet.note_details": "تفاصيل الإيضاحات",
    "e.sheet.ratios": "النسب",
    "e.sheet.disclosures": "الإفصاحات",
    "e.sourceUnits": "وحدات المصدر",
    "e.unitsAsReported": "كما وردت",
    "e.units.absolute": "قيمة مطلقة",
    "e.units.thousands": "بالآلاف",
    "e.units.millions": "بالملايين",
    "e.units.lakh": "لكh",
    "e.units.crore": "كرور",
    "e.opt.confidence": "عمود درجة الثقة",
    "e.opt.notes_sheet": "ورقة منفصلة لكل الإيضاحات",
    "e.presentation": "العرض",
    "e.dataset": "مجموعة البيانات",
    "e.currency": "العملة",
    "e.units": "الوحدات",
    "e.both": "كلاهما",
    "e.preview": "معاينة —",
    "e.previewMeta": "موحّد · ₹ كرور",
    "e.previewNeedsDoc":
      "حمِّل مستندًا واستخرجه لمعاينة الملف الذي سيُنتَج. تُظهر هذه اللوحة صفوف ذلك الاستخراج وحدها، ولا تعرض أرقامًا توضيحية أبدًا.",
    "e.col.lineitem": "البند",
    "e.col.value": "القيمة",
    "e.col.note": "إيضاح",
    "e.col.conf": "الثقة",
    "e.col.source": "المصدر",
    "e.footer.lines": "سطر",
    "e.footer.lineitems": "بند",
    "e.footer.unmappedOrFlagged": "سطر غير مرتبط أو موسوم",
    "e.footer.inReview": "بند قيد المراجعة",
    "e.download": "تنزيل",
    "e.sendForReview": "إرسال للمراجعة",
    "e.sending": "جارٍ الإرسال…",
    "e.submitted": "أُرسل للمراجعة",
  },
  fr: {
    "e.title": "Exporter",
    "e.subhead":
      "Livrer le jeu de données extrait, révisé et rapproché. Les éléments encore ouverts dans la file de révision sont signalés dans le résultat.",
    "e.format": "Format",
    "e.excel": "Excel",
    "e.excelSub": ".xlsx formaté",
    "e.json": "JSON",
    "e.jsonSub": "arborescence structurée",
    "e.csv": "CSV",
    "e.csvSub": "grille plate, une feuille",
    "e.workbook": "Classeur",
    "e.workbookSub": "la mise en page du modèle",
    "e.include": "Inclure",
    "e.sheet.note_details": "Détail des notes",
    "e.sheet.ratios": "Ratios",
    "e.sheet.disclosures": "Informations",
    "e.sourceUnits": "Unités source",
    "e.unitsAsReported": "Telles que déclarées",
    "e.units.absolute": "Absolu",
    "e.units.thousands": "Milliers",
    "e.units.millions": "Millions",
    "e.units.lakh": "Lakh",
    "e.units.crore": "Crore",
    "e.opt.confidence": "Colonne du score de confiance",
    "e.opt.notes_sheet": "Feuille distincte Toutes les notes",
    "e.presentation": "Présentation",
    "e.dataset": "Jeu de données",
    "e.currency": "Devise",
    "e.units": "Unités",
    "e.both": "Les deux",
    "e.preview": "Aperçu —",
    "e.previewMeta": "Consolidé · ₹ Crore",
    "e.previewNeedsDoc":
      "Importez et extrayez un document pour prévisualiser le fichier produit. Ce panneau affiche les lignes de cette extraction — jamais des chiffres d'exemple.",
    "e.col.lineitem": "Poste",
    "e.col.value": "Valeur",
    "e.col.note": "Note",
    "e.col.conf": "Conf.",
    "e.col.source": "Source",
    "e.footer.lines": "lignes",
    "e.footer.lineitems": "postes",
    "e.footer.unmappedOrFlagged": "non rattachées ou signalées",
    "e.footer.inReview": "en cours de révision",
    "e.download": "Télécharger",
    "e.sendForReview": "Envoyer en revue",
    "e.sending": "Envoi…",
    "e.submitted": "Envoyé en revue",
  },
};
