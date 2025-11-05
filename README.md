# CloudEver-ScoreBoard

CloudEver 积分管理系统

## 快速开始（本地）

1) 安装依赖

```powershell
pip install -r requirements.txt
```

2) 运行

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 52123
```

打开 http://127.0.0.1:52123

## Docker 运行（推荐）

```powershell
docker compose up --build -d
```

启动后访问 http://localhost:52123

挂载目录：

- `./data` 映射到容器 `/app/data`（SQLite 数据库文件 `ctf_scoring.db`）
- `./images` 映射到容器 `/app/images`（头像等静态文件，通过 `/images/...` 访问）

可用环境变量（`.env` 或 compose 环境传入）：

- `SESSION_SECRET`：会话密钥
- `DATA_DIR`：数据目录（默认 `/app/data`）
- `IMAGE_DIR`：图片目录（默认 `/app/images`）
- `DATABASE_URL`：数据库 URL（默认 `sqlite:///<DATA_DIR>/ctf_scoring.db`）

## 功能概览

- 积分榜（按月 + 累计）、战队规则、成员个人页
- 成绩提交、Markdown 报告、管理员审核/复审、活动与题目管理
- 成员管理（主/子队、管理员）
- 个人设置：更改密码、头像上传（≤1MB，PNG/JPG/WebP）