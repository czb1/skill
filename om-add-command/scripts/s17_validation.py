"""
[Skill-17/18/19] 校验与导出
通过 omres-cli validate do / result、errorcode shield、task export-struct / export-result / download 命令实现，替代直接HTTP调用。
"""

import sys
import os
import subprocess
import json
import shutil
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import TYPE_CHECKING, List, Dict, Any
from context import WorkflowContext, StepExecutionError
from omres_cli import find_omres_cli as _find_omres_cli, run_cli as _run_cli, DEFAULT_TIMEOUT as _DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from typing import Optional

# CodeHub 相关默认值。鉴权由用户在阶段零统一完成的 `codehub-cli auth login` 提供，
# 本脚本不持有、不传递任何 token（codehub-cli 自身的 -t/--token 与 CODEHUB_TOKEN 也不使用）。
# 留空表示「从代码仓的 git remote 自动推断」，推断不出来再用 DEFAULT_CODEHUB_PROJECT。
CODEHUB_PROJECT = os.environ.get("CODEHUB_PROJECT", "")
CODEHUB_HOST = os.environ.get("CODEHUB_HOST", "")
DEFAULT_CODEHUB_PROJECT = "UPCF/ComConfig"


def shield_errors(context: WorkflowContext, error_codes: str = "2017") -> dict:
    """
    屏蔽校验错误 — 通过omres-cli errorcode shield

    Args:
        context: 工作流上下文
        error_codes: 要屏蔽的错误码（逗号分隔），默认屏蔽2017（默认值冲突）

    Returns:
        dict: 屏蔽结果
    """
    task_id = context.get_required_state("taskId")
    project_id = context.get_state("projectId") or task_id

    body = json.dumps({
        "projectId": project_id,
        "shieldErrorCodes": error_codes
    }, ensure_ascii=False)

    print(f"  [DEBUG] shield_errors: projectId={project_id}, error_codes={error_codes}")

    result = _run_cli(
        context,
        ["errorcode", "shield", "--body", body],
        step_name="shield_errors",
        timeout=_DEFAULT_TIMEOUT
    )

    return result


def start_validation(context: WorkflowContext, isCommitAndPush: int = 0, auto_shield: bool = True) -> dict:
    """
    启动校验 — 通过omres-cli validate do

    Args:
        context: 工作流上下文
        isCommitAndPush: 是否提交 (0=否, 1=是)
        auto_shield: 校验失败时是否自动屏蔽错误码并重试

    Returns:
        dict: 校验结果
    """
    task_id = context.get_required_state("taskId")
    project_id = context.get_state("projectId") or task_id

    body = json.dumps({
        "projectId": project_id,
        "isCommitAndPush": isCommitAndPush
    }, ensure_ascii=False)

    print(f"  [DEBUG] start_validation: projectId={project_id}")

    result = _run_cli(
        context,
        ["validate", "do", "--body", body],
        step_name="start_validation",
        timeout=_DEFAULT_TIMEOUT
    )

    # 校验可能返回status=false但data=true的情况(如"未通过")
    is_passed = result.get("data") == True
    context.set_state("validation_passed", is_passed)
    context.set_state("validation_result", result)

    # 如果自动屏蔽且校验失败，执行屏蔽并重试
    if not is_passed and auto_shield:
        print(f"  ⚠ 校验未通过，执行屏蔽...")
        shield_result = shield_errors(context, error_codes="2017")
        if shield_result.get("status"):
            print(f"  ✓ 屏蔽成功，3秒后重新校验...")
            time.sleep(3)

            # 重试机制：最多重试3次
            max_retries = 3
            for retry_count in range(max_retries):
                result = _run_cli(
                    context,
                    ["validate", "do", "--body", body],
                    step_name="start_validation_retry",
                    timeout=_DEFAULT_TIMEOUT
                )
                is_passed = result.get("data") == True
                context.set_state("validation_passed", is_passed)
                context.set_state("validation_result", result)
                print(f"  [DEBUG] 重试校验结果(第{retry_count + 1}次): {result}")
                if is_passed:
                    print(f"  ✓ 校验通过!")
                    break
                elif retry_count < max_retries - 1:
                    print(f"  ⚠ 校验仍未通过，{3}秒后进行第{retry_count + 2}次重试...")
                    time.sleep(3)
        else:
            print(f"  ⚠ 屏蔽失败: {shield_result.get('message', '未知错误')}")

    return result


