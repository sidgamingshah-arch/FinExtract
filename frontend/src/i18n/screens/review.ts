import type { Locale } from "../../types";

/** Review-queue strings. Counts are never inside a string: t() takes a key only and interpolates
 *  nothing, so every quantity is composed as `${n} ${t(key)}` from a number the payload carried.
 *
 *  "r.applyFix" is gone: there is no generic fix to apply. Only a mis-signed figure has a
 *  mechanical correction (r.flipSignOf), and every other finding says so in words
 *  (r.manualFixOnly) rather than offering a button that would do nothing.
 *  "r.passed" says "lines with no finding" because a LINE count sits directly above the coverage
 *  band's RELATION counts, and "passed" invited the two to be read as one number. The words are
 *  unchanged in this pass and are now TRUE of the figure beneath them: `summary.passed` was rows
 *  minus (unmapped + low-confidence) — so a line indicted by a balance, note-tie, structural,
 *  guard, calculated_mismatch or uncomputed finding was counted as having no finding — and the
 *  server now counts lines NAMED BY NO SERVED FINDING, the same definition on the real and sample
 *  paths. The key keeps the payload's own spelling (`passed`) so the label, the field and the
 *  testid are one name for one quantity.
 *
 *  zh and fr said "无异常的行" / "lignes sans anomalie" — lines with no ANOMALY, a claim about the
 *  figures themselves, which is stronger than the count supports: a line no check names may still
 *  be wrong in a way no relation covers (which is exactly what the coverage band below reports).
 *  Both now name the FINDING, using the word the rest of each dict already uses for one
 *  (发现 / constat); ar already did (ملاحظات).
 *
 *  The conflict strings are LABELS only — the sentence explaining a conflict is the server's
 *  `conflict_note`, which already carries the count and the withheld-judgement clause. A second
 *  spelling of it here would be one statement in two places, free to drift from the state the
 *  server actually refused.
 *
 *  `r.withheldWithdrawable` is the exception, and only because it describes a CONTROL rather than
 *  a state: it says what pressing Withdraw does on a card that displays no acceptance. The server
 *  says the acceptance is being withheld; this says it can still be taken back. Without it the
 *  button read as one that does nothing, which is how it came to be hidden altogether. */
