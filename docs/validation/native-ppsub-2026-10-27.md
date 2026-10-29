# 原生 PPsub 与代理页修复

> 后续已补全完整 Clash 配置的实际节点组与规则读取，并限制这类投影只读；最新说明见 [当前配置读取补全](ppsub-current-config-2026-10-27.md)。本文后面的“仅载入链接并可切换为 PPsub”描述为先前实现，已被替代。

2026-10-27，Asia/Shanghai。已部署至 server5（192.168.1.41）隔离 Debian 实例 `paopaogw-debian-lab`。

| 项目 | 当前值 |
|---|---|
| 版本 | `0.1.0-hnm-v1.20261027.1` |
| 原生面板 | `http://192.168.1.41:18780/ui/#/proxies` |
| PPsub | `http://192.168.1.41:18780/ui/#/ppsub` |
| 原订阅导入地址 | `http://192.168.1.41:18781/`，现在进入同一原生面板 |
| HNM observer API | `http://192.168.1.41:18788`，契约 v1.1 不变 |
| 实例 UUID | `771563c8-b58b-4960-863c-0149cdeb52b4` |
| Git 基线 | `67600f73bb27c8bb6cc451069314060201c4f7e4`，含未提交改造 |
| 候选源摘要 | `38dd596796e241447558912513009bc44aa443b25a7a463d66abf150cd05c05c` |
| Debian 包 SHA256 | `a647b96ac8ad0414be48705a5e7a3213a676ad8d9dfb77c41093a89e8d350ea7` |

代理页此前停留在“加载中”，原因是同一订阅组和 provider 的成员列表含重复节点名，触发 Svelte `each_key_duplicate`。现在在面板代理快照中按首次出现顺序去重，保留当前选择；去重不会把普通组误标为 smart。未修改远端订阅、mihomo 配置或节点选择。

独立订阅 HTML 页面已移除。原生 PPsub 继续负责订阅提供商、节点组、规则的编辑以及 JSON 导入/导出，仅新增保存、应用和当前配置载入能力。编辑器导出的实际对象直接提交，沿用原生面板 bearer，无第二次登录。

内部 `paopaogw-subscription` 仅监听 `127.0.0.1:8789`。原生面板在完成原有 ACL/bearer 检查后，只向固定本机服务转发 config、status 和 apply；HNM observer Token 不能访问。保存检查配置版本，加载失败且无外部修改时恢复原配置。

当前仍使用用户导入的完整 Clash/Mihomo 订阅，模式为 `suburl`。在 PPsub 选择“当前配置”会把现有链接载入草稿，并明确提示：保存后使用 PPsub 编辑器中的节点组和规则，不会自动转换远端完整 YAML 的所有分流配置。此次没有替用户点击保存或转换现有规则。

验证结果：

- Python 回归 31 项通过；Rust sb-web 82 项、web_proxies 12 项通过；release 构建成功。
- 模拟网关上的原生 PPsub 载入、编辑、实际导出对象提交、应用轮询、完成状态及路由清理通过；没有新增独立编辑表单。
- 真实浏览器验证代理页显示 81 个代理对象、30 个 provider、29 个可见分组；无重复 key 错误和其他页面异常。
- 从原 18781 地址进入面板，PPsub 载入当前链接、返回代理页及刷新均复用一次登录；手机页面无横向溢出。
- 未认证/observer 凭据访问管理桥均为 401；错误方法为 405；40 KB 无效输入进入校验并返回 400，超过 1,100,000 字节返回 413；这些请求没有写入真实配置。
- 安装前后对 `/etc/paopaogw` 全部文件做摘要比对，一致；UUID、订阅、管理密码、observer Token 和 revision key 均保留。
- 真实订阅的保存/应用未重复执行，正向提交由模拟配置验证；已运行的真实订阅及代理数据读取已实测。

新包保存在 server5 `/opt/paopaogw-lab/packages/`。更新前程序与 UI 备份为 `/opt/paopaogw-lab/before-native-ppsub-20261027.tar.gz`；部署记录为 `/opt/paopaogw-lab/native-ppsub-deploy-2026-10-27.json`。报告不包含订阅地址或密码。
