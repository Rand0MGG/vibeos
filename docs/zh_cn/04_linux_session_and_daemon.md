# Linux 会话与 daemon

`vibed` 由一个 asyncio supervisor 管理数据库生命周期、任务 scheduler、outbox
dispatcher、D-Bus service 和 loopback HTTP 兼容监听。启动顺序固定；任一组件失败会
回滚已启动组件。drain 后拒绝新请求，等待在途请求结束，再逆序停止。

D-Bus 是主要 daemon 控制面：

- `CommandRequest`、`Capabilities`、`PendingReviews`；
- `TasksList`、`TaskShow`、`TaskControl`；
- `AppsList`、`WindowsList`、`AuditTail`、`Status`。

HTTP 仅绑定 loopback，带弃用响应头，并把 `/v1/*` 请求转发给同一 Broker/Task
Store。`VIBEOS_RUNTIME=http` 保持兼容；auto 模式依次尝试 D-Bus、HTTP 和本地
开发 runtime。显式 D-Bus 或要求 daemon 时不会静默绕过相应边界。

数据库启动检查 Alembic head、WAL、外键和 busy timeout。scheduler 扫描
`ready/running/verifying/reconciling/cancel_requested` 及到期 wait；处理前必须
获取 lease。outbox 至少投递一次，consumer 按消息与 consumer 组合去重。

真实 GNOME/Wayland、portal、通知显示、剪贴板与窗口副作用仍必须在 Fedora
GNOME VM 验收；WSL 只负责 Linux 用户态和 D-Bus 预验证。
