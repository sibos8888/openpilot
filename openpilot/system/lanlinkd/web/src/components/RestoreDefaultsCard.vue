<script setup lang="ts">
/** 转向页底部：一键把扭矩、车型、单位、显示等预设项强制写回默认值（列表与取值以设备端为准）。 */
import { computed, ref } from "vue";
import { RotateCcw } from "lucide-vue-next";
import { api } from "@/lib/api";
import { refreshParams, store, toast } from "@/lib/store";
import Button from "./ui/Button.vue";
import ConfirmDialog from "./toyota/ConfirmDialog.vue";

const open = ref(false);
const busy = ref(false);
const onroad = computed(() => store.status?.device?.started === true);

async function restore(): Promise<void> {
  busy.value = true;
  try {
    await api.restoreDefaults();
    open.value = false;
    toast("已恢复默认设置，车型等改动在下次启动后生效", "ok");
    // 刷新失败（并发写入）时由状态轮询按 paramsVersion 补上
    await refreshParams().catch(() => undefined);
  } catch (e) {
    toast(e instanceof Error ? e.message : "恢复默认设置失败", "error");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="sl-card px-5 py-4">
    <div class="flex items-center gap-3">
      <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
        <RotateCcw class="size-5 text-sl-text-2" />
      </div>
      <div class="min-w-0 flex-1">
        <h2 class="text-[13px] font-semibold tracking-wide text-sl-text-3">恢复默认设置</h2>
        <p class="mt-0.5 text-[13px] text-sl-text-2">扭矩控制、车型、公制单位、变道和视觉提示恢复为推荐值</p>
      </div>
      <Button variant="danger" :disabled="onroad || busy" class="min-w-20 justify-center" @click="open = true">
        恢复
      </Button>
    </div>
    <p v-if="onroad" class="mt-3 rounded-lg bg-sl-surface-2 px-3 py-2 text-[13px] text-sl-text-3">
      仅限停车（offroad）时操作。
    </p>

    <ConfirmDialog v-model:open="open" title="恢复默认设置？" confirm-text="恢复" :busy="busy" danger @confirm="restore">
      <p>以下设置会被覆盖为默认值，包括你之前手动改过的：</p>
      <ul class="list-disc pl-5">
        <li>强制扭矩横向控制及全部扭矩控制设置（整定版本 v1.0）</li>
        <li>车型：Toyota Sienna 2021-27 PATCHED</li>
        <li>使用公制单位、路沿检测禁止变道</li>
        <li>显示盲区预警、转向弧、显示转向灯、绿灯提醒、前车起步提醒</li>
      </ul>
    </ConfirmDialog>
  </section>
</template>
