# 上游 10 月累计更新合并评估

核验日期：2026-10-30。结论：可以合并，但需要适配；尚未合入当前分支，也未升级运行实例。

## 对比版本

- 本地 HEAD：`67600f73bb27c8bb6cc451069314060201c4f7e4`，此外有未提交的 Debian/HNM 改造。
- 官方 main：[`4a5757e93a46669a54c9e5864d7b4a8d5484da76`](https://github.com/kkkgo/PaoPaoGateWay/commit/4a5757e93a46669a54c9e5864d7b4a8d5484da76)，标题 `Month 10 Cumulative Update`，提交时间为北京时间 2026-10-29 23:18。
- 用户 fork `Lovest20018/PaoPaoGateWay` 的 main 本次查询仍为 `67600f7`。
- 官方新增 1 个提交，65 个路径变化，8137 行增加、1224 行删除（Git diff 统计，含压缩面板资源）。

通过 Git 从官方仓库 fetch，并对每个上游变更路径比较旧基线、当前工作文件、上游文件；临时结果保存在忽略目录 `dist/upstream-20261030/`，未使用 stash、reset 或实际 merge。

## 三方合并结果

上游变更与本地修改重叠 9 个文件，5 个可自动完成文本合并，4 个存在冲突，共 8 处冲突块。

| 文件 | 预演结果 | 需要保留的行为 |
|---|---|---|
| `FILES/usr/bin/ppg.sh` | 2 处冲突 | 保留 Debian 控制器日志脱敏、本地策略健康检查；吸收上游订阅刷新改进，不能照搬输出订阅 URL 的 echo |
| `sniffbox/crates/sniffbox/src/outbound.rs` | 2 处冲突 | 合并上游多 A 地址重试、总超时预算、可路由目标直连；保留本地 IP 字面地址和 IPv6 目标处理 |
| `sniffbox/crates/sniffbox/src/production.rs` | 1 处冲突 | 加入 UDP 会话配额；新增 `tune_rps` 需遵守 Debian 不自动调优宿主网卡的边界；继续禁用 Cloudflared |
| `sniffbox/crates/sniffbox/src/runtime.rs` | 3 处冲突 | 同时保留 HNM 身份/观测/配置锁和上游 UDP 配额；适配 `ConnectionsSource` 返回 `Arc<String>` 的签名变更 |
| `sb-web/src/native.rs`、`sniffbox/src/engine.rs`、`lib.rs`、`smart_speed.rs`、`web_proxies.rs` | 文本合并成功 | 仍须构建和语义回归，尤其流量证据、配置版本及高级 PPsub |

“文本合并成功”不代表运行兼容。上游改变了连接、嗅探及 HTTP 转发路径，需要重新核对 HNM 记录与实际处理路径的一致性。

## 已复现的无文本冲突阻断

面板从 `bundle.CWWsx0y6.js` 换为 `bundle.Bipr4Z8O.js`。将真实上游资源交给当前 `scripts/prepare_dashboard.py` 执行，结果为：

```text
ValueError: PPsub integration hook changed upstream; review before packaging
```

独立检查原有钩子：`load_hook=0`、`mount_hook=0`，规则与节点选择主钩子仍各匹配 1 次。因此必须重新定位加载和挂载入口，并检查其余身份字段钩子，再验证当前配置读取、编辑、无修改保存及高级功能。不能仅替换资源文件名。

## 值得吸收的更新

- HTTP 转发与入站握手处理改进，以及 TLS/QUIC 嗅探和负缓存改进。
- UDP 会话上限、保留配额及资源预算。
- 多地址 DNS 缓存/连接重试，真实可路由目标保留。
- PPsub 订阅 hosts、用量信息、重试及 DNS burn 处理改进。
- 智能测速、规则集及相关依赖更新。

Cargo 清单和锁文件需一起更新；包括 socket2、nix、sha2、parking_lot、yaml-rust2、wreq 等版本变化，不能只摘录 Rust 源码后沿用旧锁文件。上游默认开启的新网卡 RPS 调优需显式适配 Debian。

## 验证范围与合入条件

本轮重新运行当前改造基线：52 项 Python 测试通过，23 个 HTTP 契约样本及摘要校验通过，`git diff --check` 无空白错误。上述结果证明当前基线，**不代表上游合并候选通过**。本机无 cargo 命令，本轮未对合并候选执行 Rust 构建。

实际合入顺序建议：

1. 保存当前未提交改造的完整可恢复快照，建立整合分支。
2. 处理四个冲突文件，适配新面板钩子及新增调优参数。
3. 执行锁定依赖的 Rust 全量测试/release 构建、Python 与 API 契约检查、面板真实读写回归。
4. 在隔离 Debian 实例复验 IPv4/IPv6、UDP、OpenVPN、核心更新回退、策略冲突和历史采集，再升级现有实例。

本轮评估未推送 GitHub，未更改当前分支 HEAD、用户配置或运行实例。
