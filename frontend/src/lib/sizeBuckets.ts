// 비중 % 표기(2026-10-21 TargetDetail 에서 이동 -- 크기 분포·온도 툴팁 공용). 0 은 「0%」(정상값), 0.1 미만은 「<0.1%」(0 이 아닌
// 극소값을 0% 로 반올림하면 「없다」는 거짓), 10 미만은 소수 한 자리, 그 위는 정수.
//
// 이 모듈엔 2026-10-21 의 파일 크기 구간 사람 말(「4–64 KiB」)·분포 줄 규칙(sizeRangeLabel·sizeDistribution -- 가로 막대 목록
// SizeDistribution 의 몸통)도 있었다. 2026-11-16 두 사용량 화면(펼친 상세·스토리지 합계)이 배치 항목과 같은 세로 막대
// 차트(features/usage/ScanHistograms -- 축약 라벨 「4K~64K」, 값 누락 = 모름)로 바뀌어 쓰는 곳이 없어져 테스트와 함께 지웠다
// (git 히스토리에서 열람) -- 크기 구간 라벨 규칙은 이제 ScanHistograms.sizeBucketLabel 한 벌이다.
export const pctText = (p: number) =>
  p === 0 ? "0%" : p < 0.1 ? "<0.1%" : p < 10 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;
