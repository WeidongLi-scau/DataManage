# DataManage — 课题组文档资料管理系统

本项目实现组内资料集中管理 MVP。技术方案与需求见 `技术方案与部署说明.md`、`需求说明书.md`。

## 功能

- 账号登录，管理员创建成员并管理项目/课题分组及成员关系。
- 上传任意格式文件，单文件上限 100 MB；支持批量上传，批次总量上限 1 GB。
- 资料按分组授权，支持公共资料组、资料元数据管理、版本替换和回收恢复。
- 自有系统管理中心可维护成员、项目/课题组、资料集合、分类和期刊目录；审计记录保留关键操作。
- 支持同一资料归属多个授权组；重复内容复用 SHA-256 文件对象，每条资料独立保留元数据和删除状态。
- 支持一般资料/已发表成果区分、资料日期、BibTeX、期刊/卷期/页码及结构化检索。
- PDF、PNG、DOCX 在线预览；可解析的 PDF/DOCX 正文索引和中文检索。
- Meilisearch 暂不可用时回退到数据库检索；搜索结果和文件端点仍在服务端执行权限校验。
- PostgreSQL、Meilisearch、Redis/RQ、Django、Nginx 由 Docker Compose 管理。
- 管理员系统状态页查看磁盘、文件处理队列和全文检索服务。

## 日常使用入口

- **个人设置**：点击右上角姓名，可补充姓名、邮箱或主动修改密码。管理员在成员列表点击“重置密码”，确认后获得新的随机初始密码；原有登录失效，成员再次登录必须修改密码。初始密码只在当次结果页面展示。
- **项目资料**：按授权项目浏览说明、负责人、资料集合与最近更新；可以进入项目范围继续检索。
- **批量整理与下载**：在资料检索列表勾选本页资料后进入操作页，可下载 ZIP、修改分类、加入或移出集合、增减项目授权、移入回收站。修改需确认，且整批资料都必须有管理权限；下载上限为 200 份、1 GB。
- **历史版本**：详情页可下载、预览旧版本，上传者或管理员可将旧版本恢复为新版本，原有历史全部保留。
- **已发表成果**：按年份、作者、期刊、项目筛选，导出当前筛选范围内全部授权成果的 Excel、CSV 或 BibTeX。BibTeX 导出使用当前结构化书目信息，原始上传的 BibTeX 仍保留在详情页。
- **我的上传**：上传页提供批次历史入口，只展示当前账户的上传记录。

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

当前先使用 Python 本地模式查看界面并验收功能，无需 Docker、Redis 或 Meilisearch；前端模板由同一个 Django 服务提供。SQLite 用于本地开发，Meilisearch 不可用时使用数据库检索，文件处理默认在当前进程同步执行。DOCX 转 PDF 在线预览需要本机安装 LibreOffice；即使未安装，原件仍保留，且 PDF 预览不依赖后台索引服务。

```bash
python -m venv .venv
.venv/bin/pip install --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
cp .env.local.example .env.local
bash run-local.sh
```

首次使用可在另一个终端创建管理员：

```bash
.venv/bin/python manage.py bootstrap_admin
```

打开 `http://127.0.0.1:8000`。若从自己的电脑远程测试，请使用 SSH 本地端口转发，不要将开发服务器绑定至内网接口。运行自动化测试：

```bash
SQLITE_PATH=/tmp/teaminfo-test.sqlite3 .venv/bin/python manage.py test
```

生产容器仍使用 PostgreSQL、Redis/RQ、Meilisearch；先完成本地试用后再按 Docker 部署章节构建。

如果 Meilisearch 索引需要重建，可在应用容器中运行：

```bash
docker compose exec web python manage.py rebuild_search_index
```

## 数据与安全提示

- 用户文件、数据库和搜索索引保存在 `HOST_DATA_ROOT` 对应宿主机目录中；不要删除该目录来升级应用。
- 系统没有公网端口、证书或备份配置。仅把 HTTP 入口开放给受信任内网；HTTP 本身不加密。
- 当前没有备份/恢复功能，主机或磁盘故障可能导致资料不可恢复。请在投入正式使用前再次确认这一风险。
- 上传文件不会公开映射为静态 URL；未知格式始终作为附件下载。DOCX 解析/转换仅由非 root worker 执行。

