"""
[Main Workflow] HLBAAA MML命令创建完整流程

前提: omres-cli 已由用户在上游（E2E 阶段零）完成登录，本工作流只校验登录态，不会登录。

使用方法:
    from workflow import execute_workflow

    result = execute_workflow(
        taskName="ZL0605_HLBAAA",
        moduleId=5,
        moduleName="PCFNCS",
        moc_name="HLBAAA",
        moc_desc_ch="HTTP负载均衡配置",
        moc_desc_en="HTTP Load Balance AAA Configuration",
        enum_type_name="HlbPeerFlowCtrlSwitch",
        fields=[
            {"name": "AUTOSWITCH", "type": "ENUM", "isKey": 1, "range": "HlbPeerFlowCtrlSwitch"},
            {"name": "HLBMAXNUM", "type": "UINT32", "isKey": 0, "range": "4000~15000", "default": "12000"}
        ],
        commands=[
            {"name": "SET HLBAAA", "type": "update"},
            {"name": "LST HLBAAA", "type": "get-config"}
        ],
        file_path="D:/git/26.0/ComConfig/om/ZL0605.zip"
    )
"""

import sys
import os
import io
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Dict, Any, Optional

# 修复Windows GBK终端无法输出Unicode字符(如✓)的问题
if sys.platform == 'win32' and sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import WorkflowContext, create_context, StepExecutionError

# 导入所有Skill
from s01_login import ensure_authenticated, get_authenticated_username
from s02_create_project import create_project
from s03_upload_file import upload_file, create_zip_package
from s04_parse_xml import parse_xml
from s05_create_moc import create_moc, query_moc_list
from s06_create_enum import create_enum_type, add_enum_item
from s08_manage_fields import add_field, query_field_list, update_field_info, add_default_record, add_default_records
from s12_manage_methods import add_method, update_method_name, delete_methods, query_method_info
from s15_mml_commands import create_mml_command, add_command_para, add_command_branch, select_by_id_mml_command
from s16_add_branch import add_conditional_branch
from s17_validation import start_validation, query_validation_result, export_model, get_export_status, export_sync_and_create_mr
from s18_add_errorcode import add_error_code_with_lua_association, copy_lua_script_to_repo

if TYPE_CHECKING:
    from requests import Session


