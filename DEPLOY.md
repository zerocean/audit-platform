# 部署指南 — 一步步跟着做

> 服务器：Ubuntu 22.04，全新安装从头开始

---

## 步骤 0：准备服务器

```bash
# SSH 登录后
sudo apt update && sudo apt upgrade -y

# 装基础工具
sudo apt install -y git curl wget

# 装 Node.js 20（前端构建 + email_worker）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node -v   # 确认 v20.x

# 装 Python 3.11+
sudo apt install -y python3 python3-pip python3-venv
python3 --version   # 确认 3.11+

# 装 PM2（进程守护）
sudo npm install -g pm2
```

---

## 步骤 1：创建目录和拉代码

```bash
sudo mkdir -p /opt/audit-platform /opt/admin-dashboard /opt/shared_db
sudo chown -R $USER:$USER /opt/audit-platform /opt/admin-dashboard /opt/shared_db

# 拉代码
cd /opt
git clone https://github.com/zerocean/audit-platform.git audit-platform-tmp
git clone https://github.com/zerocean/audit-admin-dashboard.git admin-dashboard-tmp

# 如果已经 clone 到了别的位置，直接 mv 即可
mv /opt/audit-platform-tmp /opt/audit-platform 2>/dev/null
mv /opt/admin-dashboard-tmp /opt/admin-dashboard 2>/dev/null

# shared_db 不在 Git 仓库里，从本地 Windows 上传
# 在 Windows PowerShell 执行：
#   scp -r D:\Demo\shared_db\* root@你的服务器IP:/opt/shared_db/
```

最终目录：

```
/opt/
├── shared_db/           ← 共享数据库模块
│   ├── __init__.py
│   └── models.py
├── audit-platform/      ← 员工端
│   ├── backend/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── routers/
│   │   ├── services/
│   │   └── engines/
│   ├── frontend/        ← React 前端
│   └── email_worker.js  ← 邮件触发器
└── admin-dashboard/     ← 管理端
    ├── backend/
    └── frontend/
```

---

## 步骤 2：PostgreSQL

### 2.1 安装

```bash
sudo apt install -y postgresql postgresql-client
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2.2 创建数据库和用户

```bash
sudo -u postgres psql
```

在 psql 里逐行执行（`*** 换成你的密码）：

```sql
-- 1. 创建一个只能本地登录的数据库用户
CREATE USER audit_user WITH PASSWORD 'mys...';

-- 2. 创建数据库，owner 设为这个用户
CREATE DATABASE audit_platform OWNER audit_user;

-- 3. 给用户在数据库级别全部权限（建表、读写等）
GRANT ALL PRIVILEGES ON DATABASE audit_platform TO audit_user;

-- 4. 切换到新数据库
\c audit_platform

-- 5. PostgreSQL 15+ 需要额外授权 public schema（否则建表报权限错误）
GRANT ALL ON SCHEMA public TO audit_user;
GRANT CREATE ON SCHEMA public TO audit_user;

-- 退出
\q
```

### 2.3 配置本地登录

```bash
# 确认这行存在（通常在文件末尾）
sudo grep -n '^host.*all.*all.*127.0.0.1' /etc/postgresql/14/main/pg_hba.conf

# 没有就加上
echo "host    all   all   127.0.0.1/32   md5" | sudo tee -a /etc/postgresql/14/main/pg_hba.conf
sudo systemctl restart postgresql
```

### 2.4 测试

```bash
psql postgresql://audit_user:YOUR_PASSWORD@localhost:5432/audit_platform -c "SELECT 1"
# 输出 ?column? = 1 即成功
```

---

## 步骤 3：配置环境变量

在 `/opt/.env` 创建统一的环境变量文件，所有服务共用：

```bash
cat > /opt/.env << 'EOF'
# -- 数据库 --
DATABASE_URL=postgresql://audit_user:YOUR_PASSWORD@localhost:5432/audit_platform

# -- DashScope --
DASHSCOPE_API_KEY=your-dashscope-api-key

# -- 统计上报 --
ENV=production

# -- JWT（两边必须一样）--
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# -- 内部 API 鉴权（audit-platform 和 email_worker 必须一样）--
INTERNAL_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")

# -- email_worker --
EMAIL_USER=hafoshan@163.com
EMAIL_PASSWORD=your-163-auth-code
EOF

cat /opt/.env   # 确认内容正确
```

---

## 步骤 4：确保 shared_db 可导入

两个项目的 `database.py` 都 `from shared_db import ...`，需要 shared_db 在 Python 路径里。

```bash
# 验证
python3 -c "
import sys; sys.path.insert(0, '/opt')
from shared_db import SessionLocal
print('shared_db import OK')
"
```

如果 import 失败，检查目录：
```bash
ls /opt/shared_db/__init__.py   # 必须存在
```

---

## 步骤 5：安装 Python 依赖
### 5.1 audit-platform

```bash
cd /opt/audit-platform/backend

# 如果有 requirements.txt
pip install -r requirements.txt

# 没有就手动装
pip install fastapi uvicorn sqlalchemy python-jose passlib bcrypt \
            python-dotenv openai pymupdf pandas openpyxl psycopg2-binary \
            python-multipart

# 确认 shared_db 能被找到
python3 -c "
import sys; sys.path.insert(0, '/opt')
from shared_db import Base
print('shared_db OK')"
```

