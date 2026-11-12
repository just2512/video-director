# 本机验收约束与操作清单

> 适用仓库：`just2512/video-director`
> 文档性质：本机手工验收约束，不是产品功能需求，也不授权修改用户配置。
> 对应云端基线：`6b54e2c5c67e635366506c883f305418c6631e17`（2026-11-12）
> 相关执行记录：`docs/execution-ledger.md` §76–§78、`docs/parallel-workstreams.md` 的最终关闭记录。

## 1. 当前验收边界

| 项目 | 当前状态 | 本机动作 |
|---|---|---|
| 产品实现及默认云端 CI | PASS | 不重复执行，不因本机检查而改动产品代码 |
| 本机模型配置保留 | UNKNOWN | 按第 3 节进行只读核对 |
| NVIDIA 实时模型连通性 | BLOCKED_ENDPOINT | 先做配置核对；只有用户明确授权时才进行一次短超时探测 |
| 用户真实生产素材性能 | MISSING | 用户自选代表性项目，按第 5 节采集；不以公共样本代替 |

本机验收不得把 UNKNOWN 写成 PASS。没有执行或没有证据的项目必须保留 UNKNOWN / MISSING。

## 2. 强制约束（不可违反）

1. **不覆盖、不迁移、不重置用户配置。** 不删除配置目录，不清空缓存，不执行初始化向导中的重置选项，不覆盖现有模型设置。
2. **先备份，后验证。** 任何可能写入配置的操作之前，先复制相关配置文件；本清单默认只读检查。
3. **禁止输出密钥。** 不运行 `env`、`set`、`Get-ChildItem Env:` 等会批量显示环境变量的命令；不截图或粘贴 API Key、Authorization Header、Cookie、完整带凭据 URL。
4. **不把本机密钥提交到 Git。** 不把真实密钥写进 `.env.example`、日志、测试夹具、Issue、Actions artifact 或本文件。
5. **不擅自改模型、Provider、Base URL、代理、超时和重试策略。** 先记录现状；只有用户明确要求修改时才改。
6. **禁止自动重试 NVIDIA 实时请求。** 已有两次约 210 秒的超时证据；本机探测如获用户授权，只允许一次，建议总超时不超过 30 秒，不做循环重试。
7. **本机验收与 CI 隔离。** CI 不得读取用户电脑文件、密钥、用户目录或本机模型配置；不得把本机配置上传为 artifact。
8. **不因本机验收未通过而开启新功能批次。** 只有发现可复现的产品合同违例，才按执行台账的重新开启条件处理。
9. **不上传生产素材，除非用户明确授权。** 性能测试优先在本机完成；报告只保留必要的文件标识、媒体参数、耗时和错误摘要。
10. **任何步骤遇到配置缺失、路径不确定、数据可能丢失或要求输入密钥到非可信界面时，立即停止并标记 BLOCKED，不猜路径、不自动修复。**

## 3. 本机模型配置保留检查（只读优先）

### 3.1 记录运行环境

在项目实际使用的 Windows 用户账户下操作。先记录：

- Windows 版本；
- 项目工作区路径和当前 Git commit；
- 应用版本 / 启动方式；
- 当前选择的 Provider 和模型名称；
- 当前 Base URL（可记录域名和路径，不记录任何凭据参数）；
- 是否使用本机代理（只记录是否启用及非敏感代理类型，不记录认证信息）。

不要为了检查而重新安装依赖、清理缓存、重建配置或切换分支。

### 3.2 找到配置位置

1. 优先通过应用设置界面查看 Provider、模型、Base URL 等非敏感字段。
2. 若必须查看文件，只查看应用文档明确指出的配置文件；不要全盘搜索密钥。
3. 若配置位置不明确，标记 `CONFIG_LOCATION=UNKNOWN`，停止文件级检查。不得猜测文件名或目录。
4. 需要记录配置文件时，只记录路径、文件大小、修改时间和 SHA-256；不要把含密钥的文件内容贴到聊天或提交到仓库。

### 3.3 备份与前后核对

若用户决定进行任何会改变配置的操作：

1. 先退出应用。
2. 将相关配置文件复制到用户自行选择的本机备份目录；备份目录不得位于 Git 仓库内。
3. 记录原文件 SHA-256。
4. 操作后重新计算 SHA-256，并通过应用设置界面确认 Provider、模型、Base URL 等非敏感字段未被意外改变。
5. 若配置文件发生变化但原因不明，停止，不覆盖回写；保留前后副本供用户决定。

只读检查完成且配置位置、非敏感字段和文件完整性均有证据，才可记录 `LOCAL_CONFIG_PRESERVATION=PASS`。否则保持 UNKNOWN 或 BLOCKED。

## 4. NVIDIA 连接检查（需单独授权）

云端两次请求均在约 210 秒后超时，分类为 `BLOCKED_ENDPOINT`；实际 Film AI token-stream 测试没有启动。因此，SSE 顺序、审批安全和应用层实时集成均未被验收。