def setup_logging(log_dir: str = None, taskName: str = None) -> logging.Logger:
    """配置日志记录"""
    if log_dir is None:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)

    if taskName is None:
        taskName = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_file = os.path.join(log_dir, f"workflow_{taskName}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    logger = logging.getLogger(f"workflow_{taskName}")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


SERVICE_MAP = {}


def _find_omres_cli() -> str:
    """查找omres-cli可执行文件路径"""
    import shutil
    cli_path = shutil.which("omres-cli") or shutil.which("omres-cli.exe")
    if cli_path:
        return cli_path

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(project_root, "omres-cli", "omres-cli", "omres-cli.exe"),
        os.path.join(project_root, "omres-cli", "omres-cli"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    cwd = os.getcwd()
    for _ in range(3):
        candidate = os.path.join(cwd, "omres-cli", "omres-cli", "omres-cli.exe")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    return "omres-cli"


def _run_cli(context: WorkflowContext, args: list, step_name: str, timeout: int = 60) -> dict:
    """执行omres-cli命令并解析JSON-RPC 2.0输出"""
    import subprocess, json as _json
    cli_path = _find_omres_cli()
    cmd = [cli_path] + args
    if context.base_url:
        cmd.extend(["--server", context.base_url])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    except FileNotFoundError:
        raise StepExecutionError(step_name=step_name, message=f"omres-cli未找到: {cli_path}", context_state=context.state)
    except subprocess.TimeoutExpired:
        raise StepExecutionError(step_name=step_name, message="omres-cli命令执行超时", context_state=context.state)
    output = proc.stdout.strip() if proc.stdout else ""
    if not output:
        stderr_msg = proc.stderr.strip() if proc.stderr else ""
        raise StepExecutionError(step_name=step_name, message=f"命令返回为空, stderr: {stderr_msg[:200]}", context_state=context.state)
    try:
        rpc_output = _json.loads(output)
    except _json.JSONDecodeError as e:
        raise StepExecutionError(step_name=step_name, message=f"响应JSON解析失败: {e}", context_state=context.state)
    if "error" in rpc_output:
        error = rpc_output["error"]
        error_msg = error.get("message", "未知错误")
        if "data" in error:
            data = error["data"]
            if isinstance(data, dict):
                error_msg = data.get("msg", data.get("message", error_msg))
            elif isinstance(data, str):
                error_msg = data
        raise StepExecutionError(step_name=step_name, message=f"命令执行失败: {error_msg}", context_state=context.state)
    result = rpc_output.get("result", {})
    if isinstance(result, dict):
        code = result.get("code")
        if code is not None and code != 0:
            raise StepExecutionError(step_name=step_name, message=f"业务执行失败: {result.get('msg', result.get('message', '未知错误'))}", context_state=context.state)
        if "status" in result and not result.get("status"):
            raise StepExecutionError(step_name=step_name, message=f"业务执行失败: {result.get('message', '未知错误')}", context_state=context.state)
    return result


def fetch_service_map(context: WorkflowContext) -> dict:
    """
    从API动态获取SERVICE_MAP — 通过omres-cli

    Args:
        context: 工作流上下文

    Returns:
        dict: serviceName -> {moduleId, moduleName} 的映射
    """
    task_id = context.get_state("taskId")

    body = json.dumps({"projectId": str(task_id)}, ensure_ascii=False)
    try:
        result = _run_cli(context, ["overallview", "search", "--body", body], "fetch_service_map", timeout=60)
    except StepExecutionError as e:
        print(f"  [WARNING] fetch_service_map失败: {e}")
        return SERVICE_MAP

    data = result.get("data", [])

    service_map = {}
    for item in data:
        service_name = item.get("serviceName")
        module_name = item.get("moduleName")
        module_id = item.get("id")
        if service_name and module_name and module_id:
            service_map[service_name] = {
                "moduleId": module_id,
                "moduleName": module_name
            }

    print(f"  [DEBUG] 动态获取SERVICE_MAP: {len(service_map)} 个服务")
    return service_map


def execute_workflow(
    taskName: str,
    serviceName: str,
    moc_name: str,
    moc_desc_ch: str,
    moc_desc_en: str,
    enum_type_name: str,
    fields: List[Dict[str, Any]],
    commands: List[Dict[str, str]],
    file_path: str,
    base_url: str = "http://10.243.80.228",
    error_codes: List[Dict[str, Any]] = None,
    userName: str = None,
    w3Num: str = None,
    passwd: str = None
) -> Dict[str, Any]:
    """
    执行HLBAAA MML命令创建的完整工作流程

    前提：omres-cli 登录已由用户在上游（E2E 阶段零）统一完成，本函数只校验登录态。

    Args:
        taskName: 工程名称
        serviceName: 服务名称 (如 PcfPolicyEngineService)
        moc_name: 原子对象名称
        moc_desc_ch: 原子对象中文描述
        moc_desc_en: 原子对象英文描述
        enum_type_name: 枚举类型名称
        fields: 字段配置列表 [{"name": "AUTOSWITCH", "type": "ENUM", "isKey": 1, ...}, ...]
        commands: 命令配置列表 [{"name": "SET HLBAAA", "type": "update"}, ...]
        file_path: 上传文件路径
        base_url: API基础URL
        error_codes: 错误码配置列表，每个配置包含:
            - code: 错误码名称（如ZL_58321）
            - code_num: 错误码数字（如58321）
            - descCh: 中文描述
            - descEn: 英文描述
        userName: 用户名（可选，不传则从 omres-cli 登录态中获取）
        w3Num: 工号（可选，不传则与 userName 相同）
        passwd: 已废弃，传入会被忽略（登录由用户在阶段零统一完成）

    Returns:
        dict: 包含执行结果的字典
    """
    global SERVICE_MAP

    # 根据服务名获取moduleId和moduleName
    service_info = SERVICE_MAP.get(serviceName, {})
    moduleId = service_info.get("moduleId", 11)
    moduleName = service_info.get("moduleName", "PCFPES")

    # 设置日志记录
    logger = setup_logging(taskName=taskName)

    if passwd:
        logger.warning("  ! passwd 参数已废弃并被忽略：omres-cli 登录已由用户在阶段零统一完成")

    # 创建上下文（不再持有任何凭据）
    context = create_context(
        base_url=base_url,
        userName=userName,
        taskName=taskName,
        w3Num=w3Num or userName,
        moduleId=moduleId,
        moduleName=moduleName,
        moc_name=moc_name,
        moc_desc_ch=moc_desc_ch,
        moc_desc_en=moc_desc_en,
        file_path=file_path
    )

    logger.info("=" * 60)
    logger.info("HLBAAA MML命令创建工作流程")
    logger.info("=" * 60)
    logger.info(f"工程: {taskName}")
    logger.info(f"模块: {moduleName} (ID: {moduleId})")
    logger.info(f"原子对象: {moc_name}")
    logger.info(f"文件: {file_path}")

    print("=" * 60)
    print("HLBAAA MML命令创建工作流程")
    print("=" * 60)
    print(f"工程: {taskName}")
    print(f"模块: {moduleName} (ID: {moduleId})")
    print(f"原子对象: {moc_name}")
    print(f"文件: {file_path}")
    print()

    try:
        # 记录开始时间
        context.set_state("start_time", datetime.now())

        # ========== 阶段1: 登录态校验与创建工程 ==========
        logger.info("[阶段1] 登录态校验与创建工程")
        logger.info("-" * 40)

        # 登录已在上游统一完成，这里只校验；未认证直接失败，不代为登录
        ensure_authenticated(context)

        # 用户名可以不传，从登录态里取
        resolved_user = context.get_state("userName") or userName or get_authenticated_username(context)
        if not resolved_user:
            raise StepExecutionError(
                step_name="ensure_authenticated",
                message="无法确定当前用户名：omres-cli 登录态中没有用户名信息，请显式传入 userName",
                context_state=context.state
            )
        context.userName = resolved_user
        if not getattr(context, "w3Num", None):
            context.w3Num = resolved_user
        logger.info(f"  ✓ omres-cli 登录态有效: {resolved_user}")

        create_project(context)
        task_id = context.get_state("taskId")
        logger.info(f"  ✓ 工程创建成功: taskId={task_id}")

        # ========== 阶段2: 上传与解析 ==========
        logger.info("")
        logger.info("[阶段2] 上传与解析建模文件")
        logger.info("-" * 40)

        upload_zip_path = create_zip_package(source_dir=file_path)
        context.set_state("upload_zip_path", upload_zip_path)
        context.set_state("file_path", upload_zip_path)
        context.set_state("zip_path", upload_zip_path)
        logger.info(f"  ✓ 压缩包创建成功: {upload_zip_path}")

        upload_file(context)
        logger.info(f"  ✓ 文件上传成功")

        if os.path.exists(upload_zip_path):
            os.remove(upload_zip_path)
            logger.info(f"  ✓ 压缩包已删除")

        parse_xml(context)
        logger.info(f"  ✓ XML解析成功")

        # ========== 动态获取SERVICE_MAP ==========
        logger.info("")
        logger.info("[阶段2.5] 动态获取服务映射")
        logger.info("-" * 40)

        SERVICE_MAP = fetch_service_map(context)
        logger.info(f"  ✓ SERVICE_MAP动态获取成功: {len(SERVICE_MAP)} 个服务")

        # 更新上下文中的moduleId和moduleName
        service_info = SERVICE_MAP.get(serviceName, {})
        context.moduleId = service_info.get("moduleId", 11)
        context.moduleName = service_info.get("moduleName", "PCFPES")
        logger.info(f"  ✓ {serviceName} -> moduleId={context.moduleId}, moduleName={context.moduleName}")

        # ========== 阶段3: 创建原子对象 ==========
        logger.info("")
        logger.info("[阶段3] 创建原子对象")
        logger.info("-" * 40)

        create_moc(context)
        logger.info(f"  ✓ 原子对象 {moc_name} 创建成功")

        query_moc_list(context)
        moc_id = context.get_state("mocId")
        logger.info(f"  ✓ mocId={moc_id}")

        # ========== 阶段4: 创建枚举类型 ==========
        # 为每个枚举字段创建独立的枚举类型
        logger.info("")
        logger.info("[阶段4] 创建自定义枚举类型")
        logger.info("-" * 40)

        field_enum_mapping = {}  # 存储字段名到枚举cdtId的映射
        enum_idx = 0
        for field_cfg in fields:
            if field_cfg.get("type") == "ENUM" and field_cfg.get("range"):
                enum_idx += 1
                field_name = field_cfg["name"]
                this_enum_type_name = f"{enum_type_name}_{field_name}"
                range_str = field_cfg["range"]
                create_enum_type(context, dataType=this_enum_type_name, rangeStr=range_str)
                this_cdt_id = context.get_state("cdtId")
                field_enum_mapping[field_name] = {
                    "cdtId": this_cdt_id,
                    "enumTypeName": this_enum_type_name
                }
                logger.info(f"  ✓ 枚举类型 {this_enum_type_name} 创建成功: cdtId={this_cdt_id}")

        first_enum_cdt_id = list(field_enum_mapping.values())[0]["cdtId"] if field_enum_mapping else None

        # ========== 阶段5: 添加字段 ==========
        logger.info("")
        logger.info("[阶段5] 添加字段")
        logger.info("-" * 40)

        for field_cfg in fields:
            field_name = field_cfg["name"]
            add_field(context, fieldName=field_name, isKey=field_cfg.get("isKey", 0))
            logger.info(f"  ✓ 字段 {field_name} 添加成功")

        query_field_list(context)
        field_map = context.get_state("field_map", {})
        logger.info(f"  ✓ 字段映射: {field_map}")

        # ========== 阶段6: 设置字段类型 ==========
        logger.info("")
        logger.info("[阶段6] 设置字段数据类型")
        logger.info("-" * 40)

        for field_cfg in fields:
            field_name = field_cfg["name"]
            field_type = field_cfg["type"]

            if field_type == "ENUM":
                enum_info = field_enum_mapping.get(field_name, {})
                this_cdt_id = enum_info.get("cdtId", first_enum_cdt_id)
                this_enum_name = enum_info.get("enumTypeName", enum_type_name)
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=25,  # ENUM
                    dataTypeName="ENUM",
                    rangeStr=this_enum_name,
                    isKey=field_cfg.get("isKey", 0),
                    customizeDataTypeId=this_cdt_id
                )
            elif field_type == "UINT32":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=17,  # UINT32
                    dataTypeName="uint32",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "INT32":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=13,  # INT32
                    dataTypeName="int32",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "STRING":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=19,  # STRING
                    dataTypeName="string",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "INT8":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=11,  # INT8
                    dataTypeName="int8",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "INT16":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=12,  # INT16
                    dataTypeName="int16",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "INT64":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=14,  # INT64
                    dataTypeName="int64",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "UINT8":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=15,  # UINT8
                    dataTypeName="uint8",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "UINT16":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=16,  # UINT16
                    dataTypeName="uint16",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "UINT64":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=18,  # UINT64
                    dataTypeName="uint64",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "BOOL":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=21,  # BOOL
                    dataTypeName="bool",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "IPV4":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=29,  # IPV4
                    dataTypeName="ipv4",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            elif field_type == "IPV6":
                update_field_info(
                    context,
                    fieldName=field_name,
                    dataTypeId=30,  # IPV6
                    dataTypeName="ipv6",
                    rangeStr=field_cfg.get("range", ""),
                    isKey=field_cfg.get("isKey", 0),
                    defaultValue=field_cfg.get("default", "")
                )
            print(f"  ✓ 字段 {field_name} 类型设置为 {field_type}")

        import time
        print(f"  [DEBUG] 等待mmlPara创建完成...")
        time.sleep(3)

        # ========== 阶段7: 添加默认值记录 ==========
        print()
        print("[阶段7] 添加默认值记录")
        print("-" * 40)

        # 检查用户是否提供了defaultRecordsList（多行默认记录）
        default_records_list = commands[0].get("defaultRecordsList", []) if commands else []

        if default_records_list:
            # 多行默认记录：需要将字段名转换为fieldId
            default_records_to_add = []
            for record in default_records_list:
                field_id_record = {}
                for field_name, field_value in record.items():
                    if field_name in field_map:
                        field_id_record[str(field_map[field_name])] = field_value
                if field_id_record:
                    default_records_to_add.append(field_id_record)

            if default_records_to_add:
                add_default_records(context, defaultRecordsList=default_records_to_add)
                print(f"  ✓ 多行默认值记录添加成功: {len(default_records_to_add)} 行")
        else:
            # 单行默认记录（字段级别的default）
            default_records = {}
            for field_cfg in fields:
                if "default" in field_cfg:
                    field_id = field_map.get(field_cfg["name"])
                    if field_id:
                        default_records[str(field_id)] = field_cfg["default"]

            if default_records:
                add_default_record(context, defaultRecords=default_records)
                print(f"  ✓ 默认值记录添加成功: {default_records}")

        # ========== 阶段8: 创建MML命令 ==========
        print()
        print("[阶段8] 创建MML命令")
        print("-" * 40)

        # 调试函数：查询当前方法列表 — 通过omres-cli method select-info
        def debug_query_methods(step_name):
            try:
                moc_id = context.get_required_state("mocId")
                task_id = context.get_required_state("taskId")
                body = json.dumps({"taskId": str(task_id), "mocId": moc_id}, ensure_ascii=False)
                result = _run_cli(context, ["method", "select-info", "--body", body], "debug_query_methods")
                print(f"  [DEBUG] {step_name}后方法列表:")
                methods = result.get("data", [])
                for m in methods:
                    print(f"    - methodId={m.get('methodId')}, commandType={m.get('commandType')}, mmlCommandName={m.get('mmlCommandName')}")
            except Exception as e:
                print(f"    查询失败: {e}")

        # 查询现有方法
        query_method_info(context)
        method_list = context.get_state("method_list", [])
        existing_methods = {}  # 保存现有方法ID
        for method in method_list:
            existing_methods[method.get("commandType")] = method.get("methodId")

        debug_query_methods("初始查询")

        has_update_cmd = False
        has_create_cmd = False
        has_delete_cmd = False

        for cmd_cfg in commands:
            cmd_type = cmd_cfg["type"]
            if cmd_type == "update":
                has_update_cmd = True
            if cmd_type == "create":
                has_create_cmd = True
            if cmd_type == "delete":
                has_delete_cmd = True

        # 根据commands配置决定要删除的方法
        # ADD/RMV/LST时 → 删除update方法，保留create/delete/get-config
        # ADD/MOD/RMV/LST时 → 保留所有方法
        # SET/LST时 → 删除create/delete方法，保留update/get-config
        methods_to_delete = []

        if not has_update_cmd and "update" in existing_methods:
            # 用户没有提供update命令，删除update方法
            methods_to_delete.append(existing_methods["update"])

        if not has_create_cmd and not has_delete_cmd:
            if "create" in existing_methods:
                methods_to_delete.append(existing_methods["create"])
            if "delete" in existing_methods:
                methods_to_delete.append(existing_methods["delete"])

        if methods_to_delete:
            print(f"  [DEBUG] 需要删除的方法: {methods_to_delete}")
            delete_methods(context, methodIds=methods_to_delete)
            print(f"  ✓ 动态删除方法成功，删除了 {len(methods_to_delete)} 个方法")

            # 重新查询方法列表更新existing_methods
            query_method_info(context)
            method_list = context.get_state("method_list", [])
            existing_methods = {}
            for method in method_list:
                existing_methods[method.get("commandType")] = method.get("methodId")

        debug_query_methods("动态删除方法后")

        for cmd_cfg in commands:
            cmd_name = cmd_cfg["name"]
            cmd_type = cmd_cfg["type"]

            # 直接复用现有方法，不用再创建MML命令
            method_id = existing_methods.get(cmd_type)
            if method_id:
                context.set_state("methodId", method_id)
                print(f"  ✓ 复用现有方法: {cmd_type}, methodId={method_id}")

            debug_query_methods(f"绑定方法名前 ({cmd_type})")

            # 绑定方法名
            update_method_name(context, commandType=cmd_type, mmlCommandName=cmd_name)
            print(f"  ✓ 方法名绑定成功: {cmd_name}")

            # 获取mmlCommandId（从selectByIdMmlCommand响应中获取）
            select_by_id_mml_command(context, methodId=method_id)
            real_mml_command_id = context.get_state("mmlCommandId")
            print(f"  ✓ 获取到mmlCommandId: {real_mml_command_id}")

            # 调用insertOrUpdate设置service、descCh、descEn等属性
            # 注意：id用methodId，mmlCommandId用selectByIdMmlCommand响应中的值
            # commandType映射: create->Add, delete->Remove, update->Modify, get-config->Lst
            cmd_type_map = {"create": "Add", "delete": "Remove", "update": "Modify", "get-config": "Lst"}
            cmd_type_api = cmd_type_map.get(cmd_type, "Lst")
            create_mml_command(
                context,
                mmlCommandName=cmd_name,
                commandType=cmd_type_api,
                service=serviceName,
                descCh="增加话统统计对象" if cmd_type == "create" else "删除话统统计对象" if cmd_type == "delete" else "查询话统统计对象",
                descEn="Add Performance Measurement Object" if cmd_type == "create" else "Remove Performance Measurement Object" if cmd_type == "delete" else "List Performance Measurement Object",
                commandId=method_id,
                mmlCommandId=real_mml_command_id,
                moduleId=moduleId
            )

            debug_query_methods(f"create_mml_command后 ({cmd_name})")

            # 添加命令参数 - 根据用户配置的params决定输入输出
            cmd_params = cmd_cfg.get("params", [])
            for param_cfg in cmd_params:
                field_name = param_cfg["field"]
                field_id = field_map.get(field_name)
                if not field_id:
                    continue

                field_cfg = next((f for f in fields if f["name"] == field_name), None)
                if not field_cfg:
                    continue

                io_type = param_cfg.get("io", "input")
                is_required = param_cfg.get("required", False)
                is_must = "必选" if is_required else "可选"
                if io_type == "input":
                    type_in_web = "输入参数"
                elif io_type == "output":
                    type_in_web = "输出参数"
                elif io_type == "input&output":
                    type_in_web = "输入&输出"
                else:
                    type_in_web = "输入参数"

                add_command_para(
                    context,
                    mmlParaName=field_name,
                    fieldId=field_id,
                    isMust=is_must,
                    typeInWeb=type_in_web,
                    rangeStr=field_cfg.get("range", ""),
                    customizeDataTypeId=field_enum_mapping.get(field_name, {}).get("cdtId") if field_cfg["type"] == "ENUM" else None,
                    defaultValue=field_cfg.get("default", ""),
                    dataTypeId=5 if field_cfg["type"] == "ENUM" else 1,
                    commandId=method_id
                )
                print(f"    - 参数 {field_name} ({type_in_web}，{is_must}) 添加成功")

            # 添加分支参数 (条件参数) - 根据commands中的branch配置
            cmd_branches = cmd_cfg.get("branches", [])
            for branch_cfg in cmd_branches:
                switch_field = branch_cfg.get("switchField")
                trigger_value = branch_cfg.get("triggerValue", 0)
                child_fields = branch_cfg.get("childFields", [])
                if switch_field and child_fields:
                    add_conditional_branch(
                        context,
                        switchFieldName=switch_field,
                        triggerEnumValue=trigger_value,
                        childFieldNames=child_fields,
                        commandId=method_id
                    )
                    print(f"    - 分支条件: 当{switch_field}={trigger_value}时显示{child_fields}")

        # ========== 阶段9: 错误码与Lua脚本 ==========
        if error_codes:
            print()
            print("[阶段9] 添加错误码与Lua脚本")
            print("-" * 40)

            script_ops = []
            for cmd in commands:
                cmd_type = cmd.get("type", "")
                if cmd_type == "update":
                    script_ops.append("SET")
                elif cmd_type == "create":
                    script_ops.append("ADD")
                elif cmd_type == "delete":
                    script_ops.append("RMV")
                elif cmd_type == "get-config":
                    script_ops.append("LST")

            error_code_result = add_error_code_with_lua_association(
                context,
                moc_name=moc_name,
                moc_desc_ch=moc_desc_ch,
                moc_desc_en=moc_desc_en,
                error_codes=error_codes,
                script_operations=script_ops,
                service_name=serviceName
            )
            print(f"  ✓ 错误码与Lua脚本处理完成")

            if error_code_result.get("lua_generation") and error_codes:
                lua_file_path = os.path.join(
                    "D:/download/lua_scripts",
                    str(context.get_state("taskId")),
                    str(context.moduleId),
                    str(context.get_state("mocId")),
                    "generate",
                    f"{moc_name}.lua"
                )
                copy_lua_script_to_repo(
                    generated_lua_path=lua_file_path,
                    repo_path=file_path,
                    service_name=serviceName,
                    moc_name=moc_name,
                    fields=fields,
                    error_codes=error_codes
                )
                print(f"  ✓ Lua脚本已同步到仓库")

        # ========== 阶段10: 校验 ==========
        print()
        print("[阶段10] 启动校验")
        print("-" * 40)

        start_validation(context)
        is_passed = context.get_state("validation_passed")
        print(f"  ✓ 校验完成: {'通过' if is_passed else '未通过'}")

        if not is_passed:
            query_validation_result(context)
            errors = context.get_state("validation_errors", [])
            print(f"  ⚠ 发现 {len(errors)} 个错误:")
            for err in errors[:5]:
                print(f"    - [{err.get('error_level')}] {err.get('error_message')}")
                print(f"      位置: {err.get('field')}")

        # ========== 阶段11: 导出 ==========
        if is_passed:
            print()
            print("[阶段11] 导出模型")
            print("-" * 40)

            export_model(context)
            print(f"  ✓ 模型导出成功")

            # ========== 阶段12: 解压、同步、提交Git、创建MR ==========
            print()
            print("[阶段12] 解压同步文件并创建MR")
            print("-" * 40)

            mr_result = export_sync_and_create_mr(context, moc_name=moc_name, service_name=serviceName)
            context.set_state("mr_result", mr_result)

        # ========== 完成 ==========
        print()
        print("=" * 60)
        print("工作流程执行完成")
        print("=" * 60)
        print(f"taskId: {context.get_state('taskId')}")
        print(f"mocId: {context.get_state('mocId')}")
        print(f"校验结果: {'通过' if is_passed else '未通过'}")
        mr_result = context.get_state("mr_result", {})
        if mr_result.get("mr_url"):
            print(f"MR: {mr_result['mr_url']}")
        print()

        # 构建执行报告
        end_time = datetime.now()
        start_time = context.get_state("start_time", end_time)
        duration = (end_time - start_time).total_seconds()

        # 收集命令信息
        report_commands = []
        for cmd_cfg in commands:
            cmd_info = {
                "name": cmd_cfg["name"],
                "type": cmd_cfg["type"],
                "params": [p["field"] for p in cmd_cfg.get("params", [])],
                "branches": []
            }
            for branch in cmd_cfg.get("branches", []):
                cmd_info["branches"].append({
                    "switchField": branch["switchField"],
                    "triggerValue": branch["triggerValue"],
                    "triggerName": "",  # 可以后续补充
                    "childFields": branch["childFields"]
                })
            report_commands.append(cmd_info)

        # 收集字段信息
        report_fields = []
        for field_cfg in fields:
            report_fields.append({
                "name": field_cfg["name"],
                "type": field_cfg["type"],
                "isKey": field_cfg.get("isKey", 0) == 1,
                "range": field_cfg.get("range", "")
            })

        # 收集默认记录
        default_records_list = commands[0].get("defaultRecordsList", []) if commands else []

        # 构建完整报告
        report = {
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S") if isinstance(start_time, datetime) else start_time,
            "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": int(duration),
            "moc_name": moc_name,
            "service_name": serviceName,
            "commands": report_commands,
            "fields": report_fields,
            "default_records": default_records_list,
            "mr_url": mr_result.get("mr_url", ""),
            "git_branch": mr_result.get("branch", ""),
            "changed_files": mr_result.get("changed_files", [])
        }

        # 输出报告
        print()
        print("=" * 60)
        print("执行报告")
        print("=" * 60)
        print(f"开始时间: {report['start_time']}")
        print(f"结束时间: {report['end_time']}")
        print(f"执行耗时: {report['duration_seconds']} 秒")
        print(f"原子对象: {report['moc_name']}")
        print(f"服务名称: {report['service_name']}")
        print()
        print("创建的命令:")
        for cmd in report_commands:
            print(f"  - {cmd['name']} ({cmd['type']})")
            if cmd['branches']:
                for branch in cmd['branches']:
                    print(f"      当 {branch['switchField']}={branch['triggerValue']} 时显示: {branch['childFields']}")
        print()
        print("创建的字段:")
        for field in report_fields:
            print(f"  - {field['name']} ({field['type']}) key={field['isKey']} range={field['range']}")
        if default_records_list:
            print()
            print("添加的默认记录:")
            for idx, record in enumerate(default_records_list):
                print(f"  行{idx+1}: {record}")
        if mr_result.get("mr_url"):
            print()
            print(f"MR: {mr_result['mr_url']}")
            print(f"分支: {mr_result.get('branch', '')}")
        print()

        return {
            "status": "success",
            "taskId": context.get_state("taskId"),
            "mocId": context.get_state("mocId"),
            "cdtId": context.get_state("cdtId"),
            "validation_passed": is_passed,
            "mr_result": mr_result,
            "report": report,
            "errors": context.get_state("validation_errors", []) if not is_passed else []
        }

    except StepExecutionError as e:
        print()
        print("=" * 60)
        print(f"工作流程执行失败: {e}")
        print("=" * 60)
        return {
            "status": "failed",
            "step": e.step_name,
            "message": e.message,
            "state": context.state
        }

    except Exception as e:
        print()
        print("=" * 60)
        print(f"工作流程执行异常: {e}")
        print("=" * 60)
        return {
            "status": "error",
            "message": str(e),
            "state": context.state
        }


if __name__ == "__main__":
    from datetime import datetime

    # 登录已在阶段零由用户统一完成，这里不再读取任何凭据；
    # 未登录时 execute_workflow 会在第一步直接失败并提示重新登录。
    ts = datetime.now().strftime("%H%M%S")[4:]
    import random
    ts = f"{ts}{random.randint(10, 99)}"
    result = execute_workflow(
        taskName=f"ZL0605_STATATTRCMD{ts}",
        serviceName="PcfPolicyEngineService",
        moc_name=f"STATATTRCMD{ts}",
        moc_desc_ch="话统统计对象",
        moc_desc_en="Performance Measurement Object",
        enum_type_name="StatAttrCmd",
        fields=[
            {"name": "INDEX", "type": "INT32", "isKey": 1, "range": "1~128"},
            {"name": "OBJID", "type": "ENUM", "isKey": 0, "range": "SUBID（0）,APN（1）"},
            {"name": "MATCHTYPE", "type": "ENUM", "isKey": 0, "range": "PREFIX（0）,POSTFIX（1）"},
            {"name": "OBJPARA", "type": "STRING", "isKey": 0, "range": "5~15", "maxLength": 15}
        ],
        commands=[
            {
                "name": f"ADD STATATTRCMD{ts}",
                "type": "create",
                "params": [
                    {"field": "INDEX", "io": "input", "required": True},
                    {"field": "OBJID", "io": "input", "required": True},
                    {"field": "MATCHTYPE", "io": "input", "required": True},
                    {"field": "OBJPARA", "io": "input", "required": True}
                ]
            },
            {
                "name": f"RMV STATATTRCMD{ts}",
                "type": "delete",
                "params": [
                    {"field": "INDEX", "io": "input", "required": True}
                ]
            },
            {
                "name": f"LST STATATTRCMD{ts}",
                "type": "get-config",
                "params": [
                    {"field": "INDEX", "io": "input&output", "required": False},
                    {"field": "OBJID", "io": "input&output", "required": False},
                    {"field": "MATCHTYPE", "io": "output", "required": False},
                    {"field": "OBJPARA", "io": "output", "required": False}
                ]
            }
        ],
        file_path="D:/git/26.0/ComConfig/om",
        base_url="https://omtool.rnd.huawei.com",
        error_codes=[
            {
                "code": "ZL_58321",
                "code_num": 58321,
                "descCh": "缓冲区阈值冲突",
                "descEn": "Buffer threshold conflict"
            }
        ]
    )
    print()
    print("最终结果:")
    print(result)
