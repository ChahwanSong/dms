import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("parses CSV, submits items, navigates to detail", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/batches", async ({request}) => {
    body = await request.json();
    return HttpResponse.json({batch_id:"b9", status:"Running"}, {status:202}); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/new"]}>
    <Routes><Route path="/admin/batches/new" element={<BatchCreate/>} />
      <Route path="/admin/batches/:id" element={<h1>배치 b9</h1>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await userEvent.type(screen.getByLabelText("CSV"), "storage,target\ncephfs-dms,a\ncephfs-dms,b");
  await userEvent.clear(screen.getByLabelText("동시 실행 상한"));
  await userEvent.type(screen.getByLabelText("동시 실행 상한"), "2");
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  expect(await screen.findByRole("heading", { name: "배치 b9" })).toBeInTheDocument();
  expect(body).toMatchObject({ operation:"scan", max_concurrency:2,
    items:[{storage:"cephfs-dms",target:"a"},{storage:"cephfs-dms",target:"b"}] });
});
