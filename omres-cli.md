# omres-cli

面向 AI Agent / 脚本的 OMResTool 命令行工具，按《通用 AI 友好型 HTTP-CLI 封装规范》实现，参考 pipeline-x CLI 的架构。

- **配置驱动**：`internal/cli/api_cli_config.json` 决定哪些接口暴露为命令。
- **Swagger 为真相源**：`internal/cli/docs/swagger.json` 提供参数、类型、输出 schema。启动时校验配置的 `path+method` 都存在于 swagger。
- **统一输出**：所有命令输出 JSON-RPC 2.0，后端 JSON 保持原生对象，不做二次字符串化。
- **AI 可探索**：`--help`（分层）+ `describe`（完整语义契约）。
- **零业务逻辑**：CLI 仅作 HTTP 客户端，调用 `http://10.243.80.228`。

覆盖接口文档中全部 **37** 个接口。

## 构建

本环境无 Go 工具链且无网络，需在你本机构建（Go 1.21+）：

```bash
cd omres-cli
go mod tidy          # 拉取 cobra/pflag 依赖并生成 go.sum
go build -o omres-cli ./cmd/cli
```

`swagger.json` 与 `api_cli_config.json` 通过 `//go:embed` 编译进二进制，运行时不依赖外部文件。

## 使用

```bash
# 分层探索
./omres-cli --help                         # 列出所有 group
./omres-cli moc --help                     # 列出 moc 下所有 action
./omres-cli describe moc add-name          # 完整参数与输出 schema（不发网络请求）

# 执行（POST + JSON body）
./omres-cli validate do --body '{"projectId":123}'

# 路径参数按顺序作为位置参数
./omres-cli task export-struct 123 demo 0
./omres-cli moc generate-script 123 10 BoardInfo add 1   # 二进制，自动存临时文件

# 文件上传（multipart）
./omres-cli upload file --taskId 123 --file ./model.zip

# 从文件读 body
./omres-cli mml-command upsert --body-file ./cmd.json
```

### 命令分组（37 接口）

| group | actions |
|-------|---------|
| auth | login, status, logout |
| task | create, export-struct, export-result, download |
| upload | file, parse-xml |
| moc | add-name, select-name, insert-info, generate-script |
| moc-field | add-name, select-name, update-info |
| datatype | add, query-all, enum-add, enum-query-all |
| default-record | add |
| method | add-name, update-name, delete-name, select-info |
| mml-command | upsert, get |
| command-para | upsert, list |
| mml-para | list |
| command-branch | upsert, list |
| validate | do, result |
| errorcode | shield |
| info-code | add, list |
| info-module | query-all |
| overallview | search |

## 鉴权

登录、查看状态、登出都由 `auth` 组承担，登录态自动落盘，**后续命令无需再传 `--cookie`**。

```bash
# 1) 登录（推荐：密码走标准输入，不进命令历史）
omres-cli auth login --username zhangsan --password-stdin < pass.txt
# 交互式（密码不回显）
omres-cli auth login --username zhangsan
# CI/CD
$env:OMRES_AUTH_USERNAME="zhangsan"; $env:OMRES_AUTH_PASSWORD="******"
omres-cli auth login

# 2) 查看状态（本地检查，不发网络请求）
omres-cli auth status
# 额外向后端发一次只读探活，确认会话真的没失效
omres-cli auth status --online

# 3) 登出
omres-cli auth logout
```

登录成功后 Cookie 写入 `~/.omres-cli/session.json`（Windows 为 `%USERPROFILE%\.omres-cli\session.json`），
文件权限 `0600`。**密码不会出现在任何输出或文件中**，输出里的 Cookie 一律打码（`JSESSIONID=ABC******XYZ`）。

### auth status 的退出码

`auth status` 是唯一带语义退出码的命令，便于脚本 / AI Agent 直接分支判断：

| 退出码 | 含义 | 建议动作 |
|--------|------|----------|
| 0 | 已认证 | 继续后续流程 |
| 3 | 未认证或会话已过期 | 引导用户执行 `omres-cli auth login` |
| 1 | 其它错误（如 `--online` 时后端不可达） | 排查网络 / `--server`，**不要**误判为需要重新登录 |

其余命令仍保持「永远退出 0，结果看 JSON-RPC」的既有约定，不影响已有脚本。

### 会话有效期

- 后端 Cookie 带 `Expires` / `Max-Age` → 以后端为准。
- 只下发会话 Cookie（无过期时间）→ 本地按软 TTL **8 小时**判定，可用 `OMRES_SESSION_TTL_HOURS` 覆盖。

### 凭证来源优先级

请求携带的 Cookie 按此顺序解析，先命中先用：

```
--cookie  >  OMRES_AUTH_COOKIE  >  api_cli_config.json  >  ~/.omres-cli/session.json
```

`auth status` 输出的 `source` 字段会告诉你当前用的是哪一个。

其它鉴权方式（若后端改用 Token/API Key/Basic）在 `defaults.auth` 中声明 `type` 即可，对应覆盖环境变量：

| 配置字段 | 环境变量 |
|----------|----------|
| defaults.server | `OMRES_SERVER` |
| defaults.auth.token | `OMRES_AUTH_TOKEN` |
| defaults.auth.api_key | `OMRES_AUTH_API_KEY` |
| defaults.auth.username | `OMRES_AUTH_USERNAME` |
| defaults.auth.password | `OMRES_AUTH_PASSWORD` |
| defaults.auth.cookie | `OMRES_AUTH_COOKIE` |
| defaults.auth.probe_path / probe_body | 仅配置文件（`auth status --online` 用的只读探活接口） |

凭证不会出现在正常输出中；请勿把明文凭证提交到仓库。

## 输出示例

成功：
```json
{ "jsonrpc": "2.0", "result": { "code": 0, "msg": "操作成功" }, "id": "req-a1b2c3d4" }
```

后端非 2xx（JSON）：
```json
{ "jsonrpc": "2.0", "error": { "code": -32000, "message": "400 Bad Request",
  "data": { "code": 10001, "msg": "名称已存在" } }, "id": "req-..." }
```

二进制下载：
```json
{ "jsonrpc": "2.0", "result": { "file": "/tmp/download-...", "content_type": "application/octet-stream", "size": 20480 }, "id": "req-..." }
```

## 目录结构

```
omres-cli/
├── cmd/cli/main.go                    # 入口
├── internal/cli/
│   ├── cli.go                         # 加载配置+swagger，校验，构建命令树
│   ├── builder.go                     # 由配置+swagger 生成 Cobra 命令
│   ├── config.go                      # 配置解析、env 覆盖、校验
│   ├── swagger.go                     # Swagger 解析与操作索引
│   ├── describe.go                    # describe 命令 + 辅助函数
│   ├── auth.go                        # auth login / status / logout
│   ├── session.go                     # 会话落盘、过期判定、Cookie 打码
│   ├── prompt.go / prompt_*.go        # 交互式输入（密码不回显，零外部依赖）
│   ├── httpclient.go                  # HTTP 客户端、鉴权注入、响应封装
│   ├── jsonrpc.go                     # JSON-RPC 2.0 输出
│   ├── types.go                       # 数据模型
│   ├── api_cli_config.json            # CLI 命令配置（go:embed）
│   └── docs/swagger.json              # Swagger（go:embed，由 tools 生成）
├── tools/build_swagger.py             # 由 API 文档生成 swagger.json
└── go.mod
```
