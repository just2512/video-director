# 视频生成任务人工核对 API

这组接口用于处理视频供应商提交请求结果不确定的任务。典型场景是：客户端向供应商发送创建任务请求后发生超时，无法判断供应商是否已经创建并计费。

**这组接口不会重试供应商请求，也不会创建新任务。** 操作前必须先在供应商控制台核对任务状态。

## 查询项目任务

```http
GET /api/editor/generation/jobs?project_id=<project-id>
```

可选按状态过滤：

```http
GET /api/editor/generation/jobs?project_id=<project-id>&status=submission_unknown
```

响应只包含任务核对所需字段；不会返回本地 `output_file` 路径或原始供应商响应。

重点状态：

- `submitting`：已持久化提交预留，供应商请求正在进行。
- `submission_unknown`：提交结果不确定，禁止自动重复提交。
- `running`：供应商任务已确认存在，可继续轮询。
- `downloading`：供应商已完成，正在落地本地文件。
- `done`：产物已落地。
- `submission_rejected`：人工确认供应商未创建任务，预留已释放。
- `failed` / `cancelled`：任务终态。

## 人工确认供应商任务存在

在供应商控制台找到对应任务后，将真实的供应商任务 ID 绑定到预留任务：

```http
POST /api/editor/generation/jobs/resolve
Content-Type: application/json

{
  "project_id": "project-id",
  "job_id": "local-job-id",
  "provider_task_id": "provider-task-id"
}
```

服务端在项目任务锁内检查状态与重复绑定，然后原子持久化为 `running`。

## 人工确认供应商没有创建任务

只有在供应商控制台明确确认没有对应任务、且没有产生计费后，才可释放预留：

```http
POST /api/editor/generation/jobs/resolve
Content-Type: application/json

{
  "project_id": "project-id",
  "job_id": "local-job-id",
  "confirm_no_provider_task": true
}
```

服务端会将任务收敛为 `submission_rejected`。之后用户可以重新发起一次新的生成请求。

## 拒绝条件

- 两种核对结果必须且只能选择一种。
- 只允许处理 `submitting` / `submission_unknown`。
- 已绑定到本项目其他任务的供应商任务 ID 不可重复使用。
- 不存在的项目或任务、非法 ID、终态任务均拒绝修改。
- 任务状态与核对结果通过现有项目任务锁及原子台账写入持久化。

## 操作纪律

1. 先查询本地任务，再查询供应商控制台。
2. 找到供应商任务：提交 `provider_task_id`。
3. 确认供应商没有创建：提交 `confirm_no_provider_task: true`。
4. 无法确定时，不要调用 resolve；保留 `submission_unknown`，避免重复计费。
