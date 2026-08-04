import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    const h = () => qc.invalidateQueries({ queryKey: ["auth", "me"] });
    window.addEventListener("dms:unauthorized", h);
    return () => window.removeEventListener("dms:unauthorized", h);
  }, [qc]);
  return <>{children}</>;
}
