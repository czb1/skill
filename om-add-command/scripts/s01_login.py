"""
[Skill-01] 登录认证
通过 omres-cli auth login 命令实现登录，替代直接HTTP调用。
登录成功后omres-cli会自动将JSESSIONID持久化到 ~/.omres-cli/session.json，
后续omres-cli命令自动携带认证；同时将cookie注入context.session以兼容现有工作流。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING
from context import WorkflowContext, StepExecutionError

if TYPE_CHECKING:
    from typing import Optional


def _find_omres_cli() -> str:
    """
    查找omres-cli可执行文件路径

    Returns:
        str: omres-cli可执行文件的完整路径
    """
    # 1. 优先从PATH查找
    cli_path = shutil.which("omres-cli") or shutil.which("omres-cli.exe")
    if cli_path:
        return cli_path

    # 2. 从项目相对路径查找
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(project_root, "omres-cli", "omres-cli", "omres-cli.exe"),
        os.path.join(project_root, "omres-cli", "omres-cli"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    # 3. 从当前工作目录向上查找
    cwd = os.getcwd()
    for _ in range(3):
        candidate = os.path.join(cwd, "omres-cli", "omres-cli", "omres-cli.exe")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    return "omres-cli"  # fallback, rely on PATH


def _read_session_cookie() -> str:
    """
    从 ~/.omres-cli/session.json 读取omres-cli持久化的session cookie

    Returns:
        str: cookie字符串（如 "JSESSIONID=xxx"），未找到返回空字符串
    """
    home = os.path.expanduser("~")
    session_file = os.path.join(home, ".omres-cli", "session.json")
    if not os.path.isfile(session_file):
        return ""
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("cookie", "")
    except Exception:
        return ""


def _inject_cookie_to_context(context: WorkflowContext, cookie: str) -> None:
    """
    将cookie注入到context.session中，使现有工作流的HTTP调用也能携带认证

    Args:
        context: 工作流上下文
        cookie: cookie字符串（如 "JSESSIONID=xxx"）
    """
    if cookie:
        context.set_header("Cookie", cookie)
        # 设置环境变量，供后续omres-cli命令使用
        os.environ["OMRES_AUTH_COOKIE"] = cookie


def login(context: WorkflowContext, userName: str = None, passwd: str = None) -> dict:
    """
    用户登录认证（通过omres-cli）

    Args:
        context: 工作流上下文
        userName: 用户名 (可选，从context读取)
        passwd: 密码 (可选，从context读取)

    Returns:
        dict: 包含认证信息的响应结果

    Raises:
        StepExecutionError: 登录失败时抛出
    """
    userName = userName or context.userName
    passwd = passwd or context.passwd

    if not userName or not passwd:
        raise StepExecutionError(
            step_name="login",
            message="用户名和密码不能为空",
            context_state=context.state
        )

    cli_path = _find_omres_cli()

    # 构造omres-cli auth login命令
    body = json.dumps({"userName": userName, "passwd": passwd}, ensure_ascii=False)

    cmd = [
        cli_path, "auth", "login",
        "--body", body,
    ]

    if context.base_url:
        cmd.extend(["--server", context.base_url])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=30
        )
    except FileNotFoundError:
        raise StepExecutionError(
            step_name="login",
            message=f"omres-cli未找到: {cli_path}，请确认omres-cli已安装或在项目目录下",
            context_state=context.state
        )
    except subprocess.TimeoutExpired:
        raise StepExecutionError(
            step_name="login",
            message="omres-cli auth login 执行超时",
            context_state=context.state
        )

    # 解析stdout（JSON-RPC 2.0输出）
    output = result.stdout.strip() if result.stdout else ""

    if not output:
        stderr_msg = result.stderr.strip() if result.stderr else ""
        raise StepExecutionError(
            step_name="login",
            message=f"登录返回为空, stderr: {stderr_msg[:200]}",
            context_state=context.state
        )

    try:
        rpc_output = json.loads(output)
    except json.JSONDecodeError as e:
        raise StepExecutionError(
            step_name="login",
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
            step_name="login",
            message=f"登录失败: {error_msg}",
            context_state=context.state
        )

    # 提取result
    login_result = rpc_output.get("result", {})

    # 检查业务状态码
    if isinstance(login_result, dict):
        code = login_result.get("code")
        # code=0 表示成功，非0表示失败
        if code is not None and code != 0:
            raise StepExecutionError(
                step_name="login",
                message=f"登录失败: {login_result.get('msg', login_result.get('message', '未知错误'))}",
                context_state=context.state
            )

    # 登录成功后，omres-cli已将JSESSIONID持久化到 ~/.omres-cli/session.json
    # 读取持久化的cookie并注入context
    cookie = _read_session_cookie()
    if cookie:
        _inject_cookie_to_context(context, cookie)
    else:
        # 如果session文件未生成（可能是omres-cli版本不支持），打印警告
        print("  [WARNING] 未找到omres-cli持久化的session cookie，后续CLI命令可能需要手动指定--cookie")

    # 更新上下文状态
    context.set_state("login_result", login_result)
    context.set_state("userName", userName)
    context.set_state("is_logged_in", True)

    return login_result


if __name__ == "__main__":
    from context import create_context, get_windows_credential
    ctx = create_context(userName="z00847484", passwd=get_windows_credential("omtool.rnd.huawei.com", "z00847484"))
    result = login(ctx)
    print(f"登录结果: {result}")
    print(f"Cookie: {ctx.session.headers.get('Cookie', 'N/A')}")
