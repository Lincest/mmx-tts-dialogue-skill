#!/usr/bin/env python3
"""
MiniMax TTS 对话生成器

用法:
    python tts_dialogue.py --input dialogue.txt

    对话文件格式 (dialogue.txt):
        A:第一句
        B:第二句
        A:第三句
        B:第四句

    缓存机制:
        首次运行会调用 MiniMax HTTP API 生成原始音频 (raw/)
        再次运行如果对话内容没变，不会重新调用 API
        只会根据新的 --speed-a / --speed-b 重新生成输出

参数:
    --input, -i          输入文件路径 (对话内容)
    --speed-a, -sa       A 角色语速 (默认 1.0)
    --speed-b, -sb       B 角色语速 (默认 1.0)
    --voice-a, -va       A 角色音色 (默认 Chinese (Mandarin)_Cute_Spirit)
    --voice-b, -vb       B 角色音色 (默认 Chinese (Mandarin)_Gentleman)
    --model, -m          模型 (默认 speech-2.8-hd)
    --output, -o         输出文件名 (默认 dialogue_output)
    --cache-dir          缓存目录 (默认 .tts_cache)
    --force, -f          强制重新生成，忽略缓存
    --api-key            API Key (或设置 MINIMAX_API_KEY 环境变量)
    --request-interval   API 请求间隔秒数 (默认 6.5，适配免费账户 10 RPM)
    --max-retries        限流/临时错误最大重试次数 (默认 6)

示例:
    # 首次生成 (需要 API 调用)
    python tts_dialogue.py --input dialogue.txt -sa 1.2 -sb 0.9

    # 调整速度 (复用缓存，不消耗 API)
    python tts_dialogue.py --input dialogue.txt -sa 1.5 -sb 0.8

    # 强制重新生成
    python tts_dialogue.py --input dialogue.txt -f
"""

import argparse
import os
import sys
import subprocess
import json
import re
import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path

# 默认配置
DEFAULT_CONFIG = {
    "voice_a": "Chinese (Mandarin)_Cute_Spirit",
    "voice_b": "Chinese (Mandarin)_Gentleman",
    "model": "speech-2.8-hd",
    "speed_a": 1.0,
    "speed_b": 1.0,
    "output": "dialogue_output",
    "cache_dir": ".tts_cache",
    "api_base": "https://api.minimaxi.com",
    "sample_rate": 32000,
    "bitrate": 128000,
    "format": "mp3",
    "channel": 1,
    "request_interval": 6.5,
    "max_retries": 6,
}


def get_api_key() -> str:
    if os.getenv("MINIMAX_API_KEY"):
        return os.getenv("MINIMAX_API_KEY")
    return None


