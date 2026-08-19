# -*- coding: utf-8 -*-
"""
洛克王国之狼来了 —— 捕捉音效监听触发器

功能:
  1. 监听指定进程(游戏)的声音, 检测到 compare/success.wav、fail.wav 或 hit.wav 时触发
  2. 触发后: 先播放 play_material/flash.wav (音量 x2),
     再以倍速(同时变调)播放 play_material/aowu.wav
     倍率 = 0.3 + 0.1 * 倍率进度, 超过 4.0 后回到 0.3;
     界面显示的统计次数为真实累计, 不会被自动清零
     新的触发会打断上一次播放, 立即按新倍率从头播放
  3. 全屏弹出 images/aowu.png, 突然出现后淡出; 窗口鼠标点击穿透、不抢焦点,
     不影响用户操作其他软件
  4. 界面开关状态保存在 Windows 注册表中, 重启后自动恢复 (无外置配置文件)

依赖: pip install numpy soundcard scipy SoundFile Pillow proc-tap pycaw
"""

import os
import sys
import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

# ---------------- 路径 ----------------
if getattr(sys, "frozen", False):
    # PyInstaller 打包后: 资源文件位于 _MEIPASS(单文件模式为临时目录, 文件夹模式为 _internal)
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPARE_DIR = os.path.join(BASE_DIR, "compare")
PLAY_DIR = os.path.join(BASE_DIR, "play_material")
IMAGE_DIR = os.path.join(BASE_DIR, "images")

SUCCESS_WAV = os.path.join(COMPARE_DIR, "success.wav")
FAIL_WAV = os.path.join(COMPARE_DIR, "fail.wav")
HIT_WAV = os.path.join(COMPARE_DIR, "hit.wav")
SHINY_TRIGGER_WAV = os.path.join(COMPARE_DIR, "battle_clear_sprit.wav")   # 战斗内出异色的触发音效
SHINY_TAG_IMG = os.path.join(COMPARE_DIR, "different_color_tag.png")      # 异色标签模板图
FLASH_WAV = os.path.join(PLAY_DIR, "flash.wav")
AOWU_WAV = os.path.join(PLAY_DIR, "aowu.wav")

# 可选播放音效: 键=配置值, 值=(显示名, 文件路径)
SOUND_FILES = {
    "aowu": ("狼人", AOWU_WAV),
    "kskbl": ("康神开播了", os.path.join(PLAY_DIR, "kskbl.wav")),
    "zdjd": ("真的假的", os.path.join(PLAY_DIR, "zdjd.wav")),
    "wkzkbl": ("我靠真开播了", os.path.join(PLAY_DIR, "wkzkbl.wav")),
    "let_it_go": ("关羽释怀の小曲", os.path.join(PLAY_DIR, "let_it_go.wav")),
}
SOUND_ORDER = ("aowu", "kskbl", "zdjd", "wkzkbl")
# 战斗内出异色可选音效(后续新增歌曲: 在 SOUND_FILES 里加条目, 并在此追加键)
SHINY_SOUND_ORDER = ("let_it_go",)
AOWU_IMG = os.path.join(IMAGE_DIR, "aowu.png")   # 透明PNG, 显示时合成白底
AOWU_IMG_JPG = os.path.join(IMAGE_DIR, "aowu.jpg")  # 旧格式回退
ICON_PATH = os.path.join(BASE_DIR, "icon.ico")

# ---------------- 参数 ----------------
SAMPLE_RATE = 48000      # 采集采样率
MATCH_RATE = 16000       # 匹配采样率(降采样以加快匹配, 8kHz 带宽足够识别音效)
CHUNK_SEC = 0.1          # 每次采集时长(秒)
BUFFER_SEC = 6.0         # 匹配缓冲区时长(秒), 需大于比对音频长度
THRESHOLD = 0.45         # 匹配阈值(波形/频谱取较高者) 0~1: 越大越严格(误触发少, 但可能漏检)
MAX_CORR = 200.0         # 相关度超过该值视为异常, 不做任何操作
DEBOUNCE_SEC = 0.8       # 触发后冷却(秒), 防止同一音效被连续计多次
FADE_HOLD_SEC = 0.35     # 图片出现后保持时间(秒)
FADE_DURATION_SEC = 1.5  # 图片淡出时长(秒)
FADE_STEPS = 30          # 淡出步数
INITIAL_ALPHA = 0.7      # 图片出现时的初始不透明度(0~1)

FLASH_GAIN = 2.0  # flash.wav 播放音量倍数

MIN_RATE = 0.3   # 默认最低倍率(用户可调, 下限 0.1)
MAX_RATE = 4.0   # 默认最高倍率(用户可调)
IMG_MATCH_THRESHOLD = 0.5   # 截图异色标签匹配阈值 0~1
SHINY_PROTECT_SEC = 10.0    # 异色小曲播放后禁止打断的时长(秒)
PROCESS_NAME = "NRC-Win64-Shipping.exe"   # 监听的游戏进程(固定)


# ---------------- 设置持久化 (Windows 注册表, 无外置文件) ----------------
REG_KEY = r"Software\RockKingdomWolfComing"


def load_settings():
    """读取界面开关设置; 读取失败时返回 {}"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        out = {}
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                out[name] = value
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        return out
    except Exception:
        return {}


def save_setting(name, value):
    try:
        import winreg
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY)
        if isinstance(value, str):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1 if value else 0)
        winreg.CloseKey(key)
    except Exception:
        pass


def check_missing_deps():
    import importlib.util
    missing = []
    for mod, pip_name in (("numpy", "numpy"), ("soundcard", "soundcard"),
                          ("scipy", "scipy"), ("soundfile", "SoundFile"),
                          ("PIL", "Pillow"), ("proctap", "proc-tap"),
                          ("pycaw", "pycaw")):
        if importlib.util.find_spec(mod) is None:
            missing.append(pip_name)
    return missing


def ensure_admin():
    """非管理员时通过 UAC 提权重启自身; 返回是否已具备管理员权限"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):
            cmd, args = script, params
        else:
            cmd, args = sys.executable, f'"{script}" {params}'
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", cmd, args, None, 1)
        if r > 32:  # UAC 提权成功, 新进程已启动, 退出当前进程
            sys.exit(0)
        return False  # 用户取消了 UAC 提权
    except Exception:
        return True


