import os
import re
import sys
import subprocess
import site
import shutil
import hashlib
import importlib.metadata
from pathlib import Path
from . import mfaalog  # 日志工具

# =========================================================
# [配置区] 环境与依赖设定
# =========================================================
# maafw 的 py 库版本不再手写，跟随拉起本进程的客户端（C 侧）。
# PI V2 协议要求客户端启动 agent 时注入 PI_CLIENT_MAAFW_VERSION，值是客户端实际加载的
# MaaFramework 原生库的 MaaVersion()；VS Code 插件（maa-support）与 MFAAvalonia 都会注入。
# 发布包不走这里：CI 从 MFAAvalonia 二进制探测版本（scripts/detect_maa_version.py）。
CLIENT_MAAFW_ENV = "PI_CLIENT_MAAFW_VERSION"
# 客户端没报版本、环境里也还没装 maafw 时，按此范围安装。
FALLBACK_MAAFW_SPEC = ">=5.11,<6.1"
VENV_NAME = ".venv"
PREFERRED_PYTHON_VERSION = "3.10"
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
# =========================================================

def get_venv_path(project_root: Path) -> Path:
    return project_root / VENV_NAME

def is_running_in_venv() -> bool:
    return sys.prefix != sys.base_prefix

def is_running_in(venv_path: Path) -> bool:
    try:
        return os.path.samefile(sys.prefix, venv_path)
    except OSError:
        return False

def get_venv_executable(venv_path: Path) -> Path:
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    else:
        return venv_path / "bin" / "python"

def check_existing_venv_version(venv_path: Path):
    """[新增] 读取现有虚拟环境的配置文件，检查版本是否匹配"""
    cfg_path = venv_path / "pyvenv.cfg"
    if not cfg_path.exists():
        return
    
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('version'):
                    # 提取版本号，如 "version = 3.11.4" -> "3.11.4"
                    ver = line.split('=')[1].strip()
                    if not ver.startswith(PREFERRED_PYTHON_VERSION):
                        mfaalog.warning(f"⚠️ [环境提示] 当前虚拟环境版本为 {ver}，与首选版本 {PREFERRED_PYTHON_VERSION} 不匹配！")
                        mfaalog.warning("💡 如果遇到兼容性问题，建议删除 .venv 文件夹让程序重新创建。")
                    break
    except Exception as e:
        mfaalog.debug(f"读取 pyvenv.cfg 失败: {e}")

def find_preferred_python() -> str:
    current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    
    if current_version != PREFERRED_PYTHON_VERSION:
        # 明确指出这是宿主环境，避免与虚拟环境混淆
        mfaalog.info(f"💡 [环境提示] 当前外部终端使用的宿主 Python 版本 ({current_version}) 与首选开发版本 ({PREFERRED_PYTHON_VERSION}) 不一致。")
        mfaalog.info(f"正在系统中寻找 Python {PREFERRED_PYTHON_VERSION} 以为您克隆标准的虚拟隔离环境...")
        
    if sys.platform == "win32":
        try:
            cmd = ["py", f"-{PREFERRED_PYTHON_VERSION}", "-c", "import sys; print(sys.executable)"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True) # nosec
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            mfaalog.warning(f"⚠️ 未能通过 py 启动器找到 Python {PREFERRED_PYTHON_VERSION}，将回退使用 ({current_version})。")
    else:
        target_cmd = f"python{PREFERRED_PYTHON_VERSION}"
        path = shutil.which(target_cmd)
        if path:
            return path
        mfaalog.warning(f"⚠️ 未在环境变量中找到 {target_cmd}，将回退使用 ({current_version})。")
            
    return sys.executable
def create_venv(venv_path: Path):
    if venv_path.exists() and (venv_path / "pyvenv.cfg").exists():
        mfaalog.debug("虚拟环境已存在，跳过创建。")
        check_existing_venv_version(venv_path) # [修复] 在跳过创建时，补充版本检查提示
        return

    mfaalog.info(f"正在创建虚拟环境: {venv_path} ...")
    base_python = find_preferred_python()
    mfaalog.info(f"🔧 使用基础解释器: {base_python}")
    
    try:
        subprocess.check_call([base_python, "-m", "venv", str(venv_path)]) # nosec
        mfaalog.info("✅ 虚拟环境创建成功")
    except subprocess.CalledProcessError as e:
        mfaalog.error(f"❌ 创建失败: {e}")
        raise

def client_maafw_version():
    """客户端报来的框架版本，转成 PyPI 写法（v5.12.2 → 5.12.2）。

    没报、或报的值在 PyPI 上不会有对应正式版（预发布等）时返回 None，调用方保持现状。
    """
    raw = os.environ.get(CLIENT_MAAFW_ENV, "").strip()
    if not raw:
        return None
    version = raw[1:] if raw[0] in "vV" else raw
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        mfaalog.warning(f"客户端报告的框架版本 {raw!r} 对不上 PyPI 的 maafw 正式版，保持现有版本。")
        return None
    return version

