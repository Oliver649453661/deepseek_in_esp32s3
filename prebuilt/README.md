# prebuilt —— 免编译固件

这里是可以**直接烧录**的 v7.1 固件，为「不想装 Arduino 环境，只想让桌宠跑起来」准备。

## 烧录

需要 Python（3.8+）和 esptool：

```powershell
python -m pip install esptool
cd prebuilt
.\flash.ps1 -Port COM4
```

一次写入四个区域，全自动复位。串口识别不到就先 `arduino-cli board list` 或看设备管理器；如果 921600 波特率连不上，加 `-Baud 115200`。

## 重要：凭据是占位符

本固件用 `secrets.example.h` 编译，**不含任何真实 WiFi 密码或 DeepSeek API Key**（可用 `strings` 或十六进制查看确认）。

结果是：

- ✅ 30 个动画、BOOT 按键互动、平滑切换 —— 全部正常
- ❌ 余额显示 —— 不会工作

想要余额功能，请填好自己的 `secrets.h`，按主 README 的「从源码编译烧录」重新编译并烧录。

## 文件与偏移

| 文件 | 烧录偏移 | 大小 | 说明 |
|---|---|---|---|
| `bootloader.bin` | `0x0` | 19.6 KB | 二级引导 |
| `partitions.bin` | `0x8000` | 3.0 KB | 分区表（必须与固件配套） |
| `boot_app0.bin` | `0xe000` | 8.0 KB | OTA 数据初始化，与 Arduino 官方烧录配方一致 |
| `firmware.bin` | `0x10000` | 1,103.2 KB | 应用程序 |
| `../generated/pet-assets.bin` | `0x210000` | 13,744.3 KB | 30 动作动画包 |

分区布局见 [`../partitions.csv`](../partitions.csv)。**不要**只刷 `firmware.bin` 而漏掉分区表和动画包，否则会停在 `assets not ready`。

## 编译条件

| 项目 | 值 |
|---|---|
| arduino-cli | 1.5.1 |
| ESP32 支持包 | `esp32:esp32` 3.2.1-cn |
| FQBN | `esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=custom` |
| 编译日期 | 2026-11-12 |
| 程序空间 | 1,129,530 / 2,097,152 字节 |
| 静态 RAM | 96,516 / 327,680 字节 |

与主 README「设备验证」一节记录的实机结果一致。

## 校验值（SHA-256）

```
a72dbf079fb8c2ab607f2d7d81640cdc51a39c37222e519ebc884760c742573e  prebuilt/bootloader.bin
a520d9347946bcbbf891e0171f34939f66a55bad1ed7bf6334fb2e69c8cbeeb5  prebuilt/partitions.bin
f94c5d786a7a8fab06ac5d10e33bf37711a6697636dc037559ea19cc410a17f0  prebuilt/boot_app0.bin
8e72e3b358ba9daf813016831a600cc23eda08e40ae6521a288f8acfac3e1a1e  prebuilt/firmware.bin
0ca8335edb380808f5256607eb2c217f530cb456beb0e3acce461e3d3d28dde6  generated/pet-assets.bin
```

最后一项与 [`../docs/validation/device-validation-v7_1.json`](../docs/validation/device-validation-v7_1.json) 里的 `asset_pack_sha256` 完全相同 —— 即这份动画包就是实机验证通过的那一份。

Linux / macOS 可用 `sha256sum -c`，Windows 可用 `Get-FileHash <file> -Algorithm SHA256` 自行比对。
