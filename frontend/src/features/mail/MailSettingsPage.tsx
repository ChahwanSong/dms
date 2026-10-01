import { useEffect, useState } from "react";
import { useMe } from "../auth/useAuth";
import { useMailHealthCheck, useMailSettings, useMailTestSend, useSaveMailSettings } from "./useMailSettings";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { field } from "../jobs/formFields";
import { kstStampOrDash } from "../../lib/datetime";
import type { MailCheckResult, MailSettings, MailSettingsFields } from "../../lib/types";

// 포탈 메일 설정(2026-10-01 사용자 요청: "knox mail 인증에 관련된 키, relay용 knox 서버 IP, relay port(8025) 등을
// 포탈에서 설정"). 회원가입·비밀번호 재설정 인증 메일은 메신저 서버의 릴레이(knox_mail_dms_certi, TCP 8025)를
// 거쳐 Knox 메일로 나간다(src/dms/api/mailer.py). 칸마다 "비우면 환경변수·기본값" -- 서버 해석은
// mail_config.resolve_mail_config(포탈 > env > 기본, 칸별) 하나다.

// 연결 확인·테스트 메일 실패 사유 → 조치(mailer.MailerError 의 사유 표와 같은 내용). 화면 전용 표라
// reasonCodes.json 계약 밖이다(서버가 detail 이 아니라 결과 본문 reason 으로 준다).
export const MAIL_REASON_HELP: Record<string, string> = {
  relay_misconfigured: "릴레이 주소(IP·포트)나 인증 키 설정이 비었거나 잘못됐습니다 — 아래 칸을 확인하세요.",
  relay_unreachable: "릴레이에 연결하지 못했습니다 — 메신저 서버의 포트(기본 8025) 방화벽과 "
    + "systemctl status knox-mail-dms-certi 를 확인하세요.",
  relay_no_response: "릴레이에 요청은 보냈지만 응답 전에 끊기거나 타임아웃됐습니다 — 메일이 갔을 수도 있습니다. "
    + "타임아웃(초)이 릴레이의 Knox 타임아웃보다 긴지, journalctl -u knox-mail-dms-certi 를 확인하세요.",
  relay_bad_response: "그 주소에 릴레이가 아닌 다른 서비스가 응답했습니다 — IP·포트를 확인하세요.",
  unauthorized: "인증 키가 메신저 서버 knox_mail_dms_certi.env 의 RELAY_TOKEN 과 다릅니다.",
  client_not_allowed: "릴레이가 이 DMS 의 접속 IP 를 허용하지 않습니다 — knox_mail_dms_certi.env 의 "
    + "RELAY_ALLOWED_CLIENTS 에 DMS api 가 나가는 IP(api 파드가 도는 노드 IP)를 넣으세요.",
  recipient_not_allowed: "받는 사람 도메인이 knox_mail_dms_certi.env 의 RELAY_ALLOWED_DOMAINS 에 없습니다.",
  rate_limited: "릴레이의 수신자별 발송 상한(RELAY_RATE_LIMIT)에 걸렸습니다 — 잠시 뒤 다시 시도하세요.",
  knox_rejected: "Knox 가 메일을 거부했습니다(토큰 만료 등) — 메신저 서버에서 "
    + "journalctl -u knox-mail-dms-certi 로 원인을 확인하세요.",
  knox_unreachable: "메신저 서버에서 Knox 메일 API 로 연결하지 못했습니다(연결 실패·타임아웃).",
  bad_request: "릴레이가 요청 형식을 거부했습니다 — DMS 와 릴레이(knox_mail_dms_certi)의 요청 형식이 맞지 않을 수 "
    + "있습니다(상세 참고).",
  not_found: "그 주소에 릴레이 경로(/send·/healthz)가 없습니다 — 포트가 릴레이(기본 8025)가 맞는지 확인하세요.",
};

export function mailResultText(r: MailCheckResult): string {
  if (r.ok) return "";
  const reason = r.reason ?? "unknown";
  const http = /^relay_http_(\d+)$/.exec(reason);
  const help = MAIL_REASON_HELP[reason]
    ?? (http && http[1].startsWith("3")
      ? `릴레이가 다른 주소로 넘기려 했습니다(HTTP ${http[1]}) — 인증 키 유출을 막으려고 따라가지 않습니다. IP·포트를 확인하세요.`
      : http ? `릴레이가 HTTP ${http[1]} 로 답했습니다.` : "알 수 없는 오류입니다.");
  const retry = reason === "rate_limited" && r.retry_after ? ` (${r.retry_after}초 뒤 가능)` : "";
  return `${help}${retry}${r.detail ? ` — 상세: ${r.detail}` : ""} [${reason}]`;
}

