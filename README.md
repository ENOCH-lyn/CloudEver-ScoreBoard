# CloudEver-ScoreBoard

CloudEver 积分管理系统（FastAPI）。本版本完成了：

- 规范化配置（环境变量 + 目录结构：`data/`、`images/`）
- UI 重写（morphic 风格：流体渐变、平滑形变、轻微接近感知与使用频度自适应）
- 注册/登录完善：新增「更改密码」、头像上传（≤1MB，保存到 `images/` 并对外挂载）
- Docker 与 Compose 一键部署，数据库（SQLite）文件挂载到 `data/`

## 快速开始（本地）

1) 安装依赖

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) 运行

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 52123
```

打开 http://127.0.0.1:52123

默认管理员账号：`admin`，密码：`1qaz@WSX`（请尽快修改）。

## Docker 运行（推荐）

```powershell
docker compose up --build -d
```

启动后访问 http://localhost:8000

挂载目录：

- `./data` 映射到容器 `/app/data`（SQLite 数据库文件 `ctf_scoring.db`）
- `./images` 映射到容器 `/app/images`（头像等静态文件，通过 `/images/...` 访问）

可用环境变量（`.env` 或 compose 环境传入）：

- `SESSION_SECRET`：会话密钥，生产环境必须自定义
- `DATA_DIR`：数据目录（默认 `/app/data`）
- `IMAGE_DIR`：图片目录（默认 `/app/images`）
- `DATABASE_URL`：数据库 URL（默认 `sqlite:///<DATA_DIR>/ctf_scoring.db`）

## 功能概览

- 积分榜（按月 + 累计）、战队规则、成员个人页
- 成绩提交、Markdown 报告、管理员审核/复审、活动与题目管理
- 成员管理（主/子队、管理员）、按月清空提交
- 个人设置：更改密码、头像上传（≤1MB，PNG/JPG/WebP）

## UI 说明（Morphic）

- 卡片会根据鼠标位置产生流体光影与形变，反馈更自然
- 导航会根据使用频度（本地存储）进行轻微突出显示
- 排版与动效保持方向感与结构特征，避免突兀跳变

---

如需二次开发：建议逐步将路由拆分到模块（routers）、将模板移出到 `templates/` 与 `static/` 目录，并引入测试与类型检查。
