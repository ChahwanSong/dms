import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    const h = () => qc.clear();
    window.addEventListener("dms:unauthorized", h);
    return () => window.removeEventListener("dms:unauthorized", h);
  }, [qc]);
  return <>{children}</>;
}
