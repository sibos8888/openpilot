<script setup lang="ts">
/** 实时数据页：3 个主题 + 动力电池入口；主题页是大数字网格，1 s 刷新，可暂停 / 继续。
 *  里程单位并进里程卡；Ready 下发动机常不转，所以发动机相关项带「需发动机运转」标签。 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { BatteryCharging, ChevronRight, Pause, Play } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { ToyotaLiveValue } from "@/lib/schema";
import { BATTERY_12V_KEY, errorText, liveCards } from "@/lib/toyota";
import { iconByName } from "@/lib/toyotaIcons";
import { currentPage, goTo } from "@/lib/toyotaNav";
import { toyotaData } from "@/lib/toyotaData";
import { touch } from "@/lib/toyotaSession";
import { cn } from "@/lib/utils";
import Badge from "../ui/Badge.vue";
import Button from "../ui/Button.vue";

const REFRESH_MS = 1000;

const catalog = computed(() => toyotaData.catalog!);
const tool = computed(() => catalog.value.liveTools.find((t) => t.id === currentPage.value.arg));
const items = computed(() =>
  (tool.value?.keys ?? []).flatMap((k) => catalog.value.live.filter((i) => i.key === k)),
);
const values = ref<ToyotaLiveValue[]>([]);
const paused = ref(false);
const cards = computed(() => liveCards(items.value, values.value));
const readError = ref("");
const updatedAt = ref("");

let timer: ReturnType<typeof setInterval> | undefined;
let reading = false;

async function refresh(): Promise<void> {
  if (reading || paused.value || document.visibilityState !== "visible" || !tool.value) return;
  reading = true;
  const current = tool.value;
  try {
    const r = await api.toyotaReadLive(current.keys);
    if (tool.value !== current) return;
    values.value = r.values;
    readError.value = "";
    updatedAt.value = new Date().toLocaleTimeString();
    touch();
  } catch (e) {
    if (tool.value === current) readError.value = errorText(e);
  } finally {
    reading = false;
  }
}

// 主题列表和主题页是同一个组件实例（进主题页不会重新挂载），所以计时器挂载即启动，切主题时清掉旧值立即读一次
watch(tool, () => {
  values.value = [];
  readError.value = "";
  updatedAt.value = "";
  void refresh();
});

onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), REFRESH_MS);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <!-- 主题页 -->
  <div v-if="tool" class="flex flex-col gap-2.5">
    <div v-if="readError" role="status" class="rounded-lg bg-sl-danger/10 px-4 py-3 text-[13px] ring-1 ring-inset ring-sl-danger/25">
      <p class="text-sl-danger">{{ updatedAt ? "实时数据已停止更新" : "无法读取实时数据" }}：{{ readError }}</p>
      <p v-if="updatedAt" class="mt-1 text-sl-text-2">显示上次数据，可能已过期 · 上次更新 {{ updatedAt }}</p>
    </div>
    <div :class="cn('grid grid-cols-2 gap-2.5', (paused || readError) && '[&_b]:text-sl-text-2')">
      <div v-for="c in cards" :key="c.key" class="sl-card px-3 py-3.5">
        <b class="sl-tabular block text-[28px] leading-tight text-sl-text-1">
          {{ c.value }}<small v-if="c.unit" class="ml-1 text-[14px] font-medium text-sl-text-2">{{ c.unit }}</small>
        </b>
        <span class="text-[13px] text-sl-text-2">{{ c.name }}</span>
        <Badge v-if="c.needsEngineRunning" kind="warn" class="ml-1.5 normal-case">需发动机运转</Badge>
      </div>
    </div>
    <div class="flex items-center justify-between gap-3 px-1">
      <span class="text-[12px] text-sl-text-3">
        {{ readError ? "读取失败，每 1 秒重试" : `每 1 秒刷新${paused ? "（已暂停）" : ""}` }}
      </span>
      <Button @click="paused = !paused"><component :is="paused ? Play : Pause" />{{ paused ? "继续" : "暂停" }}</Button>
    </div>
    <p v-if="items.some((i) => i.key === BATTERY_12V_KEY)" class="px-1 text-[12px] text-sl-text-3">
      12V 电压：IG-ON 看蓄电池，Ready 看充电。
    </p>
  </div>

  <!-- 总览 -->
  <div v-else class="flex flex-col gap-2.5">
    <p class="text-[14px] text-sl-text-2">按主题查看车辆实时参数。</p>
    <button
      v-for="t in catalog.liveTools"
      :key="t.id"
      type="button"
      class="sl-card flex w-full items-center gap-3.5 p-3.5 text-left hover:bg-sl-surface-2"
      @click="goTo('liveTool', t.id)"
    >
      <span class="grid size-11 shrink-0 place-items-center rounded-xl bg-sl-accent-muted text-sl-accent">
        <component :is="iconByName(t.icon)" class="size-5" />
      </span>
      <span class="flex-1">
        <b class="block text-[16px] text-sl-text-1">{{ t.name }}</b>
        <span class="text-[13px] text-sl-text-2">{{ t.keys.length }} 项</span>
      </span>
      <ChevronRight class="size-5 text-sl-text-3" />
    </button>
    <button
      type="button"
      class="sl-card flex w-full items-center gap-3.5 p-3.5 text-left hover:bg-sl-surface-2"
      @click="goTo('battery')"
    >
      <span class="grid size-11 shrink-0 place-items-center rounded-xl bg-sl-accent-muted text-sl-accent">
        <BatteryCharging class="size-5" />
      </span>
      <span class="flex-1">
        <b class="block text-[16px] text-sl-text-1">动力电池</b>
        <span class="text-[13px] text-sl-text-2">12 块电压与内阻，含健康判定</span>
      </span>
      <ChevronRight class="size-5 text-sl-text-3" />
    </button>
  </div>
</template>
