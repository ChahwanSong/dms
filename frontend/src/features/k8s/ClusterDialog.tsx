import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { downloadText } from "../../lib/csvExport";
import { kstStamp } from "../../lib/datetime";
import { useSaveCluster } from "./useK8s";
import { TOKEN_LABEL, TLS_MODE_LABEL, daysUntil } from "./k8sTerms";
import { READER_CA_CMD, READER_CHECK_CMD, READER_TOKEN_CMD, READER_YAML } from "./readerManifest";
import type { ClusterBody, ClusterSummary, TlsMode } from "./types";

// 클러스터 등록·편집(설계 §5 POST/PUT /api/admin/k8s/clusters, §7). 토큰은 **쓰기 전용**이다: 저장된 값은 화면에 오지 않고
// (「설정됨」만), 새로 넣은 값은 브라우저에서 봉인돼 token_enc 로만 실린다(useK8s.useSaveCluster → sendWithSealedSecret,
// 용도 k8s_cluster_token · AAD 사용자 자리 = 클러스터 이름). API 서버 주소를 바꾸면 토큰을 다시 넣어야 저장된다 -- 저장된
// 토큰이 운영자가 모르는 새 주소로 보내지는 길을 막는다(서버 k8s_token_required 와 같은 규칙, 메일 설정 relayUrlOf 선례).

