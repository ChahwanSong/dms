import type { HistogramBucket } from "../../lib/types";
import { pctText, sizeBytesText, sizeDistribution } from "../../lib/sizeBuckets";
import { barLayout } from "./BarChart";

// 파일 크기 분포 가로 막대 목록(2026-10-21 사용자 요청 「최근 스캔 기준 파일의 사이즈별 정보」). 한 줄 = 구간 하나(작은 → 큰, dscan
// 10구간 그대로 -- 0개 구간도 지우지 않는다: 「큰 파일 없음」도 정보다). 줄: 라벨 · 막대 · 개수 · (용량) · 비중.
// - 막대 길이 = **가장 많은 구간 대비**(barLayout 관례 -- 0 이 아닌 극소값은 최소 폭, 0 은 0). 전체 대비 비중은 오른쪽 % 가 말한다.
// - 단색 accent: 크기는 hot/cold 축이 아니다 -- 온도색을 입히면 「작은 파일 = hot」이라는 거짓 짝이 생긴다(BatchDetail 근거 그대로).
// - 용량 열은 모든 구간에 bytes 가 실릴 때만(지금 dscan 은 개수만 -- 10줄 내내 「—」를 보이지 않게).
// - 열 정렬: 목록이 격자 하나이고 줄은 subgrid 다 -- 줄마다 따로 격자를 두면 개수 글자 폭에 따라 막대의 끝이 줄마다 달라져
//   길이를 비교할 수 없다. 라벨 칸은 minmax(0, max-content) 라 좁은 폭(375 의 펼친 상세)에선 막대 최소 폭을 지키고 라벨이 먼저
//   말줄임된다(가로 넘침 없음).
// - 낭독: 줄마다 sr-only 한 문장(「4 KiB 이하 590개 89%」), 보이는 칸은 aria-hidden -- 칸 사이 공백 노드는 이름 계산에서 붙는다.
//   sr-only 는 absolute 라 격자 칸을 차지하지 않고, relative 줄 안에 갇힌다(가로 스크롤 래퍼 밖으로 문서 폭을 넓힌 L1 선례).
// 표시 전용이다 -- 제목(SectionHead)과 그 id 는 호출측이 둔다.
export function SizeDistribution({ buckets, labelledBy, missingText = "크기 분포 기록이 없습니다(구형 리포트)" }: {
  buckets: HistogramBucket[] | null | undefined;
  labelledBy?: string;
  missingText?: string;
}) {
  const d = sizeDistribution(buckets);
  if (d === null) return <p className="text-xs text-muted break-keep">{missingText}</p>;
  if (d.total === 0) return <p className="text-xs text-muted">파일이 없습니다(0개)</p>;
  const bars = barLayout(d.rows.map((r) => ({ label: r.label, value: r.count ?? 0 })));
  const cols = d.hasBytes
    ? "grid-cols-[minmax(0,max-content)_minmax(2rem,1fr)_max-content_max-content_max-content]"
    : "grid-cols-[minmax(0,max-content)_minmax(2rem,1fr)_max-content_max-content]";
  return (
    <div className="max-w-xl">
      <ul aria-labelledby={labelledBy} className={`grid ${cols} gap-x-2.5 gap-y-1 text-xs leading-5`}>
        {d.rows.map((r, i) => {
          const n = r.count === null ? "—" : `${r.count.toLocaleString("ko-KR")}개`;
          const b = r.bytes === null ? "—" : sizeBytesText(r.bytes);
          const p = r.pct === null ? "—" : pctText(r.pct);
          const zero = r.count === 0;
          return (
            <li key={i} title={r.title} className="relative col-span-full grid grid-cols-subgrid items-center">
              <span className="sr-only">{`${r.label} ${n}${d.hasBytes ? ` ${b}` : ""} ${p}`}</span>
              <span aria-hidden className={`truncate ${zero ? "text-muted" : "text-ink/80"}`}>{r.label}</span>
              <span aria-hidden className="h-2 overflow-hidden rounded-full bg-accent/10">
                <span className="block h-full rounded-full bg-accent" style={{ width: `${bars[i].pct}%` }} />
              </span>
              <span aria-hidden className={`text-right tabular-nums ${zero || r.count === null ? "text-muted" : "text-ink"}`}>
                {n}</span>
              {d.hasBytes && (
                <span aria-hidden className={`text-right tabular-nums ${zero ? "text-muted" : "text-ink/70"}`}>{b}</span>
              )}
              <span aria-hidden className="min-w-[2.5rem] text-right tabular-nums text-muted">{p}</span>
            </li>
          );
        })}
      </ul>
      {d.total === null && (
        <p className="mt-1.5 text-xs text-muted break-keep">일부 구간의 개수를 몰라 비중은 표시하지 않습니다</p>
      )}
    </div>
  );
}
