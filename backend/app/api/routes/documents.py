"""Document endpoints: upload (+ upfront integrity/classification) and fetch."""
from __future__ import annotations

import copy
import re
from collections.abc import Iterable

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import db, object_store
from app.ports.object_store import LocalObjectStore
from app.security import Permission, Principal, Role, current_principal, require
from app.services.documents import analyze_document, content_hash
from app.services import review_lines
from app.services.page_scope import normalise_kind, scope_counts
from app.services.periods import (
    bases_present, basis_values as _basis_values_of, concept_value as _concept_value,
    summable as _summable,
    edited_for as _edited_for, effective_basis,
    names_a_component, period_displays, slot_for, split_current_prior)
from app.services.reconcile import tie_status

router = APIRouter(prefix="/documents", tags=["documents"])

_ELEVATED = {Role.ADMIN, Role.REVIEWER}  # roles that work across the whole queue


def _can_access(doc, principal: Principal) -> bool:
    """A document is visible to its uploader, and to reviewers/admins who work every
    analyst's queue. Ownerless (legacy/seeded) documents stay open."""
    return principal.role in _ELEVATED or not doc.owner or doc.owner == principal.username


def authorized_document(
    document_id: str,
    session: Session = Depends(db),
    principal: Principal = Depends(current_principal),
):
    """Resolve a document and enforce ownership. Returns 404 (not 403) for a document the
    caller may not see, so existence isn't leaked across tenants."""
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None or not _can_access(row, principal):
        raise HTTPException(status_code=404, detail="Document not found")
    return row

# Bounded translations for the fixed vocabulary the real integrity/review surfaces emit, so
# a real run localizes like the demo path. Dynamic parts (source labels, finding messages)
# stay verbatim — they're data, not chrome.
_TR: dict[str, dict[str, str]] = {
    # KPI / Additional-items views
    "KPIs": {"zh": "关键指标", "ar": "المؤشرات الرئيسية", "fr": "Indicateurs clés"},
    "Computed KPIs": {"zh": "计算得出的关键指标", "ar": "مؤشرات محسوبة",
                      "fr": "Indicateurs calculés"},
    "Extracted, not on a statement": {"zh": "已提取，但不在报表中",
                                      "ar": "مستخرج، وليس في أي قائمة",
                                      "fr": "Extrait, hors états financiers"},
    "computed": {"zh": "计算值", "ar": "محسوب", "fr": "calculé"},
    "unmapped": {"zh": "未映射", "ar": "غير مُعيَّن", "fr": "non mappé"},
    "inputs not extracted": {"zh": "输入项未提取", "ar": "المدخلات غير مستخرجة",
                             "fr": "entrées non extraites"},
    "numerator": {"zh": "分子", "ar": "البسط", "fr": "numérateur"},
    "denominator": {"zh": "分母", "ar": "المقام", "fr": "dénominateur"},
    "Not mapped to any concept": {"zh": "未映射到任何概念", "ar": "غير مُعيَّن إلى أي مفهوم",
                                  "fr": "Non rattaché à un concept"},
    "Mapped, but not on any statement in this template":
        {"zh": "已映射，但不在此模板的任何报表中",
         "ar": "مُعيَّن، لكنه ليس في أي قائمة في هذا القالب",
         "fr": "Rattaché, mais absent des états de ce modèle"},
    # integrity grades
    "Not analyzed": {"zh": "未分析", "ar": "لم يُحلَّل", "fr": "Non analysé"},
    "Blocked": {"zh": "已阻止", "ar": "محظور", "fr": "Bloqué"},
    "Ready to extract": {"zh": "可提取", "ar": "جاهز للاستخراج", "fr": "Prêt à extraire"},
    "Minor issues": {"zh": "存在小问题", "ar": "مشكلات طفيفة", "fr": "Problèmes mineurs"},
    "Needs attention": {"zh": "需要关注", "ar": "يتطلب انتباهًا", "fr": "À vérifier"},
    # summaries
    "No integrity report is available for this document. Re-upload to run the pre-flight checks.":
        {"zh": "该文档没有可用的完整性报告。请重新上传以运行预检。",
         "ar": "لا يتوفر تقرير سلامة لهذا المستند. أعد الرفع لتشغيل الفحوصات المسبقة.",
         "fr": "Aucun rapport d'intégrité disponible pour ce document. Re-téléversez pour lancer les contrôles."},
    "Resolve the blocking issues before extracting.":
        {"zh": "请在提取前解决阻断性问题。", "ar": "عالج المشكلات الحاجبة قبل الاستخراج.",
         "fr": "Résolvez les problèmes bloquants avant l'extraction."},
    "No material issues detected.":
        {"zh": "未发现重大问题。", "ar": "لم تُكتشف مشكلات جوهرية.", "fr": "Aucun problème important détecté."},
    "Proceed with caution — review the flagged items.":
        {"zh": "请谨慎处理——请查看标记的项目。", "ar": "تابع بحذر — راجع العناصر المُعلَّمة.",
         "fr": "Procédez avec prudence — vérifiez les éléments signalés."},
    "Several issues detected — review before extracting.":
        {"zh": "发现多个问题——请在提取前查看。", "ar": "اكتُشفت عدة مشكلات — راجعها قبل الاستخراج.",
         "fr": "Plusieurs problèmes détectés — vérifiez avant l'extraction."},
    # stat labels / subs / values
    "Pages": {"zh": "页数", "ar": "الصفحات", "fr": "Pages"},
    "Document type": {"zh": "文档类型", "ar": "نوع المستند", "fr": "Type de document"},
    "Scanned pages": {"zh": "扫描页", "ar": "الصفحات الممسوحة", "fr": "Pages numérisées"},
    "Issues": {"zh": "问题", "ar": "المشكلات", "fr": "Problèmes"},
    "detected": {"zh": "已检测", "ar": "مكتشفة", "fr": "détectées"},
    "native vs scanned": {"zh": "原生与扫描", "ar": "أصلي مقابل ممسوح", "fr": "natif vs numérisé"},
    "need OCR": {"zh": "需要 OCR", "ar": "تحتاج OCR", "fr": "nécessitent l'OCR"},
    "found": {"zh": "已发现", "ar": "موجودة", "fr": "trouvés"},
    "not checked": {"zh": "未检查", "ar": "لم تُفحص", "fr": "non vérifié"},
    "Native": {"zh": "原生", "ar": "أصلي", "fr": "Natif"},
    "Scanned": {"zh": "扫描", "ar": "ممسوح", "fr": "Numérisé"},
    "Mixed": {"zh": "混合", "ar": "مختلط", "fr": "Mixte"},
    "Unknown": {"zh": "未知", "ar": "غير معروف", "fr": "Inconnu"},
    # statuses
    "Blocking": {"zh": "阻断", "ar": "حاجب", "fr": "Bloquant"},
    "Advisory": {"zh": "提示", "ar": "إرشادي", "fr": "Indicatif"},
    "Passed": {"zh": "通过", "ar": "ناجح", "fr": "Réussi"},
    # special
    "No issues detected": {"zh": "未发现问题", "ar": "لم تُكتشف مشكلات", "fr": "Aucun problème détecté"},
    "The document passed all integrity checks.":
        {"zh": "该文档通过了所有完整性检查。", "ar": "اجتاز المستند جميع فحوصات السلامة.",
         "fr": "Le document a réussi tous les contrôles d'intégrité."},
    "Integrity not available": {"zh": "无完整性信息", "ar": "السلامة غير متاحة", "fr": "Intégrité indisponible"},
    "This document has no stored pre-flight report.":
        {"zh": "该文档没有已存储的预检报告。", "ar": "لا يوجد تقرير مسبق مخزَّن لهذا المستند.",
         "fr": "Ce document n'a pas de rapport de contrôle enregistré."},
    # review
    "All": {"zh": "全部", "ar": "الكل", "fr": "Tous"},
    "Unmapped": {"zh": "未映射", "ar": "غير مُطابَق", "fr": "Non mappé"},
    "Low confidence": {"zh": "低置信度", "ar": "ثقة منخفضة", "fr": "Faible confiance"},
    "No canonical concept matched with confidence. Pick the correct template line item, "
    "or add an alias so future runs map it automatically.":
        {"zh": "没有可信匹配的规范概念。请选择正确的模板项目，或添加别名以便后续自动映射。",
         "ar": "لم يُطابَق أي مفهوم قياسي بثقة. اختر بند القالب الصحيح أو أضف اسمًا بديلًا ليُطابَق تلقائيًا لاحقًا.",
         "fr": "Aucun concept canonique n'a été associé avec confiance. Choisissez le bon poste du modèle, ou ajoutez un alias."},
    "The mapping is uncertain. Confirm the concept is correct or reassign it; the value and "
    "its source location are shown so you can verify against the document.":
        {"zh": "映射不确定。请确认概念是否正确或重新指定；已显示数值及其来源位置以便与文档核对。",
         "ar": "التطابق غير مؤكد. أكِّد صحة المفهوم أو أعِد تعيينه؛ تظهر القيمة وموقع مصدرها للتحقق مقابل المستند.",
         "fr": "Le mappage est incertain. Confirmez le concept ou réattribuez-le ; la valeur et sa source sont affichées pour vérification."},
}


_TR.update({
    "Unrecognized file format": {"zh": "无法识别的文件格式", "ar": "تنسيق ملف غير معروف", "fr": "Format de fichier non reconnu"},
    "File appears corrupt": {"zh": "文件似乎已损坏", "ar": "يبدو الملف تالفًا", "fr": "Le fichier semble corrompu"},
    "Password-protected document": {"zh": "受密码保护的文档", "ar": "مستند محمي بكلمة مرور", "fr": "Document protégé par mot de passe"},
    "Encrypted document": {"zh": "加密文档", "ar": "مستند مشفَّر", "fr": "Document chiffré"},
    "Mixed scanned and native pages": {"zh": "扫描页与原生页混合", "ar": "صفحات ممسوحة وأصلية مختلطة", "fr": "Pages numérisées et natives mélangées"},
    "Rotated page": {"zh": "页面旋转", "ar": "صفحة مُدارة", "fr": "Page pivotée"},
    "Inconsistent page dimensions": {"zh": "页面尺寸不一致", "ar": "أبعاد صفحات غير متسقة", "fr": "Dimensions de page incohérentes"},
    "Hidden worksheet": {"zh": "隐藏的工作表", "ar": "ورقة عمل مخفية", "fr": "Feuille masquée"},
    "No pages found": {"zh": "未找到页面", "ar": "لا توجد صفحات", "fr": "Aucune page trouvée"},
    "Blank page": {"zh": "空白页", "ar": "صفحة فارغة", "fr": "Page vierge"},
})


# structural validation (template rollups / identities)
_TR.update({
    "Figure does not equal its template components":
        {"zh": "数字与模板组成部分不符", "ar": "الرقم لا يساوي مكوناته في القالب",
         "fr": "Le montant ne correspond pas à ses composantes du modèle"},
    "Reported": {"zh": "已报告", "ar": "المُبلَّغ", "fr": "Déclaré"},
    "Sum of template components": {"zh": "模板组成部分合计", "ar": "مجموع مكونات القالب",
                                   "fr": "Somme des composantes du modèle"},
    "The reported figure does not equal the components the template says it is made of, so a "
    "value is on the wrong line. Check the components and the total against the document.":
        {"zh": "报告的数字与模板所述的组成部分不相等，说明某个数值被放在了错误的行。请将各组成部分及合计与文档核对。",
         "ar": "الرقم المُبلَّغ لا يساوي المكونات التي يحددها القالب، أي أن قيمة وُضعت في السطر الخطأ. راجع المكونات والمجموع مقابل المستند.",
         "fr": "Le montant déclaré ne correspond pas aux composantes définies par le modèle : une valeur est sur la mauvaise ligne. Vérifiez les composantes et le total dans le document."},
    "Flipping the sign of": {"zh": "改变符号的项目：", "ar": "قلب إشارة", "fr": "Inverser le signe de"},
    "would make it balance.": {"zh": "即可使其平衡。", "ar": "سيجعلها متوازنة.",
                               "fr": "permettrait d'équilibrer."},
})


# The Current/Prior fallback used when a filing's columns carry no printed header. Copied verbatim
# from services/export.py:36-37 so the two modules cannot drift: without these entries
# ``_t("Current", "zh")`` returned English, so a zh/ar/fr reader saw an English column header on
# every native-PDF statement and on every note detail.
_TR.update({
    "Current": {"zh": "本期", "ar": "الحالية", "fr": "Actuel"},
    "Prior": {"zh": "上期", "ar": "السابقة", "fr": "Précédent"},
})


# Coverage contract + review judgements. The coverage vocabulary is deliberately verbal rather
# than numeric ("Nothing verified", not "0%"): the whole point of services/coverage.py is that a
# count of passes read alone is how a barely-checked filing looks clean.
_TR.update({
    "All statements": {"zh": "所有报表", "ar": "جميع القوائم", "fr": "Tous les états"},
    # statement coverage statuses
    "Nothing verified": {"zh": "未验证任何项", "ar": "لم يُتحقَّق من شيء", "fr": "Rien de vérifié"},
    "Partly verified": {"zh": "部分已验证", "ar": "تم التحقق جزئيًا", "fr": "Partiellement vérifié"},
    "Failed": {"zh": "未通过", "ar": "فاشل", "fr": "En échec"},
    "Fully verified": {"zh": "全部已验证", "ar": "تم التحقق بالكامل", "fr": "Entièrement vérifié"},
    "Not in this filing": {"zh": "本次报告中不存在", "ar": "غير موجود في هذا التقرير",
                           "fr": "Absent de ce dépôt"},
    # skip-taxonomy buckets
    "Inputs not extracted": {"zh": "输入项未提取", "ar": "المدخلات غير مستخرجة",
                             "fr": "Entrées non extraites"},
    "better extraction would recover these":
        {"zh": "提升提取质量即可恢复这些关系", "ar": "استخراج أفضل سيستعيد هذه العلاقات",
         "fr": "une meilleure extraction les récupérerait"},
    "Fed by a derived value": {"zh": "由派生值驱动", "ar": "مُغذّى بقيمة مشتقة",
                               "fr": "Alimenté par une valeur dérivée"},
    "fed by a derived value — cannot fail, however good extraction gets":
        {"zh": "由派生值驱动——无论提取质量多高都不可能失败",
         "ar": "مُغذّى بقيمة مشتقة — لا يمكن أن يفشل، مهما تحسّن الاستخراج",
         "fr": "alimenté par une valeur dérivée — ne peut pas échouer, quelle que soit la qualité de l'extraction"},
    "No printed subtotal": {"zh": "文档未打印小计", "ar": "لا يوجد مجموع فرعي مطبوع",
                            "fr": "Aucun sous-total imprimé"},
    "the filing prints no subtotal to reconcile against":
        {"zh": "该报告未打印可用于核对的小计", "ar": "لا يطبع التقرير مجموعًا فرعيًا للمطابقة",
         "fr": "le dépôt n'imprime aucun sous-total à rapprocher"},
    "Rule cannot run as authored": {"zh": "规则按当前写法无法运行",
                                    "ar": "القاعدة لا يمكن تشغيلها كما صيغت",
                                    "fr": "Règle inexécutable telle qu'écrite"},
    "the rule cannot run as authored — an authoring defect, not an extraction gap":
        {"zh": "该规则按当前写法无法运行——这是规则编写缺陷，而非提取缺口",
         "ar": "القاعدة لا يمكن تشغيلها كما صيغت — عيب في الصياغة، لا فجوة استخراج",
         "fr": "la règle ne peut pas s'exécuter telle qu'écrite — un défaut de rédaction, pas une lacune d'extraction"},
    "Unclassified skip reason": {"zh": "未分类的跳过原因", "ar": "سبب تجاوز غير مصنَّف",
                                 "fr": "Motif d'exclusion non classé"},
    "a new skip reason nobody has classified":
        {"zh": "出现了尚无人分类的新跳过原因", "ar": "سبب تجاوز جديد لم يصنّفه أحد",
         "fr": "un nouveau motif d'exclusion que personne n'a classé"},
    "Statement not in this filing": {"zh": "本次报告不含该报表",
                                     "ar": "القائمة غير موجودة في هذا التقرير",
                                     "fr": "État absent de ce dépôt"},
    "not counted — this filing has no such statement":
        {"zh": "不计入——本次报告没有该报表",
         "ar": "غير محتسب — لا توجد مثل هذه القائمة في هذا التقرير",
         "fr": "non compté — ce dépôt ne contient pas cet état"},
    # alarms
    "Blocking rule cannot be enforced": {"zh": "阻断性规则无法执行",
                                         "ar": "قاعدة حاجبة لا يمكن إنفاذها",
                                         "fr": "Règle bloquante inapplicable"},
    "declared blocking and cannot run as authored, so it fires on no filing — this filing was "
    "never checked against it.":
        {"zh": "该规则被声明为阻断性，但按当前写法无法运行，因此对任何报告都不会触发——本次报告从未据此检查。",
         "ar": "أُعلنت حاجبة ولا يمكن تشغيلها كما صيغت، فهي لا تُطلق على أي تقرير — ولم يُفحص هذا التقرير مقابلها أبدًا.",
         "fr": "déclarée bloquante et inexécutable telle qu'écrite : elle ne se déclenche sur aucun dépôt — ce dépôt n'a jamais été contrôlé par elle."},
    "Statement proved nothing": {"zh": "该报表未验证任何内容",
                                 "ar": "القائمة لم تُثبت شيئًا", "fr": "État sans aucune vérification"},
    "every relation declared for this statement was skipped, so it has no failures and has "
    "proved nothing.":
        {"zh": "为该报表声明的每一条关系都被跳过，因此它没有失败项，也没有验证任何内容。",
         "ar": "تم تجاوز كل علاقة مُعلنة لهذه القائمة، فلا توجد بها إخفاقات ولم تُثبت شيئًا.",
         "fr": "toutes les relations déclarées pour cet état ont été ignorées : il n'a aucun échec et n'a rien prouvé."},
    "Mostly checking its own arithmetic": {"zh": "多数只是在核对自身算术",
                                           "ar": "يتحقق في الغالب من حسابه الذاتي",
                                           "fr": "Vérifie surtout sa propre arithmétique"},
    "more relations were fed by derived values than were actually evaluated, so the validation "
    "layer is largely confirming its own arithmetic.":
        {"zh": "由派生值驱动的关系数量多于实际评估的关系数量，因此验证层多半只是在确认自身的算术。",
         "ar": "عدد العلاقات المُغذّاة بقيم مشتقة يفوق عدد ما تم تقييمه فعليًا، فطبقة التحقق تؤكد في الغالب حسابها الذاتي.",
         "fr": "plus de relations ont été alimentées par des valeurs dérivées qu'il n'y en a eu d'évaluées : la couche de validation confirme surtout sa propre arithmétique."},
    "Pipeline defect": {"zh": "流水线缺陷", "ar": "عيب في خط المعالجة", "fr": "Défaut de traitement"},
    "a rule that needs nothing of the filing still could not be run, so this is a defect in the "
    "pipeline or the rulebook rather than a fact about the document.":
        {"zh": "一条无需依赖报告内容的规则仍然无法运行，因此这是流水线或规则手册的缺陷，而不是关于该文档的事实。",
         "ar": "قاعدة لا تحتاج شيئًا من التقرير لم يُمكن تشغيلها، فهذا عيب في خط المعالجة أو في دليل القواعد وليس واقعة عن المستند.",
         "fr": "une règle qui n'exige rien du dépôt n'a pas pu s'exécuter : c'est un défaut du traitement ou du référentiel, pas un fait sur le document."},
    # coverage unavailable
    "This document has not been extracted, so no relation has been evaluated.":
        {"zh": "该文档尚未提取，因此没有评估任何关系。",
         "ar": "لم يُستخرج هذا المستند، لذا لم تُقيَّم أي علاقة.",
         "fr": "Ce document n'a pas été extrait : aucune relation n'a été évaluée."},
    "No template was attached to this run, so structural validation never ran.":
        {"zh": "本次运行未附加模板，因此结构性校验从未运行。",
         "ar": "لم يُرفق قالب بهذا التشغيل، لذا لم يُنفَّذ التحقق البنيوي إطلاقًا.",
         "fr": "Aucun modèle n'était attaché à ce traitement : la validation structurelle n'a jamais eu lieu."},
    "A template was attached but it declares no relation for this filing, so nothing was "
    "checked. That is an authoring gap, not a clean result.":
        {"zh": "已附加模板，但它没有为本次报告声明任何关系，因此什么都没有检查。这是规则编写的缺口，而不是干净的结果。",
         "ar": "أُرفق قالب لكنه لا يعلن أي علاقة لهذا التقرير، فلم يُفحص شيء. هذه فجوة صياغة، وليست نتيجة سليمة.",
         "fr": "Un modèle est attaché mais ne déclare aucune relation pour ce dépôt : rien n'a été contrôlé. C'est une lacune de rédaction, pas un résultat propre."},
    "The seeded sample project carries no structural validation run.":
        {"zh": "内置示例项目没有结构性校验运行记录。",
         "ar": "المشروع النموذجي المُهيَّأ لا يحتوي على تشغيل تحقق بنيوي.",
         "fr": "Le projet de démonstration ne comporte aucun traitement de validation structurelle."},
    # the flip-sign edit comment + the edited-inputs caveat
    "Sign flipped on the figure the structural check named as the one whose sign, reversed, "
    "would satisfy the relation":
        {"zh": "已对结构性校验指认的数字取反——该校验认为反转其符号即可使关系成立",
         "ar": "تم قلب إشارة الرقم الذي حدده التحقق البنيوي بأن عكس إشارته يُحقّق العلاقة",
         "fr": "Signe inversé sur le montant désigné par le contrôle structurel comme celui dont l'inversion satisferait la relation"},
    "A figure this relation uses has been edited since it was evaluated. The relation is "
    "re-evaluated on the next extraction.":
        {"zh": "该关系所用的某个数字在其评估之后被修改过。该关系将在下一次提取时重新评估。",
         "ar": "تم تعديل رقم تستخدمه هذه العلاقة بعد تقييمها. تُعاد تقييم العلاقة في الاستخراج التالي.",
         "fr": "Un montant utilisé par cette relation a été modifié depuis son évaluation. La relation sera réévaluée lors de la prochaine extraction."},
    # evidence-row labels for an accepted finding (same two-column shape as a check's `calc`).
    # Several of these are also the labels on the live check cards, which were reaching a
    # zh/ar/fr reader in English because they were never in this table.
    "Total assets": {"zh": "资产总计", "ar": "إجمالي الأصول", "fr": "Total de l'actif"},
    "Total equity and liabilities": {"zh": "权益与负债总计", "ar": "إجمالي حقوق الملكية والالتزامات",
                                     "fr": "Total du passif et des capitaux propres"},
    "Total equity per the balance sheet": {"zh": "资产负债表列示的权益总额",
                                           "ar": "إجمالي حقوق الملكية حسب الميزانية العمومية",
                                           "fr": "Total des capitaux propres selon le bilan"},
    "Difference": {"zh": "差额", "ar": "الفرق", "fr": "Écart"},
    "Face figure": {"zh": "表内数字", "ar": "الرقم في القائمة", "fr": "Montant au bilan"},
    "Residual": {"zh": "余额差", "ar": "المتبقي", "fr": "Résidu"},
    "Residual vs note total": {"zh": "与附注合计的差额", "ar": "المتبقي مقابل إجمالي الإيضاح",
                               "fr": "Résidu par rapport au total de la note"},
    "Yes": {"zh": "是", "ar": "نعم", "fr": "Oui"},
    "No": {"zh": "否", "ar": "لا", "fr": "Non"},
    "Closing balance": {"zh": "期末余额", "ar": "الرصيد الختامي", "fr": "Solde de clôture"},
    "Computed": {"zh": "计算值", "ar": "محسوب", "fr": "Calculé"},
    "Printed": {"zh": "文档打印值", "ar": "المطبوع", "fr": "Imprimé"},
    "Value": {"zh": "数值", "ar": "القيمة", "fr": "Valeur"},
    "Sign suspect": {"zh": "疑似符号错误项", "ar": "الإشارة المشتبه بها", "fr": "Signe suspect"},
    "Components": {"zh": "组成部分", "ar": "المكونات", "fr": "Composantes"},
    "Totals derived from the section subtotals":
        {"zh": "合计由各分节小计推导得出", "ar": "المجاميع مشتقة من المجاميع الفرعية للأقسام",
         "fr": "Totaux dérivés des sous-totaux de section"},
    # orphaned-judgement subject phrasing
    "Balance sheet identity": {"zh": "资产负债表恒等式", "ar": "معادلة الميزانية العمومية",
                               "fr": "Équation du bilan"},
    "Equity statement closing balance": {"zh": "权益变动表期末余额",
                                         "ar": "الرصيد الختامي لقائمة التغيرات في حقوق الملكية",
                                         "fr": "Solde de clôture de l'état des variations des capitaux propres"},
    "Note": {"zh": "附注", "ar": "إيضاح", "fr": "Note"},
    "Template relation": {"zh": "模板关系", "ar": "علاقة القالب", "fr": "Relation du modèle"},
    "Printed subtotal": {"zh": "打印的小计", "ar": "المجموع الفرعي المطبوع",
                         "fr": "Sous-total imprimé"},
    "Unverified subtotal": {"zh": "未验证的小计", "ar": "مجموع فرعي غير مُتحقَّق منه",
                            "fr": "Sous-total non vérifié"},
    "Unmapped line": {"zh": "未映射的行", "ar": "سطر غير مُعيَّن", "fr": "Ligne non rattachée"},
    "Low-confidence mapping": {"zh": "低置信度映射", "ar": "تعيين منخفض الثقة",
                               "fr": "Rattachement à faible confiance"},
    "A finding that is no longer raised": {"zh": "已不再提出的问题",
                                           "ar": "ملاحظة لم تُعد تُرفع",
                                           "fr": "Une anomalie qui n'est plus signalée"},
})


# A failed rulebook guard, and the one state in which the queue refuses to answer. Both are card
# vocabulary, so both belong here: an untranslated string on a review card is a zh/ar/fr reader
# being shown English, which is how several of the live check labels were reaching them before.
_TR.update({
    "Rulebook guard failed": {"zh": "规则手册的守卫检查未通过",
                              "ar": "فشل شرط في كتاب القواعد",
                              "fr": "Garde-fou du référentiel en échec"},
    "Rule": {"zh": "规则", "ar": "القاعدة", "fr": "Règle"},
    "Lines in violation": {"zh": "违反规则的行", "ar": "السطور المخالفة",
                           "fr": "Lignes en infraction"},
    # The unmapped card's own subject matter. "Mapped to" carries the concept when the row DID map to
    # one the template does not declare — the two ways into that one category — and the em-dash
    # sentence when nothing claimed the caption at all.
    # THE CARDS' SHARED CHROME AND THE TWO OLDEST CARDS' PROSE, which had no entries at all: the
    # balance card's title and fix, the calculated card's title, fix and both calc labels, the
    # severity every accounting card prints, and the accounting chip. Requirement 21 is input=output
    # parity, and the queue's most prominent card was served in English under a Chinese, Arabic or
    # French heading. Found by sweeping every string the route actually serves against this table
    # (tests/test_review_locales.py does that sweep now, so it cannot silently regrow).
    "Check failed": {"zh": "校验未通过", "ar": "فشل الفحص", "fr": "Contrôle en échec"},
    "Checks": {"zh": "校验", "ar": "الفحوص", "fr": "Contrôles"},
    "Balance sheet does not balance": {
        "zh": "资产负债表不平衡", "ar": "الميزانية العمومية غير متوازنة",
        "fr": "Le bilan n'est pas équilibré"},
    "Assets do not equal equity plus liabilities. Check the extracted totals and their components "
    "against the document.": {
        "zh": "资产总额不等于权益与负债之和。请对照原文核对提取的合计及其组成项目。",
        "ar": "الأصول لا تساوي حقوق الملكية زائد الالتزامات. راجع المجاميع المستخرجة ومكوّناتها "
              "مقابل المستند.",
        "fr": "L'actif n'égale pas les capitaux propres plus le passif. Vérifiez les totaux "
              "extraits et leurs composantes par rapport au document."},
    "Printed subtotal differs from its components": {
        "zh": "列示小计与其组成项目不符", "ar": "المجموع الفرعي المطبوع يخالف مكوّناته",
        "fr": "Le sous-total imprimé diffère de ses composantes"},
    "The face shows the computed figure. The document printed a different one, so a component is "
    "mis-mapped, missing, or double-counted — check the components below against the page.": {
        "zh": "报表表面显示的是计算值。文件列示的数字与之不同，说明某个组成项目映射错误、缺失或被重复计算——"
              "请对照原页核对下列组成项目。",
        "ar": "يعرض وجه القائمة الرقم المحسوب، وقد طبع المستند رقمًا مختلفًا: أي أن أحد المكوّنات "
              "مُطابَق خطأً أو مفقود أو محتسب مرتين — راجع المكوّنات أدناه مقابل الصفحة.",
        "fr": "L'état affiche le chiffre calculé. Le document en a imprimé un autre : une "
              "composante est mal rattachée, manquante ou comptée deux fois — vérifiez les "
              "composantes ci-dessous par rapport à la page."},
    "Printed in the document": {"zh": "文件列示值", "ar": "المطبوع في المستند",
                                "fr": "Imprimé dans le document"},
    "Computed from components": {"zh": "由组成项目计算", "ar": "محسوب من المكوّنات",
                                 "fr": "Calculé à partir des composantes"},
    # The containment card: a printed combined total the lines that replaced it do not account for.
    "Printed total does not match the lines that replaced it": {
        "zh": "列示合计与替代其的行不一致",
        "ar": "الإجمالي المطبوع لا يطابق السطور التي حلّت محلّه",
        "fr": "Le total imprimé ne correspond pas aux lignes qui l'ont remplacé"},
    "Sum of the lines that replaced it": {
        "zh": "替代其的各行合计", "ar": "مجموع السطور التي حلّت محلّه",
        "fr": "Somme des lignes qui l'ont remplacé"},
    "Not on any line": {"zh": "未计入任何行", "ar": "غير مدرج في أي سطر",
                        "fr": "Sur aucune ligne"},
    "This caption was printed as a combined total and its components are on the face as well, so "
    "the combined line was removed to stop the section counting the money twice. The components do "
    "not add up to it, so the difference is on no line: a component is missing from the extraction, "
    "or this caption is not their total. Check the page.": {
        "zh": "该标签以合计形式列示，而其组成项目也出现在报表表面，因此已移除该合计行，以避免该分部重复计算金额。"
              "各组成项目之和与该合计不符，故差额未计入任何行：可能是提取时遗漏了某个组成项目，或该标签并非"
              "这些项目的合计。请核对原页。",
        "ar": "طُبع هذا العنوان كإجمالي مجمَّع، ومكوّناته مدرجة على وجه القائمة أيضًا، فحُذف السطر المجمَّع "
              "لمنع احتساب المبلغ مرتين في هذا القسم. ومجموع المكوّنات لا يساوي هذا الإجمالي، فالفرق غير "
              "مدرج في أي سطر: إمّا أن مكوّنًا لم يُستخرج، أو أن هذا العنوان ليس إجماليها. راجع الصفحة.",
        "fr": "Ce libellé était imprimé comme un total combiné et ses composantes figurent aussi sur "
              "l'état : la ligne combinée a donc été retirée pour éviter que la section ne compte le "
              "montant deux fois. Les composantes n'en font pas la somme, si bien que l'écart ne "
              "figure sur aucune ligne : soit une composante manque à l'extraction, soit ce libellé "
              "n'est pas leur total. Vérifiez la page."},
    "Source label": {"zh": "原始标签", "ar": "التسمية الأصلية", "fr": "Libellé source"},
    "Mapped to": {"zh": "映射到", "ar": "مطابق إلى", "fr": "Rattaché à"},
    "— (no confident match)": {"zh": "—（无可信匹配）", "ar": "— (لا تطابق موثوق)",
                               "fr": "— (aucune correspondance fiable)"},
    "Face lines that do not tie": {"zh": "未勾稽的报表行数", "ar": "سطور القوائم غير المطابَقة",
                                   "fr": "Lignes non rapprochées"},
    "Method": {"zh": "匹配方式", "ar": "طريقة المطابقة", "fr": "Méthode"},
    "Confidence": {"zh": "置信度", "ar": "درجة الثقة", "fr": "Confiance"},
    "Confidence band": {"zh": "置信度区间", "ar": "نطاق درجة الثقة",
                        "fr": "Plage de confiance"},
    # The note-tie card's at-a-glance magnitude. Translated here rather than left to fall back to
    # English, for the reason the guard vocabulary above is: an untranslated card string is a
    # zh/ar/fr reader shown English on the figure the card is titled after.
    "Total break across the untied face lines":
        {"zh": "未勾稽的报表行合计差额（取绝对值）",
         "ar": "إجمالي الفروق على السطور غير المطابَقة (بالقيمة المطلقة)",
         "fr": "Écart total sur les lignes non rapprochées (en valeur absolue)"},
    "Violations": {"zh": "违反项数量", "ar": "عدد المخالفات", "fr": "Infractions"},
    "The rulebook declares this must hold, and it does not for the lines listed. Check each one "
    "against the document; the guard is re-evaluated on the next extraction.":
        {"zh": "规则手册要求此条件必须成立，但所列各行并不满足。请逐行与文档核对；该守卫检查将在下次提取时重新评估。",
         "ar": "يقرر كتاب القواعد أن هذا الشرط يجب أن يتحقق، ولم يتحقق للسطور المذكورة. راجع كل سطر مقابل المستند؛ ويُعاد تقييم الشرط في الاستخراج التالي.",
         "fr": "Le référentiel exige que cette condition soit vérifiée ; elle ne l'est pas pour les lignes listées. Vérifiez chacune dans le document ; le garde-fou est réévalué à la prochaine extraction."},
    "findings here share one identity but printed different figures, so the queue cannot tell "
    "them apart. None of them can be accepted until the extraction distinguishes them.":
        {"zh": "个问题共用同一标识，但打印的数字不同，因此队列无法区分它们。在提取能够区分它们之前，均不可被接受。",
         "ar": "ملاحظات هنا تتشارك هوية واحدة لكنها طبعت أرقامًا مختلفة، فلا يمكن للقائمة التمييز بينها. لا يمكن قبول أي منها حتى يميّزها الاستخراج.",
         "fr": "anomalies partagent ici une même identité mais affichent des montants différents : la file ne peut pas les distinguer. Aucune ne peut être acceptée avant que l'extraction ne les sépare."},
    "A recorded acceptance for this identity is being withheld: it cannot be matched to one of "
    "these findings, and attributing it to the wrong one would put a name against figures nobody "
    "examined.":
        {"zh": "针对该标识已记录的一项接受意见被暂缓采用：它无法与其中某一个问题对应，若归属错误，便会把某人的名字记在无人核对过的数字上。",
         "ar": "قبولٌ مسجَّل لهذه الهوية مُعلَّق: لا يمكن مطابقته بإحدى هذه الملاحظات، ونسبته إلى الملاحظة الخطأ يضع اسمًا أمام أرقام لم يفحصها أحد.",
         "fr": "Une acceptation enregistrée pour cette identité est suspendue : elle ne peut être rattachée à l'une de ces anomalies, et l'attribuer à la mauvaise apposerait un nom sur des montants que personne n'a examinés."},
})