def query_validation_result(context: WorkflowContext, level: str = "ERROR") -> dict:
    """
    查询校验结果详情 — 通过omres-cli validate result

    Args:
        context: 工作流上下文
        level: 错误级别 (ERROR/WARNING/INFO)

    Returns:
        dict: 包含错误列表的响应
    """
    task_id = context.get_required_state("taskId")
    project_id = context.get_state("projectId") or task_id

    body = json.dumps({
        "projectId": project_id,
        "level": level
    }, ensure_ascii=False)

    print(f"  [DEBUG] query_validation_result: projectId={project_id}, level={level}")

    result = _run_cli(
        context,
        ["validate", "result", "--body", body],
        step_name="query_validation_result",
        timeout=_DEFAULT_TIMEOUT
    )

    # 提取错误列表
    errors = result.get("data", [])
    context.set_state("validation_errors", errors)
    context.set_state("validation_error_count", len(errors))

    return result


def export_model(context: WorkflowContext, version: str = "1", max_wait: int = 300) -> dict:
    """
    导出模型（带轮询等待） — 通过omres-cli task export-struct / export-result / download

    Args:
        context: 工作流上下文
        version: 版本号
        max_wait: 最大等待时间（秒）

    Returns:
        dict: 包含导出结果的字典，以及zip文件路径保存在context中
    """
    task_id = context.get_required_state("taskId")
    task_name = context.get_state("taskName") or context.taskName

    downloads_dir = "D:\\download"
    zip_path = os.path.join(downloads_dir, f"om_struct_{task_name}.zip")

    # 1. trigger export — omres-cli task export-struct
    print(f"  [DEBUG] export_model: taskId={task_id}, taskName={task_name}")

    result = _run_cli(
        context,
        ["task", "export-struct", str(task_id), task_name, version],
        step_name="export_model",
        timeout=_DEFAULT_TIMEOUT
    )

    print(f"  [DEBUG] 触发导出成功，开始轮询等待...")

    # 2. poll export status (3s interval) — omres-cli task export-result
    start_time = time.time()

    while time.time() - start_time < max_wait:
        try:
            status_result = _run_cli(
                context,
                ["task", "export-result", str(task_id), task_name],
                step_name="export_model_poll",
                timeout=_DEFAULT_TIMEOUT
            )
        except StepExecutionError:
            time.sleep(3)
            continue

        print(f"  [DEBUG] 导出状态: {status_result}")

        if status_result.get('message') == '压缩包准备就绪':
            print(f"  ✓ 压缩包准备就绪，开始下载...")

            # 3. 下载压缩包 — omres-cli task download
            try:
                download_result = _run_cli(
                    context,
                    ["task", "download", str(task_id), task_name],
                    step_name="export_model_download",
                    timeout=_DEFAULT_TIMEOUT
                )
            except StepExecutionError as e:
                raise StepExecutionError(
                    step_name="export_model",
                    message=f"下载压缩包失败: {e.message}",
                    context_state=context.state
                )

            # omres-cli download会将二进制文件保存到临时文件，result中包含file路径
            download_file = download_result.get("file")
            if download_file and os.path.exists(download_file):
                os.makedirs(downloads_dir, exist_ok=True)
                shutil.move(download_file, zip_path)
                print(f"  ✓ 压缩包下载成功: {zip_path}")
            else:
                print(f"  ⚠ 下载结果中未找到文件路径: {download_result}")

            break
        elif isinstance(status_result, dict) and status_result.get('status') == False and '失败' in str(status_result.get('message', '')):
            raise StepExecutionError(
                step_name="export_model",
                message=f"导出失败: {status_result.get('message', '未知错误')}",
                context_state=context.state
            )

        time.sleep(3)
    else:
        raise StepExecutionError(
            step_name="export_model",
            message=f"导出超时（等待{max_wait}秒）",
            context_state=context.state
        )

    context.set_state("export_result", result)
    context.set_state("model_exported", True)
    context.set_state("exported_zip_path", zip_path)
    context.set_state("download_dir", downloads_dir)

    return result


def get_export_status(context: WorkflowContext) -> dict:
    """
    获取导出状态 — 通过omres-cli task export-result

    Args:
        context: 工作流上下文

    Returns:
        dict: 导出状态
    """
    task_id = context.get_required_state("taskId")
    task_name = context.get_state("taskName") or context.taskName

    print(f"  [DEBUG] get_export_status: taskId={task_id}, taskName={task_name}")

    result = _run_cli(
        context,
        ["task", "export-result", str(task_id), task_name],
        step_name="get_export_status",
        timeout=_DEFAULT_TIMEOUT
    )

    context.set_state("export_status", result)

    return result