def installed_maafw_version():
    """当前解释器里装的 maafw 版本；只读 dist-info，不加载原生库（加载会锁住 DLL，pip 就换不掉了）。"""
    try:
        return importlib.metadata.version("maafw")
    except importlib.metadata.PackageNotFoundError:
        return None

def pip_install(*args: str) -> bool:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-i", PIP_INDEX, *args]) # nosec
        importlib.invalidate_caches()  # 本进程随后就要 import 刚装的包
        return True
    except subprocess.CalledProcessError as e:
        mfaalog.warning(f"pip install {' '.join(args)} 失败（退出码 {e.returncode}）")
    except OSError as e:
        mfaalog.warning(f"pip install {' '.join(args)} 无法启动: {e}")
    return False

def sync_requirements(project_root: Path, venv_path: Path):
    """requirements.txt 变了才重装，指纹记在 venv 的 .deps_marker 里。"""
    req_file = project_root / "requirements.txt"
    marker_file = venv_path / ".deps_marker"
    current_hash = hashlib.sha256(req_file.read_bytes()).hexdigest()
    try:
        recorded = marker_file.read_text(encoding='utf-8').strip()
    except OSError:
        recorded = ""
    if recorded == current_hash:
        mfaalog.debug("requirements.txt 指纹一致，跳过安装。")
        return

    mfaalog.info("📦 requirements.txt 有更新或未安装，开始拉取依赖...")
    if pip_install("-r", str(req_file)):
        marker_file.write_text(current_hash, encoding='utf-8')

def sync_maafw():
    """maafw 跟随客户端版本。装不上就沿用现有版本，不记任何缓存，下次启动重试。"""
    target = client_maafw_version()
    current = installed_maafw_version()
    if target is None:
        if current is None:
            mfaalog.info(f"没有可跟随的客户端版本，按兼容范围安装 maafw{FALLBACK_MAAFW_SPEC} ...")
            pip_install(f"maafw{FALLBACK_MAAFW_SPEC}")
        else:
            mfaalog.debug(f"没有可跟随的客户端版本，保持 maafw {current}")
        return
    if current == target:
        mfaalog.debug(f"maafw {current} 与客户端一致")
        return

    mfaalog.info(f"🔄 maafw {current or '未安装'} → {target}（跟随客户端）")
    if pip_install(f"maafw=={target}"):
        mfaalog.info(f"✅ maafw 已对齐到 {target}")
    elif current is None:
        pip_install(f"maafw{FALLBACK_MAAFW_SPEC}")
    else:
        mfaalog.warning(f"maafw 未能切换到 {target}，本次沿用 {current}，下次启动重试。")

def ensure_venv(project_root: Path):
    venv_path = get_venv_path(project_root)
    if is_running_in_venv():
        if not is_running_in(venv_path):
            mfaalog.info(f"当前在外部虚拟环境中运行（{sys.prefix}），依赖由使用者自行管理。")
            return
        # 解释器已经对了，只是不用再建环境、切进程；依赖照查。
        # interface.json 的 child_exec 直接指向 .venv，常态启动走的就是这里。
        sync_requirements(project_root, venv_path)
        sync_maafw()
        return

    venv_python = get_venv_executable(venv_path)
    create_venv(venv_path)
    # 依赖由切换到 venv 后的进程自己装，走上面那条路径。

    if sys.platform == "win32":
        # Windows 没有 exec 语义。原先用 subprocess.run + sys.exit(0) 重启，会留下
        # 父进程空转等待，形成「宿主 python(A) → venv python(B)」双层进程：
        #   · 两者在任务管理器里同名，kill 哪个都不确定；
        #   · 杀 A 则 B 变孤儿，继续持有 AgentServer 的 socket 不放手。
        # 故此处不再自动重启，改为硬失败并给出可直接粘贴的配置。
        # 环境已在上面建好（依赖在首次用它启动时补装），开发者改一次启动配置即可。
        # 对照上游 M9A agent/bootstrap.py:56-57，其 relaunch 同样只在 Linux 生效。
        main_py = (project_root / "agent" / "main.py").as_posix()
        mfaalog.error("=" * 64)
        mfaalog.error("[venv] Agent 需要用虚拟环境的解释器启动，当前用的是宿主解释器。")
        mfaalog.error(f"[venv] 虚拟环境已创建: {venv_python}")
        mfaalog.error("[venv] 请把 interface.json 的 agent 段改为:")
        mfaalog.error(f'[venv]     "child_exec": "{venv_python.as_posix()}",')
        mfaalog.error(f'[venv]     "child_args": ["-u", "-X", "utf8=1", "{main_py}"]')
        mfaalog.error("[venv] 或直接用该解释器运行本脚本。")
        mfaalog.error("=" * 64)
        raise SystemExit(1)
    else:
        mfaalog.info(f"正在切换到虚拟环境: {venv_python}")
        mfaalog.info(">>> 重启 Agent 进程 >>>")
        args = [str(venv_python), sys.argv[0]] + sys.argv[1:]
        os.execv(str(venv_python), args)