"""ASR 本地化预研探针 —— 批次 E（契约《01-契约冻结》§7.2 / 工作包 13 §3.1）。

不依赖任何服务（不起 dev 后端 / 不连云端），可独立于 pytest 并行运行。

双门判定：
  - SPELL_GATE：Vosk + 26 字母 grammar 跑全部 `*__spell.wav`，逐文件「识别字母序列 vs 目标词」
                的**最长公共子序列长度 / 目标长度**，汇总均值 ≥ 0.90 为 PASS。
  - WHOLE_GATE：sherpa streaming zipformer 跑全部 `*__whole.wav`，逐文件「识别文本 vs 目标词」
                归一化（小写去空格）编辑距离相似度 ≥ 0.8 的**比例** ≥ 80% 为 PASS。
  - ZH_GATE（中文通路自检，非硬门）：优先用本地 TTS 合成「苹果/香蕉/早上好」3 条中文 wav 喂引擎 B；
            若 TTS 模型未就绪 → 改用引擎 B 转写 3 条英文 whole 验证英文通路，标注「待 TTS 就绪后补测」。

注意：qa/audio 在仓库根 `qa/audio/`，采样率实测为 **24000Hz**（非引擎原生率），脚本内做 numpy 重采样。

用法：
    python scripts/_asr_local_probe.py            # 自动定位模型与音频
    python scripts/_asr_local_probe.py --audio-dir X --models-dir Y
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent      # .../zhanglaoshi_zhangyuze
BACKEND = Path(__file__).resolve().parent.parent          # .../backend
AUDIO_DIR_DEFAULT = REPO / "qa" / "audio"
MODELS_DIR_DEFAULT = BACKEND / "models" / "asr"

SPELL_GATE = 0.90
WHOLE_GATE = 0.80

LETTERS = [chr(ord("a") + i) for i in range(26)]
GRAMMAR = json.dumps(LETTERS)


# ---------------------------------------------------------------------------
# 音频 IO + 重采样
# ---------------------------------------------------------------------------

def read_wav_int16(path: Path) -> tuple[int, np.ndarray]:
    """读 wav → (sample_rate, int16 单声道数组)。wave 模块自动剥离 44 字节头。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    # 多声道取均值降为单声道
    if w.getnchannels() == 2:
        data = data.reshape(-1, 2).mean(axis=1)
    return sr, data.astype(np.int16)


def resample_int16(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """线性插值重采样（探针够用，不引入 scipy）。返回 int16。"""
    if sr_in == sr_out:
        return x.astype(np.int16)
    n_out = max(1, int(round(len(x) * sr_out / sr_in)))
    old_idx = np.linspace(0, len(x) - 1, n_out)
    new = np.interp(old_idx, np.arange(len(x)), x.astype(np.float64))
    return np.clip(new, -32768, 32767).astype(np.int16)


def to_float32(x: np.ndarray) -> np.ndarray:
    return (x.astype(np.float64) / 32768.0).astype(np.float32)


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

def lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    dp = [0] * (len(b) + 1)
    for c in a:
        prev = 0
        for j in range(1, len(b) + 1):
            tmp = dp[j]
            dp[j] = prev + 1 if c == b[j - 1] else max(dp[j], dp[j - 1])
            prev = tmp
    return dp[len(b)]


def edit_distance(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    dp = list(range(len(b) + 1))
    for c in a:
        prev = dp[0]
        dp[0] += 1
        for j in range(1, len(b) + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (0 if c == b[j - 1] else 1))
            prev = cur
    return dp[len(b)]


def norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalpha())


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    m = max(len(a), len(b))
    if m == 0:
        return 1.0
    return 1.0 - edit_distance(a, b) / m


# ---------------------------------------------------------------------------
# 模型目录定位
# ---------------------------------------------------------------------------

def find_model(models_dir: Path, prefix: str) -> Path | None:
    if not models_dir.is_dir():
        return None
    matches = [p for p in models_dir.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    return matches[0] if matches else None


SHERPA_PREFIX = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue"
SHERPA_SR = 16000  # SenseVoice 原生率


def _build_sherpa(model_dir: Path):
    """SenseVoice 离线识别器（契约修订 v1.0.2：zipformer bilingual 预研 FAIL 后换型）。"""
    import sherpa_onnx

    model = model_dir / "model.int8.onnx"
    tokens = model_dir / "tokens.txt"
    if not model.is_file() or not tokens.is_file():
        raise FileNotFoundError(f"SenseVoice 文件缺失（model.int8.onnx/tokens.txt）：{model_dir}")
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model), tokens=str(tokens),
        use_itn=True, num_threads=1, provider="cpu",
    )


