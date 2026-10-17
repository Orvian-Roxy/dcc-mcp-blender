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

Agent 在选定实例工作，人可以查看、修改或中断。通常依次完成这些动作。

需要新实例时，使用 [NPR 启动入口](../../tools/npr_runtime.py)，复用官方 CLI 的 gateway 与 `start-instance`：

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

NodeBPY `verify` 的重建验证可能临时切换并恢复材质绑定，因此仍按可能写入处理。结构检查通过时，保存状态也可能为未知；需要确认落盘时，显式保存后读取 session 状态即可。

保存返回 `pending` 或 `unknown`、调用超时或连接中断后，先观察现场。未知写入不自动重试。通用 Python 里的保存仍可能返回保守状态，应由明确文件入口确认。

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
