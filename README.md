# DataManage — 课题组文档资料管理系统

本项目实现组内资料集中管理 MVP。技术方案与需求见 `技术方案与部署说明.md`、`需求说明书.md`。

## 功能

- 账号登录，管理员创建成员并管理项目/课题分组及成员关系。
- 上传任意格式文件，单文件上限 100 MB；支持批量上传，批次总量上限 1 GB。
- 资料按分组授权，支持公共资料组、资料元数据管理、版本替换和回收恢复。
- 管理员通过管理后台维护用户、分组和资料分类；审计记录保留关键操作。
- PDF、PNG、DOCX 在线预览；可解析的 PDF/DOCX 正文索引和中文检索。
- Meilisearch 暂不可用时回退到数据库检索；搜索结果和文件端点仍在服务端执行权限校验。
- PostgreSQL、Meilisearch、Redis/RQ、Django、Nginx 由 Docker Compose 管理。
- 管理员系统状态页查看磁盘、文件处理队列和全文检索服务。

## 部署

需要安装 Docker Engine 和 Docker Compose 插件。Linux 主机建议至少 4 vCPU、8 GB RAM，数据盘预留至少 10 GB。Dockerfile 中 Debian 软件包和 Python 依赖使用阿里云镜像源。基础镜像及 Compose 服务镜像仍从各镜像声明的容器仓库拉取。系统默认仅绑定 `127.0.0.1:8080`，配置内网地址前不要开放防火墙端口。

```bash
cp .env.example .env
```

编辑 `.env`：设置强随机的 `DJANGO_SECRET_KEY`、`DATABASE_PASSWORD`、`MEILI_MASTER_KEY`，将 `LAN_BIND_IP` 改为本机内网 IP，并将 `DJANGO_ALLOWED_HOSTS` 加入该 IP/主机名。`HOST_DATA_ROOT` 默认 `/srv/teaminfo/data`。应用文件目录需由容器内 UID `10001` 写入（例如 `sudo chown -R 10001:10001 /srv/teaminfo/data/files`）；PostgreSQL 和 Meilisearch 会使用各自容器账号初始化对应目录。

首次启动前创建挂载目录并设置应用目录属主：

```bash
sudo mkdir -p /srv/teaminfo/data/{postgres,meilisearch,files}
sudo chown -R 10001:10001 /srv/teaminfo/data/files
```

启动服务：

```bash
docker compose up -d --build
docker compose ps
```

首次启动完成迁移后，交互式创建管理员账户：

```bash
docker compose exec web python manage.py bootstrap_admin
```

访问 `http://<内网主机IP>:8080`。首次登录会强制修改密码。管理员在 `/admin/` 创建成员账户、项目/课题分组和资料分类，并为成员分配分组。公共资料分组会由初始化命令创建；管理员还可在“系统状态”页查看容量和检索服务。

## 本地开发与测试

需要 Python 3.13、Redis、Meilisearch，以及用于 DOCX 在线预览的 LibreOffice。SQLite 可用于本地功能开发；部署环境使用 PostgreSQL。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py test
```

异步处理依赖 Redis worker：

```bash
python manage.py rqworker default
```

如果 Meilisearch 索引需要重建，可在应用容器中运行：

```bash
docker compose exec web python manage.py rebuild_search_index
```

## 数据与安全提示

- 用户文件、数据库和搜索索引保存在 `HOST_DATA_ROOT` 对应宿主机目录中；不要删除该目录来升级应用。
- 系统没有公网端口、证书或备份配置。仅把 HTTP 入口开放给受信任内网；HTTP 本身不加密。
- 当前没有备份/恢复功能，主机或磁盘故障可能导致资料不可恢复。请在投入正式使用前再次确认这一风险。
- 上传文件不会公开映射为静态 URL；未知格式始终作为附件下载。DOCX 解析/转换仅由非 root worker 执行。

