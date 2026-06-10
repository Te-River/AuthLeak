import os, re, time, json, stat, platform, subprocess, random, threading
from queue import Queue
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import parse_qs
import configparser
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests
from requests.adapters import HTTPAdapter
from loguru import logger
from tqdm import tqdm
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import cryptocode

TITLE_VER = "1.50"
GAME_ID = "SDGB"
DOWNLOAD_SPEED_LIMIT_SINGLE = 10 * 1024 * 1024
DOWNLOAD_SPEED_LIMIT_MULTI = 5 * 1024 * 1024
REQUEST_TIMEOUT = 30
CHUNK_BLOCK_SIZE = 1 * 1024 * 1024
MAX_CONSECUTIVE_567 = 3
LITE_AUTH_KEY = bytes([47,63,106,111,43,34,76,38,92,67,114,57,40,61,107,71])
LITE_AUTH_IV = bytes.fromhex("00000000000000000000000000000000")
DELIVERY_HEADERS = {
    "User-Agent": "SDGB;Windows/Lite",
    "Pragma": "DFI",
    "Accept": "*/*",
    "Accept-Language": "zh-CN",
    "Accept-Encoding": "identity",
    "Connection": "Keep-Alive",
}
CABINET_HEADERS = {
    "User-Agent": "SDGB;Windows/Lite",
    "Pragma": "DFI",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}
SYSTEM = platform.system().lower()
MACHINE = platform.machine().lower()

def get_fsdecrypt_path() -> Path:
    tool_dir = Path("fsdecrypt")
    if SYSTEM == "windows":
        candidates = [tool_dir / "fsdecrypt.exe", Path("fsdecrypt.exe")]
        for path in candidates:
            if path.exists(): return path
        return Path("fsdecrypt.exe")
    else:
        arch_map = {"x86_64":"fsdecrypt_x86_64","amd64":"fsdecrypt_x86_64","aarch64":"fsdecrypt_arm64","arm64":"fsdecrypt_arm64","armv7l":"fsdecrypt_arm32"}
        filename = arch_map.get(MACHINE, "fsdecrypt")
        candidates = [tool_dir / filename, Path(filename)]
        for path in candidates:
            if path.exists(): return path
        logger.warning(f"未找到架构 {MACHINE} 的解密工具，尝试使用默认 fsdecrypt")
        return Path("fsdecrypt")

FSDECRYPT_EXE = get_fsdecrypt_path()

if SYSTEM != "windows" and FSDECRYPT_EXE.exists():
    try:
        st = FSDECRYPT_EXE.stat().st_mode
        if not (st & stat.S_IXUSR):
            logger.info(f"为 {FSDECRYPT_EXE} 添加可执行权限")
            FSDECRYPT_EXE.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        logger.warning(f"无法修改权限: {e}")

ENCRYPTED_KEY = "TNR1ip96qwkUBVg=*U0t8/L0fFrCK5Q/qYm3l7Q==*OSOEcXkWT3dFzvBMhTJiAA==*XG+KrOA43KJHEghrv0wWNA=="

class NoCompressionAdapter(HTTPAdapter):
    def add_headers(self, request, **kwargs): pass