// 서버 오류 문구 -- ApiError 는 사유 코드를 사람 말로 바꾼 message 를 갖고, 그 밖(네트워크 오류 등)도 빈칸이 되지 않게.
const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

const BACKEND_LABEL: Record<string, string> = {
  stub: "개발용(stub) — 메일을 보내지 않고 화면에 인증번호를 보여 줍니다",
  knox_relay: "사내 Knox 메일(knox_relay) — 메신저 서버의 릴레이를 거쳐 보냅니다",
};

// stub 은 인증번호를 요청한 사람 화면에 그대로 돌려준다 -- 아이디만 알면 누구나 남의 비밀번호를 재설정할 수 있다.
export const STUB_DANGER = "개발용(stub)은 메일을 보내지 않고 인증번호를 요청한 화면에 그대로 보여 줍니다. 아이디만 "
  + "알면 누구나 가입·비밀번호 재설정을 할 수 있으니 운영 환경에서는 쓰지 마세요.";

interface Draft {
  backend: string; relay_scheme: string; relay_host: string; relay_port: string;
  timeout_seconds: string; service_name: string;
}
const toDraft = (p: MailSettingsFields): Draft => ({
  backend: p.backend ?? "", relay_scheme: p.relay_scheme ?? "", relay_host: p.relay_host ?? "",
  relay_port: p.relay_port === null ? "" : String(p.relay_port),
  timeout_seconds: p.timeout_seconds === null ? "" : String(p.timeout_seconds),
  service_name: p.service_name ?? "",
});
const DRAFT_KEYS: (keyof Draft)[] = ["backend", "relay_scheme", "relay_host", "relay_port", "timeout_seconds",
                                     "service_name"];
// 서버 mail_config.resolve_from_row 의 주소 규칙 거울: 포탈 주소 칸(프로토콜·IP·포트)이 다 비면 env URL 원문, 하나라도
// 있으면 칸별로 포탈 > env > 기본(http·8025)을 골라 조립(IPv6 는 대괄호, IP 가 없으면 ""). "키를 다시 넣어야 하나"는
// 이 주소가 바뀌는지로 판단한다 -- 칸 문자열 비교는 같은 주소가 되는 변경(포트를 비워 기본 8025)까지 막았다.
function relayUrlOf(d: Draft, env: MailSettings["env"]): string {
  const scheme = d.relay_scheme.trim(), host = d.relay_host.trim(), port = d.relay_port.trim();
  if (scheme === "" && host === "" && port === "") return env.relay_url;
  const h = host || env.relay_host || "";
  if (h === "") return "";
  const shown = h.includes(":") ? `[${h}]` : h;
  return `${scheme || env.relay_scheme}://${shown}:${port !== "" ? Number(port) : env.relay_port}`;
}
const sameDraft = (a: Draft, b: Draft, keys = DRAFT_KEYS) => keys.every((k) => a[k].trim() === b[k].trim());
// 편집한 칸(draft != 이전 서버 값)만 남기고 나머지는 새 서버 값.
const mergeDraft = (draft: Draft, prev: Draft, next: Draft): Draft =>
  Object.fromEntries(DRAFT_KEYS.map((k) => [k, draft[k].trim() === prev[k].trim() ? next[k] : draft[k]])) as unknown as Draft;

function SourceTag({ source }: { source: "portal" | "env" | undefined }) {
  return source === "portal"
    ? <span className="ml-2 whitespace-nowrap rounded-full bg-infobg px-2 py-0.5 text-[11px] font-medium text-accent">포탈 설정</span>
    : <span className="ml-2 whitespace-nowrap rounded-full bg-panel px-2 py-0.5 text-[11px] text-muted">환경변수·기본값</span>;
}

function intError(raw: string, min: number, max: number, label: string): string | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= min && n <= max ? null : `${label}는 ${min}~${max} 사이의 정수여야 합니다`;
}

function numError(raw: string, min: number, max: number, label: string): string | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= min && n <= max ? null : `${label}은 ${min}~${max} 사이여야 합니다`;
}

function ResultLine({ r, okText }: { r: MailCheckResult | undefined; okText: string }) {
  if (!r) return null;
  return r.ok
    ? <p role="status" className="mt-2 text-sm text-ok">{okText}</p>
    : <p role="alert" className="mt-2 text-sm text-bad">{mailResultText(r)}</p>;
}

