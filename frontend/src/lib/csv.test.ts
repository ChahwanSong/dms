import { parseBatchCsv } from "./csv";
import { test, expect } from "vitest";

test("scan csv ok", () => {
  const { rows, errors } = parseBatchCsv("scan", "storage,target\ncephfs-dms,a/b\ncephfs-dms,c");
  expect(errors).toEqual([]);
  expect(rows).toEqual([{storage:"cephfs-dms",target:"a/b"},{storage:"cephfs-dms",target:"c"}]);
});
test("sync csv ok", () => {
  const { rows } = parseBatchCsv("sync",
    "source_storage,source,destination_storage,destination\ns1,a,s2,b");
  expect(rows).toEqual([{source_storage:"s1",source:"a",destination_storage:"s2",destination:"b"}]);
});
test("wrong header is an error", () => {
  const { errors } = parseBatchCsv("scan", "foo,bar\n1,2");
  expect(errors.length).toBeGreaterThan(0);
});
test("short row is an error, not silently dropped", () => {
  const { errors } = parseBatchCsv("scan", "storage,target\ncephfs-dms");
  expect(errors.length).toBeGreaterThan(0);
});
