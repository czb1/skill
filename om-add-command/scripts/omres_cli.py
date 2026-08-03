"""
[Common] omres-cli 调用封装

omres-cli 的安装与登录都由上游（E2E 阶段零 Step 1）统一完成：
二进制装在 `%USERPROFILE%\\omres-cli\\` 并通过 setx 写入用户 PATH，
登录态在 `%USERPROFILE%\\.omres-cli\\session.json`。

各 sXX 脚本原本各自复制了一份查找/调用逻辑，这里统一收敛：
- `find_omres_cli()`：定位可执行文件
- `server_args()`：决定要不要给命令追加 `--server`
- `run_cli()`：执行命令并解析 JSON-RPC 2.0 输出

注意：`setx` 写入的 PATH 对**已启动**的进程不生效，所以除了 PATH
还要回落到阶段零的安装目录，否则子进程可能找不到 omres-cli。

关于 `--server`：session.json 里的 cookie 是**绑定到登录时那台 server** 的。
如果这里再用 `--server` 覆盖成另一个地址（例如登录的是
`http://10.243.80.228`，工作流却传 `https://omtool.rnd.huawei.com`），
cookie 带不过去，后端会回 `noLogin`——而 `auth status` 只看本地会话，
仍然显示"已认证"，非常容易误判成"会话过期"。
因此只要本地存在登录态，一律以登录态里的 server 为准，不再传 `--server`。
"""

import os
import json
import shutil
import subprocess
from urllib.parse import urlparse

from context import WorkflowContext, StepExecutionError

# 阶段零的安装目录（相对用户主目录）
_INSTALL_DIR_NAME = "omres-cli"
_EXE_NAMES = ("omres-cli.exe", "omres-cli")

# 阶段零登录后写下的会话文件
_SESSION_DIR_NAME = ".omres-cli"
_SESSION_FILE_NAME = "session.json"

# 需要保留旧行为（强行用 context.base_url 覆盖 server）时的逃生门
_SERVER_OVERRIDE_ENV = "OMRES_ALLOW_SERVER_OVERRIDE"


