// 비중 % 표기 공용(사용량 표·합계 줄·경로 상세 수치·온도 툴팁).
//
// 이 파일은 예전(2026-10-21) 사용량 「파일 크기 분포」 가로 막대 목록(SizeDistribution)의 구간 라벨·분포 줄 규칙을 담았다.
// 2026-11-16 부터 사용량(경로 상세·스토리지 합계)과 배치 scan 항목이 모두 같은 세로 막대 차트(features/usage/ScanHistograms 의
// SizeHistogram -- 구간 라벨은 그 sizeBucketLabel, 값이 하나라도 빠지면 「모름」인 histBars)를 쓰고(사용자 「다르게 보일 이유가
// 없다」), 두 번째 구현은 지웠다 -- 화면 사이 모양·null 판정이 다시 갈라지지 않게 크기 분포를 그리는 코드는 ScanHistograms 하나다.
// 이름(sizeBuckets)은 import 자리를 바꾸지 않으려고 그대로 둔다.

// 0 은 「0%」(정상값), 0.1 미만은 「<0.1%」(0 이 아닌 극소값을 0% 로 반올림하면 「없다」는 거짓), 10 미만은 소수 한 자리, 그 위는 정수.
export const pctText = (p: number) =>
  p === 0 ? "0%" : p < 0.1 ? "<0.1%" : p < 10 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;