/** https://host[:port] 만(설계 §4.2 invalid_k8s_api_server — 경로·쿼리·자격증명 금지). 빈 값은 「필수」로 따로 센다. */
export function apiServerError(v: string): string | null {
  const s = v.trim();
  if (s === "") return null;
  return /^https:\/\/[^\s/?#@]+$/.test(s) ? null
    : "https://주소[:포트] 형식이어야 합니다 — 경로·쿼리 없이(예: https://10.10.10.20:6443, https://k8s-api.example.com:6443)";
}

/** 이름은 주소(경로·「:sync」)에 들어간다 -- 공백·/·:·?·#·% 만 화면에서 막고 나머지 규칙은 서버(invalid_k8s_cluster_name)가 정한다. */
export function clusterNameError(v: string): string | null {
  const s = v.trim();
  if (s === "") return null;
  return /[\s/:?#%]/.test(s) ? "이름에 공백·/·:·?·#·% 는 쓸 수 없습니다" : null;
}

/** CA: PEM 원문 또는 그 base64(Secret 의 data.ca.crt 그대로). 내용 검증은 서버(k8s_ca_invalid). */
export function caError(v: string): string | null {
  const s = v.trim();
  if (s === "" || s.includes("-----BEGIN CERTIFICATE-----") || /^[A-Za-z0-9+/=\s]+$/.test(s)) return null;
  return "PEM(-----BEGIN CERTIFICATE-----로 시작) 또는 그 base64 값이어야 합니다";
}

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

/** 읽기 전용 글상자 + 복사·파일로 저장. 복사는 글을 전체 선택한 뒤 execCommand("copy") 다 -- 비보안(http) 컨텍스트에서는
 *  navigator.clipboard 가 없을 수 있어 clipboard API 를 쓰지 않는다(BatchDetail·BuildHistory 규약). 실패하면 선택만 해 두고
 *  단축키를 안내한다. */
export function CopyArea({ label, text, filename, rows = 10 }: {
  label: string; text: string; filename: string; rows?: number;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const copy = () => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.select();
    let ok = false;
    try {
      ok = typeof document.execCommand === "function" && document.execCommand("copy");
    } catch { ok = false; }
    setMsg(ok ? "복사했습니다" : "전체 선택했습니다 — Ctrl+C 로 복사하세요");
  };
  return (
    <div className="space-y-2">
      <textarea ref={ref} aria-label={label} readOnly value={text} rows={rows} spellCheck={false}
                onFocus={(e) => e.currentTarget.select()}
                className="w-full rounded-lg border border-line bg-panel px-3 py-2 font-mono text-xs leading-5" />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" className="whitespace-nowrap" onClick={copy}>복사</Button>
        <Button type="button" variant="ghost" className="whitespace-nowrap"
                onClick={() => downloadText(filename, text, "application/yaml;charset=utf-8")}>파일로 저장</Button>
        <span role="status" className="text-xs text-ink/70">{msg}</span>
      </div>
    </div>
  );
}

function Cmd({ children }: { children: string }) {
  return (
    <code className="block select-all rounded-md bg-panel px-2 py-1 font-mono text-[11px] [overflow-wrap:anywhere]">
      {children}
    </code>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

const TLS_OPTIONS: { value: TlsMode; help: string }[] = [
  { value: "ca", help: "대상 클러스터의 CA 로 API 서버 인증서를 검증합니다(권장 — 자체 서명 클러스터는 이것)." },
  { value: "default", help: "공인 CA 가 서명한 인증서면 호출 노드 이미지의 기본 신뢰 저장소로 검증합니다." },
  { value: "insecure", help: "인증서를 검증하지 않습니다 — 시험용으로만." },
];

export function ClusterDialog({ mode, cluster, trigger }: {
  mode: "create" | "edit"; cluster?: ClusterSummary; trigger: ReactNode;
}) {
  const ids = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [apiServer, setApiServer] = useState("");
  const [tlsMode, setTlsMode] = useState<TlsMode>("ca");
  const [ca, setCa] = useState("");
  const [tlsName, setTlsName] = useState("");
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(true);
  const save = useSaveCluster();

  // **열 때만** 서버 값으로 맞춘다(닫을 때 저장 오류도 비운다 -- 다시 열었는데 낡은 오류가 남지 않게, StorageDialog 관례).
  // cluster 를 의존성에 넣지 않는다: 개요는 10초(동기화 중 3초)마다 다시 읽어 카드의 cluster 객체가 바뀌는데, 그때마다
  // 편집 중인 칸(토큰 포함)이 서버 값으로 되돌아갔다.
  useEffect(() => {
    if (!open) { save.reset(); return; }
    setName(cluster?.name ?? ""); setApiServer(cluster?.api_server ?? "");
    setTlsMode(cluster?.tls_mode ?? "ca"); setCa(""); setTlsName(cluster?.tls_server_name ?? "");
    setToken(""); setEnabled(cluster?.enabled ?? true);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const editing = mode === "edit" && cluster !== undefined;
  const nameErr = clusterNameError(name);
  const apiErr = apiServerError(apiServer);
  const caErr = tlsMode === "ca" ? caError(ca) : null;
  // 편집에서 CA 칸을 비우면 저장된 CA 를 그대로 쓴다 -- 저장된 CA 가 없던 클러스터(기본 신뢰·검증 안 함)를 CA 로 바꿀 때는 필수.
  const caKept = editing && cluster!.tls_mode === "ca";
  const caMissing = tlsMode === "ca" && ca.trim() === "" && !caKept;
  // 토큰은 「검증된 그 서버」에 묶인다(서버 규칙과 같음 — routes_k8s.update_cluster): 주소·TLS 서버 이름이 바뀌거나 검증을
  // 끄는 쪽이면 토큰을 다시 받는다. CA 교체(인증서 갱신)와 검증을 다시 켜는 쪽은 묻지 않는다.
  const addressChanged = editing && apiServer.trim() !== cluster!.api_server;
  const serverNameChanged = editing && (tlsName.trim() || null) !== (cluster!.tls_server_name || null);
  const verifyTurnedOff = editing && tlsMode === "insecure" && cluster!.tls_mode !== "insecure";
  const tokenReason = addressChanged ? "API 서버 주소를 바꾸려면" : serverNameChanged ? "TLS 서버 이름을 바꾸려면"
    : verifyTurnedOff ? "인증서 검증을 끄려면" : null;
  const tokenRequired = tokenReason !== null && token.trim() === "";
  // 등록은 토큰 필수(서버 create_cluster 422 k8s_token_required) — 편집의 빈 칸은 「저장된 토큰 유지」다.
  const missing = [
    name.trim() === "" ? "클러스터 이름" : null,
    apiServer.trim() === "" ? "API 서버 주소" : null,
    caMissing ? "CA 인증서" : null,
    !editing && token.trim() === "" ? "API 토큰" : null,
  ].filter((x): x is string => x !== null);
  const blocked = missing.length > 0 || nameErr !== null || apiErr !== null || caErr !== null || tokenRequired;

  function submit() {
    if (blocked) return;
    const body: ClusterBody = {
      name: editing ? cluster!.name : name.trim(),
      api_server: apiServer.trim(),
      tls_server_name: tlsName.trim() === "" ? null : tlsName.trim(),
      insecure_skip_tls_verify: tlsMode === "insecure",
      enabled,
    };
    // ca_pem: 새 CA 를 넣었으면 그 값, CA 방식인데 비웠으면 생략(저장된 값 유지), 다른 방식이면 null(지움).
    if (tlsMode === "ca") { if (ca.trim() !== "") body.ca_pem = ca.trim(); }
    else body.ca_pem = null;
    save.mutate({ mode, original: cluster?.name, body, token }, { onSuccess: () => setOpen(false) });
  }

  const tokenHelpId = `${ids}-token-help`;
  const tokenReqId = `${ids}-token-required`;
  const expDays = editing ? daysUntil(cluster!.token_expires_at, Date.now()) : null;

  return (
    <Dialog open={open} onOpenChange={setOpen} size="lg" busy={save.isPending}
            title={editing ? `클러스터 편집 — ${cluster!.name}` : "클러스터 등록"} trigger={trigger}>
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <Section title="연결">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
            <label className="block min-w-0">클러스터 이름
              <input aria-label="클러스터 이름" className={field} value={editing ? cluster!.name : name}
                     disabled={editing} autoComplete="off" spellCheck={false}
                     placeholder="예: prod-a" onChange={(e) => setName(e.target.value)}
                     aria-invalid={nameErr !== null}
                     aria-describedby={nameErr ? `${ids}-name-err` : undefined} />
            </label>
            <label className="block min-w-0">API 서버 주소
              <input aria-label="API 서버 주소" className={`${field} font-mono text-[13px]`} value={apiServer}
                     autoComplete="off" spellCheck={false} inputMode="url"
                     placeholder="https://10.10.10.20:6443" onChange={(e) => setApiServer(e.target.value)}
                     aria-invalid={apiErr !== null}
                     aria-describedby={apiErr ? `${ids}-api-err` : `${ids}-api-help`} />
            </label>
          </div>
          {nameErr && <p id={`${ids}-name-err`} className="text-bad">{nameErr}</p>}
          {apiErr
            ? <p id={`${ids}-api-err`} className="text-bad">{apiErr}</p>
            : <p id={`${ids}-api-help`} className="text-xs text-ink/70">
                IP 나 도메인 모두 됩니다. 이 주소는 DMS 서버가 아니라 아래 「호출 노드」(primary·secondary)에서 부릅니다 — 대상
                API 서버의 방화벽에 호출 노드 IP 를 허용하세요. 이름은 등록 뒤 바꿀 수 없습니다.
              </p>}
          <label className="flex items-center gap-2">
            <input type="checkbox" aria-label="동기화 사용" checked={enabled}
                   onChange={(e) => setEnabled(e.target.checked)} />
            동기화 사용
            <span className="text-xs text-ink/70">끄면 주기 동기화를 멈추고 마지막 스냅숏은 그대로 둡니다</span>
          </label>
        </Section>

        <Section title="TLS">
          <fieldset className="space-y-1.5">
            <legend className="mb-1 text-ink/80">TLS 검증</legend>
            {TLS_OPTIONS.map((o) => (
              <label key={o.value}
                     className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 ${
                       tlsMode === o.value ? (o.value === "insecure" ? "border-bad/50 bg-badbg/40" : "border-accent/50 bg-infobg")
                         : "border-line"}`}>
                <input type="radio" name={`${ids}-tls`} className="mt-0.5" value={o.value}
                       aria-label={TLS_MODE_LABEL[o.value]} checked={tlsMode === o.value}
                       onChange={() => setTlsMode(o.value)} />
                <span>
                  <span className="font-medium">{TLS_MODE_LABEL[o.value]}</span>
                  <span className="block text-xs text-ink/70">{o.help}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {tlsMode === "ca" && (
            <label className="block">CA 인증서(PEM)
              <textarea aria-label="CA 인증서(PEM)" className={`${field} h-28 font-mono text-xs`} value={ca}
                        spellCheck={false} onChange={(e) => setCa(e.target.value)}
                        placeholder={caKept ? "비우면 저장된 CA 를 그대로 씁니다"
                          : "-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"}
                        aria-invalid={caErr !== null}
                        aria-describedby={caErr ? `${ids}-ca-err` : `${ids}-ca-help`} />
              {caErr
                ? <span id={`${ids}-ca-err`} className="mt-1 block text-bad">{caErr}</span>
                : <span id={`${ids}-ca-help`} className="mt-1 block text-xs text-ink/70">
                    PEM 원문이나 리더 Secret 의 data.ca.crt(base64) 를 그대로 붙여넣으세요.
                  </span>}
            </label>
          )}
          {tlsMode === "insecure" && (
            <p className="rounded-lg border border-bad/40 bg-badbg/50 px-3 py-2 text-bad">
              인증서를 검증하지 않으면 같은 주소를 가로챈 쪽이 API 서버 행세를 하며 토큰을 받아 갈 수 있습니다. 시험용으로만
              쓰고, 운영에서는 「지정한 CA로 검증」을 쓰세요.
            </p>
          )}
          <label className="block">TLS 서버 이름
            <input aria-label="TLS 서버 이름" className={field} value={tlsName}
                   autoComplete="off" spellCheck={false} placeholder="비우면 주소의 호스트로 검증"
                   onChange={(e) => setTlsName(e.target.value)} aria-describedby={`${ids}-sni-help`} />
            <span id={`${ids}-sni-help`} className="mt-1 block text-xs text-ink/70">
              인증서의 SAN 에 접속 주소가 없을 때만 — 예: IP 로 접속하는데 인증서엔 kubernetes.default 같은 이름만 있는 경우.
            </span>
          </label>
        </Section>

        <Section title="API 토큰">
          {editing && (
            <p className={cluster!.token === "set" ? "text-ink/80" : "text-bad"}>
              {`지금 토큰: ${TOKEN_LABEL[cluster!.token] ?? cluster!.token}`}
              {cluster!.token === "set" && " — 값은 다시 볼 수 없습니다"}
              {cluster!.token_expires_at && (
                <span className={expDays !== null && expDays < 14 ? "text-bad" : "text-ink/70"}>
                  {` · 만료 ${kstStamp(cluster!.token_expires_at)}`}
                </span>
              )}
            </p>
          )}
          <label className="block">API 토큰
            <input aria-label="API 토큰" type="password" autoComplete="new-password" spellCheck={false}
                   className={`${field} font-mono text-[13px]`} value={token}
                   onChange={(e) => setToken(e.target.value)}
                   placeholder={editing ? "비우면 지금 토큰을 그대로 둡니다" : "리더 ServiceAccount 의 장기 토큰"}
                   aria-invalid={tokenRequired}
                   aria-describedby={tokenRequired ? `${tokenReqId} ${tokenHelpId}` : tokenHelpId} />
          </label>
          {tokenRequired && (
            <p id={tokenReqId} className="text-bad">
              {tokenReason} 토큰을 다시 넣어야 합니다 — 저장된 토큰이 검증되지 않은 서버로 보내지지 않게 하려는 것입니다.
            </p>
          )}
          <p id={tokenHelpId} className="text-xs text-ink/70">
            브라우저에서 봉인돼 전송되고 서버에는 암호화해 저장됩니다 — 저장 뒤에는 화면·로그 어디에서도 다시 볼 수 없습니다.
            Secret 의 data.token(base64) 을 그대로 붙여넣어도 됩니다.
            {editing && " 비워 두면 저장된 토큰을 그대로 씁니다."}
          </p>
        </Section>

        <Section title="대상 클러스터에 만들 읽기 전용 계정">
          <details open={!editing} className="group space-y-2">
            <summary className="cursor-pointer text-accent">리더 ServiceAccount·ClusterRole·토큰 Secret 만들기</summary>
            <p className="text-xs text-ink/70">
              DMS 는 이 계정으로 rolebindings·PVC·PV·StorageClass 를 목록으로 읽기만 합니다(쓰기 없음). 대상 클러스터에서
              kubectl apply -f 로 만든 뒤 아래 명령으로 토큰과 CA 를 꺼내 위 칸에 붙여넣으세요.
            </p>
            <CopyArea label="리더 계정 YAML" text={READER_YAML} filename="dms-inventory-reader.yaml" rows={12} />
            <div className="space-y-1 text-xs">
              <p className="text-ink/70">토큰</p><Cmd>{READER_TOKEN_CMD}</Cmd>
              <p className="text-ink/70">CA</p><Cmd>{READER_CA_CMD}</Cmd>
              <p className="text-ink/70">권한 확인(yes 여야 함)</p><Cmd>{READER_CHECK_CMD}</Cmd>
            </div>
          </details>
        </Section>

        {save.isError && <p role="alert" className="text-bad">{errText(save.error)}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
          {missing.length > 0 && <span className="mr-auto text-xs text-ink/70">{`필요: ${missing.join(" · ")}`}</span>}
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={save.isPending || blocked}>
            {save.isPending ? "저장 중…" : editing ? "저장" : "등록"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