def _find_codehub_cli() -> str:
    """查找codehub-cli可执行文件路径（安装与登录由阶段零统一完成）"""
    override = os.environ.get("CODEHUB_CLI")
    if override and os.path.isfile(override):
        return override
    return (shutil.which("codehub-cli") or shutil.which("codehub-cli.cmd")
            or shutil.which("codehub-cli.exe") or "codehub-cli")


def detect_codehub_repo_info(repo_path: str) -> dict:
    """
    从代码仓的 git remote 推断 CodeHub Host 与项目路径

    例：https://szv-y.codehub.huawei.com/UPCF/ComConfig.git
        -> {"host": "https://szv-y.codehub.huawei.com", "project": "UPCF/ComConfig"}

    Args:
        repo_path: 本地代码仓路径

    Returns:
        dict: {"host": str|None, "project": str|None}
    """
    empty = {"host": None, "project": None}
    if not repo_path or not os.path.isdir(repo_path):
        return empty

    try:
        proc = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, encoding='utf-8', timeout=_DEFAULT_TIMEOUT, cwd=repo_path
        )
    except (OSError, subprocess.SubprocessError):
        return empty

    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        return empty

    if url.startswith("git@"):
        # git@host:group/project.git
        host_part, _, path_part = url[4:].partition(":")
        host = f"https://{host_part}" if host_part else None
    else:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.netloc:
            return empty
        host = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        path_part = parsed.path

    project = path_part.strip("/")
    if project.endswith(".git"):
        project = project[:-4]

    return {"host": host, "project": project or None}


def extract_zip_package(zip_path: str, extract_to: str, service_name: str = None) -> str:
    """
    解压压缩包到指定目录

    Args:
        zip_path: 压缩包路径
        extract_to: 解压目标目录
        service_name: 服务名称，用于定位解压后的服务目录

    Returns:
        str: 解压后的服务目录路径
    """
    if not os.path.exists(zip_path):
        raise StepExecutionError(
            step_name="extract_zip_package",
            message=f"压缩包不存在: {zip_path}"
        )

    with zipfile.ZipFile(zip_path, 'r') as z:
        names = z.namelist()
        root_dirs = set()
        for n in names:
            parts = n.split('/')
            if parts:
                root_dirs.add(parts[0])

        if len(root_dirs) == 1:
            actual_root = list(root_dirs)[0]
        else:
            actual_root = os.path.splitext(os.path.basename(zip_path))[0]

    extracted_dir = os.path.join(extract_to, actual_root)

    if os.path.exists(extracted_dir):
        shutil.rmtree(extracted_dir)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

    target_service = service_name if service_name else "PcfPolicyEngineService"
    service_path = os.path.join(extracted_dir, target_service)

    if not os.path.exists(service_path):
        backup_service_path = os.path.join(extracted_dir, "resource_backup", target_service)
        if os.path.exists(backup_service_path):
            service_path = backup_service_path
        else:
            raise StepExecutionError(
                step_name="extract_zip_package",
                message=f"解压后{service_name}目录不存在: {service_path} 或 {backup_service_path}"
            )

    print(f"  ✓ 压缩包解压成功: {zip_path} -> {extracted_dir}")
    return extracted_dir


def sync_service_to_repo(extracted_dir: str, repo_path: str, service_name: str = None, service_module: str = None) -> List[str]:
    """
    同步服务目录到代码仓库

    Args:
        extracted_dir: 解压后的根目录
        repo_path: 代码仓库路径
        service_name: 服务名称
        service_module: 服务模块名（如PCFDLB）

    Returns:
        List[str]: 变更的文件列表
    """
    target_service = service_name if service_name else "PcfPolicyEngineService"
    service_source = os.path.join(extracted_dir, target_service)
    if not os.path.exists(service_source):
        service_source = os.path.join(extracted_dir, "resource_backup", target_service)
    service_target = os.path.join(repo_path, target_service)

    if not os.path.exists(service_source):
        raise StepExecutionError(
            step_name="sync_service_to_repo",
            message=f"源目录不存在: {service_source}"
        )

    changed_files = []

    for root, dirs, files in os.walk(service_source):
        for file in files:
            source_file = os.path.join(root, file)
            relative_path = os.path.relpath(source_file, service_source)
            target_file = os.path.join(service_target, relative_path)
            target_dir = os.path.dirname(target_file)

            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            if not os.path.exists(target_file) or os.path.getmtime(source_file) > os.path.getmtime(target_file):
                shutil.copy2(source_file, target_file)
                changed_files.append(f"{target_service}/{relative_path}")

    print(f"  ✓ 同步文件到仓库，共 {len(changed_files)} 个文件变更")

    standardize_infocode_style(service_target, service_module)

    return changed_files


