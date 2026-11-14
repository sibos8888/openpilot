import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/lib/api";
import type { ParamValues, StatusSnapshot } from "../src/lib/schema";
import { pollStatus, store, writeParam } from "../src/lib/store";

const status = (over: Partial<StatusSnapshot>) => over as StatusSnapshot;

beforeEach(() => {
  store.ready = true;
  store.paramsVersion = "1";
  store.params = { A: "old" };
  store.caps = {};
  store.pending.clear();
  store.toasts.splice(0);
});
afterEach(() => vi.restoreAllMocks());

describe("pollStatus 能力与参数同步", () => {
  it("首次 /api/capabilities 为空：之后每次成功的 status 都带上能力", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({ paramsVersion: "1", capabilities: { brand: "toyota" } as never }));
    await pollStatus();
    expect(store.caps).toEqual({ brand: "toyota" });
  });

  it("版本变化后刷新失败：版本不提交，下一轮重试成功后才提交", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({ paramsVersion: "2" }));
    vi.spyOn(api, "capabilities").mockResolvedValue({} as never);
    const all = vi.spyOn(api, "allParams").mockRejectedValueOnce(new Error("网络错误"));
    await pollStatus();
    expect(store.paramsVersion).toBe("1");
    expect(store.params).toEqual({ A: "old" });
    expect(store.connected).toBe(true);

    all.mockResolvedValueOnce({ A: "device" });
    await pollStatus();
    expect(store.params).toEqual({ A: "device" });
    expect(store.paramsVersion).toBe("2");
  });

  it("刷新途中发生写入：旧的 _all 结果不能盖掉刚写的值，版本留给下一轮", async () => {
    vi.spyOn(api, "status").mockResolvedValue(status({ paramsVersion: "2" }));
    vi.spyOn(api, "capabilities").mockResolvedValue({} as never);
    const read = Promise.withResolvers<ParamValues>();
    const all = vi.spyOn(api, "allParams").mockReturnValueOnce(read.promise);
    const put = Promise.withResolvers<null>();
    vi.spyOn(api, "putParam").mockReturnValueOnce(put.promise);

    const polling = pollStatus();
    await vi.waitFor(() => expect(all).toHaveBeenCalled());
    const writing = writeParam("A", "new");
    read.resolve({ A: "old" });
    await polling;
    expect(store.params.A).toBe("new");
    expect(store.paramsVersion).toBe("1");

    put.resolve(null);
    expect(await writing).toBe(true);
    all.mockResolvedValueOnce({ A: "new" });
    await pollStatus();
    expect(store.params.A).toBe("new");
    expect(store.paramsVersion).toBe("2");
  });

  it("重叠调用共用同一轮，不会并发两份 status", async () => {
    const s = Promise.withResolvers<StatusSnapshot>();
    const spy = vi.spyOn(api, "status").mockReturnValue(s.promise);
    const a = pollStatus();
    const b = pollStatus();
    s.resolve(status({ paramsVersion: "1" }));
    await Promise.all([a, b]);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("writeParam 同 key 串行", () => {
  it("第一次写入挂起时第二次被拒绝；第一次失败只回滚到写入前的值", async () => {
    const first = Promise.withResolvers<null>();
    const put = vi.spyOn(api, "putParam").mockReturnValueOnce(first.promise);
    const one = writeParam("A", "1");
    expect(await writeParam("A", "2")).toBe(false);
    expect(put).toHaveBeenCalledTimes(1);
    expect(store.params.A).toBe("1");

    first.reject(new ApiError("bad", 400));
    expect(await one).toBe(false);
    expect(store.params.A).toBe("old");
    expect(store.pending.has("A")).toBe(false);
  });

  it("409（设备当前状态不允许）提示设备给出的原因", async () => {
    vi.spyOn(api, "putParam").mockRejectedValueOnce(new ApiError("需要停车后修改", 409));
    expect(await writeParam("A", "1")).toBe(false);
    expect(store.params.A).toBe("old");
    expect(store.toasts.map((t) => t.text).join()).toContain("需要停车后修改");
  });
});
