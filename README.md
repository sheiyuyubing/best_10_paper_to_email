# 计算机顶刊顶会论文邮件提醒

每隔 10 天从 OpenAlex、Crossref、DBLP、arXiv 和会议官网获取计算机顶刊顶会论文，去重、筛选、排序后选择 10 篇，并发送到指定邮箱。

## 项目结构

```text
best_10_paper_to_email/
├── src/
│   ├── models.py
│   ├── storage.py
│   ├── sources.py
│   ├── emailer.py
│   └── pipeline.py
├── templates/
│   └── digest.html
├── config/
│   └── venues.yaml
├── deploy/
│   ├── paper-digest.service
│   └── paper-digest.timer
├── scripts/
│   ├── run_paper_digest.sh
│   ├── install_user_timer.sh
│   └── acceptance_check.py
├── tests/
│   └── test_core.py
├── data/
├── logs/
├── .env.example
├── requirements.txt
└── README.md
```

## 一、Linux 环境准备

建议使用 Ubuntu 22.04 及以上、Debian 12 及以上或同等级 Linux 发行版。

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev git curl ca-certificates tzdata sqlite3
sudo timedatectl set-timezone Asia/Shanghai
```

检查 Python：

```bash
python3 --version
```

## 二、创建虚拟环境并安装依赖

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

依赖只通过 `requirements.txt` 声明，不在 Python 源码中安装依赖。

## 三、配置邮箱

复制邮件配置模板：

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，填写真实配置：

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_TIMEOUT_SECONDS=30
SMTP_USER=your_account@example.com
SMTP_PASSWORD=your_smtp_authorization_code
MAIL_FROM=your_account@example.com
MAIL_TO=your_account@example.com

OPENALEX_EMAIL=your_account@example.com
CROSSREF_MAILTO=your_account@example.com
```

说明：

- Gmail 通常使用应用专用密码。
- QQ 邮箱和网易邮箱通常使用 SMTP 授权码，不是网页登录密码。
- 使用 587 端口和 STARTTLS 时，把 `SMTP_USE_SSL` 改为 `false`。
- `.env` 不能上传到 Git 仓库。

## 四、配置论文来源和周期

编辑：

```text
config/venues.yaml
```

主要参数：

```text
interval_days: 10
lookback_days: 180
target_count: 10
max_pages_per_source: 5
polite_delay_seconds: 1.0
```

`venues` 下面是顶刊顶会白名单。每项包含名称、简称、类型、研究方向和等级。可以删除不需要的方向，也可以增加新的期刊或会议。

## 五、执行前验收检查

不联网检查文件、依赖、数据库和邮件模板：

```bash
python scripts/acceptance_check.py
```

执行联网 dry-run，但绝不发送邮件：

```bash
python scripts/acceptance_check.py --network
```

也可以直接执行：

```bash
python -m src.pipeline --dry-run --limit 10
```

dry-run 结果保存在：

```text
data/selected_日期.json
```

## 六、正式运行和发送邮件

手动运行：

```bash
python -m src.pipeline
```

忽略 10 天周期，立即执行：

```bash
python -m src.pipeline --force
```

日志写入：

```text
logs/paper_digest.log
```

日志每 10 天轮转一次，保留最近 6 份。

## 七、安装自动定时任务

项目采用“每天定时唤醒、程序内部判断是否满 10 天”的方式。

安装 systemd 用户定时器：

```bash
bash scripts/install_user_timer.sh
```

查看定时器：

```bash
systemctl --user list-timers paper-digest.timer
systemctl --user status paper-digest.timer --no-pager
```

查看服务运行记录：

```bash
journalctl --user -u paper-digest.service --no-pager
tail -f logs/paper_digest.log
```

如果系统重启后用户服务没有运行，执行：

```bash
sudo loginctl enable-linger "$USER"
```

定时器每天北京时间 08:00 触发。程序检查 SQLite 中的成功发送时间，只有距离上次成功发送满 10 天才真正抓取和发送。

## 八、运行测试

执行核心单元测试：

```bash
python -m unittest discover -s tests -v
```

测试内容：

- DOI 规范化。
- 论文唯一标识。
- 标题去重。
- SQLite 发送记录和运行时间记录。
- HTML 邮件模板渲染。

## 九、去重和周期规则

- 有 DOI 时，使用规范化 DOI 作为唯一标识。
- 没有 DOI 时，使用规范化标题和年份生成唯一标识。
- 已经成功发送过的论文不会再次发送。
- 只有邮件发送成功，才会记录论文已发送和更新时间。
- dry-run 不会改变发送状态，也不会重置 10 天周期。
- 不足 10 篇时，程序会在最多 180 天范围内优先选择未发送论文。

## 十、常见问题

### 1. 提示没有可发送论文

检查：

```bash
python -m src.pipeline --dry-run --debug
```

可能原因是近期目标期刊会议没有新论文、API 暂时不可用、网络被限制或顶刊顶会白名单过窄。

### 2. SMTP 登录失败

确认：

- `SMTP_HOST` 和 `SMTP_PORT` 是否正确。
- 是否使用了邮箱授权码而不是网页密码。
- `MAIL_FROM` 是否与 `SMTP_USER` 匹配。
- 网络是否允许访问 SMTP 端口。

### 3. 定时器没有运行

检查：

```bash
systemctl --user daemon-reload
systemctl --user enable --now paper-digest.timer
systemctl --user list-timers --all
loginctl show-user "$USER" | grep Linger
```

### 4. 抓取接口频繁失败

适当增大：

```text
polite_delay_seconds
arxiv_delay_seconds
proceedings_delay_seconds
```

并减少 `max_pages_per_source` 或 `max_conference_queries`。

### 5. 不想发送邮件只做测试

使用：

```bash
python -m src.pipeline --dry-run
```

### 6. 临时禁用某个数据源

在 `config/venues.yaml` 中设置：

```text
openalex:
  enabled: false
```

同样可以设置 `crossref`、`dblp` 和 `arxiv`。

## 十一、安全要求

- `.env` 权限保持为 `600`。
- 不要提交 `.env`、数据库或日志到 Git。
- 不要将邮箱授权码写入 Python 代码。
- 定期检查日志中的认证错误和接口限制。
