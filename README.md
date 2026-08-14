# 洛克王国之狼来了

> ⚠️ **本项目的本质是整活。** ⚠️
>
> 一个纯娱乐向的小工具：监听《洛克王国：世界》的捕捉音效，然后在你耳边放"animals"。

## 它是干嘛的

开着游戏，勾上监听，然后：

1. 游戏里捕捉**成功 / 失败 / 球命中**时，程序通过 WASAPI 进程回环（ProcTap）静默旁听游戏进程的声音并识别音效
2. 识别命中后：
   - 播一声 `flash.wav`（音量 x2）
   - 再以倍速 + 变调播放 `aowu.wav`，倍率 = `0.3 + 0.1 × 进度`，超过 `4.0` 后回到 `0.3`（越捕捉越快越高，直到返祖）
   - 全屏弹一张 `aowu.png`（半透明出现，随后淡出；鼠标穿透、不抢焦点，绝不打扰你操作游戏）
3. 新触发会打断上一次播放，立即按新倍率重来
4. 界面上的统计次数是真实累计，绝不清零（除非你自己点，但那个按钮已经没了）

## 文件结构

```
compare/        比对音效: success.wav / fail.wav / hit.wav
play_material/  播放素材: flash.wav / aowu.wav
images/         弹图素材: aowu.png (透明图, 显示时自动加白底/黑底)
main.py         主程序 (tkinter 界面 + 监听 + 播放 + 弹图)
app.manifest    exe 的 UAC 管理员清单
```

## 怎么跑

### 方式一：直接用打包好的 exe

`dist\洛克王国之狼来了.exe`，双击即用。需要管理员权限（UAC 弹窗点"是"）。

### 方式二：运行 main.py

依赖：`pip install -r requirements.txt`（建议用项目自带的 .venv）

## 界面说明

- **启动监听功能**：总开关
- **播放条件**：捕捉成功 / 捕捉失败 / 球命中，各自可开关
- **播放音量**：只控制 aowu.wav 的音量，点击滑条直接跳对应百分比，拖动微调
- **显示设置**：是否弹图、白底/黑底
- **测试**：测试图片+音频效果、模拟触发 +1、打断当前播放

设置存在注册表里（`HKCU\Software\RockKingdomWolfComing`），重启自动恢复，不产生配置文件。

## 再打包

```bat
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed ^
  --name RockKingdomWolfComing ^
  --icon icon.ico ^
  --manifest app.manifest ^
  --add-data "compare;compare" ^
  --add-data "play_material;play_material" ^
  --add-data "images;images" ^
  --add-data "icon.ico;." ^
  --hidden-import proctap ^
  --hidden-import proctap.backends.windows ^
  --hidden-import proctap._native ^
  --hidden-import pycaw ^
  main.py

copy /y dist\RockKingdomWolfComing.exe "dist\洛克王国之狼来了.exe"
```

## 免责声明

整活项目，仅供娱乐。如果因为"奥呜"太魔性导致邻居投诉、舍友暴怒或宠物应激，本项目概不负责。
