# NPR 共作的归属、流程与验收状态

DCC 是主开发项目。当前开发基于 adapter 0.2.21，验收固定 Core、server 和 CLI 0.20.45。此 checkout 的实现和私有 NPR 验收从未切换生产 MCP。迁移范围见 [Spec #14](https://github.com/Orvian-Roxy/blender-mcp-runtime/issues/14)。

## 谁负责什么

| 内容 | 归属 |
| --- | --- |
| 实例路由、主线程执行、插件管理、RNA 和节点工具 | DCC adapter。沿用上游 Core、server 和 CLI |
| MMD、Toon、MBTS 和 Wiggle 定向操作 | Runtime `ce9ae34` 的原生 Python provider，经 [DCC 入口](../../src/dcc_mcp_blender/_native_runtime_ops.py)直接调用 |
| 构建、业务验证、材质配方和资产 | NodeBPY-Bridges 与 Assets。DCC [接入层](../../src/dcc_mcp_blender/_nodebpy_runtime_ops.py)调用项目入口 |
| 远端烘焙、物理计算、渲染和作业 | 现有 Fleet CLI 与 Worker，保持原接口和配置 |

Runtime 在此只提供原生依赖。DCC 不转调旧 MCP，也不接管 NodeBPY 业务代码或资产。生产配置、现有会话和 Worker 继续使用原入口。

## 日常共作

Agent 在选定实例工作，人可以查看、修改或中断。通常依次完成这些动作。

1. 选择明确的 `instance_id`。后续 CLI 调用持续带上 `--dcc-type blender --instance-id <id>`，避免连接到其他 Blender。
2. 按需 `load-skill`，再调用该 skill 的工具。NodeBPY 使用 `blender-nodebpy-runtime`，插件使用 `blender-dev` 和 `blender-extensions`。
3. 启动独立 NPR 时，用进程环境变量 `NODEBPY_BLENDER_BINARY` 明确指定已选 NPR 的实际可执行文件；否则 NodeBPY 会读取全局 MCP 启动设置。调用 `blender_nodebpy_runtime__prepare`，指定项目 checkout、已有依赖路径和 MMD 插件模块名。Extension 使用完整 `bl_ext.<repo>.<package>` 身份。成功须包含当前进程的插件注册和项目 doctor 结果。
4. 用 `blender_nodebpy_runtime__project` 执行项目 `run` 或 `verify`。`run` 构建独立候选并保留 GUI，`verify` 检查当前编辑。替换模板遇到 `unsaved_changes` 时，先明确保存当前场景和图像。参数见 [工具契约](../../src/dcc_mcp_blender/skills/blender-nodebpy-runtime/tools.yaml)。
5. 需要改图时，使用 `blender-node-runtime` 的局部查询、schema 和 revision 修改工具。先读取目标图及 revision，再提交有限修改。具体边界见 [节点契约](../../src/dcc_mcp_blender/skills/blender-node-runtime/tools.yaml)。

普通编辑不要求 task、参与票据、完整检查清单或外观接受。工具保留当前视图，默认不关闭 Blender。项目验证、确认保存和人的外观判断分别报告。

复跑构建时，在已完成 `prepare` 的独立实例中使用新输出路径：

```bash
dcc-mcp-cli --base-url <gateway> --no-auto-gateway --non-interactive \
  --timeout-secs 300 call blender_nodebpy_runtime__project \
  --dcc-type blender --instance-id <instance-id> \
  --json '{"project_root":"/absolute/NodeBPY-Bridges","action":"run","game":"genshin","profile":"/absolute/copied-profile.json","output":"/absolute/Assets/Genshin/characters/<character-id>/work/<new-candidate>.blend"}'
```

候选已打开时，将 `action` 改为 `verify`，使用同一输出。共作入口跳过纯视口交付要求，保留材质、灯光、色彩管理和结构检查。旧项目入口继续使用原视图约定。

## 保存状态为什么仍然保守

[共享文件入口](../../src/dcc_mcp_blender/_scene_files.py)在明确打开、替换或关闭前保护场景、图像像素和 managed pending 修改。保存须同时读回 Operator 结果、实际文件目标、场景状态和文件大小及修改时间。已有文件或外层 `success` 不能证明本次保存完成。

managed pending 记录工具可能产生但 Blender dirty 标志尚未反映的修改。普通写工具和任意 Python 调用会保守地产生这个标记，因此只准备依赖或运行查询脚本也可能多出一次保存。这是当前避免静默丢失修改的代价。使用顶层 `blender_scene__save_scene` 保存，使用只读的 `blender_scene__get_session_info` 观察状态。

`read_only` 元数据必须符合工具行为。真实只读工具不会产生 managed pending，局部查询也就不会无故阻挡后续文件操作。不能为了减少保存而把写工具标成只读。

NodeBPY `verify` 的重建验证可能临时切换并恢复材质绑定，因此仍按可能写入处理。结构检查通过时，保存状态也可能为未知；需要确认落盘时，显式保存后读取 session 状态即可。

保存返回 `pending` 或 `unknown`、调用超时或连接中断后，先观察现场。未知写入不自动重试。通用 Python 里的保存仍可能返回保守状态，应由明确文件入口确认。

## 本机验收边界

以下是 2026-10-17 私有 run03 与最终冷启动 run05 的实测，使用 NPR 0.4.0-alpha.3、Blender 5.2.0 LTS、build `4b0f35d53dec951c212b2b42f8400679fda1b804`。[run03 回执](/Users/chenrui/.cache/blender-mcp-runtime/dcc-migration-20261017/npr-run-03)与 [run05 回执](/Users/chenrui/.cache/blender-mcp-runtime/dcc-migration-20261017/npr-run-05)保留输入、调用和产物。

| 验收 | 结果与限制 |
| --- | --- |
| 冷启动和共作 | ready 3.617 秒；共作 18 次 CLI 调用通过，CLI 合计 8.287 秒。脚本批次内 Blender 操作另测为 0.049 秒，不能用两者推算全部计算时间或性能提升 |
| 插件 | 官方 MMD Tools 4.5.14 安装到私有 Extension repo，完整身份、Operator 和 OpenCC wheel 导入通过。Wiggle 2 2.2.4、MBTS 当前进程启停通过 |
| RNA | 8 次工具函数调用通过，含真实插件参数与合成 PMX 导入；不是 8 次 RPC |
| 节点 | 两种 NPR 自定义节点及 revision 检查通过。真实 Nefer Face 材质嵌套 495 个节点；对其未绑定副本完成局部查询、候选验证、人工介入后的 stale 拒绝和补丁读回，原材质及共享组保持不变。此角色图没有 ShaderInfo，不将其伪报为已使用该节点 |
| Toon | 缺少真实插件包，尚未完成原生验收 |
| NodeBPY 真实业务 | 复制 Nefer 构建通过，项目入口 12.848 秒、CLI 13.251 秒；人工修改后原位 verify 通过，入口 2.354 秒。视图和人工修改保留，最终显式保存确认。受控缺失依赖返回失败，未被外层 success 掩盖 |
| 最终冷启动 | run05 未使用热重载，14 次 CLI 调用完成插件准备、输入检查、构建、人工介入、验证与保存。ready 2.375 秒，构建入口 12.957 秒、verify 2.485 秒。610 个对象、人工修改和视图保留，GUI 打开 |

共作可用不等于角色外观或物理质量通过。后两项由对应项目验证。这些本机结果也不代表已经部署到生产。

实现提交：DCC `2540724`、`2bd8452`、`698ade8`、`6396240`、`36be7ca`、`672f7ef`、`98ce7a8`；NodeBPY `6bb7f60`、`112600d`、`d1cc188`；Runtime provider `ce9ae34`。均为本地任务分支提交，尚未推送。

集成检查为 DCC 1918 passed、42 skipped、2 deselected；后续仅验收 helper 调整，62 项相关检查通过。NodeBPY 全套 766 passed、43 skipped。Runtime 原套件 4311 passed、13 skipped，六项 stdio 因测试禁止 worktree 路径而失败；原样复制源码至非禁止路径后，六项与 61 项 provider 检查共 67 项通过，未放宽测试。

生产侧 530 个 Profile 文件和 NPR 注册哈希保持不变。本任务未写生产 MCP 配置；早先配置哈希一致，末次冷启动时观察到全局启动程序已指向 alpha.4，未回滚该变化。run05 使用进程内明确的 alpha.3 路径完成隔离验证。

run03、run04 均保存后明确关闭；当前验收实例见 run05 的 `ready.json`。run01 的启动错误窗口保留供诊断，未自动清理。节点提交失败和不完整回退分支由模拟回归验证，不冒称原生回退已验证。