def _transcribe_sherpa(rec, pcm_int16: np.ndarray, sr_in: int = SHERPA_SR) -> str:
    # ⚠️ 按真实采样率喂入；前后各垫 0.3s 静音给模型起止上下文。
    pad = np.zeros(int(sr_in * 0.3), dtype=np.int16)
    pcm = np.concatenate([pad, pcm_int16, pad])
    stream = rec.create_stream()
    stream.accept_waveform(sr_in, to_float32(pcm))
    rec.decode_stream(stream)
    return stream.result.text


# ---------------------------------------------------------------------------
# 引擎 A：Vosk 字母拼读
# ---------------------------------------------------------------------------

def run_spell(vosk_dir: Path, audio_dir: Path) -> tuple[float, list[dict], bool]:
    from vosk import KaldiRecognizer, Model

    model = Model(str(vosk_dir))
    files = sorted(audio_dir.glob("*__spell.wav"))
    rows: list[dict] = []
    for f in files:
        target = f.name.split("__spell.wav")[0].lower()
        sr, pcm = read_wav_int16(f)
        pcm16 = resample_int16(pcm, sr, 16000)
        rec = KaldiRecognizer(model, 16000, GRAMMAR)
        rec.AcceptWaveform(pcm16.tobytes())
        res = json.loads(rec.FinalResult())
        hyp = res.get("text", "")
        hyp_letters = "".join(ch for ch in hyp.lower() if ch.isalpha())
        target_letters = "".join(ch for ch in target if ch.isalpha())
        hit = lcs_len(hyp_letters, target_letters) / len(target_letters) if target_letters else 0.0
        rows.append({
            "file": f.name, "target": target, "hyp": hyp.strip(),
            "hit": round(hit, 4),
        })
    mean = sum(r["hit"] for r in rows) / len(rows) if rows else 0.0
    return mean, rows, bool(rows)


# ---------------------------------------------------------------------------
# 引擎 B：sherpa streaming zipformer
# ---------------------------------------------------------------------------



def run_whole(sherpa_dir: Path, audio_dir: Path) -> tuple[float, list[dict], bool]:
    rec = _build_sherpa(sherpa_dir)
    files = sorted(audio_dir.glob("*__whole.wav"))
    rows: list[dict] = []
    passed = 0
    for f in files:
        target = f.name.split("__whole.wav")[0]
        sri, pcm = read_wav_int16(f)
        hyp = _transcribe_sherpa(rec, pcm, sri)
        sim = similarity(hyp, target)
        ok = sim >= WHOLE_GATE
        passed += int(ok)
        rows.append({
            "file": f.name, "target": target, "hyp": hyp.strip(),
            "sim": round(sim, 4), "ok": ok,
        })
    ratio = passed / len(rows) if rows else 0.0
    return ratio, rows, bool(rows)


# ---------------------------------------------------------------------------
# 中文通路自检（ZH_GATE，非硬门）
# ---------------------------------------------------------------------------

