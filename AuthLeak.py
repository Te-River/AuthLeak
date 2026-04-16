#!/usr/bin/env python3
"""
maimai DX 国服更新助手 - 最终稳定版
功能：
1. 获取更新指示书，解析主更新包 URL
2. 检查 ORDER_TIME，未到时间则跳过下载
3. 断点续传下载 .opt 增量文件
4. 自动识别版本号（如 A071），调用 fsdecrypt 解密并重命名输出目录
5. 保存更新信息到 Log 目录，自动去重
6. 若文件不存在且未到时间，优雅退出不报错
"""

import os
import re
import shutil
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
from urllib.parse import parse_qs
import configparser

import requests
from requests.adapters import HTTPAdapter
from loguru import logger

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ---------- 配置 ----------
TITLE_VER = "1.53"
GAME_ID = "SDGB"

JIANGSU_SERIALS = [
    "A63E01C2598",
]

LITE_AUTH_KEY = bytes([47, 63, 106, 111, 43, 34, 76, 38, 92, 67, 114, 57, 40, 61, 107, 71])
LITE_AUTH_IV = bytes.fromhex("00000000000000000000000000000000")

DELIVERY_HEADERS = {
    "User-Agent": "SDGB;Windows/Lite",
    "Pragma": "DFI",
    "Accept": "*/*",
    "Accept-Language": "zh-CN",
    "Accept-Encoding": "identity",
    "Connection": "Keep-Alive",
}

EDGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

FSDECRYPT_EXE = Path("fsdecrypt.exe")


class NoCompressionAdapter(HTTPAdapter):
    def add_headers(self, request, **kwargs):
        pass


# ---------- 辅助函数 ----------
def run_fsdecrypt(opt_file: Path, version: str) -> bool:
    """
    调用 fsdecrypt.exe 解密 opt 文件，将其生成的同名目录重命名为版本号。
    """
    if not FSDECRYPT_EXE.exists():
        logger.error("未找到 fsdecrypt.exe，请将其放在脚本同目录下")
        return False

    if not opt_file.exists():
        logger.error(f"OPT 文件不存在: {opt_file}")
        return False

    logger.info(f"正在调用 fsdecrypt 解密: {opt_file.name}")

    # 获取绝对路径
    exe_path = str(FSDECRYPT_EXE.resolve())
    opt_path = str(opt_file.resolve())
    opt_dir = opt_file.parent

    # 在 opt 文件所在目录执行 fsdecrypt
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
        logger.error(f"启动 fsdecrypt 失败: {e}")
        os.chdir(original_cwd)
        return False

    # 实时读取并打印输出
    for line in process.stdout:
        print(line, end='')
    process.wait()

    os.chdir(original_cwd)

    if process.returncode != 0:
        logger.error(f"fsdecrypt 执行失败，返回码 {process.returncode}")
        return False

    # fsdecrypt 默认生成一个与 opt 文件同名的文件夹（不含扩展名）
    default_output_dir = opt_file.with_suffix('')
    target_dir = opt_dir / version

    if default_output_dir.exists() and default_output_dir.is_dir():
        logger.info(f"fsdecrypt 生成目录: {default_output_dir}")
        if target_dir.exists():
            logger.warning(f"目标目录 {target_dir} 已存在，将被覆盖")
            shutil.rmtree(target_dir)
        default_output_dir.rename(target_dir)
        logger.success(f"已重命名为: {target_dir}")
        return True
    else:
        logger.error("fsdecrypt 未生成预期的输出目录")
        return False


def extract_version_from_filename(filename: str) -> str:
    """
    从 OPT 文件名中提取版本号，如 SDGB_A071_20260408123929_0.opt -> A071
    """
    match = re.search(r'_(A\d{3})_', filename)
    if match:
        return match.group(1)
    logger.warning("无法从文件名提取版本号，使用时间戳")
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------- AES 加解密（指示书）----------
def auth_lite_encrypt(plaintext: str) -> bytes:
    content = bytes(32) + plaintext.encode("utf-8")
    padded = pad(content, AES.block_size)
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    return cipher.encrypt(padded)


def auth_lite_decrypt(ciphertext: bytes) -> str:
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return decrypted[16:].decode("utf-8").strip()


# ---------- 获取更新指示书 ----------
def get_raw_delivery(serial: str) -> str:
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


def parse_raw_delivery(delivery_str: str):
    parsed = {k: v[0] for k, v in parse_qs(delivery_str).items()}
    if parsed.get("result") != "1":
        return []
    uri_str = parsed.get("uri", "")
    urls = [url for url in uri_str.split("|") if url and url != "null"]
    return [url for url in urls if url.startswith("https://") and url.endswith(".txt")]


