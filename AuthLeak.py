#!/usr/bin/env python3
"""
游戏更新助手
用法: uv run python script.py
功能: 获取更新信息、下载增量包、解密提取资源
"""

import os
import re
import shutil
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from urllib.parse import parse_qs
import configparser

import requests
from requests.adapters import HTTPAdapter
from loguru import logger

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

import cryptocode

# ---------- 基本配置 ----------
TITLE_VER = "1.53"
GAME_ID = "SDGB"

# AES 密钥与 IV
LITE_AUTH_KEY = bytes([47, 63, 106, 111, 43, 34, 76, 38, 92, 67, 114, 57, 40, 61, 107, 71])
LITE_AUTH_IV = bytes.fromhex("00000000000000000000000000000000")

# 请求头：获取指示书
DELIVERY_HEADERS = {
    "User-Agent": "SDGB;Windows/Lite",
    "Pragma": "DFI",
    "Accept": "*/*",
    "Accept-Language": "zh-CN",
    "Accept-Encoding": "identity",
    "Connection": "Keep-Alive",
}

# 请求头：下载更新文件
CABINET_HEADERS = {
    "User-Agent": "SDGB;Windows/Lite",
    "Pragma": "DFI",
    "Accept-Encoding": "identity",
    "Connection": "Keep-Alive",
}

# 解密工具
FSDECRYPT_EXE = Path("fsdecrypt.exe")

ENCRYPTED_KEY = "TNR1ip96qwkUBVg=*U0t8/L0fFrCK5Q/qYm3l7Q==*OSOEcXkWT3dFzvBMhTJiAA==*XG+KrOA43KJHEghrv0wWNA=="

# ---------- 辅助类 ----------
class NoCompressionAdapter(HTTPAdapter):
    """禁用自动解压缩的适配器"""
    def add_headers(self, request, **kwargs):
        pass


