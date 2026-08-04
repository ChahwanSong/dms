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
