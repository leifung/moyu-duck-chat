# tools —— 构建与发布脚本

## 为什么需要这些

主程序 `摸鱼鸭聊天版.py` 是**单文件**，但它由三部分拼出来：

| 源文件 | 作用 |
|---|---|
| `vendor/欸你听说没.py` | 原来的聊天工具。取其**通信内核**（常量 / 工具函数 / `Peer` / `ChatNode`，即 `ChatUI` 类之前的全部代码） |
| `vendor/moyu_duck.py` | 原来的摸鱼鸭。取其 **Qt 绑定 + 角色 + 绘制**（`Duck` / `Fish` / `Poop` / `Fx` / `draw_*`，即 `DesktopPet` 类之前的部分） |
| `_part3.py` | 合并版新写的部分：宠物层 `PetLayer`、消息联动舞台 `MsgStage`、Qt 聊天窗口 `ChatWindow`、控制器 `Controller`、入口 `main()` |

两个原本独立程序的**可复用部分原样保留**，界面层用 PyQt5 重写（原聊天界面是 Tkinter，
Tk 和 Qt 不能在同一进程里各跑一个主循环），所以才有这个拼接方案。

## 重新生成主程序

```bash
python3 tools/build.py
```

会覆盖上一级的 `摸鱼鸭聊天版.py`。

> ⚠️ 直接改生成出来的 `摸鱼鸭聊天版.py` 是没有意义的 —— 下次构建就没了。
> 改功能请改 `_part3.py`（或 vendor 里的原始程序），再跑 build.py。

## 拼接时的几个坑（改代码时注意）

- `moyu_duck.py` 里的 `import datetime` 会和内核的 `from datetime import datetime` 撞名，
  build.py 里已删掉这一行
- 两个源文件都定义了 `APP_NAME`，宠物那处改成 `PET_NAME`
- PyQt6 的 `QAction` 在 `QtGui` 而不是 `QtWidgets`；`QFontDatabase` 的导入也要按绑定分支

## 发布到 GitHub

见 `publish_to_github.py`。