def _default_timeout() -> int:
    """
    所有子进程调用统一的超时秒数

    整包（20 个服务目录）上传/解析在后端要跑好几分钟，原先 60/120 秒会在
    客户端提前掐断。可用 OMRES_CLI_TIMEOUT 覆盖。
    """
    raw = os.environ.get("OMRES_CLI_TIMEOUT", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 300


# 各 sXX 脚本统一引用这个常量，不要再写死秒数
DEFAULT_TIMEOUT = _default_timeout()

# omres-cli(Go) 自身或后端网关的请求超时特征串
_DEADLINE_MARKERS = ("context deadline exceeded", "deadline exceeded", "client.timeout")


def deadline_hint(*texts: str) -> str:
    """
    识别 omres-cli/后端侧的请求超时，给一句可操作的提示

    这类超时和本脚本的 subprocess 超时是两回事：调大 OMRES_CLI_TIMEOUT 不解决，
    容易被误读成"客户端超时时间不够"。

    Returns:
        str: 命中时返回提示，否则返回空字符串
    """
    blob = " ".join(t for t in texts if t).lower()
    if any(marker in blob for marker in _DEADLINE_MARKERS):
        return (
            "；注意这是 omres-cli/后端侧的请求超时，不是本脚本的 subprocess 超时"
            f"（当前 {DEFAULT_TIMEOUT}s，调大 OMRES_CLI_TIMEOUT 对它无效）。"
            "整包（全部服务目录）解析耗时过长时，请只打包目标服务目录后重试"
        )
    return ""


# 后端「同名对象已存在」的报错特征串（各版本文案不完全一致，宽松匹配）
_DUPLICATE_MARKERS = (
    "已存在", "已经存在", "重复", "重名",
    "duplicate", "already exist", "already exists", "exist already", "has exist",
)


def is_duplicate_error(message: str) -> bool:
    """
    判断一条后端报错是不是「同名对象已存在」

    上传的建模文件里如果已经有同名 MOC/字段/枚举，解析阶段就会把它导入工程，
    再去 add 就会撞重复。这类冲突应该走「复用已有对象」的分支，而不是让整个
    工作流失败——需求要求的就是这个名字，改名或换工程都不对。

    Args:
        message: 后端返回的错误信息

    Returns:
        bool: 命中重复语义时返回 True
    """
    if not message:
        return False
    blob = str(message).lower()
    return any(marker in blob for marker in _DUPLICATE_MARKERS)


def iter_data_records(result) -> list:
    """
    从 CLI 返回里取出记录列表，兼容后端各接口不一致的包装形状

    见过的形状：`data` 直接是列表；`data` 是 `{"list": [...]}` / `{"records": [...]}` /
    `{"rows": [...]}` / `{"data": [...]}`；`data` 是单个对象。
    解析建模文件导入的对象走的接口和新建的不完全一样，形状差异会让「已存在」的
    字段查不出来，进而误判成「不存在」——所以这里统一兜住。

    Args:
        result: run_cli 返回的 result 字典

    Returns:
        list: 记录字典列表；取不到时返回空列表
    """
    if not isinstance(result, dict):
        return []

    data = result.get("data")
    if data is None:
        data = result.get("list") or result.get("records") or result.get("rows")

    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]

    if isinstance(data, dict):
        for key in ("list", "records", "rows", "data", "items", "content"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        # data 本身就是一条记录
        return [data]

    return []


def pick_value(record: dict, keys, default=None):
    """
    按候选键名依次取值（后端不同接口对同一概念的键名不统一，如 fieldName/attrName）

    Args:
        record: 记录字典
        keys: 候选键名（按优先级）
        default: 都取不到时的返回值

    Returns:
        第一个非空的取值
    """
    if not isinstance(record, dict):
        return default
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


# server 不一致的告警只打一次，避免刷屏
_server_mismatch_warned = False


def find_omres_cli() -> str:
    """
    查找omres-cli可执行文件路径

    顺序：OMRES_CLI 环境变量 → PATH → 阶段零安装目录(~/omres-cli) → 裸命令名。

    Returns:
        str: omres-cli可执行文件路径（找不到时返回 "omres-cli"，由PATH兜底）
    """
    override = os.environ.get("OMRES_CLI")
    if override and os.path.isfile(override):
        return override

    cli_path = shutil.which("omres-cli") or shutil.which("omres-cli.exe")
    if cli_path:
        return cli_path

    # setx 写入的 PATH 对已启动的进程不生效，回落到阶段零的安装目录
    home = os.path.expanduser("~")
    for exe in _EXE_NAMES:
        candidate = os.path.join(home, _INSTALL_DIR_NAME, exe)
        if os.path.isfile(candidate):
            return candidate

    return "omres-cli"


def session_file() -> str:
    """返回 omres-cli 会话文件路径（~/.omres-cli/session.json）"""
    return os.path.join(os.path.expanduser("~"), _SESSION_DIR_NAME, _SESSION_FILE_NAME)


def read_session() -> dict:
    """
    读取 omres-cli 持久化的会话内容

    Returns:
        dict: 会话内容；文件不存在或解析失败时返回空 dict
    """
    path = session_file()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def session_server() -> str:
    """
    返回登录时使用的 server 地址（cookie 就是绑在它上面的）

    Returns:
        str: 如 "http://10.243.80.228"；取不到返回空字符串
    """
    server = read_session().get("server", "")
    return server.strip() if isinstance(server, str) else ""


def _host_of(url: str) -> str:
    """取出 URL 的主机名（小写），用于判断两个地址是不是同一台 server"""
    if not url:
        return ""
    raw = url.strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else "//" + raw, scheme="http")
    return (parsed.hostname or "").lower()


def same_server(a: str, b: str) -> bool:
    """两个地址是否指向同一台 server（只比主机名，cookie 本来也不区分端口/协议）"""
    host_a, host_b = _host_of(a), _host_of(b)
    return bool(host_a) and host_a == host_b


def resolve_server(context: WorkflowContext = None) -> str:
    """
    决定这次调用要不要显式指定 server

    规则：
    - 设了 OMRES_ALLOW_SERVER_OVERRIDE=1 → 保留旧行为，用 context.base_url；
    - 本地有登录态 → 返回 ""（不传 `--server`），一律以登录态里的 server 为准，
      地址对不上时打一次告警，因为覆盖了就会拿不到 cookie（noLogin）；
    - 本地没有登录态 → 回落到 context.base_url。

    Returns:
        str: 要传给 `--server` 的地址；空字符串表示不传
    """
    global _server_mismatch_warned

    base_url = getattr(context, "base_url", None) if context is not None else None
    base_url = base_url.strip() if isinstance(base_url, str) else ""

    if os.environ.get(_SERVER_OVERRIDE_ENV, "").strip() in ("1", "true", "True"):
        return base_url

    saved = session_server()
    if not saved:
        return base_url

    if base_url and not same_server(base_url, saved):
        if not _server_mismatch_warned:
            _server_mismatch_warned = True
            print(
                f"  [WARNING] base_url({base_url}) 与 omres-cli 登录态的 server({saved}) 不一致；"
                f"cookie 只对 {saved} 有效，已改用登录态的 server（否则后端会返回 noLogin）。"
                f"确需访问 {base_url} 请先对该地址执行 omres-cli auth login。"
            )
    return ""


