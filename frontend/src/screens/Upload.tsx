/** Screen 1 — Documents & Template. Source docs dropzone + list, output template,
 * and ontology. Mirrors wireframe scrUpload verbatim. */
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, Card } from "../components/ui";
import { color, font, radius } from "../theme";
import type { ExtractMode, SourceDoc, TemplateRef } from "../types";
import {
  activeTemplate,
  ontologyInForce,
  useDeleteDocument,
  useDocumentIntegrity,
  useDocuments,
  useOntologies,
  useProject,
  useTemplates,
  useUploadDocument,
} from "../lib/queries";
import { useAppLocale, useUI } from "../store";
import { useT } from "../i18n";
import { useCan } from "../lib/rbac";
import { SCREENS } from "./config";

/** Extension chip color pairs (PDF → red, XLS → green). */
function extColors(ext: string): { bg: string; fg: string } {
  if (ext.toUpperCase().startsWith("XLS")) return { bg: color.greenBg, fg: color.greenFg };
  return { bg: color.redBg, fg: color.redFg };
}

/** Status tag color pairs (Mixed → amber, Native → green, Scanned → indigo tint). */
function tagColors(tag: SourceDoc["tag"]): { bg: string; fg: string } {
  if (tag === "Mixed") return { bg: color.amberBg, fg: color.amberFg };
  if (tag === "Native") return { bg: color.greenBg2, fg: color.greenFg };
  return { bg: color.indigoTint2, fg: color.indigo };
}

function DocRow({ doc, onDelete, deleting, active, onSelect }: {
  doc: SourceDoc; onDelete?: () => void; deleting?: boolean; active?: boolean;
  onSelect?: () => void;
}) {
  const t = useT();
  const ext = extColors(doc.ext);
  const tag = tagColors(doc.tag);
  return (
    <div
      data-testid="doc-row"
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (onSelect && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSelect(); }
      }}
      title={onSelect ? t("u.pickForExtraction") : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 11,
        padding: "10px 12px",
        border: `1px solid ${active ? color.indigo : color.hairline3}`,
        borderRadius: 9,
        marginBottom: 8,
        background: active ? color.indigoTint2 : "transparent",
        cursor: onSelect ? "pointer" : undefined,
      }}
    >
      <span
        style={{
          width: 30,
          height: 30,
          borderRadius: 7,
          background: ext.bg,
          color: ext.fg,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 11,
          fontWeight: 600,
          flex: "0 0 auto",
        }}
      >
        {doc.ext}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 500 }}>{doc.name}</div>
        <div style={{ fontSize: 11, color: color.muted }}>{doc.meta}</div>
      </div>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          padding: "3px 8px",
          borderRadius: radius.pill,
          background: tag.bg,
          color: tag.fg,
        }}
      >
        {doc.tag}
      </span>
      {active && (
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 600,
            padding: "3px 8px",
            borderRadius: radius.pill,
            background: color.indigo,
            color: "#fff",
          }}
        >
          {t("u.activeDoc")}
        </span>
      )}
      {onDelete && (
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          disabled={deleting}
          aria-label={t("u.deleteDoc")}
          title={t("u.deleteDoc")}
          data-testid="doc-delete"
          style={{
            fontSize: 14, color: color.muted, background: "none", border: "none",
            cursor: deleting ? "wait" : "pointer", padding: "2px 4px", lineHeight: 1,
          }}
        >
          🗑
        </button>
      )}
    </div>
  );
}

/** One selectable extraction-mode tile (radio semantics). */
function ModeOption({
  value,
  selected,
  onSelect,
  glyph,
  title,
  desc,
  badge,
}: {
  value: ExtractMode;
  selected: boolean;
  onSelect: (v: ExtractMode) => void;
  glyph: string;
  title: string;
  desc: string;
  badge?: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={() => onSelect(value)}
      style={{
        flex: 1,
        display: "flex",
        alignItems: "flex-start",
        gap: 11,
        textAlign: "start",
        padding: 13,
        borderRadius: 10,
        cursor: "pointer",
        fontFamily: font.sans,
        background: selected ? color.indigoTint : "#fff",
        border: `1.5px solid ${selected ? color.indigo : color.hairline3}`,
      }}
    >
      {/* radio dot */}
      <span
        aria-hidden
        style={{
          marginTop: 2,
          width: 16,
          height: 16,
          borderRadius: "50%",
          flex: "0 0 auto",
          border: `1.5px solid ${selected ? color.indigo : color.controlBorder}`,
          background: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {selected && (
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: color.indigo }} />
        )}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>
            <span style={{ color: color.faint, marginInlineEnd: 6 }}>{glyph}</span>
            {title}
          </span>
          {badge && (
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                padding: "2px 7px",
                borderRadius: radius.pill,
                background: color.greenBg2,
                color: color.greenFg,
              }}
            >
              {badge}
            </span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.45 }}>{desc}</div>
      </div>
    </button>
  );
}

