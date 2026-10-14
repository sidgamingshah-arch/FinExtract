import type { Locale } from "../../types";

export const scope: Record<Locale, Record<string, string>> = {
  en: {
    "sc.title": "Statement page detection",
    "sc.subhead":
      "Which pages each statement and the notes are read from. Extraction reads only the sections switched on.",
    "sc.scan": "SCAN",
    "sc.native": "NATIVE",
    "sc.back": "Back",
    "sc.preview": "Page preview",
    "sc.previewClose": "Close",
    "sc.readAs": "How this page is read",
    "sc.readAsHelp":
      "Click the page to cut it where a new part begins. Each part is a statement face, notes, or not read.",
    "sc.from": "from",
    "sc.kindFace": "Statement face",
    "sc.kindNotes": "Notes",
    "sc.kindOther": "Not read",
    "sc.entityAuto": "Entity: as printed",
    "sc.entityConsolidated": "Group (consolidated)",
    "sc.entityCompany": "Company",
    "sc.stmt.balance_sheet": "Balance sheet",
    "sc.stmt.profit_and_loss": "Profit & loss",
    "sc.stmt.cash_flow": "Cash flow",
    "sc.stmt.changes_in_equity": "Changes in equity",
    "sc.removePart": "Remove",
    "sc.saveParts": "Save — applies from the next run",
    "sc.resetParts": "Back to automatic",
    "sc.corrected": "CORRECTED",
    "sc.saved": "Saved. Re-run extraction to read the page this way.",
    // Header and section table
    "sc.readCount": "Read: {read} of {total} pages",
    "sc.col.section": "Section",
    "sc.col.pages": "Pages",
    "sc.col.use": "Use",
    "sc.chooseHelp": "Show this section's pages",
    "sc.sectionRead": "{read} of {pages} pages read",
    "sc.sectionNotFound": "Not found in this document",
    "sc.sectionOff": "Switched off: its pages are not read",
    "sc.notesOff":
      "Switched off: no note is read, so lines that take their figure from a note stay blank",
    "sc.pagesEdit": "Change the pages",
    "sc.pagesHelp":
      "Pages are counted from the start of the file: the sheet number under each page.",
    "sc.pagesCancel": "Cancel",
    "sc.useLabel": "Read {section}",
    "sc.help":
      "Click a section to light its pages, then use “Add to” or “Remove from” under a page. Or click a section's pages to type them (for example “6, 9–10”). A page cut into parts is changed in “How this page is read”.",
    "sc.noSections":
      "No statement or notes page was found in this document. Add pages to a section, or check that it is a set of financial statements.",
    // Hint, strip and what a page is read for
    "sc.hint":
      "Use “Add to {section}” or “Remove from {section}” under a page; click a page to see it large.",
    "sc.done": "Done",
    "sc.strip": "The document's pages",
    "sc.notRead": "not read",
    "sc.readUnattributed": "read · statement not identified",
    "sc.readForEntity": "{section} ({entity})",
    "sc.entityGroup": "Group",
    // The one button under a page
    "sc.pick.add": "Add to {section}",
    "sc.pick.move": "Move to {section}",
    "sc.pick.moveHelp": "This page is read for {from}. Moving it reads it for {section} instead.",
    "sc.pick.remove": "Remove from {section}",
    "sc.pick.parts": "Choose the part…",
    "sc.pick.partsHelp":
      "This page is cut into {n} parts. Set which part is {section} in “How this page is read”.",
    "sc.partsFor": "Set the part that is {section}, then save.",
    // Large view and states
    "sc.alsoOff": "Also in {sections} (switched off)",
    "sc.outOfScope": "Left out of the page selection. Add it to a section to read it again.",
    "sc.prevPage": "Previous page",
    "sc.nextPage": "Next page",
    "sc.renderFailed": "This page could not be rendered.",
    "sc.notPdf":
      "Page scope applies to PDF documents. This one is not a PDF, so nothing here changes what is read.",
    "sc.locked":
      "A run is extracting this document. Page changes wait until it finishes, or stop it on the Extraction screen.",
    "sc.openExtraction": "Open the extraction →",
    // Status, cue and footer
    "sc.saving": "Saving…",
    "sc.savedPages": "Pages saved.",
    "sc.changed": "Pages saved. Extract again to read them.",
    "sc.kept":
      "Kept as they are: {pages}. Each is read for another section or cut into parts; use the button under the page to change it.",
    "sc.extractN": "Extract {n} pages →",
    "sc.blockedNone": "Switch on at least one section.",
    // Asked before extracting a document again
    "sc.again.title": "This document has already been extracted",
    "sc.again.titleChanged": "The pages to read have changed since the last run",
    "sc.again.body":
      "Run #{n} ({date}) read these pages. You can open it as it is, or read the document again.",
    "sc.again.bodyChanged":
      "Run #{n} ({date}) read the pages as they were before your changes. Extract again to read them as they are now.",
    "sc.again.cost":
      "Extracting again reads every page afresh and takes time; with a language model configured it also uses model credits. Corrections typed into the Workspace stay with run #{n}; the new run does not carry them.",
    "sc.again.open": "Open the existing run",
    "sc.again.extract": "Extract again",
    "sc.again.cancel": "Cancel",
    "sc.again.starting": "Starting…",
    // Refusals, by the code the server sends
    "sc.err.runInFlight":
      "A run is extracting this document. Wait for it to finish before changing which pages are read.",
    "sc.err.notANumber": "“{text}” is not a page number. Write pages like 6 or 14–15.",
    "sc.err.empty":
      "Give at least one page, like 6 or 14–15. To stop reading this section, switch it off.",
    "sc.err.outOfRange": "Page {page} is not in this document; it has {pages} pages.",
  },
  zh: {
    "sc.title": "报表页面检测",
    "sc.subhead": "各报表及附注分别从哪些页面读取。提取只读取已启用的部分。",
    "sc.scan": "扫描",
    "sc.native": "原生",
    "sc.back": "返回",
    "sc.preview": "页面预览",
    "sc.previewClose": "关闭",
    "sc.readAs": "本页的读取方式",
    "sc.readAsHelp": "点击页面，在新部分开始处切分。每部分可为报表正表、附注或不读取。",
    "sc.from": "起始",
    "sc.kindFace": "报表正表",
    "sc.kindNotes": "附注",
    "sc.kindOther": "不读取",
    "sc.entityAuto": "主体：按页面所印",
    "sc.entityConsolidated": "合并",
    "sc.entityCompany": "母公司",
    "sc.stmt.balance_sheet": "资产负债表",
    "sc.stmt.profit_and_loss": "利润表",
    "sc.stmt.cash_flow": "现金流量表",
    "sc.stmt.changes_in_equity": "所有者权益变动表",
    "sc.removePart": "删除",
    "sc.saveParts": "保存 — 自下次运行起生效",
    "sc.resetParts": "恢复自动识别",
    "sc.corrected": "已更正",
    "sc.saved": "已保存。请重新运行提取以按此方式读取本页。",
    // Header and section table
    "sc.readCount": "读取：{read} / {total} 页",
    "sc.col.section": "部分",
    "sc.col.pages": "页面",
    "sc.col.use": "使用",
    "sc.chooseHelp": "显示此部分的页面",
    "sc.sectionRead": "已读取 {read}/{pages} 页",
    "sc.sectionNotFound": "本文档中未找到",
    "sc.sectionOff": "已关闭：其页面不读取",
    "sc.notesOff": "已关闭：不读取任何附注，取自附注的项目将为空",
    "sc.pagesEdit": "修改页码",
    "sc.pagesHelp": "页码从文件开头起算，即每页下方的序号。",
    "sc.pagesCancel": "取消",
    "sc.useLabel": "读取{section}",
    "sc.help":
      "点击某个部分以高亮其页面，然后使用页面下方的“添加到”或“移出”。也可以点击某部分的页码直接输入（例如“6, 9–10”）。被切分为多个部分的页面，请在“本页的读取方式”中修改。",
    "sc.noSections": "本文档中未找到报表或附注页面。请为某个部分添加页面，或确认这是一套财务报表。",
    // Hint, strip and what a page is read for
    "sc.hint": "使用页面下方的“添加到{section}”或“从{section}移出”；点击页面可放大查看。",
    "sc.done": "完成",
    "sc.strip": "文档页面",
    "sc.notRead": "不读取",
    "sc.readUnattributed": "读取 · 未识别报表",
    "sc.readForEntity": "{section}（{entity}）",
    "sc.entityGroup": "合并",
    // The one button under a page
    "sc.pick.add": "添加到{section}",
    "sc.pick.move": "移至{section}",
    "sc.pick.moveHelp": "本页当前按{from}读取。移动后改为按{section}读取。",
    "sc.pick.remove": "从{section}移出",
    "sc.pick.parts": "选择部分…",
    "sc.pick.partsHelp": "本页被切分为 {n} 个部分。请在“本页的读取方式”中指定哪一部分是{section}。",
    "sc.partsFor": "请设置属于{section}的部分，然后保存。",
    // Large view and states
    "sc.alsoOff": "也在{sections}中（已关闭）",
    "sc.outOfScope": "已从页面选择中排除。将其添加到某个部分即可重新读取。",
    "sc.prevPage": "上一页",
    "sc.nextPage": "下一页",
    "sc.renderFailed": "此页面无法渲染。",
    "sc.notPdf": "页面范围仅适用于 PDF 文档。本文档不是 PDF，此处的设置不会改变读取内容。",
    "sc.locked": "正在对本文档运行提取。页面更改需待其完成，或在“提取”页面停止该运行。",
    "sc.openExtraction": "打开提取 →",
    // Status, cue and footer
    "sc.saving": "正在保存…",
    "sc.savedPages": "页码已保存。",
    "sc.changed": "页码已保存。请重新提取以读取。",
    "sc.kept": "以下页面保持不变：{pages}。它们已按其他部分读取或被切分为多个部分；请使用页面下方的按钮修改。",
    "sc.extractN": "提取 {n} 页 →",
    "sc.blockedNone": "请至少启用一个部分。",
    // Asked before extracting a document again
    "sc.again.title": "此文档已提取过",
    "sc.again.titleChanged": "自上次运行以来，要读取的页面已更改",
    "sc.again.body": "第 {n} 次运行（{date}）已读取这些页面。您可以直接打开它，或重新读取文档。",
    "sc.again.bodyChanged": "第 {n} 次运行（{date}）读取的是更改前的页面。请重新提取，按当前设置读取。",
    "sc.again.cost": "重新提取会重新读取每一页，需要一些时间；如已配置语言模型，还会消耗模型额度。在工作区中输入的更正保留在第 {n} 次运行中，新运行不会带入。",
    "sc.again.open": "打开现有提取结果",
    "sc.again.extract": "重新提取",
    "sc.again.cancel": "取消",
    "sc.again.starting": "正在启动…",
    // Refusals, by the code the server sends
    "sc.err.runInFlight": "正在对本文档运行提取。请等待其完成后再更改读取的页面。",
    "sc.err.notANumber": "“{text}”不是页码。请按 6 或 14–15 的格式输入。",
    "sc.err.empty": "请至少输入一页，例如 6 或 14–15。若不想读取此部分，请将其关闭。",
    "sc.err.outOfRange": "第 {page} 页不在本文档中；本文档共 {pages} 页。",
  },
  ar: {
    "sc.title": "كشف صفحات القوائم المالية",
    "sc.subhead":
      "الصفحات التي تُقرأ منها كل قائمة مالية والإيضاحات. لا يقرأ الاستخراج إلا الأقسام المفعّلة.",
    "sc.scan": "ممسوحة",
    "sc.native": "أصلية",
    "sc.back": "رجوع",
    "sc.preview": "معاينة الصفحة",
    "sc.previewClose": "إغلاق",
    "sc.readAs": "كيف تُقرأ هذه الصفحة",
    "sc.readAsHelp":
      "انقر على الصفحة لتقسيمها حيث يبدأ جزء جديد. كل جزء إما قائمة مالية أو إيضاحات أو لا يُقرأ.",
    "sc.from": "من",
    "sc.kindFace": "قائمة مالية",
    "sc.kindNotes": "إيضاحات",
    "sc.kindOther": "لا يُقرأ",
    "sc.entityAuto": "الكيان: كما هو مطبوع",
    "sc.entityConsolidated": "المجموعة (موحدة)",
    "sc.entityCompany": "الشركة",
    "sc.stmt.balance_sheet": "الميزانية العمومية",
    "sc.stmt.profit_and_loss": "الأرباح والخسائر",
    "sc.stmt.cash_flow": "التدفقات النقدية",
    "sc.stmt.changes_in_equity": "التغيرات في حقوق الملكية",
    "sc.removePart": "إزالة",
    "sc.saveParts": "حفظ — يُطبق من التشغيل التالي",
    "sc.resetParts": "العودة إلى التلقائي",
    "sc.corrected": "مُصحح",
    "sc.saved": "تم الحفظ. أعد تشغيل الاستخراج لقراءة الصفحة بهذه الطريقة.",
    // Header and section table
    "sc.readCount": "تُقرأ {read} من {total} صفحة",
    "sc.col.section": "القسم",
    "sc.col.pages": "الصفحات",
    "sc.col.use": "استخدام",
    "sc.chooseHelp": "عرض صفحات هذا القسم",
    "sc.sectionRead": "تُقرأ {read} من {pages} صفحة",
    "sc.sectionNotFound": "غير موجود في هذا المستند",
    "sc.sectionOff": "موقوف: لا تُقرأ صفحاته",
    "sc.notesOff": "موقوف: لا يُقرأ أي إيضاح، فتبقى البنود التي تأخذ أرقامها من الإيضاحات فارغة",
    "sc.pagesEdit": "تغيير الصفحات",
    "sc.pagesHelp": "تُعدّ الصفحات من بداية الملف: رقم الورقة أسفل كل صفحة.",
    "sc.pagesCancel": "إلغاء",
    "sc.useLabel": "قراءة {section}",
    "sc.help":
      "انقر على قسم لإبراز صفحاته، ثم استخدم «أضف إلى» أو «أخرجها من» أسفل الصفحة. أو انقر على صفحات القسم لكتابتها (مثل «6, 9–10»). الصفحة المقسّمة إلى أجزاء تُعدَّل في «كيف تُقرأ هذه الصفحة».",
    "sc.noSections":
      "لم يُعثر في هذا المستند على صفحة قوائم مالية أو إيضاحات. أضف صفحات إلى قسم، أو تحقّق من أنه مجموعة قوائم مالية.",
    // Hint, strip and what a page is read for
    "sc.hint":
      "استخدم «أضف إلى {section}» أو «أخرجها من {section}» أسفل الصفحة؛ انقر على صفحة لعرضها بحجم كبير.",
    "sc.done": "تم",
    "sc.strip": "صفحات المستند",
    "sc.notRead": "لا تُقرأ",
    "sc.readUnattributed": "تُقرأ · لم تُحدَّد القائمة",
    "sc.readForEntity": "{section} ({entity})",
    "sc.entityGroup": "المجموعة",
    // The one button under a page
    "sc.pick.add": "أضف إلى {section}",
    "sc.pick.move": "انقلها إلى {section}",
    "sc.pick.moveHelp":
      "تُقرأ هذه الصفحة حاليًا ضمن {from}. نقلها يجعلها تُقرأ ضمن {section} بدلًا من ذلك.",
    "sc.pick.remove": "أخرجها من {section}",
    "sc.pick.parts": "اختر الجزء…",
    "sc.pick.partsHelp":
      "هذه الصفحة مقسّمة إلى {n} أجزاء. حدّد الجزء الذي يمثّل {section} في «كيف تُقرأ هذه الصفحة».",
    "sc.partsFor": "اضبط الجزء الذي يمثّل {section} ثم احفظ.",
    // Large view and states
    "sc.alsoOff": "موجودة أيضًا في {sections} (موقوف)",
    "sc.outOfScope": "مستبعدة من اختيار الصفحات. أضفها إلى قسم لقراءتها من جديد.",
    "sc.prevPage": "الصفحة السابقة",
    "sc.nextPage": "الصفحة التالية",
    "sc.renderFailed": "تعذّر عرض هذه الصفحة.",
    "sc.notPdf":
      "ينطبق نطاق الصفحات على مستندات PDF فقط. هذا المستند ليس ملف PDF، فلا يغيّر شيء هنا ما يُقرأ.",
    "sc.locked":
      "يجري استخراج هذا المستند الآن. تنتظر تغييرات الصفحات حتى ينتهي، أو أوقفه من شاشة الاستخراج.",
    "sc.openExtraction": "افتح الاستخراج →",
    // Status, cue and footer
    "sc.saving": "جارٍ الحفظ…",
    "sc.savedPages": "حُفظت الصفحات.",
    "sc.changed": "حُفظت الصفحات. أعد الاستخراج لقراءتها.",
    "sc.kept":
      "أُبقيت كما هي: {pages}. كلٌّ منها يُقرأ ضمن قسم آخر أو مقسّم إلى أجزاء؛ استخدم الزر أسفل الصفحة لتغييرها.",
    "sc.extractN": "استخرج {n} صفحة →",
    "sc.blockedNone": "فعّل قسمًا واحدًا على الأقل.",
    // Asked before extracting a document again
    "sc.again.title": "تم استخراج هذا المستند من قبل",
    "sc.again.titleChanged": "تغيّرت الصفحات المطلوب قراءتها منذ آخر تشغيل",
    "sc.again.body":
      "قرأ التشغيل رقم {n} ({date}) هذه الصفحات. يمكنك فتحه كما هو، أو قراءة المستند من جديد.",
    "sc.again.bodyChanged":
      "قرأ التشغيل رقم {n} ({date}) الصفحات كما كانت قبل تغييراتك. أعد الاستخراج لقراءتها كما هي الآن.",
    "sc.again.cost":
      "إعادة الاستخراج تقرأ كل صفحة من جديد وتستغرق وقتًا، وإذا كان نموذج لغوي مُعدًّا فإنها تستهلك أيضًا أرصدة النموذج. التصحيحات المُدخلة في مساحة العمل تبقى مع التشغيل رقم {n}، ولا ينقلها التشغيل الجديد.",
    "sc.again.open": "افتح نتيجة الاستخراج الحالية",
    "sc.again.extract": "استخرج مرة أخرى",
    "sc.again.cancel": "إلغاء",
    "sc.again.starting": "جارٍ البدء…",
    // Refusals, by the code the server sends
    "sc.err.runInFlight": "يجري استخراج هذا المستند. انتظر حتى ينتهي قبل تغيير الصفحات التي تُقرأ.",
    "sc.err.notANumber": "«{text}» ليس رقم صفحة. اكتب الصفحات مثل 6 أو 14–15.",
    "sc.err.empty": "أدخل صفحة واحدة على الأقل، مثل 6 أو 14–15. لإيقاف قراءة هذا القسم، أوقفه.",
    "sc.err.outOfRange": "الصفحة {page} ليست في هذا المستند؛ عدد صفحاته {pages}.",
  },
  fr: {
    "sc.title": "Détection des pages d'états financiers",
    "sc.subhead":
      "Les pages d’où sont lus chaque état financier et les notes. L’extraction ne lit que les sections activées.",
    "sc.scan": "SCAN",
    "sc.native": "NATIF",
    "sc.back": "Retour",
    "sc.preview": "Aperçu de la page",
    "sc.previewClose": "Fermer",
    "sc.readAs": "Lecture de cette page",
    "sc.readAsHelp":
      "Cliquez sur la page pour la couper là où commence une nouvelle partie. Chaque partie est un état financier, des notes, ou n'est pas lue.",
    "sc.from": "à partir de",
    "sc.kindFace": "État financier",
    "sc.kindNotes": "Notes",
    "sc.kindOther": "Non lue",
    "sc.entityAuto": "Entité : selon la page",
    "sc.entityConsolidated": "Groupe (consolidé)",
    "sc.entityCompany": "Société",
    "sc.stmt.balance_sheet": "Bilan",
    "sc.stmt.profit_and_loss": "Compte de résultat",
    "sc.stmt.cash_flow": "Flux de trésorerie",
    "sc.stmt.changes_in_equity": "Variation des capitaux propres",
    "sc.removePart": "Supprimer",
    "sc.saveParts": "Enregistrer — appliqué au prochain run",
    "sc.resetParts": "Revenir à l'automatique",
    "sc.corrected": "CORRIGÉE",
    "sc.saved": "Enregistré. Relancez l'extraction pour lire la page ainsi.",
    // Header and section table
    "sc.readCount": "Lues : {read} sur {total} pages",
    "sc.col.section": "Section",
    "sc.col.pages": "Pages",
    "sc.col.use": "Utiliser",
    "sc.chooseHelp": "Afficher les pages de cette section",
    "sc.sectionRead": "{read} sur {pages} pages lues",
    "sc.sectionNotFound": "Introuvable dans ce document",
    "sc.sectionOff": "Désactivée : ses pages ne sont pas lues",
    "sc.notesOff": "Désactivée : aucune note n’est lue, les lignes tirées d’une note restent vides",
    "sc.pagesEdit": "Modifier les pages",
    "sc.pagesHelp":
      "Les pages sont comptées depuis le début du fichier : le numéro de feuille sous chaque page.",
    "sc.pagesCancel": "Annuler",
    "sc.useLabel": "Lire {section}",
    "sc.help":
      "Cliquez sur une section pour mettre ses pages en évidence, puis utilisez « Ajouter à » ou « Retirer de » sous une page. Ou cliquez sur les pages d’une section pour les saisir (par exemple « 6, 9–10 »). Une page coupée en parties se modifie dans « Lecture de cette page ».",
    "sc.noSections":
      "Aucune page d’état financier ni de notes n’a été trouvée dans ce document. Ajoutez des pages à une section, ou vérifiez qu’il s’agit bien d’états financiers.",
    // Hint, strip and what a page is read for
    "sc.hint":
      "Utilisez « Ajouter à {section} » ou « Retirer de {section} » sous une page ; cliquez sur une page pour l’agrandir.",
    "sc.done": "Terminé",
    "sc.strip": "Les pages du document",
    "sc.notRead": "non lue",
    "sc.readUnattributed": "lue · état non identifié",
    "sc.readForEntity": "{section} ({entity})",
    "sc.entityGroup": "Groupe",
    // The one button under a page
    "sc.pick.add": "Ajouter à {section}",
    "sc.pick.move": "Déplacer vers {section}",
    "sc.pick.moveHelp":
      "Cette page est lue pour {from}. La déplacer la fait lire pour {section} à la place.",
    "sc.pick.remove": "Retirer de {section}",
    "sc.pick.parts": "Choisir la partie…",
    "sc.pick.partsHelp":
      "Cette page est coupée en {n} parties. Indiquez laquelle est {section} dans « Lecture de cette page ».",
    "sc.partsFor": "Réglez la partie qui correspond à {section}, puis enregistrez.",
    // Large view and states
    "sc.alsoOff": "Aussi dans {sections} (désactivé)",
    "sc.outOfScope":
      "Exclue de la sélection de pages. Ajoutez-la à une section pour la lire à nouveau.",
    "sc.prevPage": "Page précédente",
    "sc.nextPage": "Page suivante",
    "sc.renderFailed": "Cette page n’a pas pu être affichée.",
    "sc.notPdf":
      "Le périmètre des pages s’applique aux documents PDF. Ce document n’est pas un PDF : rien ici ne change ce qui est lu.",
    "sc.locked":
      "Une extraction de ce document est en cours. Les changements de pages attendent sa fin, ou arrêtez-la depuis l’écran Extraction.",
    "sc.openExtraction": "Ouvrir l’extraction →",
    // Status, cue and footer
    "sc.saving": "Enregistrement…",
    "sc.savedPages": "Pages enregistrées.",
    "sc.changed": "Pages enregistrées. Relancez l’extraction pour les lire.",
    "sc.kept":
      "Laissées telles quelles : {pages}. Chacune est lue pour une autre section ou coupée en parties ; utilisez le bouton sous la page pour la modifier.",
    "sc.extractN": "Extraire {n} pages →",
    "sc.blockedNone": "Activez au moins une section.",
    // Asked before extracting a document again
    "sc.again.title": "Ce document a déjà été extrait",
    "sc.again.titleChanged": "Les pages à lire ont changé depuis la dernière extraction",
    "sc.again.body":
      "L’extraction n° {n} ({date}) a lu ces pages. Vous pouvez l’ouvrir telle quelle, ou relire le document.",
    "sc.again.bodyChanged":
      "L’extraction n° {n} ({date}) a lu les pages telles qu’elles étaient avant vos changements. Relancez l’extraction pour les lire telles qu’elles sont maintenant.",
    "sc.again.cost":
      "Relancer l’extraction relit chaque page et prend du temps ; si un modèle de langage est configuré, elle consomme aussi des crédits. Les corrections saisies dans l’espace de travail restent attachées à l’extraction n° {n} ; la nouvelle ne les reprend pas.",
    "sc.again.open": "Ouvrir l’extraction existante",
    "sc.again.extract": "Extraire à nouveau",
    "sc.again.cancel": "Annuler",
    "sc.again.starting": "Démarrage…",
    // Refusals, by the code the server sends
    "sc.err.runInFlight":
      "Une extraction de ce document est en cours. Attendez sa fin avant de changer les pages lues.",
    "sc.err.notANumber":
      "« {text} » n’est pas un numéro de page. Écrivez les pages comme 6 ou 14–15.",
    "sc.err.empty":
      "Indiquez au moins une page, comme 6 ou 14–15. Pour ne plus lire cette section, désactivez-la.",
    "sc.err.outOfRange": "La page {page} n’est pas dans ce document ; il compte {pages} pages.",
  },
};