# The off-template finding: a mapped row whose concept the run's own template does not put on any
# statement. The statement grid no longer shows such a row (see `_build_statement`), so the card is
# the ONLY place its figure appears — an untranslated string here is a zh/ar/fr reader given English
# on the one card standing between a real extracted figure and silence.
#
# "Mapped, but not on any statement in this template" is reused verbatim from the deleted
# Additional-items view rather than re-worded: it is the same claim about the same row, already
# carried in all four locales, and a second wording would be a second name for one condition.
_TR.update({
    "Off template": {"zh": "不在模板中", "ar": "خارج القالب", "fr": "Hors modèle"},
    # Why a statement is serving no rows — see `_statement_refusal`. Translated because this sentence
    # replaces the grid, so an English-only version is a zh/ar/fr reader given a blank spread and no
    # readable reason for it.
    "This run's template declares no statement of this type, so none is shown. Extracted figures "
    "that reach no statement are reported in the review queue; re-extract against a template that "
    "declares it to see it here.":
        {"zh": "本次运行所用模板未声明此类报表，因此不予显示。未进入任何报表的已提取数字会在复核队列中列出；如需在此查看，请使用声明了该报表的模板重新提取。",
         "ar": "لا يعلن قالب هذا التشغيل أي قائمة من هذا النوع، لذا لا تُعرض. الأرقام المستخرجة التي لا تصل إلى أي قائمة تُدرج في قائمة المراجعة؛ أعِد الاستخراج مقابل قالب يعلنها لعرضها هنا.",
         "fr": "Le modèle de cette exécution ne déclare aucun état de ce type : aucun n'est affiché. Les montants extraits qui n'atteignent aucun état sont signalés dans la file de revue ; relancez l'extraction sur un modèle qui le déclare pour le voir ici."},
    "This run's template declares this statement but no line items under it, so there is nothing to "
    "render. Fix the template and re-extract; the figures themselves are unaffected.":
        {"zh": "本次运行所用模板声明了该报表，但其下没有任何项目，因此无内容可显示。请修正模板后重新提取；已提取的数字本身不受影响。",
         "ar": "يعلن قالب هذا التشغيل هذه القائمة لكن دون أي بنود تحتها، فلا يوجد ما يُعرض. أصلِح القالب وأعِد الاستخراج؛ الأرقام نفسها غير متأثرة.",
         "fr": "Le modèle de cette exécution déclare cet état mais aucun poste en dessous : il n'y a rien à afficher. Corrigez le modèle et relancez l'extraction ; les montants eux-mêmes ne sont pas affectés."},
    "No figures were extracted for this statement on this basis. Check the page scope, or the other "
    "basis.":
        {"zh": "本报表在此基础下未提取到任何数字。请检查页面范围，或改用另一基础。",
         "ar": "لم تُستخرج أي أرقام لهذه القائمة على هذا الأساس. راجع نطاق الصفحات، أو الأساس الآخر.",
         "fr": "Aucun montant n'a été extrait pour cet état sur cette base. Vérifiez la portée des pages, ou l'autre base."},
    "Line mapped off the template": {"zh": "映射到模板之外的行",
                                     "ar": "سطر مُعيَّن خارج القالب",
                                     "fr": "Ligne rattachée hors du modèle"},
    "Not on this template": {"zh": "不在此模板中", "ar": "ليس في هذا القالب",
                             "fr": "Absent de ce modèle"},
    "This figure was mapped to a concept the run's template does not put on any statement, so it "
    "appears on no spread. Re-map it to a template line item, or un-map it to record that it "
    "belongs on none — or re-extract against the current template, which may already declare it.":
        {"zh": "该数值被映射到本次运行所用模板未列入任何报表的概念，因此不会出现在任何报表上。请将其重新映射到某个模板项目，或取消映射以记录它不属于任何项目——也可以按当前模板重新提取，当前模板可能已经声明了它。",
         "ar": "عُيِّن هذا الرقم إلى مفهوم لا يضعه قالب هذا التشغيل في أي قائمة، فلا يظهر في أي جدول. أعِد تعيينه إلى بند من القالب، أو ألغِ تعيينه لتسجيل أنه لا ينتمي إلى أي بند — أو أعِد الاستخراج مقابل القالب الحالي، فقد يكون يعلنه بالفعل.",
         "fr": "Ce montant a été rattaché à un concept que le modèle de cette exécution ne place sur aucun état : il n'apparaît sur aucun tableau. Réattribuez-le à un poste du modèle, ou dé-rattachez-le pour enregistrer qu'il n'appartient à aucun — ou relancez l'extraction sur le modèle courant, qui le déclare peut-être déjà."},
})


def _t(s: str, locale: str) -> str:
    if not s or locale == "en":
        return s
    return _TR.get(s, {}).get(locale, s)


def _tag_for(fmt: str) -> str:
    return {"image": "Scanned", "pdf": "Native", "xlsx": "Native"}.get(fmt, "Native")