def run_fsdecrypt(opt_file: Path, version: str) -> bool:
    if not FSDECRYPT_EXE.exists():
        logger.error(f"未找到解密工具: {FSDECRYPT_EXE}")
        return False
    if not opt_file.exists():
        logger.error(f"文件不存在: {opt_file}")
        return False
    opt_dir = opt_file.parent
    target_dir = opt_dir / version
    if target_dir.exists() and target_dir.is_dir():
        logger.info(f"目标目录 {target_dir} 已存在，跳过解密")
        return True
    logger.info(f"正在调用解密工具处理: {opt_file.name} (工具: {FSDECRYPT_EXE})")
    exe_path = str(FSDECRYPT_EXE.resolve())
    opt_path = str(opt_file.resolve())
    original_cwd = os.getcwd()
    os.chdir(opt_dir)
    try:
        process = subprocess.Popen([exe_path, opt_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    except Exception as e:
        logger.error(f"启动解密工具失败: {e}")
        os.chdir(original_cwd)
        return False
    for line in process.stdout: print(line, end='')
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
    decrypted = cryptocode.decrypt(ENCRYPTED_KEY, "AuthLeakSaltKey2026")
    if not decrypted:
        raise ValueError("解密失败")
    return [decrypted]

def extract_version_from_desc(desc: str, filename: str) -> str:
    match = re.search(r'_(A\d{3})$', desc)
    if match: return match.group(1)
    match = re.search(r'PATCH_.*_(.+)$', desc)
    if match: return f"patch_{match.group(1)}"
    match = re.search(r'_(A\d{3})_', filename)
    if match: return match.group(1)
    match = re.search(r'_(patch_\d+\.\d+)_', filename)
    if match: return match.group(1)
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def auth_lite_encrypt(plaintext: str) -> bytes:
    content = bytes(32) + plaintext.encode("utf-8")
    padded = pad(content, AES.block_size)
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    return cipher.encrypt(padded)

def auth_lite_decrypt(ciphertext: bytes) -> str:
    cipher = AES.new(LITE_AUTH_KEY, AES.MODE_CBC, LITE_AUTH_IV)
    decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return decrypted[16:].decode("utf-8").strip()

def create_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    return session

def get_raw_delivery(serial: str) -> str:
    encrypted = auth_lite_encrypt(f"title_id={GAME_ID}&title_ver={TITLE_VER}&client_id={serial}")
    session = create_session()
    session.mount("http://", NoCompressionAdapter())
    resp = session.post("http://at.sys-allnet.cn/net/delivery/instruction", data=encrypted, headers=DELIVERY_HEADERS, timeout=REQUEST_TIMEOUT)
    decrypted = auth_lite_decrypt(resp.content)
    return "".join(c for c in decrypted if 31 < ord(c) < 127)

def parse_raw_delivery(delivery_str: str) -> List[str]:
    parsed = {k: v[0] for k, v in parse_qs(delivery_str).items()}
    if parsed.get("result") != "1": return []
    uri_str = parsed.get("uri", "")
    urls = [url for url in uri_str.split("|") if url and url != "null"]
    return [url for url in urls if url.startswith("https://") and url.endswith(".txt")]

def get_update_ini(url: str) -> str:
    session = create_session()
    session.mount("https://", NoCompressionAdapter())
    resp = session.get(url, headers=DELIVERY_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text

def parse_update_ini(ini_text: str) -> Optional[Dict]:
    if not ini_text: return None
    if ini_text.startswith('\ufeff'): ini_text = ini_text[1:]
    config = configparser.ConfigParser(allow_no_value=True)
    config.read_string(ini_text)
    if "COMMON" not in config: return None
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

def _create_567_error(resp):
    err = requests.exceptions.HTTPError("CDN node timeout (567)")
    err.response = resp
    return err

def _download_single_chunk(session, url, part_path, resume_pos, total_size, timeout_seconds, speed_limit, desc):
    headers = CABINET_HEADERS.copy()
    if resume_pos > 0: headers["Range"] = f"bytes={resume_pos}-"
    try:
        resp = session.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 567: raise _create_567_error(resp)
        if resume_pos > 0 and resp.status_code not in (200,206): resp.raise_for_status()
        elif resume_pos == 0: resp.raise_for_status()
        mode = "ab" if resume_pos > 0 else "wb"
        start_time = time.time()
        bytes_downloaded = 0
        with tqdm(total=total_size, initial=resume_pos, unit='B', unit_scale=True, unit_divisor=1024, desc=desc, leave=False) as pbar:
            with open(part_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        f.write(chunk)
                        chunk_len = len(chunk)
                        bytes_downloaded += chunk_len
                        pbar.update(chunk_len)
                        if speed_limit > 0: time.sleep(chunk_len / speed_limit)
                    if time.time() - start_time > timeout_seconds:
                        current_size = part_path.stat().st_size
                        if total_size and current_size == total_size: return True, bytes_downloaded
                        else: return False, bytes_downloaded
        current_size = part_path.stat().st_size
        return (total_size is not None and current_size == total_size), bytes_downloaded
    except requests.exceptions.HTTPError:
        raise
    except Exception as e:
        logger.debug(f"单线程下载块异常: {e}")
        return False, 0

def _download_multi_segments(session, url, part_path, start_offset, total_size, desc):
    remaining = total_size - start_offset
    if remaining <= 0: return
    blocks = []
    pos = start_offset
    while pos < total_size:
        end = min(pos + CHUNK_BLOCK_SIZE - 1, total_size - 1)
        blocks.append((pos, end))
        pos = end + 1
    logger.info(f"剩余 {remaining} 字节，分为 {len(blocks)} 块，启用多线程")
    initial_threads = 3
    extra_threads = 2
    queue = Queue()
    for blk in blocks: queue.put(blk)
    lock = threading.Lock()
    pbar = tqdm(total=total_size, initial=start_offset, unit='B', unit_scale=True, unit_divisor=1024, desc=desc, leave=False)
    def worker():
        while not queue.empty():
            blk_start, blk_end = queue.get()
            if blk_start > blk_end:
                queue.task_done()
                continue
            headers = CABINET_HEADERS.copy()
            headers["Range"] = f"bytes={blk_start}-{blk_end}"
            try:
                resp = session.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 567: raise _create_567_error(resp)
                resp.raise_for_status()
                with open(part_path, "r+b") as f:
                    f.seek(blk_start)
                    for chunk in resp.iter_content(chunk_size=4096):
                        if chunk:
                            f.write(chunk)
                            with lock: pbar.update(len(chunk))
                            if DOWNLOAD_SPEED_LIMIT_MULTI > 0: time.sleep(len(chunk) / DOWNLOAD_SPEED_LIMIT_MULTI)
            except Exception as e:
                logger.debug(f"块 {blk_start}-{blk_end} 下载失败: {e}")
                queue.put((blk_start, blk_end))
            finally:
                queue.task_done()
    threads = []
    for _ in range(initial_threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    time.sleep(random.uniform(5.0, 7.0))
    if not queue.empty():
        logger.info("📦 下载仍在进行，追加线程加速")
        for _ in range(extra_threads):
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            threads.append(t)
    for t in threads: t.join()
    pbar.close()
    if part_path.stat().st_size != total_size:
        raise IOError(f"多线程下载后文件大小不匹配，预期 {total_size}，实际 {part_path.stat().st_size}")

def download_file_cabinet(url: str, save_path: Path, max_retries: int = 20) -> bool:
    part_path = save_path.with_suffix(save_path.suffix + ".part")
    session = create_session()
    session.mount("https://", NoCompressionAdapter())
    total_size = None
    consecutive_567 = 0
    try:
        head_resp = session.head(url, headers=CABINET_HEADERS, timeout=REQUEST_TIMEOUT)
        if head_resp.status_code == 567:
            logger.warning("HEAD请求收到567错误，将忽略文件大小检查")
            consecutive_567 += 1
        elif "Content-Length" in head_resp.headers:
            total_size = int(head_resp.headers["Content-Length"])
    except Exception: pass
    if save_path.exists() and (total_size is None or save_path.stat().st_size == total_size):
        logger.info(f"文件已完整存在: {save_path.name}")
        return True
    for attempt in range(1, max_retries+1):
        if consecutive_567 >= MAX_CONSECUTIVE_567:
            logger.error("连续收到CDN拦截，建议更换IP或稍后重试")
            return False
        resume_pos = part_path.stat().st_size if part_path.exists() else 0
        if total_size is not None and resume_pos > total_size:
            logger.warning(f"本地文件({resume_pos}字节)大于远程文件({total_size}字节)，重置下载")
            part_path.unlink(missing_ok=True)
            resume_pos = 0
        remaining = total_size - resume_pos if total_size else float('inf')
        logger.info(f"下载尝试 {attempt}/{max_retries}，已下载 {resume_pos} 字节，剩余 {remaining if remaining != float('inf') else '未知'}")
        if total_size is not None and resume_pos == total_size:
            logger.success(f"文件已完整，无需下载: {save_path.name}")
            part_path.rename(save_path)
            return True
        try:
            if total_size is None or remaining < 10*1024*1024:
                logger.info("采用纯单线程下载模式")
                completed, _ = _download_single_chunk(session, url, part_path, resume_pos, total_size, float('inf'), DOWNLOAD_SPEED_LIMIT_SINGLE, save_path.name)
                if completed:
                    part_path.rename(save_path)
                    logger.success(f"单线程下载完成: {save_path.name}")
                    return True
                continue
            timeout = random.uniform(5.0, 7.0)
            logger.info(f"先尝试单线程下载，超时阈值 {timeout:.1f} 秒...")
            completed, _ = _download_single_chunk(session, url, part_path, resume_pos, total_size, timeout, DOWNLOAD_SPEED_LIMIT_SINGLE, save_path.name)
            if completed:
                part_path.rename(save_path)
                logger.success(f"单线程下载完成: {save_path.name}")
                return True
            current_size = part_path.stat().st_size
            if current_size == total_size:
                part_path.rename(save_path)
                logger.success(f"下载完成: {save_path.name}")
                return True
            _download_multi_segments(session, url, part_path, current_size, total_size, save_path.name)
            part_path.rename(save_path)
            logger.success(f"下载完成: {save_path.name}")
            return True
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            if status_code == 567:
                consecutive_567 += 1
                logger.warning(f"CDN节点响应超时 (567)，已连续出现 {consecutive_567} 次")
                if consecutive_567 >= MAX_CONSECUTIVE_567: return False
            elif status_code == 416:
                logger.warning("Range请求不满足 (416)，重置下载")
                part_path.unlink(missing_ok=True)
                consecutive_567 = 0
            else:
                logger.warning(f"HTTP错误 {status_code}: {e}")
                consecutive_567 = 0
        except Exception as e:
            logger.warning(f"下载失败: {e}")
            consecutive_567 = 0
        time.sleep(2 + random.uniform(0,2))
    logger.error("达到最大重试次数")
    return False

def parse_order_time(time_str: str) -> Optional[datetime]:
    if not time_str: return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try: return datetime.strptime(time_str, fmt)
        except ValueError: continue
    return None

def check_and_download_opt(main_info: dict, opt_dir: Path, order_time_str: str) -> Optional[Path]:
    if not main_info or not main_info.get("下载地址"): return None
    order_time = parse_order_time(order_time_str)
    filename = main_info["文件名"]
    url = main_info["下载地址"]
    file_path = opt_dir / filename
    if order_time:
        now = datetime.now()
        effective_time = order_time - timedelta(hours=24)
        if now < effective_time:
            wait_seconds = (effective_time - now).total_seconds()
            wait_days = int(wait_seconds // 86400)
            wait_hours = int((wait_seconds % 86400) // 3600)
            logger.warning(f"当前时间早于提前下载时间 {effective_time.strftime('%Y-%m-%d %H:%M:%S')}")
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
        logger.error("下载失败，可能是CDN拦截或网络问题")
        return None

def print_update_info(info: dict):
    if not info: return
    desc = info.get('更新描述','')
    if desc.startswith('OPTION'): update_type = "Opt"
    elif desc.startswith('PATCH'): update_type = "App"
    else: update_type = "未知"
    print("\n" + "="*60)
    print(f"游戏: {info.get('游戏ID','N/A')} - {desc} ({update_type})")
    print("="*60)
    print(f"允许下载时间: {info.get('允许下载时间','N/A')}")
    print(f"实际应用时间: {info.get('实际应用时间','N/A')}")
    print("-"*60)
    main = info.get('主更新包')
    if main and main.get('下载地址'):
        print("主更新包:")
        print(f"   文件名: {main['文件名']}")
        print(f"   链接:   {main['下载地址']}")
    else: print("未找到主更新包")
    optionals = info.get('历史可选更新包', [])
    if optionals:
        print(f"\n历史可选更新包 (共 {len(optionals)} 个):")
        for i, opt in enumerate(optionals, 1):
            print(f"   {i:2}. {opt['文件名']}")
    print("="*60 + "\n")

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
            if all_infos: break
        except Exception as e:
            logger.error(f"请求异常: {e}")
    
    # ========== 核心改动开始 ==========
    if not all_infos:
        logger.warning("未获取到任何有效更新信息，尝试解密本地最新更新包")
        # 查找 Opt 文件夹下所有 .app 文件，按修改时间降序取最新
        app_files = sorted(
            opt_dir.glob("*.app"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not app_files:
            logger.error("Opt 文件夹中没有 .app 文件，无法进行离线解密")
            return
        latest_app = app_files[0]
        logger.info(f"找到本地最新更新包: {latest_app.name}")
        # 提取版本标识（desc 传空，让函数根据文件名匹配）
        version = extract_version_from_desc("", latest_app.name)
        logger.info(f"版本标识: {version}")
        # 构造一个虚拟的更新信息，用于 summary 记录
        dummy_info = {
            "游戏ID": GAME_ID,
            "更新描述": f"离线解密 - {latest_app.name}",
            "允许下载时间": "",
            "实际应用时间": "",
            "主更新包": {"文件名": latest_app.name, "下载地址": ""},
            "历史可选更新包": [],
        }
        all_infos.append(dummy_info)  # 后续的 summary 可正常记录
        if run_fsdecrypt(latest_app, version):
            target_dir = opt_dir / version
            logger.success(f"✅ 离线解密成功: {target_dir}")
            processed_versions.append(version)
        else:
            logger.error(f"❌ 离线解密失败: {latest_app.name}")
        # 不再直接 return，继续执行到后面的 summary 保存逻辑
    # ========== 核心改动结束 ==========
    else:
        print("\n" + "更新信息汇总".center(60,"="))
        for info in all_infos: print_update_info(info)
        processed_versions = []
        for info in all_infos:
            main_pkg = info.get("主更新包")
            if not main_pkg: continue
            opt_path = check_and_download_opt(main_pkg, opt_dir, info["允许下载时间"])
            if not opt_path or not opt_path.exists(): continue
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