# ---------------- 声音匹配 ----------------
def normalized_max_corr(signal, template):
    """返回 signal 中与 template 的最大归一化互相关系数 (0~1)"""
    import numpy as np
    if len(signal) < len(template):
        return 0.0
    s = signal.astype(np.float64)
    t = template.astype(np.float64)
    # 预加重: 增强中高频瞬态, 减小低频背景音乐对匹配的干扰
    s = s[1:] - 0.95 * s[:-1]
    t = t[1:] - 0.95 * t[:-1]
    from scipy.signal import correlate
    try:
        corr = correlate(s, t, mode="valid", method="fft")
    except TypeError:  # 旧版 scipy 没有 method 参数
        corr = correlate(s, t, mode="valid")
    t_energy = float(np.dot(t, t))
    cs = np.concatenate(([0.0], np.cumsum(s * s)))
    win = cs[len(t):] - cs[:-len(t)]
    denom = np.sqrt(t_energy * win)
    denom[denom < 1e-12] = 1e-12
    return float(np.max(corr / denom))


def spectral_score(signal, template):
    """基于对数频谱的相关度(0~1): 对背景音乐、相位抖动更鲁棒"""
    import numpy as np
    from scipy.signal import stft, fftconvolve
    if len(signal) < len(template):
        return 0.0
    nperseg, noverlap = 512, 256
    _, _, S = stft(signal.astype(np.float64), fs=1.0, nperseg=nperseg, noverlap=noverlap)
    _, _, T = stft(template.astype(np.float64), fs=1.0, nperseg=nperseg, noverlap=noverlap)
    if T.shape[1] < 2 or S.shape[1] < T.shape[1]:
        return 0.0
    S = np.log1p(np.abs(S))
    T = np.log1p(np.abs(T))
    Tn = T - T.mean(axis=1, keepdims=True)   # 每频段去均值: 抑制平稳背景音乐
    L = T.shape[1]
    corr = fftconvolve(S, Tn[:, ::-1], mode="valid", axes=1)  # (频段, 偏移)
    t_energy = (Tn * Tn).sum(axis=1)
    cs = np.concatenate((np.zeros((S.shape[0], 1)), np.cumsum(S * S, axis=1)), axis=1)
    win = cs[:, L:] - cs[:, :-L]
    denom = np.sqrt(np.maximum(win, 1e-12) * np.maximum(t_energy, 1e-12)[:, None])
    per = corr / denom
    w = t_energy / t_energy.max()           # 按模板频段能量加权, 忽略模板无能量的频段
    return float(np.max((per * w[:, None]).sum(axis=0) / w.sum()))


def image_contains(shot, tpl_gray):
    """判断截图中是否出现模板图片, 返回最大归一化相关度(0~1)"""
    import numpy as np
    from scipy.signal import fftconvolve
    scr = np.asarray(shot.convert("L"), dtype=np.float64)
    tpl = np.asarray(tpl_gray, dtype=np.float64)
    if tpl.shape[0] > scr.shape[0] or tpl.shape[1] > scr.shape[1]:
        return 0.0
    t = tpl - tpl.mean()
    s = scr - scr.mean()
    corr = fftconvolve(s, t[::-1, ::-1], mode="valid")
    te = float((t * t).sum())
    ii = np.zeros((scr.shape[0] + 1, scr.shape[1] + 1))
    ii[1:, 1:] = np.cumsum(np.cumsum(s * s, axis=0), axis=1)
    h, w = tpl.shape
    win = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    denom = np.sqrt(np.maximum(win * te, 1e-12))
    return float((corr / denom).max())


def load_templates(evq):
    """加载比对音频模板(在监听线程内调用, 避免阻塞界面)"""
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
    except ImportError as e:
        evq.put(("detector_down", f"缺少依赖, 无法加载比对音频: {e}"))
        return {}
    templates = {}
    for name, path in (("success", SUCCESS_WAV), ("fail", FAIL_WAV), ("hit", HIT_WAV),
                       ("shiny", SHINY_TRIGGER_WAV)):
        if not os.path.exists(path):
            evq.put(("log", f"警告: 未找到比对音频 {path}"))
            continue
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        if sr != MATCH_RATE:
            mono = resample_poly(mono, MATCH_RATE, sr).astype(np.float32)
        if len(mono) > MATCH_RATE * BUFFER_SEC:
            evq.put(("log", f"警告: {os.path.basename(path)} 时长超过缓冲区, 可能无法匹配"))
        templates[name] = mono
        evq.put(("log", f"已加载模板 {name}: {os.path.basename(path)} ({len(mono) / MATCH_RATE:.2f}s)"))
    return templates


class LoopbackSource:
    """监听整个系统声音: 默认输出设备的回环录音"""

    def __init__(self, evq):
        self.evq = evq
        self._rec = None
        self._speaker_name = "?"

    def describe(self):
        return f"整个系统声音 ({self._speaker_name})"

    def open(self):
        import soundcard as sc
        try:
            speaker = sc.default_speaker()
            self._speaker_name = speaker.name
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as e:
            raise RuntimeError(f"无法打开系统声音监听(需要回环录音设备): {e}")
        self._rec = mic.recorder(samplerate=SAMPLE_RATE).__enter__()

    def read(self):
        return self._rec.record(numframes=int(SAMPLE_RATE * CHUNK_SEC))

    def close(self):
        if self._rec is not None:
            try:
                self._rec.__exit__(None, None, None)
            except Exception:
                pass
            self._rec = None


