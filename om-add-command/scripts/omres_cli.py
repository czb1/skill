"""
[Common] omres-cli 调用封装

omres-cli 的安装与登录都由上游（E2E 阶段零 Step 1）统一完成：
二进制装在 `%USERPROFILE%\\omres-cli\\` 并通过 setx 写入用户 PATH，
登录态在 `%USERPROFILE%\\.omres-cli\\session.json`。

各 sXX 脚本原本各自复制了一份查找/调用逻辑，这里统一收敛：
- `find_omres_cli()`：定位可执行文件
- `run_cli()`：执行命令并解析 JSON-RPC 2.0 输出

注意：`setx` 写入的 PATH 对**已启动**的进程不生效，所以除了 PATH
还要回落到阶段零的安装目录，否则子进程可能找不到 omres-cli。
"""

import os
import json
import shutil
import subprocess

from context import WorkflowContext, StepExecutionError

# 阶段零的安装目录（相对用户主目录）
_INSTALL_DIR_NAME = "omres-cli"
_EXE_NAMES = ("omres-cli.exe", "omres-cli")


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


def _strip_cli_prefix(args: list) -> list:
    """兼容旧调用：调用方可能已把可执行文件路径拼在参数列表首位"""
    if args and isinstance(args[0], str):
        head = os.path.basename(args[0]).lower()
        if head in ("omres-cli", "omres-cli.exe"):
            return list(args[1:])
    return list(args)


def run_cli(context: WorkflowContext, args: list, step_name: str, timeout: int = 60) -> dict:
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

    if context.base_url:
        cmd.extend(["--server", context.base_url])

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
            message=f"命令返回为空, stderr: {stderr_msg[:200]}",
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
            message=f"命令执行失败: {error_msg}",
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