# ---------- 解密与提取 ----------
def run_fsdecrypt(opt_file: Path, version: str) -> bool:
    """调用解密工具处理 opt 文件，并将输出目录重命名为指定版本号"""
    if not FSDECRYPT_EXE.exists():
        logger.error("未找到解密工具")
        return False
    if not opt_file.exists():
        logger.error(f"文件不存在: {opt_file}")
        return False

    opt_dir = opt_file.parent
    target_dir = opt_dir / version

    # 如果目标目录已存在，直接跳过解密
    if target_dir.exists() and target_dir.is_dir():
        logger.info(f"目标目录 {target_dir} 已存在，跳过解密")
        return True

    logger.info(f"正在调用解密工具处理: {opt_file.name}")

    exe_path = str(FSDECRYPT_EXE.resolve())
    opt_path = str(opt_file.resolve())

    original_cwd = os.getcwd()
    os.chdir(opt_dir)

    try:
        process = subprocess.Popen(
            [exe_path, opt_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
    except Exception as e:
        logger.error(f"启动解密工具失败: {e}")
        os.chdir(original_cwd)
        return False

    for line in process.stdout:
        print(line, end='')
    process.wait()

    os.chdir(original_cwd)

    if process.returncode != 0:
        logger.error(f"解密工具执行失败，返回码 {process.returncode}")
        return False

    default_output_dir = opt_file.with_suffix('')

    if default_output_dir.exists() and default_output_dir.is_dir():
        logger.info(f"解密工具生成目录: {default_output_dir}")
        if target_dir.exists():
            logger.warning(f"目标目录 {target_dir} 在解密期间被创建，将保留原有目录，新解密内容位于 {default_output_dir}")
            return True
        default_output_dir.rename(target_dir)
        logger.success(f"已重命名为: {target_dir}")
        return True
    else:
        logger.error("解密工具未生成预期的输出目录")
        return False
    

def get_serials() -> List[str]:
    """返回解密后的列表"""
    decrypted = cryptocode.decrypt(ENCRYPTED_KEY, "AuthLeakSaltKey2026")
    if not decrypted:
        raise ValueError("解密失败")
    return [decrypted]


def extract_version_from_desc(desc: str, filename: str) -> str:
    """从更新描述或文件名中提取版本标识"""
    match = re.search(r'_(A\d{3})$', desc)
    if match:
        return match.group(1)
    match = re.search(r'PATCH_.*_(.+)$', desc)
    if match:
        return f"patch_{match.group(1)}"
    match = re.search(r'_(A\d{3})_', filename)
    if match:
        return match.group(1)
    match = re.search(r'_(patch_\d+\.\d+)_', filename)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------- AES 通信加解密 ----------
def auth_lite_encrypt(plaintext: str) -> bytes:
    """AES-256-CBC 加密，用于向服务器发送请求"""
    content = bytes(32) + plaintext.encode("utf-8")
    padded = pad(content, AES.block_size)
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    return cipher.encrypt(padded)


def auth_lite_decrypt(ciphertext: bytes) -> str:
    """AES-256-CBC 解密，用于解析服务器响应"""
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return decrypted[16:].decode("utf-8").strip()


# ---------- 网络请求 ----------
def get_raw_delivery(serial: str) -> str:
    """向服务器发送请求，获取更新指示书的原始响应"""
    encrypted = auth_lite_encrypt(f"title_id={GAME_ID}&title_ver={TITLE_VER}&client_id={serial}")
    session = requests.Session()
    session.mount("http://", NoCompressionAdapter())
    resp = session.post(
        "http://at.sys-allnet.cn/net/delivery/instruction",
        data=encrypted,
        headers=DELIVERY_HEADERS,
        timeout=30,
    )
    decrypted = auth_lite_decrypt(resp.content)
    return "".join(c for c in decrypted if 31 < ord(c) < 127)


def parse_raw_delivery(delivery_str: str) -> List[str]:
    """解析服务器返回的原始字符串，提取有效的指示书URL"""
    parsed = {k: v[0] for k, v in parse_qs(delivery_str).items()}
    if parsed.get("result") != "1":
        return []
    uri_str = parsed.get("uri", "")
    urls = [url for url in uri_str.split("|") if url and url != "null"]
    return [url for url in urls if url.startswith("https://") and url.endswith(".txt")]


def get_update_ini(url: str) -> str:
    """下载指示书内容（INI格式）"""
    session = requests.Session()
    session.mount("https://", NoCompressionAdapter())
    resp = session.get(url, headers=DELIVERY_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_update_ini(ini_text: str) -> Optional[Dict]:
    """解析 INI 格式的指示书，提取更新包信息"""
    if not ini_text:
        return None
    if ini_text.startswith('\ufeff'):
        ini_text = ini_text[1:]
    config = configparser.ConfigParser(allow_no_value=True)
    config.read_string(ini_text)
    if "COMMON" not in config:
        return None
    common = config["COMMON"]
    main_url = common.get("INSTALL1", "")
    main_filename = main_url.split('/')[-1] if main_url else ""
    optional_files = []
    if "OPTIONAL" in config:
        for key, url in config.items("OPTIONAL"):
            if key.startswith("install") and url:
                optional_files.append({"文件名": url.split('/')[-1], "下载地址": url})
    return {
        "游戏ID": common.get("GAME_ID", ""),
        "更新描述": common.get("GAME_DESC", "").strip('"'),
        "允许下载时间": common.get("ORDER_TIME", ""),
        "实际应用时间": common.get("RELEASE_TIME", ""),
        "主更新包": {"文件名": main_filename, "下载地址": main_url} if main_url else None,
        "历史可选更新包": optional_files,
    }


def download_file_cabinet(url: str, save_path: Path, max_retries: int = 3) -> bool:
    """使用原生请求头下载文件，支持断点续传"""
    part_path = save_path.with_suffix(save_path.suffix + ".part")
    session = requests.Session()
    session.mount("https://", NoCompressionAdapter())

    resume_pos = 0
    if part_path.exists():
        resume_pos = part_path.stat().st_size
        logger.info(f"续传，已下载 {resume_pos} 字节")

    headers = CABINET_HEADERS.copy()
    if resume_pos > 0:
        headers["Range"] = f"bytes={resume_pos}-"

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"下载尝试 {attempt}/{max_retries}...")
            resp = session.get(url, headers=headers, stream=True, timeout=30)

            if resume_pos > 0:
                if resp.status_code == 206:
                    logger.info("服务器支持续传")
                elif resp.status_code == 200:
                    logger.warning("服务器忽略 Range，重新下载")
                    resume_pos = 0
                    part_path.unlink(missing_ok=True)
                else:
                    resp.raise_for_status()
            else:
                resp.raise_for_status()

            mode = "ab" if resume_pos > 0 and resp.status_code == 206 else "wb"
            with open(part_path, mode) as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)

            part_path.rename(save_path)
            logger.success(f"下载完成: {save_path.name}")
            return True

        except Exception as e:
            logger.warning(f"下载失败: {e}")
            if attempt == max_retries:
                logger.error("达到最大重试次数")
                return False
            time.sleep(2)

    return False


def parse_order_time(time_str: str) -> Optional[datetime]:
    """解析时间字段，兼容多种格式"""
    if not time_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    return None


def check_and_download_opt(main_info: dict, opt_dir: Path, order_time_str: str) -> Optional[Path]:
    """检查时间并下载主更新包"""
    if not main_info or not main_info.get("下载地址"):
        return None

    order_time = parse_order_time(order_time_str)
    filename = main_info["文件名"]
    url = main_info["下载地址"]
    file_path = opt_dir / filename

    if order_time:
        now = datetime.now()
        if now < order_time:
            wait_seconds = (order_time - now).total_seconds()
            wait_days = int(wait_seconds // 86400)
            wait_hours = int((wait_seconds % 86400) // 3600)
            logger.warning(f"当前时间早于允许下载时间 {order_time_str}")
            logger.info(f"还需等待约 {wait_days} 天 {wait_hours} 小时")
            if file_path.exists():
                logger.info(f"但本地已存在文件: {filename}")
                return file_path
            else:
                logger.info("本地也没有文件，跳过下载")
                return None

    if file_path.exists():
        logger.info(f"文件已存在，跳过下载: {filename}")
        return file_path

    logger.info(f"开始下载: {filename}")
    if download_file_cabinet(url, file_path):
        return file_path
    else:
        logger.error("下载失败")
        return None


# ---------- 信息展示 ----------
def print_update_info(info: dict):
    """打印更新信息摘要"""
    if not info:
        return

    desc = info.get('更新描述', '')
    if desc.startswith('OPTION'):
        update_type = "Opt"
    elif desc.startswith('PATCH'):
        update_type = "App"
    else:
        update_type = "未知"

    print("\n" + "=" * 60)
    print(f"游戏: {info.get('游戏ID', 'N/A')} - {desc} ({update_type})")
    print("=" * 60)
    print(f"允许下载时间: {info.get('允许下载时间', 'N/A')}")
    print(f"实际应用时间: {info.get('实际应用时间', 'N/A')}")
    print("-" * 60)
    main = info.get('主更新包')
    if main and main.get('下载地址'):
        print("主更新包:")
        print(f"   文件名: {main['文件名']}")
        print(f"   链接:   {main['下载地址']}")
    else:
        print("未找到主更新包")
    optionals = info.get('历史可选更新包', [])
    if optionals:
        print(f"\n历史可选更新包 (共 {len(optionals)} 个):")
        for i, opt in enumerate(optionals, 1):
            print(f"   {i:2}. {opt['文件名']}")
    print("=" * 60 + "\n")


# ---------- 主程序 ----------
def main():
    script_dir = Path(__file__).parent
    os.chdir(script_dir)

    opt_dir = script_dir / "Opt"
    opt_dir.mkdir(exist_ok=True)
    log_dir = script_dir / "Log"
    log_dir.mkdir(exist_ok=True)

    all_infos: List[Dict] = []
    for serial in get_serials():
        logger.info("正在验证设备授权...")
        try:
            raw = get_raw_delivery(serial)
            urls = parse_raw_delivery(raw)
            if not urls:
                logger.warning("未解析到任何指示书 URL")
                continue

            for url in urls:
                logger.info(f"获取指示书: {url.split('/')[-1]}")
                text = get_update_ini(url)
                if text:
                    info = parse_update_ini(text)
                    if info and info.get("主更新包"):
                        all_infos.append(info)
            if all_infos:
                break
        except Exception as e:
            logger.error(f"请求异常: {e}")

    if not all_infos:
        logger.error("未获取到任何有效更新信息")
        return

    print("\n" + "更新信息汇总".center(60, "="))
    for info in all_infos:
        print_update_info(info)

    processed_versions = []
    for info in all_infos:
        main_pkg = info.get("主更新包")
        if not main_pkg:
            continue

        opt_path = check_and_download_opt(main_pkg, opt_dir, info["允许下载时间"])
        if not opt_path or not opt_path.exists():
            continue

        version = extract_version_from_desc(info["更新描述"], opt_path.name)
        logger.info(f"版本标识: {version}")

        if run_fsdecrypt(opt_path, version):
            target_dir = opt_dir / version
            if target_dir.exists():
                logger.success(f"✅ {version} 可用: {target_dir}")
            else:
                logger.success(f"✅ {version} 提取成功: {target_dir}")
            processed_versions.append(version)
        else:
            logger.error(f"❌ {version} 解密失败")

    summary = {
        "获取时间": datetime.now().isoformat(),
        "处理的更新包": processed_versions,
        "指示书详情": all_infos,
    }

    existing_logs = sorted(log_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    should_save = True
    if existing_logs:
        latest_log = existing_logs[0]
        try:
            with open(latest_log, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_infos = old.get("指示书详情", [])
            if old_infos == all_infos:
                logger.info("更新信息未变化，跳过保存")
                should_save = False
        except Exception as e:
            logger.warning(f"读取旧日志失败，将保存新文件: {e}")

    if should_save:
        now = datetime.now()
        filename = now.strftime("%Y-%m-%d-%Hh%Mm.json")
        json_path = log_dir / filename
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.success(f"更新信息已保存: {json_path}")

    if not processed_versions:
        logger.info("本次未处理任何更新包（可能时间未到或文件缺失）")


if __name__ == "__main__":
    main()