import { SYNC_OPTION_FIELDS, type SyncOptionField, type SyncOptionGroup } from "./helpers";

// Controlled editor for one group of batch-wide DMS sync options. `value` is the
// canonical options object (text->string, bool->true, int->number); empty/false
// entries are omitted so the stored object stays minimal. The parent wraps this
// in a collapsible ".sync-options" section. `delete` is handled separately
// (delete_enabled checkbox) and is not in SYNC_OPTION_FIELDS.
export default function SyncOptionsFields({
  group,
  value,
  onChange,
}: {
  group: SyncOptionGroup;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  function setKey(key: string, v: unknown) {
    const next = { ...value };
    if (v === undefined || v === "" || v === false) delete next[key];
    else next[key] = v;
    onChange(next);
  }

  function renderField(f: SyncOptionField) {
    if (f.kind === "bool") {
      return (
        <label key={f.key} className="check-row">
          <input
            type="checkbox"
            checked={value[f.key] === true}
            onChange={(e) => setKey(f.key, e.target.checked)}
          />
          <span>
            <code>{f.label}</code>
            {f.hint && <span className="muted small"> — {f.hint}</span>}
          </span>
        </label>
      );
    }
    return (
      <label key={f.key}>
        <span>
          <code>{f.label}</code>
          {f.hint && (
            <span className={`small ${f.warn ? "err-num" : "muted"}`}> — {f.hint}</span>
          )}
        </span>
        <input
          type={f.kind === "int" ? "number" : "text"}
          min={f.kind === "int" ? 0 : undefined}
          placeholder={f.placeholder}
          value={value[f.key] === undefined ? "" : String(value[f.key])}
          onChange={(e) =>
            setKey(
              f.key,
              f.kind === "int"
                ? e.target.value === ""
                  ? undefined
                  : Number(e.target.value)
                : e.target.value,
            )
          }
        />
      </label>
    );
  }

  return <>{SYNC_OPTION_FIELDS.filter((f) => f.group === group).map(renderField)}</>;
}