def get_update_ini(url: str) -> str:
    session = requests.Session()
    session.mount("https://", NoCompressionAdapter())
    resp = session.get(url, headers=DELIVERY_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_update_ini(ini_text: str):
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


# ---------- 下载 .opt ----------
def download_file_edge(url: str, save_path: Path, max_retries: int = 3) -> bool:
    part_path = save_path.with_suffix(save_path.suffix + ".part")
    session = requests.Session()
    session.mount("https://", NoCompressionAdapter())

    resume_pos = 0
    if part_path.exists():
        resume_pos = part_path.stat().st_size
        logger.info(f"续传，已下载 {resume_pos} 字节")

    headers = EDGE_HEADERS.copy()
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
    if not time_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    return None


def check_and_download_opt(main_info: dict, opt_dir: Path, order_time_str: str) -> Optional[Path]:
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
            # 即使未到时间，如果本地已有文件，也返回文件路径
            if file_path.exists():
                logger.info(f"但本地已存在文件: {filename}")
                return file_path
            else:
                logger.info("本地也没有文件，跳过下载")
                return None

    # 时间已到或无法解析时间
    if file_path.exists():
        logger.info(f"文件已存在，跳过下载: {filename}")
        return file_path

    logger.info(f"开始下载: {filename}")
    if download_file_edge(url, file_path):
        return file_path
    else:
        logger.error("下载失败")
        return None


# ---------- 信息展示 ----------
def print_update_info(info: dict):
    if not info:
        return
    print("\n" + "=" * 60)
    print("🎮 舞萌 DX 更新信息")
    print("=" * 60)
    print(f"游戏标识:     {info.get('游戏ID', 'N/A')}")
    print(f"更新描述:     {info.get('更新描述', 'N/A')}")
    print(f"允许下载时间: {info.get('允许下载时间', 'N/A')}")
    print(f"实际应用时间: {info.get('实际应用时间', 'N/A')}")
    print("-" * 60)
    main = info.get('主更新包')
    if main and main.get('下载地址'):
        print("📦 主更新包:")
        print(f"   文件名: {main['文件名']}")
        print(f"   链接:   {main['下载地址']}")
    else:
        print("⚠️ 未找到主更新包")
    optionals = info.get('历史可选更新包', [])
    if optionals:
        print(f"\n📁 历史可选更新包 (共 {len(optionals)} 个):")
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

    # 1. 获取更新指示书
    success = False
    final_info = None
    for serial in JIANGSU_SERIALS:
        logger.info(f"尝试序列号: {serial}")
        try:
            raw = get_raw_delivery(serial)
            urls = parse_raw_delivery(raw)
            if not urls:
                continue
            for url in urls:
                logger.info(f"获取指示书: {url.split('/')[-1]}")
                text = get_update_ini(url)
                if text:
                    info = parse_update_ini(text)
                    if info and info.get("主更新包"):
                        logger.success("解析成功")
                        final_info = info
                        success = True
                        break
            if success:
                break
        except Exception as e:
            logger.error(f"序列号 {serial} 异常: {e}")

    if not success or not final_info:
        logger.error("未获取到更新信息")
        return

    print_update_info(final_info)

    main_pkg = final_info.get("主更新包")
    if not main_pkg:
        logger.error("更新信息中缺少主更新包")
        return

    # 2. 获取 OPT 文件（处理时间检查）
    opt_path = check_and_download_opt(main_pkg, opt_dir, final_info["允许下载时间"])

    if not opt_path or not opt_path.exists():
        logger.info("没有可用的 OPT 文件，脚本结束")
        # 仍然保存 JSON 信息（可选）
        should_save = True
        existing_logs = sorted(log_dir.glob("maimai_update_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if existing_logs:
            latest_log = existing_logs[0]
            try:
                with open(latest_log, "r", encoding="utf-8") as f:
                    old_info = json.load(f)
                if old_info == final_info:
                    should_save = False
            except:
                pass
        if should_save:
            json_path = log_dir / f"maimai_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(final_info, f, ensure_ascii=False, indent=2)
            logger.success(f"更新信息已保存: {json_path}")
        return

    # 3. 提取版本号并解密
    version = extract_version_from_filename(opt_path.name)
    logger.info(f"提取到版本号: {version}")

    if run_fsdecrypt(opt_path, version):
        logger.success(f"🎉 成功！更新文件已提取到: {opt_dir / version}")
    else:
        logger.error("解密失败，请检查 fsdecrypt 输出")

    # 4. 保存 JSON 日志（去重）
    existing_logs = sorted(log_dir.glob("maimai_update_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    should_save = True
    if existing_logs:
        latest_log = existing_logs[0]
        try:
            with open(latest_log, "r", encoding="utf-8") as f:
                old_info = json.load(f)
            if old_info == final_info:
                logger.info("更新信息未变化，跳过保存")
                should_save = False
        except Exception as e:
            logger.warning(f"读取旧日志失败: {e}")

    if should_save:
        json_path = log_dir / f"maimai_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(final_info, f, ensure_ascii=False, indent=2)
        logger.success(f"更新信息已保存: {json_path}")


if __name__ == "__main__":
    main()