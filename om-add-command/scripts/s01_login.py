"""
[Skill-01] 登录态校验（不再自行登录）

omres-cli 的登录已由上游（E2E 阶段零 Step 1）统一完成，登录态保存在
~/.omres-cli/session.json（Windows: %USERPROFILE%\\.omres-cli\\session.json），
对同一用户下的所有进程可见。

本模块只做**校验**：执行 `omres-cli auth status`，按退出码判断登录态，
并把已有的 cookie 注入 context.session，使工作流中的直接 HTTP 调用也能携带认证。

**本 skill 严禁执行 `omres-cli auth login`，严禁索要 / 读取 / 传递密码。**
校验不通过时直接返回失败，由主代理提示用户重新登录后重跑。
"""

import sys
import os
import subprocess
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli

if TYPE_CHECKING:
    from typing import Optional

# omres-cli auth status 的语义退出码
EXIT_AUTHENTICATED = 0
EXIT_UNAUTHENTICATED = 3
EXIT_OTHER_ERROR = 1

_RELOGIN_HINT = (
    "omres-cli 未认证或会话已过期。登录由用户在自己的终端统一完成，"
    "本 skill 不会也不允许代为登录。请主代理提示用户执行 "
    "`omres-cli auth login --username <域账号>`，用户确认完成后从本阶段重跑。"
)


def _read_session() -> dict:
    """
    读取 ~/.omres-cli/session.json 的内容

    Returns:
        dict: 会话内容，读取失败返回空dict
    """
    home = os.path.expanduser("~")
    session_file = os.path.join(home, ".omres-cli", "session.json")
    if not os.path.isfile(session_file):
        return {}
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_session_cookie() -> str:
    """
    从 ~/.omres-cli/session.json 读取omres-cli持久化的session cookie

    Returns:
        str: cookie字符串（如 "JSESSIONID=xxx"），未找到返回空字符串
    """
    return _read_session().get("cookie", "")


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


def _pick_username(source: dict) -> str:
    """从 auth status 结果或 session.json 中挑出用户名字段（字段名做兼容）"""
    if not isinstance(source, dict):
        return ""
    for key in ("username", "userName", "user", "account", "w3Num"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def get_authenticated_username(context: WorkflowContext = None) -> str:
    """
    获取当前登录态对应的用户名（不发起登录）

    先看 `omres-cli auth status` 的输出，再回落到 session.json。

    Args:
        context: 工作流上下文（可选，仅用于取 base_url）

    Returns:
        str: 用户名，取不到返回空字符串
    """
    status = _run_auth_status(context)
    username = _pick_username(status.get("result"))
    if username:
        return username
    return _pick_username(_read_session())


def _run_auth_status(context: WorkflowContext = None, online: bool = False) -> dict:
    """
    执行 `omres-cli auth status`

    Args:
        context: 工作流上下文（可选，仅用于取 base_url）
        online: True 时附加 --online，额外向后端发一次只读探活

    Returns:
        dict: {"returncode": int, "result": dict, "raw": str, "stderr": str}
              命令不可用时 returncode 为 None
    """
    cli_path = _find_omres_cli()
    cmd = [cli_path, "auth", "status"]
    if online:
        cmd.append("--online")
    base_url = getattr(context, "base_url", None) if context is not None else None
    if base_url:
        cmd.extend(["--server", base_url])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"returncode": None, "result": {}, "raw": "", "stderr": str(e)}

    raw = proc.stdout.strip() if proc.stdout else ""
    result = {}
    if raw:
        try:
            rpc_output = json.loads(raw)
            if isinstance(rpc_output, dict):
                result = rpc_output.get("result") or {}
        except json.JSONDecodeError:
            result = {}

    return {
        "returncode": proc.returncode,
        "result": result if isinstance(result, dict) else {},
        "raw": raw,
        "stderr": proc.stderr.strip() if proc.stderr else "",
    }


def ensure_authenticated(context: WorkflowContext, online: bool = False) -> dict:
    """
    校验 omres-cli 登录态（**不执行登录**）

    登录已由用户在 E2E 阶段零统一完成，这里只判断登录态是否可用，
    并把已有 cookie 注入 context，供工作流中的直接 HTTP 调用使用。

    Args:
        context: 工作流上下文
        online: True 时用 `auth status --online` 额外向后端探活

    Returns:
        dict: auth status 的 result 内容（含 source 等字段）

    Raises:
        StepExecutionError: 未认证、omres-cli 不可用或其它错误时抛出
    """
    status = _run_auth_status(context, online=online)
    returncode = status["returncode"]

    if returncode is None:
        raise StepExecutionError(
            step_name="ensure_authenticated",
            message=(
                f"omres-cli 不可用（{status['stderr'][:200]}）。"
                "请确认 omres-cli 已安装并在 PATH 中（安装与登录由阶段零统一完成）。"
            ),
            context_state=context.state
        )

    if returncode == EXIT_UNAUTHENTICATED:
        raise StepExecutionError(
            step_name="ensure_authenticated",
            message=_RELOGIN_HINT,
            context_state=context.state
        )

    if returncode != EXIT_AUTHENTICATED:
        # 退出码 1（或其它）：后端不可达等，不是认证问题，不要引导重新登录
        detail = status["stderr"] or status["raw"]
        raise StepExecutionError(
            step_name="ensure_authenticated",
            message=(
                f"omres-cli auth status 返回退出码 {returncode}（非认证问题，"
                f"通常是后端不可达或参数错误）: {detail[:200]}"
            ),
            context_state=context.state
        )

    # 已认证：复用现成的登录态
    cookie = _read_session_cookie()
    if cookie:
        _inject_cookie_to_context(context, cookie)
    else:
        # session.json 里没有 cookie（例如凭证来自环境变量），omres-cli 自身仍能鉴权，
        # 只有工作流中少量直接 HTTP 调用会受影响
        print("  [WARNING] 未从 session.json 读到 cookie，直接 HTTP 调用可能缺少认证；omres-cli 命令不受影响")

    auth_result = status["result"]
    username = _pick_username(auth_result) or _pick_username(_read_session()) or context.userName

    context.set_state("auth_status", auth_result)
    context.set_state("userName", username)
    context.set_state("is_logged_in", True)

    return auth_result


# 兼容旧调用方：原来的 login(context) 现在等价于登录态校验，不会发起登录
def login(context: WorkflowContext, userName: str = None, passwd: str = None) -> dict:
    """
    已废弃：登录统一由用户在阶段零完成，此处仅校验登录态。

    保留该函数名是为了兼容旧调用方；userName / passwd 参数一律忽略。
    """
    if passwd:
        print("  [WARNING] login() 的 passwd 参数已废弃并被忽略：登录已在阶段零统一完成")
    return ensure_authenticated(context)


if __name__ == "__main__":
    from context import create_context
    ctx = create_context(taskName="TEST")
    result = ensure_authenticated(ctx)
    print(f"登录态校验结果: {result}")
    print(f"当前用户: {get_authenticated_username(ctx)}")
    print(f"Cookie: {ctx.session.headers.get('Cookie', 'N/A')}")
