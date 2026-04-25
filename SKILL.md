---
name: mmx-tts-dialogue
description: MiniMax TTS 对话生成器 - 使用 MiniMax API 生成多角色对话音频，支持缓存和速度调整。当用户想要生成对话音频、创建语音对话、使用不同音色说话、生成带字幕的语音时触发。尤其适用于需要多次调整速度而不消耗 API 配额的情况。
type: skill
---

# MiniMax TTS 对话生成

使用 MiniMax (`mmx-cli`) 生成多人对话音频，每个角色使用不同音色，支持缓存机制避免重复调用 API。

## 核心功能

1. **多角色对话** - A/B 两个角色，各自使用独立音色
2. **智能缓存** - 同一对话内容只调用一次 API，后续调整速度不消耗配额
3. **速度调整** - 可分别为 A/B 设置语速 (0.5x - 2.0x)
4. **字幕生成** - 自动生成 SRT 字幕文件，时间轴与音频匹配

## 前置要求

```bash
# 1. 安装 mmx-cli
npm install -g mmx-cli

# 2. 设置 API Key
export MINIMAX_API_KEY="sk-cp-xxx"  # 你的 MiniMax API Key
mmx auth login --api-key "$MINIMAX_API_KEY"

# 3. 安装 ffmpeg (macOS)
brew install ffmpeg
```

## 快速开始

### 1. 创建对话文件

创建 `dialogue.txt`，格式如下：

```
A:最近这段时间，好多朋友离开了北京。
B:确实，感觉一到年底年初，朋友圈全是告别局。
A:每次他们问我怎么想，我都鼓励他们快走。
B:那你呢？你怎么还赖在这儿不走？
```

### 2. 生成对话音频

```bash
# 首次生成 (调用 API)
python tts_dialogue.py --input dialogue.txt

# 调整速度 (复用缓存，不消耗 API)
python tts_dialogue.py --input dialogue.txt -sa 1.2 -sb 0.9
```

### 3. 输出文件

- `dialogue_output.mp3` - 合并后的完整对话音频
- `dialogue_output.srt` - 字幕文件

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input`, `-i` | 输入对话文件路径 | 必须 |
| `--speed-a`, `-sa` | A 角色语速 | `1.0` |
| `--speed-b`, `-sb` | B 角色语速 | `1.0` |
| `--voice-a`, `-va` | A 角色音色 ID | `Chinese (Mandarin)_Cute_Spirit` (憨憨萌兽) |
| `--voice-b`, `-vb` | B 角色音色 ID | `Chinese (Mandarin)_Gentleman` (温润男声) |
| `--model`, `-m` | TTS 模型 | `speech-2.8-hd` |
| `--output`, `-o` | 输出文件名 (不含扩展名) | `dialogue_output` |
| `--force`, `-f` | 强制重新生成，忽略缓存 | `False` |
| `--list-voices` | 列出所有可用音色 | - |

## 音色推荐

### 中文对话常用组合

| 角色 | 音色 ID | 名称 |
|------|---------|------|
| 女 A (默认) | `Chinese (Mandarin)_Cute_Spirit` | 憨憨萌兽 |
| 男 B (默认) | `Chinese (Mandarin)_Gentleman` | 温润男声 |
| 男 A | `Chinese (Mandarin)_Straightforward_Boy` | 率真弟弟 |
| 男 A | `male-qn-qingse` | 青涩青年 |
| 男 A | `male-qn-badao` | 霸道青年 |
| 女 B | `Chinese (Mandarin)_Crisp_Girl` | 清脆少女 |
| 女 B | `Chinese (Mandarin)_Warm_Bestie` | 温暖闺蜜 |
| 女 B | `female-shaonv` | 少女 |

> **默认角色分配：** A = 憨憨萌兽 (可爱提问), B = 温润男声 (沉稳回答)

### 特殊音色

| 音色 ID | 名称 |
|---------|------|
| `cartoon_pig` | 卡通猪小琪 |
| `Robot_Armor` | 机械战甲 |
| `Chinese (Mandarin)_Humorous_Elder` | 搞笑大爷 |
| `Chinese (Mandarin)_Sweet_Lady` | 甜美女声 |

### 查看完整音色列表

```bash
python tts_dialogue.py --list-voices
```

## 缓存机制

缓存目录结构：

```
.tts_cache/
└── <content_hash>/
    ├── dialogue.txt          # 对话内容副本
    ├── raw/                  # 原始音频 (1.0x 速度)
    │   ├── 00_A_raw.mp3
    │   ├── 01_B_raw.mp3
    │   └── ...
    └── adjusted/             # 调整速度后的音频
        ├── 00_A_adj_1.2x.mp3
        ├── 00_A_adj_0.9x.mp3
        └── ...
```

**工作原理：**
1. 首次运行根据对话内容计算 hash，调用 API 生成原始音频保存在 `raw/`
2. 后续运行时，如果对话内容没变（hash 相同），直接复用 `raw/` 中的音频
3. 速度调整通过 ffmpeg 实现，结果保存在 `adjusted/`
4. 相同速度的调整结果会被缓存

**节省 API 的例子：**
```bash
# 第1次: 调用 API 生成缓存
python tts_dialogue.py --input dialogue.txt -sa 1.0 -sb 1.0

# 第2次: 调整速度，复用缓存
python tts_dialogue.py --input dialogue.txt -sa 1.2 -sb 0.9

# 第3次: 再次调整，再复用
python tts_dialogue.py --input dialogue.txt -sa 0.8 -sb 1.1
```

## 速度建议

| 场景 | A 速度 | B 速度 |
|------|--------|--------|
| 日常对话 | 1.0 | 1.0 |
| A 急促 / B 悠闲 | 1.2 | 0.9 |
| A 悠闲 / B 急促 | 0.9 | 1.1 |
| 卡通/开心场景 | 1.1 | 1.1 |
| 沉思/悲伤场景 | 0.8 | 0.8 |

## 完整示例

```bash
# 设置 API Key
export MINIMAX_API_KEY="sk-cp-xxx"

# 创建对话文件
cat > my_dialogue.txt << 'EOF'
A:最近这段时间，好多朋友离开了北京，或者正在计划离开。
B:确实，感觉一到年底年初，朋友圈全是告别局。
A:每次他们问我怎么想，我都鼓励他们快走。如果这里不快乐，就去快乐的地方。
B:那你呢？你怎么还赖在这儿不走？
A:说实话，有时候我也想不起自己为什么还留在这里。
EOF

# 首次生成
python tts_dialogue.py --input my_dialogue.txt -sa 1.0 -sb 1.0

# 调整 A 快一点，B 慢一点
python tts_dialogue.py --input my_dialogue.txt -sa 1.2 -sb 0.9

# 用不同音色
python tts_dialogue.py --input my_dialogue.txt \
  -va "male-qn-badao" \
  -vb "Chinese (Mandarin)_Warm_Bestie"

# 强制重新生成
python tts_dialogue.py --input my_dialogue.txt -f
```

## 脚本位置

脚本位于 `scripts/tts_dialogue.py`，调用时使用：

```bash
python scripts/tts_dialogue.py --input dialogue.txt
```

或直接使用项目根目录的脚本（如果存在）。
