# LANLink 前端

设备上**没有 Node**，运行时也不需要。设备只拿构建产物
（`../static/`，随 release 一起发）；本目录是开发用源码。

## 仓库里保留什么

| 内容 | 是否进 git | 是否上设备 | 说明 |
| --- | --- | --- | --- |
| `src/`、`tests/` 源码 | 是 | 是，运行时不用 | 我们自己的代码，含 shadcn-vue 复制过来并改造过的 UI 组件 |
| `package.json`、`package-lock.json` | 是 | 是，运行时不用 | 用于开发机上的 `npm ci` 复现依赖树 |
| `vite.config.ts`、`tsconfig.json` | 是 | 是，运行时不用 | 构建配置 |
| `node_modules/`（约 162 MB） | **否** | 否 | 框架与依赖代码，一律不入库，见 `.gitignore` |
| `../static/` 构建产物 | 是 | **是** | 设备实际加载的文件 |

当前发布由 `tools/release/device_release.sh` 的 `assemble_stage` 复制构建树，
排除 `node_modules`，但不排除本目录。发布阶段通过 `tools/release/release_lib.py check-stage` 检查产物。
仓库没有 `tools/release/release_files.py` 或 `test_release_files.py`，也没有它们提供的前端目录排除规则。

## 依赖说明

精确版本见 `package-lock.json`（这是唯一权威）。各依赖的用途：

**运行时**
- `vue` — 框架。

**构建**
- `vite`、`@vitejs/plugin-vue` — 打包；产物直接写到 `../static/`。
- `tailwindcss`、`@tailwindcss/vite` — 样式（v4，配置在 CSS 里，无 JS config）。
- `typescript`、`vue-tsc`、`@types/node` — 类型检查（`npm run build` 前置）。
  TypeScript **锁在 `~5.9`**：`vue-tsc` 3.x 只声明 `typescript >=5.0`，
  于是 npm 会装上 7.x，而 7.x 移除了 `lib/tsc` 入口，`vue-tsc` 直接
  `ERR_PACKAGE_PATH_NOT_EXPORTED` 崩掉。放宽这个版本会让类型检查失效。
- `vitest` — 单测。

**UI**
- `reka-ui` — shadcn-vue 底层的无头原语（可访问性、键盘交互）。
- `class-variance-authority`、`clsx`、`tailwind-merge` — shadcn-vue 约定的
  class 组合工具（`src/lib/utils.ts` 的 `cn()`）。
- `lucide-vue-next` — 图标。
- `tw-animate-css` — 过渡动画。

> shadcn-vue 的组件不是 npm 依赖：CLI 把**源码**复制到 `src/components/ui/`，
> 之后由本项目维护（已按暗黑极客风改造配色与尺寸），因此它们照常入库。

## 开发

```bash
npm ci                      # 按 lockfile 还原依赖
npm run dev                 # 本机起开发服务器，API 代理到真设备
LANLINK_DEVICE=http://<设备IP>:8088 npm run dev
npm test                    # 单测
npm run build               # 类型检查 + 构建到 ../static/
```

## 界面约定

- 首页与侧栏共用 `src/lib/nav.ts` 的分组、图标和入口摘要；专用功能页的用途来自 `../settings_ui.json` 的 `description`。
- 手机导航使用 Reka Dialog 管理焦点，关闭后返回菜单按钮；键盘操作不播放抽屉动画。
- 可按压入口使用 `sl-press`，不要同时叠加 `transition-*`；键盘和减少动效模式不缩放，实时遥测不播放进度条过渡。
- 首次读取失败显示重试入口；刷新失败保留旧内容并标注上次成功更新时间，恢复后清除错误。
- Vite 代理设备 API，因此本地 `settings_ui.json` 的文案调整需要后端使用新版文件才能显示；仅更新前端不会改变旧设备返回的文案。

## 提交前必做

产物随 git 提交，所以改完前端要把**源码和产物一起**提交：

```bash
git add openpilot/system/lanlinkd/web          # 先暂存源码（指纹按 git 索引算）
tools/lanlink/build_lanlink_web.py --build             # 构建 + 写 static/.build-hash
git add openpilot/system/lanlinkd/static
```

提交前手动运行 `tools/lanlink/build_lanlink_web.py --check`，比对 `static/.build-hash` 与暂存源码指纹。
不一致即报错。当前发布脚本和 `check-stage` 没有调用这项检查，忘记构建仍可能发布旧界面。
