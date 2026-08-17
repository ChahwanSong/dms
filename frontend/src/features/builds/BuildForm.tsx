import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, Check, ChevronRight, Info, Loader } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { InfoCard } from "../../components/ui/InfoCard";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError, REASON_MESSAGES } from "../../lib/api";
import { isTerminal } from "../../lib/jobState";
import { useControlState } from "../control/useControlState";
import { useBuilds, useSubmitBuild } from "./useBuilds";
import { BuildTabs } from "./BuildTabs";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

// 콘텐츠 컬럼. **왼쪽 기준선**이다 -- mx-auto 로 가운데에 모으던 것(bfc55fd)은 이
// 앱에서 이 화면 하나뿐이라, 사이드바에서 넘어오면 글줄이 혼자 가운데로 튀어
// 보였다(사용자 지적). 다른 제출 화면과 같은 관례로 맞춘다: BatchCreate 는
// `max-w-2xl`, SubmitJob·SubmitScan 은 `max-w-xl` 이고 셋 다 mx-auto 가 없다.
// 폭을 2xl 로 잡은 근거는 폼 내용이다 -- 확인 박스·안내 카드가 두 줄짜리 문장을
// 담아 xl(36rem)에서는 접히고, 3xl 은 체크박스 세 줄이 허허벌판이 된다.
const COLUMN = "w-full max-w-2xl";

const ICON = "h-4 w-4 shrink-0";

// 빌드 이미지 3종과 의존 순서: dms-mpifileutils → dms → dms-agent.
// 기본 체크는 dms만이다 — dms-mpifileutils는 소스에서 컴파일해 매우 오래 걸린다.
const IMAGES = ["dms-mpifileutils", "dms", "dms-agent"] as const;

// repositories/builds.py:BUILD_IMAGES 의 주석이 말하는 의존 관계의 화면 쪽 미러:
// dms-agent 의 Dockerfile 은 앞의 둘을 **같은 태그로** FROM 한다. 함께 빌드하지
// 않으면 그 태그가 레지스트리에 이미 있어야 성공한다 — 없으면 파드가 pull 에서
// 죽고, 사용자는 "왜 agent 만 실패하지"를 로그에서 찾아야 한다.
const AGENT_DEPS = ["dms", "dms-mpifileutils"] as const;

const DEFAULT_IMAGES = ["dms"];

const HISTORY_PATH = "/admin/builds/history";

