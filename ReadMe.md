# PaoPaoGateway · Debian / HNM fork

基于 [Lovest20018/PaoPaoGateWay](https://github.com/Lovest20018/PaoPaoGateWay) 的 Rust FakeIP 网关，改为 Debian 原生部署，并提供 HNM observer API v1。

- [Debian 安装、升级、运行与卸载](docs/debian-deployment.md)
- [HNM API 契约及模式能力](docs/integration-api-v1.md)
- [OpenAPI 3.1](docs/openapi-v1.json)
- [确定性 HTTP 样例](integration/fixtures/samples.json)
- [测试及验收记录](docs/validation/README.md)
- [上游配置和 ISO 文档归档](docs/upstream-iso-reference.md)

核心、API 和 HNM 独立运行。观察凭据不能重启、切换节点、断开连接或修改配置。已提供状态/指标、真实 FakeIP 缓存查询、有界 TCP 连接与模式派发记录、mihomo 原生规则/节点快照、持久观测历史和分阶段白名单探测。轮询和事件缓冲都有覆盖缺口，完整流量历史及远端 DNS 仍不能保证。完整透明转发验收需要独立证据，不能以本机代理探测代替。

Debian 版移除 Cloudflared，mihomo 直接从官方 GitHub 稳定版在线安装、校验、更新和回退。PPsub 支持读取及编辑完整 Clash 配置，自动刷新冲突时保留最后成功配置。新安装采用已确认的测试默认开关，显式代理使用随机密码并限制私网访问；服务调度统一为 Asia/Shanghai。

CI 构建 Debian amd64 安装包和校验文件，不再构建/发布 ISO 或推送上游 Docker Hub 镜像。旧 ISO 脚本保留以便追踪上游；Debian 运行和发布均不调用这些脚本。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/export-api-contract.py
python3 scripts/check-api-contract.py  # 需要 python3-jsonschema
cd sniffbox
cargo test --workspace --locked
cargo build --release --locked -p sniffbox
```

保留上游版权与 GPL-3.0 许可，见 [LICENSE](LICENSE)。
