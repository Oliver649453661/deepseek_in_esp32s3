# 历史 RGB565 方案

这里完整保留早期静态图和五动作版本的转换代码、生成头文件及源素材。当前 v7.1 主程序不使用这些文件。

- `convert.ps1`：静态 PNG 转 96×96 RGB565 头文件。
- `convert_frames.ps1`：五个 GIF 的全部帧及原始帧间隔转 RGB565 头文件；包含 Byte 位移溢出的修正。
- `whale_girl.h`、`whale_frames.h`：对应的历史生成结果。
- `assets/`：旧版 PNG 和五个 GIF。

脚本依赖 Windows PowerShell 的 `System.Drawing`，读写位置均相对于脚本所在目录。运行它们只会更新本目录的头文件，不会替换当前 v7.1 的动画包。

旧版素材来自 [luweiyabo/dsh-whale-pet](https://github.com/luweiyabo/dsh-whale-pet)。形象素材遵守 CC BY-NC-SA 4.0，授权与署名说明见项目根目录的 `ASSET_LICENSE.txt`。
