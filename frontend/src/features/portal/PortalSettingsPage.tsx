import { useEffect, useState } from "react";
import { PORTAL_NAME, portalTitle, usePortalInfo, useSavePortalSubtitle } from "./usePortal";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";

// 포탈 설정(2026-10-02 사용자 요청: "포탈 메인 이름의 서브네임을 포탈 운영자가 설정 -- 예: AI Storage Portal -
// SSC, DAI-CAE, DAI-OA"). 서브네임은 사이드바 이름 아래 줄, 로그인 화면, 브라우저 탭 제목에 쓰인다.
// 서버 검증(routes_portal.validate_subtitle)과 같은 상한 -- 화면은 즉답용이고 최종 판정은 서버 422 다.
const SUBTITLE_MAX = 40;

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

export function PortalSettingsPage() {
  const q = usePortalInfo();
  const save = useSavePortalSubtitle();
  const current = q.data?.subtitle ?? null;
  const [draft, setDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (q.data && draft === null) setDraft(q.data.subtitle ?? ""); }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (q.isError && !q.data) return <Card><p role="alert" className="text-bad">{errText(q.error)}</p></Card>;
  if (draft === null) return <p className="text-muted">불러오는 중…</p>;

  const value = draft.trim();
  const tooLong = value.length > SUBTITLE_MAX;
  const unchanged = value === (current ?? "");

  function onSave(next: string) {
    setSaved(false);
    save.mutate(next.trim() === "" ? null : next.trim(), {
      onSuccess: (data) => { setDraft(data.subtitle ?? ""); setSaved(true); },
    });
  }

  return (
    <section className="space-y-4 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold">포탈 설정</h1>
        <p className="mt-1 text-sm text-muted">
          포탈 이름 뒤에 붙는 서브네임(사이트·조직 이름)입니다. 사이드바 이름 아래, 로그인 화면, 브라우저 탭 제목에
          표시되고 저장하면 바로 반영됩니다.
        </p>
      </div>
      <Card className="space-y-4">
        <label className="block text-sm">서브네임
          <input aria-label="서브네임" className={field} value={draft} maxLength={SUBTITLE_MAX + 20}
                 placeholder="예: SSC, DAI-CAE, DAI-OA (비우면 서브네임 없음)"
                 aria-invalid={tooLong} aria-describedby={tooLong ? "portal-subtitle-error" : undefined}
                 onChange={(e) => { setSaved(false); setDraft(e.target.value); }} />
        </label>
        {tooLong && (
          <p id="portal-subtitle-error" role="alert" className="text-sm text-bad">
            {`서브네임은 ${SUBTITLE_MAX}자 이하여야 합니다(지금 ${value.length}자).`}
          </p>
        )}
        <p className="text-sm">
          미리보기: <strong aria-label="표시 이름 미리보기">{portalTitle(value === "" ? null : value)}</strong>
        </p>
        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button onClick={() => onSave(draft)} disabled={save.isPending || tooLong || unchanged}>저장</Button>
          {current !== null && (
            <Button variant="ghost" onClick={() => onSave("")} disabled={save.isPending}>서브네임 지우기</Button>
          )}
          {save.isPending && <span className="text-sm text-muted">저장 중…</span>}
          {saved && !save.isPending && <span role="status" className="text-sm text-ok">저장했습니다</span>}
          {save.isError && <span role="alert" className="text-sm text-bad">{errText(save.error)}</span>}
        </div>
      </Card>
      <Card className="space-y-2">
        <h2 className="text-lg font-semibold">브라우저 탭 아이콘</h2>
        <div className="flex items-center gap-3">
          <img src="/favicon.svg" alt="현재 탭 아이콘" className="h-10 w-10" />
          <p className="text-sm text-muted">
            {`${PORTAL_NAME} 의 탭 아이콘은 포탈 이미지에 포함된 파일(frontend/public/favicon.svg)입니다 — 바꾸려면 그 파일을 `
              + "교체하고 포탈 이미지를 다시 빌드하세요(사내망에서도 보이도록 외부 주소가 아니라 이미지에 넣습니다)."}
          </p>
        </div>
      </Card>
    </section>
  );
}