// 파란 안내 카드가 여는 팝업. 카드에는 2줄만 두고 전문은 전부 여기 있다 --
// "평소엔 최소, 원하면 클릭해서 전문"이 이 화면의 밀도 규칙이다. 이 글을 폼 옆에
// 캡션으로 늘어놓던 것이 직전 판(17b5faa)의 밀도 문제였다.
function ProcedureDialog({ open, onOpenChange }: {
  open: boolean; onOpenChange: (o: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="빌드 절차 안내"
            trigger={<span aria-hidden="true" />}>
      <ol className="space-y-3 text-sm">
        <li>
          <span className="font-medium">1. 프리플라이트</span>
          <span className="block text-muted">
            빌드 노드에서 egress(인터넷)·레지스트리 접근·디스크 여유 세 가지를 먼저
            검사합니다. 실패하면 수 초~수십 초 안에 사유와 함께 끝납니다.
          </span>
        </li>
        <li>
          <span className="font-medium">2. 빌드</span>
          <span className="block text-muted">
            빌드 노드의 로컬 소스 경로에서 스냅샷을 떠 이미지를 만듭니다 — 커밋·push
            하지 않은 변경도 포함됩니다(이력의 커밋에 -dirty 로 표시). 베이스 이미지·
            의존성 다운로드에 인터넷이 필요합니다 — 런타임(배포 환경)은 airgap 이라
            빌드 때만 열립니다.
          </span>
        </li>
        <li>
          <span className="font-medium">3. push</span>
          <span className="block text-muted">
            만든 이미지를 사내 레지스트리로 올립니다. 태그는 지정한 값, 지정하지
            않으면 b + 빌드ID 앞 8자입니다.
          </span>
        </li>
      </ol>
      <div className="mt-4 space-y-2 border-t border-line pt-3 text-sm">
        <p className="text-muted">
          이미지 의존성 — dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM 합니다.
          함께 빌드하거나, 그 태그가 레지스트리에 이미 있어야 합니다.
        </p>
        <p className="text-muted">
          배포는 별도 — 빌드는 레지스트리 push 까지만 합니다. 새 태그를 실제로 굴리는
          것은 릴리스·배포 단계의 일입니다.
        </p>
      </div>
      <div className="flex justify-end pt-4">
        <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>닫기</Button>
      </div>
    </Dialog>
  );
}

/** 빌드하기 — 「빌드」의 기본 하위 페이지(제출 폼 전용). 목록은 BuildHistory. */
export function BuildForm() {
  const q = useBuilds();
  const controlQ = useControlState();
  const submitBuild = useSubmitBuild();
  const navigate = useNavigate();
  const [tag, setTag] = useState("");
  const [images, setImages] = useState<string[]>(DEFAULT_IMAGES);
  const [guideOpen, setGuideOpen] = useState(false);

  const buildNodeName = controlQ.data?.build_node_name ?? null;
  const sourcePath = controlQ.data?.build_source_path ?? null;
  // 목록은 이 화면의 주인공이 아니라 **재료**다(진행 중 배너). 조회가
  // 실패해도 폼은 막지 않는다 -- 제출은 목록과 무관하게 성립한다.
  const builds = useMemo(() => (Array.isArray(q.data) ? q.data : []), [q.data]);
  const canSubmit = buildNodeName !== null && sourcePath !== null && images.length > 0;

  // 백엔드는 동시 1건만 허용한다(build_in_progress 409). 제출 **전에** 알려 주면
  // 헛클릭 한 번과 빨간 오류 한 줄을 아낀다. 막지는 않는다: 목록은 최대 5초
  // 뒤처질 수 있어(useBuilds 폴링) "진행 중"이 이미 끝난 것일 수 있다.
  const activeCount = builds.filter((b) => !isTerminal(b.state)).length;

  // dms-agent 를 고르면서 그 FROM 대상을 이번 빌드에 안 넣은 것들.
  const wantsAgent = images.includes("dms-agent");
  const missingDeps = wantsAgent ? AGENT_DEPS.filter((d) => !images.includes(d)) : [];

  const toggleImage = (name: string) => {
    setImages((prev) => (prev.includes(name) ? prev.filter((i) => i !== name) : [...prev, name]));
  };

  const submit = () => {
    submitBuild.mutate({ images, tag: tag.trim() === "" ? null : tag.trim() }, {
      // 제출 직후 알고 싶은 것은 "지금 어떻게 되고 있나"다. 목록이 이력으로 나간
      // 뒤로는 폼에 남는 것이 곧 **방금 만든 빌드가 어디에도 안 보이는** 상태라,
      // 성공하면 이력으로 옮겨 준다(방금 것이 맨 위에 선다).
      // 실패면 이동하지 않는다 -- 오류(409·422)는 고칠 수 있는 화면 옆에 서야 한다.
      onSuccess: () => navigate(HISTORY_PATH),
    });
  };

  // 「취소」는 화면을 떠나지 않고 폼만 초기화한다 -- 빌드하기는 「빌드」의 기본
  // 화면이라 되돌아갈 "이전 화면"이 없다(이력으로 튕기면 취소가 아니라 이동이다).
  // 지난 제출 오류도 함께 지운다(낡은 409 가 남지 않게).
  const cancel = () => {
    setTag("");
    setImages(DEFAULT_IMAGES);
    submitBuild.reset();
  };

  return (
    <section className={`${COLUMN} space-y-4`}>
      {/* 제목 + 한 줄 부제: "무엇을 하는 화면인지"를 한 문장으로 못 박는다.
          탭은 제목 아래에 둔다 -- 제목이 "여기가 빌드"를, 탭이 "빌드 안의 어디"를
          말하는 순서다. */}
      <header>
        <h1 className="text-2xl font-bold">빌드</h1>
        <p className="text-muted mt-1">소스를 이미지로 빌드해 레지스트리에 push 합니다</p>
      </header>
      <BuildTabs />

      {activeCount > 0 && (
        <InfoPanel className="border border-line flex items-center gap-2">
          <Loader className={`${ICON} text-busy`} aria-hidden />
          <span>
            진행 중인 빌드가 있습니다 —{" "}
            <Link className="text-accent underline" to={HISTORY_PATH}>이력에서 보기</Link>
          </span>
        </InfoPanel>
      )}

      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {/* 회색 확인 박스: 제출 전에 사용자가 확인해야 할 전제를 두 줄로만 모은다.
            흩어져 있던 캡션(노드 안내·ref 설명)을 여기로 흡수한 자리다.

            border-line 을 더하는 이유: 토큰상 panel 과 canvas 가 **같은 색**
            (#f5f6f8)이라, 카드 안이 아니라 페이지 배경 위에 바로 놓이는 이 자리
            에서는 회색 배경만으로는 박스가 보이지 않는다(실측). Card 도 보더로
            구획하므로 새 색을 들이지 않고 같은 방식으로 경계를 준다. */}
        <InfoPanel className="border border-line">
          <p className="font-medium mb-2">빌드 전 확인</p>
          <ul className="space-y-1.5">
            <li className="flex items-start gap-2">
              {/* 빌드 노드 미설정은 제출 시 422(build_node_not_set)로 끝난다 — 그
                  사실을 미리 말하고, 고칠 수 있는 화면으로 바로 보낸다. */}
              {buildNodeName === null ? (
                <>
                  <AlertTriangle className={`${ICON} mt-0.5 text-bad`} aria-hidden />
                  <span className="text-bad">
                    {REASON_MESSAGES.build_node_not_set}{" "}
                    <Link className="text-accent underline" to="/admin/control">
                      컨트롤 상태로 이동
                    </Link>
                  </span>
                </>
              ) : (
                <>
                  <Check className={`${ICON} mt-0.5 text-ok`} aria-hidden />
                  <span>
                    빌드 노드 <span className="font-medium">{buildNodeName}</span>
                  </span>
                </>
              )}
            </li>
            <li className="flex items-start gap-2">
              {/* 소스 경로도 노드처럼 컨트롤 상태가 단일 진실이다 -- 미설정은 제출
                  시 422(build_source_not_set)로 끝나므로 미리 말하고 고칠 화면으로
                  보낸다. 경로의 실재 여부는 프리플라이트가 노드 위에서 검사한다. */}
              {sourcePath === null ? (
                <>
                  <AlertTriangle className={`${ICON} mt-0.5 text-bad`} aria-hidden />
                  <span className="text-bad">
                    {REASON_MESSAGES.build_source_not_set}{" "}
                    <Link className="text-accent underline" to="/admin/control">
                      컨트롤 상태로 이동
                    </Link>
                  </span>
                </>
              ) : (
                <>
                  <Check className={`${ICON} mt-0.5 text-ok`} aria-hidden />
                  <span>
                    로컬 소스 <span className="font-mono">{sourcePath}</span> 에서
                    빌드합니다 (미커밋 변경 포함)
                  </span>
                </>
              )}
            </li>
          </ul>
        </InfoPanel>

        <Card className="space-y-3">
          <label className="block">태그 (선택)
            <input aria-label="태그" className={field} value={tag} placeholder="d73"
                   onChange={(e) => setTag(e.target.value)} />
            <span className="block text-muted text-xs mt-1">
              비우면 b + 빌드ID 앞 8자로 자동 지정됩니다 — 이미 레지스트리에 있는
              태그를 지정하면 덮어씁니다
            </span>
          </label>
          <div>
            <span className="block mb-1">이미지</span>
            <div className="space-y-1">
              {IMAGES.map((name) => (
                <label key={name} className="flex items-center gap-2">
                  <input type="checkbox" aria-label={name} checked={images.includes(name)}
                         onChange={() => toggleImage(name)} /> {name}
                </label>
              ))}
            </div>
            {/* 의존성 안내는 dms-agent 를 고른 사람에게만 **한 줄로** 나온다.
                경고여도 제출은 막지 않는다: 그 태그가 이미 레지스트리에 있으면
                agent 단독 빌드가 정상 경로다. 막으면 정당한 사용을 못 하게 된다. */}
            {wantsAgent && (
              missingDeps.length > 0 ? (
                <p className="text-busy text-xs mt-2">
                  {`dms-agent 가 FROM 하는 ${missingDeps.join("·")} 가 이번 빌드에 없습니다 — ` +
                   `같은 태그가 레지스트리에 없으면 pull 에서 실패합니다.`}
                </p>
              ) : (
                <p className="text-muted text-xs mt-2">
                  dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM 합니다 — 함께 빌드합니다.
                </p>
              )
            )}
          </div>
          {submitBuild.isError && (
            <p className="text-bad">{(submitBuild.error as ApiError).message}</p>
          )}
        </Card>

        {/* 파란 안내 카드: 평소엔 2줄, 누르면 전문 팝업. chevron 이 "더 있다"를
            말한다. type="button" 이 필수다 -- form 안이라 기본값이면 제출된다. */}
        <InfoCard className="p-0">
          <button type="button" onClick={() => setGuideOpen(true)}
                  className="flex w-full items-center gap-3 rounded-card p-4 text-left">
            <Info className={`${ICON} text-accent`} aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block font-medium">빌드 절차 안내</span>
              <span className="block text-muted text-xs mt-0.5">
                프리플라이트 → 빌드 → push 순서로 진행합니다.
              </span>
              <span className="block text-muted text-xs">
                빌드 노드의 로컬 소스에서 빌드합니다 — 미커밋 변경도 포함됩니다.
              </span>
            </span>
            <ChevronRight className={`${ICON} text-muted`} aria-hidden />
          </button>
        </InfoCard>
        <ProcedureDialog open={guideOpen} onOpenChange={setGuideOpen} />

        {/* 하단 액션 바: 좌=취소, 가운데=안내, 우=주 동작. */}
        <div className="flex items-center justify-between gap-3 border-t border-line pt-4">
          <Button type="button" variant="ghost" onClick={cancel}>취소</Button>
          <span className="text-muted text-xs text-center">
            빌드 시작을 누르면 적합성 프리플라이트부터 시작합니다
          </span>
          <Button type="submit" disabled={!canSubmit || submitBuild.isPending}>빌드 시작</Button>
        </div>
      </form>
    </section>
  );
}