def standardize_infocode_style(repo_path: str, service_module: str = None) -> None:
    """
    标准化错误码风格，将ZL_XXXXX格式改为ERR_XXXXX格式，与现有代码风格一致

    Args:
        repo_path: 服务目录路径（如 D:/git/26.0/ComConfig/om/PcfDiamLoadBalanceService）
        service_module: 服务模块名（如PCFDLB），如果为None则从repo_path推导
    """
    print(f"  [DEBUG] standardize_infocode_style called with repo_path: {repo_path}, service_module: {service_module}")

    if service_module is None:
        service_module = os.path.basename(repo_path).replace("Pcf", "").replace("Service", "")
    print(f"  [DEBUG] using service_module: {service_module}")

    infocode_dir = os.path.join(repo_path, "om", "infocode")
    if not os.path.exists(infocode_dir):
        print(f"  [DEBUG] infocode_dir not exists: {infocode_dir}")
        return

    for root, dirs, files in os.walk(infocode_dir):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, repo_path)

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                original_content = content

                if file.endswith('.go'):
                    import re
                    pattern = r'({service}_ZL_(\d+))\s+int\s*=\s*(\d+)'.format(service=service_module)
                    replacement = lambda m: f'{service_module}_ERR_{m.group(2)} int = {m.group(2)}'
                    content = re.sub(pattern, replacement, content)

                elif file.endswith('.h'):
                    import re
                    pattern = r'#define\s+({service}_ZL_(\d+))\s+(\d+)'.format(service=service_module)
                    replacement = lambda m: f'#define {service_module}_ERR_{m.group(2)} {m.group(2)}'
                    content = re.sub(pattern, replacement, content)

                elif file.endswith('.lua'):
                    import re
                    pattern = r'INFOCODE_IDZL_(\d+)\s*=\s*(\d+)'
                    replacement = r'INFOCODE_IDERR_\1 = \1'
                    content = re.sub(pattern, replacement, content)

                elif file.endswith('.xml'):
                    import re
                    def fix_xml_entry(m):
                        full_match = m.group(0)
                        code_num = m.group(1)
                        code_name = m.group(2)
                        if code_name.startswith('ZL_'):
                            new_name = 'ERR_' + code_name[3:]
                            full_match = full_match.replace(f'<infoCodeName>{code_name}</infoCodeName>', f'<infoCodeName>{new_name}</infoCodeName>')
                            full_match = full_match.replace(f'<infoCodeNum>{code_num}</infoCodeNum>', f'<infoCodeNum>{code_name[3:]}</infoCodeNum>')
                        return full_match

                    content = re.sub(r'<infoCodeNum>(\d+)</infoCodeNum>\s*<infoCodeName>(ZL_\d+)</infoCodeName>', fix_xml_entry, content)

                if content != original_content:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f"  ✓ 标准化错误码风格: {relative_path}")

            except Exception as e:
                print(f"  ⚠ 处理文件失败 {relative_path}: {e}")


def git_commit_and_push(repo_path: str, branch_name: str, commit_message: str, changed_files: List[str] = None) -> str:
    """
    Git提交并推送分支

    Args:
        repo_path: 仓库路径
        branch_name: 分支名称
        commit_message: 提交信息
        changed_files: 已变更的文件列表，用于精确git add

    Returns:
        str: 推送的分支名
    """
    try:
        current_branch_result = subprocess.run(
            ['git', 'branch', '--show-current'],
            capture_output=True,
            text=True,
            cwd=repo_path
        )
        current_branch = current_branch_result.stdout.strip()

        if changed_files:
            for f in changed_files:
                subprocess.run(['git', 'add', f], check=True, cwd=repo_path)
        else:
            subprocess.run(['git', 'add', '-A'], check=True, cwd=repo_path)

        result = subprocess.run(
            ['git', 'commit', '-m', commit_message],
            capture_output=True,
            text=True,
            cwd=repo_path
        )

        if result.returncode != 0:
            if 'nothing to commit' in result.stdout:
                print("  ✓ 没有文件变更需要提交")
                return current_branch
            raise StepExecutionError(
                step_name="git_commit_and_push",
                message=f"Git提交失败: {result.stdout}"
            )

        subprocess.run(['git', 'push', '-u', 'origin', current_branch], check=True, cwd=repo_path)
        print(f"  ✓ Git提交并推送成功: {current_branch}")

        return current_branch

    except subprocess.CalledProcessError as e:
        raise StepExecutionError(
            step_name="git_commit_and_push",
            message=f"Git命令执行失败: {e}"
        )