def _human_size(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(1, n // 1024)} KB"


def _to_source_doc(row) -> dict:
    """Map a stored Document to the frontend SourceDoc shape used by the Upload list."""
    name = row.filename or "document"
    ext = (name.rsplit(".", 1)[-1] if "." in name else row.fmt or "").upper()[:4]
    return {
        "id": row.id,
        "name": name,
        "ext": ext or "DOC",
        "meta": f"{row.page_count} pages · {_human_size(row.byte_size)}",
        "tag": _tag_for(row.fmt),
    }


@router.get("")
def list_documents(session: Session = Depends(db),
                   principal: Principal = Depends(current_principal)) -> dict:
    """Real uploaded documents, most recent first (Upload screen list). Scoped to the
    caller's own uploads; reviewers/admins see the whole queue."""
    from app.db.models import Document

    q = select(Document).order_by(Document.created_at.desc())
    if principal.role not in _ELEVATED:
        q = q.where((Document.owner == principal.username) | (Document.owner == ""))
    rows = session.execute(q).scalars().all()
    return {"documents": [_to_source_doc(r) for r in rows]}


@router.post("", status_code=201, dependencies=[Depends(require(Permission.DOCUMENTS_MANAGE))])
async def upload_document(
    file: UploadFile = File(...),
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
    principal: Principal = Depends(current_principal),
) -> dict:
    from app.db.models import Document

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    digest = content_hash(data)
    # Dedup against the caller's OWN prior upload of the same bytes (reviewers/admins may
    # also reuse an existing accessible copy rather than creating a duplicate).
    existing = session.execute(
        select(Document).where(Document.content_hash == digest)
        .where((Document.owner == principal.username) if principal.role not in _ELEVATED else True)
    ).scalars().first()
    if existing is not None and _can_access(existing, principal):
        return {"id": existing.id, "status": existing.status,
                "content_hash": digest, "duplicate_of": existing.id,
                "page_count": existing.page_count,
                "integrity_report": existing.integrity_report}

    doc_model, _ctx = analyze_document(data, filename=file.filename or "")
    object_key = store.put_bytes(data)

    pages_json = [p.model_dump(mode="json") for p in doc_model.pages]
    row = Document(
        owner=principal.username,
        filename=file.filename or "",
        content_hash=digest,
        byte_size=len(data),
        fmt=doc_model.fmt.value,
        object_key=object_key,
        status="integrity_checked",
        locale=doc_model.locale,
        page_count=len(doc_model.pages),
        integrity_report=doc_model.integrity.model_dump(mode="json") if doc_model.integrity else None,
        # Persist the classified pages so Page Scope / scope editing reuse them (no recompute).
        pages=pages_json,
    )
    session.add(row)
    session.commit()

    return {
        "id": row.id,
        "status": row.status,
        "content_hash": digest,
        "duplicate_of": None,
        "format": row.fmt,
        "locale": row.locale,
        "page_count": row.page_count,
        "pages": [p.model_dump(mode="json") for p in doc_model.pages],
        "integrity_report": row.integrity_report,
    }


@router.delete("/{document_id}", status_code=204,
               dependencies=[Depends(require(Permission.DOCUMENTS_MANAGE))])
def delete_document(
    doc=Depends(authorized_document),
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> Response:
    """Delete a document the caller owns (admins may delete any), along with its extraction
    runs. The stored blob is content-addressed and de-duplicated, so it's only removed when
    no other document still references it."""
    from app.db.models import Document, ExtractionRun, ReviewJudgement

    object_key = doc.object_key
    session.execute(sql_delete(ExtractionRun).where(ExtractionRun.document_id == doc.id))
    # Children before the parent: both tables carry a FK to documents.id, and SQLite tolerates
    # the wrong order while Postgres raises on it — so a deployment on the real database would
    # 500 on every delete of a reviewed document if this ran after session.delete(doc).
    session.execute(sql_delete(ReviewJudgement).where(ReviewJudgement.document_id == doc.id))
    session.delete(doc)
    session.commit()

    shared = session.execute(
        select(Document.id).where(Document.object_key == object_key).limit(1)
    ).first()
    if not shared:
        store.delete(object_key)
    return Response(status_code=204)


_CHECK_TITLES = {
    "UNKNOWN_FORMAT": "Unrecognized file format",
    "CORRUPT": "File appears corrupt",
    "PASSWORD_PROTECTED": "Password-protected document",
    "ENCRYPTED": "Encrypted document",
    "MIXED_SCAN": "Mixed scanned and native pages",
    "ROTATED_PAGE": "Rotated page",
    "INCONSISTENT_DIMENSIONS": "Inconsistent page dimensions",
    "HIDDEN_SHEET": "Hidden worksheet",
    "NO_PAGES": "No pages found",
    "BLANK_PAGE": "Blank page",
}
# Map pipeline severities → the frontend's issue severity + score penalty.
_SEV = {
    "blocker": ("low", 35), "error": ("low", 25),
    "warning": ("warn", 8), "info": ("ok", 2),
}


def _pct_label(ratio: float) -> str:
    """Percent label that never reads a contradictory '0%' for a non-zero ratio."""
    pct = round(ratio * 100)
    if ratio > 0 and pct == 0:
        return "<1%"
    return f"{pct}%"


def _serialize_document_integrity(row, locale: str = "en") -> dict:
    """Map a stored IntegrityReport (per uploaded document) into the IntegrityResponse the
    Document Integrity screen renders — so a real uploaded file surfaces its own pre-flight
    results, not the demo project's. Localized to `locale` for the fixed vocabulary."""
    def L(s: str) -> str:
        return _t(s, locale)

    report = row.integrity_report
    # No stored report → the file was never actually checked. Do NOT fabricate an all-clear;
    # surface an explicit "not analyzed" state so an unchecked file is never shown as clean.
    if not report:
        return {
            "score": 0, "grade": L("Not analyzed"),
            "summary": L("No integrity report is available for this document. Re-upload to run the pre-flight checks."),
            "stats": [
                {"label": L("Pages"), "value": str(row.page_count or 0), "sub": L("detected"), "tone": "neutral"},
                {"label": L("Document type"), "value": L("Unknown"), "sub": L("native vs scanned"), "tone": "warn"},
                {"label": L("Scanned pages"), "value": "—", "sub": L("need OCR"), "tone": "neutral"},
                {"label": L("Issues"), "value": "—", "sub": L("not checked"), "tone": "warn"},
            ],
            "issues": [{"title": L("Integrity not available"),
                        "detail": L("This document has no stored pre-flight report."),
                        "pages": "All", "note": "UNKNOWN", "status": L("Not analyzed"), "severity": "warn"}],
        }

    findings = report.get("findings", []) or []
    page_count = report.get("page_count", row.page_count or 0)
    scanned_ratio = float(report.get("scanned_page_ratio", 0.0) or 0.0)

    score = 100
    has_blocker = False
    issues = []
    for f in findings:
        sev = str(f.get("severity", "info"))
        fe_sev, penalty = _SEV.get(sev, ("ok", 2))
        score -= penalty
        # Only BLOCKER gates extraction — matches IntegrityReport.has_blockers and the
        # pipeline's gating semantics (ERROR/WARNING annotate but never halt the run).
        blocking = sev == "blocker"
        has_blocker = has_blocker or blocking
        pidx = f.get("page_index")
        title = _CHECK_TITLES.get(f.get("check_id", ""), str(f.get("check_id", "Issue")).replace("_", " ").title())
        issues.append({
            "title": L(title),
            "detail": f.get("message", ""),
            "pages": f"p.{pidx + 1}" if isinstance(pidx, int) else "All",
            "note": sev.upper(),
            "status": L("Blocking") if blocking else L("Advisory"),
            "severity": fe_sev,
        })
    score = max(0, min(100, score))

    if has_blocker:
        grade, summary = "Blocked", "Resolve the blocking issues before extracting."
    elif score >= 90:
        grade, summary = "Ready to extract", "No material issues detected."
    elif score >= 70:
        grade, summary = "Minor issues", "Proceed with caution — review the flagged items."
    else:
        grade, summary = "Needs attention", "Several issues detected — review before extracting."

    kind = "Native" if scanned_ratio == 0 else "Scanned" if scanned_ratio >= 0.99 else "Mixed"
    stats = [
        {"label": L("Pages"), "value": str(page_count), "sub": L("detected"), "tone": "neutral"},
        {"label": L("Document type"), "value": L(kind), "sub": L("native vs scanned"),
         "tone": "ok" if kind == "Native" else "warn"},
        {"label": L("Scanned pages"), "value": _pct_label(scanned_ratio),
         "sub": L("need OCR"), "tone": "warn" if scanned_ratio > 0 else "ok"},
        {"label": L("Issues"), "value": str(len(findings)), "sub": L("found"),
         "tone": "warn" if findings else "ok"},
    ]
    if not issues:
        issues = [{"title": L("No issues detected"), "detail": L("The document passed all integrity checks."),
                   "pages": "All", "note": "OK", "status": L("Passed"), "severity": "ok"}]
    return {"score": score, "grade": L(grade), "summary": L(summary), "stats": stats, "issues": issues}


@router.get("/{document_id}/integrity", dependencies=[Depends(authorized_document)])
def get_document_integrity(document_id: str, locale: str = Query("en"),
                           session: Session = Depends(db)) -> dict:
    """Real pre-flight integrity for one uploaded document (drives the Document Integrity
    screen when working a real file, rather than the demo project)."""
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize_document_integrity(row, locale)


def _latest_run(session: Session, document_id: str):
    """Most recent extraction run for a document, or None if it hasn't been extracted."""
    from app.db.models import ExtractionRun

    return session.execute(
        select(ExtractionRun)
        .where(ExtractionRun.document_id == document_id)
        .order_by(ExtractionRun.created_at.desc())
    ).scalars().first()


def _run_template_id(run) -> str | None:
    """Which template version a run was launched against — ONE spelling of the answer.

    The id is written in two places when the run is created (the ``template_version_id`` column
    and ``options["template_version_id"]``, routes/extractions.py), and either can be the only one
    populated: a run built straight from ``options`` (as several fixtures and callers do) leaves
    the column None. Answering the question in two places meant ``_coverage_block`` read the
    column while the check builders read the option, so one response served template-derived
    findings above a band stating no template was attached — the exact misread the band exists to
    prevent, inside a single payload.
    """
    return run.template_version_id or (run.options or {}).get("template_version_id")


def _prov_label(prov: dict | None) -> str:
    """The HUMAN-FACING source label a card prints. Page-level on purpose — "p.1" is what the
    reader wants to see. It is display text and nothing else: never put it in a judgement subject,
    because two printed lines on one page share it. Use ``_prov_anchor`` for identity.

    THE NUMBER IS THE ONE PRINTED ON THE PAGE, when the filing printed one. A PDF page has two
    numbers — its position in the file, and the folio the publisher put on it — and they are not the
    same: on one measured HK filing sheet 186 carries folio 184, on another the two coincide. The
    offset is a property of the document's front matter, not something to compute. A citation exists
    so a person can go and look, and the only number they can look up is the one on the paper, so
    that is the one this returns.

    THE SHEET POSITION IS THE FALLBACK, unlabelled and unchanged, because it is the best available
    answer and it is what this has always said: a spreadsheet has no folio, and neither does a page
    whose footer the classifier could not read (5 of 270 on the filing measured). Keeping the same
    "p.N" shape also keeps every existing reader — and the click-to-source e2e, which finds the chip
    by that shape — working.

    THE PAIR IS SHOWN AND LABELLED IN EXACTLY ONE PLACE, the viewer's own page badge, which sits on
    the image and is inherently about position. That badge printed "p.186 · 184" — both numbers,
    correct, and with nothing to say which was which. It is what a reader reported as "it is showing
    2 page numbers", and labelling it there is what makes a bare "p.184" here unambiguous everywhere
    else.
    """
    if not prov:
        return "—"
    if prov.get("source_kind") == "spreadsheet" and prov.get("sheet"):
        return f"{prov['sheet']}!{prov.get('cell', '')}"
    folio = prov.get("printed_page")
    if folio:
        return f"p.{folio}"
    return f"p.{(prov.get('page_index', 0) or 0) + 1}"


# Grid the normalized bbox is snapped to for the identity anchor: thousandths of the page.
#
# THE TWO FAILURE DIRECTIONS ARE NOT SYMMETRIC, so this number is chosen against the worse one.
# Too COARSE and two separately printed lines land on one anchor: they collide on one subject_key,
# and accepting one attributes a named reviewer's verdict to the other — a fabricated judgement,
# which is unacceptable. Too FINE and OCR/re-render jitter moves a line's box across a bucket
# boundary between runs: the anchor changes, the finding reads as new and RE-OPENS, and a reviewer
# is asked to look at something they already looked at — annoying, and safe.
#
# So: err fine. On an A4 page (~842pt tall) a thousandth is ~0.84pt vertically, while the shortest
# line a filing prints is ~7pt — eight buckets tall — so two printed lines cannot share a y
# bucket, and x0/x1 separate two sub-tables printed side by side on the same baseline. Jitter is
# normally a fraction of a point and lands in the same bucket; when it straddles a boundary the
# finding re-opens, which is the direction we chose.
#
# Quantized to an INT rather than kept as a float: the digest must not be hostage to float
# repr ("0.30000000000000004" vs "0.3" hash differently while naming one position).
_ANCHOR_GRID = 1000


def _snap(box, keys: tuple[str, ...]) -> str:
    """``keys`` of one normalized box, snapped to ``_ANCHOR_GRID``, or "" if the box is not one."""
    if not isinstance(box, dict) or not all(isinstance(box.get(k), (int, float))
                                            for k in ("x0", "y0", "x1", "y1")):
        return ""
    return "/".join(str(int(round(float(box[k]) * _ANCHOR_GRID))) for k in keys)


def _prov_anchor(prov: dict | None) -> str:
    """A PRECISE, content-derived source locator, for the judgement subject only.

    ``_prov_label`` returns "p.1" for every line on page 1, and a subject built on it makes two
    printed lines one identity — which is how accepting an unmapped "Others 1,234" came to stamp
    the accepting reviewer's name, time and reason onto a different unmapped "Others 5,678". The
    anchor is derived from the geometry the extractor recorded instead
    (core/models/geometry.py::Provenance), snapped to ``_ANCHOR_GRID``.

    IT ANCHORS ON THE ROW LABEL, NEVER ON THE FIGURE. A subject must be independent of the numbers
    the card prints: evidence changing means "stale, come look again", while a subject changing means
    "different finding", which the screen reports as the old one having been corrected or no longer
    raised. ``Provenance.bbox`` is the VALUE word's box (services/row_reconstruct.py), and a figure's
    box moves when the figure does — "Cash and cash equivalents" printed 1,204 gave
    p0#b798/101/840/118 and the same line printed 12,048 gave p0#b789/…, nine buckets left, because
    right-aligned numbers grow leftwards. So an acceptance on the two check types this whole layer
    was built around ORPHANED on a re-priced figure instead of going stale. ``label_bbox`` is the
    caption's own geometry: it does not move when the amount beside it does, and
    routes/extractions.py::_prov_dict carries it precisely so it can be used here.

    Three fallbacks, in order, each giving up discrimination rather than faking it:

    * a spreadsheet cell is already an exact address that no figure can move, so sheet + cell
      (or the label cell) is used verbatim;
    * a paginated source with no label geometry falls back to the value box's VERTICAL BAND alone —
      the printed line's y extent, which right-alignment does not touch — and deliberately drops x0
      and x1, which do move with the digit count. Two sub-tables printed on one baseline then share
      an anchor, and ``judgement.apply_judgements`` refuses to attribute a judgement to either. A
      refusal is honest; an anchor that silently follows a figure is not;
    * ``#noprov`` when the value carries no provenance at all, and ``#nobox`` for a paginated source
      with no geometry whatsoever (an adapter reporting a page and nothing else). Both are
      page-level, so two such lines on one page collide into that same refusal.
    """
    if not prov:
        return "#noprov"
    if prov.get("sheet"):
        return f"{prov['sheet']}!{prov.get('cell') or prov.get('label_cell') or ''}"
    page = int(prov.get("page_index") or 0)
    label = _snap(prov.get("label_bbox"), ("x0", "y0", "x1", "y1"))
    if label:
        return f"p{page}#l{label}"
    band = _snap(prov.get("bbox") or prov.get("value_bbox"), ("y0", "y1"))
    return f"p{page}#" + (f"y{band}" if band else "nobox")


def _row_ref(r: dict) -> str:
    """A stable handle on ONE extracted row, for an action that has to find it again.

    A run's rows carry no id, and the two handles already in the payload cannot serve: the check id
    embeds the row's INDEX (a render key that moves whenever extraction composition changes) and the
    canonical key is exactly what an unmapped row does not have. So the handle is the identity the
    judgement layer already established for a row — its normalised caption plus ``_prov_anchor``, the
    caption's own geometry — and nothing else. Deliberately NOT the subject key: that one folds in the
    finding's KIND and the concept it was mapped to, so re-mapping a row would change the key of the
    thing being re-mapped, and one row wearing two findings would have two handles.

    Value-independent by construction, which is the property that matters: the analyst reads a figure,
    picks a concept, and the row the server writes to must be the row they were looking at even if a
    concurrent edit changed the amount.
    """
    from app.services import judgement

    first = (r.get("values") or [{}])[0]
    return judgement.subject_key({"k": "row",
                                  "label": judgement.norm(r.get("source_label")),
                                  "anchor": _prov_anchor(first.get("provenance"))})


def _remap_targets(template_def: dict | None, locale: str = "en") -> list[dict]:
    """Every template line a printed row may be re-mapped ONTO, in template order.

    Two kinds of node are left out, and both would be a defect rather than a choice:

    * a CALCULATED line (one declaring a ``rollup``). Mapping a printed row onto a computed subtotal
      writes a figure the rollup then contradicts — the same reason ``_flip_sign_action`` refuses to
      flip one;
    * a section HEADER, which holds no figure at all.

    ``section`` is carried so the client can group the list. 180-odd concepts in one flat select is a
    list nobody can find anything in, and the section is the only grouping an analyst reads the
    statement by.
    """
    from app.services.rollups import calculated_nodes, node_labels

    labels = node_labels(template_def, locale)
    computed = set(calculated_nodes(template_def))
    out: list[dict] = []
    for stmt in (template_def or {}).get("statements", []):
        st = stmt.get("type", "")
        for sec in stmt.get("sections") or []:
            section = labels.get(sec.get("canonical_key") or "") or sec.get("label") or ""
            for node in sec.get("children") or []:
                key = node.get("canonical_key")
                if not key or key in computed or node.get("role") in ("header", "spacer"):
                    continue
                out.append({"canonical_key": key, "label": labels.get(key, key),
                            "statement": st, "section": section})
    return out


def _remap_offer(r: dict, locale: str = "en") -> dict:
    """What a row-shaped finding needs to offer "map this to a different line item".

    The candidate list is NOT in here — it is served once per payload as ``remap_targets``, because
    it is the same 180-odd concepts for every card. What is per card is the handle on the row, the
    caption the analyst is looking at, and where it is filed now.

    A row already re-mapped by hand says so, with who and when: the finding it answered is gone from
    the queue on the next fetch, so without this the only trace of a human decision would be the
    absence of a card.
    """
    prior = r.get("remap") or {}
    note = ""
    if prior:
        was = prior.get("from") or _t("unmapped", locale)
        note = _t("Re-mapped by hand", locale) + f" — {was} → {prior.get('to') or ''}"
        if prior.get("by"):
            note += f" ({prior['by']})"
    return {"row_ref": _row_ref(r),
            "label": r.get("source_label") or "",
            "current_key": r.get("canonical_key") or "",
            "remapped": prior or None,
            "remapped_note": note}


def _low_conf_threshold() -> float:
    """Mapping confidence below which a line routes to review — the same threshold the mapper
    uses to auto-accept, so the two never disagree."""
    from app.config import get_settings

    return get_settings().extraction.auto_accept_confidence


# DELETED with the low-confidence card: `_CONF_BAND` and `_confidence_evidence`.
#
# They quantized a mapping's score into 10-point bands so an acceptance survived jitter but not a
# collapse, and kept the METHOD exact because 'fuzzy' and 'llm' are different kinds of evidence for
# the same claim. Both were right, and both were about a card that no longer exists: a mapping's
# strength is not one of the three things this queue reports, so no acceptance is fingerprinted on
# it. The band-quantizing idea survives in `_prov_anchor` (geometry) and `judgement.q` (figures),
# which are the two places a re-run still wobbles. See the git history of this file for the reasoning
# and tests/test_review_judgement.py for the five tests that went with them.


def _row_value(rows: list[dict], key: str, basis: str = "consolidated", period: str = "current"):
    """One concept's figure — exactly the figure the statement grid shows for it.

    Read through ``concept_value`` so the accounting checks validate the number on screen: the
    sum when several printed lines map to the concept, or the analyst's manual value when one
    was entered. Checking a different number than the grid displays is worse than not checking.
    """
    return _concept_value([r for r in rows if r.get("canonical_key") == key], basis, period)


_BALANCE_KEYS = {
    "assets": "bs_total_assets",
    "assets_derived": ("bs_non_current_assets__total_non_current_assets",
                       "bs_current_assets__total_current_assets"),
    "eqliab": "bs_total_equity_and_liabilities",
    "eqliab_derived": ("bs_equity__total_equity",
                       "bs_non_current_liabilities__total_non_current_liabilities",
                       "bs_current_liabilities__total_current_liabilities"),
}


def _balance_sides(rows: list[dict], basis: str, period: str) -> tuple[float | None, float | None,
                                                                      bool]:
    """The two sides of the accounting identity, derived from subtotals when the filing does
    not print the totals themselves.

    Plenty of statements — HK/PRC ones especially — never print a "Total assets" line: they run
    non-current assets, current assets, then "Total assets less current liabilities". Requiring
    the printed total meant the identity check silently never ran on exactly those filings.
    Both sides are reconstructed from the section subtotals instead, which the template already
    defines, so the identity is genuinely checked. Returns (assets, equity+liabilities,
    whether either side was derived, the concepts the two figures were actually read from).

    That last element is what lets the queue say which extracted lines this finding NAMES without
    a second function guessing at the same key list from the outside.
    """
    def v(key: str):
        return _row_value(rows, key, basis, period)

    used: list[str] = []
    assets, derived = v(_BALANCE_KEYS["assets"]), False
    if assets is not None:
        used.append(_BALANCE_KEYS["assets"])
    else:
        nca, ca = (v(k) for k in _BALANCE_KEYS["assets_derived"])
        if nca is not None and ca is not None:
            assets, derived = nca + ca, True
            used += list(_BALANCE_KEYS["assets_derived"])

    eqliab = v(_BALANCE_KEYS["eqliab"])
    if eqliab is not None:
        used.append(_BALANCE_KEYS["eqliab"])
    else:
        eq, ncl, cl = (v(k) for k in _BALANCE_KEYS["eqliab_derived"])
        if eq is not None and ncl is not None and cl is not None:
            eqliab, derived = eq + ncl + cl, True
            used += list(_BALANCE_KEYS["eqliab_derived"])
    return assets, eqliab, derived, used


def _flip_sign_action(res: dict, rows: list[dict], template_def: dict | None,
                      locale: str) -> dict | None:
    """The one MECHANICAL fix this product offers: reverse the sign of the single figure the
    structural check already named as the culprit.

    Every condition below refuses the button rather than offering one that would do the wrong
    thing, because a button that lands on the wrong line is worse than prose telling the analyst
    to look:

    * the suspect is whichever concept ``_sign_suspect`` (structural_checks.py:510) named, and no
      other. That function already declines to name a candidate when two tie, on the grounds that
      a wrong pointer is worse than none; the judgement is reused here, never re-derived;
    * exactly ONE printed row may map to the concept. PATCH replaces a multi-row concept's SUM
      with the typed figure, so flipping a composed concept would destroy the composition — and
      which of the printed lines carries the wrong sign is precisely what a human must decide;
    * the slot must not already carry a typed figure. A machine flip must never overwrite an
      analyst's value, which also means the button cannot be clicked twice; a revert brings
      it back;
    * the suspect must not be a template-CALCULATED subtotal. Flipping one writes an override the
      rollup then honours, papering over the mis-signed component that is the actual defect.

    Without ``template_def`` that last exclusion cannot be tested at all, and a fix that cannot
    be checked is not offered.
    """
    from app.services.rollups import node_labels

    d = res.get("details") or {}
    suspect = d.get("sign_suspect")
    basis, period = d.get("basis"), d.get("period_label")
    if not template_def or not isinstance(suspect, str) or not suspect:
        return None
    if basis not in ("consolidated", "standalone") or not isinstance(period, str) or not period:
        return None
    group = [r for r in rows if r.get("canonical_key") == suspect]
    if len(group) != 1 or _edited_for(group[0], basis, period):
        return None
    current = _concept_value(group, basis, period)
    if not isinstance(current, (int, float)) or current == 0:
        return None
    if suspect in _calculated(rows, template_def, basis, period, locale):
        return None
    flipped = -float(current)
    # The edit is recorded with the RULE that prompted it and the concept it names, so
    # `edit_comments` says why the figure was changed rather than that it was.
    reason = _t("Sign flipped on the figure the structural check named as the one whose sign, "
                "reversed, would satisfy the relation", locale)
    rule_id = res.get("rule_id") or ""
    return {
        "kind": "flip_sign", "canonical_key": suspect, "basis": basis, "period": period,
        "label": node_labels(template_def, locale).get(suspect, suspect),
        "from": float(current), "to": flipped,
        # Formatted here, not in the browser: the card prints ':,.0f' everywhere and the client
        # formats no figure — which is also why judgement.q quantizes evidence to whole units.
        "from_display": f"{float(current):,.0f}", "to_display": f"{flipped:,.0f}",
        "comment": f"{reason} ({rule_id}: {suspect})." if rule_id else f"{reason} ({suspect}).",
    }


def _structural_inputs_edited(d: dict, rows: list[dict], locale: str) -> dict:
    """Whether a figure this relation uses has been typed over since the relation was evaluated.

    ``run.result["structural"]`` is written once by the pipeline (routes/extractions.py:320) and
    NOTHING recomputes it on an edit, so a structural card survives its own correction — including
    the flip-sign fix — until the next extraction. Rather than let the card look like a button
    that did nothing, it says so: after a successful flip ``fix_action`` becomes null (the slot is
    now edited) and this note explains why the finding is still on screen.
    """
    # A guard's `target`/`components` are the FIRST violation and the rest, so on a guard the set
    # that matters is `violations_keys` — every line the card lists. Naming only the first would
    # under-report an edit exactly the way the guard's evidence used to under-report a violation.
    keys = {d.get("target") or "", *(d.get("components") or []),
            *(d.get("violations_keys") or [])}
    basis, period = d.get("basis") or "consolidated", d.get("period_label")
    edited = sorted({r.get("canonical_key") for r in rows
                     if r.get("canonical_key") in keys and r.get("canonical_key")
                     and _edited_for(r, basis, period)})
    note = _t("A figure this relation uses has been edited since it was evaluated. The relation "
              "is re-evaluated on the next extraction.", locale) if edited else ""
    return {"inputs_edited": bool(edited), "inputs_edited_keys": edited,
            "inputs_edited_note": note}


def _guard_violation_label(v: dict) -> str:
    """The lines one guard violation names, as the card's row label.

    A violation dict is shaped by its predicate (``_guard_slot``): a signed key, an aggregate plus
    the components loaded beside it, or a pair asserted equal. All of them are canonical keys, so
    the label is locale-free by construction — the same reason a component list is keyed on
    canonical_key rather than its localized label.
    """
    names = [str(x) for x in (v.get("key"), v.get("aggregate"), v.get("non_zero")) if x]
    names += [str(x) for x in (v.get("equal") or [])]
    names += [str(x) for x in (v.get("components") or [])]
    label = " · ".join(dict.fromkeys(names))
    return f"{label} ({v['expected']})" if v.get("expected") else label


# The figure fields a violation can carry, in the order they are printed. Named explicitly rather
# than "every numeric-looking value", so a new field in `_guard_slot` shows up as a missing figure
# on the card (fix it here) instead of silently joining a fingerprint nobody displayed.
_GUARD_FIGURE_FIELDS = ("value", "aggregate_value", "non_zero_value")


def _guard_violations(d: dict) -> dict[str, str]:
    """A guard's violation set as ``{lines: figures}`` — what the card prints and what the
    evidence digest is taken over, so the two cannot disagree."""
    from app.services import judgement

    out: dict[str, str] = {}
    for v in d.get("violations") or []:
        figures = [f"{judgement.q(v[f]):,.0f}" for f in _GUARD_FIGURE_FIELDS
                   if v.get(f) is not None]
        out[_guard_violation_label(v)] = " / ".join(figures) if figures else "—"
    return out


def _guard_check(res: dict, d: dict, locale: str) -> dict:
    """A failed rulebook GUARD as a review item, fingerprinted on its VIOLATION SET.

    A guard leaves ``RuleResult.expected``/``actual``/``difference`` at their None defaults
    (core/models/reports.py:38-40) and puts everything it asserts in ``details.violations`` /
    ``violations_keys``. Built like an arithmetic relation, its card therefore printed "Reported 0
    · Sum of template components 0 · Difference 0" — three numbers derived from nothing — and
    fingerprinted exactly those constants. An acceptance on a guard could then NEVER go stale: a
    mapping regression that took the same BLOCKING guard from one violated key to nine left the
    subject unchanged and the evidence byte-identical, so nine violations came back as 'accepted'
    by a person who examined one, dropped out of ``summary.open``, out of the red counter and out
    of the commentary's data-quality count. The stale mechanism was structurally unreachable for
    every guard kind — sign_expectation, consolidation_eliminated, mutually_exclusive,
    equal_values, equal_while_third_non_zero.

    So the card shows the violation set and the digest is taken over the same set: it moves when a
    violation is added, removed, or its figure changes. ``delta`` is "—" rather than a computed
    zero, because a guard has no difference to report.

    THE SUBJECT IS THE GUARD, AND NOTHING THE VIOLATIONS OR THE RULEBOOK'S ORDER DECIDE. It used to
    be ``{rule_id, scope, target}``, and both halves of that were wrong:

    * ``rule_id`` is ``guard:{predicate}:{keys[0]}`` (structural_checks.py), so two rulebook
      sentences sharing a predicate and a first key were ONE identity — verified against the real
      loader with two ``equal to`` sentences both starting bs_equity__non_controlling_interests.
      Run 1 fails sentence A and a reviewer accepts; run 2 sentence A passes and sentence B fails,
      and B was served 'stale' carrying A's reviewer, A's reason and A's figures on a BLOCKING
      finding nobody had examined. ``structural_checks._unique`` then made the id unique by
      appending the sentence's 1-based ORDINAL among those sharing the base id — and an ordinal is a
      fact about POSITION, about neither the guard nor its figures. Deleting an unrelated sentence A
      renumbered a byte-identical, still-failing sentence B from ``…#2`` to ``…#1``, which moved
      subject_key and orphaned the acceptance under "corrected, or no longer raised". So the id is
      not in the identity at all: the sentence itself and the operands it names are, and those
      cannot be moved by another sentence being edited, added or removed. The id stays on the card
      (``id``/``where``) for the SCREEN's DOM key and coverage's per-rule alarms, which have only
      the id — a stored judgement is not pinned to it;
    * ``target`` is ``violations[0]["key"]`` for sign_expectation — DERIVED FROM THE FIGURES. One
      more violated key changed the SUBJECT, so the acceptance detached and the screen reported the
      finding as corrected or no longer raised while the blocking guard was failing on MORE lines
      than when it was accepted. A subject must move only when the claim changes; a figure moving
      belongs in the evidence, where it reads as 'stale' — come look again.

    ``rule`` is whitespace/case-collapsed (``judgement.norm``): re-wrapping a rulebook sentence is
    not a different assertion, while re-writing it is — and that legitimately withdraws an
    acceptance made against what the sentence used to say.
    """
    from app.services import judgement

    def L(s: str) -> str:
        return _t(s, locale)

    violations = _guard_violations(d)
    keys = ", ".join(sorted(d.get("violations_keys") or []))
    asserts = [str(k) for k in (d.get("guard_keys") or [])]
    calc = [[L("Rule"), str(d.get("rule_text") or d.get("guard") or ""), False],
            [L("Lines in violation"), keys or "—", True],
            [L("Violations"), str(len(violations)), False]]
    # The operands the SENTENCE names, printed because they are part of the identity a reviewer's
    # acceptance is pinned to — and because they are what distinguishes this guard from another one
    # the rulebook may state under the same predicate. sign_expectation names none: it scans every
    # concept with a declared sign convention, and the row would be an empty assertion.
    if asserts:
        calc.append([L("Concepts the rule names"), " · ".join(asserts), False])
    calc += [[label, figures, False] for label, figures in violations.items()]
    return {
        "id": f"chk-guard-{res.get('rule_id')}-{res.get('scope_key')}",
        "type": "structural", "icon": "≠",
        "title": L("Rulebook guard failed"),
        "where": f"{res.get('rule_id')} · {res.get('scope_key')}",
        "severity": L("Check failed"), "tone": "high",
        # No difference exists for a guard: it asserts a condition, not an equality.
        "delta": "—", "target": d.get("target") or "",
        # Every line in the violation set — the card lists them all, so a finding stands against
        # each. `target` is only the first of them.
        "names": sorted(str(k) for k in (d.get("violations_keys") or []) if k),
        "calc": calc,
        "fix": L("The rulebook declares this must hold, and it does not for the lines listed. "
                 "Check each one against the document; the guard is re-evaluated on the next "
                 "extraction."),
        # A guard is not an arithmetic relation, so it does not share the relation's subject kind:
        # the two carry different fields, and one `k` over two shapes is how `target` came to mean
        # "the declared total" on one card and "whichever key happened to break first" on the other.
        "subject": {"k": "guard",
                    "scope": str(res.get("scope_key") or ""),
                    "predicate": str(d.get("guard") or d.get("op") or ""),
                    "asserts": asserts,
                    "rule": judgement.norm(d.get("rule_text") or "")},
        "evidence": {
            # The violation set, and its size beside it so a set that grew still moves the digest
            # even if two violations were to render under one label.
            "violations": violations,
            "violation_count": len(violations),
            "violations_keys": keys,
        },
        # A guard names no single suspect figure (`sign_suspect` is None by construction), so there
        # is no mechanical fix to offer — and a button that cannot land on one line is not offered.
        "fix_action": None,
    }


# ONE assertion a served card makes: this target, in this scope, is out by this much. Every finding
# that reports a difference is suppressed on ASSERTIONS and never on bare targets, because "a card
# mentions this target" and "a card already tells the reader about this difference" are different facts,
# and only the second one makes a second card a duplicate. There used to be a weaker question beside
# it — "does this template line already have a card here at all" — for the one finding that reported no
# difference to compare (`uncomputed`); that finding is no longer raised, and the weaker test went with
# it, so an assertion is now the ONLY grounds on which a card is dropped.
#
# `covered` used to be a set of bare target strings, and that cost two blocking findings on the
# shipped rulebook. An equity-closing card whose target is bs_equity__total_equity — asserting a
# 1,000 break between the equity statement's closing row and the balance sheet — suppressed
# rollup:bs_equity__total_equity and section_reconciliation:bs_s5_equity, which assert a 2,500 break
# between total equity and its own components. Same target, a different statement about it, and the
# 2,500 was then reported NOWHERE while `failed_reported_elsewhere` counted it as raised above. The
# key also carried no scope, so the consolidated balance card — hardcoded to consolidated/current —
# deleted a 900 break in the STANDALONE column it makes no claim about at all.
_Assertion = tuple[str, str, str, int]

# Where each card kind keeps the difference it prints — EVERY kind that prints one, because this map
# is what a card declares about itself, read both to build the set of what is already on screen and to
# ask whether a new card would restate it.
#
# A kind absent from this map declares NO assertion and therefore suppresses nothing, which is the
# safe default: it over-reports, and it cannot lose a finding. The row-shaped kind (`unmapped`) is
# absent because it names a CAPTION rather than a template line and prints no difference; a guard is
# absent for a stronger reason still — see `_assertion_of`. A card type added later without a key here
# is over-reported until someone adds one, which is the failure direction to prefer.
# EVERY CARD KIND THIS QUEUE MAY SERVE, in its two shapes — the product rule, written down.
#
# The queue carries exactly three things: an extracted figure from the FACE of a statement that
# reaches no line of the output; a validation rule that failed; and a section subtotal that does not
# match the values under it. The first is row-shaped (one printed line, one caption, a re-map offer);
# the second and third are accounting findings (a relation between concepts, no single edit to offer).
#
# Declared here rather than left implicit in the builders' literals, because THREE readers have to
# agree about it and two of them are not in this module: the chip set below, whose `types` partition
# the served list, and the seeded sample (api/routes/projects.py::_DEMO_TAB_LABELS), which is the
# first thing a new user sees. The sample used to speak its own vocabulary — `subtotal`, `sign`,
# `note` — and so advertised a "Note reconciliation" category the real route does not raise while
# showing no card at all for the category that matters most. `_assert_known_kinds` holds every card
# to this, so a kind added without a chip fails loudly here instead of going invisible on screen.
#
# Retired, and not to be re-added without the product decision that put them here: `low_confidence`
# (a mapping's strength is a property of the mapping, not a finding — counted as `weak_mappings`),
# `off_template` (folded into `unmapped`: one category, two ways in), `uncomputed` (a subtotal with no
# components is a coverage fact, reported in the coverage band and the line's own note) and
# `note_tie`.
_ACCOUNTING_TYPES = frozenset({"balance", "equity_tie", "structural", "calculated_mismatch",
                               "containment_gap"})
_ROW_SHAPED_TYPES = frozenset({"unmapped"})


def _assert_known_kinds(checks: list[dict]) -> None:
    """Every served card is a kind the queue declares. Raises rather than serving an unlisted one.

    A new kind reaches the screen through the chips, and the chips are built from the declaration
    above — so an unlisted kind is a card no chip counts: invisible under every filter, and counted
    in the "All" total that then disagrees with the sum of the others. That is the
    counts-disagree-with-content defect this file has been bitten by repeatedly, and it is cheaper to
    fail here than to find it on a screen.
    """
    unknown = sorted({str(c.get("type")) for c in checks} - _ACCOUNTING_TYPES - _ROW_SHAPED_TYPES)
    if unknown:
        raise AssertionError(
            f"review card kinds not declared in _ACCOUNTING_TYPES/_ROW_SHAPED_TYPES: {unknown}. "
            "Add the kind to the right set AND give it a chip, or the card is invisible on screen.")


_ASSERTED_DIFF_KEY = {
    "balance": "diff",
    "equity_tie": "diff",
    "structural": "diff",
    "calculated_mismatch": "diff",
    # A containment gap declares one too — this target, in this column, is out by this much — and it
    # is registered because the calculated path is tested against the cards above it. Registering it
    # is what makes that true; absent from here it would assert nothing and the comment at its call
    # site would be describing something that does not happen.
    "containment_gap": "diff",
}


_CHILDREN_FLAG = "contains_mapped_children:"
_GAP_FLAG = "containment_unexplained:"


def _demoted_parent(row: dict) -> list[str] | None:
    """The concepts a DEMOTED GROSS PARENT was replaced by, or None if this row is not one.

    A filing that prints "Cash and cash equivalents" and, under it, the restricted and pledged
    balances that are part of it, has printed the parent GROSS. Filing both double-counts, so
    ``map_ontology._enforce_containment`` un-files the parent — clears its ``canonical_key`` and
    demotes it to a subtotal — and records what replaced it in this flag.

    THE FLAG IS THE POSITIVE SIGNAL, and a cleared key is not: a row with no key is the shape of a
    mapping failure AND the shape of a deliberate demotion, and only the flag tells them apart.
    Read here so that both readers below ask one question — the queue, which must not report a
    demotion as "nothing recognised this caption", and the containment card, which reports what
    actually went wrong when something did.
    """
    flags = row.get("flags") or []
    if row.get("canonical_key"):
        return None
    kids = next((f for f in flags if f.startswith(_CHILDREN_FLAG)), None)
    if kids is None:
        return None
    return [k for k in kids[len(_CHILDREN_FLAG):].split(",") if k]


def _containment_checks(rows: list[dict], locale: str, template_def: dict | None) -> list[dict]:
    """A printed aggregate the components extracted under it do not account for.

    THE THIRD CATEGORY, on the one shape of it that is not a template rollup. A gross parent whose
    children are also on the face is un-filed to stop the section counting the money twice
    (``map_ontology._enforce_containment``) — and where the children do not add up to the parent,
    part of the printed figure now appears on NO line at all. The stage's own comment says why that
    has to be reported: "without that, unfiling silently removes the unexplained part of the figure
    from the statement and every remaining check ties."

    IT USED TO BE REPORTED BY ACCIDENT, and that is what this replaces. The stage appended
    ``low_mapping_confidence`` to route the row to review, so the finding arrived as a
    low-confidence card — a card about a mapping's strength, describing a row whose mapping was an
    EXACT match at 1.0. When that card went, this row still reached the queue, now as ``unmapped``,
    printing "— (no confident match)" about a caption the mapper had recognised perfectly. Both
    labels were wrong about the same real defect. This says what happened.

    A demotion whose children DO account for the parent raises nothing: the money is all on the
    face, through the children, which is the outcome the containment pass exists to produce.

    THE ARITHMETIC IS RECOMPUTED FROM THE SERVED ROWS, not read off the stage's flag (which carries
    only a column count). The card then prints the same figures the grid does — the rule this module
    keeps everywhere: checking a different number than the reader is shown is worse than not
    checking.
    """
    def L(s: str) -> str:
        return _t(s, locale)

    from app.services import judgement
    from app.services.rollups import node_labels

    names = node_labels(template_def, locale) if template_def else {}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("canonical_key")
        if k:
            groups.setdefault(k, []).append(r)

    out: list[dict] = []
    for i, r in enumerate(rows):
        kids = _demoted_parent(r)
        if not kids:
            continue
        # THE ARITHMETIC BELOW DECIDES, NOT THE STAGE'S FLAG. The stage writes
        # `containment_unexplained` when the components did not account for the parent AT EXTRACTION
        # TIME, and this queue is rebuilt from the current figures on every fetch: an analyst edit to
        # a child can close a gap the stage found, or open one it did not. Gating on the flag would
        # keep serving the first and never serve the second — a real break, on no card, after an edit
        # the analyst made in this very screen. So the flag is read for the NAME only.
        gap = next((f for f in (r.get("flags") or []) if f.startswith(_GAP_FLAG)), None)
        # The concept this row WAS before it was un-filed. Only the flag remembers — the key was
        # cleared — so with no flag the card is identified by the caption instead, exactly as the
        # row-shaped card identifies a row with no concept. `target` is a caption there too.
        aggregate = (gap[len(_GAP_FLAG):].rsplit(":", 1)[0] if gap
                     else (r.get("source_label") or ""))
        if not aggregate:
            continue                # nothing to name it by at all: no card rather than a blank one
        label = r.get("source_label") or names.get(aggregate, aggregate)
        # BOTH COLUMNS, like the relation checks beside it (9 current + 9 prior on the filing this
        # was measured against) and unlike the calculated path, which is current-only. A break in the
        # prior column is a real break — the figures are extracted, served and exported — and this
        # card's arithmetic is available per column, so there is nothing to scope away. Do not narrow
        # it to current for symmetry with `_calculated_checks`; that path's limit is its own.
        for basis in ("consolidated", "standalone"):
            for period in ("current", "prior"):
                # A group of ONE, deliberately: the same reader the components go through and the
                # same one the grid uses, so the figures on the card are the figures on screen.
                printed = _concept_value([r], basis, period)
                if printed is None:
                    continue
                parts = [(k, _concept_value(groups.get(k, []), basis, period)) for k in kids]
                got = [(k, v) for k, v in parts if v is not None]
                if not got:
                    continue        # nothing to compare in this column, not a disagreement
                total = sum(v for _k, v in got)
                diff = printed - total
                if abs(diff) <= _CALC_TOLERANCE:
                    continue
                out.append({
                    "id": f"chk-containment-{i}-{basis}-{period}",
                    "type": "containment_gap", "icon": "⊃",
                    "title": L("Printed total does not match the lines that replaced it"),
                    "where": f"{label} · {basis}/{period}",
                    "severity": L("Check failed"), "tone": "high",
                    "delta": f"{diff:,.0f}", "target": aggregate,
                    # The parent is NOT named: it carries no key, so no grid line is it. The children
                    # are, because they are the lines an analyst checks against the page.
                    "names": [k for k, _v in got],
                    "calc": [[L("Printed in the document"), f"{printed:,.0f}", False],
                             [L("Sum of the lines that replaced it"), f"{total:,.0f}", True],
                             [L("Not on any line"), f"{diff:,.0f}", False],
                             *[[names.get(k, k), f"{v:,.0f}", False] for k, v in got]],
                    "fix": L("This caption was printed as a combined total and its components are "
                             "on the face as well, so the combined line was removed to stop the "
                             "section counting the money twice. The components do not add up to "
                             "it, so the difference is on no line: a component is missing from the "
                             "extraction, or this caption is not their total. Check the page."),
                    "subject": {"k": "containment_gap", "key": aggregate, "basis": basis,
                                "period": period},
                    "evidence": {"printed": judgement.q(printed),
                                 "components_total": judgement.q(total),
                                 "diff": judgement.q(diff),
                                 "components": {k: judgement.q(v) for k, v in got}},
                })
    return out


def _card_scope(check: dict) -> tuple[str, str] | None:
    """The COLUMN a served card makes its claim about, as (basis, period) — or None when it names
    none.

    TWO VOCABULARIES, ONE QUESTION, ONE READER. The accounting cards declare the pair outright as
    ``subject.basis``/``subject.period``; a relation card carries the evaluator's ``scope_key``, which
    is that same pair spelled as one string (services/structural_checks.py::_scope). Both suppression
    questions ask this, so it is answered here once: the suppression key that carried NO scope is how
    a consolidated card came to delete a standalone-column break, and a second reader inventing a
    third spelling of the pair is how that would come back.

    None for the row-shaped findings (unmapped, off-template, low-confidence). Those are about one
    printed line rather than a column, and their ``target`` is that line's CAPTION rather than a
    template key — so they own no template line in any column and may silence nothing. Stated rather
    than left to the fact that a caption does not usually collide with a canonical key.
    """
    subject = check.get("subject") or {}
    basis, period = subject.get("basis"), subject.get("period")
    if basis and period:
        return str(basis), str(period)
    # `scope_key` is "{basis}/{period}", with "—" for a period the slot could not name. Partitioned
    # rather than split so a malformed value yields None and keeps the card it would have silenced.
    basis, _, period = str(subject.get("scope") or "").partition("/")
    return (basis, period) if basis and period else None


def _assertion_of(check: dict) -> _Assertion | None:
    """What this card tells the reader, or None when it does not say all of it.

    None is not a failure — it means this card may not stand in for anything else. Every part has
    to be present and comparable: a card that cannot name its scope cannot be shown to be about the
    same column as the relation it would silence.
    """
    subject = check.get("subject") or {}
    # A guard asserts a CONDITION rather than an equality, so it is not the duplicate of any
    # arithmetic relation — and its own `target` is derived from the violations for several
    # predicates, which must never decide whether another card exists.
    if subject.get("k") == "guard":
        return None
    diff_key = _ASSERTED_DIFF_KEY.get(str(subject.get("k") or ""))
    target, scope = check.get("target"), _card_scope(check)
    if not (diff_key and target and scope):
        return None
    diff = (check.get("evidence") or {}).get(diff_key)
    if diff is None:
        return None
    # Magnitude, not sign: a rollup computes target − sum where the balance identity computes
    # assets − (equity + liabilities), so one break legitimately reaches the two cards with
    # opposite signs. Matching on magnitude suppresses that true duplicate; it cannot merge two
    # DIFFERENT differences, which is the failure this function exists to prevent.
    return (str(target), *scope, abs(int(diff)))


def _reported_assertions(checks: list[dict]) -> set[_Assertion]:
    """Everything the cards above already tell the reader — the only grounds for dropping a card.

    ``checks`` is the cards ALREADY SERVED, and never a list containing the card being tested against
    it: every card asserts what it asserts, so a list including it answers "yes, that is reported" for
    the card itself and drops it. Both callers pass the queue as it stands before the batch they are
    about to build.
    """
    return {a for a in (_assertion_of(c) for c in checks) if a is not None}


def _relation_reported_elsewhere(res: dict, reported: set[_Assertion]) -> bool:
    """True when this failed ARITHMETIC relation's difference is already raised by a card above it.

    ONE spelling of the suppression, read by the emitter (``_structural_checks``) and by the count
    the coverage band prints beside its failed bucket (``failed_reported_elsewhere``). The two used
    to be two expressions of one decision, and they disagreed about guards: the emitter dropped a
    guard whose figure-derived ``details.target`` collided with a target the balance card owned,
    while the count reported that guard as "reported elsewhere" when NOTHING reported it.

    A GUARD IS NEVER SUPPRESSED — it asserts a condition rather than an equality, so it is not a
    duplicate of any arithmetic card.

    Suppression requires the SAME difference about the SAME target in the SAME column, and anything
    short of that shows the card. That default is the point: a duplicate card is noise a reader can
    see past, while a dropped one is a blocking finding reported nowhere — and if this relation's
    scope is spelled in a vocabulary the cards do not use, "no match" is the answer that keeps it on
    screen. ``period_label`` is the slot name (services/periods.py CURRENT/PRIOR), the same
    vocabulary the card subjects carry, so the comparison is between like and like today.
    """
    d = res.get("details") or {}
    if res.get("status") != "fail":
        return False
    if res.get("kind") == "guard" or d.get("guard"):
        return False
    target, basis, period = d.get("target"), d.get("basis"), d.get("period_label")
    difference = res.get("difference")
    if not (target and basis and period) or difference is None:
        return False
    try:
        magnitude = abs(int(round(float(difference))))
    except (TypeError, ValueError):
        return False
    return (str(target), str(basis), str(period), magnitude) in reported


def _structural_checks(structural: list[dict], locale: str, covered: set[_Assertion],
                       rows: list[dict] | None = None,
                       template_def: dict | None = None) -> list[dict]:
    """Failed template-structure relations as review items (from the structural stage).

    Only ``fail`` rows become checks: a ``skipped`` row means the relation could not be
    evaluated because a participant was never extracted, which is a coverage fact, not a
    defect. A relation is left out only when a card above ALREADY REPORTS THE SAME DIFFERENCE
    about the same target in the same column, so the analyst does not see one break twice —
    never merely because some card mentions the same target, which silenced two blocking
    breaks and a whole standalone column (see ``_relation_reported_elsewhere``).

    The fix action and the edited-inputs note are derived HERE rather than by the caller because
    the relation's ``details`` dict is what both need: carrying it out on the payload just so a
    later pass could re-read it would be the same data in two places.

    A GUARD is not an arithmetic relation and gets its own card — see ``_guard_check`` — and it is
    emitted whatever is in ``covered``: see ``_relation_reported_elsewhere``.
    """
    from app.services import judgement

    def L(s: str) -> str:
        return _t(s, locale)

    out: list[dict] = []
    for res in structural:
        d = res.get("details") or {}
        if res.get("status") != "fail":
            continue
        # THE GUARD BRANCH COMES FIRST, AND NO SUPPRESSION TEST RUNS BEFORE IT. `covered` used to be
        # tested one line above this branch, and for sign_expectation `details.target` is
        # `violations[0]["key"]` (services/structural_checks.py::_guard_slot) — derived from the
        # FIGURES. So a run that mis-signed one more line could move the guard's target onto
        # bs_total_assets, which the balance card owns, and the whole guard card vanished from the
        # queue: the reviewer's acceptance was then reported as orphaned under "corrected, or no
        # longer raised" while the rulebook rule was failing on two lines and nothing anywhere showed
        # it. Whether a card EXISTS may not be decided by a figure-derived field.
        if res.get("kind") == "guard" or d.get("guard"):
            out.append({**_guard_check(res, d, locale),
                        **_structural_inputs_edited(d, rows or [], locale)})
            continue
        if _relation_reported_elsewhere(res, covered):
            continue
        expected, actual = float(res.get("expected") or 0), float(res.get("actual") or 0)
        suspect = d.get("sign_suspect")
        calc = [[L("Reported"), f"{actual:,.0f}", False],
                [L("Sum of template components"), f"{expected:,.0f}", True],
                [L("Difference"), f"{actual - expected:,.0f}", False]]
        calc += [[k, f"{float(v):,.0f}", False]
                 for k, v in (d.get("component_values") or {}).items()]
        fix = L("The reported figure does not equal the components the template says it is made "
                "of, so a value is on the wrong line. Check the components and the total "
                "against the document.")
        if suspect:
            fix = f"{fix} {L('Flipping the sign of')} {suspect} {L('would make it balance.')}"
        out.append({
            "id": f"chk-structural-{res.get('rule_id')}-{res.get('scope_key')}",
            "type": "structural", "icon": "≠",
            "title": L("Figure does not equal its template components"),
            "where": f"{d.get('target')} · {res.get('scope_key')}",
            "severity": L("Check failed"), "tone": "high",
            "delta": f"{actual - expected:,.0f}", "target": d.get("target") or "",
            # The total AND its components: the card prints every component's figure and tells the
            # analyst to check them, so each of those lines is named by this finding.
            "names": [k for k in [d.get("target") or "", *(d.get("components") or [])] if k],
            "calc": calc, "fix": fix,
            # WHAT THE RELATION ASSERTS, and ONLY that: the target, the operator and the components,
            # in declared order (order carries the signs for a `diff`). An authored identity id is
            # free text, so two rulebook entries can share one; with only {rule_id, scope, target} in
            # the subject, run 1 failing entry A and run 2 failing entry B served B as 'stale' under
            # A's reviewer and A's reason. Every field here is DECLARED by the template or the
            # rulebook and moves only when the rule is re-authored — which is exactly when an
            # acceptance made against what the rule used to assert should detach.
            #
            # `rule_id` is NOT here, for the same reason it left the guard subject (see
            # `_guard_check`): `structural_checks._unique` disambiguates a repeated authored id by
            # appending the entry's 1-based ORDINAL, which is a fact about POSITION in the rulebook.
            # Deleting an unrelated entry that shared the id renumbers a byte-identical, still-failing
            # relation from `dup#2` to `dup`, and a positional identity would then report that
            # acceptance as "corrected, or no longer raised". The id stays on the card's `id` and
            # `where`, which is what the screen and coverage's per-rule alarms key on.
            # Nothing derived from the figures may go in either: see `_guard_check`.
            "subject": {"k": "structural",
                        "scope": str(res.get("scope_key") or ""),
                        "target": str(d.get("target") or ""),
                        "op": str(d.get("op") or ""),
                        "components": [str(c) for c in (d.get("components") or [])]},
            "evidence": {
                "actual": judgement.q(actual), "expected": judgement.q(expected),
                "diff": judgement.q(actual - expected),
                "components": {k: judgement.q(float(v))
                               for k, v in (d.get("component_values") or {}).items()},
                "sign_suspect": suspect or None,
            },
            "fix_action": _flip_sign_action(res, rows or [], template_def, locale),
            **_structural_inputs_edited(d, rows or [], locale),
        })
    return out


def _accounting_checks(rows: list[dict], reconciliation: list[dict], locale: str,
                       structural: list[dict] | None = None,
                       template_def: dict | None = None,
                       stats: dict | None = None) -> list[dict]:
    """Failed accounting validations for the review queue (Req 11): the balance-sheet
    identity, note→face ties, the template's structural relations, and — since the face now
    carries the COMPUTED figure for every calculated line — what the document printed instead.
    Computed from the real extracted values.

    Each check also carries a locale-free ``subject`` (WHAT is being asserted) and ``evidence``
    (the figures asserted about it), because only the builder knows the semantics. They are what a
    human judgement is keyed on — see services/judgement.py for why that is not the check id.

    ``stats`` is an out-parameter for one quantity the caller cannot recompute without repeating
    this function's work: how many failed relations were suppressed because their target already
    has its own check. The coverage panel needs it, and deriving it from a second pass over the
    same rows is exactly the two-places-computing-one-count bug.

    Every check also carries ``names``: the canonical keys of the extracted lines the card actually
    indicts. Only the builder knows them — the balance identity names both sides (the section
    subtotals when a side was reconstructed from them), a guard names every line in its violation
    set, a note tie names every face line that did not tie — and the review header's third tile
    counts the lines NOT in any of them, so a second pass guessing at this list from the outside is
    the two-places-computing-one-count bug again.
    """
    from app.services import judgement

    def L(s: str) -> str:
        return _t(s, locale)

    checks: list[dict] = []
    a, e, derived, sides = _balance_sides(rows, "consolidated", "current")
    if a is not None and e is not None and abs(a - e) > 1:
        checks.append({
            "id": "chk-balance", "type": "balance", "icon": "≠",
            "title": L("Balance sheet does not balance"), "where": L("Balance sheet identity"),
            "severity": L("Check failed"), "tone": "high", "delta": f"{a - e:,.0f}",
            "target": "bs_total_assets",
            # BOTH sides, and the subtotals a reconstructed side was read from: the card prints
            # each of those figures, so each of those lines has a finding against it.
            "names": sides,
            "calc": [
                [L("Total assets"), f"{a:,.0f}", False],
                [L("Total equity and liabilities"), f"{e:,.0f}", True],
                [L("Difference"), f"{a - e:,.0f}", False],
            ] + ([[L("Totals derived from the section subtotals"), "", False]] if derived else []),
            "fix": L("Assets do not equal equity plus liabilities. Check the extracted totals "
                     "and their components against the document."),
            # basis/period are recorded explicitly even though this check is hardcoded to the
            # consolidated current column today: if it ever runs per basis, an existing acceptance
            # stays pinned to the pair it was made on instead of silently widening to cover both.
            "subject": {"k": "balance", "basis": "consolidated", "period": "current"},
            # `derived` is on the card ("Totals derived from the section subtotals"), so it is part
            # of what the human judged — the same difference reached from printed totals is a
            # different claim from one reached from reconstructed ones.
            "evidence": {"assets": judgement.q(a), "eqliab": judgement.q(e),
                         "diff": judgement.q(a - e), "derived": bool(derived)},
        })
    # The statement of changes in equity must END where the balance sheet says equity stands.
    # It is the one relation that crosses two statements, and it is worth checking precisely
    # because the two are extracted by completely different paths — a matrix reader and a
    # two-column reader — so agreement is real evidence rather than a restatement.
    eq_close, bs_equity = _equity_closing(rows, "consolidated"), _row_value(
        rows, "bs_equity__total_equity")
    if eq_close is not None and bs_equity is not None and abs(eq_close[1] - bs_equity) > 1:
        checks.append({
            "id": "chk-equity-closing", "type": "equity_tie", "icon": "≠",
            "title": L("Equity statement does not close at the balance sheet's equity"),
            "where": L("Statement of changes in equity"),
            "severity": L("Check failed"), "tone": "high",
            "delta": f"{eq_close[1] - bs_equity:,.0f}", "target": "bs_equity__total_equity",
            # BOTH lines: the balance sheet's equity total AND the equity statement's closing row.
            # The comment here used to say the closing row "is a matrix row, not one of `rows`",
            # which was false — `_equity_closing` iterates `_matrix_rows(rows, basis)`, which FILTERS
            # `rows`, so the row it returns IS one of them. The card prints that row's caption and its
            # figure and tells the analyst to check it, and the header tile was counting it as "a line
            # with no finding" while this card indicted it. It is named by canonical_key when it has
            # one and by its printed caption otherwise, which is the only handle an unmapped matrix
            # row has (see `_build_review`, which matches rows on either).
            "names": sorted({"bs_equity__total_equity", eq_close[2]} - {""}),
            "calc": [
                [eq_close[0], f"{eq_close[1]:,.0f}", False],
                [L("Total equity per the balance sheet"), f"{bs_equity:,.0f}", True],
                [L("Difference"), f"{eq_close[1] - bs_equity:,.0f}", False],
            ],
            "fix": L("The closing balance of the equity statement should equal total equity on "
                     "the balance sheet. Check both figures against the document."),
            "subject": {"k": "equity_tie", "basis": "consolidated", "period": "current"},
            # The CAPTION of the row taken as the closing balance is on the card (it is the first
            # calc row's label), so it is fingerprinted: a re-run that picks a DIFFERENT closing row
            # carrying the same figure is a different claim, and it used to keep the acceptance
            # silently. Normalized, because re-parsing a page legitimately shifts a caption's
            # spacing and that is not a different row. It is evidence and not subject: which row
            # closes the statement is a figure-level fact this check discovers, not the thing being
            # asserted, so a change means "come look again" rather than "different finding".
            "evidence": {"closing_label": judgement.norm(eq_close[0]),
                         "closing": judgement.q(eq_close[1]),
                         "bs_equity": judgement.q(bs_equity),
                         "diff": judgement.q(eq_close[1] - bs_equity)},
        })
    # NOTE-TIE CARDS ARE NOT RAISED. A note that does not sum to the face figure it supports is
    # still reconciled and still lowers note_link confidence (stages/reconcile), and the
    # ReconciliationReport still records every untied face line — what is gone is the review-queue
    # CARD. On a real filing the queue filled with them: a cited note is very often an analysis,
    # segment or commitments table rather than a decomposition, and each one that failed to tie
    # arrived as a finding an analyst had to dismiss. Removed at the user's request rather than
    # tuned, so nothing here half-raises them.
    # What the cards above ALREADY TELL THE READER, not merely which targets they mention. The two
    # are different questions and were answered by one bare-string set, which is how a 2,500 break
    # came to be silenced by a card asserting 1,000 about the same line.
    reported = _reported_assertions(checks)
    checks += _structural_checks(structural or [], locale, covered=reported,
                                 rows=rows, template_def=template_def)
    if stats is not None:
        # Derived from the SAME `covered` set AND the same predicate the emitter used, in the same
        # call: `_structural_checks` drops a failed relation whose target already has its own check,
        # so coverage.failed can legitimately exceed the number of structural cards. Counting it
        # anywhere else — or with a second expression of "was this suppressed" — is how the panel
        # starts lying, and it did: this sum counted a dropped GUARD as reported elsewhere while no
        # card anywhere reported it.
        stats["failed_reported_elsewhere"] = sum(
            1 for res in (structural or []) if _relation_reported_elsewhere(res, reported))
    # Before the calculated path, so its assertions are in the set that path is tested against: a
    # containment gap and a template rollup on the same line in the same column are the same
    # difference, and the reader needs it once.
    checks += _containment_checks(rows, locale, template_def)
    # The assertions are taken here — AFTER the relations, because a relation card is exactly what
    # legitimately covers a calculated line's mismatch: a rollup relation and a calculated line's
    # mismatch are the same arithmetic over the same components, so their assertions coincide and the
    # second card would restate the first.
    checks += _calculated_checks(rows, template_def, locale,
                                 asserted=_reported_assertions(checks))
    return checks


def _calculated_checks(rows: list[dict], template_def: dict | None, locale: str,
                       asserted: set[_Assertion]) -> list[dict]:
    """ONE review item for the template's CALCULATED lines: the document printed a subtotal that its
    own components do not come to.

    The face now shows the COMPUTED figure, so the printed one has to be accounted for somewhere.
    Either a component is mis-mapped or missing, or the filing's own arithmetic is being read
    wrongly — either way this card says what was printed instead. It is the third of the three
    things this queue reports: a section subtotal that does not match the extracted values.

    THERE USED TO BE A SECOND FINDING HERE, ``uncomputed`` — a calculated line with no extracted
    components at all, whose printed figure is therefore on the face unverified. That is a coverage
    fact rather than a defect (nothing disagrees with anything), so it is no longer a card; see the
    ``if not c.computable`` branch below for where the reader is told instead.

    A mismatch reports a difference, so it yields only to a card that reports THE SAME difference
    about the same line in the same column (``asserted``). Under an earlier target-keyed rule a
    balance card asserting 100 on total assets deleted a 250 break between that printed subtotal and
    its own components — a different fact, on no card, in no counter. The relation card for the same
    line does still cover it: a rollup relation and a calculated line's mismatch are the same
    arithmetic over the same components, so their assertions coincide. Nothing here is counted
    anywhere — ``failed_reported_elsewhere`` sums relations only — so a calculated card dropped in
    error leaves no trace on the screen at all, which is why the test is the strict one.

    IT OFFERS NO MECHANICAL FIX, deliberately: writing the PRINTED figure over the computed subtotal
    would close the card while hiding the mis-mapped, missing or double-counted component that caused
    it. That is the anti-fix — it makes the symptom disappear and leaves the defect — so no button is
    offered and nobody may re-add one.
    """
    def L(s: str) -> str:
        return _t(s, locale)

    from app.services import judgement
    from app.services.rollups import node_labels

    if not template_def:
        return []
    names = node_labels(template_def, locale)
    out: list[dict] = []
    # A calculated line is evaluated for the CURRENT column only. Named once because the same string
    # decides what is computed, what the card says it is about, and which claim silences it — three
    # spellings of one column is how the suppression came to be tested against a column nobody had
    # looked at.
    period = "current"
    for basis in ("consolidated", "standalone"):
        calc = _calculated(rows, template_def, basis, period, locale)
        if not any(c.components for c in calc.values()):
            continue
        groups: dict[str, list[dict]] = {}
        for r in rows:
            k = r.get("canonical_key")
            if k:
                groups.setdefault(k, []).append(r)
        # Only report on a basis the document actually presented.
        if not any(_basis_values(r, basis) for r in rows):
            continue
        for key, c in calc.items():
            if c.cycle:
                continue
            reported = _concept_value(groups.get(key, []), basis, period)
            label = names.get(key, key)
            where = f"{label} · {basis}/{period}"
            parts = [[comp.label, "—" if comp.value is None else f"{comp.value:,.0f}", False]
                     for comp in c.components]
            if not c.computable:
                # NO CARD. A subtotal whose components were never extracted is a COVERAGE
                # fact, not a defect: nothing disagrees with anything, and the printed figure is
                # served as the face shows it. It used to raise 'Printed subtotal could not be
                # verified', which is a true sentence and not one of the three things this queue
                # is for — and it competed for attention with the subtotals that genuinely do
                # not match. What the line still carries is its own inspector note saying the
                # figure is unverified, and the coverage band still reports the relation as
                # unevaluated, which is where an un-checkable relation belongs.
                continue
            if reported is None:
                continue            # computed cleanly and the document never printed it: fine
            diff = c.value - reported
            if abs(diff) <= _CALC_TOLERANCE:
                continue            # the printed figure and the components agree
            mismatch = {
                "id": f"chk-calc-{basis}-{key}", "type": "calculated_mismatch", "icon": "≠",
                "title": L("Printed subtotal differs from its components"),
                "where": where, "severity": L("Check failed"), "tone": "high",
                "delta": f"{diff:,.0f}", "target": key,
                # The printed subtotal and every component the card lists beside it: the fix text
                # tells the analyst to check those components against the page.
                "names": [key, *(comp.canonical_key for comp in c.components
                                 if comp.canonical_key)],
                "calc": [[L("Printed in the document"), f"{reported:,.0f}", False],
                         [L("Computed from components"), f"{c.value:,.0f}", True],
                         [L("Difference"), f"{diff:,.0f}", False], *parts],
                "fix": L("The face shows the computed figure. The document printed a different "
                         "one, so a component is mis-mapped, missing, or double-counted — check "
                         "the components below against the page."),
                "subject": {"k": "calculated_mismatch", "key": key, "basis": basis,
                            "period": period},
                "evidence": {"reported": judgement.q(reported),
                             "computed": judgement.q(c.value),
                             "diff": judgement.q(c.value - reported),
                             "components": {comp.canonical_key: judgement.q(comp.value)
                                            for comp in c.components}},
            }
            # Built first and then tested, so what is compared is WHAT THIS CARD SAYS, read by the
            # same function that read it off every card above — not a second expression of the same
            # tuple that could drift from the one on screen. `_assertion_of` returns None if the card
            # ever stops declaring all three, and None is in no set, so the card is served.
            if _assertion_of(mismatch) not in asserted:
                out.append(mismatch)
    return out


# --- Coverage contract, presented ------------------------------------------------------------
# NOTHING NEW IS PERSISTED. `run.result["structural"]` is already the substrate — every relation
# row, pass, fail and skip alike, written once at routes/extractions.py:320 — and
# services/coverage.py exists so the report can be recomputed from a stored run months later. A
# stored snapshot would be a second copy of numbers whose source sits in the same JSON column of
# the same row, and it would drift from the failures the same response lists (an unknown skip
# reason deliberately routes to UNCLASSIFIED, so the taxonomy WILL gain entries).
#
# It is derived at the point it is SERVED, inside GET /documents/{id}/review rather than on an
# endpoint of its own: one fetch, one run. A separate endpoint could show run A's coverage above
# run B's findings, which is this module's own trap in miniature.
#
# ACCEPTING A FINDING NEVER CHANGES COVERAGE. A failed relation stays failed:1 while its finding
# reads "accepted". Judgement is about findings; coverage is about what was evaluable. Collapsing
# the two would rebuild the trap coverage.py exists to prevent.
_COVERAGE_STATUS_LABELS = {
    "UNVALIDATED": "Nothing verified", "PARTIAL": "Partly verified", "FAILED": "Failed",
    "PASSED": "Fully verified", "ABSENT": "Not in this filing",
}

# Buckets in the order a reader should meet them: recoverable first, structurally unrecoverable
# next, authoring defects after that, and the one bucket outside the denominator last. Only
# buckets PRESENT in the report are served — a zero row invites the reader to average them.
_COVERAGE_SKIP_ORDER = ("INPUT_ABSENT", "TAUTOLOGICAL", "NO_REPORTED_SUBTOTAL",
                        "UNEVALUABLE_RULE", "UNCLASSIFIED", "STATEMENT_ABSENT")
_COVERAGE_SKIPS = {
    "INPUT_ABSENT": ("Inputs not extracted", "better extraction would recover these"),
    "TAUTOLOGICAL": ("Fed by a derived value",
                     "fed by a derived value — cannot fail, however good extraction gets"),
    "NO_REPORTED_SUBTOTAL": ("No printed subtotal",
                             "the filing prints no subtotal to reconcile against"),
    "UNEVALUABLE_RULE": ("Rule cannot run as authored",
                         "the rule cannot run as authored — an authoring defect, not an "
                         "extraction gap"),
    "UNCLASSIFIED": ("Unclassified skip reason", "a new skip reason nobody has classified"),
    "STATEMENT_ABSENT": ("Statement not in this filing",
                         "not counted — this filing has no such statement"),
}
_COVERAGE_ALARMS = {
    "BLOCKING_RULE_UNENFORCEABLE": (
        "Blocking rule cannot be enforced",
        "declared blocking and cannot run as authored, so it fires on no filing — this filing "
        "was never checked against it."),
    "UNVALIDATED": (
        "Statement proved nothing",
        "every relation declared for this statement was skipped, so it has no failures and has "
        "proved nothing."),
    "TAUTOLOGICAL_EXCEEDS_EVALUATED": (
        "Mostly checking its own arithmetic",
        "more relations were fed by derived values than were actually evaluated, so the "
        "validation layer is largely confirming its own arithmetic."),
    "PIPELINE_DEFECT": (
        "Pipeline defect",
        "a rule that needs nothing of the filing still could not be run, so this is a defect in "
        "the pipeline or the rulebook rather than a fact about the document."),
}
_COVERAGE_UNAVAILABLE = {
    "not_extracted": "This document has not been extracted, so no relation has been evaluated.",
    "no_template": "No template was attached to this run, so structural validation never ran.",
    "no_relations": "A template was attached but it declares no relation for this filing, so "
                    "nothing was checked. That is an authoring gap, not a clean result.",
    "sample": "The seeded sample project carries no structural validation run.",
}


def _coverage_unavailable(reason: str, locale: str) -> dict:
    """Coverage stated as unavailable, never rendered as zeros. "0 of 0 relations evaluated" is
    the exact misread services/coverage.py exists to prevent, and 0% is worse."""
    return {"available": False, "reason": reason,
            "reason_label": _t(_COVERAGE_UNAVAILABLE[reason], locale)}


def _coverage_block(run, template_def: dict | None, locale: str) -> dict:
    """The coverage report for a run, as labels and ordering over ``CoverageReport.as_dict()``.

    RECOMPUTES NO NUMBER: every integer and both rates come from the one existing spelling in
    services/coverage.py. This adds only localized labels, a reading order for the buckets and
    the alarms, and nothing else. ``failed_reported_elsewhere`` is filled in by ``_build_review``,
    which is the frame that has the `covered` set it must be derived from.

    The unavailable reasons are resolved from ``_run_template_id`` and ``run.result`` only, never
    by parsing ``run.logs`` — the run log keeps the forensic headline (StructuralStage still calls
    ``cov.headline()``, unchanged, over the same rows), and a payload built by scraping a log is a
    second parser of a format nobody versioned.

    ``_run_template_id`` is the SAME resolution the check builders use (``_template_for_run``).
    Reading ``run.template_version_id`` here while they read ``run.options`` let one response say
    "no template was attached to this run" directly above structural and uncomputed findings
    derived from that very template.
    """
    from app.services.coverage import ALARM_UNENFORCEABLE, NOT_DECLARABLE, coverage

    if run is None or not run.result:
        return _coverage_unavailable("not_extracted", locale)
    if _run_template_id(run) is None:
        return _coverage_unavailable("no_template", locale)
    rows = run.result.get("structural") or []
    if not rows:
        # Said loudly: a template WAS attached and it declares nothing for this filing. That is an
        # authoring gap, and rendering it as a clean sheet is the whole failure mode here.
        return _coverage_unavailable("no_relations", locale)

    report = coverage(rows)
    served = report.as_dict()

    def status_label(status: str) -> str:
        return _t(_COVERAGE_STATUS_LABELS.get(status, status), locale)

    aggregate = {**served["aggregate"], "label": _t("All statements", locale),
                 "status_label": status_label(served["aggregate"]["status"])}
    statements = [{**cov, "label": _stmt_label(template_def, cov["statement"], locale),
                   "status_label": status_label(cov["status"])}
                  for cov in served["statements"]]
    # Ordered buckets first, then ANY bucket the report carries that this order list does not know
    # — a new taxonomy bucket must show up unlabelled rather than drop out of the skip list, or the
    # chips would stop totalling the `skipped` count printed beside them. Same reasoning as
    # coverage.py routing an unknown skip reason to UNCLASSIFIED instead of discarding it.
    present = served["aggregate"]["skips"]
    ordered = [b for b in _COVERAGE_SKIP_ORDER if b in present]
    skips = [{"bucket": bucket, "count": present[bucket],
              "label": _t(_COVERAGE_SKIPS[bucket][0], locale) if bucket in _COVERAGE_SKIPS
              else bucket,
              "meaning": _t(_COVERAGE_SKIPS[bucket][1], locale) if bucket in _COVERAGE_SKIPS
              else "",
              # STATEMENT_ABSENT is the only bucket outside the denominator: holding a cash flow
              # statement a standalone-only filing never had against its coverage would make every
              # such filing look incomplete.
              "counts_in_denominator": bucket not in NOT_DECLARABLE}
             for bucket in [*ordered, *sorted(set(present) - set(ordered))]]

    def alarm(a: dict) -> dict:
        code = str(a.get("code") or "")
        # An alarm code with no entry here shows its raw code rather than being hidden: an
        # unfamiliar token on screen is the signal to add a label, and a dropped alarm is a
        # missing warning.
        label, text = _COVERAGE_ALARMS.get(code, (code, code))
        return {"code": code, "label": _t(label, locale), "rule_id": a.get("rule_id"),
                "statement": a.get("statement"), "text": _t(text, locale),
                # An unenforceable blocking rule is the one alarm that names an assurance the run
                # claims and does not have; coverage.py's raw English `note` is not served.
                "assurance_gap": code == ALARM_UNENFORCEABLE}

    unenforceable = report.unenforceable()
    return {
        "available": True,
        "run_id": run.id,
        "engine_version": run.engine_version,
        "aggregate": aggregate,
        "statements": statements,
        "skips": skips,
        # Unenforceable first — it is the only one invisible in every count — then the rest in the
        # report's own order. UNVALIDATED alarms are included and the statement rows also carry
        # the status, so the client renders alarms from THIS list only and never synthesises one
        # from a status, or the same alarm appears twice.
        "alarms": [alarm(a) for a in unenforceable]
                  + [alarm(a) for a in report.alarms if a.get("code") != ALARM_UNENFORCEABLE],
        # `failed_reported_elsewhere` is deliberately absent here rather than seeded with a zero:
        # this frame cannot derive it, and a placeholder integer that a later frame is supposed to
        # overwrite is precisely the fabricated count this codebase keeps deleting. `_build_review`
        # sets it from the `covered` set that suppressed those relations.
    }


# Evidence key → the label the accepted figure is shown under, per subject kind. The keys are the
# ones each builder in this module emits; the labels are the ones its `calc` already uses, so an
# accepted card reads like the card that was accepted.
_EVIDENCE_LABELS: dict[str, dict[str, str]] = {
    "balance": {"assets": "Total assets", "eqliab": "Total equity and liabilities",
                "diff": "Difference", "derived": "Totals derived from the section subtotals"},
    "equity_tie": {"closing_label": "Closing balance row", "closing": "Closing balance",
                   "bs_equity": "Total equity per the balance sheet", "diff": "Difference"},
    # One card per (note, basis, period) covering every untied face line on that note, so the
    # figures are per-face and `entries` is a nested map printing each face line under its own key.
    "structural": {"actual": "Reported", "expected": "Sum of template components",
                   "diff": "Difference", "sign_suspect": "Sign suspect",
                   "components": "Components"},
    # A guard asserts a condition, not an equality: its figures ARE its violation set (`violations`
    # is a nested map, so its members print under their own canonical keys).
    "guard": {"violation_count": "Violations", "violations_keys": "Lines in violation",
              "violations": "Violations"},
    "calculated_mismatch": {"reported": "Printed", "computed": "Computed",
                            "diff": "Difference", "components": "Components"},
    # The one row-shaped card. The concept it mapped to — when it mapped to one the template does not
    # declare — lives in the SUBJECT rather than here: what a reviewer accepts is that the caption
    # really is that concept, so a re-run that files the row elsewhere is a different claim, not the
    # same one re-confirmed. What travels here is the FIGURE, which is what the card prints.
    #
    # `uncomputed`, `off_template` and `low_confidence` had entries here and no longer do, with their
    # cards. `off_template`'s findings are raised as `unmapped` (one category, two ways in); the other
    # two are not raised at all. An accepted judgement stored against one of them is served as
    # ORPHANED rather than silently dropped — see `_serve_judgements`.
    "unmapped": {"value": "Value"},
}


def _evidence_label(subject: dict, key: str, locale: str) -> str:
    """The localized label for one evidence key, falling back to the key itself.

    A component's key IS its label here: ``components`` is keyed on canonical_key precisely so a
    locale cannot change an identity, and printing the raw key beats printing nothing.
    """
    return _t(_EVIDENCE_LABELS.get(str(subject.get("k") or ""), {}).get(key, key), locale)


def _evidence_value(v, locale: str) -> str:
    """One accepted figure, formatted HERE. The client formats no number — same reason the card's
    `calc` and the flip action's displays are server-formatted."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return _t("Yes" if v else "No", locale)
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def _evidence_rows(subject: dict, evidence: dict, locale: str) -> list[list[str]]:
    """The judged figures as ``[label, value]`` pairs — the same two-column shape as a check's
    ``calc``, so the client reuses one renderer for both."""
    out: list[list[str]] = []
    for key, val in (evidence or {}).items():
        if isinstance(val, dict):
            # A nested map is the component list; each component is its own row.
            out += [[_evidence_label(subject, k, locale), _evidence_value(v, locale)]
                    for k, v in val.items()]
            continue
        out.append([_evidence_label(subject, key, locale), _evidence_value(val, locale)])
    return out


def _subject_label(subject: dict, locale: str) -> str:
    """A judged finding named in prose, for a judgement whose finding is no longer raised.

    An orphaned row is never auto-deleted, so the screen has to be able to say what it was about
    without the check that produced it.
    """
    def L(s: str) -> str:
        return _t(s, locale)

    kind = str(subject.get("k") or "")
    if kind == "balance":
        return L("Balance sheet identity")
    if kind == "equity_tie":
        return L("Equity statement closing balance")
    if kind == "note_tie":
        # No longer produced — note-tie cards are not raised (see the review-queue builder). Kept
        # because a JUDGEMENT recorded against one before that change is still in the store, and a
        # stored judgement whose subject cannot be labelled renders as a blank row.
        return f"{L('Note')} {subject.get('note') or ''}".strip()
    if kind == "structural":
        return f"{L('Template relation')} {subject.get('rule_id') or ''} · " \
               f"{subject.get('scope') or ''}"
    if kind == "guard":
        # Named by the SENTENCE, not only the id: the id is what could not tell two guards apart,
        # and an orphaned row has no card left to read the sentence off.
        return f"{L('Rulebook guard')} {subject.get('rule_id') or ''} · " \
               f"{subject.get('scope') or ''} · {subject.get('rule') or ''}".strip()
    if kind == "calculated_mismatch":
        return f"{L('Printed subtotal')} · {subject.get('key') or ''}"
    if kind == "uncomputed":
        return f"{L('Unverified subtotal')} · {subject.get('key') or ''}"
    if kind == "unmapped":
        return f"{L('Unmapped line')} · {subject.get('label') or ''}"
    if kind == "off_template":
        # Both halves, because either alone leaves the orphaned row unreadable: the caption says
        # which printed line, and the key says which concept the template would not carry.
        return f"{L('Line mapped off the template')} · {subject.get('label') or ''} → " \
               f"{subject.get('key') or ''}"
    if kind == "low_confidence":
        return f"{L('Low-confidence mapping')} · {subject.get('label') or ''}"
    return L("A finding that is no longer raised")


def _changed_label(subject: dict, keys: list[str], locale: str) -> str:
    """The quantities that moved since the acceptance, named. "Something changed" over a card of
    figures leaves the reader to diff by eye."""
    return ", ".join(_evidence_label(subject, k, locale) for k in keys)


def _conflict_note(subject: dict, count: int, withheld: bool, locale: str) -> str:
    """What the screen must say when the queue cannot tell two findings apart.

    Said outright, in the reviewer's own words, because the alternative shipped once: the queue
    attributed one reviewer's verdict to a finding they never saw and captioned both cards
    "accepting one accepts them all", which was false of figures that differed. There is no
    honest acceptance available here, so the card says why and offers none.
    """
    def L(s: str) -> str:
        return _t(s, locale)

    cannot_tell = L("findings here share one identity but printed different figures, so the queue "
                    "cannot tell them apart. None of them can be accepted until the extraction "
                    "distinguishes them.")
    note = f"{count} {cannot_tell}"
    if withheld:
        note = note + " " + L("A recorded acceptance for this identity is being withheld: it "
                              "cannot be matched to one of these findings, and attributing it to "
                              "the wrong one would put a name against figures nobody examined.")
    return note


def _build_review(rows: list[dict], filename: str, locale: str = "en",
                  reconciliation: list[dict] | None = None,
                  structural: list[dict] | None = None,
                  template_def: dict | None = None,
                  judgements: list[dict] | None = None,
                  run_id: str = "",
                  coverage_block: dict | None = None) -> dict:
    """Derive the human-in-the-loop review queue from a real extraction — the QA the analyst works
    before export. No demo data involved.

    THREE THINGS REACH THIS QUEUE AND NOTHING ELSE:

    1. a figure extracted from the face of a statement that reaches NO LINE of the output — either
       nothing claimed the caption, or something claimed it for a concept the run's own template puts
       on no statement. One card kind (``unmapped``), two ways in, because from the output's point of
       view they are the same fact: the grid renders the template's lines and nothing else, so such a
       figure appears nowhere at all unless this queue reports it. It ends at a ``remap`` control,
       which is what makes the report an action rather than a complaint.
    2. a VALIDATION RULE that fails — the balance identity, the equity tie, the rulebook's relations
       and its cross-concept guards.
    3. a SECTION SUBTOTAL that does not match the extracted values — a printed subtotal its own
       components do not come to (``calculated_mismatch``), or a rollup relation that breaks.

    WHAT DELIBERATELY DOES NOT REACH IT, though each was a card once: a weakly-mapped line (a
    mapping's strength is a property of the mapping, shown on the row's own confidence badge, and the
    right answer to a weak match is to match it better rather than to bill an analyst for it — it is
    still COUNTED, see ``weak_mappings``), and a calculated line with no extracted components (a
    coverage fact, reported in the coverage band and in the line's own inspector note, where an
    un-checkable relation belongs). Neither is one of the three, and both competed for attention
    with findings that are.

    ``judgements`` are the in-force ACCEPTED judgement rows for this document; each check comes
    back carrying its ``status`` (open / accepted / stale) so the queue distinguishes "nobody has
    looked at this" from "a named person examined these figures and recorded that they stand".

    What travels in the fingerprint, and what deliberately does NOT:

    * ``mapping_confidence`` and ``mapping_method`` travel in NO card's evidence, because no card is
      about a mapping's strength any more. They were quantized into the low-confidence card's
      fingerprint for a reason that was sound at the time — a collapse from 0.41 'fuzzy' to 0.02
      'llm' was otherwise served 'accepted' with nothing changed — and the mechanism went with the
      card. The lesson did not: it is why ``_prov_anchor`` and ``judgement.q`` bucket rather than
      omit.
    * every localized string — title, where, severity, fix, calc labels — because one judgement
      has to hold in all four locales.
    * the formatted ``delta``, the row index and the check id.
    * the human-facing source LABEL ("p.1"). The subject carries ``_prov_anchor`` instead — the
      precise, content-derived locator — because the page label is shared by every line on the
      page, and two findings sharing an identity is how an acceptance on one came to be reported
      as a named judgement on another. That anchor is the row LABEL's geometry, never the value's:
      a subject that moved with the figure reported a still-open finding as corrected. Label-box
      jitter across the anchor's grid re-opens a finding, which is the failure direction worth
      accepting; see ``_ANCHOR_GRID``.
    * ``details.assumed_zero`` and ``details.tolerance``, because they are not displayed on the
      card today. Fingerprinting an invisible field would withdraw an acceptance for a reason the
      human was never shown; the honest order is to display them first, then fingerprint them.
    """
    from app.services import judgement

    def L(s: str) -> str:
        return _t(s, locale)

    _UNMAPPED_FIX = ("No canonical concept matched with confidence. Pick the correct template "
                     "line item, or add an alias so future runs map it automatically.")
    _LOWCONF_FIX = ("The mapping is uncertain. Confirm the concept is correct or reassign it; "
                    "the value and its source location are shown so you can verify against the document.")
    _OFFTPL_FIX = ("This figure was mapped to a concept the run's template does not put on any "
                   "statement, so it appears on no spread. Re-map it to a template line item, or "
                   "un-map it to record that it belongs on none — or re-extract against the "
                   "current template, which may already declare it.")
    stats: dict = {}
    accounting = _accounting_checks(rows, reconciliation or [], locale, structural or [],
                                    template_def, stats=stats)
    checks: list[dict] = list(accounting)
    # The extracted lines the accounting findings NAME, each contributed by the builder that knows
    # which lines its card indicts. The header's third tile counts the rows in none of them.
    named = {k for c in accounting for k in (c.get("names") or []) if k}
    unmapped = 0
    # WEAK MAPPINGS ARE COUNTED BUT NOT QUEUED. The queue carries three things — a face figure
    # nobody could place, a failed validation rule, a section subtotal that does not match its
    # components — and a mapping the engine made at low confidence is none of them: the figure IS
    # published, and the row carries its own score for the Workspace badge to colour.
    #
    # Counted anyway, because two surfaces would otherwise report that the data got better when all
    # that happened is that this queue stopped asking: the credit commentary's "figures are
    # provisional pending sign-off" caveat keys off outstanding review work, and the "lines carrying
    # no finding" tile would absorb every weakly-mapped line and certify it clean. Neither is a
    # queue, and neither is entitled to the improvement.
    weak_mappings: set[int] = set()
    # WHETHER THIS RUN HAS A TEMPLATE AT ALL is `template_def is None` and nothing else — the same
    # spelling `_build_statement` gates its no-template fallback on, so the grid and the queue cannot
    # disagree about it. It is deliberately not "are there any declared keys": a pinned definition
    # with an empty `statements` list is valid (the schema defaults it, and the publish gate does not
    # demand one), and reading emptiness as "no template" made the grid refuse every statement while
    # the queue raised nothing — every extracted figure then appearing nowhere and named by nothing,
    # under a refusal promising they were in the review queue.
    templated = template_def is not None
    declared_keys = _declared_line_keys(template_def)
    on_a_matrix = _rows_on_a_matrix_face(rows, template_def)
    # Row POSITIONS with a finding against them, not keys: an unmapped row has no canonical key,
    # and two rows can legitimately print the same caption, so matching those by label would credit
    # one row's finding to another.
    indicted: set[int] = set()
    # A MATRIX ROW IS NOT A MAPPING FAILURE, and on a real filing it is most of them. The statement
    # of changes in equity is a grid — movement rows against reserve columns ("At 1 January 2023",
    # "Transfer to statutory surplus reserve", "Dividends paid to non-controlling shareholders") —
    # and the template has no concept for a movement, because the statement is served through the
    # matrix view instead (``documents._matrix_columns``). Those rows were never candidates for a
    # canonical key, so reporting them as "extracted but unmapped" indicts the reader for not doing
    # something nothing asked it to do.
    #
    # Measured on the China SCE 2023 filing: 32 of 44 unmapped rows are that one statement, and it
    # has no mapped rows at all. Left in, the queue's largest category is noise, and the one thing
    # the category is FOR — a face figure nobody could place — is buried under it.
    #
    # ``column_index`` is the positive signal, not the page's statement: it is set in exactly one
    # place (``row_reconstruct`` matrix path, where it holds the printed left-to-right position of a
    # component column) so a row carrying it came off a matrix, and a row that does not is judged as
    # before whatever page it sits on.
    for i, r in enumerate(rows):
        if (r.get("canonical_key") or "") in named or (r.get("source_label") or "") in named:
            indicted.add(i)
        key = r.get("canonical_key")
        conf = r.get("mapping_confidence")
        flags = r.get("flags") or []
        first = (r.get("values") or [{}])[0]
        val = first.get("value")
        where = f"{filename} · {_prov_label(first.get('provenance'))}"
        # Whether the MAPPING is weak, spelled once. It raises no card — see `weak_mappings` — but
        # it is counted, because two other surfaces must not report better data because this queue
        # stopped asking.
        weak = ("low_mapping_confidence" in flags
                or (isinstance(conf, (int, float)) and conf < _low_conf_threshold()))
        if key and weak:
            weak_mappings.add(i)
        # REACHES NO LINE IN THE OUTPUT — one category, two ways in. Either nothing claimed the
        # caption, or something claimed it for a concept THIS TEMPLATE DOES NOT DECLARE, and from the
        # output's point of view those are the same fact: an extracted face figure that appears on no
        # line of the spread. The second used to be its own kind (`off_template`); deleting it
        # outright would have been worse than keeping it, because such a row reaches no grid either,
        # so with no card it reaches nothing at all and disappears in silence. It is reported here
        # instead — the queue keeps its three categories and no figure is lost.
        #
        # A row the MATRIX shows is exempt: the equity statement is served as a grid, so its
        # movements do reach the output even though no template LINE declares them.
        off_template = bool(key and templated and key not in declared_keys
                            and i not in on_a_matrix)

        if not key or off_template:
            # A DEMOTED GROSS PARENT IS NOT A MAPPING FAILURE. It mapped — often exactly — and was
            # then un-filed on purpose, because its components are on the face too and filing both
            # would count the money twice (`map_ontology._enforce_containment`). Its money IS on the
            # face, through those components. Reporting it here told the reader "nothing recognised
            # this caption" about a caption recognised at 1.0, and on the real filing it was 5 of the
            # 12 cards in this category. Where the components do NOT add up to it, that IS a finding
            # and `_containment_checks` raises it as what it is: a printed total the lines that
            # replaced it do not account for.
            if _demoted_parent(r) is not None:
                continue
            # ON THE FACE OF THE STATEMENTS, which is the whole of this category. Two positive
            # signals exclude a row, and both are POSITIVE on purpose — a row the extraction could
            # not place anywhere is judged as before, so an unstamped row cannot fall out of the
            # queue for want of a stamp:
            #
            # * `column_index` says the row came off a MATRIX. It is set in exactly one place
            #   (``row_reconstruct``'s matrix path, where it holds the printed left-to-right
            #   position of a component column), so a row carrying it is an equity-statement
            #   movement served through the matrix view rather than a template line. Those rows were
            #   never candidates for a canonical key. Measured on the China SCE 2023 filing: 32 of
            #   44 unmapped rows were that one statement, and it has no mapped rows at all — left
            #   in, the queue's largest category is noise and the one thing it is FOR is buried.
            # * `printed_in is NOTES` says the row was printed in the NOTES rather than on a face
            #   statement. A note's detail lines are served through the Notes tab and the note-detail
            #   routes; they are not lines of the spread and were never going to be, so an unplaced
            #   one is not the defect this category names. (On the real filing every unplaced row is
            #   already a face row, so this changes no count today — it is here so a filing whose
            #   notes carry unplaceable captions cannot fill the queue with them.)
            if any(v.get("column_index") is not None for v in (r.get("values") or [])):
                continue
            if str(r.get("printed_in") or "").lower() == "notes":
                continue
            unmapped += 1
            indicted.add(i)
            checks.append({
                "id": f"chk-unmapped-{i}", "type": "unmapped", "icon": "?",
                "title": r.get("source_label", "Line item"), "where": where,
                # `med`, not `high`: this is a figure to PLACE, not an arithmetic failure. It
                # used to say `low`, which the screen painted red — the loudest colour on the queue
                # went to its mildest finding while every failed check rendered informational-indigo.
                # See `toneColors` in Review.tsx for the vocabulary all three now share.
                "severity": L("Unmapped"), "tone": "med", "delta": "—",
                "target": r.get("source_label", ""),
                # Localized like every other card's labels: "Value" already had a translation that
                # this expression never asked for, which is a translation that does not reach the
                # screen it was written for.
                "calc": [
                    [L("Source label"), r.get("source_label", ""), False],
                    # WHICH OF THE TWO REASONS IT IS. A row that mapped to a concept this template
                    # does not declare has a concept to name, and naming it is the difference
                    # between "nothing recognised this caption" and "the rulebook and the template
                    # disagree about this line" — different fixes, same category.
                    [L("Mapped to"),
                     key if off_template else L("— (no confident match)"), True],
                    [L("Value"), str(val) if val is not None else "—", False],
                ],
                "fix": L(_UNMAPPED_FIX),
                # The row INDEX in this id is a render key only — the React list key and the
                # store's openCheck expand key. Judgement identity is `subject_key`, never `id`,
                # because a row index moves whenever extraction composition changes and an
                # acceptance that followed it would land on a different line item and hide a real
                # problem. Do not rename the id: the client keys its expanded state on it.
                #
                # `anchor` is `_prov_anchor`, NOT the "p.1" the card prints: a page label makes
                # every unmapped line on a page one identity, and accepting one of two "Others"
                # lines then fabricated a judgement on the other.
                # THE CONCEPT IS PART OF THE IDENTITY WHEN THERE IS ONE. For a row that mapped to
                # something the template does not declare, what a reviewer accepts is "this caption
                # really is that concept, and the template is what needs fixing" — so a re-run that
                # files the row elsewhere is a DIFFERENT claim, not the same one re-confirmed. A row
                # nothing claimed has no concept to put here, and its identity is the caption and
                # its geometry alone.
                "subject": {"k": "unmapped", "label": judgement.norm(r.get("source_label")),
                            "anchor": _prov_anchor(first.get("provenance")),
                            **({"key": key} if off_template else {})},
                # The card prints str(val), so string equality invents no rounding the screen
                # never applied.
                "evidence": {"value": str(val) if val is not None else None},
                # …and the fix the sentence above promises. `_UNMAPPED_FIX` has always said "Pick the
                # correct template line item"; until this there was nothing on the card that could.
                "remap": _remap_offer(r, locale),
            })
    for c in checks:
        c["subject_key"] = judgement.subject_key(c["subject"])
        c["evidence_digest"] = judgement.evidence_digest(c["evidence"])
        # Only the structural builder can offer a mechanical fix, and only it can have edited
        # inputs behind an un-recomputed relation. Every other card is explicit about having
        # neither rather than leaving the key absent for the client to guess at.
        c.setdefault("fix_action", None)
        # `names` — the extracted lines a card indicts — is EMPTY on the row-shaped findings rather
        # than absent: an unmapped card is about the row it was built from and nothing else, and
        # naming that row by its caption would match a second row printed under the same caption.
        # The header's tile counts those by row POSITION instead (see `indicted`).
        c.setdefault("names", [])
        c.setdefault("inputs_edited", False)
        c.setdefault("inputs_edited_keys", [])
        c.setdefault("inputs_edited_note", "")
        # Only the three ROW-shaped findings are about a single printed line, so only they can be
        # re-mapped. An accounting finding is about a relation between several concepts; offering to
        # re-map it would have to guess which of them the analyst meant.
        c.setdefault("remap", None)
    applied = judgement.apply_judgements(
        checks, judgements or [],
        label_fn=lambda subj: _subject_label(subj, locale),
        rows_fn=lambda subj, ev: _evidence_rows(subj, ev, locale),
        changed_fn=lambda subj, keys: _changed_label(subj, keys, locale),
        conflict_fn=lambda subj, n, withheld: _conflict_note(subj, n, withheld, locale))
    # Conflict first, then stale, then open, then accepted, stably so builder order survives
    # inside a rank. The client does no sorting: two orderings of one list is two answers to one
    # question.
    checks.sort(key=lambda c: judgement.rank(c["status"]))

    counts = applied["counts"]
    cov_block = coverage_block if coverage_block is not None \
        else _coverage_unavailable("not_extracted", locale)
    if cov_block.get("available"):
        # Derived by `_accounting_checks` from the very `covered` set that suppressed those
        # relations, and injected here because this is the only frame that has both. Computing it
        # a second time inside the coverage presenter is how the band starts contradicting the
        # cards above it.
        cov_block["failed_reported_elsewhere"] = stats.get("failed_reported_elsewhere", 0)
    total = len(checks)
    # ROWS NAMED BY NO FINDING — which is what the header tile beside it says, in all four locales.
    # It used to be `len(rows) - (unmapped + low_conf)`, so every line indicted by a balance, note
    # tie, structural, guard, calculated_mismatch or uncomputed finding was counted as having no
    # finding: a 9-row run with 4 checks against 4 of those rows rendered "4 open · 9 lines with no
    # finding". The old label ("passed") did not assert WHICH lines it counted; the new one does, so
    # the quantity is now the one the label names. `names` is contributed by each builder — see
    # `_accounting_checks`.
    #
    # THE POPULATION IS DEFINED ONCE, FOR BOTH PATHS, in services/review_lines.py — this route and
    # the sample route (api/routes/projects.py::_demo_review_summary) call the same predicate. Spelled
    # here as `len(rows) - len(indicted)`, it counted every serialized row including subtotals and
    # totals, while the sample counted only its `kind == "item"` rows: two populations under one
    # label, and the sample understated its own by the 8 subtotal/total rows no finding named.
    # A WEAKLY-MAPPED LINE IS NOT A CLEAN LINE, even though it no longer raises a card. Without
    # this the tile would grow by every retired low-confidence finding — on the filing this was
    # measured against, 56 lines moving from red to green because the queue stopped asking about
    # them — and a green counter beside a red one is exactly where a reader looks for "how much of
    # this is settled".
    passed = review_lines.lines_with_no_finding(
        rows, lambda i, _r: i in indicted or i in weak_mappings)
    # Each tab carries the check TYPES it selects, so the client filters by what the tab means
    # rather than by its position in this list. `types: None` is the everything tab. Positional
    # agreement between a server list and a client array is the bug that made the page-scope
    # chips filter by the wrong kind, and it is not repeated here.
    #
    # The tabs count by TYPE over the FULL list regardless of status, and no `statuses` dimension
    # is added: a second predicate would have to be spelled once here for the counts and once in
    # the TSX for the rows, and that drift is the counts-disagree-with-content bug the last three
    # commits closed. An accepted finding therefore stays in its tab and stays on screen.
    _assert_known_kinds(checks)
    accounting_types = sorted({c["type"] for c in accounting})
    return {
        "run_id": run_id,
        "checks": checks,
        "tabs": [
            {"label": L("All"), "count": total, "types": None},
            {"label": L("Checks"), "count": len(accounting), "types": accounting_types},
            {"label": L("Unmapped"), "count": unmapped, "types": sorted(_ROW_SHAPED_TYPES)},
            # TWO CHIPS, ONE PER SHAPE, and no chip for a kind that cannot be emitted. The tabs
            # partition the list, so a chip whose `types` select nothing renders as a permanent "0"
            # that filters the list to empty — a control that looks like a filter and is a dead end.
            # "Low confidence" and "Off template" went with their cards. The accounting chip carries
            # the types actually EMITTED on this run rather than all of `_ACCOUNTING_TYPES`, so
            # clicking it selects exactly the cards below it.
        ],
        # `open` is outstanding work, so it includes the stale cards — someone vouched for figures
        # that have since moved, which is more urgent than an untouched finding, not less — and the
        # conflict cards, which nobody can accept at all. `stale` and `conflict` are those subsets,
        # reported separately so the screen can say each one out loud.
        # SERVED, not recomputed by each reader. A weak mapping raises no card, but two consumers
        # need to know how many there are — the commentary (so its data-quality caveat does not
        # lapse because this queue narrowed) and the header tile (so a weakly-mapped line is not
        # counted as carrying no finding). Both read it from here.
        "weak_mappings": len(weak_mappings),
        "summary": {"open": counts["open"] + counts["stale"] + counts["conflict"],
                    "accepted": counts["accepted"], "stale": counts["stale"],
                    "conflict": counts["conflict"], "passed": passed},
        "judgements": {"orphaned": applied["orphaned"]},
        "coverage": cov_block,
        # The template lines a row-shaped finding may be re-mapped onto. ONCE per payload, not per
        # card: 180-odd concepts repeated on every unmapped row is the same list served forty times.
        # Empty when the run named no template, which is also when the offer is refused — there is
        # nothing to map onto and a select with no options is worse than no control.
        "remap_targets": _remap_targets(template_def, locale),
    }


@router.get("/{document_id}/audit",
            dependencies=[Depends(require(Permission.COMMENTARY_VIEW)),
                          Depends(authorized_document)])
def get_document_audit(document_id: str) -> dict:
    """This document's audit trail — every run against it, with token usage and how long it took.

    THE GAP THIS CLOSES. Runs against an uploaded document have always been recorded, under the
    DOCUMENT's id: extraction (both outcomes) in ``extractions._run_extraction_task``, and the credit
    narrative in ``generate_credit_narrative`` below. Nothing served that key. The only audit route
    was ``/projects/{id}/audit`` and the client asked it for the demo project, so every real run
    wrote an entry into a bucket no screen could reach — an audit trail that existed and could not be
    read, which is indistinguishable from not keeping one.

    Same permission as the project trail (COMMENTARY_VIEW — the Analysis screen is where both are
    shown) and the same ownership predicate as every other read of a document, so one analyst cannot
    read another's runs and a document they may not see answers 404 rather than 403.

    No seeded rows are folded in, unlike the project route: those describe the sample, and mixing
    them into a real filing's trail would put runs that never happened under this document's name.
    """
    from app.services import audit as audit_svc

    return audit_svc.served_trail(document_id)


@router.get("/{document_id}/run", dependencies=[Depends(authorized_document)])
def get_document_run(document_id: str, session: Session = Depends(db)) -> dict:
    """The latest extraction result for a document (drives the Export preview/counts for a
    real run). 404 until the document has been extracted."""
    from app.db.models import Document

    if session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    # Which rulebook this run read the filing against, as the run recorded it (see
    # ``routes.extractions.rulebook_record``). Reported, never re-derived: a reader deciding after
    # the fact which rulebook "must" have been in force is how a superseded one got labelled as
    # the current one.
    # WHICH STATEMENTS THIS SPREAD HAS, so the Workspace's tabs are the template's rather than a
    # hardcoded three. Read off the template the RUN was pinned to, never the newest one stored:
    # a template published later must not change the tabs above an existing spread, which is the
    # same rule ``_template_for_run`` exists to hold. A run with no template gets an empty list and
    # the client falls back — an empty list means "this run cannot say", not "this run has none".
    from app.services.statements import declared_statements

    # WHICH BASES THIS FILING ACTUALLY LABELLED, so the Workspace opens on one it has instead of
    # always opening on Consolidated and then substituting. A filing that printed one column has one
    # answer; offering a choice between two when only one exists invites the analyst to click the
    # empty one, and the substitution notice then explains a situation the screen created.
    #
    # Read off the run's own rows — the same reading ``basis_values`` filters by, so the selector and
    # the grid cannot disagree about which bases exist. Empty means nothing was extracted.
    return {"run_id": run.id, "status": run.status,
            "rulebook": (run.options or {}).get("rulebook"),
            "statements": declared_statements(_template_for_run(session, run)),
            "bases": bases_present(run.result.get("rows") or []),
            "result": run.result}


@router.get("/{document_id}/analysis", dependencies=[Depends(authorized_document)])
def get_document_analysis(document_id: str, locale: str = Query("en"),
                          session: Session = Depends(db)) -> dict:
    """Derived analysis for a document, from its latest extraction: computed ratios and
    plain-language notes (recomputed from the current values so edits show) plus the stored
    disclosure scan. Empty (but valid) until the document has been extracted."""
    from app.services.derived import (
        build_credit_analysis, build_free_notes, compute_ratios, localize_disclosures)

    run = _latest_run(session, document_id)
    if run is None or not run.result:
        return {"ratios": [], "disclosures": [], "notes": [],
                "credit": build_credit_analysis([], [], locale=locale)}
    rows = run.result.get("rows", [])
    # The run's own template, so this screen's ratios, credit factors and notes are computed from
    # the figures the spread shows — including calculated lines the filing does not print. Passing
    # None here is what left the Analysis screen reading a different set of numbers from the KPI
    # view beside it (see rollups.figures_as_shown).
    template_def = _template_for_run(session, run)
    disclosures = localize_disclosures(run.result.get("disclosures", []), locale)
    credit = build_credit_analysis(rows, disclosures, locale=locale, template_def=template_def)
    # Fold in the cached LLM narrative (auto-generated at extraction when a provider is
    # configured, or produced on demand) so the Analysis screen shows it without a click.
    narrative = run.result.get("credit_narrative")
    if narrative and narrative.get("text"):
        credit = {**credit, "narrative": narrative}
    return {
        "ratios": compute_ratios(rows, locale=locale, template_def=template_def),
        "disclosures": disclosures,
        "notes": build_free_notes(rows, locale=locale, template_def=template_def),
        # Credit view combines the extracted ratios with the report's narrative disclosures.
        "credit": credit,
    }


@router.post("/{document_id}/credit-narrative",
             dependencies=[Depends(require(Permission.ANALYSIS_RUN)), Depends(authorized_document)])
def run_credit_narrative_endpoint(document_id: str, locale: str = Query("en"),
                                  session: Session = Depends(db)) -> dict:
    """Generate an LLM credit narrative that rationalises the deterministic credit view.

    The numbers stay deterministic — the model is given the already-computed stance, rating
    factors and report signals and only writes grounded prose (see analysis_llm.CREDIT_SYSTEM).
    Requires a real LLM provider (config ``llm.provider``); returns 409 when none is configured
    so the caller can keep using the deterministic summary. The run is written to the audit log."""
    from datetime import datetime, timezone

    from app.config import get_settings
    from app.ports.registry import registry as reg
    from app.services import audit as audit_svc
    from app.services.analysis_llm import run_credit_narrative
    from app.services.derived import build_credit_analysis, localize_disclosures

    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")

    rows = run.result.get("rows", [])
    disclosures = localize_disclosures(run.result.get("disclosures", []), locale)
    credit = build_credit_analysis(rows, disclosures, locale=locale,
                                   template_def=_template_for_run(session, run))
    if not credit.get("factors") and not credit.get("flags"):
        raise HTTPException(status_code=422,
                            detail="Insufficient extracted data for a credit narrative")

    settings = get_settings()
    provider_id = settings.llm.provider
    entity = run.result.get("entity") or run.result.get("filename") or ""
    run_id = audit_svc.make_run_id(entity or "credit")
    if provider_id == "stub":
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured. Set llm.provider (e.g. 'anthropic') to enable "
                   "the credit narrative; the deterministic credit summary remains available.")

    try:
        provider = reg.get("llm", provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Timed around the provider call itself — the only part that takes any time, and the part a
    # reader comparing a slow narrative to a fast one is asking about. Started before the try so the
    # failure path measures the same interval as the success path.
    began = datetime.now(timezone.utc)
    try:
        result, meta = run_credit_narrative(provider, credit, entity=entity, locale=locale,
                                            max_tokens=settings.llm.max_tokens)
    except Exception as exc:  # provider unreachable / no key / bad response
        audit_svc.record(document_id, audit_svc.AuditEntry(
            run_id=run_id, entity=entity, action="credit_narrative", provider=provider_id,
            model=settings.llm.model, input_tokens=None, output_tokens=None, status="failed",
            duration_ms=audit_svc.elapsed_ms(began)))
        raise HTTPException(status_code=502, detail=f"Credit narrative failed: {exc}") from exc

    model = meta.get("model", settings.llm.model)
    audit_svc.record(document_id, audit_svc.AuditEntry(
        run_id=run_id, entity=entity, action="credit_narrative", provider=provider_id,
        model=model, input_tokens=meta.get("input_tokens"),
        output_tokens=meta.get("output_tokens"), status="succeeded",
        duration_ms=audit_svc.elapsed_ms(began)))

    # Persist the narrative on the run so the Excel/JSON export can fold it in. Reassigning
    # the JSON column marks it dirty for the commit.
    run.result = {**run.result, "credit_narrative": {
        "text": result.narrative, "provider": provider_id, "model": model}}
    session.commit()

    return {"narrative": result.narrative, "provider": provider_id, "model": model}


def _localize_commentary(c: dict, locale: str) -> dict:
    """Localize a commentary payload's fixed catalog strings (headline/assessment/points and
    metric/trend labels) via the i18n table — the same table the demo path uses."""
    from app.sample.i18n_data import tr

    if locale == "en" or not c.get("metrics"):
        return c
    c["headline"] = tr(c["headline"], locale)
    c["assessment"] = tr(c["assessment"], locale)
    c["data_quality"] = tr(c["data_quality"], locale)
    c["strengths"] = [tr(s, locale) for s in c["strengths"]]
    c["weaknesses"] = [tr(w, locale) for w in c["weaknesses"]]
    for mtr in c["metrics"]:
        mtr["label"] = tr(mtr["label"], locale)
    for tnd in c.get("trends", []):
        tnd["label"] = tr(tnd["label"], locale)
    return c


@router.get("/{document_id}/commentary",
            dependencies=[Depends(require(Permission.COMMENTARY_VIEW)), Depends(authorized_document)])
def get_document_commentary(document_id: str, locale: str = Query("en"),
                            basis: str = Query("consolidated"),
                            session: Session = Depends(db)) -> dict:
    """Data-driven financial commentary for a REAL document, computed from its latest
    extraction (ratios, year-on-year trends, strengths/weaknesses) — not the demo project.
    Open review items are counted from the same extraction so the assessment stays honest
    about provisional figures. Empty (valid) shape until the document is extracted."""
    from app.db.models import Document
    from app.services.commentary import build_commentary_from_rows

    run = _latest_run(session, document_id)
    if run is None or not run.result:
        return {"headline": "", "assessment": "", "metrics": [], "trends": [],
                "strengths": [], "weaknesses": [], "data_quality": "", "basis": ""}
    rows = run.result.get("rows", [])
    # The in-force judgements are passed here too, so the data-quality prose stops counting
    # findings a human has already accepted. An accepted finding is no longer OPEN because a named
    # person examined those figures and recorded that they stand; the reason and the actor on the
    # judgement row are the record of that, and they are the only guard — this route will report
    # better data quality after a reviewer accepts breaks, which is correct and is why the
    # acceptance carries a name.
    doc = session.get(Document, document_id)
    review = _build_review(rows, "", locale, run.result.get("reconciliation", []),
                           run.result.get("structural", []), _template_for_run(session, run),
                           judgements=_inforce_judgements(session, doc), run_id=run.id)
    units = run.result.get("units") or {}
    # OUTSTANDING WORK, NOT QUEUE LENGTH. The commentary's "some reported figures are provisional
    # pending sign-off" caveat, and its refusal to call a filing's data strong, key off this number
    # — so handing it the queue's own length would make the assessment improve every time the queue
    # narrows. It just did: a mapping made at low confidence no longer raises a card, and on the
    # filing this was measured against that is 56 rows. The figures are exactly as provisional as
    # they were; only the queue changed. So the weak mappings are added back in here, where the
    # question is "how much of this should a reader trust", not "what is there to work on".
    weak = review.get("weak_mappings", 0)
    c = build_commentary_from_rows(
        rows, open_review_items=review["summary"]["open"] + weak, basis=basis,
        currency=units.get("currency") or "", units=units.get("units_label") or "")
    return _localize_commentary(c, locale)


def _judgement_history_entry(row) -> dict:
    """The state a judgement row is LEAVING, for its history list.

    Appended before every change and never removed, so an accept → withdraw → accept sequence is
    one row with two history entries rather than three rival rows a reader has to date-sort. The
    evidence goes in too: what the figures were when that verdict was recorded is the part a later
    reader cannot reconstruct.

    ``at`` is when THAT VERDICT WAS MADE, which is ``row.updated_at`` — read before this write
    touches it, so it still holds the moment the state being left was recorded. Stamping
    ``_now_iso()`` here dated every entry with the time of the CHANGE, i.e. its successor's
    timestamp: accept at 10:00 and withdraw at 15:00 and the record said the acceptance was made
    at 15:00, while 10:00 survived in no column at all (``updated_at`` is bumped by ``onupdate``
    in the same flush). Nothing serves history yet, so no screen was wrong — but the audit trail
    being accumulated was, from its first row.

    No column was added to carry this. ``db/base.py::_reconcile_schema`` back-fills the
    ``documents`` table ONLY and there is no Alembic, so a column added to ``review_judgements``
    would simply be missing from any developer database that already has the table, and every
    query selecting it would 500. ``updated_at`` is already the moment this row's current verdict
    was written, so reading it is also the answer that keeps one quantity in one place.
    """
    at = row.updated_at or row.created_at
    return {"verdict": row.verdict, "reason": row.reason or "", "actor": row.actor or "",
            "actor_role": row.actor_role or "",
            "at": at.isoformat(timespec="seconds") if at else _now_iso(),
            "evidence": row.evidence or {}, "run_id": row.run_id or ""}


def _inforce_judgements(session: Session, doc) -> list[dict]:
    """The document's IN-FORCE accepted judgements, as plain dicts.

    Only ``verdict == "accepted"`` matches. A withdrawn row is kept forever — erasing who accepted
    a break is not something an audit trail should permit — but it governs nothing.
    """
    from app.db.models import ReviewJudgement

    if doc is None:
        return []
    rows = session.execute(
        select(ReviewJudgement).where(
            ReviewJudgement.document_id == doc.id,
            ReviewJudgement.tenant_id == doc.tenant_id,
            ReviewJudgement.verdict == "accepted")
    ).scalars().all()
    return [{"subject_key": r.subject_key, "subject": r.subject or {},
             "evidence": r.evidence or {}, "reason": r.reason or "", "actor": r.actor or "",
             "actor_role": r.actor_role or "", "run_id": r.run_id or "",
             "at": (r.updated_at or r.created_at).isoformat(timespec="seconds")}
            for r in rows]


@router.get("/{document_id}/review", dependencies=[Depends(authorized_document)])
def get_document_review(document_id: str, locale: str = Query("en"),
                        session: Session = Depends(db)) -> dict:
    """Real review queue for a document, derived from its latest extraction — each finding with
    its human judgement, and the coverage contract for the relations that were never evaluable.

    Both come from ONE read of ONE run: a coverage endpoint of its own could show run A's coverage
    above run B's findings.
    """
    from app.db.models import Document

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        # The empty shape is built by the SAME function as the real one rather than hand-written
        # here: a second literal is a second answer to "what does an empty queue look like", and
        # the previous one-tab literal disagreed with the four tabs the real branch serves.
        return _build_review([], doc.filename or "document", locale, [], [], None, [], "",
                             coverage_block=_coverage_unavailable("not_extracted", locale))
    template_def = _template_for_run(session, run)
    return _build_review(run.result.get("rows", []), doc.filename or "document", locale,
                         run.result.get("reconciliation", []),
                         run.result.get("structural", []), template_def,
                         judgements=_inforce_judgements(session, doc), run_id=run.id,
                         coverage_block=_coverage_block(run, template_def, locale))


class JudgementBody(BaseModel):
    """An acceptance, posted with the digest the card was showing.

    The subject key travels in the BODY and never in a path: a structural finding's scope_key
    contains a "/" (services/structural_checks.py:526), so no check identifier is URL-safe.
    """

    subject_key: str
    evidence_digest: str
    reason: str = ""


@router.post("/{document_id}/review/judgements",
             dependencies=[Depends(require(Permission.REVIEW_RESOLVE)),
                           Depends(authorized_document)])
def accept_review_finding(document_id: str, body: JudgementBody, locale: str = Query("en"),
                          session: Session = Depends(db),
                          principal: Principal = Depends(current_principal)) -> dict:
    """Record that a named person examined a finding's figures and judged that they stand.

    "Accepted" does NOT mean the check passed. It means a human looked, and it is the distinction
    the review found missing: before this there was no way to tell "nobody has looked at this"
    from "reviewed and deemed acceptable", so a finding examined and dismissed stayed red forever.

    The posted ``evidence_digest`` must match the server's current one. An acceptance must never
    attach to figures that changed while the card was open — that is the whole reason identity is
    split into subject and evidence.

    The posted subject is resolved against EVERY check sharing it, not the first in rank order.
    Matching by subject_key alone compared the posted digest against whichever card sorted first,
    so of two findings on one subject the second was told "the figures changed while this card was
    open" — quoting the other line's figures — on every retry, forever. And a subject whose
    findings disagree about their evidence is refused outright: see
    ``judgement.apply_judgements`` for why no judgement may be attributed there at all.
    """
    from sqlalchemy.exc import IntegrityError

    from app.db.models import Document, ReviewJudgement

    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail={"error": "no_run"})
    if not body.reason.strip():
        # An acceptance with no stated reason is an unsigned claim: the actor is recorded either
        # way, but what they concluded is the part a later reader needs.
        raise HTTPException(status_code=422, detail={"error": "reason_required"})

    doc = session.get(Document, document_id)
    # One build, in the request locale. No separate English rebuild is needed because identity is
    # locale-free by construction — see services/judgement.py.
    review = _build_review(run.result.get("rows", []), doc.filename or "document", locale,
                           run.result.get("reconciliation", []),
                           run.result.get("structural", []), _template_for_run(session, run),
                           judgements=_inforce_judgements(session, doc), run_id=run.id)
    group = [c for c in review["checks"] if c["subject_key"] == body.subject_key]
    if not group:
        raise HTTPException(status_code=404, detail={"error": "finding_not_found",
                                                    "subject_key": body.subject_key})
    if any(c["conflict"] for c in group):
        # The identity scheme failed for this subject. Refusing is the honest answer: accepting
        # would write one reviewer's verdict against figures they demonstrably did not see, and
        # there is no way to tell from the request which of the group they were looking at.
        raise HTTPException(status_code=409, detail={
            "error": "subject_conflict", "subject_key": body.subject_key, "count": len(group),
            "evidence_digests": sorted({c["evidence_digest"] for c in group}),
            "note": group[0]["conflict_note"]})
    check = next((c for c in group if c["evidence_digest"] == body.evidence_digest), None)
    if check is None:
        # Every card on this subject carries one digest (the group is not a conflict), so quoting
        # the first is quoting the figures now on screen — not another line's.
        current = group[0]
        raise HTTPException(status_code=409, detail={
            "error": "evidence_changed",
            "current": {"evidence_digest": current["evidence_digest"],
                        "status": current["status"],
                        "accepted_rows": _evidence_rows(current["subject"], current["evidence"],
                                                        locale)}})

    reason = body.reason.strip()[:2000]

    def _current_row():
        return session.execute(
            select(ReviewJudgement).where(
                ReviewJudgement.document_id == doc.id,
                ReviewJudgement.tenant_id == doc.tenant_id,
                ReviewJudgement.subject_key == body.subject_key)).scalars().first()

    def _record(row) -> None:
        row.subject = check["subject"]
        row.evidence = check["evidence"]
        row.context = check.get("context") or {}
        row.verdict = "accepted"
        row.reason = reason
        row.actor = principal.username
        row.actor_role = principal.role.value
        row.run_id = run.id
        flag_modified(row, "history")

    row = _current_row()
    if row is not None:
        row.history = [*(row.history or []), _judgement_history_entry(row)]
        _record(row)
        session.commit()
    else:
        new = ReviewJudgement(tenant_id=doc.tenant_id, document_id=doc.id,
                              subject_key=body.subject_key, history=[])
        session.add(new)
        _record(new)
        try:
            session.commit()
        except IntegrityError:
            # REVIEWER and ADMIN both hold REVIEW_RESOLVE, so two people can POST for the same
            # subject at the same instant: both SELECTs return None and the loser's INSERT
            # violates uq_judgement_subject. Letting that surface was a 500 with the reviewer's
            # typed reason thrown away. The loser's acceptance is instead recorded onto the row
            # that won, exactly as a second sequential POST would be — the winner's verdict is
            # appended to `history`, so neither judgement is lost.
            session.rollback()
            row = _current_row()
            if row is None:
                # The unique constraint fired and yet nothing is there to record onto: the write
                # failed for a reason this handler does not understand, so say so rather than
                # reporting an acceptance that was never stored.
                raise HTTPException(status_code=409, detail={
                    "error": "judgement_write_conflict",
                    "subject_key": body.subject_key}) from None
            row.history = [*(row.history or []), _judgement_history_entry(row)]
            _record(row)
            session.commit()
    # No summary and no check comes back: the client invalidates and refetches, so every count on
    # the screen keeps exactly one origin.
    return {"ok": True, "subject_key": body.subject_key, "status": "accepted"}


@router.delete("/{document_id}/review/judgements/{subject_key}",
               dependencies=[Depends(require(Permission.REVIEW_RESOLVE)),
                             Depends(authorized_document)])
def withdraw_review_judgement(document_id: str, subject_key: str,
                              session: Session = Depends(db),
                              principal: Principal = Depends(current_principal)) -> dict:
    """Withdraw an acceptance. ``subject_key`` is 64 hex characters, so it IS URL-safe.

    The row is never deleted — the verdict flips and the prior state is appended to ``history``.

    Deliberately NOT refused on a conflicted subject, unlike acceptance: a judgement that can no
    longer be attributed to one finding is precisely the one a reviewer needs to be able to take
    back, and withdrawing it puts a name against nothing.
    """
    from app.db.models import Document, ReviewJudgement

    doc = session.get(Document, document_id)
    row = session.execute(
        select(ReviewJudgement).where(
            ReviewJudgement.document_id == doc.id,
            ReviewJudgement.tenant_id == doc.tenant_id,
            ReviewJudgement.subject_key == subject_key,
            ReviewJudgement.verdict == "accepted")).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "no_judgement"})
    row.history = [*(row.history or []), _judgement_history_entry(row)]
    row.verdict = "withdrawn"
    row.actor = principal.username
    row.actor_role = principal.role.value
    flag_modified(row, "history")
    session.commit()
    return {"ok": True, "subject_key": subject_key, "withdrawn": True}


class RemapBody(BaseModel):
    """A row re-mapped by hand onto a different template line.

    ``row_ref`` travels in the BODY for the same reason a subject key does: it is a hash, and the
    thing it identifies is a row rather than a resource with a URL of its own.
    """

    row_ref: str
    # "" un-maps the row: the analyst's judgement that this printed line belongs to no template
    # concept at all, which is the exact inverse of the action and the only way back from a re-map
    # that started from unmapped.
    canonical_key: str = ""
    reason: str = ""


@router.post("/{document_id}/review/remap",
             dependencies=[Depends(require(Permission.EXTRACTION_EDIT)),
                           Depends(authorized_document)])
def remap_review_row(document_id: str, body: RemapBody, locale: str = Query("en"),
                     session: Session = Depends(db),
                     principal: Principal = Depends(current_principal)) -> dict:
    """Re-file one printed row onto a different template line — resolving a review finding.

    The queue's two row-shaped findings both END here. An unmapped row's fix text has always read
    "Pick the correct template line item" and a low-confidence row's "Confirm the concept is correct
    or reassign it", and until this there was nothing on either card that could do it: the only write
    the screen offered was the sign flip, and the only value endpoint is keyed on the canonical key an
    unmapped row does not have.

    THE ROW IS FOUND BY ``_row_ref``, and an ambiguous ref is REFUSED rather than resolved to the
    first match. Two rows can share an anchor — the fallback for a page with no label geometry is the
    printed line's vertical band, and two sub-tables on one baseline collide — and writing a concept
    onto the wrong one of those would move a real figure onto a concept nobody chose, invisibly,
    because the card the analyst was reading would disappear either way. ``apply_judgements`` refuses
    to attribute an acceptance in exactly this case; so does this.

    The target must be a line the RUN'S OWN template defines and must not be a calculated subtotal —
    the same two conditions ``_remap_targets`` applies to what the card offers, checked again here
    because a client is not a gate.

    Recorded, not silent: the row keeps ``remap`` naming where it came from, who moved it and why, its
    method becomes ``manual_remap``, and its confidence goes to 1.0 — a human decision is the
    strongest evidence in the system, and leaving the old score would leave the low-confidence finding
    on screen after the analyst answered it. The previous key is kept, so re-mapping back (or to "")
    is the way out and no state is lost.
    """
    from app.db.models import Document

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    ref = (body.row_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=422, detail="row_ref is required")

    result = dict(run.result)
    rows = result.get("rows", [])
    matches = [r for r in rows if _row_ref(r) == ref]
    if not matches:
        raise HTTPException(status_code=404,
                            detail="No extracted row matches that reference. Reload the queue: the "
                                   "document may have been re-extracted since it was rendered.")
    if len(matches) > 1:
        # Honest refusal over a coin flip — see the docstring.
        raise HTTPException(
            status_code=409,
            detail=f"{len(matches)} extracted rows share that reference, so which one to re-map "
                   "cannot be decided. Re-map from the Workspace, where the rows are listed "
                   "separately.")

    key = (body.canonical_key or "").strip()
    template_def = _template_for_run(session, run)
    if key:
        allowed = {t["canonical_key"] for t in _remap_targets(template_def, locale)}
        if key not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"'{key}' is not a line this run's template offers as a re-map target. "
                       "Calculated subtotals and section headers are excluded.")

    target = matches[0]
    prior = target.get("canonical_key") or ""
    if prior == key:
        raise HTTPException(status_code=409,
                            detail=f"That row is already mapped to '{key}'." if key
                                   else "That row is already unmapped.")
    target["canonical_key"] = key or None
    target["mapping_method"] = "manual_remap" if key else "manual_unmap"
    target["mapping_confidence"] = 1.0 if key else None
    # THE SECTION TAG FOLLOWS THE CONCEPT, here and in the segmentation store. A row's analyst
    # section is derived from the concept it maps to, so a re-map that left the tag alone would
    # serve a row whose figure is now a current liability under Non-current liabilities — and the
    # tag is the thing a reader groups by, so the error would be invisible in the row itself.
    #
    # Both places, because they are joined on the row id and disagree loudly if only one moves: the
    # row carries the tag it is served with, and ``result["buckets"]`` carries the membership the
    # bucket screens read. Nothing else about the segmentation is recomputed — a re-map is one row
    # changing its mind, not a reason to re-run a stage over the document.
    _retag_row(result, target, key, session, run)
    target["remap"] = {"from": prior, "to": key, "reason": body.reason.strip()[:2000],
                       "by": getattr(principal, "username", "") or "", "at": _now_iso()}
    # On the ROW's flags, not only in the payload: the export and the statement inspector both read
    # row flags, so a figure moved by hand says so wherever it is read.
    flags = [f for f in (target.get("flags") or []) if not f.startswith("remapped_by_reviewer:")]
    flags.append(f"remapped_by_reviewer:{prior or 'unmapped'}->{key or 'unmapped'}")
    # The findings the old mapping raised are answered by the analyst's decision, so the flags that
    # raise them go with it. Left behind, the row would arrive at the queue re-mapped AND still
    # low-confidence, which reads as the action having failed.
    if key:
        flags = [f for f in flags if f != "low_mapping_confidence"]
    target["flags"] = flags
    for v in target.get("values") or []:
        conf = v.get("confidence")
        if isinstance(conf, dict):
            v["confidence"] = {**conf, "mapping": 1.0 if key else conf.get("mapping"),
                               "flags": [f for f in (conf.get("flags") or [])
                                         if not (key and f == "low_mapping_confidence")]}

    result["rows"] = rows
    run.result = result
    flag_modified(run, "result")
    session.commit()
    return {"ok": True, "row_ref": ref, "label": target.get("source_label") or "",
            "from": prior, "to": key, "remap": target["remap"]}


def _retag_row(result: dict, row: dict, key: str, session, run) -> None:
    """Move one row's analyst-section tag, and its membership in the stored segmentation, to the
    section its NEW concept belongs to. A row re-mapped to nothing loses its tag with its concept."""
    from app.core.models.enums import PrintedIn
    from app.services.buckets import BUCKET_LABELS, bucket_of

    ont = _ontology_for_run(session, run)
    section = None
    declared = None
    if key and ont is not None:
        concept = next((m for m in ont.mappings if m.canonical_key == key), None)
        if concept is not None:
            section = (concept.section_scope or [None])[0]
            declared = concept.analyst_bucket
    bucket = None
    if key:
        bucket, _why = bucket_of(section, None, declared)
    row["section"] = section
    row["bucket"] = bucket
    row["bucket_label"] = BUCKET_LABELS.get(bucket or "")

    store = result.get("buckets")
    if not isinstance(store, dict):
        return
    row_id = row.get("id")
    if not row_id:
        return
    # THE TAG FOLLOWS THE CONCEPT; THE FACE MEMBERSHIP DOES NOT FOLLOW A NOTE ROW. A row printed
    # inside a note is not a face row whatever concept a reviewer maps it to — the same test
    # ``segment_source`` applies, and for the same reason: a note's lines sum to a figure the face
    # already reports, so counting one as a face row puts the note's money in the section twice,
    # once through the note and once through the row. The queue does offer a re-map on such a row
    # (an unmapped card is raised for any row), so this is reachable and not theoretical.
    if (row.get("printed_in") or "") == PrintedIn.NOTES.value:
        return
    for seg in store.get("segments") or []:
        ids = seg.get("face_item_ids") or []
        if row_id in ids:
            seg["face_item_ids"] = [i for i in ids if i != row_id]
    # The measurement of what nothing could place is membership too, and it leaves with the rest: a
    # row a human has just placed is not a row nothing placed. Left behind it would be counted as
    # uncovered by the index AND served with ``unresolved: true`` under the row's new section, so
    # the analyst's own correction would read on screen as having failed. The unmapped branch below
    # puts it back.
    store["unresolved_face_item_ids"] = [i for i in (store.get("unresolved_face_item_ids") or [])
                                         if i != row_id]
    if bucket:
        for seg in store.get("segments") or []:
            if seg.get("bucket") == bucket:
                seg["face_item_ids"] = list(seg.get("face_item_ids") or []) + [row_id]
                if section and section not in (seg.get("sections") or []):
                    seg["sections"] = sorted((seg.get("sections") or []) + [section])
    else:
        # Unmapped: it belongs to Others, and to the measurement that says Others holds it because
        # nothing placed it rather than because it belongs there.
        for seg in store.get("segments") or []:
            if seg.get("bucket") == "others":
                seg["face_item_ids"] = list(seg.get("face_item_ids") or []) + [row_id]
        store["unresolved_face_item_ids"] = list(
            store.get("unresolved_face_item_ids") or []) + [row_id]
    result["buckets"] = store


class LineItemEdit(BaseModel):
    value: float | None = None
    formula: str = ""
    period: str = "current"
    # Which set of figures is being edited. Without it every edit landed on the consolidated
    # column, so an analyst working the standalone statement saw nothing change.
    basis: str = "consolidated"
    # WHY the figure was changed. A manual value overrides what the document says and what the
    # template computes, so the reason it was overridden is part of the record — it travels with
    # the row, into the export, and to whoever reviews the spread after the analyst.
    comment: str = ""


def _template_concept(template_def: dict | None, canonical_key: str,
                      locale: str = "en") -> tuple[str, str] | None:
    """``(label, role)`` for a canonical key the template defines, else None."""
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            for c in sec.get("children") or []:
                if c.get("canonical_key") == canonical_key:
                    label = (c.get("label_i18n") or {}).get(locale) or c.get("label") \
                        or canonical_key
                    return label, (c.get("role") or "line")
    return None


def _slot_id(basis: str, period: str) -> str:
    return f"{basis}/{period}"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.patch("/{document_id}/line-items/{canonical_key:path}",
              dependencies=[Depends(require(Permission.EXTRACTION_EDIT)), Depends(authorized_document)])
def edit_document_line_item(document_id: str, canonical_key: str, body: LineItemEdit,
                            session: Session = Depends(db),
                            principal: Principal = Depends(current_principal)) -> dict:
    """Edit one figure of a real extraction: a concept, in one basis, for one period.

    Three cases, all of which an analyst hits in the grid, and all of which used to fail
    silently:

    * the concept was extracted once — the ordinary case, the value is overlaid on that row;
    * SEVERAL printed lines map to the concept (a section's "Others", three depreciation
      lines) and the grid shows their sum — a typed figure then REPLACES that sum rather than
      joining it, so what was entered is what appears;
    * the template defines the line but the document never yielded it (a blank row in the
      grid) — the figure is recorded as a manual entry against that concept instead of 404ing.

    The overlay is persisted onto the latest run, so the statement, export, checks and review
    all read it; the machine-extracted numbers are snapshotted first so a revert is exact.
    """
    from app.db.models import Document

    if session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    if body.basis not in ("consolidated", "standalone"):
        raise HTTPException(status_code=422, detail=f"Unknown basis '{body.basis}'")
    result = dict(run.result)
    rows = result.get("rows", [])
    group = [r for r in rows if r.get("canonical_key") == canonical_key]
    if group:
        # The whole concept's manual figure lives on ONE row — its first, in run order — which
        # is the row the statement view and the export look to for an override. Every other
        # contributing line keeps its printed figure, so the composition stays auditable.
        target = group[0]
    else:
        concept = _template_concept(_template_for_run(session, run), canonical_key)
        if concept is None:
            raise HTTPException(
                status_code=404,
                detail=f"'{canonical_key}' is neither in this extraction nor in its template")
        label, role = concept
        target = {"canonical_key": canonical_key, "source_label": label, "role": role,
                  "values": [], "mapping_method": "manual", "mapping_confidence": None,
                  # Entered by hand rather than read off the page: a revert removes the row
                  # entirely instead of restoring a figure that never existed.
                  "manual": True}
        rows.append(target)

    values = target.setdefault("values", [])
    # Snapshot the machine-extracted values ONCE, before this concept's first edit, so a revert
    # restores the original numbers exactly (edits are overlays, never a lossy overwrite). The
    # WHOLE list is copied rather than a (basis, period) → value map: a row can legitimately hold
    # two values that share a basis and period label, and a map would collapse them and hand both
    # the same figure back on revert.
    if not target.get("edited"):
        target["_original"] = copy.deepcopy(values)

    # The slot the grid shows for this (basis, period) — matched by the period it names, so an
    # edit to the prior column cannot land on the current one (or vice versa).
    slot = slot_for(target, body.basis, body.period)
    if slot is None:
        slot = {"period_label": body.period, "basis": body.basis, "value": None,
                "provenance": None}
        values.append(slot)

    # A formula drives the value: evaluate it against the other line items (same basis and
    # period), so a real formula shows its computed result. If a formula is given alongside an
    # explicit value and can't be evaluated (e.g. free-form references), the explicit value
    # stands and the formula is kept as an annotation. A formula-ONLY edit that can't evaluate
    # is a 422.
    computed: float | None = None
    formula_error: str | None = None
    if body.formula and body.formula.strip().lstrip("=").strip():
        from app.services.formula import FormulaError, evaluate

        def _resolve(name: str) -> float:
            # Resolve a reference the way the grid renders it: the concept's figure, summed
            # across every printed line that maps to it (or its own manual override).
            n = _concept_value([r for r in rows if r.get("canonical_key") == name],
                               body.basis, body.period)
            if n is None:
                raise KeyError(name)
            return n

        try:
            computed = evaluate(body.formula, _resolve)
        except FormulaError as exc:
            formula_error = str(exc)

    if computed is not None:
        new_val = computed
    elif body.value is not None:
        new_val = body.value            # explicit value stands; formula kept as annotation
    elif formula_error is not None:
        raise HTTPException(status_code=422,
                            detail={"error": "bad_formula", "message": formula_error})
    else:
        new_val = None
    if new_val is None:
        slot["value"] = None
    else:
        fv = float(new_val)
        slot["value"] = str(int(fv)) if fv == int(fv) else str(fv)
    slot.setdefault("basis", body.basis)
    target["formula"] = body.formula or None
    target["edited"] = True
    slot_id = _slot_id(body.basis, body.period)
    # Which figures were typed, so an edit to one basis doesn't claim the other.
    target["edited_slots"] = sorted(set(target.get("edited_slots") or []) | {slot_id})
    comments = dict(target.get("edit_comments") or {})
    if body.comment.strip():
        comments[slot_id] = {"text": body.comment.strip()[:2000],
                             "by": getattr(principal, "username", "") or "",
                             "at": _now_iso()}
    else:
        # Clearing the box removes the note rather than leaving a stale reason on a new figure.
        comments.pop(slot_id, None)
    if comments:
        target["edit_comments"] = comments
    else:
        target.pop("edit_comments", None)

    result["rows"] = rows
    run.result = result
    flag_modified(run, "result")
    session.commit()
    return {"ok": True, "canonical_key": canonical_key, "basis": body.basis,
            "period": body.period, "value": slot["value"], "formula": target.get("formula"),
            "status": "edited", "label": target.get("source_label") or "",
            "comment": (target.get("edit_comments") or {}).get(slot_id, {}).get("text", ""),
            # What the grid will now show for this concept, so the caller can confirm the
            # figure it typed is the figure that took effect.
            "current": _concept_value(group or [target], body.basis, "current"),
            "prior": _concept_value(group or [target], body.basis, "prior"),
            "combined_from": len(group) if len(group) > 1 else 0}


@router.delete("/{document_id}/line-items/{canonical_key:path}",
               dependencies=[Depends(require(Permission.EXTRACTION_EDIT)), Depends(authorized_document)])
def revert_document_line_item(document_id: str, canonical_key: str,
                              session: Session = Depends(db)) -> dict:
    """Revert an edited line item to its original machine-extracted values, dropping the
    manual value(s) and formula. A line that only ever existed as a manual entry is removed."""
    from app.db.models import Document

    if session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    result = dict(run.result)
    rows = result.get("rows", [])
    target = next((r for r in rows
                   if r.get("canonical_key") == canonical_key and r.get("edited")), None)
    if target is None:
        target = next((r for r in rows if r.get("canonical_key") == canonical_key), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Line item not found in this run")
    if not target.get("edited"):
        return {"ok": True, "canonical_key": canonical_key, "reverted": False, "status": None}

    if target.get("manual"):
        # Nothing to restore: the line came from the analyst, not the document.
        rows = [r for r in rows if r is not target]
    else:
        original = target.get("_original")
        if isinstance(original, dict):
            # Runs edited before the snapshot was a full list: restore by period label alone.
            for v in target.get("values") or []:
                if v.get("period_label") in original:
                    v["value"] = original[v["period_label"]]
        elif original is not None:
            # Restore the machine-extracted values wholesale. Slots that exist only because an
            # edit created them are not in the snapshot and go away with the edit.
            target["values"] = copy.deepcopy(original)
        target.pop("_original", None)
        target.pop("formula", None)
        target["edited"] = False
        target.pop("edited_slots", None)
        target.pop("edit_comments", None)

    result["rows"] = rows
    run.result = result
    flag_modified(run, "result")
    session.commit()
    return {"ok": True, "canonical_key": canonical_key, "reverted": True, "status": None}


@router.get("/{document_id}/export",
            dependencies=[Depends(require(Permission.EXPORT_RUN)), Depends(authorized_document)])
def export_document(
    document_id: str,
    fmt: str = Query("excel", pattern="^(excel|json)$"),
    layout: str = Query("statement", pattern="^(statement|flat)$"),
    locale: str = Query("en"),
    include: str | None = Query(None),
    units: str | None = Query(None),
    session: Session = Depends(db),
) -> Response:
    """Export a real document's extracted, mapped line items as Excel or JSON, built from
    the latest run (not the demo project).

    Excel supports two layouts: ``statement`` (default) mirrors the run's template —
    sections, subtotals, totals, ordering, localized labels, consolidated + standalone
    side by side; ``flat`` is one row per line item. ``include`` is a comma-separated set of
    optional analysis sheets to add (note_details, ratios, disclosures) — omit for all. JSON
    carries the line items with formulas plus a derived-analysis block."""
    from app.db.models import Document
    from app.services.export import (
        build_rows_json, build_rows_xlsx, build_statement_workbook, units_scale,
    )

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    rows = run.result.get("rows", [])
    name = (doc.filename or "extract").rsplit(".", 1)[0]
    include_set = ({p.strip() for p in include.split(",") if p.strip()}
                   if include is not None else None)
    # Present in a chosen unit only when the source scale was detected (never guess).
    src_units = run.result.get("units")
    scale, unit_label = units_scale(src_units, units)
    ccy = (src_units or {}).get("currency")
    caption = (f"Amounts in {ccy + ' ' if ccy else ''}{unit_label}" if unit_label else None)
    narrative = run.result.get("credit_narrative")  # stored LLM narrative, if generated
    # Resolved BEFORE the format branch so both the workbook and the JSON carry it, and read through
    # the same `_coverage_block` the review screen is served — a second computation here is how the
    # sheet and the queue would come to disagree about whether anything was checked.
    export_template = _template_for_run(session, run)
    coverage = _coverage_block(run, export_template, locale)
    if fmt == "json":
        data = build_rows_json(rows, filename=doc.filename or "document",
                               disclosures=run.result.get("disclosures", []),
                               note_details=run.result.get("note_details", []),
                               reconciliation=run.result.get("reconciliation", []), locale=locale,
                               credit_narrative=narrative,
                               netting_rules=run.result.get("netting") or [],
                               coverage=coverage, template_def=export_template)
        return Response(content=data, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})

    template_def = export_template
    if layout == "statement" and template_def:
        data = build_statement_workbook(rows, template_def, locale=locale,
                                        filename=doc.filename or "document",
                                        disclosures=run.result.get("disclosures", []),
                                        note_details=run.result.get("note_details", []),
                                        reconciliation=run.result.get("reconciliation", []),
                                        include=include_set, scale=scale, units_caption=caption,
                                        credit_narrative=narrative, coverage=coverage)
    else:
        data = build_rows_xlsx(rows, filename=doc.filename or "document", scale=scale)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


_PAGE_CLS = {"face": "Statement face", "notes": "Notes", "other": "Other",
             "cover": "Cover", "toc": "Contents", "unknown": "Unclassified"}


def _conf_cat(c) -> tuple[str, int | None]:
    """A measured confidence as (badge category, MEASURED percentage).

    The percentage is ``None`` when there is no confidence to report, and callers serve that absence
    rather than a number. It used to be 60 — a figure derived from nothing, standing where the
    measured one belongs: a page with no classification confidence was served as 'med', and the
    client's per-CATEGORY literal then printed "78%" over it. A category is a bucket of a
    measurement, so it can never be printed as one.
    """
    if not isinstance(c, (int, float)):
        return "med", None
    pct = round(c * 100)
    return ("high" if c >= 0.85 else "med" if c >= 0.7 else "low"), pct


def _default_scope(pages: list[dict]) -> set[int]:
    """Pages extraction would target by default — the classified face/notes pages."""
    return {(p.get("index", 0) or 0) for p in pages if p.get("kind") in ("face", "notes")}


def _build_pages(pages: list[dict], scope: list[int] | None = None) -> dict:
    """PagesResponse from the document's real classified pages. Inclusion reflects the user's
    persisted scope when set; otherwise the default (face/notes are in scope, the rest are
    skipped)."""
    chosen = set(scope) if scope is not None else _default_scope(pages)
    cards = []
    for p in pages:
        kind = p.get("kind", "unknown")
        idx = p.get("index", 0) or 0
        included = idx in chosen
        cat, pct = _conf_cat(p.get("classification_confidence"))
        cards.append({
            "no": idx + 1,
            "kind": normalise_kind(kind),
            "cls": _PAGE_CLS.get(kind, kind.title()),
            "sub": "in scope" if included else "skipped",
            "conf": cat,
            # THE MEASURED PERCENTAGE, SERVED BESIDE THE CATEGORY. The classifier's score was
            # computed here and thrown away (`cat, _ = …`), so the screen printed a literal per
            # CATEGORY instead — a page scored 0.40 rendered "54%" and a page with no score at all
            # rendered "78%". `null` where the classifier reported nothing: the screen has to say so
            # rather than print a number, and it cannot say so if the payload does not distinguish.
            "conf_pct": pct,
            "included": included,
            "scan": "scanned" if p.get("source_kind") == "scanned" else "native",
            # The page's own printed folio, when it prints one. `no` above is the position in the
            # FILE, which is what every index in this system means and what the viewer navigates
            # by; this is the number the reader sees on the page. They differ by however much front
            # matter the report has, which is why showing only one of them misleads.
            "printed": p.get("printed_page"),
        })
    # Counted from the cards, by the same helper the sample route uses — see app/services/
    # page_scope.py for why the two routes are no longer allowed their own arithmetic.
    return {"pages": cards, **scope_counts(cards)}


def _document_pages(row, store: LocalObjectStore) -> list[dict]:
    """The document's classified pages — the copy persisted at upload, recomputing (and
    back-filling) only for legacy rows that predate page persistence."""
    if row.pages:
        return row.pages
    doc_model, _ctx = analyze_document(store.get(row.object_key), filename=row.filename or "")
    return [p.model_dump(mode="json") for p in doc_model.pages]


@router.get("/{document_id}/pages", dependencies=[Depends(authorized_document)])
def get_document_pages(
    document_id: str,
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> dict:
    """Real per-page classification for the Page Scope screen. Served from the pages captured
    at upload (no recompute), with inclusion reflecting any persisted user scope."""
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _build_pages(_document_pages(row, store), row.page_scope)


class ScopeEdit(BaseModel):
    included_pages: list[int]


@router.put("/{document_id}/scope",
            dependencies=[Depends(require(Permission.PIPELINE_RUN)), Depends(authorized_document)])
def set_document_scope(
    document_id: str,
    body: ScopeEdit,
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> dict:
    """Persist the user's page selection for extraction (the Page Scope screen). Extraction
    then restricts itself to these pages. Indices are validated against the document's pages;
    an empty selection resets to the default (all face/notes pages)."""
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    pages = _document_pages(row, store)
    valid = {(p.get("index", 0) or 0) for p in pages}
    chosen = sorted({i for i in body.included_pages if i in valid})
    # Empty (or a selection matching the default) → clear the override so the default applies.
    row.page_scope = chosen or None
    if row.pages is None:
        row.pages = pages          # back-fill persistence for legacy rows
    session.commit()
    return {"ok": True, "document_id": document_id,
            "included_pages": chosen, "count": len(chosen)}


# Fallback chrome for the reference statement types. The real label + canonical-key prefix
# are derived from the run's template (see _stmt_label/_stmt_prefix), so a template that adds
# a fourth statement (e.g. changes in equity) renders without code changes; these maps only
# apply when the template omits a label or can't be resolved.
_STMT_PREFIX = {"balance_sheet": "bs", "profit_and_loss": "pl", "cash_flow": "cf"}
_STMT_LABEL = {"balance_sheet": "Balance sheet", "profit_and_loss": "Profit & loss",
               "cash_flow": "Cash flow"}


def _stmt_node(template_def: dict | None, statement_type: str) -> dict | None:
    return next((s for s in (template_def or {}).get("statements", [])
                 if s.get("type") == statement_type), None)


# Why a statement is serving no rows, keyed by the code the payload publishes so the code and the
# sentence cannot drift apart.
_STATEMENT_REFUSALS = {
    "statement_not_in_template":
        "This run's template declares no statement of this type, so none is shown. Extracted "
        "figures that reach no statement are reported in the review queue; re-extract against a "
        "template that declares it to see it here.",
    "template_declares_no_lines":
        "This run's template declares this statement but no line items under it, so there is "
        "nothing to render. Fix the template and re-extract; the figures themselves are unaffected.",
    "basis_not_extracted":
        "No figures were extracted for this statement on this basis. Check the page scope, or the "
        "other basis.",
}


def _statement_refusal(reason: str, locale: str) -> dict:
    """Why a statement is serving no rows, as a machine-readable code and the reader's sentence.

    THE EMPTY GRID IS NOT SELF-EXPLANATORY, and it has two causes that need telling apart: the run's
    template declares no statement of this type at all, or it declares one and nothing was extracted
    for the requested basis. "Nothing here" reads as "the extraction found nothing", which sends an
    analyst back to the document after figures that were never going to be shown.

    ``viewer.callout`` carries the same sentence and is NOT a second spelling of it: the callout is
    defined as the one line of prose a spread prints about itself, and when the spread is refused the
    refusal is that line. This block exists because only one of the Workspace's three viewer branches
    renders the callout — the paper preview, which a real uploaded PDF or workbook never takes — so a
    client showing the grid area has to be able to read the reason where the rows would have been.

    An unrecognised reason is a ``KeyError`` rather than a blank message: a refusal that cannot say
    why is the very state this exists to remove.
    """
    return {"reason": reason, "message": _t(_STATEMENT_REFUSALS[reason], locale)}


def _template_skeleton(stmt: dict | None) -> list[tuple[str, dict]]:
    """One statement's skeleton as the grid lays it out: ``("section", node)`` headings and
    ``("line", node)`` figure rows, in template order.

    A section with keyed children is a HEADING followed by those children. A section with none is
    itself a single figure row — that is how the template declares a statement-level total (gross
    profit, profit before tax, total assets, net assets, closing cash), and skipping those left a
    P&L with no "Profit for the year" on it.

    ONE walk, because the grid and the review queue ask the same question from opposite ends: the
    grid emits these keys and nothing else, and the queue raises a finding for every mapped row
    whose key is not among them. Two walks would let a key count as declared for one and undeclared
    for the other — the grid dropping a figure the queue never reported, which is precisely the
    silent disappearance this layer exists to prevent.
    """
    out: list[tuple[str, dict]] = []
    for sec in (stmt or {}).get("sections") or []:
        children = [c for c in sec.get("children") or [] if c.get("canonical_key")]
        if not children:
            if sec.get("canonical_key"):
                out.append(("line", sec))
            continue
        out.append(("section", sec))
        out.extend(("line", c) for c in children)
    return out


# Statement types served as a MATRIX — component columns, movement rows — rather than as the
# template's skeleton. ONE list, because three places turn on the same fact and any disagreement
# between them loses a figure: `_build_statement` dispatches these to `_build_matrix_statement`,
# `_declared_line_keys` must not count their template lines as grid keys (the matrix renders none of
# them), and `_rows_on_a_matrix_face` exempts the rows the matrix does show.
_MATRIX_STATEMENTS = ("changes_in_equity",)


def _rows_on_a_matrix_face(rows: list[dict], template_def: dict | None) -> set[int]:
    """Positions of rows that a MATRIX statement shows, when the template declares one.

    The off-template finding says a mapped row "appears on no spread", and for these rows that would
    be false: a matrix renders its movements as printed, so a movement whose concept the template
    puts on no LINE still reaches a face. Left out, one payload would render a row on the equity
    matrix and simultaneously report it as reaching nothing — the self-contradiction the shared
    ``_template_skeleton`` walk exists to prevent, arriving by the one door that walk cannot cover,
    because a matrix has no skeleton to walk.

    Identity rather than equality: two movements can print the same caption and the same figures, and
    ``_matrix_rows`` hands back the very dicts it filtered out of ``rows``.
    """
    if not any(_stmt_node(template_def, t) for t in _MATRIX_STATEMENTS):
        return set()
    shown = {id(r) for basis in ("consolidated", "standalone")
             for r, _cells in _matrix_rows(rows, basis)}
    return {i for i, r in enumerate(rows) if id(r) in shown}


def _declared_line_keys(template_def: dict | None) -> set[str]:
    """Every canonical key this template puts on a statement GRID, across all of its statements.

    Deliberately NOT ``_remap_targets``: that list excludes calculated subtotals and headers because
    a printed figure must not be written ONTO them, which is a different question from whether the
    template declares the line at all. A row mapped to ``bs_total_assets`` does reach the grid, so it
    is not off-template — treating it as one would raise a finding against a row the reader can see.

    And deliberately NOT every declared node either: a matrix statement's lines are skipped, because
    ``_build_matrix_statement`` renders movement rows and never consults the skeleton. Counting them
    made a row mapped to a declared equity line reach no face and raise no finding at once — declared
    enough to be silent, rendered nowhere.
    """
    return {node["canonical_key"]
            for stmt in (template_def or {}).get("statements", [])
            if stmt.get("type") not in _MATRIX_STATEMENTS
            for kind, node in _template_skeleton(stmt) if kind == "line"}


def _stmt_prefix(template_def: dict | None, statement_type: str) -> str:
    """The canonical-key prefix for a statement (e.g. 'bs'), read from the template's own
    keys so it isn't limited to the three reference statements."""
    node = _stmt_node(template_def, statement_type)
    if node:
        for sec in node.get("sections", []):
            for c in sec.get("children") or []:
                k = c.get("canonical_key") or ""
                if "_" in k:
                    return k.split("_", 1)[0]
    if statement_type in _STMT_PREFIX:
        return _STMT_PREFIX[statement_type]
    return statement_type.split("_", 1)[0] if statement_type else ""


def _stmt_label(template_def: dict | None, statement_type: str, locale: str) -> str:
    """The statement's display label in the output locale — from the template node's
    label_i18n/label, falling back to the reference labels then a humanized type."""
    node = _stmt_node(template_def, statement_type)
    if node:
        lbl = (node.get("label_i18n") or {}).get(locale) or node.get("label")
        if lbl:
            return lbl
    if statement_type in _STMT_LABEL:
        return _t(_STMT_LABEL[statement_type], locale)
    return statement_type.replace("_", " ").title()


def _to_num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _basis_values(r: dict, basis: str) -> list[dict]:
    """Values for the requested basis; values with no basis are treated as consolidated."""
    return _basis_values_of(r, basis)


def _cur_prior(r: dict, basis: str = "consolidated") -> tuple[dict | None, dict | None]:
    """This row's current- and prior-period values. A period the row has no figure for stays
    None — see services.periods for why the positional fallback must not fire here."""
    return split_current_prior(_basis_values(r, basis))


def _inspector(r: dict, cur: dict | None) -> dict:
    prov = (cur or {}).get("provenance")
    # A figure the pipeline INFERRED must not read as one it matched. The row carrying a subtotal's
    # sole component (stages.map_ontology) has method "rule", so "Mapped by rule" would tell the
    # analyst a caption on the page said "Current tax" when no such caption exists — the figure is
    # the total, filed here because nothing evidenced a sibling. Say that instead.
    flags = ((cur or {}).get("confidence") or {}).get("flags") or []
    inferred = next((f.split(":", 1)[1] for f in flags
                     if f.startswith("inferred_sole_component:")), "")
    return {"tag": "inferred" if inferred else "machine",
            "src": _prov_label(prov) if prov else "",
            "formula": "", "result": str((cur or {}).get("value") or ""),
            "note": (f"Inferred: the face printed {inferred} alone and no sibling component was "
                     "evidenced on the face or in its note"
                     if inferred else f"Mapped by {r.get('mapping_method') or 'ensemble'}")}


def _ontology_for_run(session: Session, run):
    """The RESOLVED rulebook this run was launched against, or None when it named none.

    Resolved, because the section layer a v2 rulebook authors once per section only reaches a
    concept through the fold — and the section is exactly what a re-map has to read.
    """
    from app.db.models import OntologyVersion
    from app.schemas.loader import load_ontology

    oid = (run.options or {}).get("ontology_version_id")
    row = session.get(OntologyVersion, oid) if oid else None
    if row is None:
        return None
    try:
        return load_ontology(row.definition, resolve=True)
    except Exception:  # noqa: BLE001 — a malformed rulebook must not break a re-map
        return None


def _netting_rules_for_run(session: Session, run) -> list:
    """The face-line netting rules from the ontology the run used (empty when none/unavailable)."""
    from app.db.models import OntologyVersion
    from app.schemas.loader import load_ontology

    oid = (run.options or {}).get("ontology_version_id")
    row = session.get(OntologyVersion, oid) if oid else None
    if row is None:
        return []
    try:
        return load_ontology(row.definition, resolve=True).netting_rules
    except Exception:  # noqa: BLE001 — a malformed ontology must not break the statement
        return []


def _template_for_run(session: Session, run) -> dict | None:
    """The template THIS RUN was launched against, or None. There is no fallback.

    The id comes from ``_run_template_id`` so this and the coverage band cannot disagree about
    whether a template was attached at all.

    It used to fall back to the newest seeded ``TemplateVersion`` when the run named none, and the
    template is genuinely optional (``ExtractionOptions.template_version_id`` defaults to None and
    the upload screen allows it). So a run extracted with only an ontology served a coverage band
    reading {"available": false, "reason": "no_template"} directly above four
    TEMPLATE-DERIVED findings — two calculated_mismatch and two uncomputed, built from some other
    template's rollup children and node labels. That is the same self-contradiction inside one
    payload that ``_run_template_id`` was introduced to close, reached by the other half of the
    question: agreeing on how to READ the id is worth nothing while one reader answers "none" and
    the other substitutes a template the analyst never chose.

    Findings, labels and computed subtotals attributed to an unchosen template are worse than
    absent ones: nothing on the screen says which template they came from. Without an id the answer
    is None, and every caller already handles it — ``_calculated_checks`` returns [],
    ``_flip_sign_action`` refuses the button, the statement falls back to the extracted labels.
    """
    from app.db.models import TemplateVersion

    tid = _run_template_id(run)
    if not tid:
        return None
    tv = session.get(TemplateVersion, tid)
    return tv.definition if tv else None


def _superseded_template(session: Session, run) -> dict | None:
    """This run's PINNED template version against the newest one stored under the same key.

    Stated because a run cannot be un-pinned. ``_template_for_run`` resolves the template the run was
    launched against and there is no fallback, so a document extracted before a template revision
    goes on rendering the old shape for as long as that run is the latest one — which is exactly how
    a corrected line order still looks wrong on screen, and how a reader comparing two documents can
    be reading two different spreads without anything saying so. The two are indistinguishable
    otherwise: nothing else in the payload names the template's version.

    STATED, NOT ACTED ON. Re-extracting is a new run against the current template, and it discards
    every manual value, comment and re-map recorded on this one — a decision for the analyst, not a
    side effect of opening a statement. ``POST /documents/{id}/extractions`` already starts that run.

    ``latest_version`` is the highest version STORED for the key, not the highest ``is_published``
    one: ``routes/templates.py::_publish`` writes each new version with ``is_published`` defaulting
    to False and nothing ever flips it, so filtering on that flag would report a genuinely superseded
    run as current — the seeded reference template is the only published row in the table.

    None when the run named no template (nothing to be superseded) or when the pinned row is gone.
    """
    from sqlalchemy import func

    from app.db.models import TemplateVersion

    tid = _run_template_id(run)
    tv = session.get(TemplateVersion, tid) if tid else None
    if tv is None:
        return None
    latest = session.execute(
        select(func.max(TemplateVersion.version))
        .where(TemplateVersion.template_key == tv.template_key)
    ).scalar() or tv.version
    return {"superseded": int(latest) > int(tv.version), "run_version": int(tv.version),
            "latest_version": int(latest), "template_key": tv.template_key}


_BASIS_LABEL_I18N = {
    "consolidated": {"en": "Consolidated", "zh": "合并", "ar": "موحّد", "fr": "Consolidé"},
    "standalone": {"en": "Standalone", "zh": "单独", "ar": "مستقل", "fr": "Individuel"},
}


def _loc(node: dict, locale: str) -> str:
    """A template node's label in the output locale, from label_i18n (falls back to label)."""
    return (node.get("label_i18n") or {}).get(locale) or node.get("label") or ""


def _disp_period(lbl: str | None, idx: int, locale: str) -> str:
    """Display a period header: the source's own header (e.g. a year-end date like
    '31 Mar 2025') when it captured one; otherwise the generic Current/Prior label."""
    if not lbl or lbl in ("current",) or lbl.startswith("col"):
        return _t("Current" if idx == 0 else "Prior", locale)
    if lbl == "prior":
        return _t("Prior", locale)
    return lbl


def _period_labels(rows: list[dict], basis: str, locale: str) -> list[str]:
    """The two period-column headers for one basis of a statement's rows."""
    # A generator, so the early `break` inside still stops before touching every row.
    return _period_labels_from((_basis_values(r, basis) for r in rows), locale)


def _period_labels_from(value_lists: Iterable[list[dict]], locale: str) -> list[str]:
    """The two period-column headers, from the value lists whose figures sit under them. Uses the
    real headers the extractor captured (Excel carries the year/date text); falls back to
    Current/Prior (e.g. native PDF, where the column header date isn't yet detected).

    Taking the VALUE LISTS rather than rows-plus-basis is what lets the note-detail route reuse
    this: a note row's values are the same shape, and the header a note table prints above its
    figures has to be derived from those very figures. Two labellers would let the Notes screen
    and the Workspace label the same figures differently, which is exactly what "FY25"/"FY24"
    hardcoded above a 2023/2022 filing did.

    Each header is looked up by the period it NAMES, across every list, rather than taken
    positionally from the first one that carries any value at all — a row printed for one year
    only would otherwise label both columns with that year's period.
    """
    found: dict[str, str | None] = {}
    positional: list[str | None] | None = None
    for vals in value_lists:
        if not vals:
            continue
        for period, disp in period_displays(vals).items():
            found.setdefault(period, disp)
        if positional is None and not period_displays(vals):
            # A row whose columns are unnamed (col0/col1) still tells us the header order.
            positional = [(v.get("period_display") or v.get("period_label")) for v in vals]
        if "current" in found and "prior" in found:
            break
    if found:
        return [_disp_period(found.get("current"), 0, locale),
                _disp_period(found.get("prior"), 1, locale)]
    if positional:
        return [_disp_period(positional[0] if positional else None, 0, locale),
                _disp_period(positional[1] if len(positional) > 1 else None, 1, locale)]
    return [_t("Current", locale), _t("Prior", locale)]


# A two-column comparative labels its values positionally — "current", "prior", "col2" for a
# third numeric column. A matrix labels them with the column header text it read off the page
# ("Issued capital", "Retained profits"). That is the discriminator between the two shapes, and
# it needs no extra plumbing: the label already travels with every value.
_POSITIONAL_PERIOD = re.compile(r"^(current|prior|col\d+)$", re.IGNORECASE)
# A column header that reads as a period rather than a component. Excel carries the sheet's real
# header TEXT in period_label ("2023", "31 December 2023", "FY2024"), so without this every
# spreadsheet row looks like an equity movement with two component columns.
_PERIOD_LIKE = re.compile(r"(19|20)\d{2}|\bfy\b|\bq[1-4]\b|年", re.IGNORECASE)


def _is_named_column(label) -> bool:
    """Whether a value's column is NAMED — an equity component ("Retained profits") — as opposed
    to a period, whether that period was labelled positionally or by its own printed date.

    Delegates to services.periods so the Excel reader (which decides how to LABEL a column) and
    the statement builder (which decides how to READ one) cannot disagree.
    """
    return names_a_component(label)


def _matrix_cells(row: dict, basis: str) -> dict[str, object]:
    """This row's named component cells for one basis — empty for a comparative row."""
    out: dict[str, object] = {}
    for v in row.get("values") or []:
        if (v.get("basis") or "consolidated") != basis:
            continue
        if _is_named_column(v.get("period_label")):
            out[str(v["period_label"])] = v
    return out


def _matrix_rows(rows: list[dict], basis: str) -> list[tuple[dict, dict]]:
    """(row, named cells) for every row that is part of the matrix.

    A movement in equity always touches at least its own component and a total column, so two
    named cells is the floor — that keeps a stray one-column row out of the statement.
    """
    out = []
    for r in rows:
        cells = _matrix_cells(r, basis)
        if len(cells) >= 2:
            out.append((r, cells))
    return out


def _matrix_columns(rows: list[dict], basis: str) -> list[str]:
    """The component columns of a matrix statement, in the order they are PRINTED.

    Column identity is the header text extraction already attached to each value; the order is the
    ``column_index`` extraction recorded with it, because a dict of values has no left-to-right
    order of its own and equity statements are read left-to-right — issued capital through to total
    equity.

    The fallback is the page geometry (the median x of a column's cells), which is what every
    column carried before the index was persisted, and is still right for a source with no page
    axis at all — an Excel sheet, where each cell's box is null and the encounter order stands. It
    is used only when NO column carries an index: mixing the two scales would order some columns by
    position and the rest by pixels, which is not an order.
    """
    idx: dict[str, list[float]] = {}
    xs: dict[str, list[float]] = {}
    for _r, cells in _matrix_rows(rows, basis):
        for name, v in cells.items():
            ci = v.get("column_index")
            if ci is not None:
                idx.setdefault(name, []).append(float(ci))
            box = ((v.get("provenance") or {}).get("bbox")) or {}
            x = box.get("x0")
            xs.setdefault(name, []).append(0.0 if x is None else float(x))
    def median(vals: list[float]) -> float:
        vals = sorted(vals)
        return vals[len(vals) // 2] if vals else 0.0
    if len(idx) == len(xs):            # every column reported where it was printed
        return sorted(idx, key=lambda name: median(idx[name]))
    return sorted(xs, key=lambda name: median(xs[name]))


def _build_matrix_statement(rows: list[dict], statement_type: str, filename: str, *,
                            basis: str, locale: str, units_ctx: dict | None,
                            company: str | None, doc_format: str, page_count: int,
                            template_def: dict | None) -> dict:
    """A matrix statement: named component columns, one row per movement, in document order.

    Rows keep the caption as printed. A movement's identity in a statement of changes in equity
    IS its caption ("Profit for the year", "Dividends paid", "Acquisition of a subsidiary"), and
    there is no section skeleton to slot it into — so the printed order is the statement's order,
    and every parsed row appears. ``cells`` is keyed by column name; ``v1``/``v2`` stay null so
    nothing downstream mistakes a component for a period.

    THE STATEMENT IS THE UNIT OF THE TEMPLATE'S PERMISSION HERE, not the row. Everywhere else "no
    line the template does not define may reach a statement" is decided per line, because a
    comparative's rows ARE template line items. A matrix's rows are movements through the year, and
    the template declares no movements — so filtering row by row would have to invent a
    correspondence between "Acquisition of a subsidiary" and some template line that the template
    never states, and would delete the statement's real content on the strength of that invention.
    The one thing the template can honestly declare about a matrix is whether it carries the
    statement at all. So: declared, and every parsed movement shows as printed; not declared, and
    the statement is refused outright rather than rendering a face nothing asked for.

    Refused loudly and with nothing lost. The tab is out of the nav but the route, this builder and
    the ``StatementKey`` are all live, so a deep link still arrives here — it now arrives at an empty
    grid carrying ``refused``, which says why in a code and a sentence, instead of at rows no template
    declares. A movement row that mapped to no concept is already an ``unmapped`` finding in the
    review queue, with the re-map offer that resolves it, so refusing the face hides no figure.
    """
    declared = template_def is None or _stmt_node(template_def, statement_type) is not None
    columns = _matrix_columns(rows, basis) if declared else []
    out: list[dict] = []
    for r, raw_cells in (_matrix_rows(rows, basis) if declared else []):
        cells = {name: _to_num(v.get("value")) for name, v in raw_cells.items()}
        prov = next((v.get("provenance") for v in raw_cells.values() if v.get("provenance")), None)
        cat, pct = _conf_cat(r.get("mapping_confidence"))
        label = r.get("source_label") or ""
        out.append({
            "id": r.get("canonical_key") or f"eq:{len(out)}:{label[:40]}",
            "label": label, "source_label": label,
            # A row whose every figure lands in a "total" column is the statement's own subtotal.
            "kind": "subtotal" if _looks_like_equity_total(label) else "item",
            "note": r.get("note"), "note2": None, "status": None,
            "confidence": {"cat": cat, "pct": pct} if r.get("mapping_confidence") else None,
            "editable": False, "formula": None,
            "inspector": _inspector(r, {"value": next(iter(cells.values()), None),
                                        "provenance": prov}),
            "contributions": None, "cells": cells,
            # A matrix has no prior COLUMN — its columns are components — so there is no second
            # period provenance to carry.
            "v1": None, "v2": None, "source": prov, "source2": None,
        })

    # Why the matrix is empty, when it is — the same two-cause answer the comparative grid gives, for
    # the same reason: a blank grid reads as "the extraction found nothing" and sends the analyst
    # after movements that were never going to be shown. A declared statement with nothing
    # matrix-shaped on this basis is a filing/scope question, not a template one.
    refused = None if declared and out else _statement_refusal(
        "statement_not_in_template" if not declared else "basis_not_extracted", locale)
    basis_label = _t("Consolidated" if basis == "consolidated" else "Standalone", locale)
    return {
        "statement": statement_type,
        "label": _stmt_label(template_def, statement_type, locale),
        "basis": basis,
        # The shape the client must render. Absent/"comparative" is the two-column default.
        "layout": "matrix",
        "columns": [{"key": c, "label": c} for c in columns],
        "periods": [],
        "currency": (units_ctx or {}).get("currency") or "",
        "currency_symbol": "", "units": (units_ctx or {}).get("units_label") or "",
        "units_scale_factor": _to_num((units_ctx or {}).get("scale_factor")) or 1.0,
        "format": doc_format, "page_count": page_count,
        "rows": out,
        # Null when the statement is being served. See `_statement_refusal`.
        "refused": refused,
        "viewer": {
            "company": company or filename, "subtitle": _t("Extracted statement", locale),
            "chips": [{"label": basis_label, "active": True}],
            "callout": _t("Columns are equity components as printed, not periods. Click a row "
                          "to see it in the document.", locale) if refused is None
                       else refused["message"],
        },
    }


# A movement row whose caption is an opening/closing balance is the matrix's own subtotal line.
_EQUITY_TOTAL = re.compile(
    r"^\s*(at|as at|balance(s)? (at|as at))\b|^\s*(於|于|截至)", re.IGNORECASE)


def _looks_like_equity_total(label: str) -> bool:
    return bool(_EQUITY_TOTAL.search(label or ""))


def _equity_closing(rows: list[dict], basis: str) -> tuple[str, float, str] | None:
    """The equity statement's CLOSING total equity, as (caption, amount, name of the line).

    The last balance line in document order is the closing one — an equity statement runs
    opening balance, movements, closing balance, and a two-year statement simply does that
    twice. Returns None when the document carries no equity matrix at all.

    The third element is how the review queue NAMES the line this row is: its canonical_key when the
    matrix row mapped to one, else the caption as printed. It is returned rather than re-derived by
    the caller because this function is the only place that knows WHICH row was taken as the closing
    one — and the equity_tie card indicts it, so the header tile counting "lines with no finding" has
    to be able to exclude it. `_matrix_rows` FILTERS `rows`, so this row is one of them; the card
    used to assert otherwise and named only the balance sheet's total.
    """
    total_col = None
    last = None
    for r, cells in _matrix_rows(rows, basis):
        if total_col is None:
            total_col = next((c for c in cells if "total equity" in c.lower()), None)
        if total_col and _looks_like_equity_total(r.get("source_label") or ""):
            v = _to_num((cells.get(total_col) or {}).get("value"))
            if v is not None:
                last = (r.get("source_label") or "", v,
                        str(r.get("canonical_key") or r.get("source_label") or ""))
    return last


# A computed subtotal and the printed one are read from the same page in the same units, so any
# real difference is a whole currency unit or more. Below that it is float noise from the sum.
_CALC_TOLERANCE = 0.5


def _component_value(calc: dict, owner_key: str, index: int):
    """One component's figure for the period `calc` was evaluated for.

    Read from that period's own evaluation rather than from another period's component list: a
    Calculated is evaluated for ONE period, so reusing its figures would print this year's
    components under last year's total.
    """
    own = calc.get(owner_key)
    if own is not None and index < len(own.components):
        return own.components[index].value
    return None


_CALC_NOTES = {
    "calculated": "Computed from the {n} template lines below, each of which clicks through to "
                  "the page it was printed on. The document's own printed figure is held for "
                  "review, not shown here — a subtotal that contradicts its components is a "
                  "finding rather than a number.",
    "manual": "A value entered by hand, which stands over the computed one. The components below "
              "are what the template says this line is made of.",
    # NOT "it is in the review queue", which it no longer is: a subtotal with no components is a
    # coverage fact rather than a defect, so it raises no card (`_calculated_checks`). This note and
    # the coverage band's unevaluated relation are where the reader is told, so the sentence has to
    # carry the whole of it — say only "unverified" here and nothing anywhere says why.
    "reported_uncomputed": "None of the components this line is made of were extracted, so there "
                           "was nothing to compute from and the document's printed figure is "
                           "shown unverified. Nothing disagrees with it — there was nothing to "
                           "disagree — so it raises no review item; check it against the page.",
}


def _calculated_note(origin: str, diff: float | None, n_components: int, locale: str) -> str:
    note = _t(_CALC_NOTES[origin], locale).replace("{n}", str(n_components))
    if diff is not None and abs(diff) > _CALC_TOLERANCE:
        note = f"{note} {_t('The printed figure differs by', locale)} {diff:,.0f}."
    return note


def _calculated(rows: list[dict], template_def: dict | None, basis: str, period: str,
                locale: str, netted: dict[str, float] | None = None) -> dict:
    """Evaluate the template's calculated lines for one (basis, period).

    Inputs are read through ``concept_value``, so a calculated line is built from exactly the
    figures the grid shows for its components — including an analyst's manual correction to one of
    them, and any netting restatement, which is the point: whatever a component shows is what its
    subtotal is built from.

    A pass-through to ``rollups.evaluate_rows``, which the export and the KPI layer also call. The
    plumbing used to be written out again here; two spellings of "what a subtotal is" is how the
    screen and everything reading beside it come to differ.
    """
    from app.services.rollups import evaluate_rows

    return evaluate_rows(template_def, rows, basis, period, locale, netted=netted)


_KPI_CATEGORY_I18N = {
    "Liquidity": {"zh": "流动性", "ar": "السيولة", "fr": "Liquidité"},
    "Leverage": {"zh": "杠杆", "ar": "الرافعة المالية", "fr": "Endettement"},
    "Coverage": {"zh": "偿付能力", "ar": "التغطية", "fr": "Couverture"},
    "Efficiency": {"zh": "运营效率", "ar": "الكفاءة", "fr": "Efficacité"},
    "Profitability": {"zh": "盈利能力", "ar": "الربحية", "fr": "Rentabilité"},
}


def _key_provenance(rows: list[dict], basis: str) -> dict[str, tuple[dict | None, dict | None]]:
    """canonical key → (current, prior) provenance of the first line that carried it, so a
    derived figure can still be traced to the page its inputs were printed on."""
    out: dict[str, tuple[dict | None, dict | None]] = {}
    for r in rows:
        k = r.get("canonical_key")
        # Skip a row carrying nothing for THIS basis: taking the first row with the key regardless
        # left a consolidated-only line with no location on the standalone view, so the KPI inputs
        # advertised a click-through they could not deliver.
        if not k or k in out or not _basis_values(r, basis):
            continue
        cur, prior = _cur_prior(r, basis)
        out[k] = ((cur or {}).get("provenance"), (prior or {}).get("provenance"))
    return out


def _kpi_inputs_as_contributions(inputs: dict, prior_inputs: dict, provs: dict,
                                 locale: str) -> list[dict]:
    """A ratio's inputs in the same shape as a combined line's contributions, so the inspector
    shows the arithmetic with each figure clickable through to where it was printed."""
    out: list[dict] = []
    for side, sign_label in (("numerator", _t("numerator", locale)),
                             ("denominator", _t("denominator", locale))):
        # Matched by POSITION in the term list, not by resolved key: a term may name several
        # candidate keys and resolve to a different one in each period (cost of goods sold this
        # year, total operating cost last year), and keying on the current period's answer then
        # reported the prior input as absent even though the prior ratio used it.
        prior_side = list(prior_inputs.get(side) or [])
        for pos, i in enumerate(inputs.get(side) or []):
            key = str(i.get("canonical_key") or "")
            cur_prov, prior_prov = provs.get(key, (None, None))
            sign = i.get("sign") or 1
            pv = prior_side[pos].get("value") if pos < len(prior_side) else None
            out.append({
                "label": f"{sign_label}: {i.get('label') or ''}",
                "canonical_key": key or None,
                "v1": None if i.get("value") is None else sign * float(i["value"]),
                "v2": None if pv is None else sign * float(pv),
                "method": None,
                # An input the filing never reported: shown, and shown as absent.
                "residual": i.get("value") is None,
                "src": _prov_label(cur_prov), "source": cur_prov,
                "src2": _prov_label(prior_prov), "source2": prior_prov,
            })
    return out


def _build_kpi_statement(rows: list[dict], filename: str, *, basis: str, locale: str,
                         company: str | None, doc_format: str, page_count: int,
                         template_def: dict | None) -> dict:
    """The KPI view — the ratio catalog computed from THIS extraction, current beside prior.

    A ratio is not a currency amount: it carries its own unit (×, %, days) and is untouched by
    the presentation currency or magnitude an analyst picks for the statements. So every figure
    ships a formatted ``display`` string beside the raw number and the grid renders that
    verbatim, instead of scaling a current ratio of 1.35 into "thousands".

    Ratios whose inputs the filing never reported are listed as unavailable rather than dropped:
    which KPIs a document cannot support is itself a finding.
    """
    from app.services.derived import compute_ratios

    # The template's own KPI block is the catalog when it declares one — passing it is what makes a
    # declared KPI reach this view at all, and without it the block would be an inert declaration.
    cur = compute_ratios(rows, basis=basis, period="current", locale=locale,
                         template_def=template_def)
    prior = {r["key"]: r for r in compute_ratios(rows, basis=basis, period="prior", locale=locale,
                                                 template_def=template_def)}
    provs = _key_provenance(rows, basis)

    out: list[dict] = []
    seen_cat: set[str] = set()
    for ratio in cur:
        cat = ratio.get("category") or "Profitability"
        if cat not in seen_cat:
            seen_cat.add(cat)
            out.append({"id": f"kpi_sec_{cat.lower()}",
                        "label": _KPI_CATEGORY_I18N.get(cat, {}).get(locale, cat),
                        "kind": "section", "v1": None, "v2": None})
        p = prior.get(ratio["key"], {})
        contributions = _kpi_inputs_as_contributions(
            ratio.get("inputs") or {}, p.get("inputs") or {}, provs, locale)
        out.append({
            "id": f"kpi_{ratio['key']}", "label": ratio["label"], "source_label": None,
            "kind": "item", "note": None, "note2": None,
            "status": None if ratio.get("available") else "missing",
            "confidence": None,
            # Derived, not extracted: there is no single figure on a page to correct, so the fix
            # for a wrong ratio is to fix the line items it is computed from.
            "editable": False,
            "formula": ratio.get("formula"),
            "inspector": {
                "tag": _t("computed", locale) if ratio.get("available")
                       else _t("inputs not extracted", locale),
                "src": "", "formula": ratio.get("formula") or "",
                "result": ratio.get("display") or "",
                "note": _t("Computed from the extracted line items below — each one clicks "
                           "through to the page it was printed on. Correct a KPI by correcting "
                           "its inputs.", locale),
            },
            "contributions": contributions or None,
            "v1": ratio.get("value"), "v2": p.get("value"),
            # Pre-formatted with the ratio's own unit; the grid shows these instead of applying
            # the statements' currency/magnitude presentation.
            "display1": ratio.get("display") or "—", "display2": p.get("display") or "—",
            "source": None, "source2": None,
        })

    return {
        "statement": "kpi", "label": _t("KPIs", locale), "basis": basis,
        "layout": "comparative", "periods": _period_labels(rows, basis, locale),
        # Ratios are unitless: no currency, no magnitude, nothing for the presentation
        # selectors to scale. `display1`/`display2` are already in each ratio's own unit.
        "currency": "", "currency_symbol": "", "units": "", "units_scale_factor": 1.0,
        "presentation": "raw",
        "format": doc_format, "page_count": page_count,
        "rows": out,
        # Explicit rather than absent, as on the two face shapes: the KPI view serves the catalog it
        # declares whatever the filing yielded — a ratio whose inputs are missing is a row saying so
        # — so there is no state in which it refuses, and the client needs no special case for it.
        "refused": None,
        "viewer": {
            "company": company or filename, "subtitle": _t("Computed KPIs", locale),
            "chips": [{"label": _t("Consolidated" if basis == "consolidated" else "Standalone",
                                   locale), "active": True}],
            "callout": _t("Every KPI is computed from this document's own extracted figures. "
                          "Select one to see its inputs and jump to where each was printed.",
                          locale),
        },
    }


def _face_prefixes(template_def: dict | None) -> set[str]:
    """The canonical-key prefixes that DO reach a face statement, so anything else can be
    recognised as not being on one.

    A PREFIX TEST, AND NOW AN OVER-APPROXIMATION. Its one caller (services/gap_closing.py::leftovers)
    treats a row whose key carries a face prefix as already placed, which held while the grid rendered
    off-template keys under an "Other extracted items" heading. It no longer does: ``_build_statement``
    emits the template's declared lines only, so a row keyed ``bs_something_the_template_omits`` is on
    no face while this still calls its prefix a face. The consequence is bounded — gap closing offers
    one candidate fewer, and the row is raised as an ``off_template`` review finding whose re-map
    offer is the same remedy by hand — but the honest predicate is ``_declared_line_keys``, and
    ``leftovers`` should move to it.
    """
    out = {p for p in _STMT_PREFIX.values()}
    for stmt in (template_def or {}).get("statements", []):
        t = stmt.get("type")
        if t:
            p = _stmt_prefix(template_def, t)
            if p:
                out.add(p)
    return out


def _note_sort_key_str(no: str) -> tuple[int, str]:
    """Note numbers in the order a filing prints them: numerically, with any sub-letter after."""
    head = re.match(r"\d+", str(no) or "")
    return (int(head.group(0)) if head else 10_000, str(no))


def _build_statement(rows: list[dict], template_def: dict | None, statement_type: str,
                     filename: str, basis: str = "consolidated", locale: str = "en",
                     units_ctx: dict | None = None, company: str | None = None,
                     doc_format: str = "", page_count: int = 0,
                     netting_rules: list | None = None) -> dict:
    """Group the real extracted rows into one statement (by the template's sections), so the
    Workspace grid renders real data with its provenance-backed values. Only rows that
    carry a value for the requested `basis` (consolidated / standalone) are shown. Labels are
    resolved in the output `locale` from the template's label_i18n (input=output parity)."""
    prefix = _stmt_prefix(template_def, statement_type)
    # WHICH BASIS THIS VIEW CAN ACTUALLY SHOW. Asking for a basis the document never labelled used
    # to return nothing, so a filing extracted as company-only rendered an empty Consolidated tab —
    # the default one — with its figures a tab away, and an empty grid reads exactly like a statement
    # the filing does not contain. A document that labelled only ONE basis drew no distinction to
    # filter on, so that basis answers either request; a document that printed Group and Company side
    # by side genuinely has two answers and the request stands. See `services.periods.effective_basis`.
    #
    # Scoped to THIS statement's rows, because the question is per statement: a filing can label its
    # balance sheet on both bases and its income statement on one. Falls back to every row for a view
    # with no canonical-key prefix of its own (the KPIs), which would otherwise never resolve.
    requested_basis = basis
    _scoped = [r for r in rows if (r.get("canonical_key") or "").startswith(f"{prefix}_")]
    basis, basis_why = effective_basis(_scoped or rows, basis)

    def _stamp(spread: dict) -> dict:
        """Say which basis the figures ARE, next to the one that was asked for.

        Never silent: serving the Company's figures under a tab captioned Consolidated without
        saying so would mislabel a real number, which is worse than the empty grid this replaces.
        """
        spread["basis_requested"] = requested_basis
        spread["basis"] = basis
        spread["basis_substituted"] = basis != requested_basis
        spread["basis_reason"] = basis_why
        return spread

    # A statement of changes in equity is not a two-column comparative — its columns are equity
    # COMPONENTS (issued capital, each reserve, retained profits, non-controlling interests,
    # total equity) and its rows are movements through the year. Forcing it into current/prior
    # columns files a component under a period that does not exist, so it gets its own shape.
    if statement_type in _MATRIX_STATEMENTS:
        return _stamp(_build_matrix_statement(
            rows, statement_type, filename, basis=basis, locale=locale, units_ctx=units_ctx,
            company=company, doc_format=doc_format, page_count=page_count,
            template_def=template_def))
    # Two views that are not statements the document prints, but which the document's figures
    # determine: the KPIs computed off them, and everything extracted that reaches no face.
    if statement_type == "kpi":
        return _stamp(_build_kpi_statement(
            rows, filename, basis=basis, locale=locale, company=company,
            doc_format=doc_format, page_count=page_count, template_def=template_def))
    # Several printed lines legitimately share one concept: three depreciation lines roll into
    # "Depreciation and amortisation", two tax payments into "Income tax paid", and an "Others"
    # bucket exists precisely to absorb a handful. Keeping only the first row would drop the
    # rest from the statement silently, so rows are grouped and their values added.
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("canonical_key")
        if k:
            by_key.setdefault(k, []).append(r)

    def has_basis(r: dict) -> bool:
        return bool(_basis_values(r, basis))

    # The template's calculated lines — subtotals, totals, net figures — evaluated from the
    # components the template says they are made of. A subtotal printed on the page is a fourth
    # opinion alongside the lines it is meant to be the sum of; when they disagree, showing the
    # printed one puts a figure on the face that its own components contradict. So the computed
    # figure is what the grid shows, the printed one is kept for the review queue, and their
    # difference becomes a finding rather than a silent inconsistency.
    # Netting is resolved FIRST. It restates a component's displayed figure, and a subtotal
    # computed before that restatement no longer equals the components printed beneath it — the
    # spread would contradict itself on its own face.
    net_cur: dict = {}
    net_prior: dict = {}
    if netting_rules:
        from app.services.netting import compute_netting

        net_cur = compute_netting(rows, netting_rules, basis=basis, period="current")
        net_prior = compute_netting(rows, netting_rules, basis=basis, period="prior")
    netted_cur = {k: _to_num(v["net"]) for k, v in net_cur.items() if v.get("net") is not None}
    netted_prior = {k: _to_num(v["net"]) for k, v in net_prior.items() if v.get("net") is not None}

    calc_cur = _calculated(rows, template_def, basis, "current", locale, netted_cur)
    calc_prior = _calculated(rows, template_def, basis, "prior", locale, netted_prior)

    # A template child's presentation kind (subtotal / total rows are styled differently in
    # the grid); anything else is a plain line item.
    def _kind_for(role: str | None) -> str:
        return {"subtotal": "subtotal", "total": "total"}.get(role or "", "item")

    provs = _key_provenance(rows, basis)

    def as_calculated(row: dict) -> dict:
        """Put the COMPUTED figure on a calculated line, and keep the printed one out of sight.

        The template says this line is made of other lines, so that is the figure the face
        carries. What the document printed is retained as ``reported1``/``reported2`` — not shown
        as the line's value, because a subtotal that contradicts its own components is a finding,
        not a number — and the review queue is built from exactly that difference.

        Precedence is manual > computed > printed. An analyst's typed value is their answer for
        the line and outranks the arithmetic; a line whose components were never extracted has
        nothing to compute from, so it falls back to the printed figure and says so.
        """
        key = row["id"]
        c1, c2 = calc_cur.get(key), calc_prior.get(key)
        if c1 is None and c2 is None:
            return row                                   # not a calculated line
        row["reported1"], row["reported2"] = row["v1"], row["v2"]
        group = by_key.get(key, [])

        def resolve(period: str, calc, reported):
            """This period's figure and where it came from — decided for THIS period alone.

            Every part of this is per-period on purpose. An analyst who corrects the current
            column has said nothing about last year, and a period whose components were not
            extracted is not made computable by the other period's being so. Deciding either
            question at row level silently rewrites the column nobody touched.
            """
            if any(_edited_for(x, basis, period) for x in group):
                return reported, "manual"
            if calc is not None and calc.computable:
                return calc.value, "calculated"
            return reported, "reported_uncomputed"

        row["v1"], o1 = resolve("current", c1, row["reported1"])
        row["v2"], o2 = resolve("prior", c2, row["reported2"])
        row["calculated1"] = c1.value if (c1 and c1.computable) else None
        row["calculated2"] = c2.value if (c2 and c2.computable) else None
        row["origin1"], row["origin2"] = o1, o2
        # The row-level chip summarises the two: a manual value is the most important thing to
        # say about the line, then that anything on it was computed.
        row["origin"] = ("manual" if "manual" in (o1, o2)
                         else "calculated" if "calculated" in (o1, o2)
                         else "reported_uncomputed")
        if row.get("status") == "missing" and (row["v1"] is not None or row["v2"] is not None):
            # The document not printing this line is no longer a gap: the template says what it
            # is made of, and the components were there.
            row["status"] = None

        source = c1 if (c1 and c1.components) else c2
        if source is not None:
            # Display only — see item_row. A rollup's rendering ("12,800 + 2,150 + 3,410") is not
            # an expression the server may evaluate on the next edit.
            row["arithmetic"] = source.formula or row.get("arithmetic")
            # The components ARE the traceability: each with its own figure and the page it was
            # printed on, so a computed subtotal can be taken apart line by line.
            row["contributions"] = [{
                "label": comp.label,
                "canonical_key": comp.canonical_key,
                "v1": _component_value(calc_cur, key, i),
                "v2": _component_value(calc_prior, key, i),
                "method": "calculated" if comp.canonical_key in calc_cur else None,
                "residual": comp.value is None,
                "src": _prov_label(provs.get(comp.canonical_key, (None, None))[0]),
                "source": provs.get(comp.canonical_key, (None, None))[0],
                "src2": _prov_label(provs.get(comp.canonical_key, (None, None))[1]),
                "source2": provs.get(comp.canonical_key, (None, None))[1],
            } for i, comp in enumerate(source.components)]
        diff = None
        if row.get("origin1") == "calculated" and row["v1"] is not None \
                and row["reported1"] is not None:
            diff = row["v1"] - row["reported1"]
        row["inspector"] = {
            "tag": {"calculated": _t("calculated", locale),
                    "manual": _t("manual override", locale),
                    "reported_uncomputed": _t("printed, not computable", locale)}[row["origin"]],
            "src": "", "formula": row.get("arithmetic") or row.get("formula") or "",
            "result": "" if row["v1"] is None else f"{row['v1']:,.0f}",
            "note": _calculated_note(row["origin"], diff, len(source.components) if source else 0,
                                    locale),
        }
        if diff is not None and abs(diff) > _CALC_TOLERANCE:
            # A divergence is the finding; the review queue carries it with the arithmetic.
            row["status"] = "recon"
        row["confidence"] = row.get("confidence")
        return row

    def item_row(key: str, label: str, group: list[dict], kind: str = "item") -> dict:
        """One statement row from every extracted row that mapped to this concept.

        With a single source row this is that row. With several, the values are summed and the
        inspector says how many printed lines went into the figure, so a total that does not
        match any one line on the page is explainable rather than mysterious. Provenance stays
        on the first contributing line, which is where click-to-source lands — carried for BOTH
        periods, because last year's figure is printed somewhere too and is just as much a
        number a reviewer needs to check against the page.
        """
        r = group[0]
        cur, prior = _cur_prior(r, basis)
        cat, pct = _conf_cat(min((x.get("mapping_confidence") or 0) for x in group)
                             if len(group) > 1 else r.get("mapping_confidence"))
        # An edit to the consolidated figures does not make the standalone ones edited.
        edited_row = next((x for x in group if _edited_for(x, basis)), None)
        edited = edited_row is not None
        # One reader for the figure — the sum of the printed lines, or the analyst's manual
        # value replacing it. See services.periods.concept_value.
        v1 = _concept_value(group, basis, "current")
        v2 = _concept_value(group, basis, "prior")
        inspector = _inspector(r, cur)
        formula = (edited_row or r).get("formula")
        arithmetic: str | None = None      # display-only rendering of how the figure was reached
        contributions: list[dict] = []
        if len(group) > 1:
            # A combined figure has to be auditable line by line: each contributing caption with
            # its OWN values and its OWN source location, so every part can be traced back to the
            # page it was printed on. A prose summary cannot be clicked.
            #
            # WHICH LINES ADD, per period. A fact the filing printed twice is counted once
            # (`periods.summable`), so listing every line as an addend would show a column that does
            # not add up to the figure above it — the reader would be left to guess which line the
            # total left out. Marked per period because that is the granularity the figure is.
            adds_cur = {id(x) for x, _ in _summable(group, basis, "current")}
            adds_prior = {id(x) for x, _ in _summable(group, basis, "prior")}
            for x in group:
                c, p = _cur_prior(x, basis)
                contributions.append({
                    "label": x.get("source_label") or "",
                    "canonical_key": x.get("canonical_key"),
                    "v1": _to_num((c or {}).get("value")),
                    "v2": _to_num((p or {}).get("value")),
                    "method": x.get("mapping_method"),
                    # Whether this line was ROUTED here (no specific concept matched) rather than
                    # identified — the difference an analyst needs before accepting the figure.
                    "residual": any("residual_combined" in ((v.get("confidence") or {})
                                                            .get("flags") or [])
                                    for v in (x.get("values") or [])),
                    "src": _prov_label((c or {}).get("provenance")),
                    "source": (c or {}).get("provenance"),
                    # …and the same for the prior period, which sits in its own column on its
                    # own page in a filing that reprints last year's statement.
                    "src2": _prov_label((p or {}).get("provenance")),
                    "source2": (p or {}).get("provenance"),
                    # False for a line whose figure this concept already holds from somewhere else
                    # — the same fact printed on the face and restated in a note, or on two
                    # statements. Shown as evidence, not added.
                    "counted": id(x) in adds_cur,
                    "counted2": id(x) in adds_prior,
                })
            terms = [f"{c['v1']:,.0f}" if c["v1"] is not None else "—" for c in contributions]
            printed = " + ".join(terms)
            # DISPLAY only. This is a rendering of the figures, not an expression: publishing it
            # in `formula` meant the client prefilled its formula box with "100 + 50", sent it
            # back with the next edit, and the server EVALUATED it — so a typed 200 was silently
            # replaced by the recomputed 150. `formula` is reserved for an expression an analyst
            # actually stored.
            arithmetic = printed
            inspector = {**inspector, "tag": "combined",
                         "formula": printed,
                         "result": "" if v1 is None else f"{v1:,.0f}",
                         "src": " · ".join(dict.fromkeys(
                             c["src"] for c in contributions if c["src"])),
                         "note": (f"Combined from {len(group)} printed lines that map to this "
                                  f"concept. Each line below keeps its own figure and page — "
                                  f"click one to jump to it in the document.")
                                 + (f" A manual value replaces the combined figure; the printed "
                                    f"lines still add to {printed}." if edited else "")}
        notes = {}
        for x in group:
            for slot, meta in (x.get("edit_comments") or {}).items():
                b, _, per = str(slot).partition("/")
                if b == basis and (meta or {}).get("text"):
                    notes[per] = meta
        return as_calculated({
            "id": key, "label": label or r.get("source_label"),
            "source_label": r.get("source_label"), "kind": kind,
            "note": next((x.get("note") for x in group if x.get("note")), None),
            "note2": None, "status": "edited" if edited else None,
            # WHERE THE FIGURE WAS PRINTED and WHICH ANALYST SECTION it belongs to, carried from the
            # contributing rows. Not the same question as ``origin`` below, which says whether the
            # figure was read off the document or typed by an analyst — a row can be printed on the
            # face and still carry a manual value.
            #
            # ``face`` when ANY contributing line was printed on a statement face: a concept whose
            # figure comes partly from the face is a face figure, and a reader needs to know the
            # weaker case (it came only from a note) rather than have it averaged away.
            "printed_in": ("face" if any(x.get("printed_in") == "face" for x in group)
                           else next((x.get("printed_in") for x in group
                                      if x.get("printed_in")), None)),
            "bucket": next((x.get("bucket") for x in group if x.get("bucket")), None),
            "bucket_label": next((x.get("bucket_label") for x in group
                                  if x.get("bucket_label")), None),
            # The notes that DETAIL this figure — every note any contributing line cites and that
            # the run actually extracted, so the linkage cannot offer detail that is not there.
            "notes": sorted({n for x in group for n in (x.get("notes") or [])},
                            key=_note_sort_key_str) or None,
            # No confidence object at all when nothing measured one, rather than a category beside a
            # made-up percentage: `_conf_cat` used to answer ('med', 60) for a row that carries no
            # mapping confidence, and the inspector printed "60% confidence" over a figure nothing
            # had scored. The screen already renders the badge only when this key is present.
            "confidence": ({"cat": cat, "pct": pct} if pct is not None else None),
            "editable": True,
            "origin": "manual" if edited else "extracted",
            # Why a figure was overridden, per period — kept beside the number it explains.
            "comments": notes or None,
            "formula": formula, "arithmetic": arithmetic, "inspector": inspector,
            # Present only when more than one printed line was combined into this figure.
            "contributions": contributions or None,
            "v1": v1, "v2": v2,
            # Structured source location of each period's value, so the Workspace's live viewer
            # can hyperlink BOTH figures in the row to the page+bbox (PDF) or sheet+cell (Excel)
            # they were printed at — the prior year is a number a reviewer checks too.
            "source": (cur or {}).get("provenance"),
            "source2": (prior or {}).get("provenance"),
        })

    # A template line that wasn't extracted (or has no value for this basis) still appears, so
    # the analyst sees the FULL template skeleton and can spot/fill gaps — just with blank values.
    # A CALCULATED line is a different case entirely: the document never had to print it for the
    # spread to carry it, so as_calculated fills it in from its components.
    def blank_row(key: str, label: str, kind: str = "item") -> dict:
        return as_calculated({
            "id": key, "label": label, "source_label": None, "kind": kind,
            "note": None, "note2": None, "status": "missing",
            "confidence": None, "editable": True, "formula": None, "origin": "extracted",
            "comments": None,
            "inspector": {"tag": _t("not extracted", locale), "src": "", "formula": "",
                          "result": "", "note": _t("This template line was not found in the "
                                                    "document's extraction for this basis. Enter "
                                                    "a value to record it manually.", locale)},
            "v1": None, "v2": None, "source": None, "source2": None, "arithmetic": None,
        })

    # Show the full template skeleton only when this statement+basis is actually present in the
    # document. If the basis wasn't extracted at all (e.g. no standalone figures), the grid stays
    # empty rather than implying an all-blank statement that the filing never contained.
    basis_present = any(
        has_basis(r) for r in rows if (r.get("canonical_key") or "").startswith(f"{prefix}_")
    )

    out: list[dict] = []
    seen: set[str] = set()
    stmt = _stmt_node(template_def, statement_type)
    def emit(key: str, label: str, kind: str) -> None:
        seen.add(key)
        group = [r for r in by_key.get(key, []) if has_basis(r)]
        out.append(item_row(key, label, group, kind) if group
                   else blank_row(key, label, kind))

    if stmt and basis_present:
        # THE TEMPLATE IS THE WHOLE STATEMENT. Every declared section and line appears (extracted or
        # not, so a gap is visible and fillable), in the order the template declares them, and
        # nothing else appears at all.
        #
        # What used to follow this loop was an "Other extracted items" section: any row whose
        # canonical_key carried this statement's prefix but was not one of the template's declared
        # children, rendered as real statement rows and appended AFTER every template section. It was
        # the in-statement successor of `_build_additional_items_statement`, removed for exactly this
        # reason, and it did two kinds of damage. It ADDED lines the template does not define, which
        # the spread is not allowed to do — a spread is only comparable across filings if its shape
        # is the template's. And against a run pinned to a superseded template it appended the very
        # lines whose position a template revision had corrected, so a fixed line order still
        # rendered with the totals at the end.
        #
        # A mapped figure still never vanishes in silence: an off-template row is raised as an
        # `off_template` review finding carrying a re-map offer (see `_build_review`), which both
        # says the figure reaches no spread and offers the control that fixes it.
        for kind, node in _template_skeleton(stmt):
            if kind == "section":
                out.append({"id": f"sec_{node.get('node_id', '')}", "label": _loc(node, locale),
                            "kind": "section", "v1": None, "v2": None})
            else:
                emit(node["canonical_key"], _loc(node, locale), _kind_for(node.get("role")))
    elif template_def is None:
        # NO TEMPLATE WAS PINNED to this run — `ExtractionOptions.template_version_id` defaults to
        # None and the upload screen allows it — so there is no declared shape to render and no
        # declaration for a row to be outside of. `_template_for_run` documents this fallback: the
        # statement is the extracted concepts themselves, under their own printed captions, in the
        # order they were read. No "Other" heading, because with no skeleton nothing is other.
        for r in rows:
            k = r.get("canonical_key") or ""
            if k.startswith(f"{prefix}_") and k not in seen and has_basis(r):
                emit(k, r.get("source_label") or "", "item")

    # WHY THE GRID IS EMPTY, when it is. Both causes used to be one indistinguishable blank, under a
    # callout ("Values are read deterministically from the source") that asserts figures are being
    # shown — so an analyst went back to the filing hunting for lines that were never going to
    # appear. Stated in the order they are decided: a template declaring no statement of this type
    # answers the question completely, and only then does the basis matter.
    # WHY THE GRID IS EMPTY, when it is. The causes used to be one indistinguishable blank, under a
    # callout ("Values are read deterministically from the source") that asserts figures are being
    # shown — so an analyst went back to the filing hunting for lines that were never going to
    # appear. Ordered most-specific first, because each earlier cause makes the later ones moot and
    # naming the wrong one sends the reader to the wrong place: a template defect is not fixed by
    # widening the page scope.
    refused = None
    if not out:
        refused = _statement_refusal(
            "statement_not_in_template" if stmt is None and template_def is not None
            else "template_declares_no_lines" if stmt is not None and not _template_skeleton(stmt)
            else "basis_not_extracted", locale)

    # Face-line containment netting: reduce a target line by the lines already included in it
    # (e.g. cost of sales inclusive of admin / S&M), showing the net value + formula. Signed and
    # non-destructive — the raw figure is preserved in the inspector for audit.
    if net_cur or net_prior:
        for r in out:
            if r.get("kind") != "item":
                continue
            nc, np = net_cur.get(r["id"]), net_prior.get(r["id"])
            # A figure the analyst typed is the answer for that line; an automatic restatement
            # must not quietly overwrite it. Decided per period: an edit to the current column
            # says nothing about last year, and suppressing netting there would silently show the
            # gross figure instead.
            group = by_key.get(r["id"], [])
            if nc and any(_edited_for(x, basis, "current") for x in group):
                nc = None
            if np and any(_edited_for(x, basis, "prior") for x in group):
                np = None
            if not nc and not np:
                continue
            info = nc or np
            if nc:
                r["v1"] = _to_num(nc["net"])
            if np:
                r["v2"] = _to_num(np["net"])
            r["formula"] = info["formula"]
            r["status"] = "recon"
            raw = info["raw"]
            note = info.get("label") or _t("Contained lines netted out.", locale)
            r["inspector"] = {"tag": "netted", "src": (r.get("inspector") or {}).get("src", ""),
                              "formula": info["formula"], "result": info["net"],
                              "note": f"{note} {_t('Raw', locale)}: {raw}."}

    basis_label = _BASIS_LABEL_I18N.get(basis, {}).get(locale, basis.title())
    return _stamp({
        "statement": statement_type,
        "label": _stmt_label(template_def, statement_type, locale),
        "basis": basis, "periods": _period_labels(rows, basis, locale),
        "currency": (units_ctx or {}).get("currency") or "",
        "currency_symbol": "", "units": (units_ctx or {}).get("units_label") or "",
        # The detected source magnitude (e.g. 1000 for "in thousands"); the Workspace units
        # selector converts relative to this so it never double-scales already-scaled figures.
        "units_scale_factor": _to_num((units_ctx or {}).get("scale_factor")) or 1.0,
        # Document shape so the Workspace picks the right live viewer (PDF pages vs Excel cells).
        "format": doc_format, "page_count": page_count,
        "rows": out,
        # Null when the statement is being served. See `_statement_refusal`.
        "refused": refused,
        "viewer": {
            "company": company or filename, "subtitle": _t("Extracted statement", locale),
            "chips": [{"label": basis_label, "active": True}],
            "callout": _t("Values are read deterministically from the source; mapping is by the "
                          "ensemble. Open the extraction view for click-to-source provenance.",
                          locale) if refused is None else refused["message"],
        },
    })


@router.get("/{document_id}/statement", dependencies=[Depends(authorized_document)])
def get_document_statement(
    document_id: str,
    statement: str = Query("balance_sheet"),
    basis: str = Query("consolidated"),
    locale: str = Query("en"),
    session: Session = Depends(db),
) -> dict:
    """One statement of a document's real extraction, grouped for the Workspace grid,
    with labels resolved in the output `locale`."""
    from app.db.models import Document

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="No extraction run yet for this document")
    template_def = _template_for_run(session, run)
    # Consolidated and standalone are extracted in one pass; the grid shows the requested
    # basis (empty if the source didn't present that basis).
    spread = _build_statement(run.result.get("rows", []), template_def, statement,
                              doc.filename or "document", basis, locale,
                              run.result.get("units"), company=run.result.get("entity"),
                              doc_format=run.result.get("format") or doc.fmt or "",
                              page_count=run.result.get("page_count") or doc.page_count or 0,
                              # Apply only the netting the LLM confirmed for this document (cached at
                              # extraction); the raw ontology policies are candidates, not results.
                              netting_rules=run.result.get("netting") or [])
    # Which template version this spread's SHAPE came from, and whether a newer one exists. Added
    # here rather than inside `_build_statement`, which is a pure function of a definition and has no
    # session to ask the question with — and the question is about the row in the table, not about
    # the definition. See `_superseded_template`: stated, never acted on.
    spread["superseded_template"] = _superseded_template(session, run)
    return spread


def _note_key(raw) -> str | None:
    """A note's number as the filing prints it — the key the notes index and detail are served on.

    A STRING, not an int. This parsed to ``int`` and returned None on failure, which silently
    dropped every note a filing does not number with a bare integer: a table printed as "16(b)" or
    "7A" was absent from the index, absent from its own detail route, and absent from the face rows
    grouped by note — the note existed, was linked, was filed in its section, and could not be
    opened. Parsing the leading integer instead is not the fix either: it merges a table numbered
    "16(b)" with one numbered "16" and serves the two as one note.

    Order comes from ``_note_sort_key_str`` (numerically, sub-letter after), so the index still
    reads in the order the filing prints, which is what the int key was really providing.
    """
    key = str(raw).strip() if raw is not None else ""
    return key or None


def _rows_by_note(rows: list[dict]) -> dict[str, list[dict]]:
    """Face rows grouped by the note they cite — the "linked line" the notes screen names.

    Keyed off the row's RESOLVED linkage (``notes``, see ``extractions._linked_notes``) rather than
    off the note column as printed. A row citing "16(b)" of a table numbered "16" belongs under
    note 16; re-deriving that here from the printed token would file it under nothing, and would be
    a fourth place answering "which notes does this row cite" — the rule lives in
    ``LineItem.cited_notes_among`` and the linkage is what it produced.

    The printed column is the fallback for a run stored before rows carried their linkage.
    """
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        cited = r.get("notes") or ([r.get("note")] if r.get("note") else [])
        for raw in cited:
            n = _note_key(raw)
            if n is not None:
                grouped.setdefault(n, []).append(r)
    return grouped


# A CONTINUATION HEADING, in the forms an HKEX filing prints: "(Continued)", "（續）", "(续)".
_CONTINUED = re.compile(r"\(\s*cont(?:inued|\.)?\s*\)|[（(]\s*[續续]\s*[)）]", re.IGNORECASE)


def _note_index(details: list[dict]) -> dict[str, dict]:
    """One entry per NOTE, gathering every table the filing printed under that number.

    THIS USED TO BE A DICT COMPREHENSION keyed by the note number, which meant a note printed as
    more than one table did not merge — it OVERWROTE. Extraction builds a table per (heading
    occurrence, page), so a note continued across pages with its heading repeated arrives here
    several times, and the last one won. Measured on a 270-page bilingual HKEX filing: 63 tables
    for 28 notes, note 38 in seven of them, notes 15 and 20 in five, note 6 in four. Every symptom
    an analyst saw came from that one line:

    * every title read "(Continued)", because the last table is a continuation;
    * a note's detail pane showed the last fragment's rows — sometimes a single stray row — and
      called it the note;
    * the page served was the LAST fragment's, so click-to-source jumped to a continuation page
      while the note's own heading and table sat on an earlier one. Two page numbers for one note,
      and a link that lands on neither the note nor what the pane was showing.

    So the note is assembled instead: rows concatenated in printed order, the page being where the
    note STARTS, and ``pages`` listing every page it spans so the viewer can say so.

    THE TABLES ARE NOT MERGED IN THE MODEL, and the distinction is the point. For READING, the note
    number is the unit — an analyst asked for note 22 and a page break is not part of its meaning.
    For ARITHMETIC it is not: a tax note prints the components in one table and the effective-rate
    RECONCILIATION in another, and ``map_ontology``'s decomposition has to be able to tell them
    apart or it counts a restated component twice. ``doc.notes`` therefore keeps one entry per
    printed table and this index — the presentation layer — is where they become one note.
    """
    out: dict[str, dict] = {}
    for d in details:
        n = _note_key(d.get("no"))
        if n is None:
            continue
        page = d.get("page") or 0
        cur = out.get(n)
        if cur is None:
            out[n] = {**d, "rows": list(d.get("rows") or []),
                      "pages": [page] if page else [],
                      # Kept as (sheet, folio) pairs so the folio of the note's FIRST page can be
                      # named after the sheet pages are sorted. A folio is a string and sorts
                      # lexicographically ('100' < '99'), so it can never be the sort key.
                      "printed_by_page": ({page: d.get("printed_page")}
                                          if page and d.get("printed_page") else {})}
            continue
        cur["rows"].extend(d.get("rows") or [])
        if page and d.get("printed_page"):
            cur.setdefault("printed_by_page", {})[page] = d["printed_page"]
        if page:
            if page not in cur["pages"]:
                cur["pages"].append(page)
            # The note starts at the LOWEST page it was printed on. Not the first one seen: the
            # order tables arrive in is page order today, and a note's start must not depend on
            # that staying true.
            if not cur.get("page") or page < cur["page"]:
                cur["page"] = page
        # A continuation heading names the note no worse than the original, but it names it less
        # well, so the first non-continuation title wins.
        if _CONTINUED.search(cur.get("title") or "") and not _CONTINUED.search(d.get("title") or ""):
            cur["title"] = d.get("title")
    for d in out.values():
        d["pages"] = sorted(d["pages"])
        by_page = d.pop("printed_by_page", {}) or {}
        d["printed_pages"] = [f for f in (by_page.get(pg) for pg in d["pages"]) if f]
        d["printed_page"] = by_page.get(d.get("page")) if d.get("page") else None
    return out


def _note_row_kind(row: dict) -> str | None:
    """Map a note-detail row's role to the frontend's emphasis kind: 'tot' for a total,
    'sub' for a subtotal. Falls back to a label heuristic when the role is a plain line but
    the caption clearly reads as a total."""
    role = (row.get("role") or "line").lower()
    if role == "total":
        return "tot"
    if role == "subtotal":
        return "sub"
    if role == "line" and re.match(r"\s*(total|net\b)", (row.get("label") or "").lower()):
        return "tot"
    return None


def _fmt_amt(v) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _entry_column(entry: dict) -> str:
    """Which COLUMN a reconciliation entry compared, as ``basis/period`` — the same spelling the
    review card's ``where`` uses for the same pair.

    Reconciliation records one entry per (face line, note, basis, period) and ``_TIE_PERIODS`` is
    ("current", "prior") (stages/reconcile.py), so an ordinary comparative filing holds TWO entries
    for one face line. A residual printed without its column is a figure the reader cannot place:
    the Notes screen listed one face line twice, under two different residuals, with no column named.
    """
    return f"{entry.get('basis') or '—'}/{entry.get('period_label') or '—'}"


def _reconciliation_text(entries: list[dict], note_no: str) -> str | None:
    """A human-readable note→face reconciliation summary for one note, from the reconcile
    stage's entries. Prefers the consolidated / current-period entry.

    One note can break down SEVERAL face lines (reconcile.py records an entry per face line, and
    ``link_notes`` has a first-class relationship for it), so when more than one of them does not
    tie this says how many and names each residual. Printing the best-graded entry's residual alone
    read as "the note is out by 20" over a second face line out by 2,000,000 — a number that was
    not about the sentence it sat in.

    COUNT AND NAME WHAT THE SENTENCE CLAIMS. ``mine`` spans every basis AND period for the note, so
    ``len(untied)`` counted reconciliation ENTRIES while the sentence called them "face lines": a
    comparative filing whose ONE face line missed on both columns read "does not tie to 2 of the face
    lines it supports" and then listed that line twice under two different residuals with no column
    named. Face lines and columns are now counted separately, each residual says which column it
    belongs to, and the "Face figure … → reconciled …" clause above says which column IT is about —
    it is taken from ``mine[0]``, the best-graded entry, while the residual list spans all of them.
    """
    # Both sides through ``_note_key``, so this compares a key with a key. The caller passes the
    # route's own path value and a reconciliation entry carries whatever the pipeline recorded —
    # "12" and 12 are the same note, and a bare == between them silently found nothing.
    want = _note_key(note_no)
    mine = [e for e in entries if _note_key(e.get("note_number")) == want]
    if not mine:
        return None
    # An entry we could actually grade says more than an unconfirmed one, so prefer it; then
    # prefer the consolidated / current-period view.
    mine.sort(key=lambda e: (tie_status(e) == "unconfirmed",
                             e.get("basis") != "consolidated",
                             e.get("period_label") != "current"))
    e = mine[0]
    raw, sub, rec = e.get("raw_face"), e.get("subtracted"), e.get("reconciled")
    resid = e.get("residual")
    status = tie_status(e)
    parts: list[str] = []
    try:
        if abs(float(sub)) > 0:
            parts.append(
                f"Face figure {_fmt_amt(raw)} less {_fmt_amt(sub)} of note detail already "
                f"carried as separate line items → reconciled {_fmt_amt(rec)} "
                f"({_entry_column(e)}).")
    except (TypeError, ValueError):
        pass
    if status == "tied":
        parts.append(f"The note total ties to the face figure ({_entry_column(e)}, residual "
                     f"{_fmt_amt(resid)}).")
    elif status == "untied":
        # EVERY face line this note fails to tie to, in the same sentence. `e` is the best-graded
        # entry; with a second face line out by a different amount, naming only this residual states
        # a figure that is not about the note as a whole.
        untied = sorted([u for u in mine if tie_status(u) == "untied"],
                        key=lambda u: (str(u.get("face_key") or ""), _entry_column(u)))
        # The noun in the sentence is "face lines", so the count is of face LINES; the number of
        # comparisons that missed is its own quantity and is stated as its own.
        lines = {str(u.get("face_key") or "—") for u in untied}
        if len(untied) > 1:
            each = "; ".join(f"{u.get('face_key') or '—'} ({_entry_column(u)}) "
                             f"{_fmt_amt(u.get('residual'))}" for u in untied)
            noun = "face line" if len(lines) == 1 else "face lines"
            parts.append(f"The note total does not tie to {len(lines)} of the {noun} it supports, "
                         f"on {len(untied)} of the columns compared — residual by face line and "
                         f"column: {each} (flagged for review).")
        else:
            parts.append(f"The note total does not tie to the face figure — residual "
                         f"{_fmt_amt(resid)} ({_entry_column(e)}) (flagged for review).")
    else:
        parts.append("This note is not a breakdown of the face figure it is cited from, so no "
                     "tie is asserted.")
    return " ".join(parts)


@router.get("/{document_id}/notes", dependencies=[Depends(authorized_document)])
def get_document_notes(document_id: str, session: Session = Depends(db)) -> dict:
    """All-notes index for a real document. Prefers the EXTRACTED note detail tables (the
    breakdowns parsed from the notes pages); falls back to the notes referenced by face
    line items when no detail tables were parsed."""
    run = _latest_run(session, document_id)
    if run is None or not run.result:
        return {"notes": [], "count": 0, "linked": 0}

    details = _note_index(run.result.get("note_details", []))
    # `conf_pct` is NULL on both branches, deliberately and explicitly. A note carries no measured
    # confidence of its own — the categories below say which SOURCE the index was built from (a
    # parsed detail table, or the face lines citing the note), not how confident anything is — so
    # there is no percentage to serve, and the screen must say so instead of printing the literal
    # its category happens to map to. Absent-versus-null is the distinction the client needs to
    # tell "no measurement" from "not sent".
    # Sorted by ``_note_sort_key_str``, not by the key itself: the keys are strings now, so a plain
    # sort would read 1, 10, 16(b), 2 — the printed order is numeric with any sub-letter after it.
    if details:
        notes = [{"no": n, "title": details[n].get("title") or f"Note {n}",
                  "conf": "high", "conf_pct": None}
                 for n in sorted(details, key=_note_sort_key_str)]
        linked = sum(len(details[n].get("rows", [])) for n in details)
        return {"notes": notes, "count": len(notes), "linked": linked}

    grouped = _rows_by_note(run.result.get("rows", []))
    notes = [{"no": n, "title": f"Note {n}", "conf": "med", "conf_pct": None}
             for n in sorted(grouped, key=_note_sort_key_str)]
    return {"notes": notes, "count": len(notes), "linked": sum(len(v) for v in grouped.values())}


@router.get("/{document_id}/notes/{note_no}", dependencies=[Depends(authorized_document)])
def get_document_note(document_id: str, note_no: str, locale: str = Query("en"),
                      session: Session = Depends(db)) -> dict:
    """One note's detail for a real document: its EXTRACTED breakdown rows (label + period
    values) with the page they came from, plus the face line that cites it. Falls back to
    the face line items referencing the note when no detail table was parsed.

    ``periods`` is the same key, in the same order, that the statement routes serve, so ONE client
    field labels both screens. The Notes screen used to print "FY25"/"FY24" as literals above these
    very columns, which said something different from the Workspace on any filing whose periods are
    not those two.
    """
    run = _latest_run(session, document_id)
    result = run.result if run and run.result else {}
    details = _note_index(result.get("note_details", []))
    faces = _rows_by_note(result.get("rows", []))
    linked_face = faces.get(note_no, [])
    linked_label = linked_face[0].get("source_label") if linked_face else ""
    linked_key = linked_face[0].get("canonical_key") if linked_face else ""

    if note_no in details:
        d = details[note_no]
        detail_rows = []
        for row in d.get("rows", []):
            cur_v, prior_v = split_current_prior(row.get("values") or [])
            cur, prior = cur_v or {}, prior_v or {}
            detail_rows.append({
                "label": row.get("label", ""),
                "v1": _to_num(cur.get("value")) or 0,
                "v2": _to_num(prior.get("value")) or 0,
                # Role → emphasis (subtotal/total) and mapping confidence → a per-row badge,
                # so a real note detail shows the same role/confidence cues as the demo. The badge
                # carries the MEASURED percentage beside its category: the category alone left the
                # screen printing a per-category literal ("96%" on every 'high' row, whatever the row
                # scored), and the number it needs was being computed here and dropped.
                **({"kind": _note_row_kind(row)} if _note_row_kind(row) else {}),
                **(dict(zip(("conf", "conf_pct"), _conf_cat(row.get("confidence"))))
                   if isinstance(row.get("confidence"), (int, float)) else {}),
            })
        return {
            "no": note_no, "title": d.get("title") or f"Note {note_no}", "page": d.get("page", 0),
            # EVERY page the note was printed on, not only the first. A note continued across pages
            # is one note (see ``_note_index``), and a reader sent to its first page needs to know
            # the rest of it is overleaf rather than missing.
            "pages": d.get("pages") or ([d["page"]] if d.get("page") else []),
            # The folios for the same span, so the screen can CITE the note by the number printed
            # on its page while still scrolling the viewer by ``page``. Empty when the filing
            # printed none, which is the normal case for a spreadsheet.
            "printed_page": d.get("printed_page"),
            "printed_pages": d.get("printed_pages") or [],
            "linked_line": linked_key, "linked_label": linked_label,
            "rows": detail_rows,
            # Derived from exactly the value lists whose split_current_prior above produced v1/v2,
            # so a header cannot disagree with the column under it.
            "periods": _period_labels_from((r.get("values") or [] for r in d.get("rows", [])),
                                           locale),
            "reconciliation": _reconciliation_text(result.get("reconciliation", []), note_no),
        }

    # Fallback: the face items that reference the note.
    first = linked_face[0] if linked_face else None
    prov = (_cur_prior(first)[0] or {}).get("provenance") if first else None
    detail_rows = []
    for r in linked_face:
        cur, prior = _cur_prior(r)
        detail_rows.append({"label": r.get("source_label", ""),
                            "v1": _to_num((cur or {}).get("value")) or 0,
                            "v2": _to_num((prior or {}).get("value")) or 0})
    return {
        "no": note_no, "title": f"Note {note_no}",
        "page": (prov.get("page_index", 0) + 1) if prov else 0,
        "pages": [prov["page_index"] + 1] if prov else [],
        "printed_page": (prov.get("printed_page") if prov else None),
        "printed_pages": ([prov["printed_page"]] if prov and prov.get("printed_page") else []),
        "linked_line": linked_key, "linked_label": linked_label,
        "rows": detail_rows,
        # Same consolidated default `_cur_prior` uses for these fallback rows, so the header names
        # the columns the figures were read from.
        "periods": _period_labels_from((_basis_values(r, "consolidated") for r in linked_face),
                                       locale),
        "reconciliation": None,
    }


# --- the eight analyst buckets -----------------------------------------------------------------
# The segmentation itself is stored on the run (services/buckets.py) and holds MEMBERSHIP only: which
# face rows, which notes, which pages. These two endpoints join it to ``rows`` and ``note_details``,
# so a bucket's figures are served from the one place they are stored rather than from a copy.


def _bucket_store(run) -> dict | None:
    """The stored segmentation, or None for a run extracted before the segment stage existed.

    None is served as an explicit ``segmented: false`` rather than as eight empty buckets: a filing
    whose sections were never segmented and one whose sections are all empty are different answers,
    and only the first is fixed by re-running the extraction.
    """
    if not run or not run.result:
        return None
    return run.result.get("buckets") or None


@router.get("/{document_id}/buckets", dependencies=[Depends(authorized_document)])
def get_document_buckets(document_id: str, session: Session = Depends(db)) -> dict:
    """What each bucket holds, in the order a filing is read.

    Counts and page ranges only — call the detail route for a bucket's own rows and notes. Every
    face row is in exactly one bucket, so ``face_rows`` summed over the buckets equals the run's
    face row count; ``unresolved_face_rows`` says how many of them reached Others because nothing
    could place them, which is the number that measures coverage.

    NOTES DO NOT SUM THE SAME WAY. A note cited from two buckets is filed in both, so ``notes``
    summed over the buckets exceeds the filing's note count by the overlap. ``distinct_notes`` is
    the filing-level answer and each bucket's ``shared_notes`` names its duplicated members, so a
    caller needing a total can compute it rather than being handed a figure that double counts.
    """
    from app.services.buckets import BUCKETS

    run = _latest_run(session, document_id)
    store = _bucket_store(run)
    if store is None:
        return {"segmented": False, "buckets": [], "unresolved_face_rows": 0,
                "unknown_sections": []}

    by_key = {seg.get("bucket"): seg for seg in store.get("segments", [])}
    buckets = []
    for key, label in BUCKETS:
        seg = by_key.get(key) or {}
        buckets.append({
            "bucket": key,
            "label": label,
            "face_rows": len(seg.get("face_item_ids") or []),
            "notes": len(seg.get("note_numbers") or []),
            "note_numbers": list(seg.get("note_numbers") or []),
            "shared_notes": list(seg.get("shared_notes") or []),
            # 1-based, as every page number this API serves is.
            "face_pages": [p + 1 for p in (seg.get("face_pages") or [])],
            "note_pages": [p + 1 for p in (seg.get("note_pages") or [])],
            "sections": list(seg.get("sections") or []),
        })
    return {
        "segmented": True,
        "buckets": buckets,
        # Distinct across the whole filing, because the per-bucket counts above deliberately
        # double-count a shared note and adding them up is therefore the wrong total.
        "distinct_notes": len({n for b in buckets for n in b["note_numbers"]}),
        "unresolved_face_rows": len(store.get("unresolved_face_item_ids") or []),
        "unresolved_notes": list(store.get("unresolved_note_numbers") or []),
        # A section of the filing this taxonomy has no bucket for. Its rows are in Others; naming
        # it here is what stops a swallowed section reading like a covered one.
        "unknown_sections": list(store.get("unknown_sections") or []),
    }


@router.get("/{document_id}/buckets/{bucket}", dependencies=[Depends(authorized_document)])
def get_document_bucket(document_id: str, bucket: str,
                        session: Session = Depends(db)) -> dict:
    """One bucket's own source: its face rows and the notes filed under it.

    A note cited from two buckets is filed in both and its content is served in both, because an
    analyst reading current liabilities needs the borrowings note in front of them and so does one
    reading non-current liabilities. ``shared_notes`` names which of the notes below are also filed
    elsewhere — the figures of those notes appear more than once across the buckets, so a caller
    adding the buckets up has to subtract the overlap.
    """
    from app.services.buckets import BUCKET_LABELS

    if bucket not in BUCKET_LABELS:
        raise HTTPException(status_code=404, detail=f"Unknown bucket: {bucket}")
    run = _latest_run(session, document_id)
    store = _bucket_store(run)
    if store is None:
        return {"segmented": False, "bucket": bucket, "label": BUCKET_LABELS[bucket],
                "rows": [], "notes": [], "shared_notes": []}

    seg = next((x for x in store.get("segments", []) if x.get("bucket") == bucket), {})
    wanted = set(seg.get("face_item_ids") or [])
    unresolved = set(store.get("unresolved_face_item_ids") or [])
    numbers = {str(n) for n in (seg.get("note_numbers") or [])}
    notes = [n for n in (run.result.get("note_details") or []) if str(n.get("no")) in numbers]
    by_number = {str(n.get("no")): n for n in notes}
    # Joined by id and served in the run's own row order, which is the order the rows were printed.
    #
    # EACH FACE ROW CARRIES THE NOTES THAT DETAIL IT, in full, right here — not a note number the
    # reader has to go and look up. ``notes`` on the row is the linkage the extraction recorded
    # (only notes it actually parsed, see ``extractions._linked_notes``), and this resolves it
    # against this section's own note bodies. Restricted to the notes filed in THIS section, so a
    # borrowings note split across current and non-current appears under the row that cites it in
    # the section being read, and a section's payload never smuggles in another's content.
    #
    # ``is_note`` is on every note object for the same reason ``printed_in`` is on every row: a note
    # line and a face line have the same shape and a note's lines sum to a figure the face already
    # reports, so anything adding both up double-counts the filing. The bifurcation has to be
    # readable from the payload, not inferred from which key it arrived under.
    rows = []
    for r in (run.result.get("rows") or []):
        if r.get("id") not in wanted:
            continue
        cited = [by_number[n] for n in (r.get("notes") or []) if n in by_number]
        rows.append({**r, "unresolved": r.get("id") in unresolved,
                     "note_details": [{**n, "is_note": True} for n in cited] or None})
    return {
        "segmented": True,
        "bucket": bucket,
        "label": BUCKET_LABELS[bucket],
        "sections": list(seg.get("sections") or []),
        "face_pages": [p + 1 for p in (seg.get("face_pages") or [])],
        "note_pages": [p + 1 for p in (seg.get("note_pages") or [])],
        "rows": rows,
        # The section's notes as a set as well as per row: a note this section holds that NO row in
        # it cites (placed from its own content — see ``buckets._bucket_from_note_content``) belongs
        # to the section and would otherwise be invisible here.
        "notes": [{**n, "is_note": True} for n in notes],
        "shared_notes": list(seg.get("shared_notes") or []),
    }


@router.get("/{document_id}", dependencies=[Depends(authorized_document)])
def get_document(document_id: str, session: Session = Depends(db)) -> dict:
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": row.id,
        "filename": row.filename,
        "status": row.status,
        "format": row.fmt,
        "locale": row.locale,
        "page_count": row.page_count,
        "integrity_report": row.integrity_report,
    }