### 5.2 admin-dashboard

```bash
cd /opt/admin-dashboard/backend

pip install fastapi uvicorn sqlalchemy python-jose passlib bcrypt \
            python-dotenv python-multipart psycopg2-binary

python3 -c "
import sys; sys.path.insert(0, '/opt')
from shared_db import Base
print('shared_db OK')"
```

---

## 步骤 6：初始化数据库

```bash
cd /opt/audit-platform/backend

python3 -c "
import sys; sys.path.insert(0, '/opt'); sys.path.insert(0, '/opt/audit-platform/backend')
from shared_db import init_db
init_db()
print('Database tables created, seed data inserted')
"
```

验证：

```bash
python3 -c "
import sys; sys.path.insert(0, '/opt')
from shared_db import SessionLocal
from shared_db.models import User
db = SessionLocal()
users = db.query(User).all()
for u in users: print(f'  {u.username} ({u.role})')
db.close()
"
# 应该输出: admin (admin), auditor (user)
```

---

## 步骤 7：构建前端

### 7.1 audit-platform 前端

```bash
cd /opt/audit-platform/frontend

# 安装依赖
npm install

# 构建
npx vite build

# 检查输出
ls dist/
# 应该有 index.html, assets/ 等
```

### 7.2 admin-dashboard 前端

```bash
cd /opt/admin-dashboard/frontend

npm install
npx vite build

ls dist/
```

---

## 步骤 8：安装 email_worker 依赖

```bash
cd /opt/audit-platform
npm install imap mailparser nodemailer axios form-data dotenv
```

---

## 步骤 9：PM2 启动所有服务

```bash
# 1. audit-platform 后端
pm2 start "uvicorn backend.main:app --host 0.0.0.0 --port 8767" \
  --name audit-platform \
  --cwd /opt/audit-platform

# 2. admin-dashboard 后端
pm2 start "uvicorn main:app --host 0.0.0.0 --port 5004" \
  --name admin-dashboard \
  --cwd /opt/admin-dashboard/backend

# 3. email_worker
pm2 start email_worker.js \
  --name email-worker \
  --cwd /opt/audit-platform

# 4. 查看状态
pm2 status

# 5. 保存 + 开机自启
pm2 save
pm2 startup
# 按提示执行输出里的 sudo 命令
```

---

## 步骤 10：验证

```bash
# 1. 健康检查
curl http://localhost:8767/api/health
# → {"status":"ok"}

# 2. 登录测试
curl -X POST http://localhost:8767/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → {"success":true,"data":{"token":"eyJ..."}}

# 3. admin-dashboard 统计
curl http://localhost:5004/api/v1/statistics/overview \
  -H "Authorization: Bearer <上一步拿到的token>"
# → {"success":true,"data":{...}}

# 4. 前端页面
curl -s http://localhost:8767/ | head -5
# → 能看到 <!DOCTYPE html> 就是前端 OK

# 5. email_worker 日志
pm2 logs email-worker --lines 10
```

---

## 步骤 11：防火墙

```bash
# 只开放需要的端口
sudo ufw allow 22     # SSH（别把自己锁外面）
sudo ufw allow 8767   # audit-platform（后端+前端）
sudo ufw allow 5004   # admin-dashboard 后端
sudo ufw enable
sudo ufw status
```

---

## 日常运维

```bash
pm2 status           # 看所有服务状态
pm2 logs             # 看所有日志
pm2 logs audit-platform  # 看某个服务的日志
pm2 restart all      # 重启全部
pm2 stop all         # 停全部
```

更新代码后：

```bash
cd /opt/audit-platform && git pull
cd /opt/audit-platform/frontend && npm install && npx vite build
pm2 restart audit-platform

cd /opt/admin-dashboard && git pull
cd /opt/admin-dashboard/frontend && npm install && npx vite build
pm2 restart admin-dashboard
```

### 定期清理

引擎日志、上传的 PDF、解析输出等会随时间积累。`backend/cleanup.py` 自动删除超过 30 天的旧文件：

```bash
# 手动运行
cd /opt/audit-platform/backend
python3 cleanup.py

# 加入 crontab 每天凌晨 3 点自动清理
crontab -e
# 加一行：
0 3 * * * cd /opt/audit-platform/backend && python3 cleanup.py >> /var/log/audit-cleanup.log 2>&1
```

可通过环境变量调整保留天数：`FILE_RETENTION_DAYS=60`。

### OSS 存储（后期可选）

已有 `services/oss.py` 模块支持阿里云 OSS，配置后文件自动上传云端，不占服务器磁盘。需设环境变量：

```bash
OSS_ACCESS_KEY_ID=***      Oss_SECRET***Bucket_nameOSS_ENDPOINT=oss-cn-***re.aliyuncs.com```

---

## 完整文件清单

部署完成后确认这些文件都存在：

```
/opt/.env                          ← 所有环境变量
/opt/shared_db/__init__.py         ← 数据库引擎
/opt/shared_db/models.py           ← ORM 模型

/opt/audit-platform/
├── backend/                       ← FastAPI 后端
├── frontend/dist/                 ← 前端构建产物
├── email_worker.js                ← 邮件触发器
└── node_modules/                  ← JS 依赖

/opt/admin-dashboard/
├── backend/                       ← FastAPI 后端
└── frontend/dist/                 ← 前端构建产物
```