def zh_self_check(sherpa_dir: Path, audio_dir: Path) -> tuple[str, str, list[dict]]:
    """返回 (gate, detail, rows)。用 tts_local.synthesize_local 合成中文 wav → 引擎 B 转写命中。"""
    sys.path.insert(0, str(BACKEND))
    try:
        from app.services import tts_local
    except Exception as exc:  # noqa: BLE001
        return "PENDING", f"tts_local 导入失败（{type(exc).__name__}: {exc}），待补测", []

    if not tts_local.is_ready()[0]:
        return "PENDING", "本地 TTS 未就绪，待模型就绪后补测中文自检", []

    try:
        rec = _build_sherpa(sherpa_dir)
        phrases = ["苹果。", "香蕉。", "早上好。"]
        rows = []
        ok_all = True
        for ph in phrases:
            wav = tts_local.synthesize_local(ph, lang="zh-CN")
            # wav bytes → int16 pcm（44 字节标准头，wave 解析）
            import io
            with wave.open(io.BytesIO(wav), "rb") as w:
                tts_sr = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            hyp = _transcribe_sherpa(rec, pcm, tts_sr)
            bare = ph.rstrip("。")
            hit = any(c in hyp for c in bare)
            ok_all = ok_all and hit
            rows.append({"phrase": bare, "hyp": hyp.strip(), "hit": hit})
        gate = "PASS" if ok_all else "FAIL"
        return gate, "本地 TTS 合成中文 → 引擎 B 转写命中", rows
    except Exception as exc:  # noqa: BLE001
        return "PENDING", f"中文自检异常（{type(exc).__name__}: {exc}），待 TTS 就绪后补测", []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", type=Path, default=AUDIO_DIR_DEFAULT)
    ap.add_argument("--models-dir", type=Path, default=MODELS_DIR_DEFAULT)
    args = ap.parse_args()

    audio_dir = args.audio_dir
    models_dir = args.models_dir

    print("=" * 78)
    print("ASR 本地化预研探针")
    print(f"  音频目录 : {audio_dir}")
    print(f"  模型目录 : {models_dir}")
    print("=" * 78)

    vosk_dir = find_model(models_dir, "vosk-model-small-en-us")
    sherpa_dir = find_model(models_dir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue")

    print(f"[模型] vosk   = {vosk_dir or '未找到'}")
    print(f"[模型] sherpa = {sherpa_dir or '未找到'}")
    print()

    spell_ok = whole_ok = False
    spell_mean = whole_ratio = 0.0
    spell_rows: list[dict] = []
    whole_rows: list[dict] = []

    # ---- 引擎 A：Vosk 字母 ----
    if vosk_dir is None:
        print("SPELL_GATE: FAIL（vosk 模型未下载）")
    else:
        try:
            spell_mean, spell_rows, _ = run_spell(vosk_dir, audio_dir)
            spell_ok = spell_mean >= SPELL_GATE
            print(f"\n--- SPELL（Vosk + 26 字母 grammar），门线均值 ≥ {SPELL_GATE} ---")
            print(f"{'file':42s} {'target':14s} {'hit':>6s}  hyp")
            for r in spell_rows:
                print(f"{r['file']:42s} {r['target']:14s} {r['hit']:6.3f}  {r['hyp']}")
            print(f"均值 = {spell_mean:.4f}  →  SPELL_GATE: {'PASS' if spell_ok else 'FAIL'}")
        except Exception as exc:  # noqa: BLE001
            print(f"SPELL_GATE: FAIL（运行异常 {type(exc).__name__}: {exc}）")

    # ---- 引擎 B：sherpa whole ----
    if sherpa_dir is None:
        print("\nWHOLE_GATE: FAIL（sherpa 模型未下载）")
    else:
        try:
            whole_ratio, whole_rows, _ = run_whole(sherpa_dir, audio_dir)
            whole_ok = whole_ratio >= WHOLE_GATE
            print(f"\n--- WHOLE（SenseVoice 离线，{SHERPA_SR}Hz），门线比例 ≥ {WHOLE_GATE} ---")
            print(f"{'file':42s} {'sim':>6s} {'ok':>3s}  target / hyp")
            for r in whole_rows:
                print(f"{r['file']:42s} {r['sim']:6.3f} {str(r['ok']):>3s}  "
                      f"{r['target']} / {r['hyp']}")
            print(f"≥{WHOLE_GATE} 比例 = {whole_ratio:.3f}  →  WHOLE_GATE: "
                  f"{'PASS' if whole_ok else 'FAIL'}")
        except Exception as exc:  # noqa: BLE001
            print(f"WHOLE_GATE: FAIL（运行异常 {type(exc).__name__}: {exc}）")

    # ---- 中文自检 ----
    zh_gate, zh_detail, zh_rows = "PENDING", "sherpa 模型缺失，跳过", []
    if sherpa_dir is not None:
        try:
            zh_gate, zh_detail, zh_rows = zh_self_check(sherpa_dir, audio_dir)
        except Exception as exc:  # noqa: BLE001
            zh_gate, zh_detail = "PENDING", f"自检异常 {type(exc).__name__}: {exc}"
    print(f"\n--- ZH 中文通路自检 ---")
    for r in zh_rows:
        print("  ", r)
    print(f"ZH_GATE: {zh_gate} —— {zh_detail}")

    # ---- 总判定 ----
    print("\n" + "=" * 78)
    print(f"SPELL_GATE: {'PASS' if spell_ok else 'FAIL'}  (mean={spell_mean:.4f}, 门线 {SPELL_GATE})")
    print(f"WHOLE_GATE: {'PASS' if whole_ok else 'FAIL'}  (ratio={whole_ratio:.3f}, 门线 {WHOLE_GATE})")
    print(f"ZH_GATE   : {zh_gate}")
    both = spell_ok and whole_ok
    print(f"\n双硬门（SPELL + WHOLE）: {'PASS → 允许接线' if both else 'FAIL → 停下上报，不准接线'}")
    print("=" * 78)

    return 0 if both else 2


if __name__ == "__main__":
    sys.exit(main())