def server_args(context: WorkflowContext = None) -> list:
    """
    生成 `--server` 参数片段，没有必要显式指定时返回空列表

    用法：`cmd.extend(server_args(context))`
    """
    server = resolve_server(context)
    return ["--server", server] if server else []


def align_context_server(context: WorkflowContext) -> str:
    """
    把 context.base_url 对齐到登录态里的 server

    工作流里少量直接 HTTP 调用同样依赖 base_url，而 cookie 只对登录时那台
    server 有效，不对齐的话这些调用一样会 noLogin。

    Returns:
        str: 对齐后实际生效的 server 地址
    """
    saved = session_server()
    current = getattr(context, "base_url", "") or ""
    if not saved:
        return current

    if not same_server(current, saved):
        try:
            context.base_url = saved
        except Exception:
            # context 不允许改写时不算致命：omres-cli 侧已经不传 --server 了
            return current
    return saved


def _strip_cli_prefix(args: list) -> list:
    """兼容旧调用：调用方可能已把可执行文件路径拼在参数列表首位"""
    if args and isinstance(args[0], str):
        head = os.path.basename(args[0]).lower()
        if head in ("omres-cli", "omres-cli.exe"):
            return list(args[1:])
    return list(args)


def run_cli(context: WorkflowContext, args: list, step_name: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    执行omres-cli命令并解析JSON-RPC 2.0输出

    认证由 omres-cli 自身的登录态承担（阶段零已完成登录），这里不传任何凭据。

    Args:
        context: 工作流上下文
        args: 命令参数列表（不含omres-cli本身；含也会被自动剥离）
        step_name: 步骤名称（用于错误信息）
        timeout: 超时秒数

    Returns:
        dict: JSON-RPC result字段

    Raises:
        StepExecutionError: 执行失败时抛出
    """
    cli_path = find_omres_cli()
    cmd = [cli_path] + _strip_cli_prefix(args)
    # 调用方可能已经拼过 --server，这里不重复追加
    if "--server" not in cmd:
        cmd.extend(server_args(context))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=timeout
        )
    except FileNotFoundError:
        raise StepExecutionError(
            step_name=step_name,
            message=f"omres-cli未找到: {cli_path}，请确认omres-cli已安装并在PATH中（安装由阶段零统一完成）",
            context_state=context.state
        )
    except subprocess.TimeoutExpired:
        raise StepExecutionError(
            step_name=step_name,
            message="omres-cli命令执行超时",
            context_state=context.state
        )

    output = proc.stdout.strip() if proc.stdout else ""

    if not output:
        stderr_msg = proc.stderr.strip() if proc.stderr else ""
        raise StepExecutionError(
            step_name=step_name,
            message=f"命令返回为空, stderr: {stderr_msg[:200]}{deadline_hint(stderr_msg)}",
            context_state=context.state
        )

    try:
        rpc_output = json.loads(output)
    except json.JSONDecodeError as e:
        raise StepExecutionError(
            step_name=step_name,
            message=f"响应JSON解析失败: {e}, 原始响应: {output[:200]}",
            context_state=context.state
        )

    # 检查JSON-RPC错误
    if "error" in rpc_output:
        error = rpc_output["error"]
        error_msg = error.get("message", "未知错误")
        if "data" in error:
            data = error["data"]
            if isinstance(data, dict):
                error_msg = data.get("msg", data.get("message", error_msg))
            elif isinstance(data, str):
                error_msg = data
        raise StepExecutionError(
            step_name=step_name,
            message=f"命令执行失败: {error_msg}{deadline_hint(error_msg)}",
            context_state=context.state
        )

    # 提取result
    result = rpc_output.get("result", {})

    # 检查业务状态码
    if isinstance(result, dict):
        code = result.get("code")
        if code is not None and code != 0:
            raise StepExecutionError(
                step_name=step_name,
                message=f"业务执行失败: {result.get('msg', result.get('message', '未知错误'))}",
                context_state=context.state
            )

        # 兼容旧格式：直接包含status字段
        if "status" in result and not result.get("status"):
            raise StepExecutionError(
                step_name=step_name,
                message=f"业务执行失败: {result.get('message', '未知错误')}",
                context_state=context.state
            )

    return result


# 旧名字的别名，避免各脚本内部调用点大改
_find_omres_cli = find_omres_cli
_run_cli = run_cli