@router.get("/{document_id}/pages/{page_index}/image", dependencies=[Depends(authorized_document)])
def get_page_image(
    document_id: str,
    page_index: int,
    dpi: int = Query(110, ge=50, le=300),
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> Response:
    """Rasterize one PDF page to PNG — the backdrop the frontend draws the value's
    normalized bbox over (click-to-source). PDF only; spreadsheets have no page image."""
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if row.fmt != "pdf":
        raise HTTPException(status_code=400, detail="Page images are only available for PDFs")
    try:
        import fitz
    except ImportError:  # pragma: no cover - PyMuPDF is a core dep
        raise HTTPException(status_code=501, detail="PDF rendering unavailable")
    data = store.get(row.object_key)
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
        if page_index < 0 or page_index >= pdf.page_count:
            raise HTTPException(status_code=404, detail="Page out of range")
        png = pdf[page_index].get_pixmap(dpi=dpi).tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not render page: {exc}")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/{document_id}/search", dependencies=[Depends(authorized_document)])
def search_document_text(
    document_id: str,
    q: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(80, ge=1, le=300),
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> dict:
    """Find ``q`` in the document's text layer, as page + normalized box per hit.

    The viewer renders pages as images, so it has no text of its own to search — and an analyst
    checking a figure needs to get to a page by what it SAYS, not only by the position an extracted
    value happens to carry. The boxes come back in the same normalized page space as provenance, so
    a hit is highlighted by exactly the overlay a picked value is.

    A scanned page has no text layer and cannot be searched; the count of those is returned rather
    than left to look like an absence of matches. (Their OCR text is not retained per word, so there
    is nothing to give a box to.)
    """
    from app.db.models import Document

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if row.fmt != "pdf":
        raise HTTPException(status_code=400, detail="Text search is only available for PDFs")
    try:
        import fitz
    except ImportError:  # pragma: no cover - PyMuPDF is a core dep
        raise HTTPException(status_code=501, detail="PDF rendering unavailable")

    folios = {(p.get("index", 0) or 0): p.get("printed_page")
              for p in _document_pages(row, store)}
    scanned = sum(1 for p in _document_pages(row, store) if p.get("source_kind") == "scanned")
    needle = q.strip()
    hits: list[dict] = []
    truncated = False
    try:
        pdf = fitz.open(stream=store.get(row.object_key), filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not open document: {exc}")
    for index in range(pdf.page_count):
        if len(hits) >= limit:
            truncated = True
            break
        page = pdf[index]
        try:
            found = page.search_for(needle)
        except Exception:  # noqa: BLE001 — one unreadable page must not fail the search
            continue
        if not found:
            continue
        w, h = max(page.rect.width, 1.0), max(page.rect.height, 1.0)
        lines = _text_lines_for_snippets(page)
        for rect in found:
            if len(hits) >= limit:
                truncated = True
                break
            hits.append({
                "page_index": index,
                "printed_page": folios.get(index),
                "bbox": {"x0": max(0.0, rect.x0 / w), "y0": max(0.0, rect.y0 / h),
                         "x1": min(1.0, rect.x1 / w), "y1": min(1.0, rect.y1 / h)},
                "snippet": _snippet_at(lines, rect) or needle,
            })
    pdf.close()
    return {"query": needle, "hits": hits, "count": len(hits),
            "truncated": truncated, "scanned_pages": scanned}


def _text_lines_for_snippets(page) -> list[tuple[tuple[float, float, float, float], str]]:
    """(box, text) for each text line of the page — the context a hit is quoted from."""
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for block in blocks:
        for line in block.get("lines", []):
            text = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            if text:
                out.append((tuple(line.get("bbox") or (0.0, 0.0, 0.0, 0.0)), text))
    return out


def _snippet_at(lines: list[tuple[tuple[float, float, float, float], str]], rect,
                width: int = 120) -> str | None:
    """The printed line a hit falls on, trimmed — so a result reads as the sentence it came from."""
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    best: tuple[float, str] | None = None
    for (x0, y0, x1, y1), text in lines:
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            area = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
            if best is None or area < best[0]:
                best = (area, text)
    if best is None:
        return None
    return best[1][:width] + ("…" if len(best[1]) > width else "")


@router.get("/{document_id}/cell-context", dependencies=[Depends(authorized_document)])
def get_cell_context(
    document_id: str,
    sheet: str = Query(..., description="Sheet name the value came from"),
    cell: str = Query(..., description="Cell reference, e.g. C14"),
    radius: int = Query(4, ge=1, le=12),
    session: Session = Depends(db),
    store: LocalObjectStore = Depends(object_store),
) -> dict:
    """A window of cells around a value's origin — the spreadsheet click-to-source
    backdrop (mirrors the PDF page image for XLSX sources)."""
    from app.db.models import Document
    from app.services.excel_extract import cell_context

    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if row.fmt != "xlsx":
        raise HTTPException(status_code=400, detail="Cell context is only available for spreadsheets")
    data = store.get(row.object_key)
    try:
        return cell_context(data, sheet=sheet, cell=cell, radius=radius)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Sheet not found: {sheet}")
