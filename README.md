# 洛克王国之狼来了

> ⚠️ **本项目的本质是整活。** ⚠️
>
> 一个纯娱乐向的小工具：监听《洛克王国：世界》的捕捉音效，然后在你耳边放"奥呜"。

## 它是干嘛的

开着游戏，勾上监听，然后：

1. 程序通过 WASAPI 进程回环（ProcTap）**静默旁听游戏进程的声音**（游戏声音照常从音箱播出），识别捕捉成功 / 失败 / 球命中音效（波形 + 频谱双通道融合匹配，抗背景音乐干扰）
2. 识别命中后：
   - （可选）播一声 `flash.wav` 闪现音效
   - 再以倍速 + 变调播放你为该类型选中的音效，倍率 = `最低倍率 + 0.1 × 进度`，超过最高倍率后回到最低倍率（越捕捉越快越高，直到返祖）
   - 全屏弹一张图片（半透明出现，随后淡出；鼠标穿透、不抢焦点，绝不打扰你操作游戏）
3. 新触发会**打断上一次播放**，立即按新倍率重来
4. **战斗内出异色**：检测到战斗结算音效后等待 1 秒，截图游戏画面并匹配异色标签，命中则播放所选歌曲（且 10 秒内禁止打断）
5. 统计区实时显示 命中/成功/失败 次数与成功率、失败率，真实累计绝不清零

## 文件结构

```
compare/        比对素材: success.wav / fail.wav / hit.wav /
                battle_clear_sprit.wav(异色触发音) / different_color_tag.png(异色标签图)
play_material/  播放素材: flash.wav(闪现音效, 不可选) + 任意数量的 wav/mp3/flac/ogg
images/         弹图素材: aowu.png (透明图, 显示时自动加白底/黑底)
main.py         主程序 (tkinter 界面 + 监听 + 播放 + 弹图 + 截图检测)
app.manifest    exe 的 UAC 管理员清单
```

**play_material 是动态的**：程序启动时自动扫描里面的所有音频文件（wav/mp3/flac/ogg，flash.wav 除外），文件名（不带后缀）直接出现在界面下拉框里，不需要改任何代码。

## 怎么跑

### 方式一：直接用打包好的 exe

`dist\洛克王国之狼来了.exe`，双击即用。需要管理员权限（UAC 弹窗点"是"）。托盘图标可显示主界面 / 退出。**单实例运行**：已在运行时再次启动会提示并退出。

### 方式二：运行 main.py

依赖：`pip install -r requirements.txt`（建议用项目自带的 .venv）

## 界面说明

- **启动监听功能**：总开关（优先捕获游戏进程声音，失败自动退回监听整个系统声音）
- **统计**：球命中 / 捕捉成功 / 捕捉失败次数 + 成功率 / 失败率 + 当前倍率
- **音效设置**：四类触发各自可开关，每行右侧：
  - **下拉框**：选择命中后播放的音效（播放列表 = play_material 里的全部音频，文件名不带后缀）
  - **试听**：原速播放当前选中的音效（不带闪现音效）
  - **中断**：立即停止当前播放
- **倍率设置**：最低/最高倍率可调（最低不能低于 0.1），或勾选"固定倍率"输入任意倍速
- **播放设置**：音乐音量（点击滑条直接跳对应百分比，-/+ 微调，只控制所选音效的音量）、闪现音效开关（对应 flash.wav）
- **显示设置**：是否弹图、白底/黑底、**选择图片**（上传自己的 PNG 作为弹图，未上传用默认）、恢复默认
- **测试**：捕捉成功 / 捕捉失败 / 球命中 / 出异色 分别测试对应流程，+1 模拟计数触发，打断停止当前播放

设置存在注册表里（`HKCU\Software\RockKingdomWolfComing`），重启自动恢复，不产生配置文件。

## 战斗内出异色的工作流程

1. 检测到 `battle_clear_sprit.wav` 音效
2. 等待 1 秒（等异色动画出现）
3. 截图游戏窗口（按进程 PID 定位；找不到窗口时截全屏）
4. 归一化互相关匹配 `different_color_tag.png`（阈值 0.5）
5. 命中 → 播放所选歌曲（跟随当前倍率/音量/闪现音效设置），**10 秒内禁止打断**；未命中 → 日志提示

> 注意：游戏需处于窗口化 / 无边框全屏模式才能截到画面（独占全屏截图可能全黑）。

## 加新音效（零代码）

1. 把 wav / mp3 / flac / ogg 文件丢进 `play_material/`（flash.wav 除外，它只做闪现音效）
2. 重新打包（见下）

完事。界面下拉框自动出现新文件（文件名即显示名），四类触发全部可选。

## 再打包

改完代码后执行：

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
  --hidden-import pystray._win32 ^
  main.py

copy /y dist\RockKingdomWolfComing.exe "dist\洛克王国之狼来了.exe"
```

> 单文件 exe 启动时会在系统临时目录释放内置文件（PyInstaller 单文件模式的固有行为），正常退出自动清理。

## 免责声明

整活项目，仅供娱乐。如果因为"奥呜"太魔性导致邻居投诉、舍友暴怒或宠物应激，本项目概不负责。
