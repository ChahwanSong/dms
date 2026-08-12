// 슬라이스 31: 사내망·CSP 안전을 위해 셀프호스팅(@fontsource) -- CDN 링크 금지.
// weight 는 400/500/700 만: 한글은 unicode-range 조각이라 실제 전송은 필요분만이다.
import "@fontsource/noto-sans-kr/400.css";
import "@fontsource/noto-sans-kr/500.css";
import "@fontsource/noto-sans-kr/700.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { queryClient } from "./app/queryClient";
import { AppRouter } from "./app/router";
import "./index.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><AppRouter /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
