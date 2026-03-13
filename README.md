# KOOK 提醒机器人

[![Star](https://img.shields.io/github/stars/rnm330/Kook-remind-bot?style=flat&logo=github)](https://github.com/rnm330/Kook-remind-bot/stargazers)
[![Fork](https://img.shields.io/github/forks/rnm330/Kook-remind-bot?style=flat&logo=github)](https://github.com/rnm330/Kook-remind-bot/network/members)
[![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)](https://github.com/rnm330/Kook-remind-bot)
[![Docker](https://img.shields.io/badge/Docker-supported-blue?logo=docker)](https://github.com/rnm330/Kook-remind-bot)
[![License](https://img.shields.io/github/license/rnm330/Kook-remind-bot)](https://github.com/rnm330/Kook-remind-bot)

KOOK 频道机器人，支持定时提醒功能和 Web 管理界面。

## ✨ 功能特性

- **定时提醒**: 支持按分钟、小时、每天、每周创建提醒任务
- **Web 管理界面**: 可视化管理任务和发送消息
- **数据持久化**: 任务数据自动保存到 tasks.json
- **多频道支持**: 可在不同频道创建独立任务

## 🚀 快速开始

### 首次部署

1. **克隆项目**
```bash
git clone https://github.com/rnm330/Kook-remind-bot.git
cd Kook-remind-bot
```

2. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的 KOOK_BOT_TOKEN
```

3. **启动服务**
```bash
docker-compose up -d
```

4. **首次设置密码**
首次访问管理界面时，需要设置登录密码：
- 打开浏览器访问: `http://your-server-ip:8000`
- 输入您要设置的密码并确认
- 点击"设置密码"完成首次登录

### 更新部署

如果已存在数据文件，按以下步骤更新：

```bash
# 停止并更新
docker-compose down
git pull
docker-compose build
docker-compose up -d
```

数据目录 `./data/tasks.json` 会自动保留之前的任务数据。

## 📋 Bot 指令

在 KOOK 频道中使用以下指令：

- `/提醒 分钟 1 吃饭` - 每 1 分钟提醒
- `/提醒 小时 2 喝水` - 每 2 小时提醒
- `/提醒 每天 12:00 吃午饭` - 每天 12:00 提醒
- `/提醒 每周 四 19:00 开会` - 每周四 19:00 提醒
- `/查看提醒` - 查看当前频道的所有提醒
- `/删除提醒 r1` - 删除编号为 r1 的提醒
- `/测试` - 查看帮助信息

## 🌐 Web 管理界面

访问 `http://your-server-ip:8000` 进入管理界面，功能包括：

- **查看统计信息**: 显示总任务数、频道数、Bot 状态
- **任务列表**: 查看所有定时任务，支持搜索和删除
- **发送消息**: 手动发送消息到任意频道
- **刷新数据**: 实时更新任务列表

## 🖥️ 手动部署（不推荐）

1. 安装依赖
```bash
pip install -r requirements.txt
```

2. 配置环境变量
```bash
export KOOK_BOT_TOKEN="your_bot_token"
```

3. 启动服务
```bash
python app.py
```

4. 首次访问 `http://localhost:8000` 设置登录密码

## 📁 项目结构

```
kookbot/
├── app.py              # Bot 主程序
├── api_server.py       # FastAPI 后端服务
├── static/
│   └── index.html      # Vue 3 前端管理界面
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 镜像构建文件
├── docker-compose.yml  # Docker Compose 配置
├── .env                # 环境变量配置
└── tasks.json          # 任务数据持久化文件
```

## 🔧 API 接口

### 获取所有任务
```
GET /api/tasks
```

### 获取指定任务
```
GET /api/tasks/{show_id}
```

### 删除任务
```
DELETE /api/tasks/{show_id}
```

### 发送消息
```
POST /api/send-message
Content-Type: application/json

{
  "channel_id": "1234567890",
  "content": "消息内容"
}
```

### 获取统计信息
```
GET /api/stats
```

## 🔐 安全建议

1. 不要将 `.env` 文件提交到版本控制系统
2. 在生产环境中使用防火墙限制 Web 管理界面的访问
3. 定期备份 `tasks.json` 文件
4. 建议使用反向代理（如 Nginx）并配置 HTTPS

## 📝 注意事项

- 确保 Bot 已添加到目标频道并有发送消息的权限
- Web 管理界面默认监听 0.0.0.0:8000，可根据需要修改端口
- 时区默认设置为 Asia/Shanghai，可在代码中修改
- 任务数据会自动保存到 `tasks.json` 文件

## 🤝 致谢

- **KOOK 官方** - 提供优秀的即时通讯平台
- **khl.py** - Python SDK 开源库 (https://github.com/omoidesu/khl.py)
- **APScheduler** - 定时任务调度库

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
