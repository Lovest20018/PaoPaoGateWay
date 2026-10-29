# 验证记录

- [2026-10-27：完整 Clash 配置在原生 PPsub 中编辑和保存](ppsub-policy-edit-2026-10-27.md)

- [2026-10-27：PPsub 当前配置完整读取及 Clash 格式写回保护](ppsub-current-config-2026-10-27.md)

- [2026-10-27：原生 PPsub 一次登录与代理页重复节点修复](native-ppsub-2026-10-27.md)

本地 `python -m unittest discover -s tests -v` 验证 API 边界、数据时效、故障及安装更新保留行为。`scripts/check-api-contract.py` 校验 OpenAPI/确定性样例摘要。

独立 Debian 实例使用 `deploy/debian/Dockerfile.lab` 和 `scripts/verify-lab.py` 验证真实核心与 systemd。容器仅用于隔离验收，正式部署无需 Docker。详细结果、构建/契约摘要和覆盖缺口另存本目录；生成 fixture 不属于真实运行证据。