def _parse_mr_output(output: str) -> dict:
    """
    从 codehub-cli mr create 的输出中提取MR链接与IID

    先按JSON解析（兼容外层包一层 data/result 的情况），失败再用正则兜底。

    Args:
        output: codehub-cli 的标准输出

    Returns:
        dict: {'mr_url': str|None, 'mr_iid': int|str|None}
    """
    import re

    mr_url = None
    mr_iid = None

    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, dict):
        node = parsed
        for key in ("result", "data", "mr", "mergeRequest", "merge_request"):
            inner = node.get(key)
            if isinstance(inner, dict):
                node = inner
        for key in ("web_url", "webUrl", "url", "mr_url", "mrUrl"):
            if isinstance(node.get(key), str):
                mr_url = node[key]
                break
        for key in ("iid", "mr_iid", "mrIid", "id"):
            if node.get(key) not in (None, ""):
                mr_iid = node[key]
                break

    if not mr_url:
        match = re.search(r'https?://\S+?/merge_requests/(\d+)', output or "")
        if match:
            mr_url = match.group(0).rstrip('",)')
            mr_iid = mr_iid or match.group(1)

    if mr_iid is None and mr_url:
        match = re.search(r'/merge_requests/(\d+)', mr_url)
        if match:
            mr_iid = match.group(1)

    return {'mr_url': mr_url, 'mr_iid': mr_iid}


def create_mr_on_codehub(source_branch: str, target_branch: str = "master",
                         title: str = None, description: str = None,
                         project: str = None, host: str = None,
                         repo_path: str = None, project_id: str = None) -> dict:
    """
    通过 codehub-cli 在CodeHub上创建MR

    鉴权复用 `codehub-cli auth login` 的登录态（由用户在阶段零统一完成），
    本函数不读取、不传递任何 token（不使用 -t/--token 与 CODEHUB_TOKEN）；
    未认证时直接失败返回，由主代理提示用户重新登录。

    项目与Host的取值顺序：显式参数 → CODEHUB_PROJECT / CODEHUB_HOST 环境变量
    → 从 repo_path 的 git remote 推断 → 项目兜底 DEFAULT_CODEHUB_PROJECT、
    Host 交给 codehub-cli 自身的配置。命令在 repo_path 下执行，
    codehub-cli 自带的仓库探测也能生效。

    Args:
        source_branch: 源分支
        target_branch: 目标分支
        title: MR标题
        description: MR描述
        project: 项目路径（如 UPCF/ComConfig）
        host: CodeHub地址（如 https://szv-y.codehub.huawei.com）
        repo_path: 本地代码仓路径，用于推断项目/Host并作为命令的工作目录
        project_id: 已废弃，等价于project（兼容旧调用，支持URL编码形式）

    Returns:
        dict: 包含mr_url和mr_iid的字典

    Raises:
        StepExecutionError: codehub-cli 缺失、未认证或创建失败时抛出
    """
    from urllib.parse import unquote

    detected = detect_codehub_repo_info(repo_path)
    project = (project or (unquote(project_id) if project_id else None)
               or CODEHUB_PROJECT or detected["project"] or DEFAULT_CODEHUB_PROJECT)
    host = host or CODEHUB_HOST or detected["host"]

    if title is None:
        title = "[WIP] feat: 添加MML命令"

    cli_path = _find_codehub_cli()
    cmd = [cli_path, "mr", "create", "-p", project]
    if host:
        cmd.extend(["-H", host])
    cmd.extend([
        "--source-branch", source_branch,
        "--target-branch", target_branch,
        "--title", title,
        "--description", description or "",
        "-f", "json",
    ])

    print(f"  [DEBUG] codehub-cli mr create: project={project}, host={host or '(cli默认)'}, "
          f"{source_branch} -> {target_branch}")

    workdir = repo_path if repo_path and os.path.isdir(repo_path) else None

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                              timeout=_DEFAULT_TIMEOUT, cwd=workdir)
    except FileNotFoundError:
        raise StepExecutionError(
            step_name="create_mr_on_codehub",
            message=f"codehub-cli未找到: {cli_path}。codehub-cli 的安装与登录由阶段零统一完成，请确认其已安装并在PATH中"
        )
    except subprocess.TimeoutExpired:
        raise StepExecutionError(
            step_name="create_mr_on_codehub",
            message="codehub-cli mr create 执行超时"
        )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        detail = stderr or stdout
        if any(kw in detail.lower() for kw in ("unauthorized", "not logged in", "401", "认证", "登录")):
            raise StepExecutionError(
                step_name="create_mr_on_codehub",
                message=(
                    f"codehub-cli 未认证: {detail[:300]}。登录由用户在自己的终端统一完成，"
                    "本 skill 不会代为登录，请回报主代理提示用户执行 `codehub-cli auth login` 后重跑本阶段"
                )
            )
        raise StepExecutionError(
            step_name="create_mr_on_codehub",
            message=f"MR创建失败(exit={proc.returncode}): {detail[:300]}"
        )

    mr_info = _parse_mr_output(stdout or stderr)

    if not mr_info['mr_url']:
        raise StepExecutionError(
            step_name="create_mr_on_codehub",
            message=f"MR创建命令已成功执行，但未能从输出中解析出MR链接: {(stdout or stderr)[:300]}"
        )

    print(f"  ✓ MR创建成功: {mr_info['mr_url']}")
    return mr_info


