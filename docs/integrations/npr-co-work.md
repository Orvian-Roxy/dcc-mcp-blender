# NPR 共作的归属、流程与验收状态

DCC 是主开发项目。当前开发基于 adapter 0.2.21，验收固定 Core、server 和 CLI 0.20.45。此 checkout 的实现和私有 NPR 验收从未切换生产 MCP。迁移范围见 [Spec #14](https://github.com/Orvian-Roxy/blender-mcp-runtime/issues/14)。

当前 MMD 目标按用户最新指令收敛为 MMD Tools 4.5.11 的 native API1 适配包，配套 NPR alpha.4。它已经包含原 MMR 的原生碰撞构建能力，不需要启用 MMR 插件。此前官方 MMD Tools 4.5.14 的隔离结果属于旧基线，不能代替此适配包的验证。

## 谁负责什么

MMD Tools 的源码、打包、版本和安装更新统一由[插件维护会话](codex://threads/01a147e0-5fe9-7a52-a626-9fd6e0c66e75)负责。本项目消费其安装包，仅维护 DCC 接入、调用契约和消费侧兼容验证。

| 内容 | 归属 |
| --- | --- |
| 实例路由、主线程执行、插件管理、RNA 和节点工具 | DCC adapter。沿用上游 Core、server 和 CLI |
| 当前 MMD Tools 原生物理 | DCC 现有 live Operator/RNA 调用 `mmd_tools.build_rig`，不新增 provider |
| 遗留可选 Native 方法 | Runtime `ce9ae34` 经 [DCC 入口](../../src/dcc_mcp_blender/_native_runtime_ops.py)直接调用。Toon、MBTS 和 Wiggle 不作为当前 MMD 路径的前置 |
| 构建、业务验证、材质配方和资产 | NodeBPY-Bridges 与 Assets。DCC [接入层](../../src/dcc_mcp_blender/_nodebpy_runtime_ops.py)调用项目入口 |
| 远端烘焙、物理计算、渲染和作业 | 现有 Fleet CLI 与 Worker，保持原接口和配置 |

当前 MMD Tools 路径不依赖旧 Runtime。仅使用遗留 Native 方法时才附加其依赖路径。DCC 不转调旧 MCP，也不接管 NodeBPY 业务代码或资产。生产配置、现有会话和 Worker 继续使用原入口。

`mmd.toon.*` 对应旧 Runtime 的可选 `mmd_toon_lookdev` 接口，和 MMD Tools、MBTS 是不同组件。没有证据表明当前工作路径需要它。此前将其缺包列为迁移阻塞是范围判断错误，现撤销该前置；不要求用户查找或安装。

## 日常共作

### 固定 MCP 接入

本机 `blender` 连接使用 `http://127.0.0.1:9765/mcp`；用户级 LaunchAgent `local.dcc-mcp.gateway` 托管官方 server，仅监听回环地址，禁用 remote listener，空闲时继续运行。`~/Library/LaunchAgents/local.dcc-mcp.gateway.plist` 是本机部署配置。

`tools/npr_mcp.py --config /absolute/npr.json` 提供 stdio 启动连接（依赖现有环境中的 MCP Python SDK 2.x）。Codex 注册名为 `blender_startup`，仅提供 `start_instance(task_name, project_root)` 与 `instance_status(task_name)`，委托既有 NPR 启动器；配置旁的 `instances/<task_name>` 保留官方启动记录，重复启动同名目录会拒绝。共享网关注册目录为配置目录上一级的 `gateway/registry`，必须与已部署网关一致。独立 CLI 原流程保留，默认仍使用私有网关。

自动启动传入 `--no-window-focus`。2026-10-19，本机默认 DCC 配置已切换到带原生 Cocoa 焦点修复的 alpha.4 本地补丁版，完整应用保持签名和只读安装；身份、部署回执与配置回退位置见[原生补丁清单](/Users/chenrui/.codex/worktrees/npr-no-focus-20261019/blender-mmd-physics/npr/macos-no-window-focus.json)。启动 MCP 每次读取配置，无须重启；新默认作用于新建宿主，按项目复用的已有宿主保留原二进制。实际默认入口已通过新建窗口、保存重开、MMD Tools 准备和正常关闭检查，2,071 次前台 PID 采样未观察到该测试实例置前。

NPR 配置的 `addons` 列表指定已安装的模块和必需 Operator，例如 `{"module":"mmd_tools","operators":["mmd_tools.import_model","mmd_tools.build_rig"]}`；模块路径由 `python_paths` 提供。启动器复用 DCC 插件启用接口，在实例私有配置中保存偏好，不改日常 Blender 配置。插件准备失败仍保留 DCC 连接及逐项错误，返回 `ready:false`。`environment_ready` 和 `addons` 是启动准备回执；原位修复后的当前插件状态通过 DCC 插件查询确认。NodeBPY prepare 复用已注册的导入器。

`--factory-startup` 不继承日常 Blender 的导航偏好。显式 `navigation_preferences` 配置可指定 `use_mouse_depth_navigate`、`use_zoom_to_mouse`、`use_rotate_around_active` 三个布尔值；启动器只在初始化时通过现有 Blender 偏好接口应用并保存到实例私有配置，回执返回实际值。缺省不修改这些偏好，角色构建、模板加载与审阅保留用户导航偏好。本机用户指定默认为前两项开启、最后一项关闭；日常 Blender 5.2 的用户偏好也已核对保存。23 项启动相关测试、原生工厂启动应用及下一进程读取验证通过，记录位于 `/Users/chenrui/.local/share/dcc-mcp-blender/production-20261017/navigation-defaults-a64bebec/`。未改现有其他任务的窗口。

插件启动修复验收：35 项相关测试和 Ruff 通过。真实新实例自动启用 MMD Tools，偏好、导入/构建 Operator、`Object.mmd_root` 和 UI 面板注册通过；打开现有 300 帧 MMD 工程后再次通过，另存测试副本并正常关闭。缺失插件的独立失败实例返回 `ready:false` 和具体错误，DCC 查询及关闭仍可用。原始回执分别保存在 `/tmp/dcc-addon-e2e`、`/tmp/dcc-addon-failure`。

正常任务先通过启动 MCP 取得实例身份，再由官方 DCC MCP 完成发现、编辑、保存和关闭。不要为每轮创建临时终端宿主。连接配置变更后重载 MCP；连接不可用时先恢复服务，不能将临时 CLI 验证当作正式接入完成。此接入不改变 Codex 沙箱策略，不导入旧 Runtime 的票据或场景状态管理。

2026-10-18 验收：14 项启动相关测试通过，真实 stdio MCP 启动后，独立 HTTP MCP 客户端经官方网关完成修改、保存、重开数值验证和关闭。回执及测试脚本位于 `/tmp/dcc-startup-e2e`；随后本地 Codex 会话直接通过 MCP 完成启动、对象改名、保存、重开核对及安全关闭，全程无额外审批，关闭后官方实例列表为空。追加状态查询发现 `instance:null` 处理错误，已用回归测试修复；Codex 重启后，本地验收会话通过原生 `instance_status` 返回 `ready:false`、`instance:null`，无异常或审批，随后恢复角色验收。网关登录恢复由 LaunchAgent 配置，未以重启整机验证。


Agent 在选定实例工作，人可以查看、修改或中断。通常依次完成这些动作。

日常新实例使用上面的启动 MCP。仅在独立 CLI 验证或诊断时，使用 [NPR 启动入口](../../tools/npr_runtime.py)，复用官方 CLI 的 gateway 与 `start-instance`：

```bash
python tools/npr_runtime.py start --config /absolute/npr.json --dry-run
python tools/npr_runtime.py start --config /absolute/npr.json
python tools/npr_runtime.py status --work-dir /absolute/private-run
```

配置明确指定 NPR 可执行文件及 SHA/build、现有 DCC CLI/server、adapter 源码及依赖目录、项目、私有端口和新的 `work_dir`。`python_paths` 只引用已安装目录或 wheel，入口不安装或更新插件。具体字段和示例见脚本帮助。已有或状态未知的目录用 `status` 观察，保留原操作身份；`start` 不覆盖目录。成功结果包含实际 binary/build、组件版本、PID、`instance_id` 和连接地址，内部 `host.json` 单独不代表 CLI 就绪。Agent 的长期 GUI 任务应由持续运行的终端或既有执行宿主承载；宿主结束后的进程状态须用 `status` 观察，不能只读旧 ready 回执。

GUI 空闲调度间隔默认 0.5 秒，可在本次绑定配置中显式设 `idle_interval_secs=0.05`。这不修改 Core 或其他实例；本机小查询及空闲成本对照见[第二阶段验收记录](/Users/chenrui/.codex/worktrees/addon-capabilities-research/blender-mcp-runtime/outputs/research/plugin-management-2026-10-17/核心接入第二阶段.md)。

1. 选择明确的 `instance_id`。后续 CLI 调用持续带上 `--dcc-type blender --instance-id <id>`，避免连接到其他 Blender。
2. 按需 `load-skill`，再调用该 skill 的工具。NodeBPY 使用 `blender-nodebpy-runtime`，插件使用 `blender-dev` 和 `blender-extensions`。
3. 启动独立 NPR 时，用进程环境变量 `NODEBPY_BLENDER_BINARY` 明确指定已选 NPR 的实际可执行文件；否则 NodeBPY 会读取全局 MCP 启动设置。调用 `blender_nodebpy_runtime__prepare`，指定项目 checkout、已有依赖路径和 MMD 插件模块名。Extension 使用完整 `bl_ext.<repo>.<package>` 身份。成功须包含当前进程的插件注册和项目 doctor 结果。
4. 用 `blender_nodebpy_runtime__project` 执行项目 `run` 或 `verify`。`run` 构建独立候选并保留 GUI，`verify` 检查当前编辑。替换模板遇到 `unsaved_changes` 时，先明确保存当前场景和图像。参数见 [工具契约](../../src/dcc_mcp_blender/skills/blender-nodebpy-runtime/tools.yaml)。
5. 需要改图时，使用 `blender-node-runtime` 的局部查询、schema 和 revision 修改工具。先读取目标图及 revision，再提交有限修改。具体边界见 [节点契约](../../src/dcc_mcp_blender/skills/blender-node-runtime/tools.yaml)。

普通编辑不要求 task、参与票据、完整检查清单或外观接受。工具保留当前视图，默认不关闭 Blender。项目验证、确认保存和人的外观判断分别报告。

需要构建 MMD 物理时，明确选择模型根，通过 `blender-rna-runtime` 查询并调用 `mmd_tools.build_rig`，再读取 `mmd_root.is_built` 和实际后端结果。世界参数与16项碰撞 mask 可由现有 RNA 工具读写；`collision_exclusions` 的关系维护交给插件，不为 Object 指针新增通用编码。构建可能重建现有 rig，未知结果不得自动重试。

复跑构建时，在已完成 `prepare` 的独立实例中使用新输出路径：

```bash
dcc-mcp-cli --base-url <gateway> --no-auto-gateway --non-interactive \
  --timeout-secs 300 call blender_nodebpy_runtime__project \
  --dcc-type blender --instance-id <instance-id> \
  --json '{"project_root":"/absolute/NodeBPY-Bridges","action":"run","game":"genshin","profile":"/absolute/copied-profile.json","output":"/absolute/Assets/Genshin/characters/<character-id>/work/<new-candidate>.blend"}'
```

候选已打开时，将 `action` 改为 `verify`，使用同一输出。共作入口跳过纯视口交付要求，保留材质、灯光、色彩管理和结构检查。旧项目入口继续使用原视图约定。

当前明确支持 Genshin、WutheringWaves、ZenlessZoneZero 和 HonkaiStarRail（CLI 简写为 `genshin`、`wuwa`、`zzz`、`hsr`），其他游戏仍明确拒绝。业务代码、Profile 和资源继续留在 NodeBPY 项目。默认成功结果包含状态、规模和必要资格限制；`diagnostic_only` 不表示可交付候选。

HSR 新共作从 Profile 明确的无角色模板开始，保护当前修改并保留视图；构建确认保存后保留现场，不自动重开。host `check` 明示 PMX 槽位／头骨／基础纹理及运行态仍需 `run` preflight 验证。Dahlia 的实际构建、原位 verify、模拟编辑／视图保持及显式保存已通过，范围见 [HSR 接入凭据](hsr-co-work-20261017.json)；不代表所有 HSR 角色、视觉或物理质量接受。

成功构建的完整结果独占保存为候选旁的 `<stem>.dcc-build-report.json`，返回 `report_path`，之后可直接只读查看。`full=true` 返回完整当次结果，verify 不覆盖原构建报告。报告写入失败时返回原完整成功结果及 `report_error`，避免将已完成构建误报失败而重跑；错误与重要业务限制始终保留。

远端计算由 Agent 直接使用既有 Fleet CLI 的 `submit/status/result/download`，配置、Job/Attempt 和产物校验仍由 Fleet 维护。Blender 主线程无需再转调旧 MCP 或执行 SSH。具体接口见已部署 Runtime 的 [Fleet 说明](/Users/chenrui/.local/share/blender-mcp-runtime/releases/20261017-082a179-mmd-native/docs/fleet.md)。

## 保存状态为什么仍然保守

[共享文件入口](../../src/dcc_mcp_blender/_scene_files.py)在明确打开、替换或关闭前保护场景、图像像素和 managed pending 修改。保存须同时读回 Operator 结果、实际文件目标、场景状态和文件大小及修改时间。已有文件或外层 `success` 不能证明本次保存完成。

managed pending 记录工具可能产生但 Blender dirty 标志尚未反映的修改。普通写工具和任意 Python 调用会保守地产生这个标记，因此只准备依赖或运行查询脚本也可能多出一次保存。这是当前避免静默丢失修改的代价。使用顶层 `blender_scene__save_scene` 保存，使用只读的 `blender_scene__get_session_info` 观察状态。

`read_only` 元数据必须符合工具行为。真实只读工具不会产生 managed pending，局部查询也就不会无故阻挡后续文件操作。不能为了减少保存而把写工具标成只读。

已有审阅场景仅需局部取景时，先用 `get_viewport_info` 取得 window/area 标识，再以 `set_viewport_view` 一次设置轴向、距离和观察中心。冷启动还可在同一次调用设置视口着色模式、合成器和叠加显示。它仅修改视口 UI，不重复隐藏对象或修改材质、灯光，不增加或清除场景 pending。完整角色审阅准备仍由 NodeBPY 负责；用户要求将新视图写入工程时正常保存。

typed open 现在记录加载前后稳定的文件观测，干净且未再修改时可返回 `save_status: saved, state_source: open`，不伪造 `last_save`。路径、文件 stamp、修改 revision 或脏图像变化会使该观测失效，后续未知/失败保存也不会回退到它。不再因刚重开后的状态未知而补存同一工程；真实修改与默认备份策略保持。

NodeBPY `verify` 对已审计的 `wuwa` / `WutheringWaves` 和 `hsr` / `HonkaiStarRail` 入口按只读观察处理，不新增 managed pending。WuWa 已有源码审计与真实对照，确认场景 dirty、图像、网格及节点输入和绑定保持一致；HSR 当前 `validate` 及所调用的材质／pass 验证均为读取和断言，已完成源码审计、模拟回归和下述真实后台合成管理边界检查，未宣称完整 HSR 角色验证。它们不清除已有 pending，也不绕过已有场景或图像 dirty 保护。Genshin reconstruction 验证会临时切换并恢复材质及 Outline 绑定；ZZZ 验证会 reload 引用图像，均保留可能写入分类。结构检查通过与保存确认分别报告；需要确认落盘时，显式保存后读取 session 状态。

保存返回 `pending` 或 `unknown`、调用超时或连接中断后，先观察现场。未知写入不自动重试。通用 Python 里的保存仍可能返回保守状态，应由明确文件入口确认。

`save_unconfirmed` 后的下一步是只读 `blender_scene__get_session_info`，不是 `open_scene`：只有 `file_state` 确认 saved、场景和图像 clean、managed pending 已清除时才继续重开或关闭。仍未知时保留现场，按回执诊断；不要盲目重复保存。失败的 open 也是潜在写调用，会推进 managed revision，使之前延迟保存回执不能再直接确认，因此不要用 open 探测是否已保存。

固定观察优先复用 `blender_material_library__list_images` 和 `blender_material_library__image_file_status`（参数 `image_name`）检查图像登记与单张文件状态；这不是全量依赖闭包证明，序列、UDIM 和链接库依赖须按对应工具与业务验证补充。视口用 `blender_scene__get_viewport_info`（空参数），返回所有打开窗口的活动 VIEW_3D 区域、shading、透视类型、位置、旋转与距离；无 GUI 返回空列表。它不切换区域、不更新依赖图、不加载像素、不保存，也不宣称画面或几何验收通过。两类固定观察不需要通用 Python，因此不会新增 managed pending；既有 dirty/pending 不会被它们清除。

`blender_scene__get_session_info` 一次返回当前进程的精确 `pid`、场景与 `file_state.dirty_images`、原生 `jobs` 和各窗口是否播放的 `animation_playing`。原生作业固定观察 `RENDER`、`RENDER_PREVIEW`、`OBJECT_BAKE`、`COMPOSITE`、`SHADER_COMPILATION`，不支持的项返回 null 和 `job_errors`，不伪报为空闲；它不查询 Fleet。此入口保持只读，不使用通用 Python 做进程或作业探测，也不以这些状态单独证明可以关闭窗口。

2026-10-20，[后台合成脚本](../../tests/native_session_observation.py)通过指定 alpha.4 Cocoa 修复版（Blender 5.2.0 LTS、build `4b0f35d53dec`）运行，隔离用户配置且不启动 GUI。PID 13173 的三种状态为 clean、只读 Python 查询造成的既有 managed pending、脏图像；session 查询及 `hsr` / `HonkaiStarRail` 入口均保留 revision、文件观测及像素，后两者仍被替换保护拒绝。5 类原生 jobs 在这次空闲进程中均返回 false，playback 为 false；未测试活跃作业或 GUI 播放。HSR 的业务验证及 checkout gate 使用明确 fixture 替身，其余 project、文件状态和 bridge 路径为真实实现；未读取真实角色或资产。启动参数、日志和完整回执保留在 `/var/folders/kg/ptj3dszn3z94sz48jcqvlvd00000gn/T/dcc-native-session-20261020-z9g6hges/`，运行耗时 4.054 秒，退出码 0。这是边界验收，不是构建耗时测量或部署验收。

失败传播按业务回执判断：NodeBPY 入口未返回 `ok:true` 时，adapter 返回 `success:false` 并保留原结果。Core 0.20.45 的本地 HTTP MCP 回归已确认，该结果同时成为 `structuredContent.success:false` 和外层 `isError:true`；`full` 两种取值均保留失败证据。依赖动作只有在业务成功时继续，HTTP 200 或 CLI 正常退出不能单独证明前一步成功。此检查无需真实 Blender，不代表已部署版本完成验收。

## 本机验收边界

最新[第二阶段验收](/Users/chenrui/.codex/worktrees/addon-capabilities-research/blender-mcp-runtime/outputs/research/plugin-management-2026-10-17/核心接入第二阶段.md)已完成三游戏真实共作、Hiyuki 历史 BVH/clip 和 Fleet 0–2030 计算回收及本地缓存检查。DCC 1,936 项、NodeBPY 730 项测试通过（另有 43 项跳过）；Vivian 仍是 diagnostic_only，物理表面质量未重验。三个完成负载的本轮私有实例已保存后正常关闭，另一次未写入场景的进程退出记录保留，历史窗口未动；未切换生产。下列为第一阶段的固定目标与历史回执。

当前目标的[原生回执](/Users/chenrui/.cache/blender-mcp-runtime/dcc-migration-20261017/npr-alpha4-mmd-native-01)使用 NPR alpha.4 精确二进制和 MMD Tools native API1 安装包。二进制 SHA-256 为 `e498d282bb12d00dbec6af4e556309b1c148512ec5fb93f19d15c81a93b3962a`，插件包 SHA-256 为 `63bef7589b90dd38b0b76178593934141d41437ee3be073aaaed85fb4eafe3c7`。安装包位于该 NPR 版本目录的 `addons/mmd_tools-4.5.11-npr-native-api1.zip`。

独立 Profile 通过现有外层 CLI 导入合成 PMX、选择模型和执行 `mmd_tools.build_rig`，实际返回 FINISHED。2 个刚体生成 1 条 `collision_exclusions` 关系、0 个碰撞辅助对象，后端为 `native_pairs_v1`；未注册 MMR。世界参数和16项 mask 通过现有 RNA 函数读写并恢复，保存重开后关系一致。该阶段为10次真实 CLI 调用，settings 的8次函数调用在其中一次诊断 RPC 内执行，不混为 RPC 数量。[最小原生入口](../../tests/native_mmd_tools_runtime.py)要求外层实际执行导入和构建。

同一插件完成复制 Nefer 的 NodeBPY 构建，项目入口12.248秒；人工修改后的原位 verify 为2.111秒。610个对象、人工修改和视图保持，最终保存确认、GUI打开。完整求解性能和Linux部署资格复用插件所属会话的固定包证据，本轮没有重新测量或部署。33项相关回归、Ruff和skill检查通过。没有新增产品接口或改物理实现。

以下为此前 alpha.3 与官方 MMD Tools 4.5.14 的历史基线。2026-10-17 私有 run03 与 run05 使用 Blender 5.2.0 LTS、build `4b0f35d53dec951c212b2b42f8400679fda1b804`。[run03 回执](/Users/chenrui/.cache/blender-mcp-runtime/dcc-migration-20261017/npr-run-03)与 [run05 回执](/Users/chenrui/.cache/blender-mcp-runtime/dcc-migration-20261017/npr-run-05)保留输入、调用和产物。

| 验收 | 结果与限制 |
| --- | --- |
| 冷启动和共作 | ready 3.617 秒；共作 18 次 CLI 调用通过，CLI 合计 8.287 秒。脚本批次内 Blender 操作另测为 0.049 秒，不能用两者推算全部计算时间或性能提升 |
| 插件 | 官方 MMD Tools 4.5.14 安装到私有 Extension repo，完整身份、Operator 和 OpenCC wheel 导入通过。Wiggle 2 2.2.4、MBTS 当前进程启停通过 |
| RNA | 8 次工具函数调用通过，含真实插件参数与合成 PMX 导入；不是 8 次 RPC |
| 节点 | 两种 NPR 自定义节点及 revision 检查通过。真实 Nefer Face 材质嵌套 495 个节点；对其未绑定副本完成局部查询、候选验证、人工介入后的 stale 拒绝和补丁读回，原材质及共享组保持不变。此角色图没有 ShaderInfo，不将其伪报为已使用该节点 |
| 遗留 Toon | 未启用、未验证修改路径。属于可选兼容接口，不是当前 MMD Tools 或迁移的必需项 |
| NodeBPY 真实业务 | 复制 Nefer 构建通过，项目入口 12.848 秒、CLI 13.251 秒；人工修改后原位 verify 通过，入口 2.354 秒。视图和人工修改保留，最终显式保存确认。受控缺失依赖返回失败，未被外层 success 掩盖 |
| 最终冷启动 | run05 未使用热重载，14 次 CLI 调用完成插件准备、输入检查、构建、人工介入、验证与保存。ready 2.375 秒，构建入口 12.957 秒、verify 2.485 秒。610 个对象、人工修改和视图保留，GUI 打开 |

共作可用不等于角色外观或物理质量通过。后两项由对应项目验证。这些本机结果也不代表已经部署到生产。

实现提交：DCC `2540724`、`2bd8452`、`698ade8`、`6396240`、`36be7ca`、`672f7ef`、`98ce7a8`；NodeBPY `6bb7f60`、`112600d`、`d1cc188`；Runtime provider `ce9ae34`。均为本地任务分支提交，尚未推送。

集成检查为 DCC 1918 passed、42 skipped、2 deselected；后续仅验收 helper 调整，62 项相关检查通过。NodeBPY 全套 766 passed、43 skipped。Runtime 原套件 4311 passed、13 skipped，六项 stdio 因测试禁止 worktree 路径而失败；原样复制源码至非禁止路径后，六项与 61 项 provider 检查共 67 项通过，未放宽测试。

生产侧 530 个 Profile 文件和 NPR 注册哈希保持不变。本任务未写生产 MCP 配置；早先配置哈希一致，末次冷启动时观察到全局启动程序已指向 alpha.4，未回滚该变化。run05 使用进程内明确的 alpha.3 路径完成隔离验证。

run03、run04 均保存后明确关闭；run05 保留为历史验收现场，最新 alpha.4 实例见其原生回执目录的 `ready.json`。run01 的启动错误窗口保留供诊断，未自动清理。节点提交失败和不完整回退分支由模拟回归验证，不冒称原生回退已验证。

## 本会话增量的 Git 保全（2026-10-21）

本次在 `codex/workflow-feedback-closeout-20261021` 独立分支中，以 `ddf3980` 为基线保存本会话的 38 个增量文件，涵盖批量材质反馈、Closure 节点 revision、固定视口观察与导航、保存状态观察、NPR 启动和插件准备。原工作区和已有实例保持不变。

该基线包含尚未进入官方主线的 Node/RNA runtime、managed file、NodeBPY 共作和 NPR 启动基础。增量提交依赖这些既有实现，不是可直接应用于当前 `main` 的独立补丁。基线中的插件源码归属、打包和 CI 迁移另属原迁移任务，不随本次保全合并。

隔离源码导入路径已核对，14 份相关测试文件共 334 项通过，29 份变更 Python 文件的 Ruff、Skill lint 和差异格式检查通过。本次仅保存 Git 增量并运行针对性宿主检查，沿用上文已有原生证据；没有启动 Blender、重新验收运行环境或切换部署。