本节不授权自动调用。只有用户明确要求进行一次本机连通性探测时才执行：

- Endpoint：`https://integrate.api.nvidia.com/v1`
- Model：`z-ai/glm-5.3-flash`
- API Key 必须从用户已有的安全凭据存储或进程环境读取，不得写入命令文本、脚本文件或日志。
- 请求只做最小化连通性探测；总超时上限 30 秒；最多一次；不循环、不自动重试。
- 日志只保留 HTTP 状态、耗时、错误类型和 request ID（若服务端提供且不含凭据）；必须清理 Authorization Header 和响应中可能包含的敏感信息。
- 探测失败只说明 endpoint / 网络 / 凭据链路仍不可用，不能直接判定产品代码失败。
- 探测成功也只证明基础连通性，不能据此判定 Film AI 实时验收通过。

只有在 endpoint 有响应后，且用户再次明确授权完整实时验收，才运行项目既有的 live Film AI 测试。必须保存实际测试输出；不能用健康探测替代 SSE token 顺序、审批安全和应用集成测试。

结果字段：
- `NVIDIA_ENDPOINT_PROBE`: PASS / FAIL / BLOCKED / NOT_RUN
- `NVIDIA_FILM_AI_LIVE_ACCEPTANCE`: PASS / FAIL / NOT_RUN
- `EVIDENCE`: 日志或测试报告的本机安全路径（不得包含密钥）

## 5. 用户真实生产素材性能验收（可选）

此项当前为 `MISSING`。公共 Big Buck Bunny 样本的云端端到端验收已 PASS，但不能代替用户真实项目性能数据。

如用户决定验收：

1. 由用户选择一个有代表性的项目和素材；不得擅自从用户目录搜集文件。
2. 记录素材数量、总大小、视频分辨率 / 帧率 / 编码、音频轨道数、项目场景数，以及硬件和软件版本。
3. 在本机按项目当前受支持的正常工作流执行预览、渲染、导出和 QC；不要为得到更快结果而降低项目质量参数或跳过 QC。
4. 记录各阶段开始 / 结束时间、耗时、输出文件大小、退出状态和错误摘要。
5. 同一素材、同一配置、同一硬件条件下重复测试时，明确标注每次结果；不得把单次数据描述为稳定性能基准。
6. 不上传素材、项目文件、导出文件或含个人信息的日志，除非用户明确授权。

若项目没有公开、稳定、可复现的本地基准命令或流程，不得自行编造 CLI 参数；使用应用 UI 的正常操作路径，并把手工步骤记录下来。

结果字段：
- `USER_PRODUCTION_MEDIA_PERFORMANCE`: PASS / FAIL / MISSING / BLOCKED
- `PROJECT_PROFILE`: 不含隐私信息的项目规模与媒体参数
- `HARDWARE_SOFTWARE`: 设备与版本
- `TIMINGS`: 预览 / 渲染 / 导出 / QC 各阶段耗时
- `EVIDENCE`: 本机报告路径

## 6. 本机验收报告模板

完成后只填写有实际证据的字段：

```text
Repository:
Git commit:
OS:
App version:

LOCAL_CONFIG_PRESERVATION: PASS / FAIL / UNKNOWN / BLOCKED
CONFIG_LOCATION:
CONFIG_BACKUP:
CONFIG_BEFORE_SHA256:
CONFIG_AFTER_SHA256:
PROVIDER_MODEL_BASE_URL:（仅非敏感字段）

NVIDIA_ENDPOINT_PROBE: PASS / FAIL / BLOCKED / NOT_RUN
NVIDIA_FILM_AI_LIVE_ACCEPTANCE: PASS / FAIL / NOT_RUN
NVIDIA_EVIDENCE:

USER_PRODUCTION_MEDIA_PERFORMANCE: PASS / FAIL / MISSING / BLOCKED
PROJECT_PROFILE:
HARDWARE_SOFTWARE:
TIMINGS:
MEDIA_EVIDENCE:

Unexpected changes:
Errors:
Stopped at:
```

## 7. 完成判定与停止条件

- 本机配置核对有完整证据：该项才可从 UNKNOWN 更新为 PASS。
- NVIDIA 基础探测成功：只更新 endpoint probe；完整 Film AI 实时验收仍须单独执行并通过。
- 生产素材性能有代表性项目数据和可复现记录：才可更新该项状态。
- 若用户不提供生产素材、不授权实时模型调用或不执行本机配置检查，对应状态保持 MISSING / NOT_RUN / UNKNOWN，不影响已经通过的云端默认 CI 结论。
- 不新增产品功能，不增加无目标测试，不为追求全绿而修改断言、放宽阈值或伪造证据。
- 只有发现可复现的既定合同违例，或用户提供了本文件所列的外部验收输入，才重新开启相应工作。

**本文件只定义安全边界和验收证据要求，不代表本机步骤已经执行。**