def read_command_ids_from_moc(repo_path: str, service_name: str, moc_name: str) -> dict:
    """
    从导出的MOC XML文件中读取ObjectID和Command ID

    Args:
        repo_path: 仓库路径
        service_name: 服务名称
        moc_name: MOC名称

    Returns:
        dict: 包含object_id, set_command_id, lst_command_id
    """
    import xml.etree.ElementTree as ET

    service_module_map = {
        "PcfDiamLoadBalanceService": "PCFDLB",
        "PcfPolicyEngineService": "PCFPES",
        "PcfHttpLoadBalanceService": "PCFHLB",
        "PcfDataReportService": "PCFDRS",
        "PcfNotifyLogFileService": "PCFNLF",
        "PcfNotificationTaskService": "PCFNTS",
        "PcfOfflineTaskCtrlService": "PCFOTCS",
        "PcfNrfClientService": "PCFNCS",
        "PcfPolicyManagementService": "PCFPMS",
        "PcfSgmService": "PCFSGM",
        "PcfAppCtrlService": "PCFAPPCTRL",
    }
    module_name = service_module_map.get(service_name, service_name.replace("Service", "").upper()[:6])

    moc_xml_path = os.path.join(repo_path, service_name, "om", "cfg", "microservice", "input", "modules", module_name, f"{moc_name}.xml")

    if not os.path.exists(moc_xml_path):
        print(f"  [WARNING] MOC文件不存在: {moc_xml_path}")
        return {"object_id": None, "set_command_id": None, "lst_command_id": None}

    try:
        tree = ET.parse(moc_xml_path)
        root = tree.getroot()

        object_id = None
        set_command_id = None
        lst_command_id = None

        if root.tag == "NewDataSet":
            for child in root:
                if child.tag == "MOC":
                    obj_id_elem = child.find("ObjectID")
                    if obj_id_elem is not None:
                        object_id = obj_id_elem.text

                elif child.tag == "Command":
                    name_elem = child.find("Name")
                    id_elem = child.find("ID")
                    if name_elem is not None and id_elem is not None:
                        cmd_name = name_elem.text
                        cmd_id = id_elem.text
                        if cmd_name and cmd_id:
                            if "SET" in cmd_name:
                                set_command_id = cmd_id
                            elif "LST" in cmd_name:
                                lst_command_id = cmd_id

        return {
            "object_id": object_id,
            "set_command_id": set_command_id,
            "lst_command_id": lst_command_id
        }
    except Exception as e:
        print(f"  [ERROR] 解析MOC XML失败: {e}")
        return {"object_id": None, "set_command_id": None, "lst_command_id": None}