export const review: Record<Locale, Record<string, string>> = {
  en: {
    "r.title": "Review queue",
    "r.subhead":
      "Items where automated checks — balance-sheet equality, section subtotals, sign logic and note reconciliation — did not pass. Correct each, or record that it stands, before export.",
    "r.open": "open",
    "r.accepted": "accepted",
    "r.passed": "lines with no finding",
    "r.reconciliation": "RECONCILIATION",
    "r.suggestedFix": "SUGGESTED FIX",
    "r.openInWorkspace": "Open in workspace",
    "r.acceptAsIs": "Accept as-is",
    "r.statusAccepted": "ACCEPTED",
    "r.acceptedBy": "Accepted by",
    "r.reason": "Reason",
    "r.reasonPlaceholder": "Why do these figures stand? Recorded with your name against the figures shown.",
    "r.reasonRequired": "A reason is required — an acceptance with nothing stated is an unsigned claim.",
    "r.withdrawAcceptance": "Withdraw acceptance",
    // Shown only where two orphan rows print the same name: the identity the Withdraw addresses.
    "r.orphanIdentity": "identity",
    "r.orphanIdentityHelp": "Another standing verdict shares this name. Full identity:",
    "r.withheldWithdrawable":
      "An acceptance is on record for this identity and is being withheld from every card in the group, so none of them shows it. Withdrawing takes it out of force — the record of who accepted what is kept, and the finding stays raised.",
    "r.staleStrip": "accepted findings rest on figures that have changed since",
    "r.staleTitle": "Accepted against different figures",
    "r.staleChanged": "Changed since it was accepted:",
    "r.statusConflict": "IDENTITY CONFLICT",
    "r.conflictTitle": "The queue cannot tell these findings apart",
    "r.conflictStrip": "findings share one identity while printing different figures, so none of them can be accepted",
    "r.unknownState":
      "This build does not recognise the state the server reported for this finding, so no judgement control is offered for it. Reported state:",
    "r.conflictRefused":
      "Refused: this finding shares an identity with another that printed different figures, so no verdict can be recorded against it. Reload the queue — the extraction has to distinguish them first.",
    "r.writeConflict":
      "Nothing was recorded — another write for this finding landed at the same moment and this acceptance was not stored. Reload the queue and judge the figures as they now stand.",
    "r.noJudgement":
      "Nothing was withdrawn: no acceptance is on record for this finding any more. It was already withdrawn — possibly from another card that shares its identity. Reload the queue.",
    "r.ambiguousNote": "identical findings share this judgement — accepting one accepts them all",
    "r.orphanedTitle": "prior judgements match no finding in this run",
    "r.orphanedNote":
      "The findings they were recorded against were corrected, are no longer raised, or belong to a category this queue no longer carries. Nothing is deleted, and none of these count towards the queue above.",
    "r.orphanedInForce":
      "Each of these acceptances is still in force: if the same finding is raised again with the same figures it will come back already accepted, under a verdict nobody re-made.",
    "r.orphanedWithdrawHint": "Withdraw any that should not carry forward.",
    // A COUNT, NOT A QUEUE. A weakly-mapped line raises no finding — this queue reports unplaced face
    // figures, failed validation rules, and subtotals that do not match — but the number must be
    // visible somewhere, or narrowing the queue reads as the data having improved. A plain sentence,
    // never a card and never a tab: there is nothing here to work through.
    "r.weakMappings": "lines carry a weak mapping. No finding is raised for them — a weak match is answered by matching it better, not by review — and each one shows its own confidence in the Workspace.",
    "r.flipSignOf": "Flip the sign of",
    "r.manualFixOnly": "No automatic correction — apply the fix above by hand.",
    "r.remapHead": "MAP TO A DIFFERENT LINE ITEM",
    "r.remapPick": "Choose a template line…",
    "r.remapUnmap": "— leave this line unmapped —",
    "r.remapApply": "Map this row",
    "r.remapReasonPlaceholder": "Why does this row belong there? Recorded with your name against the row.",
    "r.remapNoTargets": "This run named no template, so there is no line to map onto.",
    "r.remapCurrent": "Now mapped to",
    "r.remapNowUnmapped": "not mapped",
    "r.evidenceChanged":
      "The figures changed while this card was open, so the acceptance was refused. Reload the queue and judge the figures as they now stand.",
    "r.cov.title": "Relation coverage",
    "r.cov.missing": "This review payload carried no coverage report, so what the template's relations could check on this filing is unknown.",
    "r.cov.subhead":
      "What the template's relations could say about this filing. Every count here is RELATIONS — not findings, and not lines.",
    "r.cov.relationsEvaluated": "relations evaluated",
    "r.cov.held": "held",
    "r.cov.failed": "failed",
    "r.cov.notEvaluable": "not evaluable",
    "r.cov.ofThoseThatRan": "held, of the relations that ran",
    "r.cov.ofThoseAnswerable": "ran, of the relations this filing could answer",
    "r.cov.na": "no relation ran, so there is no pass rate",
    "r.cov.provedNothing": "no relation could be evaluated — nothing here is verified",
    "r.cov.notCounted": "not counted",
    "r.cov.reportedAbove": "failed relations are reported by a finding above",
    "r.cov.recomputedFrom": "Recomputed from run",
  },
  zh: {
    "r.title": "审核队列",
    "r.subhead":
      "自动检查未通过的项目——资产负债表平衡、分节小计、符号逻辑及附注核对。请在导出前逐项更正，或记录其成立的理由。",
    "r.open": "未解决",
    "r.accepted": "已认可",
    "r.passed": "未被任何发现指出的行",
    "r.reconciliation": "核对",
    "r.suggestedFix": "建议修正",
    "r.openInWorkspace": "在工作区中打开",
    "r.acceptAsIs": "按原样接受",
    "r.statusAccepted": "已认可",
    "r.acceptedBy": "认可人",
    "r.reason": "理由",
    "r.reasonPlaceholder": "这些数字为何成立？理由将连同您的姓名，一并记录在所示数字之上。",
    "r.reasonRequired": "必须填写理由——未说明理由的认可等于未署名的主张。",
    "r.withdrawAcceptance": "撤回认可",
    "r.orphanIdentity": "标识",
    "r.orphanIdentityHelp": "另有一条留存判定与此名称相同。完整标识：",
    "r.withheldWithdrawable":
      "本身份已有一条认可记录，但该记录对本组中的每张卡片均予保留而不予归属，因此各卡片均不显示。撤回后该认可即失效——认可人及认可内容的记录仍会保留，且该发现仍继续提出。",
    "r.staleStrip": "项已认可的发现，其所依据的数字此后已发生变化",
    "r.staleTitle": "认可时所依据的数字已不同",
    "r.staleChanged": "认可之后发生变化的项目：",
    "r.statusConflict": "身份冲突",
    "r.conflictTitle": "审核队列无法区分这些发现",
    "r.conflictStrip": "项发现共用同一身份但列示的数字不同，因此均无法被认可",
    "r.unknownState":
      "本版本无法识别服务器为该发现返回的状态，因此不提供任何判断操作。服务器返回的状态：",
    "r.conflictRefused":
      "已被拒绝：该发现与另一条发现共用同一身份，但两者列示的数字不同，因此无法对其记录任何判断。请重新加载队列——须先由提取过程将两者区分开。",
    "r.writeConflict":
      "未记录任何内容——同一发现的另一次写入在同一时刻完成，本次认可未被保存。请重新加载队列，并针对当前的数字重新作出判断。",
    "r.noJudgement":
      "未撤回任何内容：该发现已不存在任何认可记录。该认可此前已被撤回——可能是在与其共用同一身份的另一张卡片上撤回的。请重新加载队列。",
    "r.ambiguousNote": "条完全相同的发现共用此判断——认可其中一条即认可全部",
    "r.orphanedTitle": "条既往判断在本次运行中已无对应的发现",
    "r.orphanedNote":
      "其所针对的发现已被更正、已不再被提出，或属于本队列不再收录的类别。记录不会被删除，且这些判断均不计入上方队列。",
    "r.orphanedInForce":
      "这些接受判断仍然有效：若同一发现再次以相同数字被提出，它将直接显示为已接受，而该结论无人重新作出。",
    "r.orphanedWithdrawHint": "如不应继续沿用，请予撤回。",
    "r.weakMappings": "行的映射置信度偏低。系统不会为其生成待办发现——置信度偏低应通过改进映射来解决，而非交由复核——每一行在工作区中都会显示其自身的置信度。",
    "r.flipSignOf": "反转符号：",
    "r.manualFixOnly": "无自动更正——请按上述建议手动处理。",
    "r.remapHead": "映射到其他行项目",
    "r.remapPick": "选择模板行项目……",
    "r.remapUnmap": "——保持未映射——",
    "r.remapApply": "映射此行",
    "r.remapReasonPlaceholder": "为什么该行属于此项？将以您的名义记录在该行上。",
    "r.remapNoTargets": "本次运行未指定模板，因此没有可映射的行项目。",
    "r.remapCurrent": "当前映射到",
    "r.remapNowUnmapped": "未映射",
    "r.evidenceChanged":
      "本卡片打开期间数字已发生变化，因此认可被拒绝。请重新加载队列，并针对当前数字作出判断。",
    "r.cov.title": "关系式覆盖情况",
    "r.cov.missing": "本次审核数据未附带覆盖情况报告，因此无法得知模板中的关系式对本报告检查了什么。",
    "r.cov.subhead":
      "模板中的关系式对本报告能作出何种结论。此处统计的均为关系式——既非发现，也非行项目。",
    "r.cov.relationsEvaluated": "条关系式已评估",
    "r.cov.held": "条成立",
    "r.cov.failed": "条不成立",
    "r.cov.notEvaluable": "条无法评估",
    "r.cov.ofThoseThatRan": "条成立（在已运行的关系式中）",
    "r.cov.ofThoseAnswerable": "条已运行（在本报告可回答的关系式中）",
    "r.cov.na": "没有任何关系式运行，因此不存在通过率",
    "r.cov.provedNothing": "没有任何关系式可被评估——此处的内容均未获验证",
    "r.cov.notCounted": "不计入",
    "r.cov.reportedAbove": "条不成立的关系式已由上方的发现报告",
    "r.cov.recomputedFrom": "重新计算自运行",
  },
  ar: {
    "r.title": "قائمة المراجعة",
    "r.subhead":
      "البنود التي لم تجتز الفحوصات الآلية — توازن الميزانية العمومية، والمجاميع الفرعية للأقسام، ومنطق الإشارة، ومطابقة الإيضاحات. صحّح كل بند، أو سجّل أنه يصح، قبل التصدير.",
    "r.open": "مفتوح",
    "r.accepted": "مقبولة",
    "r.passed": "أسطر بلا ملاحظات",
    "r.reconciliation": "المطابقة",
    "r.suggestedFix": "التصحيح المقترح",
    "r.openInWorkspace": "فتح في مساحة العمل",
    "r.acceptAsIs": "القبول كما هو",
    "r.statusAccepted": "مقبولة",
    "r.acceptedBy": "قبِلها",
    "r.reason": "السبب",
    "r.reasonPlaceholder": "لماذا تصح هذه الأرقام؟ يُسجَّل السبب باسمك مقابل الأرقام المعروضة.",
    "r.reasonRequired": "السبب مطلوب — القبول دون بيان سبب هو ادعاء غير موقَّع.",
    "r.withdrawAcceptance": "سحب القبول",
    "r.orphanIdentity": "المعرّف",
    "r.orphanIdentityHelp": "يشترك حكم قائم آخر في هذا الاسم. المعرّف الكامل:",
    "r.withheldWithdrawable":
      "يوجد قبول مسجَّل على هذه الهوية، ويُحجب عن كل بطاقة في المجموعة فلا تُظهره أي منها. السحب يُنهي سريانه — ويُحفظ سجل مَن قبِل وماذا قال، وتبقى الملاحظة مُثارة.",
    "r.staleStrip": "ملاحظات مقبولة تستند إلى أرقام تغيّرت بعد قبولها",
    "r.staleTitle": "قُبِلت مقابل أرقام مختلفة",
    "r.staleChanged": "ما تغيّر منذ القبول:",
    "r.statusConflict": "تعارض في الهوية",
    "r.conflictTitle": "قائمة المراجعة لا تستطيع التمييز بين هذه النتائج",
    "r.conflictStrip": "نتيجة تتشارك هويةً واحدة مع أرقام مختلفة، فلا يمكن قبول أيٍّ منها",
    "r.unknownState":
      "هذه النسخة لا تتعرّف على الحالة التي أرسلها الخادم لهذه النتيجة، لذلك لا يُتاح أي إجراء حكم عليها. الحالة المُرسَلة:",
    "r.conflictRefused":
      "مرفوض: هذه النتيجة تتشارك الهوية نفسها مع نتيجة أخرى عرضت أرقامًا مختلفة، لذلك لا يمكن تسجيل أي حكم عليها. أعد تحميل القائمة — يجب أن يميّز الاستخراج بينهما أولًا.",
    "r.writeConflict":
      "لم يُسجَّل أي شيء — فقد وصلت عملية كتابة أخرى لهذه النتيجة في اللحظة نفسها ولم يُحفظ هذا القبول. أعد تحميل القائمة واحكم على الأرقام كما هي الآن.",
    "r.noJudgement":
      "لم يُسحب أي شيء: لم يعد هناك أي قبول مسجَّل على هذه الملاحظة. لقد سُحب من قبل — وربما من بطاقة أخرى تتشارك الهوية نفسها. أعد تحميل القائمة.",
    "r.ambiguousNote": "ملاحظات متطابقة تتشارك هذا الحكم — قبول واحدة يقبلها جميعًا",
    "r.orphanedTitle": "أحكام سابقة لا تطابق أي ملاحظة في هذه العملية",
    "r.orphanedNote":
      "الملاحظات التي سُجِّلت عليها صُحِّحت، أو لم تُثَر مرة أخرى، أو تنتمي إلى فئة لم تعد هذه القائمة تتضمنها. لا يُحذف شيء، ولا يُحتسب أي منها في القائمة أعلاه.",
    "r.orphanedInForce":
      "كل قبول من هذه القبولات ما زال ساريًا: فإذا أُثير الملحظ ذاته مرة أخرى بالأرقام ذاتها عاد مقبولًا بالفعل، بحكم لم يُعِد أحد إصداره.",
    "r.orphanedWithdrawHint": "اسحب ما لا ينبغي أن يستمر منها.",
    "r.weakMappings": "سطرًا مطابقتها ضعيفة. لا تُثار ملاحظة لها — فالمطابقة الضعيفة تُعالَج بمطابقة أفضل لا بالمراجعة — ويُظهر كل سطر درجة ثقته في مساحة العمل.",
    "r.flipSignOf": "اقلب إشارة",
    "r.manualFixOnly": "لا يوجد تصحيح آلي — طبّق التصحيح أعلاه يدويًا.",
    "r.remapHead": "الربط ببند آخر",
    "r.remapPick": "اختر بندًا من القالب…",
    "r.remapUnmap": "— اترك هذا السطر غير مربوط —",
    "r.remapApply": "اربط هذا السطر",
    "r.remapReasonPlaceholder": "لماذا ينتمي هذا السطر إلى ذلك البند؟ يُسجَّل باسمك على السطر.",
    "r.remapNoTargets": "لم تحدّد هذه العملية أي قالب، فلا يوجد بند للربط به.",
    "r.remapCurrent": "مربوط حاليًا بـ",
    "r.remapNowUnmapped": "غير مربوط",
    "r.evidenceChanged":
      "تغيّرت الأرقام أثناء فتح هذه البطاقة، لذا رُفض القبول. أعد تحميل القائمة واحكم على الأرقام كما هي الآن.",
    "r.cov.title": "تغطية العلاقات",
    "r.cov.missing": "لم تتضمّن بيانات المراجعة هذه تقرير تغطية، لذا لا يُعرف ما فحصته علاقات القالب في هذا الملف.",
    "r.cov.subhead":
      "ما تستطيع علاقات القالب أن تقوله عن هذا الملف. كل عدد هنا يمثل علاقات — لا ملاحظات ولا أسطرًا.",
    "r.cov.relationsEvaluated": "علاقة تم تقييمها",
    "r.cov.held": "علاقة صحيحة",
    "r.cov.failed": "علاقة فاشلة",
    "r.cov.notEvaluable": "علاقة غير قابلة للتقييم",
    "r.cov.ofThoseThatRan": "صحيحة من العلاقات التي نُفِّذت",
    "r.cov.ofThoseAnswerable": "نُفِّذت من العلاقات التي كان يمكن لهذا الملف الإجابة عنها",
    "r.cov.na": "لم تُنفَّذ أي علاقة، فلا توجد نسبة نجاح",
    "r.cov.provedNothing": "لم يتسنَّ تقييم أي علاقة — لا شيء هنا مُتحقَّق منه",
    "r.cov.notCounted": "غير محتسبة",
    "r.cov.reportedAbove": "علاقة فاشلة يُبلِّغ عنها بندٌ أعلاه",
    "r.cov.recomputedFrom": "أُعيد حسابها من العملية",
  },
  fr: {
    "r.title": "File de révision",
    "r.subhead":
      "Éléments pour lesquels les contrôles automatiques — équilibre du bilan, sous-totaux de section, logique des signes et rapprochement des notes — n'ont pas été validés. Corrigez chacun, ou consignez qu'il tient, avant l'export.",
    "r.open": "ouverts",
    "r.accepted": "acceptés",
    "r.passed": "lignes sans constat",
    "r.reconciliation": "RAPPROCHEMENT",
    "r.suggestedFix": "CORRECTION SUGGÉRÉE",
    "r.openInWorkspace": "Ouvrir dans l'espace de travail",
    "r.acceptAsIs": "Accepter tel quel",
    "r.statusAccepted": "ACCEPTÉ",
    "r.acceptedBy": "Accepté par",
    "r.reason": "Motif",
    "r.reasonPlaceholder":
      "Pourquoi ces chiffres tiennent-ils ? Le motif est enregistré à votre nom, en regard des chiffres affichés.",
    "r.reasonRequired":
      "Un motif est obligatoire — une acceptation sans rien d'énoncé est une affirmation non signée.",
    "r.withdrawAcceptance": "Retirer l'acceptation",
    "r.orphanIdentity": "identifiant",
    "r.orphanIdentityHelp": "Un autre verdict en vigueur porte ce même nom. Identifiant complet :",
    "r.withheldWithdrawable":
      "Une acceptation est enregistrée pour cette identité et elle est retenue sur toutes les fiches du groupe : aucune ne l'affiche. Le retrait la met hors d'effet — la trace de qui a accepté quoi est conservée, et le constat reste soulevé.",
    "r.staleStrip": "constats acceptés reposent sur des chiffres qui ont changé depuis",
    "r.staleTitle": "Accepté au regard de chiffres différents",
    "r.staleChanged": "Ce qui a changé depuis l'acceptation :",
    "r.statusConflict": "CONFLIT D'IDENTITÉ",
    "r.conflictTitle": "La file ne peut pas distinguer ces constats les uns des autres",
    "r.conflictStrip": "constats partagent une même identité tout en affichant des chiffres différents : aucun d'eux ne peut être accepté",
    "r.unknownState":
      "Cette version ne reconnaît pas l'état renvoyé par le serveur pour ce constat ; aucune action de jugement n'est proposée. État renvoyé :",
    "r.conflictRefused":
      "Refusé : ce constat partage une identité avec un autre qui affichait des chiffres différents ; aucun jugement ne peut donc être enregistré. Rechargez la file — l'extraction doit d'abord les distinguer.",
    "r.writeConflict":
      "Rien n'a été enregistré — une autre écriture sur ce constat est arrivée au même instant et cette acceptation n'a pas été conservée. Rechargez la file et jugez les chiffres tels qu'ils sont désormais.",
    "r.noJudgement":
      "Rien n'a été retiré : plus aucune acceptation n'est enregistrée pour ce constat. Elle a déjà été retirée — peut-être depuis une autre fiche partageant son identité. Rechargez la file.",
    "r.ambiguousNote": "constats identiques partagent ce jugement — en accepter un les accepte tous",
    "r.orphanedTitle": "jugements antérieurs ne correspondent à aucun constat de cette exécution",
    "r.orphanedNote":
      "Les constats visés ont été corrigés, ne sont plus soulevés, ou relèvent d'une catégorie que cette file ne porte plus. Rien n'est supprimé, et aucun d'eux ne compte dans la file ci-dessus.",
    "r.orphanedInForce":
      "Chacune de ces acceptations reste en vigueur : si le même constat est soulevé de nouveau avec les mêmes chiffres, il reviendra déjà accepté, sous un jugement que personne n'a refait.",
    "r.orphanedWithdrawHint": "Retirez celles qui ne doivent pas être reportées.",
    "r.weakMappings": "lignes portent un rattachement faible. Aucun constat n'est levé pour elles — un rattachement faible se corrige en le rattachant mieux, pas par la revue — et chacune affiche sa propre confiance dans l'espace de travail.",
    "r.flipSignOf": "Inverser le signe de",
    "r.manualFixOnly": "Aucune correction automatique — appliquez la correction ci-dessus à la main.",
    "r.remapHead": "RATTACHER À UN AUTRE POSTE",
    "r.remapPick": "Choisir un poste du modèle…",
    "r.remapUnmap": "— laisser cette ligne non rattachée —",
    "r.remapApply": "Rattacher cette ligne",
    "r.remapReasonPlaceholder": "Pourquoi cette ligne relève-t-elle de ce poste ? Enregistré à votre nom sur la ligne.",
    "r.remapNoTargets": "Cette exécution n'a désigné aucun modèle : il n'y a donc aucun poste de rattachement.",
    "r.remapCurrent": "Rattachée à",
    "r.remapNowUnmapped": "non rattachée",
    "r.evidenceChanged":
      "Les chiffres ont changé pendant que cette fiche était ouverte : l'acceptation a été refusée. Rechargez la file et jugez les chiffres tels qu'ils sont désormais.",
    "r.cov.title": "Couverture des relations",
    "r.cov.missing": "Ces données de révision ne comportaient aucun rapport de couverture : ce que les relations du modèle ont contrôlé sur ce dépôt est donc inconnu.",
    "r.cov.subhead":
      "Ce que les relations du modèle pouvaient dire de ce dépôt. Chaque nombre ici compte des RELATIONS — ni constats, ni lignes.",
    "r.cov.relationsEvaluated": "relations évaluées",
    "r.cov.held": "vérifiées",
    "r.cov.failed": "en échec",
    "r.cov.notEvaluable": "non évaluables",
    "r.cov.ofThoseThatRan": "vérifiées, parmi les relations évaluées",
    "r.cov.ofThoseAnswerable": "évaluées, parmi les relations auxquelles ce dépôt pouvait répondre",
    "r.cov.na": "aucune relation n'a été évaluée, donc aucun taux de réussite",
    "r.cov.provedNothing": "aucune relation n'a pu être évaluée — rien ici n'est vérifié",
    "r.cov.notCounted": "non comptées",
    "r.cov.reportedAbove": "relations en échec sont signalées par un constat ci-dessus",
    "r.cov.recomputedFrom": "Recalculé à partir de l'exécution",
  },
};
