import { useRef, useState, type ChangeEvent } from "react";
import { parseRequestsCsv, type BackupRow } from "./helpers";

// CSV/text popup for batch requests. Two modes:
//   "view"    — read-only text (current items or template) with a Copy button.
//   "replace" — editable textarea to paste (or load a file) then replace-all.
// The testbed is plain HTTP (not a secure context) so navigator.clipboard is
// unavailable; copy falls back to selecting the textarea + execCommand('copy').
export default function BackupCsvModal({
  title,
  hint,
  initialText,
  mode,
  busy,
  onReplace,
  onClose,
}: {
  title: string;
  hint?: string;
  initialText: string;
  mode: "view" | "replace";
  busy?: boolean;
  onReplace?: (rows: BackupRow[]) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(initialText);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function copy() {
    const ta = taRef.current;
    if (!ta) return;
    ta.focus();
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    if (!ok && navigator.clipboard) navigator.clipboard.writeText(ta.value).catch(() => {});
    ta.setSelectionRange(0, 0);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  async function loadFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setText(await f.text());
    setErr(null);
  }

  function replace() {
    const parsed = parseRequestsCsv(text);
    if (parsed.errors.length) {
      setErr(`CSV 오류: ${parsed.errors.join("; ")}`);
      return;
    }
    if (!parsed.rows.length) {
      setErr('내용에 항목이 없습니다 (한 줄에 "출발경로,대상경로").');
      return;
    }
    setErr(null);
    onReplace?.(parsed.rows);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="ghost" onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="form">
          {hint && <p className="muted small">{hint}</p>}
          <textarea
            ref={taRef}
            className="csv-text mono"
            value={text}
            readOnly={mode === "view"}
            spellCheck={false}
            rows={12}
            placeholder={mode === "replace" ? "출발경로,대상경로   (한 줄에 한 항목)" : undefined}
            onChange={(e) => {
              setText(e.target.value);
              setErr(null);
            }}
          />
          {err && <div className="banner err">{err}</div>}
          <div className="modal-actions">
            {mode === "view" ? (
              <button className="primary" onClick={copy} disabled={!text.trim()}>
                {copied ? "복사됨!" : "복사"}
              </button>
            ) : (
              <>
                <button className="ghost" onClick={() => fileRef.current?.click()} disabled={busy}>
                  파일에서 불러오기
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,.txt,text/csv,text/plain"
                  hidden
                  onChange={loadFile}
                />
                <button className="primary" onClick={replace} disabled={busy || !text.trim()}>
                  {busy ? "교체 중…" : "전체 교체"}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