def check_mmx_cli():
    try:
        subprocess.run(["mmx", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def read_dialogue_file(file_path: str) -> list:
    """从文件读取对话"""
    dialogues = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^([ABab])\s*[:：]?\s*(.+)', line)
            if match:
                speaker = match.group(1).upper()
                text = match.group(2).strip()
                dialogues.append((speaker, text))
    return dialogues


def compute_content_hash(dialogues: list, model: str, voice_a: str, voice_b: str) -> str:
    """计算会影响原始音频的 hash，用于缓存标识"""
    content = json.dumps({
        "model": model,
        "voice_a": voice_a,
        "voice_b": voice_b,
        "dialogues": dialogues,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def pack_adjacent_dialogues(dialogues: list, enabled: bool = True) -> list:
    """合并连续相同角色的台词，减少 TTS 请求数"""
    if not enabled:
        return [{"speaker": speaker, "text": text, "lines": [(speaker, text)]} for speaker, text in dialogues]

    segments = []
    for speaker, text in dialogues:
        if segments and segments[-1]["speaker"] == speaker:
            segments[-1]["text"] += "\n" + text
            segments[-1]["lines"].append((speaker, text))
        else:
            segments.append({"speaker": speaker, "text": text, "lines": [(speaker, text)]})
    return segments


def should_retry(status_code: int, base_code: int = None) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504} or base_code in {1001, 1002, 1039}


def generate_speech(
    text: str,
    voice: str,
    model: str,
    output_path: str,
    api_key: str,
    api_base: str,
    max_retries: int,
    sample_rate: int,
    bitrate: int,
    audio_format: str,
    channel: int,
) -> dict:
    """调用 MiniMax HTTP T2A v2 生成语音"""
    url = f"{api_base.rstrip('/')}/v1/t2a_v2"
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "format": audio_format,
            "channel": channel,
        },
        "subtitle_enable": False,
        "output_format": "hex",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        try:
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After")
            if attempt < max_retries and should_retry(error.code):
                wait_seconds = float(retry_after) if retry_after else min(60, 2 ** attempt)
                print(f"    -> HTTP {error.code}，{wait_seconds:.1f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait_seconds)
                continue
            detail = error.read().decode("utf-8", errors="replace")
            raise Exception(f"MiniMax HTTP {error.code}: {detail}")
        except urllib.error.URLError as error:
            if attempt < max_retries:
                wait_seconds = min(60, 2 ** attempt)
                print(f"    -> 网络错误，{wait_seconds:.1f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait_seconds)
                continue
            raise Exception(f"MiniMax network error: {error}")

        base_resp = data.get("base_resp") or {}
        base_code = base_resp.get("status_code", 0)
        if base_code != 0:
            if attempt < max_retries and should_retry(200, base_code):
                wait_seconds = min(60, 2 ** attempt)
                print(f"    -> API status {base_code}，{wait_seconds:.1f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait_seconds)
                continue
            trace_id = data.get("trace_id", "")
            message = base_resp.get("status_msg", "")
            raise Exception(f"MiniMax API status {base_code}: {message} trace_id={trace_id}")

        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise Exception(f"MiniMax response has no audio: {data}")

        audio_bytes = bytes.fromhex(audio_hex)
        with open(output_path, "wb") as output:
            output.write(audio_bytes)

        extra_info = data.get("extra_info") or {}
        return {
            "duration_ms": extra_info.get("audio_length", 0),
            "size_bytes": extra_info.get("audio_size", len(audio_bytes)),
            "trace_id": data.get("trace_id", ""),
        }

    raise Exception("MiniMax request retries exhausted")


def adjust_speed(input_file: str, speed: float, output_file: str) -> float:
    """用 ffmpeg 调整音频速度"""
    if speed == 1.0:
        subprocess.run(["ffmpeg", "-y", "-i", input_file, "-c", "copy", output_file],
                      capture_output=True, check=True)
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", input_file
        ], capture_output=True, text=True)
        try:
            data = json.loads(probe.stdout)
            return float(data["format"]["duration"]) * 1000
        except:
            return 0

    # 速度调整
    if speed <= 2.0 and speed >= 0.5:
        filter_str = f"atempo={speed}"
    elif speed > 2.0:
        filter_str = f"atempo=2.0,atempo={speed/2.0}"
    else:
        filter_str = f"atempo={speed}"

    result = subprocess.run([
        "ffmpeg", "-y", "-i", input_file,
        "-filter_complex", filter_str,
        "-ar", "32000",
        output_file
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise Exception(f"ffmpeg error: {result.stderr}")

    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", output_file
    ], capture_output=True, text=True)
    try:
        data = json.loads(probe.stdout)
        return float(data["format"]["duration"]) * 1000
    except:
        return 0


def merge_audio(files: list, output_path: str):
    """合并多个音频文件"""
    if not files:
        return False

    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for audio_file, _ in files:
            f.write(f"file '{os.path.abspath(audio_file)}'\n")

    result = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", output_path
    ], capture_output=True, text=True)

    os.remove(list_file)

    if result.returncode != 0:
        # 回退：直接拼接
        with open(output_path, "wb") as out:
            for audio_file, _ in files:
                with open(audio_file, "rb") as inp:
                    out.write(inp.read())
        return True

    return True


def generate_srt(subtitles: list, output_path: str):
    """生成 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, (start_ms, end_ms, text) in enumerate(subtitles, 1):
            start = ms_to_srt_time(start_ms)
            end = ms_to_srt_time(end_ms)
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")


def add_segment_subtitles(subtitles: list, start_ms: float, duration_ms: float, lines: list):
    """为合并请求后的多行台词按字数比例生成字幕时间轴"""
    if len(lines) == 1:
        subtitles.append((start_ms, start_ms + duration_ms, lines[0][1]))
        return

    weights = [max(1, len(text)) for _, text in lines]
    total_weight = sum(weights)
    cursor = start_ms
    for index, ((_, text), weight) in enumerate(zip(lines, weights)):
        if index == len(lines) - 1:
            end_ms = start_ms + duration_ms
        else:
            end_ms = cursor + duration_ms * weight / total_weight
        subtitles.append((cursor, end_ms, text))
        cursor = end_ms


def ms_to_srt_time(ms: int) -> str:
    """毫秒转 SRT 时间格式 HH:MM:SS,mmm"""
    seconds = ms / 1000
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main():
    parser = argparse.ArgumentParser(
        description="MiniMax TTS 对话生成器 (带缓存)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 首次生成
    python tts_dialogue.py --input dialogue.txt -sa 1.2 -sb 0.9

    # 调整速度 (复用缓存)
    python tts_dialogue.py --input dialogue.txt -sa 1.5 -sb 0.8

    # 强制重新生成
    python tts_dialogue.py --input dialogue.txt -f
        """
    )

    parser.add_argument("--input", "-i", help="输入对话文件路径")
    parser.add_argument("--speed-a", "-sa", type=float, default=None,
                        help=f"A 角色语速 (默认 {DEFAULT_CONFIG['speed_a']})")
    parser.add_argument("--speed-b", "-sb", type=float, default=None,
                        help=f"B 角色语速 (默认 {DEFAULT_CONFIG['speed_b']})")
    parser.add_argument("--voice-a", "-va", default=DEFAULT_CONFIG["voice_a"],
                        help=f"A 角色音色")
    parser.add_argument("--voice-b", "-vb", default=DEFAULT_CONFIG["voice_b"],
                        help=f"B 角色音色")
    parser.add_argument("--model", "-m", default=DEFAULT_CONFIG["model"],
                        help=f"模型")
    parser.add_argument("--output", "-o", default=DEFAULT_CONFIG["output"],
                        help=f"输出文件名")
    parser.add_argument("--cache-dir", default=DEFAULT_CONFIG["cache_dir"],
                        help=f"缓存目录")
    parser.add_argument("--api-base", default=DEFAULT_CONFIG["api_base"],
                        help="MiniMax API base URL")
    parser.add_argument("--request-interval", type=float, default=DEFAULT_CONFIG["request_interval"],
                        help="两次 API 请求之间的最小间隔秒数，免费账户建议 >= 6.5")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_CONFIG["max_retries"],
                        help="限流/临时错误最大重试次数")
    parser.add_argument("--no-combine-adjacent", action="store_true",
                        help="不要合并连续相同角色台词")
    parser.add_argument("--force", "-f", action="store_true",
                        help="强制重新生成，忽略缓存")
    parser.add_argument("--api-key", default=None,
                        help="API Key (或设置 MINIMAX_API_KEY 环境变量)")
    parser.add_argument("--list-voices", action="store_true",
                        help="列出所有可用音色")

    args = parser.parse_args()

    if args.list_voices:
        print("系统音色列表: https://platform.minimaxi.com/docs/faq/system-voice-id")
        print("查询可用音色 API: https://platform.minimaxi.com/docs/api-reference/voice-management-get")
        return

    if not args.input:
        print("错误: 请使用 --input 指定对话文件路径")
        sys.exit(1)

    api_key = args.api_key or get_api_key()
    if not api_key:
        print("错误: 未设置 API Key")
        sys.exit(1)

    # 读取对话
    if not os.path.exists(args.input):
        print(f"错误: 文件不存在 {args.input}")
        sys.exit(1)

    dialogues = read_dialogue_file(args.input)
    if not dialogues:
        print("错误: 无法解析对话内容")
        sys.exit(1)

    # 速度默认值
    speed_a = args.speed_a if args.speed_a is not None else DEFAULT_CONFIG["speed_a"]
    speed_b = args.speed_b if args.speed_b is not None else DEFAULT_CONFIG["speed_b"]

    segments = pack_adjacent_dialogues(dialogues, enabled=not args.no_combine_adjacent)

    # 计算 hash
    content_hash = compute_content_hash(dialogues, args.model, args.voice_a, args.voice_b)

    # 创建目录
    cache_dir = Path(args.cache_dir) / content_hash
    raw_dir = cache_dir / "raw"
    adjusted_dir = cache_dir / "adjusted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    adjusted_dir.mkdir(parents=True, exist_ok=True)

    # 保存对话内容
    with open(cache_dir / "dialogue.txt", "w", encoding="utf-8") as f:
        for speaker, text in dialogues:
            f.write(f"{speaker}:{text}\n")

    voice_map = {"A": args.voice_a, "B": args.voice_b}
    speed_map = {"A": speed_a, "B": speed_b}

    print(f"\n对话文件: {args.input}")
    print(f"内容 hash: {content_hash}")
    print(f"缓存目录: {cache_dir}")
    print(f"\n解析到 {len(dialogues)} 条对话:")
    for speaker, text in dialogues:
        print(f"  {speaker}: {text[:40]}{'...' if len(text) > 40 else ''}")
    if len(segments) != len(dialogues):
        print(f"\n已合并为 {len(segments)} 个 TTS 请求 (连续相同角色台词会合并)")
    print()

    audio_files = []
    subtitles = []
    current_time_ms = 0
    needs_api_call = args.force

    try:
        last_api_call_at = 0.0
        for i, segment in enumerate(segments):
            speaker = segment["speaker"]
            text = segment["text"]
            voice = voice_map[speaker]
            speed = speed_map[speaker]
            raw_file = raw_dir / f"{i:02d}_{speaker}_raw.mp3"
            adjusted_file = adjusted_dir / f"{i:02d}_{speaker}_adj_{speed}x.mp3"

            # 检查是否需要 API 调用
            segment_needs_api_call = not raw_file.exists() or args.force
            if segment_needs_api_call:
                needs_api_call = True
                elapsed = time.monotonic() - last_api_call_at
                if last_api_call_at and elapsed < args.request_interval:
                    sleep_seconds = args.request_interval - elapsed
                    print(f"限速等待 {sleep_seconds:.1f}s，避免触发 RPM 限流...")
                    time.sleep(sleep_seconds)

                print(f"[{i+1}/{len(segments)}] {speaker} ({voice}) @ 1.0x [HTTP API]")
                print(f"    {text[:120]}{'...' if len(text) > 120 else ''}")
                # 用 1.0x 调用 API 生成原始音频
                result = generate_speech(
                    text=text,
                    voice=voice,
                    model=args.model,
                    output_path=str(raw_file),
                    api_key=api_key,
                    api_base=args.api_base,
                    max_retries=args.max_retries,
                    sample_rate=DEFAULT_CONFIG["sample_rate"],
                    bitrate=DEFAULT_CONFIG["bitrate"],
                    audio_format=DEFAULT_CONFIG["format"],
                    channel=DEFAULT_CONFIG["channel"],
                )
                last_api_call_at = time.monotonic()
                trace_suffix = f", trace_id={result['trace_id']}" if result.get("trace_id") else ""
                print(f"    -> {result['duration_ms']}ms ({result['size_bytes']} bytes{trace_suffix})")
            else:
                print(f"[{i+1}/{len(segments)}] {speaker} ({voice}) @ 1.0x [缓存]")

            # 速度调整 (如果已调整过的文件存在，直接用)
            if segment_needs_api_call or not adjusted_file.exists():
                print(f"[{i+1}/{len(segments)}] {speaker} @ {speed}x [调整速度]")
                adjusted_duration_ms = adjust_speed(str(raw_file), speed, str(adjusted_file))
            else:
                print(f"[{i+1}/{len(segments)}] {speaker} @ {speed}x [缓存]")
                # 获取已有文件时长
                probe = subprocess.run([
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "json", str(adjusted_file)
                ], capture_output=True, text=True)
                try:
                    data = json.loads(probe.stdout)
                    adjusted_duration_ms = float(data["format"]["duration"]) * 1000
                except:
                    adjusted_duration_ms = 0

            audio_files.append((str(adjusted_file), adjusted_duration_ms))
            add_segment_subtitles(subtitles, current_time_ms, adjusted_duration_ms, segment["lines"])
            current_time_ms += adjusted_duration_ms

        # 合并音频
        print("\n合并音频...")
        output_mp3 = f"{args.output}.mp3"
        merge_audio(audio_files, output_mp3)

        # 生成字幕
        print("生成字幕...")
        output_srt = f"{args.output}.srt"
        generate_srt(subtitles, output_srt)

        print()
        print("=" * 50)
        print("完成!")
        print(f"  音频: {output_mp3}")
        print(f"  字幕: {output_srt}")
        print(f"  缓存: {cache_dir}")
        print()

        if needs_api_call:
            print("  (本次调用了 API)")
        else:
            print("  (复用缓存，未调用 API)")

    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