class AudioDetector(threading.Thread):
    """后台线程: 从音源采集音频, 与模板比对, 命中则发事件"""

    def __init__(self, evq, shared, source, fallback_source=None):
        super().__init__(daemon=True)
        self.evq = evq
        self.shared = shared   # {"success_on": bool, "fail_on": bool}
        self.source = source
        self.fallback_source = fallback_source
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import numpy as np
        self.templates = load_templates(self.evq)
        if not self.templates:
            self.evq.put(("detector_down", "没有加载到任何比对音频 (请将 success.wav / fail.wav 放入 compare 文件夹)"))
            return
        try:
            self.source.open()
        except Exception as e:
            if self.fallback_source is not None:
                self.evq.put(("log", f"进程音频捕获失败, 退回监听整个系统声音 ({e})"))
                self.source = self.fallback_source
                try:
                    self.source.open()
                except Exception as e2:
                    self.evq.put(("detector_down", f"无法开始监听: {e2}"))
                    return
            else:
                self.evq.put(("detector_down", f"无法开始监听: {e}"))
                return
        self.evq.put(("log", f"开始监听 ({self.source.describe()})"))
        max_len = int(MATCH_RATE * BUFFER_SEC)
        buf = np.zeros(0, dtype=np.float32)
        next_ok = 0.0
        try:
            while not self._stop.is_set():
                try:
                    data = self.source.read()
                except StopIteration:
                    break
                mono = data.mean(axis=1).astype(np.float32)
                buf = np.concatenate((buf, mono[::SAMPLE_RATE // MATCH_RATE]))
                if len(buf) > max_len:
                    buf = buf[-max_len:]
                if time.monotonic() < next_ok:
                    continue
                best = None
                for name, tmpl in self.templates.items():
                    if not self.shared.get(f"{name}_on", True):
                        continue
                    c = normalized_max_corr(buf, tmpl)
                    s = spectral_score(buf, tmpl)
                    score = max(c, s)
                    if score > THRESHOLD and (best is None or score > best[1]):
                        best = (name, c, s, score)
                if best:
                    self.evq.put(("trigger", best[0], best[1], best[2]))
                    buf = np.zeros(0, dtype=np.float32)
                    next_ok = time.monotonic() + DEBOUNCE_SEC
        except Exception as e:
            if not self._stop.is_set():
                self.evq.put(("detector_down", f"监听异常: {e}"))
        finally:
            try:
                self.source.close()
            except Exception:
                pass
        if self._stop.is_set():
            self.evq.put(("log", "监听已停止"))
        elif getattr(self.source, "_error", None):
            self.evq.put(("detector_down", f"监听中断: {self.source._error}"))


# ---------------- 进程级音频捕获 (proctap: WASAPI 进程回环) ----------------

def list_audio_sessions():
    """枚举当前有音频会话的进程, 返回 [(pid, name)]"""
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return []
    out = {}
    try:
        for s in AudioUtilities.GetAllSessions():
            if s.Process:
                out[s.Process.pid] = s.Process.name()
    except Exception:
        pass
    return sorted(out.items(), key=lambda x: x[1].lower())


class ProcessSource:
    """捕获指定进程的声音 (proctap / WASAPI 进程回环); 游戏声音照常从音箱播出"""

    def __init__(self, evq, pid, pname):
        self.evq = evq
        self.pid = pid
        self.pname = pname
        self._tap = None
        self._done = threading.Event()
        self._acc = []
        self._acc_frames = 0
        self._chunk_frames = int(SAMPLE_RATE * CHUNK_SEC)

    def describe(self):
        return f"指定进程 ({self.pname}, PID {self.pid})"

    def open(self):
        try:
            from proctap import ProcessAudioCapture
        except ImportError:
            raise RuntimeError("缺少 proctap 库, 请运行 pip install proc-tap")
        try:
            self._tap = ProcessAudioCapture(self.pid)
            self._tap.start()
        except Exception as e:
            self._tap = None
            raise RuntimeError(f"进程音频捕获启动失败: {e}")

    def read(self):
        import numpy as np
        last_data = time.time()
        while True:
            if self._done.is_set() or self._tap is None:
                raise StopIteration
            try:
                data = self._tap.read(timeout=0.5)
            except Exception as e:
                self.evq.put(("detector_down", f"进程音频捕获异常: {e}"))
                raise StopIteration
            if data:
                last_data = time.time()
                a = np.frombuffer(data, np.float32).reshape(-1, 2)  # 48kHz/2ch/float32
                self._acc.append(a)
                self._acc_frames += len(a)
                if self._acc_frames >= self._chunk_frames:
                    big = np.concatenate(self._acc)
                    chunk, rest = big[:self._chunk_frames], big[self._chunk_frames:]
                    self._acc = [rest] if len(rest) else []
                    self._acc_frames = len(rest)
                    return chunk
            elif time.time() - last_data > 10:
                self.evq.put(("detector_down", f"进程 {self.pname} 长时间无音频数据, 捕获已中断"))
                raise StopIteration

    def close(self):
        self._done.set()
        if self._tap is not None:
            try:
                self._tap.close()
            except Exception:
                pass


# ---------------- 音频播放 ----------------
class PlaybackManager:
    """单线程播放器: 预加载音频; 新命令打断当前播放, 立即按新倍率从头播放"""

    def __init__(self, evq):
        self.evq = evq
        self.gen = 0
        self._protect_until = 0.0   # 保护窗口结束时间: 期间不允许打断
        self.cmd = queue.Queue(maxsize=1)   # 只保留最新一条命令
        self._audio = {}
        self._speaker = None
        self._nch = 2
        self.sound_gain = 1.0   # 音效播放音量(0~1, 界面可调)
        threading.Thread(target=self._worker, daemon=True).start()

    def play(self, rate, sound="aowu", flash=True, protect_sec=0):
        """请求播放 (可选闪现音效 +) 选定音效(倍率 rate); 若有正在播放的音效则打断。
        protect_sec>0 时, 该次播放开始的 protect_sec 秒内不允许任何打断"""
        self.gen += 1
        if protect_sec > 0:
            self._protect_until = time.monotonic() + protect_sec
        try:
            self.cmd.get_nowait()
        except queue.Empty:
            pass
        try:
            self.cmd.put_nowait((rate, sound, flash, self.gen))
        except queue.Full:
            pass

    def stop(self):
        """打断当前播放(不播放新内容); 保护窗口内不生效, 返回是否执行了打断"""
        if time.monotonic() < self._protect_until:
            return False
        self.gen += 1
        return True

    def _interrupted(self, gen):
        if time.monotonic() < self._protect_until:
            return False
        return self.gen != gen

    # ---------- 工作线程 ----------
    def _worker(self):
        self._preload()
        while True:
            rate, sound, flash, gen = self.cmd.get()
            try:
                self._play_sequence(rate, sound, flash, gen)
            except Exception as e:
                self.evq.put(("error", f"播放异常: {e}"))

    def _preload(self):
        try:
            import numpy as np
            import soundcard as sc
            import soundfile as sf
            from scipy.signal import resample_poly  # noqa: F401  提前导入, 避免首次播放卡顿
        except ImportError as e:
            self.evq.put(("error", f"缺少播放依赖: {e}"))
            return
        try:
            self._speaker = sc.default_speaker()
            nch = getattr(self._speaker, "channels", 2)
            try:
                nch = int(nch)
            except Exception:
                nch = 2
            self._nch = nch if nch >= 1 else 2
        except Exception as e:
            self.evq.put(("error", f"无法打开播放设备: {e}"))
        for path in [FLASH_WAV] + [p for _, p in SOUND_FILES.values()]:
            if not os.path.exists(path):
                self.evq.put(("error", f"找不到播放音频: {path}"))
                continue
            try:
                data, sr = sf.read(path, dtype="float32", always_2d=True)
                self._audio[path] = (data, sr)
                self.evq.put(("log", f"已预加载音频: {os.path.basename(path)} ({len(data) / sr:.1f}s)"))
            except Exception as e:
                self.evq.put(("error", f"读取 {os.path.basename(path)} 失败: {e}"))

    def _play_sequence(self, rate, sound, flash, gen):
        if self._interrupted(gen):
            return
        seq = [(FLASH_WAV, 1.0, FLASH_GAIN)] if flash else []
        seq.append((SOUND_FILES.get(sound, SOUND_FILES["aowu"])[1], rate, self.sound_gain))
        for path, r, gain in seq:
            if self._interrupted(gen):
                break
            self._play_file(path, r, gain, gen)
        if self._interrupted(gen):
            self.evq.put(("log", "播放已打断"))

    def _play_file(self, path, r, gain, gen):
        import numpy as np
        cached = self._audio.get(path)
        if cached is None:
            self.evq.put(("error", f"播放音频未预加载: {os.path.basename(path)}"))
            return
        data, sr = cached
        if r != 1.0:
            from fractions import Fraction
            from scipy.signal import resample_poly
            f = Fraction(r).limit_denominator(200)
            data = resample_poly(data, f.denominator, f.numerator, axis=0).astype(np.float32)
        if gain != 1.0:
            data = np.clip(data * gain, -1.0, 1.0)
        if self._interrupted(gen):
            return
        nch = self._nch
        if data.shape[1] != nch:
            if data.shape[1] == 1:
                data = np.repeat(data, nch, axis=1)
            elif data.shape[1] > nch:
                data = data[:, :nch]
            else:
                data = np.pad(data, ((0, 0), (0, nch - data.shape[1])))
        if self._speaker is None:
            self.evq.put(("error", "无法打开播放设备"))
            return
        block = max(int(sr * 0.08), 1)   # 80ms 一块, 便于快速打断
        try:
            with self._speaker.player(samplerate=sr) as player:
                for i in range(0, len(data), block):
                    if self._interrupted(gen):
                        break
                    player.play(data[i:i + block])
        except Exception as e:
            if not self._interrupted(gen):
                self.evq.put(("error", f"播放 {os.path.basename(path)} 失败: {e}"))


# ---------------- 全屏图片 ----------------
class ImageFlash:
    """全屏图片叠加层: 常驻窗口, 启动时应用样式, 之后只用透明度控制显隐, 不抢焦点、点击穿透"""

    def __init__(self, root, evq):
        self.root = root
        self.evq = evq
        self._win = None
        self._label = None
        self._photo = None
        self._hwnd = None
        self._jobs = []
        self._prepare_window()

    def _prepare_window(self):
        """启动时创建一次全屏窗口并应用样式; 触发时不再新建窗口, 避免抢焦点"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{sw}x{sh}+-32000+-32000")  # 在屏幕外完成首次映射
        self._label = tk.Label(win)
        self._label.place(x=0, y=0, relwidth=1, relheight=1)
        win.update()
        self._hwnd = self._make_click_through(win)
        self._set_alpha(self._hwnd, 0)   # 全透明待命
        win.geometry(f"{sw}x{sh}+0+0")
        self._win = win

    def show(self, black_bg=False, user_img=None):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.evq.put(("error", "缺少 Pillow, 图片功能不可用"))
            return
        paths = []
        if user_img and os.path.exists(user_img):
            paths.append(user_img)
        paths.append(AOWU_IMG if os.path.exists(AOWU_IMG) else AOWU_IMG_JPG)
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            img = None
            for p in paths:
                try:
                    img = Image.open(p).convert("RGBA")
                    break
                except Exception:
                    continue
            if img is None:
                self.evq.put(("error", "找不到可用图片"))
                return
            iw, ih = img.size
            ratio = sh / ih          # 上下铺满屏幕
            img = img.resize((max(1, int(iw * ratio)), sh), Image.LANCZOS)
            bg = (0, 0, 0) if black_bg else (255, 255, 255)
            canvas = Image.new("RGB", (sw, sh), bg)
            if img.width >= sw:      # 太宽则左右居中裁切
                left = (img.width - sw) // 2
                img = img.crop((left, 0, left + sw, sh))
                canvas.paste(img, (0, 0), img)
            else:                    # 不够宽则居中, 两侧留白
                canvas.paste(img, ((sw - img.width) // 2, 0), img)
            self._photo = ImageTk.PhotoImage(canvas)
            self._label.config(image=self._photo)
            self._set_alpha(self._hwnd, int(255 * INITIAL_ALPHA))
            self._start_fade()
        except Exception as e:
            self.evq.put(("error", f"图片显示失败: {e}"))

    def _start_fade(self):
        for job in self._jobs:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._jobs = [self.root.after(int(FADE_HOLD_SEC * 1000), self._fade, 1)]

    def _fade(self, i):
        if i > FADE_STEPS:
            self._set_alpha(self._hwnd, 0)
            self._jobs = []
            return
        a = int(255 * INITIAL_ALPHA * (1 - i / FADE_STEPS))
        self._set_alpha(self._hwnd, a)
        self._jobs.append(self.root.after(int(FADE_DURATION_SEC * 1000 / FADE_STEPS), self._fade, i + 1))

    def _make_click_through(self, win):
        """Windows: 设置分层+鼠标穿透+禁止激活, 返回顶层窗口句柄(失败返回 None)"""
        if sys.platform != "win32":
            return None
        import ctypes
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            user32 = ctypes.windll.user32
            hwnd = win.winfo_id()
            for _ in range(5):  # 向上找到真正的顶层窗口(TkTopLevel)
                parent = user32.GetParent(hwnd)
                if not parent:
                    break
                hwnd = parent
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                 ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
            return hwnd
        except Exception:
            return None

    def _set_alpha(self, hwnd, alpha):
        if hwnd is None:
            if self._win is not None:  # 非 Windows 回退到 tk 的 alpha
                try:
                    self._win.attributes("-alpha", max(0.0, min(1.0, alpha / 255.0)))
                except tk.TclError:
                    pass
            return
        import ctypes
        LWA_ALPHA = 0x2
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, int(alpha), LWA_ALPHA)


# ---------------- 主界面 ----------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("洛克王国之狼来了")
        try:
            root.iconbitmap(ICON_PATH)
        except Exception:
            pass
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.count = 0          # 总触发次数(真实累计, 不自动清零)
        self.success_count = 0  # 捕捉成功次数
        self.fail_count = 0     # 捕捉失败次数
        self.hit_count = 0      # 球命中次数
        self.rate_count = 0     # 倍率进度(超过上限时归零, 驱动倍率)

        settings = load_settings()
        try:
            mn = float(settings.get("min_rate", MIN_RATE))
            mx = float(settings.get("max_rate", MAX_RATE))
            if mn < 0.1 or mx <= mn:
                raise ValueError
        except (TypeError, ValueError):
            mn, mx = MIN_RATE, MAX_RATE
        self.min_rate, self.max_rate = mn, mx
        try:
            rl = float(settings.get("rate_lock_val", 1.0))
            if rl <= 0:
                raise ValueError
        except (TypeError, ValueError):
            rl = 1.0
        self.rate_lock_val = rl
        self.user_img = str(settings.get("user_img", "") or "")
        self._vol_init = float(settings.get("aowu_volume", 70)) / 100.0
        self.enable_var = tk.BooleanVar(value=False)
        self.success_var = tk.BooleanVar(value=bool(settings.get("success_on", 1)))
        self.fail_var = tk.BooleanVar(value=bool(settings.get("fail_on", 1)))
        self.hit_var = tk.BooleanVar(value=bool(settings.get("hit_on", 0)))
        self.image_var = tk.BooleanVar(value=bool(settings.get("image_on", 1)))
        self.black_bg_var = tk.BooleanVar(value=bool(settings.get("black_bg", 0)))
        self.sfx_success_var = tk.StringVar(value=str(settings.get("sfx_success", "aowu")))
        self.sfx_fail_var = tk.StringVar(value=str(settings.get("sfx_fail", "aowu")))
        self.sfx_hit_var = tk.StringVar(value=str(settings.get("sfx_hit", "aowu")))
        self.flash_var = tk.BooleanVar(value=bool(settings.get("flash_on", 1)))
        self.rate_lock_var = tk.BooleanVar(value=bool(settings.get("rate_lock", 0)))
        self.shiny_var = tk.BooleanVar(value=bool(settings.get("shiny_on", 1)))
        self.sfx_shiny_var = tk.StringVar(value=str(settings.get("sfx_shiny", "let_it_go")))

        self.shared = {"success_on": self.success_var.get(),
                       "fail_on": self.fail_var.get(),
                       "hit_on": self.hit_var.get(),
                       "shiny_on": self.shiny_var.get()}

        def _bind(var, key, in_shared):
            def cb(*_):
                if in_shared:
                    self.shared[key] = var.get()
                save_setting(key, var.get())
            var.trace_add("write", cb)

        _bind(self.success_var, "success_on", True)
        _bind(self.fail_var, "fail_on", True)
        _bind(self.hit_var, "hit_on", True)
        _bind(self.image_var, "image_on", False)
        _bind(self.black_bg_var, "black_bg", False)
        _bind(self.sfx_success_var, "sfx_success", False)
        _bind(self.sfx_fail_var, "sfx_fail", False)
        _bind(self.sfx_hit_var, "sfx_hit", False)
        _bind(self.flash_var, "flash_on", False)
        _bind(self.rate_lock_var, "rate_lock", False)
        _bind(self.shiny_var, "shiny_on", True)
        _bind(self.sfx_shiny_var, "sfx_shiny", False)

        self.evq = queue.Queue()
        self.detector = None
        self.flash = ImageFlash(root, self.evq)
        self.player = PlaybackManager(self.evq)

        self._build_ui()
        self._update_img_label()
        self._tray = None
        self._setup_tray()
        self._log("就绪。勾选「启动监听功能」后开始监听 (优先捕获指定进程声音, 失败自动退回系统声音)")
        self.root.after(100, self._poll_events)

    # ---------- 界面 ----------
    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=14)
        frm.pack(fill="both", expand=True)

        row = 0
        ttk.Checkbutton(frm, text="启动监听功能", variable=self.enable_var,
                        command=self._on_enable).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))

        row += 1
        stat = ttk.LabelFrame(frm, text="统计", padding=8)
        stat.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.hit_lbl = ttk.Label(stat, text="球命中次数: 0")
        self.hit_lbl.grid(row=0, column=0, sticky="w")
        self.success_rate_lbl = ttk.Label(stat, text="成功率: --")
        self.success_rate_lbl.grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.success_lbl = ttk.Label(stat, text="捕捉成功次数: 0")
        self.success_lbl.grid(row=1, column=0, sticky="w")
        self.fail_rate_lbl = ttk.Label(stat, text="失败率: --")
        self.fail_rate_lbl.grid(row=1, column=1, sticky="w", padx=(24, 0))
        self.fail_lbl = ttk.Label(stat, text="捕捉失败次数: 0")
        self.fail_lbl.grid(row=2, column=0, sticky="w")
        self.rate_lbl = ttk.Label(stat, text=f"当前倍率: x{self.min_rate:.2f}", font=("", 10, "bold"))
        self.rate_lbl.grid(row=2, column=1, sticky="w", padx=(24, 0))

        row += 1
        cond = ttk.LabelFrame(frm, text="音效设置", padding=8)
        cond.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        def radio_row(r, text, chk_var, sfx_var, order):
            ttk.Checkbutton(cond, text=text, variable=chk_var).grid(row=r, column=0, sticky="w", padx=(0, 8))
            rbs = []
            for ci, val in enumerate(order):
                rb = ttk.Radiobutton(cond, text=SOUND_FILES[val][0], value=val, variable=sfx_var)
                rb.grid(row=r, column=1 + ci, sticky="w", padx=(0, 4))
                rbs.append(rb)

            def sync(*_):
                st = ["!disabled"] if chk_var.get() else ["disabled"]
                for rb in rbs:
                    rb.state(st)
            chk_var.trace_add("write", sync)
            sync()

        radio_row(0, "捕捉成功", self.success_var, self.sfx_success_var, SOUND_ORDER)
        radio_row(1, "捕捉失败", self.fail_var, self.sfx_fail_var, SOUND_ORDER)
        radio_row(2, "球命中", self.hit_var, self.sfx_hit_var, SOUND_ORDER)
        radio_row(3, "战斗内出异色", self.shiny_var, self.sfx_shiny_var, SHINY_SOUND_ORDER)

        row += 1
        rate_frm = ttk.LabelFrame(frm, text="倍率设置", padding=8)
        rate_frm.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(rate_frm, text="最低倍率:").grid(row=0, column=0, sticky="w")
        self.min_entry = ttk.Entry(rate_frm, width=6)
        self.min_entry.insert(0, f"{self.min_rate:g}")
        self.min_entry.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(rate_frm, text="最高倍率:").grid(row=0, column=2, sticky="w")
        self.max_entry = ttk.Entry(rate_frm, width=6)
        self.max_entry.insert(0, f"{self.max_rate:g}")
        self.max_entry.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Checkbutton(rate_frm, text="固定倍率:", variable=self.rate_lock_var).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.rate_lock_entry = ttk.Entry(rate_frm, width=6)
        self.rate_lock_entry.insert(0, f"{self.rate_lock_val:g}")
        self.rate_lock_entry.grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Button(rate_frm, text="应用", width=6, command=self._on_apply_rates).grid(row=0, column=4, rowspan=2, padx=(6, 0))

        row += 1
        vol = ttk.LabelFrame(frm, text="播放设置", padding=8)
        vol.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(vol, text="音乐音量:").grid(row=0, column=0, sticky="w")
        self.vol_var = tk.DoubleVar(value=self._vol_init)
        self.vol_scale = ttk.Scale(vol, from_=0.0, to=1.0, variable=self.vol_var,
                                   length=120, command=self._on_volume)
        self.vol_scale.grid(row=0, column=1, sticky="w", padx=6)
        self.vol_scale.bind("<ButtonPress-1>", self._scale_press)
        self.vol_lbl = ttk.Label(vol, text=f"{int(self._vol_init * 100)}%", width=4, anchor="e")
        self.vol_lbl.grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Button(vol, text="-", width=3, command=lambda: self._step_volume(-0.1)).grid(row=0, column=3)
        ttk.Button(vol, text="+", width=3, command=lambda: self._step_volume(0.1)).grid(row=0, column=4, padx=(4, 0))
        ttk.Checkbutton(vol, text="闪现音效", variable=self.flash_var).grid(row=1, column=0, sticky="w", pady=(4, 0))

        row += 1
        disp = ttk.LabelFrame(frm, text="显示设置", padding=8)
        disp.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Checkbutton(disp, text="显示图片", variable=self.image_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(disp, text="黑色背景", variable=self.black_bg_var).grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Button(disp, text="选择图片", width=10, command=self._on_choose_image).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(disp, text="恢复默认", width=10, command=self._on_reset_image).grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        self.img_lbl = ttk.Label(disp, text="", foreground="#888888")
        self.img_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        row += 1
        test = ttk.LabelFrame(frm, text="测试", padding=8)
        test.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(test, text="捕捉成功", width=10, command=lambda: self._on_test_type("success")).grid(row=0, column=0, sticky="w")
        ttk.Button(test, text="捕捉失败", width=10, command=lambda: self._on_test_type("fail")).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Button(test, text="球命中", width=10, command=lambda: self._on_test_type("hit")).grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Button(test, text="出异色", width=8, command=self._on_test_shiny).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Button(test, text="+1", width=5, command=self._on_plus_one).grid(row=0, column=4, sticky="w", padx=(10, 0))
        ttk.Button(test, text="打断", width=5, command=self._on_interrupt).grid(row=0, column=5, sticky="w", padx=(6, 0))

        row += 1
        self.log_text = tk.Text(frm, height=12, width=66, state="disabled", wrap="word",
                                bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self.log_text.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        sb = ttk.Scrollbar(frm, command=self.log_text.yview)
        sb.grid(row=row, column=2, sticky="ns", pady=(8, 0))
        self.log_text.config(yscrollcommand=sb.set)
        frm.rowconfigure(row, weight=1)

    # ---------- 倍率 ----------
    def _current_rate(self):
        if self.rate_lock_var.get():
            return self.rate_lock_val
        return self.min_rate + 0.1 * self.rate_count

    def _update_count_label(self):
        self.hit_lbl.config(text=f"球命中次数: {self.hit_count}")
        self.success_lbl.config(text=f"捕捉成功次数: {self.success_count}")
        self.fail_lbl.config(text=f"捕捉失败次数: {self.fail_count}")
        total = self.success_count + self.fail_count
        if total > 0:
            sr = f"{self.success_count / total * 100:.1f}%"
            fr = f"{self.fail_count / total * 100:.1f}%"
        else:
            sr = fr = "--"
        self.success_rate_lbl.config(text=f"成功率: {sr}")
        self.fail_rate_lbl.config(text=f"失败率: {fr}")
        self.rate_lbl.config(text=f"当前倍率: x{self._current_rate():.2f}")

    def _check_shiny(self):
        self._shiny_pending = False
        try:
            shot = self._capture_game()
        except Exception as e:
            self._log(f"截图失败: {e}")
            return
        try:
            from PIL import Image
            tpl = Image.open(SHINY_TAG_IMG).convert("L")
        except Exception:
            self._log(f"找不到异色标签图片: {SHINY_TAG_IMG}")
            return
        try:
            score = image_contains(shot, tpl)
        except Exception as e:
            self._log(f"图片匹配失败: {e}")
            return
        if score > IMG_MATCH_THRESHOLD:
            sound = self.sfx_shiny_var.get()
            disp = SOUND_FILES.get(sound, SOUND_FILES["let_it_go"])[0]
            self._log(f"检测到异色! (图片匹配度 {score:.2f}), 播放 {disp} ({SHINY_PROTECT_SEC:g}秒内禁止打断)")
            self.player.play(self._current_rate(), sound, self.flash_var.get(),
                             protect_sec=SHINY_PROTECT_SEC)
        else:
            self._log(f"截图检测完成, 未发现异色 (匹配度 {score:.2f})")

    def _capture_game(self):
        from PIL import ImageGrab
        hwnd = self._find_game_hwnd()
        if hwnd:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        return ImageGrab.grab()

    def _find_game_hwnd(self):
        pid = getattr(self, "_game_pid", None)
        if not pid:
            return None
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        found = []

        def enum(hwnd, lparam):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(cb(enum), 0)
        return found[0] if found else None

    def _on_test_shiny(self):
        if getattr(self, "_shiny_pending", False):
            self._log("异色检测已在进行中")
            return
        self._shiny_pending = True
        self._log("测试「战斗内出异色」: 1秒后截图检测异色")
        self.root.after(1000, self._check_shiny)

    def _on_interrupt(self):
        if self.player.stop():
            self._log("已打断当前播放")
        else:
            self._log(f"保护中(战斗内出异色), {SHINY_PROTECT_SEC:g}秒内不可打断")

    def _update_img_label(self):
        if self.user_img:
            self.img_lbl.config(text=f"自定义: {os.path.basename(self.user_img)}")
        else:
            self.img_lbl.config(text="默认图片")

    def _on_choose_image(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择弹图图片",
            filetypes=[("PNG 图片", "*.png"), ("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            from PIL import Image
            Image.open(path).convert("RGBA")
        except Exception as e:
            self._log(f"图片无法读取: {e}")
            return
        self.user_img = path
        save_setting("user_img", path)
        self._update_img_label()
        self._log(f"自定义图片已设置: {path}")

    def _on_reset_image(self):
        self.user_img = ""
        save_setting("user_img", "")
        self._update_img_label()
        self._log("已恢复使用默认图片")

    def _on_apply_rates(self):
        try:
            mn = float(self.min_entry.get())
            mx = float(self.max_entry.get())
        except ValueError:
            self._log("倍率必须是数字")
            return
        if mn < 0.1:
            self._log("最低倍率不能低于 0.1")
            return
        if mx <= mn:
            self._log("最高倍率必须大于最低倍率")
            return
        if self.rate_lock_var.get():
            try:
                rl = float(self.rate_lock_entry.get())
            except ValueError:
                self._log("固定倍率必须是数字")
                return
            if rl <= 0:
                self._log("固定倍率必须大于 0")
                return
            self.rate_lock_val = rl
            save_setting("rate_lock_val", f"{rl:g}")
        self.min_rate, self.max_rate = mn, mx
        save_setting("min_rate", f"{mn:g}")
        save_setting("max_rate", f"{mx:g}")
        self._update_count_label()
        if self.rate_lock_var.get():
            self._log(f"倍率已更新: {mn:g} ~ {mx:g}, 固定倍率 x{self.rate_lock_val:g}")
        else:
            self._log(f"倍率已更新: {mn:g} ~ {mx:g}")

    def _on_volume(self, v):
        try:
            val = float(v)
        except ValueError:
            return
        val = max(0.0, min(1.0, val))
        self.player.sound_gain = val
        self.vol_lbl.config(text=f"{int(val * 100)}%")
        save_setting("aowu_volume", int(val * 100))

    def _step_volume(self, delta):
        v = max(0.0, min(1.0, self.vol_var.get() + delta))
        self.vol_var.set(v)
        self._on_volume(v)

    def _scale_press(self, event):
        """点击滑槽任意位置: 直接跳到对应百分比; 点在滑块上则交给默认拖动"""
        w = self.vol_scale.winfo_width()
        if w <= 24:
            return
        pad = 10
        cur = self.vol_var.get()
        knob_x = pad + cur * (w - 2 * pad)
        if abs(event.x - knob_x) <= 12:
            return
        v = max(0.0, min(1.0, (event.x - pad) / (w - 2 * pad)))
        self.vol_var.set(v)
        self._on_volume(v)
        return "break"

    # ---------- 开关与触发 ----------
    def _on_enable(self):
        if self.enable_var.get():
            pname = PROCESS_NAME
            pid, found = None, None
            if pname:
                for p, n in list_audio_sessions():
                    if n.lower() == pname.lower():
                        pid, found = p, n
                        break
            if pid is None:
                self._log(f"未找到监听进程 {pname or '(未配置)'}, 退回监听整个系统声音")
                source = LoopbackSource(self.evq)
                fallback = None
            else:
                source = ProcessSource(self.evq, pid, found)
                fallback = LoopbackSource(self.evq)
            self._game_pid = pid
            self.detector = AudioDetector(self.evq, self.shared, source, fallback)
            self.detector.start()
        else:
            if self.detector:
                self.detector.stop()
                self.detector = None

    def _increment_count(self, name):
        """分类统计 +1(永不自动清零); 倍率进度 +1, 超过上限时归零; 返回是否重置了倍率"""
        self.count += 1
        if name == "success":
            self.success_count += 1
        elif name == "fail":
            self.fail_count += 1
        elif name == "hit":
            self.hit_count += 1
        self.rate_count += 1
        if not self.rate_lock_var.get() and self._current_rate() > self.max_rate:
            self.rate_count = 0
            return True
        return False

    def _on_trigger(self, name, corr, spec=0.0):
        label = {"success": "捕捉成功", "fail": "捕捉失败", "hit": "球命中",
                 "shiny": "战斗内出异色"}.get(name, name)
        if corr > MAX_CORR:
            self._log(f"检测到「{label}」音效, 相关度 {corr:.2f} 超过 {MAX_CORR:g}, 已忽略不做操作")
            return
        if name == "shiny":
            if getattr(self, "_shiny_pending", False):
                return
            self._shiny_pending = True
            self._log(f"检测到「{label}」触发音效 (波形 {corr:.2f} / 频谱 {spec:.2f}), 1秒后截图检测异色")
            self.root.after(1000, self._check_shiny)
            return
        if self._increment_count(name):
            self._log(f"倍率超过上限 {self.max_rate:g}, 倍率回到 {self.min_rate:g} (统计保持 {self.count})")
        rate = self._current_rate()
        self._log(f"检测到「{label}」音效 (波形 {corr:.2f} / 频谱 {spec:.2f}) → 统计 {self.count}, 播放倍率 x{rate:.2f}")
        self._play_and_flash(rate, name)

    def _on_plus_one(self):
        if self._increment_count("success"):
            self._log(f"倍率超过上限 {self.max_rate:g}, 倍率回到 {self.min_rate:g} (统计保持 {self.count})")
        rate = self._current_rate()
        self._log(f"+1 → 统计 {self.count}, 播放倍率 x{rate:.2f}")
        self._play_and_flash(rate, "success")

    def _play_and_flash(self, rate, name):
        self._update_count_label()
        sound = {"success": self.sfx_success_var.get(),
                 "fail": self.sfx_fail_var.get(),
                 "hit": self.sfx_hit_var.get()}.get(name, "aowu")
        self.player.play(rate, sound, self.flash_var.get())
        if self.image_var.get():
            self.flash.show(self.black_bg_var.get(), self.user_img)

    def _on_test_type(self, name):
        rate = self._current_rate()
        label = {"success": "捕捉成功", "fail": "捕捉失败", "hit": "球命中"}.get(name, name)
        sound = {"success": self.sfx_success_var.get(),
                 "fail": self.sfx_fail_var.get(),
                 "hit": self.sfx_hit_var.get()}.get(name, "aowu")
        disp = SOUND_FILES.get(sound, SOUND_FILES["aowu"])[0]
        self._log(f"测试「{label}」: 闪现音效{'开' if self.flash_var.get() else '关'} + {disp} (倍速 x{rate:.2f}) 并显示图片")
        self.player.play(rate, sound, self.flash_var.get())
        self.flash.show(self.black_bg_var.get(), self.user_img)

    # ---------- 日志与事件 ----------
    def _log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {text}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_events(self):
        try:
            while True:
                ev = self.evq.get_nowait()
                k = ev[0]
                if k == "trigger":
                    self._on_trigger(ev[1], ev[2], ev[3] if len(ev) > 3 else 0.0)
                elif k == "log":
                    self._log(ev[1])
                elif k == "error":
                    self._log("错误: " + ev[1])
                elif k == "detector_down":
                    self._log("错误: " + ev[1])
                    self.enable_var.set(False)
                    self.detector = None
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _show_main_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _setup_tray(self):
        """系统托盘图标: 显示主界面 / 退出"""
        try:
            import pystray
            from PIL import Image
        except ImportError:
            self._tray = None
            return
        try:
            img = Image.open(ICON_PATH)
            if img.width > 64:
                img = img.resize((64, 64))
        except Exception:
            img = Image.new("RGB", (64, 64), (60, 60, 60))
        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", lambda icon, item: self.root.after(0, self._show_main_window), default=True),
            pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self._on_close)),
        )
        self._tray = pystray.Icon("洛克王国之狼来了", img, "洛克王国之狼来了", menu)
        self._tray.run_detached()

    def _on_close(self):
        if self.detector:
            self.detector.stop()
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    elevated = ensure_admin()
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # 截图坐标与物理像素一致
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    if not elevated:
        app._log("提示: 未获得管理员权限, 若无法捕获游戏声音请以管理员身份重新运行")
    missing = check_missing_deps()
    if missing:
        app._log("缺少依赖: " + ", ".join(missing))
        messagebox.showwarning("缺少依赖",
                               "以下库未安装:\n" + ", ".join(missing) +
                               "\n\n请运行:\npip install " + " ".join(missing))
    root.mainloop()


if __name__ == "__main__":
    main()