/** Output-template card: shows the active template and lets the analyst switch to another
 * existing one (real templates from the API). Authoring a new template is admin-only and
 * routes to the Template & Ontology screen. */
function TemplateCard({
  canTemplate,
  canSelectTemplate,
  onAuthor,
}: {
  canTemplate: boolean;
  canSelectTemplate: boolean;
  onAuthor: () => void;
}) {
  const t = useT();
  const { data: templates, isPending } = useTemplates();
  const selectedId = useUI((s) => s.selectedTemplateId);
  const setSelectedId = useUI((s) => s.setSelectedTemplateId);
  const [pickerOpen, setPickerOpen] = useState(false);

  const list: TemplateRef[] = templates ?? [];
  // THE ACTIVE TEMPLATE VERSION: the one chosen, else the LATEST the server names.
  //
  // Both halves were wrong. `find(x => x.template_key === selectedKey)` matched on a KEY over a list
  // holding every version of it, so it answered the FIRST row — v1, the oldest — however many
  // revisions existed; and `list[0]` fell back to whatever insertion order happened to put first,
  // which is the same v1 rather than anything meaning "current". Selecting was worse than
  // ineffective: every row in the picker stored the same key, so choosing v2 re-stored the key
  // already held and the list re-answered v1, with the row highlight moving nowhere.
  //
  // `is_latest` is read, never re-derived (see `TemplateRef`); `list[0]` remains only as the answer
  // when a payload predates that flag, and the list is now newest-first so even that is the sensible
  // one rather than an accident.
  const active = activeTemplate(list, selectedId);

  return (
    <Card>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 11 }}>{t("u.outputTemplate")}</div>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: 11,
          border: `1px solid ${color.indigoBorder}`, background: color.indigoTint,
          borderRadius: 9, marginBottom: 9,
        }}
      >
        <span
          style={{
            width: 30, height: 30, borderRadius: 7, background: color.indigo, color: "#fff",
            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, flex: "0 0 auto",
          }}
        >
          ▦
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>
            {active ? active.name : isPending ? t("u.loadingTemplates") : t("u.noTemplates")}
          </div>
          {/* Testid because "which VERSION is active" is the claim this line makes, and it had no
              way of being asserted — the e2e test could only see that the picker closed. */}
          <div data-testid="tpl-active-meta" style={{ fontSize: 11, color: color.sec2 }}>
            {active ? `${active.template_key} · v${active.version}` : t("u.templateMeta")}
          </div>
        </div>
        {active && <span style={{ fontSize: 11, color: color.indigo, fontWeight: 600 }}>{t("u.selected")}</span>}
      </div>

      {/* Picker: the list of existing templates the analyst may select from. */}
      {pickerOpen && (
        <div style={{ border: `1px solid ${color.hairline3}`, borderRadius: 9, marginBottom: 9, overflow: "hidden" }}>
          {list.length === 0 && (
            <div style={{ padding: 12, fontSize: 11.5, color: color.muted }}>{t("u.noTemplates")}</div>
          )}
          {list.map((tpl) => {
            // Per VERSION, so the highlight tracks the row that is actually selected. Keyed on
            // template_key it marked every version of the active template at once.
            const on = active?.id === tpl.id;
            return (
              <div
                key={tpl.id}
                role="button"
                data-testid="tpl-option"
                onClick={() => { setSelectedId(tpl.id); setPickerOpen(false); }}
                style={{
                  display: "flex", alignItems: "center", gap: 9, padding: "9px 12px", cursor: "pointer",
                  background: on ? color.indigoTint : "#fff",
                  borderBottom: `1px solid ${color.hairline3}`,
                }}
              >
                <span style={{ width: 14, height: 14, borderRadius: "50%", flex: "0 0 auto",
                               border: `2px solid ${on ? color.indigo : color.controlBorder}`,
                               background: on ? color.indigo : "#fff" }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: on ? 600 : 500,
                                color: on ? color.indigo : color.ink }}>{tpl.name}</div>
                  <div style={{ fontSize: 10.5, fontFamily: font.mono, color: color.muted }}>
                    {tpl.template_key} · v{tpl.version}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {(canSelectTemplate || canTemplate) && (
        <div style={{ display: "flex", gap: 8 }}>
          {/* Analysts may choose an existing template… */}
          <Button
            variant="secondary"
            onClick={() => setPickerOpen((v) => !v)}
            style={{ flex: 1, fontSize: 11.5, padding: 8, borderRadius: radius.control }}
          >
            {pickerOpen ? t("u.closePicker") : t("u.chooseAnother")}
          </Button>
          {/* …but authoring a new template is an admin configuration action. */}
          {canTemplate && (
            <Button
              variant="ghost"
              onClick={onAuthor}
              style={{ flex: 1, fontSize: 11.5, padding: 8, borderRadius: radius.control }}
            >
              {t("u.newTemplate")}
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}

/** Rulebook card: the ontology a run started from this screen will actually read the filing
 * against — named, sized, and with its standing said plainly.
 *
 * It used to print the sample project's ontology FILENAME over a fixed "1,240 rules · 380
 * aliases" and a green "Valid" badge: three claims about no rulebook this product has ever held,
 * sitting on the screen where the analyst decides whether the run is configured correctly. Every
 * one of them now comes off the rulebook in force for the selected template, which is the same
 * question, asked the same way (`ontologyInForce`), that the extraction view's picker defaults to
 * — so the description on this screen and the rulebook the run records cannot drift apart. */
function OntologyCard({ canOntology, onManage }: { canOntology: boolean; onManage: () => void }) {
  const t = useT();
  const locale = useAppLocale();
  const { data: rows, isPending } = useOntologies();
  const { data: templates } = useTemplates();
  const selectedId = useUI((s) => s.selectedTemplateId);
  const list: TemplateRef[] = templates ?? [];
  // The same "which template version is active" rule the card above uses — one function, not a
  // second copy of it (`activeTemplate`). A rulebook targets ONE template KEY, so that is what is
  // taken from the version; the rulebook in force overall would describe a different extraction.
  const activeKey = activeTemplate(list, selectedId)?.template_key;
  const ont = ontologyInForce(rows, (o) => !activeKey || o.target_template_key === activeKey);
  const num = (n: number) => n.toLocaleString(locale);
  // Absent counts are left out rather than shown as 0 — an older server that does not serve them
  // would otherwise have this card reporting an empty rulebook.
  const size = [
    ont?.concept_count == null ? null : `${num(ont.concept_count)} ${t("u.ontConcepts")}`,
    ont?.alias_count == null ? null : `${num(ont.alias_count)} ${t("u.ontAliases")}`,
  ].filter(Boolean).join(" · ");
  const standing = ont?.superseded ? t("u.ontReplaced") : t("u.ontInForce");

  return (
    <Card>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 5 }}>{t("u.ontology")}</div>
      <p style={{ margin: "0 0 11px", fontSize: 11.5, color: color.sec2, lineHeight: 1.5 }}>
        {t("u.ontologyExplainer")}
      </p>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: 11,
          border: `1px solid ${color.hairline3}`,
          borderRadius: 9,
          marginBottom: 10,
        }}
      >
        <span
          style={{
            width: 30,
            height: 30,
            borderRadius: 7,
            background: ont?.superseded ? color.amberBg : color.greenBg2,
            color: ont?.superseded ? color.amberFg : color.greenFg,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            flex: "0 0 auto",
          }}
        >
          ◆
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div data-testid="u-rulebook" data-rulebook-id={ont?.id}
               style={{ fontSize: 12.5, fontWeight: 600 }}>
            {ont ? ont.ontology_key : isPending ? t("u.ontLoading") : t("u.ontNone")}
          </div>
          <div data-testid="u-rulebook-meta" style={{ fontSize: 11, color: color.muted }}>
            {/* While the list is in flight this line says NOTHING. "This template has no ontology
                yet" is a fact, and it is not one that is known until the rulebooks arrive — it read
                that way under "Loading rulebooks…", asserting an absence it had not established. */}
            {ont ? [`v${ont.version}`, size].filter(Boolean).join(" · ")
                 : isPending ? "" : t("u.ontNoneHint")}
          </div>
        </div>
        {ont && (
          <span
            style={{
              fontSize: 10.5,
              fontWeight: 600,
              padding: "3px 8px",
              borderRadius: radius.pill,
              background: ont.superseded ? color.amberBg : color.greenBg2,
              color: ont.superseded ? color.amberFg : color.greenFg,
            }}
          >
            {standing}
          </span>
        )}
      </div>
      {canOntology && (
        <button
          onClick={onManage}
          style={{
            width: "100%",
            fontSize: 11.5,
            fontWeight: 600,
            color: color.ink2,
            background: "#fff",
            border: `1px dashed ${color.dashed}`,
            borderRadius: radius.control,
            padding: 9,
            cursor: "pointer",
            fontFamily: font.sans,
          }}
        >
          {t("u.replaceOntology")}
        </button>
      )}
    </Card>
  );
}

export default function UploadScreen() {
  const navigate = useNavigate();
  const t = useT();
  const canTemplate = useCan("config:template"); // author/edit templates (admin)
  const canSelectTemplate = useCan("template:select"); // choose an existing one (analyst)
  const canOntology = useCan("config:ontology");
  const canUpload = useCan("documents:manage");
  const locale = useAppLocale();
  const extractMode = useUI((s) => s.extractMode);
  const setExtractMode = useUI((s) => s.setExtractMode);
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const setActiveDocumentId = useUI((s) => s.setActiveDocumentId);
  const { data, isPending } = useProject();
  const { data: docsData } = useDocuments();
  const upload = useUploadDocument();
  const del = useDeleteDocument();
  // Integrity is computed at upload; we read the active document's result to decide whether
  // "Extract directly" is allowed — a BLOCKER (corrupt/encrypted) can never be skipped.
  const docIntegrity = useDocumentIntegrity(activeDocumentId ?? undefined, locale);
  const fileRef = useRef<HTMLInputElement>(null);

  if (isPending || !data) {
    return (
      <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>
    );
  }

  // Real uploaded documents take precedence; fall back to the sample's docs when loaded.
  const realDocs = docsData?.documents ?? [];
  const documents: SourceDoc[] = realDocs.length ? realDocs : data.documents;

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) upload.mutate(file);
    e.target.value = ""; // allow re-selecting the same file
  };

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", padding: "26px 30px 60px" }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("u.title")}</h1>
        <p style={{ margin: 0, color: color.sec2 }}>{t("u.subhead")}</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.55fr 1fr", gap: 18 }}>
        {/* LEFT — Source documents */}
        <Card>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
            }}
          >
            <span style={{ fontWeight: 600, fontSize: 14 }}>{t("u.srcDocs")}</span>
            <span style={{ fontSize: 11, color: color.sec2 }}>{t("u.srcTypes")}</span>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.xlsx,.xls,.png,.jpg,.jpeg,.tif,.tiff"
            onChange={onPick}
            style={{ display: "none" }}
          />
          <div
            role={canUpload ? "button" : undefined}
            tabIndex={canUpload ? 0 : undefined}
            onClick={() => canUpload && fileRef.current?.click()}
            onKeyDown={(e) => {
              if (canUpload && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); fileRef.current?.click(); }
            }}
            onDragOver={(e) => canUpload && e.preventDefault()}
            onDrop={(e) => {
              if (!canUpload) return;
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) upload.mutate(f);
            }}
            style={{
              border: `1.5px dashed ${upload.isError ? color.redFg : color.dashed}`,
              borderRadius: 10,
              background: color.rowAltBg,
              padding: 26,
              textAlign: "center",
              marginBottom: 14,
              cursor: canUpload ? "pointer" : "not-allowed",
              opacity: canUpload ? 1 : 0.6,
            }}
          >
            <div style={{ fontSize: 26, color: color.faint, marginBottom: 6 }}>⬍</div>
            <div style={{ fontWeight: 600, marginBottom: 3 }}>
              {upload.isPending ? (
                t("u.uploading")
              ) : (
                <>
                  {t("u.dropHere")}{" "}
                  <span style={{ color: color.indigo, textDecoration: "underline", textUnderlineOffset: 2 }}>
                    {t("u.browse")}
                  </span>
                </>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: color.muted }}>{t("u.dropHint")}</div>
            {upload.isError && (
              <div style={{ fontSize: 11, color: color.redFg, marginTop: 8 }}>
                {t("u.uploadFailed")}: {(upload.error as Error)?.message}
              </div>
            )}
            {!canUpload && (
              <div style={{ fontSize: 11, color: color.muted, marginTop: 8 }}>{t("u.uploadNoPerm")}</div>
            )}
          </div>
          {documents.map((d) => (
            <DocRow
              key={d.id ?? d.name}
              doc={d}
              active={!!d.id && d.id === activeDocumentId}
              onSelect={d.id ? () => setActiveDocumentId(d.id as string) : undefined}
              deleting={del.isPending && del.variables === d.id}
              onDelete={
                d.id && canUpload
                  ? () => {
                      if (window.confirm(t("u.deleteConfirm"))) del.mutate(d.id as string);
                    }
                  : undefined
              }
            />
          ))}
        </Card>

        {/* RIGHT — Template + Ontology */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <TemplateCard
            canTemplate={canTemplate}
            canSelectTemplate={canSelectTemplate}
            onAuthor={() => navigate(SCREENS.template.path)}
          />

          <OntologyCard
            canOntology={canOntology}
            onManage={() => navigate(SCREENS.template.path)}
          />
        </div>
      </div>

      {/* 4 · Extraction mode — auto (default) vs confirm page scope */}
      <Card style={{ marginTop: 18 }}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 3 }}>{t("u.mode")}</div>
        <p style={{ margin: "0 0 12px", fontSize: 11.5, color: color.sec2, lineHeight: 1.5 }}>
          {t("u.modeHint")}
        </p>
        <div role="radiogroup" aria-label={t("u.mode")} style={{ display: "flex", gap: 12 }}>
          <ModeOption
            value="auto"
            selected={extractMode === "auto"}
            onSelect={setExtractMode}
            glyph="⚡"
            title={t("u.autoTitle")}
            desc={t("u.autoDesc")}
            badge={t("u.autoRec")}
          />
          <ModeOption
            value="confirm"
            selected={extractMode === "confirm"}
            onSelect={setExtractMode}
            glyph="▦"
            title={t("u.confirmTitle")}
            desc={t("u.confirmDesc")}
          />
        </div>
      </Card>

      {/* There is no "Save draft" here any more: there was nowhere to save to. The document is
          already persisted server-side by the upload, and everything else this screen holds
          (extraction mode, the active document) is durable client state — so the button could
          only ever have pretended. */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
        {/* Fast path: skip the integrity REVIEW screen and go straight to extraction. The
            check itself still stands — a BLOCKER document (corrupt/encrypted) can't be
            skipped, and there must be an active uploaded document to extract. */}
        {(() => {
          const noDoc = !activeDocumentId;
          const blocked = (docIntegrity.data?.issues || []).some((i) => i.note === "BLOCKER");
          const disabled = noDoc || blocked;
          const title = noDoc ? t("u.noActiveDoc") : blocked ? t("u.extractBlocked") : undefined;
          return (
            <Button
              variant="secondary"
              style={{ padding: "10px 18px" }}
              disabled={disabled}
              title={title}
              onClick={() =>
                activeDocumentId &&
                navigate(extractMode === "auto" ? SCREENS.extraction.path : SCREENS.scope.path)
              }
            >
              {t("u.extractDirectly")} →
            </Button>
          );
        })()}
        <Button style={{ padding: "10px 22px" }} onClick={() => navigate(SCREENS.integrity.path)}>
          {t("u.runIntegrity")} →
        </Button>
      </div>
    </div>
  );
}
