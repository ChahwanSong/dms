import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { MountsOverview } from "../../lib/types";

// 마운트 현황(GET /api/admin/mounts -- 서버는 DB 만 읽는다: 에이전트 보고·API 점검 행·컨트롤러 점검). 에이전트 보고가 60초
// 주기라 노드 화면과 같은 10초 폴링이면 충분하다(FS 를 건드리지 않는 가벼운 조회).
export const useMounts = () =>
  useQuery({ queryKey: ["mounts"], queryFn: () => apiGet<MountsOverview>("/api/admin/mounts"), refetchInterval: 10_000 });
