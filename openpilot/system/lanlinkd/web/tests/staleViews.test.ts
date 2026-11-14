import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/lib/api";
import type { ParamValues } from "../src/lib/schema";
import { refreshParams, store, writeParam } from "../src/lib/store";

beforeEach(() => {
  store.params = { A: "old" };
  store.pending.clear();
  store.toasts.splice(0);
});
afterEach(() => vi.restoreAllMocks());

describe("手动刷新参数快照", () => {
  it("成功返回前已应用设备值", async () => {
    vi.spyOn(api, "allParams").mockResolvedValueOnce({ A: "device" });
    await expect(refreshParams()).resolves.toBeUndefined();
    expect(store.params).toEqual({ A: "device" });
  });

  it("写入仍在进行时拒绝刷新，不把旧快照当成成功", async () => {
    const put = Promise.withResolvers<null>();
    vi.spyOn(api, "putParam").mockReturnValueOnce(put.promise);
    vi.spyOn(api, "allParams").mockResolvedValueOnce({ A: "old" });
    const writing = writeParam("A", "new");

    await expect(refreshParams()).rejects.toThrow("刷新结果未应用");
    expect(store.params).toEqual({ A: "new" });
    expect(store.pending.has("A")).toBe(true);

    put.resolve(null);
    expect(await writing).toBe(true);
  });

  it("读取途中写入已经完成，仍拒绝早于写入的快照", async () => {
    const read = Promise.withResolvers<ParamValues>();
    vi.spyOn(api, "allParams").mockReturnValueOnce(read.promise);
    vi.spyOn(api, "putParam").mockResolvedValueOnce(null);
    const refreshing = refreshParams();
    expect(await writeParam("A", "new")).toBe(true);
    expect(store.pending.size).toBe(0);

    const rejected = expect(refreshing).rejects.toThrow("刷新结果未应用");
    read.resolve({ A: "old" });
    await rejected;
    expect(store.params).toEqual({ A: "new" });
  });

  it("另一份刷新先落地后，旧刷新拒绝且保留新快照", async () => {
    const read = Promise.withResolvers<ParamValues>();
    vi.spyOn(api, "allParams")
      .mockReturnValueOnce(read.promise)
      .mockResolvedValueOnce({ A: "latest" });
    const older = refreshParams();
    await expect(refreshParams()).resolves.toBeUndefined();

    const rejected = expect(older).rejects.toThrow("刷新结果未应用");
    read.resolve({ A: "old" });
    await rejected;
    expect(store.params).toEqual({ A: "latest" });
  });

  it("网络失败向调用方报错并保留原值", async () => {
    vi.spyOn(api, "allParams").mockRejectedValueOnce(new Error("网络错误"));
    await expect(refreshParams()).rejects.toThrow("网络错误");
    expect(store.params).toEqual({ A: "old" });
  });
});