export function MailSettingsPage() {
  const q = useMailSettings();
  const me = useMe();
  const save = useSaveMailSettings();
  const health = useMailHealthCheck();
  const test = useMailTestSend();
  const s = q.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  // 입력칸이 마지막으로 맞춘 서버 포탈 값 -- draft 와 다르면 "저장 안 한 변경"이 있다.
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [token, setToken] = useState("");
  const [recipient, setRecipient] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [stubAck, setStubAck] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  // 서버 값이 바뀌면(첫 로드·저장·다른 관리자의 변경) 입력칸을 **칸별로** 맞춘다: 손대지 않은 칸은 새 서버 값을
  // 따르고, 편집 중인 칸만 남긴다(통째로 남기면 다른 관리자가 바꾼 칸을 이 화면의 저장이 되돌렸다).
  useEffect(() => {
    if (!s) return;
    const next = toDraft(s.portal);
    setDraft((d) => (d === null || baseline === null ? next : mergeDraft(d, baseline, next)));
    setBaseline(next);
  }, [s]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (s && me.data && recipient === "") setRecipient(`${me.data.actor}@${s.email_domain}`);
  }, [s?.email_domain, me.data?.actor]); // eslint-disable-line react-hooks/exhaustive-deps

  // 오류를 먼저 본다 -- 첫 조회가 실패하면 draft 가 영영 null 이라 "불러오는 중…"에 갇히던 문제. 데이터가 이미
  // 있는데 재조회만 실패했으면 폼(과 저장 안 한 입력)은 그대로 두고 위에 알린다.
  if (q.isError && !s) return <Card><p role="alert" className="text-bad">{errText(q.error)}</p></Card>;
  if (!s || !draft || !baseline) return <p className="text-muted">불러오는 중…</p>;

  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setSaved(null); setDraft({ ...draft, [k]: e.target.value });
  };
  const portError = intError(draft.relay_port, 1, 65535, "포트");
  const timeoutError = numError(draft.timeout_seconds, 1, 120, "타임아웃");
  const invalid = portError !== null || timeoutError !== null;
  const dirty = !sameDraft(draft, baseline) || token.trim() !== "";
  // 포탈에 키가 저장돼 있으면(읽을 수 없는 키 포함) 릴레이 주소가 바뀔 때 키를 같이 넣어야 한다 -- 서버
  // mail_relay_token_required 와 같은 규칙(적용 주소 비교; 주소만 돌려 테스트 메일로 키를 빼내는 경로 차단).
  const portalTokenStored = s.token.source === "portal" || s.token.unreadable;
  const tokenRequired = portalTokenStored && relayUrlOf(draft, s.env) !== relayUrlOf(baseline, s.env)
    && token.trim() === "";
  // stub 으로 바꾸는 저장은 위험을 확인한 뒤에만.
  const draftBackend = draft.backend.trim() || s.env.backend;
  const toStub = draftBackend === "stub" && s.backend !== "stub";
  const blocked = !dirty || invalid || tokenRequired || (toStub && !stubAck);

  function onSave() {
    const t = (v: string) => (v.trim() === "" ? null : v.trim());
    const all: MailSettingsFields = {
      backend: t(draft!.backend), relay_scheme: t(draft!.relay_scheme), relay_host: t(draft!.relay_host),
      relay_port: draft!.relay_port.trim() === "" ? null : Number(draft!.relay_port),
      timeout_seconds: draft!.timeout_seconds.trim() === "" ? null : Number(draft!.timeout_seconds),
      service_name: t(draft!.service_name),
    };
    // 바꾼 칸만 보낸다(서버 PUT 은 보낸 칸만 바꾼다) -- 손대지 않은 칸을 이 화면이 본 옛 값으로 덮어써 다른
    // 관리자의 변경을 되돌리지 않게.
    const fields = Object.fromEntries(DRAFT_KEYS.filter((k) => draft![k].trim() !== baseline![k].trim())
      .map((k) => [k, all[k]])) as Partial<MailSettingsFields>;
    setSaved(null);
    // 보낸 순간의 입력을 기억해 둔다 -- 응답이 오기 전에 더 고친 칸·키는 응답이 덮어쓰지 않는다.
    const sentDraft = draft!, sentToken = token;
    save.mutate({ fields, token, seenRelayUrl: s!.relay_url }, {
      onSuccess: (data) => {
        const next = toDraft(data.portal);
        setDraft((d) => (d === null ? next : mergeDraft(d, sentDraft, next))); setBaseline(next);
        setToken((t) => (t === sentToken ? "" : t));
        setStubAck(false); setSaved("저장했습니다"); health.reset(); test.reset();
      },
    });
  }

  function onClearToken() {
    // 키만 지운다 -- 편집 중인 다른 칸은 저장하지 않고 입력칸에 그대로 남는다.
    setSaved(null);
    save.mutate({ clearToken: true }, {
      onSuccess: () => { setConfirmClear(false); setSaved("포탈 인증 키를 지웠습니다"); health.reset(); test.reset(); },
    });
  }

  const envKeyFallback = s.token.env_configured && s.endpoint_source === "env";
  const tokenState = s.token.unreadable
    ? { tone: "text-bad", text: "포탈에 저장된 인증 키를 읽을 수 없습니다(세션 시크릿이 바뀌었을 수 있음) — 다시 입력하세요" }
    : s.token.configured
      ? { tone: "text-ok", text: s.token.source === "portal"
            ? "설정됨 — 포탈에 암호화해 저장(값은 다시 볼 수 없습니다)"
            : "설정됨 — 환경변수 DMS_MAIL_RELAY_TOKEN" }
      : s.token.env_unbound
        ? { tone: "text-bad", text: "미설정 — 환경변수 DMS_MAIL_RELAY_TOKEN 이 있지만 그 키는 환경변수 주소"
              + "(DMS_MAIL_RELAY_URL)에만 쓰입니다. 포탈에서 릴레이 주소를 정했으니 여기에 인증 키를 넣으세요" }
        : { tone: "text-bad", text: "미설정 — Knox 메일로 보내려면 인증 키가 필요합니다" };

  return (
    <section className="space-y-4 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold">메일 설정</h1>
        <p className="mt-1 text-sm text-muted">
          회원가입·비밀번호 재설정 인증 메일을 보내는 방법입니다. 사내 Knox 메일은 메신저 서버의 릴레이
          (knox_mail_dms_certi, TCP 8025)를 거쳐 나갑니다. 칸을 비우면 환경변수(DMS_MAILER_BACKEND·DMS_MAIL_*)
          값이나 기본값을 쓰고, 저장하면 다음 요청부터 바로 적용됩니다(재시작 불필요).
        </p>
        <p className="mt-1 text-xs text-muted">
          저장·연결 확인·테스트 메일은 포탈에 로그인한 관리자만 할 수 있습니다(API 토큰으로는 조회만 됩니다).
        </p>
      </div>

      {q.isError && (
        <p role="alert" className="text-sm text-bad">
          설정을 다시 불러오지 못했습니다 — 아래는 마지막으로 불러온 값입니다({errText(q.error)}).
        </p>
      )}

      <InfoPanel aria-label="현재 적용값">
        <p className="text-sm">
          <strong>현재 적용:</strong> {BACKEND_LABEL[s.backend] ?? `알 수 없는 발송 방식(${s.backend}) — 인증 메일이 실패합니다`}
        </p>
        <p className="mt-1 text-sm">
          릴레이 주소 <code className="rounded bg-surface px-1">{s.relay_url || "(미설정)"}</code> · 인증 키{" "}
          <span className={tokenState.tone}>{s.token.configured ? "설정됨" : s.token.unreadable ? "읽을 수 없음" : "미설정"}</span>
          {" "}· 타임아웃 {s.timeout_seconds}초 · 서비스명 「{s.service_name}」 · 받는 주소 &lt;아이디&gt;@{s.email_domain}
        </p>
      </InfoPanel>

      {s.backend === "stub" && (
        <p role="alert" aria-label="개발용 발송 경고" className="rounded-lg border border-bad px-3 py-2 text-sm text-bad">
          <strong>주의:</strong> 지금 발송 방식이 개발용(stub)입니다. {STUB_DANGER}
        </p>
      )}

      <Card className="space-y-4">
        <h2 className="text-lg font-semibold">발송 방식</h2>
        <label className="block text-sm">발송 방식 <SourceTag source={s.sources.backend} />
          <select aria-label="발송 방식" className={field} value={draft.backend}
                  onChange={(e) => { setStubAck(false); set("backend")(e); }}>
            <option value="">{`환경변수 따름 (지금: ${s.env.backend})`}</option>
            <option value="stub">{BACKEND_LABEL.stub}</option>
            <option value="knox_relay">{BACKEND_LABEL.knox_relay}</option>
          </select>
        </label>
        {toStub && (
          <div className="rounded-lg border border-bad px-3 py-2 text-sm text-bad">
            <p>{STUB_DANGER}</p>
            <label className="mt-2 flex items-center gap-2">
              <input type="checkbox" checked={stubAck} onChange={(e) => setStubAck(e.target.checked)} />
              개발·점검용으로만 쓴다는 것을 확인했습니다
            </label>
          </div>
        )}

        <h2 className="pt-2 text-lg font-semibold">릴레이 서버(메신저 서버)</h2>
        {/* 프로토콜·포트 칸은 제목 + 출처 태그("환경변수·기본값")가 한 줄에 들어가는 폭(11rem) */}
        <div className="grid gap-3 sm:grid-cols-[11rem_1fr_11rem]">
          <label className="text-sm">프로토콜 <SourceTag source={s.sources.relay_scheme} />
            <select aria-label="프로토콜" className={field} value={draft.relay_scheme} onChange={set("relay_scheme")}>
              <option value="">{`기본 (${s.env.relay_scheme})`}</option>
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
          </label>
          <label className="text-sm">릴레이 서버 IP <SourceTag source={s.sources.relay_host} />
            <input aria-label="릴레이 서버 IP" className={field} value={draft.relay_host} onChange={set("relay_host")}
                   placeholder={s.env.relay_host ? `비우면 ${s.env.relay_host}(환경변수)` : "예: 10.20.30.40"} />
          </label>
          <label className="text-sm">포트 <SourceTag source={s.sources.relay_port} />
            <input aria-label="릴레이 포트" className={field} value={draft.relay_port} onChange={set("relay_port")}
                   inputMode="numeric" placeholder={`비우면 ${s.env.relay_port}`}
                   aria-invalid={portError !== null} aria-describedby={portError ? "mail-port-error" : undefined} />
          </label>
        </div>
        {portError && <p id="mail-port-error" role="alert" className="text-sm text-bad">{portError}</p>}
        <p className="text-xs text-muted">
          http:// 와 포트를 뺀 IP(또는 호스트명)만 입력합니다. 아래 「연결 확인」은 DMS(웹서버, api 파드)에서 릴레이의
          /healthz 를 부릅니다 — 손으로 확인하려면 DMS 노드에서 curl -s http://&lt;IP&gt;:8025/healthz →
          {" "}{"{\"ok\": true}"}. 릴레이는 사내망 직통으로 부르므로 프록시 설정을 타지 않고, 다른 주소로 넘기는
          응답(리다이렉트)은 따라가지 않습니다.
        </p>

        <h2 className="pt-2 text-lg font-semibold">인증 키(RELAY_TOKEN)</h2>
        <p className={`text-sm ${tokenState.tone}`} role="status" aria-label="인증 키 상태">{tokenState.text}</p>
        <label className="block text-sm">새 인증 키
          <input aria-label="새 인증 키" type="password" autoComplete="new-password" className={field}
                 value={token} onChange={(e) => { setSaved(null); setToken(e.target.value); }}
                 placeholder="비우면 지금 키를 그대로 둡니다"
                 aria-invalid={tokenRequired} aria-describedby={tokenRequired ? "mail-token-required" : undefined} />
        </label>
        {tokenRequired && (
          <p id="mail-token-required" role="alert" className="text-sm text-bad">
            릴레이 주소(프로토콜·IP·포트)를 바꾸려면 인증 키를 다시 입력해야 합니다 — 저장된 키가 새 주소로 보내지지
            않게 하려는 것입니다.
          </p>
        )}
        <p className="text-xs text-muted">
          메신저 서버 knox_mail_dms_certi.env 의 RELAY_TOKEN 과 같은 값입니다. 브라우저에서 봉인돼 전송되고 서버에는
          암호화해 저장됩니다 — 저장 뒤에는 화면·로그 어디에서도 다시 볼 수 없습니다. 키는 저장할 때의 릴레이 주소에
          묶여, 주소를 바꾸면 다시 입력해야 합니다.
        </p>
        {portalTokenStored && !confirmClear && (
          <Button variant="ghost" onClick={() => setConfirmClear(true)} disabled={save.isPending}>
            포탈에 저장된 인증 키 지우기
          </Button>
        )}
        {portalTokenStored && confirmClear && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-bad">
              {envKeyFallback ? "지우면 환경변수 키(DMS_MAIL_RELAY_TOKEN)로 돌아갑니다."
                : "지우면 인증 키가 없어 Knox 메일 발송이 실패합니다."} 인증 키만 지우고 다른 칸은 저장하지 않습니다.
            </span>
            <Button variant="ghost" onClick={onClearToken} disabled={save.isPending}>지우기 확인</Button>
            <Button variant="ghost" onClick={() => setConfirmClear(false)} disabled={save.isPending}>취소</Button>
          </div>
        )}

        <h2 className="pt-2 text-lg font-semibold">발송 옵션</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">타임아웃(초) <SourceTag source={s.sources.timeout_seconds} />
            <input aria-label="타임아웃(초)" className={field} value={draft.timeout_seconds}
                   onChange={set("timeout_seconds")} inputMode="decimal"
                   placeholder={`비우면 ${s.env.timeout_seconds}`}
                   aria-invalid={timeoutError !== null}
                   aria-describedby={timeoutError ? "mail-timeout-error" : undefined} />
            <span className="mt-1 block text-xs text-muted">
              릴레이의 KNOX_CONNECT_TIMEOUT_SECONDS(3) + KNOX_TIMEOUT_SECONDS(10) 보다 길게(기본 20초).
            </span>
          </label>
          <label className="text-sm">서비스명 <SourceTag source={s.sources.service_name} />
            <input aria-label="서비스명" className={field} value={draft.service_name} onChange={set("service_name")}
                   placeholder={`비우면 ${s.env.service_name}`} />
            <span className="mt-1 block text-xs text-muted">메일 제목·본문에 표시됩니다: [서비스명] 회원가입 인증번호 안내</span>
          </label>
        </div>
        {timeoutError && <p id="mail-timeout-error" role="alert" className="text-sm text-bad">{timeoutError}</p>}
        <p className="text-xs text-muted">
          받는 주소는 &lt;아이디&gt;@{s.email_domain} 입니다(환경변수 DMS_ACCOUNT_EMAIL_DOMAIN — 여기서 바꾸지 않습니다).
        </p>

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button onClick={onSave} disabled={save.isPending || blocked}>저장</Button>
          {save.isPending && <span className="text-sm text-muted">저장 중…</span>}
          {saved && !save.isPending && <span role="status" className="text-sm text-ok">{saved}</span>}
          {save.isError && <span role="alert" className="text-sm text-bad">{errText(save.error)}</span>}
          <span className="ml-auto text-xs text-muted">
            마지막 수정 {kstStampOrDash(s.updated_at)}{s.updated_by ? ` · ${s.updated_by}` : ""}
          </span>
        </div>
      </Card>

      <Card className="space-y-4">
        <h2 className="text-lg font-semibold">점검</h2>
        <p className="text-sm text-muted">저장된 설정으로 확인합니다 — 바꾼 값은 먼저 저장하세요.</p>
        {dirty && (
          <p role="status" className="text-sm text-bad">
            저장하지 않은 변경이 있습니다 — 아래 점검은 저장된 값({s.relay_url || "주소 미설정"})으로 합니다.
          </p>
        )}
        <div>
          <Button variant="ghost" onClick={() => health.mutate()} disabled={health.isPending}>
            {health.isPending ? "확인 중…" : "연결 확인(/healthz)"}
          </Button>
          <ResultLine r={health.data} okText={`릴레이 응답 정상 — ${health.data?.relay_url ?? ""}`} />
          {health.isError && <p role="alert" className="mt-2 text-sm text-bad">{errText(health.error)}</p>}
        </div>
        <div>
          <label className="block text-sm">테스트 메일 받는 사람
            <input aria-label="테스트 메일 받는 사람" className={field} value={recipient}
                   onChange={(e) => setRecipient(e.target.value)} />
          </label>
          <Button className="mt-2" variant="ghost" onClick={() => test.mutate(recipient.trim())}
                  disabled={test.isPending || recipient.trim() === ""}>
            {test.isPending ? "보내는 중…" : "테스트 메일 보내기"}
          </Button>
          <p className="mt-1 text-xs text-muted">
            발송 방식과 관계없이 릴레이 → Knox 경로를 끝까지 태워 봅니다(knox_relay 로 바꾸기 전 확인용).
          </p>
          <ResultLine r={test.data} okText={`테스트 메일을 보냈습니다 — ${test.data?.to ?? ""} 메일함을 확인하세요`} />
          {test.isError && <p role="alert" className="mt-2 text-sm text-bad">{errText(test.error)}</p>}
        </div>
      </Card>
    </section>
  );
}
