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
        首次运行会调用 API 生成原始音频 (raw/)
        再次运行如果对话内容没变，不会重新调用 API
        只会根据新的 --speed-a / --speed-b 重新生成输出

参数:
    --input, -i          输入文件路径 (对话内容)
    --speed-a, -sa       A 角色语速 (默认 1.0)
    --speed-b, -sb       B 角色语速 (默认 1.0)
    --voice-a, -va       A 角色音色 (默认 Chinese (Mandarin)_Straightforward_Boy)
    --voice-b, -vb       B 角色音色 (默认 Chinese (Mandarin)_Cute_Spirit)
    --model, -m          模型 (默认 speech-2.8-hd)
    --output, -o         输出文件名 (默认 dialogue_output)
    --cache-dir          缓存目录 (默认 .tts_cache)
    --force, -f          强制重新生成，忽略缓存
    --api-key            API Key (或设置 MINIMAX_API_KEY 环境变量)

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
from pathlib import Path
from datetime import datetime

# 默认配置
DEFAULT_CONFIG = {
    "voice_a": "Chinese (Mandarin)_Straightforward_Boy",
    "voice_b": "Chinese (Mandarin)_Cute_Spirit",
    "model": "speech-2.8-hd",
    "speed_a": 1.0,
    "speed_b": 1.0,
    "output": "dialogue_output",
    "cache_dir": ".tts_cache",
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


def compute_content_hash(dialogues: list) -> str:
    """计算对话内容的 hash，用于缓存标识"""
    content = "|".join([f"{s}:{t}" for s, t in dialogues])
    return hashlib.md5(content.encode()).hexdigest()[:12]


def generate_speech(text: str, voice: str, speed: float, model: str, output_path: str, api_key: str) -> dict:
    """调用 mmx-cli 生成语音"""
    env = os.environ.copy()
    env["MINIMAX_API_KEY"] = api_key

    cmd = [
        "mmx", "speech", "synthesize",
        "--model", model,
        "--text", text,
        "--voice", voice,
        "--out", output_path,
    ]

    if speed != 1.0:
        cmd.extend(["--speed", str(speed)])

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode != 0:
        raise Exception(f"mmx error: {result.stderr}")

    try:
        output = json.loads(result.stdout)
        return {
            "duration_ms": output.get("duration_ms", 0),
            "size_bytes": output.get("size_bytes", 0)
        }
    except json.JSONDecodeError:
        return {"duration_ms": 0, "size_bytes": 0}


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

    parser.add_argument("--input", "-i", required=True, help="输入对话文件路径")
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
    parser.add_argument("--force", "-f", action="store_true",
                        help="强制重新生成，忽略缓存")
    parser.add_argument("--api-key", default=None,
                        help="API Key (或设置 MINIMAX_API_KEY 环境变量)")
    parser.add_argument("--list-voices", action="store_true",
                        help="列出所有可用音色")

    args = parser.parse_args()

    if args.list_voices:
        os.system("mmx speech voices")
        return

    if not check_mmx_cli():
        print("错误: mmx-cli 未安装，请运行: npm install -g mmx-cli")
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

    # 计算 hash
    content_hash = compute_content_hash(dialogues)

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
    print()

    audio_files = []
    subtitles = []
    current_time_ms = 0
    needs_api_call = args.force

    try:
        for i, (speaker, text) in enumerate(dialogues):
            voice = voice_map[speaker]
            speed = speed_map[speaker]
            raw_file = raw_dir / f"{i:02d}_{speaker}_raw.mp3"
            adjusted_file = adjusted_dir / f"{i:02d}_{speaker}_adj_{speed}x.mp3"

            # 检查是否需要 API 调用
            if not raw_file.exists() or args.force:
                needs_api_call = True

            if needs_api_call:
                print(f"[{i+1}/{len(dialogues)}] {speaker} ({voice}) @ 1.0x [API]")
                print(f"    {text}")
                # 用 1.0x 调用 API 生成原始音频
                result = generate_speech(text, voice, 1.0, args.model, str(raw_file), api_key)
                print(f"    -> {result['duration_ms']}ms ({result['size_bytes']} bytes)")
            else:
                print(f"[{i+1}/{len(dialogues)}] {speaker} ({voice}) @ 1.0x [缓存]")

            # 速度调整 (如果已调整过的文件存在，直接用)
            if not adjusted_file.exists():
                print(f"[{i+1}/{len(dialogues)}] {speaker} @ {speed}x [调整速度]")
                adjusted_duration_ms = adjust_speed(str(raw_file), speed, str(adjusted_file))
            else:
                print(f"[{i+1}/{len(dialogues)}] {speaker} @ {speed}x [缓存]")
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
            subtitles.append((current_time_ms, current_time_ms + adjusted_duration_ms, text))
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
