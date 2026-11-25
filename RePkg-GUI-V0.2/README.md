# RePkg GUI V0.2

Wallpaper Engine 动态壁纸提取工具，基于 [RePKG](https://github.com/notscuffed/repkg) 命令行的图形界面。
支持 `.pkg` / `.mpkg`，中文与空格路径，实时显示 RePKG 输出。

> 仅限个人娱乐使用，请勿用于商业用途。
> 解包与 TEX 转换能力来自 RePKG，版权归 NotScuffed 所有。

## 功能

- 选择 `.pkg` / `.mpkg` 文件和输出文件夹
- 后台调用同目录的 `RePKG.exe`，界面不卡顿
- 实时显示 RePKG 的 stdout / stderr，带退出码
- 路径支持中文、空格，不经过 shell，无引号转义问题
- 自动生成默认输出目录、一键打开输出文件夹
- `.pkg`：解包，并把 TEX 自动转换成 png/jpg
- `.mpkg`：自动转成临时 `.pkg` 交给 RePKG，结束后清理临时文件
- 可选人物背景，窗口固定 1380×800

## 环境要求

- Windows 10 / 11，64 位
- RePKG 0.4.0（.NET Framework 4.7.2；Win10/11 自带 4.8，无需另装）
- 仅从源码构建时需要 Python 3.10+（建议 3.12 / 3.13 / 3.14）

## 快速使用

1. 从 <https://github.com/notscuffed/repkg/releases> 下载 `RePKG.zip`，解压得到 `RePKG.exe`；
2. 把 `RePKG.exe` 和 `RePkg-GUI-V0.2.exe` 放在**同一个文件夹**；
3. 双击 `RePkg-GUI-V0.2.exe`；
4. 选择 `.pkg` / `.mpkg`，输出目录会自动填成 `包名_extracted`，点「开始提取」；
5. 完成后点「打开输出文件夹」。

> 不要把 exe 单独拿走，程序会优先在**自身所在目录**查找 `RePKG.exe`。

## 从源码构建

```bat
python -m pip install pyinstaller
build.bat
```

也可以手动执行：

```bat
python -m PyInstaller -F -w -n RePkg-GUI-V0.2 --clean --noconfirm --add-data "assets\bg_right.png;assets" repkg_gui.py
```

产物：`dist\RePkg-GUI-V0.2.exe`。

`-F` 生成单文件，`-w` 不显示控制台；子进程 RePKG 使用 `CREATE_NO_WINDOW`，也不会闪黑框。

## 目录结构

```
RePkg-GUI-V0.2/
├─ repkg_gui.py          # 主程序
├─ assets/
│  └─ bg_right.png       # 右侧背景图（可替换或删除）
├─ build.bat             # PyInstaller 打包脚本
├─ requirements-build.txt
├─ THIRD-PARTY.md
├─ README.md
└─ .gitignore
```

## 实现说明

- 子进程使用参数列表调用 RePKG，不经过 shell，中文和空格路径安全；
- stdout 与 stderr 合并，逐行解码（UTF-8 → GBK 兜底），通过队列和 `after(100)` 实时刷新；
- `.mpkg` 会创建临时 `.pkg` 硬链接/副本，因为 RePKG 0.4.0 只按扩展名识别；
  临时文件放在 `%TEMP%`，程序结束或异常都会清理；
- RePKG 查找顺序：程序同目录 `RePKG.exe` → 同目录 `repkg.exe`；
- 不联网、不写注册表、不需要管理员权限，只读取源包并写入你选择的输出目录。

## 已知限制

- RePKG 0.4.0 对采用压缩存储的条目可能无法正确解压（常见壁纸包一般未压缩）；
- 只接受 `.pkg` / `.mpkg`；
- 固定窗口尺寸为 1380×800，便于保持背景比例。

## 常见问题

### 双击 exe 提示 `Could not create temporary directory!`

PyInstaller 单文件程序启动时要先解压到 `%TEMP%`。部分杀毒软件（卡巴斯基、火绒等）会拦截新 exe 的这个行为。
把 `RePkg-GUI-V0.2.exe` 加入杀软信任/白名单，或改用 `-D` 目录模式打包即可。

### 提示找不到 RePKG.exe

确认 `RePKG.exe` 和 `RePkg-GUI-V0.2.exe` 在**同一个文件夹**，且 `RePKG.exe` 没有被改名。

## 版权与素材

- RePKG：MIT License，Copyright (c) NotScuffed，见 [THIRD-PARTY.md](THIRD-PARTY.md)；
- `assets/bg_right.png` 为用户自备的界面素材，不属于代码许可范围；
  公开分发或商用前请确认图片版权，或替换成自己的图片/删除。