def update_navitree_file(repo_path: str, service_name: str, moc_name: str, set_command_id: str = None, lst_command_id: str = None, moc_desc_ch: str = None, moc_desc_en: str = None) -> bool:
    """
    更新导航树文件，添加新的MML命令节点

    Args:
        repo_path: 仓库路径
        service_name: 服务名称（如PcfDiamLoadBalanceService）
        moc_name: MOC名称（如DLBSCTPBUFFCFG）
        set_command_id: SET命令ID（Macrocode）
        lst_command_id: LST命令ID（Macrocode）
        moc_desc_ch: 中文描述
        moc_desc_en: 英文描述

    Returns:
        bool: 是否成功
    """
    navitree_path = os.path.join(repo_path, service_name, "om", "navitree", f"NaviTree_MML_{service_name}.xml")

    if not os.path.exists(navitree_path):
        print(f"  [WARNING] 导航树文件不存在: {navitree_path}")
        return False

    with open(navitree_path, 'r', encoding='utf-8') as f:
        content = f.read()

    desc_cn = moc_desc_ch or moc_name
    desc_en = moc_desc_en or moc_name

    set_id = set_command_id or "805573"
    lst_id = lst_command_id or "805574"

    new_node = f'''      <mml Name="SET {moc_name}" Type="MML_NODE" Command="SET {moc_name}" Moc="{moc_name}" Macrocode="{set_id}" CommandDescCN="设置{moc_name}配置" CommandDescEN="Set {moc_name} Configuration" MustBeConfirmCN="" MustBeConfirmEN="" PublicMode="inner" />
      <mml Name="LST {moc_name}" Type="MML_NODE" Command="LST {moc_name}" Moc="{moc_name}" Macrocode="{lst_id}" CommandDescCN="查询{moc_name}配置" CommandDescEN="List {moc_name} Configuration" MustBeConfirmCN="" MustBeConfirmEN="" PublicMode="inner" />
    </node>
  </node>
</NaviTree>'''

    old_tail = '''    </node>
  </node>
</NaviTree>'''

    if f'<mml Name="SET {moc_name}"' in content:
        print(f"  [INFO] 导航树已包含{moc_name}，跳过")
        return True

    new_content = content.replace(old_tail, new_node)

    if new_content == content:
        print(f"  [WARNING] 导航树末尾模式不匹配，可能格式已变化")
        return False

    with open(navitree_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"  ✓ 导航树已更新: {navitree_path}")
    return True


def update_whitelist_file(repo_path: str, service_name: str, moc_name: str, object_id: str = None) -> bool:
    """
    更新白名单文件，添加新的class条目

    Args:
        repo_path: 仓库路径
        service_name: 服务名称（如PcfDiamLoadBalanceService）
        moc_name: MOC名称（如DLBSCTPBUFFCFG）
        object_id: ObjectID（mocId），用于白名单的id字段

    Returns:
        bool: 是否成功
    """
    whitelist_path = os.path.join(repo_path, "commoncfg", "id_white_list.xml")

    if not os.path.exists(whitelist_path):
        print(f"  [WARNING] 白名单文件不存在: {whitelist_path}")
        return False

    with open(whitelist_path, 'r', encoding='utf-8') as f:
        content = f.read()

    service_module_map = {
        "PcfDiamLoadBalanceService": "PCFDLB",
        "PcfPolicyEngineService": "PCFPES",
        "PcfHttpLoadBalanceService": "PCFHLB",
    }
    module_name = service_module_map.get(service_name, service_name.replace("Service", "").upper()[:6])

    search_pattern = f'''<module name="{module_name}"'''
    if search_pattern not in content:
        print(f"  [WARNING] 白名单中未找到模块: {module_name}")
        return False

    if f'class name="{moc_name}"' in content:
        print(f"  [INFO] 白名单已包含{moc_name}，跳过")
        return True

    cid = object_id or "330"

    old_entry = f'''			</module>
			</modules>
		</service>
	</services>'''

    new_class_line = f'''				<class name="{moc_name}" id="{cid}" class_type="Config" cmc_name="{moc_name}" cmc_mml_name="SET {moc_name};LST {moc_name}"/>
'''

    insert_pos = content.rfind(old_entry)
    if insert_pos == -1:
        print(f"  [WARNING] 白名单末尾模式不匹配")
        return False

    new_content = content[:insert_pos] + new_class_line + old_entry

    with open(whitelist_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"  ✓ 白名单已更新: {whitelist_path}")
    return True


