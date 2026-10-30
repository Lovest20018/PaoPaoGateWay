# 上游合并验证（2026-10-30）

本次将官方 `4a5757e93a46669a54c9e5864d7b4a8d5484da76`（Month 10 Cumulative Update）合入 Debian/HNM 分支。原有定制先保存为 `f40c40c`，恢复分支为 `codex/debian-hnm-before-upstream-20261030`；完整源码备份在忽略目录 `dist/before-upstream-merge-20261030.tar.gz`，SHA256 为 `a761e7537aeb4444b848509ff8c8e221564a211224494868a813270a9542ea5d`。

## 合并处理

- 解决 `ppg.sh`、`production.rs`、`outbound.rs`、`runtime.rs` 四个冲突文件，保留 Debian 隔离、禁用 Cloudflared、私有网段认证代理及 HNM 配置锁/身份/观测记录。
- 吸收上游 Cargo 清单与锁文件、多地址重试、UDP 配额、HTTP 转发、嗅探、PPsub hosts/用量等更新。Debian 不启用新增的宿主网卡 RPS 调优。
- 直连保留客户端已解析的 IPv4、IPv6 和 IP 字面地址；FakeIP 域名继续使用多地址解析/重试。HNM 对直接使用的目标地址记录 `literal_destination`，避免误报网关进行了 DNS 解析。
- 重新定位新面板 `bundle.Bipr4Z8O.js` 的 PPsub 加载、挂载及身份钩子；保留未知资源版本拒绝打包的检查。
- 独立审查发现的两处编译遗漏（函数边界、测试调用新参数）均已修正，并补充直连证据回归测试。

## 验证

- Python：54 项通过；覆盖订阅/策略、证据 API、历史、更新、安装暂存及防篡改、面板打包等。
- API 契约：23 个样本及摘要校验通过。
- 真实上游面板：JavaScript 语法检查及无头 Chrome 合成配置流程通过，覆盖完整 Clash 无修改往返、原生 PPsub 编辑保存、共享认证、移动端及路由清理。
- 构建来源：经用户明确授权，仅上传 151 个 Rust/Cargo/必要资源和 shell 文件至 server5 的隔离构建目录，无运行配置或凭据。归档 SHA256 `bc8a4d47679a3abd5b2478c9ddf90194cfc8afaa608647f39a060e6fbb8f7e4b`。首轮测试编译发现新增断言的 `PathBuf == &str` 类型不匹配，修正测试并单独同步 `production.rs`（SHA256 `b33bf21e6424eaf921fa069fc47ca63ab66197c66a768fad9cefc137dddb37a8`）后重跑。最终本地与远程逐文件清单的规范化摘要同为 `38ae8d44a32079ab87dc17509e9af28f2cb949b1a10d123fcbba8699ac46a699`；本地 151 文件与该清单一致（文本统一 LF）。
- Rust：`cargo test --locked --workspace` 通过，1019 项通过、0 失败、5 项忽略（包含依赖真实网络的测试）。Debian 调优、IPv6 回环实际连接及 HNM 目标解析证据测试均通过。
- Shell：隔离 Linux 环境中 `sh -n FILES/usr/bin/ppg.sh` 和 `sh -n FILES/usr/bin/nft.sh` 通过。
- Release：`cargo build --locked --release -p sniffbox` 通过（13m30s），构建容器退出码 0；实际运行 `--version` 返回 `sniffbox 0.2.0-upstream.20261030`。Linux amd64 ELF 为 10,832,648 字节，SHA256 `ca2461a4c57c81f425155e72eac707986ad5ef56f22753071a31ce2c1c56c589`，下载后摘要一致。
- 构建完整日志保存在忽略目录 `dist/upstream-merge-20261030-build.log`，SHA256 `c3d94e929d5a57110064297294773d06ea15a82ee9464e5df221f4bae9f69f31`。
- Debian 验证包：`dist/paopaogw-0.2.0-upstream.20261030-debian-amd64.tar.gz`，SHA256 `9031a36ec6da2fa77ac6991d416a43663c8979d41757a1c3e1de99b320d5f2d0`。37 个 manifest 文件摘要、归档 SHA256、安全路径、关键执行权限和包内二进制一致性全部校验通过。源码清单摘要为 `c5148d6b553c9f3b9349fe7307c3071cd22781e5c00c23b7aeee3fb412a9deab`。

## Git 检查

无未解决冲突；Cargo 清单和锁文件与上游一致，Debian 部署/API 定制文件与恢复提交一致。暂存差异检查发现上游生成的 `ppsub_offline.html:8` 含行尾空白，位置在 JavaScript 模板字符串内；该文件与上游完全一致，保留原始内容。排除此上游文件后 `git diff --cached --check` 通过，其余新改动无空白错误。

## 范围

构建容器使用 `rust:bookworm`、锁定依赖和两路编译。验证包的版本为 `0.2.0-upstream.20261030`，编译元数据使用上游基础提交 `4a5757e...`，用于此次合并候选验证；不是标记为最终合并 SHA 的正式发布包。

本次未重新执行完整 VM 网络拓扑、OpenVPN 和透明代理端到端验收；原有运行实例未部署或修改。合并仅在本地完成，未推送远程。后续正式升级需独立执行运行环境验收。
