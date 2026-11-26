"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileUp, Upload, X } from "lucide-react";
import { useRef, useState } from "react";
import { cx } from "@/lib/cx";
import { useProperties } from "@/hooks/queries";
import { uploadImport } from "@/services/api";
import type { ImportReport } from "@/types/api";

/**
 * Authorised-import workflow for operations staff: pick a file, validate it (dry run / preview),
 * then import. The backend does all validation, deduplication and property mapping; this panel
 * only drives it and renders the report (new / updated / unchanged / rejected and row errors).
 */
export function ImportPanel({ onImported }: { onImported?: () => void }) {
  const qc = useQueryClient();
  const props = useProperties();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [property, setProperty] = useState("");
  const [preview, setPreview] = useState<ImportReport | null>(null);

  const mutation = useMutation({
    mutationFn: (dryRun: boolean) => uploadImport({ file: file!, dryRun, property: property || undefined }),
    onSuccess: (report, dryRun) => {
      setPreview(report);
      if (!dryRun && report.status !== "failed") {
        void qc.invalidateQueries();
        onImported?.();
      }
    },
  });

  const reset = () => {
    setFile(null);
    setProperty("");
    setPreview(null);
    mutation.reset();
    if (inputRef.current) inputRef.current.value = "";
  };
  const pick = (f: File | null) => {
    setFile(f);
    setPreview(null);
    mutation.reset();
  };

  const committed = preview && !preview.dry_run && preview.status !== "failed";

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">
        Load reviews from an authorised export — a Booking.com Extranet / Guest Reviews file, or a
        channel-manager CSV or JSON. The file is validated first; nothing is saved until you import.
        Duplicate reviews are detected automatically.
      </p>

      <label
        className={cx(
          "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
          file ? "border-accent/50 bg-accent-50/50" : "border-line-strong hover:border-accent/40 hover:bg-canvas",
        )}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]);
        }}
      >
        <FileUp className="size-7 text-ink-faint" aria-hidden />
        {file ? (
          <span className="font-medium text-ink">{file.name} <span className="text-ink-faint">({(file.size / 1024).toFixed(0)} KB)</span></span>
        ) : (
          <>
            <span className="font-medium text-ink">Choose a CSV or JSON file, or drop it here</span>
            <span className="text-xs text-ink-faint">Columns are auto-detected; personal data is never imported</span>
          </>
        )}
        <input ref={inputRef} type="file" accept=".csv,.json,text/csv,application/json" className="sr-only" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
      </label>

      {file && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm text-ink-soft">
            Property for this file <span className="text-ink-faint">(only if the file has no property column)</span>
            <select className="mt-1 block w-64 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm" value={property} onChange={(e) => setProperty(e.target.value)}>
              <option value="">Detect from the file</option>
              {props.data?.data.map((p) => <option key={p.id} value={p.slug}>{p.name}</option>)}
            </select>
          </label>
          <div className="ml-auto flex gap-2">
            <button onClick={reset} className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-ink-soft hover:bg-canvas">
              <X className="mr-1 inline size-4" aria-hidden />Clear
            </button>
            <button
              onClick={() => mutation.mutate(true)}
              disabled={mutation.isPending}
              className="rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-ink hover:bg-canvas disabled:opacity-50"
            >
              Validate (preview)
            </button>
            <button
              onClick={() => mutation.mutate(false)}
              disabled={mutation.isPending || committed === true}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-on-accent hover:opacity-90 disabled:opacity-50"
            >
              <Upload className="size-4" aria-hidden />Import
            </button>
          </div>
        </div>
      )}

      {mutation.isPending && <p role="status" className="text-sm text-ink-soft">Processing {file?.name}…</p>}
      {mutation.isError && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-negative-100 bg-negative-50 p-3 text-sm text-negative-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{mutation.error instanceof Error ? mutation.error.message : "Import failed"}</span>
        </div>
      )}

      {preview && <ImportResult report={preview} committed={committed === true} />}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-lg border border-line bg-canvas px-3 py-2">
      <p className={cx("font-display text-2xl font-semibold tnum", tone === "good" && "text-positive-700", tone === "bad" && value > 0 && "text-negative-700")}>{value.toLocaleString("en-AU")}</p>
      <p className="text-xs text-ink-faint">{label}</p>
    </div>
  );
}

function ImportResult({ report, committed }: { report: ImportReport; committed: boolean }) {
  const failed = report.status === "failed";
  return (
    <div className={cx("rounded-xl border p-4", failed ? "border-negative-100 bg-negative-50" : committed ? "border-positive-100 bg-positive-50/60" : "border-line bg-canvas")}>
      <p className="mb-3 flex items-center gap-2 font-semibold text-ink">
        {failed ? <AlertTriangle className="size-4 text-negative-700" aria-hidden /> : <CheckCircle2 className="size-4 text-positive-700" aria-hidden />}
        {failed
          ? "File rejected — nothing was saved"
          : committed
            ? `Imported ${report.file_name}`
            : `Preview of ${report.file_name} (nothing saved yet)`}
      </p>

      {report.file_error ? (
        <p className="text-sm text-negative-700">{report.file_error.message}</p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            <Stat label="Rows in file" value={report.rows_total} />
            <Stat label="Valid" value={report.rows_valid} />
            <Stat label={committed ? "New" : "Would add"} value={report.new} tone="good" />
            <Stat label="Updated" value={report.updated} />
            <Stat label="Already held" value={report.unchanged} />
            <Stat label="Rejected" value={report.rows_rejected} tone="bad" />
          </div>
          {report.issues.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-xs font-medium text-ink-soft">First row problems ({report.rows_rejected} rejected in total):</p>
              <ul className="max-h-40 space-y-0.5 overflow-auto text-xs text-ink-soft">
                {report.issues.slice(0, 12).map((i, n) => (
                  <li key={n} className="tnum">
                    <span className="font-medium text-ink">Row {i.line}</span> · {i.code}
                    {i.column ? ` (${i.column})` : ""} — {i.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!committed && !failed && report.rows_valid > 0 && (
            <p className="mt-3 text-xs text-ink-faint">Looks good. Press <strong>Import</strong> to save {report.new.toLocaleString("en-AU")} new review{report.new === 1 ? "" : "s"}.</p>
          )}
        </>
      )}
    </div>
  );
}
