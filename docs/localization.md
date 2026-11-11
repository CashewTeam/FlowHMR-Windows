# 维护界面语言

界面支持 `zh-CN`、`zh-TW`、`en`、`ja`、`ko`、`ru`、`fr`。默认简体中文，切换时会保留当前素材和任务。Windows 桌面版还会保存语言偏好，重启服务或变更本地端口后仍能恢复。

- [界面字典](../tools/web/static/js/locales)按语言维护同一套消息键。静态标签使用 HTML 的 `data-i18n`（或 `data-i18n-title` 等），动态内容通过 [i18n.js](../tools/web/static/js/i18n.js) 的 `message(key, params)` 和 `setText`。插入值用 `{name}` 形式，由各译文自行调整位置。
- 服务端传 `error_code` / `error_params`，文件问题用 `detail_code`，运行环境选项用 `detail_code` / `detail_params`。用 `serverMessage` 查字典；原错误保留给旧版本和诊断日志。增加错误类型时先定一个稳定的键，再为每种语言补译文。不要把不同语言分支放到业务代码。
- 在界面加载前显示的 Windows 启动消息集中在 [desktop_messages.py](../tools/windows/desktop_messages.py)。新增语言时需要同时注册启动消息和界面字典。

修改后运行 `python -m unittest discover -s tests -p test_web_localization.py`，检查七套字典的键、占位符和页面引用一致。再在软件里切换语言，查看任务进度、模型与运行环境面板，以及容易因文字变长而拥挤的区域。