def export_sync_and_create_mr(
    context: WorkflowContext,
    repo_path: str = None,
    target_branch: str = None,
    moc_name: str = None,
    service_name: str = None
) -> dict:
    """
    解压已导出的zip包、同步文件、提交Git并创建MR的完整流程

    Args:
        context: 工作流上下文（需包含exported_zip_path）
        repo_path: 代码仓库路径（默认当前目录）
        target_branch: 目标分支名（默认基于moc_name生成）
        moc_name: MOC名称（用于生成分支名）
        service_name: 服务名称（用于同步对应的服务文件夹）

    Returns:
        dict: 包含mr_url、branch等信息的字典
    """
    if repo_path is None:
        repo_path = context.file_path or "D:\\git\\26.0\\ComConfig\\om"

    task_name = context.get_state("taskName") or context.taskName

    if target_branch is None:
        result = subprocess.run(
            ['git', 'branch', '--show-current'],
            capture_output=True,
            text=True,
            cwd=repo_path
        )
        target_branch = result.stdout.strip() or "master"

    print()
    print("[阶段11] 解压、同步文件、更新导航树/白名单、提交Git并创建MR")
    print("-" * 40)

    # 获取export_model已下载的zip路径
    task_name = context.get_state("taskName") or context.taskName
    zip_path = f"D:\\download\\om_struct_{task_name}.zip"

    if not os.path.exists(zip_path):
        raise StepExecutionError(
            step_name="export_sync_and_create_mr",
            message=f"压缩包不存在: {zip_path}"
        )

    extract_to = "D:\\download"

    print("  [1/5] 解压压缩包...")
    extracted_dir = extract_zip_package(zip_path, extract_to, service_name)

    print("  [2/5] 同步文件到仓库...")
    changed_files = sync_service_to_repo(extracted_dir, repo_path, service_name, context.moduleName)

    if not changed_files:
        print("  ⚠ 没有文件需要同步")
        return {'mr_url': None, 'branch': target_branch, 'changed_files': []}

    print("  [3/5] 更新导航树和白名单...")
    moc_id = context.get_state("mocId")
    moc_desc_ch = getattr(context, 'moc_desc_ch', None) or context.get_state("moc_desc_ch")
    moc_desc_en = getattr(context, 'moc_desc_en', None) or context.get_state("moc_desc_en")

    ids_info = read_command_ids_from_moc(repo_path, service_name, moc_name)
    object_id = ids_info.get("object_id") or str(moc_id) if moc_id else None
    set_command_id = ids_info.get("set_command_id")
    lst_command_id = ids_info.get("lst_command_id")

    navitree_updated = update_navitree_file(repo_path, service_name, moc_name, set_command_id, lst_command_id, moc_desc_ch, moc_desc_en)
    whitelist_updated = update_whitelist_file(repo_path, service_name, moc_name, object_id)

    if navitree_updated:
        changed_files.append(f"{service_name}/om/navitree/NaviTree_MML_{service_name}.xml")
    if whitelist_updated:
        changed_files.append("commoncfg/id_white_list.xml")

    print("  [4/5] Git提交并推送...")
    commit_message = f"feat: 添加{moc_name or 'MML命令'}相关文件"
    git_commit_and_push(repo_path, target_branch, commit_message, changed_files)

    print("  [5/5] 创建MR...")
    description = f"""## Summary
- 添加{moc_name or 'MML命令'}相关文件
- 变更文件数: {len(changed_files)}

## Changed Files
""" + "\n".join([f"- {f}" for f in changed_files[:20]])
    if len(changed_files) > 20:
        description += f"\n- ... 还有 {len(changed_files) - 20} 个文件"

    mr_result = create_mr_on_codehub(
        target_branch,
        "master",
        f"[WIP] feat: 添加{moc_name or 'MML命令'}",
        description,
        repo_path=repo_path
    )

    print("  清理临时文件...")
    if os.path.exists(extracted_dir):
        shutil.rmtree(extracted_dir)
    print("  ✓ 清理完成")

    print()
    print("=" * 40)
    print(f"✓ 完成！MR: {mr_result['mr_url']}")
    print("=" * 40)

    return {
        'mr_url': mr_result['mr_url'],
        'mr_iid': mr_result['mr_iid'],
        'branch': target_branch,
        'changed_files': changed_files
    }
